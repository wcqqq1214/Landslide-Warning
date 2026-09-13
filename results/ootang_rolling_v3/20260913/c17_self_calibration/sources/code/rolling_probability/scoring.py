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
    def __init__(
        self, horizon=30, window=90, prior_count=10, prior_sum=10, feedback=None
    ):
        self.values = [[deque(maxlen=window) for _ in range(4)] for _ in range(horizon)]
        self.prior_count, self.prior_sum = prior_count, prior_sum
        self.latest_target = -1
        self.feedback = feedback
        self.log_scale = np.zeros((horizon, 4))
        self.bound_hits = np.zeros((horizon, 4), dtype=int)
        self.feedback_updates = np.zeros(horizon, dtype=int)
        self.feedback_last_target = np.full(horizon, -1, dtype=int)
        self.log_min = self.log_max = 0.0
        if feedback:
            if not (
                feedback["rate"] > 0
                and 0 < feedback["target_coverage"] < 1
                and feedback["log_scale_bound"] > 0
                and feedback["initial_log_scale"] == 0
            ):
                raise ValueError("Invalid frozen feedback specification")

    def update(
        self, horizon_index, error, raw_sigma, target, current_origin, issued_sigma=None
    ):
        if target >= current_origin:
            raise ValueError("Unmatured forecast error entered calibration")
        z2 = (np.asarray(error) / np.asarray(raw_sigma)) ** 2
        if not np.isfinite(z2).all():
            raise ArithmeticError("Nonfinite calibration error")
        if self.feedback:
            if target <= self.feedback_last_target[horizon_index]:
                raise ValueError("Feedback target was already processed")
            issued = np.asarray(issued_sigma, dtype=float)
            if (
                issued.shape != (4,)
                or not np.isfinite(issued).all()
                or (issued <= 0).any()
            ):
                raise ValueError("Feedback requires the positive scale actually issued")
            q = ndtri((1 + self.feedback["target_coverage"]) / 2)
            miss = np.abs(error) > q * issued
            proposed = self.log_scale[horizon_index] + self.feedback["rate"] * (
                miss.astype(float) - (1 - self.feedback["target_coverage"])
            )
            bound = self.feedback["log_scale_bound"]
            self.bound_hits[horizon_index] += np.abs(proposed) >= bound
            self.log_scale[horizon_index] = np.clip(proposed, -bound, bound)
            self.feedback_updates[horizon_index] += 1
            self.feedback_last_target[horizon_index] = target
            self.log_min = min(self.log_min, float(self.log_scale.min()))
            self.log_max = max(self.log_max, float(self.log_scale.max()))
        for j in range(4):
            self.values[horizon_index][j].append(float(z2[j]))
        self.latest_target = max(self.latest_target, target)

    def factors(self, current_origin):
        if self.latest_target >= current_origin:
            raise ValueError("Calibration contains a future observation")
        rms = np.array(
            [
                [
                    math.sqrt((self.prior_sum + sum(q)) / (self.prior_count + len(q)))
                    for q in row
                ]
                for row in self.values
            ]
        )
        return rms if not self.feedback else rms * np.exp(self.log_scale)

    def feedback_summary(self):
        return dict(
            log_scale_final=self.log_scale.tolist(),
            log_scale_min=self.log_min,
            log_scale_max=self.log_max,
            bound_hits=self.bound_hits.tolist(),
            updates_per_horizon=self.feedback_updates.tolist(),
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
