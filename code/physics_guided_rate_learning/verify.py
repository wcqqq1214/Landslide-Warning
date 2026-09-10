"""Verify sealed learning evidence without new neural, gradient or native calls."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import re

import numpy as np
import pandas as pd
import torch

from physics_guided_forecast_error.artifacts import (
    ROOT,
    array_sha,
    check_hashes,
    check_index,
    read_json,
    sha,
)
from physics_guided_pinn.run_substep_audit import load_npz
from physics_guided_state_pinn.verify import (
    NumericalChecks,
    check_frames,
    metric_checks,
)
from physics_guided_state_pinn.workflow import (
    CUTOFFS,
    components as old_components,
    load_bundle,
    make_case,
    read_labels,
    specification as state_specification,
)
from .workflow import (
    CONFIG,
    CONFIG_SHA,
    PLAN_SHA,
    arrays,
    calibrate,
    compare,
    compare_output,
    components,
    limits,
    mechanical_audit,
    specification,
    validate_output,
)


def check_optimizer(checkpoint, initial, spec, epoch):
    expected = {
        k: v for k, v in initial["state_dict"].items() if k.startswith("rate_net.")
    }
    actual = checkpoint["state_dict"]
    if list(actual) != list(expected):
        raise ValueError("G parameter inventory changed")
    for key, value in actual.items():
        if (
            value.shape != expected[key].shape
            or value.dtype != torch.float64
            or not torch.isfinite(value).all()
        ):
            raise ValueError("Malformed checkpoint weights")
        if epoch == 0 and not torch.equal(value, expected[key]):
            raise ValueError("Initialization differs from corresponding frozen e0")
    saved = checkpoint["optimizer_state"]
    if len(saved["param_groups"]) != 1:
        raise ValueError("Optimizer parameter groups differ")
    group = saved["param_groups"][0]
    for key, want in dict(
        lr=spec["learning_rate"],
        betas=tuple(spec["adam_betas"]),
        eps=spec["adam_epsilon"],
        weight_decay=0,
        amsgrad=False,
        maximize=False,
    ).items():
        if group[key] != want:
            raise ValueError(f"Optimizer setting changed: {key}")
    if (
        group["params"] != list(range(6))
        or sum(v.numel() for v in actual.values()) != 548
    ):
        raise ValueError("Optimizer is not bound to all G parameters")
    if set(saved["state"]) != (set() if epoch == 0 else set(range(6))):
        raise ValueError("Missing Adam state")
    for i, state in saved["state"].items():
        if (
            set(state) != {"step", "exp_avg", "exp_avg_sq"}
            or float(state["step"]) != epoch
        ):
            raise ValueError("Adam step or state schema changed")
        for key in ("exp_avg", "exp_avg_sq"):
            value = state[key]
            if (
                value.shape != list(actual.values())[i].shape
                or not torch.isfinite(value).all()
            ):
                raise ValueError("Malformed Adam moments")
        if (state["exp_avg_sq"] < 0).any():
            raise ValueError("Negative Adam squared moment")


def objective_from_saved(output, labels):
    data = np.mean(((output["mean"][30:] - labels[30:]) / 100) ** 2)
    prior = np.mean((np.log(output["multiplier"][1:]) / np.log(2)) ** 2)
    return dict(total=data + 0.001 * prior, data=data, rate_prior=prior)


def check_observation(check, output, bundle):
    saved, _, _, y0 = bundle
    n = len(output["mean"])
    validate_output(output)
    delta = np.vstack(
        [
            np.zeros(4),
            np.cumsum(
                saved["background_rate"][1:n] * (output["multiplier"][1:] - 1), axis=0
            ),
        ]
    )
    check.close(output["delta_background"], delta)
    check.close(output["background"], saved["background"][:n] + delta)
    check.close(output["state"][:, 20:], output["background"])
    check.close(
        output["mean"],
        (output["state"][:, :4] + output["state"][:, 20:])
        @ saved["observation_matrix"].T
        + y0,
        mean=True,
    )


def verify(out, sealed=True):
    spec = specification()
    if sealed:
        check_index(out)
    manifest = read_json(out / "manifest.json")
    source = ROOT / spec["source_run"]
    if (
        manifest["specification"] != spec
        or manifest["config_sha256"] != CONFIG_SHA
        or manifest["plan_sha256"] != PLAN_SHA
    ):
        raise ValueError("Learning design changed")
    if manifest["source_index_sha256"] != sha(
        source / "artifact_manifest.json"
    ) or manifest["prototype_index_sha256"] != sha(
        ROOT / spec["prototype_run"] / "artifact_manifest.json"
    ):
        raise ValueError("Frozen source binding changed")
    protected = read_json(out / "protected_before.json")
    check_hashes(protected)
    check_hashes(manifest["sources"])
    for name, digest in manifest["sources"].items():
        if sha(out / "sources" / name) != digest:
            raise ValueError("Source snapshot differs")
    if (
        sha(out / "sources" / CONFIG.relative_to(ROOT)) != CONFIG_SHA
        or sha(out / "sources" / spec["protocol"]) != PLAN_SHA
        or sha(out / "input_prefix_792.csv") != manifest["input_sha256"]
    ):
        raise ValueError("Protocol or input snapshot changed")
    native = read_json(out / "native/build.json")
    if native["returncode"] != 0 or not native["restored_function_matches_original"]:
        raise ValueError("Original trace instrumentation was not preserved")
    if (
        sha(out / "native" / Path(native["command"][-1]).name)
        != native["library_sha256"]
    ):
        raise ValueError("Recorded integrator binary differs from its build receipt")
    for name, digest in (
        ("physical_solver.original.c", native["original_sha256"]),
        ("physical_solver.trace.c", native["derived_sha256"]),
    ):
        if sha(out / "native" / name) != digest:
            raise ValueError("Native source snapshot changed")
    for entry in read_json(out / "native_libraries.json"):
        if sha(entry["path"]) != entry["sha256"]:
            raise ValueError("Day binary changed")
    execution = read_json(out / "execution.json")
    if (
        any(execution[k] != v for k, v in limits(spec).items())
        or execution["constant_scale_fits"] != 2
        or execution["constant_scale_components"] != 6
    ):
        raise ValueError("Scientific call budget differs from fixed completed work")
    log = pd.read_csv(out / "training.csv")
    if list(log[["prefix", "seed", "epoch"]].itertuples(index=False, name=None)) != [
        (h, s, e)
        for h in spec["prefixes"]
        for s in spec["seeds"]
        for e in range(1, 201)
    ]:
        raise ValueError("Training order or update count changed")
    if (
        not np.isfinite(log.select_dtypes(include="number").to_numpy()).all()
        or (log.gradient_norm < 0).any()
        or np.any(np.diff(log.elapsed_seconds) < 0)
    ):
        raise ValueError("Invalid training log")
    check = NumericalChecks()
    check.objective(
        log.total.to_numpy(), (log.data + 0.001 * log.rate_prior).to_numpy()
    )
    labels = read_labels(out / "input_prefix_792.csv", 792)
    previous_time, previous_lock, ordinal = None, None, 0
    max_replay_mean, max_checkpoint_mean = 0.0, 0.0
    max_comp, min_dx, min_gap = 0.0, 0.0, 0.0
    checkpoint_branches = 0
    final_objectives = []
    for h in spec["prefixes"]:
        access = read_json(out / f"label_access_{h}.json")
        access_time = datetime.fromisoformat(access["opened_utc"])
        if (
            access["prefix"] != h
            or access["preceding_prediction_lock"] != previous_lock
            or (previous_time is not None and access_time < previous_time)
        ):
            raise ValueError(
                "A later label prefix was opened before the preceding forecast lock"
            )
        if read_json(out / f"label_prefix_{h}.json")["array_sha256"] != array_sha(
            labels[:h]
        ):
            raise ValueError("Training label prefix changed")
        if h in CUTOFFS:
            cal = read_json(out / f"calibration_{h}.json")
            if (
                cal["prefix"] != h
                or cal["source_prefix"] != CUTOFFS[h]
                or cal["days"] != h - CUTOFFS[h]
                or datetime.fromisoformat(cal["locked_utc"]) < access_time
            ):
                raise ValueError("Calibration chronology differs")
            expected = calibrate(out, h, labels[:h])
            actual = load_npz(out / f"calibration_{h}_S.npz")
            for key in expected:
                check.close(actual[key], expected[key])
            for strategy in ("P0", "R", "S"):
                file = out / f"calibration_{h}_{strategy}.npz"
                if sha(file) != cal["files"][strategy] or (
                    strategy != "S" and sha(file) != sha(source / file.name)
                ):
                    raise ValueError("Frozen scale or calibration artifact changed")
        bundle = load_bundle(state_specification(), h)
        train_bundle = load_bundle(state_specification(), h, h)
        case = make_case(train_bundle, h)
        if read_json(out / f"constants_{h}.json") != case.constants:
            raise ValueError("Training constants contain a different prefix")
        lock = read_json(out / f"prefix_{h}_locked.json")
        lock_time = datetime.fromisoformat(lock["locked_utc"])
        if (
            lock["prefix"] != h
            or lock["end_index_exclusive"] != h + 180
            or lock_time < access_time
            or set(lock["predictions"]) != {"0", "1", "2"}
        ):
            raise ValueError("Prediction lock is incomplete")
        if h in CUTOFFS and datetime.fromisoformat(cal["locked_utc"]) > lock_time:
            raise ValueError(
                "Scale must be fixed before the complete current forecast lock"
            )
        for seed in spec["seeds"]:
            directory = out / f"model_{h}_{seed}"
            for name, digest in lock["predictions"][str(seed)].items():
                if sha(directory / name) != digest:
                    raise ValueError("Locked prediction changed")
            prediction = load_npz(directory / "S.npz")
            check_observation(check, prediction, bundle)
            recorded = load_npz(directory / "R_check.npz")
            mechanical = mechanical_audit(bundle, prediction, recorded)
            if mechanical != read_json(directory / "mechanical_verification.json"):
                raise ValueError("Saved original mechanical audit differs")
            max_replay_mean = max(max_replay_mean, mechanical["mean_max_difference_mm"])
            native_info = mechanical["physical"]["native"]
            min_dx = min(min_dx, native_info["min_dx"])
            min_gap = min(min_gap, native_info["min_gap"])
            max_comp = max(max_comp, native_info["max_normalized_complementarity"])
            ordinal += 1
            call = read_json(directory / "replay_call.json")
            if (
                (call["ordinal"], call["prefix"], call["seed"]) != (ordinal, h, seed)
                or call["checkpoint_sha256"] != sha(directory / "e200.pt")
                or call["background_sha256"] != array_sha(recorded["background"])
            ):
                raise ValueError("Recorded integration provenance changed")
            if (
                not access_time
                <= datetime.fromisoformat(call["started_utc"])
                <= lock_time
            ):
                raise ValueError(
                    "Recorded integration occurred outside its label/lock interval"
                )
            initial = torch.load(
                source / f"model_{h}_{seed}/e0.pt",
                weights_only=True,
                map_location="cpu",
            )
            checked = read_json(directory / "checkpoint_verification.json")
            if (
                not checked["passed"]
                or [r["epoch"] for r in checked["rows"]] != spec["checkpoints"]
            ):
                raise ValueError("Incomplete checkpoint replay evidence")
            for epoch, row in zip(spec["checkpoints"], checked["rows"]):
                checkpoint = torch.load(
                    directory / f"e{epoch}.pt", weights_only=True, map_location="cpu"
                )
                if any(
                    checkpoint[k] != v
                    for k, v in dict(
                        prefix=h, seed=seed, epoch=epoch, constants=case.constants
                    ).items()
                ) or checkpoint["initial_checkpoint_sha256"] != sha(
                    source / f"model_{h}_{seed}/e0.pt"
                ):
                    raise ValueError(
                        "Checkpoint identity or initialization provenance changed"
                    )
                check_optimizer(checkpoint, initial, spec, epoch)
                for key, name in (
                    ("checkpoint_sha256", f"e{epoch}.pt"),
                    ("reference_sha256", f"reference_e{epoch}.npz"),
                    ("replay_sha256", f"replay_e{epoch}.npz"),
                ):
                    if row[key] != sha(directory / name):
                        raise ValueError("Checkpoint replay binding changed")
                expected = load_npz(directory / f"reference_e{epoch}.npz")
                actual = load_npz(directory / f"replay_e{epoch}.npz")
                report = compare_output(actual, expected)
                for key, value in report.items():
                    if row[key] != value:
                        raise ValueError("Checkpoint replay report changed")
                max_checkpoint_mean = max(
                    max_checkpoint_mean, report["max_mean_difference_mm"]
                )
                checkpoint_branches += report["changed_active_substeps"]
                check_observation(check, actual, train_bundle)
                calculated = objective_from_saved(expected, labels[:h])
                if epoch < 200:
                    logged = log[
                        (log.prefix == h)
                        & (log.seed == seed)
                        & (log.epoch == epoch + 1)
                    ].iloc[0]
                    for key, value in calculated.items():
                        check.objective(np.asarray(logged[key]), np.asarray(value))
                else:
                    compare_output(expected, arrays(prediction, h))
                    final_objectives.append(
                        dict(
                            prefix=h,
                            seed=seed,
                            **{k: float(v) for k, v in calculated.items()},
                        )
                    )
                if epoch == 0:
                    check.close(actual["mean"], bundle[0]["mean"][:h], mean=True)
                    check.close(actual["multiplier"], np.ones((h, 4)), atol=0)
        previous_time = lock_time
        previous_lock = f"prefix_{h}_locked.json"
    locked = read_json(out / "predictions_locked.json")
    if (
        locked["primary_output"] != "S"
        or locked["epochs"] != 200
        or datetime.fromisoformat(locked["locked_utc"]) < previous_time
    ):
        raise ValueError("Complete prediction lock differs")
    for h, digest in locked["prefix_locks"].items():
        if sha(out / f"prefix_{h}_locked.json") != digest:
            raise ValueError("Prefix lock changed after final lock")
    access = read_json(out / "label_access_792.json")
    if (
        access["prefix"] != 792
        or access["preceding_prediction_lock"] != "predictions_locked.json"
        or datetime.fromisoformat(access["opened_utc"])
        < datetime.fromisoformat(locked["locked_utc"])
    ):
        raise ValueError("Evaluation labels opened before final prediction lock")
    metrics = pd.read_csv(out / "metrics.csv")
    seed_metrics = pd.read_csv(out / "seed_metrics.csv")
    if len(metrics) != 48 or len(seed_metrics) != 112:
        raise ValueError("Incomplete metric tables")
    for n in CUTOFFS:
        for strategy in ("P0", "R", "S"):
            means = (
                components(out, n)
                if strategy == "S"
                else old_components(source, state_specification(), n, strategy)
            )
            scales = load_npz(out / f"calibration_{n}_{strategy}.npz")["sigma"]
            dist = load_npz(out / f"distribution_{n}_{strategy}.npz")
            rows = metrics[(metrics.outer_days == n) & (metrics.strategy == strategy)]
            metric_checks(check, means, scales, labels[: n + 180], n, rows, dist)
            for seed in range(len(means)):
                rows = seed_metrics[
                    (seed_metrics.outer_days == n)
                    & (seed_metrics.strategy == strategy)
                    & (seed_metrics.seed == seed)
                ]
                metric_checks(
                    check,
                    means[seed : seed + 1],
                    scales[seed : seed + 1],
                    labels[: n + 180],
                    n,
                    rows,
                )
    old = pd.read_csv(source / "metrics.csv")
    old_seeds = pd.read_csv(source / "seed_metrics.csv")
    for actual, expected in ((metrics, old), (seed_metrics, old_seeds)):
        check_frames(
            check,
            actual[actual.strategy.isin(["P0", "R"])].reset_index(drop=True),
            expected[expected.strategy.isin(["P0", "R"])].reset_index(drop=True),
        )
    comparison = pd.read_csv(out / "comparisons.csv")
    check_frames(check, comparison, compare(metrics))
    return dict(
        passed=True,
        checkpoint_replays_verified=36,
        mechanical_replays_verified=9,
        training_rows=1800,
        metric_rows=48,
        seed_metric_rows=112,
        comparison_rows=8,
        protected_files=len(protected),
        numeric_values_checked=check.values,
        max_checkpoint_mean_difference_mm=max_checkpoint_mean,
        max_original_replay_mean_difference_mm=max_replay_mean,
        max_mean_difference_mm=check.max_mean_difference_mm,
        max_mixed_absolute_difference=check.max_absolute_difference,
        checkpoint_branch_changes=checkpoint_branches,
        native_min_dx=min_dx,
        native_min_gap=min_gap,
        native_max_normalized_complementarity=max_comp,
        final_objectives=final_objectives,
        strict_mean_improvements=int(comparison.strict_mean_improvement.sum()),
        new_neural_evaluations=0,
        new_native_calls=0,
        new_optimizer_updates=0,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", args.run_id):
        raise ValueError("Unsafe run id")
    print(
        json.dumps(verify(ROOT / "results/ootang_bplus_v1_12" / args.run_id), indent=2)
    )


if __name__ == "__main__":
    main()
