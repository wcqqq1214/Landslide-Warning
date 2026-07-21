"""Behavioral tests for draft observed interval-state classification."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning.interval_state import (  # noqa: E402
    THESIS_FIGURE_5_1_MAPPING_ID,
    classify_observed_interval_states,
    summarize_calibration_interval_inputs,
)
from warning.levels import WarningLevel  # noqa: E402


class IntervalStateTests(unittest.TestCase):
    def test_direct_thesis_mapping_maps_observed_values_to_the_five_levels(self):
        frame = pd.DataFrame(
            {
                "actual": [10.0, 10.5, 11.5, 12.5, 13.5],
                "p10": [8.71845] * 5,
                "p50": [10.0] * 5,
                "p90": [11.28155] * 5,
            }
        )

        result = classify_observed_interval_states(frame)

        self.assertEqual(result["interval_status"].tolist(), ["valid"] * 5)
        self.assertEqual(
            result["interval_level"].tolist(),
            [
                WarningLevel.GREEN,
                WarningLevel.BLUE,
                WarningLevel.YELLOW,
                WarningLevel.ORANGE,
                WarningLevel.RED,
            ],
        )
        self.assertEqual(
            result["interval_color"].tolist(),
            ["green", "blue", "yellow", "orange", "red"],
        )
        self.assertAlmostEqual(result.loc[0, "interval_mu"], 10.0)
        self.assertAlmostEqual(result.loc[0, "interval_sigma"], 1.0)
        self.assertEqual(
            result["interval_mapping_basis"].tolist(),
            [THESIS_FIGURE_5_1_MAPPING_ID] * 5,
        )

    def test_direct_mapping_keeps_exact_upper_boundaries_in_the_lower_level(self):
        frame = pd.DataFrame(
            {
                "actual": [10.0, 11.0, 12.0, 13.0],
                "p10": [8.71845] * 4,
                "p50": [10.0] * 4,
                "p90": [11.28155] * 4,
            }
        )

        result = classify_observed_interval_states(frame)

        self.assertEqual(
            result["interval_level"].tolist(),
            [
                WarningLevel.GREEN,
                WarningLevel.BLUE,
                WarningLevel.YELLOW,
                WarningLevel.ORANGE,
            ],
        )

    def test_direct_mapping_does_not_require_calibration_gate_and_preserves_invalidity(
        self,
    ):
        frame = pd.DataFrame(
            {
                "actual": [10.0, 10.0],
                "p10": [8.71845, 12.0],
                "p50": [10.0, 10.0],
                "p90": [11.28155, 11.0],
            }
        )

        result = classify_observed_interval_states(frame)

        self.assertEqual(result.loc[0, "interval_status"], "valid")
        self.assertEqual(result.loc[0, "interval_level"], WarningLevel.GREEN)
        self.assertEqual(
            result.loc[0, "interval_reason"],
            "thesis_figure_5_1_observed_green_region",
        )
        self.assertEqual(result.loc[1, "interval_status"], "invalid")
        self.assertTrue(pd.isna(result.loc[1, "interval_level"]))
        self.assertEqual(result.loc[1, "interval_reason"], "quantile_order_invalid")

    def test_input_warmup_is_explicit_and_not_interpreted_as_green(self):
        frame = pd.DataFrame(
            {
                "actual": [10.0],
                "p10": [8.71845],
                "p50": [10.0],
                "p90": [11.28155],
                "source_status": ["warmup"],
            }
        )

        result = classify_observed_interval_states(
            frame,
            input_status_column="source_status",
        )

        self.assertEqual(result.loc[0, "interval_status"], "warmup")
        self.assertTrue(pd.isna(result.loc[0, "interval_level"]))

    def test_duplicate_dataframe_indexes_do_not_change_rowwise_classification(self):
        frame = pd.DataFrame(
            {
                "actual": [10.0, 10.5],
                "p10": [8.71845, 8.71845],
                "p50": [10.0, 10.0],
                "p90": [11.28155, 11.28155],
            },
            index=[7, 7],
        )

        result = classify_observed_interval_states(frame)

        self.assertEqual(result["interval_status"].tolist(), ["valid", "valid"])
        self.assertEqual(
            result["interval_level"].tolist(),
            [WarningLevel.GREEN, WarningLevel.BLUE],
        )

    def test_nonfinite_inputs_are_invalid_without_a_calibration_decision(self):
        frame = pd.DataFrame(
            {
                "actual": [13.5],
                "p10": [8.71845],
                "p50": [10.0],
                "p90": [float("nan")],
            }
        )

        result = classify_observed_interval_states(frame)

        self.assertEqual(result.loc[0, "interval_status"], "invalid")
        self.assertTrue(pd.isna(result.loc[0, "interval_level"]))
        self.assertEqual(result.loc[0, "interval_reason"], "nonfinite_input")

    def test_calibration_diagnostics_only_accept_calibration_rows_and_do_not_pass_gate(
        self,
    ):
        calibration = pd.DataFrame(
            {
                "split": ["calibration", "calibration", "calibration"],
                "station": ["A", "A", "B"],
                "actual": [10.0, 11.0, 10.0],
                "p10": [8.71845, 8.71845, 12.0],
                "p50": [10.0, 10.0, 10.0],
                "p90": [11.28155, 11.28155, 11.0],
            }
        )

        summary = summarize_calibration_interval_inputs(calibration)

        by_station = summary.set_index("station")
        self.assertEqual(by_station.loc["A", "n_rows"], 2)
        self.assertEqual(by_station.loc["A", "n_valid_quantile_rows"], 2)
        self.assertAlmostEqual(by_station.loc["A", "coverage_80"], 1.0)
        self.assertEqual(by_station.loc["B", "n_quantile_order_invalid"], 1)
        self.assertNotIn("gate_status", summary.columns)
        self.assertNotIn("n_abs_z_gt_3", summary.columns)

        test_rows = calibration.copy()
        test_rows.loc[0, "split"] = "test"
        with self.assertRaisesRegex(ValueError, "calibration"):
            summarize_calibration_interval_inputs(test_rows)

        with self.assertRaisesRegex(TypeError, "calibration_label"):
            summarize_calibration_interval_inputs(
                test_rows,
                calibration_label="test",
            )

        missing_split = calibration.copy()
        missing_split.loc[0, "split"] = pd.NA
        with self.assertRaisesRegex(ValueError, "calibration"):
            summarize_calibration_interval_inputs(missing_split)


if __name__ == "__main__":
    unittest.main()
