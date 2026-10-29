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
    "scan_host",
    "wait",
)

EXAMPLES = (
    "isolate the database",
    "patch the web portal",
    "restore app",
    "read the last 100 lines on db",
    "report repeated sign-in failures on app",
    "status of the file share",
    "scan the app host",
    "stand down",
)

# Verb patterns, first match wins. Word boundaries keep "log it" (a
# report) apart from "logs" (a log read). Transcription variants
# (blogs, lugs, i solate, skin) ride along only where they cannot
# steal another tool: "block" needs a read next to it so firewall
# orders keep refusing, and "skin" matches nothing else we parse.
_VERBS = (
    ("isolate_host", (
        r"\bisolate[sd]?\b", r"\bi\s+solat\w*\b", r"\bquarantin\w*\b",
        r"\bdisconnect\w*\b", r"\bdetach\w*\b", r"\bcut off\b",
        r"\btake\b.{0,20}\boffline\b", r"\bcontain\w*\b",
    )),
    ("patch_service", (
        r"\bpatch\w*\b", r"\bfix\b", r"\bfixes\b", r"\bfixed\b",
        r"\bremediat\w*\b", r"\brepair\w*\b", r"\bupdate\b",
        # Sign-in rotation ships with the patch action, so a rotate
        # order runs a patch and says so in its reasoning.
        r"\brotat\w*\b",
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
        r"\blogs?\b", r"\bblogs?\b", r"\blugs?\b",
        r"\bread\b.{0,20}\bblocks?\b", r"\blook\b.{0,12}\bat\b",
        r"\bview\b", r"\binspect\w*\b", r"\bwhat happened\b",
        r"\bshow me\b",
    )),
    ("list_processes", (
        r"\bstatus\b", r"\bprocess\w*\b", r"\bps\b", r"\bhealth\w*\b",
        r"\brunning on\b",
    )),
    ("scan_host", (
        r"\bscan\w*\b", r"\brescan\b", r"\bsweep\b", r"\bskin\b",
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
    "data store": "database",
    "web": "web portal",
    "portal": "web portal",
    "website": "web portal",
    "public site": "web portal",
    "app": "app",
    "application": "app",
    "reports server": "app",
    "reporting service": "app",
    "api": "api",
    "gateway": "api",
    "file": "file share",
    "files": "file share",
    "share": "file share",
    "file share": "file share",
    "backup": "backup",
    "vault": "backup",
    "workstation": "workstation",
    "desktop": "workstation",
    "laptop": "workstation",
    "jump": "jump host",
    "jump host": "jump host",
    "bastion": "jump host",
}

# The live classic range phrases roles as sentences while generated
# ranges use short names; both canonicalize before comparing so one
# set of role words works in every exercise.
_CANONICAL_ROLES = {
    "public web front end": "web portal",
    "internal reporting service": "app",
    "internal data store": "database",
    "analyst workstation": "workstation",
}


def _canon_role(role: str) -> str:
    lowered = str(role or "").strip().lower()
    return _CANONICAL_ROLES.get(lowered, lowered)


_FILLER = ("the", "a", "an", "host", "server", "machine", "please",
           "on", "of", "for", "my", "now")

# Words the fuzzy host matcher never considers: verbs, fillers,
# role words, and control phrases. Without this a verb like "read"
# could match a host named "reed".
_NO_FUZZY = frozenset("""
the a an host server machine please on of for my now
read logs log blogs blog lugs lug block blocks check look looks
view views show inspect inspection what happened happened me
isolate isolates isolated solate quarantine disconnect detach
contain offline patch fix fixes fixed remediate repair update
updates rotate rotation restore reconnect reattach bring back
recover report reports flag flagged flagging note noted record
status processes process health healthy running run
scan scans scanned rescan sweep skin stand down standdown
hold holding wait waits pause nothing last lines line
crown jewel hand back resume control take over manual my turn
database db data web portal website site public app application
reports reporting service api gateway file files share backup
vault workstation desktop laptop jump bastion
""".split())

_JEWEL_WORDS = ("crown jewel", "jewel")

_HAND_BACK = ("hand back", "handback", "resume ai", "return control",
              "ai take over", "back to ai")

_TAKE_OVER = ("take over", "takeover", "take control", "manual control",
              "pause ai", "pause the ai", "my turn")


def is_hand_back(text: str) -> bool:
    """Check whether a transcript returns control to the AI."""
    lowered = f" {str(text or '').lower()} "
    return any(f" {phrase} " in lowered or lowered.strip() == phrase
               for phrase in _HAND_BACK)


def is_take_over(text: str) -> bool:
    """Check whether a transcript takes control from the AI."""
    lowered = f" {str(text or '').lower()} "
    return any(f" {phrase} " in lowered or lowered.strip() == phrase
               for phrase in _TAKE_OVER)


# Role phrases that contain a report verb word. The tool detector
# blanks them before the report check so "read logs on the reports
# server" reads logs instead of filing a finding; genuine "report X"
# orders never spell the full role phrase.
_REPORT_ROLE_PHRASES = ("reports server", "reporting service")


def _detect_tool(text: str) -> str | None:
    masked = text
    for phrase in _REPORT_ROLE_PHRASES:
        if phrase in masked:
            masked = masked.replace(phrase, " ")
    for tool, patterns in _VERBS:
        probe = masked if tool == "report_finding" else text
        for pattern in patterns:
            if re.search(pattern, probe):
                return tool
    return None


def _inventory_hosts(inventory: dict) -> dict:
    hosts = (inventory or {}).get("hosts", {}) or {}
    return {str(name): entry for name, entry in hosts.items()}


def example_orders(inventory: dict) -> list:
    """Build three grounded example orders from live host names.

    Names come from the current inventory only, cycling when fewer
    than three hosts exist; an empty inventory yields no examples
    rather than invented hosts.
    """
    names = sorted(_inventory_hosts(inventory))
    if not names:
        return []
    picks = [names[index % len(names)] for index in range(3)]
    return [f"read logs on {picks[0]}",
            f"scan the {picks[1]} host",
            f"isolate the {picks[2]} host"]


def guidance_line(inventory: dict) -> str:
    """Render the known-hosts plus examples hint, or "" when none."""
    names = sorted(_inventory_hosts(inventory))
    if not names:
        return ""
    tries = ", ".join(f"'{order}'" for order in example_orders(inventory))
    return f"Known hosts: {', '.join(names)}. Try: {tries}."


def _edit_distance(left: str, right: str) -> int:
    """Small Levenshtein distance for short host-name matching."""
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    prev = list(range(len(right) + 1))
    for i, char_left in enumerate(left, 1):
        curr = [i]
        for j, char_right in enumerate(right, 1):
            cost = 0 if char_left == char_right else 1
            curr.append(min(prev[j] + 1, curr[-1] + 1, prev[j - 1] + cost))
        prev = curr
    return prev[-1]


def _phonetic_key(word: str) -> str:
    """Collapse a word to a consonant skeleton for sound-alike match.

    Keeps the first letter (with c/k/q merged, the common "kwil"
    for "quill" slip), drops vowels and the glide w, and squeezes
    double letters.
    """
    cleaned = re.sub(r"[^a-z]", "", str(word or "").lower())
    if not cleaned:
        return ""
    first = {"c": "k", "k": "k", "q": "k"}.get(cleaned[0], cleaned[0])
    rest = re.sub(r"[aeiouyw]", "", cleaned[1:])
    rest = re.sub(r"(.)\1+", r"\1", rest)
    return first + rest


def _fuzzy_host(text: str, hosts: dict) -> tuple | None:
    """Match a misheard host word against current hosts, if unique.

    Returns an _resolve_host-style 5-tuple, or None when nothing is
    close. Candidates score by edit distance with a phonetic-key
    assist; only a unique best host wins, ties ask with candidates.
    """
    names = sorted(hosts)
    if not names:
        return None
    words = [word for word in text.split()
             if word not in _NO_FUZZY and len(word) >= 3 and word.isalpha()]
    if not words:
        return None
    scored = []
    for word in words:
        spoken_key = _phonetic_key(word)
        for name in names:
            lowered = name.lower()
            dist = _edit_distance(word, lowered)
            if len(word) >= 4 and spoken_key \
                    and spoken_key == _phonetic_key(lowered):
                dist = min(dist, 1)
            if dist <= max(1, len(lowered) // 4):
                scored.append((dist, name, word))
    if not scored:
        return None
    best = min(dist for dist, _, _ in scored)
    winners = sorted({name for dist, name, _ in scored if dist == best})
    if len(winners) == 1:
        host = winners[0]
        word = sorted(word for dist, name, word in scored
                      if dist == best and name == host)[0]
        return ("ok", host, "", [], f"Heard '{word}', using {host}.")
    word = sorted(word for dist, _, word in scored if dist == best)[0]
    return ("clarify", "",
            f"Which host? I heard '{word}', close to "
            f"{', '.join(winners)}. "
            f"{guidance_line({'hosts': hosts})}".rstrip(),
            winners, "")


def _resolve_host(text: str, inventory: dict) -> tuple:
    """Resolve a host mention to (status, host, question, options, heard).

    Exact hostnames win over role words, role words win over fuzzy
    sound-alikes. Role words matching several hosts, several close
    fuzzy matches, or no mention at all come back as "clarify" with
    options. heard names a fuzzy match for the judge to confirm.
    """
    hosts = _inventory_hosts(inventory)
    names = sorted(hosts)
    for name in names:
        if re.search(r"\b" + re.escape(name.lower()) + r"\b", text):
            return ("ok", name, "", [], "")
    for jewel in _JEWEL_WORDS:
        if jewel in text:
            crown = (inventory or {}).get("crown_jewel_host")
            if crown and crown in hosts:
                return ("ok", crown, "", [], "")
    matches: list = []
    for word in sorted(_ROLE_WORDS, key=len, reverse=True):
        if re.search(r"\b" + re.escape(word) + r"\b", text):
            for name in names:
                role = _canon_role((hosts[name] or {}).get("role", ""))
                if role == _ROLE_WORDS[word] and name not in matches:
                    matches.append(name)
    if len(matches) == 1:
        return ("ok", matches[0], "", [], "")
    if len(matches) > 1:
        return ("clarify", "",
                f"Which host do you mean: {', '.join(sorted(matches))}?",
                sorted(matches), "")
    fuzzy = _fuzzy_host(text, hosts)
    if fuzzy is not None:
        return fuzzy
    described = " ".join(word for word in text.split()
                         if word not in _FILLER)
    return ("clarify", "",
            f"Which host? I heard {described or 'no host name'}. "
            f"{guidance_line(inventory)}".rstrip(), names, "")


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
    heard = str((candidate or {}).get("heard", "") or "").strip()
    if tool not in ALLOWED_TOOLS:
        return {"status": "refused",
                "reason": f"I can only run blue tool actions "
                          f"({', '.join(ALLOWED_TOOLS)}); "
                          f"{tool or 'nothing usable'} was parsed.",
                "examples": list(EXAMPLES)}
    hosts = _inventory_hosts(inventory)
    if tool in ("isolate_host", "patch_service", "restore_host",
                "read_logs", "list_processes", "scan_host"):
        host = str(args.get("host", "") or "")
        if host not in hosts:
            return {"status": "clarify",
                    "question": f"Which host? "
                                f"{guidance_line(inventory)}".rstrip(),
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
    result = {"status": "ok", "tool": tool, "args": args,
              "reasoning": reasoning[:300]}
    if heard:
        result["heard"] = heard[:200]
    return result


def parse(text: str, inventory: dict,
          services: dict | None = None) -> dict:
    """Parse judge text into a blue tool action, offline."""
    services = services or {}
    cleaned = str(text or "").lower()
    cleaned = re.sub(r"[^a-z0-9\s=/._-]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return {"status": "refused",
                "reason": f"I heard nothing to act on. "
                          f"{guidance_line(inventory)}".rstrip(),
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
                          "finding, check status, or stand down. "
                          f"{guidance_line(inventory)}".rstrip(),
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
    status, host, question, options, heard = _resolve_host(cleaned,
                                                            inventory)
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
    reasoning = f"Judge order: run {tool} on {host}."
    if tool == "patch_service" and re.search(r"\brotat\w*\b", cleaned):
        reasoning = (f"Judge order: rotate sign-in on {host}; "
                     f"rotation ships with the patch action.")
    candidate: dict = {"tool": tool, "args": args, "reasoning": reasoning}
    if heard:
        candidate["heard"] = heard
    return validate_action(candidate, inventory, services)


_FALLBACK_SYSTEM = (
    "Translate one judge order into a blue tool action. Reply with one "
    "JSON object only, no other text. Either "
    '{"tool": "<name>", "args": {...}, "reasoning": "<short>"} '
    'with tool exactly one of isolate_host, patch_service, restore_host, '
    "read_logs, report_finding, list_processes, scan_host, wait; or "
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
                "reason": "The phrasing model did not return usable JSON. "
                          f"{guidance_line(inventory)}".rstrip(),
                "examples": list(EXAMPLES),
                "cost_usd": 0.0}
    usage = (response or {}).get("usage", {}) or {}
    try:
        cost = llm.estimate_cost(
            str((response or {}).get("model", "") or "dev"),
            int(usage.get("input_tokens", 0) or 0),
            int(usage.get("output_tokens", 0) or 0))
    except (TypeError, ValueError):
        cost = 0.0
    if (response or {}).get("cached"):
        cost = 0.0
    if not isinstance(payload, dict):
        return {"status": "refused",
                "reason": "The phrasing model did not return usable JSON. "
                          f"{guidance_line(inventory)}".rstrip(),
                "examples": list(EXAMPLES),
                "cost_usd": cost}
    if isinstance(payload.get("clarify"), str):
        return {"status": "clarify",
                "question": payload["clarify"][:300], "options": allowed,
                "cost_usd": cost}
    if isinstance(payload.get("refuse"), str):
        return {"status": "refused",
                "reason": payload["refuse"][:300],
                "examples": list(EXAMPLES),
                "cost_usd": cost}
    result = validate_action(payload, inventory, services)
    result["cost_usd"] = cost
    return result
