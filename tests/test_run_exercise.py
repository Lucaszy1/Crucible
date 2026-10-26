"""Hermetic tests for the exercise runner script. No Docker here."""

from crucible import orchestrator, run_exercise, sandbox
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
                      "time_to_contain": None, "hosts_compromised": 0,
                      "compromised_hosts": [], "secret_exfiltrated": False},
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
                      "time_to_detect": None, "time_to_contain": None,
                      "hosts_compromised": 0, "compromised_hosts": [],
                      "secret_exfiltrated": False},
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
