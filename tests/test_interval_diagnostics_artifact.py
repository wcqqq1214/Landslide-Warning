"""Tests for calibration-only interval diagnostic artifacts."""

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

from warning.interval_diagnostics import (  # noqa: E402
    build_calibration_diagnostics,
    write_calibration_diagnostics,
)
from warning.protocol import load_protocol, protocol_content_sha256  # noqa: E402


def _prediction_frame(test_actual: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "split": ["fit", "calibration", "calibration", "test", "test"],
            "station": ["A", "A", "B", "A", "B"],
            "actual": [1.0, 10.0, 10.0, test_actual, -test_actual],
            "p10": [0.0, 8.71845, 8.71845, -1_000_000.0, -1_000_000.0],
            "p50": [1.0, 10.0, 10.0, 0.0, 0.0],
            "p90": [2.0, 11.28155, 11.28155, 1_000_000.0, 1_000_000.0],
        }
    )


class IntervalDiagnosticArtifactTests(unittest.TestCase):
    def test_calibration_summary_is_invariant_to_test_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_path = root / "first.csv"
            second_path = root / "second.csv"
            _prediction_frame(1.0).to_csv(first_path, index=False)
            _prediction_frame(1_000_000_000.0).to_csv(second_path, index=False)

            first = build_calibration_diagnostics(first_path)
            second = build_calibration_diagnostics(second_path)

        pd.testing.assert_frame_equal(first, second)
        self.assertEqual(set(first["station"]), {"A", "B"})
        self.assertEqual(first["n_rows"].sum(), 2)
        self.assertNotIn("interval_level", first.columns)
        self.assertNotIn("interval_color", first.columns)
        self.assertNotIn("interval_gate_status", first.columns)

    def test_written_artifacts_are_invariant_to_test_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            predictions_path = root / "predictions.csv"
            out_dir = root / "artifacts"

            _prediction_frame(1.0).to_csv(predictions_path, index=False)
            first = write_calibration_diagnostics(
                predictions_path=predictions_path,
                output_dir=out_dir,
            )
            first_summary = first.summary_path.read_bytes()
            first_manifest = first.manifest_path.read_bytes()

            _prediction_frame(1_000_000_000.0).to_csv(predictions_path, index=False)
            second = write_calibration_diagnostics(
                predictions_path=predictions_path,
                output_dir=out_dir,
            )

            self.assertEqual(first_summary, second.summary_path.read_bytes())
            self.assertEqual(first_manifest, second.manifest_path.read_bytes())

    def test_writer_emits_a_draft_diagnostic_without_a_gate_decision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            predictions_path = root / "predictions.csv"
            out_dir = root / "artifacts"
            protocol_path = root / "protocol.json"
            _prediction_frame(1.0).to_csv(predictions_path, index=False)
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

            artifacts = write_calibration_diagnostics(
                predictions_path=predictions_path,
                output_dir=out_dir,
                protocol_path=protocol_path,
            )

            summary = pd.read_csv(artifacts.summary_path)
            manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(
            artifacts.summary_path.name, "interval_calibration_diagnostics.csv"
        )
        self.assertEqual(
            artifacts.manifest_path.name,
            "interval_calibration_diagnostics_manifest.json",
        )
        self.assertEqual(set(summary["source_split"]), {"calibration"})
        self.assertIn("calibration_input_sha256", summary.columns)
        self.assertEqual(
            set(summary["diagnostic_status"]),
            {"diagnostic_only_no_gate_decision"},
        )
        self.assertNotIn("interval_level", summary.columns)
        self.assertNotIn("interval_color", summary.columns)
        self.assertNotIn("interval_gate_status", summary.columns)
        self.assertFalse(manifest["formal_warning_output"])
        self.assertEqual(manifest["selection"]["split"], "calibration")
        self.assertEqual(manifest["selection"]["n_rows"], 2)
        self.assertEqual(
            manifest["calibration_input"]["sha256"],
            summary["calibration_input_sha256"].iloc[0],
        )
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
        self.assertNotIn("gate_status", manifest)


if __name__ == "__main__":
    unittest.main()
