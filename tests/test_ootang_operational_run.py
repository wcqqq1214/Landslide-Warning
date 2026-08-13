"""Integration contract for the current non-formal Ootang v4 run."""

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

from warning import draft_evidence  # noqa: E402
from warning.operational_run import (  # noqa: E402
    OOTANG_STATIONS,
    OperationalRunInputError,
    OperationalRunProfileError,
    _load_operational_profile,
    _spatial_block_source_manifest,
    write_ootang_operational_run,
)


class OotangOperationalRunTests(unittest.TestCase):
    def test_pipeline_registers_only_v4_operational_stage(self):
        spec = importlib.util.spec_from_file_location("pipeline_main", ROOT / "main.py")
        if spec is None or spec.loader is None:
            self.fail("cannot load main.py")
        pipeline = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = pipeline
        spec.loader.exec_module(pipeline)

        self.assertEqual(
            [stage.name for stage in pipeline.STAGES if "operational" in stage.name],
            ["ootang-operational-v4"],
        )
        stage = pipeline.STAGE_BY_NAME["ootang-operational-v4"]
        self.assertTrue(stage.enabled_by_default)
        self.assertFalse(stage.formal_warning_output)

    def test_writes_complete_nonformal_v4_timeline(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            artifacts = write_ootang_operational_run(
                output_dir=root / "output",
                evidence_dir=root / "evidence",
            )
            manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))
            station = pd.read_csv(artifacts.station_timeline_path)
            site = pd.read_csv(artifacts.site_timeline_path)
            thresholds = pd.read_csv(artifacts.thresholds_path)

        self.assertEqual(manifest["operational_profile"]["id"], "ootang-operational-spatial-v4")
        self.assertFalse(manifest["formal_warning_output"])
        self.assertFalse(manifest["vajont_used"])
        self.assertEqual(manifest["ootang_stations"], list(OOTANG_STATIONS))
        self.assertEqual(len(station), 4112)
        self.assertEqual(len(site), 514)
        self.assertEqual(len(thresholds), 8)
        self.assertTrue(station["formal_warning_output"].eq(False).all())
        self.assertTrue(site["formal_warning_output"].eq(False).all())
        self.assertEqual(
            set(
                station["evidence_families"]
                .dropna()
                .str.split(";")
                .explode()
            ),
            {"interval", "kinematic_velocity_tangent", "acceleration"},
        )

    def test_v4_manifest_uses_relative_inputs_and_current_sources(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            artifacts = write_ootang_operational_run(
                output_dir=root / "output",
                evidence_dir=root / "evidence",
            )
            manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))

        for source in manifest["implementation_sources"].values():
            self.assertFalse(Path(source["path"]).is_absolute())
            source_path = ROOT / source["path"]
            self.assertEqual(hashlib.sha256(source_path.read_bytes()).hexdigest(), source["sha256"])
        for source in manifest["source_inputs"].values():
            if isinstance(source, dict) and "path" in source:
                self.assertFalse(Path(source["path"]).is_absolute())

    def test_profile_rejects_removed_historical_operational_profile(self):
        with self.assertRaisesRegex(OperationalRunProfileError, "only the v4"):
            _load_operational_profile(ROOT / "config" / "ootang_operational_run.v3.draft.json")

    def test_spatial_source_can_be_declared_without_a_local_pdf(self):
        source = ROOT / "config" / "ootang_operational_run.v4.draft.json"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / source.name
            profile = json.loads(source.read_text(encoding="utf-8"))
            profile["base_draft_protocol"]["path"] = str(
                ROOT / "config" / "ootang_warning_protocol.v1.draft.json"
            )
            profile["acceleration_protocol_extension"]["path"] = str(
                ROOT / "config" / "ootang_warning_protocol.v2.draft.json"
            )
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            loaded = _load_operational_profile(profile_path)
            source_manifest = _spatial_block_source_manifest(loaded)

        self.assertIsNotNone(source_manifest)
        self.assertFalse(source_manifest["source_file_available_at_run"])
        self.assertEqual(
            source_manifest["verification_status"],
            "profile_declared_reviewed_sha256_only",
        )

    def test_rejects_prediction_mismatched_to_forecast_manifest(self):
        predictions_source = ROOT / "figures" / "convlstm" / "forecast_predictions.csv"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            altered = root / "altered_predictions.csv"
            predictions = pd.read_csv(predictions_source)
            predictions.loc[0, "actual"] += 1.0
            predictions.to_csv(altered, index=False)
            with self.assertRaisesRegex(OperationalRunInputError, "does not match"):
                write_ootang_operational_run(
                    predictions_path=altered,
                    output_dir=root / "output",
                    evidence_dir=root / "evidence",
                )

    def test_promotion_failure_restores_existing_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_dir = root / "operational"
            first = write_ootang_operational_run(
                output_dir=output_dir, evidence_dir=root / "evidence"
            )
            paths = (
                first.station_timeline_path,
                first.site_timeline_path,
                first.thresholds_path,
                first.manifest_path,
            )
            snapshot = {path: path.read_bytes() for path in paths}
            original_replace = draft_evidence.os.replace
            writes = 0

            def fail_once(source, target):
                nonlocal writes
                if Path(target).resolve().parent == output_dir.resolve():
                    writes += 1
                    if writes == 2:
                        raise OSError("simulated operational promotion failure")
                return original_replace(source, target)

            with patch.object(draft_evidence.os, "replace", side_effect=fail_once):
                with self.assertRaisesRegex(OSError, "simulated operational promotion"):
                    write_ootang_operational_run(
                        output_dir=output_dir, evidence_dir=root / "evidence"
                    )
            self.assertEqual({path: path.read_bytes() for path in paths}, snapshot)


if __name__ == "__main__":
    unittest.main()
