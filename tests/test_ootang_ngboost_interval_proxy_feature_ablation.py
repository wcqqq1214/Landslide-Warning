"""Contracts for the fixed Ootang grouped NGBoost feature ablation."""

from __future__ import annotations

from contextlib import redirect_stderr
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning import ootang_ngboost_interval_proxy_feature_ablation as ablation  # noqa: E402
from warning import ootang_ngboost_interval_proxy_horizon_sensitivity as sensitivity  # noqa: E402
from warning import ootang_ngboost_interval_proxy_pilot as base  # noqa: E402


class AblationDataMixin:
    @classmethod
    def setUpClass(cls):
        (
            cls.profile,
            cls.sensitivity_profile,
            cls.base_profile,
            cls.sensitivity_profile_path,
        ) = ablation.load_ablation_profile()
        cls.predictions = pd.read_csv(
            base.DEFAULT_PREDICTIONS_PATH,
            usecols=base.PREDICTION_COLUMNS,
        )
        cls.kinematics = pd.read_csv(
            base.DEFAULT_KINEMATICS_PATH,
            usecols=base.KINEMATICS_COLUMNS,
        )
        cls.thresholds = pd.read_csv(
            base.DEFAULT_THRESHOLDS_PATH,
            usecols=base.THRESHOLD_COLUMNS,
        )

    @classmethod
    def samples_for_horizon(cls, horizon):
        record = next(
            item
            for item in cls.sensitivity_profile["horizons"]
            if item["horizon_days"] == horizon
        )
        derived = sensitivity._horizon_profile(
            cls.base_profile,
            cls.sensitivity_profile,
            record,
        )
        return base.build_future_interval_proxy_samples(
            cls.predictions,
            cls.kinematics,
            cls.thresholds,
            derived,
        )


class AblationProfileTests(AblationDataMixin, unittest.TestCase):
    def test_profile_locks_feature_sets_horizons_and_nonselection(self):
        self.assertEqual(self.profile["horizons"], [1, 3, 7])
        self.assertEqual(
            [record["name"] for record in self.profile["feature_sets"]],
            list(ablation.FEATURE_SET_ORDER),
        )
        self.assertFalse(self.profile["reporting"]["selection_performed"])
        self.assertFalse(self.profile["reporting"]["ranking_performed"])
        self.assertFalse(self.profile["reporting"]["models_persisted"])
        self.assertIsNone(self.profile["reporting"]["selected_feature_set"])
        self.assertEqual(self.profile["case"], "ootang")
        self.assertFalse(self.profile["vajont_used"])
        self.assertEqual(self.base_profile["model"]["class"], "NGBClassifier")

    def test_profile_rejects_reordering_selection_and_other_case(self):
        for mutation in ("order", "selection", "case", "model_override"):
            profile = deepcopy(self.profile)
            if mutation == "order":
                profile["feature_sets"] = list(reversed(profile["feature_sets"]))
            elif mutation == "selection":
                profile["reporting"]["selection_performed"] = True
                profile["reporting"]["selected_feature_set"] = "full"
            elif mutation == "case":
                profile["case"] = "vajont"
            else:
                profile["feature_sets"][0]["scientific_features"] = ["interval_z"]
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "profile.json"
                path.write_text(json.dumps(profile), encoding="utf-8")
                with self.assertRaises(ablation.AblationProfileError):
                    ablation.load_ablation_profile(path)

    def test_sensitivity_payload_hashes_are_frozen(self):
        records = ablation.validate_sensitivity_artifacts(self.profile)
        self.assertEqual(set(records), set(ablation.EXPECTED_SENSITIVITY_ARTIFACTS))
        for name, contract in ablation.EXPECTED_SENSITIVITY_ARTIFACTS.items():
            if name == "manifest":
                continue
            self.assertEqual(records[name]["sha256"], contract["expected_sha256"])


class AblationFeatureMatrixTests(AblationDataMixin, unittest.TestCase):
    def test_every_feature_set_has_exact_declared_matrix(self):
        samples = self.samples_for_horizon(3)
        for feature_set in self.profile["feature_sets"]:
            X, y, order = ablation.build_feature_set_matrix(samples, feature_set)
            expected = tuple(
                [
                    *feature_set["scientific_features"],
                    *feature_set["control_features"],
                ]
            )
            self.assertEqual(order, expected)
            self.assertEqual(list(X.columns), list(expected))
            self.assertEqual(set(y.loc[samples["split"].eq("fit")]), set(range(5)))
            self.assertFalse(
                any(
                    token in column.lower()
                    for column in X.columns
                    for token in ("acceleration", "rain", "rwl", "vajont")
                )
            )

    def test_unknown_or_duplicate_features_are_rejected(self):
        samples = self.samples_for_horizon(1)
        unknown = deepcopy(self.profile["feature_sets"][0])
        unknown["scientific_features"] = ["unknown"]
        with self.assertRaises(ablation.AblationInputError):
            ablation.build_feature_set_matrix(samples, unknown)
        duplicate = deepcopy(self.profile["feature_sets"][0])
        duplicate["scientific_features"].append("interval_z")
        with self.assertRaises(ablation.AblationInputError):
            ablation.build_feature_set_matrix(samples, duplicate)

    def test_fit_call_uses_fit_rows_only_and_no_optional_arguments(self):
        rows = []
        for index in range(7):
            level = index if index < 5 else 0
            row = {
                feature: float(index + offset)
                for offset, feature in enumerate(base.MODEL_FEATURES)
            }
            row.update({control: 0 for control in base.CONTROL_FEATURES})
            row[base.CONTROL_FEATURES[index % len(base.CONTROL_FEATURES)]] = 1
            row.update(
                {
                    "split": "fit" if index < 5 else "calibration",
                    "target_interval_level": level,
                    "current_interval_level": level,
                }
            )
            rows.append(row)
        samples = pd.DataFrame(rows)

        class FakeModel:
            def __init__(self):
                self.fit_args = None
                self.fit_kwargs = None
                self.base_models = [object()] * 3

            def fit(self, *args, **kwargs):
                self.fit_args = args
                self.fit_kwargs = kwargs
                return self

            def predict_proba(self, X):
                return np.full((len(X), 5), 0.2)

        model = FakeModel()
        with patch.object(ablation.base, "make_ngboost_classifier", return_value=model):
            fitted, _, _, order = ablation.fit_predict_feature_set(
                samples,
                self.base_profile,
                self.profile["feature_sets"][0],
            )
        self.assertIs(fitted, model)
        self.assertEqual(model.fit_kwargs, {})
        self.assertEqual(len(model.fit_args), 2)
        self.assertEqual(model.fit_args[0].shape, (5, len(order)))
        self.assertEqual(model.fit_args[1].tolist(), [0, 1, 2, 3, 4])


class AblationSummaryTests(unittest.TestCase):
    def test_summary_is_nonranking_and_deltas_recompute(self):
        metrics = pd.read_csv(sensitivity.DEFAULT_OUTPUT_DIR / "metrics.csv")
        metrics = metrics.loc[metrics["estimator"].eq("ngboost")]
        combined = pd.concat(
            [metrics.assign(feature_set=name) for name in ablation.FEATURE_SET_ORDER],
            ignore_index=True,
        )
        summary = ablation.build_ablation_summary(
            combined,
            list(ablation.EXPECTED_FEATURE_SETS),
        )
        self.assertEqual(len(summary), 840)
        self.assertFalse(
            any(
                token in column.lower()
                for column in summary.columns
                for token in ("rank", "winner", "selected", "optimal")
            )
        )
        np.testing.assert_allclose(summary["ablation_minus_full"].fillna(0), 0)

    def test_parity_checker_rejects_changed_full_probabilities(self):
        predictions = pd.read_csv(sensitivity.DEFAULT_OUTPUT_DIR / "predictions.csv")
        predictions.insert(0, "feature_set", "full")
        predictions.loc[predictions.index[0], "prob_green"] += 0.001
        predictions.loc[predictions.index[0], "prob_blue"] -= 0.001
        metrics = pd.read_csv(sensitivity.DEFAULT_OUTPUT_DIR / "metrics.csv")
        metrics = metrics.loc[metrics["estimator"].eq("ngboost")].copy()
        metrics.insert(0, "feature_set", "full")
        confusion = pd.read_csv(
            sensitivity.DEFAULT_OUTPUT_DIR / "confusion_matrices.csv"
        )
        confusion = confusion.loc[confusion["estimator"].eq("ngboost")].copy()
        confusion.insert(0, "feature_set", "full")
        reliability = pd.read_csv(sensitivity.DEFAULT_OUTPUT_DIR / "reliability.csv")
        reliability = reliability.loc[reliability["estimator"].eq("ngboost")].copy()
        reliability.insert(0, "feature_set", "full")
        with self.assertRaises(ablation.AblationOutputError):
            ablation.verify_full_parity(
                predictions=predictions,
                metrics=metrics,
                confusion=confusion,
                reliability=reliability,
            )


class AblationOutputIsolationTests(unittest.TestCase):
    def test_repository_output_override_is_rejected(self):
        with self.assertRaises(ablation.AblationOutputError):
            ablation.write_ootang_ngboost_interval_proxy_feature_ablation(
                output_dir=sensitivity.DEFAULT_OUTPUT_DIR
            )

    def test_cli_exposes_no_output_override(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                ablation._parse_args(["--output-dir", "figures/convlstm"])


class AblationMaterializedArtifactTests(unittest.TestCase):
    def test_materialized_bundle_is_complete_nonranking_and_model_free(self):
        manifest_path = ablation.DEFAULT_OUTPUT_DIR / "manifest.json"
        self.assertTrue(manifest_path.is_file())
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["case"], "ootang")
        self.assertFalse(manifest["formal_warning_output"])
        self.assertFalse(manifest["vajont_used"])
        self.assertFalse(manifest["selection_performed"])
        self.assertFalse(manifest["ranking_performed"])
        self.assertFalse(manifest["models_persisted"])
        self.assertEqual(manifest["fit_count"], 21)
        self.assertEqual(
            sorted(int(value) for value in manifest["targets_by_horizon"]),
            [1, 3, 7],
        )
        for horizon in (1, 3, 7):
            target = manifest["targets_by_horizon"][str(horizon)]
            self.assertEqual(target["kind"], "future_raw_interval_proxy_level")
            self.assertEqual(target["horizon_days"], horizon)
        self.assertTrue(
            all(
                record["target"]["horizon_days"] == record["horizon_days"]
                for record in manifest["fit_records"]
            )
        )
        self.assertEqual(
            [record["name"] for record in manifest["feature_sets"]],
            list(ablation.FEATURE_SET_ORDER),
        )
        self.assertTrue(
            all(not record["model_persisted"] for record in manifest["fit_records"])
        )
        self.assertEqual(
            set(manifest["full_parity"]),
            {"predictions", "metrics", "confusion_matrices", "reliability"},
        )

        expected_rows = {
            "predictions": 85120,
            "metrics": 4116,
            "confusion_matrices": 2100,
            "reliability": 5040,
            "ablation_summary": 840,
        }
        for name, rows in expected_rows.items():
            path = ablation.DEFAULT_OUTPUT_DIR / f"{name}.csv"
            self.assertEqual(len(pd.read_csv(path)), rows)

        predictions = pd.read_csv(ablation.DEFAULT_OUTPUT_DIR / "predictions.csv")
        self.assertEqual(
            predictions["feature_set"].drop_duplicates().tolist(),
            list(ablation.FEATURE_SET_ORDER),
        )
        self.assertEqual(predictions["horizon_days"].drop_duplicates().tolist(), [1, 3, 7])
        self.assertFalse(predictions["vajont_used"].astype(bool).any())
        self.assertFalse(predictions["formal_warning_output"].astype(bool).any())
        self.assertNotIn("acceleration", predictions.columns)

        metrics = pd.read_csv(ablation.DEFAULT_OUTPUT_DIR / "metrics.csv")
        summary = pd.read_csv(ablation.DEFAULT_OUTPUT_DIR / "ablation_summary.csv")
        recomputed = ablation.build_ablation_summary(
            metrics,
            list(ablation.EXPECTED_FEATURE_SETS),
        )
        pd.testing.assert_frame_equal(
            summary.reset_index(drop=True),
            recomputed.reset_index(drop=True),
            check_dtype=False,
            rtol=1e-12,
            atol=1e-12,
        )
        full = summary.loc[summary["feature_set"].eq("full")]
        np.testing.assert_allclose(full["ablation_minus_full"].fillna(0), 0)
        self.assertFalse(
            any(
                token in column.lower()
                for column in summary.columns
                for token in ("rank", "winner", "selected", "optimal")
            )
        )

        for record in manifest["outputs"].values():
            path = Path(record["path"])
            if not path.is_absolute():
                path = ROOT / path
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                record["sha256"],
            )
        self.assertFalse(any(ablation.DEFAULT_OUTPUT_DIR.glob("*.pkl")))


if __name__ == "__main__":
    unittest.main()
