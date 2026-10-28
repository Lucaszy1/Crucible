# Stage 3 runbook: frozen playbook on held-out networks

Copy-paste order. One bench at a time (one Docker user). Nothing here
pushes. Dashboard files stay untouched except `dashboard/results.json`,
written once at the very end by the report script.

Replan note: model red intruded in 0 of 18 train episodes (see
`docs/STAGE3_RED_DIAGNOSIS.md`), so the main evaluation runs the
solver red (proven path plus seeded imperfection, $0 red cost) and
the old train bench `runs/bench-stage3-train` is void evidence: keep
it, never freeze from it, never train on it. Model red returns only
as the small gated probe in step 6.

Cost basis (measured in-repo unless noted): solver red $0/ep, Spark
blue on Meta ~$0.12/ep, referee ~$0.035/ep, audit-end ~$0.27/ep
(estimate, never measured here), open blue on Baseten ~55k
tokens/ep at unknown price. Audit runs on the B and C held-out
cells only.

| step | episodes | Meta ~ | wall ~ |
|------|----------|--------|--------|
| train (solver) | 18 | $0.65 | 20 min |
| main held-out | 80 | $10.80 | 70 min |
| noisy reference | 40 | $0.00 | 40 min |
| spark cross-check | 6 | $0.75 | 10 min |

Total ~$12.20 Meta plus ~7.6M Baseten tokens (check the invoice).
Train and main fit one $40 day; the OK gate after train still
holds. The optional briefed-red probe (step 6) adds about $0.50
Meta and no Baseten (scripted blue makes no model calls).

## 0. Preflight (no model spend)

```bash
python -m pytest tests -m "not live" -q
node --test tests/test_host_state.mjs
python -m crucible.parity --seeds smoke
python -m crucible.run_exercise --agents scripted \
  --scenario gen:7:easy --exercise-id gen-smoke-1
python -c "import os; assert os.environ.get('MODEL_API_KEY'); \
  assert os.environ.get('BASETEN_API_KEY')"
docker ps >/dev/null && echo "docker ok"
```

Never run plain `pytest tests` here: live-marked tests need the range
and can spend money. The `-m "not live"` filter stays on every run.

## Shared exports

```bash
export BLUE="--blue-model meta-models/muse-spark-open-1026 --blue-base-url https://inference.baseten.co/v1 --blue-api-key-env BASETEN_API_KEY --blue-reasoning-effort low"
export TRAIN_SC="gen:1001:easy,gen:1002:easy,gen:1003:easy,gen:1004:medium,gen:1005:medium,gen:1006:medium,gen:1007:hard,gen:1008:hard,gen:1009:hard"
export HELD_SC="gen:9001:easy,gen:9002:easy,gen:9003:easy,gen:9004:easy,gen:9005:easy,gen:9006:easy,gen:9007:easy,gen:9008:medium,gen:9009:medium,gen:9010:medium,gen:9011:medium,gen:9012:medium,gen:9013:medium,gen:9014:medium,gen:9015:hard,gen:9016:hard,gen:9017:hard,gen:9018:hard,gen:9019:hard,gen:9020:hard"
export AUDIT_BC="--audit-cells solver-sensors-on-memory-off,solver-sensors-on-memory-on"
```

Caps come from `scripts/cap.sh <headroom>` (today's Meta spend plus
headroom). Recompute before every invocation, including resumes.

## 1. Train (solver red)

```bash
caffeinate -i python -m crucible.bench --name stage3-train-solver \
  --red solver --sensors on --memory on --episodes-per-cell 2 \
  --scenarios "$TRAIN_SC" --audit off --no-cache $BLUE \
  --spend-cap-usd "$(scripts/cap.sh 3)"
```

## 2. Train gate (no model spend) — STOP, report, wait for OK

```bash
python scripts/stage3_checks.py --baseten runs/bench-stage3-train-solver
awk -F, 'NR>1 && $8=="True" {n++} END {print n+0 " intrusions of " NR-1}' \
  runs/bench-stage3-train-solver/summary.csv
```

Print the Baseten token line, check the Baseten usage page for billed
cost, and get an explicit OK before step 4. Expect intrusions in all
18 episodes (solver red intrudes by red move 3 against any blue seen
so far). Fewer than 16 means something changed: stop and diagnose,
do not freeze. The main run must not start without the OK.

## 3. Freeze (no model spend)

```bash
mkdir -p runs/stage3
cp runs/bench-stage3-train-solver/playbooks/solver-sensors-on-memory-on.json \
  runs/stage3/frozen-playbook.json
shasum -a 256 runs/stage3/frozen-playbook.json \
  | awk '{print $1}' > runs/stage3/frozen-playbook.sha256
cat runs/stage3/frozen-playbook.sha256
python scripts/stage3_checks.py \
  --leak-grep runs/stage3/frozen-playbook.json
```

Freeze only from `bench-stage3-train-solver`. The old
`bench-stage3-train` playbook learned "do nothing" from 18 empty
episodes and is void.
The leak grep must print OK (exit 0). If it fails, stop: the book
quotes held-out content and nothing downstream is valid.

## 4. Main held-out (after OK)

```bash
caffeinate -i python -m crucible.bench --name stage3-heldout \
  --red solver --sensors on,off --memory on,off --episodes-per-cell 1 \
  --scenarios "$HELD_SC" --freeze-playbook \
  --playbook-from runs/stage3/frozen-playbook.json \
  --audit end $AUDIT_BC --no-cache $BLUE \
  --spend-cap-usd "$(scripts/cap.sh 15)"
```

Arms: A `solver-sensors-off-memory-off`, B
`solver-sensors-on-memory-off`, C `solver-sensors-on-memory-on`,
plus the bonus `solver-sensors-off-memory-on` ablation. Paired
seeds replay identical red moves per scenario+rep across arms, so
arm differences isolate blue.

## 5. Reference and cross-check (after main, can wait)

```bash
caffeinate -i python -m crucible.bench --name stage3-noisyref \
  --red noisy --sensors on --memory on,off --episodes-per-cell 1 \
  --scenarios "$HELD_SC" --freeze-playbook \
  --playbook-from runs/stage3/frozen-playbook.json \
  --audit off --no-cache $BLUE \
  --spend-cap-usd "$(scripts/cap.sh 2)"
caffeinate -i python -m crucible.bench --name stage3-xcheck \
  --red solver --sensors on --memory off --episodes-per-cell 2 \
  --scenarios "gen:9001:easy,gen:9008:medium,gen:9015:hard" \
  --audit off --no-cache \
  --spend-cap-usd "$(scripts/cap.sh 3)"
```

The cross-check uses default Spark blue on Meta (no `$BLUE` flags)
with solver red.

## 6. Model-red probe (optional, gated; briefed profile is ready)

Six episodes of briefed red against scripted blue on train seeds,
no audit. About $0.50 Meta, no Baseten:

```bash
caffeinate -i python -m crucible.bench --name stage3-redprobe \
  --red model-briefed --blue scripted --sensors on --memory off \
  --episodes-per-cell 2 \
  --scenarios "gen:1001:easy,gen:1005:medium,gen:1008:hard" \
  --max-turns 60 --audit off --no-cache \
  --spend-cap-usd "$(scripts/cap.sh 3)"
awk -F, 'NR>1 && $8=="True" {n++} END {print n+0 " intrusions of " NR-1}' \
  runs/bench-stage3-redprobe/summary.csv
```

Stop rule: report the intrusion count; on 0 of 6, stop and report,
and model red stays out of the main evaluation. Expand to a full
cell only on at least one intrusion. See
`docs/STAGE3_RED_DIAGNOSIS.md` section 7 for the headwind caveat
(scripted blue isolates at move 3) before concluding from a zero.

## 7. Merge and checks (no model spend, at the end)

```bash
python scripts/stage3_checks.py --all \
  --train runs/bench-stage3-train-solver
python scripts/stage3_report.py
```

`--all` must exit 0 before trusting the report. The report writes
`docs/STAGE3_RESULTS.md` and `dashboard/results.json`.

## Honest framing for the write-up

Model red got in 0 of 18 train episodes, so the defense evaluation
uses a solver red with seeded imperfection. That proves blue
detects and contains a known-path intruder on held-out networks;
it does not prove blue stops an adaptive attacker, and the frozen
playbook's rules were learned against the solver's path
distribution, not against model red. Say both parts.

## Resume after a crash

Re-run the identical bench command with `--resume` appended and a
freshly computed cap. Resume requires the same cells, episodes,
scenarios, freeze flag, and audit selection; `--playbook-from` is
ignored on resume (cells reload from the bench dir).

```bash
# example: resume the main run
caffeinate -i python -m crucible.bench --name stage3-heldout \
  --red solver --sensors on,off --memory on,off --episodes-per-cell 1 \
  --scenarios "$HELD_SC" --freeze-playbook \
  --audit end $AUDIT_BC --no-cache $BLUE \
  --spend-cap-usd "$(scripts/cap.sh 15)" --resume
```

If Docker is wedged (`docker ps` hangs or the stack never boots):

```bash
docker ps
docker compose -f sandbox/compose.gen.yml down
```

Then resume as above. Never start a second bench while one runs.
