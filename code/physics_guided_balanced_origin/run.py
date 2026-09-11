"""Bounded per-step gradient balancing; retains the frozen v1.19 probability flow."""

import argparse
import contextlib
import csv
from datetime import datetime, timezone
import importlib.metadata
import json
import multiprocessing
import re
import shutil
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
    index_artifacts,
    load_observations,
    read_json,
    seal,
    sha,
)
from physics_guided_sample_learning.core import fit_scale, score_components
from physics_guided_sample_learning.core import POINTS, CUTOFFS, compare_metrics
from physics_guided_history_learning.core import (
    new_model,
    evaluation_pairs,
    pair_table,
)
from physics_guided_history_diagnostics.workflow import saved_scalers
from .core import (
    STRATEGIES,
    make_samples,
    query_table,
    mean_step,
    loss_components,
    final_losses,
    predict_and_count,
    compare_equal,
    optimizer_diagnostics,
)
from physics_guided_origin_gradients.core import Budget
from .support import (
    CONFIG,
    CONFIG_SHA,
    PLAN_SHA,
    RECIPES,
    read_teachers,
    source_guard,
    specification,
)


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
                    ("P0", "0.5"),
                    ("IN", "#d27b17"),
                    ("OOF", "#16846a"),
                ):
                    dist = curves[n, strategy]
                    ax.plot(
                        np.arange(1, 181),
                        dist["mean"][n - 30 :, j],
                        color=color,
                        label=strategy if strategy == "P0" else strategy + " NORM",
                    )
                    if strategy != "P0":
                        reference = ROOT / specification()["equal_source"]
                        with np.load(
                            reference / f"distribution_{n}_{strategy}.npz"
                        ) as old:
                            ax.plot(
                                np.arange(1, 181),
                                old["mean"][n - 30 :, j],
                                color=color,
                                linestyle="--",
                                alpha=0.6,
                                label=strategy + " EQ",
                            )
                    if intervals and strategy == "OOF":
                        ax.fill_between(
                            np.arange(1, 181),
                            dist["lower_90"][n - 30 :, j],
                            dist["upper_90"][n - 30 :, j],
                            color=color,
                            alpha=0.16,
                            label="OOF 90% interval",
                        )
                ax.plot(
                    np.arange(1, 181),
                    labels[n : n + 180, j],
                    color="black",
                    label="Observed",
                )
                ax.set(
                    title=f"{station} | origin {n}",
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


def execute(out, state):
    started, spec = time.monotonic(), specification()
    protected = source_guard(spec)
    paths = [
        *sorted((ROOT / "code/physics_guided_balanced_origin").glob("*.py")),
        ROOT / "tests/test_physics_guided_balanced_origin.py",
        CONFIG,
        ROOT / spec["protocol"],
    ]
    sources = {str(p.relative_to(ROOT)): sha(p) for p in paths}
    for name in sources:
        target = out / "sources" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    # Copy the released file as bytes; numerical readers below enforce their own cutoffs.
    shutil.copyfile(ROOT / spec["input_csv"], out / "input.csv")
    seal(out / "protected_before.json", protected)
    seal(
        out / "manifest.json",
        dict(
            specification=spec,
            config_sha256=CONFIG_SHA,
            plan_sha256=PLAN_SHA,
            sources=sources,
            started_utc=now(),
            input_sha256=sha(out / "input.csv"),
            git_commit=subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            dependencies={
                p: importlib.metadata.version(p)
                for p in ("numpy", "pandas", "torch", "scipy", "matplotlib")
            },
        ),
    )
    physical = ROOT / spec["physical_source"]
    teachers = read_json(ROOT / spec["teacher_source"] / "teachers.json")
    if any(teachers[str(h)]["recipe"] != recipe for h, recipe in RECIPES.items()):
        raise ValueError("Physical teacher selection differs")
    seal(out / "teachers.json", teachers)
    forecasts, summaries, prior_lock = {}, [], None
    gradient_budget = Budget(spec["primary_gradient_calls"], 0)
    shutil.copyfile(
        ROOT / spec["equal_source"] / "metrics.csv", out / "reference_metrics.csv"
    )
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
                "anchor_loss",
                "paired_loss",
                "balanced_loss",
                "anchor_weight",
                "paired_weight",
                "anchor_norm",
                "paired_norm",
                "gradient_dot",
                "raw_anchor_slope",
                "raw_paired_slope",
                "update_norm",
                "linear_anchor_change",
                "linear_paired_change",
                "elapsed_seconds",
            ],
            lineterminator="\n",
        )
        writer.writeheader()
        for h in spec["prefixes"]:
            if prior_lock is not None:
                check_hashes(read_json(prior_lock)["files"], out)
            read_utc = now()
            _, _, labels = load_observations(out / "input.csv", h)
            pool = read_teachers(out / "input.csv", physical, h)
            base, features = pool[h]
            scaler_source = ROOT / spec["history_learning_source"]
            scalers = saved_scalers(scaler_source, h)
            shutil.copyfile(
                scaler_source / f"scalers_{h}.json", out / f"scalers_{h}.json"
            )
            pairs = query_table(h)
            if len(pairs) != spec["total_counts"][str(h)]:
                raise ValueError("Frozen sample count differs")
            pairs.to_csv(out / f"pairs_{h}.csv", index=False)
            pair_table(evaluation_pairs(h, len(base)), h).to_csv(
                out / f"queries_{h}.csv", index=False
            )
            inputs = {
                s: make_samples(s, h, labels, pool, scalers) for s in ("IN", "OOF")
            }
            for strategy, samples in inputs.items():
                np.savez_compressed(
                    out / f"samples_{h}_{strategy}.npz", **samples.payload()
                )
            seal(
                out / f"constants_{h}.json",
                dict(
                    fit_days=h,
                    label_read_utc=read_utc,
                    labels_sha256=array_sha(labels),
                    feature_sha256=array_sha(features),
                    sample_hashes={
                        s: array_sha(samples.x.numpy()) for s, samples in inputs.items()
                    },
                    prior_lock_sha256=sha(prior_lock) if prior_lock else None,
                ),
            )
            forecasts[h, "P0"] = base[None].copy()
            np.savez_compressed(out / f"mean_{h}_P0.npz", means=forecasts[h, "P0"])
            for strategy in ("IN", "OOF"):
                means = []
                for seed in spec["seeds"]:
                    model = new_model(seed)
                    if (
                        sum(p.numel() for p in model.parameters())
                        != spec["model_parameters"]
                    ):
                        raise ValueError("Registered neural architecture differs")
                    op = optimizer(model)
                    directory = out / f"model_{h}_{strategy}_{seed}"
                    directory.mkdir()
                    torch.save(model.state_dict(), directory / "e0.pt")
                    initial = loss_components(
                        inputs[strategy], np.zeros_like(inputs[strategy].base.numpy())
                    )
                    for epoch in range(1, spec["epochs"] + 1):
                        if state["neural_updates"] >= spec["max_neural_updates"]:
                            raise RuntimeError("Registered update budget exhausted")
                        record = mean_step(model, op, inputs[strategy], gradient_budget)
                        state["neural_updates"] += 1
                        writer.writerow(
                            dict(
                                fit_days=h,
                                strategy=strategy,
                                seed=seed,
                                epoch=epoch,
                                loss_before_update=record.pop("loss"),
                                **record,
                                elapsed_seconds=time.monotonic() - started,
                            )
                        )
                        stream.flush()
                        if epoch in spec["checkpoint_epochs"]:
                            torch.save(model.state_dict(), directory / f"e{epoch}.pt")
                    final = final_losses(model, inputs[strategy], gradient_budget)
                    summaries.append(
                        dict(
                            fit_days=h,
                            strategy=strategy,
                            seed=seed,
                            **{f"initial_{k}": v for k, v in initial.items()},
                            **{f"final_{k}": v for k, v in final.items()},
                        )
                    )
                    means.append(
                        predict_and_count(
                            model, base, features, labels, h, scalers, state
                        )
                    )
                    print(
                        f"fit={h} strategy={strategy} seed={seed} epoch=100 updates={state['neural_updates']}",
                        flush=True,
                    )
                forecasts[h, strategy] = np.stack(means)
                np.savez_compressed(
                    out / f"mean_{h}_{strategy}.npz", means=forecasts[h, strategy]
                )
            files = [
                *out.glob(f"model_{h}_*/e*.pt"),
                *out.glob(f"mean_{h}_*.npz"),
                *out.glob(f"samples_{h}_*.npz"),
                out / f"scalers_{h}.json",
                out / f"constants_{h}.json",
                out / f"pairs_{h}.csv",
                out / f"queries_{h}.csv",
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
    pd.DataFrame(summaries).to_csv(out / "model_summary.csv", index=False)
    optimizer_diagnostics(
        pd.read_csv(out / "training.csv"), pd.DataFrame(summaries)
    ).to_csv(out / "optimizer_diagnostics.csv", index=False)
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
        _, _, labels = load_observations(out / "input.csv", n)
        for strategy in STRATEGIES:
            calibration = forecasts[c, strategy][:, c:n]
            sigma = fit_scale(calibration, labels[c:n])
            state["scale_fits"] += 1
            np.savez_compressed(
                out / f"calibration_{n}_{strategy}.npz", means=calibration, scales=sigma
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
    scoring_time = now()
    _, _, labels = load_observations(out / "input.csv", 792)
    rows, seed_rows, curves = [], [], {}
    for (n, strategy), (means, sigma) in predictions.items():
        values, dist = score_components(means, sigma, labels[: n + 180], n, strategy)
        rows.extend(values)
        curves[n, strategy] = dist
        np.savez_compressed(out / f"distribution_{n}_{strategy}.npz", **dist)
        for k in range(len(means)):
            values, _ = score_components(
                means[k : k + 1], sigma[k : k + 1], labels[: n + 180], n, strategy
            )
            seed_rows.extend(
                [
                    dict(
                        component="deterministic" if strategy == "P0" else f"seed_{k}",
                        **r,
                    )
                    for r in values
                ]
            )
    metrics = pd.DataFrame(rows)
    metrics.to_csv(out / "metrics.csv", index=False)
    pd.DataFrame(seed_rows).to_csv(out / "seed_metrics.csv", index=False)
    compare_metrics(metrics).to_csv(out / "comparisons.csv", index=False)
    compare_equal(metrics, pd.read_csv(out / "reference_metrics.csv")).to_csv(
        out / "weighting_comparisons.csv", index=False
    )
    seal(
        out / "scoring.json",
        dict(
            label_read_utc=scoring_time,
            prediction_lock_sha256=sha(out / "prediction_lock.json"),
        ),
    )
    plot(out, curves, labels)
    if (
        state != dict(neural_updates=1800, scale_fits=6, prediction_forward_calls=90)
        or gradient_budget.gradient_calls != 9696
        or gradient_budget.backward_calls != 9696
    ):
        raise ValueError("Final registered execution counts differ")
    seal(
        out / "execution.json",
        dict(
            **state,
            gradient_calls=gradient_budget.gradient_calls,
            gradient_backward_calls=gradient_budget.backward_calls,
            physical_forward_calls=0,
            physical_optimizer_nfev=0,
            scale_network_updates=0,
            scaler_fits=0,
        ),
    )
    from .verify import verify

    seal(out / "verification.json", verify(out, sealed=False))
    check_hashes(protected)
    check_hashes(sources)
    seal(
        out / "completed.json",
        dict(
            execution_complete=True,
            numerical_verification=True,
            elapsed_seconds=time.monotonic() - started,
            completed_utc=now(),
        ),
    )


def worker(out):
    state = dict(neural_updates=0, scale_fits=0, prediction_forward_calls=0)
    with (out / "run.log").open("x", buffering=1) as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            try:
                execute(out, state)
            except BaseException as error:
                traceback.print_exc()
                seal(
                    out / "failed.json",
                    dict(error=repr(error), failed_utc=now(), **state),
                )
                raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Independent checkpoint replay with no updates or writes",
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", args.run_id):
        raise ValueError("Unsafe run id")
    spec = specification()
    out = ROOT / "results/ootang_bplus_v1_21" / args.run_id
    if args.verify:
        from .verify import verify

        print(json.dumps(verify(out), indent=2))
        return
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    process = multiprocessing.get_context("spawn").Process(target=worker, args=(out,))
    process.start()
    process.join(spec["hard_timeout_seconds"])
    if process.is_alive():
        print(
            "Registered 1,800-second hard timeout reached; terminating worker.",
            flush=True,
        )
        process.terminate()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join(5)
    if process.exitcode != 0 and not (out / "failed.json").exists():
        seal(
            out / "failed.json",
            dict(
                error=f"Worker stopped with code {process.exitcode}", failed_utc=now()
            ),
        )
    seal(
        out / "launcher.json",
        dict(exitcode=process.exitcode, wall_seconds=time.monotonic() - started),
    )
    index_artifacts(out)
    print(json.dumps(dict(run_directory=str(out), exitcode=process.exitcode)))
    raise SystemExit(process.exitcode or 0)


if __name__ == "__main__":
    main()
