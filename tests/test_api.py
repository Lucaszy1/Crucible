"""Hermetic API tests for the Operations Center backend.

All tests run through FastAPI's TestClient with a temporary runs
directory and a fake exercise runner, so no Docker, network, or model
calls are needed.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import crucible.api as api
from crucible import events, llm

FIXTURE = Path(__file__).parent / "fixtures" / "cap_test_events.jsonl"


@pytest.fixture()
def tmp_runs(tmp_path, monkeypatch):
    """Redirect the runs directory (and spend log) to a temp folder."""
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(events, "RUNS_DIR", runs)
    monkeypatch.setattr(llm, "SPEND_FILE", runs / "spend.jsonl")
    shutil.copy(FIXTURE, runs / "demo.jsonl")
    return runs


@pytest.fixture()
def client(tmp_runs):
    """TestClient with live state reset before and after each test."""
    with api._state_lock:
        api._live_state.update(
            {
                "running": False,
                "exercise_id": None,
                "agents": None,
                "run_id": None,
                "started_at": None,
                "spend_at_start": 0.0,
                "warning": None,
                "last_outcome": None,
                "last_exercise_cost": 0.0,
                "last_warning": None,
            }
        )
        api._live_buffer.clear()
        api._live_subscribers.clear()
    with TestClient(api.app) as test_client:
        yield test_client
    with api._state_lock:
        api._live_state["running"] = False
        api._live_buffer.clear()
        api._live_subscribers.clear()


def _wait_idle(client, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not client.get("/api/status").json()["running"]:
            return True
        time.sleep(0.05)
    return False


def _sse_messages(text):
    messages = []
    for chunk in text.split("\n\n"):
        for line in chunk.splitlines():
            if line.startswith("data: "):
                messages.append(json.loads(line[len("data: ") :]))
    return messages


def test_runs_list_reports_outcome_and_score(client):
    body = client.get("/api/runs").json()
    assert body["runs"] == [
        {
            "id": "demo",
            "events": 30,
            "outcome": "red_succeeded",
            "blue_score": 0,
            "justified_pct": None,
            "unsupported_count": 0,
            "invalid_refs_count": 0,
            "mean_audit_score": None,
            "mtime": body["runs"][0]["mtime"],
        }
    ]
    assert body["runs"][0]["mtime"]


def test_runs_list_skips_spend_log_and_broken_files(client, tmp_runs):
    (tmp_runs / "spend.jsonl").write_text('{"day": "2026-10-27"}\n')
    (tmp_runs / "broken.jsonl").write_text("not json\n")
    ids = [run["id"] for run in client.get("/api/runs").json()["runs"]]
    assert "spend" not in ids
    broken = [run for run in client.get("/api/runs").json()["runs"] if run["id"] == "broken"][0]
    assert broken["outcome"] is None
    assert broken["blue_score"] is None


def test_run_fetch_returns_events_and_score(client):
    body = client.get("/api/runs/demo").json()
    assert body["id"] == "demo"
    assert len(body["events"]) == 30
    assert body["events"][0]["id"] == "evt-0001"
    assert body["score"]["outcome"] == "red_succeeded"
    assert body["score"]["time_to_contain"] == 16


def test_run_fetch_rejects_unknown_and_traversal(client):
    assert client.get("/api/runs/nope").status_code == 404
    assert client.get("/api/runs/..%2F..%2Fnope").status_code == 404
    assert client.get("/api/runs/spend").status_code == 404


def test_stream_replays_events_then_done(client):
    res = client.get("/api/stream/demo?speed=100")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    messages = _sse_messages(res.text)
    assert len(messages) == 31
    assert messages[0]["index"] == 1
    assert messages[0]["total"] == 30
    assert messages[0]["event"]["tool"] == "scan_host"
    assert messages[-1]["done"] is True
    assert messages[-1]["score"]["outcome"] == "red_succeeded"


def test_stream_unknown_run_is_404(client):
    assert client.get("/api/stream/nope?speed=100").status_code == 404


def test_status_reports_idle_and_spend(client):
    body = client.get("/api/status").json()
    assert body["running"] is False
    assert body["spent_today"] == 0.0
    assert body["daily_limit"] > 0
    assert body["estimated_exercise_cost"] > 0


def _canned_event(number, actor="red", tool="scan_host"):
    return {
        "id": f"evt-{number:04d}",
        "timestamp": "2026-10-27T00:00:00+00:00",
        "actor": actor,
        "tool": tool,
        "args": {"host": "web"},
        "result": {"ok": True, "host": "web"},
        "reasoning_summary": "canned turn",
        "evidence_refs": [],
    }


def test_start_with_fake_runner_streams_live_events(client, monkeypatch):
    seen = []

    def fake(agents, exercise_id, run_path, publish):
        first = _canned_event(1)
        second = _canned_event(2, actor="blue", tool="read_logs")
        publish({"type": "event", "event": first})
        publish({"type": "event", "event": second})
        seen.append((agents, exercise_id))
        return {
            "events": [first, second],
            "score": {"outcome": "blue_held", "blue_score": 99.5},
            "run_path": str(run_path),
            "exercise_cost": 0.0,
            "spend_cap_hit": False,
        }

    monkeypatch.setattr(api, "_run_live_exercise", fake)
    res = client.post("/api/exercise/start", json={"agents": "scripted"})
    assert res.status_code == 202
    assert seen and seen[0][0] == "scripted"
    assert _wait_idle(client)

    messages = _sse_messages(client.get("/api/live").text)
    kinds = [message["type"] for message in messages]
    assert kinds[0] == "status"
    assert kinds.count("event") == 2
    assert kinds[-1] == "done"
    assert messages[-1]["score"]["outcome"] == "blue_held"

    status = client.get("/api/status").json()
    assert status["running"] is False
    assert status["last_outcome"] == "blue_held"


def test_start_rejects_bad_agents_value(client):
    res = client.post("/api/exercise/start", json={"agents": "rogue"})
    assert res.status_code == 422


def test_only_one_exercise_at_a_time(client, monkeypatch):
    release = threading.Event()

    def blocking(agents, exercise_id, run_path, publish):
        assert release.wait(timeout=10)
        return {
            "events": [],
            "score": {"outcome": "stalemate"},
            "run_path": str(run_path),
            "exercise_cost": 0.0,
            "spend_cap_hit": False,
        }

    monkeypatch.setattr(api, "_run_live_exercise", blocking)
    first = client.post("/api/exercise/start", json={"agents": "scripted"})
    assert first.status_code == 202
    try:
        second = client.post("/api/exercise/start", json={"agents": "scripted"})
        assert second.status_code == 409
        assert client.get("/api/status").json()["running"] is True
    finally:
        release.set()
    assert _wait_idle(client)


def test_model_start_warns_when_spend_cap_blocks(client, monkeypatch):
    def fake(agents, exercise_id, run_path, publish):
        return {
            "events": [],
            "score": {"outcome": "stalemate"},
            "run_path": str(run_path),
            "exercise_cost": 0.0,
            "spend_cap_hit": False,
        }

    monkeypatch.setattr(api, "_run_live_exercise", fake)
    monkeypatch.setattr(
        llm, "check_spend_guard", lambda *args, **kwargs: (_ for _ in ()).throw(
            llm.SpendLimitError("daily model spend cap reached")
        ),
    )
    res = client.post("/api/exercise/start", json={"agents": "model"})
    assert res.status_code == 202
    assert "spend cap" in (res.json()["warning"] or "").lower()
    assert _wait_idle(client)


def test_failed_exercise_publishes_error(client, monkeypatch):
    def failing(agents, exercise_id, run_path, publish):
        raise RuntimeError("sandbox boot failed: demo outage")

    monkeypatch.setattr(api, "_run_live_exercise", failing)
    res = client.post("/api/exercise/start", json={"agents": "scripted"})
    assert res.status_code == 202
    assert _wait_idle(client)
    messages = _sse_messages(client.get("/api/live").text)
    assert messages[-1]["type"] == "error"
    assert "demo outage" in messages[-1]["text"]


def test_dashboard_index_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "Operations Center" in res.text


def _campaign_fixture(tmp_runs):
    directory = tmp_runs / "campaign-mem1"
    directory.mkdir()
    (directory / "summary.json").write_text(json.dumps({
        "name": "mem1", "memory": "on", "agents": "model",
        "episodes": [
            {"episode": "mem1-1", "episode_no": 1, "outcome": "blue_held",
             "blue_score": 70.0, "downtime_pct": 5.0, "detection_delay": 3,
             "time_to_contain": 4, "cost": 0.1, "playbook_rule_count": 1,
             "referee_model": "muse-wonder-1.0"},
            {"episode": "mem1-2", "episode_no": 2, "outcome": "blue_held",
             "blue_score": 90.0, "downtime_pct": 2.0, "detection_delay": 1,
             "time_to_contain": 3, "cost": 0.1, "playbook_rule_count": 2,
             "referee_model": "muse-wonder-1.0"},
        ],
    }), encoding="utf-8")
    (directory / "playbook.json").write_text(json.dumps({
        "episode": "mem1-2", "model_used": "muse-wonder-1.0",
        "rules": [{"rule": "Contain quickly.", "episode": "mem1-1"}],
    }), encoding="utf-8")
    return directory


def test_campaigns_list_reports_counts_and_mean(client, tmp_runs):
    _campaign_fixture(tmp_runs)
    body = client.get("/api/campaigns").json()
    assert body["campaigns"] == [
        {"name": "mem1", "memory": "on", "agents": "model",
         "episodes": 2, "mean_blue_score": 80.0}
    ]


def test_campaign_detail_returns_episodes_and_playbook(client, tmp_runs):
    _campaign_fixture(tmp_runs)
    body = client.get("/api/campaigns/mem1").json()
    assert body["name"] == "mem1"
    assert len(body["episodes"]) == 2
    assert body["playbook"] == [{"rule": "Contain quickly.",
                                 "episode": "mem1-1"}]


def test_campaign_detail_rejects_unknown(client, tmp_runs):
    _campaign_fixture(tmp_runs)
    assert client.get("/api/campaigns/nope").status_code == 404
    assert client.get("/api/campaigns/..%2F..%2Fnope").status_code == 404
