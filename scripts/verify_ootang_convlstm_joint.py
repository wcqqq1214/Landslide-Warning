"""Read-only replay of v2.4 checkpoints and saved scores; never trains."""

import json
from datetime import datetime
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from physics_guided_joint import (  # noqa: E402
    ROOT, ARMS, specification, sha, write_json, check_hashes, training_inputs,
    evaluation_inputs, new_models, objectives, predict_distribution,
    load_distribution, score_saved, decide, reference_metric_error,
)
from physics_guided_forecast_error.artifacts import load_observations  # noqa: E402
from physics_guided_origin_learning.support import read_teachers  # noqa: E402


def check_frame(actual, expected, atol):
    if list(actual.columns) != list(expected.columns) or actual.shape != expected.shape:
        raise AssertionError("Score table layout changed")
    for column in expected:
        if pd.api.types.is_numeric_dtype(expected[column]):
            np.testing.assert_allclose(actual[column], expected[column], rtol=0, atol=atol, equal_nan=True)
        else:
            pd.testing.assert_series_equal(actual[column], expected[column], check_dtype=False, check_exact=True)


def run_checks(out, spec):
    manifest = json.loads((out / "manifest.json").read_text())
    check_hashes(manifest["sources"])
    index = json.loads((out / "artifact_manifest.json").read_text())
    for name, digest in index.items():
        if sha(out / name) != digest:
            raise AssertionError("Saved artifact changed: " + name)
    if manifest["config"] != spec:
        raise AssertionError("Executed config differs")
    execution = json.loads((out / "execution.json").read_text())
    if execution["status"] != "completed" or execution["elapsed_seconds"] > spec["run_limit_seconds"]:
        raise AssertionError("Incomplete or over-budget execution")
    if datetime.fromisoformat(execution["ended_utc"]) > datetime.fromisoformat(spec["work_deadline_utc"]):
        raise AssertionError("Work deadline exceeded")
    if sha(out / "config.json") != manifest["config_sha256"]:
        raise AssertionError("Saved execution configuration changed")
    for key, expected in dict(mean_updates=1200, scale_updates=1200, coordinated_iterations=1200,
                              optimizer_steps=2400, physical_forward_calls=0, scaler_fits=0, retry_count=0).items():
        if execution[key] != expected:
            raise AssertionError("Execution count differs: " + key)
    events = [json.loads(line) for line in (out / "events.jsonl").read_text().splitlines()]
    if [e["sequence"] for e in events] != list(range(len(events))):
        raise AssertionError("Broken access-event sequence")
    if [(e["kind"], e.get("rows", e.get("prefix"))) for e in events] != [
        ("run_started", None), ("observation_prefix_read", 432), ("prefix_locked", 432),
        ("observation_prefix_read", 612), ("prefix_locked", 612),
        ("observation_prefix_read", 792), ("run_completed", None),
    ]:
        raise AssertionError("Observation reads do not follow complete paired locks")
    for h in spec["outer_days"]:
        lock = json.loads((out / f"prefix_lock_{h}.json").read_text())
        event = next(e for e in events if e["kind"] == "prefix_locked" and e["prefix"] == h)
        if lock["artifacts"] != event["artifacts"]:
            raise AssertionError("Prefix lock differs from causal event")
        for name, digest in lock["artifacts"].items():
            if sha(out / name) != digest:
                raise AssertionError("Prediction changed after prefix lock")
        for arm in ARMS:
            for seed in spec["seeds"]:
                if f"model_{h}_{arm}_seed{seed}_e100.pt" not in lock["artifacts"]:
                    raise AssertionError("Incomplete arm/seed lock")
    logs = pd.read_csv(out / "training.csv", float_precision="round_trip")
    if len(logs) != 1200 or not np.isfinite(logs.select_dtypes(include="number")).all().all():
        raise AssertionError("Training log incomplete/nonfinite")
    for h in spec["outer_days"]:
        for seed in spec["seeds"]:
            for arm in ARMS:
                subset = logs[(logs.prefix == h) & (logs.seed == seed) & (logs.arm == arm)]
                if subset.epoch.tolist() != list(range(1, 101)):
                    raise AssertionError("Epochs were selected or repeated")
                norm = subset.nll_mean_gradient_norm
                if (arm == "DETACHED" and (norm != 0).any()) or (arm == "JOINT" and not (norm > 0).any()):
                    raise AssertionError("Probability-gradient arm is not established")
    maximum_reload, maximum_control, maximum_objective = 0.0, 0.0, 0.0
    for h in spec["outer_days"]:
        labels = load_observations(ROOT / spec["data"], h)[2]
        pool = read_teachers(ROOT / spec["data"], ROOT / spec["physical_source"], h)
        table, batch, context, sigma0 = training_inputs(spec, h, labels, pool)
        eval_table, eval_batch, eval_context = evaluation_inputs(spec, h, labels, pool)
        check_frame(pd.read_csv(out / f"queries_{h}.csv", float_precision="round_trip"), table, 0)
        check_frame(pd.read_csv(out / f"evaluation_queries_{h}.csv"), eval_table, 0)
        for name, data in ((f"training_inputs_{h}.npz", batch.payload()),
                           (f"evaluation_inputs_{h}.npz", eval_batch.payload())):
            with np.load(out / name, allow_pickle=False) as saved:
                if set(saved.files) != set(data):
                    raise AssertionError("Input pack changed")
                for key, value in data.items():
                    np.testing.assert_array_equal(saved[key], value)
        np.testing.assert_array_equal(
            json.loads((out / f"initial_scale_{h}.json").read_text())["sigma_mm"], sigma0
        )
        objective_rows = json.loads((out / f"objectives_{h}.json").read_text())
        if len(objective_rows) != 6:
            raise AssertionError("Missing final objective records")
        old_mean, _ = load_distribution(out, spec, h, "DIRECT_V2_0")
        for seed in spec["seeds"]:
            initial = torch.load(out / f"initial_{h}_seed{seed}.pt", weights_only=True)
            mean, scale = new_models(seed, sigma0)
            for branch, model in (("mean", mean), ("scale", scale)):
                for key, tensor in model.state_dict().items():
                    torch.testing.assert_close(tensor, initial[branch][key], rtol=0, atol=0)
            initial_mu, initial_sigma = predict_distribution(mean, scale, eval_batch, eval_context, h)
            np.testing.assert_array_equal(initial_mu[30:], pool[h][0][30:])
            np.testing.assert_allclose(initial_sigma[30:], np.broadcast_to(sigma0, initial_sigma[30:].shape),
                                       rtol=0, atol=spec["reload_atol_mm"])
            for arm in ARMS:
                before = objectives(mean, scale, batch, context, arm == "DETACHED")
                saved = torch.load(out / f"model_{h}_{arm}_seed{seed}_e100.pt", weights_only=True)
                mean.load_state_dict(saved["mean"])
                scale.load_state_dict(saved["scale"])
                mu, sigma = predict_distribution(mean, scale, eval_batch, eval_context, h)
                saved_mu, saved_sigma = load_distribution(out, spec, h, arm)
                for actual, expected in ((mu, saved_mu[seed]), (sigma, saved_sigma[seed])):
                    np.testing.assert_allclose(actual[30:], expected[30:], rtol=0, atol=spec["reload_atol_mm"])
                    maximum_reload = max(maximum_reload, float(np.max(abs(actual[30:] - expected[30:]))))
                if arm == "DETACHED":
                    np.testing.assert_allclose(mu[30:], old_mean[seed, 30:],
                                               rtol=spec["detached_rtol"], atol=spec["detached_atol_mm"])
                    maximum_control = max(maximum_control, float(np.max(abs(mu[30:] - old_mean[seed, 30:]))))
                final = objectives(mean, scale, batch, context, arm == "DETACHED")
                row = next(r for r in objective_rows if r["seed"] == seed and r["arm"] == arm)
                for phase, current in (("initial", before), ("final", final)):
                    for key, value in current.items():
                        difference = abs(value - row[phase][key])
                        if difference > 1e-12:
                            raise AssertionError("Post-update objective replay differs")
                        maximum_objective = max(maximum_objective, difference)
                mean.load_state_dict(initial["mean"])
                scale.load_state_dict(initial["scale"])
    dates = pd.date_range("2016-07-01", periods=792)
    labels = load_observations(ROOT / spec["data"], 792)[2]
    tables = score_saved(out, spec, labels, dates)
    maximum_score = 0.0
    for name, expected in tables.items():
        saved = pd.read_csv(out / name, float_precision="round_trip")
        check_frame(saved, expected, spec["score_atol_mm"])
        columns = expected.select_dtypes(include="number").columns
        maximum_score = max(maximum_score, float(np.nanmax(abs(saved[columns].values - expected[columns].values))))
    decision = decide(tables["metrics.csv"], spec)
    if decision != json.loads((out / "decision.json").read_text()):
        raise AssertionError("Full frozen decision differs")
    return dict(
        implementation_passed=True, effectiveness_passed=decision["passed"],
        artifact_files_checked=len(index), reloaded_models=12,
        max_reload_error_mm=maximum_reload, max_detached_vs_direct_error_mm=maximum_control,
        max_objective_error=maximum_objective, max_score_error_mm=maximum_score,
        max_reference_metric_error_mm=reference_metric_error(tables["metrics.csv"], spec),
        table_rows={k: len(v) for k, v in tables.items()},
        new_training_updates=0, new_physics_calls=0,
        conclusions="implementation verification is separate from candidate effectiveness",
    )


if __name__ == "__main__":
    spec = specification()
    out = ROOT / spec["output_dir"]
    result = run_checks(out, spec)
    write_json(out / "verification.json", result)
    print(json.dumps(result, indent=2))
