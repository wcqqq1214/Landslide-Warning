"""Mature errors, independent regression solutions and frozen-width replay."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))
from rolling_probability.innovation import ErrorRidge, error_features, phase_run


def predictions(start, end, H):
    N = end - start
    return dict(
        origins=np.arange(start, end),
        teacher_prefixes=np.full(N, start),
        mean=np.zeros((N, H, 4)),
        raw_sigma=np.ones((N, H, 4)),
        sigma=np.ones((N, H, 4)),
        calibration_factor=np.ones((N, H, 4)),
        feedback_log_scale=np.zeros((N, H, 4)),
    )


class InnovationChecks(unittest.TestCase):
    def test_only_latest_matured_same_horizon_forecast_enters_features(self):
        history = np.arange(12.0)[:, None] + np.zeros((12, 4))
        core = predictions(10, 20, 3)
        core["mean"][:] = np.arange(10.0)[:, None, None]
        core["sigma"][:] = 2
        features, available = error_features(
            history, {"core": core}, ["core"], 10, 12, 3
        )
        np.testing.assert_array_equal(available, [True, True, False])
        np.testing.assert_array_equal(features[:, 0, 0], [5, 5.5, 0])
        core["mean"][2:] = 1e10
        future, _ = error_features(history, {"core": core}, ["core"], 10, 12, 3)
        np.testing.assert_array_equal(features, future)
        with self.assertRaises(ValueError):
            error_features(
                np.vstack([history, np.ones((1, 4)) * 1e10]),
                {"core": core},
                ["core"],
                10,
                12,
                3,
            )

    def test_incremental_fit_matches_independent_augmented_svd(self):
        rng = np.random.default_rng(41)
        x = rng.normal(size=(9, 4, 2))
        y = rng.normal(size=(9, 4))
        state = ErrorRidge(3, 2, 100, 1)
        np.testing.assert_array_equal(state.beta, 0)
        for i in range(len(x)):
            state.update(1, x[i], y[i], 102 + i, 103 + i, 104 + i)
            for p in range(4):
                A = np.vstack([x[: i + 1, p], np.eye(2)])
                b = np.r_[y[: i + 1, p], 0, 0]
                expected = np.linalg.lstsq(A, b, rcond=None)[0]
                np.testing.assert_allclose(
                    state.beta[1, p], expected, rtol=1e-12, atol=1e-12
                )
        np.testing.assert_array_equal(state.beta[0], 0)

    def test_missing_history_future_and_duplicate_updates_are_rejected(self):
        s = ErrorRidge(2, 1, 100, 1)
        before = s.state()
        for origin, target, next_origin in [
            (100, 100, 101),
            (101, 101, 101),
            (101, 102, 103),
        ]:
            with self.assertRaises(ValueError):
                s.update(0, np.ones((4, 1)), np.ones(4), origin, target, next_origin)
            self.assertEqual(before, s.state())
        s.update(0, np.ones((4, 1)), np.ones(4), 101, 101, 102)
        after = s.state()
        with self.assertRaises(ValueError):
            s.update(0, np.ones((4, 1)), np.ones(4), 101, 101, 103)
        self.assertEqual(after, s.state())

    def test_sequential_replay_preserves_core_scales_and_original_forecasts(self):
        spec = json.loads(
            (ROOT / "config/ootang_rolling_probability.v3_12.json").read_text()
        )
        start, end, H = 4, 12, 2
        spec.update(stages={"development": [start, end]}, horizons=H, primary_horizon=H)
        points = ["ATU1", "ATU5", "MJ3", "MJ1"]
        current = {
            name: predictions(start, end, H)
            for name in ["C8_ONLINE_DATA", "B_ANCHOR", "DRIFT14", "DRIFT1"]
        }

        class Recorder:
            def __init__(self):
                self.events = []

            def event(self, kind, **value):
                self.events.append((kind, value))

        recorder = Recorder()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = root / "data.csv"
            pd.DataFrame(
                {
                    "Date": pd.date_range("2016-07-01", periods=end),
                    **{
                        p + "/mm": np.arange(end, dtype=float) + j
                        for j, p in enumerate(points)
                    },
                }
            ).to_csv(data, index=False)
            spec["data"] = str(data)
            decision = phase_run(spec, "development", current, root / "run", recorder)
            self.assertEqual(
                decision["point_solves"],
                3 * 4 * ((end - start - 1) + (end - start - 3)),
            )
            with np.load(root / "run/C13_INNOV_PHYS.npz") as p:
                np.testing.assert_array_equal(
                    p["sigma"], current["C8_ONLINE_DATA"]["sigma"]
                )
                np.testing.assert_array_equal(
                    p["mean"][:2], current["C8_ONLINE_DATA"]["mean"][:2]
                )
                self.assertGreater(abs(p["mean"][-1, 0]).max(), 0)
            with np.load(root / "run/B_ANCHOR.npz") as p:
                for key in current["B_ANCHOR"]:
                    np.testing.assert_array_equal(p[key], current["B_ANCHOR"][key])
        self.assertEqual(
            [kind for kind, _ in recorder.events][1:-1],
            ["forecast_locked", "observation_released"] * (end - start),
        )


if __name__ == "__main__":
    unittest.main()
