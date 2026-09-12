import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from physics_guided.probability import crps
from physics_guided.reference import ROOT
from physics_guided_pinn.creep import CreepPinn
from physics_guided_pinn.creep_workflow import (
    build_inputs,
    evaluate_distributions,
    forward,
    gaussian_crps,
    native_replay,
    replay_audit,
    substep_background,
)
from physics_guided_pinn.trace import compile_trace


def fixture():
    n, h = 40, 32
    rng = np.random.default_rng(7)
    theta = np.zeros(54)
    theta[24:28] = 0.1
    theta[47:51] = 0.2
    moisture = rng.uniform(0.1, 0.8, (n, 4))
    rate = theta[24:28] + theta[47:51] * moisture
    saved = dict(
        theta=theta,
        mean=np.tile(np.arange(n)[:, None], (1, 4)).astype(float),
        rain_head=rng.uniform(0, 1, (n, 4)),
        moisture=moisture,
        reservoir_head=np.linspace(160, 170, n),
        forcing=np.column_stack((np.ones(n), np.full(n, 160))),
        background_rate=rate,
        plastic=np.tile(np.arange(n)[:, None], (1, 4)) * 0.1,
        force=np.ones((n, 4)),
        length=np.ones(4),
        kc=np.eye(4),
        ke=np.eye(4),
        observation_matrix=np.eye(4),
        y0=np.zeros(4),
    )
    recorded = dict(
        lcp=np.full(((n - 1) * 64, 16), 0.01), loads=np.ones(((n - 1) * 64, 12))
    )
    return saved, recorded, saved["mean"][:h].copy(), h


class WorkflowTests(unittest.TestCase):
    def test_forecast_forcing_cannot_change_fit_normalizers_or_predictions(self):
        saved, recorded, labels, h = fixture()
        a = build_inputs(saved, recorded, labels, h)
        changed = copy.deepcopy(saved)
        changed["rain_head"][h:] += 100
        changed["reservoir_head"][h:] -= 20
        changed["forcing"][h:, 1] += 10
        b = build_inputs(changed, recorded, labels, h)
        self.assertEqual(a.normalizers, b.normalizers)
        torch.manual_seed(4)
        model = CreepPinn(a.normalizers["displacement_scale"])
        with torch.no_grad():
            model.creep_net[-1].weight.fill_(0.1)
            model.plastic_net[-1].weight.fill_(0.1)
            p, q = forward(model, a), forward(model, b)
        torch.testing.assert_close(p["mean"][:h], q["mean"][:h], rtol=0, atol=1e-12)
        with self.assertRaises(ValueError):
            build_inputs(saved, recorded, np.vstack((labels, labels[:1])), h)

    def test_interpolation_and_crps_agree_with_independent_definitions(self):
        daily = torch.tensor([[0] * 4, [1] * 4, [3] * 4], dtype=torch.float64)
        b = substep_background(daily)
        torch.testing.assert_close(b[63], daily[1])
        torch.testing.assert_close(b[64], daily[1] + (daily[2] - daily[1]) / 64)
        torch.testing.assert_close(b[-1], daily[-1])
        rng = np.random.default_rng(8)
        mu, sd, y = (
            rng.normal(size=(3, 4)),
            rng.uniform(0.1, 4, (3, 4)),
            rng.normal(size=(3, 4)),
        )
        value = gaussian_crps(
            torch.tensor(mu), torch.tensor(sd), torch.tensor(y)
        ).numpy()
        np.testing.assert_allclose(
            value, crps(mu[None], sd[None], y), rtol=1e-12, atol=1e-12
        )

    def test_primary_cannot_be_replaced_by_better_diagnostic(self):
        spec = dict(
            fit_days=32,
            end_days=40,
            score_tolerance_mm=1e-6,
            approximation_gain_fraction=0.1,
        )
        y = np.zeros((40, 4))
        base = y + 1
        old = np.repeat(base[None], 3, axis=0)
        perfect = np.zeros_like(old)
        _, _, good = evaluate_distributions(
            spec,
            y,
            base,
            old,
            np.full_like(old, 2),
            perfect,
            np.full_like(old, 0.1),
            perfect,
        )
        self.assertTrue(good["passed"])
        _, _, bad = evaluate_distributions(
            spec,
            y,
            base,
            old,
            np.full_like(old, 2),
            old * 2,
            np.full_like(old, 0.1),
            perfect,
        )
        self.assertFalse(bad["passed"])
        self.assertFalse(bad["checks"]["prediction_mae_each_lower"])

    def test_original_c_accepts_longer_history_and_uses_supplied_background(self):
        with tempfile.TemporaryDirectory() as directory:
            library = compile_trace(Path(directory) / "native")
            n = 800
            inputs = dict(
                force=np.full((n, 4), -1.0),
                elastic=np.zeros((n, 4)),
                background=np.tile(np.arange(n)[:, None], (1, 4)) * 0.001,
                eta=np.ones(4),
                hardening=np.ones(4),
                tau_rest=np.ones(4),
                tau_motion=np.ones(4),
                kc=np.eye(4),
                ke=np.eye(4),
                tau_contact=1.0,
                tau_bulk=1.0,
            )
            result = native_replay(library, inputs)
            np.testing.assert_allclose(
                result["coordinates"], inputs["background"], atol=1e-12, rtol=0
            )
            spec = json.loads(
                (ROOT / "config/ootang_probability_pinn.v2_3.json").read_text()
            )
            saved = dict(
                theta=np.zeros(54),
                length=np.ones(4),
                kc=np.eye(4),
                ke=np.eye(4),
                force=inputs["force"],
                rain_head=np.zeros((n, 4)),
                observation_matrix=np.eye(4),
                y0=np.zeros(4),
            )
            report = replay_audit(result, saved, inputs["background"], spec)
            self.assertTrue(report["passed"])


if __name__ == "__main__":
    torch.set_num_threads(1)
    unittest.main()
