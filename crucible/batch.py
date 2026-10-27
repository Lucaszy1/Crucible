#!/usr/bin/env python3
"""Run a batch of validation exercises and summarize the results.

Boots the sandbox once, runs N exercises with scripted or model agents,
writes one CSV row per exercise under runs/batch-<prefix>.csv, then
prints per-exercise costs plus an aggregate summary.

Examples:
  python -m crucible.batch --count 3 --agents scripted --prefix day4
  python -m crucible.batch --count 5 --agents model --prefix tryout --no-cache
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from crucible import events, llm, orchestrator, run_exercise, sandbox, tools
from crucible.agents import ModelBlue, ModelRed, ScriptedBlue, ScriptedRed

CSV_FIELDS = (
    "exercise_id",
    "outcome",
    "detection_delay",
    "time_to_contain",
    "downtime_cost",
    "downtime_pct",
    "blue_score",
    "justified_pct",
    "unsupported_count",
    "invalid_refs_count",
    "mean_audit_score",
    "red_attempts",
    "model_turns",
    "fallback_turns",
    "estimated_cost_usd",
    "sensors",
    "intrusion",
    "contained_after_intrusion",
    "secret_read",
    "relevant_detection_delay",
)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run a batch of exercises.")
    parser.add_argument(
        "--count",
        type=int,
        required=True,
        help="Number of exercises to run",
    )
    parser.add_argument(
        "--agents",
        choices=("scripted", "model"),
        default="scripted",
        help="scripted runs the zero-cost baseline, model uses Muse Spark",
    )
    parser.add_argument(
        "--prefix",
        required=True,
        help="Prefix for exercise ids; CSV lands at runs/batch-<prefix>.csv",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=40,
        help="Turns to attempt per exercise, capped at 40",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Skip the on-disk model response cache for this batch",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Abort the batch if the spend cap forces scripted fallback",
    )
    parser.add_argument(
        "--audit",
        choices=("off", "end"),
        default="off",
        help="off skips judging (saves cost), end audits after each exercise",
    )
    parser.add_argument(
        "--sensors",
        choices=("on", "off"),
        default="on",
        help="on shows blue new sensor alerts each turn, "
        "off leaves blue on logs alone",
    )
    parser.add_argument(
        "--blue-model",
        default=None,
        help="Model id for blue (default: the blue role model)",
    )
    parser.add_argument(
        "--blue-base-url",
        default=None,
        help="Custom API base URL for blue",
    )
    parser.add_argument(
        "--blue-api-key-env",
        default=None,
        help="Env var holding the API key for blue's custom host",
    )
    parser.add_argument(
        "--blue-reasoning-effort",
        choices=("omit", "minimal", "low", "medium", "high"),
        default=None,
        help="Reasoning effort for blue's calls only (default: today's "
        "behavior; omit sends no reasoning_effort field at all)",
    )
    return parser.parse_args(argv)


def _mean(values: list) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _csv_path(prefix: str) -> Path:
    return events.RUNS_DIR / f"batch-{prefix}.csv"


def _red_attempts(event_list: list) -> str:
    """Count red try_credential calls per host, like db=2, web=1."""
    counts: dict = {}
    for event in event_list or []:
        if event.get("actor") != "red" or event.get("tool") != "try_credential":
            continue
        args = event.get("args", {}) or {}
        result = event.get("result", {}) or {}
        host = args.get("host") or result.get("host") or "unknown"
        counts[host] = counts.get(host, 0) + 1
    return ", ".join(f"{host}={counts[host]}" for host in sorted(counts))


def main(argv=None) -> int:
    args = parse_args(argv)
    count = int(args.count)
    if count < 1:
        print("--count must be at least 1", file=sys.stderr)
        return 1
    try:
        blue_client = llm.custom_client(args.blue_base_url, args.blue_api_key_env)
    except RuntimeError as exc:
        print(f"batch: {exc}", file=sys.stderr)
        return 1
    batch_start = run_exercise._spend_line_count()

    print(f"Booting sandbox for a batch of {count} {args.agents} exercises...")
    try:
        sandbox.start()
    except sandbox.SandboxError as exc:
        print(f"Sandbox boot failed: {exc}", file=sys.stderr)
        print("Check that Docker is running, then try again.", file=sys.stderr)
        return 1

    rows: list[dict] = []
    try:
        for index in range(count):
            exercise_id = f"{args.prefix}-{index + 1}"
            if args.agents == "model":
                red = ModelRed(exercise_id=exercise_id, use_cache=not args.no_cache)
                blue = ModelBlue(exercise_id=exercise_id, use_cache=not args.no_cache,
                                 client=blue_client, model=args.blue_model,
                                 reasoning_effort=args.blue_reasoning_effort)
            else:
                red = ScriptedRed()
                blue = ScriptedBlue()
            tools.restore_host("web")
            tools.restore_host("app")
            tools.restore_host("db")
            tools.restore_host("workstation")
            spend_start = run_exercise._spend_line_count()
            result = orchestrator.run_exercise(
                red,
                blue,
                max_turns=args.max_turns,
                exercise_id=exercise_id,
                audit=args.audit,
                sensors=args.sensors,
                blue_reasoning_effort=args.blue_reasoning_effort,
            )
            if run_exercise._spend_cap_hit(result.get("events", [])):
                run_exercise._warn_spend_cap()
                if args.strict and args.agents == "model":
                    print(
                        f"Strict mode: spend cap hit on {exercise_id}; "
                        f"aborting batch instead of recording scripted "
                        f"games as model games.",
                        file=sys.stderr,
                    )
                    return 2
            score = result.get("score", {}) or {}
            cost = run_exercise.exercise_cost_since(spend_start)
            model_turns, fallback_turns = run_exercise._count_driven_vs_fallback(
                result.get("events", [])
            )
            rows.append(
                {
                    "exercise_id": exercise_id,
                    "outcome": score.get("outcome", "unknown"),
                    "detection_delay": score.get("detection_delay"),
                    "time_to_contain": score.get("time_to_contain"),
                    "downtime_cost": score.get("downtime_cost", 0),
                    "downtime_pct": score.get("downtime_pct", 0.0),
                    "blue_score": score.get("blue_score", 0),
                    "justified_pct": score.get("justified_pct"),
                    "unsupported_count": score.get("unsupported_count", 0),
                    "invalid_refs_count": score.get("invalid_refs_count", 0),
                    "mean_audit_score": score.get("mean_audit_score"),
                    "red_attempts": _red_attempts(result.get("events", [])),
                    "model_turns": model_turns,
                    "fallback_turns": fallback_turns,
                    "estimated_cost_usd": round(cost, 6),
                    "sensors": score.get("sensors", args.sensors),
                    "intrusion": score.get("intrusion", False),
                    "contained_after_intrusion": score.get(
                        "contained_after_intrusion", False),
                    "secret_read": score.get("secret_read", score.get(
                        "secret_exfiltrated", False)),
                    "relevant_detection_delay": score.get(
                        "relevant_detection_delay"),
                }
            )
            delay = score.get("detection_delay")
            print(
                f"{exercise_id}: outcome={score.get('outcome', 'unknown')} "
                f"delay={delay if delay is not None else 'no'} "
                f"downtime={score.get('downtime_pct', 0.0)}% "
                f"blue_score={score.get('blue_score', 0)} "
                f"cost=${cost:.4f}"
            )
    finally:
        print("Stopping sandbox...")
        try:
            sandbox.stop()
        except sandbox.SandboxError as exc:
            print(f"Sandbox stop noted: {exc}", file=sys.stderr)
        tools.reset_state()

    path = _csv_path(args.prefix)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_FIELDS))
        writer.writeheader()
        for row in rows:
            out = dict(row)
            for key in ("detection_delay", "time_to_contain", "justified_pct",
                        "mean_audit_score", "relevant_detection_delay"):
                if out[key] is None:
                    out[key] = ""
            writer.writerow(out)

    red_wins = sum(1 for row in rows if row["outcome"] == "red_succeeded")
    blue_holds = sum(1 for row in rows if row["outcome"] == "blue_held")
    stalemates = sum(1 for row in rows if row["outcome"] == "stalemate")
    mean_delay = _mean(
        [row["detection_delay"] for row in rows if row["detection_delay"] is not None]
    )
    mean_blue = _mean([row["blue_score"] for row in rows])
    mean_downtime = _mean([row["downtime_pct"] for row in rows])
    total_cost = sum(row["estimated_cost_usd"] for row in rows)

    print(f"Batch done: {len(rows)} exercises (prefix={args.prefix})")
    print(f"Sensors: {args.sensors}")
    print(f"Red wins: {red_wins}, blue holds: {blue_holds}, stalemates: {stalemates}")
    if mean_delay is None:
        print("Mean detection delay: no detections")
    else:
        print(f"Mean detection delay: {mean_delay:.2f} events")
    print(f"Mean downtime: {mean_downtime:.2f}%" if mean_downtime is not None else "Mean downtime: no")
    print(f"Mean blue_score: {mean_blue:.2f}" if mean_blue is not None else "Mean blue_score: no")
    for line in orchestrator.format_intrusion_lines(
            orchestrator.intrusion_conditional_summary(rows)):
        print(line)
    print(f"Total estimated cost: ${total_cost:.4f}")
    baseten_line = run_exercise.format_baseten_line(
        run_exercise.baseten_tokens_since(batch_start))
    if baseten_line:
        print(baseten_line)
    print(f"CSV: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
