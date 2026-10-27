"""Hermetic tests for the red/blue information boundary.

Red copied account names out of blue's report text through the shared
recent-moves prompt section. Both prompts now render only the owning
team's own moves, results, and reasoning.
"""

from crucible.agents.blue import _build_messages as blue_build_messages
from crucible.agents.red import _build_messages as red_build_messages


def _event(index, actor, tool, args, result, reasoning):
    return {
        "id": f"evt-{index:04d}", "timestamp": "2026-01-01T00:00:00+00:00",
        "actor": actor, "tool": tool, "args": args, "result": result,
        "reasoning_summary": reasoning, "evidence_refs": [],
    }


def _contaminated_history():
    return [
        _event(1, "red", "scan_host", {"host": "web"},
               {"ok": True, "host": "web"}, "RED-REASON-MARKER"),
        _event(2, "blue", "read_logs", {"host": "app"},
               {"ok": True, "host": "app", "lines": [
                   {"id": "app:0", "line": "signin fail user=admin from=x"}]},
               "BLUE-REASON-MARKER"),
        _event(3, "red", "report_finding", {"text": "RED-REPORT-MARKER"},
               {"ok": True, "id": "finding-1"}, "RED-REASON-MARKER"),
        _event(4, "blue", "report_finding",
               {"text": "sign-in as svc-report on app, BLUE-REPORT-MARKER"},
               {"ok": True, "id": "finding-2"}, "BLUE-REASON-MARKER"),
        _event(5, "auditor", "audit", {"target_event_id": "evt-0004"},
               {"ok": True, "verdict": "justified",
                "rationale": "AUDIT-MARKER"}, "AUDIT-MARKER"),
    ]


def _user_text(messages):
    return messages[1]["content"]


def test_red_prompt_excludes_blue_text_auditor_and_playbook():
    history = _contaminated_history()
    playbook = [{"rule": "PLAYBOOK-MARKER never contain", "episode": "x"}]
    text = _user_text(red_build_messages(6, history))
    system = red_build_messages(6, history)[0]["content"]
    for forbidden in ("svc-report", "BLUE-REPORT-MARKER", "BLUE-REASON-MARKER",
                      "AUDIT-MARKER", "PLAYBOOK-MARKER"):
        assert forbidden not in text, forbidden
        assert forbidden not in system, forbidden


def test_red_prompt_keeps_own_moves_and_reasoning():
    text = _user_text(red_build_messages(6, _contaminated_history()))
    assert "RED-REASON-MARKER" in text
    assert "RED-REPORT-MARKER" in text
    assert "scan_host" in text


def test_blue_prompt_excludes_red_reasoning_and_reports():
    history = _contaminated_history()
    text = _user_text(blue_build_messages(6, history))
    for forbidden in ("RED-REASON-MARKER", "RED-REPORT-MARKER"):
        assert forbidden not in text, forbidden


def test_blue_prompt_keeps_logs_own_moves_and_playbook():
    history = [
        _event(1, "red", "scan_host", {"host": "web"},
               {"ok": True, "host": "web"}, "RED-REASON-MARKER"),
        _event(2, "blue", "report_finding",
               {"text": "sign-in as svc-report on app, BLUE-REPORT-MARKER"},
               {"ok": True, "id": "finding-2"}, "BLUE-REASON-MARKER"),
        _event(3, "blue", "read_logs", {"host": "app"},
               {"ok": True, "host": "app", "lines": [
                   {"id": "app:0", "line": "signin fail user=admin from=x"}]},
               "BLUE-REASON-MARKER"),
    ]
    playbook = [{"rule": "Contain the host the evidence points at.",
                 "episode": "camp-1"}]
    text = _user_text(blue_build_messages(4, history, 40, playbook))
    assert "signin fail user=admin" in text
    assert "svc-report" in text  # blue's own report text stays
    assert "BLUE-REPORT-MARKER" in text
    assert "Contain the host the evidence points at." in text


def test_blue_prompt_states_log_scope_and_moves_so_far():
    text = _user_text(blue_build_messages(6, _contaminated_history()))
    assert "Log lines cover only this exercise." in text
    assert "Moves so far this exercise: 5." in text
    first = _user_text(blue_build_messages(2, []))
    assert "Moves so far this exercise: 1." in first


def test_blue_down_hosts_ignore_other_actors():
    history = [
        _event(1, "red", "isolate_host", {"host": "db"},
               {"ok": True, "host": "db"}, "RED-REASON-MARKER"),
    ]
    text = _user_text(blue_build_messages(2, history))
    assert "Isolated hosts: none." in text
