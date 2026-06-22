import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "code"))

import shap_stability  # noqa: E402


class ShapStabilityTests(unittest.TestCase):
    def test_outputs_are_grouped_under_stability_directory(self):
        outputs = [
            shap_stability.OUT_PROTOCOL,
            shap_stability.OUT_FEATURE_IMPORTANCE,
            shap_stability.OUT_FEATURE_STABILITY,
            shap_stability.OUT_RANK_STABILITY,
            shap_stability.OUT_GROUP_IMPORTANCE,
            shap_stability.OUT_ABLATION_FOLDS,
            shap_stability.OUT_ABLATION_SUMMARY,
            shap_stability.OUT_GROUP_PLOT,
            shap_stability.OUT_ABLATION_PLOT,
        ]

        self.assertTrue(all(path.parent == shap_stability.OUT_DIR for path in outputs))

    def test_preregistered_groups_cover_expected_feature_counts(self):
        columns = []
        for lag in range(1, 6):
            columns.extend([
                f"disp_lag{lag}",
                f"disp_rate_lag{lag}",
                f"disp_accel_lag{lag}",
            ])
            columns.extend(f"{name}_lag{lag}" for name in shap_stability.ENV_COLS)
            columns.extend([f"RWL_rate_lag{lag}", f"GWT_rate_lag{lag}"])
            columns.extend(f"Rain_cum{days}_lag{lag}" for days in (7, 15, 30))
        columns.extend(f"station_{station}" for station in shap_stability.STATIONS)

        groups = shap_stability.build_feature_groups(columns, validate_counts=True)

        self.assertEqual(
            {name: len(features) for name, features in groups.items()},
            shap_stability.EXPECTED_GROUP_COUNTS,
        )
        self.assertEqual(sum(map(len, groups.values())), 88)

    def test_unknown_feature_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "未注册特征组"):
            shap_stability.assign_feature_group("future_warning_label")

    def test_even_date_sample_preserves_all_rows_on_selected_dates(self):
        dates = pd.date_range("2020-01-01", periods=10)
        meta = pd.DataFrame({
            "Date": np.repeat(dates, 2),
            "station": ["A", "B"] * len(dates),
        })
        X = pd.DataFrame({"value": np.arange(len(meta))})
        mask = pd.Series(True, index=meta.index)

        sample, selected = shap_stability.evenly_spaced_date_sample(
            X,
            meta,
            mask,
            date_count=4,
        )

        self.assertEqual(selected[0], dates[0])
        self.assertEqual(selected[-1], dates[-1])
        self.assertEqual(len(selected), 4)
        self.assertEqual(len(sample), 8)

    def test_spearman_direction_handles_monotonic_and_constant_values(self):
        self.assertAlmostEqual(
            shap_stability.spearman_from_values([1, 2, 3], [2, 4, 8]),
            1.0,
        )
        self.assertAlmostEqual(
            shap_stability.spearman_from_values([1, 2, 3], [8, 4, 2]),
            -1.0,
        )
        self.assertTrue(
            np.isnan(shap_stability.spearman_from_values([1, 1, 1], [1, 2, 3]))
        )

    def test_spearman_direction_ignores_dataframe_row_index(self):
        feature = pd.Series([1.0, 2.0, 3.0], index=[100, 200, 300])

        result = shap_stability.spearman_from_values(feature, [2.0, 4.0, 8.0])

        self.assertAlmostEqual(result, 1.0)

    def test_score_degradation_has_positive_worse_orientation(self):
        self.assertAlmostEqual(
            shap_stability.score_degradation("regression", "mae", 1.0, 1.3),
            0.3,
        )
        self.assertAlmostEqual(
            shap_stability.score_degradation("classification", "brier", 0.1, 0.2),
            0.1,
        )
        self.assertAlmostEqual(
            shap_stability.score_degradation("classification", "auc", 0.9, 0.7),
            0.2,
        )

    def test_feature_stability_counts_rank_and_direction(self):
        frame = pd.DataFrame({
            "task": ["regression"] * 3,
            "fold": [1, 2, 3],
            "feature": ["disp_lag1"] * 3,
            "feature_group": ["displacement_kinematics"] * 3,
            "normalized_share": [0.3, 0.2, 0.1],
            "rank": [1.0, 2.0, 12.0],
            "direction_spearman": [0.8, 0.6, -0.2],
        })

        result = shap_stability.summarize_feature_stability(frame).iloc[0]

        self.assertEqual(result["top_k_folds"], 2)
        self.assertEqual(result["dominant_direction"], "positive")
        self.assertAlmostEqual(result["direction_consistency"], 2 / 3)

    def test_pairwise_rank_stability_reports_all_fold_pairs(self):
        feature = pd.DataFrame({
            "task": ["regression"] * 6,
            "fold": [1, 1, 2, 2, 3, 3],
            "feature": ["a", "b"] * 3,
            "rank": [1, 2, 1, 2, 2, 1],
        })
        group = feature.rename(columns={"feature": "feature_group"})

        result = shap_stability.pairwise_rank_stability(feature, group)

        self.assertEqual(len(result), 6)
        self.assertEqual(set(result["level"]), {"feature", "group"})


if __name__ == "__main__":
    unittest.main()
