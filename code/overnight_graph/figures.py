"""Six compact PDF pages from the locked spatial experiment, with source data."""

import argparse
import importlib.util
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
from matplotlib.backends.backend_pdf import PdfPages
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import numpy as np
import pandas as pd
from scipy.stats import norm

from tcn_short_horizon.figures import text_geometry
from transformer_regularization.figures import canvas
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
    verify_implementation,
    write_json,
)

COLORS = {
    B: "#777777",
    "DRIFT1": "#B97838",
    "OLD_HALF": "#886FA0",
    "COND_ATTN": "#AE6870",
    "GRU_LOCAL": "#469081",
    "GRU_GRAPH": "#246592",
}
NAMES = {
    B: "改进 B+",
    "DRIFT1": "DRIFT1",
    "OLD_HALF": "旧半残差",
    "COND_ATTN": "旧起点注意力",
    "GRU_LOCAL": "GRU 本点",
    "GRU_GRAPH": "GRU 固定图",
}
STYLES = {
    B: "--",
    "DRIFT1": ":",
    "OLD_HALF": "-.",
    "COND_ATTN": "--",
    "GRU_LOCAL": "--",
    "GRU_GRAPH": "-",
}
MARKERS = {
    B: "x",
    "DRIFT1": "v",
    "OLD_HALF": "D",
    "COND_ATTN": "s",
    "GRU_LOCAL": "s",
    "GRU_GRAPH": "o",
}


def main(attempt):
    cfg = spec()
    verify_implementation(cfg)
    root = ROOT / cfg["out"]
    assert read_json(root / "independent_audit/receipt.json")["status"] == "passed"
    out = ROOT / cfg["figures"] / attempt
    out.mkdir(parents=True, exist_ok=False)
    pdfout = ROOT / cfg["pdf_out"] / attempt
    pdfout.mkdir(parents=True, exist_ok=False)
    pdfpath = pdfout / "ootang_spatial_pilot_figures.pdf"
    loader = importlib.util.spec_from_file_location(
        "spatial_panel_alignment",
        Path.home() / ".codex/skills/nature-figure/scripts/audit_panel_alignment.py",
    )
    module = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(module)
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
            "pdf.fonttype": 42,
            "axes.unicode_minus": False,
            "legend.frameon": False,
            "path.simplify": False,
        }
    )
    truth = read_labels(ROOT / cfg["data"], 1461)
    _, ds = read_forcing(ROOT / cfg["data"], 1461)
    dates = pd.to_datetime(ds)
    y0 = truth[0]
    stage = root / "base/origin_1168"
    means, sigma, seeds = [
        load_npz(stage / f"{x}.npz") for x in ("means", "sigmas", "seeds")
    ]
    bplus = bank(cfg)[1168]["mean"]
    np.savez_compressed(
        out / "source_arrays.npz",
        observed=truth,
        dates=ds,
        y0=y0,
        bplus_full=bplus,
        **means,
        **{k + "__sigma": v for k, v in sigma.items()},
        **{k + "__seeds": v for k, v in seeds.items()},
    )
    point = pd.read_csv(root / "analysis/metrics_by_point.csv").set_index(
        ["origin", "method", "point"]
    )
    summary = pd.read_csv(root / "analysis/phase_summary.csv").set_index(
        ["origin", "method"]
    )
    seedtable = pd.read_csv(root / "analysis/seed_summary.csv").set_index(
        ["origin", "method", "seed"]
    )
    variance = pd.read_csv(root / "diagnostic/variances.csv").set_index(
        ["start", "point"]
    )
    decision = read_json(root / "diagnostic/decision.json")
    records, numerical, exports = [], [], []

    def record(fig, ax, name, gid, xs, ys, method, recipe, seed=None, point_name=None):
        ax.plot(
            xs,
            ys,
            color=COLORS[method],
            ls="None" if seed is not None else STYLES[method],
            marker=MARKERS[method],
            ms=2.8 if seed is not None else 4.2,
            alpha=0.38 if seed is not None else 1,
            lw=1.05,
            gid=gid,
            zorder=2 if seed is not None else 3,
        )
        records.append(
            dict(
                figure=name,
                gid=gid,
                kind="markers" if seed is not None else "line",
                x=np.asarray(xs, float).tolist(),
                y=np.asarray(ys, float).tolist(),
                method=method,
                recipe=recipe,
                seed=seed,
                point=point_name,
                axes=fig.axes.index(ax),
            )
        )

    def footer(fig, first, second):
        fig.text(0.5, 0.064, first, ha="center", fontsize=7.6)
        fig.text(0.5, 0.034, second, ha="center", fontsize=7.6)

    def finish(fig, name, kind, pdf, **kwargs):
        page = len(exports) + 1
        fig.text(0.975, 0.012, f"{page} / 6", ha="right", fontsize=7.2, color="#666666")
        fig.canvas.draw()
        for rec in (r for r in records if r["figure"] == name):
            xy = fig.axes[rec["axes"]].transData.transform(
                np.column_stack([rec["x"], rec["y"]])
            )
            rec["svg_xy"] = np.column_stack(
                [xy[:, 0] * 72 / fig.dpi, (fig.bbox.height - xy[:, 1]) * 72 / fig.dpi]
            ).tolist()
        geom = text_geometry(fig)
        write_json(out / f"{name}.text_geometry.json", geom)
        assert geom["passed"], geom
        module.require_matplotlib_panel_alignment(
            fig,
            json_out=out / f"{name}.alignment.json",
            tolerance_pt=1.5,
            gutter_tolerance_pt=1.5,
            strict=True,
        )
        renderer = fig.canvas.get_renderer()
        for ax in fig.axes:
            for annotation in ax.texts:
                box = annotation.get_window_extent(renderer)
                assert not any(
                    line.get_path()
                    .transformed(line.get_transform())
                    .intersects_bbox(box, filled=False)
                    for line in ax.lines
                ), annotation.get_text()
        fig.savefig(out / f"{name}.svg")
        fig.savefig(out / f"{name}.png", dpi=300)
        pdf.savefig(fig)
        plt.close(fig)
        exports.append(dict(page=page, figure=name, kind=kind, **kwargs))

    with PdfPages(
        pdfpath,
        metadata={
            "Title": "藕塘固定小图与残差尺度小试",
            "Author": "Landslide-Warning research project",
            "Subject": "Exploratory complete 293-day conditional prediction",
        },
    ) as pdf:
        name = "comparison"
        fig, axes = canvas(
            "固定小图小试：均值收益仍随时段变化",
            "三个起点均评价完整293日 | 给定未来驱动、无位移反馈 | 全部结果为探索性",
        )
        order = [B, "DRIFT1", "OLD_HALF", "COND_ATTN", "GRU_LOCAL", "GRU_GRAPH"]
        fig.legend(
            handles=[
                Line2D(
                    [],
                    [],
                    color=COLORS[m],
                    ls=STYLES[m],
                    marker=MARKERS[m],
                    ms=3.8,
                    lw=1,
                    label=NAMES[m],
                )
                for m in order
            ],
            loc="upper center",
            bbox_to_anchor=(0.5, 0.89),
            ncol=6,
            fontsize=7.8,
            columnspacing=1.6,
        )
        fig.text(
            0.5,
            0.845,
            "线/大符号：集成预测评分；浅色小点：全部三个种子评分；无独立重复置信区间",
            ha="center",
            fontsize=7.6,
        )
        ns = cfg["origins"][1:]
        for j, m in enumerate(order):
            xs = np.arange(3) + (j - 2.5) * 0.018
            vals = [
                summary.loc[(n, m), "rmse"] / summary.loc[(n, B), "rmse"] for n in ns
            ]
            record(fig, axes[0, 0], name, f"ratio_{m}", xs, vals, m, "ratio")
            if m not in (B, "DRIFT1"):
                for s in cfg["seeds"]:
                    vals = [
                        seedtable.loc[(n, m, s), "rmse"] / summary.loc[(n, B), "rmse"]
                        for n in ns
                    ]
                    record(
                        fig,
                        axes[0, 0],
                        name,
                        f"ratio_{m}_seed{s}",
                        xs + (s - 1) * 0.008,
                        vals,
                        m,
                        "ratio",
                        s,
                    )
        for s in [None] + cfg["seeds"]:
            tab = summary if s is None else seedtable

            def rmse(n, m):
                return tab.loc[(n, m) if s is None else (n, m, s), "rmse"]

            vals = [rmse(n, "GRU_GRAPH") - rmse(n, "GRU_LOCAL") for n in ns]
            record(
                fig,
                axes[0, 1],
                name,
                f"paired_{s}",
                np.arange(3) + (0 if s is None else (s - 1) * 0.04),
                vals,
                "GRU_GRAPH",
                "paired",
                s,
            )
        for j, m in enumerate([B, "GRU_LOCAL", "GRU_GRAPH"]):
            xs = np.arange(4) + (j - 1) * 0.06
            for ax, metric in [(axes[1, 0], "rmse"), (axes[1, 1], "crps")]:
                vals = [point.loc[(1168, m, p), metric] for p in cfg["points"]]
                record(fig, ax, name, f"point_{metric}_{m}", xs, vals, m, metric)
                if m != B:
                    for s in cfg["seeds"]:
                        # Read scores from saved arrays, preserving every seed and point.
                        from transformer_temporal.audit import independent_scores

                        vals = [
                            independent_scores(
                                truth[1168:, p], seeds[m][s, :, p], sigma[m][p]
                            )[metric]
                            for p in range(4)
                        ]
                        record(
                            fig,
                            ax,
                            name,
                            f"point_{metric}_{m}_seed{s}",
                            xs + (s - 1) * 0.015,
                            vals,
                            m,
                            metric,
                            s,
                        )
        axes[0, 1].axhline(0, color="#777777", ls=":", lw=0.8)
        titles = [
            "a  各时段误差：低于1优于 B+",
            "b  配对增量：图版 - 本点版",
            "c  最终四点均值误差",
            "d  最终四点概率评分",
        ]
        labels = ["RMSE / B+ RMSE", "RMSE 差值 / mm", "RMSE / mm", "CRPS / mm"]
        for i, ax in enumerate(axes.flat):
            ax.set_title(titles[i], loc="left", fontweight="bold", pad=7)
            ax.set_ylabel(labels[i])
            ax.grid(True, color="#D9DDE1", lw=0.55, alpha=0.85)
            ax.set_axisbelow(True)
            count = 3 if i < 2 else 4
            ax.set_xticks(
                np.arange(count), [str(n) for n in ns] if i < 2 else cfg["points"]
            )
            ax.set_xlim(-0.2, count - 0.8)
            if i < 2:
                ax.set_xlabel("拟合前缀 / 日")
            if i != 1:
                ax.set_ylim(bottom=0)
        footer(
            fig,
            "图边增量三窗均未过预定门槛；最终两版均值门未过，概率门通过。",
            "图版最终90%覆盖99.15%、平均宽度104.16 mm；B+为100%、201.64 mm。概率改善不代表均值改善。",
        )
        finish(fig, name, "comparison", pdf)

        ranges = {}
        # Both full-range figures share point-specific limits based on both arms.
        for p in range(4):
            v = [truth[:, p] - y0[p], bplus[:, p] - y0[p]]
            for m in cfg["arms"]:
                mu = means[m][:, p] - y0[p]
                q = norm.ppf(0.975) * sigma[m][p]
                v += [mu - q, mu + q]
            allv = np.concatenate(v)
            lo, hi = float(allv.min()), float(allv.max())
            ranges[p] = (lo - 0.05 * (hi - lo), hi + 0.06 * (hi - lo))

        def trajectory(method, display):
            name = f"{method}_{display}"
            fig, axes = canvas(
                f"{NAMES[method]}：四点 8:2 独立条件预测",
                "前1168日训练 | 后293日一次发出 | 给定未来降雨/库水位，无实测位移反馈",
            )
            blue = COLORS["GRU_GRAPH"]
            fig.legend(
                handles=[
                    Line2D([], [], color="#161616", lw=1.1, label="实测"),
                    Line2D([], [], color="#777777", ls="--", lw=1, label="改进 B+"),
                    Patch(facecolor=blue, alpha=0.12, label="95% 预测区间"),
                    Patch(facecolor=blue, alpha=0.24, label="80% 预测区间"),
                    Line2D([], [], color=blue, lw=1.2, label=NAMES[method]),
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
                pname = cfg["points"][p]
                mu = means[method][:, p] - y0[p]
                limits = LIMITS[p] if display == "mentor" else ranges[p]
                ax.axvspan(dates[0], split, color="#F0F6FA", zorder=0)
                ax.axvspan(split, dates[-1], color="#FFF3E9", zorder=0)
                clipping = {}
                for level, alpha in [(95, 0.12), (80, 0.24)]:
                    q = norm.ppf((1 + level / 100) / 2) * sigma[method][p]
                    lo, hi = mu - q, mu + q
                    band = ax.fill_between(
                        dates[1168:], lo, hi, color=blue, alpha=alpha, lw=0, zorder=1
                    )
                    band.set_gid(f"band_{pname}_{level}")
                    clipping[f"band{level}_outside_days"] = int(
                        ((lo < limits[0]) | (hi > limits[1])).sum()
                    )
                for key, x, y, color, style, width, z in [
                    ("observed", dates, truth[:, p] - y0[p], "#161616", "-", 1.05, 3),
                    ("bplus", dates, bplus[:, p] - y0[p], "#777777", "--", 1, 2),
                    ("model", dates[1168:], mu, blue, "-", 1.15, 4),
                ]:
                    ax.plot(
                        x,
                        y,
                        color=color,
                        ls=style,
                        lw=width,
                        zorder=z,
                        gid=f"{key}_{pname}",
                    )
                ax.axvline(split, color="#777777", ls=":", lw=0.85)
                ax.set_title(
                    f"{pname} | {DOMAINS[p]}", loc="left", fontweight="bold", pad=7
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
                ax.grid(True, color="#D9DDE1", lw=0.55, alpha=0.85)
                ax.set_axisbelow("line")
                rmse, brmse = [point.loc[(1168, m, pname), "rmse"] for m in (method, B)]
                ax.text(
                    0,
                    -0.215,
                    f"预测 RMSE：模型 {rmse:.2f} mm；B+ {brmse:.2f} mm",
                    transform=ax.transAxes,
                    ha="left",
                    va="top",
                    fontsize=7.8,
                )
                v = np.r_[mu, truth[:, p] - y0[p], bplus[:, p] - y0[p]]
                clipping["mean_observed_outside_values"] = int(
                    ((v < limits[0]) | (v > limits[1])).sum()
                )
                if display == "full":
                    assert not any(clipping.values())
                numerical.append(
                    dict(
                        figure=name,
                        point=pname,
                        rmse=rmse,
                        bplus_rmse=brmse,
                        ylim_low=limits[0],
                        ylim_high=limits[1],
                        **clipping,
                    )
                )
            footer(
                fig,
                "蓝色背景：历史训练段；橙色背景：完整预测段；蓝线为三种子等权集成，阴影为固定高斯边际区间",
                "导师固定纵轴；部分区间越界，全部范围见第5-6页，数值评分不裁剪"
                if display == "mentor"
                else "完整纵轴范围：含全部95%区间；两版GRU使用相同的逐点纵轴范围",
            )
            finish(fig, name, "trajectory", pdf, method=method, display=display)

        trajectory("GRU_GRAPH", "mentor")
        trajectory("GRU_LOCAL", "mentor")

        name = "residual_diagnostic"
        fig, axes = canvas(
            "因果残差诊断：未形成稳定的双分量启动依据",
            "横轴：180日块起点索引 | 因果EMA30，前30日为预定初始化期 | 仅用前1152日标签",
        )
        fig.legend(
            handles=[
                Line2D(
                    [],
                    [],
                    color=COLORS["GRU_GRAPH"],
                    marker="o",
                    lw=1.05,
                    label="fast 方差 / 总残差方差",
                ),
                Line2D(
                    [],
                    [],
                    color="#777777",
                    ls="--",
                    lw=0.8,
                    label="预定方差比参考线 0.1",
                ),
            ],
            loc="upper center",
            bbox_to_anchor=(0.5, 0.889),
            ncol=2,
            fontsize=8.2,
        )
        fig.text(
            0.5,
            0.84,
            "方差比仅为描述性诊断；slow 与 fast 存在协方差，不能解释为互不重叠的贡献比例",
            ha="center",
            fontsize=7.6,
        )
        starts = [432, 612, 792, 972]
        for p, ax in enumerate(axes.flat):
            pname = cfg["points"][p]
            vals = [variance.loc[(n, pname), "fast_ratio"] for n in starts]
            record(
                fig,
                ax,
                name,
                f"variance_{pname}",
                np.arange(4),
                vals,
                "GRU_GRAPH",
                "fast_ratio",
                point_name=pname,
            )
            ax.axhline(0.1, color="#777777", ls="--", lw=0.8)
            ax.set_ylim(0, 0.7)
            ax.set_yticks(np.arange(0, 0.71, 0.1))
            ax.set_xlim(-0.2, 3.2)
            ax.set_xticks(np.arange(4), [str(n) for n in starts])
            ax.set_ylabel("方差比")
            ax.set_title(
                f"{pname} | {DOMAINS[p]}", loc="left", fontweight="bold", pad=7
            )
            ax.set_axisbelow(True)
            ax.grid(True, color="#D9DDE1", lw=0.55, alpha=0.85)
            support = max(
                len(e["passing_blocks"])
                for e in decision["eligible"]
                if e["point"] == pname
            )
            ax.text(
                0,
                -0.215,
                f"同一驱动/滞后/符号同时满足条件：最多 {support}/4 块（需≥3）",
                transform=ax.transAxes,
                ha="left",
                va="top",
                fontsize=7.6,
            )
        footer(
            fig,
            "启动还须 |相关|≥0.3、同符号跨至少3块且至少2点共享；96组预定相关全部保留，符合点0。",
            "未启动 GRU_GRAPH_DUAL，新增双头拟合0；此结果不证明所有分解无效，也不建立驱动的因果机制。",
        )
        finish(fig, name, "diagnostic", pdf)
        trajectory("GRU_GRAPH", "full")
        trajectory("GRU_LOCAL", "full")

    write_json(out / "plot_records.json", records)
    pd.DataFrame(numerical).to_csv(
        out / "figure_numbers.csv", index=False, float_format="%.17g"
    )
    write_json(
        out / "manifest.json",
        dict(
            time_utc=utc(),
            exports=exports,
            source_script_sha256=sha(ROOT / "code/overnight_graph/figures.py"),
            contract_sha256=sha(
                ROOT / "docs/ootang_overnight_graph_figure_contract.v1.0.md"
            ),
            files={p.name: sha(p) for p in sorted(out.glob("*"))},
            pdf=str(pdfpath.relative_to(ROOT)),
            pdf_sha256=sha(pdfpath),
            prediction_fits=0,
            physical_forwards=0,
        ),
    )
    print(pdfpath)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", default="v1")
    main(parser.parse_args().attempt)
