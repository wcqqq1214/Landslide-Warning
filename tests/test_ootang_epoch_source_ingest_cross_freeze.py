"""Fast contracts for the cross-freeze source-ingest writer/adopter."""

from __future__ import annotations

from datetime import timedelta
import json
import os
from pathlib import Path
import stat
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_epoch_source_ingest_cross_freeze as cross  # noqa: E402
from monitoring import (  # noqa: E402
    ootang_epoch_source_ingest_derived_reservation as derived,
)
from monitoring import ootang_epoch_workset_manifest as manifest  # noqa: E402
from monitoring import ootang_live_source as source  # noqa: E402
from tests import (  # noqa: E402
    test_ootang_epoch_source_ingest_derived_reservation as derived_test,
)


NOW = derived_test.AUTHORITY_NOW + timedelta(seconds=1)


class EpochSourceIngestCrossFreezeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = derived_test.EpochSourceIngestDerivedReservationTests(
            "test_seq2_not_published_waits_without_reservation"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.binding = self.fixture._publish_manifest()  # noqa: SLF001
        self.deploy_lock = (
            self.fixture.source_fixture.root
            / self.fixture.source_fixture.profile["runtime"]["deploy_lock"]
        )
        self.sentinel_identity: tuple[int, int, bytes] | None = None

    def _install_cut_sentinel(self) -> None:
        raw = b"synthetic-admission-cut-deploy-sentinel\n"
        self.deploy_lock.write_bytes(raw)
        self.deploy_lock.chmod(0o444)
        before = os.lstat(self.deploy_lock)
        self.sentinel_identity = (before.st_ino, stat.S_IMODE(before.st_mode), raw)

    def _assert_cut_sentinel_unchanged(self) -> None:
        assert self.sentinel_identity is not None
        inode, mode, raw = self.sentinel_identity
        after = os.lstat(self.deploy_lock)
        self.assertEqual(after.st_ino, inode)
        self.assertEqual(stat.S_IMODE(after.st_mode), mode)
        self.assertEqual(self.deploy_lock.read_bytes(), raw)

    def _load_derived(self, _paths: object, _now: object) -> object:
        return self.fixture._load_authority(self.binding)  # noqa: SLF001

    def _ensure_derived(self, *_args: object, **_kwargs: object) -> object:
        authority = self.fixture._load_authority(self.binding)  # noqa: SLF001
        self.assertIsNotNone(authority)
        return self.fixture._coordinate(authority)  # noqa: SLF001

    def _run(
        self,
        *,
        fault_hook: object | None = None,
        ensure_derived: object | None = None,
    ) -> cross.SourceIngestCrossFreezeResult:
        with (
            mock.patch.object(
                manifest, "_default_admission_cut_binding", return_value=self.binding
            ),
            mock.patch.object(
                source,
                "ingest_source",
                side_effect=AssertionError(
                    "cross-freeze adapter must not open the cut deploy lock"
                ),
            ),
        ):
            return cross._coordinate_source_ingest_cross_freeze(  # noqa: SLF001
                registry_root=self.fixture.manifest_fixture.registry_root,
                active_root=self.fixture.source_fixture.root,
                shadow_root=self.fixture.manifest_fixture.shadow_root,
                clock=lambda: NOW,
                fault_hook=fault_hook,
                ensure_derived=(ensure_derived or self._ensure_derived),
                load_derived_authority=self._load_derived,
            )

    def _current(self) -> source.CanonicalSource:
        return source.load_current_source(
            self.fixture.source_fixture.profile,
            runtime_root=self.fixture.source_fixture.root,
        )

    def test_fresh_edge_uses_private_kernel_and_binds_derived_event(self) -> None:
        self._install_cut_sentinel()
        kernel = source._ingest_source_locked  # noqa: SLF001
        with mock.patch.object(
            source,
            "_ingest_source_locked",
            wraps=kernel,  # noqa: SLF001
        ) as writer:
            result = self._run()

        self.assertEqual(result.status, "source_ingest_cross_freeze_completed")
        self.assertEqual(writer.call_count, 1)
        self.assertEqual(result.snapshot_sequence_id, 2)
        self.assertEqual(result.derived_key_count, 2)
        self.assertEqual(result.rebound_item_count, 0)
        self.assertEqual(result.invalidated_item_count, 0)
        self.assertEqual(self._current().snapshot_sequence_id, 2)
        self.assertIsNotNone(result.prepare_path)
        self.assertIsNotNone(result.intent_path)
        self.assertIsNotNone(result.receipt_path)
        self.assertIsNotNone(result.event_path)
        receipt = json.loads(result.receipt_path.read_bytes())
        event = json.loads(result.event_path.read_bytes())
        for payload in (receipt, event):
            self.assertTrue(payload["source_snapshot_ingested"])
            self.assertTrue(payload["derived_batch_classified"])
            self.assertFalse(payload["terminal_for_recovery_v6_key"])
            self.assertFalse(payload["outcome_materialization_performed"])
            self.assertFalse(payload["legacy_deploy_writer_reactivated"])
            self.assertFalse(payload["formal_warning_output"])
        self._assert_cut_sentinel_unchanged()

    def test_intent_crash_reuses_prepared_feed_after_mutable_inbox_changes(
        self,
    ) -> None:
        self._install_cut_sentinel()

        def crash(stage: str) -> None:
            if stage == "after_intent":
                raise RuntimeError("synthetic intent-only crash")

        with self.assertRaisesRegex(RuntimeError, "intent-only"):
            self._run(fault_hook=crash)
        self.assertEqual(self._current().snapshot_sequence_id, 1)

        self.fixture.source_fixture.write(self.fixture.source_fixture.payload(count=4))
        result = self._run()

        self.assertEqual(result.status, "source_ingest_cross_freeze_completed")
        current = self._current()
        self.assertEqual(current.snapshot_sequence_id, 2)
        self.assertEqual(current.watermark.isoformat(), "2020-07-03")
        self._assert_cut_sentinel_unchanged()

    def test_receipt_first_pointer_crash_recovers_without_second_source_write(
        self,
    ) -> None:
        self._install_cut_sentinel()
        atomic_write = source._atomic_write  # noqa: SLF001

        def crash_before_public_pointer(path: Path, raw: bytes) -> None:
            if path == self.fixture.source_fixture.pointer_path:
                raise OSError("synthetic crash before public pointer")
            atomic_write(path, raw)

        with (
            mock.patch.object(
                source, "_atomic_write", side_effect=crash_before_public_pointer
            ),
            self.assertRaisesRegex(RuntimeError, "public pointer|materialization"),
        ):
            self._run()

        registry_state = source._load_snapshot_receipt_registry(  # noqa: SLF001
            self.fixture.source_fixture.objects_path, required=True
        )
        self.assertEqual(registry_state.head_payload["snapshot_sequence_id"], 2)

        with mock.patch.object(
            source,
            "_ingest_source_locked",
            side_effect=AssertionError("committed source action must not repeat"),
        ):
            result = self._run()

        self.assertEqual(result.status, "source_ingest_cross_freeze_completed")
        self.assertEqual(self._current().snapshot_sequence_id, 2)
        self._assert_cut_sentinel_unchanged()

    def test_pointer_recovery_requires_exact_durable_adapter_intent(self) -> None:
        self._install_cut_sentinel()
        pointer_before = self.fixture.source_fixture.pointer_path.read_bytes()
        atomic_write = source._atomic_write  # noqa: SLF001

        def crash_before_public_pointer(path: Path, raw: bytes) -> None:
            if path == self.fixture.source_fixture.pointer_path:
                raise OSError("synthetic external receipt-before-pointer crash")
            atomic_write(path, raw)

        with (
            mock.patch.object(
                source, "_atomic_write", side_effect=crash_before_public_pointer
            ),
            self.assertRaisesRegex(RuntimeError, "public pointer|materialization"),
        ):
            source._ingest_source_locked(  # noqa: SLF001
                self.fixture.source_fixture.profile,
                runtime_root=self.fixture.source_fixture.root,
                now=NOW,
            )

        with self.assertRaisesRegex(
            cross.SourceIngestCrossFreezeIntegrityError,
            "chain tip|Current source replay failed",
        ):
            self._run()

        self.assertEqual(
            self.fixture.source_fixture.pointer_path.read_bytes(), pointer_before
        )
        registry_state = source._load_snapshot_receipt_registry(  # noqa: SLF001
            self.fixture.source_fixture.objects_path, required=True
        )
        self.assertEqual(registry_state.head_payload["snapshot_sequence_id"], 2)
        self._assert_cut_sentinel_unchanged()

    def test_intent_does_not_recover_pointer_to_mismatched_n_plus_one(self) -> None:
        self._install_cut_sentinel()

        def crash_after_intent(stage: str) -> None:
            if stage == "after_intent":
                raise RuntimeError("synthetic durable-intent crash")

        with self.assertRaisesRegex(RuntimeError, "durable-intent"):
            self._run(fault_hook=crash_after_intent)

        pointer_before = self.fixture.source_fixture.pointer_path.read_bytes()
        self.fixture.source_fixture.write(self.fixture.source_fixture.payload(count=4))
        atomic_write = source._atomic_write  # noqa: SLF001

        def crash_before_wrong_public_pointer(path: Path, raw: bytes) -> None:
            if path == self.fixture.source_fixture.pointer_path:
                raise OSError("synthetic wrong-child pointer crash")
            atomic_write(path, raw)

        with (
            mock.patch.object(
                source,
                "_atomic_write",
                side_effect=crash_before_wrong_public_pointer,
            ),
            self.assertRaisesRegex(RuntimeError, "public pointer|materialization"),
        ):
            source._ingest_source_locked(  # noqa: SLF001
                self.fixture.source_fixture.profile,
                runtime_root=self.fixture.source_fixture.root,
                now=NOW,
            )

        with self.assertRaisesRegex(
            cross.SourceIngestCrossFreezeIntegrityError,
            "does not match the durable frozen-feed intent",
        ):
            self._run()

        self.assertEqual(
            self.fixture.source_fixture.pointer_path.read_bytes(), pointer_before
        )
        self._assert_cut_sentinel_unchanged()

    def test_receipt_only_crash_appends_event_without_reensuring_or_rewriting(
        self,
    ) -> None:
        self._install_cut_sentinel()

        def crash(stage: str) -> None:
            if stage == "after_receipt":
                raise RuntimeError("synthetic receipt-only crash")

        with self.assertRaisesRegex(RuntimeError, "receipt-only"):
            self._run(fault_hook=crash)

        with mock.patch.object(
            source,
            "_ingest_source_locked",
            side_effect=AssertionError("receipt adoption must not rewrite source"),
        ):
            result = self._run(
                ensure_derived=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    AssertionError("receipt adoption must not re-ensure derived")
                )
            )

        self.assertEqual(
            result.status, "source_ingest_cross_freeze_event_forward_adopted"
        )
        paths = cross.source_ingest_cross_freeze_paths(
            cross.load_source_ingest_cross_freeze_profile(),
            registry_root=self.fixture.manifest_fixture.registry_root,
            active_root=self.fixture.source_fixture.root,
            shadow_root=self.fixture.manifest_fixture.shadow_root,
        )
        self.assertEqual(len(tuple(paths.receipts.glob("*.json"))), 1)
        self.assertEqual(len(tuple(paths.events.glob("*.json"))), 1)
        self._assert_cut_sentinel_unchanged()

    def test_committed_source_waiting_for_derived_event_reports_source_truth(
        self,
    ) -> None:
        result = self._run(ensure_derived=lambda *_args, **_kwargs: None)

        self.assertEqual(result.status, "waiting_for_derived_batch_classification")
        self.assertEqual(result.snapshot_sequence_id, 2)
        self.assertTrue(result.source_snapshot_ingested)
        self.assertFalse(result.derived_batch_classified)
        status = json.loads(result.status_path.read_bytes())
        self.assertEqual(status["snapshot_sequence_id"], 2)
        self.assertTrue(status["source_snapshot_ingested"])
        self.assertFalse(status["derived_batch_classified"])

    def test_derived_lock_contention_remains_machine_retryable(self) -> None:
        def busy(*_args: object, **_kwargs: object) -> None:
            raise derived.SourceIngestDerivedBusyError("synthetic derived lock busy")

        with self.assertRaisesRegex(
            cross.SourceIngestCrossFreezeBusyError, "derived lock busy"
        ):
            self._run(ensure_derived=busy)

    def test_seq3_adopts_historical_seq2_without_rollback_or_writer_call(self) -> None:
        seq2 = self.fixture._publish_seq2()  # noqa: SLF001
        self.fixture.source_fixture.write(self.fixture.source_fixture.payload(count=4))
        seq3_result = source.ingest_source(
            self.fixture.source_fixture.profile,
            runtime_root=self.fixture.source_fixture.root,
            now=NOW,
        )
        self.assertIsNotNone(seq3_result.source)
        self.assertEqual(seq3_result.source.snapshot_sequence_id, 3)
        self._install_cut_sentinel()

        with mock.patch.object(
            source,
            "_ingest_source_locked",
            side_effect=AssertionError("historical child adoption must not write"),
        ):
            result = self._run()

        self.assertEqual(result.status, "source_ingest_cross_freeze_completed")
        self.assertEqual(result.snapshot_sequence_id, seq2.snapshot_sequence_id)
        self.assertEqual(self._current().snapshot_sequence_id, 3)
        self._assert_cut_sentinel_unchanged()


if __name__ == "__main__":
    unittest.main()
