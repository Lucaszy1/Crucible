"""Unit tests for the model client wrapper. No network calls here."""

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
