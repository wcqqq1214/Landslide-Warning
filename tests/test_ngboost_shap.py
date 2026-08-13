"""Contracts for the current independent NGBoost--SHAP analysis."""

from __future__ import annotations

import sys
import unittest
from unittest.mock import patch
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from explainability import ngboost_shap  # noqa: E402


class NgboostShapTests(unittest.TestCase):
    def test_outputs_are_current_regression_artifacts(self):
        self.assertEqual(ngboost_shap.OUT_PNG.parent, ROOT / "figures" / "shap")
        self.assertIn("not ConvLSTM", ngboost_shap.ANALYSIS_SCOPE)
        self.assertIn("causal inference", ngboost_shap.ANALYSIS_SCOPE)
        self.assertEqual(
            len(ngboost_shap._sha256_file(ngboost_shap.DATA_CSV)),
            64,
        )

    def test_lagged_samples_have_no_warning_label(self):
        data = pd.read_csv(ngboost_shap.DATA_CSV)
        X, target, meta = ngboost_shap.build_lagged_samples(data)

        self.assertEqual(len(X), len(target))
        self.assertEqual(len(X), len(meta))
        self.assertEqual(target.name, "target_displacement_increment")
        self.assertFalse(any("warning" in column.lower() for column in X.columns))
        self.assertTrue(X.notna().all().all())

    def test_time_split_and_date_sampling_are_chronological(self):
        data = pd.read_csv(ngboost_shap.DATA_CSV)
        X, _, meta = ngboost_shap.build_lagged_samples(data)
        mask, split_date = ngboost_shap.time_train_mask(meta, train_frac=0.5)
        sample, dates = ngboost_shap.evenly_spaced_date_sample(X, meta, mask, 5)

        self.assertTrue((meta.loc[mask, "Date"] < split_date).all())
        self.assertEqual(sample.index.tolist(), sorted(sample.index.tolist()))
        self.assertEqual(len(dates), 5)

    def test_regressor_uses_fixed_seed(self):
        model = ngboost_shap.make_regressor(n_estimators=7)
        self.assertEqual(model.random_state.get_state()[1][0], ngboost_shap.SEED)

    def test_importance_frame_keeps_interpretation_fields(self):
        frame = ngboost_shap.importance_frame(
            np.array([[1.0, -2.0], [3.0, 0.0]]),
            pd.Index(["feature_a", "feature_b"]),
            {"analysis_scope": "exploratory"},
        )
        self.assertEqual(frame.iloc[0]["feature"], "feature_a")
        self.assertEqual(frame.iloc[0]["analysis_scope"], "exploratory")

    def test_shap_matrix_uses_permutation_seed(self):
        model = ngboost_shap.make_regressor(n_estimators=2)
        background = pd.DataFrame([[1.0, 2.0], [2.0, 3.0]], columns=["a", "b"])
        sample = pd.DataFrame([[1.5, 2.5]], columns=["a", "b"])
        with patch.object(ngboost_shap.shap, "Explainer") as factory:
            factory.return_value.return_value.values = np.zeros((1, 2))
            values = ngboost_shap.shap_matrix(model, background, sample)

        self.assertEqual(values.shape, (1, 2))
        self.assertEqual(factory.call_args.kwargs["algorithm"], "permutation")
        self.assertEqual(factory.call_args.kwargs["seed"], ngboost_shap.SEED)


if __name__ == "__main__":
    unittest.main()
