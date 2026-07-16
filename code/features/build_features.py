"""Feature engineering for displacement, reservoir level and rainfall drivers."""
from pathlib import Path
import sys

import pandas as pd

CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from features.tangent_angle import (  # noqa: E402
    build_tangent_frame,
    load_reference_stages,
    uniform_rate_rows,
)
from features.kinematics import (  # noqa: E402
    build_long_kinematics,
    compute_point_kinematics,
    summarize_kinematics,
)

ROOT = Path(__file__).resolve().parents[2]
DATA_CSV = ROOT / "data" / "monitoring_data.csv"
OUT_CSV = ROOT / "data" / "features.csv"
OUT_KINEMATICS_CSV = ROOT / "data" / "ootang_kinematics_long.csv"
OUT_KINEMATICS_SUMMARY_CSV = ROOT / "data" / "ootang_kinematics_summary.csv"
REFERENCE_STAGES_CSV = ROOT / "config" / "tangent_reference_stages.csv"

DATE_COL = "Date"
DISP_COLS = ["MJ9/mm", "MJ1/mm", "MJ3/mm",
             "ATU1/mm", "ATU2/mm", "ATU3/mm", "ATU4/mm", "ATU5/mm"]
RWL_COL = "RWL/m"
RAIN_COL = "Rainfall/mm"
RAIN_WINDOWS = [7, 15, 30]
CASE_ID = "ootang"

TANGENT_DIR = ROOT / "figures" / "tangent_angle"
OUT_UNIFORM_RATES = TANGENT_DIR / "uniform_rates.csv"
UNIFORM_STAGE_RANGES = {}


def short(col: str) -> str:
    """列名去单位后缀,便于派生列命名:'MJ9/mm' -> 'MJ9'。"""
    return col.split("/")[0]


def build_features(df, reference_stages=None, *, include_kinematics=False):
    """Build model features plus a time-aware long kinematics table."""
    df = df.rename(columns=lambda column: column.strip())
    df[DATE_COL] = pd.to_datetime(df[DATE_COL])
    df = df.sort_values(DATE_COL).reset_index(drop=True)

    stations = {short(column): column for column in DISP_COLS}
    kinematics = build_long_kinematics(
        df,
        case=CASE_ID,
        stations=stations,
        date_col=DATE_COL,
    )
    tangent_frame, parameters = build_tangent_frame(
        df,
        stations,
        manual_ranges=UNIFORM_STAGE_RANGES,
        reference_stages=reference_stages,
    )

    out = pd.DataFrame({DATE_COL: df[DATE_COL]})

    for col in DISP_COLS:
        station = short(col)
        station_kinematics = kinematics.loc[
            kinematics["station"].eq(station)
        ].reset_index(drop=True)
        if not station_kinematics["date"].equals(out[DATE_COL]):
            raise RuntimeError(f"{station} 运动学日期与特征日期未对齐")
        out[f"{station}_disp"] = station_kinematics["displacement"]
        out[f"{station}_v"] = station_kinematics["velocity"]
        out[f"{station}_delta_v"] = station_kinematics["delta_v"]
        for suffix in (
            "_alpha_raw",
            "_alpha_smooth",
            "_alpha_raw_level",
            "_alpha_daily_level",
            "_alpha_level",
        ):
            out[f"{station}{suffix}"] = tangent_frame[f"{station}{suffix}"]

        out[f"{station}_alpha"] = out[f"{station}_alpha_smooth"]

    rwl_kinematics = compute_point_kinematics(df[DATE_COL], df[RWL_COL])
    out["RWL"] = df[RWL_COL]
    out["RWL_rate"] = rwl_kinematics["velocity"]
    out["Rain"] = df[RAIN_COL]
    for window in RAIN_WINDOWS:
        out[f"Rain_cum{window}"] = df[RAIN_COL].rolling(window).sum()

    out = out.dropna().reset_index(drop=True)
    if include_kinematics:
        return out, parameters, kinematics
    return out, parameters


def main():
    df = pd.read_csv(DATA_CSV)
    reference_stages = None
    if REFERENCE_STAGES_CSV.exists():
        reference_stages = load_reference_stages(REFERENCE_STAGES_CSV)
    out, parameters, kinematics = build_features(
        df,
        reference_stages=reference_stages,
        include_kinematics=True,
    )
    out.to_csv(OUT_CSV, index=False)
    kinematics.to_csv(OUT_KINEMATICS_CSV, index=False)
    summary = summarize_kinematics(kinematics)
    summary.to_csv(OUT_KINEMATICS_SUMMARY_CSV, index=False)

    print(f"[features] 输出 {OUT_CSV}")
    print(f"[features] 输出 {OUT_KINEMATICS_CSV}")
    print(f"[features] 输出 {OUT_KINEMATICS_SUMMARY_CSV}")
    print(f"[features] 形状 {out.shape}, 日期 {out[DATE_COL].iloc[0].date()} -> {out[DATE_COL].iloc[-1].date()}")
    print(f"[features] 列({len(out.columns)}): {list(out.columns)}")
    print(f"[features] NaN 总数: {int(out.isna().sum().sum())}")
    print(
        "[features] 运动学有效速度/ΔV: "
        f"{int(summary['n_valid_velocity'].sum())}/"
        f"{int(summary['n_valid_delta_v'].sum())}"
    )
    alpha_cols = [c for c in out.columns if c.endswith("_alpha")]
    amin, amax = out[alpha_cols].min().min(), out[alpha_cols].max().max()
    print(f"[features] 切线角范围: [{amin:.2f}, {amax:.2f}] 度  (落在 -90~90;负角=位移回缩期,属稳定级)")

    TANGENT_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(uniform_rate_rows(parameters)).to_csv(
        OUT_UNIFORM_RATES,
        index=False,
    )
    print(f"[features] 输出 {OUT_UNIFORM_RATES}")


if __name__ == "__main__":
    main()
