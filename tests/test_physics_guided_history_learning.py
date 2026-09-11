"""Synthetic origin, masking, gradient, and continuous-forecast checks."""

import unittest

import numpy as np
import torch

from physics_guided_history_learning.core import (
    HISTORY_FLOOR,
    new_model,
    training_pairs,
    evaluation_pairs,
    history_values,
    training_scalers,
    windows,
    predict,
)


def example(h=70):
    t = np.arange(h + 180, dtype=float)
    base = np.stack([t * 0.2, t * 0.3, 100 + t * 0.1, 50 + t * 0.4], axis=1)
    labels = base[:h] + np.column_stack([np.arange(h) ** 2 / 100] * 4)
    features = np.sin(t[:, None, None] / 21 + np.arange(80).reshape(1, 20, 4))
    scalers = training_scalers(base[:h], features[:h], labels, h)
    return base, features, labels, scalers


class HistoryLearningTests(unittest.TestCase):
    def test_registered_training_sample_counts_and_boundaries(self):
        for h, count in ((342, 136), (432, 188), (612, 291)):
            pairs = training_pairs(h)
            self.assertEqual(len(pairs), count)
            self.assertTrue(np.all(pairs[:, 0] >= 31))
            self.assertTrue(np.all(pairs[:, 1] < h))
            self.assertEqual(len(np.unique(pairs[:, 1])), count)

    def test_evaluation_keeps_one_origin_across_all_forecast_days(self):
        pairs = evaluation_pairs(432, 612)
        np.testing.assert_array_equal(pairs[pairs[:, 1] >= 432, 0], np.full(180, 432))
        for target, expected in (
            (31, 31),
            (210, 31),
            (211, 211),
            (390, 211),
            (391, 391),
            (431, 391),
        ):
            self.assertEqual(pairs[pairs[:, 1] == target, 0].item(), expected)

    def test_observation_window_and_difference_left_endpoint(self):
        base, features, labels, scalers = example()
        x = windows(
            base, features, labels, 70, np.array([[31, 90]]), *scalers, "H"
        ).numpy()[0, :, :, 0]
        raw = x[:, 20:22] * scalers[1].scale + scalers[1].mean
        e = labels - base[:70]
        np.testing.assert_allclose(raw[:, 0], e[1:31], atol=1e-14, rtol=0)
        np.testing.assert_allclose(raw[:, 1], e[1:31] - e[:30], atol=1e-14, rtol=0)
        np.testing.assert_allclose(x[:, 22], 59 / 179, atol=0, rtol=0)

    def test_target_and_later_labels_do_not_enter_origin_window(self):
        base, features, labels, scalers = example()
        pairs = np.array([[31, 60]])
        a = windows(base, features, labels, 70, pairs, *scalers, "H")
        changed = labels.copy()
        changed[31:] += 1e6
        b = windows(base, features, changed, 70, pairs, *scalers, "H")
        torch.testing.assert_close(a, b, atol=0, rtol=0)

    def test_masked_control_is_invariant_to_all_observed_values(self):
        base, features, labels, scalers = example()
        pairs = np.array([[70, 249]])
        a = windows(base, features, labels, 70, pairs, *scalers, "C")
        b = windows(base, features, labels + 1000, 70, pairs, *scalers, "C")
        torch.testing.assert_close(a, b, atol=0, rtol=0)
        self.assertEqual(torch.count_nonzero(a[:, :, 20:22]).item(), 0)

    def test_extra_labels_and_invalid_queries_are_rejected(self):
        base, features, labels, scalers = example()
        for pair in ([[30, 31]], [[71, 80]], [[70, 69]], [[70, 250]], [[70.0, 80.0]]):
            with self.subTest(pair=pair), self.assertRaises(ValueError):
                windows(base, features, labels, 70, np.array(pair), *scalers, "H")
        with self.assertRaises(ValueError):
            windows(
                base,
                features,
                np.vstack([labels, labels[-1]]),
                70,
                np.array([[70, 80]]),
                *scalers,
                "H",
            )

    def test_scalers_use_exact_prefix_and_fixed_history_floor(self):
        base, features, labels, scalers = example()
        history = history_values(base[:70], labels)[1:]
        np.testing.assert_allclose(scalers[1].mean, history.mean(axis=0))
        np.testing.assert_allclose(
            scalers[1].scale, np.maximum(history.std(axis=0), HISTORY_FLOOR)
        )
        with self.assertRaises(ValueError):
            training_scalers(base, features, labels, 70)

    def test_zero_head_and_parameter_count(self):
        base, features, labels, scalers = example()
        model = new_model(0)
        self.assertEqual(sum(p.numel() for p in model.parameters()), 7569)
        for strategy in ("C", "H"):
            output = predict(
                model, base, features, labels, 70, *scalers, strategy, chunk=53
            )
            np.testing.assert_array_equal(output[29:], base[29:])

    def test_history_gradient_reaches_nonzero_cell_and_output(self):
        base, features, labels, scalers = example()
        model = new_model(0)
        with torch.no_grad():
            model.output.weight.fill_(0.01)
        x = windows(
            base, features, labels, 70, np.array([[70, 90]]), *scalers, "H"
        ).requires_grad_(True)
        model(x).square().mean().backward()
        self.assertGreater(x.grad[:, :, 20:22].abs().sum().item(), 0)
        self.assertGreater(model.gates.weight.grad.abs().sum().item(), 0)
        self.assertGreater(model.output.weight.grad.abs().sum().item(), 0)

    def test_nonzero_history_changes_output_and_batching_does_not(self):
        base, features, labels, scalers = example()
        model = new_model(1)
        with torch.no_grad():
            model.output.weight.fill_(0.01)
        a = predict(model, base, features, labels, 70, *scalers, "H", chunk=128)
        b = predict(model, base, features, labels, 70, *scalers, "H", chunk=53)
        np.testing.assert_allclose(a[29:], b[29:], atol=1e-10, rtol=0)
        changed = predict(model, base, features, labels + 1, 70, *scalers, "H")
        self.assertGreater(np.max(abs(a[70:] - changed[70:])), 1e-6)

    def test_anchor_bias_is_fixed_throughout_forecast(self):
        base, features, labels, scalers = example()
        result = predict(None, base, features, labels, 70, *scalers, "A")
        expected = np.broadcast_to(labels[69] - base[69], (180, 4))
        np.testing.assert_allclose(
            result[70:] - base[70:], expected, rtol=0, atol=2e-14
        )
        np.testing.assert_array_equal(result[30], base[30])


if __name__ == "__main__":
    unittest.main()
