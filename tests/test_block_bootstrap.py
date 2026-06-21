import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "code"))

from block_bootstrap import moving_block_indices, percentile_interval  # noqa: E402


class MovingBlockBootstrapTests(unittest.TestCase):
    def test_indices_are_deterministic_and_preserve_contiguous_blocks(self):
        first = moving_block_indices(10, 3, np.random.default_rng(42))
        second = moving_block_indices(10, 3, np.random.default_rng(42))

        np.testing.assert_array_equal(first, second)
        self.assertEqual(len(first), 10)
        self.assertTrue(np.all((0 <= first) & (first < 10)))
        for start in range(0, 9, 3):
            np.testing.assert_array_equal(np.diff(first[start:start + 3]), [1, 1])

    def test_same_date_station_rows_remain_paired(self):
        observations = np.column_stack((np.arange(8) * 10, np.arange(8) * 10 + 1))
        indices = moving_block_indices(8, 3, np.random.default_rng(7))

        resampled = observations[indices]

        np.testing.assert_array_equal(resampled[:, 1] - resampled[:, 0], 1)

    def test_invalid_sample_or_block_length_is_rejected(self):
        rng = np.random.default_rng(0)

        with self.assertRaises(ValueError):
            moving_block_indices(0, 1, rng)
        with self.assertRaises(ValueError):
            moving_block_indices(5, 0, rng)
        with self.assertRaises(ValueError):
            moving_block_indices(5, 6, rng)

    def test_percentile_interval_matches_quantiles(self):
        lower, upper = percentile_interval([0.0, 1.0, 2.0, 3.0], 0.5)

        self.assertAlmostEqual(lower, 0.75)
        self.assertAlmostEqual(upper, 2.25)

    def test_percentile_interval_rejects_invalid_inputs(self):
        with self.assertRaises(ValueError):
            percentile_interval([], 0.95)
        with self.assertRaises(ValueError):
            percentile_interval([[1.0]], 0.95)
        with self.assertRaises(ValueError):
            percentile_interval([1.0, np.nan], 0.95)
        with self.assertRaises(ValueError):
            percentile_interval([1.0], 1.0)


if __name__ == "__main__":
    unittest.main()
