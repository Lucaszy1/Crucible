"""Voice route tests through FastAPI's TestClient (no Docker, no spend)."""

import pytest
from fastapi.testclient import TestClient

from crucible import api, voice
from crucible.agents import (ModelBlue, ModelRed, ScriptedBlue, ScriptedRed)
from crucible.human_blue import (AIWithOverride, CommandQueue, HumanBlue,
                                 DEFAULT_DEMO_TRANSCRIPTS)
from crucible import voice_api


@pytest.fixture()
def client(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(api.events, "RUNS_DIR", runs)
    voice_api.reset_for_tests()
    with TestClient(api.app) as test_client:
        yield test_client
    voice_api.reset_for_tests()


def _attach(exercise_id="ex-1"):
    cmd_queue = CommandQueue()
    voice_api.attach_queue(cmd_queue, exercise_id)
    return cmd_queue


def test_command_queued_and_state(client):
    cmd_queue = _attach()
    res = client.post("/api/command",
                      json={"text": "isolate the database"})
    assert res.status_code == 200
    assert res.json()["status"] == "queued"
    assert res.json()["id"] == 1
    assert cmd_queue.pending() == 1
    state = client.get("/api/command/state").json()
    assert state == {"attached": True, "exercise_id": "ex-1",
                     "pending": 1, "last": None}


def test_command_voice_source(client):
    _attach()
    res = client.post("/api/command",
                      json={"text": "stand down", "source": "voice"})
    assert res.status_code == 200


def test_command_rejects_bad_input(client):
    _attach()
    assert client.post("/api/command", json={"text": "  "}).status_code == 422
    assert client.post("/api/command",
                       json={"text": "x" * 501}).status_code == 422
    assert client.post("/api/command", json={"text": "isolate db",
                                             "source": "radio"}).status_code == 422


def test_command_needs_live_exercise(client):
    res = client.post("/api/command", json={"text": "isolate db"})
    assert res.status_code == 409
    state = client.get("/api/command/state").json()
    assert state["attached"] is False


def test_demo_advances_offline(client):
    _attach()
    first = client.post("/api/command/demo", json={}).json()
    second = client.post("/api/command/demo", json={}).json()
    assert first["transcript"] == DEFAULT_DEMO_TRANSCRIPTS[0]
    assert second["transcript"] == DEFAULT_DEMO_TRANSCRIPTS[1]
    assert first["demo_index"] == 0
    picked = client.post("/api/command/demo", json={"index": 3}).json()
    assert picked["transcript"] == DEFAULT_DEMO_TRANSCRIPTS[3]
    assert client.post("/api/command/demo",
                       json={"index": 99}).status_code == 422


def test_audio_transcribes_and_queues(client, monkeypatch):
    _attach()
    calls = []

    def fake_transcribe(raw, exercise_id=None):
        calls.append(exercise_id)
        return {"text": "isolate the database", "seconds": 1.0,
                "cost_usd": 0.0001}

    monkeypatch.setattr(voice, "transcribe_file", fake_transcribe)
    wav = voice.make_probe_wav(seconds=1.0)
    res = client.post("/api/audio", content=wav,
                      headers={"Content-Type": "audio/wav"})
    assert res.status_code == 200
    body = res.json()
    assert body["transcript"] == "isolate the database"
    assert body["cost_usd"] == pytest.approx(0.0001)
    assert calls == ["ex-1"]
    state = client.get("/api/command/state").json()
    assert state["pending"] == 1


def test_audio_rejects_garbage_and_oversize(client):
    _attach()
    res = client.post("/api/audio", content=b"nope",
                      headers={"Content-Type": "audio/wav"})
    assert res.status_code == 422
    big = b"x" * (voice_api.MAX_UPLOAD_BYTES + 1)
    res = client.post("/api/audio", content=big,
                      headers={"Content-Type": "audio/wav"})
    assert res.status_code == 413


def test_command_rate_limit(client):
    _attach()
    for _ in range(30):
        res = client.post("/api/command", json={"text": "stand down"})
        assert res.status_code == 200
    res = client.post("/api/command", json={"text": "stand down"})
    assert res.status_code == 429


def test_build_agents_wires_human_modes():
    red, blue = api._build_agents("scripted", "x")
    assert isinstance(red, ScriptedRed) and isinstance(blue, ScriptedBlue)
    red, blue = api._build_agents("model", "x")
    assert isinstance(red, ModelRed) and isinstance(blue, ModelBlue)
    try:
        red, blue = api._build_agents("human", "x")
        assert isinstance(red, ScriptedRed)
        assert isinstance(blue, HumanBlue)
        red, blue = api._build_agents("ai_with_override", "x")
        assert isinstance(red, ScriptedRed)
        assert isinstance(blue, AIWithOverride)
    finally:
        voice_api.detach_queue()


def test_start_accepts_human_agents(client, monkeypatch):
    def fake(agents, exercise_id, run_path, publish, scenario_key="classic"):
        return {"events": [],
                "score": {"outcome": "stalemate"},
                "run_path": str(run_path), "exercise_cost": 0.0,
                "spend_cap_hit": False}

    monkeypatch.setattr(api, "_run_live_exercise", fake)
    for agents in ("human", "ai_with_override"):
        res = client.post("/api/exercise/start",
                          json={"agents": agents,
                                "exercise_id": f"v-{agents}"})
        assert res.status_code == 202, agents
        assert res.json()["agents"] == agents
    assert client.post("/api/exercise/start",
                       json={"agents": "rogue"}).status_code == 422


def test_serve_binds_localhost_only(monkeypatch):
    import uvicorn

    from crucible import serve

    seen = {}
    monkeypatch.setattr(uvicorn, "run",
                        lambda *args, **kwargs: seen.update(kwargs))
    assert serve.main(["--port", "8123"]) == 0
    assert seen["host"] == "127.0.0.1"
    assert seen["port"] == 8123
