"""Station-specific V0 warning thresholds from monthly displacement rates."""
import numpy as np
import pandas as pd

TRAIN_FRAC = 0.8
MONTH_WINDOW_DAYS = 30
ACCEL_PERCENTILE = 0.90
VIGILANCE_MULTIPLIER = 5.0
ALARM_MULTIPLIER = 10.0
V0_ESTIMATION_METHOD = "thesis_steady_stage_velocity_statistic"
HIGH_LEVEL_THRESHOLD_SOURCE = "chen_et_al_2024_eq10_default_vd"


def compute_v0(
    displacement,
    train_frac=TRAIN_FRAC,
    month_window_days=MONTH_WINDOW_DAYS,
    accel_percentile=ACCEL_PERCENTILE,
):
    displacement = pd.Series(displacement, dtype=float).reset_index(drop=True)
    n_train = int(len(displacement) * train_frac)
    train_inc = displacement.diff().iloc[1:n_train]
    monthly_rates = train_inc.rolling(
        window=month_window_days,
        min_periods=month_window_days,
    ).sum().dropna()
    if monthly_rates.empty:
        raise ValueError("训练期长度不足，无法计算月速率 V0")

    cutoff = float(monthly_rates.quantile(accel_percentile))
    steady_rates = monthly_rates[monthly_rates <= cutoff]
    if len(steady_rates) < 2:
        raise ValueError("稳定月速率样本不足，无法计算 V0")

    v_bar = float(steady_rates.mean())
    sigma = float(steady_rates.std(ddof=1))
    v0 = float(1.5 * v_bar + 2 * sigma)
    return {
        "v_bar_mm_per_month": v_bar,
        "sigma_mm_per_month": sigma,
        "accel_cutoff_mm_per_month": cutoff,
        "v0_mm_per_month": v0,
        "v0_yellow_threshold": v0,
        "v0_orange_threshold": VIGILANCE_MULTIPLIER * v0,
        "v0_red_threshold": ALARM_MULTIPLIER * v0,
        "month_window_days": int(month_window_days),
        "accel_percentile": float(accel_percentile),
        "vigilance_multiplier": VIGILANCE_MULTIPLIER,
        "alarm_multiplier": ALARM_MULTIPLIER,
        "v0_estimation_method": V0_ESTIMATION_METHOD,
        "high_level_threshold_source": HIGH_LEVEL_THRESHOLD_SOURCE,
        "n_monthly_samples": int(len(monthly_rates)),
        "n_steady_months": int(len(steady_rates)),
    }


def compute_station_thresholds(
    df,
    stations,
    train_frac=TRAIN_FRAC,
    month_window_days=MONTH_WINDOW_DAYS,
    accel_percentile=ACCEL_PERCENTILE,
):
    return {
        station: compute_v0(
            df[disp_col],
            train_frac=train_frac,
            month_window_days=month_window_days,
            accel_percentile=accel_percentile,
        )
        for station, disp_col in stations.items()
    }


def monthly_displacement_rate(displacement, month_window_days=MONTH_WINDOW_DAYS):
    displacement = pd.Series(displacement, dtype=float).reset_index(drop=True)
    return displacement - displacement.shift(month_window_days)


def classify_monthly_rates(monthly_rates, v0):
    rates = np.asarray(monthly_rates, dtype=float)
    valid = np.isfinite(rates)
    levels = np.full(rates.shape, -1, dtype=int)
    levels[valid & (rates < v0)] = 0
    vigilance = VIGILANCE_MULTIPLIER * v0
    alarm = ALARM_MULTIPLIER * v0
    levels[valid & (rates >= v0) & (rates < vigilance)] = 1
    levels[valid & (rates >= vigilance) & (rates < alarm)] = 2
    levels[valid & (rates >= alarm)] = 3
    return levels


def build_warning_frame(
    df,
    stations,
    thresholds=None,
    date_col="Date",
    train_frac=TRAIN_FRAC,
    month_window_days=MONTH_WINDOW_DAYS,
    accel_percentile=ACCEL_PERCENTILE,
):
    ordered = df.copy()
    ordered.columns = [c.strip() for c in ordered.columns]
    ordered[date_col] = pd.to_datetime(ordered[date_col])
    ordered = ordered.sort_values(date_col).reset_index(drop=True)
    if thresholds is None:
        thresholds = compute_station_thresholds(
            ordered,
            stations,
            train_frac=train_frac,
            month_window_days=month_window_days,
            accel_percentile=accel_percentile,
        )

    out = pd.DataFrame({date_col: ordered[date_col]})
    level_cols = []
    for station, disp_col in stations.items():
        rate_col = f"{station}_monthly_rate"
        level_col = f"{station}_warning_level"
        out[rate_col] = monthly_displacement_rate(
            ordered[disp_col],
            month_window_days=month_window_days,
        )
        out[level_col] = classify_monthly_rates(
            out[rate_col],
            thresholds[station]["v0_mm_per_month"],
        )
        level_cols.append(level_col)

    levels = out[level_cols].to_numpy()
    out["warning_level"] = levels.max(axis=1)
    out.loc[(levels < 0).any(axis=1), "warning_level"] = -1
    out["warning_level"] = out["warning_level"].astype(int)
    return out, thresholds


def threshold_rows(thresholds):
    return [
        {"station": station, **values}
        for station, values in thresholds.items()
    ]
