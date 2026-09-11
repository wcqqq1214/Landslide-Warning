"""Full-block gradients and detached, normalized weights before the original Adam."""

import math

import numpy as np
import pandas as pd
import torch

from physics_guided.training import update
from physics_guided_history_learning.core import predict
from physics_guided_origin_gradients.core import block_gradients
from physics_guided_origin_learning.core import (
    STRATEGIES as STRATEGIES,
    make_samples as make_samples,
    query_table as query_table,
    loss_components as loss_components,
)

ZERO = 1e-12


def dot(a, b):
    return math.fsum(float(x) * float(y) for x, y in zip(a, b))


def weights(a, p):
    if not math.isfinite(a) or not math.isfinite(p) or a < 0 or p < 0:
        raise ValueError("Finite nonnegative gradient norms required")
    if a <= ZERO and p <= ZERO:
        return 0.5, 0.5
    if a <= ZERO:
        return 0.0, 1.0
    if p <= ZERO:
        return 1.0, 0.0
    return p / (a + p), a / (a + p)


def profile(losses, gradients):
    if (
        np.shape(losses) != (2,)
        or gradients.ndim != 2
        or gradients.shape[0] != 2
        or not np.isfinite(losses).all()
        or not np.isfinite(gradients).all()
    ):
        raise ValueError("Two finite block losses and gradients required")
    a, p = (math.sqrt(dot(g, g)) for g in gradients)
    qa, qp = weights(a, p)
    mixed = qa * gradients[0] + qp * gradients[1]
    row = dict(
        loss=float(np.mean(losses)),
        anchor_loss=float(losses[0]),
        paired_loss=float(losses[1]),
        balanced_loss=float(qa * losses[0] + qp * losses[1]),
        anchor_weight=qa,
        paired_weight=qp,
        anchor_norm=a,
        paired_norm=p,
        gradient_norm=math.sqrt(dot(mixed, mixed)),
        gradient_dot=dot(*gradients),
        raw_anchor_slope=-dot(gradients[0], mixed),
        raw_paired_slope=-dot(gradients[1], mixed),
    )
    return row, mixed


def mean_step(model, op, samples, budget):
    op.zero_grad(set_to_none=True)
    losses, gradients = block_gradients(model, samples, 128, budget)
    row, mixed = profile(losses, gradients)
    parameters = tuple(model.parameters())
    before = torch.nn.utils.parameters_to_vector(parameters).detach().clone()
    start = 0
    for parameter in parameters:
        stop = start + parameter.numel()
        parameter.grad = (
            torch.as_tensor(mixed[start:stop]).reshape(parameter.shape).clone()
        )
        start = stop
    if start != len(mixed):
        raise ValueError("Gradient layout differs from the model")
    norm = update(model, op)
    if not math.isclose(norm, row["gradient_norm"], rel_tol=1e-9, abs_tol=1e-10):
        raise ArithmeticError("Original clipping norm differs from mixed gradient")
    delta = (torch.nn.utils.parameters_to_vector(parameters).detach() - before).numpy()
    row.update(
        update_norm=math.sqrt(dot(delta, delta)),
        linear_anchor_change=dot(gradients[0], delta),
        linear_paired_change=dot(gradients[1], delta),
    )
    return row


def final_losses(model, samples, budget):
    losses, gradients = block_gradients(model, samples, 128, budget)
    row, _ = profile(losses, gradients)
    return row


def predict_and_count(model, base, features, labels, h, scalers, state):
    def count(*_):
        state["prediction_forward_calls"] += 1

    handle = model.register_forward_hook(count)
    try:
        return predict(model, base, features, labels, h, *scalers, "H")
    finally:
        handle.remove()


def optimizer_diagnostics(logs, summaries):
    records = []
    for (h, strategy, seed), group in logs.groupby(
        ["fit_days", "strategy", "seed"], sort=False
    ):
        if group.epoch.tolist() != list(range(1, 101)):
            raise ValueError("Complete ordered model update log required")
        final = summaries[
            (summaries.fit_days == h)
            & (summaries.strategy == strategy)
            & (summaries.seed == seed)
        ]
        if len(final) != 1:
            raise ValueError("Exactly one final block loss record required")
        a = np.r_[group.anchor_loss.to_numpy(), final.final_anchor_loss.iloc[0]]
        p = np.r_[group.paired_loss.to_numpy(), final.final_paired_loss.iloc[0]]
        for i, row in enumerate(group.itertuples()):
            da, dp = float(a[i + 1] - a[i]), float(p[i + 1] - p[i])
            records.append(
                dict(
                    fit_days=h,
                    strategy=strategy,
                    seed=seed,
                    epoch=int(row.epoch),
                    actual_anchor_change=da,
                    actual_paired_change=dp,
                    linear_anchor_change=row.linear_anchor_change,
                    linear_paired_change=row.linear_paired_change,
                    actual_both_decrease=bool(da < -1e-12 and dp < -1e-12),
                    actual_anchor_increases=bool(da > 1e-12),
                    actual_paired_increases=bool(dp > 1e-12),
                )
            )
    return pd.DataFrame(records)


def compare_equal(current, reference):
    keys = ["outer_days", "strategy", "station", "part"]
    if current.duplicated(keys).any() or reference.duplicated(keys).any():
        raise ValueError("Metric comparison requires unique point/part rows")
    old = reference.set_index(keys)
    rows = []
    for n in (432, 612):
        for strategy in ("IN", "OOF"):
            for station in ("ATU1", "ATU5", "MJ3", "MJ1"):
                row = dict(outer_days=n, strategy=strategy, station=station)
                for part in ("train", "prediction"):
                    selection = current[
                        (current.outer_days == n)
                        & (current.strategy == strategy)
                        & (current.station == station)
                        & (current.part == part)
                    ]
                    if len(selection) != 1:
                        raise ValueError("Incomplete current metric row")
                    new = selection.iloc[0]
                    for metric in ("rmse_mm", "mae_mm", "crps_mm"):
                        row[f"{part}_{metric}_minus_EQ"] = float(
                            new[metric] - old.loc[(n, strategy, station, part), metric]
                        )
                row["strict_mean_improvement_over_EQ"] = all(
                    row[f"{part}_{metric}_minus_EQ"] < -1e-6
                    for part in ("train", "prediction")
                    for metric in ("rmse_mm", "mae_mm")
                )
                rows.append(row)
    return pd.DataFrame(rows)
