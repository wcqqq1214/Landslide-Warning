"""Public-seam tests for the fit-only MVIF initial-slope candidate."""

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

from warning.mvif_initial_slope import (  # noqa: E402
    FIT_STATUS_CANDIDATE,
    FIT_STATUS_FAILED,
    select_mvif_initial_stable_slope,
)


def _daily_mvif_curve() -> pd.DataFrame:
    """Return a smooth curve with an early 5-day uniform-stage run.

    The small deterministic perturbation keeps the profile likelihood finite.
    The source MVIF shape is approximately linear at first and then curves
    outside the Wang--An uniform interval, so the selected run is auditable.
    """

    elapsed_days = np.arange(0.0, 71.0)
    tf_days = 150.0
    displacement = (
        20.0 * np.log((tf_days + 0.25 * elapsed_days) / (tf_days - elapsed_days))
        + 10.0
        + 0.03 * np.sin(elapsed_days)
    )
    return pd.DataFrame(
        {
            "date": pd.Timestamp("2020-01-01")
            + pd.to_timedelta(elapsed_days, unit="D"),
            "displacement": displacement,
            "displacement_valid": True,
        }
    )


def _accelerating_mvif_curve() -> pd.DataFrame:
    elapsed_days = np.arange(0.0, 31.0)
    tf_days = 50.0
    displacement = (
        20.0 * np.log((tf_days + 0.25 * elapsed_days) / (tf_days - elapsed_days))
        + 10.0
        + 0.03 * np.sin(elapsed_days)
    )
    return pd.DataFrame(
        {
            "date": pd.Timestamp("2020-01-01")
            + pd.to_timedelta(elapsed_days, unit="D"),
            "displacement": displacement,
            "displacement_valid": True,
        }
    )


class MvifInitialSlopeTests(unittest.TestCase):
    def test_selects_earliest_uniform_trend_run_and_profiles_its_slope(self):
        result = select_mvif_initial_stable_slope(
            _daily_mvif_curve(),
            station="A",
            fit_end_date="2020-03-11",
        )

        self.assertEqual(result.status, FIT_STATUS_CANDIDATE)
        self.assertIsNone(result.failure_reason)
        self.assertEqual(result.uniform_segment_start_date, pd.Timestamp("2020-01-01"))
        self.assertEqual(result.uniform_segment_end_date, pd.Timestamp("2020-02-17"))
        self.assertEqual(result.n_uniform_windows, 43)
        self.assertFalse(result.uniform_segment_covers_full_fit)
        self.assertGreater(result.candidate_v_mm_per_day, 0.0)
        self.assertGreater(result.candidate_sigma_mm_per_day, 0.0)
        self.assertGreater(result.candidate_v0_mm_per_day, 0.0)
        self.assertLess(
            result.profile_v_lower_mm_per_day,
            result.candidate_v_mm_per_day,
        )
        self.assertLess(
            result.candidate_v_mm_per_day,
            result.profile_v_upper_mm_per_day,
        )
        self.assertGreater(result.profile_target_evaluations, 0)

        record = result.to_record()
        self.assertNotIn("V0", record)
        self.assertNotIn("velocity_level", record)
        self.assertNotIn("tangent_angle", record)
        self.assertNotIn("warning_level", record)

    def test_is_fit_only_when_future_rows_change(self):
        fit_curve = _daily_mvif_curve()
        post_fit_rows = pd.DataFrame(
            {
                "date": pd.to_datetime(["2030-01-01", "2030-01-02"]),
                "displacement": [-1_000_000.0, 1_000_000.0],
                "displacement_valid": [True, True],
            }
        )

        baseline = select_mvif_initial_stable_slope(
            fit_curve,
            station="A",
            fit_end_date="2020-03-11",
        )
        with_future_rows = select_mvif_initial_stable_slope(
            pd.concat([fit_curve, post_fit_rows], ignore_index=True),
            station="A",
            fit_end_date="2020-03-11",
        )

        self.assertEqual(baseline.to_record(), with_future_rows.to_record())

    def test_requires_daily_fit_dates_for_the_five_day_l_method(self):
        non_daily = _daily_mvif_curve().drop(index=10).reset_index(drop=True)

        result = select_mvif_initial_stable_slope(
            non_daily,
            station="A",
            fit_end_date="2020-03-11",
        )

        self.assertEqual(result.status, FIT_STATUS_FAILED)
        self.assertEqual(result.failure_reason, "non_daily_fit_dates_for_l_method")
        self.assertIsNone(result.candidate_v_mm_per_day)
        self.assertIsNone(result.candidate_sigma_mm_per_day)
        self.assertIsNone(result.candidate_v0_mm_per_day)

    def test_records_convexity_audit_when_no_uniform_window_exists(self):
        result = select_mvif_initial_stable_slope(
            _accelerating_mvif_curve(),
            station="A",
            fit_end_date="2020-01-31",
        )

        self.assertEqual(result.status, FIT_STATUS_FAILED)
        self.assertEqual(result.failure_reason, "no_uniform_mvif_segment")
        self.assertEqual(result.n_convexity_windows, 26)
        self.assertGreater(result.n_valid_convexity_windows, 0)
        self.assertEqual(result.n_uniform_windows, 0)
        self.assertIsNone(result.candidate_v_mm_per_day)


if __name__ == "__main__":
    unittest.main()
