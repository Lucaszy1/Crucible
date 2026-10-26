"""Hermetic tests for model-driven agents with a fake llm client.

No network and no Docker here. Fake clients stand in for the Meta Model
API, and the allowlist check runs before any container access, so
off-range attempts stay fully local.
"""

import json
from types import SimpleNamespace

from crucible import llm, orchestrator
from crucible.agents import ModelBlue, ModelRed
from crucible.agents.blue import SYSTEM_PROMPT as BLUE_PROMPT
from crucible.agents.blue import TOOL_SCHEMAS as BLUE_SCHEMAS
from crucible.agents.red import SYSTEM_PROMPT as RED_PROMPT
from crucible.agents.red import TOOL_SCHEMAS as RED_SCHEMAS


def _response(content, tool_calls, finish_reason="stop"):
    calls = []
    for index, (name, args) in enumerate(tool_calls):
        if isinstance(args, dict):
            args_text = json.dumps(args)
        else:
            args_text = args
        func = SimpleNamespace(name=name, arguments=args_text)
        calls.append(SimpleNamespace(id=f"call_{index}", function=func))
    message = SimpleNamespace(content=content, tool_calls=calls)
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


class _FakeClient:
    def __init__(self, responses=None, error=None):
        self.calls = []
        if responses is None:
            responses = []
        if not isinstance(responses, list):
            responses = [responses]
        self._responses = list(responses)
        self.error = error
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create)
        )

    def create(self, **kwargs):
        self.calls.append(kwargs)
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


def test_prompts_name_only_team_tools():
    for name in ("scan_host", "http_request", "try_credential", "read_file"):
        assert name in RED_PROMPT
    assert "read_logs" not in RED_PROMPT
    assert "isolate_host" not in RED_PROMPT
    for name in ("read_logs", "list_processes", "block_ip", "isolate_host",
                 "patch_service", "restore_host"):
        assert name in BLUE_PROMPT
    assert "scan_host" not in BLUE_PROMPT
    assert "try_credential" not in BLUE_PROMPT
    red_names = {entry["function"]["name"] for entry in RED_SCHEMAS}
    blue_names = {entry["function"]["name"] for entry in BLUE_SCHEMAS}
    assert red_names == {"scan_host", "http_request", "try_credential",
                         "read_file", "report_finding"}
    assert blue_names == {"read_logs", "list_processes", "block_ip",
                          "isolate_host", "patch_service", "restore_host",
                          "report_finding"}


def test_model_ids_and_spend_cap():
    assert llm.model_for("red") == "muse-spark-1.3"
    assert llm.model_for("blue") == "muse-spark-1.3"
    assert llm.DEFAULT_DAILY_LIMIT_USD == 5.0


def test_red_valid_tool_call_uses_one_call(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    resp = _response("The web host looks like the first place to check.",
                     [("scan_host", {"host": "web"})])
    client = _FakeClient(resp)
    agent = ModelRed(client=client, exercise_id="ex-valid")
    action = agent.next_action(1, [])
    assert action["tool"] == "scan_host"
    assert action["args"] == {"host": "web"}
    assert "first place" in action["reasoning"]
    assert action["evidence_refs"] == []
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["model"] == "muse-spark-1.3"
    assert call["max_tokens"] == 1024
    assert call["max_tokens"] <= 2048


def test_blue_valid_call_carries_reasoning(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    resp = _response("Checking web logs for odd requests.",
                     [("read_logs", {"host": "web", "n": 20})])
    client = _FakeClient(resp)
    agent = ModelBlue(client=client, exercise_id="ex-blue")
    action = agent.next_action(2, [])
    assert action["tool"] == "read_logs"
    assert action["args"]["host"] == "web"
    assert "odd requests" in action["reasoning"]


def test_red_passes_default_reasoning_effort(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    monkeypatch.delenv("LLM_REASONING_EFFORT", raising=False)
    resp = _response("Probing the web host.",
                     [("scan_host", {"host": "web"})])
    client = _FakeClient(resp)
    ModelRed(client=client).next_action(1, [])
    assert client.calls[0]["reasoning_effort"] == "low"


def test_blue_reasoning_effort_env_override(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    monkeypatch.setenv("LLM_REASONING_EFFORT", "minimal")
    resp = _response("Reading web logs.",
                     [("read_logs", {"host": "web"})])
    client = _FakeClient(resp)
    ModelBlue(client=client).next_action(2, [])
    assert client.calls[0]["reasoning_effort"] == "minimal"


def test_invalid_tool_call_falls_back(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    resp = _response("Trying something odd.", [("evil_tool", {"host": "web"})])
    agent = ModelRed(client=_FakeClient(resp))
    action = agent.next_action(1, [])
    assert action["tool"] == "scan_host"
    assert "baseline" in action["reasoning"].lower()


def test_bad_json_args_fall_back(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    resp = _response("Bad args.", [("scan_host", "not-json{{{")])
    agent = ModelRed(client=_FakeClient(resp))
    action = agent.next_action(1, [])
    assert action["tool"] == "scan_host"
    assert "baseline" in action["reasoning"].lower()


def test_no_tool_call_falls_back(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    resp = _response("I will just think and not act.", [])
    agent = ModelBlue(client=_FakeClient(resp))
    action = agent.next_action(2, [])
    assert action["tool"] == "read_logs"
    assert "no tool call" in action["reasoning"].lower()


def test_length_finish_logs_exact_cause(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    resp = _response("", [], finish_reason="length")
    agent = ModelRed(client=_FakeClient(resp))
    action = agent.next_action(1, [])
    assert action["tool"] == "scan_host"
    assert "finish_reason=length" in action["reasoning"]
    assert "no tool call" not in action["reasoning"].lower()


def test_empty_reply_logs_exact_cause(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    resp = _response("   ", [])
    agent = ModelBlue(client=_FakeClient(resp))
    action = agent.next_action(2, [])
    assert action["tool"] == "read_logs"
    assert "empty reply" in action["reasoning"].lower()
    assert "no tool call" not in action["reasoning"].lower()


def test_length_reply_not_cached_and_cause_logged(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    bad = _response("", [], finish_reason="length")
    good = _response("Probing the web host first.",
                     [("scan_host", {"host": "web"})])
    client = _FakeClient([bad, good])
    agent = ModelRed(client=client)
    first = agent.next_action(1, [])
    second = agent.next_action(1, [])
    assert len(client.calls) == 2
    assert "finish_reason=length" in first["reasoning"]
    assert "baseline" not in second["reasoning"].lower()
    assert second["reasoning"] == "Probing the web host first."


def test_length_cause_lands_in_event_log(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    resp = _response("", [], finish_reason="length")
    red = ModelRed(client=_FakeClient(resp))
    blue = ModelBlue(client=_FakeClient(resp))
    monkeypatch.setattr(orchestrator, "run_tool", lambda name, args: {"ok": True})
    run_path = tmp_path / "length.jsonl"
    result = orchestrator.run_exercise(red, blue, max_turns=2, run_path=run_path)
    assert len(result["events"]) == 2
    for event in result["events"]:
        assert "finish_reason=length" in event["reasoning_summary"]


def test_api_error_falls_back_and_never_hangs(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    red = ModelRed(client=_FakeClient(error=RuntimeError("boom")))
    blue = ModelBlue(client=_FakeClient(error=RuntimeError("boom")))
    monkeypatch.setattr(orchestrator, "run_tool", lambda name, args: {"ok": True})
    run_path = tmp_path / "fallback.jsonl"
    result = orchestrator.run_exercise(red, blue, max_turns=4, run_path=run_path)
    assert len(result["events"]) == 4
    for event in result["events"]:
        assert "baseline" in event["reasoning_summary"].lower()


def test_first_tool_call_wins(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    resp = _response("Two ideas at once.",
                     [("scan_host", {"host": "web"}),
                      ("read_file", {"host": "db", "path": "/secret/x"})])
    agent = ModelRed(client=_FakeClient(resp))
    action = agent.next_action(1, [])
    assert action["tool"] == "scan_host"


def test_credential_pass_alias_normalized(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    resp = _response("Trying the default sign-in.",
                     [("try_credential", {"host": "db", "user": "admin",
                                          "pass": "changeme"})])
    agent = ModelRed(client=_FakeClient(resp))
    action = agent.next_action(3, [])
    assert action["tool"] == "try_credential"
    assert action["args"]["password"] == "changeme"
    assert "pass" not in action["args"]


def test_blue_cites_evidence_refs(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    history = [{
        "id": "evt-0001", "timestamp": "2026-01-01T00:00:00+00:00",
        "actor": "blue", "tool": "read_logs", "args": {"host": "web"},
        "result": {"ok": True, "host": "web", "lines": [
            {"id": "web:0", "line": "request from=1.2.3.4 /admin"},
            {"id": "web:1", "line": "plain line"},
        ]},
        "reasoning_summary": "inspect", "evidence_refs": [],
    }]
    resp = _response("Odd probing seen at web:0, recording it.",
                     [("report_finding", {"text": "probing seen",
                                          "evidence_refs": ["web:0"]})])
    agent = ModelBlue(client=_FakeClient(resp))
    action = agent.next_action(2, history)
    assert action["tool"] == "report_finding"
    assert "web:0" in action["evidence_refs"]
    assert "evidence_refs" not in action["args"]


def test_blue_containment_finds_evidence_in_text(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    resp = _response("Isolating the data host based on web:4.",
                     [("isolate_host", {"host": "db"})])
    agent = ModelBlue(client=_FakeClient(resp))
    action = agent.next_action(4, [])
    assert action["tool"] == "isolate_host"
    assert action["evidence_refs"] == ["web:4"]


def test_off_range_attempt_blocked_and_logged(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    resp = _response("Checking an outside host.", [("scan_host", {"host": "evil.example.com"})])
    red = ModelRed(client=_FakeClient(resp))
    action = red.next_action(1, [])
    assert action["args"]["host"] == "evil.example.com"
    result = orchestrator.run_tool(action["tool"], action["args"])
    assert result["ok"] is False
    assert "outside the sandbox" in result["error"]
    from crucible.agents import ScriptedBlue
    run_path = tmp_path / "offrange.jsonl"
    out = orchestrator.run_exercise(red, ScriptedBlue(), max_turns=1, run_path=run_path)
    assert out["events"][0]["result"]["ok"] is False
    assert "outside the sandbox" in out["events"][0]["result"]["error"]


def test_reasoning_summary_lands_in_event_log(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    red_resp = _response("Probing the web host first.",
                         [("scan_host", {"host": "web"})])
    blue_resp = _response("Reading logs for traces.", [("read_logs", {"host": "web"})])
    red = ModelRed(client=_FakeClient(red_resp))
    blue = ModelBlue(client=_FakeClient(blue_resp))
    monkeypatch.setattr(orchestrator, "run_tool", lambda name, args: {"ok": True})
    run_path = tmp_path / "reasoning.jsonl"
    result = orchestrator.run_exercise(red, blue, max_turns=2, run_path=run_path)
    assert result["events"][0]["reasoning_summary"] == "Probing the web host first."
    assert result["events"][1]["reasoning_summary"] == "Reading logs for traces."


def test_cache_applies_to_repeat_turn(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    resp = _response("Same probe again.", [("scan_host", {"host": "web"})])
    client = _FakeClient(resp)
    agent = ModelRed(client=client, exercise_id="ex-cache")
    first = agent.next_action(1, [])
    second = agent.next_action(1, [])
    assert first["tool"] == second["tool"] == "scan_host"
    assert len(client.calls) == 1
    assert llm.spent_today() > 0


def test_exercise_turn_cap(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)

    class _Always:
        actor = "red"

        def next_action(self, turn, history):
            return {"tool": "scan_host", "args": {"host": "web"},
                    "reasoning": "keep probing", "evidence_refs": []}

    class _AlwaysBlue(_Always):
        actor = "blue"

    monkeypatch.setattr(orchestrator, "run_tool", lambda name, args: {"ok": True})
    run_path = tmp_path / "capped.jsonl"
    result = orchestrator.run_exercise(_Always(), _AlwaysBlue(), max_turns=50,
                                       run_path=run_path)
    assert len(result["events"]) == 20
    assert result["score"]["turns_used"] == 20
