"""Write raw velocity/V0 and tangent-angle diagnostics without tolerances.

The thesis supplies relative five-level boundaries around ``V0`` and the
improved-tangent-angle boundaries around 45/80/85 degrees, but it does not
quantify the two blue ``approximately equal`` bands.  The current raw-velocity
KMeans V0 input is explicitly marked as a non-Word-thesis comparator.  This
module therefore records only fit/calibration evidence for a later, explicit
calibration decision.  It never assigns velocity/tangent levels, blue
tolerances, a fused warning level, or a formal warning output.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

# Permit direct execution with ``uv run python code/warning/...py`` as well as
# package imports from the test suite.
CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning.protocol import (  # noqa: E402
    DEFAULT_PROTOCOL_PATH,
    load_protocol,
    protocol_content_sha256,
    unresolved_item_ids,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KINEMATICS_PATH = ROOT / "data" / "ootang_kinematics_long.csv"
DEFAULT_PREDICTIONS_PATH = ROOT / "figures" / "convlstm" / "forecast_predictions.csv"
DEFAULT_STABLE_SEGMENT_CANDIDATES_PATH = (
    ROOT / "figures" / "warning_draft" / "stable_segment_candidates.csv"
)
DEFAULT_OUTPUT_DIR = ROOT / "figures" / "warning_draft"
SUMMARY_FILENAME = "velocity_tangent_fit_calibration_diagnostics.csv"
MANIFEST_FILENAME = "velocity_tangent_fit_calibration_diagnostics_manifest.json"
FIT_SPLIT = "fit"
CALIBRATION_SPLIT = "calibration"
DIAGNOSTIC_STATUS = "diagnostic_only_no_tolerance_decision"
DRAFT_CANDIDATE_STATUS = "draft_candidate_not_formal"
FIT_KINEMATICS_SCOPE = "all_station_history_through_fit_cutoff"
CALIBRATION_KINEMATICS_SCOPE = "exact_station_calibration_prediction_dates"
_PREDICTION_COLUMNS = ("date", "station", "split")
_KINEMATICS_COLUMNS = ("date", "station", "velocity", "velocity_status")
_CANDIDATE_SOURCE_ALIGNMENT_FIELDS = (
    "candidate_method_id",
    "candidate_method_role",
    "word_thesis_v0_input",
    "word_thesis_v0_input_status",
)
_CANDIDATE_COLUMNS = (
    "station",
    "candidate_status",
    *_CANDIDATE_SOURCE_ALIGNMENT_FIELDS,
    "source_split",
    "kinematics_temporal_scope",
    "fit_prediction_input_sha256",
    "fit_kinematics_input_sha256",
    "selection_status",
    "failure_reason",
    "fit_end_date",
    "V0",
    "protocol_content_sha256",
)


@dataclass(frozen=True)
class VelocityTangentDiagnosticArtifacts:
    """Paths and row count for one non-formal diagnostic run."""

    summary_path: Path
    manifest_path: Path
    n_summary_rows: int


@dataclass(frozen=True)
class _VelocityTangentInputs:
    """Selected non-test data needed to reproduce this diagnostic summary."""

    fit_prediction_rows: pd.DataFrame
    calibration_prediction_rows: pd.DataFrame
    windows: pd.DataFrame
    fit_kinematics: pd.DataFrame
    calibration_kinematics: pd.DataFrame
    stable_segment_candidates: pd.DataFrame
    fit_prediction_input_sha256: str
    fit_kinematics_input_sha256: str


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_canonical_csv(
    frame: pd.DataFrame,
    *,
    columns: tuple[str, ...],
    sort_columns: tuple[str, ...],
) -> str:
    """Hash a deterministic selected slice, never an entire source file."""

    canonical = (
        frame.loc[:, columns]
        .sort_values(list(sort_columns), kind="stable")
        .reset_index(drop=True)
    )
    buffer = io.StringIO()
    canonical.to_csv(
        buffer,
        index=False,
        lineterminator="\n",
        na_rep="<NA>",
        float_format="%.17g",
        date_format="%Y-%m-%d",
    )
    return hashlib.sha256(buffer.getvalue().encode("utf-8")).hexdigest()


def _read_required_columns(
    path: str | Path,
    columns: tuple[str, ...],
    *,
    source_name: str,
) -> pd.DataFrame:
    source_path = Path(path)
    try:
        return pd.read_csv(source_path, usecols=list(columns))
    except FileNotFoundError as exc:
        raise ValueError(f"{source_name} file does not exist: {source_path}") from exc
    except ValueError as exc:
        required = ", ".join(columns)
        raise ValueError(
            f"{source_name} file must include required columns: {required}"
        ) from exc


def _normalize_date_and_station(
    frame: pd.DataFrame,
    *,
    source_name: str,
) -> pd.DataFrame:
    normalized = frame.copy()
    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
    normalized["station"] = normalized["station"].astype("string").str.strip()
    if normalized["date"].isna().any():
        raise ValueError(f"{source_name} contains an invalid date")
    if normalized["station"].isna().any() or normalized["station"].eq("").any():
        raise ValueError(f"{source_name} contains a missing station")
    return normalized


def _require_unique_coordinates(rows: pd.DataFrame, *, source_name: str) -> None:
    duplicated = rows.duplicated(["station", "date"], keep=False)
    if not duplicated.any():
        return
    keys = (
        rows.loc[duplicated, ["station", "date"]]
        .drop_duplicates()
        .sort_values(["station", "date"], kind="stable")
    )
    raise ValueError(
        f"{source_name} contains duplicate station/date rows: "
        f"{keys.to_dict(orient='records')}"
    )


def _load_prediction_windows(
    predictions_path: str | Path,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    predictions = _read_required_columns(
        predictions_path,
        _PREDICTION_COLUMNS,
        source_name="prediction",
    )
    split = predictions["split"].astype("string")
    fit_rows = predictions.loc[split.eq(FIT_SPLIT)].copy()
    calibration_rows = predictions.loc[split.eq(CALIBRATION_SPLIT)].copy()
    if fit_rows.empty:
        raise ValueError("prediction file contains no split=fit rows")
    if calibration_rows.empty:
        raise ValueError("prediction file contains no split=calibration rows")
    fit_rows = _normalize_date_and_station(fit_rows, source_name="fit prediction")
    calibration_rows = _normalize_date_and_station(
        calibration_rows,
        source_name="calibration prediction",
    )
    _require_unique_coordinates(fit_rows, source_name="fit prediction")
    _require_unique_coordinates(calibration_rows, source_name="calibration prediction")

    fit_stations = set(fit_rows["station"])
    calibration_stations = set(calibration_rows["station"])
    if fit_stations != calibration_stations:
        raise ValueError(
            "fit and calibration prediction stations must match; "
            f"fit_only={sorted(fit_stations.difference(calibration_stations))}, "
            f"calibration_only={sorted(calibration_stations.difference(fit_stations))}"
        )

    fit_ends = (
        fit_rows.groupby("station", as_index=False, sort=True)["date"]
        .max()
        .rename(columns={"date": "fit_end_date"})
    )
    calibration_windows = calibration_rows.groupby(
        "station", as_index=False, sort=True
    )["date"].agg(calibration_start_date="min", calibration_end_date="max")
    windows = fit_ends.merge(
        calibration_windows,
        on="station",
        how="inner",
        validate="one_to_one",
    ).sort_values("station", kind="stable")
    if windows["fit_end_date"].ge(windows["calibration_start_date"]).any():
        raise ValueError("fit must end before each station's calibration window")
    return fit_rows, calibration_rows, windows.reset_index(drop=True)


def _load_kinematics(kinematics_path: str | Path) -> pd.DataFrame:
    return _read_required_columns(
        kinematics_path,
        _KINEMATICS_COLUMNS,
        source_name="kinematics",
    )


def _select_kinematics_windows(
    kinematics: pd.DataFrame,
    windows: pd.DataFrame,
    calibration_prediction_rows: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Select fit history and exact calibration coordinates, excluding test."""

    stations = set(windows["station"])
    selected = kinematics.copy()
    selected["station"] = selected["station"].astype("string").str.strip()
    selected = selected.loc[selected["station"].isin(stations)].copy()
    missing = sorted(stations.difference(set(selected["station"])))
    if missing:
        raise ValueError(
            f"kinematics is missing stations required by prediction windows: {missing}"
        )
    selected = _normalize_date_and_station(
        selected,
        source_name="selected kinematics",
    )
    selected["velocity"] = pd.to_numeric(selected["velocity"], errors="coerce")
    selected["velocity_status"] = selected["velocity_status"].astype("string")

    fit_parts: list[pd.DataFrame] = []
    calibration_parts: list[pd.DataFrame] = []
    for window in windows.itertuples(index=False):
        station_rows = selected.loc[selected["station"].eq(window.station)]
        fit_rows = station_rows.loc[station_rows["date"] <= window.fit_end_date].copy()
        if fit_rows.empty:
            raise ValueError(
                "kinematics contains no rows through the fit cutoff for station "
                f"{window.station}"
            )
        fit_parts.append(fit_rows)

        expected_dates = pd.Index(
            calibration_prediction_rows.loc[
                calibration_prediction_rows["station"].eq(window.station), "date"
            ].unique()
        )
        calibration_rows = station_rows.loc[
            station_rows["date"].isin(expected_dates)
        ].copy()
        missing_dates = sorted(
            set(expected_dates).difference(set(calibration_rows["date"]))
        )
        if missing_dates:
            missing_text = ", ".join(
                date.strftime("%Y-%m-%d") for date in missing_dates
            )
            raise ValueError(
                "kinematics is missing exact calibration prediction dates for "
                f"station {window.station}: {missing_text}"
            )
        calibration_parts.append(calibration_rows)

    fit_kinematics = pd.concat(fit_parts, ignore_index=True)
    calibration_kinematics = pd.concat(calibration_parts, ignore_index=True)
    _require_unique_coordinates(fit_kinematics, source_name="fit kinematics")
    _require_unique_coordinates(
        calibration_kinematics,
        source_name="calibration kinematics",
    )
    return fit_kinematics, calibration_kinematics


def _load_stable_segment_candidates(
    path: str | Path,
    *,
    stations: set[str],
) -> pd.DataFrame:
    """Load one candidate V0 record per required station without upgrading it."""

    candidates = _read_required_columns(
        path,
        _CANDIDATE_COLUMNS,
        source_name="stable-segment candidate",
    )
    candidates = candidates.copy()
    candidates["station"] = candidates["station"].astype("string").str.strip()
    if candidates["station"].isna().any() or candidates["station"].eq("").any():
        raise ValueError("stable-segment candidate contains a missing station")
    duplicates = candidates.duplicated("station", keep=False)
    if duplicates.any():
        names = sorted(candidates.loc[duplicates, "station"].drop_duplicates())
        raise ValueError(
            "stable-segment candidate contains duplicate station rows: "
            f"{names}"
        )
    candidate_stations = set(candidates["station"])
    if candidate_stations != stations:
        raise ValueError(
            "stable-segment candidate stations must match prediction stations; "
            f"candidate_only={sorted(candidate_stations.difference(stations))}, "
            f"prediction_only={sorted(stations.difference(candidate_stations))}"
        )
    required_provenance_fields = (
        "candidate_status",
        "candidate_method_id",
        "candidate_method_role",
        "word_thesis_v0_input",
        "word_thesis_v0_input_status",
        "source_split",
        "kinematics_temporal_scope",
        "fit_prediction_input_sha256",
        "fit_kinematics_input_sha256",
        "selection_status",
        "protocol_content_sha256",
    )
    for field in required_provenance_fields:
        values = candidates[field].astype("string").str.strip()
        candidates[field] = values
        if values.isna().any() or values.eq("").fillna(True).any():
            raise ValueError(
                "stable-segment candidate contains a missing required provenance "
                f"field: {field}"
            )
    candidates["failure_reason"] = candidates["failure_reason"].astype("string")
    candidates["fit_end_date"] = pd.to_datetime(
        candidates["fit_end_date"],
        errors="coerce",
    )
    if candidates["fit_end_date"].isna().any():
        raise ValueError("stable-segment candidate contains an invalid fit end date")
    candidates["V0"] = pd.to_numeric(candidates["V0"], errors="coerce")
    return candidates.sort_values("station", kind="stable").reset_index(drop=True)


def _validate_stable_segment_candidate_provenance(
    candidates: pd.DataFrame,
    *,
    windows: pd.DataFrame,
    fit_prediction_input_sha256: str,
    fit_kinematics_input_sha256: str,
    expected_source_alignment: dict[str, str],
) -> None:
    """Reject candidates that do not originate from this exact fit slice."""

    if not candidates["candidate_status"].eq(DRAFT_CANDIDATE_STATUS).fillna(False).all():
        raise ValueError(
            "stable-segment candidate status must be draft_candidate_not_formal"
        )
    for field in _CANDIDATE_SOURCE_ALIGNMENT_FIELDS:
        if candidates[field].nunique(dropna=False) != 1:
            raise ValueError(
                "stable-segment candidate source-alignment provenance must be "
                f"consistent across stations: {field}"
            )
        if not candidates[field].eq(
            expected_source_alignment[field]
        ).fillna(False).all():
            raise ValueError(
                "stable-segment candidate source-alignment provenance does not "
                "match the diagnostic protocol"
            )
    if not candidates["source_split"].eq(FIT_SPLIT).fillna(False).all():
        raise ValueError("stable-segment candidate source_split must be fit")
    if not candidates["kinematics_temporal_scope"].eq(
        FIT_KINEMATICS_SCOPE
    ).fillna(False).all():
        raise ValueError(
            "stable-segment candidate kinematics temporal scope does not match "
            "the fit-only diagnostic scope"
        )
    if not candidates["fit_prediction_input_sha256"].eq(
        fit_prediction_input_sha256
    ).fillna(False).all() or not candidates["fit_kinematics_input_sha256"].eq(
        fit_kinematics_input_sha256
    ).fillna(False).all():
        raise ValueError(
            "stable-segment candidate fit input provenance does not match "
            "the selected diagnostic inputs"
        )
    expected_fit_ends = windows.set_index("station")["fit_end_date"]
    actual_fit_ends = candidates.set_index("station")["fit_end_date"]
    if not actual_fit_ends.eq(expected_fit_ends).fillna(False).all():
        raise ValueError(
            "stable-segment candidate fit input provenance does not match "
            "the selected diagnostic inputs"
        )


def _load_inputs(
    *,
    kinematics_path: str | Path,
    predictions_path: str | Path,
    stable_segment_candidates_path: str | Path,
    expected_source_alignment: dict[str, str],
) -> _VelocityTangentInputs:
    fit_rows, calibration_rows, windows = _load_prediction_windows(predictions_path)
    fit_kinematics, calibration_kinematics = _select_kinematics_windows(
        _load_kinematics(kinematics_path),
        windows,
        calibration_rows,
    )
    candidates = _load_stable_segment_candidates(
        stable_segment_candidates_path,
        stations=set(windows["station"]),
    )
    fit_prediction_input_sha256 = _sha256_canonical_csv(
        fit_rows,
        columns=_PREDICTION_COLUMNS,
        sort_columns=("station", "date"),
    )
    fit_kinematics_input_sha256 = _sha256_canonical_csv(
        fit_kinematics,
        columns=_KINEMATICS_COLUMNS,
        sort_columns=("station", "date"),
    )
    _validate_stable_segment_candidate_provenance(
        candidates,
        windows=windows,
        fit_prediction_input_sha256=fit_prediction_input_sha256,
        fit_kinematics_input_sha256=fit_kinematics_input_sha256,
        expected_source_alignment=expected_source_alignment,
    )
    return _VelocityTangentInputs(
        fit_prediction_rows=fit_rows,
        calibration_prediction_rows=calibration_rows,
        windows=windows,
        fit_kinematics=fit_kinematics,
        calibration_kinematics=calibration_kinematics,
        stable_segment_candidates=candidates,
        fit_prediction_input_sha256=fit_prediction_input_sha256,
        fit_kinematics_input_sha256=fit_kinematics_input_sha256,
    )


def _expected_candidate_source_alignment(protocol: dict) -> dict[str, str]:
    """Read the active protocol's identity for its draft V0 candidate."""

    try:
        candidate = protocol["confirmed"]["v0_framework"][
            "stable_segment_candidate"
        ]
    except KeyError as exc:
        raise ValueError(
            "diagnostic protocol is missing stable-segment source-alignment metadata"
        ) from exc
    if not isinstance(candidate, dict):
        raise ValueError(
            "diagnostic protocol stable-segment source-alignment metadata is invalid"
        )

    alignment: dict[str, str] = {}
    for field in _CANDIDATE_SOURCE_ALIGNMENT_FIELDS:
        value = candidate.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                "diagnostic protocol is missing stable-segment source-alignment "
                f"field: {field}"
            )
        alignment[field] = value.strip()
    return alignment


def _load_protocol_checked_inputs(
    *,
    kinematics_path: str | Path,
    predictions_path: str | Path,
    stable_segment_candidates_path: str | Path,
    protocol_path: str | Path,
) -> tuple[_VelocityTangentInputs, dict, str]:
    """Load inputs only after proving their V0 candidate matches this protocol."""

    protocol = load_protocol(protocol_path)
    expected_source_alignment = _expected_candidate_source_alignment(protocol)
    inputs = _load_inputs(
        kinematics_path=kinematics_path,
        predictions_path=predictions_path,
        stable_segment_candidates_path=stable_segment_candidates_path,
        expected_source_alignment=expected_source_alignment,
    )
    protocol_sha256 = protocol_content_sha256(protocol)
    candidate_protocol_hashes = set(
        inputs.stable_segment_candidates["protocol_content_sha256"]
    )
    if candidate_protocol_hashes != {protocol_sha256}:
        raise ValueError(
            "stable-segment candidate protocol fingerprint does not match "
            "the diagnostic protocol"
        )
    return inputs, protocol, protocol_sha256


def _append_distribution_statistics(
    record: dict[str, object],
    *,
    metric: str,
    values: pd.Series,
    suffix: str = "",
) -> None:
    """Write raw descriptive values, never a threshold recommendation."""

    finite = values.loc[np.isfinite(values.to_numpy(dtype=float))]
    statistics = {
        "min": float(finite.min()) if not finite.empty else np.nan,
        "max": float(finite.max()) if not finite.empty else np.nan,
        "mean": float(finite.mean()) if not finite.empty else np.nan,
        "median": float(finite.median()) if not finite.empty else np.nan,
        "std_ddof1": float(finite.std(ddof=1)) if len(finite) > 1 else np.nan,
        "p10": float(finite.quantile(0.10)) if not finite.empty else np.nan,
        "p90": float(finite.quantile(0.90)) if not finite.empty else np.nan,
    }
    for statistic, value in statistics.items():
        record[f"{metric}_{statistic}{suffix}"] = value


def _summary_rows(
    frame: pd.DataFrame,
    *,
    source_split: str,
    temporal_scope: str,
    candidates: pd.DataFrame,
) -> pd.DataFrame:
    candidate_by_station = candidates.set_index("station", drop=False)
    rows: list[dict[str, object]] = []
    for station, station_rows in frame.groupby("station", sort=True):
        candidate = candidate_by_station.loc[station]
        selection_status = str(candidate["selection_status"])
        v0 = float(candidate["V0"]) if pd.notna(candidate["V0"]) else np.nan
        candidate_available = (
            selection_status == "selected" and np.isfinite(v0) and v0 > 0
        )
        numeric = station_rows["velocity"]
        valid_status = station_rows["velocity_status"].eq("valid").fillna(False)
        finite = pd.Series(
            np.isfinite(numeric.to_numpy(dtype=float)),
            index=station_rows.index,
            dtype=bool,
        )
        valid = valid_status & finite
        velocity = numeric.loc[valid]
        record: dict[str, object] = {
            "source_split": source_split,
            "kinematics_temporal_scope": temporal_scope,
            "station": station,
            "candidate_status": str(candidate["candidate_status"]),
            "candidate_method_id": str(candidate["candidate_method_id"]),
            "candidate_method_role": str(candidate["candidate_method_role"]),
            "word_thesis_v0_input": str(candidate["word_thesis_v0_input"]),
            "word_thesis_v0_input_status": str(
                candidate["word_thesis_v0_input_status"]
            ),
            "stable_segment_candidate_protocol_content_sha256": str(
                candidate["protocol_content_sha256"]
            ),
            "candidate_selection_status": selection_status,
            "candidate_failure_reason": candidate["failure_reason"],
            "candidate_v0_mm_per_day": v0,
            "candidate_v0_available": bool(candidate_available),
            "n_rows": int(len(station_rows)),
            "n_valid_velocity": int(valid.sum()),
            "n_warmup": int(station_rows["velocity_status"].eq("warmup").sum()),
            "n_nonvalid_status": int((~valid_status).sum()),
            "n_nonfinite_velocity": int((valid_status & ~finite).sum()),
        }
        _append_distribution_statistics(
            record,
            metric="velocity",
            values=velocity,
            suffix="_mm_per_day",
        )
        if candidate_available:
            velocity_over_v0 = velocity / v0
            velocity_minus_v0 = velocity - v0
            tangent_angle = pd.Series(
                np.degrees(np.arctan(velocity_over_v0.to_numpy(dtype=float))),
                index=velocity.index,
                dtype=float,
            )
            tangent_angle_minus_45 = tangent_angle - 45.0
        else:
            velocity_over_v0 = pd.Series(dtype=float)
            velocity_minus_v0 = pd.Series(dtype=float)
            tangent_angle = pd.Series(dtype=float)
            tangent_angle_minus_45 = pd.Series(dtype=float)
        _append_distribution_statistics(
            record,
            metric="velocity_over_v0",
            values=velocity_over_v0,
        )
        _append_distribution_statistics(
            record,
            metric="velocity_minus_v0",
            values=velocity_minus_v0,
            suffix="_mm_per_day",
        )
        _append_distribution_statistics(
            record,
            metric="tangent_angle",
            values=tangent_angle,
            suffix="_degree",
        )
        _append_distribution_statistics(
            record,
            metric="tangent_angle_minus_45",
            values=tangent_angle_minus_45,
            suffix="_degree",
        )
        _append_distribution_statistics(
            record,
            metric="abs_tangent_angle_minus_45",
            values=tangent_angle_minus_45.abs(),
            suffix="_degree",
        )
        rows.append(record)
    return pd.DataFrame(rows)


def _build_summary(inputs: _VelocityTangentInputs) -> pd.DataFrame:
    return pd.concat(
        [
            _summary_rows(
                inputs.fit_kinematics,
                source_split=FIT_SPLIT,
                temporal_scope=FIT_KINEMATICS_SCOPE,
                candidates=inputs.stable_segment_candidates,
            ),
            _summary_rows(
                inputs.calibration_kinematics,
                source_split=CALIBRATION_SPLIT,
                temporal_scope=CALIBRATION_KINEMATICS_SCOPE,
                candidates=inputs.stable_segment_candidates,
            ),
        ],
        ignore_index=True,
    )


def build_velocity_tangent_diagnostics(
    *,
    kinematics_path: str | Path,
    predictions_path: str | Path,
    stable_segment_candidates_path: str | Path,
    protocol_path: str | Path = DEFAULT_PROTOCOL_PATH,
) -> pd.DataFrame:
    """Return raw fit/calibration summaries without a tolerance decision."""

    inputs, _, _ = _load_protocol_checked_inputs(
        kinematics_path=kinematics_path,
        predictions_path=predictions_path,
        stable_segment_candidates_path=stable_segment_candidates_path,
        protocol_path=protocol_path,
    )
    return _build_summary(inputs)


def write_velocity_tangent_diagnostics(
    *,
    kinematics_path: str | Path = DEFAULT_KINEMATICS_PATH,
    predictions_path: str | Path = DEFAULT_PREDICTIONS_PATH,
    stable_segment_candidates_path: str | Path = DEFAULT_STABLE_SEGMENT_CANDIDATES_PATH,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    protocol_path: str | Path = DEFAULT_PROTOCOL_PATH,
) -> VelocityTangentDiagnosticArtifacts:
    """Write a draft V0/tangent evidence table and a non-formal manifest."""

    inputs, protocol, protocol_sha256 = _load_protocol_checked_inputs(
        kinematics_path=kinematics_path,
        predictions_path=predictions_path,
        stable_segment_candidates_path=stable_segment_candidates_path,
        protocol_path=protocol_path,
    )
    summary = _build_summary(inputs)
    prediction_hashes = {
        FIT_SPLIT: inputs.fit_prediction_input_sha256,
        CALIBRATION_SPLIT: _sha256_canonical_csv(
            inputs.calibration_prediction_rows,
            columns=_PREDICTION_COLUMNS,
            sort_columns=("station", "date"),
        ),
    }
    kinematics_hashes = {
        FIT_SPLIT: inputs.fit_kinematics_input_sha256,
        CALIBRATION_SPLIT: _sha256_canonical_csv(
            inputs.calibration_kinematics,
            columns=_KINEMATICS_COLUMNS,
            sort_columns=("station", "date"),
        ),
    }
    candidates_hash = _sha256_canonical_csv(
        inputs.stable_segment_candidates,
        columns=_CANDIDATE_COLUMNS,
        sort_columns=("station",),
    )
    candidate_method = {
        "id": str(inputs.stable_segment_candidates["candidate_method_id"].iloc[0]),
        "role": str(
            inputs.stable_segment_candidates["candidate_method_role"].iloc[0]
        ),
        "word_thesis_v0_input": str(
            inputs.stable_segment_candidates["word_thesis_v0_input"].iloc[0]
        ),
        "word_thesis_v0_input_status": str(
            inputs.stable_segment_candidates["word_thesis_v0_input_status"].iloc[0]
        ),
    }

    summary = summary.copy()
    summary.insert(
        0, "stable_segment_candidates_sha256", candidates_hash
    )
    summary.insert(
        0, "kinematics_input_sha256", summary["source_split"].map(kinematics_hashes)
    )
    summary.insert(
        0, "prediction_window_sha256", summary["source_split"].map(prediction_hashes)
    )
    metadata = {
        "protocol_id": protocol["protocol_id"],
        "protocol_version": protocol["protocol_version"],
        "protocol_status": protocol["status"],
        "protocol_content_sha256": protocol_sha256,
        "diagnostic_status": DIAGNOSTIC_STATUS,
    }
    for column, value in reversed(tuple(metadata.items())):
        summary.insert(0, column, value)

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / SUMMARY_FILENAME
    manifest_path = out_dir / MANIFEST_FILENAME
    summary.to_csv(summary_path, index=False)

    manifest = {
        "artifact_kind": "ootang_velocity_tangent_fit_calibration_diagnostics",
        "diagnostic_status": DIAGNOSTIC_STATUS,
        "formal_warning_output": False,
        "protocol": {
            "id": protocol["protocol_id"],
            "version": protocol["protocol_version"],
            "status": protocol["status"],
            "content_sha256": protocol_sha256,
            "unresolved_item_ids": list(unresolved_item_ids(protocol)),
        },
        "selection": {
            "splits": {
                FIT_SPLIT: {
                    "temporal_scope": FIT_KINEMATICS_SCOPE,
                    "fit_end_dates": {
                        row.station: row.fit_end_date.strftime("%Y-%m-%d")
                        for row in inputs.windows.itertuples(index=False)
                    },
                    "n_kinematics_rows": int(len(inputs.fit_kinematics)),
                },
                CALIBRATION_SPLIT: {
                    "temporal_scope": CALIBRATION_KINEMATICS_SCOPE,
                    "n_exact_prediction_dates_by_station": {
                        station: int(n_dates)
                        for station, n_dates in inputs.calibration_prediction_rows.groupby(
                            "station",
                            sort=True,
                        )["date"]
                        .nunique()
                        .items()
                    },
                    "windows": {
                        row.station: {
                            "start_date": row.calibration_start_date.strftime(
                                "%Y-%m-%d"
                            ),
                            "end_date": row.calibration_end_date.strftime("%Y-%m-%d"),
                        }
                        for row in inputs.windows.itertuples(index=False)
                    },
                    "n_kinematics_rows": int(len(inputs.calibration_kinematics)),
                },
            },
            "candidate_v0_dependency": {
                "status": "draft_candidate_not_formal",
                "candidate_method": candidate_method,
                "formula": "tangent_angle_degree=atan(velocity/V0)*180/pi",
                "does_not_assign": [
                    "velocity_level",
                    "tangent_angle_level",
                    "v0_blue_tolerance",
                    "tangent_blue_tolerance",
                ],
            },
        },
        "source_predictions": {
            "path": str(Path(predictions_path)),
            "selected_columns": list(_PREDICTION_COLUMNS),
            "selected_splits": [FIT_SPLIT, CALIBRATION_SPLIT],
        },
        "source_kinematics": {
            "path": str(Path(kinematics_path)),
            "selected_columns": list(_KINEMATICS_COLUMNS),
        },
        "source_stable_segment_candidates": {
            "path": str(Path(stable_segment_candidates_path)),
            "selected_columns": list(_CANDIDATE_COLUMNS),
            "protocol_content_sha256": protocol_sha256,
            "candidate_status": DRAFT_CANDIDATE_STATUS,
            "candidate_method": candidate_method,
            "source_split": FIT_SPLIT,
            "kinematics_temporal_scope": FIT_KINEMATICS_SCOPE,
            "fit_prediction_input_sha256": inputs.fit_prediction_input_sha256,
            "fit_kinematics_input_sha256": inputs.fit_kinematics_input_sha256,
            "fit_end_dates": {
                row.station: row.fit_end_date.strftime("%Y-%m-%d")
                for row in inputs.windows.itertuples(index=False)
            },
        },
        "inputs": {
            "fit_prediction_window": {
                "sha256": prediction_hashes[FIT_SPLIT],
                "n_rows": int(len(inputs.fit_prediction_rows)),
            },
            "calibration_prediction_window": {
                "sha256": prediction_hashes[CALIBRATION_SPLIT],
                "n_rows": int(len(inputs.calibration_prediction_rows)),
            },
            "fit_kinematics": {
                "sha256": kinematics_hashes[FIT_SPLIT],
                "n_rows": int(len(inputs.fit_kinematics)),
            },
            "calibration_kinematics": {
                "sha256": kinematics_hashes[CALIBRATION_SPLIT],
                "n_rows": int(len(inputs.calibration_kinematics)),
            },
            "stable_segment_candidates": {
                "sha256": candidates_hash,
                "n_rows": int(len(inputs.stable_segment_candidates)),
            },
        },
        "outputs": {
            "summary": {
                "path": str(summary_path),
                "sha256": _sha256_file(summary_path),
                "n_rows": int(len(summary)),
            },
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return VelocityTangentDiagnosticArtifacts(
        summary_path=summary_path,
        manifest_path=manifest_path,
        n_summary_rows=int(len(summary)),
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write fit/calibration V0 and tangent-angle diagnostic evidence."
    )
    parser.add_argument("--kinematics", default=DEFAULT_KINEMATICS_PATH)
    parser.add_argument("--predictions", default=DEFAULT_PREDICTIONS_PATH)
    parser.add_argument(
        "--stable-segment-candidates",
        default=DEFAULT_STABLE_SEGMENT_CANDIDATES_PATH,
    )
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--protocol", default=DEFAULT_PROTOCOL_PATH)
    return parser.parse_args()


def main() -> None:
    """Write the diagnostic artifacts and report their locations."""

    args = _parse_args()
    artifacts = write_velocity_tangent_diagnostics(
        kinematics_path=args.kinematics,
        predictions_path=args.predictions,
        stable_segment_candidates_path=args.stable_segment_candidates,
        output_dir=args.output_dir,
        protocol_path=args.protocol,
    )
    print(
        "[velocity-tangent-diagnostics] "
        f"rows={artifacts.n_summary_rows}, summary={artifacts.summary_path}, "
        f"manifest={artifacts.manifest_path}"
    )


if __name__ == "__main__":
    main()


__all__ = [
    "CALIBRATION_KINEMATICS_SCOPE",
    "DIAGNOSTIC_STATUS",
    "FIT_KINEMATICS_SCOPE",
    "VelocityTangentDiagnosticArtifacts",
    "build_velocity_tangent_diagnostics",
    "write_velocity_tangent_diagnostics",
]
