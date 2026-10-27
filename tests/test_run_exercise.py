"""Hermetic tests for the exercise runner script. No Docker here."""

import json

from crucible import llm, orchestrator, run_exercise, sandbox
from crucible.agents import ModelBlue, ModelRed, ScriptedBlue, ScriptedRed


def _stubbed_run(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(sandbox, "start", lambda: {"web": True})
    monkeypatch.setattr(sandbox, "stop", lambda: seen.setdefault("stopped", True))
    monkeypatch.setattr(run_exercise.tools, "restore_host",
                        lambda host: {"ok": True, "host": host})
    monkeypatch.setattr(run_exercise.tools, "reset_state", lambda: None)

    def fake_exercise(red, blue, max_turns=12, run_path=None, exercise_id=None):
        seen["red"] = type(red).__name__
        seen["blue"] = type(blue).__name__
        seen["max_turns"] = max_turns
        path = str(tmp_path / "fake.jsonl")
        (tmp_path / "fake.jsonl").write_text("{}\n", encoding="utf-8")
        return {
            "events": [],
            "score": {"turns_used": 2, "total_events": 2, "time_to_detect": 2,
                      "detection_delay": 1, "time_to_contain": None,
                      "hosts_compromised": 0, "compromised_hosts": [],
                      "secret_exfiltrated": False, "outcome": "blue_held"},
            "run_path": path,
        }

    monkeypatch.setattr(orchestrator, "run_exercise", fake_exercise)
    monkeypatch.setattr(run_exercise.orchestrator, "run_exercise", fake_exercise)
    return seen


def test_runner_scripted_flag(tmp_path, monkeypatch, capsys):
    seen = _stubbed_run(monkeypatch, tmp_path)
    assert run_exercise.main(["--agents", "scripted"]) == 0
    assert seen["red"] == ScriptedRed.__name__
    assert seen["blue"] == ScriptedBlue.__name__
    assert seen["max_turns"] == 30
    assert seen["stopped"] is True
    out = capsys.readouterr().out
    assert "agents=scripted" in out
    assert "Log:" in out


def test_runner_model_flag(tmp_path, monkeypatch, capsys):
    seen = _stubbed_run(monkeypatch, tmp_path)
    assert run_exercise.main(["--agents", "model", "--exercise-id", "demo-1"]) == 0
    assert seen["red"] == ModelRed.__name__
    assert seen["blue"] == ModelBlue.__name__
    out = capsys.readouterr().out
    assert "agents=model" in out
    assert "Turns used" in out


def _evt(reasoning):
    return {"actor": "red", "tool": "scan_host", "args": {},
            "result": {"ok": True}, "reasoning_summary": reasoning,
            "evidence_refs": []}


def _stubbed_run_with_events(monkeypatch, tmp_path, events_list):
    seen = _stubbed_run(monkeypatch, tmp_path)

    def fake_exercise(red, blue, max_turns=12, run_path=None, exercise_id=None):
        path = str(tmp_path / "fake.jsonl")
        return {
            "events": events_list,
            "score": {"turns_used": len(events_list),
                      "total_events": len(events_list),
                      "time_to_detect": None, "detection_delay": None,
                      "time_to_contain": None, "hosts_compromised": 0,
                      "compromised_hosts": [], "secret_exfiltrated": False,
                      "outcome": "stalemate"},
            "run_path": path,
        }

    monkeypatch.setattr(orchestrator, "run_exercise", fake_exercise)
    monkeypatch.setattr(run_exercise.orchestrator, "run_exercise", fake_exercise)
    return seen


def test_runner_prints_driven_vs_fallback_counts(tmp_path, monkeypatch, capsys):
    events_list = [
        _evt("Probing the web host first."),
        _evt("Model reply hit the token limit (finish_reason=length), "
             "using baseline. Baseline move: probe web."),
        _evt("Reading logs for traces."),
    ]
    _stubbed_run_with_events(monkeypatch, tmp_path, events_list)
    assert run_exercise.main(["--agents", "model"]) == 0
    out = capsys.readouterr().out
    assert "Model-driven turns: 2" in out
    assert "fallback turns: 1" in out


def test_runner_strict_fails_on_fallback(tmp_path, monkeypatch, capsys):
    events_list = [
        _evt("Model returned an empty reply, using baseline. "
             "Baseline move: read web logs."),
    ]
    _stubbed_run_with_events(monkeypatch, tmp_path, events_list)
    assert run_exercise.main(["--agents", "model", "--strict"]) != 0
    out = capsys.readouterr().out
    assert "fallback turns: 1" in out


def test_runner_strict_passes_without_fallback(tmp_path, monkeypatch, capsys):
    _stubbed_run_with_events(monkeypatch, tmp_path,
                             [_evt("Probing the web host first.")])
    assert run_exercise.main(["--agents", "model", "--strict"]) == 0


def test_no_cache_flag_parses():
    assert run_exercise.parse_args(["--agents", "model"]).no_cache is False
    assert run_exercise.parse_args(["--agents", "model", "--no-cache"]).no_cache is True


def test_runner_no_cache_disables_agent_cache(tmp_path, monkeypatch):
    seen = {}
    _stubbed_run(monkeypatch, tmp_path)

    class _RecordingRed(ModelRed):
        def __init__(self, *args, **kwargs):
            seen["red_use_cache"] = kwargs.get("use_cache", True)
            super().__init__(*args, **kwargs)

    class _RecordingBlue(ModelBlue):
        def __init__(self, *args, **kwargs):
            seen["blue_use_cache"] = kwargs.get("use_cache", True)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(run_exercise, "ModelRed", _RecordingRed)
    monkeypatch.setattr(run_exercise, "ModelBlue", _RecordingBlue)
    assert run_exercise.main(["--agents", "model", "--no-cache"]) == 0
    assert seen["red_use_cache"] is False
    assert seen["blue_use_cache"] is False


def test_runner_summary_prints_outcome_and_delay(tmp_path, monkeypatch, capsys):
    _stubbed_run(monkeypatch, tmp_path)
    assert run_exercise.main(["--agents", "scripted"]) == 0
    out = capsys.readouterr().out
    assert "Outcome: blue_held" in out
    assert "Detection delay: 1 events" in out


def test_runner_summary_prints_downtime_blue_and_cost(tmp_path, monkeypatch, capsys):
    seen = _stubbed_run(monkeypatch, tmp_path)
    monkeypatch.setattr(llm, "SPEND_FILE", tmp_path / "spend.jsonl")

    def fake_exercise(red, blue, max_turns=12, run_path=None, exercise_id=None):
        path = str(tmp_path / "fake.jsonl")
        return {
            "events": [],
            "score": {"turns_used": 2, "total_events": 2, "time_to_detect": 2,
                      "detection_delay": 1, "time_to_contain": None,
                      "hosts_compromised": 0, "compromised_hosts": [],
                      "secret_exfiltrated": False, "outcome": "blue_held",
                      "downtime_cost": 9, "blue_score": 31},
            "run_path": path,
        }

    monkeypatch.setattr(orchestrator, "run_exercise", fake_exercise)
    monkeypatch.setattr(run_exercise.orchestrator, "run_exercise", fake_exercise)
    assert run_exercise.main(["--agents", "scripted"]) == 0
    out = capsys.readouterr().out
    assert "Downtime cost: 9" in out
    assert "Blue score: 31" in out
    assert "Estimated cost: $" in out
    assert seen["stopped"] is True


def test_exercise_cost_since_sums_new_entries(tmp_path, monkeypatch):
    spend = tmp_path / "spend.jsonl"
    spend.write_text(
        json.dumps({"exercise_id": "a", "cost_usd": 0.01}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(llm, "SPEND_FILE", spend)
    start = run_exercise._spend_line_count()
    assert start == 1
    with spend.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"exercise_id": "b", "cost_usd": 0.02}) + "\n")
        handle.write(json.dumps({"exercise_id": "b", "cost_usd": 0.03}) + "\n")
    assert run_exercise.exercise_cost_since(start) == 0.05


def test_exercise_cost_for_id_filters(tmp_path, monkeypatch):
    spend = tmp_path / "spend.jsonl"
    spend.write_text(
        "\n".join([
            json.dumps({"exercise_id": "keep", "cost_usd": 0.02}),
            json.dumps({"exercise_id": "drop", "cost_usd": 0.50}),
            json.dumps({"exercise_id": "keep", "cost_usd": 0.03}),
        ]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(llm, "SPEND_FILE", spend)
    assert run_exercise.exercise_cost_for_id("keep") == 0.05
    assert run_exercise.exercise_cost_for_id(None) == 0.0
    assert run_exercise.exercise_cost_for_id("missing") == 0.0


def _cap_event():
    return _evt(
        "Model call failed "
        "(daily model spend cap reached: $5.00 of $5.00 used), "
        "using baseline. Baseline move: probe web."
    )


def test_spend_cap_hit_detects_marker():
    assert run_exercise._spend_cap_hit([_cap_event()]) is True
    assert run_exercise._spend_cap_hit([_evt("Probing first.")]) is False
    assert run_exercise._spend_cap_hit([]) is False


def test_runner_warns_on_spend_cap(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LLM_DAILY_LIMIT_USD", "5")
    monkeypatch.setattr(llm, "spent_today", lambda *a, **k: 5.0)
    _stubbed_run_with_events(monkeypatch, tmp_path, [_cap_event()])
    assert run_exercise.main(["--agents", "model"]) == 0
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "spend cap" in err.lower()
    assert "$5.00 of $5.00" in err
    assert "scripted baseline" in err.lower()


def test_runner_strict_still_fails_on_spend_cap(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LLM_DAILY_LIMIT_USD", "5")
    monkeypatch.setattr(llm, "spent_today", lambda *a, **k: 5.0)
    _stubbed_run_with_events(monkeypatch, tmp_path, [_cap_event()])
    assert run_exercise.main(["--agents", "model", "--strict"]) == 2
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "$5.00 of $5.00" in err
