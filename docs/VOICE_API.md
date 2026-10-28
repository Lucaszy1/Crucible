# Voice and takeover API (backend contract)

Backend for judge takeover: a judge speaks or types an order and blue
runs it as its next tool action in a live exercise. All routes serve
from the Operations Center on localhost only
(`python -m crucible.serve`, default `http://127.0.0.1:8000`).

## Start a human exercise

`POST /api/exercise/start` accepts two new agent sets beside
`scripted` and `model`:

- `human`: every blue turn waits for a judge order (30 s turn clock),
  else blue holds. Red runs its scripted plan: free and predictable.
- `ai_with_override`: model blue plays until a judge order arrives.
  The order interrupts the AI's next turn and latches human control;
  later turns wait on the queue until `hand back` returns control.

```json
// request
{"agents": "human", "exercise_id": "judge-1", "scenario": "classic"}

// response (202)
{"exercise_id": "judge-1", "run_id": "judge-1", "agents": "human",
 "scenario": "classic", "warning": null, "live": "/api/live"}
```

## Queue a typed order

`POST /api/command` — works fully offline (deterministic parser).

```json
// request
{"text": "isolate the database", "source": "text"}

// response (200)
{"id": 1, "status": "queued", "pending": 1}
```

`source` is `text` or `voice` (transcript origin); it is recorded on
the resulting event. Errors: 409 when no human exercise is live, 422
for empty/oversize text or a bad source, 429 past 30 calls/minute.

## Queue a demo transcript

`POST /api/command/demo` — feeds one fixed transcript offline, so a
demo never depends on network speech recognition.

```json
// request: {} advances in order, or {"index": 2} picks one
{}

// response (200)
{"id": 2, "status": "queued", "transcript": "isolate the database",
 "demo_index": 2, "pending": 1}
```

The fixed list (classic range):

1. `read the web logs`
2. `report probing on the web host`
3. `isolate the database`
4. `stand down`

Indexes wrap around. Errors: 409 with no live exercise, 422 for a bad
index, 429 past 30 calls/minute.

## Send audio

`POST /api/audio` — raw WAV bytes as the body
(`Content-Type: audio/wav`). Transcribes, then queues the transcript
as a `voice` order.

```json
// response (200)
{"id": 3, "status": "queued", "transcript": "stand down",
 "seconds": 1.2, "cost_usd": 0.00012, "pending": 1}
```

Caps: 5 MB body, WAV only (1-2 channels, 8/16-bit,
8-48 kHz), 60 s duration. Errors: 409 with no live exercise, 413 for
an oversize body, 422 for unreadable audio, 429 past 6 calls/minute
or past the daily spend cap, 502 when transcription fails. Typed
orders keep working when audio or the spend cap fails.

## Read pending state

`GET /api/command/state` — cheap; poll freely, no rate cap.

```json
// attached, one order waiting, last order ran isolate_host
{"attached": true, "exercise_id": "judge-1", "pending": 1,
 "last": {"id": 1, "text": "isolate the database", "source": "voice",
          "status": "ok", "tool": "isolate_host",
          "args": {"host": "db"}}}

// no live human exercise
{"attached": false, "exercise_id": null, "pending": 0, "last": null}
```

`last.status` is `ok`, `clarify` (with `message`), `refused` (with
`message`), or `handed-back`. Clarify and refused orders surface in
the log as hold turns whose reason carries the question, so the log
shows exactly what the judge heard back.

## Order language

Orders map onto the blue tool surface only: isolate, patch, restore,
read logs, report finding, status, stand down. Host names and roles
resolve against the live inventory (`the database` finds the database
role); ambiguous matches ask a clarifying question and never guess.
Anything outside the surface (including destructive verbs and ending
the exercise) is refused with a plain explanation. Examples:

- `isolate the database`
- `patch the web portal`
- `restore app`
- `read the last 100 lines on db`
- `report repeated sign-in failures on app`
- `status of the application`
- `stand down`

## Event log shape

Human turns log like AI turns, plus two fields: `source`
(`voice` or `text`) and `transcript` (the order text). Scoring, the
auditor, and replay treat them identically. Runs without the mode
log byte-identically to before (proven by golden test).

## What the dashboard tab needs to do

- Command bar: text field + send button wired to `POST /api/command`;
  show `pending` and the `last` outcome from `GET /api/command/state`
  (poll about 1 Hz while an exercise runs).
- Mic button: capture in-browser audio, encode 16 kHz mono 16-bit
  WAV under 60 s / 5 MB, POST raw bytes to `/api/audio`.
- Demo button: call `POST /api/command/demo` with `{}` to step the
  fixed list; show the returned transcript before sending the next.
- Exercise picker: offer `human` and `ai_with_override` beside the
  existing sets; surface the 409 text when no human exercise runs.
- Latency note: orders apply on blue's NEXT turn (red moves first
  each round), and hold turns still consume the 30 s clock when the
  queue is empty, so show the pending count prominently.

## Assumptions and risks

- The file endpoint shape (multipart `file` field, JSON `text`
  reply) and the realtime message shapes are assumed; no public spec
  was available. The live probe below checks the file endpoint only.
- Realtime speech (`wss://api.meta.ai/v1/asr/realtime`) is
  implemented against a fake server and has never touched the real
  endpoint.
- Audio pricing is a $0.006/minute placeholder for the spend guard;
  check the invoice for real dollars.
- `POST /api/audio` spends per call; repeated long clips can reach
  the daily cap, after which audio returns 429 and typed orders
  continue.
