"""Future-warning targets and event-level evaluation utilities."""

from collections.abc import Iterable
from numbers import Integral

import numpy as np
import pandas as pd


def _validated_series(dates, levels):
    dates = pd.DatetimeIndex(pd.to_datetime(dates))
    levels = pd.Series(levels, dtype="Int64").reset_index(drop=True)
    if len(dates) != len(levels):
        raise ValueError("日期和预警等级长度必须一致")
    if dates.hasnans or levels.isna().any():
        raise ValueError("日期和预警等级不能包含缺失值")
    if dates.has_duplicates or not dates.is_monotonic_increasing:
        raise ValueError("日期必须严格递增且不能重复")
    if len(dates) > 1 and not np.all(
        dates[1:] - dates[:-1] == pd.Timedelta(days=1)
    ):
        raise ValueError("事件分析要求连续的日尺度日期")
    if (levels < -1).any():
        raise ValueError("预警等级不能小于 -1")
    return dates, levels.astype(int)


def _positive_int(value, name):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Integral):
        raise ValueError(f"{name} 必须是正整数")
    if value <= 0:
        raise ValueError(f"{name} 必须是正整数")
    return int(value)


def extract_warning_events(
    dates,
    levels,
    min_level=1,
    merge_gap_days=0,
    min_active_days=1,
):
    """Return contiguous warning events without using future model output."""
    dates, levels = _validated_series(dates, levels)
    min_level = _positive_int(min_level, "min_level")
    if (
        isinstance(merge_gap_days, (bool, np.bool_))
        or not isinstance(merge_gap_days, Integral)
        or merge_gap_days < 0
    ):
        raise ValueError("merge_gap_days 必须是非负整数")
    min_active_days = _positive_int(min_active_days, "min_active_days")

    active_positions = np.flatnonzero(levels.to_numpy() >= min_level)
    if not len(active_positions):
        return pd.DataFrame(columns=[
            "event_id",
            "start_date",
            "end_date",
            "duration_days",
            "active_days",
            "max_level",
        ])

    groups = [[int(active_positions[0])]]
    for position in active_positions[1:]:
        position = int(position)
        previous = groups[-1][-1]
        gap = position - previous - 1
        gap_levels = levels.iloc[previous + 1:position]
        can_merge = gap <= merge_gap_days and not gap_levels.eq(-1).any()
        if can_merge:
            groups[-1].append(position)
        else:
            groups.append([position])

    rows = []
    for positions in groups:
        if len(positions) < min_active_days:
            continue
        start, end = positions[0], positions[-1]
        rows.append({
            "start_date": dates[start],
            "end_date": dates[end],
            "duration_days": int((dates[end] - dates[start]).days + 1),
            "active_days": int(len(positions)),
            "max_level": int(levels.iloc[start:end + 1].max()),
        })

    events = pd.DataFrame(rows)
    if events.empty:
        return pd.DataFrame(columns=[
            "event_id",
            "start_date",
            "end_date",
            "duration_days",
            "active_days",
            "max_level",
        ])
    events.insert(0, "event_id", np.arange(1, len(events) + 1))
    return events


def build_onset_targets(
    dates,
    levels,
    horizons=(1, 3, 7),
    min_level=1,
):
    """Build at-risk future-onset labels for one or more forecast horizons."""
    dates, levels = _validated_series(dates, levels)
    min_level = _positive_int(min_level, "min_level")
    if not isinstance(horizons, Iterable):
        raise ValueError("horizons 必须是正整数序列")
    horizons = tuple(_positive_int(value, "horizon") for value in horizons)
    if not horizons or len(set(horizons)) != len(horizons):
        raise ValueError("horizons 不能为空或包含重复值")

    valid = levels.ge(0)
    active = levels.ge(min_level) & valid
    previous_active = active.shift(1, fill_value=False)
    previous_valid = valid.shift(1, fill_value=False)
    onset = active & previous_valid & ~previous_active
    at_risk = valid & ~active

    result = pd.DataFrame({
        "Date": dates,
        "warning_level": levels,
        "at_risk": at_risk,
        "onset_event": onset,
    })

    onset_values = onset.to_numpy(dtype=bool)
    valid_values = valid.to_numpy(dtype=bool)
    at_risk_values = at_risk.to_numpy(dtype=bool)
    for horizon in horizons:
        target = pd.Series(pd.NA, index=result.index, dtype="Int64")
        for index in range(len(result) - horizon):
            future = slice(index + 1, index + horizon + 1)
            if at_risk_values[index] and valid_values[future].all():
                target.iloc[index] = int(onset_values[future].any())
        result[f"onset_h{horizon}"] = target

    return result


def score_onset_alerts(dates, onset_events, alerts, horizon):
    """Score alerts against pre-onset windows at a fixed decision threshold."""
    dates = pd.DatetimeIndex(pd.to_datetime(dates))
    onset_events = pd.Series(onset_events, dtype=bool).reset_index(drop=True)
    alerts = pd.Series(alerts, dtype=bool).reset_index(drop=True)
    if len(dates) != len(onset_events) or len(dates) != len(alerts):
        raise ValueError("日期、事件和报警序列长度必须一致")
    horizon = _positive_int(horizon, "horizon")

    event_rows = []
    covered_alerts = np.zeros(len(dates), dtype=bool)
    for event_position in np.flatnonzero(onset_events.to_numpy()):
        window_start = max(0, int(event_position) - horizon)
        positions = np.arange(window_start, int(event_position))
        if not len(positions):
            continue
        covered_alerts[positions] = True
        hits = positions[alerts.iloc[positions].to_numpy()]
        first_alert = dates[hits[0]] if len(hits) else pd.NaT
        event_rows.append({
            "event_date": dates[event_position],
            "hit": bool(len(hits)),
            "first_alert_date": first_alert,
            "lead_days": (
                int((dates[event_position] - first_alert).days)
                if len(hits)
                else np.nan
            ),
        })

    details = pd.DataFrame(event_rows)
    alert_values = alerts.to_numpy(dtype=bool)
    false_alert_mask = alert_values & ~covered_alerts
    false_alert_starts = false_alert_mask & ~np.r_[False, false_alert_mask[:-1]]
    leads = details.loc[details["hit"], "lead_days"] if not details.empty else []
    metrics = {
        "event_support": int(len(details)),
        "event_hits": int(details["hit"].sum()) if not details.empty else 0,
        "event_recall": (
            float(details["hit"].mean()) if not details.empty else np.nan
        ),
        "median_lead_days": float(np.median(leads)) if len(leads) else np.nan,
        "false_alert_days": int(false_alert_mask.sum()),
        "false_alert_events": int(false_alert_starts.sum()),
    }
    return metrics, details
