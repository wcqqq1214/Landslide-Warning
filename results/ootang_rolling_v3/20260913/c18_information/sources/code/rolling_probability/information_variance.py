"""Fixed information-matrix variance proxy for frozen C16 regression means."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import signal
import time

import numpy as np
import pandas as pd

from physics_guided.reference import ROOT, save_json, sha
from .data import array_sha
from .empirical import Recorder, checked_run
from .horizon_scale import arrays
from .scoring import aggregate, gate, score_predictions


def variance_terms(features, available, units):
    n, h, points, dimensions = features.shape
    matrix = np.broadcast_to(
        np.eye(dimensions), (h, points, dimensions, dimensions)
    ).copy()
    values = np.full((n, h, points), np.nan)
    issued = np.full((n, h, points, dimensions, dimensions), np.nan)
    count = 0
    for i in range(n):
        for k in range(h):
            j = i - k - 1
            if j >= 0 and available[j, k]:
                x = features[j, k]
                matrix[k] += np.einsum("pi,pj->pij", x, x, optimize=False)
                count += points
        valid = min(h, n - i)
        x = features[i, :valid]
        solved = np.linalg.solve(matrix[:valid], x[..., None])[..., 0]
        values[i, :valid] = units[:valid] ** 2 * np.sum(x * solved, axis=-1)
        issued[i, :valid] = matrix[:valid]
        if np.any(values[i, :valid] < 0) or not np.isfinite(values[i, :valid]).all():
            raise ArithmeticError("Invalid information variance")
    return values, issued, count


def run(config, out):
    config = Path(config).resolve()
    spec = json.loads(config.read_text())
    start = datetime.now(timezone.utc)
    deadline = min(
        datetime.fromisoformat(spec[k])
        for k in ("deadline_utc", "candidate_deadline_utc")
    )
    if start >= deadline:
        raise TimeoutError("Original budget exhausted")
    if (
        spec["prior_precision"] != 1
        or spec["uncertainty"] != "ridge_information_variance_proxy"
    ):
        raise ValueError("Changed frozen variance rule")
    signal.signal(
        signal.SIGALRM,
        lambda *_: (_ for _ in ()).throw(TimeoutError("Candidate deadline")),
    )
    signal.alarm(
        max(
            1, int(min(spec["run_timeout_seconds"], (deadline - start).total_seconds()))
        )
    )
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    tic = time.perf_counter()
    save_json(out / "status.json", dict(state="running", started_utc=start.isoformat()))
    try:
        prior = ROOT / spec["prior_verification"]
        if (
            sha(prior) != spec["prior_verification_sha256"]
            or not json.loads(prior.read_text())["passed"]
        ):
            raise ValueError("Changed prior verification")
        if sha(ROOT / spec["data"]) != spec["data_sha256"]:
            raise ValueError("Changed observations")
        paths = [config, ROOT / spec["plan"], ROOT / spec["data"], prior]
        paths += list((ROOT / "code/rolling_probability").glob("*.py"))
        paths.append(ROOT / "code/physics_guided/reference.py")
        recorder = Recorder(out)
        phases, count = {}, 0
        for phase, (begin, end) in spec["stages"].items():
            ref = spec["sources"][phase]
            source = checked_run(ref["current_run"], ref["current_manifest_sha256"])
            src = source / ref["current_phase"]
            folder = out / phase
            folder.mkdir()
            paths.append(source / "artifact_manifest.json")
            paths.append(src / "normalization.json")
            units = np.asarray(
                json.loads((src / "normalization.json").read_text())["response_scales"]
            )
            current = {}
            for path in sorted(src.glob("*.npz")):
                z = arrays(path)
                if {"mean", "origins", "sigma", "raw_sigma"} <= z.keys():
                    current[path.stem] = z
                    paths.append(path)
            for name, original in spec["variance_sources"].items():
                path = src / (original + "_learning.npz")
                paths.append(path)
                learning = arrays(path)
                v, matrices, updates = variance_terms(
                    learning["features"], learning["available"], units
                )
                count += updates
                pred = {k: value.copy() for k, value in current[original].items()}
                pred["core_sigma"] = pred["sigma"].copy()
                pred["information_variance"] = v
                pred["sigma"] = np.sqrt(pred["core_sigma"] ** 2 + v)
                current[name] = pred
                np.savez_compressed(
                    folder / (name + "_information.npz"), matrix=matrices
                )
            for i, origin in enumerate(range(begin, end)):
                valid = min(spec["horizons"], end - origin)
                recorder.event(
                    "forecast_locked",
                    phase=phase,
                    origin=origin,
                    latest_mature_target=origin - 1,
                    predictions={
                        name: array_sha(
                            np.stack(
                                [current[name][k][i, :valid] for k in ("mean", "sigma")]
                            )
                        )
                        for name in spec["variance_sources"]
                    },
                )
            for name, pred in current.items():
                np.savez_compressed(folder / (name + ".npz"), **pred)
            phases[phase] = current
        if count != spec["expected_covariance_point_updates"]:
            raise ValueError("Unexpected information update count")
        hashes = {str(p.relative_to(ROOT)): sha(p) for p in paths}
        for p in paths:
            if p.suffix in (".py", ".md", ".json"):
                target = out / "sources" / p.relative_to(ROOT)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(p, target)
        save_json(out / "sources.json", dict(files=hashes))
        recorder.event("all_distributions_locked_before_scoring")
        labels = pd.read_csv(ROOT / spec["data"])[
            [p + "/mm" for p in spec["points"]]
        ].to_numpy(float)
        decisions = {}
        for phase, current in phases.items():
            frames = []
            for name, pred in current.items():
                frame = score_predictions(
                    pred,
                    labels[: spec["stages"][phase][1]],
                    spec["probability"]["levels"],
                )
                if name == "B_RAW":
                    for col in frame:
                        if col not in ("horizon", "point", "n", "mae", "rmse"):
                            frame[col] = np.nan
                frame.insert(0, "model", name)
                frames.append(frame)
            metrics = pd.concat(frames, ignore_index=True)
            summary = aggregate(metrics)
            decision = gate(
                metrics, summary, spec["candidate"], spec["simple_baseline"], spec
            )
            metrics.to_csv(
                out / phase / "metrics.csv", index=False, float_format="%.12g"
            )
            summary.to_csv(
                out / phase / "summary.csv", index=False, float_format="%.12g"
            )
            save_json(out / phase / "decision.json", decision)
            decisions[phase] = decision
        save_json(
            out / "decision.json",
            dict(
                candidate=spec["candidate"],
                phases=decisions,
                covariance_point_updates=count,
                new_mean_fits=0,
                new_calibration_updates=0,
                new_physics=0,
            ),
        )
        save_json(
            out / "status.json",
            dict(
                state="completed",
                exit_code=0,
                started_utc=start.isoformat(),
                finished_utc=datetime.now(timezone.utc).isoformat(),
                wall_seconds=time.perf_counter() - tic,
            ),
        )
        files = {str(p.relative_to(out)): sha(p) for p in out.rglob("*") if p.is_file()}
        save_json(out / "artifact_manifest.json", dict(files=files))
        print(
            json.dumps(
                dict(
                    phases={k: v["passed_count"] for k, v in decisions.items()},
                    covariance_point_updates=count,
                )
            )
        )
    except BaseException as error:
        save_json(
            out / "status.json",
            dict(
                state="failed",
                exit_code=1,
                started_utc=start.isoformat(),
                finished_utc=datetime.now(timezone.utc).isoformat(),
                error=repr(error),
            ),
        )
        raise
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    run(args.config, args.out)
