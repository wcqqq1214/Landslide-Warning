"""Tests for fit-only MVIF finite-``t_f`` diagnostic artifacts."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning.mvif_diagnostics import (  # noqa: E402
    DEFAULT_KINEMATICS_PATH,
    DEFAULT_PREDICTIONS_PATH,
    build_fit_mvif_diagnostics,
    write_fit_mvif_diagnostics,
)
from warning.protocol import load_protocol, protocol_content_sha256  # noqa: E402


def _fit_curve() -> pd.DataFrame:
    elapsed_days = np.arange(0.0, 31.0)
    tf_days = 60.0
    displacement = 20.0 * np.log(
        (tf_days + 0.25 * elapsed_days) / (tf_days - elapsed_days)
    ) + 10.0
    return pd.DataFrame(
        {
            "date": pd.Timestamp("2020-01-01")
            + pd.to_timedelta(elapsed_days, unit="D"),
            "station": "A",
            "displacement": displacement,
            "displacement_valid": True,
        }
    )


def _predictions() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": ["2020-01-30", "2020-01-31", "2020-02-01", "2020-02-02"],
            "station": ["A"] * 4,
            "split": ["fit", "fit", "calibration", "test"],
        }
    )


class MvifDiagnosticArtifactTests(unittest.TestCase):
    def test_default_ootang_data_records_tf_instability_without_v0(self):
        summary = build_fit_mvif_diagnostics(
            kinematics_path=DEFAULT_KINEMATICS_PATH,
            predictions_path=DEFAULT_PREDICTIONS_PATH,
        )

        self.assertEqual(len(summary), 8)
        self.assertEqual(set(summary["fit_status"]), {"failed"})
        self.assertEqual(
            set(summary["failure_reason"]),
            {"tf_multistart_unstable"},
        )
        self.assertTrue(summary[["A", "B", "C", "tf_elapsed_days"]].isna().all().all())
        self.assertNotIn("V0", summary.columns)
        self.assertNotIn("velocity_level", summary.columns)

    def test_candidate_artifact_is_fit_only_and_cannot_supply_v0(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_kinematics = root / "first_kinematics.csv"
            second_kinematics = root / "second_kinematics.csv"
            first_predictions = root / "first_predictions.csv"
            second_predictions = root / "second_predictions.csv"
            output_dir = root / "artifacts"
            fit_curve = _fit_curve()
            post_fit = pd.DataFrame(
                {
                    "date": pd.to_datetime(["2030-01-01", "2030-01-02"]),
                    "station": ["A", "A"],
                    "displacement": [-1_000_000.0, 1_000_000.0],
                    "displacement_valid": [True, True],
                }
            )
            fit_curve.to_csv(first_kinematics, index=False)
            pd.concat([fit_curve, post_fit], ignore_index=True).to_csv(
                second_kinematics,
                index=False,
            )
            _predictions().to_csv(first_predictions, index=False)
            changed_predictions = _predictions()
            changed_predictions.loc[2:, "date"] = ["2040-01-01", "2040-01-02"]
            changed_predictions.to_csv(second_predictions, index=False)

            first = build_fit_mvif_diagnostics(
                kinematics_path=first_kinematics,
                predictions_path=first_predictions,
            )
            second = build_fit_mvif_diagnostics(
                kinematics_path=second_kinematics,
                predictions_path=second_predictions,
            )
            artifacts = write_fit_mvif_diagnostics(
                kinematics_path=first_kinematics,
                predictions_path=first_predictions,
                output_dir=output_dir,
            )
            expected_summary = artifacts.summary_path.read_bytes()
            expected_manifest = artifacts.manifest_path.read_bytes()
            repeated = write_fit_mvif_diagnostics(
                kinematics_path=first_kinematics,
                predictions_path=first_predictions,
                output_dir=output_dir,
            )
            repeated_summary = repeated.summary_path.read_bytes()
            repeated_manifest = repeated.manifest_path.read_bytes()
            summary = pd.read_csv(artifacts.summary_path)
            manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))

        pd.testing.assert_frame_equal(first, second)
        self.assertEqual(expected_summary, repeated_summary)
        self.assertEqual(expected_manifest, repeated_manifest)
        self.assertEqual(first.loc[0, "fit_status"], "candidate_finite_tf")
        self.assertNotIn("V0", first.columns)
        self.assertNotIn("velocity_level", first.columns)
        self.assertNotIn("warning_level", first.columns)
        self.assertEqual(
            set(summary["candidate_method_role"]),
            {"word_formula_numerical_identifiability_diagnostic_not_v0_implementation"},
        )
        self.assertFalse(manifest["formal_warning_output"])
        expected_protocol_sha256 = protocol_content_sha256(load_protocol())
        self.assertEqual(
            set(summary["protocol_content_sha256"]),
            {expected_protocol_sha256},
        )
        self.assertEqual(
            manifest["protocol"]["content_sha256"],
            expected_protocol_sha256,
        )
        self.assertEqual(manifest["selection"]["split"], "fit")
        self.assertEqual(
            manifest["not_evaluated"],
            [
                "automatic_initial_stable_slope_selection",
                "V",
                "sigma",
                "V0",
                "five_level_velocity_mapping",
                "tangent_angle",
                "formal_warning_output",
            ],
        )

    def test_rejects_a_protocol_with_drifted_mvif_numeric_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kinematics_path = root / "kinematics.csv"
            predictions_path = root / "predictions.csv"
            protocol_path = root / "protocol.json"
            _fit_curve().to_csv(kinematics_path, index=False)
            _predictions().to_csv(predictions_path, index=False)
            protocol = load_protocol()
            protocol["confirmed"]["v0_framework"]["mvif_trend_fit_diagnostic"][
                "deterministic_multistart"
            ]["max_function_evaluations_per_start"] = 999
            protocol_path.write_text(
                json.dumps(protocol, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                "max_function_evaluations_per_start does not match",
            ):
                write_fit_mvif_diagnostics(
                    kinematics_path=kinematics_path,
                    predictions_path=predictions_path,
                    output_dir=root / "artifacts",
                    protocol_path=protocol_path,
                )


if __name__ == "__main__":
    unittest.main()
