"""Export only the fixed v1.4 comparisons; never fit or alter a selection lock."""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from physics_guided.data import POINTS
from .run import read_json, seal


def compare(metrics, selections):
    table = metrics.set_index(["fit_days", "candidate", "phase", "station"])
    rows, acceptance = [], []
    for n in (432, 612):
        selected = selections[str(n)]
        t, v = selected["T"], selected["V"]["selected_recipe"]
        for station in POINTS:
            differences = []
            for phase in ("train", "prediction"):
                keys = [(n, r, phase, station) for r in (t, v)]
                if not all(k in table.index for k in keys):
                    continue
                a, b = [table.loc[k] for k in keys]
                row = dict(
                    fit_days=n, station=station, phase=phase, T_recipe=t, V_recipe=v
                )
                for field in ("rmse_mm", "mae_mm", "bias_mm"):
                    row[f"T_{field}"] = float(a[field])
                    row[f"V_{field}"] = float(b[field])
                    row[f"change_{field}"] = float(b[field] - a[field])
                rows.append(row)
                differences.extend([row["change_rmse_mm"], row["change_mae_mm"]])
            valid = len(differences) == 4
            acceptance.append(
                dict(
                    fit_days=n,
                    station=station,
                    evaluable=valid,
                    simultaneously_improved=valid
                    and all(d < -1e-6 for d in differences),
                )
            )
    return pd.DataFrame(rows), pd.DataFrame(acceptance)


def export(out):
    if (out / "summary.json").exists() or (out / "artifact_manifest.json").exists():
        raise FileExistsError("Report already exported or evidence sealed")
    task = read_json(out / "manifest.json")["task"]
    summary = dict(
        task=task,
        **read_json(out / "completion.json"),
        effectiveness="not inferred from execution",
    )
    if task == "A":
        summary["prefixes"] = {}
        for n in (432, 612):
            path = out / f"A_{n}/retained_locked.json"
            if not path.exists():
                summary["prefixes"][str(n)] = dict(status="unavailable")
                continue
            locked = read_json(path)
            gradient = read_json(out / f"A_{n}/retained_gradient.json")
            stage_path = out / f"A_{n}/stage1.json"
            stage = read_json(stage_path) if stage_path.exists() else {}
            summary["prefixes"][str(n)] = {
                k: v for k, v in locked.items() if k != "theta"
            } | dict(
                retained_optimality=gradient["optimality"],
                attempt_optimality=stage.get("optimality"),
                attempt_message=stage.get("message"),
                attempt_nfev=stage.get("nfev"),
                relative_improvement_percent=100
                * locked["objective_reduction"]
                / locked["anchor_objective"],
            )
    else:
        selected = read_json(out / "selection_locked.json")["selections"]
        metrics = pd.read_csv(out / "metrics.csv")
        comparison, acceptance = compare(metrics, selected)
        comparison.to_csv(out / "comparison.csv", index=False)
        acceptance.to_csv(out / "acceptance.csv", index=False)
        summary.update(
            selected_improved=int(acceptance.simultaneously_improved.sum()),
            selected_total=8,
            evaluable=int(acceptance.evaluable.sum()),
            folds={},
        )
        windows = read_json(out / "inner_scores_locked.json")
        for n in (432, 612):
            choice = selected[str(n)]
            row = dict(
                T=choice["T"],
                V=choice["V"]["selected_recipe"],
                inner_candidate_means=choice["V"]["candidates"],
                inner_window_scores={},
            )
            for inner, pair in windows[str(n)].items():
                row["inner_window_scores"][inner] = {
                    r: dict(
                        rmse_mm=float(np.mean(score["rmse_mm"])),
                        mae_mm=float(np.mean(score["mae_mm"])),
                    )
                    if score.get("valid")
                    else score
                    for r, score in pair.items()
                }
            for arm in ("T", "V"):
                frame = metrics[
                    (metrics.fit_days == n)
                    & (metrics.candidate == row[arm])
                    & (metrics.station == "four_point_mean")
                ]
                row[arm + "_metrics"] = {
                    r.phase: dict(rmse_mm=r.rmse_mm, mae_mm=r.mae_mm, bias_mm=r.bias_mm)
                    for r in frame.itertuples()
                }
            summary["folds"][str(n)] = row
        plot(out, selected)
    seal(out / "summary.json", summary)
    return summary


def plot(out, selections):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    data = pd.read_csv(out / "predictions.csv", parse_dates=["date"])
    for n in (432, 612):
        cutoff = pd.Timestamp("2016-07-01") + pd.Timedelta(days=n)
        fig, axes = plt.subplots(
            2, 2, figsize=(12, 7.5), sharex=True, layout="constrained"
        )
        selected = selections[str(n)]
        for ax, point in zip(axes.flat, POINTS):
            frame = data[
                (data.fit_days == n)
                & (data.station == point)
                & (data.date >= cutoff - pd.Timedelta(days=60))
            ]
            observed = (
                frame[["date", "observed_mm"]].drop_duplicates().sort_values("date")
            )
            ax.plot(
                observed.date,
                observed.observed_mm,
                color="#222222",
                lw=1.5,
                label="Observed",
            )
            for recipe, color in (("A", "#1b9e77"), ("B", "#d95f02")):
                tags = [
                    arm
                    for arm, chosen in [
                        ("T", selected["T"]),
                        ("V", selected["V"]["selected_recipe"]),
                    ]
                    if chosen == recipe
                ]
                f = frame[frame.recipe == recipe].sort_values("date")
                ax.plot(
                    f.date,
                    f.mean_mm,
                    color=color,
                    lw=1.7,
                    label=f"Recipe {recipe} ({', '.join(tags) or 'unselected'})",
                )
            ax.axvline(cutoff, color="#666666", ls="--", lw=1)
            ax.set_title(point)
            ax.set_ylabel("Displacement (mm)")
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
            ax.grid(alpha=0.2)
            ax.legend(fontsize=8)
        fig.suptitle(
            f"B+ v1.4 | {n}-day training | 180-day historical forecast\nT: training objective; V: internal temporal selection"
        )
        fig.savefig(out / f"selection_{n}.png", dpi=160)
        fig.savefig(out / f"selection_{n}.pdf")
        plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(export(args.output))
