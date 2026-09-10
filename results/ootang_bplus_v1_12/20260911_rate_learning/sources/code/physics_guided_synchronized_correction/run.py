"""One finite synchronized U/L run; no physical fits or forwards."""

import argparse
import contextlib
import csv
from datetime import datetime, timezone
import importlib.metadata
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time
import traceback

import numpy as np
import pandas as pd
import torch

from physics_guided.training import optimizer
from physics_guided_forecast_error.artifacts import (
    ROOT,
    array_sha,
    check_hashes,
    check_index,
    index_artifacts,
    load_observations,
    read_json,
    seal,
    sha,
)
from physics_guided_sample_learning.core import new_model, fit_scale, score_components
from .core import (
    PREFIXES,
    CUTOFFS,
    POINTS,
    read_teacher,
    training_constants,
    windows,
    mean_step,
    predict,
    compare,
    bound_diagnostics,
)

CONFIG = ROOT / "config/ootang_bplus_synchronized_correction.v1_7.json"
CONFIG_SHA = "d3e3ee032d6b082b9272dbe66a3f7975499a2b25ec9b85eaff04ff6471df6938"
PLAN_SHA = "01aa694ce01a37371665801980729c8ba5e1276e62f58f90c397f626ddf46ee4"


def now():
    return datetime.now(timezone.utc).isoformat()


def plot(out, curves, labels):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    for intervals in (False, True):
        fig, axes = plt.subplots(4, 2, figsize=(12, 13), layout="constrained")
        for col, n in enumerate(CUTOFFS):
            for j, station in enumerate(POINTS):
                ax = axes[j, col]
                for strategy, color in (
                    ("P0", "0.45"),
                    ("U", "#d27b17"),
                    ("L", "#4466ab"),
                ):
                    dist = curves[n, strategy]
                    ax.plot(
                        np.arange(1, 181),
                        dist["mean"][n - 30 :, j],
                        color=color,
                        label=strategy,
                    )
                    if intervals and strategy == "L":
                        ax.fill_between(
                            np.arange(1, 181),
                            dist["lower_90"][n - 30 :, j],
                            dist["upper_90"][n - 30 :, j],
                            color=color,
                            alpha=0.16,
                            label="L 90% interval",
                        )
                ax.plot(
                    np.arange(1, 181),
                    labels[n : n + 180, j],
                    color="black",
                    label="Observed",
                )
                ax.set(
                    title=f"{station} | outer {n} days",
                    xlabel="Forecast day",
                    ylabel="Displacement (mm)",
                )
                ax.grid(alpha=0.15)
        handles, texts = axes[0, 0].get_legend_handles_labels()
        fig.legend(handles, texts, loc="outside upper center", ncol=len(texts))
        fig.savefig(
            out / ("forecast_intervals.png" if intervals else "forecast_means.png"),
            dpi=150,
        )
        plt.close(fig)


def execute(out, started, state):
    if sha(CONFIG) != CONFIG_SHA:
        raise ValueError("Frozen configuration changed")
    spec = read_json(CONFIG)
    if sha(ROOT / spec["protocol"]) != PLAN_SHA:
        raise ValueError("Frozen protocol changed")
    diagnostic = ROOT / spec["diagnostic_source"]
    physical = ROOT / spec["physical_source"]
    learning = ROOT / spec["learning_source"]
    for source in (diagnostic, physical, learning):
        check_index(source)
    previous = read_json(diagnostic / "manifest.json")["sources"]
    protected = read_json(diagnostic / "protected_before.json")
    protected.update(previous)
    protected.update(
        {str(p.relative_to(ROOT)): sha(p) for p in diagnostic.rglob("*") if p.is_file()}
    )
    protected.update(
        {str(CONFIG.relative_to(ROOT)): CONFIG_SHA, spec["protocol"]: PLAN_SHA}
    )
    check_hashes(protected)
    sources = dict(previous)
    for p in [
        *Path(__file__).parent.glob("*.py"),
        ROOT / "tests/test_physics_guided_synchronized_correction.py",
    ]:
        sources[str(p.relative_to(ROOT))] = sha(p)
    for relative in [*sources, str(CONFIG.relative_to(ROOT)), spec["protocol"]]:
        target = out / "sources" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    shutil.copy2(physical / "input_prefix_792.csv", out / "input_prefix_792.csv")
    seal(out / "protected_before.json", protected)
    seal(
        out / "manifest.json",
        dict(
            specification=spec,
            started_utc=now(),
            git_commit=subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            sources=sources,
            config_sha256=CONFIG_SHA,
            plan_sha256=PLAN_SHA,
            input_sha256=sha(out / "input_prefix_792.csv"),
            dependencies={
                p: importlib.metadata.version(p)
                for p in ("numpy", "pandas", "torch", "scipy", "matplotlib")
            },
            physical_forward_calls=0,
        ),
    )
    teachers = read_json(learning / "teachers.json")
    for h, recipe in PREFIXES.items():
        if teachers[str(h)]["recipe"] != recipe:
            raise ValueError("Teacher selection differs from the frozen record")
    seal(out / "teachers.json", {str(h): teachers[str(h)] for h in PREFIXES})
    forecasts, audits = {}, {}
    prior_lock = None
    with (out / "training.csv").open("x", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "fit_days",
                "strategy",
                "seed",
                "epoch",
                "loss_before_update",
                "gradient_norm",
                "elapsed_seconds",
            ],
            lineterminator="\n",
        )
        writer.writeheader()
        for h in PREFIXES:
            if prior_lock is not None:
                check_hashes(read_json(prior_lock)["files"], out)
            label_read_utc = now()
            _, _, labels = load_observations(out / "input_prefix_792.csv", h)
            base, features = read_teacher(out / "input_prefix_792.csv", physical, h)
            scaler, amplitude, rate = training_constants(
                base[:h], features[:h], labels, h
            )
            seal(out / f"scaler_{h}.json", dict(fit_days=h, **scaler.record()))
            seal(
                out / f"constants_{h}.json",
                dict(
                    fit_days=h,
                    label_read_utc=label_read_utc,
                    labels_sha256=array_sha(labels),
                    amplitude_mm=amplitude.tolist(),
                    rate_mm_per_day=rate.tolist(),
                    prior_lock_sha256=sha(prior_lock)
                    if prior_lock is not None
                    else None,
                ),
            )
            x = windows(features[:h], scaler)
            base_train, y = torch.as_tensor(base[30:h]), torch.as_tensor(labels[30:])
            forecasts[h, "P0"] = base[None]
            np.savez_compressed(
                out / f"mean_{h}_P0.npz",
                means=base[None],
                raw=np.zeros_like(base[None]),
            )
            for strategy in ("U", "L"):
                means, raws = [], []
                for seed in range(3):
                    model = new_model(seed)
                    if sum(p.numel() for p in model.parameters()) != 6993:
                        raise ValueError("M1 architecture changed")
                    op = optimizer(model)
                    directory = out / f"model_{h}_{strategy}_{seed}"
                    directory.mkdir()
                    torch.save(model.state_dict(), directory / "e0.pt")
                    for epoch in range(1, 101):
                        if state["neural_updates"] >= 1800:
                            raise RuntimeError("Frozen neural update budget exhausted")
                        loss, norm = mean_step(
                            model, op, x, base_train, y, strategy, amplitude, rate
                        )
                        state["neural_updates"] += 1
                        writer.writerow(
                            dict(
                                fit_days=h,
                                strategy=strategy,
                                seed=seed,
                                epoch=epoch,
                                loss_before_update=loss,
                                gradient_norm=norm,
                                elapsed_seconds=time.monotonic() - started,
                            )
                        )
                        stream.flush()
                        if epoch % 25 == 0:
                            torch.save(model.state_dict(), directory / f"e{epoch}.pt")
                    mean, raw = predict(
                        model, base, features, scaler, strategy, amplitude, rate
                    )
                    means.append(mean)
                    raws.append(raw)
                    audit = bound_diagnostics(mean, base, amplitude, rate)
                    audits[f"{h}_{strategy}_{seed}"] = audit
                    if strategy == "L" and (
                        (np.asarray(audit["max_abs_mm"]) > amplitude + 1e-8).any()
                        or (np.asarray(audit["max_step_mm"]) > rate + 1e-8).any()
                    ):
                        raise ArithmeticError(
                            "Bounded correction violated its declared limits"
                        )
                    print(
                        f"fit={h} strategy={strategy} seed={seed} epoch=100 updates={state['neural_updates']}",
                        flush=True,
                    )
                forecasts[h, strategy] = np.stack(means)
                np.savez_compressed(
                    out / f"mean_{h}_{strategy}.npz",
                    means=np.stack(means),
                    raw=np.stack(raws),
                )
            files = [
                *out.glob(f"model_{h}_*/e*.pt"),
                *out.glob(f"mean_{h}_*.npz"),
                out / f"scaler_{h}.json",
                out / f"constants_{h}.json",
            ]
            prior_lock = out / f"mean_lock_{h}.json"
            seal(
                prior_lock,
                dict(
                    fit_days=h,
                    locked_utc=now(),
                    files={str(p.relative_to(out)): sha(p) for p in files},
                ),
            )
    seal(out / "bound_diagnostics.json", audits)
    seal(
        out / "mean_lock.json",
        dict(
            locked_utc=now(),
            files={p.name: sha(p) for p in out.glob("mean_lock_*.json")},
        ),
    )
    predictions = {}
    for n, c in CUTOFFS.items():
        read_utc = now()
        _, _, labels = load_observations(out / "input_prefix_792.csv", n)
        for strategy in ("P0", "U", "L"):
            calibration_means = forecasts[c, strategy][:, c:n]
            sigma = fit_scale(calibration_means, labels[c:n])
            state["scale_fits"] += 1
            np.savez_compressed(
                out / f"calibration_{n}_{strategy}.npz",
                means=calibration_means,
                scales=sigma,
            )
            seal(
                out / f"calibration_{n}_{strategy}.json",
                dict(
                    origin=c,
                    end=n,
                    label_read_utc=read_utc,
                    labels_sha256=array_sha(labels[c:n]),
                    mean_lock_sha256=sha(out / f"mean_lock_{c}.json"),
                ),
            )
            predictions[n, strategy] = (forecasts[n, strategy], sigma)
            np.savez_compressed(
                out / f"prediction_{n}_{strategy}.npz",
                means=forecasts[n, strategy],
                scales=sigma,
            )
    seal(
        out / "prediction_lock.json",
        dict(
            locked_utc=now(),
            files={
                p.name: sha(p)
                for pattern in (
                    "prediction_*.npz",
                    "calibration_*.npz",
                    "calibration_*.json",
                )
                for p in out.glob(pattern)
            },
        ),
    )
    scoring_read_utc = now()
    _, _, labels = load_observations(out / "input_prefix_792.csv", 792)
    metrics, seed_rows, curves = [], [], {}
    for (n, strategy), (means, sigma) in predictions.items():
        rows, dist = score_components(means, sigma, labels[: n + 180], n, strategy)
        metrics.extend(rows)
        curves[n, strategy] = dist
        np.savez_compressed(out / f"distribution_{n}_{strategy}.npz", **dist)
        for k in range(len(means)):
            rows, _ = score_components(
                means[k : k + 1], sigma[k : k + 1], labels[: n + 180], n, strategy
            )
            seed_rows.extend(
                [
                    dict(
                        component="deterministic" if strategy == "P0" else f"seed_{k}",
                        **row,
                    )
                    for row in rows
                ]
            )
    metrics = pd.DataFrame(metrics)
    metrics.to_csv(out / "metrics.csv", index=False)
    compare(metrics).to_csv(out / "comparisons.csv", index=False)
    pd.DataFrame(seed_rows).to_csv(out / "seed_metrics.csv", index=False)
    plot(out, curves, labels)
    seal(
        out / "scoring.json",
        dict(
            label_read_utc=scoring_read_utc,
            prediction_lock_sha256=sha(out / "prediction_lock.json"),
        ),
    )
    check_hashes(protected)
    check_hashes(sources)
    if state != {"neural_updates": 1800, "scale_fits": 6}:
        raise ValueError("Final execution accounting differs from the fixed budget")
    from .verify import verify

    seal(out / "verification.json", verify(out))
    seal(
        out / "completion.json",
        dict(
            complete=True,
            finished_utc=now(),
            elapsed_seconds=time.monotonic() - started,
            physical_forward_calls=0,
            physical_optimizer_nfev=0,
            scale_network_updates=0,
            **state,
        ),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", args.run_id):
        parser.error("Run ID must be one new path component")
    out = ROOT / "results/ootang_bplus_v1_7" / args.run_id
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    state = dict(neural_updates=0, scale_fits=0)

    def timeout(signum, frame):
        raise TimeoutError("1,800-second budget exhausted; no automatic retry")

    previous = signal.signal(signal.SIGALRM, timeout)
    signal.alarm(1800)
    try:
        with (
            (out / "run.log").open("x") as log,
            contextlib.redirect_stdout(log),
            contextlib.redirect_stderr(log),
        ):
            try:
                execute(out, started, state)
            except Exception as exc:
                traceback.print_exc()
                seal(
                    out / "failure.json",
                    dict(
                        error=str(exc),
                        elapsed_seconds=time.monotonic() - started,
                        **state,
                    ),
                )
                raise
        index_artifacts(out)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
    print(out)


if __name__ == "__main__":
    main()
