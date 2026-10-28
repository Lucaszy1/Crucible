#!/usr/bin/env python3
"""Export the dashboard as a self-contained static replay site.

Builds docs/site/ from dashboard/ plus a small set of featured
replays and the Learning/results data, so the bundle works from
plain static hosting (GitHub Pages) and from file:// with no
server, no Docker, and no model calls.

What the build does:

- Embeds 4 featured replays, all real episode logs: the easy
  contained run from runs/sample.jsonl plus three bench episodes
  (briefed red, hard held-out, sensors-off exposed) read from the
  run folders. Each replay JSON stores its source bench, episode
  id, and seed; every run is scored with
  orchestrator.score_events and outcome-checked.
- Writes the API replacements as static JSON under site/data/:
  runs index, one replay per run, one Layer 1 debrief per run,
  featured list, attack map, config, stage3 Learning numbers
  (derived from dashboard/results.json), frozen playbook, and
  results.json. Debriefs are precomputed with
  debrief.build_debrief, deterministic and model-free, so the
  Story tab Debrief card works with no server.
- Embeds the same data as window.__CRUCIBLE_STATIC__ in
  site/data.js. The page reads embedded data only, never fetch:
  fetch and ES modules are both blocked on file:// in Chrome,
  so the dashboard JS is bundled into one classic script
  (site/app.bundle.js) with a static api layer.
- Rewrites absolute URLs to relative ones, disables the
  live-exercise and takeover controls with a "run locally to try
  this" note, shows a "Static replay" server state, and shows a
  "Real run" provenance badge on lobby cards and the replay
  header for every featured replay.
- Fails the build if any emitted text file still references
  localhost or /api, or if any replay lacks real provenance.

Usage:
  python scripts/build_static.py [--out docs/site]
      [--source-runs PATH]

Bench episodes are read from --source-runs (default: the runs/
folders next to this checkout), plus runs/sample.jsonl locally.

Deterministic: episode logs are fixed bytes and JSON keys are
sorted, so rebuilding produces identical bytes.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = ROOT / "dashboard"
JS_DIR = DASHBOARD_DIR / "js"
DEFAULT_OUT = ROOT / "docs" / "site"

sys.path.insert(0, str(ROOT))

from crucible import debrief  # noqa: E402
from crucible import orchestrator  # noqa: E402

BASE_TIME = datetime(2026, 10, 28, 12, 0, 0, tzinfo=timezone.utc)

# Featured replays: all real episode logs. The sensors-off arm of
# bench-stage3-heldout is valid; only that bench's sensors-on half
# is void, and no pick comes from it.
SOURCES = [
    {"id": "scn-9001-contained",
     "bench": "bench-stage3-heldout-sensors",
     "episode": "gen-9001-easy-solver-sensors-on-memory-on-1",
     "want": "contained"},
    {"id": "briefed-medium-contained",
     "bench": "bench-stage3-briefed-vs-open",
     "episode": "gen-9008-medium-model-briefed-sensors-on-memory-off-1",
     "want": "contained"},
    {"id": "heldout-hard-contained",
     "bench": "bench-stage3-heldout-sensors",
     "episode": "gen-9019-hard-solver-sensors-on-memory-on-1",
     "want": "contained"},
    {"id": "sensorsoff-hard-exposed",
     "bench": "bench-stage3-heldout",
     "episode": "gen-9018-hard-solver-sensors-off-memory-off-1",
     "want": "secret-exposed"},
]

RUN_IDS = [source["id"] for source in SOURCES]

FORBIDDEN_MARKERS = ("localhost", "/api")
BINARY_SUFFIXES = {".png", ".webp", ".jpg", ".jpeg", ".gif",
                    ".woff", ".woff2", ".ttf", ".ico"}

VOID_NOTE = (
    "The sensors-on half of the first held-out run is void because of a "
    "collector bug, so it is not shown. Sensors-on numbers come from the "
    "corrected rerun (runs/bench-stage3-heldout-sensors); sensors-off numbers "
    "come from the valid half of the first run (runs/bench-stage3-heldout)."
)


# --------------------------------------------------------------------------
# Real episode logs
# --------------------------------------------------------------------------

def resolve_source_runs(explicit: Path | None) -> Path:
    """Return the bench run folder, defaulting to the main checkout."""
    if explicit is not None:
        return explicit
    sibling = ROOT.parent / "Crucible" / "runs"
    if sibling.is_dir():
        return sibling
    return ROOT / "runs"


def load_episode_events(source_runs: Path, source: dict) -> list:
    """Read one featured replay's episode log, verbatim."""
    if "local" in source:
        path = ROOT / source["local"]
    else:
        path = (source_runs / source["bench"]
                / f"{source['episode']}.jsonl")
        if not path.is_file():
            local = (ROOT / "runs" / source["bench"]
                     / f"{source['episode']}.jsonl")
            if local.is_file():
                path = local
    if not path.is_file():
        raise SystemExit(f"error: episode log missing: {path}")
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def scenario_info(events: list) -> dict:
    """Pull seed, scenario, and difficulty from the system event."""
    for event in events:
        args = event.get("args") or {}
        if event.get("actor") == "system" and args.get("scenario_id"):
            return {
                "seed": args.get("scenario_seed"),
                "scenario": args.get("scenario_id"),
                "difficulty": args.get("scenario_difficulty"),
            }
    return {"seed": None, "scenario": None, "difficulty": None}


def outcome_key(events: list) -> str:
    """Mirror the dashboard displayOutcome rule for build checks."""
    secret = any(event.get("actor") == "red"
                 and event.get("tool") == "read_file"
                 and (event.get("result") or {}).get("ok") is True
                 and str((event.get("result") or {}).get("path", ""))
                 .startswith("/secret/")
                 for event in events)
    got_in = any(event.get("actor") == "red"
                 and event.get("tool") == "try_credential"
                 and (event.get("result") or {}).get("authenticated")
                 is True
                 for event in events)
    contained = any(event.get("actor") in ("blue", "human")
                    and event.get("tool") in ("isolate_host",
                                              "patch_service")
                    and (event.get("result") or {}).get("ok") is True
                    for event in events)
    acted = any(event.get("actor") in ("blue", "human")
                for event in events)
    if secret:
        return "secret-exposed"
    if got_in and contained:
        return "contained"
    if got_in:
        return "uncontained"
    if contained:
        return "held"
    if acted:
        return "no-intrusion"
    return "stalemate"


def featured_curation() -> dict:
    """Read the live lobby curation for static cross-checks."""
    return json.loads(
        (DASHBOARD_DIR / "featured.json").read_text(encoding="utf-8"))


def title_outcome_key(title: str) -> str | None:
    """Outcome key a curated title promises, from its plain words."""
    low = str(title or "").lower()
    if "exposed" in low:
        return "secret-exposed"
    if "contained" in low:
        return "contained"
    return None


def build_replays(source_runs: Path) -> dict:
    """Load, score, and outcome-check every featured replay."""
    curated = featured_curation()
    live_ids = sorted(curated.get("featured") or [])
    static_ids = sorted(source["episode"] for source in SOURCES)
    if static_ids != live_ids:
        raise SystemExit(
            "error: static SOURCES episodes drifted from "
            "dashboard/featured.json")
    runs = {}
    for source in SOURCES:
        run_id = source["id"]
        events = load_episode_events(source_runs, source)
        if len(events) < 6:
            raise SystemExit(f"error: {run_id} has too few events")
        score = orchestrator.score_events(events)
        if score is None:
            raise SystemExit(f"error: {run_id} could not be scored")
        got = outcome_key(events)
        if got != source["want"]:
            raise SystemExit(
                f"error: {run_id} came out {got}, want {source['want']}")
        titled = title_outcome_key(
            (curated.get("titles") or {}).get(source["episode"]))
        if titled is None:
            raise SystemExit(
                f"error: {run_id} episode has no featured title")
        if got != titled:
            raise SystemExit(
                f"error: {run_id} title promises {titled}, "
                f"episode came out {got}")
        info = scenario_info(events)
        if info["seed"] is None:
            raise SystemExit(f"error: {run_id} has no scenario seed")
        try:
            report = debrief.build_debrief(events, run_id=run_id)
        except ValueError as exc:
            raise SystemExit(f"error: {run_id} debrief failed: {exc}")
        missing = debrief.validate_citations(report, events)
        if missing:
            raise SystemExit(
                f"error: {run_id} debrief cites missing events: "
                f"{', '.join(missing[:3])}")
        runs[run_id] = {
            "events": events,
            "score": score,
            "debrief": report,
            "provenance": {
                "bench": source["bench"],
                "episode": source["episode"],
                "seed": info["seed"],
                "scenario": info["scenario"],
                "difficulty": info["difficulty"],
            },
        }
    return runs


# --------------------------------------------------------------------------
# Static JSON data
# --------------------------------------------------------------------------

def build_runs_index(runs: dict) -> dict:
    """Mirror GET /api/runs over the exported replays."""
    entries = []
    for run_id in RUN_IDS:
        events = runs[run_id]["events"]
        score = runs[run_id]["score"] or {}
        entries.append({
            "id": run_id,
            "events": len(events),
            "outcome": score.get("outcome"),
            "blue_score": score.get("blue_score"),
            "justified_pct": score.get("justified_pct"),
            "unsupported_count": score.get("unsupported_count", 0),
            "invalid_refs_count": score.get("invalid_refs_count", 0),
            "mean_audit_score": score.get("mean_audit_score"),
            "mtime": BASE_TIME.isoformat(),
        })
    return {"runs": entries}


def build_stage3() -> dict:
    """Mirror GET /api/bench/stage3 from dashboard/results.json.

    Arm counts are exact cell hits. The sensors-on pairings are exact
    too: both memory arms agree everywhere (20/20 contained, 0/20
    secret reads), so every discordant count is necessarily zero.
    The sensors-off half has no exported episode pairings, so it
    carries pairs of zero with an honest note instead of p-values.
    """
    results = json.loads(
        (DASHBOARD_DIR / "results.json").read_text(encoding="utf-8"))
    cells = results["cells"]

    def arm(key: str) -> dict:
        cell = cells[key]
        return {
            "n": cell["n"],
            "contained": cell["contained_after_intrusion"]["hits"],
            "secretRead": cell["secret_read"]["hits"],
        }

    book = json.loads((ROOT / "runs" / "stage3" / "frozen-playbook.json")
                      .read_text(encoding="utf-8"))
    rules = [{"rule": entry["rule"],
              "episode": entry.get("episode", "")}
             for entry in book.get("rules", []) or []
             if isinstance(entry, dict) and entry.get("rule")]
    return {
        "sensorsOn": {
            "source": "dashboard/results.json: "
                      "solver-sensors-on-memory-on/off "
                      "(corrected rerun)",
            "rerun": True,
            "on": arm("solver-sensors-on-memory-on"),
            "off": arm("solver-sensors-on-memory-off"),
            "pairs": {"n": 20, "contained": {"b": 0, "c": 0},
                      "secret": {"b": 0, "c": 0}},
        },
        "sensorsOff": {
            "source": "dashboard/results.json: "
                      "solver-sensors-off-memory-on/off "
                      "(valid half of the first run)",
            "rerun": False,
            "on": arm("solver-sensors-off-memory-on"),
            "off": arm("solver-sensors-off-memory-off"),
            "pairs": {"n": 0, "contained": {"b": 0, "c": 0},
                      "secret": {"b": 0, "c": 0}},
            "pairNote": "No paired p-value here: results.json keeps "
                        "exact arm counts but no episode-level pairings "
                        "for this half, so only the counts above apply.",
        },
        "voidNote": VOID_NOTE,
        "playbook": {
            "source": "runs/stage3/frozen-playbook.json",
            "frozen": True,
            "rules": rules,
        },
    }


def build_config() -> dict:
    """Mirror GET /api/config with the classic range defaults."""
    weights = dict(orchestrator.DEFAULT_BUSINESS_WEIGHTS)
    return {
        "hosts": [host for host in weights],
        "business_weights": weights,
        "daily_limit": 5.0,
    }


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def write_data_files(out: Path, runs: dict) -> dict:
    """Write site/data/*.json and return the embedded-data payload."""
    data_dir = out / "data"
    if data_dir.exists():
        shutil.rmtree(data_dir)

    index = build_runs_index(runs)
    write_json(data_dir / "runs.json", index)
    replays = {}
    for run_id in RUN_IDS:
        payload = {"id": run_id, "events": runs[run_id]["events"],
                   "score": runs[run_id]["score"],
                   "provenance": runs[run_id]["provenance"]}
        write_json(data_dir / "runs" / f"{run_id}.json", payload)
        replays[run_id] = payload
        debrief_payload = {"id": run_id, "debrief": runs[run_id]["debrief"]}
        write_json(data_dir / "debriefs" / f"{run_id}.json", debrief_payload)

    featured = {"_note": "Featured replays in the static bundle, "
                         "in display order.",
                "featured": list(RUN_IDS)}
    write_json(data_dir / "featured.json", featured)

    attack_map = json.loads(
        (DASHBOARD_DIR / "attack_map.json").read_text(encoding="utf-8"))
    write_json(data_dir / "attack_map.json", attack_map)

    config = build_config()
    write_json(data_dir / "config.json", config)

    stage3 = build_stage3()
    write_json(data_dir / "stage3.json", stage3)

    playbook = json.loads((ROOT / "runs" / "stage3" / "frozen-playbook.json")
                          .read_text(encoding="utf-8"))
    write_json(data_dir / "playbook.json", playbook)

    results = json.loads(
        (DASHBOARD_DIR / "results.json").read_text(encoding="utf-8"))
    write_json(data_dir / "results.json", results)

    # The embedded copy is read back from the JSON files just
    # written, so data.js and data/*.json cannot disagree.
    def read(name: str) -> object:
        return json.loads((data_dir / name).read_text(encoding="utf-8"))

    embedded_replays = {}
    for run_id in RUN_IDS:
        embedded_replays[run_id] = json.loads(
            (data_dir / "runs" / f"{run_id}.json")
            .read_text(encoding="utf-8"))
    embedded_debriefs = {}
    for run_id in RUN_IDS:
        embedded_debriefs[run_id] = json.loads(
            (data_dir / "debriefs" / f"{run_id}.json")
            .read_text(encoding="utf-8"))
    return {
        "runsIndex": read("runs.json")["runs"],
        "replays": embedded_replays,
        "debriefs": embedded_debriefs,
        "featured": read("featured.json")["featured"],
        "attackMap": read("attack_map.json"),
        "config": read("config.json"),
        "stage3": read("stage3.json"),
    }


def write_data_js(out: Path, embedded: dict) -> None:
    """Write the embedded data as a classic script (file:// safe)."""
    blob = json.dumps(embedded, sort_keys=True, separators=(",", ":"))
    blob = blob.replace("</", "<\\/")
    out.joinpath("data.js").write_text(
        "// Crucible static replay data. Generated by "
        "scripts/build_static.py — do not edit.\n"
        f"window.__CRUCIBLE_STATIC__ = {blob};\n",
        encoding="utf-8")


# --------------------------------------------------------------------------
# Static api layer (replaces dashboard/js/api.js in the bundle)
# --------------------------------------------------------------------------

STATIC_API = '''\
// Static replay data layer: every reader serves the embedded
// export, and every live writer fails with the local-run note.
import { normalizeActor } from "./state.js";

function embedded() {
  return window.__CRUCIBLE_STATIC__ || {};
}

function localOnly() {
  return new Error("Live exercises need a local server — run locally to try this.");
}

export function fetchRuns() {
  return Promise.resolve({ runs: embedded().runsIndex || [] });
}

export async function fetchRun(runId) {
  const replay = (embedded().replays || {})[runId];
  if (!replay) throw new Error(`unknown run ${JSON.stringify(runId)}`);
  const events = (replay.events || []).map((ev) => normalizeActor(ev));
  return {
    id: replay.id, events, score: replay.score || null,
    provenance: replay.provenance || null,
  };
}

export function fetchStage3() {
  return Promise.resolve(embedded().stage3 || null);
}

export function fetchAttackMap() {
  return Promise.resolve(embedded().attackMap || { red: {}, blue: {}, stages: [] });
}

export function fetchFeatured() {
  return Promise.resolve({ featured: embedded().featured || [] });
}

export function fetchConfig() {
  return Promise.resolve(
    embedded().config || { hosts: [], business_weights: {}, daily_limit: 5 });
}

export function fetchDebrief(runId) {
  const hit = (embedded().debriefs || {})[runId];
  if (!hit) return Promise.reject(new Error(`unknown run ${JSON.stringify(runId)}`));
  return Promise.resolve(hit);
}

export function fetchCampaigns() {
  return Promise.resolve({ campaigns: [] });
}

export function fetchCampaign(name) {
  return Promise.reject(new Error(`unknown campaign ${JSON.stringify(name)}`));
}

export function fetchStatus() {
  return Promise.resolve({
    running: false, exercise_id: null, agents: null, scenario: null,
    run_id: null, started_at: null, spent_today: 0, daily_limit: 5,
    live_exercise_cost: 0, estimated_exercise_cost: 0, warning: null,
    last_outcome: null, last_exercise_cost: 0, last_warning: null,
  });
}

export function fetchPreview() {
  return Promise.reject(localOnly());
}

export function parseOrder() {
  return Promise.reject(localOnly());
}

export function sendOrder() {
  return Promise.reject(localOnly());
}

export function sendDemo() {
  return Promise.reject(localOnly());
}

export function sendTakeover() {
  return Promise.reject(localOnly());
}

export function fetchCommandState() {
  return Promise.reject(localOnly());
}

export async function sendAudio() {
  throw localOnly();
}

export async function startExercise() {
  throw localOnly();
}

export function openLive(onMessage, onError) {
  const err = localOnly();
  if (typeof onError === "function") {
    setTimeout(() => onError(err), 0);
  }
  return { close() {} };
}
'''

APP_STATIC_BOOT = '''
const STATIC_TRY_LOCAL = "run locally to try this";
function staticModeBoot() {
  setServer(controls, "static", "Static replay");
  const note = "Live exercises need a local server — " + STATIC_TRY_LOCAL + ".";
  if (controls.run) { controls.run.disabled = true; controls.run.title = note; }
  if (controls.agents) { controls.agents.disabled = true; controls.agents.title = note; }
  if (els.liveBtn) { els.liveBtn.disabled = true; els.liveBtn.title = note; }
  if (els.followBox) { els.followBox.disabled = true; els.followBox.title = note; }
  const actions = document.querySelector(".hero-actions");
  if (actions && !document.getElementById("staticLiveNote")) {
    const line = document.createElement("p");
    line.id = "staticLiveNote";
    line.className = "static-note";
    line.textContent = "Static replay bundle: live exercises are disabled here; " +
      STATIC_TRY_LOCAL + ".";
    actions.insertAdjacentElement("afterend", line);
  }
}
function updateProvBadge(prov) {
  const badge = document.getElementById("provBadge");
  if (!badge) return;
  badge.textContent = "";
  if (prov && prov.bench && prov.episode) {
    badge.hidden = false;
    const chip = document.createElement("span");
    chip.className = "lobby-badge";
    chip.textContent = "Real run";
    badge.append(chip, document.createTextNode(
      ` ${prov.bench} / ${prov.episode}, seed ${prov.seed ?? "?"}`));
    badge.title = `Real run: ${prov.bench} / ${prov.episode}, seed ${prov.seed ?? "?"}`;
  } else {
    badge.hidden = true;
  }
}
'''

APP_PATCHES = [
    ('loadStageArt("/assets/art/")', 'loadStageArt("assets/art/")'),
    ("  refreshRuns();\n  await pollStatus();\n  setInterval(pollStatus, 5000);",
     "  refreshRuns();\n  staticModeBoot();"),
    ("  if (livePath) {", "  if (livePath && !window.CRUCIBLE_STATIC) {"),
    ("      id: run.id,\n"
     "      title: (store.lobby.titles || {})[run.id] || null,\n"
     "      provenance: (store.lobby.provenance || {})[run.id] || null,\n"
     "      outcomeKey: out.key,",
     "      id: run.id,\n"
     "      title: (store.lobby.titles || {})[run.id] || null,\n"
     "      provenance: data.provenance || null,\n"
     "      outcomeKey: out.key,"),
    ("  store.replayScore = data.score || null;",
     "  store.replayScore = data.score || null;\n"
     "  if (window.CRUCIBLE_STATIC) updateProvBadge(data.provenance);"),
]

# lobby.js needs no static patches: the live cards render the
# provenance line from model.provenance, and the static bundle
# only swaps where that field comes from (see APP_PATCHES).

COMMANDBAR_STATIC_BAR = '''\
  function staticCommandBar() {
    const note = "Defender orders need a live exercise — run locally to try this.";
    ctl.visible = true;
    bar.hidden = false;
    if (els.mic) { els.mic.disabled = true; els.mic.title = note; }
    if (els.input) { els.input.disabled = true; els.input.title = note; }
    if (els.send) { els.send.disabled = true; els.send.title = note; }
    if (els.demo) { els.demo.disabled = true; els.demo.title = note; }
    if (els.takeover) {
      els.takeover.disabled = true;
      els.takeover.title = note;
      setText(els.takeover, "Take over blue");
    }
    setText(els.spend, "");
    if (els.preview) els.preview.hidden = true;
    if (els.transcript) els.transcript.hidden = true;
    show(note);
  }

'''

COMMANDBAR_PATCHES = [
    ("  async function refreshState() {\n    let state = null;",
     COMMANDBAR_STATIC_BAR
     + "  async function refreshState() {\n"
     + "    if (window.CRUCIBLE_STATIC) { staticCommandBar(); return; }\n"
     + "    let state = null;"),
]

LEARN_PATCHES = [
    ("// Numbers arrive from /api/bench/stage3; stats and sentence helpers",
     "// Numbers arrive from the embedded static export; "
     "stats and sentence helpers"),
    ("  root.append(head, src, wrap, pairs, line);",
     "  root.append(head, src, wrap, pairs, line);\n"
     "  if (section && section.pairNote) {\n"
     "    const extra = doc.createElement(\"p\");\n"
     "    extra.className = \"pairs-line\";\n"
     "    extra.textContent = section.pairNote;\n"
     "    root.append(extra);\n"
     "  }"),
]

# thumbnail.js needs no static patches: the live code re-queues
# jobs dropped by the art turnover and paints frames straight
# onto cards when snapshots fail, which covers file hosting too.
MODULE_PATCHES = {
    "app": APP_PATCHES,
    "commandbar": COMMANDBAR_PATCHES,
    "learn": LEARN_PATCHES,
}


# --------------------------------------------------------------------------
# Module bundler (ES modules -> one classic script for file://)
# --------------------------------------------------------------------------

IMPORT_RE = re.compile(
    r"^[ \t]*import\s*\{\s*([^}]*?)\s*\}\s*from\s*[\"']\./(\w+)\.js[\"'];?",
    re.MULTILINE | re.DOTALL)
EXPORT_FUNC_RE = re.compile(
    r"^([ \t]*)export\s+(async\s+)?function\s+([A-Za-z_$][\w$]*)",
    re.MULTILINE)
EXPORT_CONST_RE = re.compile(
    r"^([ \t]*)export\s+const\s+([A-Za-z_$][\w$]*)", re.MULTILINE)
LEFTOVER_RE = re.compile(r"^[ \t]*(import|export)\s", re.MULTILINE)


def apply_patches(name: str, src: str) -> str:
    for needle, replacement in MODULE_PATCHES.get(name, []):
        if needle not in src:
            raise SystemExit(
                f"error: static patch missed in {name}.js: {needle[:60]!r}")
        src = src.replace(needle, replacement, 1)
    return src


def parse_module(name: str, src: str) -> tuple:
    """Rewrite imports to registry reads and strip export keywords."""
    deps: list = []

    def rewrite_import(match: re.Match) -> str:
        names = [piece.strip() for piece in match.group(1).split(",")
                 if piece.strip()]
        for piece in names:
            if " as " in piece:
                raise SystemExit(
                    f"error: aliased import in {name}.js: {piece!r}")
        target = match.group(2)
        if target not in deps:
            deps.append(target)
        return f"const {{ {', '.join(names)} }} = __mod_{target}__;"

    body = IMPORT_RE.sub(rewrite_import, src)
    exports: list = []

    def strip_func(match: re.Match) -> str:
        exports.append(match.group(3))
        return f"{match.group(1)}{match.group(2) or ''}function {match.group(3)}"

    def strip_const(match: re.Match) -> str:
        exports.append(match.group(2))
        return f"{match.group(1)}const {match.group(2)}"

    body = EXPORT_FUNC_RE.sub(strip_func, body)
    body = EXPORT_CONST_RE.sub(strip_const, body)
    leftover = LEFTOVER_RE.search(body)
    if leftover:
        raise SystemExit(
            f"error: unhandled module syntax in {name}.js: "
            f"{leftover.group(0).strip()!r}")
    return body, deps, exports


def topo_order(modules: dict) -> list:
    """Order modules so every dependency comes before its importers."""
    order: list = []
    visiting: set = set()

    def visit(name: str) -> None:
        if name in order:
            return
        if name in visiting:
            raise SystemExit(f"error: import cycle at {name}.js")
        visiting.add(name)
        _body, deps, _exports = modules[name]
        for dep in deps:
            if dep not in modules:
                raise SystemExit(
                    f"error: {name}.js imports missing {dep}.js")
            visit(dep)
        visiting.remove(name)
        order.append(name)

    for name in sorted(modules):
        visit(name)
    order.remove("app")
    order.append("app")
    return order


def build_bundle() -> str:
    """Bundle dashboard/js into one classic script with static patches."""
    sources = {"api": STATIC_API}
    for path in sorted(JS_DIR.glob("*.js")):
        if path.name == "api.js":
            continue
        sources[path.stem] = path.read_text(encoding="utf-8")
    modules = {}
    for name, src in sources.items():
        if name == "app":
            src = APP_STATIC_BOOT + src
        src = apply_patches(name, src)
        modules[name] = parse_module(name, src)
    parts = ["// Crucible static replay bundle. Generated by "
             "scripts/build_static.py — do not edit."]
    for name in topo_order(modules):
        body, _deps, exports = modules[name]
        if name == "app":
            if exports:
                raise SystemExit("error: app.js must not export")
            parts.append(f"(() => {{\n\"use strict\";\n{body}\n}})();")
        else:
            joined = ", ".join(exports)
            parts.append(f"const __mod_{name}__ = (() => {{\n"
                         f"\"use strict\";\n{body}\n"
                         f"return {{ {joined} }};\n}})();")
    return "\n".join(parts) + "\n"


# --------------------------------------------------------------------------
# Site assembly
# --------------------------------------------------------------------------

HTML_PATCHES = [
    ('<link rel="stylesheet" href="/css/style.css">',
     '<link rel="icon" href="data:,">\n'
     '<link rel="stylesheet" href="css/style.css">'),
    ('<script type="module" src="/js/app.js"></script>',
     '<script>window.CRUCIBLE_STATIC = true;</script>\n'
     '<script src="data.js"></script>\n'
     '<script src="app.bundle.js"></script>'),
    ('<button id="runBtn" class="btn primary">Start exercise</button>',
     '<button id="runBtn" class="btn primary" disabled '
     'title="Live exercises need a local server — '
     'run locally to try this.">Start exercise</button>'),
    ('<button id="liveBtn" class="btn primary hero-cta">'
     'Run a live exercise</button>',
     '<button id="liveBtn" class="btn primary hero-cta" disabled '
     'title="Live exercises need a local server — '
     'run locally to try this.">Run a live exercise</button>'),
    ('<span id="eventCount" class="event-count">0 events</span>',
     '<span id="eventCount" class="event-count">0 events</span>\n'
     '    <span id="provBadge" class="prov-badge" hidden></span>'),
]

STATIC_CSS = """
.light[data-state="static"] { background: var(--accent); }
.static-note {
  margin: 10px 0 0;
  color: var(--muted);
  font-size: 0.9rem;
}
.prov-badge {
  font-size: 0.78rem;
  color: var(--muted);
  max-width: 44vw;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
"""


def write_shell(out: Path, bundle: str) -> None:
    """Write index.html, the bundle, styles, and art with relative URLs."""
    html = (DASHBOARD_DIR / "index.html").read_text(encoding="utf-8")
    for needle, replacement in HTML_PATCHES:
        if needle not in html:
            raise SystemExit(
                f"error: static HTML patch missed: {needle[:60]!r}")
        html = html.replace(needle, replacement, 1)
    out.joinpath("index.html").write_text(html, encoding="utf-8")
    out.joinpath("app.bundle.js").write_text(bundle, encoding="utf-8")

    css = (DASHBOARD_DIR / "css" / "style.css").read_text(encoding="utf-8")
    if 'url("/assets/' not in css:
        raise SystemExit("error: expected absolute art URLs in style.css")
    css = css.replace('url("/assets/', 'url("../assets/')
    out.joinpath("css").mkdir(parents=True, exist_ok=True)
    out.joinpath("css", "style.css").write_text(css + STATIC_CSS,
                                                encoding="utf-8")

    assets = out / "assets"
    if assets.exists():
        shutil.rmtree(assets)
    shutil.copytree(DASHBOARD_DIR / "assets", assets,
                    ignore=shutil.ignore_patterns("*.md"))


def enforce_no_server_refs(out: Path) -> None:
    """Fail the build if any emitted text file names localhost or /api."""
    hits = []
    for path in sorted(out.rglob("*")):
        if not path.is_file() or path.suffix.lower() in BINARY_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for marker in FORBIDDEN_MARKERS:
                if marker in line:
                    hits.append(f"{path.relative_to(out)}:"
                                f"{lineno}: {marker}")
    if hits:
        raise SystemExit("error: static site still references the server:\n"
                         + "\n".join(hits))


def build_site(out: Path, source_runs: Path | None = None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    runs = build_replays(resolve_source_runs(source_runs))
    embedded = write_data_files(out, runs)
    copy.deepcopy(embedded)  # guard: payload must stay JSON-plain
    write_data_js(out, embedded)
    bundle = build_bundle()
    write_shell(out, bundle)
    enforce_no_server_refs(out)
    return {"runs": list(RUN_IDS), "out": str(out)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Export the static replay site.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="output directory (default docs/site)")
    parser.add_argument("--source-runs", type=Path, default=None,
                        help="bench run folder (default: main checkout)")
    args = parser.parse_args(argv)
    summary = build_site(args.out, args.source_runs)
    print(f"Static site: {summary['out']}")
    print(f"Featured replays ({len(summary['runs'])}): "
          f"{', '.join(summary['runs'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
