"""Three figures from the locked origin-conditioned pilot; no fitting."""

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
from scipy.stats import norm

from transformer_regularization.figures import canvas, save
from transformer_temporal.figures import LIMITS, TICKS, DOMAINS
from .core import (
    B,
    ROOT,
    bank,
    load_npz,
    read_forcing,
    read_json,
    read_labels,
    sha,
    spec,
    utc,
    write_json,
)

NAMES = {
    "COND_ATTN": "完整历史注意力",
    "NO_OBS_ATTN": "无历史位移注意力",
    "POOL_MLP": "历史均匀池化",
    B: "改进 B+",
}
COLORS = {
    "COND_ATTN": "#246592",
    "NO_OBS_ATTN": "#B97838",
    "POOL_MLP": "#598D7B",
    B: "#777777",
}
MARKERS = {"COND_ATTN": "o", "NO_OBS_ATTN": "s", "POOL_MLP": "D", B: "x"}
STYLES = {"COND_ATTN": "-", "NO_OBS_ATTN": "--", "POOL_MLP": "-.", B: ":"}


def main():
    cfg = spec()
    root = ROOT / cfg["out"]
    assert read_json(root / "independent_audit/receipt.json")["status"] == "passed"
    out = ROOT / cfg["figures"] / "v1"
    out.mkdir(parents=True, exist_ok=False)
    helper = (
        Path.home() / ".codex/skills/nature-figure/scripts/audit_panel_alignment.py"
    )
    loader = importlib.util.spec_from_file_location("origin_alignment", helper)
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
    truth = read_labels(ROOT / cfg["data"], 1461)
    _, ds = read_forcing(ROOT / cfg["data"], 1461)
    dates = pd.to_datetime(ds)
    y0 = truth[0]
    means = load_npz(root / "origin_1168/means.npz")
    sigma = load_npz(root / "origin_1168/sigmas.npz")
    allseeds = load_npz(root / "origin_1168/seeds.npz")
    bplus = bank(cfg)[1168]["mean"]
    np.savez_compressed(
        out / "source_arrays.npz",
        observed=truth,
        dates=ds,
        y0=y0,
        bplus_full=bplus,
        **means,
        **{k + "__sigma": v for k, v in sigma.items()},
        **{k + "__seeds": v for k, v in allseeds.items()},
    )
    points = pd.read_csv(root / "analysis/metrics_by_point.csv").set_index(
        ["origin", "method", "point"]
    )
    summary = pd.read_csv(root / "analysis/phase_summary.csv").set_index(
        ["origin", "method"]
    )
    seeds = pd.read_csv(root / "analysis/seed_summary.csv").set_index(
        ["origin", "method", "seed"]
    )
    numerical, exports = [], []
    method = "COND_ATTN"
    ranges = []
    for p in range(4):
        mu = means[method][:, p] - y0[p]
        q = norm.ppf(0.975) * sigma[method][p]
        v = np.r_[truth[:, p] - y0[p], bplus[:, p] - y0[p], mu - q, mu + q]
        lo, hi = v.min(), v.max()
        ranges.append((lo - 0.05 * (hi - lo), hi + 0.06 * (hi - lo)))
    for display in ["mentor", "full"]:
        name = f"COND_ATTN_{display}"
        fig, axes = canvas(
            "小型历史交叉注意力：四点 8:2 独立条件预测",
            "前1168日训练｜后293日一次发出｜给定未来降雨/库水位，无实测位移反馈",
        )
        fig.legend(
            handles=[
                Line2D([], [], color="#161616", lw=1.1, label="实测"),
                Line2D([], [], color="#777777", ls="--", lw=1, label="改进 B+"),
                Patch(facecolor="#246592", alpha=0.12, label="95% 预测区间"),
                Patch(facecolor="#246592", alpha=0.24, label="80% 预测区间"),
                Line2D([], [], color="#246592", lw=1.2, label=NAMES[method]),
            ],
            loc="upper center",
            bbox_to_anchor=(0.5, 0.889),
            ncol=5,
            fontsize=8.2,
        )
        fig.text(
            0.5,
            0.84,
            "历史段仅展示实测和 B+；神经预测从分界线开始；最终窗口为探索性评价",
            ha="center",
            fontsize=7.6,
        )
        split = dates[1168] - pd.Timedelta(hours=12)
        for p, ax in enumerate(axes.flat):
            point = cfg["points"][p]
            mu = means[method][:, p] - y0[p]
            limits = LIMITS[p] if display == "mentor" else ranges[p]
            ax.axvspan(dates[0], split, color="#F0F6FA", zorder=0)
            ax.axvspan(split, dates[-1], color="#FFF3E9", zorder=0)
            clipping = {}
            for level, alpha in [(95, 0.12), (80, 0.24)]:
                q = norm.ppf((1 + level / 100) / 2) * sigma[method][p]
                lo, hi = mu - q, mu + q
                band = ax.fill_between(
                    dates[1168:], lo, hi, color="#246592", alpha=alpha, lw=0, zorder=1
                )
                band.set_gid(f"band_{point}_{level}")
                clipping[f"band{level}_outside_days"] = int(
                    ((lo < limits[0]) | (hi > limits[1])).sum()
                )
            for key, x, y, color, style, width, z in [
                ("observed", dates, truth[:, p] - y0[p], "#161616", "-", 1.05, 3),
                ("bplus", dates, bplus[:, p] - y0[p], "#777777", "--", 1.0, 2),
                ("model", dates[1168:], mu, "#246592", "-", 1.15, 4),
            ]:
                ax.plot(
                    x,
                    y,
                    color=color,
                    ls=style,
                    lw=width,
                    zorder=z,
                    gid=f"{key}_{point}",
                )
            ax.axvline(split, color="#777777", ls=":", lw=0.85)
            ax.set_title(
                f"{point} | {DOMAINS[p]}", loc="left", fontweight="bold", pad=7
            )
            ax.set_ylabel("累计位移 / mm")
            ax.set_ylim(limits)
            ax.set_xlim(
                dates[0] - pd.Timedelta(days=20), dates[-1] + pd.Timedelta(days=20)
            )
            if display == "mentor":
                ax.set_yticks(TICKS[p])
            ax.xaxis.set_major_locator(mdates.YearLocator())
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
            ax.set_axisbelow("line")
            ax.grid(True, axis="both", color="#D9DDE1", lw=0.55, alpha=0.85)
            rmse = points.loc[(1168, method, point), "rmse"]
            brmse = points.loc[(1168, B, point), "rmse"]
            ax.text(
                0,
                -0.215,
                f"预测 RMSE：模型 {rmse:.2f} mm；B+ {brmse:.2f} mm",
                transform=ax.transAxes,
                ha="left",
                va="top",
                fontsize=7.8,
            )
            values = np.r_[mu, truth[:, p] - y0[p], bplus[:, p] - y0[p]]
            clipping["mean_observed_outside_values"] = int(
                ((values < limits[0]) | (values > limits[1])).sum()
            )
            if display == "full":
                assert not any(clipping.values())
            numerical.append(
                dict(
                    figure=name,
                    point=point,
                    rmse=rmse,
                    bplus_rmse=brmse,
                    ylim_low=limits[0],
                    ylim_high=limits[1],
                    **clipping,
                )
            )
        fig.text(
            0.5,
            0.061,
            "蓝色：历史训练段；橙色：完整预测段；蓝线为三种子等权集成，阴影为固定高斯边际预测区间",
            ha="center",
            fontsize=7.7,
        )
        fig.text(
            0.5,
            0.030,
            "导师固定纵轴；超出坐标的区间另见完整范围图"
            if display == "mentor"
            else "完整纵轴范围：包含实测、均值及全部95%预测区间",
            ha="center",
            fontsize=7.6,
        )
        save(fig, out / name, align)
        exports.append(dict(figure=name, kind="trajectory", method=method))

    fig, axes = canvas(
        "小型历史交叉注意力：跨时段收益未稳定",
        "三个起点均评价完整293日｜预定三臂全部保留｜所有窗口为探索性、存在重叠",
    )
    handles = [
        Line2D(
            [],
            [],
            color=COLORS[m],
            ls=STYLES[m],
            marker=MARKERS[m],
            ms=4,
            lw=1.1,
            label=NAMES[m],
        )
        for m in cfg["arms"] + [B]
    ]
    fig.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.89),
        ncol=4,
        fontsize=8.2,
    )
    fig.text(
        0.5,
        0.845,
        "线/大符号：集成预测的评分；浅色小点：全部三个种子；不代表独立重复置信区间",
        ha="center",
        fontsize=7.6,
    )
    records = []
    ns = cfg["origins"][1:]

    def record(ax, gid, xs, ys, method, recipe, seed=None, marker_only=False):
        ax.plot(
            xs,
            ys,
            color=COLORS[method],
            ls="None" if marker_only else STYLES[method],
            marker=MARKERS[method],
            ms=2.7 if marker_only else 4.1,
            alpha=0.38 if marker_only else 1,
            lw=1.05,
            gid=gid,
            zorder=2 if marker_only else 3,
        )
        records.append(
            dict(
                gid=gid,
                kind="markers" if marker_only else "line",
                x=np.asarray(xs, dtype=float).tolist(),
                y=np.asarray(ys, dtype=float).tolist(),
                method=method,
                recipe=recipe,
                seed=seed,
                axes=fig.axes.index(ax),
            )
        )

    for j, method in enumerate(cfg["arms"]):
        xs = np.arange(3) + (j - 1) * 0.045
        ratio = [
            summary.loc[(n, method), "rmse"] / summary.loc[(n, B), "rmse"] for n in ns
        ]
        record(axes[0, 0], f"ratio_{method}", xs, ratio, method, "ratio")
        for seed in cfg["seeds"]:
            values = [
                seeds.loc[(n, method, seed), "rmse"] / summary.loc[(n, B), "rmse"]
                for n in ns
            ]
            record(
                axes[0, 0],
                f"ratio_{method}_seed{seed}",
                xs + (seed - 1) * 0.011,
                values,
                method,
                "ratio",
                seed,
                True,
            )
        values = [points.loc[(1168, method, p), "rmse"] for p in cfg["points"]]
        record(
            axes[1, 0],
            f"point_{method}",
            np.arange(4) + (j - 1) * 0.045,
            values,
            method,
            "point",
        )
        for seed in cfg["seeds"]:
            values = np.sqrt(
                np.mean((allseeds[method][seed] - truth[1168:]) ** 2, axis=0)
            )
            record(
                axes[1, 0],
                f"point_{method}_seed{seed}",
                np.arange(4) + (j - 1) * 0.045 + (seed - 1) * 0.011,
                values,
                method,
                "point",
                seed,
                True,
            )
        values = [100 * summary.loc[(n, method), "coverage90"] for n in ns]
        record(axes[1, 1], f"coverage_{method}", xs, values, method, "coverage")
        for seed in cfg["seeds"]:
            values = [100 * seeds.loc[(n, method, seed), "coverage90"] for n in ns]
            record(
                axes[1, 1],
                f"coverage_{method}_seed{seed}",
                xs + (seed - 1) * 0.011,
                values,
                method,
                "coverage",
                seed,
                True,
            )
    for j, method in enumerate(["NO_OBS_ATTN", "POOL_MLP"]):
        xs = np.arange(3) + (j - 0.5) * 0.04
        values = [
            summary.loc[(n, "COND_ATTN"), "rmse"] - summary.loc[(n, method), "rmse"]
            for n in ns
        ]
        record(axes[0, 1], f"paired_{method}", xs, values, method, "paired")
        for seed in cfg["seeds"]:
            values = [
                seeds.loc[(n, "COND_ATTN", seed), "rmse"]
                - seeds.loc[(n, method, seed), "rmse"]
                for n in ns
            ]
            record(
                axes[0, 1],
                f"paired_{method}_seed{seed}",
                xs + (seed - 1) * 0.011,
                values,
                method,
                "paired",
                seed,
                True,
            )
    record(
        axes[1, 0],
        "point_BPLUS",
        np.arange(4),
        [points.loc[(1168, B, p), "rmse"] for p in cfg["points"]],
        B,
        "point",
    )
    record(
        axes[1, 1],
        "coverage_BPLUS",
        np.arange(3),
        [100 * summary.loc[(n, B), "coverage90"] for n in ns],
        B,
        "coverage",
    )
    axes[0, 0].axhline(1, color="#777777", ls=":", lw=0.8)
    axes[0, 1].axhline(0, color="#777777", ls=":", lw=0.8)
    axes[1, 1].axhline(90, color="#999999", ls="--", lw=0.7)
    titles = [
        "a  各时段误差：低于1优于 B+",
        "b  配对差值：完整历史 − 对照",
        "c  最终四点误差",
        "d  概率覆盖：目标水平90%",
    ]
    labels = ["RMSE / B+ RMSE", "RMSE 差值 / mm", "RMSE / mm", "90% 区间覆盖率 / %"]
    for i, ax in enumerate(axes.flat):
        ax.set_title(titles[i], loc="left", fontweight="bold", pad=7)
        ax.set_ylabel(labels[i])
        ax.grid(True, axis="both", color="#D9DDE1", lw=0.55, alpha=0.85)
        ax.set_axisbelow(True)
        if i == 2:
            ax.set_xticks(np.arange(4), cfg["points"])
            ax.set_xlim(-0.2, 3.2)
        else:
            ax.set_xticks(np.arange(3), [str(n) for n in ns])
            ax.set_xlabel("拟合前缀 / 日")
            ax.set_xlim(-0.18, 2.18)
    axes[0, 0].set_ylim(bottom=0)
    axes[1, 0].set_ylim(bottom=0)
    axes[1, 1].set_ylim(0, 105)
    fig.text(
        0.5,
        0.077,
        "b：负值表示完整历史更好；橙色对照为无历史位移，绿色对照为历史均匀池化。",
        ha="center",
        fontsize=7.6,
    )
    fig.text(
        0.5,
        0.044,
        "最终均值与概率完整条件均未通过；覆盖率须结合宽度、CRPS和区间评分判断，详见报告。",
        ha="center",
        fontsize=7.6,
    )
    fig.canvas.draw()
    for r in records:
        xy = fig.axes[r["axes"]].transData.transform(np.column_stack([r["x"], r["y"]]))
        r["svg_xy"] = np.column_stack(
            [xy[:, 0] * 72 / fig.dpi, (fig.bbox.height - xy[:, 1]) * 72 / fig.dpi]
        ).tolist()
    write_json(out / "plot_records.json", records)
    save(fig, out / "comparison", align)
    exports.append(dict(figure="comparison", kind="comparison"))
    pd.DataFrame(numerical).to_csv(
        out / "figure_numbers.csv", index=False, float_format="%.17g"
    )
    write_json(
        out / "manifest.json",
        dict(
            time_utc=utc(),
            exports=exports,
            source_script_sha256=sha(ROOT / "code/transformer_origin/figures.py"),
            contract_sha256=sha(
                ROOT / "docs/ootang_transformer_origin_figure_contract.v1.0.md"
            ),
            files={p.name: sha(p) for p in sorted(out.glob("*"))},
        ),
    )
    print(out)


if __name__ == "__main__":
    main()
