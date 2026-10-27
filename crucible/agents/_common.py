"""Shared helpers for model-driven exercise agents.

Keeps parsing and prompt shaping in one place so the red and blue agents
stay consistent. All model traffic still flows through crucible.llm.
"""

from __future__ import annotations

import json
import re

MAX_TOKENS = 1536
TEMPERATURE = 0.0
HISTORY_LIMIT = 6
REASONING_LIMIT = 400
DEFAULT_MAX_TURNS = 40
COMPACT_DETAIL_LIMIT = 200
LAST_RESULT_LIMIT = 1500

_EVIDENCE_RE = re.compile(r"\b(web|app|db|workstation):\d+\b")


def active_hosts() -> tuple:
    """Return the hosts valid under the active scenario, classic first."""
    try:
        from crucible import sandbox

        hosts = tuple(sandbox.active_hosts())
    except Exception:
        return ("web", "app", "db", "workstation")
    return hosts or ("web", "app", "db", "workstation")


def host_sentence(hosts: tuple | None = None) -> str:
    """Join host names the way the prompts do ('a, b, and c')."""
    names = list(hosts if hosts is not None else active_hosts())
    if len(names) > 2:
        return ", ".join(names[:-1]) + f", and {names[-1]}"
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    if names:
        return names[0]
    return "no hosts"


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


def summarize_own_recent(history: list, actor: str,
                         limit: int = HISTORY_LIMIT) -> str:
    """Render one team's own recent moves with args and reasoning.

    Information boundary: agent prompts build their recent-moves
    section from this, so neither team ever sees the other team's
    report text or reasoning summaries.
    """
    own = [event for event in (history or []) if event.get("actor") == actor]
    return summarize_history(own, limit=limit)


def _squash(text: str) -> str:
    """Collapse whitespace so a note stays on one line."""
    return " ".join(str(text or "").split())


def _summarize_result(tool: str, result: dict) -> str:
    """Render one tool result as a compact single-line note."""
    ok = (result or {}).get("ok", "?")
    if tool == "read_logs" and (result or {}).get("ok") is True:
        lines = (result or {}).get("lines", []) or []
        ids = [entry.get("id") for entry in lines if entry.get("id")]
        return f"ok lines={len(lines)} last={ids[-3:]}"
    if tool == "http_request":
        note = f"ok={ok} status={(result or {}).get('status', '?')}"
        body = (result or {}).get("body")
        if body:
            note += f" body={_squash(body)[:COMPACT_DETAIL_LIMIT]}"
        error = (result or {}).get("error")
        if error:
            note += f" error={_squash(error)[:COMPACT_DETAIL_LIMIT]}"
        return note
    if tool == "try_credential":
        return f"ok={ok} authenticated={(result or {}).get('authenticated', '?')}"
    if tool == "read_file":
        if (result or {}).get("ok") is True:
            content = str((result or {}).get("content", ""))
            path = str((result or {}).get("path", "?"))[:COMPACT_DETAIL_LIMIT]
            return f"ok path={path} bytes={len(content)}"
        return f"ok=False error={str((result or {}).get('error', ''))[:80]}"
    if tool == "list_files":
        if (result or {}).get("ok") is True:
            files = (result or {}).get("files", []) or []
            names = ", ".join(str(name) for name in files)[:COMPACT_DETAIL_LIMIT]
            path = str((result or {}).get("path", "?"))[:COMPACT_DETAIL_LIMIT]
            return f"ok path={path} files={len(files)} names={names}"
        return f"ok=False error={str((result or {}).get('error', ''))[:80]}"
    if tool == "scan_host":
        return (f"ok={ok} reachable={(result or {}).get('reachable', '?')} "
                f"ports={(result or {}).get('ports', '?')}")
    try:
        return json.dumps(result, sort_keys=True)[:160]
    except (TypeError, ValueError):
        return str(result)[:160]


def summarize_own_history(history: list, actor: str, limit: int = HISTORY_LIMIT) -> str:
    """Render one team's own past moves with compact result notes."""
    own = [event for event in (history or []) if event.get("actor") == actor]
    if not own:
        return "No moves yet."
    lines = []
    for event in own[-limit:]:
        tool = event.get("tool", "?")
        args = event.get("args", {})
        try:
            args_text = json.dumps(args, sort_keys=True)[:160]
        except (TypeError, ValueError):
            args_text = str(args)[:160]
        result_text = _summarize_result(tool, event.get("result", {}) or {})
        lines.append(f"{tool} {args_text} -> {result_text}")
    return "\n".join(lines)


def format_last_result(history: list, actor: str,
                       limit: int = LAST_RESULT_LIMIT) -> str:
    """Render the actor's most recent tool result as full text."""
    for event in reversed(history or []):
        if event.get("actor") != actor:
            continue
        try:
            text = json.dumps(event.get("result", {}), sort_keys=True)
        except (TypeError, ValueError):
            text = str(event.get("result", ""))
        rendered = f"{event.get('tool', '?')} -> {text}"
        if len(rendered) > limit:
            rendered = rendered[:limit].rstrip() + "..."
        return rendered
    return "No prior tool result."


def red_known_state(history: list) -> str:
    """Render a short checklist of what red already learned."""
    scanned: list = []
    no_gate: list = []
    authed: list = []
    for event in history or []:
        if event.get("actor") != "red":
            continue
        args = event.get("args", {}) or {}
        result = event.get("result", {}) or {}
        host = args.get("host") or result.get("host")
        if not host:
            continue
        if event.get("tool") == "scan_host" and host not in scanned:
            scanned.append(host)
        if event.get("tool") == "try_credential":
            if result.get("authenticated") is True and host not in authed:
                authed.append(host)
            if "no sign-in gate" in str(result.get("note", "")) and host not in no_gate:
                no_gate.append(host)
    def _fmt(hosts: list) -> str:
        return ", ".join(sorted(hosts)) if hosts else "none"
    return (
        f"hosts scanned: {_fmt(scanned)}; "
        f"hosts with no sign-in gate: {_fmt(no_gate)}; "
        f"hosts authenticated: {_fmt(authed)}"
    )


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


def fallback_note_for_empty_reply(response: dict) -> str:
    """Explain a model reply that carried no usable tool call."""
    finish_reason = (response or {}).get("finish_reason") or ""
    content = (response or {}).get("content", "")
    if finish_reason == "length":
        return (
            "Model reply hit the token limit "
            "(finish_reason=length), using baseline."
        )
    if not str(content or "").strip():
        if finish_reason:
            return (
                "Model returned an empty reply "
                f"(finish_reason={finish_reason}), using baseline."
            )
        return "Model returned an empty reply, using baseline."
    if finish_reason:
        return (
            "Model returned no tool call "
            f"(finish_reason={finish_reason}), using baseline."
        )
    return "Model returned no tool call, using baseline."


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
    hosts = active_hosts()
    if hosts == ("web", "app", "db", "workstation"):
        pattern = _EVIDENCE_RE
    else:
        pattern = re.compile(r"\b(" + "|".join(
            re.escape(host) for host in hosts) + r"):\d+\b")
    ordered = []
    for hit in pattern.finditer(str(text or "")):
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
