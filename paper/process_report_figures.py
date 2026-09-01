"""Build data-driven report figures from the current project artifacts.

The process overview is maintained as an editable Draw.io document at
``paper/figures/process_overview.drawio`` and is intentionally not generated
by this script.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib as mpl

mpl.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import font_manager
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

try:
    from audit_panel_alignment import require_matplotlib_panel_alignment
except ModuleNotFoundError:
    require_matplotlib_panel_alignment = None


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
FORECAST_PREDICTIONS_PATH = ROOT / "figures/convlstm/forecast_predictions.csv"

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
    if require_matplotlib_panel_alignment is not None:
        require_matplotlib_panel_alignment(
            fig,
            json_out=QA_OUTPUT_DIR / f"{stem}.alignment-audit.json",
            overlay_svg=QA_OUTPUT_DIR / f"{stem}.alignment-overlay.svg",
            tolerance_pt=1.5,
            gutter_tolerance_pt=1.5,
            strict=True,
        )
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
    ax.annotate(
        label,
        xy=(0, 1),
        xycoords="axes fraction",
        xytext=(-38, 8),
        textcoords="offset points",
        fontsize=12,
        fontweight="bold",
        va="top",
        ha="left",
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


def build_forecast_station_figures() -> None:
    data = pd.read_csv(FORECAST_PREDICTIONS_PATH, parse_dates=["date"])
    station_order = ["MJ9", "MJ1", "MJ3", "ATU4", "ATU5", "ATU3", "ATU2", "ATU1"]
    required_columns = {
        "date",
        "station",
        "split",
        "actual",
        "persistence",
        "p10",
        "p50",
        "p90",
        "calibrated_p10",
        "calibrated_p90",
    }
    missing = required_columns - set(data.columns)
    if missing:
        raise ValueError(f"Missing forecast columns: {sorted(missing)}")
    if data.duplicated(["date", "station"]).any():
        raise ValueError("Forecast rows must be unique by date and station")
    if set(data["station"].unique()) != set(station_order):
        raise ValueError("Expected the eight fixed Ootang monitoring stations")
    if not data.groupby("date")["station"].nunique().eq(len(station_order)).all():
        raise ValueError("Every forecast date must include all eight monitoring stations")

    split_by_date = data.groupby("date", sort=True)["split"].agg(lambda values: set(values))
    if any(len(values) != 1 for values in split_by_date):
        raise ValueError("Every date must belong to exactly one forecast split")
    split_sequence = [next(iter(values)) for values in split_by_date]
    split_transitions = [
        split
        for index, split in enumerate(split_sequence)
        if index == 0 or split != split_sequence[index - 1]
    ]
    if split_transitions != ["fit", "calibration", "test"]:
        raise ValueError("Expected ordered fit, calibration and test periods")

    interval_columns = {
        "fit": ("p10", "p90"),
        "calibration": ("calibrated_p10", "calibrated_p90"),
        "test": ("calibrated_p10", "calibrated_p90"),
    }
    for split, (lower_column, upper_column) in interval_columns.items():
        rows = data.loc[data["split"] == split]
        values = rows[[lower_column, "p50", upper_column]].to_numpy(dtype=float)
        if not np.isfinite(values).all():
            raise ValueError(f"Non-finite {split} prediction interval")
        if np.any(values[:, 0] > values[:, 1]) or np.any(values[:, 1] > values[:, 2]):
            raise ValueError(f"Invalid ordered {split} prediction interval")

    calibration_start = data.loc[data["split"] == "calibration", "date"].min()
    test_start = data.loc[data["split"] == "test", "date"].min()
    prediction_color = "#D6544D"
    interval_color = "#E9A6A2"
    split_backgrounds = {
        "fit": ("#F2F4F5", "拟合"),
        "calibration": ("#EAF1F6", "校准"),
        "test": ("#FAEEE9", "测试"),
    }
    legend_handles = [
        Line2D(
            [],
            [],
            color="#111111",
            linewidth=1.0,
            marker="o",
            markerfacecolor="white",
            markersize=3.4,
            label="实测",
        ),
        Line2D(
            [],
            [],
            color=prediction_color,
            linewidth=1.5,
            linestyle=(0, (4, 2.5)),
            label="P50",
        ),
        Patch(facecolor=interval_color, edgecolor="none", alpha=0.32, label="P10-P90 区间"),
    ]

    for station in station_order:
        fig, (ax, ax_increment, ax_residual) = plt.subplots(
            3,
            1,
            figsize=(7.2, 8.2),
            gridspec_kw={"height_ratios": [0.85, 1.35, 0.80]},
        )
        fig.subplots_adjust(left=0.13, right=0.985, bottom=0.075, top=0.89, hspace=0.48)
        fig.suptitle(
            f"{station} 测点位移预测",
            y=0.985,
            fontsize=13,
            fontweight="bold",
            color=COLORS["ink"],
        )

        station_data = data.loc[data["station"] == station].sort_values("date")
        marker_step_full = max(1, len(station_data) // 42)

        for split in ("fit", "calibration", "test"):
            split_data = station_data.loc[station_data["split"] == split]
            background_color, split_label = split_backgrounds[split]
            split_start = split_data["date"].min()
            split_end = split_data["date"].max()
            ax.axvspan(split_start, split_end, color=background_color, zorder=0)
            ax.annotate(
                split_label,
                xy=(split_start + (split_end - split_start) / 2, 1),
                xycoords=("data", "axes fraction"),
                xytext=(0, 2),
                textcoords="offset points",
                color="#60676D",
                fontsize=7.5,
                ha="center",
                va="bottom",
            )

        ax.plot(
            station_data["date"],
            station_data["actual"],
            color="#111111",
            linewidth=0.9,
            marker="o",
            markevery=marker_step_full,
            markersize=2.8,
            markerfacecolor="white",
            markeredgewidth=0.7,
            zorder=5,
        )
        ax.plot(
            station_data["date"],
            station_data["p50"],
            color=prediction_color,
            linewidth=1.45,
            linestyle=(0, (4, 2.5)),
            zorder=4,
        )

        for boundary in (calibration_start, test_start):
            ax.axvline(
                boundary,
                color="#747B80",
                linewidth=1.0,
                linestyle=":",
                zorder=2,
            )
        ax.set_title("全时段累计位移（背景）", loc="left", pad=5)
        ax.set_ylabel("位移 U（mm）")
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax.grid(axis="y", color="#D9DEE2", linewidth=0.65, alpha=0.75)
        ax.set_axisbelow(True)
        ax.margins(x=0)

        test_data = station_data.loc[station_data["split"] == "test"]
        actual_increment = test_data["actual"] - test_data["persistence"]
        predicted_increment = test_data["p50"] - test_data["persistence"]
        lower_increment = test_data["calibrated_p10"] - test_data["persistence"]
        upper_increment = test_data["calibrated_p90"] - test_data["persistence"]
        marker_step_test = max(1, len(test_data) // 24)
        ax_increment.fill_between(
            test_data["date"],
            lower_increment,
            upper_increment,
            color=interval_color,
            alpha=0.32,
            linewidth=0,
            zorder=1,
        )
        ax_increment.plot(
            test_data["date"],
            actual_increment,
            color="#111111",
            linewidth=1.15,
            marker="o",
            markevery=marker_step_test,
            markersize=3.0,
            markerfacecolor="white",
            markeredgewidth=0.7,
            zorder=4,
        )
        ax_increment.plot(
            test_data["date"],
            predicted_increment,
            color=prediction_color,
            linewidth=1.45,
            linestyle=(0, (4, 2.5)),
            zorder=5,
        )
        ax_increment.axhline(0, color="#747B80", linewidth=0.8, zorder=2)
        ax_increment.set_title("测试段日增量及 80% 预测区间", loc="left", pad=5)
        ax_increment.set_ylabel("日增量 ΔU（mm/d）")
        ax_increment.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
        ax_increment.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax_increment.grid(axis="y", color="#D9DEE2", linewidth=0.65, alpha=0.75)
        ax_increment.set_axisbelow(True)
        ax_increment.margins(x=0)

        residual = test_data["actual"] - test_data["p50"]
        lower_residual_bound = test_data["calibrated_p10"] - test_data["p50"]
        upper_residual_bound = test_data["calibrated_p90"] - test_data["p50"]
        covered = (
            (test_data["actual"] >= test_data["calibrated_p10"])
            & (test_data["actual"] <= test_data["calibrated_p90"])
        )
        picp = float(covered.mean())

        ax_residual.fill_between(
            test_data["date"],
            lower_residual_bound,
            upper_residual_bound,
            color=interval_color,
            alpha=0.32,
            linewidth=0,
            zorder=1,
        )
        ax_residual.plot(
            test_data["date"],
            residual,
            color="#111111",
            linewidth=1.05,
            marker="o",
            markevery=marker_step_test,
            markersize=2.8,
            markerfacecolor="white",
            markeredgewidth=0.7,
            zorder=4,
        )
        ax_residual.axhline(0, color="#747B80", linewidth=0.8, zorder=2)
        residual_extent = float(
            np.nanmax(
                np.abs(
                    np.concatenate(
                        [
                            residual.to_numpy(dtype=float),
                            lower_residual_bound.to_numpy(dtype=float),
                            upper_residual_bound.to_numpy(dtype=float),
                        ]
                    )
                )
            )
        )
        if not np.isfinite(residual_extent) or residual_extent <= 0:
            raise ValueError(f"Invalid residual extent for {station}")
        ax_residual.set_ylim(-1.08 * residual_extent, 1.08 * residual_extent)
        ax_residual.set_title(
            f"测试段残差与区间覆盖（PICP={picp:.3f}）",
            loc="left",
            pad=5,
        )
        ax_residual.set_ylabel("实测-P50（mm）")
        ax_residual.set_xlabel("测试段日期")
        ax_residual.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
        ax_residual.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax_residual.grid(axis="y", color="#D9DEE2", linewidth=0.65, alpha=0.75)
        ax_residual.set_axisbelow(True)
        ax_residual.margins(x=0)

        for panel, label in zip((ax, ax_increment, ax_residual), "abc", strict=True):
            _panel_label(panel, label)

        fig.legend(
            handles=legend_handles,
            loc="upper center",
            bbox_to_anchor=(0.5, 0.95),
            ncol=3,
            frameon=False,
            fontsize=8.7,
            handlelength=2.6,
            columnspacing=1.8,
            handletextpad=0.6,
        )

        _save_figure(fig, f"forecast_{station.lower()}")
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
    build_forecast_station_figures()
    build_validation_summary()
    build_station_indicator_overview()


if __name__ == "__main__":
    main()
