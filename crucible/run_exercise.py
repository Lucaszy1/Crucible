#!/usr/bin/env python3
"""Run one validation exercise from the terminal.

Boots the sandbox, runs a single exercise with scripted or model agents,
prints a short summary with the jsonl path, then tears the range down.

Examples:
  python -m crucible.run_exercise --agents scripted
  python -m crucible.run_exercise --agents model --exercise-id demo-1
"""

from __future__ import annotations

import argparse
import json
import sys

from crucible import llm, orchestrator, sandbox, tools
from crucible.agents import ModelBlue, ModelRed, ScriptedBlue, ScriptedRed


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run one Crucible exercise.")
    parser.add_argument(
        "--agents",
        choices=("scripted", "model"),
        default="scripted",
        help="scripted runs the zero-cost baseline, model uses Muse Spark",
    )
    parser.add_argument(
        "--exercise-id",
        default=None,
        help="Name for the jsonl log under runs/",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=30,
        help="Turns to attempt, capped at 30",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Optional jsonl path for the event log",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit nonzero if any turn fell back to the scripted baseline",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Skip the on-disk model response cache for this run",
    )
    return parser.parse_args(argv)


def _count_driven_vs_fallback(event_list: list) -> tuple:
    """Split logged turns into model-driven and baseline fallback counts."""
    fallback = sum(
        1
        for event in (event_list or [])
        if "using baseline" in str(event.get("reasoning_summary", "")).lower()
    )
    return len(event_list or []) - fallback, fallback


def _summary(score: dict) -> list:
    detect = score.get("time_to_detect")
    delay = score.get("detection_delay")
    contain = score.get("time_to_contain")
    hosts = ", ".join(score.get("compromised_hosts", [])) or "none"
    return [
        f"Turns used: {score.get('turns_used')} of {score.get('total_events')} events",
        f"Outcome: {score.get('outcome', 'unknown')}",
        f"Detected at event: {detect if detect is not None else 'no'}",
        f"Detection delay: {delay if delay is not None else 'no'} events",
        f"Contained at event: {contain if contain is not None else 'no'}",
        f"Hosts compromised: {score.get('hosts_compromised')} ({hosts})",
        f"Secret exfiltrated: {'yes' if score.get('secret_exfiltrated') else 'no'}",
        f"Downtime cost: {score.get('downtime_cost', 0)}",
        f"Blue score: {score.get('blue_score', 0)}",
    ]


def _spend_line_count() -> int:
    """Count spend log lines so one exercise can measure its own cost."""
    try:
        text = llm.SPEND_FILE.read_text(encoding="utf-8")
    except OSError:
        return 0
    return len([line for line in text.splitlines() if line.strip()])


def exercise_cost_since(start_lines: int) -> float:
    """Sum cost_usd for spend entries appended after a snapshot."""
    try:
        lines = llm.SPEND_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0.0
    total = 0.0
    for line in [entry for entry in lines if entry.strip()][start_lines:]:
        try:
            total += float(json.loads(line).get("cost_usd", 0.0))
        except (ValueError, TypeError, AttributeError):
            continue
    return total


def exercise_cost_for_id(exercise_id: str | None) -> float:
    """Sum recorded cost for one exercise id (0.0 when unknown)."""
    if exercise_id is None:
        return 0.0
    try:
        lines = llm.SPEND_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0.0
    total = 0.0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("exercise_id") == exercise_id:
            try:
                total += float(entry.get("cost_usd", 0.0))
            except (TypeError, ValueError):
                continue
    return total


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.agents == "model":
        red = ModelRed(exercise_id=args.exercise_id, use_cache=not args.no_cache)
        blue = ModelBlue(exercise_id=args.exercise_id, use_cache=not args.no_cache)
    else:
        red = ScriptedRed()
        blue = ScriptedBlue()

    print(f"Booting sandbox for one {args.agents} exercise...")
    try:
        sandbox.start()
    except sandbox.SandboxError as exc:
        print(f"Sandbox boot failed: {exc}", file=sys.stderr)
        print("Check that Docker is running, then try again.", file=sys.stderr)
        return 1

    spend_start = _spend_line_count()
    try:
        tools.restore_host("web")
        tools.restore_host("db")
        result = orchestrator.run_exercise(
            red,
            blue,
            max_turns=args.max_turns,
            run_path=args.out,
            exercise_id=args.exercise_id,
        )
    finally:
        print("Stopping sandbox...")
        try:
            sandbox.stop()
        except sandbox.SandboxError as exc:
            print(f"Sandbox stop noted: {exc}", file=sys.stderr)
        tools.reset_state()

    print(f"Exercise done (agents={args.agents})")
    for line in _summary(result["score"]):
        print(line)
    model_driven, fallback = _count_driven_vs_fallback(result["events"])
    print(f"Model-driven turns: {model_driven}, fallback turns: {fallback}")
    cost = exercise_cost_since(spend_start)
    print(f"Estimated cost: ${cost:.4f}")
    print(f"Log: {result['run_path']}")
    if args.strict and fallback:
        print(
            f"Strict mode: {fallback} turn(s) fell back to baseline.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
