"""Integration contract for the non-formal Ootang implementation run."""

from __future__ import annotations

import hashlib
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
    DEFAULT_V2_OUTPUT_DIR,
    DEFAULT_V3_OUTPUT_DIR,
    OOTANG_STATIONS,
    OperationalRunInputError,
    OperationalRunProfileError,
    _classify_velocity,
    _load_operational_profile,
    _site_record,
    _spatial_block_source_manifest,
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

    def test_pipeline_registers_versioned_v3_dual_axis_operational_stage(self):
        pipeline_spec = importlib.util.spec_from_file_location(
            "ootang_operational_v3_pipeline",
            ROOT / "main.py",
        )
        if pipeline_spec is None or pipeline_spec.loader is None:
            self.fail("cannot load main.py")
        pipeline = importlib.util.module_from_spec(pipeline_spec)
        sys.modules[pipeline_spec.name] = pipeline
        pipeline_spec.loader.exec_module(pipeline)

        names = [stage.name for stage in pipeline.STAGES]
        stage = pipeline.STAGE_BY_NAME["ootang-operational-v3"]
        self.assertEqual(
            names.index("ootang-operational-v3"),
            names.index("ootang-operational-v2") + 1,
        )
        self.assertEqual(stage.script, "code/warning/operational_run_v3.py")
        self.assertEqual(stage.warning_artifact_scope, "operational_draft")
        self.assertFalse(stage.formal_warning_output)
        self.assertEqual(len(stage.inputs), 7)
        self.assertEqual(len(stage.outputs), 16)
        self.assertIn(
            "config/ootang_operational_v3_typical_days.v1.json",
            stage.inputs,
        )
        self.assertIn(
            "config/ootang_operational_v3_station_diagnostic.v1.json",
            stage.inputs,
        )
        self.assertIn(
            "figures/warning_operational_draft_v3/ootang_operational_run_manifest.json",
            stage.outputs,
        )
        self.assertIn(
            "figures/warning_operational_draft_v3/ootang_v3_typical_days.svg",
            stage.outputs,
        )
        self.assertIn(
            "figures/warning_operational_draft_v3/ootang_v3_typical_days_manifest.json",
            stage.outputs,
        )
        self.assertIn(
            "figures/warning_operational_draft_v3/ootang_v3_full_warning_timeline.svg",
            stage.outputs,
        )
        self.assertIn(
            "figures/warning_operational_draft_v3/ootang_v3_full_warning_timeline_manifest.json",
            stage.outputs,
        )
        self.assertIn(
            "figures/warning_operational_draft_v3/ootang_v3_all_station_combined_diagnostic.svg",
            stage.outputs,
        )
        self.assertIn(
            "figures/warning_operational_draft_v3/ootang_v3_all_station_combined_diagnostic_manifest.json",
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
        forecast_source = manifest["source_inputs"]["forecast_run_manifest"]
        self.assertTrue(forecast_source["prediction_sha256_matches"])
        self.assertEqual(
            forecast_source["elevation_usage"],
            "static_model_input_channel",
        )
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
        self.assertIn("trend_component", station_rows.columns)
        self.assertIn("transition_status", station_rows.columns)
        self.assertIn("evidence_consistency_status", station_rows.columns)
        self.assertIn("composite_warning_signal", station_rows.columns)
        self.assertEqual(
            set(station_rows["trend_component"]),
            {"delta_v_negative", "delta_v_near_zero", "delta_v_positive"},
        )
        self.assertTrue(
            station_rows.apply(
                lambda row: f"delta_v_{row['delta_v_state']}"
                in row["composite_warning_signal"],
                axis=1,
            ).all()
        )
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

    def test_writes_a_versioned_v3_dual_axis_timeline(self):
        profile_path = ROOT / "config" / "ootang_operational_run.v3.draft.json"
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
            threshold_rows = pd.read_csv(artifacts.thresholds_path)

        v2_dir = ROOT / "figures" / "warning_operational_draft_v2"
        v2_station_rows = pd.read_csv(
            v2_dir / "ootang_operational_station_timeline.csv"
        )
        v2_threshold_rows = pd.read_csv(
            v2_dir / "ootang_operational_thresholds.csv"
        )

        self.assertEqual(
            manifest["operational_profile"]["id"],
            "ootang-operational-spatial-v3",
        )
        self.assertFalse(manifest["formal_warning_output"])
        self.assertFalse(manifest["vajont_used"])
        self.assertEqual(
            manifest["site_output_contract"]["version"],
            "v3_dual_axis_1",
        )
        self.assertEqual(
            set(manifest["implementation_sources"]),
            {"runner", "station_fusion", "site_fusion", "spatial_blocks"},
        )
        for source in manifest["implementation_sources"].values():
            self.assertFalse(Path(source["path"]).is_absolute())
            source_path = ROOT / source["path"]
            self.assertEqual(
                hashlib.sha256(source_path.read_bytes()).hexdigest(),
                source["sha256"],
            )
        self.assertEqual(
            site_rows["site_fusion_status"].value_counts().to_dict(),
            {"candidate_not_site_confirmed": 400, "valid": 114},
        )
        self.assertEqual(
            site_rows["site_confirmed_color"].value_counts().to_dict(),
            {"blue": 48, "yellow": 31, "red": 18, "orange": 9, "green": 8},
        )
        self.assertEqual(
            site_rows["local_max_candidate_color"].value_counts().to_dict(),
            {"yellow": 196, "red": 151, "orange": 111, "blue": 56},
        )
        self.assertEqual(
            site_rows["local_attention_status"].value_counts().to_dict(),
            {"none": 506, "localized_blue_attention": 8},
        )
        self.assertTrue(site_rows["coverage_complete"].all())
        pd.testing.assert_series_equal(
            site_rows["site_level"],
            site_rows["site_confirmed_level"],
            check_names=False,
        )
        pd.testing.assert_series_equal(
            site_rows["site_candidate_level"],
            site_rows["local_max_candidate_level"],
            check_names=False,
        )
        profile_columns = {
            "operational_profile_id",
            "operational_profile_version",
        }
        station_contract_columns = [
            column
            for column in v2_station_rows.columns
            if column not in profile_columns
        ]
        threshold_contract_columns = [
            column
            for column in v2_threshold_rows.columns
            if column not in profile_columns
        ]
        pd.testing.assert_frame_equal(
            station_rows[station_contract_columns],
            v2_station_rows[station_contract_columns],
            check_dtype=False,
        )
        pd.testing.assert_frame_equal(
            threshold_rows[threshold_contract_columns],
            v2_threshold_rows[threshold_contract_columns],
            check_dtype=False,
        )

    def test_spatial_source_metadata_is_reproducible_without_local_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            config_dir = Path(directory) / "config"
            config_dir.mkdir()
            profile_path = config_dir / "ootang_operational_run.v3.draft.json"
            base_path = config_dir / "ootang_warning_protocol.v1.draft.json"
            profile_path.write_bytes(
                (
                    ROOT / "config" / "ootang_operational_run.v3.draft.json"
                ).read_bytes()
            )
            base_path.write_bytes(
                (
                    ROOT / "config" / "ootang_warning_protocol.v1.draft.json"
                ).read_bytes()
            )

            loaded = _load_operational_profile(profile_path)
            source = _spatial_block_source_manifest(loaded)

        self.assertIsNotNone(source)
        self.assertFalse(source["source_file_available_at_run"])
        self.assertEqual(
            source["verification_status"],
            "profile_declared_reviewed_sha256_only",
        )
        self.assertEqual(
            source["sha256"],
            "d2ae22029288dd2eca5ed888b864342d624b36ee233f35f14119e23a511553df",
        )

    def test_spatial_source_rejects_a_drifted_declared_fingerprint(self):
        profile = json.loads(
            (
                ROOT / "config" / "ootang_operational_run.v3.draft.json"
            ).read_text(encoding="utf-8")
        )
        profile["site_fusion"]["spatial_blocks_source"][
            "source_file_sha256"
        ] = "0" * 64

        with tempfile.TemporaryDirectory() as directory:
            config_dir = Path(directory) / "config"
            config_dir.mkdir()
            profile_path = config_dir / "ootang_operational_run.v3.draft.json"
            base_path = config_dir / "ootang_warning_protocol.v1.draft.json"
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            base_path.write_bytes(
                (
                    ROOT / "config" / "ootang_warning_protocol.v1.draft.json"
                ).read_bytes()
            )

            with self.assertRaisesRegex(
                OperationalRunProfileError,
                "reviewed paper fingerprint",
            ):
                _load_operational_profile(profile_path)

    def test_spatial_source_rejects_an_incorrect_local_pdf(self):
        source_profile = (
            ROOT / "config" / "ootang_operational_run.v3.draft.json"
        )
        profile = json.loads(source_profile.read_text(encoding="utf-8"))
        source_name = Path(
            profile["site_fusion"]["spatial_blocks_source"]["source_file"]
        ).name

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_dir = root / "config"
            literature_dir = root / "literature"
            config_dir.mkdir()
            literature_dir.mkdir()
            profile_path = config_dir / source_profile.name
            profile_path.write_bytes(source_profile.read_bytes())
            (config_dir / "ootang_warning_protocol.v1.draft.json").write_bytes(
                (
                    ROOT / "config" / "ootang_warning_protocol.v1.draft.json"
                ).read_bytes()
            )
            (literature_dir / source_name).write_bytes(b"incorrect local source")

            with self.assertRaisesRegex(
                OperationalRunProfileError,
                "file fingerprint does not match",
            ):
                _load_operational_profile(profile_path)

    def test_tracked_v3_implementation_fingerprints_match_sources(self):
        manifest_path = (
            ROOT
            / "figures"
            / "warning_operational_draft_v3"
            / "ootang_operational_run_manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(
            set(manifest["implementation_sources"]),
            {"runner", "station_fusion", "site_fusion", "spatial_blocks"},
        )
        for source in manifest["implementation_sources"].values():
            self.assertFalse(Path(source["path"]).is_absolute())
            source_path = ROOT / source["path"]
            self.assertEqual(
                hashlib.sha256(source_path.read_bytes()).hexdigest(),
                source["sha256"],
                source_path,
            )

    def test_v2_profile_cannot_overwrite_the_preserved_v1_output_directory(self):
        profile_path = ROOT / "config" / "ootang_operational_run.v2.draft.json"

        with self.assertRaisesRegex(OperationalRunProfileError, "preserved v1"):
            write_ootang_operational_run(
                profile_path=profile_path,
                output_dir=DEFAULT_OUTPUT_DIR,
            )

    def test_v3_and_v2_profiles_cannot_overwrite_each_others_directories(self):
        v2_profile = ROOT / "config" / "ootang_operational_run.v2.draft.json"
        v3_profile = ROOT / "config" / "ootang_operational_run.v3.draft.json"

        with self.assertRaisesRegex(OperationalRunProfileError, "preserved v2"):
            write_ootang_operational_run(
                profile_path=v3_profile,
                output_dir=DEFAULT_V2_OUTPUT_DIR,
            )
        with self.assertRaisesRegex(OperationalRunProfileError, "preserved v3"):
            write_ootang_operational_run(
                profile_path=v2_profile,
                output_dir=DEFAULT_V3_OUTPUT_DIR,
            )

    def test_v3_profile_freezes_global_coverage_and_cross_block_support(self):
        source = ROOT / "config" / "ootang_operational_run.v3.draft.json"
        cases = (
            ("minimum_assessable_station_count", 4, "exactly 3"),
            ("minimum_supporting_stations", 1, "exactly 2"),
            ("minimum_supporting_blocks", 1, "exactly 2"),
        )
        for field, value, message in cases:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                profile = json.loads(source.read_text(encoding="utf-8"))
                profile["site_fusion"][field] = value
                profile_path = root / "drifted_v3.json"
                profile_path.write_text(json.dumps(profile), encoding="utf-8")

                with self.assertRaisesRegex(
                    OperationalRunProfileError,
                    message,
                ):
                    write_ootang_operational_run(
                        profile_path=profile_path,
                        output_dir=root / "output",
                    )

    def test_test_period_prediction_change_does_not_change_fit_thresholds(self):
        source_predictions = ROOT / "figures" / "convlstm" / "forecast_predictions.csv"
        source_manifest = (
            ROOT / "figures" / "convlstm" / "forecast_run_manifest.json"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_output = root / "first"
            altered_predictions = root / "altered_predictions.csv"
            altered_manifest = root / "altered_forecast_manifest.json"
            predictions = pd.read_csv(source_predictions)
            test_index = predictions.index[predictions["split"].eq("test")][0]
            predictions.loc[test_index, "actual"] += 10_000.0
            predictions.to_csv(altered_predictions, index=False)
            manifest = json.loads(source_manifest.read_text(encoding="utf-8"))
            manifest["outputs"][
                "figures/convlstm/forecast_predictions.csv"
            ]["sha256"] = hashlib.sha256(
                altered_predictions.read_bytes()
            ).hexdigest()
            altered_manifest.write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )

            first = write_ootang_operational_run(
                output_dir=first_output,
                evidence_dir=root / "first_evidence",
            )
            altered = write_ootang_operational_run(
                predictions_path=altered_predictions,
                forecast_manifest_path=altered_manifest,
                output_dir=root / "altered",
                evidence_dir=root / "altered_evidence",
            )

            self.assertEqual(
                first.thresholds_path.read_bytes(),
                altered.thresholds_path.read_bytes(),
            )

    def test_rejects_prediction_that_does_not_match_forecast_manifest(self):
        source_predictions = (
            ROOT / "figures" / "convlstm" / "forecast_predictions.csv"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            altered_predictions = root / "altered_predictions.csv"
            predictions = pd.read_csv(source_predictions)
            predictions.loc[0, "actual"] += 1.0
            predictions.to_csv(altered_predictions, index=False)

            with self.assertRaisesRegex(
                OperationalRunInputError,
                "does not match",
            ):
                write_ootang_operational_run(
                    predictions_path=altered_predictions,
                    output_dir=root / "output",
                    evidence_dir=root / "evidence",
                )

    def test_rejects_non_object_forecast_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            invalid_manifest = root / "forecast_manifest.json"
            invalid_manifest.write_text("[]\n", encoding="utf-8")

            with self.assertRaisesRegex(
                OperationalRunInputError,
                "must be a JSON object",
            ):
                write_ootang_operational_run(
                    forecast_manifest_path=invalid_manifest,
                    output_dir=root / "output",
                    evidence_dir=root / "evidence",
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
