"""Contract tests for draft velocity/V0 and tangent-angle diagnostics."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
import sys
import tempfile
import unittest

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning.velocity_tangent_diagnostics import (  # noqa: E402
    build_velocity_tangent_diagnostics,
    write_velocity_tangent_diagnostics,
)
from warning.protocol import load_protocol, protocol_content_sha256  # noqa: E402


_PREDICTION_COLUMNS = ("date", "station", "split")
_KINEMATICS_COLUMNS = ("date", "station", "velocity", "velocity_status")


def _canonical_csv_sha256(
    frame: pd.DataFrame,
    *,
    columns: tuple[str, ...],
    sort_columns: tuple[str, ...],
) -> str:
    """Encode the upstream stable-candidate artifact contract independently."""

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


def _write_candidate_frame(
    *,
    path: Path,
    predictions_path: Path,
    kinematics_path: Path,
    v0: float,
    overrides: dict[str, object] | None = None,
) -> None:
    """Write one candidate row with the upstream fit-only provenance fields."""

    predictions = pd.read_csv(predictions_path, usecols=list(_PREDICTION_COLUMNS))
    predictions["date"] = pd.to_datetime(predictions["date"])
    predictions["station"] = predictions["station"].astype("string").str.strip()
    fit_predictions = predictions.loc[predictions["split"].eq("fit")].copy()
    station = str(fit_predictions["station"].iloc[0])
    fit_end_date = fit_predictions.loc[
        fit_predictions["station"].eq(station), "date"
    ].max()
    kinematics = pd.read_csv(kinematics_path, usecols=list(_KINEMATICS_COLUMNS))
    kinematics["date"] = pd.to_datetime(kinematics["date"])
    kinematics["station"] = kinematics["station"].astype("string").str.strip()
    kinematics["velocity"] = pd.to_numeric(kinematics["velocity"], errors="coerce")
    kinematics["velocity_status"] = kinematics["velocity_status"].astype("string")
    fit_kinematics = kinematics.loc[
        kinematics["station"].eq(station)
        & kinematics["date"].le(fit_end_date)
    ].copy()
    row: dict[str, object] = {
        "station": station,
        "candidate_status": "draft_candidate_not_formal",
        "source_split": "fit",
        "kinematics_temporal_scope": "all_station_history_through_fit_cutoff",
        "fit_prediction_input_sha256": _canonical_csv_sha256(
            fit_predictions,
            columns=_PREDICTION_COLUMNS,
            sort_columns=("station", "date"),
        ),
        "fit_kinematics_input_sha256": _canonical_csv_sha256(
            fit_kinematics,
            columns=_KINEMATICS_COLUMNS,
            sort_columns=("station", "date"),
        ),
        "selection_status": "selected",
        "failure_reason": None,
        "fit_end_date": fit_end_date.strftime("%Y-%m-%d"),
        "V0": v0,
        "protocol_content_sha256": protocol_content_sha256(load_protocol()),
    }
    if overrides:
        row.update(overrides)
    pd.DataFrame([row]).to_csv(path, index=False)


class VelocityTangentDiagnosticContractTests(unittest.TestCase):
    def test_builds_fit_and_calibration_evidence_without_levels_or_tolerances(self):
        """Only selected non-test velocities and a candidate V0 enter the audit."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            predictions_path = root / "predictions.csv"
            kinematics_path = root / "kinematics.csv"
            candidates_path = root / "candidates.csv"
            pd.DataFrame(
                {
                    "date": [
                        "2020-01-02",
                        "2020-01-03",
                        "2020-01-04",
                        "2020-01-05",
                    ],
                    "station": ["A"] * 4,
                    "split": ["fit", "fit", "calibration", "test"],
                }
            ).to_csv(predictions_path, index=False)
            pd.DataFrame(
                {
                    "date": [
                        "2020-01-01",
                        "2020-01-02",
                        "2020-01-03",
                        "2020-01-04",
                        "2020-01-05",
                    ],
                    "station": ["A"] * 5,
                    "velocity": [float("nan"), 1.0, 2.0, 4.0, 1_000_000.0],
                    "velocity_status": ["warmup", "valid", "valid", "valid", "valid"],
                }
            ).to_csv(kinematics_path, index=False)
            _write_candidate_frame(
                path=candidates_path,
                predictions_path=predictions_path,
                kinematics_path=kinematics_path,
                v0=2.0,
            )

            summary = build_velocity_tangent_diagnostics(
                kinematics_path=kinematics_path,
                predictions_path=predictions_path,
                stable_segment_candidates_path=candidates_path,
            )

        self.assertEqual(list(summary["source_split"]), ["fit", "calibration"])
        fit = summary.loc[summary["source_split"].eq("fit")].iloc[0]
        calibration = summary.loc[summary["source_split"].eq("calibration")].iloc[0]
        self.assertEqual(fit["n_rows"], 3)
        self.assertEqual(fit["n_valid_velocity"], 2)
        self.assertAlmostEqual(fit["velocity_over_v0_median"], 0.75)
        self.assertAlmostEqual(
            calibration["velocity_over_v0_median"],
            2.0,
        )
        self.assertAlmostEqual(
            calibration["tangent_angle_median_degree"],
            63.43494882292201,
        )
        self.assertNotIn("velocity_level", summary.columns)
        self.assertNotIn("tangent_angle_level", summary.columns)
        self.assertNotIn("v0_blue_tolerance", summary.columns)
        self.assertNotIn("tangent_blue_tolerance", summary.columns)

    def test_written_artifacts_ignore_test_rows_and_remain_nonformal(self):
        """A test-period change must not alter calibration evidence or provenance."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            predictions_path = root / "predictions.csv"
            kinematics_path = root / "kinematics.csv"
            candidates_path = root / "candidates.csv"
            output_dir = root / "artifacts"
            pd.DataFrame(
                {
                    "date": [
                        "2020-01-02",
                        "2020-01-03",
                        "2020-01-04",
                        "2020-01-05",
                    ],
                    "station": ["A"] * 4,
                    "split": ["fit", "fit", "calibration", "test"],
                }
            ).to_csv(predictions_path, index=False)
            kinematics = pd.DataFrame(
                {
                    "date": [
                        "2020-01-01",
                        "2020-01-02",
                        "2020-01-03",
                        "2020-01-04",
                        "2020-01-05",
                    ],
                    "station": ["A"] * 5,
                    "velocity": [float("nan"), 1.0, 2.0, 4.0, 5.0],
                    "velocity_status": ["warmup", "valid", "valid", "valid", "valid"],
                }
            )
            kinematics.to_csv(kinematics_path, index=False)
            _write_candidate_frame(
                path=candidates_path,
                predictions_path=predictions_path,
                kinematics_path=kinematics_path,
                v0=2.0,
            )

            first = write_velocity_tangent_diagnostics(
                kinematics_path=kinematics_path,
                predictions_path=predictions_path,
                stable_segment_candidates_path=candidates_path,
                output_dir=output_dir,
            )
            first_summary = first.summary_path.read_bytes()
            first_manifest = first.manifest_path.read_bytes()

            kinematics.loc[kinematics["date"].eq("2020-01-05"), "velocity"] = (
                1_000_000.0
            )
            kinematics.to_csv(kinematics_path, index=False)
            second = write_velocity_tangent_diagnostics(
                kinematics_path=kinematics_path,
                predictions_path=predictions_path,
                stable_segment_candidates_path=candidates_path,
                output_dir=output_dir,
            )
            summary = pd.read_csv(second.summary_path)
            second_summary = second.summary_path.read_bytes()
            second_manifest = second.manifest_path.read_bytes()
            manifest = second_manifest.decode("utf-8")

        self.assertEqual(first_summary, second_summary)
        self.assertEqual(first_manifest, second_manifest)
        self.assertEqual(set(summary["diagnostic_status"]), {"diagnostic_only_no_tolerance_decision"})
        self.assertNotIn("velocity_level", summary.columns)
        self.assertNotIn("tangent_angle_level", summary.columns)
        self.assertIn('"formal_warning_output": false', manifest)
        self.assertIn('"stable_segment_candidates"', manifest)

    def test_writer_rejects_stale_stable_segment_protocol_provenance(self):
        """A V0 candidate from another protocol must not silently enter the audit."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            predictions_path = root / "predictions.csv"
            kinematics_path = root / "kinematics.csv"
            candidates_path = root / "candidates.csv"
            pd.DataFrame(
                {
                    "date": ["2020-01-02", "2020-01-03"],
                    "station": ["A", "A"],
                    "split": ["fit", "calibration"],
                }
            ).to_csv(predictions_path, index=False)
            pd.DataFrame(
                {
                    "date": ["2020-01-01", "2020-01-02", "2020-01-03"],
                    "station": ["A", "A", "A"],
                    "velocity": [float("nan"), 1.0, 2.0],
                    "velocity_status": ["warmup", "valid", "valid"],
                }
            ).to_csv(kinematics_path, index=False)
            _write_candidate_frame(
                path=candidates_path,
                predictions_path=predictions_path,
                kinematics_path=kinematics_path,
                v0=1.0,
                overrides={"protocol_content_sha256": "0" * 64},
            )

            with self.assertRaisesRegex(
                ValueError,
                "stable-segment candidate protocol fingerprint does not match",
            ):
                write_velocity_tangent_diagnostics(
                    kinematics_path=kinematics_path,
                    predictions_path=predictions_path,
                    stable_segment_candidates_path=candidates_path,
                    output_dir=root / "artifacts",
                )

    def test_builder_rejects_candidate_with_mismatched_fit_input_provenance(self):
        """The public builder must reject a candidate from another fit slice."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            predictions_path = root / "predictions.csv"
            kinematics_path = root / "kinematics.csv"
            candidates_path = root / "candidates.csv"
            pd.DataFrame(
                {
                    "date": ["2020-01-02", "2020-01-03", "2020-01-04"],
                    "station": ["A", "A", "A"],
                    "split": ["fit", "fit", "calibration"],
                }
            ).to_csv(predictions_path, index=False)
            pd.DataFrame(
                {
                    "date": ["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-04"],
                    "station": ["A", "A", "A", "A"],
                    "velocity": [float("nan"), 1.0, 2.0, 4.0],
                    "velocity_status": ["warmup", "valid", "valid", "valid"],
                }
            ).to_csv(kinematics_path, index=False)
            _write_candidate_frame(
                path=candidates_path,
                predictions_path=predictions_path,
                kinematics_path=kinematics_path,
                v0=2.0,
                overrides={"fit_prediction_input_sha256": "stale-fit-input"},
            )

            with self.assertRaisesRegex(
                ValueError,
                "stable-segment candidate fit input provenance does not match",
            ):
                build_velocity_tangent_diagnostics(
                    kinematics_path=kinematics_path,
                    predictions_path=predictions_path,
                    stable_segment_candidates_path=candidates_path,
                )

    def test_builder_rejects_non_draft_candidate_status(self):
        """A non-draft V0 must not be relabelled as a draft dependency."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            predictions_path = root / "predictions.csv"
            kinematics_path = root / "kinematics.csv"
            candidates_path = root / "candidates.csv"
            pd.DataFrame(
                {
                    "date": ["2020-01-02", "2020-01-03"],
                    "station": ["A", "A"],
                    "split": ["fit", "calibration"],
                }
            ).to_csv(predictions_path, index=False)
            pd.DataFrame(
                {
                    "date": ["2020-01-01", "2020-01-02", "2020-01-03"],
                    "station": ["A", "A", "A"],
                    "velocity": [float("nan"), 1.0, 2.0],
                    "velocity_status": ["warmup", "valid", "valid"],
                }
            ).to_csv(kinematics_path, index=False)
            _write_candidate_frame(
                path=candidates_path,
                predictions_path=predictions_path,
                kinematics_path=kinematics_path,
                v0=1.0,
                overrides={"candidate_status": "formal_candidate"},
            )

            with self.assertRaisesRegex(
                ValueError,
                "stable-segment candidate status must be draft_candidate_not_formal",
            ):
                build_velocity_tangent_diagnostics(
                    kinematics_path=kinematics_path,
                    predictions_path=predictions_path,
                    stable_segment_candidates_path=candidates_path,
                )

    def test_builder_rejects_blank_candidate_provenance_field(self):
        """Missing provenance must never be treated as a matching candidate."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            predictions_path = root / "predictions.csv"
            kinematics_path = root / "kinematics.csv"
            candidates_path = root / "candidates.csv"
            pd.DataFrame(
                {
                    "date": ["2020-01-02", "2020-01-03"],
                    "station": ["A", "A"],
                    "split": ["fit", "calibration"],
                }
            ).to_csv(predictions_path, index=False)
            pd.DataFrame(
                {
                    "date": ["2020-01-01", "2020-01-02", "2020-01-03"],
                    "station": ["A", "A", "A"],
                    "velocity": [float("nan"), 1.0, 2.0],
                    "velocity_status": ["warmup", "valid", "valid"],
                }
            ).to_csv(kinematics_path, index=False)
            _write_candidate_frame(
                path=candidates_path,
                predictions_path=predictions_path,
                kinematics_path=kinematics_path,
                v0=1.0,
                overrides={"candidate_status": None},
            )

            with self.assertRaisesRegex(
                ValueError,
                "missing required provenance field: candidate_status",
            ):
                build_velocity_tangent_diagnostics(
                    kinematics_path=kinematics_path,
                    predictions_path=predictions_path,
                    stable_segment_candidates_path=candidates_path,
                )

    def test_builder_rejects_candidate_from_another_protocol(self):
        """No public summary path may bypass the candidate protocol check."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            predictions_path = root / "predictions.csv"
            kinematics_path = root / "kinematics.csv"
            candidates_path = root / "candidates.csv"
            pd.DataFrame(
                {
                    "date": ["2020-01-02", "2020-01-03"],
                    "station": ["A", "A"],
                    "split": ["fit", "calibration"],
                }
            ).to_csv(predictions_path, index=False)
            pd.DataFrame(
                {
                    "date": ["2020-01-01", "2020-01-02", "2020-01-03"],
                    "station": ["A", "A", "A"],
                    "velocity": [float("nan"), 1.0, 2.0],
                    "velocity_status": ["warmup", "valid", "valid"],
                }
            ).to_csv(kinematics_path, index=False)
            _write_candidate_frame(
                path=candidates_path,
                predictions_path=predictions_path,
                kinematics_path=kinematics_path,
                v0=1.0,
                overrides={"protocol_content_sha256": "0" * 64},
            )

            with self.assertRaisesRegex(
                ValueError,
                "stable-segment candidate protocol fingerprint does not match",
            ):
                build_velocity_tangent_diagnostics(
                    kinematics_path=kinematics_path,
                    predictions_path=predictions_path,
                    stable_segment_candidates_path=candidates_path,
                )


if __name__ == "__main__":
    unittest.main()
