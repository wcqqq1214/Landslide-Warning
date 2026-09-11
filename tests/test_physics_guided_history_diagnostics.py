"""Synthetic tests for the frozen probe and independent demand statistics."""

import math
import unittest

import numpy as np
import torch

from physics_guided.features import Scaler
from physics_guided_history_learning.core import new_model, windows
from physics_guided_history_diagnostics.core import (
    build_tables,
    demand_stats,
    evaluate,
    masked_inputs,
    model_stats,
    queries,
    summary_arrays,
)
from physics_guided_history_diagnostics.verify import scalar_demand, scalar_models
from physics_guided_history_diagnostics.workflow import specification


class Probe(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.weight = torch.nn.Parameter(torch.tensor(2.0, dtype=torch.float64))

    def forward(self, x):
        if torch.is_grad_enabled():
            raise RuntimeError("Gradients unexpectedly enabled")
        return self.weight * x[:, -1, 20, 0]


def artificial(h, zero=False):
    pairs, kind = queries(h)
    base = np.zeros((h + 180, 4))
    labels = (
        base if zero else np.repeat(np.sin(np.arange(h + 180) / 19)[:, None], 4, axis=1)
    )
    full = np.repeat(np.array([1, -1, 0])[:, None, None], len(pairs), axis=1) * np.ones(
        (3, len(pairs), 4)
    )
    saved = dict(pairs=pairs, kind=kind, H=full, H0=full * 0)
    return summary_arrays(saved, base, labels)


class HistoryDiagnosticTests(unittest.TestCase):
    def test_mask_changes_only_two_channels_without_mutating_inputs(self):
        x = torch.arange(3 * 30 * 23 * 4, dtype=torch.float64).reshape(3, 30, 23, 1, 4)
        before = x.clone()
        x0 = masked_inputs(x)
        torch.testing.assert_close(x, before, atol=0, rtol=0)
        torch.testing.assert_close(x0[:, :, :20], x[:, :, :20], atol=0, rtol=0)
        torch.testing.assert_close(x0[:, :, 22:], x[:, :, 22:], atol=0, rtol=0)
        self.assertEqual(torch.count_nonzero(x0[:, :, 20:22]), 0)
        self.assertGreater(torch.count_nonzero(x[:, :, 20:22]), 0)

    def test_same_weights_have_known_history_response_and_no_gradients(self):
        model = Probe()
        x = torch.ones((7, 30, 23, 1, 4), dtype=torch.float64)
        calls = {"forward_calls": 0}
        full = evaluate(model, x, 3, calls, 6)
        masked = evaluate(model, masked_inputs(x), 3, calls, 6)
        np.testing.assert_array_equal(full, np.full((7, 4), 2.0))
        np.testing.assert_array_equal(masked, np.zeros((7, 4)))
        self.assertEqual(calls["forward_calls"], 6)
        self.assertEqual(model.weight.item(), 2)
        self.assertIsNone(model.weight.grad)
        with self.assertRaisesRegex(RuntimeError, "budget"):
            evaluate(model, x, 3, calls, 6)

    def test_mutating_model_is_rejected(self):
        class Mutable(Probe):
            def forward(self, x):
                self.weight.add_(1)
                return super().forward(x)

        with self.assertRaisesRegex(ValueError, "state changed"):
            evaluate(
                Mutable(),
                torch.ones((1, 30, 23, 1, 4), dtype=torch.float64),
                1,
                {"forward_calls": 0},
                1,
            )

    def test_mask_rejects_nonfinite_or_wrong_channels(self):
        for x in (
            torch.zeros((2, 30, 22, 1, 4)),
            torch.full((2, 30, 23, 1, 4), float("nan")),
        ):
            with self.assertRaises(ValueError):
                masked_inputs(x)

    def test_registered_query_and_forward_budgets_match_all_prefixes(self):
        spec = specification()
        calls = {128: 0, 53: 0}
        for h, n in ((342, 627), (432, 769), (612, 1052)):
            pairs, kind = queries(h)
            self.assertEqual(len(pairs), n)
            self.assertTrue((pairs[kind == 0, 1] < h).all())
            self.assertTrue((pairs[kind == 2, 0] == h).all())
            np.testing.assert_array_equal(pairs[kind == 2, 1], np.arange(h, h + 180))
            self.assertEqual(np.sum(kind == 1), h - 31)
            for batch in calls:
                calls[batch] += 6 * math.ceil(n / batch)
        self.assertEqual(calls[128], spec["primary_forward_calls"])
        self.assertEqual(calls[53], spec["replay_forward_calls"])

    def test_day30_stays_in_all_fit_but_has_no_horizon(self):
        data = artificial(342)
        self.assertEqual(data["lead"][-1], -1)
        self.assertEqual(data["kind"][-1], 1)
        self.assertEqual(np.sum(data["kind"] == 1), 312)
        np.testing.assert_array_equal(data["H"][:, -1], np.zeros((3, 4)))
        self.assertEqual(np.sum((data["kind"] == 1) & (data["lead"] >= 0)), 311)

    def test_ensemble_is_averaged_before_error_and_zero_demand_is_undefined(self):
        tables = build_tables({342: artificial(342, zero=True)})
        selected = (
            tables["models"]
            .query("part == 'prediction' and band == 'all' and station == 'ATU1'")
            .set_index("seed")
        )
        self.assertEqual(selected.loc["ensemble", "H_rmse_mm"], 0)
        self.assertEqual(selected.loc["0", "H_rmse_mm"], 1)
        self.assertTrue(tables["demand"].need_rms_over_training.isna().all())

    def test_range_excess_reports_frequency_and_magnitude(self):
        result = demand_stats(np.array([-4.0, 0.0, 8.0]), np.array([-1.0, 2.0]))
        self.assertEqual(result["outside_training_count"], 2)
        self.assertEqual(result["maximum_excess_mm"], 6)
        self.assertEqual(result["outside_training_fraction"], 2 / 3)
        self.assertAlmostEqual(
            result["need_rms_over_training"], math.sqrt((80 / 3) / 2.5)
        )

    def test_wrong_direction_ignores_zero_and_both_mse_identities_hold(self):
        r = np.array([2.0, -3.0, 0.0, 1e-8])
        full = np.array([1.0, 4.0, 0.0, 2.0])
        zero = np.zeros(4)
        result = model_stats(r, full, zero)
        self.assertEqual(result["H_wrong_direction_count"], 1)
        self.assertEqual(result["H_active_count"], 3)
        self.assertEqual(result["H0_active_count"], 0)
        self.assertAlmostEqual(
            result["H_mse_minus_P0_mm2"], result["H_mse_identity_mm2"]
        )
        self.assertAlmostEqual(
            result["H_minus_H0_mse_mm2"], result["history_mse_identity_mm2"]
        )

    def test_scalar_independent_statistics_agree_on_mixed_sign_values(self):
        rng = np.random.default_rng(823)
        r, a, b = [rng.normal(size=17) * 20 for _ in range(3)]
        for actual, expected in (
            (demand_stats(r, a), scalar_demand(r.tolist(), a.tolist())),
            (model_stats(r, a, b), scalar_models(r.tolist(), a.tolist(), b.tolist())),
        ):
            self.assertEqual(set(actual), set(expected))
            np.testing.assert_allclose(
                list(actual.values()), [expected[k] for k in actual], atol=1e-10, rtol=0
            )

    def test_table_inventory_and_lead_partition_are_complete(self):
        tables = build_tables({h: artificial(h) for h in (342, 432, 612)})
        self.assertEqual(
            {k: len(v) for k, v in tables.items()},
            dict(demand=144, models=576, lead_coverage=540),
        )
        coverage = tables["lead_coverage"].groupby("fit_days").sum(numeric_only=True)
        self.assertEqual(coverage.training_pairs.tolist(), [136, 188, 291])
        self.assertEqual(coverage.prediction.tolist(), [180, 180, 180])
        selected = (
            tables["demand"]
            .query("fit_days == 342 and part == 'prediction' and station == 'ATU1'")
            .set_index("band")
        )
        self.assertEqual(
            selected.loc[["1_30", "31_90", "91_180"], "count"].tolist(), [30, 60, 90]
        )

    def test_frozen_origin_ignores_later_labels_and_rejects_extra_rows(self):
        h = 50
        base = np.zeros((230, 4))
        features = np.ones((230, 20, 4))
        labels = np.repeat(np.arange(h)[:, None], 4, axis=1).astype(float)
        scalers = [
            Scaler(np.zeros((n, 4)), np.ones((n, 4)), np.ones((n, 4))) for n in (20, 2)
        ]
        pairs = np.array([[31, 31], [31, 210]])
        x = windows(base, features, labels, h, pairs, *scalers, "H")
        labels[31:] += 1e9
        changed = windows(base, features, labels, h, pairs, *scalers, "H")
        torch.testing.assert_close(x, changed, atol=0, rtol=0)
        with self.assertRaises(ValueError):
            windows(
                base,
                features,
                np.vstack([labels, np.zeros((1, 4))]),
                h,
                pairs,
                *scalers,
                "H",
            )

    def test_actual_network_nonzero_head_probe_is_batch_invariant(self):
        model = new_model(0)
        with torch.no_grad():
            model.output.weight.fill_(0.03)
        x = torch.randn((5, 30, 23, 1, 4), dtype=torch.float64)
        a = evaluate(model, x, 3, {"forward_calls": 0}, 2)
        b = evaluate(model, x, 2, {"forward_calls": 0}, 3)
        masked = evaluate(model, masked_inputs(x), 3, {"forward_calls": 0}, 2)
        np.testing.assert_allclose(a, b, atol=1e-10, rtol=0)
        self.assertGreater(abs(a - masked).max(), 1e-6)


if __name__ == "__main__":
    unittest.main()
