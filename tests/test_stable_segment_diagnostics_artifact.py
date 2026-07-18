"""Tests for fit-only automatic stable-segment candidate artifacts."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning.stable_segment_diagnostics import (  # noqa: E402
    DEFAULT_KINEMATICS_PATH,
    DEFAULT_PREDICTIONS_PATH,
    build_fit_stable_segment_candidates,
    write_fit_stable_segment_candidates,
)
from warning.protocol import load_protocol, protocol_content_sha256  # noqa: E402


def _prediction_frame(calibration_date: str, test_date: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [
                "2020-01-02",
                "2020-01-03",
                "2020-01-04",
                calibration_date,
                test_date,
            ],
            "station": ["A"] * 5,
            "split": ["fit", "fit", "fit", "calibration", "test"],
        }
    )


def _kinematics_frame(
    post_fit_velocity: float,
    *,
    include_extra_post_fit_station: bool = False,
) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "date": [
                "2020-01-01",
                "2020-01-02",
                "2020-01-03",
                "2020-01-04",
                "2020-01-05",
            ],
            "station": ["A"] * 5,
            "velocity": [float("nan"), 1.0, 1.0, 10.0, post_fit_velocity],
            "velocity_status": ["warmup", "valid", "valid", "valid", "valid"],
        }
    )
    if include_extra_post_fit_station:
        frame = pd.concat(
            [
                frame,
                pd.DataFrame(
                    {
                        "date": ["2030-01-01"],
                        "station": ["UNUSED"],
                        "velocity": [1_000_000.0],
                        "velocity_status": ["valid"],
                    }
                ),
            ],
            ignore_index=True,
        )
    return frame


class StableSegmentDiagnosticArtifactTests(unittest.TestCase):
    def test_default_ootang_artifacts_are_byte_reproducible(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "artifacts"
            first = write_fit_stable_segment_candidates(
                kinematics_path=DEFAULT_KINEMATICS_PATH,
                predictions_path=DEFAULT_PREDICTIONS_PATH,
                output_dir=output_dir,
            )
            expected_summary = first.summary_path.read_bytes()
            expected_manifest = first.manifest_path.read_bytes()

            for _ in range(10):
                repeated = write_fit_stable_segment_candidates(
                    kinematics_path=DEFAULT_KINEMATICS_PATH,
                    predictions_path=DEFAULT_PREDICTIONS_PATH,
                    output_dir=output_dir,
                )
                self.assertEqual(expected_summary, repeated.summary_path.read_bytes())
                self.assertEqual(expected_manifest, repeated.manifest_path.read_bytes())

    def test_candidate_summary_is_invariant_to_post_fit_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_predictions = root / "first_predictions.csv"
            second_predictions = root / "second_predictions.csv"
            first_kinematics = root / "first_kinematics.csv"
            second_kinematics = root / "second_kinematics.csv"
            _prediction_frame("2020-01-05", "2020-01-06").to_csv(
                first_predictions,
                index=False,
            )
            _prediction_frame("not-a-date", "").to_csv(
                second_predictions,
                index=False,
            )
            _kinematics_frame(100.0).to_csv(first_kinematics, index=False)
            _kinematics_frame(
                -1_000_000.0,
                include_extra_post_fit_station=True,
            ).to_csv(second_kinematics, index=False)

            first = build_fit_stable_segment_candidates(
                kinematics_path=first_kinematics,
                predictions_path=first_predictions,
            )
            second = build_fit_stable_segment_candidates(
                kinematics_path=second_kinematics,
                predictions_path=second_predictions,
            )

        pd.testing.assert_frame_equal(first, second)
        self.assertEqual(first.loc[0, "station"], "A")
        self.assertEqual(first.loc[0, "selection_status"], "selected")
        self.assertAlmostEqual(first.loc[0, "first_valid_velocity"], 1.0)
        self.assertAlmostEqual(first.loc[0, "V0"], 1.5)
        self.assertNotIn("velocity_level", first.columns)
        self.assertNotIn("warning_level", first.columns)

    def test_written_artifacts_are_invariant_to_post_fit_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            predictions_path = root / "predictions.csv"
            kinematics_path = root / "kinematics.csv"
            out_dir = root / "artifacts"
            _prediction_frame("2020-01-05", "2020-01-06").to_csv(
                predictions_path,
                index=False,
            )
            _kinematics_frame(100.0).to_csv(kinematics_path, index=False)

            first = write_fit_stable_segment_candidates(
                kinematics_path=kinematics_path,
                predictions_path=predictions_path,
                output_dir=out_dir,
            )
            first_summary = first.summary_path.read_bytes()
            first_manifest = first.manifest_path.read_bytes()

            _prediction_frame("not-a-date", "").to_csv(
                predictions_path,
                index=False,
            )
            _kinematics_frame(
                -1_000_000.0,
                include_extra_post_fit_station=True,
            ).to_csv(kinematics_path, index=False)
            second = write_fit_stable_segment_candidates(
                kinematics_path=kinematics_path,
                predictions_path=predictions_path,
                output_dir=out_dir,
            )

            self.assertEqual(first_summary, second.summary_path.read_bytes())
            self.assertEqual(first_manifest, second.manifest_path.read_bytes())

    def test_writer_emits_draft_candidates_without_velocity_levels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            predictions_path = root / "predictions.csv"
            kinematics_path = root / "kinematics.csv"
            out_dir = root / "artifacts"
            protocol_path = root / "protocol.json"
            _prediction_frame("2020-01-05", "2020-01-06").to_csv(
                predictions_path,
                index=False,
            )
            _kinematics_frame(100.0).to_csv(kinematics_path, index=False)
            protocol = load_protocol()
            protocol["unresolved_items"].append(
                {
                    "id": "test_only_protocol_gate",
                    "reason": "exercise custom protocol provenance",
                    "required_before_formal_run": True,
                }
            )
            protocol_path.write_text(
                json.dumps(protocol, ensure_ascii=False),
                encoding="utf-8",
            )

            artifacts = write_fit_stable_segment_candidates(
                kinematics_path=kinematics_path,
                predictions_path=predictions_path,
                output_dir=out_dir,
                protocol_path=protocol_path,
            )
            summary = pd.read_csv(artifacts.summary_path)
            manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(
            set(summary["candidate_status"]), {"draft_candidate_not_formal"}
        )
        self.assertEqual(set(summary["source_split"]), {"fit"})
        self.assertEqual(
            set(summary["kinematics_temporal_scope"]),
            {"all_station_history_through_fit_cutoff"},
        )
        self.assertNotIn("velocity_level", summary.columns)
        self.assertNotIn("warning_level", summary.columns)
        self.assertFalse(manifest["formal_warning_output"])
        self.assertEqual(manifest["selection"]["split"], "fit")
        self.assertEqual(manifest["selection"]["n_stations"], 1)
        self.assertIn("fit_kinematics_input", manifest)
        self.assertIn("fit_prediction_input", manifest)
        expected_protocol_sha256 = protocol_content_sha256(protocol)
        self.assertEqual(
            set(summary["protocol_content_sha256"]),
            {expected_protocol_sha256},
        )
        self.assertEqual(
            manifest["protocol"]["content_sha256"],
            expected_protocol_sha256,
        )
        self.assertNotIn("sha256", manifest["source_predictions"])
        self.assertNotIn("sha256", manifest["source_kinematics"])
        self.assertEqual(
            manifest["source_kinematics"]["temporal_scope"],
            "all_station_history_through_fit_cutoff",
        )


if __name__ == "__main__":
    unittest.main()
