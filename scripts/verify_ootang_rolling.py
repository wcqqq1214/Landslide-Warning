"""Read-only reconstruction of rolling chronology, scores and model outputs.

The probability and calibration checks are implemented independently of the
training scorer. Neural regeneration imports the run's frozen source snapshot.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from statistics import NormalDist
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
POINTS = ("ATU1", "ATU5", "MJ3", "MJ1")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def array_sha(x):
    x = np.ascontiguousarray(x)
    return hashlib.sha256(
        str((x.shape, str(x.dtype))).encode() + x.tobytes()
    ).hexdigest()


def independent_metrics(y, mean, sigma):
    error = mean - y
    z = (y - mean) / sigma
    erf = np.vectorize(math.erf, otypes=[float])
    c = sigma * (
        z * erf(z / math.sqrt(2))
        + math.sqrt(2 / math.pi) * np.exp(-z * z / 2)
        - 1 / math.sqrt(math.pi)
    )
    result = dict(
        mae=abs(error).mean(axis=0),
        rmse=np.sqrt((error**2).mean(axis=0)),
        crps=c.mean(axis=0),
    )
    normal = NormalDist()
    for coverage in (0.8, 0.9, 0.95):
        q = normal.inv_cdf((1 + coverage) / 2)
        low, high = mean - q * sigma, mean + q * sigma
        miss = np.where(y < low, low - y, np.where(y > high, y - high, 0))
        result[f"coverage{round(coverage * 100)}"] = ((low <= y) & (y <= high)).mean(
            axis=0
        )
        result[f"width{round(coverage * 100)}"] = (high - low).mean(axis=0)
        result[f"interval_score{round(coverage * 100)}"] = (
            high - low + 2 * miss / (1 - coverage)
        ).mean(axis=0)
    return result


def verify(run, out, reload_models=True):
    if out.exists():
        raise FileExistsError(out)
    out.mkdir(parents=True)
    status = json.loads((run / "status.json").read_text())
    if status["state"] != "completed" or status["exit_code"] != 0:
        raise ValueError("Run did not complete")
    manifest = json.loads((run / "artifact_manifest.json").read_text())["files"]
    for name, expected in manifest.items():
        if sha(run / name) != expected:
            raise ValueError("Run artifact changed: " + name)
    sources = json.loads((run / "sources.json").read_text())["files"]
    for name, expected in sources.items():
        frozen = run / "sources" / name
        path = frozen if frozen.exists() else ROOT / name
        if sha(path) != expected:
            raise ValueError("Frozen source changed: " + name)
    config_paths = list(
        (run / "sources/config").glob("ootang_rolling_probability*.json")
    )
    if len(config_paths) != 1:
        raise ValueError("Ambiguous execution configuration")
    spec = json.loads(config_paths[0].read_text())
    phase_names = [
        name
        for name in ("inner", "development", "transfer")
        if (run / name / "metrics.csv").exists()
    ]
    max_end = max(spec["stages"][name][1] for name in phase_names)
    labels = pd.read_csv(
        ROOT / spec["data"], nrows=max_end, usecols=[p + "/mm" for p in POINTS]
    )[[p + "/mm" for p in POINTS]].to_numpy(float)
    phases = {}
    for phase in phase_names:
        phases[phase] = {}
        for p in (run / phase).glob("*.npz"):
            with np.load(p) as a:
                phases[phase][p.stem] = {k: a[k].copy() for k in a.files}
    raw_events = (run / "events.jsonl").read_text().splitlines()
    events = [json.loads(line) for line in raw_events]
    chain = ""
    phase = None
    next_origin = None
    awaiting_release = False
    locks = 0
    for raw_event, event in zip(raw_events, events):
        if event["previous_sha256"] != chain:
            raise ValueError("Event chain broken")
        # Hash the original bytes. Integer score keys become strings on JSON
        # loading, so sorting a reconstructed object can change their order.
        chain = hashlib.sha256(raw_event.encode()).hexdigest()
        if event["kind"] == "forecast_phase_started":
            phase = next(
                k
                for k in phase_names
                if spec["stages"][k] == [event["start"], event["end"]]
            )
            next_origin = event["start"]
            if awaiting_release:
                raise ValueError("Unreleased origin at phase boundary")
        elif event["kind"] == "forecast_locked":
            n = event["origin"]
            if (
                awaiting_release
                or n != next_origin
                or event["available_label_last_index"] != n - 1
            ):
                raise ValueError("Forecast follows future label release")
            if event["teacher_prefix"] > n:
                raise ValueError("Future-calibrated physics teacher")
            if event["history_sha256"] != array_sha(labels[:n]):
                raise ValueError("Origin observation prefix differs")
            for name, expected in event["predictions"].items():
                a = phases[phase][name]
                i = n - int(a["origins"][0])
                h = event["valid_horizons"]
                actual = array_sha(
                    np.stack(
                        [a["mean"][i, :h], a["sigma"][i, :h], a["raw_sigma"][i, :h]]
                    )
                )
                if actual != expected:
                    raise ValueError(
                        "Saved prediction differs from its pre-release lock"
                    )
            awaiting_release = True
            locks += 1
        elif event["kind"] == "observation_released":
            if not awaiting_release or event["index"] != next_origin:
                raise ValueError("Observation released without forecast")
            if event["value_sha256"] != array_sha(labels[next_origin]):
                raise ValueError("Wrong released observation")
            next_origin += 1
            awaiting_release = False
    if awaiting_release:
        raise ValueError("Incomplete chronological record")
    summary_records = []
    max_cal_error = 0.0
    max_score_error = 0.0
    checked_scores = 0
    for phase, forecasts in phases.items():
        start, end = spec["stages"][phase]
        metrics = pd.read_csv(run / phase / "metrics.csv").set_index(
            ["model", "horizon", "point"]
        )
        summary = pd.read_csv(run / phase / "summary.csv").set_index(
            ["model", "horizon"]
        )
        prob = spec["probability"]
        for name, a in forecasts.items():
            np.testing.assert_array_equal(a["origins"], np.arange(start, end))
            valid = a["origins"][:, None] + np.arange(spec["horizons"])[None, :] < end
            if not np.isnan(a["mean"][~valid]).all():
                raise ValueError("Out-of-window predictions were retained")
            for i, n in enumerate(a["origins"]):
                for k in range(min(spec["horizons"], end - n)):
                    # A horizon k+1 matures after k+1 subsequent observed days.
                    stop = i - k
                    begin = max(0, stop - prob["window"])
                    stop = max(stop, 0)
                    history = np.arange(begin, stop)
                    target_ids = a["origins"][history] + k
                    if len(target_ids) and target_ids.max() >= n:
                        raise ValueError(
                            "Independent calibration reached future labels"
                        )
                    squared = (
                        (labels[target_ids] - a["mean"][history, k])
                        / a["raw_sigma"][history, k]
                    ) ** 2
                    factor = np.sqrt(
                        (prob["prior_sum_squares"] + squared.sum(axis=0))
                        / (prob["prior_count"] + len(history))
                    )
                    sigma = np.maximum(
                        prob["sigma_floor_mm"], a["raw_sigma"][i, k] * factor
                    )
                    max_cal_error = max(
                        max_cal_error, float(np.max(abs(sigma - a["sigma"][i, k])))
                    )
                    np.testing.assert_allclose(
                        sigma, a["sigma"][i, k], rtol=1e-12, atol=1e-10
                    )
            for k in range(spec["horizons"]):
                ids = a["origins"] + k
                mask = ids < end
                values = independent_metrics(
                    labels[ids[mask]], a["mean"][mask, k], a["sigma"][mask, k]
                )
                for j, p in enumerate(POINTS):
                    row = metrics.loc[(name, k + 1, p)]
                    if int(row.n) != int(mask.sum()):
                        raise ValueError("Wrong horizon sample count")
                    for metric, v in values.items():
                        if name == "B_RAW" and metric not in ("mae", "rmse"):
                            if pd.notna(row[metric]):
                                raise ValueError(
                                    "Original B+ was assigned probability scores"
                                )
                            continue
                        difference = abs(v[j] - float(row[metric]))
                        max_score_error = max(max_score_error, float(difference))
                        checked_scores += 1
                        np.testing.assert_allclose(
                            v[j], row[metric], rtol=5e-11, atol=1e-8
                        )
                aggregate = summary.loc[(name, k + 1)]
                for metric, v in values.items():
                    if name == "B_RAW" and metric not in ("mae", "rmse"):
                        continue
                    np.testing.assert_allclose(
                        v.mean(), aggregate[metric], rtol=5e-11, atol=1e-8
                    )
                np.testing.assert_allclose(
                    np.sqrt(np.mean(values["rmse"] ** 2)),
                    aggregate.pooled_rmse,
                    rtol=5e-11,
                    atol=1e-8,
                )
            summary_records.append(
                dict(
                    phase=phase,
                    model=name,
                    origins=end - start,
                    max_horizon=spec["horizons"],
                )
            )
        print(f"verified chronology and independent scores: {phase}", flush=True)
    reload_result = {"performed": False}
    if reload_models:
        # Frozen training and feature code, not a newer candidate's implementation.
        sys.path.insert(0, str(run / "sources/code"))
        sys.path.insert(1, str(ROOT / "code"))
        import torch
        from rolling_probability.data import (
            example,
            load_teachers,
            select_teacher,
            training_examples,
        )
        from rolling_probability.models import Scaling, predict
        from rolling_probability.run import load_group

        torch.set_num_threads(spec["cpu_threads"])
        torch.use_deterministic_algorithms(True)
        pool_paths = [
            ROOT / name for name in sources if name.endswith("/teacher_252.npz")
        ]
        if len(pool_paths) != 1:
            raise ValueError("Ambiguous physical cache")
        pool = load_teachers(pool_paths[0].parent)
        max_mu = 0.0
        max_sd = 0.0
        model_count = 0
        origins_regenerated = 0
        data_options = {}
        if spec.get("extra_baselines"):
            data_options["extra_baselines"] = spec["extra_baselines"]
        for phase in phase_names:
            start, end = spec["stages"][phase]
            train_dir = run / (
                "inner_training" if phase == "inner" else phase + "_training"
            )
            contract = json.loads((train_dir / "data_contract.json").read_text())
            if contract["label_rows"] != start or contract["label_sha256"] != array_sha(
                labels[:start]
            ):
                raise ValueError("Training label prefix changed")
            data = training_examples(
                labels[:start],
                pool,
                spec["horizons"],
                spec["history_days"],
                **data_options,
            )
            regenerated = Scaling(data)
            stored = json.loads((train_dir / "scaling.json").read_text())
            for key, val in regenerated.__dict__.items():
                np.testing.assert_array_equal(val, np.array(stored[key]))
            with np.load(train_dir / "training_queries.npz") as queries:
                np.testing.assert_array_equal(queries["origins"], data["origins"])
                np.testing.assert_array_equal(queries["teachers"], data["teachers"])
                if queries["target_last"].max() >= start:
                    raise ValueError("Training target crossed stage split")
            steps = (
                spec["checkpoints"]
                if phase == "inner"
                else [
                    json.loads((run / "decision.json").read_text())["selected_updates"]
                ]
            )
            for step in steps:
                name = (
                    f"{spec['candidate']}_u{step}"
                    if phase == "inner"
                    else spec["candidate"]
                )
                models, scaling, _ = load_group(train_dir, spec, step)
                saved = phases[phase][name]
                model_count += len(models)
                for i, n in enumerate(saved["origins"]):
                    x, z, b = example(
                        labels[:n],
                        select_teacher(pool, n),
                        spec["horizons"],
                        spec["history_days"],
                        **data_options,
                    )
                    H = min(len(z), end - n)
                    z = z[:H]
                    model_options = {}
                    if spec.get("expert_names"):
                        model_options["expert_means"] = np.stack(
                            [b[k][:H] for k in spec["expert_names"]], axis=1
                        )[None]
                    mean, sd, seed_mean, seed_sigma = predict(
                        models,
                        scaling,
                        x[None],
                        z[None],
                        b["B_ANCHOR"][None, :H],
                        **model_options,
                    )
                    max_mu = max(
                        max_mu, float(np.max(abs(mean[0] - saved["mean"][i, :H])))
                    )
                    max_sd = max(
                        max_sd, float(np.max(abs(sd[0] - saved["raw_sigma"][i, :H])))
                    )
                    np.testing.assert_array_equal(mean[0], saved["mean"][i, :H])
                    np.testing.assert_array_equal(sd[0], saved["raw_sigma"][i, :H])
                    for j, seed in enumerate(spec["seeds"]):
                        seed_saved = phases[phase][f"{name}_seed{seed}"]
                        np.testing.assert_array_equal(
                            seed_mean[j, 0], seed_saved["mean"][i, :H]
                        )
                        np.testing.assert_array_equal(
                            seed_sigma[j, 0], seed_saved["raw_sigma"][i, :H]
                        )
                    origins_regenerated += 1
                print(f"reloaded all origins: {phase} {name}", flush=True)
        reload_result = dict(
            performed=True,
            models=model_count,
            origin_ensembles=origins_regenerated,
            max_mean_difference_mm=max_mu,
            max_raw_sigma_difference_mm=max_sd,
        )
    receipt = dict(
        passed=True,
        checked_utc=datetime.now(timezone.utc).isoformat(),
        run=str(run.relative_to(ROOT)),
        immutable_artifacts_checked=len(manifest),
        source_entries_checked=len(sources),
        chronological_forecast_locks=locks,
        score_cells_checked=checked_scores,
        max_score_csv_rounding_error=max_score_error,
        max_calibration_difference_mm=max_cal_error,
        stages=summary_records,
        reload=reload_result,
        maximum_observation_rows_read=max_end,
        new_training_updates=0,
        physical_forward_calls=0,
        verification_script_sha256=sha(__file__),
    )
    (out / "verification.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )
    print(json.dumps(receipt, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("run")
    parser.add_argument("--out", required=True)
    parser.add_argument("--skip-reload", action="store_true")
    args = parser.parse_args()
    verify(Path(args.run).resolve(), Path(args.out).resolve(), not args.skip_reload)
