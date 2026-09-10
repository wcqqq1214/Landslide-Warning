"""Temporal roles, matched baselines and additive prediction decomposition."""

from types import SimpleNamespace

import numpy as np
import pandas as pd

from physics_guided.data import Drivers, POINTS
from physics_guided.features import point_features
from physics_guided.probability import crps, interval_score, summarize

CUTOFFS = {432: 342, 612: 432}


def periods(n):
    c = CUTOFFS[n]
    return {
        "old_fit_context": (30, c),
        "scale_context": (c, n),
        "outer_train": (30, n),
        "prediction": (n, n + 180),
    }


def read_drivers(path, n):
    if n not in CUTOFFS:
        raise ValueError("Unregistered outer window")
    frame = pd.read_csv(path, nrows=n + 180, usecols=["Date", "Rainfall/mm", "RWL/m"])
    y0 = pd.read_csv(path, nrows=1, usecols=[p + "/mm" for p in POINTS])[
        [p + "/mm" for p in POINTS]
    ].to_numpy(float)[0]
    dates = pd.DatetimeIndex(pd.to_datetime(frame.Date))
    forcing = frame[["Rainfall/mm", "RWL/m"]].to_numpy(float)
    if (
        not dates.equals(pd.date_range("2016-07-01", periods=n + 180))
        or not np.isfinite(forcing).all()
        or not np.isfinite(y0).all()
    ):
        raise ValueError("Invalid driver dates or values")
    if (
        (forcing[:, 0] < 0).any()
        or (forcing[:, 1] < 130).any()
        or (forcing[:, 1] > 190).any()
    ):
        raise ValueError("Drivers outside the frozen physical interface")
    return Drivers(dates, forcing, y0)


def teacher_features(path, drivers, n):
    if n not in CUTOFFS or len(drivers.dates) != n + 180:
        raise ValueError("This extension must end at the registered forecast boundary")
    with np.load(path, allow_pickle=False) as saved:
        if not np.array_equal(saved["dates"], drivers.dates.strftime("%Y-%m-%d")):
            raise ValueError("Teacher calendar differs")
        mean = saved["mean"].copy()
        states = {
            key: saved[key].copy()
            for key in ("rain_head", "moisture", "reservoir_head")
        }
    if mean.shape != (n + 180, 4) or not np.isfinite(mean).all():
        raise ValueError("Incomplete physical trajectory")
    mechanics = SimpleNamespace(u=mean - drivers.y0, reference=states)
    x = point_features(drivers, mechanics)
    if not np.isfinite(x).all():
        raise ValueError("Nonfinite physical features")
    return mean, x


def score(means, scales, labels, n, branch, strategy):
    if means.shape[1:] != (n + 180, 4) or labels.shape != (n + 180, 4):
        raise ValueError("Scoring boundaries differ")
    sigma = np.broadcast_to(scales[:, None, :], means.shape)
    dist = summarize(means[:, 30:], sigma[:, 30:])
    scores = crps(means[:, 30:], sigma[:, 30:], labels[30:])
    rows = []
    for part, (a, b) in periods(n).items():
        ix = slice(a - 30, b - 30)
        error = dist["mean"][ix] - labels[a:b]
        for j, station in enumerate(POINTS):
            row = dict(
                outer_days=n,
                branch=branch,
                strategy=strategy,
                station=station,
                part=part,
                first_index=a,
                end_index_exclusive=b,
                days=b - a,
                rmse_mm=float(np.sqrt(np.mean(error[:, j] ** 2))),
                mae_mm=float(np.mean(abs(error[:, j]))),
                bias_mm=float(error[:, j].mean()),
                crps_mm=float(scores[ix, j].mean()),
            )
            for level in (80, 90, 95):
                lo, hi = dist[f"lower_{level}"][ix, j], dist[f"upper_{level}"][ix, j]
                y = labels[a:b, j]
                row[f"coverage_{level}"] = float(((lo <= y) & (y <= hi)).mean())
                row[f"width_{level}_mm"] = float((hi - lo).mean())
                row[f"interval_score_{level}_mm"] = float(
                    interval_score(y, lo, hi, level).mean()
                )
            rows.append(row)
    return rows, dist


def matched_comparisons(metrics):
    indexed = metrics.set_index(["outer_days", "branch", "strategy", "station", "part"])
    rows = []
    for n in CUTOFFS:
        for branch in ("OLD", "NEW"):
            for strategy in ("IN", "OOF"):
                for station in POINTS:
                    row = dict(
                        outer_days=n, branch=branch, strategy=strategy, station=station
                    )
                    for ref_name, ref_branch in (("own", branch), ("current", "NEW")):
                        differences = []
                        for part in ("outer_train", "prediction"):
                            own = indexed.loc[n, branch, strategy, station, part]
                            base = indexed.loc[n, ref_branch, "P0", station, part]
                            for metric in ("rmse_mm", "mae_mm", "crps_mm"):
                                value = float(own[metric] - base[metric])
                                row[f"{part}_{metric}_minus_{ref_name}"] = value
                                if metric != "crps_mm":
                                    differences.append(value)
                        row[f"strict_improvement_vs_{ref_name}"] = all(
                            value < -1e-6 for value in differences
                        )
                    rows.append(row)
    return pd.DataFrame(rows)


def decompose(old_base, new_base, old_mean, new_mean):
    if (
        old_base.shape != new_base.shape
        or old_mean.shape != old_base.shape
        or new_mean.shape != old_base.shape
    ):
        raise ValueError("Matched dates and points required for decomposition")
    physical = new_base - old_base
    correction = (new_mean - new_base) - (old_mean - old_base)
    total = new_mean - old_mean
    if not np.isfinite(total).all() or not np.allclose(
        total, physical + correction, rtol=0, atol=1e-8
    ):
        raise ArithmeticError("Additive decomposition failed")
    return physical, correction, total
