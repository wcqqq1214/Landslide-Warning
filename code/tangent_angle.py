"""Estimate uniform rates and tangent-angle warning levels."""

import numpy as np
import pandas as pd

TRAIN_FRAC = 0.8
CANDIDATE_WINDOW = 30
SMOOTH_WINDOW = 3
PERSIST_WINDOW = 5
PERSIST_MIN_HITS = 3


def _causal_linear_slopes(displacement, window):
    """Fit a trailing linear slope without using future observations."""
    displacement = pd.Series(displacement, dtype=float).reset_index(drop=True)
    if window == 1:
        return displacement.diff()

    x = np.arange(window, dtype=float)

    def linear_slope(values):
        return float(np.polyfit(x, values, 1)[0])

    return displacement.rolling(window=window, min_periods=window).apply(
        linear_slope,
        raw=True,
    )


def tangent_angle_series(displacement, v_eq, smooth_window=SMOOTH_WINDOW):
    """Return raw and trailing-smoothed rates and tangent angles."""
    if (
        isinstance(v_eq, (bool, np.bool_))
        or not isinstance(v_eq, Real)
        or not np.isfinite(v_eq)
        or v_eq <= 0
    ):
        raise ValueError("v_eq 必须是有限正数")
    if (
        isinstance(smooth_window, (bool, np.bool_))
        or not isinstance(smooth_window, Integral)
        or smooth_window <= 0
    ):
        raise ValueError("smooth_window 必须是正整数")

    displacement = pd.Series(displacement, dtype=float).reset_index(drop=True)
    raw_rate = displacement.diff()
    smooth_rate = _causal_linear_slopes(displacement, smooth_window)
    finite_raw_rate = raw_rate.where(np.isfinite(raw_rate))
    finite_smooth_rate = smooth_rate.where(np.isfinite(smooth_rate))

    return pd.DataFrame(
        {
            "raw_rate": raw_rate,
            "smooth_rate": smooth_rate,
            "alpha_raw": np.degrees(np.arctan(finite_raw_rate / v_eq)),
            "alpha_smooth": np.degrees(
                np.arctan(finite_smooth_rate / v_eq)
            ),
        }
    )


def classify_tangent_angles(angles):
    """Classify tangent angles using the paper's warning boundaries."""
    angles = pd.Series(angles, dtype=float).reset_index(drop=True)
    levels = pd.Series(-1, index=angles.index, dtype=int)
    valid = np.isfinite(angles)

    levels.loc[valid] = 0
    levels.loc[valid & angles.gt(45.0) & angles.lt(80.0)] = 1
    levels.loc[valid & angles.ge(80.0) & angles.lt(85.0)] = 2
    levels.loc[valid & angles.ge(85.0)] = 3
    return levels


def persistent_warning_levels(
    levels,
    window=PERSIST_WINDOW,
    min_hits=PERSIST_MIN_HITS,
):
    """Apply a full-window persistence rule to daily warning levels."""
    if (
        isinstance(window, (bool, np.bool_))
        or not isinstance(window, Integral)
        or window <= 0
    ):
        raise ValueError("window 必须是正整数")
    if (
        isinstance(min_hits, (bool, np.bool_))
        or not isinstance(min_hits, Integral)
        or min_hits <= 0
    ):
        raise ValueError("min_hits 必须是正整数")
    if min_hits > window:
        raise ValueError("min_hits 不能大于 window")

    levels = pd.Series(levels, dtype=float).reset_index(drop=True)
    persistent = pd.Series(-1, index=levels.index, dtype=int)

    for end_index in range(window - 1, len(levels)):
        recent = levels.iloc[end_index - window + 1:end_index + 1]
        if not recent.isin([0.0, 1.0, 2.0, 3.0]).all():
            continue

        persistent.iloc[end_index] = 0
        for level in (3, 2, 1):
            if int(recent.ge(level).sum()) >= min_hits:
                persistent.iloc[end_index] = level
                break

    return persistent


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


def build_tangent_frame(
    df,
    stations,
    manual_ranges=None,
    date_col="Date",
    train_frac=TRAIN_FRAC,
    candidate_window=CANDIDATE_WINDOW,
):
    """Compute per-station tangent-angle series, daily levels, and persistent levels."""
    df = df.rename(columns=lambda c: c.strip())
    date_col = date_col.strip()

    date_index = validate_daily_dates(df[date_col])

    result = pd.DataFrame({"Date": date_index})
    parameters = {}

    for station_name, disp_col in stations.items():
        disp_col = disp_col.strip()
        displacement = df[disp_col]

        manual_range = None
        if manual_ranges and station_name in manual_ranges:
            manual_range = manual_ranges[station_name]

        rate_params = estimate_uniform_rate(
            date_index,
            displacement,
            train_frac=train_frac,
            window=candidate_window,
            manual_range=manual_range,
        )
        parameters[station_name] = rate_params

        angles_df = tangent_angle_series(
            displacement,
            rate_params["v_eq_mm_per_day"],
        )

        daily_levels = classify_tangent_angles(angles_df["alpha_smooth"])
        persistent_levels = persistent_warning_levels(daily_levels)

        result[f"{station_name}_alpha_raw"] = angles_df["alpha_raw"]
        result[f"{station_name}_alpha_smooth"] = angles_df["alpha_smooth"]
        result[f"{station_name}_alpha_daily_level"] = daily_levels
        result[f"{station_name}_alpha_level"] = persistent_levels

    return result, parameters


def uniform_rate_rows(parameters):
    """Return a deterministic list of dicts from the station parameter mapping."""
    return [
        {"station": station, **params} for station, params in parameters.items()
    ]


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
