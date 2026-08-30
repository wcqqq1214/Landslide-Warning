"""Focused contracts for automatic future-state label construction."""

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

from warning import ootang_ngboost_auto_state as auto_state  # noqa: E402


class AutomaticStateLabelTests(unittest.TestCase):
    @staticmethod
    def _seed_fixture() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        date = pd.Timestamp("2020-01-01")
        prediction_rows = []
        for seed, p50 in ((0, 1.0), (1, 3.0)):
            for station in auto_state.OOTANG_STATIONS:
                prediction_rows.append(
                    {
                        "seed": seed,
                        "fold": 1,
                        "date": date,
                        "station": station,
                        "actual": 2.0,
                        "p50": p50,
                        "calibrated_p10": 0.0,
                        "calibrated_p90": 4.0,
                    }
                )
        predictions = pd.DataFrame(prediction_rows)
        runs = pd.DataFrame({"fold": [1, 1], "seed": [0, 1]})
        boundaries = pd.DataFrame(
            {
                "fold": [1],
                "test_start_date": [date],
                "test_end_date": [date],
            }
        )
        return predictions, runs, boundaries

    def test_equal_weight_seed_aggregation_and_actual_disagreement_rejection(self):
        predictions, runs, boundaries = self._seed_fixture()

        aggregated = auto_state._aggregate_seed_predictions(
            predictions, runs, boundaries
        )

        self.assertEqual(len(aggregated), len(auto_state.OOTANG_STATIONS))
        np.testing.assert_allclose(aggregated["p50"], 2.0)
        np.testing.assert_allclose(aggregated["actual"], 2.0)

        inconsistent = predictions.copy()
        inconsistent.loc[
            inconsistent["seed"].eq(1)
            & inconsistent["station"].eq(auto_state.OOTANG_STATIONS[0]),
            "actual",
        ] = 2.5
        with self.assertRaisesRegex(
            auto_state.AutoStateInputError,
            "Actual displacement differs across seeds",
        ):
            auto_state._aggregate_seed_predictions(inconsistent, runs, boundaries)

    def test_h7_targets_remain_within_fold_and_terminal_days_are_unlabelled(self):
        dates = pd.date_range("2020-01-01", periods=20, freq="D")
        kinetic_rows = []
        feature_rows = []
        for station_index, station in enumerate(auto_state.OOTANG_STATIONS, start=1):
            for day_index, date in enumerate(dates):
                kinetic_rows.append(
                    {
                        "date": date,
                        "station": station,
                        "displacement": float(station_index * day_index),
                        "velocity": float(station_index),
                        "velocity_status": "valid",
                        "acceleration": float(station_index) / 10.0,
                        "acceleration_status": "valid",
                    }
                )
                feature_rows.append(
                    {
                        "fold": 1 if day_index < 10 else 2,
                        "date": date,
                        "station": station,
                    }
                )
        boundaries = pd.DataFrame(
            {
                "fold": [1, 2],
                "test_start_date": [dates[0], dates[10]],
                "test_end_date": [dates[9], dates[19]],
            }
        )

        labelled = auto_state._attach_oof_outcomes(
            pd.DataFrame(feature_rows),
            pd.DataFrame(kinetic_rows),
            boundaries,
            horizon_days=7,
        )

        valid = labelled.loc[labelled["label_status"].eq("valid")]
        target_gap = (valid["target_end_date"] - valid["date"]).dt.days
        fold_ends = boundaries.set_index("fold")["test_end_date"]
        self.assertTrue(target_gap.eq(7).all())
        self.assertTrue(
            valid.apply(
                lambda row: row["target_end_date"] <= fold_ends.loc[row["fold"]],
                axis=1,
            ).all()
        )
        terminal_counts = (
            labelled.loc[labelled["label_status"].eq("unavailable_fold_terminal")]
            .groupby(["fold", "station"])
            .size()
        )
        self.assertEqual(len(terminal_counts), 2 * len(auto_state.OOTANG_STATIONS))
        self.assertTrue(terminal_counts.eq(7).all())

    def test_ordered_state_centers_are_deterministic_under_row_reordering(self):
        segments = pd.DataFrame(
            {
                "severity": np.repeat([-4.0, -2.0, 0.0, 2.0, 4.0], 3),
                "weight": np.tile([1.0, 2.0, 3.0], 5),
            }
        )

        first = auto_state._ordered_kmeans_boundaries(
            segments, n_clusters=5, random_state=0
        )
        second = auto_state._ordered_kmeans_boundaries(
            segments.sample(frac=1.0, random_state=19),
            n_clusters=5,
            random_state=0,
        )

        self.assertEqual(first, second)
        self.assertEqual(first.centers, (-4.0, -2.0, 0.0, 2.0, 4.0))
        self.assertTrue(np.all(np.diff(first.centers) > 0))
        self.assertEqual(first.thresholds, (-3.0, -1.0, 1.0, 3.0))

    def test_compact_label_gate_has_an_explicit_pass_and_fail(self):
        levels = list(range(5))
        station_fit = pd.DataFrame(
            {
                "auto_state_level": levels,
                auto_state.OUTCOME_COLUMNS[0]: np.arange(5.0),
                auto_state.OUTCOME_COLUMNS[1]: np.arange(5.0),
            }
        )
        site_fit = pd.DataFrame(
            {
                "auto_state_level": levels,
                auto_state.OUTCOME_COLUMNS[0]: np.arange(5.0),
                auto_state.OUTCOME_COLUMNS[1]: np.arange(5.0),
            }
        )
        fold_specs = (
            (1, pd.Timestamp("2020-01-01")),
            (2, pd.Timestamp("2020-02-01")),
        )
        station_rows = []
        site_rows = []
        for fold, start in fold_specs:
            for offset in range(12):
                date = start + pd.Timedelta(days=offset)
                valid = offset < 5
                level = offset if valid else pd.NA
                for station in auto_state.OOTANG_STATIONS:
                    station_rows.append(
                        {
                            "fold": fold,
                            "date": date,
                            "station": station,
                            "label_status": (
                                "valid" if valid else "unavailable_fold_terminal"
                            ),
                            "auto_state_level": level,
                            "target_end_date": (
                                date + pd.Timedelta(days=7) if valid else pd.NaT
                            ),
                        }
                    )
                site_rows.append(
                    {
                        "fold": fold,
                        "date": date,
                        "label_status": (
                            "valid" if valid else "unavailable_fold_terminal"
                        ),
                        "auto_state_level": level,
                    }
                )
        station_oof = pd.DataFrame(station_rows)
        site_oof = pd.DataFrame(site_rows)
        boundaries = pd.DataFrame(
            {
                "fold": [1, 2],
                "test_start_date": [start for _, start in fold_specs],
                "test_end_date": [
                    start + pd.Timedelta(days=11) for _, start in fold_specs
                ],
            }
        )
        definitions = pd.DataFrame(
            {
                "record_type": ["station_scaler"],
                "max_input_date": [pd.Timestamp("2020-01-12")],
            }
        )
        ordered = auto_state._Boundaries(
            centers=(0.0, 1.0, 2.0, 3.0, 4.0),
            thresholds=(0.5, 1.5, 2.5, 3.5),
        )

        def build_gate(station: pd.DataFrame, site: pd.DataFrame) -> dict:
            return auto_state._build_gate(
                station_fit,
                station,
                site_fit,
                site,
                ordered,
                ordered,
                definitions,
                boundaries,
                horizon_days=7,
                label_fit_fold=1,
                required_nonempty_folds=(1, 2),
                min_class_support_advisory=20,
            )

        passing = build_gate(station_oof, site_oof)
        self.assertTrue(passing["label_gate_passed"])
        self.assertTrue(passing["advisories"][0]["triggered"])
        self.assertFalse(passing["advisories"][0]["blocking"])

        missing_level = station_oof.copy()
        missing_level.loc[
            missing_level["fold"].eq(2) & missing_level["auto_state_level"].eq(4),
            "auto_state_level",
        ] = 3
        missing_site_level = site_oof.copy()
        missing_site_level.loc[
            missing_site_level["fold"].eq(2)
            & missing_site_level["auto_state_level"].eq(4),
            "auto_state_level",
        ] = 3
        failing = build_gate(missing_level, missing_site_level)
        gate_by_name = {record["name"]: record for record in failing["gates"]}
        self.assertFalse(failing["label_gate_passed"])
        self.assertFalse(gate_by_name["required_folds_five_levels_nonempty"]["passed"])


if __name__ == "__main__":
    unittest.main()
