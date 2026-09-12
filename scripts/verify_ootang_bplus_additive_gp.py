"""Verify frozen ADD/TIME_ONLY outputs without fitting, optimizing or physical calls."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import warnings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))

import joblib
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from physics_guided_gp import (
    check_sources,
    prepare_inputs,
    raw_features,
    read_labels,
    read_npz,
    sha,
    write_json,
)
from physics_guided_additive_gp import (
    ARMS,
    CONFIG,
    POINTS,
    ROOT,
    check_saved_scores,
    compare_frame,
    comparisons,
    components,
    decide,
    independent_posterior,
    kernel,
    load_sources,
    predict,
    score_all,
    specification,
)


def verify():
    spec = specification()
    out = ROOT / spec["output_dir"]
    manifest = json.loads((out / "artifact_manifest.json").read_text())["files"]
    if set(manifest) | {"artifact_manifest.json"} != {
        str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()
    }:
        raise AssertionError("Run artifact inventory changed")
    for name, record in manifest.items():
        if (
            sha(out / name) != record["sha256"]
            or (out / name).stat().st_size != record["bytes"]
        ):
            raise AssertionError("Raw artifact changed: " + name)
    source = json.loads((out / "source_snapshot.json").read_text())
    check_sources(source["files"])
    check_sources(source["frozen_sources"])
    if sha(out / "config.json") != sha(CONFIG) or source["config_sha256"] != sha(
        CONFIG
    ):
        raise AssertionError("Configuration snapshot changed")
    for path, digest in source["files"].items():
        committed = subprocess.check_output(
            ["git", "show", f"{source['commit']}:{path}"], cwd=ROOT
        )
        if hashlib.sha256(committed).hexdigest() != digest:
            raise AssertionError(
                "Implementation differs from committed pre-run snapshot"
            )
    state = json.loads((out / "run_status.json").read_text())
    events = [
        json.loads(line) for line in (out / "events.jsonl").read_text().splitlines()
    ]
    kinds = [e["kind"] for e in events]
    expected = [p + "_" + a for p in POINTS for a in ARMS]
    starts = [e for e in events if e["kind"] == "fit_started"]
    requests = [e for e in events if e["kind"] == "objective_requested"]
    iterations = [e for e in events if e["kind"] == "iteration"]
    complete = [e["data"]["model_id"] for e in events if e["kind"] == "model_completed"]
    if (
        state["fit_calls"] != len(starts)
        or state["objective_calls"] != len(requests)
        or state["iterations"] != len(iterations)
        or state["completed_models"] != complete
    ):
        raise AssertionError("Execution counts disagree")
    if [e["data"]["model_id"] for e in starts] != expected[
        : len(starts)
    ] or complete != expected[: len(complete)]:
        raise AssertionError("Point/arm order or retry differs")
    for model_id in expected:
        rs = [
            e["data"]["evaluation"]
            for e in requests
            if e["data"]["model_id"] == model_id
        ]
        it = [
            e["data"]["iteration"]
            for e in iterations
            if e["data"]["model_id"] == model_id
        ]
        if (
            rs != list(range(1, len(rs) + 1))
            or len(rs) > 400
            or it != list(range(1, len(it) + 1))
            or len(it) > 200
        ):
            raise AssertionError("Per-model optimizer budget/counts differ")
    for event in starts:
        arm = event["data"]["model_id"].split("_", 1)[1]
        original = kernel(spec, arm)
        np.testing.assert_array_equal(
            event["data"]["initial_log_theta"], original.theta
        )
        np.testing.assert_array_equal(event["data"]["bounds"], original.bounds)
        if event["data"]["rows"] != 762:
            raise AssertionError("Fit did not use registered prefix")
    if (
        state["fit_calls"] > 8
        or state["objective_calls"] > 3200
        or state["iterations"] > 1600
        or state["formal_startups"] != 1
        or state["automatic_retries"] != 0
        or state["physical_solver_calls"] != 0
        or state["historical_fit_calls"] != 0
        or state["final_window_labels_read"] != 0
    ):
        raise AssertionError("Execution scope/budget differs")
    start = datetime.fromisoformat(state["started_utc"])
    end = datetime.fromisoformat(state["ended_utc"])
    if start >= datetime.fromisoformat(spec["implementation_deadline_utc"]):
        raise AssertionError("Run started after preparation deadline")
    in_budget = state["elapsed_seconds"] <= 1800 and end <= datetime.fromisoformat(
        spec["training_deadline_utc"]
    )
    report = dict(
        verified_utc=datetime.now(timezone.utc).isoformat(),
        status=state["status"],
        sources_and_inventory_passed=True,
        fit_calls=state["fit_calls"],
        objective_calls=state["objective_calls"],
        iterations=state["iterations"],
        completed_models=complete,
        in_run_budget=in_budget,
        verification_fit_calls=0,
        physical_solver_calls=0,
        final_window_labels_read=0,
    )
    if state["status"] != "completed_pending_independent_verification":
        if "development_labels_read" in kinds and "all_predictions_locked" not in kinds:
            raise AssertionError("Development labels read before lock on failed path")
        report.update(
            complete_effectiveness_evaluation=False,
            effect_passed=False,
            error=state.get("error"),
            outcome="stopped incomplete; do not score or fill missing models",
        )
        return report, None, None
    if not in_budget or complete != expected or state["fit_calls"] != 8:
        raise AssertionError("Complete execution lacks eight models or exceeds budget")
    if state["displacement_prefixes_read"] != [792, 1168]:
        raise AssertionError("Displacement read sequence differs")
    if (
        kinds.count("fit_labels_read") != 1
        or kinds.count("all_predictions_locked") != 1
        or kinds.count("development_labels_read") != 1
    ):
        raise AssertionError("Missing or duplicate label/lock event")
    lock_index = kinds.index("all_predictions_locked")
    if not (
        kinds.index("fit_labels_read")
        < lock_index
        < kinds.index("development_labels_read")
        < kinds.index("scoring_completed")
    ):
        raise AssertionError("Label isolation failed")
    if any(
        i > lock_index
        for i, k in enumerate(kinds)
        if k in ("fit_started", "objective_requested", "model_completed")
    ):
        raise AssertionError("Models changed after lock")
    lock = json.loads((out / "prediction_lock.json").read_text())
    if lock["models"] != expected or events[lock_index]["data"]["sha256"] != sha(
        out / "prediction_lock.json"
    ):
        raise AssertionError("Locked model list/hash differs")
    for name, digest in lock["files"].items():
        if sha(out / name) != digest:
            raise AssertionError("Locked model/prediction changed")
    pools = json.loads((out / "threadpools.json").read_text())
    if any(p["num_threads"] != 1 for p in pools):
        raise AssertionError("Run did not record single-thread pools")
    saved, old, historical, audit = load_sources(spec)
    if audit != json.loads((out / "reference_audit.json").read_text()):
        raise AssertionError("Reference audit differs")
    labels = read_labels(ROOT / spec["data"], 792)
    inputs = prepare_inputs(raw_features(saved), saved["mean"], labels, spec)
    stored = read_npz(out / "inputs.npz")
    for name in ("raw", "x", "targets"):
        np.testing.assert_array_equal(stored[name], getattr(inputs, name))
    if inputs.normalizers != json.loads((out / "normalizers.json").read_text()):
        raise AssertionError("Train-only normalization differs")
    if inputs.normalizers != json.loads(
        (ROOT / spec["old_gp_dir"] / "normalizers.json").read_text()
    ):
        raise AssertionError("Normalizer differs from original GP task")
    predictions = {arm: read_npz(out / (arm + "_predictions.npz")) for arm in ARMS}
    for pred in predictions.values():
        np.testing.assert_array_equal(pred["dates"], saved["dates"])
    checks = []
    for j, point in enumerate(POINTS):
        for arm in ARMS:
            model_id = point + "_" + arm
            model = joblib.load(out / (model_id + ".joblib"))
            learned = json.loads((out / (model_id + "_audit.json")).read_text())
            if not learned["optimizer"]["success"] or model.optimizer is not None:
                raise AssertionError("Missing converged inference model")
            if (
                model.alpha != spec["jitter"]
                or model.normalize_y
                or model.n_restarts_optimizer
            ):
                raise AssertionError("Learned model configuration differs")
            if len(model.kernel_.theta) != spec["hyperparameter_counts"][arm]:
                raise AssertionError("Model parameter count differs")
            np.testing.assert_array_equal(
                model.kernel_.theta, learned["optimizer"]["log_theta"]
            )
            np.testing.assert_array_equal(model.X_train_, inputs.x[30:792])
            np.testing.assert_array_equal(model.y_train_, inputs.targets[:, j])
            kt, kh, _ = components(model, arm)
            if kt.active_dims != (0,) or (
                kh is not None and kh.active_dims != (1, 2, 3, 4)
            ):
                raise AssertionError("Model column mapping differs")
            if model.X_train_.dtype != np.float64 or model.y_train_.dtype != np.float64:
                raise AssertionError("Model dtype changed")
            scale = inputs.normalizers["residual_denominator_mm"][j]
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                reloaded, var = predict(
                    model, arm, inputs.x, saved["mean"][:, j], scale, spec
                )
            if var != learned["variance"]:
                raise AssertionError("Reload variance audit differs")
            single = read_npz(out / (model_id + "_prediction.npz"))
            np.testing.assert_array_equal(single["dates"], saved["dates"])
            reload_error = {}
            for name, value in reloaded.items():
                np.testing.assert_array_equal(
                    single[name], predictions[arm][name][:, j]
                )
                np.testing.assert_allclose(
                    value, single[name], rtol=0, atol=spec["reload_atol"]
                )
                reload_error[name] = float(abs(value - single[name]).max())
            independent = independent_posterior(
                inputs.x[30:792], inputs.targets[:, j], inputs.x, var, spec["jitter"]
            )
            for name in independent:
                independent[name] *= scale if name.endswith("mean") else scale**2
            independent["mean"] = saved["mean"][:, j] + independent["residual_mean"]
            matrix_error = {}
            for name, value in independent.items():
                if not np.isfinite(value).all():
                    raise AssertionError("Independent matrix result is nonfinite")
                np.testing.assert_allclose(
                    value,
                    single[name],
                    atol=spec["matrix_atol"],
                    rtol=spec["matrix_rtol"],
                )
                matrix_error[name] = float(abs(value - single[name]).max())
            np.testing.assert_allclose(
                single["latent_variance"],
                single["time_variance"]
                + single["physical_variance"]
                + 2 * single["cross_covariance"],
                atol=spec["matrix_atol"],
                rtol=spec["matrix_rtol"],
            )
            np.testing.assert_allclose(
                single["observation_variance"] - single["latent_variance"],
                single["noise_variance"],
                atol=spec["matrix_atol"],
                rtol=spec["matrix_rtol"],
            )
            if arm == "TIME_ONLY":
                for name in ("physical_mean", "physical_variance", "cross_covariance"):
                    np.testing.assert_array_equal(single[name], np.zeros(1168))
            checks.append(
                dict(
                    model_id=model_id,
                    reload_max_errors=reload_error,
                    independent_matrix_max_errors=matrix_error,
                    warnings=[
                        dict(category=w.category.__name__, message=str(w.message))
                        for w in caught
                    ],
                )
            )
    labels = read_labels(ROOT / spec["data"], 1168)
    tables = score_all(saved, old, historical, predictions, labels)
    scores = {
        name: compare_frame(frame, out / name, spec)
        for name, frame in zip(
            ("metrics.csv", "aggregate.csv", "daily_predictions.csv"), tables
        )
    }
    scores["comparisons.csv"] = compare_frame(
        comparisons(tables[0]), out / "comparisons.csv", spec
    )
    historical_check = check_saved_scores(tables, spec)
    if historical_check != json.loads(
        (out / "historical_score_check.json").read_text()
    ):
        raise AssertionError("Historical common-score audit differs")
    decision = decide(tables[0], spec)
    if decision != json.loads((out / "decision.json").read_text()):
        raise AssertionError("Frozen decision differs on saved predictions")
    report.update(
        complete_effectiveness_evaluation=True,
        numerical_checks_passed=True,
        prediction_lock_passed=True,
        effect_passed=decision["effect_passed"],
        model_numerics=checks,
        score_checks=scores,
        historical_scores=historical_check,
        outcome="stop; no control promotion, retries or final-window training",
    )
    return report, tables[2], predictions


def plot(daily, predictions, folder):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as dates
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {"font.size": 9, "axes.spines.top": False, "axes.spines.right": False}
    )
    for phase in ("train", "prediction"):
        fig, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
        for ax, point in zip(axes.flat, POINTS):
            part = daily[(daily.station == point) & (daily.part == phase)]
            for arm, color in (("TIME_ONLY", "#8562a4"), ("ADD", "#167795")):
                data = part[part.strategy == arm]
                x = pd.to_datetime(data.date)
                ax.fill_between(
                    x,
                    data.lower_90_mm,
                    data.upper_90_mm,
                    color=color,
                    alpha=0.15,
                    label=arm + " 90% observation interval",
                )
                ax.plot(
                    x, data.mean_mm, color=color, linewidth=1.2, label=arm + " mean"
                )
            for name, color, style in (
                ("M0", "#bf7926", "--"),
                ("BPLUS_GP_V1", "#777777", ":"),
            ):
                data = part[part.strategy == name]
                ax.plot(
                    pd.to_datetime(data.date),
                    data.mean_mm,
                    color=color,
                    linestyle=style,
                    linewidth=1,
                    label=name + " mean",
                )
            ax.plot(
                x,
                part[part.strategy == "ADD"].observed_mm,
                color="#222222",
                linewidth=1,
                label="Observed",
            )
            ax.set(title=point, ylabel="Cumulative displacement (mm)")
            ax.xaxis.set_major_locator(
                dates.MonthLocator(interval=6 if phase == "train" else 3)
            )
            ax.xaxis.set_major_formatter(dates.DateFormatter("%Y-%m"))
            ax.grid(alpha=0.15)
        handles, labels = axes.flat[0].get_legend_handles_labels()
        fig.legend(handles, labels, loc="outside lower center", ncol=3, frameon=False)
        fig.suptitle(f"Grouped B+ GP | complete {phase} window | no date exclusions")
        for suffix in ("png", "pdf"):
            fig.savefig(folder / (phase + "_curves." + suffix), dpi=160)
        plt.close(fig)
    fig, axes = plt.subplots(2, 2, figsize=(12, 7), constrained_layout=True)
    pred = predictions["ADD"]
    x = pd.to_datetime(pred["dates"][792:])
    for j, (ax, point) in enumerate(zip(axes.flat, POINTS)):
        for key, color in (
            ("time_mean", "#8562a4"),
            ("physical_mean", "#167795"),
            ("residual_mean", "#222222"),
        ):
            ax.plot(x, pred[key][792:, j], color=color, label=key)
        ax.axhline(0, color="#888888", linewidth=0.5)
        ax.set(title=point, ylabel="Residual correction (mm)")
        ax.xaxis.set_major_locator(dates.MonthLocator(interval=3))
        ax.xaxis.set_major_formatter(dates.DateFormatter("%Y-%m"))
        ax.grid(alpha=0.15)
    handles, labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="outside lower center", ncol=3, frameon=False)
    fig.suptitle(
        "ADD posterior mean components | model decomposition, not measured physical contributions"
    )
    for suffix in ("png", "pdf"):
        fig.savefig(folder / ("prediction_components." + suffix), dpi=160)
    plt.close(fig)


def main():
    with threadpool_limits(limits=1):
        report, daily, predictions = verify()
    folder = ROOT / specification()["verification_dir"]
    folder.mkdir(parents=True, exist_ok=False)
    write_json(folder / "verification.json", report)
    if daily is not None:
        plot(daily, predictions, folder)
    write_json(
        folder / "artifact_manifest.json",
        dict(
            verifier_sha256=sha(__file__),
            raw_manifest_sha256=sha(
                ROOT / specification()["output_dir"] / "artifact_manifest.json"
            ),
            files={
                p.name: dict(sha256=sha(p), bytes=p.stat().st_size)
                for p in sorted(folder.iterdir())
                if p.is_file()
            },
        ),
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
