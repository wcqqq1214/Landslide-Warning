"""Synthetic checks only: no real-data trial training before the single run."""

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
import torch

from physics_guided.features import Scaler
from physics_guided.training import optimizer
from physics_guided_direct import mean_step, scale_features
from physics_guided_sequence_learning.core import make_batch
from physics_guided_joint import (
    ARMS, STRATEGIES, row_context, initial_scale, new_models, joint_step,
    probability_loss, selected_mean, decide, score_saved, training_inputs,
    evaluation_inputs, predict_distribution, specification, load_distribution,
)


class JointTests(unittest.TestCase):
    def setUp(self):
        days = np.arange(240)
        self.base = days[:, None] * np.arange(1, 5)[None] / 7
        physical = np.sin(days[:, None, None] / 11 + np.arange(80).reshape(1, 20, 4))
        self.labels = self.base[:60] + .8 * np.cos(days[:60, None] / 9 + np.arange(4)[None])
        self.pool = {60: (self.base, physical), 50: (self.base + 2, physical + .1)}
        self.table = pd.DataFrame(dict(origin=[31, 31, 45, 45], target=[31, 35, 46, 51],
                                       teacher=[60, 60, 50, 50], block=[0, 0, 1, 1], weight=[.25] * 4))
        scalers = (Scaler(np.zeros((20, 4)), np.ones((20, 4)), np.ones((20, 4))),
                   Scaler(np.zeros((2, 4)), np.ones((2, 4)), np.ones((2, 4))))
        self.batch = make_batch(self.table, self.pool, self.labels, scalers, "teacher", True)
        self.context = row_context(self.table, self.pool, self.labels, "teacher")
        self.sigma0 = initial_scale(self.batch)

    def test_tensor_features_match_numpy_and_keep_mean_gradient(self):
        for row_id, row in self.table.iterrows():
            base = self.pool[row.teacher][0]
            full_mean = base + 3
            expected = scale_features(base, full_mean, self.labels,
                                      np.array([int(row.origin)]), np.array([int(row.target)]))
            mu = (self.context.baseline + 3).requires_grad_()
            actual = self.context.features(mu)[row_id:row_id + 1]
            torch.testing.assert_close(actual, expected, rtol=0, atol=1e-15)
            gradient = torch.autograd.grad(actual[..., 2].sum(), mu)[0]
            self.assertGreater(float(gradient[row_id].abs().sum()), 0)

    def test_post_origin_observations_cannot_enter_scale_features(self):
        for row_id, row in self.table.iterrows():
            poisoned = self.labels.copy()
            poisoned[int(row.origin):] = np.nan
            context = row_context(self.table.iloc[row_id:row_id + 1], self.pool, poisoned, "teacher")
            torch.testing.assert_close(context.constants[0], self.context.constants[row_id], rtol=0, atol=0)

    def test_scale_initialization_and_nll_use_only_oof_rows(self):
        changed = copy.deepcopy(self.batch)
        changed.targets[changed.blocks == 0] += 1e8
        np.testing.assert_array_equal(initial_scale(changed), self.sigma0)
        mean, scale = new_models(0, self.sigma0)
        mu = selected_mean(self.batch, mean(self.batch.encoder, self.batch.decoder))
        torch.testing.assert_close(
            probability_loss(self.batch, self.context, mu, scale, False),
            probability_loss(changed, self.context, mu, scale, False), rtol=0, atol=0,
        )

    def test_paired_initialization_and_zero_correction(self):
        mean, scale = new_models(1, self.sigma0)
        a, b = (copy.deepcopy((mean, scale)) for _ in ARMS)
        for x, y in zip(a, b):
            for p, q in zip(x.parameters(), y.parameters()):
                torch.testing.assert_close(p, q, rtol=0, atol=0)
        self.assertEqual(int(torch.count_nonzero(mean(self.batch.encoder, self.batch.decoder))), 0)
        expected = np.broadcast_to(self.sigma0, self.context.baseline.shape)
        np.testing.assert_allclose(scale(self.context.features(self.context.baseline)).detach(), expected,
                                   rtol=0, atol=1e-12)

    def test_detached_mean_reproduces_old_update_and_joint_receives_nll_gradient(self):
        original, scale = new_models(2, self.sigma0)
        detached, joint = copy.deepcopy(original), copy.deepcopy(original)
        detached_scale, joint_scale = copy.deepcopy(scale), copy.deepcopy(scale)
        original_op, detached_op, joint_op = map(optimizer, (original, detached, joint))
        scale_op, joint_scale_op = optimizer(detached_scale), optimizer(joint_scale)
        for _ in range(2):
            mean_step(original, original_op, self.batch)
            detached_record = joint_step(detached, detached_scale, detached_op, scale_op,
                                         self.batch, self.context, True)
            joint_record = joint_step(joint, joint_scale, joint_op, joint_scale_op,
                                      self.batch, self.context, False)
            self.assertEqual(detached_record["nll_mean_gradient_norm"], 0)
            self.assertGreater(joint_record["nll_mean_gradient_norm"], 0)
            for p, q in zip(original.parameters(), detached.parameters()):
                torch.testing.assert_close(p, q, rtol=0, atol=0)
        self.assertTrue(any(not torch.equal(p, q) for p, q in zip(joint.parameters(), detached.parameters())))

    def test_scale_feature_indirect_gradient_is_detached_in_control(self):
        _, scale = new_models(0, self.sigma0)
        with torch.no_grad():
            scale.network[-1].weight.fill_(.4)
        mu = (self.context.baseline + 3).requires_grad_()
        sigma = scale(self.context.features(mu))
        self.assertGreater(float(torch.autograd.grad(sigma.sum(), mu)[0].abs().sum()), 0)
        sigma = scale(self.context.features(mu.detach()))
        self.assertIsNone(torch.autograd.grad(sigma.sum(), mu, allow_unused=True)[0])

    def test_gates_require_both_windows_and_do_not_promote_detached(self):
        rows = []
        for h in (432, 612):
            for strategy in STRATEGIES:
                for part in ("train", "prediction"):
                    for station in ("ATU1", "ATU5", "MJ3", "MJ1"):
                        value = 5. if strategy == "JOINT" else 10.
                        rows.append(dict(outer_days=h, strategy=strategy, part=part, station=station,
                                         days=180, rmse_mm=value, mae_mm=value, crps_mm=value,
                                         interval_score_90_mm=value, coverage_90=.9, width_90_mm=20.))
        table = pd.DataFrame(rows)
        spec = dict(strict_tolerance_mm=1e-6, coverage_tolerance=1e-12, coverage_slack=1 / 180)
        self.assertTrue(decide(table, spec)["passed"])
        index = table[(table.outer_days == 612) & (table.strategy == "JOINT")
                      & (table.part == "prediction") & (table.station == "MJ3")].index
        table.loc[index, "width_90_mm"] = 21
        self.assertFalse(decide(table, spec)["passed"])
        table.loc[index, "width_90_mm"] = 20
        table.loc[table.strategy == "DETACHED", "rmse_mm"] = 1
        result = decide(table, spec)
        self.assertFalse(result["passed"])
        self.assertEqual(result["candidate"], "JOINT")

    def test_training_api_rejects_labels_beyond_exact_prefix(self):
        with self.assertRaises(ValueError):
            training_inputs({}, 59, self.labels, self.pool)

    def test_registered_query_packing_with_synthetic_values_only(self):
        spec = specification()
        for h in (432, 612):
            day = np.arange(h + 180)
            base = day[:, None] * np.arange(1, 5)[None] / 7
            physical = np.sin(day[:, None, None] / 11 + np.arange(80).reshape(1, 20, 4))
            labels = base[:h] + .8 * np.cos(day[:h, None] / 9 + np.arange(4)[None])
            pool = {k: (base[:k + 180], physical[:k + 180]) for k in (252, 342, 432, h)}
            table, batch, context, sigma0 = training_inputs(spec, h, labels, pool)
            self.assertEqual(len(table), {432: 458, 612: 831}[h])
            _, eval_batch, eval_context = evaluation_inputs(spec, h, labels, pool)
            mean, scale = new_models(0, sigma0)
            mu, sigma = predict_distribution(mean, scale, eval_batch, eval_context, h)
            np.testing.assert_array_equal(mu[30:], base[30:])
            self.assertTrue(np.isfinite(sigma[30:]).all())
            self.assertTrue((sigma[30:] > .001).all())
            self.assertEqual(batch.targets.shape, context.baseline.shape)

    def test_complete_scoring_preserves_dates_seeds_and_pooled_rmse(self):
        labels = np.arange(792)[:, None] + np.zeros((792, 4))
        def synthetic_distribution(out, spec, h, strategy):
            means = np.broadcast_to(labels[:h + 180] + np.arange(1, 5), (3, h + 180, 4)).copy()
            return means, np.ones_like(means) * 2
        with patch("physics_guided_joint.load_distribution", side_effect=synthetic_distribution):
            tables = score_saved(None, dict(outer_days=[432, 612], seeds=[0, 1, 2],
                                            strict_tolerance_mm=1e-6, coverage_tolerance=1e-12,
                                            coverage_slack=1 / 180),
                                  labels, pd.date_range("2016-07-01", periods=792))
        self.assertEqual(len(tables["metrics.csv"]), 64)
        self.assertEqual(len(tables["seed_metrics.csv"]), 144)
        self.assertNotIn("P0", set(tables["seed_metrics.csv"].strategy))
        self.assertEqual(len(tables["daily_predictions.csv"]), (582 + 762) * 4 * 4)
        aggregate = tables["aggregate_metrics.csv"]
        np.testing.assert_array_equal(aggregate.loc[aggregate.aggregation == "point_mean", "rmse_mm"], 2.5)
        np.testing.assert_array_equal(aggregate.loc[aggregate.aggregation == "pooled", "rmse_mm"], np.sqrt(7.5))

    def test_p0_retains_one_fixed_component_without_fabricated_seeds(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            means = np.zeros((1, 612, 4))
            np.savez(path / "prediction_432_P0.npz", means=means, scales=np.full((1, 4), 2.))
            with patch("physics_guided_joint.ROOT", path):
                mu, sigma = load_distribution(path, dict(reference_source="."), 432, "P0")
            self.assertEqual(mu.shape, (1, 612, 4))
            np.testing.assert_array_equal(sigma, np.full_like(means, 2.))


if __name__ == "__main__":
    unittest.main()
