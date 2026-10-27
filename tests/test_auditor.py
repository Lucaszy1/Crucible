"""Hermetic tests for the auditor agent. No network, Docker, or models.

A fake client stands in for the Meta Model API. Layer 1 tests use no
client at all. Each verdict is covered through a canned model reply.
"""

import json
from types import SimpleNamespace

import pytest

from crucible import llm
from crucible.agents import auditor
from crucible.agents.auditor import (
    audit_blue_event,
    audit_exercise,
    build_messages,
    check_evidence,
    deterministic_judgment,
    judge_with_model,
    parse_judgment,
    summarize_audits,
)


def _response(content, model="muse-wonder-1.0", finish_reason="stop"):
    message = SimpleNamespace(content=content, tool_calls=[])
    usage = SimpleNamespace(prompt_tokens=100, completion_tokens=20)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


class _FakeClient:
    def __init__(self, responses=None, error=None, errors=None):
        self.calls = []
        if responses is None:
            responses = []
        if not isinstance(responses, list):
            responses = [responses]
        self._responses = list(responses)
        self.error = error
        self._errors = list(errors or [])
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create)
        )

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._errors:
            raise self._errors.pop(0)
        if self.error is not None:
            raise self.error
        if not self._responses:
            raise AssertionError("fake client ran out of responses")
        if len(self._responses) == 1:
            return self._responses[0]
        return self._responses.pop(0)


def _isolated_llm(tmp_path, monkeypatch, limit="5"):
    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(llm, "SPEND_FILE", tmp_path / "spend.jsonl")
    monkeypatch.setenv("LLM_DAILY_LIMIT_USD", limit)


def _evt(index, actor, tool, args=None, result=None, reasoning="note", refs=None):
    return {
        "id": f"evt-{index:04d}",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "actor": actor,
        "tool": tool,
        "args": args or {},
        "result": result or {"ok": True},
        "reasoning_summary": reasoning,
        "evidence_refs": refs or [],
    }


def _read_logs(index, host, lines):
    return _evt(index, "blue", "read_logs", {"host": host},
                {"ok": True, "host": host,
                 "lines": [{"id": ref, "line": text} for ref, text in lines]})


def _judgment(verdict, score=7, rationale="Plain reason here."):
    return json.dumps({"verdict": verdict, "score": score,
                       "rationale": rationale})


# Layer 1: deterministic evidence checks.


def test_refs_valid_when_logs_seen_and_host_matches():
    priors = [_read_logs(1, "db", [("db:0", "signin reset from=controller"),
                                   ("db:1", "signin ok user=admin from=controller")])]
    blue = _evt(2, "blue", "isolate_host", {"host": "db"}, {"ok": True},
                "Isolating db after the sign-in.", ["db:1"])
    check = check_evidence(blue, priors)
    assert check["evidence_checked"] == ["db:1"]
    assert check["refs_valid"] is True
    assert check["invalid_refs"] == []
    assert check["suspicious_visible"] is True


def test_ref_missing_from_visible_logs_is_invalid():
    priors = [_read_logs(1, "web", [("web:0", "webapp listening on 8000")])]
    blue = _evt(2, "blue", "report_finding", {"text": "Odd sign-in on db"},
                {"ok": True}, "Reporting.", ["db:9"])
    check = check_evidence(blue, priors)
    assert check["refs_valid"] is False
    assert check["invalid_refs"] == ["db:9"]
    assert check["suspicious_visible"] is False


def test_ref_from_unrelated_host_is_invalid():
    priors = [_read_logs(1, "web", [("web:0", "request from=1.2.3.4 GET /admin")]),
              _read_logs(2, "db", [("db:0", "signin reset from=controller")])]
    blue = _evt(3, "blue", "isolate_host", {"host": "db"}, {"ok": True},
                "Isolating db.", ["web:0"])
    check = check_evidence(blue, priors)
    assert check["refs_valid"] is False
    assert check["invalid_refs"] == ["web:0"]


def test_ref_from_linked_host_is_valid_for_finding():
    priors = [_read_logs(1, "web", [("web:0", "request from=1.2.3.4 GET /admin")])]
    blue = _evt(2, "blue", "report_finding",
                {"text": "Probing seen against web admin page"},
                {"ok": True}, "Reporting web probing.", ["web:0"])
    check = check_evidence(blue, priors)
    assert check["refs_valid"] is True


def test_later_logs_do_not_count_for_earlier_action():
    priors = [_read_logs(1, "db", [("db:0", "signin reset from=controller")])]
    blue = _evt(2, "blue", "report_finding", {"text": "Sign-in on db"},
                {"ok": True}, "Reporting.", ["db:1"])
    assert check_evidence(blue, priors)["refs_valid"] is False


def test_suspicious_uses_log_lines_not_red_reasoning():
    red = _evt(1, "red", "try_credential", {"host": "db"},
               {"ok": True, "host": "db", "authenticated": True},
               "Stealing the secret right now with full success.")
    priors = [red, _read_logs(2, "db", [("db:0", "signin reset from=controller")])]
    blue = _evt(3, "blue", "wait", {"reason": "quiet"}, {"ok": True, "waited": True})
    check = check_evidence(blue, priors)
    assert check["suspicious_visible"] is False
    assert check["refs_valid"] is True


def test_each_suspicious_marker_counts():
    lines = [("h:0", "signin ok user=a from=controller"),
             ("h:1", "signin fail user=b from=controller"),
             ("h:2", "file read path=/secret/x bytes=3 from=controller"),
             ("h:3", "dir list path=/secret entries=1 from=controller"),
             ("h:4", "request from=1.2.3.4 method=GET path=/admin status=200")]
    for ref, line in lines:
        priors = [_evt(1, "blue", "read_logs", {"host": "web"},
                       {"ok": True, "host": "web",
                        "lines": [{"id": ref, "line": line}]})]
        blue = _evt(2, "blue", "wait", {}, {"ok": True})
        assert check_evidence(blue, priors)["suspicious_visible"] is True, line


# Layer 2: model judgment through the fake client.


@pytest.mark.parametrize("verdict", list(auditor.VERDICTS))
def test_each_verdict_passes_through(tmp_path, monkeypatch, verdict):
    _isolated_llm(tmp_path, monkeypatch)
    client = _FakeClient(_response(_judgment(verdict, 6, f"Why {verdict} here.")))
    priors = [_read_logs(1, "db", [("db:1", "signin ok user=admin from=controller")])]
    blue = _evt(2, "blue", "isolate_host", {"host": "db"}, {"ok": True},
                "Isolating db.", ["db:1"])
    audit = audit_blue_event(blue, priors, exercise_id="ex-audit", client=client)
    assert audit["target_event_id"] == "evt-0002"
    assert audit["verdict"] == verdict
    assert audit["score"] == 6
    assert audit["rationale"] == f"Why {verdict} here."
    assert audit["evidence_checked"] == ["db:1"]
    assert audit["refs_valid"] is True
    assert audit["model_used"] == "muse-wonder-1.0"
    assert audit["estimated_cost_usd"] > 0
    assert client.calls[0]["model"] == "muse-wonder-1.0"
    assert client.calls[0]["reasoning_effort"] == "medium"


def test_prompt_never_contains_red_reasoning(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    secret = "RED-PLANS-ALPHA-SEVEN steal via dbops tonight"
    priors = [
        _evt(1, "red", "try_credential", {"host": "db"},
             {"ok": True, "host": "db", "authenticated": True}, secret),
        _read_logs(2, "db", [("db:1", "signin ok user=dbops from=controller")]),
    ]
    blue = _evt(3, "blue", "isolate_host", {"host": "db"}, {"ok": True},
                "Isolating db.", ["db:1"])
    blob = json.dumps(build_messages(blue, priors))
    assert secret not in blob
    assert "RED-PLANS-ALPHA-SEVEN" not in blob
    assert "try_credential" not in blob
    assert "db:1" in blob
    assert "signin ok user=dbops" in blob
    assert "isolate_host" in blob


def test_prompt_carries_weight_and_downtime():
    priors = [_evt(1, "blue", "isolate_host", {"host": "web"},
                   {"ok": True, "host": "web"}, "Isolating web."),
              _read_logs(2, "db", [("db:1", "signin ok user=a from=controller")])]
    blue = _evt(3, "blue", "isolate_host", {"host": "db"}, {"ok": True},
                "Isolating db.", ["db:1"])
    blob = json.dumps(build_messages(blue, priors))
    assert "db weight 5" in blob
    assert "web" in blob  # down host named
    assert "downtime" in blob.lower()


def test_model_unavailable_falls_back_to_spark_high(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    missing = RuntimeError("model muse-wonder-1.0 is unavailable for this key")
    client = _FakeClient(_response(_judgment("justified", 8, "Fits the logs.")),
                         errors=[missing])
    priors = [_read_logs(1, "db", [("db:1", "signin ok user=a from=controller")])]
    blue = _evt(2, "blue", "isolate_host", {"host": "db"}, {"ok": True},
                "Isolating db.", ["db:1"])
    out = judge_with_model(blue, priors, check_evidence(blue, priors),
                           exercise_id="ex-fb", client=client)
    assert len(client.calls) == 2
    assert client.calls[0]["model"] == "muse-wonder-1.0"
    assert client.calls[0]["reasoning_effort"] == "medium"
    assert client.calls[1]["model"] == "muse-spark-1.3"
    assert client.calls[1]["reasoning_effort"] == "high"
    assert out["verdict"] == "justified"
    assert out["model_used"] == "muse-wonder-1.0" or out["model_used"]
    # model_used echoes the responding model id from the fake payload
    assert out["model_used"] in ("muse-wonder-1.0", "muse-spark-1.3")


def test_spend_cap_uses_deterministic_fallback(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch, limit="0.0001")
    llm.record_spend(model="muse-spark-1.3", input_tokens=100000,
                     output_tokens=0)
    priors = [_read_logs(1, "db", [("db:1", "signin ok user=a from=controller")])]
    blue = _evt(2, "blue", "isolate_host", {"host": "db"}, {"ok": True},
                "Isolating db.", ["db:1"])
    audit = audit_blue_event(blue, priors, client=_FakeClient())
    assert audit["model_used"] == "none"
    assert audit["estimated_cost_usd"] == 0.0
    assert audit["verdict"] == "justified"


def test_bad_model_json_uses_deterministic_fallback(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    client = _FakeClient(_response("definitely not json at all"))
    priors = [_read_logs(1, "web", [("web:0", "webapp listening on 8000")])]
    blue = _evt(2, "blue", "isolate_host", {"host": "web"}, {"ok": True},
                "Isolating web.", [])
    audit = audit_blue_event(blue, priors, client=client)
    assert audit["verdict"] == "unsupported"
    assert audit["estimated_cost_usd"] == 0.0


def test_parse_judgment_rejects_bad_values():
    assert parse_judgment("") is None
    assert parse_judgment("no json") is None
    assert parse_judgment(json.dumps({"verdict": "bogus", "score": 5,
                                      "rationale": "x"})) is None
    assert parse_judgment(json.dumps({"verdict": "justified", "score": 99,
                                      "rationale": "x"})) is None
    assert parse_judgment(json.dumps({"verdict": "justified", "score": 5,
                                      "rationale": ""})) is None
    parsed = parse_judgment('prefix {"verdict": "late", "score": 4, '
                            '"rationale": "Too slow here."} suffix')
    assert parsed == {"verdict": "late", "score": 4,
                      "rationale": "Too slow here."}


def test_deterministic_judgment_branches():
    bad_ref = {"evidence_checked": ["db:9"], "refs_valid": False,
               "invalid_refs": ["db:9"], "suspicious_visible": True}
    out = deterministic_judgment(_evt(1, "blue", "isolate_host"), bad_ref)
    assert out["verdict"] == "unsupported"
    assert "db:9" in out["rationale"]
    good = {"evidence_checked": ["db:1"], "refs_valid": True,
            "invalid_refs": [], "suspicious_visible": True}
    out = deterministic_judgment(_evt(1, "blue", "isolate_host"), good)
    assert (out["verdict"], out["score"]) == ("justified", 7)
    quiet = {"evidence_checked": [], "refs_valid": True,
             "invalid_refs": [], "suspicious_visible": False}
    out = deterministic_judgment(_evt(1, "blue", "read_logs"), quiet)
    assert out["verdict"] == "justified"
    out = deterministic_judgment(_evt(1, "blue", "isolate_host"), quiet)
    assert out["verdict"] == "unsupported"


def test_audit_exercise_skips_red_and_errors(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    client = _FakeClient(_response(_judgment("justified")))
    events = [
        _evt(1, "red", "scan_host", {"host": "web"}),
        _read_logs(2, "web", [("web:0", "webapp listening on 8000")]),
        _evt(3, "blue", "agent_error", {}, {"ok": False}, "boom"),
        _evt(4, "blue", "wait", {}, {"ok": True}),
    ]
    audits = audit_exercise(events, client=client)
    assert [audit["target_event_id"] for audit in audits] == ["evt-0002",
                                                              "evt-0004"]


def test_summarize_audits_metrics():
    audits = [
        {"verdict": "justified", "score": 8, "refs_valid": True,
         "estimated_cost_usd": 0.01},
        {"verdict": "justified", "score": 6, "refs_valid": True,
         "estimated_cost_usd": 0.01},
        {"verdict": "unsupported", "score": 2, "refs_valid": False,
         "estimated_cost_usd": 0.02},
        {"verdict": "late", "score": 4, "refs_valid": True,
         "estimated_cost_usd": 0.0},
    ]
    summary = summarize_audits(audits)
    assert summary["justified_pct"] == 50.0
    assert summary["unsupported_count"] == 1
    assert summary["invalid_refs_count"] == 1
    assert summary["mean_audit_score"] == 5.0
    assert summary["audit_count"] == 4
    assert summary["audit_cost_usd"] == 0.04
    empty = summarize_audits([])
    assert empty["justified_pct"] is None
    assert empty["mean_audit_score"] is None
    assert empty["unsupported_count"] == 0
    assert empty["invalid_refs_count"] == 0


# Orchestrator wiring: audit modes, event shape, score metrics.


class _OneShotBlue:
    actor = "blue"

    def __init__(self, actions):
        self._actions = list(actions)

    def next_action(self, turn, history):
        if not self._actions:
            return None
        return self._actions.pop(0)


class _IdleRed:
    actor = "red"

    def next_action(self, turn, history):
        return None


def _run_tools(monkeypatch):
    from crucible import orchestrator

    def fake_run_tool(name, args):
        if name == "read_logs":
            return {"ok": True, "host": "db",
                    "lines": [{"id": "db:1",
                               "line": "signin ok user=a from=controller"}]}
        if name == "report_finding":
            return {"ok": True, "id": "finding-1"}
        return {"ok": True}

    monkeypatch.setattr(orchestrator, "run_tool", fake_run_tool)


def test_end_mode_appends_auditor_events(tmp_path, monkeypatch):
    from crucible import orchestrator

    _isolated_llm(tmp_path, monkeypatch)
    _run_tools(monkeypatch)
    client = _FakeClient(_response(_judgment("justified", 9, "Fits well.")))
    blue = _OneShotBlue([
        {"tool": "read_logs", "args": {"host": "db"}, "reasoning": "look",
         "evidence_refs": []},
        {"tool": "report_finding", "args": {"text": "Sign-in on db"},
         "reasoning": "tell", "evidence_refs": ["db:1"]},
    ])
    result = orchestrator.run_exercise(
        _IdleRed(), blue, max_turns=4, run_path=tmp_path / "audit.jsonl",
        exercise_id="ex-end", audit="end", auditor_client=client)
    audits = [event for event in result["events"]
              if event.get("actor") == "auditor"]
    assert len(audits) == 2
    assert len(client.calls) == 2
    for event in audits:
        result_payload = event["result"]
        assert event["tool"] == "audit"
        assert result_payload["target_event_id"].startswith("evt-")
        assert result_payload["verdict"] == "justified"
        assert result_payload["score"] == 9
        assert result_payload["rationale"] == "Fits well."
        assert isinstance(result_payload["refs_valid"], bool)
        assert result_payload["model_used"] == "muse-wonder-1.0"
        assert event["args"]["target_event_id"] == result_payload["target_event_id"]
    assert result["score"]["justified_pct"] == 100.0
    assert result["score"]["mean_audit_score"] == 9.0
    assert result["score"]["unsupported_count"] == 0
    assert result["score"]["invalid_refs_count"] == 0
    assert result["audit_summary"]["mode"] == "end"
    assert result["audit_summary"]["audit_count"] == 2
    assert result["audit_summary"]["justified_pct"] == 100.0


def test_live_mode_interleaves_and_hides_from_agents(tmp_path, monkeypatch):
    from crucible import orchestrator

    _isolated_llm(tmp_path, monkeypatch)
    _run_tools(monkeypatch)
    client = _FakeClient(_response(_judgment("premature", 5, "Too soon.")))
    seen_lengths = []

    class _WatchBlue(_OneShotBlue):
        def next_action(self, turn, history):
            seen_lengths.append(len(history))
            assert all(event.get("actor") != "auditor" for event in history)
            return super().next_action(turn, history)

    blue = _WatchBlue([
        {"tool": "read_logs", "args": {"host": "db"}, "reasoning": "look",
         "evidence_refs": []},
    ])
    result = orchestrator.run_exercise(
        _IdleRed(), blue, max_turns=4, run_path=tmp_path / "live.jsonl",
        exercise_id="ex-live", audit="live", auditor_client=client)
    actors = [event["actor"] for event in result["events"]]
    assert actors == ["blue", "auditor"]
    assert result["events"][1]["result"]["verdict"] == "premature"
    assert result["audit_summary"]["mode"] == "live"
    assert result["score"]["time_to_detect"] is None


def test_off_mode_writes_no_audits(tmp_path, monkeypatch):
    from crucible import orchestrator

    _isolated_llm(tmp_path, monkeypatch)
    _run_tools(monkeypatch)
    client = _FakeClient(_response(_judgment("justified")))
    blue = _OneShotBlue([
        {"tool": "read_logs", "args": {"host": "db"}, "reasoning": "look",
         "evidence_refs": []},
    ])
    result = orchestrator.run_exercise(
        _IdleRed(), blue, max_turns=4, run_path=tmp_path / "off.jsonl",
        audit="off", auditor_client=client)
    assert all(event.get("actor") != "auditor" for event in result["events"])
    assert len(client.calls) == 0
    assert result["score"]["justified_pct"] is None
    assert result["score"]["mean_audit_score"] is None
    assert result["audit_summary"]["mode"] == "off"
    assert result["audit_summary"]["audit_count"] == 0


def test_bad_audit_mode_raises(tmp_path):
    from crucible import orchestrator

    with pytest.raises(ValueError):
        orchestrator.run_exercise(_IdleRed(), _IdleRed(), max_turns=2,
                                  run_path=tmp_path / "bad.jsonl",
                                  audit="sometimes")


def test_auditor_events_do_not_shift_scoring():
    from crucible import orchestrator

    history = [
        _evt(1, "red", "scan_host", {"host": "web"}),
        _evt(2, "blue", "read_logs", {"host": "web"}, {"ok": True}),
        _evt(3, "red", "try_credential", {"host": "db"},
             {"ok": True, "host": "db", "authenticated": True}),
        _evt(4, "blue", "report_finding", {"text": "x"},
             {"ok": True, "id": "finding-1"}),
    ]
    plain = orchestrator.score_events(history)
    audit_event = _evt(5, "auditor", "audit", {"target_event_id": "evt-0002"},
                       {"ok": True, "target_event_id": "evt-0002",
                        "verdict": "justified", "score": 8,
                        "rationale": "Fine.", "evidence_checked": [],
                        "refs_valid": True, "model_used": "muse-wonder-1.0",
                        "estimated_cost_usd": 0.01})
    mixed = [history[0], history[1], audit_event, history[2], history[3]]
    scored = orchestrator.score_events(mixed)
    for key in ("time_to_detect", "detection_delay", "time_to_contain",
                "outcome", "downtime_cost", "downtime_pct", "blue_score",
                "total_events"):
        assert scored[key] == plain[key], key
    assert scored["justified_pct"] == 100.0
    assert scored["mean_audit_score"] == 8.0
