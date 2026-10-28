"""Hermetic tests for the blue reasoning-effort control. No network here.

Covers the --blue-reasoning-effort flag on all four CLIs, the request
each value produces (omit sends no field at all), red staying on the
unchanged default path, first-event recording, and the bench CSV
column plus report label.
"""

from types import SimpleNamespace

import pytest

from crucible import batch, bench, campaign, llm, orchestrator, run_exercise, tools
from crucible.agents import ModelBlue, ModelRed


def _response(name, args):
    func = SimpleNamespace(name=name, arguments=args)
    call = SimpleNamespace(id="call_0", function=func)
    message = SimpleNamespace(content="Working.", tool_calls=[call])
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    return SimpleNamespace(choices=[choice], usage=usage)


class _FakeClient:
    def __init__(self, response):
        self.calls = []
        self._response = response
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


@pytest.fixture()
def isolated_llm(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(llm, "SPEND_FILE", tmp_path / "spend.jsonl")
    monkeypatch.setenv("LLM_DAILY_LIMIT_USD", "5")
    monkeypatch.delenv("LLM_REASONING_EFFORT", raising=False)
    return tmp_path


@pytest.mark.parametrize("effort", ["minimal", "low", "medium", "high"])
def test_blue_effort_value_reaches_request(isolated_llm, effort):
    client = _FakeClient(_response("read_logs", '{"host": "web"}'))
    ModelBlue(client=client, reasoning_effort=effort).next_action(2, [])
    assert client.calls[0]["reasoning_effort"] == effort


def test_blue_effort_omit_sends_no_field(isolated_llm):
    client = _FakeClient(_response("read_logs", '{"host": "web"}'))
    ModelBlue(client=client, reasoning_effort="omit").next_action(2, [])
    assert "reasoning_effort" not in client.calls[0]


def test_blue_effort_default_stays_low(isolated_llm):
    client = _FakeClient(_response("read_logs", '{"host": "web"}'))
    ModelBlue(client=client).next_action(2, [])
    assert client.calls[0]["reasoning_effort"] == "low"


def test_red_stays_on_default_effort(isolated_llm):
    client = _FakeClient(_response("scan_host", '{"host": "web"}'))
    ModelRed(client=client).next_action(1, [])
    assert client.calls[0]["reasoning_effort"] == "low"


def test_red_explicit_effort_passes_through(isolated_llm):
    client = _FakeClient(_response("scan_host", '{"host": "web"}'))
    ModelRed(client=client, reasoning_effort="medium").next_action(1, [])
    assert client.calls[0]["reasoning_effort"] == "medium"


def test_first_event_records_effort(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "run_tool",
                        lambda name, args: {"ok": True})
    monkeypatch.setattr(tools, "begin_episode",
                        lambda: {"ok": True, "attempts": 1, "checks": {}})
    from crucible.agents import ScriptedBlue, ScriptedRed
    result = orchestrator.run_exercise(
        ScriptedRed(), ScriptedBlue(), max_turns=2,
        run_path=tmp_path / "effort.jsonl", blue_reasoning_effort="high")
    assert result["events"][0]["args"]["blue_reasoning_effort"] == "high"
    plain = orchestrator.run_exercise(
        ScriptedRed(), ScriptedBlue(), max_turns=2,
        run_path=tmp_path / "plain.jsonl")
    assert plain["events"][0]["args"]["blue_reasoning_effort"] == "default"


@pytest.mark.parametrize("module,argv", [
    (run_exercise, []),
    (batch, ["--count", "1", "--prefix", "x"]),
    (campaign, ["--episodes", "1", "--name", "x", "--memory", "on"]),
    (bench, ["--name", "x"]),
])
def test_effort_flag_parses_on_all_clis(module, argv):
    args = module.parse_args(argv + ["--blue-reasoning-effort", "medium"])
    assert args.blue_reasoning_effort == "medium"
    assert module.parse_args(argv).blue_reasoning_effort is None


def test_bench_csv_appends_effort_column():
    assert bench.CSV_FIELDS[-7] == "blue_reasoning_effort"
    assert bench.CSV_FIELDS[-6:-3] == ("scenario_id", "scenario_seed",
                                       "scenario_difficulty")
    assert bench.CSV_FIELDS[-3] == "blue_profile"
    assert bench.CSV_FIELDS[-2] == "assisted"
    assert bench.CSV_FIELDS[-1] == "degraded"
    assert bench.CSV_FIELDS[:-7] == (
        "episode", "cell", "red_profile", "sensors", "memory", "seed",
        "outcome", "intrusion", "contained_after_intrusion", "secret_read",
        "relevant_detection_delay", "detection_delay", "time_to_contain",
        "downtime_pct", "blue_score", "cost",
    )


def test_bench_report_labels_effort(tmp_path, capsys):
    bench_dict = {"name": "eff", "rows": [], "dir": tmp_path,
                  "blue_reasoning_effort": "high"}
    bench._report(bench_dict)
    out = capsys.readouterr().out
    assert "(name=eff, blue_reasoning_effort=high)" in out
    bench._report({"name": "eff", "rows": [], "dir": tmp_path})
    out = capsys.readouterr().out
    assert "blue_reasoning_effort=default" in out
