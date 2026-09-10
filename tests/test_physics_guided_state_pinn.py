from pathlib import Path
import sys
import unittest

import torch
from torch.nn.utils import parameters_to_vector, vector_to_parameters

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from physics_guided_forecast_error.artifacts import ROOT
from physics_guided_pinn.run_substep_audit import load_npz
from physics_guided_state_pinn.core import (
    Case,
    PHYSICS_KEYS,
    StatePINN,
    background_delta,
    equation_values,
    losses,
    tensor,
)


class StatePinnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)
        # Fixed physical arrays only; no solver call, observed labels or optimizer update.
        original = load_npz(
            ROOT / "results/ootang_bplus_v1_4/20260911_selection/inner_612_342_A.npz"
        )
        trace = load_npz(
            ROOT / "results/ootang_bplus_v1_8/20260911_substep_audit/342A.npz"
        )
        drivers = load_npz(
            ROOT / "results/ootang_bplus_v1_8/20260911_substep_audit/drivers.npz"
        )
        cls.n, cls.h = 48, 36
        cls.saved = {
            name: (value[: cls.n].copy() if value.shape[:1] == (522,) else value.copy())
            for name, value in original.items()
        }
        cls.trace = {
            "current": trace["current"][: (cls.n - 1) * 64].copy(),
            "loads": trace["loads"][: (cls.n - 1) * 64].copy(),
            "length": trace["length"].copy(),
        }
        cls.forcing, cls.y0 = drivers["forcing"][: cls.n].copy(), drivers["y0"].copy()

    def setUp(self):
        torch.manual_seed(3)
        self.case = Case(self.saved, self.trace, self.forcing, self.y0, self.h)
        self.model = StatePINN()

    def perturb_heads(self):
        with torch.no_grad():
            for net in (self.model.rate_net, self.model.state_net):
                net[-1].weight.normal_(0, 1e-4)
                net[-1].bias.normal_(0, 1e-4)

    def test_parameter_count_zero_correction_and_original_substep_residuals(self):
        self.assertEqual(sum(p.numel() for p in self.model.parameters()), 3576)
        output = self.model(self.case)
        torch.testing.assert_close(
            output["mean"], tensor(self.saved["mean"]), rtol=0, atol=1e-8
        )
        torch.testing.assert_close(output["state"], self.case.base, rtol=0, atol=0)
        torch.testing.assert_close(
            output["multiplier"], torch.ones_like(output["multiplier"]), rtol=0, atol=0
        )
        values = equation_values(self.case, output)
        for key in (
            "motion",
            "basal_memory",
            "contact_memory",
            "bulk_memory",
            "background",
        ):
            self.assertLess(float(abs(values[key]).max().detach()), 1e-8)

    def test_nonzero_network_preserves_initial_state_bounds_and_prefix(self):
        self.perturb_heads()
        full = self.model(self.case)
        short = self.model(self.case, days=self.h)
        chunked = self.model(self.case, chunk=73)
        torch.testing.assert_close(
            full["state"][0], torch.zeros(24, dtype=torch.float64), rtol=0, atol=0
        )
        self.assertTrue(
            bool(((full["multiplier"] >= 0.5) & (full["multiplier"] <= 2)).all())
        )
        torch.testing.assert_close(
            full["mean"][: self.h], short["mean"], rtol=0, atol=1e-8
        )
        torch.testing.assert_close(full["mean"], chunked["mean"], rtol=0, atol=1e-8)
        torch.testing.assert_close(
            full["state"][: len(short["state"])], short["state"], rtol=0, atol=1e-8
        )

    def test_background_keeps_early_day_gradient_to_later_days(self):
        rate = torch.arange(1, 25, dtype=torch.float64).reshape(6, 4) / 100
        multiplier = torch.full((6, 4), 1.1, dtype=torch.float64, requires_grad=True)
        delta = background_delta(rate, multiplier)
        gradient = torch.autograd.grad(delta[-1].sum(), multiplier)[0]
        torch.testing.assert_close(gradient[0], torch.zeros(4, dtype=torch.float64))
        torch.testing.assert_close(gradient[1:], rate[1:])

    def test_training_scales_and_predictions_ignore_future_arrays(self):
        self.perturb_heads()
        saved = {k: v.copy() for k, v in self.saved.items()}
        trace = {k: v.copy() for k, v in self.trace.items()}
        forcing = self.forcing.copy()
        for name, value in saved.items():
            if value.shape[:1] == (self.n,) and value.dtype.kind == "f":
                value[self.h :] += 1e6
        trace["current"][(self.h - 1) * 64 :] += 1e6
        trace["loads"][(self.h - 1) * 64 :] += 1e6
        forcing[self.h :] += 1e6
        changed = Case(saved, trace, forcing, self.y0, self.h)
        self.assertEqual(self.case.constants, changed.constants)
        a, b = self.model(self.case, days=self.h), self.model(changed, days=self.h)
        torch.testing.assert_close(a["state"], b["state"], rtol=0, atol=0)
        self.case.x.requires_grad_(True)
        output = self.model(self.case, days=self.h)
        labels = tensor(self.saved["mean"][: self.h] + 1.0)
        loss, _ = losses(self.case, output, labels, 17)
        grad = torch.autograd.grad(loss, self.case.x)[0]
        torch.testing.assert_close(
            grad[self.h :], torch.zeros_like(grad[self.h :]), rtol=0, atol=0
        )

    def test_loss_terms_and_gradients_of_both_networks(self):
        self.perturb_heads()
        output = self.model(self.case, days=self.h)
        labels = tensor(self.saved["mean"][: self.h] + 2.0)
        total, parts = losses(self.case, output, labels, 5)
        torch.testing.assert_close(
            parts["physics"], torch.stack([parts[k] for k in PHYSICS_KEYS]).mean()
        )
        torch.testing.assert_close(
            total, parts["data"] + 0.25 * parts["physics"] + 0.001 * parts["rate_prior"]
        )
        total.backward()
        for net in (self.model.rate_net, self.model.state_net):
            for layer in (net[0], net[-1]):
                self.assertTrue(bool(torch.isfinite(layer.weight.grad).all()))
                self.assertGreater(float(layer.weight.grad.norm()), 0)
        with self.assertRaises(ValueError):
            losses(self.case, output, tensor(self.saved["mean"]), 5)

    def test_complete_multiday_directional_gradient_with_nonzero_heads(self):
        self.perturb_heads()
        labels = tensor(self.saved["mean"][: self.h] + 1.0)

        def objective():
            return losses(self.case, self.model(self.case, days=self.h), labels, 20)[0]

        original = parameters_to_vector(self.model.parameters()).detach().clone()
        value = objective()
        gradient = torch.cat(
            [
                g.reshape(-1)
                for g in torch.autograd.grad(value, tuple(self.model.parameters()))
            ]
        )
        generator = torch.Generator().manual_seed(31415)
        try:
            for _ in range(4):
                direction = torch.randn(
                    original.shape, generator=generator, dtype=torch.float64
                )
                direction /= direction.norm()
                analytic = float(gradient @ direction)
                for step in (1e-5, 1e-6):
                    vector_to_parameters(
                        original + step * direction, self.model.parameters()
                    )
                    high = float(objective().detach())
                    vector_to_parameters(
                        original - step * direction, self.model.parameters()
                    )
                    low = float(objective().detach())
                    numeric = (high - low) / (2 * step)
                    self.assertLessEqual(
                        abs(analytic - numeric),
                        1e-5 + 1e-3 * max(abs(analytic), abs(numeric)),
                    )
        finally:
            vector_to_parameters(original, self.model.parameters())


if __name__ == "__main__":
    unittest.main()
