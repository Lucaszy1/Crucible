"""Shared helpers for model-driven exercise agents.

Keeps parsing and prompt shaping in one place so the red and blue agents
stay consistent. All model traffic still flows through crucible.llm.
"""

from __future__ import annotations

import json
import re

MAX_TOKENS = 256
TEMPERATURE = 0.0
HISTORY_LIMIT = 6
REASONING_LIMIT = 400

_EVIDENCE_RE = re.compile(r"\b(web|db|workstation):\d+\b")


def summarize_history(history: list, limit: int = HISTORY_LIMIT) -> str:
    """Render recent events as short lines for the model prompt."""
    if not history:
        return "No moves yet."
    lines = []
    for event in (history or [])[-limit:]:
        actor = event.get("actor", "?")
        tool = event.get("tool", "?")
        args = event.get("args", {})
        result = event.get("result", {})
        ok = (result or {}).get("ok", "?")
        try:
            args_text = json.dumps(args, sort_keys=True)[:160]
        except (TypeError, ValueError):
            args_text = str(args)[:160]
        reasoning = str(event.get("reasoning_summary", ""))[:120]
        lines.append(f"{actor} {tool} {args_text} ok={ok} note={reasoning}")
    return "\n".join(lines)


def parse_tool_args(arguments) -> dict:
    """Coerce tool call arguments into a dict, raising ValueError if bad."""
    if arguments is None:
        return {}
    if isinstance(arguments, dict):
        return dict(arguments)
    if not isinstance(arguments, str):
        raise ValueError(f"tool arguments must be JSON: {arguments!r}")
    text = arguments.strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"tool arguments are not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("tool arguments must decode to an object")
    return parsed


def clean_reasoning(content: str, fallback: str) -> str:
    """Return a short reasoning summary from model text."""
    text = " ".join(str(content or "").split())
    if not text:
        text = fallback
    if len(text) > REASONING_LIMIT:
        text = text[:REASONING_LIMIT].rstrip() + "..."
    return text


def extract_evidence_ids(text: str) -> list:
    """Find log line ids like web:3 in free text, deduped in order."""
    ordered = []
    for hit in _EVIDENCE_RE.finditer(str(text or "")):
        ref = hit.group(0)
        if ref not in ordered:
            ordered.append(ref)
    return ordered


def normalize_credential_args(args: dict) -> dict:
    """Accept pass as an alias for password on credential attempts."""
    fixed = dict(args or {})
    if "password" not in fixed and "pass" in fixed:
        fixed["password"] = fixed.pop("pass")
    return fixed
