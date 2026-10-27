"""FastAPI backend for the Crucible Operations Center dashboard.

Serves the static dashboard page plus a small JSON and server-sent
events API over finished runs and live exercises. Runs are the jsonl
files under runs/ (spend.jsonl excluded). Only one exercise runs at a
time; live events stream over GET /api/live while it proceeds.
"""

from __future__ import annotations

import json
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

from crucible import events, llm, orchestrator, sandbox, tools
from crucible.agents import ModelBlue, ModelRed, ScriptedBlue, ScriptedRed
from crucible.run_exercise import SPEND_CAP_MARKER

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

# Fallback estimate for one model exercise when no spend history exists.
DEFAULT_EXERCISE_ESTIMATE_USD = 0.35

app = FastAPI(title="Crucible Operations Center")


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
    agents: str = Field(description='Agent set: "scripted" or "model"')
    exercise_id: str | None = Field(
        default=None, description="Optional name for the jsonl log under runs/"
    )


# Live exercise state. Guarded by _state_lock; only one runs at a time.
_state_lock = threading.Lock()
_live_state: dict[str, Any] = {
    "running": False,
    "exercise_id": None,
    "agents": None,
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


def _run_live_exercise(
    agents: str, exercise_id: str, run_path: Path, publish
) -> dict:
    """Run one exercise through the orchestrator, publishing live events.

    Mirrors run_exercise.main: boots the sandbox, runs the turn loop with
    scripted or model agents, then stops the range. Each event reaches
    subscribers through the patched writer. Tests replace this function
    with a fake runner; production always uses this real one.
    """
    if agents == "model":
        red = ModelRed(exercise_id=exercise_id)
        blue = ModelBlue(exercise_id=exercise_id)
    else:
        red = ScriptedRed()
        blue = ScriptedBlue()

    publish({"type": "status", "phase": "booting", "exercise_id": exercise_id})
    try:
        sandbox.start()
    except sandbox.SandboxError as exc:
        raise RuntimeError(f"sandbox boot failed: {exc}") from exc

    spend_start = _spend_line_count()
    original_writer = events.EventWriter
    events.EventWriter = _LiveWriter  # type: ignore[assignment]
    try:
        tools.restore_host("web")
        tools.restore_host("app")
        tools.restore_host("db")
        result = orchestrator.run_exercise(
            red, blue, max_turns=orchestrator.MAX_TURNS,
            run_path=run_path, exercise_id=exercise_id,
        )
    finally:
        events.EventWriter = original_writer  # type: ignore[assignment]
        try:
            sandbox.stop()
        except sandbox.SandboxError as exc:
            publish({"type": "note", "text": f"sandbox stop noted: {exc}"})
        tools.reset_state()
    result["exercise_cost"] = _cost_since(spend_start)
    result["spend_cap_hit"] = _spend_cap_hit(result.get("events", []))
    return result


def _exercise_thread(agents: str, exercise_id: str, run_path: Path) -> None:
    """Background body for one exercise: run, score, publish the outcome."""
    try:
        result = _run_live_exercise(agents, exercise_id, run_path, _publish)
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
        runs.append(entry)
    runs.sort(key=lambda item: item["mtime"] or "", reverse=True)
    return {"runs": runs}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    """Return one run's events plus its score when computable."""
    found = _read_run_events(run_id)
    return {"id": run_id, "events": found, "score": _score_or_none(found)}


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


@app.post("/api/exercise/start", status_code=202)
def start_exercise(body: ExerciseStart) -> dict:
    """Start one exercise in a background thread (only one at a time)."""
    agents = (body.agents or "").strip().lower()
    if agents not in ("scripted", "model"):
        raise HTTPException(
            status_code=422, detail='agents must be "scripted" or "model"'
        )
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
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        exercise_id = f"exercise-{stamp}"

    warning = None
    if agents == "model":
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
        args=(agents, exercise_id, run_path),
        daemon=True,
        name=f"crucible-exercise-{exercise_id}",
    )
    worker.start()
    return {
        "exercise_id": exercise_id,
        "run_id": run_path.stem,
        "agents": agents,
        "warning": warning,
        "live": "/api/live",
    }


@app.get("/api/status")
def get_status() -> dict:
    """Report whether an exercise is running plus spend figures."""
    return _snapshot_status()


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
