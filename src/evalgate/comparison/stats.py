"""Paired statistics: mean-difference CI and McNemar test. Pure, deterministic, stdlib only."""

import random
from math import ceil, exp, floor, fsum, lgamma, log, sqrt
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

    # Percentile bootstrap. Index draws use rng.random() only: its sequence for a given seed is
    # documented as stable across Python versions; choices() and randrange() are not.
    rng = random.Random(_SEED)
    rnd = rng.random
    means = sorted(fsum(diffs[int(rnd() * n)] for _ in range(n)) / n for _ in range(RESAMPLES))
    low = means[floor(alpha / 2 * RESAMPLES)]
    high = means[ceil((1 - alpha / 2) * RESAMPLES) - 1]
    return low, high, "bootstrap", RESAMPLES


def mcnemar_p(newly_failing: int, newly_passing: int) -> float:
    """One-sided exact McNemar p-value for "the candidate fails this check more often".

    Only discordant pairs carry information. Under H0 (no change) each flip is equally likely
    to go either way, so newly_failing ~ Binomial(newly_failing + newly_passing, 1/2) and
    p = P(X >= newly_failing). Summed in log space so large counts neither overflow nor slow down.
    """
    n = newly_failing + newly_passing
    if newly_failing == 0:
        return 1.0
    log_half_n = n * log(0.5)
    log_terms = [
        lgamma(n + 1) - lgamma(k + 1) - lgamma(n - k + 1) + log_half_n
        for k in range(newly_failing, n + 1)
    ]
    peak = max(log_terms)
    return min(1.0, exp(peak) * fsum(exp(t - peak) for t in log_terms))
