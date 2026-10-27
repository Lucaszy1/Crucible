"""Hermetic tests for episode-scoped container logs.

The scope recorded at episode start hides earlier lines from
container_logs and read_logs, so ids restart at 0 per episode.
"""

import pytest

from crucible import sandbox, tools


@pytest.fixture(autouse=True)
def clean_scope():
    sandbox.clear_log_offsets()
    tools.reset_state()
    yield
    sandbox.clear_log_offsets()
    tools.reset_state()


def test_scope_hides_earlier_lines_and_keeps_tail(monkeypatch):
    stream = ["old-1", "old-2"]
    monkeypatch.setattr(sandbox, "_raw_logs",
                        lambda host, tail=None: list(stream))
    sandbox.note_log_offsets()
    stream.extend(["new-1", "new-2", "new-3"])
    assert sandbox.container_logs("db", tail=None) == ["new-1", "new-2", "new-3"]
    assert sandbox.container_logs("db", tail=2) == ["new-2", "new-3"]
    assert sandbox.container_logs("db") == ["new-1", "new-2", "new-3"]


def test_scope_clamps_when_stream_shrank(monkeypatch):
    monkeypatch.setattr(sandbox, "_raw_logs",
                        lambda host, tail=None: ["a", "b", "c"])
    sandbox.note_log_offsets()
    monkeypatch.setattr(sandbox, "_raw_logs",
                        lambda host, tail=None: ["x"])
    assert sandbox.container_logs("db", tail=None) == []


def test_no_scope_reads_full_stream(monkeypatch):
    seen = {}
    monkeypatch.setattr(sandbox, "_raw_logs",
                        lambda host, tail=None: seen.setdefault(
                            "tail", tail) or ["a", "b"])
    assert sandbox.container_logs("db", tail=None) == ["a", "b"]
    assert seen["tail"] is None


def test_read_logs_ids_restart_at_scope_start(monkeypatch):
    stream = ["old-1", "old-2"]
    monkeypatch.setattr(sandbox, "_raw_logs",
                        lambda host, tail=None: list(stream))
    sandbox.note_log_offsets()
    stream.append("fresh")
    out = tools.read_logs("db", 50)
    assert out["lines"] == [{"id": "db:0", "line": "fresh"}]


def test_reset_state_clears_scope(monkeypatch):
    monkeypatch.setattr(sandbox, "_raw_logs",
                        lambda host, tail=None: ["a"])
    sandbox.note_log_offsets()
    assert sandbox.log_offsets() == {"web": 1, "app": 1, "db": 1,
                                     "workstation": 1}
    tools.reset_state()
    assert sandbox.log_offsets() == {}
    assert sandbox.container_logs("db", tail=None) == ["a"]
