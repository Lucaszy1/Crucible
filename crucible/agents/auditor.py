"""Third agent that judges blue team actions after they happen.

Two layers. Layer 1 is deterministic code with no model: for each blue
action it checks that every cited evidence_ref existed in the logs blue
had already read, that each ref comes from the host the action concerns
(or a host linked to the claim), and whether those logs showed any
suspicious activity. Layer 2 asks muse-wonder-1.0 for one verdict, a
short rationale, and a 0 to 10 score, given only what blue knew then.

The auditor never sees red's reasoning. Its prompt carries blue's own
prior tool results, the cited log lines, the action, the host weight,
and current downtime, nothing from red's side.
"""

from __future__ import annotations

import json
import re

from crucible import llm

ACTOR = "auditor"
ROLE = "auditor"
PRIMARY_MODEL = "muse-wonder-1.0"
PRIMARY_EFFORT = "medium"
FALLBACK_MODEL = "muse-spark-1.3"
FALLBACK_EFFORT = "high"
FALLBACK_NO_MODEL = "none"
MAX_TOKENS = 512
TEMPERATURE = 0.0

VERDICTS = ("justified", "premature", "unsupported", "late", "disproportionate")

AUDIT_MODES = ("off", "end", "live")

# Blue tools too broken to judge; everything else gets an audit event.
SKIP_TOOLS = ("agent_error",)

ALLOWED_HOSTS = ("web", "app", "db", "workstation")

DEFAULT_BUSINESS_WEIGHTS = {"web": 3, "app": 3, "db": 5, "workstation": 1}

# Substrings that mark a log line as suspicious. These name observable
# log output from the sandbox scripts (sign-in attempts, file and
# directory reads, admin page probes), never red's hidden reasoning.
SUSPICIOUS_MARKERS = (
    "signin ok",
    "signin fail",
    "file read",
    "dir list",
    "/admin",
)

_HOST_RE = re.compile(r"\b(web|app|db|workstation)\b")

SYSTEM_PROMPT = (
    "You judge one blue team defensive action from a validation exercise. "
    "You see only what blue knew at that moment: its prior tool results, "
    "the log lines it cited, the action, the host weight, and current "
    "downtime. Reply with exactly one JSON object and nothing else: "
    '{"verdict": "...", "score": 0-10, "rationale": "..."}. '
    "Verdict must be one of justified, premature, unsupported, late, "
    "disproportionate. justified means the action fits the evidence seen "
    "so far. premature means the right direction but the evidence was too "
    "thin to act yet. unsupported means no visible evidence backs this "
    "action. late means correct but it should have happened earlier given "
    "what was already visible. disproportionate means the response was "
    "heavier than the evidence warranted, such as isolating a costly host "
    "on a weak signal. Rationale is one or two sentences in plain language."
)


def visible_logs(prior_events: list) -> dict:
    """Map each log line id blue had read to its text.

    Only blue's own successful read_logs results count, in order, so the
    map holds exactly the logs available to blue at that moment. Newer
    text never overwrites: ids are stable per host.
    """
    visible: dict = {}
    for event in prior_events or []:
        if event.get("actor") != "blue" or event.get("tool") != "read_logs":
            continue
        result = event.get("result", {}) or {}
        if result.get("ok") is not True:
            continue
        for row in result.get("lines", []) or []:
            ref = (row or {}).get("id")
            if ref and ref not in visible:
                visible[ref] = str((row or {}).get("line", ""))
    return visible


def ref_first_seen(prior_events: list) -> dict:
    """Map each log line id to the event id where blue first saw it."""
    seen: dict = {}
    for event in prior_events or []:
        if event.get("actor") != "blue" or event.get("tool") != "read_logs":
            continue
        result = event.get("result", {}) or {}
        if result.get("ok") is not True:
            continue
        for row in result.get("lines", []) or []:
            ref = (row or {}).get("id")
            if ref and ref not in seen:
                seen[ref] = event.get("id", "")
    return seen


def suspicious_in_logs(visible: dict) -> list:
    """Return [(ref, line)] for visible lines matching suspicion markers."""
    hits = []
    for ref, line in (visible or {}).items():
        lowered = str(line or "").lower()
        if any(marker in lowered for marker in SUSPICIOUS_MARKERS):
            hits.append((ref, str(line or "")))
    return hits


def action_host(blue_event: dict) -> str | None:
    """Return the host a blue action concerns, if it names one."""
    args = (blue_event or {}).get("args", {}) or {}
    host = args.get("host")
    return host if host in ALLOWED_HOSTS else None


def hosts_named_in(*texts: str) -> set:
    """Find sandbox host names mentioned in free text."""
    found = set()
    for text in texts:
        found.update(_HOST_RE.findall(str(text or "")))
    return found


def claim_text(blue_event: dict) -> str:
    """Return the human claim behind a blue action (finding text, reason)."""
    args = (blue_event or {}).get("args", {}) or {}
    parts = [str(args.get("text", "") or ""),
             str((blue_event or {}).get("reasoning_summary", "") or "")]
    return " ".join(part for part in parts if part)


def check_evidence(blue_event: dict, prior_events: list) -> dict:
    """Layer 1: deterministic evidence checks with no model.

    Every cited ref must exist in the logs blue had read, and each ref
    must come from the host the action concerns or a host linked to the
    claim. Also reports whether those visible logs held any suspicious
    activity, judged from red's already-visible log output only.
    """
    refs = list((blue_event or {}).get("evidence_refs", []) or [])
    visible = visible_logs(prior_events)
    linked = hosts_named_in(claim_text(blue_event))
    host = action_host(blue_event)
    if host:
        linked.add(host)
    invalid = []
    for ref in refs:
        if ref not in visible:
            invalid.append(ref)
            continue
        ref_host = str(ref).split(":", 1)[0]
        if linked and ref_host not in linked:
            invalid.append(ref)
    suspicious = suspicious_in_logs(visible)
    return {
        "evidence_checked": refs,
        "refs_valid": not invalid,
        "invalid_refs": invalid,
        "suspicious_visible": bool(suspicious),
        "suspicious_lines": [{"id": ref, "line": line} for ref, line in suspicious],
    }


def _down_hosts(prior_events: list) -> set:
    """Return hosts blue left down before this action (isolate or patch)."""
    down: set = set()
    for event in prior_events or []:
        result = event.get("result", {}) or {}
        if result.get("ok") is not True:
            continue
        tool = event.get("tool", "")
        args = event.get("args", {}) or {}
        host = args.get("host") or result.get("host")
        if not host:
            continue
        if tool in ("isolate_host", "patch_service"):
            down.add(host)
        elif tool == "restore_host":
            down.discard(host)
    return down


def downtime_so_far(prior_events: list, weights: dict | None = None) -> dict:
    """Summarize downtime blue caused before this action.

    Mirrors scoring: every prior event adds the weights of the hosts
    down after it. Returns the down hosts, their weights, and the
    accumulated cost.
    """
    table = dict(DEFAULT_BUSINESS_WEIGHTS)
    for host, value in (weights or {}).items():
        try:
            table[host] = int(value)
        except (TypeError, ValueError):
            continue
    down: set = set()
    total = 0
    for event in prior_events or []:
        result = event.get("result", {}) or {}
        if result.get("ok") is not True:
            continue
        tool = event.get("tool", "")
        args = event.get("args", {}) or {}
        host = args.get("host") or result.get("host")
        if tool in ("isolate_host", "patch_service") and host:
            down.add(host)
        elif tool == "restore_host" and host:
            down.discard(host)
        for name in down:
            try:
                total += int(table.get(name, 0) or 0)
            except (TypeError, ValueError):
                continue
    return {
        "down_hosts": sorted(down),
        "weights": {name: table.get(name, 0) for name in sorted(down)},
        "downtime_cost": total,
    }


def _compact_result(tool: str, result: dict) -> str:
    """Render one blue tool result as a short single-line note."""
    result = result or {}
    if result.get("ok") is not True:
        return f"failed: {str(result.get('error', 'unknown error'))[:100]}"
    if tool == "read_logs":
        lines = result.get("lines", []) or []
        ids = [str(row.get("id", "")) for row in lines if row.get("id")]
        return f"ok lines={len(lines)} last={ids[-3:]}"
    if tool == "list_processes":
        return f"ok processes={len(result.get('processes', []) or [])}"
    if tool == "report_finding":
        return f"ok recorded as {result.get('id', '?')}"
    if tool == "wait":
        return f"ok waited ({str(result.get('reason', 'stood by'))[:60]})"
    if tool == "block_ip":
        return f"ok blocked {result.get('ip', '?')}"
    if tool == "isolate_host":
        return f"ok {result.get('host', '?')} detached"
    if tool == "patch_service":
        return (f"ok {result.get('service', '?')} on {result.get('host', '?')}: "
                f"{str(result.get('detail', 'patched'))[:80]}")
    if tool == "restore_host":
        return f"ok {result.get('host', '?')} restored"
    try:
        return json.dumps(result, sort_keys=True)[:160]
    except (TypeError, ValueError):
        return str(result)[:160]


def _blue_history_text(prior_events: list, limit: int = 6) -> str:
    """Render blue's own prior moves; red events never appear here."""
    own = [event for event in prior_events or [] if event.get("actor") == "blue"]
    if not own:
        return "No prior blue moves."
    lines = []
    for event in own[-limit:]:
        tool = event.get("tool", "?")
        try:
            args_text = json.dumps(event.get("args", {}), sort_keys=True)[:160]
        except (TypeError, ValueError):
            args_text = str(event.get("args", ""))[:160]
        note = _compact_result(tool, event.get("result", {}) or {})
        lines.append(f"{event.get('id', '?')} {tool} {args_text} -> {note}")
    return "\n".join(lines)


def build_messages(blue_event: dict, prior_events: list,
                   check: dict | None = None,
                   weights: dict | None = None) -> list:
    """Build the Layer 2 prompt from only what blue knew then.

    Carries blue's prior tool results, the cited log lines with text,
    the action, the host weight, and current downtime. Red events are
    never read here, so red's reasoning cannot leak into the prompt.
    """
    audit_check = check if check is not None else check_evidence(blue_event, prior_events)
    visible = visible_logs(prior_events)
    seen = ref_first_seen(prior_events)
    refs = list((blue_event or {}).get("evidence_refs", []) or [])
    if refs:
        cited = []
        for ref in refs:
            if ref in visible:
                first = seen.get(ref, "?")
                cited.append(f"{ref} (first seen at {first}): {visible[ref]}")
            else:
                cited.append(f"{ref}: NOT VISIBLE to blue at that time")
        cited_text = "\n".join(cited)
    else:
        cited_text = "No log lines cited."
    suspicious = audit_check.get("suspicious_lines", []) or []
    if suspicious:
        susp_text = "\n".join(f"{row['id']}: {row['line']}" for row in suspicious[:8])
    else:
        susp_text = "None."
    host = action_host(blue_event)
    table = dict(DEFAULT_BUSINESS_WEIGHTS)
    for name, value in (weights or {}).items():
        try:
            table[name] = int(value)
        except (TypeError, ValueError):
            continue
    weight_text = f"{host} weight {table.get(host, '?')}" if host else "no host targeted"
    downtime = downtime_so_far(prior_events, weights)
    try:
        args_text = json.dumps((blue_event or {}).get("args", {}), sort_keys=True)
    except (TypeError, ValueError):
        args_text = str((blue_event or {}).get("args", ""))
    user_text = (
        f"Judge this blue team action: {(blue_event or {}).get('tool', '?')} {args_text}\n"
        f"Blue's own note: {str((blue_event or {}).get('reasoning_summary', ''))[:300]}\n"
        f"Cited log lines:\n{cited_text}\n"
        f"Evidence checks: refs_valid={audit_check.get('refs_valid')}, "
        f"suspicious_visible={audit_check.get('suspicious_visible')}.\n"
        f"Suspicious lines visible to blue:\n{susp_text}\n"
        f"Host weight: {weight_text}. "
        f"Downtime so far: cost {downtime['downtime_cost']}, "
        f"hosts down {downtime['down_hosts'] or 'none'}.\n"
        f"Blue's prior tool results:\n{_blue_history_text(prior_events)}\n"
        "Reply with exactly one JSON object: verdict, score, rationale."
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_text},
    ]


def parse_judgment(content: str) -> dict | None:
    """Parse a model reply into verdict, score, and rationale.

    Returns None when the reply carries no usable judgment, so the
    caller can fall back to the deterministic Layer 1 verdict.
    """
    text = str(content or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(text[start:end + 1])
        except ValueError:
            return None
    if not isinstance(parsed, dict):
        return None
    verdict = str(parsed.get("verdict", "") or "").strip().lower()
    if verdict not in VERDICTS:
        return None
    try:
        score = int(parsed.get("score", -1))
    except (TypeError, ValueError):
        return None
    if not 0 <= score <= 10:
        return None
    rationale = " ".join(str(parsed.get("rationale", "") or "").split())
    if not rationale:
        return None
    return {"verdict": verdict, "score": score, "rationale": rationale[:400]}


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


def deterministic_judgment(blue_event: dict, check: dict) -> dict:
    """Layer 1 fallback verdict when the model cannot judge.

    Invalid refs mean the action cited evidence blue never saw, so it
    is unsupported. Valid refs with suspicious activity visible mean
    the action fits what blue knew, so it is justified. Anything else
    acted without visible evidence, so it is unsupported. Finer
    verdicts (premature, late, disproportionate) need the model.
    """
    refs = check.get("evidence_checked", []) or []
    if not check.get("refs_valid", True):
        bad = ", ".join(check.get("invalid_refs", []) or refs)
        return {
            "verdict": "unsupported",
            "score": 2,
            "rationale": (f"Cited log lines ({bad}) were not visible to blue "
                          f"at that time, so the action lacks evidence."),
        }
    if check.get("suspicious_visible"):
        return {
            "verdict": "justified",
            "score": 7,
            "rationale": ("Visible logs showed suspicious activity matching "
                          "this action, so it fits what blue knew."),
        }
    tool = (blue_event or {}).get("tool", "")
    if tool in ("read_logs", "list_processes", "wait"):
        return {
            "verdict": "justified",
            "score": 8,
            "rationale": "Looking before acting is reasonable when nothing is confirmed yet.",
        }
    return {
        "verdict": "unsupported",
        "score": 3,
        "rationale": ("No suspicious activity was visible in the logs blue "
                      "had read, so this action lacks evidence."),
    }


def judge_with_model(blue_event: dict, prior_events: list, check: dict,
                     *, exercise_id: str | None = None, client=None,
                     use_cache: bool = True,
                     weights: dict | None = None) -> dict:
    """Layer 2: ask the model for a verdict, with fallbacks.

    Uses muse-wonder-1.0 at medium effort first. When the API reports
    that model unavailable, retries once on muse-spark-1.3 at high
    effort. Any other failure (spend cap, parse error, outage) falls
    back to the deterministic Layer 1 verdict at zero cost. Returns the
    verdict, score, rationale, model_used, and estimated cost.
    """
    messages = build_messages(blue_event, prior_events, check, weights)
    try:
        response = llm.chat(
            ROLE, messages,
            exercise_id=exercise_id, client=client, use_cache=use_cache,
            max_tokens=MAX_TOKENS, temperature=TEMPERATURE,
            reasoning_effort=PRIMARY_EFFORT,
        )
    except Exception as exc:  # noqa: BLE001 - fallback below by cause
        if _model_unavailable(exc):
            try:
                response = llm.chat(
                    FALLBACK_MODEL, messages,
                    exercise_id=exercise_id, client=client,
                    use_cache=use_cache, max_tokens=MAX_TOKENS,
                    temperature=TEMPERATURE,
                    reasoning_effort=FALLBACK_EFFORT,
                )
            except Exception:
                verdict = deterministic_judgment(blue_event, check)
                return {**verdict, "model_used": FALLBACK_NO_MODEL,
                        "estimated_cost_usd": 0.0}
        else:
            verdict = deterministic_judgment(blue_event, check)
            return {**verdict, "model_used": FALLBACK_NO_MODEL,
                    "estimated_cost_usd": 0.0}
    parsed = parse_judgment((response or {}).get("content", ""))
    if parsed is None:
        verdict = deterministic_judgment(blue_event, check)
        model_used = str((response or {}).get("model", "") or PRIMARY_MODEL)
        return {**verdict, "model_used": model_used,
                "estimated_cost_usd": 0.0}
    model_used = str((response or {}).get("model", "") or PRIMARY_MODEL)
    usage = (response or {}).get("usage", {}) or {}
    try:
        cost = llm.estimate_cost(
            model_used, int(usage.get("input_tokens", 0) or 0),
            int(usage.get("output_tokens", 0) or 0))
    except (TypeError, ValueError):
        cost = 0.0
    if (response or {}).get("cached"):
        cost = 0.0
    return {**parsed, "model_used": model_used,
            "estimated_cost_usd": round(cost, 6)}


def audit_blue_event(blue_event: dict, prior_events: list,
                     *, exercise_id: str | None = None, client=None,
                     use_cache: bool = True,
                     weights: dict | None = None) -> dict:
    """Run both layers for one blue event and return the audit record."""
    priors = [event for event in prior_events or []
              if event.get("actor") != ACTOR]
    check = check_evidence(blue_event, priors)
    judgment = judge_with_model(
        blue_event, priors, check, exercise_id=exercise_id,
        client=client, use_cache=use_cache, weights=weights)
    return {
        "target_event_id": (blue_event or {}).get("id", ""),
        "verdict": judgment["verdict"],
        "score": judgment["score"],
        "rationale": judgment["rationale"],
        "evidence_checked": check["evidence_checked"],
        "refs_valid": check["refs_valid"],
        "model_used": judgment["model_used"],
        "estimated_cost_usd": judgment["estimated_cost_usd"],
    }


def audit_exercise(event_list: list, *, exercise_id: str | None = None,
                   client=None, use_cache: bool = True,
                   weights: dict | None = None) -> list:
    """Audit every blue action in an event list (end-of-exercise mode)."""
    audits = []
    for index, event in enumerate(event_list or []):
        if event.get("actor") != "blue":
            continue
        if event.get("tool") in SKIP_TOOLS:
            continue
        audits.append(audit_blue_event(
            event, list(event_list[:index]), exercise_id=exercise_id,
            client=client, use_cache=use_cache, weights=weights))
    return audits


def summarize_audits(audits: list) -> dict:
    """Aggregate audit records into the summary metrics."""
    total = len(audits or [])
    if not total:
        return {"justified_pct": None, "unsupported_count": 0,
                "invalid_refs_count": 0, "mean_audit_score": None,
                "audit_count": 0, "audit_cost_usd": 0.0}
    justified = sum(1 for audit in audits if audit.get("verdict") == "justified")
    unsupported = sum(1 for audit in audits if audit.get("verdict") == "unsupported")
    invalid = sum(1 for audit in audits if audit.get("refs_valid") is False)
    scores = [audit.get("score") for audit in audits
              if isinstance(audit.get("score"), (int, float))]
    mean_score = round(sum(scores) / len(scores), 2) if scores else None
    cost = 0.0
    for audit in audits:
        try:
            cost += float(audit.get("estimated_cost_usd", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
    return {"justified_pct": round(justified / total * 100.0, 2),
            "unsupported_count": unsupported,
            "invalid_refs_count": invalid,
            "mean_audit_score": mean_score,
            "audit_count": total,
            "audit_cost_usd": round(cost, 6)}


def audit_result(audit: dict) -> dict:
    """Render an audit record as the result payload of an auditor event."""
    return {
        "ok": True,
        "target_event_id": audit.get("target_event_id", ""),
        "verdict": audit.get("verdict", ""),
        "score": audit.get("score", 0),
        "rationale": audit.get("rationale", ""),
        "evidence_checked": list(audit.get("evidence_checked", []) or []),
        "refs_valid": audit.get("refs_valid") is True,
        "model_used": audit.get("model_used", FALLBACK_NO_MODEL),
        "estimated_cost_usd": audit.get("estimated_cost_usd", 0.0),
    }


def audit_reasoning(audit: dict) -> str:
    """Render an audit record as a one-line reasoning summary."""
    return f"[{audit.get('verdict', '?')} {audit.get('score', '?')}/10] {audit.get('rationale', '')}"
