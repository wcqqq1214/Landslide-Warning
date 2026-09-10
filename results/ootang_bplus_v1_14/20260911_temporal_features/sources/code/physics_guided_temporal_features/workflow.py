"""Frozen teacher inputs and chronological auxiliary outputs."""

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from physics_guided_forecast_error.artifacts import (
    ROOT,
    array_sha,
    read_json,
    sha,
    trajectory_name,
)
from physics_guided_forecast_error.core import choose_teacher
from physics_guided_rate_diagnostics.workflow import (
    DOMAINS,
    FEATURES,
    load_npz,
    no_model_runtime,
    source_guard as frozen_guard,
)
from .core import DIMENSIONS, POINTS, comparisons, score, trailing_mean

CONFIG = ROOT / "config/ootang_bplus_temporal_features.v1_14.json"
CONFIG_SHA = "0b31881f267a4eb69d6a618c2dda675f22d22f17787fe9bb14d23cf361c4e2b5"
PLAN_SHA = "0ddd13ead2a50c583ab2c1a0ef1271a08089903b32e8812d53d8a76f285d7c58"
NAMES = [
    *FEATURES,
    *["history30_" + name for name in FEATURES],
    *[
        name + "_" + domain
        for name in ("s", "p", "rb", "rc", "rE", "background")
        for domain in DOMAINS
    ],
]


def now():
    return datetime.now(timezone.utc).isoformat()


def specification():
    spec = read_json(CONFIG)
    if sha(CONFIG) != CONFIG_SHA or sha(ROOT / spec["protocol"]) != PLAN_SHA:
        raise ValueError("Fixed temporal comparison design changed")
    return spec


def source_guard(spec):
    protected = frozen_guard(spec)
    name = "docs/ootang_bplus_rate_diagnostics_results.v1.13.md"
    protected[name] = sha(ROOT / name)
    return protected


def read_labels(path, n):
    if n not in (252, 342, 432, 612):
        raise ValueError("Unregistered observation prefix")
    frame = pd.read_csv(path, nrows=n, usecols=["Date", *[p + "/mm" for p in POINTS]])
    dates = pd.DatetimeIndex(pd.to_datetime(frame.Date))
    if not dates.equals(pd.date_range("2016-07-01", periods=n)):
        raise ValueError("Exact observation prefix required")
    values = frame[[p + "/mm" for p in POINTS]].to_numpy(float)
    if not np.isfinite(values).all():
        raise ValueError("Nonfinite labels; no imputation")
    return values


def copy_input(spec, target):
    with (ROOT / spec["input_csv"]).open("rb") as source, target.open("xb") as output:
        for _ in range(spec["max_label_prefix"] + 1):
            line = source.readline()
            if not line:
                raise ValueError("Source CSV prefix is incomplete")
            output.write(line)


def load_case(spec, origin):
    i = spec["origins"].index(origin)
    end, recipe = spec["ends"][i], spec["recipes"][i]
    source = ROOT / spec["physical_source"]
    records = read_json(source / "fitted_parameters_locked.json")["records"]
    selected = choose_teacher(
        {r: records[f"{origin}_{r}"]["record"]["objective"] for r in ("A", "B")}
    )
    if selected != recipe or not records[f"{origin}_{recipe}"]["valid"]:
        raise ValueError("Original training-only teacher selection changed")
    record = records[f"{origin}_{recipe}"]["record"]
    physical = load_npz(source / trajectory_name(origin, recipe))
    if (
        record["fit_days"] != origin
        or physical["mean"].shape != (origin + 180, 4)
        or not np.array_equal(physical["theta"], record["theta"])
    ):
        raise ValueError("Teacher parameter or training boundary changed")
    with np.load(ROOT / spec["driver_source"], allow_pickle=False) as drivers:
        forcing, dates, y0 = (
            drivers["forcing"][:end].copy(),
            drivers["dates"][:end].copy(),
            drivers["y0"].copy(),
        )
    if (
        not np.array_equal(dates, physical["dates"][:end])
        or array_sha(forcing[:origin]) != record["training_forcing_sha256"]
    ):
        raise ValueError("Original forcing identity changed")
    hydro = np.column_stack(
        [
            forcing[:, 0],
            forcing[:, 1],
            np.r_[0.0, np.diff(forcing[:, 1])],
            physical["rain_head"][:end],
            physical["moisture"][:end],
            physical["reservoir_head"][:end],
        ]
    )
    mechanical = np.column_stack(
        [
            (physical["coordinates"] - physical["background"])[:end],
            *[
                physical[k][:end]
                for k in (
                    "plastic",
                    "basal_reaction",
                    "contact",
                    "bulk_reaction",
                    "background",
                )
            ],
        ]
    )
    raw = np.column_stack(
        [hydro, trailing_mean(hydro, spec["history_days"]), mechanical]
    )
    result = dict(
        raw_features=raw,
        hydro=hydro,
        mechanics=mechanical,
        forcing=forcing,
        dates=dates,
        base_mean=physical["mean"][:end],
        theta=physical["theta"],
        observation=physical["observation_matrix"],
        y0=y0,
    )
    if raw.shape != (end, 48) or any(
        not np.isfinite(v).all() for k, v in result.items() if k != "dates"
    ):
        raise ValueError("Malformed auxiliary inputs")
    no_model_runtime()
    return result, record


def read_outputs(out, origin, base):
    outputs = {"P0": base, "C": load_npz(out / f"constant_{origin}.npz")["prediction"]}
    for name in DIMENSIONS:
        outputs[name] = load_npz(out / f"model_{origin}_{name}.npz")["prediction"]
    return outputs


def metric_tables(out, spec, labels):
    frames = []
    for origin, end in zip(spec["origins"], spec["ends"]):
        case = load_npz(out / f"case_{origin}.npz")
        frames.append(
            score(
                case["base_mean"],
                read_outputs(out, origin, case["base_mean"]),
                labels[:end],
                origin,
                spec["direction_tolerance_mm"],
            )
        )
    metrics = pd.concat(frames, ignore_index=True)
    comparison = comparisons(metrics)
    if (
        len(metrics) != spec["metric_rows"]
        or len(comparison) != spec["comparison_rows"]
    ):
        raise ValueError("Missing registered metric rows")
    return metrics, comparison


def plot(out, spec, labels):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(4, 3, figsize=(15, 11), layout="constrained")
    for col, (origin, end) in enumerate(zip(spec["origins"], spec["ends"])):
        base = load_npz(out / f"case_{origin}.npz")["base_mean"]
        outputs = read_outputs(out, origin, base)
        x = np.arange(1, end - origin + 1)
        for j, point in enumerate(POINTS):
            ax = axes[j, col]
            ax.plot(
                x,
                labels[origin:end, j] - base[origin:, j],
                color="black",
                label="Observed need (diagnostic)",
            )
            for name, color, style in (
                ("C", "0.5", ":"),
                ("H", "#c47c27", "--"),
                ("HH", "#23825e", "-."),
                ("HHS", "#315f9c", "-"),
            ):
                ax.plot(
                    x,
                    outputs[name][origin:, j] - base[origin:, j],
                    color=color,
                    linestyle=style,
                    label=name,
                )
            ax.axhline(0, color="0.6", linewidth=0.6, label="P0 (zero correction)")
            ax.set(
                title=f"{point} | origin {origin}",
                xlabel="Forward day",
                ylabel="Auxiliary correction (mm)",
            )
            ax.grid(alpha=0.15)
    handles, names = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, names, loc="outside upper center", ncol=6)
    fig.savefig(out / "temporal_corrections.png", dpi=150)
    plt.close(fig)
