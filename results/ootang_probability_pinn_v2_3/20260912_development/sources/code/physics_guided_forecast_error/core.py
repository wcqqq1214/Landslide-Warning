"""Explicit error signs, chronological endpoints and repeated-date accounting."""

import numpy as np
import pandas as pd

POINTS = ("ATU1", "ATU5", "MJ3", "MJ1")
ORIGINS = (252, 342, 432, 612)
FEATURES = ("rain_mm", "rain_sum30_mm", "rwl_m", "rwl_change30_m")


def date_at(index):
    return str((pd.Timestamp("2016-07-01") + pd.Timedelta(days=int(index))).date())


def choose_teacher(objectives):
    if (
        set(objectives) != {"A", "B"}
        or not np.isfinite(list(objectives.values())).all()
    ):
        raise ValueError("Both finite training objectives are required")
    return "B" if objectives["B"] < objectives["A"] - 1e-12 else "A"


def describe_error(mu, observed, fit_days):
    """Post-hoc constant/linear removal is a description, not a model forecast."""
    mu, observed = np.asarray(mu), np.asarray(observed)
    if (
        fit_days <= 30
        or mu.shape != (fit_days + 180, 4)
        or observed.shape != mu.shape
        or not np.isfinite(mu).all()
        or not np.isfinite(observed).all()
    ):
        raise ValueError("Exactly one finite training prefix and 180 forecast days")
    error = mu - observed
    horizon = np.arange(1, 181)
    centered = horizon - horizon.mean()
    rows = []
    for j, station in enumerate(POINTS):
        train = error[30:fit_days, j]
        future = error[fit_days:, j]
        bias = future.mean()
        slope = np.sum(centered * (future - bias)) / np.sum(centered**2)
        detrended = future - bias - slope * centered
        total = np.sum(future**2)
        mu_delta = mu[fit_days:, j] - mu[fit_days - 30 : -30, j]
        y_delta = observed[fit_days:, j] - observed[fit_days - 30 : -30, j]
        delta_error = mu_delta - y_delta
        p_peak, y_peak = int(np.argmax(mu_delta)), int(np.argmax(y_delta))
        row = dict(
            station=station,
            train_days=len(train),
            prediction_days=180,
            train_rmse_mm=float(np.sqrt(np.mean(train**2))),
            train_mae_mm=float(np.mean(abs(train))),
            train_bias_mm=float(train.mean()),
            prediction_rmse_mm=float(np.sqrt(np.mean(future**2))),
            prediction_mae_mm=float(np.mean(abs(future))),
            prediction_bias_mm=float(bias),
            last_training_error_mm=float(error[fit_days - 1, j]),
            first_prediction_error_mm=float(future[0]),
            last_prediction_error_mm=float(future[-1]),
            growth_error_mm_per_day=float((future[-1] - error[fit_days - 1, j]) / 180),
            increment30_rmse_mm=float(np.sqrt(np.mean(delta_error**2))),
            increment30_mae_mm=float(np.mean(abs(delta_error))),
            oracle_constant_removed_rmse_mm=float(
                np.sqrt(np.mean((future - bias) ** 2))
            ),
            oracle_linear_removed_rmse_mm=float(np.sqrt(np.mean(detrended**2))),
            oracle_linear_slope_mm_per_day=float(slope),
            oracle_linear_explained_error_fraction=float(
                1 - np.sum(detrended**2) / total
            )
            if total > 0
            else None,
            observed_peak30_increment_mm=float(y_delta[y_peak]),
            predicted_peak30_increment_mm=float(mu_delta[p_peak]),
            observed_peak30_date=date_at(fit_days + y_peak),
            predicted_peak30_date=date_at(fit_days + p_peak),
            peak30_date_difference_days=p_peak - y_peak,
        )
        for start in (0, 60, 120):
            first, last = fit_days + start - 1, fit_days + start + 59
            row[f"growth_error_days_{start + 1}_{start + 60}_mm_per_day"] = float(
                (error[last, j] - error[first, j]) / 60
            )
        rows.append(row)
    return rows


def driver_features(forcing):
    f = np.asarray(forcing)
    if f.ndim != 2 or f.shape[1] != 2 or not np.isfinite(f).all():
        raise ValueError("Finite rainfall and reservoir level are required")
    rain, level = f.T
    change = np.full(len(f), np.nan)
    change[30:] = level[30:] - level[:-30]
    return dict(
        rain_mm=rain.copy(),
        rain_sum30_mm=pd.Series(rain).rolling(30, min_periods=1).sum().to_numpy(),
        rwl_m=level.copy(),
        rwl_change30_m=change,
    )


def correlation(a, b):
    a, b = np.asarray(a), np.asarray(b)
    if (
        a.shape != b.shape
        or a.ndim != 1
        or not np.isfinite(a).all()
        or not np.isfinite(b).all()
    ):
        raise ValueError("Correlation requires paired finite one-dimensional arrays")
    ac, bc = a - a.mean(), b - b.mean()
    sa, sb = np.sqrt(np.mean(ac**2)), np.sqrt(np.mean(bc**2))
    if min(sa, sb) <= 1e-12:
        return None
    return float(np.mean(ac * bc) / (sa * sb))


def inventory(outer_days, mode, calibration_days=60):
    """Availability only; this does not change v1.4's complete-window protocol."""
    if outer_days not in (432, 612) or mode not in ("complete_180", "available_prefix"):
        raise ValueError("Unregistered inventory")
    if calibration_days != 60:
        raise ValueError("Only the declared 60-day candidate partition is described")
    windows, multiplicity = [], {}
    cut = outer_days - calibration_days
    for n in ORIGINS:
        if n >= outer_days or (mode == "complete_180" and n + 180 > outer_days):
            continue
        end = min(n + 180, outer_days)
        dates = list(range(n, end))
        windows.append(
            dict(
                teacher_fit_days=n,
                first_date=date_at(n),
                last_date=date_at(end - 1),
                first_index=n,
                end_index_exclusive=end,
                window_days=end - n,
                candidate_mean_days=sum(t < cut for t in dates),
                candidate_scale_days=sum(t >= cut for t in dates),
            )
        )
        for t in dates:
            multiplicity[t] = multiplicity.get(t, 0) + 1
    return dict(
        outer_days=outer_days,
        mode=mode,
        windows=windows,
        window_days=sum(w["window_days"] for w in windows),
        unique_days=len(multiplicity),
        repeated_window_days=sum(multiplicity.values()) - len(multiplicity),
        max_date_multiplicity=max(multiplicity.values(), default=0),
        candidate_mean_unique_days=sum(t < cut for t in multiplicity),
        candidate_scale_unique_days=sum(t >= cut for t in multiplicity),
        candidate_mean_last_date=date_at(cut - 1),
        candidate_scale_first_date=date_at(cut),
        date_multiplicity={
            date_at(t): count for t, count in sorted(multiplicity.items())
        },
        interpretation="Repeated teacher forecasts share labels; days are not independent repetitions.",
    )
