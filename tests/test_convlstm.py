import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
import torch

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "code"))

import convlstm  # noqa: E402


class ConvLSTMForecastTests(unittest.TestCase):
    def test_outputs_are_grouped_under_convlstm_figures(self):
        expected = ROOT / "figures" / "convlstm"

        self.assertEqual(convlstm.OUT_PNG.parent, expected)
        self.assertEqual(convlstm.OUT_METRICS.parent, expected)
        self.assertEqual(convlstm.OUT_PERIOD_METRICS.parent, expected)
        self.assertEqual(convlstm.OUT_CALIBRATION_METRICS.parent, expected)

    def test_default_lookback_matches_thesis_max_window(self):
        self.assertEqual(convlstm.HORIZON, 1)
        self.assertEqual(convlstm.LOOKBACK, 7)
        self.assertEqual(convlstm.CAL_FRAC, 0.2)
        self.assertEqual(convlstm.THESIS_WINDOWS["MJ9"], 7)
        self.assertEqual(convlstm.THESIS_WINDOWS["MJ1"], 2)
        self.assertEqual(convlstm.THESIS_WINDOWS["MJ3"], 2)

    def test_forecast_outputs_ordered_quantiles(self):
        model = convlstm.ConvLSTMForecast(
            in_ch=1,
            hid_ch=4,
            kernel=3,
            quantiles=[0.1, 0.5, 0.9],
        )
        x = torch.randn(2, 5, 1, 4, 7)

        pred = model(x)

        self.assertEqual(tuple(pred.shape), (2, 3, 4, 7))
        self.assertTrue(torch.all(pred[:, 0] <= pred[:, 1]))
        self.assertTrue(torch.all(pred[:, 1] <= pred[:, 2]))

    def test_station_windows_align_last_and_future_rows(self):
        values = np.arange(20, dtype=float).reshape(10, 2)

        future, last, delta = convlstm.make_station_windows(
            values,
            split=6,
            lookback=3,
            horizon=2,
        )

        np.testing.assert_array_equal(last[0], values[5])
        np.testing.assert_array_equal(future[0], values[7])
        np.testing.assert_array_equal(delta[0], values[7] - values[5])
        np.testing.assert_array_equal(last[-1], values[7])
        np.testing.assert_array_equal(future[-1], values[9])
        self.assertEqual(tuple(future.shape), (3, 2))

    def test_forecast_metrics_include_baseline_and_quantile_crossings(self):
        y_true = np.array([[1.0, 2.0], [3.0, 5.0]])
        last = np.array([[0.0, 1.0], [2.0, 4.0]])
        p10 = np.array([[0.5, 1.5], [3.5, 4.5]])
        p50 = np.array([[1.0, 2.5], [3.0, 5.5]])
        p90 = np.array([[1.5, 2.2], [4.0, 6.0]])

        metrics = convlstm.compute_forecast_metrics(p10, p50, p90, y_true, last)

        self.assertIn("model_rmse", metrics)
        self.assertIn("baseline_rmse", metrics)
        self.assertIn("coverage", metrics)
        self.assertIn("mean_pinball", metrics)
        self.assertIn("interval_score_80", metrics)
        self.assertAlmostEqual(metrics["model_r2"], metrics["model_nse"])
        self.assertEqual(metrics["p50_gt_p90"], 1)
        self.assertEqual(metrics["p10_gt_p50"], 1)
        self.assertGreater(metrics["baseline_rmse"], 0)

    def test_forecast_interval_metrics_match_hand_calculation(self):
        y_true = np.array([1.0, 2.0])
        last = np.array([0.0, 1.0])
        p10 = y_true - 0.5
        p50 = y_true.copy()
        p90 = y_true + 0.5

        metrics = convlstm.compute_forecast_metrics(p10, p50, p90, y_true, last)

        self.assertAlmostEqual(metrics["pinball_p10"], 0.05)
        self.assertAlmostEqual(metrics["pinball_p50"], 0.0)
        self.assertAlmostEqual(metrics["pinball_p90"], 0.05)
        self.assertAlmostEqual(metrics["mean_pinball"], 1.0 / 30.0)
        self.assertAlmostEqual(metrics["coverage"], 1.0)
        self.assertAlmostEqual(metrics["coverage_gap"], 0.2)
        self.assertAlmostEqual(metrics["mean_width"], 1.0)
        self.assertAlmostEqual(metrics["interval_score_80"], 1.0)

    def test_delta_scale_uses_increment_variability(self):
        train_delta = np.array([
            [0.1, 10.0],
            [0.2, 11.0],
            [0.3, 12.0],
            [0.4, 13.0],
        ])

        scale = convlstm.make_delta_scale(train_delta, floor=0.01)

        np.testing.assert_allclose(scale, train_delta.std(axis=0), rtol=1e-12)
        self.assertLess(scale[0], scale[1])

    def test_conformal_interval_calibration_is_station_specific(self):
        p10 = np.array([[0.0, 0.0], [0.0, 0.0]])
        p90 = np.array([[1.0, 1.0], [1.0, 1.0]])
        y_true = np.array([[0.5, 1.4], [0.5, 1.3]])

        p10_cal, p90_cal, qhat = convlstm.calibrate_intervals(
            p10,
            p90,
            y_true,
            target_coverage=0.8,
        )

        np.testing.assert_allclose(qhat, [0.0, 0.4])
        np.testing.assert_allclose(p10_cal[:, 0], p10[:, 0])
        np.testing.assert_allclose(p90_cal[:, 0], p90[:, 0])
        self.assertLessEqual(p10_cal[0, 0], p10[0, 0])
        self.assertGreaterEqual(p90_cal[0, 1], y_true[0, 1])

    def test_conformal_interval_calibration_rejects_invalid_target(self):
        values = np.ones((2, 1))

        with self.assertRaises(ValueError):
            convlstm.calibrate_intervals(values, values, values, 1.0)

    def test_chronological_calibration_split_keeps_latest_training_windows(self):
        n_fit, n_calibration = convlstm.chronological_fit_calibration_split(
            1138,
            0.2,
        )

        self.assertEqual(n_fit, 911)
        self.assertEqual(n_calibration, 227)
        self.assertEqual(n_fit + n_calibration, 1138)

    def test_chronological_calibration_split_rejects_invalid_fraction(self):
        with self.assertRaises(ValueError):
            convlstm.chronological_fit_calibration_split(100, 1.0)

    def test_model_input_scaling_ignores_post_fit_values(self):
        df = pd.DataFrame({
            column: [1.0, 2.0, 3.0, 4.0]
            for column in convlstm.EXOG_COLS
        })
        disp = np.array([
            [1.0, 10.0],
            [2.0, 20.0],
            [3.0, 30.0],
            [4.0, 40.0],
        ])
        modified_df = df.copy()
        modified_df.loc[2:, convlstm.EXOG_COLS] = 10000.0
        modified_disp = disp.copy()
        modified_disp[2:] = 10000.0
        def interp(values):
            means = values.mean(axis=1)[:, None, None]
            return np.broadcast_to(
                means,
                (len(values), convlstm.GRID_H, convlstm.GRID_W),
            )

        inputs, scale = convlstm.make_model_inputs(df, disp, 2, interp)
        modified_inputs, modified_scale = convlstm.make_model_inputs(
            modified_df,
            modified_disp,
            2,
            interp,
        )

        np.testing.assert_allclose(inputs[:2], modified_inputs[:2])
        np.testing.assert_allclose(scale, modified_scale)

    def test_station_metric_rows_include_each_station(self):
        y_true = np.array([[1.0, 2.0], [3.0, 5.0]])
        last = np.array([[0.0, 1.0], [2.0, 4.0]])
        p10 = np.array([[0.5, 1.5], [2.5, 4.5]])
        p50 = np.array([[1.0, 2.5], [3.0, 5.5]])
        p90 = np.array([[1.5, 3.0], [3.5, 6.0]])

        rows = convlstm.station_metric_rows(
            p10,
            p50,
            p90,
            y_true,
            last,
            station_names=["MJ9", "MJ1"],
            thesis_windows={"MJ9": 7},
        )

        self.assertEqual([row["station"] for row in rows], ["MJ9", "MJ1"])
        self.assertEqual(rows[0]["thesis_window"], 7)
        self.assertEqual(rows[1]["thesis_window"], "")
        self.assertEqual(rows[0]["interval_variant"], "calibrated")
        self.assertIn("rmse_skill_vs_baseline", rows[0])

    def test_period_metrics_keep_contiguous_date_blocks(self):
        dates = pd.date_range("2020-01-01", periods=6)
        y_true = np.arange(12, dtype=float).reshape(6, 2)
        last = y_true - 1.0
        p10 = y_true - 0.5
        p50 = y_true
        p90 = y_true + 0.5

        rows = convlstm.period_metric_rows(
            p10,
            p50,
            p90,
            y_true,
            last,
            dates,
            n_periods=3,
        )

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["start_date"], "2020-01-01")
        self.assertEqual(rows[0]["end_date"], "2020-01-02")
        self.assertEqual(rows[0]["interval_variant"], "calibrated")
        self.assertEqual(rows[-1]["start_date"], "2020-01-05")
        self.assertEqual(rows[-1]["n_dates"], 2)


if __name__ == "__main__":
    unittest.main()
