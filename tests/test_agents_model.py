"""Hermetic tests for model-driven agents with a fake llm client.

No network and no Docker here. Fake clients stand in for the Meta Model
API, and the allowlist check runs before any container access, so
off-range attempts stay fully local.
"""

import json
from types import SimpleNamespace

from crucible import llm, orchestrator
from crucible.agents import ModelBlue, ModelRed
from crucible.agents import _common
from crucible.agents.blue import SYSTEM_PROMPT as BLUE_PROMPT
from crucible.agents.blue import TOOL_SCHEMAS as BLUE_SCHEMAS
from crucible.agents.blue import _build_messages as blue_build_messages
from crucible.agents.red import SYSTEM_PROMPT as RED_PROMPT
from crucible.agents.red import TOOL_SCHEMAS as RED_SCHEMAS
from crucible.agents.red import _build_messages as red_build_messages


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
    for name in ("scan_host", "http_request", "try_credential", "read_file",
                 "list_files"):
        assert name in RED_PROMPT
    assert "read_logs" not in RED_PROMPT
    assert "isolate_host" not in RED_PROMPT
    for name in ("read_logs", "list_processes", "wait", "block_ip", "isolate_host",
                 "patch_service", "restore_host"):
        assert name in BLUE_PROMPT
    assert "scan_host" not in BLUE_PROMPT
    assert "try_credential" not in BLUE_PROMPT
    red_names = {entry["function"]["name"] for entry in RED_SCHEMAS}
    blue_names = {entry["function"]["name"] for entry in BLUE_SCHEMAS}
    assert red_names == {"scan_host", "http_request", "try_credential",
                         "read_file", "list_files", "report_finding"}
    assert blue_names == {"read_logs", "list_processes", "wait", "block_ip",
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
    assert call["max_tokens"] == 1536
    assert call["max_tokens"] <= 2048


def test_blue_wait_call_is_allowed(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    resp = _response("No new log lines, waiting this turn.",
                     [("wait", {})])
    client = _FakeClient(resp)
    agent = ModelBlue(client=client, exercise_id="ex-wait")
    action = agent.next_action(2, [])
    assert action["tool"] == "wait"
    assert "baseline" not in action["reasoning"].lower()
    assert orchestrator.run_tool("wait", {})["ok"] is True


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


def test_blue_length_fallback_logs_cause(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    resp = _response("", [], finish_reason="length")
    agent = ModelBlue(client=_FakeClient(resp))
    action = agent.next_action(16, [])
    assert action["tool"] == "read_logs"
    assert "finish_reason=length" in action["reasoning"]
    assert "token limit" in action["reasoning"].lower()


def test_empty_reply_includes_finish_reason(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    resp = _response("   ", [], finish_reason="stop")
    agent = ModelBlue(client=_FakeClient(resp))
    action = agent.next_action(2, [])
    assert "empty reply" in action["reasoning"].lower()
    assert "finish_reason=stop" in action["reasoning"]


def test_no_tool_call_includes_finish_reason(tmp_path, monkeypatch):
    _isolated_llm(tmp_path, monkeypatch)
    resp = _response("I will just think and not act.", [], finish_reason="stop")
    agent = ModelBlue(client=_FakeClient(resp))
    action = agent.next_action(2, [])
    assert "no tool call" in action["reasoning"].lower()
    assert "finish_reason=stop" in action["reasoning"]


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


def _mixed_history():
    return [
        {
            "id": "evt-0001", "timestamp": "2026-01-01T00:00:00+00:00",
            "actor": "red", "tool": "scan_host", "args": {"host": "web"},
            "result": {"ok": True, "host": "web", "reachable": True,
                       "ports": [8000]},
            "reasoning_summary": "probe web", "evidence_refs": [],
        },
        {
            "id": "evt-0002", "timestamp": "2026-01-01T00:00:01+00:00",
            "actor": "blue", "tool": "read_logs", "args": {"host": "web"},
            "result": {"ok": True, "host": "web", "lines": [
                {"id": "web:0", "line": "webapp listening on 8000"},
                {"id": "web:1", "line": "request from=1.2.3.4 GET /admin"},
            ]},
            "reasoning_summary": "inspect", "evidence_refs": [],
        },
    ]


def _own_section(user_text):
    after = user_text.split("Your previous moves:")[1]
    return after.split("Recent exercise moves:")[0]


def test_red_prompt_states_objective_and_pacing():
    assert "exercise secret" in RED_PROMPT
    assert "db" in RED_PROMPT
    assert "admin" in RED_PROMPT
    assert "default sign-in" in RED_PROMPT
    assert "quickly" in RED_PROMPT
    assert "changeme" in RED_PROMPT
    assert "password" in RED_PROMPT
    assert "root" in RED_PROMPT
    assert "toor" in RED_PROMPT
    assert "service accounts with password" in RED_PROMPT
    assert "hints worth acting on" in RED_PROMPT
    assert "no sign-in gate" in RED_PROMPT
    assert "do not try more credentials" in RED_PROMPT
    assert "act on the host it names" in RED_PROMPT
    assert "svc-report" not in RED_PROMPT
    assert "dbops" not in RED_PROMPT
    assert "config.ini" not in RED_PROMPT


def test_prompts_cover_app_host_without_answers():
    assert "app" in RED_PROMPT
    assert "app" in BLUE_PROMPT
    for prompt in (RED_PROMPT, BLUE_PROMPT):
        assert "svc-report" not in prompt
        assert "dbops" not in prompt
        assert "config.ini" not in prompt
        assert "dbops-fake-19" not in prompt
    assert _common.extract_evidence_ids("see app:3 for proof") == ["app:3"]


def test_blue_prompt_states_detect_report_contain():
    assert "report_finding" in BLUE_PROMPT
    assert "evidence_refs" in BLUE_PROMPT
    assert "isolate_host" in BLUE_PROMPT
    assert "patch_service" in BLUE_PROMPT
    assert "before the secret" in BLUE_PROMPT
    assert "wasted" in BLUE_PROMPT


def test_red_prompt_avoids_repeats_and_lists_after_signin():
    assert "Do not repeat" in RED_PROMPT
    assert "history" in RED_PROMPT.lower()
    assert "Once signed in" in RED_PROMPT
    assert "list_files" in RED_PROMPT


def test_blue_prompt_notes_business_cost_without_formula():
    assert "business cost" in BLUE_PROMPT
    assert "proportional" in BLUE_PROMPT
    assert "only what the evidence supports" in BLUE_PROMPT
    assert "business_weights" not in BLUE_PROMPT
    assert "downtime_cost" not in BLUE_PROMPT
    assert "blue_score" not in BLUE_PROMPT


def test_blue_prompt_states_secret_loss_outweighs_downtime():
    assert "exercise secret being read" in BLUE_PROMPT
    assert "total loss" in BLUE_PROMPT
    assert "outweighs any downtime cost" in BLUE_PROMPT


def test_blue_prompt_contain_immediately_on_host_activity():
    assert "signed in to a host" in BLUE_PROMPT
    assert "listing or reading files" in BLUE_PROMPT
    assert "contain that host immediately" in BLUE_PROMPT
    assert "rather than keep gathering evidence" in BLUE_PROMPT


def test_blue_prompt_check_data_host_early_and_restore_after():
    assert "most likely heading toward" in BLUE_PROMPT
    assert "data host" in BLUE_PROMPT
    assert "app and data hosts" in BLUE_PROMPT
    assert "worth doing early" in BLUE_PROMPT
    assert "consider restore_host" in BLUE_PROMPT
    assert "once red has stopped" in BLUE_PROMPT


def test_blue_prompt_urges_narrow_contain_patch_and_restore():
    assert "every turn" in BLUE_PROMPT
    assert "stays isolated or patched" in BLUE_PROMPT
    assert "contain narrowly" in BLUE_PROMPT
    assert "host red is actually using" in BLUE_PROMPT
    assert "prefer patch_service" in BLUE_PROMPT
    assert "close the hole" in BLUE_PROMPT
    assert "or the hole is closed" in BLUE_PROMPT


def test_blue_prompt_gives_no_scoring_formula():
    assert "business_weights" not in BLUE_PROMPT
    assert "downtime_cost" not in BLUE_PROMPT
    assert "downtime_pct" not in BLUE_PROMPT
    assert "blue_score" not in BLUE_PROMPT
    assert "percent" not in BLUE_PROMPT
    assert "100" not in BLUE_PROMPT


def test_red_messages_carry_remaining_turns_and_own_history():
    messages = red_build_messages(5, _mixed_history(), 20)
    user_text = messages[1]["content"]
    assert "turn 5 of 20" in user_text.lower()
    assert "Turns remaining (including this one): 16" in user_text
    own = _own_section(user_text)
    assert "scan_host" in own
    assert "read_logs" not in own


def test_blue_messages_carry_remaining_turns_and_own_history():
    messages = blue_build_messages(6, _mixed_history(), 20)
    user_text = messages[1]["content"]
    assert "turn 6 of 20" in user_text.lower()
    assert "Turns remaining (including this one): 15" in user_text
    own = _own_section(user_text)
    assert "read_logs" in own
    assert "web:1" in own
    assert "scan_host" not in own


def test_own_history_empty_when_no_moves():
    assert _common.summarize_own_history([], "red") == "No moves yet."
    assert _common.summarize_own_history(_mixed_history(), "green") == "No moves yet."


def test_orchestrator_passes_turn_cap_to_agents(tmp_path, monkeypatch):
    seen = {}

    class _Recording:
        actor = "red"

        def next_action(self, turn, history, max_turns=None):
            seen.setdefault("caps", []).append(max_turns)
            return None

    class _RecordingBlue(_Recording):
        actor = "blue"

    monkeypatch.setattr(orchestrator, "run_tool", lambda name, args: {"ok": True})
    orchestrator.run_exercise(_Recording(), _RecordingBlue(), max_turns=8,
                              run_path=tmp_path / "caps.jsonl")
    assert seen["caps"] == [8, 8]


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
    assert len(result["events"]) == 40
    assert result["score"]["turns_used"] == 40


def test_default_turn_cap_is_forty():
    assert orchestrator.MAX_TURNS == 40
    assert _common.DEFAULT_MAX_TURNS == 40
    messages = red_build_messages(1, [])
    assert "turn 1 of 40" in messages[1]["content"].lower()


def _red_event(tool, args, result, index=1):
    return {
        "id": f"evt-{index:04d}", "timestamp": "2026-01-01T00:00:00+00:00",
        "actor": "red", "tool": tool, "args": args, "result": result,
        "reasoning_summary": "probe", "evidence_refs": [],
    }


def _blue_event(tool, args, result, index=2):
    event = _red_event(tool, args, result, index)
    event["actor"] = "blue"
    return event


def test_own_history_keeps_listed_file_names():
    history = [_red_event("list_files", {"host": "db", "path": "/secret/"},
                          {"ok": True, "host": "db", "path": "/secret",
                           "files": ["exercise_secret.txt"]})]
    assert "exercise_secret.txt" in _common.summarize_own_history(history, "red")


def test_listed_file_name_reaches_later_red_prompt():
    history = [
        _red_event("list_files", {"host": "db", "path": "/secret/"},
                   {"ok": True, "host": "db", "path": "/secret",
                    "files": ["exercise_secret.txt"]}, 1),
        _blue_event("read_logs", {"host": "web"},
                    {"ok": True, "host": "web", "lines": []}, 2),
    ]
    user_text = red_build_messages(3, history)[1]["content"]
    assert "exercise_secret.txt" in user_text


def test_last_tool_result_reaches_blue_prompt():
    history = [_blue_event("read_logs", {"host": "web"},
                           {"ok": True, "host": "web", "lines": [
                               {"id": "web:0",
                                "line": "unique-probe-marker GET /admin"},
                           ]})]
    user_text = blue_build_messages(2, history)[1]["content"]
    assert "unique-probe-marker" in user_text


def test_blue_messages_show_isolated_hosts_with_turn_counts():
    history = [
        _red_event("scan_host", {"host": "web"},
                   {"ok": True, "host": "web"}, 1),
        _blue_event("isolate_host", {"host": "db"},
                    {"ok": True, "host": "db"}, 2),
        _red_event("scan_host", {"host": "app"},
                   {"ok": True, "host": "app"}, 3),
        _blue_event("isolate_host", {"host": "web"},
                    {"ok": True, "host": "web"}, 4),
        _red_event("scan_host", {"host": "db"},
                   {"ok": True, "host": "db"}, 5),
    ]
    user_text = blue_build_messages(6, history)[1]["content"]
    assert "Isolated hosts: db (4 turns), web (2 turns)." in user_text


def test_blue_messages_show_no_isolated_hosts_when_clear():
    assert "Isolated hosts: none." in blue_build_messages(1, [])[1]["content"]
    history = [
        _blue_event("isolate_host", {"host": "db"},
                    {"ok": True, "host": "db"}, 1),
        _blue_event("restore_host", {"host": "db"},
                    {"ok": True, "host": "db"}, 2),
    ]
    assert "Isolated hosts: none." in blue_build_messages(3, history)[1]["content"]


def test_blue_isolated_line_ignores_failed_actions():
    history = [
        _blue_event("isolate_host", {"host": "db"},
                    {"ok": False, "error": "host is not up"}, 1),
        _red_event("scan_host", {"host": "web"},
                   {"ok": True, "host": "web"}, 2),
    ]
    assert "Isolated hosts: none." in blue_build_messages(3, history)[1]["content"]


def test_blue_isolated_line_counts_patched_hosts_until_restore():
    history = [
        _blue_event("patch_service", {"host": "web", "service": "webapp"},
                    {"ok": True, "host": "web"}, 1),
        _red_event("scan_host", {"host": "web"},
                   {"ok": True, "host": "web"}, 2),
    ]
    assert "Isolated hosts: web (2 turns)." in blue_build_messages(3, history)[1]["content"]
    history.append(_blue_event("restore_host", {"host": "web"},
                               {"ok": True, "host": "web"}, 3))
    assert "Isolated hosts: none." in blue_build_messages(4, history)[1]["content"]


def test_own_history_keeps_http_hint_text():
    history = [_red_event("http_request", {"host": "web", "path": "/admin"},
                          {"ok": True, "host": "web", "status": 200,
                           "body": "admin page, data host db uses admin"})]
    assert "data host db" in _common.summarize_own_history(history, "red")


def test_own_history_keeps_read_file_path():
    history = [_red_event("read_file", {"host": "db", "path": "/secret/a.txt"},
                          {"ok": True, "host": "db", "path": "/secret/a.txt",
                           "content": "words"})]
    assert "/secret/a.txt" in _common.summarize_own_history(history, "red")


def test_compact_http_hint_truncated_to_200_chars():
    body = "HINT-" + "x" * 500
    history = [_red_event("http_request", {"host": "web", "path": "/admin"},
                          {"ok": True, "host": "web", "status": 200,
                           "body": body})]
    summary = _common.summarize_own_history(history, "red")
    assert body[:200] in summary
    assert body not in summary
    assert "x" * 201 not in summary


def test_last_result_truncated_to_1500_chars():
    history = [_red_event("read_file", {"host": "db", "path": "/secret/a.txt"},
                          {"ok": True, "host": "db", "path": "/secret/a.txt",
                           "content": "C" * 3000})]
    user_text = red_build_messages(2, history)[1]["content"]
    assert "C" * 100 in user_text
    assert "C" * 1501 not in user_text


def test_red_prompt_shows_known_state_checklist():
    history = [
        _red_event("scan_host", {"host": "web"},
                   {"ok": True, "host": "web", "reachable": True,
                    "ports": [8000]}, 1),
        _red_event("try_credential",
                   {"host": "web", "user": "admin", "password": "changeme"},
                   {"ok": True, "host": "web", "authenticated": False,
                    "note": "no sign-in gate on 'web'"}, 2),
        _red_event("try_credential",
                   {"host": "db", "user": "admin", "password": "changeme"},
                   {"ok": True, "host": "db", "authenticated": True}, 3),
    ]
    user_text = red_build_messages(4, history)[1]["content"]
    assert "hosts scanned: web" in user_text
    assert "hosts with no sign-in gate: web" in user_text
    assert "hosts authenticated: db" in user_text


def test_red_checklist_reports_none_when_empty():
    user_text = red_build_messages(1, [])[1]["content"]
    assert "hosts scanned: none" in user_text
    assert "hosts with no sign-in gate: none" in user_text
    assert "hosts authenticated: none" in user_text
