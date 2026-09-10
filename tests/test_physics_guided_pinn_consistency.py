from pathlib import Path
import sys
import unittest

import numpy as np
import torch
from torch import nn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from physics_guided_pinn_consistency.core import (
    TERMS,
    get_vector,
    gradient_tables,
    gradient_vectors,
    inventory,
    motion_decomposition,
    set_vector,
)


class ConsistencyTests(unittest.TestCase):
    def test_nonzero_group_gradients_match_analytic_derivatives(self):
        model = nn.Module()
        model.rate_net = nn.Linear(2, 1, dtype=torch.float64)
        model.state_net = nn.Linear(2, 1, dtype=torch.float64)
        weights = np.array([0.3, 0.1, 0.2, -0.2, 0.4, 0.5])
        set_vector(model, weights)
        np.testing.assert_array_equal(get_vector(model), weights)
        x = torch.tensor([1.0, -2.0], dtype=torch.float64)
        r, h = model.rate_net(x).squeeze(), model.state_net(x).squeeze()
        terms = {"data": (r + h) ** 2, "rate_prior": r**2}
        terms.update(
            {name: (i + 1) * (r - h) ** 2 for i, name in enumerate(TERMS[1:8])}
        )
        vector = gradient_vectors(model, terms)
        a = np.array([1.0, -2.0, 1.0])
        expected = np.stack(
            [
                -0.4 * np.r_[a, a],
                *[1.6 * (i + 1) * np.r_[a, -a] for i in range(7)],
                0.6 * np.r_[a, np.zeros(3)],
            ]
        )
        np.testing.assert_allclose(vector, expected, atol=1e-12, rtol=0)
        metadata, groups = inventory(model)
        self.assertEqual(metadata[-1]["stop"], 6)
        _, balance, total = gradient_tables(
            vector, {k: float(v.detach()) for k, v in terms.items()}, groups
        )
        np.testing.assert_allclose(
            total,
            expected[0] + expected[1:8].mean(axis=0) + 0.001 * expected[-1],
            atol=1e-12,
            rtol=0,
        )
        self.assertAlmostEqual(balance["G_data_physics_cosine"], -1.0)
        self.assertAlmostEqual(balance["H_data_physics_cosine"], 1.0)

    def test_motion_components_match_closed_form_and_mixed_observations(self):
        beta = np.array([0.05, 0.2, 0.4, 0.8])
        time = np.arange(129)[:, None]
        plastic = 1 - (1 - beta) ** time
        motion = 0.02 * plastic / beta
        p, r = np.zeros((129, 24)), np.zeros((129, 24))
        p[1:, 4:8] = 1
        p[:, :4] = plastic + motion
        p[:, 20:] = time * 0.0001
        elastic = np.sin(np.arange(128)[:, None]) * np.ones((1, 4))
        observation = np.array(
            [[1, 0.2, 0, 0], [0, 1, 0.3, 0], [0, 0, 1, 0.4], [0.1, 0, 0, 1.0]]
        )
        arrays, checks = motion_decomposition(p, r, elastic, beta, observation)
        np.testing.assert_allclose(
            arrays["plastic_driven_s"], plastic[::64], atol=1e-12, rtol=0
        )
        np.testing.assert_allclose(
            arrays["motion_defect_s"], motion[::64], atol=1e-12, rtol=0
        )
        np.testing.assert_allclose(
            arrays["actual_observed"],
            ((p[:, :4] + p[:, 20:]) @ observation.T)[::64],
            atol=1e-12,
            rtol=0,
        )
        self.assertLess(checks["max_state_error_mm"], 1e-12)

    def test_incomplete_nonfinite_or_nonzero_initial_trajectories_fail(self):
        states = np.zeros((65, 24))
        bad = states.copy()
        bad[0, 0] = 1
        nonfinite = states.copy()
        nonfinite[3, 2] = np.nan
        for candidate in (states[:-1], bad, nonfinite):
            with self.assertRaises(ValueError):
                motion_decomposition(
                    candidate, states, np.zeros((64, 4)), np.full(4, 0.1), np.eye(4)
                )


if __name__ == "__main__":
    unittest.main()
