"""Immutable inputs, table verification, and a signed-group diagnostic figure."""

import numpy as np
import pandas as pd

from physics_guided_forecast_error.artifacts import (
    ROOT,
    check_hashes,
    check_index,
    read_json,
    sha,
)
from physics_guided_rate_diagnostics.workflow import (
    load_npz,
    no_model_runtime,
    source_guard as frozen_guard,
)
from physics_guided_temporal_features.verify import Checks
from physics_guided_temporal_features.workflow import NAMES, read_labels
from .core import POINTS, build_tables, decompose, slices

CONFIG = ROOT / "config/ootang_bplus_temporal_decomposition.v1_15.json"
CONFIG_SHA = "060f5317ac4327c7372029081ec47923ba07631b332416b5985dc6705e7fc830"
PLAN_SHA = "0e843adb2a4270cb01173380dc6e130aa4e5232b230014c6d86d7192b8bdeb96"


def specification():
    spec = read_json(CONFIG)
    if sha(CONFIG) != CONFIG_SHA or sha(ROOT / spec["protocol"]) != PLAN_SHA:
        raise ValueError("Frozen decomposition design changed")
    return spec


def source_guard(spec):
    protected = frozen_guard(spec)
    name = "docs/ootang_bplus_temporal_features_results.v1.14.md"
    protected[name] = sha(ROOT / name)
    return protected


def load_daily(spec):
    source = ROOT / spec["source_run"]
    if read_json(source / "manifest.json")["feature_names"] != NAMES:
        raise ValueError("Source feature order changed")
    labels = read_labels(source / "input_prefix_612.csv", 612)
    result = {}
    for origin, end in zip(spec["origins"], spec["ends"]):
        case = load_npz(source / f"case_{origin}.npz")
        scaler = load_npz(source / f"scaler_{origin}.npz")
        if case["raw_features"].shape != (end, 48):
            raise ValueError("Frozen feature calendar changed")
        z = (case["raw_features"] - scaler["mean"]) / scaler["std"]
        data = dict(z=z, dates=case["dates"], demand=labels[:end] - case["base_mean"])
        for name, dimensions in spec["representations"].items():
            model = load_npz(source / f"model_{origin}_{name}.npz")
            values = decompose(z[:, :dimensions], model["coefficient"], origin)
            data.update({name + "_" + k: v for k, v in values.items()})
            data[name + "_coefficient"] = model["coefficient"]
            data[name + "_original"] = model["correction"]
        if any(
            not np.isfinite(v).all()
            for k, v in data.items()
            if k != "dates" and not k.endswith("_groups")
        ):
            raise ValueError("Nonfinite frozen decomposition")
        result[origin] = data
    no_model_runtime()
    return result


def verify(out, sealed=True):
    spec = specification()
    if sealed:
        check_index(out)
        completed = read_json(out / "completed.json")
        if (
            not completed["execution_complete"]
            or not completed["numerical_verification"]
            or not 0 < completed["elapsed_seconds"] <= spec["timeout_seconds"]
            or read_json(out / "launcher.json")["exitcode"] != 0
        ):
            raise ValueError("Original diagnostic run was not successful")
    manifest = read_json(out / "manifest.json")
    if (
        manifest["specification"] != spec
        or manifest["config_sha256"] != CONFIG_SHA
        or manifest["plan_sha256"] != PLAN_SHA
        or manifest["feature_names"] != NAMES
    ):
        raise ValueError("Diagnostic specification differs")
    protected = source_guard(spec)
    if protected != read_json(out / "protected_before.json"):
        raise ValueError("Protected inventory changed")
    check_hashes(manifest["sources"])
    check_hashes(manifest["sources"], out / "sources")
    originals = load_daily(spec)
    check, daily = Checks(spec["numeric_atol"]), {}
    for origin, expected in originals.items():
        actual = load_npz(out / f"prefix_{origin}.npz")
        if set(actual) != set(expected):
            raise ValueError("Daily schema changed")
        for key, value in expected.items():
            if key == "dates" or key.endswith("_groups"):
                if not np.array_equal(actual[key], value):
                    raise ValueError("Date or group axis changed")
            else:
                check.close(actual[key], value)
        for name, dimensions in spec["representations"].items():
            z, coefficient = actual["z"][:, :dimensions], actual[name + "_coefficient"]
            groups = slices(dimensions)
            # Independent scalar-feature accumulation, including the boundary split.
            parts = np.zeros_like(actual[name + "_parts"])
            excess = np.zeros_like(parts)
            parts[:, 0] = 100 * coefficient[0]
            for k, ix in enumerate(groups.values(), 1):
                for j in range(ix.start, ix.stop):
                    lower, upper = min(z[30:origin, j]), max(z[30:origin, j])
                    clipped = np.minimum(np.maximum(z[:, j], lower), upper)
                    parts[:, k] += 100 * z[:, j, None] * coefficient[j + 1]
                    excess[:, k] += (
                        100 * (z[:, j] - clipped)[:, None] * coefficient[j + 1]
                    )
            check.close(actual[name + "_parts"], parts, mm=True)
            check.close(actual[name + "_excess"], excess, mm=True)
            check.close(parts.sum(axis=1), actual[name + "_original"], mm=True)
            check.close(excess[30:origin], np.zeros_like(excess[30:origin]), mm=True)
            check.close(
                (parts - excess).sum(axis=1) + excess.sum(axis=1),
                actual[name + "_original"],
                mm=True,
            )
        daily[origin] = actual
    expected_tables = build_tables(spec, daily, NAMES)
    tables = {}
    for name, expected in expected_tables.items():
        table = pd.read_csv(out / (name + ".csv"))
        pd.testing.assert_frame_equal(
            table,
            expected,
            check_dtype=False,
            check_exact=False,
            atol=spec["numeric_atol"],
            rtol=0,
        )
        tables[name] = table
    source_metrics = pd.read_csv(ROOT / spec["source_run"] / "metrics.csv")
    columns = [
        "days",
        "rmse_mm",
        "mae_mm",
        "bias_mm",
        "demand_mean_mm",
        "correction_mean_mm",
        "active_days",
        "opposed_days",
        "opposed_fraction",
    ]
    for row in tables["total"].itertuples(index=False):
        original = source_metrics[
            (source_metrics.origin == row.origin)
            & (source_metrics.strategy == row.representation)
            & (source_metrics.part == row.part)
            & (source_metrics.station == row.station)
        ]
        if len(original) != 1:
            raise ValueError("Ambiguous original point metric")
        a, b = (
            np.array([getattr(row, k) for k in columns]),
            original[columns].iloc[0].to_numpy(float),
        )
        if not np.array_equal(np.isnan(a), np.isnan(b)):
            raise ValueError("Unavailable direction fraction differs")
        check.close(a[~np.isnan(a)], b[~np.isnan(b)], mm=True)
        check.close(
            row.boundary_mean_mm + row.excess_mean_mm, row.correction_mean_mm, mm=True
        )
        selected = tables["group"][
            (tables["group"].origin == row.origin)
            & (tables["group"].representation == row.representation)
            & (tables["group"].part == row.part)
            & (tables["group"].station == row.station)
        ]
        check.close(selected.mean_mm.sum(), row.correction_mean_mm, mm=True)
    for row in tables["ranges"].itertuples(index=False):
        z = daily[row.origin]["z"][:, NAMES.index(row.feature)]
        lower, upper = (
            min(z[row.reference_start : row.origin]),
            max(z[row.reference_start : row.origin]),
        )
        below = sum(v < lower for v in z[row.origin :])
        above = sum(v > upper for v in z[row.origin :])
        check.close(
            [
                row.reference_min_z,
                row.reference_max_z,
                row.below_days,
                row.above_days,
                row.outside_fraction,
            ],
            [lower, upper, below, above, (below + above) / row.days],
        )
    for row in tables["group"].itertuples(index=False):
        check.close(row.rms_mm**2, row.mean_mm**2 + row.sd_mm**2)
        check.close(row.boundary_mean_mm + row.excess_mean_mm, row.mean_mm, mm=True)
    if read_json(out / "execution.json") != {
        k: v for k, v in spec.items() if k.startswith("new_")
    }:
        raise ValueError("Unexpected fitting/model execution budget")
    check_hashes(protected)
    no_model_runtime()
    return dict(
        passed=True,
        protected_files=len(protected),
        numeric_values_checked=int(check.values),
        max_difference_mm=check.mm_maximum,
        max_mixed_absolute_difference=check.maximum,
        table_rows={k: len(v) for k, v in tables.items()},
        original_metric_rows_verified=72,
        decompositions_verified=9,
        new_fits=0,
        new_neural_calls=0,
        new_physical_calls=0,
    )


def plot(out, spec, tables):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    groups = ["intercept", *slices(48)]
    values, labels = [], []
    for origin in spec["origins"]:
        for point in POINTS:
            group = tables["group"][
                (tables["group"].origin == origin)
                & (tables["group"].station == point)
                & (tables["group"].representation == "HHS")
                & (tables["group"].part == "forward")
            ].set_index("group")
            total = tables["total"][
                (tables["total"].origin == origin)
                & (tables["total"].station == point)
                & (tables["total"].representation == "HHS")
                & (tables["total"].part == "forward")
            ].iloc[0]
            values.append(
                [
                    *[group.loc[g, "mean_mm"] for g in groups],
                    total.correction_mean_mm,
                    total.demand_mean_mm,
                ]
            )
            labels.append(f"{origin} | {point}")
    values = np.asarray(values)
    scale = max(float(np.max(abs(values))), 1)
    fig, ax = plt.subplots(figsize=(14, 8), layout="constrained")
    picture = ax.imshow(values, cmap="RdBu_r", vmin=-scale, vmax=scale, aspect="auto")
    ax.set_xticks(
        range(len(groups) + 2), [*groups, "total", "need"], rotation=35, ha="right"
    )
    ax.set_yticks(range(len(labels)), labels)
    ax.set_title(
        "Frozen HHS: forward mean linear terms, total correction and observed need"
    )
    for i, row in enumerate(values):
        for j, value in enumerate(row):
            ax.text(
                j,
                i,
                f"{value:.1f}",
                ha="center",
                va="center",
                fontsize=10,
                color="white" if abs(value) > scale * 0.55 else "black",
            )
    ax.axvline(len(groups) - 0.5, color="black", linewidth=1)
    for y in (3.5, 7.5):
        ax.axhline(y, color="black", linewidth=1)
    fig.colorbar(picture, ax=ax, label="Signed mean (mm)", shrink=0.8)
    fig.savefig(out / "group_means.png", dpi=150)
    plt.close(fig)
