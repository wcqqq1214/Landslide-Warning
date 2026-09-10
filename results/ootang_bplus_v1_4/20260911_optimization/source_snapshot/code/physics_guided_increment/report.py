"""Export the prespecified comparison from saved curves; no fitting or selection tuning."""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

from physics_guided.data import POINTS
from physics_guided.reference import ROOT, save_json, sha


def comparisons(metrics, increments, selections):
    joined = metrics.merge(
        increments,
        on=["fit_days", "candidate", "phase", "station", "days"],
        validate="one_to_one",
    ).set_index(["fit_days", "candidate", "phase", "station"])
    rows, acceptance = [], []
    fields = ["rmse_mm", "mae_mm", "bias_mm", "delta30_rmse_mm", "delta30_mae_mm"]
    for n in (432, 612):
        selected = [
            selections[str(n)][f"{arm}_final"]["selected_start"] for arm in ("C0", "C1")
        ]
        for comparison, starts in [
            ("selected", selected),
            ("paired_A", ["A", "A"]),
            ("paired_B", ["B", "B"]),
        ]:
            if None in starts:
                continue
            names = [f"{arm}_{start}_final" for arm, start in zip(("C0", "C1"), starts)]
            for station in [*POINTS, "four_point_mean"]:
                changes = []
                for phase in ("train", "prediction"):
                    keys = [(n, name, phase, station) for name in names]
                    if not all(key in joined.index for key in keys):
                        continue
                    control, candidate = [joined.loc[key] for key in keys]
                    row = dict(
                        fit_days=n,
                        comparison=comparison,
                        C0_start=starts[0],
                        C1_start=starts[1],
                        phase=phase,
                        station=station,
                        days=int(control.days),
                    )
                    for field in fields:
                        row[f"C0_{field}"] = float(control[field])
                        row[f"C1_{field}"] = float(candidate[field])
                        row[f"change_{field}"] = float(
                            candidate[field] - control[field]
                        )
                    rows.append(row)
                    changes.extend([row["change_rmse_mm"], row["change_mae_mm"]])
                if station in POINTS and len(changes) == 4:
                    acceptance.append(
                        dict(
                            fit_days=n,
                            comparison=comparison,
                            station=station,
                            C0_start=starts[0],
                            C1_start=starts[1],
                            simultaneously_improved=all(
                                change < -1e-6 for change in changes
                            ),
                        )
                    )
    return pd.DataFrame(rows), pd.DataFrame(acceptance)


def plot_fold(out, n, selections):
    data = pd.read_csv(out / "predictions.csv", parse_dates=["date"])
    cutoff = pd.Timestamp("2016-07-01") + pd.Timedelta(days=n - 1)
    fig, axes = plt.subplots(2, 2, figsize=(12, 7.5), sharex=True, layout="constrained")
    for ax, point in zip(axes.flat, POINTS):
        frame = data[
            (data.fit_days == n)
            & (data.station == point)
            & (data.date >= cutoff - pd.Timedelta(days=59))
        ]
        observations = (
            frame[["date", "observed_mm"]].drop_duplicates().sort_values("date")
        )
        ax.plot(
            observations.date,
            observations.observed_mm,
            color="#222222",
            lw=1.5,
            label="Observed",
        )
        for arm, color, style, label in [
            ("C0", "#2b6cb0", "--", "Original loss"),
            ("C1", "#b45309", "-", "+ 30-day increment loss"),
        ]:
            start = selections[str(n)][f"{arm}_final"]["selected_start"]
            if start is None:
                continue
            curve = frame[frame.candidate == f"{arm}_{start}_final"].sort_values("date")
            ax.plot(
                curve.date,
                curve.mean_mm,
                color=color,
                ls=style,
                lw=1.4,
                label=f"{label} ({start})",
            )
        ax.axvline(cutoff, color="#888888", lw=0.8)
        ax.axvspan(cutoff, observations.date.max(), color="#dceaf4", alpha=0.3)
        ax.set_title(point, loc="left", weight="bold")
        ax.set_ylabel("Displacement (mm)")
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax.grid(alpha=0.2)
        ax.spines[["top", "right"]].set_visible(False)
        ax.legend(fontsize=8, loc="best")
    fig.suptitle(
        f"B+ increment-objective comparison: {n} fitting days + 180 prediction days\n"
        "Shaded: conditional prediction; starts selected by each training objective",
        fontsize=12,
    )
    fig.savefig(out / f"increment_{n}.png", dpi=180)
    fig.savefig(out / f"increment_{n}.pdf")
    plt.close(fig)


def render(out):
    completed = json.loads((out / "completion.json").read_text())
    selections = json.loads((out / "selection.json").read_text())
    metrics = pd.read_csv(out / "metrics.csv")
    increments = pd.read_csv(out / "increment_metrics.csv")
    compared, accepted = comparisons(metrics, increments, selections)
    compared.to_csv(out / "comparison.csv", index=False)
    accepted.to_csv(out / "acceptance.csv", index=False)
    selected = compared[
        (compared.comparison == "selected") & (compared.station == "four_point_mean")
    ]
    selected.to_csv(out / "selected_summary.csv", index=False)
    convergence, objectives = [], []
    for n in (432, 612):
        for start in ("A", "B"):
            for stage in (1, 2, 3, 4):
                path = out / f"fit_{n}/{start}/stage{stage}.json"
                if not path.exists():
                    continue
                record = json.loads(path.read_text())
                convergence.append(
                    dict(
                        fit_days=n,
                        start=start,
                        stage=stage,
                        **{
                            key: record[key]
                            for key in [
                                "objective",
                                "trajectory_objective",
                                "terminal_objective",
                                "increment_objective",
                                "nfev",
                                "max_nfev",
                                "optimization_forward_calls",
                                "postcheck_forward_calls",
                                "success",
                                "status",
                                "message",
                                "optimality",
                                "gtol_met",
                                "max_parameter_change_bound_fraction",
                                "seconds",
                            ]
                        },
                    )
                )
        for path in sorted((out / f"fit_{n}").glob("*_audit.json")):
            name = path.name.removesuffix("_audit.json")
            arm, start, stage = name.split("_")
            record = json.loads(path.read_text())
            objectives.append(
                dict(
                    fit_days=n,
                    candidate=name,
                    arm=arm,
                    start=start,
                    stage=stage,
                    training_selected=selections[str(n)][f"{arm}_{stage}"][
                        "selected_start"
                    ]
                    == start,
                    **{
                        key: record[key]
                        for key in [
                            "objective",
                            "trajectory_objective",
                            "terminal_objective",
                            "increment_objective",
                            "original_objective",
                            "joint_objective",
                        ]
                    },
                )
            )
        plot_fold(out, n, selections)
    pd.DataFrame(convergence).to_csv(out / "convergence.csv", index=False)
    pd.DataFrame(objectives).to_csv(out / "objective_components.csv", index=False)
    final_stages = [r for r in convergence if r["stage"] == 4]
    counts = []
    for (n, comparison), frame in accepted.groupby(["fit_days", "comparison"]):
        counts.append(
            dict(
                fit_days=int(n),
                comparison=comparison,
                improved=int(frame.simultaneously_improved.sum()),
                total=len(frame),
            )
        )
    main_counts = [r for r in counts if r["comparison"] == "selected"]
    save_json(
        out / "summary.json",
        dict(
            execution_status=completed["status"],
            new_optimizer_nfev=sum(r["nfev"] for r in convergence),
            optimization_forward_calls=sum(
                r["optimization_forward_calls"] for r in convergence
            ),
            optimizer_terminal_postchecks=sum(
                r["postcheck_forward_calls"] for r in convergence
            ),
            audit_calls_excluded=True,
            final_stage_gtol_met_count=sum(r["gtol_met"] for r in final_stages),
            final_stage_count=len(final_stages),
            selected=selected.to_dict("records"),
            acceptance=counts,
            selected_improved=sum(r["improved"] for r in main_counts),
            selected_total=sum(r["total"] for r in main_counts),
            user_acceptance="pending",
            claim_scope="exploratory historical conditional displacement comparison; no new blind test or probability model",
        ),
    )
    save_json(
        out / "export_provenance.json",
        dict(
            source=str(Path(__file__).resolve().relative_to(ROOT)),
            source_sha256=sha(Path(__file__)),
            source_completion_sha256=sha(out / "completion.json"),
            matplotlib_version=matplotlib.__version__,
            pandas_version=pd.__version__,
        ),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_directory", type=Path)
    render(parser.parse_args().result_directory)
