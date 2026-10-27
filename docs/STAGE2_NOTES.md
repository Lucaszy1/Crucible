# Stage 2: generated scenarios on Docker

Stage 1 produced specs plus an in-memory simulator. This stage runs
any spec on real containers: one config-driven toyhost image, a
compose renderer, spec-driven tooling, scenario selection on every
CLI, a frozen-playbook evaluation hook, and a parity harness that
diffs container logs against the simulator.

## Toyhost image (`sandbox/toyhost/`)

One image plays any host in any spec. The entrypoint
(`toyhost.py`, stdlib only on python:3.12-slim) reads the mounted
spec JSON plus `TOYHOST_ID`, plants the host's files, and writes
the credential/gate state the shell handlers enforce:

- `signin` checks `user:password` lines, touches the session flag
  on success, and always fails with `NO_GATE` on workstation-role
  hosts. Log lines match the classic gate exactly.
- `read-file` / `list-files` enforce session gating: the /secret
  tree plus the spec's planted files and parent dirs need an
  earlier successful sign-in on that host, exactly the simulator's
  rule. Denied reads print `GATE_REQUIRED` and log nothing.
- `rotate-signin` / `reset-signin` rotate or restore the live
  passwords and drop the session flag, logging the classic lines.
- The web server (HTTP roles only) serves the leaky admin page
  with its hint, decoy pages that leak nothing, and status pages;
  the patch flag closes the admin pages. Every request logs the
  classic `request from=` line.
- A seeded noise thread prints the spec's background failures and
  requests for this host from fake `10.9.0.x` clients. `TOYHOST_NOISE=0`
  disables it (the parity harness renders stacks that way).

Passwords never reach the logs. A `ps` stub stands in for procps,
which slim does not ship. The classic images are untouched.

## Renderer and active spec

`scenarios/render.py` writes `sandbox/compose.gen.yml` plus
`sandbox/spec.gen.json`: one service per spec host (max 10) on an
internal-only `range` network, no published ports, 0.5 CPU / 256M
RAM limits each, container names from the spec, spec mounted
read-only.

`sandbox.set_active_spec(spec)` switches the whole harness to the
spec: the allowlist, the compose file, `load_scenario` (via the
classic-shaped adapter in `scenarios/compat.py`), the blue
inventory, business weights, scripted reds, scoring, the auditor,
and the model prompts all follow it. `None` restores classic
exactly. `sandbox.start()` builds; `start_warm()` skips the build
for fast scenario switches.

Two judgment calls worth knowing:

- HTTP requests relay through the entry host's container on
  generated ranges (there is no workstation). If blue isolates
  the entry host, later page probes fail.
- The clean-start account check now matches the `user=` token
  exactly instead of a bare substring: the account `metrics`
  appears in the benign path `/metrics`, which used to be a
  false positive. `user=`-form detections are unchanged.

## Scenario selection

- `run_exercise --scenario classic|gen:<seed>:<difficulty>`
- `batch`, `campaign`, `bench`: `--scenarios` comma list plus
  `--scenario-set train|heldout` (appended at `--difficulty`,
  default medium). Counts are per scenario; episodes group by
  scenario so the stack restarts as rarely as possible; the image
  builds once.
- Every episode's first event records `scenario_id`,
  `scenario_seed`, `scenario_difficulty`; CSVs append the same
  three columns; campaign/bench summaries record the scenario
  list. Resume requires the same scenario set.

## Frozen playbooks

`campaign` and `bench` accept `--freeze-playbook`: blue still
reads the playbook, but the referee never updates it. Train on
the train set unfrozen, then evaluate on held-out with the
playbook frozen. Recorded as `playbook_frozen` in summary.json
(the rule count also stays flat in the CSV).

## Parity harness

`python -m crucible.parity --seeds smoke` boots each smoke spec
(noise off), runs the derived ChainRed through the real tools,
and diffs every container log line against the simulator
(`from=` masked on request lines). Any divergence, including a
failed live action, is reported with host, line, and both sides;
exit 1 on any mismatch. `--difficulty` narrows the sweep,
`--leave-up` keeps the last stack running.

## Not verified without Docker

- The toyhost image build and every container behavior: page
  serving, gates, handlers, noise pacing, relay path.
- Compose semantics: service names, `deploy.resources.limits`,
  internal networking, warm-switch time (target under 40s).
- The parity harness end to end (unit-tested with fakes only).
- Any live generated exercise, campaign, or benchmark.

## Commands to run, in order

(a) Build the image (from the repo root, with Docker running):

    cd sandbox && docker compose -f compose.gen.yml build

  The compose file is rendered on demand; generate one first with:

    python -c "from crucible.scenarios import generate, render; \
      render.write_stack(generate.generate(1, 'medium'), 'sandbox')"

  Or simply run (b), whose first boot builds automatically.

(b) Parity harness on the smoke seeds:

    python -m crucible.parity --seeds smoke

  Expect 9 scenarios (3 seeds x 3 difficulties), 0 mismatches.
  Add `--difficulty hard` to narrow it, `--leave-up` to keep the
  last stack for inspection.

(c) One live generated exercise with the scripted chain red:

    python -m crucible.run_exercise --agents scripted \
      --scenario gen:7:easy --exercise-id gen-smoke-1
