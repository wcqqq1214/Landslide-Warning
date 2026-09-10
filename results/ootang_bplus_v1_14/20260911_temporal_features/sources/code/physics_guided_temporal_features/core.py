"""Causal features and fixed-penalty four-point residual regression."""

import numpy as np
import pandas as pd

POINTS = ("ATU1", "ATU5", "MJ3", "MJ1")
DIMENSIONS = {"H": 12, "HH": 24, "HHS": 48}
HYDRO_FLOORS = np.array([1, 1, 1, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 1])
FLOORS = np.r_[HYDRO_FLOORS, HYDRO_FLOORS, np.ones(24)]


class Budget:
    def __init__(self, spec):
        self.limits = {
            k: spec["max_" + k]
            for k in ("ridge_fits", "constant_estimates", "feature_scalers")
        }
        self.counts = dict.fromkeys(self.limits, 0)

    def take(self, name):
        if self.counts[name] >= self.limits[name]:
            raise RuntimeError("Registered fit budget exhausted before computation")
        self.counts[name] += 1


def trailing_mean(values, width=30):
    values = np.asarray(values, dtype=float)
    if (
        values.ndim != 2
        or not len(values)
        or width < 1
        or not np.isfinite(values).all()
    ):
        raise ValueError("Finite daily features and a positive history width required")
    sums = np.vstack([np.zeros_like(values[:1]), np.cumsum(values, axis=0)])
    ends = np.arange(1, len(values) + 1)
    starts = np.maximum(0, ends - width)
    return (sums[ends] - sums[starts]) / (ends - starts)[:, None]


def standardize(fit_features, expected_rows, budget):
    x = np.asarray(fit_features, dtype=float)
    if expected_rows < 1 or x.shape != (expected_rows, 48) or not np.isfinite(x).all():
        raise ValueError("Scaler accepts only its exact complete training prefix")
    budget.take("feature_scalers")
    return dict(
        mean=x.mean(axis=0),
        std=np.maximum(x.std(axis=0, ddof=0), FLOORS),
        floors=FLOORS.copy(),
    )


def design(x):
    x = np.asarray(x, dtype=float)
    if x.ndim != 2 or not len(x) or not np.isfinite(x).all():
        raise ValueError("A finite feature matrix is required")
    return np.column_stack([np.ones(len(x)), x])


def ridge_fit(x, demand, expected_rows, ridge_lambda, target_scale, budget):
    x, demand = np.asarray(x, dtype=float), np.asarray(demand, dtype=float)
    if (
        expected_rows < 1
        or x.ndim != 2
        or len(x) != expected_rows
        or demand.shape != (expected_rows, 4)
    ):
        raise ValueError("Regression accepts only exact matched training rows")
    if (
        not np.isfinite(demand).all()
        or not np.isfinite([ridge_lambda, target_scale]).all()
        or ridge_lambda <= 0
        or target_scale <= 0
    ):
        raise ValueError("Finite targets and positive fixed regularization required")
    phi = design(x)
    budget.take("ridge_fits")
    gram = np.einsum("ni,nj->ij", phi, phi) / expected_rows
    rhs = np.einsum("ni,nj->ij", phi, demand / target_scale) / expected_rows
    gram[1:, 1:] += ridge_lambda * np.eye(x.shape[1])
    coefficient = np.linalg.solve(gram, rhs)
    if not np.isfinite(coefficient).all():
        raise ArithmeticError("Nonfinite regression coefficients")
    return coefficient


def constant_fit(demand, expected_rows, budget):
    demand = np.asarray(demand, dtype=float)
    if (
        expected_rows < 1
        or demand.shape != (expected_rows, 4)
        or not np.isfinite(demand).all()
    ):
        raise ValueError("Constant reference requires exact training targets")
    budget.take("constant_estimates")
    return demand.mean(axis=0)


def correction(x, coefficient, target_scale):
    return target_scale * np.einsum("ni,ij->nj", design(x), coefficient)


def score(base, outputs, labels, origin, direction_tolerance):
    if (
        base.shape != labels.shape
        or base.shape[1:] != (4,)
        or not np.isfinite(labels).all()
        or not np.isfinite(base).all()
        or not 30 < origin < len(labels)
    ):
        raise ValueError("Matched four-point scoring calendars required")
    rows = []
    for strategy, prediction in outputs.items():
        if prediction.shape != labels.shape or not np.isfinite(prediction).all():
            raise ValueError("Invalid auxiliary prediction")
        for part, ix in (
            ("train", slice(30, origin)),
            ("forward", slice(origin, None)),
        ):
            for j, point in enumerate(POINTS):
                error = prediction[ix, j] - labels[ix, j]
                demand, applied = (
                    labels[ix, j] - base[ix, j],
                    prediction[ix, j] - base[ix, j],
                )
                active = (abs(demand) > direction_tolerance) & (
                    abs(applied) > direction_tolerance
                )
                opposed = (demand * applied < 0) & active
                rows.append(
                    dict(
                        origin=origin,
                        strategy=strategy,
                        part=part,
                        station=point,
                        days=len(error),
                        rmse_mm=float(np.sqrt(np.mean(error**2))),
                        mae_mm=float(np.mean(abs(error))),
                        bias_mm=float(error.mean()),
                        demand_mean_mm=float(demand.mean()),
                        correction_mean_mm=float(applied.mean()),
                        active_days=int(active.sum()),
                        opposed_days=int(opposed.sum()),
                        opposed_fraction=float(opposed.sum() / active.sum())
                        if active.any()
                        else np.nan,
                    )
                )
    return pd.DataFrame(rows)


def comparisons(metrics):
    selected = metrics[metrics.part == "forward"].set_index(
        ["origin", "strategy", "station"]
    )
    rows = []
    for origin in metrics.origin.unique():
        for strategy, reference in (("HH", "H"), ("HHS", "H"), ("HHS", "C")):
            for point in POINTS:
                actual, baseline = (
                    selected.loc[(origin, strategy, point)],
                    selected.loc[(origin, reference, point)],
                )
                rmse, mae = (
                    actual.rmse_mm - baseline.rmse_mm,
                    actual.mae_mm - baseline.mae_mm,
                )
                rows.append(
                    dict(
                        origin=origin,
                        strategy=strategy,
                        reference=reference,
                        station=point,
                        rmse_difference_mm=rmse,
                        mae_difference_mm=mae,
                        both_improve=bool(rmse < -1e-6 and mae < -1e-6),
                    )
                )
    return pd.DataFrame(rows)
