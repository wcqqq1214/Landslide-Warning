"""Focused objective and information-boundary checks, zero optimizer updates."""

import io
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from transformer_regularization.core import (
    OLD,
    TrajectoryModel,
    objective,
    predict,
    read_forcing,
    read_labels,
    reload_model,
    spec,
    training_inputs,
)
from sequence_conditional.independent import numpy_forward


class RegularizationContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)
        cls.cfg = spec()

    def test_exact_zero_loss_and_gradient(self):
        q = (
            torch.linspace(-1, 1, 44, dtype=torch.float64)
            .reshape(11, 4)
            .requires_grad_()
        )
        r = torch.sin(q.detach())
        old = (q - r).square().mean()
        new, _, _ = objective(q, r, 0)
        torch.testing.assert_close(old, new, atol=0, rtol=0)
        torch.testing.assert_close(
            torch.autograd.grad(old, q)[0],
            torch.autograd.grad(new, q)[0],
            atol=0,
            rtol=0,
        )

    def test_penalty_identity_and_analytic_gradient(self):
        q = (
            torch.linspace(-2, 2, 52, dtype=torch.float64)
            .reshape(13, 4)
            .requires_grad_()
        )
        r = q.detach().cos()
        loss, _, _ = objective(q, r, 1)
        other = 2 * (q - r / 2).square().mean() + r.square().mean() / 2
        torch.testing.assert_close(loss, other, atol=2e-15, rtol=0)
        torch.testing.assert_close(
            torch.autograd.grad(loss, q)[0],
            (4 * q - 2 * r) / q.numel(),
            atol=2e-16,
            rtol=0,
        )
        optimum = (r / 2).clone().requires_grad_()
        torch.testing.assert_close(
            torch.autograd.grad(objective(optimum, r, 1)[0], optimum)[0],
            torch.zeros_like(optimum),
            atol=0,
            rtol=0,
        )

    def test_future_poison_does_not_change_training(self):
        rng = np.random.default_rng(4)
        data = dict(x=rng.normal(size=(105, 22)), mean=rng.normal(size=(105, 4)))
        y = rng.normal(size=(80, 4)).cumsum(0)
        altered = {k: v.copy() for k, v in data.items()}
        for v in altered.values():
            v[80:] = np.nan
        a, x, t = training_inputs(data, y, self.cfg)
        b, x2, t2 = training_inputs(altered, y, self.cfg)
        self.assertEqual(a.state, b.state)
        torch.testing.assert_close(x, x2, atol=0, rtol=0)
        torch.testing.assert_close(t, t2, atol=0, rtol=0)
        frame = pd.DataFrame(
            dict(
                Date=pd.date_range("2016-07-01", periods=105),
                **{"Rainfall/mm": 2, "RWL/m": 160},
            )
        )
        for point in self.cfg["points"]:
            frame[point + "/mm"] = [1.0] * 80 + ["unreleased"] * 25
        csv = frame.to_csv(index=False)
        self.assertEqual(read_labels(io.StringIO(csv), 80).shape, (80, 4))
        self.assertEqual(read_forcing(io.StringIO(csv), 105)[0].shape, (105, 2))

    def test_causality_fallback_reload_and_numpy(self):
        rng = np.random.default_rng(7)
        data = dict(x=rng.normal(size=(95, 22)), mean=rng.normal(size=(95, 4)))
        y = rng.normal(size=(60, 4)).cumsum(0)
        scale, _, _ = training_inputs(data, y, self.cfg)
        model = TrajectoryModel(self.cfg, 0, OLD)
        np.testing.assert_array_equal(
            predict(model, scale, OLD, data["x"], data["mean"]), data["mean"]
        )
        with torch.no_grad():
            model.head.weight.normal_(0, 0.03)
        full = predict(model, scale, OLD, data["x"], data["mean"])
        changed = data["x"].copy()
        changed[60:] += 3
        alternate = predict(model, scale, OLD, changed, data["mean"])
        np.testing.assert_allclose(full[:60], alternate[:60], atol=1e-10, rtol=0)
        self.assertGreater(np.max(abs(full[60:] - alternate[60:])), 1e-6)
        ck = dict(state_dict=model.state_dict(), scaling=scale.state, seed=0, arm=OLD)
        np.testing.assert_allclose(
            numpy_forward(ck, data["x"], data["mean"]), full, atol=1e-9, rtol=0
        )
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "model.pt"
            torch.save(ck, p)
            restored, sc, _ = reload_model(p, self.cfg)
            np.testing.assert_array_equal(
                predict(restored, sc, OLD, data["x"], data["mean"]), full
            )

    def test_invalid_loss_inputs_fail(self):
        x = torch.ones(3, 4, dtype=torch.float64)
        with self.assertRaises(ValueError):
            objective(x, x, -1)
        with self.assertRaises(ValueError):
            objective(x, x[:2], 1)
        with self.assertRaises(ArithmeticError):
            objective(x * float("nan"), x, 1)


if __name__ == "__main__":
    unittest.main()
