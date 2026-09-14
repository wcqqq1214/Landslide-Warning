"""Chinese report figures from saved plot data and scores, without model calls.

Adapt the existing six quantitative panels at their original 166 mm width.
All legal dates, selected methods, scores and 90% marginal prediction bands
are retained. The overview also includes the verified initial-state candidate.
No smoothing, fitting,
selection or scoring is performed. The existing experiment figures are inputs,
never output targets. PDF text remains editable; previews use 300 dpi.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "results/ootang_short_horizon_v4/20260913_short_horizon"
INITIAL_STATE = ROOT / "results/ootang_neural_initial_state_v1_1/20260914"
ORIGINAL = ROOT / "figures/ootang_short_horizon_v4/20260913_short_horizon"
OUT = ROOT / "paper/figures/short_horizon_zh"
SCRATCH = ROOT / "tmp/pdfs/short_horizon_zh_edit"
SKILL = Path("/Users/wcqqq1214/.codex/skills/nature-figure/scripts")
sys.path.insert(0, str(SKILL))
from audit_panel_alignment import require_matplotlib_panel_alignment

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial Unicode MS", "DejaVu Sans"],
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
})
COLORS = {"B+": "#777777", "ConvLSTM": "#c97934", "PINN": "#93719d",
          "NIS_BPLUS": "#23897e", "Ridge": "#365f91", "DRIFT1": "#6f8e72"}
LABELS = {"B+": "B+物理模型", "ConvLSTM": "ConvLSTM",
          "PINN": "软约束PINN（物理未达标）",
          "NIS_BPLUS": "神经初态估计与 B+ 严格递推（数值检查通过）", "Ridge": "在线回归＋反馈",
          "DRIFT1": "速度外推"}
PHASES = [("development", "开发段"), ("later_exploratory", "后期评价")]
OUTPUTS = {}
PLOTTED_LINES = 0
OVERVIEW_SERIES = []


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def line(ax, x, y, **kwargs):
    """Check the plotted ordinates against the supplied saved values."""
    global PLOTTED_LINES
    artist, = ax.plot(x, y, **kwargs)
    np.testing.assert_array_equal(artist.get_ydata(), np.asarray(y))
    PLOTTED_LINES += 1
    return artist


def finish(fig, name):
    fig.canvas.draw()
    alignment = SCRATCH / f"{name}.alignment.json"
    require_matplotlib_panel_alignment(
        fig, json_out=str(alignment), tolerance_pt=1.5,
        gutter_tolerance_pt=1.5, strict=True,
    )
    pdf = OUT / f"{name}.pdf"
    fig.savefig(pdf)
    fig.savefig(SCRATCH / f"{name}.svg")
    fig.savefig(SCRATCH / f"{name}.png", dpi=300)
    plt.close(fig)
    OUTPUTS[name] = {
        "pdf_sha256": sha(pdf),
        "panel_alignment": json.loads(alignment.read_text()),
    }


def horizon_comparison():
    frame = pd.read_csv(RUN / "analysis/family_representatives.csv")
    scores = pd.read_csv(RUN / "analysis/summary_by_horizon.csv")
    receipt = json.loads((INITIAL_STATE / "verification/receipt.json").read_text())
    assert receipt["status"] == "passed" and receipt["physics_pass"]
    assert receipt["counts"]["trajectories"] == 4707
    initial = {phase: pd.read_csv(INITIAL_STATE / phase / "summary_by_horizon.csv")
               for phase, _ in PHASES}
    fig, axs = plt.subplots(2, 2, figsize=(166 / 25.4, 118 / 25.4), sharex=True)
    for row, (phase, title) in enumerate(PHASES):
        for col, metric in enumerate(["rmse", "crps"]):
            ax = axs[row, col]
            for family in COLORS:
                part = (initial[phase][initial[phase].model == family]
                        if family == "NIS_BPLUS" else
                        frame[(frame.phase == phase) & (frame.family == family)]
                        if family != "DRIFT1" else
                        scores[(scores.phase == phase) & (scores.model == family)])
                part = part.sort_values("horizon")
                assert part.horizon.tolist() == list(range(1, 8))
                assert np.isfinite(part[metric]).all()
                assert (part[metric] > 0).all()
                line(ax, part.horizon, part[metric],
                     marker={"B+": "s", "ConvLSTM": "^", "PINN": "D",
                             "NIS_BPLUS": "v", "Ridge": "o", "DRIFT1": "x"}[family],
                     markersize=3, color=COLORS[family], label=LABELS[family],
                     ls={"DRIFT1": "--", "NIS_BPLUS": "-."}.get(family, "-"))
                OVERVIEW_SERIES.append({
                    "phase": phase, "family": family, "metric": metric,
                    "horizon": part.horizon.tolist(), "value": part[metric].tolist(),
                })
            ax.set_yscale("log")
            ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
            ax.set_xticks(range(1, 8))
            ax.grid(axis="y", alpha=0.18)
            ax.set_title(f"{chr(97 + row * 2 + col)}  {title}",
                         loc="left", fontweight="bold")
            ax.set_ylabel(f"四点平均{metric.upper()} / 毫米\n（对数坐标）")
            if row == 1:
                ax.set_xlabel("预测步长 / 天")
    handles, labels = axs[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.51, 1.0),
               ncol=3, columnspacing=1.6)
    fig.subplots_adjust(left=0.115, right=0.985, bottom=0.105, top=0.80,
                        wspace=0.35, hspace=0.40)
    finish(fig, "horizon_comparison")


def paired_effects():
    frame = pd.read_csv(RUN / "analysis/paired_differences.csv")
    frame = frame[frame.metric == "rmse"]
    fig, axs = plt.subplots(2, 2, figsize=(166 / 25.4, 95 / 25.4), sharex=True)
    titles = [("convlstm_residual", "ConvLSTM：残差 - 直接"),
              ("ridge_residual", "岭回归：残差 - 直接"),
              ("equation_constraint", "PINN：方程约束 - 无方程约束"),
              ("physical_error", "误差反馈：含物理参照 - 无物理参照")]
    for i, (kind, title) in enumerate(titles):
        ax = axs.flat[i]
        for (phase, label), color, marker in zip(
            PHASES, ["#365f91", "#c97934"], ["o", "s"]
        ):
            part = frame[(frame.contrast == kind) & (frame.phase == phase)]
            part = part.sort_values("horizon")
            assert part.horizon.tolist() == list(range(1, 8))
            line(ax, part.horizon, part.difference, color=color, label=label,
                 marker=marker, markersize=3)
        ax.axhline(0, color="#777777", lw=0.65, ls="--")
        ax.set_title(f"{chr(97 + i)}  {title}", loc="left",
                     fontsize=7.5, fontweight="bold")
        ax.set_ylabel("RMSE差值 / 毫米")
        ax.set_xticks(range(1, 8))
        ax.grid(axis="y", alpha=0.15)
        if i >= 2:
            ax.set_xlabel("预测步长 / 天")
    handles, labels = axs[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, 1.0), ncol=2)
    fig.subplots_adjust(left=0.12, right=0.98, bottom=0.13, top=0.83,
                        wspace=0.40, hspace=0.52)
    finish(fig, "paired_effects")


def point_curves():
    curves = pd.read_csv(ORIGINAL / "curve_source_data.csv")
    metrics = pd.read_csv(RUN / "analysis/metrics_by_point_horizon.csv")
    metrics = metrics[(metrics.phase == "later_exploratory")
                      & (metrics.model == "C16_CORE_RULES")
                      & (metrics.horizon == 7)].set_index("point")
    rows = 0
    for point in ["ATU1", "ATU5", "MJ3", "MJ1"]:
        part = curves[(curves.point == point) & (curves.horizon == 7)]
        part = part.sort_values("origin")
        assert len(part) == 287 and part.origin.is_unique
        assert part.phase.eq("later_exploratory").all()
        assert part.model.eq("C16_CORE_RULES").all()
        assert part.origin.tolist() == list(range(1168, 1455))
        time = pd.to_datetime(part.target_date)
        fig, axs = plt.subplots(3, 1, figsize=(166 / 25.4, 90 / 25.4), sharex=True)
        line(axs[0], time, part.observed, color="#222222", label="实测位移", lw=1.45)
        for name, color, label, ls in [
            ("mean", "#365f91", "预测均值", "--"),
            ("bplus", "#999999", "B+物理模型", "-."),
            ("drift", "#6f8e72", "速度外推", ":"),
        ]:
            line(axs[0], time, part[name], color=color, label=label, ls=ls, lw=1.0)
            increment = (part.increment_mean if name == "mean"
                         else part[name] - part.last_observed)
            line(axs[1], time, increment, color=color, ls=ls, lw=1.0)
        line(axs[1], time, part.increment_observed, color="#222222", lw=1.0)
        axs[2].fill_between(time, part.band_lower, part.band_upper,
                            color="#b6cce3", alpha=0.75, label="90%预测区间")
        line(axs[2], time, part.residual, color="#365f91", lw=0.85)
        axs[2].axhline(0, color="#888888", lw=0.5)
        for ax, label in zip(axs, ["累计位移\n/ 毫米", "7日位移增量\n/ 毫米", "残差 / 毫米"]):
            ax.set_ylabel(label)
            ax.grid(axis="y", alpha=0.13)
            ax.tick_params(axis="y", labelsize=6.5)
            ax.ticklabel_format(axis="y", style="plain", useOffset=False)
        coverage = float(metrics.loc[point, "coverage90"])
        fig.suptitle(f"{point}  |  提前7天  |  287个起点  |  90%区间覆盖率 {100 * coverage:.1f}%",
                     x=0.13, y=0.994, ha="left", fontsize=8, fontweight="bold")
        handles, labels = axs[0].get_legend_handles_labels()
        fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.55, 0.925),
                   ncol=4, fontsize=6.5)
        axs[2].xaxis.set_major_locator(mdates.MonthLocator(interval=2))
        axs[2].xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        axs[2].set_xlabel("日期")
        fig.subplots_adjust(left=0.14, right=0.985, top=0.79, bottom=0.13, hspace=0.15)
        finish(fig, f"forecast_{point.lower()}_h7")
        rows += len(part)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--horizon-only", action="store_true",
                        help="Update the overview while preserving the other five PDFs.")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    inputs = [RUN / f"analysis/{name}.csv" for name in [
        "family_representatives", "summary_by_horizon", "paired_differences",
        "metrics_by_point_horizon",
    ]] + [ORIGINAL / "curve_source_data.csv"]
    inputs += [INITIAL_STATE / phase / "summary_by_horizon.csv" for phase, _ in PHASES]
    inputs += [INITIAL_STATE / "verification/receipt.json"]
    if args.horizon_only:
        previous = json.loads((OUT / "sources.json").read_text())
        for name, digest in previous["input_sha256"].items():
            assert sha(ROOT / name) == digest, name
        for name, metadata in previous["figures"].items():
            if name != "horizon_comparison":
                assert sha(OUT / f"{name}.pdf") == metadata["pdf_sha256"], name
                OUTPUTS[name] = metadata
    horizon_comparison()
    if args.horizon_only:
        rows = previous["complete_h7_curve_rows"]
        assert PLOTTED_LINES == 24
    else:
        paired_effects()
        rows = point_curves()
        assert PLOTTED_LINES == 68
    assert rows == 1148 and len(OUTPUTS) == 6 and len(OVERVIEW_SERIES) == 24
    (OUT / "sources.json").write_text(json.dumps({
        "role": "Chinese presentation adaptation of saved quantitative figures",
        "input_sha256": {str(p.relative_to(ROOT)): sha(p) for p in inputs},
        "builder_sha256": sha(Path(__file__)),
        "plotted_lines_checked_against_saved_ordinates": 68,
        "lines_checked_this_run": PLOTTED_LINES,
        "reused_figures": sorted(set(OUTPUTS) - {"horizon_comparison"}) if args.horizon_only else [],
        "overview_series": OVERVIEW_SERIES,
        "overview_contract": {
            "claim": "Numerical physical consistency passes for the initial-state coupling; online regression remains more accurate.",
            "mapping": "phase × horizon (1–7 days) × model/family → saved four-point mean RMSE or CRPS in mm",
            "selection": "original family representatives unchanged; NIS_BPLUS is the single fixed initial-state candidate",
            "uncertainty": "saved aggregate point scores, not seed means with inferential error bars; no uncertainty recalculation",
            "reuse": "structural adaptation; original 140 ordinates plus 28 saved initial-state ordinates; no exclusions",
        },
        "complete_h7_curve_rows": rows,
        "band_source": "saved band_lower and band_upper; 90% marginal prediction interval",
        "increment_transform": "saved increments for observed/mean; bplus/drift minus last_observed",
        "new_training": 0, "new_selection": 0, "new_prediction_or_scoring": 0,
        "figures": OUTPUTS,
    }, ensure_ascii=False, indent=2) + "\n")
    print(f"Saved {len(OUTPUTS)} Chinese figures; {rows} complete curve rows; {PLOTTED_LINES} lines checked.")


if __name__ == "__main__":
    main()
