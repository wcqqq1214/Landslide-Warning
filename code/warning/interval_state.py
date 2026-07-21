"""Observed interval-state diagnostics and thesis-referenced five-level mapping.

The project uses interval *state identification*: an already-issued P10/P50/P90
forecast is compared with the subsequently observed displacement at the same
time point.  It is not a claim of an h-step-ahead warning.

The specified thesis Figure 5-1 directly supplies the five ``mu + k*sigma``
regions.  Its source does not establish Ootang-specific calibration acceptance
thresholds, so coverage, symmetry, and tail summaries remain audit evidence;
they do not become invented hard gates that suppress the source-referenced
mapping.  The draft protocol still controls whether any downstream use is
formal.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd

from warning.levels import WarningLevel


NORMAL_90_QUANTILE = 1.28155
CALIBRATION_SPLIT = "calibration"
REQUIRED_INTERVAL_COLUMNS = ("actual", "p10", "p50", "p90")
THESIS_FIGURE_5_1_MAPPING_ID = "specified_thesis_figure_5_1_normal_regions"
INTERVAL_OUTPUT_COLUMNS = (
    "interval_status",
    "interval_level",
    "interval_color",
    "interval_reason",
    "interval_mapping_basis",
    "interval_mu",
    "interval_sigma",
    "interval_z",
)


class IntervalStatus(str, Enum):
    """Per-row interval-state status before any four-indicator fusion."""

    VALID = "valid"
    WARMUP = "warmup"
    INVALID = "invalid"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class _IntervalDiagnostics:
    """Shared per-row numerical evidence for mapping and calibration audit."""

    finite_quantiles: pd.Series
    finite_all: pd.Series
    quantile_valid: pd.Series
    midpoint_error: pd.Series
    sigma: pd.Series
    z: pd.Series
    covered: pd.Series


def _validate_frame_columns(frame: pd.DataFrame, columns: tuple[str, ...]) -> None:
    if not isinstance(frame, pd.DataFrame):
        raise ValueError("interval input must be a DataFrame")
    missing = set(columns).difference(frame.columns)
    if missing:
        raise ValueError(
            f"interval input is missing required columns: {sorted(missing)}"
        )


def _normalise_source_status(value) -> IntervalStatus:
    if not isinstance(value, str):
        raise ValueError(
            "input status must be valid, warmup, invalid, or not_applicable"
        )
    try:
        return IntervalStatus(value.strip().lower())
    except ValueError as exc:
        valid = ", ".join(status.value for status in IntervalStatus)
        raise ValueError(f"input status must be one of: {valid}") from exc


def _numeric_columns(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.loc[:, REQUIRED_INTERVAL_COLUMNS].apply(
        pd.to_numeric,
        errors="coerce",
    )


def _build_interval_diagnostics(numeric: pd.DataFrame) -> _IntervalDiagnostics:
    """Calculate the common numerical evidence without a pass/fail decision."""

    finite_quantiles = pd.Series(
        np.isfinite(numeric.loc[:, ["p10", "p50", "p90"]].to_numpy(dtype=float)).all(
            axis=1
        ),
        index=numeric.index,
        dtype=bool,
    )
    finite_actual = pd.Series(
        np.isfinite(numeric["actual"].to_numpy(dtype=float)),
        index=numeric.index,
        dtype=bool,
    )
    finite_all = finite_quantiles & finite_actual
    quantile_valid = (
        finite_quantiles
        & numeric["p10"].le(numeric["p50"])
        & numeric["p50"].le(numeric["p90"])
        & numeric["p90"].gt(numeric["p10"])
    )
    midpoint_error = (numeric["p50"] - (numeric["p10"] + numeric["p90"]) / 2.0).abs()
    sigma = (numeric["p90"] - numeric["p10"]) / (2.0 * NORMAL_90_QUANTILE)
    z = (numeric["actual"] - numeric["p50"]) / sigma
    covered = numeric["actual"].ge(numeric["p10"]) & numeric["actual"].le(
        numeric["p90"]
    )
    return _IntervalDiagnostics(
        finite_quantiles=finite_quantiles,
        finite_all=finite_all,
        quantile_valid=quantile_valid,
        midpoint_error=midpoint_error,
        sigma=sigma,
        z=z,
        covered=covered,
    )


def _at_or_below_upper_boundary(actual: float, boundary: float) -> bool:
    """Apply an inclusive boundary without promoting IEEE-754 round-off.

    The protocol's boundaries are mathematical ``<=`` conditions.  Moving the
    computed upper endpoint by one representable float toward positive infinity
    prevents, for example, an intended ``mu + 2*sigma`` boundary from becoming
    marginally smaller solely because ``sigma`` came from decimal P10/P90 input.
    This is numerical bookkeeping, not a physical warning tolerance.
    """

    return bool(actual <= np.nextafter(boundary, np.inf))


def classify_observed_interval_states(
    frame: pd.DataFrame,
    *,
    input_status_column: str | None = None,
) -> pd.DataFrame:
    """Append thesis-referenced observed interval states to a prediction frame.

    The caller chooses the already-produced lower/median/upper columns by
    naming them ``p10``, ``p50``, and ``p90`` in the supplied frame.  Valid
    rows use the thesis Figure 5-1 regions directly; calibration summaries are
    retained separately as audit evidence rather than converted into unstated
    pass/fail thresholds.
    """

    columns = list(REQUIRED_INTERVAL_COLUMNS)
    if input_status_column is not None:
        columns.append(input_status_column)
    _validate_frame_columns(frame, tuple(columns))

    result = frame.copy()
    numeric = _numeric_columns(result)
    diagnostics = _build_interval_diagnostics(numeric)

    result["interval_status"] = pd.Series(pd.NA, index=result.index, dtype="string")
    result["interval_level"] = pd.Series(pd.NA, index=result.index, dtype="Int64")
    result["interval_color"] = pd.Series(pd.NA, index=result.index, dtype="string")
    result["interval_reason"] = pd.Series(pd.NA, index=result.index, dtype="string")
    result["interval_mapping_basis"] = pd.Series(
        pd.NA,
        index=result.index,
        dtype="string",
    )
    result["interval_mu"] = np.where(
        diagnostics.quantile_valid,
        numeric["p50"],
        np.nan,
    )
    result["interval_sigma"] = np.where(
        diagnostics.quantile_valid,
        diagnostics.sigma,
        np.nan,
    )
    result["interval_z"] = np.where(
        diagnostics.quantile_valid & diagnostics.finite_all,
        diagnostics.z,
        np.nan,
    )

    source_statuses = (
        pd.Series(IntervalStatus.VALID, index=result.index, dtype="object")
        if input_status_column is None
        else result[input_status_column].map(_normalise_source_status)
    )
    output_column_positions = {
        column: result.columns.get_loc(column) for column in INTERVAL_OUTPUT_COLUMNS
    }
    for position in range(len(result)):
        source_status = source_statuses.iloc[position]
        if source_status is not IntervalStatus.VALID:
            result.iat[position, output_column_positions["interval_status"]] = (
                source_status.value
            )
            result.iat[position, output_column_positions["interval_reason"]] = (
                f"source_{source_status.value}"
            )
            continue
        if not diagnostics.finite_all.iloc[position]:
            result.iat[position, output_column_positions["interval_status"]] = (
                IntervalStatus.INVALID.value
            )
            result.iat[position, output_column_positions["interval_reason"]] = (
                "nonfinite_input"
            )
            continue
        if not diagnostics.quantile_valid.iloc[position]:
            result.iat[position, output_column_positions["interval_status"]] = (
                IntervalStatus.INVALID.value
            )
            result.iat[position, output_column_positions["interval_reason"]] = (
                "quantile_order_invalid"
            )
            continue
        actual = numeric["actual"].iloc[position]
        mu = numeric["p50"].iloc[position]
        row_sigma = diagnostics.sigma.iloc[position]
        if _at_or_below_upper_boundary(actual, mu):
            level = WarningLevel.GREEN
        elif _at_or_below_upper_boundary(actual, mu + row_sigma):
            level = WarningLevel.BLUE
        elif _at_or_below_upper_boundary(actual, mu + 2.0 * row_sigma):
            level = WarningLevel.YELLOW
        elif _at_or_below_upper_boundary(actual, mu + 3.0 * row_sigma):
            level = WarningLevel.ORANGE
        else:
            level = WarningLevel.RED
        result.iat[position, output_column_positions["interval_status"]] = (
            IntervalStatus.VALID.value
        )
        result.iat[position, output_column_positions["interval_level"]] = int(level)
        result.iat[position, output_column_positions["interval_color"]] = level.color
        result.iat[position, output_column_positions["interval_reason"]] = (
            f"thesis_figure_5_1_observed_{level.color}_region"
        )
        result.iat[position, output_column_positions["interval_mapping_basis"]] = (
            THESIS_FIGURE_5_1_MAPPING_ID
        )
    return result


def summarize_calibration_interval_inputs(
    calibration: pd.DataFrame,
    *,
    station_column: str = "station",
    split_column: str = "split",
) -> pd.DataFrame:
    """Summarize calibration-only diagnostics without deciding acceptance.

    This deliberately produces raw evidence (coverage, quantile-order faults,
    midpoint differences, and standardized-residual summaries), not a
    passed/failed decision or an unfrozen tail cutoff.  The direct
    thesis-referenced mapping does not turn these diagnostics into a hard
    gate; they remain auditable evidence about the normal approximation.
    """

    _validate_frame_columns(
        calibration,
        (*REQUIRED_INTERVAL_COLUMNS, station_column, split_column),
    )
    splits = calibration[split_column].astype("string")
    split_matches = splits.eq(CALIBRATION_SPLIT).fillna(False)
    if not bool(split_matches.all()):
        raise ValueError("calibration diagnostics may only receive calibration rows")

    numeric = _numeric_columns(calibration)
    diagnostics = _build_interval_diagnostics(numeric)

    rows: list[dict[str, Any]] = []
    for station, indices in calibration.groupby(
        station_column, sort=True
    ).groups.items():
        index = pd.Index(indices)
        valid_quantiles = diagnostics.quantile_valid.loc[index]
        valid_observations = valid_quantiles & diagnostics.finite_all.loc[index]
        valid_z = diagnostics.z.loc[index].where(valid_observations).dropna()
        valid_midpoint_error = (
            diagnostics.midpoint_error.loc[index].where(valid_quantiles).dropna()
        )
        rows.append(
            {
                "station": station,
                "n_rows": int(len(index)),
                "n_valid_quantile_rows": int(valid_quantiles.sum()),
                "n_valid_observation_rows": int(valid_observations.sum()),
                "n_nonfinite_input": int((~diagnostics.finite_all.loc[index]).sum()),
                "n_quantile_order_invalid": int(
                    (
                        diagnostics.finite_quantiles.loc[index]
                        & ~diagnostics.quantile_valid.loc[index]
                    ).sum()
                ),
                "coverage_80": (
                    float(
                        diagnostics.covered.loc[index].where(valid_observations).mean()
                    )
                    if valid_observations.any()
                    else np.nan
                ),
                "mean_abs_midpoint_error": (
                    float(valid_midpoint_error.mean())
                    if not valid_midpoint_error.empty
                    else np.nan
                ),
                "max_abs_midpoint_error": (
                    float(valid_midpoint_error.max())
                    if not valid_midpoint_error.empty
                    else np.nan
                ),
                "n_standardized_residuals": int(len(valid_z)),
                "max_abs_z": float(valid_z.abs().max())
                if not valid_z.empty
                else np.nan,
            }
        )
    return pd.DataFrame(rows)


__all__ = [
    "CALIBRATION_SPLIT",
    "INTERVAL_OUTPUT_COLUMNS",
    "NORMAL_90_QUANTILE",
    "REQUIRED_INTERVAL_COLUMNS",
    "IntervalStatus",
    "THESIS_FIGURE_5_1_MAPPING_ID",
    "classify_observed_interval_states",
    "summarize_calibration_interval_inputs",
]
