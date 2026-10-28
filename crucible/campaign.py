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

from crucible import events, llm, orchestrator, run_exercise, sandbox, tools
from crucible.agents import ModelBlue, ModelRed, ScriptedBlue, ScriptedRed
from crucible.agents import referee
from crucible.scenarios import select, stack
from crucible.scenarios.schema import DIFFICULTIES

CSV_FIELDS = (
    "episode",
    "outcome",
    "blue_score",
    "downtime_pct",
    "detection_delay",
    "time_to_contain",
    "cost",
    "playbook_rule_count",
    "sensors",
    "intrusion",
    "contained_after_intrusion",
    "secret_read",
    "relevant_detection_delay",
    "scenario_id",
    "scenario_seed",
    "scenario_difficulty",
)

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run a learning campaign.")
    parser.add_argument("--name", help="Campaign name (single-arm mode)")
    parser.add_argument("--episodes", type=int, required=True,
                        help="Episodes to run per arm per scenario")
    parser.add_argument("--memory", choices=("on", "off"),
                        help="on: blue reads the playbook; off: control")
    parser.add_argument("--arms",
                        help="Two arms as name,name (first memory on, second "
                        "off) or name:on,name:off; interleaves episodes "
                        "pair by pair. Arms pair by episode index only; "
                        "the shared pair seed only splits cache keys, "
                        "play still diverges.")
    parser.add_argument("--agents", choices=("scripted", "model"),
                        default="scripted",
                        help="scripted is free, model uses Muse Spark")
    parser.add_argument("--audit", choices=("off", "end"), default="off",
                        help="off skips judging, end audits after each episode")
    parser.add_argument("--sensors", choices=("on", "off"), default="on",
                        help="on shows blue new sensor alerts each turn, "
                        "off leaves blue on logs alone")
    parser.add_argument("--max-turns", type=int, default=40,
                        help="Turns per episode, capped at 60")
    parser.add_argument("--blue-model", default=None,
                        help="Model id for blue (default: the blue role model)")
    parser.add_argument("--blue-base-url", default=None,
                        help="Custom API base URL for blue")
    parser.add_argument("--blue-api-key-env", default=None,
                        help="Env var holding the API key for blue's custom host")
    parser.add_argument("--blue-reasoning-effort", default=None,
                        choices=("omit", "minimal", "low", "medium", "high"),
                        help="Reasoning effort for blue's calls only "
                        "(default: today's behavior; omit sends no "
                        "reasoning_effort field at all)")
    parser.add_argument("--no-cache", action="store_true",
                        help="Skip the model response cache for this campaign")
    parser.add_argument("--resume", action="store_true",
                        help="Continue an existing campaign instead of starting over")
    parser.add_argument("--force", action="store_true",
                        help="Archive the existing campaign dir and start over")
    parser.add_argument("--scenarios", default=None,
                        help="Comma list of classic and "
                        "gen:<seed>:<difficulty> (default: classic only)")
    parser.add_argument("--scenario-set", choices=("train", "heldout"),
                        default=None,
                        help="Append the named seed set at --difficulty")
    parser.add_argument("--difficulty", choices=list(DIFFICULTIES),
                        default="medium",
                        help="Difficulty for --scenario-set seeds "
                        "(default: medium)")
    parser.add_argument("--freeze-playbook", action="store_true",
                        help="Blue still reads the playbook but the referee "
                        "never updates it (for held-out evaluation)")
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
            for key in ("detection_delay", "time_to_contain",
                        "relevant_detection_delay", "scenario_seed",
                        "scenario_difficulty"):
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


def _parse_arms(spec: str) -> list:
    """Parse --arms into [(name, memory)] with positional defaults.

    Bare names default first arm to memory on, second to off; an
    explicit :on or :off suffix wins. Exactly two arms are required.
    """
    parts = [piece.strip() for piece in (spec or "").split(",") if piece.strip()]
    if len(parts) != 2:
        raise ValueError("--arms needs exactly two arms, e.g. mem,ctrl")
    arms = []
    for order, piece in enumerate(parts):
        if ":" in piece:
            name, memory = piece.split(":", 1)
            name, memory = name.strip(), memory.strip()
            if memory not in ("on", "off"):
                raise ValueError(f"arm {piece!r} memory must be :on or :off")
        else:
            name, memory = piece, ("on" if order == 0 else "off")
        arms.append((_check_name(name), memory))
    if arms[0][0] == arms[1][0]:
        raise ValueError("--arms names must differ")
    return arms


def _episode_flags(events_list: list) -> tuple:
    """Flag red probing /admin and red getting in for one episode."""
    admin = False
    got_in = False
    for event in events_list or []:
        if event.get("actor") != "red":
            continue
        if event.get("tool") == "http_request":
            path = str((event.get("args", {}) or {}).get("path", "") or "")
            if path.startswith("/admin"):
                admin = True
        if event.get("tool") == "try_credential":
            if (event.get("result", {}) or {}).get("authenticated") is True:
                got_in = True
    return admin, got_in


def _arm_stats(rows: list) -> dict:
    """Summarize red behavior and blue wins over episode rows."""
    total = len(rows)
    admin = sum(1 for row in rows if row.get("red_admin"))
    got_in = sum(1 for row in rows if row.get("red_in"))
    wins = sum(1 for row in rows if row.get("outcome") == "blue_held")
    return {
        "episodes": total,
        "admin_count": admin,
        "admin_rate": round(admin / total, 3) if total else None,
        "in_count": got_in,
        "in_rate": round(got_in / total, 3) if total else None,
        "wins": wins,
    }


def _archive_dir(campaign_dir: Path, name: str) -> Path:
    """Move an existing campaign dir aside under runs/archive/."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = events.RUNS_DIR / "archive" / f"campaign-{name}-{stamp}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(campaign_dir), str(target))
    return target


def _open_arm(name: str, memory: str, total: int, args,
              labels: list) -> dict | None:
    """Load or start one arm's state; None with an error printed on failure."""
    campaign_dir = _campaign_dir(name)
    summary_path = campaign_dir / "summary.json"
    rows: list = []
    playbook = referee.empty_playbook()
    if args.resume:
        state = _load_state(summary_path)
        if state is None or state.get("name") != name:
            print(f"campaign: no resumable campaign {name!r} "
                  f"at {summary_path}", file=sys.stderr)
            return None
        if state.get("memory", memory) != memory:
            print(f"campaign: resume memory flag differs from the saved "
                  f"campaign {name!r} ({state.get('memory')}); aborting",
                  file=sys.stderr)
            return None
        if state.get("sensors", args.sensors) != args.sensors:
            print(f"campaign: resume sensors flag differs from the saved "
                  f"campaign {name!r} ({state.get('sensors')}); aborting",
                  file=sys.stderr)
            return None
        if state.get("scenarios", ["classic"]) != labels:
            print(f"campaign: resume scenario set differs from the saved "
                  f"campaign {name!r} ({state.get('scenarios')}); aborting",
                  file=sys.stderr)
            return None
        if state.get("playbook_frozen", False) != bool(args.freeze_playbook):
            print(f"campaign: resume freeze flag differs from the saved "
                  f"campaign {name!r}; aborting", file=sys.stderr)
            return None
        rows = list(state.get("episodes", []) or [])
        if len(rows) >= total:
            print(f"campaign: {name} already has {len(rows)} episodes",
                  file=sys.stderr)
            return None
        playbook = referee.load_playbook(campaign_dir / "playbook.json")
        print(f"Resuming campaign {name} at episode {len(rows) + 1} "
              f"of {total}...")
    else:
        if campaign_dir.exists():
            if not args.force:
                print(f"campaign: {campaign_dir} already exists; pass "
                      "--resume to continue it or --force to archive it "
                      "and start over", file=sys.stderr)
                return None
            archived = _archive_dir(campaign_dir, name)
            print(f"Archived previous campaign to {archived}")
        print(f"Starting campaign {name}: {total} episodes, "
              f"memory={memory}, agents={args.agents}...")
    return {"name": name, "memory": memory, "dir": campaign_dir,
            "summary_path": summary_path, "rows": rows, "playbook": playbook,
            "sensors": args.sensors, "scenarios": labels,
            "playbook_frozen": bool(args.freeze_playbook)}


def _run_episode(arm: dict, number: int, args, seed: str | None,
               blue_client=None, scenario: dict | None = None,
               label: str = "classic", grouped: bool = False) -> None:
    """Run one episode for one arm and persist its summaries."""
    exercise_id = f"{arm['name']}-{label}-{number}" if grouped \
        else f"{arm['name']}-{number}"
    rules = arm["playbook"].get("rules", []) or []
    if args.agents == "model":
        red = ModelRed(exercise_id=exercise_id,
                       use_cache=not args.no_cache, seed=seed)
        blue = ModelBlue(exercise_id=exercise_id,
                         use_cache=not args.no_cache,
                         playbook=rules if arm["memory"] == "on" else None,
                         seed=seed, client=blue_client, model=args.blue_model,
                         reasoning_effort=args.blue_reasoning_effort)
    else:
        red = ScriptedRed()
        blue = ScriptedBlue()
    for host in sandbox.active_hosts():
        tools.restore_host(host)
    spend_start = run_exercise._spend_line_count()
    extra = {"scenario": scenario} if scenario else {}
    result = orchestrator.run_exercise(
        red, blue, max_turns=args.max_turns,
        run_path=arm["dir"] / f"{exercise_id}.jsonl",
        exercise_id=exercise_id, audit=args.audit,
        sensors=args.sensors, seed=seed,
        blue_reasoning_effort=args.blue_reasoning_effort, **extra)
    score = result.get("score", {}) or {}
    referee_model = None
    if arm["memory"] == "on" and not args.freeze_playbook:
        review = referee.review_episode(
            result.get("events", []), score, exercise_id, arm["playbook"],
            use_cache=not args.no_cache)
        arm["playbook"] = review["playbook"]
        referee_model = review["model_used"]
        referee.save_playbook(arm["playbook"])
        referee.save_playbook(
            arm["playbook"], arm["dir"] / "playbook.json",
            arm["dir"] / "playbook.md")
        referee.save_playbook(
            arm["playbook"],
            arm["dir"] / "playbooks" / f"{exercise_id}.json",
            arm["dir"] / "playbooks" / f"{exercise_id}.md")
    cost = run_exercise.exercise_cost_since(spend_start)
    admin, got_in = _episode_flags(result.get("events", []))
    arm["rows"].append({
        "episode": exercise_id,
        "episode_no": number,
        "outcome": score.get("outcome", "unknown"),
        "blue_score": score.get("blue_score", 0),
        "downtime_pct": score.get("downtime_pct", 0.0),
        "detection_delay": score.get("detection_delay"),
        "time_to_contain": score.get("time_to_contain"),
        "cost": round(cost, 6),
        "playbook_rule_count": len(arm["playbook"].get("rules", []) or []),
        "sensors": score.get("sensors", args.sensors),
        "intrusion": score.get("intrusion", False),
        "contained_after_intrusion": score.get(
            "contained_after_intrusion", False),
        "secret_read": score.get("secret_read", score.get(
            "secret_exfiltrated", False)),
        "relevant_detection_delay": score.get("relevant_detection_delay"),
        "scenario": label,
        "scenario_id": (scenario or {}).get("scenario_id", "classic"),
        "scenario_seed": (scenario or {}).get("seed"),
        "scenario_difficulty": (scenario or {}).get("difficulty"),
        "referee_model": referee_model,
        "red_admin": admin,
        "red_in": got_in,
    })
    latest = arm["rows"][-1]
    print(f"{exercise_id}: outcome={latest['outcome']} "
          f"blue_score={latest['blue_score']} "
          f"rules={latest['playbook_rule_count']} "
          f"cost=${cost:.4f}")
    _write_csv(arm["dir"] / "summary.csv", arm["rows"])
    _write_json(arm["summary_path"], {"name": arm["name"],
                                     "memory": arm["memory"],
                                     "agents": args.agents,
                                     "sensors": args.sensors,
                                     "scenarios": arm.get("scenarios",
                                                          ["classic"]),
                                     "playbook_frozen": bool(
                                         args.freeze_playbook),
                                     "episodes": arm["rows"]})


def _report_arm(arm: dict) -> float:
    """Print one arm's final table and return its total cost."""
    rows = arm["rows"]
    first, last = _thirds(rows)
    first_mean = _mean([row["blue_score"] for row in first])
    last_mean = _mean([row["blue_score"] for row in last])
    stats = _arm_stats(rows)
    total_cost = sum(float(row.get("cost", 0.0) or 0.0) for row in rows)
    print(f"Campaign done: {len(rows)} episodes (name={arm['name']})")
    print(f"Sensors: {arm.get('sensors', 'on')}")
    if arm.get("playbook_frozen"):
        print("Playbook: frozen (referee never updated it)")
    print("episode | outcome | blue_score | cost")
    for row in rows:
        print(f"{row['episode']} | {row['outcome']} | {row['blue_score']} "
              f"| ${float(row.get('cost', 0.0) or 0.0):.4f}")
    if first_mean is not None and last_mean is not None:
        print(f"Mean blue_score first third: {first_mean:.2f} "
              f"vs last third: {last_mean:.2f}")
    print(f"{arm['name']}: red /admin {stats['admin_count']}/"
          f"{stats['episodes']} ({stats['admin_rate']}) | red in "
          f"{stats['in_count']}/{stats['episodes']} ({stats['in_rate']}) | "
          f"wins {stats['wins']}")
    for line in orchestrator.format_intrusion_lines(
            orchestrator.intrusion_conditional_summary(rows)):
        print(line)
    print(f"Total cost: ${total_cost:.4f}")
    print(f"CSV: {arm['dir'] / 'summary.csv'}")
    return total_cost


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        if args.arms:
            if args.memory is not None:
                raise ValueError("--memory conflicts with --arms; use "
                                 "name:on,name:off suffixes instead")
            specs = _parse_arms(args.arms)
        else:
            if not args.name:
                raise ValueError("--name is required without --arms")
            if args.memory is None:
                raise ValueError("--memory is required without --arms")
            specs = [(_check_name(args.name), args.memory)]
    except ValueError as exc:
        print(f"campaign: {exc}", file=sys.stderr)
        return 1
    per_scenario = int(args.episodes)
    if per_scenario < 1:
        print("--episodes must be at least 1", file=sys.stderr)
        return 1
    try:
        keys = select.selection(args.scenarios, args.scenario_set,
                                args.difficulty)
    except ValueError as exc:
        print(f"campaign: {exc}", file=sys.stderr)
        return 1
    labels = [select.label(key) for key in keys]
    total = per_scenario * len(keys)
    try:
        blue_client = llm.custom_client(args.blue_base_url, args.blue_api_key_env)
    except RuntimeError as exc:
        print(f"campaign: {exc}", file=sys.stderr)
        return 1
    campaign_start = run_exercise._spend_line_count()

    arms = []
    for name, memory in specs:
        arm = _open_arm(name, memory, total, args, labels)
        if arm is None:
            return 1
        arms.append(arm)
    if not args.resume:
        referee.save_playbook(referee.empty_playbook())

    grouped = len(keys) > 1
    built = False
    try:
        for key in keys:
            label = select.label(key)
            try:
                info = stack.activate(key)
            except ValueError as exc:
                print(f"campaign: {exc}", file=sys.stderr)
                return 1
            print(f"Booting sandbox (scenario={label})...")
            try:
                if built:
                    sandbox.start_warm()
                else:
                    sandbox.start()
            except sandbox.SandboxError as exc:
                print(f"Sandbox boot failed: {exc}", file=sys.stderr)
                print("Check that Docker is running, then try again.",
                      file=sys.stderr)
                return 1
            built = True
            for number in range(1, per_scenario + 1):
                for arm in arms:
                    done = {(row.get("scenario", "classic"),
                             row.get("episode_no")) for row in arm["rows"]}
                    if (label, number) in done:
                        continue
                    seed = f"pair-{label}-{number}" if len(arms) > 1 \
                        and grouped else (
                            f"pair-{number}" if len(arms) > 1 else None)
                    _run_episode(arm, number, args, seed, blue_client,
                                 info, label, grouped)
            try:
                sandbox.stop()
            except sandbox.SandboxError as exc:
                print(f"Sandbox stop noted: {exc}", file=sys.stderr)
    finally:
        print("Stopping sandbox...")
        try:
            sandbox.stop()
        except sandbox.SandboxError as exc:
            print(f"Sandbox stop noted: {exc}", file=sys.stderr)
        tools.reset_state()
        stack.deactivate()

    for arm in arms:
        _report_arm(arm)
    baseten_line = run_exercise.format_baseten_line(
        run_exercise.baseten_tokens_since(campaign_start))
    if baseten_line:
        print(baseten_line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
