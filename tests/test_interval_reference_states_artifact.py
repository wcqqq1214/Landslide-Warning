"""Tests for the source-referenced observed interval-state artifact."""

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

from warning.interval_reference_states import (  # noqa: E402
    STATE_ARTIFACT_STATUS,
    build_reference_interval_states,
    write_reference_interval_states,
)
from warning.interval_state import THESIS_FIGURE_5_1_MAPPING_ID  # noqa: E402
from warning.protocol import load_protocol, protocol_content_sha256  # noqa: E402


def _prediction_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": ["2020-01-01", "2020-01-02", "2020-01-03"],
            "station": ["A", "A", "A"],
            "split": ["fit", "calibration", "test"],
            "actual": [10.0, 10.5, 13.5],
            "p10": [8.71845, 8.71845, 8.71845],
            "p50": [10.0, 10.0, 10.0],
            "p90": [11.28155, 11.28155, 11.28155],
        }
    )


class IntervalReferenceStateArtifactTests(unittest.TestCase):
    def test_build_marks_fit_as_not_applicable_and_maps_issued_predictions(self):
        states = build_reference_interval_states(_prediction_frame())

        self.assertEqual(
            states["interval_status"].tolist(), ["not_applicable", "valid", "valid"]
        )
        self.assertTrue(pd.isna(states.loc[0, "interval_level"]))
        self.assertEqual(states.loc[1, "interval_color"], "blue")
        self.assertEqual(states.loc[2, "interval_color"], "red")
        self.assertEqual(
            states.loc[1, "interval_mapping_basis"],
            THESIS_FIGURE_5_1_MAPPING_ID,
        )
        self.assertEqual(states.loc[0, "interval_reason"], "source_not_applicable")

    def test_writer_emits_nonformal_provenance_for_all_prediction_splits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            predictions_path = root / "predictions.csv"
            output_dir = root / "artifacts"
            _prediction_frame().to_csv(predictions_path, index=False)

            artifacts = write_reference_interval_states(
                predictions_path=predictions_path,
                output_dir=output_dir,
            )

            states = pd.read_csv(artifacts.states_path)
            manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(artifacts.n_rows, 3)
        self.assertEqual(
            artifacts.states_path.name,
            "interval_reference_states.csv",
        )
        self.assertEqual(
            artifacts.manifest_path.name,
            "interval_reference_states_manifest.json",
        )
        self.assertEqual(set(states["artifact_status"]), {STATE_ARTIFACT_STATUS})
        self.assertEqual(set(states["formal_warning_output"]), {False})
        self.assertEqual(
            states["interval_input_status"].tolist(),
            ["not_applicable", "valid", "valid"],
        )
        self.assertEqual(manifest["artifact_status"], STATE_ARTIFACT_STATUS)
        self.assertFalse(manifest["formal_warning_output"])
        self.assertEqual(
            manifest["mapping"]["basis"],
            THESIS_FIGURE_5_1_MAPPING_ID,
        )
        self.assertEqual(
            manifest["selection"]["splits"], ["fit", "calibration", "test"]
        )
        self.assertEqual(
            manifest["mapping"]["input_status_policy"],
            "forecast_predictions_csv_has_no_interval_source_status; "
            "fit_is_derived_not_applicable; calibration_and_test_are_valid_candidates",
        )
        self.assertEqual(
            manifest["protocol"]["content_sha256"],
            protocol_content_sha256(load_protocol()),
        )

    def test_build_rejects_an_unknown_prediction_split(self):
        predictions = _prediction_frame()
        predictions.loc[2, "split"] = "future"

        with self.assertRaisesRegex(ValueError, "split"):
            build_reference_interval_states(predictions)

    def test_build_rejects_duplicate_prediction_natural_keys(self):
        predictions = pd.concat(
            [_prediction_frame(), _prediction_frame().iloc[[1]]],
            ignore_index=True,
        )

        with self.assertRaisesRegex(ValueError, "duplicate"):
            build_reference_interval_states(predictions)

    def test_writer_rejects_duplicate_prediction_natural_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            predictions_path = Path(directory) / "predictions.csv"
            predictions = pd.concat(
                [_prediction_frame(), _prediction_frame().iloc[[1]]],
                ignore_index=True,
            )
            predictions.to_csv(predictions_path, index=False)

            with self.assertRaisesRegex(ValueError, "duplicate"):
                write_reference_interval_states(predictions_path=predictions_path)


if __name__ == "__main__":
    unittest.main()
