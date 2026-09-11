"""Synthetic balancing, optimizer-direction, and paired provenance contracts."""

import math
import unittest

import numpy as np
import pandas as pd
import torch

from physics_guided.training import optimizer, update
from physics_guided_origin_gradients.core import Budget, block_gradients
from physics_guided_origin_learning.core import backward_loss, make_samples
from physics_guided_balanced_origin.core import (
    weights,
    profile,
    dot,
    mean_step,
    optimizer_diagnostics,
    compare_equal,
)
from physics_guided_balanced_origin.audit import scalar_profile, verify_logs
from physics_guided_sample_learning.core import compare_metrics
from test_physics_guided_origin_gradients import Probe, fixture
from test_physics_guided_origin_learning import fixture as source_fixture


class BalancedOriginTests(unittest.TestCase):
    def test_nonzero_weights_equalize_gradient_magnitudes(self):
        for a, p in ((1.0, 100.0), (20.0, 1.0), (0.0001, 0.0003)):
            qa, qp = weights(a, p)
            self.assertAlmostEqual(qa + qp, 1)
            self.assertAlmostEqual(qa * a, qp * p)
            self.assertGreater(qa, 0)
            self.assertGreater(qp, 0)

    def test_zero_branches_and_invalid_norms(self):
        self.assertEqual(weights(0, 0), (0.5, 0.5))
        self.assertEqual(weights(0, 1), (0.0, 1.0))
        self.assertEqual(weights(1, 0), (1.0, 0.0))
        self.assertEqual(weights(1e-12, 1e-12), (0.5, 0.5))
        for values in ((-1, 2), (math.nan, 1), (1, math.inf)):
            with self.assertRaises(ValueError):
                weights(*values)

    def test_raw_mixed_direction_does_not_increase_either_objective(self):
        for gradients in (
            np.array([[1.0, 0], [-20.0, 1]]),
            np.array([[1.0, 2], [3.0, 4]]),
            np.array([[0.0, 0], [3.0, 4]]),
        ):
            row, mixed = profile(np.array([1.0, 2.0]), gradients)
            self.assertLessEqual(row["raw_anchor_slope"], 1e-12)
            self.assertLessEqual(row["raw_paired_slope"], 1e-12)
            self.assertTrue(np.isfinite(mixed).all())

    def test_opposite_gradients_can_cancel(self):
        row, mixed = profile(
            np.array([1.0, 2.0]), np.array([[1.0, 2.0], [-10.0, -20.0]])
        )
        np.testing.assert_allclose(mixed, 0, atol=1e-15, rtol=0)
        self.assertLess(row["gradient_norm"], 1e-12)

    def test_independent_scalar_profile_matches(self):
        model, samples = Probe(), fixture()
        losses, gradients = block_gradients(model, samples, 3, Budget(10, 0))
        actual, _ = profile(losses, gradients)
        expected = scalar_profile(losses, gradients)
        self.assertEqual(actual.keys(), expected.keys())
        np.testing.assert_allclose(
            list(actual.values()), list(expected.values()), rtol=1e-12, atol=1e-14
        )

    def test_weights_are_constants_in_weighted_autograd(self):
        model, samples = Probe(), fixture()
        losses, gradients = block_gradients(model, samples, 3, Budget(10, 0))
        row, mixed = profile(losses, gradients)
        self.assertIsInstance(row["anchor_weight"], float)
        residual = (model(samples.x) + samples.base - samples.target) / 100
        per_row = residual.square().mean(1) * samples.weights * 2
        objective = (
            row["anchor_weight"] * per_row[samples.blocks == 0].sum()
            + row["paired_weight"] * per_row[samples.blocks == 1].sum()
        )
        actual = torch.cat(
            [
                g.flatten()
                for g in torch.autograd.grad(objective, tuple(model.parameters()))
            ]
        ).numpy()
        np.testing.assert_allclose(actual, mixed, rtol=1e-12, atol=1e-14)

    def test_equal_weight_special_case_reproduces_old_gradient(self):
        model, samples = Probe(), fixture()
        _, g = block_gradients(model, samples, 3, Budget(10, 0))
        model.zero_grad(set_to_none=True)
        backward_loss(model, samples, chunk=2)
        actual = torch.cat([p.grad.flatten() for p in model.parameters()]).numpy()
        np.testing.assert_allclose(actual, g.mean(0), rtol=1e-12, atol=1e-14)

    def test_batching_does_not_change_balancing_weights(self):
        model, samples = Probe(), fixture()
        a = profile(*block_gradients(model, samples, 2, Budget(10, 0)))
        b = profile(*block_gradients(model, samples, 7, Budget(10, 0)))
        np.testing.assert_allclose(
            list(a[0].values()), list(b[0].values()), rtol=1e-12, atol=1e-14
        )
        np.testing.assert_allclose(a[1], b[1], rtol=1e-12, atol=1e-14)

    def test_logged_update_projection_uses_actual_parameter_change(self):
        model, samples = Probe(), fixture()
        _, g = block_gradients(model, samples, 3, Budget(10, 0))
        before = (
            torch.nn.utils.parameters_to_vector(model.parameters()).detach().clone()
        )
        row = mean_step(model, optimizer(model), samples, Budget(10, 0))
        delta = (
            torch.nn.utils.parameters_to_vector(model.parameters()).detach() - before
        ).numpy()
        self.assertAlmostEqual(
            row["linear_anchor_change"], float(np.sum(g[0] * delta)), places=14
        )
        self.assertAlmostEqual(
            row["linear_paired_change"], float(np.sum(g[1] * delta)), places=14
        )
        self.assertAlmostEqual(
            row["update_norm"], math.sqrt(float(np.sum(delta**2))), places=14
        )

    def test_adam_momentum_can_reverse_one_objective_direction(self):
        model = torch.nn.Linear(2, 1, bias=False, dtype=torch.float64)
        op = optimizer(model)
        # Two clipped negative steps leave enough first-moment history to
        # outweigh the next positive gradient in the first coordinate.
        for _ in range(2):
            model.weight.grad = torch.tensor([[-10.0, 0.0]], dtype=torch.float64)
            update(model, op)
        before = model.weight.detach().clone()
        g = np.array([[1.0, 0.0], [1.0, 1.0]])
        row, mixed = profile(np.ones(2), g)
        self.assertLess(row["raw_anchor_slope"], 0)
        self.assertLess(row["raw_paired_slope"], 0)
        model.weight.grad = torch.as_tensor(mixed[None])
        update(model, op)
        delta = (model.weight.detach() - before).numpy().flatten()
        self.assertGreater(dot(g[0], delta), 0)

    def test_budget_rejects_training_before_optimizer_update(self):
        model = Probe()
        before = {k: v.clone() for k, v in model.state_dict().items()}
        with self.assertRaises(RuntimeError):
            mean_step(model, optimizer(model), fixture(), Budget(0, 0))
        for k, v in model.state_dict().items():
            self.assertTrue(torch.equal(before[k], v))

    def test_source_boundary_and_common_block_stay_frozen(self):
        labels, teachers, scalers = source_fixture()
        a = make_samples("IN", 342, labels, teachers, scalers)
        b = make_samples("OOF", 342, labels, teachers, scalers)
        anchor = a.blocks == 0
        self.assertTrue(torch.equal(a.x[anchor], b.x[anchor]))
        self.assertTrue(torch.equal(a.weights, b.weights))
        for strategy in ("IN", "OOF"):
            with self.assertRaises(ValueError):
                make_samples(
                    strategy,
                    342,
                    np.r_[labels, np.full((1, 4), 9999.0)],
                    teachers,
                    scalers,
                )

    def test_actual_loss_changes_differ_from_linearized_logs(self):
        rows = []
        for e in range(1, 101):
            rows.append(
                dict(
                    fit_days=342,
                    strategy="IN",
                    seed=0,
                    epoch=e,
                    anchor_loss=1 + e / 100,
                    paired_loss=2 - e / 100,
                    linear_anchor_change=-0.1,
                    linear_paired_change=-0.1,
                )
            )
        summary = pd.DataFrame(
            [
                dict(
                    fit_days=342,
                    strategy="IN",
                    seed=0,
                    final_anchor_loss=2.01,
                    final_paired_loss=0.99,
                )
            ]
        )
        diagnostics = optimizer_diagnostics(pd.DataFrame(rows), summary)
        self.assertTrue(diagnostics.actual_anchor_increases.all())
        self.assertFalse(diagnostics.actual_both_decrease.any())
        np.testing.assert_allclose(
            diagnostics.actual_anchor_change, 0.01, atol=1e-14, rtol=0
        )
        self.assertTrue((diagnostics.linear_anchor_change < 0).all())

    def test_eq_improvement_is_not_bplus_improvement(self):
        rows = []
        for n in (432, 612):
            for s in ("P0", "IN", "OOF"):
                for station in ("ATU1", "ATU5", "MJ3", "MJ1"):
                    for part in ("train", "prediction"):
                        value = 1.0 if s == "P0" else 1.5
                        rows.append(
                            dict(
                                outer_days=n,
                                strategy=s,
                                station=station,
                                part=part,
                                rmse_mm=value,
                                mae_mm=value,
                                crps_mm=value,
                            )
                        )
        current = pd.DataFrame(rows)
        old = current.copy()
        old.loc[old.strategy != "P0", ["rmse_mm", "mae_mm", "crps_mm"]] = 2.0
        self.assertTrue(
            compare_equal(current, old).strict_mean_improvement_over_EQ.all()
        )
        self.assertFalse(compare_metrics(current).strict_mean_improvement.any())

    def test_full_synthetic_logs_pass_independent_algebra_and_endpoint_checks(self):
        model, samples = Probe(), fixture()
        rows = []
        # Reuse one frozen synthetic gradient; this test does not fit real observations.
        loss, gradient = block_gradients(model, samples, 7, Budget(10, 0))
        record, _ = profile(loss, gradient)
        for epoch in range(1, 101):
            rows.append(
                dict(
                    fit_days=342,
                    strategy="IN",
                    seed=0,
                    epoch=epoch,
                    loss_before_update=record["loss"],
                    **{k: v for k, v in record.items() if k != "loss"},
                    update_norm=0.0,
                    linear_anchor_change=0.0,
                    linear_paired_change=0.0,
                )
            )
        logs = pd.DataFrame(rows)
        summary = pd.DataFrame(
            [
                dict(
                    fit_days=342,
                    strategy="IN",
                    seed=0,
                    final_anchor_loss=record["anchor_loss"],
                    final_paired_loss=record["paired_loss"],
                )
            ]
        )
        diagnostics = optimizer_diagnostics(logs, summary)
        verify_logs(logs, summary, diagnostics)
        logs.loc[0, "anchor_weight"] = 0.9
        with self.assertRaises(ArithmeticError):
            verify_logs(logs, summary, diagnostics)


if __name__ == "__main__":
    unittest.main()
