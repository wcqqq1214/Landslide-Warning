"""Independent probability arithmetic and checks of saved balancing identities."""

import math

import numpy as np
import pandas as pd
from scipy.stats import norm

from physics_guided_sample_learning.core import CUTOFFS, POINTS
from physics_guided_synchronized_correction.verify import independent_crps
from .support import STRATEGIES, require


def close(actual, expected, atol=1e-8, rtol=0):
    np.testing.assert_allclose(actual, expected, atol=atol, rtol=rtol)


def table_close(actual, expected, atol=1e-8):
    require(
        list(actual.columns) == list(expected.columns)
        and actual.shape == expected.shape,
        "Metric table layout differs",
    )
    for column in expected:
        if expected[column].dtype.kind == "f":
            close(actual[column], expected[column], atol=atol)
        else:
            np.testing.assert_array_equal(actual[column], expected[column])


def probability_rows(means, scales, labels, n, strategy, distribution):
    require(
        means.ndim == 3
        and means.shape[1:] == (n + 180, 4)
        and scales.shape == (len(means), 4)
        and labels.shape == (n + 180, 4)
        and np.isfinite(means[:, 30:]).all()
        and np.isfinite(scales).all()
        and (scales >= 0.001).all(),
        "Invalid probability components",
    )
    mu, y = means[:, 30:], labels[30:]
    center = np.mean(mu, axis=0)
    spread = np.sqrt(np.mean(scales[:, None] ** 2 + (mu - center) ** 2, axis=0))
    require(
        set(distribution)
        == {
            "mean",
            "std",
            *[
                f"{side}_{level}"
                for side in ("lower", "upper")
                for level in (80, 90, 95)
            ],
        },
        "Incomplete saved probability distribution",
    )
    close(distribution["mean"], center)
    close(distribution["std"], spread)
    max_cdf_error = 0.0
    for level in (80, 90, 95):
        for side, probability in (
            ("lower", (1 - level / 100) / 2),
            ("upper", (1 + level / 100) / 2),
        ):
            bound = distribution[f"{side}_{level}"]
            require(
                bound.shape == center.shape and np.isfinite(bound).all(),
                "Invalid mixture interval",
            )
            cdf = norm.cdf((bound[None] - mu) / scales[:, None]).mean(axis=0)
            error = np.abs(cdf - probability)
            tolerance = 1e-6 / (scales.min(axis=0) * math.sqrt(2 * math.pi)) + 1e-12
            require(
                (error <= tolerance).all(),
                "Saved quantile fails independent mixture CDF",
            )
            max_cdf_error = max(max_cdf_error, float(error.max()))
        require(
            (distribution[f"upper_{level}"] >= distribution[f"lower_{level}"]).all(),
            "Reversed interval",
        )
    for small, large in ((80, 90), (90, 95)):
        require(
            (distribution[f"lower_{large}"] <= distribution[f"lower_{small}"]).all()
            and (
                distribution[f"upper_{large}"] >= distribution[f"upper_{small}"]
            ).all(),
            "Non-nested central intervals",
        )
    crps = independent_crps(mu, scales, y)
    rows = []
    for part, start, stop in (("train", 30, n), ("prediction", n, n + 180)):
        loc = slice(start - 30, stop - 30)
        for j, station in enumerate(POINTS):
            error = center[loc, j] - y[loc, j]
            row = dict(
                outer_days=n,
                strategy=strategy,
                part=part,
                station=station,
                days=stop - start,
                rmse_mm=math.sqrt(float(np.mean(error**2))),
                mae_mm=float(np.mean(np.abs(error))),
                crps_mm=float(crps[loc, j].mean()),
            )
            for level in (80, 90, 95):
                low, high = (
                    distribution[f"{side}_{level}"][loc, j]
                    for side in ("lower", "upper")
                )
                observed = y[loc, j]
                width = high - low
                row[f"coverage_{level}"] = float(
                    np.mean((observed >= low) & (observed <= high))
                )
                row[f"width_{level}_mm"] = float(width.mean())
                row[f"interval_score_{level}_mm"] = float(
                    np.mean(
                        width
                        + 2
                        / (1 - level / 100)
                        * (
                            np.maximum(low - observed, 0)
                            + np.maximum(observed - high, 0)
                        )
                    )
                )
            rows.append(row)
    return rows, max_cdf_error


def comparisons(metrics, reference):
    current = metrics.set_index(["outer_days", "strategy", "station", "part"])
    old = reference.set_index(["outer_days", "strategy", "station", "part"])
    require(
        current.index.is_unique and old.index.is_unique,
        "Duplicate reference metric rows",
    )
    rows = []
    for n in CUTOFFS:
        for strategy in STRATEGIES:
            source = strategy.split("_")[0]
            for station in POINTS:
                row = dict(outer_days=n, strategy=strategy, station=station)
                for part in ("train", "prediction"):
                    chosen = current.loc[(n, strategy, station, part)]
                    references = {
                        "P0": current.loc[(n, "P0", station, part)],
                        "RESET": current.loc[(n, source + "_RESET", station, part)],
                        "NORM": old.loc[(n, source, station, part)],
                    }
                    for name, value in references.items():
                        for metric in ("rmse_mm", "mae_mm", "crps_mm"):
                            row[f"{part}_{metric}_minus_{name}"] = float(
                                chosen[metric] - value[metric]
                            )
                row["strict_mean_improvement"] = all(
                    row[f"{part}_{metric}_minus_P0"] < -1e-6
                    for part in ("train", "prediction")
                    for metric in ("rmse_mm", "mae_mm")
                )
                rows.append(row)
    return pd.DataFrame(rows)


def verify_logs(log, summaries, spec):
    require(
        len(log) == spec["main_limits"]["neural_updates"] and len(summaries) == 36,
        "Incomplete training record",
    )
    require(
        np.isfinite(log.select_dtypes(include="number")).all().all()
        and (log.elapsed_seconds.diff().dropna() >= 0).all(),
        "Invalid training log",
    )
    diagnostics = []
    for h in spec["prefixes"]:
        for strategy in STRATEGIES:
            for seed in spec["seeds"]:
                rows = log[
                    (log.fit_days == h)
                    & (log.strategy == strategy)
                    & (log.seed == seed)
                ]
                summary = summaries[
                    (summaries.fit_days == h)
                    & (summaries.strategy == strategy)
                    & (summaries.seed == seed)
                ]
                require(len(summary) == 1, "Missing model summary")
                summary = summary.iloc[0]
                np.testing.assert_array_equal(rows.epoch, np.arange(1, 101))
                for row in rows.itertuples():
                    a, p, dot = row.anchor_norm, row.paired_norm, row.gradient_dot
                    require(
                        a >= 0
                        and p >= 0
                        and row.anchor_loss >= 0
                        and row.paired_loss >= 0,
                        "Invalid block losses or norms",
                    )
                    if a <= 1e-12 and p <= 1e-12:
                        qa, qp = 0.5, 0.5
                    elif a <= 1e-12:
                        qa, qp = 0.0, 1.0
                    elif p <= 1e-12:
                        qa, qp = 1.0, 0.0
                    else:
                        qa, qp = p / (a + p), a / (a + p)
                    close([row.anchor_weight, row.paired_weight], [qa, qp], atol=1e-14)
                    require(
                        abs(dot) <= a * p + 1e-10 * max(1, a * p),
                        "Invalid gradient Gram matrix",
                    )
                    expected = [
                        0.5 * (row.anchor_loss + row.paired_loss),
                        qa * row.anchor_loss + qp * row.paired_loss,
                        qa * qa * a * a + qp * qp * p * p + 2 * qa * qp * dot,
                        -(qa * a * a + qp * dot),
                        -(qa * dot + qp * p * p),
                    ]
                    close(
                        [
                            row.loss,
                            row.balanced_loss,
                            row.gradient_norm**2,
                            row.raw_anchor_slope,
                            row.raw_paired_slope,
                        ],
                        expected,
                        atol=1e-10,
                        rtol=1e-9,
                    )
                close(
                    rows.iloc[0][["anchor_loss", "paired_loss"]].to_numpy(float),
                    summary[["initial_anchor_loss", "initial_paired_loss"]].to_numpy(
                        float
                    ),
                )
                values = np.vstack(
                    [
                        rows[["anchor_loss", "paired_loss"]].to_numpy(),
                        summary[["final_anchor_loss", "final_paired_loss"]].to_numpy(
                            float
                        ),
                    ]
                )
                change = np.diff(values, axis=0)
                diagnostics.append(
                    dict(
                        fit_days=h,
                        strategy=strategy,
                        seed=seed,
                        both_blocks_decreased=int(np.all(change < 0, axis=1).sum()),
                        anchor_increased=int((change[:, 0] > 0).sum()),
                        paired_increased=int((change[:, 1] > 0).sum()),
                        final_anchor_below_initial=bool(values[-1, 0] < values[0, 0]),
                        final_paired_below_initial=bool(values[-1, 1] < values[0, 1]),
                    )
                )
    return pd.DataFrame(diagnostics)
