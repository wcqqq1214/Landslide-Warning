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
from convlstm import rolling_validation as rolling  # noqa: E402


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

    def test_station_geometry_aligns_finite_elevation_to_displacements(self):
        names, xy, elevation = convlstm.load_station_geometry(
            convlstm.DISP_COLS
        )

        self.assertEqual(
            names,
            ["MJ9", "MJ1", "MJ3", "ATU1", "ATU2", "ATU3", "ATU4", "ATU5"],
        )
        np.testing.assert_allclose(
            elevation,
            [190.0, 335.0, 425.0, 515.0, 410.0, 310.0, 285.0, 475.0],
        )
        self.assertEqual(xy.shape, (8, 2))
        self.assertTrue(np.isfinite(xy).all())
        self.assertTrue(np.isfinite(elevation).all())

    def test_model_inputs_include_static_elevation_channel(self):
        frame = pd.DataFrame(
            {
                column: [1.0, 2.0, 3.0]
                for column in convlstm.EXOG_COLS
            }
        )
        displacement = np.array(
            [
                [1.0, 10.0],
                [2.0, 20.0],
                [3.0, 30.0],
            ]
        )

        def interpolate(values):
            means = values.mean(axis=1)[:, None, None]
            return np.broadcast_to(
                means,
                (len(values), convlstm.GRID_H, convlstm.GRID_W),
            )

        elevation_grid = np.arange(
            convlstm.GRID_H * convlstm.GRID_W,
            dtype=np.float32,
        ).reshape(convlstm.GRID_H, convlstm.GRID_W)
        inputs, _ = convlstm.make_model_inputs(
            frame,
            displacement,
            2,
            interpolate,
            elevation_grid=elevation_grid,
        )

        self.assertEqual(
            inputs.shape,
            (
                len(frame),
                convlstm.MODEL_INPUT_CHANNELS,
                convlstm.GRID_H,
                convlstm.GRID_W,
            ),
        )
        np.testing.assert_allclose(
            inputs[:, 1],
            np.broadcast_to(elevation_grid, inputs[:, 1].shape),
        )

    def test_elevation_grid_changes_when_station_elevation_changes(self):
        _, xy, elevation = convlstm.load_station_geometry(
            convlstm.DISP_COLS
        )
        interpolate, _ = convlstm.make_interpolator(
            xy,
            convlstm.GRID_H,
            convlstm.GRID_W,
        )

        baseline = convlstm.make_elevation_grid(elevation, interpolate)
        changed_elevation = elevation.copy()
        changed_elevation[0] += 100.0
        changed = convlstm.make_elevation_grid(
            changed_elevation,
            interpolate,
        )

        self.assertEqual(
            baseline.shape,
            (convlstm.GRID_H, convlstm.GRID_W),
        )
        self.assertFalse(np.allclose(baseline, changed))

    def test_diagnostic_reference_rejects_legacy_input_schema(self):
        legacy = pd.DataFrame(
            {
                "model_input_schema": ["displacement_exog_v0"],
                "model_input_channels": [6],
                "station_geometry_sha256": ["legacy"],
            }
        )

        with self.assertRaisesRegex(RuntimeError, "与当前模型输入不一致"):
            rolling.require_current_model_input_provenance(
                legacy,
                artifact_name="legacy.csv",
            )


if __name__ == "__main__":
    unittest.main()
