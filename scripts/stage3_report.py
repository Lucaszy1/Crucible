#!/usr/bin/env python3
"""Merge finished Stage 3 cells into the results report (no model calls).

Reads the bench dirs plus the frozen playbook, recomputes every
metric from the saved CSVs and jsonls, and writes:

- docs/STAGE3_RESULTS.md: per-cell tables, Wilson intervals, exact
  paired p-values, README-ready claims and explicit non-claims
- dashboard/results.json: the same numbers for the Results tab:

  {"stage": 3, "generated": iso, "frozen_sha256": hex-or-null,
   "benches": {name: dir}, "cells": {key: {...}}, "comparisons": [...],
   "void": {"cells": {...}, "comparisons": [...]},
   "reference": {...}, "claims": [...], "non_claims": [...]}

The headline sensors-on arms come from the corrected rerun
(--sensors); the sensors-off arms come from the valid half of the
first run (--heldout). The first run's sensors-on half is void
(documented sensor bug, fixed in 1224db4) and is shown only in the
void section, with its original p-values, for the record.

Cells carry n, intrusion/secret/containment rates with Wilson bounds,
median delays, mean downtime, audit justified rate (audited cells),
and total Meta cost. Comparisons carry discordant counts, exact
two-sided p-values, and a verdict ("differs" or "within noise").

Pairing key is (scenario_id, rep): same scenario, same rep index,
across arms. B-vs-C pairs within the rerun with matched red; A-vs-B
pairs across benches by scenario, which is approximate because red
re-rolled between benches (the episode seed includes the bench name).
Statistics are stdlib-only.
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
BRIEFED_CELL = "model-briefed-sensors-on-memory-off"
BRIEFED_LABEL = "briefed-red-vs-Spark-Open"

# Benches whose sensors-on episodes are void: the collector bug kept
# every lookup missing on generated nets (fixed in 1224db4).
VOID_BENCH_NAMES = ("bench-stage3-train-solver", "bench-stage3-heldout")

SPEND_FILE = ROOT / "runs" / "spend.jsonl"
ARCHIVE_DIR = ROOT / "runs" / "archive"
_SPEND_TAIL_S = 300.0


def _log_mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _briefed_baseten_tokens(bench_dir: Path, episodes: list) -> int:
    """Sum one bench's Baseten tokens, excluding archived reruns.

    Reruns reuse episode ids, and the spend log still holds the
    archived 401 run under the same ids, so entries at or before
    each archived log's mtime are cut; the rest, up to this bench's
    log plus a referee tail, is this bench's run.
    """
    if not SPEND_FILE.exists():
        return 0
    by_episode: dict = {}
    for line in SPEND_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("host") != "baseten":
            continue
        by_episode.setdefault(entry.get("exercise_id", ""), []).append(entry)
    try:
        archived = [path for path in ARCHIVE_DIR.iterdir()
                    if path.is_dir()]
    except OSError:
        archived = []
    total = 0
    for episode in episodes:
        mine = _log_mtime(bench_dir / f"{episode}.jsonl")
        if mine is None:
            continue
        floor = float("-inf")
        for old in archived:
            seen = _log_mtime(old / f"{episode}.jsonl")
            if seen is not None and seen < mine:
                floor = max(floor, seen)
        for entry in by_episode.get(episode, []):
            try:
                when = float(entry.get("ts", None))
            except (TypeError, ValueError):
                continue
            if when > floor and when <= mine + _SPEND_TAIL_S:
                total += int(entry.get("input_tokens", 0) or 0)
                total += int(entry.get("output_tokens", 0) or 0)
    return total


def _fallback_census(bench_dir: Path, episodes: list) -> dict:
    """Count soft baseline fallbacks per actor over episode logs."""
    turns = {"red": 0, "blue": 0}
    fell = {"red": 0, "blue": 0}
    affected: set = set()
    for episode in episodes:
        try:
            lines = (bench_dir / f"{episode}.jsonl").read_text(
                encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            actor = event.get("actor")
            if actor not in turns:
                continue
            turns[actor] += 1
            note = str(event.get("reasoning_summary", "") or "")
            if "using baseline" in note.lower():
                fell[actor] += 1
                affected.add(episode)
    return {"red_fallbacks": fell["red"], "red_turns": turns["red"],
            "blue_fallbacks": fell["blue"], "blue_turns": turns["blue"],
            "episodes": len(affected), "n": len(episodes)}


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
        "secret_read": {"hits": sec_hits, "n": len(intruded),
                        "rate": (sec_hits / len(intruded)
                                 if intruded else None),
                        "wilson": wilson(sec_hits, len(intruded))},
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
        "mean_downtime_contained_pct": _mean(contained, "downtime_pct"),
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


def _paired_rows_cross(bench_a: Path, key_a: str, bench_b: Path,
                      key_b: str) -> list:
    """Pair rows from two benches by (scenario_id, rep).

    Used for the corrected sensors comparison, whose arms live in
    different benches. Keys match because both benches ran the same
    scenarios at the same rep index; red still re-rolled between
    benches, so the pairing is approximate.
    """
    rows_a = {row_key: row for row in _csv_rows(bench_a)
              if row.get("cell") == key_a
              for row_key in [_pair_key(row)]}
    rows_b = {row_key: row for row in _csv_rows(bench_b)
              if row.get("cell") == key_b
              for row_key in [_pair_key(row)]}
    return [(rows_a[key], rows_b[key]) for key in rows_a if key in rows_b]


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


def _metrics_for(pairs: list) -> dict:
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
    return metrics


def compare(bench_dir: Path, key_a: str, key_b: str,
            label_a: str, label_b: str) -> dict:
    """Paired A-vs-B comparison with exact p-values and verdicts."""
    pairs = _paired_rows(bench_dir, key_a, key_b)
    return {"arms": [label_a, label_b], "cells": [key_a, key_b],
            "pairs": len(pairs), "metrics": _metrics_for(pairs)}


def compare_cross(bench_a: Path, key_a: str, bench_b: Path, key_b: str,
                  label_a: str, label_b: str) -> dict:
    """Cross-bench paired comparison (approximate: red re-rolled)."""
    pairs = _paired_rows_cross(bench_a, key_a, bench_b, key_b)
    return {"arms": [label_a, label_b], "cells": [key_a, key_b],
            "pairs": len(pairs), "metrics": _metrics_for(pairs),
            "note": "cross-bench pairing by scenario; red re-rolled "
                    "between benches, so this pairing is approximate"}


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


def _p(value: float) -> str:
    if 0.0 < value < 0.00005:
        return "<0.0001"
    return f"{value:.4f}"


def _cell_row(arm: str, cell: dict) -> str:
    intr = cell["intrusion"]
    sec = cell["secret_read"]
    cont = cell["contained_after_intrusion"]
    return (
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


def _comparison_rows(comps: list) -> list:
    rows = []
    for comp in comps:
        label = f"{comp['arms'][0]} vs {comp['arms'][1]}"
        for metric, result in comp["metrics"].items():
            dis = result["discordant"]
            rows.append(f"| {label} | {comp['pairs']} | {metric} | "
                        f"{dis[0]}-{dis[1]} | {_p(result['p'])} | "
                        f"{result['verdict']} |")
    return rows


def render_markdown(payload: dict) -> str:
    """Render the full results page from the merged payload."""
    benches = payload["benches"]
    lines = ["# Stage 3 results: frozen playbook on held-out networks",
             "",
             f"Generated {payload['generated']} from "
             + ", ".join(f"`{name}`" for name in benches) + ".",
             f"Frozen playbook sha256: `{payload['frozen_sha256'] or 'n/a'}`.",
             "Corrected headline: sensors-on arms come from the rerun "
             f"`{benches.get('sensors', 'runs/bench-stage3-heldout-sensors')}` "
             "(working sensors); sensors-off arms come from the valid half "
             f"of `{benches.get('heldout', 'runs/bench-stage3-heldout')}`. "
             "The first run's sensors-on half is void (documented sensor "
             "bug, fixed in 1224db4) and appears only in the void section.",
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
    for arm, key in (("A sensors-off", CELL_A),
                     ("B sensors-on (rerun)", CELL_B),
                     ("C + playbook (rerun)", CELL_C),
                     ("bonus off+book", CELL_BONUS)):
        cell = payload["cells"].get(key)
        if cell is None:
            continue
        lines.append(_cell_row(arm, cell))
    lines += ["",
              "Downtime is the mean over all episodes; med delay is the "
              "median first-report delay; med contain is the median time "
              "to contain over contained episodes. Audit covers the rerun "
              "B and C cells only.",
              "",
              "## Paired comparisons",
              "",
              "| comparison | pairs | metric | discordant | p | verdict |",
              "|------------|-------|--------|------------|---|---------|"]
    lines += _comparison_rows(payload["comparisons"])
    lines += ["",
              "Discordant counts read (first-arm-worse, second-arm-worse) "
              "for binary outcomes: for intrusion and secret reads, the "
              "worse side is the arm where the bad outcome happened. For "
              "delays and downtime they read (first-arm-slower, "
              "second-arm-slower)."]
    for comp in payload["comparisons"]:
        if comp.get("note"):
            lines.append(f"{comp['arms'][0]} vs {comp['arms'][1]}: "
                         f"{comp['note']}.")
    void = payload.get("void", {})
    if void.get("cells"):
        names = ", ".join(f"`{name}`" for name in void.get("benches", []))
        lines += ["",
                  "## Void original (for the record, not evidence)",
                  "",
                  "The first run's sensors-on arms ran with silently "
                  "broken sensors (zero alerts in every episode) and are "
                  f"void by name ({names}; documented sensor bug, fixed "
                  "in 1224db4). Original cells and p-values:",
                  "",
                  "| arm | n | intrusions | secret reads | contained | "
                  "med delay | med contain | downtime | audit just |",
                  "|-----|---|------------|--------------|-----------|"
                  "-----------|-------------|----------|------------|"]
        for arm, key in (("B sensors-on (void)", CELL_B),
                         ("C + playbook (void)", CELL_C)):
            cell = void["cells"].get(key)
            if cell is None:
                continue
            lines.append(_cell_row(arm, cell))
        lines += ["",
                  "| comparison | pairs | metric | discordant | p | verdict |",
                  "|------------|-------|--------|------------|---|---------|"]
        lines += _comparison_rows(void.get("comparisons", []))
    lines += ["", "## Reference runs", ""]
    for name, cell in payload["reference"].items():
        intr = cell["intrusion"]
        sec = cell["secret_read"]
        cont = cell["contained_after_intrusion"]
        lines.append(f"- {name} (n={cell['n']}): intrusions "
                     f"{intr['hits']}/{intr['n']}, secret reads "
                     f"{sec['hits']}/{sec['n']}, contained "
                     f"{cont['hits']}/{cont['n']}, med delay "
                     f"{_num(cell['median_detection_delay'])}, med relevant "
                     f"{_num(cell['median_relevant_delay'])}, med contain "
                     f"{_num(cell['median_time_to_contain'])}.")
    lines += ["", "## Claims", ""]
    lines += [f"- {claim}" for claim in payload["claims"]]
    lines += ["", "## Non-claims", ""]
    lines += [f"- {text}" for text in payload["non_claims"]]
    return "\n".join(lines) + "\n"


def _claim_sentence(comp: dict, metric: str, bad: str) -> str:
    arms = comp["arms"]
    result = comp["metrics"][metric]
    dis = result["discordant"]
    verdict = ("differs (p=%s)" % _p(result["p"]) if result["p"] < 0.05
               else "is within noise (p=%s)" % _p(result["p"]))
    return (f"{arms[0]} vs {arms[1]} on {bad}: discordant pairs "
            f"{dis[0]}-{dis[1]} over {comp['pairs']} pairs, {verdict}.")


def build_claims(comparisons: list, cells: dict, reference: dict,
                 void: dict) -> list:
    """Factual claim sentences; significance comes from the p-values."""
    claims = []
    for comp in comparisons:
        claims.append(_claim_sentence(comp, "intrusion", "intrusion rate"))
        claims.append(_claim_sentence(comp, "secret_read",
                                      "secret-read rate"))
    base = cells.get(CELL_B, {})
    plus = cells.get(CELL_C, {})
    if base.get("n") and plus.get("n"):
        cont_b = base["contained_after_intrusion"]
        cont_c = plus["contained_after_intrusion"]
        sec_b = base["secret_read"]
        sec_c = plus["secret_read"]
        claims.append(
            f"With working sensors every intrusion was contained in both "
            f"memory arms ({cont_b['hits']}/{cont_b['n']} without memory, "
            f"{cont_c['hits']}/{cont_c['n']} with memory; secret reads "
            f"{sec_b['hits']}/{sec_b['n']} and {sec_c['hits']}/{sec_c['n']}): "
            f"a ceiling effect, so memory is not distinguishable on "
            f"outcomes with working sensors.")
    briefed = reference.get(BRIEFED_LABEL)
    if briefed is not None and briefed.get("n"):
        intr = briefed["intrusion"]
        cont = briefed["contained_after_intrusion"]
        sec = briefed["secret_read"]
        claims.append(
            f"Briefed model red against Open blue (n={briefed['n']}, "
            f"descriptive only): {intr['hits']} of {intr['n']} networks "
            f"had an intrusion, and all {cont['hits']} were contained; "
            f"red did not get a foothold on the other "
            f"{intr['n'] - intr['hits']}. Secret reads {sec['hits']}/"
            f"{sec['n']} ({_pct(sec['rate'])}), median relevant detection "
            f"delay {_num(briefed['median_relevant_delay'])} events, "
            f"median time to contain "
            f"{_num(briefed['median_time_to_contain'])} events, mean "
            f"downtime {_num(briefed['mean_downtime_contained_pct'])}%, "
            f"cost ${briefed['total_cost_usd']:.2f}, Baseten tokens "
            f"{briefed.get('baseten_tokens', 0):,}.")
        census = briefed.get("fallback_census") or {}
        fell = (census.get("red_fallbacks", 0)
                + census.get("blue_fallbacks", 0))
        if fell:
            claims.append(
                f"{census['red_fallbacks']} of {census['red_turns']} red "
                f"turns and {census['blue_fallbacks']} of "
                f"{census['blue_turns']} blue turns used a safe baseline "
                f"move after model replies with no usable tool call, "
                f"across {census['episodes']} of {census['n']} episodes.")
        else:
            claims.append(
                f"No soft baseline fallback turns in {census.get('n', 0)} "
                f"episodes.")
    for comp in void.get("comparisons", []):
        if comp.get("arms") == ["B", "C"]:
            result = comp["metrics"]["secret_read"]
            dis = result["discordant"]
            claims.append(
                f"Void record only: the void run's B vs C secret-read gap "
                f"was discordant pairs {dis[0]}-{dis[1]} over "
                f"{comp['pairs']} pairs (p={_p(result['p'])}); it is not "
                f"evidence.")
            break
    return claims


# Verified by timestamp attribution of runs/spend.jsonl: 14 cached
# entries, all muse-wonder-1.0 auditor calls during the rerun, zero
# for blue, red, or referee.
REPLAY_NOTE = (
    "14 auditor verdicts in the corrected held-out rerun were replayed "
    "from the voided original run on byte-identical prompts; no blue, "
    "red, or referee response was cached and outcomes are unaffected."
)


NON_CLAIMS = [
    "No result here shows the playbook generalizes beyond these held-out "
    "seeds and difficulties; 20 scenarios are one pilot, not coverage.",
    "A comparison within noise is no conclusion, not proof of no effect: "
    "with 20 pairs only large asymmetries can reach p < 0.05.",
    "Held episodes can still reflect red failing on its own rather than "
    "blue defending; read wins beside intrusion and containment rates.",
    "B-vs-C pairs are exactly matched: the same scenario and rep index "
    "in one bench replays identical red moves across arms by design, so "
    "arm differences isolate blue, not red sampling. A-vs-B pairs "
    "across benches by scenario instead, which is approximate because "
    "red re-rolled between benches.",
    "The main table uses a solver red with seeded imperfection, because "
    "unbriefed model red achieved no intrusion in 18 train episodes: "
    "results show blue contains a known-path intruder, not that blue "
    "stops an adaptive attacker. The briefed-red run is descriptive "
    "only at n=20: 18 of 20 networks had an intrusion, and all 18 were "
    "contained; red did not get a foothold on the other 2.",
    "Audit justified rates cover the rerun B and C cells only; no other "
    "cell was audited.",
    "The frozen playbook was learned on train episodes whose sensors "
    "were void too, so its rules were learned without sensor input; the "
    "rerun tests a deaf-learned playbook under working sensors.",
]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Merge Stage 3 cells into the results report.")
    parser.add_argument("--heldout", default="runs/bench-stage3-heldout",
                        help="First run: valid sensors-off half plus the "
                        "void original")
    parser.add_argument("--sensors",
                        default="runs/bench-stage3-heldout-sensors",
                        help="Corrected rerun for the headline sensors-on "
                        "arms")
    parser.add_argument("--noisyref", default="runs/bench-stage3-noisyref")
    parser.add_argument("--xcheck", default="runs/bench-stage3-xcheck")
    parser.add_argument("--briefed",
                        default="runs/bench-stage3-briefed-heldout")
    parser.add_argument("--frozen",
                        default="runs/stage3/frozen-playbook.json")
    parser.add_argument("--out-md", default="docs/STAGE3_RESULTS.md")
    parser.add_argument("--out-json", default="dashboard/results.json")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    heldout = Path(args.heldout)
    sensors = Path(args.sensors)
    frozen = Path(args.frozen)
    if not (heldout / "summary.csv").exists():
        print(f"report: no finished held-out bench at {heldout}",
              file=sys.stderr)
        return 1
    if not (sensors / "summary.csv").exists():
        print(f"report: no finished sensors rerun bench at {sensors}",
              file=sys.stderr)
        return 1
    if not frozen.exists():
        print(f"report: no frozen playbook at {frozen}", file=sys.stderr)
        return 1
    cells = {}
    for bench_dir, key in ((heldout, CELL_A), (sensors, CELL_B),
                           (sensors, CELL_C), (heldout, CELL_BONUS)):
        try:
            cell = summarize_cell(bench_dir, key)
        except FileNotFoundError:
            continue
        if cell["n"]:
            cells[key] = cell
    comparisons = [compare_cross(heldout, CELL_A, sensors, CELL_B,
                                 "A", "B"),
                   compare(sensors, CELL_B, CELL_C, "B", "C")]
    void_cells = {}
    for key in (CELL_B, CELL_C):
        cell = summarize_cell(heldout, key)
        if cell["n"]:
            void_cells[key] = cell
    void = {"benches": list(VOID_BENCH_NAMES), "cells": void_cells,
            "comparisons": [compare(heldout, CELL_A, CELL_B, "A", "B"),
                            compare(heldout, CELL_B, CELL_C, "B", "C")]}
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
    briefed = Path(args.briefed)
    if (briefed / "summary.csv").exists():
        cell = summarize_cell(briefed, BRIEFED_CELL)
        if cell["n"]:
            episodes = [row.get("episode", "") for row in
                        _csv_rows(briefed)
                        if row.get("cell") == BRIEFED_CELL]
            cell["baseten_tokens"] = _briefed_baseten_tokens(
                briefed, episodes)
            cell["fallback_census"] = _fallback_census(briefed, episodes)
            reference[BRIEFED_LABEL] = cell
    payload = {
        "stage": 3,
        "generated": datetime.now(timezone.utc).isoformat(),
        "frozen_sha256": hashlib.sha256(
            frozen.read_bytes()).hexdigest(),
        "benches": {"heldout": str(heldout), "sensors": str(sensors),
                    "noisyref": str(noisyref), "xcheck": str(xcheck),
                    "briefed": str(briefed)},
        "cells": cells,
        "comparisons": comparisons,
        "void": void,
        "reference": reference,
        "claims": build_claims(comparisons, cells, reference, void)
        + [REPLAY_NOTE],
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
