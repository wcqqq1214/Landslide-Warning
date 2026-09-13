"""Per-horizon scale indexing and immutable mean supervision."""

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))
from rolling_probability.horizon_scale import initial_residuals, predict_scale


class HorizonScaleChecks(unittest.TestCase):
    def test_changing_one_head_changes_only_its_horizon_and_point(self):
        state = dict(
            target_scale=np.ones((3, 4)).tolist(),
            scale_logs=np.zeros((3, 4, 2)).tolist(),
            sigma_floor_mm=0.01,
        )
        f = np.ones((5, 3, 4, 6))
        before = predict_scale(f, state)
        logs = np.array(state["scale_logs"])
        logs[1, 2] = [np.log(3), np.log(4)]
        state["scale_logs"] = logs.tolist()
        after = predict_scale(f, state)
        unchanged = np.ones(before.shape, dtype=bool)
        unchanged[:, 1, 2] = False
        np.testing.assert_array_equal(after[unchanged], before[unchanged])
        np.testing.assert_allclose(
            after[:, 1, 2], np.sqrt(25 + 0.0001), rtol=0, atol=1e-14
        )
        np.testing.assert_array_equal(predict_scale(f[0, :2], state), after[0, :2])

    def test_training_residuals_use_frozen_mean_and_normalization(self):
        f = np.ones((5, 3, 4, 6))
        beta = np.full((3, 4, 4), 0.25)
        model = dict(
            dimensions=4,
            feature_scale=np.full((3, 4, 4), 2.0).tolist(),
            target_scale=np.full((3, 4), 3.0).tolist(),
            beta=beta.tolist(),
        )
        train = dict(
            features=f, base=np.full((5, 3, 4), 10.0), target=np.full((5, 3, 4), 13.0)
        )
        before = repr(model)
        r, q, sy = initial_residuals(train, model)
        np.testing.assert_array_equal(r, np.full_like(r, 0.5))
        np.testing.assert_array_equal(q, np.full_like(q, 1 / 3))
        np.testing.assert_array_equal(sy, np.full((3, 4), 3.0))
        self.assertEqual(repr(model), before)


if __name__ == "__main__":
    unittest.main()
