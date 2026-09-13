"""Online ridge updates must equal batch fitting and reject immature labels."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from rolling_probability.online import OnlineRidge


def fixture(forgetting=1.0):
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
    return (
        OnlineRidge(model, features, base, target, 100, forgetting=forgetting),
        features,
        target,
        rng,
    )


class OnlineChecks(unittest.TestCase):
    def test_forgetting_matches_weighted_svd_with_fixed_penalty(self):
        factor = 0.93
        state, x, y, rng = fixture(factor)
        all_x, all_y = x[:, 1].copy(), y[:, 1].copy()
        weights = np.ones(len(x))
        for step in range(9):
            row, target = rng.normal(size=(4, 2)), rng.normal(size=4)
            state.update(
                1, row, np.zeros(4), target, 100 + step, 101 + step, 102 + step
            )
            weights = np.r_[factor * weights, 1.0]
            all_x, all_y = (
                np.concatenate([all_x, row[None]]),
                np.concatenate([all_y, target[None]]),
            )
            for p in range(4):
                augmented = np.vstack(
                    [np.sqrt(weights[:, None]) * all_x[:, p], np.sqrt(1.2) * np.eye(2)]
                )
                response = np.r_[np.sqrt(weights) * all_y[:, p], np.zeros(2)]
                expected = np.linalg.lstsq(augmented, response, rcond=None)[0]
                np.testing.assert_allclose(
                    state.beta[1, p], expected, atol=1e-12, rtol=1e-12
                )
                gram = augmented.T @ augmented
                np.testing.assert_allclose(
                    state.gram[1, p], gram, atol=1e-12, rtol=1e-12
                )
            self.assertAlmostEqual(state.effective_weight[1], weights.sum(), places=12)
        np.testing.assert_array_equal(state.effective_weight[[0, 2]], [12, 12])

    def test_rejected_supervision_does_not_decay_history(self):
        state, _, _, _ = fixture(0.9)
        before = [
            v.copy()
            for v in (state.gram, state.rhs, state.beta, state.effective_weight)
        ]
        with self.assertRaises(ValueError):
            state.update(2, np.ones((4, 2)), np.zeros(4), np.ones(4), 100, 102, 102)
        for a, b in zip(
            before, (state.gram, state.rhs, state.beta, state.effective_weight)
        ):
            np.testing.assert_array_equal(a, b)
        state.update(2, np.ones((4, 2)), np.zeros(4), np.ones(4), 100, 102, 103)
        after = state.gram.copy()
        with self.assertRaises(ValueError):
            state.update(2, np.ones((4, 2)), np.zeros(4), np.ones(4), 100, 102, 104)
        np.testing.assert_array_equal(after, state.gram)

    def test_factor_one_preserves_default_and_invalid_factors_fail(self):
        a, _, _, _ = fixture()
        b, _, _, _ = fixture(1.0)
        for k in range(3):
            for obj in (a, b):
                obj.update(
                    k, np.ones((4, 2)), np.zeros(4), np.arange(4), 100, 100 + k, 101 + k
                )
        for field in ("gram", "rhs", "beta", "effective_weight"):
            np.testing.assert_array_equal(getattr(a, field), getattr(b, field))
        for bad in (0, -0.1, 1.01, np.nan):
            with self.assertRaises(ValueError):
                fixture(bad)

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
