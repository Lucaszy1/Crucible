"""Voice and takeover HTTP routes for the Operations Center.

Additive routes only; included by crucible.api. All state lives in
this module (the live command queue plus rate buckets) so the core
API stays untouched. Localhost-only binding comes from serve.py.

Routes (exact JSON in docs/VOICE_API.md):

- POST /api/command       queue one typed order for the live exercise
- POST /api/command/demo  queue one scripted demo transcript (offline)
- POST /api/audio         transcribe WAV, queue it as a voice order
- GET  /api/command/state pending count plus last order result
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from crucible import llm, voice
from crucible.human_blue import DEFAULT_DEMO_TRANSCRIPTS, CommandQueue

router = APIRouter()

MAX_TEXT_CHARS = 500
MAX_UPLOAD_BYTES = voice.MAX_WAV_BYTES + 1024
AUDIO_CONTENT_TYPES = ("audio/wav", "audio/x-wav")

# Per-route token buckets: (max calls, window seconds). Single-user
# localhost traffic; the dashboard polls state, which stays unlimited.
_RATE_CAPS = {
    "command": (30, 60.0),
    "demo": (30, 60.0),
    "audio": (6, 60.0),
}

_lock = threading.Lock()
_queue: CommandQueue | None = None
_exercise_id: str | None = None
_demo_index = 0
_buckets: dict = {name: deque() for name in _RATE_CAPS}


class CommandIn(BaseModel):
    text: str = Field(description="Judge order, e.g. 'isolate the database'")
    source: str = Field(default="text",
                        description="'text' or 'voice' (transcript origin)")


class DemoIn(BaseModel):
    index: int | None = Field(
        default=None,
        description="Demo transcript index; default advances in order",
    )


def attach_queue(cmd_queue: CommandQueue, exercise_id: str) -> None:
    """Bind the live exercise's queue for the command routes."""
    global _queue, _exercise_id, _demo_index
    with _lock:
        _queue = cmd_queue
        _exercise_id = exercise_id
        _demo_index = 0


def detach_queue() -> None:
    """Release the live queue when the exercise ends."""
    global _queue, _exercise_id
    with _lock:
        _queue = None
        _exercise_id = None


def reset_for_tests() -> None:
    """Clear queue and rate buckets (tests only)."""
    global _demo_index
    detach_queue()
    with _lock:
        _demo_index = 0
        for bucket in _buckets.values():
            bucket.clear()


def _check_rate(name: str) -> None:
    cap, window = _RATE_CAPS[name]
    now = time.monotonic()
    with _lock:
        bucket = _buckets[name]
        while bucket and now - bucket[0] > window:
            bucket.popleft()
        if len(bucket) >= cap:
            raise HTTPException(status_code=429,
                                detail=f"{name} rate limit: {cap} per "
                                       f"{window:.0f}s; try again shortly")
        bucket.append(now)


def _live_queue() -> CommandQueue:
    with _lock:
        cmd_queue = _queue
    if cmd_queue is None:
        raise HTTPException(
            status_code=409,
            detail="no live human exercise: start one with agents "
                   "'human' or 'ai_with_override' first")
    return cmd_queue


@router.post("/api/command")
def post_command(body: CommandIn) -> dict:
    """Queue one typed order for the live human exercise."""
    _check_rate("command")
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="text must not be empty")
    if len(text) > MAX_TEXT_CHARS:
        raise HTTPException(status_code=422, detail="text is too long")
    source = (body.source or "text").strip().lower()
    if source not in ("text", "voice"):
        raise HTTPException(status_code=422,
                            detail="source must be 'text' or 'voice'")
    cmd_queue = _live_queue()
    try:
        receipt = cmd_queue.push(text, source=source)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"id": receipt["id"], "status": "queued",
            "pending": cmd_queue.pending()}


@router.post("/api/command/demo")
def post_demo(body: DemoIn | None = None) -> dict:
    """Queue one scripted demo transcript (works fully offline)."""
    _check_rate("demo")
    global _demo_index
    cmd_queue = _live_queue()
    with _lock:
        index = _demo_index
        if body is not None and body.index is not None:
            index = body.index
        if not 0 <= index < len(DEFAULT_DEMO_TRANSCRIPTS):
            raise HTTPException(status_code=422, detail="demo index "
                                f"must be 0..{len(DEFAULT_DEMO_TRANSCRIPTS) - 1}")
        transcript = DEFAULT_DEMO_TRANSCRIPTS[index]
        _demo_index = (index + 1) % len(DEFAULT_DEMO_TRANSCRIPTS)
    receipt = cmd_queue.push(transcript, source="text")
    return {"id": receipt["id"], "status": "queued",
            "transcript": transcript, "demo_index": index,
            "pending": cmd_queue.pending()}


async def _read_capped_body(request: Request, cap: int) -> bytes:
    """Read the body, refusing anything over the cap up front."""
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > cap:
                raise HTTPException(status_code=413,
                                    detail="audio file too large")
        except ValueError:
            pass
    chunks = []
    seen = 0
    try:
        async for chunk in request.stream():
            seen += len(chunk)
            if seen > cap:
                raise HTTPException(status_code=413,
                                    detail="audio file too large")
            chunks.append(chunk)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422,
                            detail=f"could not read upload: {exc}")
    return b"".join(chunks)


@router.post("/api/audio")
async def post_audio(request: Request) -> dict:
    """Transcribe a raw WAV body and queue it as a voice order.

    The body is the WAV bytes themselves (Content-Type audio/wav),
    so no multipart parser is needed; bytes validation is the gate.
    """
    _check_rate("audio")
    cmd_queue = _live_queue()
    media = (request.headers.get("content-type") or "").split(";")[0]
    if media.strip().lower() not in AUDIO_CONTENT_TYPES:
        raise HTTPException(status_code=415,
                            detail="content-type must be audio/wav")
    raw = await _read_capped_body(request, MAX_UPLOAD_BYTES)
    try:
        facts = voice.validate_wav(raw)
    except voice.VoiceError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    with _lock:
        exercise_id = _exercise_id
    try:
        result = voice.transcribe_file(raw, exercise_id=exercise_id)
    except llm.SpendLimitError:
        raise HTTPException(status_code=429, detail="daily model spend "
                            "cap reached; typed orders still work")
    except voice.VoiceError as exc:
        if "no transcript text" in str(exc):
            raise HTTPException(status_code=422,
                                detail="no speech detected in clip")
        raise HTTPException(status_code=502, detail=str(exc))
    receipt = cmd_queue.push(result["text"], source="voice")
    return {"id": receipt["id"], "status": "queued",
            "transcript": result["text"], "seconds": facts["seconds"],
            "cost_usd": result["cost_usd"],
            "pending": cmd_queue.pending()}


@router.get("/api/command/state")
def command_state() -> dict[str, Any]:
    """Pending order count plus the last order result."""
    with _lock:
        cmd_queue = _queue
        exercise_id = _exercise_id
    if cmd_queue is None:
        return {"attached": False, "exercise_id": None, "pending": 0,
                "last": None}
    return {"attached": True, "exercise_id": exercise_id,
            "pending": cmd_queue.pending(),
            "last": cmd_queue.last_result()}
