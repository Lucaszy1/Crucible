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
                     "pending": 1, "last": None, "mode": None,
                     "latched": False, "spend_usd": 0.0,
                     "spend_cap_usd": voice_api.HUMAN_SESSION_CAP_USD}


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
    res = client.post("/api/audio", content=b"nope",
                      headers={"Content-Type": "text/plain"})
    assert res.status_code == 415


def test_audio_rejects_wrong_content_type(client, monkeypatch):
    _attach()
    calls = []

    def fake_transcribe(raw, exercise_id=None):
        calls.append(raw)
        return {"text": "stand down", "seconds": 1.0, "cost_usd": 0.0}

    monkeypatch.setattr(voice, "transcribe_file", fake_transcribe)
    wav = voice.make_probe_wav(seconds=1.0)
    res = client.post("/api/audio", content=wav,
                      headers={"Content-Type": "text/plain"})
    assert res.status_code == 415
    res = client.post("/api/audio", content=wav)
    assert res.status_code == 415
    res = client.post("/api/audio", content=wav,
                      headers={"Content-Type": "audio/x-wav"})
    assert res.status_code == 200
    assert calls and calls[0] == wav


def test_command_rate_limit(client):
    _attach()
    for _ in range(30):
        res = client.post("/api/command", json={"text": "stand down"})
        assert res.status_code == 200
    res = client.post("/api/command", json={"text": "stand down"})
    assert res.status_code == 429


def test_build_agents_wires_human_modes():
    try:
        red, blue = api._build_agents("scripted", "x")
        assert isinstance(red, ScriptedRed)
        assert isinstance(blue, AIWithOverride)
        assert isinstance(blue._inner, ScriptedBlue)
        red, blue = api._build_agents("model", "x")
        assert isinstance(red, ModelRed)
        assert isinstance(blue, AIWithOverride)
        assert isinstance(blue._inner, ModelBlue)
        red, blue = api._build_agents("human", "x")
        assert isinstance(red, ScriptedRed)
        assert isinstance(blue, HumanBlue)
        red, blue = api._build_agents("ai_with_override", "x")
        assert isinstance(red, ScriptedRed)
        assert isinstance(blue, AIWithOverride)
    finally:
        voice_api.detach_queue()


def test_build_agents_attaches_queue_every_mode():
    for agents in ("scripted", "model", "human", "ai_with_override"):
        try:
            api._build_agents(agents, f"x-{agents}")
            state = voice_api.command_state()
            assert state["attached"] is True
            assert state["exercise_id"] == f"x-{agents}"
            assert state["mode"] == agents
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


def _live_inventory(monkeypatch):
    inventory = {"hosts": {
        "willow": {"role": "app"},
        "wren": {"role": "workstation"},
    }, "crown_jewel_host": "willow"}
    services = {"willow": "reportapi", "wren": "desktop"}
    monkeypatch.setattr(voice_api, "default_inventory",
                        lambda: inventory)
    monkeypatch.setattr(voice_api, "default_services",
                        lambda: services)


def test_parse_previews_without_queueing(client, monkeypatch):
    cmd_queue = _attach()
    _live_inventory(monkeypatch)
    res = client.post("/api/command/parse",
                      json={"text": "isolate the willow host"})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["tool"] == "isolate_host"
    assert body["args"] == {"host": "willow"}
    assert body["ai_used"] is False
    assert cmd_queue.pending() == 0


def test_parse_control_words_stay_out_of_tools(client, monkeypatch):
    _attach()
    _live_inventory(monkeypatch)
    res = client.post("/api/command/parse", json={"text": "take over"})
    assert res.json()["status"] == "control"
    assert res.json()["control"] == "take-over"
    res = client.post("/api/command/parse", json={"text": "hand back"})
    assert res.json()["control"] == "hand-back"


def test_parse_refusal_names_allowlist(client, monkeypatch):
    _attach()
    _live_inventory(monkeypatch)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    res = client.post("/api/command/parse",
                      json={"text": "delete everything on willow"})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "refused"
    assert body["ai_used"] is False


def test_parse_model_only_when_offline_stuck(client, monkeypatch):
    from crucible import commands

    _attach()
    _live_inventory(monkeypatch)
    monkeypatch.setenv("MODEL_API_KEY", "test-key")
    calls = []

    def fake_model(text, inventory, services, **kwargs):
        calls.append(text)
        return {"status": "ok", "tool": "read_logs",
                "args": {"host": "wren", "n": 50},
                "reasoning": "model reading", "cost_usd": 0.01}

    monkeypatch.setattr(commands, "parse_with_model", fake_model)
    clear = client.post("/api/command/parse",
                        json={"text": "read logs on wren"}).json()
    assert clear["status"] == "ok" and clear["ai_used"] is False
    assert calls == []
    vague = client.post("/api/command/parse",
                        json={"text": "hmm, maybe look around"}).json()
    assert vague["status"] == "ok" and vague["ai_used"] is True
    assert vague["cost_usd"] == pytest.approx(0.01)
    assert calls == ["hmm, maybe look around"]
    state = client.get("/api/command/state").json()
    assert state["spend_usd"] == pytest.approx(0.01)


def test_parse_capped_session_skips_model(client, monkeypatch):
    from crucible import commands

    _attach()
    _live_inventory(monkeypatch)
    monkeypatch.setenv("MODEL_API_KEY", "test-key")
    voice_api._add_spend(voice_api.HUMAN_SESSION_CAP_USD)

    def boom(*args, **kwargs):
        raise AssertionError("model must not run past the cap")

    monkeypatch.setattr(commands, "parse_with_model", boom)
    body = client.post("/api/command/parse",
                       json={"text": "hmm, maybe look around"}).json()
    assert body["ai_used"] is False
    assert "cap" in body.get("note", "")


def test_takeover_queues_markers(client):
    cmd_queue = _attach()
    res = client.post("/api/takeover", json={"take": True})
    assert res.json()["status"] == "queued"
    assert cmd_queue.pop(timeout=0)["text"] == "take over"
    res = client.post("/api/takeover", json={"take": False})
    assert res.json()["take"] is False
    assert cmd_queue.pop(timeout=0)["text"] == "hand back"


def test_takeover_noop_in_human_mode(client):
    cmd_queue = CommandQueue()
    voice_api.attach_queue(cmd_queue, "ex-h", "human")
    res = client.post("/api/takeover", json={"take": True})
    assert res.json()["status"] == "noop"
    assert cmd_queue.pending() == 0


def test_state_reports_latch_from_last_result(client):
    cmd_queue = _attach()
    assert client.get("/api/command/state").json()["latched"] is False
    cmd_queue.note_result({"status": "taken-over"})
    assert client.get("/api/command/state").json()["latched"] is True
    cmd_queue.note_result({"status": "handed-back"})
    assert client.get("/api/command/state").json()["latched"] is False


def test_command_accepts_previewed_action(client):
    cmd_queue = _attach()
    res = client.post("/api/command", json={
        "text": "do the previewed thing",
        "action": {"tool": "isolate_host", "args": {"host": "willow"}}})
    assert res.status_code == 200
    receipt = cmd_queue.pop(timeout=0)
    assert receipt["resolved"] == {"tool": "isolate_host",
                                   "args": {"host": "willow"}}
    bad = client.post("/api/command", json={
        "text": "sneaky", "action": {"tool": "format_disk", "args": {}}})
    assert bad.status_code == 422


def test_audio_session_cap_blocks(client, monkeypatch):
    _attach()
    voice_api._add_spend(voice_api.HUMAN_SESSION_CAP_USD)
    wav = voice.make_probe_wav(seconds=1.0)
    res = client.post("/api/audio", content=wav,
                      headers={"Content-Type": "audio/wav"})
    assert res.status_code == 429
