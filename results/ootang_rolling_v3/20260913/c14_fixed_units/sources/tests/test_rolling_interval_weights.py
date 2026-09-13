"""Interval risk agrees with independent CDF roots and strictly mature pools."""

from pathlib import Path
from statistics import NormalDist
import sys
import unittest

import numpy as np
from scipy.optimize import brentq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from rolling_probability.mixture import (
    IntervalWeights,
    central_radius,
    distribution_scores,
    mixture_crps,
)
from rolling_probability.scoring import score_predictions


def loss_grid(error, c, s, grid):
    normal = NormalDist()
    scores = []
    for w in grid:
        r = brentq(
            lambda x: (1 - w) * normal.cdf(x / c) + w * normal.cdf(x / s) - 0.95,
            0,
            s * 5,
            xtol=1e-13,
        )
        scores.append(2 * r + 20 * max(abs(error) - r, 0))
    return np.array(scores)


class IntervalChecks(unittest.TestCase):
    def test_recent_grid_risk_matches_independent_roots_and_eviction(self):
        m = IntervalWeights(2, 3, 100, 100, 0.9, 1e-10, 60)
        recent = []
        for i, error in enumerate([0.0, 10.0, 5.0, 0.1, 2.0]):
            c, s = 1 + i * 0.1, 5 + i
            m.update_batch(
                np.full((1, 4), error),
                np.full((1, 4), c),
                np.full((1, 4), s),
                np.array([100 + i]),
                100 + i,
                101 + i,
            )
            recent.append(loss_grid(error, c, s, m.grid))
            expected = np.mean(recent[-3:], axis=0)
            np.testing.assert_allclose(
                m.mean_losses[0], np.tile(expected, (4, 1)), atol=1e-10, rtol=1e-12
            )
            wanted = np.flatnonzero(
                expected <= expected.min() + 1e-10 * max(1, abs(expected.min()))
            )[0]
            np.testing.assert_array_equal(m.weights[0], m.grid[wanted])
        self.assertEqual(m.state()["pool_counts"], [3, 0])
        np.testing.assert_array_equal(m.weights[1], 0)

    def test_equal_scales_choose_zero_for_all_points(self):
        m = IntervalWeights(1, 90, 100, 100, 0.9, 1e-10, 60)
        m.update_batch(
            np.array([[0.0, 1.0, 10.0, -100.0]]),
            np.ones((1, 4)),
            np.ones((1, 4)),
            np.array([100]),
            100,
            101,
        )
        np.testing.assert_array_equal(m.weights, 0)

    def test_invalid_batch_is_atomic_and_future_values_cannot_change_weights(self):
        m = IntervalWeights(2, 90, 100, 100, 0.9, 1e-10, 60)
        before = m.state()
        for value in (-1e6, 1e6):
            with self.assertRaises(ValueError):
                m.update_batch(
                    np.full((2, 4), value),
                    np.ones((2, 4)),
                    np.full((2, 4), 3.0),
                    np.array([101, 100]),
                    101,
                    101,
                )
            self.assertEqual(before, m.state())
        m.update_batch(
            np.ones((2, 4)),
            np.ones((2, 4)),
            np.full((2, 4), 3.0),
            np.array([101, 100]),
            101,
            102,
        )
        after = m.state()
        with self.assertRaises(ValueError):
            m.update_batch(
                np.ones((2, 4)),
                np.ones((2, 4)),
                np.full((2, 4), 3.0),
                np.array([101, 100]),
                101,
                103,
            )
        self.assertEqual(after, m.state())

    def test_saved_mixture_controls_keep_true_scores(self):
        c, s, w = np.ones((3, 1, 4)), np.full((3, 1, 4), 7.0), np.full((3, 1, 4), 0.2)
        mu = np.full((3, 1, 4), 10.0)
        pred = dict(
            origins=np.arange(1, 4),
            mean=mu,
            core_sigma=c,
            wide_sigma=s,
            weight=w,
            sigma=np.sqrt((1 - w) * c * c + w * s * s),
        )
        labels = np.arange(4.0)[:, None] + np.zeros((4, 4))
        for level in (0.8, 0.9, 0.95):
            r = central_radius(c, s, w, level)
            percent = round(100 * level)
            pred[f"lower{percent}"], pred[f"upper{percent}"] = mu - r, mu + r
        actual = distribution_scores(pred, labels, [0.8, 0.9, 0.95])
        expected = mixture_crps(labels[1:] - mu[:, 0], c[:, 0], s[:, 0], w[:, 0]).mean(
            axis=0
        )
        np.testing.assert_allclose(actual.crps, expected, rtol=0, atol=1e-12)
        gaussian = score_predictions(pred, labels, [0.8, 0.9, 0.95])
        self.assertGreater(abs(actual.crps - gaussian.crps).max(), 0.1)


if __name__ == "__main__":
    unittest.main()
