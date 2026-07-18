"""Write raw fit/calibration ΔV diagnostics without choosing a tolerance.

The protocol fixes ``ΔV_i = v_i - v_{i-1}`` as a qualitative auxiliary input,
but the specified literature supplies no Ootang-specific numerical definition
of ``ΔV≈0``.  This runner records only fit/calibration distribution evidence;
it never creates negative/near-zero/positive states, a tolerance, a warning
level, or a formal warning output.
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
DEFAULT_OUTPUT_DIR = ROOT / "figures" / "warning_draft"
SUMMARY_FILENAME = "delta_v_fit_calibration_diagnostics.csv"
MANIFEST_FILENAME = "delta_v_fit_calibration_diagnostics_manifest.json"
FIT_SPLIT = "fit"
CALIBRATION_SPLIT = "calibration"
DIAGNOSTIC_STATUS = "diagnostic_only_no_tolerance_decision"
FIT_KINEMATICS_SCOPE = "all_station_history_through_fit_cutoff"
CALIBRATION_KINEMATICS_SCOPE = "exact_station_calibration_prediction_dates"
_PREDICTION_COLUMNS = ("date", "station", "split")
_KINEMATICS_COLUMNS = ("date", "station", "delta_v", "delta_v_status")


@dataclass(frozen=True)
class DeltaVDiagnosticArtifacts:
    """Paths and row count for one non-formal ΔV diagnostic run."""

    summary_path: Path
    manifest_path: Path
    n_summary_rows: int


@dataclass(frozen=True)
class _DeltaVInputs:
    """Selected non-test inputs required to reproduce a ΔV summary."""

    fit_prediction_rows: pd.DataFrame
    calibration_prediction_rows: pd.DataFrame
    windows: pd.DataFrame
    fit_kinematics: pd.DataFrame
    calibration_kinematics: pd.DataFrame


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
) -> str:
    """Hash one selected input slice, never an entire source file."""

    canonical = (
        frame.loc[:, columns]
        .sort_values(["station", "date"], kind="stable")
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


def _require_unique_prediction_coordinates(
    rows: pd.DataFrame,
    *,
    split: str,
) -> None:
    """Reject ambiguous repeated prediction coordinates within one input split."""

    duplicate_rows = rows.duplicated(["station", "date"], keep=False)
    if not duplicate_rows.any():
        return
    duplicate_keys = (
        rows.loc[duplicate_rows, ["station", "date"]]
        .drop_duplicates()
        .sort_values(["station", "date"], kind="stable")
    )
    raise ValueError(
        f"{split} prediction contains duplicate station/date rows: "
        f"{duplicate_keys.to_dict(orient='records')}"
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
    _require_unique_prediction_coordinates(fit_rows, split=FIT_SPLIT)
    _require_unique_prediction_coordinates(
        calibration_rows,
        split=CALIBRATION_SPLIT,
    )

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
    """Load raw kinematics without validating irrelevant stations' dates.

    Selection by the fit/calibration prediction stations must precede date and
    numeric validation.  Otherwise an unrelated malformed record could alter
    whether a fit/calibration-only artifact can be generated.
    """

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
    stations = set(windows["station"])
    kinematics = kinematics.copy()
    kinematics["station"] = kinematics["station"].astype("string").str.strip()
    kinematics = kinematics.loc[kinematics["station"].isin(stations)].copy()
    missing = sorted(stations.difference(set(kinematics["station"])))
    if missing:
        raise ValueError(
            f"kinematics is missing stations required by prediction windows: {missing}"
        )

    kinematics = _normalize_date_and_station(
        kinematics,
        source_name="selected kinematics",
    )
    kinematics["delta_v"] = pd.to_numeric(kinematics["delta_v"], errors="coerce")
    kinematics["delta_v_status"] = kinematics["delta_v_status"].astype("string")

    fit_parts: list[pd.DataFrame] = []
    calibration_parts: list[pd.DataFrame] = []
    for window in windows.itertuples(index=False):
        station_rows = kinematics.loc[kinematics["station"].eq(window.station)]
        fit_rows = station_rows.loc[station_rows["date"] <= window.fit_end_date].copy()
        if fit_rows.empty:
            raise ValueError(
                "kinematics contains no rows through the fit cutoff for station "
                f"{window.station}"
            )
        fit_parts.append(fit_rows)

        expected_calibration_dates = pd.Index(
            calibration_prediction_rows.loc[
                calibration_prediction_rows["station"].eq(window.station), "date"
            ].unique()
        )
        calibration_rows = station_rows.loc[
            station_rows["date"].isin(expected_calibration_dates)
        ].copy()
        missing_calibration_dates = sorted(
            set(expected_calibration_dates).difference(set(calibration_rows["date"]))
        )
        if missing_calibration_dates:
            missing_text = ", ".join(
                date.strftime("%Y-%m-%d") for date in missing_calibration_dates
            )
            raise ValueError(
                "kinematics is missing exact calibration prediction dates for "
                f"station {window.station}: {missing_text}"
            )
        calibration_parts.append(calibration_rows)
    fit_kinematics = pd.concat(fit_parts, ignore_index=True)
    calibration_kinematics = pd.concat(calibration_parts, ignore_index=True)
    for scope, selected_rows in (
        (FIT_SPLIT, fit_kinematics),
        (CALIBRATION_SPLIT, calibration_kinematics),
    ):
        duplicate_rows = selected_rows.duplicated(["station", "date"], keep=False)
        if duplicate_rows.any():
            duplicate_keys = (
                selected_rows.loc[duplicate_rows, ["station", "date"]]
                .drop_duplicates()
                .sort_values(["station", "date"], kind="stable")
            )
            raise ValueError(
                f"{scope} kinematics contains duplicate station/date rows: "
                f"{duplicate_keys.to_dict(orient='records')}"
            )
    return (
        fit_kinematics,
        calibration_kinematics,
    )


def _load_inputs(
    *,
    kinematics_path: str | Path,
    predictions_path: str | Path,
) -> _DeltaVInputs:
    fit_prediction_rows, calibration_prediction_rows, windows = (
        _load_prediction_windows(predictions_path)
    )
    fit_kinematics, calibration_kinematics = _select_kinematics_windows(
        _load_kinematics(kinematics_path),
        windows,
        calibration_prediction_rows,
    )
    return _DeltaVInputs(
        fit_prediction_rows=fit_prediction_rows,
        calibration_prediction_rows=calibration_prediction_rows,
        windows=windows,
        fit_kinematics=fit_kinematics,
        calibration_kinematics=calibration_kinematics,
    )


def _summary_rows(
    frame: pd.DataFrame,
    *,
    source_split: str,
    temporal_scope: str,
) -> pd.DataFrame:
    rows = []
    for station, station_rows in frame.groupby("station", sort=True):
        numeric = station_rows["delta_v"]
        valid_status = station_rows["delta_v_status"].eq("valid").fillna(False)
        finite = pd.Series(
            np.isfinite(numeric.to_numpy(dtype=float)),
            index=station_rows.index,
            dtype=bool,
        )
        valid = valid_status & finite
        values = numeric.loc[valid]
        rows.append(
            {
                "source_split": source_split,
                "kinematics_temporal_scope": temporal_scope,
                "station": station,
                "n_rows": int(len(station_rows)),
                "n_valid_delta_v": int(valid.sum()),
                "n_warmup": int(station_rows["delta_v_status"].eq("warmup").sum()),
                "n_nonvalid_status": int((~valid_status).sum()),
                "n_nonfinite_delta_v": int((valid_status & ~finite).sum()),
                "delta_v_min": float(values.min()) if not values.empty else np.nan,
                "delta_v_max": float(values.max()) if not values.empty else np.nan,
                "delta_v_mean": float(values.mean()) if not values.empty else np.nan,
                "delta_v_median": float(values.median())
                if not values.empty
                else np.nan,
                "delta_v_std_ddof1": (
                    float(values.std(ddof=1)) if len(values) > 1 else np.nan
                ),
                "mean_abs_delta_v": (
                    float(values.abs().mean()) if not values.empty else np.nan
                ),
                "median_abs_delta_v": (
                    float(values.abs().median()) if not values.empty else np.nan
                ),
                "max_abs_delta_v": (
                    float(values.abs().max()) if not values.empty else np.nan
                ),
            }
        )
    return pd.DataFrame(rows)


def _build_summary(inputs: _DeltaVInputs) -> pd.DataFrame:
    return pd.concat(
        [
            _summary_rows(
                inputs.fit_kinematics,
                source_split=FIT_SPLIT,
                temporal_scope=FIT_KINEMATICS_SCOPE,
            ),
            _summary_rows(
                inputs.calibration_kinematics,
                source_split=CALIBRATION_SPLIT,
                temporal_scope=CALIBRATION_KINEMATICS_SCOPE,
            ),
        ],
        ignore_index=True,
    )


def build_delta_v_diagnostics(
    *,
    kinematics_path: str | Path,
    predictions_path: str | Path,
) -> pd.DataFrame:
    """Return fit/calibration raw ΔV summaries without a tolerance decision."""

    return _build_summary(
        _load_inputs(
            kinematics_path=kinematics_path,
            predictions_path=predictions_path,
        )
    )


def write_delta_v_diagnostics(
    *,
    kinematics_path: str | Path = DEFAULT_KINEMATICS_PATH,
    predictions_path: str | Path = DEFAULT_PREDICTIONS_PATH,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    protocol_path: str | Path = DEFAULT_PROTOCOL_PATH,
) -> DeltaVDiagnosticArtifacts:
    """Write a draft ΔV evidence table and non-formal manifest."""

    inputs = _load_inputs(
        kinematics_path=kinematics_path,
        predictions_path=predictions_path,
    )
    protocol = load_protocol(protocol_path)
    protocol_sha256 = protocol_content_sha256(protocol)
    summary = _build_summary(inputs)
    prediction_hashes = {
        FIT_SPLIT: _sha256_canonical_csv(
            inputs.fit_prediction_rows,
            columns=_PREDICTION_COLUMNS,
        ),
        CALIBRATION_SPLIT: _sha256_canonical_csv(
            inputs.calibration_prediction_rows,
            columns=_PREDICTION_COLUMNS,
        ),
    }
    kinematics_hashes = {
        FIT_SPLIT: _sha256_canonical_csv(
            inputs.fit_kinematics,
            columns=_KINEMATICS_COLUMNS,
        ),
        CALIBRATION_SPLIT: _sha256_canonical_csv(
            inputs.calibration_kinematics,
            columns=_KINEMATICS_COLUMNS,
        ),
    }

    summary = summary.copy()
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
        "artifact_kind": "ootang_delta_v_fit_calibration_diagnostics",
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
            }
        },
        "source_predictions": {
            "path": str(Path(predictions_path)),
            "selected_columns": list(_PREDICTION_COLUMNS),
            "selected_splits": [FIT_SPLIT, CALIBRATION_SPLIT],
        },
        "source_kinematics": {
            "path": str(Path(kinematics_path)),
            "selected_columns": list(_KINEMATICS_COLUMNS),
            "fit_time_filter": "station_date<=station_fit_end_date",
            "calibration_time_filter": "station_date_is_exact_calibration_prediction_date",
        },
        "inputs": {
            "fit_prediction_window": {
                "sha256": prediction_hashes[FIT_SPLIT],
                "canonicalization": "csv_utf8_lf_na_<NA>_float_%.17g",
            },
            "calibration_prediction_window": {
                "sha256": prediction_hashes[CALIBRATION_SPLIT],
                "canonicalization": "csv_utf8_lf_na_<NA>_float_%.17g",
            },
            "fit_kinematics": {
                "sha256": kinematics_hashes[FIT_SPLIT],
                "canonicalization": "csv_utf8_lf_na_<NA>_float_%.17g",
            },
            "calibration_kinematics": {
                "sha256": kinematics_hashes[CALIBRATION_SPLIT],
                "canonicalization": "csv_utf8_lf_na_<NA>_float_%.17g",
            },
        },
        "summary": {
            "path": str(summary_path),
            "sha256": _sha256_file(summary_path),
            "columns": list(summary.columns),
        },
        "not_evaluated": [
            "delta_v_near_zero_tolerance",
            "delta_v_state_mapping",
            "five_level_delta_v_mapping",
            "test_split_threshold_selection",
        ],
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return DeltaVDiagnosticArtifacts(
        summary_path=summary_path,
        manifest_path=manifest_path,
        n_summary_rows=len(summary),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write fit/calibration ΔV diagnostics without choosing a tolerance."
    )
    parser.add_argument("--kinematics", type=Path, default=DEFAULT_KINEMATICS_PATH)
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL_PATH)
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    artifacts = write_delta_v_diagnostics(
        kinematics_path=args.kinematics,
        predictions_path=args.predictions,
        output_dir=args.output_dir,
        protocol_path=args.protocol,
    )
    print(
        "wrote fit/calibration ΔV diagnostics "
        f"({artifacts.n_summary_rows} station-split summaries): {artifacts.summary_path}"
    )


if __name__ == "__main__":
    main()


__all__ = [
    "CALIBRATION_SPLIT",
    "DIAGNOSTIC_STATUS",
    "FIT_SPLIT",
    "DeltaVDiagnosticArtifacts",
    "build_delta_v_diagnostics",
    "write_delta_v_diagnostics",
]
