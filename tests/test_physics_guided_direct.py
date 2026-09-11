"""Synthetic tests for the new model's scientific boundaries and decisions."""

import copy
import unittest

import numpy as np
import pandas as pd
import torch
from scipy.special import ndtri

from physics_guided.features import Scaler
from physics_guided.training import optimizer, setup
from physics_guided_direct import (
    ConditionalScale,
    DirectConvLSTM,
    decide,
    evaluation_batch,
    mean_step,
    predict,
    scale_features,
    scale_step,
    score,
)
from physics_guided_sample_learning.core import POINTS
from physics_guided_sequence.core import make_sequence
from physics_guided_sequence_learning.core import block_losses, make_batch


def fixture():
    day = np.arange(240, dtype=float)
    base = day[:, None] * np.arange(1, 5)[None] / 7
    features = np.sin(day[:, None, None] / 11 + np.arange(80).reshape(1, 20, 4))
    labels = base[:60] + 0.8 * np.cos(day[:60, None] / 9 + np.arange(4)[None])
    scalers = (
        Scaler(np.zeros((20, 4)), np.ones((20, 4)), np.ones((20, 4))),
        Scaler(np.zeros((2, 4)), np.ones((2, 4)), np.ones((2, 4))),
    )
    return base, features, labels, scalers


class DirectTests(unittest.TestCase):
    def setUp(self):
        setup(0)

    def test_zero_head_is_exact_bplus_for_every_evaluation_day(self):
        base, features, labels, scalers = fixture()
        table, batch = evaluation_batch(base, features, labels, 60, scalers)
        model = DirectConvLSTM()
        self.assertEqual(sum(p.numel() for p in model.parameters()), 8177)
        values = predict(model, batch, base, table.target.to_numpy())
        np.testing.assert_array_equal(values[30:], base[30:])
        self.assertTrue(np.isnan(values[:30]).all())
        self.assertEqual(table[table.target >= 60].origin.unique().tolist(), [60])
        with self.assertRaises(ValueError):
            evaluation_batch(base, features, np.r_[labels, labels[:1]], 60, scalers)

    def test_history_reaches_last_lead_and_future_steps_are_direct(self):
        base, features, labels, scalers = fixture()
        sequence = make_sequence(base, features, labels, 60, 180, *scalers)
        model = DirectConvLSTM()
        with torch.no_grad():
            model.head[-1].weight.fill_(0.01)
        history = sequence.encoder.clone().requires_grad_(True)
        future = sequence.decoder.clone().requires_grad_(True)
        outputs = model(history, future)
        history_grad, future_grad = torch.autograd.grad(
            outputs[0, 179, 0], (history, future)
        )
        self.assertGreater(float(history_grad[:, :, 20:22].norm()), 0)
        self.assertEqual(float(future_grad[:, :179].norm()), 0)
        # A shorter query computes precisely the same first 30 direct outputs.
        with torch.no_grad():
            short = model(history, future[:, :30])
        torch.testing.assert_close(short, outputs[:, :30], rtol=1e-12, atol=1e-12)

    def test_packed_queries_match_separate_origins_without_later_history(self):
        base, features, labels, scalers = fixture()
        table = pd.DataFrame(
            dict(
                origin=[31, 31, 45, 45],
                target=[31, 35, 46, 51],
                teacher=[60, 60, 50, 50],
                block=[0, 0, 1, 1],
                weight=[0.25] * 4,
            )
        )
        pool = {60: (base, features), 50: (base + 2, features + 0.1)}
        batch = make_batch(table, pool, labels, scalers, "teacher", True)
        model = DirectConvLSTM()
        with torch.no_grad():
            model.head[-1].weight.fill_(0.01)
        packed = model(batch.encoder, batch.decoder)
        expected = []
        for row in table.itertuples():
            sequence = make_sequence(
                *pool[row.teacher],
                labels[: row.origin],
                row.origin,
                row.target - row.origin + 1,
                *scalers,
            )
            expected.append(model(sequence.encoder, sequence.decoder)[0, -1])
        torch.testing.assert_close(
            packed[batch.row_batch, batch.row_lead],
            torch.stack(expected),
            rtol=1e-12,
            atol=1e-12,
        )
        perturbed = labels.copy()
        perturbed[45:] += 1e5
        other = make_batch(table, pool, perturbed, scalers, "teacher", True)
        torch.testing.assert_close(batch.encoder, other.encoder, rtol=0, atol=0)
        with torch.no_grad():
            torch.testing.assert_close(
                packed, model(other.encoder, other.decoder), rtol=0, atol=0
            )
        # Unselected padded predictions have no influence on the two objectives.
        selected = torch.zeros_like(packed, dtype=torch.bool)
        selected[batch.row_batch, batch.row_lead] = True
        changed = packed.clone()
        changed[~selected] += 1000
        torch.testing.assert_close(
            block_losses(batch, packed), block_losses(batch, changed), rtol=0, atol=0
        )
        before = model.head[-1].weight.detach().clone()
        record = mean_step(model, optimizer(model), batch)
        self.assertTrue(np.isfinite(list(record.values())).all())
        self.assertFalse(torch.equal(before, model.head[-1].weight))

    def test_scale_features_ignore_every_post_origin_observation(self):
        base, _, labels, _ = fixture()
        mean = base + 2
        origins, targets = np.full(12, 45), np.arange(45, 57)
        values = scale_features(base, mean, labels[:45], origins, targets)
        poison = labels.copy()
        poison[45:] = np.nan
        torch.testing.assert_close(
            values, scale_features(base, mean, poison, origins, targets), rtol=0, atol=0
        )
        with self.assertRaises(ValueError):
            scale_features(base, mean, labels[:44], origins, targets)
        np.testing.assert_allclose(
            values[:, :, 0], np.broadcast_to(np.arange(12)[:, None] / 179, (12, 4))
        )

    def test_scale_initialization_nll_and_separation_from_mean_training(self):
        model = ConditionalScale(np.array([0.002, 1, 20, 100.0]))
        self.assertEqual(sum(p.numel() for p in model.parameters()), 61)
        x = torch.zeros((10, 4, 5), dtype=torch.float64)
        torch.testing.assert_close(
            model(x), torch.tensor([[0.002, 1, 20, 100.0]] * 10, dtype=torch.float64)
        )
        frozen = torch.ones((10, 4), dtype=torch.float64)
        original = frozen.clone()
        labels = frozen + torch.tensor([0.001, 1, 20, 100.0])
        result = scale_step(model, optimizer(model), x, frozen, labels)
        self.assertTrue(np.isfinite(list(result.values())).all())
        torch.testing.assert_close(frozen, original, rtol=0, atol=0)
        self.assertIsNone(frozen.grad)
        with self.assertRaises(ValueError):
            scale_step(model, optimizer(model), x, frozen.requires_grad_(True), labels)

    def test_time_varying_gaussian_scores_have_analytic_reference(self):
        n = 432
        means = np.zeros((1, n + 180, 4))
        sigmas = np.broadcast_to(
            np.linspace(1, 4, n + 180)[None, :, None], means.shape
        ).copy()
        rows, summary = score(means, sigmas, np.zeros((n + 180, 4)), n, "DIRECT")
        expected = sigmas[0, 30:] * (np.sqrt(2) - 1) / np.sqrt(np.pi)
        self.assertAlmostEqual(
            rows[0]["crps_mm"], expected[: n - 30, 0].mean(), places=12
        )
        np.testing.assert_allclose(
            summary["upper_90"], ndtri(0.95) * sigmas[0, 30:], atol=1e-6
        )
        self.assertEqual(rows[4]["coverage_90"], 1)
        self.assertAlmostEqual(rows[4]["width_90_mm"], rows[4]["interval_score_90_mm"])

    def test_complete_decision_rejects_one_failed_point_or_unjustified_width(self):
        rows = []
        for n in (432, 612):
            for strategy in ("P0", "DIRECT"):
                a = strategy == "DIRECT"
                for part in ("train", "prediction"):
                    for station in POINTS:
                        rows.append(
                            dict(
                                outer_days=n,
                                strategy=strategy,
                                part=part,
                                station=station,
                                rmse_mm=1 if a else 2,
                                mae_mm=1 if a else 2,
                                crps_mm=1 if a else 2,
                                coverage_90=0.9 if a else 0.7,
                                width_90_mm=5 if a else 10,
                                interval_score_90_mm=5 if a else 10,
                            )
                        )
        data = pd.DataFrame(rows)
        self.assertTrue(decide(data)["passed"])
        changed = copy.deepcopy(data)
        idx = changed[
            (changed.strategy == "DIRECT") & (changed.part == "prediction")
        ].index[0]
        changed.loc[idx, "rmse_mm"] = 2
        self.assertEqual(decide(changed)["strict_mean_improvement_count"], 7)
        self.assertFalse(decide(changed)["passed"])
        changed.loc[idx, ["rmse_mm", "width_90_mm", "interval_score_90_mm"]] = [
            1,
            11,
            11,
        ]
        self.assertFalse(decide(changed)["passed"])
        with self.assertRaises(ValueError):
            decide(data.iloc[:-1])


if __name__ == "__main__":
    unittest.main()
