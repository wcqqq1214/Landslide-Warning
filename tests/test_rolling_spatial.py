"""Cross-point ordering, physical isolation and independent ridge solutions."""

import json
from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))
from rolling_probability.online import OnlineRidge
from rolling_probability.spatial import (
    SpatialDistribution,
    fit_spatial,
    spatial_features,
)


class SpatialChecks(unittest.TestCase):
    def setUp(self):
        self.spec = json.loads(
            (ROOT / "config/ootang_rolling_probability.v3_14.json").read_text()
        )
        rng = np.random.default_rng(37)
        f = rng.normal(size=(40, 3, 4, 6))
        base = rng.normal(size=(40, 3, 4))
        target = base + 0.5 * np.roll(f[..., 0], -1, axis=-1) - 0.2 * f[..., 1]
        self.training = dict(features=f, base=base, target=target)

    def test_point_order_and_physical_columns_are_isolated(self):
        f = self.training["features"]
        full = spatial_features(f, "FULL")
        data = spatial_features(f, "DATA")
        self.assertEqual(full.shape[-1], 18)
        self.assertEqual(data.shape[-1], 16)
        expected = np.concatenate(
            [f[..., 1, :6], f[..., 0, :4], f[..., 2, :4], f[..., 3, :4]], axis=-1
        )
        np.testing.assert_array_equal(full[..., 1, :], expected)
        altered = f.copy()
        altered[..., 2, 4:] += 1000
        np.testing.assert_array_equal(spatial_features(altered, "DATA"), data)
        np.testing.assert_array_equal(
            spatial_features(altered, "FULL")[..., 1, :], full[..., 1, :]
        )
        altered = f.copy()
        altered[..., 2, 0] += 7
        changed = spatial_features(altered, "FULL")[..., 1, :] - full[..., 1, :]
        wanted = np.zeros_like(changed)
        wanted[..., 10] = 7
        np.testing.assert_allclose(changed, wanted, rtol=0, atol=1e-14)

    def test_initial_svd_reload_and_learned_neighbor_effect(self):
        for arm in ("FULL", "DATA"):
            model = fit_spatial(self.training, arm, self.spec)
            f = spatial_features(self.training["features"], arm)
            for h, p in [(0, 0), (2, 1)]:
                x = f[:, h, p] / model.feature_scale[h, p]
                y = (
                    self.training["target"][:, h, p] - self.training["base"][:, h, p]
                ) / model.target_scale[h, p]
                aug = np.vstack(
                    [x, np.eye(model.dimensions) * np.sqrt(len(x) * self.spec["alpha"])]
                )
                expected = np.linalg.lstsq(
                    aug, np.r_[y, np.zeros(model.dimensions)], rcond=None
                )[0]
                np.testing.assert_allclose(
                    model.beta[h, p], expected, rtol=1e-11, atol=1e-11
                )
            mu, sd = model.predict_features(
                self.training["features"], self.training["base"]
            )
            clone = SpatialDistribution(json.loads(json.dumps(model.state)))
            for a, b in zip(
                (mu, sd),
                clone.predict_features(
                    self.training["features"], self.training["base"]
                ),
            ):
                np.testing.assert_array_equal(a, b)
            altered = self.training["features"].copy()
            altered[..., 2, 0] += 1
            changed, _ = model.predict_features(altered, self.training["base"])
            self.assertGreater(np.mean(changed[:, :, 1] - mu[:, :, 1]), 0.4)
            self.assertTrue(np.all(sd >= self.spec["probability"]["sigma_floor_mm"]))

    def test_online_spatial_update_matches_augmented_batch(self):
        model = fit_spatial(self.training, "FULL", self.spec)
        features = spatial_features(self.training["features"], "FULL")
        state = OnlineRidge(
            model, features, self.training["base"], self.training["target"], 100
        )
        f = features[-1, 1] * 1.2
        base = self.training["base"][-1, 1]
        y = base + np.arange(4.0)
        state.update(1, f, base, y, 100, 101, 102)
        for p in range(4):
            x = np.vstack([features[:, 1, p], f[p]]) / model.feature_scale[1, p]
            target = (
                np.r_[
                    self.training["target"][:, 1, p] - self.training["base"][:, 1, p],
                    y[p] - base[p],
                ]
                / model.target_scale[1, p]
            )
            aug = np.vstack(
                [
                    x,
                    np.eye(model.dimensions)
                    * np.sqrt(len(features) * self.spec["alpha"]),
                ]
            )
            expected = np.linalg.lstsq(
                aug, np.r_[target, np.zeros(model.dimensions)], rcond=None
            )[0]
            np.testing.assert_allclose(
                state.beta[1, p], expected, rtol=1e-11, atol=1e-11
            )


if __name__ == "__main__":
    unittest.main()
