import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "code"))

import sensitivity_analysis  # noqa: E402
from warning_thresholds import build_warning_frame  # noqa: E402


class SensitivityAnalysisTests(unittest.TestCase):
    def setUp(self):
        dates = pd.date_range("2020-01-01", periods=90)
        base = np.arange(len(dates), dtype=float)
        acceleration = np.where(np.arange(len(dates)) >= 65, 0.3, 0.0)
        cumulative_acceleration = np.cumsum(acceleration)
        self.raw = pd.DataFrame({
            "Date": dates,
            "MJ9/mm": base + cumulative_acceleration,
            "MJ1/mm": 1.2 * base + cumulative_acceleration,
            "MJ3/mm": 0.8 * base + cumulative_acceleration,
        })
        self.stations = {
            "MJ9": "MJ9/mm",
            "MJ1": "MJ1/mm",
            "MJ3": "MJ3/mm",
        }

    def test_compare_sequences_uses_only_common_valid_days(self):
        result = sensitivity_analysis.compare_level_sequences(
            [-1, 0, 1, 2],
            [0, 0, 2, 2],
        )

        self.assertEqual(result["common_valid_days"], 3)
        self.assertEqual(result["changed_days_vs_default"], 1)
        self.assertAlmostEqual(result["agreement_rate_vs_default"], 2 / 3)

    def test_v0_analysis_reports_every_prespecified_combination(self):
        summary, parameters = sensitivity_analysis.analyze_v0_sensitivity(
            self.raw,
            stations=self.stations,
            month_windows=(5, 10),
            accel_percentiles=(0.8, 0.9),
            default_config=(10, 0.9),
        )

        self.assertEqual(len(summary), 4)
        self.assertEqual(len(parameters), 12)
        self.assertEqual(int(summary["is_default"].sum()), 1)
        self.assertTrue(summary["agreement_rate_vs_default"].between(0, 1).all())

    def test_tangent_analysis_separates_parameter_and_fusion_outputs(self):
        v0_frame, _ = build_warning_frame(
            self.raw,
            self.stations,
            month_window_days=10,
            accel_percentile=0.9,
        )
        summary, parameters = sensitivity_analysis.analyze_tangent_sensitivity(
            self.raw,
            v0_frame,
            stations=self.stations,
            key_stations=("MJ9", "MJ1", "MJ3"),
            candidate_windows=(5, 10),
            smooth_windows=(1, 3),
            persistence_rules=((3, 2),),
            default_config=(10, 3, 3, 2),
        )

        self.assertEqual(len(summary), 4)
        self.assertEqual(len(parameters), 6)
        self.assertEqual(int(summary["is_default"].sum()), 1)
        self.assertIn("agreement_rate_vs_default", summary)
        self.assertIn("agreement_rate_vs_candidate_default", summary)
        self.assertIn("warning_events", summary)
        self.assertTrue(
            summary["agreement_rate_vs_candidate_default"].between(0, 1).all()
        )
        self.assertTrue((summary["upgraded_days"] >= 0).all())

    def test_outputs_are_grouped_under_sensitivity_figures(self):
        outputs = [
            sensitivity_analysis.OUT_V0_SUMMARY,
            sensitivity_analysis.OUT_V0_PARAMETERS,
            sensitivity_analysis.OUT_TANGENT_SUMMARY,
            sensitivity_analysis.OUT_TANGENT_PARAMETERS,
        ]

        self.assertTrue(
            all(path.parent == ROOT / "figures" / "sensitivity" for path in outputs)
        )


if __name__ == "__main__":
    unittest.main()
