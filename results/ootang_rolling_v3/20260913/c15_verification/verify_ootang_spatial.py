"""Independent spatial feature, initial fit, online fit and score verification."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from verify_ootang_online import (
    arrays,
    sha,
    array_sha,
    batch_coefficients,
    verify_calibration,
    independent_metrics,
    decision_checks,
)

ROOT = Path(__file__).resolve().parents[1]
POINTS = ("ATU1", "ATU5", "MJ3", "MJ1")
DEPENDENCIES = (
    "verify_ootang_online.py",
    "verify_ootang_empirical.py",
    "verify_ootang_rolling.py",
)


def project(six, arm):
    flat = six.reshape(*six.shape[:-2], 24)
    output = []
    orders = []
    for p in range(4):
        indices = list(range(p * 6, p * 6 + (6 if arm == "FULL" else 4)))
        indices += [q * 6 + d for q in range(4) if q != p for d in range(4)]
        output.append(flat[..., indices])
        orders.append([[i // 6, i % 6] for i in indices])
    return np.stack(output, axis=-2), orders


def raw_scale(six, model):
    H = six.shape[-3]
    a, b = np.exp(np.asarray(model["scale_logs"])).T
    sy = np.asarray(model["target_scale"])[:H]
    q_squared = np.sum(six[..., :4] * six[..., :4], axis=-1) / 4
    return np.sqrt(a * a * sy * sy + b * b * q_squared + model["sigma_floor_mm"] ** 2)


def verify_initial(train, model, spec):
    arm = model["arm"]
    features, order = project(train["features"], arm)
    N, H, P, D = features.shape
    if (
        model["spatial_feature_sources"] != order
        or model["dimensions"] != D
        or model["training_samples"] != N
        or model["alpha"] != spec["alpha"]
    ):
        raise ValueError("Initial model contract differs")
    sx = np.maximum(np.sqrt(np.mean(features**2, axis=0)), 1e-6)
    sy = np.maximum(
        np.sqrt(np.mean((train["target"] - train["base"]) ** 2, axis=0)),
        spec["probability"]["sigma_floor_mm"],
    )
    np.testing.assert_array_equal(sx, model["feature_scale"])
    np.testing.assert_array_equal(sy, model["target_scale"])
    x = features / sx
    z = (train["target"] - train["base"]) / sy
    beta = np.asarray(model["beta"])
    max_svd = 0.0
    for h in range(H):
        for p in range(P):
            A = np.vstack([x[:, h, p], np.sqrt(N * spec["alpha"]) * np.eye(D)])
            y = np.r_[z[:, h, p], np.zeros(D)]
            coef = np.linalg.lstsq(A, y, rcond=None)[0]
            np.testing.assert_allclose(coef, beta[h, p], rtol=5e-11, atol=1e-8)
            max_svd = max(max_svd, float(abs(coef - beta[h, p]).max()))
    normalized_mu = np.sum(x * beta, axis=-1)
    residual = z - normalized_mu
    q2 = np.mean(train["features"][..., :4] ** 2, axis=-1) / (sy * sy)
    loss_max = gradient_max = 0.0
    calls = iterations = 0
    for p, op in enumerate(model["scale_optimizer_records"]):
        logs = np.array(model["scale_logs"][p])
        np.testing.assert_array_equal(logs, op["logs"])

        def objective(theta):
            var = (
                np.exp(2 * theta[0])
                + np.exp(2 * theta[1]) * q2[:, :, p]
                + (model["sigma_floor_mm"] / sy[None, :, p]) ** 2
            )
            return np.mean(np.log(var) + (residual[:, :, p] ** 2) / var) / 2

        value = float(objective(logs))
        gradient = []
        for d in range(2):
            theta = logs.astype(complex)
            theta[d] += 1e-20j
            gradient.append(float(objective(theta).imag / 1e-20))
        np.testing.assert_allclose(value, op["objective"], rtol=5e-11, atol=1e-8)
        np.testing.assert_allclose(gradient, op["gradient"], rtol=5e-11, atol=1e-8)
        loss_max = max(loss_max, abs(value - op["objective"]))
        gradient_max = max(
            gradient_max, float(np.max(abs(np.asarray(gradient) - op["gradient"])))
        )
        calls += op["function_calls"]
        iterations += op["iterations"]
        bounds = spec["ridge"]["scale_log_bounds"]
        if np.any(logs < bounds[0]) or np.any(logs > bounds[1]):
            raise ValueError("Scale outside fixed bounds")
    return (
        features,
        train["base"] + sy * normalized_mu,
        dict(
            initial_svd=H * P,
            max_svd=max_svd,
            scale_fits=P,
            scale_calls=calls,
            scale_iterations=iterations,
            max_nll=loss_max,
            max_gradient=gradient_max,
        ),
    )


def verify(run):
    manifest = json.loads((run / "artifact_manifest.json").read_text())["files"]
    for name, wanted in manifest.items():
        if sha(run / name) != wanted:
            raise ValueError("Changed artifact " + name)
    sources = json.loads((run / "sources.json").read_text())["files"]
    for name, wanted in sources.items():
        p = run / "sources" / name
        if not p.exists():
            p = ROOT / name
        if sha(p) != wanted:
            raise ValueError("Changed source " + name)
    cfgs = list((run / "sources/config").glob("ootang_rolling_probability.*.json"))
    if len(cfgs) != 1:
        raise ValueError("Ambiguous source config")
    spec = json.loads(cfgs[0].read_text())
    status = json.loads((run / "status.json").read_text())
    if (
        status["state"] != "completed"
        or status["exit_code"] != 0
        or datetime.fromisoformat(status["finished_utc"])
        > datetime.fromisoformat(spec["deadline_utc"])
    ):
        raise ValueError("Incomplete or late run")
    prior = ROOT / spec["prior_verification"]
    if (
        sha(prior) != spec["prior_verification_sha256"]
        or not json.loads(prior.read_text())["passed"]
    ):
        raise ValueError("C8 prior verification changed")
    if not spec["post_transfer_exposure"] or spec["original_single_transfer_reused"]:
        raise ValueError("Exposure rule changed")
    labels = pd.read_csv(ROOT / spec["data"])[[p + "/mm" for p in POINTS]].to_numpy(
        float
    )
    decisions = json.loads((run / "decision.json").read_text())
    records = {}
    learning = {}
    totals = dict(
        initial_svd=0,
        online_svd=0,
        point_updates=0,
        score_cells=0,
        scale_fits=0,
        scale_calls=0,
        scale_iterations=0,
    )
    maximum = dict(
        feature=0.0,
        initial_svd=0.0,
        online_beta=0.0,
        online_svd=0.0,
        mean_mm=0.0,
        raw_sigma_mm=0.0,
        calibration_mm=0.0,
        nll=0.0,
        gradient=0.0,
        scores_mm=0.0,
    )
    for phase, (start, end) in spec["stages"].items():
        N, H = end - start, spec["horizons"]
        ref = spec["sources"][phase]
        source = ROOT / ref["current_run"] / ref["current_phase"]
        if (
            sha(source.parent / "artifact_manifest.json")
            != ref["current_manifest_sha256"]
        ):
            raise ValueError("C8 source changed")
        train = arrays(run / phase / "initial_training.npz")
        issued = arrays(run / phase / "original_issued_features.npz")
        for data, key in [(train, "training"), (issued, "issued")]:
            old = arrays(ROOT / ref[key + "_path"])
            if (
                sha(ROOT / ref[key + "_path"]) != ref[key + "_sha256"]
                or old.keys() != data.keys()
            ):
                raise ValueError("Changed original feature source")
            for field in old:
                np.testing.assert_array_equal(data[field], old[field])
        targets = train["origins"][:, None] + np.arange(H)
        if targets.max() >= start or np.any(train["teachers"] > train["origins"]):
            raise ValueError("Teacher or labels cross training boundary")
        np.testing.assert_array_equal(train["target"], labels[targets])
        np.testing.assert_array_equal(issued["origins"], np.arange(start, end))
        contract = json.loads((run / phase / "training_contract.json").read_text())
        if (
            contract["rows"] != start
            or contract["max_target"] != int(targets.max())
            or contract["label_sha256"] != array_sha(labels[:start])
            or contract["training_samples"] != len(train["origins"])
        ):
            raise ValueError("Training contract differs")
        current = {}
        for p in (run / phase).glob("*.npz"):
            v = arrays(p)
            if {"origins", "mean", "sigma", "raw_sigma"} <= v.keys():
                current[p.stem] = v
        records[phase] = current
        learning[phase] = {}
        calibration = json.loads((run / phase / "calibration.json").read_text())
        phase_counts = dict(
            initial_point_solves=0,
            online_point_solves=0,
            scale_fits=0,
            scale_objective_calls=0,
            scale_iterations=0,
        )
        for arm in spec["ridge"]["arms"]:
            name = "C15_SPATIAL_" + arm
            pred = current[name]
            model = json.loads((run / phase / ("model_" + arm + ".json")).read_text())
            transformed, fit_mean, info = verify_initial(train, model, spec)
            if contract["dimensions"][arm] != model["dimensions"]:
                raise ValueError("Incorrect dimension contract")
            fit = arrays(run / phase / ("fit_" + arm + ".npz"))
            np.testing.assert_array_equal(fit["origins"], train["origins"])
            np.testing.assert_allclose(fit["mean"], fit_mean, rtol=5e-11, atol=1e-8)
            np.testing.assert_allclose(
                fit["raw_sigma"],
                raw_scale(train["features"], model),
                rtol=5e-11,
                atol=1e-8,
            )
            log = arrays(run / phase / ("learning_" + arm + ".npz"))
            learning[phase][arm] = log
            fx, _ = project(issued["features"], arm)
            np.testing.assert_array_equal(fx, log["features"])
            np.testing.assert_array_equal(
                log["penalty"],
                len(train["origins"]) * spec["alpha"] * np.eye(model["dimensions"]),
            )
            saved = dict(log, beta_issued=log["beta"])
            mean, batch = batch_coefficients(
                dict(train, features=transformed),
                dict(issued, features=fx),
                model,
                saved,
                labels,
                start,
                end,
            )
            np.testing.assert_allclose(
                pred["mean"], mean, rtol=5e-11, atol=1e-8, equal_nan=True
            )
            sigma = raw_scale(issued["features"], model)
            np.testing.assert_allclose(
                pred["raw_sigma"], sigma, rtol=5e-11, atol=1e-8, equal_nan=True
            )
            delta = verify_calibration(
                pred, labels, start, end, spec["probability"], calibration[arm]
            )
            for key, value in [
                ("initial_svd", info["max_svd"]),
                ("online_beta", batch["max_batch_coefficient_difference"]),
                ("online_svd", batch["max_augmented_svd_difference"]),
                ("mean_mm", float(np.nanmax(abs(mean - pred["mean"])))),
                ("raw_sigma_mm", float(np.nanmax(abs(sigma - pred["raw_sigma"])))),
                ("calibration_mm", delta),
                ("nll", info["max_nll"]),
                ("gradient", info["max_gradient"]),
            ]:
                maximum[key] = max(maximum[key], value)
            for key, value in [
                ("initial_svd", info["initial_svd"]),
                ("online_svd", batch["augmented_svd_checks"]),
                ("point_updates", batch["point_updates"]),
                ("scale_fits", info["scale_fits"]),
                ("scale_calls", info["scale_calls"]),
                ("scale_iterations", info["scale_iterations"]),
            ]:
                totals[key] += value
            for key, value in [
                ("initial_point_solves", info["initial_svd"]),
                ("online_point_solves", batch["point_updates"]),
                ("scale_fits", info["scale_fits"]),
                ("scale_objective_calls", info["scale_calls"]),
                ("scale_iterations", info["scale_iterations"]),
            ]:
                phase_counts[key] += value
        rows = []
        for name, pred in current.items():
            np.testing.assert_array_equal(pred["origins"], np.arange(start, end))
            np.testing.assert_array_equal(pred["teacher_prefixes"], np.full(N, start))
            if not name.startswith("C15_SPATIAL_"):
                old = arrays(source / (name + ".npz"))
                if old.keys() != pred.keys():
                    raise ValueError("Changed control fields")
                for key in old:
                    np.testing.assert_array_equal(old[key], pred[key])
            for k in range(H):
                count = N - k
                y = labels[start + k : end]
                mu = pred["mean"][:count, k]
                values = (
                    dict(
                        mae=np.mean(abs(y - mu), axis=0),
                        rmse=np.sqrt(np.mean((y - mu) ** 2, axis=0)),
                    )
                    if name == "B_RAW"
                    else independent_metrics(y, mu, pred["sigma"][:count, k])
                )
                rows.extend(
                    dict(
                        model=name,
                        horizon=k + 1,
                        point=p,
                        n=count,
                        **{key: float(v[j]) for key, v in values.items()},
                    )
                    for j, p in enumerate(POINTS)
                )
        computed = pd.DataFrame(rows)
        summary = []
        for (name, h), g in computed.groupby(["model", "horizon"], sort=False):
            summary.append(
                dict(
                    model=name,
                    horizon=int(h),
                    n_per_point=int(g.n.iloc[0]),
                    pooled_rmse=float(np.sqrt(np.mean(g.rmse**2))),
                    **{
                        key: float(g[key].mean())
                        for key in computed
                        if key not in ("model", "horizon", "point", "n")
                    },
                )
            )
        summary = pd.DataFrame(summary)
        for frame, filename, keys in [
            (computed, "metrics.csv", ["model", "horizon", "point"]),
            (summary, "summary.csv", ["model", "horizon"]),
        ]:
            a = frame.set_index(keys).sort_index()
            b = pd.read_csv(run / phase / filename).set_index(keys).sort_index()
            np.testing.assert_allclose(
                a.to_numpy(),
                b[a.columns].to_numpy(),
                rtol=5e-11,
                atol=1e-8,
                equal_nan=True,
            )
            maximum["scores_mm"] = max(
                maximum["scores_mm"],
                float(np.nanmax(abs(a.to_numpy() - b[a.columns].to_numpy()))),
            )
            totals["score_cells"] += a.size
        checks = decision_checks(computed, summary, spec["candidate"], spec)
        dec = json.loads((run / phase / "decision.json").read_text())
        if (
            checks != dec["checks"]
            or dec["passed"] != all(checks.values())
            or dec["passed_count"] != sum(checks.values())
            or dec != decisions["phases"][phase]
        ):
            raise ValueError("Effect decision differs")
        for key, value in phase_counts.items():
            if dec[key] != value:
                raise ValueError("Incorrect phase accounting")
    chain, pending, locks = "", None, 0
    next_origin = {}
    for raw in (run / "events.jsonl").read_text().splitlines():
        e = json.loads(raw)
        if e["previous_sha256"] != chain:
            raise ValueError("Broken event chain")
        chain = hashlib.sha256(raw.encode()).hexdigest()
        phase = e["phase"]
        start, end = spec["stages"][phase]
        if e["kind"] == "phase_started":
            if e["training_prefix"] != start or e["label_sha256"] != array_sha(
                labels[:start]
            ):
                raise ValueError("Incorrect training prefix")
            next_origin[phase] = start
        elif e["kind"] == "forecast_locked":
            n = e["origin"]
            i = n - start
            h = min(spec["horizons"], end - n)
            if (
                pending is not None
                or n != next_origin[phase]
                or e["history_sha256"] != array_sha(labels[:n])
            ):
                raise ValueError("Incorrect forecast order")
            predictions = {
                name: array_sha(
                    np.stack([p[k][i, :h] for k in ("mean", "sigma", "raw_sigma")])
                )
                for name, p in records[phase].items()
            }
            inputs = {
                arm: dict(
                    features=array_sha(log["features"][i, :h]),
                    beta=array_sha(log["beta"][i]),
                )
                for arm, log in learning[phase].items()
            }
            if predictions != e["predictions"] or inputs != e["inputs"]:
                raise ValueError("Incorrect forecast/input lock")
            pending = (phase, n)
            locks += 1
        elif e["kind"] == "observation_released":
            if pending != (phase, e["index"]) or e["value_sha256"] != array_sha(
                labels[e["index"]]
            ):
                raise ValueError("Premature observation")
            pending = None
            next_origin[phase] += 1
        elif e["kind"] == "phase_completed":
            if pending is not None or next_origin[phase] != end:
                raise ValueError("Incomplete phase")
        else:
            raise ValueError("Unknown event")
    if locks != sum(e - s for s, e in spec["stages"].values()) or pending is not None:
        raise ValueError("Incomplete locks")
    for key, config, total in [
        ("initial_point_solves", "expected_initial_solves", "initial_svd"),
        ("online_point_solves", "expected_online_solves", "point_updates"),
        ("scale_fits", "expected_scale_fits", "scale_fits"),
    ]:
        if decisions[key] != spec[config] or decisions[key] != totals[total]:
            raise ValueError("Budget count differs")
    if (
        decisions["scale_objective_calls"] != totals["scale_calls"]
        or decisions["scale_iterations"] != totals["scale_iterations"]
        or decisions["new_neural_updates"] != 0
        or decisions["physical_calls"] != 0
    ):
        raise ValueError("Additional work count differs")
    return dict(
        passed=True,
        checked_utc=datetime.now(timezone.utc).isoformat(),
        run=str(run),
        immutable_artifacts_checked=len(manifest),
        sources_checked=len(sources),
        forecast_locks=locks,
        checks=totals,
        maximum_differences=maximum,
        old_distributions_exactly_unchanged=True,
        post_exposure=True,
        independent_transfer=False,
        new_training_updates=0,
        physical_calls=0,
        verifier_sha256=sha(Path(__file__)),
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
    for name in (Path(__file__).name, *DEPENDENCIES):
        (args.out / name).write_bytes(Path(__file__).with_name(name).read_bytes())
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
