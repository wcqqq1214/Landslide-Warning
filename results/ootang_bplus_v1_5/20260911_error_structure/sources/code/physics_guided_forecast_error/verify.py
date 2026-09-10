"""Independent arithmetic from frozen arrays; no fitting and no physical forward."""

import argparse
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from .artifacts import (
    ROOT,
    array_sha,
    check_hashes,
    check_index,
    load_observations,
    read_json,
    sha,
    trajectory_name,
)


def verify(out):
    out = Path(out)
    manifest = read_json(out / "manifest.json")
    spec = manifest["specification"]
    source = ROOT / spec["source_run"]
    protected = read_json(out / "protected_before.json")
    check_hashes(protected)
    check_hashes(manifest["sources"])
    check_hashes(manifest["sources"], out / "sources")
    check_index(source)
    if (out / "artifact_manifest.json").exists():
        check_index(out)
    if sha(out / "input_prefix_792.csv") != manifest["input_sha256"] or manifest[
        "input_sha256"
    ] != sha(source / "input_prefix_792.csv"):
        raise ValueError("Input snapshot differs from source")
    for filename, digest in (
        ("config/ootang_bplus_error_structure.v1_5.json", manifest["config_sha256"]),
        (spec["protocol"], manifest["protocol_sha256"]),
    ):
        if sha(out / "sources" / filename) != digest:
            raise ValueError("Protocol/config snapshot changed")
    dates, forcing, y = load_observations(out / "input_prefix_792.csv", 792)
    points = ("ATU1", "ATU5", "MJ3", "MJ1")
    metrics = pd.read_csv(out / "error_metrics.csv")
    daily = pd.read_csv(out / "daily_errors.csv")
    corrs = pd.read_csv(out / "driver_correlations.csv")
    if (len(metrics), len(daily), len(corrs)) != (32, 5760, 128):
        raise ValueError("Missing or duplicated output rows")
    if (
        metrics.duplicated(["fit_days", "recipe", "station"]).any()
        or daily.duplicated(["fit_days", "recipe", "station", "date"]).any()
        or corrs.duplicated(["fit_days", "recipe", "station", "feature"]).any()
    ):
        raise ValueError("Duplicate output keys")
    selected = read_json(out / "teacher_selection.json")
    records = read_json(source / "fitted_parameters_locked.json")["records"]
    max_error = 0.0
    numeric_checks = 0

    def close(actual, expected):
        nonlocal max_error, numeric_checks
        if expected is None:
            if not pd.isna(actual):
                raise ArithmeticError("A degenerate statistic must be NA")
            return
        a, b = np.asarray(actual, dtype=float), np.asarray(expected, dtype=float)
        if a.shape != b.shape or not np.allclose(a, b, rtol=1e-10, atol=1e-8):
            raise ArithmeticError(
                f"Independent recomputation differs: {actual} vs {expected}"
            )
        max_error = max(max_error, float(np.max(abs(a - b))))
        numeric_checks += a.size

    for n in (252, 342, 432, 612):
        objective = {r: records[f"{n}_{r}"]["record"]["objective"] for r in ("A", "B")}
        choice = "A" if objective["A"] <= objective["B"] + 1e-12 else "B"
        if selected[str(n)] != dict(objectives=objective, selected=choice):
            raise ValueError("Teacher selection differs from own-training J0")
        for recipe in ("A", "B"):
            record = records[f"{n}_{recipe}"]["record"]
            with np.load(
                source / trajectory_name(n, recipe), allow_pickle=False
            ) as saved:
                mu = saved["mean"]
                if not np.array_equal(
                    saved["theta"], record["theta"]
                ) or not np.array_equal(saved["dates"], dates[: n + 180]):
                    raise ValueError("Parameter/date identity differs")
            if record["training_label_sha256"] != array_sha(y[:n]) or record[
                "training_forcing_sha256"
            ] != array_sha(forcing[:n]):
                raise ValueError("Parameter training prefix differs")
            if sha(ROOT / record["source"]) != record["source_sha256"]:
                raise ValueError("Parameter source differs")
            error = mu - y[: n + 180]
            j0 = (
                np.square(error[:n] / 100).sum()
                + n * np.square(error[n - 1]).sum() / 100
            )
            close(j0, record["objective"])
            for j, station in enumerate(points):
                key = (
                    (metrics.fit_days == n)
                    & (metrics.recipe == recipe)
                    & (metrics.station == station)
                )
                row = metrics[key].iloc[0]
                d = daily[
                    (daily.fit_days == n)
                    & (daily.recipe == recipe)
                    & (daily.station == station)
                ].sort_values("lead_day")
                c = corrs[
                    (corrs.fit_days == n)
                    & (corrs.recipe == recipe)
                    & (corrs.station == station)
                ]
                if (
                    len(d) != 180
                    or len(c) != 4
                    or not np.array_equal(d.date, dates[n : n + 180])
                ):
                    raise ValueError("Prediction coverage differs")
                if (
                    not bool(row.selected_by_training) == (recipe == choice)
                    or not (d.selected_by_training == (recipe == choice)).all()
                    or not (c.selected_by_training == (recipe == choice)).all()
                ):
                    raise ValueError("Incorrect selected-teacher flag")
                close(d.lead_day.to_numpy(), np.arange(1, 181))
                e, tr = error[n:, j], error[30:n, j]
                delta_mu = mu[n:, j] - mu[n - 30 : n + 150, j]
                delta_y = y[n : n + 180, j] - y[n - 30 : n + 150, j]
                de = delta_mu - delta_y
                for name, expected in {
                    "predicted_mm": mu[n:, j],
                    "observed_mm": y[n : n + 180, j],
                    "error_mm": e,
                    "predicted_increment30_mm": delta_mu,
                    "observed_increment30_mm": delta_y,
                    "increment30_error_mm": de,
                }.items():
                    close(d[name].to_numpy(), expected)
                slope, intercept = np.polyfit(np.arange(1, 181), e, 1)
                removed = e - (slope * np.arange(1, 181) + intercept)

                def rmse(a):
                    return float(np.sqrt(np.square(a).mean()))

                expected = dict(
                    train_days=n - 30,
                    prediction_days=180,
                    train_rmse_mm=rmse(tr),
                    train_mae_mm=float(abs(tr).mean()),
                    train_bias_mm=float(tr.mean()),
                    prediction_rmse_mm=rmse(e),
                    prediction_mae_mm=float(abs(e).mean()),
                    prediction_bias_mm=float(e.mean()),
                    last_training_error_mm=float(error[n - 1, j]),
                    first_prediction_error_mm=float(e[0]),
                    last_prediction_error_mm=float(e[-1]),
                    growth_error_mm_per_day=float((e[-1] - error[n - 1, j]) / 180),
                    increment30_rmse_mm=rmse(de),
                    increment30_mae_mm=float(abs(de).mean()),
                    oracle_constant_removed_rmse_mm=rmse(e - e.mean()),
                    oracle_linear_removed_rmse_mm=rmse(removed),
                    oracle_linear_slope_mm_per_day=float(slope),
                    oracle_linear_explained_error_fraction=float(
                        1 - np.square(removed).sum() / np.square(e).sum()
                    )
                    if np.square(e).sum()
                    else None,
                    observed_peak30_increment_mm=float(max(delta_y)),
                    predicted_peak30_increment_mm=float(max(delta_mu)),
                    peak30_date_difference_days=int(
                        np.argmax(delta_mu) - np.argmax(delta_y)
                    ),
                )
                endpoints = error[[n - 1, n + 59, n + 119, n + 179], j]
                for block, value in enumerate(np.diff(endpoints) / 60):
                    expected[
                        f"growth_error_days_{block * 60 + 1}_{block * 60 + 60}_mm_per_day"
                    ] = value
                for name, value in expected.items():
                    close(row[name], value)
                if (
                    row.observed_peak30_date != dates[n + np.argmax(delta_y)]
                    or row.predicted_peak30_date != dates[n + np.argmax(delta_mu)]
                ):
                    raise ArithmeticError("First peak date differs")
                features = dict(
                    rain_mm=forcing[n : n + 180, 0],
                    rain_sum30_mm=np.array(
                        [sum(forcing[t - 29 : t + 1, 0]) for t in range(n, n + 180)]
                    ),
                    rwl_m=forcing[n : n + 180, 1],
                    rwl_change30_m=forcing[n : n + 180, 1]
                    - forcing[n - 30 : n + 150, 1],
                )
                for name, values in features.items():
                    close(d[name].to_numpy(), values)
                    crow = c[c.feature == name].iloc[0]
                    close(crow.days, 180)
                    p = (
                        None
                        if min(np.std(values), np.std(de)) <= 1e-12
                        else np.corrcoef(de, values)[0, 1]
                    )
                    close(crow.pearson, p)

    inventories = read_json(out / "sample_inventory.json")
    if len(inventories) != 4 or {(r["outer_days"], r["mode"]) for r in inventories} != {
        (n, mode) for n in (432, 612) for mode in ("complete_180", "available_prefix")
    }:
        raise ValueError("Inventory coverage differs")
    for item in inventories:
        n, mode = item["outer_days"], item["mode"]
        origins = [
            k
            for k in (252, 342, 432)
            if k < n and (mode == "available_prefix" or k + 180 <= n)
        ]
        counter = Counter(t for k in origins for t in range(k, min(k + 180, n)))
        expected_windows = []
        for k in origins:
            end = min(k + 180, n)
            expected_windows.append(
                dict(
                    teacher_fit_days=k,
                    first_date=dates[k],
                    last_date=dates[end - 1],
                    first_index=k,
                    end_index_exclusive=end,
                    window_days=end - k,
                    candidate_mean_days=max(0, min(end, n - 60) - k),
                    candidate_scale_days=end - max(k, min(end, n - 60)),
                )
            )
        if item["windows"] != expected_windows or item["date_multiplicity"] != {
            dates[t]: v for t, v in sorted(counter.items())
        }:
            raise ArithmeticError("Window availability or repeated dates differ")
        for name, expected in dict(
            window_days=sum(counter.values()),
            unique_days=len(counter),
            repeated_window_days=sum(counter.values()) - len(counter),
            max_date_multiplicity=max(counter.values()),
            candidate_mean_unique_days=sum(t < n - 60 for t in counter),
            candidate_scale_unique_days=sum(t >= n - 60 for t in counter),
        ).items():
            close(item[name], expected)
        if (
            item["candidate_mean_last_date"] != dates[n - 61]
            or item["candidate_scale_first_date"] != dates[n - 60]
        ):
            raise ValueError("Candidate calibration boundary differs")
    return dict(
        passed=True,
        metric_rows=32,
        daily_rows=5760,
        correlation_rows=128,
        inventory_cases=4,
        numeric_values_checked=numeric_checks,
        max_absolute_recomputation_difference=max_error,
        protected_files_checked=len(protected),
        source_files_checked=len(manifest["sources"]),
        physical_forward_calls=0,
        new_optimizer_nfev=0,
        neural_training=False,
        limitation="Arithmetic and provenance checks do not establish forecast improvement or independent replication.",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    run_dir = ROOT / "results/ootang_bplus_v1_5" / args.run_id
    print(verify(run_dir))
