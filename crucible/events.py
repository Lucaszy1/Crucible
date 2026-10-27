"""Event schema and jsonl writer for exercise replay.

Every tool call during an exercise becomes one event with the fields
listed in the build spec: id, timestamp, actor, tool, args, result,
reasoning_summary, evidence_refs. The orchestrator appends each event
to a jsonl file under runs/ as the exercise proceeds.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "runs"

REQUIRED_FIELDS = (
    "id",
    "timestamp",
    "actor",
    "tool",
    "args",
    "result",
    "reasoning_summary",
    "evidence_refs",
)


def utc_now_iso() -> str:
    """Return the current UTC time in ISO format."""
    return datetime.now(timezone.utc).isoformat()


def sanitize_reasoning_summary(text: str) -> str:
    """Replace em dashes and en dashes with commas for clean logs."""
    cleaned = str(text or "")
    cleaned = cleaned.replace(" — ", ", ").replace("—", ",")
    cleaned = cleaned.replace(" – ", ", ").replace("–", ",")
    return cleaned


def new_event(
    *,
    actor: str,
    tool: str,
    args: dict,
    result: dict,
    reasoning_summary: str = "",
    evidence_refs: list | None = None,
    event_id: str | None = None,
    timestamp: str | None = None,
) -> dict:
    """Build one event dict with the spec fields."""
    return {
        "id": event_id or "evt-0000",
        "timestamp": timestamp or utc_now_iso(),
        "actor": actor,
        "tool": tool,
        "args": dict(args or {}),
        "result": dict(result or {}),
        "reasoning_summary": sanitize_reasoning_summary(reasoning_summary or ""),
        "evidence_refs": list(evidence_refs or []),
    }


def validate_event(event: dict) -> dict:
    """Check that an event carries all spec fields, then return it."""
    missing = [key for key in REQUIRED_FIELDS if key not in event]
    if missing:
        raise ValueError(f"event is missing fields: {', '.join(missing)}")
    if not isinstance(event["evidence_refs"], list):
        raise ValueError("event evidence_refs must be a list")
    return event


def new_run_path(exercise_id: str | None = None) -> Path:
    """Return a fresh jsonl path under runs/ for one exercise."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = exercise_id or f"exercise-{stamp}"
    if not name.endswith(".jsonl"):
        name += ".jsonl"
    return RUNS_DIR / name


def append_event(path: str | Path, event: dict) -> dict:
    """Validate one event and append it to a jsonl file."""
    validate_event(event)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")
    return event


def read_events(path: str | Path) -> list:
    """Read a jsonl exercise log back into a list of events."""
    target = Path(path)
    events = []
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))
    return events


class EventWriter:
    """Sequential writer that numbers events as they land.

    Refuses an existing log file unless resume or overwrite is set,
    so reusing an exercise id cannot silently append a second game.
    Resume continues numbering after the stored events; overwrite
    truncates the file first.
    """

    def __init__(self, path: str | Path | None = None, *,
                 resume: bool = False, overwrite: bool = False):
        self.path = Path(path) if path else new_run_path()
        self.events: list = []
        if self.path.exists():
            if resume:
                self.events = read_events(self.path)
            elif overwrite:
                self.path.unlink()
            else:
                raise FileExistsError(
                    f"log {self.path} already exists; pass resume=True to "
                    "continue it or overwrite=True to replace it"
                )
        self.count = len(self.events)

    def log(
        self,
        *,
        actor: str,
        tool: str,
        args: dict,
        result: dict,
        reasoning_summary: str = "",
        evidence_refs: list | None = None,
    ) -> dict:
        """Build, store, and persist the next numbered event."""
        self.count += 1
        event = new_event(
            actor=actor,
            tool=tool,
            args=args,
            result=result,
            reasoning_summary=reasoning_summary,
            evidence_refs=evidence_refs,
            event_id=f"evt-{self.count:04d}",
        )
        self.events.append(event)
        append_event(self.path, event)
        return event
