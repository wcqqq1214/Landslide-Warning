"""Figures from complete frozen forecasts, with explicit plot data and layout QA.

Figure contract: (1) per-horizon family comparison, (2) longest-horizon actual
curves and empirical coverage, (3) paired residual/physics increments.
Archetype: quantitative grids. Each point panel preserves all legal dates.
The center is the score of the issued equal-weight predictive mean, not an
average of seed scores; seed dispersion is in the separate CSV. Bands in the
time-series plots are 90% marginal prediction intervals, not confidence bands.
Python/matplotlib only; 166-mm report width, editable PDF/SVG, 300-dpi previews.
"""

import argparse
import json
from pathlib import Path
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import FuncFormatter
import numpy as np
import pandas as pd
from scipy.special import ndtri

from .common import ROOT, load_spec, save_json, sha, check_deadline
from .data import observations
from .verify import arrays

SKILL = Path("/Users/wcqqq1214/.codex/skills/nature-figure/scripts")
sys.path.insert(0, str(SKILL))
from audit_panel_alignment import require_matplotlib_panel_alignment

plt.rcParams.update(
    {
        "font.family": "sans-serif",
        "font.sans-serif": ["Arial", "DejaVu Sans"],
        "font.size": 7,
        "axes.titlesize": 8,
        "axes.labelsize": 7,
        "xtick.labelsize": 6.5,
        "ytick.labelsize": 6.5,
        "legend.fontsize": 7,
        "pdf.fonttype": 42,
        "svg.fonttype": "none",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.linewidth": 0.65,
        "lines.linewidth": 1.1,
        "legend.frameon": False,
    }
)
COLORS = {
    "B+": "#777777",
    "ConvLSTM": "#c97934",
    "PINN": "#93719d",
    "Ridge": "#365f91",
    "DRIFT1": "#6f8e72",
}
LABELS = {
    "B+": "B+",
    "ConvLSTM": "ConvLSTM",
    "PINN": "PINN (physics check failed)",
    "Ridge": "Online regression",
    "DRIFT1": "Velocity extrapolation",
}


def finish(fig, path, axes=None):
    fig.canvas.draw()
    require_matplotlib_panel_alignment(
        fig,
        json_out=str(path) + ".alignment.json",
        tolerance_pt=1.5,
        gutter_tolerance_pt=1.5,
        strict=True,
    )
    fig.savefig(str(path) + ".pdf")
    fig.savefig(str(path) + ".svg")
    fig.savefig(str(path) + ".png", dpi=300)
    plt.close(fig)


def overview(out):
    fig, ax = plt.subplots(figsize=(166 / 25.4, 35 / 25.4))
    ax.axis("off")
    boxes = [
        (0.02, 0.54, 0.25, 0.29, "Past 30 days\nObserved displacement + drivers"),
        (
            0.37,
            0.54,
            0.26,
            0.29,
            "Four model families\nDirect / residual / physics pairs",
        ),
        (0.73, 0.54, 0.25, 0.29, "Seven endpoints\nDisplacement at days 1 to 7"),
    ]
    for x, y, w, h, text in boxes:
        ax.add_patch(
            plt.Rectangle(
                (x, y), w, h, facecolor="#f1f5f9", edgecolor="#365f91", linewidth=0.8
            )
        )
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=7)
    for x1, x2 in ((0.28, 0.36), (0.64, 0.72)):
        ax.annotate(
            "",
            (x2, 0.685),
            (x1, 0.685),
            arrowprops=dict(arrowstyle="->", color="#365f91", lw=0.9),
        )
    ax.text(
        0.5,
        0.30,
        "Only matured errors calibrate intervals; no true intermediate future displacement is used.",
        ha="center",
        fontsize=7,
    )
    ax.text(
        0.5,
        0.08,
        "Internal choice  →  Development model lock  →  Fixed later evaluation",
        ha="center",
        color="#365f91",
        fontsize=8,
    )
    fig.subplots_adjust(0, 0.08, 1, 0.96)
    finish(fig, out / "workflow")


def horizon_comparison(root, out):
    df = pd.read_csv(root / "analysis/family_representatives.csv")
    allscores = pd.read_csv(root / "analysis/summary_by_horizon.csv")
    fig, axs = plt.subplots(2, 2, figsize=(166 / 25.4, 118 / 25.4), sharex=True)
    for row, (phase, title) in enumerate(
        [("development", "Development"), ("later_exploratory", "Later evaluation")]
    ):
        for col, metric in enumerate(["rmse", "crps"]):
            ax = axs[row, col]
            for family in ("B+", "ConvLSTM", "PINN", "Ridge", "DRIFT1"):
                s = (
                    df[(df.phase == phase) & (df.family == family)]
                    if family != "DRIFT1"
                    else allscores[
                        (allscores.phase == phase) & (allscores.model == "DRIFT1")
                    ]
                )
                s = s.sort_values("horizon")
                assert len(s) == 7 and (s[metric] > 0).all()
                ax.plot(
                    s.horizon,
                    s[metric],
                    marker={
                        "B+": "s",
                        "ConvLSTM": "^",
                        "PINN": "D",
                        "Ridge": "o",
                        "DRIFT1": "x",
                    }[family],
                    markersize=3,
                    color=COLORS[family],
                    label=LABELS[family],
                    ls="--" if family == "DRIFT1" else "-",
                )
            ax.set_yscale("log")
            ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
            ax.set_xticks(range(1, 8))
            ax.grid(axis="y", alpha=0.18)
            ax.set_title(
                f"{chr(97 + row * 2 + col)}  {title}", loc="left", fontweight="bold"
            )
            ax.set_ylabel(
                ("Mean point RMSE" if metric == "rmse" else "Mean CRPS")
                + " / mm (log scale)"
            )
            if row == 1:
                ax.set_xlabel("Forecast horizon / days")
    handles, labels = axs[0, 0].get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        loc="upper center",
        bbox_to_anchor=(0.51, 1.0),
        ncol=3,
        columnspacing=1.6,
    )
    fig.subplots_adjust(
        left=0.115, right=0.985, bottom=0.105, top=0.80, wspace=0.35, hspace=0.40
    )
    finish(fig, out / "horizon_comparison")


def point_curves(spec, root, out, y, dates, selection):
    sources = []
    plot_rows = []
    phase = "later_exploratory"
    start, end = spec["stages"][phase]
    for h in range(1, 8):
        check_deadline(spec)
        chosen = selection[h - 1]["recommended"] or selection[h - 1]["mean_best"]
        predictions = {
            n: arrays(root / phase / (n + ".npz"))
            for n in (chosen, "B_ANCHOR", "DRIFT1")
        }
        origins = predictions[chosen]["origins"]
        mask = origins + h <= end
        origins = origins[mask]
        ids = origins + h - 1
        time = pd.to_datetime(dates[ids])
        truth = y[ids]
        last = y[origins - 1]
        for p, point in enumerate(spec["points"]):
            fig, axs = plt.subplots(3, 1, figsize=(166 / 25.4, 90 / 25.4), sharex=True)
            ax = axs[0]
            ax.plot(time, truth[:, p], color="#222222", label="Observed", lw=1.45)
            for name, color, label, ls in [
                (chosen, "#365f91", "Selected mean", "--"),
                ("B_ANCHOR", "#999999", "B+", "-."),
                ("DRIFT1", "#6f8e72", "Velocity extrapolation", ":"),
            ]:
                pred = predictions[name]["mean"][mask, h - 1, p]
                ax.plot(time, pred, color=color, label=label, ls=ls, lw=1.0)
                axs[1].plot(time, pred - last[:, p], color=color, ls=ls, lw=1.0)
            axs[1].plot(time, truth[:, p] - last[:, p], color="#222222", lw=1.0)
            mean = predictions[chosen]["mean"][mask, h - 1, p]
            sigma = predictions[chosen]["sigma"][mask, h - 1, p]
            error = truth[:, p] - mean
            q = ndtri(0.95) * sigma
            axs[2].fill_between(
                time, -q, q, color="#b6cce3", alpha=0.75, label="90% Prediction band"
            )
            axs[2].plot(time, error, color="#365f91", label="Observed - mean", lw=0.85)
            axs[2].axhline(0, color="#888888", lw=0.5)
            for ax, label in zip(
                axs, ("Displacement / mm", f"{h}-day increment / mm", "Residual / mm")
            ):
                ax.set_ylabel(label)
                ax.grid(axis="y", alpha=0.13)
                ax.tick_params(axis="y", labelsize=6.5)
                ax.ticklabel_format(axis="y", style="plain", useOffset=False)
            coverage = float(np.mean(abs(error) <= q))
            title = f"{point}  |  Day {h}  |  n={len(origins)}  |  90% coverage {100 * coverage:.1f}%"
            fig.suptitle(
                title, x=0.13, y=0.994, ha="left", fontsize=8, fontweight="bold"
            )
            handles, labels = axs[0].get_legend_handles_labels()
            fig.legend(
                handles,
                labels,
                loc="upper center",
                bbox_to_anchor=(0.55, 0.925),
                ncol=4,
                fontsize=6.5,
            )
            axs[2].xaxis.set_major_locator(mdates.MonthLocator(interval=2))
            axs[2].xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
            fig.subplots_adjust(
                left=0.14, right=0.985, top=0.79, bottom=0.085, hspace=0.15
            )
            path = out / f"forecast_{point.lower()}_h{h}"
            finish(fig, path)
            for i, n in enumerate(origins):
                plot_rows.append(
                    dict(
                        phase=phase,
                        point=point,
                        horizon=h,
                        origin=int(n),
                        target_index=int(ids[i]),
                        target_date=str(dates[ids[i]]),
                        model=chosen,
                        observed=truth[i, p],
                        last_observed=last[i, p],
                        mean=mean[i],
                        sigma=sigma[i],
                        bplus=predictions["B_ANCHOR"]["mean"][mask, h - 1, p][i],
                        drift=predictions["DRIFT1"]["mean"][mask, h - 1, p][i],
                        increment_observed=truth[i, p] - last[i, p],
                        increment_mean=mean[i] - last[i, p],
                        residual=error[i],
                        band_lower=-q[i],
                        band_upper=q[i],
                    )
                )
            sources.append(
                dict(
                    figure=path.name,
                    point=point,
                    horizon=h,
                    model=chosen,
                    n=len(origins),
                    coverage90=coverage,
                    time_first=str(dates[ids[0]]),
                    time_last=str(dates[ids[-1]]),
                    panels=[
                        "full_cumulative_displacement",
                        "h_day_increment",
                        "residual_and_90_percent_band",
                    ],
                )
            )
    pd.DataFrame(plot_rows).to_csv(
        out / "curve_source_data.csv", index=False, float_format="%.15g"
    )
    save_json(out / "curve_manifest.json", sources)


def paired_effects(root, out):
    frame = pd.read_csv(root / "analysis/paired_differences.csv")
    frame = frame[frame.metric == "rmse"]
    fig, axs = plt.subplots(2, 2, figsize=(166 / 25.4, 95 / 25.4), sharex=True)
    titles = [
        ("convlstm_residual", "ConvLSTM: residual - direct"),
        ("ridge_residual", "Ridge: residual - direct"),
        ("equation_constraint", "PINN: equation - no equation"),
        ("physical_error", "Feedback: physical - core"),
    ]
    for i, (kind, title) in enumerate(titles):
        ax = axs.flat[i]
        for phase, color, label, marker in [
            ("development", "#365f91", "Development", "o"),
            ("later_exploratory", "#c97934", "Later evaluation", "s"),
        ]:
            part = frame[(frame.contrast == kind) & (frame.phase == phase)].sort_values(
                "horizon"
            )
            ax.plot(
                part.horizon,
                part.difference,
                color=color,
                label=label,
                marker=marker,
                markersize=3,
            )
        ax.axhline(0, color="#777777", lw=0.65, ls="--")
        ax.set_title(
            f"{chr(97 + i)}  {title}", loc="left", fontsize=7.5, fontweight="bold"
        )
        ax.set_ylabel("RMSE difference / mm")
        ax.set_xticks(range(1, 8))
        ax.grid(axis="y", alpha=0.15)
        if i >= 2:
            ax.set_xlabel("Forecast horizon / days")
    handles, labels = axs[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, 1.0), ncol=2)
    fig.subplots_adjust(
        left=0.12, right=0.98, bottom=0.13, top=0.83, wspace=0.40, hspace=0.52
    )
    finish(fig, out / "paired_effects")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    spec = load_spec(args.config)
    check_deadline(spec)
    root = ROOT / spec["output_root"]
    out = ROOT / spec["figures_root"]
    out.mkdir(parents=True, exist_ok=True)
    if not json.loads((root / "verification/receipt.json").read_text())["passed"]:
        raise ValueError("Verification incomplete")
    y, _, dates = observations(spec)
    selection = json.loads((root / "selection.json").read_text())["by_horizon"]
    overview(out)
    horizon_comparison(root, out)
    point_curves(spec, root, out, y, dates, selection)
    paired_effects(root, out)
    save_json(
        out / "source_receipt.json",
        dict(
            config_sha256=sha(
                ROOT / "config/ootang_short_horizon_comparison.v4_0.json"
            ),
            selection_sha256=sha(root / "selection.json"),
            plot_source_sha256=sha(Path(__file__)),
            all_legal_later_dates=True,
            main_curve_horizon=7,
            interval="90% marginal Gaussian empirical prediction band",
            claim="Published daily-sequence endpoint comparison; no verified real warning performance",
            comparison_centers="Scores of fixed issued predictions; not averages across seeds; seed dispersion in analysis/seed_mean_metrics.csv",
        ),
    )
    print(json.dumps({"figures": len(list(out.glob("*.png"))), "directory": str(out)}))


if __name__ == "__main__":
    main()
