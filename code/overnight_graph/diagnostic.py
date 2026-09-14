"""Prespecified one-sided residual decomposition and conditional-run trigger."""

import numpy as np
import pandas as pd

from .core import (
    ROOT,
    bank,
    ema30,
    labels,
    lock,
    read_forcing,
    spec,
    utc,
    verify_implementation,
    verify_lock,
    write_json,
)


def main():
    cfg = spec()
    verify_implementation(cfg)
    root = ROOT / cfg["out"]
    verify_lock(root / "base/training_complete.json")
    out = root / "diagnostic"
    out.mkdir(exist_ok=False)
    dc = cfg["conditional_diagnostic"]
    y = labels(cfg, 1152, "prespecified_causal_residual_diagnostic_no_final_labels")
    teachers = bank(cfg)
    forcing, dates = read_forcing(ROOT / cfg["data"], 1152)
    cumulative = np.r_[0, np.cumsum(forcing[:, 0])]
    ends = np.arange(1, 1153)
    drivers = {
        "rain7": cumulative[ends] - cumulative[np.maximum(0, ends - 7)],
        "rwl_change": np.diff(forcing[:, 1], prepend=forcing[0, 1]),
    }
    components, variances, correlations = [], [], []
    for start, end, teacher in dc["blocks"]:
        r = y[start:end] - teachers[teacher]["mean"][start:end]
        slow = ema30(r)
        fast = r - slow
        assert np.max(abs(slow + fast - r)) < 1e-12
        for j, p in enumerate(cfg["points"]):
            keep = slice(dc["warmup"], None)
            total_var = float(np.var(r[keep, j]))
            sv = float(np.var(slow[keep, j]))
            fv = float(np.var(fast[keep, j]))
            cov = float(
                np.mean(
                    (slow[keep, j] - slow[keep, j].mean())
                    * (fast[keep, j] - fast[keep, j].mean())
                )
            )
            ratio = fv / total_var if total_var > 0 else None
            variances.append(
                dict(
                    start=start,
                    end=end,
                    teacher=teacher,
                    point=p,
                    total_variance=total_var,
                    slow_variance=sv,
                    fast_variance=fv,
                    covariance=cov,
                    fast_ratio=ratio,
                )
            )
            for t in range(end - start):
                components.append(
                    dict(
                        start=start,
                        end=end,
                        teacher=teacher,
                        point=p,
                        index=start + t,
                        date=dates[start + t],
                        residual=r[t, j],
                        slow=slow[t, j],
                        fast=fast[t, j],
                        diagnostic_retained=t >= dc["warmup"],
                    )
                )
            ix = np.arange(start + dc["warmup"], end)
            for name, driver in drivers.items():
                for lag in dc["lags"]:
                    a = driver[ix - lag]
                    b = fast[keep, j]
                    rho = (
                        float(np.corrcoef(a, b)[0, 1])
                        if a.std() > 0 and b.std() > 0
                        else None
                    )
                    correlations.append(
                        dict(
                            start=start,
                            end=end,
                            point=p,
                            driver=name,
                            lag=lag,
                            correlation=rho,
                            fast_ratio=ratio,
                            n=len(a),
                        )
                    )
    eligible = []
    for p in cfg["points"]:
        for driver in dc["drivers"]:
            for lag in dc["lags"]:
                rows = [
                    r
                    for r in correlations
                    if (r["point"], r["driver"], r["lag"]) == (p, driver, lag)
                ]
                for sign in [-1, 1]:
                    passing = [
                        r["start"]
                        for r in rows
                        if r["correlation"] is not None
                        and sign * r["correlation"] >= dc["abs_correlation_min"]
                        and r["fast_ratio"] is not None
                        and r["fast_ratio"] >= dc["fast_variance_ratio_min"]
                    ]
                    eligible.append(
                        dict(
                            point=p,
                            driver=driver,
                            lag=lag,
                            sign=sign,
                            passing_blocks=passing,
                            passed=len(passing) >= dc["consistent_blocks_min"],
                        )
                    )
    points = sorted(set(r["point"] for r in eligible if r["passed"]))
    groups = []
    for driver in dc["drivers"]:
        for lag in dc["lags"]:
            for sign in [-1, 1]:
                matched = [
                    r["point"]
                    for r in eligible
                    if r["passed"]
                    and (r["driver"], r["lag"], r["sign"]) == (driver, lag, sign)
                ]
                groups.append(
                    dict(
                        driver=driver,
                        lag=lag,
                        sign=sign,
                        points=matched,
                        passed=len(matched) >= dc["points_min"],
                    )
                )
    decision = dict(
        time_utc=utc(),
        triggered=any(g["passed"] for g in groups),
        eligible_points=points,
        new_neural_fits=0,
        physical_forwards=0,
        label_prefix_max=1152,
        interpretation="descriptive trigger only; correlation is not causality or 293-day forecast validation",
        eligible=eligible,
        groups=groups,
    )
    for name, data in [
        ("components", components),
        ("variances", variances),
        ("correlations", correlations),
    ]:
        pd.DataFrame(data).to_csv(
            out / (name + ".csv"), index=False, float_format="%.17g"
        )
    write_json(out / "decision.json", decision)
    lock(out, "lock.json", list(out.glob("*")), status="complete")
    print({k: v for k, v in decision.items() if k != "eligible"})


if __name__ == "__main__":
    main()
