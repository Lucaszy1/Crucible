#!/usr/bin/env python3
"""Export the dashboard as a self-contained static replay site.

Builds docs/site/ from dashboard/ plus a small set of featured
replays and the Learning/results data, so the bundle works from
plain static hosting (GitHub Pages) and from file:// with no
server, no Docker, and no model calls.

What the build does:

- Generates 4 featured replays (one contained easy run taken
  verbatim from runs/sample.jsonl, one hard run, one briefed-red
  run, one held run) using real scenario specs from
  crucible.scenarios.generate. Crafted runs carry
  "synthetic": true in their JSON for provenance; every run is
  scored with orchestrator.score_events and outcome-checked.
- Writes the API replacements as static JSON under site/data/:
  runs index, one replay per run, featured list, attack map,
  config, stage3 Learning numbers (derived from
  dashboard/results.json), frozen playbook, and results.json.
- Embeds the same data as window.__CRUCIBLE_STATIC__ in
  site/data.js. The page reads embedded data only, never fetch:
  fetch and ES modules are both blocked on file:// in Chrome,
  so the dashboard JS is bundled into one classic script
  (site/app.bundle.js) with a static api layer.
- Rewrites absolute URLs to relative ones, disables the
  live-exercise and takeover controls with a "run locally to try
  this" note, and shows a "Static replay" server state.
- Fails the build if any emitted text file still references
  localhost or /api.

Usage:
  python scripts/build_static.py [--out docs/site]

Deterministic: fixed timestamps and sorted keys, so rebuilding
produces identical bytes.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = ROOT / "dashboard"
JS_DIR = DASHBOARD_DIR / "js"
DEFAULT_OUT = ROOT / "docs" / "site"

sys.path.insert(0, str(ROOT))

from crucible import orchestrator  # noqa: E402
from crucible import sandbox  # noqa: E402
from crucible.scenarios.generate import generate  # noqa: E402

BASE_TIME = datetime(2026, 10, 28, 12, 0, 0, tzinfo=timezone.utc)
STEP = timedelta(seconds=7)

RUN_IDS = [
    "scn-0007-contained",
    "static-hard-exposed",
    "briefed-red-contained",
    "static-held-quick",
]

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
# Event builders
# --------------------------------------------------------------------------

class Script:
    """Append events with sequential ids and fixed timestamps."""

    def __init__(self, base: datetime):
        self.base = base
        self.events: list = []
        self.findings = 0

    def add(self, actor: str, tool: str, args: dict, result: dict,
            summary: str) -> dict:
        event = {
            "id": f"evt-{len(self.events) + 1:04d}",
            "timestamp": (self.base + STEP * len(self.events)).isoformat(),
            "actor": actor,
            "tool": tool,
            "args": args,
            "result": result,
            "reasoning_summary": summary,
            "evidence_refs": [],
        }
        self.events.append(event)
        return event

    def finding_id(self) -> str:
        self.findings += 1
        return f"finding-{self.findings}"

    def audit(self, target_id: str, verdict: str, score: int,
              rationale: str) -> dict:
        return self.add(
            "auditor", "audit", {"target_event_id": target_id},
            {"ok": True, "target_event_id": target_id,
             "verdict": verdict, "score": score,
             "rationale": rationale, "evidence_checked": [],
             "refs_valid": True, "model_used": "none",
             "estimated_cost_usd": 0.0},
            f"[{verdict} {score}/10] {rationale}",
        )


def system_event(script: Script, spec: dict, seed: int,
                 difficulty: str) -> None:
    layout = sandbox.layout_for_spec(spec)
    hosts = [host["id"] for host in layout["hosts"]]
    script.add(
        "system", "clean_start_check",
        {"sensors": "on", "blue_reasoning_effort": "default",
         "scenario_id": spec["scenario_id"], "scenario_seed": seed,
         "scenario_difficulty": difficulty,
         "scenario_layout": {
             "scenario_id": spec["scenario_id"], "hosts": [
                 {"id": host["id"], "role": host["role"],
                  "segment": host["segment"], "links": host["links"],
                  "weight": host["weight"]}
                 for host in layout["hosts"]],
             "jewel_host": layout["jewel_host"]}},
        {"ok": True, "attempts": 1,
         "checks": {"ok": True, "failures": {}, "hosts": hosts}},
        "Clean-start check passed on attempt 1; "
        "log scope covers this exercise only.",
    )


def unreachable(host: str) -> dict:
    return {"ok": False,
            "error": f"host {host!r} is unreachable "
                     "(isolated from the range)"}


def log_lines(host: str, lines: list, start: int = 0) -> list:
    return [{"id": f"{host}:{start + i}", "line": line}
            for i, line in enumerate(lines)]


def hint_body(host: str, account: str) -> str:
    return json.dumps({
        "admin": True,
        "note": "toy misconfiguration: admin page left open",
        "hint": f"internal host {host!r} runs reports as {account!r}",
    })


def spec_context(spec: dict) -> dict:
    """Pull the names and path steps builders need from one spec."""
    path = spec.get("path", [])
    pages = [step for step in path if step["kind"] == "exposed_page"]
    steps = {step["kind"]: step for step in path}
    return {
        "spec": spec,
        "entry": spec.get("entry_host", ""),
        "jewel_host": (spec.get("jewel", {}) or {}).get("host", ""),
        "jewel_path": (spec.get("jewel", {}) or {}).get("path", ""),
        "pages": pages,
        "steps": steps,
    }


def build_hard_exposed() -> list:
    """Hard run where red reads the protected file before blue reacts."""
    seed, difficulty = 9019, "hard"
    spec = generate(seed, difficulty)
    ctx = spec_context(spec)
    entry = ctx["entry"]
    assert len(ctx["pages"]) == 1, "hard chain shape changed"
    page = ctx["pages"][0]
    weak = ctx["steps"]["weak_creds"]
    backup = ctx["steps"]["backup_creds"]
    known = ctx["steps"]["signin_known"]
    jewel_host, jewel_path = ctx["jewel_host"], ctx["jewel_path"]
    mid_host = weak["host"]
    assert page["host"] == entry
    assert backup["host"] == mid_host
    assert known["host"] == jewel_host

    script = Script(BASE_TIME)
    system_event(script, spec, seed, difficulty)
    script.add("red", "scan_host", {"host": entry},
               {"ok": True, "host": entry, "running": True,
                "reachable": True, "ports": [8000], "isolated": False},
               f"Check whether the {entry} portal answers.")
    script.add("blue", "read_logs", {"host": entry, "n": 50},
               {"ok": True, "host": entry, "lines": []},
               f"Skim recent {entry} logs for early probing.")
    script.add("red", "http_request",
               {"host": entry, "path": "/admin/dashboard"},
               {"ok": True, "host": entry, "status": 404,
                "body": "not found"},
               "Enumerate the known admin endpoint list for a live page.")
    script.add("blue", "list_processes", {"host": jewel_host},
               {"ok": True, "host": jewel_host,
                "processes": ["pid 1 toyhost", "pid 31 reports",
                              "pid 44 fileshare"]},
               f"Confirm the {jewel_host} data host runs clean.")
    script.add("red", "http_request",
               {"host": entry, "path": page["page_path"]},
               {"ok": True, "host": entry, "status": 200,
                "body": hint_body(page["leaks_host"],
                                  page["leaks_account"])},
               "Follow the exposed page hint to the next host.")
    script.add("blue", "read_logs", {"host": entry, "n": 50},
               {"ok": True, "host": entry, "lines": log_lines(entry, [
                   "request from=172.18.0.5 method=GET "
                   "path=/admin/dashboard status=404",
                   f"request from=172.18.0.5 method=GET "
                   f"path={page['page_path']} status=200",
               ])},
               "The portal took two admin page hits in a row.")
    script.add("red", "try_credential",
               {"host": mid_host, "user": weak["account"],
                "password": weak["password"]},
               {"ok": True, "host": mid_host, "authenticated": True},
               f"Try the leaked sign-in on {mid_host}.")
    script.add("blue", "wait", {"reason": "watch for lateral movement"},
               {"ok": True, "waited": True,
                "reason": "watch for lateral movement"},
               "Hold one turn and watch for lateral movement.")
    script.add("red", "list_files",
               {"host": mid_host, "path": backup["dir"]},
               {"ok": True, "host": mid_host, "path": backup["dir"],
                "files": [backup["path"].rsplit("/", 1)[-1]]},
               f"List the {mid_host} archive for stored sign-ins.")
    script.add("blue", "report_finding",
               {"text": f"Portal probing plus an archive listing on "
                        f"{mid_host} looks like one intrusion."},
               {"ok": True, "id": script.finding_id()},
               "Record the intrusion pattern with log lines.")
    script.add("red", "read_file",
               {"host": mid_host, "path": backup["path"]},
               {"ok": True, "host": mid_host, "path": backup["path"],
                "content": "[logins]\n"
                           f"host={known['host']}\n"
                           f"user={known['account']}\n"
                           "password=<redacted in replay>"},
               "Read the exported sign-ins for the next hop.")
    script.add("blue", "read_logs", {"host": mid_host, "n": 50},
               {"ok": True, "host": mid_host, "lines": log_lines(mid_host, [
                   f"signin ok user={weak['account']} from=controller",
                   f"dir list path={backup['dir']} entries=1 "
                   "from=controller",
                   f"file read path={backup['path']} bytes=96 "
                   "from=controller",
               ])},
               f"Pull {mid_host} logs to confirm the sign-in.")
    script.add("red", "try_credential",
               {"host": known["host"], "user": known["account"],
                "password": known["password"]},
               {"ok": True, "host": known["host"],
                "authenticated": True},
               f"Reuse the harvested sign-in on {known['host']}.")
    script.add("blue", "isolate_host", {"host": known["host"]},
               {"ok": True, "host": known["host"], "isolated": True},
               f"Detach {known['host']} to cut off the session.")
    script.add("red", "read_file",
               {"host": jewel_host, "path": jewel_path},
               {"ok": True, "host": jewel_host, "path": jewel_path,
                "content": "CRUCIBLE-EXERCISE-SECRET (toy)"},
               "Read the protected file before the cutoff lands.")
    script.add("blue", "patch_service",
               {"host": jewel_host, "service": "fileshare"},
               {"ok": True, "host": jewel_host, "service": "fileshare",
                "detail": "patch flag applied; "
                          "sign-in rotated on the host"},
               "Rotate the data host sign-in after the read.")
    script.add("red", "report_finding",
               {"text": "Protected file obtained from the data host."},
               {"ok": True, "id": script.finding_id()},
               "Record what the probe sequence obtained.")
    blue_ids = [event["id"] for event in script.events
                if event["actor"] == "blue"]
    script.audit(blue_ids[0], "justified", 8,
                 "Looking before acting is reasonable when nothing "
                 "is confirmed yet.")
    script.audit(blue_ids[1], "justified", 7,
                 "Checking the data host early is proportionate.")
    script.audit(blue_ids[2], "justified", 8,
                 "The request lines support watching closely.")
    script.audit(blue_ids[3], "unsupported", 3,
                 "Waiting while an intruder moves laterally lacks "
                 "evidence.")
    script.audit(blue_ids[4], "justified", 7,
                 "The finding matches the portal and archive lines.")
    script.audit(blue_ids[5], "justified", 8,
                 "Confirming the sign-in in logs is solid practice.")
    script.audit(blue_ids[6], "justified", 9,
                 "Detaching on a confirmed harvested sign-in is right, "
                 "even though the read beat it.")
    script.audit(blue_ids[7], "justified", 7,
                 "Rotating the sign-in after exposure limits reuse.")
    return script.events


def build_briefed_contained() -> list:
    """Briefed model red vs scripted blue: fast intrusion, contained."""
    seed, difficulty = 9005, "medium"
    spec = generate(seed, difficulty)
    ctx = spec_context(spec)
    entry = ctx["entry"]
    assert len(ctx["pages"]) == 2, "medium chain shape changed"
    first, second = ctx["pages"]
    weak = ctx["steps"]["weak_creds"]
    jewel_host, jewel_path = ctx["jewel_host"], ctx["jewel_path"]
    target = weak["host"]
    assert first["host"] == entry
    assert second["host"] == first["leaks_host"]
    assert target == second["leaks_host"] == jewel_host

    script = Script(BASE_TIME + timedelta(hours=2))
    system_event(script, spec, seed, difficulty)
    script.add("red", "scan_host", {"host": entry},
               {"ok": True, "host": entry, "running": True,
                "reachable": True, "ports": [8000], "isolated": False},
               "Per the engagement scope, confirm the dmz surface "
               "answers before enumerating it.")
    script.add("blue", "read_logs", {"host": entry, "n": 50},
               {"ok": True, "host": entry, "lines": []},
               f"Inspect recent {entry} logs for odd requests.")
    script.add("red", "http_request",
               {"host": entry, "path": first["page_path"]},
               {"ok": True, "host": entry, "status": 200,
                "body": hint_body(first["leaks_host"],
                                  first["leaks_account"])},
               "Enumerate the scoped endpoint list; the live page "
               "names the next host and account.")
    script.add("blue", "report_finding",
               {"text": f"Scoped probing on {entry} matches the "
                        "briefed red playbook."},
               {"ok": True, "id": script.finding_id()},
               "Record the detection with supporting log lines.")
    script.add("red", "http_request",
               {"host": second["host"], "path": second["page_path"]},
               {"ok": True, "host": second["host"], "status": 200,
                "body": hint_body(second["leaks_host"],
                                  second["leaks_account"])},
               "The scope names this host once the first page leaks "
               "it; enumerate the same list there.")
    script.add("blue", "read_logs", {"host": second["host"], "n": 50},
               {"ok": True, "host": second["host"],
                "lines": log_lines(second["host"], [
                    f"request from=172.18.0.5 method=GET "
                    f"path={second['page_path']} status=200",
                ])},
               f"Confirm the {second['host']} page hit in its logs.")
    script.add("red", "try_credential",
               {"host": target, "user": weak["account"],
                "password": weak["password"]},
               {"ok": True, "host": target, "authenticated": True},
               "Per the weak-pool guidance, try the leaked account "
               "only, then move to discovery.")
    script.add("blue", "read_logs", {"host": target, "n": 50},
               {"ok": True, "host": target, "lines": log_lines(target, [
                   f"signin ok user={weak['account']} from=controller",
               ])},
               f"Confirm the {target} sign-in in its own logs.")
    script.add("red", "list_files",
               {"host": target, "path": "/secret"},
               {"ok": True, "host": target, "path": "/secret",
                "files": [jewel_path.rsplit("/", 1)[-1]]},
               "List the secret directory before reading.")
    script.add("blue", "isolate_host", {"host": target},
               {"ok": True, "host": target, "isolated": True},
               f"Detach the {target} host to stop further access.")
    script.add("red", "read_file",
               {"host": target, "path": jewel_path},
               unreachable(target),
               "Read the exercise secret after signing in.")
    script.add("blue", "patch_service",
               {"host": target, "service": "fileshare"},
               {"ok": True, "host": target, "service": "fileshare",
                "detail": "patch flag applied; "
                          "sign-in rotated on the host"},
               "Apply the toy fix so the leaked sign-in stops working.")
    script.add("red", "report_finding",
               {"text": "Containment landed before the file read."},
               {"ok": True, "id": script.finding_id()},
               "Record what the probe sequence obtained.")
    blue_ids = [event["id"] for event in script.events
                if event["actor"] == "blue"]
    script.audit(blue_ids[0], "justified", 8,
                 "Looking before acting is reasonable when nothing "
                 "is confirmed yet.")
    script.audit(blue_ids[1], "unsupported", 4,
                 "The report ran ahead of the log lines blue had read.")
    script.audit(blue_ids[2], "justified", 8,
                 "Tracking the second scoped page hit is solid.")
    script.audit(blue_ids[3], "justified", 9,
                 "Confirming the sign-in before detaching is solid.")
    script.audit(blue_ids[4], "justified", 9,
                 "Detaching on a confirmed sign-in plus a restricted "
                 "listing is exactly right.")
    script.audit(blue_ids[5], "justified", 7,
                 "Rotating the leaked sign-in closes the briefing path.")
    return script.events


def build_held_quick() -> list:
    """Easy run where blue detaches the entry host before red gets in."""
    seed, difficulty = 9003, "easy"
    spec = generate(seed, difficulty)
    ctx = spec_context(spec)
    entry = ctx["entry"]
    assert len(ctx["pages"]) == 1, "easy chain shape changed"
    page = ctx["pages"][0]
    weak = ctx["steps"]["weak_creds"]
    target = weak["host"]
    assert page["host"] == entry
    assert target == page["leaks_host"] == ctx["jewel_host"]

    script = Script(BASE_TIME + timedelta(hours=4))
    system_event(script, spec, seed, difficulty)
    script.add("red", "scan_host", {"host": entry},
               {"ok": True, "host": entry, "running": True,
                "reachable": True, "ports": [8000], "isolated": False},
               f"Check whether the {entry} portal is reachable.")
    script.add("blue", "read_logs", {"host": entry, "n": 50},
               {"ok": True, "host": entry, "lines": []},
               f"Inspect recent {entry} logs for odd requests.")
    script.add("red", "http_request",
               {"host": entry, "path": page["page_path"]},
               {"ok": True, "host": entry, "status": 200,
                "body": hint_body(page["leaks_host"],
                                  page["leaks_account"])},
               "Probe the exposed admin page for a lead.")
    script.add("blue", "read_logs", {"host": entry, "n": 50},
               {"ok": True, "host": entry, "lines": log_lines(entry, [
                   f"request from=172.18.0.5 method=GET "
                   f"path={page['page_path']} status=200",
               ])},
               "One admin page hit is enough to watch closely.")
    script.add("red", "try_credential",
               {"host": target, "user": weak["account"],
                "password": "wrong-guess-1"},
               {"ok": True, "host": target, "authenticated": False},
               "Guess at the hinted account before the real leak.")
    script.add("blue", "isolate_host", {"host": entry},
               {"ok": True, "host": entry, "isolated": True},
               f"Detach {entry} while the intruder is still outside.")
    script.add("red", "http_request",
               {"host": entry, "path": page["page_path"]},
               unreachable(entry),
               "Retry the admin page through the cutoff.")
    script.add("blue", "patch_service",
               {"host": entry, "service": "webapp"},
               {"ok": True, "host": entry, "service": "webapp",
                "detail": "patch flag applied"},
               f"Close the exposed admin page on {entry}.")
    script.add("red", "report_finding",
               {"text": "No path past the detached portal."},
               {"ok": True, "id": script.finding_id()},
               "Record what the probe sequence obtained.")
    blue_ids = [event["id"] for event in script.events
                if event["actor"] == "blue"]
    script.audit(blue_ids[0], "justified", 8,
                 "Looking before acting is reasonable when nothing "
                 "is confirmed yet.")
    script.audit(blue_ids[1], "justified", 8,
                 "The request line supports watching closely.")
    script.audit(blue_ids[2], "justified", 9,
                 "Detaching the probed portal before any sign-in "
                 "held the range.")
    script.audit(blue_ids[3], "justified", 7,
                 "Closing the exposed page removes the entry.")
    return script.events


def load_sample_events() -> list:
    """Read the real sample run verbatim (the easy contained replay)."""
    path = ROOT / "runs" / "sample.jsonl"
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


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


def build_replays() -> dict:
    """Build, score, and outcome-check every featured replay."""
    runs = {
        "scn-0007-contained": {
            "events": load_sample_events(),
            "synthetic": False,
            "note": "Real scripted exercise on scn-0007-easy-a0, "
                    "kept verbatim from runs/sample.jsonl.",
            "want": "contained",
        },
        "static-hard-exposed": {
            "events": build_hard_exposed(),
            "synthetic": True,
            "note": "Docker-free reconstruction on scn-9019-hard-a0: "
                    "red reads the protected file before blue reacts.",
            "want": "secret-exposed",
        },
        "briefed-red-contained": {
            "events": build_briefed_contained(),
            "synthetic": True,
            "note": "Docker-free reconstruction of a briefed model red "
                    "run (engagement scope briefing) vs scripted blue on "
                    "a medium network: fast intrusion, contained.",
            "want": "contained",
        },
        "static-held-quick": {
            "events": build_held_quick(),
            "synthetic": True,
            "note": "Docker-free reconstruction on an easy network: "
                    "blue detaches the portal before red gets in.",
            "want": "held",
        },
    }
    for run_id, run in runs.items():
        events = run["events"]
        score = orchestrator.score_events(events)
        if score is None:
            raise SystemExit(f"error: {run_id} could not be scored")
        got = outcome_key(events)
        if got != run["want"]:
            raise SystemExit(
                f"error: {run_id} came out {got}, want {run['want']}")
        run["score"] = score
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
                   "synthetic": runs[run_id]["synthetic"],
                   "note": runs[run_id]["note"]}
        write_json(data_dir / "runs" / f"{run_id}.json", payload)
        replays[run_id] = payload

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
    return {
        "runsIndex": read("runs.json")["runs"],
        "replays": embedded_replays,
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
  return { id: replay.id, events, score: replay.score || null };
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
'''

APP_PATCHES = [
    ('loadStageArt("/assets/art/")', 'loadStageArt("assets/art/")'),
    ("  refreshRuns();\n  await pollStatus();\n  setInterval(pollStatus, 5000);",
     "  refreshRuns();\n  staticModeBoot();"),
    ("  if (livePath) {", "  if (livePath && !window.CRUCIBLE_STATIC) {"),
]

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

THUMB_PATCHES = [
    ("          const live = thumbJobs.get(key);\n"
     "          if (live) live.visible = true;\n"
     "          requestThumbIdle();",
     "          const live = thumbJobs.get(key);\n"
     "          if (live) {\n"
     "            live.visible = true;\n"
     "          } else if (!(thumbInFlight && thumbInFlight.key === key) &&\n"
     "              !cacheGet(key, job.moves)) {\n"
     "            // The cache generation turned over after this card\n"
     "            // queued (art arrived): re-queue it instead of waiting\n"
     "            // on a job that no longer exists.\n"
     "            job.gen = bitmapGen;\n"
     "            job.visible = true;\n"
     "            thumbJobs.set(key, job);\n"
     "          }\n"
     "          requestThumbIdle();"),
    ("  const entry = job.gen === bitmapGen ? cacheGet(job.key, job.moves) : null;\n"
     "  for (const target of job.targets) {\n"
     "    if (entry) blitThumb(target, entry);\n",
     "  const entry = job.gen === bitmapGen ? cacheGet(job.key, job.moves) : null;\n"
     "  for (const target of job.targets) {\n"
     "    if (entry) blitThumb(target, entry);\n"
     "    else if (typeof window !== \"undefined\" && window.CRUCIBLE_STATIC &&\n"
     "        target && typeof target._thumbPaint === \"function\") {\n"
     "      // Snapshots fail where the canvas is tainted (file art on\n"
     "      // file hosting): paint the frame straight onto the card.\n"
     "      try { target._thumbPaint(); } catch { /* stays empty */ }\n"
     "    }\n"),
]

MODULE_PATCHES = {
    "app": APP_PATCHES,
    "commandbar": COMMANDBAR_PATCHES,
    "learn": LEARN_PATCHES,
    "thumbnail": THUMB_PATCHES,
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
]

STATIC_CSS = """
.light[data-state="static"] { background: var(--accent); }
.static-note {
  margin: 10px 0 0;
  color: var(--muted);
  font-size: 0.9rem;
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


def build_site(out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    runs = build_replays()
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
    args = parser.parse_args(argv)
    summary = build_site(args.out)
    print(f"Static site: {summary['out']}")
    print(f"Featured replays ({len(summary['runs'])}): "
          f"{', '.join(summary['runs'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
