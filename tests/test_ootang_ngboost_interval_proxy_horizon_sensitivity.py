"""Contracts for the fixed Ootang h=1/3/7 NGBoost sensitivity."""

from __future__ import annotations

from contextlib import redirect_stderr
from copy import deepcopy
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

from warning import ootang_ngboost_interval_proxy_pilot as base  # noqa: E402
from warning import ootang_ngboost_interval_proxy_horizon_sensitivity as sensitivity  # noqa: E402


class SensitivityDataMixin:
    @classmethod
    def setUpClass(cls):
        cls.profile, cls.base_profile, cls.base_profile_path = (
            sensitivity.load_sensitivity_profile()
        )
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
            item for item in cls.profile["horizons"]
            if item["horizon_days"] == horizon
        )
        derived = sensitivity._horizon_profile(
            cls.base_profile,
            cls.profile,
            record,
        )
        return base.build_future_interval_proxy_samples(
            cls.predictions,
            cls.kinematics,
            cls.thresholds,
            derived,
        )


class SensitivityProfileTests(SensitivityDataMixin, unittest.TestCase):
    def test_profile_locks_horizons_reporting_and_base_model(self):
        self.assertEqual(
            [item["horizon_days"] for item in self.profile["horizons"]],
            [1, 3, 7],
        )
        self.assertEqual(self.profile["case"], "ootang")
        self.assertFalse(self.profile["formal_warning_output"])
        self.assertFalse(self.profile["vajont_used"])
        self.assertFalse(self.profile["reporting"]["selection_performed"])
        self.assertFalse(self.profile["reporting"]["ranking_performed"])
        self.assertIsNone(self.profile["reporting"]["selected_horizon_days"])
        self.assertIsNone(self.profile["reporting"]["optimal_horizon_days"])
        self.assertEqual(self.base_profile["model"]["class"], "NGBClassifier")
        self.assertEqual(
            [item["name"] for item in self.base_profile["features"]["scientific"]],
            list(base.MODEL_FEATURES),
        )

    def test_profile_rejects_reordered_horizons_and_selection(self):
        for mutation in ("horizons", "selection", "case", "base_hash"):
            profile = deepcopy(self.profile)
            if mutation == "horizons":
                profile["horizons"] = list(reversed(profile["horizons"]))
            elif mutation == "selection":
                profile["reporting"]["selected_horizon_days"] = 3
                profile["reporting"]["selection_performed"] = True
            elif mutation == "case":
                profile["case"] = "vajont"
            else:
                profile["base_pilot_profile"]["expected_file_sha256"] = "0" * 64
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "profile.json"
                path.write_text(json.dumps(profile), encoding="utf-8")
                with self.assertRaises(sensitivity.SensitivityProfileError):
                    sensitivity.load_sensitivity_profile(path)

    def test_committed_h1_baseline_hashes_are_locked(self):
        records = sensitivity.validate_base_pilot_artifacts(self.profile)
        self.assertEqual(set(records), set(sensitivity.EXPECTED_BASE_ARTIFACTS))
        for name, contract in sensitivity.EXPECTED_BASE_ARTIFACTS.items():
            if name == "manifest":
                continue
            self.assertEqual(records[name]["sha256"], contract["expected_sha256"])
    def test_base_manifest_volatile_provenance_may_change_but_payloads_may_not(self):
        manifest_path = Path(
            self.profile["base_pilot_artifacts"]["manifest"]["path"]
        )
        if not manifest_path.is_absolute():
            manifest_path = ROOT / manifest_path
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["git_commit"] = "future-commit"
        manifest["git_worktree_dirty"] = False
        with tempfile.TemporaryDirectory() as directory:
            temporary_manifest = Path(directory) / "manifest.json"
            temporary_manifest.write_text(json.dumps(manifest), encoding="utf-8")
            profile = deepcopy(self.profile)
            profile["base_pilot_artifacts"]["manifest"]["path"] = str(
                temporary_manifest
            )
            records = sensitivity.validate_base_pilot_artifacts(profile)
            self.assertEqual(
                records["manifest"]["path"],
                str(temporary_manifest.resolve()),
            )

            manifest["outputs"]["predictions"]["sha256"] = "0" * 64
            temporary_manifest.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(
                sensitivity.SensitivityInputError, "frozen predictions payload"
            ):
                sensitivity.validate_base_pilot_artifacts(profile)


class SensitivityPairingTests(SensitivityDataMixin, unittest.TestCase):
    def test_all_horizons_have_exact_counts_support_and_gaps(self):
        for record in self.profile["horizons"]:
            horizon = record["horizon_days"]
            samples = self.samples_for_horizon(horizon)
            self.assertEqual(
                samples.groupby("split", sort=False).size().to_dict(),
                record["expected_sample_counts"],
            )
            gap = (
                pd.to_datetime(samples["target_date"])
                - pd.to_datetime(samples["date"])
            ).dt.days
            self.assertTrue(gap.eq(horizon).all())
            self.assertEqual(
                11400 - len(samples),
                record["terminal_source_rows_excluded"],
            )
            for split in base.SPLIT_ORDER:
                scoped = samples.loc[samples["split"].eq(split)]
                counts = (
                    scoped["target_interval_color"]
                    .value_counts()
                    .reindex(base.WARNING_COLORS, fill_value=0)
                    .to_dict()
                )
                self.assertEqual(
                    counts,
                    record["expected_target_class_counts"][split],
                )
                transitions = int(scoped["transition_type"].ne("stable").sum())
                self.assertEqual(
                    transitions,
                    record["expected_transition_counts"][split],
                )
                correct = int(
                    scoped["current_interval_level"]
                    .eq(scoped["target_interval_level"])
                    .sum()
                )
                self.assertEqual(
                    correct,
                    record["expected_persistence_correct"][split],
                )
            self.assertEqual(
                set(
                    samples.loc[samples["split"].eq("fit"), "target_interval_level"]
                ),
                set(base.CLASS_LEVELS),
            )
            self.assertEqual(
                int(
                    samples.loc[
                        samples["split"].eq("calibration"),
                        "target_interval_level",
                    ].eq(4).sum()
                ),
                0,
            )

    def test_model_matrix_is_identical_across_horizons(self):
        expected_columns = [*base.MODEL_FEATURES, *base.CONTROL_FEATURES]
        for horizon in sensitivity.HORIZONS:
            X, _ = base.build_model_matrix(self.samples_for_horizon(horizon))
            self.assertEqual(list(X.columns), expected_columns)
            self.assertFalse(
                any(
                    token in column.lower()
                    for column in X.columns
                    for token in ("acceleration", "rain", "rwl", "vajont")
                )
            )


class SensitivitySummaryTests(unittest.TestCase):
    def test_summary_is_nonranking_and_deltas_recompute(self):
        baseline = pd.read_csv(base.DEFAULT_OUTPUT_DIR / "metrics.csv")
        baseline = baseline.loc[baseline["split"].isin(sensitivity.EVALUATION_SPLITS)]
        combined = pd.concat(
            [baseline.assign(horizon_days=horizon) for horizon in sensitivity.HORIZONS],
            ignore_index=True,
        )
        summary = sensitivity.build_horizon_summary(combined)

        self.assertEqual(len(summary), 96)
        self.assertFalse(
            {"rank", "winner", "selected_horizon", "optimal_horizon"}.intersection(
                summary.columns
            )
        )
        finite = summary.dropna(
            subset=["ngboost_value", "current_state_persistence_value"]
        )
        np.testing.assert_allclose(
            finite["ngboost_minus_current_state_persistence"],
            finite["ngboost_value"] - finite["current_state_persistence_value"],
        )

    def test_h1_parity_checker_rejects_changed_candidate(self):
        baseline = pd.read_csv(sensitivity.DEFAULT_BASE_PREDICTIONS_PATH)
        candidate = baseline.loc[
            baseline["split"].isin(sensitivity.EVALUATION_SPLITS)
        ].copy()
        candidate.loc[candidate.index[0], "prob_green"] += 0.001
        candidate.loc[candidate.index[0], "prob_blue"] -= 0.001
        with self.assertRaisesRegex(
            sensitivity.SensitivityOutputError, "numeric/probability parity"
        ):
            sensitivity.verify_h1_parity(candidate)


class SensitivityOutputIsolationTests(unittest.TestCase):
    def test_repository_overrides_cannot_target_existing_artifacts(self):
        protected_models = deepcopy(sensitivity.DEFAULT_MODEL_PATHS)
        protected_models[3] = base.DEFAULT_MODEL_PATH
        with self.assertRaisesRegex(sensitivity.SensitivityOutputError, "model_h3"):
            sensitivity.write_ootang_ngboost_interval_proxy_horizon_sensitivity(
                model_paths=protected_models
            )
        with self.assertRaisesRegex(sensitivity.SensitivityOutputError, "output_dir"):
            sensitivity.write_ootang_ngboost_interval_proxy_horizon_sensitivity(
                output_dir=base.DEFAULT_OUTPUT_DIR
            )

    def test_external_same_basenames_stage_uniquely_and_overlaps_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            models = {
                horizon: root / f"h{horizon}" / "model.pkl"
                for horizon in sensitivity.HORIZONS
            }
            sensitivity._validate_output_destinations(root / "bundle", models)
            staged = sensitivity._staged_model_paths(root / "staging", models)
            self.assertEqual(len(set(staged.values())), 3)
            self.assertEqual(
                [staged[horizon].name for horizon in sensitivity.HORIZONS],
                ["h1-model.pkl", "h3-model.pkl", "h7-model.pkl"],
            )

            duplicated = deepcopy(models)
            duplicated[3] = duplicated[1]
            with self.assertRaisesRegex(
                sensitivity.SensitivityOutputError, "must be distinct"
            ):
                sensitivity._validate_output_destinations(
                    root / "bundle", duplicated
                )

            overlapping = deepcopy(models)
            overlapping[7] = root / "bundle" / "predictions.csv"
            with self.assertRaisesRegex(
                sensitivity.SensitivityOutputError, "cannot overlap"
            ):
                sensitivity._validate_output_destinations(
                    root / "bundle", overlapping
                )

            directory_target = deepcopy(models)
            directory_target[1] = root / "bundle"
            with self.assertRaisesRegex(
                sensitivity.SensitivityOutputError, "cannot equal or contain"
            ):
                sensitivity._validate_output_destinations(
                    root / "bundle", directory_target
                )

            existing_directory = root / "existing-model-directory"
            existing_directory.mkdir()
            directory_target = deepcopy(models)
            directory_target[1] = existing_directory
            with self.assertRaisesRegex(
                sensitivity.SensitivityOutputError, "must be files"
            ):
                sensitivity._validate_output_destinations(
                    root / "bundle", directory_target
                )

    def test_cli_exposes_no_output_path_override(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                sensitivity._parse_args(["--output-dir", "figures/convlstm"])


class SensitivityMaterializedArtifactTests(unittest.TestCase):
    def test_materialized_bundle_is_nonranking_isolated_and_complete(self):
        manifest_path = sensitivity.DEFAULT_OUTPUT_DIR / "manifest.json"
        self.assertTrue(manifest_path.is_file())
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["case"], "ootang")
        self.assertFalse(manifest["formal_warning_output"])
        self.assertFalse(manifest["vajont_used"])
        self.assertFalse(manifest["selection_performed"])
        self.assertFalse(manifest["ranking_performed"])
        self.assertIsNone(manifest["selected_horizon_days"])
        self.assertIsNone(manifest["optimal_horizon_days"])
        self.assertEqual(manifest["h1_parity"]["status"], "exact_within_1e-12")
        self.assertIn("selected_or_optimal_horizon", manifest["not_claimed"])

        expected_rows = {
            "predictions": 12160,
            "metrics": 1584,
            "confusion_matrices": 900,
            "reliability": 1440,
            "horizon_summary": 96,
        }
        for name, rows in expected_rows.items():
            path = sensitivity.DEFAULT_OUTPUT_DIR / f"{name}.csv"
            self.assertEqual(len(pd.read_csv(path)), rows)

        summary = pd.read_csv(
            sensitivity.DEFAULT_OUTPUT_DIR / "horizon_summary.csv"
        )
        self.assertEqual(summary["horizon_days"].drop_duplicates().tolist(), [1, 3, 7])
        self.assertFalse(
            any(
                token in column.lower()
                for column in summary.columns
                for token in ("rank", "winner", "optimal", "selected")
            )
        )
        metrics = pd.read_csv(sensitivity.DEFAULT_OUTPUT_DIR / "metrics.csv")
        recomputed_summary = sensitivity.build_horizon_summary(metrics)
        pd.testing.assert_frame_equal(
            summary.reset_index(drop=True),
            recomputed_summary.reset_index(drop=True),
            check_dtype=False,
            rtol=1e-12,
            atol=1e-12,
        )

        predictions = pd.read_csv(
            sensitivity.DEFAULT_OUTPUT_DIR / "predictions.csv"
        )
        self.assertEqual(
            predictions.groupby("horizon_days", sort=False).size().to_dict(),
            {1: 4096, 3: 4064, 7: 4000},
        )
        h1_parity = sensitivity.verify_h1_parity(
            predictions.loc[predictions["horizon_days"].eq(1)]
        )
        self.assertEqual(h1_parity["status"], "exact_within_1e-12")
        self.assertEqual(h1_parity["maximum_absolute_probability_difference"], 0.0)
        self.assertFalse(predictions["vajont_used"].astype(bool).any())
        self.assertFalse(predictions["formal_warning_output"].astype(bool).any())
        self.assertNotIn("acceleration", predictions.columns)

        for horizon, path in sensitivity.DEFAULT_MODEL_PATHS.items():
            self.assertTrue(path.is_file())
            with path.open("rb") as handle:
                bundle = pickle.load(handle)
            self.assertEqual(bundle["horizon_days"], horizon)
            self.assertIsInstance(bundle["model"], NGBClassifier)
            self.assertFalse(bundle["vajont_used"])

        for record in manifest["outputs"].values():
            path = Path(record["path"])
            if not path.is_absolute():
                path = ROOT / path
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                record["sha256"],
            )


if __name__ == "__main__":
    unittest.main()
