"""Fuse dynamic V0 and persistent tangent-angle warning evidence."""

from pathlib import Path

import pandas as pd

from warning_thresholds import build_warning_frame

ROOT = Path(__file__).resolve().parent.parent
FEATURE_CSV = ROOT / "data" / "features.csv"
RAW_CSV = ROOT / "data" / "monitoring_data.csv"
NGBOOST_PROBABILITIES_CSV = ROOT / "figures" / "ngboost" / "warning_probabilities.csv"
FIG_DIR = ROOT / "figures" / "warning_fusion"
OUT_CSV = FIG_DIR / "warning_fusion.csv"

KEY_STATIONS = ("MJ9", "MJ1", "MJ3")
WARNING_STATIONS = {
    station: f"{station}/mm"
    for station in ("MJ9", "MJ1", "MJ3", "ATU1", "ATU2", "ATU3", "ATU4", "ATU5")
}


def fuse_warning_levels(v0_levels, alpha_levels, key_stations=KEY_STATIONS):
    """Apply V0-primary warning rules without allowing a downgrade."""
    v0_levels = pd.Series(v0_levels, dtype=int).reset_index(drop=True)
    alpha_levels = alpha_levels.copy().reset_index(drop=True)
    expected = [f"{station}_alpha_level" for station in key_stations]
    missing = [column for column in expected if column not in alpha_levels]
    if missing:
        raise ValueError(f"缺少关键测点切线角等级: {missing}")
    if len(v0_levels) != len(alpha_levels):
        raise ValueError("V0 与切线角序列长度必须一致")

    key_alpha = alpha_levels[expected].astype(int)
    valid_alpha = key_alpha.where(key_alpha >= 0)
    alpha_max = valid_alpha.max(axis=1).fillna(-1).astype(int)
    elevated_count = key_alpha.gt(0).sum(axis=1).astype(int)

    final_levels = []
    reasons = []
    for v0_level, max_alpha, count in zip(
        v0_levels,
        alpha_max,
        elevated_count,
    ):
        if v0_level < 0:
            final_levels.append(-1)
            reasons.append("invalid_v0")
        elif v0_level == 0 and count == 0:
            final_levels.append(0)
            reasons.append("v0_green")
        elif v0_level == 0 and count == 1:
            final_levels.append(1)
            reasons.append("alpha_watch")
        elif v0_level == 0 and count >= 2:
            final_levels.append(max(1, max_alpha))
            reasons.append("multi_station_confirmed")
        elif max_alpha > v0_level:
            final_levels.append(max_alpha)
            reasons.append("multi_scale_confirmed")
        elif max_alpha >= v0_level:
            final_levels.append(v0_level)
            reasons.append("v0_alpha_consistent")
        else:
            final_levels.append(v0_level)
            reasons.append("v0_primary")

    return pd.DataFrame({
        "v0_level": v0_levels,
        "alpha_max_level": alpha_max,
        "alpha_elevated_station_count": elevated_count,
        "final_level": pd.Series(final_levels, dtype=int),
        "fusion_reason": reasons,
    })


def build_fusion_frame(
    features,
    raw,
    key_stations=KEY_STATIONS,
    warning_stations=WARNING_STATIONS,
    probabilities=None,
):
    """Align feature, V0, tangent-angle, and optional NGBoost evidence."""
    features = features.rename(columns=lambda column: column.strip()).copy()
    features["Date"] = pd.to_datetime(features["Date"])
    warning_frame, thresholds = build_warning_frame(raw, warning_stations)
    merged = features.merge(warning_frame, on="Date", how="inner")
    merged = merged[merged["warning_level"] >= 0].reset_index(drop=True)

    alpha_columns = [f"{station}_alpha_level" for station in key_stations]
    fused = fuse_warning_levels(
        merged["warning_level"],
        merged[alpha_columns],
        key_stations=key_stations,
    )
    out = pd.concat(
        [merged[["Date", *alpha_columns]].reset_index(drop=True), fused],
        axis=1,
    )

    if probabilities is not None:
        probabilities = probabilities.copy()
        probabilities["Date"] = pd.to_datetime(probabilities["Date"])
        probability_columns = [
            column
            for column in probabilities
            if column == "predicted_level" or column.startswith("prob_")
        ]
        evidence = probabilities[["Date", *probability_columns]].copy()
        probability_only = [
            column for column in probability_columns if column.startswith("prob_")
        ]
        if probability_only:
            evidence["ngboost_confidence"] = evidence[probability_only].max(axis=1)
        out = out.merge(evidence, on="Date", how="left")

    if (out["final_level"] < out["v0_level"]).any():
        raise RuntimeError("融合结果不能降低 V0 预警等级")
    return out, thresholds


def main():
    features = pd.read_csv(FEATURE_CSV)
    raw = pd.read_csv(RAW_CSV)
    probabilities = None
    if NGBOOST_PROBABILITIES_CSV.exists():
        probabilities = pd.read_csv(NGBOOST_PROBABILITIES_CSV)

    result, _ = build_fusion_frame(
        features,
        raw,
        probabilities=probabilities,
    )
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    result.to_csv(OUT_CSV, index=False)

    print(f"[fusion] 输出: {OUT_CSV}")
    print("[fusion] 最终等级分布:")
    for level, count in result["final_level"].value_counts().sort_index().items():
        print(f"        level {level}: {count}")
    print("[fusion] 证据类型:")
    for reason, count in result["fusion_reason"].value_counts().items():
        print(f"        {reason}: {count}")


if __name__ == "__main__":
    main()
