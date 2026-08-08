"""Regression tests for separating legacy outputs from the formal path."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

SPEC = importlib.util.spec_from_file_location("isolated_pipeline_main", ROOT / "main.py")
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load main.py")
pipeline = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pipeline
SPEC.loader.exec_module(pipeline)

from explainability import shap_select, shap_stability  # noqa: E402
from features import tangent_stage_review  # noqa: E402
from warning import (  # noqa: E402
    ngboost_warn,
    onset_analysis,
    sensitivity_analysis,
    warning_fusion,
    warning_thresholds,
)
from warning.legacy_warning import write_legacy_warning_manifest  # noqa: E402


class LegacyWarningIsolationTests(unittest.TestCase):
    def test_legacy_fusion_frame_cannot_be_mistaken_for_a_formal_warning(self):
        dates = pd.date_range("2020-01-01", periods=80)
        raw = pd.DataFrame({"Date": dates, "MJ9/mm": range(80)})
        features = pd.DataFrame({
            "Date": dates,
            "MJ9_alpha_level": [0] * len(dates),
        })

        result, _ = warning_fusion.build_fusion_frame(
            features,
            raw,
            key_stations=("MJ9",),
            warning_stations={"MJ9": "MJ9/mm"},
        )

        self.assertEqual(set(result["warning_path"]), {"legacy_exploratory"})
        self.assertTrue((result["formal_warning_output"] == False).all())  # noqa: E712
        self.assertEqual(
            set(result["warning_method_id"]),
            {"legacy_30_day_v0_four_level_primary_secondary_fusion"},
        )

    def test_exported_30_day_v0_thresholds_are_marked_as_legacy(self):
        rows = warning_thresholds.threshold_rows(
            {"MJ9": {"v0_mm_per_month": 5.0}}
        )

        self.assertEqual(rows[0]["warning_path"], "legacy_exploratory")
        self.assertFalse(rows[0]["formal_warning_output"])
        self.assertEqual(
            rows[0]["warning_method_id"],
            "legacy_30_day_v0_four_level_primary_secondary_fusion",
        )

    def test_threshold_metadata_cannot_be_overridden_by_caller_values(self):
        rows = warning_thresholds.threshold_rows({
            "MJ9": {
                "warning_path": "formal",
                "formal_warning_output": True,
                "warning_method_id": "not_legacy",
            }
        })

        self.assertEqual(rows[0]["warning_path"], "legacy_exploratory")
        self.assertFalse(rows[0]["formal_warning_output"])
        self.assertEqual(
            rows[0]["warning_method_id"],
            "legacy_30_day_v0_four_level_primary_secondary_fusion",
        )

    def test_legacy_manifest_records_non_formal_status(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "legacy_warning_manifest.json"
            write_legacy_warning_manifest(
                manifest_path,
                producer="warning.example",
                artifacts=("figures/example.csv",),
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["warning_path"], "legacy_exploratory")
        self.assertFalse(manifest["formal_warning_output"])
        self.assertEqual(manifest["producer"], "warning.example")
        self.assertEqual(manifest["artifacts"], ["figures/example.csv"])

    def test_each_legacy_script_declares_a_non_formal_sidecar_manifest(self):
        expected = {
            onset_analysis.OUT_LEGACY_MANIFEST:
                "figures/warning_onset/legacy_warning_manifest.json",
            shap_select.OUT_LEGACY_MANIFEST:
                "figures/shap/legacy_warning_manifest.json",
            shap_stability.OUT_LEGACY_MANIFEST:
                "figures/shap/stability/legacy_warning_manifest.json",
            ngboost_warn.OUT_LEGACY_MANIFEST:
                "figures/ngboost/legacy_warning_manifest.json",
            ngboost_warn.OUT_MODEL_LEGACY_MANIFEST:
                "models/ngboost_legacy_warning_manifest.json",
            warning_fusion.OUT_LEGACY_MANIFEST:
                "figures/warning_fusion/legacy_warning_manifest.json",
            sensitivity_analysis.OUT_LEGACY_MANIFEST:
                "figures/sensitivity/legacy_warning_manifest.json",
            tangent_stage_review.OUT_LEGACY_MANIFEST:
                "figures/tangent_angle/review/legacy_warning_manifest.json",
        }

        for manifest, manifest_path in expected.items():
            with self.subTest(manifest=manifest):
                self.assertIn(
                    manifest_path,
                    str(manifest.relative_to(ROOT)),
                )

    def test_pipeline_manifest_marks_legacy_fusion_as_non_formal(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "run.json"
            report = pipeline.run_pipeline(
                [pipeline.STAGE_BY_NAME["fusion"]],
                runner=lambda command, **kwargs: subprocess.CompletedProcess(
                    command, 0
                ),
                manifest_path=manifest_path,
                verify_contracts=False,
            )

        self.assertFalse(report["formal_warning_output"])
        self.assertEqual(
            report["stages"][0]["warning_artifact_scope"],
            "legacy_exploratory",
        )
        self.assertFalse(report["stages"][0]["formal_warning_output"])

    def test_pipeline_contract_tracks_every_legacy_sidecar(self):
        expected = {
            "onset": {"figures/warning_onset/legacy_warning_manifest.json"},
            "shap": {"figures/shap/legacy_warning_manifest.json"},
            "shap-stability": {
                "figures/shap/stability/legacy_warning_manifest.json"
            },
            "ngboost": {
                "figures/ngboost/legacy_warning_manifest.json",
                "models/ngboost_legacy_warning_manifest.json",
            },
            "fusion": {"figures/warning_fusion/legacy_warning_manifest.json"},
            "sensitivity": {
                "figures/sensitivity/legacy_warning_manifest.json"
            },
            "tangent-review": {
                "figures/tangent_angle/review/legacy_warning_manifest.json"
            },
        }
        legacy_stages = {
            stage.name
            for stage in pipeline.STAGES
            if stage.warning_artifact_scope == "legacy_exploratory"
        }

        self.assertEqual(set(expected), legacy_stages)

        for stage_name, sidecars in expected.items():
            with self.subTest(stage=stage_name):
                stage = pipeline.STAGE_BY_NAME[stage_name]
                self.assertTrue(sidecars.issubset(set(stage.outputs)))
                self.assertFalse(stage.enabled_by_default)


if __name__ == "__main__":
    unittest.main()
