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
import sys

from crucible import orchestrator, sandbox, tools
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
        default=12,
        help="Turns to attempt, capped at 12",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Optional jsonl path for the event log",
    )
    return parser.parse_args(argv)


def _summary(score: dict) -> list:
    detect = score.get("time_to_detect")
    contain = score.get("time_to_contain")
    hosts = ", ".join(score.get("compromised_hosts", [])) or "none"
    return [
        f"Turns used: {score.get('turns_used')} of {score.get('total_events')} events",
        f"Detected at event: {detect if detect is not None else 'no'}",
        f"Contained at event: {contain if contain is not None else 'no'}",
        f"Hosts compromised: {score.get('hosts_compromised')} ({hosts})",
        f"Secret exfiltrated: {'yes' if score.get('secret_exfiltrated') else 'no'}",
    ]


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.agents == "model":
        red = ModelRed(exercise_id=args.exercise_id)
        blue = ModelBlue(exercise_id=args.exercise_id)
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
    print(f"Log: {result['run_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
