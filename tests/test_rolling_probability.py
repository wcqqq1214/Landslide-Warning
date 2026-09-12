"""Scientific contracts for the new rolling task, using synthetic data only."""

import sys
from pathlib import Path
import unittest
import tempfile

import numpy as np
import torch
import pandas as pd
from scipy.integrate import quad
from scipy.special import ndtr

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from rolling_probability.data import Teacher, example, select_teacher, training_examples
from rolling_probability.models import (
    DirectConvLSTM,
    Scaling,
    gaussian_crps_torch,
    predict,
)
from rolling_probability.scoring import CausalCalibration, crps, interval
from rolling_probability.run import forecast_phase


def synthetic(n=360, q=100):
    t = np.arange(n, dtype=float)
    y = t[:, None] * np.array([0.1, 0.2, 0.3, 0.4]) + np.sin(t[:, None] / 20)
    b = t[:, None] * np.array([0.08, 0.18, 0.28, 0.38])
    f = np.stack([np.sin(t / 5) ** 2 * 2, 160 + np.sin(t / 90)], axis=1)
    return y, Teacher(q, b, f, np.ones((n, 4)) * 0.5, np.zeros((n, 4)), f[:, 1].copy())


class RollingContracts(unittest.TestCase):
    def test_stream_locks_forecasts_before_release_and_masks_end(self):
        y, t = synthetic()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.csv"
            frame = pd.DataFrame(
                y, columns=[p + "/mm" for p in ("ATU1", "ATU5", "MJ3", "MJ1")]
            )
            frame.insert(
                0,
                "Date",
                pd.date_range("2016-07-01", periods=len(y)).strftime("%Y-%m-%d"),
            )
            frame.to_csv(path, index=False)

            class Recorder:
                def __init__(self):
                    self.events = []

                def event(self, kind, **fields):
                    self.events.append((kind, fields))

            spec = {
                "data": str(path),
                "horizons": 5,
                "history_days": 30,
                "seeds": [0],
                "probability": {
                    "window": 90,
                    "prior_count": 10,
                    "prior_sum_squares": 10,
                    "sigma_floor_mm": 0.01,
                    "levels": [0.8, 0.9, 0.95],
                },
            }
            from rolling_probability.data import BASELINES

            scales = {k: np.ones((5, 4)) for k in BASELINES}
            r = Recorder()
            metrics, _ = forecast_phase(
                {100: t}, spec, 180, 190, {}, None, scales, Path(directory) / "first", r
            )
            self.assertEqual(
                metrics[(metrics.model == "B_ANCHOR") & (metrics.horizon == 5)].n.iloc[
                    0
                ],
                6,
            )
            events = [
                e
                for e in r.events
                if e[0] in ("forecast_locked", "observation_released")
            ]
            for i in range(10):
                self.assertEqual(events[2 * i][0], "forecast_locked")
                self.assertEqual(events[2 * i + 1][0], "observation_released")
                self.assertEqual(
                    events[2 * i][1]["available_label_last_index"], 179 + i
                )
            with np.load(Path(directory) / "first/B_ANCHOR.npz") as a:
                first = a["mean"].copy()
                self.assertTrue(np.isnan(first[-1, 1:]).all())
            frame.loc[180:, [p + "/mm" for p in ("ATU1", "ATU5", "MJ3", "MJ1")]] += 100
            frame.to_csv(path, index=False)
            forecast_phase(
                {100: t},
                spec,
                180,
                190,
                {},
                None,
                scales,
                Path(directory) / "poisoned",
                Recorder(),
            )
            with np.load(Path(directory) / "poisoned/B_ANCHOR.npz") as a:
                np.testing.assert_array_equal(first[0], a["mean"][0])
                self.assertFalse(np.array_equal(first[1], a["mean"][1]))

    def test_origin_target_alignment_and_reanchoring(self):
        y, t = synthetic()
        _, z, b = example(y[:180], t)
        np.testing.assert_allclose(b["B_ANCHOR"][0], y[179] + t.mean[180] - t.mean[179])
        np.testing.assert_allclose(
            b["B_ANCHOR"][-1], y[179] + t.mean[209] - t.mean[179]
        )
        self.assertEqual(z.shape, (30, 8, 4))

    def test_future_observation_poison_cannot_change_features(self):
        y, t = synthetic()
        original = example(y[:180], t)
        y[180:] = 1e12
        altered = example(y[:180], t)
        for a, b in zip(original[:2], altered[:2]):
            np.testing.assert_array_equal(a, b)
        for k in original[2]:
            np.testing.assert_array_equal(original[2][k], altered[2][k])

    def test_physics_teacher_fit_must_precede_origin(self):
        y, t = synthetic(q=181)
        with self.assertRaises(ValueError):
            example(y[:180], t)
        pool = {100: synthetic(q=100)[1], 200: synthetic(q=200)[1]}
        self.assertEqual(select_teacher(pool, 199).prefix, 100)
        self.assertEqual(select_teacher(pool, 200).prefix, 200)

    def test_training_labels_and_scaler_cannot_cross_prefix(self):
        y, t = synthetic()
        d = training_examples(y[:240], {100: t})
        self.assertEqual(d["origins"][-1] + 29, 239)
        np.testing.assert_array_equal(d["y"][-1, -1], y[239])
        s = Scaling(d)
        y[240:] = -1e15
        s2 = Scaling(training_examples(y[:240], {100: t}))
        for k in s.__dict__:
            np.testing.assert_array_equal(s.__dict__[k], s2.__dict__[k])

    def test_residual_velocity_control_matches_constant_residual_slope(self):
        y, t = synthetic()
        y = t.mean + np.arange(len(y))[:, None] * 0.17
        _, _, b = example(y[:180], t)
        np.testing.assert_allclose(b["B_TREND14"], y[180:210], atol=1e-12)

    def test_calibration_rejects_future_and_uses_raw_error(self):
        c = CausalCalibration(1, window=2, prior_count=1, prior_sum=1)
        with self.assertRaises(ValueError):
            c.update(0, np.ones(4), np.ones(4), 10, 10)
        c.update(0, np.ones(4) * 4, np.ones(4) * 2, 9, 10)
        np.testing.assert_allclose(c.factors(10), np.sqrt(2.5))
        c.update(0, np.zeros(4), np.ones(4), 10, 11)
        c.update(0, np.zeros(4), np.ones(4), 11, 12)
        np.testing.assert_allclose(c.factors(12), np.sqrt(1 / 3))

    def test_crps_matches_integrated_cdf_and_autograd(self):
        for y, mu, sd in ((0.0, 0.0, 1.0), (2.0, -0.4, 1.3), (-1.0, 3.0, 0.7)):
            a = quad(lambda x: ndtr((x - mu) / sd) ** 2, -np.inf, y)[0]
            b = quad(lambda x: (1 - ndtr((x - mu) / sd)) ** 2, y, np.inf)[0]
            self.assertAlmostEqual(float(crps(y, mu, sd)), a + b, places=9)
            tm = torch.tensor(mu, dtype=torch.float64, requires_grad=True)
            ts = torch.tensor(sd, dtype=torch.float64, requires_grad=True)
            loss = gaussian_crps_torch(tm, ts, torch.tensor(y))
            self.assertAlmostEqual(float(loss.detach()), a + b, places=8)
            loss.backward()
            self.assertTrue(torch.isfinite(tm.grad) and torch.isfinite(ts.grad))

    def test_interval_score_penalizes_noncoverage(self):
        cv, width, score = interval(np.array([0.0, 10.0]), np.zeros(2), np.ones(2), 0.9)
        self.assertTrue(cv[0])
        self.assertFalse(cv[1])
        self.assertEqual(score[0], width[0])
        self.assertGreater(score[1], width[1])

    def test_zero_head_is_bplus_anchor_and_ensemble_moments(self):
        torch.set_num_threads(1)
        y, t = synthetic()
        d = training_examples(y[:240], {100: t})
        s = Scaling(d)
        m = DirectConvLSTM(d["x"].shape[2], 8, 4)
        mean, sd, _, _ = predict([m, m], s, d["x"][:2], d["z"][:2], d["anchor"][:2])
        np.testing.assert_array_equal(mean, d["anchor"][:2])
        np.testing.assert_allclose(sd, np.broadcast_to(s.target_scale, (2, 30, 4)))
        loaded = DirectConvLSTM(d["x"].shape[2], 8, 4)
        loaded.load_state_dict(m.state_dict())
        a = predict([loaded], s, d["x"][:2], d["z"][:2], d["anchor"][:2])
        np.testing.assert_array_equal(mean, a[0])


if __name__ == "__main__":
    unittest.main()
