"""Four frozen diagnostic figures sourced only from verified CSVs."""

import argparse
import importlib.util
from pathlib import Path
import tempfile
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np
import pandas as pd
from tcn_short_horizon.figures import text_geometry
from . import core as c

o = c.o
COLORS = ["#0072B2", "#D55E00", "#8060A0", "#008477"]


def main(attempt):
    cfg = c.spec()
    c.guard()
    o.check_deadline(cfg)
    root = c.ROOT / cfg["out"]
    o.verify_lock(root / "audit_v1/lock.json")
    o.verify_lock(root / "diagnostic_v1/lock.json")
    out = c.ROOT / cfg["figures"] / attempt
    out.mkdir(parents=True, exist_ok=False)
    qa = Path(tempfile.mkdtemp(prefix="ootang-kin-diagnostic-qa-"))
    spec = importlib.util.spec_from_file_location(
        "kin_alignment",
        Path.home() / ".codex/skills/nature-figure/scripts/audit_panel_alignment.py",
    )
    alignment = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(alignment)
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial Unicode MS", "DejaVu Sans"],
            "font.size": 8,
            "axes.labelsize": 8,
            "axes.titlesize": 9,
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
    tables = {}
    records, exports = [], []

    def recipe(table, filters, column, order, metric, multiplier=1):
        return dict(
            table=table,
            filters=filters,
            order_column=column,
            order=order,
            metric=metric,
            multiplier=multiplier,
        )

    def values(r):
        if r["table"] not in tables:
            tables[r["table"]] = pd.read_csv(
                root / "diagnostic_v1" / (r["table"] + ".csv"),
                float_precision="round_trip",
                dtype={"seed": str},
            )
        frame = tables[r["table"]]
        for key, value in r["filters"].items():
            frame = frame[
                frame[key].isin(value if isinstance(value, list) else [value])
            ]
        result = []
        for v in r["order"]:
            a = frame.loc[frame[r["order_column"]] == v, r["metric"]].to_numpy(float)
            assert len(a) and np.isfinite(a).all(), r
            result.append(float(a.mean()) * r["multiplier"])
        return np.asarray(result)

    def canvas(title, subtitle):
        fig, axes = plt.subplots(2, 2, figsize=(240 / 25.4, 170 / 25.4))
        fig.subplots_adjust(
            left=0.10, right=0.974, bottom=0.18, top=0.765, wspace=0.29, hspace=0.64
        )
        fig.suptitle(title, y=0.972, fontsize=12, fontweight="bold")
        fig.text(0.5, 0.932, subtitle, ha="center", fontsize=8, color="#444444")
        return fig, axes.ravel()

    def setup(ax, p, title, xlabel, ylabel):
        ax.set_title(f"{chr(97 + p)}  {title}", loc="left", fontweight="bold", pad=7)
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.grid(axis="y", color="#dddddd", lw=0.5)

    def line(fig, ax, name, x, r, **style):
        y = values(r)
        gid = f"{name}_series_{len(records)}"
        ax.plot(x, y, gid=gid, **style)
        records.append(
            dict(
                figure=name,
                gid=gid,
                axes=fig.axes.index(ax),
                x=np.asarray(x, float).tolist(),
                y=y.tolist(),
                recipe=r,
                marker_only=style.get("ls") == "None",
            )
        )

    def footer(fig, first, second):
        fig.text(0.5, 0.078, first, ha="center", fontsize=7.2)
        fig.text(0.5, 0.041, second, ha="center", fontsize=7.2, color="#444444")

    def legend(fig, handles, ncol):
        fig.legend(
            handles=handles,
            loc="upper center",
            bbox_to_anchor=(0.5, 0.898),
            ncol=ncol,
            fontsize=7.8,
        )

    def finish(fig, name):
        fig.canvas.draw()
        geo = text_geometry(fig)
        o.write_json(out / f"{name}.text_geometry.json", geo)
        assert geo["passed"], geo
        alignment.require_matplotlib_panel_alignment(
            fig,
            json_out=out / f"{name}.alignment.json",
            tolerance_pt=1.5,
            gutter_tolerance_pt=1.5,
            strict=True,
        )
        for rec in [r for r in records if r["figure"] == name]:
            ax = fig.axes[rec["axes"]]
            v = np.column_stack([rec["x"], rec["y"]])
            rec["xlim"], rec["ylim"] = list(ax.get_xlim()), list(ax.get_ylim())
            assert np.all(
                (v[:, 0] >= rec["xlim"][0])
                & (v[:, 0] <= rec["xlim"][1])
                & (v[:, 1] >= rec["ylim"][0])
                & (v[:, 1] <= rec["ylim"][1])
            )
            xy = ax.transData.transform(v)
            rec["svg_xy"] = np.column_stack(
                [xy[:, 0] * 72 / fig.dpi, (fig.bbox.height - xy[:, 1]) * 72 / fig.dpi]
            ).tolist()
        fig.savefig(out / f"{name}.svg")
        fig.savefig(out / f"{name}.png", dpi=300)
        fig.savefig(qa / f"{name}.pdf")
        plt.close(fig)
        exports.append(
            dict(
                name=name,
                png=f"{name}.png",
                svg=f"{name}.svg",
                qa_pdf=str(qa / f"{name}.pdf"),
                panels=4,
            )
        )

    titles = [
        "612日：启动诊断",
        "792日：历史窗1",
        "972日：历史窗2",
        "1168日：最终探索窗",
    ]
    name = "supervision_weights"
    fig, axes = canvas(
        "TiDE KIN：远距离实际获得多少训练权重？",
        "原抽样日程精确复算 | 黑线为所有合法起点的理论期望，彩线为原400次更新",
    )
    legend(
        fig,
        [Line2D([], [], color="#111111", lw=1.5, label="理论期望")]
        + [
            Line2D([], [], color=COLORS[s], lw=0.9, label=f"种子 {s}")
            for s in cfg["seeds"]
        ]
        + [Line2D([], [], color="#888888", ls=":", label="各距离均匀参考 = 1")],
        5,
    )
    for p, (n, ax) in enumerate(zip(cfg["origins"], axes)):
        setup(ax, p, titles[p], "预测距离 / 日", "293 × 距离损失系数")
        for s in cfg["seeds"]:
            line(
                fig,
                ax,
                name,
                np.arange(1, 294),
                recipe(
                    "supervision_weights",
                    {"origin": n, "seed": str(s), "step": 400},
                    "horizon",
                    list(range(1, 294)),
                    "actual_loss_weight",
                    293,
                ),
                color=COLORS[s],
                lw=0.8,
                alpha=0.8,
            )
        line(
            fig,
            ax,
            name,
            np.arange(1, 294),
            recipe(
                "supervision_weights",
                {"origin": n, "seed": "0", "step": 400},
                "horizon",
                list(range(1, 294)),
                "expected_loss_weight",
                293,
            ),
            color="#111111",
            lw=1.3,
        )
        ax.axhline(1, color="#888888", ls=":", lw=0.7)
        ax.set_xlim(0, 300)
        ax.set_xticks([1, 90, 180, 293])
        ax.set_ylim(bottom=-0.1)
    footer(
        fig,
        "每个起点先对自身已成熟步长取均值，因此样本数与损失系数不是同一概念。",
        "系数不等于梯度大小；相邻起点不是独立重复。612日前缀没有181—293日直接监督。",
    )
    finish(fig, name)

    name = "error_by_distance"
    fig, axes = canvas(
        "TiDE KIN：误差并非只在远期出现",
        "三条原发报完整293日路径按预定距离分段 | 四点分别显示，未删除尾段",
    )
    pc = [COLORS[1], COLORS[2], COLORS[0]]
    styles = {"TiDE_KIN": "-", "BPLUS_CONTINUOUS": "--", "DRIFT1": ":"}
    legend(
        fig,
        [
            Line2D([], [], color=color, lw=1.6, label=lab)
            for color, lab in zip(pc, ["792日历史窗1", "972日历史窗2", "1168日最终窗"])
        ]
        + [
            Line2D([], [], color="#444444", ls=style, label=lab)
            for lab, style in [("TiDE KIN", "-"), ("改进 B+", "--"), ("DRIFT1", ":")]
        ],
        3,
    )
    bins = [b["name"] for b in cfg["bins"][:-1]]
    for p, (point, ax) in enumerate(zip(cfg["points"], axes)):
        setup(ax, p, point, "预测距离段 / 日", "逐点集成 RMSE / mm")
        for n, color in zip(cfg["primary_origins"], pc):
            for method, style in styles.items():
                line(
                    fig,
                    ax,
                    name,
                    np.arange(4),
                    recipe(
                        "issued_by_point",
                        {
                            "origin": n,
                            "method": method,
                            "point": point,
                            "seed": "ensemble",
                        },
                        "bin",
                        bins,
                        "rmse",
                    ),
                    color=color,
                    ls=style,
                    lw=1.7 if method == "TiDE_KIN" else 1.0,
                    marker="o" if method == "TiDE_KIN" else None,
                    ms=3,
                    alpha=1 if method == "TiDE_KIN" else 0.85,
                )
        ax.set_xticks(range(4), ["1—30", "31—90", "91—180", "181—293"])
        ax.set_xlim(-0.12, 3.12)
        ax.set_ylim(bottom=0)
    footer(
        fig,
        "曲线为原三种子均值预测的误差，未重选种子；完整六方法及各种子结果见CSV。",
        "各点纵轴范围随数值调整；窗间重叠且日期已暴露，均为探索性比较，不作显著性或因果结论。",
    )
    finish(fig, name)

    name = "checkpoint_fit"
    fig, axes = canvas(
        "TiDE KIN：训练拟合改善，跨时段收益仍不一致",
        "全部原检查点重放，无新训练、无重选 | 实线使用同一汇总口径，集成另列",
    )
    legend(
        fig,
        [
            Line2D([], [], color=COLORS[0], label="训练拟合：种子平均"),
            Line2D([], [], color=COLORS[1], label="原发报：种子平均"),
            Line2D([], [], color="#111111", ls="--", label="原发报：集成"),
            Line2D(
                [], [], color="#666666", marker="x", ls="None", label="散点：全部种子"
            ),
        ],
        4,
    )
    for p, (n, ax) in enumerate(zip(cfg["origins"], axes)):
        setup(ax, p, titles[p], "原检查点 / 更新次数", "四点平均 RMSE / mm")
        for table, color in [
            ("training_summary", COLORS[0]),
            ("checkpoint_forecast_summary", COLORS[1]),
        ]:
            for s, marker in zip(cfg["seeds"], ["o", "x", "+"]):
                line(
                    fig,
                    ax,
                    name,
                    cfg["steps"],
                    recipe(
                        table,
                        {"origin": n, "seed": str(s), "bin": "all"},
                        "step",
                        cfg["steps"],
                        "rmse",
                    ),
                    color=color,
                    ls="None",
                    marker=marker,
                    ms=3.5,
                    alpha=0.4,
                )
            line(
                fig,
                ax,
                name,
                cfg["steps"],
                recipe(
                    table,
                    {"origin": n, "seed": ["0", "1", "2"], "bin": "all"},
                    "step",
                    cfg["steps"],
                    "rmse",
                ),
                color=color,
                lw=1.5,
            )
        line(
            fig,
            ax,
            name,
            cfg["steps"],
            recipe(
                "checkpoint_forecast_summary",
                {"origin": n, "seed": "ensemble", "bin": "all"},
                "step",
                cfg["steps"],
                "rmse",
            ),
            color="#111111",
            lw=1.1,
            ls="--",
        )
        ax.set_xlim(-12, 412)
        ax.set_xticks(cfg["steps"])
        ax.set_ylim(bottom=0)
    footer(
        fig,
        "训练只计各起点已成熟单元；原发报评价完整293日。两者日期/样本集合不同，不能把差值当因果效应。",
        "训练与发报实线：先平均四点RMSE，再平均三种子；黑虚线：先平均预测，再计算四点RMSE均值。",
    )
    finish(fig, name)

    name = "visibility_sensitivity"
    fig, axes = canvas(
        "TiDE KIN：晚期情景输入会影响共同早期输出",
        "固定原e400权重与位移历史 | 只改变未来驱动/K的可见距离，不评分或选优",
    )
    legend(
        fig,
        [
            Line2D([], [], color=color, label=point)
            for color, point in zip(COLORS, cfg["points"])
        ]
        + [
            Line2D(
                [], [], color="#666666", marker="x", ls="None", label="散点：全部种子"
            )
        ],
        5,
    )
    for p, (n, ax) in enumerate(zip(cfg["origins"], axes)):
        setup(
            ax, p, titles[p], "未来协变量可见距离 d / 日", "共同 h1…d 预测变化 RMS / mm"
        )
        for point, color in zip(cfg["points"], COLORS):
            for s, marker in zip(cfg["seeds"], ["o", "x", "+"]):
                line(
                    fig,
                    ax,
                    name,
                    cfg["visibility"],
                    recipe(
                        "visibility_sensitivity",
                        {"origin": n, "point": point, "seed": str(s)},
                        "visible",
                        cfg["visibility"],
                        "rms_change",
                    ),
                    color=color,
                    ls="None",
                    marker=marker,
                    ms=3.3,
                    alpha=0.38,
                )
            line(
                fig,
                ax,
                name,
                cfg["visibility"],
                recipe(
                    "visibility_sensitivity",
                    {"origin": n, "point": point, "seed": "ensemble"},
                    "visible",
                    cfg["visibility"],
                    "rms_change",
                ),
                color=color,
                lw=1.3,
                marker="o",
                ms=3,
            )
        ax.set_xlim(18, 305)
        ax.set_xticks(cfg["visibility"])
        ax.set_ylim(bottom=-0.5)
    footer(
        fig,
        "相对完整293日输入计算输出变化；这不是对实测的RMSE，不说明掩码方案更好或存在标签泄露。",
        "实线是集成预测变化；d=293为零变化对照。不同d平均不同日期集合，不宜比较为单调剂量效应。",
    )
    finish(fig, name)

    assert len(records) == 152
    o.write_json(out / "plot_records.json", records)
    o.write_json(
        out / "exports.json",
        dict(
            figures=exports,
            source_code_sha256=o.sha(Path(__file__)),
            source_tables={
                k: o.sha(root / "diagnostic_v1" / (k + ".csv")) for k in tables
            },
            contract_sha256=o.sha(
                c.ROOT / "docs/ootang_tide_kin_diagnostic_figure_contract.v1.0.md"
            ),
        ),
    )
    o.lock(
        out,
        "artifact_lock.json",
        list(out.glob("*")),
        status="rendered awaiting independent and manual QA",
    )
    print(out, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", default="v1")
    main(parser.parse_args().attempt)
