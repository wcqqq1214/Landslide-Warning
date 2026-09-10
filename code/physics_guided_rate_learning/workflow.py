"""Fixed design, prefix-only training objects and saved-output scientific checks."""

import numpy as np
import pandas as pd
import torch

from physics_guided_forecast_error.artifacts import ROOT, read_json, sha
from physics_guided_pinn.run_substep_audit import load_npz
from physics_guided_shared_mechanics.core import RecordedMechanics
from physics_guided_sample_learning.core import fit_scale, score_components
from physics_guided_state_pinn.verify import NumericalChecks
from physics_guided_state_pinn.workflow import CUTOFFS, POINTS, replay_audit

CONFIG = ROOT / "config/ootang_bplus_rate_learning.v1_12.json"
CONFIG_SHA = "611c66fe9074bb511e8764328a20af44b192eda87a4d3c133049b2b8dfa8354a"
PLAN_SHA = "98f1ca475602636519fd6fed207743fa4959a6a0b55a74bc4b07416cde4eb624"
ARRAY_KEYS = (
    "state",
    "mean",
    "multiplier",
    "background",
    "delta_background",
    "masks",
    "audit",
)
REPLAY_KEYS = {
    "previous",
    "current",
    "loads",
    "lcp",
    "masks",
    "coordinates",
    "plastic",
    "contact",
    "bulk_reaction",
    "basal_reaction",
    "bad",
    "mean",
    "background",
}


def specification():
    if sha(CONFIG) != CONFIG_SHA:
        raise ValueError("Frozen v1.12 configuration changed")
    spec = read_json(CONFIG)
    if sha(ROOT / spec["protocol"]) != PLAN_SHA:
        raise ValueError("Frozen v1.12 learning plan changed")
    return spec


def limits(spec):
    names = (
        "reference_forwards",
        "neural_evaluations",
        "trajectories",
        "day_forwards",
        "day_backwards",
        "reverse_passes",
        "neural_updates",
        "checkpoint_replays",
        "recorded_integrations",
        "parameter_fits",
        "scale_neural_updates",
    )
    return {
        **{name: spec["max_" + name] for name in names},
        "reference_substeps": spec["reference_substeps"],
        "recorded_substeps": spec["recorded_substeps"],
    }


def training_solver(full, h):
    """A Day-only object with no future forcing, hydrology, baseline or context."""
    if not 31 <= h <= len(full.force):
        raise ValueError("Invalid training solver prefix")
    result = object.__new__(RecordedMechanics)
    result.coeff = full.coeff.copy()
    result.force, result.elastic = full.force[:h].copy(), full.elastic[:h].copy()
    result.lib, result.records = full.lib, []
    return result


def arrays(output, days=None):
    if set(output) != set(ARRAY_KEYS):
        raise ValueError("Complete shared mechanical output required")
    n = len(output["mean"]) if days is None else days
    result = {}
    for key, value in output.items():
        value = (
            value.detach().cpu().numpy()
            if isinstance(value, torch.Tensor)
            else np.asarray(value)
        )
        result[key] = value[: n - 1 if key in ("masks", "audit") else n].copy()
    validate_output(result)
    return result


def validate_output(output):
    n = len(output["mean"])
    shapes = {
        "state": (n, 24),
        "mean": (n, 4),
        "multiplier": (n, 4),
        "background": (n, 4),
        "delta_background": (n, 4),
        "masks": (n - 1, 64),
        "audit": (n - 1, 5),
    }
    if set(output) != set(shapes) or any(
        output[k].shape != shape or not np.isfinite(output[k]).all()
        for k, shape in shapes.items()
    ):
        raise ValueError("Incomplete, malformed or nonfinite prediction")
    if np.count_nonzero(output["state"][0]) or np.count_nonzero(
        output["background"][0]
    ):
        raise ArithmeticError("The shared initial state changed")
    if np.any((output["multiplier"] < 0.5) | (output["multiplier"] > 2)):
        raise ArithmeticError("Learned rate exceeds the frozen bounds")
    mask, audit = output["masks"], output["audit"]
    if mask.dtype.kind not in "iu" or np.any((mask < 0) | (mask > 15)):
        raise ValueError("Invalid recorded active branches")
    if (
        audit[:, 0].min() < -1e-8
        or audit[:, 1].min() < -1e-7
        or audit[:, 2].max() > 1e-8
        or audit[:, 3].min() < 0
    ):
        raise ArithmeticError("Original daily physical tolerances failed")


def compare_output(actual, expected):
    validate_output(actual)
    validate_output(expected)
    check = NumericalChecks()
    for key in ARRAY_KEYS:
        a, b = actual[key], expected[key]
        if key == "state":
            check.close(a[:, :8], b[:, :8])
            check.close(a[:, 20:], b[:, 20:])
            check.close(
                a[:, 8:20],
                b[:, 8:20],
                atol=1e-7 + 256 * np.finfo(float).eps * np.max(abs(b[:, 8:20])),
            )
        elif key in ("masks", "audit"):
            # Both outputs must pass the original physical tolerances above.
            # Valid branch choices near a tolerance boundary are reported separately.
            continue
        else:
            check.close(a, b, mean=key == "mean")
    return dict(
        values=check.values,
        max_mean_difference_mm=check.max_mean_difference_mm,
        max_mixed_absolute_difference=check.max_absolute_difference,
        changed_active_substeps=int(
            np.count_nonzero(actual["masks"] != expected["masks"])
        ),
        max_audit_absolute_difference=float(
            np.max(abs(actual["audit"] - expected["audit"]))
        ),
    )


def mechanical_audit(bundle, prediction, recorded):
    if set(recorded) != REPLAY_KEYS:
        raise ValueError("Saved mechanical replay fields differ")
    validate_output(prediction)
    expected, physical = replay_audit(bundle, prediction, recorded)
    if not physical["passed"]:
        raise ArithmeticError("Original substep audit failed")
    check = NumericalChecks()
    check.close(prediction["mean"], recorded["mean"], mean=True)
    check.close(recorded["mean"], expected["mean"], mean=True)
    state = np.vstack([np.zeros((1, 24)), recorded["current"][63::64]])
    check.close(prediction["state"][:, :8], state[:, :8])
    check.close(prediction["state"][:, 20:], state[:, 20:])
    check.close(
        prediction["state"][:, 8:20],
        state[:, 8:20],
        atol=1e-7 + 256 * np.finfo(float).eps * np.max(abs(state[:, 8:20])),
    )
    return dict(
        physical=physical,
        mean_max_difference_mm=check.max_mean_difference_mm,
        mixed_max_difference=check.max_absolute_difference,
        changed_active_substeps=int(
            np.count_nonzero(prediction["masks"].ravel() != recorded["masks"][:, 0])
        ),
    )


def components(out, h):
    return np.stack(
        [load_npz(out / f"model_{h}_{seed}/S.npz")["mean"] for seed in (0, 1, 2)]
    )


def calibrate(out, n, labels):
    if n not in CUTOFFS or labels.shape != (n, 4):
        raise ValueError("Exact chronological calibration label prefix required")
    c = CUTOFFS[n]
    means = components(out, c)[:, c:n]
    return dict(
        component_means=means,
        errors=means - labels[None, c:n],
        sigma=fit_scale(means, labels[c:n]),
    )


def compare(metrics):
    indexed = metrics.set_index(["outer_days", "strategy", "station", "part"])
    rows = []
    for n in CUTOFFS:
        for station in POINTS:
            row = dict(outer_days=n, strategy="S", station=station)
            for reference in ("P0", "R"):
                for part in ("train", "prediction"):
                    a = indexed.loc[n, "S", station, part]
                    b = indexed.loc[n, reference, station, part]
                    for metric in ("rmse_mm", "mae_mm", "crps_mm"):
                        row[f"{part}_{metric}_minus_{reference}"] = float(
                            a[metric] - b[metric]
                        )
            row["strict_mean_improvement"] = all(
                row[f"{p}_{m}_minus_P0"] < -1e-6
                for p in ("train", "prediction")
                for m in ("rmse_mm", "mae_mm")
            )
            rows.append(row)
    return pd.DataFrame(rows)


def scoring(out, source, labels):
    rows = []
    seeds = []
    curves = {}
    for n in CUTOFFS:
        means = components(out, n)
        scales = load_npz(out / f"calibration_{n}_S.npz")["sigma"]
        scores, dist = score_components(means, scales, labels[: n + 180], n, "S")
        rows.extend(scores)
        curves[n, "S"] = dist
        for seed in (0, 1, 2):
            scores, _ = score_components(
                means[seed : seed + 1],
                scales[seed : seed + 1],
                labels[: n + 180],
                n,
                "S",
            )
            seeds.extend(dict(row, seed=seed) for row in scores)
        for strategy in ("P0", "R"):
            curves[n, strategy] = load_npz(source / f"distribution_{n}_{strategy}.npz")
    old = pd.read_csv(source / "metrics.csv")
    old_seeds = pd.read_csv(source / "seed_metrics.csv")
    metrics = pd.concat(
        [old[old.strategy.isin(["P0", "R"])], pd.DataFrame(rows)], ignore_index=True
    )
    seed_metrics = pd.concat(
        [old_seeds[old_seeds.strategy.isin(["P0", "R"])], pd.DataFrame(seeds)],
        ignore_index=True,
    )
    return metrics, seed_metrics, compare(metrics), curves


def plot(out, curves, labels):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    for intervals in (False, True):
        fig, axes = plt.subplots(4, 2, figsize=(12, 13), layout="constrained")
        for col, n in enumerate(CUTOFFS):
            for j, point in enumerate(POINTS):
                ax = axes[j, col]
                for strategy, title, color, style in (
                    ("P0", "B+", "0.5", "-"),
                    ("R", "R (v1.9)", "#bf7b24", "--"),
                    ("S", "S shared mechanics", "#315f9c", "-"),
                ):
                    values = curves[n, strategy]
                    ax.plot(
                        np.arange(1, 181),
                        values["mean"][n - 30 :, j],
                        color=color,
                        linestyle=style,
                        label=title,
                    )
                    if intervals and strategy == "S":
                        ax.fill_between(
                            np.arange(1, 181),
                            values["lower_90"][n - 30 :, j],
                            values["upper_90"][n - 30 :, j],
                            alpha=0.17,
                            color=color,
                            label="S 90% interval",
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
