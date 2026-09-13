"""Reapply the original causal calibration to frozen corrected means."""

import argparse
from datetime import datetime, timezone
import json
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
from .scoring import CausalCalibration, aggregate, gate, score_predictions


def phase_run(spec, phase, current, out, recorder):
    start, end = spec["stages"][phase]
    H, N = spec["horizons"], end - start
    stream = ObservationStream(ROOT / spec["data"], start, end)
    prob = spec["probability"]
    records = {
        name: {k: v.copy() for k, v in pred.items()} for name, pred in current.items()
    }
    calibrators = {}
    out.mkdir(parents=True)
    for name, source in spec["calibration_sources"].items():
        pred = current[source]
        np.testing.assert_array_equal(pred["origins"], np.arange(start, end))
        records[name] = {
            k: pred[k].copy()
            for k in ("origins", "teacher_prefixes", "mean", "raw_sigma")
        }
        for key in ("sigma", "calibration_factor", "feedback_log_scale"):
            records[name][key] = np.full((N, H, 4), np.nan)
        calibrators[name] = CausalCalibration(
            H,
            prob["window"],
            prob["prior_count"],
            prob["prior_sum_squares"],
            feedback=prob["feedback"],
        )
    recorder.event(
        "phase_started",
        phase=phase,
        training_prefix=start,
        end=end,
        models=list(records),
    )
    for i, origin in enumerate(range(start, end)):
        valid = min(H, end - origin)
        for name, cal in calibrators.items():
            pred = records[name]
            factor = cal.factors(origin)[:valid]
            sigma = np.maximum(
                prob["sigma_floor_mm"], pred["raw_sigma"][i, :valid] * factor
            )
            pred["sigma"][i, :valid] = sigma
            pred["calibration_factor"][i, :valid] = factor
            pred["feedback_log_scale"][i, :valid] = cal.log_scale[:valid]
        recorder.event(
            "forecast_locked",
            phase=phase,
            origin=origin,
            history_sha256=array_sha(stream.history),
            predictions={
                name: array_sha(
                    np.stack([p[k][i, :valid] for k in ("mean", "sigma", "raw_sigma")])
                )
                for name, p in records.items()
            },
        )
        observed = stream.release()
        recorder.event(
            "observation_released",
            phase=phase,
            index=origin,
            value_sha256=array_sha(observed),
        )
        for k in range(min(H, i + 1)):
            issued = i - k
            for name, cal in calibrators.items():
                pred = records[name]
                cal.update(
                    k,
                    observed - pred["mean"][issued, k],
                    pred["raw_sigma"][issued, k],
                    origin,
                    origin + 1,
                    issued_sigma=pred["sigma"][issued, k],
                )
    identity = spec["identity_control"]
    original = current[spec["calibration_sources"][identity]]
    for key in (
        "mean",
        "raw_sigma",
        "sigma",
        "calibration_factor",
        "feedback_log_scale",
    ):
        np.testing.assert_array_equal(records[identity][key], original[key])
    frames = []
    for name, pred in records.items():
        frame = score_predictions(pred, stream.history, prob["levels"])
        if name == "B_RAW":
            for col in frame:
                if col not in ("horizon", "point", "n", "mae", "rmse"):
                    frame[col] = np.nan
        frame.insert(0, "model", name)
        frames.append(frame)
        np.savez_compressed(out / (name + ".npz"), **pred)
    metrics = pd.concat(frames, ignore_index=True)
    summary = aggregate(metrics)
    metrics.to_csv(out / "metrics.csv", index=False, float_format="%.12g")
    summary.to_csv(out / "summary.csv", index=False, float_format="%.12g")
    states = {name: cal.feedback_summary() for name, cal in calibrators.items()}
    save_json(out / "feedback_state.json", states)
    count = 4 * sum(int(cal.feedback_updates.sum()) for cal in calibrators.values())
    expected = 4 * len(calibrators) * sum(N - h + 1 for h in range(1, H + 1))
    if count != expected:
        raise ValueError("Calibration maturity count differs")
    decision = gate(metrics, summary, spec["candidate"], spec["simple_baseline"], spec)
    decision.update(
        calibration_point_updates=count,
        new_mean_fits=0,
        new_scale_optimizations=0,
        neural_updates=0,
        physical_calls=0,
        identity_control_exact=True,
        means_and_raw_scales_unchanged=True,
        post_exposure=True,
        independent_transfer=False,
    )
    save_json(out / "decision.json", decision)
    recorder.event("phase_completed", phase=phase, decision=decision)
    print(
        json.dumps(
            dict(
                phase=phase,
                passed=decision["passed"],
                passed_count=decision["passed_count"],
                calibration_point_updates=count,
            )
        ),
        flush=True,
    )
    return decision


def execute(spec, config, out):
    if (
        sha(ROOT / spec["data"]) != spec["data_sha256"]
        or not spec["post_transfer_exposure"]
        or spec["original_single_transfer_reused"]
        or spec["uncertainty"] != "own_issued_error_calibration"
    ):
        raise ValueError("Changed input or exposure rule")
    prior = ROOT / spec["prior_verification"]
    if (
        sha(prior) != spec["prior_verification_sha256"]
        or not json.loads(prior.read_text())["passed"]
    ):
        raise ValueError("Changed prior verification")
    sources = [
        Path(config).resolve(),
        ROOT / spec["plan"],
        ROOT / spec["data"],
        prior,
        ROOT / "code/physics_guided/reference.py",
    ]
    sources += list((ROOT / "code/rolling_probability").glob("*.py"))
    loaded = {}
    for phase, ref in spec["sources"].items():
        root = checked_run(ref["current_run"], ref["current_manifest_sha256"])
        loaded[phase] = {}
        for path in sorted((root / ref["current_phase"]).glob("*.npz")):
            record = arrays(path)
            if {"origins", "mean", "sigma", "raw_sigma"} <= record.keys():
                loaded[phase][path.stem] = record
                sources.append(path)
        if not set(spec["calibration_sources"].values()) <= loaded[phase].keys():
            raise ValueError("Missing frozen mean")
        sources.append(root / "artifact_manifest.json")
    hashes = {str(p.relative_to(ROOT)): sha(p) for p in sources}
    for path in sources:
        if path.suffix in (".json", ".md", ".py"):
            target = out / "sources" / path.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    save_json(
        out / "sources.json",
        dict(
            files=hashes,
            git_head=subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
        ),
    )
    recorder = Recorder(out)
    decisions = {
        phase: phase_run(spec, phase, current, out / phase, recorder)
        for phase, current in loaded.items()
    }
    count = sum(d["calibration_point_updates"] for d in decisions.values())
    if count != spec["expected_calibration_point_updates"]:
        raise ValueError("Total calibration count differs")
    save_json(
        out / "decision.json",
        dict(
            candidate=spec["candidate"],
            phases=decisions,
            calibration_point_updates=count,
            new_mean_fits=0,
            new_scale_optimizations=0,
            neural_updates=0,
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
    deadline = min(
        datetime.fromisoformat(spec["deadline_utc"]),
        datetime.fromisoformat(spec["candidate_deadline_utc"]),
    )
    remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
    if remaining <= 0:
        raise TimeoutError("Authorized deadline expired")
    args.out.mkdir(parents=True, exist_ok=False)
    status = dict(
        state="running",
        pid=os.getpid(),
        started_utc=utc(),
        deadline=deadline.isoformat(),
    )
    save_json(args.out / "status.json", status)
    started = time.monotonic()

    def timeout(_signum, _frame):
        raise TimeoutError("Candidate or total deadline reached")

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
