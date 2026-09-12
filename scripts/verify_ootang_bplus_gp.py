"""Read-only numerical verification: never calls fit, minimize or a physical solver."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))

import joblib
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from physics_guided_gp import (
    CONFIG,
    POINTS,
    ROOT,
    check_historical_scores,
    check_sources,
    decide,
    independent_posterior,
    load_reference,
    predict,
    prepare_inputs,
    raw_features,
    read_labels,
    read_npz,
    score_distributions,
    sha,
    specification,
    write_json,
)


def compare_frame(expected, path, spec):
    actual = pd.read_csv(path)
    if list(actual.columns) != list(expected.columns) or actual.shape != expected.shape:
        raise AssertionError("Score table identity/shape differs: " + str(path))
    maxima = {}
    for column in expected:
        if pd.api.types.is_numeric_dtype(expected[column]):
            tolerance = (
                spec["coverage_tolerance"]
                if column.startswith("coverage_")
                else spec["score_atol_mm"]
            )
            a, b = actual[column].to_numpy(float), expected[column].to_numpy(float)
            np.testing.assert_allclose(a, b, rtol=0, atol=tolerance, equal_nan=True)
            valid = np.isfinite(b)
            maxima[column] = (
                float(abs(a[valid] - b[valid]).max()) if valid.any() else None
            )
        else:
            np.testing.assert_array_equal(
                actual[column].astype(str), expected[column].astype(str)
            )
    return dict(rows=len(actual), max_absolute_difference_by_column=maxima)


def plot_complete_window(daily, folder):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    plt.rcParams.update(
        {"font.size": 9, "axes.spines.top": False, "axes.spines.right": False}
    )
    for phase in ("train", "prediction"):
        fig, axes = plt.subplots(2, 2, figsize=(12, 7), constrained_layout=True)
        for axis, point in zip(axes.flat, POINTS):
            part = daily[(daily.station == point) & (daily.part == phase)]
            gp = part[part.strategy == "BPLUS_GP"]
            base = part[part.strategy == "M0"]
            prior = part[part.strategy == "v1.1-e0"]
            dates = pd.to_datetime(gp.date)
            axis.fill_between(
                dates,
                gp.lower_90_mm,
                gp.upper_90_mm,
                color="#337faa",
                alpha=0.20,
                label="B+ GP 90% observation interval",
            )
            axis.plot(
                dates, gp.observed_mm, color="#222222", linewidth=1.2, label="Observed"
            )
            axis.plot(
                dates,
                base.mean_mm,
                color="#bf7926",
                linewidth=1.1,
                label="B+ / e0 mean",
            )
            axis.plot(
                dates, gp.mean_mm, color="#21658f", linewidth=1.2, label="B+ GP mean"
            )
            axis.plot(
                dates,
                prior.lower_90_mm,
                color="#bf7926",
                linewidth=0.6,
                linestyle=":",
                label="e0 90% interval",
            )
            axis.plot(
                dates, prior.upper_90_mm, color="#bf7926", linewidth=0.6, linestyle=":"
            )
            axis.set(title=point, ylabel="Cumulative displacement (mm)")
            axis.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=5))
            axis.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
            axis.grid(alpha=0.15)
        handles, labels = axes.flat[0].get_legend_handles_labels()
        fig.legend(handles, labels, loc="outside lower center", ncol=3, frameon=False)
        fig.suptitle(f"B+ GP | complete {phase} window | no date exclusions")
        fig.savefig(folder / f"{phase}_curves.png", dpi=160)
        fig.savefig(folder / f"{phase}_curves.pdf")
        plt.close(fig)


def verify():
    spec = specification()
    out = ROOT / spec["output_dir"]
    manifest = json.loads((out / "artifact_manifest.json").read_text())["files"]
    actual = {str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()}
    if actual != set(manifest) | {"artifact_manifest.json"}:
        raise AssertionError("Raw run inventory changed")
    for path, record in manifest.items():
        if (
            sha(out / path) != record["sha256"]
            or (out / path).stat().st_size != record["bytes"]
        ):
            raise AssertionError("Raw run artifact changed: " + path)
    snapshot = json.loads((out / "source_snapshot.json").read_text())
    check_sources(snapshot["files"])
    check_sources(snapshot["frozen_sources"])
    if snapshot["config_sha256"] != sha(CONFIG) or sha(out / "config.json") != sha(
        CONFIG
    ):
        raise AssertionError("Configuration identity differs")
    for path, digest in snapshot["files"].items():
        blob = subprocess.check_output(
            ["git", "show", f"{snapshot['commit']}:{path}"], cwd=ROOT
        )
        if hashlib.sha256(blob).hexdigest() != digest:
            raise AssertionError("Pre-run implementation commit differs")
    state = json.loads((out / "run_status.json").read_text())
    events = [
        json.loads(line) for line in (out / "events.jsonl").read_text().splitlines()
    ]
    kinds = [e["kind"] for e in events]
    fit_events = [e for e in events if e["kind"] == "fit_started"]
    objective_events = [e for e in events if e["kind"] == "objective_requested"]
    if state["fit_calls"] != len(fit_events) or state["objective_calls"] != len(
        objective_events
    ):
        raise AssertionError("Fit/objective count differs")
    if (
        state["fit_calls"] > 4
        or state["objective_calls"] > 1600
        or state["physical_solver_calls"] != 0
        or state["final_window_labels_read"] != 0
        or state["formal_startups"] != 1
        or state["automatic_retries"] != 0
    ):
        raise AssertionError("Execution exceeded registered call limits")
    if [e["data"]["station"] for e in fit_events] != list(POINTS[: len(fit_events)]):
        raise AssertionError("Point order or restart differs")
    for point in POINTS:
        evaluations = [
            e["data"]["evaluation"]
            for e in objective_events
            if e["data"]["station"] == point
        ]
        if (
            evaluations != list(range(1, len(evaluations) + 1))
            or len(evaluations) > 400
        ):
            raise AssertionError("Per-point evaluation accounting differs")
    started = datetime.fromisoformat(state["started_utc"])
    ended = datetime.fromisoformat(state["ended_utc"])
    if started >= datetime.fromisoformat(spec["implementation_deadline_utc"]):
        raise AssertionError("Formal run started after implementation deadline")
    # A hard interrupt can take a short time to serialize the stop record; do not relabel it a successful run.
    in_budget = (
        ended <= datetime.fromisoformat(spec["training_deadline_utc"])
        and state["elapsed_seconds"] <= spec["run_limit_seconds"]
    )
    report = dict(
        verified_utc=datetime.now(timezone.utc).isoformat(),
        source_integrity_passed=True,
        run_status=state["status"],
        formal_fit_calls=state["fit_calls"],
        objective_calls=state["objective_calls"],
        physical_solver_calls=0,
        verification_fit_calls=0,
        final_window_labels_read=0,
        in_run_budget=in_budget,
        completed_points=state["completed_points"],
    )
    if state["status"] != "completed_pending_independent_verification":
        report.update(
            complete_effectiveness_evaluation=False,
            effect_passed=False,
            outcome="stopped incomplete; missing predictions are not scored or filled",
            error=state.get("error"),
        )
        return report, None
    if (
        not in_budget
        or state["completed_points"] != list(POINTS)
        or state["fit_calls"] != 4
    ):
        raise AssertionError(
            "Successful run lacks complete four-point budget compliance"
        )
    if state["displacement_prefixes_read"] != [792, 1168]:
        raise AssertionError("Unexpected displacement label read")
    lock = json.loads((out / "prediction_lock.json").read_text())
    if (
        kinds.count("all_predictions_locked") != 1
        or kinds.count("development_labels_read") != 1
    ):
        raise AssertionError("Prediction lock/development label access differs")
    if not (
        kinds.index("fit_labels_read")
        < kinds.index("all_predictions_locked")
        < kinds.index("development_labels_read")
        < kinds.index("scoring_completed")
    ):
        raise AssertionError("Labels read before predictions were locked")
    lock_position = kinds.index("all_predictions_locked")
    if any(
        i > lock_position
        for i, k in enumerate(kinds)
        if k in ("fit_started", "objective", "point_completed")
    ):
        raise AssertionError("Model changed after locking")
    for path, digest in lock["files"].items():
        if sha(out / path) != digest:
            raise AssertionError("Locked prediction/model changed")
    lock_event = events[lock_position]["data"]["lock_sha256"]
    if lock_event != sha(out / "prediction_lock.json"):
        raise AssertionError("Prediction lock hash differs")
    saved, old, audit = load_reference(spec)
    if audit != json.loads((out / "reference_audit.json").read_text()):
        raise AssertionError("Reference provenance audit differs")
    fit_labels = read_labels(ROOT / spec["data"], 792)
    inputs = prepare_inputs(raw_features(saved), saved["mean"], fit_labels, spec)
    stored_inputs = read_npz(out / "inputs.npz")
    for key in ("raw", "x", "targets"):
        np.testing.assert_array_equal(stored_inputs[key], getattr(inputs, key))
    if inputs.normalizers != json.loads((out / "normalizers.json").read_text()):
        raise AssertionError("Training-only normalization differs")
    prediction = read_npz(out / "predictions.npz")
    np.testing.assert_array_equal(prediction["dates"], saved["dates"])
    point_checks = []
    for j, point in enumerate(POINTS):
        model = joblib.load(out / f"{point}.joblib")
        point_audit = json.loads((out / f"{point}_audit.json").read_text())
        if not point_audit["optimizer"]["success"] or model.optimizer is not None:
            raise AssertionError("Model is not the completed saved inference state")
        np.testing.assert_array_equal(model.X_train_, inputs.x[30:792])
        np.testing.assert_array_equal(model.y_train_, inputs.targets[:, j])
        scale = inputs.normalizers["residual_denominator_mm"][j]
        reloaded, variance_audit = predict(
            model, inputs.x, saved["mean"][:, j], scale, spec
        )
        if variance_audit != point_audit["variance"]:
            raise AssertionError("Variance audit differs")
        reload_errors = {}
        for key, value in reloaded.items():
            np.testing.assert_allclose(
                value, prediction[key][:, j], atol=spec["reload_atol"], rtol=0
            )
            reload_errors[key] = float(abs(value - prediction[key][:, j]).max())
        amplitude = variance_audit["amplitude"]
        lengths = np.asarray(variance_audit["length_scales"])
        noise = variance_audit["noise_variance_normalized"]
        mu, latent, obs = independent_posterior(
            inputs.x[30:792],
            inputs.targets[:, j],
            inputs.x,
            amplitude,
            lengths,
            noise,
            spec["jitter"],
        )
        independent = dict(
            mean=saved["mean"][:, j] + mu * scale,
            latent_variance=latent * scale**2,
            observation_variance=obs * scale**2,
        )
        matrix_errors = {}
        for key, value in independent.items():
            np.testing.assert_allclose(
                value,
                prediction[key][:, j],
                atol=spec["matrix_atol"],
                rtol=spec["matrix_rtol"],
            )
            matrix_errors[key] = float(abs(value - prediction[key][:, j]).max())
        np.testing.assert_allclose(
            prediction["observation_variance"][:, j]
            - prediction["latent_variance"][:, j],
            prediction["noise_variance"][:, j],
            atol=spec["matrix_atol"],
            rtol=spec["matrix_rtol"],
        )
        point_checks.append(
            dict(
                station=point,
                reload_max_errors=reload_errors,
                independent_matrix_max_errors=matrix_errors,
            )
        )
    labels = read_labels(ROOT / spec["data"], 1168)
    metrics, aggregate, daily = score_distributions(saved, old, prediction, labels)
    tables = {
        name: compare_frame(frame, out / name, spec)
        for name, frame in (
            ("metrics.csv", metrics),
            ("aggregate.csv", aggregate),
            ("daily_predictions.csv", daily),
        )
    }
    historical = check_historical_scores(metrics, aggregate, spec)
    if historical != json.loads((out / "historical_score_check.json").read_text()):
        raise AssertionError("Historical baseline score check differs")
    decision = decide(metrics, spec)
    if decision != json.loads((out / "decision.json").read_text()):
        raise AssertionError("Frozen decision differs")
    report.update(
        complete_effectiveness_evaluation=True,
        numerical_checks_passed=True,
        effect_passed=decision["effect_passed"],
        source_audit=audit,
        prediction_lock_passed=True,
        per_point_numerics=point_checks,
        score_tables=tables,
        historical_scores=historical,
        outcome="stop; no automatic extension or final-window training",
    )
    return report, daily


def main():
    with threadpool_limits(limits=1):
        report, daily = verify()
    # Verification outputs stay outside the sealed raw run directory.
    folder = ROOT / "results/ootang_bplus_gp_v1/20260913_verification"
    folder.mkdir(parents=True, exist_ok=False)
    write_json(folder / "verification.json", report)
    if daily is not None:
        plot_complete_window(daily, folder)
    write_json(
        folder / "artifact_manifest.json",
        dict(
            verifier_sha256=sha(__file__),
            raw_manifest_sha256=sha(
                ROOT
                / "results/ootang_bplus_gp_v1/20260913_development/artifact_manifest.json"
            ),
            files={
                p.name: dict(sha256=sha(p), bytes=p.stat().st_size)
                for p in sorted(folder.iterdir())
                if p.is_file()
            },
        ),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
