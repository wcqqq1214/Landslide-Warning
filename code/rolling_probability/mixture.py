"""C11: learn a symmetric reference-scale mixture from matured CRPS losses."""

import argparse
from collections import deque
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
from scipy.special import ndtr, ndtri

from physics_guided.reference import ROOT, save_json, sha
from .data import ObservationStream, array_sha
from .empirical import Recorder, checked_run, utc
from .horizon_scale import arrays
from .scoring import POINTS, aggregate, gate, score_predictions


def absolute_normal(error, sigma):
    z = error / sigma
    return sigma * np.sqrt(2 / np.pi) * np.exp(-z * z / 2) + error * (2 * ndtr(z) - 1)


def loss_coefficients(error, core, wide):
    cross = np.sqrt(2 * (core * core + wide * wide))
    linear = (
        absolute_normal(error, wide)
        - absolute_normal(error, core)
        + (2 * core - cross) / np.sqrt(np.pi)
    )
    quadratic = (wide - core) ** 2 / (np.sqrt(np.pi) * (cross + core + wide))
    return linear, quadratic


def mixture_crps(error, core, wide, weight):
    linear, quadratic = loss_coefficients(error, core, wide)
    return (
        absolute_normal(error, core)
        - core / np.sqrt(np.pi)
        + weight * linear
        + weight * weight * quadratic
    )


def central_radius(core, wide, weight, level, iterations=60):
    probability = (1 + level) / 2
    low = np.zeros_like(core)
    high = np.maximum(core, wide) * ndtri(probability)
    for _ in range(iterations):
        middle = (low + high) / 2
        cdf = (1 - weight) * ndtr(middle / core) + weight * ndtr(middle / wide)
        smaller = cdf < probability
        low = np.where(smaller, middle, low)
        high = np.where(smaller, high, middle)
    return (low + high) / 2


class MixtureWeights:
    def __init__(self, horizons, window, start):
        self.pools = [deque(maxlen=window) for _ in range(horizons)]
        self.weights = np.zeros((horizons, 4))
        self.last_target = np.full(horizons, start - 1, int)
        self.updates = np.zeros(horizons, int)
        self.start = start

    def update(self, k, error, core, wide, issued_origin, target, next_origin):
        if (
            issued_origin < self.start
            or target != issued_origin + k
            or target >= next_origin
            or target <= self.last_target[k]
        ):
            raise ValueError(
                "Future, unissued, wrong-horizon or duplicate mixture supervision"
            )
        if (
            not np.isfinite(np.stack([error, core, wide])).all()
            or (core <= 0).any()
            or (wide < core).any()
        ):
            raise ValueError("Invalid mixture scale or supervision")
        linear, quadratic = loss_coefficients(error, core, wide)
        self.pools[k].append(np.stack([linear, quadratic]))
        total = np.sum(self.pools[k], axis=0)
        weight = np.divide(-total[0], 2 * total[1], out=np.zeros(4), where=total[1] > 0)
        self.weights[k] = np.clip(weight, 0, 1)
        self.last_target[k] = target
        self.updates[k] += 1

    def state(self):
        return dict(
            weights=self.weights.tolist(),
            last_target=self.last_target.tolist(),
            updates=self.updates.tolist(),
            coefficient_pools=[list(map(lambda a: a.tolist(), p)) for p in self.pools],
        )


def mixture_scores(pred, labels, levels):
    rows = []
    start, N, H = int(pred["origins"][0]), *pred["mean"].shape[:2]
    for k in range(H):
        count = N - k
        error = labels[start + k : start + N] - pred["mean"][:count, k]
        loss = mixture_crps(
            error,
            pred["core_sigma"][:count, k],
            pred["wide_sigma"][:count, k],
            pred["weight"][:count, k],
        )
        values = dict(
            mae=abs(error).mean(axis=0),
            rmse=np.sqrt((error * error).mean(axis=0)),
            crps=loss.mean(axis=0),
        )
        y = labels[start + k : start + N]
        for level in levels:
            percent = round(100 * level)
            lo, hi = (
                pred[f"lower{percent}"][:count, k],
                pred[f"upper{percent}"][:count, k],
            )
            width = hi - lo
            miss = np.where(y < lo, lo - y, np.where(y > hi, y - hi, 0))
            values[f"coverage{percent}"] = ((lo <= y) & (y <= hi)).mean(axis=0)
            values[f"width{percent}"] = width.mean(axis=0)
            values[f"interval_score{percent}"] = (width + 2 * miss / (1 - level)).mean(
                axis=0
            )
        for p, point in enumerate(POINTS):
            rows.append(
                dict(
                    horizon=k + 1,
                    point=point,
                    n=count,
                    **{key: float(value[p]) for key, value in values.items()},
                )
            )
    return pd.DataFrame(rows)


def phase_run(spec, phase, current, out, recorder):
    start, end = spec["stages"][phase]
    H = spec["horizons"]
    core = current[spec["core_model"]]
    stream = ObservationStream(ROOT / spec["data"], start, end)
    out.mkdir(parents=True)
    records = {name: {k: v.copy() for k, v in r.items()} for name, r in current.items()}
    learners = {}
    for name in spec["reference_models"]:
        records[name] = {
            k: core[k].copy()
            for k in ("origins", "teacher_prefixes", "mean", "raw_sigma")
        }
        records[name].update(
            core_sigma=core["sigma"].copy(),
            core_calibration_factor=core["calibration_factor"].copy(),
            core_feedback_log_scale=core["feedback_log_scale"].copy(),
        )
        records[name].update(
            {
                k: np.full_like(core["mean"], np.nan)
                for k in ("wide_sigma", "weight", "sigma")
            }
        )
        learners[name] = MixtureWeights(H, spec["mixture_window"], start)
    recorder.event(
        "phase_started",
        phase=phase,
        training_prefix=start,
        end=end,
        models=list(records),
        inherited_core=spec["core_model"],
    )
    for i, n in enumerate(range(start, end)):
        if len(stream.history) != n:
            raise ValueError("Mixture forecast crossed the observed prefix")
        valid = min(H, end - n)
        for name, reference in spec["reference_models"].items():
            pred, ref = records[name], current[reference]
            c = pred["core_sigma"][i, :valid]
            mu = pred["mean"][i, :valid]
            wide = np.maximum(
                c,
                np.sqrt(
                    ref["sigma"][i, :valid] ** 2 + (ref["mean"][i, :valid] - mu) ** 2
                ),
            )
            weight = learners[name].weights[:valid]
            sigma = np.sqrt((1 - weight) * c * c + weight * wide * wide)
            if not np.isfinite(np.stack([wide, weight, sigma])).all():
                raise ArithmeticError("Invalid issued mixture")
            for key, value in dict(wide_sigma=wide, weight=weight, sigma=sigma).items():
                pred[key][i, :valid] = value
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
            mixture_parameters={
                name: array_sha(
                    np.stack(
                        [
                            records[name][k][i, :valid]
                            for k in ("mean", "core_sigma", "wide_sigma", "weight")
                        ]
                    )
                )
                for name in learners
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
            for name, learner in learners.items():
                r = records[name]
                learner.update(
                    k,
                    observed - r["mean"][j, k],
                    r["core_sigma"][j, k],
                    r["wide_sigma"][j, k],
                    start + j,
                    n,
                    n + 1,
                )
    rows = []
    for name, pred in records.items():
        if name in learners:
            for level in spec["probability"]["levels"]:
                radius = central_radius(
                    pred["core_sigma"],
                    pred["wide_sigma"],
                    pred["weight"],
                    level,
                    spec["quantile_bisection_steps"],
                )
                percent = round(100 * level)
                pred[f"lower{percent}"], pred[f"upper{percent}"] = (
                    pred["mean"] - radius,
                    pred["mean"] + radius,
                )
            frame = mixture_scores(pred, stream.history, spec["probability"]["levels"])
        else:
            frame = score_predictions(
                pred, stream.history, spec["probability"]["levels"]
            )
            if name == "B_RAW":
                for key in frame:
                    if key not in ("horizon", "point", "n", "mae", "rmse"):
                        frame[key] = np.nan
        np.savez_compressed(out / f"{name}.npz", **pred)
        frame.insert(0, "model", name)
        rows.append(frame)
    metrics = pd.concat(rows, ignore_index=True)
    summary = aggregate(metrics)
    metrics.to_csv(out / "metrics.csv", index=False, float_format="%.12g")
    summary.to_csv(out / "summary.csv", index=False, float_format="%.12g")
    save_json(
        out / "mixture_state.json",
        {name: learner.state() for name, learner in learners.items()},
    )
    decision = gate(metrics, summary, spec["candidate"], spec["simple_baseline"], spec)
    decision.update(
        mature_point_updates=int(
            sum(learner.updates.sum() for learner in learners.values()) * 4
        ),
        new_mean_updates=0,
        new_neural_updates=0,
        new_scale_optimizations=0,
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
        or not spec["post_transfer_exposure"]
        or spec["original_single_transfer_reused"]
    ):
        raise ValueError("Frozen data or exposure rule changed")
    if spec["mixture_initial_weight"] != 0 or spec["mixture_weight_bounds"] != [
        0.0,
        1.0,
    ]:
        raise ValueError("Mixture weight contract changed")
    prior = ROOT / spec["prior_verification"]
    if (
        sha(prior) != spec["prior_verification_sha256"]
        or not json.loads(prior.read_text())["passed"]
    ):
        raise ValueError("Prior causal verification changed")
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
        records, paths = {}, []
        for p in sorted((root / ref["current_phase"]).glob("*.npz")):
            values = arrays(p)
            if {"mean", "sigma", "raw_sigma", "origins"} <= values.keys():
                records[p.stem] = values
                paths.append(p)
        if (
            not {spec["core_model"], *spec["reference_models"].values(), "DRIFT1"}
            <= records.keys()
        ):
            raise ValueError("Missing core or reference forecasts")
        loaded[phase] = records
        sources += paths + [root / "artifact_manifest.json"]
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
    decisions = {
        phase: phase_run(spec, phase, current, out / phase, recorder)
        for phase, current in loaded.items()
    }
    count = sum(d["mature_point_updates"] for d in decisions.values())
    if count != spec["expected_mature_point_updates"]:
        raise ValueError("Mixture update count differs")
    save_json(
        out / "decision.json",
        dict(
            candidate=spec["candidate"],
            phases=decisions,
            mature_point_updates=count,
            new_mean_updates=0,
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
