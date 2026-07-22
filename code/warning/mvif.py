"""Fit-only MVIF trend diagnostics with an explicit finite-``t_f`` gate.

The designated Word thesis uses an MVIF trend-displacement curve to obtain the
initial stable slope of step-type reservoir landslides.  Its displayed
``s0``/``C`` notation has been reconciled in the draft protocol as

``s(t) = A * ln((t_f - B * t) / (t_f - t)) + C``.

This module implements only the numerical fit and identifiability screen.  It
does not choose an initial-slope window, calculate ``V0``, assign a velocity
level, or emit a warning result.  A finite failure time must survive a
deterministic multi-start audit; otherwise the result is explicitly failed and
cannot feed downstream warning diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import least_squares


REQUIRED_COLUMNS = ("date", "displacement", "displacement_valid")
N_FIT_PARAMETERS = 4
FIT_STATUS_CANDIDATE = "candidate_finite_tf"
FIT_STATUS_FAILED = "failed"

# These are deterministic numerical starting points, not a stable-segment rule
# or a warning threshold.  ``tau`` is the positive normalized excess of t_f
# beyond the last fit observation and ``gap`` keeps t_f - B*t positive over the
# observed normalized time range [0, 1].
INITIAL_TAU_EXCESS = (0.25, 4.0)
INITIAL_GAP_FACTORS = (0.25, 1.0, 4.0)
NUMERICAL_RELATIVE_TOLERANCE = math.sqrt(np.finfo(float).eps)
MAX_FUNCTION_EVALUATIONS_PER_START = 5_000
_INVALID_RESIDUAL = 1e100


@dataclass(frozen=True)
class MvifFitResult:
    """One fit-only MVIF result, including a non-formal failure audit."""

    station: str
    status: str
    failure_reason: str | None
    fit_start_date: pd.Timestamp | None
    fit_end_date: pd.Timestamp
    n_fit_rows: int
    time_span_days: float | None
    n_multistart_attempts: int
    n_converged_fits: int
    n_near_optimal_fits: int
    jacobian_rank: int | None
    objective_cost: float | None
    tf_log_excess_duration_min: float | None
    tf_log_excess_duration_max: float | None
    a: float | None
    b: float | None
    c: float | None
    tf_elapsed_days: float | None

    def to_record(self) -> dict[str, Any]:
        """Return only fit diagnostics; deliberately omit V0 and warning fields."""

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
            "n_near_optimal_fits": self.n_near_optimal_fits,
            "jacobian_rank": self.jacobian_rank,
            "n_fit_parameters": N_FIT_PARAMETERS,
            "objective_cost": self.objective_cost,
            "tf_log_excess_duration_min": self.tf_log_excess_duration_min,
            "tf_log_excess_duration_max": self.tf_log_excess_duration_max,
            "A": self.a,
            "B": self.b,
            "C": self.c,
            "tf_elapsed_days": self.tf_elapsed_days,
        }


@dataclass(frozen=True)
class _FitAttempt:
    """One finite least-squares attempt in a domain-safe parameterization."""

    failure_reason: str | None
    cost: float | None
    theta: np.ndarray | None
    jacobian_rank: int | None


def _date_to_iso(value: pd.Timestamp | None) -> str | None:
    if value is None or pd.isna(value):
        return None
    return value.strftime("%Y-%m-%d")


def _normalise_fit_end_date(fit_end_date) -> pd.Timestamp:
    value = pd.to_datetime(fit_end_date, errors="coerce")
    if pd.isna(value):
        raise ValueError("fit_end_date must be a valid timestamp")
    value = pd.Timestamp(value)
    if value.tz is not None:
        value = value.tz_localize(None)
    return value


def _failure_result(
    *,
    station: str,
    failure_reason: str,
    fit_end_date: pd.Timestamp,
    fit_start_date: pd.Timestamp | None = None,
    n_fit_rows: int = 0,
    time_span_days: float | None = None,
    n_multistart_attempts: int = 0,
    n_converged_fits: int = 0,
    n_near_optimal_fits: int = 0,
    jacobian_rank: int | None = None,
    objective_cost: float | None = None,
    tf_log_excess_duration_min: float | None = None,
    tf_log_excess_duration_max: float | None = None,
) -> MvifFitResult:
    return MvifFitResult(
        station=station,
        status=FIT_STATUS_FAILED,
        failure_reason=failure_reason,
        fit_start_date=fit_start_date,
        fit_end_date=fit_end_date,
        n_fit_rows=n_fit_rows,
        time_span_days=time_span_days,
        n_multistart_attempts=n_multistart_attempts,
        n_converged_fits=n_converged_fits,
        n_near_optimal_fits=n_near_optimal_fits,
        jacobian_rank=jacobian_rank,
        objective_cost=objective_cost,
        tf_log_excess_duration_min=tf_log_excess_duration_min,
        tf_log_excess_duration_max=tf_log_excess_duration_max,
        a=None,
        b=None,
        c=None,
        tf_elapsed_days=None,
    )


def _prepare_fit_frame(
    displacement: pd.DataFrame,
    *,
    fit_end_date: pd.Timestamp,
) -> tuple[pd.DataFrame | None, str | None]:
    if not isinstance(displacement, pd.DataFrame):
        raise ValueError("displacement must be a DataFrame")
    missing = set(REQUIRED_COLUMNS).difference(displacement.columns)
    if missing:
        raise ValueError(
            "displacement is missing required columns: "
            f"{sorted(missing)}"
        )

    frame = displacement.loc[:, REQUIRED_COLUMNS].copy().reset_index(drop=True)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["displacement"] = pd.to_numeric(frame["displacement"], errors="coerce")
    if frame["date"].isna().any():
        return None, "invalid_date_in_input"
    if not frame["date"].is_monotonic_increasing:
        return None, "dates_not_strictly_increasing"

    fit_frame = frame.loc[frame["date"] <= fit_end_date].reset_index(drop=True)
    if fit_frame.empty:
        return None, "no_rows_in_fit_period"
    if not fit_frame["displacement_valid"].map(_is_valid_displacement_flag).all():
        return None, "invalid_displacement_flag_in_fit_period"
    if not np.isfinite(fit_frame["displacement"].to_numpy(dtype=float)).all():
        return None, "nonfinite_displacement_in_fit_period"
    if fit_frame["date"].duplicated().any():
        return None, "duplicate_date_in_fit_period"
    if len(fit_frame) <= N_FIT_PARAMETERS:
        return None, "insufficient_fit_rows"
    return fit_frame, None


def _is_valid_displacement_flag(value: object) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1"}
    if isinstance(value, (int, np.integer)):
        return int(value) == 1
    return False


def _log_nonnegative(values: np.ndarray) -> np.ndarray:
    """Return log(values) while representing an exact zero as -infinity."""

    output = np.full_like(values, -np.inf, dtype=float)
    positive = values > 0.0
    output[positive] = np.log(values[positive])
    return output


def _evaluate_normalized_mvif(theta: np.ndarray, time: np.ndarray) -> np.ndarray:
    """Evaluate the source equation without artificial physical bounds.

    ``theta = (A, log_gap, log_tau, C)`` uses the normalized fit time range
    ``[0, 1]``.  The transform defines ``t_f = 1 + exp(log_tau)`` and
    ``B = t_f - exp(log_gap)``.  It therefore represents every finite set of
    source parameters whose logarithm is defined over the observed time range,
    while avoiding a user-invented maximum failure horizon.
    """

    a, log_gap, log_tau, c = theta
    if not np.isfinite(theta).all():
        raise FloatingPointError("nonfinite normalized MVIF parameters")
    with np.errstate(over="raise", invalid="raise", under="ignore"):
        log_tf = np.logaddexp(0.0, log_tau)
        log_numerator = np.logaddexp(
            log_tf + _log_nonnegative(1.0 - time),
            log_gap + _log_nonnegative(time),
        )
        log_denominator = np.logaddexp(
            _log_nonnegative(1.0 - time),
            log_tau,
        )
        output = a * (log_numerator - log_denominator) + c
    if not np.isfinite(output).all():
        raise FloatingPointError("nonfinite normalized MVIF output")
    return output


def _residual_function(
    *,
    time: np.ndarray,
    displacement: np.ndarray,
):
    def residual(theta: np.ndarray) -> np.ndarray:
        try:
            values = _evaluate_normalized_mvif(theta, time) - displacement
        except (FloatingPointError, OverflowError, ValueError):
            return np.full_like(displacement, _INVALID_RESIDUAL)
        if not np.isfinite(values).all():
            return np.full_like(displacement, _INVALID_RESIDUAL)
        return values

    return residual


def _initial_thetas() -> tuple[np.ndarray, ...]:
    starts = []
    for tau in INITIAL_TAU_EXCESS:
        tf = 1.0 + tau
        for gap_factor in INITIAL_GAP_FACTORS:
            starts.append(
                np.array(
                    [1.0, math.log(gap_factor * tf), math.log(tau), 0.0],
                    dtype=float,
                )
            )
    return tuple(starts)


def _run_multistart(
    *,
    time: np.ndarray,
    displacement: np.ndarray,
) -> list[_FitAttempt]:
    residual = _residual_function(time=time, displacement=displacement)
    attempts: list[_FitAttempt] = []
    for initial_theta in _initial_thetas():
        try:
            fit = least_squares(
                residual,
                initial_theta,
                method="trf",
                jac="3-point",
                x_scale="jac",
                max_nfev=MAX_FUNCTION_EVALUATIONS_PER_START,
            )
        except (FloatingPointError, OverflowError, ValueError, np.linalg.LinAlgError):
            attempts.append(
                _FitAttempt(
                    failure_reason="mvif_optimization_failed",
                    cost=None,
                    theta=None,
                    jacobian_rank=None,
                )
            )
            continue
        if not fit.success:
            attempts.append(
                _FitAttempt(
                    failure_reason="mvif_optimization_failed",
                    cost=None,
                    theta=None,
                    jacobian_rank=None,
                )
            )
            continue
        if (
            not np.isfinite(fit.cost)
            or not np.isfinite(fit.x).all()
            or not np.isfinite(fit.jac).all()
        ):
            attempts.append(
                _FitAttempt(
                    failure_reason="nonfinite_optimizer_output",
                    cost=None,
                    theta=None,
                    jacobian_rank=None,
                )
            )
            continue
        try:
            rank = int(np.linalg.matrix_rank(fit.jac))
        except np.linalg.LinAlgError:
            attempts.append(
                _FitAttempt(
                    failure_reason="jacobian_rank_unavailable",
                    cost=None,
                    theta=None,
                    jacobian_rank=None,
                )
            )
            continue
        attempts.append(
            _FitAttempt(
                failure_reason=None,
                cost=float(fit.cost),
                theta=np.asarray(fit.x, dtype=float),
                jacobian_rank=rank,
            )
        )
    return attempts


def _physical_parameters(
    *,
    theta: np.ndarray,
    displacement_scale: float,
    displacement_origin: float,
    time_span_days: float,
) -> tuple[float, float, float, float]:
    a_normalized, log_gap, log_tau, c_normalized = theta
    tau = math.exp(float(log_tau))
    gap = math.exp(float(log_gap))
    tf_normalized = 1.0 + tau
    a = float(a_normalized * displacement_scale)
    b = float(tf_normalized - gap)
    c = float(c_normalized * displacement_scale + displacement_origin)
    tf_elapsed_days = float(tf_normalized * time_span_days)
    parameters = (a, b, c, tf_elapsed_days)
    if not all(math.isfinite(value) for value in parameters):
        raise FloatingPointError("nonfinite physical MVIF parameters")
    return parameters


def _costs_are_numerically_indistinguishable(
    *,
    cost: float,
    best_cost: float,
) -> bool:
    scale = max(1.0, abs(cost), abs(best_cost))
    return abs(cost - best_cost) <= NUMERICAL_RELATIVE_TOLERANCE * scale


def fit_mvif_trend(
    displacement: pd.DataFrame,
    *,
    station: str,
    fit_end_date,
) -> MvifFitResult:
    """Fit one station's fit-period cumulative displacement with the MVIF curve.

    The result is accepted only when equally good deterministic starts agree on
    a finite failure time to numerical precision and retain a full-column-rank
    residual Jacobian.  This is a conservative numerical identifiability gate,
    not a stable-slope-selection or warning threshold rule.
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
            failure_reason=preparation_failure,
            fit_end_date=fit_end,
        )
    assert fit_frame is not None

    fit_start = pd.Timestamp(fit_frame.loc[0, "date"])
    time_span_days = float(
        (
            pd.Timestamp(fit_frame.loc[len(fit_frame) - 1, "date"]) - fit_start
        ).total_seconds()
        / 86_400.0
    )
    if not math.isfinite(time_span_days) or time_span_days <= 0.0:
        return _failure_result(
            station=station,
            failure_reason="nonpositive_fit_time_span",
            fit_start_date=fit_start,
            fit_end_date=fit_end,
            n_fit_rows=len(fit_frame),
            time_span_days=time_span_days,
        )

    raw_time = (
        fit_frame["date"].sub(fit_start).dt.total_seconds().to_numpy(dtype=float)
        / 86_400.0
    )
    time = raw_time / time_span_days
    raw_displacement = fit_frame["displacement"].to_numpy(dtype=float)
    displacement_origin = float(raw_displacement[0])
    displacement_scale = float(
        max(np.ptp(raw_displacement), np.std(raw_displacement), 1.0)
    )
    normalized_displacement = (
        raw_displacement - displacement_origin
    ) / displacement_scale

    attempts = _run_multistart(time=time, displacement=normalized_displacement)
    converged_attempts = [
        attempt for attempt in attempts if attempt.failure_reason is None
    ]
    failed_attempts = [
        attempt for attempt in attempts if attempt.failure_reason is not None
    ]
    if failed_attempts:
        failure_reason = failed_attempts[0].failure_reason
        assert failure_reason is not None
        finite_costs = [
            attempt.cost
            for attempt in converged_attempts
            if attempt.cost is not None
        ]
        return _failure_result(
            station=station,
            failure_reason=failure_reason,
            fit_start_date=fit_start,
            fit_end_date=fit_end,
            n_fit_rows=len(fit_frame),
            time_span_days=time_span_days,
            n_multistart_attempts=len(_initial_thetas()),
            n_converged_fits=len(converged_attempts),
            objective_cost=min(finite_costs) if finite_costs else None,
        )

    best = min(
        converged_attempts,
        key=lambda attempt: float(attempt.cost),
    )
    near_optimal = [
        attempt
        for attempt in converged_attempts
        if _costs_are_numerically_indistinguishable(
            cost=float(attempt.cost),
            best_cost=float(best.cost),
        )
    ]
    try:
        # ``log_tau`` is normalized by the fit span; report and compare the
        # physical-day duration specified by the protocol.  Adding this common
        # station-level scale leaves the multi-start agreement test unchanged.
        near_log_tf_excess_durations = [
            float(attempt.theta[2]) + math.log(time_span_days)
            for attempt in near_optimal
            if attempt.theta is not None
        ]
        for attempt in near_optimal:
            assert attempt.theta is not None
            _physical_parameters(
                theta=attempt.theta,
                displacement_scale=displacement_scale,
                displacement_origin=displacement_origin,
                time_span_days=time_span_days,
            )
        physical = _physical_parameters(
            theta=np.asarray(best.theta, dtype=float),
            displacement_scale=displacement_scale,
            displacement_origin=displacement_origin,
            time_span_days=time_span_days,
        )
    except (FloatingPointError, OverflowError, ValueError):
        return _failure_result(
            station=station,
            failure_reason="nonfinite_tf_candidate",
            fit_start_date=fit_start,
            fit_end_date=fit_end,
            n_fit_rows=len(fit_frame),
            time_span_days=time_span_days,
            n_multistart_attempts=len(_initial_thetas()),
            n_converged_fits=len(attempts),
            n_near_optimal_fits=len(near_optimal),
            jacobian_rank=best.jacobian_rank,
            objective_cost=best.cost,
        )

    min_log_tf_excess_duration = min(near_log_tf_excess_durations)
    max_log_tf_excess_duration = max(near_log_tf_excess_durations)
    common = {
        "station": station,
        "fit_start_date": fit_start,
        "fit_end_date": fit_end,
        "n_fit_rows": len(fit_frame),
        "time_span_days": time_span_days,
        "n_multistart_attempts": len(_initial_thetas()),
        "n_converged_fits": len(converged_attempts),
        "n_near_optimal_fits": len(near_optimal),
        "jacobian_rank": best.jacobian_rank,
        "objective_cost": best.cost,
        "tf_log_excess_duration_min": min_log_tf_excess_duration,
        "tf_log_excess_duration_max": max_log_tf_excess_duration,
    }
    if any(
        attempt.jacobian_rank is None
        or attempt.jacobian_rank < N_FIT_PARAMETERS
        for attempt in near_optimal
    ):
        return _failure_result(
            failure_reason="tf_unidentifiable_rank_deficient",
            **common,
        )
    if (
        max_log_tf_excess_duration - min_log_tf_excess_duration
        > NUMERICAL_RELATIVE_TOLERANCE
    ):
        return _failure_result(
            failure_reason="tf_multistart_unstable",
            **common,
        )

    a, b, c, tf_elapsed_days = physical
    return MvifFitResult(
        status=FIT_STATUS_CANDIDATE,
        failure_reason=None,
        a=a,
        b=b,
        c=c,
        tf_elapsed_days=tf_elapsed_days,
        **common,
    )


def evaluate_fitted_mvif_trend(
    result: MvifFitResult,
    dates,
) -> np.ndarray:
    """Evaluate a *strictly accepted* MVIF fit on dates inside its fit period.

    This is a narrow read-only bridge for diagnostics that need the source
    trend itself after :func:`fit_mvif_trend` has passed every finite-``t_f``
    gate.  It deliberately refuses failed fits and dates outside the selected
    fit history, so callers cannot reinterpret a rejected fit as a trend or
    extrapolate it into a warning result.
    """

    if result.status != FIT_STATUS_CANDIDATE:
        raise ValueError("MVIF trend evaluation requires a strictly accepted fit")
    parameters = (result.a, result.b, result.c, result.tf_elapsed_days)
    if any(value is None or not math.isfinite(float(value)) for value in parameters):
        raise ValueError("accepted MVIF fit has nonfinite physical parameters")
    if result.fit_start_date is None or result.time_span_days is None:
        raise ValueError("accepted MVIF fit is missing its fit time range")

    parsed_dates = pd.to_datetime(dates, errors="coerce")
    if getattr(parsed_dates, "isna")().any():
        raise ValueError("MVIF trend evaluation dates must be valid timestamps")
    date_index = pd.DatetimeIndex(parsed_dates)
    if date_index.tz is not None:
        date_index = date_index.tz_localize(None)
    elapsed_days = (
        (date_index - pd.Timestamp(result.fit_start_date)).total_seconds()
        / 86_400.0
    ).to_numpy(dtype=float)
    time_span_days = float(result.time_span_days)
    tolerance = math.sqrt(np.finfo(float).eps) * max(1.0, time_span_days)
    if (
        not np.isfinite(elapsed_days).all()
        or (elapsed_days < -tolerance).any()
        or (elapsed_days > time_span_days + tolerance).any()
    ):
        raise ValueError("MVIF trend evaluation dates must remain inside the fit period")

    a, b, c, tf_elapsed_days = (float(value) for value in parameters)
    numerator = tf_elapsed_days - b * elapsed_days
    denominator = tf_elapsed_days - elapsed_days
    if (numerator <= 0.0).any() or (denominator <= 0.0).any():
        raise ValueError("accepted MVIF parameters are outside the logarithm domain")
    with np.errstate(divide="raise", invalid="raise", over="raise"):
        values = a * np.log(numerator / denominator) + c
    if not np.isfinite(values).all():
        raise ValueError("accepted MVIF trend evaluation is nonfinite")
    return np.asarray(values, dtype=float)


__all__ = [
    "FIT_STATUS_CANDIDATE",
    "FIT_STATUS_FAILED",
    "INITIAL_GAP_FACTORS",
    "INITIAL_TAU_EXCESS",
    "MAX_FUNCTION_EVALUATIONS_PER_START",
    "MvifFitResult",
    "N_FIT_PARAMETERS",
    "NUMERICAL_RELATIVE_TOLERANCE",
    "REQUIRED_COLUMNS",
    "evaluate_fitted_mvif_trend",
    "fit_mvif_trend",
]
