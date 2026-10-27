"""Hermetic tests for the Baseten custom-client path. No network calls here.

Fake responses mimic each quirk scripts/probe_baseten.py measured on
https://inference.baseten.co/v1 with meta-models/muse-spark-open-1026:
content null on tool-call and length-cut replies, thinking in
message.reasoning_content, reasoning token counts in usage, string
tool arguments with ids, and accepted reasoning_effort/temperature.
"""

from types import SimpleNamespace

import pytest

from crucible import batch, bench, campaign, llm, run_exercise

OPEN_MODEL = "meta-models/muse-spark-open-1026"
BASETEN_URL = "https://inference.baseten.co/v1"


class _FakeCompletions:
    def __init__(self, parent):
        self._parent = parent

    def create(self, **kwargs):
        self._parent.calls.append(kwargs)
        return self._parent.next_response()


class _FakeClient:
    """Fake OpenAI-compatible client with a scripted response queue."""

    def __init__(self, responses, base_url=None):
        self.calls = []
        self._responses = list(responses)
        self.chat = SimpleNamespace(completions=_FakeCompletions(self))
        if base_url is not None:
            self.base_url = base_url

    def next_response(self):
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]


def _usage(prompt=10, completion=5, reasoning=0):
    details = SimpleNamespace(reasoning_tokens=reasoning)
    return SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        completion_tokens_details=details,
    )


def _response(message, finish_reason="stop", usage=None):
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage or _usage())


def _tool_call(name, arguments, call_id="chatcmpl-tool-1"):
    func = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(id=call_id, function=func)


def _baseten_tool_response():
    # Wire shape from probe b-single: content null, thinking in
    # reasoning_content, string arguments, present ids.
    message = SimpleNamespace(
        content=None,
        reasoning_content="Need to isolate host app.",
        tool_calls=[_tool_call("isolate_host", '{"host": "app"}')],
    )
    return _response(message, "tool_calls", _usage(637, 150, 81))


def _baseten_length_response():
    # Wire shape from probe a-length: finish length, content null,
    # reasoning cut to a stub, no calls.
    message = SimpleNamespace(content=None, reasoning_content="We", tool_calls=None)
    return _response(message, "length", _usage(153, 4, 1))


@pytest.fixture()
def isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(llm, "SPEND_FILE", tmp_path / "spend.jsonl")
    monkeypatch.setenv("LLM_DAILY_LIMIT_USD", "10")
    return tmp_path


def _baseten_client(responses):
    return _FakeClient(responses, base_url=BASETEN_URL)


def test_baseten_tool_call_content_null_and_reasoning(isolated_storage):
    client = _baseten_client([_baseten_tool_response()])
    out = llm.chat(OPEN_MODEL, [{"role": "user", "content": "hi"}],
                   tools=[{"type": "function"}], client=client,
                   use_cache=False)
    assert out["content"] == ""
    assert out["reasoning"] == "Need to isolate host app."
    assert out["finish_reason"] == "tool_calls"
    assert out["tool_calls"] == [
        {"id": "chatcmpl-tool-1", "name": "isolate_host",
         "arguments": '{"host": "app"}'}
    ]
    assert out["usage"]["reasoning_tokens"] == 81
    assert out["host"] == "baseten"


def test_baseten_length_cut_has_no_content_or_calls(isolated_storage):
    client = _baseten_client([_baseten_length_response()])
    out = llm.chat(OPEN_MODEL, [{"role": "user", "content": "hi"}],
                   client=client, use_cache=False)
    assert out["content"] == ""
    assert out["tool_calls"] == []
    assert out["finish_reason"] == "length"
    assert out["reasoning"] == "We"


def test_baseten_dict_arguments_pass_through(isolated_storage):
    message = SimpleNamespace(
        content=None, reasoning_content="",
        tool_calls=[_tool_call("isolate_host", {"host": "app"})],
    )
    client = _baseten_client([_response(message, "tool_calls")])
    out = llm.chat(OPEN_MODEL, [{"role": "user", "content": "hi"}],
                   client=client, use_cache=False)
    assert out["tool_calls"][0]["arguments"] == {"host": "app"}


def test_baseten_none_arguments_pass_through(isolated_storage):
    message = SimpleNamespace(
        content=None, reasoning_content="",
        tool_calls=[_tool_call("wait", None)],
    )
    client = _baseten_client([_response(message, "tool_calls")])
    out = llm.chat(OPEN_MODEL, [{"role": "user", "content": "hi"}],
                   client=client, use_cache=False)
    assert out["tool_calls"][0]["arguments"] is None


def test_baseten_keeps_reasoning_effort_and_temperature(isolated_storage):
    # The probe found both parameters accepted (and reasoning_effort
    # collapses thinking cost), so the client must keep sending them.
    client = _baseten_client([_baseten_tool_response()])
    llm.chat(OPEN_MODEL, [{"role": "user", "content": "hi"}],
             client=client, use_cache=False, temperature=0.5,
             reasoning_effort="low", max_tokens=3000)
    sent = client.calls[0]
    assert sent["temperature"] == 0.5
    assert sent["reasoning_effort"] == "low"
    assert sent["max_tokens"] == 3000


def test_baseten_raises_small_max_tokens_to_floor(isolated_storage):
    client = _baseten_client([_baseten_tool_response()])
    llm.chat(OPEN_MODEL, [{"role": "user", "content": "hi"}],
             client=client, use_cache=False, max_tokens=1536)
    assert client.calls[0]["max_tokens"] == 3000


def test_baseten_leaves_large_max_tokens_alone(isolated_storage):
    client = _baseten_client([_baseten_tool_response()])
    llm.chat(OPEN_MODEL, [{"role": "user", "content": "hi"}],
             client=client, use_cache=False, max_tokens=4096)
    assert client.calls[0]["max_tokens"] == 4096


def test_meta_client_keeps_caller_max_tokens(isolated_storage):
    client = _FakeClient([_baseten_tool_response()])  # no base_url: Meta path
    llm.chat("muse-spark-1.3", [{"role": "user", "content": "hi"}],
             client=client, use_cache=False, max_tokens=1536)
    assert client.calls[0]["max_tokens"] == 1536


def test_meta_response_without_reasoning_stays_empty(isolated_storage):
    message = SimpleNamespace(content="hello", tool_calls=[])
    client = _FakeClient([_response(message, "stop")])
    out = llm.chat("muse-spark-1.3", [{"role": "user", "content": "hi"}],
                   client=client, use_cache=False)
    assert out["reasoning"] == ""
    assert out["usage"]["reasoning_tokens"] == 0
    assert out["host"] == "meta"


def test_baseten_spend_costs_zero_but_logs_tokens(isolated_storage):
    client = _baseten_client([_baseten_tool_response()])
    llm.chat(OPEN_MODEL, [{"role": "user", "content": "hi"}],
             client=client, use_cache=False, exercise_id="ex-1")
    assert llm.spent_today() == 0.0
    totals = llm.baseten_tokens_today()
    assert totals["calls"] == 1
    assert totals["prompt_tokens"] == 637
    assert totals["completion_tokens"] == 150
    assert totals["reasoning_tokens"] == 81
    assert totals["total_tokens"] == 787


def test_spend_guard_ignores_baseten_keeps_meta(isolated_storage):
    llm.record_spend(model=OPEN_MODEL, input_tokens=100000,
                     output_tokens=100000, host="baseten",
                     reasoning_tokens=90000)
    assert llm.spent_today() == 0.0
    llm.check_spend_guard()  # must not raise on Baseten volume alone
    llm.record_spend(model="muse-spark-1.3", input_tokens=1000,
                     output_tokens=0)
    assert llm.spent_today() > 0.0


def test_baseten_cache_hit_keeps_host(isolated_storage):
    tool = _baseten_tool_response()
    tool_message = tool.choices[0].message
    tool_message.content = "isolating app"
    client = _baseten_client([tool])
    messages = [{"role": "user", "content": "hi"}]
    first = llm.chat(OPEN_MODEL, messages, client=client, use_cache=True)
    assert first["cached"] is False
    second = llm.chat(OPEN_MODEL, messages, client=client, use_cache=True)
    assert second["cached"] is True
    assert len(client.calls) == 1
    assert llm.spent_today() == 0.0
    assert llm.baseten_tokens_today()["calls"] == 2


def test_baseten_tokens_since_counts_one_window(isolated_storage):
    llm.record_spend(model="muse-spark-1.3", input_tokens=5, output_tokens=5)
    start = sum(1 for _ in llm.SPEND_FILE.read_text(encoding="utf-8").splitlines()
                if _.strip())
    llm.record_spend(model=OPEN_MODEL, input_tokens=100, output_tokens=50,
                     host="baseten", reasoning_tokens=40)
    totals = run_exercise.baseten_tokens_since(start)
    assert totals["calls"] == 1
    assert totals["total_tokens"] == 150
    assert totals["reasoning_tokens"] == 40
    line = run_exercise.format_baseten_line(totals)
    assert "150 total" in line
    assert "price unknown" in line
    assert run_exercise.format_baseten_line(
        {"calls": 0, "total_tokens": 0}) is None


@pytest.mark.parametrize("module,argv", [
    (run_exercise, []),
    (batch, ["--count", "1", "--prefix", "x"]),
    (campaign, ["--episodes", "1", "--name", "x", "--memory", "on"]),
    (bench, ["--name", "x"]),
])
def test_blue_override_flags_exist(module, argv):
    args = module.parse_args(argv + [
        "--blue-model", OPEN_MODEL,
        "--blue-base-url", BASETEN_URL,
        "--blue-api-key-env", "BASETEN_API_KEY",
    ])
    assert args.blue_model == OPEN_MODEL
    assert args.blue_base_url == BASETEN_URL
    assert args.blue_api_key_env == "BASETEN_API_KEY"


@pytest.mark.parametrize("module,argv", [
    (run_exercise, []),
    (batch, ["--count", "1", "--prefix", "x"]),
    (campaign, ["--episodes", "1", "--name", "x", "--memory", "on"]),
    (bench, ["--name", "x"]),
])
def test_blue_override_flags_default_off(module, argv):
    args = module.parse_args(argv)
    assert args.blue_model is None
    assert args.blue_base_url is None
    assert args.blue_api_key_env is None
