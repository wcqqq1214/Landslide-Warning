"""Isolated prefix inputs and prescribed calibration stages; no forecast labels."""

import hashlib
import time

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

from physics_guided.data import Drivers, POINTS


def array_sha(a):
    return hashlib.sha256(
        np.ascontiguousarray(a, dtype=np.float64).tobytes()
    ).hexdigest()


def read_prefix(path):
    cols = ["Date", "Rainfall/mm", "RWL/m", *[p + "/mm" for p in POINTS]]
    frame = pd.read_csv(path, usecols=cols, nrows=792)
    dates = pd.DatetimeIndex(pd.to_datetime(frame["Date"], errors="raise"))
    if not dates.equals(pd.date_range("2016-07-01", periods=792, freq="D")):
        raise ValueError("Require exactly the ordered first 792 dates")
    forcing = frame[["Rainfall/mm", "RWL/m"]].to_numpy(dtype=np.float64)
    labels = frame[[p + "/mm" for p in POINTS]].to_numpy(dtype=np.float64)
    if (
        not np.isfinite(forcing).all()
        or not np.isfinite(labels).all()
        or (forcing[:, 0] < 0).any()
        or (forcing[:, 1] < 130).any()
        or (forcing[:, 1] > 190).any()
    ):
        raise ValueError("Invalid prefix data; no filling or clipping")
    return Drivers(dates, forcing, labels[0].copy()), labels, frame


def validate_training(drivers, labels, n):
    if n not in (432, 612, 792):
        raise ValueError("Unregistered training length")
    if (
        len(drivers.dates) != n
        or drivers.forcing.shape != (n, 2)
        or labels.shape != (n, 4)
    ):
        raise ValueError("Training API accepts this fold only")
    if not drivers.dates.equals(pd.date_range("2016-07-01", periods=n, freq="D")):
        raise ValueError("Training dates differ from fixed prefix")
    if not np.array_equal(drivers.y0, labels[0]):
        raise ValueError("Initial displacement must equal first training observation")
    if not np.isfinite(labels).all() or not np.isfinite(drivers.forcing).all():
        raise ValueError("Nonfinite training inputs")


def residual_vector(prediction, target, weight):
    r = (prediction - target) / 100.0
    return np.r_[r.ravel(), np.sqrt(weight) * r[-1]]


def bounded_jacobian(fun, theta, upper):
    base = fun(theta)
    cols = []
    for j in range(len(theta)):
        h = 2e-6 * max(1, abs(theta[j]))
        if theta[j] + h >= upper[j]:
            h = -h
        shifted = theta.copy()
        shifted[j] += h
        cols.append((fun(shifted) - base) / h)
    return np.column_stack(cols)


def fit_stage(ref, drivers, labels, n, theta, weight, budget, name):
    validate_training(drivers, labels, n)
    if (weight, budget) not in ((0, 900), (n, 800), (100 * n, 800)):
        raise ValueError("Unregistered optimization stage")
    if len(theta) != 54 or np.any(theta < ref.LO) or np.any(theta > ref.HI):
        raise ValueError("Invalid parameter vector")
    ctx = ref.Context(drivers.forcing.copy())
    target = labels.copy() - drivers.y0
    calls = 0
    started = time.monotonic()

    def fun(x):
        nonlocal calls
        calls += 1
        if calls % 10000 == 0:
            print(
                f"{name}: calls={calls} seconds={time.monotonic() - started:.1f}",
                flush=True,
            )
        return residual_vector(ref.forward(x, ctx), target, weight)

    def jac(x):
        return bounded_jacobian(fun, x, ref.HI)

    start = np.clip(theta, ref.LO + 1e-9, ref.HI - 1e-9)
    try:
        op = least_squares(
            fun,
            start,
            jac=jac,
            bounds=(ref.LO, ref.HI),
            method="trf",
            loss="linear",
            x_scale="jac",
            max_nfev=budget,
            ftol=1e-9,
            xtol=1e-10,
            gtol=1e-7,
        )
    except Exception as exc:
        exc.diagnostic_stage = dict(
            name=name,
            fit_days=n,
            lambda_T=weight,
            max_nfev=budget,
            input_theta=theta.tolist(),
            optimization_forward_calls=calls,
            seconds=time.monotonic() - started,
        )
        raise
    if not np.isfinite(op.x).all():
        raise ArithmeticError("Nonfinite fitted parameters")
    r = (ref.forward(op.x, ctx) - target) / 100
    trajectory = float((r * r).sum())
    terminal = float(weight * (r[-1] ** 2).sum())
    if abs(trajectory + terminal - 2 * op.cost) > 1e-7:
        raise ArithmeticError(
            "Training objective failed independent terminal recomputation"
        )
    record = dict(
        name=name,
        fit_days=n,
        lambda_T=weight,
        max_nfev=budget,
        input_theta=theta.tolist(),
        optimizer_initial_theta=start.tolist(),
        theta=op.x.tolist(),
        nfev=int(op.nfev),
        njev=int(op.njev),
        optimization_forward_calls=calls,
        postcheck_forward_calls=1,
        objective=float(2 * op.cost),
        trajectory_objective=trajectory,
        terminal_objective=terminal,
        terminal_error_mm=(r[-1] * 100).tolist(),
        success=bool(op.success),
        status=int(op.status),
        message=op.message,
        optimality=float(op.optimality),
        gtol_met=bool(op.optimality <= 1e-7),
        seconds=time.monotonic() - started,
        iteration_trace=[],
        iteration_trace_status="unavailable: scipy 1.15 no callback",
        training_label_sha256=array_sha(labels),
        training_forcing_sha256=array_sha(drivers.forcing),
        max_parameter_change_bound_fraction=float(
            np.max(abs(op.x - theta) / (ref.HI - ref.LO))
        ),
    )
    print(
        f"{name}: objective={record['objective']:.7g} success={op.success} "
        f"optimality={op.optimality:.4g}",
        flush=True,
    )
    return record


def choose_start(records):
    valid = {k: v for k, v in records.items() if v.get("valid", False)}
    if not valid:
        return None
    best = sorted(valid)[0]
    for key in sorted(valid):
        if valid[key]["objective"] < valid[best]["objective"] - 1e-12:
            best = key
    return best


def metric_rows(mu, labels, n, name):
    rows = []
    for phase, sl in [("train", slice(30, n)), ("prediction", slice(n, len(labels)))]:
        if len(labels[sl]) == 0:
            continue
        e = mu[sl] - labels[sl]
        vals = {
            "rmse_mm": np.sqrt((e * e).mean(0)),
            "mae_mm": abs(e).mean(0),
            "bias_mm": e.mean(0),
        }
        for station, j in zip(POINTS, range(4)):
            rows.append(
                dict(
                    candidate=name,
                    phase=phase,
                    station=station,
                    days=len(e),
                    **{k: float(v[j]) for k, v in vals.items()},
                )
            )
        rows.append(
            dict(
                candidate=name,
                phase=phase,
                station="four_point_mean",
                days=len(e),
                **{k: float(v.mean()) for k, v in vals.items()},
            )
        )
    return rows


def growth_rows(drivers, labels, mu, state, obs, n, name):
    intervals = [("train_last30", n - 31, n - 1), ("train_last90", n - 91, n - 1)]
    if len(mu) > n:
        intervals += [
            ("prediction_first30", n - 1, n + 29),
            ("prediction_first90", n - 1, n + 89),
            ("prediction_all", n - 1, len(mu) - 1),
        ]
    background = state["background"] @ obs.T
    other = (state["coordinates"] - state["background"]) @ obs.T
    rows = []
    for window, a, b in intervals:
        days = b - a
        for j, station in enumerate(POINTS):
            rows.append(
                dict(
                    candidate=name,
                    window=window,
                    station=station,
                    days=days,
                    start=str(drivers.dates[a].date()),
                    end=str(drivers.dates[b].date()),
                    observed_mm_day=float((labels[b, j] - labels[a, j]) / days),
                    predicted_mm_day=float((mu[b, j] - mu[a, j]) / days),
                    background_mm_day=float(
                        (background[b, j] - background[a, j]) / days
                    ),
                    other_state_mm_day=float((other[b, j] - other[a, j]) / days),
                )
            )
    return rows
