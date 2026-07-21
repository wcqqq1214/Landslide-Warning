"""Materialize fit-only MVIF initial-slope candidates and provenance.

This runner is intentionally separate from the strict finite-``t_f`` audit and
from the raw-velocity KMeans comparator.  It writes only the user-approved
project-adaptation candidate, with protocol and input fingerprints, and never
turns it into a velocity/tangent/fusion warning result.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

import pandas as pd


CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning.mvif_diagnostics import (  # noqa: E402
    DEFAULT_KINEMATICS_PATH,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_PREDICTIONS_PATH,
    FIT_SPLIT,
    KINEMATICS_TEMPORAL_SCOPE,
    _FIT_PREDICTION_COLUMNS,
    _KINEMATICS_COLUMNS,
    _FitSelectionInputs,
    _load_fit_selection_inputs,
    _sha256_canonical_csv,
)
from warning.mvif_initial_slope import (  # noqa: E402
    CONVEXITY_WINDOW_DAYS,
    PROFILE_BISECTION_ITERATIONS,
    PROFILE_CONFIDENCE_LEVEL,
    PROFILE_EXPANSION_FACTOR,
    PROFILE_MAX_EXPANSIONS,
    PROFILE_MAX_FUNCTION_EVALUATIONS,
    SIGMA_DDOF,
    UNIFORM_L_LOWER,
    UNIFORM_L_UPPER,
    select_mvif_initial_stable_slope,
)
from warning.protocol import (  # noqa: E402
    DEFAULT_PROTOCOL_PATH,
    load_protocol,
    protocol_content_sha256,
    unresolved_item_ids,
)


SUMMARY_FILENAME = "mvif_initial_slope_candidates.csv"
MANIFEST_FILENAME = "mvif_initial_slope_candidates_manifest.json"
CANDIDATE_STATUS = "draft_candidate_not_formal"


@dataclass(frozen=True)
class MvifInitialSlopeCandidateArtifacts:
    """Paths and station count for one non-formal candidate run."""

    summary_path: Path
    manifest_path: Path
    n_stations: int


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _candidate_records(inputs: _FitSelectionInputs) -> pd.DataFrame:
    records = []
    for boundary in inputs.fit_boundaries.itertuples(index=False):
        station_kinematics = inputs.fit_kinematics.loc[
            inputs.fit_kinematics["station"].eq(boundary.station),
            ["date", "displacement", "displacement_valid"],
        ]
        result = select_mvif_initial_stable_slope(
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


def _candidate_method_metadata(protocol: dict) -> dict[str, Any]:
    """Read and validate the explicit project-adaptation boundary."""

    candidate = protocol["confirmed"]["v0_framework"][
        "mvif_initial_slope_profile_candidate"
    ]
    if candidate.get("status") != CANDIDATE_STATUS:
        raise ValueError(
            "draft protocol MVIF initial-slope candidate status must be "
            f"{CANDIDATE_STATUS}"
        )
    required_fields = (
        "candidate_method_id",
        "candidate_method_role",
        "word_thesis_v0_input",
        "word_thesis_v0_input_status",
        "formula",
        "input",
        "time_origin",
        "objective",
        "daily_sampling_policy",
        "failure_policy",
    )
    metadata: dict[str, Any] = {}
    for field in required_fields:
        value = candidate.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                "draft protocol MVIF initial-slope candidate is missing source "
                f"alignment field: {field}"
            )
        metadata[field] = value
    if candidate.get("fit_only") is not True:
        raise ValueError("draft protocol MVIF initial-slope candidate must be fit-only")

    selection = candidate.get("trend_selection")
    if not isinstance(selection, dict):
        raise ValueError("draft protocol MVIF initial-slope trend_selection is missing")
    selection_rule = selection.get("selection_rule")
    if not isinstance(selection_rule, str) or not selection_rule:
        raise ValueError("draft protocol MVIF initial-slope selection_rule is missing")
    metadata["selection_rule"] = selection_rule
    _validate_runtime_policy(candidate)
    not_evaluated = candidate.get("not_evaluated")
    if not isinstance(not_evaluated, list) or not all(
        isinstance(value, str) and value for value in not_evaluated
    ):
        raise ValueError(
            "draft protocol MVIF initial-slope not_evaluated must be strings"
        )
    metadata["not_evaluated"] = not_evaluated
    return metadata


def _validate_runtime_policy(candidate: dict) -> None:
    """Reject artifacts whose stated numerical candidate differs from code."""

    selection = candidate.get("trend_selection")
    if not isinstance(selection, dict):
        raise ValueError("draft protocol MVIF initial-slope trend_selection is missing")
    expected_selection = {
        "window_days": CONVEXITY_WINDOW_DAYS,
        "uniform_l_interval": [UNIFORM_L_LOWER, UNIFORM_L_UPPER],
        "selection_rule": "earliest_contiguous_uniform_window_run_on_fitted_mvif_trend",
        "intercept_handling": "local_window_increment_ratio_for_C_invariance",
    }
    for field, expected in expected_selection.items():
        if selection.get(field) != expected:
            raise ValueError(
                f"draft protocol MVIF initial-slope trend_selection.{field} "
                "does not match the runtime setting"
            )

    profile = candidate.get("target_profile")
    if not isinstance(profile, dict):
        raise ValueError("draft protocol MVIF initial-slope target_profile is missing")
    expected_profile = {
        "confidence_level": PROFILE_CONFIDENCE_LEVEL,
        "optimizer": "scipy_least_squares_trf_3point_jacobian_x_scale_jac",
        "max_function_evaluations_per_start": PROFILE_MAX_FUNCTION_EVALUATIONS,
        "max_expansions_per_bound": PROFILE_MAX_EXPANSIONS,
        "bisection_iterations_per_bound": PROFILE_BISECTION_ITERATIONS,
        "expansion_factor": PROFILE_EXPANSION_FACTOR,
    }
    for field, expected in expected_profile.items():
        if profile.get(field) != expected:
            raise ValueError(
                f"draft protocol MVIF initial-slope target_profile.{field} "
                "does not match the runtime setting"
            )
    sigma = candidate.get("candidate_statistics")
    if not isinstance(sigma, dict) or sigma.get("sigma_ddof") != SIGMA_DDOF:
        raise ValueError(
            "draft protocol MVIF initial-slope candidate_statistics.sigma_ddof "
            "does not match the runtime setting"
        )


def build_fit_mvif_initial_slope_candidates(
    *,
    kinematics_path: str | Path,
    predictions_path: str | Path,
) -> pd.DataFrame:
    """Return fit-only trend-slope candidates with no formal warning fields."""

    return _candidate_records(
        _load_fit_selection_inputs(
            kinematics_path=kinematics_path,
            predictions_path=predictions_path,
        )
    )


def write_fit_mvif_initial_slope_candidates(
    *,
    kinematics_path: str | Path = DEFAULT_KINEMATICS_PATH,
    predictions_path: str | Path = DEFAULT_PREDICTIONS_PATH,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    protocol_path: str | Path = DEFAULT_PROTOCOL_PATH,
) -> MvifInitialSlopeCandidateArtifacts:
    """Write a profile-audited candidate table and a non-formal manifest."""

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
        "selection_rule": candidate_method["selection_rule"],
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
        "artifact_kind": "ootang_fit_mvif_initial_slope_candidates",
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
                "daily_sampling_policy": candidate_method["daily_sampling_policy"],
                "selection_rule": candidate_method["selection_rule"],
                "failure_policy": candidate_method["failure_policy"],
            },
            "source_boundary": {
                "source_uniform_method": "Wang_An_2023_raw_S_t_current_nearest_uniform_segment",
                "project_adaptation": "fitted_MVIF_trend_earliest_uniform_segment_with_selection_conditioned_target_profile",
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
    return MvifInitialSlopeCandidateArtifacts(
        summary_path=summary_path,
        manifest_path=manifest_path,
        n_stations=len(inputs.fit_boundaries),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Write fit-only MVIF initial-slope profile candidates without warnings."
        )
    )
    parser.add_argument("--kinematics", type=Path, default=DEFAULT_KINEMATICS_PATH)
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL_PATH)
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    artifacts = write_fit_mvif_initial_slope_candidates(
        kinematics_path=args.kinematics,
        predictions_path=args.predictions,
        output_dir=args.output_dir,
        protocol_path=args.protocol,
    )
    print(
        "wrote fit-only MVIF initial-slope candidates "
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
    "MvifInitialSlopeCandidateArtifacts",
    "SUMMARY_FILENAME",
    "build_fit_mvif_initial_slope_candidates",
    "write_fit_mvif_initial_slope_candidates",
]
