"""C10: replay causal C8 features with one frozen forgetting factor."""

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

import numpy as np
import pandas as pd

from physics_guided.reference import ROOT, save_json, sha
from .data import ObservationStream, array_sha
from .empirical import Recorder, checked_run, utc
from .horizon_scale import arrays
from .online import OnlineRidge
from .ridge import RidgeDistribution
from .scoring import CausalCalibration, aggregate, gate, score_predictions


def phase_run(spec, phase, current, training, issued, models, out, recorder):
    start, end = spec["stages"][phase]
    H, N = spec["horizons"], end - start
    prob = spec["probability"]
    stream = ObservationStream(ROOT / spec["data"], start, end)
    targets = training["origins"][:, None] + np.arange(H)[None]
    if targets.max() >= start:
        raise ValueError("Initial training crosses the observed prefix")
    np.testing.assert_array_equal(training["target"], stream.history[targets])
    np.testing.assert_array_equal(issued["origins"], np.arange(start, end))
    states = {}
    for arm, model in models.items():
        if (
            model.state["training_samples"] != len(training["origins"])
            or model.state["alpha"] != spec["alpha"]
        ):
            raise ValueError("Initial sample set or regularization changed")
        states[arm] = OnlineRidge(
            model,
            training["features"],
            training["base"],
            training["target"],
            start,
            forgetting=spec["forgetting_factor"],
        )
    out.mkdir(parents=True)
    save_json(
        out / "initial_contract.json",
        dict(
            training_rows=start,
            label_sha256=array_sha(stream.history),
            training_samples=len(training["origins"]),
            max_target=int(targets.max()),
            forgetting_factor=spec["forgetting_factor"],
            fixed_regularization={
                arm: len(training["origins"]) * model.state["alpha"]
                for arm, model in models.items()
            },
        ),
    )
    records = {
        name: {k: v.copy() for k, v in values.items()}
        for name, values in current.items()
    }
    calibrators = {}
    for arm in models:
        name = "C10_FORGET_" + arm
        records[name] = {k: v.copy() for k, v in current["C8_ONLINE_" + arm].items()}
        for key in ("mean", "sigma", "calibration_factor", "feedback_log_scale"):
            records[name][key] = np.full_like(records[name][key], np.nan)
        calibrators[name] = CausalCalibration(
            H,
            prob["window"],
            prob["prior_count"],
            prob["prior_sum_squares"],
            feedback=prob["feedback"],
        )
    betas = {arm: np.empty((N,) + state.beta.shape) for arm, state in states.items()}
    weights = {arm: np.empty((N, H)) for arm in models}
    recorder.event(
        "phase_started",
        phase=phase,
        training_prefix=start,
        end=end,
        label_sha256=array_sha(stream.history),
        models=list(records),
    )
    for i, n in enumerate(range(start, end)):
        if len(stream.history) != n:
            raise ValueError("Observation stream crossed forecast origin")
        valid = min(H, end - n)
        features, base = issued["features"][i, :valid], issued["base"][i, :valid]
        for arm, state in states.items():
            name = "C10_FORGET_" + arm
            pred, cal = records[name], calibrators[name]
            betas[arm][i], weights[arm][i] = state.beta, state.effective_weight
            mean = state.predict(features, base)
            if i == 0:
                np.testing.assert_array_equal(
                    mean, current["C8_ONLINE_" + arm]["mean"][0, :valid]
                )
            raw = pred["raw_sigma"][i, :valid]
            factor = cal.factors(n)[:valid]
            sigma = np.maximum(prob["sigma_floor_mm"], raw * factor)
            if not np.isfinite(mean).all() or not np.isfinite(sigma).all():
                raise ArithmeticError("Nonfinite forgetting forecast")
            for key, value in dict(
                mean=mean,
                sigma=sigma,
                calibration_factor=factor,
                feedback_log_scale=cal.log_scale[:valid],
            ).items():
                pred[key][i, :valid] = value
        recorder.event(
            "forecast_locked",
            phase=phase,
            origin=n,
            history_sha256=array_sha(stream.history),
            feature_sha256=array_sha(features),
            beta_sha256={arm: array_sha(state.beta) for arm, state in states.items()},
            predictions={
                name: array_sha(
                    np.stack([r[k][i, :valid] for k in ("mean", "sigma", "raw_sigma")])
                )
                for name, r in records.items()
            },
        )
        observed = stream.release()
        recorder.event(
            "observation_released",
            phase=phase,
            index=n,
            value_sha256=array_sha(observed),
        )
        for k in range(min(H, i + 1)):
            j = i - k
            for arm, state in states.items():
                name = "C10_FORGET_" + arm
                pred = records[name]
                calibrators[name].update(
                    k,
                    observed - pred["mean"][j, k],
                    pred["raw_sigma"][j, k],
                    n,
                    n + 1,
                    issued_sigma=pred["sigma"][j, k],
                )
                state.update(
                    k,
                    issued["features"][j, k],
                    issued["base"][j, k],
                    observed,
                    start + j,
                    n,
                    n + 1,
                )
    for arm, state in states.items():
        np.savez_compressed(
            out / f"online_state_{arm}.npz",
            beta_issued=betas[arm],
            effective_weight_issued=weights[arm],
            initial_gram=state.initial_gram,
            initial_rhs=state.initial_rhs,
            penalty=state.penalty,
            final_gram=state.gram,
            final_rhs=state.rhs,
            final_beta=state.beta,
            final_effective_weight=state.effective_weight,
            updates=state.updates,
            last_target=state.last_target,
        )
    rows = []
    for name, pred in records.items():
        np.savez_compressed(out / f"{name}.npz", **pred)
        frame = score_predictions(pred, stream.history, prob["levels"])
        if name == "B_RAW":
            for key in frame:
                if key not in ("horizon", "point", "n", "mae", "rmse"):
                    frame[key] = np.nan
        frame.insert(0, "model", name)
        rows.append(frame)
    metrics = pd.concat(rows, ignore_index=True)
    summary = aggregate(metrics)
    metrics.to_csv(out / "metrics.csv", index=False, float_format="%.12g")
    summary.to_csv(out / "summary.csv", index=False, float_format="%.12g")
    save_json(
        out / "feedback_state.json",
        {name: cal.feedback_summary() for name, cal in calibrators.items()},
    )
    decision = gate(metrics, summary, spec["candidate"], spec["simple_baseline"], spec)
    decision.update(
        online_point_solves=sum(state.point_solves for state in states.values()),
        new_neural_updates=0,
        new_scale_optimizations=0,
        physical_calls=0,
        first_origin_matches_c8=True,
        post_exposure=True,
        independent_transfer=False,
    )
    save_json(out / "decision.json", decision)
    recorder.event("phase_completed", phase=phase, decision=decision)
    print(
        json.dumps(dict(phase=phase, decision=decision), ensure_ascii=False), flush=True
    )
    return decision


def execute(spec, config, out):
    if (
        sha(ROOT / spec["data"]) != spec["data_sha256"]
        or spec["new_scale_optimizations_permitted"]
    ):
        raise ValueError("Frozen data or scale rule changed")
    if (
        not spec["post_transfer_exposure"]
        or spec["original_single_transfer_reused"]
        or not spec["fixed_ridge_penalty"]
    ):
        raise ValueError("Study boundaries changed")
    if spec["forgetting_factor"] != math.exp(
        -1 / spec["forgetting_e_folding_mature_updates"]
    ):
        raise ValueError("Forgetting factor differs from frozen definition")
    verification_path = ROOT / spec["prior_verification"]
    if (
        sha(verification_path) != spec["prior_verification_sha256"]
        or not json.loads(verification_path.read_text())["passed"]
    ):
        raise ValueError("Original causal feature verification changed")
    sources = [
        Path(config).resolve(),
        ROOT / spec["plan"],
        ROOT / spec["data"],
        verification_path,
        ROOT / "code/physics_guided/reference.py",
    ]
    sources += list((ROOT / "code/rolling_probability").glob("*.py")) + list(
        (ROOT / "tests").glob("test_rolling_*.py")
    )
    loaded = {}
    for phase, ref in spec["sources"].items():
        root = checked_run(ref["current_run"], ref["current_manifest_sha256"])
        current, paths = {}, []
        for p in sorted((root / ref["current_phase"]).glob("*.npz")):
            value = arrays(p)
            if {"mean", "sigma", "raw_sigma", "origins"} <= value.keys():
                current[p.stem] = value
                paths.append(p)
        if (
            not {"C8_ONLINE_FULL", "C8_ONLINE_DATA", "B_ANCHOR", "DRIFT1"}
            <= current.keys()
        ):
            raise ValueError("Missing C8 comparisons")
        train_path, issued_path = (
            ROOT / ref["initial_training"],
            ROOT / ref["issued_features"],
        )
        models, model_paths = {}, []
        for arm in ("FULL", "DATA"):
            p = ROOT / ref["original_mean_training_path"] / f"ridge_{arm}_a0.001.json"
            models[arm] = RidgeDistribution.load(p)
            model_paths.append(p)
        loaded[phase] = current, arrays(train_path), arrays(issued_path), models
        sources += (
            paths
            + model_paths
            + [train_path, issued_path, root / "artifact_manifest.json"]
        )
    source_hashes = {str(p.relative_to(ROOT)): sha(p) for p in sources}
    for p in sources:
        if p.suffix in (".py", ".json", ".md"):
            q = out / "sources" / p.relative_to(ROOT)
            q.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, q)
    save_json(
        out / "sources.json",
        dict(
            git_head=subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            files=source_hashes,
        ),
    )
    recorder = Recorder(out)
    decisions = {
        phase: phase_run(spec, phase, *values, out / phase, recorder)
        for phase, values in loaded.items()
    }
    count = sum(d["online_point_solves"] for d in decisions.values())
    if count != spec["expected_online_point_solves"]:
        raise ValueError("Wrong online update count")
    save_json(
        out / "decision.json",
        dict(
            candidate=spec["candidate"],
            phases=decisions,
            online_point_solves=count,
            new_neural_updates=0,
            new_scale_optimizations=0,
            physical_calls=0,
            independent_transfer=False,
        ),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    spec = json.loads(args.config.read_text())
    remaining = (
        datetime.fromisoformat(spec["deadline_utc"]) - datetime.now(timezone.utc)
    ).total_seconds()
    if remaining <= 0:
        raise RuntimeError("Original autonomous deadline expired")
    args.out.mkdir(parents=True, exist_ok=False)
    status = dict(
        state="running",
        pid=os.getpid(),
        started_utc=utc(),
        deadline=spec["deadline_utc"],
    )
    save_json(args.out / "status.json", status)
    started = time.monotonic()

    def timeout(_signum, _frame):
        raise TimeoutError("Candidate or original total deadline reached")

    signal.signal(signal.SIGALRM, timeout)
    signal.setitimer(signal.ITIMER_REAL, min(remaining, spec["run_timeout_seconds"]))
    try:
        execute(spec, args.config, args.out)
        status.update(state="completed", exit_code=0)
    except BaseException as error:
        status.update(state="failed", exit_code=1, error=repr(error))
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        status.update(finished_utc=utc(), elapsed_seconds=time.monotonic() - started)
        save_json(args.out / "status.json", status)
        save_json(
            args.out / "artifact_manifest.json",
            dict(
                files={
                    str(p.relative_to(args.out)): sha(p)
                    for p in sorted(args.out.rglob("*"))
                    if p.is_file() and p != args.out / "artifact_manifest.json"
                }
            ),
        )


if __name__ == "__main__":
    main()
