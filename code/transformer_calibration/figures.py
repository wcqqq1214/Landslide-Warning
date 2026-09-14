"""Mentor-style fixed-mean comparisons; every frozen interval value is retained."""

import importlib.util
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import numpy as np
import pandas as pd

from transformer_regularization.figures import canvas, save
from .core import (
    B,
    OLD,
    REG,
    HALF,
    ROOT,
    load_npz,
    read_json,
    read_labels,
    sha,
    spec,
    utc,
    write_json,
)

NAMES = {
    B: "改进 B+",
    "DRIFT1": "DRIFT1",
    "RR_COND": "普通岭回归",
    OLD: "Transformer 原版",
    REG: "Transformer REG1",
    HALF: "Transformer 半残差",
}
COLORS = {
    B: "#777777",
    "DRIFT1": "#AD814A",
    "RR_COND": "#8A7FAD",
    OLD: "#C55255",
    REG: "#246592",
    HALF: "#3A939B",
}
DOMAINS = ("O3", "O2", "O1-up", "O1-down")
RULE_LABELS = {"LAST90": "末尾 90 条成熟误差", "DIST90": "按预测距离匹配 90 条成熟误差"}


def main():
    cfg = spec()
    root = ROOT / cfg["out"]
    assert read_json(root / "verification_v1/receipt.json")["status"] == "passed"
    out = ROOT / cfg["figures"] / "v1"
    out.mkdir(parents=True, exist_ok=False)
    helper = (
        Path.home() / ".codex/skills/nature-figure/scripts/audit_panel_alignment.py"
    )
    loader = importlib.util.spec_from_file_location("calibration_alignment", helper)
    module = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(module)
    align = module.require_matplotlib_panel_alignment
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial Unicode MS", "DejaVu Sans"],
            "font.size": 8,
            "axes.labelsize": 8.5,
            "axes.titlesize": 9.5,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.linewidth": 0.7,
            "svg.fonttype": "none",
            "axes.unicode_minus": False,
            "legend.frameon": False,
            "path.simplify": False,
        }
    )
    issue = load_npz(root / "final_exploratory/issued_distribution.npz")
    full = load_npz(root / "final_exploratory/full_means.npz")
    truth = read_labels(ROOT / cfg["data"], 1461)
    y0 = truth[0]
    dates = pd.to_datetime(full["dates"])
    split = dates[1168] - pd.Timedelta(hours=12)
    points = pd.read_csv(root / "final_exploratory/metrics_by_point.csv").set_index(
        ["model", "rule", "point"]
    )
    # The issue dictionary's dates are forecast-only; retain the full display calendar too.
    source = {**full, **issue, "full_dates": full["dates"], "observed": truth, "y0": y0}
    np.savez_compressed(out / "source_arrays.npz", **source)
    limits = []
    for p in range(4):
        values = [truth[:, p] - y0[p], full[B][:, p] - y0[p]]
        for m, r in cfg["figure_contract"]["panels"]:
            mu = full[m][:, p] - y0[p]
            sd = issue[m + "__" + r + "__sigma"][:, p]
            values.extend(
                [
                    mu,
                    mu[1168:] - 1.959963984540054 * sd,
                    mu[1168:] + 1.959963984540054 * sd,
                ]
            )
        lo = min(v.min() for v in values)
        hi = max(v.max() for v in values)
        limits.append((lo - 0.05 * (hi - lo), hi + 0.06 * (hi - lo)))
    figure_rows = []
    exports = []

    def axis_style(ax, p, first, last):
        ax.set_title(
            f"{cfg['points'][p]} | {DOMAINS[p]}", loc="left", fontweight="bold", pad=7
        )
        ax.set_ylabel("累计位移 / mm")
        ax.set_xlim(first - pd.Timedelta(days=20), last + pd.Timedelta(days=20))
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.set_axisbelow("line")
        ax.grid(
            True,
            which="major",
            axis="both",
            color="#D9DDE1",
            linewidth=0.55,
            linestyle="-",
            alpha=0.85,
        )

    for method, rule in cfg["figure_contract"]["panels"]:
        short = "REG1" if method == REG else "HALF"
        for display in ("mentor", "full"):
            name = f"{short}_{rule}_{display}"
            fig, axes = canvas(
                f"{NAMES[method]}：四点拟合与 8:2 条件预测",
                "前 1168 日拟合｜后 293 日一次发出｜给定逐日降雨与库水位，无实测位移反馈",
            )
            color = "#246592"
            handles = [
                Line2D([], [], color="#161616", lw=1.15, label="实测"),
                Line2D([], [], color=COLORS[B], lw=1, ls="--", label="改进 B+"),
                Patch(facecolor=color, alpha=0.12, label="95% 预测区间"),
                Patch(facecolor=color, alpha=0.24, label="80% 预测区间"),
                Line2D([], [], color=color, lw=1.2, label=NAMES[method]),
            ]
            fig.legend(
                handles=handles,
                loc="upper center",
                bbox_to_anchor=(0.5, 0.889),
                ncol=5,
                fontsize=8.2,
                handlelength=2.2,
                columnspacing=1.7,
            )
            fig.text(
                0.5,
                0.84,
                f"区间：{RULE_LABELS[rule]}｜均值固定，区间在起点冻结",
                ha="center",
                fontsize=7.6,
                color="#444444",
            )
            clip_counts = []
            for p, ax in enumerate(axes.flat):
                point = cfg["points"][p]
                ax.axvspan(dates[0], split, color="#F0F6FA", zorder=0)
                ax.axvspan(split, dates[-1], color="#FFF3E9", zorder=0)
                mu = full[method][:, p] - y0[p]
                sd = issue[method + "__" + rule + "__sigma"][:, p]
                bounds = (
                    cfg["figure_contract"]["mentor_limits"][p]
                    if display == "mentor"
                    else limits[p]
                )
                clip = {}
                for level, z, alpha in (
                    (95, 1.959963984540054, 0.12),
                    (80, 1.2815515655446004, 0.24),
                ):
                    low, high = mu[1168:] - z * sd, mu[1168:] + z * sd
                    band = ax.fill_between(
                        dates[1168:],
                        low,
                        high,
                        color=color,
                        alpha=alpha,
                        lw=0,
                        zorder=1,
                    )
                    band.set_gid(f"band_{point}_{level}")
                    clip[f"band{level}_days_outside_axes"] = int(
                        ((low < bounds[0]) | (high > bounds[1])).sum()
                    )
                ax.plot(
                    dates,
                    truth[:, p] - y0[p],
                    color="#161616",
                    lw=1.05,
                    zorder=3,
                    gid=f"observed_{point}",
                )
                ax.plot(
                    dates,
                    full[B][:, p] - y0[p],
                    color=COLORS[B],
                    lw=1,
                    ls="--",
                    zorder=2,
                    gid=f"bplus_{point}",
                )
                ax.plot(dates, mu, color=color, lw=1.15, zorder=4, gid=f"model_{point}")
                ax.axvline(split, color="#777777", ls=":", lw=0.85, zorder=2)
                axis_style(ax, p, dates[0], dates[-1])
                ax.set_ylim(bounds)
                if display == "mentor":
                    ax.set_yticks(cfg["figure_contract"]["mentor_ticks"][p])
                ax.xaxis.set_major_locator(mdates.YearLocator())
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
                rmse = points.loc[(method, rule, point), "rmse"]
                brmse = points.loc[(B, rule, point), "rmse"]
                ax.text(
                    0,
                    -0.215,
                    f"预测 RMSE：模型 {rmse:.2f} mm；B+ {brmse:.2f} mm",
                    transform=ax.transAxes,
                    ha="left",
                    va="top",
                    fontsize=7.8,
                )
                shown = np.concatenate([mu, truth[:, p] - y0[p], full[B][:, p] - y0[p]])
                clip["mean_or_observation_values_outside_axes"] = int(
                    ((shown < bounds[0]) | (shown > bounds[1])).sum()
                )
                assert clip["mean_or_observation_values_outside_axes"] == 0
                if display == "full":
                    assert all(v == 0 for v in clip.values())
                clip_counts.append(clip)
                figure_rows.append(
                    dict(
                        figure=name,
                        model=method,
                        rule=rule,
                        display=display,
                        point=point,
                        model_rmse=float(rmse),
                        bplus_rmse=float(brmse),
                        ylim_low=float(bounds[0]),
                        ylim_high=float(bounds[1]),
                        **clip,
                    )
                )
            clipped = any(
                c["band95_days_outside_axes"] or c["band80_days_outside_axes"]
                for c in clip_counts
            )
            fig.text(
                0.5,
                0.07,
                "蓝色背景：训练拟合；橙色背景：条件预测｜三种子均值，80% / 95% 为逐日边际区间",
                ha="center",
                fontsize=7.6,
            )
            note = (
                "部分区间超出参考图框，仅裁切显示；区间未缩窄，另附完整范围图。"
                if clipped
                else "完整保留所有日期与区间；最终段为探索性评价，未据其成绩重选模型。"
            )
            fig.text(0.5, 0.033, note, ha="center", fontsize=7.6, color="#444444")
            save(fig, out / name, align)
            exports.append(
                dict(
                    figure=name,
                    model=method,
                    rule=rule,
                    display=display,
                    clipped_display=clipped,
                )
            )

    name = "all_methods_forecast"
    fig, axes = canvas(
        "六方法比较：完整 293 日独立条件预测",
        "给定逐日降雨与库水位，无实测位移反馈｜全部方法、全部日期，三种子等权均值",
    )
    styles = {
        B: "--",
        "DRIFT1": ":",
        "RR_COND": "-.",
        OLD: "-",
        REG: "-",
        HALF: (0, (4, 1.5)),
    }
    handles = [Line2D([], [], color="#161616", lw=1.2, label="实测")] + [
        Line2D([], [], color=COLORS[m], ls=styles[m], lw=1.2, label=NAMES[m])
        for m in cfg["methods"]
    ]
    fig.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.893),
        ncol=4,
        fontsize=8,
        handlelength=2.2,
        columnspacing=1.7,
    )
    for p, ax in enumerate(axes.flat):
        point = cfg["points"][p]
        ax.set_facecolor("#FFF8F2")
        for method in cfg["methods"]:
            ax.plot(
                dates[1168:],
                issue[method + "__mean"][:, p] - y0[p],
                color=COLORS[method],
                ls=styles[method],
                lw=1.15 if method in (REG, HALF) else 1,
                gid=f"comparison_{method}_{point}",
            )
        ax.plot(
            dates[1168:],
            truth[1168:, p] - y0[p],
            color="#161616",
            lw=1.05,
            gid=f"comparison_observed_{point}",
        )
        axis_style(ax, p, dates[1168], dates[-1])
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax.text(
            0,
            -0.215,
            f"RMSE：REG1 {points.loc[(REG, 'LAST90', point), 'rmse']:.2f}；半残差 {points.loc[(HALF, 'LAST90', point), 'rmse']:.2f} mm",
            transform=ax.transAxes,
            ha="left",
            va="top",
            fontsize=7.8,
        )
    fig.text(
        0.5,
        0.07,
        "预测期：2019-09-12 至 2020-06-30｜最终段已多次暴露，本图仅作探索性比较",
        ha="center",
        fontsize=7.6,
    )
    fig.text(
        0.5,
        0.033,
        "区间在配套四点图中显示；本图只比较均值，不按测点或种子拼接输出。",
        ha="center",
        fontsize=7.6,
        color="#444444",
    )
    save(fig, out / name, align)
    exports.append(
        dict(
            figure=name,
            model="all",
            rule="mean_only",
            display="full_forecast",
            clipped_display=False,
        )
    )
    pd.DataFrame(figure_rows).to_csv(out / "figure_numbers.csv", index=False)
    write_json(
        out / "manifest.json",
        dict(
            time_utc=utc(),
            exports=exports,
            exclusions=0,
            source_script_sha256=sha(Path(__file__)),
            alignment_helper_sha256=sha(helper),
            reused_save_helper_sha256=sha(
                ROOT / "code/transformer_regularization/figures.py"
            ),
            files={p.name: sha(p) for p in sorted(out.iterdir()) if p.is_file()},
        ),
    )
    print(f"Exported {len(exports)} complete figures: {out}")


if __name__ == "__main__":
    main()
