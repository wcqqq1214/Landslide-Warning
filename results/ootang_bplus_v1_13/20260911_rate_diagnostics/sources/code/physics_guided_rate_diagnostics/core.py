"""Descriptive identities for a frozen correction, rate and uncertainty scale."""

import numpy as np


def vector(values):
    result = np.asarray(values, dtype=float)
    if result.ndim != 1 or not len(result) or not np.isfinite(result).all():
        raise ValueError("A nonempty finite vector is required")
    return result


def rms(values):
    return float(np.sqrt(np.mean(np.square(values))))


def point_statistics(base, prediction, observed, background, state, tolerance):
    base, prediction, observed, background, state = map(
        vector, (base, prediction, observed, background, state)
    )
    if len({x.shape for x in (base, prediction, observed, background, state)}) != 1:
        raise ValueError("Point series must share the exact calendar")
    demand, correction = observed - base, prediction - base
    if not np.allclose(correction, background + state, rtol=0, atol=1e-8):
        raise ValueError("Background and state do not reconstruct the correction")
    active = (abs(demand) > tolerance) & (abs(correction) > tolerance)
    opposed = active & (demand * correction < 0)
    overshot = (
        active
        & (demand * correction > 0)
        & (abs(correction) > 2 * abs(demand) + tolerance)
    )
    energy = float(np.mean(correction**2))
    alignment = float(-2 * np.mean(demand * correction))
    result = dict(
        days=len(base),
        active_days=int(active.sum()),
        opposed_days=int(opposed.sum()),
        overshot_days=int(overshot.sum()),
        opposed_fraction=float(opposed.sum() / active.sum())
        if active.any()
        else np.nan,
        overshot_fraction=float(overshot.sum() / active.sum())
        if active.any()
        else np.nan,
        base_rmse_mm=rms(base - observed),
        base_mae_mm=float(np.mean(abs(base - observed))),
        s_rmse_mm=rms(prediction - observed),
        s_mae_mm=float(np.mean(abs(prediction - observed))),
        correction_max_abs_mm=float(np.max(abs(correction))),
        correction_energy_mm2=energy,
        alignment_term_mm2=alignment,
        mse_change_mm2=energy + alignment,
    )
    for name, values in (
        ("demand", demand),
        ("correction", correction),
        ("background", background),
        ("state", state),
    ):
        result[name + "_mean_mm"] = float(np.mean(values))
        result[name + "_rms_mm"] = rms(values)
    return result


def multiplier_statistics(multiplier, rate, edge):
    multiplier, rate = vector(multiplier), vector(rate)
    if multiplier.shape != rate.shape or (rate < 0).any():
        raise ValueError("Matched nonnegative baseline rates required")
    if (multiplier < 0.5).any() or (multiplier > 2).any():
        raise ValueError("Frozen multiplier bounds violated")
    scaled = np.log(multiplier) / np.log(2)
    lower, upper = scaled <= -edge, scaled >= edge
    weights = rate.sum()
    result = dict(
        days=len(rate),
        mean=float(multiplier.mean()),
        lower_fraction=float(lower.mean()),
        upper_fraction=float(upper.mean()),
        baseline_rate_sum_mm=float(weights),
        weighted_mean=float(np.sum(rate * multiplier) / weights) if weights else np.nan,
        weighted_lower_fraction=float(rate[lower].sum() / weights)
        if weights
        else np.nan,
        weighted_upper_fraction=float(rate[upper].sum() / weights)
        if weights
        else np.nan,
    )
    for name, value in zip(
        ("min", "q05", "median", "q95", "max"),
        np.quantile(multiplier, [0, 0.05, 0.5, 0.95, 1], method="linear"),
    ):
        result[name] = float(value)
    return result


def feature_statistics(z, h, start, end, tolerance):
    z = np.asarray(z, dtype=float)
    if (
        z.ndim != 2
        or z.shape[1] != 12
        or not 0 <= start < end <= len(z)
        or not 1 <= h <= len(z)
        or not np.isfinite(z).all()
    ):
        raise ValueError("A finite feature calendar and valid prefix are required")
    fit_min, fit_max = z[:h].min(axis=0), z[:h].max(axis=0)
    values = z[start:end]
    outside = (values < fit_min - tolerance) | (values > fit_max + tolerance)
    rows = []
    for j in range(12):
        rows.append(
            dict(
                days=len(values),
                mean_z=float(values[:, j].mean()),
                sd_z=float(values[:, j].std(ddof=0)),
                min_z=float(values[:, j].min()),
                max_z=float(values[:, j].max()),
                training_min_z=float(fit_min[j]),
                training_max_z=float(fit_max[j]),
                outside_days=int(outside[:, j].sum()),
                outside_fraction=float(outside[:, j].mean()),
            )
        )
    return rows, dict(
        days=len(values),
        outside_days=int(np.any(outside, axis=1).sum()),
        outside_fraction=float(np.any(outside, axis=1).mean()),
    )


def scale_statistics(calibration, prediction, sigma):
    calibration, prediction = vector(calibration), vector(prediction)
    if not np.isfinite(sigma) or sigma <= 0:
        raise ValueError("A positive frozen sigma is required")
    result = dict(sigma_mm=float(sigma))
    for name, values in (("calibration", calibration), ("prediction", prediction)):
        bias, sd = float(values.mean()), float(values.std(ddof=0))
        result.update(
            {
                name + "_days": len(values),
                name + "_bias_mm": bias,
                name + "_sd_mm": sd,
                name + "_rms_mm": rms(values),
                name + "_bias_squared_mm2": bias**2,
                name + "_sd_squared_mm2": sd**2,
                name + "_rms_over_sigma": rms(values) / sigma,
            }
        )
    return result
