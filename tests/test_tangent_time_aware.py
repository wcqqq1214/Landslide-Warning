"""Regression tests for time-aware tangent-angle kinematics."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from features.tangent_angle import (  # noqa: E402
    estimate_uniform_rate,
    tangent_angle_series,
    validate_time_index,
)


class TimeAwareTangentTests(unittest.TestCase):
    def test_tangent_raw_rate_uses_actual_elapsed_days(self):
        dates = ["2020-01-01", "2020-01-03", "2020-01-04"]
        frame = tangent_angle_series(
            [0.0, 4.0, 6.0],
            v_eq=2.0,
            smooth_window=1,
            dates=dates,
        )

        self.assertTrue(np.isnan(frame.loc[0, "raw_rate"]))
        self.assertEqual(frame.loc[1, "raw_rate"], 2.0)
        self.assertEqual(frame.loc[2, "raw_rate"], 2.0)
        self.assertAlmostEqual(frame.loc[1, "alpha_raw"], 45.0)

    def test_manual_uniform_stage_rate_uses_actual_elapsed_days(self):
        dates = ["2020-01-01", "2020-01-03", "2020-01-04", "2020-01-07"]
        result = estimate_uniform_rate(
            dates,
            [0.0, 4.0, 6.0, 10.0],
            manual_range=("2020-01-01", "2020-01-04"),
        )

        self.assertEqual(result["v_eq_mm_per_day"], 2.0)
        self.assertEqual(result["mean_abs_delta_v_mm_per_day"], 0.0)

    def test_time_index_allows_nonuniform_positive_intervals(self):
        index = validate_time_index(
            ["2020-01-01", "2020-01-03", "2020-01-04"]
        )
        self.assertEqual(len(index), 3)


if __name__ == "__main__":
    unittest.main()
