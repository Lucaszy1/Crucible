#!/usr/bin/env python3
"""Parity harness: Stage 1 ChainRed against real vs simulated hosts.

For each (seed, difficulty), boots the rendered toyhost stack with
noise off, runs the spec's derived chain plan through the real
tools, then diffs the container logs line by line against the
in-memory Range simulator running the same plan. Request lines mask
their from= address (the relay IP varies run to run); everything
else must match verbatim. Every mismatch is reported; the exit code
is 1 when any scenario diverges.

Needs Docker; never run from the hermetic test suite. Unit tests
drive run_suite with fake hooks instead.

Examples:
  python -m crucible.parity --seeds smoke
  python -m crucible.parity --seeds 1,2,3 --difficulty hard --leave-up
"""

from __future__ import annotations

import argparse
import re
import sys

from crucible import orchestrator, sandbox, tools
from crucible.scenarios import chain as chain_mod
from crucible.scenarios import generate, render, seeds, simulate, stack
from crucible.scenarios.schema import DIFFICULTIES

_FROM_RE = re.compile(r"from=\S+")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Diff container logs against the Range simulator.")
    parser.add_argument(
        "--seeds",
        default="smoke",
        help="smoke runs seeds 1,2,3; otherwise a comma list of ints",
    )
    parser.add_argument(
        "--difficulty",
        default=",".join(DIFFICULTIES),
        help="Comma list of easy, medium, hard, frontier (default: all four)",
    )
    parser.add_argument(
        "--leave-up",
        action="store_true",
        help="Leave the last stack running instead of stopping it",
    )
    return parser.parse_args(argv)


def parse_seeds(raw: str) -> list:
    """Expand --seeds into an int list."""
    cleaned = (raw or "").strip().lower()
    if cleaned == "smoke":
        return list(seeds.SMOKE_SEEDS)
    out = []
    for piece in cleaned.split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            out.append(int(piece))
        except ValueError:
            raise ValueError(
                f"seeds must be 'smoke' or ints: {raw!r}") from None
    if not out:
        raise ValueError(f"seeds must be 'smoke' or ints: {raw!r}")
    return out


def parse_difficulties(raw: str) -> list:
    """Expand --difficulty into a difficulty list."""
    out = [piece.strip() for piece in (raw or "").split(",")
           if piece.strip()]
    if not out:
        raise ValueError("difficulty needs at least one value")
    for item in out:
        if item not in DIFFICULTIES:
            raise ValueError(f"difficulty {item!r} must be one of "
                             f"{', '.join(DIFFICULTIES)}")
    return out


def normalize_line(line: str) -> str:
    """Mask the client address on request lines for comparison.

    Everything else compares verbatim: sign-in, file, and directory
    lines carry no run-varying fields.
    """
    text = str(line or "")
    if text.startswith("request "):
        return _FROM_RE.sub("from=*", text, count=1)
    return text


def compare_logs(expected: dict, actual: dict) -> list:
    """Diff expected vs actual {host: [lines]}, returning mismatches.

    Each mismatch names the host, the 0-based line index, and both
    sides (None when one side ran out of lines).
    """
    mismatches = []
    for host in sorted(set(expected) | set(actual)):
        want = expected.get(host, [])
        got = actual.get(host, [])
        for index in range(max(len(want), len(got))):
            left = want[index] if index < len(want) else None
            right = got[index] if index < len(got) else None
            if left != right:
                mismatches.append({"host": host, "index": index,
                                   "expected": left, "actual": right})
    return mismatches


def expected_logs(spec: dict, plan: list) -> dict:
    """Run one plan through the simulator, returning {host: [lines]}."""
    runner = simulate.Range(spec)
    for action in plan:
        runner.run_action({"tool": action["tool"],
                           "args": dict(action.get("args", {}))})
    return {host: [normalize_line(line) for _ref, line in lines]
            for host, lines in runner.logs.items()}


def run_one(spec: dict, run_tool, read_logs) -> dict:
    """Run one chain plan via hooks, diffing live logs vs simulated.

    run_tool(tool, args) executes one action; read_logs(host) returns
    that host's scoped log lines. Returns the plan results plus the
    line mismatches (live action failures count as mismatches).
    """
    plan = chain_mod.chain_plan(spec)
    results = []
    failures = []
    for move_no, action in enumerate(plan, start=1):
        try:
            result = run_tool(action["tool"],
                              dict(action.get("args", {})))
        except Exception as exc:  # noqa: BLE001 (harness reports it)
            result = {"ok": False, "error": str(exc)[:200]}
        results.append(result)
        if result.get("ok") is not True:
            failures.append({"host": "", "index": move_no,
                             "expected": "live action ok",
                             "actual": f"{action['tool']} failed: "
                                       f"{result.get('error', result)}"})
    live = {}
    for host in [item["name"] for item in spec.get("hosts", [])]:
        try:
            lines = read_logs(host)
        except Exception as exc:  # noqa: BLE001 (harness reports it)
            failures.append({"host": host, "index": -1,
                             "expected": "readable logs",
                             "actual": f"unreadable: {exc}"})
            lines = []
        live[host] = [normalize_line(line) for line in lines or []]
    mismatches = compare_logs(expected_logs(spec, plan), live)
    return {"results": results, "mismatches": failures + mismatches}


def run_suite(specs: list, hooks: dict) -> dict:
    """Run every spec through hook-driven stacks, collecting reports.

    hooks carries boot(built), stop(), begin(), run_tool(tool, args),
    and read_logs(host). Returns per-scenario reports plus totals.
    """
    reports = []
    for spec in specs:
        hooks["activate"](spec)
        hooks["boot"]()
        try:
            hooks["begin"]()
            plan_outcome = run_one(spec, hooks["run_tool"],
                                   hooks["read_logs"])
        finally:
            hooks["stop"]()
        reports.append({"scenario_id": spec.get("scenario_id", ""),
                        "seed": spec.get("seed"),
                        "difficulty": spec.get("difficulty", ""),
                        "moves": len(chain_mod.chain_plan(spec)),
                        "mismatches": plan_outcome["mismatches"]})
    total = sum(len(report["mismatches"]) for report in reports)
    return {"reports": reports,
            "scenarios": len(reports),
            "mismatches": total}


def _live_hooks() -> dict:
    """Build the Docker-backed hooks for main()."""
    built = {"done": False}

    def boot():
        if built["done"]:
            sandbox.start_warm()
        else:
            sandbox.start()
            built["done"] = True

    def begin():
        tools.reset_state()
        tools.begin_episode()

    def activate(spec):
        render.write_stack(spec, sandbox.SANDBOX_DIR, noise=False)
        sandbox.set_active_spec(spec)

    def stop():
        try:
            sandbox.stop()
        finally:
            tools.reset_state()

    return {"boot": boot, "begin": begin,
            "run_tool": orchestrator.run_tool,
            "read_logs": lambda host: sandbox.container_logs(host,
                                                             tail=None),
            "activate": activate, "stop": stop}


def format_report(report: dict) -> list:
    """Render one scenario's mismatches as plain lines."""
    lines = [f"{report['scenario_id']}: {report['moves']} moves, "
             f"{len(report['mismatches'])} mismatches"]
    for item in report["mismatches"]:
        where = f"{item['host']}:{item['index']}" if item["host"] \
            else f"move {item['index']}"
        lines.append(f"  {where} expected={item['expected']!r} "
                     f"actual={item['actual']!r}")
    return lines


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        seed_list = parse_seeds(args.seeds)
        difficulties = parse_difficulties(args.difficulty)
    except ValueError as exc:
        print(f"parity: {exc}", file=sys.stderr)
        return 1
    try:
        specs = [generate.generate(seed, difficulty)
                 for difficulty in difficulties for seed in seed_list]
    except ValueError as exc:
        print(f"parity: {exc}", file=sys.stderr)
        return 1
    hooks = _live_hooks()
    if args.leave_up:
        real_stop = hooks["stop"]
        calls = {"count": 0}

        def stop_unless_last():
            calls["count"] += 1
            if calls["count"] < len(specs):
                real_stop()

        hooks["stop"] = stop_unless_last
    try:
        outcome = run_suite(specs, hooks)
    finally:
        stack.deactivate()
    for report in outcome["reports"]:
        for line in format_report(report):
            print(line)
    print(f"Parity: {outcome['scenarios']} scenarios, "
          f"{outcome['mismatches']} mismatches")
    return 1 if outcome["mismatches"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
