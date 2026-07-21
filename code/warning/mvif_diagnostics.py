"""Write fit-only MVIF finite-``t_f`` diagnostics without V0 or warnings."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
import sys

import pandas as pd

# Permit direct execution with ``uv run python code/warning/...py`` as well as
# package imports from the test suite.
CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning.mvif import (  # noqa: E402
    INITIAL_GAP_FACTORS,
    INITIAL_TAU_EXCESS,
    MAX_FUNCTION_EVALUATIONS_PER_START,
    fit_mvif_trend,
)
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
SUMMARY_FILENAME = "mvif_fit_candidates.csv"
MANIFEST_FILENAME = "mvif_fit_candidates_manifest.json"
FIT_SPLIT = "fit"
CANDIDATE_STATUS = "diagnostic_only_no_v0"
KINEMATICS_TEMPORAL_SCOPE = "all_station_history_through_fit_cutoff"
_FIT_PREDICTION_COLUMNS = ("date", "station", "split")
_KINEMATICS_COLUMNS = (
    "date",
    "station",
    "displacement",
    "displacement_valid",
)


@dataclass(frozen=True)
class MvifDiagnosticArtifacts:
    """Paths and station count for one non-formal MVIF diagnostic run."""

    summary_path: Path
    manifest_path: Path
    n_stations: int


@dataclass(frozen=True)
class _FitSelectionInputs:
    """Fit-period inputs needed to reproduce one MVIF diagnostic table."""

    fit_prediction_rows: pd.DataFrame
    fit_boundaries: pd.DataFrame
    fit_kinematics: pd.DataFrame


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


def _load_fit_prediction_rows(predictions_path: str | Path) -> pd.DataFrame:
    predictions = _read_required_columns(
        predictions_path,
        _FIT_PREDICTION_COLUMNS,
        source_name="prediction",
    )
    fit_rows = predictions.loc[
        predictions["split"].astype("string").eq(FIT_SPLIT)
    ].copy()
    if fit_rows.empty:
        raise ValueError("prediction file contains no split=fit rows")
    return _normalize_date_and_station(fit_rows, source_name="fit prediction")


def _load_kinematics(kinematics_path: str | Path) -> pd.DataFrame:
    kinematics = _read_required_columns(
        kinematics_path,
        _KINEMATICS_COLUMNS,
        source_name="kinematics",
    )
    return _normalize_date_and_station(kinematics, source_name="kinematics")


def _fit_boundaries(fit_prediction_rows: pd.DataFrame) -> pd.DataFrame:
    return (
        fit_prediction_rows.groupby("station", as_index=False, sort=True)["date"]
        .max()
        .rename(columns={"date": "fit_end_date"})
    )


def _load_fit_selection_inputs(
    *,
    kinematics_path: str | Path,
    predictions_path: str | Path,
) -> _FitSelectionInputs:
    fit_prediction_rows = _load_fit_prediction_rows(predictions_path)
    fit_boundaries = _fit_boundaries(fit_prediction_rows)
    kinematics = _load_kinematics(kinematics_path)

    boundary_stations = set(fit_boundaries["station"])
    kinematics = kinematics.loc[kinematics["station"].isin(boundary_stations)].copy()
    kinematic_stations = set(kinematics["station"])
    missing_kinematics = sorted(boundary_stations.difference(kinematic_stations))
    if missing_kinematics:
        raise ValueError(
            "kinematics is missing stations required by fit predictions: "
            f"{missing_kinematics}"
        )

    fit_kinematics_parts: list[pd.DataFrame] = []
    for boundary in fit_boundaries.itertuples(index=False):
        station_rows = kinematics.loc[kinematics["station"].eq(boundary.station)]
        fit_kinematics_parts.append(
            station_rows.loc[station_rows["date"] <= boundary.fit_end_date].copy()
        )
    return _FitSelectionInputs(
        fit_prediction_rows=fit_prediction_rows,
        fit_boundaries=fit_boundaries,
        fit_kinematics=pd.concat(fit_kinematics_parts, ignore_index=True),
    )


def _candidate_records(inputs: _FitSelectionInputs) -> pd.DataFrame:
    records = []
    for boundary in inputs.fit_boundaries.itertuples(index=False):
        station_kinematics = inputs.fit_kinematics.loc[
            inputs.fit_kinematics["station"].eq(boundary.station),
            ["date", "displacement", "displacement_valid"],
        ]
        result = fit_mvif_trend(
            station_kinematics,
            station=boundary.station,
            fit_end_date=boundary.fit_end_date,
        )
        records.append(result.to_record())
    return (
        pd.DataFrame(records)
        .sort_values("station", kind="stable")
        .reset_index(drop=True)
    )


def _candidate_method_metadata(protocol: dict) -> dict:
    candidate = protocol["confirmed"]["v0_framework"]["mvif_trend_fit_diagnostic"]
    if candidate.get("status") != "diagnostic_only_no_v0_until_initial_slope_rule_is_frozen":
        raise ValueError("draft protocol MVIF diagnostic status is not recognized")
    required_string_fields = (
        "candidate_method_id",
        "candidate_method_role",
        "word_thesis_v0_input",
        "word_thesis_v0_input_status",
        "formula",
        "input",
        "time_origin",
        "objective",
        "domain_parameterization",
        "failure_policy",
    )
    metadata: dict = {}
    for field in required_string_fields:
        value = candidate.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                "draft protocol MVIF diagnostic is missing source alignment field: "
                f"{field}"
            )
        metadata[field] = value
    if candidate.get("fit_only") is not True:
        raise ValueError("draft protocol MVIF diagnostic must remain fit-only")
    _validate_runtime_policy(candidate)
    not_evaluated = candidate.get("not_evaluated")
    if not isinstance(not_evaluated, list) or not all(
        isinstance(value, str) and value for value in not_evaluated
    ):
        raise ValueError("draft protocol MVIF diagnostic not_evaluated must be strings")
    metadata["not_evaluated"] = not_evaluated
    return metadata


def _validate_runtime_policy(candidate: dict) -> None:
    """Reject a draft protocol whose executable MVIF settings drifted from code.

    The protocol deliberately records the numerical audit settings so generated
    artifacts remain interpretable.  Runtime settings live in :mod:`mvif` to
    keep the public fitting seam deterministic; this guard prevents a later
    protocol edit from silently claiming a different numerical experiment.
    """

    multistart = candidate.get("deterministic_multistart")
    if not isinstance(multistart, dict):
        raise ValueError("draft protocol MVIF deterministic_multistart is missing")
    if multistart.get("normalized_tf_excess_duration_starts") != list(
        INITIAL_TAU_EXCESS
    ):
        raise ValueError(
            "draft protocol MVIF normalized_tf_excess_duration_starts "
            "does not match the runtime fitting settings"
        )
    if multistart.get("domain_gap_factor_starts") != list(INITIAL_GAP_FACTORS):
        raise ValueError(
            "draft protocol MVIF domain_gap_factor_starts does not match "
            "the runtime fitting settings"
        )
    if (
        multistart.get("optimizer")
        != "scipy_least_squares_trf_3point_jacobian_x_scale_jac"
    ):
        raise ValueError(
            "draft protocol MVIF optimizer does not match the runtime fitting settings"
        )
    if (
        multistart.get("max_function_evaluations_per_start")
        != MAX_FUNCTION_EVALUATIONS_PER_START
    ):
        raise ValueError(
            "draft protocol MVIF max_function_evaluations_per_start does not "
            "match the runtime fitting settings"
        )

    finite_gate = candidate.get("finite_tf_gate")
    if not isinstance(finite_gate, dict):
        raise ValueError("draft protocol MVIF finite_tf_gate is missing")
    required_gate_values = {
        "requires_finite_physical_parameters": True,
        "requires_successful_finite_optimizer_output_for_all_deterministic_starts": True,
        "requires_full_column_rank_jacobian_for_all_near_objective_starts": True,
        "near_objective_cost_tolerance": "sqrt(float64_machine_epsilon)",
        "tf_agreement_tolerance": "sqrt(float64_machine_epsilon)_on_log_tf_excess_duration",
    }
    for field, expected in required_gate_values.items():
        if finite_gate.get(field) != expected:
            raise ValueError(
                f"draft protocol MVIF finite_tf_gate.{field} does not match "
                "the runtime fitting settings"
            )


def build_fit_mvif_diagnostics(
    *,
    kinematics_path: str | Path,
    predictions_path: str | Path,
) -> pd.DataFrame:
    """Return per-station MVIF fit records using no data after the fit cutoff."""

    return _candidate_records(
        _load_fit_selection_inputs(
            kinematics_path=kinematics_path,
            predictions_path=predictions_path,
        )
    )


def write_fit_mvif_diagnostics(
    *,
    kinematics_path: str | Path = DEFAULT_KINEMATICS_PATH,
    predictions_path: str | Path = DEFAULT_PREDICTIONS_PATH,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    protocol_path: str | Path = DEFAULT_PROTOCOL_PATH,
) -> MvifDiagnosticArtifacts:
    """Write non-formal MVIF fit records and an auditable provenance manifest."""

    inputs = _load_fit_selection_inputs(
        kinematics_path=kinematics_path,
        predictions_path=predictions_path,
    )
    protocol = load_protocol(protocol_path)
    protocol_sha256 = protocol_content_sha256(protocol)
    candidate_method = _candidate_method_metadata(protocol)
    summary = _candidate_records(inputs)
    fit_prediction_sha256 = _sha256_canonical_csv(
        inputs.fit_prediction_rows,
        columns=_FIT_PREDICTION_COLUMNS,
        sort_columns=("station", "date"),
    )
    fit_kinematics_sha256 = _sha256_canonical_csv(
        inputs.fit_kinematics,
        columns=_KINEMATICS_COLUMNS,
        sort_columns=("station", "date"),
    )

    summary = summary.copy()
    metadata = {
        "protocol_id": protocol["protocol_id"],
        "protocol_version": protocol["protocol_version"],
        "protocol_status": protocol["status"],
        "protocol_content_sha256": protocol_sha256,
        "candidate_status": CANDIDATE_STATUS,
        "candidate_method_id": candidate_method["candidate_method_id"],
        "candidate_method_role": candidate_method["candidate_method_role"],
        "word_thesis_v0_input": candidate_method["word_thesis_v0_input"],
        "word_thesis_v0_input_status": candidate_method[
            "word_thesis_v0_input_status"
        ],
        "mvif_formula": candidate_method["formula"],
        "source_split": FIT_SPLIT,
        "kinematics_temporal_scope": KINEMATICS_TEMPORAL_SCOPE,
        "fit_prediction_input_sha256": fit_prediction_sha256,
        "fit_kinematics_input_sha256": fit_kinematics_sha256,
    }
    for column, value in reversed(tuple(metadata.items())):
        summary.insert(0, column, value)

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / SUMMARY_FILENAME
    manifest_path = out_dir / MANIFEST_FILENAME
    summary.to_csv(summary_path, index=False)

    manifest = {
        "artifact_kind": "ootang_fit_mvif_finite_tf_diagnostics",
        "candidate_status": CANDIDATE_STATUS,
        "formal_warning_output": False,
        "protocol": {
            "id": protocol["protocol_id"],
            "version": protocol["protocol_version"],
            "status": protocol["status"],
            "content_sha256": protocol_sha256,
            "unresolved_item_ids": list(unresolved_item_ids(protocol)),
        },
        "selection": {
            "split": FIT_SPLIT,
            "candidate_method": {
                "id": candidate_method["candidate_method_id"],
                "role": candidate_method["candidate_method_role"],
                "word_thesis_v0_input": candidate_method["word_thesis_v0_input"],
                "word_thesis_v0_input_status": candidate_method[
                    "word_thesis_v0_input_status"
                ],
                "formula": candidate_method["formula"],
                "input": candidate_method["input"],
                "time_origin": candidate_method["time_origin"],
                "objective": candidate_method["objective"],
                "domain_parameterization": candidate_method[
                    "domain_parameterization"
                ],
                "failure_policy": candidate_method["failure_policy"],
            },
            "n_stations": int(len(inputs.fit_boundaries)),
            "fit_end_dates": {
                row.station: row.fit_end_date.strftime("%Y-%m-%d")
                for row in inputs.fit_boundaries.itertuples(index=False)
            },
        },
        "source_predictions": {
            "path": str(Path(predictions_path)),
            "selected_columns": list(_FIT_PREDICTION_COLUMNS),
            "selected_split": FIT_SPLIT,
        },
        "source_kinematics": {
            "path": str(Path(kinematics_path)),
            "selected_columns": list(_KINEMATICS_COLUMNS),
            "time_filter": "station_date<=station_fit_end_date",
            "temporal_scope": KINEMATICS_TEMPORAL_SCOPE,
        },
        "fit_prediction_input": {
            "sha256": fit_prediction_sha256,
            "canonicalization": "csv_utf8_lf_na_<NA>_float_%.17g",
        },
        "fit_kinematics_input": {
            "sha256": fit_kinematics_sha256,
            "canonicalization": "csv_utf8_lf_na_<NA>_float_%.17g",
        },
        "summary": {
            "path": str(summary_path),
            "sha256": _sha256_file(summary_path),
            "columns": list(summary.columns),
        },
        "not_evaluated": candidate_method["not_evaluated"],
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return MvifDiagnosticArtifacts(
        summary_path=summary_path,
        manifest_path=manifest_path,
        n_stations=len(inputs.fit_boundaries),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write fit-only MVIF finite-tf diagnostics without V0 or warnings."
    )
    parser.add_argument("--kinematics", type=Path, default=DEFAULT_KINEMATICS_PATH)
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL_PATH)
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    artifacts = write_fit_mvif_diagnostics(
        kinematics_path=args.kinematics,
        predictions_path=args.predictions,
        output_dir=args.output_dir,
        protocol_path=args.protocol,
    )
    print(
        "wrote fit-only MVIF finite-tf diagnostics "
        f"({artifacts.n_stations} stations): {artifacts.summary_path}"
    )


if __name__ == "__main__":
    main()


__all__ = [
    "CANDIDATE_STATUS",
    "DEFAULT_KINEMATICS_PATH",
    "DEFAULT_OUTPUT_DIR",
    "DEFAULT_PREDICTIONS_PATH",
    "FIT_SPLIT",
    "KINEMATICS_TEMPORAL_SCOPE",
    "MANIFEST_FILENAME",
    "MvifDiagnosticArtifacts",
    "SUMMARY_FILENAME",
    "build_fit_mvif_diagnostics",
    "write_fit_mvif_diagnostics",
]
