"""Behavioral tests for fit-only automatic V0 stable-segment selection."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning.stable_segment import select_initial_stable_segment  # noqa: E402


def _kinematics_frame(velocities: list[float]) -> pd.DataFrame:
    """Build one valid/warm-up velocity trace from a compact fixture."""

    dates = pd.date_range("2020-01-01", periods=len(velocities), freq="D")
    valid = np.isfinite(velocities)
    return pd.DataFrame(
        {
            "date": dates,
            "velocity": velocities,
            "velocity_status": ["valid" if item else "warmup" for item in valid],
        }
    )


class StableSegmentSelectionTests(unittest.TestCase):
    def test_selects_the_initial_contiguous_low_speed_prefix_and_computes_v0(self):
        result = select_initial_stable_segment(
            _kinematics_frame([np.nan, 1.0, 1.0, 1.0, 10.0, 10.0]),
            station="ATU1",
            fit_end_date="2020-01-06",
        )

        self.assertEqual(result.status, "selected")
        self.assertEqual(result.station, "ATU1")
        self.assertEqual(result.n_selected_velocities, 3)
        self.assertEqual(result.segment_start_date, pd.Timestamp("2020-01-02"))
        self.assertEqual(result.segment_end_date, pd.Timestamp("2020-01-04"))
        self.assertAlmostEqual(result.first_valid_velocity, 1.0)
        self.assertAlmostEqual(result.mean_velocity, 1.0)
        self.assertAlmostEqual(result.sigma, 0.0)
        self.assertAlmostEqual(result.v0, 1.5)
        self.assertEqual(result.openmp_threads, 1)
        self.assertEqual(result.failure_reason, None)
        record = result.to_record()
        self.assertEqual(json.loads(record["cluster_centers"]), [1.0, 10.0])
        self.assertAlmostEqual(record["first_valid_velocity"], 1.0)
        self.assertAlmostEqual(record["V"], 1.0)
        self.assertAlmostEqual(record["sigma"], 0.0)
        self.assertAlmostEqual(record["V0"], 1.5)
        self.assertEqual(record["openmp_threads"], 1)

    def test_selection_does_not_use_values_after_the_fit_end_date(self):
        fitted = _kinematics_frame([np.nan, 1.0, 1.0, 1.0, 10.0, 10.0])
        with_test_tail = pd.concat(
            [
                fitted,
                pd.DataFrame(
                    {
                        "date": pd.to_datetime(["2020-01-07", "2020-01-08"]),
                        "velocity": [-1000.0, -1000.0],
                        "velocity_status": ["valid", "valid"],
                    }
                ),
            ],
            ignore_index=True,
        )

        baseline = select_initial_stable_segment(
            fitted,
            station="ATU1",
            fit_end_date="2020-01-06",
        )
        result = select_initial_stable_segment(
            with_test_tail,
            station="ATU1",
            fit_end_date="2020-01-06",
        )

        self.assertEqual(result, baseline)

    def test_refuses_to_skip_an_initial_high_speed_value_for_a_later_low_segment(self):
        result = select_initial_stable_segment(
            _kinematics_frame([np.nan, 10.0, 1.0, 1.0, 10.0, 10.0]),
            station="ATU1",
            fit_end_date="2020-01-06",
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_reason, "first_valid_velocity_not_low_speed")
        self.assertEqual(result.n_selected_velocities, 0)
        self.assertIsNone(result.v0)

    def test_invalid_rows_end_the_prefix_instead_of_being_skipped(self):
        frame = _kinematics_frame([np.nan, 1.0, 1.0, np.nan, 1.0, 1.0, 10.0, 10.0])
        frame.loc[3, "velocity_status"] = "invalid"

        result = select_initial_stable_segment(
            frame,
            station="ATU1",
            fit_end_date="2020-01-08",
        )

        self.assertEqual(result.status, "selected")
        self.assertEqual(result.n_selected_velocities, 2)
        self.assertEqual(result.segment_end_date, pd.Timestamp("2020-01-03"))

    def test_invalid_dates_are_an_explicit_selection_failure(self):
        frame = _kinematics_frame([np.nan, 1.0, 1.0, 10.0, 10.0])
        frame.loc[2, "date"] = pd.NaT

        result = select_initial_stable_segment(
            frame,
            station="ATU1",
            fit_end_date="2020-01-05",
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_reason, "invalid_date_in_input")
        self.assertIsNone(result.v0)

    def test_fails_when_the_initial_low_speed_prefix_cannot_support_a_sample_std(self):
        result = select_initial_stable_segment(
            _kinematics_frame([np.nan, 1.0, 10.0, 10.0]),
            station="ATU1",
            fit_end_date="2020-01-04",
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(
            result.failure_reason, "insufficient_initial_low_speed_samples"
        )
        self.assertEqual(result.n_selected_velocities, 1)

    def test_fails_when_the_formula_produces_a_nonpositive_v0(self):
        result = select_initial_stable_segment(
            _kinematics_frame([np.nan, -3.0, -3.0, -3.0, -1.0, -1.0]),
            station="ATU1",
            fit_end_date="2020-01-06",
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_reason, "nonpositive_v0")
        self.assertAlmostEqual(result.mean_velocity, -3.0)
        self.assertAlmostEqual(result.sigma, 0.0)
        self.assertAlmostEqual(result.v0, -3.0)

    def test_draft_policy_cannot_be_overridden_at_call_time(self):
        with self.assertRaises(TypeError):
            select_initial_stable_segment(
                _kinematics_frame([np.nan, 1.0, 1.0, 10.0, 10.0]),
                station="ATU1",
                fit_end_date="2020-01-05",
                sigma_ddof=0,
            )


if __name__ == "__main__":
    unittest.main()
