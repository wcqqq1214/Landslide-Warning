"""Three mentor-style figures; complete saved trajectories, no PDF output."""

import argparse
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

from tcn_short_horizon.figures import text_geometry
from .core import ROOT, load_npz, read_json, read_labels, sha, spec, utc, write_json


COLORS = {
    "BPLUS_CONTINUOUS": "#777777",
    "DRIFT1": "#B17C46",
    "RR_COND": "#8A7FAD",
    "TCN_DIRECT_COND": "#246592",
    "TCN_BRES_COND": "#308B85",
}
NAMES = {
    "BPLUS_CONTINUOUS": "改进 B+",
    "DRIFT1": "DRIFT1",
    "RR_COND": "普通岭回归",
    "TCN_DIRECT_COND": "TCN 直接",
    "TCN_BRES_COND": "TCN 残差",
}
DOMAINS = ("O3", "O2", "O1-up", "O1-down")


def canvas(title, subtitle):
    fig, axes = plt.subplots(2, 2, figsize=(240 / 25.4, 170 / 25.4))
    fig.subplots_adjust(
        left=0.085, right=0.975, bottom=0.165, top=0.805, wspace=0.22, hspace=0.50
    )
    fig.suptitle(title, y=0.966, fontsize=12, fontweight="bold")
    fig.text(0.5, 0.924, subtitle, ha="center", fontsize=8.2, color="#444444")
    return fig, axes


def save(fig, path, alignment):
    geometry = text_geometry(fig)
    write_json(path.with_suffix(".text_geometry.json"), geometry)
    if not geometry["passed"]:
        raise ValueError("Text geometry failed: " + str(geometry))
    alignment(
        fig,
        json_out=path.with_suffix(".alignment.json"),
        tolerance_pt=1.5,
        gutter_tolerance_pt=1.5,
        strict=True,
    )
    # Every text box must also be clear of all data strokes and interval edges.
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    collisions = []
    for ax in fig.axes:
        for text in ax.texts:
            box = text.get_window_extent(renderer)
            for line in ax.lines:
                if (
                    line.get_path()
                    .transformed(line.get_transform())
                    .intersects_bbox(box, filled=False)
                ):
                    collisions.append(text.get_text())
    if collisions:
        raise ValueError("Data stroke crossed annotation: " + str(collisions))
    fig.savefig(path.with_suffix(".svg"))
    fig.savefig(path.with_suffix(".png"), dpi=300)
    plt.close(fig)


def main(attempt):
    cfg = spec()
    root = ROOT / cfg["out"]
    audit = read_json(root / "verification_v1/receipt.json")
    assert audit["status"] == "passed"
    out = ROOT / cfg["figures"] / attempt
    out.mkdir(parents=True, exist_ok=False)
    helper = (
        Path.home() / ".codex/skills/nature-figure/scripts/audit_panel_alignment.py"
    )
    loader = importlib.util.spec_from_file_location(
        "conditional_alignment_helper", helper
    )
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
    directory = root / "final_exploratory"
    data = load_npz(root / "implementation_verification/teacher_1168.npz")
    issue = load_npz(directory / "issued_distribution.npz")
    selected = read_json(root / "internal_selection.json")["selected_updates"]
    ensemble = load_npz(directory / f"ensemble_e{selected}.npz")
    metrics = pd.read_csv(directory / "metrics_by_point.csv").set_index(
        ["model", "point"]
    )
    y = read_labels(ROOT / cfg["data"], 1461)
    y0 = y[0]
    dates = pd.to_datetime(data["dates"])
    split = dates[1168] - pd.Timedelta(hours=12)
    figure_rows = []
    source = {
        "dates": data["dates"],
        "y0": y0,
        "observed": y,
        "BPLUS_CONTINUOUS": data["mean"],
    }
    for arm in cfg["arms"]:
        caption = "直接预测" if arm == cfg["arms"][0] else "B+ 残差学习"
        fig, axes = canvas(
            f"TCN {caption}：四点训练拟合与 8:2 独立条件预测",
            "前 1168 日从头训练｜后 293 日一次输出｜给定逐日降雨与库水位，无实测位移反馈",
        )
        color = COLORS[arm]
        handles = [
            Line2D([], [], color="#161616", lw=1.15, label="实测"),
            Line2D(
                [], [], color=COLORS["BPLUS_CONTINUOUS"], lw=1, ls="--", label="改进 B+"
            ),
            Patch(facecolor=color, alpha=0.12, label="95% 预测区间"),
            Patch(facecolor=color, alpha=0.24, label="80% 预测区间"),
            Line2D([], [], color=color, lw=1.2, label=NAMES[arm]),
        ]
        fig.legend(
            handles=handles,
            loc="upper center",
            bbox_to_anchor=(0.5, 0.889),
            ncol=5,
            fontsize=8.2,
            handlelength=2.2,
            columnspacing=1.8,
        )
        mu = ensemble[arm]
        sd = issue[arm + "__sigma"]
        source[arm] = mu
        source[arm + "__sigma"] = sd
        for p, (ax, point) in enumerate(zip(axes.flat, cfg["points"])):
            ax.axvspan(dates[0], split, color="#F0F6FA", zorder=0)
            ax.axvspan(split, dates[-1], color="#FFF3E9", zorder=0)
            for level, z, alpha in (
                (95, 1.959963984540054, 0.12),
                (80, 1.2815515655446004, 0.24),
            ):
                lo, hi = (
                    mu[1168:, p] - y0[p] - z * sd[p],
                    mu[1168:, p] - y0[p] + z * sd[p],
                )
                band = ax.fill_between(
                    dates[1168:], lo, hi, color=color, alpha=alpha, lw=0, zorder=1
                )
                band.set_gid(f"band_{arm}_{point}_{level}")
            ax.plot(
                dates,
                y[:, p] - y0[p],
                color="#161616",
                lw=1.05,
                zorder=3,
                gid=f"observed_{point}",
            )
            ax.plot(
                dates,
                data["mean"][:, p] - y0[p],
                color=COLORS["BPLUS_CONTINUOUS"],
                lw=1,
                ls="--",
                zorder=2,
                gid=f"bplus_{point}",
            )
            ax.plot(
                dates,
                mu[:, p] - y0[p],
                color=color,
                lw=1.15,
                zorder=4,
                gid=f"model_{arm}_{point}",
            )
            ax.axvline(split, color="#777777", ls=":", lw=0.85, zorder=2)
            ax.set_title(
                f"{point} | {DOMAINS[p]}", loc="left", fontweight="bold", pad=7
            )
            ax.set_ylabel("累计位移 / mm")
            ax.set_xlim(
                dates[0] - pd.Timedelta(days=20), dates[-1] + pd.Timedelta(days=20)
            )
            ax.xaxis.set_major_locator(mdates.YearLocator())
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
            model_rmse = metrics.loc[(arm, point), "rmse"]
            b_rmse = metrics.loc[("BPLUS_CONTINUOUS", point), "rmse"]
            ax.text(
                0,
                -0.215,
                f"预测 RMSE：TCN {model_rmse:.2f} mm；B+ {b_rmse:.2f} mm",
                transform=ax.transAxes,
                ha="left",
                va="top",
                fontsize=7.9,
            )
            figure_rows.append(
                dict(
                    figure=arm,
                    point=point,
                    model_rmse=model_rmse,
                    bplus_rmse=b_rmse,
                    fitted_days=1168,
                    predicted_days=293,
                )
            )
        fig.text(
            0.5,
            0.075,
            "蓝色背景：训练段拟合；橙色背景：完整 293 日预测；位移以原始第一日为零点。",
            ha="center",
            fontsize=7.8,
        )
        fig.text(
            0.5,
            0.044,
            "主线为三个种子的等权均值；区间由此前 90 条已兑现误差校准。当前为探索性评价。",
            ha="center",
            fontsize=7.6,
        )
        save(fig, out / arm, align)
    fig, axes = canvas(
        "四点完整 293 日预测：TCN 与物理、简单及岭回归对照",
        "所有方法使用同一预测日期｜均值对比；两版 TCN 的概率区间见对应训练/预测图",
    )
    handles = [Line2D([], [], color="#111111", lw=1.2, label="实测")]
    handles += [
        Line2D(
            [],
            [],
            color=COLORS[m],
            lw=1.15,
            ls="--" if m == "BPLUS_CONTINUOUS" else "-",
            label=NAMES[m],
        )
        for m in cfg["methods"]
    ]
    fig.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.889),
        ncol=6,
        fontsize=8.2,
        handlelength=2,
        columnspacing=1.5,
    )
    future = dates[1168:]
    for p, (ax, point) in enumerate(zip(axes.flat, cfg["points"])):
        ax.plot(future, y[1168:, p] - y0[p], color="#111111", lw=1.2, zorder=5)
        for m in cfg["methods"]:
            mu = issue[m + "__mean"][:, p] - y0[p]
            ax.plot(
                future,
                mu,
                color=COLORS[m],
                lw=1.15,
                ls="--" if m == "BPLUS_CONTINUOUS" else "-",
                gid=f"comparison_{m}_{point}",
            )
        ax.set_title(f"{point} | {DOMAINS[p]}", loc="left", fontweight="bold", pad=7)
        ax.set_ylabel("累计位移 / mm")
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax.set_xlim(future[0] - pd.Timedelta(days=4), future[-1] + pd.Timedelta(days=4))
        ax.text(
            0,
            -0.215,
            "2019-09-12—2020-06-30；完整 293 日，未截尾",
            transform=ax.transAxes,
            fontsize=7.8,
            va="top",
        )
    fig.text(
        0.5,
        0.075,
        "给定逐日降雨与库水位；预测期无实测位移反馈；保留全部方法与日期。",
        ha="center",
        fontsize=7.8,
    )
    fig.text(
        0.5,
        0.044,
        "颜色对应固定方法；TCN 主线为三个种子均值。指标及概率区间详见配套 CSV。",
        ha="center",
        fontsize=7.6,
    )
    save(fig, out / "all_methods_forecast", align)
    exported = dict(source)
    for key, value in issue.items():
        if key == "dates":
            continue
        if key in exported:
            np.testing.assert_array_equal(exported[key], value)
        exported[key] = value
    np.savez_compressed(out / "source_arrays.npz", **exported)
    pd.DataFrame(figure_rows).to_csv(
        out / "figure_numbers.csv", index=False, float_format="%.17g"
    )
    write_json(
        out / "manifest.json",
        dict(
            time_utc=utc(),
            backend="python",
            attempt=attempt,
            model_audit_sha256=sha(root / "verification_v1/receipt.json"),
            source_script_sha256=sha(Path(__file__)),
            files={p.name: sha(p) for p in sorted(out.glob("*")) if p.is_file()},
            pdf="not generated as requested; PDF-only audits not run",
        ),
    )
    print(str(out), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", default="v1")
    main(parser.parse_args().attempt)
