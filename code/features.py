"""Feature engineering for displacement, reservoir level and rainfall drivers."""
from pathlib import Path

import pandas as pd

from tangent_angle import build_tangent_frame, uniform_rate_rows

ROOT = Path(__file__).resolve().parent.parent
DATA_CSV = ROOT / "data" / "monitoring_data.csv"
OUT_CSV = ROOT / "data" / "features.csv"

DATE_COL = "Date"
DISP_COLS = ["MJ9/mm", "MJ1/mm", "MJ3/mm",
             "ATU1/mm", "ATU2/mm", "ATU3/mm", "ATU4/mm", "ATU5/mm"]
RWL_COL = "RWL/m"
RAIN_COL = "Rainfall/mm"
RAIN_WINDOWS = [7, 15, 30]
DT_DAYS = 1.0

TANGENT_DIR = ROOT / "figures" / "tangent_angle"
OUT_UNIFORM_RATES = TANGENT_DIR / "uniform_rates.csv"
UNIFORM_STAGE_RANGES = {}


def short(col: str) -> str:
    """列名去单位后缀,便于派生列命名:'MJ9/mm' -> 'MJ9'。"""
    return col.split("/")[0]


def build_features(df):
    """Build a feature DataFrame with auditable tangent-angle warning columns."""
    df = df.rename(columns=lambda c: c.strip())

    date_col = DATE_COL.strip()
    df[date_col] = pd.to_datetime(df[date_col])
    df = df.sort_values(date_col).reset_index(drop=True)

    stations = {short(col): col.strip() for col in DISP_COLS}
    tangent_frame, parameters = build_tangent_frame(
        df,
        stations,
        manual_ranges=UNIFORM_STAGE_RANGES,
    )

    out = pd.DataFrame({DATE_COL: df[date_col]})

    for col in DISP_COLS:
        s = short(col)
        out[f"{s}_disp"] = df[col.strip()]
        out[f"{s}_v"] = df[col.strip()].diff() / DT_DAYS
        out[f"{s}_a"] = out[f"{s}_v"].diff() / DT_DAYS

        for suffix in ["_alpha_raw", "_alpha_smooth", "_alpha_daily_level", "_alpha_level"]:
            out[f"{s}{suffix}"] = tangent_frame[f"{s}{suffix}"]

        out[f"{s}_alpha"] = out[f"{s}_alpha_smooth"]

    out["RWL"] = df[RWL_COL.strip()]
    out["RWL_rate"] = df[RWL_COL.strip()].diff() / DT_DAYS
    out["Rain"] = df[RAIN_COL.strip()]
    for w in RAIN_WINDOWS:
        out[f"Rain_cum{w}"] = df[RAIN_COL.strip()].rolling(w).sum()

    out = out.dropna().reset_index(drop=True)
    return out, parameters


def main():
    df = pd.read_csv(DATA_CSV)
    features_frame, parameters = build_features(df)

    features_frame.to_csv(OUT_CSV, index=False)

    print(f"[features] 输出 {OUT_CSV}")
    print(f"[features] 形状 {features_frame.shape}, 日期 {features_frame[DATE_COL].iloc[0].date()} -> {features_frame[DATE_COL].iloc[-1].date()}")
    print(f"[features] 列({len(features_frame.columns)}): {list(features_frame.columns)}")
    print(f"[features] NaN 总数: {int(features_frame.isna().sum().sum())}")
    alpha_cols = [c for c in features_frame.columns if c.endswith("_alpha")]
    amin, amax = features_frame[alpha_cols].min().min(), features_frame[alpha_cols].max().max()
    print(f"[features] 切线角范围: [{amin:.2f}, {amax:.2f}] 度  (落在 -90~90;负角=位移回缩期,属稳定级)")

    TANGENT_DIR.mkdir(parents=True, exist_ok=True)
    rates_df = pd.DataFrame(uniform_rate_rows(parameters))
    rates_df.to_csv(OUT_UNIFORM_RATES, index=False)
    print(f"[features] 输出 {OUT_UNIFORM_RATES}")


if __name__ == "__main__":
    main()
