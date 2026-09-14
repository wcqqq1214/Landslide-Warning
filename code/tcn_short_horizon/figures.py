"""Complete endpoint curves and descriptive paired-effect figures; SVG and PNG only."""

import argparse
import importlib.util
from itertools import combinations
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.text import Text
from matplotlib.ticker import FuncFormatter
import numpy as np
import pandas as pd

from short_horizon.common import ROOT, load_spec, save_json, sha


COLORS = {
    "B_ANCHOR": "#93989E",
    "DRIFT1": "#596E8A",
    "RR_DIRECT": "#17816E",
    "TCN_DIRECT": "#2767A8",
    "TCN_BRES": "#C47732",
}
LABELS = {
    "B_ANCHOR": "B+ anchor",
    "DRIFT1": "Drift 1 day",
    "RR_DIRECT": "Ridge direct",
    "TCN_DIRECT": "TCN direct",
    "TCN_BRES": "TCN B+ residual",
}
STYLES = {
    "B_ANCHOR": "--",
    "DRIFT1": ":",
    "RR_DIRECT": "-.",
    "TCN_DIRECT": "-",
    "TCN_BRES": "-",
}


def text_geometry(fig):
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    # Axis.draw omits tick artists outside its view interval even though their
    # Text.get_visible() flags remain true. Do not audit those undrawn labels.
    undrawn_ticks = set()
    for ax in fig.axes:
        for axis in (ax.xaxis, ax.yaxis):
            low, high = sorted(axis.get_view_interval())
            for tick in [*axis.get_major_ticks(), *axis.get_minor_ticks()]:
                if not low <= tick.get_loc() <= high:
                    undrawn_ticks.update((id(tick.label1), id(tick.label2)))
    texts = []
    for text in fig.findobj(Text):
        if (
            id(text) not in undrawn_ticks
            and text.get_visible()
            and text.get_text().strip()
        ):
            box = text.get_window_extent(renderer)
            if box.width > 0 and box.height > 0:
                texts.append((text.get_text(), box, text.get_fontsize()))
    collisions = []
    for (a, ab, _), (b, bb, _) in combinations(texts, 2):
        width = min(ab.x1, bb.x1) - max(ab.x0, bb.x0)
        height = min(ab.y1, bb.y1) - max(ab.y0, bb.y0)
        if width > 1.0 and height > 1.0:
            collisions.append([a, b])
    clipped = [
        t
        for t, b, _ in texts
        if b.x0 < -0.5
        or b.y0 < -0.5
        or b.x1 > fig.bbox.x1 + 0.5
        or b.y1 > fig.bbox.y1 + 0.5
    ]
    return dict(
        passed=not collisions and not clipped and min(s for _, _, s in texts) >= 6,
        text_count=len(texts),
        excluded_undrawn_tick_artists=len(undrawn_ticks),
        minimum_font_pt=min(s for _, _, s in texts),
        text_text_collisions=collisions,
        canvas_clipping=clipped,
        boundary="Matplotlib text rectangles; PDF audits not run; data-stroke overlap reviewed visually",
    )


def save(fig, out, name, require_matplotlib_panel_alignment):
    geometry = text_geometry(fig)
    save_json(out / (name + ".text_geometry.json"), geometry)
    if not geometry["passed"]:
        raise ValueError(f"Figure text geometry failed: {name}: {geometry}")
    require_matplotlib_panel_alignment(
        fig,
        json_out=out / (name + ".alignment.json"),
        tolerance_pt=1.5,
        gutter_tolerance_pt=1.5,
        strict=True,
    )
    fig.savefig(out / (name + ".svg"))
    fig.savefig(out / (name + ".png"), dpi=300)
    plt.close(fig)


def canvas(title, height=150):
    width_mm = 183
    fig, axes = plt.subplots(2, 2, figsize=(width_mm / 25.4, height / 25.4))
    fig.subplots_adjust(
        left=0.12, right=0.975, bottom=0.20, top=0.87, wspace=0.36, hspace=0.48
    )
    fig.suptitle(title, y=0.96, fontsize=9, fontweight="bold")
    return fig, axes


def method_legend(fig, include_observed=False):
    handles = []
    if include_observed:
        handles.append(Line2D([], [], color="#17191D", lw=1, label="Observed"))
    handles += [
        Line2D([], [], color=COLORS[n], ls=STYLES[n], lw=1.2, label=LABELS[n])
        for n in COLORS
    ]
    fig.legend(
        handles=handles,
        loc="lower center",
        bbox_to_anchor=(0.52, 0.02),
        ncol=3,
        columnspacing=1.6,
        handlelength=2.4,
        fontsize=7,
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--attempt", default="v1")
    args = p.parse_args()
    spec = load_spec(args.config)
    root = ROOT / spec["output_root"]
    out = ROOT / spec["figures_root"] / args.attempt
    out.mkdir(parents=True, exist_ok=False)
    helper = (
        Path.home() / ".codex/skills/nature-figure/scripts/audit_panel_alignment.py"
    )
    module_spec = importlib.util.spec_from_file_location("tcn_alignment_helper", helper)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    align = module.require_matplotlib_panel_alignment
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial", "DejaVu Sans"],
            "font.size": 7,
            "axes.labelsize": 7,
            "xtick.labelsize": 6.5,
            "ytick.labelsize": 6.5,
            "legend.fontsize": 7,
            "svg.fonttype": "none",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.linewidth": 0.6,
            "legend.frameon": False,
            "savefig.facecolor": "white",
        }
    )
    source = root / "analysis"
    summary = pd.read_csv(source / "summary_by_horizon.csv")
    curves = pd.read_csv(source / "forecasts_long.csv", parse_dates=["target_date"])
    pair = pd.read_csv(source / "comparisons_by_point.csv")
    fig, axes = canvas("Small TCN: comparison across seven forecast horizons")
    for col, (phase, label) in enumerate(
        (("development", "Development"), ("later_exploratory", "Later exploratory"))
    ):
        for row, (metric, ylabel) in enumerate(
            (("rmse", "RMSE (mm, log scale)"), ("crps", "CRPS (mm, log scale)"))
        ):
            ax = axes[row, col]
            ax.set_title(f"{'abcd'[row * 2 + col]}  {label}", loc="left", fontsize=8)
            for name in spec["models"]:
                d = summary[
                    (summary.phase == phase) & (summary.model == name)
                ].sort_values("horizon")
                assert len(d) == 7 and np.all(d[metric].to_numpy() > 0)
                ax.plot(
                    d.horizon,
                    d[metric],
                    color=COLORS[name],
                    ls=STYLES[name],
                    lw=1.2,
                    marker="o"
                    if name == "TCN_DIRECT"
                    else "s"
                    if name == "TCN_BRES"
                    else None,
                    ms=2.5,
                )
            ax.set_yscale("log")
            ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
            ax.set_xticks(range(1, 8))
            ax.set_xlabel("Forecast horizon (days)")
            ax.set_ylabel(ylabel)
    method_legend(fig)
    save(fig, out, "overview", align)
    paired = pair[(pair.candidate == "TCN_BRES") & (pair.reference == "TCN_DIRECT")]
    point_colors = dict(
        zip(spec["points"], ["#2767A8", "#C47732", "#17816E", "#9166A4"])
    )
    fig, axes = canvas(
        "B+ residual output: gains in mean can accompany interval losses"
    )
    for col, (phase, label) in enumerate(
        (("development", "Development"), ("later_exploratory", "Later exploratory"))
    ):
        for row, (metric, ylabel) in enumerate(
            (
                ("rmse_difference", "BRES - direct RMSE (mm)"),
                ("interval_score90_difference", "BRES - direct interval score (mm)"),
            )
        ):
            ax = axes[row, col]
            ax.set_title(f"{'abcd'[row * 2 + col]}  {label}", loc="left", fontsize=8)
            ax.axhline(0, color="#41444A", ls="--", lw=0.7)
            for point in spec["points"]:
                d = paired[
                    (paired.phase == phase) & (paired.point == point)
                ].sort_values("horizon")
                ax.plot(
                    d.horizon,
                    d[metric],
                    color=point_colors[point],
                    marker="o",
                    ms=2.5,
                    lw=1,
                )
            ax.set_xticks(range(1, 8))
            ax.set_xlabel("Forecast horizon (days)")
            ax.set_ylabel(ylabel)
    fig.legend(
        handles=[
            Line2D([], [], color=c, marker="o", lw=1, ms=3, label=p)
            for p, c in point_colors.items()
        ],
        loc="lower center",
        bbox_to_anchor=(0.52, 0.04),
        ncol=4,
        fontsize=7,
    )
    save(fig, out, "residual_pair", align)
    line_checks = 0
    for phase, label in (
        ("development", "Development"),
        ("later_exploratory", "Later exploratory"),
    ):
        for h in range(1, 8):
            n = spec["stages"][phase][1] - spec["stages"][phase][0] - h + 1
            fig, axes = canvas(
                f"{label}: day {h} displacement forecasts (n = {n} per point)", 160
            )
            for j, (ax, point) in enumerate(zip(axes.flat, spec["points"])):
                d = curves[
                    (curves.phase == phase)
                    & (curves.horizon == h)
                    & (curves.point == point)
                ]
                truth = d[d.model == "B_ANCHOR"].sort_values("origin")
                assert len(truth) == n
                for name in ("TCN_DIRECT", "TCN_BRES"):
                    part = d[d.model == name].sort_values("origin")
                    ax.fill_between(
                        part.target_date,
                        part.lower90_mm,
                        part.upper90_mm,
                        color=COLORS[name],
                        alpha=0.12,
                        lw=0,
                    )
                for name in spec["models"]:
                    part = d[d.model == name].sort_values("origin")
                    assert len(part) == n and np.array_equal(part.origin, truth.origin)
                    (line,) = ax.plot(
                        part.target_date,
                        part.mean_mm,
                        color=COLORS[name],
                        ls=STYLES[name],
                        lw=0.9,
                    )
                    np.testing.assert_array_equal(
                        line.get_ydata(), part.mean_mm.to_numpy()
                    )
                    line_checks += 1
                ax.plot(
                    truth.target_date,
                    truth.observed_mm,
                    color="#17191D",
                    lw=0.65,
                    alpha=0.8,
                )
                ax.set_title(f"{'abcd'[j]}  {point}", loc="left", fontsize=8)
                ax.set_ylabel("Displacement (mm)")
                ax.xaxis.set_major_locator(
                    mdates.AutoDateLocator(minticks=3, maxticks=4)
                )
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
                ax.tick_params(axis="x", pad=7)
                ax.set_xlabel("Target date")
            method_legend(fig, True)
            save(fig, out, f"{phase}_h{h}", align)
    save_json(
        out / "receipt.json",
        dict(
            passed=True,
            figure_count=16,
            curve_lines_checked=line_checks,
            formats=["svg", "png"],
            dpi=300,
            no_pdf_generated=True,
            source_sha256={
                p.name: sha(p)
                for p in [
                    source / "summary_by_horizon.csv",
                    source / "forecasts_long.csv",
                    source / "comparisons_by_point.csv",
                ]
            },
            script_sha256=sha(__file__),
            alignment_helper_sha256=sha(helper),
            intervals="90% empirical Gaussian marginal prediction intervals on TCN curves",
            visual_review="pending",
            pdf_specific_audits="not_run_user_requested_no_pdf",
        ),
    )
    print(out)


if __name__ == "__main__":
    main()
