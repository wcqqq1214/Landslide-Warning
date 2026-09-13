"""C9: separate conditional scale coefficients by lead, with frozen C8 means."""

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
from scipy.optimize import minimize

from physics_guided.reference import ROOT, save_json, sha
from .data import ObservationStream, array_sha
from .empirical import Recorder, checked_run, utc
from .ridge import scale_objective
from .scoring import CausalCalibration, aggregate, gate, score_predictions


def arrays(path):
    with np.load(path, allow_pickle=False) as z:
        return {k: z[k].copy() for k in z.files}


def initial_residuals(training, mean_state):
    d = mean_state["dimensions"]
    sx = np.array(mean_state["feature_scale"])
    sy = np.array(mean_state["target_scale"])
    beta = np.array(mean_state["beta"])
    f = training["features"]
    mu = (
        training["base"]
        + np.einsum("nhpd,hpd->nhp", f[..., :d] / sx[None], beta, optimize=False)
        * sy[None]
    )
    return (
        (training["target"] - mu) / sy[None],
        np.sqrt(np.mean(f[..., :4] ** 2, axis=-1)) / sy[None],
        sy,
    )


def fit_scale(training, mean_state, spec):
    residual, q, sy = initial_residuals(training, mean_state)
    H = residual.shape[1]
    logs = np.empty((H, 4, 2))
    records = []
    for h in range(H):
        for p in range(4):
            floor = spec["probability"]["sigma_floor_mm"] / sy[h, p]
            op = minimize(
                scale_objective,
                np.array(spec["scale_initial_logs"]),
                args=(residual[:, h, p], q[:, h, p], floor),
                jac=True,
                method="L-BFGS-B",
                bounds=[spec["scale_log_bounds"]] * 2,
                options=dict(
                    maxiter=spec["scale_maxiter"],
                    maxfun=spec["scale_maxfun"],
                    ftol=spec["scale_ftol"],
                    gtol=spec["scale_gtol"],
                ),
            )
            if not np.isfinite(op.x).all() or not np.isfinite(op.fun):
                raise ArithmeticError("Nonfinite horizon scale optimization")
            logs[h, p] = op.x
            records.append(
                dict(
                    horizon=h + 1,
                    point=p,
                    success=bool(op.success),
                    message=str(op.message),
                    iterations=int(op.nit),
                    function_calls=int(op.nfev),
                    objective=float(op.fun),
                    logs=op.x.tolist(),
                    gradient=op.jac.tolist(),
                )
            )
    return dict(
        family="horizon_conditional_scale",
        target_scale=sy.tolist(),
        scale_logs=logs.tolist(),
        sigma_floor_mm=spec["probability"]["sigma_floor_mm"],
        training_samples=len(residual),
        optimizer_records=records,
    )


def predict_scale(features, state):
    h = features.shape[-3]
    sy = np.array(state["target_scale"])[:h]
    ab = np.exp(np.array(state["scale_logs"])[:h])
    q = np.sqrt(np.mean(features[..., :4] ** 2, axis=-1))
    return np.sqrt(
        (ab[..., 0] * sy) ** 2 + (ab[..., 1] * q) ** 2 + state["sigma_floor_mm"] ** 2
    )


def phase_run(spec, phase, current, training, issued, models, out, recorder):
    start, end = spec["stages"][phase]
    H = spec["horizons"]
    prob = spec["probability"]
    stream = ObservationStream(ROOT / spec["data"], start, end)
    targets = training["origins"][:, None] + np.arange(H)[None, :]
    if targets.max() >= start:
        raise ValueError("Scale training targets cross the observed prefix")
    np.testing.assert_array_equal(training["target"], stream.history[targets])
    out.mkdir(parents=True)
    recorder.event(
        "training_prefix_read",
        phase=phase,
        rows=start,
        label_sha256=array_sha(stream.history),
    )
    scales = {}
    originals = {}
    for arm in ("FULL", "DATA"):
        scales[arm] = fit_scale(training, models[arm], spec)
        save_json(out / f"scale_{arm}.json", scales[arm])
        originals[arm] = current["C8_ONLINE_" + arm]
    save_json(
        out / "training_contract.json",
        dict(
            rows=start,
            max_target=int(targets.max()),
            training_queries=len(targets),
            label_sha256=array_sha(stream.history),
            mean_unchanged=True,
        ),
    )
    records = {
        (
            "C9_SHARED_" + name.rsplit("_", 1)[-1]
            if name.startswith("C8_ONLINE_")
            else name
        ): {k: v.copy() for k, v in value.items()}
        for name, value in current.items()
    }
    calibrators = {}
    for arm, original in originals.items():
        name = "C9_HORIZON_" + arm
        records[name] = {k: v.copy() for k, v in original.items()}
        for key in ("raw_sigma", "sigma", "calibration_factor", "feedback_log_scale"):
            records[name][key] = np.full_like(original[key], np.nan)
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
    for i, n in enumerate(range(start, end)):
        if len(stream.history) != n:
            raise ValueError("Future observation entered the calibration stage")
        valid = min(H, end - n)
        for arm, state in scales.items():
            name = "C9_HORIZON_" + arm
            r = records[name]
            cal = calibrators[name]
            raw = predict_scale(issued["features"][i, :valid], state)
            factors = cal.factors(n)[:valid]
            sd = np.maximum(prob["sigma_floor_mm"], raw * factors)
            if not np.isfinite(sd).all():
                raise ArithmeticError("Nonfinite issued scale")
            for key, value in dict(
                raw_sigma=raw,
                sigma=sd,
                calibration_factor=factors,
                feedback_log_scale=cal.log_scale[:valid],
            ).items():
                r[key][i, :valid] = value
        recorder.event(
            "forecast_locked",
            phase=phase,
            origin=n,
            history_sha256=array_sha(stream.history),
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
            for name, cal in calibrators.items():
                r = records[name]
                cal.update(
                    k,
                    observed - r["mean"][j, k],
                    r["raw_sigma"][j, k],
                    n,
                    n + 1,
                    issued_sigma=r["sigma"][j, k],
                )
    rows = []
    for name, r in records.items():
        if name.startswith("C9_HORIZON_"):
            np.testing.assert_array_equal(
                r["mean"], originals[name.rsplit("_", 1)[-1]]["mean"]
            )
        np.savez_compressed(out / f"{name}.npz", **r)
        frame = score_predictions(r, stream.history, prob["levels"])
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
    opts = [r for s in scales.values() for r in s["optimizer_records"]]
    decision.update(
        scale_optimizations=len(opts),
        scale_function_calls=sum(r["function_calls"] for r in opts),
        scale_iterations=sum(r["iterations"] for r in opts),
        all_scale_optimizers_converged=all(r["success"] for r in opts),
        new_mean_updates=0,
        physical_calls=0,
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
        or spec["new_mean_updates_permitted"]
    ):
        raise ValueError("Data or frozen mean rule changed")
    if not spec["post_transfer_exposure"] or spec["original_single_transfer_reused"]:
        raise ValueError("Exposure boundary changed")
    sources = [
        Path(config).resolve(),
        ROOT / spec["plan"],
        ROOT / spec["data"],
        ROOT / spec["pre_run_audit"],
        ROOT / "code/physics_guided/reference.py",
    ]
    sources += list((ROOT / "code/rolling_probability").glob("*.py")) + list(
        (ROOT / "tests").glob("test_rolling_*.py")
    )
    loaded = {}
    for phase, ref in spec["sources"].items():
        root = checked_run(ref["current_run"], ref["current_manifest_sha256"])
        current = {}
        paths = []
        for p in sorted((root / ref["current_phase"]).glob("*.npz")):
            value = arrays(p)
            if {"mean", "sigma", "raw_sigma", "origins"} <= value.keys():
                current[p.stem] = value
                paths.append(p)
        if (
            not {"C8_ONLINE_FULL", "C8_ONLINE_DATA", "B_ANCHOR", "DRIFT1"}
            <= current.keys()
        ):
            raise ValueError("Missing frozen comparisons")
        training = arrays(ROOT / ref["initial_training"])
        issued = arrays(ROOT / ref["issued_features"])
        model_paths = [
            ROOT / ref["original_mean_training_path"] / f"ridge_{arm}_a0.001.json"
            for arm in ("FULL", "DATA")
        ]
        models = {
            arm: json.loads(p.read_text())
            for arm, p in zip(("FULL", "DATA"), model_paths)
        }
        loaded[phase] = current, training, issued, models
        sources += (
            paths
            + model_paths
            + [
                root / "artifact_manifest.json",
                ROOT / ref["initial_training"],
                ROOT / ref["issued_features"],
            ]
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
    decisions = {}
    for phase, values in loaded.items():
        decisions[phase] = phase_run(spec, phase, *values, out / phase, recorder)
    counts = {
        key: sum(d[key] for d in decisions.values())
        for key in ("scale_optimizations", "scale_function_calls", "scale_iterations")
    }
    if counts["scale_optimizations"] != spec["expected_scale_fits"]:
        raise ValueError("Wrong number of scale optimizations")
    save_json(
        out / "decision.json",
        dict(
            candidate=spec["candidate"],
            phases=decisions,
            **counts,
            new_mean_updates=0,
            new_neural_updates=0,
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
