#!/usr/bin/env python3
"""Probe Muse Spark Open on Baseten before depending on it.

Makes a handful of tiny live calls (the only live calls in this task)
and reports placement of reasoning vs answer, tool-call shapes,
truncation behavior, and which parameters Baseten accepts.

Reads the key from env BASETEN_API_KEY (via .env, never printed).
Aborts past a call/token budget. Prints token counts, latency and
finish_reason for every call.

Usage:
  python scripts/probe_baseten.py
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE_URL = "https://inference.baseten.co/v1"
MODEL = "meta-models/muse-spark-open-1026"

MAX_CALLS = 26
MAX_TOTAL_TOKENS = 120_000
CALL_TIMEOUT_S = 180

CONTENT_PREVIEW = 300
REASONING_PREVIEW = 200


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    load_dotenv(os.path.join(here, ".env"), override=False)


class Budget:
    def __init__(self) -> None:
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.reasoning_tokens = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def check(self) -> None:
        if self.calls >= MAX_CALLS:
            raise SystemExit(f"probe budget hit: {self.calls} calls (max {MAX_CALLS})")
        if self.total_tokens >= MAX_TOTAL_TOKENS:
            raise SystemExit(
                f"probe budget hit: {self.total_tokens} tokens (max {MAX_TOTAL_TOKENS})"
            )


def _preview(text, limit: int) -> str:
    text = text if isinstance(text, str) else json.dumps(text, default=str)
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit] + "..."


def probe_call(client, budget: Budget, label: str, **kwargs):
    """One raw call; returns the wire-decoded body (never raises on HTTP)."""
    budget.check()
    started = time.monotonic()
    try:
        raw = client.chat.completions.with_raw_response.create(
            model=MODEL, timeout=CALL_TIMEOUT_S, **kwargs
        )
        status = raw.status_code
        wire = json.loads(raw.text)
        error = None
    except Exception as exc:  # noqa: BLE001 - probe must report, not crash
        latency = time.monotonic() - started
        print(f"[{label}] TRANSPORT/CLIENT ERROR in {latency:.1f}s: "
              f"{type(exc).__name__}: {exc}")
        return None
    latency = time.monotonic() - started
    budget.calls += 1
    if status != 200 or "choices" not in wire:
        print(f"[{label}] HTTP {status} in {latency:.1f}s")
        print(f"[{label}] body: {_preview(json.dumps(wire), 500)}")
        return {"_label": label, "_http_error": status, "_wire": wire}
    choice = wire["choices"][0]
    message = choice.get("message", {})
    usage = wire.get("usage", {})
    prompt_t = int(usage.get("prompt_tokens", 0) or 0)
    completion_t = int(usage.get("completion_tokens", 0) or 0)
    details = usage.get("completion_tokens_details") or {}
    reasoning_t = int(details.get("reasoning_tokens", 0) or 0)
    budget.prompt_tokens += prompt_t
    budget.completion_tokens += completion_t
    budget.reasoning_tokens += reasoning_t
    content = message.get("content")
    reasoning = message.get("reasoning_content")
    calls = message.get("tool_calls") or []
    print(f"[{label}] HTTP 200 in {latency:.1f}s "
          f"finish={choice.get('finish_reason')} "
          f"prompt={prompt_t} completion={completion_t} "
          f"reasoning={reasoning_t} calls={len(calls)}")
    print(f"[{label}] message keys: {sorted(message.keys())}")
    print(f"[{label}] content ({len(content) if isinstance(content, str) else type(content).__name__}): "
          f"{_preview(content if content is not None else '', CONTENT_PREVIEW)}")
    if reasoning is None:
        print(f"[{label}] reasoning_content: ABSENT")
    else:
        print(f"[{label}] reasoning_content ({len(reasoning)} chars): "
              f"{_preview(reasoning, REASONING_PREVIEW)}")
    for call in calls:
        func = call.get("function", {})
        args = func.get("arguments")
        print(f"[{label}] tool_call id={call.get('id')!r} "
              f"name={func.get('name')!r} "
              f"arguments-type={type(args).__name__} arguments={_preview(args, 200)}")
    return {
        "_label": label, "_http_error": None, "_wire": wire,
        "finish_reason": choice.get("finish_reason"),
        "content": content, "reasoning_content": reasoning,
        "tool_calls": calls,
        "prompt_tokens": prompt_t, "completion_tokens": completion_t,
        "reasoning_tokens": reasoning_t,
    }


ISOLATE_TOOL = {
    "type": "function",
    "function": {
        "name": "isolate_host",
        "description": "Cut one host off the network.",
        "parameters": {
            "type": "object",
            "properties": {"host": {"type": "string"}},
            "required": ["host"],
        },
    },
}


def blue_messages():
    from crucible.agents.blue import SYSTEM_PROMPT

    observation = (
        "Turn 1 of 40. Sensor alert: successful sign-in on host app "
        "(user svc-report) at 02:10, first sign-in seen this exercise. "
        "Asset inventory: web runs the webapp on port 8000, app runs "
        "reportapi, db runs fakestore, workstation is a desktop. "
        "No prior moves yet. Respond with exactly one tool call."
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": observation},
    ]


def blue_tools():
    from crucible.agents.blue import TOOL_SCHEMAS

    return TOOL_SCHEMAS


def section(title: str) -> None:
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


def main() -> int:
    _load_env()
    if not os.environ.get("BASETEN_API_KEY"):
        print("BASETEN_API_KEY is not set; add it to .env (never print it).",
              file=sys.stderr)
        return 2
    # The key stays inside the client; only its presence is ever shown.
    print(f"endpoint: {BASE_URL} model: {MODEL} key: present (hidden)")
    print(f"budget: max {MAX_CALLS} calls, max {MAX_TOTAL_TOKENS} tokens")

    from openai import OpenAI

    client = OpenAI(base_url=BASE_URL, api_key=os.environ["BASETEN_API_KEY"])
    budget = Budget()
    results = {}

    section("(a) plain chat: where do reasoning and answer appear?")
    results["a_plain"] = probe_call(
        client, budget, "a-plain",
        messages=[{"role": "user", "content": "Reply with exactly one word: ok."}],
        max_tokens=512,
    )
    results["a_length"] = probe_call(
        client, budget, "a-length",
        messages=[{"role": "user",
                   "content": "Write one full sentence about host app."}],
        max_tokens=4,
    )

    section("(b) tool calling: shape, ids, multiples, tool_choice")
    results["b_single"] = probe_call(
        client, budget, "b-single",
        messages=[{"role": "user",
                   "content": "Host app is compromised. Isolate it now with the tool."}],
        tools=[ISOLATE_TOOL],
        max_tokens=1024,
    )
    results["b_multi"] = probe_call(
        client, budget, "b-multi",
        messages=[{"role": "user",
                   "content": "Hosts app and db are both compromised. "
                              "Isolate both of them now with the tool."}],
        tools=[ISOLATE_TOOL],
        max_tokens=1024,
    )
    results["b_choice"] = probe_call(
        client, budget, "b-choice",
        messages=[{"role": "user",
                   "content": "What should I do about host app?"}],
        tools=[ISOLATE_TOOL],
        tool_choice={"type": "function",
                     "function": {"name": "isolate_host"}},
        max_tokens=1024,
    )

    section("(c) blue-style turn with the real prompt and tools")
    results["c_blue"] = probe_call(
        client, budget, "c-blue",
        messages=blue_messages(),
        tools=blue_tools(),
        temperature=0.5,
        max_tokens=3000,
    )

    section("(d) truncation: which max_tokens still yields a tool call?")
    for limit in (256, 1024, 1536, 3000):
        for rep in (1, 2, 3):
            key = f"d_{limit}_{rep}"
            results[key] = probe_call(
                client, budget, f"d-{limit}r{rep}",
                messages=blue_messages(),
                tools=blue_tools(),
                temperature=0.5,
                max_tokens=limit,
            )

    section("(e) reasoning controls: omitted vs low vs medium")
    results["e_omitted"] = probe_call(
        client, budget, "e-omitted",
        messages=blue_messages(),
        tools=blue_tools(),
        temperature=0.5,
        max_tokens=3000,
    )
    results["e_low"] = probe_call(
        client, budget, "e-low",
        messages=blue_messages(),
        tools=blue_tools(),
        temperature=0.5,
        max_tokens=3000,
        reasoning_effort="low",
    )
    results["e_medium"] = probe_call(
        client, budget, "e-medium",
        messages=blue_messages(),
        tools=blue_tools(),
        temperature=0.5,
        max_tokens=3000,
        reasoning_effort="medium",
    )

    section("(f) temperature 0.0 accepted?")
    results["f_temp0"] = probe_call(
        client, budget, "f-temp0",
        messages=[{"role": "user",
                   "content": "Reply with exactly one word: ok."}],
        temperature=0.0,
        max_tokens=512,
    )

    section("PROBE SUMMARY")
    print(f"calls: {budget.calls} prompt_tokens: {budget.prompt_tokens} "
          f"completion_tokens: {budget.completion_tokens} "
          f"reasoning_tokens: {budget.reasoning_tokens} "
          f"total: {budget.total_tokens}")
    print()
    print(f"{'call':<12}{'http':<6}{'finish':<10}{'prompt':>7} "
          f"{'compl':>6} {'reas':>6} {'calls':>5} content?")
    for key, res in results.items():
        if res is None:
            print(f"{key:<12}{'ERR':<6}{'-':<10}{'-':>7} "
                  f"{'-':>6} {'-':>6} {'-':>5} transport error")
            continue
        if res.get("_http_error"):
            print(f"{key:<12}{res['_http_error']:<6}{'-':<10}{'-':>7} "
                  f"{'-':>6} {'-':>6} {'-':>5} HTTP error (see above)")
            continue
        content = res.get("content")
        has_content = bool(isinstance(content, str) and content.strip())
        print(f"{key:<12}{'200':<6}{str(res.get('finish_reason')):<10}"
              f"{res.get('prompt_tokens', 0):>7} "
              f"{res.get('completion_tokens', 0):>6} "
              f"{res.get('reasoning_tokens', 0):>6} "
              f"{len(res.get('tool_calls') or []):>5} "
              f"{'yes' if has_content else 'NO'}")
    d_rows = []
    for limit in (256, 1024, 1536, 3000):
        hits = sum(
            1 for rep in (1, 2, 3)
            if (results.get(f"d_{limit}_{rep}") or {}).get("tool_calls")
        )
        d_rows.append(f"max_tokens={limit}: {hits}/3 tool calls")
    print()
    print("truncation: " + " | ".join(d_rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
