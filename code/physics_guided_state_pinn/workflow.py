"""Frozen inputs, chronological calibration, and P/R numerical diagnostics."""

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import torch

from physics_guided.data import POINTS
from physics_guided_forecast_error.artifacts import (
    ROOT,
    read_json,
    sha,
    trajectory_name,
)
from physics_guided_pinn.run_substep_audit import (
    load_npz,
    specification as trace_specification,
)
from physics_guided_pinn.substep_audit import audit_trace
from physics_guided_sample_learning.core import fit_scale, score_components
from .core import Case, PHYSICS_KEYS, equation_values, tensor

CONFIG = ROOT / "config/ootang_bplus_state_pinn.v1_9.json"
CONFIG_SHA = "e2fce9b692e1e79b7058af3a4dc34143c54790b0f9d6221f20fc24eb91877f00"
PLAN_SHA = "3c4d1ecc234fb57bb4ab48eef732e18f5ca07c4c341d101e9ea890cd58555866"
PREFIXES = {342: "A", 432: "B", 612: "B"}
CUTOFFS = {432: 342, 612: 432}


def now():
    return datetime.now(timezone.utc).isoformat()


def specification():
    if sha(CONFIG) != CONFIG_SHA:
        raise ValueError("Frozen v1.9 configuration changed")
    spec = read_json(CONFIG)
    if sha(ROOT / spec["protocol"]) != PLAN_SHA:
        raise ValueError("Frozen v1.9 plan changed")
    return spec


def load_bundle(spec, h, end=None):
    if h not in PREFIXES:
        raise ValueError("Unregistered physical teacher")
    end = h + 180 if end is None else end
    if end not in (h, h + 180):
        raise ValueError("Only the training or complete forecast prefix is permitted")
    physical = ROOT / spec["physical_source"]
    source = ROOT / spec["trace_source"]
    saved = load_npz(physical / trajectory_name(h, PREFIXES[h]))
    n = len(saved["mean"])
    if n != h + 180:
        raise ValueError("Physical source horizon changed")
    saved = {
        k: (v[:end].copy() if v.shape[:1] == (n,) else v) for k, v in saved.items()
    }
    with np.load(source / f"{h}{PREFIXES[h]}.npz", allow_pickle=False) as z:
        trace = {
            k: z[k].copy()
            for k in ("current", "loads", "length", "y0", "dates", "observation_matrix")
        }
    trace["current"] = trace["current"][: (end - 1) * 64].copy()
    trace["loads"] = trace["loads"][: (end - 1) * 64].copy()
    drivers = load_npz(source / "drivers.npz")
    if not np.array_equal(saved["dates"], drivers["dates"][:end]) or not np.array_equal(
        trace["dates"][:end], saved["dates"]
    ):
        raise ValueError("Physical, trace and driver calendars differ")
    if not np.array_equal(trace["y0"], drivers["y0"]) or not np.array_equal(
        saved["observation_matrix"], trace["observation_matrix"]
    ):
        raise ValueError("Initial displacement or observation map differs")
    return saved, trace, drivers["forcing"][:end].copy(), drivers["y0"].copy()


def make_case(bundle, h, constants=None):
    saved, trace, forcing, y0 = bundle
    return Case(saved, trace, forcing, y0, h, constants)


def read_labels(path, n):
    if n not in (342, 432, 612, 792):
        raise ValueError("Unregistered label prefix")
    columns = [station + "/mm" for station in POINTS]
    frame = pd.read_csv(path, nrows=n, usecols=["Date", *columns])
    if not pd.DatetimeIndex(pd.to_datetime(frame.Date)).equals(
        pd.date_range("2016-07-01", periods=n)
    ):
        raise ValueError("Label calendar differs")
    values = frame[columns].to_numpy(float)
    if not np.isfinite(values).all():
        raise ValueError("Nonfinite labels; no filling")
    return values


def numpy_output(output):
    result = {
        name: value.detach().cpu().numpy().copy() for name, value in output.items()
    }
    if not all(np.isfinite(x).all() for x in result.values()):
        raise ArithmeticError("Nonfinite state PINN output")
    if (result["multiplier"] < 0.5).any() or (result["multiplier"] > 2).any():
        raise ArithmeticError("Creep multiplier outside registered bounds")
    return result


def projected_background(saved, output):
    gamma = output["multiplier"]
    if (
        gamma.shape != saved["background_rate"].shape
        or not np.isfinite(gamma).all()
        or (gamma < 0.5).any()
        or (gamma > 2).any()
    ):
        raise ValueError("Invalid frozen daily multiplier")
    delta = np.vstack(
        [np.zeros(4), np.cumsum(saved["background_rate"][1:] * (gamma[1:] - 1), axis=0)]
    )
    if not np.allclose(delta, output["delta_background"], rtol=0, atol=1e-8):
        raise ValueError(
            "Neural background differs from independent NumPy accumulation"
        )
    return saved["background"] + delta


def replay_reference(saved, recorded, background, y0):
    result = dict(saved)
    for name in (
        "coordinates",
        "plastic",
        "basal_reaction",
        "contact",
        "bulk_reaction",
    ):
        result[name] = recorded[name]
    result["background"] = background
    result["mean"] = recorded["coordinates"] @ saved["observation_matrix"].T + y0
    return result


def replay_audit(bundle, output, recorded):
    saved, trace, _, y0 = bundle
    background = projected_background(saved, output)
    expected = replay_reference(saved, recorded, background, y0)
    report = audit_trace(
        recorded,
        expected,
        trace["length"],
        saved["observation_matrix"],
        y0,
        trace_specification()["tolerances"],
    )
    return expected, report


def diagnostics(case, output, replay_mean):
    with torch.no_grad():
        values = {
            name: value.numpy()
            for name, value in equation_values(
                case, {k: tensor(v) for k, v in output.items()}
            ).items()
        }
    result = {}
    split = (case.h - 1) * 64
    for part, ix, days in (
        ("train", slice(0, split), slice(30, case.h)),
        ("prediction", slice(split, None), slice(case.h, case.days)),
    ):
        if not len(values["motion"][ix]):
            continue
        dx, gap = values["slip_step"][ix], values["yield_gap"][ix]
        normalization = 1 + np.max(abs(dx), axis=1) * np.max(abs(gap), axis=1)
        difference = output["mean"][days] - replay_mean[days]
        result[part] = dict(
            substeps=len(dx),
            equations={
                name: dict(
                    raw_rms_by_domain=np.sqrt(
                        np.mean(values[name][ix] ** 2, axis=0)
                    ).tolist(),
                    raw_max_abs_by_domain=np.max(
                        abs(values[name][ix]), axis=0
                    ).tolist(),
                    normalized_rms_by_domain=np.sqrt(
                        np.mean(
                            (values[name][ix] / case.scales[name].numpy()) ** 2, axis=0
                        )
                    ).tolist(),
                    normalized_max_abs_by_domain=np.max(
                        abs(values[name][ix] / case.scales[name].numpy()), axis=0
                    ).tolist(),
                )
                for name in PHYSICS_KEYS
            },
            background_residual_max_mm=float(np.max(abs(values["background"][ix]))),
            dx_violation_fraction_by_domain=np.mean(dx < -1e-8, axis=0).tolist(),
            gap_violation_fraction_by_domain=np.mean(gap < -1e-7, axis=0).tolist(),
            normalized_complementarity_max=float(
                np.max(abs(dx * gap).max(axis=1) / normalization)
            ),
            normalized_complementarity_violation_fraction=float(
                np.mean(abs(dx * gap).max(axis=1) / normalization > 1e-8)
            ),
            p_minus_r_rms_by_point_mm=np.sqrt(np.mean(difference**2, axis=0)).tolist(),
            p_minus_r_max_abs_by_point_mm=np.max(abs(difference), axis=0).tolist(),
        )
    return result


def components(out, spec, h, strategy):
    if strategy == "P0":
        with np.load(
            ROOT / spec["physical_source"] / trajectory_name(h, PREFIXES[h]),
            allow_pickle=False,
        ) as saved:
            return saved["mean"][None].copy()
    if strategy not in ("P", "R"):
        raise ValueError("Unregistered prediction output")
    means = []
    for seed in spec["seeds"]:
        with np.load(
            out / f"model_{h}_{seed}" / f"{strategy}.npz", allow_pickle=False
        ) as saved:
            means.append(saved["mean"].copy())
    return np.stack(means)


def calibration(out, spec, n, labels):
    c = CUTOFFS[n]
    if labels.shape != (n, 4):
        raise ValueError("Calibration must receive exactly the current label prefix")
    result = {}
    for strategy in ("P0", "P", "R"):
        forecasts = components(out, spec, c, strategy)[:, c:n]
        result[strategy] = dict(
            sigma=fit_scale(forecasts, labels[c:n]),
            errors=forecasts - labels[None, c:n],
            component_means=forecasts,
        )
    return result


def compare(metrics, historical):
    combined = pd.concat(
        [metrics, historical[historical.strategy.isin(["U", "L"])]], ignore_index=True
    )
    indexed = combined.set_index(["outer_days", "strategy", "station", "part"])
    rows = []
    for n in CUTOFFS:
        for strategy in ("P", "R"):
            for point in POINTS:
                row = dict(
                    outer_days=n,
                    strategy=strategy,
                    station=point,
                    primary=strategy == "R",
                )
                for reference in ("P0", "U", "L"):
                    for part in ("train", "prediction"):
                        a = indexed.loc[n, strategy, point, part]
                        b = indexed.loc[n, reference, point, part]
                        for metric in ("rmse_mm", "mae_mm", "crps_mm"):
                            row[f"{part}_{metric}_minus_{reference}"] = float(
                                a[metric] - b[metric]
                            )
                row["strict_mean_improvement"] = all(
                    row[f"{part}_{metric}_minus_P0"] < -1e-6
                    for part in ("train", "prediction")
                    for metric in ("rmse_mm", "mae_mm")
                )
                rows.append(row)
    return pd.DataFrame(rows)


def scoring(out, spec, labels):
    rows, seed_rows, distributions = [], [], {}
    for n in CUTOFFS:
        for strategy in ("P0", "P", "R"):
            means = components(out, spec, n, strategy)
            scales = load_npz(out / f"calibration_{n}_{strategy}.npz")["sigma"]
            scores, dist = score_components(
                means, scales, labels[: n + 180], n, strategy
            )
            rows.extend(scores)
            distributions[n, strategy] = dist
            for seed in range(len(means)):
                specific, _ = score_components(
                    means[seed : seed + 1],
                    scales[seed : seed + 1],
                    labels[: n + 180],
                    n,
                    strategy,
                )
                seed_rows.extend(dict(row, seed=seed) for row in specific)
    metrics = pd.DataFrame(rows)
    historical = pd.read_csv(ROOT / spec["comparison_source"] / "metrics.csv")
    return metrics, pd.DataFrame(seed_rows), compare(metrics, historical), distributions


def plot(out, curves, labels):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    for intervals in (False, True):
        fig, axes = plt.subplots(4, 2, figsize=(12, 13), layout="constrained")
        for column, n in enumerate(CUTOFFS):
            for j, point in enumerate(POINTS):
                ax = axes[j, column]
                for strategy, title, color, style in (
                    ("P0", "B+", "0.45", "-"),
                    ("P", "P state (diagnostic)", "#d27b17", "--"),
                    ("R", "R replay (primary)", "#4466ab", "-"),
                ):
                    values = curves[n, strategy]
                    ax.plot(
                        np.arange(1, 181),
                        values["mean"][n - 30 :, j],
                        color=color,
                        linestyle=style,
                        label=title,
                    )
                    if intervals and strategy == "R":
                        ax.fill_between(
                            np.arange(1, 181),
                            values["lower_90"][n - 30 :, j],
                            values["upper_90"][n - 30 :, j],
                            color=color,
                            alpha=0.16,
                            label="R 90% interval",
                        )
                ax.plot(
                    np.arange(1, 181),
                    labels[n : n + 180, j],
                    color="black",
                    label="Observed",
                )
                ax.set(
                    title=f"{point} | outer {n} days",
                    xlabel="Forecast day",
                    ylabel="Displacement (mm)",
                )
                ax.grid(alpha=0.15)
        handles, titles = axes[0, 0].get_legend_handles_labels()
        fig.legend(handles, titles, loc="outside upper center", ncol=3)
        fig.savefig(
            out / ("forecast_intervals.png" if intervals else "forecast_means.png"),
            dpi=150,
        )
        plt.close(fig)
