"""Research contracts: offline information boundary and deterministic calibration."""

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from transformer_calibration.core import (
    calibration,
    neighbors,
    read_labels,
    shrink,
    unit,
)


class CalibrationContracts(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(41)
        self.y = self.rng.normal(size=(792, 4)).cumsum(0)
        self.source = dict(
            indices=[702, 792], teacher_prefix=612, available_after_selection_index=701
        )
        self.mu = self.y[702:] + self.rng.normal(size=(90, 4))

    def test_shrink_fallback_and_ensemble_commute(self):
        b = self.rng.normal(size=(120, 4))
        originals = self.rng.normal(size=(3, 120, 4))
        np.testing.assert_array_equal(shrink(b, originals[0], 0), b)
        np.testing.assert_allclose(
            shrink(b, originals[0], 1), originals[0], atol=1e-14, rtol=0
        )
        np.testing.assert_allclose(
            shrink(b, originals.mean(0), 0.5),
            np.mean([shrink(b, v, 0.5) for v in originals], 0),
            atol=1e-14,
            rtol=0,
        )

    def test_distance_count_unique_ties_and_boundary(self):
        dist = np.arange(1, 377)
        take = neighbors(dist, 400, "DIST90")
        for i in range(400):
            expected = sorted(
                range(376), key=lambda j: (abs(int(dist[j]) - (i + 1)), int(dist[j]))
            )[:90]
            self.assertEqual(take[i].tolist(), expected)
            self.assertEqual(len(set(take[i])), 90)
        self.assertEqual(set(take[0]), set(range(90)))
        self.assertEqual(set(take[-1]), set(range(286, 376)))
        self.assertEqual(take[100, :5].tolist(), [100, 99, 101, 98, 102])

    def test_selection_exclusion_and_unmatured_rejection(self):
        with self.assertRaises(ValueError):
            calibration(self.mu, self.y[:-1], self.source, 20, "LAST90")
        invalid = dict(self.source, indices=[701, 791])
        with self.assertRaises(ValueError):
            calibration(self.mu, self.y, invalid, 20, "LAST90")
        with self.assertRaises(ValueError):
            neighbors(np.arange(1, 90), 40, "DIST90")
        with self.assertRaises(ValueError):
            neighbors(np.ones(90, int), 40, "DIST90")

    def test_target_label_poison_cannot_enter_prefix_reader(self):
        full = np.vstack([self.y, self.rng.normal(size=(669, 4))])
        columns = [p + "/mm" for p in ("ATU1", "ATU5", "MJ3", "MJ1")]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "labels.csv"
            pd.DataFrame(full, columns=columns).to_csv(p, index=False)
            before = read_labels(p, 792)
            full[792:] = 1e120
            pd.DataFrame(full, columns=columns).to_csv(p, index=False)
            after = read_labels(p, 792)
            np.testing.assert_array_equal(before, after)
            for rule in ("LAST90", "DIST90", "DIST90_UNIT"):
                a = calibration(self.mu, before, self.source, 376, rule)[0]
                b = calibration(self.mu, after, self.source, 376, rule)[0]
                np.testing.assert_array_equal(a, b)

    def test_development_distance_rule_degenerates(self):
        a = calibration(self.mu, self.y, self.source, 376, "LAST90")[0]
        b, ix, _ = calibration(self.mu, self.y, self.source, 376, "DIST90")
        np.testing.assert_array_equal(a, b)
        self.assertTrue(np.all(np.sort(ix, axis=1) == np.arange(90)))

    def test_units_only_use_the_two_fitting_prefixes(self):
        direct = calibration(self.mu, self.y, self.source, 376, "DIST90")[0]
        normalized = calibration(self.mu, self.y, self.source, 376, "DIST90_UNIT")[0]
        expected = direct * unit(self.y) / unit(self.y[:612])
        np.testing.assert_allclose(normalized, expected, atol=1e-14, rtol=0)
        changed = self.y.copy()
        changed[:702] += 50
        unchanged_raw = calibration(self.mu, changed, self.source, 376, "LAST90")[0]
        raw = calibration(self.mu, self.y, self.source, 376, "LAST90")[0]
        np.testing.assert_array_equal(unchanged_raw, raw)

    def test_zero_errors_floor_and_invalid_rule(self):
        sigma = calibration(self.y[702:], self.y, self.source, 376, "LAST90")[0]
        np.testing.assert_array_equal(sigma, np.full((376, 4), 1e-6))
        with self.assertRaises(ValueError):
            neighbors(np.arange(1, 100), 50, "NEW_RULE")

    def test_saved_arrays_reload_exactly(self):
        sigma, ix, error = calibration(self.mu, self.y, self.source, 376, "DIST90_UNIT")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "issued.npz"
            np.savez_compressed(p, sigma=sigma, indices=ix, errors=error)
            with np.load(p) as saved:
                for k, value in (("sigma", sigma), ("indices", ix), ("errors", error)):
                    np.testing.assert_array_equal(saved[k], value)


if __name__ == "__main__":
    unittest.main()
