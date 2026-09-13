"""C8: expand ridge sufficient statistics only after issued targets mature."""

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

from physics_guided.reference import ROOT, save_json, sha
from .data import (
    ObservationStream,
    array_sha,
    example,
    load_teachers,
    select_teacher,
    training_examples,
)
from .empirical import Recorder, checked_run, load_forecasts, utc
from .ridge import RidgeDistribution, dynamic_features
from .scoring import CausalCalibration, aggregate, gate, score_predictions


class OnlineRidge:
    def __init__(self, model, features, base, target, start, forgetting=1.0):
        if not np.isfinite(forgetting) or not 0 < forgetting <= 1:
            raise ValueError("Forgetting factor must be in (0, 1]")
        self.model = model
        self.start = start
        self.forgetting = float(forgetting)
        d = model.dimensions
        normalized = features[..., :d] / model.feature_scale[None]
        response = (target - base) / model.target_scale[None]
        self.gram = np.einsum("nhpi,nhpj->hpij", normalized, normalized, optimize=False)
        self.penalty = len(features) * model.state["alpha"] * np.eye(d)
        self.gram += self.penalty
        self.rhs = np.einsum("nhpi,nhp->hpi", normalized, response, optimize=False)
        self.beta = model.beta.copy()
        self.initial_gram = self.gram.copy()
        self.initial_rhs = self.rhs.copy()
        self.last_target = np.full(len(self.beta), start - 1, dtype=int)
        self.updates = np.zeros(len(self.beta), dtype=int)
        self.effective_weight = np.full(len(self.beta), float(len(features)))
        self.point_solves = 0

    def predict(self, features, base):
        h = len(features)
        x = (
            features[None, ..., : self.model.dimensions]
            / self.model.feature_scale[None, :h]
        )
        delta = np.einsum("nhpd,hpd->nhp", x, self.beta[:h], optimize=False)[0]
        return base + delta * self.model.target_scale[:h]

    def update(self, k, features, base, observed, issued_origin, target, next_origin):
        if (
            issued_origin < self.start
            or target != issued_origin + k
            or target >= next_origin
            or target <= self.last_target[k]
        ):
            raise ValueError(
                "A future, unissued, or duplicate target entered online learning"
            )
        x = features[..., : self.model.dimensions] / self.model.feature_scale[k]
        response = (observed - base) / self.model.target_scale[k]
        if not np.isfinite(x).all() or not np.isfinite(response).all():
            raise ArithmeticError("Nonfinite online supervision")
        if self.forgetting != 1.0:
            self.gram[k] *= self.forgetting
            self.gram[k] += (1 - self.forgetting) * self.penalty
            self.rhs[k] *= self.forgetting
        self.gram[k] += np.einsum("pi,pj->pij", x, x, optimize=False)
        self.rhs[k] += x * response[:, None]
        for p in range(4):
            self.beta[k, p] = solve(self.gram[k, p], self.rhs[k, p], assume_a="pos")
            self.point_solves += 1
        if not np.isfinite(self.beta[k]).all():
            raise ArithmeticError("Nonfinite online coefficients")
        self.last_target[k] = target
        self.updates[k] += 1
        self.effective_weight[k] = self.forgetting * self.effective_weight[k] + 1


def phase_run(spec, phase, current, pool, models, out, recorder):
    start, end = spec["stages"][phase]
    H = spec["horizons"]
    N = end - start
    stream = ObservationStream(ROOT / spec["data"], start, end)
    data = training_examples(
        stream.history, pool, H, spec["history_days"], spec["extra_baselines"]
    )
    experts = np.stack([data["baselines"][k] for k in spec["expert_names"]], axis=2)
    training_features, training_base, _ = dynamic_features(data["x"], experts)
    states = {
        arm: OnlineRidge(model, training_features, training_base, data["y"], start)
        for arm, model in models.items()
    }
    for arm, model in models.items():
        sx = np.maximum(
            np.sqrt(np.mean(training_features[..., : model.dimensions] ** 2, axis=0)),
            1e-6,
        )
        sy = np.maximum(
            np.sqrt(np.mean((data["y"] - training_base) ** 2, axis=0)),
            spec["probability"]["sigma_floor_mm"],
        )
        np.testing.assert_array_equal(sx, model.feature_scale)
        np.testing.assert_array_equal(sy, model.target_scale)
        if len(training_features) != model.state["training_samples"]:
            raise ValueError("Training sample set changed")
    out.mkdir(parents=True)
    np.savez_compressed(
        out / "initial_training.npz",
        features=training_features,
        base=training_base,
        target=data["y"],
        origins=data["origins"],
        teachers=data["teachers"],
    )
    save_json(
        out / "initial_contract.json",
        dict(
            training_rows=start,
            label_sha256=array_sha(stream.history),
            training_samples=len(training_features),
            initial_regularization={
                arm: len(training_features) * model.state["alpha"]
                for arm, model in models.items()
            },
        ),
    )
    records = {
        ("C8_STATIC_" + name if name in ("FULL", "DATA") else name): {
            k: v.copy() for k, v in value.items()
        }
        for name, value in current.items()
    }
    prob = spec["probability"]
    calibrators = {}
    for arm in models:
        name = "C8_ONLINE_" + arm
        records[name] = dict(
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
        calibrators[name] = CausalCalibration(
            H,
            prob["window"],
            prob["prior_count"],
            prob["prior_sum_squares"],
            feedback=prob["feedback"],
        )
    features_issued = np.full((N, H, 4, 6), np.nan)
    bases_issued = np.full((N, H, 4), np.nan)
    betas = {arm: np.empty((N,) + state.beta.shape) for arm, state in states.items()}
    recorder.event(
        "phase_started",
        phase=phase,
        training_prefix=start,
        end=end,
        models=list(records),
    )
    for i, n in enumerate(range(start, end)):
        if len(stream.history) != n:
            raise ValueError("Observation stream crossed the origin")
        teacher = select_teacher(pool, n)
        x, z, bases = example(
            stream.history, teacher, H, spec["history_days"], spec["extra_baselines"]
        )
        valid = min(H, end - n)
        z = z[:valid]
        bases = {k: v[:valid] for k, v in bases.items()}
        expert = np.stack([bases[k] for k in spec["expert_names"]], axis=1)[None]
        features, base, _ = dynamic_features(x[None], expert)
        features = features[0]
        base = base[0]
        features_issued[i, :valid] = features
        bases_issued[i, :valid] = base
        for name, means in bases.items():
            np.testing.assert_array_equal(means, current[name]["mean"][i, :valid])
        for arm, state in states.items():
            model = models[arm]
            old_mu, old_sd = model.predict_raw(
                x[None], z[None], bases["B_ANCHOR"][None], expert
            )
            np.testing.assert_array_equal(old_mu[0], current[arm]["mean"][i, :valid])
            np.testing.assert_array_equal(
                np.sqrt(old_sd[0] ** 2), current[arm]["raw_sigma"][i, :valid]
            )
            name = "C8_ONLINE_" + arm
            record = records[name]
            cal = calibrators[name]
            betas[arm][i] = state.beta
            mean = state.predict(features, base)
            if i == 0:
                np.testing.assert_array_equal(mean, current[arm]["mean"][0, :valid])
            raw = current[arm]["raw_sigma"][i, :valid]
            factors = cal.factors(n)[:valid]
            sd = np.maximum(prob["sigma_floor_mm"], raw * factors)
            if not np.isfinite(mean).all() or not np.isfinite(sd).all():
                raise ArithmeticError("Nonfinite online forecast")
            for key, val in dict(
                mean=mean,
                raw_sigma=raw,
                sigma=sd,
                calibration_factor=factors,
                feedback_log_scale=cal.log_scale[:valid],
            ).items():
                record[key][i, :valid] = val
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
            issued_i = i - k
            for arm, state in states.items():
                name = "C8_ONLINE_" + arm
                r = records[name]
                calibrators[name].update(
                    k,
                    observed - r["mean"][issued_i, k],
                    r["raw_sigma"][issued_i, k],
                    n,
                    n + 1,
                    issued_sigma=r["sigma"][issued_i, k],
                )
                state.update(
                    k,
                    features_issued[issued_i, k],
                    bases_issued[issued_i, k],
                    observed,
                    start + issued_i,
                    n,
                    n + 1,
                )
    np.savez_compressed(
        out / "issued_features.npz",
        features=features_issued,
        base=bases_issued,
        origins=np.arange(start, end),
    )
    for arm, state in states.items():
        np.savez_compressed(
            out / f"online_state_{arm}.npz",
            beta_issued=betas[arm],
            initial_gram=state.initial_gram,
            initial_rhs=state.initial_rhs,
            final_gram=state.gram,
            final_rhs=state.rhs,
            final_beta=state.beta,
            updates=state.updates,
            last_target=state.last_target,
        )
    rows = []
    for name, r in records.items():
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
    feedback = {name: cal.feedback_summary() for name, cal in calibrators.items()}
    save_json(out / "feedback_state.json", feedback)
    decision = gate(metrics, summary, spec["candidate"], spec["simple_baseline"], spec)
    decision.update(
        online_point_solves=sum(s.point_solves for s in states.values()),
        new_neural_updates=0,
        new_scale_optimizations=0,
        physical_calls=0,
        post_exposure=True,
        independent_transfer=False,
        first_origin_matches_c4=True,
        all_static_distributions_unchanged=True,
    )
    save_json(out / "decision.json", decision)
    recorder.event("phase_completed", phase=phase, decision=decision)
    print(
        json.dumps(dict(phase=phase, decision=decision), ensure_ascii=False), flush=True
    )
    return decision


def execute(spec, config, out):
    if sha(ROOT / spec["data"]) != spec["data_sha256"]:
        raise ValueError("Frozen data changed")
    if not spec["post_transfer_exposure"] or spec["original_single_transfer_reused"]:
        raise ValueError("Study exposure status changed")
    sources = [
        Path(config).resolve(),
        ROOT / spec["plan"],
        ROOT / spec["data"],
        ROOT / "code/physics_guided/reference.py",
    ]
    sources += list((ROOT / "code/rolling_probability").glob("*.py")) + list(
        (ROOT / "tests").glob("test_rolling_*.py")
    )
    loaded = {}
    for phase, ref in spec["sources"].items():
        root = checked_run(ref["current_run"], ref["current_manifest_sha256"])
        current, paths = load_forecasts(
            root, ref["current_phase"], "C4_RIDGE_FULL", "C4_RIDGE_DATA"
        )
        cache = ROOT / ref["physics_cache"]
        if sha(cache / "provenance.json") != ref["physics_provenance_sha256"]:
            raise ValueError("Frozen physics cache changed")
        pool = load_teachers(cache)
        model_paths = [
            ROOT / ref["training_path"] / f"ridge_{arm}_a0.001.json"
            for arm in ("FULL", "DATA")
        ]
        models = {
            arm: RidgeDistribution.load(p)
            for arm, p in zip(("FULL", "DATA"), model_paths)
        }
        if any(m.state["alpha"] != spec["alpha"] for m in models.values()):
            raise ValueError("Ridge strength changed")
        loaded[phase] = current, pool, models
        sources += (
            paths
            + model_paths
            + [root / "artifact_manifest.json"]
            + list(cache.glob("*.json"))
            + list(cache.glob("*.npz"))
        )
    records = {str(p.relative_to(ROOT)): sha(p) for p in sources}
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
            files=records,
        ),
    )
    recorder = Recorder(out)
    decisions = {}
    for phase in spec["stages"]:
        current, pool, models = loaded[phase]
        decisions[phase] = phase_run(
            spec, phase, current, pool, models, out / phase, recorder
        )
    save_json(
        out / "decision.json",
        dict(
            candidate=spec["candidate"],
            phases=decisions,
            online_point_solves=sum(
                d["online_point_solves"] for d in decisions.values()
            ),
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
