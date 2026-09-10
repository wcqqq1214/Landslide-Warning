"""One finite, paired IN/OOF experiment with locks before outer scoring."""

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
import sys
import time
import traceback

import numpy as np
import pandas as pd
import torch

from physics_guided.training import optimizer
from physics_guided_forecast_error.artifacts import (
    ROOT,
    CONFIG as DIAG_CONFIG,
    check_hashes,
    check_index,
    index_artifacts,
    load_observations,
    read_json,
    seal,
    sha,
    source_guard,
)
from physics_guided_forecast_error.core import choose_teacher
from .core import (
    CUTOFFS,
    ORIGINS,
    POINTS,
    common_scaler,
    compare_metrics,
    fit_scale,
    make_samples,
    mean_step,
    new_model,
    predict,
    score_components,
    teacher_arrays,
)

CONFIG = ROOT / "config/ootang_bplus_sample_learning.v1_5.json"
CONFIG_SHA = "0f425606c2ce49f182feacaae7165ae2f28ad31d35cc460c1ebf8f0d52296d5a"
PLAN_SHA = "2a9f68c6f4e69493cb638f9dcdeb55ec8bad7fb2591ed282060cf33831535432"


def now():
    return datetime.now(timezone.utc).isoformat()


def plot_results(out, curves, labels):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(4, 2, figsize=(12, 13), layout="constrained")
    for column, n in enumerate((432, 612)):
        for j, station in enumerate(POINTS):
            ax = axes[j, column]
            lead = np.arange(1, 181)
            for strategy, color in (
                ("P0", "0.4"),
                ("IN", "#d27b17"),
                ("OOF", "#4c5fae"),
            ):
                curve = curves[n, strategy]
                ax.plot(lead, curve["mean"][n - 30 :, j], color=color, label=strategy)
                if strategy == "OOF":
                    ax.fill_between(
                        lead,
                        curve["lower_90"][n - 30 :, j],
                        curve["upper_90"][n - 30 :, j],
                        color=color,
                        alpha=0.13,
                        label="OOF 90% interval",
                    )
            ax.plot(
                lead,
                labels[n : n + 180, j],
                color="black",
                linewidth=1.4,
                label="Observed",
            )
            ax.set(
                title=f"{station} | {n} training days",
                xlabel="Forecast day",
                ylabel="Displacement (mm)",
            )
            ax.grid(alpha=0.15)
    handles, texts = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, texts, loc="outside upper center", ncol=5)
    fig.savefig(out / "forecast_comparison.png", dpi=150)
    plt.close(fig)


def execute(out, started, state):
    if sha(CONFIG) != CONFIG_SHA:
        raise ValueError("Committed configuration changed")
    spec = read_json(CONFIG)
    if sha(ROOT / spec["protocol"]) != PLAN_SHA:
        raise ValueError("Committed protocol changed")
    source, diagnostic = ROOT / spec["source_run"], ROOT / spec["diagnostic_run"]
    protected = source_guard(read_json(DIAG_CONFIG))
    check_index(diagnostic)
    protected.update(
        {str(p.relative_to(ROOT)): sha(p) for p in diagnostic.rglob("*") if p.is_file()}
    )
    protected.update(
        {str(CONFIG.relative_to(ROOT)): CONFIG_SHA, spec["protocol"]: PLAN_SHA}
    )
    frozen = (
        ROOT
        / "results/ootang_bplus_v1_1/20260910_implementation/source_snapshot/code/physics_guided"
    )
    for filename in ("models.py", "features.py", "training.py", "probability.py"):
        if sha(ROOT / "code/physics_guided" / filename) != sha(frozen / filename):
            raise ValueError(f"The v1.1 comparison component changed: {filename}")
    sources = sorted(
        set(
            [
                *Path(__file__).parent.glob("*.py"),
                *(ROOT / "code/physics_guided").glob("*.py"),
                *(ROOT / "code/physics_guided_forecast_error").glob("*.py"),
                ROOT / "tests/test_physics_guided_sample_learning.py",
            ]
        )
    )
    source_hashes = {str(p.relative_to(ROOT)): sha(p) for p in sources}
    for p in sources + [CONFIG, ROOT / spec["protocol"]]:
        target = out / "sources" / p.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
    shutil.copy2(source / "input_prefix_792.csv", out / "input_prefix_792.csv")
    seal(out / "protected_before.json", protected)
    seal(
        out / "manifest.json",
        dict(
            specification=spec,
            started_utc=now(),
            git_commit=subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            command=sys.argv,
            python=sys.version,
            dependencies={
                p: importlib.metadata.version(p)
                for p in ("numpy", "pandas", "torch", "scipy", "matplotlib")
            },
            sources=source_hashes,
            config_sha256=CONFIG_SHA,
            plan_sha256=PLAN_SHA,
            input_sha256=sha(out / "input_prefix_792.csv"),
            physical_forward_calls=0,
        ),
    )
    records = read_json(source / "fitted_parameters_locked.json")["records"]
    recipes = {
        n: choose_teacher(
            {r: records[f"{n}_{r}"]["record"]["objective"] for r in ("A", "B")}
        )
        for n in (252, 342, 432, 612)
    }
    seal(
        out / "teachers.json",
        {
            str(n): dict(recipe=r, record=records[f"{n}_{r}"]["record"])
            for n, r in recipes.items()
        },
    )
    models, scalers, mean_paths = {}, {}, {}
    with (out / "training.csv").open("x", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "outer_days",
                "strategy",
                "seed",
                "epoch",
                "loss",
                "gradient_norm",
                "elapsed_seconds",
            ],
        )
        writer.writeheader()
        for n, c in CUTOFFS.items():
            _, forcing, labels = load_observations(out / "input_prefix_792.csv", c)
            teachers = {
                k: teacher_arrays(source, k, recipes[k], forcing, labels[0])
                for k in (*ORIGINS[c], c)
            }
            scaler = common_scaler(teachers[c][1], c)
            scalers[n] = scaler
            seal(out / f"scaler_{n}.json", dict(mean_cutoff=c, **scaler.record()))
            for strategy in ("IN", "OOF"):
                samples = make_samples(strategy, c, labels, teachers, scaler)
                pd.DataFrame(
                    dict(
                        index=samples.indices,
                        origin=samples.origins,
                        weight=samples.weights.numpy(),
                        label_date=pd.date_range("2016-07-01", periods=c).strftime(
                            "%Y-%m-%d"
                        )[samples.indices],
                    )
                ).to_csv(out / f"samples_{n}_{strategy}.csv", index=False)
                for seed in range(3):
                    model = new_model(seed)
                    if sum(p.numel() for p in model.parameters()) != 6993:
                        raise ValueError("Frozen M1 parameter count changed")
                    op = optimizer(model)
                    directory = out / f"mean_{n}_{strategy}_{seed}"
                    directory.mkdir()
                    torch.save(model.state_dict(), directory / "e0.pt")
                    for epoch in range(1, 101):
                        if state["optimizer_updates"] >= 1200:
                            raise RuntimeError("Update budget exhausted")
                        loss, norm = mean_step(model, op, samples)
                        state["optimizer_updates"] += 1
                        writer.writerow(
                            dict(
                                outer_days=n,
                                strategy=strategy,
                                seed=seed,
                                epoch=epoch,
                                loss=loss,
                                gradient_norm=norm,
                                elapsed_seconds=time.monotonic() - started,
                            )
                        )
                        stream.flush()
                        if epoch % 25 == 0:
                            torch.save(model.state_dict(), directory / f"e{epoch}.pt")
                            print(
                                f"mean n={n} {strategy} seed={seed} epoch={epoch} loss={loss:.8f} updates={state['optimizer_updates']}",
                                flush=True,
                            )
                    models[n, strategy, seed] = model
                    mean_paths[str(directory.relative_to(out) / "e100.pt")] = sha(
                        directory / "e100.pt"
                    )
            print(f"Mean training completed for n={n}; cutoff={c}", flush=True)
    seal(
        out / "mean_lock.json",
        dict(
            locked_utc=now(),
            checkpoints=mean_paths,
            scalers={
                f"scaler_{n}.json": sha(out / f"scaler_{n}.json") for n in CUTOFFS
            },
            updates=state["optimizer_updates"],
        ),
    )
    scales = {}
    for n, c in CUTOFFS.items():
        _, forcing, labels = load_observations(out / "input_prefix_792.csv", n)
        base, features = teacher_arrays(source, c, recipes[c], forcing, labels[0])
        for strategy in ("P0", "IN", "OOF"):
            means = (
                base[None, c:n]
                if strategy == "P0"
                else np.stack(
                    [
                        predict(models[n, strategy, seed], base, features, scalers[n])[
                            c:n
                        ]
                        for seed in range(3)
                    ]
                )
            )
            sigma = fit_scale(means, labels[c:n].copy())
            scales[n, strategy] = sigma
            np.savez_compressed(
                out / f"calibration_{n}_{strategy}.npz", means=means, scales=sigma
            )
    seal(
        out / "scale_lock.json",
        dict(
            locked_utc=now(),
            files={p.name: sha(p) for p in sorted(out.glob("calibration_*.npz"))},
            mean_lock_sha256=sha(out / "mean_lock.json"),
        ),
    )
    predictions = {}
    for n in CUTOFFS:
        # Forecast data interface parses driver columns and the first observation only.
        frame = pd.read_csv(
            out / "input_prefix_792.csv",
            nrows=n + 180,
            usecols=["Date", "Rainfall/mm", "RWL/m"],
        )
        forcing = frame[["Rainfall/mm", "RWL/m"]].to_numpy(float)
        _, _, first_prefix = load_observations(out / "input_prefix_792.csv", 31)
        base, features = teacher_arrays(source, n, recipes[n], forcing, first_prefix[0])
        for strategy in ("P0", "IN", "OOF"):
            means = (
                base[None].copy()
                if strategy == "P0"
                else np.stack(
                    [
                        predict(models[n, strategy, seed], base, features, scalers[n])
                        for seed in range(3)
                    ]
                )
            )
            predictions[n, strategy] = means
            np.savez_compressed(
                out / f"prediction_{n}_{strategy}.npz",
                means=means,
                scales=scales[n, strategy],
            )
    seal(
        out / "prediction_lock.json",
        dict(
            locked_utc=now(),
            files={p.name: sha(p) for p in sorted(out.glob("prediction_*.npz"))},
            scale_lock_sha256=sha(out / "scale_lock.json"),
        ),
    )
    # Only now may the held-out displacement columns enter scoring.
    _, _, labels = load_observations(out / "input_prefix_792.csv", 792)
    all_metrics, curves, diagnostics, seed_metrics = [], {}, [], []
    for (n, strategy), means in predictions.items():
        rows, values = score_components(
            means, scales[n, strategy], labels[: n + 180], n, strategy
        )
        all_metrics.extend(rows)
        curves[n, strategy] = values
        np.savez_compressed(out / f"distribution_{n}_{strategy}.npz", **values)
        physical = predictions[n, "P0"][0]
        for seed, mu in enumerate(means):
            residual = mu[30:] - physical[30:]
            diagnostics.append(
                dict(
                    outer_days=n,
                    strategy=strategy,
                    seed=seed,
                    max_abs_correction_mm=float(abs(residual).max()),
                    max_abs_daily_correction_change_mm=float(
                        abs(np.diff(residual, axis=0)).max()
                    ),
                    negative_prediction_increment_fraction=float(
                        (np.diff(mu[n - 1 :], axis=0) < 0).mean()
                    ),
                )
            )
            for j, station in enumerate(POINTS):
                for part, start, end in (("train", 30, n), ("prediction", n, n + 180)):
                    error = mu[start:end, j] - labels[start:end, j]
                    seed_metrics.append(
                        dict(
                            outer_days=n,
                            strategy=strategy,
                            seed=seed,
                            station=station,
                            part=part,
                            rmse_mm=float(np.sqrt(np.mean(error**2))),
                            mae_mm=float(abs(error).mean()),
                        )
                    )
    metrics = pd.DataFrame(all_metrics)
    metrics.to_csv(out / "metrics.csv", index=False)
    pd.DataFrame(seed_metrics).to_csv(out / "seed_metrics.csv", index=False)
    pd.DataFrame(diagnostics).to_csv(out / "correction_diagnostics.csv", index=False)
    comparisons = compare_metrics(metrics)
    comparisons.to_csv(out / "mean_comparisons.csv", index=False)
    plot_results(out, curves, labels)
    check_hashes(protected)
    check_hashes(source_hashes)
    from .verify import verify

    checked = verify(out)
    seal(out / "verification.json", checked)
    seal(
        out / "completion.json",
        dict(
            complete=True,
            finished_utc=now(),
            elapsed_seconds=time.monotonic() - started,
            optimizer_updates=state["optimizer_updates"],
            physical_forward_calls=0,
            physical_nfev=0,
            neural_scale_updates=0,
            strict_mean_improvements={
                s: int(
                    comparisons[comparisons.strategy == s].strict_mean_improvement.sum()
                )
                for s in ("IN", "OOF")
            },
            effectiveness="See all per-point errors and probability metrics; execution success alone is not effectiveness.",
        ),
    )
    print(
        f"Complete: {state['optimizer_updates']} updates; independent artifact checks passed",
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", args.run_id):
        parser.error("Run ID must be a single new path component")
    out = ROOT / "results/ootang_bplus_v1_5" / args.run_id
    out.mkdir(parents=True, exist_ok=False)
    started, state = time.monotonic(), dict(optimizer_updates=0)

    def timeout(signum, frame):
        raise TimeoutError("20-minute experiment budget exhausted; no automatic retry")

    previous = signal.signal(signal.SIGALRM, timeout)
    signal.alarm(1200)
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
