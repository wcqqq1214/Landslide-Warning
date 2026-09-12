"""One authorized four-point run. Existing output or expired budget prohibits fitting."""

import os

for variable in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
):
    os.environ[variable] = "1"

import copy
from datetime import datetime, timezone
import json
import signal
import subprocess
import time
import traceback
import warnings

import joblib
import numpy as np
from threadpoolctl import threadpool_info, threadpool_limits

from physics_guided_gp import (
    CONFIG,
    POINTS,
    ROOT,
    BoundedOptimizer,
    check_historical_scores,
    decide,
    load_reference,
    new_gp,
    predict,
    prepare_inputs,
    raw_features,
    read_labels,
    score_distributions,
    sha,
    specification,
    write_json,
)

IMPLEMENTATION = (
    "code/physics_guided_gp.py",
    "code/run_ootang_bplus_gp.py",
    "scripts/verify_ootang_bplus_gp.py",
    "tests/test_physics_guided_gp.py",
)


def now():
    return datetime.now(timezone.utc)


def committed_snapshot():
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    import hashlib

    paths = (*IMPLEMENTATION, str(CONFIG.relative_to(ROOT)))
    for path in paths:
        frozen = subprocess.check_output(["git", "show", f"{head}:{path}"], cwd=ROOT)
        if hashlib.sha256(frozen).hexdigest() != sha(ROOT / path):
            raise ValueError(
                "Commit implementation/configuration before the single formal run"
            )
    return dict(commit=head, files={path: sha(ROOT / path) for path in paths})


def main():
    spec = specification()
    snapshot = committed_snapshot()
    if now() >= datetime.fromisoformat(spec["implementation_deadline_utc"]):
        raise RuntimeError("Implementation deadline has expired; do not start")
    out = ROOT / spec["output_dir"]
    out.mkdir(parents=True, exist_ok=False)
    started, monotonic_start = now(), time.monotonic()
    run_seconds = min(
        spec["run_limit_seconds"],
        (
            datetime.fromisoformat(spec["training_deadline_utc"]) - started
        ).total_seconds(),
    )
    state = dict(
        status="running",
        started_utc=started.isoformat(),
        fit_calls=0,
        completed_points=[],
        objective_calls=0,
        physical_solver_calls=0,
        formal_startups=1,
        automatic_retries=0,
        final_window_labels_read=0,
        displacement_prefixes_read=[],
        optimizer_records={},
        run_limit_seconds=run_seconds,
    )
    events = out / "events.jsonl"

    def event(kind, data=None):
        with events.open("a") as stream:
            stream.write(
                json.dumps(
                    dict(utc=now().isoformat(), kind=kind, data=data or {}),
                    ensure_ascii=False,
                    allow_nan=False,
                )
                + "\n"
            )

    def check():
        if time.monotonic() - monotonic_start >= run_seconds:
            raise TimeoutError("Fixed formal-run deadline reached")

    def alarm_handler(signum, frame):
        raise TimeoutError("Fixed formal-run deadline reached")

    signal.signal(signal.SIGALRM, alarm_handler)
    signal.setitimer(signal.ITIMER_REAL, run_seconds)
    try:
        (out / "config.json").write_bytes(CONFIG.read_bytes())
        write_json(
            out / "source_snapshot.json",
            dict(
                **snapshot,
                config_sha256=sha(CONFIG),
                frozen_sources=spec["source_sha256"],
                versions=spec["versions"],
                scientific_baseline_commit=spec["scientific_baseline_commit"],
            ),
        )
        event("formal_run_started", dict(run_seconds=run_seconds))
        with threadpool_limits(limits=1):
            write_json(out / "threadpools.json", threadpool_info())
            saved, old, audit = load_reference(spec)
            write_json(out / "reference_audit.json", audit)
            labels = read_labels(ROOT / spec["data"], spec["fit_days"])
            state["displacement_prefixes_read"].append(792)
            event("fit_labels_read", dict(rows=792))
            inputs = prepare_inputs(raw_features(saved), saved["mean"], labels, spec)
            write_json(out / "normalizers.json", inputs.normalizers)
            np.savez_compressed(
                out / "inputs.npz", raw=inputs.raw, x=inputs.x, targets=inputs.targets
            )
            predictions = []
            for j, point in enumerate(POINTS):
                check()

                def record(kind, data, point=point):
                    if kind == "objective_requested":
                        state["objective_calls"] += 1
                        if state["objective_calls"] > spec["max_total_objective_calls"]:
                            raise RuntimeError("Total likelihood call limit exceeded")
                    event(kind, dict(station=point, **data))

                optimizer = BoundedOptimizer(spec, check=check, record=record)
                model = new_gp(spec, optimizer)
                state["fit_calls"] += 1
                if state["fit_calls"] > spec["max_fit_calls"]:
                    raise RuntimeError("Fit call limit exceeded")
                event(
                    "fit_started",
                    dict(
                        station=point,
                        initial_log_theta=model.kernel.theta.tolist(),
                        bounds=model.kernel.bounds.tolist(),
                        rows=762,
                    ),
                )
                try:
                    with warnings.catch_warnings(record=True) as captured:
                        warnings.simplefilter("always")
                        model.fit(inputs.x[30:792], inputs.targets[:, j])
                    check()
                    prediction, variance_audit = predict(
                        model,
                        inputs.x,
                        saved["mean"][:, j],
                        inputs.normalizers["residual_denominator_mm"][j],
                        spec,
                    )
                    # The optimized fitted state is unchanged; remove only the live logging callable.
                    inference_model = copy.copy(model)
                    inference_model.optimizer = None
                    joblib.dump(inference_model, out / f"{point}.joblib", compress=3)
                    record_value = dict(
                        optimizer=optimizer.result,
                        variance=variance_audit,
                        warnings=[str(w.message) for w in captured],
                        serialized_optimizer="None; inference-only fitted state",
                        log_marginal_likelihood=float(
                            model.log_marginal_likelihood_value_
                        ),
                    )
                    write_json(out / f"{point}_audit.json", record_value)
                    state["optimizer_records"][point] = record_value
                    state["completed_points"].append(point)
                    predictions.append(prediction)
                    event(
                        "point_completed",
                        dict(
                            station=point,
                            evaluations=optimizer.calls,
                            iterations=optimizer.iterations,
                        ),
                    )
                    print(
                        f"{point}: fitted and saved; {optimizer.calls} evaluations",
                        flush=True,
                    )
                except BaseException:
                    state["optimizer_records"][point] = dict(
                        optimizer=optimizer.result,
                        attempted_calls=optimizer.calls,
                        iterations=optimizer.iterations,
                        invocations=optimizer.invocations,
                    )
                    raise
            prediction = {
                key: np.column_stack([p[key] for p in predictions])
                for key in predictions[0]
            }
            np.savez_compressed(
                out / "predictions.npz", dates=saved["dates"], **prediction
            )
            locked_paths = [
                "normalizers.json",
                "inputs.npz",
                "predictions.npz",
                *[f"{p}.joblib" for p in POINTS],
                *[f"{p}_audit.json" for p in POINTS],
            ]
            lock = dict(
                locked_utc=now().isoformat(),
                points=list(POINTS),
                files={name: sha(out / name) for name in locked_paths},
            )
            write_json(out / "prediction_lock.json", lock)
            event(
                "all_predictions_locked",
                dict(lock_sha256=sha(out / "prediction_lock.json")),
            )
            check()
            labels = read_labels(ROOT / spec["data"], spec["end_days"])
            state["displacement_prefixes_read"].append(1168)
            event("development_labels_read", dict(rows=1168, prediction_rows=376))
            metrics, aggregate, daily = score_distributions(
                saved, old, prediction, labels
            )
            metrics.to_csv(out / "metrics.csv", index=False)
            aggregate.to_csv(out / "aggregate.csv", index=False)
            daily.to_csv(out / "daily_predictions.csv", index=False)
            historical = check_historical_scores(metrics, aggregate, spec)
            write_json(out / "historical_score_check.json", historical)
            decision = decide(metrics, spec)
            write_json(out / "decision.json", decision)
            state["status"] = "completed_pending_independent_verification"
            state["effect_passed_provisional"] = decision["effect_passed"]
            event(
                "scoring_completed",
                dict(
                    metrics_rows=len(metrics),
                    daily_rows=len(daily),
                    effect_passed=decision["effect_passed"],
                ),
            )
            check()
    except BaseException as error:
        state["status"] = "stopped_incomplete"
        state["error"] = dict(type=type(error).__name__, message=str(error))
        (out / "failure.txt").write_text(traceback.format_exc())
        event("stopped_incomplete", state["error"])
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        state["ended_utc"] = now().isoformat()
        state["elapsed_seconds"] = time.monotonic() - monotonic_start
        state["next_action"] = (
            "verify/report only; no retry, additional fit or final-window training"
        )
        write_json(out / "run_status.json", state)
        files = {
            str(p.relative_to(out)): dict(sha256=sha(p), bytes=p.stat().st_size)
            for p in sorted(out.rglob("*"))
            if p.is_file()
        }
        write_json(out / "artifact_manifest.json", dict(files=files))
    print(json.dumps(state, ensure_ascii=False, indent=2), flush=True)
    return 0 if state["status"] == "completed_pending_independent_verification" else 1


if __name__ == "__main__":
    raise SystemExit(main())
