"""Scientific identities and causal boundaries for C7 distributions."""

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from rolling_probability.empirical import (
    ErrorPool,
    distribution_losses,
    empirical_quantile,
    replay,
)


def forecast(start, end, horizon=3):
    origins = np.arange(start, end)
    return dict(
        origins=origins,
        mean=np.zeros((len(origins), horizon, 4)),
        raw_sigma=np.ones((len(origins), horizon, 4)),
        teacher_prefixes=np.zeros(len(origins), dtype=int),
    )


class Stream:
    def __init__(self, values, start):
        self.values = values
        self.history = values[:start].copy()

    def release(self):
        row = self.values[len(self.history)].copy()
        self.history = np.vstack([self.history, row])
        return row


class Recorder:
    def __init__(self):
        self.events = []

    def event(self, kind, **fields):
        self.events.append((kind, fields))


class EmpiricalChecks(unittest.TestCase):
    def test_crps_matches_all_pairs_and_preserves_mean(self):
        radii = np.array([0.0, 0.5, 1.0, 10.0])
        support = np.r_[-radii[::-1], radii] * 2 + 17
        self.assertEqual(support.mean(), 17)
        for actual in (-100, 0, 17, 28, 100):
            independent = (
                np.abs(support - actual).mean()
                - 0.5 * np.abs(support[:, None] - support[None, :]).mean()
            )
            actual_loss = distribution_losses(
                np.array(actual),
                np.array(17),
                np.array(2),
                radii,
                "empirical",
                [0.8, 0.9, 0.95],
            )
            self.assertAlmostEqual(float(actual_loss["crps"]), independent, places=12)

    def test_quantile_flat_boundaries_and_symmetry(self):
        values = np.r_[-np.arange(90, 0, -1), np.arange(1, 91)]
        self.assertEqual(empirical_quantile(values, 0.05), -81.5)
        self.assertEqual(empirical_quantile(values, 0.95), 81.5)
        self.assertEqual(empirical_quantile(values, 0.025), -86)
        self.assertEqual(empirical_quantile(values, 0.975), 86)

    def test_zero_distribution_limit_both_laws(self):
        for law in ("empirical", "moment_gaussian"):
            result = distribution_losses(
                np.array(5), np.array(2), np.array(1), np.zeros(90), law, [0.9]
            )
            self.assertEqual(result["crps"], 3)
            self.assertEqual(result["width90"], 0)
            self.assertAlmostEqual(result["interval_score90"], 60)

    def test_initialization_and_maturity_boundaries(self):
        prior = forecast(0, 12)
        values = np.arange(48).reshape(12, 4)
        pool = ErrorPool(prior, values, 12, 3, 5)
        np.testing.assert_array_equal(
            pool.initial_origins, [[7, 8, 9, 10, 11], [6, 7, 8, 9, 10], [5, 6, 7, 8, 9]]
        )
        with self.assertRaises(ValueError):
            pool.forecast(11)
        with self.assertRaises(ValueError):
            pool.update(0, np.ones(4), np.ones(4), 12, 12)
        pool.update(0, np.ones(4), np.ones(4), 12, 13)
        with self.assertRaises(ValueError):
            pool.update(0, np.ones(4), np.ones(4), 12, 13)
        with self.assertRaises(ValueError):
            ErrorPool(prior, np.vstack([values, np.zeros(4)]), 12, 3, 5)
        with self.assertRaises(ValueError):
            ErrorPool(prior, values, 12, 3, 12)

    def test_future_observation_cannot_change_issued_distribution(self):
        values = np.arange(72).reshape(18, 4).astype(float)
        poisoned = values.copy()
        poisoned[15:] += 1e9
        current = {"FULL": forecast(12, 18)}
        prior = {"FULL": forecast(0, 12)}
        logs = []
        outputs = []
        for v in (values, poisoned):
            recorder = Recorder()
            result, _ = replay(
                current, prior, Stream(v, 12), 12, 18, 3, 5, recorder, "synthetic"
            )
            outputs.append(result["FULL"])
            logs.append(recorder.events)
        np.testing.assert_array_equal(
            outputs[0]["radii"][:, :, :4], outputs[1]["radii"][:, :, :4]
        )
        self.assertEqual(logs[0][:7], logs[1][:7])
        self.assertFalse(
            np.array_equal(outputs[0]["radii"][:, :, 4:], outputs[1]["radii"][:, :, 4:])
        )
        np.testing.assert_array_equal(outputs[0]["updates"], [6, 5, 4])
        for i in range(0, len(logs[0]), 2):
            self.assertEqual(logs[0][i][0], "forecast_locked")
            self.assertEqual(logs[0][i + 1][0], "observation_released")


if __name__ == "__main__":
    unittest.main()
