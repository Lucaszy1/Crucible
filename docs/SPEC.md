# Crucible: Build Spec

Single source of truth for the build. Read this fully at the start of every session. Update the checklist at the bottom as work lands.

## 1. One-line pitch

Crucible is an autonomous security validation range. It lets AI defender agents be stress-tested against AI red-team agents inside a simulated company network, so defensive AI can be measured, audited, and improved without ever touching a real system.

## 2. Framing rules (apply to ALL copy)

The hackathon bans projects involving "war." Every README line, UI label, commit message, code comment, and video script must read as security validation and defensive testing.

- Use: validation range, red team, blue team, exercise, scenario, containment, operations center, detection, response.
- Never use: war, warfare, battlefield, combat, weapon, kill, destroy, enemy, army, or the old project name.
- UI labels: "Operations Center" (not war room), "Exercise" (not battle/match), "Containment" (not defeat).
- No em dashes in any written content. Vary wording, no copy-pasted phrasing.
- No commit messages, comments, or files may mention any AI tool or assistant. Commits are plain and describe the change only.

## 3. Safety boundaries (hard rules)

- Everything runs in isolated Docker containers on an internal-only network with no internet access.
- Red-team tools can only target hosts inside the sandbox allowlist. Enforce this in code, not just in prompts.
- The planted weaknesses are toy misconfigurations (exposed admin endpoint, weak default credential, unpatched fake service version). No real exploit code, no malware, no real ransomware. Any "encryption" scenario is simulated by flipping state flags on dummy files.
- No real credentials, keys, or data anywhere. MODEL_API_KEY lives in .env only, which is gitignored.

## 4. Hackathon constraints

- Rubric: Impact 30, Meta tooling 25, Technical depth 20, Demo 25.
- All code must be written during the event. Repo is public. Commit early and often so history proves it.
- Demo video: 3 minutes, shows only what was built during the event.
- Submit the night of Nov 3. Official deadline is Nov 4, 9 PM ET (6 PM PT). Nov 4 is buffer only.
- Bonus targets: Best Build on Open Weight Models (blue team on Muse Spark Open), Best Agentic Use-Case (auditor agent), Most Viral (short clip posted on X).

## 5. Models (Meta Model API, OpenAI-compatible)

Base URL: https://api.meta.ai/v1, key in env var MODEL_API_KEY.

| Role | Model | Notes |
|---|---|---|
| Red team agents | muse-spark-1.3 | Tight tool loops, cheap |
| Blue team agents (main) | muse-spark-1.3 | |
| Blue team (open-weights variant) | meta-models/muse-spark-open-1026 via Baseten | Use this exact ID, not the one in the Baseten docs. Needs Baseten key. |
| Auditor and referee | muse-wonder-1.0, reasoning effort high | Low volume, judgment heavy |
| Voice control | muse-voice-transcribe-1.0 | Realtime websocket wss://api.meta.ai/v1/asr/realtime |
| Dev and bulk runs | muse-spark-1.3-contributor | Discounted. Not for final demo recording. No "max" reasoning. |

Budget: $150 credits. Cache responses, keep scripted baseline agents for tests, log token spend per exercise, and add a hard daily spend guard in the client wrapper.

## 6. Architecture

```
crucible/
  docs/            PRD.md, SPEC.md
  sandbox/         docker-compose, vulnerable toy services, scenario definitions
  crucible/
    llm.py         single client wrapper (retry, cache, spend guard, model routing)
    sandbox.py     controls containers, enforces allowlist, resets state
    tools.py       the only actions agents can take (see section 7)
    agents/        red.py, blue.py, auditor.py, referee.py
    orchestrator.py  turn loop, event bus, scoring, persistence
    api.py         FastAPI: REST + server-sent events + websocket for voice
    events.py      event schema, written to jsonl for replay
  dashboard/       static HTML/JS operations center
  runs/            exercise logs (gitignored except one sample)
  tests/
```

Stack: Python 3, FastAPI, Docker SDK, plain JS dashboard with SSE (add a small graph lib only if needed).

## 7. Tool API (the only way agents act)

Red team tools: scan_host(host), http_request(host, path, method, body), try_credential(host, user, pass), read_file(host, path), report_finding(text).

Blue team tools: read_logs(host, n), list_processes(host), block_ip(ip), isolate_host(host), patch_service(host, service), restore_host(host), report_finding(text).

Every tool call emits an event: id, timestamp, actor, tool, args, result, reasoning_summary, evidence_refs (log line ids). Tools reject anything outside the allowlist.

## 8. Features

### Must have (done by end of Day 6)
1. Sandbox with 3 to 5 containers (web, db, workstation, plus file server and backup if time allows) and a deliberately weak scenario.
2. Red team and blue team agents with tool calling, turn-based loop, deterministic seed.
3. Live Operations Center dashboard: animated network graph (host states: clean, probed, compromised, isolated, restored), side-by-side agent reasoning streams, event timeline with MITRE ATT&CK tags, containment timer, scoreboard.
4. Auditor agent: scores every blue action as justified or not, citing the exact log line. Shown live and in the final scorecard. This is the verifiable-agents story.
5. Voice commands via Muse Voice Transcribe: judge can say "isolate the file server" and the operations center executes it as a human blue-team action.
6. Replay: every exercise saved as jsonl, dashboard can play it back with a scrubber.

### Should have (Day 7)
7. Blue team on Muse Spark Open (Baseten) vs Muse Spark, with a head-to-head scorecard.
8. Playbook memory: after each exercise the referee writes lessons, blue loads them next run. Chart containment time across exercises.
9. Event injection panel: "new vulnerability appears", "credential leaked" mid-exercise.

### Stretch (only if ahead of schedule)
10. Procedurally generated networks per exercise.
11. Benchmark mode: run N exercises across model pairs, output a leaderboard with Elo.
12. Multi-agent teams (recon, access, persistence for red; analyst, responder for blue).
13. Vision: an agent reads a dashboard screenshot.

## 9. Scoring

Per exercise: time to detect, time to contain, hosts compromised, secret exfiltrated yes or no, blue actions justified percentage (from auditor), tokens spent. Learning curve chart across exercises is the hero visual.

## 10. Schedule

| Day | Date | Goal |
|---|---|---|
| 1 | Mon Oct 26 | Repo skeleton, .env, llm.py wrapper with spend guard, docker sandbox with 3 hosts, tools.py with allowlist |
| 2 | Tue Oct 27 | Orchestrator turn loop, events.py, first end-to-end exercise with scripted agents |
| 3 | Wed Oct 28 | Red and blue agents on Muse Spark, tool calling working end to end |
| 4 | Thu Oct 29 | Auditor agent, scoring, jsonl logging |
| 5 | Fri Oct 30 | Dashboard v1: network graph, streams, timeline over SSE |
| 6 | Sat Oct 31 | Dashboard polish, replay scrubber, voice commands. All must-haves done. |
| 7 | Sun Nov 1 | Should-haves: open-weights blue team, playbook memory, event injection |
| 8 | Mon Nov 2 | FEATURE COMPLETE. Full test runs, bug fixes, run the batch for the learning curve. No new features after today. |
| 9 | Tue Nov 3 | README, architecture diagram, record and edit 3 minute video, post clip on X, make repo public check, SUBMIT THAT NIGHT |
| 10 | Wed Nov 4 | Buffer only. Official deadline 9 PM ET. |

## 11. Demo video plan (3:00)

0:00 to 0:20 problem: small teams cannot safely test defensive AI. 0:20 to 1:30 live exercise on the operations center, red probing, blue detecting and containing, auditor scoring in real time. 1:30 to 2:10 voice command and event injection. 2:10 to 2:40 learning curve across exercises plus open-weights vs Spark scorecard. 2:40 to 3:00 impact and what is next.

## 12. Working rules for the coding agent

- Small commits, plain messages, push often.
- Never commit .env, keys, or runs/ output (except one sample).
- Keep a tests/ smoke test that runs one scripted exercise with no LLM calls.
- If a must-have is behind schedule, cut a stretch item, never the dashboard or auditor.
- When unsure about a Meta API detail, check the docs, do not guess model IDs.

## 13. Checklist

- [ ] Repo skeleton and .gitignore
- [ ] llm.py with spend guard
- [ ] Sandbox containers and allowlist
- [ ] tools.py and event schema
- [ ] Orchestrator loop
- [ ] Red and blue agents
- [ ] Auditor agent
- [ ] Dashboard v1
- [ ] Replay
- [ ] Voice commands
- [ ] Open-weights blue team
- [ ] Playbook memory and learning curve
- [ ] Event injection
- [ ] README and diagram
- [ ] Demo video
- [ ] Submission
