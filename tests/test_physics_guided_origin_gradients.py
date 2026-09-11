"""Synthetic analytic gradients, objective tradeoffs, and immutable probes."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import torch

from physics_guided_forecast_error.artifacts import load_observations
from physics_guided_history_learning.core import new_model
from physics_guided_origin_learning.core import Samples
from physics_guided_origin_gradients.core import (
    Budget,
    block_gradients,
    descent_direction,
    finite_differences,
    geometry,
    layout,
    tables,
)
from physics_guided_origin_gradients.verify import replay_gradients
from physics_guided_origin_gradients.workflow import (
    check_model_state,
    samples_at,
    specification,
)


class Probe(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.gates = torch.nn.Linear(2, 2, bias=False, dtype=torch.float64)
        self.output = torch.nn.Linear(2, 4, dtype=torch.float64)
        with torch.no_grad():
            self.gates.weight.copy_(torch.eye(2))
            self.output.weight.fill_(0.1)
            self.output.bias.zero_()

    def forward(self, x):
        return self.output(self.gates(x))


def fixture():
    x = torch.arange(14, dtype=torch.float64).reshape(7, 2) / 10
    base = torch.arange(28, dtype=torch.float64).reshape(7, 4) / 5
    target = base + torch.tensor([1.0, -2.0, 3.0, -4.0], dtype=torch.float64)
    weights = torch.tensor([0.1, 0.4, 0.05, 0.1, 0.1, 0.05, 0.2], dtype=torch.float64)
    return Samples(x, base, target, weights, np.array([0, 0, 1, 1, 1, 1, 1]))


class OriginGradientTests(unittest.TestCase):
    def test_linear_gradients_match_closed_form(self):
        model, samples = Probe(), fixture()
        loss, gradient = block_gradients(model, samples, 2, Budget(10, 0))
        with torch.no_grad():
            for block in (0, 1):
                ids = samples.blocks == block
                x = samples.x[ids]
                z = x @ model.gates.weight.T
                error = (
                    z @ model.output.weight.T
                    + model.output.bias
                    + samples.base[ids]
                    - samples.target[ids]
                )
                delta = error * samples.weights[ids, None] / 10000
                expected = torch.cat(
                    [
                        ((delta @ model.output.weight).T @ x).flatten(),
                        (delta.T @ z).flatten(),
                        delta.sum(0),
                    ]
                ).numpy()
                np.testing.assert_allclose(
                    gradient[block], expected, atol=1e-16, rtol=0
                )
                self.assertAlmostEqual(
                    loss[block],
                    float((error.square() * samples.weights[ids, None]).sum() / 20000),
                    places=16,
                )

    def test_different_batches_and_backward_agree(self):
        model, samples = Probe(), fixture()
        a = block_gradients(model, samples, 2, Budget(10, 0))
        b = replay_gradients(model, samples, 3, Budget(10, 0))
        for x, y in zip(a, b):
            np.testing.assert_allclose(x, y, atol=1e-16, rtol=0)
        self.assertTrue(all(p.grad is None for p in model.parameters()))

    def test_original_half_weighted_total_matches_direct_autograd(self):
        model, samples = Probe(), fixture()
        values, gradients = block_gradients(model, samples, 2, Budget(10, 0))
        loss = (
            ((model(samples.x) + samples.base - samples.target) / 100).square().mean(1)
            * samples.weights
        ).sum()
        actual = torch.cat(
            [g.flatten() for g in torch.autograd.grad(loss, tuple(model.parameters()))]
        ).numpy()
        self.assertAlmostEqual(float(loss.detach()), values.mean(), places=16)
        np.testing.assert_allclose(actual, gradients.mean(0), atol=1e-16, rtol=0)

    def test_duplicate_rows_with_split_weights_preserve_gradients(self):
        model, a = Probe(), fixture()
        b = Samples(
            a.x.repeat_interleave(2, 0),
            a.base.repeat_interleave(2, 0),
            a.target.repeat_interleave(2, 0),
            a.weights.repeat_interleave(2) / 2,
            np.repeat(a.blocks, 2),
        )
        ga = block_gradients(model, a, 3, Budget(20, 0))
        gb = block_gradients(model, b, 3, Budget(20, 0))
        for x, y in zip(ga, gb):
            np.testing.assert_allclose(x, y, atol=1e-16, rtol=0)

    def test_conflict_and_magnitude_are_distinct(self):
        spec = specification()
        a = geometry(np.array([1.0, 0]), np.array([-20.0, 1.0]), spec)
        self.assertTrue(
            a["conflict"] and a["magnitude_imbalance"] and a["anchor_increases"]
        )
        self.assertFalse(a["paired_increases"])
        b = geometry(np.array([1.0, 0]), np.array([20.0, 1.0]), spec)
        self.assertFalse(b["conflict"] or b["anchor_increases"])
        self.assertTrue(b["magnitude_imbalance"])

    def test_zero_and_cancelled_gradients_are_explicit(self):
        spec = specification()
        row = geometry(np.zeros(2), np.array([1.0, 2.0]), spec)
        self.assertTrue(row["anchor_zero"])
        self.assertTrue(np.isnan(row["cosine"]) and np.isnan(row["norm_ratio"]))
        row = geometry(np.ones(2), -np.ones(2), spec)
        self.assertTrue(row["conflict"] and row["total_zero"])
        self.assertTrue(np.isnan(row["anchor_directional"]))
        self.assertIsNone(descent_direction(np.array([np.ones(2), -np.ones(2)])))

    def test_finite_difference_and_gradient_probe_preserve_model(self):
        model, samples = Probe(), fixture()
        snapshot = copy.deepcopy(model.state_dict())
        _, gradients = block_gradients(model, samples, 3, Budget(10, 0))
        direction = descent_direction(gradients)
        steps = [1e-4, 1e-5]
        values = finite_differences(model, samples, direction, steps, 3, Budget(0, 20))
        for step, v in zip(steps, values):
            np.testing.assert_allclose(
                (v[1] - v[0]) / (2 * step), gradients @ direction, atol=1e-9, rtol=1e-4
            )
        check_model_state(model, snapshot)
        self.assertTrue(all(p.grad is None for p in model.parameters()))

    def test_zero_direction_is_skipped_without_forward(self):
        budget = Budget(0, 0)
        values = finite_differences(Probe(), fixture(), None, [1e-4, 1e-5], 3, budget)
        self.assertTrue(np.isnan(values).all())
        self.assertEqual(budget.record()["forward_calls"], 0)

    def test_frozen_block_weights_and_bad_batches_rejected(self):
        samples = fixture()
        for batch in (0, -1, 1.5):
            with self.assertRaises(ValueError):
                block_gradients(Probe(), samples, batch, Budget(20, 0))
        samples.weights[0] += 0.01
        with self.assertRaises(ValueError):
            block_gradients(Probe(), samples, 3, Budget(20, 0))

    def test_budget_stops_before_extra_forward(self):
        model, samples, budget = Probe(), fixture(), Budget(1, 0)
        with patch.object(model, "forward", wraps=model.forward) as call:
            with self.assertRaises(RuntimeError):
                block_gradients(model, samples, 1, budget)
            self.assertEqual(call.call_count, 1)
        self.assertEqual(budget.backward_calls, 1)

    def test_history_network_zero_head_has_zero_encoder_gradient(self):
        model = new_model(0)
        x = torch.linspace(-0.2, 0.2, 2 * 30 * 23 * 4).double().reshape(2, 30, 23, 1, 4)
        samples = Samples(
            x,
            torch.zeros((2, 4), dtype=torch.float64),
            torch.ones((2, 4), dtype=torch.float64),
            torch.full((2,), 0.5, dtype=torch.float64),
            np.array([0, 1]),
        )
        _, gradients = block_gradients(model, samples, 1, Budget(2, 0))
        names = layout(model)
        self.assertEqual(names[-1]["stop"], 7569)
        encoder_end = next(v["stop"] for v in names if v["name"] == "gates.bias")
        np.testing.assert_array_equal(gradients[:, :encoder_end], 0)
        self.assertGreater(np.linalg.norm(gradients[:, encoder_end:]), 0)

    def test_future_rows_do_not_enter_observation_parser(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.csv"
            import pandas as pd

            frame = pd.DataFrame(
                {
                    "Date": pd.date_range("2016-07-01", periods=32),
                    "Rainfall/mm": 0.0,
                    "RWL/m": 150.0,
                    **{p + "/mm": 1.0 for p in ("ATU1", "ATU5", "MJ3", "MJ1")},
                }
            )
            frame.iloc[-1, 3:] = np.nan
            frame.to_csv(path, index=False)
            _, _, labels = load_observations(path, 31)
            self.assertEqual(labels.shape, (31, 4))
        with self.assertRaises(ValueError):
            samples_at(Path("/unused"), 792)

    def test_tables_keep_all_groups_and_finite_checks(self):
        model, samples = Probe(), fixture()
        losses, gradients = block_gradients(model, samples, 3, Budget(10, 0))
        spec = specification()
        diff = finite_differences(
            model,
            samples,
            descent_direction(gradients),
            spec["finite_steps"],
            3,
            Budget(0, 20),
        )
        data = {
            342: dict(
                keys=np.array([[0, 0, 100]]),
                losses=losses[None],
                gradients=gradients[None],
                finite=diff[None],
            )
        }
        rows, checks = tables(data, layout(model), spec)
        self.assertEqual(rows.parameter_group.tolist(), ["all", "gates", "output"])
        self.assertEqual(len(checks), 4)
        self.assertTrue(checks.passed.all())


if __name__ == "__main__":
    unittest.main()
