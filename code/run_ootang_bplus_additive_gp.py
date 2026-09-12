"""Single authorized eight-fit run; immutable sources and labels-after-lock scoring."""

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
import hashlib
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
    BoundedOptimizer,
    prepare_inputs,
    raw_features,
    read_labels,
    sha,
    write_json,
)
from physics_guided_additive_gp import (
    ARMS,
    CONFIG,
    POINTS,
    ROOT,
    check_saved_scores,
    comparisons,
    decide,
    load_sources,
    new_gp,
    predict,
    score_all,
    specification,
)

IMPLEMENTATION = (
    "code/physics_guided_additive_gp.py",
    "code/run_ootang_bplus_additive_gp.py",
    "scripts/verify_ootang_bplus_additive_gp.py",
    "tests/test_physics_guided_additive_gp.py",
)


def now():
    return datetime.now(timezone.utc)


def snapshot():
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    paths = (*IMPLEMENTATION, str(CONFIG.relative_to(ROOT)))
    for path in paths:
        data = subprocess.check_output(["git", "show", f"{head}:{path}"], cwd=ROOT)
        if hashlib.sha256(data).hexdigest() != sha(ROOT / path):
            raise ValueError("Commit exact implementation before formal run")
    return dict(commit=head, files={p: sha(ROOT / p) for p in paths})


def main():
    spec = specification()
    source = snapshot()
    if now() >= datetime.fromisoformat(spec["implementation_deadline_utc"]):
        raise RuntimeError("Preparation deadline expired; no shortened run")
    out = ROOT / spec["output_dir"]
    out.mkdir(parents=True, exist_ok=False)
    started, mono = now(), time.monotonic()
    limit = min(
        spec["run_limit_seconds"],
        (
            datetime.fromisoformat(spec["training_deadline_utc"]) - started
        ).total_seconds(),
    )
    state = dict(
        status="running",
        started_utc=started.isoformat(),
        run_limit_seconds=limit,
        fit_calls=0,
        objective_calls=0,
        iterations=0,
        completed_models=[],
        optimizer_records={},
        physical_solver_calls=0,
        historical_fit_calls=0,
        final_window_labels_read=0,
        displacement_prefixes_read=[],
        formal_startups=1,
        automatic_retries=0,
    )

    def event(kind, data=None):
        with (out / "events.jsonl").open("a") as f:
            f.write(
                json.dumps(
                    dict(utc=now().isoformat(), kind=kind, data=data or {}),
                    allow_nan=False,
                )
                + "\n"
            )

    def check():
        if time.monotonic() - mono >= limit:
            raise TimeoutError("Formal run time limit reached")

    def alarm(signum, frame):
        raise TimeoutError("Formal run time limit reached")

    previous = signal.signal(signal.SIGALRM, alarm)
    signal.setitimer(signal.ITIMER_REAL, limit)
    try:
        (out / "config.json").write_bytes(CONFIG.read_bytes())
        write_json(
            out / "source_snapshot.json",
            dict(
                **source,
                config_sha256=sha(CONFIG),
                frozen_sources=spec["source_sha256"],
            ),
        )
        event("formal_run_started")
        with threadpool_limits(limits=1):
            write_json(out / "threadpools.json", threadpool_info())
            saved, old, historical, audit = load_sources(spec)
            write_json(out / "reference_audit.json", audit)
            labels = read_labels(ROOT / spec["data"], 792)
            state["displacement_prefixes_read"].append(792)
            event("fit_labels_read", dict(rows=792))
            inputs = prepare_inputs(raw_features(saved), saved["mean"], labels, spec)
            previous_norm = json.loads(
                (ROOT / spec["old_gp_dir"] / "normalizers.json").read_text()
            )
            if inputs.normalizers != previous_norm:
                raise AssertionError("v1 training-only normalization changed")
            write_json(out / "normalizers.json", inputs.normalizers)
            np.savez_compressed(
                out / "inputs.npz", raw=inputs.raw, x=inputs.x, targets=inputs.targets
            )
            per_arm = {arm: [] for arm in ARMS}
            for j, point in enumerate(POINTS):
                for arm in ARMS:
                    check()
                    model_id = point + "_" + arm

                    def record(kind, data, model_id=model_id):
                        if kind == "objective_requested":
                            state["objective_calls"] += 1
                            if (
                                state["objective_calls"]
                                > spec["max_total_objective_calls"]
                            ):
                                raise RuntimeError("Total objective limit reached")
                        if kind == "iteration":
                            state["iterations"] += 1
                            if state["iterations"] > spec["max_total_iterations"]:
                                raise RuntimeError("Total iteration limit reached")
                        event(kind, dict(model_id=model_id, **data))

                    optimizer = BoundedOptimizer(spec, check=check, record=record)
                    model = new_gp(spec, arm, optimizer)
                    if len(model.kernel.theta) != spec["hyperparameter_counts"][arm]:
                        raise AssertionError("Wrong group hyperparameter count")
                    state["fit_calls"] += 1
                    if state["fit_calls"] > spec["max_fit_calls"]:
                        raise RuntimeError("Fit count exceeded")
                    event(
                        "fit_started",
                        dict(
                            model_id=model_id,
                            initial_log_theta=model.kernel.theta.tolist(),
                            bounds=model.kernel.bounds.tolist(),
                            rows=762,
                        ),
                    )
                    captured = []
                    try:
                        with warnings.catch_warnings(record=True) as captured:
                            warnings.simplefilter("always")
                            model.fit(inputs.x[30:792], inputs.targets[:, j])
                            check()
                            prediction, variance = predict(
                                model,
                                arm,
                                inputs.x,
                                saved["mean"][:, j],
                                inputs.normalizers["residual_denominator_mm"][j],
                                spec,
                            )
                        inference = copy.copy(model)
                        inference.optimizer = None
                        joblib.dump(inference, out / (model_id + ".joblib"), compress=3)
                        record_value = dict(
                            optimizer=optimizer.result,
                            variance=variance,
                            warnings=[
                                dict(
                                    category=w.category.__name__, message=str(w.message)
                                )
                                for w in captured
                            ],
                            serialized_optimizer="None; optimized fitted inference state retained",
                            log_marginal_likelihood=float(
                                model.log_marginal_likelihood_value_
                            ),
                        )
                        write_json(out / (model_id + "_audit.json"), record_value)
                        # Preserve each complete prediction even if a later fit stops the whole run.
                        np.savez_compressed(
                            out / (model_id + "_prediction.npz"),
                            dates=saved["dates"],
                            **prediction,
                        )
                        state["optimizer_records"][model_id] = record_value
                        state["completed_models"].append(model_id)
                        per_arm[arm].append(prediction)
                        event(
                            "model_completed",
                            dict(
                                model_id=model_id,
                                evaluations=optimizer.calls,
                                iterations=optimizer.iterations,
                            ),
                        )
                        print(
                            f"{model_id}: saved; {optimizer.calls} objective requests",
                            flush=True,
                        )
                    except BaseException:
                        state["optimizer_records"][model_id] = dict(
                            optimizer=optimizer.result,
                            attempted_calls=optimizer.calls,
                            iterations=optimizer.iterations,
                            warnings=[
                                dict(
                                    category=w.category.__name__, message=str(w.message)
                                )
                                for w in captured
                            ],
                        )
                        raise
            predictions = {}
            for arm in ARMS:
                predictions[arm] = {
                    key: np.column_stack([p[key] for p in per_arm[arm]])
                    for key in per_arm[arm][0]
                }
                np.savez_compressed(
                    out / (arm + "_predictions.npz"),
                    dates=saved["dates"],
                    **predictions[arm],
                )
            names = [
                "inputs.npz",
                "normalizers.json",
                *[a + "_predictions.npz" for a in ARMS],
            ]
            for model_id in state["completed_models"]:
                names.extend(
                    [
                        model_id + ".joblib",
                        model_id + "_audit.json",
                        model_id + "_prediction.npz",
                    ]
                )
            lock = dict(
                locked_utc=now().isoformat(),
                models=state["completed_models"],
                files={p: sha(out / p) for p in names},
            )
            write_json(out / "prediction_lock.json", lock)
            event(
                "all_predictions_locked", dict(sha256=sha(out / "prediction_lock.json"))
            )
            check()
            labels = read_labels(ROOT / spec["data"], 1168)
            state["displacement_prefixes_read"].append(1168)
            event("development_labels_read", dict(rows=1168, prediction_rows=376))
            tables = score_all(saved, old, historical, predictions, labels)
            for name, frame in zip(
                ("metrics.csv", "aggregate.csv", "daily_predictions.csv"), tables
            ):
                frame.to_csv(out / name, index=False)
            checks = check_saved_scores(tables, spec)
            write_json(out / "historical_score_check.json", checks)
            comparisons(tables[0]).to_csv(out / "comparisons.csv", index=False)
            decision = decide(tables[0], spec)
            write_json(out / "decision.json", decision)
            check()
            state["status"] = "completed_pending_independent_verification"
            state["effect_passed_provisional"] = decision["effect_passed"]
            event(
                "scoring_completed",
                dict(
                    metric_rows=len(tables[0]),
                    daily_rows=len(tables[2]),
                    effect_passed=decision["effect_passed"],
                ),
            )
    except BaseException as error:
        state["status"] = "stopped_incomplete"
        state["error"] = dict(type=type(error).__name__, message=str(error))
        (out / "failure.txt").write_text(traceback.format_exc())
        event("stopped_incomplete", state["error"])
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        state["ended_utc"] = now().isoformat()
        state["elapsed_seconds"] = time.monotonic() - mono
        state["next_action"] = "verify/report only; no retries or final-window training"
        write_json(out / "run_status.json", state)
        write_json(
            out / "artifact_manifest.json",
            dict(
                files={
                    str(p.relative_to(out)): dict(sha256=sha(p), bytes=p.stat().st_size)
                    for p in sorted(out.rglob("*"))
                    if p.is_file()
                }
            ),
        )
    print(
        json.dumps(
            {k: v for k, v in state.items() if k != "optimizer_records"}, indent=2
        ),
        flush=True,
    )
    return 0 if state["status"] == "completed_pending_independent_verification" else 1


if __name__ == "__main__":
    raise SystemExit(main())
