# Crucible

Crucible is an AI cyber range where an AI red team and an AI blue team run containment exercises on simulated company networks, with an independent auditor that checks every defensive decision. Every exercise is logged event by event, so results can be replayed, scored, and inspected after the fact.

Defenders cannot safely test AI agents on live systems: a mistake there costs real uptime or real data. Crucible gives those agents a realistic but fully simulated network to probe and protect, plus honest measurement of what the defense actually did.

Built for the Meta Global AI Developer Hackathon, Indie track, Oct 26 to Nov 4 2026, with Muse Code.

## Architecture

- Sandbox of toy hosts on an internal-only Docker network, no published ports.
- One allowlisted action surface (`crucible/tools.py`): agents can only call listed tools against listed hosts.
- Red and blue agents (scripted baselines plus model versions), taking alternating turns through the orchestrator.
- Sensors and asset inventory: blue sees host roles, links, business weights, and deterministic sensor alerts.
- Auditor: a separate model call judges each blue action against the exact log lines blue had seen.
- Referee and playbook: after each exercise the referee merges lessons into a short tactical playbook (scrubbed of credentials, paths, and host names) that blue reads next run.
- Scenario generator: deterministic seeded networks with a proven intrusion path, decoys, dead ends, and noise, split into train and held-out seeds.
- Dashboard: the operations center page with live exercise views, replay, lobby, and results.
- Voice takeover: a judge can type or speak orders that blue executes live, through a command parser plus Muse Voice Transcribe.

## Models used and where

| Where | Model |
|---|---|
| Red team, blue team (main) | Muse Spark (`muse-spark-1.3`) |
| Blue team, open-weights variant | Muse Spark Open (`meta-models/muse-spark-open-1026`) on Baseten |
| Auditor and referee | Muse Wonder (`muse-wonder-1.0`) |
| Voice takeover transcription | Muse Voice Transcribe (`muse-voice-transcribe-1.0`) |
| Dashboard art | Muse Image (`muse-image-1.0`, generated at build time) |
| Dev and bulk runs | Muse Spark contributor (`muse-spark-1.3-contributor`) |

Chat models run through the Meta Model API (`https://api.meta.ai/v1`, OpenAI compatible); the open-weights blue variant runs on Baseten under a separate key. Every model call passes one client wrapper with retries, a disk cache, per-exercise spend logging, and a hard daily spend guard.

## Quickstart (fresh clone)

Prerequisites: Python 3.12 or newer, Docker running, a terminal.

```bash
git clone https://github.com/Lucaszy1/Crucible.git
cd Crucible
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`: set `MODEL_API_KEY` (required). Set `BASETEN_API_KEY` only for the open-weights blue variant. `LLM_DAILY_LIMIT_USD` caps estimated model spend per day.

Start the operations center (localhost only, no build step):

```bash
python -m crucible.serve
```

Open http://127.0.0.1:8000 in a browser. Use the control bar to run a scripted exercise, run a metered model exercise, or replay a past run.

Run one scripted exercise from the terminal (free, needs Docker):

```bash
python -m crucible.run_exercise --agents scripted --exercise-id demo-1
```

Run a small benchmark (scripted red costs nothing; blue model calls are metered):

```bash
python -m crucible.bench --name try --red chain --sensors on --memory off --episodes-per-cell 2
```

Run the checks without spending anything:

```bash
python -m pytest tests -m "not live" -q
node --test tests/*.mjs
```

Live-marked tests need the range up and can spend money; the `-m "not live"` filter stays on for routine runs.

## Repo layout

```text
crucible/          turn loop, agents, tools, API, scenarios, voice
crucible/agents/   red, blue, auditor, referee, scripted baselines
crucible/scenarios/ generator, simulator, seed sets, Docker stack files
dashboard/         static operations center page (HTML/JS, no build step)
docs/              specs, design notes, runbooks, result analyses
sandbox/           toy host images, compose files, classic scenario
scripts/           probes, art generation, Stage 3 merge and check tools
tests/             Python and node suites plus fixtures
runs/              exercise logs (local only, gitignored except a sample)
playbooks/         live blue playbook (local only, gitignored)
```

`docs/README.md` indexes the design notes and analyses.

## Results

All numbers below come from `docs/CAMPAIGN_RESULTS.md` (20 episodes per arm, classic scenario) and the saved benchmark runs under `runs/bench-*/`. Counts are quoted exactly; p-values marked computed were derived from those counts with exact tests.

Validation story first. Our first campaign looked like a win for memory, then analysis showed the result was contaminated: container logs were never cleared between episodes, so blue read stale success lines, quoted them in reports, and red (which could then see blue report text) reused the leaked account names. We threw that campaign out, fixed the design (per-episode log scoping with clean-start checks, and prompts where neither team sees the other team's report text), and reran from scratch. The rerun is the only campaign we report.

Memory versus control (model red and model blue, one scenario, 20 episodes per arm): the memory arm held 16 of 20, control held 10 of 20 (Fisher exact two-sided p = 0.096, not significant at 0.05). Mean blue score was 79.9 versus 49.9 on a bimodal 0-or-100 scale, so the mean restates the win counts. Red probed the exposed admin page in 4 of 20 memory episodes versus 10 of 20 control, and that probe predicted the outcome perfectly; red is blind to blue by construction, so that gap is sampling variation, not something blue caused. We do NOT claim memory helps: the gap is within noise, every perfect score came with zero containment against a red that never authenticated, and blue contained 0 of its 4 real intrusions.

Sensors with scripted red (`runs/bench-bench1`, classic scenario, Spark blue, memory off, 10 episodes per cell):

| Red | Sensors | Intrusions | Secret reads | Contained |
|---|---|---|---|---|
| chain | on | 10/10 | 0/10 | 10/10 |
| chain | off | 10/10 | 10/10 | 6/10 |
| noisy | on | 10/10 | 0/10 | 10/10 |
| noisy | off | 10/10 | 9/10 | 3/10 |

Secret-read gap, sensors on versus off: chain p < 0.0001, noisy p = 0.0001 (Fisher exact two-sided, computed). Limits, stated plainly: scripted red intrudes in every episode by construction, so this measures containment of a fixed chain, not detection of a varied intruder; one scenario only; 10 episodes per cell.

TBD: Stage 3 held-out results. The frozen-playbook generalization run on unseen networks (train seeds 1001 to 1040, held-out seeds 9001 to 9020) is in progress; its intrusion, containment, secret-read, delay, downtime, and auditor numbers will replace this paragraph when the benchmark finishes. No claim about generalization is made until then.

## What was built during the hackathon

From git history: 197 commits, Oct 26 11:34 PT (initial commit) through Oct 27 17:14 PT (latest at time of writing), all inside the event window so far.

Oct 26, 123 commits. Repo skeleton, PRD and build spec, model client with cache and spend guard, Docker sandbox growing from 3 to 4 toy hosts (web, app, db, workstation), allowlisted tools, event schema, orchestrator turn loop and scoring, scripted baselines, model agents with tool calling, batch runner, API backend plus dashboard v1, auditor with live judging and an eval harness, referee with playbook memory and campaigns, then the contamination diagnosis and the per-episode log scoping fix.

Oct 27, 74 commits. Arena and canvas dashboard scenes, removal of the no-effect block tool, asset inventory, deterministic sensor alerts with an on/off flag, intrusion-conditional scoring, the controlled benchmark harness, Baseten open-model blue with effort probes, the scenario generator with simulator proof and train/held-out seeds, the config-driven toy host image with compose renderer and parity harness, frozen playbooks, Stage 3 prep (paired seeds, scrub hardening, runbook, merge and suspicion checks), and voice takeover (command parser, transcribe client with a verified live probe, human blue mode, takeover routes).

TODO: review this timeline against the organizers' answer about the Oct 26 start before submitting, and confirm every listed item counts as event work.

## Responsible design

- Toy hosts only: fixed pretend services with scripted log lines, no real software under test.
- No real malware, no exploit code, no ransomware behavior; the weaknesses are toy misconfigurations such as an open admin page and default credentials.
- Internal Docker network with no published ports; agents reach hosts only through the allowlisted tool surface, enforced in code.
- No real credentials, keys, or data anywhere; API keys live in `.env`, which is gitignored and was never committed.
- A hard daily spend guard stops model calls before cost overruns, and exercises degrade to scripted baselines instead of failing.
