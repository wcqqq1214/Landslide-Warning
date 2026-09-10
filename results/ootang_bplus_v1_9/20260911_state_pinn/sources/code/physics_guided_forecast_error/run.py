"""Describe frozen forecasts once, without calibration or neural training."""

import argparse
import contextlib
from datetime import datetime, timezone
import importlib.metadata
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import time
import traceback

import numpy as np
import pandas as pd

from .artifacts import (
    ROOT,
    CONFIG,
    CONFIG_SHA,
    PROTOCOL_SHA,
    array_sha,
    check_hashes,
    index_artifacts,
    load_observations,
    read_json,
    seal,
    sha,
    source_guard,
    trajectory_name,
)
from .core import (
    FEATURES,
    ORIGINS,
    POINTS,
    choose_teacher,
    correlation,
    describe_error,
    driver_features,
    inventory,
)


def figure(rows, out):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(11, 7), sharex=True, layout="constrained")
    for station, ax in zip(POINTS, axes.flat):
        subset = rows[(rows.station == station) & rows.selected_by_training]
        for n in ORIGINS:
            part = subset[subset.fit_days == n]
            ax.plot(
                part.lead_day, part.error_mm, label=f"{n} days / {part.recipe.iloc[0]}"
            )
        ax.axhline(0, color="0.4", linewidth=0.7)
        ax.set(
            title=station,
            xlabel="Days after physical fit",
            ylabel="B+ minus observed (mm)",
        )
        ax.grid(alpha=0.15)
    axes[0, 0].legend(fontsize=8, loc="best")
    fig.suptitle(
        "Frozen B+ forecast errors | own-training J0 selection\nHistorical description; no learned correction",
        fontsize=13,
    )
    fig.savefig(out / "selected_forecast_errors.png", dpi=170)
    plt.close(fig)


def execute(out, started):
    if sha(CONFIG) != CONFIG_SHA:
        raise ValueError("The committed configuration changed")
    spec = read_json(CONFIG)
    if sha(ROOT / spec["protocol"]) != PROTOCOL_SHA:
        raise ValueError("The committed protocol changed")
    protected = source_guard(spec)
    source = ROOT / spec["source_run"]
    sources = [
        *Path(__file__).parent.glob("*.py"),
        ROOT / "tests/test_physics_guided_forecast_error.py",
    ]
    code_hashes = {str(p.relative_to(ROOT)): sha(p) for p in sorted(sources)}
    for p in sources + [CONFIG, ROOT / spec["protocol"]]:
        target = out / "sources" / p.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
    shutil.copy2(source / "input_prefix_792.csv", out / "input_prefix_792.csv")
    dates, forcing, labels = load_observations(out / "input_prefix_792.csv", 792)
    seal(out / "protected_before.json", protected)
    seal(
        out / "manifest.json",
        dict(
            specification=spec,
            started_utc=datetime.now(timezone.utc).isoformat(),
            git_commit=subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            command=sys.argv,
            python=sys.version,
            platform=platform.platform(),
            dependencies={
                p: importlib.metadata.version(p)
                for p in ("numpy", "pandas", "matplotlib")
            },
            sources=code_hashes,
            config_sha256=CONFIG_SHA,
            protocol_sha256=PROTOCOL_SHA,
            input_sha256=sha(out / "input_prefix_792.csv"),
            new_optimizer_nfev=0,
            physical_forward_calls=0,
            neural_training=False,
            interpretation="Exploratory forecast-error description, not new model effectiveness.",
        ),
    )
    records = read_json(source / "fitted_parameters_locked.json")["records"]
    features = driver_features(forcing)
    metrics, daily, correlations, selectors = [], [], [], {}
    for n in ORIGINS:
        objectives = {r: records[f"{n}_{r}"]["record"]["objective"] for r in ("A", "B")}
        selected = choose_teacher(objectives)
        selectors[str(n)] = dict(objectives=objectives, selected=selected)
        for recipe in ("A", "B"):
            envelope = records[f"{n}_{recipe}"]
            record = envelope["record"]
            if not envelope["valid"] or record["fit_days"] != n:
                raise ValueError("Invalid source record")
            if record["training_label_sha256"] != array_sha(labels[:n]) or record[
                "training_forcing_sha256"
            ] != array_sha(forcing[:n]):
                raise ValueError("Source parameter prefix differs")
            if sha(ROOT / record["source"]) != record["source_sha256"]:
                raise ValueError("Source parameter file changed")
            with np.load(
                source / trajectory_name(n, recipe), allow_pickle=False
            ) as trajectory:
                mu = trajectory["mean"]
                if not np.array_equal(
                    trajectory["dates"], dates[: n + 180]
                ) or not np.array_equal(trajectory["theta"], record["theta"]):
                    raise ValueError(
                        "Saved trajectory does not match its locked parameter record"
                    )
            r = (mu[:n] - labels[:n]) / 100
            objective = float(np.sum(r**2) + 100 * n * np.sum(r[-1] ** 2))
            if abs(objective - record["objective"]) > 1e-7:
                raise ArithmeticError(
                    "Saved-curve J0 does not reproduce the training record"
                )
            identity = dict(
                fit_days=n, recipe=recipe, selected_by_training=recipe == selected
            )
            metrics.extend(
                dict(identity, **row)
                for row in describe_error(mu, labels[: n + 180], n)
            )
            for j, station in enumerate(POINTS):
                delta_mu = mu[n:, j] - mu[n - 30 : -30, j]
                delta_y = labels[n : n + 180, j] - labels[n - 30 : n + 150, j]
                for key in FEATURES:
                    correlations.append(
                        dict(
                            identity,
                            station=station,
                            feature=key,
                            pearson=correlation(
                                delta_mu - delta_y, features[key][n : n + 180]
                            ),
                            days=180,
                        )
                    )
                for lead in range(180):
                    t = n + lead
                    daily.append(
                        dict(
                            identity,
                            station=station,
                            date=dates[t],
                            lead_day=lead + 1,
                            predicted_mm=float(mu[t, j]),
                            observed_mm=float(labels[t, j]),
                            error_mm=float(mu[t, j] - labels[t, j]),
                            predicted_increment30_mm=float(delta_mu[lead]),
                            observed_increment30_mm=float(delta_y[lead]),
                            increment30_error_mm=float(delta_mu[lead] - delta_y[lead]),
                            **{key: float(features[key][t]) for key in FEATURES},
                        )
                    )
        print(
            f"Described {n}-day A/B forecasts; own-training selector {selected}",
            flush=True,
        )
    seal(out / "teacher_selection.json", selectors)
    pd.DataFrame(metrics).to_csv(out / "error_metrics.csv", index=False)
    pd.DataFrame(correlations).to_csv(out / "driver_correlations.csv", index=False)
    daily_frame = pd.DataFrame(daily)
    daily_frame.to_csv(out / "daily_errors.csv", index=False)
    seal(
        out / "sample_inventory.json",
        [inventory(n, mode) for n in (432, 612) for mode in spec["inventory_modes"]],
    )
    figure(daily_frame, out)
    check_hashes(protected)
    check_hashes(code_hashes)
    from .verify import verify

    verification = verify(out)
    seal(out / "verification.json", verification)
    seal(
        out / "completion.json",
        dict(
            complete=True,
            elapsed_seconds=time.monotonic() - started,
            new_optimizer_nfev=0,
            physical_forward_calls=0,
            neural_training=False,
            metrics_rows=len(metrics),
            daily_rows=len(daily),
            correlation_rows=len(correlations),
            verification_passed=verification["passed"],
        ),
    )
    print(
        f"Verified {len(metrics)} metric rows, {len(daily)} daily rows and {len(correlations)} correlations.",
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
    started = time.monotonic()

    def timeout(signum, frame):
        raise TimeoutError("120-second diagnostic budget exhausted; no automatic retry")

    previous = signal.signal(signal.SIGALRM, timeout)
    signal.alarm(120)
    try:
        with (
            (out / "run.log").open("x") as log,
            contextlib.redirect_stdout(log),
            contextlib.redirect_stderr(log),
        ):
            try:
                execute(out, started)
            except Exception as exc:
                traceback.print_exc()
                seal(
                    out / "failure.json",
                    dict(error=str(exc), elapsed_seconds=time.monotonic() - started),
                )
                raise
        index_artifacts(out)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
    print(out)


if __name__ == "__main__":
    main()
