"""Online ridge updates must equal batch fitting and reject immature labels."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from rolling_probability.online import OnlineRidge


def fixture():
    rng = np.random.default_rng(4)
    features = rng.normal(size=(12, 3, 4, 2))
    target = rng.normal(size=(12, 3, 4))
    base = np.zeros_like(target)
    beta = np.empty((3, 4, 2))
    for h in range(3):
        for p in range(4):
            beta[h, p] = np.linalg.solve(
                features[:, h, p].T @ features[:, h, p] + 1.2 * np.eye(2),
                features[:, h, p].T @ target[:, h, p],
            )
    model = SimpleNamespace(
        dimensions=2,
        feature_scale=np.ones((3, 4, 2)),
        target_scale=np.ones((3, 4)),
        beta=beta,
        state={"alpha": 0.1},
    )
    return OnlineRidge(model, features, base, target, 100), features, target, rng


class OnlineChecks(unittest.TestCase):
    def test_each_update_equals_independent_augmented_batch_fit(self):
        state, x, y, rng = fixture()
        expected_x = x[:, 1].copy()
        expected_y = y[:, 1].copy()
        for step in range(8):
            row = rng.normal(size=(4, 2))
            target = rng.normal(size=4)
            state.update(
                1, row, np.zeros(4), target, 100 + step, 101 + step, 102 + step
            )
            expected_x = np.concatenate([expected_x, row[None]])
            expected_y = np.concatenate([expected_y, target[None]])
            for p in range(4):
                augmented = np.vstack([expected_x[:, p], np.sqrt(1.2) * np.eye(2)])
                response = np.r_[expected_y[:, p], np.zeros(2)]
                beta = np.linalg.lstsq(augmented, response, rcond=None)[0]
                np.testing.assert_allclose(
                    state.beta[1, p], beta, atol=1e-12, rtol=1e-12
                )
        self.assertEqual(state.point_solves, 32)

    def test_future_duplicate_and_wrong_horizon_are_rejected(self):
        state, _, _, _ = fixture()
        x = np.ones((4, 2))
        y = np.ones(4)
        before = state.beta.copy()
        with self.assertRaises(ValueError):
            state.update(2, x, y, y, 100, 102, 102)
        with self.assertRaises(ValueError):
            state.update(2, x, y, y, 100, 101, 103)
        with self.assertRaises(ValueError):
            state.update(0, x, y, y, 99, 99, 101)
        np.testing.assert_array_equal(before, state.beta)
        state.update(2, x, y, y, 100, 102, 103)
        with self.assertRaises(ValueError):
            state.update(2, x, y, y, 100, 102, 104)

    def test_only_the_matured_horizon_changes_and_scales_stay_frozen(self):
        state, _, _, rng = fixture()
        before = state.beta.copy()
        sx = state.model.feature_scale.copy()
        sy = state.model.target_scale.copy()
        state.update(
            1, rng.normal(size=(4, 2)), np.zeros(4), np.full(4, 1000.0), 100, 101, 102
        )
        np.testing.assert_array_equal(state.beta[[0, 2]], before[[0, 2]])
        self.assertFalse(np.array_equal(state.beta[1], before[1]))
        np.testing.assert_array_equal(state.model.feature_scale, sx)
        np.testing.assert_array_equal(state.model.target_scale, sy)


if __name__ == "__main__":
    unittest.main()
