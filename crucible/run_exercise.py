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
from crucible.scenarios import select, stack


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
        default=40,
        help="Turns to attempt, capped at 60",
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
    parser.add_argument(
        "--audit",
        choices=("off", "end", "live"),
        default="end",
        help="off skips judging, end audits after the exercise, "
        "live audits each blue action as it happens",
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
    parser.add_argument(
        "--scenario",
        default="classic",
        help="classic runs the hand-built range; "
        "gen:<seed>:<difficulty> runs a generated spec",
    )
    return parser.parse_args(argv)


SPEND_CAP_MARKER = "daily model spend cap reached"


def _count_driven_vs_fallback(event_list: list) -> tuple:
    """Split logged turns into model-driven and baseline fallback counts."""
    fallback = sum(
        1
        for event in (event_list or [])
        if "using baseline" in str(event.get("reasoning_summary", "")).lower()
    )
    return len(event_list or []) - fallback, fallback


def _spend_cap_hit(event_list: list) -> bool:
    """Check whether any turn fell back because the spend cap blocked it."""
    return any(
        SPEND_CAP_MARKER in str(event.get("reasoning_summary", "")).lower()
        for event in (event_list or [])
    )


def _degraded_hit(event_list: list) -> bool:
    """Check whether any turn fell back after a model error.

    Red turns carry the flag on the event; auditor verdicts carry it
    in their result payload.
    """
    for event in event_list or []:
        if event.get("degraded") is True:
            return True
        result = event.get("result", {}) or {}
        if result.get("degraded") is True:
            return True
    return False


def _warn_spend_cap() -> None:
    """Print a loud warning with the spend limit and amount used."""
    try:
        limit = float(llm.daily_limit_usd())
    except (TypeError, ValueError):
        limit = float(llm.DEFAULT_DAILY_LIMIT_USD)
    try:
        used = float(llm.spent_today())
    except (TypeError, ValueError, OSError):
        used = 0.0
    print(
        f"WARNING: daily model spend cap reached: "
        f"${used:.2f} of ${limit:.2f} used; "
        f"turns fell back to scripted baseline, not model calls.",
        file=sys.stderr,
    )


def _summary(score: dict) -> list:
    detect = score.get("time_to_detect")
    delay = score.get("detection_delay")
    relevant = score.get("relevant_detection_delay")
    contain = score.get("time_to_contain")
    hosts = ", ".join(score.get("compromised_hosts", [])) or "none"
    try:
        downtime_pct = f"{float(score.get('downtime_pct', 0.0)):.2f}%"
    except (TypeError, ValueError):
        downtime_pct = f"{score.get('downtime_pct', 0.0)}%"
    lines = [
        f"Turns used: {score.get('turns_used')} of {score.get('total_events')} events",
        f"Sensors: {score.get('sensors', 'off')}",
        f"Outcome: {score.get('outcome', 'unknown')}",
        f"Intrusion: {'yes' if score.get('intrusion') else 'no'}",
        f"Detected at event: {detect if detect is not None else 'no'}",
        f"Detection delay: {delay if delay is not None else 'no'} events",
        f"Relevant detection delay: {relevant if relevant is not None else 'no'} events",
        f"Contained at event: {contain if contain is not None else 'no'}",
        f"Contained after intrusion: "
        f"{'yes' if score.get('contained_after_intrusion') else 'no'}",
        f"Hosts compromised: {score.get('hosts_compromised')} ({hosts})",
        f"Secret exfiltrated: {'yes' if score.get('secret_exfiltrated') else 'no'}",
        f"Downtime cost: {score.get('downtime_cost', 0)}",
        f"Downtime: {downtime_pct}",
        f"Blue score: {score.get('blue_score', 0)}",
    ]
    justified = score.get("justified_pct")
    if justified is None:
        lines.append("Audits: none")
    else:
        lines.append(f"Audit justified: {justified}%")
        lines.append(f"Audit unsupported: {score.get('unsupported_count', 0)}")
        lines.append(f"Audit invalid refs: {score.get('invalid_refs_count', 0)}")
        lines.append(f"Audit mean score: {score.get('mean_audit_score')}")
    return lines


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


def baseten_tokens_since(start_lines: int) -> dict:
    """Sum Baseten-hosted token counts logged after a snapshot.

    Dollars stay 0 (Baseten price unknown); only tokens are tracked.
    """
    totals = {
        "calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
    }
    try:
        lines = llm.SPEND_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return totals
    for line in [entry for entry in lines if entry.strip()][start_lines:]:
        try:
            entry = json.loads(line)
        except (ValueError, TypeError, AttributeError):
            continue
        if entry.get("host") != "baseten":
            continue
        try:
            prompt = int(entry.get("input_tokens", 0))
            completion = int(entry.get("output_tokens", 0))
            reasoning = int(entry.get("reasoning_tokens", 0))
        except (TypeError, ValueError):
            continue
        totals["calls"] += 1
        totals["prompt_tokens"] += prompt
        totals["completion_tokens"] += completion
        totals["reasoning_tokens"] += reasoning
        totals["total_tokens"] += prompt + completion
    return totals


def format_baseten_line(totals: dict) -> str | None:
    """One summary line for Baseten tokens, None when there were none."""
    if not totals or not totals.get("calls"):
        return None
    return (
        f"Baseten tokens: {totals['total_tokens']} total "
        f"({totals['prompt_tokens']} in, {totals['completion_tokens']} out, "
        f"{totals['reasoning_tokens']} reasoning, {totals['calls']} calls; "
        f"billed separately, price unknown)"
    )


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
    try:
        key = select.parse_token(args.scenario)
    except ValueError as exc:
        print(f"run_exercise: {exc}", file=sys.stderr)
        return 1
    try:
        info = stack.activate(key)
    except ValueError as exc:
        print(f"run_exercise: {exc}", file=sys.stderr)
        return 1
    # scenario= rides along only for generated runs so the classic
    # call shape stays exactly as it was.
    extra = {"scenario": info} if info else {}
    key_envs = []
    if args.agents == "model" or args.audit != "off":
        key_envs.append("MODEL_API_KEY")
    if args.agents == "model" and args.blue_api_key_env:
        key_envs.append(args.blue_api_key_env)
    for env_var in key_envs:
        try:
            llm.require_api_key(env_var)
        except RuntimeError as exc:
            print(f"run_exercise: {exc}", file=sys.stderr)
            return 1
    if args.agents == "model":
        try:
            blue_client = llm.custom_client(args.blue_base_url, args.blue_api_key_env)
        except RuntimeError as exc:
            print(f"run_exercise: {exc}", file=sys.stderr)
            return 1
        red = ModelRed(exercise_id=args.exercise_id, use_cache=not args.no_cache)
        blue = ModelBlue(exercise_id=args.exercise_id, use_cache=not args.no_cache,
                         client=blue_client, model=args.blue_model,
                         reasoning_effort=args.blue_reasoning_effort)
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
        for host in sandbox.active_hosts():
            tools.restore_host(host)
        result = orchestrator.run_exercise(
            red,
            blue,
            max_turns=args.max_turns,
            run_path=args.out,
            exercise_id=args.exercise_id,
            audit=args.audit,
            auditor_use_cache=not args.no_cache,
            sensors=args.sensors,
            blue_reasoning_effort=args.blue_reasoning_effort,
            **extra,
        )
    finally:
        print("Stopping sandbox...")
        try:
            sandbox.stop()
        except sandbox.SandboxError as exc:
            print(f"Sandbox stop noted: {exc}", file=sys.stderr)
        tools.reset_state()
        stack.deactivate()

    print(f"Exercise done (agents={args.agents}, scenario={select.label(key)})")
    for line in _summary(result["score"]):
        print(line)
    model_driven, fallback = _count_driven_vs_fallback(result["events"])
    print(f"Model-driven turns: {model_driven}, fallback turns: {fallback}")
    cost = exercise_cost_since(spend_start)
    print(f"Estimated cost: ${cost:.4f}")
    baseten_line = format_baseten_line(baseten_tokens_since(spend_start))
    if baseten_line:
        print(baseten_line)
    print(f"Log: {result['run_path']}")
    if _spend_cap_hit(result["events"]):
        _warn_spend_cap()
    if args.strict and fallback:
        print(
            f"Strict mode: {fallback} turn(s) fell back to baseline.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
