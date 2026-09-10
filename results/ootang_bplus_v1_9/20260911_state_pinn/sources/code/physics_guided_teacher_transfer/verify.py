"""Read-only checkpoint/array and arithmetic verification; no physical forward."""

import argparse

import numpy as np
import pandas as pd
from scipy.stats import norm
import torch

from physics_guided.features import Scaler
from physics_guided_forecast_error.artifacts import (
    ROOT,
    check_hashes,
    check_index,
    load_observations,
    read_json,
    sha,
    trajectory_name,
)
from physics_guided_sample_learning.core import new_model, predict
from .core import CUTOFFS, POINTS, read_drivers, teacher_features


def verify(out):
    manifest = read_json(out / "manifest.json")
    spec = manifest["specification"]
    learning, physical = ROOT / spec["learning_source"], ROOT / spec["physical_source"]
    protected = read_json(out / "protected_before.json")
    check_hashes(protected)
    check_hashes(manifest["sources"])
    check_hashes(manifest["sources"], out / "sources")
    check_hashes(read_json(out / "frozen_inputs.json"))
    for p in (learning, physical):
        check_index(p)
    if (out / "artifact_manifest.json").exists():
        check_index(out)
    lock = read_json(out / "prediction_lock.json")
    check_hashes(lock["files"], out)
    if (
        lock["frozen_inputs_sha256"] != sha(out / "frozen_inputs.json")
        or len(lock["files"]) != 12
    ):
        raise ValueError("Prediction lock incomplete")
    if sha(out / "input_prefix_792.csv") != manifest["input_sha256"] or manifest[
        "input_sha256"
    ] != sha(learning / "input_prefix_792.csv"):
        raise ValueError("Input copy changed")
    count = read_json(out / "physical_verification.json")
    if any(
        count[k] != v
        for k, v in {
            "reference_trajectory_calls": 4,
            "independent_mechanical_trajectories": 2,
            "independent_substeps": 89728,
        }.items()
    ):
        raise ValueError("Physical budget/accounting differs")
    if any(v > 1e-8 for v in count["reference_replay_errors_mm"].values()):
        raise ArithmeticError("Physical replay did not pass")
    _, _, labels = load_observations(out / "input_prefix_792.csv", 792)
    metrics = pd.read_csv(out / "metrics.csv")
    expected_keys = ["outer_days", "branch", "strategy", "station", "part"]
    if len(metrics) != 192 or metrics.duplicated(expected_keys).any():
        raise ValueError("Metric inventory differs")
    daily = pd.read_csv(out / "daily_decomposition.csv")
    if (
        len(daily) != 4320
        or daily.duplicated(["outer_days", "strategy", "station", "date"]).any()
    ):
        raise ValueError("Daily inventory differs")
    shifts = pd.read_csv(out / "shift_summary.csv")
    if (
        len(shifts) != 24
        or shifts.duplicated(["outer_days", "strategy", "station"]).any()
    ):
        raise ValueError("Shift summary inventory differs")
    prior_metrics = pd.read_csv(learning / "metrics.csv")
    differences = []
    numeric_count = 0

    def close(actual, expected, atol=1e-8):
        nonlocal numeric_count
        a, b = np.asarray(actual), np.asarray(expected)
        if a.shape != b.shape or not np.allclose(
            a, b, rtol=1e-10, atol=atol, equal_nan=True
        ):
            raise ArithmeticError("Independent replay/arithmetic differs")
        finite = np.isfinite(a) & np.isfinite(b)
        if finite.any():
            differences.append(float(abs(a[finite] - b[finite]).max()))
        numeric_count += a.size

    all_means = {}
    for n, c in CUTOFFS.items():
        drivers = read_drivers(out / "input_prefix_792.csv", n)
        info = read_json(learning / f"scaler_{n}.json")
        scaler = Scaler(*[np.asarray(info[k]) for k in ("mean", "scale", "floor")])
        models = {}
        for strategy in ("IN", "OOF"):
            for seed in range(3):
                model = new_model(seed)
                model.load_state_dict(
                    torch.load(
                        learning / f"mean_{n}_{strategy}_{seed}/e100.pt",
                        weights_only=True,
                    )
                )
                models[strategy, seed] = model
        audit = read_json(out / f"old_physical_{n}.audit.json")
        if (
            not audit["valid"]
            or audit["zero_trajectory_max_error_mm"] > 1e-5
            or audit["substeps_checked"] != (n + 179) * 64
        ):
            raise ArithmeticError("Independent physical audit differs")
        prefix = read_json(out / f"prefix_{n}.json")
        with (
            np.load(ROOT / prefix["prior"]) as old,
            np.load(out / f"old_physical_{n}.npz") as extended,
        ):
            for key in old.files:
                a, b = old[key], extended[key]
                if b.shape != a.shape:
                    b = b[: len(a)]
                if key == "dates":
                    if not np.array_equal(a, b):
                        raise ValueError("Old physical prefix dates changed")
                else:
                    close(a, b)
            checks = extended["substep_audit"]
            if (
                checks[:, 0].min() < -1e-8
                or checks[:, 1].min() < -1e-7
                or checks[:, 2].max() > 1e-7
                or checks[:, 3].min() < 0
            ):
                raise ArithmeticError("Stored substep constraints failed")
        roles = {
            "old_fit_context": (30, c),
            "scale_context": (c, n),
            "outer_train": (30, n),
            "prediction": (n, n + 180),
        }
        for branch in ("OLD", "NEW"):
            path = (
                out / f"old_physical_{n}.npz"
                if branch == "OLD"
                else physical / trajectory_name(n, "B")
            )
            base, x = teacher_features(path, drivers, n)
            for strategy in ("P0", "IN", "OOF"):
                expected = (
                    base[None]
                    if strategy == "P0"
                    else np.stack(
                        [
                            predict(models[strategy, k], base, x, scaler, chunk=17)
                            for k in range(3)
                        ]
                    )
                )
                with (
                    np.load(out / f"prediction_{n}_{branch}_{strategy}.npz") as saved,
                    np.load(learning / f"calibration_{n}_{strategy}.npz") as cal,
                ):
                    mean, scale = saved["means"], saved["scales"]
                    if (
                        not np.isfinite(mean[:, 30:]).all()
                        or not np.isfinite(scale).all()
                        or (scale <= 0).any()
                        or not np.array_equal(scale, cal["scales"])
                    ):
                        raise ArithmeticError(
                            "Invalid prediction or changed frozen scale"
                        )
                    close(mean, expected)
                    close(scale, cal["scales"], atol=0)
                all_means[n, branch, strategy] = mean.mean(axis=0)
                if branch == "NEW":
                    with np.load(learning / f"prediction_{n}_{strategy}.npz") as saved:
                        close(mean, saved["means"])
                with np.load(
                    out / f"distribution_{n}_{branch}_{strategy}.npz"
                ) as saved:
                    dist = {k: saved[k] for k in saved.files}
                close(dist["mean"], mean[:, 30:].mean(axis=0))
                for level in (80, 90, 95):
                    for side, p in (
                        ("lower", (1 - level / 100) / 2),
                        ("upper", (1 + level / 100) / 2),
                    ):
                        values = np.mean(
                            norm.cdf(
                                (dist[f"{side}_{level}"] - mean[:, 30:])
                                / scale[:, None]
                            ),
                            axis=0,
                        )
                        close(
                            values,
                            np.full_like(values, p),
                            atol=1e-6 / (float(scale.min()) * np.sqrt(2 * np.pi))
                            + 1e-12,
                        )
                for part, (a, b) in roles.items():
                    y = labels[a:b]
                    m, s = mean[:, a:b], scale[:, None]
                    error = m.mean(axis=0) - y
                    z = (y[None] - m) / s
                    first = np.mean(
                        (y[None] - m) * (2 * norm.cdf(z) - 1) + 2 * s * norm.pdf(z),
                        axis=0,
                    )
                    d = m[:, None] - m[None, :]
                    sd = np.sqrt(s[:, None] ** 2 + s[None, :] ** 2)
                    z = d / sd
                    crps = first - 0.5 * np.mean(
                        d * (2 * norm.cdf(z) - 1) + 2 * sd * norm.pdf(z), axis=(0, 1)
                    )
                    for j, station in enumerate(POINTS):
                        row = metrics[
                            (metrics.outer_days == n)
                            & (metrics.branch == branch)
                            & (metrics.strategy == strategy)
                            & (metrics.part == part)
                            & (metrics.station == station)
                        ].iloc[0]
                        close(
                            [row.first_index, row.end_index_exclusive, row.days],
                            [a, b, b - a],
                        )
                        close(
                            [row.rmse_mm, row.mae_mm, row.bias_mm, row.crps_mm],
                            [
                                np.sqrt(np.mean(error[:, j] ** 2)),
                                abs(error[:, j]).mean(),
                                error[:, j].mean(),
                                crps[:, j].mean(),
                            ],
                        )
                        for level in (80, 90, 95):
                            lo, hi = (
                                dist[f"lower_{level}"][a - 30 : b - 30, j],
                                dist[f"upper_{level}"][a - 30 : b - 30, j],
                            )
                            target = y[:, j]
                            values = [
                                ((lo <= target) & (target <= hi)).mean(),
                                (hi - lo).mean(),
                                (
                                    hi
                                    - lo
                                    + 2
                                    / (1 - level / 100)
                                    * (
                                        np.maximum(lo - target, 0)
                                        + np.maximum(target - hi, 0)
                                    )
                                ).mean(),
                            ]
                            close(
                                [
                                    row[f"coverage_{level}"],
                                    row[f"width_{level}_mm"],
                                    row[f"interval_score_{level}_mm"],
                                ],
                                values,
                            )
                        if branch == "NEW" and part in ("outer_train", "prediction"):
                            old = prior_metrics[
                                (prior_metrics.outer_days == n)
                                & (prior_metrics.strategy == strategy)
                                & (prior_metrics.station == station)
                                & (
                                    prior_metrics.part
                                    == ("train" if part == "outer_train" else part)
                                )
                            ].iloc[0]
                            for key in old.index:
                                if key not in (
                                    "outer_days",
                                    "strategy",
                                    "station",
                                    "part",
                                ):
                                    close(row[key], old[key])
    for n in CUTOFFS:
        for strategy in ("P0", "IN", "OOF"):
            for j, station in enumerate(POINTS):
                d = daily[
                    (daily.outer_days == n)
                    & (daily.strategy == strategy)
                    & (daily.station == station)
                ].sort_values("lead_day")
                if list(d.date) != list(
                    pd.date_range("2016-07-01", periods=n + 180).strftime("%Y-%m-%d")[
                        n:
                    ]
                ):
                    raise ValueError("Matched prediction dates differ")
                close(d.lead_day, np.arange(1, 181))
                old, new = (
                    all_means[n, "OLD", strategy][n:, j],
                    all_means[n, "NEW", strategy][n:, j],
                )
                old_base, new_base = (
                    all_means[n, "OLD", "P0"][n:, j],
                    all_means[n, "NEW", "P0"][n:, j],
                )
                for key, value in {
                    "old_mean_mm": old,
                    "new_mean_mm": new,
                    "old_physical_mm": old_base,
                    "new_physical_mm": new_base,
                    "observed_mm": labels[n : n + 180, j],
                    "physical_change_mm": new_base - old_base,
                    "correction_change_mm": (new - new_base) - (old - old_base),
                    "total_change_mm": new - old,
                }.items():
                    close(d[key], value)
                close(d.total_change_mm, d.physical_change_mm + d.correction_change_mm)
                summary = shifts[
                    (shifts.outer_days == n)
                    & (shifts.strategy == strategy)
                    & (shifts.station == station)
                ].iloc[0]
                for name in ("physical", "correction", "total"):
                    values = d[f"{name}_change_mm"].to_numpy()
                    close(
                        [
                            summary[f"{name}_mean_mm"],
                            summary[f"{name}_rms_mm"],
                            summary[f"{name}_max_abs_mm"],
                        ],
                        [
                            values.mean(),
                            np.linalg.norm(values) / np.sqrt(180),
                            abs(values).max(),
                        ],
                    )
    comparisons = pd.read_csv(out / "comparisons.csv")
    if (
        len(comparisons) != 32
        or comparisons.duplicated(["outer_days", "branch", "strategy", "station"]).any()
    ):
        raise ValueError("Comparison inventory differs")
    for row in comparisons.itertuples():
        for name, reference in (("own", row.branch), ("current", "NEW")):
            values = []
            for part in ("outer_train", "prediction"):
                chosen = metrics[
                    (metrics.outer_days == row.outer_days)
                    & (metrics.branch == row.branch)
                    & (metrics.strategy == row.strategy)
                    & (metrics.station == row.station)
                    & (metrics.part == part)
                ].iloc[0]
                base = metrics[
                    (metrics.outer_days == row.outer_days)
                    & (metrics.branch == reference)
                    & (metrics.strategy == "P0")
                    & (metrics.station == row.station)
                    & (metrics.part == part)
                ].iloc[0]
                for metric in ("rmse_mm", "mae_mm", "crps_mm"):
                    difference = chosen[metric] - base[metric]
                    close(getattr(row, f"{part}_{metric}_minus_{name}"), difference)
                    if metric != "crps_mm":
                        values.append(difference)
            if bool(getattr(row, f"strict_improvement_vs_{name}")) != all(
                value < -1e-6 for value in values
            ):
                raise ArithmeticError("Baseline reference/strict acceptance differs")
    return dict(
        passed=True,
        metrics_rows=192,
        daily_rows=4320,
        shift_summary_rows=24,
        comparisons_rows=32,
        numeric_values_checked=numeric_count,
        max_absolute_difference=max(differences),
        protected_files_checked=len(protected),
        source_files_checked=len(manifest["sources"]),
        new_reference_trajectory_calls=0,
        new_neural_updates=0,
        scope="Prefix, stored physical audits, checkpoint replay, NEW v1.5 identity, independent scores/CDF and same-day decomposition; no physical or neural retraining.",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    print(verify(ROOT / "results/ootang_bplus_v1_6" / args.run_id))
