"""Synthetic paired-source, normalized-loss, and prefix-boundary tests."""

import copy
import unittest
from unittest.mock import patch

import numpy as np
import torch

from physics_guided.features import Scaler
from physics_guided.training import optimizer
from physics_guided_history_learning.core import new_model, windows
from physics_guided_origin_learning.core import (
    ORIGINS,
    backward_loss,
    loss_components,
    make_samples,
    mean_step,
    query_table,
)
from physics_guided_origin_learning.verify import (
    independent_samples,
    independent_losses,
)


def fixture(h=342):
    teachers = {}
    for o in (*ORIGINS[h], h):
        t = np.arange(o + 180, dtype=float)
        base = t[:, None] * np.array([0.1, 0.2, 0.3, 0.4]) + o / 100
        features = np.sin(t[:, None, None] / 17 + np.arange(80).reshape(1, 20, 4))
        features[:, 0] = base
        teachers[o] = (base, features)
    labels = teachers[h][0][:h] + np.cos(np.arange(h)[:, None] / 20) * np.array(
        [1, 2, 3, 4]
    )
    scalers = tuple(
        Scaler(np.zeros((n, 4)), np.ones((n, 4)), np.ones((n, 4))) for n in (20, 2)
    )
    return labels, teachers, scalers


class LinearProbe(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.weight = torch.nn.Parameter(
            torch.linspace(-0.01, 0.01, 23, dtype=torch.float64)
        )

    def forward(self, x):
        return (x.mean(dim=1)[:, :, 0, :] * self.weight[None, :, None]).sum(dim=1)


class OriginLearningTests(unittest.TestCase):
    def test_independent_date_constructor_rebuilds_both_sample_sources(self):
        labels, teachers, scalers = fixture(612)
        records = dict(physical=scalers[0].record(), history=scalers[1].record())
        for strategy in ("IN", "OOF"):
            samples = make_samples(strategy, 612, labels, teachers, scalers)
            table, x, payload = independent_samples(
                612, strategy, labels, teachers, records
            )
            self.assertEqual(table.to_dict("list"), query_table(612).to_dict("list"))
            np.testing.assert_array_equal(x, samples.x.numpy())
            for key, value in samples.payload().items():
                np.testing.assert_array_equal(payload[key], value)

    def test_independent_scalar_loss_matches_two_weighted_blocks(self):
        labels, teachers, scalers = fixture()
        samples = make_samples("OOF", 342, labels, teachers, scalers)
        values = np.cos(np.arange(len(samples.blocks))[:, None]) * np.array(
            [2, 3, 4, 5]
        )
        actual = loss_components(samples, values)
        expected = independent_losses(samples.payload(), values)
        np.testing.assert_allclose(
            list(actual.values()), [expected[k] for k in actual], atol=1e-14, rtol=0
        )

    def test_inventory_and_equal_block_weights(self):
        for h, a, p, u in (
            (342, 136, 90, 90),
            (432, 188, 270, 180),
            (612, 291, 540, 360),
        ):
            q = query_table(h)
            self.assertEqual(len(q), a + p)
            self.assertEqual((q.block == 0).sum(), a)
            self.assertEqual((q.block == 1).sum(), p)
            paired = q[q.block == 1]
            self.assertEqual(paired.target.nunique(), u)
            np.testing.assert_allclose(
                q.groupby("block").weight.sum().to_numpy(),
                [0.5, 0.5],
                atol=1e-15,
                rtol=0,
            )
            np.testing.assert_allclose(
                paired.groupby("target").weight.sum().to_numpy(),
                np.full(u, 0.5 / u),
                atol=1e-15,
                rtol=0,
            )

    def test_past_teachers_precede_targets_and_have_full_lead_accounting(self):
        for h, max_lead in ((342, 89), (432, 179), (612, 179)):
            q = query_table(h).query("block==1")
            self.assertTrue(((q.teacher_OOF - 1) < q.target).all())
            self.assertTrue((q.target < h).all())
            self.assertEqual(q.lead.max(), max_lead)
            self.assertEqual(q.lead.nunique(), max_lead + 1)
            np.testing.assert_array_equal(q.last_observation_index, q.origin - 1)

    def test_same_labels_weights_and_anchor_inputs(self):
        labels, teachers, scalers = fixture()
        a = make_samples("IN", 342, labels, teachers, scalers)
        b = make_samples("OOF", 342, labels, teachers, scalers)
        for x, y in (
            (a.target, b.target),
            (a.weights, b.weights),
            (a.x[:136], b.x[:136]),
            (a.base[:136], b.base[:136]),
        ):
            torch.testing.assert_close(x, y, atol=0, rtol=0)
        self.assertGreater(float(abs(a.base[136:] - b.base[136:]).max()), 0)
        self.assertGreater(float(abs(a.x[136:] - b.x[136:]).max()), 0)

    def test_oof_passes_only_origin_prefix_to_history_constructor(self):
        labels, teachers, scalers = fixture(432)
        bounds = []

        def observed(base, features, y, h, pairs, *args):
            bounds.append((h, len(y), int(pairs[0, 0]), int(pairs[-1, 1])))
            return windows(base, features, y, h, pairs, *args)

        with patch("physics_guided_origin_learning.core.windows", side_effect=observed):
            make_samples("OOF", 432, labels, teachers, scalers)
        self.assertEqual(
            bounds, [(432, 432, 31, 429), (252, 252, 252, 431), (342, 342, 342, 431)]
        )

    def test_later_labels_change_targets_but_not_origin_history(self):
        labels, teachers, scalers = fixture()
        a = make_samples("OOF", 342, labels, teachers, scalers)
        changed = labels.copy()
        changed[252:] += 1000
        b = make_samples("OOF", 342, changed, teachers, scalers)
        torch.testing.assert_close(a.x[136:], b.x[136:], atol=0, rtol=0)
        torch.testing.assert_close(
            b.target[136:] - a.target[136:],
            torch.full_like(a.target[136:], 1000),
            atol=1e-12,
            rtol=0,
        )

    def test_oof_history_is_relative_to_its_own_teacher(self):
        labels, teachers, scalers = fixture()
        b = make_samples("OOF", 342, labels, teachers, scalers)
        r = labels[:252] - teachers[252][0][:252]
        np.testing.assert_array_equal(b.x[136, :, 20, 0].numpy(), r[222:252])
        np.testing.assert_array_equal(
            b.x[136, :, 21, 0].numpy(), r[222:252] - r[221:251]
        )
        self.assertEqual(torch.count_nonzero(b.x[136, :, 22]), 0)
        np.testing.assert_allclose(
            b.x[-1, :, 22].numpy(), np.full((30, 1, 4), 89 / 179), atol=0, rtol=0
        )

    def test_extra_or_nonfinite_labels_are_rejected(self):
        labels, teachers, scalers = fixture()
        for bad in (
            np.vstack([labels, np.zeros((1, 4))]),
            np.full_like(labels, np.nan),
        ):
            with self.assertRaises(ValueError):
                make_samples("OOF", 342, bad, teachers, scalers)

    def test_missing_or_incomplete_teacher_is_rejected(self):
        labels, teachers, scalers = fixture()
        for bad in (
            {342: teachers[342]},
            {**teachers, 252: (teachers[252][0][:-1], teachers[252][1][:-1])},
        ):
            with self.assertRaises(ValueError):
                make_samples("OOF", 342, labels, bad, scalers)

    def test_shared_scalers_are_not_modified(self):
        labels, teachers, scalers = fixture()
        before = [s.record() for s in scalers]
        for strategy in ["IN", "OOF"]:
            make_samples(strategy, 342, labels, teachers, scalers)
        self.assertEqual(before, [s.record() for s in scalers])

    def test_loss_blocks_each_have_one_half_total_mass(self):
        labels, teachers, scalers = fixture()
        s = make_samples("OOF", 342, labels, teachers, scalers)
        correction = (
            s.target.numpy()
            - s.base.numpy()
            + np.where(s.blocks[:, None] == 0, 2.0, 4.0)
        )
        loss = loss_components(s, correction)
        self.assertAlmostEqual(loss["anchor_loss"], 4 / 10000)
        self.assertAlmostEqual(loss["paired_loss"], 16 / 10000)
        self.assertAlmostEqual(loss["loss"], 10 / 10000)

    def test_chunked_weighted_gradient_matches_full_batch(self):
        labels, teachers, scalers = fixture()
        samples = make_samples("OOF", 342, labels, teachers, scalers)
        a = LinearProbe()
        b = copy.deepcopy(a)
        left = backward_loss(a, samples, chunk=53)
        right = backward_loss(b, samples, chunk=1000)
        self.assertAlmostEqual(left, right, places=14)
        torch.testing.assert_close(a.weight.grad, b.weight.grad, atol=1e-12, rtol=0)

    def test_one_synthetic_update_occurs_after_accumulating_all_rows(self):
        labels, teachers, scalers = fixture()
        samples = make_samples("OOF", 342, labels, teachers, scalers)
        model = LinearProbe()
        old = model.weight.detach().clone()
        op = optimizer(model)
        loss, norm = mean_step(model, op, samples)
        self.assertTrue(np.isfinite(loss) and np.isfinite(norm))
        self.assertFalse(torch.equal(old, model.weight))
        self.assertEqual(op.state[model.weight]["step"].item(), 1)

    def test_zero_head_is_same_physical_baseline_for_each_sample(self):
        labels, teachers, scalers = fixture()
        s = make_samples("OOF", 342, labels, teachers, scalers)
        model = new_model(0)
        with torch.no_grad():
            torch.testing.assert_close(
                model(s.x[[0, 136, 225]]),
                torch.zeros((3, 4), dtype=torch.float64),
                atol=0,
                rtol=0,
            )


if __name__ == "__main__":
    unittest.main()
