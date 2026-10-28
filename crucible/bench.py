#!/usr/bin/env python3
"""Run a controlled benchmark across red profiles and blue arms.

Red profiles: chain runs the full intrusion chain with fixed moves and
no model calls; noisy runs the same chain with seeded decoy probes
mixed in; solver runs the spec-derived chain with seeded imperfection
and no model calls; model is the existing model red team, and
model-briefed is model red with an engagement scope briefing and a
60-turn budget. Blue arms vary memory
on or off and sensors on or off. Cells interleave round-robin so time
effects spread
evenly, episodes carry fixed seeds, and every episode gets the same
clean-start check the orchestrator runs. Resume-safe like campaign.py,
with a spend guard that stops before degraded episodes pollute a cell.

Per-episode logs, cell playbooks, and summaries land under
runs/bench-<name>/, plus a CSV and a plain per-cell table on stdout.

Examples:
  python -m crucible.bench --name oct27 --episodes-per-cell 10
  python -m crucible.bench --name try --red chain --sensors on --memory off \\
      --episodes-per-cell 2
  python -m crucible.bench --name open --blue-model meta-models/muse-spark-open-1026 \\
      --blue-base-url https://inference.baseten.co/v1 --blue-api-key-env BASETEN_API_KEY
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from crucible import events, llm, orchestrator, run_exercise, sandbox, tools
from crucible.solver_red import SolverRed
from crucible.agents import ModelBlue, ModelRed
from crucible.agents.briefed_red import ModelBriefedRed
from crucible.agents.scripted_blue import ScriptedBlue
from crucible.agents import referee
from crucible.scenarios import select, stack
from crucible.scenarios.schema import DIFFICULTIES

RED_PROFILES = ("chain", "noisy", "model", "solver", "model-briefed")
BLUE_PROFILES = ("model", "scripted")
SENSOR_MODES = ("on", "off")
MEMORY_MODES = ("on", "off")

CSV_FIELDS = (
    "episode",
    "cell",
    "red_profile",
    "sensors",
    "memory",
    "seed",
    "outcome",
    "intrusion",
    "contained_after_intrusion",
    "secret_read",
    "relevant_detection_delay",
    "detection_delay",
    "time_to_contain",
    "downtime_pct",
    "blue_score",
    "cost",
    "blue_reasoning_effort",
    "scenario_id",
    "scenario_seed",
    "scenario_difficulty",
    "blue_profile",
    "assisted",
)

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# Wrong sign-in pairs for noisy-red decoys. Checked against the live
# scenario defaults at plan time so a decoy can never guess right.
WRONG_PAIRS = (
    ("admin", "changeme"),
    ("admin", "admin"),
    ("root", "toor"),
    ("guest", "guest"),
    ("operator", "operator"),
)

DECOY_PATHS = ("/", "/status")

DECOY_HOSTS = ("web", "app", "db", "workstation")


def _chain_defaults() -> dict:
    """Read the chain's hosts, accounts, and paths from the scenario."""
    try:
        scenario = sandbox.load_scenario()
    except Exception:
        scenario = {}
    shipped = scenario.get("shipped_defaults", {}) or {}
    app = shipped.get("app", {}) or {}
    db = shipped.get("db", {}) or {}
    secret = scenario.get("secret", {}) or {}
    admin = scenario.get("admin_endpoint", {}) or {}
    app_config = scenario.get("app_config", {}) or {}
    return {
        "admin_host": admin.get("host", "web"),
        "admin_path": admin.get("path", "/admin"),
        "app_host": app_config.get("host", "app"),
        "app_dir": "/srv/app/",
        "app_config_path": app_config.get("path", "/srv/app/config.ini"),
        "app_user": app.get("user", "svc-report"),
        "app_password": app.get("pass", "password"),
        "db_user": db.get("user", "dbops"),
        "db_password": db.get("pass", "dbops-fake-19"),
        "secret_host": secret.get("host", "db"),
        "secret_dir": "/secret/",
        "secret_path": secret.get("path", "/secret/exercise_secret.txt"),
    }


def chain_plan() -> list:
    """Return the fixed full-chain move list, no model calls.

    Reads the admin page, signs in to app, lists and reads the config,
    signs in to db, then lists and reads the secret.
    """
    info = _chain_defaults()
    return [
        {"tool": "http_request",
         "args": {"host": info["admin_host"], "path": info["admin_path"]},
         "reasoning": "Read the exposed admin page.",
         "evidence_refs": []},
        {"tool": "try_credential",
         "args": {"host": info["app_host"], "user": info["app_user"],
                 "password": info["app_password"]},
         "reasoning": "Sign in to the app host.",
         "evidence_refs": []},
        {"tool": "list_files",
         "args": {"host": info["app_host"], "path": info["app_dir"]},
         "reasoning": "List the app directory before reading.",
         "evidence_refs": []},
        {"tool": "read_file",
         "args": {"host": info["app_host"], "path": info["app_config_path"]},
         "reasoning": "Read the app config for the next sign-in.",
         "evidence_refs": []},
        {"tool": "try_credential",
         "args": {"host": info["secret_host"], "user": info["db_user"],
                 "password": info["db_password"]},
         "reasoning": "Sign in to the data host.",
         "evidence_refs": []},
        {"tool": "list_files",
         "args": {"host": info["secret_host"], "path": info["secret_dir"]},
         "reasoning": "List the secret directory before reading.",
         "evidence_refs": []},
        {"tool": "read_file",
         "args": {"host": info["secret_host"], "path": info["secret_path"]},
         "reasoning": "Read the exercise secret.",
         "evidence_refs": []},
    ]


class ChainRed:
    """Scripted red that always runs the full chain, deterministically.

    Under a generated spec the plan is the spec's derived chain.
    """

    actor = "red"

    def __init__(self, spec=None):
        if spec is None:
            spec = sandbox.active_spec()
        if spec is not None:
            from crucible.scenarios import chain as chain_mod

            self._plan = chain_mod.chain_plan(spec)
        else:
            self._plan = chain_plan()
        self._step = 0

    def next_action(self, turn: int, history: list) -> dict | None:
        """Return the next chain move, or None when finished."""
        del turn, history
        if self._step >= len(self._plan):
            return None
        action = self._plan[self._step]
        self._step += 1
        return {"tool": action["tool"], "args": dict(action["args"]),
                "reasoning": action["reasoning"], "evidence_refs": []}


def noisy_plan(seed: str) -> list:
    """Return the chain with seeded decoy probes mixed in.

    The same seed always yields the same move list. Decoys are scans,
    benign page probes, failed guesses that can never match the live
    defaults, and idle notes, inserted at seeded positions ahead of
    chain steps. Chain steps keep their relative order.
    """
    info = _chain_defaults()
    rng = random.Random(seed)
    real = {(info["app_host"], info["app_user"], info["app_password"]),
            (info["secret_host"], info["db_user"], info["db_password"])}
    chain = chain_plan()
    gaps = list(range(len(chain)))
    count = rng.randint(2, 5)
    slots = sorted(rng.sample(gaps, min(count, len(gaps))))
    decoys: dict = {}
    for slot in slots:
        kind = rng.choice(("scan", "probe", "guess", "idle"))
        if kind == "scan":
            decoys.setdefault(slot, []).append({
                "tool": "scan_host",
                "args": {"host": rng.choice(DECOY_HOSTS)},
                "reasoning": "Decoy scan of one host.",
                "evidence_refs": []})
        elif kind == "probe":
            decoys.setdefault(slot, []).append({
                "tool": "http_request",
                "args": {"host": info["admin_host"],
                         "path": rng.choice(DECOY_PATHS)},
                "reasoning": "Decoy page probe.",
                "evidence_refs": []})
        elif kind == "guess":
            for _ in range(10):
                host = rng.choice(DECOY_HOSTS)
                user, password = rng.choice(WRONG_PAIRS)
                if (host, user, password) not in real:
                    break
            decoys.setdefault(slot, []).append({
                "tool": "try_credential",
                "args": {"host": host, "user": user, "password": password},
                "reasoning": "Decoy sign-in guess.",
                "evidence_refs": []})
        else:
            decoys.setdefault(slot, []).append({
                "tool": "report_finding",
                "args": {"text": "Continuing probes, nothing conclusive yet."},
                "reasoning": "Idle note while probing.",
                "evidence_refs": []})
    plan = []
    for index, step in enumerate(chain):
        plan.extend(decoys.get(index, []))
        plan.append(step)
    return plan


class NoisyRed:
    """Scripted chain red with seeded decoys, still zero model calls.

    Under a generated spec the plan is the spec's derived noisy chain.
    """

    actor = "red"

    def __init__(self, seed: str, spec=None):
        self._seed = seed
        if spec is None:
            spec = sandbox.active_spec()
        if spec is not None:
            from crucible.scenarios import chain as chain_mod

            self._plan = chain_mod.noisy_plan(spec, seed)
        else:
            self._plan = noisy_plan(seed)
        self._step = 0

    def next_action(self, turn: int, history: list) -> dict | None:
        """Return the next planned move, or None when finished."""
        del turn, history
        if self._step >= len(self._plan):
            return None
        action = self._plan[self._step]
        self._step += 1
        return {"tool": action["tool"], "args": dict(action["args"]),
                "reasoning": action["reasoning"], "evidence_refs": []}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run a controlled benchmark.")
    parser.add_argument("--name", required=True, help="Benchmark name")
    parser.add_argument("--red", default="chain,noisy",
                        help="Comma-separated red profiles: chain, noisy, "
                        "model, solver, model-briefed")
    parser.add_argument("--blue", default="model", choices=BLUE_PROFILES,
                        help="Blue profile for every cell: model (default) "
                        "or scripted (zero-cost baseline). One blue per "
                        "benchmark.")
    parser.add_argument("--sensors", default="on,off",
                        help="Comma-separated blue sensor modes: on, off")
    parser.add_argument("--memory", default="off",
                        help="Comma-separated blue memory modes: on, off")
    parser.add_argument("--episodes-per-cell", type=int, default=10,
                        help="Episodes per cell per scenario (default 10)")
    parser.add_argument("--blue-model", default=None,
                        help="Model id for blue (default: the blue role model)")
    parser.add_argument("--blue-base-url", default=None,
                        help="Custom API base URL for blue")
    parser.add_argument("--blue-api-key-env", default=None,
                        help="Env var holding the API key for blue's custom host")
    parser.add_argument("--blue-reasoning-effort", default=None,
                        choices=("omit", "minimal", "low", "medium", "high"),
                        help="Reasoning effort for blue's calls only "
                        "(default: today's behavior; omit sends no "
                        "reasoning_effort field at all)")
    parser.add_argument("--audit", choices=("off", "end"), default="off",
                        help="off skips judging, end audits after each episode")
    parser.add_argument("--audit-cells", default=None,
                        help="Comma-separated cell keys to audit when "
                        "--audit end (default: audit every cell)")
    parser.add_argument("--max-turns", type=int, default=40,
                        help="Turns per episode, capped at 60")
    parser.add_argument("--no-cache", action="store_true",
                        help="Skip the model response cache for this run")
    parser.add_argument("--spend-cap-usd", type=float, default=None,
                        help="Stop before an episode would pass this spend "
                        "(default: the daily limit)")
    parser.add_argument("--resume", action="store_true",
                        help="Continue an existing benchmark instead of starting over")
    parser.add_argument("--force", action="store_true",
                        help="Archive the existing benchmark dir and start over")
    parser.add_argument("--scenarios", default=None,
                        help="Comma list of classic and "
                        "gen:<seed>:<difficulty> (default: classic only)")
    parser.add_argument("--scenario-set", choices=("train", "heldout"),
                        default=None,
                        help="Append the named seed set at --difficulty")
    parser.add_argument("--difficulty", choices=list(DIFFICULTIES),
                        default="medium",
                        help="Difficulty for --scenario-set seeds "
                        "(default: medium)")
    parser.add_argument("--freeze-playbook", action="store_true",
                        help="Blue still reads cell playbooks but the referee "
                        "never updates them (for held-out evaluation)")
    parser.add_argument("--playbook-from", default=None,
                        help="Seed memory-on cells from this playbook JSON "
                        "on a fresh start (ignored with --resume)")
    return parser.parse_args(argv)


def _split_list(raw: str, allowed: tuple, flag: str) -> list:
    parts = [piece.strip() for piece in (raw or "").split(",") if piece.strip()]
    if not parts:
        raise ValueError(f"--{flag} needs at least one value")
    for piece in parts:
        if piece not in allowed:
            raise ValueError(f"--{flag} value {piece!r} must be one of "
                             f"{', '.join(allowed)}")
    return parts


def _cells(args) -> list:
    """Expand the profile flags into the ordered cell list."""
    reds = _split_list(args.red, RED_PROFILES, "red")
    sensor_modes = _split_list(args.sensors, SENSOR_MODES, "sensors")
    memory_modes = _split_list(args.memory, MEMORY_MODES, "memory")
    cells = []
    for red in reds:
        for sensor_mode in sensor_modes:
            for memory in memory_modes:
                key = f"{red}-sensors-{sensor_mode}-memory-{memory}"
                cells.append({"key": key, "red": red,
                              "sensors": sensor_mode, "memory": memory})
    return cells


def _audit_cells(args, cells: list) -> list | None:
    """Parse --audit-cells into cell keys, None for every cell."""
    raw = (args.audit_cells or "").strip()
    if not raw:
        return None
    want = [piece.strip() for piece in raw.split(",") if piece.strip()]
    known = {cell["key"] for cell in cells}
    for key in want:
        if key not in known:
            raise ValueError(f"--audit-cells value {key!r} is not a cell "
                             f"in this benchmark")
    return want


def _bench_dir(name: str) -> Path:
    return events.RUNS_DIR / f"bench-{name}"


def _check_name(name: str) -> str:
    if not _NAME_RE.match(name or ""):
        raise ValueError(
            f"benchmark name {name!r} must use letters, digits, dot, dash, underscore")
    return name


def _write_csv(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_FIELDS))
        writer.writeheader()
        for row in rows:
            out = {key: row.get(key) for key in CSV_FIELDS}
            for key in ("relevant_detection_delay", "detection_delay",
                        "time_to_contain", "scenario_seed",
                        "scenario_difficulty"):
                if out[key] is None:
                    out[key] = ""
            writer.writerow(out)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _load_state(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _archive_dir(bench_dir: Path, name: str) -> Path:
    """Move an existing benchmark dir aside under runs/archive/."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = events.RUNS_DIR / "archive" / f"bench-{name}-{stamp}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(bench_dir), str(target))
    return target


def _open_bench(name: str, cells: list, per_cell: int, args,
                labels: list, audit_cells: list | None) -> dict | None:
    """Load or start the benchmark state; None with an error on failure."""
    bench_dir = _bench_dir(name)
    summary_path = bench_dir / "summary.json"
    rows: list = []
    playbooks: dict = {}
    if args.resume:
        state = _load_state(summary_path)
        if state is None or state.get("name") != name:
            print(f"bench: no resumable benchmark {name!r} "
                  f"at {summary_path}", file=sys.stderr)
            return None
        if state.get("cells") != [cell["key"] for cell in cells]:
            print(f"bench: resume cell set differs from the saved "
                  f"benchmark {name!r}; aborting", file=sys.stderr)
            return None
        if state.get("episodes_per_cell") != per_cell:
            print(f"bench: resume episodes-per-cell differs from the saved "
                  f"benchmark {name!r}; aborting", file=sys.stderr)
            return None
        if state.get("scenarios", ["classic"]) != labels:
            print(f"bench: resume scenario set differs from the saved "
                  f"benchmark {name!r}; aborting", file=sys.stderr)
            return None
        if state.get("playbook_frozen", False) != bool(args.freeze_playbook):
            print(f"bench: resume freeze flag differs from the saved "
                  f"benchmark {name!r}; aborting", file=sys.stderr)
            return None
        if state.get("audit_cells") != audit_cells:
            print(f"bench: resume audit cell set differs from the saved "
                  f"benchmark {name!r}; aborting", file=sys.stderr)
            return None
        saved_audit = state.get("audit")
        if saved_audit is not None and saved_audit != args.audit:
            print(f"bench: resume audit mode differs from the saved "
                  f"benchmark {name!r}; aborting", file=sys.stderr)
            return None
        saved_blue = state.get("blue_profile")
        if saved_blue is not None and saved_blue != args.blue:
            print(f"bench: resume blue profile differs from the saved "
                  f"benchmark {name!r}; aborting", file=sys.stderr)
            return None
        rows = list(state.get("episodes", []) or [])
        for cell in cells:
            playbooks[cell["key"]] = referee.load_playbook(
                bench_dir / "playbooks" / f"{cell['key']}.json")
        print(f"Resuming benchmark {name} with {len(rows)} episodes done...")
    else:
        if bench_dir.exists():
            if not args.force:
                print(f"bench: {bench_dir} already exists; pass "
                      "--resume to continue it or --force to archive it "
                      "and start over", file=sys.stderr)
                return None
            archived = _archive_dir(bench_dir, name)
            print(f"Archived previous benchmark to {archived}")
        seed_book = None
        if args.playbook_from:
            seed_path = Path(args.playbook_from)
            if not seed_path.exists():
                print(f"bench: --playbook-from file not found: "
                      f"{args.playbook_from}", file=sys.stderr)
                return None
            seed_book = referee.load_playbook(seed_path)
            if not seed_book.get("rules"):
                print(f"bench: warning: --playbook-from file has no "
                      f"rules: {args.playbook_from}", file=sys.stderr)
        for cell in cells:
            if cell["memory"] == "on" and seed_book is not None:
                playbooks[cell["key"]] = {
                    "episode": seed_book.get("episode"),
                    "rules": [dict(entry)
                              for entry in seed_book.get("rules", [])],
                    "model_used": seed_book.get("model_used")}
            else:
                playbooks[cell["key"]] = referee.empty_playbook()
        if seed_book is not None:
            for cell in cells:
                if cell["memory"] != "on":
                    continue
                referee.save_playbook(
                    playbooks[cell["key"]],
                    bench_dir / "playbooks" / f"{cell['key']}.json",
                    bench_dir / "playbooks" / f"{cell['key']}.md")
            print(f"Seeded memory-on playbooks from {args.playbook_from}")
        print(f"Starting benchmark {name}: {len(cells)} cells x "
              f"{per_cell} episodes...")
    frozen = bool(args.freeze_playbook)
    if frozen and not args.resume:
        print("Playbooks frozen: blue reads them but the referee "
              "never updates them.")
    return {"name": name, "dir": bench_dir, "summary_path": summary_path,
            "rows": rows, "playbooks": playbooks, "scenarios": labels,
            "playbook_frozen": frozen, "audit_cells": audit_cells}


def _run_episode(bench: dict, cell: dict, rep: int, args, client,
               scenario: dict | None = None, label: str = "classic",
               grouped: bool = False) -> dict:
    """Run one episode for one cell and persist the summaries."""
    exercise_id = f"{label}-{cell['key']}-{rep}" if grouped \
        else f"{cell['key']}-{rep}"
    # The seed pairs arms: same scenario and rep share it across cells,
    # so scripted noisy reds replay identical decoys per pair. Model reds
    # stay stochastic regardless; the exercise id (unique per cell) keeps
    # cache entries separate.
    seed = f"{bench['name']}:{label}:{rep}" if grouped \
        else f"{bench['name']}:{rep}"
    rules = bench["playbooks"][cell["key"]].get("rules", []) or []
    if cell["red"] == "chain":
        red = ChainRed()
    elif cell["red"] == "noisy":
        red = NoisyRed(seed)
    elif cell["red"] == "solver":
        red = SolverRed(seed)
    elif cell["red"] == "model-briefed":
        red = ModelBriefedRed(exercise_id=exercise_id,
                              use_cache=not args.no_cache, seed=seed)
    else:
        red = ModelRed(exercise_id=exercise_id,
                       use_cache=not args.no_cache, seed=seed)
    if args.blue == "scripted":
        blue = ScriptedBlue()
    else:
        blue = ModelBlue(exercise_id=exercise_id,
                         use_cache=not args.no_cache,
                         playbook=rules if cell["memory"] == "on" else None,
                         seed=seed, client=client, model=args.blue_model,
                         reasoning_effort=args.blue_reasoning_effort)
    for host in sandbox.active_hosts():
        tools.restore_host(host)
    spend_start = run_exercise._spend_line_count()
    extra = {"scenario": scenario} if scenario else {}
    audit_mode = args.audit
    wanted = bench.get("audit_cells")
    if audit_mode == "end" and wanted is not None \
            and cell["key"] not in wanted:
        audit_mode = "off"
    result = orchestrator.run_exercise(
        red, blue, max_turns=args.max_turns,
        run_path=bench["dir"] / f"{exercise_id}.jsonl",
        exercise_id=exercise_id, audit=audit_mode,
        auditor_use_cache=not args.no_cache,
        sensors=cell["sensors"], seed=seed,
        blue_reasoning_effort=args.blue_reasoning_effort, **extra)
    score = result.get("score", {}) or {}
    if cell["memory"] == "on" and not args.freeze_playbook:
        review = referee.review_episode(
            result.get("events", []), score, exercise_id,
            bench["playbooks"][cell["key"]], use_cache=not args.no_cache)
        bench["playbooks"][cell["key"]] = review["playbook"]
        referee.save_playbook(
            review["playbook"],
            bench["dir"] / "playbooks" / f"{cell['key']}.json",
            bench["dir"] / "playbooks" / f"{cell['key']}.md")
    cost = run_exercise.exercise_cost_since(spend_start)
    bench["rows"].append({
        "episode": exercise_id,
        "cell": cell["key"],
        "red_profile": cell["red"],
        "sensors": score.get("sensors", cell["sensors"]),
        "memory": cell["memory"],
        "seed": seed,
        "outcome": score.get("outcome", "unknown"),
        "intrusion": score.get("intrusion", False),
        "contained_after_intrusion": score.get(
            "contained_after_intrusion", False),
        "secret_read": score.get("secret_read", score.get(
            "secret_exfiltrated", False)),
        "relevant_detection_delay": score.get("relevant_detection_delay"),
        "detection_delay": score.get("detection_delay"),
        "time_to_contain": score.get("time_to_contain"),
        "downtime_pct": score.get("downtime_pct", 0.0),
        "blue_score": score.get("blue_score", 0),
        "cost": round(cost, 6),
        "blue_reasoning_effort": args.blue_reasoning_effort or "default",
        "scenario": label,
        "scenario_id": (scenario or {}).get("scenario_id", "classic"),
        "scenario_seed": (scenario or {}).get("seed"),
        "scenario_difficulty": (scenario or {}).get("difficulty"),
        "blue_profile": args.blue,
        "assisted": bool(score.get("assisted", False)),
    })
    latest = bench["rows"][-1]
    print(f"{exercise_id}: outcome={latest['outcome']} "
          f"blue_score={latest['blue_score']} "
          f"cost=${cost:.4f}")
    _write_csv(bench["dir"] / "summary.csv", bench["rows"])
    _write_json(bench["summary_path"], {
        "name": bench["name"],
        "cells": [cell["key"] for cell in _cells(args)],
        "episodes_per_cell": args.episodes_per_cell,
        "scenarios": bench.get("scenarios", ["classic"]),
        "playbook_frozen": bool(args.freeze_playbook),
        "audit": args.audit,
        "audit_cells": bench.get("audit_cells"),
        "blue_profile": args.blue,
        "episodes": bench["rows"],
    })
    return result


def _report(bench: dict) -> float:
    """Print the per-cell plain table and return the total cost."""
    rows = bench["rows"]
    effort = bench.get("blue_reasoning_effort", "default")
    print(f"Benchmark done: {len(rows)} episodes (name={bench['name']}, "
          f"blue_reasoning_effort={effort})")
    if bench.get("playbook_frozen"):
        print("Playbooks: frozen (referee never updated them)")
    print("cell | n | intr | secret% | contain% | med-rel-delay | "
          "med-contain | mean-downtime")
    cells = []
    for row in rows:
        if row.get("cell") not in cells:
            cells.append(row.get("cell"))
    for key in cells:
        cell_rows = [row for row in rows if row.get("cell") == key]
        summary = orchestrator.intrusion_conditional_summary(cell_rows)
        secret = summary["secret_read_rate"]
        if secret is None:
            print(f"{key} | {len(cell_rows)} | "
                  f"{summary['intrusions']} | no intrusions")
            continue
        contain = summary["containment_rate"]
        delay = summary["median_relevant_detection_delay"]
        contain_at = summary["median_time_to_contain"]
        downtime = summary["mean_downtime_contained"]
        downtime_text = (f"{downtime:.2f}%"
                         if downtime is not None else "no contained episodes")
        print(f"{key} | {len(cell_rows)} | {summary['intrusions']} | "
              f"{secret * 100:.1f}% | {contain * 100:.1f}% | "
              f"{delay if delay is not None else 'no'} | "
              f"{contain_at if contain_at is not None else 'no'} | "
              f"{downtime_text}")
    for key in cells:
        print(f"--- {key} ---")
        for line in orchestrator.format_intrusion_lines(
                orchestrator.intrusion_conditional_summary(
                    [row for row in rows if row.get("cell") == key])):
            print(line)
    total_cost = sum(float(row.get("cost", 0.0) or 0.0) for row in rows)
    print(f"Total cost: ${total_cost:.4f}")
    print(f"CSV: {bench['dir'] / 'summary.csv'}")
    return total_cost


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        name = _check_name(args.name)
        cells = _cells(args)
        audit_cells = _audit_cells(args, cells)
    except ValueError as exc:
        print(f"bench: {exc}", file=sys.stderr)
        return 1
    per_cell = int(args.episodes_per_cell)
    if per_cell < 1:
        print("--episodes-per-cell must be at least 1", file=sys.stderr)
        return 1
    try:
        keys = select.selection(args.scenarios, args.scenario_set,
                                args.difficulty)
    except ValueError as exc:
        print(f"bench: {exc}", file=sys.stderr)
        return 1
    labels = [select.label(key) for key in keys]
    if args.spend_cap_usd is None:
        cap = llm.daily_limit_usd()
    else:
        cap = float(args.spend_cap_usd)
    if cap <= 0:
        print("--spend-cap-usd must be positive", file=sys.stderr)
        return 1

    bench = _open_bench(name, cells, per_cell, args, labels, audit_cells)
    if bench is None:
        return 1
    bench["blue_reasoning_effort"] = args.blue_reasoning_effort or "default"
    try:
        client = llm.custom_client(args.blue_base_url, args.blue_api_key_env)
    except RuntimeError as exc:
        print(f"bench: {exc}", file=sys.stderr)
        return 1
    bench_start = run_exercise._spend_line_count()

    grouped = len(keys) > 1
    built = False
    done = {row.get("episode") for row in bench["rows"]}
    try:
        for key in keys:
            label = select.label(key)
            try:
                info = stack.activate(key)
            except ValueError as exc:
                print(f"bench: {exc}", file=sys.stderr)
                return 1
            print(f"Booting sandbox (scenario={label})...")
            try:
                if built:
                    sandbox.start_warm()
                else:
                    sandbox.start()
            except sandbox.SandboxError as exc:
                print(f"Sandbox boot failed: {exc}", file=sys.stderr)
                print("Check that Docker is running, then try again.",
                      file=sys.stderr)
                return 1
            built = True
            for rep in range(1, per_cell + 1):
                for cell in cells:
                    want = f"{label}-{cell['key']}-{rep}" if grouped \
                        else f"{cell['key']}-{rep}"
                    if want in done:
                        continue
                    try:
                        spent = llm.spent_today()
                    except (OSError, ValueError):
                        spent = 0.0
                    if spent >= cap:
                        print(f"bench: spend guard hit ${spent:.2f} of "
                              f"${cap:.2f}; stopping with "
                              f"{len(bench['rows'])} episodes done "
                              f"(resume later to continue)", file=sys.stderr)
                        return 2
                    result = _run_episode(bench, cell, rep, args, client,
                                          info, label, grouped)
                    latest_id = bench["rows"][-1]["episode"]
                    done.add(latest_id)
                    if run_exercise._spend_cap_hit(result.get("events", [])):
                        run_exercise._warn_spend_cap()
                        print(f"bench: spend cap degraded {latest_id}; "
                              f"stopping instead of recording degraded "
                              f"episodes as clean ones.",
                              file=sys.stderr)
                        return 2
            try:
                sandbox.stop()
            except sandbox.SandboxError as exc:
                print(f"Sandbox stop noted: {exc}", file=sys.stderr)
    finally:
        print("Stopping sandbox...")
        try:
            sandbox.stop()
        except sandbox.SandboxError as exc:
            print(f"Sandbox stop noted: {exc}", file=sys.stderr)
        tools.reset_state()
        stack.deactivate()
        close = getattr(client, "close", None)
        if callable(close):
            close()

    _report(bench)
    baseten_line = run_exercise.format_baseten_line(
        run_exercise.baseten_tokens_since(bench_start))
    if baseten_line:
        print(baseten_line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
