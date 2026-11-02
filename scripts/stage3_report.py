#!/usr/bin/env python3
"""Merge finished Stage 3 cells into the results report (no model calls).

Reads the bench dirs plus the frozen playbook, recomputes every
metric from the saved CSVs and jsonls, and writes:

- docs/STAGE3_RESULTS.md: per-cell tables, Wilson intervals, exact
  paired p-values, README-ready claims and explicit non-claims
- docs/COST.md: per-exercise cost and token averages by role for
  the end-to-end (briefed red vs open blue) bench (--cost-only)
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
import re
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
BRIEFED_SENSORS_OFF_CELL = "model-briefed-sensors-off-memory-off"
BRIEFED_LABEL = "briefed-red-vs-Spark-Open"
BRIEFED_SPARK_LABEL = "briefed-red-vs-Spark"

# Benches whose sensors-on episodes are void: the collector bug kept
# every lookup missing on generated nets (fixed in 1224db4).
VOID_BENCH_NAMES = ("bench-stage3-train-solver", "bench-stage3-heldout")

SPEND_FILE = ROOT / "runs" / "spend.jsonl"
ARCHIVE_DIR = ROOT / "runs" / "archive"
_SPEND_TAIL_S = 300.0

# Cost table models (mirror crucible/llm.py ROLE_MODELS): stage3 blue
# runs the open-weights model on Baseten while red and the auditor
# bill Meta. Auditor, referee, and debrief share muse-wonder-1.0,
# but the stage3 benches froze the playbook (referee never called)
# and never run debrief, so every wonder entry in bench scope is
# the auditor.
RED_MODEL = "muse-spark-1.3"
AUDIT_MODEL = "muse-wonder-1.0"
COST_ROLES = ("red", "blue", "auditor", "referee", "debrief")


def _log_mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _read_spend(spend_file: Path) -> dict:
    """Map exercise id to its spend entries; blanks and bad lines skipped."""
    by_episode: dict = {}
    try:
        text = spend_file.read_text(encoding="utf-8")
    except OSError:
        return by_episode
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            by_episode.setdefault(entry.get("exercise_id", ""), []).append(entry)
    return by_episode


def _archive_dirs(archive_dir: Path) -> list:
    try:
        return [path for path in archive_dir.iterdir() if path.is_dir()]
    except OSError:
        return []


def _archive_files(archive_dir: Path) -> list:
    try:
        return [path for path in archive_dir.iterdir()
                if path.is_file() and path.suffix == ".jsonl"]
    except OSError:
        return []


_GEN_RE = re.compile(r"gen-(\d+)")


def _gen_seed(name: str) -> str | None:
    match = _GEN_RE.search(name or "")
    return match.group(1) if match else None


def _scoped_spend(bench_dir: Path, episodes: list, *,
                 spend_file: Path = SPEND_FILE,
                 archive_dir: Path = ARCHIVE_DIR) -> list:
    """Spend entries plausibly written by this bench's run.

    Reruns reuse episode ids, and the spend log still holds the
    archived 401 run under the same ids, so entries at or before
    each archived log's mtime are cut; the rest, up to this bench's
    log plus a referee tail, is this bench's run. Renamed single-file
    archives (the degraded gen-9015 log moved with a timeout suffix)
    cut only their own episode by scenario seed, never the whole bench.
    """
    by_episode = _read_spend(spend_file)
    archived = _archive_dirs(archive_dir)
    loose = _archive_files(archive_dir)
    scoped = []
    for episode in episodes:
        mine = _log_mtime(bench_dir / f"{episode}.jsonl")
        if mine is None:
            continue
        floor = float("-inf")
        for old in archived:
            seen = _log_mtime(old / f"{episode}.jsonl")
            if seen is not None and seen < mine:
                floor = max(floor, seen)
        want = _gen_seed(episode)
        for cand in loose:
            seen = _log_mtime(cand)
            if seen is None or seen >= mine:
                continue
            if cand.name == f"{episode}.jsonl":
                floor = max(floor, seen)
            elif want is not None and _gen_seed(cand.name) == want:
                floor = max(floor, seen)
        for entry in by_episode.get(episode, []):
            try:
                when = float(entry.get("ts", None))
            except (TypeError, ValueError):
                continue
            if when > floor and when <= mine + _SPEND_TAIL_S:
                scoped.append(entry)
    return scoped


def _briefed_baseten_tokens(bench_dir: Path, episodes: list, *,
                          spend_file: Path = SPEND_FILE,
                          archive_dir: Path = ARCHIVE_DIR) -> int:
    """Sum one bench's Baseten tokens, excluding archived reruns.

    Reruns reuse episode ids, and the spend log still holds the
    archived 401 run under the same ids, so entries at or before
    each archived log's mtime are cut; the rest, up to this bench's
    log plus a referee tail, is this bench's run.
    """
    total = 0
    for entry in _scoped_spend(bench_dir, episodes, spend_file=spend_file,
                               archive_dir=archive_dir):
        if entry.get("host") != "baseten":
            continue
        total += int(entry.get("input_tokens", 0) or 0)
        total += int(entry.get("output_tokens", 0) or 0)
    return total


def _cell_episodes(bench_dir: Path, cell: str) -> list:
    """Episode ids for one cell from the bench summary."""
    return [row.get("episode", "") for row in _csv_rows(bench_dir)
            if row.get("cell") == cell]


def build_cost_table(bench_dir: Path, episodes: list, *,
                     cell: str = BRIEFED_CELL,
                     spend_file: Path = SPEND_FILE,
                     archive_dir: Path = ARCHIVE_DIR) -> dict:
    """Per-exercise cost and token averages by role for one bench.

    Spend is scoped with the archive-aware window, so the archived
    invalid run under the same episode ids is not double counted.
    Baseten-hosted entries count as blue tokens (the Baseten price
    is unknown, so dollars are never shown for blue). Meta
    muse-spark-1.3 entries count as red and Meta muse-wonder-1.0 as
    auditor: the stage3 benches froze the playbook (referee never
    called) and never run debrief, so referee and debrief are zero
    by construction. Cached entries cost $0 in the spend log and
    count $0 here.
    """
    scoped = _scoped_spend(bench_dir, episodes, spend_file=spend_file,
                           archive_dir=archive_dir)
    red_usd = 0.0
    audit_usd = 0.0
    unattributed_usd = 0.0
    blue_tokens = 0
    for entry in scoped:
        if entry.get("host") == "baseten":
            blue_tokens += (int(entry.get("input_tokens", 0) or 0)
                            + int(entry.get("output_tokens", 0) or 0))
            continue
        try:
            cost = float(entry.get("cost_usd", 0.0) or 0.0)
        except (TypeError, ValueError):
            cost = 0.0
        model = entry.get("model", "")
        if model == AUDIT_MODEL:
            audit_usd += cost
        elif model == RED_MODEL:
            red_usd += cost
        else:
            unattributed_usd += cost
    count = len(episodes)
    red_usd = round(red_usd, 4)
    audit_usd = round(audit_usd, 4)
    unattributed_usd = round(unattributed_usd, 4)
    meta_usd = round(red_usd + audit_usd + unattributed_usd, 4)
    roles = {
        "red": {"meta_usd": red_usd, "baseten_tokens": 0,
                "meta_per_exercise": round(red_usd / count, 4) if count else 0.0,
                "tokens_per_exercise": 0},
        "blue": {"meta_usd": 0.0, "baseten_tokens": blue_tokens,
                 "meta_per_exercise": 0.0,
                 "tokens_per_exercise": round(blue_tokens / count) if count else 0},
        "auditor": {"meta_usd": audit_usd, "baseten_tokens": 0,
                    "meta_per_exercise": round(audit_usd / count, 4) if count else 0.0,
                    "tokens_per_exercise": 0},
        "referee": {"meta_usd": 0.0, "baseten_tokens": 0,
                    "meta_per_exercise": 0.0, "tokens_per_exercise": 0},
        "debrief": {"meta_usd": 0.0, "baseten_tokens": 0,
                    "meta_per_exercise": 0.0, "tokens_per_exercise": 0},
    }
    return {"bench": str(bench_dir), "cell": cell, "n": count,
            "roles": roles, "meta_usd": meta_usd,
            "meta_per_exercise": round(meta_usd / count, 4) if count else 0.0,
            "baseten_tokens": blue_tokens,
            "tokens_per_exercise": round(blue_tokens / count) if count else 0,
            "unattributed_usd": unattributed_usd}


def _usd(value: float) -> str:
    return f"${value:.2f}"


def render_cost_markdown(table: dict) -> str:
    """Render the cost page: one small table plus three sentences."""
    roles = table["roles"]
    blue_tok = roles["blue"]["tokens_per_exercise"]
    lines = ["# Exercise cost: briefed red vs open blue",
             "",
             "| role | Meta $/exercise | Baseten tokens/exercise |",
             "|------|-----------------|-------------------------|",
             f"| red | {_usd(roles['red']['meta_per_exercise'])} | n/a |",
             f"| blue | n/a | {blue_tok:,} |",
             f"| auditor | {_usd(roles['auditor']['meta_per_exercise'])} | n/a |",
             f"| referee | "
             f"{_usd(roles['referee']['meta_per_exercise'])} | n/a |",
             "| debrief | n/a | n/a |",
             f"| total | {_usd(table['meta_per_exercise'])} | "
             f"{table['tokens_per_exercise']:,} |",
             "",
             f"One end-to-end exercise, briefed model red against "
             f"open-weight blue with end-of-episode audit (n={table['n']}), "
             f"costs {_usd(table['meta_per_exercise'])} in Meta credits. "
             f"The open-weight blue uses {blue_tok:,} Baseten tokens per "
             f"exercise, billed separately at an unknown price, so the "
             f"table shows tokens only and no dollar figure for blue. "
             f"Audit averages "
             f"{_usd(roles['auditor']['meta_per_exercise'])} per exercise; "
             f"the referee never runs under the frozen playbook, debrief "
             f"is dashboard-only, and spend is attributed per episode "
             f"with the archive-aware timestamp window from the results "
             f"report so the archived invalid 401 run is not double "
             f"counted."]
    return "\n".join(lines) + "\n"


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


def _is_frontier_row(row: dict) -> bool:
    """True for frontier-tier rows; the classic stowaway has no difficulty."""
    return (row.get("scenario_difficulty") or "").strip() == "frontier"


def summarize_cell(bench_dir: Path, key: str, *,
                   include=None) -> dict:
    """Recompute one cell's metrics from its CSV rows and jsonls.

    include is an optional row predicate; rows it rejects are left
    out of every metric (used to keep the classic stowaway out of
    the frontier tier numbers).
    """
    rows = [row for row in _csv_rows(bench_dir)
            if row.get("cell") == key
            and (include is None or include(row))]
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
    parity = payload.get("parity")
    if parity is not None:
        shared_n = parity["same_networks"]["shared_n"]
        lines += ["",
                  "## Open-weight parity (briefed red, Open vs Spark blue)",
                  "",
                  "| blue | n | same nets | intrusions | contained | "
                  "secret reads | med delay | med relevant | med contain | "
                  "downtime | downtime cont | cost | red fb | blue fb |",
                  "|------|---|-----------|------------|-----------|"
                  "--------------|-----------|--------------|-------------|"
                  "----------|---------------|------|--------|---------|"]
        lines.append(_parity_row("Open", parity["open"], shared_n))
        lines.append(_parity_row("Spark", parity["spark"], shared_n))
        lines += ["",
                  "Same nets reads shared seeds over arm episodes; med "
                  "delay is the median first-report delay, med relevant "
                  "the median relevant delay; downtime is the mean over "
                  "all episodes, downtime cont the mean over contained "
                  "episodes; red/blue fb read fallbacks over turns.",
                  ""]
        lines += [f"- {sentence}" for sentence in parity["sentences"]]
    frontier = payload.get("frontier")
    if frontier is not None:
        lines += ["",
                  "## Frontier tier (briefed red, Open blue)",
                  ""]
        lines += [f"- {sentence}" for sentence in frontier["sentences"]]
    ablation = payload.get("sensors_ablation")
    if ablation is not None:
        shared_n = ablation["same_networks"]["shared_n"]
        lines += ["",
                  "## Sensors ablation with a model red",
                  "",
                  "| arm | n | same nets | intrusions | contained | "
                  "secret reads | med delay | med relevant | med contain | "
                  "downtime | downtime cont | cost | red fb | blue fb |",
                  "|------|---|-----------|------------|-----------|"
                  "--------------|-----------|--------------|-------------|"
                  "----------|---------------|------|--------|---------|"]
        lines.append(_parity_row("Sensors-on", ablation["on"], shared_n))
        lines.append(_parity_row("Sensors-off", ablation["off"], shared_n))
        lines += ["",
                  "Same nets reads shared seeds over arm episodes; med "
                  "delay is the median first-report delay, med relevant "
                  "the median relevant delay; downtime is the mean over "
                  "all episodes, downtime cont the mean over contained "
                  "episodes; red/blue fb read fallbacks over turns.",
                  ""]
        lines += [f"- {sentence}" for sentence in ablation["sentences"]]
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


def _scenario_seeds(bench_dir: Path, key: str) -> list:
    """Sorted scenario seeds for one cell, as strings."""
    seeds = []
    for row in _csv_rows(bench_dir):
        if row.get("cell") != key:
            continue
        seed = (row.get("scenario_seed") or "").strip()
        if seed:
            seeds.append(seed)
    return sorted(set(seeds), key=lambda value: (len(value), value))


def _lower_wins(open_val, spark_val, noun: str) -> str:
    """Neutral winner phrase for lower-is-better metrics."""
    if open_val is None and spark_val is None:
        return "neither reported"
    if open_val is None:
        return "Spark reported, Open did not"
    if spark_val is None:
        return "Open reported, Spark did not"
    if open_val == spark_val:
        return "tied"
    if open_val < spark_val:
        return f"Open {noun}"
    return f"Spark {noun}"


def _higher_wins(open_val, spark_val, noun: str) -> str:
    """Neutral winner phrase for higher-is-better metrics."""
    if open_val is None and spark_val is None:
        return "neither applies"
    if open_val is None:
        return "Spark applies, Open n/a"
    if spark_val is None:
        return "Open applies, Spark n/a"
    if open_val == spark_val:
        return "tied"
    if open_val > spark_val:
        return f"Open {noun}"
    return f"Spark {noun}"


def _labeled_lower_wins(a_val, b_val, label_a: str, label_b: str,
                       noun: str) -> str:
    """Neutral winner phrase for lower-is-better metrics, custom labels."""
    if a_val is None and b_val is None:
        return "neither reported"
    if a_val is None:
        return f"{label_b} reported, {label_a} did not"
    if b_val is None:
        return f"{label_a} reported, {label_b} did not"
    if a_val == b_val:
        return "tied"
    if a_val < b_val:
        return f"{label_a} {noun}"
    return f"{label_b} {noun}"


def _labeled_higher_wins(a_val, b_val, label_a: str, label_b: str,
                        noun: str) -> str:
    """Neutral winner phrase for higher-is-better metrics, custom labels."""
    if a_val is None and b_val is None:
        return "neither applies"
    if a_val is None:
        return f"{label_b} applies, {label_a} n/a"
    if b_val is None:
        return f"{label_a} applies, {label_b} n/a"
    if a_val == b_val:
        return "tied"
    if a_val > b_val:
        return f"{label_a} {noun}"
    return f"{label_b} {noun}"


def _rate_of(cell: dict, key: str):
    entry = cell.get(key, {}) or {}
    if not entry.get("n"):
        return None
    return entry.get("rate")


def build_parity(open_dir: Path, spark_dir: Path, open_cell: dict,
                 spark_cell: dict) -> dict:
    """Compare the two briefed-red runs, Open blue vs Spark blue.

    Returns shared seeds plus neutral per-metric sentences that name
    whichever arm does better. Makes no parity claim; callers add the
    disclaimer that pairing is approximate because briefed red
    re-rolled between benches.
    """
    open_seeds = _scenario_seeds(open_dir, BRIEFED_CELL)
    spark_seeds = _scenario_seeds(spark_dir, BRIEFED_CELL)
    shared = sorted(set(open_seeds) & set(spark_seeds),
                    key=lambda value: (len(value), value))
    return {"open_bench": str(open_dir), "spark_bench": str(spark_dir),
            "cell": BRIEFED_CELL,
            "same_networks": {"shared": shared, "shared_n": len(shared),
                              "open_n": len(open_seeds),
                              "spark_n": len(spark_seeds),
                              "open_only": sorted(set(open_seeds)
                                                  - set(spark_seeds)),
                              "spark_only": sorted(set(spark_seeds)
                                                   - set(open_seeds))},
            "open": open_cell, "spark": spark_cell,
            "sentences": _parity_sentences(open_cell, spark_cell, shared,
                                           len(open_seeds),
                                           len(spark_seeds))}


def _seed_span(shared: list) -> str:
    if not shared:
        return "none"
    if len(shared) <= 10:
        return ", ".join(shared)
    nums = sorted(int(seed) for seed in shared if seed.isdigit())
    if nums and nums == list(range(nums[0], nums[0] + len(nums))):
        return f"{nums[0]}-{nums[-1]} ({len(shared)} seeds)"
    return f"{len(shared)} seeds"


def _parity_sentences(open_cell: dict, spark_cell: dict, shared: list,
                      open_n: int, spark_n: int) -> list:
    """Neutral per-metric sentences; each names the better arm or ties."""
    intr_o = open_cell["intrusion"]
    intr_s = spark_cell["intrusion"]
    cont_o = open_cell["contained_after_intrusion"]
    cont_s = spark_cell["contained_after_intrusion"]
    sec_o = open_cell["secret_read"]
    sec_s = spark_cell["secret_read"]
    if open_n == spark_n:
        nets = (f"Same networks: {len(shared)} of {open_n} seeds overlap "
                f"({_seed_span(shared)}).")
    else:
        nets = (f"Same networks: {len(shared)} shared "
                f"({open_n} Open, {spark_n} Spark; {_seed_span(shared)}).")
    intr = (f"Intrusions: Open {intr_o['hits']}/{intr_o['n']}, Spark "
            f"{intr_s['hits']}/{intr_s['n']} "
            f"({_lower_wins(_rate_of(open_cell, 'intrusion'),
                            _rate_of(spark_cell, 'intrusion'), 'fewer')}).")
    cont = (f"Containment given intrusion: Open {cont_o['hits']}/"
            f"{cont_o['n']}, Spark {cont_s['hits']}/{cont_s['n']} "
            f"({_higher_wins(_rate_of(open_cell,
                                      'contained_after_intrusion'),
                             _rate_of(spark_cell,
                                      'contained_after_intrusion'),
                             'higher')}).")
    sec = (f"Secret reads: Open {sec_o['hits']}/{sec_o['n']}, Spark "
           f"{sec_s['hits']}/{sec_s['n']} "
           f"({_lower_wins(_rate_of(open_cell, 'secret_read'),
                           _rate_of(spark_cell, 'secret_read'),
                           'fewer')}).")
    delay = (f"Median relevant detection delay: Open "
             f"{_num(open_cell['median_relevant_delay'])}, Spark "
             f"{_num(spark_cell['median_relevant_delay'])} events "
             f"({_lower_wins(open_cell['median_relevant_delay'],
                             spark_cell['median_relevant_delay'],
                             'faster')}).")
    contain = (f"Median time to contain: Open "
               f"{_num(open_cell['median_time_to_contain'])}, Spark "
               f"{_num(spark_cell['median_time_to_contain'])} events "
               f"({_lower_wins(open_cell['median_time_to_contain'],
                               spark_cell['median_time_to_contain'],
                               'faster')}).")
    down = (f"Mean downtime: Open {_num(open_cell['mean_downtime_pct'])}%, "
            f"Spark {_num(spark_cell['mean_downtime_pct'])}% "
            f"({_lower_wins(open_cell['mean_downtime_pct'],
                            spark_cell['mean_downtime_pct'], 'lower')}; "
            f"contained means Open "
            f"{_num(open_cell['mean_downtime_contained_pct'])}%, Spark "
            f"{_num(spark_cell['mean_downtime_contained_pct'])}%).")
    cost = (f"Cost (Meta): Open ${open_cell['total_cost_usd']:.2f}, Spark "
            f"${spark_cell['total_cost_usd']:.2f} "
            f"({_lower_wins(open_cell['total_cost_usd'],
                            spark_cell['total_cost_usd'], 'lower')}).")
    census_o = open_cell.get("fallback_census") or {}
    census_s = spark_cell.get("fallback_census") or {}
    fall = (f"Baseline fallbacks: red Open {census_o.get('red_fallbacks', 0)}"
            f" of {census_o.get('red_turns', 0)}, Spark "
            f"{census_s.get('red_fallbacks', 0)} of "
            f"{census_s.get('red_turns', 0)}; blue Open "
            f"{census_o.get('blue_fallbacks', 0)} of "
            f"{census_o.get('blue_turns', 0)}, Spark "
            f"{census_s.get('blue_fallbacks', 0)} of "
            f"{census_s.get('blue_turns', 0)}.")
    note = (f"Descriptive only at n={open_cell['n']} Open and "
            f"n={spark_cell['n']} Spark; briefed red re-rolled between "
            f"benches, so pairing is approximate. This section compares "
            f"the two runs; it does not conclude parity.")
    return [nets, intr, cont, sec, delay, contain, down, cost, fall, note]


def _parity_row(label: str, cell: dict, shared_n: int) -> str:
    intr = cell["intrusion"]
    cont = cell["contained_after_intrusion"]
    sec = cell["secret_read"]
    census = cell.get("fallback_census") or {}
    return (
        f"| {label} | {cell['n']} | {shared_n}/{cell['n']} "
        f"| {intr['hits']}/{intr['n']} "
        f"| {cont['hits']}/{cont['n']} "
        f"| {sec['hits']}/{sec['n']} "
        f"| {_num(cell['median_detection_delay'])} "
        f"| {_num(cell['median_relevant_delay'])} "
        f"| {_num(cell['median_time_to_contain'])} "
        f"| {_num(cell['mean_downtime_pct'])}% "
        f"| {_num(cell['mean_downtime_contained_pct'])}% "
        f"| ${cell['total_cost_usd']:.2f} "
        f"| {census.get('red_fallbacks', 0)}/{census.get('red_turns', 0)} "
        f"| {census.get('blue_fallbacks', 0)}/"
        f"{census.get('blue_turns', 0)} |")


def build_parity_claims(parity: dict) -> list:
    """Short neutral claims for the parity runs (no parity conclusion)."""
    open_cell = parity["open"]
    spark_cell = parity["spark"]
    shared_n = parity["same_networks"]["shared_n"]
    intr_o = open_cell["intrusion"]
    intr_s = spark_cell["intrusion"]
    cont_o = open_cell["contained_after_intrusion"]
    cont_s = spark_cell["contained_after_intrusion"]
    sec_o = open_cell["secret_read"]
    sec_s = spark_cell["secret_read"]
    return [
        f"Briefed-red blue comparison on {shared_n} shared networks "
        f"(descriptive only): Open intrusions {intr_o['hits']}/"
        f"{intr_o['n']}, Spark {intr_s['hits']}/{intr_s['n']}; "
        f"containment Open {cont_o['hits']}/{cont_o['n']}, Spark "
        f"{cont_s['hits']}/{cont_s['n']}; secret reads Open "
        f"{sec_o['hits']}/{sec_o['n']}, Spark {sec_s['hits']}/"
        f"{sec_s['n']}.",
        f"Briefed-red delays and cost: median relevant delay Open "
        f"{_num(open_cell['median_relevant_delay'])} vs Spark "
        f"{_num(spark_cell['median_relevant_delay'])}, median time to "
        f"contain Open {_num(open_cell['median_time_to_contain'])} vs "
        f"Spark {_num(spark_cell['median_time_to_contain'])}, cost Open "
        f"${open_cell['total_cost_usd']:.2f} vs Spark "
        f"${spark_cell['total_cost_usd']:.2f}.",
    ]


PARITY_NON_CLAIM = (
    "The Open-vs-Spark blue comparison pairs across benches by scenario; "
    "briefed red re-rolled between benches, so pairing is approximate and "
    "descriptive only at n=20 per arm."
)


def _sensors_ablation_outcome_sentences(label: str, cell: dict,
                                       uncontained: list,
                                       secret_eps: list) -> list:
    """Plain containment and secret-read outcomes, naming episode ids."""
    cont = cell["contained_after_intrusion"]
    sec = cell["secret_read"]
    out = []
    if cont["n"] == 0:
        out.append(f"{label} had no intrusions, so containment does not "
                   f"apply.")
    elif uncontained:
        out.append(f"{label} blue failed to contain {len(uncontained)} of "
                   f"{cont['n']} intrusions: {', '.join(uncontained)}.")
    else:
        out.append(f"{label} blue contained all {cont['hits']} intrusions.")
    if sec["n"] == 0:
        out.append(f"{label} had no intrusions, so secret reads do not "
                   f"apply.")
    elif secret_eps:
        out.append(f"{label}: protected files were read in {len(secret_eps)} "
                   f"of {sec['n']} intrusions: {', '.join(secret_eps)}.")
    else:
        out.append(f"{label}: no protected file was read in {sec['n']} "
                   f"intrusions.")
    return out


SENSORS_OFF_RERUN_NOTE = (
    "Episode gen-9015 hit a model timeout, the degraded log was moved to "
    "runs/archive/sensors-off-gen-9015-degraded-timeout-20261101.jsonl, "
    "and the episode was re-run cleanly; token and cost scoping excludes "
    "the archived log."
)


def _sensors_ablation_sentences(on_cell: dict, off_cell: dict, shared: list,
                               on_n: int, off_n: int, *,
                               uncontained_on: list,
                               uncontained_off: list,
                               secret_on: list,
                               secret_off: list) -> list:
    """Neutral per-metric sentences; each names the better arm or ties."""
    intr_on = on_cell["intrusion"]
    intr_off = off_cell["intrusion"]
    cont_on = on_cell["contained_after_intrusion"]
    cont_off = off_cell["contained_after_intrusion"]
    sec_on = on_cell["secret_read"]
    sec_off = off_cell["secret_read"]
    if on_n == off_n:
        nets = (f"Same networks: {len(shared)} of {on_n} seeds overlap "
                f"({_seed_span(shared)}).")
    else:
        nets = (f"Same networks: {len(shared)} shared "
                f"({on_n} Sensors-on, {off_n} Sensors-off; "
                f"{_seed_span(shared)}).")
    intr = (f"Intrusions: Sensors-on {intr_on['hits']}/{intr_on['n']}, "
            f"Sensors-off {intr_off['hits']}/{intr_off['n']} "
            f"({_labeled_lower_wins(_rate_of(on_cell, 'intrusion'),
                                   _rate_of(off_cell, 'intrusion'),
                                   'Sensors-on', 'Sensors-off', 'fewer')}).")
    cont = (f"Containment given intrusion: Sensors-on {cont_on['hits']}/"
            f"{cont_on['n']}, Sensors-off {cont_off['hits']}/"
            f"{cont_off['n']} "
            f"({_labeled_higher_wins(_rate_of(on_cell,
                                             'contained_after_intrusion'),
                                    _rate_of(off_cell,
                                             'contained_after_intrusion'),
                                    'Sensors-on', 'Sensors-off',
                                    'higher')}).")
    sec = (f"Secret reads given intrusion: Sensors-on {sec_on['hits']}/"
           f"{sec_on['n']}, Sensors-off {sec_off['hits']}/{sec_off['n']} "
           f"({_labeled_lower_wins(_rate_of(on_cell, 'secret_read'),
                                   _rate_of(off_cell, 'secret_read'),
                                   'Sensors-on', 'Sensors-off', 'fewer')}).")
    delay = (f"Median relevant detection delay: Sensors-on "
             f"{_num(on_cell['median_relevant_delay'])}, Sensors-off "
             f"{_num(off_cell['median_relevant_delay'])} events "
             f"({_labeled_lower_wins(on_cell['median_relevant_delay'],
                                     off_cell['median_relevant_delay'],
                                     'Sensors-on', 'Sensors-off',
                                     'faster')}).")
    contain = (f"Median time to contain: Sensors-on "
               f"{_num(on_cell['median_time_to_contain'])}, Sensors-off "
               f"{_num(off_cell['median_time_to_contain'])} events "
               f"({_labeled_lower_wins(on_cell['median_time_to_contain'],
                                       off_cell['median_time_to_contain'],
                                       'Sensors-on', 'Sensors-off',
                                       'faster')}).")
    down = (f"Mean downtime: Sensors-on "
            f"{_num(on_cell['mean_downtime_pct'])}%, Sensors-off "
            f"{_num(off_cell['mean_downtime_pct'])}% "
            f"({_labeled_lower_wins(on_cell['mean_downtime_pct'],
                                    off_cell['mean_downtime_pct'],
                                    'Sensors-on', 'Sensors-off', 'lower')}; "
            f"contained means Sensors-on "
            f"{_num(on_cell['mean_downtime_contained_pct'])}%, Sensors-off "
            f"{_num(off_cell['mean_downtime_contained_pct'])}%).")
    cost = (f"Cost (Meta): Sensors-on ${on_cell['total_cost_usd']:.2f}, "
            f"Sensors-off ${off_cell['total_cost_usd']:.2f} "
            f"({_labeled_lower_wins(on_cell['total_cost_usd'],
                                    off_cell['total_cost_usd'],
                                    'Sensors-on', 'Sensors-off', 'lower')}).")
    census_on = on_cell.get("fallback_census") or {}
    census_off = off_cell.get("fallback_census") or {}
    fall = (f"Baseline fallbacks: red Sensors-on "
            f"{census_on.get('red_fallbacks', 0)}"
            f" of {census_on.get('red_turns', 0)}, Sensors-off "
            f"{census_off.get('red_fallbacks', 0)} of "
            f"{census_off.get('red_turns', 0)}; blue Sensors-on "
            f"{census_on.get('blue_fallbacks', 0)} of "
            f"{census_on.get('blue_turns', 0)}, Sensors-off "
            f"{census_off.get('blue_fallbacks', 0)} of "
            f"{census_off.get('blue_turns', 0)}.")
    sentences = [nets, intr, cont, sec, delay, contain, down, cost, fall]
    sentences += _sensors_ablation_outcome_sentences(
        "Sensors-on", on_cell, uncontained_on, secret_on)
    sentences += _sensors_ablation_outcome_sentences(
        "Sensors-off", off_cell, uncontained_off, secret_off)
    if "9015" in (shared or []):
        sentences.append(SENSORS_OFF_RERUN_NOTE)
    note = (f"Descriptive only at n={on_cell['n']} Sensors-on and "
            f"n={off_cell['n']} Sensors-off; briefed red re-rolled between "
            f"benches, so pairing is approximate. This section compares "
            f"the two runs; it does not conclude that sensors caused the "
            f"difference.")
    sentences.append(note)
    return sentences


def build_sensors_ablation(on_dir: Path, off_dir: Path, on_cell: dict,
                           off_cell: dict) -> dict:
    """Compare the two briefed-red runs, sensors-on vs sensors-off.

    Returns shared seeds plus neutral per-metric sentences that name
    whichever arm does better. Makes no causal claim about sensors;
    callers add the disclaimer that pairing is approximate because
    briefed red re-rolled between benches.
    """
    on_seeds = _scenario_seeds(on_dir, BRIEFED_CELL)
    off_seeds = _scenario_seeds(off_dir, BRIEFED_SENSORS_OFF_CELL)
    shared = sorted(set(on_seeds) & set(off_seeds),
                    key=lambda value: (len(value), value))
    on_rows = [row for row in _csv_rows(on_dir)
               if row.get("cell") == BRIEFED_CELL]
    off_rows = [row for row in _csv_rows(off_dir)
                if row.get("cell") == BRIEFED_SENSORS_OFF_CELL]
    uncontained_on = sorted(row.get("episode", "") for row in on_rows
                            if row.get("intrusion") == "True"
                            and row.get("contained_after_intrusion")
                            != "True")
    uncontained_off = sorted(row.get("episode", "") for row in off_rows
                             if row.get("intrusion") == "True"
                             and row.get("contained_after_intrusion")
                             != "True")
    secret_on = sorted(row.get("episode", "") for row in on_rows
                       if row.get("secret_read") == "True")
    secret_off = sorted(row.get("episode", "") for row in off_rows
                        if row.get("secret_read") == "True")
    return {"on_bench": str(on_dir), "off_bench": str(off_dir),
            "cell_on": BRIEFED_CELL, "cell_off": BRIEFED_SENSORS_OFF_CELL,
            "same_networks": {"shared": shared, "shared_n": len(shared),
                              "on_n": len(on_seeds),
                              "off_n": len(off_seeds),
                              "on_only": sorted(set(on_seeds)
                                                - set(off_seeds)),
                              "off_only": sorted(set(off_seeds)
                                                 - set(on_seeds))},
            "on": on_cell, "off": off_cell,
            "uncontained_episodes": {"on": uncontained_on,
                                     "off": uncontained_off},
            "secret_read_episodes": {"on": secret_on,
                                     "off": secret_off},
            "sentences": _sensors_ablation_sentences(
                on_cell, off_cell, shared, len(on_seeds), len(off_seeds),
                uncontained_on=uncontained_on,
                uncontained_off=uncontained_off,
                secret_on=secret_on, secret_off=secret_off)}


def build_sensors_ablation_claims(ablation: dict) -> list:
    """Short neutral claims for the sensors ablation (no causal conclusion)."""
    on_cell = ablation["on"]
    off_cell = ablation["off"]
    shared_n = ablation["same_networks"]["shared_n"]
    intr_on = on_cell["intrusion"]
    intr_off = off_cell["intrusion"]
    cont_on = on_cell["contained_after_intrusion"]
    cont_off = off_cell["contained_after_intrusion"]
    sec_on = on_cell["secret_read"]
    sec_off = off_cell["secret_read"]
    claims = [
        f"Sensors ablation with briefed red on {shared_n} shared networks "
        f"(descriptive only): Sensors-on intrusions {intr_on['hits']}/"
        f"{intr_on['n']}, Sensors-off {intr_off['hits']}/{intr_off['n']}; "
        f"containment Sensors-on {cont_on['hits']}/{cont_on['n']}, "
        f"Sensors-off {cont_off['hits']}/{cont_off['n']}; secret reads "
        f"Sensors-on {sec_on['hits']}/{sec_on['n']}, Sensors-off "
        f"{sec_off['hits']}/{sec_off['n']}.",
        f"Sensors ablation delays and cost: median relevant delay "
        f"Sensors-on {_num(on_cell['median_relevant_delay'])} vs "
        f"Sensors-off {_num(off_cell['median_relevant_delay'])}, median "
        f"time to contain Sensors-on "
        f"{_num(on_cell['median_time_to_contain'])} vs Sensors-off "
        f"{_num(off_cell['median_time_to_contain'])}, cost Sensors-on "
        f"${on_cell['total_cost_usd']:.2f} vs Sensors-off "
        f"${off_cell['total_cost_usd']:.2f}.",
    ]
    uncontained = ablation.get("uncontained_episodes") or {}
    secret_eps = ablation.get("secret_read_episodes") or {}
    for label, key in (("Sensors-on", "on"), ("Sensors-off", "off")):
        missing = uncontained.get(key) or []
        if missing:
            cont = cont_on if key == "on" else cont_off
            claims.append(
                f"{label} blue failed to contain {len(missing)} of "
                f"{cont['n']} intrusions: {', '.join(missing)}.")
        leaked = secret_eps.get(key) or []
        if leaked:
            sec = sec_on if key == "on" else sec_off
            claims.append(
                f"{label}: protected files were read in {len(leaked)} of "
                f"{sec['n']} intrusions: {', '.join(leaked)}.")
    return claims


SENSORS_ABLATION_NON_CLAIM = (
    "The sensors-on vs sensors-off comparison with briefed red pairs "
    "across benches by scenario; briefed red re-rolled between benches, "
    "so pairing is approximate and descriptive only. It does not conclude "
    "that sensors caused any difference."
)


def _fallback_sentence(cell: dict) -> str:
    """Baseline-fallback counts with turns, percent, and episodes."""
    census = cell.get("fallback_census") or {}
    red_fell = census.get("red_fallbacks", 0)
    red_turns = census.get("red_turns", 0)
    blue_fell = census.get("blue_fallbacks", 0)
    blue_turns = census.get("blue_turns", 0)
    red_pct = f"{100.0 * red_fell / red_turns:.1f}%" if red_turns else "n/a"
    blue_pct = f"{100.0 * blue_fell / blue_turns:.1f}%" if blue_turns else "n/a"
    return (f"Baseline fallbacks: {red_fell} of {red_turns} red turns "
            f"({red_pct}) and {blue_fell} of {blue_turns} blue turns "
            f"({blue_pct}) used a safe baseline move after model replies "
            f"with no usable tool call, across {census.get('episodes', 0)} "
            f"of {census.get('n', cell['n'])} episodes.")


def _excluded_sentence(excluded: list) -> str:
    """Labeled line for bench episodes left out of the frontier table."""
    parts = []
    for row in excluded:
        name = row.get("episode", "?")
        if row.get("intrusion") == "True":
            held = ("contained" if row.get("contained_after_intrusion")
                    == "True" else "not contained")
            parts.append(f"{name} (intrusion, {held})")
        else:
            parts.append(f"{name} (no intrusion)")
    noun = "episode" if len(parts) == 1 else "episodes"
    joined = "; ".join(parts)
    return (f"Excluded from the frontier table: {len(parts)} classic-network "
            f"{noun} the bench ran by accident: {joined}.")


def build_frontier_sentences(cell: dict, *, excluded: list | None = None,
                             uncontained_episodes: list | None = None) -> list:
    """Neutral outcome sentences for the frontier tier run.

    States intrusions, containment, and secret reads with
    denominators, naming uncontained intrusions outright when any
    happened, plus delays, downtime, cost, Baseten tokens, and
    baseline-fallback counts. Excluded non-frontier episodes get
    their own labeled line, never folded into the main numbers.
    Makes no comparison against the held-out cells.
    """
    intr = cell["intrusion"]
    cont = cell["contained_after_intrusion"]
    sec = cell["secret_read"]
    uncontained = intr["hits"] - cont["hits"]
    outcome = (f"Frontier tier on {cell['n']} frontier networks "
               f"(descriptive only): intrusions {intr['hits']}/"
               f"{intr['n']}, contained {cont['hits']}/{cont['n']}")
    if uncontained:
        names = ", ".join(uncontained_episodes or [])
        outcome += f" ({uncontained} not contained" + (
            f": {names}" if names else "") + ")"
    outcome += f", secret reads {sec['hits']}/{sec['n']}."
    timing = (f"Median relevant detection delay "
              f"{_num(cell['median_relevant_delay'])} events, median time "
              f"to contain {_num(cell['median_time_to_contain'])} events, "
              f"mean downtime {_num(cell['mean_downtime_contained_pct'])}% "
              f"over contained episodes, cost "
              f"${cell['total_cost_usd']:.2f}, Baseten tokens "
              f"{cell.get('baseten_tokens', 0):,}.")
    sentences = [outcome, timing, _fallback_sentence(cell)]
    if excluded:
        sentences.append(_excluded_sentence(excluded))
    return sentences


FRONTIER_NON_CLAIM = (
    "The frontier tier run is descriptive only; it makes no claim "
    "that the tier is harder than other tiers and no claim that blue "
    "always holds."
)


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
    parser.add_argument("--briefed-spark",
                        default="runs/bench-stage3-briefed-heldout-blue-spark",
                        help="Spark-blue repeat of the briefed held-out bench; "
                        "the parity section is skipped quietly when missing")
    parser.add_argument("--briefed-sensors-off",
                        default="runs/bench-stage3-briefed-heldout-sensors-off",
                        help="Sensors-off repeat of the briefed held-out "
                        "bench; the sensors ablation section is skipped "
                        "quietly when missing")
    parser.add_argument("--frontier",
                        default="runs/bench-stage3-frontier",
                        help="Frontier-tier bench; the frontier section is "
                        "skipped quietly when missing")
    parser.add_argument("--frozen",
                        default="runs/stage3/frozen-playbook.json")
    parser.add_argument("--out-md", default="docs/STAGE3_RESULTS.md")
    parser.add_argument("--out-json", default="dashboard/results.json")
    parser.add_argument("--out-cost", default=None,
                        help="Write the per-role cost table here "
                        "(default: skipped in full runs, docs/COST.md "
                        "with --cost-only)")
    parser.add_argument("--spend-file", default=str(SPEND_FILE),
                        help="Spend log for cost attribution")
    parser.add_argument("--archive-dir", default=str(ARCHIVE_DIR),
                        help="Archived benches whose reruns share "
                        "episode ids")
    parser.add_argument("--cost-only", action="store_true",
                        help="Write only the cost table, skipping "
                        "the results report")
    return parser.parse_args(argv)


def _cost_only(args) -> int:
    briefed = Path(args.briefed)
    if not (briefed / "summary.csv").exists():
        print(f"report: no finished briefed bench at {briefed}",
              file=sys.stderr)
        return 1
    episodes = _cell_episodes(briefed, BRIEFED_CELL)
    if not episodes:
        print(f"report: no {BRIEFED_CELL} episodes at {briefed}",
              file=sys.stderr)
        return 1
    table = build_cost_table(
        briefed, episodes, spend_file=Path(args.spend_file),
        archive_dir=Path(args.archive_dir))
    out_cost = Path(args.out_cost) if args.out_cost else Path("docs/COST.md")
    out_cost.parent.mkdir(parents=True, exist_ok=True)
    out_cost.write_text(render_cost_markdown(table), encoding="utf-8")
    print(f"wrote {out_cost}")
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.cost_only:
        return _cost_only(args)
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
                briefed, episodes, spend_file=Path(args.spend_file),
                archive_dir=Path(args.archive_dir))
            cell["fallback_census"] = _fallback_census(briefed, episodes)
            reference[BRIEFED_LABEL] = cell
    briefed_spark = Path(args.briefed_spark)
    if (briefed_spark / "summary.csv").exists():
        cell = summarize_cell(briefed_spark, BRIEFED_CELL)
        if cell["n"]:
            episodes = [row.get("episode", "") for row in
                        _csv_rows(briefed_spark)
                        if row.get("cell") == BRIEFED_CELL]
            cell["baseten_tokens"] = _briefed_baseten_tokens(
                briefed_spark, episodes, spend_file=Path(args.spend_file),
                archive_dir=Path(args.archive_dir))
            cell["fallback_census"] = _fallback_census(
                briefed_spark, episodes)
            reference[BRIEFED_SPARK_LABEL] = cell
    briefed_sensors_off = Path(args.briefed_sensors_off)
    off_cell = None
    if (briefed_sensors_off / "summary.csv").exists():
        candidate = summarize_cell(
            briefed_sensors_off, BRIEFED_SENSORS_OFF_CELL)
        if candidate["n"]:
            off_episodes = [row.get("episode", "") for row in
                            _csv_rows(briefed_sensors_off)
                            if row.get("cell")
                            == BRIEFED_SENSORS_OFF_CELL]
            candidate["baseten_tokens"] = _briefed_baseten_tokens(
                briefed_sensors_off, off_episodes,
                spend_file=Path(args.spend_file),
                archive_dir=Path(args.archive_dir))
            candidate["fallback_census"] = _fallback_census(
                briefed_sensors_off, off_episodes)
            off_cell = candidate
    # The frontier bench reuses the briefed cell key (same red,
    # sensors, and memory); the bench dir and frontier seeds tell
    # it apart from the held-out run.
    frontier_dir = Path(args.frontier)
    frontier = None
    if (frontier_dir / "summary.csv").exists():
        frontier_cell = summarize_cell(frontier_dir, BRIEFED_CELL,
                                       include=_is_frontier_row)
        if frontier_cell["n"]:
            bench_rows = [row for row in _csv_rows(frontier_dir)
                          if row.get("cell") == BRIEFED_CELL]
            frontier_rows = [row for row in bench_rows
                             if _is_frontier_row(row)]
            excluded_rows = [row for row in bench_rows
                             if not _is_frontier_row(row)]
            frontier_episodes = [row.get("episode", "")
                                 for row in frontier_rows]
            frontier_cell["baseten_tokens"] = _briefed_baseten_tokens(
                frontier_dir, frontier_episodes,
                spend_file=Path(args.spend_file),
                archive_dir=Path(args.archive_dir))
            frontier_cell["fallback_census"] = _fallback_census(
                frontier_dir, frontier_episodes)
            uncontained = [row.get("episode", "") for row in frontier_rows
                           if row.get("intrusion") == "True"
                           and row.get("contained_after_intrusion") != "True"]
            frontier = {
                "bench": str(frontier_dir), "cell": frontier_cell,
                "excluded": [row.get("episode", "") for row in excluded_rows],
                "sentences": build_frontier_sentences(
                    frontier_cell, excluded=excluded_rows,
                    uncontained_episodes=uncontained)}
    benches = {"heldout": str(heldout), "sensors": str(sensors),
               "noisyref": str(noisyref), "xcheck": str(xcheck),
               "briefed": str(briefed)}
    parity = None
    open_cell = reference.get(BRIEFED_LABEL)
    spark_cell = reference.get(BRIEFED_SPARK_LABEL)
    if open_cell is not None and spark_cell is not None:
        parity = build_parity(briefed, briefed_spark, open_cell, spark_cell)
        benches["briefed_spark"] = str(briefed_spark)
    if frontier is not None:
        benches["frontier"] = str(frontier_dir)
    ablation = None
    on_cell = reference.get(BRIEFED_LABEL)
    if on_cell is not None and off_cell is not None:
        ablation = build_sensors_ablation(
            briefed, briefed_sensors_off, on_cell, off_cell)
        benches["briefed_sensors_off"] = str(briefed_sensors_off)
    claims = build_claims(comparisons, cells, reference, void)
    non_claims = list(NON_CLAIMS)
    if parity is not None:
        claims += build_parity_claims(parity)
        non_claims.append(PARITY_NON_CLAIM)
    if frontier is not None:
        claims += frontier["sentences"]
        non_claims.append(FRONTIER_NON_CLAIM)
    if ablation is not None:
        claims += build_sensors_ablation_claims(ablation)
        non_claims.append(SENSORS_ABLATION_NON_CLAIM)
    claims += [REPLAY_NOTE]
    payload = {
        "stage": 3,
        "generated": datetime.now(timezone.utc).isoformat(),
        "frozen_sha256": hashlib.sha256(
            frozen.read_bytes()).hexdigest(),
        "benches": benches,
        "cells": cells,
        "comparisons": comparisons,
        "void": void,
        "reference": reference,
        "claims": claims,
        "non_claims": non_claims,
    }
    if parity is not None:
        payload["parity"] = parity
    if frontier is not None:
        payload["frontier"] = frontier
    if ablation is not None:
        payload["sensors_ablation"] = ablation
    md_path = Path(args.out_md)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    json_path = Path(args.out_json)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2) + "\n",
                         encoding="utf-8")
    if args.out_cost and (briefed / "summary.csv").exists():
        cost_episodes = _cell_episodes(briefed, BRIEFED_CELL)
        if cost_episodes:
            cost_table = build_cost_table(
                briefed, cost_episodes,
                spend_file=Path(args.spend_file),
                archive_dir=Path(args.archive_dir))
            cost_path = Path(args.out_cost)
            cost_path.parent.mkdir(parents=True, exist_ok=True)
            cost_path.write_text(render_cost_markdown(cost_table),
                                 encoding="utf-8")
            print(f"wrote {md_path}, {json_path}, and {cost_path}")
            return 0
    print(f"wrote {md_path} and {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
