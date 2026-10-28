"""FastAPI backend for the Crucible Operations Center dashboard.

Serves the static dashboard page plus a small JSON and server-sent
events API over finished runs and live exercises. Runs are the jsonl
files under runs/ (spend.jsonl excluded). Only one exercise runs at a
time; live events stream over GET /api/live while it proceeds.
"""

from __future__ import annotations

import json
import os
import queue
import re
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from crucible import debrief, events, llm, orchestrator, sandbox, tools, voice_api
from crucible.agents import ModelBlue, ModelRed, ScriptedBlue, ScriptedRed
from crucible.agents.briefed_red import ModelBriefedRed
from crucible.human_blue import AIWithOverride, CommandQueue, HumanBlue
from crucible.scenarios import generate, select, stack
from crucible.run_exercise import SPEND_CAP_MARKER
from crucible.voice_api import router as voice_router

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = ROOT / "dashboard"

# Run ids come from jsonl stems under runs/. This keeps path traversal
# out: only plain file names reach the filesystem, never directories.
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_EXERCISE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# Non-run jsonl files under runs/ that must stay out of listings.
SKIP_FILES = {"spend.jsonl"}

# Replay pacing: one event per 1.2 seconds at speed 1.
REPLAY_INTERVAL_S = 1.2

# Model-driven live variant: briefed red on the Meta API, open blue
# on Baseten, both behind the same $2 run budget as the daily guard.
MODEL_OPEN_BLUE_MODEL = "meta-models/muse-spark-open-1026"
MODEL_OPEN_BLUE_BASE_URL = "https://inference.baseten.co/v1"
MODEL_OPEN_BLUE_KEY_ENV = "BASETEN_API_KEY"
MODEL_OPEN_RUN_CAP_USD = 2.0

# Fallback estimate for one model exercise when no spend history exists.
DEFAULT_EXERCISE_ESTIMATE_USD = 0.35

app = FastAPI(title="Crucible Operations Center")
app.include_router(voice_router)


def _runs_dir() -> Path:
    """Return the runs directory (indirect so tests can redirect it)."""
    return events.RUNS_DIR


def _resolve_run(run_id: str) -> Path:
    """Map a run id to its jsonl file, rejecting traversal attempts."""
    if not _RUN_ID_RE.match(run_id or ""):
        raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")
    target = (_runs_dir() / f"{run_id}.jsonl").resolve()
    try:
        target.relative_to(_runs_dir().resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")
    if target.name in SKIP_FILES or not target.is_file():
        raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")
    return target


def _read_run_events(run_id: str) -> list:
    """Read one run's events, raising 404/422 on missing or broken logs."""
    try:
        found = events.read_events(_resolve_run(run_id))
    except HTTPException:
        raise
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=422, detail=f"run {run_id!r} is unreadable: {exc}"
        )
    return found


def _score_or_none(event_list: list) -> dict | None:
    """Score events, returning None when scoring cannot proceed."""
    try:
        return orchestrator.score_events(event_list)
    except Exception:
        return None


def _estimate_exercise_cost() -> float:
    """Estimate one model exercise from spend history, else a default."""
    try:
        lines = llm.SPEND_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return DEFAULT_EXERCISE_ESTIMATE_USD
    per_exercise: dict[str, float] = {}
    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        name = entry.get("exercise_id")
        if not name:
            continue
        try:
            per_exercise[name] = per_exercise.get(name, 0.0) + float(
                entry.get("cost_usd", 0.0)
            )
        except (TypeError, ValueError):
            continue
    costs = [cost for cost in per_exercise.values() if cost > 0]
    if not costs:
        return DEFAULT_EXERCISE_ESTIMATE_USD
    return round(sum(costs) / len(costs), 2)


def _spend_cap_hit(event_list: list) -> bool:
    """Check whether the spend cap blocked any turn of an exercise."""
    return any(
        SPEND_CAP_MARKER in str(event.get("reasoning_summary", "")).lower()
        for event in (event_list or [])
    )


def _spend_line_count() -> int:
    """Count spend log lines so one exercise can measure its own cost."""
    try:
        text = llm.SPEND_FILE.read_text(encoding="utf-8")
    except OSError:
        return 0
    return len([line for line in text.splitlines() if line.strip()])


def _cost_since(start_lines: int) -> float:
    """Sum cost_usd for spend entries appended after a snapshot."""
    try:
        lines = llm.SPEND_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0.0
    total = 0.0
    for line in [entry for entry in lines if entry.strip()][start_lines:]:
        try:
            total += float(json.loads(line).get("cost_usd", 0.0))
        except (ValueError, TypeError, AttributeError):
            continue
    return total


def _cap_warning_text() -> str:
    """Render the spend cap warning with current usage and limit."""
    try:
        limit = float(llm.daily_limit_usd())
    except (TypeError, ValueError):
        limit = float(llm.DEFAULT_DAILY_LIMIT_USD)
    try:
        used = float(llm.spent_today())
    except (TypeError, ValueError, OSError):
        used = 0.0
    return (
        "Daily model spend cap reached: "
        f"${used:.2f} of ${limit:.2f} used; "
        "turns fell back to the scripted baseline, not model calls."
    )


class ExerciseStart(BaseModel):
    agents: str = Field(
        description='Agent set: "scripted", "model", "human", '
        '"ai_with_override", or "model_open" (briefed red plus '
        "open-weights blue)"
    )
    exercise_id: str | None = Field(
        default=None, description="Optional name for the jsonl log under runs/"
    )
    scenario: str | None = Field(
        default=None,
        description="Scenario token: 'classic' (default) or 'gen:<seed>:<difficulty>'",
    )


# Live exercise state. Guarded by _state_lock; only one runs at a time.
_state_lock = threading.Lock()
_live_state: dict[str, Any] = {
    "running": False,
    "exercise_id": None,
    "agents": None,
    "scenario": None,
    "run_id": None,
    "started_at": None,
    "spend_at_start": 0.0,
    "warning": None,
    "last_outcome": None,
    "last_exercise_cost": 0.0,
    "last_warning": None,
}
_live_buffer: deque = deque(maxlen=1000)
_live_subscribers: list[queue.Queue] = []


def _publish(message: dict) -> None:
    """Append a live message to the buffer and every subscriber queue."""
    with _state_lock:
        _live_buffer.append(message)
        targets = list(_live_subscribers)
    for target in targets:
        try:
            target.put_nowait(message)
        except queue.Full:
            continue


def _snapshot_status() -> dict:
    """Build the status payload from current state and spend figures."""
    with _state_lock:
        state = dict(_live_state)
    try:
        spent = float(llm.spent_today())
    except (TypeError, ValueError, OSError):
        spent = 0.0
    try:
        limit = float(llm.daily_limit_usd())
    except (TypeError, ValueError):
        limit = float(llm.DEFAULT_DAILY_LIMIT_USD)
    live_cost = 0.0
    if state["running"]:
        live_cost = max(0.0, spent - float(state.get("spend_at_start") or 0.0))
    return {
        "running": state["running"],
        "exercise_id": state["exercise_id"],
        "agents": state["agents"],
        "scenario": state.get("scenario"),
        "run_id": state["run_id"],
        "started_at": state["started_at"],
        "spent_today": round(spent, 4),
        "daily_limit": limit,
        "live_exercise_cost": round(live_cost, 4),
        "estimated_exercise_cost": _estimate_exercise_cost(),
        "warning": state["warning"],
        "last_outcome": state["last_outcome"],
        "last_exercise_cost": state["last_exercise_cost"],
        "last_warning": state["last_warning"],
    }


class _LiveWriter(events.EventWriter):
    """Event writer that also publishes each event to live subscribers."""

    def log(self, **kwargs) -> dict:
        event = super().log(**kwargs)
        _publish({"type": "event", "event": event})
        return event


def _build_agents(agents: str, exercise_id: str) -> tuple:
    """Build one exercise's red/blue pair.

    Every live blue takes defender orders: scripted and model blue
    run inside the override wrapper (which delegates untouched while
    nothing is queued), human mode waits on orders entirely, and
    ai_with_override pairs scripted red with model blue plus orders.
    model_open pairs briefed model red with open-weights model blue
    on Baseten. The shared command queue binds for the voice routes
    in all modes.
    """
    cmd_queue = CommandQueue()
    voice_api.attach_queue(cmd_queue, exercise_id, agents)
    if agents == "model":
        return (ModelRed(exercise_id=exercise_id),
                AIWithOverride(ModelBlue(exercise_id=exercise_id),
                               cmd_queue, exercise_id=exercise_id))
    if agents == "human":
        return ScriptedRed(), HumanBlue(cmd_queue,
                                        exercise_id=exercise_id)
    if agents == "ai_with_override":
        return (ScriptedRed(),
                AIWithOverride(ModelBlue(exercise_id=exercise_id),
                               cmd_queue, exercise_id=exercise_id))
    if agents == "model_open":
        blue_client = llm.custom_client(MODEL_OPEN_BLUE_BASE_URL,
                                        MODEL_OPEN_BLUE_KEY_ENV)
        return (ModelBriefedRed(exercise_id=exercise_id),
                AIWithOverride(ModelBlue(exercise_id=exercise_id,
                                         client=blue_client,
                                         model=MODEL_OPEN_BLUE_MODEL),
                               cmd_queue, exercise_id=exercise_id))
    return ScriptedRed(), AIWithOverride(ScriptedBlue(), cmd_queue,
                                         exercise_id=exercise_id)


def _run_live_exercise(
    agents: str, exercise_id: str, run_path: Path, publish,
    scenario_key="classic",
) -> dict:
    """Run one exercise through the orchestrator, publishing live events.

    Mirrors run_exercise.main: activates the scenario, boots the
    sandbox, runs the turn loop with scripted, model, or human-driven
    agents, then stops the range. Each event reaches subscribers through the
    patched writer, including live auditor verdicts on every blue
    action. Tests replace this function with a fake runner;
    production always uses this real one.
    """
    try:
        info = stack.activate(scenario_key)
    except ValueError as exc:
        raise RuntimeError(f"scenario activation failed: {exc}") from exc
    # Agents after activation: ScriptedRed snapshots the active spec
    # at construction, like run_exercise and batch already do.
    red, blue = _build_agents(agents, exercise_id)
    publish({"type": "status", "phase": "booting", "exercise_id": exercise_id})
    try:
        sandbox.start()
    except sandbox.SandboxError as exc:
        raise RuntimeError(f"sandbox boot failed: {exc}") from exc

    spend_start = _spend_line_count()
    original_writer = events.EventWriter
    events.EventWriter = _LiveWriter  # type: ignore[assignment]
    try:
        for host in sandbox.active_hosts():
            tools.restore_host(host)
        extra = {"scenario": info} if info else {}
        result = orchestrator.run_exercise(
            red, blue, max_turns=40,  # live episodes keep the classic length
            run_path=run_path, exercise_id=exercise_id,
            audit="live",
            # Live runs never replay cached audits: exercise ids can
            # repeat (user-supplied or minute-stamp defaults), so a
            # cache hit would judge this run with another run's words.
            auditor_use_cache=False,
            **extra,
        )
    finally:
        events.EventWriter = original_writer  # type: ignore[assignment]
        try:
            sandbox.stop()
        except sandbox.SandboxError as exc:
            publish({"type": "note", "text": f"sandbox stop noted: {exc}"})
        tools.reset_state()
        stack.deactivate()
        voice_api.detach_queue()
    result["exercise_cost"] = _cost_since(spend_start)
    result["spend_cap_hit"] = _spend_cap_hit(result.get("events", []))
    return result


def _exercise_thread(
    agents: str, exercise_id: str, run_path: Path, scenario_key="classic",
) -> None:
    """Background body for one exercise: run, score, publish the outcome."""
    try:
        try:
            result = _run_live_exercise(
                agents, exercise_id, run_path, _publish, scenario_key)
        finally:
            llm.clear_run_budget()
    except Exception as exc:  # noqa: BLE001 - failure must reach the UI
        with _state_lock:
            _live_state["running"] = False
            _live_state["warning"] = None
        _publish({"type": "error", "text": str(exc)[:500]})
        return
    score = result.get("score") or _score_or_none(result.get("events", [])) or {}
    warning = _cap_warning_text() if result.get("spend_cap_hit") else None
    if warning:
        print(f"WARNING: {warning}", file=sys.stderr)
    with _state_lock:
        _live_state["running"] = False
        _live_state["warning"] = None
        _live_state["last_outcome"] = score.get("outcome")
        _live_state["last_exercise_cost"] = round(
            float(result.get("exercise_cost", 0.0) or 0.0), 4
        )
        _live_state["last_warning"] = warning
    _publish(
        {
            "type": "done",
            "exercise_id": exercise_id,
            "run_id": run_path.stem,
            "score": score,
            "exercise_cost": _live_state["last_exercise_cost"],
            "spend_cap_warning": warning,
        }
    )


@app.get("/api/runs")
def list_runs() -> dict:
    """List finished runs with outcome and score when computable."""
    runs = []
    directory = _runs_dir()
    try:
        files = sorted(directory.glob("*.jsonl"))
    except OSError:
        files = []
    for path in files:
        if path.name in SKIP_FILES:
            continue
        run_id = path.stem
        entry: dict[str, Any] = {
            "id": run_id,
            "events": 0,
            "outcome": None,
            "blue_score": None,
            "justified_pct": None,
            "unsupported_count": 0,
            "invalid_refs_count": 0,
            "mean_audit_score": None,
            "mtime": None,
        }
        try:
            entry["mtime"] = datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc
            ).isoformat()
        except OSError:
            entry["mtime"] = None
        try:
            found = events.read_events(path)
        except (OSError, ValueError):
            runs.append(entry)
            continue
        entry["events"] = len(found)
        score = _score_or_none(found)
        if score is not None:
            entry["outcome"] = score.get("outcome")
            entry["blue_score"] = score.get("blue_score")
            entry["justified_pct"] = score.get("justified_pct")
            entry["unsupported_count"] = score.get("unsupported_count", 0)
            entry["invalid_refs_count"] = score.get("invalid_refs_count", 0)
            entry["mean_audit_score"] = score.get("mean_audit_score")
        runs.append(entry)
    runs.sort(key=lambda item: item["mtime"] or "", reverse=True)
    return {"runs": runs}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    """Return one run's events plus its score when computable."""
    found = _read_run_events(run_id)
    return {"id": run_id, "events": found, "score": _score_or_none(found)}


@app.get("/api/runs/{run_id}/debrief")
def get_debrief(
    run_id: str, rewrite: int = Query(default=0)
) -> dict:
    """Return one run's exercise debrief report.

    Layer 1 is deterministic and needs no model. rewrite=1 asks Muse
    Wonder for a plain-language rewrite, falling back to Layer 1 on
    any mismatch or error, including a missing model key.
    """
    found = _read_run_events(run_id)
    try:
        report = debrief.build_debrief(found, run_id=run_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if rewrite:
        report = debrief.rewrite_plain(
            report, found, exercise_id=f"debrief-{run_id}"
        )
    return {"id": run_id, "debrief": report}


@app.get("/api/stream/{run_id}")
def stream_run(
    run_id: str, speed: float = Query(default=1.0)
) -> StreamingResponse:
    """Replay a finished run over server-sent events.

    Speed 1 sends one event per 1.2 seconds; higher values go faster.
    """
    found = _read_run_events(run_id)
    score = _score_or_none(found)
    try:
        pace = float(speed)
    except (TypeError, ValueError):
        pace = 1.0
    pace = min(50.0, max(0.25, pace))
    interval = REPLAY_INTERVAL_S / pace
    total = len(found)

    def generate():
        for index, event in enumerate(found, start=1):
            payload = json.dumps({"index": index, "total": total, "event": event})
            yield f"data: {payload}\n\n"
            time.sleep(interval)
        yield f"data: {json.dumps({'done': True, 'score': score})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/api/live")
def live_events() -> StreamingResponse:
    """Stream the running exercise, then close when it ends.

    New subscribers first receive everything buffered for the current
    (or just finished) run, then follow live until the done marker.
    """

    def generate():
        inbox: queue.Queue = queue.Queue(maxsize=1000)
        with _state_lock:
            backlog = list(_live_buffer)
            running = _live_state["running"]
            _live_subscribers.append(inbox)
        try:
            for message in backlog:
                yield f"data: {json.dumps(message)}\n\n"
                if message.get("type") in ("done", "error"):
                    return
            if not running and not backlog:
                yield f"data: {json.dumps({'type': 'idle'})}\n\n"
                return
            if not running:
                return
            while True:
                try:
                    message = inbox.get(timeout=15)
                except queue.Empty:
                    yield ": ping\n\n"
                    continue
                yield f"data: {json.dumps(message)}\n\n"
                if message.get("type") in ("done", "error"):
                    return
        finally:
            with _state_lock:
                if inbox in _live_subscribers:
                    _live_subscribers.remove(inbox)

    return StreamingResponse(generate(), media_type="text/event-stream")


def _preflight_model_open() -> None:
    """Check model keys before the range boots, then arm the run cap.

    Raises 409 naming the missing keys (never their values) so the
    lobby can say so and Docker never starts.
    """
    llm.refresh_env()
    missing = [name for name in ("MODEL_API_KEY", MODEL_OPEN_BLUE_KEY_ENV)
               if not (os.environ.get(name) or "").strip()]
    if missing:
        raise HTTPException(
            status_code=409,
            detail=f"model keys missing ({', '.join(missing)}): add them "
                   f"to .env (never paste keys into chat) and retry; "
                   f"the range was not booted",
        )
    llm.set_run_budget(llm.spent_today(), MODEL_OPEN_RUN_CAP_USD)


@app.post("/api/exercise/start", status_code=202)
def start_exercise(body: ExerciseStart) -> dict:
    """Start one exercise in a background thread (only one at a time)."""
    agents = (body.agents or "").strip().lower()
    if agents not in ("scripted", "model", "human", "ai_with_override",
                      "model_open"):
        raise HTTPException(
            status_code=422,
            detail='agents must be "scripted", "model", "human", '
            '"ai_with_override", or "model_open"',
        )
    if agents == "model_open":
        _preflight_model_open()
    try:
        scenario_key = select.parse_token((body.scenario or "").strip() or "classic")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    with _state_lock:
        if _live_state["running"]:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "an exercise is already running",
                    "exercise_id": _live_state["exercise_id"],
                },
            )
    exercise_id = (body.exercise_id or "").strip()
    if exercise_id:
        if not _EXERCISE_ID_RE.match(exercise_id):
            raise HTTPException(
                status_code=422,
                detail="exercise_id may only hold letters, digits, dot, dash, underscore",
            )
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M")
        exercise_id = f"exercise-{stamp}"
        stem = exercise_id
        suffix = 2
        while (_runs_dir() / f"{exercise_id}.jsonl").exists():
            exercise_id = f"{stem}-{suffix}"
            suffix += 1

    warning = None
    if agents in ("model", "ai_with_override", "model_open"):
        try:
            llm.check_spend_guard()
        except llm.SpendLimitError:
            warning = _cap_warning_text()

    run_path = events.new_run_path(exercise_id)
    # Keep the new log inside runs/ even if the id was odd.
    run_path = (_runs_dir() / run_path.name).resolve()
    try:
        run_path.relative_to(_runs_dir().resolve())
    except ValueError:
        raise HTTPException(status_code=422, detail="exercise_id is not usable")

    with _state_lock:
        _live_state.update(
            {
                "running": True,
                "exercise_id": exercise_id,
                "agents": agents,
                "scenario": select.label(scenario_key),
                "run_id": run_path.stem,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "warning": warning,
            }
        )
        try:
            _live_state["spend_at_start"] = float(llm.spent_today())
        except (TypeError, ValueError, OSError):
            _live_state["spend_at_start"] = 0.0
        _live_buffer.clear()
    _publish(
        {
            "type": "status",
            "phase": "starting",
            "exercise_id": exercise_id,
            "agents": agents,
            "run_id": run_path.stem,
            "warning": warning,
        }
    )
    worker = threading.Thread(
        target=_exercise_thread,
        args=(agents, exercise_id, run_path, scenario_key),
        daemon=True,
        name=f"crucible-exercise-{exercise_id}",
    )
    worker.start()
    return {
        "exercise_id": exercise_id,
        "run_id": run_path.stem,
        "agents": agents,
        "scenario": select.label(scenario_key),
        "warning": warning,
        "live": "/api/live",
    }


@app.get("/api/scenario/preview")
def scenario_preview(scenario: str = Query(default="classic")) -> dict:
    """Preview a generated scenario's blue-visible structure.

    The lobby deals an unseen network before starting a live
    exercise; this returns its identity and range layout (hosts,
    roles, segments, links, weights, crown jewel host) with no
    vulnerabilities, accounts, or paths. Generated tokens only.
    """
    try:
        key = select.parse_token(scenario)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if select.is_classic(key):
        raise HTTPException(
            status_code=422, detail="preview needs a 'gen:<seed>:<difficulty>' token"
        )
    _kind, seed, difficulty = key
    spec = generate.generate(seed, difficulty)
    layout = sandbox.layout_for_spec(spec)
    return {
        "scenario": select.label(key),
        "scenario_id": spec["scenario_id"],
        "seed": seed,
        "difficulty": difficulty,
        "host_count": len(layout["hosts"]),
        "layout": layout,
    }


@app.get("/api/status")
def get_status() -> dict:
    """Report whether an exercise is running plus spend figures."""
    return _snapshot_status()


@app.get("/api/campaigns")
def list_campaigns() -> dict:
    """List learning campaigns with episode counts and mean score."""
    found = []
    try:
        paths = sorted(_runs_dir().glob("campaign-*/summary.json"))
    except OSError:
        paths = []
    for path in paths:
        name = path.parent.name[len("campaign-") :]
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(state, dict):
            continue
        episodes = state.get("episodes", []) or []
        scores = [row.get("blue_score") for row in episodes
                  if isinstance(row.get("blue_score"), (int, float))]
        found.append(
            {
                "name": name,
                "memory": state.get("memory"),
                "agents": state.get("agents"),
                "episodes": len(episodes),
                "mean_blue_score": round(sum(scores) / len(scores), 2) if scores else None,
            }
        )
    return {"campaigns": found}


@app.get("/api/campaigns/{name}")
def get_campaign(name: str) -> dict:
    """Return one campaign's episodes plus its current playbook rules."""
    if not _RUN_ID_RE.match(name or ""):
        raise HTTPException(status_code=404, detail=f"unknown campaign {name!r}")
    directory = (_runs_dir() / f"campaign-{name}").resolve()
    try:
        directory.relative_to(_runs_dir().resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail=f"unknown campaign {name!r}")
    summary_path = directory / "summary.json"
    if not summary_path.is_file():
        raise HTTPException(status_code=404, detail=f"unknown campaign {name!r}")
    try:
        state = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=422, detail=f"campaign {name!r} is unreadable: {exc}")
    rules = []
    try:
        saved = json.loads((directory / "playbook.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    if isinstance(saved, dict):
        for entry in saved.get("rules", []) or []:
            if isinstance(entry, dict) and entry.get("rule"):
                rules.append({"rule": entry["rule"],
                              "episode": entry.get("episode", "")})
    return {
        "name": name,
        "memory": (state or {}).get("memory"),
        "agents": (state or {}).get("agents"),
        "episodes": (state or {}).get("episodes", []) or [],
        "playbook": rules,
    }


_STAGE3_VOID_NOTE = (
    "The sensors-on half of the first held-out run is void because of a "
    "collector bug, so it is not shown. Sensors-on numbers come from the "
    "corrected rerun (runs/bench-stage3-heldout-sensors); sensors-off numbers "
    "come from the valid half of the first run (runs/bench-stage3-heldout)."
)


def _stage3_episodes(name: str) -> list | None:
    """Read one bench summary's episodes, or None when missing."""
    try:
        state = json.loads((_runs_dir() / name / "summary.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict):
        return None
    episodes = state.get("episodes", [])
    return episodes if isinstance(episodes, list) else None


def _stage3_cell(rows: list) -> dict:
    return {
        "n": len(rows),
        "contained": sum(1 for row in rows if row.get("contained_after_intrusion") is True),
        "secretRead": sum(1 for row in rows if row.get("secret_read") is True),
    }


def _stage3_pairs(on_rows: list, off_rows: list) -> dict:
    """Discordant counts over scenarios present in both arms."""
    on = {row.get("scenario"): row for row in on_rows if row.get("scenario")}
    off = {row.get("scenario"): row for row in off_rows if row.get("scenario")}
    both = sorted(set(on) & set(off))
    contained = {"b": 0, "c": 0}
    secret = {"b": 0, "c": 0}
    for scenario in both:
        if bool(on[scenario].get("contained_after_intrusion")) != bool(
            off[scenario].get("contained_after_intrusion")
        ):
            slot = "b" if on[scenario].get("contained_after_intrusion") else "c"
            contained[slot] += 1
        if bool(on[scenario].get("secret_read")) != bool(off[scenario].get("secret_read")):
            slot = "b" if on[scenario].get("secret_read") else "c"
            secret[slot] += 1
    return {"n": len(both), "contained": contained, "secret": secret}


def _stage3_section(name: str, sensors: str, rerun: bool) -> dict | None:
    episodes = _stage3_episodes(name)
    if episodes is None:
        return None
    on_rows = [row for row in episodes
               if row.get("sensors") == sensors and row.get("memory") == "on"]
    off_rows = [row for row in episodes
                if row.get("sensors") == sensors and row.get("memory") == "off"]
    if not on_rows and not off_rows:
        return None
    return {
        "source": f"runs/{name}/summary.json",
        "rerun": rerun,
        "on": _stage3_cell(on_rows),
        "off": _stage3_cell(off_rows),
        "pairs": _stage3_pairs(on_rows, off_rows),
    }


def _stage3_playbook() -> dict | None:
    try:
        saved = json.loads(
            (_runs_dir() / "bench-stage3-heldout" / "playbooks"
             / "solver-sensors-on-memory-on.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    if not isinstance(saved, dict):
        return None
    rules = [
        {"rule": entry["rule"], "episode": entry.get("episode", "")}
        for entry in saved.get("rules", []) or []
        if isinstance(entry, dict) and entry.get("rule")
    ]
    if not rules:
        return None
    return {
        "source": "runs/bench-stage3-heldout/playbooks/solver-sensors-on-memory-on.json",
        "frozen": True,
        "rules": rules,
    }


@app.get("/api/bench/stage3")
def get_stage3() -> dict:
    """Stage 3 memory experiment: honest per-cell counts plus the frozen playbook."""
    return {
        "sensorsOn": _stage3_section("bench-stage3-heldout-sensors", "on", True),
        "sensorsOff": _stage3_section("bench-stage3-heldout", "off", False),
        "voidNote": _STAGE3_VOID_NOTE,
        "playbook": _stage3_playbook(),
    }


@app.get("/api/config")
def get_config() -> dict:
    """Return dashboard config: hosts, business weights, spend limit."""
    try:
        scenario = sandbox.load_scenario()
    except Exception:
        scenario = {}
    weights = scenario.get("business_weights") or dict(
        orchestrator.DEFAULT_BUSINESS_WEIGHTS
    )
    hosts = list(scenario.get("hosts") or ("web", "app", "db", "workstation"))
    try:
        limit = float(llm.daily_limit_usd())
    except (TypeError, ValueError):
        limit = float(llm.DEFAULT_DAILY_LIMIT_USD)
    return {
        "hosts": [host for host in hosts if host in weights],
        "business_weights": {host: int(weights.get(host, 0)) for host in hosts},
        "daily_limit": limit,
    }


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """Serve the dashboard page."""
    return FileResponse(DASHBOARD_DIR / "index.html")


# Static dashboard assets only. This mount cannot reach runs/, .env, or
# any other server file: it serves the dashboard directory and nothing else.
app.mount("/", StaticFiles(directory=DASHBOARD_DIR, html=True), name="dashboard")
