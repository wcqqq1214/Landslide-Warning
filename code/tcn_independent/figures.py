"""Chinese four-point plots from fixed, independently verified saved predictions."""

import argparse
import importlib.util
import json
from pathlib import Path
import shutil

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import numpy as np
import pandas as pd

from short_horizon.common import ROOT, save_json, sha, now
from tcn_short_horizon.figures import text_geometry
from .engine import read_spec
from .run import check_lock


def annotation(ax, text, series):
    """Keep labels clear of data, uncertainty bands and the forecast split."""
    candidates = []
    for x, start_y, ha, va, direction in [
        (0.975, 0.035, "right", "bottom", 1),
        (0.025, 0.955, "left", "top", -1),
        (0.975, 0.955, "right", "top", -1),
        (0.025, 0.035, "left", "bottom", 1),
    ]:
        candidates.extend(
            (x, start_y + direction * shift, ha, va)
            for shift in np.arange(0, 0.241, 0.015)
        )
    for x, y, ha, va in candidates:
        artist = ax.text(
            x,
            y,
            text,
            transform=ax.transAxes,
            ha=ha,
            va=va,
            fontsize=6.5,
            linespacing=1.3,
            color="#292929",
            zorder=8,
        )
        ax.figure.canvas.draw()
        box = artist.get_window_extent(ax.figure.canvas.get_renderer()).expanded(
            1.03, 1.08
        )
        bounds = ax.transData.inverted().transform([[box.x0, box.y0], [box.x1, box.y1]])
        left, bottom = bounds[0]
        right, top = bounds[1]
        collision = False
        for xs, ys in series:
            mask = (xs >= left) & (xs <= right)
            if (
                mask.any()
                and np.nanmin(ys[mask]) <= top
                and np.nanmax(ys[mask]) >= bottom
            ):
                collision = True
                break
        if not collision:
            for line in ax.lines:
                if line.get_visible() and line.get_path().transformed(
                    line.get_transform()
                ).intersects_bbox(box, filled=False):
                    collision = True
                    break
        if not collision:
            for band in ax.collections:
                if any(
                    path.transformed(band.get_transform()).intersects_bbox(
                        box, filled=True
                    )
                    for path in band.get_paths()
                ):
                    collision = True
                    break
        if not collision:
            return dict(
                location=[x, y],
                horizontal_alignment=ha,
                vertical_alignment=va,
                local_envelope_clear=True,
                data_and_reference_strokes_clear=True,
            )
        artist.remove()
    raise ValueError("No clear annotation corner; change layout before export")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--attempt", default="v1")
    parser.add_argument("--reference")
    args = parser.parse_args()
    spec = read_spec(args.config)
    root = ROOT / spec["output_root"]
    figures = ROOT / spec["figures_root"]
    out = figures / args.attempt
    out.mkdir(parents=True, exist_ok=False)
    verification = json.loads((root / "verification/receipt.json").read_text())
    assert verification["passed"]
    check_lock(root / "forecast")
    check_lock(root / "score")
    if args.reference:
        reference = figures / "reference_style.jpg"
        if not reference.exists():
            shutil.copyfile(args.reference, reference)
        assert sha(reference) == sha(args.reference)
    helper = (
        Path.home() / ".codex/skills/nature-figure/scripts/audit_panel_alignment.py"
    )
    ms = importlib.util.spec_from_file_location("independent_alignment_helper", helper)
    module = importlib.util.module_from_spec(ms)
    ms.loader.exec_module(module)
    require_matplotlib_panel_alignment = module.require_matplotlib_panel_alignment
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial Unicode MS", "DejaVu Sans"],
            "font.size": 7,
            "axes.labelsize": 7,
            "axes.titlesize": 8,
            "xtick.labelsize": 6.5,
            "ytick.labelsize": 6.5,
            "legend.fontsize": 6.5,
            "svg.fonttype": "none",
            "axes.unicode_minus": False,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.linewidth": 0.65,
            "legend.frameon": False,
            "savefig.facecolor": "white",
        }
    )
    observed = pd.read_csv(root / "score/observations.csv")
    training = pd.read_csv(root / "score/training_reconstruction.csv")
    prediction = pd.read_csv(root / "score/forecasts.csv")
    metrics = pd.read_csv(root / "score/metrics_by_point.csv").set_index(
        ["model", "point"]
    )
    cut = pd.Timestamp("2019-09-12")
    labels = {"TCN_DIRECT": "直接位移增量预测", "TCN_BRES": "B+ 残差学习"}
    blue = "#155C91"
    records = []
    for model in ["TCN_DIRECT", "TCN_BRES"]:
        fig, axes = plt.subplots(2, 2, figsize=(183 / 25.4, 128 / 25.4))
        fig.subplots_adjust(
            left=0.075, right=0.982, bottom=0.105, top=0.805, wspace=0.245, hspace=0.36
        )
        fig.suptitle(
            f"TCN {labels[model]}：四监测点训练段回代与 8:2 独立预测",
            y=0.975,
            fontsize=10,
            fontweight="bold",
        )
        fig.text(
            0.5,
            0.927,
            "前1168天训练；后293天独立预测（七天块递推，不接收预测段位移或驱动）",
            ha="center",
            fontsize=6.5,
        )
        handles = [
            Line2D([], [], color="#181818", lw=1.0, label="实测"),
            Line2D([], [], color="#7D7D7D", ls="--", lw=1, label="B+ 增量基线"),
            Patch(facecolor=blue, alpha=0.12, label="95% 预测区间"),
            Patch(facecolor=blue, alpha=0.25, label="80% 预测区间"),
            Line2D([], [], color=blue, lw=1.45, label="TCN：" + labels[model]),
        ]
        fig.legend(
            handles=handles,
            ncol=5,
            loc="center",
            bbox_to_anchor=(0.52, 0.87),
            handlelength=1.8,
            columnspacing=1.05,
        )
        panel_records = []
        for idx, (ax, point) in enumerate(zip(axes.flat, spec["points"])):
            obs = observed[observed.point == point]
            dates = pd.to_datetime(obs.date)
            offset = float(obs.display_offset_mm.iloc[0])
            actual = obs.observed_mm.to_numpy() - offset
            train = training[(training.model == model) & (training.point == point)]
            pred = prediction[(prediction.model == model) & (prediction.point == point)]
            btrain = training[
                (training.model == "B_ANCHOR") & (training.point == point)
            ]
            bpred = prediction[
                (prediction.model == "B_ANCHOR") & (prediction.point == point)
            ]
            assert len(obs) == 1461 and len(train) == 916 and len(pred) == 293
            tx = pd.to_datetime(train.date)
            px = pd.to_datetime(pred.date)
            assert px.iloc[0] == cut and px.iloc[-1] == pd.Timestamp("2020-06-30")
            ax.axvspan(dates.iloc[0], cut, facecolor="#EDF4FA", alpha=0.85, zorder=0)
            ax.axvspan(cut, dates.iloc[-1], facecolor="#FFF1E5", alpha=0.75, zorder=0)
            intervals = []
            bounds = []
            for level, alpha in [(95, 0.12), (80, 0.25)]:
                lo = pred[f"lower{level}_mm"].to_numpy() - offset
                hi = pred[f"upper{level}_mm"].to_numpy() - offset
                band = ax.fill_between(
                    px, lo, hi, color=blue, alpha=alpha, lw=0, zorder=1
                )
                vertices = band.get_paths()[0].vertices
                numeric_x = mdates.date2num(px)
                for x, lower, upper in zip(numeric_x, lo, hi):
                    values = vertices[vertices[:, 0] == x, 1]
                    assert len(values) >= 2
                    np.testing.assert_allclose(
                        [values.min(), values.max()], [lower, upper], rtol=0, atol=1e-9
                    )
                intervals.append((numeric_x, np.column_stack([lo, hi])))
                bounds.extend([lo, hi])
            allx = pd.concat([tx, px], ignore_index=True)
            allmean = np.r_[train.mean_mm.to_numpy(), pred.mean_mm.to_numpy()] - offset
            allb = np.r_[btrain.mean_mm.to_numpy(), bpred.mean_mm.to_numpy()] - offset
            observation_line = ax.plot(
                dates, actual, color="#181818", lw=1.0, zorder=4
            )[0]
            baseline_line = ax.plot(
                allx, allb, color="#7D7D7D", lw=1.0, ls="--", zorder=3
            )[0]
            model_line = ax.plot(allx, allmean, color=blue, lw=1.45, zorder=5)[0]
            for line, expected in [
                (observation_line, actual),
                (baseline_line, allb),
                (model_line, allmean),
            ]:
                np.testing.assert_array_equal(line.get_ydata(), expected)
            full = np.concatenate([actual, allmean, allb, *bounds])
            assert np.isfinite(full).all()
            low, high = float(full.min()), float(full.max())
            span = max(high - low, 1.0)
            ax.set_ylim(low - 0.085 * span, high + 0.055 * span)
            ax.set_xlim(
                dates.iloc[0] - pd.Timedelta(days=30),
                dates.iloc[-1] + pd.Timedelta(days=25),
            )
            ax.axvline(cut, color="#737373", ls=":", lw=0.8, zorder=2)
            ax.set_title(
                f"{'abcd'[idx]}  {point}", loc="left", fontweight="bold", pad=7
            )
            ax.set_ylabel("累计位移 / mm")
            ax.xaxis.set_major_locator(mdates.YearLocator())
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
            ax.grid(color="#D1D5D8", lw=0.45, alpha=0.55)
            ax.set_axisbelow(True)
            rmse = metrics.loc[(model, point), "rmse"]
            brmse = metrics.loc[("B_ANCHOR", point), "rmse"]
            series = [
                (mdates.date2num(dates), actual),
                (mdates.date2num(allx), allmean),
                (mdates.date2num(allx), allb),
                *intervals,
            ]
            placement = annotation(
                ax, f"预测 RMSE: {rmse:.2f} mm\nB+ RMSE: {brmse:.2f} mm", series
            )
            panel_records.append(
                dict(
                    point=point,
                    mean="equal average of three complete seed paths",
                    forecast_n=293,
                    training_reconstruction_n=916,
                    zero_offset_mm=offset,
                    rmse_mm=float(rmse),
                    baseline_rmse_mm=float(brmse),
                    annotation=placement,
                    complete_curve_and_80_95_band_vertices_verified=True,
                )
            )
        fig.text(
            0.5,
            0.042,
            "蓝底：训练段一步回代    |    橙底：293天独立预测；区间仅绘于预测段",
            ha="center",
            fontsize=6.5,
        )
        fig.text(
            0.5,
            0.016,
            "位移统一以首日为零点；复用固定短期TCN，完整递推期评分，未据后期结果选模",
            ha="center",
            fontsize=6.5,
            color="#555555",
        )
        geometry = text_geometry(fig)
        save_json(out / (model + ".text_geometry.json"), geometry)
        if not geometry["passed"]:
            raise ValueError(str(geometry))
        require_matplotlib_panel_alignment(
            fig,
            json_out=out / (model + ".alignment.json"),
            tolerance_pt=1.5,
            gutter_tolerance_pt=1.5,
            strict=True,
        )
        fig.savefig(out / (model + ".svg"))
        fig.savefig(out / (model + ".png"), dpi=300)
        plt.close(fig)
        records.append(dict(model=model, panels=panel_records))
    save_json(
        out / "receipt.json",
        dict(
            passed=True,
            time_utc=now(),
            script_sha256=sha(__file__),
            alignment_helper_sha256=sha(helper),
            source_csv_sha256={p.name: sha(p) for p in (root / "score").glob("*.csv")},
            figure_count=2,
            formats=["svg", "png"],
            width_mm=183,
            height_mm=128,
            dpi=300,
            visual_review="pending",
            pdf_specific_qa="not_applicable_user_requested_no_pdf",
            figures=records,
        ),
    )
    print(out / "receipt.json")


if __name__ == "__main__":
    main()
