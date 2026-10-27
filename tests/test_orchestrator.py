"""Hermetic tests for events, scoring, and the turn loop.

All sandbox backends stay stubbed here. Live coverage of one full
scripted exercise sits in tests/test_exercise_live.py.
"""

import json

import pytest

from crucible import events, orchestrator, sandbox, tools
from crucible.agents import ScriptedBlue, ScriptedRed


def _stub_begin_episode(monkeypatch):
    monkeypatch.setattr(tools, "begin_episode",
                        lambda: {"ok": True, "attempts": 1, "checks": {}})


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
    _stub_begin_episode(monkeypatch)
    run_path = tmp_path / "exercise.jsonl"
    result = orchestrator.run_exercise(ScriptedRed(), ScriptedBlue(),
                                       max_turns=20, run_path=run_path)
    actors = [entry["actor"] for entry in result["events"]]
    assert actors == ["system"] + ["red", "blue"] * 5 + ["red", "red"]
    assert len(result["events"]) == 13
    assert result["score"]["secret_exfiltrated"] is True
    assert result["score"]["time_to_detect"] is not None
    assert result["score"]["time_to_contain"] is not None
    assert result["score"]["turns_used"] == 12
    assert run_path.exists()
    assert events.read_events(run_path) == result["events"]
    blue_report = next(entry for entry in result["events"]
                       if entry["actor"] == "blue" and entry["tool"] == "report_finding")
    assert blue_report["evidence_refs"] == ["web:0"]


def test_run_stops_as_soon_as_secret_read(tmp_path, monkeypatch):
    calls = []

    def fake_run_tool(name, args):
        calls.append(name)
        if name == "read_file":
            return {"ok": True, "host": "db", "path": args["path"]}
        return {"ok": True}

    class _QuickRed:
        actor = "red"

        def next_action(self, turn, history):
            if turn > 1:
                return {"tool": "scan_host", "args": {"host": "web"},
                        "reasoning": "extra", "evidence_refs": []}
            return {"tool": "read_file",
                    "args": {"host": "db", "path": "/secret/exercise_secret.txt"},
                    "reasoning": "grab secret", "evidence_refs": []}

    class _QuickBlue:
        actor = "blue"

        def next_action(self, turn, history):
            return {"tool": "read_logs", "args": {"host": "web"},
                    "reasoning": "inspect", "evidence_refs": []}

    monkeypatch.setattr(orchestrator, "run_tool", fake_run_tool)
    _stub_begin_episode(monkeypatch)
    result = orchestrator.run_exercise(_QuickRed(), _QuickBlue(),
                                       max_turns=10,
                                       run_path=tmp_path / "quick.jsonl")
    assert len(result["events"]) == 2
    assert result["score"]["secret_exfiltrated"] is True
    assert result["score"]["turns_used"] == 1


def test_first_event_records_clean_start_check(tmp_path, monkeypatch):
    check = {"ok": True, "attempts": 1, "checks": {"ok": True}}
    monkeypatch.setattr(orchestrator, "run_tool",
                        lambda name, args: {"ok": True})
    monkeypatch.setattr(tools, "begin_episode", lambda: check)
    result = orchestrator.run_exercise(ScriptedRed(), ScriptedBlue(),
                                       max_turns=2,
                                       run_path=tmp_path / "checked.jsonl")
    first = result["events"][0]
    assert (first["id"], first["actor"], first["tool"]) == (
        "evt-0001", "system", "clean_start_check")
    assert first["result"] == check
    assert set(events.REQUIRED_FIELDS) <= set(first)


def test_system_events_never_shift_scoring():
    moves = [
        _event("red", "scan_host", {"ok": True}, 2),
        _event("blue", "report_finding", {"ok": True}, 3),
    ]
    plain = orchestrator.score_events(moves)
    with_system = orchestrator.score_events(
        [_event("system", "clean_start_check", {"ok": True}, 1)] + moves)
    assert with_system["time_to_detect"] == plain["time_to_detect"] == 2
    assert with_system["detection_delay"] == plain["detection_delay"] == 1
    assert with_system["total_events"] == plain["total_events"] == 2
    assert with_system["downtime_pct"] == plain["downtime_pct"] == 0.0


def test_scripted_red_follows_longer_chain():
    red = ScriptedRed()
    actions = [red.next_action(turn, []) for turn in range(1, 9)]
    assert [(action["tool"], action["args"].get("host")) for action in actions] == [
        ("scan_host", "web"),
        ("http_request", "web"),
        ("try_credential", "app"),
        ("read_file", "app"),
        ("try_credential", "db"),
        ("list_files", "db"),
        ("read_file", "db"),
        ("report_finding", None),
    ]
    assert actions[2]["args"]["user"] == "svc-report"
    assert actions[3]["args"]["path"] == "/srv/app/config.ini"
    assert actions[4]["args"]["user"] == "dbops"
    assert actions[6]["args"]["path"] == "/secret/exercise_secret.txt"
    assert red.next_action(9, []) is None


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
                        lambda: {"web": True, "app": False, "db": False,
                                 "workstation": False})
    with pytest.raises(sandbox.SandboxError) as excinfo:
        sandbox.start()
    assert "db" in str(excinfo.value)
    assert "workstation" in str(excinfo.value)


def test_start_returns_state_when_all_up(monkeypatch):
    monkeypatch.setattr(sandbox, "_compose", lambda *a, **k: "")
    monkeypatch.setattr(sandbox, "status",
                        lambda: {"web": True, "app": True, "db": True,
                                 "workstation": True})
    assert sandbox.start() == {"web": True, "app": True, "db": True,
                               "workstation": True}


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
    assert score["downtime_pct"] == 18.75
    assert score["blue_score"] == 81.25
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
    assert score["downtime_pct"] == 16.67
    assert score["blue_score"] == 83.33


def test_score_patch_service_counts_downtime():
    history = [
        _event("red", "scan_host", {"ok": True}, 1),
        _evt_host("blue", "patch_service", "db", True, 2),
        _event("red", "scan_host", {"ok": True}, 3),
    ]
    score = orchestrator.score_events(history)
    assert score["downtime_cost"] == 10
    assert score["downtime_pct"] == 27.78
    assert score["blue_score"] == 72.22


def test_score_downtime_ignores_failed_actions():
    history = [
        _event("red", "scan_host", {"ok": True}, 1),
        _evt_host("blue", "isolate_host", "db", False, 2),
        _event("red", "scan_host", {"ok": True}, 3),
    ]
    score = orchestrator.score_events(history)
    assert score["downtime_cost"] == 0
    assert score["downtime_pct"] == 0.0
    assert score["blue_score"] == 100.0


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
    assert score["downtime_pct"] == 27.78


def test_score_blue_score_scales_with_downtime_pct():
    history = [_event("red", "scan_host", {"ok": True}, 1)]
    history.append(_evt_host("blue", "isolate_host", "db", True, 2))
    for index in range(3, 10):
        history.append(_event("red", "scan_host", {"ok": True}, index))
    score = orchestrator.score_events(history)
    assert score["downtime_cost"] == 5 * 8
    assert score["downtime_pct"] == 37.04
    assert score["blue_score"] == 62.96


def test_score_blue_score_tracks_full_outage():
    history = [
        _evt_host("blue", "isolate_host", "web", True, 1),
        _evt_host("blue", "isolate_host", "app", True, 2),
        _evt_host("blue", "isolate_host", "db", True, 3),
        _evt_host("blue", "isolate_host", "workstation", True, 4),
    ]
    score = orchestrator.score_events(history)
    assert score["downtime_cost"] == 3 + 6 + 11 + 12
    assert score["downtime_pct"] == 66.67
    assert score["blue_score"] == 33.33
    full = [
        _evt_host("blue", "isolate_host", "web", True, 1),
        _evt_host("blue", "isolate_host", "app", True, 2),
        _evt_host("blue", "isolate_host", "db", True, 3),
        _evt_host("blue", "isolate_host", "workstation", True, 4),
        _event("red", "scan_host", {"ok": True}, 5),
        _event("red", "scan_host", {"ok": True}, 6),
    ]
    full_score = orchestrator.score_events(full)
    assert full_score["downtime_cost"] == 3 + 6 + 11 + 12 + 12 + 12
    assert full_score["downtime_pct"] == 77.78
    assert full_score["blue_score"] == 22.22


def test_score_stays_in_range_on_long_outage():
    history = []
    for host in ("web", "app", "db", "workstation"):
        history.append(_evt_host("blue", "isolate_host", host, True, len(history) + 1))
    for index in range(5, 9):
        history.append(_event("red", "scan_host", {"ok": True}, index))
    score = orchestrator.score_events(history)
    assert 0 <= score["blue_score"] <= 100
    assert 0 <= score["downtime_pct"] <= 100
    assert score["blue_score"] == round(100 - score["downtime_pct"], 2)


def test_score_quiet_exercise_scores_hundred():
    history = [
        _event("red", "scan_host", {"ok": True}, 1),
        _event("blue", "read_logs", {"ok": True}, 2),
    ]
    score = orchestrator.score_events(history)
    assert score["downtime_cost"] == 0
    assert score["downtime_pct"] == 0.0
    assert score["blue_score"] == 100.0


def test_score_empty_exercise_scores_hundred():
    score = orchestrator.score_events([])
    assert score["downtime_cost"] == 0
    assert score["downtime_pct"] == 0.0
    assert score["blue_score"] == 100.0


def test_score_accepts_weight_override():
    history = [
        _event("red", "scan_host", {"ok": True}, 1),
        _evt_host("blue", "isolate_host", "web", True, 2),
    ]
    score = orchestrator.score_events(history, business_weights={"web": 7})
    assert score["downtime_cost"] == 7
    assert score["downtime_pct"] == 21.88
    assert score["blue_score"] == 78.12


def test_business_weights_match_scenario():
    scenario = sandbox.load_scenario()
    assert scenario["business_weights"] == {"web": 3, "app": 3, "db": 5,
                                            "workstation": 1}
    assert orchestrator.DEFAULT_BUSINESS_WEIGHTS == {"web": 3, "app": 3, "db": 5,
                                                     "workstation": 1}
    assert orchestrator.BLUE_SCORE_MAX == 100


def test_score_downtime_stops_at_secret_read():
    history = [
        _event("red", "scan_host", {"ok": True}, 1),
        _evt_host("blue", "isolate_host", "web", True, 2),
        _event("red", "read_file",
               {"ok": True, "host": "db", "path": "/secret/exercise_secret.txt"}, 3),
        _evt_host("blue", "isolate_host", "db", True, 4),
        _event("red", "scan_host", {"ok": True}, 5),
    ]
    score = orchestrator.score_events(history)
    assert score["secret_exfiltrated"] is True
    assert score["downtime_cost"] == 6
    assert score["downtime_pct"] == 16.67
    assert score["blue_score"] == 0


def test_score_downtime_counts_app_weight():
    history = [
        _event("red", "scan_host", {"ok": True}, 1),
        _evt_host("blue", "isolate_host", "app", True, 2),
        _event("red", "scan_host", {"ok": True}, 3),
    ]
    score = orchestrator.score_events(history)
    assert score["downtime_cost"] == 6
    assert score["downtime_pct"] == 16.67
    assert score["blue_score"] == 83.33


def _full_event(index, actor, tool, args, result, refs=None, alerts=None):
    event = {
        "id": f"evt-{index:04d}",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "actor": actor,
        "tool": tool,
        "args": args,
        "result": result,
        "reasoning_summary": "note",
        "evidence_refs": list(refs or []),
    }
    if alerts:
        event["sensor_alerts"] = alerts
    return event


def _intrusion_history():
    return [
        _full_event(1, "red", "scan_host", {"host": "web"}, {"ok": True}),
        _full_event(2, "blue", "read_logs", {"host": "app"},
                    {"ok": True, "host": "app", "lines": []}),
        _full_event(3, "red", "http_request",
                    {"host": "web", "path": "/admin"},
                    {"ok": True, "host": "web", "status": 200, "body": "{}"}),
        _full_event(4, "blue", "read_logs", {"host": "web"},
                    {"ok": True, "host": "web", "lines": [
                        {"id": "web:0", "line": "request from=1.2.3.4 "
                                               "method=GET path=/admin "
                                               "status=200"}]}),
        _full_event(5, "red", "try_credential",
                    {"host": "app", "user": "a", "password": "b"},
                    {"ok": True, "host": "app", "authenticated": True}),
        _full_event(6, "blue", "report_finding", {"text": "app signed in"},
                    {"ok": True, "id": "finding-1"}, ["alert-1"],
                    alerts=[{"id": "alert-1", "rule": "success_first_try",
                             "severity": "high", "text": "Signed in on app.",
                             "refs": ["app:0"]}]),
        _full_event(7, "red", "read_file",
                    {"host": "db", "path": "/secret/exercise_secret.txt"},
                    {"ok": True, "host": "db",
                     "path": "/secret/exercise_secret.txt"}),
        _full_event(8, "blue", "isolate_host", {"host": "db"},
                    {"ok": True, "host": "db"}, ["alert-1"]),
    ]


def test_score_intrusion_contained_and_secret_read():
    score = orchestrator.score_events(_intrusion_history())
    assert score["intrusion"] is True
    assert score["contained_after_intrusion"] is True
    assert score["secret_read"] is True
    assert score["secret_read"] == score["secret_exfiltrated"]
    assert score["detection_delay"] == 5


def test_score_no_intrusion_without_signin():
    history = [
        _full_event(1, "red", "scan_host", {"host": "web"}, {"ok": True}),
        _full_event(2, "blue", "isolate_host", {"host": "web"},
                    {"ok": True, "host": "web"}),
        _full_event(3, "red", "try_credential", {"host": "app"},
                    {"ok": True, "host": "app", "authenticated": False}),
    ]
    score = orchestrator.score_events(history)
    assert score["intrusion"] is False
    assert score["contained_after_intrusion"] is False
    assert score["secret_read"] is False
    assert score["relevant_detection_delay"] is None


def test_score_contained_before_intrusion_does_not_count():
    history = [
        _full_event(1, "blue", "isolate_host", {"host": "web"},
                    {"ok": True, "host": "web"}),
        _full_event(2, "red", "try_credential", {"host": "app"},
                    {"ok": True, "host": "app", "authenticated": True}),
    ]
    score = orchestrator.score_events(history)
    assert score["intrusion"] is True
    assert score["contained_after_intrusion"] is False


def test_relevant_delay_counts_from_hint_read_to_success_report():
    score = orchestrator.score_events(_intrusion_history())
    assert score["relevant_detection_delay"] == 3


def test_relevant_delay_uses_signin_when_no_hint_read():
    history = [
        _full_event(1, "red", "try_credential", {"host": "app"},
                    {"ok": True, "host": "app", "authenticated": True}),
        _full_event(2, "blue", "read_logs", {"host": "app"},
                    {"ok": True, "host": "app", "lines": [
                        {"id": "app:0", "line": "signin ok user=a "
                                               "from=controller"}]}),
        _full_event(3, "blue", "report_finding", {"text": "app signed in"},
                    {"ok": True, "id": "finding-1"}, ["app:0"]),
    ]
    assert orchestrator.score_events(history)["relevant_detection_delay"] == 2


def test_relevant_delay_ignores_reports_about_failures_only():
    history = [
        _full_event(1, "red", "try_credential", {"host": "app"},
                    {"ok": True, "host": "app", "authenticated": True}),
        _full_event(2, "blue", "read_logs", {"host": "app"},
                    {"ok": True, "host": "app", "lines": [
                        {"id": "app:0", "line": "signin fail user=a "
                                               "from=controller"}]}),
        _full_event(3, "blue", "report_finding", {"text": "guessing on app"},
                    {"ok": True, "id": "finding-1"}, ["app:0"]),
    ]
    assert orchestrator.score_events(history)["relevant_detection_delay"] is None


def test_relevant_delay_accepts_alert_citing_containment():
    history = [
        _full_event(1, "red", "try_credential", {"host": "app"},
                    {"ok": True, "host": "app", "authenticated": True}),
        _full_event(2, "blue", "isolate_host", {"host": "app"},
                    {"ok": True, "host": "app"}, ["alert-1"],
                    alerts=[{"id": "alert-1", "rule": "success_first_try",
                             "severity": "high", "text": "Signed in on app.",
                             "refs": ["app:0"]}]),
    ]
    assert orchestrator.score_events(history)["relevant_detection_delay"] == 1


def test_relevant_delay_ignores_burst_alert_citations():
    history = [
        _full_event(1, "red", "try_credential", {"host": "app"},
                    {"ok": True, "host": "app", "authenticated": True}),
        _full_event(2, "blue", "report_finding", {"text": "guessing on app"},
                    {"ok": True, "id": "finding-1"}, ["alert-1"],
                    alerts=[{"id": "alert-1", "rule": "failed_burst",
                             "severity": "medium", "text": "Burst on app.",
                             "refs": ["app:0"]}]),
    ]
    assert orchestrator.score_events(history)["relevant_detection_delay"] is None


def test_intrusion_conditional_summary_math():
    rows = [
        {"intrusion": True, "contained_after_intrusion": True,
         "secret_read": True, "relevant_detection_delay": 3,
         "time_to_contain": 6, "downtime_pct": 5.0},
        {"intrusion": True, "contained_after_intrusion": False,
         "secret_read": True, "relevant_detection_delay": 7,
         "time_to_contain": None, "downtime_pct": 0.0},
        {"intrusion": False, "contained_after_intrusion": False,
         "secret_read": False, "relevant_detection_delay": None,
         "time_to_contain": None, "downtime_pct": 0.0},
    ]
    summary = orchestrator.intrusion_conditional_summary(rows)
    assert summary["episodes"] == 3
    assert summary["intrusions"] == 2
    assert summary["secret_read_rate"] == 1.0
    assert summary["containment_rate"] == 0.5
    assert summary["median_relevant_detection_delay"] == 5.0
    assert summary["median_time_to_contain"] == 6
    assert summary["mean_downtime_contained"] == 5.0
    lines = orchestrator.format_intrusion_lines(summary)
    assert lines[0] == "Episodes with intrusion: 2/3"
    assert "Secret-read rate given intrusion: 100.0%" in lines
    assert "Containment rate given intrusion: 50.0%" in lines
    assert ("Median relevant detection delay given intrusion: 5.0 events"
            in lines)
    assert "Median time to contain given intrusion: 6 events" in lines
    assert "Mean downtime among contained: 5.00%" in lines


def test_intrusion_conditional_summary_empty():
    summary = orchestrator.intrusion_conditional_summary(
        [{"intrusion": False, "downtime_pct": 0.0}])
    assert summary["intrusions"] == 0
    assert summary["secret_read_rate"] is None
    lines = orchestrator.format_intrusion_lines(summary)
    assert lines[0] == "Episodes with intrusion: 0/1"
    assert "Secret-read rate given intrusion: no intrusions" in lines
    assert "Mean downtime among contained: no contained episodes" in lines
