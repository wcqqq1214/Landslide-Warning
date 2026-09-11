"""Explicit forecast origins, paired history masking, and frozen-history rollout."""

import numpy as np
import pandas as pd
import torch
from torch import nn

from physics_guided.features import Scaler
from physics_guided.models import M1, initialize
from physics_guided.training import setup, update
from physics_guided_sample_learning.core import FLOOR, POINTS, CUTOFFS

LEADS = (0, 6, 29, 59, 89, 119, 149, 179)
STRATEGIES = ("P0", "A", "C", "H")
HISTORY_FLOOR = np.array([1.0, 0.01])[:, None]


class HistoryM1(M1):
    """The existing 30-step M1 cell with three explicitly registered channels."""

    def __init__(self):
        nn.Module.__init__(self)
        self.gates = nn.Conv2d(23 + 16, 64, (1, 3), padding=(0, 1), dtype=torch.float64)
        self.output = nn.Conv2d(16, 1, 1, dtype=torch.float64)
        initialize(self, self.output)


def new_model(seed):
    setup(seed)
    return HistoryM1()


def training_pairs(h):
    if type(h) is not int or h <= 31:
        raise ValueError("Training requires a nonempty origin/history prefix")
    return np.array(
        [(o, o + lead) for o in range(31, h, 14) for lead in LEADS if o + lead < h],
        dtype=int,
    )


def evaluation_pairs(h, days):
    if type(h) is not int or h <= 31 or days != h + 180:
        raise ValueError("Evaluation requires exactly the registered 180-day horizon")
    return np.array(
        [(31 + ((t - 31) // 180) * 180 if t < h else h, t) for t in range(31, days)],
        dtype=int,
    )


def history_values(base, labels):
    if (
        labels.ndim != 2
        or labels.shape[1] != 4
        or base.shape != labels.shape
        or len(labels) < 31
        or not np.isfinite(labels).all()
        or not np.isfinite(base).all()
    ):
        raise ValueError("History requires matching finite four-point prefixes")
    error = labels - base
    values = np.empty((len(labels), 2, 4))
    values[:, 0] = error
    values[0, 1] = np.nan  # Never a valid history input: its left endpoint is absent.
    values[1:, 1] = np.diff(error, axis=0)
    return values


def training_scalers(base, features, labels, h):
    if base.shape != (h, 4) or features.shape != (h, 20, 4) or labels.shape != (h, 4):
        raise ValueError("Scalers may receive only the exact training prefix")
    if not np.isfinite(features).all():
        raise ValueError("Nonfinite physical features")
    history = history_values(base, labels)
    return Scaler.fit(features, FLOOR, static=4), Scaler.fit(history[1:], HISTORY_FLOOR)


def windows(
    base, features, labels, h, pairs, physical_scaler, history_scaler, strategy
):
    if strategy not in ("C", "H"):
        raise ValueError("Only the paired neural strategies have network inputs")
    if (
        labels.shape != (h, 4)
        or base.shape != (len(features), 4)
        or features.shape[1:] != (20, 4)
        or h > len(features)
    ):
        raise ValueError("Input labels must end exactly at the model cutoff")
    pairs = np.asarray(pairs)
    if (
        pairs.ndim != 2
        or pairs.shape[1] != 2
        or not len(pairs)
        or pairs.dtype.kind not in "iu"
    ):
        raise ValueError("Queries must be nonempty integer origin/target pairs")
    origins, targets = pairs.T
    if (
        np.any(origins < 31)
        or np.any(origins > h)
        or np.any(targets < origins)
        or np.any(targets >= len(features))
        or np.any(targets - origins > 179)
    ):
        raise ValueError("Query reads unavailable history or exceeds its horizon")
    history = history_scaler.transform(history_values(base[:h], labels))
    physical = physical_scaler.transform(features)
    result = []
    for o, t in pairs:
        observed = history[o - 30 : o].copy()
        if strategy == "C":
            observed.fill(0)
        lead = np.full((30, 1, 4), (t - o) / 179)
        result.append(
            np.concatenate([physical[t - 29 : t + 1], observed, lead], axis=1)
        )
    result = np.stack(result)
    if not np.isfinite(result).all():
        raise ValueError("Nonfinite forecast inputs")
    return torch.as_tensor(result[:, :, :, None, :], dtype=torch.float64)


def mean_step(model, op, x, base_targets, targets):
    op.zero_grad(set_to_none=True)
    loss = ((base_targets + model(x) - targets) / 100).square().mean()
    if not torch.isfinite(loss):
        raise ArithmeticError("Nonfinite history-conditioned mean loss")
    loss.backward()
    return float(loss.detach()), update(model, op)


def predict(
    model,
    base,
    features,
    labels,
    h,
    physical_scaler,
    history_scaler,
    strategy,
    chunk=128,
):
    if (
        strategy not in STRATEGIES
        or type(chunk) is not int
        or chunk < 1
        or labels.shape != (h, 4)
    ):
        raise ValueError("Invalid strategy, batch size, or label boundary")
    pairs = evaluation_pairs(h, len(base))
    if strategy == "P0":
        return base.copy()
    mean = np.full_like(base, np.nan)
    mean[29:31] = base[29:31]  # Common scoring day 30 explicitly has zero correction.
    if strategy == "A":
        e = labels - base[:h]
        mean[pairs[:, 1]] = base[pairs[:, 1]] + e[pairs[:, 0] - 1]
    else:
        with torch.no_grad():
            for start in range(0, len(pairs), chunk):
                selected = pairs[start : start + chunk]
                x = windows(
                    base,
                    features,
                    labels,
                    h,
                    selected,
                    physical_scaler,
                    history_scaler,
                    strategy,
                )
                mean[selected[:, 1]] = base[selected[:, 1]] + model(x).numpy()
    if not np.isfinite(mean[29:]).all():
        raise ArithmeticError("Nonfinite history-conditioned prediction")
    return mean


def pair_table(pairs, h):
    origins, targets = pairs.T
    return pd.DataFrame(
        dict(
            origin=origins,
            target=targets,
            lead=targets - origins,
            last_observation_index=origins - 1,
            teacher_fit_days=h,
            part=np.where(targets < h, "train", "prediction"),
        )
    )


def compare(metrics):
    indexed = metrics.set_index(["outer_days", "strategy", "station", "part"])
    rows = []
    for n in CUTOFFS:
        for strategy in ("A", "C", "H"):
            for station in POINTS:
                row = dict(outer_days=n, strategy=strategy, station=station)
                for reference in ("P0", "A", "C"):
                    for part in ("train", "prediction"):
                        for metric in ("rmse_mm", "mae_mm", "crps_mm"):
                            row[f"{part}_{metric}_minus_{reference}"] = float(
                                indexed.loc[(n, strategy, station, part), metric]
                                - indexed.loc[(n, reference, station, part), metric]
                            )
                row["strict_mean_improvement"] = all(
                    row[f"{part}_{metric}_minus_P0"] < -1e-6
                    for part in ("train", "prediction")
                    for metric in ("rmse_mm", "mae_mm")
                )
                rows.append(row)
    return pd.DataFrame(rows)
