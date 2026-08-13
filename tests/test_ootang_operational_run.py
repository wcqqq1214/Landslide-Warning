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
    def _profile_variant(self, directory: Path, mutate) -> Path:
        """Write a v4 profile copy with linked repository inputs made explicit."""

        profile = json.loads(
            (ROOT / "config" / "ootang_operational_run.v4.draft.json").read_text(
                encoding="utf-8"
            )
        )
        profile["base_draft_protocol"]["path"] = str(
            ROOT / "config" / "ootang_warning_protocol.v1.draft.json"
        )
        profile["acceleration_protocol_extension"]["path"] = str(
            ROOT / "config" / "ootang_warning_protocol.v2.draft.json"
        )
        mutate(profile)
        path = directory / "ootang_operational_run.v4.variant.json"
        path.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def _assert_repo_relative_paths(self, payload: object) -> None:
        """Every manifest field named path must resolve inside this checkout."""

        paths: list[str] = []

        def collect(value: object, key: str | None = None) -> None:
            if isinstance(value, dict):
                for child_key, child_value in value.items():
                    collect(child_value, child_key)
            elif isinstance(value, list):
                for child_value in value:
                    collect(child_value, key)
            elif key == "path" and isinstance(value, str):
                paths.append(value)

        collect(payload)
        self.assertTrue(paths)
        for path in paths:
            self.assertFalse(Path(path).is_absolute(), path)
            self.assertTrue((ROOT / path).exists(), path)

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
            evidence_manifest = json.loads(
                artifacts.evidence_manifest_path.read_text(encoding="utf-8")
            )
            self._assert_repo_relative_paths(manifest)
            self._assert_repo_relative_paths(evidence_manifest)
            for source in manifest["implementation_sources"].values():
                self.assertFalse(Path(source["path"]).is_absolute())
                source_path = ROOT / source["path"]
                self.assertEqual(
                    hashlib.sha256(source_path.read_bytes()).hexdigest(),
                    source["sha256"],
                )
            for source in manifest["source_inputs"].values():
                if isinstance(source, dict) and "path" in source:
                    self.assertFalse(Path(source["path"]).is_absolute())

            for component in evidence_manifest["components"]:
                sidecar = json.loads(
                    (ROOT / component["manifest_path"]).read_text(encoding="utf-8")
                )
                self._assert_repo_relative_paths(sidecar)
                self.assertFalse(Path(component["manifest_path"]).is_absolute())
                self.assertFalse(Path(component["output_path"]).is_absolute())

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

    def test_profile_rejects_spatial_source_declared_sha_drift(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            profile_path = self._profile_variant(
                Path(directory),
                lambda profile: profile["site_fusion"]["spatial_blocks_source"].update(
                    source_file_sha256="0" * 64
                ),
            )
            with self.assertRaisesRegex(
                OperationalRunProfileError,
                "reviewed paper fingerprint",
            ):
                _load_operational_profile(profile_path)

    def test_profile_rejects_incorrect_local_spatial_source_copy(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            fake_paper = root / "paper.pdf"
            fake_paper.write_bytes(b"not the reviewed paper")
            profile_path = self._profile_variant(
                root,
                lambda profile: profile["site_fusion"]["spatial_blocks_source"].update(
                    source_file=str(fake_paper)
                ),
            )
            with self.assertRaisesRegex(
                OperationalRunProfileError,
                "fingerprint does not match",
            ):
                _load_operational_profile(profile_path)

    def test_profile_rejects_formal_warning_output(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            profile_path = self._profile_variant(
                Path(directory),
                lambda profile: profile.update(formal_warning_output=True),
            )
            with self.assertRaisesRegex(
                OperationalRunProfileError,
                "must explicitly prohibit formal warning output",
            ):
                _load_operational_profile(profile_path)

    def test_profile_rejects_base_protocol_version_or_content_hash_drift(self):
        for field, value, message in (
            (
                "protocol_version",
                "1.3-drift",
                "Base draft protocol protocol_version",
            ),
            (
                "revision_date",
                "2099-01-01",
                "content fingerprint",
            ),
        ):
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory(dir=ROOT) as directory:
                    root = Path(directory)
                    altered_protocol = json.loads(
                        (
                            ROOT / "config" / "ootang_warning_protocol.v1.draft.json"
                        ).read_text(encoding="utf-8")
                    )
                    altered_protocol[field] = value
                    altered_protocol_path = root / "altered_base_protocol.json"
                    altered_protocol_path.write_text(
                        json.dumps(altered_protocol, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    profile_path = self._profile_variant(
                        root,
                        lambda profile, altered_protocol_path=altered_protocol_path: profile[
                            "base_draft_protocol"
                        ].update(path=str(altered_protocol_path)),
                    )
                    with self.assertRaisesRegex(
                        OperationalRunProfileError,
                        message,
                    ):
                        _load_operational_profile(profile_path)

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

    def test_test_split_prediction_changes_do_not_change_fit_only_thresholds(self):
        predictions_source = ROOT / "figures" / "convlstm" / "forecast_predictions.csv"
        manifest_source = ROOT / "figures" / "convlstm" / "forecast_run_manifest.json"
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            altered_predictions = root / "altered_predictions.csv"
            predictions = pd.read_csv(predictions_source)
            test_index = predictions.index[predictions["split"].eq("test")][0]
            for column in ("actual", "p10", "p50", "p90"):
                predictions.loc[test_index, column] += 0.25
            predictions.to_csv(altered_predictions, index=False)

            forecast_manifest = json.loads(
                manifest_source.read_text(encoding="utf-8")
            )
            forecast_manifest["outputs"]["figures/convlstm/forecast_predictions.csv"][
                "sha256"
            ] = hashlib.sha256(altered_predictions.read_bytes()).hexdigest()
            altered_manifest = root / "altered_forecast_run_manifest.json"
            altered_manifest.write_text(
                json.dumps(forecast_manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            baseline = write_ootang_operational_run(
                output_dir=root / "baseline_output",
                evidence_dir=root / "baseline_evidence",
            )
            altered = write_ootang_operational_run(
                predictions_path=altered_predictions,
                forecast_manifest_path=altered_manifest,
                output_dir=root / "altered_output",
                evidence_dir=root / "altered_evidence",
            )
            baseline_thresholds = pd.read_csv(baseline.thresholds_path)
            altered_thresholds = pd.read_csv(altered.thresholds_path)

        pd.testing.assert_frame_equal(
            baseline_thresholds,
            altered_thresholds,
            check_dtype=False,
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
