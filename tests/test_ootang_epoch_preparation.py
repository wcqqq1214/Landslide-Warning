from datetime import datetime, timezone
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_epoch_preparation as preparation
from monitoring import ootang_epoch_registry as registry


FIXED_NOW = datetime(2026, 8, 26, 15, 0, tzinfo=timezone.utc)
EXPECTED_CLOSURE = [
    "convlstm",
    "convlstm.block_bootstrap",
    "convlstm.grid_interp",
    "convlstm.model",
    "convlstm.ootang_production_bundle",
    "monitoring",
    "monitoring.calibration_challengers",
    "monitoring.ootang_calibration_shadow_ledger",
    "monitoring.ootang_issue_producer",
    "monitoring.ootang_issue_replay",
    "monitoring.ootang_live_ledger",
    "monitoring.ootang_live_source",
    "monitoring.ootang_outcome_materializer",
    "monitoring.ootang_prequential_calibration_shadow",
    "monitoring.ootang_prequential_cycle",
    "monitoring.ootang_prequential_cycle_v2",
    "monitoring.ootang_prequential_cycle_v3",
    "monitoring.ootang_prequential_live",
    "monitoring.ootang_trusted_time_shadow",
    "monitoring.ootang_trusted_time_shadow_core",
    "monitoring.ootang_verified_live",
    "monitoring.prequential_core",
]


class EpochPreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = preparation.load_preparation_profile()
        cls.r1_profile = registry.load_registry_profile()

    @staticmethod
    def _record(path: Path, **extra: object) -> dict[str, object]:
        raw = path.read_bytes()
        return {
            "path": str(path.resolve()),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
            **extra,
        }

    @staticmethod
    def _feed_bytes(
        *,
        revision_id: str = "source-revision-one",
        exported_at_utc: str = "2026-08-26T14:00:00Z",
        value: float = 1.0,
    ) -> bytes:
        stations = ("ATU1", "ATU2", "ATU3", "ATU4", "ATU5", "MJ1", "MJ3", "MJ9")
        payload = {
            "schema_version": "ootang_daily_finalized_feed_v1",
            "outcome_source_id": "machine-feed-v1",
            "exported_at_utc": exported_at_utc,
            "records": [
                {
                    "schema_version": "ootang_daily_finalized_record_v1",
                    "date": "2020-07-01",
                    "revision_id": revision_id,
                    "observed_at_utc": "2020-07-01T04:00:00Z",
                    "available_at_utc": "2020-07-01T05:00:00Z",
                    "finalized_at_utc": "2020-07-01T06:00:00Z",
                    "finalized": True,
                    "rainfall_mm": value,
                    "reservoir_water_level_m": 150.0 + value,
                    "displacement_mm": {
                        station: 1000.0 + value + index
                        for index, station in enumerate(stations)
                    },
                }
            ],
        }
        return (json.dumps(payload, separators=(",", ":")) + "\n").encode()

    def _fake_prebuilder(
        self,
        profile,
        _paths,
        live_root: Path,
        _now,
        feed_copy: Path,
    ) -> registry.CandidateBuild:
        feed = json.loads(feed_copy.read_text(encoding="utf-8"))
        artifacts = live_root / "objects" / "sha256"
        artifacts.mkdir(parents=True, exist_ok=True)
        source_rows = []
        for role, name in (
            ("current_source_pointer", "source-current.json"),
            ("activation_source_manifest", "activation.json"),
            ("semantic_source_manifest", "semantic.json"),
            ("canonical_source_dataset", "source.json"),
        ):
            path = artifacts / name
            path.write_bytes(f"{role}\n".encode())
            source_rows.append(self._record(path, role=role))
        feed_snapshot = artifacts / "daily-feed.json"
        feed_snapshot.write_bytes(feed_copy.read_bytes())
        source_rows.append(self._record(feed_snapshot, role="daily_feed_snapshot"))
        model = artifacts / "model.json"
        training = artifacts / "training.json"
        model.write_bytes(b"model\n")
        training.write_bytes(b"training\n")
        model_record = self._record(model)
        checkpoints = []
        for seed in range(5):
            checkpoint = artifacts / f"seed-{seed}.pt"
            checkpoint.write_bytes(f"checkpoint-{seed}\n".encode())
            checkpoints.append(self._record(checkpoint, seed=seed))
        live_binding = profile["upstream_profiles"]["live"]
        live_profile = json.loads(
            (Path(profile["_project_root"]) / live_binding["path"]).read_text(
                encoding="utf-8"
            )
        )
        watermark = feed["records"][-1]["date"]
        live_epoch_id = registry._live_canonical_sha256(  # noqa: SLF001
            {
                "profile_id": live_profile["profile_id"],
                "profile_sha256": live_binding["expected_sha256"],
                "source_snapshot_sha256": next(
                    row["sha256"]
                    for row in source_rows
                    if row["role"] == "activation_source_manifest"
                ),
                "model_manifest_sha256": model_record["sha256"],
                "watermark": watermark,
                "implementation_sha256": "c" * 64,
                "environment_sha256": "d" * 64,
            }
        )
        return registry.CandidateBuild(
            live_epoch_id=live_epoch_id,
            watermark=watermark,
            outcome_source_id=feed["outcome_source_id"],
            source_artifacts=tuple(source_rows),
            model_manifest=model_record,
            training_manifest=self._record(training),
            checkpoints=tuple(checkpoints),
            algorithm_profile_sha256="b" * 64,
            implementation_sha256="c" * 64,
            environment_sha256="d" * 64,
        )

    def _seed_registry(
        self, temporary: str, *, feed_bytes: bytes | None = None
    ) -> tuple[registry.RegistryPaths, Path]:
        root = Path(temporary)
        feed = root / "candidate-feed.json"
        feed.write_bytes(feed_bytes or self._feed_bytes())
        runtime = root / "registry"
        registry._poll_epoch_registry(  # noqa: SLF001
            runtime_root=runtime,
            _source_feed_path=feed,
            clock=lambda: FIXED_NOW,
            _prebuilder=self._fake_prebuilder,
        )
        return (
            registry.registry_paths(
                self.r1_profile,
                runtime_root=runtime,
                source_feed_path=feed,
            ),
            feed,
        )

    @staticmethod
    def _fake_smoke(profile, _paths, manifest, _tree):
        def common(domain_name):
            expected = profile["root_environment"][domain_name]
            return {
                "python_version": profile["root_environment"][
                    "required_python_version"
                ],
                "isolated_mode": True,
                "dont_write_bytecode": True,
                "distribution_count": expected["distribution_count"],
                "distribution_inventory_sha256": expected[
                    "distribution_inventory_sha256"
                ],
                "python_executable_sha256": expected["python_executable_sha256"],
                "python_soabi": expected["python_soabi"],
                "platform": expected["platform"],
            }

        root_domain = {
            **common("root_smoke_domain"),
            "schema_version": "ootang_epoch_root_domain_smoke_v1",
            "tree_sha256": manifest["tree_sha256"],
            "root_modules_imported": manifest["root_modules"],
            "compiled_python_file_count": 22,
            "candidate_live_epoch_id": manifest["candidate_live_epoch_id"],
            "candidate_prerequisites_reloaded": True,
            "five_seed_numerical_replay_passed": True,
            "five_seed_max_abs_difference_mm": 0.0,
        }
        trusted_domain = {
            **common("trusted_time_smoke_domain"),
            "schema_version": "ootang_epoch_trusted_time_domain_smoke_v1",
            "trusted_time_core_imported": True,
        }
        return {
            "schema_version": "ootang_epoch_isolated_smoke_result_v2",
            "python_version": profile["root_environment"]["required_python_version"],
            "uv_version": profile["root_environment"]["required_uv_version"],
            "uv_executable_sha256": profile["root_environment"]["required_uv_sha256"],
            "isolated_mode": True,
            "dont_write_bytecode": True,
            "tree_sha256": manifest["tree_sha256"],
            "root_modules_imported": manifest["root_modules"],
            "compiled_python_file_count": 22,
            "candidate_live_epoch_id": manifest["candidate_live_epoch_id"],
            "candidate_prerequisites_reloaded": True,
            "five_seed_numerical_replay_passed": True,
            "five_seed_max_abs_difference_mm": 0.0,
            "root_domain": root_domain,
            "trusted_time_domain": trusted_domain,
        }

    def _prepare(self, r1_paths: registry.RegistryPaths, *, smoke=None) -> Path:
        return preparation._poll_epoch_preparation(  # noqa: SLF001
            runtime_root=r1_paths.root,
            clock=lambda: FIXED_NOW,
            _smoke_runner=smoke or self._fake_smoke,
        )

    def _replay(self, r1_paths: registry.RegistryPaths):
        paths = preparation.preparation_paths(self.profile, runtime_root=r1_paths.root)
        r1_events = registry.replay_registry(self.r1_profile, r1_paths)
        return paths, preparation.replay_preparations(
            self.profile, paths, self.r1_profile, r1_paths, r1_events
        )

    def _verified_manifest(
        self,
        r1_paths: registry.RegistryPaths,
        paths: preparation.PreparationPaths,
        event,
    ):
        r1_events = registry.replay_registry(self.r1_profile, r1_paths)
        r1_event = next(
            row
            for row in r1_events
            if row["sequence_id"] == event["registry_event_sequence_id"]
        )
        receipt = registry._verify_receipt(  # noqa: SLF001
            self.r1_profile, r1_paths, r1_event["candidate_receipt"]
        )
        r1_manifest = registry._verify_capsule(  # noqa: SLF001
            self.r1_profile, r1_paths, receipt["capsule"]
        )
        return preparation._verify_executable_capsule(  # noqa: SLF001
            self.profile,
            paths,
            event["executable_capsule"],
            r1_paths=r1_paths,
            r1_manifest=r1_manifest,
            expected_candidate=receipt,
            expected_registry_event=r1_event,
        )

    def test_profile_is_machine_only_same_origin_r2a(self):
        self.assertEqual(
            self.profile["engineering_capabilities"], preparation.EXPECTED_CAPABILITIES
        )
        self.assertFalse(
            self.profile["engineering_capabilities"]["portable_offline_runtime"]
        )
        self.assertFalse(
            self.profile["engineering_capabilities"][
                "automatic_epoch_rotation_implemented"
            ]
        )
        self.assertTrue(
            self.profile["executable_capsule"]["canonical_project_root_required"]
        )
        self.assertTrue(
            self.profile["executable_capsule"]["canonical_slot_root_required"]
        )
        self.assertFalse(self.profile["executable_capsule"]["relocatable"])
        uv_path = Path(self.profile["root_environment"]["required_uv_path"])
        self.assertTrue(uv_path.is_absolute())
        self.assertEqual(
            hashlib.sha256(uv_path.read_bytes()).hexdigest(),
            self.profile["root_environment"]["required_uv_sha256"],
        )
        self.assertEqual(
            self.profile["root_environment"]["root_smoke_domain"]["distribution_count"],
            43,
        )
        self.assertEqual(
            self.profile["root_environment"]["trusted_time_smoke_domain"][
                "distribution_count"
            ],
            5,
        )
        for forbidden in ("date", "freeze", "approve", "force", "backdate"):
            self.assertNotIn(
                forbidden,
                preparation._parse_args([]).__dict__,  # noqa: SLF001
            )

    def test_profile_path_rejects_file_and_parent_symlinks(self):
        for target in ("file", "parent"):
            with (
                self.subTest(target=target),
                tempfile.TemporaryDirectory() as outside_temporary,
                tempfile.TemporaryDirectory(dir=ROOT) as inside_temporary,
            ):
                outside = Path(outside_temporary)
                inside = Path(inside_temporary)
                outside_profile = outside / "profile.json"
                outside_profile.write_bytes(
                    preparation.DEFAULT_CONFIG_PATH.read_bytes()
                )
                if target == "file":
                    candidate = inside / "profile.json"
                    candidate.symlink_to(outside_profile)
                else:
                    linked_parent = inside / "linked"
                    linked_parent.symlink_to(outside, target_is_directory=True)
                    candidate = linked_parent / "profile.json"
                with mock.patch.object(preparation, "DEFAULT_CONFIG_PATH", candidate):
                    with self.assertRaises(preparation.EpochPreparationConfigError):
                        preparation.load_preparation_profile(candidate)

    def test_missing_r1_candidate_is_machine_waiting(self):
        with tempfile.TemporaryDirectory() as temporary:
            status_path = preparation._poll_epoch_preparation(  # noqa: SLF001
                runtime_root=Path(temporary) / "registry",
                clock=lambda: FIXED_NOW,
                _smoke_runner=mock.Mock(
                    side_effect=AssertionError("waiting must not run smoke")
                ),
            )
            status = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(
                status["preparation_status"], "waiting_for_immutable_candidate"
            )
            self.assertEqual(status["preparation_event_count"], 0)
            self.assertFalse(status["same_origin_executable_preflight_verified"])
            for claim in preparation.FALSE_CLAIM_KEYS:
                self.assertFalse(status[claim])

    def test_exact_22_module_closure_and_two_reviewed_augmentations(self):
        with tempfile.TemporaryDirectory() as temporary:
            r1_paths, _ = self._seed_registry(temporary)
            self._prepare(r1_paths)
            paths, events = self._replay(r1_paths)
            manifest = self._verified_manifest(r1_paths, paths, events[0])
            self.assertEqual(manifest["closure_modules"], EXPECTED_CLOSURE)
            self.assertEqual(len(manifest["closure_modules"]), 22)
            python_rows = {
                row["logical_path"]: row
                for row in manifest["artifacts"]
                if row["logical_path"].endswith(".py")
            }
            self.assertEqual(len(python_rows), 22)
            self.assertEqual(
                {
                    path
                    for path, row in python_rows.items()
                    if row["source"] == "r2_closure_capture"
                },
                {"code/convlstm/__init__.py", "code/monitoring/__init__.py"},
            )
            self.assertNotIn("code/monitoring/ootang_epoch_registry.py", python_rows)
            self.assertNotIn(
                "code/monitoring/ootang_prequential_monitor.py", python_rows
            )
            self.assertNotIn("code/warning/draft_evidence.py", python_rows)
            self.assertEqual(
                python_rows["code/monitoring/ootang_trusted_time_shadow_core.py"][
                    "role"
                ],
                "trusted_time_isolated_entrypoint",
            )

    def test_profile_and_implementation_bytes_are_content_addressed(self):
        with tempfile.TemporaryDirectory() as temporary:
            r1_paths, _ = self._seed_registry(temporary)
            self._prepare(r1_paths)
            paths, events = self._replay(r1_paths)
            event = events[0]
            manifest = self._verified_manifest(r1_paths, paths, event)
            smoke = json.loads(
                (paths.root / event["smoke_receipt"]["path"]).read_text(
                    encoding="utf-8"
                )
            )
            for key, source_path, sha_key in (
                (
                    "preparation_profile",
                    Path(self.profile["_profile_path"]),
                    "preparation_profile_sha256",
                ),
                (
                    "preparation_implementation",
                    ROOT / preparation.IMPLEMENTATION_LOGICAL_PATH,
                    "preparation_implementation_sha256",
                ),
            ):
                reference = manifest[key]
                captured = paths.root / reference["path"]
                self.assertEqual(captured.read_bytes(), source_path.read_bytes())
                self.assertEqual(reference["sha256"], manifest[sha_key])
                self.assertEqual(event[key], reference)
                self.assertEqual(smoke[key], reference)

    def test_preparation_provenance_object_tampering_fails_closed(self):
        for key in ("preparation_profile", "preparation_implementation"):
            with (
                self.subTest(key=key),
                tempfile.TemporaryDirectory() as temporary,
            ):
                r1_paths, _ = self._seed_registry(temporary)
                self._prepare(r1_paths)
                paths, events = self._replay(r1_paths)
                manifest = self._verified_manifest(r1_paths, paths, events[0])
                (paths.root / manifest[key]["path"]).write_bytes(b"tampered\n")
                with self.assertRaises(preparation.EpochPreparationIntegrityError):
                    self._prepare(r1_paths)

    def test_candidate_preparation_is_content_addressed_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            r1_paths, _ = self._seed_registry(temporary)
            status_path = self._prepare(r1_paths)
            status = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(
                status["preparation_status"], "executable_candidate_prepared"
            )
            self.assertEqual(status["preparation_event_count"], 1)
            self.assertTrue(status["five_seed_numerical_replay_verified"])
            self.assertTrue(status["trusted_time_isolated_domain_smoke_verified"])
            self.assertFalse(status["portable_offline_runtime"])
            paths, events = self._replay(r1_paths)
            first = events[0].copy()
            self.assertEqual(first["event_type"], "candidate_prepared")
            self.assertEqual(first["previous_entry_sha256"], preparation.ZERO_HASH)
            self.assertTrue(first["materialized_executable_tree"])
            self.assertFalse(first["automatic_epoch_rotation_implemented"])

            current_smoke = mock.Mock(side_effect=self._fake_smoke)
            rerun = self._prepare(r1_paths, smoke=current_smoke)
            current_smoke.assert_called_once()
            self.assertEqual(
                json.loads(rerun.read_text(encoding="utf-8"))[
                    "preparation_event_count"
                ],
                1,
            )
            self.assertEqual(self._replay(r1_paths)[1][0], first)
            self.assertEqual(len(list(paths.capsules.glob("*.json"))), 1)
            self.assertEqual(len(list(paths.smoke_receipts.glob("*.json"))), 1)

    def test_prepared_candidate_rejects_later_namespace_pollution(self):
        with tempfile.TemporaryDirectory() as temporary:
            r1_paths, _ = self._seed_registry(temporary)
            self._prepare(r1_paths)
            paths, events = self._replay(r1_paths)
            manifest = self._verified_manifest(r1_paths, paths, events[0])
            (Path(manifest["slot_live_root"]) / "ledger.sqlite3").write_bytes(
                b"unauthorized post-preparation namespace\n"
            )

            with self.assertRaises(preparation.EpochPreparationIntegrityError):
                self._prepare(r1_paths)

    def test_prepared_candidate_rejects_later_artifact_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            r1_paths, _ = self._seed_registry(temporary)
            self._prepare(r1_paths)
            paths, events = self._replay(r1_paths)
            manifest = self._verified_manifest(r1_paths, paths, events[0])
            del manifest
            r1_event = registry.replay_registry(self.r1_profile, r1_paths)[0]
            receipt = registry._verify_receipt(  # noqa: SLF001
                self.r1_profile, r1_paths, r1_event["candidate_receipt"]
            )
            checkpoint = receipt["candidate_build"]["checkpoints"][0]
            Path(checkpoint["path"]).write_bytes(b"tampered after preparation\n")

            with self.assertRaises(preparation.EpochPreparationIntegrityError):
                self._prepare(r1_paths)

    def test_materialized_tree_is_exact_and_nonrelocatable(self):
        with tempfile.TemporaryDirectory() as temporary:
            r1_paths, _ = self._seed_registry(temporary)
            self._prepare(r1_paths)
            paths, events = self._replay(r1_paths)
            manifest = self._verified_manifest(r1_paths, paths, events[0])
            tree = preparation._tree_root(paths, manifest)  # noqa: SLF001
            preparation._verify_materialized_tree(paths, manifest, tree)  # noqa: SLF001
            self.assertTrue(manifest["materialized_executable_tree"])
            self.assertTrue(manifest["canonical_project_root_required"])
            self.assertTrue(manifest["canonical_slot_root_required"])
            self.assertFalse(manifest["relocatable"])
            self.assertFalse(manifest["portable_offline_runtime"])
            self.assertFalse((tree / ".venv").exists())

    def test_orphan_smoke_receipt_recovers_only_after_current_reattestation(self):
        with tempfile.TemporaryDirectory() as temporary:
            r1_paths, _ = self._seed_registry(temporary)
            with mock.patch.object(
                preparation,
                "_append_preparation_event",
                side_effect=preparation.EpochPreparationIntegrityError(
                    "simulated crash"
                ),
            ):
                with self.assertRaises(preparation.EpochPreparationIntegrityError):
                    self._prepare(r1_paths)
            paths = preparation.preparation_paths(
                self.profile, runtime_root=r1_paths.root
            )
            self.assertEqual(len(list(paths.smoke_receipts.glob("*.json"))), 1)
            self.assertFalse(paths.events.exists())

            current_smoke = mock.Mock(side_effect=self._fake_smoke)
            self._prepare(r1_paths, smoke=current_smoke)
            current_smoke.assert_called_once()
            self.assertEqual(len(self._replay(r1_paths)[1]), 1)

    def test_orphan_smoke_receipt_blocks_environment_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            r1_paths, _ = self._seed_registry(temporary)
            with mock.patch.object(
                preparation,
                "_append_preparation_event",
                side_effect=preparation.EpochPreparationIntegrityError(
                    "simulated crash"
                ),
            ):
                with self.assertRaises(preparation.EpochPreparationIntegrityError):
                    self._prepare(r1_paths)

            def drifted(profile, paths, manifest, tree):
                result = self._fake_smoke(profile, paths, manifest, tree)
                result["root_domain"]["distribution_inventory_sha256"] = "f" * 64
                return result

            with self.assertRaises(preparation.EpochPreparationIntegrityError):
                self._prepare(r1_paths, smoke=drifted)
            paths = preparation.preparation_paths(
                self.profile, runtime_root=r1_paths.root
            )
            self.assertFalse(paths.events.exists())

    def test_candidate_is_reverified_after_smoke_before_event(self):
        with tempfile.TemporaryDirectory() as temporary:
            r1_paths, _ = self._seed_registry(temporary)

            def polluted(profile, paths, manifest, tree):
                result = self._fake_smoke(profile, paths, manifest, tree)
                (Path(manifest["slot_live_root"]) / "ledger.sqlite3").write_bytes(
                    b"unauthorized pre-activation namespace\n"
                )
                return result

            with self.assertRaises(preparation.EpochPreparationIntegrityError):
                self._prepare(r1_paths, smoke=polluted)
            paths = preparation.preparation_paths(
                self.profile, runtime_root=r1_paths.root
            )
            self.assertFalse(paths.events.exists())

    def test_capsule_and_tree_are_reverified_immediately_before_event(self):
        for target in ("capsule", "tree"):
            with (
                self.subTest(target=target),
                tempfile.TemporaryDirectory() as temporary,
            ):
                r1_paths, _ = self._seed_registry(temporary)

                def tampering_smoke(profile, paths, manifest, tree):
                    result = self._fake_smoke(profile, paths, manifest, tree)
                    if target == "capsule":
                        raw = registry._canonical_bytes(manifest)  # noqa: SLF001
                        capsule = (
                            paths.capsules / f"{hashlib.sha256(raw).hexdigest()}.json"
                        )
                        capsule.write_bytes(b"tampered during smoke\n")
                    else:
                        logical = manifest["artifacts"][0]["logical_path"]
                        (tree / logical).write_bytes(b"tampered during smoke\n")
                    return result

                with self.assertRaises(preparation.EpochPreparationIntegrityError):
                    self._prepare(r1_paths, smoke=tampering_smoke)
                paths = preparation.preparation_paths(
                    self.profile, runtime_root=r1_paths.root
                )
                self.assertFalse(paths.events.exists())

    def test_capsule_object_tree_smoke_and_event_tampering_fail_closed(self):
        for target in ("capsule", "object", "tree", "smoke", "event"):
            with (
                self.subTest(target=target),
                tempfile.TemporaryDirectory() as temporary,
            ):
                r1_paths, _ = self._seed_registry(temporary)
                self._prepare(r1_paths)
                paths, events = self._replay(r1_paths)
                event_path = next(paths.events.iterdir())
                event = events[0]
                manifest_path = paths.root / event["executable_capsule"]["path"]
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if target == "capsule":
                    manifest_path.write_bytes(b"{}\n")
                elif target == "object":
                    row = manifest["artifacts"][0]
                    (paths.root / row["object_path"]).write_bytes(b"tampered\n")
                elif target == "tree":
                    tree = preparation._tree_root(paths, manifest)  # noqa: SLF001
                    (tree / manifest["artifacts"][0]["logical_path"]).write_bytes(
                        b"tampered\n"
                    )
                elif target == "smoke":
                    (paths.root / event["smoke_receipt"]["path"]).write_bytes(b"{}\n")
                else:
                    payload = json.loads(event_path.read_text(encoding="utf-8"))
                    payload["candidate_id"] = "f" * 64
                    event_path.write_bytes(
                        registry._canonical_bytes(payload)  # noqa: SLF001
                    )
                with self.assertRaises(
                    (preparation.EpochPreparationError, registry.EpochRegistryError)
                ):
                    self._prepare(r1_paths)

    def test_self_consistent_capsule_cannot_drop_a_root_module(self):
        with tempfile.TemporaryDirectory() as temporary:
            r1_paths, _ = self._seed_registry(temporary)
            self._prepare(r1_paths)
            paths, events = self._replay(r1_paths)
            event = dict(events[0])
            manifest_path = paths.root / event["executable_capsule"]["path"]
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            removed_module = "monitoring.ootang_prequential_cycle_v3"
            removed_logical = "code/monitoring/ootang_prequential_cycle_v3.py"
            manifest["closure_modules"].remove(removed_module)
            manifest["artifacts"] = [
                row
                for row in manifest["artifacts"]
                if row["logical_path"] != removed_logical
            ]
            manifest["artifact_count"] = len(manifest["artifacts"])
            manifest["tree_sha256"] = hashlib.sha256(
                registry._canonical_bytes(  # noqa: SLF001
                    preparation._tree_payload(manifest["artifacts"])  # noqa: SLF001
                )
            ).hexdigest()
            capsule_raw = registry._canonical_bytes(manifest)  # noqa: SLF001
            capsule_snapshot = registry._publish_once(  # noqa: SLF001
                paths.capsules / f"{hashlib.sha256(capsule_raw).hexdigest()}.json",
                capsule_raw,
                root=paths.root,
                name="hostile self-consistent capsule",
            )
            smoke_snapshot = preparation._publish_smoke_receipt(  # noqa: SLF001
                self.profile,
                paths,
                capsule_snapshot,
                manifest,
                self._fake_smoke(self.profile, paths, manifest, paths.root),
            )
            event["executable_capsule"] = preparation._capsule_reference(  # noqa: SLF001
                paths, capsule_snapshot
            )
            event["smoke_receipt"] = preparation._capsule_reference(  # noqa: SLF001
                paths, smoke_snapshot
            )
            event["entry_sha256"] = hashlib.sha256(
                registry._canonical_bytes(  # noqa: SLF001
                    preparation._event_unsigned(event)  # noqa: SLF001
                )
            ).hexdigest()
            next(paths.events.iterdir()).write_bytes(
                registry._canonical_bytes(event)  # noqa: SLF001
            )
            paths.head.unlink()

            with self.assertRaises(preparation.EpochPreparationIntegrityError):
                self._prepare(r1_paths)

    def test_tree_extra_file_and_head_rollback_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            r1_paths, _ = self._seed_registry(temporary)
            self._prepare(r1_paths)
            paths, events = self._replay(r1_paths)
            manifest = self._verified_manifest(r1_paths, paths, events[0])
            (preparation._tree_root(paths, manifest) / "undeclared").write_bytes(  # noqa: SLF001
                b"extra\n"
            )
            with self.assertRaises(preparation.EpochPreparationIntegrityError):
                self._prepare(r1_paths)

        with tempfile.TemporaryDirectory() as temporary:
            r1_paths, _ = self._seed_registry(temporary)
            self._prepare(r1_paths)
            paths, events = self._replay(r1_paths)
            manifest = self._verified_manifest(r1_paths, paths, events[0])
            (
                preparation._tree_root(paths, manifest)  # noqa: SLF001
                / "undeclared-empty"
            ).mkdir()
            with self.assertRaises(preparation.EpochPreparationIntegrityError):
                self._prepare(r1_paths)

        with tempfile.TemporaryDirectory() as temporary:
            r1_paths, _ = self._seed_registry(temporary)
            self._prepare(r1_paths)
            paths, _ = self._replay(r1_paths)
            head = json.loads(paths.head.read_text(encoding="utf-8"))
            head["sequence_id"] = 2
            paths.head.write_bytes(registry._canonical_bytes(head))  # noqa: SLF001
            with self.assertRaises(preparation.EpochPreparationIntegrityError):
                self._prepare(r1_paths)

    def test_materialization_checks_object_before_publish(self):
        with tempfile.TemporaryDirectory() as temporary:
            r1_paths, _ = self._seed_registry(temporary)
            self._prepare(r1_paths)
            paths, events = self._replay(r1_paths)
            manifest = dict(self._verified_manifest(r1_paths, paths, events[0]))
            manifest["tree_sha256"] = "f" * 64
            real_read = registry._read_regular  # noqa: SLF001

            def changed_object(path, *, name, **kwargs):
                if name.startswith("object for "):
                    return registry.ArtifactSnapshot(
                        path=path,
                        raw=b"changed-before-materialization\n",
                        sha256=hashlib.sha256(
                            b"changed-before-materialization\n"
                        ).hexdigest(),
                        size_bytes=len(b"changed-before-materialization\n"),
                    )
                return real_read(path, name=name, **kwargs)

            with mock.patch.object(
                preparation.registry, "_read_regular", side_effect=changed_object
            ):
                with self.assertRaises(preparation.EpochPreparationIntegrityError):
                    preparation._materialize_tree(paths, manifest)  # noqa: SLF001
            hostile_tree = preparation._tree_root(  # noqa: SLF001
                paths, manifest
            )
            self.assertEqual(list(hostile_tree.rglob("*")), [])

    def test_boolean_sequence_and_float_capsule_size_fail_closed(self):
        for target in ("sequence", "capsule_size"):
            with (
                self.subTest(target=target),
                tempfile.TemporaryDirectory() as temporary,
            ):
                r1_paths, _ = self._seed_registry(temporary)
                self._prepare(r1_paths)
                paths, _ = self._replay(r1_paths)
                event_path = next(paths.events.iterdir())
                event = json.loads(event_path.read_text(encoding="utf-8"))
                if target == "sequence":
                    event["sequence_id"] = True
                else:
                    event["executable_capsule"]["size_bytes"] = float(
                        event["executable_capsule"]["size_bytes"]
                    )
                event["entry_sha256"] = hashlib.sha256(
                    registry._canonical_bytes(  # noqa: SLF001
                        preparation._event_unsigned(event)  # noqa: SLF001
                    )
                ).hexdigest()
                event_path.write_bytes(
                    registry._canonical_bytes(event)  # noqa: SLF001
                )
                with self.assertRaises(preparation.EpochPreparationIntegrityError):
                    self._prepare(r1_paths)

    def test_deleted_caches_are_rebuilt_with_false_claims(self):
        with tempfile.TemporaryDirectory() as temporary:
            r1_paths, _ = self._seed_registry(temporary)
            self._prepare(r1_paths)
            paths, _ = self._replay(r1_paths)
            paths.head.unlink()
            hostile = json.loads(paths.status.read_text(encoding="utf-8"))
            hostile["automatic_epoch_rotation_implemented"] = True
            hostile["formal_warning_output"] = True
            paths.status.write_bytes(registry._canonical_bytes(hostile))  # noqa: SLF001
            self._prepare(r1_paths)
            repaired = json.loads(paths.status.read_text(encoding="utf-8"))
            self.assertTrue(paths.head.is_file())
            self.assertFalse(repaired["automatic_epoch_rotation_implemented"])
            self.assertFalse(repaired["formal_warning_output"])

    def test_historical_preparation_survives_coordinator_upgrade(self):
        with tempfile.TemporaryDirectory() as temporary:
            r1_paths, _ = self._seed_registry(temporary)
            self._prepare(r1_paths)
            paths, _ = self._replay(r1_paths)
            future_profile = {**self.profile, "_implementation_sha256": "f" * 64}
            r1_events = registry.replay_registry(self.r1_profile, r1_paths)
            replayed = preparation.replay_preparations(
                future_profile,
                paths,
                self.r1_profile,
                r1_paths,
                r1_events,
            )
            self.assertEqual(len(replayed), 1)
            self.assertNotEqual(
                replayed[0]["preparation_implementation_sha256"],
                future_profile["_implementation_sha256"],
            )

    def test_coordinator_upgrade_machine_revalidates_same_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            r1_paths, _ = self._seed_registry(temporary)
            self._prepare(r1_paths)
            paths, events = self._replay(r1_paths)
            current_event = events[0]
            event_path = next(paths.events.iterdir())
            event = dict(current_event)
            manifest = json.loads(
                (paths.root / event["executable_capsule"]["path"]).read_text(
                    encoding="utf-8"
                )
            )
            historical_raw = b"historical preparation implementation\n"
            historical_sha256 = hashlib.sha256(historical_raw).hexdigest()
            historical_object = registry._publish_once(  # noqa: SLF001
                paths.objects / historical_sha256,
                historical_raw,
                root=paths.root,
                name="historical preparation implementation",
            )
            historical_reference = preparation._capsule_reference(  # noqa: SLF001
                paths, historical_object
            )
            manifest["preparation_implementation_sha256"] = historical_sha256
            manifest["preparation_implementation"] = historical_reference
            capsule_raw = registry._canonical_bytes(manifest)  # noqa: SLF001
            historical_capsule = registry._publish_once(  # noqa: SLF001
                paths.capsules / f"{hashlib.sha256(capsule_raw).hexdigest()}.json",
                capsule_raw,
                root=paths.root,
                name="historical executable capsule",
            )
            historical_smoke = preparation._publish_smoke_receipt(  # noqa: SLF001
                self.profile,
                paths,
                historical_capsule,
                manifest,
                self._fake_smoke(self.profile, paths, manifest, paths.root),
            )
            event["preparation_implementation_sha256"] = historical_sha256
            event["preparation_implementation"] = historical_reference
            event["executable_capsule"] = preparation._capsule_reference(  # noqa: SLF001
                paths, historical_capsule
            )
            event["smoke_receipt"] = preparation._capsule_reference(  # noqa: SLF001
                paths, historical_smoke
            )
            event["entry_sha256"] = hashlib.sha256(
                registry._canonical_bytes(  # noqa: SLF001
                    preparation._event_unsigned(event)  # noqa: SLF001
                )
            ).hexdigest()
            event_path.write_bytes(registry._canonical_bytes(event))  # noqa: SLF001
            (paths.root / current_event["smoke_receipt"]["path"]).unlink()
            paths.head.unlink()

            smoke = mock.Mock(side_effect=self._fake_smoke)
            self._prepare(r1_paths, smoke=smoke)
            smoke.assert_called_once()
            _, revalidated = self._replay(r1_paths)
            self.assertEqual(
                [row["event_type"] for row in revalidated],
                ["candidate_prepared", "candidate_revalidated"],
            )
            self.assertEqual(
                [row["registry_event_sequence_id"] for row in revalidated], [1, 1]
            )
            self.assertEqual(
                revalidated[-1]["preparation_implementation_sha256"],
                self.profile["_implementation_sha256"],
            )

    def test_current_smoke_repoll_allows_only_bounded_numerical_jitter(self):
        with tempfile.TemporaryDirectory() as temporary:
            r1_paths, _ = self._seed_registry(temporary)
            self._prepare(r1_paths)

            def jittered(profile, paths, manifest, tree):
                result = self._fake_smoke(profile, paths, manifest, tree)
                jitter = (
                    0.5
                    * profile["executable_capsule"][
                        "numerical_replay_absolute_tolerance_mm"
                    ]
                )
                result["five_seed_max_abs_difference_mm"] = jitter
                result["root_domain"]["five_seed_max_abs_difference_mm"] = jitter
                return result

            self._prepare(r1_paths, smoke=jittered)

            def drifted(profile, paths, manifest, tree):
                result = self._fake_smoke(profile, paths, manifest, tree)
                result["root_domain"]["distribution_inventory_sha256"] = "f" * 64
                return result

            with self.assertRaises(preparation.EpochPreparationIntegrityError):
                self._prepare(r1_paths, smoke=drifted)

    def test_newer_r1_candidate_advances_preparation_without_rollback(self):
        with tempfile.TemporaryDirectory() as temporary:
            r1_paths, feed = self._seed_registry(temporary)
            self._prepare(r1_paths)
            feed.write_bytes(
                self._feed_bytes(
                    revision_id="source-revision-two",
                    exported_at_utc="2026-08-26T14:30:00Z",
                    value=2.0,
                )
            )
            registry._poll_epoch_registry(  # noqa: SLF001
                runtime_root=r1_paths.root,
                _source_feed_path=feed,
                clock=lambda: FIXED_NOW,
                _prebuilder=self._fake_prebuilder,
            )
            self._prepare(r1_paths)
            _, events = self._replay(r1_paths)
            self.assertEqual(len(events), 2)
            self.assertEqual(
                [event["registry_event_sequence_id"] for event in events], [1, 2]
            )
            self.assertNotEqual(events[0]["candidate_id"], events[1]["candidate_id"])

    def test_dynamic_local_import_and_unreviewed_augmentation_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            r1_paths, _ = self._seed_registry(temporary)
            event = registry.replay_registry(self.r1_profile, r1_paths)[0]
            receipt = registry._verify_receipt(  # noqa: SLF001
                self.r1_profile, r1_paths, event["candidate_receipt"]
            )
            r1_manifest = registry._verify_capsule(  # noqa: SLF001
                self.r1_profile, r1_paths, receipt["capsule"]
            )
            paths = preparation.preparation_paths(
                self.profile, runtime_root=r1_paths.root
            )
            real_parse = ast.parse

            for dynamic_source in (
                '__import__("monitoring.fake")',
                ('from importlib import import_module as im\nim("monitoring.fake")'),
                'import runpy\nrunpy.run_module("monitoring.fake")',
            ):
                with self.subTest(dynamic_source=dynamic_source):

                    def inject_dynamic(source, filename):
                        tree = real_parse(source, filename=filename)
                        tree.body.extend(real_parse(dynamic_source).body)
                        return tree

                    with mock.patch.object(
                        preparation.ast, "parse", side_effect=inject_dynamic
                    ):
                        with self.assertRaises(
                            preparation.EpochPreparationIntegrityError
                        ):
                            preparation._resolve_local_closure(  # noqa: SLF001
                                self.profile, paths, r1_paths, r1_manifest
                            )

            hostile = {
                **self.profile,
                "executable_capsule": dict(self.profile["executable_capsule"]),
            }
            hostile["executable_capsule"]["augmentation_files"] = [
                {
                    "path": "code/convlstm/__init__.py",
                    "expected_sha256": "f" * 64,
                },
                self.profile["executable_capsule"]["augmentation_files"][1],
            ]
            with self.assertRaises(preparation.EpochPreparationIntegrityError):
                preparation._resolve_local_closure(  # noqa: SLF001
                    hostile, paths, r1_paths, r1_manifest
                )

    def test_invalid_smoke_never_commits_preparation_event(self):
        for mutation in ("not_isolated", "numerical_drift", "trusted_missing"):
            with (
                self.subTest(mutation=mutation),
                tempfile.TemporaryDirectory() as temporary,
            ):
                r1_paths, _ = self._seed_registry(temporary)

                def invalid(profile, paths, manifest, tree):
                    result = self._fake_smoke(profile, paths, manifest, tree)
                    if mutation == "not_isolated":
                        result["isolated_mode"] = False
                    elif mutation == "numerical_drift":
                        result["five_seed_max_abs_difference_mm"] = 1.0
                    else:
                        result["trusted_time_domain"]["trusted_time_core_imported"] = (
                            False
                        )
                    return result

                with self.assertRaises(preparation.EpochPreparationIntegrityError):
                    self._prepare(r1_paths, smoke=invalid)
                paths = preparation.preparation_paths(
                    self.profile, runtime_root=r1_paths.root
                )
                self.assertFalse(paths.events.exists())

    def test_shared_manager_lock_is_busy_and_public_api_has_no_overrides(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "registry"
            r1_paths = registry.registry_paths(
                self.r1_profile,
                runtime_root=root,
                source_feed_path=Path(temporary) / "missing.json",
            )
            lock = registry._acquire_manager_lock(r1_paths)  # noqa: SLF001
            try:
                with self.assertRaises(preparation.EpochPreparationBusyError):
                    preparation._poll_epoch_preparation(  # noqa: SLF001
                        runtime_root=root,
                        _smoke_runner=self._fake_smoke,
                    )
            finally:
                lock.close()
        with self.assertRaises(TypeError):
            preparation.poll_epoch_preparation(  # type: ignore[call-arg]
                runtime_root=ROOT / "runtime" / "ootang_epoch_registry_v1"
            )
        with self.assertRaises(preparation.EpochPreparationIntegrityError):
            preparation._poll_epoch_preparation(  # noqa: SLF001
                _smoke_runner=self._fake_smoke
            )
        with self.assertRaises(preparation.EpochPreparationIntegrityError):
            preparation._poll_epoch_preparation(  # noqa: SLF001
                runtime_root=ROOT / "runtime" / "ootang_epoch_registry_v1",
                _smoke_runner=self._fake_smoke,
            )

    def test_default_smoke_uses_two_frozen_isolated_uv_domains(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = preparation.PreparationPaths(
                root=root,
                manager_lock=root / "manager.lock",
                status=root / "status.json",
                head=root / "head.json",
                events=root / "events",
                objects=root / "objects",
                capsules=root / "capsules",
                trees=root / "trees",
                smoke_receipts=root / "smoke",
            )
            paths.capsules.mkdir(parents=True)
            manifest = {
                "tree_sha256": "1" * 64,
                "root_modules": self.profile["executable_capsule"]["root_modules"],
                "candidate_live_epoch_id": "2" * 64,
                "slot_live_root": str(root / "slot"),
            }
            capsule_raw = registry._canonical_bytes(manifest)  # noqa: SLF001
            (
                paths.capsules / f"{hashlib.sha256(capsule_raw).hexdigest()}.json"
            ).write_bytes(capsule_raw)
            fake = self._fake_smoke(self.profile, paths, manifest, root / "tree")
            calls = []

            def completed(command, **kwargs):
                calls.append((command, kwargs))
                if command[-1] == "--version":
                    return subprocess.CompletedProcess(
                        command, 0, stdout=b"uv 0.12.5 (test)\n", stderr=b""
                    )
                payload = (
                    fake["root_domain"]
                    if _ISOLATED_SCRIPT_IN_COMMAND(
                        command, preparation._ISOLATED_SMOKE_SCRIPT
                    )
                    else fake["trusted_time_domain"]
                )
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps(payload, separators=(",", ":")).encode(),
                    stderr=b"",
                )

            with mock.patch.object(
                preparation.subprocess, "run", side_effect=completed
            ):
                result = preparation._default_smoke_runner(  # noqa: SLF001
                    self.profile, paths, manifest, root / "tree"
                )
            self.assertEqual(
                result["schema_version"], "ootang_epoch_isolated_smoke_result_v2"
            )
            self.assertEqual(len(calls), 3)
            self.assertEqual(
                calls[0][0][0], self.profile["root_environment"]["required_uv_path"]
            )
            self.assertEqual(
                result["uv_executable_sha256"],
                self.profile["root_environment"]["required_uv_sha256"],
            )
            for command, _ in calls[1:]:
                self.assertIn("--no-config", command)
                self.assertIn("--isolated", command)
                self.assertIn("--frozen", command)
                self.assertIn("-I", command)
                self.assertIn("-B", command)
            self.assertNotEqual(calls[1][0][4], calls[2][0][4])

    def test_isolated_numerical_script_rejects_nan_and_missing_stations(self):
        predictions = {
            "valid": '{seed: {"A": 1.0, "B": 2.0} for seed in range(5)}',
            "nan": '{seed: {"A": float("nan"), "B": 2.0} for seed in range(5)}',
            "missing": "{seed: {} for seed in range(5)}",
        }
        for scenario, expression in predictions.items():
            with (
                self.subTest(scenario=scenario),
                tempfile.TemporaryDirectory() as temporary,
            ):
                tree = Path(temporary) / "tree"
                monitoring = tree / "code" / "monitoring"
                convlstm = tree / "code" / "convlstm"
                monitoring.mkdir(parents=True)
                convlstm.mkdir(parents=True)
                sources = {
                    "code/monitoring/ootang_prequential_live.py": """
def load_config(*args): return {}
def runtime_paths(*args, **kwargs): return None
def load_prerequisites(*args, **kwargs): return object()
def _epoch_id(*args): return "e" * 64
""",
                    "code/monitoring/ootang_live_source.py": """
from types import SimpleNamespace
def load_deploy_profile(*args, **kwargs): return {}
def load_activation_source(*args, **kwargs): return SimpleNamespace(frame=object())
""",
                    "code/convlstm/ootang_production_bundle.py": f"""
from pathlib import Path
from types import SimpleNamespace
def load_deploy_profile(*args, **kwargs):
    return {{"source_feed": {{"station_order_model": ["A", "B"]}}}}
def load_deploy_bundle(*args, **kwargs):
    training = Path(__file__).resolve().parents[2] / "training.json"
    return SimpleNamespace(training_manifest=SimpleNamespace(path=training))
def predict_p50(*args, **kwargs): return {expression}
""",
                }
                artifacts = []
                for logical, source in sorted(sources.items()):
                    path = tree / logical
                    path.write_text(source, encoding="utf-8")
                    raw = path.read_bytes()
                    artifacts.append(
                        {
                            "logical_path": logical,
                            "sha256": hashlib.sha256(raw).hexdigest(),
                            "size_bytes": len(raw),
                        }
                    )
                (tree / "training.json").write_text(
                    json.dumps(
                        {
                            "reload_replay": [
                                {
                                    "seed": seed,
                                    "reloaded_p50_mm": [1.0, 2.0],
                                }
                                for seed in range(5)
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
                manifest = {
                    "artifacts": artifacts,
                    "root_modules": [],
                    "slot_live_root": str(tree / "slot"),
                    "candidate_live_epoch_id": "e" * 64,
                    "tree_sha256": "1" * 64,
                }
                manifest_path = tree / "manifest.json"
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        "-B",
                        "-c",
                        preparation._ISOLATED_SMOKE_SCRIPT,  # noqa: SLF001
                        str(manifest_path),
                        str(tree),
                        sys.version.split()[0],
                        "0.000001",
                    ],
                    check=False,
                    capture_output=True,
                    timeout=20,
                )
                if scenario == "valid":
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                else:
                    self.assertNotEqual(completed.returncode, 0)

    def test_cli_maps_busy_and_integrity_without_manual_switches(self):
        with self.assertRaises(SystemExit):
            preparation._parse_args(["--force"])  # noqa: SLF001
        with mock.patch.object(
            preparation,
            "poll_epoch_preparation",
            side_effect=preparation.EpochPreparationBusyError("busy"),
        ):
            self.assertEqual(preparation.main([]), 3)
        with mock.patch.object(
            preparation,
            "poll_epoch_preparation",
            side_effect=preparation.EpochPreparationIntegrityError("tamper"),
        ):
            self.assertEqual(preparation.main([]), 2)


def _ISOLATED_SCRIPT_IN_COMMAND(command: list[str], script: str) -> bool:
    return any(argument == script for argument in command)


if __name__ == "__main__":
    unittest.main()
