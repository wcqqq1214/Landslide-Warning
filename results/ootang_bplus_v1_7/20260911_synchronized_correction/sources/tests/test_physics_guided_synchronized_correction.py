from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from physics_guided.features import Scaler
from physics_guided_forecast_error.artifacts import load_observations
from physics_guided_sample_learning.core import new_model, fit_scale
from physics_guided_synchronized_correction.core import (
    POINTS,
    limit_torch,
    training_constants,
    predict,
    read_teacher,
)
from physics_guided_synchronized_correction.verify import numpy_limit, independent_crps


class SynchronizedCorrectionTests(unittest.TestCase):
    def test_amplitude_and_rate_hold_under_extreme_switching(self):
        raw = np.tile(
            np.array([[1e9, -1e9, 2e8, -1e8], [-1e9, 1e9, -2e8, 1e8]]), (50, 1)
        )
        a, d = np.array([1, 2, 3, 4.0]), np.array([0.01, 0.2, 10, 2.0])
        result = limit_torch(torch.as_tensor(raw), a, d).numpy()
        self.assertTrue((abs(result) <= a + 1e-12).all())
        self.assertTrue(
            (abs(np.diff(np.vstack([np.zeros(4), result]), axis=0)) <= d + 1e-12).all()
        )
        np.testing.assert_allclose(result, numpy_limit(raw, a, d), rtol=0, atol=1e-12)
        with self.assertRaises(ValueError):
            limit_torch(torch.as_tensor(raw), a, np.zeros(4))

    def test_later_loss_differentiates_through_earlier_corrections(self):
        raw = (
            torch.linspace(-0.3, 0.4, 24, dtype=torch.float64)
            .reshape(6, 4)
            .requires_grad_()
        )
        a, d = np.full(4, 2.0), np.full(4, 0.5)
        loss = (limit_torch(raw, a, d)[-1] * torch.tensor([1.0, 2.0, 3.0, 4.0])).sum()
        grad = torch.autograd.grad(loss, raw)[0].numpy()
        self.assertGreater(abs(grad[0, 0]), 1e-8)
        original = raw.detach().numpy()
        for index in ((0, 0), (2, 1), (5, 3)):
            upper, lower = original.copy(), original.copy()
            upper[index] += 1e-6
            lower[index] -= 1e-6
            difference = (
                (numpy_limit(upper, a, d)[-1] - numpy_limit(lower, a, d)[-1])
                * np.arange(1, 5)
            ).sum() / 2e-6
            np.testing.assert_allclose(grad[index], difference, rtol=1e-5, atol=1e-9)

    def test_training_constants_require_exact_prefix_and_known_rate(self):
        h = 342
        base = np.zeros((h, 4))
        labels = np.arange(h)[:, None] * np.array([0.01, 0.02, 0.04, 0.08])[None]
        features = np.zeros((h, 20, 4))
        _, a, d = training_constants(base, features, labels, h)
        np.testing.assert_allclose(d, [0.02, 0.04, 0.08, 0.16], rtol=0, atol=1e-14)
        np.testing.assert_allclose(
            a, 2 * np.sqrt((labels[30:] ** 2).mean(axis=0)), rtol=0, atol=1e-14
        )
        with self.assertRaises(ValueError):
            training_constants(np.pad(base, ((0, 1), (0, 0))), features, labels, h)
        labels[-1] = np.nan
        with self.assertRaises(ValueError):
            training_constants(base, features, labels, h)

    def test_zero_model_returns_the_physical_curve_without_boundary_reset(self):
        rng = np.random.default_rng(8)
        base = rng.normal(size=(80, 4)).cumsum(axis=0)
        features = rng.normal(size=(80, 20, 4))
        scaler = Scaler.fit(features[:40], np.ones((20, 1)), static=4)
        for strategy in ("U", "L"):
            mean, _ = predict(
                new_model(0),
                base,
                features,
                scaler,
                strategy,
                np.ones(4),
                np.ones(4),
                chunk=7,
            )
            np.testing.assert_array_equal(mean[29:], base[29:])
            self.assertTrue(np.isnan(mean[:29]).all())

    def test_rollout_is_causal_and_chunk_independent_with_nonzero_network(self):
        rng = np.random.default_rng(12)
        base = rng.normal(size=(90, 4)).cumsum(axis=0)
        features = rng.normal(size=(90, 20, 4))
        scaler = Scaler.fit(features[:40], np.ones((20, 1)), static=4)
        model = new_model(1)
        with torch.no_grad():
            model.output.weight.fill_(0.005)
        a, d = np.full(4, 5.0), np.full(4, 0.5)
        long, _ = predict(model, base, features, scaler, "L", a, d, chunk=17)
        short, _ = predict(model, base[:60], features[:60], scaler, "L", a, d, chunk=5)
        changed = features.copy()
        changed[60:] += 500
        other, _ = predict(model, base, changed, scaler, "L", a, d, chunk=9)
        np.testing.assert_allclose(long[29:60], short[29:], rtol=0, atol=1e-12)
        np.testing.assert_allclose(long[29:60], other[29:60], rtol=0, atol=1e-12)
        self.assertGreater(abs(long[60:] - other[60:]).max(), 0.001)

    def test_driver_reader_does_not_parse_forecast_displacement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frame = pd.DataFrame(
                {
                    "Date": pd.date_range("2016-07-01", periods=792).strftime(
                        "%Y-%m-%d"
                    ),
                    "Rainfall/mm": np.zeros(792),
                    "RWL/m": np.full(792, 150.0),
                    **{p + "/mm": np.zeros(792) for p in POINTS},
                }
            )
            np.savez_compressed(
                root / "inner_612_342_A.npz",
                dates=frame.Date[:522].to_numpy(dtype="U10"),
                mean=np.zeros((522, 4)),
                rain_head=np.zeros((522, 4)),
                moisture=np.zeros((522, 4)),
                reservoir_head=np.zeros(522),
            )
            path = root / "input.csv"
            frame.to_csv(path, index=False)
            before = read_teacher(path, root, 342)
            for p in POINTS:
                frame[p + "/mm"] = frame[p + "/mm"].astype(object)
                frame.loc[342:, p + "/mm"] = "unavailable future displacement"
            frame.loc[522:, "Date"] = "unavailable later date"
            frame.to_csv(path, index=False)
            after = read_teacher(path, root, 342)
            for x, y in zip(before, after):
                np.testing.assert_array_equal(x, y)
            self.assertEqual(load_observations(path, 342)[2].shape, (342, 4))
            with self.assertRaises(ValueError):
                load_observations(path, 432)

    def test_scale_uses_only_the_registered_forecast_segment(self):
        for days in (90, 180):
            means = np.zeros((3, days, 4))
            y = np.full((days, 4), 2.0)
            np.testing.assert_array_equal(fit_scale(means, y), np.full((3, 4), 2.0))
        with self.assertRaises(ValueError):
            fit_scale(np.zeros((3, 432, 4)), np.zeros((432, 4)))

    def test_independent_crps_known_normal_and_identical_mixture(self):
        expected = (np.sqrt(2) - 1) / np.sqrt(np.pi)
        for k in (1, 3):
            scores = independent_crps(np.zeros((k, 10)), np.ones(k), np.zeros(10))
            np.testing.assert_allclose(scores, expected, rtol=0, atol=1e-14)


if __name__ == "__main__":
    unittest.main()
