"""Tests for the draft Bai--Perron MVIF-trend initial-slope selector."""

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

from warning.bai_perron_initial_slope import (  # noqa: E402
    FIT_STATUS_CANDIDATE,
    FIT_STATUS_FAILED,
    MIN_SEGMENT_OBSERVATIONS,
    select_bai_perron_initial_stable_slope,
)


def _trend_with_accelerating_break() -> pd.DataFrame:
    elapsed_days = np.arange(120, dtype=float)
    displacement = np.where(
        elapsed_days < 60,
        5.0 + 0.10 * elapsed_days,
        11.0 + 0.45 * (elapsed_days - 60.0),
    )
    displacement += 0.002 * np.sin(elapsed_days)
    return pd.DataFrame(
        {
            "date": pd.Timestamp("2020-01-01")
            + pd.to_timedelta(elapsed_days, unit="D"),
            "trend_displacement": displacement,
        }
    )


def _trend_with_decelerating_break() -> pd.DataFrame:
    trend = _trend_with_accelerating_break()
    elapsed_days = np.arange(len(trend), dtype=float)
    trend["trend_displacement"] = np.where(
        elapsed_days < 60,
        5.0 + 0.10 * elapsed_days,
        11.0 + 0.05 * (elapsed_days - 60.0),
    ) + 0.002 * np.sin(elapsed_days)
    return trend


def _trend_without_break() -> pd.DataFrame:
    elapsed_days = np.arange(120, dtype=float)
    return pd.DataFrame(
        {
            "date": pd.Timestamp("2020-01-01")
            + pd.to_timedelta(elapsed_days, unit="D"),
            "trend_displacement": 5.0 + 0.10 * elapsed_days
            + 0.002 * np.sin(elapsed_days),
        }
    )


class BaiPerronInitialSlopeTests(unittest.TestCase):
    def test_selects_the_first_linear_segment_before_an_accelerating_break(self):
        result = select_bai_perron_initial_stable_slope(
            _trend_with_accelerating_break(),
            station="A",
            fit_end_date="2020-04-29",
        )

        self.assertEqual(result.status, FIT_STATUS_CANDIDATE)
        self.assertIsNone(result.failure_reason)
        self.assertGreaterEqual(result.selected_segment_count, 2)
        self.assertEqual(result.initial_segment_start_date, pd.Timestamp("2020-01-01"))
        self.assertEqual(result.first_break_date, pd.Timestamp("2020-03-01"))
        self.assertEqual(result.initial_segment_end_date, pd.Timestamp("2020-02-29"))
        self.assertAlmostEqual(result.candidate_v_mm_per_day, 0.10, places=2)
        self.assertGreater(
            result.following_segment_v_mm_per_day,
            result.candidate_v_mm_per_day,
        )

        record = result.to_record()
        self.assertNotIn("candidate_v0_mm_per_day", record)
        self.assertNotIn("velocity_level", record)
        self.assertNotIn("tangent_angle", record)
        self.assertNotIn("warning_level", record)

    def test_rejects_a_fit_period_without_a_selected_structural_break(self):
        result = select_bai_perron_initial_stable_slope(
            _trend_without_break(),
            station="A",
            fit_end_date="2020-04-29",
        )

        self.assertEqual(result.status, FIT_STATUS_FAILED)
        self.assertEqual(result.failure_reason, "no_structural_break_before_fit_cutoff")
        self.assertEqual(result.selected_segment_count, 1)
        self.assertIsNone(result.candidate_v_mm_per_day)

    def test_rejects_a_first_break_that_is_not_accelerating(self):
        result = select_bai_perron_initial_stable_slope(
            _trend_with_decelerating_break(),
            station="A",
            fit_end_date="2020-04-29",
        )

        self.assertEqual(result.status, FIT_STATUS_FAILED)
        self.assertEqual(result.failure_reason, "first_break_is_not_accelerating")
        self.assertGreaterEqual(result.selected_segment_count, 2)
        self.assertIsNone(result.candidate_v_mm_per_day)

    def test_requires_enough_rows_for_at_least_two_trimmed_segments(self):
        short = _trend_with_accelerating_break().iloc[
            : 2 * MIN_SEGMENT_OBSERVATIONS - 1
        ]

        result = select_bai_perron_initial_stable_slope(
            short,
            station="A",
            fit_end_date=short["date"].iloc[-1],
        )

        self.assertEqual(result.status, FIT_STATUS_FAILED)
        self.assertEqual(
            result.failure_reason,
            "insufficient_rows_for_bai_perron_break_selection",
        )
        self.assertIsNone(result.candidate_v_mm_per_day)

    def test_ignores_rows_after_the_fit_cutoff(self):
        trend = _trend_with_accelerating_break()
        future = pd.DataFrame(
            {
                "date": pd.to_datetime(["2030-01-01", "2030-01-02"]),
                "trend_displacement": [-1_000_000.0, 1_000_000.0],
            }
        )

        baseline = select_bai_perron_initial_stable_slope(
            trend,
            station="A",
            fit_end_date="2020-04-29",
        )
        with_future = select_bai_perron_initial_stable_slope(
            pd.concat([trend, future], ignore_index=True),
            station="A",
            fit_end_date="2020-04-29",
        )

        self.assertEqual(baseline.to_record(), with_future.to_record())


if __name__ == "__main__":
    unittest.main()
