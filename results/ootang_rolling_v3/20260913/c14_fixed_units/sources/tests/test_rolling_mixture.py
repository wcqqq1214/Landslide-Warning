"""Mixture probabilities, proper scores and delayed weight learning."""

from pathlib import Path
from statistics import NormalDist
import sys
import unittest

import numpy as np
from scipy.integrate import quad
from scipy.optimize import minimize_scalar

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from rolling_probability.mixture import MixtureWeights, central_radius, mixture_crps


def cdf_integral(error, core, wide, weight):
    a, b = NormalDist(0, core), NormalDist(0, wide)

    def cdf(t):
        return (1 - weight) * a.cdf(t) + weight * b.cdf(t)

    return (
        quad(lambda t: cdf(t) ** 2, -np.inf, error, epsabs=1e-11)[0]
        + quad(lambda t: (1 - cdf(t)) ** 2, error, np.inf, epsabs=1e-11)[0]
    )


class MixtureChecks(unittest.TestCase):
    def test_crps_matches_cdf_integrals_and_equal_scale_limit(self):
        for error, core, wide, weight in [
            (0, 1, 5, 0.2),
            (-4, 0.3, 7, 0.5),
            (2, 1, 3, 0),
            (8, 1, 4, 1),
            (0.7, 2, 2, 0.8),
        ]:
            self.assertAlmostEqual(
                float(mixture_crps(error, core, wide, weight)),
                cdf_integral(error, core, wide, weight),
                places=9,
            )
        a = mixture_crps(np.array([-1.0, 0, 2]), 2, 2, 0)
        b = mixture_crps(np.array([-1.0, 0, 2]), 2, 2, 1)
        np.testing.assert_allclose(a, b, atol=1e-14, rtol=0)

    def test_quantiles_have_the_requested_cdf_and_gaussian_limits(self):
        core, wide, weight = (
            np.array([1.0, 2.0, 0.5]),
            np.array([7.0, 9.0, 2.0]),
            np.array([0.0, 0.4, 1.0]),
        )
        normal = NormalDist()
        for level in (0.8, 0.9, 0.95):
            radius = central_radius(core, wide, weight, level)
            for j in range(3):
                cdf = (1 - weight[j]) * normal.cdf(radius[j] / core[j]) + weight[
                    j
                ] * normal.cdf(radius[j] / wide[j])
                self.assertAlmostEqual(cdf, (1 + level) / 2, places=13)
            self.assertAlmostEqual(
                radius[0], normal.inv_cdf((1 + level) / 2) * core[0], places=12
            )
            self.assertAlmostEqual(
                radius[2], normal.inv_cdf((1 + level) / 2) * wide[2], places=12
            )

    def test_closed_weight_minimizes_actual_historical_mixture_crps(self):
        learner = MixtureWeights(2, 5, 100)
        errors = np.array([0.0, 0.1, 1.0, 6.0, -4.0, 0.2, -0.3])
        for i, error in enumerate(errors):
            learner.update(
                1,
                np.full(4, error),
                np.ones(4),
                np.full(4, 5.0),
                100 + i,
                101 + i,
                102 + i,
            )
            recent = errors[max(0, i - 4) : i + 1]
            optimum = minimize_scalar(
                lambda w: np.mean([cdf_integral(e, 1, 5, w) for e in recent]),
                bounds=(0, 1),
                method="bounded",
                options={"xatol": 1e-9},
            )
            chosen = learner.weights[1, 0]
            actual = np.mean([cdf_integral(e, 1, 5, chosen) for e in recent])
            self.assertLessEqual(actual, optimum.fun + 1e-8)
        np.testing.assert_array_equal(learner.weights[0], 0)
        self.assertEqual(len(learner.pools[1]), 5)

    def test_unmatured_duplicate_and_future_changes_do_not_change_current_weights(self):
        learner = MixtureWeights(3, 90, 100)
        before = learner.state()
        for value in (-100000.0, 100000.0):
            with self.assertRaises(ValueError):
                learner.update(
                    2, np.full(4, value), np.ones(4), np.full(4, 3.0), 100, 102, 102
                )
            self.assertEqual(before, learner.state())
        learner.update(2, np.ones(4), np.ones(4), np.full(4, 3.0), 100, 102, 103)
        after = learner.state()
        with self.assertRaises(ValueError):
            learner.update(2, np.ones(4), np.ones(4), np.full(4, 3.0), 100, 102, 104)
        self.assertEqual(after, learner.state())


if __name__ == "__main__":
    unittest.main()
