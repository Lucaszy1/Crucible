#!/usr/bin/env python3
"""Run a learning campaign: sequential episodes with a shared playbook.

Each episode runs one exercise, then the referee folds lessons into
blue's playbook for the next episode. Memory on means blue reads the
playbook; memory off is the stateless control (no referee calls).
Per-episode logs, playbook snapshots, and summaries land under
runs/campaign-<name>/.

Examples:
  python -m crucible.campaign --name mem1 --episodes 6 --memory on --agents model
  python -m crucible.campaign --name ctl1 --episodes 6 --memory off --agents model
  python -m crucible.campaign --name mem1 --episodes 9 --memory on --resume
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from crucible import events, orchestrator, run_exercise, sandbox, tools
from crucible.agents import ModelBlue, ModelRed, ScriptedBlue, ScriptedRed
from crucible.agents import referee

CSV_FIELDS = (
    "episode",
    "outcome",
    "blue_score",
    "downtime_pct",
    "detection_delay",
    "time_to_contain",
    "cost",
    "playbook_rule_count",
)

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run a learning campaign.")
    parser.add_argument("--name", required=True, help="Campaign name")
    parser.add_argument("--episodes", type=int, required=True,
                        help="Total episodes to run")
    parser.add_argument("--memory", choices=("on", "off"), required=True,
                        help="on: blue reads the playbook; off: control")
    parser.add_argument("--agents", choices=("scripted", "model"),
                        default="scripted",
                        help="scripted is free, model uses Muse Spark")
    parser.add_argument("--audit", choices=("off", "end"), default="off",
                        help="off skips judging, end audits after each episode")
    parser.add_argument("--max-turns", type=int, default=40,
                        help="Turns per episode, capped at 40")
    parser.add_argument("--no-cache", action="store_true",
                        help="Skip the model response cache for this campaign")
    parser.add_argument("--resume", action="store_true",
                        help="Continue an existing campaign instead of starting over")
    parser.add_argument("--force", action="store_true",
                        help="Archive the existing campaign dir and start over")
    return parser.parse_args(argv)


def _campaign_dir(name: str) -> Path:
    return events.RUNS_DIR / f"campaign-{name}"


def _check_name(name: str) -> str:
    if not _NAME_RE.match(name or ""):
        raise ValueError(
            f"campaign name {name!r} must use letters, digits, dot, dash, underscore")
    return name


def _mean(values: list) -> float | None:
    clean = [value for value in values
             if isinstance(value, (int, float)) and value is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def _thirds(rows: list) -> tuple:
    """Split episode rows into first and last thirds for comparison."""
    size = max(1, len(rows) // 3)
    return rows[:size], rows[-size:]


def _write_csv(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_FIELDS))
        writer.writeheader()
        for row in rows:
            out = {key: row.get(key) for key in CSV_FIELDS}
            for key in ("detection_delay", "time_to_contain"):
                if out[key] is None:
                    out[key] = ""
            writer.writerow(out)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _load_state(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _archive_dir(campaign_dir: Path, name: str) -> Path:
    """Move an existing campaign dir aside under runs/archive/."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = events.RUNS_DIR / "archive" / f"campaign-{name}-{stamp}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(campaign_dir), str(target))
    return target


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        _check_name(args.name)
    except ValueError as exc:
        print(f"campaign: {exc}", file=sys.stderr)
        return 1
    total = int(args.episodes)
    if total < 1:
        print("--episodes must be at least 1", file=sys.stderr)
        return 1

    campaign_dir = _campaign_dir(args.name)
    summary_path = campaign_dir / "summary.json"
    rows: list = []
    playbook = referee.empty_playbook()
    if args.resume:
        state = _load_state(summary_path)
        if state is None or state.get("name") != args.name:
            print(f"campaign: no resumable campaign {args.name!r} "
                  f"at {summary_path}", file=sys.stderr)
            return 1
        if state.get("memory", args.memory) != args.memory:
            print("campaign: resume memory flag differs from the saved "
                  f"campaign ({state.get('memory')}); aborting",
                  file=sys.stderr)
            return 1
        rows = list(state.get("episodes", []) or [])
        if len(rows) >= total:
            print(f"campaign: {args.name} already has {len(rows)} episodes",
                  file=sys.stderr)
            return 1
        playbook = referee.load_playbook(campaign_dir / "playbook.json")
        print(f"Resuming campaign {args.name} at episode {len(rows) + 1} "
              f"of {total}...")
    else:
        if campaign_dir.exists():
            if not args.force:
                print(f"campaign: {campaign_dir} already exists; pass "
                      "--resume to continue it or --force to archive it "
                      "and start over", file=sys.stderr)
                return 1
            archived = _archive_dir(campaign_dir, args.name)
            print(f"Archived previous campaign to {archived}")
        referee.save_playbook(referee.empty_playbook())
        print(f"Starting campaign {args.name}: {total} episodes, "
              f"memory={args.memory}, agents={args.agents}...")

    print("Booting sandbox...")
    try:
        sandbox.start()
    except sandbox.SandboxError as exc:
        print(f"Sandbox boot failed: {exc}", file=sys.stderr)
        print("Check that Docker is running, then try again.", file=sys.stderr)
        return 1

    try:
        for number in range(len(rows) + 1, total + 1):
            exercise_id = f"{args.name}-{number}"
            rules = playbook.get("rules", []) or []
            if args.agents == "model":
                red = ModelRed(exercise_id=exercise_id,
                               use_cache=not args.no_cache)
                blue = ModelBlue(exercise_id=exercise_id,
                                 use_cache=not args.no_cache,
                                 playbook=rules if args.memory == "on" else None)
            else:
                red = ScriptedRed()
                blue = ScriptedBlue()
            for host in ("web", "app", "db", "workstation"):
                tools.restore_host(host)
            spend_start = run_exercise._spend_line_count()
            result = orchestrator.run_exercise(
                red, blue, max_turns=args.max_turns,
                run_path=campaign_dir / f"{exercise_id}.jsonl",
                exercise_id=exercise_id, audit=args.audit)
            score = result.get("score", {}) or {}
            referee_model = None
            if args.memory == "on":
                review = referee.review_episode(
                    result.get("events", []), score, exercise_id, playbook,
                    use_cache=not args.no_cache)
                playbook = review["playbook"]
                referee_model = review["model_used"]
                referee.save_playbook(playbook)
                referee.save_playbook(
                    playbook, campaign_dir / "playbook.json",
                    campaign_dir / "playbook.md")
                referee.save_playbook(
                    playbook,
                    campaign_dir / "playbooks" / f"{exercise_id}.json",
                    campaign_dir / "playbooks" / f"{exercise_id}.md")
            cost = run_exercise.exercise_cost_since(spend_start)
            rows.append({
                "episode": exercise_id,
                "episode_no": number,
                "outcome": score.get("outcome", "unknown"),
                "blue_score": score.get("blue_score", 0),
                "downtime_pct": score.get("downtime_pct", 0.0),
                "detection_delay": score.get("detection_delay"),
                "time_to_contain": score.get("time_to_contain"),
                "cost": round(cost, 6),
                "playbook_rule_count": len(playbook.get("rules", []) or []),
                "referee_model": referee_model,
            })
            latest = rows[-1]
            print(f"{exercise_id}: outcome={latest['outcome']} "
                  f"blue_score={latest['blue_score']} "
                  f"rules={latest['playbook_rule_count']} "
                  f"cost=${cost:.4f}")
            _write_csv(campaign_dir / "summary.csv", rows)
            _write_json(summary_path, {"name": args.name,
                                       "memory": args.memory,
                                       "agents": args.agents,
                                       "episodes": rows})
    finally:
        print("Stopping sandbox...")
        try:
            sandbox.stop()
        except sandbox.SandboxError as exc:
            print(f"Sandbox stop noted: {exc}", file=sys.stderr)
        tools.reset_state()

    first, last = _thirds(rows)
    first_mean = _mean([row["blue_score"] for row in first])
    last_mean = _mean([row["blue_score"] for row in last])
    total_cost = sum(float(row.get("cost", 0.0) or 0.0) for row in rows)
    print(f"Campaign done: {len(rows)} episodes (name={args.name})")
    print("episode | outcome | blue_score | cost")
    for row in rows:
        print(f"{row['episode']} | {row['outcome']} | {row['blue_score']} "
              f"| ${float(row.get('cost', 0.0) or 0.0):.4f}")
    if first_mean is not None and last_mean is not None:
        print(f"Mean blue_score first third: {first_mean:.2f} "
              f"vs last third: {last_mean:.2f}")
    print(f"Total cost: ${total_cost:.4f}")
    print(f"CSV: {campaign_dir / 'summary.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
