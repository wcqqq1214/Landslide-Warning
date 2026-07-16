"""Auditable, time-aware displacement kinematics.

This module deliberately separates two quantities that were previously
conflated in the pipeline:

``velocity_i = (U_i - U_(i-1)) / (t_i - t_(i-1))``
``delta_v_i = velocity_i - velocity_(i-1)``

``delta_v`` is a velocity increment (mm/day), not an acceleration.  Missing
observations and unusable timestamps are never imputed; they are represented
by explicit per-row status fields so downstream warning rules can decide how
to handle them after the protocol is frozen.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd


KINEMATICS_COLUMNS = (
    "case",
    "date",
    "station",
    "displacement",
    "dt_days",
    "displacement_valid",
    "time_status",
    "velocity",
    "velocity_status",
    "delta_v",
    "delta_v_status",
)

SUMMARY_COLUMNS = (
    "case",
    "station",
    "n_observations",
    "first_observation_date",
    "last_observation_date",
    "first_valid_velocity_date",
    "first_valid_delta_v_date",
    "n_nonfinite_displacement",
    "n_invalid_date",
    "n_nonpositive_dt",
    "n_valid_velocity",
    "n_valid_delta_v",
)


def _normalise_inputs(dates, displacement):
    """Return aligned parsed date and numeric displacement series."""
    date_values = pd.Series(dates).reset_index(drop=True)
    displacement_values = pd.Series(displacement).reset_index(drop=True)
    if len(date_values) != len(displacement_values):
        raise ValueError("日期与位移序列长度必须一致")

    dates = pd.Series(
        pd.to_datetime(date_values, errors="coerce"),
        dtype="datetime64[ns]",
    ).reset_index(drop=True)
    displacement = pd.to_numeric(
        displacement_values,
        errors="coerce",
    ).astype(float).reset_index(drop=True)
    return dates, displacement


def compute_point_kinematics(dates, displacement):
    """Compute pointwise velocity and velocity increments for one station.

    The input order is preserved intentionally.  A duplicate or descending
    timestamp is marked ``nonpositive_dt`` rather than silently sorted or
    corrected.  The first velocity and the first two ``delta_v`` positions are
    warm-up values unless their timestamp itself is invalid.
    """
    dates, displacement = _normalise_inputs(dates, displacement)
    n_rows = len(dates)

    dt_days = dates.diff().dt.total_seconds() / 86_400.0
    date_valid = dates.notna()
    displacement_valid = pd.Series(
        np.isfinite(displacement.to_numpy(dtype=float)),
        index=displacement.index,
        dtype=bool,
    )

    time_status = np.full(n_rows, "warmup", dtype=object)
    for index in range(n_rows):
        if not date_valid.iloc[index]:
            time_status[index] = "invalid_date"
        elif index == 0:
            time_status[index] = "warmup"
        elif not date_valid.iloc[index - 1]:
            time_status[index] = "invalid_date"
        elif not np.isfinite(dt_days.iloc[index]) or dt_days.iloc[index] <= 0:
            time_status[index] = "nonpositive_dt"
        else:
            time_status[index] = "valid"
    time_status = pd.Series(time_status, index=dates.index, dtype="object")

    previous_displacement_valid = displacement_valid.shift(
        1,
        fill_value=False,
    )
    velocity_valid = (
        time_status.eq("valid")
        & displacement_valid
        & previous_displacement_valid
    )
    velocity = pd.Series(np.nan, index=dates.index, dtype=float)
    velocity.loc[velocity_valid] = (
        displacement.loc[velocity_valid]
        - displacement.shift(1).loc[velocity_valid]
    ) / dt_days.loc[velocity_valid]

    velocity_status = np.full(n_rows, "warmup", dtype=object)
    for index in range(n_rows):
        if velocity_valid.iloc[index]:
            velocity_status[index] = "valid"
        elif time_status.iloc[index] in {"invalid_date", "nonpositive_dt"}:
            velocity_status[index] = time_status.iloc[index]
        elif index == 0:
            velocity_status[index] = "warmup"
        else:
            velocity_status[index] = "nonfinite_displacement"
    velocity_status = pd.Series(
        velocity_status,
        index=dates.index,
        dtype="object",
    )

    previous_velocity_valid = velocity_valid.shift(1, fill_value=False)
    delta_v_valid = velocity_valid & previous_velocity_valid
    delta_v = pd.Series(np.nan, index=dates.index, dtype=float)
    delta_v.loc[delta_v_valid] = (
        velocity.loc[delta_v_valid]
        - velocity.shift(1).loc[delta_v_valid]
    )

    delta_v_status = np.full(n_rows, "warmup", dtype=object)
    for index in range(n_rows):
        if index < 2:
            delta_v_status[index] = "warmup"
        elif delta_v_valid.iloc[index]:
            delta_v_status[index] = "valid"
        elif not velocity_valid.iloc[index]:
            delta_v_status[index] = "velocity_invalid"
        else:
            delta_v_status[index] = "previous_velocity_invalid"
    delta_v_status = pd.Series(
        delta_v_status,
        index=dates.index,
        dtype="object",
    )

    return pd.DataFrame(
        {
            "date": dates,
            "displacement": displacement,
            "dt_days": dt_days,
            "displacement_valid": displacement_valid,
            "time_status": time_status,
            "velocity": velocity,
            "velocity_status": velocity_status,
            "delta_v": delta_v,
            "delta_v_status": delta_v_status,
        }
    )


def build_long_kinematics(df, *, case, stations: Mapping[str, str], date_col="Date"):
    """Build a station-long auditable kinematics table from raw monitoring data.

    Parameters
    ----------
    df:
        Source monitoring table.  No date sorting or imputation is performed
        in this function so any upstream ordering fault remains visible.
    case:
        Stable case identifier, for example ``"ootang"``.
    stations:
        Mapping from output station identifier to raw displacement column.
    date_col:
        Raw timestamp column name.
    """
    if not isinstance(df, pd.DataFrame):
        raise ValueError("df 必须是 DataFrame")
    if not stations:
        raise ValueError("stations 不能为空")

    case = str(case).strip()
    if not case:
        raise ValueError("case 不能为空")
    date_col = str(date_col).strip()
    if not date_col:
        raise ValueError("date_col 不能为空")

    ordered = df.rename(columns=lambda column: str(column).strip()).copy()
    date_col = date_col.strip()
    missing = {date_col, *[str(column).strip() for column in stations.values()]}
    missing -= set(ordered.columns)
    if missing:
        raise ValueError(f"原始数据缺少列: {sorted(missing)}")

    frames = []
    for station, displacement_col in stations.items():
        station = str(station).strip()
        if not station:
            raise ValueError("station 名称不能为空")
        displacement_col = str(displacement_col).strip()
        point = compute_point_kinematics(
            ordered[date_col],
            ordered[displacement_col],
        )
        point.insert(0, "station", station)
        point.insert(0, "case", case)
        frames.append(point)

    return pd.concat(frames, ignore_index=True)[list(KINEMATICS_COLUMNS)]


def _first_valid_date(group, status_col):
    dates = group.loc[group[status_col].eq("valid"), "date"].dropna()
    return dates.iloc[0] if not dates.empty else pd.NaT


def summarize_kinematics(long_frame):
    """Summarize validity, warm-up, and first-valid dates per case/station."""
    if not isinstance(long_frame, pd.DataFrame):
        raise ValueError("long_frame 必须是 DataFrame")
    missing = set(KINEMATICS_COLUMNS) - set(long_frame.columns)
    if missing:
        raise ValueError(f"运动学长表缺少列: {sorted(missing)}")

    if long_frame.empty:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)

    frame = long_frame.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    rows = []
    for (case, station), group in frame.groupby(["case", "station"], sort=True):
        observed_dates = group["date"].dropna()
        rows.append(
            {
                "case": case,
                "station": station,
                "n_observations": int(len(group)),
                "first_observation_date": (
                    observed_dates.iloc[0] if not observed_dates.empty else pd.NaT
                ),
                "last_observation_date": (
                    observed_dates.iloc[-1] if not observed_dates.empty else pd.NaT
                ),
                "first_valid_velocity_date": _first_valid_date(
                    group,
                    "velocity_status",
                ),
                "first_valid_delta_v_date": _first_valid_date(
                    group,
                    "delta_v_status",
                ),
                "n_nonfinite_displacement": int(
                    (~group["displacement_valid"].astype(bool)).sum()
                ),
                "n_invalid_date": int(group["time_status"].eq("invalid_date").sum()),
                "n_nonpositive_dt": int(
                    group["time_status"].eq("nonpositive_dt").sum()
                ),
                "n_valid_velocity": int(
                    group["velocity_status"].eq("valid").sum()
                ),
                "n_valid_delta_v": int(
                    group["delta_v_status"].eq("valid").sum()
                ),
            }
        )
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


__all__ = [
    "KINEMATICS_COLUMNS",
    "SUMMARY_COLUMNS",
    "build_long_kinematics",
    "compute_point_kinematics",
    "summarize_kinematics",
]
