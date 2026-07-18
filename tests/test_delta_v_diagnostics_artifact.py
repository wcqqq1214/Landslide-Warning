"""Tests for fit/calibration-only raw ΔV diagnostic artifacts."""

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

from warning.delta_v_diagnostics import (  # noqa: E402
    build_delta_v_diagnostics,
    write_delta_v_diagnostics,
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
                "2020-01-06",
                test_date,
            ],
            "station": ["A"] * 6,
            "split": ["fit", "fit", "fit", "calibration", "calibration", "test"],
        }
    )


def _kinematics_frame(
    post_test_delta_v: float,
    *,
    include_extra_post_test_station: bool = False,
    include_duplicate_post_test_row: bool = False,
) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "date": [
                "2020-01-01",
                "2020-01-02",
                "2020-01-03",
                "2020-01-04",
                "2020-01-05",
                "2020-01-06",
                "2020-01-07",
            ],
            "station": ["A"] * 7,
            "delta_v": [
                float("nan"),
                float("nan"),
                -0.5,
                0.0,
                0.5,
                -0.25,
                post_test_delta_v,
            ],
            "delta_v_status": [
                "warmup",
                "warmup",
                "valid",
                "valid",
                "valid",
                "valid",
                "valid",
            ],
        }
    )
    if include_extra_post_test_station:
        frame = pd.concat(
            [
                frame,
                pd.DataFrame(
                    {
                        "date": ["not-a-date"],
                        "station": ["UNUSED"],
                        "delta_v": [1_000_000.0],
                        "delta_v_status": ["valid"],
                    }
                ),
            ],
            ignore_index=True,
        )
    if include_duplicate_post_test_row:
        frame = pd.concat(
            [
                frame,
                pd.DataFrame(
                    {
                        "date": ["2020-01-07"],
                        "station": ["A"],
                        "delta_v": [1_000_000.0],
                        "delta_v_status": ["valid"],
                    }
                ),
            ],
            ignore_index=True,
        )
    return frame


def _interleaved_prediction_frame() -> pd.DataFrame:
    """Place a test date inside the calibration date envelope on purpose."""

    return pd.DataFrame(
        {
            "date": [
                "2020-01-02",
                "2020-01-03",
                "2020-01-04",
                "2020-01-05",
                "2020-01-06",
                "2020-01-07",
            ],
            "station": ["A"] * 6,
            "split": ["fit", "fit", "fit", "calibration", "test", "calibration"],
        }
    )


def _interleaved_kinematics(test_delta_v: float) -> pd.DataFrame:
    frame = _kinematics_frame(post_test_delta_v=0.75)
    frame.loc[frame["date"].eq("2020-01-06"), "delta_v"] = test_delta_v
    return frame


class DeltaVDiagnosticArtifactTests(unittest.TestCase):
    def test_summary_is_invariant_to_test_and_post_test_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_predictions = root / "first_predictions.csv"
            second_predictions = root / "second_predictions.csv"
            first_kinematics = root / "first_kinematics.csv"
            second_kinematics = root / "second_kinematics.csv"
            _prediction_frame("2020-01-05", "2020-01-07").to_csv(
                first_predictions,
                index=False,
            )
            _prediction_frame("2020-01-05", "").to_csv(second_predictions, index=False)
            _kinematics_frame(100.0).to_csv(first_kinematics, index=False)
            _kinematics_frame(
                -1_000_000.0,
                include_extra_post_test_station=True,
                include_duplicate_post_test_row=True,
            ).to_csv(second_kinematics, index=False)

            first = build_delta_v_diagnostics(
                kinematics_path=first_kinematics,
                predictions_path=first_predictions,
            )
            second = build_delta_v_diagnostics(
                kinematics_path=second_kinematics,
                predictions_path=second_predictions,
            )

        pd.testing.assert_frame_equal(first, second)
        self.assertEqual(set(first["source_split"]), {"fit", "calibration"})
        self.assertEqual(
            first.loc[first["source_split"].eq("fit"), "n_rows"].iloc[0], 4
        )
        self.assertEqual(
            first.loc[first["source_split"].eq("calibration"), "n_valid_delta_v"].iloc[
                0
            ],
            2,
        )
        self.assertNotIn("delta_v_state", first.columns)
        self.assertNotIn("near_zero_tolerance", first.columns)

    def test_written_artifacts_are_invariant_to_test_and_post_test_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            predictions_path = root / "predictions.csv"
            kinematics_path = root / "kinematics.csv"
            output_dir = root / "artifacts"
            _prediction_frame("2020-01-05", "2020-01-07").to_csv(
                predictions_path,
                index=False,
            )
            _kinematics_frame(100.0).to_csv(kinematics_path, index=False)

            first = write_delta_v_diagnostics(
                kinematics_path=kinematics_path,
                predictions_path=predictions_path,
                output_dir=output_dir,
            )
            first_summary = first.summary_path.read_bytes()
            first_manifest = first.manifest_path.read_bytes()

            _prediction_frame("2020-01-05", "").to_csv(predictions_path, index=False)
            _kinematics_frame(
                -1_000_000.0,
                include_extra_post_test_station=True,
            ).to_csv(kinematics_path, index=False)
            second = write_delta_v_diagnostics(
                kinematics_path=kinematics_path,
                predictions_path=predictions_path,
                output_dir=output_dir,
            )

            self.assertEqual(first_summary, second.summary_path.read_bytes())
            self.assertEqual(first_manifest, second.manifest_path.read_bytes())

    def test_interleaved_test_date_does_not_enter_calibration_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            predictions_path = root / "predictions.csv"
            kinematics_path = root / "kinematics.csv"
            output_dir = root / "artifacts"
            _interleaved_prediction_frame().to_csv(predictions_path, index=False)
            _interleaved_kinematics(100.0).to_csv(kinematics_path, index=False)

            first = write_delta_v_diagnostics(
                kinematics_path=kinematics_path,
                predictions_path=predictions_path,
                output_dir=output_dir,
            )
            first_summary = first.summary_path.read_bytes()
            first_manifest = first.manifest_path.read_bytes()

            _interleaved_kinematics(-1_000_000.0).to_csv(
                kinematics_path,
                index=False,
            )
            second = write_delta_v_diagnostics(
                kinematics_path=kinematics_path,
                predictions_path=predictions_path,
                output_dir=output_dir,
            )
            second_summary = second.summary_path.read_bytes()
            second_manifest = second.manifest_path.read_bytes()
            summary = pd.read_csv(second.summary_path)
            manifest = json.loads(second_manifest)

        self.assertEqual(first_summary, second_summary)
        self.assertEqual(first_manifest, second_manifest)
        calibration = summary.loc[summary["source_split"].eq("calibration")]
        self.assertEqual(calibration["n_rows"].iloc[0], 2)
        self.assertEqual(calibration["n_valid_delta_v"].iloc[0], 2)
        self.assertEqual(
            manifest["selection"]["splits"]["calibration"][
                "n_exact_prediction_dates_by_station"
            ],
            {"A": 2},
        )

    def test_missing_calibration_kinematics_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            predictions_path = root / "predictions.csv"
            kinematics_path = root / "kinematics.csv"
            pd.DataFrame(
                {
                    "date": ["2020-01-02", "2020-01-04"] * 2,
                    "station": ["A", "A", "B", "B"],
                    "split": ["fit", "calibration", "fit", "calibration"],
                }
            ).to_csv(predictions_path, index=False)
            pd.DataFrame(
                {
                    "date": ["2020-01-02", "2020-01-04", "2020-01-02"],
                    "station": ["A", "A", "B"],
                    "delta_v": [float("nan"), 0.0, float("nan")],
                    "delta_v_status": ["warmup", "valid", "warmup"],
                }
            ).to_csv(kinematics_path, index=False)

            with self.assertRaisesRegex(
                ValueError,
                r"missing exact calibration prediction dates for station B: 2020-01-04",
            ):
                build_delta_v_diagnostics(
                    kinematics_path=kinematics_path,
                    predictions_path=predictions_path,
                )

    def test_duplicate_calibration_prediction_date_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            predictions_path = root / "predictions.csv"
            kinematics_path = root / "kinematics.csv"
            predictions = pd.concat(
                [
                    _prediction_frame("2020-01-05", "2020-01-07"),
                    pd.DataFrame(
                        {
                            "date": ["2020-01-05"],
                            "station": ["A"],
                            "split": ["calibration"],
                        }
                    ),
                ],
                ignore_index=True,
            )
            predictions.to_csv(predictions_path, index=False)
            _kinematics_frame(100.0).to_csv(kinematics_path, index=False)

            with self.assertRaisesRegex(
                ValueError,
                r"calibration prediction contains duplicate station/date rows",
            ):
                build_delta_v_diagnostics(
                    kinematics_path=kinematics_path,
                    predictions_path=predictions_path,
                )

    def test_writer_emits_draft_diagnostics_without_delta_v_states(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            predictions_path = root / "predictions.csv"
            kinematics_path = root / "kinematics.csv"
            output_dir = root / "artifacts"
            protocol_path = root / "protocol.json"
            _prediction_frame("2020-01-05", "2020-01-07").to_csv(
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

            artifacts = write_delta_v_diagnostics(
                kinematics_path=kinematics_path,
                predictions_path=predictions_path,
                output_dir=output_dir,
                protocol_path=protocol_path,
            )
            summary = pd.read_csv(artifacts.summary_path)
            manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(
            set(summary["diagnostic_status"]),
            {"diagnostic_only_no_tolerance_decision"},
        )
        self.assertNotIn("delta_v_state", summary.columns)
        self.assertNotIn("near_zero_tolerance", summary.columns)
        self.assertFalse(manifest["formal_warning_output"])
        self.assertEqual(set(manifest["selection"]["splits"]), {"fit", "calibration"})
        self.assertIn("fit_prediction_window", manifest["inputs"])
        self.assertIn("calibration_kinematics", manifest["inputs"])
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


if __name__ == "__main__":
    unittest.main()
