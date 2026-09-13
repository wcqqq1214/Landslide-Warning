from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))

from rolling_probability.information_variance import variance_terms


def test_information_variance_matches_augmented_svd():
    rng = np.random.default_rng(20260913)
    x = rng.normal(size=(12, 3, 4, 2))
    available = np.ones((12, 3), bool)
    available[0] = False
    x[0] = 0
    units = rng.uniform(0.1, 2, size=(3, 4))
    actual, _, _ = variance_terms(x, available, units)
    for i in range(12):
        for k in range(min(3, 12 - i)):
            for p in range(4):
                past = [j for j in range(i) if j + k < i and available[j, k]]
                design = np.vstack([np.eye(2), x[past, k, p]])
                _, singular, vt = np.linalg.svd(design, full_matrices=False)
                expected = units[k, p] ** 2 * np.sum((vt @ x[i, k, p] / singular) ** 2)
                np.testing.assert_allclose(actual[i, k, p], expected, atol=1e-11)
    np.testing.assert_array_equal(actual[0], np.zeros((3, 4)))


def test_unmatured_input_does_not_change_current_covariance():
    x = np.random.default_rng(3).normal(size=(10, 3, 4, 2))
    available = np.ones((10, 3), bool)
    available[0] = False
    x[0] = 0
    original, _, _ = variance_terms(x, available, np.ones((3, 4)))
    changed = x.copy()
    changed[5, 2] *= 1e6  # Target 7 is still unknown at origin 6.
    perturbed, _, _ = variance_terms(changed, available, np.ones((3, 4)))
    np.testing.assert_array_equal(original[6, 2], perturbed[6, 2])


class InformationVarianceTests(unittest.TestCase):
    def test_svd(self):
        test_information_variance_matches_augmented_svd()

    def test_maturity(self):
        test_unmatured_input_does_not_change_current_covariance()


if __name__ == "__main__":
    unittest.main()
