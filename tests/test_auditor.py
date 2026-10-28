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


def test_human_defense_audited_like_blue(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    history = [
        _evt(1, "red", "scan_host"),
        _evt(2, "human", "isolate_host", {"host": "db"},
             {"ok": True, "host": "db"}, "Judge order."),
    ]
    client = _FakeClient(_response(_judgment("justified", 8)))
    records = audit_exercise(history, exercise_id="audit-human",
                             client=client, use_cache=False)
    assert len(records) == 1
    assert records[0]["target_event_id"] == "evt-0002"
    assert records[0]["verdict"] == "justified"


def test_human_reads_count_as_visible_logs():
    history = [
        _evt(1, "human", "read_logs", {"host": "db"},
             {"ok": True, "host": "db",
              "lines": [{"id": "db:7", "line": "signin ok"}]}),
    ]
    assert auditor.visible_logs(history) == {"db:7": "signin ok"}


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


# Layer 1: redundant reads and moot actions.


def test_repeat_read_with_no_new_lines_is_redundant():
    lines = [("db:0", "signin reset from=controller"),
             ("db:1", "signin ok user=a from=controller")]
    priors = [_read_logs(1, "db", lines)]
    repeat = _evt(2, "blue", "read_logs", {"host": "db"},
                  {"ok": True, "host": "db",
                   "lines": [{"id": ref, "line": text}
                             for ref, text in lines]})
    check = check_evidence(repeat, priors)
    assert check["redundant"] is True
    assert check["new_lines"] == 0


def test_read_with_new_lines_is_not_redundant():
    priors = [_read_logs(1, "db", [("db:0", "signin reset from=controller")])]
    fresh = _evt(2, "blue", "read_logs", {"host": "db"},
                 {"ok": True, "host": "db",
                  "lines": [{"id": "db:0", "line": "signin reset"},
                            {"id": "db:1", "line": "signin ok user=a"}]})
    check = check_evidence(fresh, priors)
    assert check["redundant"] is False
    assert check["new_lines"] == 1


def test_first_failed_and_other_host_reads_not_redundant():
    other = [_read_logs(1, "web", [("web:0", "webapp listening on 8000")])]
    first = _evt(2, "blue", "read_logs", {"host": "db"},
                 {"ok": True, "host": "db",
                  "lines": [{"id": "db:0", "line": "signin reset"}]})
    assert check_evidence(first, other)["redundant"] is False
    failed = _evt(3, "blue", "read_logs", {"host": "web"},
                  {"ok": False, "error": "down"})
    assert check_evidence(failed, other)["redundant"] is False
    idle = _evt(4, "blue", "wait", {}, {"ok": True})
    assert check_evidence(idle, other)["redundant"] is False


def test_moot_when_secret_read_visible_in_logs():
    priors = [_read_logs(1, "db", [
        ("db:2", "dir list path=/secret entries=1 from=controller"),
        ("db:3", "file read path=/secret/exercise_secret.txt bytes=9 from=controller"),
    ])]
    late = _evt(2, "blue", "isolate_host", {"host": "db"}, {"ok": True},
                "Isolating db.", ["db:3"])
    check = check_evidence(late, priors)
    assert check["moot"] is True
    assert check["refs_valid"] is True


def test_not_moot_without_secret_read_line():
    priors = [_read_logs(1, "db", [("db:1", "signin ok user=a from=controller")])]
    blue = _evt(2, "blue", "isolate_host", {"host": "db"}, {"ok": True},
                "Isolating db.", ["db:1"])
    assert check_evidence(blue, priors)["moot"] is False


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


def test_error_fallback_marks_degraded_and_warns_once_per_exercise(
        tmp_path, monkeypatch, capsys):
    _isolated_llm(tmp_path, monkeypatch)
    client = _FakeClient(error=RuntimeError("401 unauthorized"))
    priors = [_read_logs(1, "db", [("db:1", "signin ok user=a from=controller")])]
    blue = _evt(2, "blue", "isolate_host", {"host": "db"}, {"ok": True},
                "Isolating db.", ["db:1"])
    check = check_evidence(blue, priors)
    first = judge_with_model(blue, priors, check, exercise_id="ex-deg-audit",
                             client=client)
    assert first["degraded"] is True
    assert first["model_used"] == "none"
    second = judge_with_model(blue, priors, check, exercise_id="ex-deg-audit",
                              client=client)
    assert second["degraded"] is True
    assert capsys.readouterr().err.count("WARNING") == 1
    third = judge_with_model(blue, priors, check,
                             exercise_id="ex-deg-audit-other", client=client)
    assert third["degraded"] is True
    err = capsys.readouterr().err
    assert err.count("WARNING") == 1
    assert "degraded" in err.lower()


def test_audit_record_and_result_carry_degraded_flag(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    client = _FakeClient(error=RuntimeError("connection reset"))
    priors = [_read_logs(1, "db", [("db:1", "signin ok user=a from=controller")])]
    blue = _evt(2, "blue", "isolate_host", {"host": "db"}, {"ok": True},
                "Isolating db.", ["db:1"])
    audit = audit_blue_event(blue, priors, exercise_id="ex-deg-record",
                             client=client)
    assert audit["degraded"] is True
    assert auditor.audit_result(audit)["degraded"] is True


def test_clean_audit_record_is_not_degraded(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    client = _FakeClient(_response(_judgment("justified", 8, "Fits.")))
    priors = [_read_logs(1, "db", [("db:1", "signin ok user=a from=controller")])]
    blue = _evt(2, "blue", "isolate_host", {"host": "db"}, {"ok": True},
                "Isolating db.", ["db:1"])
    audit = audit_blue_event(blue, priors, exercise_id="ex-clean-audit",
                             client=client)
    assert audit["degraded"] is False
    assert auditor.audit_result(audit)["degraded"] is False


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


def test_prompt_carries_scale_anchors_and_flags():
    priors = [_read_logs(1, "db", [("db:0", "signin reset from=controller")])]
    repeat = _evt(2, "blue", "read_logs", {"host": "db"},
                  {"ok": True, "host": "db",
                   "lines": [{"id": "db:0", "line": "signin reset"}]})
    blob = json.dumps(build_messages(
        repeat, priors, position={"blue_action_no": 2, "events_so_far": 3,
                                  "is_last": True}))
    for anchor in ("9 to 10 exemplary", "7 to 8 reasonable", "4 to 6 weak",
                   "1 to 3 poor", "0 invalid evidence",
                   "Do not default every action to 7",
                   "redundant=True", "usually redundant",
                   "blue action 2", "final action"):
        assert anchor in blob, anchor


def test_prompt_names_moot_when_secret_observed():
    priors = [_read_logs(1, "db", [
        ("db:3", "file read path=/secret/exercise_secret.txt bytes=9 from=controller"),
    ])]
    late = _evt(2, "blue", "isolate_host", {"host": "db"}, {"ok": True},
                "Isolating db.", ["db:3"])
    blob = json.dumps(build_messages(late, priors))
    assert "moot=True" in blob
    assert "usually moot" in blob


def test_role_override_chooses_model(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    client = _FakeClient(_response(_judgment("justified", 8, "Solid.")))
    priors = [_read_logs(1, "db", [("db:1", "signin ok user=a from=controller")])]
    blue = _evt(2, "blue", "isolate_host", {"host": "db"}, {"ok": True},
                "Isolating db.", ["db:1"])
    out = judge_with_model(blue, priors, check_evidence(blue, priors),
                           client=client, role="muse-spark-1.3")
    assert client.calls[0]["model"] == "muse-spark-1.3"
    assert out["verdict"] == "justified"


def test_audit_sends_full_token_budget(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    client = _FakeClient(_response(_judgment("justified")))
    priors = [_read_logs(1, "db", [("db:1", "signin ok user=a from=controller")])]
    blue = _evt(2, "blue", "isolate_host", {"host": "db"}, {"ok": True},
                "Isolating db.", ["db:1"])
    audit_blue_event(blue, priors, client=client)
    assert client.calls[0]["max_tokens"] == 1536


def test_deterministic_judgment_branches():
    bad_ref = {"evidence_checked": ["db:9"], "refs_valid": False,
               "invalid_refs": ["db:9"], "suspicious_visible": True}
    out = deterministic_judgment(_evt(1, "blue", "isolate_host"), bad_ref)
    assert out["verdict"] == "unsupported"
    assert out["score"] == 0
    assert "db:9" in out["rationale"]
    repeat = {"evidence_checked": [], "refs_valid": True,
              "invalid_refs": [], "suspicious_visible": False,
              "redundant": True, "moot": False}
    out = deterministic_judgment(_evt(1, "blue", "read_logs"), repeat)
    assert (out["verdict"], out["score"]) == ("redundant", 3)
    over = {"evidence_checked": [], "refs_valid": True,
            "invalid_refs": [], "suspicious_visible": True,
            "redundant": False, "moot": True}
    out = deterministic_judgment(_evt(1, "blue", "isolate_host"), over)
    assert (out["verdict"], out["score"]) == ("moot", 2)
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
    from crucible import orchestrator, tools

    monkeypatch.setattr(tools, "begin_episode",
                        lambda: {"ok": True, "attempts": 1, "checks": {}})

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
    assert actors == ["system", "blue", "auditor"]
    assert result["events"][2]["result"]["verdict"] == "premature"
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


# CLI flags, batch CSV, and API listing.


def test_run_exercise_audit_flag_defaults_to_end():
    from crucible import run_exercise

    assert run_exercise.parse_args(["--agents", "scripted"]).audit == "end"
    assert run_exercise.parse_args(["--audit", "off"]).audit == "off"
    assert run_exercise.parse_args(["--audit", "live"]).audit == "live"


def test_run_exercise_passes_audit_mode(tmp_path, monkeypatch, capsys):
    from crucible import orchestrator, run_exercise, sandbox

    seen = {}
    monkeypatch.setenv("MODEL_API_KEY", "unit-test-key-0123456789abcdef")
    monkeypatch.setattr(sandbox, "start", lambda: {"web": True})
    monkeypatch.setattr(sandbox, "stop", lambda: True)
    monkeypatch.setattr(run_exercise.tools, "restore_host",
                        lambda host: {"ok": True, "host": host})
    monkeypatch.setattr(run_exercise.tools, "reset_state", lambda: None)

    def fake_exercise(red, blue, max_turns=12, run_path=None,
                      exercise_id=None, audit="off", sensors="on",
                      blue_reasoning_effort=None, auditor_use_cache=True):
        seen["audit"] = audit
        return {"events": [],
                "score": {"turns_used": 0, "total_events": 0,
                          "time_to_detect": None, "detection_delay": None,
                          "time_to_contain": None, "hosts_compromised": 0,
                          "compromised_hosts": [],
                          "secret_exfiltrated": False, "outcome": "stalemate",
                          "downtime_cost": 0, "downtime_pct": 0.0,
                          "blue_score": 100.0, "justified_pct": 75.0,
                          "unsupported_count": 1, "invalid_refs_count": 0,
                          "mean_audit_score": 7.5},
                "run_path": str(tmp_path / "fake.jsonl")}

    monkeypatch.setattr(orchestrator, "run_exercise", fake_exercise)
    monkeypatch.setattr(run_exercise.orchestrator, "run_exercise", fake_exercise)
    assert run_exercise.main(["--agents", "scripted", "--audit", "live"]) == 0
    assert seen["audit"] == "live"
    out = capsys.readouterr().out
    assert "Audit justified: 75.0%" in out
    assert "Audit unsupported: 1" in out
    assert "Audit invalid refs: 0" in out
    assert "Audit mean score: 7.5" in out


def test_batch_audit_flag_defaults_off_and_allows_end():
    from crucible import batch

    assert batch.parse_args(["--count", "1", "--prefix", "x"]).audit == "off"
    args = batch.parse_args(["--count", "1", "--prefix", "x", "--audit", "end"])
    assert args.audit == "end"
    with pytest.raises(SystemExit):
        batch.parse_args(["--count", "1", "--prefix", "x", "--audit", "live"])


def test_batch_csv_carries_audit_metrics(tmp_path, monkeypatch):
    import csv

    from crucible import batch, events, llm, orchestrator, sandbox

    monkeypatch.setattr(sandbox, "start", lambda: {"web": True})
    monkeypatch.setattr(sandbox, "stop", lambda: True)
    monkeypatch.setattr(batch.tools, "restore_host",
                        lambda host: {"ok": True, "host": host})
    monkeypatch.setattr(batch.tools, "reset_state", lambda: None)
    monkeypatch.setattr(events, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(llm, "SPEND_FILE", tmp_path / "spend.jsonl")
    seen = {}

    def fake_exercise(red, blue, max_turns=30, run_path=None,
                      exercise_id=None, audit="off", sensors="on",
                      blue_reasoning_effort=None):
        seen[exercise_id] = audit
        return {"events": [],
                "score": {"outcome": "blue_held", "detection_delay": 2,
                          "time_to_contain": 4, "downtime_cost": 3,
                          "downtime_pct": 6.25, "blue_score": 93.75,
                          "justified_pct": 80.0, "unsupported_count": 1,
                          "invalid_refs_count": 2, "mean_audit_score": 6.5},
                "run_path": str(tmp_path / "x.jsonl")}

    monkeypatch.setattr(orchestrator, "run_exercise", fake_exercise)
    monkeypatch.setattr(batch.orchestrator, "run_exercise", fake_exercise)
    assert batch.main(["--count", "1", "--prefix", "au",
                       "--audit", "end"]) == 0
    assert seen == {"au-1": "end"}
    with (tmp_path / "runs" / "batch-au.csv").open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["justified_pct"] == "80.0"
    assert rows[0]["unsupported_count"] == "1"
    assert rows[0]["invalid_refs_count"] == "2"
    assert rows[0]["mean_audit_score"] == "6.5"


def test_api_listing_carries_audit_metrics(tmp_path, monkeypatch):
    import json as jsonlib

    from fastapi.testclient import TestClient

    import crucible.api as api
    from crucible import events, llm

    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(events, "RUNS_DIR", runs)
    monkeypatch.setattr(llm, "SPEND_FILE", runs / "spend.jsonl")
    log = [
        _evt(1, "blue", "read_logs", {"host": "db"}, {"ok": True}),
        _evt(2, "auditor", "audit", {"target_event_id": "evt-0001"},
             {"ok": True, "target_event_id": "evt-0001",
              "verdict": "unsupported", "score": 3, "rationale": "No proof.",
              "evidence_checked": ["db:9"], "refs_valid": False,
              "model_used": "muse-wonder-1.0", "estimated_cost_usd": 0.01}),
    ]
    (runs / "audited.jsonl").write_text(
        "\n".join(jsonlib.dumps(entry) for entry in log) + "\n",
        encoding="utf-8")
    with TestClient(api.app) as client:
        body = client.get("/api/runs").json()
    assert body["runs"][0]["justified_pct"] == 0.0
    assert body["runs"][0]["unsupported_count"] == 1
    assert body["runs"][0]["invalid_refs_count"] == 1
    assert body["runs"][0]["mean_audit_score"] == 3.0
