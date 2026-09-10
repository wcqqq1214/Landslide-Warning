"""Extend two fixed B+ trajectories and replay frozen neural corrections once."""

import argparse
import contextlib
from datetime import datetime, timezone
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

from physics_guided.features import Scaler
from physics_guided.reference import load
from physics_guided_diagnostics.run import REFERENCE, verify_reference
from physics_guided_optimization_selection.run import audit_trajectory
from physics_guided_forecast_error.artifacts import (
    ROOT,
    check_hashes,
    check_index,
    index_artifacts,
    load_observations,
    read_json,
    seal,
    sha,
    trajectory_name,
)
from physics_guided_sample_learning.core import new_model, predict
from .core import (
    CUTOFFS,
    POINTS,
    decompose,
    matched_comparisons,
    read_drivers,
    score,
    teacher_features,
)

CONFIG = ROOT / "config/ootang_bplus_teacher_transfer.v1_6.json"
CONFIG_SHA = "a922faa62df939671a57504b88b8da0bc5fb20dce4b8a7f7e5d86d02ea7153c5"
PLAN_SHA = "2a32538a476b6a39248d8a8ca9787abade2c9eea77dabd537d775259319e4e53"


def now():
    return datetime.now(timezone.utc).isoformat()


def plot(out, curves, labels, comparisons):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(4, 2, figsize=(12, 13), layout="constrained")
    for col, n in enumerate(CUTOFFS):
        for j, station in enumerate(POINTS):
            ax = axes[j, col]
            for branch, strategy, color, style in (
                ("OLD", "P0", "#777777", "--"),
                ("NEW", "P0", "#444444", "-"),
                ("OLD", "OOF", "#b371ba", "--"),
                ("NEW", "OOF", "#4466ab", "-"),
            ):
                ax.plot(
                    np.arange(1, 181),
                    curves[n, branch, strategy]["mean"][n - 30 :, j],
                    color=color,
                    linestyle=style,
                    label=f"{branch} {strategy}",
                )
            ax.plot(
                np.arange(1, 181),
                labels[n : n + 180, j],
                color="black",
                label="Observed",
            )
            ax.set(
                title=f"{station} | outer {n} days",
                xlabel="Same forecast day",
                ylabel="Displacement (mm)",
            )
            ax.grid(alpha=0.15)
    handles, texts = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, texts, loc="outside upper center", ncol=5)
    fig.savefig(out / "same_day_forecasts.png", dpi=150)
    plt.close(fig)
    fig, axes = plt.subplots(1, 2, figsize=(12, 4), layout="constrained")
    for ax, n in zip(axes, CUTOFFS):
        for i, (branch, strategy) in enumerate(
            (("OLD", "IN"), ("NEW", "IN"), ("OLD", "OOF"), ("NEW", "OOF"))
        ):
            group = (
                comparisons[
                    (comparisons.outer_days == n)
                    & (comparisons.branch == branch)
                    & (comparisons.strategy == strategy)
                ]
                .set_index("station")
                .loc[list(POINTS)]
            )
            ax.bar(
                np.arange(4) + (i - 1.5) * 0.19,
                group.prediction_rmse_mm_minus_own,
                width=0.18,
                label=f"{branch} {strategy}",
            )
        ax.axhline(0, color="black", linewidth=0.8)
        ax.set(
            xticks=np.arange(4),
            xticklabels=POINTS,
            ylabel="RMSE change vs own B+ (mm)",
            title=f"Outer {n} days | lower is better",
        )
    axes[0].legend(fontsize=8)
    fig.savefig(out / "correction_value_add.png", dpi=160)
    plt.close(fig)


def execute(out, started, state):
    if sha(CONFIG) != CONFIG_SHA:
        raise ValueError("Frozen configuration changed")
    spec = read_json(CONFIG)
    if sha(ROOT / spec["protocol"]) != PLAN_SHA:
        raise ValueError("Frozen protocol changed")
    learning, physical = ROOT / spec["learning_source"], ROOT / spec["physical_source"]
    check_index(learning)
    check_index(physical)
    protected = read_json(learning / "protected_before.json")
    protected.update(read_json(learning / "manifest.json")["sources"])
    protected.update(
        {str(p.relative_to(ROOT)): sha(p) for p in learning.rglob("*") if p.is_file()}
    )
    protected.update(
        {str(CONFIG.relative_to(ROOT)): CONFIG_SHA, spec["protocol"]: PLAN_SHA}
    )
    check_hashes(protected)
    previous_sources = read_json(learning / "manifest.json")["sources"]
    sources = {
        **previous_sources,
        **{
            str(p.relative_to(ROOT)): sha(p)
            for p in sorted(Path(__file__).parent.glob("*.py"))
        },
    }
    for p in [
        ROOT / "code/physics_guided_optimization_selection/run.py",
        ROOT / "code/physics_guided_diagnostics/run.py",
        ROOT / "code/physics_guided/mechanics.c",
        ROOT / "tests/test_physics_guided_teacher_transfer.py",
    ]:
        sources[str(p.relative_to(ROOT))] = sha(p)
    for relative in [*sources, str(CONFIG.relative_to(ROOT)), spec["protocol"]]:
        target = out / "sources" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    shutil.copy2(learning / "input_prefix_792.csv", out / "input_prefix_792.csv")
    reference = verify_reference()
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
            reference_provenance=reference,
        ),
    )
    seal(out / "protected_before.json", protected)
    teachers = read_json(learning / "teachers.json")
    input_reuse = {}
    for n in CUTOFFS:
        for name in [
            f"scaler_{n}.json",
            *[f"calibration_{n}_{s}.npz" for s in ("P0", "IN", "OOF")],
            *[f"mean_{n}_{s}_{k}/e100.pt" for s in ("IN", "OOF") for k in range(3)],
        ]:
            input_reuse[str((learning / name).relative_to(ROOT))] = sha(learning / name)
    seal(out / "frozen_inputs.json", input_reuse)
    ref = load(REFERENCE)
    original = ref.forward

    def counted(*args, **kwargs):
        if state["reference_trajectory_calls"] >= 4:
            raise RuntimeError("Reference trajectory budget exhausted")
        state["reference_trajectory_calls"] += 1
        return original(*args, **kwargs)

    ref.forward = counted
    try:
        trajectory_paths = {}
        for n, c in CUTOFFS.items():
            drivers = read_drivers(out / "input_prefix_792.csv", n)
            row = next(row for row in spec["folds"] if row["outer_days"] == n)
            record = teachers[str(c)]["record"]
            if (
                teachers[str(c)]["recipe"] != row["old_recipe"]
                or teachers[str(n)]["recipe"] != row["new_recipe"]
            ):
                raise ValueError("Fixed teacher choice differs from v1.5")
            theta = np.asarray(record["theta"])
            if theta.shape != (54,) or np.any(theta < ref.LO) or np.any(theta > ref.HI):
                raise ValueError("Invalid frozen physical parameter")
            path = out / f"old_physical_{n}.npz"
            _, audit = audit_trajectory(ref, theta, drivers, path)
            state["independent_mechanical_trajectories"] += 1
            state["independent_substeps"] += audit["substeps_checked"]
            prior_path = physical / trajectory_name(c, row["old_recipe"])
            with np.load(prior_path) as prior, np.load(path) as extended:
                errors = {}
                for key in prior.files:
                    if key == "dates":
                        if not np.array_equal(
                            prior[key], extended[key][: len(prior[key])]
                        ):
                            raise ValueError("Extended prefix dates differ")
                        continue
                    expected, actual = prior[key], extended[key]
                    if actual.shape != expected.shape:
                        actual = actual[: len(expected)]
                    difference = float(abs(actual - expected).max())
                    if difference > 1e-8:
                        raise ArithmeticError(
                            f"Existing physical prefix changed: {key}"
                        )
                    errors[key] = difference
            seal(
                out / f"prefix_{n}.json",
                dict(prior=str(prior_path.relative_to(ROOT)), differences=errors),
            )
            trajectory_paths[n, "OLD"] = path
            trajectory_paths[n, "NEW"] = physical / trajectory_name(
                n, row["new_recipe"]
            )
            print(
                f"Extended OLD {c}{row['old_recipe']} to {n + 180} days; prefix and {audit['substeps_checked']} substeps checked",
                flush=True,
            )
        predictions = {}
        for n in CUTOFFS:
            drivers = read_drivers(out / "input_prefix_792.csv", n)
            scale_record = read_json(learning / f"scaler_{n}.json")
            scaler = Scaler(
                *[np.asarray(scale_record[k]) for k in ("mean", "scale", "floor")]
            )
            models = {}
            for strategy in ("IN", "OOF"):
                for seed in range(3):
                    model = new_model(seed)
                    model.load_state_dict(
                        torch.load(
                            learning / f"mean_{n}_{strategy}_{seed}/e100.pt",
                            weights_only=True,
                        )
                    )
                    models[strategy, seed] = model
            for branch in ("OLD", "NEW"):
                base, features = teacher_features(
                    trajectory_paths[n, branch], drivers, n
                )
                for strategy in ("P0", "IN", "OOF"):
                    means = (
                        base[None]
                        if strategy == "P0"
                        else np.stack(
                            [
                                predict(models[strategy, k], base, features, scaler)
                                for k in range(3)
                            ]
                        )
                    )
                    with np.load(learning / f"calibration_{n}_{strategy}.npz") as saved:
                        scales = saved["scales"].copy()
                    if branch == "NEW":
                        with np.load(
                            learning / f"prediction_{n}_{strategy}.npz"
                        ) as previous:
                            difference = float(
                                np.nanmax(abs(means - previous["means"]))
                            )
                            if difference > 1e-8 or not np.array_equal(
                                scales, previous["scales"]
                            ):
                                raise ArithmeticError("NEW does not reproduce v1.5")
                    predictions[n, branch, strategy] = (means, scales)
                    np.savez_compressed(
                        out / f"prediction_{n}_{branch}_{strategy}.npz",
                        means=means,
                        scales=scales,
                    )
            print(
                f"Replayed all fixed networks for n={n}; no parameter updates",
                flush=True,
            )
        seal(
            out / "prediction_lock.json",
            dict(
                locked_utc=now(),
                files={p.name: sha(p) for p in out.glob("prediction_*.npz")},
                frozen_inputs_sha256=sha(out / "frozen_inputs.json"),
            ),
        )
        dates, _, labels = load_observations(out / "input_prefix_792.csv", 792)
        metrics, curves, daily, shifts = [], {}, [], []
        for (n, branch, strategy), (means, scales) in predictions.items():
            rows, dist = score(means, scales, labels[: n + 180], n, branch, strategy)
            metrics.extend(rows)
            curves[n, branch, strategy] = dist
            np.savez_compressed(
                out / f"distribution_{n}_{branch}_{strategy}.npz", **dist
            )
        for n in CUTOFFS:
            a, b = n, n + 180
            old_base = predictions[n, "OLD", "P0"][0][0, a:b]
            new_base = predictions[n, "NEW", "P0"][0][0, a:b]
            for strategy in ("P0", "IN", "OOF"):
                old = predictions[n, "OLD", strategy][0][:, a:b].mean(axis=0)
                new = predictions[n, "NEW", strategy][0][:, a:b].mean(axis=0)
                pieces = decompose(old_base, new_base, old, new)
                for j, station in enumerate(POINTS):
                    row = dict(outer_days=n, strategy=strategy, station=station)
                    for name, values in zip(
                        ("physical", "correction", "total"), pieces
                    ):
                        row[f"{name}_mean_mm"] = float(values[:, j].mean())
                        row[f"{name}_rms_mm"] = float(
                            np.sqrt(np.mean(values[:, j] ** 2))
                        )
                        row[f"{name}_max_abs_mm"] = float(abs(values[:, j]).max())
                    shifts.append(row)
                    for t in range(180):
                        daily.append(
                            dict(
                                outer_days=n,
                                strategy=strategy,
                                station=station,
                                date=dates[n + t],
                                lead_day=t + 1,
                                observed_mm=float(labels[n + t, j]),
                                old_physical_mm=float(old_base[t, j]),
                                new_physical_mm=float(new_base[t, j]),
                                old_mean_mm=float(old[t, j]),
                                new_mean_mm=float(new[t, j]),
                                physical_change_mm=float(pieces[0][t, j]),
                                correction_change_mm=float(pieces[1][t, j]),
                                total_change_mm=float(pieces[2][t, j]),
                            )
                        )
        metrics = pd.DataFrame(metrics)
        comparisons = matched_comparisons(metrics)
        metrics.to_csv(out / "metrics.csv", index=False)
        comparisons.to_csv(out / "comparisons.csv", index=False)
        pd.DataFrame(daily).to_csv(out / "daily_decomposition.csv", index=False)
        pd.DataFrame(shifts).to_csv(out / "shift_summary.csv", index=False)
        plot(out, curves, labels, comparisons)
        forward_checks = {}
        for n, c in CUTOFFS.items():
            drivers = read_drivers(out / "input_prefix_792.csv", n)
            with np.load(trajectory_paths[n, "OLD"]) as saved:
                recomputed = (
                    ref.forward(saved["theta"], ref.Context(drivers.forcing))
                    + drivers.y0
                )
                difference = float(abs(recomputed - saved["mean"]).max())
            if difference > 1e-8:
                raise ArithmeticError("Old trajectory forward replay failed")
            forward_checks[str(n)] = difference
        seal(
            out / "physical_verification.json",
            dict(reference_replay_errors_mm=forward_checks, **state),
        )
        check_hashes(protected)
        check_hashes(sources)
        check_hashes(input_reuse)
        from .verify import verify

        seal(out / "verification.json", verify(out))
        seal(
            out / "completion.json",
            dict(
                complete=True,
                finished_utc=now(),
                elapsed_seconds=time.monotonic() - started,
                **state,
                optimizer_nfev=0,
                neural_updates=0,
                scale_refits=0,
            ),
        )
    finally:
        ref.forward = original


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", args.run_id):
        parser.error("Run ID must be one new path component")
    out = ROOT / "results/ootang_bplus_v1_6" / args.run_id
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    state = dict(
        reference_trajectory_calls=0,
        independent_mechanical_trajectories=0,
        independent_substeps=0,
    )

    def timeout(signum, frame):
        raise TimeoutError("600-second comparison budget exhausted; no automatic retry")

    previous = signal.signal(signal.SIGALRM, timeout)
    signal.alarm(600)
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
