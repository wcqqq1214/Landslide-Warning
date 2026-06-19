import sys
import unittest
from pathlib import Path

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "code"))

import ngboost_warn


class NgboostWarnTests(unittest.TestCase):
    def test_outputs_are_grouped_under_ngboost_figures(self):
        expected = ROOT / "figures" / "ngboost"

        self.assertEqual(ngboost_warn.OUT_PNG.parent, expected)
        self.assertEqual(ngboost_warn.OUT_THRESHOLDS_CSV.parent, expected)
        self.assertEqual(ngboost_warn.OUT_METRICS_CSV.parent, expected)
        self.assertEqual(ngboost_warn.OUT_PROBABILITIES_CSV.parent, expected)

    def test_multiclass_metrics_expose_missing_high_level_support(self):
        probability = np.array([
            [0.8, 0.2, 0.0, 0.0],
            [0.1, 0.9, 0.0, 0.0],
        ])

        metrics = ngboost_warn.multiclass_metrics([0, 1], probability)

        self.assertEqual(metrics["accuracy"], 1.0)
        self.assertEqual(metrics["orange_support"], 0)
        self.assertEqual(metrics["red_support"], 0)

    def test_probability_frame_keeps_dates_labels_and_probabilities(self):
        result = ngboost_warn.probability_frame(
            pd.Series(pd.date_range("2020-01-01", periods=2)),
            [0, 1],
            [[0.8, 0.2], [0.1, 0.9]],
            level_names=["green", "yellow"],
        )

        self.assertEqual(
            list(result.columns),
            ["Date", "actual_level", "predicted_level", "prob_green", "prob_yellow"],
        )

    def test_class_count_matches_observed_warning_levels(self):
        self.assertEqual(ngboost_warn.class_count_for_labels([0, 0, 1]), 2)
        self.assertEqual(ngboost_warn.class_count_for_labels([0, 1, 2, 3]), 4)

    def test_attach_dynamic_warning_labels_aligns_dates_and_drops_invalid_window(self):
        raw = pd.DataFrame({
            "Date": pd.date_range("2020-01-01", periods=6),
            "MJ9/mm": [0, 1, 2, 3, 9, 15],
            "MJ1/mm": [0, 1, 2, 3, 24, 55],
        })
        features = pd.DataFrame({
            "Date": pd.date_range("2020-01-02", periods=5),
            "feature": [1, 2, 3, 4, 5],
        })
        thresholds = {
            "MJ9": {"v0_mm_per_month": 5.0},
            "MJ1": {"v0_mm_per_month": 10.0},
        }

        merged, _ = ngboost_warn.attach_dynamic_warning_labels(
            features,
            raw,
            stations={"MJ9": "MJ9/mm", "MJ1": "MJ1/mm"},
            thresholds=thresholds,
            month_window_days=3,
        )

        self.assertEqual(
            merged["Date"].tolist(),
            pd.date_range("2020-01-04", periods=3).tolist(),
        )
        self.assertEqual(merged["warning_level"].tolist(), [0, 1, 2])


if __name__ == "__main__":
    unittest.main()
