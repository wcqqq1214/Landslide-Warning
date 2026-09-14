"""Meaningful information-boundary contracts; zero optimization steps."""

import io
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from tcn_conditional_trajectory.core import (
    ARMS,
    Scaling,
    TrajectoryTCN,
    calibrate,
    drift,
    predict,
    read_forcing,
    read_labels,
    reload_model,
    spec,
)


class ConditionalContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)
        cls.cfg = spec()
        rng = np.random.default_rng(13)
        cls.x = rng.normal(size=(180, 22))
        cls.y = rng.normal(size=(100, 4)).cumsum(0)
        cls.b = rng.normal(size=(180, 4)).cumsum(0)
        cls.scale = Scaling(cls.x[:100], cls.y, cfg=cls.cfg)

    def test_reader_discards_future_displacement(self):
        frame = pd.DataFrame(
            dict(
                Date=pd.date_range("2016-07-01", periods=180),
                **{"Rainfall/mm": 2.0, "RWL/m": 160.0},
            )
        )
        for p in self.cfg["points"]:
            frame[p + "/mm"] = [1.0] * 100 + ["unreleased"] * 80
        text = frame.to_csv(index=False)
        self.assertEqual(read_labels(io.StringIO(text), 100).shape, (100, 4))
        self.assertEqual(read_forcing(io.StringIO(text), 180)[0].shape, (180, 2))

    def test_scaler_requires_prefix(self):
        with self.assertRaises(ValueError):
            Scaling(self.x, self.y, cfg=self.cfg)
        np.testing.assert_allclose(self.scale.x_mean, self.x[:100].mean(0))
        np.testing.assert_allclose(self.scale.unit, np.maximum(self.y.std(0), 1))

    def test_zero_output_and_paired_initialization(self):
        a, b = TrajectoryTCN(self.cfg, 1), TrajectoryTCN(self.cfg, 1)
        for key in a.state_dict():
            torch.testing.assert_close(
                a.state_dict()[key], b.state_dict()[key], rtol=0, atol=0
            )
        np.testing.assert_array_equal(
            predict(a, self.scale, ARMS[1], self.x, self.b), self.b
        )
        np.testing.assert_array_equal(
            predict(b, self.scale, ARMS[0], self.x, self.b),
            np.broadcast_to(self.y[0], self.b.shape),
        )

    def test_nonzero_model_causal_and_chunked(self):
        model = TrajectoryTCN(self.cfg, 2)
        with torch.no_grad():
            model.head.weight.fill_(0.1)
        full = predict(model, self.scale, ARMS[0], self.x, self.b)
        changed = self.x.copy()
        changed[120:] += 4
        alt = predict(model, self.scale, ARMS[0], changed, self.b)
        np.testing.assert_array_equal(full[:120], alt[:120])
        part = predict(model, self.scale, ARMS[0], self.x[40:], self.b[40:])
        np.testing.assert_allclose(full[100:], part[60:], atol=1e-10, rtol=0)
        self.assertGreater(float(abs(full[120:] - alt[120:]).max()), 0)

    def test_reload_and_nonzero_gradient(self):
        model = TrajectoryTCN(self.cfg, 0)
        output = model(self.scale.tensor(self.x[:100]))
        (output - torch.ones_like(output)).square().mean().backward()
        self.assertGreater(float(model.head.weight.grad.abs().sum()), 0)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture.pt"
            torch.save(
                dict(
                    state_dict=model.state_dict(),
                    scaling=self.scale.state,
                    seed=0,
                    arm=ARMS[1],
                    updates=0,
                ),
                path,
            )
            other, scale, _ = reload_model(path, self.cfg)
            np.testing.assert_array_equal(
                predict(model, self.scale, ARMS[1], self.x, self.b),
                predict(other, scale, ARMS[1], self.x, self.b),
            )

    def test_calibration_and_drift_full_horizon(self):
        sd = calibrate(np.ones((90, 4)) * 2, self.cfg)
        np.testing.assert_array_equal(sd, np.ones(4) * 2)
        with self.assertRaises(ValueError):
            calibrate(np.ones((89, 4)), self.cfg)
        expected = self.y[-1] + 293 * (self.y[-1] - self.y[-2])
        np.testing.assert_array_equal(drift(self.y, 293)[-1], expected)


if __name__ == "__main__":
    unittest.main()
