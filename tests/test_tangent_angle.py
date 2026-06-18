import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "code"))

import tangent_angle


class TangentAngleRateTests(unittest.TestCase):
    def test_validate_daily_dates_rejects_date_gap(self):
        dates = ["2020-01-01", "2020-01-02", "2020-01-04"]

        with self.assertRaisesRegex(ValueError, "日等间隔"):
            tangent_angle.validate_daily_dates(dates)

    def test_validate_daily_dates_rejects_duplicates_and_non_monotonic_input(self):
        with self.assertRaises(ValueError):
            tangent_angle.validate_daily_dates(
                ["2020-01-01", "2020-01-01", "2020-01-02"]
            )
        with self.assertRaises(ValueError):
            tangent_angle.validate_daily_dates(
                ["2020-01-02", "2020-01-01", "2020-01-03"]
            )

    def test_manual_range_excludes_rate_at_start_date(self):
        dates = pd.date_range("2020-01-01", "2020-01-06")
        displacement = [0, 1, 3, 5, 7, 20]

        result = tangent_angle.estimate_uniform_rate(
            dates,
            displacement,
            manual_range=("2020-01-02", "2020-01-05"),
        )

        self.assertEqual(result["method"], "manual")
        self.assertAlmostEqual(result["v_eq_mm_per_day"], 2.0)
        self.assertEqual(result["start_date"], "2020-01-02")
        self.assertEqual(result["end_date"], "2020-01-05")
        self.assertEqual(result["n_rate_samples"], 3)

    def test_automatic_selection_uses_training_period_only(self):
        dates = pd.date_range("2020-01-01", periods=12)
        first_ten = np.arange(10, dtype=float)
        stable_test = np.concatenate([first_ten, [10.0, 11.0]])
        changed_test = np.concatenate([first_ten, [1000.0, -1000.0]])

        stable_result = tangent_angle.estimate_uniform_rate(
            dates,
            stable_test,
            train_frac=10 / 12,
            window=4,
        )
        changed_result = tangent_angle.estimate_uniform_rate(
            dates,
            changed_test,
            train_frac=10 / 12,
            window=4,
        )

        self.assertEqual(stable_result, changed_result)
        self.assertEqual(stable_result["method"], "automatic_candidate")
        self.assertAlmostEqual(stable_result["v_eq_mm_per_day"], 1.0)

    def test_all_zero_displacement_rejects_nonpositive_rate(self):
        dates = pd.date_range("2020-01-01", periods=8)

        with self.assertRaisesRegex(ValueError, "正的等速阶段速率"):
            tangent_angle.estimate_uniform_rate(
                dates,
                np.zeros(len(dates)),
                train_frac=1.0,
                window=4,
            )

    def test_estimate_uniform_rate_rejects_length_mismatch(self):
        with self.assertRaises(ValueError):
            tangent_angle.estimate_uniform_rate(
                pd.date_range("2020-01-01", periods=3),
                [0.0, 1.0],
            )

    def test_manual_range_requires_ordered_dates_in_input(self):
        dates = pd.date_range("2020-01-01", periods=5)
        displacement = np.arange(5, dtype=float)

        invalid_ranges = [
            ("2019-12-31", "2020-01-03"),
            ("2020-01-02", "2020-01-06"),
            ("2020-01-03", "2020-01-03"),
            ("2020-01-04", "2020-01-03"),
        ]
        for manual_range in invalid_ranges:
            with self.subTest(manual_range=manual_range):
                with self.assertRaises(ValueError):
                    tangent_angle.estimate_uniform_rate(
                        dates,
                        displacement,
                        manual_range=manual_range,
                    )


if __name__ == "__main__":
    unittest.main()
