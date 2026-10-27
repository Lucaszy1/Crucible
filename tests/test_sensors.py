"""Hermetic tests for the deterministic sensor rules.

No Docker and no model calls. Sample log lines use the exact verbs the
sandbox scripts emit, and the orchestrator stays stubbed where the range
would be touched.
"""

from pathlib import Path

from crucible import orchestrator, sensors, tools
from crucible.agents import auditor
from crucible.agents.blue import _build_messages as blue_build_messages


def _logs(**hosts):
    """Build a logs dict from host=[lines] keyword args."""
    return {host: [(f"{host}:{index}", line) for index, line in enumerate(lines)]
            for host, lines in hosts.items()}


def _by_rule(alerts):
    out = {}
    for alert in alerts:
        out.setdefault(alert["rule"], []).append(alert)
    return out


def test_failed_burst_needs_three_gated_failures():
    logs = _logs(app=["signin fail user=a from=controller"] * 2)
    assert sensors.build_alerts(logs) == []
    logs = _logs(app=["signin fail user=a from=controller"] * 3)
    alerts = sensors.build_alerts(logs)
    assert len(alerts) == 1
    burst = alerts[0]
    assert burst["rule"] == "failed_burst"
    assert burst["severity"] == "medium"
    assert burst["refs"] == ["app:0", "app:1", "app:2"]
    assert "app" in burst["text"]


def test_failed_burst_ignores_no_gate_lines():
    logs = _logs(workstation=[
        "signin fail user=a from=controller note=no-gate"] * 4)
    assert sensors.build_alerts(logs) == []


def test_success_after_failures_cites_ok_and_prior_fails():
    logs = _logs(app=[
        "signin fail user=svc-report from=controller",
        "signin fail user=svc-report from=controller",
        "signin ok user=svc-report from=controller",
    ])
    alerts = _by_rule(sensors.build_alerts(logs))
    assert set(alerts) == {"success_after_failures"}
    hit = alerts["success_after_failures"][0]
    assert hit["severity"] == "high"
    assert hit["refs"] == ["app:2", "app:0", "app:1"]


def test_success_first_try_with_no_earlier_failures():
    logs = _logs(app=["signin ok user=svc-report from=controller"])
    alerts = _by_rule(sensors.build_alerts(logs))
    assert set(alerts) == {"success_first_try"}
    hit = alerts["success_first_try"][0]
    assert hit["severity"] == "high"
    assert hit["refs"] == ["app:0"]


def test_success_first_try_suppressed_on_ungated_host():
    logs = _logs(workstation=[
        "signin fail user=a from=controller note=no-gate",
        "signin ok user=a from=controller",
    ])
    rules = _by_rule(sensors.build_alerts(logs))
    assert "success_first_try" not in rules


def test_privileged_page_needs_success_status():
    ok = _logs(web=["request from=1.2.3.4 method=GET path=/admin status=200"])
    alerts = _by_rule(sensors.build_alerts(ok))
    assert set(alerts) == {"privileged_page"}
    assert alerts["privileged_page"][0]["refs"] == ["web:0"]
    miss = _logs(web=["request from=1.2.3.4 method=GET path=/admin status=404"])
    assert sensors.build_alerts(miss) == []
    plain = _logs(web=["request from=1.2.3.4 method=GET path=/ status=200"])
    assert sensors.build_alerts(plain) == []


def test_sensitive_file_read_matches_generic_words_only():
    hit = _logs(app=["file read path=/srv/app/config.ini bytes=90 from=controller"])
    alerts = _by_rule(sensors.build_alerts(hit))
    assert set(alerts) == {"sensitive_file_read"}
    assert alerts["sensitive_file_read"][0]["severity"] == "high"
    plain = _logs(app=["file read path=/srv/data/notes.txt bytes=10 from=controller"])
    assert sensors.build_alerts(plain) == []


def test_list_then_read_needs_protected_area():
    logs = _logs(app=[
        "dir list path=/srv/app/ entries=2 from=controller",
        "file read path=/srv/app/config.ini bytes=90 from=controller",
    ])
    alerts = _by_rule(sensors.build_alerts(logs))
    assert set(alerts) == {"sensitive_file_read", "list_then_read"}
    pair = alerts["list_then_read"][0]
    assert pair["refs"] == ["app:0", "app:1"]
    other = _logs(app=[
        "dir list path=/srv/data/ entries=1 from=controller",
        "file read path=/srv/data/notes.txt bytes=10 from=controller",
    ])
    assert sensors.build_alerts(other) == []


def test_lateral_step_needs_connected_hosts():
    logs = _logs(app=["signin ok user=a from=controller"],
                 db=["signin ok user=b from=controller"])
    links = {"app": {"db"}, "db": {"app"}}
    alerts = _by_rule(sensors.build_alerts(logs, connections=links))
    assert "lateral_step" in alerts
    hit = alerts["lateral_step"][0]
    assert hit["refs"] == ["app:0", "db:0"]
    assert "app" in hit["text"] and "db" in hit["text"]
    assert "lateral_step" not in _by_rule(sensors.build_alerts(logs))


def test_crown_jewel_read_is_critical():
    logs = _logs(db=["file read path=/secret/exercise_secret.txt bytes=20 from=controller"])
    alerts = _by_rule(sensors.build_alerts(logs, crown_jewel_host="db"))
    assert "crown_jewel_read" in alerts
    hit = alerts["crown_jewel_read"][0]
    assert hit["severity"] == "critical"
    assert hit["refs"] == ["db:0"]
    elsewhere = _logs(app=["file read path=/srv/data/notes.txt bytes=10 from=controller"])
    assert "crown_jewel_read" not in _by_rule(
        sensors.build_alerts(elsewhere, crown_jewel_host="db"))


def test_alerts_are_deterministic_with_stable_ids():
    logs = _logs(
        web=["request from=1.2.3.4 method=GET path=/admin status=200"],
        app=["signin fail user=a from=controller"] * 3,
    )
    first = sensors.build_alerts(logs)
    second = sensors.build_alerts(logs)
    assert first == second
    assert [alert["id"] for alert in first] == ["alert-1", "alert-2"]
    assert len({sensors.alert_key(alert) for alert in first}) == 2


def test_sensor_source_has_no_scenario_strings():
    source = Path(sensors.__file__).read_text(encoding="utf-8")
    for forbidden in ("svc-report", "dbops", "changeme", "toor",
                      "config.ini", "exercise_secret.txt", "/srv/app",
                      "/secret", "/admin", "password"):
        assert forbidden not in source, forbidden


def test_alerts_contain_nothing_red_only():
    logs = _logs(
        web=["request from=1.2.3.4 method=GET path=/admin status=200"],
        app=["signin ok user=svc-report from=controller",
             "dir list path=/srv/app/ entries=2 from=controller",
             "file read path=/srv/app/config.ini bytes=90 from=controller"],
        db=["signin ok user=dbops from=controller",
            "file read path=/secret/exercise_secret.txt bytes=20 from=controller"],
    )
    links = {"app": {"db"}, "db": {"app"}}
    alerts = sensors.build_alerts(logs, connections=links, crown_jewel_host="db")
    assert len(alerts) >= 5
    blob = " ".join(alert["text"] for alert in alerts)
    for forbidden in ("svc-report", "dbops", "password", "config.ini",
                      "exercise_secret.txt", "/srv/app", "/secret", "/admin",
                      "hint"):
        assert forbidden not in blob, forbidden
    for alert in alerts:
        assert alert["id"].startswith("alert-")
        assert alert["severity"] in sensors.SEVERITIES
        for ref in alert["refs"]:
            assert ":" in ref and not ref.startswith("alert-")


def test_format_alerts_section():
    assert sensors.format_alerts_section([]) == (
        "New alerts since your last move: none.\n")
    text = sensors.format_alerts_section([
        {"id": "alert-2", "rule": "r", "severity": "high",
         "text": "Successful sign-in on app.", "refs": ["app:1"]}])
    assert "New alerts since your last move:" in text
    assert "alert-2" in text and "[high]" in text and "app:1" in text


def test_collect_episode_logs_never_raises(monkeypatch):
    from crucible import sandbox

    monkeypatch.setattr(sandbox, "container_logs",
                        lambda host, tail=None: (_ for _ in ()).throw(
                            sandbox.SandboxError("down")))
    assert sensors.collect_episode_logs() == {}


def test_alert_map_and_resolve_round_trip():
    events = [
        {"id": "evt-0002", "actor": "blue", "sensor_alerts": [
            {"id": "alert-1", "text": "First.", "refs": ["app:0", "app:1"]}]},
        {"id": "evt-0004", "actor": "blue", "sensor_alerts": [
            {"id": "alert-2", "text": "Second.", "refs": ["db:0"]}]},
    ]
    mapping = sensors.alert_map_from_events(events)
    assert mapping["alert-1"]["refs"] == ["app:0", "app:1"]
    assert mapping["alert-2"]["text"] == "Second."
    assert sensors.resolve_refs(["alert-1", "web:0", "alert-2", "app:0"],
                                mapping) == ["app:0", "app:1", "web:0", "db:0"]
    assert sensors.resolve_refs(["alert-9"], mapping) == ["alert-9"]
    assert sensors.is_alert_ref("alert-12") is True
    assert sensors.is_alert_ref("app:12") is False


def _event(index, actor, tool, args, result, reasoning, refs=None,
           alerts=None):
    event = {
        "id": f"evt-{index:04d}", "timestamp": "2026-01-01T00:00:00+00:00",
        "actor": actor, "tool": tool, "args": args, "result": result,
        "reasoning_summary": reasoning, "evidence_refs": list(refs or []),
    }
    if alerts:
        event["sensor_alerts"] = alerts
    return event


def test_auditor_accepts_shown_alert_refs():
    shown = [{"id": "alert-1", "rule": "success_first_try", "severity": "high",
              "text": "Successful sign-in on app.", "refs": ["app:0"]}]
    prior = [_event(2, "blue", "read_logs", {"host": "web"},
                    {"ok": True, "host": "web", "lines": []}, "sweep",
                    alerts=shown)]
    target = _event(3, "blue", "isolate_host", {"host": "app"},
                    {"ok": True, "host": "app"}, "Contain app.", ["alert-1"])
    check = auditor.check_evidence(target, prior)
    assert check["refs_valid"] is True
    assert check["resolved_refs"] == ["app:0"]
    assert check["invalid_refs"] == []


def test_auditor_accepts_same_turn_alert_refs():
    shown = [{"id": "alert-1", "rule": "success_first_try", "severity": "high",
              "text": "Successful sign-in on app.", "refs": ["app:0"]}]
    target = _event(2, "blue", "report_finding", {"text": "app compromised"},
                    {"ok": True, "id": "finding-1"}, "Report app.", ["alert-1"],
                    alerts=shown)
    check = auditor.check_evidence(target, [])
    assert check["refs_valid"] is True
    assert check["resolved_refs"] == ["app:0"]


def test_auditor_rejects_unshown_alert_refs():
    target = _event(2, "blue", "report_finding", {"text": "app compromised"},
                    {"ok": True, "id": "finding-1"}, "Report app.", ["alert-7"])
    check = auditor.check_evidence(target, [])
    assert check["refs_valid"] is False
    assert check["invalid_refs"] == ["alert-7"]
    messages = auditor.build_messages(target, [])
    assert "NOT SHOWN to blue at that time" in messages[1]["content"]


def test_auditor_renders_alert_evidence_text():
    shown = [{"id": "alert-1", "rule": "success_first_try", "severity": "high",
              "text": "Successful sign-in on app.", "refs": ["app:0"]}]
    prior = [_event(2, "blue", "wait", {}, {"ok": True, "waited": True}, "wait",
                    alerts=shown)]
    target = _event(3, "blue", "report_finding", {"text": "app compromised"},
                    {"ok": True, "id": "finding-1"}, "Report app.", ["alert-1"])
    messages = auditor.build_messages(target, prior)
    assert "Successful sign-in on app." in messages[1]["content"]
    assert "app:0" in messages[1]["content"]


def test_blue_prompt_alert_section_modes():
    quiet = blue_build_messages(2, [], 40, None, [])[1]["content"]
    assert "New alerts since your last move: none." in quiet
    off = blue_build_messages(2, [], 40, None, None)[1]["content"]
    assert "New alerts since your last move" not in off
    alerts = [{"id": "alert-1", "rule": "r", "severity": "critical",
               "text": "Read activity on db.", "refs": ["db:0"]}]
    loud = blue_build_messages(2, [], 40, None, alerts)[1]["content"]
    assert "alert-1" in loud and "[critical]" in loud and "db:0" in loud


def test_sensors_off_leaves_old_behavior_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "begin_episode",
                        lambda: {"ok": True, "attempts": 1, "checks": {}})
    monkeypatch.setattr(orchestrator, "run_tool",
                        lambda name, args: {"ok": True})
    from crucible.agents import ScriptedBlue, ScriptedRed

    off = orchestrator.run_exercise(
        ScriptedRed(), ScriptedBlue(), max_turns=4,
        run_path=tmp_path / "off.jsonl", sensors="off")
    assert off["events"][0]["args"] == {"sensors": "off"}
    assert all("sensor_alerts" not in event for event in off["events"])
    assert off["score"]["sensors"] == "off"

    calls = []
    monkeypatch.setattr(
        sensors, "collect_episode_logs",
        lambda hosts=None: calls.append(1) or _logs(
            app=["signin fail user=a from=controller"] * 3))

    class _AlertBlue:
        actor = "blue"

        def __init__(self):
            self.seen = []

        def next_action(self, turn, history, max_turns=40, alerts=None):
            self.seen.append(alerts)
            return {"tool": "wait", "args": {},
                    "reasoning": "stand by", "evidence_refs": []}

    class _QuietRed:
        actor = "red"

        def next_action(self, turn, history):
            return {"tool": "scan_host", "args": {"host": "web"},
                    "reasoning": "probe", "evidence_refs": []}

    blue = _AlertBlue()
    result = orchestrator.run_exercise(
        _QuietRed(), blue, max_turns=4,
        run_path=tmp_path / "off2.jsonl", sensors="off")
    assert calls == []
    assert blue.seen == [None, None]
    assert all("sensor_alerts" not in event for event in result["events"])

    fresh = orchestrator.run_exercise(
        _QuietRed(), _AlertBlue(), max_turns=4,
        run_path=tmp_path / "on.jsonl", sensors="on")
    assert fresh["events"][0]["args"] == {"sensors": "on"}
    assert fresh["score"]["sensors"] == "on"


def test_orchestrator_shows_only_new_alerts(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "begin_episode",
                        lambda: {"ok": True, "attempts": 1, "checks": {}})
    monkeypatch.setattr(orchestrator, "run_tool",
                        lambda name, args: {"ok": True})
    states = [
        _logs(app=["signin fail user=a from=controller"] * 2),
        _logs(app=["signin fail user=a from=controller"] * 3
              + ["signin ok user=a from=controller"]),
    ]
    monkeypatch.setattr(sensors, "collect_episode_logs",
                        lambda hosts=None: states.pop(0) if states else {})

    class _AlertBlue:
        actor = "blue"

        def __init__(self):
            self.seen = []

        def next_action(self, turn, history, max_turns=40, alerts=None):
            self.seen.append(list(alerts or []))
            return {"tool": "wait", "args": {},
                    "reasoning": "stand by", "evidence_refs": []}

    class _QuietRed:
        actor = "red"

        def next_action(self, turn, history):
            return {"tool": "scan_host", "args": {"host": "web"},
                    "reasoning": "probe", "evidence_refs": []}

    blue = _AlertBlue()
    result = orchestrator.run_exercise(
        _QuietRed(), blue, max_turns=4,
        run_path=tmp_path / "diff.jsonl", sensors="on")
    assert blue.seen[0] == []
    assert [alert["rule"] for alert in blue.seen[1]] == [
        "failed_burst", "success_after_failures"]
    assert [alert["id"] for alert in blue.seen[1]] == ["alert-1", "alert-2"]
    blues = [event for event in result["events"] if event["actor"] == "blue"]
    assert "sensor_alerts" not in blues[0]
    assert [alert["id"] for alert in blues[1]["sensor_alerts"]] == [
        "alert-1", "alert-2"]


def test_score_sensors_defaults_off_without_first_event():
    history = [
        _event(1, "red", "scan_host", {"host": "web"}, {"ok": True}, "probe"),
        _event(2, "blue", "read_logs", {"host": "web"},
               {"ok": True, "host": "web", "lines": []}, "sweep"),
    ]
    assert orchestrator.score_events(history)["sensors"] == "off"
    first = _event(0, "system", "clean_start_check", {"sensors": "on"},
                   {"ok": True}, "check")
    assert orchestrator.score_events([first] + history)["sensors"] == "on"
