"""Public-seam tests for the fit-only MVIF identifiability diagnostic."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning import mvif  # noqa: E402
from warning.mvif import fit_mvif_trend  # noqa: E402


def _mvif_frame(
    *,
    a: float,
    b: float,
    tf_days: float,
    c: float,
    elapsed_days: np.ndarray,
) -> pd.DataFrame:
    displacement = a * np.log(
        (tf_days - b * elapsed_days) / (tf_days - elapsed_days)
    ) + c
    return pd.DataFrame(
        {
            "date": pd.Timestamp("2020-01-01")
            + pd.to_timedelta(elapsed_days, unit="D"),
            "displacement": displacement,
            "displacement_valid": True,
        }
    )


class MvifTrendFitTests(unittest.TestCase):
    def test_accepts_a_known_finite_mvif_curve_without_creating_v0(self):
        elapsed_days = np.arange(0.0, 101.0)
        frame = _mvif_frame(
            a=20.0,
            b=-0.25,
            tf_days=200.0,
            c=10.0,
            elapsed_days=elapsed_days,
        )

        result = fit_mvif_trend(
            frame,
            station="A",
            fit_end_date="2020-04-10",
        )

        self.assertEqual(result.status, "candidate_finite_tf")
        self.assertIsNone(result.failure_reason)
        self.assertAlmostEqual(result.a, 20.0, places=6)
        self.assertAlmostEqual(result.b, -0.25, places=6)
        self.assertAlmostEqual(result.c, 10.0, places=6)
        self.assertAlmostEqual(result.tf_elapsed_days, 200.0, places=6)
        self.assertEqual(result.n_multistart_attempts, 6)
        self.assertEqual(result.n_converged_fits, 6)
        self.assertNotIn("V0", result.to_record())
        self.assertNotIn("velocity_level", result.to_record())

    def test_fails_instead_of_fabricating_a_failure_time_for_linear_data(self):
        elapsed_days = np.arange(0.0, 101.0)
        frame = pd.DataFrame(
            {
                "date": pd.Timestamp("2020-01-01")
                + pd.to_timedelta(elapsed_days, unit="D"),
                "displacement": 10.0 + 0.5 * elapsed_days,
                "displacement_valid": True,
            }
        )

        result = fit_mvif_trend(
            frame,
            station="A",
            fit_end_date="2020-04-10",
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_reason, "mvif_optimization_failed")
        self.assertIsNone(result.tf_elapsed_days)
        self.assertEqual(result.n_near_optimal_fits, 0)

    def test_ignores_displacement_rows_after_the_fit_cutoff(self):
        elapsed_days = np.arange(0.0, 101.0)
        fit_frame = _mvif_frame(
            a=20.0,
            b=-0.25,
            tf_days=200.0,
            c=10.0,
            elapsed_days=elapsed_days,
        )
        post_fit = pd.DataFrame(
            {
                "date": pd.to_datetime(["2030-01-01", "2030-01-02"]),
                "displacement": [-1_000_000.0, 1_000_000.0],
            }
        )

        fit_only = fit_mvif_trend(
            fit_frame,
            station="A",
            fit_end_date="2020-04-10",
        )
        with_post_fit_rows = fit_mvif_trend(
            pd.concat([fit_frame, post_fit], ignore_index=True),
            station="A",
            fit_end_date="2020-04-10",
        )

        self.assertEqual(fit_only.to_record(), with_post_fit_rows.to_record())

    def test_rejects_an_invalid_cumulative_displacement_flag_in_fit_period(self):
        elapsed_days = np.arange(0.0, 101.0)
        frame = _mvif_frame(
            a=20.0,
            b=-0.25,
            tf_days=200.0,
            c=10.0,
            elapsed_days=elapsed_days,
        )
        frame["displacement_valid"] = True
        frame.loc[20, "displacement_valid"] = False

        result = fit_mvif_trend(
            frame,
            station="A",
            fit_end_date="2020-04-10",
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(
            result.failure_reason,
            "invalid_displacement_flag_in_fit_period",
        )
        self.assertIsNone(result.tf_elapsed_days)

    def test_requires_the_cumulative_displacement_validity_flag(self):
        elapsed_days = np.arange(0.0, 101.0)
        frame = _mvif_frame(
            a=20.0,
            b=-0.25,
            tf_days=200.0,
            c=10.0,
            elapsed_days=elapsed_days,
        ).drop(columns="displacement_valid")

        with self.assertRaisesRegex(ValueError, "displacement_valid"):
            fit_mvif_trend(
                frame,
                station="A",
                fit_end_date="2020-04-10",
            )

    def test_fails_when_any_deterministic_start_does_not_return_an_optimizer_fit(self):
        elapsed_days = np.arange(0.0, 101.0)
        frame = _mvif_frame(
            a=20.0,
            b=-0.25,
            tf_days=200.0,
            c=10.0,
            elapsed_days=elapsed_days,
        )
        original_least_squares = mvif.least_squares
        call_count = 0

        def fail_only_first_start(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("simulated deterministic-start failure")
            return original_least_squares(*args, **kwargs)

        with patch("warning.mvif.least_squares", side_effect=fail_only_first_start):
            result = fit_mvif_trend(
                frame,
                station="A",
                fit_end_date="2020-04-10",
            )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_reason, "mvif_optimization_failed")
        self.assertEqual(result.n_multistart_attempts, 6)
        self.assertEqual(result.n_converged_fits, 5)
        self.assertIsNone(result.tf_elapsed_days)


if __name__ == "__main__":
    unittest.main()
