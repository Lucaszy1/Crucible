#!/usr/bin/env python3
"""Suspicion battery for the Stage 3 benchmark (no model calls).

Checks that a finished run is evidence, not artifact:

- splits: train bench used only train seeds, eval benches only held-out
- freeze: held-out ran frozen and every memory-on cell still matches the
  frozen playbook byte for byte (rules)
- leak-grep: the frozen playbook contains no held-out scenario content
- episodes: per-episode artifact scan (zero-cost model episodes, cached
  replays under --no-cache scoped per bench by episode log times,
  degraded or dirty episodes, audit gaps, sensors-on benches with
  zero alerts across all episodes; the two void benches are marked
  void by name instead of failing on silence, and duplicate exercise
  ids fail only within one bench)
- briefed: the 20-network briefed held-out bench covers seeds
  9001-9020 exactly once, ran sensors-on memory-off frozen with the
  auditor on, and reports soft baseline fallbacks as a warning with
  red/blue counts (only the degraded flag fails)

Exit 0 when every FAIL check passes (warnings allowed); exit 1 on any
FAIL, or on warnings with --strict. Reads runs/, spend.jsonl, and the
frozen playbook only; writes nothing.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from crucible.agents.auditor import SKIP_TOOLS  # noqa: E402
from crucible.scenarios import generate  # noqa: E402
from crucible.scenarios.seeds import HELDOUT_SEEDS, TRAIN_SEEDS  # noqa: E402

STAGE3_DIR = ROOT / "runs" / "stage3"
FROZEN_JSON = STAGE3_DIR / "frozen-playbook.json"
FROZEN_SHA = STAGE3_DIR / "frozen-playbook.sha256"
SPEND_FILE = ROOT / "runs" / "spend.jsonl"
DIFFICULTIES = ("easy", "medium", "hard")

# Benches whose sensors-on silence is the documented collector bug,
# not evidence: sensors read the classic host list on generated nets,
# so every lookup missed (fixed in c9dd960; rerun as
# bench-stage3-heldout-sensors). Zero-alert findings on these two are
# marked void by name instead of failing; every other check still
# applies to them, and silence still fails everywhere else.
VOID_ZERO_ALERT_BENCHES = frozenset({
    "bench-stage3-train-solver",
    "bench-stage3-heldout",
})

# Spend entries for one episode can land slightly after its jsonl
# closes (the trailing referee review runs after the loop), but never
# before the episode starts. 300 seconds clears any referee call (one
# model call with bounded retries; no entry in the log lands later
# than that past its newest same-id log) while staying far under the
# hours between same-id episodes across benches (79 minutes minimum
# on grouped ids), so reruns stop flagging each other.
_SPEND_TAIL_S = 300

FAIL = "FAIL"
WARN = "WARN"
OK = "OK"


class Report:
    """Collect (level, text) findings and render them at the end."""

    def __init__(self) -> None:
        self.findings: list = []

    def add(self, level: str, text: str) -> None:
        self.findings.append((level, text))

    def fails(self) -> list:
        return [text for level, text in self.findings if level == FAIL]

    def warns(self) -> list:
        return [text for level, text in self.findings if level == WARN]

    def show(self) -> None:
        for level, text in self.findings:
            print(f"[{level}] {text}")
        print(f"checks: {len(self.fails())} fail, {len(self.warns())} warn")


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _bench_summary(bench_dir: Path) -> dict:
    path = bench_dir / "summary.json"
    if not path.exists():
        raise FileNotFoundError(f"no summary.json in {bench_dir}")
    return _read_json(path)


def _csv_rows(bench_dir: Path) -> list:
    path = bench_dir / "summary.csv"
    if not path.exists():
        raise FileNotFoundError(f"no summary.csv in {bench_dir}")
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _jsonl_events(path: Path) -> list:
    return [json.loads(line) for line in path.read_text(encoding="utf-8")
            .splitlines() if line.strip()]


def _frozen_rules() -> list:
    return _read_json(FROZEN_JSON).get("rules", []) or []


def check_splits(report: Report, train: Path, evals: list) -> None:
    """Train touched only train seeds; eval benches only held-out."""
    train_seeds = set()
    try:
        rows = _csv_rows(train)
    except FileNotFoundError as exc:
        report.add(FAIL, str(exc))
        return
    for row in rows:
        raw = (row.get("scenario_seed") or "").strip()
        if raw:
            train_seeds.add(int(raw))
    bad = [seed for seed in sorted(train_seeds)
           if seed not in set(TRAIN_SEEDS)]
    if bad:
        report.add(FAIL, f"train bench used non-train seeds: {bad}")
    else:
        report.add(OK, f"train seeds all in 1001-1040 ({len(train_seeds)})")
    for bench_dir in evals:
        try:
            rows = _csv_rows(bench_dir)
        except FileNotFoundError as exc:
            report.add(FAIL, str(exc))
            continue
        seeds = set()
        for row in rows:
            raw = (row.get("scenario_seed") or "").strip()
            if raw:
                seeds.add(int(raw))
        bad = [seed for seed in sorted(seeds)
               if seed not in set(HELDOUT_SEEDS)]
        leaked = sorted(seeds & train_seeds)
        if bad:
            report.add(FAIL, f"{bench_dir.name} used non-heldout seeds: {bad}")
        elif leaked:
            report.add(FAIL, f"{bench_dir.name} reused train seeds: {leaked}")
        else:
            report.add(OK, f"{bench_dir.name} seeds all in 9001-9020 "
                           f"({len(seeds)})")


def check_freeze(report: Report, bench_dir: Path) -> None:
    """Held-out ran frozen and cell books still match the frozen file."""
    try:
        summary = _bench_summary(bench_dir)
    except FileNotFoundError as exc:
        report.add(FAIL, str(exc))
        return
    if not summary.get("playbook_frozen"):
        report.add(FAIL, f"{bench_dir.name}: playbook_frozen is not true")
        return
    report.add(OK, f"{bench_dir.name}: playbook_frozen=true")
    if not FROZEN_JSON.exists():
        report.add(FAIL, f"frozen playbook missing: {FROZEN_JSON}")
        return
    want = [entry.get("rule", "") for entry in _frozen_rules()]
    if FROZEN_SHA.exists():
        pinned = FROZEN_SHA.read_text(encoding="utf-8").split()[0]
        actual = hashlib.sha256(
            FROZEN_JSON.read_bytes()).hexdigest()
        if pinned != actual:
            report.add(FAIL, "frozen playbook changed since pinning: "
                             f"sha {actual[:12]} != pinned {pinned[:12]}")
            return
        report.add(OK, f"frozen sha matches pin ({pinned[:12]})")
    else:
        report.add(WARN, "no frozen sha pin file; skipping pin check")
    checked = 0
    for cell in summary.get("cells", []) or []:
        if not cell.endswith("-memory-on"):
            continue
        path = bench_dir / "playbooks" / f"{cell}.json"
        if not path.exists():
            report.add(FAIL, f"{bench_dir.name}: missing cell book {cell}")
            continue
        got = [entry.get("rule", "")
               for entry in _read_json(path).get("rules", []) or []]
        checked += 1
        if got != want:
            report.add(FAIL, f"{bench_dir.name}: {cell} drifted from frozen "
                             f"({len(got)} rules vs {len(want)})")
    if checked:
        report.add(OK, f"{bench_dir.name}: {checked} memory-on cell books "
                       "match frozen")


def _heldout_corpus() -> dict:
    """Held-out-identifying strings: hostnames, toy passwords, jewels.

    Shared pools (weak passwords, generic page paths, noise users) are
    deliberately excluded: they repeat across splits, so quoting one
    cannot leak held-out identity.
    """
    hosts: set = set()
    toys: set = set()
    jewels: set = set()
    configs: set = set()
    ids: set = set()
    for seed in HELDOUT_SEEDS:
        for difficulty in DIFFICULTIES:
            spec = generate.generate(seed, difficulty)
            ids.add(spec["scenario_id"])
            for host in spec.get("hosts", []) or []:
                hosts.add(host["name"])
            for group in ("path", "dead_ends"):
                for step in spec.get(group, []) or []:
                    password = step.get("password", "")
                    reveal = step.get("reveals_password", "")
                    for value in (password, reveal):
                        if value.startswith("toy-"):
                            toys.add(value)
                    path = step.get("path", "")
                    if path.startswith("/srv/"):
                        configs.add(path)
            jewel = spec.get("jewel", {}).get("path", "")
            if jewel:
                jewels.add(jewel)
                jewels.add(jewel.rsplit("/", 1)[-1])
    return {"hostnames": hosts, "toy_passwords": toys, "jewel_paths": jewels,
            "config_paths": configs, "scenario_ids": ids}


def check_leak(report: Report, playbook_path: Path) -> None:
    """The frozen playbook must quote no held-out-identifying string."""
    if not playbook_path.exists():
        report.add(FAIL, f"playbook missing: {playbook_path}")
        return
    rules = _read_json(playbook_path).get("rules", []) or []
    texts = [entry.get("rule", "") for entry in rules]
    blob = "\n".join(texts)
    lowered = blob.lower()
    corpus = _heldout_corpus()
    hits = []
    for host in sorted(corpus["hostnames"]):
        if re.search(r"\b" + re.escape(host.lower()) + r"\b", lowered):
            hits.append(f"hostname {host!r}")
    for group in ("toy_passwords", "jewel_paths", "config_paths",
                  "scenario_ids"):
        for value in sorted(corpus[group]):
            if value.lower() in lowered:
                hits.append(f"{group} {value!r}")
    if hits:
        report.add(FAIL, f"held-out content in {playbook_path.name}: "
                         + "; ".join(hits[:8]))
    else:
        report.add(OK, f"no held-out content in {playbook_path.name} "
                       f"({len(texts)} rules vs "
                       f"{sum(len(v) for v in corpus.values())} strings)")


def _spend_index() -> dict:
    """Map exercise id to its spend entries (cached flags matter)."""
    index: dict = {}
    if not SPEND_FILE.exists():
        return index
    for line in SPEND_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        index.setdefault(entry.get("exercise_id"), []).append(entry)
    return index


def _episode_mtime(bench_dir: Path, episode: str) -> float | None:
    try:
        return (bench_dir / f"{episode}.jsonl").stat().st_mtime
    except OSError:
        return None


def _bench_entries(bench_dir: Path, episode: str, entries: list) -> list:
    """Keep the spend entries plausibly written by this bench's run.

    Reruns reuse exercise ids across benches, so a global lookup flags
    one bench for another's cache hits. An entry belongs to this bench
    when it predates this bench's episode log (plus a tail for the
    trailing referee review) and postdates every older same-id log on
    disk. Episodes without a log, and entries without a timestamp,
    keep everything: missing logs fail elsewhere, and scoping must
    never hide a real same-bench hit.
    """
    mine = _episode_mtime(bench_dir, episode)
    if mine is None:
        return list(entries)
    try:
        siblings = [path for path in bench_dir.parent.iterdir()
                    if path.is_dir() and path != bench_dir]
    except OSError:
        siblings = []
    floor = float("-inf")
    for sibling in siblings:
        other = _episode_mtime(sibling, episode)
        if other is not None and other < mine:
            floor = max(floor, other + _SPEND_TAIL_S)
    kept = []
    for entry in entries:
        raw = entry.get("ts", None)
        try:
            when = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            when = None
        if when is None \
                or (when <= mine + _SPEND_TAIL_S and when > floor):
            kept.append(entry)
    return kept


def _spec_hosts(seed: int, difficulty: str) -> set:
    spec = generate.generate(seed, difficulty)
    return {host["name"] for host in spec.get("hosts", []) or []}


def _owed_alerts(events: list) -> bool:
    """Check whether a sensors-on episode owed the log an alert.

    A successful sign-in always leaves a gated signin-ok line behind
    (no-gate attempts never authenticate), so the first blue turn after
    it must attach at least one alert when collection works. Episodes
    with no intrusion, or with none followed by a blue turn, owe
    nothing and stay silent without blame.
    """
    signin_at = None
    for index, event in enumerate(events or []):
        if event.get("actor") != "red" \
                or event.get("tool") != "try_credential":
            continue
        if (event.get("result", {}) or {}).get("authenticated") is True:
            signin_at = index
            break
    if signin_at is None:
        return False
    return any(event.get("actor") == "blue"
               for event in events[signin_at + 1:])


def check_episodes(report: Report, bench_dirs: list) -> None:
    """Scan every episode for replay, cost, hygiene, and audit gaps.

    Duplicate exercise ids fail only within one bench: reruns and
    the cross-check intentionally reuse episode ids across benches
    (same scenario, cell, and rep), so cross-bench reuse is not a
    finding. Within-bench duplicates still fail everywhere.
    """
    spend = _spend_index()
    for bench_dir in bench_dirs:
        try:
            summary = _bench_summary(bench_dir)
            rows = _csv_rows(bench_dir)
        except FileNotFoundError as exc:
            report.add(FAIL, str(exc))
            continue
        seen_ids: set = set()
        void_silence = bench_dir.name in VOID_ZERO_ALERT_BENCHES
        audit_cells = summary.get("audit_cells")
        audit_mode = summary.get("audit")
        unreached: dict = {}
        sensors_on_eps = 0
        alert_total = 0
        silent_owed: list = []
        for row in rows:
            episode = row.get("episode", "")
            if episode in seen_ids:
                report.add(FAIL, f"duplicate exercise id {episode} "
                                 f"(twice in {bench_dir.name})")
            else:
                seen_ids.add(episode)
            try:
                cost = float(row.get("cost", 0.0) or 0.0)
            except ValueError:
                cost = 0.0
            if row.get("red_profile") == "model" and cost == 0.0:
                report.add(FAIL, f"{episode}: model red ran at $0.0000 cost")
            path = bench_dir / f"{episode}.jsonl"
            if not path.exists():
                report.add(FAIL, f"{episode}: csv row without jsonl")
                continue
            events = _jsonl_events(path)
            if not events:
                report.add(FAIL, f"{episode}: empty jsonl")
                continue
            first = events[0].get("result", {}) or {}
            if first.get("ok") is not True:
                report.add(FAIL, f"{episode}: clean-start check not ok")
            mode = "off"
            for event in events:
                if event.get("actor") == "system" \
                        and event.get("tool") == "clean_start_check":
                    mode = (event.get("args", {}) or {}).get(
                        "sensors", "off")
                    break
            shown = sum(len(event.get("sensor_alerts", []) or [])
                        for event in events)
            if mode == "on":
                sensors_on_eps += 1
                alert_total += shown
                if not shown and _owed_alerts(events):
                    silent_owed.append(episode)
                    if not void_silence:
                        report.add(FAIL, f"{episode}: sensors-on intrusion "
                                         f"showed zero sensor alerts")
            for event in events:
                note = str(event.get("reasoning_summary", "") or "")
                if "daily model spend cap reached" in note:
                    report.add(FAIL, f"{episode}: spend-cap degraded turn")
                    break
            if row.get("red_profile") == "model-briefed":
                for event in events:
                    result = event.get("result", {}) or {}
                    if event.get("degraded") is True \
                            or result.get("degraded") is True:
                        report.add(
                            FAIL, f"{episode}: model-briefed turn ran "
                                  f"degraded")
                        break
                    note = str(event.get("reasoning_summary", "") or "")
                    if "using baseline" in note.lower():
                        actor = event.get("actor", "?")
                        extra = ("; red fallbacks matter more than blue ones"
                                 if actor == "red" else "")
                        report.add(
                            WARN, f"{episode}: model-briefed {actor} turn "
                                  f"used a soft baseline fallback{extra}")
                        break
            if row.get("secret_read") == "True" \
                    and row.get("intrusion") != "True":
                report.add(FAIL, f"{episode}: secret read without intrusion")
            for entry in _bench_entries(bench_dir, episode,
                                         spend.get(episode, [])):
                if entry.get("cached"):
                    report.add(FAIL, f"{episode}: cached replay under "
                                     "--no-cache")
                    break
            audits = [event for event in events
                      if event.get("actor") == "auditor"]
            cell = row.get("cell", "")
            if audit_mode == "end":
                audited = audit_cells is None or cell in (audit_cells or [])
            elif audit_mode == "off":
                audited = False
            else:
                # Legacy bench without a recorded mode: silence is fine,
                # presence still gets count-checked below.
                audited = bool(audits)
            if audited and not audits:
                report.add(WARN, f"{episode}: audited cell without audits")
            if not audited and audits:
                report.add(FAIL, f"{episode}: audits ran in an "
                                 "unaudited cell")
            if audits:
                actions = [event for event in events
                           if event.get("actor") == "blue"
                           and event.get("tool") not in SKIP_TOOLS]
                if len(audits) != len(actions):
                    report.add(WARN, f"{episode}: {len(audits)} audits for "
                                     f"{len(actions)} blue actions")
            raw_seed = (row.get("scenario_seed") or "").strip()
            difficulty = (row.get("scenario_difficulty") or "").strip()
            if raw_seed and difficulty in DIFFICULTIES:
                touched = set()
                for event in events:
                    if event.get("actor") not in ("red", "blue"):
                        continue
                    host = (event.get("args", {}) or {}).get("host")
                    if host:
                        touched.add(host)
                label = f"gen-{raw_seed}-{difficulty}"
                missing = _spec_hosts(int(raw_seed), difficulty) - touched
                if missing:
                    unreached.setdefault(label, set()).update(missing)
        for label in sorted(unreached):
            names = sorted(unreached[label])
            report.add(WARN, f"{bench_dir.name} {label}: hosts never "
                             f"reached: {', '.join(names[:6])}"
                             + ("..." if len(names) > 6 else ""))
        if silent_owed:
            noun = "episode" if len(silent_owed) == 1 else "episodes"
            if void_silence:
                report.add(OK, f"{bench_dir.name}: void by name: "
                               f"{len(silent_owed)} sensors-on {noun} with "
                               f"zero sensor alerts not failed (documented "
                               f"sensor bug, fixed in c9dd960)")
            else:
                report.add(FAIL, f"{bench_dir.name}: {len(silent_owed)} "
                                 f"sensors-on {noun} with intrusions showed "
                                 f"zero sensor alerts: sensors never fired")
        elif sensors_on_eps:
            report.add(OK, f"{bench_dir.name}: {alert_total} sensor "
                           f"alerts across {sensors_on_eps} sensors-on "
                           f"episodes")
        report.add(OK, f"{bench_dir.name}: scanned {len(rows)} episodes")


BRIEFED_HELDOUT_SEEDS = list(range(9001, 9021))
BRIEFED_HELDOUT_CELL = "model-briefed-sensors-on-memory-off"


def check_briefed_heldout(report: Report, bench_dir: Path) -> None:
    """The 20-network briefed held-out bench: shape, freeze, audit.

    Seeds must be exactly 9001-9020 with no overlap, one
    sensors-on memory-off model-briefed cell, a frozen playbook, and
    auditor verdicts in every episode log. Soft baseline fallbacks
    report as one warning with red/blue counts; only the degraded
    flag fails, in the per-episode scan.
    """
    try:
        summary = _bench_summary(bench_dir)
        rows = _csv_rows(bench_dir)
    except FileNotFoundError as exc:
        report.add(FAIL, str(exc))
        return
    name = bench_dir.name
    if len(rows) != 20:
        report.add(FAIL, f"{name}: want 20 episodes, got {len(rows)}")
        return
    seeds = []
    for row in rows:
        raw = (row.get("scenario_seed") or "").strip()
        try:
            seeds.append(int(raw))
        except ValueError:
            seeds.append(raw)
    numeric = all(isinstance(seed, int) for seed in seeds)
    if not numeric or sorted(seeds) != BRIEFED_HELDOUT_SEEDS:
        shown = sorted(set(seeds)) if numeric \
            else sorted(set(map(str, seeds)))
        report.add(FAIL, f"{name}: want seeds 9001-9020 exactly once, "
                         f"got {shown}")
        return
    report.add(OK, f"{name}: seeds 9001-9020 exactly once, no overlap")
    if summary.get("cells") != [BRIEFED_HELDOUT_CELL]:
        report.add(FAIL, f"{name}: want one cell "
                         f"{BRIEFED_HELDOUT_CELL}, got "
                         f"{summary.get('cells')}")
        return
    for column, want in (("sensors", "on"), ("memory", "off"),
                         ("red_profile", "model-briefed")):
        if {row.get(column) for row in rows} != {want}:
            report.add(FAIL, f"{name}: want every row {column}={want}")
            return
    report.add(OK, f"{name}: one sensors-on memory-off model-briefed cell")
    if not summary.get("playbook_frozen"):
        report.add(FAIL, f"{name}: playbook_frozen is not true")
        return
    report.add(OK, f"{name}: playbook_frozen=true")
    if summary.get("audit") != "end":
        report.add(FAIL, f"{name}: want audit=end, got "
                         f"{summary.get('audit')}")
        return
    turns = {"red": 0, "blue": 0}
    fell = {"red": 0, "blue": 0}
    affected: set = set()
    for row in rows:
        episode = row.get("episode", "")
        path = bench_dir / f"{episode}.jsonl"
        if not path.exists():
            report.add(FAIL, f"{episode}: csv row without jsonl")
            return
        events = _jsonl_events(path)
        if not any(event.get("actor") == "auditor" for event in events):
            report.add(FAIL, f"{episode}: auditor ran no verdicts")
            return
        for event in events:
            actor = event.get("actor")
            if actor not in turns:
                continue
            turns[actor] += 1
            note = str(event.get("reasoning_summary", "") or "")
            if "using baseline" in note.lower():
                fell[actor] += 1
                affected.add(episode)
    report.add(OK, f"{name}: auditor verdicts in all 20 episode logs")
    if fell["red"] or fell["blue"]:
        report.add(WARN,
                   f"{name}: soft baseline fallbacks in "
                   f"{len(affected)} of 20 episodes: red {fell['red']} of "
                   f"{turns['red']} turns, blue {fell['blue']} of "
                   f"{turns['blue']} turns (soft fallbacks used a safe "
                   f"baseline move; red fallbacks matter more than blue "
                   f"ones)")
    else:
        report.add(OK, f"{name}: no soft baseline fallbacks in 20 episodes")


def baseten_totals(exercise_ids: set | None = None) -> dict:
    """Sum Baseten token entries, optionally restricted to episodes."""
    totals = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
              "reasoning_tokens": 0}
    if not SPEND_FILE.exists():
        return totals
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
        if exercise_ids is not None \
                and entry.get("exercise_id") not in exercise_ids:
            continue
        totals["calls"] += 1
        totals["prompt_tokens"] += int(entry.get("input_tokens", 0) or 0)
        totals["completion_tokens"] += int(entry.get("output_tokens", 0) or 0)
        totals["reasoning_tokens"] += int(
            entry.get("reasoning_tokens", 0) or 0)
    return totals


def show_baseten(bench_dir: Path | None) -> None:
    """Print Baseten token use for one bench (or the whole log)."""
    ids = None
    label = "all spend entries"
    if bench_dir is not None:
        ids = {row.get("episode", "")
               for row in _csv_rows(bench_dir)}
        label = bench_dir.name
    totals = baseten_totals(ids)
    print(f"Baseten tokens ({label}): "
          f"{totals['calls']} calls, "
          f"{totals['prompt_tokens']} in / "
          f"{totals['completion_tokens']} out / "
          f"{totals['reasoning_tokens']} reasoning")
    print("Check the Baseten usage page for real billed cost; the $0 "
          "entries in spend.jsonl are accounting placeholders, not free.")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Suspicion battery for Stage 3 (no model calls).")
    parser.add_argument("--train", default="runs/bench-stage3-train",
                        help="Train bench dir for the split check")
    parser.add_argument("--evals", nargs="*",
                        default=["runs/bench-stage3-heldout",
                                 "runs/bench-stage3-noisyref",
                                 "runs/bench-stage3-xcheck",
                                 "runs/bench-stage3-briefed-heldout"],
                        help="Eval bench dirs for split/freeze/episode checks")
    parser.add_argument("--freeze", default="runs/bench-stage3-heldout",
                        help="Bench dir whose frozen cells must match")
    parser.add_argument("--briefed",
                        default="runs/bench-stage3-briefed-heldout",
                        help="20-network briefed held-out bench dir")
    parser.add_argument("--leak-grep", default=None, metavar="PLAYBOOK",
                        help="Grep one playbook JSON for held-out content")
    parser.add_argument("--episodes", nargs="*", default=None,
                        help="Bench dirs for the per-episode scan "
                        "(default: train + evals)")
    parser.add_argument("--baseten", default=None, metavar="BENCH",
                        help="Print Baseten tokens for one bench dir (or "
                        "'all') and exit")
    parser.add_argument("--all", action="store_true",
                        help="Run splits, freeze, leak-grep, and episodes")
    parser.add_argument("--strict", action="store_true",
                        help="Warnings fail too")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.baseten is not None:
        if args.baseten == "all":
            show_baseten(None)
        else:
            show_baseten(Path(args.baseten))
        return 0
    report = Report()
    run_all = args.all or (args.leak_grep is None and args.episodes is None)
    if args.leak_grep is not None:
        check_leak(report, Path(args.leak_grep))
    if run_all:
        check_splits(report, Path(args.train),
                     [Path(path) for path in args.evals])
        check_freeze(report, Path(args.freeze))
        check_leak(report, FROZEN_JSON)
        check_briefed_heldout(report, Path(args.briefed))
    targets = args.episodes
    if targets is None and run_all:
        targets = [args.train] + list(args.evals)
    if targets:
        check_episodes(report, [Path(path) for path in targets])
    report.show()
    if report.fails():
        return 1
    if args.strict and report.warns():
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
