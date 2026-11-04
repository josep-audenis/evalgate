"""Confidence interval for the paired mean difference. Pure, deterministic, stdlib only."""

import random
from math import ceil, floor, fsum, sqrt
from statistics import NormalDist, stdev
from typing import Literal

CIMethod = Literal["bootstrap", "normal"]

RESAMPLES = 1000
# Above this many pairs the bootstrap distribution of a mean is ~normal (CLT) and resampling
# in pure Python gets slow (n * RESAMPLES draws), so the normal interval is used instead.
BOOTSTRAP_MAX_N = 2000
_SEED = 0  # fixed: identical inputs must give identical intervals


def mean_diff_ci(diffs: list[float], confidence: float) -> tuple[float, float, CIMethod, int]:
    """CI for mean(candidate - baseline) over paired cases: (low, high, method, resamples)."""
    n = len(diffs)
    mean = fsum(diffs) / n
    if n == 1:
        return mean, mean, "bootstrap", 0
    alpha = 1 - confidence
    if n > BOOTSTRAP_MAX_N:
        half = NormalDist().inv_cdf(1 - alpha / 2) * stdev(diffs) / sqrt(n)
        return mean - half, mean + half, "normal", 0

    # Percentile bootstrap. Index draws use rng.random() only: its sequence for a given seed
    # is stable across Python versions, unlike random.choices().
    rng = random.Random(_SEED)
    rnd = rng.random
    means = sorted(fsum(diffs[int(rnd() * n)] for _ in range(n)) / n for _ in range(RESAMPLES))
    low = means[floor(alpha / 2 * RESAMPLES)]
    high = means[ceil((1 - alpha / 2) * RESAMPLES) - 1]
    return low, high, "bootstrap", RESAMPLES
