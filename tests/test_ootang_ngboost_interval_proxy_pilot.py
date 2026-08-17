"""Contracts for the isolated Ootang NGBoost interval-proxy pilot."""

from __future__ import annotations

from copy import deepcopy
from contextlib import redirect_stderr
import hashlib
import io
import json
import pickle
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd
from ngboost import NGBClassifier


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning import ootang_ngboost_interval_proxy_pilot as pilot  # noqa: E402


class PilotTestDataMixin:
    @staticmethod
    def profile():
        return pilot.load_pilot_profile()

    @staticmethod
    def source_frames():
        predictions = pd.read_csv(
            pilot.DEFAULT_PREDICTIONS_PATH,
            usecols=pilot.PREDICTION_COLUMNS,
        )
        kinematics = pd.read_csv(
            pilot.DEFAULT_KINEMATICS_PATH,
            usecols=pilot.KINEMATICS_COLUMNS,
        )
        thresholds = pd.read_csv(
            pilot.DEFAULT_THRESHOLDS_PATH,
            usecols=pilot.THRESHOLD_COLUMNS,
        )
        return predictions, kinematics, thresholds

    @classmethod
    def samples(cls):
        predictions, kinematics, thresholds = cls.source_frames()
        return pilot.build_future_interval_proxy_samples(
            predictions,
            kinematics,
            thresholds,
            cls.profile(),
        )


class PilotProfileTests(PilotTestDataMixin, unittest.TestCase):
    def test_profile_locks_model_scope_and_target(self):
        profile = self.profile()

        self.assertEqual(profile["case"], "ootang")
        self.assertFalse(profile["formal_warning_output"])
        self.assertFalse(profile["vajont_used"])
        self.assertFalse(profile["default_pipeline_member"])
        self.assertEqual(profile["model"]["class"], "NGBClassifier")
        self.assertEqual(profile["model"]["distribution"]["classes"], 5)
        self.assertEqual(
            profile["target"]["kind"],
            "next_calendar_day_raw_interval_proxy_level",
        )
        self.assertEqual(
            [item["name"] for item in profile["features"]["scientific"]],
            list(pilot.MODEL_FEATURES),
        )
        self.assertIn("acceleration", profile["features"]["forbidden"])
        self.assertTrue(all(not value for value in profile["training_policy"].values()))

    def test_profile_rejects_model_case_and_vajont_changes(self):
        for path, value in (
            (("case",), "vajont"),
            (("vajont_used",), True),
            (("model", "class"), "LightGBM"),
            (("target", "horizon_days"), 3),
        ):
            profile = deepcopy(self.profile())
            target = profile
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            with tempfile.TemporaryDirectory() as directory:
                profile_path = Path(directory) / "profile.json"
                profile_path.write_text(json.dumps(profile), encoding="utf-8")
                with self.assertRaises(pilot.PilotProfileError):
                    pilot.load_pilot_profile(profile_path)


class PilotSampleTests(PilotTestDataMixin, unittest.TestCase):
    def test_current_bundle_builds_exact_next_day_support(self):
        samples = self.samples()

        self.assertEqual(len(samples), 11376)
        self.assertEqual(
            samples.groupby("split", sort=False).size().to_dict(),
            {"fit": 7280, "calibration": 1808, "test": 2288},
        )
        expected = self.profile()["splits"]["expected_target_class_counts"]
        for split in pilot.SPLIT_ORDER:
            counts = (
                samples.loc[samples["split"].eq(split), "target_interval_color"]
                .value_counts()
                .reindex(pilot.WARNING_COLORS, fill_value=0)
                .to_dict()
            )
            self.assertEqual(counts, expected[split])
        gap = (
            pd.to_datetime(samples["target_date"])
            - pd.to_datetime(samples["date"])
        ).dt.days
        self.assertTrue(gap.eq(1).all())
        self.assertTrue(
            samples.groupby(["split", "date"])["station"]
            .nunique()
            .eq(8)
            .all()
        )

    def test_calibrated_columns_cannot_change_samples_or_labels(self):
        predictions, kinematics, thresholds = self.source_frames()
        baseline = pilot.build_future_interval_proxy_samples(
            predictions,
            kinematics,
            thresholds,
            self.profile(),
        )
        changed = predictions.copy()
        changed["calibrated_p10"] = np.linspace(-1e9, 1e9, len(changed))
        changed["calibrated_p90"] = -changed["calibrated_p10"]
        changed["qhat_mm"] = 1e12
        repeated = pilot.build_future_interval_proxy_samples(
            changed,
            kinematics,
            thresholds,
            self.profile(),
        )

        columns = [
            "date",
            "target_date",
            "station",
            *pilot.MODEL_FEATURES,
            "current_interval_level",
            "target_interval_level",
        ]
        pd.testing.assert_frame_equal(
            baseline.loc[:, columns],
            repeated.loc[:, columns],
        )

    def test_interior_date_gap_is_rejected(self):
        predictions, kinematics, thresholds = self.source_frames()
        row = predictions.loc[
            predictions["station"].eq("ATU1")
            & predictions["split"].eq("fit")
        ].index[10]
        predictions = predictions.drop(index=row)

        with self.assertRaisesRegex(pilot.PilotInputError, "Interior date gap"):
            pilot.build_future_interval_proxy_samples(
                predictions,
                kinematics,
                thresholds,
                self.profile(),
            )

    def test_model_matrix_has_only_four_indicators_and_station_controls(self):
        samples = self.samples()
        X, y = pilot.build_model_matrix(samples)

        self.assertEqual(
            list(X.columns),
            [*pilot.MODEL_FEATURES, *pilot.CONTROL_FEATURES],
        )
        self.assertEqual(set(y.unique()), set(pilot.CLASS_LEVELS))
        forbidden_tokens = {
            "acceleration",
            "rain",
            "rwl",
            "groundwater",
            "actual",
            "date",
            "split",
            "target",
        }
        self.assertFalse(
            any(
                token in column.lower()
                for column in X.columns
                for token in forbidden_tokens
            )
        )

    def test_tangent_formula_matches_v4_materialized_values(self):
        station = pd.read_csv(
            ROOT
            / "figures"
            / "warning_operational_draft_v4"
            / "ootang_operational_station_timeline.csv",
            usecols=[
                "velocity",
                "comparator_v0_mm_per_day",
                "tangent_angle_degree",
            ],
        ).dropna()
        calculated = pilot.compute_improved_tangent_angle_degree(
            station["velocity"],
            station["comparator_v0_mm_per_day"],
        )
        np.testing.assert_allclose(
            calculated,
            station["tangent_angle_degree"].to_numpy(),
            rtol=0,
            atol=1e-12,
        )


class PilotModelAndMetricTests(PilotTestDataMixin, unittest.TestCase):
    def test_classifier_is_fixed_five_class_ngboost(self):
        model = pilot.make_ngboost_classifier(self.profile())

        self.assertIsInstance(model, NGBClassifier)
        self.assertEqual(model.Dist.K_, 5)
        self.assertEqual(model.n_estimators, 500)
        self.assertEqual(model.learning_rate, 0.01)
        self.assertEqual(model.minibatch_frac, 1.0)
        self.assertEqual(model.col_sample, 1.0)
        self.assertEqual(model.Base.max_depth, 3)
        self.assertEqual(model.Base.random_state, 0)

    def test_small_classifier_returns_ordered_five_probabilities(self):
        profile = deepcopy(self.profile())
        profile["model"]["n_estimators"] = 3
        model = pilot.make_ngboost_classifier(profile)
        X = np.array(
            [[float(level), float(index % 2)] for level in range(5) for index in range(4)]
        )
        y = np.repeat(np.arange(5), 4)
        model.fit(X, y)
        probabilities = model.predict_proba(X)

        self.assertEqual(probabilities.shape, (20, 5))
        self.assertTrue(np.isfinite(probabilities).all())
        np.testing.assert_allclose(probabilities.sum(axis=1), 1.0)

    def test_metrics_and_confusion_preserve_absent_calibration_red(self):
        samples = self.samples()
        predictions = samples.copy()
        probabilities = np.full((len(samples), 5), 0.05)
        probabilities[:, 0] = 0.8
        for index, column in enumerate(pilot.PROBABILITY_COLUMNS):
            predictions[column] = probabilities[:, index]
        predictions["ngboost_predicted_level"] = 0
        predictions["fit_majority_predicted_level"] = 0
        predictions["persistence_predicted_level"] = predictions[
            "current_interval_level"
        ]
        fit_counts = (
            predictions.loc[predictions["split"].eq("fit"), "target_interval_level"]
            .value_counts()
            .reindex(pilot.CLASS_LEVELS, fill_value=0)
            .to_numpy(dtype=float)
        )
        priors = fit_counts / fit_counts.sum()

        metrics = pilot.build_metric_rows(predictions, priors, bins=10)
        red_recall = metrics.loc[
            metrics["split"].eq("calibration")
            & metrics["estimator"].eq("ngboost")
            & metrics["subset"].eq("all")
            & metrics["metric"].eq("recall")
            & metrics["class_level"].eq(4)
        ].iloc[0]
        self.assertEqual(red_recall["status"], "absent_target_class")
        self.assertTrue(pd.isna(red_recall["value"]))

        confusion = pilot.build_confusion_rows(predictions)
        self.assertEqual(len(confusion), 3 * 3 * 2 * 25)
        red_actual = confusion.loc[
            confusion["split"].eq("calibration")
            & confusion["subset"].eq("all")
            & confusion["actual_level"].eq(4)
        ]
        self.assertTrue(red_actual["count"].eq(0).all())

        reliability = pilot.build_reliability_rows(predictions, priors, bins=10)
        red_reliability = reliability.loc[
            reliability["split"].eq("calibration")
            & reliability["estimator"].eq("ngboost")
            & reliability["subset"].eq("all")
            & reliability["reliability_kind"].eq("one_vs_rest")
            & reliability["class_level"].eq(4)
        ]
        self.assertTrue(red_reliability["class_support"].eq(0).all())
        self.assertTrue(
            red_reliability["calibration_status"].eq("no_positive_examples").all()
        )


class PilotLineageTests(unittest.TestCase):
    def test_current_sources_match_their_manifests(self):
        forecast = pilot.validate_forecast_lineage(
            pilot.DEFAULT_PREDICTIONS_PATH,
            pilot.DEFAULT_FORECAST_MANIFEST_PATH,
        )
        v4 = pilot.validate_v4_lineage(
            kinematics_path=pilot.DEFAULT_KINEMATICS_PATH,
            predictions_path=pilot.DEFAULT_PREDICTIONS_PATH,
            thresholds_path=pilot.DEFAULT_THRESHOLDS_PATH,
            manifest_path=pilot.DEFAULT_V4_MANIFEST_PATH,
        )

        self.assertTrue(forecast["prediction_sha256_matches"])
        self.assertEqual(set(v4["sources"]), {"kinematics", "predictions", "thresholds"})


class PilotOutputIsolationTests(unittest.TestCase):
    def test_repository_output_overrides_cannot_target_protected_artifacts(self):
        with self.assertRaisesRegex(pilot.PilotOutputError, "model_output_path"):
            pilot.write_ootang_ngboost_interval_proxy_pilot(
                model_output_path=ROOT / "models" / "convlstm.pt"
            )
        with self.assertRaisesRegex(pilot.PilotOutputError, "output_dir"):
            pilot.write_ootang_ngboost_interval_proxy_pilot(
                output_dir=ROOT / "figures" / "warning_operational_draft_v4"
            )

    def test_cli_does_not_expose_output_path_overrides(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                pilot._parse_args(["--model-output", "models/convlstm.pt"])
            with self.assertRaises(SystemExit):
                pilot._parse_args(
                    ["--output-dir", "figures/warning_operational_draft_v4"]
                )


class PilotMaterializedArtifactTests(unittest.TestCase):
    def test_materialized_bundle_is_isolated_and_auditable(self):
        output_dir = pilot.DEFAULT_OUTPUT_DIR
        manifest_path = output_dir / "manifest.json"
        self.assertTrue(manifest_path.is_file())
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["artifact_status"], pilot.ARTIFACT_STATUS)
        self.assertEqual(manifest["case"], "ootang")
        self.assertFalse(manifest["formal_warning_output"])
        self.assertFalse(manifest["vajont_used"])
        self.assertFalse(manifest["default_pipeline_member"])
        self.assertEqual(manifest["target_class_support"]["calibration"]["red"], 0)
        self.assertEqual(
            manifest["feature_order"],
            [*pilot.MODEL_FEATURES, *pilot.CONTROL_FEATURES],
        )
        self.assertIn("acceleration", manifest["forbidden_predictors"])
        self.assertIn("vajont_use_or_validation", manifest["not_claimed"])

        protected_prefixes = (
            "figures/convlstm/",
            "figures/shap/",
            "figures/warning_operational_draft_v4/",
        )
        for record in manifest["outputs"].values():
            self.assertFalse(str(record["path"]).startswith(protected_prefixes))

        for record in manifest["outputs"].values():
            path = Path(record["path"])
            if not path.is_absolute():
                path = ROOT / path
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                record["sha256"],
            )

        predictions = pd.read_csv(output_dir / "predictions.csv")
        self.assertEqual(len(predictions), 11376)
        self.assertNotIn("acceleration", predictions.columns)
        self.assertFalse(predictions["vajont_used"].astype(bool).any())
        self.assertFalse(predictions["formal_warning_output"].astype(bool).any())
        np.testing.assert_allclose(
            predictions.loc[:, pilot.PROBABILITY_COLUMNS].sum(axis=1),
            1.0,
            rtol=1e-10,
            atol=1e-10,
        )

        with pilot.DEFAULT_MODEL_PATH.open("rb") as handle:
            bundle = pickle.load(handle)
        self.assertIsInstance(bundle["model"], NGBClassifier)
        self.assertEqual(bundle["class_order"], list(pilot.WARNING_COLORS))
        self.assertFalse(bundle["vajont_used"])


if __name__ == "__main__":
    unittest.main()
