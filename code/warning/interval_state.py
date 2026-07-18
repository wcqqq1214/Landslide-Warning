"""Draft observed interval-state diagnostics and guarded five-level mapping.

The project uses interval *state identification*: an already-issued P10/P50/P90
forecast is compared with the subsequently observed displacement at the same
time point.  It is not a claim of an h-step-ahead warning.

The specified thesis motivates the five ``mu + k*sigma`` regions, but it does
not establish Ootang-specific coverage, symmetry, or tail-diagnostic pass
thresholds.  Consequently this module never invents a calibration decision:
callers must supply four explicit gate checks.  Until every check is passed,
valid rows are returned as ``not_applicable`` rather than colored warnings.
The draft protocol still controls whether any downstream use is formal.
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
CALIBRATION_CHECK_NAMES = ("quantile_order", "coverage", "symmetry", "tail")
INTERVAL_OUTPUT_COLUMNS = (
    "interval_status",
    "interval_level",
    "interval_color",
    "interval_reason",
    "interval_mu",
    "interval_sigma",
    "interval_z",
)


class CalibrationCheckStatus(str, Enum):
    """Status of one globally evaluated calibration condition."""

    PASSED = "passed"
    FAILED = "failed"
    UNCONFIGURED = "unconfigured"


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


@dataclass(frozen=True)
class CalibrationCheck:
    """One explicit evidence-bearing calibration gate check.

    The check value is optional because the future frozen tail diagnostic may
    not reduce to one scalar.  ``evidence`` identifies the source artifact or
    reason used to set the status.
    """

    status: CalibrationCheckStatus
    evidence: str
    value: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, CalibrationCheckStatus):
            raise ValueError("CalibrationCheck status must be a CalibrationCheckStatus")
        if not isinstance(self.evidence, str) or not self.evidence.strip():
            raise ValueError("CalibrationCheck evidence must be nonempty")
        if self.value is not None and not np.isfinite(self.value):
            raise ValueError("CalibrationCheck value must be finite when supplied")


@dataclass(frozen=True)
class IntervalCalibrationGate:
    """The four globally required conditions for an interval-level mapping."""

    quantile_order: CalibrationCheck
    coverage: CalibrationCheck
    symmetry: CalibrationCheck
    tail: CalibrationCheck

    def __post_init__(self) -> None:
        if not all(isinstance(check, CalibrationCheck) for _, check in self.checks):
            raise ValueError("every interval gate entry must be a CalibrationCheck")

    @property
    def checks(self) -> tuple[tuple[str, CalibrationCheck], ...]:
        """Return checks in the protocol's declared order."""

        return (
            ("quantile_order", self.quantile_order),
            ("coverage", self.coverage),
            ("symmetry", self.symmetry),
            ("tail", self.tail),
        )

    @property
    def status(self) -> CalibrationCheckStatus:
        """Aggregate checks without substituting an unstated threshold."""

        statuses = tuple(check.status for _, check in self.checks)
        if CalibrationCheckStatus.FAILED in statuses:
            return CalibrationCheckStatus.FAILED
        if CalibrationCheckStatus.UNCONFIGURED in statuses:
            return CalibrationCheckStatus.UNCONFIGURED
        return CalibrationCheckStatus.PASSED

    @property
    def unresolved_checks(self) -> tuple[str, ...]:
        """Return checks that prevent a five-level interval state."""

        return tuple(
            name
            for name, check in self.checks
            if check.status is not CalibrationCheckStatus.PASSED
        )

    def to_record(self) -> dict[str, Any]:
        """Return flat gate evidence suitable for an audit record."""

        record: dict[str, Any] = {"interval_gate_status": self.status.value}
        for name, check in self.checks:
            record[f"{name}_status"] = check.status.value
            record[f"{name}_evidence"] = check.evidence
            record[f"{name}_value"] = check.value
        return record


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


def classify_observed_interval_states(
    frame: pd.DataFrame,
    *,
    gate: IntervalCalibrationGate,
    input_status_column: str | None = None,
) -> pd.DataFrame:
    """Append guarded observed interval states to a prediction frame.

    The caller chooses the already-produced lower/median/upper columns by
    naming them ``p10``, ``p50``, and ``p90`` in the supplied frame.  If the
    global calibration gate is not passed, numerically valid rows retain their
    diagnostic ``mu``/``sigma`` values but receive no five-level color.
    """

    if not isinstance(gate, IntervalCalibrationGate):
        raise ValueError("gate must be an IntervalCalibrationGate")
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
    for index in result.index:
        source_status = source_statuses.loc[index]
        if source_status is not IntervalStatus.VALID:
            result.loc[index, "interval_status"] = source_status.value
            result.loc[index, "interval_reason"] = f"source_{source_status.value}"
            continue
        if not diagnostics.finite_all.loc[index]:
            result.loc[index, "interval_status"] = IntervalStatus.INVALID.value
            result.loc[index, "interval_reason"] = "nonfinite_input"
            continue
        if not diagnostics.quantile_valid.loc[index]:
            result.loc[index, "interval_status"] = IntervalStatus.INVALID.value
            result.loc[index, "interval_reason"] = "quantile_order_invalid"
            continue
        if gate.status is not CalibrationCheckStatus.PASSED:
            result.loc[index, "interval_status"] = IntervalStatus.NOT_APPLICABLE.value
            result.loc[index, "interval_reason"] = (
                f"calibration_gate_{gate.status.value}:"
                f"{','.join(gate.unresolved_checks)}"
            )
            continue

        actual = numeric.loc[index, "actual"]
        mu = numeric.loc[index, "p50"]
        row_sigma = diagnostics.sigma.loc[index]
        if actual <= mu:
            level = WarningLevel.GREEN
        elif actual <= mu + row_sigma:
            level = WarningLevel.BLUE
        elif actual <= mu + 2.0 * row_sigma:
            level = WarningLevel.YELLOW
        elif actual <= mu + 3.0 * row_sigma:
            level = WarningLevel.ORANGE
        else:
            level = WarningLevel.RED
        result.loc[index, "interval_status"] = IntervalStatus.VALID.value
        result.loc[index, "interval_level"] = int(level)
        result.loc[index, "interval_color"] = level.color
        result.loc[index, "interval_reason"] = f"observed_{level.color}_region"
    return result


def summarize_calibration_interval_inputs(
    calibration: pd.DataFrame,
    *,
    station_column: str = "station",
    split_column: str = "split",
) -> pd.DataFrame:
    """Summarize calibration-only diagnostics without deciding a gate outcome.

    This deliberately produces raw evidence (coverage, quantile-order faults,
    midpoint differences, and standardized-residual summaries), not a
    passed/failed decision or an unfrozen tail cutoff.  The protocol must
    freeze the relevant acceptance rules before those diagnostics can
    instantiate ``IntervalCalibrationGate``.
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
    "CALIBRATION_CHECK_NAMES",
    "INTERVAL_OUTPUT_COLUMNS",
    "NORMAL_90_QUANTILE",
    "REQUIRED_INTERVAL_COLUMNS",
    "CalibrationCheck",
    "CalibrationCheckStatus",
    "IntervalCalibrationGate",
    "IntervalStatus",
    "classify_observed_interval_states",
    "summarize_calibration_interval_inputs",
]
