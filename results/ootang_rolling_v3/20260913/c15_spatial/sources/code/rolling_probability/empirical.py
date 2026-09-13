"""Frozen-mean C7: chronological empirical distributions and same-pool Gaussian.

The later phase is explicitly post-exposure exploratory research. This runner
does not change or invoke the original single-transfer selection workflow.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

import numpy as np
import pandas as pd

from physics_guided.reference import ROOT, save_json, sha
from .data import POINTS, ObservationStream, array_sha
from .scoring import aggregate, crps, gate, interval


def utc():
    return datetime.now(timezone.utc).isoformat()


class Recorder:
    def __init__(self, out):
        self.path = Path(out) / "events.jsonl"
        self.chain = ""

    def event(self, kind, **fields):
        row = dict(kind=kind, utc=utc(), previous_sha256=self.chain, **fields)
        raw = json.dumps(row, sort_keys=True, allow_nan=False)
        self.chain = hashlib.sha256(raw.encode()).hexdigest()
        with self.path.open("a") as handle:
            handle.write(raw + "\n")


def empirical_quantile(sorted_support, probability):
    """Average adjacent quantiles at a flat CDF boundary; exact mass otherwise."""
    n = sorted_support.shape[-1]
    position = probability * n
    boundary = round(position)
    if abs(position - boundary) < 1e-10 and 0 < boundary < n:
        return (sorted_support[..., boundary - 1] + sorted_support[..., boundary]) / 2
    index = max(0, min(n - 1, math.ceil(position) - 1))
    return sorted_support[..., index]


def distribution_losses(y, mean, raw_sigma, sorted_radii, law, levels):
    error = mean - y
    result = dict(absolute=np.abs(error), squared=error**2)
    if law == "empirical":
        support = np.concatenate([-sorted_radii[..., ::-1], sorted_radii], axis=-1)
        n = support.shape[-1]
        weights = 2 * np.arange(1, n + 1) - n - 1
        result["crps"] = (
            np.abs(error[..., None] + raw_sigma[..., None] * support).mean(axis=-1)
            - raw_sigma * np.sum(support * weights, axis=-1) / n**2
        )
        for level in levels:
            lower = mean + raw_sigma * empirical_quantile(support, (1 - level) / 2)
            upper = mean + raw_sigma * empirical_quantile(support, (1 + level) / 2)
            suffix = round(100 * level)
            result[f"coverage{suffix}"] = ((lower <= y) & (y <= upper)).astype(float)
            result[f"width{suffix}"] = upper - lower
            result[f"interval_score{suffix}"] = (
                upper
                - lower
                + 2
                / (1 - level)
                * (np.maximum(lower - y, 0) + np.maximum(y - upper, 0))
            )
    elif law == "moment_gaussian":
        sigma = raw_sigma * np.sqrt(np.mean(sorted_radii**2, axis=-1))
        result["crps"] = np.where(
            sigma > 0, crps(y, mean, np.where(sigma > 0, sigma, 1)), np.abs(error)
        )
        for level in levels:
            coverage, width, score = interval(y, mean, sigma, level)
            suffix = round(100 * level)
            result.update(
                {
                    f"coverage{suffix}": coverage.astype(float),
                    f"width{suffix}": width,
                    f"interval_score{suffix}": score,
                }
            )
    else:
        raise ValueError("Unknown probability law")
    if not all(np.isfinite(v).all() for v in result.values()):
        raise ArithmeticError("Nonfinite distribution scores")
    return result


class ErrorPool:
    def __init__(self, prior, labels, start, horizon, window):
        if len(labels) != start:
            raise ValueError("Initialization requires exactly the observed prefix")
        origins = prior["origins"]
        if (np.diff(origins) <= 0).any() or (origins >= start).any():
            raise ValueError("Historical forecasts are not an earlier ordered phase")
        if (prior["teacher_prefixes"] > origins).any():
            raise ValueError("A historical physical teacher saw future labels")
        self.values = np.empty((horizon, 4, window))
        self.initial_origins = np.empty((horizon, window), dtype=int)
        self.last_target = np.empty(horizon, dtype=int)
        self.updates = np.zeros(horizon, dtype=int)
        for k in range(horizon):
            valid = np.flatnonzero(origins + k < start)[-window:]
            if len(valid) != window:
                raise ValueError("Insufficient completed historical predictions")
            targets = origins[valid] + k
            mu, scale = prior["mean"][valid, k], prior["raw_sigma"][valid, k]
            if (
                (scale <= 0).any()
                or not np.isfinite(mu).all()
                or not np.isfinite(scale).all()
            ):
                raise ValueError("Invalid historical prediction")
            self.values[k] = (np.abs(labels[targets] - mu) / scale).T
            self.initial_origins[k] = origins[valid]
            self.last_target[k] = int(targets[-1])
        self.initial_values = self.values.copy()

    def forecast(self, origin):
        if (self.last_target >= origin).any():
            raise ValueError("Unobserved label in probability pool")
        return np.sort(self.values, axis=-1)

    def update(self, k, error, raw_sigma, target, next_origin):
        if target >= next_origin or target <= self.last_target[k]:
            raise ValueError("Future or duplicate probability update")
        scale = np.asarray(raw_sigma)
        if scale.shape != (4,) or (scale <= 0).any():
            raise ValueError("Invalid issued raw standard deviation")
        value = np.abs(error) / scale
        if not np.isfinite(value).all():
            raise ArithmeticError("Nonfinite standardized error")
        self.values[k, :, :-1] = self.values[k, :, 1:]
        self.values[k, :, -1] = value
        self.last_target[k] = target
        self.updates[k] += 1


def replay(current, prior, stream, start, end, horizon, window, recorder, phase):
    """Issue all distributions before releasing each next observation."""
    pools, predictions = {}, {}
    for name, values in current.items():
        if not np.array_equal(values["origins"], np.arange(start, end)):
            raise ValueError("Wrong current forecast origin range")
        if (values["teacher_prefixes"] > start).any():
            raise ValueError("Current teacher exceeds the training prefix")
        fields = {
            k: values[k].copy()
            for k in ("origins", "mean", "raw_sigma", "teacher_prefixes")
        }
        if name != "B_RAW":
            pool = ErrorPool(prior[name], stream.history, start, horizon, window)
            pools[name] = pool
            fields.update(
                # Store adjacent origins together so overlapping pools compress.
                radii=np.empty((horizon, 4, end - start, window)),
                initial_values=pool.initial_values,
                initial_origins=pool.initial_origins,
            )
        predictions[name] = fields
    for i, origin in enumerate(range(start, end)):
        if len(stream.history) != origin:
            raise ValueError("Observation stream is ahead of the forecast origin")
        valid_h = min(horizon, end - origin)
        locks = {}
        for name, fields in predictions.items():
            mu, scale = fields["mean"][i, :valid_h], fields["raw_sigma"][i, :valid_h]
            if (
                not np.isfinite(mu).all()
                or not np.isfinite(scale).all()
                or (scale <= 0).any()
            ):
                raise ValueError("Invalid frozen current prediction")
            locks[name] = dict(mean=array_sha(mu), raw_sigma=array_sha(scale))
            if name in pools:
                radii = pools[name].forecast(origin)
                fields["radii"][:, :, i] = radii
                locks[name]["radii"] = array_sha(radii[:valid_h])
        recorder.event("forecast_locked", phase=phase, origin=origin, forecasts=locks)
        observed = stream.release()
        recorder.event(
            "observation_released",
            phase=phase,
            index=origin,
            value_sha256=array_sha(observed),
        )
        for k in range(min(horizon, i + 1)):
            issued_i = i - k
            for name, pool in pools.items():
                fields = predictions[name]
                pool.update(
                    k,
                    observed - fields["mean"][issued_i, k],
                    fields["raw_sigma"][issued_i, k],
                    origin,
                    origin + 1,
                )
    for name, pool in pools.items():
        predictions[name].update(
            final_values=pool.values, updates=pool.updates, last_target=pool.last_target
        )
    return predictions, stream.history


def score_phase(predictions, labels, levels, law):
    rows = []
    prefix = "C7_EMP_" if law == "empirical" else "C7_MOMENT_"
    for name, fields in predictions.items():
        model = prefix + name if name in ("FULL", "DATA") else name
        for k in range(fields["mean"].shape[1]):
            targets = fields["origins"] + k
            valid = targets < len(labels)
            mu, scale = fields["mean"][valid, k], fields["raw_sigma"][valid, k]
            actual = labels[targets[valid]]
            if name == "B_RAW":
                errors = mu - actual
                losses = dict(absolute=np.abs(errors), squared=errors**2)
            else:
                radii = fields["radii"][k].transpose(1, 0, 2)[valid]
                losses = distribution_losses(actual, mu, scale, radii, law, levels)
            for j, point in enumerate(POINTS):
                row = dict(model=model, horizon=k + 1, point=point, n=len(actual))
                row.update(
                    {key: float(values[:, j].mean()) for key, values in losses.items()}
                )
                row["mae"] = row.pop("absolute")
                row["rmse"] = math.sqrt(row.pop("squared"))
                rows.append(row)
    frame = pd.DataFrame(rows)
    return frame, aggregate(frame)


def checked_run(path, expected):
    root = ROOT / path
    manifest = root / "artifact_manifest.json"
    if sha(manifest) != expected:
        raise ValueError("Frozen run manifest changed")
    records = json.loads(manifest.read_text())["files"]
    for name, wanted in records.items():
        if sha(root / name) != wanted:
            raise ValueError("Frozen run artifact changed: " + name)
    return root


def load_forecasts(root, phase, full, data):
    result, paths = {}, []
    for p in sorted((root / phase).glob("*.npz")):
        name = p.stem
        if "seed" in name or ("RIDGE" in name and name not in (full, data)):
            continue
        name = "FULL" if name == full else "DATA" if name == data else name
        if name in result:
            raise ValueError("Duplicate frozen forecast")
        with np.load(p, allow_pickle=False) as values:
            result[name] = {k: values[k].copy() for k in values.files}
        paths.append(p)
    if not {"FULL", "DATA", "DRIFT1", "B_ANCHOR", "B_RAW"} <= result.keys():
        raise ValueError("Missing primary or comparison forecast")
    return result, paths


def execute(spec, config, out):
    data = ROOT / spec["data"]
    if sha(data) != spec["data_sha256"] or spec["new_fits_permitted"]:
        raise ValueError("Frozen data or no-fitting rule violated")
    if not spec["post_transfer_exposure"] or spec["original_single_transfer_reused"]:
        raise ValueError("This study must retain the post-exposure limitation")
    sources = [
        Path(config).resolve(),
        ROOT / spec["plan"],
        data,
        ROOT / "code/physics_guided/reference.py",
    ]
    sources += [
        ROOT / "code/rolling_probability" / name
        for name in ("empirical.py", "data.py", "scoring.py")
    ]
    sources += [ROOT / "tests/test_rolling_empirical.py"]
    loaded = {}
    for phase, record in spec["sources"].items():
        current_root = checked_run(
            record["current_run"], record["current_manifest_sha256"]
        )
        prior_root = checked_run(record["prior_run"], record["prior_manifest_sha256"])
        current, paths = load_forecasts(
            current_root, record["current_phase"], "C4_RIDGE_FULL", "C4_RIDGE_DATA"
        )
        prior, old_paths = load_forecasts(
            prior_root,
            record["prior_phase"],
            record["prior_full_name"],
            record["prior_data_name"],
        )
        if current.keys() != prior.keys():
            raise ValueError("Historical and current comparison identities differ")
        loaded[phase] = current, prior
        sources += (
            paths
            + old_paths
            + [
                current_root / "artifact_manifest.json",
                prior_root / "artifact_manifest.json",
            ]
        )
    records = {str(p.relative_to(ROOT)): sha(p) for p in sources}
    for p in sources:
        if p.suffix in (".json", ".py", ".md"):
            target = out / "sources" / p.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, target)
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
    for phase, (start, end) in spec["stages"].items():
        current, prior = loaded[phase]
        recorder.event(
            "phase_started",
            phase=phase,
            training_prefix=start,
            end=end,
            post_exposure=True,
        )
        stream = ObservationStream(data, start, end)
        predictions, labels = replay(
            current,
            prior,
            stream,
            start,
            end,
            spec["horizons"],
            spec["window"],
            recorder,
            phase,
        )
        target = out / phase
        target.mkdir()
        for name, fields in predictions.items():
            for key in ("mean", "raw_sigma", "origins", "teacher_prefixes"):
                if not np.array_equal(fields[key], current[name][key], equal_nan=True):
                    raise ValueError("A frozen raw forecast changed")
            np.savez_compressed(target / f"{name}.npz", **fields)
        decisions[phase] = {}
        for law in ("empirical", "moment_gaussian"):
            metrics, summary = score_phase(predictions, labels, spec["levels"], law)
            scores = target / law
            scores.mkdir()
            metrics.to_csv(scores / "metrics.csv", index=False, float_format="%.12g")
            summary.to_csv(scores / "summary.csv", index=False, float_format="%.12g")
            name = "C7_EMP_FULL" if law == "empirical" else "C7_MOMENT_FULL"
            decision = gate(metrics, summary, name, spec["simple_baseline"], spec)
            decision.update(
                post_exposure=True,
                independent_transfer=False,
                original_c4_decision_unchanged=True,
            )
            decisions[phase][law] = decision
            save_json(scores / "decision.json", decision)
        recorder.event("phase_completed", phase=phase, decisions=decisions[phase])
        print(
            json.dumps(
                dict(phase=phase, decisions=decisions[phase]), ensure_ascii=False
            ),
            flush=True,
        )
    save_json(
        out / "decision.json",
        dict(
            candidate=spec["candidate"],
            phases=decisions,
            new_mean_fits=0,
            new_scale_optimizations=0,
            neural_updates=0,
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
