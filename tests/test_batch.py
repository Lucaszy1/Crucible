"""Hermetic tests for the batch runner. No Docker or model calls."""

import csv
import json

from crucible import batch, events, llm, orchestrator, sandbox
from crucible.agents import ModelBlue, ModelRed


def _stub_common(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(sandbox, "start", lambda: {"web": True})
    monkeypatch.setattr(sandbox, "stop", lambda: seen.setdefault("stopped", True))
    monkeypatch.setattr(batch.tools, "restore_host",
                        lambda host: {"ok": True, "host": host})
    monkeypatch.setattr(batch.tools, "reset_state", lambda: None)
    monkeypatch.setattr(events, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(llm, "SPEND_FILE", tmp_path / "spend.jsonl")
    return seen


def _evt(reasoning="note"):
    return {"actor": "red", "tool": "scan_host", "args": {},
            "result": {"ok": True}, "reasoning_summary": reasoning,
            "evidence_refs": []}


def test_batch_args_parse():
    args = batch.parse_args(["--count", "3", "--agents", "model",
                             "--prefix", "demo", "--no-cache"])
    assert args.count == 3
    assert args.agents == "model"
    assert args.prefix == "demo"
    assert args.no_cache is True
    args = batch.parse_args(["--count", "1", "--prefix", "x"])
    assert args.agents == "scripted"
    assert args.no_cache is False
    assert args.max_turns == 30


def test_batch_rejects_zero_count(tmp_path, monkeypatch, capsys):
    _stub_common(monkeypatch, tmp_path)
    assert batch.main(["--count", "0", "--prefix", "x"]) == 1
    assert "at least 1" in capsys.readouterr().err


def test_batch_writes_csv_and_summary(tmp_path, monkeypatch, capsys):
    seen = _stub_common(monkeypatch, tmp_path)
    plans = {
        "demo-1": ("blue_held", 3, None, 9, 11, 0.02),
        "demo-2": ("red_succeeded", 5, 8, 0, 0, 0.03),
        "demo-3": ("stalemate", None, None, 0, 20, 0.00),
    }

    def fake_exercise(red, blue, max_turns=30, run_path=None, exercise_id=None):
        outcome, delay, contain, down, blue, cost = plans[exercise_id]
        if cost:
            llm.record_spend(model="muse-spark-1.3", input_tokens=1000,
                             output_tokens=0, exercise_id=exercise_id)
            # Normalize to the planned cost regardless of price table.
            lines = (tmp_path / "spend.jsonl").read_text(encoding="utf-8").splitlines()
            entry = json.loads(lines[-1])
            entry["cost_usd"] = cost
            lines[-1] = json.dumps(entry)
            (tmp_path / "spend.jsonl").write_text("\n".join(lines) + "\n",
                                                 encoding="utf-8")
        return {
            "events": [_evt("Probing first."),
                       _evt("Model returned no tool call, using baseline.")],
            "score": {"turns_used": 2, "total_events": 2,
                      "time_to_detect": 2 if delay is not None else None,
                      "detection_delay": delay, "time_to_contain": contain,
                      "hosts_compromised": 0, "compromised_hosts": [],
                      "secret_exfiltrated": outcome == "red_succeeded",
                      "outcome": outcome, "downtime_cost": down,
                      "blue_score": blue},
            "run_path": str(tmp_path / f"{exercise_id}.jsonl"),
        }

    monkeypatch.setattr(orchestrator, "run_exercise", fake_exercise)
    monkeypatch.setattr(batch.orchestrator, "run_exercise", fake_exercise)
    assert batch.main(["--count", "3", "--agents", "scripted",
                       "--prefix", "demo"]) == 0
    assert seen["stopped"] is True

    csv_path = tmp_path / "runs" / "batch-demo.csv"
    assert csv_path.exists()
    with csv_path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["exercise_id"] for row in rows] == ["demo-1", "demo-2", "demo-3"]
    assert rows[0]["outcome"] == "blue_held"
    assert rows[0]["detection_delay"] == "3"
    assert rows[0]["downtime_cost"] == "9"
    assert rows[0]["blue_score"] == "11"
    assert rows[0]["model_turns"] == "1"
    assert rows[0]["fallback_turns"] == "1"
    assert float(rows[0]["estimated_cost_usd"]) == 0.02
    assert rows[2]["detection_delay"] == ""
    assert rows[2]["time_to_contain"] == ""

    out = capsys.readouterr().out
    assert "demo-1: outcome=blue_held" in out
    assert "cost=$" in out
    assert "Red wins: 1" in out
    assert "blue holds: 1" in out.lower()
    assert "stalemates: 1" in out.lower()
    assert "Mean detection delay: 4.00" in out
    assert "Mean blue_score: 10.33" in out
    assert "Total estimated cost: $0.0500" in out
    assert "CSV:" in out


def test_batch_model_flag_uses_cache_option(tmp_path, monkeypatch):
    _stub_common(monkeypatch, tmp_path)
    seen = {}

    class _RecordingRed(ModelRed):
        def __init__(self, *args, **kwargs):
            seen.setdefault("red_cache", []).append(kwargs.get("use_cache", True))
            super().__init__(*args, **kwargs)

    class _RecordingBlue(ModelBlue):
        def __init__(self, *args, **kwargs):
            seen.setdefault("blue_cache", []).append(kwargs.get("use_cache", True))
            super().__init__(*args, **kwargs)

    def fake_exercise(red, blue, max_turns=30, run_path=None, exercise_id=None):
        return {"events": [], "score": {"outcome": "stalemate",
                                       "detection_delay": None,
                                       "time_to_contain": None,
                                       "downtime_cost": 0, "blue_score": 20},
                "run_path": str(tmp_path / "x.jsonl")}

    monkeypatch.setattr(batch, "ModelRed", _RecordingRed)
    monkeypatch.setattr(batch, "ModelBlue", _RecordingBlue)
    monkeypatch.setattr(orchestrator, "run_exercise", fake_exercise)
    monkeypatch.setattr(batch.orchestrator, "run_exercise", fake_exercise)
    assert batch.main(["--count", "2", "--agents", "model",
                       "--prefix", "m", "--no-cache"]) == 0
    assert seen["red_cache"] == [False, False]
    assert seen["blue_cache"] == [False, False]


def test_batch_mean_handles_no_detections(tmp_path, monkeypatch, capsys):
    _stub_common(monkeypatch, tmp_path)

    def fake_exercise(red, blue, max_turns=30, run_path=None, exercise_id=None):
        return {"events": [],
                "score": {"outcome": "stalemate", "detection_delay": None,
                          "time_to_contain": None, "downtime_cost": 0,
                          "blue_score": 20},
                "run_path": str(tmp_path / "x.jsonl")}

    monkeypatch.setattr(orchestrator, "run_exercise", fake_exercise)
    monkeypatch.setattr(batch.orchestrator, "run_exercise", fake_exercise)
    assert batch.main(["--count", "2", "--prefix", "quiet"]) == 0
    out = capsys.readouterr().out
    assert "Mean detection delay: no detections" in out
    assert "Mean blue_score: 20.00" in out
