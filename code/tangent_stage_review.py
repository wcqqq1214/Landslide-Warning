"""Generate auditable tangent-angle equal-speed stage review charts and CSV.

This script only provides candidate-stage evidence. It does not select,
recommend, or approve any stage automatically.
"""

import warnings
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from tangent_angle import (
    TRAIN_FRAC,
    _causal_linear_slopes,
    build_tangent_frame,
    estimate_uniform_rate,
    validate_daily_dates,
)
from warning_fusion import WARNING_STATIONS, fuse_warning_levels
from warning_thresholds import build_warning_frame

# Use a CJK-capable font for Chinese labels. Fall back to default sans if unavailable.
_CJK_CANDIDATES = ["Heiti TC", "STHeiti", "Lantinghei SC",
                   "PingFang HK", "Songti SC", "SimSong", "STFangsong"]
_available = {f.name for f in matplotlib.font_manager.fontManager.ttflist}
_cjk_font = next((f for f in _CJK_CANDIDATES if f in _available), None)
if _cjk_font:
    matplotlib.rcParams["font.family"] = _cjk_font
    matplotlib.rcParams["axes.unicode_minus"] = False
# Suppress CJK font missing-glyph warnings — fallback glyphs are used
warnings.filterwarnings("ignore", message="Glyph.*missing from font")

ROOT = Path(__file__).resolve().parent.parent
RAW_CSV = ROOT / "data" / "monitoring_data.csv"
FIG_DIR = ROOT / "figures" / "tangent_angle" / "review"
OUT_CSV = FIG_DIR / "candidate_stage_comparison.csv"

CANDIDATE_WINDOWS = (15, 30, 60)
KEY_STATION_COLS = dict(WARNING_STATIONS)
LEVEL_NAMES = ("green", "yellow", "orange", "red")
FUSION_REASONS = (
    "v0_green",
    "alpha_watch",
    "multi_station_confirmed",
    "multi_scale_confirmed",
    "v0_alpha_consistent",
    "v0_primary",
)


def _load_ordered_raw(path=RAW_CSV, date_col="Date"):
    raw = pd.read_csv(path)
    raw = raw.rename(columns=lambda c: c.strip())
    raw[date_col] = pd.to_datetime(raw[date_col])
    return raw.sort_values(date_col).reset_index(drop=True)


def _train_boundary_index(n):
    return int(n * TRAIN_FRAC)


def _build_candidate_table(dates, displacement, windows=CANDIDATE_WINDOWS):
    """Collect candidate stage parameters for each window size."""
    rows = []
    for window in windows:
        try:
            result = estimate_uniform_rate(
                dates,
                displacement,
                train_frac=TRAIN_FRAC,
                window=window,
            )
            rows.append({
                "candidate_window_days": int(window),
                **result,
            })
        except ValueError:
            rows.append({
                "candidate_window_days": int(window),
                "method": "automatic_candidate",
                "start_date": None,
                "end_date": None,
                "v_eq_mm_per_day": None,
                "rate_mad_mm_per_day": None,
                "mean_abs_accel_mm_per_day2": None,
                "n_rate_samples": None,
                "error": "无法获得正的等速阶段速率",
            })
    return pd.DataFrame(rows)


def _plot_single_station_review(dates, displacement, station, fig_path):
    """Generate a multi-panel review figure for one station."""
    dates = validate_daily_dates(dates)
    displacement = pd.Series(displacement, dtype=float).reset_index(drop=True)
    rates = displacement.diff()
    smooth_rates = _causal_linear_slopes(displacement, 3)
    accel = rates.diff()

    train_end = _train_boundary_index(len(displacement))
    train_boundary_date = dates[train_end - 1]

    candidate_table = _build_candidate_table(dates, displacement)

    fig, axes = plt.subplots(4, 1, figsize=(14, 16), sharex=True)
    fig.suptitle(
        f"{station} — 等速阶段候选复核图",
        fontsize=13,
        fontweight="bold",
    )

    ax = axes[0]
    ax.plot(dates, displacement, color="black", linewidth=0.8, label="累计位移")
    ax.axvline(train_boundary_date, color="gray", linestyle="--",
               linewidth=0.8, label=f"训练期边界 ({train_boundary_date.strftime('%Y-%m-%d')})")
    ax.set_ylabel("累计位移 (mm)")
    ax.legend(fontsize=8, loc="upper left")
    ax.set_title("(a) 全时段累计位移曲线", fontsize=10, loc="left")

    ax = axes[1]
    ax.plot(dates, rates, color="silver", linewidth=0.5, alpha=0.7, label="日位移速率 (原始)")
    smooth_valid = smooth_rates.where(np.isfinite(smooth_rates))
    ax.plot(dates, smooth_valid, color="steelblue", linewidth=0.8,
            label="3 日因果平滑速率")
    ax.axvline(train_boundary_date, color="gray", linestyle="--", linewidth=0.8)
    ax.set_ylabel("位移速率 (mm/d)")
    ax.legend(fontsize=8, loc="upper left")
    ax.set_title("(b) 日位移速率与因果平滑速率", fontsize=10, loc="left")

    ax = axes[2]
    ax.plot(dates, accel, color="darkorange", linewidth=0.5, alpha=0.6,
            label="日加速度 (原始)")
    ax.axhline(0, color="gray", linewidth=0.5, linestyle=":")
    ax.axvline(train_boundary_date, color="gray", linestyle="--", linewidth=0.8)

    colors = {15: "red", 30: "blue", 60: "green"}
    for _, row in candidate_table.iterrows():
        if pd.isna(row["start_date"]):
            continue
        w = int(row["candidate_window_days"])
        sd = pd.Timestamp(row["start_date"])
        ed = pd.Timestamp(row["end_date"])
        ax.axvspan(sd, ed, alpha=0.12, color=colors.get(w, "gray"),
                   label=f"{w} 日候选阶段")

    ax.set_ylabel("加速度 (mm/d²)")
    ax.legend(fontsize=7, loc="upper left")
    ax.set_title("(c) 日加速度与候选阶段位置", fontsize=10, loc="left")

    ax = axes[3]
    y_positions = {15: 3, 30: 2, 60: 1}
    for _, row in candidate_table.iterrows():
        if pd.isna(row["start_date"]):
            continue
        w = int(row["candidate_window_days"])
        sd = pd.Timestamp(row["start_date"])
        ed = pd.Timestamp(row["end_date"])
        y = y_positions[w]
        color = colors.get(w, "gray")
        ax.barh(y, (ed - sd).days, left=pd.Timestamp(sd), height=0.5,
                color=color, alpha=0.5, edgecolor=color)
        mid = sd + (ed - sd) / 2
        ax.text(mid, y,
                f"v_eq={row['v_eq_mm_per_day']:.4f}\n"
                f"{row['start_date']} – {row['end_date']}\n"
                f"n={int(row['n_rate_samples'])}",
                ha="center", va="center", fontsize=7,
                bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.8))

    ax.axvline(train_boundary_date, color="gray", linestyle="--", linewidth=0.8)
    ax.set_yticks([1, 2, 3])
    ax.set_yticklabels(["60 日候选窗口", "30 日候选窗口", "15 日候选窗口"], fontsize=9)
    ax.set_xlabel("日期")
    ax.set_title("(d) 自动候选阶段对比 (仅训练期数据)", fontsize=10, loc="left")

    fig.tight_layout(rect=[0, 0, 1, 0.97])
    fig_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(fig_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def _level_counts(prefix, levels):
    levels = pd.Series(levels, dtype=int)
    valid = levels[levels >= 0]
    return {
        f"{prefix}_{name}_days": int(valid.eq(level).sum())
        for level, name in enumerate(LEVEL_NAMES)
    }


def _agreement_rate(reference, current):
    reference = np.asarray(reference, dtype=int)
    current = np.asarray(current, dtype=int)
    common = (reference >= 0) & (current >= 0)
    if not common.any():
        return np.nan
    return float((reference[common] == current[common]).mean())


def build_candidate_comparison_csv(raw, stations=KEY_STATION_COLS):
    """Build candidate, tangent-level, and fusion audit fields."""
    ordered = _load_ordered_raw(raw) if isinstance(raw, (str, Path)) else raw
    if not isinstance(ordered, pd.DataFrame):
        ordered = _load_ordered_raw()
    ordered = ordered.rename(columns=lambda column: column.strip()).copy()
    ordered["Date"] = pd.to_datetime(ordered["Date"])
    ordered = ordered.sort_values("Date").reset_index(drop=True)

    all_rows = []
    tangent_frames = {}
    for window in CANDIDATE_WINDOWS:
        tangent_frame, parameters = build_tangent_frame(
            ordered,
            stations,
            candidate_window=window,
        )
        tangent_frames[window] = tangent_frame
        for station, values in parameters.items():
            all_rows.append({
                "station": station,
                "candidate_window_days": window,
                **values,
            })

    reference_frame = tangent_frames[30]
    for row in all_rows:
        station = row["station"]
        window = row["candidate_window_days"]
        levels = tangent_frames[window][f"{station}_alpha_level"]
        reference = reference_frame[f"{station}_alpha_level"]
        row.update(_level_counts("alpha", levels))
        row["alpha_agreement_rate_vs_30d"] = _agreement_rate(
            reference,
            levels,
        )

    available_warning_stations = {
        station: column
        for station, column in WARNING_STATIONS.items()
        if column in ordered
    }
    warning_frame, _ = build_warning_frame(
        ordered,
        available_warning_stations,
    )
    fusion_outputs = {}
    key_stations = tuple(stations)
    alpha_columns = [f"{station}_alpha_level" for station in key_stations]
    for window, tangent_frame in tangent_frames.items():
        merged = tangent_frame[["Date", *alpha_columns]].merge(
            warning_frame[["Date", "warning_level"]],
            on="Date",
            how="inner",
        )
        merged = merged[merged["warning_level"] >= 0].reset_index(drop=True)
        fusion_outputs[window] = fuse_warning_levels(
            merged["warning_level"],
            merged[alpha_columns],
            key_stations=key_stations,
        )

    reference_fusion = fusion_outputs[30]["final_level"]
    fusion_summaries = {}
    for window, fused in fusion_outputs.items():
        reasons = fused["fusion_reason"].value_counts()
        fusion_summaries[window] = {
            **_level_counts("fusion", fused["final_level"]),
            "fusion_upgraded_days": int(
                fused["final_level"].gt(fused["v0_level"]).sum()
            ),
            "fusion_agreement_rate_vs_30d": _agreement_rate(
                reference_fusion,
                fused["final_level"],
            ),
            **{
                f"fusion_reason_{reason}_days": int(reasons.get(reason, 0))
                for reason in FUSION_REASONS
            },
        }

    for row in all_rows:
        row.update(fusion_summaries[row["candidate_window_days"]])

    return pd.DataFrame(all_rows)


def main():
    raw = _load_ordered_raw()

    for station, disp_col in KEY_STATION_COLS.items():
        fig_path = FIG_DIR / f"{station}_stage_review.png"
        displacement = raw[disp_col]
        _plot_single_station_review(
            raw["Date"],
            displacement,
            station,
            fig_path,
        )
        print(f"[review] 复核图: {fig_path}")

    FIG_DIR.mkdir(parents=True, exist_ok=True)
    comparison = build_candidate_comparison_csv(raw)
    comparison.to_csv(OUT_CSV, index=False)
    print(f"[review] 候选阶段对比表: {OUT_CSV}")

    print("\n[review] 测点候选阶段 v_eq 对比:")
    for station in KEY_STATION_COLS:
        subset = comparison[comparison["station"] == station]
        values = []
        for _, row in subset.iterrows():
            w = row["candidate_window_days"]
            v = row["v_eq_mm_per_day"]
            if pd.notna(v):
                values.append(f"{w}d={v:.4f}")
            else:
                values.append(f"{w}d=无有效阶段")
        print(f"  {station}: {', '.join(values)}")

    print("\n[review] 重要提示:")
    print("  - 以上均为自动候选阶段，尚未经专家复核")
    print("  - 不得根据融合结果或预警表现反向选择窗口")
    print("  - 最终等速阶段需结合累计位移曲线和宏观变形资料独立确定")


if __name__ == "__main__":
    main()
