"""Pre-specified robustness analysis for V0 and tangent-angle rules."""

from itertools import product
from pathlib import Path
import sys

import numpy as np
import pandas as pd

CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from features.tangent_angle import build_tangent_frame, uniform_rate_rows  # noqa: E402
from warning.warning_events import extract_warning_events  # noqa: E402
from warning.warning_fusion import KEY_STATIONS, WARNING_STATIONS, fuse_warning_levels  # noqa: E402
from warning.warning_thresholds import build_warning_frame, threshold_rows  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RAW_CSV = ROOT / "data" / "monitoring_data.csv"
OUT_DIR = ROOT / "figures" / "sensitivity"
OUT_V0_SUMMARY = OUT_DIR / "v0_sensitivity.csv"
OUT_V0_PARAMETERS = OUT_DIR / "v0_parameters.csv"
OUT_TANGENT_SUMMARY = OUT_DIR / "tangent_sensitivity.csv"
OUT_TANGENT_PARAMETERS = OUT_DIR / "tangent_parameters.csv"

V0_MONTH_WINDOWS = (15, 30, 60)
V0_ACCEL_PERCENTILES = (0.85, 0.90, 0.95)
DEFAULT_V0_CONFIG = (30, 0.90)

TANGENT_CANDIDATE_WINDOWS = (15, 30, 60)
TANGENT_SMOOTH_WINDOWS = (1, 3, 5)
TANGENT_PERSISTENCE_RULES = ((3, 2), (5, 3), (7, 4))
DEFAULT_TANGENT_CONFIG = (30, 3, 5, 3)

LEVEL_NAMES = ("green", "yellow", "orange", "red")
FUSION_REASONS = (
    "v0_green",
    "alpha_watch",
    "multi_station_confirmed",
    "multi_scale_confirmed",
    "v0_alpha_consistent",
    "v0_primary",
)
ANALYSIS_SCOPE = "retrospective_fixed_first_80pct"


def _ordered_raw(raw, date_col="Date"):
    ordered = raw.rename(columns=lambda column: column.strip()).copy()
    ordered[date_col] = pd.to_datetime(ordered[date_col])
    return ordered.sort_values(date_col).reset_index(drop=True)


def compare_level_sequences(reference, current):
    """Compare two warning sequences only where both levels are valid."""
    reference = np.asarray(reference, dtype=int)
    current = np.asarray(current, dtype=int)
    if reference.shape != current.shape:
        raise ValueError("参考与当前等级序列形状必须一致")

    common = (reference >= 0) & (current >= 0)
    common_days = int(common.sum())
    changed_days = int((reference[common] != current[common]).sum())
    agreement = (
        float(1 - changed_days / common_days) if common_days else np.nan
    )
    return {
        "common_valid_days": common_days,
        "changed_days_vs_default": changed_days,
        "agreement_rate_vs_default": agreement,
    }


def _level_counts(levels):
    levels = pd.Series(levels, dtype=int)
    valid = levels[levels >= 0]
    counts = {"valid_days": int(len(valid))}
    for level, name in enumerate(LEVEL_NAMES):
        counts[f"{name}_days"] = int(valid.eq(level).sum())
    counts["yellow_plus_days"] = int(valid.ge(1).sum())
    counts["orange_plus_days"] = int(valid.ge(2).sum())
    return counts


def _event_count(dates, levels):
    return int(len(extract_warning_events(dates, levels, min_level=1)))


def analyze_v0_sensitivity(
    raw,
    stations=WARNING_STATIONS,
    month_windows=V0_MONTH_WINDOWS,
    accel_percentiles=V0_ACCEL_PERCENTILES,
    default_config=DEFAULT_V0_CONFIG,
):
    """Evaluate pre-specified V0 configurations without selecting a winner."""
    ordered = _ordered_raw(raw)
    evaluations = []
    parameter_rows = []

    for month_window, accel_percentile in product(
        month_windows,
        accel_percentiles,
    ):
        warning_frame, thresholds = build_warning_frame(
            ordered,
            stations,
            month_window_days=month_window,
            accel_percentile=accel_percentile,
        )
        config = (int(month_window), float(accel_percentile))
        evaluations.append({
            "config": config,
            "frame": warning_frame,
        })
        for values in threshold_rows(thresholds):
            parameter_rows.append({
                "analysis_scope": ANALYSIS_SCOPE,
                "month_window_days": config[0],
                "accel_percentile": config[1],
                "is_default": config == default_config,
                **values,
            })

    references = [item for item in evaluations if item["config"] == default_config]
    if len(references) != 1:
        raise ValueError("V0 默认参数必须且只能出现在敏感性组合中一次")
    reference_levels = references[0]["frame"]["warning_level"].to_numpy()

    summary_rows = []
    for item in evaluations:
        month_window, accel_percentile = item["config"]
        frame = item["frame"]
        levels = frame["warning_level"]
        summary_rows.append({
            "analysis_scope": ANALYSIS_SCOPE,
            "month_window_days": month_window,
            "accel_percentile": accel_percentile,
            "is_default": item["config"] == default_config,
            **_level_counts(levels),
            "warning_events": _event_count(frame["Date"], levels),
            **compare_level_sequences(reference_levels, levels),
        })

    return pd.DataFrame(summary_rows), pd.DataFrame(parameter_rows)


def analyze_tangent_sensitivity(
    raw,
    v0_frame,
    stations=WARNING_STATIONS,
    key_stations=KEY_STATIONS,
    candidate_windows=TANGENT_CANDIDATE_WINDOWS,
    smooth_windows=TANGENT_SMOOTH_WINDOWS,
    persistence_rules=TANGENT_PERSISTENCE_RULES,
    default_config=DEFAULT_TANGENT_CONFIG,
):
    """Evaluate tangent-angle fusion robustness against fixed default V0."""
    ordered = _ordered_raw(raw)
    v0_frame = v0_frame.copy()
    v0_frame["Date"] = pd.to_datetime(v0_frame["Date"])
    evaluations = []
    parameter_rows = []
    recorded_candidate_windows = set()

    for candidate_window, smooth_window, persistence_rule in product(
        candidate_windows,
        smooth_windows,
        persistence_rules,
    ):
        persist_window, persist_min_hits = persistence_rule
        tangent_frame, parameters = build_tangent_frame(
            ordered,
            stations,
            candidate_window=candidate_window,
            smooth_window=smooth_window,
            persist_window=persist_window,
            persist_min_hits=persist_min_hits,
        )
        if candidate_window not in recorded_candidate_windows:
            for values in uniform_rate_rows(parameters):
                parameter_rows.append({
                    "analysis_scope": ANALYSIS_SCOPE,
                    "candidate_window_days": int(candidate_window),
                    **values,
                })
            recorded_candidate_windows.add(candidate_window)

        alpha_columns = [f"{station}_alpha_level" for station in key_stations]
        merged = tangent_frame[["Date", *alpha_columns]].merge(
            v0_frame[["Date", "warning_level"]],
            on="Date",
            how="inner",
        )
        merged = merged[merged["warning_level"] >= 0].reset_index(drop=True)
        fused = fuse_warning_levels(
            merged["warning_level"],
            merged[alpha_columns],
            key_stations=key_stations,
        )
        config = (
            int(candidate_window),
            int(smooth_window),
            int(persist_window),
            int(persist_min_hits),
        )
        evaluations.append({
            "config": config,
            "dates": merged["Date"],
            "fused": fused,
        })

    references = [item for item in evaluations if item["config"] == default_config]
    if len(references) != 1:
        raise ValueError("切线角默认参数必须且只能出现在敏感性组合中一次")
    reference_levels = references[0]["fused"]["final_level"].to_numpy()
    candidate_references = {}
    for candidate_window in candidate_windows:
        candidate_config = (
            int(candidate_window),
            default_config[1],
            default_config[2],
            default_config[3],
        )
        matches = [
            item for item in evaluations if item["config"] == candidate_config
        ]
        if len(matches) != 1:
            raise ValueError("每个候选窗口必须包含默认平滑与持续性规则")
        candidate_references[int(candidate_window)] = matches[0]["fused"][
            "final_level"
        ].to_numpy()

    summary_rows = []
    for item in evaluations:
        candidate, smooth, persist_window, persist_hits = item["config"]
        fused = item["fused"]
        levels = fused["final_level"]
        reasons = fused["fusion_reason"].value_counts()
        candidate_comparison = compare_level_sequences(
            candidate_references[candidate],
            levels,
        )
        summary_rows.append({
            "analysis_scope": ANALYSIS_SCOPE,
            "candidate_window_days": candidate,
            "smooth_window_days": smooth,
            "persist_window_days": persist_window,
            "persist_min_hits": persist_hits,
            "is_default": item["config"] == default_config,
            **_level_counts(levels),
            "warning_events": _event_count(item["dates"], levels),
            "alpha_elevated_days": int(
                fused["alpha_elevated_station_count"].gt(0).sum()
            ),
            "upgraded_days": int(fused["final_level"].gt(fused["v0_level"]).sum()),
            **{
                f"reason_{reason}_days": int(reasons.get(reason, 0))
                for reason in FUSION_REASONS
            },
            **compare_level_sequences(reference_levels, levels),
            "common_valid_days_vs_candidate_default": candidate_comparison[
                "common_valid_days"
            ],
            "changed_days_vs_candidate_default": candidate_comparison[
                "changed_days_vs_default"
            ],
            "agreement_rate_vs_candidate_default": candidate_comparison[
                "agreement_rate_vs_default"
            ],
        })

    return pd.DataFrame(summary_rows), pd.DataFrame(parameter_rows)


def main():
    raw = pd.read_csv(RAW_CSV)
    v0_summary, v0_parameters = analyze_v0_sensitivity(raw)
    default_v0_frame, _ = build_warning_frame(raw, WARNING_STATIONS)
    tangent_summary, tangent_parameters = analyze_tangent_sensitivity(
        raw,
        default_v0_frame,
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    v0_summary.to_csv(OUT_V0_SUMMARY, index=False)
    v0_parameters.to_csv(OUT_V0_PARAMETERS, index=False)
    tangent_summary.to_csv(OUT_TANGENT_SUMMARY, index=False)
    tangent_parameters.to_csv(OUT_TANGENT_PARAMETERS, index=False)

    print(f"[sensitivity] V0 组合: {len(v0_summary)} -> {OUT_V0_SUMMARY}")
    print(
        "[sensitivity] V0 等级一致率范围: "
        f"{v0_summary['agreement_rate_vs_default'].min():.3f}-"
        f"{v0_summary['agreement_rate_vs_default'].max():.3f}"
    )
    print(
        f"[sensitivity] 切线角组合: {len(tangent_summary)} "
        f"-> {OUT_TANGENT_SUMMARY}"
    )
    print(
        "[sensitivity] 融合等级一致率范围: "
        f"{tangent_summary['agreement_rate_vs_default'].min():.3f}-"
        f"{tangent_summary['agreement_rate_vs_default'].max():.3f}"
    )


if __name__ == "__main__":
    main()
