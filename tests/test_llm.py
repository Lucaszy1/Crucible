"""Unit tests for the model client wrapper. No network calls here."""

import json
import os
from datetime import date
from types import SimpleNamespace

import pytest

from crucible import llm


class _FakeCompletions:
    def __init__(self, parent):
        self._parent = parent

    def create(self, **kwargs):
        self._parent.calls.append(kwargs)
        message = SimpleNamespace(content="hello", tool_calls=[])
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


class _FakeClient:
    def __init__(self):
        self.calls = []
        self.chat = SimpleNamespace(completions=_FakeCompletions(self))


@pytest.fixture()
def isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(llm, "SPEND_FILE", tmp_path / "spend.jsonl")
    monkeypatch.setenv("LLM_DAILY_LIMIT_USD", "10")
    return tmp_path


def test_model_routing():
    assert llm.model_for("red") == "muse-spark-1.3"
    assert llm.model_for("blue-open") == "meta-models/muse-spark-open-1026"
    assert llm.model_for("dev") == "muse-spark-1.3-contributor"
    assert llm.model_for("auditor") == "muse-wonder-1.0"
    with pytest.raises(ValueError):
        llm.model_for("no-such-role")


def test_estimate_cost_scales_with_tokens():
    one = llm.estimate_cost("muse-spark-1.3", 1000, 1000)
    two = llm.estimate_cost("muse-spark-1.3", 2000, 2000)
    assert one > 0
    assert two == pytest.approx(one * 2)


def test_spend_roundtrip(isolated_storage):
    assert llm.spent_today() == 0.0
    entry = llm.record_spend(model="muse-spark-1.3", input_tokens=1000, output_tokens=0)
    assert entry["day"] == date.today().isoformat()
    assert llm.spent_today() == pytest.approx(entry["cost_usd"])


def test_guard_blocks_past_cap(isolated_storage, monkeypatch):
    monkeypatch.setenv("LLM_DAILY_LIMIT_USD", "0.0001")
    llm.record_spend(model="muse-spark-1.3", input_tokens=100000, output_tokens=0)
    with pytest.raises(llm.SpendLimitError):
        llm.check_spend_guard()
    with pytest.raises(llm.SpendLimitError):
        llm.chat("dev", [{"role": "user", "content": "hi"}], client=_FakeClient())


def test_chat_caches_identical_calls(isolated_storage):
    client = _FakeClient()
    messages = [{"role": "user", "content": "hi"}]
    first = llm.chat("dev", messages, client=client, exercise_id="ex1")
    second = llm.chat("dev", messages, client=client, exercise_id="ex1")
    assert first["cached"] is False
    assert second["cached"] is True
    assert len(client.calls) == 1
    assert llm.spent_today() == pytest.approx(
        llm.estimate_cost("muse-spark-1.3-contributor", 10, 5)
    )


def test_chat_skips_cache_for_new_prompts(isolated_storage):
    client = _FakeClient()
    llm.chat("dev", [{"role": "user", "content": "one"}], client=client)
    llm.chat("dev", [{"role": "user", "content": "two"}], client=client)
    assert len(client.calls) == 2


def test_chat_cache_isolated_by_exercise_id(isolated_storage):
    client = _FakeClient()
    messages = [{"role": "user", "content": "hi"}]
    llm.chat("dev", messages, client=client, exercise_id="ex1")
    llm.chat("dev", messages, client=client, exercise_id="ex2")
    assert len(client.calls) == 2
    third = llm.chat("dev", messages, client=client, exercise_id="ex1")
    assert third["cached"] is True
    assert len(client.calls) == 2


def test_tool_calls_normalized(isolated_storage):
    func = SimpleNamespace(name="scan_host", arguments='{"host": "web"}')
    call = SimpleNamespace(id="call_1", function=func)
    message = SimpleNamespace(content="", tool_calls=[call])
    usage = SimpleNamespace(prompt_tokens=3, completion_tokens=4)
    response = SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)

    class _OneShotClient(_FakeClient):
        def __init__(self, resp):
            super().__init__()
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=lambda **k: resp)
            )

    out = llm.chat("red", [{"role": "user", "content": "scan"}], client=_OneShotClient(response))
    assert out["tool_calls"] == [
        {"id": "call_1", "name": "scan_host", "arguments": '{"host": "web"}'}
    ]


def test_chat_sends_default_reasoning_effort_low(isolated_storage, monkeypatch):
    monkeypatch.delenv("LLM_REASONING_EFFORT", raising=False)
    client = _FakeClient()
    llm.chat("dev", [{"role": "user", "content": "hi"}], client=client)
    assert client.calls[0]["reasoning_effort"] == "low"


def test_chat_reasoning_effort_env_override(isolated_storage, monkeypatch):
    monkeypatch.setenv("LLM_REASONING_EFFORT", "minimal")
    client = _FakeClient()
    llm.chat("dev", [{"role": "user", "content": "hi"}], client=client)
    assert client.calls[0]["reasoning_effort"] == "minimal"


def test_chat_reasoning_effort_explicit_wins(isolated_storage, monkeypatch):
    monkeypatch.setenv("LLM_REASONING_EFFORT", "minimal")
    client = _FakeClient()
    llm.chat("dev", [{"role": "user", "content": "hi"}], client=client,
             reasoning_effort="low")
    assert client.calls[0]["reasoning_effort"] == "low"


def _chat_response(content="", tool_calls=(), finish_reason="stop"):
    calls = []
    for index, (name, args) in enumerate(tool_calls):
        if not isinstance(args, str):
            args = json.dumps(args)
        func = SimpleNamespace(name=name, arguments=args)
        calls.append(SimpleNamespace(id=f"call_{index}", function=func))
    message = SimpleNamespace(content=content, tool_calls=calls)
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


class _ScriptedClient:
    def __init__(self, response):
        self.calls = []
        self._response = response
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create)
        )

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


def _repeat_chat(client, tools=None):
    messages = [{"role": "user", "content": "act"}]
    llm.chat("red", messages, client=client, tools=tools)
    return llm.chat("red", messages, client=client, tools=tools)


def test_chat_reports_finish_reason(isolated_storage):
    out = llm.chat("red", [{"role": "user", "content": "act"}],
                   client=_ScriptedClient(_chat_response(finish_reason="length")))
    assert out["finish_reason"] == "length"


def test_chat_skips_cache_on_length_finish(isolated_storage):
    client = _ScriptedClient(_chat_response("cut off", finish_reason="length"))
    _repeat_chat(client)
    assert len(client.calls) == 2


def test_chat_skips_cache_on_empty_content(isolated_storage):
    client = _ScriptedClient(_chat_response(""))
    _repeat_chat(client)
    assert len(client.calls) == 2


def test_chat_skips_cache_when_tool_call_missing(isolated_storage):
    tools = [{"type": "function", "function": {"name": "scan_host"}}]
    client = _ScriptedClient(_chat_response("thinking out loud"))
    _repeat_chat(client, tools=tools)
    assert len(client.calls) == 2


def test_chat_still_caches_valid_tool_call(isolated_storage):
    tools = [{"type": "function", "function": {"name": "scan_host"}}]
    resp = _chat_response("Scanning first.",
                          [("scan_host", {"host": "web"})])
    client = _ScriptedClient(resp)
    second = _repeat_chat(client, tools=tools)
    assert len(client.calls) == 1
    assert second["cached"] is True


def test_load_env_file_reads_temp_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_DAILY_LIMIT_USD", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text("LLM_DAILY_LIMIT_USD=12.5\n", encoding="utf-8")
    assert llm._load_env_file(env_path) is True
    assert os.environ.get("LLM_DAILY_LIMIT_USD") == "12.5"
    assert llm.daily_limit_usd() == pytest.approx(12.5)


def test_load_env_file_does_not_override(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_DAILY_LIMIT_USD", "7")
    env_path = tmp_path / ".env"
    env_path.write_text("LLM_DAILY_LIMIT_USD=99\n", encoding="utf-8")
    llm._load_env_file(env_path)
    assert os.environ.get("LLM_DAILY_LIMIT_USD") == "7"
    assert llm.daily_limit_usd() == pytest.approx(7.0)


def test_temperature_defaults_per_role(monkeypatch):
    monkeypatch.delenv("LLM_TEMPERATURE", raising=False)
    assert llm.temperature_for("red") == 0.8
    assert llm.temperature_for("blue") == 0.5
    assert llm.temperature_for("auditor") == 0.7


def test_temperature_env_override_wins(monkeypatch):
    monkeypatch.setenv("LLM_TEMPERATURE", "0.25")
    assert llm.temperature_for("red") == 0.25
    assert llm.temperature_for("blue") == 0.25


def test_temperature_bad_env_falls_back(monkeypatch):
    monkeypatch.setenv("LLM_TEMPERATURE", "hot")
    assert llm.temperature_for("red") == 0.8


def test_chat_resolves_temperature_per_role(isolated_storage, monkeypatch):
    monkeypatch.delenv("LLM_TEMPERATURE", raising=False)
    client = _FakeClient()
    messages = [{"role": "user", "content": "hi"}]
    llm.chat("red", messages, client=client, exercise_id="ex-t")
    llm.chat("blue", messages, client=client, exercise_id="ex-t")
    assert client.calls[0]["temperature"] == 0.8
    assert client.calls[1]["temperature"] == 0.5


def test_chat_explicit_temperature_wins(isolated_storage):
    client = _FakeClient()
    messages = [{"role": "user", "content": "hi"}]
    llm.chat("red", messages, client=client, exercise_id="ex-t",
             temperature=0.0)
    assert client.calls[0]["temperature"] == 0.0


def test_chat_cache_splits_on_seed(isolated_storage):
    client = _FakeClient()
    messages = [{"role": "user", "content": "hi"}]
    llm.chat("red", messages, client=client, exercise_id="ex-s",
             seed="pair-1")
    llm.chat("red", messages, client=client, exercise_id="ex-s",
             seed="pair-2")
    llm.chat("red", messages, client=client, exercise_id="ex-s",
             seed="pair-2")
    assert len(client.calls) == 2


def test_chat_cache_unchanged_without_seed(isolated_storage):
    client = _FakeClient()
    messages = [{"role": "user", "content": "hi"}]
    llm.chat("red", messages, client=client, exercise_id="ex-s")
    llm.chat("red", messages, client=client, exercise_id="ex-s")
    assert len(client.calls) == 1
