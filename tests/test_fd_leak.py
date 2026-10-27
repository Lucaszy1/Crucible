"""File descriptor leak tests.

The campaign crash came from unclosed Docker SDK and model clients:
every tool call built a fresh client and never closed it. These tests
prove owned clients close, borrowed clients stay open, and repeated
scripted exercises hold a flat fd count. No Docker or network here.
"""

import gc
import os
from types import SimpleNamespace

import pytest

from crucible import llm, sandbox
from crucible import orchestrator
from crucible.agents import ScriptedBlue, ScriptedRed

needs_dev_fd = pytest.mark.skipif(not os.path.isdir("/dev/fd"),
                                  reason="no /dev/fd on this platform")


class _FakeContainer:
    def __init__(self, networks=("crucible_range",)):
        self.attrs = {"NetworkSettings":
                      {"Networks": {name: {} for name in networks}}}
        self.client = None

    def exec_run(self, cmd, demux=True):
        return 0, (b"ok\n", b"")

    def logs(self, tail=None):
        return b"line one\nline two\n"

    def reload(self):
        pass


class _FakeNetwork:
    def __init__(self):
        self.calls = []

    def disconnect(self, container):
        self.calls.append(("disconnect", container))

    def connect(self, container):
        self.calls.append(("connect", container))


class _FakeContainers:
    def __init__(self, container):
        self._container = container

    def list(self, filters=None):
        return [self._container]


class _FakeNetworks:
    def __init__(self):
        self.networks = {}

    def get(self, name):
        return self.networks.setdefault(name, _FakeNetwork())


class _FakeDockerClient:
    def __init__(self, container=None):
        self.container = container or _FakeContainer()
        self.container.client = self
        self.containers = _FakeContainers(self.container)
        self.networks = _FakeNetworks()
        self.closed = 0

    def close(self):
        self.closed += 1


def _stub_docker(monkeypatch):
    made = []

    def factory():
        client = _FakeDockerClient()
        made.append(client)
        return client

    monkeypatch.setattr(sandbox, "_docker_client", factory)
    return made


def test_exec_closes_owned_client(monkeypatch):
    made = _stub_docker(monkeypatch)
    out = sandbox.exec("web", ["ps"])
    assert out["exit_code"] == 0 and out["output"] == "ok\n"
    assert len(made) == 1 and made[0].closed == 1


def test_logs_and_status_close_owned_clients(monkeypatch):
    made = _stub_docker(monkeypatch)
    assert sandbox.container_logs("db", tail=2) == ["line one", "line two"]
    assert sandbox.is_running("web") is True
    assert sandbox.is_isolated("web") is False
    assert len(made) == 3
    assert all(client.closed == 1 for client in made)


def test_isolate_and_reattach_close_owned_clients(monkeypatch):
    made = _stub_docker(monkeypatch)
    assert sandbox.isolate("app")["isolated"] is True
    assert sandbox.reattach("app")["isolated"] is False
    assert len(made) == 2
    assert all(client.closed == 1 for client in made)


def test_borrowed_docker_client_stays_open(monkeypatch):
    made = _stub_docker(monkeypatch)
    client = _FakeDockerClient()
    with sandbox._client_scope(client) as scoped:
        assert scoped is client
    assert client.closed == 0
    assert made == []


def _llm_response(text="hi"):
    message = SimpleNamespace(content=text, tool_calls=[])
    usage = SimpleNamespace(prompt_tokens=5, completion_tokens=3)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    return SimpleNamespace(choices=[choice], usage=usage)


class _FakeModelClient:
    def __init__(self):
        self.closed = 0
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        return _llm_response()

    def close(self):
        self.closed += 1


def test_chat_closes_owned_default_client(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(llm, "SPEND_FILE", tmp_path / "spend.jsonl")
    monkeypatch.setenv("LLM_DAILY_LIMIT_USD", "5")
    made = []
    monkeypatch.setattr(llm, "_default_client",
                        lambda: made.append(_FakeModelClient()) or made[-1])
    out = llm.chat("blue", [{"role": "user", "content": "go"}],
                   use_cache=False)
    assert out["content"] == "hi"
    assert len(made) == 1 and made[0].closed == 1


def test_chat_leaves_borrowed_client_open(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(llm, "SPEND_FILE", tmp_path / "spend.jsonl")
    monkeypatch.setenv("LLM_DAILY_LIMIT_USD", "5")
    client = _FakeModelClient()
    llm.chat("blue", [{"role": "user", "content": "go"}], client=client,
             use_cache=False)
    assert client.closed == 0


def _fake_sandbox(monkeypatch):
    monkeypatch.setattr(sandbox, "start", lambda: {"web": True})
    monkeypatch.setattr(sandbox, "stop", lambda: {"web": False})
    monkeypatch.setattr(sandbox, "is_running", lambda host: True)
    monkeypatch.setattr(sandbox, "is_isolated", lambda host: False)
    monkeypatch.setattr(sandbox, "container_logs",
                        lambda host, tail=100: ["webapp listening on 8000"])
    monkeypatch.setattr(
        sandbox, "exec",
        lambda host, cmd, timeout=30: {"exit_code": 0, "output": "",
                                       "stdout": "", "stderr": ""})
    monkeypatch.setattr(sandbox, "isolate",
                        lambda host: {"host": host, "isolated": True})
    monkeypatch.setattr(sandbox, "reattach",
                        lambda host: {"host": host, "isolated": False})


def _fd_count():
    gc.collect()
    return len(os.listdir("/dev/fd"))


@needs_dev_fd
def test_repeated_exercises_hold_fd_count_flat(tmp_path, monkeypatch):
    _fake_sandbox(monkeypatch)
    monkeypatch.setattr(orchestrator.events, "RUNS_DIR", tmp_path / "runs")
    orchestrator.run_exercise(ScriptedRed(), ScriptedBlue(), max_turns=10,
                              run_path=tmp_path / "warm.jsonl",
                              exercise_id="warm")
    before = _fd_count()
    for number in range(4):
        orchestrator.run_exercise(
            ScriptedRed(), ScriptedBlue(), max_turns=10,
            run_path=tmp_path / f"run-{number}.jsonl",
            exercise_id=f"run-{number}")
    assert _fd_count() == before
