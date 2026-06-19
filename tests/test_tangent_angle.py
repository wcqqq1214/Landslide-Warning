import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "code"))

import tangent_angle
import features


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


class TangentAngleWarningTests(unittest.TestCase):
    def test_raw_angle_is_45_degrees_when_rate_equals_uniform_rate(self):
        result = tangent_angle.tangent_angle_series(
            [0.0, 2.0, 4.0, 6.0, 8.0],
            v_eq=2.0,
        )

        self.assertEqual(
            list(result.columns),
            ["raw_rate", "smooth_rate", "alpha_raw", "alpha_smooth"],
        )
        self.assertEqual(result.loc[4, "alpha_raw"], 45.0)

    def test_classification_uses_exact_paper_thresholds(self):
        angles = [44.9, 45.0, 45.1, 79.9, 80.0, 84.9, 85.0, np.nan, -1.0]

        result = tangent_angle.classify_tangent_angles(angles)

        self.assertEqual(result.tolist(), [0, 0, 1, 1, 2, 2, 3, -1, 0])
        self.assertTrue(pd.api.types.is_integer_dtype(result.dtype))

    def test_classification_rejects_all_nonfinite_angles(self):
        result = tangent_angle.classify_tangent_angles(
            [np.inf, -np.inf, np.nan]
        )

        self.assertEqual(result.tolist(), [-1, -1, -1])

    def test_nonfinite_displacement_never_produces_valid_angle_levels(self):
        displacements = [
            [0.0, 1.0, np.inf, 3.0, 4.0],
            [0.0, 1.0, -np.inf, 3.0, 4.0],
            [0.0, 1.0, np.nan, 3.0, 4.0],
        ]

        for displacement in displacements:
            with self.subTest(displacement=displacement):
                result = tangent_angle.tangent_angle_series(
                    displacement,
                    v_eq=1.0,
                )

                for rate_column, angle_column in [
                    ("raw_rate", "alpha_raw"),
                    ("smooth_rate", "alpha_smooth"),
                ]:
                    invalid_rate = ~np.isfinite(result[rate_column])
                    self.assertTrue(invalid_rate.any())
                    self.assertTrue(result.loc[invalid_rate, angle_column].isna().all())
                    levels = tangent_angle.classify_tangent_angles(
                        result[angle_column]
                    )
                    self.assertTrue(levels.loc[invalid_rate].eq(-1).all())

    def test_smoothing_is_causal_and_requires_a_full_window(self):
        first = tangent_angle.tangent_angle_series(
            [0.0, 1.0, 2.0, 3.0, 100.0],
            v_eq=1.0,
        )
        changed_future = tangent_angle.tangent_angle_series(
            [0.0, 1.0, 2.0, 3.0, 1000.0],
            v_eq=1.0,
        )

        self.assertTrue(first["alpha_smooth"].iloc[:2].isna().all())
        np.testing.assert_allclose(
            first["alpha_smooth"].iloc[:4],
            changed_future["alpha_smooth"].iloc[:4],
            equal_nan=True,
        )

    def test_three_point_smoothing_has_known_linear_slope_and_angle(self):
        result = tangent_angle.tangent_angle_series(
            [0.0, 2.0, 4.0, 6.0],
            v_eq=2.0,
            smooth_window=3,
        )

        self.assertTrue(result["smooth_rate"].iloc[:2].isna().all())
        np.testing.assert_allclose(result["smooth_rate"].iloc[2:], [2.0, 2.0])
        np.testing.assert_allclose(result["alpha_smooth"].iloc[2:], [45.0, 45.0])

    def test_one_point_smoothing_is_raw_rate_identity(self):
        result = tangent_angle.tangent_angle_series(
            [0.0, 2.0, 4.0, 1.0],
            v_eq=2.0,
            smooth_window=1,
        )

        pd.testing.assert_series_equal(
            result["smooth_rate"],
            result["raw_rate"],
            check_names=False,
        )
        pd.testing.assert_series_equal(
            result["alpha_smooth"],
            result["alpha_raw"],
            check_names=False,
        )

    def test_persistence_selects_highest_level_with_enough_hits(self):
        result = tangent_angle.persistent_warning_levels(
            [0, 2, 2, 1, 2, 0],
            window=5,
            min_hits=3,
        )

        self.assertEqual(result.tolist(), [-1, -1, -1, -1, 2, 2])
        self.assertTrue(pd.api.types.is_integer_dtype(result.dtype))

    def test_persistence_stays_green_when_no_level_has_enough_hits(self):
        result = tangent_angle.persistent_warning_levels(
            [0, 1, 0, 1, 0],
            window=5,
            min_hits=3,
        )

        self.assertEqual(result.tolist(), [-1, -1, -1, -1, 0])

    def test_persistence_counts_higher_levels_toward_yellow(self):
        result = tangent_angle.persistent_warning_levels(
            [0, 1, 2, 1, 0],
            window=5,
            min_hits=3,
        )

        self.assertEqual(result.tolist(), [-1, -1, -1, -1, 1])

    def test_persistence_is_invalid_when_window_contains_invalid_level(self):
        result = tangent_angle.persistent_warning_levels(
            [0, 2, -1, 2, 2],
            window=5,
            min_hits=3,
        )

        self.assertEqual(result.tolist(), [-1, -1, -1, -1, -1])

    def test_tangent_angle_series_requires_finite_positive_uniform_rate(self):
        for v_eq in [0.0, -1.0, np.nan, np.inf, -np.inf]:
            with self.subTest(v_eq=v_eq):
                with self.assertRaisesRegex(ValueError, "正数"):
                    tangent_angle.tangent_angle_series([0.0, 1.0], v_eq=v_eq)

    def test_smooth_window_must_be_positive_integer_and_not_bool(self):
        for smooth_window in [0, -1, 1.5, True, False, np.bool_(True)]:
            with self.subTest(smooth_window=smooth_window):
                with self.assertRaises(ValueError):
                    tangent_angle.tangent_angle_series(
                        [0.0, 1.0],
                        v_eq=1.0,
                        smooth_window=smooth_window,
                    )

    def test_persistence_parameters_must_be_valid_positive_integers(self):
        for window in [0, -1, 1.5, True, False, np.bool_(True)]:
            with self.subTest(window=window):
                with self.assertRaises(ValueError):
                    tangent_angle.persistent_warning_levels(
                        [0, 1, 2],
                        window=window,
                        min_hits=1,
                    )

        for min_hits in [0, -1, 1.5, True, False, np.bool_(True)]:
            with self.subTest(min_hits=min_hits):
                with self.assertRaises(ValueError):
                    tangent_angle.persistent_warning_levels(
                        [0, 1, 2],
                        window=3,
                        min_hits=min_hits,
                    )

        with self.assertRaises(ValueError):
            tangent_angle.persistent_warning_levels(
                [0, 1, 2],
                window=2,
                min_hits=3,
            )


class TangentAngleFrameTests(unittest.TestCase):
    def test_build_tangent_frame_reports_auditable_manual_station_columns(self):
        dates = pd.date_range("2020-01-01", periods=10)
        frame = pd.DataFrame(
            {
                " Date ": dates,
                " MJ9/mm ": [0.0, 1.0, 3.0, 5.0, 7.0, 9.0,
                              11.0, 14.0, 18.0, 23.0],
            }
        )

        result, parameters = tangent_angle.build_tangent_frame(
            frame,
            {"MJ9": "MJ9/mm"},
            manual_ranges={"MJ9": ("2020-01-02", "2020-01-06")},
        )

        self.assertEqual(
            list(result.columns),
            [
                "Date",
                "MJ9_alpha_raw",
                "MJ9_alpha_smooth",
                "MJ9_alpha_daily_level",
                "MJ9_alpha_level",
            ],
        )
        self.assertEqual(len(result), 10)
        self.assertEqual(parameters["MJ9"]["method"], "manual")
        self.assertEqual(parameters["MJ9"]["start_date"], "2020-01-02")
        self.assertEqual(parameters["MJ9"]["end_date"], "2020-01-06")
        self.assertAlmostEqual(parameters["MJ9"]["v_eq_mm_per_day"], 2.0)
        self.assertTrue(
            pd.api.types.is_integer_dtype(result["MJ9_alpha_daily_level"])
        )
        self.assertTrue(pd.api.types.is_integer_dtype(result["MJ9_alpha_level"]))

    def test_build_tangent_frame_rejects_non_daily_or_unsorted_dates(self):
        invalid_dates = [
            ["2020-01-01", "2020-01-03", "2020-01-04"],
            ["2020-01-02", "2020-01-01", "2020-01-03"],
        ]

        for dates in invalid_dates:
            with self.subTest(dates=dates):
                frame = pd.DataFrame(
                    {"Date": dates, "MJ9/mm": [0.0, 1.0, 2.0]}
                )
                with self.assertRaises(ValueError):
                    tangent_angle.build_tangent_frame(
                        frame,
                        {"MJ9": "MJ9/mm"},
                        manual_ranges={
                            "MJ9": ("2020-01-01", "2020-01-03")
                        },
                    )

    def test_uniform_rate_rows_preserves_station_order_and_statistics(self):
        parameters = {
            "MJ9": {
                "method": "manual",
                "v_eq_mm_per_day": 2.0,
                "rate_mad_mm_per_day": 0.0,
            },
            "MJ1": {
                "method": "automatic_candidate",
                "v_eq_mm_per_day": 1.5,
                "rate_mad_mm_per_day": 0.1,
            },
        }

        rows = tangent_angle.uniform_rate_rows(parameters)

        self.assertEqual(
            rows,
            [
                {
                    "station": "MJ9",
                    "method": "manual",
                    "v_eq_mm_per_day": 2.0,
                    "rate_mad_mm_per_day": 0.0,
                },
                {
                    "station": "MJ1",
                    "method": "automatic_candidate",
                    "v_eq_mm_per_day": 1.5,
                    "rate_mad_mm_per_day": 0.1,
                },
            ],
        )


class FeatureGenerationTests(unittest.TestCase):
    def test_build_features_includes_auditable_angles_and_legacy_alias(self):
        dates = pd.date_range("2020-01-01", periods=40)
        data = {
            " Date ": dates.astype(str),
            " RWL/m ": np.linspace(140.0, 141.0, len(dates)),
            " Rainfall/mm ": np.ones(len(dates)),
        }
        for station_index, column in enumerate(features.DISP_COLS, start=1):
            data[f" {column} "] = np.arange(len(dates), dtype=float) * station_index
        frame = pd.DataFrame(data).iloc[::-1].reset_index(drop=True)

        result, parameters = features.build_features(frame)

        expected_mj9_columns = {
            "MJ9_disp",
            "MJ9_v",
            "MJ9_a",
            "MJ9_alpha_raw",
            "MJ9_alpha_smooth",
            "MJ9_alpha_daily_level",
            "MJ9_alpha_level",
            "MJ9_alpha",
        }
        self.assertTrue(expected_mj9_columns.issubset(result.columns))
        pd.testing.assert_series_equal(
            result["MJ9_alpha"],
            result["MJ9_alpha_smooth"],
            check_names=False,
        )
        self.assertEqual(
            list(parameters),
            [features.short(column) for column in features.DISP_COLS],
        )
        self.assertEqual(len(parameters), 8)
        self.assertEqual(int(result.isna().sum().sum()), 0)
        self.assertEqual(result["Date"].iloc[0], dates[29])
        self.assertEqual(result["Date"].iloc[-1], dates[-1])

    def test_uniform_rate_output_is_grouped_with_tangent_angle_artifacts(self):
        self.assertEqual(
            features.TANGENT_DIR,
            features.ROOT / "figures" / "tangent_angle",
        )
        self.assertEqual(
            features.OUT_UNIFORM_RATES,
            features.TANGENT_DIR / "uniform_rates.csv",
        )


if __name__ == "__main__":
    unittest.main()
