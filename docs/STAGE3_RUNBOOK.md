# Stage 3 runbook: frozen playbook on held-out networks

Copy-paste order. One bench at a time (one Docker user). Nothing here
pushes. Dashboard files stay untouched except `dashboard/results.json`,
written once at the very end by the report script.

Cost basis (measured in-repo): model red on Meta ~$0.08/ep, Spark blue
on Meta ~$0.12/ep, referee ~$0.035/ep, audit-end ~$0.27/ep, open blue
on Baseten ~52k tokens/ep at unknown price. Audit runs on the B and C
held-out cells only.

| step | episodes | Meta ~ | wall ~ |
|------|----------|--------|--------|
| train | 18 | $2.10 | 30 min |
| main held-out | 80 | $17.20 | 2.5 h |
| noisy reference | 40 | $0.00 | 40 min |
| spark cross-check | 6 | $1.20 | 10 min |

Total ~$20.50 Meta plus ~7.2M Baseten tokens (check the invoice).
$20.28 of the $40 daily cap was already spent on Oct 27, so: train
today, main tomorrow after the OK gate. Reference and cross-check can
wait until after the main run, same day or later.

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
export AUDIT_BC="--audit-cells model-sensors-on-memory-off,model-sensors-on-memory-on"
```

Caps come from `scripts/cap.sh <headroom>` (today's Meta spend plus
headroom). Recompute before every invocation, including resumes.

## 1. Train (today)

```bash
caffeinate -i python -m crucible.bench --name stage3-train \
  --red model --sensors on --memory on --episodes-per-cell 2 \
  --scenarios "$TRAIN_SC" --audit off --no-cache $BLUE \
  --spend-cap-usd "$(scripts/cap.sh 6)"
```

## 2. Train gate (no model spend) — STOP, report, wait for OK

```bash
python scripts/stage3_checks.py --baseten runs/bench-stage3-train
```

Print the Baseten token line, check the Baseten usage page for billed
cost, and get an explicit OK before step 4. The main run must not
start without it.

## 3. Freeze (no model spend)

```bash
mkdir -p runs/stage3
cp runs/bench-stage3-train/playbooks/model-sensors-on-memory-on.json \
  runs/stage3/frozen-playbook.json
shasum -a 256 runs/stage3/frozen-playbook.json \
  | awk '{print $1}' > runs/stage3/frozen-playbook.sha256
cat runs/stage3/frozen-playbook.sha256
python scripts/stage3_checks.py \
  --leak-grep runs/stage3/frozen-playbook.json
```

The leak grep must print OK (exit 0). If it fails, stop: the book
quotes held-out content and nothing downstream is valid.

## 4. Main held-out (tomorrow, after OK)

```bash
caffeinate -i python -m crucible.bench --name stage3-heldout \
  --red model --sensors on,off --memory on,off --episodes-per-cell 1 \
  --scenarios "$HELD_SC" --freeze-playbook \
  --playbook-from runs/stage3/frozen-playbook.json \
  --audit end $AUDIT_BC --no-cache $BLUE \
  --spend-cap-usd "$(scripts/cap.sh 25)"
```

## 5. Reference and cross-check (after main, can wait)

```bash
caffeinate -i python -m crucible.bench --name stage3-noisyref \
  --red noisy --sensors on --memory on,off --episodes-per-cell 1 \
  --scenarios "$HELD_SC" --freeze-playbook \
  --playbook-from runs/stage3/frozen-playbook.json \
  --audit off --no-cache $BLUE \
  --spend-cap-usd "$(scripts/cap.sh 2)"
caffeinate -i python -m crucible.bench --name stage3-xcheck \
  --red model --sensors on --memory off --episodes-per-cell 2 \
  --scenarios "gen:9001:easy,gen:9008:medium,gen:9015:hard" \
  --audit off --no-cache \
  --spend-cap-usd "$(scripts/cap.sh 4)"
```

The cross-check uses default Spark blue on Meta (no `$BLUE` flags).

## 6. Merge and checks (no model spend, at the end)

```bash
python scripts/stage3_checks.py --all
python scripts/stage3_report.py
```

`--all` must exit 0 before trusting the report. The report writes
`docs/STAGE3_RESULTS.md` and `dashboard/results.json`.

## Resume after a crash

Re-run the identical bench command with `--resume` appended and a
freshly computed cap. Resume requires the same cells, episodes,
scenarios, freeze flag, and audit selection; `--playbook-from` is
ignored on resume (cells reload from the bench dir).

```bash
# example: resume the main run
caffeinate -i python -m crucible.bench --name stage3-heldout \
  --red model --sensors on,off --memory on,off --episodes-per-cell 1 \
  --scenarios "$HELD_SC" --freeze-playbook \
  --audit end $AUDIT_BC --no-cache $BLUE \
  --spend-cap-usd "$(scripts/cap.sh 25)" --resume
```

If Docker is wedged (`docker ps` hangs or the stack never boots):

```bash
docker ps
docker compose -f sandbox/compose.gen.yml down
```

Then resume as above. Never start a second bench while one runs.
