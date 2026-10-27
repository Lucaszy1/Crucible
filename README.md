# Crucible

An autonomous security validation range for testing defensive AI agents safely.

Crucible simulates a small company network and lets AI agents probe it and protect it, so teams can measure how well automated defenders detect and contain threats without touching a real system. Everything runs in a sandboxed simulation. No real exploitation, no real targets.

Built during the Meta Global AI Developer Hackathon, Oct 26 to Nov 4, 2026, using Meta's Muse models through the Meta Model API.

Status: under active development.

## Operations Center

The dashboard watches exercises live and replays past runs. It needs no
network access beyond localhost and has no build step.

```
pip install -r requirements.txt
python -m crucible.serve
```

Open http://127.0.0.1:8000 in a browser. Use the control bar to run a
scripted exercise, run a metered model exercise, or replay a past run
from the list. The server binds to 127.0.0.1 only.

Verify the backend and frontend logic without Docker:

```
python -m pytest tests/test_api.py -q
node --test tests/test_host_state.mjs
```
