"""C15: cross-point observed dynamics, with the original ridge/scale rules."""

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
from scipy.linalg import solve
from scipy.optimize import minimize

from physics_guided.reference import ROOT, save_json, sha
from .data import ObservationStream, array_sha
from .empirical import Recorder, checked_run, utc
from .horizon_scale import arrays
from .online import OnlineRidge
from .ridge import RidgeDistribution, scale_objective
from .scoring import CausalCalibration, aggregate, gate, score_predictions


def feature_sources(arm):
    own = 6 if arm == "FULL" else 4
    if arm not in ("FULL", "DATA"):
        raise ValueError("Unknown spatial arm")
    return [
        [(p, d) for d in range(own)]
        + [(q, d) for q in range(4) if q != p for d in range(4)]
        for p in range(4)
    ]


def spatial_features(six, arm):
    if six.shape[-2:] != (4, 6):
        raise ValueError("Expected four points and six original features")
    return np.stack(
        [
            np.stack([six[..., q, d] for q, d in order], axis=-1)
            for order in feature_sources(arm)
        ],
        axis=-2,
    )


class SpatialDistribution(RidgeDistribution):
    def raw_scale(self, six):
        H = six.shape[-3]
        q = np.sqrt(np.mean(six[..., :4] ** 2, axis=-1))
        a, b = np.exp(self.scale_logs).T
        return np.sqrt(
            (a * self.target_scale[:H]) ** 2
            + (b * q) ** 2
            + self.state["sigma_floor_mm"] ** 2
        )

    def predict_features(self, six, base):
        H = six.shape[-3]
        x = spatial_features(six, self.state["arm"]) / self.feature_scale[:H]
        mu = base + self.target_scale[:H] * np.einsum(
            "...hpd,hpd->...hp", x, self.beta[:H], optimize=False
        )
        return mu, self.raw_scale(six)


def fit_spatial(training, arm, spec):
    features = spatial_features(training["features"], arm)
    N, H, P, D = features.shape
    sx = np.maximum(np.sqrt(np.mean(features**2, axis=0)), 1e-6)
    sy = np.maximum(
        np.sqrt(np.mean((training["target"] - training["base"]) ** 2, axis=0)),
        spec["probability"]["sigma_floor_mm"],
    )
    x = features / sx
    target = (training["target"] - training["base"]) / sy
    beta = np.empty((H, P, D))
    for h in range(H):
        for p in range(P):
            a = x[:, h, p]
            gram = np.einsum("ni,nj->ij", a, a, optimize=False) / N + spec[
                "alpha"
            ] * np.eye(D)
            rhs = np.einsum("ni,n->i", a, target[:, h, p], optimize=False) / N
            beta[h, p] = solve(gram, rhs, assume_a="pos")
    residual = target - np.einsum("nhpd,hpd->nhp", x, beta, optimize=False)
    q = np.sqrt(np.mean(training["features"][..., :4] ** 2, axis=-1)) / sy
    cfg = spec["ridge"]
    logs = []
    records = []
    for p in range(P):
        op = minimize(
            scale_objective,
            np.array(cfg["scale_initial_logs"]),
            args=(
                residual[:, :, p],
                q[:, :, p],
                spec["probability"]["sigma_floor_mm"] / sy[None, :, p],
            ),
            method="L-BFGS-B",
            jac=True,
            bounds=[cfg["scale_log_bounds"]] * 2,
            options=dict(
                maxiter=cfg["scale_maxiter"],
                maxfun=cfg["scale_maxfun"],
                ftol=cfg["scale_ftol"],
                gtol=cfg["scale_gtol"],
            ),
        )
        if not np.isfinite(op.x).all() or not np.isfinite(op.fun):
            raise ArithmeticError("Nonfinite spatial scale fit")
        logs.append(op.x.tolist())
        records.append(
            dict(
                point=p,
                success=bool(op.success),
                message=str(op.message),
                iterations=int(op.nit),
                function_calls=int(op.nfev),
                objective=float(op.fun),
                gradient=op.jac.tolist(),
                logs=op.x.tolist(),
            )
        )
    state = dict(
        family="spatial_ridge_dynamic",
        arm=arm,
        dimensions=D,
        alpha=spec["alpha"],
        feature_scale=sx.tolist(),
        target_scale=sy.tolist(),
        beta=beta.tolist(),
        scale_logs=logs,
        scale_optimizer_records=records,
        sigma_floor_mm=spec["probability"]["sigma_floor_mm"],
        training_samples=N,
        linear_solves=H * P,
        spatial_feature_sources=feature_sources(arm),
    )
    return SpatialDistribution(state)


def phase_run(spec, phase, current, training, issued, out, recorder):
    start, end = spec["stages"][phase]
    N, H = end - start, spec["horizons"]
    prob = spec["probability"]
    stream = ObservationStream(ROOT / spec["data"], start, end)
    targets = training["origins"][:, None] + np.arange(H)[None]
    if targets.max() >= start:
        raise ValueError("Training crosses prefix")
    np.testing.assert_array_equal(training["target"], stream.history[targets])
    np.testing.assert_array_equal(issued["origins"], np.arange(start, end))
    out.mkdir(parents=True)
    models = {}
    states = {}
    calibrators = {}
    logs = {}
    records = {
        name: {k: v.copy() for k, v in pred.items()} for name, pred in current.items()
    }
    np.savez_compressed(out / "initial_training.npz", **training)
    np.savez_compressed(out / "original_issued_features.npz", **issued)
    for arm in spec["ridge"]["arms"]:
        model = fit_spatial(training, arm, spec)
        models[arm] = model
        save_json(out / ("model_" + arm + ".json"), model.state)
        fit_mu, fit_sigma = model.predict_features(
            training["features"], training["base"]
        )
        np.savez_compressed(
            out / ("fit_" + arm + ".npz"),
            mean=fit_mu,
            raw_sigma=fit_sigma,
            origins=training["origins"],
        )
        transformed = spatial_features(training["features"], arm)
        states[arm] = OnlineRidge(
            model, transformed, training["base"], training["target"], start
        )
        calibrators[arm] = CausalCalibration(
            H,
            prob["window"],
            prob["prior_count"],
            prob["prior_sum_squares"],
            feedback=prob["feedback"],
        )
        logs[arm] = dict(
            features=np.full((N, H, 4, model.dimensions), np.nan),
            beta=np.zeros((N, H, 4, model.dimensions)),
        )
        records["C15_SPATIAL_" + arm] = dict(
            origins=np.arange(start, end),
            teacher_prefixes=np.full(N, start),
            **{
                k: np.full((N, H, 4), np.nan)
                for k in (
                    "mean",
                    "raw_sigma",
                    "sigma",
                    "calibration_factor",
                    "feedback_log_scale",
                )
            },
        )
    save_json(
        out / "training_contract.json",
        dict(
            rows=start,
            max_target=int(targets.max()),
            label_sha256=array_sha(stream.history),
            training_samples=len(training["origins"]),
            dimensions={arm: m.dimensions for arm, m in models.items()},
        ),
    )
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
            raise ValueError("Prediction crossed observations")
        valid = min(H, end - n)
        six = issued["features"][i, :valid]
        base = issued["base"][i, :valid]
        for arm, state in states.items():
            model, cal, log = models[arm], calibrators[arm], logs[arm]
            pred = records["C15_SPATIAL_" + arm]
            features = spatial_features(six, arm)
            log["features"][i, :valid] = features
            log["beta"][i] = state.beta
            mean = state.predict(features, base)
            raw = model.raw_scale(six)
            factor = cal.factors(n)[:valid]
            sigma = np.maximum(prob["sigma_floor_mm"], raw * factor)
            for k, v in dict(
                mean=mean,
                raw_sigma=raw,
                sigma=sigma,
                calibration_factor=factor,
                feedback_log_scale=cal.log_scale[:valid],
            ).items():
                pred[k][i, :valid] = v
            if not np.isfinite(np.stack([mean, raw, sigma])).all():
                raise ArithmeticError("Nonfinite spatial prediction")
        recorder.event(
            "forecast_locked",
            phase=phase,
            origin=n,
            history_sha256=array_sha(stream.history),
            predictions={
                name: array_sha(
                    np.stack([p[k][i, :valid] for k in ("mean", "sigma", "raw_sigma")])
                )
                for name, p in records.items()
            },
            inputs={
                arm: dict(
                    features=array_sha(log["features"][i, :valid]),
                    beta=array_sha(log["beta"][i]),
                )
                for arm, log in logs.items()
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
                pred = records["C15_SPATIAL_" + arm]
                calibrators[arm].update(
                    k,
                    observed - pred["mean"][j, k],
                    pred["raw_sigma"][j, k],
                    n,
                    n + 1,
                    issued_sigma=pred["sigma"][j, k],
                )
                state.update(
                    k,
                    logs[arm]["features"][j, k],
                    issued["base"][j, k],
                    observed,
                    start + j,
                    n,
                    n + 1,
                )
    frames = []
    for name, pred in records.items():
        frame = score_predictions(pred, stream.history, prob["levels"])
        if name == "B_RAW":
            for key in frame:
                if key not in ("horizon", "point", "n", "mae", "rmse"):
                    frame[key] = np.nan
        frame.insert(0, "model", name)
        frames.append(frame)
        np.savez_compressed(out / (name + ".npz"), **pred)
    for arm, state in states.items():
        np.savez_compressed(
            out / ("learning_" + arm + ".npz"),
            **logs[arm],
            initial_gram=state.initial_gram,
            initial_rhs=state.initial_rhs,
            final_gram=state.gram,
            final_rhs=state.rhs,
            final_beta=state.beta,
            updates=state.updates,
            last_target=state.last_target,
            penalty=state.penalty,
        )
    save_json(
        out / "calibration.json",
        {arm: cal.feedback_summary() for arm, cal in calibrators.items()},
    )
    metrics = pd.concat(frames, ignore_index=True)
    summary = aggregate(metrics)
    metrics.to_csv(out / "metrics.csv", index=False, float_format="%.12g")
    summary.to_csv(out / "summary.csv", index=False, float_format="%.12g")
    decision = gate(metrics, summary, spec["candidate"], spec["simple_baseline"], spec)
    ops = [r for m in models.values() for r in m.state["scale_optimizer_records"]]
    decision.update(
        initial_point_solves=sum(m.state["linear_solves"] for m in models.values()),
        online_point_solves=sum(s.point_solves for s in states.values()),
        scale_fits=len(ops),
        scale_objective_calls=sum(r["function_calls"] for r in ops),
        scale_iterations=sum(r["iterations"] for r in ops),
        new_neural_updates=0,
        physical_calls=0,
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
                initial_solves=decision["initial_point_solves"],
                online_solves=decision["online_point_solves"],
                scale_fits=len(ops),
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
    ):
        raise ValueError("Frozen input or exposure changed")
    prior = ROOT / spec["prior_verification"]
    if (
        sha(prior) != spec["prior_verification_sha256"]
        or not json.loads(prior.read_text())["passed"]
    ):
        raise ValueError("Missing core causal verification")
    sources = [
        Path(config).resolve(),
        ROOT / spec["plan"],
        ROOT / spec["data"],
        prior,
        ROOT / "code/physics_guided/reference.py",
    ]
    sources += list((ROOT / "code/rolling_probability").glob("*.py")) + list(
        (ROOT / "tests").glob("test_rolling_*.py")
    )
    loaded = {}
    for phase, ref in spec["sources"].items():
        root = checked_run(ref["current_run"], ref["current_manifest_sha256"])
        current = {}
        for p in sorted((root / ref["current_phase"]).glob("*.npz")):
            pred = arrays(p)
            if {"origins", "mean", "sigma", "raw_sigma"} <= pred.keys():
                current[p.stem] = pred
                sources.append(p)
        if not {"C8_ONLINE_FULL", "C8_ONLINE_DATA", "DRIFT1"} <= current.keys():
            raise ValueError("Missing frozen controls")
        for key in ("training", "issued"):
            p = ROOT / ref[key + "_path"]
            if sha(p) != ref[key + "_sha256"]:
                raise ValueError("Changed " + key + " source")
            sources.append(p)
        loaded[phase] = (
            current,
            arrays(ROOT / ref["training_path"]),
            arrays(ROOT / ref["issued_path"]),
        )
        sources.append(root / "artifact_manifest.json")
    hashes = {str(p.relative_to(ROOT)): sha(p) for p in sources}
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
            files=hashes,
        ),
    )
    recorder = Recorder(out)
    decisions = {
        phase: phase_run(spec, phase, *data, out / phase, recorder)
        for phase, data in loaded.items()
    }
    totals = {
        key: sum(d[key] for d in decisions.values())
        for key in (
            "initial_point_solves",
            "online_point_solves",
            "scale_fits",
            "scale_objective_calls",
            "scale_iterations",
        )
    }
    for key, wanted in [
        ("initial_point_solves", "expected_initial_solves"),
        ("online_point_solves", "expected_online_solves"),
        ("scale_fits", "expected_scale_fits"),
    ]:
        if totals[key] != spec[wanted]:
            raise ValueError("Unexpected " + key + " count")
    save_json(
        out / "decision.json",
        dict(
            candidate=spec["candidate"],
            phases=decisions,
            **totals,
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
    started = time.monotonic()
    status = dict(
        state="running",
        pid=os.getpid(),
        started_utc=utc(),
        deadline=spec["deadline_utc"],
    )
    save_json(args.out / "status.json", status)

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
