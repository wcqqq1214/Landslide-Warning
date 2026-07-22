"""Integration tests for the Ootang draft-evidence bundle entry point."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import warning.draft_evidence as draft_evidence  # noqa: E402
from warning.draft_evidence import (  # noqa: E402
    BUNDLE_MANIFEST_FILENAME,
    DraftEvidenceBundleIntegrityError,
    DraftEvidenceBundleProtocolError,
    OOTANG_STATIONS,
    write_draft_warning_evidence_bundle,
)
from warning.protocol import load_protocol, protocol_content_sha256  # noqa: E402


class DraftWarningEvidenceBundleTests(unittest.TestCase):
    def test_rebuilds_all_active_draft_artifacts_with_one_protocol_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "warning_draft"
            artifacts = write_draft_warning_evidence_bundle(output_dir=output_dir)
            first_manifest = artifacts.manifest_path.read_bytes()
            repeated = write_draft_warning_evidence_bundle(output_dir=output_dir)
            repeated_manifest = repeated.manifest_path.read_bytes()
            manifest = json.loads(first_manifest)

            component_names = [item["name"] for item in manifest["components"]]
            component_manifests = [
                json.loads(Path(item["manifest_path"]).read_text(encoding="utf-8"))
                for item in manifest["components"]
            ]

        protocol = load_protocol()
        expected_names = [
            "interval_reference_states",
            "interval_calibration_diagnostics",
            "stable_segment_candidates",
            "delta_v_fit_calibration_diagnostics",
            "velocity_tangent_fit_calibration_diagnostics",
            "mvif_fit_candidates",
            "bai_perron_mvif_initial_slope_candidates",
        ]
        self.assertEqual(artifacts.manifest_path.name, BUNDLE_MANIFEST_FILENAME)
        self.assertEqual(component_names, expected_names)
        self.assertEqual(first_manifest, repeated_manifest)
        self.assertFalse(manifest["formal_warning_output"])
        self.assertFalse(manifest["vajont_used"])
        self.assertEqual(manifest["ootang_stations"], list(OOTANG_STATIONS))
        self.assertEqual(manifest["protocol"]["status"], "draft")
        self.assertEqual(
            manifest["protocol"]["content_sha256"],
            protocol_content_sha256(protocol),
        )
        self.assertNotIn("mvif_initial_slope_candidates", component_names)
        for component in component_manifests:
            self.assertFalse(component["formal_warning_output"])
            self.assertEqual(
                component["protocol"]["content_sha256"],
                manifest["protocol"]["content_sha256"],
            )

    def test_rejects_a_frozen_protocol_before_writing_a_draft_bundle(self):
        frozen = copy.deepcopy(load_protocol())
        frozen["status"] = "frozen"
        frozen["unresolved_items"] = []

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            protocol_path = root / "frozen.json"
            output_dir = root / "warning_draft"
            protocol_path.write_text(
                json.dumps(frozen, ensure_ascii=False), encoding="utf-8"
            )

            with self.assertRaises(DraftEvidenceBundleProtocolError):
                write_draft_warning_evidence_bundle(
                    output_dir=output_dir,
                    protocol_path=protocol_path,
                )

            self.assertFalse(output_dir.exists())

    def test_rejects_non_ootang_station_input_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kinematics_path = root / "other_case.csv"
            output_dir = root / "warning_draft"
            kinematics_path.write_text("station\nVJ1\n", encoding="utf-8")

            with self.assertRaises(DraftEvidenceBundleProtocolError):
                write_draft_warning_evidence_bundle(
                    kinematics_path=kinematics_path,
                    output_dir=output_dir,
                )

            self.assertFalse(output_dir.exists())

    def test_component_failure_leaves_the_previous_bundle_files_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "warning_draft"
            output_dir.mkdir()
            existing_states = output_dir / "interval_reference_states.csv"
            existing_bundle = output_dir / BUNDLE_MANIFEST_FILENAME
            existing_states.write_bytes(b"old interval states\n")
            existing_bundle.write_bytes(b"old bundle manifest\n")

            with patch(
                "warning.draft_evidence.write_calibration_diagnostics",
                side_effect=RuntimeError("simulated component failure"),
            ):
                with self.assertRaisesRegex(RuntimeError, "simulated component"):
                    write_draft_warning_evidence_bundle(output_dir=output_dir)

            self.assertEqual(existing_states.read_bytes(), b"old interval states\n")
            self.assertEqual(existing_bundle.read_bytes(), b"old bundle manifest\n")
            self.assertFalse(
                (output_dir / "interval_calibration_diagnostics.csv").exists()
            )

    def test_promotion_oserror_restores_the_previous_complete_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "warning_draft"
            initial = write_draft_warning_evidence_bundle(output_dir=output_dir)
            manifest = json.loads(initial.manifest_path.read_text(encoding="utf-8"))
            snapshot_paths = [initial.manifest_path]
            for component in manifest["components"]:
                snapshot_paths.extend(
                    [
                        Path(component["output_path"]),
                        Path(component["manifest_path"]),
                    ]
                )
            previous_snapshot = {
                path: path.read_bytes() for path in snapshot_paths
            }
            original_replace = draft_evidence.os.replace
            replace_calls = 0

            def fail_once_during_promotion(source, target):
                nonlocal replace_calls
                replace_calls += 1
                if replace_calls == 10:
                    raise OSError("simulated promotion failure")
                return original_replace(source, target)

            with patch.object(
                draft_evidence.os,
                "replace",
                side_effect=fail_once_during_promotion,
            ):
                with self.assertRaisesRegex(OSError, "simulated promotion"):
                    write_draft_warning_evidence_bundle(output_dir=output_dir)

            self.assertGreater(replace_calls, 10)
            self.assertEqual(
                {path: path.read_bytes() for path in snapshot_paths},
                previous_snapshot,
            )

    def test_rejects_a_component_manifest_with_mismatched_input_provenance(self):
        original_writer = draft_evidence.write_reference_interval_states

        def write_with_wrong_prediction_path(**kwargs):
            artifacts = original_writer(**kwargs)
            manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))
            manifest["source_predictions"]["path"] = "/unrelated/ootang.csv"
            artifacts.manifest_path.write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            return artifacts

        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "warning_draft"
            with patch.object(
                draft_evidence,
                "write_reference_interval_states",
                new=write_with_wrong_prediction_path,
            ):
                with self.assertRaisesRegex(
                    DraftEvidenceBundleIntegrityError,
                    "source_predictions",
                ):
                    write_draft_warning_evidence_bundle(output_dir=output_dir)

            self.assertFalse(output_dir.exists())


if __name__ == "__main__":
    unittest.main()
