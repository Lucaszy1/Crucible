"""Hermetic tests for the Stage 3 parity table (Open vs Spark blue).

No Docker and no model calls. Fixture benches are built under tmp_path;
only scripts/stage3_report.py runs, against local summary and jsonl files.
"""

import csv
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import stage3_report

CELL = "model-briefed-sensors-on-memory-off"
CSV_FIELDS = ["episode", "cell", "intrusion", "contained_after_intrusion",
              "secret_read", "relevant_detection_delay", "detection_delay",
              "time_to_contain", "downtime_pct", "cost", "scenario_id",
              "scenario_seed", "scenario_difficulty"]


def _write_csv(path: Path, rows: list) -> None:
    path.mkdir(parents=True, exist_ok=True)
    with (path / "summary.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in CSV_FIELDS})


def _write_episode(path: Path, name: str, *, red_note="probing",
                   blue_note="stand by") -> None:
    with (path / f"{name}.jsonl").open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"actor": "red", "tool": "try_credential",
                             "reasoning_summary": red_note}) + "\n")
        fh.write(json.dumps({"actor": "blue", "tool": "read_logs",
                             "reasoning_summary": blue_note}) + "\n")


def _row(seed: int, *, intrusion=True, contained=True, secret=False,
         relevant=7, delay=12, contain=22, downtime=6.0, cost=0.65) -> dict:
    name = f"gen-{seed}-easy-{CELL}-1"
    return {"episode": name, "cell": CELL,
            "intrusion": "True" if intrusion else "False",
            "contained_after_intrusion": "True" if contained else "False",
            "secret_read": "True" if secret else "False",
            "relevant_detection_delay": str(relevant),
            "detection_delay": str(delay),
            "time_to_contain": str(contain) if contained else "",
            "downtime_pct": str(downtime), "cost": str(cost),
            "scenario_id": f"scn-{seed}-easy-a0",
            "scenario_seed": str(seed), "scenario_difficulty": "easy"}


def _scaffold(tmp_path: Path) -> dict:
    heldout = tmp_path / "heldout"
    sensors = tmp_path / "sensors"
    _write_csv(heldout, [])
    _write_csv(sensors, [])
    frozen = tmp_path / "frozen.json"
    frozen.write_text(json.dumps({"rules": []}), encoding="utf-8")
    return {"heldout": heldout, "sensors": sensors, "frozen": frozen}


def _run_report(tmp_path: Path, scaffold: dict, *, briefed: Path,
                spark: Path) -> tuple:
    out_md = tmp_path / "out.md"
    out_json = tmp_path / "out.json"
    code = stage3_report.main([
        "--heldout", str(scaffold["heldout"]),
        "--sensors", str(scaffold["sensors"]),
        "--noisyref", str(tmp_path / "missing-noisy"),
        "--xcheck", str(tmp_path / "missing-xcheck"),
        "--briefed", str(briefed),
        "--briefed-spark", str(spark),
        "--frozen", str(scaffold["frozen"]),
        "--out-md", str(out_md), "--out-json", str(out_json)])
    assert code == 0
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    return out_md.read_text(encoding="utf-8"), payload


def test_parity_skipped_when_spark_missing(tmp_path):
    scaffold = _scaffold(tmp_path)
    briefed = tmp_path / "briefed-open"
    rows = [_row(9001), _row(9002)]
    _write_csv(briefed, rows)
    for row in rows:
        _write_episode(briefed, row["episode"])
    md, payload = _run_report(tmp_path, scaffold, briefed=briefed,
                              spark=tmp_path / "missing-spark")
    assert "Open-weight parity" not in md
    assert "same nets" not in md.lower()
    assert payload.get("parity") is None
    assert "briefed_spark" not in payload.get("benches", {})


def test_parity_table_reports_both_arms(tmp_path):
    scaffold = _scaffold(tmp_path)
    briefed = tmp_path / "briefed-open"
    open_rows = [_row(9001, relevant=7, delay=12, contain=22,
                      downtime=6.0, cost=0.65),
                 _row(9002, relevant=8, delay=13, contain=24,
                      downtime=7.0, cost=0.66)]
    _write_csv(briefed, open_rows)
    _write_episode(briefed, open_rows[0]["episode"],
                   blue_note="Model returned no tool call, using baseline.")
    _write_episode(briefed, open_rows[1]["episode"])
    spark = tmp_path / "briefed-spark"
    spark_rows = [_row(9001, secret=True, contained=True, relevant=5,
                       delay=10, contain=20, downtime=5.0, cost=0.75),
                  _row(9002, secret=False, contained=True, relevant=6,
                       delay=11, contain=21, downtime=5.5, cost=0.76)]
    _write_csv(spark, spark_rows)
    for row in spark_rows:
        _write_episode(spark, row["episode"],
                       red_note="Model reply hit the token limit, "
                                "using baseline.")
    md, payload = _run_report(tmp_path, scaffold, briefed=briefed, spark=spark)
    assert "Open-weight parity" in md
    # Same networks.
    assert "2 of 2" in md
    # Intrusion, containment, secret reads for both arms.
    assert "2/2" in md  # intrusions both arms
    assert "0/2" in md  # open secret reads
    assert "1/2" in md  # spark secret reads
    # Delays, contain, downtime, cost.
    assert "7.50" in md or "7.5" in md  # open med relevant (7+8)/2
    assert "5.50" in md or "5.5" in md  # spark med relevant (5+6)/2
    assert "23" in md or "22" in md  # med contain values
    assert "6.50" in md or "6.5" in md  # open mean downtime
    assert "1.31" in md  # open total cost 0.65+0.66
    assert "1.51" in md  # spark total cost 0.75+0.76
    # Fallback counts for red and blue.
    assert "1 of 2" in md
    # Neutral: names the better arm per metric, never claims parity.
    lowered = md.lower()
    assert "open fewer" in lowered  # secret reads: open 0 vs spark 1
    assert "spark faster" in lowered  # relevant delay: spark 5.5 vs 7.5
    for claim in ("parity achieved", "at parity", "parity confirmed",
                  "equivalent models", "no difference between models"):
        assert claim not in lowered
    parity = payload.get("parity")
    assert parity is not None
    assert parity["same_networks"]["shared_n"] == 2
    assert parity["open"]["intrusion"]["hits"] == 2
    assert parity["spark"]["secret_read"]["hits"] == 1
    assert payload["benches"]["briefed_spark"] == str(spark)


def test_parity_reports_whichever_better(tmp_path):
    scaffold = _scaffold(tmp_path)
    # Flip the secret-read winner: spark now clean, open leaks once.
    briefed = tmp_path / "briefed-open"
    open_rows = [_row(9001, secret=True), _row(9002, secret=False)]
    _write_csv(briefed, open_rows)
    for row in open_rows:
        _write_episode(briefed, row["episode"])
    spark = tmp_path / "briefed-spark"
    spark_rows = [_row(9001), _row(9002)]
    _write_csv(spark, spark_rows)
    for row in spark_rows:
        _write_episode(spark, row["episode"])
    md, _ = _run_report(tmp_path, scaffold, briefed=briefed, spark=spark)
    assert "spark fewer" in md.lower()


# --- cost table ---


def _touch(path: Path, moment: float) -> None:
    os.utime(path, (moment, moment))


def _write_spend(path: Path, entries: list) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")


def _cost_scaffold(tmp_path: Path) -> dict:
    """Briefed bench (2 episodes) plus an older archived same-id run.

    The spend log holds both runs under the same episode ids; the
    archived run's entries land at or before the archived log mtime
    and must not be counted.
    """
    briefed = tmp_path / "briefed"
    rows = [_row(9001), _row(9002)]
    _write_csv(briefed, rows)
    archive = tmp_path / "archive" / "bench-401"
    archive.mkdir(parents=True)
    base = 1793225000.0
    for row in rows:
        _write_episode(briefed, row["episode"])
        (archive / f"{row['episode']}.jsonl").write_text(
            "{}\n", encoding="utf-8")
        _touch(archive / f"{row['episode']}.jsonl", base)
        _touch(briefed / f"{row['episode']}.jsonl", base + 5000.0)
    spend = tmp_path / "spend.jsonl"
    entries = []
    for row in rows:
        episode = row["episode"]
        entries.append({"day": "2026-10-28", "ts": base - 100.0,
                        "model": "muse-spark-1.3", "exercise_id": episode,
                        "input_tokens": 100000, "output_tokens": 100000,
                        "cost_usd": 99.0, "cached": False, "host": "meta"})
        entries.append({"day": "2026-10-28", "ts": base + 5100.0,
                        "model": "muse-spark-1.3", "exercise_id": episode,
                        "input_tokens": 1000, "output_tokens": 100,
                        "cost_usd": 0.0026, "cached": False, "host": "meta"})
        entries.append({"day": "2026-10-28", "ts": base + 5200.0,
                        "model": "muse-wonder-1.0", "exercise_id": episode,
                        "input_tokens": 2000, "output_tokens": 200,
                        "cost_usd": 0.013, "cached": False, "host": "meta"})
        entries.append({"day": "2026-10-28", "ts": base + 5300.0,
                        "model": "meta-models/muse-spark-open-1026",
                        "exercise_id": episode, "input_tokens": 70000,
                        "output_tokens": 7000, "reasoning_tokens": 500,
                        "cost_usd": 0.0, "cached": False, "host": "baseten"})
    _write_spend(spend, entries)
    return {"briefed": briefed, "rows": rows, "spend": spend,
            "archive": tmp_path / "archive"}


def _cost_table(scaffold: dict) -> dict:
    episodes = [row["episode"] for row in scaffold["rows"]]
    return stage3_report.build_cost_table(
        scaffold["briefed"], episodes, spend_file=scaffold["spend"],
        archive_dir=scaffold["archive"])


def test_cost_table_attributes_roles_and_cuts_archive(tmp_path):
    table = _cost_table(_cost_scaffold(tmp_path))
    assert table["n"] == 2
    assert table["roles"]["red"]["meta_usd"] == round(0.0026 * 2, 4)
    assert table["roles"]["auditor"]["meta_usd"] == round(0.013 * 2, 4)
    assert table["roles"]["blue"]["baseten_tokens"] == 77000 * 2
    assert table["roles"]["referee"] == {"meta_usd": 0.0,
                                         "baseten_tokens": 0,
                                         "meta_per_exercise": 0.0,
                                         "tokens_per_exercise": 0}
    assert table["roles"]["debrief"] == {"meta_usd": 0.0,
                                         "baseten_tokens": 0,
                                         "meta_per_exercise": 0.0,
                                         "tokens_per_exercise": 0}
    assert table["meta_usd"] == round(0.0026 * 2 + 0.013 * 2, 4)
    assert table["unattributed_usd"] == 0.0
    assert table["tokens_per_exercise"] == 77000


def test_cost_markdown_is_table_plus_three_sentences(tmp_path):
    table = _cost_table(_cost_scaffold(tmp_path))
    md = stage3_report.render_cost_markdown(table)
    assert "| red | $0.00 | n/a |" in md
    assert "| blue | n/a | 77,000 |" in md
    assert "| auditor | $0.01 | n/a |" in md
    assert "| total | $0.02 | 77,000 |" in md
    context = md.rsplit("\n\n", 1)[1].strip()
    assert context.count(". ") == 2 and context.endswith(".")
    assert "401" in context


def test_cost_only_writes_cost_md(tmp_path):
    scaffold = _cost_scaffold(tmp_path)
    out_cost = tmp_path / "COST.md"
    code = stage3_report.main([
        "--cost-only", "--briefed", str(scaffold["briefed"]),
        "--spend-file", str(scaffold["spend"]),
        "--archive-dir", str(scaffold["archive"]),
        "--out-cost", str(out_cost)])
    assert code == 0
    text = out_cost.read_text(encoding="utf-8")
    assert "| blue | n/a | 77,000 |" in text


def test_cost_only_requires_briefed_bench(tmp_path):
    code = stage3_report.main([
        "--cost-only", "--briefed", str(tmp_path / "missing"),
        "--out-cost", str(tmp_path / "COST.md")])
    assert code == 1
