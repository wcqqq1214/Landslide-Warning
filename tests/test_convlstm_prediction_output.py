"""Regression checks for the single-holdout ConvLSTM prediction audit table."""

from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "code"
if str(CODE) not in sys.path:
    sys.path.insert(0, str(CODE))

from convlstm import model as convlstm  # noqa: E402


class ForecastPredictionOutputTests(unittest.TestCase):
    def setUp(self):
        self.dates = pd.to_datetime(["2020-01-01", "2020-01-02"])
        self.stations = ["A", "B"]
        self.actual = np.array([[1.0, 2.0], [3.0, 4.0]])
        self.persistence = np.array([[0.5, 1.5], [2.5, 3.5]])
        self.p10 = np.array([[0.0, 1.0], [2.0, 3.0]])
        self.p50 = np.array([[1.0, 2.0], [3.0, 4.0]])
        self.p90 = np.array([[2.0, 3.0], [4.0, 5.0]])

    def test_rows_keep_chronology_station_pairing_and_calibration_status(self):
        rows = convlstm.forecast_prediction_rows(
            self.dates,
            "test",
            self.actual,
            self.persistence,
            self.p10,
            self.p50,
            self.p90,
            self.stations,
            calibrated_p10=self.p10 - 0.25,
            calibrated_p90=self.p90 + 0.25,
            qhat=np.array([0.25, 0.25]),
            calibrated_bounds_status="split_conformal_from_calibration",
        )

        frame = pd.DataFrame(rows)
        self.assertEqual(len(frame), 4)
        self.assertEqual(
            list(frame.columns),
            [
                "date",
                "station",
                "split",
                "actual",
                "persistence",
                "p10",
                "p50",
                "p90",
                "calibrated_p10",
                "calibrated_p90",
                "qhat_mm",
                "calibrated_bounds_status",
            ],
        )
        self.assertEqual(
            list(frame[["date", "station"]].itertuples(index=False, name=None)),
            [
                ("2020-01-01", "A"),
                ("2020-01-01", "B"),
                ("2020-01-02", "A"),
                ("2020-01-02", "B"),
            ],
        )
        self.assertTrue(frame["qhat_mm"].eq(0.25).all())
        self.assertEqual(
            set(frame["calibrated_bounds_status"]),
            {"split_conformal_from_calibration"},
        )

    def test_fit_rows_do_not_backfill_later_calibration_bounds(self):
        rows = convlstm.forecast_prediction_rows(
            self.dates,
            "fit",
            self.actual,
            self.persistence,
            self.p10,
            self.p50,
            self.p90,
            self.stations,
            calibrated_bounds_status="not_available_for_fit_diagnostic",
        )

        frame = pd.DataFrame(rows)
        self.assertTrue(frame[["calibrated_p10", "calibrated_p90", "qhat_mm"]].isna().all().all())


if __name__ == "__main__":
    unittest.main()
