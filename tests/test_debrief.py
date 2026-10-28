"""Tests for the exercise debrief report (crucible/debrief.py).

Layer 1 is deterministic and model-free: two recorded runs under runs/
(sample.jsonl and day3d-5.jsonl) pin its output, schema, citations,
and vocabulary. Layer 2 tests use a stub model client, never the
network. No Docker is needed anywhere here.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import crucible.api as api
from crucible import debrief, events, llm

RUNS_DIR = Path(__file__).resolve().parent.parent / "runs"
RECORDED = ["sample", "day3d-5"]


def _load(run_id: str) -> list:
    path = RUNS_DIR / f"{run_id}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _dump(report: dict) -> str:
    return json.dumps(report, sort_keys=True)


# Layer 1: deterministic output on recorded runs.


def test_layer1_is_deterministic_on_both_recorded_runs():
    for run_id in RECORDED:
        found = _load(run_id)
        first = debrief.build_debrief(found, run_id=run_id)
        second = debrief.build_debrief(found, run_id=run_id)
        assert first == second
        assert _dump(first) == _dump(second)


def test_layer1_schema_on_both_recorded_runs():
    for run_id in RECORDED:
        report = debrief.build_debrief(_load(run_id), run_id=run_id)
        assert report["run_id"] == run_id
        assert report["layer"] == 1
        assert report["model_used"] == "none"
        assert report["outcome"] in ("contained", "file_read", "quiet")
        assert isinstance(report["outcome_text"], str) and report["outcome_text"]
        assert 4 <= len(report["timeline"]) <= 6
        for moment in report["timeline"]:
            assert set(moment) == {"event_id", "title", "detail"}
            assert moment["event_id"] and moment["title"] and moment["detail"]
        assert isinstance(report["strengths"], list) and report["strengths"]
        for item in report["strengths"]:
            assert set(item) == {"text", "event_ids"}
            assert item["text"] and item["event_ids"]
        assert 2 <= len(report["weaknesses"]) <= 4
        for item in report["weaknesses"]:
            assert set(item) == {"text", "recommendation", "event_ids"}
            assert item["text"] and item["recommendation"] and item["event_ids"]
        assert isinstance(report["markdown"], str) and report["markdown"]


def test_layer1_sample_key_facts():
    report = debrief.build_debrief(_load("sample"), run_id="sample")
    assert report["outcome"] == "contained"
    assert report["detection_delay_events"] == 3
    assert report["detection_event_id"] == "evt-0005"
    assert report["containment_event_id"] == "evt-0007"
    assert "granite" in (report["containment_move"] or "")
    timeline_ids = [moment["event_id"] for moment in report["timeline"]]
    for expected in ("evt-0005", "evt-0006", "evt-0007"):
        assert expected in timeline_ids


def test_layer1_day3d5_key_facts():
    report = debrief.build_debrief(_load("day3d-5"), run_id="day3d-5")
    assert report["outcome"] == "file_read"
    assert report["detection_delay_events"] == 3
    assert report["detection_event_id"] == "evt-0004"
    assert report["containment_event_id"] == "evt-0006"
    timeline_ids = [moment["event_id"] for moment in report["timeline"]]
    for expected in ("evt-0004", "evt-0005", "evt-0006", "evt-0007"):
        assert expected in timeline_ids


def test_layer1_citations_all_exist_on_both_recorded_runs():
    for run_id in RECORDED:
        found = _load(run_id)
        report = debrief.build_debrief(found, run_id=run_id)
        assert debrief.validate_citations(report, found) == []


def test_layer1_vocabulary_and_secrets_on_both_recorded_runs():
    banned = ["Blue", "Red", "episode", "Episode", "quarantine",
              "attack", "Attack", "battle", "enemy", "kill", "war ",
              "\u2014", "\u2013"]
    for run_id in RECORDED:
        found = _load(run_id)
        report = debrief.build_debrief(found, run_id=run_id)
        text = report["markdown"]
        lowered = text.lower()
        for word in ("exercise", "defender", "containment"):
            assert word in lowered, f"{word} missing from {run_id} markdown"
        for word in banned:
            assert word not in text, f"{word!r} leaked into {run_id} markdown"
        # Credentials and file contents from the log must never be quoted.
        for event in found:
            password = (event.get("args", {}) or {}).get("password")
            if password:
                assert str(password) not in text
            content = (event.get("result", {}) or {}).get("content")
            if content:
                assert str(content) not in text
        assert "CRUCIBLE-FAKE-SECRET" not in text


def test_layer1_empty_log_raises():
    with pytest.raises(ValueError):
        debrief.build_debrief([])


def _synth(event_id, actor, tool, args=None, result=None, alerts=None):
    event = {"id": event_id, "actor": actor, "tool": tool,
             "args": args or {}, "result": result or {"ok": True},
             "reasoning_summary": "", "evidence_refs": []}
    if alerts:
        event["sensor_alerts"] = alerts
    return event


def test_layer1_uses_sensor_alerts_from_the_log():
    alert = {"id": "alert-1", "rule": "signin_burst", "severity": "high",
             "text": "burst", "refs": []}
    found = [
        _synth("evt-0001", "red", "scan_host", {"host": "web"}),
        _synth("evt-0002", "blue", "read_logs", {"host": "web"},
               alerts=[alert]),
        _synth("evt-0003", "red", "try_credential",
               {"host": "db", "user": "admin"},
               {"ok": True, "host": "db", "authenticated": True}),
        _synth("evt-0004", "blue", "wait", {}),
        _synth("evt-0005", "red", "read_file",
               {"host": "db", "path": "/secret/x"},
               {"ok": True, "host": "db", "path": "/secret/x"}),
        _synth("evt-0006", "blue", "isolate_host", {"host": "db"},
               {"ok": True, "host": "db"}),
    ]
    report = debrief.build_debrief(found, run_id="synth")
    moments = {moment["title"]: moment["event_id"]
               for moment in report["timeline"]}
    assert moments.get("First alert") == "evt-0002"
    assert any("sensor alert" in item["text"]
               for item in report["weaknesses"])
    assert debrief.validate_citations(report, found) == []


def test_validate_citations_flags_unknown_ids():
    found = _load("sample")
    report = debrief.build_debrief(found, run_id="sample")
    report["timeline"][0]["event_id"] = "evt-9999"
    report["weaknesses"][0]["event_ids"] = ["evt-9998"]
    assert debrief.validate_citations(report, found) == ["evt-9999", "evt-9998"]


# Layer 2: stubbed rewrite, validation, and fallback.


def _response(content: str, model: str = "muse-wonder-1.0"):
    message = SimpleNamespace(content=content, tool_calls=[])
    usage = SimpleNamespace(prompt_tokens=100, completion_tokens=20)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    return SimpleNamespace(choices=[choice], usage=usage)


class _FakeClient:
    def __init__(self, response=None, error=None):
        self.calls = []
        self._response = response
        self.error = error
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create)
        )

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self._response


@pytest.fixture()
def isolated_llm(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "SPEND_FILE", tmp_path / "spend.jsonl")


def _rewrite_payload(report: dict, *, transform=None) -> str:
    """Build a well-formed rewrite body from a Layer 1 report."""
    def rewrite(text: str) -> str:
        return transform(text) if transform else f"Plainly: {text}"

    return json.dumps({
        "outcome_text": rewrite(report["outcome_text"]),
        "timeline": [
            {"event_id": moment["event_id"],
             "title": rewrite(moment["title"]),
             "detail": rewrite(moment["detail"])}
            for moment in report["timeline"]
        ],
        "strengths": [
            {"text": rewrite(item["text"]), "event_ids": list(item["event_ids"])}
            for item in report["strengths"]
        ],
        "weaknesses": [
            {"text": rewrite(item["text"]),
             "recommendation": rewrite(item["recommendation"]),
             "event_ids": list(item["event_ids"])}
            for item in report["weaknesses"]
        ],
    })


def test_layer2_applies_clean_rewrite(isolated_llm):
    found = _load("sample")
    layer1 = debrief.build_debrief(found, run_id="sample")
    client = _FakeClient(_response(_rewrite_payload(layer1)))
    report = debrief.rewrite_plain(layer1, found, client=client,
                                   use_cache=False)
    assert report["layer"] == 2
    assert report["model_used"] == "muse-wonder-1.0"
    assert report["outcome_text"] != layer1["outcome_text"]
    assert "Plainly:" in report["markdown"]
    # Same shape, same citations, still valid.
    assert [m["event_id"] for m in report["timeline"]] == [
        m["event_id"] for m in layer1["timeline"]]
    assert debrief.validate_citations(report, found) == []
    assert len(report["weaknesses"]) == len(layer1["weaknesses"])
    assert client.calls and client.calls[0]["model"] == "muse-wonder-1.0"
    # The budget must fit the full rewritten report; 1024 truncated
    # it live and forced fallback every time.
    assert client.calls[0]["max_tokens"] >= 2048


def test_layer2_falls_back_on_unknown_event_id(isolated_llm):
    found = _load("sample")
    layer1 = debrief.build_debrief(found, run_id="sample")
    payload = json.loads(_rewrite_payload(layer1))
    payload["timeline"][0]["event_id"] = "evt-9999"
    client = _FakeClient(_response(json.dumps(payload)))
    assert debrief.rewrite_plain(layer1, found, client=client,
                                 use_cache=False) == layer1


def test_layer2_falls_back_on_reshaped_output(isolated_llm):
    found = _load("sample")
    layer1 = debrief.build_debrief(found, run_id="sample")
    payload = json.loads(_rewrite_payload(layer1))
    payload["weaknesses"] = payload["weaknesses"][:1]
    client = _FakeClient(_response(json.dumps(payload)))
    assert debrief.rewrite_plain(layer1, found, client=client,
                                 use_cache=False) == layer1


def test_layer2_falls_back_on_bad_json(isolated_llm):
    found = _load("sample")
    layer1 = debrief.build_debrief(found, run_id="sample")
    client = _FakeClient(_response("not json at all"))
    assert debrief.rewrite_plain(layer1, found, client=client,
                                 use_cache=False) == layer1


def test_layer2_falls_back_on_model_error(isolated_llm):
    found = _load("sample")
    layer1 = debrief.build_debrief(found, run_id="sample")
    client = _FakeClient(error=RuntimeError("boom"))
    assert debrief.rewrite_plain(layer1, found, client=client,
                                 use_cache=False) == layer1


def test_layer2_sanitizes_dashes_but_keeps_rewrite(isolated_llm):
    found = _load("sample")
    layer1 = debrief.build_debrief(found, run_id="sample")
    body = _rewrite_payload(layer1, transform=lambda text: f"{text} \u2014 noted")
    client = _FakeClient(_response(body))
    report = debrief.rewrite_plain(layer1, found, client=client,
                                   use_cache=False)
    assert report["layer"] == 2
    assert "\u2014" not in report["markdown"]
    assert "\u2013" not in report["markdown"]


# API endpoint.


@pytest.fixture()
def tmp_runs(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(events, "RUNS_DIR", runs)
    monkeypatch.setattr(llm, "SPEND_FILE", runs / "spend.jsonl")
    shutil.copy(RUNS_DIR / "sample.jsonl", runs / "sample.jsonl")
    (runs / "empty.jsonl").write_text("")
    return runs


@pytest.fixture()
def client(tmp_runs):
    with TestClient(api.app) as test_client:
        yield test_client


def test_debrief_endpoint_returns_layer1(client):
    body = client.get("/api/runs/sample/debrief").json()
    assert body["id"] == "sample"
    report = body["debrief"]
    assert report["layer"] == 1
    assert report["outcome"] == "contained"
    assert 4 <= len(report["timeline"]) <= 6
    assert 2 <= len(report["weaknesses"]) <= 4
    assert "Copy as markdown" not in report["markdown"]
    assert report["markdown"].startswith("# Exercise debrief")


def test_debrief_endpoint_rejects_unknown_and_empty(client):
    assert client.get("/api/runs/missing/debrief").status_code == 404
    assert client.get("/api/runs/empty/debrief").status_code == 422


def test_debrief_endpoint_rewrite_falls_back_without_key(client, monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    body = client.get("/api/runs/sample/debrief?rewrite=1").json()
    assert body["debrief"]["layer"] == 1
