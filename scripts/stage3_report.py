#!/usr/bin/env python3
"""Merge finished Stage 3 cells into the results report (no model calls).

Reads the four bench dirs plus the frozen playbook, recomputes every
metric from the saved CSVs and jsonls, and writes:

- docs/STAGE3_RESULTS.md: per-cell tables, Wilson intervals, exact
  paired p-values, README-ready claims and explicit non-claims
- dashboard/results.json: the same numbers for the Results tab:

  {"stage": 3, "generated": iso, "frozen_sha256": hex-or-null,
   "benches": {name: dir}, "cells": {key: {...}}, "comparisons": [...],
   "claims": [...], "non_claims": [...]}

Cells carry n, intrusion/secret/containment rates with Wilson bounds,
median delays, mean downtime, audit justified rate (audited cells),
and total Meta cost. Comparisons carry discordant counts, exact
two-sided p-values, and a verdict ("differs" or "within noise").

Pairing key is (scenario_id, rep): same scenario, same rep index,
across arms. Statistics are stdlib-only.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CELL_A = "solver-sensors-off-memory-off"
CELL_B = "solver-sensors-on-memory-off"
CELL_C = "solver-sensors-on-memory-on"
CELL_BONUS = "solver-sensors-off-memory-on"


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _csv_rows(bench_dir: Path) -> list:
    with (bench_dir / "summary.csv").open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _jsonl_events(path: Path) -> list:
    return [json.loads(line) for line in path.read_text(encoding="utf-8")
            .splitlines() if line.strip()]


def wilson(hits: int, total: int, z: float = 1.96) -> tuple:
    """Wilson 95% interval for a rate; (0, 1) when total is 0."""
    if total <= 0:
        return (0.0, 1.0)
    center = (hits + z * z / 2) / (total + z * z)
    half = z * math.sqrt(hits * (total - hits) / total + z * z / 4)
    half /= total + z * z
    return (max(0.0, center - half), min(1.0, center + half))


def _binom_tail(total: int, upto: int) -> float:
    return sum(math.comb(total, k) for k in range(upto + 1)) / (2 ** total)


def exact_paired_p(wins_a: int, wins_b: int) -> float:
    """Exact two-sided paired p-value (McNemar / sign test form).

    wins_a/wins_b are the discordant counts in each direction; ties
    are dropped before calling. p = 2 * lower binomial tail, capped.
    """
    total = wins_a + wins_b
    if total == 0:
        return 1.0
    return min(1.0, 2.0 * _binom_tail(total, min(wins_a, wins_b)))


def _rate(rows: list, key: str) -> tuple:
    hits = sum(1 for row in rows if row.get(key) == "True")
    return hits, len(rows)


def _median(rows: list, key: str):
    values = []
    for row in rows:
        raw = (row.get(key) or "").strip()
        if not raw:
            continue
        try:
            values.append(float(raw))
        except ValueError:
            continue
    if not values:
        return None
    return statistics.median(values)


def _mean(rows: list, key: str):
    values = []
    for row in rows:
        try:
            values.append(float(row.get(key, 0.0) or 0.0))
        except ValueError:
            continue
    if not values:
        return None
    return sum(values) / len(values)


def _audit_rate(bench_dir: Path, rows: list) -> tuple:
    """Pooled justified fraction and episode count with audits."""
    justified = 0
    total = 0
    episodes = 0
    for row in rows:
        path = bench_dir / f"{row.get('episode', '')}.jsonl"
        if not path.exists():
            continue
        verdicts = [(event.get("result", {}) or {}).get("verdict")
                    for event in _jsonl_events(path)
                    if event.get("actor") == "auditor"]
        if not verdicts:
            continue
        episodes += 1
        justified += sum(1 for verdict in verdicts if verdict == "justified")
        total += len(verdicts)
    if not total:
        return None, 0
    return justified / total, episodes


def summarize_cell(bench_dir: Path, key: str) -> dict:
    """Recompute one cell's metrics from its CSV rows and jsonls."""
    rows = [row for row in _csv_rows(bench_dir) if row.get("cell") == key]
    intruded = [row for row in rows if row.get("intrusion") == "True"]
    contained = [row for row in intruded
                 if row.get("contained_after_intrusion") == "True"]
    intr_hits, intr_n = _rate(rows, "intrusion")
    sec_hits, _sec_n = _rate(rows, "secret_read")
    audit_rate, audit_eps = _audit_rate(bench_dir, rows)
    return {
        "cell": key,
        "n": len(rows),
        "intrusion": {"hits": intr_hits, "n": intr_n,
                      "rate": intr_hits / intr_n if intr_n else None,
                      "wilson": wilson(intr_hits, intr_n)},
        "secret_read": {"hits": sec_hits, "n": intr_n,
                        "rate": sec_hits / intr_n if intr_n else None,
                        "wilson": wilson(sec_hits, intr_n)},
        "contained_after_intrusion": {
            "hits": len(contained), "n": len(intruded),
            "rate": (len(contained) / len(intruded)
                     if intruded else None),
            "wilson": wilson(len(contained), len(intruded))},
        "median_relevant_delay": _median(intruded,
                                         "relevant_detection_delay"),
        "median_detection_delay": _median(rows, "detection_delay"),
        "median_time_to_contain": _median(contained, "time_to_contain"),
        "mean_downtime_pct": _mean(rows, "downtime_pct"),
        "audit_justified_rate": audit_rate,
        "audit_episodes": audit_eps,
        "total_cost_usd": round(sum(float(row.get("cost", 0.0) or 0.0)
                                     for row in rows), 4),
    }


def _pair_key(row: dict) -> tuple:
    episode = row.get("episode", "")
    rep = episode.rsplit("-", 1)[-1] if "-" in episode else episode
    return (row.get("scenario_id", "classic"), rep)


def _paired_rows(bench_dir: Path, key_a: str, key_b: str) -> list:
    rows = _csv_rows(bench_dir)
    by_pair: dict = {}
    for row in rows:
        if row.get("cell") not in (key_a, key_b):
            continue
        by_pair.setdefault(_pair_key(row), {})[row["cell"]] = row
    return [(pair[key_a], pair[key_b]) for pair in by_pair.values()
            if key_a in pair and key_b in pair]


def _binary_comparison(pairs: list, key: str) -> dict:
    wins_a = sum(1 for row_a, row_b in pairs
                 if row_a.get(key) == "True" and row_b.get(key) != "True")
    wins_b = sum(1 for row_a, row_b in pairs
                 if row_b.get(key) == "True" and row_a.get(key) != "True")
    return {"discordant": [wins_a, wins_b],
            "p": exact_paired_p(wins_a, wins_b)}


def _continuous_comparison(pairs: list, key: str) -> dict:
    diffs = []
    for row_a, row_b in pairs:
        try:
            value_a = float((row_a.get(key) or "").strip())
            value_b = float((row_b.get(key) or "").strip())
        except ValueError:
            continue
        if value_a != value_b:
            diffs.append(value_a - value_b)
    # Slower is worse, matching the binary (worse-side) convention.
    wins_a = sum(1 for diff in diffs if diff > 0)
    wins_b = sum(1 for diff in diffs if diff < 0)
    medians = []
    for index in (0, 1):
        values = []
        for pair in pairs:
            try:
                values.append(float((pair[index].get(key) or "").strip()))
            except ValueError:
                continue
        medians.append(statistics.median(values) if values else None)
    return {"discordant": [wins_a, wins_b],
            "p": exact_paired_p(wins_a, wins_b),
            "medians": medians, "paired_n": len(diffs)}


def compare(bench_dir: Path, key_a: str, key_b: str,
            label_a: str, label_b: str) -> dict:
    """Paired A-vs-B comparison with exact p-values and verdicts."""
    pairs = _paired_rows(bench_dir, key_a, key_b)
    metrics = {}
    for key in ("intrusion", "secret_read"):
        result = _binary_comparison(pairs, key)
        result["verdict"] = ("differs" if result["p"] < 0.05
                             else "within noise")
        metrics[key] = result
    for key in ("detection_delay", "downtime_pct"):
        result = _continuous_comparison(pairs, key)
        result["verdict"] = ("differs" if result["p"] < 0.05
                             else "within noise")
        metrics[key] = result
    return {"arms": [label_a, label_b], "cells": [key_a, key_b],
            "pairs": len(pairs), "metrics": metrics}


def _pct(value) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _num(value) -> str:
    if value is None:
        return "n/a"
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}"


def _interval(pair) -> str:
    return f"({_pct(pair[0])}, {_pct(pair[1])})"


def render_markdown(payload: dict) -> str:
    """Render the full results page from the merged payload."""
    lines = ["# Stage 3 results: frozen playbook on held-out networks",
             "",
             f"Generated {payload['generated']} from "
             + ", ".join(f"`{name}`" for name in payload["benches"]) + ".",
             f"Frozen playbook sha256: `{payload['frozen_sha256'] or 'n/a'}`.",
             "Rates carry Wilson 95% intervals; paired p-values are exact "
             "two-sided (McNemar for binary outcomes, sign test for delays "
             "and downtime). p >= 0.05 is reported as within noise.",
             "",
             "## Held-out cells (solver red, paired scenarios)",
             "",
             "| arm | n | intrusions | secret reads | contained | "
             "med delay | med contain | downtime | audit just |",
             "|-----|---|------------|--------------|-----------|"
             "-----------|-------------|----------|------------|"]
    for arm, key in (("A sensors-off", CELL_A), ("B sensors-on", CELL_B),
                     ("C + playbook", CELL_C), ("bonus off+book", CELL_BONUS)):
        cell = payload["cells"].get(key)
        if cell is None:
            continue
        intr = cell["intrusion"]
        sec = cell["secret_read"]
        cont = cell["contained_after_intrusion"]
        lines.append(
            f"| {arm} | {cell['n']} "
            f"| {intr['hits']}/{intr['n']} {_pct(intr['rate'])} "
            f"{_interval(intr['wilson'])} "
            f"| {sec['hits']}/{sec['n']} {_pct(sec['rate'])} "
            f"{_interval(sec['wilson'])} "
            f"| {cont['hits']}/{cont['n']} {_pct(cont['rate'])} "
            f"| {_num(cell['median_detection_delay'])} "
            f"| {_num(cell['median_time_to_contain'])} "
            f"| {_num(cell['mean_downtime_pct'])}% "
            f"| {_pct(cell['audit_justified_rate'])} |")
    lines += ["",
              "Downtime is the mean over all episodes; med delay is the "
              "median first-report delay; med contain is the median time "
              "to contain over contained episodes. Audit covers the B and "
              "C cells only.",
              "",
              "## Paired comparisons",
              "",
              "| comparison | pairs | metric | discordant | p | verdict |",
              "|------------|-------|--------|------------|---|---------|"]
    for comp in payload["comparisons"]:
        label = f"{comp['arms'][0]} vs {comp['arms'][1]}"
        for metric, result in comp["metrics"].items():
            dis = result["discordant"]
            lines.append(f"| {label} | {comp['pairs']} | {metric} | "
                         f"{dis[0]}-{dis[1]} | {result['p']:.4f} | "
                         f"{result['verdict']} |")
    lines += ["",
              "Discordant counts read (first-arm-worse, second-arm-worse) "
              "for binary outcomes: for intrusion and secret reads, the "
              "worse side is the arm where the bad outcome happened. For "
              "delays and downtime they read (first-arm-slower, "
              "second-arm-slower).",
              "",
              "## Reference runs",
              ""]
    for name, cell in payload["reference"].items():
        intr = cell["intrusion"]
        sec = cell["secret_read"]
        lines.append(f"- {name} (n={cell['n']}): intrusions "
                     f"{intr['hits']}/{intr['n']}, secret reads "
                     f"{sec['hits']}/{sec['n']}, med delay "
                     f"{_num(cell['median_detection_delay'])}.")
    lines += ["", "## Claims", ""]
    lines += [f"- {claim}" for claim in payload["claims"]]
    lines += ["", "## Non-claims", ""]
    lines += [f"- {text}" for text in payload["non_claims"]]
    return "\n".join(lines) + "\n"


def _claim_sentence(comp: dict, metric: str, bad: str) -> str:
    arms = comp["arms"]
    result = comp["metrics"][metric]
    dis = result["discordant"]
    verdict = ("differs (p=%.4f)" % result["p"] if result["p"] < 0.05
               else "is within noise (p=%.4f)" % result["p"])
    return (f"{arms[0]} vs {arms[1]} on {bad}: discordant pairs "
            f"{dis[0]}-{dis[1]} over {comp['pairs']} pairs, {verdict}.")


def build_claims(comparisons: list) -> list:
    """Factual claim sentences; significance comes from the p-values."""
    claims = []
    for comp in comparisons:
        claims.append(_claim_sentence(comp, "intrusion", "intrusion rate"))
        claims.append(_claim_sentence(comp, "secret_read",
                                      "secret-read rate"))
    return claims


NON_CLAIMS = [
    "No result here shows the playbook generalizes beyond these held-out "
    "seeds and difficulties; 20 scenarios are one pilot, not coverage.",
    "A comparison within noise is no conclusion, not proof of no effect: "
    "with 20 pairs only large asymmetries can reach p < 0.05.",
    "Held episodes can still reflect red failing on its own rather than "
    "blue defending; read wins beside intrusion and containment rates.",
    "Solver-red pairs are exactly matched: the same scenario and rep "
    "index replays identical red moves across arms by design, so arm "
    "differences isolate blue, not red sampling.",
    "Model red achieved no intrusion in 18 train episodes, so the "
    "defense comparison uses a solver red with seeded imperfection: "
    "results show blue contains a known-path intruder, not that blue "
    "stops an adaptive attacker.",
    "Audit justified rates cover the B and C cells only; no other cell "
    "was audited.",
]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Merge Stage 3 cells into the results report.")
    parser.add_argument("--heldout", default="runs/bench-stage3-heldout")
    parser.add_argument("--noisyref", default="runs/bench-stage3-noisyref")
    parser.add_argument("--xcheck", default="runs/bench-stage3-xcheck")
    parser.add_argument("--frozen",
                        default="runs/stage3/frozen-playbook.json")
    parser.add_argument("--out-md", default="docs/STAGE3_RESULTS.md")
    parser.add_argument("--out-json", default="dashboard/results.json")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    heldout = Path(args.heldout)
    frozen = Path(args.frozen)
    if not (heldout / "summary.csv").exists():
        print(f"report: no finished held-out bench at {heldout}",
              file=sys.stderr)
        return 1
    if not frozen.exists():
        print(f"report: no frozen playbook at {frozen}", file=sys.stderr)
        return 1
    cells = {}
    for key in (CELL_A, CELL_B, CELL_C, CELL_BONUS):
        try:
            cells[key] = summarize_cell(heldout, key)
        except FileNotFoundError:
            continue
    cells = {key: cell for key, cell in cells.items() if cell["n"]}
    comparisons = [compare(heldout, CELL_A, CELL_B, "A", "B"),
                   compare(heldout, CELL_B, CELL_C, "B", "C")]
    reference = {}
    noisyref = Path(args.noisyref)
    if (noisyref / "summary.csv").exists():
        for key in ("noisy-sensors-on-memory-off",
                    "noisy-sensors-on-memory-on"):
            cell = summarize_cell(noisyref, key)
            if cell["n"]:
                reference[key] = cell
    xcheck = Path(args.xcheck)
    if (xcheck / "summary.csv").exists():
        key = "solver-sensors-on-memory-off"
        cell = summarize_cell(xcheck, key)
        if cell["n"]:
            reference[f"spark-blue:{key}"] = cell
    payload = {
        "stage": 3,
        "generated": datetime.now(timezone.utc).isoformat(),
        "frozen_sha256": hashlib.sha256(
            frozen.read_bytes()).hexdigest(),
        "benches": {"heldout": str(heldout),
                    "noisyref": str(noisyref), "xcheck": str(xcheck)},
        "cells": cells,
        "comparisons": comparisons,
        "reference": reference,
        "claims": build_claims(comparisons),
        "non_claims": list(NON_CLAIMS),
    }
    md_path = Path(args.out_md)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    json_path = Path(args.out_json)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2) + "\n",
                         encoding="utf-8")
    print(f"wrote {md_path} and {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
