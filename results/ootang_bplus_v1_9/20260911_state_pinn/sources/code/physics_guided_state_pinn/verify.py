"""Read-only checkpoint, mechanical, chronology, and independent probability checks."""

import argparse
from datetime import datetime
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.special import erf, ndtri
import torch

from physics_guided.training import setup
from physics_guided_forecast_error.artifacts import (
    ROOT,
    array_sha,
    check_hashes,
    check_index,
    read_json,
    sha,
)
from physics_guided_pinn.run_substep_audit import load_npz
from .core import PHYSICS_KEYS, StatePINN, losses, tensor
from .workflow import (
    CONFIG,
    CONFIG_SHA,
    PLAN_SHA,
    CUTOFFS,
    POINTS,
    calibration,
    components,
    diagnostics,
    load_bundle,
    make_case,
    numpy_output,
    read_labels,
    replay_audit,
    scoring,
    specification,
)


def normal_cdf(z):
    return 0.5 * (1 + erf(z / np.sqrt(2)))


def independent_crps(means, scales, labels):
    def absolute_expectation(delta, sigma):
        z = delta / sigma
        return delta * erf(z / np.sqrt(2)) + sigma * np.sqrt(2 / np.pi) * np.exp(
            -z * z / 2
        )

    k = len(means)
    value = (
        sum(absolute_expectation(labels - means[i], scales[i]) for i in range(k)) / k
    )
    for i in range(k):
        for j in range(k):
            value -= absolute_expectation(
                means[i] - means[j], np.hypot(scales[i], scales[j])
            ) / (2 * k * k)
    return value


class NumericalChecks:
    def __init__(self):
        self.values = 0
        self.max_absolute_difference = 0.0
        self.max_mean_difference_mm = 0.0

    def close(self, actual, expected, atol=1e-8, mean=False):
        actual, expected = np.asarray(actual), np.asarray(expected)
        if (
            actual.shape != expected.shape
            or not np.isfinite(actual).all()
            or not np.isfinite(expected).all()
        ):
            raise ValueError("Numerical check has invalid shape or nonfinite values")
        error = abs(actual - expected)
        maximum = float(error.max(initial=0))
        if (error > atol).any():
            raise ArithmeticError(
                f"Numerical mismatch {maximum:g} exceeds its {np.max(atol):g} bound"
            )
        self.values += error.size
        self.max_absolute_difference = max(self.max_absolute_difference, maximum)
        if mean:
            self.max_mean_difference_mm = max(self.max_mean_difference_mm, maximum)

    def objective(self, actual, expected):
        # A fixed floating-point accumulation allowance, independent of outcomes.
        self.close(
            actual,
            expected,
            atol=1e-7
            + 64
            * np.finfo(np.float64).eps
            * np.maximum(1.0, abs(np.asarray(expected))),
        )


def metric_checks(check, means, scales, labels, n, rows, distribution=None):
    mean = means.mean(axis=0)
    scores = independent_crps(means, scales[:, None], labels)
    if (scores < -1e-8).any():
        raise ArithmeticError("Negative independent CRPS")
    if distribution is not None:
        check.close(distribution["mean"], mean[30:], mean=True)
        std = np.sqrt(np.mean(scales[:, None] ** 2 + (means - mean) ** 2, axis=0))
        check.close(distribution["std"], std[30:])
        for level in (80, 90, 95):
            alpha = 1 - level / 100
            lo, hi = distribution[f"lower_{level}"], distribution[f"upper_{level}"]
            if (lo > hi).any():
                raise ValueError("Reversed probability interval")
            tolerance = 1e-6 / (np.sqrt(2 * np.pi) * scales.min(axis=0)) + 1e-10
            check.close(
                normal_cdf((lo[None] - means[:, 30:]) / scales[:, None]).mean(axis=0),
                np.full_like(lo, alpha / 2),
                atol=tolerance,
            )
            check.close(
                normal_cdf((hi[None] - means[:, 30:]) / scales[:, None]).mean(axis=0),
                np.full_like(hi, 1 - alpha / 2),
                atol=tolerance,
            )
        if (
            (distribution["lower_95"] > distribution["lower_90"]).any()
            or (distribution["lower_90"] > distribution["lower_80"]).any()
            or (distribution["upper_95"] < distribution["upper_90"]).any()
            or (distribution["upper_90"] < distribution["upper_80"]).any()
        ):
            raise ValueError("Non-nested intervals")
    for part, a, b in (("train", 30, n), ("prediction", n, n + 180)):
        for j, station in enumerate(POINTS):
            row = rows[(rows.part == part) & (rows.station == station)]
            if len(row) != 1:
                raise ValueError("Missing or duplicate metric row")
            row = row.iloc[0]
            error = mean[a:b, j] - labels[a:b, j]
            check.close(
                np.array([row.rmse_mm, row.mae_mm, row.crps_mm]),
                np.array(
                    [
                        np.sqrt(np.mean(error**2)),
                        np.mean(abs(error)),
                        np.mean(scores[a:b, j]),
                    ]
                ),
            )
            if row.days != b - a:
                raise ValueError("Incorrect metric denominator")
            for level in (80, 90, 95):
                alpha = 1 - level / 100
                if distribution is None:
                    if len(means) != 1:
                        raise ValueError("Analytic seed check expects one Gaussian")
                    lo = mean[a:b, j] + scales[0, j] * ndtri(alpha / 2)
                    hi = mean[a:b, j] + scales[0, j] * ndtri(1 - alpha / 2)
                    width_tolerance = 1.01e-6
                    interval_tolerance = 1e-6 * (1 + 2 / alpha) + 1e-8
                    ambiguity = np.mean(
                        np.minimum(abs(labels[a:b, j] - lo), abs(labels[a:b, j] - hi))
                        <= 1e-6
                    )
                else:
                    lo = distribution[f"lower_{level}"][a - 30 : b - 30, j]
                    hi = distribution[f"upper_{level}"][a - 30 : b - 30, j]
                    width_tolerance, interval_tolerance, ambiguity = 1e-8, 1e-8, 0.0
                y = labels[a:b, j]
                interval_score = (
                    hi
                    - lo
                    + (2 / alpha) * (np.maximum(lo - y, 0) + np.maximum(y - hi, 0))
                )
                check.close(
                    np.array(row[f"coverage_{level}"]),
                    np.array(np.mean((y >= lo) & (y <= hi))),
                    atol=ambiguity + 1e-12,
                )
                check.close(
                    np.array(row[f"width_{level}_mm"]),
                    np.array(np.mean(hi - lo)),
                    atol=width_tolerance,
                )
                check.close(
                    np.array(row[f"interval_score_{level}_mm"]),
                    np.array(np.mean(interval_score)),
                    atol=interval_tolerance,
                )


def check_frames(check, actual, expected):
    if actual.shape != expected.shape or list(actual.columns) != list(expected.columns):
        raise ValueError("Table schema or row count differs")
    for name in expected.columns:
        if expected[name].dtype.kind in "iuf":
            check.close(actual[name].to_numpy(float), expected[name].to_numpy(float))
        elif actual[name].tolist() != expected[name].tolist():
            raise ValueError(f"Table identifiers differ: {name}")


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
        raise ValueError("Manifest specification changed")
    protected = read_json(out / "protected_before.json")
    check_hashes(protected)
    check_hashes(manifest["sources"])
    for name, digest in manifest["sources"].items():
        if sha(out / "sources" / name) != digest:
            raise ValueError("Scientific source snapshot differs")
    if (
        sha(out / "sources" / CONFIG.relative_to(ROOT)) != CONFIG_SHA
        or sha(out / "sources" / spec["protocol"]) != PLAN_SHA
    ):
        raise ValueError("Frozen protocol copy differs")
    if (
        sha(ROOT / manifest["native_library"]) != manifest["native_library_sha256"]
        or sha(out / "input_prefix_792.csv") != manifest["input_sha256"]
    ):
        raise ValueError("Native library or input copy differs")
    locked = read_json(out / "predictions_locked.json")
    if (
        locked["primary_output"] != "R"
        or locked["diagnostic_output"] != "P"
        or locked["epochs"] != 200
    ):
        raise ValueError("Prediction roles or final epoch changed")
    execution = read_json(out / "execution.json")
    for key, expected in dict(
        update_attempts=1800,
        optimizer_step_calls=1800,
        completed_updates=1800,
        training_evaluations=1800,
        zero_check_evaluations=9,
        final_prediction_evaluations=9,
        recorded_integrations=9,
        recorded_substeps=369216,
        constant_scale_fits=6,
        parameter_fits=0,
        scale_neural_updates=0,
    ).items():
        if execution[key] != expected:
            raise ValueError(f"Execution count differs: {key}")
    labels = read_labels(out / "input_prefix_792.csv", 792)
    check = NumericalChecks()
    training = pd.read_csv(out / "training.csv")
    if (
        len(training) != 1800
        or not np.isfinite(training.select_dtypes(include="number").to_numpy()).all()
    ):
        raise ValueError("Incomplete or nonfinite training log")
    expected_order = [
        (h, seed, epoch)
        for h in spec["prefixes"]
        for seed in spec["seeds"]
        for epoch in range(1, 201)
    ]
    if (
        list(training[["prefix", "seed", "epoch"]].itertuples(index=False, name=None))
        != expected_order
    ):
        raise ValueError("Training order or epochs changed")
    check.objective(
        training.physics.to_numpy(),
        training[list(PHYSICS_KEYS)].mean(axis=1).to_numpy(),
    )
    check.close(
        training.physics_weight.to_numpy(),
        np.minimum(1.0, training.epoch.to_numpy() / 20),
    )
    check.objective(
        training.total.to_numpy(),
        (
            training.data
            + training.physics_weight * training.physics
            + 0.001 * training.rate_prior
        ).to_numpy(),
    )
    previous_time, previous_lock, neural_evaluations, ordinal = None, None, 0, 0
    for h in spec["prefixes"]:
        access = read_json(out / f"label_access_{h}.json")
        access_time = datetime.fromisoformat(access["opened_utc"])
        if (
            access["prefix"] != h
            or (previous_time is not None and access_time < previous_time)
            or access["preceding_prediction_lock"] != previous_lock
        ):
            raise ValueError("Later labels were accessed before prior prediction lock")
        if read_json(out / f"label_prefix_{h}.json")["array_sha256"] != array_sha(
            labels[:h]
        ):
            raise ValueError("Training label prefix differs")
        if h in CUTOFFS:
            cal = read_json(out / f"calibration_{h}.json")
            if (
                datetime.fromisoformat(cal["locked_utc"]) < access_time
                or cal["source_prefix"] != CUTOFFS[h]
                or cal["days"] != h - CUTOFFS[h]
            ):
                raise ValueError("Calibration role or chronology differs")
            for strategy, values in calibration(out, spec, h, labels[:h]).items():
                actual = load_npz(out / f"calibration_{h}_{strategy}.npz")
                if cal["files"][strategy] != sha(
                    out / f"calibration_{h}_{strategy}.npz"
                ):
                    raise ValueError("Calibration lock hash differs")
                for name, expected in values.items():
                    check.close(actual[name], expected)
        train_bundle, full_bundle = load_bundle(spec, h, h), load_bundle(spec, h)
        constants = read_json(out / f"constants_{h}.json")
        train_case, full_case = (
            make_case(train_bundle, h, constants),
            make_case(full_bundle, h, constants),
        )
        prefix_lock = read_json(out / f"prefix_{h}_locked.json")
        if locked["prefix_locks"][str(h)] != sha(out / f"prefix_{h}_locked.json"):
            raise ValueError("Prefix lock hash differs")
        previous_time = datetime.fromisoformat(prefix_lock["locked_utc"])
        previous_lock = f"prefix_{h}_locked.json"
        if previous_time < access_time or prefix_lock["end_index_exclusive"] != h + 180:
            raise ValueError("Prefix forecast horizon or chronology differs")
        for seed in spec["seeds"]:
            directory = out / f"model_{h}_{seed}"
            if sorted(p.name for p in directory.glob("e*.pt")) != sorted(
                f"e{e}.pt" for e in spec["checkpoints"]
            ):
                raise ValueError("Checkpoint inventory differs")
            for name, digest in prefix_lock["predictions"][str(seed)].items():
                if sha(directory / name) != digest:
                    raise ValueError("Frozen checkpoint or prediction changed")
            setup(seed)
            model = StatePINN()
            for epoch in spec["checkpoints"]:
                checkpoint = torch.load(
                    directory / f"e{epoch}.pt", map_location="cpu", weights_only=True
                )
                if (checkpoint["prefix"], checkpoint["seed"], checkpoint["epoch"]) != (
                    h,
                    seed,
                    epoch,
                ) or checkpoint["constants"] != constants:
                    raise ValueError("Checkpoint training metadata differs")
                if epoch == 0:
                    for name, value in model.state_dict().items():
                        if not torch.equal(value, checkpoint["state_dict"][name]):
                            raise ValueError(
                                "Initial checkpoint differs from the registered seed"
                            )
                model.load_state_dict(checkpoint["state_dict"], strict=True)
                if epoch < 200:
                    with torch.no_grad():
                        total, terms = losses(
                            train_case, model(train_case), tensor(labels[:h]), epoch + 1
                        )
                    neural_evaluations += 1
                    row = training[
                        (training.prefix == h)
                        & (training.seed == seed)
                        & (training.epoch == epoch + 1)
                    ].iloc[0]
                    # CSV round-trips retain these objectives; tolerances are scalar objective units.
                    check.objective(np.array(float(total)), np.array(row.total))
                    for name, value in terms.items():
                        check.objective(np.array(float(value)), np.array(row[name]))
            with torch.no_grad():
                recovered = numpy_output(model(full_case, chunk=1024))
            neural_evaluations += 1
            prediction = load_npz(directory / "P.npz")
            for name, expected in recovered.items():
                check.close(prediction[name], expected, mean=name == "mean")
            ordinal += 1
            called = read_json(directory / "replay_call.json")
            if (called["ordinal"], called["prefix"], called["seed"]) != (
                ordinal,
                h,
                seed,
            ) or called["checkpoint_sha256"] != sha(directory / "e200.pt"):
                raise ValueError("Replay invocation differs")
            recorded = load_npz(directory / "R.npz")
            expected, audit = replay_audit(full_bundle, prediction, recorded)
            if called["background_sha256"] != array_sha(expected["background"]):
                raise ValueError("Replay forcing provenance differs")
            check.close(recorded["background"], expected["background"])
            check.close(recorded["mean"], expected["mean"], mean=True)
            if not audit["passed"] or audit != read_json(
                directory / "replay_audit.json"
            ):
                raise ValueError("R mechanical audit differs or fails")
            if diagnostics(full_case, prediction, expected["mean"]) != read_json(
                directory / "diagnostics.json"
            ):
                raise ValueError("P/R constraint or discrepancy diagnostic differs")
    final_time = datetime.fromisoformat(locked["locked_utc"])
    access = read_json(out / "label_access_792.json")
    if (
        final_time < previous_time
        or datetime.fromisoformat(access["opened_utc"]) < final_time
    ):
        raise ValueError("Final labels were accessed before predictions locked")
    metrics, seeds, comparisons, distributions = scoring(out, spec, labels)
    for name, expected in (
        ("metrics", metrics),
        ("seed_metrics", seeds),
        ("comparisons", comparisons),
    ):
        check_frames(check, pd.read_csv(out / f"{name}.csv"), expected)
    for (n, strategy), distribution in distributions.items():
        stored = load_npz(out / f"distribution_{n}_{strategy}.npz")
        for key in distribution:
            check.close(stored[key], distribution[key], mean=key == "mean")
        means = components(out, spec, n, strategy)
        scales = load_npz(out / f"calibration_{n}_{strategy}.npz")["sigma"]
        metric_checks(
            check,
            means,
            scales,
            labels[: n + 180],
            n,
            metrics[(metrics.outer_days == n) & (metrics.strategy == strategy)],
            stored,
        )
        for seed in range(len(means)):
            selected = seeds[
                (seeds.outer_days == n)
                & (seeds.strategy == strategy)
                & (seeds.seed == seed)
            ]
            metric_checks(
                check,
                means[seed : seed + 1],
                scales[seed : seed + 1],
                labels[: n + 180],
                n,
                selected,
            )
    return dict(
        passed=True,
        checkpoints_checked=36,
        neural_evaluations_during_verification=neural_evaluations,
        native_integrations_during_verification=0,
        substeps_checked=369216,
        protected_files=len(protected),
        scientific_sources=len(manifest["sources"]),
        numerical_values=check.values,
        max_absolute_difference_mixed_units=check.max_absolute_difference,
        max_mean_difference_mm=check.max_mean_difference_mm,
        metrics_rows=len(metrics),
        seed_metric_rows=len(seeds),
        comparison_rows=len(comparisons),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if not args.run_id or Path(args.run_id).name != args.run_id:
        raise ValueError("Unsafe run id")
    setup(0)
    print(
        json.dumps(verify(ROOT / "results/ootang_bplus_v1_9" / args.run_id), indent=2)
    )


if __name__ == "__main__":
    main()
