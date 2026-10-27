"""Post-exercise referee that keeps blue's playbook.

After each exercise the referee reads the full event log, the outcome,
and the score, then rewrites blue's playbook: at most ten short general
tactical rules, each tagged with the episode it came from. It merges
and replaces weaker rules instead of only appending. Rules must be
defensive tactics (what evidence to look for, when to contain, when to
restore) and must never carry credentials, exact file names, or exact
answers; a deterministic scrub drops any rule that does.

The live playbook lives at playbooks/blue.json (plus a readable
playbooks/blue.md); campaigns keep their own copies under
runs/campaign-<name>/.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from crucible import llm

ACTOR = "referee"
ROLE = "referee"
PRIMARY_MODEL = "muse-wonder-1.0"
PRIMARY_EFFORT = "medium"
FALLBACK_MODEL = "muse-spark-1.3"
FALLBACK_EFFORT = "high"
FALLBACK_NO_MODEL = "none"
MAX_TOKENS = 1536
TEMPERATURE = 0.0
MAX_RULES = 10
RULE_LIMIT = 280

ROOT = Path(__file__).resolve().parent.parent.parent
PLAYBOOK_JSON = ROOT / "playbooks" / "blue.json"
PLAYBOOK_MD = ROOT / "playbooks" / "blue.md"

SYSTEM_PROMPT = (
    "You coach the blue team after a defensive validation exercise. "
    "Read the event log, the outcome, and the score, then return the "
    "FULL updated playbook as one JSON object on one line, no preamble, "
    "no reasoning text, no markdown fences: "
    '{"rules": [{"rule": "...", "episode": "..."}]}. '
    "At most ten rules. Merge and replace weaker rules instead of only "
    "appending: keep rules that still hold with their original episode "
    "id, strengthen vague ones, drop or replace contradicted ones, and "
    "add at most three new ones from this episode. Phrase every rule as "
    "a defensive tactic: what evidence to look for, when to contain, "
    "when to restore. Never include credentials, passwords, user names, "
    "exact file names, exact file paths, or exact answers. Each rule is "
    "one short sentence under 280 characters."
)


def _active_spec_dict() -> dict:
    """Return the active generated spec, or {} on classic / failure."""
    try:
        from crucible import sandbox

        return sandbox.active_spec() or {}
    except Exception:
        return {}


def _scenario_secrets() -> set:
    """Collect password and path strings that rules must avoid.

    Substring tier: these never appear in legitimate tactic prose, so
    any mention drops the rule. Covers the classic scenario and, when
    a generated spec is active, every step, dead end, and decoy page.
    """
    try:
        from crucible import sandbox

        scenario = sandbox.load_scenario()
    except Exception:
        scenario = {}
    denied = set()
    shipped = scenario.get("shipped_defaults", {}) or {}
    if isinstance(shipped, dict):
        for entry in shipped.values():
            if not isinstance(entry, dict):
                continue
            password = entry.get("pass")
            if isinstance(password, str) and password.strip():
                denied.add(password.strip())
    for key in ("secret", "app_config"):
        block = scenario.get(key, {}) or {}
        path = block.get("path") if isinstance(block, dict) else None
        if isinstance(path, str) and path.strip():
            denied.add(path.strip())
            denied.add(path.strip().rsplit("/", 1)[-1])
    admin = scenario.get("admin_endpoint", {}) or {}
    admin_path = admin.get("path") if isinstance(admin, dict) else None
    if isinstance(admin_path, str) and admin_path.strip():
        denied.add(admin_path.strip())
    spec = _active_spec_dict()
    for group in ("path", "dead_ends"):
        steps = spec.get(group, []) or []
        for step in steps:
            if not isinstance(step, dict):
                continue
            for key in ("password", "reveals_password"):
                value = step.get(key)
                if isinstance(value, str) and value.strip():
                    denied.add(value.strip())
            for key in ("path", "page_path", "dir"):
                value = step.get(key)
                if isinstance(value, str) and value.strip():
                    cleaned = value.strip()
                    denied.add(cleaned)
                    denied.add(cleaned.rsplit("/", 1)[-1])
    jewel = spec.get("jewel", {}) or {}
    jewel_path = jewel.get("path") if isinstance(jewel, dict) else None
    if isinstance(jewel_path, str) and jewel_path.strip():
        denied.add(jewel_path.strip())
        denied.add(jewel_path.strip().rsplit("/", 1)[-1])
    decoys = spec.get("decoys", {}) or {}
    for group in ("admin_pages", "status_pages"):
        for page in decoys.get(group, []) or []:
            if not isinstance(page, dict):
                continue
            value = page.get("path")
            if isinstance(value, str) and value.strip():
                denied.add(value.strip())
    return {item for item in denied if item}


def _scenario_names() -> set:
    """Collect hostname and account strings that rules must avoid.

    Whole-word tier: matched with word boundaries so generic tactic
    words keep working ("monitoring" must survive an account named
    "monitor"). Covers the classic scenario and the active spec.
    """
    try:
        from crucible import sandbox

        scenario = sandbox.load_scenario()
    except Exception:
        scenario = {}
    names = set()
    for host in scenario.get("hosts", []) or []:
        if isinstance(host, str) and host.strip():
            names.add(host.strip())
    shipped = scenario.get("shipped_defaults", {}) or {}
    if isinstance(shipped, dict):
        for entry in shipped.values():
            if not isinstance(entry, dict):
                continue
            user = entry.get("user")
            if isinstance(user, str) and user.strip():
                names.add(user.strip())
            for extra in entry.get("users", []) or []:
                if isinstance(extra, str) and extra.strip():
                    names.add(extra.strip())
    spec = _active_spec_dict()
    for host in spec.get("hosts", []) or []:
        if not isinstance(host, dict):
            continue
        name = host.get("name")
        if isinstance(name, str) and name.strip():
            names.add(name.strip())
    for group in ("path", "dead_ends"):
        steps = spec.get(group, []) or []
        for step in steps:
            if not isinstance(step, dict):
                continue
            for key in ("host", "account", "leaks_host", "leaks_account",
                        "reveals_host", "reveals_account", "terminal_host",
                        "terminal_account"):
                value = step.get(key)
                if isinstance(value, str) and value.strip():
                    names.add(value.strip())
    return {item for item in names if item}


def rule_is_clean(rule: str, denied: set | None = None,
                  names: set | None = None) -> bool:
    """Check that a rule carries no credential, path, or scenario name."""
    text = str(rule or "").lower()
    if not text.strip():
        return False
    banned = denied if denied is not None else _scenario_secrets()
    if any(secret.lower() in text for secret in banned if secret):
        return False
    words = names if names is not None else _scenario_names()
    return not any(re.search(r"\b" + re.escape(name.lower()) + r"\b", text)
                   for name in words if name)


def scrub_rules(rules: list, episode: str,
                denied: set | None = None,
                names: set | None = None) -> tuple:
    """Clean model-proposed rules into playbook shape.

    Drops empty rules, rules over the length cap, and rules naming
    credentials, paths, or scenario hosts and accounts. Missing
    episode ids default to the current episode. Returns
    (kept, dropped_count).
    """
    banned = denied if denied is not None else _scenario_secrets()
    words = names if names is not None else _scenario_names()
    kept = []
    dropped = 0
    for entry in rules or []:
        if not isinstance(entry, dict):
            dropped += 1
            continue
        text = " ".join(str(entry.get("rule", "") or "").split())
        if not text or len(text) > RULE_LIMIT:
            dropped += 1
            continue
        if not rule_is_clean(text, banned, words):
            dropped += 1
            continue
        tag = str(entry.get("episode", "") or "").strip() or episode
        kept.append({"rule": text, "episode": tag[:64]})
    return kept, dropped


def empty_playbook() -> dict:
    """Return a fresh playbook with no rules."""
    return {"episode": None, "rules": [], "model_used": None}


def load_playbook(path: str | Path | None = None) -> dict:
    """Load a playbook file, returning an empty one when missing."""
    target = Path(path) if path else PLAYBOOK_JSON
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty_playbook()
    rules = []
    if isinstance(data, dict):
        for entry in data.get("rules", []) or []:
            if isinstance(entry, dict) and str(entry.get("rule", "")).strip():
                rules.append({"rule": str(entry["rule"])[:RULE_LIMIT],
                              "episode": str(entry.get("episode", "") or "")[:64]})
    return {"episode": (data.get("episode") if isinstance(data, dict) else None),
            "rules": rules[:MAX_RULES],
            "model_used": (data.get("model_used") if isinstance(data, dict) else None)}


def render_markdown(playbook: dict) -> str:
    """Render a playbook as a readable markdown page."""
    lines = ["# Blue playbook", ""]
    rules = (playbook or {}).get("rules", []) or []
    if not rules:
        lines.append("No lessons yet.")
        return "\n".join(lines) + "\n"
    for order, entry in enumerate(rules, start=1):
        lines.append(f"{order}. {entry.get('rule', '')} (from {entry.get('episode', '?')})")
    return "\n".join(lines) + "\n"


def save_playbook(playbook: dict, json_path: str | Path | None = None,
                  md_path: str | Path | None = None) -> dict:
    """Write a playbook as json plus a readable markdown page."""
    payload = {"episode": playbook.get("episode"),
               "rules": list(playbook.get("rules", []) or [])[:MAX_RULES],
               "model_used": playbook.get("model_used")}
    json_target = Path(json_path) if json_path else PLAYBOOK_JSON
    md_target = Path(md_path) if md_path else PLAYBOOK_MD
    json_target.parent.mkdir(parents=True, exist_ok=True)
    md_target.parent.mkdir(parents=True, exist_ok=True)
    json_target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    md_target.write_text(render_markdown(payload), encoding="utf-8")
    return payload


def _event_line(event: dict) -> str:
    """Render one event as a compact single line for the referee."""
    args = event.get("args", {}) or {}
    result = event.get("result", {}) or {}
    try:
        args_text = json.dumps(args, sort_keys=True)[:140]
    except (TypeError, ValueError):
        args_text = str(args)[:140]
    if event.get("tool") == "read_logs" and result.get("ok") is True:
        lines = result.get("lines", []) or []
        result_text = f"ok lines={len(lines)}"
    else:
        try:
            result_text = json.dumps(result, sort_keys=True)[:140]
        except (TypeError, ValueError):
            result_text = str(result)[:140]
    note = str(event.get("reasoning_summary", ""))[:100]
    return (f"{event.get('id', '?')} {event.get('actor', '?')} "
            f"{event.get('tool', '?')} {args_text} -> {result_text} note={note}")


def build_messages(events: list, score: dict, exercise_id: str,
                   playbook: dict) -> list:
    """Build the referee prompt from the full log, score, and playbook."""
    log_text = "\n".join(_event_line(event) for event in events or []) or "No events."
    try:
        score_text = json.dumps(score or {}, sort_keys=True)[:800]
    except (TypeError, ValueError):
        score_text = str(score or "")[:800]
    rules = (playbook or {}).get("rules", []) or []
    if rules:
        book_text = "\n".join(f"{order}. [{entry.get('episode', '?')}] "
                              f"{entry.get('rule', '')}"
                              for order, entry in enumerate(rules, start=1))
    else:
        book_text = "Empty: this is the first episode."
    user_text = (
        f"Episode {exercise_id} is over. Outcome: {(score or {}).get('outcome', '?')}.\n"
        f"Score: {score_text}\n"
        f"Full event log:\n{log_text}\n"
        f"Current playbook ({len(rules)}/10 rules):\n{book_text}\n"
        "Return the full updated playbook as one JSON object on one line."
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_text},
    ]


def parse_playbook(content: str) -> list | None:
    """Parse a model reply into a rule list, or None when unusable."""
    text = str(content or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(text[start:end + 1])
        except ValueError:
            return None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("rules"), list):
        return None
    return parsed["rules"]


def _model_unavailable(exc: BaseException) -> bool:
    """Check whether an error means the model id is not served."""
    if getattr(exc, "status_code", None) == 404:
        return True
    response = getattr(exc, "response", None)
    if getattr(response, "status_code", None) == 404:
        return True
    text = str(exc).lower()
    if "model" not in text:
        return False
    return any(marker in text for marker in (
        "unavailable", "not found", "does not exist",
        "unknown model", "unsupported model", "no such model",
    ))


def _call(role: str, messages: list, exercise_id: str | None, client,
          use_cache: bool, effort: str) -> dict:
    return llm.chat(
        role, messages, exercise_id=exercise_id, client=client,
        use_cache=use_cache, max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE, reasoning_effort=effort,
    )


def review_episode(events: list, score: dict, exercise_id: str,
                   playbook: dict | None = None, *, client=None,
                   use_cache: bool = True) -> dict:
    """Update the playbook from one finished episode.

    Returns the new playbook plus model_used, dropped count, and
    estimated cost. Model failures keep the current playbook unchanged
    at zero cost; the caller still learns which model was used.
    """
    current = playbook if playbook is not None else empty_playbook()
    messages = build_messages(events, score, exercise_id, current)
    try:
        response = _call(ROLE, messages, exercise_id, client, use_cache,
                         PRIMARY_EFFORT)
    except Exception as exc:  # noqa: BLE001 - fallback below by cause
        if not _model_unavailable(exc):
            return {"playbook": current, "model_used": FALLBACK_NO_MODEL,
                    "dropped": 0, "estimated_cost_usd": 0.0,
                    "updated": datetime.now(timezone.utc).isoformat()}
        try:
            response = _call(FALLBACK_MODEL, messages, exercise_id, client,
                             use_cache, FALLBACK_EFFORT)
        except Exception:
            return {"playbook": current, "model_used": FALLBACK_NO_MODEL,
                    "dropped": 0, "estimated_cost_usd": 0.0,
                    "updated": datetime.now(timezone.utc).isoformat()}
    proposed = parse_playbook((response or {}).get("content", ""))
    model_used = str((response or {}).get("model", "") or PRIMARY_MODEL)
    if proposed is None:
        return {"playbook": current, "model_used": model_used,
                "dropped": 0, "estimated_cost_usd": 0.0,
                "updated": datetime.now(timezone.utc).isoformat()}
    kept, dropped = scrub_rules(proposed, exercise_id)
    new_book = {"episode": exercise_id, "rules": kept[:MAX_RULES],
                "model_used": model_used}
    usage = (response or {}).get("usage", {}) or {}
    try:
        cost = llm.estimate_cost(
            model_used, int(usage.get("input_tokens", 0) or 0),
            int(usage.get("output_tokens", 0) or 0))
    except (TypeError, ValueError):
        cost = 0.0
    if (response or {}).get("cached"):
        cost = 0.0
    return {"playbook": new_book, "model_used": model_used,
            "dropped": dropped, "estimated_cost_usd": round(cost, 6),
            "updated": datetime.now(timezone.utc).isoformat()}
