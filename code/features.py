"""Feature engineering for displacement, reservoir level and rainfall drivers."""
from pathlib import Path
import numpy as np
import pandas as pd

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


def short(col: str) -> str:
    """列名去单位后缀,便于派生列命名:'MJ9/mm' -> 'MJ9'。"""
    return col.split("/")[0]


def improved_tangent_angle(disp: pd.Series, dt: float) -> pd.Series:
    """许强 2009 改进切线角。

    无人工划分等速阶段时,用整段一阶差分中位数近似 v_bar。
    """
    v = disp.diff() / dt
    v_bar = v.median()
    if v_bar == 0 or np.isnan(v_bar):
        v_bar = 1.0
    alpha = np.degrees(np.arctan(v / v_bar))
    return alpha


def main():
    df = pd.read_csv(DATA_CSV)
    df.columns = [c.strip() for c in df.columns]
    df[DATE_COL] = pd.to_datetime(df[DATE_COL])
    df = df.sort_values(DATE_COL).reset_index(drop=True)

    out = pd.DataFrame({DATE_COL: df[DATE_COL]})

    for col in DISP_COLS:
        s = short(col)
        out[f"{s}_disp"] = df[col]
        out[f"{s}_v"] = df[col].diff() / DT_DAYS
        out[f"{s}_a"] = out[f"{s}_v"].diff() / DT_DAYS
        out[f"{s}_alpha"] = improved_tangent_angle(df[col], DT_DAYS)

    out["RWL"] = df[RWL_COL]
    out["RWL_rate"] = df[RWL_COL].diff() / DT_DAYS
    out["Rain"] = df[RAIN_COL]
    for w in RAIN_WINDOWS:
        out[f"Rain_cum{w}"] = df[RAIN_COL].rolling(w).sum()

    out = out.dropna().reset_index(drop=True)
    out.to_csv(OUT_CSV, index=False)

    print(f"[features] 输出 {OUT_CSV}")
    print(f"[features] 形状 {out.shape}, 日期 {out[DATE_COL].iloc[0].date()} -> {out[DATE_COL].iloc[-1].date()}")
    print(f"[features] 列({len(out.columns)}): {list(out.columns)}")
    print(f"[features] NaN 总数: {int(out.isna().sum().sum())}")
    alpha_cols = [c for c in out.columns if c.endswith("_alpha")]
    amin, amax = out[alpha_cols].min().min(), out[alpha_cols].max().max()
    print(f"[features] 切线角范围: [{amin:.2f}, {amax:.2f}] 度  (落在 -90~90;负角=位移回缩期,属稳定级)")


if __name__ == "__main__":
    main()
