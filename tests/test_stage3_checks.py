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


def _write_bench(path: Path, *, sensors: str, alerts: int, count: int = 2,
                 signin_last: bool = False,
                 episodes: list | None = None) -> Path:
    """Write a bench with N alert-bearing blue events.

    Every episode intrudes: a red authenticated sign-in with a blue
    turn after it, unless signin_last puts the sign-in after blue's
    only turn (sensors never had a chance, so silence owes nothing).
    episodes overrides the default ep-{index} ids (duplicates allowed,
    to test the within-bench duplicate check).
    """
    path.mkdir(parents=True, exist_ok=True)
    (path / "summary.json").write_text(json.dumps({
        "cells": ["solver-sensors-on-memory-off"],
        "audit": "off",
        "audit_cells": [],
    }), encoding="utf-8")
    names = list(episodes) if episodes is not None \
        else [f"ep-{index}" for index in range(count)]
    rows = [{"episode": name, "cell": "solver-sensors-on-memory-off",
             "red_profile": "solver", "cost": "0.0", "secret_read": "False",
             "intrusion": "True", "scenario_seed": "",
             "scenario_difficulty": ""}
            for name in names]
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
        signin = {"id": "evt-0002", "actor": "red", "tool": "try_credential",
                  "args": {"host": "app", "user": "a", "password": "b"},
                  "result": {"ok": True, "host": "app",
                             "authenticated": True},
                  "reasoning_summary": "sign in", "evidence_refs": []}
        first = {"id": "evt-0001", "actor": "system",
                 "tool": "clean_start_check", "args": {"sensors": sensors},
                 "result": {"ok": True}, "reasoning_summary": "check",
                 "evidence_refs": []}
        if signin_last:
            blue["id"] = "evt-0002"
            signin["id"] = "evt-0003"
            events = [first, blue, signin]
        else:
            events = [first, signin, blue]
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
    bench = _write_bench(tmp_path / "loud", sensors="on", alerts=2)
    report = stage3_checks.Report()
    stage3_checks.check_episodes(report, [bench])
    assert report.fails() == []


def test_sensors_off_bench_without_alerts_passes(tmp_path):
    bench = _write_bench(tmp_path / "off", sensors="off", alerts=0)
    report = stage3_checks.Report()
    stage3_checks.check_episodes(report, [bench])
    assert report.fails() == []


def test_single_silent_episode_fails_loudly(tmp_path):
    bench = _write_bench(tmp_path / "one", sensors="on", alerts=0, count=1)
    report = stage3_checks.Report()
    stage3_checks.check_episodes(report, [bench])
    fails = report.fails()
    assert any("ep-0: sensors-on intrusion" in fail for fail in fails)
    assert any("1 sensors-on episode with intrusions" in fail
               for fail in fails)


def test_intrusion_without_later_blue_turn_passes(tmp_path):
    bench = _write_bench(tmp_path / "late", sensors="on", alerts=0,
                         signin_last=True)
    report = stage3_checks.Report()
    stage3_checks.check_episodes(report, [bench])
    assert report.fails() == []


def test_void_bench_silent_marks_void_not_fail(tmp_path):
    for name in ("bench-stage3-train-solver", "bench-stage3-heldout"):
        bench = _write_bench(tmp_path / name, sensors="on", alerts=0)
        report = stage3_checks.Report()
        stage3_checks.check_episodes(report, [bench])
        assert report.fails() == []
        assert any("void by name" in level_text[1]
                   for level_text in report.findings)


def test_cross_bench_reused_ids_pass(tmp_path):
    first = _write_bench(tmp_path / "bench-a", sensors="on", alerts=2)
    second = _write_bench(tmp_path / "bench-b", sensors="on", alerts=2)
    report = stage3_checks.Report()
    stage3_checks.check_episodes(report, [first, second])
    assert report.fails() == []


def test_within_bench_duplicate_ids_fail(tmp_path):
    bench = _write_bench(tmp_path / "dup", sensors="on", alerts=2,
                         episodes=["ep-0", "ep-0"])
    report = stage3_checks.Report()
    stage3_checks.check_episodes(report, [bench])
    assert any("duplicate exercise id ep-0" in fail
               for fail in report.fails())
