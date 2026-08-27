"""Contracts for the R2b machine-only epoch drain-start barrier."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_epoch_drain as drain  # noqa: E402
from monitoring import ootang_epoch_preparation as preparation  # noqa: E402
from monitoring import ootang_epoch_registry as registry  # noqa: E402
from monitoring import ootang_issue_producer as issue_producer  # noqa: E402
from monitoring import ootang_issue_replay as issue_replay  # noqa: E402
from monitoring import ootang_live_ledger as live_ledger  # noqa: E402
from monitoring import ootang_live_source as source  # noqa: E402
from monitoring import ootang_outcome_materializer as outcomes  # noqa: E402
from monitoring import ootang_prequential_live as live  # noqa: E402
from monitoring import ootang_verified_live as guard  # noqa: E402
from tests import test_ootang_epoch_preparation as preparation_tests  # noqa: E402
from tests import test_ootang_issue_replay as replay_tests  # noqa: E402
from tests.test_ootang_prequential_live import _LiveFixture  # noqa: E402


FIXED_NOW = datetime(2030, 1, 2, 12, 0, tzinfo=timezone.utc)


class EpochDrainIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.profile = drain.load_drain_profile()
        cls.r1_profile = registry.load_registry_profile()
        cls.preparation_profile = preparation.load_preparation_profile()

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(
            prefix="ootang-epoch-drain-test-", dir=ROOT
        )
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        helper = preparation_tests.EpochPreparationTests(
            methodName="test_profile_is_machine_only_same_origin_r2a"
        )
        helper.profile = self.preparation_profile
        helper.r1_profile = self.r1_profile
        self.preparation_helper = helper
        self.r1_paths, self.feed = helper._seed_registry(str(self.base))
        helper._prepare(self.r1_paths)
        self.registry_root = self.r1_paths.root
        self.active_root = self.base / "live"
        self.shadow_root = self.base / "shadow"
        self._install_clean_live()

    def _install_clean_live(self) -> None:
        source_profile = source.load_deploy_profile()
        incoming = self.active_root / source_profile["runtime"]["incoming_feed"]
        incoming.parent.mkdir(parents=True, exist_ok=True)
        incoming.write_bytes(self.feed.read_bytes())
        ingested = source.ingest_source(
            source_profile, runtime_root=self.active_root, now=FIXED_NOW
        )
        self.assertEqual(ingested.status, "ready")
        assert ingested.source is not None
        fixture = object.__new__(_LiveFixture)
        fixture.root = self.active_root
        fixture.profile = live.load_config()
        fixture.paths = live.runtime_paths(
            fixture.profile, runtime_root=self.active_root
        )
        fixture.stations = list(fixture.profile["stations"])
        fixture.latest = dict(ingested.source.records[-1].displacement_mm)
        fixture.source_manifest = self.active_root / "source_snapshot" / "manifest.json"
        fixture.model_manifest = self.active_root / "model_bundle" / "manifest.json"
        checkpoints = [
            {
                "seed": seed,
                "artifact": fixture._artifact(  # noqa: SLF001
                    f"checkpoint-seed-{seed}.bin", f"seed={seed}\n".encode()
                ),
            }
            for seed in range(5)
        ]
        target = fixture.profile["target"]
        fixture._write_json(  # noqa: SLF001
            fixture.model_manifest,
            {
                "schema_version": fixture.profile["production_model"]["schema_version"],
                "case": "ootang",
                "model_version": "ootang-five-seed-production-test-v1",
                "created_at_utc": "2026-08-26T15:00:00Z",
                "training_cutoff_date": ingested.source.watermark.isoformat(),
                "stations": fixture.stations,
                "seeds": [0, 1, 2, 3, 4],
                "best_seed_selected": False,
                "target": {
                    "name": target["name"],
                    "unit": target["unit"],
                    "horizon": target["horizon"],
                },
                "input_schema_sha256": "a" * 64,
                "training_manifest": fixture._artifact(  # noqa: SLF001
                    "training-manifest.json", b"{}\n"
                ),
                "checkpoints": checkpoints,
            },
        )
        fixture.poll()

    def _run(self) -> drain.EpochDrainResult:
        return drain._start_epoch_drain(  # noqa: SLF001
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
            clock=lambda: FIXED_NOW,
        )

    def _paths(self) -> drain.DrainPaths:
        return drain.drain_paths(
            self.profile,
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
        )

    def test_crash_recovery_fence_tamper_and_idempotent_authority(self) -> None:
        paths = self._paths()
        incoming = self.active_root / "incoming" / "daily_finalized_feed.json"
        incoming_raw = incoming.read_bytes()
        extended = json.loads(incoming_raw)
        next_record = dict(extended["records"][-1])
        next_record.update(
            {
                "date": "2020-07-02",
                "revision_id": "source-revision-two",
                "observed_at_utc": "2020-07-02T04:00:00Z",
                "available_at_utc": "2020-07-02T05:00:00Z",
                "finalized_at_utc": "2020-07-02T06:00:00Z",
            }
        )
        extended["records"].append(next_record)
        incoming.write_bytes(
            (json.dumps(extended, separators=(",", ":")) + "\n").encode()
        )
        with self.assertRaises(drain.EpochDrainIntegrityError):
            drain._load_source_gate(paths, machine_now=FIXED_NOW)  # noqa: SLF001
        extended["exported_at_utc"] = "2026-08-27T14:00:00Z"
        extended_raw = (json.dumps(extended, separators=(",", ":")) + "\n").encode()
        large_staged_raw = extended_raw + b" " * (5 * 1024 * 1024)
        incoming.write_bytes(extended_raw)
        waiting = self._run()
        self.assertEqual(waiting.status, "waiting_for_source_ingest")
        self.assertFalse(paths.intents.exists())
        self.assertFalse(paths.events.exists())
        self.assertFalse(
            json.loads(waiting.status_path.read_bytes())["candidate_at_intent"]
        )
        incoming.write_bytes(incoming_raw)

        with (
            mock.patch.object(
                drain,
                "_publish_intent",
                side_effect=RuntimeError("crash-after-tombstone-before-intent"),
            ),
            self.assertRaises(drain.EpochDrainIntegrityError),
        ):
            self._run()
        self.assertFalse(paths.intents.exists())
        orphan_tombstones = list(paths.tombstones.iterdir())
        self.assertEqual(len(orphan_tombstones), 1)
        orphan_inode = os.stat(orphan_tombstones[0]).st_ino

        real_fsync_directory = registry._fsync_directory  # noqa: SLF001
        intent_parent_fsync_crashed = False

        def crash_intent_parent_fsync(path: Path, *, name: str) -> None:
            nonlocal intent_parent_fsync_crashed
            if not intent_parent_fsync_crashed and path == paths.intents:
                intent_parent_fsync_crashed = True
                raise OSError("crash-before-intent-parent-fsync")
            real_fsync_directory(path, name=name)

        with (
            mock.patch.object(
                registry,
                "_fsync_directory",
                side_effect=crash_intent_parent_fsync,
            ),
            self.assertRaises(drain.EpochDrainIntegrityError),
        ):
            self._run()
        self.assertTrue(intent_parent_fsync_crashed)
        self.assertEqual(len(list(paths.intents.glob("*.json"))), 1)
        blocked_after_intent_link = json.loads(paths.status.read_bytes())
        self.assertEqual(blocked_after_intent_link["drain_status"], "blocked_integrity")
        self.assertFalse(blocked_after_intent_link["candidate_at_intent"])

        attempt_parent_fsync_crashed = False

        def crash_attempt_parent_fsync(path: Path, *, name: str) -> None:
            nonlocal attempt_parent_fsync_crashed
            if not attempt_parent_fsync_crashed and path == paths.exchange_attempts:
                attempt_parent_fsync_crashed = True
                raise OSError("crash-before-exchange-attempt-parent-fsync")
            real_fsync_directory(path, name=name)

        with (
            mock.patch.object(
                registry,
                "_fsync_directory",
                side_effect=crash_attempt_parent_fsync,
            ),
            self.assertRaises(drain.EpochDrainIntegrityError) as attempt_fsync_crash,
        ):
            self._run()
        self.assertTrue(attempt_parent_fsync_crashed)
        self.assertIn(
            "crash-before-exchange-attempt-parent-fsync",
            str(attempt_fsync_crash.exception),
        )
        self.assertEqual(len(list(paths.exchange_attempts.glob("*.json"))), 1)
        self.assertFalse(
            (orphan_tombstones[0] / drain.ARMED_ATTEMPT_MARKER_NAME).exists()
        )
        drain._verify_fence_acl(  # noqa: SLF001
            orphan_tombstones[0], name="unarmed fence after WAL fsync crash"
        )

        real_write_armed_temp = drain._write_armed_marker_temp  # noqa: SLF001
        armed_temp_crashed = False
        retry_attempt_parent_fsynced = False

        def observe_retry_attempt_parent_fsync(path: Path, *, name: str) -> None:
            nonlocal retry_attempt_parent_fsynced
            real_fsync_directory(path, name=name)
            if path == paths.exchange_attempts:
                retry_attempt_parent_fsynced = True

        def crash_after_armed_temp(*args: object, **kwargs: object) -> None:
            nonlocal armed_temp_crashed
            self.assertTrue(
                retry_attempt_parent_fsynced,
                "WAL parent must be re-fsynced before marker arming",
            )
            real_write_armed_temp(*args, **kwargs)  # type: ignore[arg-type]
            armed_temp_crashed = True
            raise RuntimeError("crash-after-armed-marker-temp")

        with (
            mock.patch.object(
                registry,
                "_fsync_directory",
                side_effect=observe_retry_attempt_parent_fsync,
            ),
            mock.patch.object(
                drain,
                "_write_armed_marker_temp",
                side_effect=crash_after_armed_temp,
            ),
            self.assertRaises(drain.EpochDrainIntegrityError) as temp_crash,
        ):
            self._run()
        self.assertTrue(retry_attempt_parent_fsynced)
        self.assertTrue(armed_temp_crashed)
        self.assertIn("crash-after-armed-marker-temp", str(temp_crash.exception))
        self.assertFalse(paths.events.exists())
        armed_temp_entries = list(orphan_tombstones[0].iterdir())
        self.assertEqual(len(armed_temp_entries), 1)
        self.assertTrue(
            armed_temp_entries[0].name.startswith(
                f"{drain.ARMED_ATTEMPT_MARKER_NAME}.tmp-"
            )
        )
        descriptor = drain._open_directory(  # noqa: SLF001
            orphan_tombstones[0], name="armed-marker temp crash fence"
        )
        try:
            self.assertIsNone(
                drain._read_fence_acl(descriptor, allow_missing=True)  # noqa: SLF001
            )
        finally:
            os.close(descriptor)

        # The queued feed changes after attempt 1 was durably prepared.  Recovery
        # must finish the known temp, append attempt 2, and atomically re-arm the
        # same fence inode rather than requiring manual cleanup.
        incoming.write_bytes(extended_raw)

        with (
            mock.patch.object(
                drain,
                "_rename_exchange",
                side_effect=RuntimeError("crash-before-exchange"),
            ),
            self.assertRaises(drain.EpochDrainIntegrityError) as recovered_crash,
        ):
            self._run()
        incoming.write_bytes(incoming_raw)
        self.assertIn("crash-before-exchange", str(recovered_crash.exception))
        self.assertFalse(paths.events.exists())
        intents = sorted(paths.intents.glob("*.json"))
        self.assertEqual(len(intents), 1)
        intent = json.loads(intents[0].read_text(encoding="utf-8"))
        self.assertEqual(os.stat(Path(intent["archive_route"])).st_ino, orphan_inode)
        self.assertEqual(
            os.stat(paths.issue_inbox).st_ino,
            intent["old_route_identity"]["inode"],
        )
        drain._verify_prepared_fence(paths, intent)  # noqa: SLF001
        attempt_paths = sorted(paths.exchange_attempts.glob("*.json"))
        self.assertEqual(len(attempt_paths), 2)
        attempt_tip = json.loads(attempt_paths[-1].read_bytes())
        armed_marker = json.loads(
            (
                Path(intent["archive_route"]) / drain.ARMED_ATTEMPT_MARKER_NAME
            ).read_bytes()
        )
        self.assertEqual(armed_marker["attempt_sequence_id"], 2)
        self.assertEqual(
            armed_marker["attempt_entry_sha256"], attempt_tip["entry_sha256"]
        )
        self.assertEqual(armed_marker["drain_boundary"], attempt_tip["drain_boundary"])

        fence_path = Path(intent["archive_route"])
        descriptor = drain._open_directory(  # noqa: SLF001
            fence_path, name="armed-marker ACL durability crash"
        )
        try:
            drain._remove_fence_acl(descriptor)  # noqa: SLF001
        finally:
            os.close(descriptor)
        real_marker_fsync = os.fsync
        marker_acl_fsync_crashed = False

        def crash_after_marker_acl_install(descriptor: int) -> None:
            nonlocal marker_acl_fsync_crashed
            opened = os.fstat(descriptor)
            if not marker_acl_fsync_crashed and opened.st_ino == orphan_inode:
                marker_acl_fsync_crashed = True
                raise OSError("crash-after-marker-ACL-before-fsync")
            real_marker_fsync(descriptor)

        with (
            mock.patch.object(
                drain.os,
                "fsync",
                side_effect=crash_after_marker_acl_install,
            ),
            self.assertRaises(drain.EpochDrainIntegrityError),
        ):
            self._run()
        self.assertTrue(marker_acl_fsync_crashed)
        self.assertEqual(
            os.stat(paths.issue_inbox).st_ino,
            intent["old_route_identity"]["inode"],
        )
        drain._verify_fence_acl(  # noqa: SLF001
            fence_path, name="marker ACL installed before fsync crash"
        )

        retry_fence_fsynced = False

        def observe_retry_fence_fsync(descriptor: int) -> None:
            nonlocal retry_fence_fsynced
            real_marker_fsync(descriptor)
            if os.fstat(descriptor).st_ino == orphan_inode:
                retry_fence_fsynced = True

        def stop_after_retry_fence_fsync(*args: object, **kwargs: object) -> None:
            del args, kwargs
            self.assertTrue(
                retry_fence_fsynced,
                "exact fence ACL must be re-fsynced before rename",
            )
            raise RuntimeError("stop-after-retry-fence-fsync")

        incoming.write_bytes(extended_raw)
        with (
            mock.patch.object(
                drain.os,
                "fsync",
                side_effect=observe_retry_fence_fsync,
            ),
            mock.patch.object(
                drain,
                "_rename_exchange",
                side_effect=stop_after_retry_fence_fsync,
            ),
            self.assertRaises(drain.EpochDrainIntegrityError) as retry_fsync_stop,
        ):
            self._run()
        incoming.write_bytes(incoming_raw)
        self.assertTrue(retry_fence_fsynced)
        self.assertIn("stop-after-retry-fence-fsync", str(retry_fsync_stop.exception))
        self.assertEqual(
            os.stat(paths.issue_inbox).st_ino,
            intent["old_route_identity"]["inode"],
        )

        unknown_temp = fence_path / f"{drain.ARMED_ATTEMPT_MARKER_NAME}.tmp-{'f' * 64}"
        descriptor = drain._open_directory(  # noqa: SLF001
            fence_path, name="unknown armed-marker temp injection"
        )
        try:
            drain._remove_fence_acl(descriptor)  # noqa: SLF001
            injected = os.open(
                unknown_temp.name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=descriptor,
            )
            try:
                os.write(injected, b"unknown armed temp\n")
                os.fsync(injected)
            finally:
                os.close(injected)
            drain._install_fence_acl(descriptor)  # noqa: SLF001
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        with self.assertRaises(drain.EpochDrainIntegrityError):
            self._run()
        self.assertEqual(
            os.stat(paths.issue_inbox).st_ino, intent["old_route_identity"]["inode"]
        )
        descriptor = drain._open_directory(  # noqa: SLF001
            fence_path, name="unknown armed-marker temp cleanup"
        )
        try:
            drain._remove_fence_acl(descriptor)  # noqa: SLF001
            os.unlink(unknown_temp.name, dir_fd=descriptor)
            drain._install_fence_acl(descriptor)  # noqa: SLF001
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        drain._verify_fence_acl(fence_path, name="cleaned armed fence")  # noqa: SLF001

        canonical_inode = os.stat(paths.issue_inbox).st_ino
        archive_inode = os.stat(Path(intent["archive_route"])).st_ino
        real_load_clean = drain._load_clean_state  # noqa: SLF001
        clean_load_count = 0
        capacity_extension_injected = False

        def load_with_legal_capacity_extension(
            *args: object, **kwargs: object
        ) -> object:
            nonlocal clean_load_count, capacity_extension_injected
            loaded = real_load_clean(*args, **kwargs)
            if isinstance(loaded, drain._Waiting):  # noqa: SLF001
                return loaded
            clean_load_count += 1
            if clean_load_count != 2:
                return loaded
            extended_clean = replace(
                loaded,
                guard_record_count=loaded.guard_record_count + 1,
                guard_inventory=(
                    *loaded.guard_inventory,
                    {
                        "target_date": "2099-12-31",
                        "capacity_test_padding": "x" * 1024,
                    },
                ),
            )
            self.assertTrue(
                drain._clean_state_extends(loaded, extended_clean)  # noqa: SLF001
            )
            capacity_extension_injected = True
            return extended_clean

        real_size_check = drain._require_manifest_size  # noqa: SLF001

        def reject_extended_boundary(raw: bytes, *, name: str) -> None:
            if name == "Epoch drain boundary":
                raise drain.EpochDrainIntegrityError("test boundary capacity")
            real_size_check(raw, name=name)

        with (
            mock.patch.object(
                drain,
                "_load_clean_state",
                side_effect=load_with_legal_capacity_extension,
            ),
            mock.patch.object(
                drain,
                "_require_manifest_size",
                side_effect=reject_extended_boundary,
            ),
        ):
            capacity_waiting = self._run()
        self.assertTrue(capacity_extension_injected)
        self.assertEqual(capacity_waiting.status, "waiting_for_drain_boundary_capacity")
        self.assertFalse(paths.events.exists())
        self.assertEqual(os.stat(paths.issue_inbox).st_ino, canonical_inode)
        self.assertEqual(os.stat(Path(intent["archive_route"])).st_ino, archive_inode)

        original_fence_verifier = drain._verify_prepared_fence  # noqa: SLF001
        delivery_injected = False

        def deliver_after_intent(*args: object, **kwargs: object) -> None:
            nonlocal delivery_injected
            original_fence_verifier(*args, **kwargs)
            if not delivery_injected:
                incoming.write_bytes(
                    (json.dumps(extended, separators=(",", ":")) + "\n").encode()
                )
                delivery_injected = True

        real_fsync = os.fsync
        fsync_crashed = False
        first_parent_stat = os.stat(paths.issue_inbox.parent)

        def crash_first_parent_fsync_after_exchange(descriptor: int) -> None:
            nonlocal fsync_crashed
            opened = os.fstat(descriptor)
            if (
                not fsync_crashed
                and os.stat(paths.issue_inbox).st_ino
                == intent["fence_identity"]["inode"]
                and (opened.st_dev, opened.st_ino)
                == (first_parent_stat.st_dev, first_parent_stat.st_ino)
            ):
                fsync_crashed = True
                raise OSError("crash-after-rename-before-parent-fsync")
            real_fsync(descriptor)

        with (
            mock.patch.object(
                drain,
                "_verify_prepared_fence",
                side_effect=deliver_after_intent,
            ),
            mock.patch.object(
                drain.os,
                "fsync",
                side_effect=crash_first_parent_fsync_after_exchange,
            ),
            self.assertRaises(drain.EpochDrainIntegrityError),
        ):
            self._run()
        self.assertTrue(delivery_injected)
        self.assertEqual(incoming.read_bytes(), extended_raw)
        self.assertTrue(fsync_crashed)
        self.assertFalse(paths.events.exists())
        self.assertEqual(
            os.stat(paths.issue_inbox).st_ino,
            intent["fence_identity"]["inode"],
        )
        with self.assertRaises(PermissionError):
            (paths.issue_inbox / "fsync-window-must-not-write.json").write_bytes(
                b"{}\n"
            )
        incoming.write_bytes(incoming_raw)

        second_parent_stat = os.stat(paths.tombstones)
        second_fsync_crashed = False

        def crash_second_parent_fsync_during_recovery(descriptor: int) -> None:
            nonlocal second_fsync_crashed
            opened = os.fstat(descriptor)
            if (
                not second_fsync_crashed
                and os.stat(paths.issue_inbox).st_ino
                == intent["fence_identity"]["inode"]
                and (opened.st_dev, opened.st_ino)
                == (second_parent_stat.st_dev, second_parent_stat.st_ino)
            ):
                second_fsync_crashed = True
                raise OSError("crash-before-tombstone-parent-fsync")
            real_fsync(descriptor)

        with (
            mock.patch.object(
                drain.os,
                "fsync",
                side_effect=crash_second_parent_fsync_during_recovery,
            ),
            self.assertRaises(drain.EpochDrainIntegrityError),
        ):
            self._run()
        self.assertTrue(second_fsync_crashed)
        self.assertFalse(paths.events.exists())
        self.assertEqual(os.stat(paths.issue_inbox).st_mode & 0o777, 0o555)

        # APFS forbids the cross-parent test rollback while the operand is 0555.
        # Temporarily permit the external rollback syscall, then present the
        # coordinator with the exact prepared inode pair again.
        os.chmod(paths.issue_inbox, 0o755)
        drain._rename_exchange(  # noqa: SLF001
            paths.issue_inbox, Path(intent["archive_route"])
        )
        drain._verify_prepared_fence(paths, intent)  # noqa: SLF001

        with (
            mock.patch.object(
                drain,
                "_append_event",
                side_effect=RuntimeError("crash-after-exchange"),
            ),
            self.assertRaises(drain.EpochDrainIntegrityError),
        ):
            self._run()
        self.assertFalse(paths.events.exists())
        self.assertEqual(
            os.stat(paths.issue_inbox).st_ino,
            intent["fence_identity"]["inode"],
        )
        self.assertEqual(os.stat(paths.issue_inbox).st_mode & 0o777, 0o555)
        with self.assertRaises(PermissionError):
            (paths.issue_inbox / "must-not-write.json").write_bytes(b"{}\n")

        os.chmod(paths.issue_inbox, 0o755)
        drain._rename_exchange(  # noqa: SLF001
            paths.issue_inbox, Path(intent["archive_route"])
        )
        drain._verify_prepared_fence(paths, intent)  # noqa: SLF001

        verify_calls = 0

        def deliver_after_second_verifier(*args: object, **kwargs: object) -> None:
            nonlocal verify_calls
            original_fence_verifier(*args, **kwargs)
            verify_calls += 1
            if verify_calls == 2:
                incoming.write_bytes(large_staged_raw)

        with mock.patch.object(
            drain,
            "_verify_prepared_fence",
            side_effect=deliver_after_second_verifier,
        ):
            result = self._run()
        self.assertEqual(result.status, "epoch_draining")
        self.assertEqual(verify_calls, 2)
        self.assertEqual(incoming.read_bytes(), large_staged_raw)
        events = sorted(paths.events.glob("*.json"))
        self.assertEqual(len(events), 1)
        event_raw = events[0].read_bytes()
        intent_raw = intents[0].read_bytes()
        event_payload = json.loads(event_raw)
        self.assertTrue(event_payload["candidate_at_intent"])
        self.assertFalse(event_payload["activation_candidate_selected"])
        boundary = drain._read_reference(  # noqa: SLF001
            event_payload["drain_boundary"],
            root=paths.root,
            name="test drain boundary",
        )
        boundary_payload = json.loads(boundary.raw)
        self.assertTrue(boundary_payload["staged_incoming"]["present"])
        staged = drain._read_reference(  # noqa: SLF001
            boundary_payload["staged_incoming"]["artifact"],
            root=paths.root,
            name="test staged next-epoch feed",
            maximum_bytes=registry.MAX_FEED_BYTES,
        )
        self.assertEqual(staged.raw, large_staged_raw)
        self.assertTrue(boundary_payload["candidate_at_intent"])
        self.assertFalse(boundary_payload["activation_candidate_selected"])
        authority = drain._load_authority(  # noqa: SLF001
            self.profile,
            paths,
            registry_entry_sha256=event_payload["registry_event_entry_sha256"],
            preparation_entry_sha256=event_payload["preparation_event_entry_sha256"],
        )
        self.assertIsInstance(authority, drain._Authority)  # noqa: SLF001
        assert isinstance(authority, drain._Authority)  # noqa: SLF001
        intent_snapshot = drain._load_single_intent(paths)  # noqa: SLF001
        assert intent_snapshot is not None
        verified_intent, verified_capsule, intent_start = drain._verify_intent(  # noqa: SLF001
            self.profile,
            paths,
            authority,
            None,
            intent_snapshot,
            require_current_implementation=False,
        )
        attempts = drain._scan_exchange_attempts(  # noqa: SLF001
            self.profile,
            paths,
            authority,
            intent_snapshot,
            verified_intent,
            verified_capsule,
        )
        self.assertGreaterEqual(len(attempts), 2)
        terminal_attempt = attempts[-1]
        terminal_attempt.snapshot.path.unlink()
        with self.assertRaises(drain.EpochDrainIntegrityError):
            self._run()
        self.assertEqual(events[0].read_bytes(), event_raw)
        registry._publish_once(  # noqa: SLF001
            terminal_attempt.snapshot.path,
            terminal_attempt.snapshot.raw,
            root=paths.root,
            name="restored terminal exchange attempt",
        )
        self.assertEqual(self._run().status, "already_epoch_draining_idempotent")

        attempts = drain._scan_exchange_attempts(  # noqa: SLF001
            self.profile,
            paths,
            authority,
            intent_snapshot,
            verified_intent,
            verified_capsule,
        )
        fence_prepare = drain._load_single_fence_prepare(paths)  # noqa: SLF001
        assert fence_prepare is not None
        extra_sequence = len(attempts) + 1
        extra_unsigned = drain._exchange_attempt_unsigned(  # noqa: SLF001
            self.profile,
            paths,
            authority,
            intent_snapshot,
            verified_intent,
            fence_prepare,
            verified_capsule,
            attempts[-1].boundary,
            sequence_id=extra_sequence,
            previous_entry_sha256=attempts[-1].payload["entry_sha256"],
        )
        extra_entry = drain._sha256(  # noqa: SLF001
            drain._canonical_bytes(extra_unsigned)  # noqa: SLF001
        )
        extra_payload = {**extra_unsigned, "entry_sha256": extra_entry}
        extra_attempt_path = drain._exchange_attempt_path(  # noqa: SLF001
            paths, extra_sequence, extra_entry
        )
        registry._publish_once(  # noqa: SLF001
            extra_attempt_path,
            drain._canonical_bytes(extra_payload),  # noqa: SLF001
            root=paths.root,
            name="adversarial post-event exchange attempt",
        )
        with self.assertRaises(drain.EpochDrainIntegrityError):
            self._run()
        self.assertEqual(events[0].read_bytes(), event_raw)
        extra_attempt_path.unlink()
        self.assertEqual(self._run().status, "already_epoch_draining_idempotent")

        outside_attempt = self.base / "outside-exchange-attempt.json"
        outside_attempt.write_bytes(b"{}\n")
        polluted_attempt = paths.exchange_attempts / "extra.json"
        polluted_attempt.symlink_to(outside_attempt)
        with self.assertRaises(drain.EpochDrainIntegrityError):
            self._run()
        polluted_attempt.unlink()

        canonical_marker = paths.issue_inbox / drain.ARMED_ATTEMPT_MARKER_NAME
        archive_marker = Path(intent["archive_route"]) / drain.ARMED_ATTEMPT_MARKER_NAME
        archive_marker.write_bytes(canonical_marker.read_bytes())
        os.chmod(archive_marker, drain.ARMED_ATTEMPT_MARKER_MODE)
        with self.assertRaises(drain.EpochDrainIntegrityError):
            self._run()
        archive_marker.unlink()
        self.assertEqual(self._run().status, "already_epoch_draining_idempotent")

        replaced_live_entries = list(intent_start.live_entry_sha256s)
        replaced_live_entries[-1] = "f" * 64
        same_count_replacement = replace(
            intent_start,
            live_entry_sha256s=tuple(replaced_live_entries),
            live_terminal_sha256="f" * 64,
        )
        with self.assertRaises(drain.EpochDrainIntegrityError):
            drain._verify_intent(  # noqa: SLF001
                self.profile,
                paths,
                authority,
                same_count_replacement,
                intent_snapshot,
                require_current_implementation=False,
            )
        changed_source = json.loads(json.dumps(intent_start.source_authority))
        changed_source["records"][0]["rainfall_mm"] += 1.0
        source_replacement = replace(intent_start, source_authority=changed_source)
        with self.assertRaises(drain.EpochDrainIntegrityError):
            drain._verify_intent(  # noqa: SLF001
                self.profile,
                paths,
                authority,
                source_replacement,
                intent_snapshot,
                require_current_implementation=False,
            )
        extra_tombstone = paths.tombstones / ("b" * 64)
        extra_tombstone.mkdir()
        with self.assertRaises(drain.EpochDrainIntegrityError):
            drain._verify_intent(  # noqa: SLF001
                self.profile,
                paths,
                authority,
                None,
                intent_snapshot,
                require_current_implementation=False,
            )
        extra_tombstone.rmdir()
        outside_tombstone = self.base / "outside-extra-tombstone"
        outside_tombstone.mkdir()
        extra_tombstone.symlink_to(outside_tombstone, target_is_directory=True)
        with self.assertRaises(drain.EpochDrainIntegrityError):
            drain._verify_intent(  # noqa: SLF001
                self.profile,
                paths,
                authority,
                None,
                intent_snapshot,
                require_current_implementation=False,
            )
        extra_tombstone.unlink()
        changed_implementation_profile = dict(self.profile)
        changed_implementation_profile["_implementation_sha256"] = "f" * 64
        drain._verify_capsule(  # noqa: SLF001
            changed_implementation_profile,
            paths,
            authority,
            None,
            intent["drain_capsule"],
            require_current_implementation=False,
        )
        drain._verify_intent(  # noqa: SLF001
            changed_implementation_profile,
            paths,
            authority,
            None,
            intent_snapshot,
            require_current_implementation=False,
        )
        self.assertEqual(
            drain.replay_drain(
                changed_implementation_profile,
                paths,
                authority,
                None,
            ),
            (event_payload,),
        )
        with self.assertRaises(drain.EpochDrainIntegrityError):
            drain._verify_capsule(  # noqa: SLF001
                changed_implementation_profile,
                paths,
                authority,
                None,
                intent["drain_capsule"],
                require_current_implementation=True,
            )

        capsule = drain._read_reference(  # noqa: SLF001
            intent["drain_capsule"], root=paths.root, name="test drain capsule"
        )
        relocated_capsule = paths.root / "relocated-capsule.json"
        relocated_capsule.write_bytes(capsule.raw)
        relocated_capsule_ref = {
            "path": relocated_capsule.relative_to(paths.root).as_posix(),
            "sha256": capsule.sha256,
            "size_bytes": capsule.size_bytes,
        }
        with self.assertRaises(drain.EpochDrainIntegrityError):
            drain._verify_capsule(  # noqa: SLF001
                self.profile,
                paths,
                authority,
                None,
                relocated_capsule_ref,
                require_current_implementation=False,
            )
        relocated_capsule.unlink()

        capsule_payload = json.loads(capsule.raw)
        self.assertTrue(capsule_payload["candidate_at_intent"])
        self.assertFalse(capsule_payload["activation_candidate_selected"])
        for field, suffix in (
            ("profile_object", "relocated-profile.json"),
            ("implementation_object", "relocated-implementation.py"),
        ):
            with self.subTest(relocated_capsule_object=field):
                bound = drain._read_reference(  # noqa: SLF001
                    capsule_payload[field],
                    root=paths.root,
                    name=f"test {field}",
                )
                relocated_object = paths.objects / f"{bound.sha256}.{suffix}"
                relocated_object.write_bytes(bound.raw)
                modified = json.loads(capsule.raw)
                modified[field]["path"] = relocated_object.relative_to(
                    paths.root
                ).as_posix()
                modified_raw = drain._canonical_bytes(modified)  # noqa: SLF001
                modified_digest = drain._sha256(modified_raw)  # noqa: SLF001
                modified_path = paths.capsules / f"{modified_digest}.json"
                modified_path.write_bytes(modified_raw)
                modified_ref = {
                    "path": modified_path.relative_to(paths.root).as_posix(),
                    "sha256": modified_digest,
                    "size_bytes": len(modified_raw),
                }
                try:
                    with self.assertRaises(drain.EpochDrainIntegrityError):
                        drain._verify_capsule(  # noqa: SLF001
                            self.profile,
                            paths,
                            authority,
                            None,
                            modified_ref,
                            require_current_implementation=False,
                        )
                finally:
                    modified_path.unlink()
                    relocated_object.unlink()
        self.assertEqual(
            os.stat(intent["archive_route"]).st_ino,
            intent["old_route_identity"]["inode"],
        )
        self.assertEqual(os.stat(paths.issue_inbox).st_mode & 0o777, 0o555)
        status = json.loads(result.status_path.read_text(encoding="utf-8"))
        self.assertEqual(status["lifecycle_state"], "DRAINING")
        self.assertTrue(status["epoch_drain_started"])
        self.assertFalse(status["old_epoch_drained"])
        for claim in drain.FALSE_CLAIMS:
            self.assertFalse(status[claim])

        paths.head.write_bytes(b"tampered cache\n")
        paths.status.write_bytes(b"tampered cache\n")
        repeated = self._run()
        self.assertEqual(repeated.status, "already_epoch_draining_idempotent")
        self.assertEqual(events[0].read_bytes(), event_raw)
        self.assertEqual(len(list(paths.events.glob("*.json"))), 1)

        extra_prepare = paths.fence_prepares / "extra.json"
        extra_prepare.write_bytes(b"{}\n")
        with self.assertRaises(drain.EpochDrainIntegrityError):
            self._run()
        extra_prepare.unlink()
        outside_prepare = self.base / "outside-fence-prepare.json"
        outside_prepare.write_bytes(b"{}\n")
        extra_prepare.symlink_to(outside_prepare)
        with self.assertRaises(drain.EpochDrainIntegrityError):
            self._run()
        extra_prepare.unlink()

        old_route_inode = os.stat(intent["archive_route"]).st_ino
        fence_inode = os.stat(paths.issue_inbox).st_ino
        os.chmod(paths.issue_inbox, 0o755)
        drain._rename_exchange(  # noqa: SLF001
            paths.issue_inbox, Path(intent["archive_route"])
        )
        os.chmod(intent["archive_route"], 0o555)
        self.assertEqual(os.stat(paths.issue_inbox).st_ino, old_route_inode)
        self.assertEqual(os.stat(intent["archive_route"]).st_ino, fence_inode)
        self.assertEqual(os.stat(intent["archive_route"]).st_mode & 0o777, 0o555)
        drain._verify_fence_acl(  # noqa: SLF001
            Path(intent["archive_route"]), name="rolled-back hardened fence"
        )
        repaired = self._run()
        self.assertEqual(repaired.status, "already_epoch_draining_idempotent")
        self.assertEqual(os.stat(paths.issue_inbox).st_ino, fence_inode)
        self.assertEqual(os.stat(intent["archive_route"]).st_ino, old_route_inode)
        self.assertEqual(os.stat(paths.issue_inbox).st_mode & 0o777, 0o555)
        self.assertEqual(events[0].read_bytes(), event_raw)

        os.chmod(paths.issue_inbox, 0o500)
        with self.assertRaises(drain.EpochDrainIntegrityError):
            self._run()
        blocked = json.loads(paths.status.read_text(encoding="utf-8"))
        self.assertEqual(blocked["lifecycle_state"], "DRAINING")
        self.assertEqual(blocked["event_count"], 1)
        self.assertFalse(blocked["canonical_old_issue_route_fenced"])
        self.assertFalse(blocked["scheduler_old_issue_creation_fenced"])
        os.chmod(paths.issue_inbox, 0o555)
        os.chmod(paths.issue_inbox, 0o444)
        with self.assertRaises(drain.EpochDrainIntegrityError):
            drain._verify_fenced_route(paths, intent)  # noqa: SLF001
        os.chmod(paths.issue_inbox, 0o555)

        archive_mode = stat.S_IMODE(os.stat(intent["archive_route"]).st_mode)
        os.chmod(intent["archive_route"], 0o700)
        with self.assertRaises(drain.EpochDrainIntegrityError):
            self._run()
        os.chmod(intent["archive_route"], archive_mode)

        boundary.path.write_bytes(boundary.raw + b" ")
        with self.assertRaises(drain.EpochDrainIntegrityError):
            self._run()
        boundary.path.write_bytes(boundary.raw)

        fence_inode = os.stat(paths.issue_inbox).st_ino
        archive_inode = os.stat(intent["archive_route"]).st_ino
        events[0].unlink()
        intents[0].unlink()
        with self.assertRaises(drain.EpochDrainIntegrityError):
            self._run()
        self.assertEqual(os.stat(paths.issue_inbox).st_ino, fence_inode)
        self.assertEqual(os.stat(intent["archive_route"]).st_ino, archive_inode)
        self.assertEqual(list(paths.events.iterdir()), [])
        self.assertEqual(list(paths.intents.iterdir()), [])
        events[0].write_bytes(event_raw)
        intents[0].write_bytes(intent_raw)

        events[0].write_bytes(event_raw + b" ")
        with self.assertRaises(drain.EpochDrainIntegrityError):
            self._run()

    def test_fence_prepare_completes_when_actual_r1_r2a_tip_advances(self) -> None:
        paths = self._paths()
        with (
            mock.patch.object(
                drain,
                "_publish_intent",
                side_effect=RuntimeError("crash-after-fence-prepare"),
            ),
            self.assertRaises(drain.EpochDrainIntegrityError),
        ):
            self._run()
        self.assertFalse(paths.intents.exists())
        prepare_path = next(paths.fence_prepares.glob("*.json"))
        prepare = json.loads(prepare_path.read_text(encoding="utf-8"))
        old_route_inode = os.stat(paths.issue_inbox).st_ino

        self.feed.write_bytes(
            self.preparation_helper._feed_bytes(  # noqa: SLF001
                revision_id="source-revision-two",
                exported_at_utc="2026-08-26T14:30:00Z",
                value=2.0,
            )
        )
        registry._poll_epoch_registry(  # noqa: SLF001
            runtime_root=self.r1_paths.root,
            _source_feed_path=self.feed,
            clock=lambda: FIXED_NOW,
            _prebuilder=self.preparation_helper._fake_prebuilder,  # noqa: SLF001
        )
        self.preparation_helper._prepare(self.r1_paths)  # noqa: SLF001
        preparation_events = preparation.replay_preparations(
            self.preparation_profile,
            preparation.preparation_paths(
                self.preparation_profile, runtime_root=self.r1_paths.root
            ),
            self.r1_profile,
            self.r1_paths,
            registry.replay_registry(self.r1_profile, self.r1_paths),
        )
        self.assertEqual(len(preparation_events), 2)
        self.assertNotEqual(
            preparation_events[-1]["candidate_id"], prepare["candidate_id"]
        )

        completed = self._run()
        self.assertEqual(completed.status, "epoch_draining")
        events = sorted(paths.events.glob("*.json"))
        self.assertEqual(len(events), 1)
        event = json.loads(events[0].read_text(encoding="utf-8"))
        intent_path = next(paths.intents.glob("*.json"))
        intent = json.loads(intent_path.read_text(encoding="utf-8"))
        self.assertEqual(
            event["fence_prepare"]["sha256"], drain._sha256(prepare_path.read_bytes())
        )  # noqa: SLF001
        self.assertEqual(event["candidate_id"], prepare["candidate_id"])
        self.assertTrue(event["candidate_at_intent"])
        self.assertFalse(event["activation_candidate_selected"])
        self.assertNotEqual(os.stat(paths.issue_inbox).st_ino, old_route_inode)
        self.assertEqual(
            os.stat(paths.issue_inbox).st_ino, intent["fence_identity"]["inode"]
        )
        self.assertFalse(event["active_epoch_switch_implemented"])
        self.assertFalse(event["automatic_epoch_rotation_implemented"])

    def test_exchanged_recovery_uses_armed_boundary_after_large_extension(
        self,
    ) -> None:
        paths = self._paths()
        with (
            mock.patch.object(
                drain,
                "_append_event",
                side_effect=RuntimeError("crash-after-exchange-before-event"),
            ),
            self.assertRaises(drain.EpochDrainIntegrityError),
        ):
            self._run()
        self.assertFalse(paths.events.exists())

        intent_snapshot = drain._load_single_intent(paths)  # noqa: SLF001
        assert intent_snapshot is not None
        intent = json.loads(intent_snapshot.raw)
        authority = drain._load_authority(  # noqa: SLF001
            self.profile,
            paths,
            registry_entry_sha256=intent["registry_event_entry_sha256"],
            preparation_entry_sha256=intent["preparation_event_entry_sha256"],
        )
        self.assertIsInstance(authority, drain._Authority)  # noqa: SLF001
        assert isinstance(authority, drain._Authority)  # noqa: SLF001
        intent_payload, capsule, _ = drain._verify_intent(  # noqa: SLF001
            self.profile,
            paths,
            authority,
            None,
            intent_snapshot,
            require_current_implementation=False,
        )
        attempts = drain._scan_exchange_attempts(  # noqa: SLF001
            self.profile,
            paths,
            authority,
            intent_snapshot,
            intent_payload,
            capsule,
        )
        self.assertEqual(len(attempts), 1)
        armed_attempt = attempts[-1]
        attempt_paths_before = tuple(paths.exchange_attempts.iterdir())
        real_load_clean = drain._load_clean_state  # noqa: SLF001
        extension_observed = False

        def load_with_large_post_swap_extension(
            *args: object, **kwargs: object
        ) -> object:
            nonlocal extension_observed
            loaded = real_load_clean(*args, **kwargs)
            if (
                isinstance(loaded, drain._CleanState)  # noqa: SLF001
                and kwargs.get("archived_issue_route") is not None
            ):
                extension_observed = True
                extended = replace(
                    loaded,
                    guard_record_count=loaded.guard_record_count + 1,
                    guard_inventory=(
                        *loaded.guard_inventory,
                        {
                            "target_date": "2099-12-31",
                            "post_swap_extension": "x" * (5 * 1024 * 1024),
                        },
                    ),
                )
                self.assertTrue(
                    drain._clean_state_extends(  # noqa: SLF001
                        armed_attempt.boundary_clean, extended
                    )
                )
                return extended
            return loaded

        with (
            mock.patch.object(
                drain,
                "_load_clean_state",
                side_effect=load_with_large_post_swap_extension,
            ),
            mock.patch.object(
                drain,
                "_publish_boundary",
                side_effect=AssertionError(
                    "exchanged recovery must not rebuild its armed boundary"
                ),
            ),
        ):
            recovered = self._run()
        self.assertTrue(extension_observed)
        self.assertEqual(recovered.status, "already_epoch_draining_idempotent")
        self.assertEqual(tuple(paths.exchange_attempts.iterdir()), attempt_paths_before)
        event_path = next(paths.events.glob("*.json"))
        event = json.loads(event_path.read_bytes())
        self.assertEqual(
            event["exchange_attempt"],
            drain._artifact_reference(  # noqa: SLF001
                armed_attempt.snapshot, paths.root
            ),
        )
        self.assertEqual(
            event["drain_boundary"],
            drain._artifact_reference(  # noqa: SLF001
                armed_attempt.boundary, paths.root
            ),
        )
        self.assertEqual(os.stat(paths.issue_inbox).st_mode & 0o777, 0o555)


class EpochDrainArchivedGuardTests(unittest.TestCase):
    """Exercise the physical archive with a real replayed, settled issue."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(
            prefix="ootang-epoch-drain-archive-test-", dir=ROOT
        )
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.active_root = self.base / "live"
        self.shadow_root = self.base / "shadow"
        self.registry_root = self.base / "registry"
        self.profile = drain.load_drain_profile()

    def _install_settled_guarded_issue(self) -> None:
        helper = object.__new__(replay_tests.IndependentIssueReplayContracts)
        helper.root = self.base
        helper.runtime = self.active_root
        helper.profile = issue_replay.load_replay_profile()
        helper.paths = issue_replay.runtime_paths(
            helper.profile, runtime_root=self.active_root
        )
        helper.paths.objects.mkdir(parents=True)
        helper.frame = helper._activation_frame()  # noqa: SLF001
        (
            helper.activation,
            helper.activation_payload,
            helper.prerequisites,
            helper.training_source,
        ) = helper._activation_lineage()  # noqa: SLF001
        helper.preprocessing = issue_replay._activation_preprocessing(  # noqa: SLF001
            activation=helper.activation,
            activation_payload=helper.activation_payload,
            training_source=helper.training_source,
            prerequisite_data_manifest=helper.prerequisites.source.data_manifest,
            paths=helper.paths,
            profile=helper.profile,
        )

        deploy = source.load_deploy_profile()
        incoming = self.active_root / deploy["runtime"]["incoming_feed"]
        incoming.parent.mkdir(parents=True, exist_ok=True)
        incoming.write_bytes(helper.feed_raw)
        ingested = source.ingest_source(
            deploy,
            runtime_root=self.active_root,
            now=datetime(2021, 7, 8, 10, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(ingested.status, "ready")

        _, _, _, issue_path = helper._ready_materials()  # noqa: SLF001
        issue_payload = json.loads(issue_path.read_text(encoding="utf-8"))
        issue_path.unlink()
        genesis_now = datetime(2021, 7, 8, 10, 1, tzinfo=timezone.utc)
        with mock.patch.object(
            live_ledger,
            "_utc_now_text",
            return_value=guard._utc_text(genesis_now),  # noqa: SLF001
        ):
            live.poll_live_runner(
                runtime_root=self.active_root, clock=lambda: genesis_now
            )

        producer_profile = issue_producer.load_config()
        receipt_root = issue_producer.required_path(
            self.active_root, producer_profile["runtime"]["issue_receipts"]
        )
        object_root = issue_producer.required_path(
            self.active_root, producer_profile["runtime"]["objects"]
        )
        issue_producer._publish_issue_idempotently(  # noqa: SLF001
            issue_path,
            issue_payload,
            receipt_root=receipt_root,
            object_root=object_root,
        )
        issue_now = datetime(2021, 7, 8, 10, 5, tzinfo=timezone.utc)
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(
                live_ledger,
                "_utc_now_text",
                return_value=guard._utc_text(issue_now),  # noqa: SLF001
            ),
        ):
            guard.poll_verified_live_runner(
                runtime_root=self.active_root, clock=lambda: issue_now
            )

        feed = json.loads(helper.feed_raw)
        finalized_record = dict(feed["records"][-1])
        finalized_record.update(
            {
                "date": "2021-07-09",
                "revision_id": "revision-2021-07-09",
                "observed_at_utc": "2021-07-09T08:00:00Z",
                "available_at_utc": "2021-07-09T08:01:00Z",
                "finalized_at_utc": "2021-07-09T08:02:00Z",
                "displacement_mm": {
                    station: float(value) + 1.0
                    for station, value in finalized_record["displacement_mm"].items()
                },
            }
        )
        feed["records"].append(finalized_record)
        feed["exported_at_utc"] = "2021-07-09T09:00:00Z"
        self.final_feed_raw = issue_replay._canonical_bytes(  # noqa: SLF001
            feed, newline=True
        )
        incoming.write_bytes(self.final_feed_raw)
        updated = source.ingest_source(
            deploy,
            runtime_root=self.active_root,
            now=datetime(2021, 7, 9, 9, 30, tzinfo=timezone.utc),
        )
        self.assertEqual(updated.status, "ready")
        outcome_now = datetime(2021, 7, 9, 10, 5, tzinfo=timezone.utc)
        materialized = outcomes.materialize_outcome(
            runtime_root=self.active_root, clock=lambda: outcome_now
        )
        self.assertEqual(materialized.status, "materialized")
        with mock.patch.dict(os.environ, {}, clear=True):
            for _ in range(2):
                with mock.patch.object(
                    live_ledger,
                    "_utc_now_text",
                    return_value=guard._utc_text(outcome_now),  # noqa: SLF001
                ):
                    guard.poll_verified_live_runner(
                        runtime_root=self.active_root, clock=lambda: outcome_now
                    )

        live_profile = live.load_config()
        live_paths = live.runtime_paths(live_profile, runtime_root=self.active_root)
        prerequisites = live.load_prerequisites(live_profile, live_paths)
        assert prerequisites is not None
        projection = live.load_verified_ledger_projection(
            live_profile, live_paths, prerequisites
        )
        self.assertIsNone(projection.outstanding_target_date)
        self.assertIn("2021-07-09", projection.settled_events)
        self.assertTrue(
            (self.active_root / "verified_live_intents/2021-07-09.json").is_file()
        )
        self.assertTrue(
            (self.active_root / "verified_live_completions/2021-07-09.json").is_file()
        )

    def test_settled_guard_replays_from_archive_across_restart(self) -> None:
        self._install_settled_guarded_issue()
        preparation_helper = preparation_tests.EpochPreparationTests(
            methodName="test_profile_is_machine_only_same_origin_r2a"
        )
        preparation_helper.profile = preparation.load_preparation_profile()
        preparation_helper.r1_profile = registry.load_registry_profile()
        r1_paths, _ = preparation_helper._seed_registry(  # noqa: SLF001
            str(self.base), feed_bytes=self.final_feed_raw
        )
        preparation_helper._prepare(r1_paths)  # noqa: SLF001
        paths = drain.drain_paths(
            self.profile,
            runtime_root=r1_paths.root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
        )
        machine_now = datetime(2021, 7, 9, 11, 0, tzinfo=timezone.utc)
        before = drain._load_clean_state(  # noqa: SLF001
            paths, machine_now=machine_now
        )
        self.assertIsInstance(before, drain._CleanState)  # noqa: SLF001
        assert isinstance(before, drain._CleanState)  # noqa: SLF001
        self.assertEqual(before.guard_record_count, 1)

        def run_coordinator() -> drain.EpochDrainResult:
            return drain._start_epoch_drain(  # noqa: SLF001
                runtime_root=r1_paths.root,
                active_runtime_root=self.active_root,
                shadow_runtime_root=self.shadow_root,
                clock=lambda: FIXED_NOW,
            )

        with (
            mock.patch.object(
                drain,
                "_append_event",
                side_effect=RuntimeError("crash-after-real-archive-exchange"),
            ),
            self.assertRaises(drain.EpochDrainIntegrityError),
        ):
            run_coordinator()
        self.assertFalse(paths.events.exists())
        intent_snapshot = drain._load_single_intent(paths)  # noqa: SLF001
        assert intent_snapshot is not None
        intent = json.loads(intent_snapshot.raw)
        archive = Path(intent["archive_route"])
        self.assertFalse((paths.issue_inbox / "2021-07-09.json").exists())
        self.assertTrue((archive / "2021-07-09.json").is_file())

        recovered = run_coordinator()
        self.assertEqual(recovered.status, "already_epoch_draining_idempotent")
        self.assertEqual(len(list(paths.events.glob("*.json"))), 1)
        event_path = next(paths.events.glob("*.json"))
        first_event_raw = event_path.read_bytes()
        event = json.loads(first_event_raw)
        boundary = drain._read_reference(  # noqa: SLF001
            event["drain_boundary"], root=paths.root, name="settled drain boundary"
        )
        boundary_payload = json.loads(boundary.raw)
        self.assertEqual(len(boundary_payload["issue_route_inventory"]["records"]), 1)
        self.assertEqual(len(boundary_payload["guard_inventory"]["records"]), 1)
        self.assertEqual(
            len(boundary_payload["outcome_registry_inventory"]["records"]), 1
        )
        self.assertEqual(
            boundary_payload["live_ledger"]["event_count"],
            len(boundary_payload["live_ledger"]["entry_sha256s"]),
        )
        restarted = run_coordinator()
        self.assertEqual(restarted.status, "already_epoch_draining_idempotent")
        self.assertEqual(event_path.read_bytes(), first_event_raw)
        self.assertFalse((paths.issue_inbox / "2021-07-09.json").exists())
        self.assertTrue((archive / "2021-07-09.json").is_file())


class EpochDrainFastTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(
            prefix="ootang-epoch-drain-fast-", dir=ROOT
        )
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.registry_root = self.base / "registry"
        self.active_root = self.base / "live"
        self.shadow_root = self.base / "shadow"
        self.profile = drain.load_drain_profile()

    def _run(self) -> drain.EpochDrainResult:
        return drain._start_epoch_drain(  # noqa: SLF001
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
            clock=lambda: FIXED_NOW,
        )

    def _paths(self) -> drain.DrainPaths:
        return drain.drain_paths(
            self.profile,
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
        )

    def test_absent_candidate_waits_without_fence_or_event(self) -> None:
        result = self._run()
        self.assertEqual(result.status, "waiting_for_candidate_ready")
        paths = self._paths()
        self.assertFalse(paths.intents.exists())
        self.assertFalse(paths.events.exists())
        status = json.loads(result.status_path.read_text(encoding="utf-8"))
        self.assertEqual(status["lifecycle_state"], "PREPARING")
        self.assertFalse(status["canonical_old_issue_route_fenced"])
        self.assertFalse(status["candidate_at_intent"])

    def test_manager_lock_busy_is_nonblocking_and_leaves_no_authority(self) -> None:
        paths = self._paths()
        paths.root.mkdir(parents=True)
        with paths.manager_lock.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(drain.EpochDrainBusyError):
                self._run()
        self.assertFalse(paths.intents.exists())
        self.assertFalse(paths.events.exists())

    def test_symlinked_mutable_namespace_fails_closed(self) -> None:
        paths = self._paths()
        paths.root.mkdir(parents=True)
        outside = self.base / "outside"
        outside.mkdir()
        paths.events.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(drain.EpochDrainIntegrityError):
            drain.replay_drain(  # type: ignore[arg-type]
                self.profile, paths, mock.sentinel.authority, None
            )

    def test_hidden_outcome_receipt_entry_is_not_ignored(self) -> None:
        receipt_root = self.base / "outcome_receipts"
        receipt_root.mkdir()
        (receipt_root / ".hidden").write_bytes(b"tamper\n")
        with self.assertRaises(drain.EpochDrainIntegrityError):
            drain._strict_outcome_receipt_targets(receipt_root)  # noqa: SLF001

    def test_unregistered_tombstone_or_canonical_fence_cannot_be_reused(self) -> None:
        paths = self._paths()
        paths.issue_inbox.mkdir(parents=True)
        paths.tombstones.mkdir(parents=True)
        tombstone = paths.tombstones / ("b" * 64)
        tombstone.mkdir()
        route_inode = os.stat(paths.issue_inbox).st_ino
        tombstone_before = os.stat(tombstone)
        with self.assertRaises(drain.EpochDrainIntegrityError):
            drain._ensure_route_and_tombstone(  # noqa: SLF001
                paths, "a" * 64, {}
            )
        self.assertEqual(os.stat(paths.issue_inbox).st_ino, route_inode)
        tombstone_after = os.stat(tombstone)
        self.assertEqual(tombstone_after.st_ino, tombstone_before.st_ino)
        self.assertEqual(
            stat.S_IMODE(tombstone_after.st_mode),
            stat.S_IMODE(tombstone_before.st_mode),
        )

        tombstone.rmdir()
        descriptor = drain._open_directory(  # noqa: SLF001
            paths.issue_inbox, name="test unregistered canonical fence"
        )
        try:
            drain._install_fence_acl(descriptor)  # noqa: SLF001
            os.fchmod(descriptor, 0o555)
        finally:
            os.close(descriptor)
        with self.assertRaises(drain.EpochDrainIntegrityError):
            drain._ensure_route_and_tombstone(  # noqa: SLF001
                paths, "a" * 64, {}
            )
        self.assertEqual(os.stat(paths.issue_inbox).st_ino, route_inode)
        self.assertEqual(list(paths.tombstones.iterdir()), [])

    def test_candidate_tombstone_crash_windows_recover_machine_only(self) -> None:
        paths = self._paths()
        paths.issue_inbox.mkdir(parents=True)
        candidate_id = "a" * 64
        tombstone = paths.tombstones / candidate_id
        prepare_payload = {
            "old_route_identity": drain._directory_identity(  # noqa: SLF001
                paths.issue_inbox, name="test canonical route"
            ),
            "tombstone_path": str(tombstone),
        }
        registry._publish_once(  # noqa: SLF001
            drain._fence_prepare_path(paths, candidate_id),  # noqa: SLF001
            drain._canonical_bytes(prepare_payload),  # noqa: SLF001
            root=paths.root,
            name="test fence prepare",
        )
        with (
            mock.patch.object(
                drain,
                "_install_fence_acl",
                side_effect=OSError("crash-after-mkdir-before-acl"),
            ),
            self.assertRaises(drain.EpochDrainIntegrityError),
        ):
            drain._ensure_route_and_tombstone(  # noqa: SLF001
                paths, candidate_id, prepare_payload
            )
        self.assertTrue(tombstone.is_dir())
        tombstone_inode = os.stat(tombstone).st_ino
        descriptor = drain._open_directory(  # noqa: SLF001
            tombstone, name="missing-ACL crash orphan"
        )
        try:
            self.assertIsNone(
                drain._read_fence_acl(descriptor, allow_missing=True)  # noqa: SLF001
            )
        finally:
            os.close(descriptor)

        real_fsync = os.fsync
        fsync_crashed = False

        def crash_after_acl_install(descriptor: int) -> None:
            nonlocal fsync_crashed
            opened = os.fstat(descriptor)
            if not fsync_crashed and opened.st_ino == tombstone_inode:
                fsync_crashed = True
                raise OSError("crash-after-acl-before-fsync")
            real_fsync(descriptor)

        with (
            mock.patch.object(drain.os, "fsync", side_effect=crash_after_acl_install),
            self.assertRaises(drain.EpochDrainIntegrityError),
        ):
            drain._ensure_route_and_tombstone(  # noqa: SLF001
                paths, candidate_id, prepare_payload
            )
        self.assertTrue(fsync_crashed)
        self.assertEqual(os.stat(tombstone).st_ino, tombstone_inode)
        drain._verify_fence_acl(  # noqa: SLF001
            tombstone, name="recovered crash-orphan tombstone"
        )
        recovered, _, fence = drain._ensure_route_and_tombstone(  # noqa: SLF001
            paths, candidate_id, prepare_payload
        )
        self.assertEqual(recovered, tombstone)
        self.assertEqual(fence["inode"], tombstone_inode)

        descriptor = drain._open_directory(  # noqa: SLF001
            tombstone, name="ACL remove/readback probe"
        )
        try:
            drain._remove_fence_acl(descriptor)  # noqa: SLF001
            self.assertIsNone(
                drain._read_fence_acl(descriptor, allow_missing=True)  # noqa: SLF001
            )
            drain._install_fence_acl(descriptor)  # noqa: SLF001
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        drain._verify_fence_acl(  # noqa: SLF001
            tombstone, name="restored ACL remove/readback probe"
        )

    def test_oversize_manifests_fail_before_durable_authority(self) -> None:
        paths = self._paths()
        clean = drain._CleanState(  # noqa: SLF001
            old_live_epoch_id="old-epoch",
            live_event_count=1,
            live_terminal_sha256="a" * 64,
            guard_record_count=0,
            trusted_time_record_count=0,
            shadow_event_count=0,
            shadow_terminal_sha256=drain.ZERO_HASH,
            issue_route_inventory_sha256=drain._sha256(b'{"records":[]}'),  # noqa: SLF001
            issue_route_inventory=(),
            outcome_registry_inventory=(),
            guard_inventory=(),
            trusted_time_inventory=(),
            source_authority={"oversize_test": "x" * 512},
            live_entry_sha256s=("a" * 64,),
            shadow_entry_sha256s=(),
        )
        with (
            mock.patch.object(drain, "MAX_MANIFEST_BYTES", 64),
            self.assertRaises(drain.EpochDrainIntegrityError),
        ):
            drain._publish_intent_prefix(self.profile, paths, clean)  # noqa: SLF001
        self.assertFalse(paths.objects.exists())

        snapshot = registry.ArtifactSnapshot(
            paths.root / "fixture.json",
            b"{}\n",
            drain._sha256(b"{}\n"),  # noqa: SLF001
            3,
        )
        oversized_payload = {"oversize_test": "x" * 512}
        with (
            mock.patch.object(drain, "MAX_MANIFEST_BYTES", 64),
            mock.patch.object(drain, "_capture_bound_object", return_value=snapshot),
            mock.patch.object(drain, "_publish_intent_prefix", return_value=snapshot),
            mock.patch.object(
                drain, "_capsule_payload", return_value=oversized_payload
            ),
            mock.patch.object(registry, "_publish_once") as publish_once,
            self.assertRaises(drain.EpochDrainIntegrityError),
        ):
            drain._publish_capsule(  # noqa: SLF001
                self.profile, paths, mock.sentinel.authority, clean
            )
        publish_once.assert_not_called()

        with (
            mock.patch.object(drain, "MAX_MANIFEST_BYTES", 64),
            mock.patch.object(
                drain, "_load_single_fence_prepare", return_value=snapshot
            ),
            mock.patch.object(drain, "_verify_fence_prepare"),
            mock.patch.object(drain, "_staged_next_epoch_snapshot", return_value={}),
            mock.patch.object(
                drain, "_boundary_payload", return_value=oversized_payload
            ),
            mock.patch.object(registry, "_publish_once") as publish_once,
            self.assertRaises(drain.EpochDrainIntegrityError),
        ):
            drain._publish_boundary(  # noqa: SLF001
                self.profile,
                paths,
                mock.sentinel.authority,
                clean,
                snapshot,
                snapshot,
                machine_now=FIXED_NOW,
                captured_at_utc="2026-08-27T12:00:00.000000Z",
            )
        publish_once.assert_not_called()
        for namespace in (
            paths.fence_prepares,
            paths.tombstones,
            paths.intents,
            paths.events,
        ):
            self.assertFalse(namespace.exists())

    def test_public_cli_has_no_manual_drain_overrides(self) -> None:
        arguments = drain._parse_args([]).__dict__  # noqa: SLF001
        for forbidden in ("date", "freeze", "approve", "force", "backdate"):
            self.assertNotIn(forbidden, arguments)
        fixture = drain.EpochDrainResult(
            "epoch_draining", self.base / "status.json", self.base / "event.json"
        )
        with mock.patch.object(drain, "start_epoch_drain", return_value=fixture):
            self.assertEqual(
                drain.main(["--config", str(drain.DEFAULT_CONFIG_PATH)]), 0
            )


if __name__ == "__main__":
    unittest.main()
