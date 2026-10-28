"""Hermetic tests for the Stage 3 suspicion battery.

No Docker and no model calls. Fixture benches are built under tmp_path;
only check_episodes runs, against local summary and jsonl files.
"""

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import stage3_checks


def _write_bench(path: Path, *, sensors: str, alerts: int) -> Path:
    """Write a two-episode bench with N alert-bearing blue events."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "summary.json").write_text(json.dumps({
        "cells": ["solver-sensors-on-memory-off"],
        "audit": "off",
        "audit_cells": [],
    }), encoding="utf-8")
    rows = [{"episode": f"ep-{index}", "cell": "solver-sensors-on-memory-off",
             "red_profile": "solver", "cost": "0.0", "secret_read": "False",
             "intrusion": "True", "scenario_seed": "",
             "scenario_difficulty": ""}
            for index in range(2)]
    with (path / "summary.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    for order, row in enumerate(rows):
        blue = {"id": "evt-0003", "actor": "blue", "tool": "wait",
                "args": {}, "result": {"ok": True, "waited": True},
                "reasoning_summary": "stand by", "evidence_refs": []}
        if order < alerts:
            blue["sensor_alerts"] = [
                {"id": "alert-1", "rule": "success_first_try",
                 "severity": "high", "text": "Successful sign-in.",
                 "refs": ["app:0"]}]
        events = [
            {"id": "evt-0001", "actor": "system",
             "tool": "clean_start_check", "args": {"sensors": sensors},
             "result": {"ok": True}, "reasoning_summary": "check",
             "evidence_refs": []},
            {"id": "evt-0002", "actor": "red", "tool": "scan_host",
             "args": {"host": "web"}, "result": {"ok": True},
             "reasoning_summary": "probe", "evidence_refs": []},
            blue,
        ]
        with (path / f"{row['episode']}.jsonl").open(
                "w", encoding="utf-8") as fh:
            for event in events:
                fh.write(json.dumps(event) + "\n")
    return path


def test_sensors_on_bench_with_zero_alerts_fails(tmp_path):
    bench = _write_bench(tmp_path / "silent", sensors="on", alerts=0)
    report = stage3_checks.Report()
    stage3_checks.check_episodes(report, [bench])
    assert any("zero sensor alerts" in fail for fail in report.fails())


def test_sensors_on_bench_with_alerts_passes(tmp_path):
    bench = _write_bench(tmp_path / "loud", sensors="on", alerts=1)
    report = stage3_checks.Report()
    stage3_checks.check_episodes(report, [bench])
    assert report.fails() == []


def test_sensors_off_bench_without_alerts_passes(tmp_path):
    bench = _write_bench(tmp_path / "off", sensors="off", alerts=0)
    report = stage3_checks.Report()
    stage3_checks.check_episodes(report, [bench])
    assert report.fails() == []
