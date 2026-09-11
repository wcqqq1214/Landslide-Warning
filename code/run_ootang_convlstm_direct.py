"""Execute the single frozen v2.0 candidate once, then audit saved results."""

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time

import numpy as np
import pandas as pd
import torch

from physics_guided.features import Scaler
from physics_guided.training import optimizer, setup
from physics_guided_direct import (
    ConditionalScale,
    DirectConvLSTM,
    decide,
    evaluation_batch,
    mean_step,
    predict,
    scale_features,
    scale_step,
    score,
)
from physics_guided_forecast_error.artifacts import load_observations
from physics_guided_origin_learning.core import query_table
from physics_guided_origin_learning.support import read_teachers
from physics_guided_sequence_learning.core import block_losses, make_batch

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/ootang_convlstm_direct.v2_0.json"
CONFIG_SHA = "8fef549e1f189b9479bf80d940aef7448461fa29ac93092811cf256b9e3db892"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    with Path(path).open("x") as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")


def check_sources(mapping):
    for name, digest in mapping.items():
        if sha(ROOT / name) != digest:
            raise ValueError(f"Frozen source differs: {name}")


def specification():
    if sha(CONFIG) != CONFIG_SHA:
        raise ValueError("Frozen candidate configuration differs")
    spec = json.loads(CONFIG.read_text())
    check_sources(spec["source_sha256"])
    return spec


def scalers(spec, h):
    saved = json.loads(
        (ROOT / spec["reference_source"] / f"scalers_{h}.json").read_text()
    )
    return tuple(
        Scaler(*(np.array(saved[k][v]) for v in ("mean", "scale", "floor")))
        for k in ("physical", "history")
    )


def source_code():
    paths = {Path(__file__).resolve()}
    for module in tuple(sys.modules.values()):
        name = getattr(module, "__file__", None)
        if name:
            path = Path(name).resolve()
            if path.is_relative_to(ROOT / "code") and path.suffix == ".py":
                paths.add(path)
    return {str(p.relative_to(ROOT)): sha(p) for p in sorted(paths)}


def append_csv(path, record):
    exists = path.exists()
    with path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(record))
        if not exists:
            writer.writeheader()
        writer.writerow(record)


class Run:
    def __init__(self, out, spec):
        self.out, self.spec = out, spec
        self.started = time.monotonic()
        self.deadline = datetime.fromisoformat(spec["work_deadline_utc"]).timestamp()
        self.counts = {"mean_updates": 0, "scale_updates": 0}
        self.events = []

    def check(self):
        if (
            time.time() >= self.deadline
            or time.monotonic() - self.started >= self.spec["run_limit_seconds"]
        ):
            raise TimeoutError("The original total or single-run deadline was reached")

    def event(self, kind, **fields):
        record = dict(
            sequence=len(self.events),
            kind=kind,
            utc=datetime.now(timezone.utc).isoformat(),
            **fields,
        )
        self.events.append(record)
        with (self.out / "events.jsonl").open("a") as f:
            f.write(json.dumps(record, allow_nan=False) + "\n")

    def update(self, kind):
        self.check()
        key = kind + "_updates"
        if self.counts[key] >= self.spec["max_" + key]:
            raise RuntimeError("Frozen update limit reached")
        self.counts[key] += 1

    def labels(self, h):
        self.check()
        self.event("observation_prefix_read", rows=h)
        return load_observations(ROOT / self.spec["data"], h)[2]

    def lock(self, kind, h, paths):
        hashes = {p.name: sha(p) for p in paths}
        write_json(self.out / f"{kind}_{h}.json", dict(prefix=h, artifacts=hashes))
        self.event(kind, prefix=h, artifacts=hashes)


def fit_mean(run, h, labels, pool):
    spec, out = run.spec, run.out
    table = query_table(h)
    table.to_csv(out / f"queries_{h}.csv", index=False)
    fixed_scalers = scalers(spec, h)
    batch = make_batch(table, pool, labels, fixed_scalers, spec["teacher_column"], True)
    base, features = pool[h]
    eval_table, eval_batch = evaluation_batch(base, features, labels, h, fixed_scalers)
    eval_table.to_csv(out / f"evaluation_queries_{h}.csv", index=False)
    all_means, paths, losses = [], [], []
    for seed in spec["seeds"]:
        run.check()
        setup(seed)
        model = DirectConvLSTM()
        if sum(p.numel() for p in model.parameters()) != spec["mean_parameters"]:
            raise ValueError("Mean architecture differs from frozen count")
        with torch.no_grad():
            initial = model(batch.encoder, batch.decoder)
            if torch.count_nonzero(initial).item() != 0:
                raise ArithmeticError("Initial mean must equal B+ exactly")
            before = block_losses(batch, initial).numpy()
        op = optimizer(model)
        for epoch in range(1, spec["mean_epochs"] + 1):
            run.update("mean")
            record = mean_step(model, op, batch)
            append_csv(
                out / "mean_training.csv",
                dict(prefix=h, seed=seed, epoch=epoch, **record),
            )
        model.eval().requires_grad_(False)
        with torch.no_grad():
            after = block_losses(batch, model(batch.encoder, batch.decoder)).numpy()
        losses.append(
            dict(
                prefix=h,
                seed=seed,
                initial_anchor=float(before[0]),
                initial_paired=float(before[1]),
                final_anchor=float(after[0]),
                final_paired=float(after[1]),
            )
        )
        path = out / f"mean_{h}_{seed}.pt"
        torch.save(model.state_dict(), path)
        paths.append(path)
        all_means.append(predict(model, eval_batch, base, eval_table.target.to_numpy()))
        print(f"mean prefix={h} seed={seed} e100 saved", flush=True)
    means = np.stack(all_means)
    bundle = out / f"means_{h}.npz"
    np.savez_compressed(bundle, means=means)
    write_json(out / f"losses_{h}.json", losses)
    run.lock("mean_forecast_lock", h, paths + [bundle])
    return means, eval_table


def fit_probability(run, h, labels, pool, means, eval_table, previous):
    spec, out = run.spec, run.out
    origin = {432: 342, 612: 432}[h]
    old_mean = previous["means"]
    old_base = previous["base"]
    old_labels = previous["labels"]
    calibration_ids = np.arange(origin, h)
    calibration_origins = np.full(h - origin, origin)
    base = pool[h][0]
    targets = np.r_[30, eval_table.target.to_numpy()]
    origins = np.r_[30, eval_table.origin.to_numpy()]
    if not np.array_equal(targets, np.arange(30, h + 180)):
        raise ValueError("Evaluation rows no longer cover the frozen interval")
    sigmas, paths = [], []
    for seed in spec["seeds"]:
        run.check()
        setup(seed)
        frozen = old_mean[seed, calibration_ids]
        initial_sigma = np.maximum(
            0.002, np.sqrt(np.mean((frozen - labels[calibration_ids]) ** 2, axis=0))
        )
        model = ConditionalScale(initial_sigma)
        if sum(p.numel() for p in model.parameters()) != spec["scale_parameters"]:
            raise ValueError("Scale architecture differs from frozen count")
        x = scale_features(
            old_base, old_mean[seed], old_labels, calibration_origins, calibration_ids
        )
        frozen_tensor = torch.tensor(frozen, dtype=torch.float64)
        scale_labels = torch.tensor(labels[calibration_ids], dtype=torch.float64)
        op = optimizer(model)
        for epoch in range(1, spec["scale_epochs"] + 1):
            run.update("scale")
            record = scale_step(model, op, x, frozen_tensor, scale_labels)
            append_csv(
                out / "scale_training.csv",
                dict(prefix=h, seed=seed, epoch=epoch, **record),
            )
        model.eval().requires_grad_(False)
        path = out / f"scale_{h}_{seed}.pt"
        torch.save(model.state_dict(), path)
        paths.append(path)
        values = scale_features(base, means[seed], labels, origins, targets)
        with torch.no_grad():
            scale = model(values).numpy()
        if not np.isfinite(scale).all() or (scale <= 0.001).any():
            raise ArithmeticError("Nonfinite or collapsed probability scale")
        full = np.full_like(base, np.nan)
        full[targets] = scale
        sigmas.append(full)
    sigmas = np.stack(sigmas)
    path = out / f"prediction_{h}_DIRECT.npz"
    np.savez_compressed(path, means=means, sigmas=sigmas)
    run.lock("distribution_lock", h, paths + [path])
    print(f"distribution prefix={h} frozen before later observations", flush=True)


def score_all(out, spec, labels):
    rows, seed_rows = [], []
    for n in spec["outer_days"]:
        for strategy in ("P0", "DIRECT"):
            if strategy == "P0":
                path = ROOT / spec["reference_source"] / f"prediction_{n}_P0.npz"
                with np.load(path, allow_pickle=False) as saved:
                    means = saved["means"]
                    sigmas = np.broadcast_to(
                        saved["scales"][:, None], means.shape
                    ).copy()
            else:
                with np.load(
                    out / f"prediction_{n}_DIRECT.npz", allow_pickle=False
                ) as saved:
                    means, sigmas = saved["means"], saved["sigmas"]
            metrics, summary = score(means, sigmas, labels[: n + 180], n, strategy)
            rows.extend(metrics)
            np.savez_compressed(out / f"summary_{n}_{strategy}.npz", **summary)
            if strategy == "DIRECT":
                for seed in spec["seeds"]:
                    metrics, _ = score(
                        means[seed : seed + 1],
                        sigmas[seed : seed + 1],
                        labels[: n + 180],
                        n,
                        strategy,
                    )
                    seed_rows.extend(dict(seed=seed, **row) for row in metrics)
    metrics = pd.DataFrame(rows)
    reference = pd.read_csv(ROOT / spec["reference_source"] / "metrics.csv")
    keys = ["outer_days", "strategy", "part", "station"]
    expected = reference[reference.strategy == "P0"].set_index(keys).sort_index()
    actual = metrics[metrics.strategy == "P0"].set_index(keys).sort_index()
    if list(expected.index) != list(actual.index) or not np.allclose(
        expected[actual.columns], actual, atol=1e-10, rtol=1e-12
    ):
        raise ArithmeticError("P0 metric recomputation differs from frozen reference")
    metrics.to_csv(out / "metrics.csv", index=False)
    pd.DataFrame(seed_rows).to_csv(out / "seed_metrics.csv", index=False)
    result = decide(metrics, spec["strict_mean_tolerance_mm"], spec["coverage_slack"])
    pd.DataFrame(result["point_windows"]).to_csv(out / "comparison.csv", index=False)
    write_json(out / "decision.json", result)
    return result


def verify(out):
    """One reload per final model; saved-array scoring; no training or diagnostics."""
    spec = specification()
    manifest = json.loads((out / "manifest.json").read_text())
    check_sources(manifest["source_code_sha256"])
    events = [json.loads(s) for s in (out / "events.jsonl").read_text().splitlines()]
    if [e["rows"] for e in events if e["kind"] == "observation_prefix_read"] != [
        342,
        432,
        612,
        792,
    ]:
        raise ValueError("Observation access order differs")
    for h, later in ((342, 432), (432, 612), (612, 792)):
        kinds = ["mean_forecast_lock"] + (["distribution_lock"] if h != 342 else [])
        read_event = next(
            e
            for e in events
            if e["kind"] == "observation_prefix_read" and e["rows"] == later
        )
        for kind in kinds:
            lock = next(e for e in events if e["kind"] == kind and e["prefix"] == h)
            if lock["sequence"] >= read_event["sequence"]:
                raise ValueError("Future observations preceded a required lock")
            for name, digest in lock["artifacts"].items():
                if sha(out / name) != digest:
                    raise ValueError("Locked prediction or model changed")
    for kind, cap in (("mean", 900), ("scale", 600)):
        logs = pd.read_csv(out / f"{kind}_training.csv")
        if (
            len(logs) != cap
            or not np.isfinite(logs.select_dtypes("number")).all().all()
        ):
            raise ValueError("Incomplete finite optimizer log")
        for _, group in logs.groupby(["prefix", "seed"]):
            if group.epoch.tolist() != list(range(1, 101)):
                raise ValueError("Updates differ from frozen e100")
    max_mean_error = max_scale_error = 0.0
    for h in spec["prefixes"]:
        labels = load_observations(ROOT / spec["data"], h)[2]
        pool = read_teachers(ROOT / spec["data"], ROOT / spec["physical_source"], h)
        base, features = pool[h]
        table, batch = evaluation_batch(base, features, labels, h, scalers(spec, h))
        with np.load(out / f"means_{h}.npz", allow_pickle=False) as saved:
            means = saved["means"]
        for seed in spec["seeds"]:
            model = DirectConvLSTM().eval().requires_grad_(False)
            model.load_state_dict(
                torch.load(out / f"mean_{h}_{seed}.pt", weights_only=True)
            )
            replay = predict(model, batch, base, table.target.to_numpy())
            max_mean_error = max(
                max_mean_error, float(np.max(abs(replay[30:] - means[seed, 30:])))
            )
            if h in spec["outer_days"]:
                scale = ConditionalScale(np.ones(4)).eval().requires_grad_(False)
                scale.load_state_dict(
                    torch.load(out / f"scale_{h}_{seed}.pt", weights_only=True)
                )
                x = scale_features(
                    base,
                    means[seed],
                    labels,
                    np.r_[30, table.origin],
                    np.arange(30, h + 180),
                )
                with (
                    torch.no_grad(),
                    np.load(
                        out / f"prediction_{h}_DIRECT.npz", allow_pickle=False
                    ) as saved,
                ):
                    if not np.array_equal(saved["means"], means, equal_nan=True):
                        raise ValueError("Distribution mean differs from mean lock")
                    max_scale_error = max(
                        max_scale_error,
                        float(
                            np.max(abs(scale(x).numpy() - saved["sigmas"][seed, 30:]))
                        ),
                    )
    if max_mean_error > 1e-10 or max_scale_error > 1e-10:
        raise ArithmeticError("Checkpoint reload differs from saved predictions")
    labels = load_observations(ROOT / spec["data"], 792)[2]
    stored_metrics = pd.read_csv(out / "metrics.csv")
    for n in spec["outer_days"]:
        with np.load(out / f"prediction_{n}_DIRECT.npz", allow_pickle=False) as saved:
            rows, _ = score(
                saved["means"], saved["sigmas"], labels[: n + 180], n, "DIRECT"
            )
        old = stored_metrics[
            (stored_metrics.outer_days == n) & (stored_metrics.strategy == "DIRECT")
        ]
        keys = ["outer_days", "strategy", "part", "station"]
        a, b = (
            pd.DataFrame(rows).set_index(keys).sort_index(),
            old.set_index(keys).sort_index(),
        )
        if not np.allclose(a, b, atol=1e-10, rtol=1e-12):
            raise ArithmeticError("Saved metric recomputation differs")
    decision = decide(
        stored_metrics, spec["strict_mean_tolerance_mm"], spec["coverage_slack"]
    )
    original = json.loads((out / "decision.json").read_text())
    if (
        decision["passed"] != original["passed"]
        or decision["strict_mean_improvement_count"]
        != original["strict_mean_improvement_count"]
    ):
        raise ArithmeticError("Saved decision differs")
    check_sources(spec["source_sha256"])
    write_json(
        out / "verification.json",
        dict(
            passed=True,
            max_mean_reload_error_mm=max_mean_error,
            max_scale_reload_error_mm=max_scale_error,
            mean_models_reloaded=9,
            scale_models_reloaded=6,
            checked="source hashes, temporal locks, update counts, P0 equality, all model reloads, saved scores and decision",
            effectiveness_passed=decision["passed"],
        ),
    )


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    setup(0)
    spec = specification()
    remaining = (
        datetime.fromisoformat(spec["work_deadline_utc"]).timestamp() - time.time()
    )
    if remaining <= 0:
        raise TimeoutError("Original total work deadline expired; no automatic rerun")

    def timeout(*_):
        raise TimeoutError("Hard run/total deadline reached")

    signal.signal(signal.SIGALRM, timeout)
    signal.setitimer(signal.ITIMER_REAL, min(remaining, spec["run_limit_seconds"]))
    if args.verify:
        verify(args.output)
        return
    args.output.mkdir(parents=True, exist_ok=False)
    run = Run(args.output, spec)
    manifest = dict(
        config_sha256=CONFIG_SHA,
        source_sha256=spec["source_sha256"],
        source_code_sha256=source_code(),
        git_commit=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        work_started_utc=spec["work_started_utc"],
        work_deadline_utc=spec["work_deadline_utc"],
        python=platform.python_version(),
        numpy=np.__version__,
        pandas=pd.__version__,
        torch=torch.__version__,
    )
    write_json(args.output / "manifest.json", manifest)
    status = {"status": "failed", "automatic_retry": False}
    try:
        previous = None
        for h in spec["prefixes"]:
            labels = run.labels(h)
            pool = read_teachers(ROOT / spec["data"], ROOT / spec["physical_source"], h)
            means, table = fit_mean(run, h, labels, pool)
            if h in spec["outer_days"]:
                fit_probability(run, h, labels, pool, means, table, previous)
            previous = dict(means=means, base=pool[h][0], labels=labels)
        labels = run.labels(792)
        result = score_all(args.output, spec, labels)
        check_sources(manifest["source_code_sha256"])
        check_sources(spec["source_sha256"])
        status.update(
            status="complete",
            effectiveness_passed=result["passed"],
            strict_mean_improvement_count=result["strict_mean_improvement_count"],
        )
        print(json.dumps(status), flush=True)
    except BaseException as exc:
        status.update(error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        status.update(
            counts=run.counts,
            elapsed_run_seconds=time.monotonic() - run.started,
            finished_utc=datetime.now(timezone.utc).isoformat(),
        )
        write_json(args.output / "run_status.json", status)


if __name__ == "__main__":
    main()
