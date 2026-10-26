"""Hermetic tests for events, scoring, and the turn loop.

All sandbox backends stay stubbed here. Live coverage of one full
scripted exercise sits in tests/test_exercise_live.py.
"""

import json

import pytest

from crucible import events, orchestrator, sandbox
from crucible.agents import ScriptedBlue, ScriptedRed


def _event(actor, tool, result, index=1):
    return {
        "id": f"evt-{index:04d}",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "actor": actor,
        "tool": tool,
        "args": {},
        "result": result,
        "reasoning_summary": "note",
        "evidence_refs": [],
    }


def test_event_writer_numbers_and_persists(tmp_path):
    writer = events.EventWriter(tmp_path / "run.jsonl")
    first = writer.log(actor="red", tool="scan_host", args={"host": "web"},
                       result={"ok": True}, reasoning_summary="probe")
    second = writer.log(actor="blue", tool="read_logs", args={"host": "web"},
                        result={"ok": True}, reasoning_summary="inspect")
    assert (first["id"], second["id"]) == ("evt-0001", "evt-0002")
    lines = (tmp_path / "run.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    loaded = [json.loads(line) for line in lines]
    assert loaded == writer.events
    for entry in loaded:
        assert set(events.REQUIRED_FIELDS) <= set(entry)


def test_log_replaces_em_and_en_dashes(tmp_path):
    writer = events.EventWriter(tmp_path / "dashes.jsonl")
    logged = writer.log(actor="red", tool="scan_host", args={"host": "web"},
                        result={"ok": True},
                        reasoning_summary="Recon done — now probing – fast.")
    assert "—" not in logged["reasoning_summary"]
    assert "–" not in logged["reasoning_summary"]
    assert logged["reasoning_summary"] == "Recon done, now probing, fast."
    stored = events.read_events(tmp_path / "dashes.jsonl")[0]
    assert stored["reasoning_summary"] == logged["reasoning_summary"]


def test_append_rejects_incomplete_event(tmp_path):
    with pytest.raises(ValueError):
        events.append_event(tmp_path / "bad.jsonl", {"id": "evt-1"})


def test_score_marks_detection_and_containment():
    history = [
        _event("red", "scan_host", {"ok": True}, 1),
        _event("blue", "read_logs", {"ok": True}, 2),
        _event("red", "try_credential",
               {"ok": True, "host": "db", "authenticated": True}, 3),
        _event("blue", "report_finding", {"ok": True, "id": "finding-1"}, 4),
        _event("red", "read_file",
               {"ok": True, "host": "db", "path": "/secret/exercise_secret.txt"}, 5),
        _event("blue", "isolate_host", {"ok": True, "host": "db"}, 6),
    ]
    score = orchestrator.score_events(history)
    assert score["time_to_detect"] == 4
    assert score["time_to_contain"] == 6
    assert score["hosts_compromised"] == 1
    assert score["compromised_hosts"] == ["db"]
    assert score["secret_exfiltrated"] is True


def test_score_stays_empty_without_progress():
    history = [
        _event("red", "scan_host", {"ok": True}, 1),
        _event("blue", "read_logs", {"ok": True}, 2),
    ]
    score = orchestrator.score_events(history)
    assert score["time_to_detect"] is None
    assert score["time_to_contain"] is None
    assert score["hosts_compromised"] == 0
    assert score["secret_exfiltrated"] is False


def test_score_red_succeeded_when_secret_read():
    history = [
        _event("red", "scan_host", {"ok": True}, 1),
        _event("blue", "read_logs", {"ok": True}, 2),
        _event("red", "try_credential",
               {"ok": True, "host": "db", "authenticated": True}, 3),
        _event("blue", "report_finding", {"ok": True, "id": "finding-1"}, 4),
        _event("red", "read_file",
               {"ok": True, "host": "db", "path": "/secret/exercise_secret.txt"}, 5),
        _event("blue", "isolate_host", {"ok": True, "host": "db"}, 6),
    ]
    score = orchestrator.score_events(history)
    assert score["outcome"] == "red_succeeded"
    assert score["secret_exfiltrated"] is True
    assert score["detection_delay"] == 3
    assert score["time_to_contain"] == 6


def test_score_blue_held_without_secret_read():
    history = [
        _event("red", "scan_host", {"ok": True}, 1),
        _event("blue", "read_logs", {"ok": True}, 2),
        _event("red", "http_request", {"ok": True}, 3),
        _event("blue", "report_finding", {"ok": True, "id": "finding-1"}, 4),
    ]
    score = orchestrator.score_events(history)
    assert score["outcome"] == "blue_held"
    assert score["secret_exfiltrated"] is False
    assert score["time_to_detect"] == 4
    assert score["detection_delay"] == 3
    assert score["time_to_contain"] is None


def test_score_contain_stays_null_without_compromise():
    history = [
        _event("red", "scan_host", {"ok": True}, 1),
        _event("blue", "isolate_host", {"ok": True, "host": "web"}, 2),
    ]
    score = orchestrator.score_events(history)
    assert score["hosts_compromised"] == 0
    assert score["time_to_contain"] is None
    assert score["outcome"] == "blue_held"


def test_score_stalemate_when_blue_idle():
    history = [
        _event("red", "scan_host", {"ok": True}, 1),
        _event("blue", "read_logs", {"ok": True}, 2),
        _event("red", "scan_host", {"ok": True}, 3),
        _event("blue", "wait", {"ok": True, "waited": True}, 4),
    ]
    score = orchestrator.score_events(history)
    assert score["outcome"] == "stalemate"
    assert score["secret_exfiltrated"] is False
    assert score["detection_delay"] is None
    assert score["time_to_contain"] is None


def test_run_tool_maps_unknown_and_bad_calls():
    assert orchestrator.run_tool("no-such-tool", {})["ok"] is False
    out = orchestrator.run_tool("scan_host", {"host": "off-range.example"})
    assert out["ok"] is False


def test_turn_loop_alternates_and_writes_jsonl(tmp_path, monkeypatch):
    calls = []

    def fake_run_tool(name, args):
        calls.append(name)
        if name == "try_credential":
            return {"ok": True, "host": "db", "authenticated": True}
        if name == "read_file":
            return {"ok": True, "host": "db", "path": args["path"]}
        if name == "report_finding":
            return {"ok": True, "id": f"finding-{len(calls)}"}
        if name in ("isolate_host", "patch_service"):
            return {"ok": True}
        if name == "read_logs":
            return {"ok": True, "host": "web",
                    "lines": [{"id": "web:0", "line": "request from=1.2.3.4 /admin"}]}
        return {"ok": True}

    monkeypatch.setattr(orchestrator, "run_tool", fake_run_tool)
    run_path = tmp_path / "exercise.jsonl"
    result = orchestrator.run_exercise(ScriptedRed(), ScriptedBlue(),
                                       max_turns=10, run_path=run_path)
    actors = [entry["actor"] for entry in result["events"]]
    assert actors == ["red", "blue"] * 5
    assert result["score"]["secret_exfiltrated"] is True
    assert result["score"]["time_to_detect"] is not None
    assert result["score"]["time_to_contain"] is not None
    assert run_path.exists()
    assert events.read_events(run_path) == result["events"]
    blue_report = next(entry for entry in result["events"]
                       if entry["actor"] == "blue" and entry["tool"] == "report_finding")
    assert blue_report["evidence_refs"] == ["web:0"]


def test_scripted_agents_make_zero_model_calls(monkeypatch):
    import crucible.llm as llm

    def _boom(*args, **kwargs):
        raise AssertionError("scripted exercise must not call the model client")

    monkeypatch.setattr(llm, "chat", _boom)
    red, blue = ScriptedRed(), ScriptedBlue()
    history = []
    for turn in range(1, 11):
        agent = red if turn % 2 == 1 else blue
        action = agent.next_action(turn, history)
        assert action is None or "tool" in action


def test_start_raises_when_hosts_stay_down(monkeypatch):
    monkeypatch.setattr(sandbox, "_compose", lambda *a, **k: "")
    monkeypatch.setattr(sandbox, "status",
                        lambda: {"web": True, "db": False, "workstation": False})
    with pytest.raises(sandbox.SandboxError) as excinfo:
        sandbox.start()
    assert "db" in str(excinfo.value)
    assert "workstation" in str(excinfo.value)


def test_start_returns_state_when_all_up(monkeypatch):
    monkeypatch.setattr(sandbox, "_compose", lambda *a, **k: "")
    monkeypatch.setattr(sandbox, "status",
                        lambda: {"web": True, "db": True, "workstation": True})
    assert sandbox.start() == {"web": True, "db": True, "workstation": True}


def _evt_host(actor, tool, host, ok=True, index=1, extra=None):
    result = {"ok": ok, "host": host}
    if extra:
        result.update(extra)
    return {
        "id": f"evt-{index:04d}",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "actor": actor,
        "tool": tool,
        "args": {"host": host} if host else {},
        "result": result,
        "reasoning_summary": "note",
        "evidence_refs": [],
    }


def test_score_downtime_counts_isolated_turns():
    history = [
        _event("red", "scan_host", {"ok": True}, 1),
        _evt_host("blue", "isolate_host", "web", True, 2),
        _event("red", "scan_host", {"ok": True}, 3),
        _event("blue", "read_logs", {"ok": True}, 4),
    ]
    score = orchestrator.score_events(history)
    assert score["downtime_cost"] == 9
    assert score["blue_score"] == 31
    assert score["outcome"] == "blue_held"


def test_score_patch_counts_and_restore_clears():
    history = [
        _event("red", "scan_host", {"ok": True}, 1),
        _evt_host("blue", "isolate_host", "db", True, 2),
        _event("red", "scan_host", {"ok": True}, 3),
        _evt_host("blue", "restore_host", "db", True, 4),
        _event("red", "scan_host", {"ok": True}, 5),
    ]
    score = orchestrator.score_events(history)
    assert score["downtime_cost"] == 10
    assert score["blue_score"] == 30


def test_score_patch_service_counts_downtime():
    history = [
        _event("red", "scan_host", {"ok": True}, 1),
        _evt_host("blue", "patch_service", "db", True, 2),
        _event("red", "scan_host", {"ok": True}, 3),
    ]
    score = orchestrator.score_events(history)
    assert score["downtime_cost"] == 10
    assert score["blue_score"] == 30


def test_score_downtime_ignores_failed_actions():
    history = [
        _event("red", "scan_host", {"ok": True}, 1),
        _evt_host("blue", "isolate_host", "db", False, 2),
        _event("red", "scan_host", {"ok": True}, 3),
    ]
    score = orchestrator.score_events(history)
    assert score["downtime_cost"] == 0
    assert score["blue_score"] == 40


def test_score_blue_score_zero_when_secret_read():
    history = [
        _event("red", "scan_host", {"ok": True}, 1),
        _evt_host("blue", "isolate_host", "db", True, 2),
        _event("red", "read_file",
               {"ok": True, "host": "db", "path": "/secret/exercise_secret.txt"}, 3),
    ]
    score = orchestrator.score_events(history)
    assert score["secret_exfiltrated"] is True
    assert score["blue_score"] == 0
    assert score["downtime_cost"] == 10


def test_score_blue_score_floors_at_zero():
    history = [_event("red", "scan_host", {"ok": True}, 1)]
    history.append(_evt_host("blue", "isolate_host", "db", True, 2))
    for index in range(3, 10):
        history.append(_event("red", "scan_host", {"ok": True}, index))
    score = orchestrator.score_events(history)
    assert score["downtime_cost"] == 5 * 8
    assert score["blue_score"] == 0


def test_score_accepts_weight_override():
    history = [
        _event("red", "scan_host", {"ok": True}, 1),
        _evt_host("blue", "isolate_host", "web", True, 2),
    ]
    score = orchestrator.score_events(history, business_weights={"web": 7})
    assert score["downtime_cost"] == 7
    assert score["blue_score"] == 33


def test_business_weights_match_scenario():
    scenario = sandbox.load_scenario()
    assert scenario["business_weights"] == {"web": 3, "db": 5, "workstation": 1}
    assert orchestrator.DEFAULT_BUSINESS_WEIGHTS == {"web": 3, "db": 5, "workstation": 1}
    assert orchestrator.SECRET_PROTECTED_POINTS == 40
