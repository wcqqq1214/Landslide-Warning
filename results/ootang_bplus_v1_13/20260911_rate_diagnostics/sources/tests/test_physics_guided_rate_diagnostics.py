"""Analytic examples for frozen diagnostics, without trained models."""

import ast
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

from physics_guided_rate_diagnostics.core import (
    feature_statistics,
    multiplier_statistics,
    point_statistics,
    scale_statistics,
)
from physics_guided_rate_diagnostics.workflow import no_model_runtime, project


class DiagnosticTests(unittest.TestCase):
    def test_opposition_and_overshoot_do_not_count_zero_as_correct(self):
        demand = np.array([2.0, 2, 2, 0, 2])
        correction = np.array([-1.0, 5, 1, 1, 0])
        result = point_statistics(
            np.zeros(5), correction, demand, correction, np.zeros(5), 1e-6
        )
        self.assertEqual(result["active_days"], 3)
        self.assertAlmostEqual(result["opposed_fraction"], 1 / 3)
        self.assertAlmostEqual(result["overshot_fraction"], 1 / 3)
        self.assertAlmostEqual(result["mse_change_mm2"], 1.6)

    def test_canceling_components_do_not_add_rms(self):
        result = point_statistics([0, 0], [1, -1], [2, -2], [10, -10], [-9, 9], 1e-6)
        self.assertEqual(result["correction_rms_mm"], 1)
        self.assertEqual(result["background_rms_mm"], 10)
        self.assertEqual(result["state_rms_mm"], 9)
        self.assertEqual(result["mse_change_mm2"], -3)
        with self.assertRaises(ValueError):
            point_statistics([0], [1], [2], [10], [-8], 1e-6)

    def test_zero_denominators_remain_undefined(self):
        result = point_statistics([0], [0], [0], [0], [0], 1e-6)
        self.assertTrue(np.isnan(result["opposed_fraction"]))
        rates = multiplier_statistics([0.5, 2], [0, 0], 0.95)
        self.assertTrue(np.isnan(rates["weighted_mean"]))
        self.assertEqual(rates["lower_fraction"], 0.5)

    def test_rate_weighting_excludes_idle_day_dominance(self):
        result = multiplier_statistics([0.5, 2, 1], [0, 3, 1], 0.95)
        self.assertEqual(result["weighted_lower_fraction"], 0)
        self.assertEqual(result["weighted_upper_fraction"], 0.75)
        self.assertEqual(result["weighted_mean"], 1.75)
        with self.assertRaises(ValueError):
            multiplier_statistics([2.01], [1], 0.95)

    def test_future_ranges_do_not_refit_training_reference(self):
        z = np.zeros((6, 12))
        z[:4, 0] = [0, 1, 2, 3]
        z[4:, 0] = [-1, 4]
        rows, joint = feature_statistics(z, 4, 4, 6, 1e-12)
        self.assertEqual(rows[0]["training_max_z"], 3)
        self.assertEqual(rows[0]["outside_days"], 2)
        self.assertEqual(joint["outside_fraction"], 1)
        z[4:, 0] = [0, 3]
        _, joint = feature_statistics(z, 4, 4, 6, 1e-12)
        self.assertEqual(joint["outside_days"], 0)
        with self.assertRaises(ValueError):
            feature_statistics(z, 4, 4, 7, 1e-12)

    def test_bias_and_variation_are_not_a_new_scale_fit(self):
        result = scale_statistics([-1, 1], [3, 5], 1)
        self.assertEqual(result["sigma_mm"], 1)
        self.assertEqual(result["prediction_bias_squared_mm2"], 16)
        self.assertEqual(result["prediction_sd_squared_mm2"], 1)
        self.assertAlmostEqual(result["prediction_rms_over_sigma"], np.sqrt(17))

    def test_observation_rows_are_points_and_columns_are_domains(self):
        values = np.array([[[1, 2, 3, 4], [2, 4, 6, 8]]])
        matrix = np.array([[0, 0, 0, 1], [0, 1, 0, 0], [1, 0, 1, 0], [0, 0, 1, 0]])
        expected = np.array([[[4, 2, 4, 3], [8, 4, 8, 6]]])
        np.testing.assert_array_equal(project(values, matrix), expected)

    def test_runtime_guard_rejects_neural_import_without_loading_torch(self):
        no_model_runtime()
        with patch.dict("sys.modules", {"torch": object()}):
            with self.assertRaises(RuntimeError):
                no_model_runtime()

    def test_no_neural_or_mechanical_imports(self):
        folder = (
            Path(__file__).resolve().parents[1] / "code/physics_guided_rate_diagnostics"
        )
        forbidden = (
            "torch",
            "ctypes",
            "physics_guided_state_pinn",
            "physics_guided_pinn",
            "physics_guided_shared_mechanics",
            "physics_guided.autograd",
        )
        for path in folder.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    names = [item.name for item in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                self.assertFalse(
                    any(name.startswith(forbidden) for name in names), path
                )


if __name__ == "__main__":
    unittest.main()
