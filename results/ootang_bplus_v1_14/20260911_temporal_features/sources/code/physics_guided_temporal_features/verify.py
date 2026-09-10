"""Verify sealed auxiliary regressions by substitution, without solving again."""

import argparse
from datetime import datetime
from itertools import islice, product
import json
import re

import numpy as np
import pandas as pd

from physics_guided_forecast_error.artifacts import (
    ROOT,
    array_sha,
    check_hashes,
    check_index,
    read_json,
    sha,
)
from .core import FLOORS, POINTS
from .workflow import (
    CONFIG_SHA,
    NAMES,
    PLAN_SHA,
    load_case,
    load_npz,
    no_model_runtime,
    read_labels,
    source_guard,
    specification,
)


class Checks:
    def __init__(self, atol):
        self.atol, self.values, self.maximum = atol, 0, 0.0
        self.mm_maximum = 0.0

    def close(self, actual, expected, mm=False):
        a, b = np.asarray(actual), np.asarray(expected)
        if a.shape != b.shape or not np.isfinite(a).all() or not np.isfinite(b).all():
            raise ValueError("Mismatched or nonfinite verification values")
        difference = float(np.max(abs(a - b))) if a.size else 0.0
        self.values += a.size
        self.maximum = max(self.maximum, difference)
        if mm:
            self.mm_maximum = max(self.mm_maximum, difference)
        if difference > self.atol:
            raise ValueError(
                f"Verification difference {difference} exceeds {self.atol}"
            )


def regression_substitution(x, demand, coefficient, ridge_lambda, scale, atol):
    """The unpenalized intercept and positive ridge make stationarity sufficient."""
    x, demand, coefficient = map(np.asarray, (x, demand, coefficient))
    if (
        x.ndim != 2
        or not len(x)
        or demand.shape != (len(x), 4)
        or coefficient.shape != (x.shape[1] + 1, 4)
    ):
        raise ValueError("Regression verification shapes differ")
    if any(not np.isfinite(v).all() for v in (x, demand, coefficient)):
        raise ValueError("Nonfinite regression evidence")
    phi = np.column_stack([np.ones(len(x)), x])
    fitted = np.sum(phi[:, :, None] * coefficient[None, :, :], axis=1)
    residual = fitted - demand / scale
    gradient = np.mean(phi[:, :, None] * residual[:, None, :], axis=0)
    gradient[1:] += ridge_lambda * coefficient[1:]
    maximum = float(np.max(abs(gradient)))
    if not np.isfinite(maximum) or maximum > atol:
        raise ValueError(f"Normal-equation residual {maximum} exceeds {atol}")
    data = float(np.mean(np.sum(residual**2, axis=1)))
    penalty = float(ridge_lambda * np.sum(coefficient[1:] ** 2))
    return dict(
        normal_equation_max_abs=maximum,
        data=data,
        penalty=penalty,
        total=data + penalty,
    )


def verify_tables(out, spec, labels, cases, predictions, check):
    metrics = pd.read_csv(out / "metrics.csv")
    keys = ["origin", "strategy", "part", "station"]
    expected = set(
        product(spec["origins"], spec["strategies"], ("train", "forward"), POINTS)
    )
    if (
        len(metrics) != spec["metric_rows"]
        or set(map(tuple, metrics[keys].values)) != expected
    ):
        raise ValueError("Incomplete or duplicate point metric rows")
    for row in metrics.itertuples(index=False):
        case = cases[row.origin]
        ix = (
            slice(30, row.origin)
            if row.part == "train"
            else slice(row.origin, len(case["base_mean"]))
        )
        j = POINTS.index(row.station)
        truth, base = labels[ix, j], case["base_mean"][ix, j]
        estimate = predictions[row.origin][row.strategy][ix, j]
        error, demand, correction = estimate - truth, truth - base, estimate - base
        active = (abs(demand) > spec["direction_tolerance_mm"]) & (
            abs(correction) > spec["direction_tolerance_mm"]
        )
        opposed = active & (np.signbit(demand) != np.signbit(correction))
        check.close(
            [row.days, row.active_days, row.opposed_days],
            [len(truth), active.sum(), opposed.sum()],
        )
        check.close(
            [
                row.rmse_mm,
                row.mae_mm,
                row.bias_mm,
                row.demand_mean_mm,
                row.correction_mean_mm,
            ],
            [
                np.sqrt(np.sum(error**2) / len(error)),
                np.sum(abs(error)) / len(error),
                error.mean(),
                demand.mean(),
                correction.mean(),
            ],
            mm=True,
        )
        if active.any():
            check.close(row.opposed_fraction, opposed.sum() / active.sum())
        elif not np.isnan(row.opposed_fraction):
            raise ValueError("Inactive direction fraction must be unavailable")
    comparisons = pd.read_csv(out / "comparisons.csv")
    expected = {
        (h, a, b, p)
        for h in spec["origins"]
        for a, b in (("HH", "H"), ("HHS", "H"), ("HHS", "C"))
        for p in POINTS
    }
    keys = ["origin", "strategy", "reference", "station"]
    if (
        len(comparisons) != spec["comparison_rows"]
        or set(map(tuple, comparisons[keys].values)) != expected
    ):
        raise ValueError("Incomplete or duplicate comparison rows")
    forward = metrics[metrics.part == "forward"].set_index(
        ["origin", "strategy", "station"]
    )
    for row in comparisons.itertuples(index=False):
        a, b = (
            forward.loc[(row.origin, row.strategy, row.station)],
            forward.loc[(row.origin, row.reference, row.station)],
        )
        rmse, mae = a.rmse_mm - b.rmse_mm, a.mae_mm - b.mae_mm
        check.close(
            [row.rmse_difference_mm, row.mae_difference_mm], [rmse, mae], mm=True
        )
        if not isinstance(
            row.both_improve, (bool, np.bool_)
        ) or row.both_improve != bool(rmse < -1e-6 and mae < -1e-6):
            raise ValueError("Comparison improvement flag differs")


def verify(out, sealed=True):
    spec = specification()
    no_model_runtime()
    if sealed:
        check_index(out)
        completed = read_json(out / "completed.json")
        if (
            not completed["execution_complete"]
            or not completed["numerical_verification"]
            or read_json(out / "launcher.json")["exitcode"] != 0
        ):
            raise ValueError("Original run did not complete successfully")
    manifest = read_json(out / "manifest.json")
    if (
        manifest["specification"] != spec
        or manifest["config_sha256"] != CONFIG_SHA
        or manifest["plan_sha256"] != PLAN_SHA
        or manifest["feature_names"] != NAMES
    ):
        raise ValueError("Fixed specification or feature order changed")
    protected = source_guard(spec)
    if protected != read_json(out / "protected_before.json"):
        raise ValueError("Protected source inventory changed")
    check_hashes(manifest["sources"])
    check_hashes(manifest["sources"], out / "sources")
    input_path = out / "input_prefix_612.csv"
    with (ROOT / spec["input_csv"]).open("rb") as source:
        raw_prefix = b"".join(islice(source, 613))
    if (
        input_path.read_bytes() != raw_prefix
        or sha(input_path) != manifest["input_sha256"]
        or len(raw_prefix.splitlines()) != 613
    ):
        raise ValueError("Input is not exactly the original 612-day raw prefix")
    labels = read_labels(input_path, 612)
    check, cases, predictions, fits = Checks(spec["numeric_atol"]), {}, {}, []
    preceding, previous_time = None, datetime.fromisoformat(manifest["started_utc"])
    for step, origin in enumerate(spec["origins"], 1):
        access = read_json(out / f"label_access_{origin}.json")
        lock = read_json(out / f"prefix_{origin}_locked.json")
        opened, locked = map(
            datetime.fromisoformat, (access["opened_utc"], lock["locked_utc"])
        )
        if (
            access["prefix"] != origin
            or access["preceding_lock"] != preceding
            or lock["origin"] != origin
            or not previous_time <= opened <= locked
        ):
            raise ValueError("Training-label access/output-lock order differs")
        names = {
            f"case_{origin}.npz",
            f"teacher_{origin}.json",
            f"scaler_{origin}.npz",
            f"constant_{origin}.npz",
            *[f"model_{origin}_{name}.npz" for name in spec["representations"]],
        }
        if set(lock["outputs"]) != names or lock["counts"] != dict(
            ridge_fits=step * 3, constant_estimates=step, feature_scalers=step
        ):
            raise ValueError("Locked outputs or cumulative budget differs")
        check_hashes(lock["outputs"], out)
        original, record = load_case(spec, origin)
        if (
            read_json(out / f"teacher_{origin}.json") != record
            or lock["training_label_sha256"] != array_sha(labels[:origin])
            or record["training_label_sha256"] != array_sha(labels[:origin])
        ):
            raise ValueError("Original teacher/label binding changed")
        case = load_npz(out / f"case_{origin}.npz")
        if set(case) != set(original):
            raise ValueError("Saved input schema differs")
        for key, value in original.items():
            if key == "dates":
                if not np.array_equal(case[key], value):
                    raise ValueError("Saved date axis differs")
            else:
                check.close(case[key], value)
        # Direct rolling slices are independent of the execution's cumulative sum.
        history = np.stack(
            [
                case["hydro"][max(0, t - 29) : t + 1].mean(axis=0)
                for t in range(len(case["hydro"]))
            ]
        )
        check.close(case["raw_features"][:, 12:24], history)
        projected = (
            np.sum(
                (case["mechanics"][:, :4] + case["mechanics"][:, 20:24])[:, None, :]
                * case["observation"],
                axis=2,
            )
            + case["y0"]
        )
        check.close(case["base_mean"], projected, mm=True)
        scaler = load_npz(out / f"scaler_{origin}.npz")
        if set(scaler) != {"mean", "std", "floors"}:
            raise ValueError("Scaler schema differs")
        check.close(scaler["floors"], FLOORS)
        check.close(scaler["mean"], case["raw_features"][:origin].mean(axis=0))
        check.close(
            scaler["std"], np.maximum(case["raw_features"][:origin].std(axis=0), FLOORS)
        )
        if origin in (342, 432):
            old = read_json(
                (ROOT / spec["input_csv"]).parent / f"constants_{origin}.json"
            )
            check.close(scaler["mean"][:12], old["feature_mean"])
            check.close(scaler["std"][:12], old["feature_std"])
        x = (case["raw_features"] - scaler["mean"]) / scaler["std"]
        demand = labels[30:origin] - case["base_mean"][30:origin]
        constant = load_npz(out / f"constant_{origin}.npz")
        if set(constant) != {"offset", "prediction"}:
            raise ValueError("Constant reference schema differs")
        check.close(constant["offset"], demand.mean(axis=0), mm=True)
        check.close(
            constant["prediction"], case["base_mean"] + constant["offset"], mm=True
        )
        output = dict(P0=case["base_mean"], C=constant["prediction"])
        for name, dimensions in spec["representations"].items():
            model = load_npz(out / f"model_{origin}_{name}.npz")
            if set(model) != {
                "coefficient",
                "correction",
                "prediction",
                "data_objective",
                "penalty",
                "total_objective",
            }:
                raise ValueError("Auxiliary model schema differs")
            result = regression_substitution(
                x[30:origin, :dimensions],
                demand,
                model["coefficient"],
                spec["ridge_lambda"],
                spec["target_scale_mm"],
                spec["normal_equation_atol"],
            )
            phi = np.column_stack([np.ones(len(x)), x[:, :dimensions]])
            applied = spec["target_scale_mm"] * np.sum(
                phi[:, :, None] * model["coefficient"][None, :, :], axis=1
            )
            check.close(model["correction"], applied, mm=True)
            check.close(model["prediction"], case["base_mean"] + applied, mm=True)
            check.close(
                [model["data_objective"], model["penalty"], model["total_objective"]],
                [result["data"], result["penalty"], result["total"]],
            )
            fits.append(dict(origin=origin, representation=name, **result))
            output[name] = model["prediction"]
        cases[origin], predictions[origin] = case, output
        preceding, previous_time = f"prefix_{origin}_locked.json", locked
    scoring = read_json(out / "label_access_612.json")
    if (
        scoring["prefix"] != 612
        or scoring["preceding_lock"] != preceding
        or datetime.fromisoformat(scoring["opened_utc"]) < previous_time
    ):
        raise ValueError("Scoring labels were parsed before the final lock")
    verify_tables(out, spec, labels, cases, predictions, check)
    expected_counts = dict(ridge_fits=9, constant_estimates=3, feature_scalers=3)
    if read_json(out / "execution.json") != {
        **expected_counts,
        **{k: v for k, v in spec.items() if k.startswith("new_")},
    }:
        raise ValueError("Registered execution budget differs")
    if sealed and (
        any(completed[k] != v for k, v in expected_counts.items())
        or not 0 < completed["elapsed_seconds"] <= spec["timeout_seconds"]
        or datetime.fromisoformat(completed["completed_utc"])
        < datetime.fromisoformat(scoring["opened_utc"])
    ):
        raise ValueError("Completion timing or counts differ")
    check_hashes(protected)
    no_model_runtime()
    return dict(
        passed=True,
        protected_files=len(protected),
        numeric_values_checked=int(check.values),
        max_difference_mm=check.mm_maximum,
        max_mixed_absolute_difference=check.maximum,
        max_normal_equation_residual=max(f["normal_equation_max_abs"] for f in fits),
        fits_verified=fits,
        metric_rows_verified=120,
        comparison_rows_verified=36,
        chronological_prefixes_verified=3,
        original_scalers_verified=2,
        new_regression_fits=0,
        new_neural_calls=0,
        new_physical_calls=0,
        new_scale_fits=0,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", args.run_id):
        raise ValueError("Unsafe run id")
    print(
        json.dumps(verify(ROOT / "results/ootang_bplus_v1_14" / args.run_id), indent=2)
    )


if __name__ == "__main__":
    main()
