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
