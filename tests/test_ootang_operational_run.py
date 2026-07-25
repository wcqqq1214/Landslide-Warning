"""Integration contract for the non-formal Ootang implementation run."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning import draft_evidence
from warning.operational_run import (
    DEFAULT_OUTPUT_DIR,
    OOTANG_STATIONS,
    OperationalRunProfileError,
    _classify_velocity,
    _site_record,
    write_ootang_operational_run,
)


class OotangOperationalRunTests(unittest.TestCase):
    def test_pipeline_registers_nonformal_operational_stage(self):
        pipeline_spec = importlib.util.spec_from_file_location(
            "ootang_operational_pipeline",
            ROOT / "main.py",
        )
        if pipeline_spec is None or pipeline_spec.loader is None:
            self.fail("cannot load main.py")
        pipeline = importlib.util.module_from_spec(pipeline_spec)
        sys.modules[pipeline_spec.name] = pipeline
        pipeline_spec.loader.exec_module(pipeline)

        names = [stage.name for stage in pipeline.STAGES]
        stage = pipeline.STAGE_BY_NAME["ootang-operational"]
        self.assertEqual(
            names.index("ootang-operational"), names.index("convlstm-capacity") + 1
        )
        self.assertEqual(stage.warning_artifact_scope, "operational_draft")
        self.assertFalse(stage.formal_warning_output)
        self.assertIn(
            "figures/warning_operational_draft/ootang_operational_run_manifest.json",
            stage.outputs,
        )

    def test_pipeline_registers_versioned_v2_spatial_operational_stage(self):
        pipeline_spec = importlib.util.spec_from_file_location(
            "ootang_operational_v2_pipeline",
            ROOT / "main.py",
        )
        if pipeline_spec is None or pipeline_spec.loader is None:
            self.fail("cannot load main.py")
        pipeline = importlib.util.module_from_spec(pipeline_spec)
        sys.modules[pipeline_spec.name] = pipeline
        pipeline_spec.loader.exec_module(pipeline)

        stage = pipeline.STAGE_BY_NAME["ootang-operational-v2"]
        self.assertEqual(stage.script, "code/warning/operational_run_v2.py")
        self.assertEqual(stage.warning_artifact_scope, "operational_draft")
        self.assertFalse(stage.formal_warning_output)
        self.assertIn(
            "figures/warning_operational_draft_v2/ootang_operational_run_manifest.json",
            stage.outputs,
        )

    def test_writes_a_complete_nonformal_ootang_timeline(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            artifacts = write_ootang_operational_run(
                output_dir=output_dir,
                evidence_dir=output_dir / "evidence",
            )
            manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))
            station_rows = pd.read_csv(artifacts.station_timeline_path)
            site_rows = pd.read_csv(artifacts.site_timeline_path)
            threshold_rows = pd.read_csv(artifacts.thresholds_path)

        self.assertEqual(manifest["artifact_status"], "operational_draft_not_formal")
        self.assertFalse(manifest["formal_warning_output"])
        self.assertFalse(manifest["vajont_used"])
        self.assertEqual(manifest["ootang_stations"], list(OOTANG_STATIONS))
        self.assertEqual(manifest["parameter_fit_splits"], ["fit"])
        self.assertEqual(manifest["result_splits"], ["calibration", "test"])
        self.assertEqual(set(station_rows["station"]), set(OOTANG_STATIONS))
        self.assertEqual(set(station_rows["split"]), {"calibration", "test"})
        self.assertFalse(station_rows["formal_warning_output"].any())
        self.assertTrue(
            station_rows["velocity_baseline_source"].eq(
                "raw_velocity_kmeans_comparator"
            ).all()
        )
        self.assertTrue(site_rows["formal_warning_output"].eq(False).all())
        self.assertEqual(site_rows["date"].nunique(), len(site_rows))
        self.assertIn("operational_profile", manifest)
        self.assertIn("base_draft_protocol", manifest)
        for frame in (station_rows, site_rows, threshold_rows):
            self.assertTrue(
                frame["artifact_status"].eq("operational_draft_not_formal").all()
            )
            self.assertTrue(frame["formal_warning_output"].eq(False).all())
            self.assertTrue(frame["vajont_used"].eq(False).all())

    def test_writes_a_versioned_v2_spatial_timeline_without_changing_v0_status(self):
        profile_path = ROOT / "config" / "ootang_operational_run.v2.draft.json"
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            artifacts = write_ootang_operational_run(
                profile_path=profile_path,
                output_dir=output_dir,
                evidence_dir=output_dir / "evidence",
            )
            manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))
            station_rows = pd.read_csv(artifacts.station_timeline_path)
            site_rows = pd.read_csv(artifacts.site_timeline_path)

        self.assertEqual(
            manifest["operational_profile"]["id"],
            "ootang-operational-spatial-v2",
        )
        self.assertFalse(manifest["formal_warning_output"])
        self.assertFalse(manifest["vajont_used"])
        self.assertTrue(
            station_rows["velocity_baseline_source"].eq(
                "raw_velocity_kmeans_comparator"
            ).all()
        )
        self.assertTrue(station_rows["station_assessment_status"].eq("valid").all())
        self.assertIn("kinematic_level", station_rows.columns)
        self.assertIn("station_confirmation_status", station_rows.columns)
        self.assertTrue(site_rows["minimum_assessable_station_count"].eq(3).all())
        self.assertTrue(site_rows["assessable_block_count"].eq(3).all())
        self.assertTrue(
            site_rows["cross_block_confirmation_minimum_color"].eq("yellow").all()
        )
        self.assertFalse(
            site_rows["site_fusion_status"].eq(
                "insufficient_assessable_coverage"
            ).any()
        )
        spatial_source = manifest["source_inputs"]["spatial_block_topology"]
        self.assertEqual(spatial_source["doi"], "10.1029/2025JH000592")
        self.assertEqual(spatial_source["pdf_page"], 7)
        self.assertEqual(spatial_source["figure"], "Figure 4(a, d)")
        self.assertEqual(
            spatial_source["sha256"],
            "d2ae22029288dd2eca5ed888b864342d624b36ee233f35f14119e23a511553df",
        )

    def test_v2_profile_cannot_overwrite_the_preserved_v1_output_directory(self):
        profile_path = ROOT / "config" / "ootang_operational_run.v2.draft.json"

        with self.assertRaisesRegex(OperationalRunProfileError, "preserved v1"):
            write_ootang_operational_run(
                profile_path=profile_path,
                output_dir=DEFAULT_OUTPUT_DIR,
            )

    def test_test_period_prediction_change_does_not_change_fit_thresholds(self):
        source_predictions = ROOT / "figures" / "convlstm" / "forecast_predictions.csv"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_output = root / "first"
            altered_predictions = root / "altered_predictions.csv"
            predictions = pd.read_csv(source_predictions)
            test_index = predictions.index[predictions["split"].eq("test")][0]
            predictions.loc[test_index, "actual"] += 10_000.0
            predictions.to_csv(altered_predictions, index=False)

            first = write_ootang_operational_run(
                output_dir=first_output,
                evidence_dir=root / "first_evidence",
            )
            altered = write_ootang_operational_run(
                predictions_path=altered_predictions,
                output_dir=root / "altered",
                evidence_dir=root / "altered_evidence",
            )

            self.assertEqual(
                first.thresholds_path.read_bytes(),
                altered.thresholds_path.read_bytes(),
            )

    def test_rejects_a_profile_that_claims_formal_warning_output(self):
        profile_path = ROOT / "config" / "ootang_operational_run.v1.draft.json"
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        profile["formal_warning_output"] = True

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            invalid_profile = root / "invalid_profile.json"
            output_dir = root / "output"
            invalid_profile.write_text(json.dumps(profile), encoding="utf-8")

            with self.assertRaises(OperationalRunProfileError):
                write_ootang_operational_run(
                    profile_path=invalid_profile,
                    output_dir=output_dir,
                )

            self.assertFalse(output_dir.exists())

    def test_rejects_a_profile_with_a_drifted_base_protocol_version(self):
        profile_path = ROOT / "config" / "ootang_operational_run.v1.draft.json"
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        profile["base_draft_protocol"]["path"] = str(
            ROOT / "config" / "ootang_warning_protocol.v1.draft.json"
        )
        profile["base_draft_protocol"]["expected_protocol_version"] = "9.9-draft"

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            drifted_profile = root / "drifted_profile.json"
            output_dir = root / "output"
            drifted_profile.write_text(json.dumps(profile), encoding="utf-8")

            with self.assertRaisesRegex(OperationalRunProfileError, "protocol_version"):
                write_ootang_operational_run(
                    profile_path=drifted_profile,
                    output_dir=output_dir,
                )

            self.assertFalse(output_dir.exists())

    def test_configured_velocity_range_boundary_changes_classification(self):
        profile_path = ROOT / "config" / "ootang_operational_run.v1.draft.json"
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        mapping = profile["velocity_baseline"]["mapping"]
        baseline = _classify_velocity(
            velocity=11.0,
            status="valid",
            lower_blue=0.9,
            upper_blue=1.1,
            v0=1.0,
            sigma=0.1,
            mapping=mapping,
        )
        mapping[3]["upper"]["multiple"] = 12.0
        mapping[4]["lower"]["multiple"] = 12.0
        changed = _classify_velocity(
            velocity=11.0,
            status="valid",
            lower_blue=0.9,
            upper_blue=1.1,
            v0=1.0,
            sigma=0.1,
            mapping=mapping,
        )

        self.assertEqual(baseline["velocity_color"], "red")
        self.assertEqual(changed["velocity_color"], "orange")

    def test_site_reason_uses_the_configured_support_count(self):
        profile_path = ROOT / "config" / "ootang_operational_run.v1.draft.json"
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        profile["site_fusion"]["minimum_supporting_stations"] = 3
        rows = pd.DataFrame(
            {
                "station": list(OOTANG_STATIONS),
                "split": ["calibration"] * len(OOTANG_STATIONS),
                "fusion_status": ["valid"] * 7 + ["uncorroborated"],
                "final_level": [2, 2, 0, 0, 0, 0, 0, None],
            }
        )

        record = _site_record(
            pd.Timestamp("2019-02-03"),
            rows,
            profile=profile,
        )

        self.assertEqual(record["site_fusion_status"], "uncorroborated")
        self.assertEqual(record["site_fusion_reason"], "no_3_station_elevated_support")

    def test_promotion_failure_restores_the_previous_operational_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_dir = root / "operational"
            first = write_ootang_operational_run(
                output_dir=output_dir,
                evidence_dir=root / "evidence",
            )
            snapshot_paths = (
                first.station_timeline_path,
                first.site_timeline_path,
                first.thresholds_path,
                first.manifest_path,
            )
            snapshot = {path: path.read_bytes() for path in snapshot_paths}
            original_replace = draft_evidence.os.replace
            promotion_writes = 0
            live_output_dir = output_dir.resolve()

            def fail_once_during_operational_promotion(source, target):
                nonlocal promotion_writes
                if Path(target).resolve().parent == live_output_dir:
                    promotion_writes += 1
                    if promotion_writes == 2:
                        raise OSError("simulated operational promotion failure")
                return original_replace(source, target)

            with patch.object(
                draft_evidence.os,
                "replace",
                side_effect=fail_once_during_operational_promotion,
            ), self.assertRaisesRegex(OSError, "simulated operational promotion"):
                write_ootang_operational_run(
                    output_dir=output_dir,
                    evidence_dir=root / "evidence",
                )

            self.assertGreaterEqual(promotion_writes, 2)
            self.assertEqual(
                {path: path.read_bytes() for path in snapshot_paths},
                snapshot,
            )


if __name__ == "__main__":
    unittest.main()
