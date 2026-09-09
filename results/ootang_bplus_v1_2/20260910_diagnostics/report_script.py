"""Export diagnostic tables/figures from completed v1.2 artifacts; never fit models."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd


def write_json(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )


def render(out):
    completed = json.loads((out / "completion.json").read_text())
    convergence, selected, all_metrics, all_growth = [], [], [], []
    total_nfev = total_forward = postchecks = 0
    for n in (432, 612, 792):
        scope = out / f"fit_{n}"
        if not (scope / "selection.json").exists():
            continue
        selection = json.loads((scope / "selection.json").read_text())
        try:
            metrics = pd.read_csv(scope / "metrics.csv")
            growth = pd.read_csv(scope / "growth.csv")
        except pd.errors.EmptyDataError:
            continue
        all_metrics.append(metrics.assign(fit_days=n))
        all_growth.append(growth.assign(fit_days=n))
        for path in list(scope.glob("*_stage[123].json")) + list(
            scope.glob("*_continued.json")
        ):
            record = json.loads(path.read_text())
            total_nfev += record["nfev"]
            total_forward += record["optimization_forward_calls"]
            postchecks += record["postcheck_forward_calls"]
        for start in ("A", "B"):
            before_path, after_path = (
                scope / f"{start}_baseline.json",
                scope / f"{start}_continued.json",
            )
            if not before_path.exists() or not after_path.exists():
                continue
            before, after = (
                json.loads(before_path.read_text()),
                json.loads(after_path.read_text()),
            )
            convergence.append(
                dict(
                    fit_days=n,
                    start=start,
                    objective_before=before["objective"],
                    objective_after=after["objective"],
                    objective_reduction_percent=100
                    * (before["objective"] - after["objective"])
                    / before["objective"],
                    before_optimality=before["optimality"],
                    after_optimality=after["optimality"],
                    success=after["success"],
                    gtol_met=after["gtol_met"],
                    termination=after["message"],
                    nfev=after["nfev"],
                    parameter_change_bound_fraction=after[
                        "max_parameter_change_bound_fraction"
                    ],
                )
            )
        for variant in ("baseline", "continued"):
            start = selection[variant]["selected_start"]
            if start is None:
                continue
            candidate = f"{start}_{variant}"
            rows = metrics[
                (metrics.candidate == candidate)
                & (metrics.station == "four_point_mean")
            ]
            for _, row in rows.iterrows():
                selected.append(
                    dict(
                        fit_days=n,
                        variant=variant,
                        selected_start=start,
                        phase=row.phase,
                        days=int(row.days),
                        rmse_mm=float(row.rmse_mm),
                        mae_mm=float(row.mae_mm),
                        bias_mm=float(row.bias_mm),
                    )
                )
        if n < 792:
            plot_fold(scope, n, selection, out)
    pd.DataFrame(convergence).to_csv(out / "convergence_summary.csv", index=False)
    pd.DataFrame(selected).to_csv(out / "selected_summary.csv", index=False)
    if all_metrics:
        pd.concat(all_metrics, ignore_index=True).to_csv(
            out / "metrics_all_candidates.csv", index=False
        )
        pd.concat(all_growth, ignore_index=True).to_csv(
            out / "growth_all_candidates.csv", index=False
        )
    write_json(
        out / "summary.json",
        dict(
            execution_status=completed["status"],
            new_optimizer_nfev=total_nfev,
            optimization_forward_calls=total_forward,
            optimizer_terminal_postchecks=postchecks,
            audit_calls_excluded=True,
            selected=selected,
            convergence=convergence,
            gtol_met_count=sum(r["gtol_met"] for r in convergence),
            continuation_count=len(convergence),
            user_acceptance="pending",
            claim_scope="exploratory within first 792 days; no neural training or new blind test",
        ),
    )
    write_json(
        out / "export_provenance.json",
        dict(
            script=str(Path(__file__).resolve()),
            script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            source_completion_sha256=hashlib.sha256(
                (out / "completion.json").read_bytes()
            ).hexdigest(),
            matplotlib_version=matplotlib.__version__,
            pandas_version=pd.__version__,
        ),
    )
    if Path(__file__).resolve() != (out / "report_script.py").resolve():
        shutil.copy2(Path(__file__), out / "report_script.py")


def plot_fold(scope, n, selection, out):
    data = pd.read_csv(scope / "predictions.csv", parse_dates=["date"])
    cutoff = pd.Timestamp("2016-07-01") + pd.Timedelta(days=n - 1)
    points = ["ATU1", "ATU5", "MJ3", "MJ1"]
    fig, axes = plt.subplots(2, 2, figsize=(12, 7.5), sharex=True, layout="constrained")
    for ax, point in zip(axes.flat, points):
        station = data[
            (data.station == point) & (data.date >= cutoff - pd.Timedelta(days=59))
        ]
        observed = (
            station[["date", "observed_mm"]].drop_duplicates().sort_values("date")
        )
        ax.plot(
            observed.date,
            observed.observed_mm,
            color="#222222",
            lw=1.5,
            label="Observed",
        )
        for variant, color, style in [
            ("baseline", "#2b6cb0", "--"),
            ("continued", "#b45309", "-"),
        ]:
            start = selection[variant]["selected_start"]
            if start is None:
                continue
            frame = station[station.candidate == f"{start}_{variant}"].sort_values(
                "date"
            )
            ax.plot(
                frame.date,
                frame.mean_mm,
                color=color,
                ls=style,
                lw=1.4,
                label=f"{variant.title()} (start {start})",
            )
        ax.axvline(cutoff, color="#888888", lw=0.8)
        ax.axvspan(cutoff, observed.date.max(), color="#dceaf4", alpha=0.3)
        ax.set_title(point, loc="left", weight="bold")
        ax.set_ylabel("Displacement (mm)")
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax.grid(alpha=0.2)
        ax.spines[["top", "right"]].set_visible(False)
        ax.legend(fontsize=8, loc="best")
    fig.suptitle(
        f"B+ prefix diagnostic: {n} fitting days + 180 prediction days\n"
        "Shaded: conditional prediction; starts selected by training objective",
        fontsize=12,
    )
    fig.savefig(out / f"rolling_{n}.png", dpi=180)
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_directory", type=Path)
    render(parser.parse_args().result_directory)
