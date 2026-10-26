# Crucible: Product Requirements

## What it is
An autonomous security validation range. AI agents probe and protect a small, deliberately vulnerable simulated network running in isolated Docker containers. Every match plays out live on a web dashboard, and every defender action is traceable to the exact log evidence behind it.

## Built for
Meta Global AI Developer Hackathon, Indie track, Oct 26 to Nov 4 2026. All code is new, written during the event.

## Core loop (MVP)
1. Scenario: 3 Docker containers on an isolated internal network, no internet access.
   - web: a vulnerable web app with an exposed admin endpoint
   - db: holds a fake secret the attacker wants
   - workstation: a third node for lateral movement
2. Attacker agent (Muse Spark, tool calling): scan, send HTTP requests, exploit. Goal: read the secret.
3. Defender agent (Muse Spark, tool calling): read logs, block an IP, patch the hole. Goal: stop the attacker.
4. Orchestrator (Python, FastAPI): runs the match turn by turn, streams every action as an event.
5. Dashboard: web page showing the 3 nodes lighting up as the match plays.

## Stack
- Python 3, FastAPI, Docker SDK for Python
- Meta Model API, model muse-spark-1.2-contributor for dev, muse-spark-1.3 for demo runs
- Plain HTML/JS dashboard with server-sent events

## Later (after MVP works)
- Verifier agent grading each defender action, linked to the exact log line (evidence chain)
- Second defender team on Muse Spark Open (open-weights prize)
- Live match commentator
- Scorecard: attacks stopped, seconds to contain
- Fine-tuned triage model from match data

## Rules we follow
- Public repo, new code only
- Attacks only ever hit the sandboxed containers, never any real system
- No key committed; MODEL_API_KEY stays in the environment
