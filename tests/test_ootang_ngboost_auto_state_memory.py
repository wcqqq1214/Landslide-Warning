"""Focused contracts for the lag-seven state-memory challenger."""

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

from warning import ootang_ngboost_auto_state_memory as memory  # noqa: E402


STATIONS = ("ATU1", "ATU2", "ATU3", "ATU4", "ATU5", "MJ1", "MJ3", "MJ9")
FEATURES = (
    "interval_z",
    "velocity_mm_per_day",
    "acceleration_mm_per_day_squared",
    "tangent_angle_degree",
)


class AutomaticStateMemoryTests(unittest.TestCase):
    def test_lag7_memory_uses_same_fold_truth_and_minus_one_for_first_seven_days(self):
        rows = []
        specifications = (
            (1, pd.Timestamp("2020-01-01"), [0, 1, 2, 3, 4, 0, 1, 2, 3]),
            (2, pd.Timestamp("2020-01-10"), [4, 3, 2, 1, 0, 4, 3, 2, 1]),
        )
        for fold, start, levels in specifications:
            for offset, level in enumerate(levels):
                date = start + pd.Timedelta(days=offset)
                rows.append(
                    {
                        "fold": fold,
                        "date": date,
                        "target_end_date": date + pd.Timedelta(days=7),
                        "truth_status": "valid",
                        "actual_level": level,
                    }
                )

        source = pd.DataFrame(rows)
        result = memory._attach_lag7_memory(source)

        for fold, _, levels in specifications:
            group = result.loc[result["fold"].eq(fold)].sort_values("date")
            self.assertEqual(
                group[memory.MEMORY_FEATURE].tolist(),
                [-1.0] * 7 + [float(levels[0]), float(levels[1])],
            )
            self.assertEqual(
                group["lag7_actual_level"].notna().tolist(),
                [False] * 7 + [True, True],
            )

        wrong_maturity = source.copy()
        wrong_maturity.loc[
            wrong_maturity["fold"].eq(2)
            & wrong_maturity["date"].eq(pd.Timestamp("2020-01-10")),
            "target_end_date",
        ] = pd.Timestamp("2020-01-18")
        with self.assertRaisesRegex(
            memory.artifact_io.AutoStateInputError,
            "source target does not mature at issue date",
        ):
            memory._attach_lag7_memory(wrong_maturity)

    def test_memory_model_matrix_is_only_the_32_whitelisted_inputs_plus_lag7_state(
        self,
    ):
        expected_base = tuple(
            f"{station}__{feature}" for station in STATIONS for feature in FEATURES
        )
        expected = (*expected_base, "lag7_state_level")
        row = {name: float(index) for index, name in enumerate(expected)}
        row.update(
            {
                "actual_level": 4,
                "severity": 99.0,
                "future_velocity_q90_mm_per_day": 98.0,
                "target_end_date": "2020-01-08",
            }
        )
        frame = pd.DataFrame([row])

        feature_names = memory._memory_feature_names()
        X = frame.loc[:, feature_names].to_numpy(dtype=float)

        self.assertEqual(feature_names, expected)
        self.assertEqual(X.shape, (1, 33))
        self.assertEqual(X[0, -1], 32.0)
        forbidden = {"actual", "severity", "future", "target", "color", "prob"}
        self.assertFalse(
            any(token in name.lower() for name in feature_names for token in forbidden)
        )

    def test_common_mask_has_273_days_and_transition_resets_at_fold_boundary(self):
        rows = [
            {
                "fold": 1,
                "date": pd.Timestamp("2020-12-31"),
                "target_end_date": pd.Timestamp("2021-01-07"),
                "truth_status": "valid",
                "actual_level": 0,
            }
        ]
        start = pd.Timestamp("2021-01-01")
        for offset in range(287):
            valid = offset < 280
            rows.append(
                {
                    "fold": 2,
                    "date": start + pd.Timedelta(days=offset),
                    "target_end_date": (
                        start + pd.Timedelta(days=offset + 7) if valid else pd.NaT
                    ),
                    "truth_status": "valid" if valid else "unavailable_fold_terminal",
                    "actual_level": (4 if offset < 7 else 3) if valid else pd.NA,
                }
            )
        rows.append(
            {
                "fold": 3,
                "date": pd.Timestamp("2022-01-01"),
                "target_end_date": pd.Timestamp("2022-01-08"),
                "truth_status": "valid",
                "actual_level": 2,
            }
        )

        frame = memory._attach_lag7_memory(pd.DataFrame(rows))
        masks = memory._comparison_masks(frame, fold=2)
        fold2 = frame.loc[frame["fold"].eq(2)].sort_values("date")

        self.assertEqual(int(masks["all_valid"].sum()), 280)
        self.assertEqual(int(masks["lag7_common"].sum()), 273)
        self.assertEqual(int(masks["transition_common"].sum()), 1)
        self.assertFalse(bool(fold2.iloc[0]["transition_truth"]))
        self.assertTrue(bool(fold2.iloc[7]["transition_truth"]))
        self.assertTrue(np.all(masks["transition_common"] <= masks["lag7_common"]))

        fold2_valid = frame.loc[masks["all_valid"]]
        fold2_common = frame.loc[masks["lag7_common"]]

        def prediction_rows(
            source: pd.DataFrame, estimator: str, predicted: pd.Series
        ) -> pd.DataFrame:
            result = source.loc[:, ["fold", "date", "actual_level"]].copy()
            result["estimator"] = estimator
            result["prediction_status"] = "available"
            result["predicted_level"] = predicted.to_numpy(dtype=int)
            probabilities = np.zeros((len(result), 5), dtype=float)
            probabilities[np.arange(len(result)), result["predicted_level"]] = 1.0
            for index, column in enumerate(memory.PROBABILITY_COLUMNS):
                result[column] = probabilities[:, index]
            return result

        memory_predictions = prediction_rows(
            fold2_valid,
            "memory_ngboost",
            fold2_valid["actual_level"].astype(int),
        )
        comparators = pd.concat(
            [
                prediction_rows(
                    fold2_common,
                    "v1_ngboost",
                    fold2_common["actual_level"].astype(int),
                ),
                prediction_rows(
                    fold2_common,
                    "lag7_persistence",
                    fold2_common["lag7_actual_level"].astype(int),
                ),
            ],
            ignore_index=True,
        )
        fold3_prediction = pd.DataFrame(
            {
                "fold": [3],
                "date": [pd.Timestamp("2022-01-01")],
                "actual_level": [2],
                "estimator": ["memory_ngboost"],
                "prediction_status": ["available"],
                "predicted_level": [0],
                **{
                    column: [1.0 if index == 0 else 0.0]
                    for index, column in enumerate(memory.PROBABILITY_COLUMNS)
                },
            }
        )
        memory_predictions = pd.concat(
            [memory_predictions, fold3_prediction], ignore_index=True
        )
        metrics = memory._metric_rows(memory_predictions, comparators, frame)

        self.assertEqual(set(metrics["fold"]), {2})


if __name__ == "__main__":
    unittest.main()
