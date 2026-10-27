"""Turn loop and scoring for validation exercises.

Runs red team and blue team agents in alternation up to a max turn
count. Each agent move calls one shared tool, and each call is logged
as an event to jsonl. Scoring reads the event list only, so results
stay reproducible from the saved log.
"""

from __future__ import annotations

import inspect

from crucible import events, tools
from crucible.agents.scripted_blue import ScriptedBlue
from crucible.agents.scripted_red import ScriptedRed

CONTAINMENT_TOOLS = ("isolate_host", "patch_service", "restore_host", "block_ip")
MAX_TURNS = 40

DEFAULT_BUSINESS_WEIGHTS = {"web": 3, "app": 3, "db": 5, "workstation": 1}
SECRET_PROTECTED_POINTS = 40

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
    "block_ip": tools.block_ip,
    "isolate_host": tools.isolate_host,
    "patch_service": tools.patch_service,
    "restore_host": tools.restore_host,
}


def _next_action(agent, turn: int, history: list, capped_turns: int):
    """Ask one agent for its next move, sharing the turn cap it accepts."""
    try:
        params = inspect.signature(agent.next_action).parameters
    except (TypeError, ValueError):
        return agent.next_action(turn, history)
    if "max_turns" in params:
        return agent.next_action(turn, history, capped_turns)
    return agent.next_action(turn, history)


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
    secret read when it occurs, else the exercise end. Blue score is the
    protected secret value minus downtime cost, floored at zero, and zero
    when the secret was read.
    """
    compromised = _compromised_hosts(event_list)
    secret_read = _secret_exfiltrated(event_list)
    first_compromise_at = None
    for index, event in enumerate(event_list, start=1):
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
    for index, event in enumerate(event_list, start=1):
        if event.get("actor") == "red":
            first_red_at = index
            break

    time_to_detect = None
    for index, event in enumerate(event_list, start=1):
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
        for index, event in enumerate(event_list, start=1):
            if index < first_compromise_at:
                continue
            if event.get("actor") != "blue":
                continue
            if event.get("tool") not in CONTAINMENT_TOOLS:
                continue
            if (event.get("result", {}) or {}).get("ok") is True:
                time_to_contain = index
                break

    if secret_read:
        outcome = "red_succeeded"
    elif _blue_acted(event_list):
        outcome = "blue_held"
    else:
        outcome = "stalemate"

    weights = _business_weights(business_weights)
    downtime_cost = _downtime_cost(_events_for_downtime(event_list), weights)
    if secret_read:
        blue_score = 0
    else:
        blue_score = max(0, SECRET_PROTECTED_POINTS - downtime_cost)

    return {
        "time_to_detect": time_to_detect,
        "detection_delay": detection_delay,
        "time_to_contain": time_to_contain,
        "hosts_compromised": len(compromised),
        "compromised_hosts": sorted(compromised),
        "secret_exfiltrated": secret_read,
        "outcome": outcome,
        "total_events": len(event_list),
        "downtime_cost": downtime_cost,
        "blue_score": blue_score,
    }


def run_exercise(
    red_agent=None,
    blue_agent=None,
    max_turns: int = MAX_TURNS,
    run_path=None,
    exercise_id: str | None = None,
) -> dict:
    """Run one full exercise with alternating red and blue moves.

    Each turn gives one agent a single tool call, starting with red.
    Agents that return None sit out their turn. The loop ends as soon as
    the secret is read, when both agents are finished, or when max_turns
    moves have been attempted. Turns are capped at forty (20 moves per
    team) so model exercises stay bounded. Agent errors are logged as
    events and the loop moves on, so an exercise never hangs on one bad
    turn.
    """
    red = red_agent if red_agent is not None else ScriptedRed()
    blue = blue_agent if blue_agent is not None else ScriptedBlue()
    tools.reset_state()
    writer = events.EventWriter(run_path if run_path else events.new_run_path(exercise_id))

    turns_used = 0
    idle_rounds = 0
    capped_turns = max(1, min(int(max_turns), MAX_TURNS))
    for slot in range(capped_turns):
        agent = red if slot % 2 == 0 else blue
        try:
            action = _next_action(agent, slot + 1, writer.events, capped_turns)
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
        writer.log(
            actor=getattr(agent, "actor", "unknown"),
            tool=action.get("tool", ""),
            args=action.get("args", {}),
            result=result,
            reasoning_summary=action.get("reasoning", ""),
            evidence_refs=action.get("evidence_refs", []),
        )
        if _secret_exfiltrated(writer.events):
            break

    score = score_events(writer.events)
    score["turns_used"] = turns_used
    return {"events": writer.events, "score": score, "run_path": str(writer.path)}
