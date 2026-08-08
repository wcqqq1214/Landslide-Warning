import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "code"))

from convlstm import model as base
from convlstm import rolling_validation as rolling
from convlstm import seed_stability as stability


class ConvLSTMSeedStabilityTests(unittest.TestCase):
    def test_rolling_reference_validation_uses_canonical_station_names(self):
        frames = (
            pd.DataFrame({"fold": [1]}),
            pd.DataFrame({"metric": [1]}),
            pd.DataFrame({"station": ["MJ9"]}),
        )
        with mock.patch.object(
            stability.rolling.OUT_MANIFEST.__class__,
            "is_file",
            return_value=True,
        ), mock.patch.object(
            stability.protocol,
            "validate_stage_manifest",
        ), mock.patch.object(
            stability.pd,
            "read_csv",
            side_effect=frames,
        ), mock.patch.object(
            stability.base,
            "load_station_geometry",
            return_value=(
                ["MJ9"],
                np.array([[0.0, 0.0]]),
                np.array([100.0]),
            ),
        ), mock.patch.object(
            stability.rolling,
            "validate_output_frames",
        ) as validate_frames, mock.patch.object(
            stability.rolling,
            "validate_frozen_fold_boundaries",
        ):
            stability.load_rolling_reference()

        self.assertEqual(validate_frames.call_args.args[-1], ["MJ9"])

    def test_outputs_are_isolated_in_versioned_stage_bundle(self):
        expected = (
            ROOT
            / "figures"
            / "convlstm"
            / "runs"
            / "displacement_elevation_exog_v1"
            / "fixed120_v1"
            / "seed_stability_0_4"
        )

        self.assertEqual(stability.OUT_RUNS.parent, expected)
        self.assertEqual(stability.OUT_METRICS.parent, expected)
        self.assertEqual(stability.OUT_SUMMARY.parent, expected)
        self.assertEqual(stability.OUT_TRAINING.parent, expected)
        self.assertEqual(stability.OUT_PREDICTIONS.parent, expected)
        self.assertEqual(stability.OUT_MANIFEST.parent, expected)

    def test_seed_protocol_is_predeclared(self):
        self.assertEqual(stability.SEEDS, (0, 1, 2, 3, 4))
        self.assertEqual(rolling.N_SPLITS, 3)
        self.assertEqual(base.EPOCHS, 120)

    def test_skill_sign_reports_mixed_and_consistent_directions(self):
        self.assertEqual(stability.skill_sign([0.1, 0.2]), "all_positive")
        self.assertEqual(stability.skill_sign([0.0, -0.2]), "all_nonpositive")
        self.assertEqual(stability.skill_sign([-0.1, 0.2]), "mixed")

    def test_increment_diagnostics_reports_correlation_and_variance_ratio(self):
        persistence = np.zeros(4)
        actual = np.array([0.0, 1.0, 2.0, 3.0])
        predicted = np.array([0.0, 2.0, 4.0, 6.0])

        diagnostics = rolling.increment_diagnostics(
            actual,
            predicted,
            persistence,
        )

        self.assertAlmostEqual(diagnostics["increment_correlation"], 1.0)
        self.assertAlmostEqual(diagnostics["increment_std_ratio"], 2.0)

    def test_training_rows_preserve_all_epoch_fields(self):
        result = {
            "training_history": [
                {
                    "epoch": 1,
                    "train_pinball_loss": 0.3,
                    "gradient_l2_norm": 0.2,
                }
            ]
        }

        rows = stability.training_rows(result, seed=2, fold=3)

        self.assertEqual(rows[0]["seed"], 2)
        self.assertEqual(rows[0]["fold"], 3)
        self.assertEqual(rows[0]["epoch"], 1)
        self.assertEqual(
            rows[0]["model_input_schema"],
            "displacement_elevation_exog_v1",
        )
        self.assertEqual(rows[0]["model_input_channels"], 7)

    def test_prediction_rows_preserve_seed_fold_date_station_keys(self):
        actual = np.arange(4, dtype=float).reshape(2, 2)
        result = {
            "raw_p10": actual - 0.4,
            "p50": actual,
            "raw_p90": actual + 0.4,
            "calibrated_p10": actual - 0.5,
            "calibrated_p90": actual + 0.5,
            "actual": actual,
            "persistence": actual - 1.0,
            "qhat": np.array([0.1, 0.1]),
        }

        rows = stability.prediction_rows(
            result,
            ["A", "B"],
            pd.date_range("2020-01-01", periods=2),
            seed=4,
            fold=3,
        )

        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["seed"], 4)
        self.assertEqual(rows[0]["fold"], 3)
        self.assertEqual(rows[-1]["station"], "B")
        self.assertEqual(rows[-1]["model_input_channels"], 7)

    def test_prediction_key_contract_requires_all_34440_frozen_keys(self):
        rows = []
        for seed in stability.SEEDS:
            for fold in stability.protocol.EXPECTED_FOLDS:
                for date in pd.date_range(
                    fold["test_start_date"],
                    fold["test_end_date"],
                ):
                    for station in base.DISP_COLS:
                        rows.append({
                            "seed": seed,
                            "fold": fold["fold"],
                            "date": date.date().isoformat(),
                            "station": station,
                        })
        frame = pd.DataFrame(rows)

        self.assertEqual(len(frame), 34440)
        stability.validate_prediction_key_contract(frame, base.DISP_COLS)
        with self.assertRaises(RuntimeError):
            stability.validate_prediction_key_contract(
                frame.iloc[:-1],
                base.DISP_COLS,
            )

    def test_seed0_reproduction_gate_compares_all_three_artifacts(self):
        folds = pd.DataFrame({"fold": [1], "value": [1.0]})
        metrics = pd.DataFrame({
            "fold": [1],
            "scope": ["overall"],
            "interval_variant": ["raw"],
            "value": [2.0],
        })
        predictions = pd.DataFrame({
            "fold": [1],
            "date": ["2020-01-01"],
            "station": ["A"],
            "value": [3.0],
        })
        runs = folds.assign(seed=0)[["seed", "fold", "value"]]
        seed_metrics = metrics.assign(seed=0)[
            ["seed", "fold", "scope", "interval_variant", "value"]
        ]
        seed_predictions = predictions.assign(seed=0)[
            ["seed", "fold", "date", "station", "value"]
        ]

        stability.validate_seed0_reproduction(
            runs,
            seed_metrics,
            seed_predictions,
            (folds, metrics, predictions),
        )
        seed_predictions.loc[0, "value"] = 4.0
        with self.assertRaises(RuntimeError):
            stability.validate_seed0_reproduction(
                runs,
                seed_metrics,
                seed_predictions,
                (folds, metrics, predictions),
            )

    def test_aggregate_reports_all_seeds_without_best_seed_selection(self):
        rows = []
        for seed, skill in zip(stability.SEEDS, [-0.2, -0.1, 0.1, 0.2, 0.3]):
            row = {
                "seed": seed,
                "fold": 1,
                "scope": "overall",
                "interval_variant": "raw",
                "test_start_date": "2020-01-01",
                "test_end_date": "2020-01-10",
                "n_dates": 10,
                "n_stations": 8,
                "baseline_rmse": 1.0,
                "baseline_mae": 0.8,
                "mean_actual_increment": 0.1,
                "actual_increment_std": 0.2,
                "rmse_skill_vs_baseline": skill,
                "mae_skill_vs_baseline": skill,
            }
            for metric in stability.SUMMARY_METRICS:
                row.setdefault(metric, 0.5 + seed * 0.01)
            rows.append(row)

        summary = stability.aggregate_seed_metrics(pd.DataFrame(rows))

        self.assertEqual(len(summary), 1)
        self.assertEqual(summary.loc[0, "seed_count"], 5)
        self.assertEqual(summary.loc[0, "rmse_skill_positive_seeds"], 3)
        self.assertEqual(summary.loc[0, "rmse_skill_sign"], "mixed")
        self.assertFalse(summary.loc[0, "best_seed_selected"])

    def test_aggregate_rejects_missing_predeclared_seed(self):
        frame = pd.DataFrame({
            "seed": [0],
            "fold": [1],
            "scope": ["overall"],
            "interval_variant": ["raw"],
        })

        with self.assertRaises(RuntimeError):
            stability.aggregate_seed_metrics(frame)


if __name__ == "__main__":
    unittest.main()
