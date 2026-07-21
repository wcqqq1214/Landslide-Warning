"""Fit-only MVIF initial-stable-slope candidates without warning output.

The designated Word thesis describes ``V0`` from the initial stable slope of
an MVIF trend-displacement curve, but it does not prescribe an automatic
window-selection rule.  This module is an explicitly non-formal, user-approved
project adaptation:

* fit the reconciled ``+C`` MVIF equation on each station's fit period;
* use the Wang--An 5-day curve-convexity interval on the *fitted trend*;
* select the earliest continuous uniform run rather than the source method's
  current-nearest raw-displacement run; and
* profile the selected trend-slope functional instead of requiring a stable
  finite ``t_f`` parameter.

It deliberately remains separate from :mod:`warning.mvif`: the earlier strict
finite-``t_f`` diagnostic keeps its historical gate unchanged.  This module
never emits a velocity level, tangent-angle level, fusion result, or formal
warning output.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import least_squares
from scipy.stats import chi2

from warning.mvif import (
    _evaluate_normalized_mvif,
    _initial_thetas,
    _normalise_fit_end_date,
    _prepare_fit_frame,
    _run_multistart,
)


FIT_STATUS_CANDIDATE = "candidate_profiled_initial_slope"
FIT_STATUS_FAILED = "failed"
TF_PARAMETER_STATUS = "not_required_for_target_slope_profile"

# Wang and An (2023) use this interval and a five-day moving window on their
# original S--t curve.  Applying it to a fitted MVIF trend and choosing the
# earliest run are recorded project adaptations, not a claim of literal source
# reproduction.
CONVEXITY_WINDOW_DAYS = 5
UNIFORM_L_LOWER = 0.99
UNIFORM_L_UPPER = 1.01

# These values control a reproducible numerical profile calculation.  They are
# not source thresholds, warning levels, or a failure-time horizon.
PROFILE_CONFIDENCE_LEVEL = 0.95
PROFILE_MAX_FUNCTION_EVALUATIONS = 1_000
PROFILE_MAX_EXPANSIONS = 12
PROFILE_BISECTION_ITERATIONS = 24
PROFILE_EXPANSION_FACTOR = 2.0
SIGMA_DDOF = 1
_INVALID_RESIDUAL = 1e100
_MIN_SLOPE_MAGNITUDE = np.finfo(float).eps


@dataclass(frozen=True)
class MvifInitialSlopeResult:
    """One fit-only initial-slope candidate and its profile audit."""

    station: str
    status: str
    failure_reason: str | None
    fit_end_date: pd.Timestamp
    fit_start_date: pd.Timestamp | None = None
    n_fit_rows: int = 0
    time_span_days: float | None = None
    n_multistart_attempts: int = 0
    n_converged_fits: int = 0
    objective_cost: float | None = None
    tf_parameter_status: str = TF_PARAMETER_STATUS
    convexity_window_days: int = CONVEXITY_WINDOW_DAYS
    uniform_l_lower: float = UNIFORM_L_LOWER
    uniform_l_upper: float = UNIFORM_L_UPPER
    n_convexity_windows: int = 0
    n_valid_convexity_windows: int = 0
    n_uniform_windows: int = 0
    uniform_segment_start_date: pd.Timestamp | None = None
    uniform_segment_end_date: pd.Timestamp | None = None
    n_uniform_segment_trend_points: int = 0
    uniform_segment_covers_full_fit: bool | None = None
    candidate_v_mm_per_day: float | None = None
    candidate_sigma_mm_per_day: float | None = None
    candidate_v0_mm_per_day: float | None = None
    sigma_ddof: int = SIGMA_DDOF
    profile_confidence_level: float = PROFILE_CONFIDENCE_LEVEL
    profile_rss_min: float | None = None
    profile_rss_threshold: float | None = None
    profile_v_lower_mm_per_day: float | None = None
    profile_v_upper_mm_per_day: float | None = None
    profile_target_evaluations: int = 0
    profile_optimization_attempts: int = 0
    profile_converged_fits: int = 0

    def to_record(self) -> dict[str, Any]:
        """Return candidate statistics only; omit all warning-class fields."""

        return {
            "station": self.station,
            "fit_status": self.status,
            "failure_reason": self.failure_reason,
            "fit_start_date": _date_to_iso(self.fit_start_date),
            "fit_end_date": _date_to_iso(self.fit_end_date),
            "n_fit_rows": self.n_fit_rows,
            "time_span_days": self.time_span_days,
            "n_multistart_attempts": self.n_multistart_attempts,
            "n_converged_fits": self.n_converged_fits,
            "objective_cost": self.objective_cost,
            "tf_parameter_status": self.tf_parameter_status,
            "convexity_window_days": self.convexity_window_days,
            "uniform_l_lower": self.uniform_l_lower,
            "uniform_l_upper": self.uniform_l_upper,
            "n_convexity_windows": self.n_convexity_windows,
            "n_valid_convexity_windows": self.n_valid_convexity_windows,
            "n_uniform_windows": self.n_uniform_windows,
            "uniform_segment_start_date": _date_to_iso(
                self.uniform_segment_start_date
            ),
            "uniform_segment_end_date": _date_to_iso(self.uniform_segment_end_date),
            "n_uniform_segment_trend_points": self.n_uniform_segment_trend_points,
            "uniform_segment_covers_full_fit": self.uniform_segment_covers_full_fit,
            "candidate_v_mm_per_day": self.candidate_v_mm_per_day,
            "candidate_sigma_mm_per_day": self.candidate_sigma_mm_per_day,
            "candidate_v0_mm_per_day": self.candidate_v0_mm_per_day,
            "sigma_ddof": self.sigma_ddof,
            "profile_confidence_level": self.profile_confidence_level,
            "profile_rss_min": self.profile_rss_min,
            "profile_rss_threshold": self.profile_rss_threshold,
            "profile_v_lower_mm_per_day": self.profile_v_lower_mm_per_day,
            "profile_v_upper_mm_per_day": self.profile_v_upper_mm_per_day,
            "profile_target_evaluations": self.profile_target_evaluations,
            "profile_optimization_attempts": self.profile_optimization_attempts,
            "profile_converged_fits": self.profile_converged_fits,
        }


@dataclass(frozen=True)
class _UniformSegment:
    start_index: int
    end_index: int
    n_uniform_windows: int


@dataclass(frozen=True)
class _ProfilePoint:
    rss: float | None


def _date_to_iso(value: pd.Timestamp | None) -> str | None:
    if value is None or pd.isna(value):
        return None
    return value.strftime("%Y-%m-%d")


def _failure_result(
    *,
    station: str,
    fit_end_date: pd.Timestamp,
    failure_reason: str,
    **values: Any,
) -> MvifInitialSlopeResult:
    return MvifInitialSlopeResult(
        station=station,
        status=FIT_STATUS_FAILED,
        failure_reason=failure_reason,
        fit_end_date=fit_end_date,
        **values,
    )


def _linear_slope(time: np.ndarray, values: np.ndarray) -> float:
    """Return the ordinary least-squares slope without a polynomial fit API."""

    centered_time = time - float(np.mean(time))
    denominator = float(np.dot(centered_time, centered_time))
    if not math.isfinite(denominator) or denominator <= 0.0:
        raise FloatingPointError("nonpositive slope time denominator")
    centered_values = values - float(np.mean(values))
    slope = float(np.dot(centered_time, centered_values) / denominator)
    if not math.isfinite(slope):
        raise FloatingPointError("nonfinite linear slope")
    return slope


def _is_daily(raw_time_days: np.ndarray) -> bool:
    intervals = np.diff(raw_time_days)
    return bool(
        len(intervals)
        and np.isfinite(intervals).all()
        and np.all(np.isclose(intervals, 1.0, rtol=0.0, atol=1e-9))
    )


def _select_earliest_uniform_segment(
    *,
    theta: np.ndarray,
    time: np.ndarray,
    fitted_trend: np.ndarray,
    displacement_scale: float,
    displacement_origin: float,
) -> tuple[_UniformSegment | None, dict[str, int], str | None]:
    """Select the earliest continuous run of five-day uniform windows.

    Each window subtracts its fitted start displacement before evaluating ``L``.
    That makes the convexity ratio invariant to the estimated MVIF intercept
    ``C``.  This local increment form is a project adaptation required when the
    source curve's cumulative-displacement origin is not zero.
    """

    n_windows = len(time) - CONVEXITY_WINDOW_DAYS
    if n_windows <= 0:
        return (
            None,
            {
                "n_convexity_windows": 0,
                "n_valid_convexity_windows": 0,
                "n_uniform_windows": 0,
            },
            "insufficient_rows_for_convexity_window",
        )

    uniform = np.zeros(n_windows, dtype=bool)
    valid = np.zeros(n_windows, dtype=bool)
    for start_index in range(n_windows):
        end_index = start_index + CONVEXITY_WINDOW_DAYS
        midpoint_time = np.array(
            [(time[start_index] + time[end_index]) / 2.0],
            dtype=float,
        )
        try:
            midpoint = float(
                _evaluate_normalized_mvif(theta, midpoint_time)[0]
                * displacement_scale
                + displacement_origin
            )
            start_value = float(fitted_trend[start_index])
            end_increment = float(fitted_trend[end_index] - start_value)
            midpoint_increment = float(midpoint - start_value)
            denominator = end_increment / 2.0
            if (
                not math.isfinite(midpoint_increment)
                or not math.isfinite(denominator)
                or denominator <= 0.0
            ):
                continue
            ratio = midpoint_increment / denominator
        except (FloatingPointError, OverflowError, ValueError):
            continue
        if not math.isfinite(ratio):
            continue
        valid[start_index] = True
        uniform[start_index] = UNIFORM_L_LOWER <= ratio <= UNIFORM_L_UPPER

    n_valid = int(valid.sum())
    audit = {
        "n_convexity_windows": n_windows,
        "n_valid_convexity_windows": n_valid,
        "n_uniform_windows": 0,
    }
    if n_valid == 0:
        return None, audit, "no_valid_convexity_windows"
    uniform_indices = np.flatnonzero(uniform)
    if not len(uniform_indices):
        return None, audit, "no_uniform_mvif_segment"

    run_start = int(uniform_indices[0])
    run_end = run_start
    while run_end + 1 < n_windows and uniform[run_end + 1]:
        run_end += 1
    n_uniform_windows = run_end - run_start + 1
    audit["n_uniform_windows"] = n_uniform_windows
    return (
        _UniformSegment(
            start_index=run_start,
            end_index=run_end + CONVEXITY_WINDOW_DAYS,
            n_uniform_windows=n_uniform_windows,
        ),
        audit,
        None,
    )


def _profile_start_points(best_theta: np.ndarray) -> tuple[np.ndarray, ...]:
    """Use deterministic starts to reduce local-profile optimizer dependence."""

    starts = [np.asarray(best_theta[1:3], dtype=float)]
    starts.extend(
        np.asarray(theta[1:3], dtype=float) for theta in _initial_thetas()
    )
    unique: list[np.ndarray] = []
    for start in starts:
        if not any(np.array_equal(start, prior) for prior in unique):
            unique.append(start)
    return tuple(unique)


class _TargetSlopeProfile:
    """Selection-conditioned profile likelihood for one trend-slope functional."""

    def __init__(
        self,
        *,
        time: np.ndarray,
        raw_time_days: np.ndarray,
        normalized_displacement: np.ndarray,
        displacement_scale: float,
        segment_start_index: int,
        segment_end_index: int,
        start_points: tuple[np.ndarray, ...],
    ) -> None:
        self._time = time
        self._raw_time_days = raw_time_days
        self._normalized_displacement = normalized_displacement
        self._displacement_scale = displacement_scale
        self._segment_start_index = segment_start_index
        self._segment_end_index = segment_end_index
        self._start_points = start_points
        self._cache: dict[float, _ProfilePoint] = {}
        self.target_evaluations = 0
        self.optimization_attempts = 0
        self.converged_fits = 0

    def _residual_for_target(self, target_slope: float):
        def residual(log_parameters: np.ndarray) -> np.ndarray:
            try:
                theta = np.array(
                    [1.0, log_parameters[0], log_parameters[1], 0.0],
                    dtype=float,
                )
                shape = _evaluate_normalized_mvif(theta, self._time)
                selected_shape = shape[
                    self._segment_start_index : self._segment_end_index + 1
                ]
                selected_time = self._raw_time_days[
                    self._segment_start_index : self._segment_end_index + 1
                ]
                shape_slope = _linear_slope(selected_time, selected_shape)
                if abs(shape_slope) <= _MIN_SLOPE_MAGNITUDE:
                    raise FloatingPointError("near-zero MVIF shape slope")
                a = target_slope / (self._displacement_scale * shape_slope)
                c = float(np.mean(self._normalized_displacement - a * shape))
                values = a * shape + c - self._normalized_displacement
            except (FloatingPointError, OverflowError, ValueError, IndexError):
                return np.full_like(self._normalized_displacement, _INVALID_RESIDUAL)
            if not np.isfinite(values).all():
                return np.full_like(self._normalized_displacement, _INVALID_RESIDUAL)
            return values

        return residual

    def evaluate(self, target_slope: float) -> _ProfilePoint:
        """Return the least profile RSS for one physical slope target."""

        if target_slope in self._cache:
            return self._cache[target_slope]
        self.target_evaluations += 1
        if not math.isfinite(target_slope) or target_slope <= 0.0:
            point = _ProfilePoint(rss=None)
            self._cache[target_slope] = point
            return point

        residual = self._residual_for_target(target_slope)
        best_rss: float | None = None
        for start in self._start_points:
            self.optimization_attempts += 1
            try:
                fit = least_squares(
                    residual,
                    start,
                    method="trf",
                    jac="3-point",
                    x_scale="jac",
                    max_nfev=PROFILE_MAX_FUNCTION_EVALUATIONS,
                )
            except (
                FloatingPointError,
                OverflowError,
                ValueError,
                np.linalg.LinAlgError,
            ):
                continue
            if not fit.success or not np.isfinite(fit.x).all():
                continue
            values = residual(np.asarray(fit.x, dtype=float))
            if (
                not np.isfinite(values).all()
                or np.any(np.abs(values) >= _INVALID_RESIDUAL)
            ):
                continue
            rss = float(np.dot(values, values))
            if not math.isfinite(rss):
                continue
            self.converged_fits += 1
            if best_rss is None or rss < best_rss:
                best_rss = rss

        point = _ProfilePoint(rss=best_rss)
        self._cache[target_slope] = point
        return point


def _profile_bound(
    *,
    profile: _TargetSlopeProfile,
    target_slope: float,
    rss_threshold: float,
    direction: int,
) -> tuple[float | None, str | None]:
    """Locate one finite local profile interval boundary by bracketing/bisection."""

    inside = target_slope
    if direction < 0:
        outside = target_slope / PROFILE_EXPANSION_FACTOR
    else:
        outside = target_slope * PROFILE_EXPANSION_FACTOR

    for _ in range(PROFILE_MAX_EXPANSIONS):
        point = profile.evaluate(outside)
        if point.rss is None:
            return None, "profile_optimization_failed"
        if point.rss > rss_threshold:
            break
        inside = outside
        if direction < 0:
            outside /= PROFILE_EXPANSION_FACTOR
        else:
            outside *= PROFILE_EXPANSION_FACTOR
    else:
        if direction < 0:
            return None, "profile_ci_lower_unbounded"
        return None, "profile_ci_upper_unbounded"

    for _ in range(PROFILE_BISECTION_ITERATIONS):
        midpoint = (inside + outside) / 2.0
        point = profile.evaluate(midpoint)
        if point.rss is None:
            return None, "profile_optimization_failed"
        if point.rss <= rss_threshold:
            inside = midpoint
        else:
            outside = midpoint
    boundary = (inside + outside) / 2.0
    if not math.isfinite(boundary) or boundary <= 0.0:
        return None, "nonfinite_profile_bound"
    return boundary, None


def _profile_audit_values(profile: _TargetSlopeProfile) -> dict[str, int]:
    return {
        "profile_target_evaluations": profile.target_evaluations,
        "profile_optimization_attempts": profile.optimization_attempts,
        "profile_converged_fits": profile.converged_fits,
    }


def select_mvif_initial_stable_slope(
    displacement: pd.DataFrame,
    *,
    station: str,
    fit_end_date,
) -> MvifInitialSlopeResult:
    """Return a non-formal earliest-uniform MVIF trend-slope candidate.

    The fit period is selected before any fitting or window selection.  Rows
    after ``fit_end_date`` therefore cannot affect model parameters, the chosen
    uniform segment, profile bounds, or candidate statistics.
    """

    station = str(station).strip()
    if not station:
        raise ValueError("station must be nonempty")
    fit_end = _normalise_fit_end_date(fit_end_date)
    fit_frame, preparation_failure = _prepare_fit_frame(
        displacement,
        fit_end_date=fit_end,
    )
    if preparation_failure is not None:
        return _failure_result(
            station=station,
            fit_end_date=fit_end,
            failure_reason=preparation_failure,
        )
    assert fit_frame is not None

    fit_start = pd.Timestamp(fit_frame.loc[0, "date"])
    raw_time_days = (
        fit_frame["date"].sub(fit_start).dt.total_seconds().to_numpy(dtype=float)
        / 86_400.0
    )
    time_span_days = float(raw_time_days[-1])
    base = {
        "fit_start_date": fit_start,
        "n_fit_rows": len(fit_frame),
        "time_span_days": time_span_days,
        "n_multistart_attempts": len(_initial_thetas()),
    }
    if not math.isfinite(time_span_days) or time_span_days <= 0.0:
        return _failure_result(
            station=station,
            fit_end_date=fit_end,
            failure_reason="nonpositive_fit_time_span",
            **base,
        )
    if not _is_daily(raw_time_days):
        return _failure_result(
            station=station,
            fit_end_date=fit_end,
            failure_reason="non_daily_fit_dates_for_l_method",
            **base,
        )

    time = raw_time_days / time_span_days
    raw_displacement = fit_frame["displacement"].to_numpy(dtype=float)
    displacement_origin = float(raw_displacement[0])
    displacement_scale = float(
        max(np.ptp(raw_displacement), np.std(raw_displacement), 1.0)
    )
    normalized_displacement = (
        raw_displacement - displacement_origin
    ) / displacement_scale
    attempts = _run_multistart(
        time=time,
        displacement=normalized_displacement,
    )
    converged = [attempt for attempt in attempts if attempt.failure_reason is None]
    if not converged:
        return _failure_result(
            station=station,
            fit_end_date=fit_end,
            failure_reason="mvif_optimization_failed",
            n_converged_fits=0,
            **base,
        )
    best = min(converged, key=lambda attempt: float(attempt.cost))
    common_fit = {
        **base,
        "n_converged_fits": len(converged),
        "objective_cost": float(best.cost),
    }
    assert best.theta is not None
    try:
        fitted_trend = (
            _evaluate_normalized_mvif(best.theta, time) * displacement_scale
            + displacement_origin
        )
    except (FloatingPointError, OverflowError, ValueError):
        return _failure_result(
            station=station,
            fit_end_date=fit_end,
            failure_reason="nonfinite_mvif_trend",
            **common_fit,
        )
    if not np.isfinite(fitted_trend).all():
        return _failure_result(
            station=station,
            fit_end_date=fit_end,
            failure_reason="nonfinite_mvif_trend",
            **common_fit,
        )

    (
        uniform_segment,
        selection_audit,
        selection_failure,
    ) = _select_earliest_uniform_segment(
        theta=best.theta,
        time=time,
        fitted_trend=fitted_trend,
        displacement_scale=displacement_scale,
        displacement_origin=displacement_origin,
    )
    if selection_failure is not None:
        return _failure_result(
            station=station,
            fit_end_date=fit_end,
            failure_reason=selection_failure,
            **common_fit,
            **selection_audit,
        )
    assert uniform_segment is not None
    selection = {
        **selection_audit,
        "uniform_segment_start_date": pd.Timestamp(
            fit_frame.loc[uniform_segment.start_index, "date"]
        ),
        "uniform_segment_end_date": pd.Timestamp(
            fit_frame.loc[uniform_segment.end_index, "date"]
        ),
        "n_uniform_segment_trend_points": (
            uniform_segment.end_index - uniform_segment.start_index + 1
        ),
        "uniform_segment_covers_full_fit": (
            uniform_segment.start_index == 0
            and uniform_segment.end_index == len(fit_frame) - 1
        ),
    }
    selected_time = raw_time_days[
        uniform_segment.start_index : uniform_segment.end_index + 1
    ]
    selected_trend = fitted_trend[
        uniform_segment.start_index : uniform_segment.end_index + 1
    ]
    try:
        candidate_v = _linear_slope(selected_time, selected_trend)
        daily_rates = np.diff(selected_trend) / np.diff(selected_time)
        candidate_sigma = float(np.std(daily_rates, ddof=SIGMA_DDOF))
        candidate_v0 = float(
            max(1.5 * candidate_v, candidate_v + 2.0 * candidate_sigma)
        )
    except (FloatingPointError, OverflowError, ValueError):
        return _failure_result(
            station=station,
            fit_end_date=fit_end,
            failure_reason="nonfinite_candidate_statistics",
            **common_fit,
            **selection,
        )
    if (
        not math.isfinite(candidate_v)
        or not math.isfinite(candidate_sigma)
        or not math.isfinite(candidate_v0)
        or candidate_v <= 0.0
        or candidate_v0 <= 0.0
    ):
        return _failure_result(
            station=station,
            fit_end_date=fit_end,
            failure_reason="nonpositive_or_nonfinite_candidate_statistics",
            **common_fit,
            **selection,
        )

    profile = _TargetSlopeProfile(
        time=time,
        raw_time_days=raw_time_days,
        normalized_displacement=normalized_displacement,
        displacement_scale=displacement_scale,
        segment_start_index=uniform_segment.start_index,
        segment_end_index=uniform_segment.end_index,
        start_points=_profile_start_points(best.theta),
    )
    target_point = profile.evaluate(candidate_v)
    unconstrained_rss = 2.0 * float(best.cost)
    if target_point.rss is None:
        return _failure_result(
            station=station,
            fit_end_date=fit_end,
            failure_reason="profile_optimization_failed",
            **common_fit,
            **selection,
            **_profile_audit_values(profile),
        )
    profile_rss_min = min(unconstrained_rss, target_point.rss)
    if not math.isfinite(profile_rss_min) or profile_rss_min <= 0.0:
        return _failure_result(
            station=station,
            fit_end_date=fit_end,
            failure_reason="nonpositive_profile_rss_min",
            **common_fit,
            **selection,
            **_profile_audit_values(profile),
        )
    likelihood_quantile = float(chi2.ppf(PROFILE_CONFIDENCE_LEVEL, df=1))
    profile_rss_threshold = profile_rss_min * math.exp(
        likelihood_quantile / len(fit_frame)
    )
    if not math.isfinite(profile_rss_threshold) or profile_rss_threshold <= profile_rss_min:
        return _failure_result(
            station=station,
            fit_end_date=fit_end,
            failure_reason="nonfinite_profile_rss_threshold",
            **common_fit,
            **selection,
            profile_rss_min=profile_rss_min,
            **_profile_audit_values(profile),
        )
    lower, lower_failure = _profile_bound(
        profile=profile,
        target_slope=candidate_v,
        rss_threshold=profile_rss_threshold,
        direction=-1,
    )
    if lower_failure is not None:
        return _failure_result(
            station=station,
            fit_end_date=fit_end,
            failure_reason=lower_failure,
            **common_fit,
            **selection,
            profile_rss_min=profile_rss_min,
            profile_rss_threshold=profile_rss_threshold,
            **_profile_audit_values(profile),
        )
    upper, upper_failure = _profile_bound(
        profile=profile,
        target_slope=candidate_v,
        rss_threshold=profile_rss_threshold,
        direction=1,
    )
    if upper_failure is not None:
        return _failure_result(
            station=station,
            fit_end_date=fit_end,
            failure_reason=upper_failure,
            **common_fit,
            **selection,
            profile_rss_min=profile_rss_min,
            profile_rss_threshold=profile_rss_threshold,
            **_profile_audit_values(profile),
        )
    assert lower is not None
    assert upper is not None
    if not lower < candidate_v < upper:
        return _failure_result(
            station=station,
            fit_end_date=fit_end,
            failure_reason="invalid_profile_interval_order",
            **common_fit,
            **selection,
            profile_rss_min=profile_rss_min,
            profile_rss_threshold=profile_rss_threshold,
            **_profile_audit_values(profile),
        )
    return MvifInitialSlopeResult(
        station=station,
        status=FIT_STATUS_CANDIDATE,
        failure_reason=None,
        fit_end_date=fit_end,
        candidate_v_mm_per_day=candidate_v,
        candidate_sigma_mm_per_day=candidate_sigma,
        candidate_v0_mm_per_day=candidate_v0,
        profile_rss_min=profile_rss_min,
        profile_rss_threshold=profile_rss_threshold,
        profile_v_lower_mm_per_day=lower,
        profile_v_upper_mm_per_day=upper,
        **common_fit,
        **selection,
        **_profile_audit_values(profile),
    )


__all__ = [
    "CONVEXITY_WINDOW_DAYS",
    "FIT_STATUS_CANDIDATE",
    "FIT_STATUS_FAILED",
    "MvifInitialSlopeResult",
    "PROFILE_BISECTION_ITERATIONS",
    "PROFILE_CONFIDENCE_LEVEL",
    "PROFILE_EXPANSION_FACTOR",
    "PROFILE_MAX_EXPANSIONS",
    "PROFILE_MAX_FUNCTION_EVALUATIONS",
    "SIGMA_DDOF",
    "TF_PARAMETER_STATUS",
    "UNIFORM_L_LOWER",
    "UNIFORM_L_UPPER",
    "select_mvif_initial_stable_slope",
]
