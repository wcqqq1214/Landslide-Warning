import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "code"))

import convlstm as base  # noqa: E402
import convlstm_seed_stability as stability  # noqa: E402
import convlstm_rolling_validation as rolling  # noqa: E402


class ConvLSTMSeedStabilityTests(unittest.TestCase):
    def test_outputs_are_grouped_under_convlstm_figures(self):
        expected = ROOT / "figures" / "convlstm"

        self.assertEqual(stability.OUT_RUNS.parent, expected)
        self.assertEqual(stability.OUT_METRICS.parent, expected)
        self.assertEqual(stability.OUT_SUMMARY.parent, expected)
        self.assertEqual(stability.OUT_TRAINING.parent, expected)

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
