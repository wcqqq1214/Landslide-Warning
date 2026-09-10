"""Two isolated starts, three fixed prefix-only least-squares stages (v1.1 §3.2)."""

import time
import numpy as np
from scipy.optimize import least_squares
from .reference import save_json


def initial(ref, which):
    a = which == "A"
    groups = dict(
        tauP=7 if a else 30,
        margin=0.01 if a else 0.001,
        eta=0.01 if a else 0.1,
        hardening=0.001 if a else 0.01,
        tauRest=365 if a else 1000,
        tauMotion=7 if a else 30,
        creep=0.02 if a else 0.05,
        initial_relaxation=0,
        tauKelvin=30 if a else 90,
        tauR=30 if a else 60,
        reservoir_gradient_gain=0.3 if a else 1,
        reservoir_support_gain=0.3 if a else 1,
        k32=0.00001 if a else 0.0001,
        k21=0.00001 if a else 0.0001,
        E_MPa=0.1 if a else 1,
        tauContact=150 if a else 500,
        tauBulk=100 if a else 500,
        rain_compliance=1 if a else 5,
        wet_creep=0.1 if a else 0.2,
        tauMoisture=30 if a else 90,
    )
    vals = []
    for name in ref.NAMES:
        log = name.startswith("log_")
        physical = name[4:] if log else name
        key = next(
            k
            for k in sorted(groups, key=len, reverse=True)
            if physical == k or physical.startswith(k + "_")
        )
        v = groups[key]
        vals.append(np.log(v) if log else v)
    theta = np.array(vals, dtype=np.float64)
    if len(theta) != 54 or np.any(theta < ref.LO) or np.any(theta > ref.HI):
        raise ValueError("Invalid fixed initial parameter vector")
    return theta


def calibrate(ref, drivers, labels, output):
    """Cannot receive held-out labels or forcing: require the exact 792-day prefix."""
    if len(drivers.dates) != 792 or labels.shape != (792, 4):
        raise ValueError("Calibration accepts only the 792-day training prefix")
    ctx = ref.Context(drivers.forcing)
    target = labels - drivers.y0
    records, candidates = [], []
    for start in ("A", "B"):
        th = initial(ref, start)
        original = th.copy()
        _, states = ref.forward(th, ctx, states=True)
        active = (np.diff(states["plastic"], axis=0) > 0).sum(axis=0).tolist()
        run = dict(
            start=start,
            initial_theta=original.tolist(),
            initial_active_days=active,
            stages=[],
        )
        begin = time.monotonic()
        try:
            for weight, budget in [(0, 900), (792, 800), (79200, 800)]:
                calls = 0

                def fun(x):
                    nonlocal calls
                    calls += 1
                    r = (ref.forward(x, ctx) - target) / 100
                    if calls % 10000 == 0:
                        print(
                            f"calibration {start} weight={weight} forward_calls={calls} elapsed={time.monotonic() - begin:.1f}s",
                            flush=True,
                        )
                    return np.r_[r.ravel(), np.sqrt(weight) * r[-1]]

                def jac(x):
                    r = fun(x)
                    cols = []
                    for j in range(54):
                        h = 2e-6 * max(1, abs(x[j]))
                        if x[j] + h >= ref.HI[j]:
                            h = -h
                        z = x.copy()
                        z[j] += h
                        cols.append((fun(z) - r) / h)
                    return np.column_stack(cols)

                tick = time.monotonic()
                op = least_squares(
                    fun,
                    np.clip(th, ref.LO + 1e-9, ref.HI - 1e-9),
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
                th = op.x
                record = dict(
                    lambda_T=weight,
                    max_nfev=budget,
                    nfev=op.nfev,
                    njev=op.njev,
                    total_forward_calls=calls,
                    success=bool(op.success),
                    message=op.message,
                    status=op.status,
                    objective=float(2 * op.cost),
                    optimality=float(op.optimality),
                    theta=th.tolist(),
                    seconds=time.monotonic() - tick,
                )
                run["stages"].append(record)
                save_json(output / f"calibration_{start}.json", run)
                print(
                    f"calibration {start} stage done: {record['objective']:.8g}; {op.message}",
                    flush=True,
                )
            if not np.isfinite(th).all() or np.any(th < ref.LO) or np.any(th > ref.HI):
                raise ArithmeticError("Invalid terminal parameter vector")
            # forward() enforces the native complementarity failure and finite-trajectory checks.
            ref.forward(th, ctx)
            run["valid"] = True
            candidates.append((start, run["stages"][-1]["objective"], th))
        except Exception as exc:
            run.update(valid=False, failure=repr(exc))
        records.append(run)
        save_json(output / f"calibration_{start}.json", run)
    if not candidates:
        raise RuntimeError(
            "Both prescribed prefix calibration starts failed; no full-fit fallback"
        )
    best = candidates[0]
    for c in candidates[1:]:
        if c[1] < best[1] - 1e-12:
            best = c
    result = dict(
        physics_version="prefix_792_v1_1",
        fit_end="2018-08-31",
        selected_start=best[0],
        theta=best[2].tolist(),
        objective=best[1],
        records=records,
        y0=drivers.y0.tolist(),
        names=ref.NAMES,
        initial_state="zero mechanics; moisture=.5; H=R0",
        globally_optimal=False,
    )
    save_json(output / "prefix_calibrated.json", result)
    return best[2]
