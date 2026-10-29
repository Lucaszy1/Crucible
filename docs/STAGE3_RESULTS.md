# Stage 3 results: frozen playbook on held-out networks

Generated 2026-10-29T14:38:01.119221+00:00 from `heldout`, `sensors`, `noisyref`, `xcheck`, `briefed`, `briefed_spark`, `frontier`.
Frozen playbook sha256: `76c925e82ddeb30bd592ffe3511bba067aca8dc32db1130185ed8c2422a2c0d8`.
Corrected headline: sensors-on arms come from the rerun `runs/bench-stage3-heldout-sensors` (working sensors); sensors-off arms come from the valid half of `runs/bench-stage3-heldout`. The first run's sensors-on half is void (documented sensor bug, fixed in 1224db4) and appears only in the void section.
Rates carry Wilson 95% intervals; paired p-values are exact two-sided (McNemar for binary outcomes, sign test for delays and downtime). p >= 0.05 is reported as within noise.

## Held-out cells (solver red, paired scenarios)

| arm | n | intrusions | secret reads | contained | med delay | med contain | downtime | audit just |
|-----|---|------------|--------------|-----------|-----------|-------------|----------|------------|
| A sensors-off | 20 | 20/20 100.0% (83.9%, 100.0%) | 18/20 90.0% (69.9%, 97.2%) | 4/20 20.0% | 9 | 14 | 0.75% | n/a |
| B sensors-on (rerun) | 20 | 20/20 100.0% (83.9%, 100.0%) | 0/20 0.0% (0.0%, 16.1%) | 20/20 100.0% | 15 | 6 | 8.50% | 62.3% |
| C + playbook (rerun) | 20 | 20/20 100.0% (83.9%, 100.0%) | 0/20 0.0% (0.0%, 16.1%) | 20/20 100.0% | 11 | 6 | 8.19% | 57.5% |
| bonus off+book | 20 | 20/20 100.0% (83.9%, 100.0%) | 7/20 35.0% (18.1%, 56.7%) | 14/20 70.0% | 11 | 12 | 3.88% | n/a |

Downtime is the mean over all episodes; med delay is the median first-report delay; med contain is the median time to contain over contained episodes. Audit covers the rerun B and C cells only.

## Paired comparisons

| comparison | pairs | metric | discordant | p | verdict |
|------------|-------|--------|------------|---|---------|
| A vs B | 20 | intrusion | 0-0 | 1.0000 | within noise |
| A vs B | 20 | secret_read | 18-0 | <0.0001 | differs |
| A vs B | 20 | detection_delay | 2-0 | 0.5000 | within noise |
| A vs B | 20 | downtime_pct | 0-20 | <0.0001 | differs |
| B vs C | 20 | intrusion | 0-0 | 1.0000 | within noise |
| B vs C | 20 | secret_read | 0-0 | 1.0000 | within noise |
| B vs C | 20 | detection_delay | 11-4 | 0.1185 | within noise |
| B vs C | 20 | downtime_pct | 4-4 | 1.0000 | within noise |

Discordant counts read (first-arm-worse, second-arm-worse) for binary outcomes: for intrusion and secret reads, the worse side is the arm where the bad outcome happened. For delays and downtime they read (first-arm-slower, second-arm-slower).
A vs B: cross-bench pairing by scenario; red re-rolled between benches, so this pairing is approximate.

## Void original (for the record, not evidence)

The first run's sensors-on arms ran with silently broken sensors (zero alerts in every episode) and are void by name (`bench-stage3-train-solver`, `bench-stage3-heldout`; documented sensor bug, fixed in 1224db4). Original cells and p-values:

| arm | n | intrusions | secret reads | contained | med delay | med contain | downtime | audit just |
|-----|---|------------|--------------|-----------|-----------|-------------|----------|------------|
| B sensors-on (void) | 20 | 20/20 100.0% (83.9%, 100.0%) | 17/20 85.0% (64.0%, 94.8%) | 4/20 20.0% | 20 | 18 | 0.55% | 90.2% |
| C + playbook (void) | 20 | 20/20 100.0% (83.9%, 100.0%) | 11/20 55.0% (34.2%, 74.2%) | 9/20 45.0% | 12.50 | 12 | 2.47% | 83.5% |

| comparison | pairs | metric | discordant | p | verdict |
|------------|-------|--------|------------|---|---------|
| A vs B | 20 | intrusion | 0-0 | 1.0000 | within noise |
| A vs B | 20 | secret_read | 2-1 | 1.0000 | within noise |
| A vs B | 20 | detection_delay | 1-0 | 1.0000 | within noise |
| A vs B | 20 | downtime_pct | 3-2 | 1.0000 | within noise |
| B vs C | 20 | intrusion | 0-0 | 1.0000 | within noise |
| B vs C | 20 | secret_read | 6-0 | 0.0312 | differs |
| B vs C | 20 | detection_delay | 1-3 | 0.6250 | within noise |
| B vs C | 20 | downtime_pct | 1-7 | 0.0703 | within noise |

## Reference runs

- noisy-sensors-on-memory-off (n=20): intrusions 20/20, secret reads 0/20, contained 20/20, med delay 14.50, med relevant 5, med contain 8.
- noisy-sensors-on-memory-on (n=20): intrusions 20/20, secret reads 0/20, contained 20/20, med delay 10, med relevant 5, med contain 8.
- spark-blue:solver-sensors-on-memory-off (n=6): intrusions 6/6, secret reads 0/6, contained 6/6, med delay 3, med relevant 1, med contain 6.
- briefed-red-vs-Spark-Open (n=20): intrusions 18/20, secret reads 0/18, contained 18/18, med delay 12, med relevant 7, med contain 22.
- briefed-red-vs-Spark (n=20): intrusions 20/20, secret reads 0/20, contained 20/20, med delay 5, med relevant 1, med contain 24.

## Open-weight parity (briefed red, Open vs Spark blue)

| blue | n | same nets | intrusions | contained | secret reads | med delay | med relevant | med contain | downtime | downtime cont | cost | red fb | blue fb |
|------|---|-----------|------------|-----------|--------------|-----------|--------------|-------------|----------|---------------|------|--------|---------|
| Open | 20 | 20/20 | 18/20 | 18/18 | 0/18 | 12 | 7 | 22 | 6.30% | 6.96% | $13.16 | 1/600 | 11/600 |
| Spark | 20 | 20/20 | 20/20 | 20/20 | 0/20 | 5 | 1 | 24 | 6.85% | 6.85% | $17.93 | 1/600 | 1/600 |

Same nets reads shared seeds over arm episodes; med delay is the median first-report delay, med relevant the median relevant delay; downtime is the mean over all episodes, downtime cont the mean over contained episodes; red/blue fb read fallbacks over turns.

- Same networks: 20 of 20 seeds overlap (9001-9020 (20 seeds)).
- Intrusions: Open 18/20, Spark 20/20 (Open fewer).
- Containment given intrusion: Open 18/18, Spark 20/20 (tied).
- Secret reads: Open 0/18, Spark 0/20 (tied).
- Median relevant detection delay: Open 7, Spark 1 events (Spark faster).
- Median time to contain: Open 22, Spark 24 events (Open faster).
- Mean downtime: Open 6.30%, Spark 6.85% (Open lower; contained means Open 6.96%, Spark 6.85%).
- Cost (Meta): Open $13.16, Spark $17.93 (Open lower).
- Baseline fallbacks: red Open 1 of 600, Spark 1 of 600; blue Open 11 of 600, Spark 1 of 600.
- Descriptive only at n=20 Open and n=20 Spark; briefed red re-rolled between benches, so pairing is approximate. This section compares the two runs; it does not conclude parity.

## Frontier tier (briefed red, Open blue)

- Frontier tier on 10 frontier networks (descriptive only): intrusions 9/10, contained 9/9, secret reads 0/9.
- Median relevant detection delay 7 events, median time to contain 20 events, mean downtime 4.26% over contained episodes, cost $6.38, Baseten tokens 807,490.
- Baseline fallbacks: 2 of 300 red turns (0.7%) and 3 of 300 blue turns (1.0%) used a safe baseline move after model replies with no usable tool call, across 5 of 10 episodes.
- Excluded from the frontier table: 1 classic-network episode the bench ran by accident: classic-model-briefed-sensors-on-memory-off-1 (no intrusion).

## Claims

- A vs B on intrusion rate: discordant pairs 0-0 over 20 pairs, is within noise (p=1.0000).
- A vs B on secret-read rate: discordant pairs 18-0 over 20 pairs, differs (p=<0.0001).
- B vs C on intrusion rate: discordant pairs 0-0 over 20 pairs, is within noise (p=1.0000).
- B vs C on secret-read rate: discordant pairs 0-0 over 20 pairs, is within noise (p=1.0000).
- With working sensors every intrusion was contained in both memory arms (20/20 without memory, 20/20 with memory; secret reads 0/20 and 0/20): a ceiling effect, so memory is not distinguishable on outcomes with working sensors.
- Briefed model red against Open blue (n=20, descriptive only): 18 of 20 networks had an intrusion, and all 18 were contained; red did not get a foothold on the other 2. Secret reads 0/18 (0.0%), median relevant detection delay 7 events, median time to contain 22 events, mean downtime 6.96%, cost $13.16, Baseten tokens 1,547,043.
- 1 of 600 red turns and 11 of 600 blue turns used a safe baseline move after model replies with no usable tool call, across 9 of 20 episodes.
- Void record only: the void run's B vs C secret-read gap was discordant pairs 6-0 over 20 pairs (p=0.0312); it is not evidence.
- Briefed-red blue comparison on 20 shared networks (descriptive only): Open intrusions 18/20, Spark 20/20; containment Open 18/18, Spark 20/20; secret reads Open 0/18, Spark 0/20.
- Briefed-red delays and cost: median relevant delay Open 7 vs Spark 1, median time to contain Open 22 vs Spark 24, cost Open $13.16 vs Spark $17.93.
- Frontier tier on 10 frontier networks (descriptive only): intrusions 9/10, contained 9/9, secret reads 0/9.
- Median relevant detection delay 7 events, median time to contain 20 events, mean downtime 4.26% over contained episodes, cost $6.38, Baseten tokens 807,490.
- Baseline fallbacks: 2 of 300 red turns (0.7%) and 3 of 300 blue turns (1.0%) used a safe baseline move after model replies with no usable tool call, across 5 of 10 episodes.
- Excluded from the frontier table: 1 classic-network episode the bench ran by accident: classic-model-briefed-sensors-on-memory-off-1 (no intrusion).
- 14 auditor verdicts in the corrected held-out rerun were replayed from the voided original run on byte-identical prompts; no blue, red, or referee response was cached and outcomes are unaffected.

## Non-claims

- No result here shows the playbook generalizes beyond these held-out seeds and difficulties; 20 scenarios are one pilot, not coverage.
- A comparison within noise is no conclusion, not proof of no effect: with 20 pairs only large asymmetries can reach p < 0.05.
- Held episodes can still reflect red failing on its own rather than blue defending; read wins beside intrusion and containment rates.
- B-vs-C pairs are exactly matched: the same scenario and rep index in one bench replays identical red moves across arms by design, so arm differences isolate blue, not red sampling. A-vs-B pairs across benches by scenario instead, which is approximate because red re-rolled between benches.
- The main table uses a solver red with seeded imperfection, because unbriefed model red achieved no intrusion in 18 train episodes: results show blue contains a known-path intruder, not that blue stops an adaptive attacker. The briefed-red run is descriptive only at n=20: 18 of 20 networks had an intrusion, and all 18 were contained; red did not get a foothold on the other 2.
- Audit justified rates cover the rerun B and C cells only; no other cell was audited.
- The frozen playbook was learned on train episodes whose sensors were void too, so its rules were learned without sensor input; the rerun tests a deaf-learned playbook under working sensors.
- The Open-vs-Spark blue comparison pairs across benches by scenario; briefed red re-rolled between benches, so pairing is approximate and descriptive only at n=20 per arm.
- The frontier tier run is descriptive only; it makes no claim that the tier is harder than other tiers and no claim that blue always holds.
