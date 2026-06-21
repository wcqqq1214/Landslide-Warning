import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "code"))

import convlstm_inner_validation as inner  # noqa: E402
import convlstm_rolling_validation as rolling  # noqa: E402


class ConvLSTMInnerValidationTests(unittest.TestCase):
    def test_outputs_are_grouped_under_convlstm_figures(self):
        expected = ROOT / "figures" / "convlstm"

        for path in (
            inner.OUT_RUNS,
            inner.OUT_SELECTION,
            inner.OUT_REFIT,
            inner.OUT_METRICS,
            inner.OUT_SUMMARY,
            inner.OUT_PREDICTIONS,
            inner.OUT_COMPARISON,
        ):
            self.assertEqual(path.parent, expected)

    def test_protocol_constants_match_preregistration(self):
        self.assertEqual(inner.SEEDS, (0, 1, 2, 3, 4))
        self.assertEqual(inner.INNER_VALIDATION_FRACTION, 0.2)
        self.assertEqual(inner.MAX_EPOCHS, 300)
        self.assertEqual(inner.MIN_EPOCHS, 30)
        self.assertEqual(inner.PATIENCE, 30)
        self.assertEqual(inner.MIN_RELATIVE_IMPROVEMENT, 0.001)

    def test_inner_splits_preserve_chronology_and_all_fit_windows(self):
        outer_splits = rolling.expanding_window_splits(1432)
        inner_splits = [
            inner.make_inner_split(split.fit_windows)
            for split in outer_splits
        ]

        self.assertEqual(
            [split.train_windows for split in inner_splits],
            [362, 545, 729],
        )
        self.assertEqual(
            [split.validation_windows for split in inner_splits],
            [90, 136, 182],
        )
        for outer, nested in zip(outer_splits, inner_splits):
            self.assertEqual(
                nested.train_windows + nested.validation_windows,
                outer.fit_windows,
            )

    def test_inner_date_metadata_precedes_outer_calibration(self):
        dates = pd.date_range("2016-07-30", periods=1432)
        outer = rolling.expanding_window_splits(len(dates))[0]
        nested = inner.make_inner_split(outer.fit_windows)

        metadata = inner.inner_date_metadata(outer, nested, dates)
        outer_metadata = rolling.split_metadata(outer, dates)

        self.assertEqual(metadata["inner_train_start_date"], "2016-08-06")
        self.assertEqual(metadata["inner_train_windows"], 362)
        self.assertEqual(metadata["inner_validation_windows"], 90)
        self.assertLess(
            metadata["inner_train_end_date"],
            metadata["inner_validation_start_date"],
        )
        self.assertLess(
            metadata["inner_validation_end_date"],
            outer_metadata["calibration_start_date"],
        )

    def test_monitor_requires_predeclared_relative_improvement(self):
        self.assertTrue(inner.is_monitor_improvement(1.0, None))
        self.assertTrue(inner.is_monitor_improvement(0.998, 1.0))
        self.assertFalse(inner.is_monitor_improvement(0.9995, 1.0))
        with self.assertRaises(ValueError):
            inner.is_monitor_improvement(np.nan, 1.0)

    def test_best_epoch_uses_absolute_minimum_and_earlier_tie(self):
        history = [
            {"epoch": 1, "validation_pinball_loss": 0.4},
            {"epoch": 2, "validation_pinball_loss": 0.3},
            {"epoch": 3, "validation_pinball_loss": 0.3},
        ]

        epoch, loss = inner.select_best_epoch(history)

        self.assertEqual(epoch, 2)
        self.assertEqual(loss, 0.3)

    def test_fixed_comparison_pairs_same_seed_fold_and_scope(self):
        base_row = {
            "seed": 0,
            "fold": 1,
            "scope": "overall",
            "interval_variant": "raw",
            "test_start_date": "2020-01-01",
            "test_end_date": "2020-01-10",
            "baseline_rmse": 1.0,
            "baseline_mae": 0.8,
        }
        for metric in inner.COMPARISON_METRICS:
            base_row.setdefault(metric, 0.5)
        fixed = pd.DataFrame([base_row])
        early_row = {**base_row, "model_rmse": 0.4, "model_mae": 0.4}
        early = pd.DataFrame([early_row])
        runs = pd.DataFrame([{
            "seed": 0,
            "fold": 1,
            "selected_epoch": 45,
            "stop_reason": "patience_exhausted",
        }])

        comparison = inner.build_fixed_comparison(early, fixed, runs)

        self.assertEqual(len(comparison), 1)
        self.assertEqual(comparison.loc[0, "selected_epoch"], 45)
        self.assertAlmostEqual(comparison.loc[0, "delta_model_rmse"], -0.1)
        self.assertTrue(comparison.loc[0, "rmse_improved"])
        self.assertFalse(comparison.loc[0, "best_seed_selected"])

    def test_fold_training_rejects_invalid_epoch_count_before_data_access(self):
        with self.assertRaises(TypeError):
            rolling.train_predict_fold(
                None,
                None,
                None,
                None,
                None,
                seed=0,
                epochs=1.5,
            )
        with self.assertRaises(ValueError):
            rolling.train_predict_fold(
                None,
                None,
                None,
                None,
                None,
                seed=0,
                epochs=0,
            )


if __name__ == "__main__":
    unittest.main()
