"""Estimate uniform rates and tangent-angle warning levels."""

from numbers import Integral, Real

import numpy as np
import pandas as pd

TRAIN_FRAC = 0.8
CANDIDATE_WINDOW = 30
SMOOTH_WINDOW = 3
PERSIST_WINDOW = 5
PERSIST_MIN_HITS = 3

_VALID_STATUSES = frozenset({"candidate", "approved", "rejected"})
_VALID_SOURCES = frozenset(
    {"automatic_15d", "automatic_30d", "automatic_60d", "expert_manual"}
)


def _validate_reference_stages(stages):
    """Normalize and validate a reference-stage table."""
    if not isinstance(stages, pd.DataFrame):
        raise ValueError("参考阶段配置必须是 DataFrame")

    stages = stages.copy()
    stages.columns = [c.strip() for c in stages.columns]
    required = {"station", "start_date", "end_date", "status", "source"}
    missing = required - set(stages.columns)
    if missing:
        raise ValueError(f"参考阶段配置文件缺少必需列: {missing}")

    for column in ("station", "status", "source"):
        if stages[column].isna().any():
            raise ValueError(f"参考阶段配置列 {column} 不能包含空值")
        stages[column] = stages[column].astype(str).str.strip()
        if stages[column].eq("").any():
            raise ValueError(f"参考阶段配置列 {column} 不能为空")

    unknown_status = set(stages["status"]) - _VALID_STATUSES
    if unknown_status:
        raise ValueError(f"无效的阶段状态值: {unknown_status}")

    unknown_source = set(stages["source"]) - _VALID_SOURCES
    if unknown_source:
        raise ValueError(f"无效的阶段来源值: {unknown_source}")

    for column in ("start_date", "end_date"):
        parsed = pd.to_datetime(stages[column], errors="coerce")
        if parsed.isna().any():
            raise ValueError(f"参考阶段配置日期无效: {column}")
        stages[column] = parsed

    if "review_note" not in stages:
        stages["review_note"] = ""
    return stages


def load_reference_stages(csv_path):
    """Read and validate the reference stages configuration file."""
    return _validate_reference_stages(pd.read_csv(csv_path))


def _training_end_date(date_index, train_frac):
    if (
        isinstance(train_frac, (bool, np.bool_))
        or not isinstance(train_frac, Real)
        or not np.isfinite(train_frac)
        or train_frac <= 0
        or train_frac > 1
    ):
        raise ValueError("train_frac 必须在 (0, 1] 范围内")
    n_train = int(len(date_index) * train_frac)
    if n_train < 1:
        raise ValueError("训练期长度不足")
    return date_index[n_train - 1]


def _build_manual_ranges_from_stages(stages, dates, stations, train_frac=TRAIN_FRAC):
    """Validate approved stages and return a ``manual_ranges`` dict.

    Rules enforced:
    - At most one approved stage per station.
    - Approved stage dates must exist in the data and start_date < end_date.
    - Approved stage must lie completely within the training period.
    - Rejects any station that has multiple approved stages.
    - Approved stations not present in ``stations`` are rejected.
    """
    stages = _validate_reference_stages(stages)
    date_index = validate_daily_dates(dates)
    train_end_date = _training_end_date(date_index, train_frac)

    approved = stages[stages["status"] == "approved"].copy()
    if approved.empty:
        return {}

    counts = approved.groupby("station").size()
    multi = counts[counts > 1]
    if not multi.empty:
        raise ValueError(
            f"以下测点存在多个已批准阶段，必须只有一个: "
            f"{list(multi.index)}"
        )

    unknown_stations = sorted(set(approved["station"]) - set(stations))
    if unknown_stations:
        raise ValueError(f"已批准阶段包含未知测点: {unknown_stations}")

    manual_ranges = {}
    for _, row in approved.iterrows():
        station = row["station"]
        start_date = pd.Timestamp(row["start_date"])
        end_date = pd.Timestamp(row["end_date"])

        if start_date not in date_index or end_date not in date_index:
            raise ValueError(
                f"测点 {station} 的人工等速阶段日期不在数据中: "
                f"{start_date.strftime('%Y-%m-%d')} – "
                f"{end_date.strftime('%Y-%m-%d')}"
            )
        if start_date >= end_date:
            raise ValueError(
                f"测点 {station} 的人工等速阶段起始必须早于结束: "
                f"{start_date.strftime('%Y-%m-%d')} – "
                f"{end_date.strftime('%Y-%m-%d')}"
            )
        if end_date > train_end_date:
            raise ValueError(
                f"测点 {station} 的人工等速阶段结束日期 "
                f"({end_date.strftime('%Y-%m-%d')}) 超出训练期 "
                f"({train_end_date.strftime('%Y-%m-%d')})，"
                f"只能使用训练期数据"
            )

        manual_ranges[station] = (start_date, end_date)

    return manual_ranges


def _check_manual_range_feasibility(
    dates,
    displacement,
    manual_range,
    train_frac=TRAIN_FRAC,
):
    """Validate a manual range and return its indices and finite rates."""
    date_index = validate_daily_dates(dates)
    displacement = pd.Series(displacement, dtype=float).reset_index(drop=True)
    if len(date_index) != len(displacement):
        raise ValueError("日期与位移序列长度必须一致")
    try:
        start_date, end_date = map(pd.Timestamp, manual_range)
    except (TypeError, ValueError):
        raise ValueError("人工等速阶段必须包含有效的起止日期") from None
    if start_date not in date_index or end_date not in date_index:
        raise ValueError("人工等速阶段起止日期必须存在于数据中")
    if start_date >= end_date:
        raise ValueError("人工等速阶段起点必须早于终点")

    train_end_date = _training_end_date(date_index, train_frac)
    if end_date > train_end_date:
        raise ValueError(
            f"人工等速阶段结束日期 ({end_date.strftime('%Y-%m-%d')}) "
            f"超出训练期 ({train_end_date.strftime('%Y-%m-%d')})，"
            "只能使用训练期数据"
        )

    start_index = int(date_index.get_loc(start_date))
    end_index = int(date_index.get_loc(end_date))
    rates = displacement.diff().to_numpy(dtype=float)[start_index + 1:end_index + 1]

    if len(rates) < 2:
        raise ValueError(
            f"人工阶段 {start_date.strftime('%Y-%m-%d')} – "
            f"{end_date.strftime('%Y-%m-%d')} 有效速率样本不足 "
            f"({len(rates)} 个)"
        )
    if not np.isfinite(rates).all():
        raise ValueError("人工等速阶段不能包含缺失或非有限位移速率")
    mean_rate = float(np.mean(rates))
    if not np.isfinite(mean_rate) or mean_rate <= 0:
        raise ValueError(
            f"人工阶段 {start_date.strftime('%Y-%m-%d')} – "
            f"{end_date.strftime('%Y-%m-%d')} 无法获得正的等速阶段速率 "
            f"(均值={mean_rate})"
        )
    return start_index, end_index, rates


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
    """Classify tangent angles using Xu et al. (2009) strict boundaries."""
    angles = pd.Series(angles, dtype=float).reset_index(drop=True)
    levels = pd.Series(-1, index=angles.index, dtype=int)
    valid = np.isfinite(angles)

    levels.loc[valid] = 0
    levels.loc[valid & angles.gt(45.0)] = 1
    levels.loc[valid & angles.gt(80.0)] = 2
    levels.loc[valid & angles.gt(85.0)] = 3
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
    return mean_rate, median_rate, rate_mad, mean_abs_accel


def _result(method, dates, start_index, end_index, rates, source=None):
    mean_rate, median_rate, rate_mad, mean_abs_accel = _rate_statistics(rates)
    entry = {
        "method": method,
        "source": source,
        "start_date": dates[start_index].strftime("%Y-%m-%d"),
        "end_date": dates[end_index].strftime("%Y-%m-%d"),
        "stage_duration_days": int((dates[end_index] - dates[start_index]).days),
        "v_eq_mm_per_day": mean_rate,
        "median_rate_mm_per_day": median_rate,
        "rate_mad_mm_per_day": rate_mad,
        "mean_abs_accel_mm_per_day2": mean_abs_accel,
        "n_rate_samples": int(len(rates)),
    }
    return entry


def build_tangent_frame(
    df,
    stations,
    manual_ranges=None,
    date_col="Date",
    train_frac=TRAIN_FRAC,
    candidate_window=CANDIDATE_WINDOW,
    smooth_window=SMOOTH_WINDOW,
    persist_window=PERSIST_WINDOW,
    persist_min_hits=PERSIST_MIN_HITS,
    reference_stages=None,
):
    """Build auditable tangent-angle and persistent warning columns.

    Parameters
    ----------
    reference_stages : DataFrame or None
        If given, must contain columns ``station``, ``start_date``,
        ``end_date``, ``status``, ``source``.  Rows with
        ``status == "approved"`` are used as manual ranges.
    """
    df = df.rename(columns=lambda column: column.strip())
    date_col = date_col.strip()
    date_index = validate_daily_dates(df[date_col])

    extra_manual = {}
    manual_sources = {}
    validated_stages = None
    if reference_stages is not None:
        validated_stages = _validate_reference_stages(reference_stages)
        extra_manual = _build_manual_ranges_from_stages(
            validated_stages,
            date_index,
            stations,
            train_frac=train_frac,
        )
        approved = validated_stages[validated_stages["status"] == "approved"]
        manual_sources.update(
            dict(zip(approved["station"], approved["source"]))
        )

    if manual_ranges is not None:
        unknown_manual = sorted(set(manual_ranges) - set(stations))
        if unknown_manual:
            raise ValueError(f"人工阶段包含未知测点: {unknown_manual}")
        extra_manual.update(manual_ranges)
        manual_sources.update({
            station: "manual_ranges_argument" for station in manual_ranges
        })

    result = pd.DataFrame({"Date": date_index})
    parameters = {}

    for station, disp_col in stations.items():
        displacement = df[disp_col.strip()]
        manual_range = extra_manual.get(station)

        rate_parameters = estimate_uniform_rate(
            date_index,
            displacement,
            train_frac=train_frac,
            window=candidate_window,
            manual_range=manual_range,
        )
        if manual_range is not None:
            rate_parameters["source"] = manual_sources[station]
        parameters[station] = rate_parameters

        angle_frame = tangent_angle_series(
            displacement,
            rate_parameters["v_eq_mm_per_day"],
            smooth_window=smooth_window,
        )
        raw_levels = classify_tangent_angles(angle_frame["alpha_raw"])
        daily_levels = classify_tangent_angles(angle_frame["alpha_smooth"])
        persistent_levels = persistent_warning_levels(
            daily_levels,
            window=persist_window,
            min_hits=persist_min_hits,
        )

        result[f"{station}_alpha_raw"] = angle_frame["alpha_raw"]
        result[f"{station}_alpha_smooth"] = angle_frame["alpha_smooth"]
        result[f"{station}_alpha_raw_level"] = raw_levels
        result[f"{station}_alpha_daily_level"] = daily_levels
        result[f"{station}_alpha_level"] = persistent_levels

    return result, parameters


def uniform_rate_rows(parameters):
    """Return station parameters as deterministic tabular rows."""
    return [
        {"station": station, **values}
        for station, values in parameters.items()
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
        start_index, end_index, selected_rates = _check_manual_range_feasibility(
            date_index,
            displacement,
            manual_range,
            train_frac=train_frac,
        )
        return _result(
            "manual",
            date_index,
            start_index,
            end_index,
            selected_rates,
            source="manual_ranges_argument",
        )

    n_train = int(len(date_index) * train_frac)
    if n_train < window + 1:
        raise ValueError("训练期长度不足，无法选择等速阶段")

    best = None
    for end_index in range(window, n_train):
        start_rate_index = end_index - window + 1
        candidate_rates = rates[start_rate_index:end_index + 1]
        mean_rate, _, rate_mad, mean_abs_accel = _rate_statistics(candidate_rates)
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
        source=f"automatic_{window}d",
    )
