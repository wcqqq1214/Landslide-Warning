"""Write strict-MVIF-gated Bai--Perron initial-slope draft diagnostics.

The designated Word thesis supplies the MVIF trend-displacement input to its
``V0`` framework but not an automatic initial-stable-segment algorithm.  This
runner therefore materializes a fit-only project adaptation: a strict accepted
MVIF curve is segmented with the Bai--Perron dynamic-programming/BIC route,
and only an initial segment followed immediately by a higher slope yields a
draft ``V`` candidate.  ``sigma``, ``V0``, warning levels, fusion, test data,
and Vajont are deliberately outside this artifact.
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

from warning.bai_perron_initial_slope import (  # noqa: E402
    BIC_FORMULA,
    BaiPerronInitialSlopeResult,
    CONTINUITY_CONSTRAINT,
    FIT_STATUS_FAILED,
    FIRST_SEGMENT_ACCEPTANCE,
    MAX_SEGMENTS,
    MIN_SEGMENT_OBSERVATIONS,
    REGRESSION_PARAMETERS_PER_SEGMENT,
    SEGMENTATION_ALGORITHM,
    SEGMENT_COUNT_SELECTION,
    SELECTION_RULE,
    select_bai_perron_initial_stable_slope,
)
from warning.mvif import (  # noqa: E402
    FIT_STATUS_CANDIDATE as MVIF_FIT_STATUS_CANDIDATE,
    MvifFitResult,
    evaluate_fitted_mvif_trend,
    fit_mvif_trend,
)
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
from warning.protocol import (  # noqa: E402
    DEFAULT_PROTOCOL_PATH,
    load_protocol,
    protocol_content_sha256,
    unresolved_item_ids,
)


SUMMARY_FILENAME = "bai_perron_mvif_initial_slope_candidates.csv"
MANIFEST_FILENAME = "bai_perron_mvif_initial_slope_candidates_manifest.json"
CANDIDATE_STATUS = "draft_candidate_not_formal"


@dataclass(frozen=True)
class BaiPerronInitialSlopeCandidateArtifacts:
    """Paths and station count for one non-formal Bai--Perron run."""

    summary_path: Path
    manifest_path: Path
    n_stations: int


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _strict_precondition_failure(
    *,
    station: str,
    fit_result: MvifFitResult,
    failure_reason: str,
) -> BaiPerronInitialSlopeResult:
    return BaiPerronInitialSlopeResult(
        station=station,
        status=FIT_STATUS_FAILED,
        failure_reason=failure_reason,
        fit_start_date=fit_result.fit_start_date,
        fit_end_date=fit_result.fit_end_date,
        n_fit_rows=fit_result.n_fit_rows,
        min_segment_observations=MIN_SEGMENT_OBSERVATIONS,
        max_segments_considered=min(
            MAX_SEGMENTS,
            fit_result.n_fit_rows // MIN_SEGMENT_OBSERVATIONS,
        ),
    )


def _candidate_records(inputs: _FitSelectionInputs) -> pd.DataFrame:
    records = []
    for boundary in inputs.fit_boundaries.itertuples(index=False):
        station_kinematics = inputs.fit_kinematics.loc[
            inputs.fit_kinematics["station"].eq(boundary.station),
            ["date", "displacement", "displacement_valid"],
        ]
        fit_result = fit_mvif_trend(
            station_kinematics,
            station=boundary.station,
            fit_end_date=boundary.fit_end_date,
        )
        trend_evaluation_failure_reason: str | None = None
        if fit_result.status != MVIF_FIT_STATUS_CANDIDATE:
            selection = _strict_precondition_failure(
                station=boundary.station,
                fit_result=fit_result,
                failure_reason="strict_mvif_fit_failed",
            )
        else:
            try:
                fitted_trend = evaluate_fitted_mvif_trend(
                    fit_result,
                    station_kinematics["date"],
                )
                selection = select_bai_perron_initial_stable_slope(
                    pd.DataFrame(
                        {
                            "date": station_kinematics["date"].to_numpy(),
                            "trend_displacement": fitted_trend,
                        }
                    ),
                    station=boundary.station,
                    fit_end_date=boundary.fit_end_date,
                )
            except (FloatingPointError, ValueError) as exc:
                trend_evaluation_failure_reason = str(exc)
                selection = _strict_precondition_failure(
                    station=boundary.station,
                    fit_result=fit_result,
                    failure_reason="strict_mvif_trend_evaluation_failed",
                )
        record = selection.to_record()
        record.update(
            {
                "mvif_fit_status": fit_result.status,
                "mvif_fit_failure_reason": fit_result.failure_reason,
                "mvif_trend_evaluation_failure_reason": (
                    trend_evaluation_failure_reason
                    if fit_result.status == MVIF_FIT_STATUS_CANDIDATE
                    else None
                ),
                "mvif_fit_objective_cost": fit_result.objective_cost,
                "mvif_fit_tf_elapsed_days": fit_result.tf_elapsed_days,
            }
        )
        records.append(record)
    return pd.DataFrame(records).sort_values("station", kind="stable").reset_index(
        drop=True
    )


def _candidate_method_metadata(protocol: dict) -> dict[str, Any]:
    """Read and validate the source/project-adaptation boundary."""

    candidate = protocol["confirmed"]["v0_framework"][
        "bai_perron_mvif_initial_slope_candidate"
    ]
    if candidate.get("status") != CANDIDATE_STATUS:
        raise ValueError(
            "draft protocol Bai-Perron initial-slope candidate status must be "
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
        "strict_mvif_precondition",
        "failure_policy",
    )
    metadata: dict[str, Any] = {}
    for field in required_fields:
        value = candidate.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                "draft protocol Bai-Perron initial-slope candidate is missing "
                f"source alignment field: {field}"
            )
        metadata[field] = value
    if candidate.get("fit_only") is not True:
        raise ValueError("draft protocol Bai-Perron candidate must remain fit-only")
    if (
        candidate["strict_mvif_precondition"]
        != "strict_accepted_finite_tf_MVIF_fit_required_before_segmentation"
    ):
        raise ValueError("draft protocol Bai-Perron strict MVIF precondition drifted")

    segmentation = candidate.get("segmentation")
    if not isinstance(segmentation, dict):
        raise ValueError("draft protocol Bai-Perron segmentation is missing")
    expected_segmentation = {
        "algorithm": SEGMENTATION_ALGORITHM,
        "input": "strictly_accepted_fitted_MVIF_trend_displacement",
        "minimum_segment_observations": MIN_SEGMENT_OBSERVATIONS,
        "max_segments": MAX_SEGMENTS,
        "segment_count_selection": SEGMENT_COUNT_SELECTION,
        "regression_parameters_per_segment": REGRESSION_PARAMETERS_PER_SEGMENT,
        "bic_formula": BIC_FORMULA,
        "continuity_constraint": CONTINUITY_CONSTRAINT,
        "selection_rule": SELECTION_RULE,
        "first_segment_acceptance": FIRST_SEGMENT_ACCEPTANCE,
    }
    for field, expected in expected_segmentation.items():
        if segmentation.get(field) != expected:
            raise ValueError(
                f"draft protocol Bai-Perron segmentation.{field} does not match "
                "the runtime setting"
            )
    metadata["segmentation"] = segmentation

    statistics = candidate.get("candidate_statistics")
    expected_statistics = {
        "V": "ordinary_least_squares_slope_of_selected_initial_MVIF_trend_segment_mm_per_day",
        "sigma": "not_evaluated_until_a_source_or_mentor_approved_operational_convention_is_frozen",
        "candidate_v0": "not_emitted_until_sigma_convention_and_formal_segment_rule_are_frozen",
    }
    if not isinstance(statistics, dict):
        raise ValueError("draft protocol Bai-Perron candidate_statistics is missing")
    for field, expected in expected_statistics.items():
        if statistics.get(field) != expected:
            raise ValueError(
                f"draft protocol Bai-Perron candidate_statistics.{field} does not "
                "match the runtime boundary"
            )
    metadata["candidate_statistics"] = statistics

    not_evaluated = candidate.get("not_evaluated")
    if not isinstance(not_evaluated, list) or not all(
        isinstance(value, str) and value for value in not_evaluated
    ):
        raise ValueError("draft protocol Bai-Perron not_evaluated must be strings")
    metadata["not_evaluated"] = not_evaluated
    return metadata


def build_fit_bai_perron_initial_slope_candidates(
    *,
    kinematics_path: str | Path,
    predictions_path: str | Path,
) -> pd.DataFrame:
    """Return strict-MVIF-gated, fit-only Bai--Perron candidate records."""

    return _candidate_records(
        _load_fit_selection_inputs(
            kinematics_path=kinematics_path,
            predictions_path=predictions_path,
        )
    )


def write_fit_bai_perron_initial_slope_candidates(
    *,
    kinematics_path: str | Path = DEFAULT_KINEMATICS_PATH,
    predictions_path: str | Path = DEFAULT_PREDICTIONS_PATH,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    protocol_path: str | Path = DEFAULT_PROTOCOL_PATH,
) -> BaiPerronInitialSlopeCandidateArtifacts:
    """Write a non-formal candidate table and its complete provenance manifest."""

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
        "selection_rule": candidate_method["segmentation"]["selection_rule"],
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
        "artifact_kind": "ootang_fit_bai_perron_mvif_initial_slope_candidates",
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
                "strict_mvif_precondition": candidate_method[
                    "strict_mvif_precondition"
                ],
                "selection_rule": candidate_method["segmentation"][
                    "selection_rule"
                ],
                "segmentation": candidate_method["segmentation"],
                "candidate_statistics": candidate_method["candidate_statistics"],
                "failure_policy": candidate_method["failure_policy"],
            },
            "source_boundary": {
                "specified_word_thesis": "uses_MVIF_trend_initial_stable_slope_but_does_not_specify_an_automatic_segment_selection_algorithm",
                "bai_perron": "project_adaptation_for_automatic_fit_only_candidate_selection_not_a_source_prescribed_V0_or_warning_rule",
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
            "test_split_used": False,
        },
        "source_kinematics": {
            "path": str(Path(kinematics_path)),
            "selected_columns": list(_KINEMATICS_COLUMNS),
            "time_filter": "station_date<=station_fit_end_date",
            "temporal_scope": KINEMATICS_TEMPORAL_SCOPE,
            "vajont_used": False,
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
    return BaiPerronInitialSlopeCandidateArtifacts(
        summary_path=summary_path,
        manifest_path=manifest_path,
        n_stations=len(inputs.fit_boundaries),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Write strict-MVIF-gated Bai-Perron initial-slope candidates without "
            "V0 or warnings."
        )
    )
    parser.add_argument("--kinematics", type=Path, default=DEFAULT_KINEMATICS_PATH)
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL_PATH)
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    artifacts = write_fit_bai_perron_initial_slope_candidates(
        kinematics_path=args.kinematics,
        predictions_path=args.predictions,
        output_dir=args.output_dir,
        protocol_path=args.protocol,
    )
    print(
        "wrote strict-MVIF-gated Bai-Perron initial-slope candidates "
        f"({artifacts.n_stations} stations): {artifacts.summary_path}"
    )


if __name__ == "__main__":
    main()


__all__ = [
    "BaiPerronInitialSlopeCandidateArtifacts",
    "CANDIDATE_STATUS",
    "DEFAULT_KINEMATICS_PATH",
    "DEFAULT_OUTPUT_DIR",
    "DEFAULT_PREDICTIONS_PATH",
    "FIT_SPLIT",
    "KINEMATICS_TEMPORAL_SCOPE",
    "MANIFEST_FILENAME",
    "SUMMARY_FILENAME",
    "build_fit_bai_perron_initial_slope_candidates",
    "write_fit_bai_perron_initial_slope_candidates",
]
