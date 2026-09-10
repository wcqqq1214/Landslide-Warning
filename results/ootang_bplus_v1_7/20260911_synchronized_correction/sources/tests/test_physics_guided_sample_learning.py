import copy
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd
from scipy.stats import norm
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from physics_guided_sample_learning.core import (
    ORIGINS,
    backward_loss,
    common_scaler,
    fit_scale,
    make_samples,
    new_model,
    predict,
    score_components,
    teacher_arrays,
)


def fixture(c=432):
    y = np.tile(np.arange(c, dtype=float)[:, None], (1, 4))
    teachers = {
        k: (
            y + k / 100,
            np.broadcast_to(
                np.arange(c)[:, None, None] / 100 + k / 100, (c, 20, 4)
            ).copy(),
        )
        for k in (*ORIGINS[c], c)
    }
    return y, teachers, common_scaler(teachers[c][1], c)


class SampleLearningTests(unittest.TestCase):
    def test_paired_dates_weights_and_out_of_fit_teachers(self):
        y, teachers, scaler = fixture()
        inside = make_samples("IN", 432, y, teachers, scaler)
        outside = make_samples("OOF", 432, y, teachers, scaler)
        self.assertEqual((len(outside.indices), outside.unique_days), (270, 180))
        np.testing.assert_array_equal(inside.indices, outside.indices)
        torch.testing.assert_close(inside.target, outside.target, rtol=0, atol=0)
        torch.testing.assert_close(inside.weights, outside.weights, rtol=0, atol=0)
        self.assertTrue(np.all(outside.origins <= outside.indices))
        self.assertTrue(np.all(outside.indices < 432))
        for t in np.unique(outside.indices):
            self.assertEqual(float(outside.weights[outside.indices == t].sum()), 1)
        self.assertFalse(torch.equal(inside.base, outside.base))
        self.assertFalse(torch.equal(inside.windows, outside.windows))

    def test_extra_future_labels_features_or_teachers_are_rejected(self):
        y, teachers, scaler = fixture(342)
        with self.assertRaises(ValueError):
            make_samples("OOF", 342, np.vstack((y, y[:1])), teachers, scaler)
        extra = dict(teachers, **{})
        extra[432] = teachers[342]
        with self.assertRaises(ValueError):
            make_samples("IN", 342, y, extra, scaler)
        with self.assertRaises(ValueError):
            common_scaler(np.concatenate((teachers[342][1], teachers[342][1][:1])), 342)
        with self.assertRaises(ValueError):
            make_samples(
                "IN",
                342,
                y,
                {252: teachers[252], 342: (y[:-1], teachers[342][1][:-1])},
                scaler,
            )

    def test_zero_initialization_exactly_reproduces_physical_mean(self):
        y, teachers, scaler = fixture(342)
        model = new_model(0)
        self.assertEqual(sum(p.numel() for p in model.parameters()), 6993)
        mean = predict(model, teachers[342][0], teachers[342][1], scaler, chunk=17)
        np.testing.assert_array_equal(mean[29:], teachers[342][0][29:])
        self.assertTrue(np.isnan(mean[:29]).all())

    def test_chunked_full_batch_gradient_equivalence(self):
        y, teachers, scaler = fixture(342)
        full = make_samples("OOF", 342, y, teachers, scaler)
        samples = replace(
            full,
            windows=full.windows[:11],
            base=full.base[:11],
            target=full.target[:11],
            weights=full.weights[:11],
            indices=full.indices[:11],
            origins=full.origins[:11],
            unique_days=11,
        )
        a = new_model(1)
        with torch.no_grad():
            a.output.weight.fill_(0.01)
        b = copy.deepcopy(a)
        la, lb = backward_loss(a, samples, 11), backward_loss(b, samples, 3)
        self.assertAlmostEqual(la, lb, places=12)
        for ap, bp in zip(a.parameters(), b.parameters()):
            torch.testing.assert_close(ap.grad, bp.grad, rtol=1e-10, atol=1e-12)
        self.assertGreater(float(a.gates.weight.grad.norm()), 0)

    def test_scale_is_prefix_segment_only_and_does_not_mutate_mean(self):
        means = np.zeros((3, 90, 4))
        y = np.full((90, 4), 3.0)
        original = means.copy()
        np.testing.assert_array_equal(fit_scale(means, y), np.full((3, 4), 3.0))
        np.testing.assert_array_equal(means, original)
        np.testing.assert_array_equal(fit_scale(means, y * 0), np.full((3, 4), 0.001))
        with self.assertRaises(ValueError):
            fit_scale(np.zeros((3, 91, 4)), np.zeros((91, 4)))

    def test_future_drivers_and_saved_states_cannot_change_earlier_forecasts(self):
        n = 100
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            forcing = np.column_stack((np.arange(n) / 100, np.full(n, 150.0)))
            mean = np.tile(np.arange(n, dtype=float)[:, None], (1, 4))
            fields = dict(
                mean=mean,
                dates=pd.date_range("2016-07-01", periods=n)
                .strftime("%Y-%m-%d")
                .to_numpy(dtype="U10"),
                rain_head=np.zeros((n, 4)),
                moisture=np.zeros((n, 4)),
                reservoir_head=np.zeros(n),
            )
            path = source / "inner_432_252_B.npz"
            np.savez_compressed(path, **fields)
            base, x = teacher_arrays(source, 252, "B", forcing, np.zeros(4))
            # Use training-only statistics with a frozen transformer in this synthetic case.
            from physics_guided.features import Scaler

            scaler = Scaler.fit(x[:70], np.ones((20, 1)), static=4)
            model = new_model(2)
            with torch.no_grad():
                model.output.weight.fill_(0.02)
            before = predict(model, base, x, scaler)
            forcing[70:] += 1000
            fields["mean"][70:] += 10000
            fields["rain_head"][70:] += 1000
            np.savez_compressed(path, **fields)
            after_base, after_x = teacher_arrays(source, 252, "B", forcing, np.zeros(4))
            after = predict(model, after_base, after_x, scaler)
            np.testing.assert_array_equal(before[:70], after[:70])
            self.assertFalse(np.array_equal(before[70:], after[70:]))

    def test_probability_and_score_partitions_match_known_gaussian(self):
        means, labels, scales = (
            np.zeros((1, 612, 4)),
            np.zeros((612, 4)),
            np.full((1, 4), 2.0),
        )
        rows, curves = score_components(means, scales, labels, 432, "P0")
        self.assertEqual(len(rows), 8)
        for row in rows:
            self.assertEqual(row["days"], 402 if row["part"] == "train" else 180)
            self.assertEqual(row["rmse_mm"], 0)
            self.assertEqual(row["coverage_90"], 1)
            self.assertAlmostEqual(row["width_90_mm"], 4 * norm.ppf(0.95), places=5)
            self.assertAlmostEqual(
                row["crps_mm"], 2 * (np.sqrt(2) - 1) / np.sqrt(np.pi)
            )
        self.assertEqual(curves["mean"].shape, (582, 4))


if __name__ == "__main__":
    unittest.main()
