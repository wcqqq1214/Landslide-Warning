"""Summarize verified, saved v1.9 predictions without training or integration."""

import argparse
import json
from pathlib import Path
import shutil

import numpy as np
import pandas as pd

from physics_guided_forecast_error.artifacts import (
    ROOT,
    check_hashes,
    check_index,
    index_artifacts,
    read_json,
    seal,
    sha,
)
from physics_guided_pinn.run_substep_audit import load_npz
from .workflow import POINTS, now, read_labels


def summarize(source, verification, out):
    for path in (source, verification):
        check_index(path)
    checked = read_json(verification / "manifest.json")
    if (
        checked["source_run"] != str(source.relative_to(ROOT))
        or checked["source_index_sha256"] != sha(source / "artifact_manifest.json")
        or not read_json(verification / "verification.json")["passed"]
    ):
        raise ValueError("Verification does not bind the selected source run")
    check_hashes(checked["sources"])
    check_hashes(read_json(source / "protected_before.json"))
    check_hashes(read_json(source / "manifest.json")["sources"])
    labels = read_labels(source / "input_prefix_792.csv", 792)
    metrics = pd.read_csv(source / "metrics.csv")
    historical = (
        ROOT / read_json(source / "manifest.json")["specification"]["comparison_source"]
    )
    history = pd.read_csv(historical / "metrics.csv")
    combined = pd.concat([metrics, history[history.strategy.isin(["U", "L"])]])
    columns = ["rmse_mm", "mae_mm", "crps_mm"] + [
        f"{name}_{level}{suffix}"
        for level in (80, 90, 95)
        for name, suffix in (
            ("coverage", ""),
            ("width", "_mm"),
            ("interval_score", "_mm"),
        )
    ]
    summary = (
        combined.groupby(["outer_days", "strategy", "part"])[columns]
        .mean()
        .reset_index()
    )
    comparisons = pd.read_csv(source / "comparisons.csv")
    strict = (
        comparisons.groupby(["outer_days", "strategy"])["strict_mean_improvement"]
        .sum()
        .reset_index()
    )
    strict["point_windows"] = 4
    training = pd.read_csv(source / "training.csv")
    objectives, discrepancy, mechanics = [], [], []
    for h in (342, 432, 612):
        for seed in (0, 1, 2):
            directory = source / f"model_{h}_{seed}"
            prediction = load_npz(directory / "P.npz")
            diagnostic = read_json(directory / "diagnostics.json")
            audit = read_json(directory / "replay_audit.json")
            initial = training[
                (training.prefix == h) & (training.seed == seed) & (training.epoch == 1)
            ].iloc[0]
            data = float(
                np.mean(((prediction["mean"][30:h] - labels[30:h]) / 100) ** 2)
            )
            physics = float(
                np.mean(
                    [
                        np.mean(np.asarray(item["normalized_rms_by_domain"]) ** 2)
                        for item in diagnostic["train"]["equations"].values()
                    ]
                )
            )
            rate = float(
                np.mean((np.log(prediction["multiplier"][1:h]) / np.log(2)) ** 2)
            )
            objectives.append(
                dict(
                    prefix=h,
                    seed=seed,
                    e0_objective_at_lambda1=initial.data
                    + initial.physics
                    + 0.001 * initial.rate_prior,
                    e200_data=data,
                    e200_physics=physics,
                    e200_rate_prior=rate,
                    e200_objective_at_lambda1=data + physics + 0.001 * rate,
                )
            )
            mechanics.append(
                dict(
                    prefix=h,
                    seed=seed,
                    r_checks_passed=audit["passed"],
                    r_min_dx_mm=audit["native"]["min_dx"],
                    r_min_gap_original_units=audit["native"]["min_gap"],
                    r_max_normalized_complementarity=audit["native"][
                        "max_normalized_complementarity"
                    ],
                    p_prediction_max_normalized_complementarity=diagnostic[
                        "prediction"
                    ]["normalized_complementarity_max"],
                    p_prediction_complementarity_violation_fraction=diagnostic[
                        "prediction"
                    ]["normalized_complementarity_violation_fraction"],
                )
            )
            for part in ("train", "prediction"):
                for j, station in enumerate(POINTS):
                    discrepancy.append(
                        dict(
                            prefix=h,
                            seed=seed,
                            part=part,
                            station=station,
                            p_minus_r_rms_mm=diagnostic[part][
                                "p_minus_r_rms_by_point_mm"
                            ][j],
                            p_minus_r_max_abs_mm=diagnostic[part][
                                "p_minus_r_max_abs_by_point_mm"
                            ][j],
                        )
                    )
    out.mkdir(exist_ok=False)
    source_name = str(Path(__file__).resolve().relative_to(ROOT))
    source_hash = sha(ROOT / source_name)
    destination = out / "sources" / source_name
    destination.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / source_name, destination)
    seal(
        out / "manifest.json",
        dict(
            kind="descriptive_summary_of_saved_predictions",
            source_run=str(source.relative_to(ROOT)),
            source_index_sha256=sha(source / "artifact_manifest.json"),
            verification_run=str(verification.relative_to(ROOT)),
            verification_index_sha256=sha(verification / "artifact_manifest.json"),
            sources={source_name: source_hash},
            created_utc=now(),
            aggregation="equal arithmetic mean of four point metrics; no model selection",
        ),
    )
    for name, frame in (
        ("mean_metrics", summary),
        ("strict_improvement", strict),
        ("training_objectives", pd.DataFrame(objectives)),
        ("state_replay_discrepancy", pd.DataFrame(discrepancy)),
        ("mechanical_summary", pd.DataFrame(mechanics)),
    ):
        frame.to_csv(out / f"{name}.csv", index=False, lineterminator="\n")
    plot_saved(source, out, labels)
    for path in (source, verification):
        check_index(path)
    check_hashes({source_name: source_hash})
    seal(
        out / "completed.json",
        dict(
            descriptive_summary_complete=True,
            new_optimizer_updates=0,
            new_native_integrations=0,
            new_neural_forward_evaluations=0,
            objectives=9,
            discrepancy_rows=72,
            completed_utc=now(),
        ),
    )
    index_artifacts(out)


def plot_saved(source, out, labels):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    curves = {
        (n, strategy): load_npz(source / f"distribution_{n}_{strategy}.npz")
        for n in (432, 612)
        for strategy in ("P0", "P", "R")
    }
    for intervals in (False, True):
        fig, axes = plt.subplots(4, 2, figsize=(12, 14))
        fig.subplots_adjust(
            left=0.09, right=0.98, bottom=0.055, top=0.91, hspace=0.48, wspace=0.23
        )
        for column, n in enumerate((432, 612)):
            for j, point in enumerate(POINTS):
                ax = axes[j, column]
                for strategy, title, color, style in (
                    ("P0", "B+", "0.45", "-"),
                    ("P", "P state (diagnostic)", "#d27b17", "--"),
                    ("R", "R replay (primary)", "#4466ab", "-"),
                ):
                    values = curves[n, strategy]
                    ax.plot(
                        np.arange(1, 181),
                        values["mean"][n - 30 :, j],
                        color=color,
                        linestyle=style,
                        label=title,
                    )
                    if intervals and strategy == "R":
                        ax.fill_between(
                            np.arange(1, 181),
                            values["lower_90"][n - 30 :, j],
                            values["upper_90"][n - 30 :, j],
                            color=color,
                            alpha=0.16,
                            label="R 90% interval",
                        )
                ax.plot(
                    np.arange(1, 181),
                    labels[n : n + 180, j],
                    color="black",
                    label="Observed",
                )
                ax.set(
                    title=f"{point} | outer {n} days",
                    xlabel="Forecast day",
                    ylabel="Displacement (mm)",
                )
                ax.grid(alpha=0.15)
        handles, titles = axes[0, 0].get_legend_handles_labels()
        fig.legend(
            handles, titles, loc="upper center", bbox_to_anchor=(0.5, 0.985), ncol=3
        )
        fig.savefig(
            out / ("forecast_intervals.png" if intervals else "forecast_means.png"),
            dpi=150,
        )
        plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--verification-id", required=True)
    parser.add_argument("--output-id", required=True)
    args = parser.parse_args()
    if any(not name or Path(name).name != name for name in vars(args).values()):
        raise ValueError("Unsafe run or output id")
    root = ROOT / "results/ootang_bplus_v1_9"
    summarize(root / args.run_id, root / args.verification_id, root / args.output_id)
    print(json.dumps({"output": str(root / args.output_id), "completed": True}))


if __name__ == "__main__":
    main()
