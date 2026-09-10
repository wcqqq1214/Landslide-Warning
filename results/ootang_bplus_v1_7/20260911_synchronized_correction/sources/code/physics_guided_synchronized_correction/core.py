"""Prefix-bound training, a differentiable rate limiter, and frozen rollout."""

import numpy as np
import pandas as pd
import torch

from physics_guided.features import Scaler
from physics_guided.training import update
from physics_guided_sample_learning.core import FLOOR, POINTS, CUTOFFS, teacher_arrays

PREFIXES = {342: "A", 432: "B", 612: "B"}


def read_teacher(path, source, h):
    """Parse forcing and initial displacement only, including the forecast period."""
    if h not in PREFIXES:
        raise ValueError("Unregistered model prefix")
    frame = pd.read_csv(path, nrows=h + 180, usecols=["Date", "Rainfall/mm", "RWL/m"])
    if not pd.DatetimeIndex(pd.to_datetime(frame.Date)).equals(
        pd.date_range("2016-07-01", periods=h + 180)
    ):
        raise ValueError("Forecast driver dates differ")
    forcing = frame[["Rainfall/mm", "RWL/m"]].to_numpy(float)
    first = pd.read_csv(path, nrows=1, usecols=[p + "/mm" for p in POINTS])
    y0 = first[[p + "/mm" for p in POINTS]].to_numpy(float)[0]
    if (
        not np.isfinite(forcing).all()
        or not np.isfinite(y0).all()
        or (forcing[:, 0] < 0).any()
        or (forcing[:, 1] < 130).any()
        or (forcing[:, 1] > 190).any()
    ):
        raise ValueError("Invalid drivers or initial displacement")
    return teacher_arrays(source, h, PREFIXES[h], forcing, y0)


def training_constants(base, features, labels, h):
    if (
        h not in PREFIXES
        or base.shape != (h, 4)
        or labels.shape != (h, 4)
        or features.shape != (h, 20, 4)
        or not all(np.isfinite(x).all() for x in (base, features, labels))
    ):
        raise ValueError("Constants require the exact registered training prefix")
    residual = labels - base
    amplitude = np.maximum(1, 2 * np.sqrt(np.mean(residual[30:] ** 2, axis=0)))
    rate = np.maximum(
        0.01, 2 * np.sqrt(np.mean(np.diff(residual[29:], axis=0) ** 2, axis=0))
    )
    return Scaler.fit(features, FLOOR, static=4), amplitude, rate


def limit_torch(raw, amplitude, rate):
    """All previous corrections remain connected to autograd."""
    if raw.ndim != 2 or raw.shape[1] != 4 or not len(raw):
        raise ValueError("A nonempty consecutive four-point sequence is required")
    a = torch.as_tensor(amplitude, dtype=raw.dtype, device=raw.device)
    d = torch.as_tensor(rate, dtype=raw.dtype, device=raw.device)
    if (
        a.shape != (4,)
        or d.shape != (4,)
        or not torch.isfinite(a).all()
        or not torch.isfinite(d).all()
        or (a <= 0).any()
        or (d <= 0).any()
    ):
        raise ValueError("Correction limits must be finite and positive")
    previous = torch.zeros_like(raw[0])
    values = []
    for desired in raw:
        target = a * torch.tanh(desired / a)
        previous = previous + d * torch.tanh((target - previous) / d)
        values.append(previous)
    return torch.stack(values)


def windows(features, scaler, start=30, end=None):
    end = len(features) if end is None else end
    if features.shape[1:] != (20, 4) or not 30 <= start < end <= len(features):
        raise ValueError("Window boundary differs")
    x = scaler.transform(features)
    return torch.as_tensor(
        np.stack([x[t - 29 : t + 1, :, None, :] for t in range(start, end)]),
        dtype=torch.float64,
    )


def correction(raw, strategy, amplitude, rate):
    if strategy == "U":
        return raw
    if strategy == "L":
        return limit_torch(raw, amplitude, rate)
    raise ValueError("Unknown correction strategy")


def mean_step(model, op, x, base, labels, strategy, amplitude, rate):
    op.zero_grad(set_to_none=True)
    raw = model(x)
    residual = correction(raw, strategy, amplitude, rate)
    loss = ((base + residual - labels) / 100).square().mean()
    if not torch.isfinite(loss):
        raise ArithmeticError("Nonfinite full-prefix mean loss")
    loss.backward()
    norm = update(model, op)
    return float(loss.detach()), norm


def predict(model, base, features, scaler, strategy, amplitude, rate, chunk=128):
    if base.shape != (len(features), 4) or len(base) <= 30 or chunk < 1:
        raise ValueError("Incomplete rollout")
    raw = np.full_like(base, np.nan)
    raw[29] = 0
    with torch.no_grad():
        for start in range(30, len(base), chunk):
            raw[start : start + chunk] = model(
                windows(features, scaler, start, min(start + chunk, len(base)))
            ).numpy()
        r = correction(torch.as_tensor(raw[30:]), strategy, amplitude, rate).numpy()
    mean = np.full_like(base, np.nan)
    mean[29] = base[29]
    mean[30:] = base[30:] + r
    if not np.isfinite(mean[29:]).all() or not np.isfinite(raw[29:]).all():
        raise ArithmeticError("Nonfinite forecast")
    return mean, raw


def compare(metrics):
    indexed = metrics.set_index(["outer_days", "strategy", "station", "part"])
    rows = []
    for n in CUTOFFS:
        for strategy in ("U", "L"):
            for station in POINTS:
                row = dict(outer_days=n, strategy=strategy, station=station)
                for reference in ("P0", "U"):
                    for part in ("train", "prediction"):
                        selected = indexed.loc[n, strategy, station, part]
                        base = indexed.loc[n, reference, station, part]
                        for metric in ("rmse_mm", "mae_mm", "crps_mm"):
                            row[f"{part}_{metric}_minus_{reference}"] = float(
                                selected[metric] - base[metric]
                            )
                row["strict_mean_improvement"] = all(
                    row[f"{part}_{metric}_minus_P0"] < -1e-6
                    for part in ("train", "prediction")
                    for metric in ("rmse_mm", "mae_mm")
                )
                rows.append(row)
    return pd.DataFrame(rows)


def bound_diagnostics(mean, base, amplitude, rate):
    residual = mean[29:] - base[29:]
    return dict(
        max_abs_mm=np.max(abs(residual[1:]), axis=0).tolist(),
        max_step_mm=np.max(abs(np.diff(residual, axis=0)), axis=0).tolist(),
        amplitude_mm=np.asarray(amplitude).tolist(),
        rate_mm_per_day=np.asarray(rate).tolist(),
    )
