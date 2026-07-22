"""Tests for retained historical Wang--An MVIF candidate behavior."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning.mvif_initial_slope_diagnostics import (  # noqa: E402
    DEFAULT_KINEMATICS_PATH,
    DEFAULT_PREDICTIONS_PATH,
    build_fit_mvif_initial_slope_candidates,
    write_fit_mvif_initial_slope_candidates,
)
def _fit_curve() -> pd.DataFrame:
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
            "station": "A",
            "displacement": displacement,
            "displacement_valid": True,
        }
    )


def _predictions() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": ["2020-03-10", "2020-03-11", "2020-03-12", "2020-03-13"],
            "station": ["A"] * 4,
            "split": ["fit", "fit", "calibration", "test"],
        }
    )


class MvifInitialSlopeDiagnosticArtifactTests(unittest.TestCase):
    def test_historical_helper_retains_the_archived_full_fit_audit(self):
        summary = build_fit_mvif_initial_slope_candidates(
            kinematics_path=DEFAULT_KINEMATICS_PATH,
            predictions_path=DEFAULT_PREDICTIONS_PATH,
        )

        self.assertEqual(len(summary), 8)
        self.assertEqual(
            set(summary["fit_status"]),
            {"candidate_profiled_initial_slope"},
        )
        self.assertTrue(
            summary["failure_reason"].isna().all(),
        )
        self.assertTrue(
            summary["n_uniform_segment_trend_points"].eq(summary["n_fit_rows"]).all(),
        )
        self.assertTrue(summary["uniform_segment_covers_full_fit"].all())
        self.assertNotIn("velocity_level", summary.columns)
        self.assertNotIn("warning_level", summary.columns)

    def test_writer_rejects_the_retired_wang_an_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kinematics_path = root / "kinematics.csv"
            predictions_path = root / "predictions.csv"
            fit_curve = _fit_curve()
            fit_curve.to_csv(kinematics_path, index=False)
            _predictions().to_csv(predictions_path, index=False)

            with self.assertRaisesRegex(ValueError, "retired"):
                write_fit_mvif_initial_slope_candidates(
                    kinematics_path=kinematics_path,
                    predictions_path=predictions_path,
                    output_dir=root / "artifacts",
                )


if __name__ == "__main__":
    unittest.main()
