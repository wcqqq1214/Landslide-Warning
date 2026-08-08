"""Build the two report-specific figures from versioned project artifacts."""

from __future__ import annotations

from pathlib import Path

import matplotlib as mpl

mpl.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import font_manager
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = Path(__file__).resolve().parent / "figures"
QA_OUTPUT_DIR = Path(__file__).resolve().parent / "build" / "figure_qa"
SUMMARY_PATH = (
    ROOT
    / "figures/convlstm/runs/displacement_elevation_exog_v1/fixed120_v1/"
    "seed_stability_0_4/seed_stability_summary.csv"
)

COLORS = {
    "blue": "#416B8F",
    "blue_light": "#DCE8F2",
    "green": "#5F8C62",
    "green_light": "#E2EEE3",
    "orange": "#D7832F",
    "orange_light": "#F7E7D5",
    "red": "#B64D4D",
    "red_light": "#F4DEDE",
    "gray": "#60676D",
    "gray_light": "#EEF1F3",
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
    fig.savefig(OUTPUT_DIR / f"{stem}.png", dpi=300, bbox_inches="tight")
    svg_path = OUTPUT_DIR / f"{stem}.svg"
    fig.savefig(svg_path, bbox_inches="tight")
    svg_text = svg_path.read_text(encoding="utf-8")
    svg_path.write_text(
        "\n".join(line.rstrip() for line in svg_text.splitlines()) + "\n",
        encoding="utf-8",
    )
    fig.savefig(QA_OUTPUT_DIR / f"{stem}.pdf", bbox_inches="tight")


def _add_card(
    ax: plt.Axes,
    x: float,
    y: float,
    width: float,
    height: float,
    title: str,
    lines: list[str],
    facecolor: str,
    edgecolor: str,
) -> None:
    card = FancyBboxPatch(
        (x, y),
        width,
        height,
        boxstyle="round,pad=0.012,rounding_size=0.014",
        linewidth=1.4,
        edgecolor=edgecolor,
        facecolor=facecolor,
        transform=ax.transAxes,
    )
    ax.add_patch(card)
    ax.text(
        x + width / 2,
        y + height * 0.73,
        title,
        ha="center",
        va="center",
        fontsize=9.2,
        fontweight="bold",
        color=COLORS["ink"],
        transform=ax.transAxes,
    )
    ax.text(
        x + width / 2,
        y + height * 0.36,
        "\n".join(lines),
        ha="center",
        va="center",
        fontsize=7.2,
        linespacing=1.35,
        color=COLORS["ink"],
        transform=ax.transAxes,
    )


def build_process_overview() -> None:
    fig, ax = plt.subplots(figsize=(7.2, 4.15))
    ax.set_axis_off()
    ax.text(
        0.03,
        0.93,
        "藕塘案例当前技术链与证据边界",
        fontsize=13,
        fontweight="bold",
        color=COLORS["ink"],
        transform=ax.transAxes,
    )
    ax.text(
        0.03,
        0.875,
        "从 8 个测点的物化日序列出发，先完成概率预测，再进行独立解释和观测后状态审计。",
        fontsize=7.2,
        color=COLORS["gray"],
        transform=ax.transAxes,
    )

    cards = [
        (
            "数据输入",
            ["1461 日 × 8 测点", "坐标 + 静态高程"],
            COLORS["gray_light"],
            COLORS["gray"],
        ),
        (
            "特征与运动学",
            ["相邻时刻速度", "ΔV / 水位 / 雨量"],
            COLORS["blue_light"],
            COLORS["blue"],
        ),
        (
            "ConvLSTM",
            ["7 通道 × 7 日", "次日 P10 / P50 / P90"],
            COLORS["green_light"],
            COLORS["green"],
        ),
        (
            "独立 SHAP",
            ["解释 NGBoost 依赖", "回归 + 历史分类"],
            COLORS["orange_light"],
            COLORS["orange"],
        ),
        (
            "v3 空间融合",
            ["四指标 × 8 测点", "整体确认 / 局部最高"],
            COLORS["red_light"],
            COLORS["red"],
        ),
    ]

    x_positions = np.linspace(0.03, 0.81, len(cards))
    width = 0.16
    y = 0.51
    height = 0.25
    for index, (title, lines, facecolor, edgecolor) in enumerate(cards):
        x = float(x_positions[index])
        _add_card(ax, x, y, width, height, title, lines, facecolor, edgecolor)
        if index < len(cards) - 1:
            next_x = float(x_positions[index + 1])
            arrow = FancyArrowPatch(
                (x + width + 0.006, y + height / 2),
                (next_x - 0.006, y + height / 2),
                arrowstyle="-|>",
                mutation_scale=13,
                linewidth=1.2,
                color=COLORS["gray"],
                transform=ax.transAxes,
            )
            ax.add_patch(arrow)

    bands = [
        (
            0.03,
            0.30,
            0.94,
            0.10,
            "已跑通",
            "藕塘内部工程原型：全测点概率预测、SHAP 依赖分析、逐点状态和多测点空间审计",
            COLORS["green_light"],
            COLORS["green"],
        ),
        (
            0.03,
            0.16,
            0.94,
            0.10,
            "尚未完成",
            "正式稳定段 / V0、严格加速度等级、独立标签下的 NGBoost、正式 F / F_site 与前瞻预警评价",
            COLORS["orange_light"],
            COLORS["orange"],
        ),
        (
            0.03,
            0.02,
            0.94,
            0.10,
            "证据门禁",
            "缺少原始 GNSS 与日值生成链：允许原型初跑，但确认性证据和正式预警仍阻断；Vajont 未获启动授权",
            COLORS["gray_light"],
            COLORS["gray"],
        ),
    ]
    for x, y0, w, h, label, text, facecolor, edgecolor in bands:
        patch = FancyBboxPatch(
            (x, y0),
            w,
            h,
            boxstyle="round,pad=0.008,rounding_size=0.012",
            linewidth=1.2,
            edgecolor=edgecolor,
            facecolor=facecolor,
            transform=ax.transAxes,
        )
        ax.add_patch(patch)
        ax.text(
            x + 0.015,
            y0 + h / 2,
            label,
            va="center",
            ha="left",
            fontsize=10,
            fontweight="bold",
            color=edgecolor,
            transform=ax.transAxes,
        )
        ax.text(
            x + 0.12,
            y0 + h / 2,
            text,
            va="center",
            ha="left",
            fontsize=7.5,
            color=COLORS["ink"],
            transform=ax.transAxes,
        )

    _save_figure(fig, "process_overview")
    plt.close(fig)


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


def main() -> None:
    _configure_style()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    QA_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    build_process_overview()
    build_validation_summary()


if __name__ == "__main__":
    main()
