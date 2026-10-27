"""Parse judge text into blue tool actions.

The deterministic parser runs fully offline: verbs and synonyms map to
exactly the blue tool surface (isolate, patch, restore, read logs,
report finding, status, stand down), and host names and roles resolve
against the exercise's asset inventory. Ambiguous matches return a
clarifying question, never a guess. Anything outside the tool surface
is refused with a plain explanation. No shell, no free-form commands.

An optional Muse Spark fallback handles free-form phrasing: it must
return strict JSON inside the same surface, and its result passes
through the same validator. The fallback is off unless requested, so
typed commands work with no network by default.
"""

from __future__ import annotations

import json
import re

ALLOWED_TOOLS = (
    "isolate_host",
    "patch_service",
    "restore_host",
    "read_logs",
    "report_finding",
    "list_processes",
    "wait",
)

EXAMPLES = (
    "isolate the database",
    "patch the web portal",
    "restore app",
    "read the last 100 lines on db",
    "report repeated sign-in failures on app",
    "status of the file share",
    "stand down",
)

# Verb patterns, first match wins. Word boundaries keep "log it" (a
# report) apart from "logs" (a log read).
_VERBS = (
    ("isolate_host", (
        r"\bisolate[sd]?\b", r"\bquarantin\w*\b", r"\bdisconnect\w*\b",
        r"\bdetach\w*\b", r"\bcut off\b", r"\btake\b.{0,20}\boffline\b",
        r"\bcontain\w*\b",
    )),
    ("patch_service", (
        r"\bpatch\w*\b", r"\bfix\b", r"\bfixes\b", r"\bfixed\b",
        r"\bremediat\w*\b", r"\brepair\w*\b", r"\bupdate\b",
    )),
    ("restore_host", (
        r"\brestor\w*\b", r"\breconnect\w*\b", r"\breattach\w*\b",
        r"\bbring\b.{0,20}\bback\b", r"\brecove\w*\b",
    )),
    ("report_finding", (
        r"\breport\w*\b", r"\bflag\b", r"\bflagged\b", r"\bflagging\b",
        r"\bnote\b", r"\bnoted\b", r"\brecord\b", r"\blog (it|this|that)\b",
    )),
    ("read_logs", (
        r"\blogs?\b", r"\binspect\w*\b", r"\bwhat happened\b",
        r"\bshow me\b",
    )),
    ("list_processes", (
        r"\bstatus\b", r"\bprocess\w*\b", r"\bps\b", r"\bhealth\w*\b",
        r"\brunning on\b",
    )),
    ("wait", (
        r"\bstand down\b", r"\bstanddown\b", r"\bhold\b", r"\bholding\b",
        r"\bwait\b", r"\bdo nothing\b", r"\bpause\b",
    )),
)

# Role words the judge may say, mapped to inventory roles.
_ROLE_WORDS = {
    "database": "database",
    "db": "database",
    "data host": "database",
    "web": "web portal",
    "portal": "web portal",
    "website": "web portal",
    "app": "app",
    "application": "app",
    "api": "api",
    "gateway": "api",
    "file": "file share",
    "files": "file share",
    "share": "file share",
    "backup": "backup",
    "vault": "backup",
    "workstation": "workstation",
    "desktop": "workstation",
    "laptop": "workstation",
    "jump": "jump host",
    "bastion": "jump host",
}

_FILLER = ("the", "a", "an", "host", "server", "machine", "please",
           "on", "of", "for", "my")

_JEWEL_WORDS = ("crown jewel", "jewel")

_HAND_BACK = ("hand back", "handback", "resume ai", "return control",
              "ai take over", "back to ai")


def is_hand_back(text: str) -> bool:
    """Check whether a transcript returns control to the AI."""
    lowered = f" {str(text or '').lower()} "
    return any(f" {phrase} " in lowered or lowered.strip() == phrase
               for phrase in _HAND_BACK)


def _detect_tool(text: str) -> str | None:
    for tool, patterns in _VERBS:
        for pattern in patterns:
            if re.search(pattern, text):
                return tool
    return None


def _inventory_hosts(inventory: dict) -> dict:
    hosts = (inventory or {}).get("hosts", {}) or {}
    return {str(name): entry for name, entry in hosts.items()}


def _resolve_host(text: str, inventory: dict) -> tuple:
    """Resolve a host mention to (status, host, question, options).

    Exact hostnames win over role words. Role words matching several
    hosts, or no mention at all, come back as "clarify" with options.
    """
    hosts = _inventory_hosts(inventory)
    names = sorted(hosts)
    for name in names:
        if re.search(r"\b" + re.escape(name.lower()) + r"\b", text):
            return ("ok", name, "", [])
    for jewel in _JEWEL_WORDS:
        if jewel in text:
            crown = (inventory or {}).get("crown_jewel_host")
            if crown and crown in hosts:
                return ("ok", crown, "", [])
    roles = {}
    for name, entry in hosts.items():
        role = str((entry or {}).get("role", "") or "").lower()
        roles.setdefault(role, []).append(name)
    matches: list = []
    for word in sorted(_ROLE_WORDS, key=len, reverse=True):
        if re.search(r"\b" + re.escape(word) + r"\b", text):
            for name in names:
                role = str((hosts[name] or {}).get("role", "") or "").lower()
                if role == _ROLE_WORDS[word] and name not in matches:
                    matches.append(name)
    if len(matches) == 1:
        return ("ok", matches[0], "", [])
    if len(matches) > 1:
        return ("clarify", "", f"Which host do you mean: "
                               f"{', '.join(sorted(matches))}?",
                sorted(matches))
    described = " ".join(word for word in text.split()
                         if word not in _FILLER)
    return ("clarify", "",
            f"Which host? I heard {described or 'no host name'}. "
            f"Known hosts: {', '.join(names) or 'none'}.", names)


def _log_count(text: str) -> int:
    match = re.search(r"\b(\d{1,3})\b.{0,12}\blines?\b", text)
    if match:
        return max(1, min(200, int(match.group(1))))
    match = re.search(r"\bn\s*=\s*(\d{1,3})\b", text)
    if match:
        return max(1, min(200, int(match.group(1))))
    return 50


def _finding_text(text: str) -> str:
    cleaned = re.sub(r"^(please\s+)?(report|flag|note|record|log)\b\s*",
                     "", text).strip()
    cleaned = re.sub(r"^(that|this|it|a|an|the)\b\s*", "", cleaned).strip()
    cleaned = re.sub(r"^(finding|issue|alert)\s*:\s*", "", cleaned).strip()
    return cleaned.lstrip(":,- ").strip()


def validate_action(candidate: dict, inventory: dict,
                    services: dict | None = None) -> dict:
    """Validate one parsed action against the tool surface.

    Both the deterministic parser and the model fallback return
    through here, so no path can emit a tool, host, or service the
    exercise does not have.
    """
    services = services or {}
    tool = (candidate or {}).get("tool", "")
    args = dict((candidate or {}).get("args", {}) or {})
    reasoning = str((candidate or {}).get("reasoning", "") or "").strip()
    if tool not in ALLOWED_TOOLS:
        return {"status": "refused",
                "reason": f"I can only run blue tool actions "
                          f"({', '.join(ALLOWED_TOOLS)}); "
                          f"{tool or 'nothing usable'} was parsed.",
                "examples": list(EXAMPLES)}
    hosts = _inventory_hosts(inventory)
    if tool in ("isolate_host", "patch_service", "restore_host",
                "read_logs", "list_processes"):
        host = str(args.get("host", "") or "")
        if host not in hosts:
            return {"status": "clarify",
                    "question": f"Which host? Known hosts: "
                                f"{', '.join(sorted(hosts)) or 'none'}.",
                    "options": sorted(hosts)}
        wanted_n = args.get("n", 50)
        args = {"host": host}
        if tool == "patch_service":
            # The service always comes from the scenario, never from the
            # order text, so no phrasing can aim a patch at a wrong name.
            service = services.get(host, "")
            if not service:
                return {"status": "clarify",
                        "question": f"Which service on {host}?",
                        "options": []}
            args["service"] = service
        if tool == "read_logs":
            try:
                args["n"] = max(1, min(200, int(wanted_n)))
            except (TypeError, ValueError):
                args["n"] = 50
    elif tool == "report_finding":
        text = str(args.get("text", "") or "").strip()
        if not text:
            return {"status": "clarify",
                    "question": "What should the finding say?",
                    "options": []}
        args = {"text": text}
    else:
        reason = str(args.get("reason", "") or "").strip()
        args = {"reason": reason or "holding for orders"}
    if not reasoning:
        reasoning = f"Judge order: run {tool}."
    return {"status": "ok", "tool": tool, "args": args,
            "reasoning": reasoning[:300]}


def parse(text: str, inventory: dict,
          services: dict | None = None) -> dict:
    """Parse judge text into a blue tool action, offline."""
    services = services or {}
    cleaned = str(text or "").lower()
    cleaned = re.sub(r"[^a-z0-9\s=/._-]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return {"status": "refused",
                "reason": "I heard nothing to act on.",
                "examples": list(EXAMPLES)}
    if re.search(r"\bstop\b.{0,12}\bexercise\b|\bend\b.{0,12}\bexercise\b",
                 cleaned):
        return {"status": "refused",
                "reason": "Ending the exercise is not a blue tool action; "
                          "use the dashboard stop control instead.",
                "examples": list(EXAMPLES)}
    destructive = re.search(r"\b(delet\w*|destroy\w*|wip\w*|eras\w*|"
                            r"format\w*|reboot\w*|shutdown\b|kill\b|"
                            r"drop\b|remove\b)", cleaned)
    if destructive:
        return {"status": "refused",
                "reason": f"'{destructive.group(0)}' is outside the blue "
                          "tool surface: blue can isolate, patch, restore, "
                          "read logs, report, check status, or stand down, "
                          "but never deletes or shuts anything down.",
                "examples": list(EXAMPLES)}
    tool = _detect_tool(cleaned)
    if tool is None:
        return {"status": "refused",
                "reason": "That is outside the blue tool surface. I can "
                          "isolate, patch, restore, read logs, report a "
                          "finding, check status, or stand down.",
                "examples": list(EXAMPLES)}
    if tool == "wait":
        return validate_action({"tool": "wait",
                                "args": {"reason": "judge ordered a hold"},
                                "reasoning": "Judge order: stand down."},
                               inventory, services)
    if tool == "report_finding":
        finding = _finding_text(cleaned)
        if not finding:
            return {"status": "clarify",
                    "question": "What should the finding say?",
                    "options": []}
        return validate_action(
            {"tool": tool, "args": {"text": finding},
             "reasoning": f"Judge order: report finding: {finding}."},
            inventory, services)
    status, host, question, options = _resolve_host(cleaned, inventory)
    if status != "ok":
        return {"status": status, "question": question, "options": options}
    args: dict = {"host": host}
    if tool == "read_logs":
        args["n"] = _log_count(cleaned)
    if tool == "patch_service":
        expected = services.get(host, "")
        if not expected:
            return {"status": "clarify",
                    "question": f"Which service on {host}?",
                    "options": []}
        args["service"] = expected
    return validate_action(
        {"tool": tool, "args": args,
         "reasoning": f"Judge order: run {tool} on {host}."},
        inventory, services)


_FALLBACK_SYSTEM = (
    "Translate one judge order into a blue tool action. Reply with one "
    "JSON object only, no other text. Either "
    '{"tool": "<name>", "args": {...}, "reasoning": "<short>"} '
    'with tool exactly one of isolate_host, patch_service, restore_host, '
    "read_logs, report_finding, list_processes, wait; or "
    '{"clarify": "<question>"} when the order is ambiguous; or '
    '{"refuse": "<reason>"} when it is outside that tool list. '
    "Hosts must come from the allowed list. Never invent a host, "
    "service, or tool."
)


def parse_with_model(text: str, inventory: dict,
                     services: dict | None = None, *,
                     exercise_id: str | None = None,
                     client=None, use_cache: bool = False) -> dict:
    """Parse free-form phrasing with Muse Spark, then validate.

    Needs network; the deterministic parser stays the default. Any
    model failure or schema violation becomes a plain refusal, and a
    valid reply still passes through validate_action untouched.
    """
    from crucible import llm

    services = services or {}
    hosts = _inventory_hosts(inventory)
    allowed = sorted(hosts)
    messages = [
        {"role": "system", "content": _FALLBACK_SYSTEM},
        {"role": "user", "content":
         f"Allowed hosts: {', '.join(allowed) or 'none'}. "
         f"Order: {str(text or '').strip()}"},
    ]
    try:
        response = llm.chat("dev", messages, exercise_id=exercise_id,
                            client=client, use_cache=use_cache,
                            temperature=0.0, max_tokens=512)
        payload = json.loads(str(response.get("content", "") or ""))
    except Exception:
        return {"status": "refused",
                "reason": "The phrasing model did not return usable JSON; "
                          "try one of the example orders.",
                "examples": list(EXAMPLES)}
    if not isinstance(payload, dict):
        return {"status": "refused",
                "reason": "The phrasing model did not return usable JSON.",
                "examples": list(EXAMPLES)}
    if isinstance(payload.get("clarify"), str):
        return {"status": "clarify",
                "question": payload["clarify"][:300], "options": allowed}
    if isinstance(payload.get("refuse"), str):
        return {"status": "refused",
                "reason": payload["refuse"][:300],
                "examples": list(EXAMPLES)}
    return validate_action(payload, inventory, services)
