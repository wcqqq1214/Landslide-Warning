"""Prefix-only fitting and selection; unchanged B+ equations and objectives."""

import time

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

from physics_guided.data import Drivers, POINTS
from physics_guided_diagnostics.core import array_sha, bounded_jacobian
from physics_guided_increment.objective import components, residual

INNER = {432: (252,), 612: (252, 342, 432)}
STAGES = ((0, 900), (1, 800), (100, 800), (100, 800))


def read_input(path, n):
    """Parse only requested rows, including when later rows are malformed."""
    if not isinstance(n, int) or not 1 <= n <= 792:
        raise ValueError("Input must stay within the first 792 days")
    frame = pd.read_csv(
        path,
        nrows=n,
        usecols=["Date", "Rainfall/mm", "RWL/m", *[p + "/mm" for p in POINTS]],
    )
    dates = pd.DatetimeIndex(pd.to_datetime(frame.Date, errors="raise"))
    forcing = frame[["Rainfall/mm", "RWL/m"]].to_numpy(dtype=float)
    labels = frame[[p + "/mm" for p in POINTS]].to_numpy(dtype=float)
    if not dates.equals(pd.date_range("2016-07-01", periods=n)):
        raise ValueError("Expected the exact ordered date prefix")
    if (
        not np.isfinite(forcing).all()
        or not np.isfinite(labels).all()
        or (forcing[:, 0] < 0).any()
        or (forcing[:, 1] < 130).any()
        or (forcing[:, 1] > 190).any()
    ):
        raise ValueError("Invalid input; no filling or clipping")
    return Drivers(dates, forcing, labels[0].copy()), labels


def validate_training(drivers, labels, n):
    if n not in (252, 342, 432, 612):
        raise ValueError("Unregistered v1.4 training prefix")
    if (
        len(drivers.dates) != n
        or drivers.forcing.shape != (n, 2)
        or labels.shape != (n, 4)
        or not drivers.dates.equals(pd.date_range("2016-07-01", periods=n))
        or not np.array_equal(drivers.y0, labels[0])
        or not np.isfinite(labels).all()
        or not np.isfinite(drivers.forcing).all()
    ):
        raise ValueError("Training API accepts only this prefix and its origin")


def validate_parameters(ref, theta):
    x = np.array(theta, dtype=float, copy=True)
    if (
        x.shape != (54,)
        or not np.isfinite(x).all()
        or (x < ref.LO).any()
        or (x > ref.HI).any()
    ):
        raise ValueError("Invalid 54-parameter vector")
    return x


def validate_source(record, drivers, labels, n, ref):
    validate_training(drivers, labels, n)
    if (
        record["fit_days"] != n
        or record["training_label_sha256"] != array_sha(labels)
        or record["training_forcing_sha256"] != array_sha(drivers.forcing)
    ):
        raise ValueError("Source is not the identical training prefix")
    return validate_parameters(ref, record["theta"])


def retain_incumbent(anchor, attempted):
    if attempted is None or not attempted.get("valid", False):
        return "anchor", "attempt failed or physically invalid"
    if not np.isfinite(attempted["objective"]):
        return "anchor", "attempt objective is nonfinite"
    if anchor["objective"] - attempted["objective"] <= 1e-7:
        return "anchor", "no improvement greater than 1e-7"
    return "attempt", "training objective improved by more than 1e-7"


def choose_anchor(candidates):
    best = None
    for name, record in candidates.items():
        if not record.get("valid", False):
            continue
        if best is None or record["objective"] < candidates[best]["objective"] - 1e-12:
            best = name
    return best


def fit_stage(ref, drivers, labels, n, theta, task, stage, progress=None):
    validate_training(drivers, labels, n)
    if task == "A" and n in INNER and stage == 1:
        weight, budget, alpha = 100 * n, 800, 1.0
    elif task == "B" and n in (252, 342) and stage in range(1, 5):
        multiplier, budget = STAGES[stage - 1]
        weight, alpha = n * multiplier, 0.0
    else:
        raise ValueError("Unregistered task, prefix or stage")
    theta = validate_parameters(ref, theta)
    start = np.clip(theta, ref.LO + 1e-9, ref.HI - 1e-9)
    ctx, target = ref.Context(drivers.forcing.copy()), labels.copy() - drivers.y0
    started = time.monotonic()
    counters = dict(nfev=0, njev=0, optimization_forward_calls=0)
    meta = dict(
        task=task,
        stage=stage,
        fit_days=n,
        fit_last_date=str(drivers.dates[-1].date()),
        lambda_T=weight,
        max_nfev=budget,
        increment_weight=alpha,
        input_theta=theta.tolist(),
        optimizer_initial_theta=start.tolist(),
        training_label_sha256=array_sha(labels),
        training_forcing_sha256=array_sha(drivers.forcing),
    )

    def checkpoint():
        if progress:
            progress(dict(**meta, **counters, seconds=time.monotonic() - started))

    def evaluate(x):
        if counters["optimization_forward_calls"] >= 56 * budget:
            raise RuntimeError("Physical forward budget exhausted")
        counters["optimization_forward_calls"] += 1
        if counters["optimization_forward_calls"] % 1000 == 0:
            checkpoint()
        if counters["optimization_forward_calls"] % 10000 == 0:
            print(f"{task}/{n}/stage{stage}: {counters}", flush=True)
        return residual(ref.forward(x, ctx), target, weight, alpha)

    def fun(x):
        if counters["nfev"] >= budget:
            raise RuntimeError("Objective evaluation budget exhausted")
        counters["nfev"] += 1
        return evaluate(x)

    def jac(x):
        if counters["njev"] >= budget:
            raise RuntimeError("Jacobian budget exhausted")
        counters["njev"] += 1
        return bounded_jacobian(evaluate, x, ref.HI)

    key = "joint_objective" if alpha else "original_objective"
    pre = [components(ref.forward(x, ctx), target, weight)[key] for x in (theta, start)]
    checkpoint()
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
        x = validate_parameters(ref, op.x)
        prediction = ref.forward(x, ctx)
        terms = components(prediction, target, weight)
        if (
            not np.isfinite(prediction).all()
            or not np.isfinite(list(terms.values())).all()
            or abs(terms[key] - 2 * op.cost) > 1e-7
            or counters["nfev"] != op.nfev
            or counters["njev"] != op.njev
            or counters["optimization_forward_calls"] != op.nfev + 55 * op.njev
        ):
            raise ArithmeticError("Objective or numerical-call accounting mismatch")
        record = dict(
            **meta,
            **counters,
            **terms,
            theta=x.tolist(),
            objective=terms[key],
            input_objective=pre[0],
            optimizer_initial_objective=pre[1],
            clipping_objective_change=pre[1] - pre[0],
            precheck_forward_calls=2,
            postcheck_forward_calls=1,
            terminal_error_mm=(prediction[-1] - target[-1]).tolist(),
            success=bool(op.success),
            status=int(op.status),
            message=op.message,
            optimality=float(op.optimality),
            gtol_met=bool(op.optimality <= 1e-7),
            max_parameter_change_bound_fraction=float(
                np.max(abs(x - theta) / (ref.HI - ref.LO))
            ),
            seconds=time.monotonic() - started,
        )
    except Exception as exc:
        checkpoint()
        exc.diagnostic_stage = dict(
            **meta, **counters, seconds=time.monotonic() - started
        )
        raise
    checkpoint()
    print(
        f"{task}/{n}/stage{stage}: J={record['objective']:.8g}, {op.message}",
        flush=True,
    )
    return record


def inner_scores(mu, observations, inner_n, outer_n):
    if outer_n not in INNER or inner_n not in INNER[outer_n]:
        raise ValueError("Window outside this outer training prefix")
    if mu.shape != (180, 4) or observations.shape != (180, 4):
        raise ValueError("Exactly 180 validation dates and four stations required")
    error = mu - observations
    if not np.isfinite(error).all():
        raise ValueError("Nonfinite validation values")
    return dict(
        valid=True,
        inner_days=inner_n,
        first_date=str(
            (pd.Timestamp("2016-07-01") + pd.Timedelta(days=inner_n)).date()
        ),
        last_date=str(
            (pd.Timestamp("2016-07-01") + pd.Timedelta(days=inner_n + 179)).date()
        ),
        rmse_mm=np.sqrt(np.mean(error**2, axis=0)).tolist(),
        mae_mm=np.mean(abs(error), axis=0).tolist(),
        bias_mm=np.mean(error, axis=0).tolist(),
    )


def select_recipe(outer_n, windows):
    """Accept only prescribed internal-window scores; no outer outcomes."""
    if outer_n not in INNER or set(windows) != set(INNER[outer_n]):
        raise ValueError("Incorrect internal window set")
    aggregates = {}
    allowed = {
        "valid",
        "inner_days",
        "first_date",
        "last_date",
        "rmse_mm",
        "mae_mm",
        "bias_mm",
    }
    for n, candidates in windows.items():
        if set(candidates) != {"A", "B"}:
            raise ValueError("Both recipes must be recorded, including failures")
        for entry in candidates.values():
            if entry.get("valid", False):
                if set(entry) != allowed or entry["inner_days"] != n:
                    raise ValueError("Unregistered selector inputs")
                expected = pd.date_range("2016-07-01", periods=n + 180)[n:]
                if entry["first_date"] != str(expected[0].date()) or entry[
                    "last_date"
                ] != str(expected[-1].date()):
                    raise ValueError("Validation dates changed")
                for field in ("rmse_mm", "mae_mm", "bias_mm"):
                    values = np.asarray(entry[field])
                    if values.shape != (4,) or not np.isfinite(values).all():
                        raise ValueError("Invalid per-station score")
                    if field != "bias_mm" and (values < 0).any():
                        raise ValueError("Negative error metric")
    for recipe in ("A", "B"):
        entries = [windows[n][recipe] for n in INNER[outer_n]]
        valid = all(r.get("valid", False) for r in entries)
        aggregates[recipe] = dict(valid=valid)
        if valid:
            aggregates[recipe].update(
                {
                    field: float(np.mean([r[field] for r in entries]))
                    for field in ("rmse_mm", "mae_mm")
                }
            )
    selected = None
    for recipe in ("A", "B"):
        r = aggregates[recipe]
        if not r["valid"]:
            continue
        if selected is None:
            selected = recipe
            continue
        old = aggregates[selected]
        if r["rmse_mm"] < old["rmse_mm"] - 1e-6 or (
            abs(r["rmse_mm"] - old["rmse_mm"]) <= 1e-6
            and r["mae_mm"] < old["mae_mm"] - 1e-6
        ):
            selected = recipe
    return dict(selected_recipe=selected, candidates=aggregates)
