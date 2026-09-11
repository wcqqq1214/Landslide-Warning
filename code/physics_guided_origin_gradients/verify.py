"""Independent sample reconstruction, backward accumulation, and archive checks."""

import numpy as np
import pandas as pd
import torch

from physics_guided_forecast_error.artifacts import (
    ROOT,
    check_hashes,
    check_index,
    read_json,
)
from .core import Budget, tables, validate_samples


def replay_gradients(model, samples, batch, budget):
    validate_samples(samples, batch)
    values, gradients = [], []
    for block in (0, 1):
        model.zero_grad(set_to_none=True)
        positions = np.flatnonzero(samples.blocks == block)
        total = 0.0
        for start in range(0, len(positions), batch):
            ids = positions[start : start + batch]
            budget.forward("gradient")
            error = model(samples.x[ids]) - (samples.target[ids] - samples.base[ids])
            loss = torch.dot(error.square().sum(dim=1), samples.weights[ids]) / 20000
            budget.backward()
            loss.backward()
            total += float(loss.detach())
        values.append(total)
        gradients.append(
            torch.cat([p.grad.flatten() for p in model.parameters()]).numpy().copy()
        )
    model.zero_grad(set_to_none=True)
    return np.array(values), np.array(gradients)


def replay_values(model, samples, batch, budget):
    values = []
    with torch.no_grad():
        for block in (0, 1):
            positions = np.flatnonzero(samples.blocks == block)
            total = 0.0
            for start in range(0, len(positions), batch):
                ids = positions[start : start + batch]
                budget.forward("difference")
                error = model(samples.x[ids]) - (
                    samples.target[ids] - samples.base[ids]
                )
                total += float(
                    torch.dot(error.square().sum(1), samples.weights[ids]) / 20000
                )
            values.append(total)
    return np.array(values)


def verify(out, sealed=True):
    from .workflow import (
        CONFIG_SHA,
        PLAN_SHA,
        evaluate_prefix,
        source_guard,
        specification,
    )

    spec = specification()
    source = ROOT / spec["source_run"]
    if sealed:
        check_index(out)
        completed = read_json(out / "completed.json")
        if (
            not completed["execution_complete"]
            or not completed["numerical_verification"]
            or not 0 < completed["elapsed_seconds"] <= spec["hard_timeout_seconds"]
            or read_json(out / "launcher.json")["exitcode"] != 0
        ):
            raise ValueError("Scientific diagnostic did not finish successfully")
    manifest = read_json(out / "manifest.json")
    if (
        manifest["specification"] != spec
        or manifest["config_sha256"] != CONFIG_SHA
        or manifest["plan_sha256"] != PLAN_SHA
    ):
        raise ValueError("Diagnostic specification differs")
    protected = source_guard(spec)
    if protected != read_json(out / "protected_before.json"):
        raise ValueError("Protected inventory differs")
    check_hashes(manifest["sources"])
    check_hashes(manifest["sources"], out / "sources")
    lock = read_json(out / "output_lock.json")
    expected_lock = {
        f"{name}_{h}.{ext}"
        for h in spec["prefixes"]
        for name, ext in (("prefix", "npz"), ("inputs", "json"))
    }
    if set(lock["files"]) != expected_lock:
        raise ValueError("Incomplete gradient output lock")
    check_hashes(lock["files"], out)
    parameter_layout = read_json(out / "parameter_layout.json")
    budget = Budget(spec["replay_gradient_calls"], spec["replay_difference_calls"])
    prefixes, count, max_difference = {}, 0, 0.0

    def close(actual, expected, atol, rtol):
        nonlocal count, max_difference
        a, b = np.asarray(actual), np.asarray(expected)
        if a.shape != b.shape or not np.allclose(
            a, b, atol=atol, rtol=rtol, equal_nan=True
        ):
            raise ArithmeticError("Independent diagnostic values differ")
        valid = np.isfinite(a) & np.isfinite(b)
        if valid.any():
            max_difference = max(max_difference, float(abs(a[valid] - b[valid]).max()))
        count += a.size

    expected_difference, skipped, input_values = 0, 0, 0
    for h in spec["prefixes"]:
        data, info, names = evaluate_prefix(source, h, spec, budget, independent=True)
        if names != parameter_layout:
            raise ValueError("Parameter groups changed")
        stored_info = read_json(out / f"inputs_{h}.json")
        for key in (
            "fit_days",
            "observation_rows",
            "labels_sha256",
            "sample_hashes",
            "finite_skipped_models",
        ):
            if stored_info[key] != info[key]:
                raise ValueError(f"Input/skip record differs: {key}")
        expected_difference += info["expected_difference_calls"]
        skipped += info["finite_skipped_models"]
        with np.load(out / f"prefix_{h}.npz", allow_pickle=False) as saved:
            if set(saved.files) != set(data):
                raise ValueError("Gradient array inventory differs")
            for key in data:
                atol = (
                    spec["gradient_atol"] if key == "gradients" else spec["loss_atol"]
                )
                rtol = (
                    spec["gradient_rtol"] if key == "gradients" else spec["loss_rtol"]
                )
                close(saved[key], data[key], atol, rtol)
        input_values += 2 * {342: 226, 432: 458, 612: 831}[h] * 30 * 23 * 4
        prefixes[h] = data
    if (
        budget.gradient_calls != spec["replay_gradient_calls"]
        or budget.backward_calls != budget.gradient_calls
        or budget.difference_calls != expected_difference
    ):
        raise ValueError("Independent gradient/difference call count differs")
    records, differences = tables(prefixes, parameter_layout, spec)
    for name, frame in (("gradients", records), ("finite_differences", differences)):
        pd.testing.assert_frame_equal(
            frame,
            pd.read_csv(out / f"{name}.csv"),
            check_exact=False,
            atol=1e-8,
            rtol=1e-9,
        )
    if (
        len(records) != 270
        or len(differences) != 72
        or not (differences.passed | differences.skipped).all()
    ):
        raise ArithmeticError(
            "Finite-difference verification or record inventory failed"
        )
    # Cross-check saved checkpoint quantities against the original optimizer logs.
    training = pd.read_csv(source / "training.csv")
    summary = pd.read_csv(source / "model_summary.csv")
    log_checks, final_checks = 0, 0
    for row in records[records.parameter_group == "all"].itertuples():
        if row.epoch < 100:
            match = training[
                (training.fit_days == row.fit_days)
                & (training.strategy == row.strategy)
                & (training.seed == row.seed)
                & (training.epoch == row.epoch + 1)
            ]
            if len(match) != 1:
                raise ValueError("Missing pre-update training log")
            close(
                row.total_loss,
                match.loss_before_update.iloc[0],
                spec["loss_atol"],
                spec["loss_rtol"],
            )
            close(row.total_norm, match.gradient_norm.iloc[0], 1e-8, 1e-9)
            log_checks += 1
        else:
            match = summary[
                (summary.fit_days == row.fit_days)
                & (summary.strategy == row.strategy)
                & (summary.seed == row.seed)
            ]
            if len(match) != 1:
                raise ValueError("Missing final model summary")
            close(
                [row.anchor_loss, row.paired_loss],
                match[["final_anchor_loss", "final_paired_loss"]].to_numpy()[0],
                spec["loss_atol"],
                spec["loss_rtol"],
            )
            final_checks += 1
    execution = read_json(out / "execution.json")
    primary_difference = sum(
        read_json(out / f"inputs_{h}.json")["expected_difference_calls"]
        for h in spec["prefixes"]
    )
    expected = dict(
        gradient_calls=spec["primary_gradient_calls"],
        difference_calls=primary_difference,
        forward_calls=spec["primary_gradient_calls"] + primary_difference,
        backward_calls=spec["primary_gradient_calls"],
        **{
            k: spec[k]
            for k in (
                "neural_updates",
                "physical_forward_calls",
                "physical_optimizer_nfev",
                "scaler_fits",
                "probability_scale_fits",
            )
        },
    )
    if execution != expected or log_checks != 72 or final_checks != 18:
        raise ValueError("Scientific execution count differs")
    check_hashes(protected)
    return dict(
        passed=True,
        checkpoint_replays=90,
        gradient_rows=len(records),
        difference_rows=len(differences),
        finite_skipped_models=skipped,
        finite_max_absolute_error=float(differences.absolute_error.max()),
        numeric_values_checked=count,
        max_absolute_difference=max_difference,
        input_fingerprint_values=input_values,
        original_log_checks=log_checks,
        final_loss_checks=final_checks,
        protected_files_checked=len(protected),
        source_files_checked=len(manifest["sources"]),
        replay_calls=budget.record(),
        new_neural_updates=0,
        new_physical_forwards=0,
        raw_observation_as_of_verified="unknown",
    )
