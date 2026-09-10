"""Prefix-only arrays and unchanged ConvLSTM; no physical solver calls."""

from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np
import pandas as pd
import torch

from physics_guided.features import Scaler, point_features
from physics_guided.probability import crps, interval_score, summarize
from physics_guided.training import setup, update
from physics_guided.models import M1
from physics_guided_forecast_error.artifacts import trajectory_name

POINTS = ("ATU1", "ATU5", "MJ3", "MJ1")
CUTOFFS = {432: 342, 612: 432}
ORIGINS = {342: (252,), 432: (252, 342)}
FLOOR = np.array([1, 0.01, 1, 0.1, 0.1, 1, 1, 0.1] + [0.01] * 8 + [1] * 4)[:, None]


def teacher_arrays(source, n, recipe, forcing, y0):
    """Only drivers and day-zero displacement enter physical feature construction."""
    days = len(forcing)
    if forcing.shape != (days, 2) or np.asarray(y0).shape != (4,) or days > n + 180:
        raise ValueError("Teacher driver boundary violation")
    with np.load(source / trajectory_name(n, recipe), allow_pickle=False) as saved:
        dates = pd.date_range("2016-07-01", periods=days)
        if not np.array_equal(saved["dates"][:days], dates.strftime("%Y-%m-%d")):
            raise ValueError("Teacher date identity differs")
        mean = saved["mean"][:days].copy()
        states = {
            k: saved[k][:days].copy()
            for k in ("rain_head", "moisture", "reservoir_head")
        }
    drivers = SimpleNamespace(
        forcing=np.array(forcing, copy=True), dates=dates, y0=np.array(y0, copy=True)
    )
    mechanics = SimpleNamespace(u=mean - y0, reference=states)
    features = point_features(drivers, mechanics)
    if (
        mean.shape != (days, 4)
        or features.shape != (days, 20, 4)
        or not np.isfinite(features).all()
    ):
        raise ValueError("Incomplete or nonfinite saved teacher")
    return mean, features


def common_scaler(features, cutoff):
    if features.shape != (cutoff, 20, 4) or cutoff not in ORIGINS:
        raise ValueError("Scaler must see exactly the registered mean prefix")
    return Scaler.fit(features, FLOOR, static=4)


@dataclass
class Samples:
    windows: torch.Tensor
    base: torch.Tensor
    target: torch.Tensor
    weights: torch.Tensor
    indices: np.ndarray
    origins: np.ndarray
    unique_days: int


def make_samples(strategy, cutoff, labels, teachers, scaler):
    if (
        strategy not in ("IN", "OOF")
        or cutoff not in ORIGINS
        or labels.shape != (cutoff, 4)
    ):
        raise ValueError("Mean training accepts only its exact registered label prefix")
    if (
        set(teachers) != set(ORIGINS[cutoff]) | {cutoff}
        or not np.isfinite(labels).all()
    ):
        raise ValueError("Unexpected teacher or nonfinite training labels")
    for mean, features in teachers.values():
        if (
            mean.shape != (cutoff, 4)
            or features.shape != (cutoff, 20, 4)
            or not np.isfinite(mean).all()
            or not np.isfinite(features).all()
        ):
            raise ValueError("Teacher inputs must end at the mean cutoff")
    pairs = [(k, t) for k in ORIGINS[cutoff] for t in range(k, min(k + 180, cutoff))]
    counts = {t: sum(tt == t for _, tt in pairs) for _, t in pairs}
    windows, bases = [], []
    transformed = {
        k: scaler.transform(features) for k, (_, features) in teachers.items()
    }
    for k, t in pairs:
        teacher = cutoff if strategy == "IN" else k
        windows.append(transformed[teacher][t - 29 : t + 1, :, None, :])
        bases.append(teachers[teacher][0][t])
    ids = np.array([t for _, t in pairs])

    def tensor(a):
        return torch.as_tensor(np.array(a), dtype=torch.float64)

    return Samples(
        tensor(windows),
        tensor(bases),
        tensor(labels[ids]),
        tensor([1 / counts[t] for t in ids]),
        ids,
        np.array([k for k, _ in pairs]),
        len(counts),
    )


def backward_loss(model, samples, chunk=128):
    """Accumulate the same weighted full-batch objective across chunks."""
    total = 0.0
    for start in range(0, len(samples.indices), chunk):
        end = min(start + chunk, len(samples.indices))
        error = (
            samples.base[start:end]
            + model(samples.windows[start:end])
            - samples.target[start:end]
        ) / 100
        loss = (error.square() * samples.weights[start:end, None]).sum() / (
            4 * samples.unique_days
        )
        loss.backward()
        total += float(loss.detach())
    return total


def new_model(seed):
    setup(seed)
    return M1()


def mean_step(model, op, samples):
    op.zero_grad(set_to_none=True)
    loss = backward_loss(model, samples)
    if not np.isfinite(loss):
        raise ArithmeticError("Nonfinite mean loss")
    return loss, update(model, op)


def predict(model, base, features, scaler, chunk=128):
    if features.shape != (len(base), 20, 4) or base.shape != (len(base), 4):
        raise ValueError("Incomplete prediction features")
    x = scaler.transform(features)
    mean = np.full_like(base, np.nan)
    with torch.no_grad():
        for start in range(29, len(base), chunk):
            end = min(start + chunk, len(base))
            windows = torch.as_tensor(
                np.stack([x[t - 29 : t + 1, :, None, :] for t in range(start, end)]),
                dtype=torch.float64,
            )
            mean[start:end] = base[start:end] + model(windows).numpy()
    if not np.isfinite(mean[29:]).all():
        raise ArithmeticError("Nonfinite predicted mean")
    return mean


def fit_scale(frozen_forecasts, scale_labels):
    if (
        frozen_forecasts.ndim != 3
        or frozen_forecasts.shape[1:] != scale_labels.shape
        or scale_labels.shape[1:] != (4,)
        or len(scale_labels) not in (90, 180)
    ):
        raise ValueError("Scale API accepts only the isolated 90/180-day label segment")
    if not np.isfinite(frozen_forecasts).all() or not np.isfinite(scale_labels).all():
        raise ValueError("Nonfinite scale inputs")
    return np.maximum(
        0.001, np.sqrt(np.mean((frozen_forecasts - scale_labels[None]) ** 2, axis=1))
    )


def score_components(means, scales, labels, n, strategy):
    sigmas = np.broadcast_to(scales[:, None, :], means.shape).copy()
    valid = summarize(means[:, 30:], sigmas[:, 30:])
    probability_crps = crps(means[:, 30:], sigmas[:, 30:], labels[30:])
    rows = []
    for part, start, end in (("train", 30, n), ("prediction", n, n + 180)):
        loc = slice(start - 30, end - 30)
        prediction = valid["mean"][loc]
        error = prediction - labels[start:end]
        for j, station in enumerate(POINTS):
            row = dict(
                outer_days=n,
                strategy=strategy,
                part=part,
                station=station,
                days=end - start,
                rmse_mm=float(np.sqrt(np.mean(error[:, j] ** 2))),
                mae_mm=float(np.mean(abs(error[:, j]))),
                crps_mm=float(probability_crps[loc, j].mean()),
            )
            for level in (80, 90, 95):
                lower, upper = (
                    valid[f"lower_{level}"][loc, j],
                    valid[f"upper_{level}"][loc, j],
                )
                y = labels[start:end, j]
                row[f"coverage_{level}"] = float(np.mean((y >= lower) & (y <= upper)))
                row[f"width_{level}_mm"] = float(np.mean(upper - lower))
                row[f"interval_score_{level}_mm"] = float(
                    interval_score(y, lower, upper, level).mean()
                )
            rows.append(row)
    return rows, valid


def compare_metrics(metrics):
    rows = []
    for n in (432, 612):
        for strategy in ("IN", "OOF"):
            for station in POINTS:
                differences = {}
                for part in ("train", "prediction"):
                    chosen = metrics[
                        (metrics.outer_days == n)
                        & (metrics.strategy == strategy)
                        & (metrics.station == station)
                        & (metrics.part == part)
                    ].iloc[0]
                    for reference in ("P0", "IN"):
                        base = metrics[
                            (metrics.outer_days == n)
                            & (metrics.strategy == reference)
                            & (metrics.station == station)
                            & (metrics.part == part)
                        ].iloc[0]
                        for metric in ("rmse_mm", "mae_mm", "crps_mm"):
                            differences[f"{part}_{metric}_minus_{reference}"] = float(
                                chosen[metric] - base[metric]
                            )
                strict = all(
                    differences[f"{p}_{m}_minus_P0"] < -1e-6
                    for p in ("train", "prediction")
                    for m in ("rmse_mm", "mae_mm")
                )
                rows.append(
                    dict(
                        outer_days=n,
                        strategy=strategy,
                        station=station,
                        strict_mean_improvement=bool(strict),
                        **differences,
                    )
                )
    return pd.DataFrame(rows)
