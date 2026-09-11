"""One bounded paired experiment with prefix locks and a separate sealed replay."""

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
from physics_guided_sample_learning.core import (
    CUTOFFS,
    POINTS,
    fit_scale,
    score_components,
)
from physics_guided_sequence.core import Budget
from .audit import comparisons, verify_logs
from .core import history_diagnostics, loss_values, mean_step, new_model, predict
from .support import (
    CONFIG,
    CONFIG_SHA,
    PLAN_SHA,
    STRATEGIES,
    prepare_prefix,
    read_table,
    require,
    source_guard,
    specification,
)


def now():
    return datetime.now(timezone.utc).isoformat()


def lock(out, name, paths, **metadata):
    seal(
        out / name,
        dict(
            locked_utc=now(),
            files={str(p.relative_to(out)): sha(p) for p in sorted(paths)},
            **metadata,
        ),
    )


def plot(out, curves, labels, source):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    for sample in ("IN", "OOF"):
        fig, axes = plt.subplots(4, 2, figsize=(12, 13), layout="constrained")
        for col, n in enumerate(CUTOFFS):
            with np.load(source / f"distribution_{n}_{sample}.npz") as old:
                reference = old["mean"][n - 30 :]
            for j, point in enumerate(POINTS):
                ax = axes[j, col]
                days = np.arange(1, 181)
                for strategy, color in (
                    ("P0", "0.5"),
                    (sample + "_CARRY", "#126a83"),
                    (sample + "_RESET", "#c67e24"),
                ):
                    dist = curves[n, strategy]
                    ax.plot(
                        days, dist["mean"][n - 30 :, j], color=color, label=strategy
                    )
                    if strategy != "P0":
                        ax.fill_between(
                            days,
                            dist["lower_90"][n - 30 :, j],
                            dist["upper_90"][n - 30 :, j],
                            color=color,
                            alpha=0.12,
                        )
                ax.plot(
                    days,
                    reference[:, j],
                    linestyle="--",
                    color="#9570a8",
                    label="v1.21 " + sample,
                )
                ax.plot(days, labels[n : n + 180, j], color="black", label="Observed")
                ax.set(
                    title=f"{point} | origin {n}",
                    xlabel="Forecast day",
                    ylabel="Displacement (mm)",
                )
                ax.grid(alpha=0.15)
        handles, names = axes[0, 0].get_legend_handles_labels()
        fig.legend(
            handles,
            names,
            loc="outside upper center",
            ncol=5,
            title="Central 90% intervals shaded | conditional historical development",
        )
        fig.savefig(out / f"forecast_{sample}.png", dpi=150)
        plt.close(fig)


def execute(out, state):
    started, spec = time.monotonic(), specification()
    protected = source_guard(spec)
    source = ROOT / spec["learning_source"]
    paths = [
        *sorted((ROOT / "code/physics_guided_sequence_learning").glob("*.py")),
        ROOT / "tests/test_physics_guided_sequence_learning.py",
        CONFIG,
        ROOT / spec["protocol"],
    ]
    sources = {str(p.relative_to(ROOT)): sha(p) for p in paths}
    for name in sources:
        target = out / "sources" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    shutil.copyfile(source / "input.csv", out / "input.csv")
    shutil.copyfile(source / "metrics.csv", out / "reference_metrics.csv")
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
    budget = Budget(spec["main_limits"])
    state["counts"] = budget.counts
    forecasts, summaries, all_records, all_gradients = {}, [], [], []
    previous = None
    input_count = 0
    for h in spec["prefixes"]:
        if previous is not None:
            check_hashes(read_json(previous)["files"], out)
        read_utc = now()
        data = prepare_prefix(spec, out / "input.csv", h)
        input_count += data["input_values_checked"]
        base = data["pool"][h][0]
        data["pairs"].to_csv(out / f"pairs_{h}.csv", index=False)
        data["queries"].to_csv(out / f"queries_{h}.csv", index=False)
        data["fingerprints"].to_csv(out / f"fingerprints_{h}.csv", index=False)
        shutil.copyfile(source / f"scalers_{h}.json", out / f"scalers_{h}.json")
        for sample, batch in data["batches"].items():
            np.savez_compressed(out / f"batch_{h}_{sample}.npz", **batch.payload())
        np.savez_compressed(out / f"evaluation_{h}.npz", **data["evaluation"].payload())
        seal(
            out / f"constants_{h}.json",
            dict(
                fit_days=h,
                label_read_utc=read_utc,
                labels_sha256=array_sha(data["labels"]),
                feature_sha256=array_sha(data["pool"][h][1]),
                prior_lock_sha256=sha(previous) if previous else None,
                input_values_checked=data["input_values_checked"],
            ),
        )
        shutil.copyfile(source / f"mean_{h}_P0.npz", out / f"mean_{h}_P0.npz")
        for strategy in STRATEGIES:
            sample, variant = strategy.split("_")
            batch = data["batches"][sample]
            means = []
            for seed in spec["seeds"]:
                model = new_model(seed)
                require(
                    sum(p.numel() for p in model.parameters())
                    == spec["model_parameters"],
                    "Changed parameter count",
                )
                op = optimizer(model)
                directory = out / f"model_{h}_{strategy}_{seed}"
                directory.mkdir()
                torch.save(model.state_dict(), directory / "e0.pt")
                initial = loss_values(model, batch, variant, budget)
                with (directory / "training.csv").open("x", newline="") as stream:
                    writer = None
                    for epoch in range(1, spec["epochs"] + 1):
                        record = dict(
                            fit_days=h,
                            strategy=strategy,
                            seed=seed,
                            epoch=epoch,
                            **mean_step(model, op, batch, variant, budget),
                            elapsed_seconds=time.monotonic() - started,
                        )
                        if writer is None:
                            writer = csv.DictWriter(
                                stream, fieldnames=list(record), lineterminator="\n"
                            )
                            writer.writeheader()
                        writer.writerow(record)
                        stream.flush()
                        all_records.append(record)
                        if epoch in spec["checkpoint_epochs"]:
                            torch.save(model.state_dict(), directory / f"e{epoch}.pt")
                            print(
                                f"h={h} {strategy} seed={seed} epoch={epoch} updates={budget.counts['neural_updates']} elapsed={time.monotonic() - started:.1f}s",
                                flush=True,
                            )
                final = loss_values(model, batch, variant, budget)
                summary = dict(
                    fit_days=h,
                    strategy=strategy,
                    seed=seed,
                    initial_anchor_loss=float(initial[0]),
                    initial_paired_loss=float(initial[1]),
                    final_anchor_loss=float(final[0]),
                    final_paired_loss=float(final[1]),
                )
                summaries.append(summary)
                seal(directory / "summary.json", summary)
                means.append(
                    predict(
                        model,
                        data["evaluation"],
                        variant,
                        base,
                        data["queries"].target.to_numpy(),
                        budget,
                    )
                )
                gradients = history_diagnostics(
                    model,
                    data["evaluation"],
                    variant,
                    h,
                    strategy,
                    seed,
                    POINTS,
                    budget,
                )
                pd.DataFrame(gradients).to_csv(
                    directory / "history_gradients.csv", index=False
                )
                all_gradients.extend(gradients)
            forecasts[h, strategy] = np.stack(means)
            np.savez_compressed(
                out / f"mean_{h}_{strategy}.npz", means=forecasts[h, strategy]
            )
        files = [
            *out.glob(f"model_{h}_*/*"),
            *out.glob(f"mean_{h}_*.npz"),
            *out.glob(f"batch_{h}_*.npz"),
            out / f"evaluation_{h}.npz",
            out / f"scalers_{h}.json",
            out / f"constants_{h}.json",
            out / f"pairs_{h}.csv",
            out / f"queries_{h}.csv",
            out / f"fingerprints_{h}.csv",
        ]
        lock(out, f"mean_lock_{h}.json", files, fit_days=h)
        previous = out / f"mean_lock_{h}.json"
    log, summary = pd.DataFrame(all_records), pd.DataFrame(summaries)
    log.to_csv(out / "training.csv", index=False)
    summary.to_csv(out / "model_summary.csv", index=False)
    pd.DataFrame(all_gradients).to_csv(out / "history_gradients.csv", index=False)
    verify_logs(log, summary, spec).to_csv(
        out / "optimizer_diagnostics.csv", index=False
    )
    lock(
        out,
        "mean_lock.json",
        [
            *out.glob("mean_lock_*.json"),
            out / "training.csv",
            out / "model_summary.csv",
            out / "history_gradients.csv",
        ],
    )
    predictions = {}
    for n, c in CUTOFFS.items():
        read_utc = now()
        _, _, labels = load_observations(out / "input.csv", n)
        for strategy in STRATEGIES:
            calibration = forecasts[c, strategy][:, c:n]
            require(
                state["scale_fits"] < spec["max_scale_fits"], "Scale budget exhausted"
            )
            state["scale_fits"] += 1
            sigma = fit_scale(calibration, labels[c:n])
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
            predictions[n, strategy] = forecasts[n, strategy], sigma
            np.savez_compressed(
                out / f"prediction_{n}_{strategy}.npz",
                means=forecasts[n, strategy],
                scales=sigma,
            )
        shutil.copyfile(
            source / f"prediction_{n}_P0.npz", out / f"prediction_{n}_P0.npz"
        )
        with np.load(out / f"prediction_{n}_P0.npz") as saved:
            predictions[n, "P0"] = saved["means"].copy(), saved["scales"].copy()
    lock(
        out,
        "prediction_lock.json",
        [
            p
            for pattern in (
                "prediction_*.npz",
                "calibration_*.npz",
                "calibration_*.json",
            )
            for p in out.glob(pattern)
        ],
    )
    scoring_utc = now()
    _, _, labels = load_observations(out / "input.csv", 792)
    rows, seed_rows, curves = [], [], {}
    for (n, strategy), (means, sigma) in predictions.items():
        values, dist = score_components(means, sigma, labels[: n + 180], n, strategy)
        rows.extend(values)
        curves[n, strategy] = dist
        np.savez_compressed(out / f"distribution_{n}_{strategy}.npz", **dist)
        for k in range(len(means)):
            values, dist = score_components(
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
            np.savez_compressed(
                out / f"distribution_{n}_{strategy}_seed{k}.npz", **dist
            )
    metrics = pd.DataFrame(rows)
    metrics.to_csv(out / "metrics.csv", index=False)
    pd.DataFrame(seed_rows).to_csv(out / "seed_metrics.csv", index=False)
    comparisons(metrics, read_table(out / "reference_metrics.csv")).to_csv(
        out / "comparisons.csv", index=False
    )
    seal(
        out / "scoring.json",
        dict(
            label_read_utc=scoring_utc,
            prediction_lock_sha256=sha(out / "prediction_lock.json"),
        ),
    )
    plot(out, curves, labels, source)
    require(
        budget.counts == spec["main_limits"] and state["scale_fits"] == 8,
        "Execution counts differ from registration",
    )
    seal(
        out / "execution.json",
        dict(
            **state,
            physical_forward_calls=0,
            physical_fits=0,
            scaler_fits=0,
            scale_network_updates=0,
            input_values_checked=input_count,
            elapsed_before_verification_seconds=time.monotonic() - started,
        ),
    )
    from .verify import verify

    print(
        "Training and scoring complete; beginning registered numerical verification",
        flush=True,
    )
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
    state = dict(counts={}, scale_fits=0)
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
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", args.run_id), "Unsafe run id")
    spec = specification()
    out = ROOT / "results/ootang_bplus_v1_25" / args.run_id
    if args.verify:
        from .verify import verify

        print(json.dumps(verify(out), indent=2))
        return
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    process = multiprocessing.get_context("spawn").Process(target=worker, args=(out,))
    process.start()
    while (
        process.is_alive() and time.monotonic() - started < spec["hard_timeout_seconds"]
    ):
        process.join(
            timeout=min(
                1, max(0, spec["hard_timeout_seconds"] - (time.monotonic() - started))
            )
        )
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
    else:
        process.join()
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
