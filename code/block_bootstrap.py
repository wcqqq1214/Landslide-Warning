"""Moving-block bootstrap helpers for dependent daily observations."""

from __future__ import annotations

import math

import numpy as np


def moving_block_indices(n_observations, block_length, rng):
    """Sample overlapping non-circular blocks and return exactly n indices."""
    if n_observations <= 0:
        raise ValueError("n_observations must be positive")
    if not 1 <= block_length <= n_observations:
        raise ValueError("block_length must be between 1 and n_observations")

    n_blocks = math.ceil(n_observations / block_length)
    max_start = n_observations - block_length
    starts = rng.integers(0, max_start + 1, size=n_blocks)
    offsets = np.arange(block_length)
    return (starts[:, None] + offsets[None, :]).ravel()[:n_observations]


def percentile_interval(samples, confidence_level=0.95):
    """Return a two-sided percentile interval from bootstrap replicates."""
    values = np.asarray(samples, dtype=float)
    if values.ndim != 1 or len(values) == 0:
        raise ValueError("samples must be a non-empty one-dimensional array")
    if not np.all(np.isfinite(values)):
        raise ValueError("samples must contain only finite values")
    if not 0 < confidence_level < 1:
        raise ValueError("confidence_level must be between 0 and 1")
    alpha = 1.0 - confidence_level
    lower, upper = np.quantile(values, [alpha / 2.0, 1.0 - alpha / 2.0])
    return float(lower), float(upper)
