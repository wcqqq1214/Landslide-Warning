"""Gaussian mixture CDF inversion, analytic CRPS and proper interval scores."""

import numpy as np
from scipy.special import ndtr


def validate(mu, sigma):
    mu, sigma = np.asarray(mu), np.asarray(sigma)
    if mu.shape != sigma.shape or mu.ndim < 1 or len(mu) not in (1, 3):
        raise ValueError("Expected one Gaussian or a complete three-seed mixture")
    if not np.isfinite(mu).all() or not np.isfinite(sigma).all() or np.any(sigma <= 0):
        raise ValueError("Mixture components must be finite with positive sigma")
    return mu, sigma


def quantile(mu, sigma, p):
    mu, sigma = validate(mu, sigma)
    if not 0 < p < 1:
        raise ValueError("Interior quantile required")
    low = np.min(mu - 8 * sigma, axis=0)
    high = np.max(mu + 8 * sigma, axis=0)
    span = high - low
    for _ in range(100):
        below = np.mean(ndtr((low - mu) / sigma), axis=0) > p
        above = np.mean(ndtr((high - mu) / sigma), axis=0) < p
        if not (below.any() or above.any()):
            break
        low = np.where(below, low - span, low)
        high = np.where(above, high + span, high)
        span *= 2
    else:
        raise ArithmeticError("Cannot bracket mixture quantile")
    for _ in range(200):
        mid = (low + high) / 2
        left = np.mean(ndtr((mid - mu) / sigma), axis=0) < p
        low, high = np.where(left, mid, low), np.where(left, high, mid)
        if np.max(high - low) <= 1e-6:
            return (low + high) / 2
    raise ArithmeticError("Mixture quantile did not converge")


def crps(mu, sigma, y):
    mu, sigma = validate(mu, sigma)

    def a(d, s):
        z = d / s
        return 2 * s * np.exp(-0.5 * z * z) / np.sqrt(2 * np.pi) + d * (2 * ndtr(z) - 1)

    first = np.mean(a(y - mu, sigma), axis=0)
    second = np.mean(
        a(
            mu[:, None] - mu[None, :],
            np.sqrt(sigma[:, None] ** 2 + sigma[None, :] ** 2),
        ),
        axis=(0, 1),
    )
    return first - 0.5 * second


def summarize(mu, sigma):
    mu, sigma = validate(mu, sigma)
    mean = mu.mean(axis=0)
    # Algebraically equivalent centered identity avoids cancellation at large y0.
    variance = np.mean(sigma**2 + (mu - mean) ** 2, axis=0)
    result = dict(mean=mean, std=np.sqrt(variance))
    for level in (80, 90, 95):
        alpha = 1 - level / 100
        result[f"lower_{level}"] = quantile(mu, sigma, alpha / 2)
        result[f"upper_{level}"] = quantile(mu, sigma, 1 - alpha / 2)
    return result


def interval_score(y, lower, upper, level):
    alpha = 1 - level / 100
    return (
        upper
        - lower
        + 2 / alpha * (np.maximum(lower - y, 0) + np.maximum(y - upper, 0))
    )
