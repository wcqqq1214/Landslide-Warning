"""Synthetic solutions and chronology checks, without field-model fitting."""

import unittest
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory
import numpy as np
import pandas as pd

from physics_guided_temporal_features.core import (
    Budget,
    POINTS,
    constant_fit,
    correction,
    ridge_fit,
    score,
    standardize,
    trailing_mean,
)
from physics_guided_temporal_features.verify import regression_substitution
from physics_guided_temporal_features.workflow import read_labels


def budget(fits=1):
    return Budget(
        dict(max_ridge_fits=fits, max_constant_estimates=1, max_feature_scalers=1)
    )


class TemporalFeatureTests(unittest.TestCase):
    def test_trailing_history_has_no_future_access(self):
        x = np.arange(50.0)[:, None]
        result = trailing_mean(x)
        self.assertEqual(result[0, 0], 0)
        self.assertEqual(result[29, 0], 14.5)
        self.assertEqual(result[30, 0], 15.5)
        x[31:] = 1e9
        np.testing.assert_array_equal(trailing_mean(x)[:31], result[:31])

    def test_scaler_accepts_only_registered_prefix(self):
        x = np.arange(10.0)[:, None] * np.ones((1, 48))
        fitted = standardize(x[:5], 5, budget())
        np.testing.assert_array_equal(fitted["mean"], np.full(48, 2.0))
        with self.assertRaises(ValueError):
            standardize(x, 5, budget())

    def test_known_ridge_solution_keeps_intercept_unpenalized(self):
        x = np.array([-1.0, 0, 1])[:, None]
        y = np.tile(100 * (2 + 3 * x), (1, 4))
        coefficient = ridge_fit(x, y, 3, 1 / 3, 100, budget())
        np.testing.assert_allclose(
            coefficient, np.full((2, 4), 2.0), rtol=0, atol=1e-12
        )
        np.testing.assert_allclose(
            correction(x, coefficient, 100),
            np.tile([[0], [200], [400]], (1, 4)),
            rtol=0,
            atol=1e-10,
        )

    def test_regularization_handles_duplicate_columns(self):
        x = np.column_stack([np.arange(4.0), np.arange(4.0)])
        coefficient = ridge_fit(x, np.ones((4, 4)) * 300, 4, 0.01, 100, budget())
        np.testing.assert_allclose(coefficient[0], 3, atol=1e-12)
        np.testing.assert_allclose(coefficient[1:], 0, atol=1e-12)

    def test_budget_is_checked_before_solve(self):
        with patch("physics_guided_temporal_features.core.np.linalg.solve") as solve:
            with self.assertRaises(RuntimeError):
                ridge_fit(np.ones((3, 1)), np.ones((3, 4)), 3, 0.01, 100, budget(0))
            solve.assert_not_called()

    def test_future_targets_cannot_be_passed_to_fit(self):
        with self.assertRaises(ValueError):
            ridge_fit(np.ones((5, 1)), np.ones((5, 4)), 3, 0.01, 100, budget())

    def test_nonfinite_target_is_rejected_before_budget_consumption(self):
        limits = budget()
        with self.assertRaises(ValueError):
            ridge_fit(np.ones((3, 1)), np.full((3, 4), np.nan), 3, 0.01, 100, limits)
        self.assertEqual(limits.counts["ridge_fits"], 0)

    def test_verifier_substitutes_known_solution_without_solving(self):
        x = np.array([-1.0, 0, 1])[:, None]
        target = np.tile(100 * (2 + 3 * x), (1, 4))
        coefficient = np.full((2, 4), 2.0)
        with patch(
            "numpy.linalg.solve",
            side_effect=AssertionError("No solving in verification"),
        ):
            result = regression_substitution(x, target, coefficient, 1 / 3, 100, 1e-9)
            self.assertLess(result["normal_equation_max_abs"], 1e-12)
            self.assertAlmostEqual(result["data"], 8 / 3)
            self.assertAlmostEqual(result["penalty"], 16 / 3)
            coefficient[0, 0] += 0.01
            with self.assertRaises(ValueError):
                regression_substitution(x, target, coefficient, 1 / 3, 100, 1e-9)

    def test_label_parser_does_not_convert_unopened_future_targets(self):
        frame = pd.DataFrame(
            {"Date": pd.date_range("2016-07-01", periods=342).strftime("%Y-%m-%d")}
        )
        for point in POINTS:
            frame[point + "/mm"] = ["1"] * 252 + ["future_not_opened"] * 90
        with TemporaryDirectory() as directory:
            path = Path(directory) / "labels.csv"
            frame.to_csv(path, index=False)
            np.testing.assert_array_equal(read_labels(path, 252), np.ones((252, 4)))
            with self.assertRaises(ValueError):
                read_labels(path, 342)

    def test_scoring_excludes_warmup_and_preserves_direction(self):
        base, labels = np.zeros((35, 4)), np.ones((35, 4)) * 2
        labels[:30] = 1e6
        output = np.full_like(base, -1)
        metrics = score(base, {"H": output, "P0": base}, labels, 32, 1e-6)
        actual = metrics[(metrics.strategy == "H") & (metrics.part == "forward")]
        np.testing.assert_array_equal(actual.days, [3] * 4)
        np.testing.assert_array_equal(actual.rmse_mm, [3] * 4)
        np.testing.assert_array_equal(actual.mae_mm, [3] * 4)
        np.testing.assert_array_equal(actual.opposed_fraction, [1] * 4)
        self.assertTrue(metrics[metrics.strategy == "P0"].opposed_fraction.isna().all())
        with self.assertRaises(ValueError):
            score(base, {"P0": base}, labels, 35, 1e-6)

    def test_constant_reference_uses_all_and_only_fit_rows(self):
        demand = np.arange(12.0).reshape(3, 4)
        limits = budget()
        np.testing.assert_array_equal(constant_fit(demand, 3, limits), [4, 5, 6, 7])
        self.assertEqual(limits.counts["constant_estimates"], 1)
        with self.assertRaises(ValueError):
            constant_fit(demand, 2, budget())


if __name__ == "__main__":
    unittest.main()
