"""Information boundary and paired-model contracts, without optimizer updates."""

import io
import json
import unittest
from unittest.mock import patch

import numpy as np
import torch

from rolling_probability.data import Teacher, example
from short_horizon.common import ROOT, load_spec
from short_horizon.data import load_cache, query, training, observations
from short_horizon.physics import PhysicalBank
from short_horizon.evaluation import ErrorCalibration
from tcn_short_horizon.models import TCN, Scaling, history_features, objective


class TCNContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)
        cls.spec = load_spec(ROOT / "config/ootang_tcn.v1_0.json")
        cls.cache = load_cache(dict(cls.spec, output_root=cls.spec["legacy_root"]))
        cls.tr = training(cls.cache, cls.spec, 612)
        cls.scale = Scaling(cls.tr)
        cls.y, cls.forcing, cls.dates = observations(cls.spec)
        cls.bank = PhysicalBank(cls.forcing, cls.y[0], cls.spec["teacher_prefixes"])

    def test_date_alignment_and_teacher_boundaries(self):
        for phase, (start, end) in self.spec["stages"].items():
            with self.subTest(phase=phase):
                tr = training(self.cache, self.spec, start)
                self.assertEqual(len(tr["origins"]), start - 258)
                self.assertTrue((tr["origins"] + 6 < start).all())
                self.assertTrue((tr["teacher"] <= tr["origins"]).all())
                np.testing.assert_array_equal(
                    tr["target"][-1], self.y[start - 7 : start]
                )
                for h in range(1, 8):
                    origins = np.arange(start, end - h + 1)
                    self.assertEqual(len(origins), end - start - h + 1)
                    self.assertEqual(origins[-1] + h - 1, end - 1)
        self.assertEqual(self.dates[612], "2018-03-05")
        self.assertEqual(self.dates[1168], "2019-09-12")

    def test_training_reads_only_prefix(self):
        real = observations
        calls = []

        def guarded(spec, end=None):
            calls.append(end)
            self.assertEqual(end, 612)
            return real(spec, end)

        with patch("short_horizon.data.observations", side_effect=guarded):
            tr = training(self.cache, self.spec, 612)
        self.assertEqual(calls, [612])
        self.assertTrue(np.isfinite(tr["target"]).all())

    def test_thirty_day_observation_boundary(self):
        n = 700
        q, f, b, s, _, _ = self.bank.forecast(n)
        teacher = Teacher(q, b, f, s["moisture"], s["rain_head"], s["reservoir_head"])
        x, z, _ = example(self.y[:n], teacher, horizon=7, window=30)
        altered = self.y.copy()
        altered[: n - 30] += 10000
        altered[n:] -= 20000
        xx, zz, _ = example(altered[:n], teacher, horizon=7, window=30)
        np.testing.assert_array_equal(
            history_features({"x": x[None]}), history_features({"x": xx[None]})
        )
        np.testing.assert_array_equal(z, zz)
        features = history_features({"x": x[None]})
        self.assertEqual(features.shape, (1, 30, 28))
        np.testing.assert_array_equal(features[0, 0, 4:8], 0)
        np.testing.assert_array_equal(
            features[0, -1, 4:8], self.y[n - 1] - self.y[n - 2]
        )

    def test_future_driver_isolation_and_cache(self):
        for n in (252, 342, 432, 612, 792, 1168, 1460):
            q, f, b, s, packed, _ = self.bank.forecast(n)
            old = self.bank.forcing
            changed = old.copy()
            changed[n:] = [888, 200]
            self.bank.forcing = changed
            alt = self.bank.forecast(n)
            self.bank.forcing = old
            np.testing.assert_array_equal(f, alt[1])
            np.testing.assert_array_equal(b, alt[2])
            np.testing.assert_array_equal(packed, alt[4])
            row = query(self.cache, [n])
            np.testing.assert_allclose(
                row["anchor"][0],
                self.y[n - 1] + b[n : n + 7] - b[n - 1],
                atol=1e-8,
                rtol=0,
            )
            self.assertEqual(row["teacher"][0], q)

    def test_causal_convolutions_and_shape(self):
        model = TCN(self.spec)
        torch.manual_seed(30)
        x = torch.randn(2, 28, 30, dtype=torch.float64)
        altered = x.clone()
        altered[:, :, 19:] += 100
        np.testing.assert_array_equal(
            model.encoder(x).detach().numpy()[:, :, :19],
            model.encoder(altered).detach().numpy()[:, :, :19],
        )
        output, _ = model(
            self.scale.tensors(self.tr, slice(0, 3)), self.scale, "TCN_DIRECT"
        )
        self.assertEqual(tuple(output.shape), (3, 7, 4))
        self.assertEqual(output.dtype, torch.float64)

    def test_paired_initialization_and_batches(self):
        torch.manual_seed(1)
        one = TCN(self.spec)
        torch.manual_seed(1)
        two = TCN(self.spec)
        for a, b in zip(one.parameters(), two.parameters()):
            torch.testing.assert_close(a, b, atol=0, rtol=0)
        a = np.random.default_rng(1)
        b = np.random.default_rng(1)
        for _ in range(400):
            np.testing.assert_array_equal(
                a.integers(0, 354, 64), b.integers(0, 354, 64)
            )

    def test_zero_residual_and_direct_baselines(self):
        model = TCN(self.spec)
        data = self.scale.tensors(self.tr, slice(0, 5))
        bres, _ = model(data, self.scale, "TCN_BRES")
        direct, _ = model(data, self.scale, "TCN_DIRECT")
        np.testing.assert_array_equal(bres.detach().numpy(), self.tr["anchor"][:5])
        np.testing.assert_array_equal(
            direct.detach().numpy(),
            np.broadcast_to(self.tr["last_y"][:5, None], (5, 7, 4)),
        )

    def test_nonzero_model_reload(self):
        torch.manual_seed(2)
        model = TCN(self.spec)
        with torch.no_grad():
            model.head.weight.normal_(0, 0.01)
            model.head.bias.normal_(0, 0.01)
        data = self.scale.tensors(self.tr, slice(0, 8))
        original, _ = model(data, self.scale, "TCN_BRES")
        buf = io.BytesIO()
        torch.save(model.state_dict(), buf)
        buf.seek(0)
        restored = TCN(self.spec)
        restored.load_state_dict(torch.load(buf, weights_only=True))
        scale = Scaling(state=json.loads(json.dumps(self.scale.state)))
        reload, _ = restored(data, scale, "TCN_BRES")
        np.testing.assert_array_equal(
            original.detach().numpy(), reload.detach().numpy()
        )

    def test_common_units_and_finite_loss_gradient(self):
        expected = np.sqrt(
            np.mean((self.tr["target"] - self.tr["last_y"][:, None]) ** 2, axis=0)
        )
        np.testing.assert_array_equal(
            self.scale.target_unit, np.maximum(expected, 1e-6)
        )
        model = TCN(self.spec)
        batch = self.scale.tensors(self.tr, slice(0, 64))
        for name in ("TCN_DIRECT", "TCN_BRES"):
            model.zero_grad()
            loss = objective(model, name, batch, self.scale)
            loss.backward()
            self.assertTrue(torch.isfinite(loss))
            self.assertTrue(
                all(
                    p.grad is None or torch.isfinite(p.grad).all()
                    for p in model.parameters()
                )
            )

    def test_mature_error_pool_rejects_future_and_duplicates(self):
        cal = ErrorCalibration(self.y[:612], 612)
        before = cal.scale(612).copy()
        with self.assertRaises(ValueError):
            cal.update(6, np.ones(4), 612, 618, 618)
        cal.update(0, np.zeros(4), 612, 612, 613)
        after = cal.scale(613)
        np.testing.assert_array_equal(before[1:], after[1:])
        with self.assertRaises(ValueError):
            cal.update(0, np.ones(4), 612, 612, 613)


if __name__ == "__main__":
    unittest.main()
