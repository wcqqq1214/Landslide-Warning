"""One objective change; all fitting inputs remain restricted to their prefix."""

import time
import numpy as np
from scipy.optimize import least_squares

from physics_guided_diagnostics.core import (
    array_sha,
    bounded_jacobian,
    validate_training,
)


def specification():
    return dict(
        version="1.3",
        plan="docs/ootang_bplus_increment_plan.v1.3.md",
        control_run="results/ootang_bplus_v1_2/20260910_diagnostics",
        training_days=[432, 612],
        forecast_days=180,
        input_days=792,
        increment_days=30,
        increment_weight=1.0,
        scale_mm=100.0,
        increment_count_normalization="n/(n-30)",
        stage_terminal_weight_multipliers=[0, 1, 100, 100],
        stage_nfev_budgets=[900, 800, 800, 800],
        starts=["A", "B"],
        max_total_nfev=13200,
        selection="own training objective; absolute tie <=1e-12 chooses A",
        acceptance_tolerance_mm=0.000001,
        max_local_processes=3,
        exploratory=True,
        neural_training=False,
    )


def residual(prediction, target, terminal_weight, alpha=1.0):
    if (
        prediction.shape != target.shape
        or prediction.ndim != 2
        or prediction.shape[1] != 4
    ):
        raise ValueError("Expected matching time x four-station arrays")
    n = len(target)
    if n <= 30 or alpha not in (0.0, 1.0) or terminal_weight < 0:
        raise ValueError("Unregistered objective dimensions or weight")
    r = (prediction - target) / 100
    original = np.r_[r.ravel(), np.sqrt(terminal_weight) * r[-1]]
    if alpha == 0:
        return original
    return np.r_[original, np.sqrt(alpha * n / (n - 30)) * (r[30:] - r[:-30]).ravel()]


def components(prediction, target, terminal_weight):
    error = prediction - target
    delta_error = (prediction[30:] - prediction[:-30]) - (target[30:] - target[:-30])
    trajectory = float(np.sum(error**2) / 10000)
    terminal = float(terminal_weight * np.sum(error[-1] ** 2) / 10000)
    increment = float(len(error) / (len(error) - 30) * np.sum(delta_error**2) / 10000)
    return dict(
        trajectory_objective=trajectory,
        terminal_objective=terminal,
        increment_objective=increment,
        original_objective=trajectory + terminal,
        joint_objective=trajectory + terminal + increment,
    )


def fit_stage(ref, drivers, labels, n, theta, weight, budget, name):
    validate_training(drivers, labels, n)
    if n not in (432, 612) or (weight, budget) not in (
        (0, 900),
        (n, 800),
        (100 * n, 800),
    ):
        raise ValueError("Unregistered v1.3 optimization stage")
    if (
        len(theta) != 54
        or not np.isfinite(theta).all()
        or np.any(theta < ref.LO)
        or np.any(theta > ref.HI)
    ):
        raise ValueError("Invalid initial parameters")
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
        return residual(ref.forward(x, ctx), target, weight)

    start = np.clip(theta, ref.LO + 1e-9, ref.HI - 1e-9)
    try:
        op = least_squares(
            fun,
            start,
            jac=lambda x: bounded_jacobian(fun, x, ref.HI),
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
            optimization_forward_calls=calls,
            input_theta=theta.tolist(),
            fit_days=n,
            lambda_T=weight,
            max_nfev=budget,
            seconds=time.monotonic() - started,
        )
        raise
    prediction = ref.forward(op.x, ctx)
    terms = components(prediction, target, weight)
    if (
        not np.isfinite(op.x).all()
        or not np.isfinite(prediction).all()
        or not np.isfinite(list(terms.values())).all()
        or abs(terms["joint_objective"] - 2 * op.cost) > 1e-7
    ):
        raise ArithmeticError("Nonfinite parameters or objective mismatch")
    record = dict(
        name=name,
        fit_days=n,
        lambda_T=weight,
        max_nfev=budget,
        increment_weight=1.0,
        input_theta=theta.tolist(),
        optimizer_initial_theta=start.tolist(),
        theta=op.x.tolist(),
        nfev=int(op.nfev),
        njev=int(op.njev),
        optimization_forward_calls=calls,
        postcheck_forward_calls=1,
        objective=float(2 * op.cost),
        **terms,
        terminal_error_mm=(prediction[-1] - target[-1]).tolist(),
        success=bool(op.success),
        status=int(op.status),
        message=op.message,
        optimality=float(op.optimality),
        gtol_met=bool(op.optimality <= 1e-7),
        training_label_sha256=array_sha(labels),
        training_forcing_sha256=array_sha(drivers.forcing),
        max_parameter_change_bound_fraction=float(
            np.max(abs(op.x - theta) / (ref.HI - ref.LO))
        ),
        seconds=time.monotonic() - started,
    )
    print(
        f"{name}: objective={record['objective']:.7g} optimality={op.optimality:.4g} {op.message}",
        flush=True,
    )
    return record


def increment_rows(mu, labels, n, name):
    rows = []
    delta = (mu[30:] - mu[:-30]) - (labels[30:] - labels[:-30])
    for phase, sl in [
        ("train", slice(0, n - 30)),
        ("prediction", slice(n - 30, len(delta))),
    ]:
        e = delta[sl]
        rmse = np.sqrt(np.mean(e**2, axis=0))
        mae = np.mean(abs(e), axis=0)
        for j, station in enumerate(["ATU1", "ATU5", "MJ3", "MJ1"]):
            rows.append(
                dict(
                    candidate=name,
                    phase=phase,
                    station=station,
                    days=len(e),
                    delta30_rmse_mm=float(rmse[j]),
                    delta30_mae_mm=float(mae[j]),
                )
            )
        rows.append(
            dict(
                candidate=name,
                phase=phase,
                station="four_point_mean",
                days=len(e),
                delta30_rmse_mm=float(rmse.mean()),
                delta30_mae_mm=float(mae.mean()),
            )
        )
    return rows
