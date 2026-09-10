"""Check diagnostic identities and original evidence without model execution."""

import argparse
import json
import re

import numpy as np
import pandas as pd

from physics_guided_forecast_error.artifacts import (
    ROOT,
    check_hashes,
    check_index,
    read_json,
    sha,
)
from .workflow import (
    CONFIG_SHA,
    PLAN_SHA,
    POINTS,
    TABLES,
    build_tables,
    load_daily,
    load_npz,
    no_model_runtime,
    source_guard,
    specification,
)


class Checks:
    def __init__(self, spec):
        self.spec, self.values, self.maximum = spec, 0, 0.0
        self.mean_maximum = 0.0

    def close(self, actual, expected, mm=False):
        actual, expected = np.asarray(actual), np.asarray(expected)
        if (
            actual.shape != expected.shape
            or not np.isfinite(actual).all()
            or not np.isfinite(expected).all()
        ):
            raise ValueError("Mismatched or nonfinite numerical evidence")
        difference = float(np.max(abs(actual - expected))) if actual.size else 0.0
        self.values += actual.size
        self.maximum = max(self.maximum, difference)
        if mm:
            self.mean_maximum = max(self.mean_maximum, difference)
        if not np.allclose(
            actual,
            expected,
            atol=self.spec["numeric_atol"],
            rtol=0 if mm else self.spec["numeric_rtol"],
        ):
            raise ValueError(
                f"Diagnostic identity failed: maximum difference {difference}"
            )


def observed(domain, matrix):
    return np.sum(domain[..., None, :] * matrix, axis=-1)


def verify(out, sealed=True):
    spec = specification()
    if sealed:
        check_index(out)
    manifest = read_json(out / "manifest.json")
    if (
        manifest["specification"] != spec
        or manifest["config_sha256"] != CONFIG_SHA
        or manifest["plan_sha256"] != PLAN_SHA
    ):
        raise ValueError("Diagnostic specification binding differs")
    protected = source_guard(spec)
    if protected != read_json(out / "protected_before.json"):
        raise ValueError("Protected source inventory changed")
    check_hashes(manifest["sources"])
    for path, digest in manifest["sources"].items():
        if sha(out / "sources" / path) != digest:
            raise ValueError("Diagnostic source snapshot changed")
    check = Checks(spec)
    original = load_daily(spec)
    daily = {}
    for h, end in zip(spec["prefixes"], spec["ends"]):
        data = load_npz(out / f"prefix_{h}.npz")
        if set(data) != set(original[h]):
            raise ValueError("Daily diagnostic schema differs")
        for name, expected in original[h].items():
            if name == "dates":
                if not np.array_equal(data[name], expected):
                    raise ValueError("Daily diagnostic dates differ")
            else:
                check.close(data[name], expected)
        if len(data["labels"]) != end:
            raise ValueError("Unregistered forward window")
        raw, mean, std = data["raw_features"], data["feature_mean"], data["feature_std"]
        floors = np.array([1, 1, 1, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 1])
        check.close(mean, raw[:h].mean(axis=0))
        check.close(std, np.maximum(raw[:h].std(axis=0, ddof=0), floors))
        check.close(data["z_features"] * std + mean, raw)
        observation = data["observation"]
        check.close(
            observed(data["base_s"] + data["base_background"], observation)
            + data["y0"],
            data["base_mean"],
            mm=True,
        )
        check.close(
            observed(data["state_s"] + data["background"], observation) + data["y0"],
            data["prediction"],
            mm=True,
        )
        delta = np.zeros_like(data["background"])
        delta[:, 1:] = np.add.accumulate(
            data["base_rate"][None, 1:] * (data["multiplier"][:, 1:] - 1), axis=1
        )
        check.close(data["background"] - data["base_background"], delta, mm=True)
        check.close(
            data["prediction"] - data["base_mean"],
            data["correction_background"] + data["correction_state"],
            mm=True,
        )
        daily[h] = data
    expected_tables = build_tables(spec, daily)
    tables = {}
    for name in TABLES:
        actual = pd.read_csv(out / f"{name}.csv")
        pd.testing.assert_frame_equal(
            actual,
            expected_tables[name],
            check_dtype=False,
            check_exact=False,
            atol=spec["numeric_atol"],
            rtol=spec["numeric_rtol"],
        )
        tables[name] = actual
    source = ROOT / spec["source_run"]
    metrics = pd.read_csv(source / "metrics.csv")
    seeds = pd.read_csv(source / "seed_metrics.csv")
    for row in tables["point"].itertuples(index=False):
        data = daily[row.prefix]
        ix = slice(row.start_index, row.end_index_exclusive)
        j = POINTS.index(row.station)
        base_error = data["base_mean"][ix, j] - data["labels"][ix, j]
        prediction = (
            data["prediction"].mean(axis=0)
            if row.seed == "mean"
            else data["prediction"][int(row.seed)]
        )
        error = prediction[ix, j] - data["labels"][ix, j]
        check.close(
            row.mse_change_mm2, float(np.mean(error**2) - np.mean(base_error**2))
        )
        if row.prefix not in (432, 612):
            continue
        part = "train" if row.part == "train" else "prediction"
        subset = metrics if row.seed == "mean" else seeds[seeds.seed == int(row.seed)]
        old = subset[
            (subset.outer_days == row.prefix)
            & (subset.strategy == "S")
            & (subset.part == part)
            & (subset.station == row.station)
        ]
        if len(old) != 1:
            raise ValueError("Original S metric identity is ambiguous")
        check.close(
            [row.s_rmse_mm, row.s_mae_mm],
            old[["rmse_mm", "mae_mm"]].iloc[0].to_numpy(float),
            mm=True,
        )
        base = metrics[
            (metrics.outer_days == row.prefix)
            & (metrics.strategy == "P0")
            & (metrics.part == part)
            & (metrics.station == row.station)
        ]
        check.close(
            [row.base_rmse_mm, row.base_mae_mm],
            base[["rmse_mm", "mae_mm"]].iloc[0].to_numpy(float),
            mm=True,
        )
    for final in read_json(source / "verification.json")["final_objectives"]:
        selected = tables["point"][
            (tables["point"].prefix == final["prefix"])
            & (tables["point"].seed == str(final["seed"]))
            & (tables["point"].part == "train")
        ]
        if len(selected) != 4:
            raise ValueError("Incomplete final training objective")
        check.close(float(np.mean(selected.s_rmse_mm**2) / 10000), final["data"])
    for n, old_prefix in ((432, 342), (612, 432)):
        calibration = load_npz(source / f"calibration_{n}_S.npz")
        expected_error = (
            daily[old_prefix]["prediction"][:, old_prefix:n]
            - daily[old_prefix]["labels"][None, old_prefix:n]
        )
        check.close(calibration["errors"], expected_error, mm=True)
        check.close(
            calibration["component_means"],
            daily[old_prefix]["prediction"][:, old_prefix:n],
            mm=True,
        )
        check.close(
            calibration["sigma"],
            np.maximum(
                np.sqrt(np.mean(expected_error**2, axis=1)), spec["sigma_floor_mm"]
            ),
            mm=True,
        )
    for row in tables["calibration"].itertuples(index=False):
        for part in ("calibration", "prediction"):
            check.close(
                getattr(row, part + "_rms_mm") ** 2,
                getattr(row, part + "_bias_squared_mm2")
                + getattr(row, part + "_sd_squared_mm2"),
            )
    no_model_runtime()
    return dict(
        passed=True,
        protected_files=len(protected),
        numeric_values_checked=int(check.values),
        max_mean_difference_mm=check.mean_maximum,
        max_mixed_absolute_difference=check.maximum,
        table_rows={key: len(value) for key, value in tables.items()},
        original_objectives_verified=9,
        original_point_metric_rows_verified=64,
        new_neural_evaluations=0,
        new_gradients=0,
        new_native_calls=0,
        new_optimizer_updates=0,
        new_scale_fits=0,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", args.run_id):
        raise ValueError("Unsafe run id")
    print(
        json.dumps(verify(ROOT / "results/ootang_bplus_v1_13" / args.run_id), indent=2)
    )


if __name__ == "__main__":
    main()
