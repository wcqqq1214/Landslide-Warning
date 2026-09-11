"""Synthetic time, isolation, spatial, state, gradient and budget regressions."""

import unittest
from unittest.mock import patch
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd
import torch

from physics_guided.features import Scaler
from physics_guided_sequence.core import Budget, initial_models, make_sequence
from physics_guided_sequence.reference import inputs, predict
from physics_guided_sequence.run import compare_csv


def example(origin=45, horizon=60):
    day = np.arange(origin + horizon + 5, dtype=float)
    base = day[:, None] * np.arange(1, 5)[None, :] / 7
    features = np.sin(day[:, None, None] / 11 + np.arange(80).reshape(1, 20, 4))
    labels = base[:origin] + np.cos(day[:origin, None] / 9 + np.arange(4)[None, :])
    physical = Scaler(np.full((20, 4), 0.13), np.full((20, 4), 1.2), np.ones((20, 4)))
    history = Scaler(np.full((2, 4), 0.07), np.full((2, 4), 0.8), np.ones((2, 4)))
    return base, features, labels, origin, horizon, physical, history


class SequenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)

    def test_independent_calendar_and_normalization_enumeration(self):
        args = example()
        sequence = make_sequence(*args)
        expected = inputs(*args)
        for value, reference in zip(
            (sequence.encoder, sequence.decoder, sequence.baseline), expected
        ):
            np.testing.assert_array_equal(value.numpy(), reference)
        self.assertLess(sequence.encoder[0, -1, 22, 0, 0], 0)
        self.assertEqual(sequence.decoder[0, 0, 22, 0, 0], 0)
        self.assertEqual(torch.count_nonzero(sequence.decoder[:, :, 20:22]), 0)

    def test_exact_observation_prefix_rejects_future_labels(self):
        args = list(example())
        args[2] = np.vstack([args[2], np.full((1, 4), np.nan)])
        with self.assertRaises(ValueError):
            make_sequence(*args)

    def test_unused_future_physical_values_do_not_change_short_inputs(self):
        args = list(example(horizon=8))
        expected = make_sequence(*args)
        args[0][args[3] + args[4] :] = np.nan
        args[1][args[3] + args[4] :] = np.nan
        actual = make_sequence(*args)
        for key in ("encoder", "decoder", "baseline"):
            self.assertTrue(torch.equal(getattr(actual, key), getattr(expected, key)))

    def test_difference_uses_only_the_registered_extra_left_day(self):
        args = list(example())
        before = make_sequence(*args)
        args[2][args[3] - 31, 0] += 8
        after = make_sequence(*args)
        difference = after.encoder - before.encoder
        self.assertAlmostEqual(difference[0, 0, 21, 0, 0].item(), -10)
        self.assertEqual(torch.count_nonzero(difference).item(), 1)

    def test_input_bounds_and_required_nonfinite_values_are_rejected(self):
        for index, value in ((3, 30), (4, 0), (4, 181)):
            args = list(example())
            args[index] = value
            with self.assertRaises(ValueError):
                make_sequence(*args)
        args = list(example())
        args[1][args[3], 0, 0] = np.nan
        with self.assertRaises(ValueError):
            make_sequence(*args)

    def test_input_construction_does_not_fit_scalers(self):
        with patch.object(
            Scaler, "fit", side_effect=AssertionError("forbidden scaler fit")
        ):
            make_sequence(*example())

    def test_zero_readout_exactly_recovers_baseline_with_original_parameter_count(self):
        sequence = make_sequence(*example(horizon=8))
        zero, _ = initial_models()
        self.assertEqual(sum(p.numel() for p in zero.parameters()), 7569)
        mean = sequence.baseline + zero(sequence.encoder, sequence.decoder)
        self.assertTrue(torch.equal(mean, sequence.baseline))

    def test_numpy_reference_matches_two_batch_elements(self):
        sequence = make_sequence(*example(horizon=8))
        _, probe = initial_models()
        encoder = sequence.encoder.repeat(2, 1, 1, 1, 1)
        encoder[1, :, :20] += 0.2
        decoder = sequence.decoder.repeat(2, 1, 1, 1, 1)
        actual = probe(encoder, decoder).detach().numpy()
        reference = predict(probe.state_dict(), encoder.numpy(), decoder.numpy())
        np.testing.assert_allclose(actual, reference, atol=1e-12, rtol=1e-12)

    def test_spatial_kernel_uses_neighbor_point_axis(self):
        zero, _ = initial_models()
        with torch.no_grad():
            for p in zero.parameters():
                p.zero_()
            zero.gates.weight[48, 0, 0, 0] = 0.2
            zero.output.weight[0, 0, 0, 0] = 0.1
        encoder = torch.zeros((1, 30, 23, 1, 4), dtype=torch.float64)
        future = torch.zeros((1, 1, 23, 1, 4), dtype=torch.float64)
        future[0, 0, 0, 0, 0] = 1
        output = zero(encoder, future)[0, 0]
        self.assertGreater(output[1], 0)
        self.assertEqual(torch.count_nonzero(output).item(), 1)

    def test_segmented_decode_keeps_state_and_autograd_history(self):
        sequence = make_sequence(*example(horizon=8))
        _, probe = initial_models()
        encoder = sequence.encoder.clone().requires_grad_(True)
        state = probe.encode(encoder)
        first, state = probe.decode(sequence.decoder[:, :3], state)
        last, _ = probe.decode(sequence.decoder[:, 3:], state)
        joined = torch.cat([first, last], dim=1)
        self.assertTrue(torch.equal(joined, probe(encoder, sequence.decoder)))
        (history_grad,) = torch.autograd.grad(joined[0, -1].sum(), encoder)
        self.assertGreater(history_grad[:, :, 20:22].norm(), 0)

    def test_forecast_prefix_is_isolated_from_later_physical_inputs(self):
        sequence = make_sequence(*example(horizon=8))
        _, probe = initial_models()
        full = probe(sequence.encoder, sequence.decoder)
        future = sequence.decoder.clone()
        future[:, 3:, :20] += 100
        changed = probe(sequence.encoder, future)
        self.assertTrue(torch.equal(full[:, :3], changed[:, :3]))
        self.assertTrue(
            torch.equal(full[:, :3], probe(sequence.encoder, sequence.decoder[:, :3]))
        )

    def test_history_directional_gradient_matches_finite_difference(self):
        sequence = make_sequence(*example(horizon=1))
        _, probe = initial_models()
        encoder = sequence.encoder.clone().requires_grad_(True)
        value = probe(encoder, sequence.decoder)[0, 0, 0]
        (gradient,) = torch.autograd.grad(value, encoder)
        direction = torch.zeros_like(encoder)
        direction[:, -1, 20, 0, 0] = 1
        epsilon = 1e-5
        with torch.no_grad():
            plus = probe(encoder + epsilon * direction, sequence.decoder)[0, 0, 0]
            minus = probe(encoder - epsilon * direction, sequence.decoder)[0, 0, 0]
        self.assertAlmostEqual(
            float((plus - minus) / (2 * epsilon)),
            float((gradient * direction).sum()),
            places=8,
        )

    def test_zero_head_blocks_history_gradient_until_readout_changes(self):
        sequence = make_sequence(*example(horizon=1))
        zero, probe = initial_models()
        encoder = sequence.encoder.clone().requires_grad_(True)
        (grad,) = torch.autograd.grad(zero(encoder, sequence.decoder).sum(), encoder)
        self.assertEqual(torch.count_nonzero(grad), 0)
        (grad,) = torch.autograd.grad(probe(encoder, sequence.decoder).sum(), encoder)
        self.assertGreater(grad[:, :, 20:22].norm(), 0)

    def test_budget_rejects_cell_before_exceeding_limit(self):
        sequence = make_sequence(*example(horizon=1))
        zero, _ = initial_models()
        budget = Budget(
            dict(
                model_calls=1,
                sequence_segments=2,
                cell_sample_steps=29,
                readout_sample_steps=1,
                gradient_calls=0,
                reference_sample_steps=0,
            )
        )
        with self.assertRaises(RuntimeError):
            zero(sequence.encoder, sequence.decoder, budget)
        self.assertEqual(budget.counts["cell_sample_steps"], 29)

    def test_csv_replay_preserves_and_checks_tiny_nonzero_gradients(self):
        expected = pd.DataFrame(dict(name=["a", "b"], gradient=[1e-200, -2e-49]))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gradients.csv"
            expected.to_csv(path, index=False)
            compare_csv(expected, path)
            altered = expected.copy()
            altered.loc[0, "gradient"] = 0.0
            altered.to_csv(path, index=False)
            with self.assertRaises(AssertionError):
                compare_csv(expected, path)


if __name__ == "__main__":
    unittest.main()
