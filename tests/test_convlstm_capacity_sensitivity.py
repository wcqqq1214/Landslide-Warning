import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "code"))

import convlstm_capacity_sensitivity as capacity  # noqa: E402
import convlstm_inner_validation as inner  # noqa: E402
import convlstm_seed_stability as stability  # noqa: E402


class ConvLSTMCapacitySensitivityTests(unittest.TestCase):
    def test_protocol_matrix_and_parameter_counts_are_predeclared(self):
        configs = {
            (config.hidden_channels, config.weight_decay)
            for config in capacity.CONFIGS
        }

        self.assertEqual(
            configs,
            {(8, 0.0), (8, 1e-4), (16, 0.0), (16, 1e-4)},
        )
        self.assertEqual(capacity.model_parameter_count(8), 4091)
        self.assertEqual(capacity.model_parameter_count(16), 12787)
        self.assertEqual(capacity.REFERENCE_CONFIG_ID, "h16_wd0")

    def test_candidate_aggregation_uses_all_seeds_and_inner_loss_only(self):
        rows = []
        losses = {
            "h08_wd0": 0.3,
            "h08_wd1e4": 0.2,
            "h16_wd0": 0.4,
            "h16_wd1e4": 0.5,
        }
        for fold in (1, 2, 3):
            for config in capacity.CONFIGS:
                for seed in stability.SEEDS:
                    rows.append({
                        "fold": fold,
                        "config_id": config.config_id,
                        "hidden_channels": config.hidden_channels,
                        "weight_decay": config.weight_decay,
                        "parameter_count": capacity.model_parameter_count(
                            config.hidden_channels
                        ),
                        "seed": seed,
                        "selected_validation_loss": (
                            losses[config.config_id] + seed * 0.001
                        ),
                        "selected_epoch": 10 + seed,
                    })

        summary = capacity.aggregate_candidates(pd.DataFrame(rows))
        selected = capacity.selected_configs(summary)

        self.assertEqual(len(summary), 12)
        self.assertTrue(all(
            config.config_id == "h08_wd1e4"
            for config in selected.values()
        ))
        self.assertFalse(summary["outer_test_used_for_ranking"].any())
        self.assertFalse(summary["best_seed_selected"].any())

    def test_exact_tie_prefers_smaller_regularized_configuration(self):
        rows = []
        for fold in (1, 2, 3):
            for config in capacity.CONFIGS:
                for seed in stability.SEEDS:
                    rows.append({
                        "fold": fold,
                        "config_id": config.config_id,
                        "hidden_channels": config.hidden_channels,
                        "weight_decay": config.weight_decay,
                        "parameter_count": capacity.model_parameter_count(
                            config.hidden_channels
                        ),
                        "seed": seed,
                        "selected_validation_loss": 0.2,
                        "selected_epoch": 5,
                    })

        selected = capacity.selected_configs(
            capacity.aggregate_candidates(pd.DataFrame(rows))
        )

        self.assertTrue(all(
            config.config_id == "h08_wd1e4"
            for config in selected.values()
        ))

    def test_reference_candidate_must_reproduce_saved_early_stop_run(self):
        candidate_rows = []
        reference_rows = []
        for fold in (1, 2, 3):
            for seed in stability.SEEDS:
                common = {
                    "seed": seed,
                    "fold": fold,
                    "selected_epoch": 10 + seed,
                    "selected_validation_loss": 0.2 + seed * 0.01,
                    "observed_epochs": 40 + seed,
                    "stop_reason": "patience_exhausted",
                }
                candidate_rows.append({
                    "config_id": capacity.REFERENCE_CONFIG_ID,
                    **common,
                })
                reference_rows.append(common)

        capacity.validate_reference_candidate(
            pd.DataFrame(candidate_rows),
            pd.DataFrame(reference_rows),
        )
        candidate_rows[0]["selected_validation_loss"] += (
            capacity.REFERENCE_LOSS_ATOL
        )
        capacity.validate_reference_candidate(
            pd.DataFrame(candidate_rows),
            pd.DataFrame(reference_rows),
        )
        candidate_rows[0]["selected_epoch"] += 1
        with self.assertRaises(RuntimeError):
            capacity.validate_reference_candidate(
                pd.DataFrame(candidate_rows),
                pd.DataFrame(reference_rows),
            )
        candidate_rows[0]["selected_epoch"] -= 1
        candidate_rows[0]["selected_validation_loss"] += 1e-10
        with self.assertRaises(RuntimeError):
            capacity.validate_reference_candidate(
                pd.DataFrame(candidate_rows),
                pd.DataFrame(reference_rows),
            )

    def test_reference_comparison_uses_unambiguous_prefixes(self):
        row = {
            "seed": 0,
            "fold": 1,
            "scope": "overall",
            "interval_variant": "raw",
            "test_start_date": "2020-01-01",
            "test_end_date": "2020-01-10",
            "baseline_rmse": 1.0,
            "baseline_mae": 0.8,
        }
        for metric in stability.SUMMARY_METRICS:
            row.setdefault(metric, 0.5)
        row.update({
            "model_mean_error": 0.1,
            "mean_predicted_increment": 0.1,
            "predicted_increment_std": 0.2,
            "increment_std_ratio": 0.3,
            "increment_correlation": 0.4,
        })
        reference = pd.DataFrame([row])
        selected = pd.DataFrame([{**row, "model_rmse": 0.4}])
        runs = pd.DataFrame([{
            "seed": 0,
            "fold": 1,
            "selected_epoch": 12,
            "stop_reason": "patience_exhausted",
        }])

        comparison = capacity.build_reference_comparison(
            selected,
            reference,
            runs,
        )

        self.assertIn("reference_model_rmse", comparison)
        self.assertIn("selected_model_rmse", comparison)
        self.assertNotIn("fixed_model_rmse", comparison)
        self.assertAlmostEqual(comparison.loc[0, "delta_model_rmse"], -0.1)
        self.assertTrue(comparison.loc[0, "rmse_improved"])

        selected.loc[0, "model_rmse"] = (
            reference.loc[0, "model_rmse"] - 1e-14
        )
        comparison = capacity.build_reference_comparison(
            selected,
            reference,
            runs,
        )
        self.assertFalse(comparison.loc[0, "rmse_improved"])
        self.assertTrue(comparison.loc[0, "rmse_numerically_equal"])

    def test_existing_inner_selection_defaults_remain_current_configuration(self):
        self.assertEqual(inner.run_epoch_selection.__kwdefaults__["hidden_channels"], 16)
        self.assertEqual(inner.run_epoch_selection.__kwdefaults__["weight_decay"], 0.0)


if __name__ == "__main__":
    unittest.main()
