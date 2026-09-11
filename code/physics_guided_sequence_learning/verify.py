"""Bounded final-checkpoint replay, NumPy forecasts and independent scoring."""

from datetime import datetime
import math

import numpy as np
import pandas as pd
import torch

from physics_guided_forecast_error.artifacts import (
    ROOT,
    array_sha,
    check_hashes,
    check_index,
    load_observations,
    read_json,
    sha,
)
from physics_guided_sample_learning.core import CUTOFFS, POINTS
from physics_guided_sequence.core import Budget
from .audit import close, comparisons, probability_rows, table_close, verify_logs
from .core import history_diagnostics, new_model, output, predict
from .reference import predict as reference_predict
from .support import (
    CONFIG_SHA,
    PLAN_SHA,
    STRATEGIES,
    prepare_prefix,
    read_table,
    require,
    source_guard,
    specification,
)


def arrays(path):
    with np.load(path, allow_pickle=False) as saved:
        return {key: saved[key].copy() for key in saved.files}


def payload_check(path, expected):
    actual = arrays(path)
    require(set(actual) == set(expected), "Changed saved input keys")
    for key, value in expected.items():
        np.testing.assert_array_equal(actual[key], value)


def lock_check(out, name, expected):
    receipt = read_json(out / name)
    require(
        set(receipt["files"]) == {str(p.relative_to(out)) for p in expected},
        f"Incomplete locked inventory: {name}",
    )
    check_hashes(receipt["files"], out)
    return datetime.fromisoformat(receipt["locked_utc"])


def manual_losses(batch, correction):
    result = []
    for block in (0, 1):
        terms = []
        for i in np.flatnonzero(batch.blocks == block):
            b, lead = int(batch.row_batch[i]), int(batch.row_lead[i])
            difference = (
                batch.baseline[b, lead].numpy()
                + correction[b, lead]
                - batch.targets[i].numpy()
            ) / 100
            terms.append(
                2
                * float(batch.weights[i])
                * math.fsum(float(v) ** 2 for v in difference)
                / 4
            )
        result.append(math.fsum(terms))
    return np.array(result)


def sorted_metrics(frame):
    return frame.sort_values(["outer_days", "strategy", "station", "part"]).reset_index(
        drop=True
    )


def verify(out, sealed=True):
    spec = specification()
    if sealed:
        check_index(out)
        complete = read_json(out / "completed.json")
        require(
            complete["execution_complete"]
            and complete["numerical_verification"]
            and 0 < complete["elapsed_seconds"] <= spec["hard_timeout_seconds"]
            and read_json(out / "launcher.json")["exitcode"] == 0,
            "Original execution did not succeed",
        )
    manifest = read_json(out / "manifest.json")
    require(
        manifest["specification"] == spec
        and manifest["config_sha256"] == CONFIG_SHA
        and manifest["plan_sha256"] == PLAN_SHA,
        "Changed manifest specification",
    )
    protected = source_guard(spec)
    require(
        protected == read_json(out / "protected_before.json"),
        "Changed protected inventory",
    )
    check_hashes(manifest["sources"])
    check_hashes(manifest["sources"], out / "sources")
    source = ROOT / spec["learning_source"]
    require(
        sha(out / "input.csv") == manifest["input_sha256"] == sha(source / "input.csv"),
        "Changed input snapshot",
    )
    require(
        sha(out / "reference_metrics.csv") == sha(source / "metrics.csv"),
        "Changed NORM reference",
    )
    budget = Budget(spec["verification_limits"])
    means, summary_rows, training_rows, history_rows = {}, [], [], []
    input_count, checkpoint_count, mean_count, maximum = 0, 0, 0, 0.0
    previous_time = None
    for h in spec["prefixes"]:
        data = prepare_prefix(spec, out / "input.csv", h)
        input_count += data["input_values_checked"]
        constants = read_json(out / f"constants_{h}.json")
        read_time = datetime.fromisoformat(constants["label_read_utc"])
        require(
            constants["fit_days"] == h
            and constants["labels_sha256"] == array_sha(data["labels"])
            and constants["feature_sha256"] == array_sha(data["pool"][h][1])
            and constants["input_values_checked"] == data["input_values_checked"],
            "Changed prefix constants",
        )
        previous_h = {432: 342, 612: 432}.get(h)
        require(
            constants["prior_lock_sha256"]
            == (sha(out / f"mean_lock_{previous_h}.json") if previous_h else None),
            "Changed prefix lock chain",
        )
        if previous_time is not None:
            require(
                previous_time <= read_time,
                "Next observation prefix read before the prior mean lock",
            )
        require(
            sha(out / f"scalers_{h}.json") == sha(source / f"scalers_{h}.json"),
            "Changed frozen scaler",
        )
        for name in ("pairs", "queries", "fingerprints"):
            pd.testing.assert_frame_equal(
                read_table(out / f"{name}_{h}.csv"), data[name], check_exact=True
            )
        for sample, batch in data["batches"].items():
            payload_check(out / f"batch_{h}_{sample}.npz", batch.payload())
        evaluation = data["evaluation"]
        payload_check(out / f"evaluation_{h}.npz", evaluation.payload())
        base = data["pool"][h][0]
        require(
            sha(out / f"mean_{h}_P0.npz") == sha(source / f"mean_{h}_P0.npz"),
            "Changed P0 mean",
        )
        np.testing.assert_array_equal(
            arrays(out / f"mean_{h}_P0.npz")["means"], base[None]
        )
        for strategy in STRATEGIES:
            sample, variant = strategy.split("_")
            batch = data["batches"][sample]
            saved_mean = arrays(out / f"mean_{h}_{strategy}.npz")["means"]
            require(
                saved_mean.shape == (3, h + 180, 4)
                and np.isnan(saved_mean[:, :29]).all()
                and np.isfinite(saved_mean[:, 29:]).all(),
                "Incomplete three-seed mean",
            )
            means[h, strategy] = saved_mean
            for seed in spec["seeds"]:
                model = new_model(seed)
                initial = {k: v.clone() for k, v in model.state_dict().items()}
                require(
                    sum(v.numel() for v in initial.values())
                    == spec["model_parameters"],
                    "Changed model size",
                )
                directory = out / f"model_{h}_{strategy}_{seed}"
                require(
                    {p.name for p in directory.glob("*.pt")}
                    == {f"e{e}.pt" for e in spec["checkpoint_epochs"]},
                    "Checkpoint inventory differs",
                )
                for epoch in spec["checkpoint_epochs"]:
                    checkpoint = torch.load(
                        directory / f"e{epoch}.pt",
                        weights_only=True,
                        map_location="cpu",
                    )
                    require(
                        list(checkpoint) == list(initial), "Checkpoint identity differs"
                    )
                    for key, value in checkpoint.items():
                        require(
                            value.shape == initial[key].shape
                            and value.dtype == torch.float64
                            and torch.isfinite(value).all(),
                            "Invalid checkpoint parameter",
                        )
                        if epoch == 0:
                            require(
                                torch.equal(value, initial[key]),
                                "Changed paired deterministic initialization",
                            )
                    checkpoint_count += 1
                model.load_state_dict(checkpoint)
                with torch.no_grad():
                    correction = output(
                        model, batch.encoder, batch.decoder, variant, budget
                    ).numpy()
                initial_loss = manual_losses(batch, np.zeros_like(correction))
                final_loss = manual_losses(batch, correction)
                summary = read_json(directory / "summary.json")
                require(
                    (summary["fit_days"], summary["strategy"], summary["seed"])
                    == (h, strategy, seed),
                    "Changed summary identity",
                )
                close(
                    [summary["initial_anchor_loss"], summary["initial_paired_loss"]],
                    initial_loss,
                )
                close(
                    [summary["final_anchor_loss"], summary["final_paired_loss"]],
                    final_loss,
                )
                summary_rows.append(summary)
                training_rows.append(read_table(directory / "training.csv"))
                replay = predict(
                    model,
                    evaluation,
                    variant,
                    base,
                    data["queries"].target.to_numpy(),
                    budget,
                )
                reference = reference_predict(
                    model.state_dict(),
                    evaluation.encoder.numpy(),
                    evaluation.decoder.numpy(),
                    variant,
                    budget,
                )
                independent = np.full_like(base, np.nan)
                independent[29:31] = base[29:31]
                targets = data["queries"].target.to_numpy()
                independent[targets] = (
                    base[targets]
                    + reference[
                        evaluation.row_batch.numpy(), evaluation.row_lead.numpy()
                    ]
                )
                for value in (replay, independent):
                    close(saved_mean[seed], value)
                    maximum = max(
                        maximum,
                        float(np.max(np.abs(saved_mean[seed, 29:] - value[29:]))),
                    )
                    mean_count += value[29:].size
                gradients = pd.DataFrame(
                    history_diagnostics(
                        model, evaluation, variant, h, strategy, seed, POINTS, budget
                    )
                )
                stored = read_table(directory / "history_gradients.csv")
                pd.testing.assert_frame_equal(
                    stored.drop(columns="history_gradient_norm"),
                    gradients.drop(columns="history_gradient_norm"),
                    check_exact=True,
                )
                close(
                    stored.history_gradient_norm,
                    gradients.history_gradient_norm,
                    atol=0,
                    rtol=1e-10,
                )
                require(
                    np.isfinite(stored.history_gradient_norm).all()
                    and (stored.history_gradient_norm >= 0).all(),
                    "Invalid history gradient",
                )
                if variant == "RESET":
                    require(
                        (stored.history_gradient_norm == 0).all(),
                        "Reset history path was not removed",
                    )
                history_rows.append(stored)
        files = [
            *out.glob(f"model_{h}_*/*"),
            *out.glob(f"mean_{h}_*.npz"),
            *out.glob(f"batch_{h}_*.npz"),
            out / f"evaluation_{h}.npz",
            out / f"scalers_{h}.json",
            out / f"constants_{h}.json",
            out / f"pairs_{h}.csv",
            out / f"queries_{h}.csv",
            out / f"fingerprints_{h}.csv",
        ]
        previous_time = lock_check(out, f"mean_lock_{h}.json", files)
        require(read_time <= previous_time, "Mean lock predates inputs")
    pd.testing.assert_frame_equal(
        read_table(out / "training.csv"),
        pd.concat(training_rows, ignore_index=True),
        check_exact=True,
    )
    pd.testing.assert_frame_equal(
        read_table(out / "history_gradients.csv"),
        pd.concat(history_rows, ignore_index=True),
        check_exact=True,
    )
    summaries = pd.DataFrame(summary_rows)
    pd.testing.assert_frame_equal(
        read_table(out / "model_summary.csv"), summaries, check_exact=True
    )
    diagnostics = verify_logs(read_table(out / "training.csv"), summaries, spec)
    pd.testing.assert_frame_equal(
        read_table(out / "optimizer_diagnostics.csv"), diagnostics, check_exact=True
    )
    mean_time = lock_check(
        out,
        "mean_lock.json",
        [
            *out.glob("mean_lock_*.json"),
            out / "training.csv",
            out / "model_summary.csv",
            out / "history_gradients.csv",
        ],
    )
    require(previous_time <= mean_time, "Global mean lock is out of order")
    prediction_time = lock_check(
        out,
        "prediction_lock.json",
        [
            p
            for pattern in (
                "prediction_*.npz",
                "calibration_*.npz",
                "calibration_*.json",
            )
            for p in out.glob(pattern)
        ],
    )
    scoring = read_json(out / "scoring.json")
    require(
        mean_time
        <= prediction_time
        <= datetime.fromisoformat(scoring["label_read_utc"])
        and scoring["prediction_lock_sha256"] == sha(out / "prediction_lock.json"),
        "Scoring preceded prediction lock",
    )
    _, _, labels = load_observations(out / "input.csv", 792)
    rows, seed_rows, reference_rows, max_cdf = [], [], [], 0.0
    for n, c in CUTOFFS.items():
        for strategy in (*STRATEGIES, "P0"):
            prediction = arrays(out / f"prediction_{n}_{strategy}.npz")
            mu, sigma = prediction["means"], prediction["scales"]
            if strategy == "P0":
                require(
                    sha(out / f"prediction_{n}_P0.npz")
                    == sha(source / f"prediction_{n}_P0.npz"),
                    "P0 was refitted",
                )
            else:
                np.testing.assert_array_equal(mu, means[n, strategy])
                calibration = arrays(out / f"calibration_{n}_{strategy}.npz")
                frozen = means[c, strategy][:, c:n]
                np.testing.assert_array_equal(calibration["means"], frozen)
                expected_sigma = np.maximum(
                    0.001, np.sqrt(np.mean((frozen - labels[None, c:n]) ** 2, axis=1))
                )
                close(sigma, expected_sigma)
                np.testing.assert_array_equal(calibration["scales"], sigma)
                receipt = read_json(out / f"calibration_{n}_{strategy}.json")
                require(
                    receipt["origin"] == c
                    and receipt["end"] == n
                    and receipt["labels_sha256"] == array_sha(labels[c:n])
                    and receipt["mean_lock_sha256"] == sha(out / f"mean_lock_{c}.json")
                    and mean_time
                    <= datetime.fromisoformat(receipt["label_read_utc"])
                    <= prediction_time,
                    "Changed scale calibration or time ordering",
                )
            values, error = probability_rows(
                mu,
                sigma,
                labels[: n + 180],
                n,
                strategy,
                arrays(out / f"distribution_{n}_{strategy}.npz"),
            )
            rows.extend(values)
            max_cdf = max(max_cdf, error)
            for k in range(len(mu)):
                values, error = probability_rows(
                    mu[k : k + 1],
                    sigma[k : k + 1],
                    labels[: n + 180],
                    n,
                    strategy,
                    arrays(out / f"distribution_{n}_{strategy}_seed{k}.npz"),
                )
                seed_rows.extend(
                    [
                        dict(
                            component="deterministic"
                            if strategy == "P0"
                            else f"seed_{k}",
                            **r,
                        )
                        for r in values
                    ]
                )
                max_cdf = max(max_cdf, error)
        for strategy in ("P0", "IN", "OOF"):
            frozen = arrays(source / f"prediction_{n}_{strategy}.npz")
            values, error = probability_rows(
                frozen["means"],
                frozen["scales"],
                labels[: n + 180],
                n,
                strategy,
                arrays(source / f"distribution_{n}_{strategy}.npz"),
            )
            reference_rows.extend(values)
            max_cdf = max(max_cdf, error)
    metrics, seed_metrics = (
        read_table(out / "metrics.csv"),
        read_table(out / "seed_metrics.csv"),
    )
    table_close(metrics, pd.DataFrame(rows))
    table_close(seed_metrics, pd.DataFrame(seed_rows))
    reference = read_table(out / "reference_metrics.csv")
    table_close(sorted_metrics(reference), sorted_metrics(pd.DataFrame(reference_rows)))
    table_close(read_table(out / "comparisons.csv"), comparisons(metrics, reference))
    execution = read_json(out / "execution.json")
    require(
        execution["counts"] == spec["main_limits"]
        and execution["scale_fits"] == 8
        and execution["input_values_checked"] == input_count
        and all(
            execution[k] == 0
            for k in (
                "physical_forward_calls",
                "physical_fits",
                "scaler_fits",
                "scale_network_updates",
            )
        ),
        "Changed recorded execution counts",
    )
    require(
        budget.counts == spec["verification_limits"] and checkpoint_count == 180,
        "Numerical replay counts differ",
    )
    check_hashes(protected)
    check_hashes(manifest["sources"])
    return dict(
        passed=True,
        counts=budget.counts,
        checkpoints_checked=checkpoint_count,
        final_models_replayed=36,
        input_values_checked=input_count,
        mean_values_checked=mean_count,
        max_mean_difference_mm=maximum,
        max_quantile_cdf_error=max_cdf,
        ensemble_metric_rows=len(metrics),
        seed_metric_rows=len(seed_metrics),
        reference_metric_rows=len(reference),
        history_gradient_rows=sum(len(r) for r in history_rows),
        protected_files_checked=len(protected),
        source_files_checked=len(manifest["sources"]),
        neural_updates=0,
        scale_fits=0,
        physical_calls=0,
        scaler_fits=0,
        complete_optimizer_trajectory_retrained=False,
        raw_observation_as_of_verified="unknown",
    )
