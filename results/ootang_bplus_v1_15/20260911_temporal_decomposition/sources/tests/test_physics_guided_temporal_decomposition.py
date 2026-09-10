"""Synthetic signed groups and prefix-range checks, with no field fits."""

import unittest
from unittest.mock import patch

import numpy as np

from physics_guided_temporal_decomposition.core import bounds, decompose, range_arrays


class DecompositionTests(unittest.TestCase):
    def test_known_signed_groups_preserve_cancellation(self):
        z = np.zeros((6, 48))
        z[:, [0, 12, 24]] = np.arange(6)[:, None]
        coefficient = np.zeros((49, 4))
        coefficient[0], coefficient[1], coefficient[13], coefficient[25] = 1, 2, -1, -1
        actual = decompose(z, coefficient, 4, start=2, scale=1)
        np.testing.assert_array_equal(
            actual["groups"],
            ["intercept", "hydro", "history", "s", "p", "rb", "rc", "rE", "background"],
        )
        np.testing.assert_array_equal(actual["parts"][5, :4, 0], [1, 10, -5, -5])
        np.testing.assert_array_equal(actual["parts"].sum(axis=1), np.ones((6, 4)))
        np.testing.assert_array_equal(actual["excess"].sum(axis=1), np.zeros((6, 4)))

    def test_boundary_split_and_zero_training_excess(self):
        z = np.zeros((6, 12))
        z[:, 0] = np.arange(6)
        coefficient = np.zeros((13, 4))
        coefficient[1] = 1
        data = decompose(z, coefficient, 4, start=2, scale=1)
        np.testing.assert_array_equal(data["excess"][2:4], np.zeros((2, 2, 4)))
        np.testing.assert_array_equal(data["excess"][4:, 1, 0], [1, 2])
        np.testing.assert_array_equal(
            (data["parts"] - data["excess"])[4:, 1, 0], [3, 3]
        )

    def test_two_registered_reference_starts_are_distinct(self):
        z = np.array([-10, -9, 1, 2, -5, 4.0])[:, None]
        full, fit = range_arrays(z, 4, 0), range_arrays(z, 4, 2)
        np.testing.assert_array_equal(full["outside"][:, 0], [False, True])
        np.testing.assert_array_equal(fit["below"][:, 0], [True, False])
        np.testing.assert_array_equal(fit["above"][:, 0], [False, True])
        np.testing.assert_array_equal(fit["distance"][:, 0], [6, 2])

    def test_future_values_do_not_change_reference_or_training_terms(self):
        z = np.arange(72.0).reshape(6, 12)
        coefficient = np.ones((13, 4))
        original = decompose(z, coefficient, 4, start=2)
        before = bounds(z, 4, 2)
        z[4:] = -1e8
        changed = decompose(z, coefficient, 4, start=2)
        np.testing.assert_array_equal(bounds(z, 4, 2), before)
        for key in ("parts", "excess"):
            np.testing.assert_array_equal(changed[key][:4], original[key][:4])

    def test_invalid_shapes_nonfinite_and_empty_ranges_rejected(self):
        for z, coef, origin, start in [
            (np.ones((6, 12)), np.ones((13, 3)), 4, 2),
            (np.ones((6, 13)), np.ones((14, 4)), 4, 2),
            (np.full((6, 12), np.nan), np.ones((13, 4)), 4, 2),
            (np.ones((6, 12)), np.ones((13, 4)), 4, 4),
            (np.ones((6, 12)), np.ones((13, 4)), 6, 2),
        ]:
            with (
                self.subTest(shape=z.shape, origin=origin, start=start),
                self.assertRaises(ValueError),
            ):
                decompose(z, coef, origin, start=start)

    def test_zero_fit_path_never_calls_linear_solver(self):
        with patch(
            "numpy.linalg.solve", side_effect=AssertionError("Fitting is forbidden")
        ):
            data = decompose(np.ones((6, 12)), np.ones((13, 4)), 4, start=2)
            self.assertEqual(data["parts"].shape, (6, 2, 4))


if __name__ == "__main__":
    unittest.main()
