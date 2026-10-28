# Stage 3 results: frozen playbook on held-out networks

Generated 2026-10-28T07:56:24.228298+00:00 from `heldout`, `sensors`, `noisyref`, `xcheck`, `briefed`.
Frozen playbook sha256: `76c925e82ddeb30bd592ffe3511bba067aca8dc32db1130185ed8c2422a2c0d8`.
Corrected headline: sensors-on arms come from the rerun `runs/bench-stage3-heldout-sensors` (working sensors); sensors-off arms come from the valid half of `runs/bench-stage3-heldout`. The first run's sensors-on half is void (documented sensor bug, fixed in c9dd960) and appears only in the void section.
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

The first run's sensors-on arms ran with silently broken sensors (zero alerts in every episode) and are void by name (`bench-stage3-train-solver`, `bench-stage3-heldout`; documented sensor bug, fixed in c9dd960). Original cells and p-values:

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
- briefed-red-vs-Spark-Open (n=6): intrusions 6/6, secret reads 0/6, contained 6/6, med delay 17, med relevant 11, med contain 15.

## Claims

- A vs B on intrusion rate: discordant pairs 0-0 over 20 pairs, is within noise (p=1.0000).
- A vs B on secret-read rate: discordant pairs 18-0 over 20 pairs, differs (p=<0.0001).
- B vs C on intrusion rate: discordant pairs 0-0 over 20 pairs, is within noise (p=1.0000).
- B vs C on secret-read rate: discordant pairs 0-0 over 20 pairs, is within noise (p=1.0000).
- With working sensors every intrusion was contained in both memory arms (20/20 without memory, 20/20 with memory; secret reads 0/20 and 0/20): a ceiling effect, so memory is not distinguishable on outcomes with working sensors.
- Briefed model red against Open blue (n=6, descriptive only): intrusions 6/6, contained 6/6, secret reads 0/6, median relevant detection delay 11 events, median time to contain 15 events.
- Void record only: the void run's B vs C secret-read gap was discordant pairs 6-0 over 20 pairs (p=0.0312); it is not evidence.

## Non-claims

- No result here shows the playbook generalizes beyond these held-out seeds and difficulties; 20 scenarios are one pilot, not coverage.
- A comparison within noise is no conclusion, not proof of no effect: with 20 pairs only large asymmetries can reach p < 0.05.
- Held episodes can still reflect red failing on its own rather than blue defending; read wins beside intrusion and containment rates.
- B-vs-C pairs are exactly matched: the same scenario and rep index in one bench replays identical red moves across arms by design, so arm differences isolate blue, not red sampling. A-vs-B pairs across benches by scenario instead, which is approximate because red re-rolled between benches.
- The main table uses a solver red with seeded imperfection, because unbriefed model red achieved no intrusion in 18 train episodes: results show blue contains a known-path intruder, not that blue stops an adaptive attacker. Briefed model red intruded in 6 of 6 probe episodes but that probe is descriptive only at n=6.
- Audit justified rates cover the rerun B and C cells only; no other cell was audited.
- The frozen playbook was learned on train episodes whose sensors were void too, so its rules were learned without sensor input; the rerun tests a deaf-learned playbook under working sensors.
