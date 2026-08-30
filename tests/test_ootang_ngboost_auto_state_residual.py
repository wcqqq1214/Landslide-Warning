"""Focused contracts for the lag-conditioned residual challenger."""

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

from warning import ootang_ngboost_auto_state_residual as residual  # noqa: E402


STATIONS = ("ATU1", "ATU2", "ATU3", "ATU4", "ATU5", "MJ1", "MJ3", "MJ9")
FEATURES = (
    "interval_z",
    "velocity_mm_per_day",
    "acceleration_mm_per_day_squared",
    "tangent_angle_degree",
)


class ResidualChallengerTests(unittest.TestCase):
    def test_registered_delta_encoding_and_33_feature_whitelist(self):
        deltas = np.repeat(residual.DELTA_VALUES, residual.EXPECTED_DELTA_COUNTS)
        lag_by_delta = {-2: 2, -1: 1, 0: 2, 1: 3}
        lag = np.array([lag_by_delta[int(value)] for value in deltas])
        frame = pd.DataFrame(
            {
                "fold": 1,
                "truth_status": "valid",
                "lag7_actual_level": lag,
                "actual_level": lag + deltas,
            }
        )

        mask, encoded = residual._delta_training_rows(frame)
        expected_features = tuple(
            f"{station}__{feature}" for station in STATIONS for feature in FEATURES
        ) + ("lag7_state_level",)

        self.assertTrue(mask.all())
        self.assertEqual(np.bincount(encoded, minlength=4).tolist(), [4, 49, 177, 43])
        self.assertEqual(residual._feature_names(), expected_features)
        self.assertEqual(len(expected_features), 33)

    def test_delta_probabilities_zero_infeasible_mass_and_map_exactly(self):
        delta_probabilities = np.array(
            [[0.1, 0.2, 0.3, 0.4], [0.1, 0.2, 0.3, 0.4], [0.1, 0.2, 0.3, 0.4]]
        )
        np.testing.assert_array_equal(
            residual._validate_delta_probabilities(delta_probabilities, 3),
            delta_probabilities,
        )

        mapped = residual._map_delta_probabilities(
            delta_probabilities, np.array([0, 4, 2])
        )

        expected = np.array(
            [
                [3 / 7, 4 / 7, 0, 0, 0],
                [0, 0, 1 / 6, 1 / 3, 1 / 2],
                [0.1, 0.2, 0.3, 0.4, 0],
            ]
        )
        np.testing.assert_allclose(mapped, expected)
        np.testing.assert_allclose(mapped.sum(axis=1), 1.0)

    def test_all_dates_use_first_seven_v1_fallback_then_residual_probabilities(self):
        rows = []
        for fold in (1, 2, 3):
            start = pd.Timestamp("2020-01-01") + pd.Timedelta(days=(fold - 1) * 287)
            for offset in range(287):
                rows.append(
                    {
                        "fold": fold,
                        "date": start + pd.Timedelta(days=offset),
                        "truth_status": "valid",
                        "actual_level": 0,
                        "transition_truth": False,
                        "lag7_actual_level": np.nan if offset < 7 else 0,
                    }
                )
        site_frame = pd.DataFrame(rows)
        fallback = site_frame.loc[:, ["fold", "date"]].copy()
        for index, column in enumerate(residual.PROBABILITY_COLUMNS):
            fallback[column] = 1.0 if index == 0 else 0.0

        residual_probabilities = np.zeros((840, 5), dtype=float)
        residual_probabilities[:, 4] = 1.0
        result = residual._compose_all_predictions(
            site_frame, residual_probabilities, fallback
        )

        fallback_rows = result["prediction_source"].eq("v1_fallback")
        self.assertEqual(
            result.groupby("fold")["prediction_source"]
            .apply(lambda values: int(values.eq("v1_fallback").sum()))
            .to_dict(),
            {1: 7, 2: 7, 3: 7},
        )
        self.assertTrue(result.loc[fallback_rows, "predicted_level"].eq(0).all())
        self.assertTrue(result.loc[~fallback_rows, "predicted_level"].eq(4).all())
        np.testing.assert_allclose(
            result.loc[:, residual.PROBABILITY_COLUMNS].sum(axis=1).to_numpy(), 1.0
        )


if __name__ == "__main__":
    unittest.main()
