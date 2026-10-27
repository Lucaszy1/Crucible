"""Tests for EventWriter reuse guards."""

import pytest

from crucible.events import EventWriter, read_events


def _log(writer, tool="wait"):
    return writer.log(actor="blue", tool=tool, args={},
                      result={"ok": True}, reasoning_summary="r",
                      evidence_refs=[])


def test_fresh_log_numbers_from_one(tmp_path):
    writer = EventWriter(tmp_path / "run.jsonl")
    assert _log(writer)["id"] == "evt-0001"
    assert len(read_events(tmp_path / "run.jsonl")) == 1


def test_existing_log_refuses_second_game(tmp_path):
    path = tmp_path / "run.jsonl"
    _log(EventWriter(path))
    with pytest.raises(FileExistsError):
        EventWriter(path)


def test_resume_continues_numbering(tmp_path):
    path = tmp_path / "run.jsonl"
    _log(EventWriter(path))
    _log(EventWriter(path, resume=True))
    writer = EventWriter(path, resume=True)
    assert [event["id"] for event in writer.events] == ["evt-0001",
                                                        "evt-0002"]
    assert _log(writer)["id"] == "evt-0003"
    assert len(read_events(path)) == 3


def test_overwrite_truncates(tmp_path):
    path = tmp_path / "run.jsonl"
    _log(EventWriter(path))
    writer = EventWriter(path, overwrite=True)
    assert writer.events == []
    assert _log(writer)["id"] == "evt-0001"
    assert len(read_events(path)) == 1
