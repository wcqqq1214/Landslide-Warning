"""Create auditable future-onset labels and event inventory."""

from pathlib import Path
import sys

import pandas as pd

CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning.ngboost_warn import WARNING_STATIONS  # noqa: E402
from warning.warning_events import build_onset_targets, extract_warning_events  # noqa: E402
from warning.warning_thresholds import build_warning_frame, threshold_rows  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RAW_CSV = ROOT / "data" / "monitoring_data.csv"
OUT_DIR = ROOT / "figures" / "warning_onset"
OUT_EVENTS_CSV = OUT_DIR / "onset_events.csv"
OUT_TARGETS_CSV = OUT_DIR / "onset_targets.csv"
OUT_INVENTORY_CSV = OUT_DIR / "onset_inventory.csv"
OUT_THRESHOLDS_CSV = ROOT / "figures" / "thresholds" / "v0_thresholds.csv"

HORIZONS = (1, 3, 7)
MIN_WARNING_LEVEL = 1
MERGE_GAP_DAYS = 0
MIN_ACTIVE_DAYS = 1
LABEL_SCOPE = "exploratory_fixed_v0_first_80pct"


def annotate_event_forecastability(events, targets, horizons=HORIZONS):
    events = events.copy()
    target_dates = pd.DatetimeIndex(pd.to_datetime(targets["Date"]))
    for horizon in horizons:
        column = f"onset_h{horizon}"
        forecastable = []
        for start_date in pd.to_datetime(events["start_date"]):
            window = (
                (target_dates >= start_date - pd.Timedelta(days=horizon))
                & (target_dates < start_date)
            )
            forecastable.append(bool(targets.loc[window, column].eq(1).any()))
        events[f"forecastable_h{horizon}"] = forecastable
    return events


def build_inventory(targets, events, horizons=HORIZONS):
    rows = []
    for horizon in horizons:
        column = f"onset_h{horizon}"
        valid = targets[column].notna()
        rows.append({
            "horizon_days": horizon,
            "valid_at_risk_days": int(valid.sum()),
            "positive_days": int(targets.loc[valid, column].sum()),
            "negative_days": int((targets.loc[valid, column] == 0).sum()),
            "warning_events": int(len(events)),
            "forecastable_events": int(events[f"forecastable_h{horizon}"].sum()),
        })
    return pd.DataFrame(rows)


def main():
    raw = pd.read_csv(RAW_CSV)
    warning_frame, thresholds = build_warning_frame(raw, WARNING_STATIONS)
    events = extract_warning_events(
        warning_frame["Date"],
        warning_frame["warning_level"],
        min_level=MIN_WARNING_LEVEL,
        merge_gap_days=MERGE_GAP_DAYS,
        min_active_days=MIN_ACTIVE_DAYS,
    )
    targets = build_onset_targets(
        warning_frame["Date"],
        warning_frame["warning_level"],
        horizons=HORIZONS,
        min_level=MIN_WARNING_LEVEL,
    )
    events = annotate_event_forecastability(events, targets)
    targets.insert(1, "label_scope", LABEL_SCOPE)
    inventory = build_inventory(targets, events)
    events.insert(1, "label_scope", LABEL_SCOPE)
    inventory.insert(0, "label_scope", LABEL_SCOPE)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_THRESHOLDS_CSV.parent.mkdir(parents=True, exist_ok=True)
    events.to_csv(OUT_EVENTS_CSV, index=False)
    targets.to_csv(OUT_TARGETS_CSV, index=False)
    inventory.to_csv(OUT_INVENTORY_CSV, index=False)
    pd.DataFrame(threshold_rows(thresholds)).to_csv(
        OUT_THRESHOLDS_CSV,
        index=False,
    )

    print(f"[onset] 事件清单: {OUT_EVENTS_CSV}")
    print(f"[onset] 未来标签: {OUT_TARGETS_CSV}")
    print(f"[onset] 样本盘点: {OUT_INVENTORY_CSV}")
    print(events.to_string(index=False))
    print(inventory.to_string(index=False))


if __name__ == "__main__":
    main()
