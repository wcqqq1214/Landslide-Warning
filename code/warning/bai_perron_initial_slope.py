"""Draft Bai--Perron selection of an MVIF-trend initial-slope candidate.

The specified Word thesis uses the initial stable slope of a fitted MVIF
trend-displacement curve as the input to its ``V0`` formula, but does not
define an automatic stable-segment algorithm.  This module therefore records a
project adaptation, not a source-prescribed warning rule: BIC selects a
multiple-structural-break, piecewise ordinary-least-squares model and only its
first segment can be a candidate when the immediately following segment has a
higher positive slope.

The module accepts an already fitted trend.  It never fits MVIF parameters,
calculates ``sigma`` or ``V0``, assigns a warning level, or emits a formal
warning result.  The runner applies a separate strict MVIF finite-``t_f``
precondition before this selector is called on project data.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np
import pandas as pd


REQUIRED_TREND_COLUMNS = ("date", "trend_displacement")
MIN_SEGMENT_OBSERVATIONS = 30
MAX_SEGMENTS = 6
SEGMENT_COUNT_SELECTION = "bic"
REGRESSION_PARAMETERS_PER_SEGMENT = 2
SEGMENTATION_ALGORITHM = "Bai_Perron_multiple_structural_change_piecewise_unconstrained_OLS"
BIC_FORMULA = "n*ln(max(RSS/n,eps*max(1,var(trend_displacement))))+2*K*ln(n)"
CONTINUITY_CONSTRAINT = "none"
SELECTION_RULE = "bic_selected_bai_perron_piecewise_ols_first_accelerating_segment"
FIRST_SEGMENT_ACCEPTANCE = (
    "require_positive_initial_slope_and_immediately_following_slope_"
    "greater_than_initial_slope"
)
FIT_STATUS_CANDIDATE = "candidate_bai_perron_initial_slope"
FIT_STATUS_FAILED = "failed"


@dataclass(frozen=True)
class _SegmentModel:
    """One unconstrained intercept-and-slope OLS segment."""

    start_index: int
    end_index: int
    intercept: float
    slope: float
    sse: float


@dataclass(frozen=True)
class BaiPerronInitialSlopeResult:
    """Auditable draft outcome for one station's fitted MVIF trend."""

    station: str
    status: str
    failure_reason: str | None
    fit_start_date: pd.Timestamp | None
    fit_end_date: pd.Timestamp
    n_fit_rows: int
    min_segment_observations: int
    max_segments_considered: int
    selected_segment_count: int | None = None
    selected_bic: float | None = None
    one_segment_bic: float | None = None
    initial_segment_start_date: pd.Timestamp | None = None
    initial_segment_end_date: pd.Timestamp | None = None
    initial_segment_n_observations: int | None = None
    initial_segment_covers_full_fit: bool | None = None
    first_break_date: pd.Timestamp | None = None
    following_segment_n_observations: int | None = None
    following_segment_v_mm_per_day: float | None = None
    candidate_v_mm_per_day: float | None = None

    def to_record(self) -> dict[str, Any]:
        """Return a draft audit record without sigma, V0, or warning fields."""

        return {
            "station": self.station,
            "fit_status": self.status,
            "failure_reason": self.failure_reason,
            "fit_start_date": _date_to_iso(self.fit_start_date),
            "fit_end_date": _date_to_iso(self.fit_end_date),
            "n_fit_rows": self.n_fit_rows,
            "min_segment_observations": self.min_segment_observations,
            "max_segments_considered": self.max_segments_considered,
            "segment_count_selection": SEGMENT_COUNT_SELECTION,
            "regression_parameters_per_segment": REGRESSION_PARAMETERS_PER_SEGMENT,
            "selected_segment_count": self.selected_segment_count,
            "selected_bic": self.selected_bic,
            "one_segment_bic": self.one_segment_bic,
            "initial_segment_start_date": _date_to_iso(
                self.initial_segment_start_date
            ),
            "initial_segment_end_date": _date_to_iso(self.initial_segment_end_date),
            "initial_segment_n_observations": self.initial_segment_n_observations,
            "initial_segment_covers_full_fit": self.initial_segment_covers_full_fit,
            "first_break_date": _date_to_iso(self.first_break_date),
            "following_segment_n_observations": self.following_segment_n_observations,
            "following_segment_v_mm_per_day": self.following_segment_v_mm_per_day,
            "candidate_v_mm_per_day": self.candidate_v_mm_per_day,
        }


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
    max_segments_considered: int = 0,
    selected_segment_count: int | None = None,
    selected_bic: float | None = None,
    one_segment_bic: float | None = None,
    initial_segment: _SegmentModel | None = None,
    following_segment: _SegmentModel | None = None,
    dates: pd.Series | None = None,
) -> BaiPerronInitialSlopeResult:
    initial_start_date = None
    initial_end_date = None
    initial_count = None
    initial_covers_full_fit = None
    first_break_date = None
    following_count = None
    following_slope = None
    if initial_segment is not None and dates is not None:
        initial_start_date = pd.Timestamp(dates.iloc[initial_segment.start_index])
        initial_end_date = pd.Timestamp(dates.iloc[initial_segment.end_index - 1])
        initial_count = initial_segment.end_index - initial_segment.start_index
        initial_covers_full_fit = initial_segment.end_index == n_fit_rows
    if following_segment is not None and dates is not None:
        first_break_date = pd.Timestamp(dates.iloc[following_segment.start_index])
        following_count = following_segment.end_index - following_segment.start_index
        following_slope = following_segment.slope
    return BaiPerronInitialSlopeResult(
        station=station,
        status=FIT_STATUS_FAILED,
        failure_reason=failure_reason,
        fit_start_date=fit_start_date,
        fit_end_date=fit_end_date,
        n_fit_rows=n_fit_rows,
        min_segment_observations=MIN_SEGMENT_OBSERVATIONS,
        max_segments_considered=max_segments_considered,
        selected_segment_count=selected_segment_count,
        selected_bic=selected_bic,
        one_segment_bic=one_segment_bic,
        initial_segment_start_date=initial_start_date,
        initial_segment_end_date=initial_end_date,
        initial_segment_n_observations=initial_count,
        initial_segment_covers_full_fit=initial_covers_full_fit,
        first_break_date=first_break_date,
        following_segment_n_observations=following_count,
        following_segment_v_mm_per_day=following_slope,
        candidate_v_mm_per_day=None,
    )


def _prepare_trend_frame(
    trend: pd.DataFrame,
    *,
    fit_end_date: pd.Timestamp,
) -> tuple[pd.DataFrame | None, str | None]:
    if not isinstance(trend, pd.DataFrame):
        raise ValueError("trend must be a DataFrame")
    missing = set(REQUIRED_TREND_COLUMNS).difference(trend.columns)
    if missing:
        raise ValueError(f"trend is missing required columns: {sorted(missing)}")

    frame = trend.loc[:, REQUIRED_TREND_COLUMNS].copy().reset_index(drop=True)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["trend_displacement"] = pd.to_numeric(
        frame["trend_displacement"], errors="coerce"
    )
    if frame["date"].isna().any():
        return None, "invalid_date_in_trend"
    if not frame["date"].is_monotonic_increasing:
        return None, "dates_not_strictly_increasing"

    fit_frame = frame.loc[frame["date"] <= fit_end_date].reset_index(drop=True)
    if fit_frame.empty:
        return None, "no_rows_in_fit_period"
    if fit_frame["date"].duplicated().any():
        return None, "duplicate_date_in_fit_period"
    values = fit_frame["trend_displacement"].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        return None, "nonfinite_trend_displacement_in_fit_period"
    return fit_frame, None


def _prefix_sums(values: np.ndarray) -> np.ndarray:
    return np.concatenate((np.array([0.0]), np.cumsum(values, dtype=float)))


def _fit_segment(
    *,
    prefix_x: np.ndarray,
    prefix_y: np.ndarray,
    prefix_xx: np.ndarray,
    prefix_xy: np.ndarray,
    prefix_yy: np.ndarray,
    start_index: int,
    end_index: int,
) -> _SegmentModel:
    """Fit one OLS segment from cumulative sums in constant time."""

    n_observations = end_index - start_index
    if n_observations < 2:
        raise ValueError("a linear segment requires at least two observations")
    sum_x = float(prefix_x[end_index] - prefix_x[start_index])
    sum_y = float(prefix_y[end_index] - prefix_y[start_index])
    sum_xx = float(prefix_xx[end_index] - prefix_xx[start_index])
    sum_xy = float(prefix_xy[end_index] - prefix_xy[start_index])
    sum_yy = float(prefix_yy[end_index] - prefix_yy[start_index])
    denominator = n_observations * sum_xx - sum_x * sum_x
    if not math.isfinite(denominator) or denominator <= 0.0:
        raise ValueError("nonpositive_segment_time_variation")
    slope = (n_observations * sum_xy - sum_x * sum_y) / denominator
    intercept = (sum_y - slope * sum_x) / n_observations
    sse = sum_yy - intercept * sum_y - slope * sum_xy
    if not all(math.isfinite(value) for value in (intercept, slope, sse)):
        raise ValueError("nonfinite_segment_ols_result")
    numerical_scale = max(
        1.0,
        abs(sum_yy),
        abs(intercept * sum_y),
        abs(slope * sum_xy),
    )
    if sse < 0.0 and abs(sse) <= np.finfo(float).eps * numerical_scale * 32.0:
        sse = 0.0
    if sse < 0.0:
        raise ValueError("negative_segment_sse")
    return _SegmentModel(
        start_index=start_index,
        end_index=end_index,
        intercept=float(intercept),
        slope=float(slope),
        sse=float(sse),
    )


def _segment_cost_matrix(
    time_days: np.ndarray,
    trend_displacement: np.ndarray,
) -> tuple[np.ndarray, tuple[np.ndarray, ...]]:
    """Return eligible segment SSEs plus reusable cumulative sums."""

    n_rows = len(time_days)
    prefix_sums = (
        _prefix_sums(time_days),
        _prefix_sums(trend_displacement),
        _prefix_sums(time_days * time_days),
        _prefix_sums(time_days * trend_displacement),
        _prefix_sums(trend_displacement * trend_displacement),
    )
    costs = np.full((n_rows + 1, n_rows + 1), np.inf, dtype=float)
    for start_index in range(0, n_rows - MIN_SEGMENT_OBSERVATIONS + 1):
        for end_index in range(
            start_index + MIN_SEGMENT_OBSERVATIONS,
            n_rows + 1,
        ):
            model = _fit_segment(
                prefix_x=prefix_sums[0],
                prefix_y=prefix_sums[1],
                prefix_xx=prefix_sums[2],
                prefix_xy=prefix_sums[3],
                prefix_yy=prefix_sums[4],
                start_index=start_index,
                end_index=end_index,
            )
            costs[start_index, end_index] = model.sse
    return costs, prefix_sums


def _bic_for_segment_count(
    *,
    rss: float,
    n_rows: int,
    segment_count: int,
    trend_variance: float,
) -> float:
    """Return the declared draft BIC for a segmented linear regression.

    Each segment has one intercept and one slope.  A floating-point floor lets
    an exactly linear synthetic segment be compared without changing the
    deterministic preference for the simpler model.
    """

    variance_floor = np.finfo(float).eps * max(1.0, trend_variance)
    residual_variance = max(rss / n_rows, variance_floor)
    return float(
        n_rows * math.log(residual_variance)
        + REGRESSION_PARAMETERS_PER_SEGMENT * segment_count * math.log(n_rows)
    )


def _select_bic_segmented_model(
    *,
    time_days: np.ndarray,
    trend_displacement: np.ndarray,
) -> tuple[list[_SegmentModel], int, float, float]:
    """Use exact dynamic programming for each allowed Bai--Perron partition."""

    n_rows = len(time_days)
    max_segments = min(MAX_SEGMENTS, n_rows // MIN_SEGMENT_OBSERVATIONS)
    if max_segments < 1:
        raise ValueError("insufficient_rows_for_bai_perron_break_selection")
    costs, prefix_sums = _segment_cost_matrix(time_days, trend_displacement)
    dynamic_costs = np.full((max_segments + 1, n_rows + 1), np.inf, dtype=float)
    backpointers = np.full((max_segments + 1, n_rows + 1), -1, dtype=int)
    dynamic_costs[0, 0] = 0.0

    for segment_count in range(1, max_segments + 1):
        first_end = segment_count * MIN_SEGMENT_OBSERVATIONS
        for end_index in range(first_end, n_rows + 1):
            first_previous_end = (segment_count - 1) * MIN_SEGMENT_OBSERVATIONS
            last_previous_end = end_index - MIN_SEGMENT_OBSERVATIONS
            previous_ends = np.arange(
                first_previous_end,
                last_previous_end + 1,
                dtype=int,
            )
            candidate_costs = (
                dynamic_costs[segment_count - 1, previous_ends]
                + costs[previous_ends, end_index]
            )
            best_offset = int(np.argmin(candidate_costs))
            best_cost = float(candidate_costs[best_offset])
            if math.isfinite(best_cost):
                dynamic_costs[segment_count, end_index] = best_cost
                backpointers[segment_count, end_index] = int(
                    previous_ends[best_offset]
                )

    trend_variance = float(np.var(trend_displacement))
    bic_by_segment_count: dict[int, float] = {}
    for segment_count in range(1, max_segments + 1):
        rss = float(dynamic_costs[segment_count, n_rows])
        if math.isfinite(rss):
            bic_by_segment_count[segment_count] = _bic_for_segment_count(
                rss=rss,
                n_rows=n_rows,
                segment_count=segment_count,
                trend_variance=trend_variance,
            )
    if not bic_by_segment_count:
        raise ValueError("no_bai_perron_partition")
    selected_segment_count = min(
        bic_by_segment_count,
        key=lambda count: (bic_by_segment_count[count], count),
    )

    boundaries = [n_rows]
    end_index = n_rows
    for segment_count in range(selected_segment_count, 0, -1):
        start_index = int(backpointers[segment_count, end_index])
        if start_index < 0:
            raise ValueError("bai_perron_backpointer_unavailable")
        boundaries.append(start_index)
        end_index = start_index
    boundaries.reverse()
    models = []
    for start_index, end_index in zip(boundaries[:-1], boundaries[1:]):
        models.append(
            _fit_segment(
                prefix_x=prefix_sums[0],
                prefix_y=prefix_sums[1],
                prefix_xx=prefix_sums[2],
                prefix_xy=prefix_sums[3],
                prefix_yy=prefix_sums[4],
                start_index=start_index,
                end_index=end_index,
            )
        )
    return (
        models,
        selected_segment_count,
        bic_by_segment_count[selected_segment_count],
        bic_by_segment_count[1],
    )


def select_bai_perron_initial_stable_slope(
    trend: pd.DataFrame,
    *,
    station: str,
    fit_end_date,
) -> BaiPerronInitialSlopeResult:
    """Select a non-formal initial stable-slope candidate from an MVIF trend.

    The first segment is never accepted when the BIC selects only one segment.
    This intentionally prevents a smooth full-fit trend from being relabeled as
    a physically identified initial stable stage.
    """

    station = str(station).strip()
    if not station:
        raise ValueError("station must be nonempty")
    fit_end = _normalise_fit_end_date(fit_end_date)
    fit_frame, preparation_failure = _prepare_trend_frame(
        trend,
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
    n_rows = len(fit_frame)
    max_segments = min(MAX_SEGMENTS, n_rows // MIN_SEGMENT_OBSERVATIONS)
    if n_rows < 2 * MIN_SEGMENT_OBSERVATIONS:
        return _failure_result(
            station=station,
            failure_reason="insufficient_rows_for_bai_perron_break_selection",
            fit_start_date=fit_start,
            fit_end_date=fit_end,
            n_fit_rows=n_rows,
            max_segments_considered=max_segments,
        )

    time_days = (
        fit_frame["date"].sub(fit_start).dt.total_seconds().to_numpy(dtype=float)
        / 86_400.0
    )
    trend_displacement = fit_frame["trend_displacement"].to_numpy(dtype=float)
    try:
        models, selected_count, selected_bic, one_segment_bic = (
            _select_bic_segmented_model(
                time_days=time_days,
                trend_displacement=trend_displacement,
            )
        )
    except ValueError as exc:
        return _failure_result(
            station=station,
            failure_reason=str(exc),
            fit_start_date=fit_start,
            fit_end_date=fit_end,
            n_fit_rows=n_rows,
            max_segments_considered=max_segments,
        )

    initial_segment = models[0]
    following_segment = models[1] if len(models) >= 2 else None
    common = {
        "station": station,
        "fit_start_date": fit_start,
        "fit_end_date": fit_end,
        "n_fit_rows": n_rows,
        "max_segments_considered": max_segments,
        "selected_segment_count": selected_count,
        "selected_bic": selected_bic,
        "one_segment_bic": one_segment_bic,
        "initial_segment": initial_segment,
        "following_segment": following_segment,
        "dates": fit_frame["date"],
    }
    if selected_count == 1:
        return _failure_result(
            failure_reason="no_structural_break_before_fit_cutoff",
            **common,
        )
    assert following_segment is not None
    if initial_segment.slope <= 0.0:
        return _failure_result(
            failure_reason="nonpositive_initial_segment_slope",
            **common,
        )
    if following_segment.slope <= initial_segment.slope:
        return _failure_result(
            failure_reason="first_break_is_not_accelerating",
            **common,
        )

    return BaiPerronInitialSlopeResult(
        status=FIT_STATUS_CANDIDATE,
        failure_reason=None,
        min_segment_observations=MIN_SEGMENT_OBSERVATIONS,
        initial_segment_start_date=pd.Timestamp(
            fit_frame["date"].iloc[initial_segment.start_index]
        ),
        initial_segment_end_date=pd.Timestamp(
            fit_frame["date"].iloc[initial_segment.end_index - 1]
        ),
        initial_segment_n_observations=(
            initial_segment.end_index - initial_segment.start_index
        ),
        initial_segment_covers_full_fit=False,
        first_break_date=pd.Timestamp(
            fit_frame["date"].iloc[following_segment.start_index]
        ),
        following_segment_n_observations=(
            following_segment.end_index - following_segment.start_index
        ),
        following_segment_v_mm_per_day=following_segment.slope,
        candidate_v_mm_per_day=initial_segment.slope,
        **{
            key: value
            for key, value in common.items()
            if key
            not in {"initial_segment", "following_segment", "dates"}
        },
    )


__all__ = [
    "BaiPerronInitialSlopeResult",
    "BIC_FORMULA",
    "CONTINUITY_CONSTRAINT",
    "FIT_STATUS_CANDIDATE",
    "FIT_STATUS_FAILED",
    "FIRST_SEGMENT_ACCEPTANCE",
    "MAX_SEGMENTS",
    "MIN_SEGMENT_OBSERVATIONS",
    "REGRESSION_PARAMETERS_PER_SEGMENT",
    "REQUIRED_TREND_COLUMNS",
    "SEGMENTATION_ALGORITHM",
    "SEGMENT_COUNT_SELECTION",
    "SELECTION_RULE",
    "select_bai_perron_initial_stable_slope",
]
