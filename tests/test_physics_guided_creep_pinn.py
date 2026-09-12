from pathlib import Path
import sys
import unittest

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from physics_guided_pinn.creep import CreepPinn, linear_memory, memory_states
from physics_guided_pinn.equations import Coefficients, residuals


class CreepPinnTests(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(23)

    def test_scan_matches_sequential_values_and_full_history_gradients(self):
        x = torch.randn(137, 4, dtype=torch.float64, requires_grad=True)
        a = torch.tensor([0.0, 0.4, 0.999, 1.0], dtype=torch.float64)
        state, values = torch.zeros(4, dtype=torch.float64), []
        for row in x:
            state = a * state + row
            values.append(state)
        expected = torch.stack(values)
        actual = linear_memory(x, a)
        torch.testing.assert_close(actual, expected, rtol=1e-12, atol=1e-12)
        g1 = torch.autograd.grad(actual[-1].sum(), x, retain_graph=True)[0]
        g2 = torch.autograd.grad(expected[-1].sum(), x)[0]
        torch.testing.assert_close(g1, g2, rtol=1e-12, atol=1e-12)
        self.assertGreater(float(g1[0, 2]), 0.8)

    def test_original_memory_equations_and_gradient_check(self):
        theta = torch.zeros(54, dtype=torch.float64)
        c = Coefficients.from_reference(
            theta, torch.ones(4), torch.eye(4), torch.eye(4)
        )
        dp = torch.rand(12, 4, dtype=torch.float64, requires_grad=True) * 0.01
        b = torch.randn(12, 4, dtype=torch.float64, requires_grad=True) * 0.01
        e = torch.rand_like(b)
        states = memory_states(dp, e, b, c)
        db = torch.diff(b, dim=0, prepend=torch.zeros_like(b[:1]))
        r = residuals(states[:-1], states[1:], torch.zeros_like(b), e, db, c)
        for key in (
            "motion",
            "basal_memory",
            "contact_memory",
            "bulk_memory",
            "background",
        ):
            torch.testing.assert_close(r[key], torch.zeros_like(b), rtol=0, atol=1e-12)
        self.assertTrue(
            torch.autograd.gradcheck(lambda p, bg: memory_states(p, e, bg, c), (dp, b))
        )
        # Arbitrary positive increments do not imply force complementarity.
        self.assertGreater(float(r["complementarity"].detach().abs().max()), 0)

    def test_initialization_and_fit_only_center(self):
        model = CreepPinn(8.829636906719527)
        self.assertEqual(sum(p.numel() for p in model.parameters()), 514)
        x = torch.randn(40, 4, 4, dtype=torch.float64)
        rate = torch.rand(40, 4, dtype=torch.float64)
        transient = torch.zeros_like(rate)
        bg, _, _ = model.background(x, rate, transient, fit_days=25)
        torch.testing.assert_close(bg[1:], rate[1:].cumsum(0))
        torch.testing.assert_close(
            model.sigma(x[:, 0, :3]), torch.full_like(rate, 8.829636906719527)
        )
        with torch.no_grad():
            model.creep_net[-1].weight.fill_(0.2)
        m, a, center = model.creep_response(x, fit_days=25)
        changed = x.clone()
        changed[25:] += 50
        m2, _, center2 = model.creep_response(changed, fit_days=25)
        torch.testing.assert_close(center, center2)
        torch.testing.assert_close(m[:25], m2[:25])
        frozen, _, _ = model.creep_response(x[25:], center=center.detach())
        torch.testing.assert_close(frozen, m[25:])
        self.assertTrue(bool(((m >= 0.5) & (m <= 2)).all()))
        torch.testing.assert_close(
            a[:25].mean(0), torch.zeros(4, dtype=torch.float64), atol=1e-15, rtol=0
        )
        with self.assertRaises(ValueError):
            model.creep_response(x)

    def test_signed_reference_step_is_preserved_but_rejected_in_neural_path(self):
        c = Coefficients.from_reference(
            torch.zeros(54), torch.ones(4), torch.eye(4), torch.eye(4)
        )
        dp = torch.full((2, 4), 0.1, dtype=torch.float64)
        dp[1, 0] = -1e-9
        zero = torch.zeros_like(dp)
        with self.assertRaises(ValueError):
            memory_states(dp, zero, zero, c)
        states = memory_states(dp, zero, zero, c, reference_slip_tolerance=1e-8)
        self.assertLess(float(states[-1, 4]), float(states[-2, 4]))
        self.assertAlmostEqual(float(states[-1, 4]), 0.1 - 1e-9, places=15)


if __name__ == "__main__":
    unittest.main()
