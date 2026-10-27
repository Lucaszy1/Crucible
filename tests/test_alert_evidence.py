"""Hermetic tests for self-contained sensor alert evidence. No Docker here.

Alerts quote up to three of their own log lines so blue can act
without re-reading logs. Quotes are verbatim apart from a length cut,
never carry password-like content, and come only from the episode
logs blue could read herself. The auditor renders the same quotes.
"""

from crucible import sensors
from crucible.agents import auditor
from crucible.agents.blue import SYSTEM_PROMPT as BLUE_PROMPT
from crucible.agents.blue import _build_messages as blue_build_messages
from crucible.agents.referee import _scenario_secrets


def _logs():
    return {
        "app": [
            ("app:0", "signin fail user=admin from=red"),
            ("app:1", "signin fail user=admin from=red"),
            ("app:2", "signin fail user=svc-report from=red"),
            ("app:3", "signin ok user=svc-report from=red"),
            ("app:4", "dir list path=/srv/app from=red"),
            ("app:5", "file read path=/srv/app/config.ini bytes=88 from=red"),
        ],
        "web": [
            ("web:0", "request from=1.2.3.4 path=/admin status=200 method=GET"),
        ],
    }


def _by_rule(alerts, rule):
    return [alert for alert in alerts if alert["rule"] == rule]


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


def test_evidence_quotes_own_refs_verbatim():
    alerts = sensors.build_alerts(_logs())
    found = _by_rule(alerts, "success_after_failures")
    assert len(found) == 1
    alert = found[0]
    assert alert["refs"][0] == "app:3"
    assert alert["evidence"][0] == {
        "ref": "app:3", "line": "signin ok user=svc-report from=red"}
    privileged = _by_rule(alerts, "privileged_page")
    assert privileged[0]["evidence"] == [{
        "ref": "web:0",
        "line": "request from=1.2.3.4 path=/admin status=200 method=GET"}]


def test_evidence_capped_at_three_lines():
    logs = {"app": [
        ("app:0", "signin fail user=admin from=red"),
        ("app:1", "signin fail user=admin from=red"),
        ("app:2", "signin fail user=admin from=red"),
        ("app:3", "signin fail user=admin from=red"),
        ("app:4", "signin fail user=admin from=red"),
        ("app:5", "signin ok user=admin from=red"),
    ]}
    alert = _by_rule(sensors.build_alerts(logs), "success_after_failures")[0]
    assert len(alert["refs"]) == 6
    assert len(alert["evidence"]) == 3
    assert [item["ref"] for item in alert["evidence"]] == [
        "app:5", "app:0", "app:1"]


def test_evidence_trims_long_lines():
    long_line = "file read path=/srv/configs/" + "x" * 200 + " from=red"
    logs = {"db": [("db:0", "dir list path=/srv/configs from=red"),
                   ("db:1", long_line)]}
    alert = _by_rule(sensors.build_alerts(logs), "list_then_read")[0]
    quoted = [item for item in alert["evidence"] if item["ref"] == "db:1"][0]
    assert quoted["line"] == long_line[:sensors.MAX_EVIDENCE_CHARS] + "..."
    assert len(quoted["line"]) == sensors.MAX_EVIDENCE_CHARS + 3


def test_password_lines_are_listed_but_never_quoted():
    secrets = _scenario_secrets()
    assert "dbops-fake-19" in secrets  # the known password value
    logs = {"app": [
        ("app:0", "signin fail user=admin from=red"),
        ("app:1", "signin fail user=admin from=red"),
        ("app:2", "signin fail user=admin from=red"),
        ("app:3", "signin ok user=admin from=red password=dbops-fake-19"),
    ]}
    alert = _by_rule(sensors.build_alerts(logs), "success_after_failures")[0]
    assert "app:3" in alert["refs"]  # the ref still points at the line
    quoted_refs = [item["ref"] for item in alert["evidence"]]
    assert "app:3" not in quoted_refs
    quoted_text = " ".join(item["line"] for item in alert["evidence"])
    assert "dbops-fake-19" not in quoted_text
    for item in alert["evidence"]:
        assert sensors.safe_to_quote(item["line"])


def test_password_token_shapes_are_all_skipped():
    for token in ("password=x", "pass=x", "passwd=x", "pwd=x",
                  "secret=x", "token=x", "Password: x"):
        assert not sensors.safe_to_quote(f"signin ok user=admin from=r {token}")
    for clean in ("signin ok user=svc-report from=red",
                  "signin fail user=admin from=red",
                  "file read path=/srv/app/config.ini bytes=88 from=red",
                  "file read path=/secret/exercise_secret.txt bytes=9 from=red",
                  "request from=1.2.3.4 path=/admin status=200 method=GET",
                  "dir list path=/srv/app from=red",
                  "blocked signin user=admin from=red"):
        assert sensors.safe_to_quote(clean), clean


def test_quoted_lines_come_only_from_visible_logs():
    logs = _logs()
    for alert in sensors.build_alerts(logs):
        for item in alert["evidence"]:
            assert item["ref"] in alert["refs"]
            host = item["ref"].split(":")[0]
            source = dict(logs[host])[item["ref"]]
            assert item["line"] == source[:sensors.MAX_EVIDENCE_CHARS] + (
                "" if len(source) <= sensors.MAX_EVIDENCE_CHARS else "...")


def test_evidence_skips_unknown_refs():
    out = sensors.evidence_for(["app:9", "app:0"], {"app:0": "signin ok"})
    assert out == [{"ref": "app:0", "line": "signin ok"}]


def test_blue_prompt_shows_exact_quoted_lines():
    alerts = sensors.build_alerts(_logs())
    text = blue_build_messages(2, [], 40, None, alerts)[1]["content"]
    assert "  app:3: signin ok user=svc-report from=red" in text
    assert "  web:0: request from=1.2.3.4 path=/admin status=200 method=GET" in text


def test_sensors_off_prompt_has_no_alert_section():
    text = blue_build_messages(2, [], 40, None, None)[1]["content"]
    assert "New alerts since" not in text
    assert "alert-" not in text


def test_old_alerts_without_evidence_still_render():
    text = sensors.format_alerts_section([
        {"id": "alert-2", "rule": "r", "severity": "high",
         "text": "Successful sign-in on app.", "refs": ["app:1"]}])
    assert "- alert-2 [high] Successful sign-in on app. (refs: app:1)" in text


def test_alert_map_carries_evidence_through():
    alerts = sensors.build_alerts(_logs())
    prior = [_event(2, "blue", "wait", {}, {"ok": True, "waited": True},
                    "wait", alerts=alerts)]
    shown = sensors.alert_map_from_events(prior)
    for alert in alerts:
        assert shown[alert["id"]]["evidence"] == alert["evidence"]
        assert shown[alert["id"]]["refs"] == alert["refs"]


def test_auditor_sees_same_quoted_lines_and_resolves_refs():
    alerts = sensors.build_alerts(_logs())
    prior = [_event(2, "blue", "wait", {}, {"ok": True, "waited": True},
                    "wait", alerts=alerts)]
    target = _event(3, "blue", "report_finding", {"text": "app compromised"},
                    {"ok": True, "id": "finding-1"}, "Report app.", ["alert-2"])
    messages = auditor.build_messages(target, prior)
    content = messages[1]["content"]
    assert "  app:3: signin ok user=svc-report from=red" in content
    check = auditor.check_evidence(target, prior)
    assert "alert-2" not in check.get("invalid", [])
    assert "app:3" in check.get("resolved_refs", [])


def test_prompt_guidance_names_quoted_alerts_as_confirmed():
    assert "Sensor alerts quote the log lines they fired on." in BLUE_PROMPT
    assert "act on it in that same move and do not spend a move " \
        "re-reading those same logs to verify it." in BLUE_PROMPT
    assert "Re-read logs only for ambiguous alerts" in BLUE_PROMPT
    assert "Do not isolate on failed guesses alone." in BLUE_PROMPT
    assert "Downtime cost accrues every turn" in BLUE_PROMPT
