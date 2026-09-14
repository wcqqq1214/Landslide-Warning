"""Saved-result figures: paired temporal evidence and complete mentor trajectories."""

import importlib.util
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import FuncFormatter
import numpy as np
import pandas as pd
from scipy.stats import norm

from transformer_regularization.figures import canvas, save
from .core import (
    ALPHAS,
    B,
    ROOT,
    load_npz,
    read_forcing,
    read_json,
    read_labels,
    sha,
    spec,
    utc,
    write_json,
)

LIMITS = [(-27, 565), (-37, 760), (-17, 365), (-18, 399)]
TICKS = [
    list(range(0, 501, 100)),
    list(range(0, 701, 100)),
    list(range(0, 351, 50)),
    list(range(0, 351, 50)),
]
DOMAINS = ["O3", "O2", "O1-up", "O1-down"]
NAMES = {
    B: "改进 B+",
    "DRIFT1": "DRIFT1",
    "RR_COND": "普通岭回归",
    "ALPHA_SELECTED": "历史选择 α",
    "LAMBDA_SELECTED": "历史选择 λ",
}
COLORS = {
    B: "#777777",
    "DRIFT1": "#AD814A",
    "RR_COND": "#8A7FAD",
    "ALPHA_SELECTED": "#3A939B",
    "LAMBDA_SELECTED": "#246592",
}


def main():
    cfg = spec()
    root = ROOT / cfg["out"]
    out = ROOT / cfg["figures"] / "v1"
    assert read_json(root / "verification_v1/receipt.json")["status"] == "passed"
    out.mkdir(parents=True, exist_ok=False)
    helper = (
        Path.home() / ".codex/skills/nature-figure/scripts/audit_panel_alignment.py"
    )
    loader = importlib.util.spec_from_file_location("temporal_alignment", helper)
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
    y0 = truth[0]
    _, date_strings = read_forcing(ROOT / cfg["data"], 1461)
    dates = pd.to_datetime(date_strings)
    full = {}
    sigma = {}
    for phase in ["alpha", "lambda"]:
        full.update(load_npz(root / f"origin_1168/{phase}_means.npz"))
        sigma.update(load_npz(root / f"origin_1168/{phase}_sigmas.npz"))
    np.savez_compressed(
        out / "source_arrays.npz",
        observed=truth,
        dates=date_strings,
        y0=y0,
        **full,
        **{k + "__sigma": v for k, v in sigma.items()},
    )
    table = pd.read_csv(root / "analysis/metrics_by_point.csv").set_index(
        ["origin", "method", "point"]
    )
    summary = pd.read_csv(root / "analysis/phase_summary.csv").set_index(
        ["origin", "method"]
    )
    seeds = pd.read_csv(root / "analysis/seed_summary.csv").set_index(
        ["origin", "method", "seed"]
    )
    selected = {
        str(n): {
            p: read_json(root / f"origin_{n}/{p}_selection.json")["selected"]
            for p in ["alpha", "lambda"]
        }
        for n in cfg["origins"][1:]
    }
    write_json(out / "selected_parameters.json", selected)
    numerical = []
    exports = []
    ranges = []
    for p in range(4):
        values = [truth[:, p] - y0[p], full[B][:, p] - y0[p]]
        for method in ["ALPHA_SELECTED", "LAMBDA_SELECTED"]:
            mu = full[method][:, p] - y0[p]
            sd = sigma[method][p]
            values += [
                mu,
                mu[1168:] - norm.ppf(0.975) * sd,
                mu[1168:] + norm.ppf(0.975) * sd,
            ]
        low = min(v.min() for v in values)
        high = max(v.max() for v in values)
        ranges.append((low - 0.05 * (high - low), high + 0.06 * (high - low)))

    for method in ["ALPHA_SELECTED", "LAMBDA_SELECTED"]:
        phase = "alpha" if method == "ALPHA_SELECTED" else "lambda"
        chosen = selected["1168"][phase]
        value = ALPHAS[chosen] if phase == "alpha" else cfg["lambdas"][chosen]
        for display in ["mentor", "full"]:
            name = f"{method}_{display}"
            fig, axes = canvas(
                f"Transformer：历史选择 {'α' if phase == 'alpha' else 'λ'}={value:g} 的最终预测",
                "前1168日拟合｜后293日一次发出｜给定未来降雨/库水位，无实测位移反馈",
            )
            handles = [
                Line2D([], [], color="#161616", lw=1.1, label="实测"),
                Line2D([], [], color="#777777", ls="--", lw=1, label="改进 B+"),
                Patch(facecolor="#246592", alpha=0.12, label="95% 预测区间"),
                Patch(facecolor="#246592", alpha=0.24, label="80% 预测区间"),
                Line2D([], [], color="#246592", lw=1.2, label=NAMES[method]),
            ]
            fig.legend(
                handles=handles,
                loc="upper center",
                bbox_to_anchor=(0.5, 0.889),
                ncol=5,
                fontsize=8.2,
            )
            fig.text(
                0.5,
                0.84,
                "参数与区间均在起点冻结；最终窗口为探索性评价",
                ha="center",
                fontsize=7.6,
            )
            split = dates[1168] - pd.Timedelta(hours=12)
            for p, ax in enumerate(axes.flat):
                mu = full[method][:, p] - y0[p]
                sd = sigma[method][p]
                limits = LIMITS[p] if display == "mentor" else ranges[p]
                ax.axvspan(dates[0], split, color="#F0F6FA", zorder=0)
                ax.axvspan(split, dates[-1], color="#FFF3E9", zorder=0)
                clipping = {}
                for level, opacity in [(95, 0.12), (80, 0.24)]:
                    z = norm.ppf((1 + level / 100) / 2)
                    lo, hi = mu[1168:] - z * sd, mu[1168:] + z * sd
                    band = ax.fill_between(
                        dates[1168:],
                        lo,
                        hi,
                        color="#246592",
                        alpha=opacity,
                        lw=0,
                        zorder=1,
                    )
                    band.set_gid(f"band_{cfg['points'][p]}_{level}")
                    clipping[f"band{level}_outside_days"] = int(
                        ((lo < limits[0]) | (hi > limits[1])).sum()
                    )
                ax.plot(
                    dates,
                    truth[:, p] - y0[p],
                    color="#161616",
                    lw=1.05,
                    zorder=3,
                    gid=f"observed_{cfg['points'][p]}",
                )
                ax.plot(
                    dates,
                    full[B][:, p] - y0[p],
                    color="#777777",
                    ls="--",
                    lw=1,
                    zorder=2,
                    gid=f"bplus_{cfg['points'][p]}",
                )
                ax.plot(
                    dates,
                    mu,
                    color="#246592",
                    lw=1.15,
                    zorder=4,
                    gid=f"model_{cfg['points'][p]}",
                )
                ax.axvline(split, color="#777777", ls=":", lw=0.85)
                ax.set_title(
                    f"{cfg['points'][p]} | {DOMAINS[p]}",
                    loc="left",
                    fontweight="bold",
                    pad=7,
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
                rmse = table.loc[(1168, method, cfg["points"][p]), "rmse"]
                brmse = table.loc[(1168, B, cfg["points"][p]), "rmse"]
                ax.text(
                    0,
                    -0.215,
                    f"预测 RMSE：模型 {rmse:.2f} mm；B+ {brmse:.2f} mm",
                    transform=ax.transAxes,
                    ha="left",
                    va="top",
                    fontsize=7.8,
                )
                shown = np.r_[mu, truth[:, p] - y0[p], full[B][:, p] - y0[p]]
                clipping["mean_observed_outside_values"] = int(
                    ((shown < limits[0]) | (shown > limits[1])).sum()
                )
                if display == "full":
                    assert not any(clipping.values())
                numerical.append(
                    dict(
                        figure=name,
                        method=method,
                        point=cfg["points"][p],
                        rmse=rmse,
                        bplus_rmse=brmse,
                        ylim_low=limits[0],
                        ylim_high=limits[1],
                        **clipping,
                    )
                )
            fig.text(
                0.5,
                0.055,
                "蓝底：训练拟合；橙底：独立条件预测。区间为逐日边际预测区间。",
                ha="center",
                fontsize=7.6,
            )
            fig.text(
                0.5,
                0.025,
                "部分区间可能超出参考图框；另附完整范围图，区间数据未缩窄。"
                if display == "mentor"
                else "完整范围保留全部均值、实测及95%区间；不删尾段。",
                ha="center",
                fontsize=7.6,
            )
            save(fig, out / name, align)
            exports.append(
                dict(figure=name, kind="trajectory", method=method, display=display)
            )

    fig, axes = canvas(
        "历史选择能否迁移到下一个293日窗口？",
        "每次仅用较早90日选参数，随后90日校准；四个预测窗口部分重叠",
    )
    fig.subplots_adjust(bottom=0.165, top=0.805, hspace=0.55, wspace=0.28)
    nvals = cfg["origins"][1:]
    x = np.arange(4)
    records = []
    labels = ["612", "792", "972", "1168\n最终探索"]

    def track(ax, xv, yv, gid, kind="line", **kwargs):
        (line,) = ax.plot(xv, yv, gid=gid, **kwargs)
        records.append(
            dict(
                gid=gid,
                kind=kind,
                x=np.asarray(xv).tolist(),
                y=np.asarray(yv).tolist(),
                axes=fig.axes.index(ax),
                artist=line,
            )
        )

    ax = axes[0, 0]
    ax.set_title("a  历史选择的参数", loc="left", fontweight="bold")
    track(
        ax,
        x,
        [ALPHAS[selected[str(n)]["alpha"]] for n in nvals],
        "chosen_alpha",
        color="#3A939B",
        lw=1.3,
        label="α",
    )
    track(
        ax,
        x,
        [cfg["lambdas"][selected[str(n)]["lambda"]] for n in nvals],
        "chosen_lambda",
        color="#246592",
        ls="--",
        lw=1.3,
        label="λ",
    )
    ax.set_ylabel("参数值")
    ax.set_ylim(-0.15, 3.3)
    ax.legend(fontsize=7.6, loc="center left")
    ax = axes[0, 1]
    ax.set_title("b  四点平均误差与对照", loc="left", fontweight="bold")
    for key in NAMES:
        values = np.array(
            [summary.loc[(n, key), "rmse"] / summary.loc[(n, B), "rmse"] for n in nvals]
        )
        assert (values > 0).all()
        track(
            ax,
            x,
            values,
            "ratio_" + key,
            color=COLORS[key],
            lw=1.2,
            ls="--" if key == B else "-",
            label=NAMES[key],
        )
    ax.set_yscale("log")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
    ax.set_ylabel("RMSE / B+（对数）")
    ax.set_ylim(.12, 5)
    ax.set_yticks([.2, .5, 1, 2, 5])
    ax.legend(fontsize=7.6, loc="upper left", ncol=2, columnspacing=0.8)
    ax = axes[1, 0]
    ax.set_title("c  λ 对 α 的同种子增益", loc="left", fontweight="bold")
    ax.axhline(0, color="#888888", ls="--", lw=0.7)
    for s in range(3):
        delta = [
            100
            * (
                seeds.loc[(n, "LAMBDA_SELECTED", s), "rmse"]
                / seeds.loc[(n, "ALPHA_SELECTED", s), "rmse"]
                - 1
            )
            for n in nvals
        ]
        track(
            ax,
            x + (s - 1) * 0.1,
            delta,
            "seed_" + str(s),
            kind="markers",
            color=["#92B5CF", "#548BB3", "#246592"][s],
            marker="o",
            markersize=4,
            ls="none",
            label=f"种子{s}",
        )
    ax.set_ylabel("RMSE变化 / %（负值更好）")
    ax.set_ylim(-9, 7)
    ax.legend(fontsize=7.6, loc="upper left", ncol=3)
    ax = axes[1, 1]
    ax.set_title("d  λ 选择版逐点区间覆盖", loc="left", fontweight="bold")
    ax.axhline(80, color="#888888", ls="--", lw=0.7)
    for p, point in enumerate(cfg["points"]):
        track(
            ax,
            x,
            [
                100 * table.loc[(n, "LAMBDA_SELECTED", point), "coverage90"]
                for n in nvals
            ],
            "coverage_" + point,
            color=["#246592", "#3A939B", "#AD814A", "#8A7FAD"][p],
            lw=1.2,
            label=point,
        )
    ax.set_ylabel("90%区间覆盖 / %")
    ax.set_ylim(-5, 108)
    ax.legend(fontsize=7.6, loc="lower center", ncol=4, columnspacing=.8, handlelength=1.4)
    for ax in axes.flat:
        ax.set_xticks(x, labels, fontsize=7.6)
        ax.set_xlim(-0.3, 3.3)
        ax.set_xlabel("预测起点：已观测天数", fontsize=7.6)
        ax.set_axisbelow(True)
        ax.grid(True, axis="both", color="#D9DDE1", lw=0.55, alpha=0.85)
    fig.text(
        0.5,
        0.055,
        "三种子点为训练随机性；窗口有重叠，不作为独立实验或置信区间。",
        ha="center",
        fontsize=7.6,
    )
    fig.text(
        0.5,
        0.025,
        "b：低于1优于B+；c：负值优于α；d：虚线为事前逐点80%覆盖保护。",
        ha="center",
        fontsize=7.6,
    )
    fig.canvas.draw()
    for record in records:
        xy = (
            record.pop("artist")
            .get_transform()
            .transform(np.column_stack([record["x"], record["y"]]))
        )
        xy *= 72 / fig.dpi
        xy[:, 1] = fig.get_figheight() * 72 - xy[:, 1]
        record["svg_xy"] = xy.tolist()
    write_json(out / "temporal_plot_records.json", records)
    save(fig, out / "temporal_validation", align)
    exports.append(dict(figure="temporal_validation", kind="temporal"))
    pd.DataFrame(numerical).to_csv(
        out / "figure_numbers.csv", index=False, float_format="%.17g"
    )
    write_json(
        out / "manifest.json",
        dict(
            time_utc=utc(),
            exports=exports,
            source_script_sha256=sha(Path(__file__)),
            files={p.name: sha(p) for p in sorted(out.glob("*")) if p.is_file()},
        ),
    )
    print("Exported five PNG/SVG figures", flush=True)


if __name__ == "__main__":
    main()
