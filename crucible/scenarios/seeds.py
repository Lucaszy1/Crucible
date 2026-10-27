"""Named seed sets: train memory on one, evaluate on the other.

Benchmarks in Stage 3 train blue's playbook on TRAIN_SEEDS and
measure generalization on HELDOUT_SEEDS, which no training episode
ever touches. SMOKE_SEEDS are three quick seeds for ad-hoc checks.
"""

from __future__ import annotations

TRAIN_SEEDS = list(range(1001, 1041))

HELDOUT_SEEDS = list(range(9001, 9021))

SMOKE_SEEDS = [1, 2, 3]
