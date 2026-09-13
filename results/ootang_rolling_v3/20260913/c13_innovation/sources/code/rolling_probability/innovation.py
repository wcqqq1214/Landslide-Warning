"""C13: learn future core errors using only already matured forecast errors."""

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
from .data import ObservationStream, array_sha
from .empirical import Recorder, checked_run, utc
from .horizon_scale import arrays
from .scoring import aggregate, gate, score_predictions


class ErrorRidge:
    def __init__(self, horizons, dimensions, start, alpha):
        self.start = start
        self.gram = np.broadcast_to(
            alpha * np.eye(dimensions), (horizons, 4, dimensions, dimensions)
        ).copy()
        self.rhs = np.zeros((horizons, 4, dimensions))
        self.beta = np.zeros_like(self.rhs)
        self.last_target = np.full(horizons, start - 1, int)
        self.updates = np.zeros(horizons, int)

    def update(self, k, feature, response, issued_origin, target, next_origin):
        if (
            issued_origin < self.start + k + 1
            or target != issued_origin + k
            or target >= next_origin
            or target <= self.last_target[k]
        ):
            raise ValueError(
                "Unissued, unavailable, premature or duplicate error supervision"
            )
        if feature.shape != self.rhs[k].shape or response.shape != (4,):
            raise ValueError("Invalid error feature shape")
        if not np.isfinite(feature).all() or not np.isfinite(response).all():
            raise ArithmeticError("Nonfinite error supervision")
        self.gram[k] += np.einsum("pi,pj->pij", feature, feature, optimize=False)
        self.rhs[k] += feature * response[:, None]
        for p in range(4):
            self.beta[k, p] = solve(self.gram[k, p], self.rhs[k, p], assume_a="pos")
        if not np.isfinite(self.beta[k]).all():
            raise ArithmeticError("Invalid error coefficients")
        self.last_target[k] = target
        self.updates[k] += 1

    def state(self):
        return {
            k: getattr(self, k).tolist()
            for k in ("gram", "rhs", "beta", "last_target", "updates")
        }


def error_features(history, current, references, start, origin, valid):
    if len(history) != origin:
        raise ValueError("Error input crosses observed prefix")
    features = np.zeros((valid, 4, len(references)))
    available = np.zeros(valid, bool)
    for k in range(valid):
        previous = origin - k - 1
        if previous < start:
            continue
        j = previous - start
        for d, name in enumerate(references):
            pred = current[name]
            if pred["origins"][j] != previous:
                raise ValueError("Incorrect previous forecast origin")
            features[k, :, d] = (history[-1] - pred["mean"][j, k]) / pred["sigma"][j, k]
        available[k] = True
    if not np.isfinite(features).all():
        raise ArithmeticError("Nonfinite issued error features")
    return features, available


def phase_run(spec, phase, current, out, recorder):
    start, end = spec["stages"][phase]
    H, N = spec["horizons"], end - start
    core = current[spec["core_model"]]
    stream = ObservationStream(ROOT / spec["data"], start, end)
    records = {name: {k: v.copy() for k, v in r.items()} for name, r in current.items()}
    states, issued = {}, {}
    out.mkdir(parents=True)
    for name, refs in spec["innovation_references"].items():
        states[name] = ErrorRidge(H, len(refs), start, spec["innovation_alpha"])
        pred = {
            k: core[k].copy()
            for k in ("origins", "teacher_prefixes", "mean", "raw_sigma", "sigma")
        }
        pred["core_mean"] = core["mean"].copy()
        pred["core_calibration_factor"] = core["calibration_factor"].copy()
        pred["core_feedback_log_scale"] = core["feedback_log_scale"].copy()
        records[name] = pred
        issued[name] = dict(
            features=np.full((N, H, 4, len(refs)), np.nan),
            beta=np.zeros((N, H, 4, len(refs))),
            available=np.zeros((N, H), bool),
        )
    recorder.event(
        "phase_started",
        phase=phase,
        training_prefix=start,
        end=end,
        inherited_core=spec["core_model"],
        models=list(records),
    )
    for i, n in enumerate(range(start, end)):
        valid = min(H, end - n)
        for name, refs in spec["innovation_references"].items():
            feature, available = error_features(
                stream.history, current, refs, start, n, valid
            )
            pred, state, log = records[name], states[name], issued[name]
            log["features"][i, :valid], log["available"][i, :valid] = feature, available
            log["beta"][i] = state.beta
            correction = np.einsum(
                "hpd,hpd->hp", feature, state.beta[:valid], optimize=False
            )
            pred["mean"][i, :valid] = (
                core["mean"][i, :valid] + core["sigma"][i, :valid] * correction
            )
            if not np.isfinite(pred["mean"][i, :valid]).all():
                raise ArithmeticError("Invalid issued mean")
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
            inputs={
                name: dict(
                    features=array_sha(log["features"][i, :valid]),
                    available=array_sha(log["available"][i, :valid]),
                    beta=array_sha(log["beta"][i]),
                )
                for name, log in issued.items()
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
            response = (observed - core["mean"][j, k]) / core["sigma"][j, k]
            for name, state in states.items():
                if issued[name]["available"][j, k]:
                    state.update(
                        k, issued[name]["features"][j, k], response, start + j, n, n + 1
                    )
    frames = []
    for name, pred in records.items():
        frame = score_predictions(pred, stream.history, spec["probability"]["levels"])
        if name == "B_RAW":
            for col in frame:
                if col not in ("horizon", "point", "n", "mae", "rmse"):
                    frame[col] = np.nan
        frame.insert(0, "model", name)
        frames.append(frame)
        np.savez_compressed(out / (name + ".npz"), **pred)
        if name in issued:
            np.savez_compressed(out / (name + "_learning.npz"), **issued[name])
    metrics = pd.concat(frames, ignore_index=True)
    summary = aggregate(metrics)
    metrics.to_csv(out / "metrics.csv", index=False, float_format="%.12g")
    summary.to_csv(out / "summary.csv", index=False, float_format="%.12g")
    save_json(
        out / "learning_state.json",
        {name: state.state() for name, state in states.items()},
    )
    decision = gate(metrics, summary, spec["candidate"], spec["simple_baseline"], spec)
    count = int(sum(s.updates.sum() for s in states.values()) * 4)
    expected = len(states) * 4 * sum(N - 2 * h + 1 for h in range(1, H + 1))
    if count != expected:
        raise ValueError("Error supervision count differs")
    decision.update(
        point_solves=count,
        new_initial_fits=0,
        new_neural_updates=0,
        new_scale_optimizations=0,
        physical_calls=0,
        frozen_core_sigma=True,
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
                point_solves=count,
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
        or spec["uncertainty"] != "frozen_core_sigma"
    ):
        raise ValueError("Frozen input or exposure contract changed")
    prior = ROOT / spec["prior_verification"]
    if (
        sha(prior) != spec["prior_verification_sha256"]
        or not json.loads(prior.read_text())["passed"]
    ):
        raise ValueError("Core verification changed")
    diagnostic = ROOT / spec["diagnostic_source"]
    if sha(diagnostic) != spec["diagnostic_sha256"]:
        raise ValueError("Diagnostic source changed")
    sources = [
        Path(config).resolve(),
        ROOT / spec["plan"],
        ROOT / spec["data"],
        prior,
        diagnostic,
        ROOT / "code/physics_guided/reference.py",
    ]
    sources += list((ROOT / "code/rolling_probability").glob("*.py"))
    sources += list((ROOT / "tests").glob("test_rolling_*.py"))
    loaded = {}
    for phase, ref in spec["sources"].items():
        root = checked_run(ref["current_run"], ref["current_manifest_sha256"])
        records = {}
        for p in sorted((root / ref["current_phase"]).glob("*.npz")):
            pred = arrays(p)
            if {"origins", "mean", "sigma", "raw_sigma"} <= pred.keys():
                records[p.stem] = pred
                sources.append(p)
        required = {spec["core_model"], "DRIFT1"} | {
            r for refs in spec["innovation_references"].values() for r in refs
        }
        if not required <= records.keys():
            raise ValueError("Missing frozen reference")
        loaded[phase] = records
        sources.append(root / "artifact_manifest.json")
    hashes = {str(p.relative_to(ROOT)): sha(p) for p in sources}
    for p in sources:
        if p.suffix in (".json", ".md", ".py"):
            dest = out / "sources" / p.relative_to(ROOT)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, dest)
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
        phase: phase_run(spec, phase, current, out / phase, recorder)
        for phase, current in loaded.items()
    }
    count = sum(d["point_solves"] for d in decisions.values())
    if count != spec["expected_point_solves"]:
        raise ValueError("Total error solves differ")
    save_json(
        out / "decision.json",
        dict(
            candidate=spec["candidate"],
            phases=decisions,
            point_solves=count,
            new_initial_fits=0,
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
