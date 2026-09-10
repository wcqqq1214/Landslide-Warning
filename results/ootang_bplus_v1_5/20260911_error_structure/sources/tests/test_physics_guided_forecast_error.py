import sys
from pathlib import Path
import unittest
import tempfile

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from physics_guided_forecast_error.core import (
    choose_teacher,
    correlation,
    date_at,
    describe_error,
    driver_features,
    inventory,
)
from physics_guided_forecast_error.artifacts import load_observations


class ForecastErrorTests(unittest.TestCase):
    def test_constant_error_is_descriptive_offset(self):
        y = np.zeros((240, 4))
        rows = describe_error(y + 7, y, 60)
        for row in rows:
            self.assertEqual(row["prediction_rmse_mm"], 7)
            self.assertEqual(row["oracle_constant_removed_rmse_mm"], 0)
            self.assertEqual(row["increment30_rmse_mm"], 0)
            self.assertEqual(row["growth_error_mm_per_day"], 0)
        self.assertIsNone(
            describe_error(y, y, 60)[0]["oracle_linear_explained_error_fraction"]
        )

    def test_linear_error_and_real_endpoint_denominators(self):
        y = np.zeros((240, 4))
        mu = y + (7 + 0.2 * np.arange(240))[:, None]
        row = describe_error(mu, y, 60)[0]
        self.assertAlmostEqual(row["increment30_rmse_mm"], 6)
        self.assertAlmostEqual(row["growth_error_mm_per_day"], 0.2)
        self.assertAlmostEqual(row["oracle_linear_slope_mm_per_day"], 0.2)
        self.assertLess(row["oracle_linear_removed_rmse_mm"], 1e-12)
        for start in (1, 61, 121):
            self.assertAlmostEqual(
                row[f"growth_error_days_{start}_{start + 59}_mm_per_day"], 0.2
            )

    def test_increment_includes_training_boundary_and_peak_ties(self):
        y, mu = np.zeros((240, 4)), np.zeros((240, 4))
        y[110:] = 5
        mu[120:] = 5
        row = describe_error(mu, y, 60)[0]
        self.assertEqual(row["observed_peak30_date"], date_at(110))
        self.assertEqual(row["predicted_peak30_date"], date_at(120))
        self.assertEqual(row["peak30_date_difference_days"], 10)
        mu[:] = 0
        mu[59] = 4
        mu[60:] = 1
        row = describe_error(mu, y * 0, 60)[0]
        self.assertAlmostEqual(row["growth_error_days_1_60_mm_per_day"], -3 / 60)
        self.assertGreater(row["increment30_rmse_mm"], 0)

    def test_correlation_and_causal_driver_windows(self):
        a = np.arange(70, dtype=float)
        self.assertAlmostEqual(correlation(a, -2 * a), -1)
        self.assertIsNone(correlation(a, a * 0))
        forcing = np.column_stack((np.ones(70), 150 + a / 10))
        f = driver_features(forcing)
        self.assertEqual(f["rain_sum30_mm"][29], 30)
        self.assertEqual(f["rain_sum30_mm"][30], 30)
        self.assertAlmostEqual(f["rwl_change30_m"][30], 3)
        changed = forcing.copy()
        changed[45:] += 10000
        for key, old in f.items():
            np.testing.assert_array_equal(old[:45], driver_features(changed)[key][:45])

    def test_training_objective_choice_does_not_accept_forecast_metrics(self):
        self.assertEqual(choose_teacher({"A": 10, "B": 9}), "B")
        self.assertEqual(choose_teacher({"A": 10, "B": 10 - 5e-13}), "A")
        with self.assertRaises(ValueError):
            choose_teacher({"A": 10, "B": 9, "prediction_rmse": 1})

    def test_unique_dates_partial_windows_and_candidate_partition(self):
        a = inventory(432, "complete_180")
        b = inventory(612, "complete_180")
        c = inventory(432, "available_prefix")
        self.assertEqual((a["window_days"], a["unique_days"]), (180, 180))
        self.assertEqual((b["window_days"], b["unique_days"]), (540, 360))
        self.assertEqual((c["window_days"], c["unique_days"]), (270, 180))
        self.assertEqual(
            (c["candidate_mean_unique_days"], c["candidate_scale_unique_days"]),
            (120, 60),
        )
        for row in (a, b, c):
            self.assertTrue(
                all(
                    w["end_index_exclusive"] <= row["outer_days"]
                    for w in row["windows"]
                )
            )
            self.assertEqual(sum(row["date_multiplicity"].values()), row["window_days"])

    def test_rejects_missing_days_or_nonfinite_values(self):
        y = np.zeros((240, 4))
        with self.assertRaises(ValueError):
            describe_error(y[:-1], y[:-1], 60)
        y[-1, 0] = np.nan
        with self.assertRaises(ValueError):
            describe_error(y, y, 60)
        with self.assertRaises(ValueError):
            correlation(np.array([1, 2]), np.array([1, np.nan]))

    def test_short_prefix_is_unaffected_by_future_labels_and_malformed_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.csv"
            frame = pd.DataFrame(
                {
                    "Date": pd.date_range("2016-07-01", periods=70).strftime(
                        "%Y-%m-%d"
                    ),
                    "Rainfall/mm": np.ones(70),
                    "RWL/m": np.full(70, 150),
                    **{
                        p + "/mm": np.arange(70, dtype=float)
                        for p in ("ATU1", "ATU5", "MJ3", "MJ1")
                    },
                }
            )
            frame.to_csv(path, index=False)
            before = load_observations(path, 60)
            frame.loc[60:, "ATU1/mm"] = np.nan
            frame.loc[60:, "Date"] = "invalid future date"
            frame.to_csv(path, index=False)
            after = load_observations(path, 60)
            for a, b in zip(before, after):
                np.testing.assert_array_equal(a, b)
            with self.assertRaises(ValueError):
                load_observations(path, 70)


if __name__ == "__main__":
    unittest.main()
