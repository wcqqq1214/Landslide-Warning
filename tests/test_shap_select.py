import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import numpy as np
from ngboost import NGBClassifier, NGBRegressor

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "code"))

import shap_select  # noqa: E402
import warning_thresholds  # noqa: E402


class ShapSelectTests(unittest.TestCase):
    def test_outputs_are_grouped_under_shap_figures(self):
        expected = ROOT / "figures" / "shap"
        threshold_dir = ROOT / "figures" / "thresholds"
        outputs = [
            shap_select.OUT_REG_PNG,
            shap_select.OUT_CLS_PNG,
            shap_select.OUT_REG_CSV,
            shap_select.OUT_CLS_CSV,
            shap_select.OUT_METRICS_CSV,
            shap_select.OUT_CV_METRICS_CSV,
        ]

        self.assertTrue(all(path.parent == expected for path in outputs))
        self.assertEqual(shap_select.OUT_THRESHOLDS_CSV.parent, threshold_dir)

    def test_binary_metrics_report_discrimination_and_calibration(self):
        metrics = shap_select.binary_probability_metrics(
            [0, 0, 1, 1],
            [0.1, 0.2, 0.8, 0.9],
        )

        self.assertEqual(metrics["auc"], 1.0)
        self.assertEqual(metrics["pr_auc"], 1.0)
        self.assertGreater(metrics["brier"], 0.0)
        self.assertEqual(metrics["positive_rows"], 2)

    def test_binary_metrics_mark_single_class_auc_unavailable(self):
        metrics = shap_select.binary_probability_metrics([0, 0], [0.1, 0.2])

        self.assertTrue(np.isnan(metrics["auc"]))
        self.assertTrue(np.isnan(metrics["pr_auc"]))

    def test_walk_forward_ranges_keep_train_before_test(self):
        ranges = shap_select.walk_forward_date_ranges(
            pd.date_range("2020-01-01", periods=12),
            n_splits=3,
        )

        self.assertEqual(len(ranges), 3)
        self.assertTrue(all(row["train_end"] < row["test_start"] for row in ranges))

    def test_compute_v0_uses_thesis_monthly_rate_formula(self):
        disp = pd.Series([0, 1, 3, 6, 10, 15], dtype=float)

        result = warning_thresholds.compute_v0(
            disp,
            train_frac=1.0,
            month_window_days=2,
            accel_percentile=1.0,
        )

        expected_rates = pd.Series([3.0, 5.0, 7.0, 9.0])
        self.assertAlmostEqual(result["v_bar_mm_per_month"], expected_rates.mean())
        self.assertAlmostEqual(result["sigma_mm_per_month"], expected_rates.std(ddof=1))
        self.assertAlmostEqual(
            result["v0_mm_per_month"],
            1.5 * expected_rates.mean() + 2 * expected_rates.std(ddof=1),
        )
        self.assertAlmostEqual(result["v0_orange_threshold"], 5 * result["v0_mm_per_month"])
        self.assertAlmostEqual(result["v0_red_threshold"], 10 * result["v0_mm_per_month"])
        self.assertEqual(
            result["v0_estimation_method"],
            "supervisor_specified_steady_mean_std",
        )
        self.assertEqual(
            result["high_level_threshold_source"],
            "chen_et_al_2024_eq10_default_vd",
        )

    def test_classify_monthly_rates_uses_v0_warning_levels(self):
        levels = warning_thresholds.classify_monthly_rates(
            pd.Series([0.0, 10.0, 49.0, 50.0, 90.0, 100.0]),
            v0=10.0,
        )

        self.assertEqual(levels.tolist(), [0, 1, 1, 2, 2, 3])

    def test_build_warning_frame_uses_station_specific_v0_and_max_level(self):
        df = pd.DataFrame({
            "Date": pd.date_range("2020-01-01", periods=6),
            "MJ9/mm": [0, 1, 2, 3, 9, 15],
            "MJ1/mm": [0, 1, 2, 3, 24, 55],
        })
        thresholds = {
            "MJ9": {"v0_mm_per_month": 5.0},
            "MJ1": {"v0_mm_per_month": 10.0},
        }

        warning_frame, _ = warning_thresholds.build_warning_frame(
            df,
            stations={"MJ9": "MJ9/mm", "MJ1": "MJ1/mm"},
            thresholds=thresholds,
            month_window_days=3,
        )

        self.assertEqual(warning_frame["warning_level"].tolist(), [-1, -1, -1, 0, 1, 2])
        self.assertEqual(warning_frame["MJ9_warning_level"].tolist(), [-1, -1, -1, 0, 1, 1])
        self.assertEqual(warning_frame["MJ1_warning_level"].tolist(), [-1, -1, -1, 0, 1, 2])

    def test_build_lagged_samples_uses_five_day_history_and_dynamic_v0_labels(self):
        df = pd.DataFrame({
            "Date": pd.date_range("2020-01-01", periods=10),
            "MJ9/mm": [0, 1, 2, 3, 4, 5, 9, 13, 17, 21],
            "Rainfall/mm": range(10),
            "RWL/m": range(150, 160),
            "GWT/m": range(260, 270),
            "aveT/℃": range(20, 30),
            "minT/℃": range(15, 25),
            "maxT/℃": range(25, 35),
            "DP": range(10, 20),
            "RH": np.linspace(0.5, 0.9, 10),
        })

        X, y_reg, y_cls, meta = shap_select.build_lagged_samples(
            df,
            stations={"MJ9": "MJ9/mm"},
            window=5,
            month_window_days=3,
            thresholds={"MJ9": {"v0_mm_per_month": 5.0}},
            rain_windows=(),
        )

        self.assertEqual(len(X), 3)
        self.assertIn("disp_lag1", X.columns)
        self.assertIn("RWL_lag5", X.columns)
        self.assertIn("disp_rate_lag1", X.columns)
        self.assertIn("disp_accel_lag5", X.columns)
        self.assertIn("RWL_rate_lag1", X.columns)
        self.assertIn("GWT_rate_lag1", X.columns)
        self.assertIn("station_MJ9", X.columns)
        self.assertEqual(y_reg.iloc[0], 4)
        self.assertEqual(y_reg.iloc[1], 4)
        self.assertEqual(y_cls.tolist(), [1, 1, 1])
        self.assertEqual(meta.iloc[0]["station"], "MJ9")
        self.assertEqual(meta["warning_level"].tolist(), [1, 1, 1])
        self.assertIn("previous_monthly_rate", meta.columns)
        self.assertIn("previous_warning_level", meta.columns)

    def test_persistence_baseline_uses_previous_warning_state(self):
        meta = pd.DataFrame({
            "previous_monthly_rate": [1.0, 2.0, 8.0, 9.0],
            "v0_mm_per_month": [5.0] * 4,
            "previous_warning_level": [0, 0, 1, 1],
        })

        metrics = shap_select.persistence_baseline_metrics(
            [0, 0, 1, 1],
            meta,
        )

        self.assertEqual(metrics["auc"], 1.0)
        self.assertEqual(metrics["f1"], 1.0)

    def test_build_lagged_samples_drops_monthly_rate_warmup(self):
        periods = 8
        df = pd.DataFrame({
            "Date": pd.date_range("2020-01-01", periods=periods),
            "MJ9/mm": range(periods),
            "Rainfall/mm": range(periods),
            "RWL/m": range(periods),
            "GWT/m": range(periods),
            "aveT/℃": range(periods),
            "minT/℃": range(periods),
            "maxT/℃": range(periods),
            "DP": range(periods),
            "RH": range(periods),
        })

        X, _, _, meta = shap_select.build_lagged_samples(
            df,
            stations={"MJ9": "MJ9/mm"},
            window=2,
            month_window_days=4,
            thresholds={"MJ9": {"v0_mm_per_month": 10.0}},
            rain_windows=(),
        )

        self.assertEqual(len(X), 4)
        self.assertEqual(meta["Date"].min(), pd.Timestamp("2020-01-05"))

    def test_time_train_mask_splits_unique_dates(self):
        meta = pd.DataFrame({
            "Date": list(pd.date_range("2020-01-01", periods=4)) * 2,
            "station": ["MJ9"] * 4 + ["MJ1"] * 4,
        })

        mask, split_date = shap_select.time_train_mask(meta, train_frac=0.5)

        self.assertEqual(split_date, pd.Timestamp("2020-01-03"))
        self.assertEqual(mask.sum(), 4)

    def test_train_models_uses_ngboost(self):
        X = pd.DataFrame({
            "disp_lag1": [0, 1, 2, 3, 4, 5],
            "RWL_lag1": [150, 151, 152, 153, 154, 155],
            "station_MJ9": [1, 1, 1, 1, 1, 1],
        })
        y_reg = pd.Series([0.0, 0.2, 0.1, 0.5, 0.4, 0.6])
        y_cls = pd.Series([0, 0, 0, 1, 1, 1])

        reg, cls = shap_select.train_models(X, y_reg, y_cls, n_estimators=5)

        self.assertIsInstance(reg, NGBRegressor)
        self.assertIsInstance(cls, NGBClassifier)

    def test_shap_explainer_uses_fixed_seed(self):
        background = pd.DataFrame({"x": [0.0, 1.0]})
        sample = pd.DataFrame({"x": [0.5]})

        with patch.object(shap_select.shap, "Explainer") as factory:
            factory.return_value.return_value.values = np.array([[0.1]])
            values = shap_select.shap_matrix(
                object(),
                background,
                sample,
                "regression",
            )

        self.assertEqual(factory.call_args.kwargs["seed"], shap_select.SEED)
        np.testing.assert_array_equal(values, [[0.1]])


if __name__ == "__main__":
    unittest.main()
