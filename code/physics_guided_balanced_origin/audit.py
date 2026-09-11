"""Scalar balancing algebra, all-step logs, and independent checkpoint gradients."""

import math

import numpy as np
import pandas as pd

from physics_guided_origin_gradients.verify import replay_gradients


def rule(a, p):
    active = [a > 1e-12, p > 1e-12]
    if not any(active):
        return [0.5, 0.5]
    if not all(active):
        return [float(x) for x in active]
    return [1 / (1 + a / p), 1 / (1 + p / a)]


def scalar_profile(losses, gradients):
    a, p = gradients.tolist()

    def norm(values):
        return math.sqrt(math.fsum(x * x for x in values))

    na, np_ = norm(a), norm(p)
    qa, qp = rule(na, np_)
    g = [qa * x + qp * y for x, y in zip(a, p)]
    return dict(
        loss=math.fsum(float(x) for x in losses) / 2,
        anchor_loss=float(losses[0]),
        paired_loss=float(losses[1]),
        balanced_loss=qa * float(losses[0]) + qp * float(losses[1]),
        anchor_weight=qa,
        paired_weight=qp,
        anchor_norm=na,
        paired_norm=np_,
        gradient_norm=norm(g),
        gradient_dot=math.fsum(x * y for x, y in zip(a, p)),
        raw_anchor_slope=-math.fsum(x * y for x, y in zip(a, g)),
        raw_paired_slope=-math.fsum(x * y for x, y in zip(p, g)),
    )


def checkpoint(model, samples, budget):
    losses, gradients = replay_gradients(model, samples, 53, budget)
    return scalar_profile(losses, gradients)


def verify_logs(logs, summaries, stored_diagnostics):
    rows = []
    for (h, s, seed), block in logs.groupby(
        ["fit_days", "strategy", "seed"], sort=False
    ):
        if block.epoch.tolist() != list(range(1, 101)):
            raise ValueError("Update sequence differs")
        final = summaries[
            (summaries.fit_days == h)
            & (summaries.strategy == s)
            & (summaries.seed == seed)
        ]
        if len(final) != 1:
            raise ValueError("Final summary missing")
        values = block.to_dict("records")
        for i, row in enumerate(values):
            a, p, qa, qp = (
                float(row[k])
                for k in (
                    "anchor_norm",
                    "paired_norm",
                    "anchor_weight",
                    "paired_weight",
                )
            )
            expected = rule(a, p)
            if not np.allclose(
                [qa, qp], expected, rtol=1e-10, atol=1e-12
            ) or not math.isclose(qa + qp, 1, rel_tol=0, abs_tol=1e-12):
                raise ArithmeticError("Training-only weighting rule differs")
            if not math.isclose(
                row["loss_before_update"],
                (row["anchor_loss"] + row["paired_loss"]) / 2,
                rel_tol=1e-10,
                abs_tol=1e-10,
            ):
                raise ArithmeticError("Equal-weight readout differs")
            if not math.isclose(
                row["balanced_loss"],
                qa * row["anchor_loss"] + qp * row["paired_loss"],
                rel_tol=1e-10,
                abs_tol=1e-10,
            ):
                raise ArithmeticError("Current weighted readout differs")
            square = (
                qa * qa * a * a + qp * qp * p * p + 2 * qa * qp * row["gradient_dot"]
            )
            if not math.isclose(
                row["gradient_norm"] ** 2, square, rel_tol=1e-9, abs_tol=1e-12
            ):
                raise ArithmeticError("Gradient norm decomposition differs")
            if row["raw_anchor_slope"] > 1e-12 or row["raw_paired_slope"] > 1e-12:
                raise ArithmeticError(
                    "Untransformed gradient fails the local nonincrease property"
                )
            if row["update_norm"] < 0 or row["gradient_norm"] < 0:
                raise ArithmeticError("Negative update/gradient norm")
            next_a = (
                values[i + 1]["anchor_loss"]
                if i < 99
                else float(final.final_anchor_loss.iloc[0])
            )
            next_p = (
                values[i + 1]["paired_loss"]
                if i < 99
                else float(final.final_paired_loss.iloc[0])
            )
            da, dp = next_a - row["anchor_loss"], next_p - row["paired_loss"]
            rows.append(
                dict(
                    fit_days=h,
                    strategy=s,
                    seed=seed,
                    epoch=i + 1,
                    actual_anchor_change=da,
                    actual_paired_change=dp,
                    linear_anchor_change=row["linear_anchor_change"],
                    linear_paired_change=row["linear_paired_change"],
                    actual_both_decrease=bool(da < -1e-12 and dp < -1e-12),
                    actual_anchor_increases=bool(da > 1e-12),
                    actual_paired_increases=bool(dp > 1e-12),
                )
            )
    pd.testing.assert_frame_equal(
        pd.DataFrame(rows), stored_diagnostics, check_exact=False, rtol=1e-9, atol=1e-10
    )
