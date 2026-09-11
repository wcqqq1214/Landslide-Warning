"""Explicit paired sample sources and two equally weighted supervised blocks."""

from dataclasses import dataclass

import numpy as np
import pandas as pd
import torch

from physics_guided.training import update
from physics_guided_history_learning.core import training_pairs, windows

ORIGINS = {342: (252,), 432: (252, 342), 612: (252, 342, 432)}
STRATEGIES = ("P0", "IN", "OOF")


def query_table(h):
    if h not in ORIGINS:
        raise ValueError("Unregistered training prefix")
    anchors = training_pairs(h)
    paired = np.array([(o, t) for o in ORIGINS[h] for t in range(o, min(o + 180, h))])
    dates, counts = np.unique(paired[:, 1], return_counts=True)
    multiplicity = dict(zip(dates, counts))
    pairs = np.concatenate([anchors, paired])
    blocks = np.r_[np.zeros(len(anchors), dtype=int), np.ones(len(paired), dtype=int)]
    weights = np.r_[
        np.full(len(anchors), 0.5 / len(anchors)),
        [0.5 / (len(dates) * multiplicity[t]) for _, t in paired],
    ]
    return pd.DataFrame(
        dict(
            origin=pairs[:, 0],
            target=pairs[:, 1],
            lead=pairs[:, 1] - pairs[:, 0],
            block=blocks,
            teacher_IN=h,
            teacher_OOF=np.where(blocks == 0, h, pairs[:, 0]),
            last_observation_index=pairs[:, 0] - 1,
            weight=weights,
        )
    )


@dataclass
class Samples:
    x: torch.Tensor
    base: torch.Tensor
    target: torch.Tensor
    weights: torch.Tensor
    blocks: np.ndarray

    def payload(self):
        return dict(
            base=self.base.numpy(),
            target=self.target.numpy(),
            weights=self.weights.numpy(),
            blocks=self.blocks,
        )


def make_samples(strategy, h, labels, teachers, scalers):
    if (
        strategy not in ("IN", "OOF")
        or h not in ORIGINS
        or labels.shape != (h, 4)
        or not np.isfinite(labels).all()
    ):
        raise ValueError("Training labels must end at the registered prefix")
    if set(teachers) != set(ORIGINS[h]) | {h}:
        raise ValueError("Unexpected physical teacher set")
    for o, (base, features) in teachers.items():
        if (
            base.shape != (o + 180, 4)
            or features.shape != (o + 180, 20, 4)
            or not np.isfinite(base).all()
            or not np.isfinite(features).all()
        ):
            raise ValueError("A frozen teacher trajectory is incomplete")
    table = query_table(h)
    pairs = table[["origin", "target"]].to_numpy()
    x, bases = [], []
    # Keep original row order: shared anchors, then each complete origin window.
    groups = [np.flatnonzero(table.block.to_numpy() == 0)]
    groups.extend(
        np.flatnonzero((table.block.to_numpy() == 1) & (table.origin.to_numpy() == o))
        for o in ORIGINS[h]
    )
    for indices in groups:
        group = table.iloc[indices]
        teacher = int(group[f"teacher_{strategy}"].iloc[0])
        if group[f"teacher_{strategy}"].nunique() != 1:
            raise ValueError("Mixed teachers inside an origin block")
        base, features = teachers[teacher]
        bound = teacher if strategy == "OOF" and group.block.iloc[0] == 1 else h
        x.append(
            windows(
                base, features, labels[:bound], bound, pairs[indices], *scalers, "H"
            )
        )
        bases.append(base[pairs[indices, 1]])
    return Samples(
        torch.cat(x),
        torch.as_tensor(np.concatenate(bases)),
        torch.as_tensor(labels[pairs[:, 1]]),
        torch.as_tensor(table.weight.to_numpy()),
        table.block.to_numpy(),
    )


def backward_loss(model, samples, chunk=128):
    if type(chunk) is not int or chunk < 1:
        raise ValueError("Positive integer batch size required")
    total = 0.0
    for start in range(0, len(samples.weights), chunk):
        end = min(start + chunk, len(samples.weights))
        residual = (
            samples.base[start:end]
            + model(samples.x[start:end])
            - samples.target[start:end]
        ) / 100
        loss = (residual.square() * samples.weights[start:end, None]).sum() / 4
        if not torch.isfinite(loss):
            raise ArithmeticError("Nonfinite weighted learning loss")
        loss.backward()
        total += float(loss.detach())
    return total


def mean_step(model, op, samples):
    op.zero_grad(set_to_none=True)
    loss = backward_loss(model, samples)
    return loss, update(model, op)


def loss_components(samples, correction):
    error = ((samples.base.numpy() + correction - samples.target.numpy()) / 100) ** 2
    weighted = error.mean(axis=1) * samples.weights.numpy()
    return dict(
        loss=float(weighted.sum()),
        anchor_loss=float(weighted[samples.blocks == 0].sum() / 0.5),
        paired_loss=float(weighted[samples.blocks == 1].sum() / 0.5),
    )


def final_losses(model, samples, chunk=128):
    with torch.no_grad():
        values = np.concatenate(
            [
                model(samples.x[start : start + chunk]).numpy()
                for start in range(0, len(samples.weights), chunk)
            ]
        )
    return loss_components(samples, values)
