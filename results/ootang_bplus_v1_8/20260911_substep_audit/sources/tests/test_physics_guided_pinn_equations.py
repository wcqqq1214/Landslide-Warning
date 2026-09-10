from pathlib import Path
import sys
import unittest

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from physics_guided_pinn.equations import Coefficients, observe, residuals


class PinnEquationTests(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(4)
        theta = torch.zeros(54, dtype=torch.float64)
        theta[8:12] = torch.log(torch.tensor([0.3, 0.4, 0.7, 1.0], dtype=torch.float64))
        theta[12:16] = torch.log(
            torch.tensor([0.1, 0.2, 0.4, 0.3], dtype=torch.float64)
        )
        theta[16:20] = torch.log(
            torch.tensor([30.0, 45.0, 60.0, 90.0], dtype=torch.float64)
        )
        theta[20:24] = torch.log(
            torch.tensor([1.0, 2.0, 4.0, 8.0], dtype=torch.float64)
        )
        theta[42:44] = torch.log(torch.tensor([20.0, 50.0], dtype=torch.float64))
        matrix = torch.randn(4, 4, dtype=torch.float64)
        other = torch.randn(4, 4, dtype=torch.float64)
        self.length = torch.tensor([1.0, 2.0, 3.0, 4.0], dtype=torch.float64)
        self.c = Coefficients.from_reference(
            theta, self.length, matrix @ matrix.T, other @ other.T
        )

    def test_parameter_storage_and_length_weighting(self):
        torch.testing.assert_close(
            self.c.eta, torch.tensor([0.3, 0.8, 2.1, 4.0], dtype=torch.float64)
        )
        torch.testing.assert_close(
            self.c.hardening, torch.tensor([0.1, 0.4, 1.2, 1.2], dtype=torch.float64)
        )
        torch.testing.assert_close(
            self.c.tau_rest, torch.tensor([30.0, 45.0, 60.0, 90.0], dtype=torch.float64)
        )
        self.assertAlmostEqual(float(self.c.tau_contact), 20.0)
        self.assertAlmostEqual(float(self.c.tau_bulk), 50.0)
        with self.assertRaises(ValueError):
            Coefficients.from_reference(
                torch.zeros(47), self.length, torch.eye(4), torch.eye(4)
            )

    def test_feasible_substeps_and_original_active_set_gap_identity(self):
        c, dt = self.c, 1 / 64
        previous = torch.randn(32, 24, dtype=torch.float64)
        s0, p0, rb0, rc0, re0, b0 = previous.split(4, dim=-1)
        elastic = torch.randn(32, 4, dtype=torch.float64)
        db = torch.randn(32, 4, dtype=torch.float64) * 0.01
        dp = torch.rand(32, 4, dtype=torch.float64) * 0.1
        active = torch.rand(32, 4) > 0.5
        dp[~active] = 0
        g = torch.rand(32, 4, dtype=torch.float64)
        g[active] = 0
        beta = dt / (c.tau_motion + dt)
        ar = 1 / (1 + dt / c.tau_rest)
        ac, ae = 1 / (1 + dt / c.tau_contact), 1 / (1 + dt / c.tau_bulk)
        dx = beta * dp
        # Follow the original C update order to construct one feasible substep.
        p = p0 + dx / beta
        s = s0 + beta * (p + elastic - s0)
        b = b0 + db
        k0 = beta * (p0 + elastic - s0) + db
        du = k0 + dx
        rb = ar * (rb0 + c.hardening * dx / beta)
        rc = ac * (rc0 + du @ c.contact.T)
        re = ae * (re0 + du @ c.bulk.T)
        current = torch.cat([s, p, rb, rc, re, b], dim=-1)
        force = c.eta * dp / dt + rb + rc + re - g
        r = residuals(previous, current, force, elastic, db, c)
        for name in (
            "motion",
            "basal_memory",
            "contact_memory",
            "bulk_memory",
            "background",
            "negative_slip",
            "negative_gap",
            "complementarity",
        ):
            torch.testing.assert_close(
                r[name], torch.zeros_like(r[name]), rtol=0, atol=2e-12
            )
        stiffness = ac * c.contact + ae * c.bulk
        a = stiffness + torch.diag((c.eta / dt + ar * c.hardening) / beta)
        rhs = force - ar * rb0 - ac * rc0 - ae * re0 - k0 @ stiffness.T
        torch.testing.assert_close(r["yield_gap"], dx @ a.T - rhs, rtol=0, atol=2e-12)
        torch.testing.assert_close(r["slip_step"], dx, rtol=0, atol=2e-12)

    def test_negative_slip_and_negative_gap_are_exposed_separately(self):
        old = torch.zeros(24, dtype=torch.float64)
        current = old.clone()
        current[4] = -1
        result = residuals(
            old,
            current,
            torch.ones(4, dtype=torch.float64),
            torch.zeros(4, dtype=torch.float64),
            torch.zeros(4, dtype=torch.float64),
            self.c,
        )
        self.assertGreater(float(result["negative_slip"][0]), 0)
        self.assertTrue((result["negative_gap"] > 0).all())
        self.assertFalse(torch.equal(result["motion"], torch.zeros(4)))

    def test_gradients_preserve_both_previous_and_current_state_paths(self):
        old = torch.randn(24, dtype=torch.float64, requires_grad=True)
        current = torch.randn(24, dtype=torch.float64, requires_grad=True)
        force = torch.randn(4, dtype=torch.float64)
        elastic = torch.randn(4, dtype=torch.float64)
        db = torch.randn(4, dtype=torch.float64)

        def equations(a, b):
            values = residuals(a, b, force, elastic, db, self.c)
            return torch.cat(list(values.values()))

        self.assertTrue(
            torch.autograd.gradcheck(
                equations, (old, current), eps=1e-6, atol=2e-5, rtol=1e-4
            )
        )

    def test_observation_axis_and_initial_condition(self):
        state = torch.zeros(2, 24, dtype=torch.float64)
        state[1, :4] = torch.tensor([1.0, 2.0, 3.0, 4.0])
        state[1, 20:] = torch.tensor([2.0, 1.0, 2.0, -2.0])
        observation = torch.tensor(
            [
                [1.0, 0.0, 0.0, 0.0],
                [0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, 0.8, 0.2],
                [0.0, 0.0, 0.1, 0.9],
            ],
            dtype=torch.float64,
        )
        y0 = torch.tensor([10.0, 20.0, 30.0, 40.0], dtype=torch.float64)
        mean = observe(state, observation, y0)
        torch.testing.assert_close(mean[0], y0)
        torch.testing.assert_close(
            mean[1], y0 + torch.tensor([3.0, 3.0, 4.4, 2.3], dtype=torch.float64)
        )
        with self.assertRaises(ValueError):
            observe(state, torch.ones(3, 4), y0)


if __name__ == "__main__":
    unittest.main()
