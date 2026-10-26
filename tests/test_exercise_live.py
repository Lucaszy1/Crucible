"""Live exercise test: boot the range, run one scripted exercise, tear down.

Run only with Docker available:
    pytest tests/test_exercise_live.py -v -m live

The test boots the sandbox itself, restores a clean state, runs the
scripted red and blue agents through the orchestrator with zero model
calls, checks the jsonl log and the score, then stops the range.
"""

import pytest

from crucible import events, orchestrator, sandbox, tools
from crucible.agents import ScriptedBlue, ScriptedRed

pytestmark = pytest.mark.live

BOOT_HINT = (
    "sandbox is not up; run: pip install -r requirements.txt, then "
    "check that the docker daemon is running"
)


def test_scripted_exercise_end_to_end(tmp_path, monkeypatch):
    import crucible.llm as llm

    def _boom(*args, **kwargs):
        raise AssertionError("scripted exercise must not call the model client")

    monkeypatch.setattr(llm, "chat", _boom)

    try:
        sandbox.start()
    except sandbox.SandboxError as exc:
        pytest.skip(f"{BOOT_HINT}: {exc}")

    try:
        tools.restore_host("web")
        tools.restore_host("db")
        run_path = tmp_path / "exercise.jsonl"
        result = orchestrator.run_exercise(
            ScriptedRed(), ScriptedBlue(), max_turns=10, run_path=run_path
        )

        assert run_path.exists(), "exercise did not write its jsonl log"
        logged = events.read_events(run_path)
        assert logged == result["events"]
        assert len(logged) == 10
        for entry in logged:
            assert set(events.REQUIRED_FIELDS) <= set(entry)
        actors = [entry["actor"] for entry in logged]
        assert actors == ["red", "blue"] * 5

        score = result["score"]
        assert score["secret_exfiltrated"] is True
        assert score["hosts_compromised"] >= 1
        assert score["time_to_detect"] is not None
        assert score["time_to_contain"] is not None
        assert score["turns_used"] == 10
    finally:
        tools.reset_state()
        try:
            sandbox.stop()
        except sandbox.SandboxError:
            pass
