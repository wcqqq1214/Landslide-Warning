"""Frozen v2.0 direct multi-horizon ConvLSTM and conditional Gaussian scale."""

import numpy as np
import pandas as pd
import torch
from torch import nn
from torch.nn import functional as F

from physics_guided.models import initialize
from physics_guided.probability import crps, interval_score, summarize
from physics_guided.training import update
from physics_guided_balanced_origin.core import profile
from physics_guided_history_learning.core import evaluation_pairs
from physics_guided_sample_learning.core import POINTS
from physics_guided_sequence.core import SequenceM1, valid_input
from physics_guided_sequence_learning.core import block_losses, make_batch


class DirectConvLSTM(nn.Module):
    """One historical ConvLSTM encoding reaches every lead without recurrence."""

    def __init__(self):
        super().__init__()
        self.encoder = SequenceM1()
        del self.encoder.output  # Only its frozen cell and history API are reused.
        self.head = nn.Sequential(
            nn.Conv2d(37, 16, 1, dtype=torch.float64),
            nn.Tanh(),
            nn.Conv2d(16, 1, 1, dtype=torch.float64),
        )
        initialize(self.head, self.head[-1])

    def forward(self, history, future):
        valid_input(future)
        if history.shape[0] != future.shape[0]:
            raise ValueError("History and future batch identities differ")
        hidden, _ = self.encoder.encode(history)
        batch, steps = future.shape[:2]
        context = hidden[:, None].expand(-1, steps, -1, -1, -1)
        known = torch.cat((future[:, :, :20], future[:, :, 22:23]), dim=2)
        values = torch.cat((context, known), dim=2).reshape(batch * steps, 37, 1, 4)
        return (100 * self.head(values)[:, 0, 0]).reshape(batch, steps, 4)


def mean_step(model, op, batch):
    op.zero_grad(set_to_none=True)
    losses = block_losses(batch, model(batch.encoder, batch.decoder))
    parameters = tuple(model.parameters())
    gradients = []
    for b in (0, 1):
        values = torch.autograd.grad(losses[b], parameters, retain_graph=b == 0)
        gradients.append(torch.cat([v.reshape(-1) for v in values]).numpy())
    record, mixed = profile(losses.detach().numpy(), np.stack(gradients))
    start = 0
    for p in parameters:
        end = start + p.numel()
        p.grad = torch.from_numpy(mixed[start:end].reshape(p.shape).copy())
        start = end
    record["clipped_from"] = update(model, op)
    return record


def evaluation_batch(base, features, labels, h, scalers):
    if labels.shape != (h, 4):
        raise ValueError("Evaluation inputs must contain the exact history prefix")
    pairs = evaluation_pairs(h, h + 180)
    table = pd.DataFrame(dict(origin=pairs[:, 0], target=pairs[:, 1], teacher=h))
    return table, make_batch(
        table, {h: (base, features)}, labels, scalers, "teacher", False
    )


def predict(model, batch, base, targets):
    with torch.no_grad():
        correction = model(batch.encoder, batch.decoder)
    mean = np.full_like(base, np.nan)
    mean[30] = base[30]
    mean[targets] = base[targets] + correction[batch.row_batch, batch.row_lead].numpy()
    if not np.isfinite(mean[30:]).all():
        raise ArithmeticError("Incomplete direct mean")
    return mean


def scale_features(base, mean, labels, origins, targets):
    """Stable five-channel features; only labels strictly before each origin."""
    origins, targets = np.asarray(origins), np.asarray(targets)
    if (
        origins.dtype.kind not in "iu"
        or targets.dtype.kind not in "iu"
        or origins.shape != targets.shape
        or origins.ndim != 1
        or len(origins) == 0
        or base.shape != mean.shape
        or base.shape[1:] != (4,)
        or labels.ndim != 2
        or labels.shape[1:] != (4,)
        or (origins < 30).any()
        or (origins > len(labels)).any()
        or (targets < origins).any()
        or (targets - origins >= 180).any()
        or (targets >= len(base)).any()
    ):
        raise ValueError("Invalid scale feature boundary or point identity")
    last = labels[origins - 1] - base[origins - 1]
    spread = np.stack(
        [np.std(labels[o - 30 : o] - base[o - 30 : o], axis=0) for o in origins]
    )
    values = np.stack(
        [
            np.broadcast_to((targets - origins)[:, None] / 179, last.shape),
            np.tanh((base[targets] - base[origins - 1]) / 100),
            np.tanh((mean[targets] - base[targets]) / 100),
            np.tanh(last / 100),
            np.tanh(spread / 100),
        ],
        axis=-1,
    )
    if not np.isfinite(values).all():
        raise ValueError("Nonfinite required scale input")
    return torch.tensor(values, dtype=torch.float64)


class ConditionalScale(nn.Module):
    def __init__(self, initial_sigma):
        super().__init__()
        initial = torch.as_tensor(initial_sigma, dtype=torch.float64)
        if (
            initial.shape != (4,)
            or not torch.isfinite(initial).all()
            or (initial < 0.002).any()
        ):
            raise ValueError("Four finite initial scales of at least 0.002 mm required")
        self.network = nn.Sequential(
            nn.Linear(5, 8, dtype=torch.float64),
            nn.Tanh(),
            nn.Linear(8, 1, dtype=torch.float64),
        )
        initialize(self.network, self.network[-1])
        x = (initial - 0.001) / 100
        self.intercept = nn.Parameter(x + torch.log(-torch.expm1(-x)))

    def forward(self, values):
        if (
            values.ndim != 3
            or values.shape[1:] != (4, 5)
            or not torch.isfinite(values).all()
        ):
            raise ValueError("Finite [time,point,feature] scale input required")
        return 0.001 + 100 * F.softplus(self.network(values)[..., 0] + self.intercept)


def scale_step(model, op, features, frozen_mean, labels):
    if (
        frozen_mean.requires_grad
        or labels.requires_grad
        or frozen_mean.shape != labels.shape
    ):
        raise ValueError("Scale training requires matching frozen means and labels")
    op.zero_grad(set_to_none=True)
    sigma = model(features)
    loss = (torch.log(sigma) + 0.5 * ((labels - frozen_mean) / sigma).square()).mean()
    if not torch.isfinite(loss):
        raise ArithmeticError("Nonfinite Gaussian scale loss")
    loss.backward()
    return dict(nll=float(loss.detach()), clipped_from=update(model, op))


def score(means, sigmas, labels, n, strategy):
    if (
        means.shape != sigmas.shape
        or means.shape[1:] != (n + 180, 4)
        or labels.shape != (n + 180, 4)
    ):
        raise ValueError("Complete two-part prediction arrays required")
    summary = summarize(means[:, 30:], sigmas[:, 30:])
    values = crps(means[:, 30:], sigmas[:, 30:], labels[30:])
    rows = []
    for part, start, end in (("train", 30, n), ("prediction", n, n + 180)):
        loc = slice(start - 30, end - 30)
        error = summary["mean"][loc] - labels[start:end]
        for j, station in enumerate(POINTS):
            row = dict(
                outer_days=n,
                strategy=strategy,
                part=part,
                station=station,
                days=end - start,
                rmse_mm=float(np.sqrt(np.mean(error[:, j] ** 2))),
                mae_mm=float(np.mean(abs(error[:, j]))),
                crps_mm=float(values[loc, j].mean()),
            )
            for level in (80, 90, 95):
                lower, upper = (
                    summary[f"{edge}_{level}"][loc, j] for edge in ("lower", "upper")
                )
                y = labels[start:end, j]
                row[f"coverage_{level}"] = float(np.mean((y >= lower) & (y <= upper)))
                row[f"width_{level}_mm"] = float(np.mean(upper - lower))
                row[f"interval_score_{level}_mm"] = float(
                    interval_score(y, lower, upper, level).mean()
                )
            rows.append(row)
    return rows, summary


def decide(metrics, tol=1e-6, coverage_slack=1 / 180):
    keys = ["outer_days", "strategy", "part", "station"]
    if len(metrics) != 32 or metrics.duplicated(keys).any():
        raise ValueError("Exactly 32 unique baseline/candidate metric rows required")
    indexed = metrics.set_index(keys)
    points, windows = [], []
    for n in (432, 612):
        for station in POINTS:
            row = dict(outer_days=n, station=station)
            for part in ("train", "prediction"):
                a = indexed.loc[(n, "DIRECT", part, station)]
                b = indexed.loc[(n, "P0", part, station)]
                for metric in ("rmse_mm", "mae_mm", "crps_mm"):
                    row[f"{part}_{metric}_minus_P0"] = float(a[metric] - b[metric])
            a = indexed.loc[(n, "DIRECT", "prediction", station)]
            b = indexed.loc[(n, "P0", "prediction", station)]
            da, db = abs(a.coverage_90 - 0.9), abs(b.coverage_90 - 0.9)
            row["strict_mean_improvement"] = all(
                row[f"{p}_{m}_minus_P0"] < -tol
                for p in ("train", "prediction")
                for m in ("rmse_mm", "mae_mm")
            )
            row["coverage_guard"] = bool(da <= db + coverage_slack + 1e-12)
            row["width_guard"] = bool(
                a.width_90_mm <= b.width_90_mm + tol
                or (
                    da < db - 1e-12
                    and a.interval_score_90_mm < b.interval_score_90_mm - tol
                )
            )
            points.append(row)
        a = metrics[
            (metrics.outer_days == n)
            & (metrics.strategy == "DIRECT")
            & (metrics.part == "prediction")
        ]
        b = metrics[
            (metrics.outer_days == n)
            & (metrics.strategy == "P0")
            & (metrics.part == "prediction")
        ]
        row = dict(outer_days=n)
        for key in (
            "rmse_mm",
            "mae_mm",
            "crps_mm",
            "coverage_90",
            "width_90_mm",
            "interval_score_90_mm",
        ):
            row[key] = float(a[key].mean())
            row[key + "_P0"] = float(b[key].mean())
        row["probability_scores_improved"] = all(
            row[k] < row[k + "_P0"] - tol for k in ("crps_mm", "interval_score_90_mm")
        )
        windows.append(row)
    strict = sum(p["strict_mean_improvement"] for p in points)
    passed = (
        strict == 8
        and all(w["probability_scores_improved"] for w in windows)
        and all(p["coverage_guard"] and p["width_guard"] for p in points)
    )
    return dict(
        passed=passed,
        strict_mean_improvement_count=strict,
        point_windows=points,
        windows=windows,
        next_action="stop; no automatic extension",
        evidence_status="exploratory exposed historical windows; no user or adviser acceptance",
    )
