"""Check C9 scales, complex-step NLL gradients, causal replay and frozen means.

No training, optimizer reruns, production scoring, or physical solver imports.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

from verify_ootang_empirical import decision_checks
from verify_ootang_online import array_sha, arrays, sha, verify_calibration
from verify_ootang_rolling import independent_metrics

ROOT = Path(__file__).resolve().parents[1]
POINTS = ("ATU1", "ATU5", "MJ3", "MJ1")
DEPENDENCIES = (
    "verify_ootang_online.py",
    "verify_ootang_empirical.py",
    "verify_ootang_rolling.py",
)


def check_scale(train, issued, model, state, spec):
    sx, sy, beta = (
        np.asarray(model[k]) for k in ("feature_scale", "target_scale", "beta")
    )
    d, H = model["dimensions"], spec["horizons"]
    np.testing.assert_array_equal(state["target_scale"], sy)
    if state["training_samples"] != len(train["origins"]):
        raise ValueError("Wrong scale sample count")
    if state["sigma_floor_mm"] != spec["probability"]["sigma_floor_mm"]:
        raise ValueError("Scale floor changed")
    logs = np.asarray(state["scale_logs"])
    if logs.shape != (H, 4, 2):
        raise ValueError("Wrong scale head dimensions")
    lo, hi = spec["scale_log_bounds"]
    if not np.isfinite(logs).all() or (logs < lo).any() or (logs > hi).any():
        raise ValueError("Scale parameters outside frozen bounds")
    ops = state["optimizer_records"]
    if len(ops) != H * 4:
        raise ValueError("Missing scale optimization records")
    fit_coverage, fit_rms = np.empty((H, 4)), np.empty((H, 4))
    raw = np.full(issued["features"].shape[:-1], np.nan)
    max_obj = max_grad = 0.0
    for h in range(H):
        for p in range(4):
            op = ops[4 * h + p]
            if (op["horizon"], op["point"]) != (h + 1, p):
                raise ValueError("Optimization head identity differs")
            np.testing.assert_array_equal(op["logs"], logs[h, p])
            if not isinstance(op["success"], bool) or not op["message"]:
                raise ValueError("Optimizer status missing")
            prediction = train["base"][:, h, p] + sy[h, p] * np.sum(
                train["features"][:, h, p, :d] / sx[h, p] * beta[h, p], axis=1
            )
            residual = (train["target"][:, h, p] - prediction) / sy[h, p]
            q2 = np.sum(train["features"][:, h, p, :4] ** 2, axis=1) / 4 / sy[h, p] ** 2
            floor2 = (state["sigma_floor_mm"] / sy[h, p]) ** 2

            def objective(parameters):
                variance = (
                    np.exp(2 * parameters[0]) + np.exp(2 * parameters[1]) * q2 + floor2
                )
                return np.mean(np.log(variance) + residual**2 / variance) / 2

            value = objective(logs[h, p])
            # Complex perturbations differentiate the scalar objective independently.
            gradient = []
            for j in range(2):
                z = logs[h, p].astype(complex)
                z[j] += 1e-20j
                gradient.append(float(objective(z).imag / 1e-20))
            np.testing.assert_allclose(value, op["objective"], rtol=5e-11, atol=1e-8)
            np.testing.assert_allclose(gradient, op["gradient"], rtol=5e-11, atol=1e-8)
            max_obj = max(max_obj, float(abs(value - op["objective"])))
            max_grad = max(
                max_grad, float(np.max(abs(np.asarray(gradient) - op["gradient"])))
            )
            variance = (
                np.exp(2 * logs[h, p, 0]) + np.exp(2 * logs[h, p, 1]) * q2 + floor2
            )
            z = residual / np.sqrt(variance)
            fit_coverage[h, p] = np.mean(abs(z) <= NormalDist().inv_cdf(0.95))
            fit_rms[h, p] = np.sqrt(np.mean(z**2))
            q_future2 = np.sum(issued["features"][:, h, p, :4] ** 2, axis=1) / 4
            raw[:, h, p] = np.sqrt(
                np.exp(2 * logs[h, p, 0]) * sy[h, p] ** 2
                + np.exp(2 * logs[h, p, 1]) * q_future2
                + state["sigma_floor_mm"] ** 2
            )
    return raw, dict(
        heads=len(ops),
        objective_max_difference=max_obj,
        complex_step_gradient_max_difference=max_grad,
        all_saved_convergence_flags=all(op["success"] for op in ops),
        function_calls=sum(op["function_calls"] for op in ops),
        iterations=sum(op["iterations"] for op in ops),
        bound_parameter_count=int(np.sum((logs == lo) | (logs == hi))),
        raw_fit_coverage90=fit_coverage.tolist(),
        raw_fit_standardized_error_rms=fit_rms.tolist(),
    )


def verify(run):
    manifest = json.loads((run / "artifact_manifest.json").read_text())["files"]
    for name, wanted in manifest.items():
        if sha(run / name) != wanted:
            raise ValueError("Artifact changed: " + name)
    source = json.loads((run / "sources.json").read_text())["files"]
    for name, wanted in source.items():
        p = run / "sources" / name
        if not p.exists():
            p = ROOT / name
        if sha(p) != wanted:
            raise ValueError("Source changed: " + name)
    spec = json.loads(
        (run / "sources/config/ootang_rolling_probability.v3_8.json").read_text()
    )
    if (
        not spec["post_transfer_exposure"]
        or spec["original_single_transfer_reused"]
        or spec["new_mean_updates_permitted"]
    ):
        raise ValueError("Frozen exposure or mean rule changed")
    if sha(ROOT / spec["data"]) != spec["data_sha256"]:
        raise ValueError("Data differs")
    status = json.loads((run / "status.json").read_text())
    if status["state"] != "completed" or status["exit_code"] != 0:
        raise ValueError("Run incomplete")
    if datetime.fromisoformat(status["finished_utc"]) > datetime.fromisoformat(
        spec["deadline_utc"]
    ):
        raise ValueError("Original deadline exceeded")
    labels = pd.read_csv(ROOT / spec["data"], usecols=[p + "/mm" for p in POINTS])[
        [p + "/mm" for p in POINTS]
    ].to_numpy(float)
    final = json.loads((run / "decision.json").read_text())
    records, scale_checks = {}, {}
    max_raw = max_cal = max_score = 0.0
    score_cells = 0
    for phase, (start, end) in spec["stages"].items():
        ref, directory = spec["sources"][phase], run / phase
        H, N = spec["horizons"], end - start
        train, issued = (
            arrays(ROOT / ref["initial_training"]),
            arrays(ROOT / ref["issued_features"]),
        )
        np.testing.assert_array_equal(issued["origins"], np.arange(start, end))
        targets = train["origins"][:, None] + np.arange(H)[None]
        if targets.max() >= start or (train["teachers"] > train["origins"]).any():
            raise ValueError("Training target or teacher crossed available history")
        np.testing.assert_array_equal(train["target"], labels[targets])
        contract = json.loads((directory / "training_contract.json").read_text())
        if contract != dict(
            rows=start,
            max_target=int(targets.max()),
            training_queries=len(targets),
            label_sha256=array_sha(labels[:start]),
            mean_unchanged=True,
        ):
            raise ValueError("Training contract differs")
        if (
            sha(ROOT / ref["current_run"] / "artifact_manifest.json")
            != ref["current_manifest_sha256"]
        ):
            raise ValueError("Original C8 manifest differs")
        records[phase] = {p.stem: arrays(p) for p in directory.glob("*.npz")}
        feedback = json.loads((directory / "feedback_state.json").read_text())
        for arm in ("FULL", "DATA"):
            model = json.loads(
                (
                    run
                    / "sources"
                    / ref["original_mean_training_path"]
                    / f"ridge_{arm}_a0.001.json"
                ).read_text()
            )
            state = json.loads((directory / f"scale_{arm}.json").read_text())
            raw, check = check_scale(train, issued, model, state, spec)
            name = "C9_HORIZON_" + arm
            pred = records[phase][name]
            np.testing.assert_allclose(
                raw, pred["raw_sigma"], rtol=5e-11, atol=1e-8, equal_nan=True
            )
            max_raw = max(max_raw, float(np.nanmax(abs(raw - pred["raw_sigma"]))))
            max_cal = max(
                max_cal,
                verify_calibration(
                    pred, labels, start, end, spec["probability"], feedback[name]
                ),
            )
            scale_checks[phase + "/" + arm] = check
        rows = []
        for name, pred in records[phase].items():
            np.testing.assert_array_equal(pred["origins"], np.arange(start, end))
            np.testing.assert_array_equal(pred["teacher_prefixes"], np.full(N, start))
            original = (
                "C8_ONLINE_" + name.rsplit("_", 1)[-1]
                if name.startswith("C9_")
                else name
            )
            old = arrays(
                ROOT / ref["current_run"] / ref["current_phase"] / (original + ".npz")
            )
            if old.keys() != pred.keys():
                raise ValueError("Prediction fields changed")
            mutable = (
                {"raw_sigma", "sigma", "calibration_factor", "feedback_log_scale"}
                if name.startswith("C9_HORIZON_")
                else set()
            )
            for key in old.keys() - mutable:
                np.testing.assert_array_equal(pred[key], old[key])
            for k in range(H):
                count = N - k
                y, mu, sd = (
                    labels[start + k : end],
                    pred["mean"][:count, k],
                    pred["sigma"][:count, k],
                )
                if name == "B_RAW":
                    vals = dict(
                        mae=abs(mu - y).mean(axis=0),
                        rmse=np.sqrt(((mu - y) ** 2).mean(axis=0)),
                    )
                else:
                    vals = independent_metrics(y, mu, sd)
                for j, point in enumerate(POINTS):
                    rows.append(
                        dict(
                            model=name,
                            horizon=k + 1,
                            point=point,
                            n=count,
                            **{key: float(value[j]) for key, value in vals.items()},
                        )
                    )
        computed = pd.DataFrame(rows)
        summary_rows = []
        for (name, h), g in computed.groupby(["model", "horizon"], sort=False):
            summary_rows.append(
                dict(
                    model=name,
                    horizon=int(h),
                    n_per_point=int(g.n.iloc[0]),
                    **{
                        key: float(g[key].mean())
                        for key in computed
                        if key not in ("model", "horizon", "point", "n")
                    },
                    pooled_rmse=float(np.sqrt(np.mean(g.rmse**2))),
                )
            )
        summary = pd.DataFrame(summary_rows)
        for frame, filename, keys in (
            (computed, "metrics.csv", ["model", "horizon", "point"]),
            (summary, "summary.csv", ["model", "horizon"]),
        ):
            a = frame.set_index(keys).sort_index()
            b = pd.read_csv(directory / filename).set_index(keys).sort_index()
            np.testing.assert_allclose(
                a.to_numpy(),
                b[a.columns].to_numpy(),
                rtol=5e-11,
                atol=1e-8,
                equal_nan=True,
            )
            max_score = max(
                max_score, float(np.nanmax(abs(a.to_numpy() - b[a.columns].to_numpy())))
            )
            score_cells += a.size
        checks = decision_checks(computed, summary, spec["candidate"], spec)
        decision = json.loads((directory / "decision.json").read_text())
        if (
            checks != decision["checks"]
            or decision["passed"] != all(checks.values())
            or decision["passed_count"] != sum(checks.values())
            or decision != final["phases"][phase]
        ):
            raise ValueError("Effect decision differs")
        for key, check_key in (
            ("scale_optimizations", "heads"),
            ("scale_function_calls", "function_calls"),
            ("scale_iterations", "iterations"),
        ):
            if decision[key] != sum(
                scale_checks[phase + "/" + arm][check_key] for arm in ("FULL", "DATA")
            ):
                raise ValueError("Phase optimization counts differ")
    chain, pending, locks = "", None, 0
    next_origin = {}
    prefix_events = set()
    for raw in (run / "events.jsonl").read_text().splitlines():
        event = json.loads(raw)
        if event["previous_sha256"] != chain:
            raise ValueError("Event hash chain differs")
        chain = hashlib.sha256(raw.encode()).hexdigest()
        phase = event["phase"]
        start, end = spec["stages"][phase]
        kind = event["kind"]
        if kind == "training_prefix_read":
            if (
                phase in prefix_events
                or phase in next_origin
                or event["rows"] != start
                or event["label_sha256"] != array_sha(labels[:start])
            ):
                raise ValueError("Training prefix event differs")
            prefix_events.add(phase)
        elif kind == "phase_started":
            if phase not in prefix_events or phase in next_origin:
                raise ValueError("Phase ordering differs")
            next_origin[phase] = start
        elif kind == "forecast_locked":
            n = event["origin"]
            i, h = n - start, min(spec["horizons"], end - n)
            if (
                pending is not None
                or next_origin[phase] != n
                or event["history_sha256"] != array_sha(labels[:n])
            ):
                raise ValueError("Forecast history or ordering differs")
            expected = {
                name: array_sha(
                    np.stack([pred[k][i, :h] for k in ("mean", "sigma", "raw_sigma")])
                )
                for name, pred in records[phase].items()
            }
            if event["predictions"] != expected:
                raise ValueError("Issued prediction hash differs")
            pending = (phase, n)
            locks += 1
        elif kind == "observation_released":
            if pending != (phase, event["index"]) or event["value_sha256"] != array_sha(
                labels[event["index"]]
            ):
                raise ValueError("Released target ordering differs")
            pending = None
            next_origin[phase] += 1
        elif kind == "phase_completed":
            if pending is not None or next_origin[phase] != end:
                raise ValueError("Incomplete phase")
        else:
            raise ValueError("Unknown event")
    if locks != sum(e - s for s, e in spec["stages"].values()) or pending is not None:
        raise ValueError("Missing forecasts")
    for key, check_key in (
        ("scale_optimizations", "heads"),
        ("scale_function_calls", "function_calls"),
        ("scale_iterations", "iterations"),
    ):
        if final[key] != sum(c[check_key] for c in scale_checks.values()):
            raise ValueError("Total optimization counts differ")
    if final["scale_optimizations"] != spec["expected_scale_fits"] or any(
        final[k] != 0
        for k in ("new_mean_updates", "new_neural_updates", "physical_calls")
    ):
        raise ValueError("Budget accounting differs")
    return dict(
        passed=True,
        checked_utc=datetime.now(timezone.utc).isoformat(),
        run=str(run),
        immutable_artifacts_checked=len(manifest),
        sources_checked=len(source),
        forecast_locks=locks,
        scale_heads_verified=final["scale_optimizations"],
        scales=scale_checks,
        means_and_old_distributions_exactly_unchanged=True,
        max_raw_scale_difference_mm=max_raw,
        max_calibration_difference_mm=max_cal,
        score_cells_checked=score_cells,
        max_csv_rounding_difference=max_score,
        post_exposure=True,
        independent_transfer=False,
        new_training_updates=0,
        optimizer_reruns=0,
        physical_calls=0,
        verifier_sha256=sha(__file__),
        dependency_sha256={
            name: sha(Path(__file__).with_name(name)) for name in DEPENDENCIES
        },
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    result = verify(args.run)
    args.out.mkdir(parents=True)
    (args.out / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    for name in (Path(__file__).name,) + DEPENDENCIES:
        (args.out / name).write_bytes(Path(__file__).with_name(name).read_bytes())
    print(json.dumps({k: v for k, v in result.items() if k != "scales"}, indent=2))


if __name__ == "__main__":
    main()
