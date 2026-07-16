"""Regression tests for the time-aware displacement kinematics layer."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from features.kinematics import (  # noqa: E402
    build_long_kinematics,
    compute_point_kinematics,
    summarize_kinematics,
)


class PointKinematicsTests(unittest.TestCase):
    def test_velocity_uses_actual_time_gap_and_delta_v_is_velocity_difference(self):
        frame = compute_point_kinematics(
            ["2020-01-01", "2020-01-03", "2020-01-04"],
            [0.0, 4.0, 6.0],
        )

        self.assertTrue(np.isnan(frame.loc[0, "dt_days"]))
        self.assertEqual(frame.loc[1, "dt_days"], 2.0)
        self.assertEqual(frame.loc[2, "dt_days"], 1.0)
        self.assertTrue(np.isnan(frame.loc[0, "velocity"]))
        self.assertEqual(frame.loc[1, "velocity"], 2.0)
        self.assertEqual(frame.loc[2, "velocity"], 2.0)
        self.assertTrue(np.isnan(frame.loc[0, "delta_v"]))
        self.assertTrue(np.isnan(frame.loc[1, "delta_v"]))
        self.assertEqual(frame.loc[2, "delta_v"], 0.0)
        self.assertEqual(
            frame["velocity_status"].tolist(),
            ["warmup", "valid", "valid"],
        )
        self.assertEqual(
            frame["delta_v_status"].tolist(),
            ["warmup", "warmup", "valid"],
        )

    def test_nonfinite_displacement_and_nonpositive_time_gap_are_explicitly_invalid(self):
        frame = compute_point_kinematics(
            [
                "2020-01-01",
                "2020-01-02",
                "2020-01-04",
                "2020-01-04",
                "2020-01-05",
            ],
            [0.0, np.nan, 4.0, 5.0, 6.0],
        )

        self.assertEqual(
            frame["velocity_status"].tolist(),
            [
                "warmup",
                "nonfinite_displacement",
                "nonfinite_displacement",
                "nonpositive_dt",
                "valid",
            ],
        )
        self.assertEqual(
            frame["delta_v_status"].tolist(),
            [
                "warmup",
                "warmup",
                "velocity_invalid",
                "velocity_invalid",
                "previous_velocity_invalid",
            ],
        )
        self.assertEqual(frame.loc[4, "velocity"], 1.0)
        self.assertTrue(np.isnan(frame.loc[4, "delta_v"]))


class LongKinematicsTests(unittest.TestCase):
    def test_long_frame_and_station_summary_preserve_auditable_status_fields(self):
        raw = pd.DataFrame(
            {
                "Date": ["2020-01-01", "2020-01-03", "2020-01-04"],
                "A/mm": [0.0, 4.0, 6.0],
                "B/mm": [1.0, 3.0, 6.0],
            }
        )

        long_frame = build_long_kinematics(
            raw,
            case="ootang",
            stations={"A": "A/mm", "B": "B/mm"},
        )

        self.assertEqual(len(long_frame), 6)
        self.assertEqual(
            list(long_frame.columns),
            [
                "case",
                "date",
                "station",
                "displacement",
                "dt_days",
                "displacement_valid",
                "time_status",
                "velocity",
                "velocity_status",
                "delta_v",
                "delta_v_status",
            ],
        )
        self.assertEqual(set(long_frame["station"]), {"A", "B"})

        summary = summarize_kinematics(long_frame).set_index("station")
        self.assertEqual(summary.loc["A", "n_observations"], 3)
        self.assertEqual(summary.loc["A", "n_valid_velocity"], 2)
        self.assertEqual(summary.loc["A", "n_valid_delta_v"], 1)
        self.assertEqual(
            summary.loc["A", "first_valid_velocity_date"].strftime("%Y-%m-%d"),
            "2020-01-03",
        )
        self.assertEqual(
            summary.loc["A", "first_valid_delta_v_date"].strftime("%Y-%m-%d"),
            "2020-01-04",
        )


if __name__ == "__main__":
    unittest.main()
