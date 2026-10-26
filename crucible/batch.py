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

from crucible import events, orchestrator, run_exercise, sandbox, tools
from crucible.agents import ModelBlue, ModelRed, ScriptedBlue, ScriptedRed

CSV_FIELDS = (
    "exercise_id",
    "outcome",
    "detection_delay",
    "time_to_contain",
    "downtime_cost",
    "blue_score",
    "red_attempts",
    "model_turns",
    "fallback_turns",
    "estimated_cost_usd",
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
        default=30,
        help="Turns to attempt per exercise, capped at 30",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Skip the on-disk model response cache for this batch",
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
                blue = ModelBlue(exercise_id=exercise_id, use_cache=not args.no_cache)
            else:
                red = ScriptedRed()
                blue = ScriptedBlue()
            tools.restore_host("web")
            tools.restore_host("db")
            tools.restore_host("workstation")
            spend_start = run_exercise._spend_line_count()
            result = orchestrator.run_exercise(
                red,
                blue,
                max_turns=args.max_turns,
                exercise_id=exercise_id,
            )
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
                    "blue_score": score.get("blue_score", 0),
                    "red_attempts": _red_attempts(result.get("events", [])),
                    "model_turns": model_turns,
                    "fallback_turns": fallback_turns,
                    "estimated_cost_usd": round(cost, 6),
                }
            )
            delay = score.get("detection_delay")
            print(
                f"{exercise_id}: outcome={score.get('outcome', 'unknown')} "
                f"delay={delay if delay is not None else 'no'} "
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
            if out["detection_delay"] is None:
                out["detection_delay"] = ""
            if out["time_to_contain"] is None:
                out["time_to_contain"] = ""
            writer.writerow(out)

    red_wins = sum(1 for row in rows if row["outcome"] == "red_succeeded")
    blue_holds = sum(1 for row in rows if row["outcome"] == "blue_held")
    stalemates = sum(1 for row in rows if row["outcome"] == "stalemate")
    mean_delay = _mean(
        [row["detection_delay"] for row in rows if row["detection_delay"] is not None]
    )
    mean_blue = _mean([row["blue_score"] for row in rows])
    total_cost = sum(row["estimated_cost_usd"] for row in rows)

    print(f"Batch done: {len(rows)} exercises (prefix={args.prefix})")
    print(f"Red wins: {red_wins}, blue holds: {blue_holds}, stalemates: {stalemates}")
    if mean_delay is None:
        print("Mean detection delay: no detections")
    else:
        print(f"Mean detection delay: {mean_delay:.2f} events")
    print(f"Mean blue_score: {mean_blue:.2f}" if mean_blue is not None else "Mean blue_score: no")
    print(f"Total estimated cost: ${total_cost:.4f}")
    print(f"CSV: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
