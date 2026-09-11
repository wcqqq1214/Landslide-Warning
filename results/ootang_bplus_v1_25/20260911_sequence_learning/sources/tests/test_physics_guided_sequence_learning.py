"""Bounded synthetic checks of supervision, paired state controls and verification."""

import copy
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
import torch

from physics_guided.features import Scaler
from physics_guided.training import optimizer
from physics_guided_sample_learning.core import POINTS, score_components
from physics_guided_sequence.core import Budget, make_sequence
from physics_guided_sequence_learning.audit import (
    close,
    comparisons,
    probability_rows,
    table_close,
    verify_logs,
)
from physics_guided_sequence_learning.core import (
    block_losses,
    history_diagnostics,
    make_batch,
    mean_step,
    new_model,
    output,
)
from physics_guided_sequence_learning.reference import predict as reference_predict
from physics_guided_sequence_learning.support import (
    STRATEGIES,
    original_table,
    read_table,
    specification,
)
from physics_guided_sequence_learning.verify import manual_losses
from physics_guided_forecast_error.artifacts import ROOT


def fixture():
    day = np.arange(240, dtype=float)
    base = day[:, None] * np.arange(1, 5)[None] / 7
    features = np.sin(day[:, None, None] / 11 + np.arange(80).reshape(1, 20, 4))
    labels = base[:60] + 0.8 * np.cos(day[:60, None] / 9 + np.arange(4)[None])
    scalers = (
        Scaler(np.zeros((20, 4)), np.ones((20, 4)), np.ones((20, 4))),
        Scaler(np.zeros((2, 4)), np.ones((2, 4)), np.ones((2, 4))),
    )
    pool = {60: (base, features), 50: (base + 2, features + 0.1)}
    table = pd.DataFrame(
        dict(
            origin=[31, 31, 31, 45, 45],
            target=[31, 35, 33, 46, 51],
            teacher_IN=[60] * 5,
            teacher_OOF=[60, 60, 50, 50, 50],
            block=[0, 0, 1, 1, 1],
            weight=[0.25, 0.25, 1 / 6, 1 / 6, 1 / 6],
        )
    )
    return table, pool, labels, scalers


def probe():
    model = new_model(0)
    with torch.no_grad():
        model.output.weight.copy_(torch.linspace(-0.01, 0.01, 16).reshape(1, 16, 1, 1))
        model.gates.bias.fill_(0.15)
    return model


def limits(batch, updates=1, calls=1, gradients=2):
    b, horizon = batch.decoder.shape[:2]
    return dict(
        neural_updates=updates,
        model_calls=calls,
        gradient_calls=gradients,
        sequence_segments=2 * calls,
        cell_sample_steps=b * (30 + horizon) * calls,
        readout_sample_steps=b * horizon * calls,
    )


class LearningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)

    def query_equivalence(self, variant):
        table, pool, labels, scalers = fixture()
        batch = make_batch(table, pool, labels, scalers, "teacher_OOF", True)
        model = probe()
        batched = output(model, batch.encoder, batch.decoder, variant)
        separate, objectives = [], [[], []]
        for row in table.itertuples():
            sequence = make_sequence(
                *pool[row.teacher_OOF],
                labels[: row.origin],
                row.origin,
                row.target - row.origin + 1,
                *scalers,
            )
            correction = output(model, sequence.encoder, sequence.decoder, variant)[
                0, -1
            ]
            separate.append(correction)
            loss = (
                (
                    (
                        sequence.baseline[0, -1]
                        + correction
                        - torch.tensor(labels[row.target])
                    )
                    / 100
                )
                .square()
                .mean()
            )
            objectives[row.block].append(2 * row.weight * loss)
        np.testing.assert_allclose(
            batched[batch.row_batch, batch.row_lead].detach(),
            torch.stack(separate).detach(),
            rtol=1e-12,
            atol=1e-12,
        )
        losses = block_losses(batch, batched)
        for block in (0, 1):
            separate_loss = sum(objectives[block])
            close(losses[block].detach(), separate_loss.detach(), atol=1e-14)
            full = torch.autograd.grad(
                losses[block], tuple(model.parameters()), retain_graph=True
            )
            individual = torch.autograd.grad(
                separate_loss, tuple(model.parameters()), retain_graph=True
            )
            for a, b in zip(full, individual):
                np.testing.assert_allclose(a, b, atol=1e-12, rtol=1e-10)

    def test_carry_matches_individual_queries_and_full_gradients(self):
        self.query_equivalence("CARRY")

    def test_reset_matches_individual_queries_and_full_gradients(self):
        self.query_equivalence("RESET")

    def test_teacher_and_origin_define_groups_and_keep_original_weight(self):
        table, pool, labels, scalers = fixture()
        batch = make_batch(table, pool, labels, scalers, "teacher_OOF", True)
        self.assertEqual(batch.groups, [(60, 31), (50, 31), (50, 45)])
        self.assertEqual(batch.lengths, [5, 3, 7])
        np.testing.assert_array_equal(batch.weights, table.weight)
        correction = np.zeros(batch.baseline.shape)
        close(
            block_losses(batch, torch.tensor(correction)),
            manual_losses(batch, correction),
            atol=1e-14,
        )

    def test_unqueried_and_padding_outputs_have_no_loss_or_gradient(self):
        table, pool, labels, scalers = fixture()
        batch = make_batch(table, pool, labels, scalers, "teacher_OOF", True)
        correction = torch.zeros_like(batch.baseline, requires_grad=True)
        selected = torch.zeros(batch.baseline.shape[:2], dtype=torch.bool)
        selected[batch.row_batch, batch.row_lead] = True
        changed = correction.clone()
        changed[~selected] = 1e6
        self.assertTrue(
            torch.equal(block_losses(batch, correction), block_losses(batch, changed))
        )
        (gradient,) = torch.autograd.grad(
            block_losses(batch, correction).sum(), correction
        )
        self.assertEqual(gradient[~selected].count_nonzero(), 0)

    def test_padding_cannot_affect_earlier_selected_forecasts(self):
        table, pool, labels, scalers = fixture()
        batch = make_batch(table, pool, labels, scalers, "teacher_OOF", True)
        changed = batch.decoder.clone()
        for b, length in enumerate(batch.lengths):
            changed[b, length:] = 9
        model = probe()
        for variant in ("CARRY", "RESET"):
            a = output(model, batch.encoder, batch.decoder, variant)
            b = output(model, batch.encoder, changed, variant)
            self.assertTrue(
                torch.equal(
                    a[batch.row_batch, batch.row_lead],
                    b[batch.row_batch, batch.row_lead],
                )
            )

    def test_labels_after_origins_change_targets_but_not_inputs(self):
        table, pool, labels, scalers = fixture()
        before = make_batch(table, pool, labels, scalers, "teacher_OOF", True)
        labels[45:] += 10
        after = make_batch(table, pool, labels, scalers, "teacher_OOF", True)
        self.assertTrue(torch.equal(before.encoder, after.encoder))
        self.assertTrue(torch.equal(before.decoder, after.decoder))
        self.assertFalse(torch.equal(before.targets, after.targets))
        self.assertEqual(after.decoder[:, :, 20:22].count_nonzero(), 0)

    def test_prediction_pack_has_no_labels_and_invalid_supervision_is_rejected(self):
        table, pool, labels, scalers = fixture()
        table.loc[0, "target"] = 60
        with self.assertRaises(ValueError):
            make_batch(table, pool, labels, scalers, "teacher_IN", True)
        batch = make_batch(table, pool, labels, scalers, "teacher_IN", False)
        self.assertIsNone(batch.targets)
        self.assertNotIn("targets", batch.payload())
        with self.assertRaises(ValueError):
            block_losses(batch, torch.zeros_like(batch.baseline))

    def test_invalid_horizons_history_identities_and_values_are_rejected(self):
        table, pool, labels, scalers = fixture()
        for name, value in (
            ("target", 239),
            ("origin", 61),
            ("origin", 30),
            ("weight", -1),
        ):
            changed = table.copy()
            changed.loc[0, name] = value
            with self.assertRaises(ValueError):
                make_batch(changed, pool, labels, scalers, "teacher_IN", True)
        with self.assertRaises(ValueError):
            make_batch(
                table.astype({"origin": float}),
                pool,
                labels,
                scalers,
                "teacher_IN",
                True,
            )
        labels[51, 0] = np.nan
        with self.assertRaises(ValueError):
            make_batch(table, pool, labels, scalers, "teacher_IN", True)

    def test_pack_never_refits_a_scaler(self):
        table, pool, labels, scalers = fixture()
        with patch.object(Scaler, "fit", side_effect=AssertionError("forbidden refit")):
            make_batch(table, pool, labels, scalers, "teacher_IN", True)

    def test_original_tables_and_registered_budget_are_unchanged(self):
        spec = specification()
        for h in spec["prefixes"]:
            pd.testing.assert_frame_equal(
                original_table(h),
                read_table(ROOT / spec["learning_source"] / f"pairs_{h}.csv"),
                check_exact=True,
            )
        self.assertEqual(spec["main_limits"]["neural_updates"], 36 * 100)

    def test_paired_initializations_and_zero_head_preserve_bplus(self):
        table, pool, labels, scalers = fixture()
        batch = make_batch(table, pool, labels, scalers, "teacher_IN", True)
        for seed in (0, 1, 2):
            a, b = new_model(seed), new_model(seed)
            self.assertEqual(sum(p.numel() for p in a.parameters()), 7569)
            for key, value in a.state_dict().items():
                self.assertTrue(torch.equal(value, b.state_dict()[key]))
            for model, variant in ((a, "CARRY"), (b, "RESET")):
                self.assertEqual(
                    output(
                        model, batch.encoder, batch.decoder, variant
                    ).count_nonzero(),
                    0,
                )

    def test_reset_numpy_reference_resets_state_even_with_nonzero_gate_bias(self):
        table, pool, labels, scalers = fixture()
        batch = make_batch(table, pool, labels, scalers, "teacher_OOF", True)
        model = probe()
        for variant in ("CARRY", "RESET"):
            actual = (
                output(model, batch.encoder, batch.decoder, variant).detach().numpy()
            )
            budget = Budget({"reference_sample_steps": 3 * 37})
            independent = reference_predict(
                model.state_dict(),
                batch.encoder.numpy(),
                batch.decoder.numpy(),
                variant,
                budget,
            )
            close(actual, independent, atol=1e-12)
            self.assertEqual(budget.counts["reference_sample_steps"], 3 * 37)
        reset = output(model, batch.encoder, batch.decoder, "RESET")
        masked = output(model, torch.zeros_like(batch.encoder), batch.decoder, "CARRY")
        self.assertGreater((reset[:, 0] - masked[:, 0]).abs().max(), 1e-4)

    def test_complete_encoder_history_reaches_future_and_reset_cuts_only_boundary(self):
        table, pool, labels, scalers = fixture()
        batch = make_batch(table, pool, labels, scalers, "teacher_IN", True)
        model = probe()
        encoder = batch.encoder.clone().requires_grad_(True)
        carry = output(model, encoder, batch.decoder, "CARRY")
        (gradient,) = torch.autograd.grad(carry[0, -1].sum(), encoder)
        self.assertGreater(gradient[0, 0, 20:22].abs().sum(), 0)
        reset = output(model, encoder, batch.decoder, "RESET")
        (absent,) = torch.autograd.grad(reset[0, -1].sum(), encoder, allow_unused=True)
        self.assertIsNone(absent)
        changed = batch.decoder.clone()
        changed[:, 0, 0] += 1
        self.assertGreater(
            (output(model, encoder, changed, "RESET")[:, -1] - reset[:, -1])
            .abs()
            .sum(),
            0,
        )

    def test_history_diagnostics_preserve_tiny_gradients_and_reset_zeros(self):
        _, pool, labels, scalers = fixture()
        table = pd.DataFrame(dict(origin=[60], target=[239], teacher=[60]))
        batch = make_batch(table, pool, labels, scalers, "teacher", False)
        for variant in ("CARRY", "RESET"):
            budget = Budget(limits(batch, updates=0, gradients=12))
            rows = history_diagnostics(
                probe(), batch, variant, 60, "IN_" + variant, 0, POINTS, budget
            )
            self.assertEqual(len(rows), 12)
            norms = np.array([r["history_gradient_norm"] for r in rows])
            self.assertTrue(
                (norms > 0).all() if variant == "CARRY" else (norms == 0).all()
            )
            self.assertEqual(budget.counts["gradient_calls"], 12)
        with self.assertRaises(AssertionError):
            close([0.0], [1e-200], atol=0, rtol=1e-10)

    def test_one_balanced_adam_update_matches_independent_first_step(self):
        table, pool, labels, scalers = fixture()
        batch = make_batch(table, pool, labels, scalers, "teacher_IN", True)
        for variant in ("CARRY", "RESET"):
            model = probe()
            parameters = tuple(model.parameters())
            before = (
                torch.nn.utils.parameters_to_vector(parameters).detach().numpy().copy()
            )
            loss = block_losses(
                batch, output(model, batch.encoder, batch.decoder, variant)
            )
            gradients = np.stack(
                [
                    torch.cat(
                        [
                            g.reshape(-1)
                            for g in torch.autograd.grad(
                                loss[b], parameters, retain_graph=True
                            )
                        ]
                    ).numpy()
                    for b in (0, 1)
                ]
            )
            a, p = np.linalg.norm(gradients, axis=1)
            mixed = (p * gradients[0] + a * gradients[1]) / (a + p)
            clipped = mixed * min(1, 1 / (np.linalg.norm(mixed) + 1e-6))
            expected = before - 0.001 * clipped / (np.abs(clipped) + 1e-8)
            budget = Budget(limits(batch))
            mean_step(model, optimizer(model), batch, variant, budget)
            close(
                torch.nn.utils.parameters_to_vector(parameters).detach().numpy(),
                expected,
                atol=1e-12,
            )
            self.assertEqual(budget.counts, limits(batch))

    def test_budget_exhaustion_rejects_before_parameter_update(self):
        table, pool, labels, scalers = fixture()
        batch = make_batch(table, pool, labels, scalers, "teacher_IN", True)
        model = probe()
        before = copy.deepcopy(model.state_dict())
        budget = Budget(limits(batch, updates=0))
        with self.assertRaises(RuntimeError):
            mean_step(model, optimizer(model), batch, "CARRY", budget)
        for key, value in model.state_dict().items():
            self.assertTrue(torch.equal(value, before[key]))
        self.assertEqual(budget.counts["neural_updates"], 0)

    def test_independent_probability_scores_and_quantile_tamper_rejection(self):
        n = 50
        labels = np.arange(n + 180)[:, None] * np.arange(1, 5)[None] / 10
        means = np.stack([labels + delta for delta in (-1, 0.2, 0.8)])
        scales = np.array([[0.4] * 4, [0.8] * 4, [1.2] * 4])
        for mu, sigma in ((means, scales), (means[:1], scales[:1])):
            expected, dist = score_components(mu, sigma, labels, n, "SYNTHETIC")
            actual, _ = probability_rows(mu, sigma, labels, n, "SYNTHETIC", dist)
            table_close(pd.DataFrame(actual), pd.DataFrame(expected))
            dist["lower_90"][0, 0] += 0.1
            with self.assertRaises(ValueError):
                probability_rows(mu, sigma, labels, n, "SYNTHETIC", dist)

    def test_balancing_log_checks_keep_failures_and_reject_missing_steps(self):
        spec = specification()
        rows, summaries = [], []
        for h in spec["prefixes"]:
            for strategy in STRATEGIES:
                for seed in spec["seeds"]:
                    summaries.append(
                        dict(
                            fit_days=h,
                            strategy=strategy,
                            seed=seed,
                            initial_anchor_loss=100.0,
                            initial_paired_loss=200.0,
                            final_anchor_loss=150.0,
                            final_paired_loss=250.0,
                        )
                    )
                    for epoch in range(1, 101):
                        a, p = 101 - epoch, 202 - 2 * epoch
                        rows.append(
                            dict(
                                fit_days=h,
                                strategy=strategy,
                                seed=seed,
                                epoch=epoch,
                                elapsed_seconds=float(len(rows)),
                                loss=(a + p) / 2,
                                anchor_loss=float(a),
                                paired_loss=float(p),
                                anchor_norm=3.0,
                                paired_norm=4.0,
                                gradient_dot=0.0,
                                anchor_weight=4 / 7,
                                paired_weight=3 / 7,
                                balanced_loss=4 / 7 * a + 3 / 7 * p,
                                gradient_norm=12 * 2**0.5 / 7,
                                raw_anchor_slope=-36 / 7,
                                raw_paired_slope=-48 / 7,
                            )
                        )
        log, summary = pd.DataFrame(rows), pd.DataFrame(summaries)
        diagnostics = verify_logs(log, summary, spec)
        self.assertTrue((diagnostics.both_blocks_decreased == 99).all())
        self.assertFalse(diagnostics.final_anchor_below_initial.any())
        self.assertFalse(diagnostics.final_paired_below_initial.any())
        with self.assertRaises(ValueError):
            verify_logs(log.iloc[:-1], summary, spec)
        log.loc[0, "anchor_weight"] = 0.5
        with self.assertRaises(AssertionError):
            verify_logs(log, summary, spec)

    def test_comparison_requires_all_four_errors_against_bplus(self):
        rows, references = [], []
        for n in (432, 612):
            for station in POINTS:
                for part in ("train", "prediction"):
                    for strategy in (*STRATEGIES, "P0"):
                        value = (
                            5.0
                            if strategy == "P0"
                            else 6.0
                            if strategy.endswith("CARRY")
                            else 8.0
                        )
                        rows.append(
                            dict(
                                outer_days=n,
                                strategy=strategy,
                                station=station,
                                part=part,
                                rmse_mm=value,
                                mae_mm=value,
                                crps_mm=value,
                            )
                        )
                    for strategy in ("IN", "OOF", "P0"):
                        references.append(
                            dict(
                                outer_days=n,
                                strategy=strategy,
                                station=station,
                                part=part,
                                rmse_mm=10.0,
                                mae_mm=10.0,
                                crps_mm=10.0,
                            )
                        )
        metrics, reference = pd.DataFrame(rows), pd.DataFrame(references)
        result = comparisons(metrics, reference)
        self.assertFalse(result.strict_mean_improvement.any())
        self.assertTrue(
            (
                result[
                    result.strategy.str.endswith("CARRY")
                ].prediction_rmse_mm_minus_RESET
                < 0
            ).all()
        )
        mask = (
            (metrics.outer_days == 432)
            & (metrics.station == POINTS[0])
            & (metrics.strategy == "IN_CARRY")
        )
        metrics.loc[mask, ["rmse_mm", "mae_mm"]] = 4.0
        self.assertEqual(
            comparisons(metrics, reference).strict_mean_improvement.sum(), 1
        )
        metrics.loc[mask & (metrics.part == "prediction"), "mae_mm"] = 5.0 - 0.5e-6
        self.assertFalse(comparisons(metrics, reference).strict_mean_improvement.any())


if __name__ == "__main__":
    unittest.main()
