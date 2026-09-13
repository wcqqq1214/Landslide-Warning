"""Numerical and causal contracts for the small probabilistic regression."""

import sys
from pathlib import Path
import json
import unittest
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from rolling_probability.data import Teacher, training_examples
from rolling_probability.ridge import (
    dynamic_features,
    fit_distribution,
    scale_objective,
    RidgeDistribution,
)


class RidgeContracts(unittest.TestCase):
    def test_conditional_scale_gradient(self):
        rng = np.random.default_rng(9)
        residual = rng.normal(size=(20, 3))
        q = rng.uniform(0.1, 2, (20, 3))
        floor = np.ones((1, 3)) * 0.01
        logs = np.array([-0.5, 0.2])
        _, analytic = scale_objective(logs, residual, q, floor)
        numerical = []
        for j in range(2):
            step = np.eye(2)[j] * 1e-6
            numerical.append(
                (
                    scale_objective(logs + step, residual, q, floor)[0]
                    - scale_objective(logs - step, residual, q, floor)[0]
                )
                / 2e-6
            )
        np.testing.assert_allclose(analytic, numerical, rtol=1e-7, atol=1e-9)

    def test_regularized_solve_matches_augmented_svd_and_reload(self):
        root = Path(__file__).resolve().parents[1]
        spec = json.loads(
            (root / "config/ootang_rolling_probability.v3_2.json").read_text()
        )
        n = 300
        t = np.arange(n, dtype=float)
        y = 0.002 * t[:, None] ** 2 * np.arange(1, 5)[None, :] + 0.1 * t[:, None]
        mean = 0.09 * t[:, None] * np.arange(1, 5)[None, :]
        f = np.column_stack([np.zeros(n), np.ones(n) * 160])
        teacher = Teacher(
            100, mean, f, np.ones((n, 4)) * 0.5, np.zeros((n, 4)), f[:, 1]
        )
        data = training_examples(
            y[:240], {100: teacher}, horizon=5, extra_baselines=spec["extra_baselines"]
        )
        models = [fit_distribution(data, 0.01, arm, spec) for arm in ("FULL", "DATA")]
        experts = np.stack([data["baselines"][k] for k in spec["expert_names"]], axis=2)
        raw, base, _ = dynamic_features(data["x"], experts)
        for model in models:
            D = model.dimensions
            N = len(raw)
            h = 4
            p = 2
            x = raw[:, h, p, :D] / model.feature_scale[h, p]
            target = (data["y"][:, h, p] - base[:, h, p]) / model.target_scale[h, p]
            A = np.concatenate([x / np.sqrt(N), np.eye(D) * np.sqrt(0.01)])
            b = np.r_[target / np.sqrt(N), np.zeros(D)]
            coef = np.linalg.lstsq(A, b, rcond=None)[0]
            np.testing.assert_allclose(coef, model.beta[h, p], atol=1e-11, rtol=1e-9)
            values = model.predict_raw(data["x"], data["z"], data["anchor"], experts)
            copied = RidgeDistribution(json.loads(json.dumps(model.state)))
            duplicate = copied.predict_raw(
                data["x"], data["z"], data["anchor"], experts
            )
            for a, b in zip(values, duplicate):
                np.testing.assert_array_equal(a, b)
            self.assertTrue(np.isfinite(values[0]).all())
            self.assertTrue((values[1] >= 0.01).all())
        self.assertEqual(models[0].dimensions, 6)
        self.assertEqual(models[1].dimensions, 4)


if __name__ == "__main__":
    unittest.main()
