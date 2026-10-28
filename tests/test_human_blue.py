"""Human blue tests: queue, turn clock, override, inert hook proof."""

from pathlib import Path

import pytest

from crucible import events, human_blue, orchestrator, tools
from crucible.agents import ScriptedBlue, ScriptedRed
from crucible.human_blue import AIWithOverride, CommandQueue, HumanBlue

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "human_inert_golden.jsonl"


def _inventory():
    return {"hosts": {
        "web": {"role": "web portal"},
        "app": {"role": "app"},
        "db": {"role": "database"},
        "workstation": {"role": "workstation"},
    }, "crown_jewel_host": "db"}


def _services():
    return {"web": "webapp", "app": "reportapi", "db": "fakestore",
            "workstation": "desktop"}


def _human(cmd_queue, **kwargs):
    params = {"inventory": _inventory(), "services": _services(),
              "turn_timeout_s": 0.05}
    params.update(kwargs)
    return HumanBlue(cmd_queue, **params)


# -- queue --------------------------------------------------------------


def test_queue_round_trip_and_last_result():
    cmd_queue = CommandQueue()
    assert cmd_queue.pending() == 0
    assert cmd_queue.last_result() is None
    receipt = cmd_queue.push("isolate the database", source="voice")
    assert receipt["id"] == 1
    assert cmd_queue.pending() == 1
    taken = cmd_queue.pop(timeout=0.1)
    assert taken["text"] == "isolate the database"
    assert taken["source"] == "voice"
    assert cmd_queue.pop(timeout=0.01) is None
    cmd_queue.note_result({"id": 1, "status": "ok"})
    assert cmd_queue.last_result() == {"id": 1, "status": "ok"}


def test_queue_rejects_empty_and_bad_source():
    cmd_queue = CommandQueue()
    with pytest.raises(ValueError):
        cmd_queue.push("   ")
    with pytest.raises(ValueError):
        cmd_queue.push("isolate db", source="radio")


# -- human turns ----------------------------------------------------------


def test_human_executes_order_with_provenance():
    cmd_queue = CommandQueue()
    cmd_queue.push("isolate the database", source="voice")
    action = _human(cmd_queue).next_action(1, [])
    assert action["tool"] == "isolate_host"
    assert action["args"] == {"host": "db"}
    assert action["_human_source"] == "voice"
    assert action["_human_transcript"] == "isolate the database"
    result = cmd_queue.last_result()
    assert result["status"] == "ok"
    assert result["tool"] == "isolate_host"


def test_human_clarify_becomes_wait_with_question():
    cmd_queue = CommandQueue()
    cmd_queue.push("isolate", source="text")
    action = _human(cmd_queue).next_action(1, [])
    assert action["tool"] == "wait"
    assert "Which host" in action["args"]["reason"]
    assert action["_human_source"] == "text"
    assert cmd_queue.last_result()["status"] == "clarify"


def test_human_refusal_becomes_wait_with_reason():
    cmd_queue = CommandQueue()
    cmd_queue.push("delete the logs on db", source="text")
    action = _human(cmd_queue).next_action(1, [])
    assert action["tool"] == "wait"
    assert "outside the blue tool surface" in action["args"]["reason"]
    assert cmd_queue.last_result()["status"] == "refused"


def test_human_timeout_holds_without_provenance():
    cmd_queue = CommandQueue()
    action = _human(cmd_queue).next_action(1, [])
    assert action == {"tool": "wait",
                      "args": {"reason": human_blue.HOLD_REASON},
                      "reasoning": f"Blue holds: {human_blue.HOLD_REASON}.",
                      "evidence_refs": []}
    assert cmd_queue.last_result() is None


def test_human_script_replays_offline():
    cmd_queue = CommandQueue()
    blue = _human(cmd_queue, script=["isolate the database", "stand down"])
    first = blue.next_action(1, [])
    second = blue.next_action(3, [])
    assert (first["tool"], second["tool"]) == ("isolate_host", "wait")
    assert first["_human_source"] == "text"
    assert blue.next_action(5, []) is None


# -- override ---------------------------------------------------------------


class _Inner:
    actor = "blue"

    def __init__(self):
        self.calls = []

    def next_action(self, turn, history, max_turns=None, alerts=None):
        self.calls.append({"turn": turn, "alerts": alerts})
        return {"tool": "read_logs", "args": {"host": "web", "n": 50},
                "reasoning": "ai sweep", "evidence_refs": []}


def _override(cmd_queue, inner=None, **kwargs):
    params = {"inventory": _inventory(), "services": _services(),
              "turn_timeout_s": 0.05}
    params.update(kwargs)
    return AIWithOverride(inner or _Inner(), cmd_queue, **params)


def test_override_delegates_to_ai_when_quiet():
    cmd_queue = CommandQueue()
    inner = _Inner()
    action = _override(cmd_queue, inner).next_action(
        1, [], max_turns=40, alerts=[{"rule": "x"}])
    assert action["reasoning"] == "ai sweep"
    assert inner.calls == [{"turn": 1, "alerts": [{"rule": "x"}]}]


def test_override_interrupt_latches_and_holds():
    cmd_queue = CommandQueue()
    inner = _Inner()
    blue = _override(cmd_queue, inner)
    cmd_queue.push("isolate the database", source="voice")
    action = blue.next_action(1, [])
    assert action["tool"] == "isolate_host"
    assert blue.latched is True
    assert inner.calls == []
    held = blue.next_action(3, [])
    assert held["tool"] == "wait"
    assert blue.latched is True


def test_override_hand_back_returns_control():
    cmd_queue = CommandQueue()
    inner = _Inner()
    blue = _override(cmd_queue, inner)
    cmd_queue.push("isolate the database")
    blue.next_action(1, [])
    assert blue.latched is True
    cmd_queue.push("hand back", source="voice")
    action = blue.next_action(3, [])
    assert action["reasoning"] == "ai sweep"
    assert blue.latched is False
    assert inner.calls[-1]["turn"] == 3
    assert cmd_queue.last_result()["status"] == "handed-back"


def test_override_ignores_hand_back_when_unlatched():
    cmd_queue = CommandQueue()
    inner = _Inner()
    blue = _override(cmd_queue, inner)
    cmd_queue.push("hand back")
    action = blue.next_action(1, [])
    assert action["reasoning"] == "ai sweep"
    assert blue.latched is False


def test_human_actor_is_human():
    blue = _human(CommandQueue())
    assert blue.actor == "human"


def test_previewed_action_revalidated_at_run_time():
    cmd_queue = CommandQueue()
    blue = _human(cmd_queue)
    cmd_queue.push("do the previewed thing",
                   resolved={"tool": "isolate_host",
                             "args": {"host": "db"}})
    action = blue.next_action(1, [])
    assert action["tool"] == "isolate_host"
    assert action["args"] == {"host": "db"}
    assert action["_actor"] == "human"
    cmd_queue.push("sneaky text",
                   resolved={"tool": "isolate_host",
                             "args": {"host": "moon"}})
    held = blue.next_action(3, [])
    assert held["tool"] == "wait"
    assert "Which host" in held["args"]["reason"]


def test_take_over_latches_without_action():
    cmd_queue = CommandQueue()
    inner = _Inner()
    blue = _override(cmd_queue, inner)
    cmd_queue.push("take over")
    assert blue.next_action(1, []) is None
    assert blue.latched is True
    assert inner.calls == []
    assert cmd_queue.last_result()["status"] == "taken-over"


def test_take_over_order_marks_human_actor():
    cmd_queue = CommandQueue()
    inner = _Inner()
    blue = _override(cmd_queue, inner)
    cmd_queue.push("take over")
    blue.next_action(1, [])
    cmd_queue.push("isolate the database", source="voice")
    action = blue.next_action(3, [])
    assert action["tool"] == "isolate_host"
    assert action["_actor"] == "human"
    held = blue.next_action(5, [])
    assert held["tool"] == "wait"
    assert held["_actor"] == "human"


# -- loop hook --------------------------------------------------------------


def test_provenance_lands_in_event_log(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "begin_episode",
                        lambda: {"ok": True, "attempts": 1, "checks": {}})
    monkeypatch.setattr(orchestrator, "run_tool",
                        lambda tool, args: {"ok": True})
    cmd_queue = CommandQueue()
    cmd_queue.push("isolate the database", source="voice")
    blue = _human(cmd_queue)
    result = orchestrator.run_exercise(
        ScriptedRed(), blue, max_turns=2,
        run_path=tmp_path / "human.jsonl", exercise_id="human-1",
        audit="off", sensors="off")
    logged = [event for event in result["events"]
              if event.get("actor") == "human"]
    assert len(logged) == 1
    assert logged[0]["tool"] == "isolate_host"
    assert logged[0]["source"] == "voice"
    assert logged[0]["transcript"] == "isolate the database"
    assert result["score"]["assisted"] is True
    stored = events.read_events(tmp_path / "human.jsonl")
    assert stored[-1]["source"] == "voice"
    assert "_human_source" not in logged[0]["args"]


def test_writer_extra_none_keeps_bytes(tmp_path):
    plain = events.EventWriter(tmp_path / "a.jsonl")
    plain.log(actor="blue", tool="wait", args={"reason": "x"},
              result={"ok": True})
    assert "source" not in plain.events[0]


def test_runs_without_mode_match_golden_bytes(tmp_path, monkeypatch):
    """The inert-hook proof: no human keys, golden bytes out."""
    monkeypatch.setattr(events, "utc_now_iso",
                        lambda: "2026-01-01T00:00:00+00:00")
    monkeypatch.setattr(tools, "begin_episode",
                        lambda: {"ok": True, "attempts": 1, "checks": {}})
    monkeypatch.setattr(orchestrator, "run_tool",
                        lambda tool, args: {"ok": True, "tool": tool})
    run_path = tmp_path / "replay.jsonl"
    orchestrator.run_exercise(
        ScriptedRed(), ScriptedBlue(), max_turns=8, run_path=run_path,
        exercise_id="golden", audit="off", sensors="on", seed="golden-1")
    assert run_path.read_bytes() == GOLDEN.read_bytes()
