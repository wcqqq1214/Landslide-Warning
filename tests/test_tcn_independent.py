"""No-feedback forecast contracts; no optimizer calls or final-period label reads."""

import json
import unittest

import numpy as np
import torch

from rolling_probability.data import Teacher
from short_horizon.common import ROOT
from short_horizon.data import observations
from short_horizon.physics import PhysicalBank
from tcn_short_horizon.models import TCN
from tcn_independent.engine import (
    read_spec,
    Learners,
    features,
    rollout,
    forecast_origin,
    calibrate,
)
from tcn_independent.run import arrays


class IndependentContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)
        cls.spec = read_spec("config/ootang_tcn_independent.v1_0.json")
        cls.y, cls.f, _ = observations(cls.spec, 1168)
        cls.bank = PhysicalBank(cls.f, cls.y[0], [612, 792, 1168])
        cls.learners = Learners(cls.spec)

    def teacher(self, origin, length):
        q, f, b, s, _, _ = self.bank.forecast(origin, 7 * ((length + 6) // 7))
        return Teacher(q, b, f, s["moisture"], s["rain_head"], s["reservoir_head"])

    def test_first_seven_days_match_saved_rolling_means_and_seeds(self):
        means, seeds, audit = forecast_origin(
            self.spec, self.y, self.bank, self.learners, 1168, 7
        )
        path = ROOT / self.spec["tcn_root"] / "later_exploratory"
        for name in self.spec["models"]:
            np.testing.assert_allclose(
                means[name],
                arrays(path / (name + ".npz"))["mean"][0],
                atol=1e-9,
                rtol=0,
            )
        for name in ["TCN_DIRECT", "TCN_BRES"]:
            np.testing.assert_allclose(
                seeds[name],
                arrays(path / (name + "_seed_means.npz"))["mean"][:, 0],
                atol=1e-9,
                rtol=0,
            )
        self.assertTrue(audit["future_forcing_constant"])

    def test_later_displacements_cannot_change_historical_independent_forecast(self):
        a, _, _ = forecast_origin(self.spec, self.y, self.bank, self.learners, 806, 21)
        changed = self.y.copy()
        changed[806:] = 1e8
        b, _, _ = forecast_origin(self.spec, changed, self.bank, self.learners, 806, 21)
        for name in a:
            np.testing.assert_array_equal(a[name], b[name])

    def test_later_forcing_cannot_change_independent_scenario(self):
        forcing = self.f.copy()
        forcing[806:, 0] += 100
        forcing[806:, 1] += 0.5
        altered = PhysicalBank(forcing, self.y[0], [612, 792])
        a, _, _ = forecast_origin(self.spec, self.y, self.bank, self.learners, 806, 21)
        b, _, _ = forecast_origin(self.spec, self.y, altered, self.learners, 806, 21)
        for name in a:
            np.testing.assert_allclose(a[name], b[name], atol=1e-9, rtol=0)

    def test_zero_residual_falls_back_for_entire_293_days(self):
        oldspec = json.loads((ROOT / self.spec["tcn_config"]).read_text())
        net = TCN(oldspec).eval()
        _, scale = self.learners.groups[1168]["TCN_BRES"]
        teacher = self.teacher(1168, 293)
        with torch.no_grad():
            mu, trace = rollout(
                self.y,
                teacher,
                293,
                lambda d: net(scale.tensors(d), scale, "TCN_BRES")[0].numpy()[0],
                True,
            )
        expected = self.y[-1] + teacher.mean[1168:1461] - teacher.mean[1167]
        np.testing.assert_allclose(mu, expected, atol=1e-9, rtol=0)
        self.assertEqual(len(trace), 42)
        self.assertEqual(trace[-1]["model_origin"], 1455)

    def test_zero_direct_retains_last_observation(self):
        oldspec = json.loads((ROOT / self.spec["tcn_config"]).read_text())
        net = TCN(oldspec).eval()
        _, scale = self.learners.groups[1168]["TCN_DIRECT"]
        with torch.no_grad():
            mu, _ = rollout(
                self.y,
                self.teacher(1168, 293),
                293,
                lambda d: net(scale.tensors(d), scale, "TCN_DIRECT")[0].numpy()[0],
            )
        np.testing.assert_array_equal(mu, np.tile(self.y[-1], (293, 1)))

    def test_saved_model_reload_and_autoregressive_history(self):
        teacher = self.teacher(806, 21)
        fresh = Learners(self.spec)
        a, tr = rollout(
            self.y[:806],
            teacher,
            21,
            lambda d: self.learners.block(792, "TCN_BRES", 0, d),
            True,
        )
        b, _ = rollout(
            self.y[:806], teacher, 21, lambda d: fresh.block(792, "TCN_BRES", 0, d)
        )
        np.testing.assert_array_equal(a, b)
        d = features(np.concatenate([self.y[:806], a[:7]]), teacher)
        np.testing.assert_array_equal(d["last_y"][0], a[6])
        self.assertEqual([r["observed_prefix"] for r in tr], [806] * 3)
        self.assertEqual([r["model_origin"] for r in tr], [806, 813, 820])

    def test_all_293_calibration_pools_mature_before_split(self):
        origins = np.arange(786, 1168)
        # Synthetic arrays are confined to this boundary test, never figure data.
        pred = np.zeros((382, 293, 4))
        pred[origins[:, None] + np.arange(293)[None, :] >= 1168] = np.nan
        sd, pools = calibrate(origins, {"example": pred}, self.y, 1168)
        for k in range(293):
            target_indices = np.arange(1078, 1168)
            np.testing.assert_array_equal(pools[k] + k, target_indices)
            expected = np.sqrt(np.mean(self.y[target_indices] ** 2, axis=0))
            np.testing.assert_allclose(sd["example"][k], expected, atol=1e-10, rtol=0)
        self.assertEqual(pools.shape, (293, 90))

    def test_nonfinite_failure_is_preserved_as_missing_tail(self):
        count = 0

        def failing_predictor(data):
            nonlocal count
            count += 1
            return (
                np.tile(data["last_y"][0], (7, 1))
                if count == 1
                else np.full((7, 4), np.nan)
            )

        mu, trace = rollout(self.y, self.teacher(1168, 21), 21, failing_predictor, True)
        self.assertTrue(np.isfinite(mu[:7]).all())
        self.assertTrue(np.isnan(mu[7:]).all())
        self.assertIn("nonfinite", trace[-1]["status"])


if __name__ == "__main__":
    unittest.main()
