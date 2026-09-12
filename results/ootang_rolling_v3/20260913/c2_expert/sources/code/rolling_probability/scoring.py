"""Marginal scores and chronological, already-matured forecast error calibration."""

from collections import deque
import math
import numpy as np
import pandas as pd
from scipy.special import ndtr, ndtri

from .data import POINTS


def crps(y, mu, sigma):
    z = (y - mu) / sigma
    return sigma * (
        z * (2 * ndtr(z) - 1)
        + 2 * np.exp(-z * z / 2) / math.sqrt(2 * math.pi)
        - 1 / math.sqrt(math.pi)
    )


def interval(y, mu, sigma, level):
    q = ndtri((1 + level) / 2)
    low, high = mu - q * sigma, mu + q * sigma
    score = (
        high
        - low
        + 2 / (1 - level) * (np.maximum(low - y, 0) + np.maximum(y - high, 0))
    )
    return (low <= y) & (y <= high), high - low, score


class CausalCalibration:
    def __init__(self, horizon=30, window=90, prior_count=10, prior_sum=10):
        self.values = [[deque(maxlen=window) for _ in range(4)] for _ in range(horizon)]
        self.prior_count, self.prior_sum = prior_count, prior_sum
        self.latest_target = -1

    def update(self, horizon_index, error, raw_sigma, target, current_origin):
        if target >= current_origin:
            raise ValueError("Unmatured forecast error entered calibration")
        z2 = (np.asarray(error) / np.asarray(raw_sigma)) ** 2
        if not np.isfinite(z2).all():
            raise ArithmeticError("Nonfinite calibration error")
        for j in range(4):
            self.values[horizon_index][j].append(float(z2[j]))
        self.latest_target = max(self.latest_target, target)

    def factors(self, current_origin):
        if self.latest_target >= current_origin:
            raise ValueError("Calibration contains a future observation")
        return np.array(
            [
                [
                    math.sqrt((self.prior_sum + sum(q)) / (self.prior_count + len(q)))
                    for q in row
                ]
                for row in self.values
            ]
        )


def score_predictions(prediction, labels, levels=(0.8, 0.9, 0.95)):
    origins = prediction["origins"]
    means, sigmas = prediction["mean"], prediction["sigma"]
    rows = []
    for k in range(means.shape[1]):
        ids = origins + k
        mask = ids < len(labels)
        y = labels[ids[mask]]
        mu, sd = means[mask, k], sigmas[mask, k]
        if not np.isfinite(mu).all() or not np.isfinite(sd).all() or np.any(sd <= 0):
            raise ValueError("Invalid valid-horizon forecast")
        errors = mu - y
        for j, p in enumerate(POINTS):
            row = dict(
                horizon=k + 1,
                point=p,
                n=len(y),
                mae=float(abs(errors[:, j]).mean()),
                rmse=float(np.sqrt((errors[:, j] ** 2).mean())),
                crps=float(crps(y[:, j], mu[:, j], sd[:, j]).mean()),
            )
            for level in levels:
                cv, wi, sc = interval(y[:, j], mu[:, j], sd[:, j], level)
                for name, values in (
                    ("coverage", cv),
                    ("width", wi),
                    ("interval_score", sc),
                ):
                    row[f"{name}{round(level * 100)}"] = float(values.mean())
            rows.append(row)
    return pd.DataFrame(rows)


def aggregate(frame):
    numeric = [k for k in frame if k not in ("point", "n", "horizon", "model")]
    rows = []
    for (model, h), g in frame.groupby(["model", "horizon"], sort=False):
        if tuple(g.point) != POINTS:
            raise ValueError("Incomplete four-point metric table")
        row = dict(
            model=model,
            horizon=int(h),
            n_per_point=int(g.n.iloc[0]),
            **{k: float(g[k].mean()) for k in numeric},
        )
        row["pooled_rmse"] = float(np.sqrt(np.mean(g.rmse**2)))
        rows.append(row)
    return pd.DataFrame(rows)


def gate(metrics, summary, candidate, simple_baseline, spec):
    h = spec["primary_horizon"]
    m = metrics[metrics.horizon == h].set_index(["model", "point"])
    s = summary[summary.horizon == h].set_index("model")
    c, b = s.loc[candidate], s.loc["B_ANCHOR"]
    eff = spec["effect"]
    checks = {
        f"average_{k}_improves_5pct": bool(
            c[k] <= b[k] * (1 - eff["relative_improvement"])
        )
        for k in ("mae", "rmse", "crps", "interval_score90")
    }
    lo, hi = eff["coverage90_average"]
    checks["average_coverage90"] = bool(lo <= c.coverage90 <= hi)
    for p in POINTS:
        pc, pb = m.loc[(candidate, p)], m.loc[("B_ANCHOR", p)]
        for k in ("mae", "rmse"):
            checks[f"{p}_{k}_nonregression"] = bool(
                pc[k] <= pb[k] + eff["point_mean_atol_mm"]
            )
        for k in ("crps", "interval_score90"):
            checks[f"{p}_{k}_guard"] = bool(
                pc[k] <= pb[k] * (1 + eff["point_probability_max_regression"])
            )
        lo, hi = eff["coverage90_point"]
        checks[f"{p}_coverage90"] = bool(lo <= pc.coverage90 <= hi)
    for k in ("rmse", "crps"):
        checks[f"beats_simple_{k}"] = bool(c[k] <= s.loc[simple_baseline, k])
    return dict(
        candidate=candidate,
        primary_horizon=h,
        simple_baseline=simple_baseline,
        passed=all(checks.values()),
        passed_count=sum(checks.values()),
        checks=checks,
    )
