"""Exercise debrief: short structured report from a finished event log.

Layer 1 is deterministic code with no model: outcome, a timeline of 4
to 6 key moments with event ids, detection delay and the containment
move, what the defender did well, and 2 to 4 weaknesses with
recommendations, each citing the event ids that support it. Facts come
from sensor alerts, defender moves, and host and credential facts in
the log.

Layer 2 optionally rewrites the wording in plain language with Muse
Wonder. Every rewritten citation must match Layer 1 exactly and exist
in the log; any mismatch or error falls back to Layer 1 unchanged.

Wording across both layers: exercise, containment, defender. No em
dashes. Credentials and file contents from the log are never quoted.
"""

from __future__ import annotations

import json

from crucible import events as event_defs
from crucible import llm, sensors

ROLE = "debrief"
# The rewrite returns the full report (about as long as the prompt,
# near 800 tokens for the recorded runs), so 1024 truncates the JSON
# and every live call falls back. Measured live, then raised.
REWRITE_MAX_TOKENS = 2048
REWRITE_TEMPERATURE = 0.0
REWRITE_EFFORT = "low"

CONTAINMENT_TOOLS = ("isolate_host", "patch_service", "restore_host")
LOOK_TOOLS = ("read_logs", "list_processes")

SYSTEM_PROMPT = (
    "You rewrite an exercise debrief in plain language. Reply with ONLY "
    "one JSON object, no preamble, no markdown fences: "
    '{"outcome_text": "...", "timeline": [{"event_id": "...", '
    '"title": "...", "detail": "..."}], '
    '"strengths": [{"text": "...", "event_ids": [...]}], '
    '"weaknesses": [{"text": "...", "recommendation": "...", '
    '"event_ids": [...]}]}. '
    "Keep every event_id and event_ids value EXACTLY as given, in the "
    "same order, with the same item counts. Change only the wording. "
    "Never invent event ids. Use the words exercise, containment, and "
    "defender. Never use em dashes."
)


def _is_defender(actor: str | None) -> bool:
    return actor in ("blue", "human")


def _is_lane(actor: str | None) -> bool:
    return actor in ("red", "blue", "human")


def _ok(event: dict) -> bool:
    return (event.get("result", {}) or {}).get("ok") is True


def _host_of(event: dict) -> str | None:
    args = event.get("args", {}) or {}
    result = event.get("result", {}) or {}
    return result.get("host") or args.get("host")


def _moves(n: int) -> str:
    return f"{n} move" if n == 1 else f"{n} moves"


def _join_hosts(hosts: list) -> str:
    if len(hosts) == 1:
        return hosts[0]
    return ", ".join(hosts[:-1]) + " and " + hosts[-1]


def _scoring(event_list: list) -> list:
    """Events that count for positions: all but auditor and system."""
    return [event for event in event_list or []
            if event.get("actor") not in ("auditor", "system")]


def _first(scoring: list, pred) -> tuple[int, dict] | None:
    for pos, event in enumerate(scoring, start=1):
        if pred(event):
            return pos, event
    return None


def _secret_path(text: str | None) -> bool:
    return isinstance(text, str) and "/secret" in text


def _hint_probe(event: dict) -> bool:
    """Red request to a privileged page with a 2xx status."""
    if event.get("actor") != "red" or event.get("tool") != "http_request":
        return False
    result = event.get("result", {}) or {}
    if result.get("ok") is not True:
        return False
    if not str(result.get("status", "")).startswith("2"):
        return False
    path = str((event.get("args", {}) or {}).get("path", "") or "")
    return "admin" in path.lower()


def _blocked_read(event: dict) -> bool:
    """Failed red read or listing aimed at the protected file."""
    if event.get("actor") != "red" or _ok(event):
        return False
    args = event.get("args", {}) or {}
    result = event.get("result", {}) or {}
    if event.get("tool") == "read_file":
        return _secret_path(result.get("path")) or _secret_path(
            args.get("path"))
    if event.get("tool") == "list_files":
        return _secret_path(result.get("path")) or _secret_path(
            args.get("path"))
    return False


def _down_at_end(scoring: list) -> dict:
    """Map hosts still down at the end to their last down-causing id."""
    down: dict = {}
    for event in scoring:
        if not _ok(event):
            continue
        host = _host_of(event)
        if not host:
            continue
        if event.get("tool") in ("isolate_host", "patch_service"):
            down[host] = event.get("id", "")
        elif event.get("tool") == "restore_host":
            down.pop(host, None)
    return down


def _containment_move_text(event: dict) -> str:
    host = _host_of(event) or "a host"
    if event.get("tool") == "patch_service":
        args = event.get("args", {}) or {}
        result = event.get("result", {}) or {}
        service = args.get("service") or result.get("service")
        if service:
            return f"patched {service} on {host}"
        return f"patched a service on {host}"
    if event.get("tool") == "restore_host":
        return f"restored {host}"
    return f"isolated {host}"


def _facts(event_list: list):
    """Collect the positioned facts Layer 1 rules build on."""
    scoring = _scoring(event_list)
    moves = [event for event in scoring if _is_lane(event.get("actor"))]
    first_red = _first(scoring, lambda e: e.get("actor") == "red")
    detection = _first(
        scoring, lambda e: _is_defender(e.get("actor"))
        and e.get("tool") == "report_finding" and _ok(e))
    signin = _first(
        scoring, lambda e: e.get("actor") == "red"
        and e.get("tool") == "try_credential"
        and (e.get("result", {}) or {}).get("authenticated") is True)
    attempt = _first(
        scoring, lambda e: e.get("actor") == "red"
        and e.get("tool") == "try_credential")
    containment = _first(
        scoring, lambda e: _is_defender(e.get("actor"))
        and e.get("tool") in CONTAINMENT_TOOLS and _ok(e))
    secret = _first(
        scoring, lambda e: e.get("actor") == "red"
        and e.get("tool") == "read_file" and _ok(e)
        and _secret_path((e.get("result", {}) or {}).get("path")))
    probe = _first(
        scoring, lambda e: e.get("actor") == "red"
        and e.get("tool") in ("scan_host", "http_request"))
    look = _first(
        scoring, lambda e: _is_defender(e.get("actor"))
        and e.get("tool") in LOOK_TOOLS)
    alerted = _first(scoring, lambda e: bool(e.get("sensor_alerts")))
    blocked = None
    for pos, event in enumerate(scoring, start=1):
        if not _blocked_read(event):
            continue
        if containment is not None and pos >= containment[0]:
            blocked = (pos, event)
            break
    return {
        "scoring": scoring, "moves": moves, "first_red": first_red,
        "detection": detection, "signin": signin, "attempt": attempt,
        "containment": containment, "secret": secret, "probe": probe,
        "look": look, "alerted": alerted, "blocked": blocked,
    }


def _outcome(facts: dict) -> tuple[str, str]:
    if facts["secret"] is not None:
        host = _host_of(facts["secret"][1])
        where = f" on {host}" if host else ""
        return ("file_read",
                "Exercise over: the intruder read the protected file"
                f"{where}.")
    acted = any(
        _is_defender(event.get("actor")) and _ok(event)
        and (event.get("tool") == "report_finding"
             or event.get("tool") in CONTAINMENT_TOOLS)
        for event in facts["scoring"])
    if acted:
        if facts["signin"] is not None:
            host = None
            if facts["containment"] is not None:
                host = _host_of(facts["containment"][1])
            host = host or _host_of(facts["signin"][1])
            where = f" on {host}" if host else ""
            return ("contained",
                    "Exercise over: the defender contained the intruder"
                    f"{where}. The protected file was never read.")
        return ("contained",
                "Exercise over: the defender held the exercise. "
                "The intruder never signed in and the protected file "
                "was never read.")
    return ("quiet",
            "Exercise over: quiet exercise. The intruder never signed "
            "in and the defender took no action.")


def _timeline(facts: dict) -> list:
    """Pick 4 to 6 key moments in chronological order.

    Detection and containment moments are always present when the
    exercise gave them something to say (a real move, or an honest
    note that none happened while the intruder moved).
    """
    scoring = facts["scoring"]
    moves = facts["moves"]
    first_id = moves[0].get("id", "") if moves else ""
    last_id = moves[-1].get("id", "") if moves else ""
    total = len(scoring)
    moments = []

    def add(key, rank, pos, event_id, title, detail):
        moments.append({"key": key, "rank": rank, "pos": pos,
                        "event_id": event_id, "title": title,
                        "detail": detail})

    if facts["detection"] is not None:
        pos, event = facts["detection"]
        if facts["first_red"] is not None:
            delay = pos - facts["first_red"][0]
            rid = facts["first_red"][1].get("id", "")
            detail = (
                f"Detection came {_moves(delay)} after the first "
                f"intruder move ({rid} to {event.get('id', '')}).")
        else:
            detail = (f"The defender reported a finding "
                      f"({event.get('id', '')}).")
        add("detection", 0, pos, event.get("id", ""), "Detection", detail)
    elif facts["first_red"] is not None and last_id:
        add("no_detection", 0, total + 1, last_id, "No detection",
            "The defender never reported the intrusion through "
            f"{last_id}.")

    if facts["containment"] is not None:
        pos, event = facts["containment"]
        detail = (f"The defender {_containment_move_text(event)} "
                  f"({event.get('id', '')}).")
        if facts["signin"] is not None:
            gap = pos - facts["signin"][0]
            sid = facts["signin"][1].get("id", "")
            detail += (f" That came {_moves(gap)} after the sign-in "
                       f"({sid} to {event.get('id', '')}).")
        add("containment", 0, pos, event.get("id", ""),
            "Containment", detail)
    elif (facts["signin"] is not None or facts["secret"] is not None) \
            and last_id:
        add("no_containment", 0, total + 1, last_id, "No containment",
            "The signed-in host was never contained through "
            f"{last_id}.")

    if facts["signin"] is not None:
        pos, event = facts["signin"]
        args = event.get("args", {}) or {}
        host = _host_of(event)
        user = args.get("user")
        if host and user:
            detail = (f"The intruder signed in to {host} as {user} "
                      f"({event.get('id', '')}).")
        else:
            detail = (f"The intruder signed in "
                      f"({event.get('id', '')}).")
        add("signin", 10, pos, event.get("id", ""),
            "First sign-in", detail)
    elif facts["attempt"] is not None:
        pos, event = facts["attempt"]
        host = _host_of(event)
        where = f" to {host}" if host else ""
        add("attempt", 50, pos, event.get("id", ""),
            "Sign-in attempts",
            f"The intruder tried to sign in{where} "
            f"({event.get('id', '')}). No attempt opened a host.")

    if facts["secret"] is not None:
        pos, event = facts["secret"]
        host = _host_of(event)
        where = f" on {host}" if host else ""
        add("secret", 10, pos, event.get("id", ""),
            "Protected file read",
            f"The intruder read the protected file{where} "
            f"({event.get('id', '')}).")

    if facts["blocked"] is not None:
        pos, event = facts["blocked"]
        add("blocked", 60, pos, event.get("id", ""), "Read blocked",
            "The intruder could not reach the protected file after "
            f"containment ({event.get('id', '')}).")

    if facts["probe"] is not None:
        pos, event = facts["probe"]
        host = _host_of(event) or "a host"
        if event.get("tool") == "http_request":
            path = (event.get("args", {}) or {}).get("path", "a page")
            detail = (f"The intruder asked {host} for {path} "
                      f"({event.get('id', '')}).")
        else:
            detail = (f"The intruder checked whether {host} is up "
                      f"({event.get('id', '')}).")
        add("probe", 70, pos, event.get("id", ""),
            "First probe", detail)

    if facts["alerted"] is not None:
        pos, event = facts["alerted"]
        alerts = list(event.get("sensor_alerts", []) or [])
        order = {name: index for index, name
                 in enumerate(sensors.SEVERITIES)}
        worst = "low"
        for alert in alerts:
            level = str((alert or {}).get("severity", "low"))
            if order.get(level, 0) >= order.get(worst, 0):
                worst = level if level in order else worst
        if len(alerts) == 1:
            count = "1 sensor alert was"
        else:
            count = f"{len(alerts)} sensor alerts were"
        add("alert", 80, pos, event.get("id", ""), "First alert",
            f"{count} showing on the defender turn, "
            f"highest {worst} ({event.get('id', '')}).")

    if facts["look"] is not None:
        pos, event = facts["look"]
        host = _host_of(event) or "a host"
        if event.get("tool") == "list_processes":
            what = f"checked running programs on {host}"
        else:
            what = f"checked logs on {host}"
        add("look", 90, pos, event.get("id", ""), "First look",
            f"The defender {what} ({event.get('id', '')}).")

    if last_id:
        add("end", 100, total + 1, last_id, "Exercise closed",
            f"The exercise closed after {total} moves.")

    moments.sort(key=lambda item: (item["pos"], item["rank"]))
    while len(moments) > 6:
        moments.sort(key=lambda item: (item["rank"], item["pos"]),
                     reverse=True)
        moments.pop(0)
        moments.sort(key=lambda item: (item["pos"], item["rank"]))
    if len(moments) < 4:
        cited = {item["event_id"] for item in moments}
        rest = [event for event in moves
                if event.get("id", "") not in cited]
        while len(moments) < 4 and rest:
            event = rest[len(rest) // 2]
            rest.remove(event)
            pos = scoring.index(event) + 1
            moments.append(
                {"key": "mid", "rank": 95, "pos": pos,
                 "event_id": event.get("id", ""), "title": "Mid exercise",
                 "detail": f"Move {pos} of {total}."})
        moments.sort(key=lambda item: (item["pos"], item["rank"]))
    return [{"event_id": item["event_id"], "title": item["title"],
             "detail": item["detail"]} for item in moments]


def _strengths(facts: dict) -> list:
    """What the defender did well, each with supporting event ids."""
    scoring = facts["scoring"]
    found = []

    if facts["containment"] is not None:
        cid = facts["containment"][1].get("id", "")
        if facts["signin"] is not None:
            gap = facts["containment"][0] - facts["signin"][0]
            sid = facts["signin"][1].get("id", "")
            if gap <= 2:
                found.append(
                    {"text": "The defender contained the signed-in "
                             f"host within {_moves(gap)} of the sign-in.",
                     "event_ids": [sid, cid]})
        else:
            found.append(
                {"text": "The defender contained a host before any "
                         "sign-in.",
                 "event_ids": [cid]})

    if facts["detection"] is not None:
        did = facts["detection"][1].get("id", "")
        if facts["first_red"] is not None:
            delay = facts["detection"][0] - facts["first_red"][0]
            rid = facts["first_red"][1].get("id", "")
            found.append(
                {"text": "The defender reported the intrusion "
                         f"{_moves(delay)} after the first intruder move.",
                 "event_ids": [rid, did]})
        else:
            found.append(
                {"text": "The defender reported a finding before any "
                         "intruder move.",
                 "event_ids": [did]})

    if facts["look"] is not None:
        acted = _first(
            scoring, lambda e: _is_defender(e.get("actor")) and _ok(e)
            and (e.get("tool") == "report_finding"
                 or e.get("tool") in CONTAINMENT_TOOLS))
        if acted is not None and facts["look"][0] < acted[0]:
            lid = facts["look"][1].get("id", "")
            aid = acted[1].get("id", "")
            if facts["look"][1].get("tool") == "list_processes":
                what = "checked running programs before acting"
            else:
                what = "checked host logs before acting"
            found.append(
                {"text": f"The defender {what}.",
                 "event_ids": [lid, aid]})

    patch = _first(
        scoring, lambda e: _is_defender(e.get("actor"))
        and e.get("tool") == "patch_service" and _ok(e))
    if patch is not None:
        args = patch[1].get("args", {}) or {}
        result = patch[1].get("result", {}) or {}
        service = args.get("service") or result.get("service")
        host = _host_of(patch[1]) or "a host"
        if service:
            text = f"The defender patched {service} on {host}."
        else:
            text = f"The defender patched a service on {host}."
        found.append({"text": text, "event_ids": [patch[1].get("id", "")]})

    cited = [event for event in scoring
             if _is_defender(event.get("actor")) and _ok(event)
             and event.get("tool") in ("report_finding",) + CONTAINMENT_TOOLS
             and list(event.get("evidence_refs", []) or [])]
    if cited:
        alerted = any(sensors.is_alert_ref(ref)
                      for event in cited
                      for ref in (event.get("evidence_refs", []) or []))
        if alerted:
            text = "The defender acted on sensor alert evidence."
        else:
            text = "The defender attached log evidence to a report."
        found.append({"text": text,
                      "event_ids": [event.get("id", "") for event in cited]})

    restore = _first(
        scoring, lambda e: _is_defender(e.get("actor"))
        and e.get("tool") == "restore_host" and _ok(e))
    if restore is not None:
        host = _host_of(restore[1]) or "a host"
        found.append(
            {"text": f"The defender restored {host}, bringing it back up.",
             "event_ids": [restore[1].get("id", "")]})

    if facts["secret"] is None and facts["signin"] is not None:
        sid = facts["signin"][1].get("id", "")
        if facts["containment"] is not None:
            support = [sid, facts["containment"][1].get("id", "")]
        else:
            support = [sid, facts["moves"][-1].get("id", "")]
        found.append(
            {"text": "The protected file stayed unread even after "
                     "a sign-in.",
             "event_ids": support})
    return found


def _weaknesses(facts: dict, outcome: str) -> list:
    """2 to 4 weaknesses with recommendations, each citing event ids.

    Specific rules fire first in importance order; grounded fallbacks
    fill the list to at least 2 so a clean exercise still reviews
    honestly.
    """
    scoring = facts["scoring"]
    moves = facts["moves"]
    first_id = moves[0].get("id", "") if moves else ""
    last_id = moves[-1].get("id", "") if moves else ""
    delay = None
    if facts["detection"] is not None and facts["first_red"] is not None:
        delay = facts["detection"][0] - facts["first_red"][0]
    candidates = []

    def add(text, recommendation, event_ids):
        candidates.append({"text": text, "recommendation": recommendation,
                           "event_ids": [eid for eid in event_ids if eid]})

    if facts["secret"] is not None:
        host = _host_of(facts["secret"][1])
        where = f" on {host}" if host else ""
        ids = [facts["secret"][1].get("id", "")]
        if facts["signin"] is not None:
            ids.append(facts["signin"][1].get("id", ""))
        order = {event.get("id", ""): pos
                 for pos, event in enumerate(scoring, start=1)}
        ids.sort(key=lambda eid: order.get(eid, 0))
        add(f"The intruder read the protected file{where}.",
            "Contain the signed-in host before the intruder reaches "
            "the protected file.",
            ids)

    compromised = facts["signin"] is not None or facts["secret"] is not None
    if compromised and facts["containment"] is None:
        anchor = facts["signin"] or facts["secret"]
        add("The signed-in host was never contained.",
            "Contain the signed-in host on the next defender turn.",
            [anchor[1].get("id", ""), last_id])

    if facts["first_red"] is not None and facts["detection"] is None:
        rid = facts["first_red"][1].get("id", "")
        add("The defender never reported the intrusion.",
            "Report findings as soon as logs or alerts look wrong.",
            [rid, last_id])

    if delay is not None and delay > 4:
        rid = facts["first_red"][1].get("id", "")
        did = facts["detection"][1].get("id", "")
        add(f"Detection took {_moves(delay)} after the first intruder "
            "move.",
            "Check entry host logs on every defender turn so probing "
            "is seen sooner.",
            [rid, did])

    if facts["signin"] is not None and facts["containment"] is not None:
        gap = facts["containment"][0] - facts["signin"][0]
        if gap > 4:
            sid = facts["signin"][1].get("id", "")
            cid = facts["containment"][1].get("id", "")
            add(f"Containment came {_moves(gap)} after the sign-in.",
                "Contain the signed-in host on the next defender turn.",
                [sid, cid])

    if facts["signin"] is not None:
        args = facts["signin"][1].get("args", {}) or {}
        host = _host_of(facts["signin"][1])
        user = args.get("user")
        if host and user:
            text = f"A guessed password opened {host} as {user}."
            rec_host = f"Rotate the {host} password and slow repeated "
        elif host:
            text = f"A guessed password opened {host}."
            rec_host = f"Rotate the {host} password and slow repeated "
        else:
            text = "A guessed password opened one host."
            rec_host = "Rotate the host password and slow repeated "
        add(text, rec_host + "bad guesses.",
            [facts["signin"][1].get("id", "")])

    probe = _first(scoring, _hint_probe)
    if probe is not None:
        host = _host_of(probe[1])
        patched = None
        for pos, event in enumerate(scoring, start=1):
            if pos <= probe[0]:
                continue
            if not _is_defender(event.get("actor")) or not _ok(event):
                continue
            if event.get("tool") != "patch_service":
                continue
            if host is None or _host_of(event) == host:
                patched = (pos, event)
                break
        where = f" on {host}" if host else ""
        if patched is None:
            add(f"The open admin page{where} stayed open through the "
                "exercise.",
                "Patch the exposed page as soon as probing shows.",
                [probe[1].get("id", ""), last_id])
        elif patched[0] - probe[0] > 2:
            gap = patched[0] - probe[0]
            add(f"The open admin page{where} stayed up {_moves(gap)} "
                "after probing.",
                "Patch the exposed page as soon as probing shows.",
                [probe[1].get("id", ""), patched[1].get("id", "")])

    if facts["alerted"] is not None:
        acted = _first(
            scoring, lambda e: _is_defender(e.get("actor")) and _ok(e)
            and (e.get("tool") == "report_finding"
                 or e.get("tool") in CONTAINMENT_TOOLS))
        aid = facts["alerted"][1].get("id", "")
        if acted is None:
            add("Sensor alerts were showing but the defender never "
                "reported or contained.",
                "Treat the first sensor alert as the trigger to report.",
                [aid, last_id])
        elif acted[0] - facts["alerted"][0] > 3:
            gap = acted[0] - facts["alerted"][0]
            add(f"The defender took {_moves(gap)} to act after the "
                "first sensor alert.",
                "Treat the first sensor alert as the trigger to report.",
                [aid, acted[1].get("id", "")])

    down = _down_at_end(scoring)
    if down:
        order = {event.get("id", ""): pos
                 for pos, event in enumerate(scoring, start=1)}
        ids = sorted(down.values(), key=lambda eid: order.get(eid, 0))
        hosts = _join_hosts(sorted(down))
        add(f"{hosts} stayed down at the end of the exercise.",
            "Restore each host after patching to limit downtime.",
            ids)

    actions = [event for event in scoring
               if _is_defender(event.get("actor")) and _ok(event)
               and event.get("tool") in ("report_finding",)
               + CONTAINMENT_TOOLS]
    if actions and not any(list(event.get("evidence_refs", []) or [])
                           for event in actions):
        add("Defender reports and containment cited no log lines.",
            "Attach the log lines that support each report.",
            [event.get("id", "") for event in actions])

    failed = [event for event in scoring
              if _is_defender(event.get("actor"))
              and (event.get("result", {}) or {}).get("ok") is False
              and event.get("tool") not in ("wait", "agent_error")]
    if failed:
        host = _host_of(failed[0])
        where = f" on {host}" if host else ""
        if len(failed) == 1:
            text = f"A defender move failed{where}."
        else:
            text = f"{len(failed)} defender moves failed, first{where}."
        add(text,
            "Re-check the host state and retry failed moves.",
            [event.get("id", "") for event in failed])

    red_positions = [pos for pos, event in enumerate(scoring, start=1)
                     if event.get("actor") == "red"]
    last_red = max(red_positions) if red_positions else 0
    idle = [event for pos, event in enumerate(scoring, start=1)
            if _is_defender(event.get("actor"))
            and event.get("tool") in ("wait", "agent_error")
            and pos < last_red]
    if idle:
        count = f"{len(idle)} turn" + ("" if len(idle) == 1 else "s")
        add(f"The defender skipped {count} while the intruder kept "
            "moving.",
            "Use quiet turns to check logs on the busiest hosts.",
            [event.get("id", "") for event in idle])

    if delay is not None and 1 <= delay <= 4:
        rid = facts["first_red"][1].get("id", "")
        did = facts["detection"][1].get("id", "")
        add(f"The intruder still had {_moves(delay)} before detection.",
            "Shorten the watch cycle on the entry host.",
            [rid, did])

    if facts["signin"] is None:
        add("The intruder never signed in, so containment of a "
            "signed-in host went untested.",
            "Run an exercise where the intruder signs in, to test "
            "containment under pressure.",
            [first_id, last_id])

    if not any(event.get("sensor_alerts") for event in scoring):
        add("No sensor alerts appear in this log.",
            "Confirm alerts are recorded on defender turns so reviews "
            "can use them.",
            [first_id, last_id])

    add("Review each timeline moment with the team before the next "
        "exercise.",
        "Confirm who owns detection and containment for every host.",
        [first_id, last_id])

    return candidates[:4]


def build_debrief(event_list: list, run_id: str | None = None) -> dict:
    """Layer 1: build the deterministic debrief for one event log."""
    facts = _facts(event_list)
    if not facts["moves"]:
        raise ValueError("no exercise moves in this log")
    outcome, outcome_text = _outcome(facts)
    delay = None
    if facts["detection"] is not None and facts["first_red"] is not None:
        delay = facts["detection"][0] - facts["first_red"][0]
    containment_move = None
    if facts["containment"] is not None:
        containment_move = _containment_move_text(facts["containment"][1])
    report = {
        "run_id": run_id,
        "layer": 1,
        "model_used": "none",
        "outcome": outcome,
        "outcome_text": outcome_text,
        "detection_delay_events": delay,
        "detection_event_id": (facts["detection"][1].get("id", "")
                               if facts["detection"] is not None else None),
        "containment_event_id": (
            facts["containment"][1].get("id", "")
            if facts["containment"] is not None else None),
        "containment_move": containment_move,
        "timeline": _timeline(facts),
        "strengths": _strengths(facts),
        "weaknesses": _weaknesses(facts, outcome),
    }
    report["markdown"] = to_markdown(report)
    return report


def cited_event_ids(report: dict) -> list:
    """Every event id a report cites, in order, without duplicates."""
    seen = []
    candidates = []
    candidates.extend(moment.get("event_id", "")
                      for moment in (report or {}).get("timeline", []) or [])
    for key in ("detection_event_id", "containment_event_id"):
        value = (report or {}).get(key)
        if value:
            candidates.append(value)
    for item in (report or {}).get("strengths", []) or []:
        candidates.extend(item.get("event_ids", []) or [])
    for item in (report or {}).get("weaknesses", []) or []:
        candidates.extend(item.get("event_ids", []) or [])
    for event_id in candidates:
        if event_id and event_id not in seen:
            seen.append(event_id)
    return seen


def validate_citations(report: dict, event_list: list) -> list:
    """Return cited event ids missing from the log, in cited order."""
    known = {event.get("id", "") for event in event_list or []}
    return [event_id for event_id in cited_event_ids(report)
            if event_id not in known]


def to_markdown(report: dict) -> str:
    """Render one debrief report as markdown for the copy button."""
    report = report or {}
    title = "# Exercise debrief"
    if report.get("run_id"):
        title += f": {report['run_id']}"
    lines = [title, "", "## Outcome", report.get("outcome_text", ""), ""]
    delay = report.get("detection_delay_events")
    if delay is None:
        lines.append("Detection delay: none recorded.")
    else:
        lines.append(f"Detection delay: {_moves(delay)}.")
    move = report.get("containment_move")
    if move and report.get("containment_event_id"):
        lines.append(f"Containment move: {move} "
                     f"({report['containment_event_id']}).")
    else:
        lines.append("Containment move: none.")
    lines += ["", "## Timeline"]
    for moment in report.get("timeline", []) or []:
        lines.append(f"- **{moment.get('title', '')}** "
                     f"({moment.get('event_id', '')}): "
                     f"{moment.get('detail', '')}")
    lines += ["", "## What the defender did well"]
    strengths = report.get("strengths", []) or []
    if not strengths:
        lines.append("None recorded.")
    for item in strengths:
        refs = ", ".join(item.get("event_ids", []) or [])
        lines.append(f"- {item.get('text', '')} ({refs})")
    lines += ["", "## Weaknesses and recommendations"]
    for item in report.get("weaknesses", []) or []:
        refs = ", ".join(item.get("event_ids", []) or [])
        lines.append(f"- {item.get('text', '')} ({refs})")
        lines.append(f"  Recommendation: {item.get('recommendation', '')}")
    layer = report.get("layer", 1)
    if layer == 2:
        tail = ("_Layer 2 report, plain-language rewrite by Muse Wonder. "
                "All event ids cite the exercise log._")
    else:
        tail = ("_Layer 1 report, derived from the exercise log with no "
                "model. All event ids cite the exercise log._")
    lines += ["", tail, ""]
    return "\n".join(lines)


def build_messages(report: dict) -> list:
    """Build the plain-language rewrite prompt from a Layer 1 report."""
    source = {
        "outcome_text": (report or {}).get("outcome_text", ""),
        "timeline": (report or {}).get("timeline", []) or [],
        "strengths": (report or {}).get("strengths", []) or [],
        "weaknesses": (report or {}).get("weaknesses", []) or [],
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(source, sort_keys=True)},
    ]


def _parse_rewrite(content: str) -> dict | None:
    """Extract the rewrite JSON object, or None when it is not usable."""
    text = str(content or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _clean_text(value) -> str | None:
    """Sanitize one rewritten string, or None when it is empty."""
    if not isinstance(value, str):
        return None
    cleaned = event_defs.sanitize_reasoning_summary(value).strip()
    return cleaned or None


def _checked_rewrite(parsed: dict, layer1: dict) -> dict | None:
    """Validate a rewrite against Layer 1, or None on any mismatch.

    Item counts and every cited event id must match Layer 1 exactly,
    so the rewrite cannot add a claim Layer 1 never made. Only the
    wording may change.
    """
    if not isinstance(parsed, dict):
        return None
    outcome_text = _clean_text(parsed.get("outcome_text"))
    if outcome_text is None:
        return None
    timeline, strengths, weaknesses = [], [], []
    want_timeline = layer1.get("timeline", []) or []
    got_timeline = parsed.get("timeline")
    if not isinstance(got_timeline, list) or len(got_timeline) != len(
            want_timeline):
        return None
    for want, got in zip(want_timeline, got_timeline):
        if not isinstance(got, dict):
            return None
        if got.get("event_id") != want.get("event_id"):
            return None
        title = _clean_text(got.get("title"))
        detail = _clean_text(got.get("detail"))
        if title is None or detail is None:
            return None
        timeline.append({"event_id": want.get("event_id"),
                         "title": title, "detail": detail})
    for key, target in (("strengths", strengths),
                        ("weaknesses", weaknesses)):
        want_items = layer1.get(key, []) or []
        got_items = parsed.get(key)
        if not isinstance(got_items, list) or len(got_items) != len(
                want_items):
            return None
        for want, got in zip(want_items, got_items):
            if not isinstance(got, dict):
                return None
            if list(got.get("event_ids", [])) != list(
                    want.get("event_ids", [])):
                return None
            text = _clean_text(got.get("text"))
            if text is None:
                return None
            item = {"text": text,
                    "event_ids": list(want.get("event_ids", []))}
            if key == "weaknesses":
                rec = _clean_text(got.get("recommendation"))
                if rec is None:
                    return None
                item["recommendation"] = rec
            target.append(item)
    return {"outcome_text": outcome_text, "timeline": timeline,
            "strengths": strengths, "weaknesses": weaknesses}


def rewrite_plain(report: dict, event_list: list, *,
                  exercise_id: str | None = None, client=None,
                  use_cache: bool = True, role: str = ROLE) -> dict:
    """Layer 2: rewrite a Layer 1 report in plain language.

    Uses the debrief role (Muse Wonder). Any failure, unparseable
    reply, shape mismatch, or unknown cited event id falls back to
    the Layer 1 report unchanged.
    """
    try:
        response = llm.chat(
            role, build_messages(report), exercise_id=exercise_id,
            client=client, use_cache=use_cache,
            max_tokens=REWRITE_MAX_TOKENS,
            temperature=REWRITE_TEMPERATURE,
            reasoning_effort=REWRITE_EFFORT,
        )
        parsed = _parse_rewrite((response or {}).get("content", ""))
        checked = _checked_rewrite(parsed or {}, report)
        if checked is None:
            return report
        model_used = str((response or {}).get("model", "") or
                         "muse-wonder-1.0")
        rewritten = {**report, **checked, "layer": 2,
                     "model_used": model_used}
        rewritten["markdown"] = to_markdown(rewritten)
        if validate_citations(rewritten, event_list):
            return report
        return rewritten
    except Exception:
        return report
