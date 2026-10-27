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


def _evt(reasoning="note", tool="scan_host", host=None):
    return {"actor": "red", "tool": tool,
            "args": {"host": host} if host else {},
            "result": {"ok": True}, "reasoning_summary": reasoning,
            "evidence_refs": []}


def test_red_attempts_counts_red_credentials_per_host():
    events = [
        _evt("Probing first."),
        _evt("Trying the data host.", "try_credential", "db"),
        _evt("Trying the data host again.", "try_credential", "db"),
        _evt("Trying the web host.", "try_credential", "web"),
    ]
    blue_note = _evt("Reading logs.", "read_logs", "web")
    blue_note["actor"] = "blue"
    events.append(blue_note)
    assert batch._red_attempts(events) == "db=2, web=1"
    assert batch._red_attempts([]) == ""


def test_batch_args_parse():
    args = batch.parse_args(["--count", "3", "--agents", "model",
                             "--prefix", "demo", "--no-cache"])
    assert args.count == 3
    assert args.agents == "model"
    assert args.prefix == "demo"
    assert args.no_cache is True
    assert args.sensors == "on"
    args = batch.parse_args(["--count", "1", "--prefix", "x"])
    assert args.agents == "scripted"
    assert args.no_cache is False
    assert args.max_turns == 40
    assert args.sensors == "on"
    assert batch.parse_args(["--count", "1", "--prefix", "x",
                             "--sensors", "off"]).sensors == "off"


def test_batch_rejects_zero_count(tmp_path, monkeypatch, capsys):
    _stub_common(monkeypatch, tmp_path)
    assert batch.main(["--count", "0", "--prefix", "x"]) == 1
    assert "at least 1" in capsys.readouterr().err


def test_batch_writes_csv_and_summary(tmp_path, monkeypatch, capsys):
    seen = _stub_common(monkeypatch, tmp_path)
    plans = {
        "demo-1": ("blue_held", 3, None, 9, 18.75, 81.25, 0.02),
        "demo-2": ("red_succeeded", 5, 8, 0, 0.0, 0, 0.03),
        "demo-3": ("stalemate", None, None, 0, 0.0, 100.0, 0.00),
    }

    def fake_exercise(red, blue, max_turns=30, run_path=None, exercise_id=None, audit="off", sensors="on",
                      blue_reasoning_effort=None):
        outcome, delay, contain, down, pct, blue, cost = plans[exercise_id]
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
                       _evt("Trying the data host.", "try_credential", "db"),
                       _evt("Trying the data host again.", "try_credential", "db"),
                       _evt("Trying the web host.", "try_credential", "web"),
                       _evt("Model returned no tool call, using baseline.")],
            "score": {"turns_used": 2, "total_events": 2,
                      "time_to_detect": 2 if delay is not None else None,
                      "detection_delay": delay, "time_to_contain": contain,
                      "hosts_compromised": 0, "compromised_hosts": [],
                      "secret_exfiltrated": outcome == "red_succeeded",
                      "outcome": outcome, "downtime_cost": down,
                      "downtime_pct": pct, "blue_score": blue},
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
    assert rows[0]["downtime_pct"] == "18.75"
    assert rows[0]["blue_score"] == "81.25"
    assert rows[0]["model_turns"] == "4"
    assert rows[0]["fallback_turns"] == "1"
    assert rows[0]["red_attempts"] == "db=2, web=1"
    assert float(rows[0]["estimated_cost_usd"]) == 0.02
    assert rows[2]["detection_delay"] == ""
    assert rows[2]["time_to_contain"] == ""
    assert rows[0]["sensors"] == "on"
    assert rows[0]["intrusion"] == "False"
    assert rows[0]["relevant_detection_delay"] == ""

    out = capsys.readouterr().out
    assert "demo-1: outcome=blue_held" in out
    assert "downtime=18.75%" in out
    assert "cost=$" in out
    assert "Sensors: on" in out
    assert "Red wins: 1" in out
    assert "blue holds: 1" in out.lower()
    assert "stalemates: 1" in out.lower()
    assert "Mean detection delay: 4.00" in out
    assert "Mean downtime: 6.25%" in out
    assert "Mean blue_score: 60.42" in out
    assert "Episodes with intrusion: 0/3" in out
    assert "Secret-read rate given intrusion: no intrusions" in out
    assert "Total estimated cost: $0.0500" in out
    assert "CSV:" in out


def test_batch_reports_intrusion_conditional_numbers(tmp_path, monkeypatch,
                                                     capsys):
    _stub_common(monkeypatch, tmp_path)
    plans = {
        "mix-1": {"outcome": "red_succeeded", "detection_delay": 2,
                  "time_to_contain": 6, "downtime_cost": 5,
                  "downtime_pct": 5.0, "blue_score": 0,
                  "intrusion": True, "contained_after_intrusion": True,
                  "secret_read": True, "relevant_detection_delay": 3},
        "mix-2": {"outcome": "red_succeeded", "detection_delay": 2,
                  "time_to_contain": None, "downtime_cost": 0,
                  "downtime_pct": 0.0, "blue_score": 0,
                  "intrusion": True, "contained_after_intrusion": False,
                  "secret_read": True, "relevant_detection_delay": 7},
        "mix-3": {"outcome": "blue_held", "detection_delay": 1,
                  "time_to_contain": None, "downtime_cost": 0,
                  "downtime_pct": 0.0, "blue_score": 100.0,
                  "intrusion": False, "contained_after_intrusion": False,
                  "secret_read": False, "relevant_detection_delay": None},
    }

    def fake_exercise(red, blue, max_turns=30, run_path=None, exercise_id=None, audit="off", sensors="on",
                      blue_reasoning_effort=None):
        return {"events": [], "score": dict(plans[exercise_id]),
                "run_path": str(tmp_path / "x.jsonl")}

    monkeypatch.setattr(orchestrator, "run_exercise", fake_exercise)
    monkeypatch.setattr(batch.orchestrator, "run_exercise", fake_exercise)
    assert batch.main(["--count", "3", "--prefix", "mix"]) == 0
    out = capsys.readouterr().out
    assert "Episodes with intrusion: 2/3" in out
    assert "Secret-read rate given intrusion: 100.0%" in out
    assert "Containment rate given intrusion: 50.0%" in out
    assert "Median relevant detection delay given intrusion: 5.0 events" in out
    assert "Median time to contain given intrusion: 6 events" in out
    assert "Mean downtime among contained: 5.00%" in out
    with (tmp_path / "runs" / "batch-mix.csv").open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["intrusion"] == "True"
    assert rows[0]["contained_after_intrusion"] == "True"
    assert rows[0]["secret_read"] == "True"
    assert rows[0]["relevant_detection_delay"] == "3"
    assert rows[2]["intrusion"] == "False"
    assert rows[2]["relevant_detection_delay"] == ""
    header = list(rows[0].keys())
    assert header[:15] == [
        "exercise_id", "outcome", "detection_delay", "time_to_contain",
        "downtime_cost", "downtime_pct", "blue_score", "justified_pct",
        "unsupported_count", "invalid_refs_count", "mean_audit_score",
        "red_attempts", "model_turns", "fallback_turns",
        "estimated_cost_usd",
    ]
    assert header[15:] == ["sensors", "intrusion",
                           "contained_after_intrusion", "secret_read",
                           "relevant_detection_delay", "scenario_id",
                           "scenario_seed", "scenario_difficulty"]
    assert rows[0]["scenario_id"] == "classic"
    assert rows[0]["scenario_seed"] == ""
    assert rows[0]["scenario_difficulty"] == ""


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

    def fake_exercise(red, blue, max_turns=30, run_path=None, exercise_id=None, audit="off", sensors="on",
                      blue_reasoning_effort=None):
        return {"events": [], "score": {"outcome": "stalemate",
                                       "detection_delay": None,
                                       "time_to_contain": None,
                                       "downtime_cost": 0,
                                       "downtime_pct": 0.0,
                                       "blue_score": 100.0},
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

    def fake_exercise(red, blue, max_turns=30, run_path=None, exercise_id=None, audit="off", sensors="on",
                      blue_reasoning_effort=None):
        return {"events": [],
                "score": {"outcome": "stalemate", "detection_delay": None,
                          "time_to_contain": None, "downtime_cost": 0,
                          "downtime_pct": 0.0, "blue_score": 100.0},
                "run_path": str(tmp_path / "x.jsonl")}

    monkeypatch.setattr(orchestrator, "run_exercise", fake_exercise)
    monkeypatch.setattr(batch.orchestrator, "run_exercise", fake_exercise)
    assert batch.main(["--count", "2", "--prefix", "quiet"]) == 0
    out = capsys.readouterr().out
    assert "Mean detection delay: no detections" in out
    assert "Mean downtime: 0.00%" in out
    assert "Mean blue_score: 100.00" in out


def _cap_event():
    return _evt(
        "Model call failed "
        "(daily model spend cap reached: $5.00 of $5.00 used), "
        "using baseline."
    )


def _cap_score():
    return {"outcome": "stalemate", "detection_delay": None,
            "time_to_contain": None, "downtime_cost": 0,
            "downtime_pct": 0.0, "blue_score": 100.0}


def test_batch_strict_flag_parses():
    assert batch.parse_args(["--count", "1", "--prefix", "x"]).strict is False
    args = batch.parse_args(["--count", "1", "--prefix", "x", "--strict"])
    assert args.strict is True


def test_batch_warns_on_spend_cap(tmp_path, monkeypatch, capsys):
    _stub_common(monkeypatch, tmp_path)
    monkeypatch.setenv("LLM_DAILY_LIMIT_USD", "5")
    monkeypatch.setattr(llm, "spent_today", lambda *a, **k: 5.0)

    def fake_exercise(red, blue, max_turns=30, run_path=None, exercise_id=None, audit="off", sensors="on",
                      blue_reasoning_effort=None):
        return {"events": [_cap_event()], "score": _cap_score(),
                "run_path": str(tmp_path / "x.jsonl")}

    monkeypatch.setattr(orchestrator, "run_exercise", fake_exercise)
    monkeypatch.setattr(batch.orchestrator, "run_exercise", fake_exercise)
    assert batch.main(["--count", "2", "--agents", "model",
                       "--prefix", "cap"]) == 0
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "spend cap" in err.lower()
    assert "$5.00 of $5.00" in err
    assert (tmp_path / "runs" / "batch-cap.csv").exists()


def test_batch_strict_aborts_on_spend_cap(tmp_path, monkeypatch, capsys):
    seen = _stub_common(monkeypatch, tmp_path)
    monkeypatch.setenv("LLM_DAILY_LIMIT_USD", "5")
    monkeypatch.setattr(llm, "spent_today", lambda *a, **k: 5.0)
    ran = []

    def fake_exercise(red, blue, max_turns=30, run_path=None, exercise_id=None, audit="off", sensors="on",
                      blue_reasoning_effort=None):
        ran.append(exercise_id)
        return {"events": [_cap_event()], "score": _cap_score(),
                "run_path": str(tmp_path / "x.jsonl")}

    monkeypatch.setattr(orchestrator, "run_exercise", fake_exercise)
    monkeypatch.setattr(batch.orchestrator, "run_exercise", fake_exercise)
    assert batch.main(["--count", "3", "--agents", "model",
                       "--prefix", "halt", "--strict"]) == 2
    assert ran == ["halt-1"]
    assert seen["stopped"] is True
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "$5.00 of $5.00" in err
    assert "aborting batch" in err.lower()
    assert "scripted games as model games" in err.lower()
    assert not (tmp_path / "runs" / "batch-halt.csv").exists()
