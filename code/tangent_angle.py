"""Estimate station-specific rates from a uniform displacement stage."""

import numpy as np
import pandas as pd

TRAIN_FRAC = 0.8
CANDIDATE_WINDOW = 30
SMOOTH_WINDOW = 3
PERSIST_WINDOW = 5
PERSIST_MIN_HITS = 3


def validate_daily_dates(dates):
    """Parse dates and require a strictly increasing daily sequence."""
    index = pd.DatetimeIndex(pd.to_datetime(dates))
    if index.hasnans:
        raise ValueError("日期中不能包含无效值")
    if index.has_duplicates:
        raise ValueError("日期不能重复")
    if not index.is_monotonic_increasing:
        raise ValueError("日期必须严格递增")
    if len(index) > 1:
        gaps = index[1:] - index[:-1]
        if not np.all(gaps == pd.Timedelta(days=1)):
            raise ValueError("日期必须保持日等间隔")
    return index


def _rate_statistics(rates):
    mean_rate = float(np.mean(rates))
    median_rate = float(np.median(rates))
    rate_mad = float(np.median(np.abs(rates - median_rate)))
    if len(rates) > 1:
        mean_abs_accel = float(np.mean(np.abs(np.diff(rates))))
    else:
        mean_abs_accel = 0.0
    return mean_rate, rate_mad, mean_abs_accel


def _result(method, dates, start_index, end_index, rates):
    mean_rate, rate_mad, mean_abs_accel = _rate_statistics(rates)
    return {
        "method": method,
        "start_date": dates[start_index].strftime("%Y-%m-%d"),
        "end_date": dates[end_index].strftime("%Y-%m-%d"),
        "v_eq_mm_per_day": mean_rate,
        "rate_mad_mm_per_day": rate_mad,
        "mean_abs_accel_mm_per_day2": mean_abs_accel,
        "n_rate_samples": int(len(rates)),
    }


def estimate_uniform_rate(
    dates,
    displacement,
    train_frac=TRAIN_FRAC,
    window=CANDIDATE_WINDOW,
    manual_range=None,
):
    """Estimate an equal-speed-stage daily rate manually or from training data."""
    date_index = validate_daily_dates(dates)
    displacement = pd.Series(displacement, dtype=float).reset_index(drop=True)
    if len(date_index) != len(displacement):
        raise ValueError("日期与位移序列长度必须一致")

    rates = displacement.diff().to_numpy(dtype=float)

    if manual_range is not None:
        try:
            start_date, end_date = map(pd.Timestamp, manual_range)
        except (TypeError, ValueError):
            raise ValueError("人工等速阶段必须包含有效的起止日期") from None
        if (
            start_date not in date_index
            or end_date not in date_index
            or start_date >= end_date
        ):
            raise ValueError("人工等速阶段起止日期必须存在于数据中且起点早于终点")

        start_index = int(date_index.get_loc(start_date))
        end_index = int(date_index.get_loc(end_date))
        selected_rates = rates[start_index + 1:end_index + 1]
        mean_rate, _, _ = _rate_statistics(selected_rates)
        if not np.isfinite(mean_rate) or mean_rate <= 0:
            raise ValueError("无法获得正的等速阶段速率")
        return _result(
            "manual",
            date_index,
            start_index,
            end_index,
            selected_rates,
        )

    n_train = int(len(date_index) * train_frac)
    if n_train < window + 1:
        raise ValueError("训练期长度不足，无法选择等速阶段")

    best = None
    for end_index in range(window, n_train):
        start_rate_index = end_index - window + 1
        candidate_rates = rates[start_rate_index:end_index + 1]
        mean_rate, rate_mad, mean_abs_accel = _rate_statistics(candidate_rates)
        if not np.isfinite(mean_rate) or mean_rate <= 0:
            continue

        score = (
            rate_mad / mean_rate,
            mean_abs_accel / mean_rate,
            end_index,
        )
        if best is None or score < best[0]:
            best = (score, start_rate_index, end_index, candidate_rates.copy())

    if best is None:
        raise ValueError("无法获得正的等速阶段速率")

    _, start_rate_index, end_index, selected_rates = best
    return _result(
        "automatic_candidate",
        date_index,
        start_rate_index - 1,
        end_index,
        selected_rates,
    )
