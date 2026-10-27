"""Turn loop and scoring for validation exercises.

Runs red team and blue team agents in alternation up to a max turn
count. Each agent move calls one shared tool, and each call is logged
as an event to jsonl. Scoring reads the event list only, so results
stay reproducible from the saved log.
"""

from __future__ import annotations

import inspect
import statistics

from crucible import events, tools
from crucible import sensors as sensor_rules
from crucible.agents.auditor import (
    AUDIT_MODES,
    SKIP_TOOLS as AUDIT_SKIP_TOOLS,
    audit_blue_event,
    audit_exercise as audit_blue_events,
    audit_reasoning,
    audit_result,
    summarize_audits,
)
from crucible.agents.scripted_blue import ScriptedBlue
from crucible.agents.scripted_red import ScriptedRed

CONTAINMENT_TOOLS = ("isolate_host", "patch_service", "restore_host")
MAX_TURNS = 40

DEFAULT_BUSINESS_WEIGHTS = {"web": 3, "app": 3, "db": 5, "workstation": 1}
BLUE_SCORE_MAX = 100

_DISPATCH = {
    "scan_host": tools.scan_host,
    "http_request": tools.http_request,
    "try_credential": tools.try_credential,
    "read_file": tools.read_file,
    "list_files": tools.list_files,
    "report_finding": tools.report_finding,
    "read_logs": tools.read_logs,
    "list_processes": tools.list_processes,
    "wait": tools.wait,
    "isolate_host": tools.isolate_host,
    "patch_service": tools.patch_service,
    "restore_host": tools.restore_host,
}


def _next_action(agent, turn: int, history: list, capped_turns: int,
                 alerts: list | None = None):
    """Ask one agent for its next move, sharing what it accepts.

    Passes the turn cap and the new sensor alerts only to agents whose
    next_action declares them, so scripted baselines stay untouched.
    """
    try:
        params = inspect.signature(agent.next_action).parameters
    except (TypeError, ValueError):
        return agent.next_action(turn, history)
    kwargs: dict = {}
    if "max_turns" in params:
        kwargs["max_turns"] = capped_turns
    if "alerts" in params:
        kwargs["alerts"] = alerts
    if kwargs:
        return agent.next_action(turn, history, **kwargs)
    return agent.next_action(turn, history)


def _agent_accepts_alerts(agent) -> bool:
    """Check whether an agent's next_action takes sensor alerts."""
    try:
        params = inspect.signature(agent.next_action).parameters
    except (TypeError, ValueError):
        return False
    return "alerts" in params


def _sensor_context() -> tuple:
    """Load the inventory context the sensor rules need.

    Returns (connections, crown_jewel_host) from the blue-visible
    inventory, defaulting to empty links and no jewel on any load
    trouble so exercises never hang on sensors.
    """
    try:
        from crucible import sandbox

        inventory = sandbox.blue_inventory()
    except Exception:
        return {}, None
    links = {}
    for host, entry in (inventory.get("hosts", {}) or {}).items():
        links[host] = set((entry or {}).get("connected_to", []) or [])
    return links, inventory.get("crown_jewel_host")


def _new_sensor_alerts(shown_keys: set) -> list:
    """Collect episode logs and return alerts not shown before.

    Runs the deterministic rules over the same episode-scoped logs blue
    could read, then filters to alerts whose key is new. Updates the
    shown set. Never raises: unreadable logs mean no alerts.
    """
    try:
        logs = sensor_rules.collect_episode_logs()
    except Exception:
        return []
    try:
        links, jewel = _sensor_context()
        current = sensor_rules.build_alerts(logs, connections=links,
                                            crown_jewel_host=jewel)
    except Exception:
        return []
    fresh = [alert for alert in current
             if sensor_rules.alert_key(alert) not in shown_keys]
    for alert in fresh:
        shown_keys.add(sensor_rules.alert_key(alert))
    return fresh


def run_tool(tool_name: str, args: dict) -> dict:
    """Call one shared tool by name and always return a result dict."""
    func = _DISPATCH.get(tool_name)
    if func is None:
        return {"ok": False, "error": f"unknown tool {tool_name!r}"}
    try:
        return func(**(args or {}))
    except (ValueError, RuntimeError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}


def _compromised_hosts(event_list: list) -> set:
    found = set()
    for event in event_list:
        if event.get("actor") != "red":
            continue
        result = event.get("result", {}) or {}
        if event.get("tool") == "try_credential" and result.get("authenticated") is True:
            found.add(result.get("host") or (event.get("args", {}) or {}).get("host"))
        if event.get("tool") == "read_file" and result.get("ok") is True:
            path = result.get("path", "")
            if isinstance(path, str) and path.startswith("/secret/"):
                found.add(result.get("host") or (event.get("args", {}) or {}).get("host"))
    return {host for host in found if host}


def _secret_exfiltrated(event_list: list) -> bool:
    for event in event_list:
        if event.get("actor") != "red" or event.get("tool") != "read_file":
            continue
        result = event.get("result", {}) or {}
        path = result.get("path", "")
        if result.get("ok") is True and isinstance(path, str) and path.startswith("/secret/"):
            return True
    return False


def _blue_acted(event_list: list) -> bool:
    for event in event_list:
        if event.get("actor") != "blue":
            continue
        result = event.get("result", {}) or {}
        if result.get("ok") is not True:
            continue
        if event.get("tool") == "report_finding":
            return True
        if event.get("tool") in CONTAINMENT_TOOLS:
            return True
    return False


def _business_weights(override: dict | None = None) -> dict:
    """Return host business weights, preferring an explicit override."""
    if override is not None:
        merged = dict(DEFAULT_BUSINESS_WEIGHTS)
        for host, value in (override or {}).items():
            try:
                merged[host] = int(value)
            except (TypeError, ValueError):
                continue
        return merged
    try:
        from crucible import sandbox

        weights = (sandbox.load_scenario().get("business_weights", {}) or {})
        merged = dict(DEFAULT_BUSINESS_WEIGHTS)
        for host, value in weights.items():
            try:
                merged[host] = int(value)
            except (TypeError, ValueError):
                continue
        return merged
    except Exception:
        return dict(DEFAULT_BUSINESS_WEIGHTS)


def _downtime_cost(event_list: list, weights: dict) -> int:
    """Sum business weights for every turn each host stays down.

    A successful isolate_host or patch_service marks the host down, and a
    successful restore_host clears it. Each event adds the weights of the
    hosts down after that event, so the isolating turn itself counts.
    """
    down: set = set()
    total = 0
    for event in event_list or []:
        result = event.get("result", {}) or {}
        if result.get("ok") is True:
            tool = event.get("tool", "")
            args = event.get("args", {}) or {}
            host = args.get("host") or result.get("host")
            if tool == "isolate_host" and host:
                down.add(host)
            elif tool == "patch_service" and host:
                down.add(host)
            elif tool == "restore_host" and host:
                down.discard(host)
        for host in down:
            try:
                total += int(weights.get(host, 0) or 0)
            except (TypeError, ValueError):
                continue
    return total


def _events_for_downtime(event_list: list) -> list:
    """Return events up to the secret read, or the full list."""
    for index, event in enumerate(event_list or []):
        if event.get("actor") != "red" or event.get("tool") != "read_file":
            continue
        result = event.get("result", {}) or {}
        path = result.get("path", "")
        if result.get("ok") is True and isinstance(path, str) and path.startswith("/secret/"):
            return list(event_list[: index + 1])
    return list(event_list or [])


def _downtime_pct(downtime_cost: int, weights: dict, num_events: int) -> float:
    """Return downtime as a percent of maximum weighted uptime.

    Maximum is the sum of business weights times the number of events
    counted. Zero events or zero total weight yields 0.0.
    """
    try:
        total_weight = 0
        for host in weights or {}:
            try:
                total_weight += int(weights.get(host, 0) or 0)
            except (TypeError, ValueError):
                continue
    except (TypeError, AttributeError):
        total_weight = 0
    try:
        count = max(0, int(num_events))
    except (TypeError, ValueError):
        count = 0
    maximum = total_weight * count
    if maximum <= 0:
        return 0.0
    try:
        raw = float(downtime_cost) / maximum * 100.0
    except (TypeError, ValueError):
        return 0.0
    return round(min(100.0, max(0.0, raw)), 2)


def _red_signin_positions(scoring: list) -> list:
    """Return 1-based positions of red's successful sign-ins."""
    positions = []
    for index, event in enumerate(scoring, start=1):
        if event.get("actor") != "red" or event.get("tool") != "try_credential":
            continue
        if (event.get("result", {}) or {}).get("authenticated") is True:
            positions.append(index)
    return positions


def _hint_read_positions(scoring: list) -> list:
    """Return 1-based positions of red's privileged page reads.

    A hint read is a successful request to a privileged page, matched
    generically on the page word and a 2xx status, never on scenario
    paths or response bodies.
    """
    positions = []
    for index, event in enumerate(scoring, start=1):
        if event.get("actor") != "red" or event.get("tool") != "http_request":
            continue
        result = event.get("result", {}) or {}
        if result.get("ok") is not True:
            continue
        if not str(result.get("status", "")).startswith("2"):
            continue
        path = str((event.get("args", {}) or {}).get("path", "") or "")
        if "admin" in path.lower():
            positions.append(index)
    return positions


def _line_shows_red_success(line: str) -> bool:
    """Check whether a log line records a red success.

    Successful sign-ins, file reads, directory listings, and
    successful privileged page requests all come only from red's
    tools, so any such line is red-success evidence.
    """
    text = str(line or "")
    if "signin ok" in text or "file read" in text or "dir list" in text:
        return True
    lowered = text.lower()
    return "admin" in lowered and "status=2" in lowered


def _read_line_texts(scoring: list) -> dict:
    """Map log line ids blue read to their text, over all events."""
    texts: dict = {}
    for event in scoring or []:
        if event.get("actor") != "blue" or event.get("tool") != "read_logs":
            continue
        result = event.get("result", {}) or {}
        if result.get("ok") is not True:
            continue
        for row in result.get("lines", []) or []:
            ref = (row or {}).get("id")
            if ref and ref not in texts:
                texts[ref] = str((row or {}).get("line", ""))
    return texts


def _cites_red_success(event: dict, alert_map: dict, texts: dict) -> bool:
    """Check whether blue's evidence refs include a red-success line.

    Cited alert refs count when the alert fired on red-success
    evidence (any rule but the failed-guess burst). Cited log ids
    count when their text shows a red success.
    """
    refs = list((event or {}).get("evidence_refs", []) or [])
    for ref in refs:
        if sensor_rules.is_alert_ref(ref):
            entry = (alert_map or {}).get(ref)
            if entry is not None and sensor_rules.alert_shows_success(entry):
                return True
    for ref in sensor_rules.resolve_refs(refs, alert_map):
        if sensor_rules.is_alert_ref(ref):
            continue
        if _line_shows_red_success((texts or {}).get(ref, "")):
            return True
    return False


def _relevant_detection_delay(scoring: list) -> int | None:
    """Count events from red's first compromise step to blue's detection.

    The first compromise step is red's first successful sign-in or hint
    read, whichever comes first. Detection is blue's first successful
    report or alert-citing action whose evidence refs include a log
    line from a red success. None when either side never happens.
    """
    steps = _red_signin_positions(scoring) + _hint_read_positions(scoring)
    if not steps:
        return None
    first_step = min(steps)
    alert_map = sensor_rules.alert_map_from_events(scoring)
    texts = _read_line_texts(scoring)
    for index, event in enumerate(scoring, start=1):
        if event.get("actor") != "blue":
            continue
        if (event.get("result", {}) or {}).get("ok") is not True:
            continue
        refs = list(event.get("evidence_refs", []) or [])
        cites_alert = any(sensor_rules.is_alert_ref(ref) for ref in refs)
        if event.get("tool") != "report_finding" and not cites_alert:
            continue
        if _cites_red_success(event, alert_map, texts):
            return index - first_step
    return None


def score_events(event_list: list, business_weights: dict | None = None) -> dict:
    """Score an exercise from its events.

    Times are 1-based positions in the event list, since each agent
    move writes exactly one event. Detection is the first successful
    blue finding report. Detection delay counts events from red's first
    action to that finding. Containment is the first successful blue
    containment action at or after the first compromise, and stays None
    when no compromise occurred. Outcome is red_succeeded when the secret
    was read, blue_held when it was not read and blue acted, else
    stalemate. Downtime cost sums host business weights for every turn
    each host stays isolated or under patch, counting only up to the
    secret read when it occurs, else the exercise end. Downtime pct is
    that cost as a percent of maximum weighted uptime (weight sum times
    events counted). Blue score is 0 when the secret was read, else 100
    minus downtime pct, so a fully quiet exercise scores 100. Intrusion
    is true when red signed in anywhere; contained_after_intrusion is
    true when blue contained at or after the first such sign-in.
    Relevant detection delay counts events from red's first compromise
    step (first successful sign-in or hint read) to blue's first
    report or alert-citing action citing a red-success line. Auditor
    and system events never shift positions or downtime: detection,
    containment, and downtime count over red and blue events only,
    while the audit metrics summarize the auditor events separately.
    """
    scoring = [event for event in (event_list or [])
               if event.get("actor") not in ("auditor", "system")]
    compromised = _compromised_hosts(scoring)
    secret_read = _secret_exfiltrated(scoring)
    first_compromise_at = None
    for index, event in enumerate(scoring, start=1):
        if event.get("actor") != "red":
            continue
        result = event.get("result", {}) or {}
        if event.get("tool") == "try_credential" and result.get("authenticated") is True:
            first_compromise_at = index
            break
        if event.get("tool") == "read_file" and result.get("ok") is True:
            path = result.get("path", "")
            if isinstance(path, str) and path.startswith("/secret/"):
                first_compromise_at = index
                break

    first_red_at = None
    for index, event in enumerate(scoring, start=1):
        if event.get("actor") == "red":
            first_red_at = index
            break

    time_to_detect = None
    for index, event in enumerate(scoring, start=1):
        if event.get("actor") != "blue" or event.get("tool") != "report_finding":
            continue
        if (event.get("result", {}) or {}).get("ok") is True:
            time_to_detect = index
            break

    detection_delay = None
    if first_red_at is not None and time_to_detect is not None:
        detection_delay = time_to_detect - first_red_at

    time_to_contain = None
    if compromised and first_compromise_at is not None:
        for index, event in enumerate(scoring, start=1):
            if index < first_compromise_at:
                continue
            if event.get("actor") != "blue":
                continue
            if event.get("tool") not in CONTAINMENT_TOOLS:
                continue
            if (event.get("result", {}) or {}).get("ok") is True:
                time_to_contain = index
                break

    signin_positions = _red_signin_positions(scoring)
    intrusion = bool(signin_positions)
    contained_after_intrusion = False
    if intrusion:
        first_signin_at = min(signin_positions)
        for index, event in enumerate(scoring, start=1):
            if index < first_signin_at:
                continue
            if event.get("actor") != "blue":
                continue
            if event.get("tool") not in CONTAINMENT_TOOLS:
                continue
            if (event.get("result", {}) or {}).get("ok") is True:
                contained_after_intrusion = True
                break
    relevant_detection_delay = _relevant_detection_delay(scoring)

    if secret_read:
        outcome = "red_succeeded"
    elif _blue_acted(scoring):
        outcome = "blue_held"
    else:
        outcome = "stalemate"

    weights = _business_weights(business_weights)
    counted = _events_for_downtime(scoring)
    downtime_cost = _downtime_cost(counted, weights)
    downtime_pct = _downtime_pct(downtime_cost, weights, len(counted))
    if secret_read:
        blue_score = 0
    else:
        blue_score = round(max(0.0, BLUE_SCORE_MAX - downtime_pct), 2)

    audit = _audit_metrics(event_list)
    return {
        "sensors": _sensors_mode(event_list),
        "time_to_detect": time_to_detect,
        "detection_delay": detection_delay,
        "relevant_detection_delay": relevant_detection_delay,
        "time_to_contain": time_to_contain,
        "intrusion": intrusion,
        "contained_after_intrusion": contained_after_intrusion,
        "hosts_compromised": len(compromised),
        "compromised_hosts": sorted(compromised),
        "secret_exfiltrated": secret_read,
        "secret_read": secret_read,
        "outcome": outcome,
        "total_events": len(scoring),
        "downtime_cost": downtime_cost,
        "downtime_pct": downtime_pct,
        "blue_score": blue_score,
        "justified_pct": audit["justified_pct"],
        "unsupported_count": audit["unsupported_count"],
        "invalid_refs_count": audit["invalid_refs_count"],
        "mean_audit_score": audit["mean_audit_score"],
    }


def intrusion_conditional_summary(rows: list) -> dict:
    """Summarize intrusion-conditional numbers over episode rows.

    Each row carries intrusion, contained_after_intrusion, secret_read
    (or secret_exfiltrated), relevant_detection_delay, time_to_contain,
    and downtime_pct. Returns the intrusion count, the secret-read and
    containment rates among intruded episodes, the median relevant
    detection delay and time to contain among them, and the mean
    downtime among contained ones. Rates are None when no episode had
    an intrusion; medians skip episodes that never detected or
    contained; the downtime mean covers contained_after_intrusion
    episodes only.
    """
    total = len(rows or [])
    intruded = [row for row in rows or [] if (row or {}).get("intrusion") is True]
    count = len(intruded)
    if not count:
        return {
            "episodes": total,
            "intrusions": 0,
            "secret_read_rate": None,
            "containment_rate": None,
            "median_relevant_detection_delay": None,
            "median_time_to_contain": None,
            "mean_downtime_contained": None,
        }

    def _secret(row: dict) -> bool:
        if "secret_read" in row:
            return row.get("secret_read") is True
        return row.get("secret_exfiltrated") is True

    secret_hits = sum(1 for row in intruded if _secret(row or {}))
    contained = [row for row in intruded
                 if (row or {}).get("contained_after_intrusion") is True]
    delays = [(row or {}).get("relevant_detection_delay") for row in intruded]
    delays = [value for value in delays if isinstance(value, (int, float))]
    contains = [(row or {}).get("time_to_contain") for row in intruded]
    contains = [value for value in contains if isinstance(value, (int, float))]
    downtimes = [(row or {}).get("downtime_pct") for row in contained]
    downtimes = [value for value in downtimes if isinstance(value, (int, float))]
    return {
        "episodes": total,
        "intrusions": count,
        "secret_read_rate": round(secret_hits / count, 3),
        "containment_rate": round(len(contained) / count, 3),
        "median_relevant_detection_delay": (statistics.median(delays)
                                            if delays else None),
        "median_time_to_contain": (statistics.median(contains)
                                  if contains else None),
        "mean_downtime_contained": (round(sum(downtimes) / len(downtimes), 2)
                                   if downtimes else None),
    }


def format_intrusion_lines(summary: dict) -> list:
    """Render intrusion-conditional summary lines for final tables.

    Shared by batch, campaign, and bench so every final table reports
    the same intrusion-conditional numbers in the same words.
    """
    summary = summary or {}
    total = summary.get("episodes", 0)
    count = summary.get("intrusions", 0)
    lines = [f"Episodes with intrusion: {count}/{total}"]
    secret_rate = summary.get("secret_read_rate")
    contain_rate = summary.get("containment_rate")
    if secret_rate is None:
        lines.append("Secret-read rate given intrusion: no intrusions")
    else:
        lines.append(f"Secret-read rate given intrusion: {secret_rate * 100:.1f}%")
    if contain_rate is None:
        lines.append("Containment rate given intrusion: no intrusions")
    else:
        lines.append(f"Containment rate given intrusion: {contain_rate * 100:.1f}%")
    delay = summary.get("median_relevant_detection_delay")
    if delay is None:
        lines.append("Median relevant detection delay given intrusion: no detections")
    else:
        lines.append(f"Median relevant detection delay given intrusion: {delay} events")
    contain_at = summary.get("median_time_to_contain")
    if contain_at is None:
        lines.append("Median time to contain given intrusion: no containment")
    else:
        lines.append(f"Median time to contain given intrusion: {contain_at} events")
    downtime = summary.get("mean_downtime_contained")
    if downtime is None:
        lines.append("Mean downtime among contained: no contained episodes")
    else:
        lines.append(f"Mean downtime among contained: {downtime:.2f}%")
    return lines


def _sensors_mode(event_list: list) -> str:
    """Read the sensors flag from the episode's first event.

    The clean-start check records on or off in its args. Logs written
    before sensors existed carry no flag and read as off, since no
    alerts were shown then.
    """
    for event in event_list or []:
        if event.get("actor") == "system" and event.get("tool") == "clean_start_check":
            mode = (event.get("args", {}) or {}).get("sensors", "off")
            return mode if mode in ("on", "off") else "off"
    return "off"


def _audit_metrics(event_list: list) -> dict:
    """Aggregate auditor events into the four summary metrics.

    Reads verdict, score, refs_valid, and cost from each auditor event
    result. Logs without audits report None pct and score with zero
    counts, so every score carries the same keys.
    """
    audits = []
    for event in event_list or []:
        if event.get("actor") != "auditor":
            continue
        result = event.get("result", {}) or {}
        audits.append(
            {
                "verdict": result.get("verdict"),
                "score": result.get("score"),
                "refs_valid": result.get("refs_valid"),
                "estimated_cost_usd": result.get("estimated_cost_usd", 0.0),
            }
        )
    return summarize_audits(audits)


def _log_audit(writer, audit: dict) -> dict:
    """Append one audit record as an actor=auditor event."""
    return writer.log(
        actor="auditor",
        tool="audit",
        args={"target_event_id": audit.get("target_event_id", "")},
        result=audit_result(audit),
        reasoning_summary=audit_reasoning(audit),
        evidence_refs=list(audit.get("evidence_checked", []) or []),
    )


def run_exercise(
    red_agent=None,
    blue_agent=None,
    max_turns: int = MAX_TURNS,
    run_path=None,
    exercise_id: str | None = None,
    audit: str = "off",
    auditor_client=None,
    auditor_use_cache: bool = True,
    sensors: str = "on",
    seed: str | None = None,
) -> dict:
    """Run one full exercise with alternating red and blue moves.

    Each turn gives one agent a single tool call, starting with red.
    Agents that return None sit out their turn. The loop ends as soon as
    the secret is read, when both agents are finished, or when max_turns
    moves have been attempted. Turns are capped at forty (20 moves per
    team) so model exercises stay bounded. Agent errors are logged as
    events and the loop moves on, so an exercise never hangs on one bad
    turn. Before move 1 the range is restored, log scope starts, and a
    clean-start check is recorded as the first (system) event; agents
    never see system events and scoring skips them like auditor events.
    That first event records the sensors mode (and seed when given) in
    its args so ablations stay reproducible from the log.

    Audit modes: off skips judging, end audits every blue action after
    the loop, live audits each blue action right after it is logged.
    Agents never see auditor events; scoring ignores them for positions
    and downtime but summarizes them as audit metrics. The result adds
    an audit_summary with the mode, the metrics, and the audit cost.

    Sensors mode on (the default) shows blue the new sensor alerts each
    turn; off leaves blue on logs alone. Blue agents that accept alerts
    get only the alerts fired since their last move, and the shown
    alerts are attached to that turn's blue event for the audit trail.
    """
    if audit not in AUDIT_MODES:
        raise ValueError(f"audit must be one of {', '.join(AUDIT_MODES)}: {audit!r}")
    if sensors not in ("on", "off"):
        raise ValueError(f"sensors must be 'on' or 'off': {sensors!r}")
    red = red_agent if red_agent is not None else ScriptedRed()
    blue = blue_agent if blue_agent is not None else ScriptedBlue()
    tools.reset_state()
    check = tools.begin_episode()
    writer = events.EventWriter(run_path if run_path else events.new_run_path(exercise_id))
    first_args: dict = {"sensors": sensors}
    if seed is not None:
        first_args["seed"] = str(seed)
    writer.log(
        actor="system",
        tool="clean_start_check",
        args=first_args,
        result=check,
        reasoning_summary=(
            f"Clean-start check passed on attempt {check.get('attempts', 1)}; "
            "log scope covers this exercise only."
        ),
        evidence_refs=[],
    )
    audit_weights = _business_weights()
    shown_alert_keys: set = set()
    blue_takes_alerts = sensors == "on" and _agent_accepts_alerts(blue)

    turns_used = 0
    idle_rounds = 0
    capped_turns = max(1, min(int(max_turns), MAX_TURNS))
    for slot in range(capped_turns):
        agent = red if slot % 2 == 0 else blue
        visible = [event for event in writer.events
                   if event.get("actor") not in ("auditor", "system")]
        fresh_alerts = None
        if agent is blue and blue_takes_alerts:
            fresh_alerts = _new_sensor_alerts(shown_alert_keys)
        try:
            action = _next_action(agent, slot + 1, visible, capped_turns,
                                  alerts=fresh_alerts)
        except Exception as exc:
            idle_rounds = 0
            turns_used += 1
            writer.log(
                actor=getattr(agent, "actor", "unknown"),
                tool="agent_error",
                args={},
                result={"ok": False, "error": str(exc)[:300]},
                reasoning_summary=f"Agent turn failed ({exc}), moving on.",
                evidence_refs=[],
            )
            continue
        if action is None:
            idle_rounds += 1
            if idle_rounds >= 2:
                break
            continue
        idle_rounds = 0
        turns_used += 1
        result = run_tool(action.get("tool", ""), action.get("args", {}))
        logged = writer.log(
            actor=getattr(agent, "actor", "unknown"),
            tool=action.get("tool", ""),
            args=action.get("args", {}),
            result=result,
            reasoning_summary=action.get("reasoning", ""),
            evidence_refs=action.get("evidence_refs", []),
            sensor_alerts=fresh_alerts or None,
        )
        if (
            audit == "live"
            and logged.get("actor") == "blue"
            and logged.get("tool") not in AUDIT_SKIP_TOOLS
        ):
            priors = [event for event in writer.events[:-1]
                      if event.get("actor") != "auditor"]
            blue_no = sum(1 for event in priors
                          if event.get("actor") == "blue"
                          and event.get("tool") not in AUDIT_SKIP_TOOLS) + 1
            _log_audit(writer, audit_blue_event(
                logged, priors, exercise_id=exercise_id,
                client=auditor_client, use_cache=auditor_use_cache,
                weights=audit_weights,
                position={"blue_action_no": blue_no,
                          "events_so_far": len(writer.events)}))
        if _secret_exfiltrated(writer.events):
            break

    if audit == "end":
        for record in audit_blue_events(
            writer.events, exercise_id=exercise_id, client=auditor_client,
            use_cache=auditor_use_cache, weights=audit_weights,
        ):
            _log_audit(writer, record)

    score = score_events(writer.events)
    score["turns_used"] = turns_used
    summary = summarize_audits(
        [
            {
                "verdict": (event.get("result", {}) or {}).get("verdict"),
                "score": (event.get("result", {}) or {}).get("score"),
                "refs_valid": (event.get("result", {}) or {}).get("refs_valid"),
                "estimated_cost_usd": (event.get("result", {}) or {}).get(
                    "estimated_cost_usd", 0.0),
            }
            for event in writer.events
            if event.get("actor") == "auditor"
        ]
    )
    summary["mode"] = audit
    return {"events": writer.events, "score": score, "run_path": str(writer.path),
            "audit_summary": summary}
