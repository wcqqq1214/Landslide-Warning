"""Mature errors, independent regression solutions and frozen-width replay."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))
from rolling_probability.innovation import (
    ErrorRidge,
    error_features,
    fixed_error_scales,
    phase_run,
    historical_error_scales,
)


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
    def test_short_feedback_uses_previous_one_day_forecast_for_every_head(self):
        history = np.arange(12.0)[:, None] + np.zeros((12, 4))
        core = predictions(10, 20, 3)
        core["mean"][1, 0] = 3
        core["mean"][:, 1:] = 1e9
        fixed = {"core": np.ones((3, 4)) * 2}
        features, available = error_features(
            history, {"core": core}, ["core"], 10, 12, 3, fixed, 1
        )
        np.testing.assert_array_equal(available, True)
        np.testing.assert_array_equal(features, 4)
        core["mean"][2:] = -1e9
        again, _ = error_features(
            history, {"core": core}, ["core"], 10, 12, 3, fixed, 1
        )
        np.testing.assert_array_equal(features, again)
        _, available = error_features(
            history[:10], {"core": core}, ["core"], 10, 10, 3, fixed, 1
        )
        np.testing.assert_array_equal(available, False)

    def test_short_feedback_allows_early_feature_but_waits_for_long_target(self):
        state = ErrorRidge(3, 2, 100, 1, feedback_lead_days=1)
        original = ErrorRidge(3, 2, 100, 1)
        x = np.array([[[1.0, 2.0]] * 4, [[2.0, -1.0]] * 4, [[3.0, 1.0]] * 4])
        y = np.array([[2.0] * 4, [-1.0] * 4, [4.0] * 4])
        with self.assertRaises(ValueError):
            state.update(2, x[0], y[0], 101, 103, 103)
        with self.assertRaises(ValueError):
            original.update(2, x[0], y[0], 101, 103, 104)
        for i in range(3):
            state.update(2, x[i], y[i], 101 + i, 103 + i, 104 + i)
            A = np.vstack([x[: i + 1, 0], np.eye(2)])
            b = np.r_[y[: i + 1, 0], 0.0, 0.0]
            expected = np.linalg.lstsq(A, b, rcond=None)[0]
            np.testing.assert_allclose(
                state.beta[2], np.broadcast_to(expected, (4, 2)), atol=1e-12, rtol=1e-12
            )
        with self.assertRaises(ValueError):
            state.update(2, x[-1], y[-1], 103, 105, 107)

    def test_historical_units_ignore_future_labels_and_reject_boundary_target(self):
        import hashlib

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            (path / "inner").mkdir()
            verify = path / "verification.json"
            verify.write_text('{"passed": true}')
            values = np.arange(7.0)[:, None] + np.arange(4.0)[None]
            table = pd.DataFrame(
                values, columns=[p + "/mm" for p in ("ATU1", "ATU5", "MJ3", "MJ1")]
            )
            table.to_csv(path / "data.csv", index=False)
            source = dict(
                run=str(path),
                phase="inner",
                manifest_sha256="fixture",
                verification=str(verify),
                verification_sha256=hashlib.sha256(verify.read_bytes()).hexdigest(),
                model_map={"core": "old_core"},
                expected_rows=3,
            )
            spec = dict(
                normalization_sources={"development": source},
                stages={"development": [4, 7]},
                data=str(path / "data.csv"),
                points=["ATU1", "ATU5", "MJ3", "MJ1"],
                horizons=3,
                feature_rms_floor_mm=1e-6,
            )
            forecast = predictions(1, 4, 3)
            np.savez(path / "inner/old_core.npz", **forecast)
            with (
                patch("rolling_probability.innovation.checked_run", return_value=path),
                patch("rolling_probability.innovation.ROOT", path),
            ):
                units, contract, _ = historical_error_scales(spec, "development")
                expected = np.sqrt(np.mean(values[1:4] ** 2, axis=0))
                np.testing.assert_array_equal(
                    units["core"], np.broadcast_to(expected, (3, 4))
                )
                self.assertEqual(contract["input_entries"]["core"]["last_target"], 3)
                table.iloc[4:] = 1e10
                table.to_csv(path / "data.csv", index=False)
                after, _, _ = historical_error_scales(spec, "development")
                np.testing.assert_array_equal(after["core"], units["core"])
                forecast["origins"] += 1
                np.savez(path / "inner/old_core.npz", **forecast)
                with self.assertRaises(ValueError):
                    historical_error_scales(spec, "development")

    def test_fixed_units_ignore_later_scales_and_validate_initial_origin(self):
        history = np.arange(12.0)[:, None] + np.zeros((12, 4))
        core = predictions(10, 20, 3)
        core["sigma"][0] = 2
        core["sigma"][1] = 10
        fixed = fixed_error_scales({"core": core}, ["core"], 10)
        f, _ = error_features(history, {"core": core}, ["core"], 10, 12, 3, fixed)
        np.testing.assert_array_equal(f[:2], 5.5)
        core["sigma"][1:] = 1000
        f2, _ = error_features(history, {"core": core}, ["core"], 10, 12, 3, fixed)
        np.testing.assert_array_equal(f, f2)
        with self.assertRaises(ValueError):
            fixed_error_scales({"core": core}, ["core"], 11)

    def test_constant_units_have_the_declared_millimeter_ridge_solution(self):
        scale = 2.5
        past = np.array([1.0, -2.0, 3.0, -4.0])
        future = np.array([-0.2, 1.0, -2.0, 3.0])
        state = ErrorRidge(1, 1, 100, 1)
        for i in range(len(past)):
            state.update(
                0,
                np.full((4, 1), past[i] / scale),
                np.full(4, future[i] / scale),
                101 + i,
                101 + i,
                102 + i,
            )
        beta = (past @ future) / (past @ past + scale * scale)
        predicted = scale * (6.0 / scale) * state.beta[0, :, 0]
        np.testing.assert_allclose(predicted, 6 * beta, rtol=1e-13, atol=1e-13)

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
