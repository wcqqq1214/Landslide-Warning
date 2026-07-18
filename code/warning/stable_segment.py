"""Fit-only, auditable automatic candidates for per-station V0 baselines.

The literature requires an initial stable displacement stage but does not give
an Ootang-reproducible automatic start/end rule.  This module implements only
the documented *draft candidate*: two-cluster K-means on a station's fit-period
point velocities, followed by the contiguous low-speed prefix beginning at the
first valid velocity.  It deliberately returns an explicit failure instead of
skipping forward to a more favorable later segment.

This is not a formal warning runner.  The versioned protocol remains ``draft``
until the remaining thresholds and fusion decisions are frozen.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from threadpoolctl import threadpool_limits


REQUIRED_COLUMNS = ("date", "velocity", "velocity_status")
DEFAULT_N_CLUSTERS = 2
DEFAULT_INIT = "random"
DEFAULT_RANDOM_STATE = 0
DEFAULT_N_INIT = 20
DEFAULT_SIGMA_DDOF = 1
DEFAULT_OPENMP_THREAD_LIMIT = 1


@dataclass(frozen=True)
class StableSegmentResult:
    """One per-station automatic stable-segment selection audit record."""

    station: str
    status: str
    failure_reason: str | None
    fit_end_date: pd.Timestamp
    n_fit_rows: int
    n_valid_velocities: int
    first_valid_velocity_date: pd.Timestamp | None
    first_valid_velocity: float | None
    segment_start_date: pd.Timestamp | None
    segment_end_date: pd.Timestamp | None
    n_selected_velocities: int
    cluster_centers: tuple[float, float] | None
    low_speed_cluster_center: float | None
    mean_velocity: float | None
    sigma: float | None
    v0: float | None
    n_clusters: int
    init: str
    random_state: int
    n_init: int
    sigma_ddof: int
    openmp_threads: int

    def to_record(self) -> dict[str, Any]:
        """Return plain scalar values suitable for an audit CSV row."""

        return {
            "station": self.station,
            "selection_status": self.status,
            "failure_reason": self.failure_reason,
            "fit_end_date": _date_to_iso(self.fit_end_date),
            "n_fit_rows": self.n_fit_rows,
            "n_valid_velocities": self.n_valid_velocities,
            "first_valid_velocity_date": _date_to_iso(self.first_valid_velocity_date),
            "first_valid_velocity": self.first_valid_velocity,
            "segment_start_date": _date_to_iso(self.segment_start_date),
            "segment_end_date": _date_to_iso(self.segment_end_date),
            "n_selected_velocities": self.n_selected_velocities,
            "cluster_centers": (
                None
                if self.cluster_centers is None
                else json.dumps(list(self.cluster_centers))
            ),
            "low_speed_cluster_center": self.low_speed_cluster_center,
            "cluster_center_low": (
                None if self.cluster_centers is None else self.cluster_centers[0]
            ),
            "cluster_center_high": (
                None if self.cluster_centers is None else self.cluster_centers[1]
            ),
            "mean_velocity": self.mean_velocity,
            "V": self.mean_velocity,
            "sigma": self.sigma,
            "v0": self.v0,
            "V0": self.v0,
            "n_clusters": self.n_clusters,
            "init": self.init,
            "random_state": self.random_state,
            "n_init": self.n_init,
            "sigma_ddof": self.sigma_ddof,
            "openmp_threads": self.openmp_threads,
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


def _prepare_frame(kinematics: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(kinematics, pd.DataFrame):
        raise ValueError("kinematics must be a DataFrame")
    missing = set(REQUIRED_COLUMNS).difference(kinematics.columns)
    if missing:
        raise ValueError(f"kinematics is missing required columns: {sorted(missing)}")

    frame = kinematics.loc[:, REQUIRED_COLUMNS].copy().reset_index(drop=True)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    if frame["date"].notna().all() and not frame["date"].is_monotonic_increasing:
        raise ValueError("kinematics dates must be in source order")
    frame["velocity"] = pd.to_numeric(frame["velocity"], errors="coerce")
    frame["velocity_status"] = frame["velocity_status"].astype("string")
    return frame


def _build_result(
    *,
    station: str,
    status: str,
    failure_reason: str | None,
    fit_end_date: pd.Timestamp,
    n_fit_rows: int,
    n_valid_velocities: int,
    first_valid_velocity_date: pd.Timestamp | None,
    first_valid_velocity: float | None = None,
    segment_start_date: pd.Timestamp | None = None,
    segment_end_date: pd.Timestamp | None = None,
    n_selected_velocities: int = 0,
    cluster_centers: tuple[float, float] | None = None,
    low_speed_cluster_center: float | None = None,
    mean_velocity: float | None = None,
    sigma: float | None = None,
    v0: float | None = None,
    n_clusters: int,
    init: str,
    random_state: int,
    n_init: int,
    sigma_ddof: int,
    openmp_threads: int = DEFAULT_OPENMP_THREAD_LIMIT,
) -> StableSegmentResult:
    return StableSegmentResult(
        station=station,
        status=status,
        failure_reason=failure_reason,
        fit_end_date=fit_end_date,
        n_fit_rows=n_fit_rows,
        n_valid_velocities=n_valid_velocities,
        first_valid_velocity_date=first_valid_velocity_date,
        first_valid_velocity=first_valid_velocity,
        segment_start_date=segment_start_date,
        segment_end_date=segment_end_date,
        n_selected_velocities=n_selected_velocities,
        cluster_centers=cluster_centers,
        low_speed_cluster_center=low_speed_cluster_center,
        mean_velocity=mean_velocity,
        sigma=sigma,
        v0=v0,
        n_clusters=n_clusters,
        init=init,
        random_state=random_state,
        n_init=n_init,
        sigma_ddof=sigma_ddof,
        openmp_threads=openmp_threads,
    )


def select_initial_stable_segment(
    kinematics: pd.DataFrame,
    *,
    station: str,
    fit_end_date,
) -> StableSegmentResult:
    """Select a per-station V0 candidate without accessing post-fit rows.

    A returned ``status='failed'`` is an auditable data/selection outcome, not
    an exception to be bypassed.  Input-shape and policy errors raise
    ``ValueError`` because they would invalidate the proposed protocol itself.
    """

    station = str(station).strip()
    if not station:
        raise ValueError("station must be nonempty")
    n_clusters = DEFAULT_N_CLUSTERS
    random_state = DEFAULT_RANDOM_STATE
    n_init = DEFAULT_N_INIT
    sigma_ddof = DEFAULT_SIGMA_DDOF
    openmp_threads = DEFAULT_OPENMP_THREAD_LIMIT
    fit_end = _normalise_fit_end_date(fit_end_date)
    frame = _prepare_frame(kinematics)
    if frame["date"].isna().any():
        return _build_result(
            station=station,
            status="failed",
            failure_reason="invalid_date_in_input",
            fit_end_date=fit_end,
            n_fit_rows=0,
            n_valid_velocities=0,
            first_valid_velocity_date=None,
            n_clusters=n_clusters,
            init=DEFAULT_INIT,
            random_state=random_state,
            n_init=n_init,
            sigma_ddof=sigma_ddof,
        )
    fit_frame = frame.loc[frame["date"] <= fit_end].reset_index(drop=True)
    if fit_frame.empty:
        return _build_result(
            station=station,
            status="failed",
            failure_reason="no_rows_in_fit_period",
            fit_end_date=fit_end,
            n_fit_rows=0,
            n_valid_velocities=0,
            first_valid_velocity_date=None,
            n_clusters=n_clusters,
            init=DEFAULT_INIT,
            random_state=random_state,
            n_init=n_init,
            sigma_ddof=sigma_ddof,
        )

    valid_velocity = fit_frame["velocity_status"].eq("valid") & np.isfinite(
        fit_frame["velocity"]
    )
    valid_positions = np.flatnonzero(valid_velocity.to_numpy())
    first_valid_date = (
        pd.Timestamp(fit_frame.loc[valid_positions[0], "date"])
        if len(valid_positions)
        else None
    )
    first_valid_velocity = (
        float(fit_frame.loc[valid_positions[0], "velocity"])
        if len(valid_positions)
        else None
    )
    base = {
        "station": station,
        "fit_end_date": fit_end,
        "n_fit_rows": len(fit_frame),
        "n_valid_velocities": len(valid_positions),
        "first_valid_velocity_date": first_valid_date,
        "first_valid_velocity": first_valid_velocity,
        "n_clusters": n_clusters,
        "init": DEFAULT_INIT,
        "random_state": random_state,
        "n_init": n_init,
        "sigma_ddof": sigma_ddof,
        "openmp_threads": openmp_threads,
    }
    if len(valid_positions) < n_clusters:
        return _build_result(
            status="failed",
            failure_reason="insufficient_valid_velocity_for_clustering",
            **base,
        )

    valid_values = fit_frame.loc[valid_velocity, "velocity"].to_numpy(dtype=float)
    if np.unique(valid_values).size < n_clusters:
        return _build_result(
            status="failed",
            failure_reason="insufficient_velocity_variation",
            **base,
        )

    try:
        with threadpool_limits(limits=openmp_threads, user_api="openmp"):
            model = KMeans(
                n_clusters=n_clusters,
                init=DEFAULT_INIT,
                random_state=random_state,
                n_init=n_init,
                algorithm="lloyd",
            ).fit(valid_values.reshape(-1, 1))
    except ValueError:
        return _build_result(
            status="failed",
            failure_reason="clustering_failed",
            **base,
        )

    centers = model.cluster_centers_.reshape(-1)
    if not np.all(np.isfinite(centers)) or np.isclose(centers[0], centers[1]):
        return _build_result(
            status="failed",
            failure_reason="clustering_not_separated",
            **base,
        )
    low_cluster = int(np.argmin(centers))
    ordered_centers = tuple(sorted(float(center) for center in centers))
    labels_by_position = dict(zip(valid_positions, model.labels_, strict=True))

    first_position = int(valid_positions[0])
    if labels_by_position[first_position] != low_cluster:
        return _build_result(
            status="failed",
            failure_reason="first_valid_velocity_not_low_speed",
            cluster_centers=ordered_centers,
            low_speed_cluster_center=float(centers[low_cluster]),
            **base,
        )

    selected_positions: list[int] = []
    position = first_position
    while position < len(fit_frame):
        if not valid_velocity.iloc[position]:
            break
        if labels_by_position[position] != low_cluster:
            break
        selected_positions.append(position)
        position += 1

    selected_values = fit_frame.loc[selected_positions, "velocity"].to_numpy(
        dtype=float
    )
    selected_dates = fit_frame.loc[selected_positions, "date"]
    selection = {
        "segment_start_date": pd.Timestamp(selected_dates.iloc[0]),
        "segment_end_date": pd.Timestamp(selected_dates.iloc[-1]),
        "n_selected_velocities": len(selected_positions),
        "cluster_centers": ordered_centers,
        "low_speed_cluster_center": float(centers[low_cluster]),
    }
    if len(selected_values) <= sigma_ddof:
        return _build_result(
            status="failed",
            failure_reason="insufficient_initial_low_speed_samples",
            **selection,
            **base,
        )

    mean_velocity = float(np.mean(selected_values))
    sigma = float(np.std(selected_values, ddof=sigma_ddof))
    v0 = float(max(1.5 * mean_velocity, mean_velocity + 2.0 * sigma))
    statistics = {
        "mean_velocity": mean_velocity,
        "sigma": sigma,
        "v0": v0,
    }
    if not np.isfinite(v0) or v0 <= 0:
        return _build_result(
            status="failed",
            failure_reason="nonpositive_v0",
            **statistics,
            **selection,
            **base,
        )
    return _build_result(
        status="selected",
        failure_reason=None,
        **statistics,
        **selection,
        **base,
    )


__all__ = [
    "DEFAULT_N_CLUSTERS",
    "DEFAULT_INIT",
    "DEFAULT_N_INIT",
    "DEFAULT_OPENMP_THREAD_LIMIT",
    "DEFAULT_RANDOM_STATE",
    "DEFAULT_SIGMA_DDOF",
    "REQUIRED_COLUMNS",
    "StableSegmentResult",
    "select_initial_stable_segment",
]
