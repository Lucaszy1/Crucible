"""Hermetic tests for the Stage 3 suspicion battery.

No Docker and no model calls. Fixture benches are built under tmp_path;
only check_episodes runs, against local summary and jsonl files.
"""

import csv
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import stage3_checks


def _write_bench(path: Path, *, sensors: str, alerts: int, count: int = 2,
                 signin_last: bool = False,
                 episodes: list | None = None,
                 red_profile: str = "solver",
                 blue_notes: dict | None = None,
                 degraded: tuple = ()) -> Path:
    """Write a bench with N alert-bearing blue events.

    Every episode intrudes: a red authenticated sign-in with a blue
    turn after it, unless signin_last puts the sign-in after blue's
    only turn (sensors never had a chance, so silence owes nothing).
    episodes overrides the default ep-{index} ids (duplicates allowed,
    to test the within-bench duplicate check). red_profile sets the
    CSV red profile; blue_notes overrides the blue reasoning note per
    episode; degraded flags those episodes' blue events degraded.
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
             "red_profile": red_profile, "cost": "0.0",
             "secret_read": "False",
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
                "reasoning_summary": (blue_notes or {}).get(
                    row["episode"], "stand by"),
                "evidence_refs": []}
        if row["episode"] in (degraded or ()):
            blue["degraded"] = True
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


def _write_spend(path: Path, entries: list) -> Path:
    with path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + "\n")
    return path


def _finish(path: Path, name: str, when: float) -> None:
    os.utime(path / f"{name}.jsonl", (when, when))


def test_cached_entry_scoped_to_writing_bench(tmp_path, monkeypatch):
    old = _write_bench(tmp_path / "bench-old", sensors="on", alerts=2,
                       episodes=["shared"])
    new = _write_bench(tmp_path / "bench-new", sensors="on", alerts=2,
                       episodes=["shared"])
    base = 1793000000.0
    _finish(old, "shared", base)
    _finish(new, "shared", base + 7200)
    spend = _write_spend(tmp_path / "spend.jsonl", [
        {"exercise_id": "shared", "cached": True,
         "model": "muse-wonder-1.0", "ts": base + 7100}])
    monkeypatch.setattr(stage3_checks, "SPEND_FILE", spend)
    for order in ([old, new], [new, old]):
        report = stage3_checks.Report()
        stage3_checks.check_episodes(report, order)
        assert report.fails() == ["shared: cached replay under --no-cache"]


def test_older_bench_keeps_own_cached_entry(tmp_path, monkeypatch):
    old = _write_bench(tmp_path / "bench-old", sensors="on", alerts=2,
                       episodes=["shared"])
    new = _write_bench(tmp_path / "bench-new", sensors="on", alerts=2,
                       episodes=["shared"])
    base = 1793000000.0
    _finish(old, "shared", base)
    _finish(new, "shared", base + 7200)
    spend = _write_spend(tmp_path / "spend.jsonl", [
        {"exercise_id": "shared", "cached": True,
         "model": "muse-spark-1.3", "ts": base - 100}])
    monkeypatch.setattr(stage3_checks, "SPEND_FILE", spend)
    report = stage3_checks.Report()
    stage3_checks.check_episodes(report, [old, new])
    assert report.fails() == ["shared: cached replay under --no-cache"]


def test_cached_entry_in_own_window_still_flags(tmp_path, monkeypatch):
    bench = _write_bench(tmp_path / "bench-solo", sensors="on", alerts=2,
                         episodes=["solo"])
    base = 1793000000.0
    _finish(bench, "solo", base)
    spend = _write_spend(tmp_path / "spend.jsonl", [
        {"exercise_id": "solo", "cached": True,
         "model": "muse-spark-1.3", "ts": base - 100}])
    monkeypatch.setattr(stage3_checks, "SPEND_FILE", spend)
    report = stage3_checks.Report()
    stage3_checks.check_episodes(report, [bench])
    assert report.fails() == ["solo: cached replay under --no-cache"]


def test_model_briefed_degraded_episode_fails(tmp_path):
    bench = _write_bench(tmp_path / "briefed-deg", sensors="on", alerts=2,
                         red_profile="model-briefed", degraded=("ep-0",))
    report = stage3_checks.Report()
    stage3_checks.check_episodes(report, [bench])
    assert report.fails() == ["ep-0: model-briefed turn ran degraded"]


def test_model_briefed_baseline_note_fails(tmp_path):
    bench = _write_bench(
        tmp_path / "briefed-base", sensors="on", alerts=2,
        red_profile="model-briefed",
        blue_notes={"ep-1": "Model call failed (boom), using baseline."})
    report = stage3_checks.Report()
    stage3_checks.check_episodes(report, [bench])
    assert report.fails() == ["ep-1: model-briefed turn used baseline fallback"]


def test_model_briefed_clean_episodes_pass(tmp_path):
    bench = _write_bench(tmp_path / "briefed-clean", sensors="on", alerts=2,
                         red_profile="model-briefed")
    report = stage3_checks.Report()
    stage3_checks.check_episodes(report, [bench])
    assert report.fails() == []


def test_solver_baseline_note_is_not_checked(tmp_path):
    bench = _write_bench(
        tmp_path / "solver-base", sensors="on", alerts=2,
        blue_notes={"ep-0": "Model call failed (boom), using baseline."})
    report = stage3_checks.Report()
    stage3_checks.check_episodes(report, [bench])
    assert report.fails() == []
