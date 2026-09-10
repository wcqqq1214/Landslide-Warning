from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from physics_guided_shared_mechanics.core import (
    Budget,
    CountedLibrary,
    RateInputs,
    RateReplay,
    objective,
)


def artificial_day(old, end, solver, t, previous):
    """Nonphysical linear recurrence used only to test wrapper/graph behavior."""
    state = torch.cat([0.75 * old[:4] + 0.2 * end, old[4:20], end])
    solver.records.append(np.zeros(64, dtype=np.int32))
    return state, torch.tensor(0), torch.tensor([0, 0, 0, 1, 0], dtype=torch.float64)


class SharedMechanicsTests(unittest.TestCase):
    def setUp(self):
        torch.set_num_threads(1)
        torch.manual_seed(17)
        n, h = 42, 36
        self.inputs = RateInputs(
            torch.randn(n, 12, dtype=torch.float64),
            torch.full((n, 4), 0.2, dtype=torch.float64),
            torch.arange(n, dtype=torch.float64)[:, None].repeat(1, 4) * 0.2,
            torch.eye(4, dtype=torch.float64),
            torch.zeros(4, dtype=torch.float64),
            h,
        )
        self.model = RateReplay()
        with torch.no_grad():
            self.model.rate_net[-1].weight.normal_(0, 0.02)
        self.solver = SimpleNamespace(force=np.zeros((n, 4)), elastic=np.zeros((n, 4)))
        self.solver.begin = lambda: setattr(self.solver, "records", [])
        self.budget = Budget({"neural_evaluations": 100, "trajectories": 100})
        self.day_patch = patch(
            "physics_guided_shared_mechanics.core.Day.apply", side_effect=artificial_day
        )
        self.day_patch.start()
        self.addCleanup(self.day_patch.stop)

    def predict(self, inputs=None, **kwargs):
        return self.model(inputs or self.inputs, self.solver, self.budget, **kwargs)

    def test_shared_prefix_and_nonzero_parameter_gradient(self):
        self.assertEqual(sum(p.numel() for p in self.model.parameters()), 548)
        full = self.predict()
        short = self.predict(days=self.inputs.h)
        with torch.no_grad():
            inference = self.predict(days=self.inputs.h)
        torch.testing.assert_close(full["mean"][: self.inputs.h], short["mean"])
        torch.testing.assert_close(inference["mean"], short["mean"], rtol=0, atol=0)
        self.assertFalse(torch.count_nonzero(short["state"][0]))
        loss, _ = objective(self.inputs, short, short["mean"].detach() + 1)
        gradients = torch.autograd.grad(loss, tuple(self.model.parameters()))
        self.assertTrue(all(torch.isfinite(g).all() for g in gradients))
        self.assertGreater(float(gradients[0].norm()), 0)

    def test_complete_early_multiplier_derivative_matches_finite_difference(self):
        n = self.inputs.h
        gamma = torch.full((n, 4), 1.1, dtype=torch.float64, requires_grad=True)
        out = self.predict(days=n, multiplier=gamma)
        gradient = torch.autograd.grad(out["mean"][-1].sum(), gamma)[0]
        self.assertGreater(float(gradient[1].norm()), 0)
        step, direction = 1e-6, torch.full((4,), 0.5, dtype=torch.float64)
        values = []
        for sign in (1, -1):
            altered = gamma.detach().clone()
            altered[1] += sign * step * direction
            values.append(
                float(self.predict(days=n, multiplier=altered)["mean"][-1].sum())
            )
        self.assertAlmostEqual(
            float(gradient[1] @ direction),
            (values[0] - values[1]) / (2 * step),
            places=7,
        )
        self.assertEqual(self.budget.counts["neural_evaluations"], 0)

    def test_future_values_and_gradient_are_isolated(self):
        changed = replace(
            self.inputs,
            x=self.inputs.x.clone(),
            rate=self.inputs.rate.clone(),
            background=self.inputs.background.clone(),
        )
        for value in (changed.x, changed.rate, changed.background):
            value[self.inputs.h :] += 100
        a, b = (
            self.predict(days=self.inputs.h),
            self.predict(changed, days=self.inputs.h),
        )
        torch.testing.assert_close(a["mean"], b["mean"], rtol=0, atol=0)
        self.inputs.x.requires_grad_(True)
        out = self.predict(days=self.inputs.h)
        loss, _ = objective(self.inputs, out, out["mean"].detach() + 1)
        gradient = torch.autograd.grad(loss, self.inputs.x)[0]
        self.assertGreater(float(gradient[: self.inputs.h].norm()), 0)
        self.assertFalse(torch.count_nonzero(gradient[self.inputs.h :]))

    def test_exact_labels_and_finite_inputs_required(self):
        out = self.predict(days=self.inputs.h)
        with self.assertRaises(ValueError):
            objective(self.inputs, out, torch.zeros(42, 4))
        with self.assertRaises(ValueError):
            objective(self.inputs, out, torch.full((self.inputs.h, 4), float("nan")))
        with self.assertRaises(ValueError):
            replace(self.inputs, x=torch.zeros(42, 13, dtype=torch.float64))
        with self.assertRaises(ValueError):
            self.predict(multiplier=torch.full((42, 4), 2.01, dtype=torch.float64))

    def test_call_budgets_reject_before_native_or_neural_execution(self):
        calls = []
        library = SimpleNamespace(
            day_forward=lambda: calls.append("forward"),
            day_backward=lambda: calls.append("backward"),
        )
        counted = CountedLibrary(
            library, Budget({"day_forwards": 0, "day_backwards": 0})
        )
        for function in (counted.day_forward, counted.day_backward):
            with self.assertRaises(RuntimeError):
                function()
        self.assertEqual(calls, [])
        with patch.object(
            self.model.rate_net, "forward", side_effect=AssertionError("must not run")
        ):
            with self.assertRaises(RuntimeError):
                self.model(
                    self.inputs,
                    self.solver,
                    Budget({"neural_evaluations": 0, "trajectories": 0}),
                )


if __name__ == "__main__":
    unittest.main()
