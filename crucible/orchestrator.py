"""Turn loop and scoring for validation exercises.

Runs red team and blue team agents in alternation up to a max turn
count. Each agent move calls one shared tool, and each call is logged
as an event to jsonl. Scoring reads the event list only, so results
stay reproducible from the saved log.
"""

from __future__ import annotations

from crucible import events, tools
from crucible.agents.scripted_blue import ScriptedBlue
from crucible.agents.scripted_red import ScriptedRed

CONTAINMENT_TOOLS = ("isolate_host", "patch_service", "restore_host", "block_ip")
MAX_TURNS = 20

_DISPATCH = {
    "scan_host": tools.scan_host,
    "http_request": tools.http_request,
    "try_credential": tools.try_credential,
    "read_file": tools.read_file,
    "report_finding": tools.report_finding,
    "read_logs": tools.read_logs,
    "list_processes": tools.list_processes,
    "wait": tools.wait,
    "block_ip": tools.block_ip,
    "isolate_host": tools.isolate_host,
    "patch_service": tools.patch_service,
    "restore_host": tools.restore_host,
}


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


def score_events(event_list: list) -> dict:
    """Score an exercise from its events.

    Times are 1-based positions in the event list, since each agent
    move writes exactly one event. Detection is the first successful
    blue finding report. Containment is the first successful blue
    containment action at or after the first compromise. Both stay
    None when the exercise never reaches them.
    """
    compromised = _compromised_hosts(event_list)
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

    time_to_detect = None
    for index, event in enumerate(event_list, start=1):
        if event.get("actor") != "blue" or event.get("tool") != "report_finding":
            continue
        if (event.get("result", {}) or {}).get("ok") is True:
            time_to_detect = index
            break

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

    return {
        "time_to_detect": time_to_detect,
        "time_to_contain": time_to_contain,
        "hosts_compromised": len(compromised),
        "compromised_hosts": sorted(compromised),
        "secret_exfiltrated": _secret_exfiltrated(event_list),
        "total_events": len(event_list),
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
    Agents that return None sit out their turn. The loop ends when
    both agents are finished or max_turns moves have been attempted.
    Turns are capped at twenty so model exercises stay bounded.
    Agent errors are logged as events and the loop moves on, so an
    exercise never hangs on one bad turn.
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
            action = agent.next_action(slot + 1, writer.events)
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

    score = score_events(writer.events)
    score["turns_used"] = turns_used
    return {"events": writer.events, "score": score, "run_path": str(writer.path)}
