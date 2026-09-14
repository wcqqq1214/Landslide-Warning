"""Numerical and legal-information contracts, with zero optimizer updates."""

import ast
import io
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd
import torch
from torch.nn import functional as F

from sequence_conditional.core import (
    ARMS,
    ROOT,
    Scaling,
    predict,
    read_forcing,
    read_labels,
    reload_model,
    spec,
)
from sequence_conditional.models import TrajectoryModel, selective_scan
from sequence_conditional.independent import numpy_forward


def serial_scan(u, dt, A, B, C, D, z):
    h = torch.zeros(u.shape[0], u.shape[2], A.shape[1], dtype=u.dtype)
    output = []
    for i in range(u.shape[1]):
        h = (
            torch.exp(dt[:, i, :, None] * A) * h
            + dt[:, i, :, None] * B[:, i, None, :] * u[:, i, :, None]
        )
        output.append(((h * C[:, i, None, :]).sum(-1) + D * u[:, i]) * F.silu(z[:, i]))
    return torch.stack(output, dim=1)


def official_ref():
    path = ROOT / spec()["out"] / "sources/selective_scan_interface.py"
    parsed = ast.parse(path.read_text())
    fn = next(
        n
        for n in parsed.body
        if isinstance(n, ast.FunctionDef) and n.name == "selective_scan_ref"
    )

    # This fixture exercises only real, time-varying 3-D B/C; the sole needed
    # einops operation is D[:, None]. The original function AST is unchanged.
    def rearrange(x, pattern, **kwargs):
        if pattern != "d -> d 1" or kwargs:
            raise ValueError("Unexpected upstream layout in real 3-D fixture")
        return x[:, None]

    namespace = {"torch": torch, "F": F, "rearrange": rearrange}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["selective_scan_ref"]


class SequenceContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)
        cls.cfg = spec()
        rng = np.random.default_rng(13)
        cls.x = rng.normal(size=(151, 22))
        cls.y = rng.normal(size=(100, 4)).cumsum(0)
        cls.b = rng.normal(size=(151, 4)).cumsum(0)
        cls.scale = Scaling(cls.x[:100], cls.y, cfg=cls.cfg)

    def test_poison_future_labels_and_scaling(self):
        f = pd.DataFrame(
            dict(
                Date=pd.date_range("2016-07-01", periods=151),
                **{"Rainfall/mm": 2.0, "RWL/m": 160.0},
            )
        )
        for p in self.cfg["points"]:
            f[p + "/mm"] = [1.0] * 100 + ["not released"] * 51
        text = f.to_csv(index=False)
        self.assertEqual(read_labels(io.StringIO(text), 100).shape, (100, 4))
        self.assertEqual(read_forcing(io.StringIO(text), 151)[0].shape, (151, 2))
        with self.assertRaises(ValueError):
            Scaling(self.x, self.y, cfg=self.cfg)
        np.testing.assert_array_equal(self.scale.x_mean, self.x[:100].mean(0))

    def test_identical_pair_zero_fallback(self):
        for pair in self.cfg["families"].values():
            a, b = [TrajectoryModel(self.cfg, 1, arm) for arm in pair]
            for k in a.state_dict():
                torch.testing.assert_close(
                    a.state_dict()[k], b.state_dict()[k], rtol=0, atol=0
                )
            np.testing.assert_array_equal(
                predict(a, self.scale, pair[0], self.x, self.b),
                np.broadcast_to(self.y[0], self.b.shape),
            )
            np.testing.assert_array_equal(
                predict(b, self.scale, pair[1], self.x, self.b), self.b
            )

    def test_nonzero_causal_prefix_invariance(self):
        for arm in ARMS:
            model = TrajectoryModel(self.cfg, 2, arm)
            with torch.no_grad():
                model.head.weight.normal_(0, 0.1)
            full = predict(model, self.scale, arm, self.x, self.b)
            changed = self.x.copy()
            changed[100:] += 5
            alt = predict(model, self.scale, arm, changed, self.b)
            short = predict(model, self.scale, arm, self.x[:100], self.b[:100])
            np.testing.assert_allclose(full[:100], alt[:100], atol=1e-10, rtol=0)
            np.testing.assert_allclose(full[:100], short, atol=1e-10, rtol=0)
            self.assertGreater(abs(full[100:] - alt[100:]).max(), 1e-6)

    def test_reload_independent_forward_and_encoder_gradient(self):
        for arm in ARMS:
            model = TrajectoryModel(self.cfg, 0, arm)
            with torch.no_grad():
                model.head.weight.normal_(0, 0.1)
            mu = predict(model, self.scale, arm, self.x, self.b)
            model(self.scale.tensor(self.x)).square().mean().backward()
            self.assertGreater(float(model.input.weight.grad.abs().sum()), 0.0)
            ck = dict(
                state_dict=model.state_dict(), scaling=self.scale.state, seed=0, arm=arm
            )
            np.testing.assert_allclose(
                numpy_forward(ck, self.x, self.b), mu, atol=1e-9, rtol=0
            )
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "fixture.pt"
                torch.save(ck, p)
                other, scale, _ = reload_model(p, self.cfg)
                np.testing.assert_array_equal(
                    predict(other, scale, arm, self.x, self.b), mu
                )

    def test_parallel_scan_serial_value_and_gradient(self):
        torch.manual_seed(71)
        values = [
            torch.randn(1, 37, 6, dtype=torch.float64) * 0.2,
            torch.rand(1, 37, 6, dtype=torch.float64) * 0.05,
            -torch.rand(6, 4, dtype=torch.float64),
            torch.randn(1, 37, 4, dtype=torch.float64) * 0.2,
            torch.randn(1, 37, 4, dtype=torch.float64) * 0.2,
            torch.ones(6, dtype=torch.float64),
            torch.randn(1, 37, 6, dtype=torch.float64) * 0.2,
        ]
        a = [v.clone().requires_grad_() for v in values]
        b = [v.clone().requires_grad_() for v in values]
        x, y = selective_scan(*a), serial_scan(*b)
        torch.testing.assert_close(x, y, atol=1e-12, rtol=0)
        ga = torch.autograd.grad(x.square().sum(), a)
        gb = torch.autograd.grad(y.square().sum(), b)
        for u, v in zip(ga, gb):
            torch.testing.assert_close(u, v, atol=1e-10, rtol=0)

    def test_official_reference_float32_forward_and_gradient(self):
        torch.manual_seed(19)
        values = [
            torch.randn(1, 65, 32) * 0.2,
            torch.rand(1, 65, 32) * 0.05,
            -torch.rand(32, 16),
            torch.randn(1, 65, 16) * 0.2,
            torch.randn(1, 65, 16) * 0.2,
            torch.ones(32),
            torch.randn(1, 65, 32) * 0.2,
        ]
        a = [v.clone().requires_grad_() for v in values]
        b = [v.clone().requires_grad_() for v in values]
        actual = selective_scan(*a)
        u, dt, A, B, C, D, z = b
        expected = official_ref()(
            u.transpose(1, 2),
            dt.transpose(1, 2),
            A,
            B.transpose(1, 2),
            C.transpose(1, 2),
            D,
            z.transpose(1, 2),
        ).transpose(1, 2)
        torch.testing.assert_close(actual, expected, atol=2e-5, rtol=0)
        ga = torch.autograd.grad(actual.square().sum(), a)
        gb = torch.autograd.grad(expected.square().sum(), b)
        for u, v in zip(ga, gb):
            torch.testing.assert_close(u, v, atol=2e-5, rtol=0)


if __name__ == "__main__":
    unittest.main()
