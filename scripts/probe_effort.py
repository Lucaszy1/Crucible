#!/usr/bin/env python3
"""Compare blue-turn reasoning effort values on Baseten. At most 6 calls.

Uses the real blue prompt and tools with a small made-up observation.
Reports thinking tokens, first tool called, and latency per effort.
Key comes from BASETEN_API_KEY and is never printed.

Usage:
  python scripts/probe_effort.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import probe_baseten as probe


def main() -> int:
    probe._load_env()
    if not os.environ.get("BASETEN_API_KEY"):
        print("BASETEN_API_KEY is not set; add it to .env (never print it).")
        return 2
    print(f"endpoint: {probe.BASE_URL} model: {probe.MODEL} key: present (hidden)")

    from openai import OpenAI

    client = OpenAI(base_url=probe.BASE_URL, api_key=os.environ["BASETEN_API_KEY"])
    budget = probe.Budget()
    messages = probe.blue_messages()
    tools = probe.blue_tools()

    plan = [
        ("eff-omit", {}),
        ("eff-low", {"reasoning_effort": "low"}),
        ("eff-medium", {"reasoning_effort": "medium"}),
        ("eff-high", {"reasoning_effort": "high"}),
        ("eff-medium-r2", {"reasoning_effort": "medium"}),
        ("eff-high-r2", {"reasoning_effort": "high"}),
    ]
    import time

    results = []
    for label, extra in plan:
        started = time.monotonic()
        res = probe.probe_call(
            client, budget, label, messages=messages, tools=tools,
            temperature=0.5, max_tokens=3000, **extra)
        latency = time.monotonic() - started
        results.append(
            (label, extra.get("reasoning_effort", "omitted"), res, latency))

    print()
    print("EFFORT COMPARISON (blue turn, max_tokens=3000, temp=0.5)")
    print(f"{'effort':<9}{'think':>7} {'first tool':<14} {'latency':>7} finish")
    for label, effort, res, latency in results:
        if res is None or res.get("_http_error"):
            print(f"{effort:<9}{'-':>7} {'ERROR':<14} {'-':>7} "
                  f"{res.get('_http_error') if res else 'transport'}")
            continue
        calls = res.get("tool_calls") or []
        first = calls[0].get("function", {}).get("name", "-") if calls else "-"
        print(f"{effort:<9}{res.get('reasoning_tokens', 0):>7} {first:<14} "
              f"{latency:>6.1f}s {res.get('finish_reason')}")
    print(f"probe tokens this run: {budget.total_tokens} "
          f"({budget.prompt_tokens} in, {budget.completion_tokens} out, "
          f"{budget.reasoning_tokens} reasoning)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
