"""Build data-driven report figures from the current project artifacts.

The process overview is maintained as an editable Draw.io document at
``paper/figures/process_overview.drawio`` and is intentionally not generated
by this script.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib as mpl

mpl.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import font_manager
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.patches import Patch


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = Path(__file__).resolve().parent / "figures"
QA_OUTPUT_DIR = Path(__file__).resolve().parent / "build" / "figure_qa"
SUMMARY_PATH = (
    ROOT
    / "figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/"
    "seed_stability_0_4/seed_stability_summary.csv"
)
STATION_INDICATOR_PATH = (
    ROOT
    / "figures/warning_operational_draft_v4/"
    "ootang_operational_station_timeline.csv"
)

COLORS = {
    "gray": "#60676D",
    "ink": "#202428",
}


def _configure_style() -> None:
    font_candidates = ["Arial Unicode MS", "Songti SC", "DejaVu Sans"]
    selected = "DejaVu Sans"
    for candidate in font_candidates:
        try:
            font_manager.findfont(candidate, fallback_to_default=False)
        except ValueError:
            continue
        selected = candidate
        break

    mpl.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": [selected, "DejaVu Sans"],
            "axes.unicode_minus": False,
            "svg.fonttype": "none",
            "svg.hashsalt": "ootang-process-report-v1",
            "pdf.fonttype": 42,
            "font.size": 9,
            "axes.labelsize": 9,
            "axes.titlesize": 10,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "legend.fontsize": 8,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.linewidth": 0.8,
            "figure.facecolor": "white",
            "savefig.facecolor": "white",
        }
    )


def _save_figure(fig: plt.Figure, stem: str) -> None:
    fig.savefig(
        OUTPUT_DIR / f"{stem}.png",
        dpi=300,
        bbox_inches="tight",
        metadata={"Software": "Landslide-Warning"},
    )
    svg_path = OUTPUT_DIR / f"{stem}.svg"
    fig.savefig(
        svg_path,
        bbox_inches="tight",
        metadata={"Date": None, "Creator": "Landslide-Warning"},
    )
    svg_text = svg_path.read_text(encoding="utf-8")
    svg_path.write_text(
        "\n".join(line.rstrip() for line in svg_text.splitlines()) + "\n",
        encoding="utf-8",
    )
    fig.savefig(QA_OUTPUT_DIR / f"{stem}.pdf", bbox_inches="tight")


def _panel_label(ax: plt.Axes, label: str) -> None:
    ax.text(
        -0.13,
        1.08,
        label,
        transform=ax.transAxes,
        fontsize=12,
        fontweight="bold",
        va="top",
    )


def build_validation_summary() -> None:
    summary = pd.read_csv(SUMMARY_PATH)
    overall = summary.loc[
        (summary["scope"] == "overall")
        & (summary["interval_variant"] == "calibrated")
    ].sort_values("fold")
    if overall["fold"].tolist() != [1, 2, 3]:
        raise ValueError("Expected exactly three ordered overall calibrated folds")

    folds = overall["fold"].to_numpy(dtype=int)
    x = np.arange(len(folds))
    fold_colors = ["#416B8F", "#779AB6", "#B65B53"]

    fig, axes = plt.subplots(2, 2, figsize=(7.2, 4.7), constrained_layout=True)
    axes = axes.ravel()

    rmse_ratio = overall["model_rmse_mean"].to_numpy() / overall["baseline_rmse"].to_numpy()
    rmse_ratio_err = overall["model_rmse_std"].to_numpy() / overall["baseline_rmse"].to_numpy()
    axes[0].bar(x, rmse_ratio, yerr=rmse_ratio_err, color=fold_colors, capsize=4, width=0.62)
    axes[0].axhline(1.0, color=COLORS["gray"], linestyle="--", linewidth=1.1)
    axes[0].set_ylabel("模型 RMSE / 持久性 RMSE")
    axes[0].set_title("点误差相对简单基线")
    axes[0].set_xticks(x, [f"折 {fold}" for fold in folds])
    axes[0].set_xlim(-0.45, 2.85)
    axes[0].set_ylim(0, max(rmse_ratio + rmse_ratio_err) * 1.16)
    for xi, value in zip(x, rmse_ratio, strict=True):
        axes[0].text(xi, value + 0.18, f"{value:.2f}×", ha="center", va="bottom", fontsize=8)
    axes[0].text(2.42, 1.03, "基线 = 1", ha="left", va="bottom", fontsize=7.5, color=COLORS["gray"])
    _panel_label(axes[0], "a")

    coverage = overall["coverage_mean"].to_numpy()
    coverage_err = overall["coverage_std"].to_numpy()
    axes[1].bar(x, coverage, yerr=coverage_err, color=fold_colors, capsize=4, width=0.62)
    axes[1].axhline(0.8, color=COLORS["gray"], linestyle="--", linewidth=1.1)
    axes[1].set_ylabel("P10-P90 覆盖率")
    axes[1].set_title("校准区间覆盖随时期变化")
    axes[1].set_xticks(x, [f"折 {fold}" for fold in folds])
    axes[1].set_xlim(-0.45, 2.85)
    axes[1].set_ylim(0, 1.08)
    for xi, value in zip(x, coverage, strict=True):
        axes[1].text(xi, min(value + 0.035, 1.02), f"{value:.3f}", ha="center", va="bottom", fontsize=8)
    axes[1].text(2.42, 0.81, "目标 = 0.80", ha="left", va="bottom", fontsize=7.5, color=COLORS["gray"])
    _panel_label(axes[1], "b")

    correlation = overall["increment_correlation_mean"].to_numpy()
    correlation_err = overall["increment_correlation_std"].to_numpy()
    axes[2].bar(x, correlation, yerr=correlation_err, color=fold_colors, capsize=4, width=0.62)
    axes[2].axhline(0.0, color=COLORS["gray"], linewidth=1.0)
    axes[2].set_ylabel("预测与实际日增量相关")
    axes[2].set_title("逐日动态跟踪能力有限")
    axes[2].set_xticks(x, [f"折 {fold}" for fold in folds])
    axes[2].set_ylim(-0.3, 0.42)
    for xi, value in zip(x, correlation, strict=True):
        y_text = value + 0.04 if value >= 0 else value - 0.06
        axes[2].text(xi, y_text, f"{value:.3f}", ha="center", va="center", fontsize=8)
    _panel_label(axes[2], "c")

    std_ratio = overall["increment_std_ratio_mean"].to_numpy()
    std_ratio_err = overall["increment_std_ratio_std"].to_numpy()
    if np.any(std_ratio <= 0) or np.any(std_ratio - std_ratio_err <= 0):
        raise ValueError("Log-scale increment variability values and error extents must be positive")
    axes[3].bar(x, std_ratio, yerr=std_ratio_err, color=fold_colors, capsize=4, width=0.62)
    axes[3].axhline(1.0, color=COLORS["gray"], linestyle="--", linewidth=1.1)
    axes[3].set_yscale("log")
    axes[3].set_ylabel("预测 / 实际日增量标准差")
    axes[3].set_title("前两折放大，第三折强平滑")
    axes[3].set_xticks(x, [f"折 {fold}" for fold in folds])
    axes[3].set_xlim(-0.45, 2.85)
    axes[3].set_ylim(0.09, 12.5)
    for xi, value in zip(x, std_ratio, strict=True):
        axes[3].text(xi, value * 1.28, f"{value:.3f}", ha="center", va="bottom", fontsize=8)
    axes[3].text(2.42, 1.07, "幅度比 = 1", ha="left", va="bottom", fontsize=7.5, color=COLORS["gray"])
    _panel_label(axes[3], "d")

    for ax in axes:
        ax.grid(axis="y", color="#D9DEE2", linewidth=0.7, alpha=0.8)
        ax.set_axisbelow(True)

    _save_figure(fig, "convlstm_validation_summary")
    plt.close(fig)


def build_station_indicator_overview() -> None:
    data = pd.read_csv(STATION_INDICATOR_PATH, parse_dates=["date"])
    station_order = ["MJ9", "MJ1", "MJ3", "ATU1", "ATU2", "ATU3", "ATU4", "ATU5"]
    panels = [
        ("区间位置", "interval_level"),
        ("速度", "velocity_level"),
        ("严格加速度", "acceleration_level"),
        ("改进切线角", "tangent_angle_level"),
        ("测点融合", "candidate_level"),
    ]

    if data.duplicated(["date", "station"]).any():
        raise ValueError("Station indicator rows must be unique by date and station")
    if set(data["station"].unique()) != set(station_order):
        raise ValueError("Expected the eight fixed Ootang monitoring stations")

    dates = pd.DatetimeIndex(sorted(data["date"].unique()))
    warning_colors = ["#70A66C", "#4C78A8", "#F2CF5B", "#E6923A", "#C44E52"]
    cmap = ListedColormap(warning_colors)
    cmap.set_bad("#E5E7E9")
    norm = BoundaryNorm(np.arange(-0.5, 5.5, 1), cmap.N)

    fig, axes = plt.subplots(
        len(panels),
        1,
        figsize=(8.25, 6.4),
        sharex=True,
    )
    fig.subplots_adjust(left=0.16, right=0.985, bottom=0.09, top=0.86, hspace=0.08)
    fig.suptitle(
        "全部 8 个测点的四项指标与测点融合",
        fontsize=13,
        fontweight="bold",
        color=COLORS["ink"],
    )

    for ax, (title, column) in zip(axes, panels, strict=True):
        matrix = (
            data.pivot(index="station", columns="date", values=column)
            .reindex(index=station_order, columns=dates)
            .to_numpy(dtype=float)
        )
        invalid = matrix[np.isfinite(matrix)]
        if invalid.size and (invalid.min() < 0 or invalid.max() > 4):
            raise ValueError(f"Unexpected five-level values in {column}")

        ax.imshow(
            np.ma.masked_invalid(matrix),
            aspect="auto",
            interpolation="nearest",
            cmap=cmap,
            norm=norm,
        )
        ax.set_yticks(np.arange(len(station_order)), station_order)
        ax.set_ylabel(title, rotation=0, ha="right", va="center", labelpad=24, fontweight="bold")
        ax.tick_params(axis="y", length=0)
        for spine in ax.spines.values():
            spine.set_visible(False)

    tick_positions = np.linspace(0, len(dates) - 1, 6, dtype=int)
    axes[-1].set_xticks(tick_positions, [dates[i].strftime("%Y-%m") for i in tick_positions])
    axes[-1].set_xlabel("日期")
    fig.legend(
        handles=[
            Patch(facecolor=color, edgecolor="none", label=label)
            for color, label in zip(
                warning_colors,
                ["green", "blue", "yellow", "orange", "red"],
                strict=True,
            )
        ],
        loc="upper center",
        bbox_to_anchor=(0.5, 0.925),
        ncol=5,
        frameon=False,
    )

    _save_figure(fig, "station_indicator_overview")
    plt.close(fig)


def main() -> None:
    _configure_style()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    QA_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    build_validation_summary()
    build_station_indicator_overview()


if __name__ == "__main__":
    main()
