"""Focused contracts for the ECDF-label NGBoost classifier helpers."""

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

from warning import ootang_ngboost_auto_state_classifier as classifier  # noqa: E402


STATIONS = ("ATU1", "ATU2", "ATU3", "ATU4", "ATU5", "MJ1", "MJ3", "MJ9")
FEATURES = (
    "interval_z",
    "velocity_mm_per_day",
    "acceleration_mm_per_day_squared",
    "tangent_angle_degree",
)


class AutomaticStateClassifierTests(unittest.TestCase):
    def test_site_matrix_is_an_exact_station_major_32_feature_whitelist(self):
        station_rows = []
        site_rows = []
        global_day = 0
        for fold in (1, 2, 3):
            start = pd.Timestamp("2020-01-01") + pd.Timedelta(days=(fold - 1) * 400)
            for day in range(287):
                date = start + pd.Timedelta(days=day)
                site_rows.append(
                    {
                        "fold": fold,
                        "date": date,
                        "label_status": "valid",
                        "auto_state_level": global_day % 5,
                    }
                )
                for station_index, station in enumerate(STATIONS):
                    row = {
                        "fold": fold,
                        "date": date,
                        "station": station,
                        "actual": -999.0,
                        "severity": -888.0,
                        "future_displacement_rate_mm_per_day": -777.0,
                        "auto_state_level": 4,
                        "auto_state_color": "red",
                    }
                    for feature_index, feature in enumerate(FEATURES):
                        row[feature] = (
                            station_index * 100_000.0
                            + feature_index * 1_000.0
                            + global_day
                        )
                    station_rows.append(row)
                global_day += 1

        frame = classifier._build_site_frame(
            pd.DataFrame(station_rows), pd.DataFrame(site_rows)
        )
        expected = tuple(
            f"{station}__{feature}" for station in STATIONS for feature in FEATURES
        )

        self.assertEqual(classifier.SCIENTIFIC_FEATURES, FEATURES)
        self.assertEqual(classifier._site_feature_names(), expected)
        self.assertEqual(frame.loc[:, expected].shape, (861, 32))
        self.assertEqual(
            frame.loc[0, "ATU3__acceleration_mm_per_day_squared"], 202_000.0
        )
        self.assertEqual(frame.loc[0, "actual_level"], 0)
        forbidden = {
            "actual",
            "severity",
            "future",
            "level",
            "color",
            "p10",
            "p50",
            "p90",
        }
        self.assertFalse(
            any(token in column.lower() for column in expected for token in forbidden)
        )

    def test_persistence_uses_only_the_same_fold_matured_t_minus_7_label(self):
        rows = []
        specifications = (
            (1, pd.Timestamp("2020-01-01"), [0, 1, 2, 3, 4, 0, 1, 2, 3]),
            (2, pd.Timestamp("2020-01-10"), [4, 3, 2, 1, 0, 4, 3, 2, 1]),
        )
        for fold, start, levels in specifications:
            for offset, level in enumerate(levels):
                rows.append(
                    {
                        "fold": fold,
                        "date": start + pd.Timedelta(days=offset),
                        "truth_status": "valid",
                        "actual_level": level,
                    }
                )

        result = classifier._attach_transition_and_lag7_truth(pd.DataFrame(rows))

        for fold, _, levels in specifications:
            group = result.loc[result["fold"].eq(fold)].sort_values("date")
            available = group["lag7_actual_level"].notna().tolist()
            self.assertEqual(available, [False] * 7 + [True, True])
            self.assertEqual(group["lag7_actual_level"].dropna().tolist(), levels[:2])
            mature = group.loc[group["lag7_actual_level"].notna()]
            for row in mature.itertuples():
                source = group.loc[group["date"].eq(row.date - pd.Timedelta(days=7))]
                self.assertEqual(len(source), 1)
                self.assertEqual(row.lag7_actual_level, source.iloc[0]["actual_level"])

    def test_transition_mask_resets_at_each_fold_boundary(self):
        dates = pd.date_range("2020-01-01", periods=6, freq="D")
        frame = pd.DataFrame(
            {
                "fold": [1, 1, 1, 2, 2, 2],
                "date": dates,
                "truth_status": ["valid"] * 6,
                "actual_level": [0, 0, 1, 4, 4, 2],
            }
        )

        result = classifier._attach_transition_and_lag7_truth(frame)

        self.assertEqual(
            result["transition_truth"].tolist(),
            [False, False, True, False, False, True],
        )

    def test_probability_metrics_and_colors_use_the_fixed_five_class_order(self):
        base = pd.DataFrame(
            {
                "fold": [2] * 5,
                "date": pd.date_range("2020-01-01", periods=5, freq="D"),
                "truth_status": ["valid"] * 5,
                "actual_level": range(5),
                "transition_truth": [True] * 5,
            }
        )
        probabilities = np.full((5, 5), 0.1)
        np.fill_diagonal(probabilities, 0.6)
        probabilities = classifier._validate_probabilities(probabilities, rows=5)
        predictions = pd.concat(
            [
                classifier._probability_rows(
                    base,
                    estimator=estimator,
                    probabilities=probabilities,
                )
                for estimator in classifier.ESTIMATORS
            ],
            ignore_index=True,
        )

        metrics = classifier._metric_rows(predictions, folds=(2,))
        ngboost_all = metrics.loc[
            metrics["estimator"].eq("ngboost") & metrics["subset"].eq("all_valid")
        ].set_index("metric")

        self.assertEqual(
            classifier.PROBABILITY_COLUMNS,
            ("prob_green", "prob_blue", "prob_yellow", "prob_orange", "prob_red"),
        )
        self.assertEqual(
            predictions.loc[
                predictions["estimator"].eq("ngboost"), "predicted_color"
            ].tolist(),
            ["green", "blue", "yellow", "orange", "red"],
        )
        self.assertAlmostEqual(
            ngboost_all.loc["multiclass_log_loss", "value"], -np.log(0.6)
        )
        self.assertAlmostEqual(ngboost_all.loc["multiclass_brier", "value"], 0.2)


if __name__ == "__main__":
    unittest.main()
