"""Fast contracts for the one-event official scheduler transition."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_epoch_active_transition as transition  # noqa: E402
from monitoring import ootang_epoch_registry as registry  # noqa: E402
from monitoring import ootang_epoch_workset_manifest as manifest  # noqa: E402


NOW = datetime(2031, 2, 3, 4, 5, tzinfo=timezone.utc)


class ActiveTransitionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(
            prefix="ootang-active-transition-", dir=ROOT
        )
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.registry_root = self.base / "registry"
        self.old_active_root = self.base / "old-active"
        self.old_shadow_root = self.base / "old-shadow"
        for root in (
            self.registry_root,
            self.old_active_root,
            self.old_shadow_root,
        ):
            root.mkdir(parents=True)
        self.candidate_id = "candidate-a"
        self.slot_id = "b" * 64
        self.old_epoch_id = "old-epoch-a"
        self.new_epoch_id = "new-epoch-a"
        self.adapter_sha256 = "a" * 64

    @staticmethod
    def _snapshot(path: Path) -> registry.ArtifactSnapshot:
        raw = path.read_bytes()
        return registry.ArtifactSnapshot(
            path=path,
            raw=raw,
            sha256=hashlib.sha256(raw).hexdigest(),
            size_bytes=len(raw),
        )

    def _paths(self) -> transition.ActiveTransitionPaths:
        return transition.active_transition_paths(
            runtime_root=self.registry_root,
            active_runtime_root=self.old_active_root,
            shadow_runtime_root=self.old_shadow_root,
        )

    def _completion(self) -> transition._CompletionEvidence:  # noqa: SLF001
        paths = self._paths()
        static = {
            "schema_version": transition.completion.EVENT_SCHEMA_VERSION,
            "sequence_id": 1,
            "previous_entry_sha256": transition.ZERO_HASH,
            "event_type": transition.completion.EVENT_TYPE,
            "branch": "v2_non_clean_recovery",
            "authority_scope": "official_machine_reserved_workset",
            "candidate_id": self.candidate_id,
            "slot_id": self.slot_id,
            "old_live_epoch_id": self.old_epoch_id,
            "frozen_live_event_count": 7,
            "frozen_live_terminal_sha256": "7" * 64,
            "fresh_live_event_count": 9,
            "fresh_live_terminal_sha256": "9" * 64,
            "admission_cut_event": {"synthetic": True},
            "admission_cut_latest_attempt": {"synthetic": True},
            "source_derived_bounded_terminal_closure_proof": {"synthetic": True},
            "source_derived_bounded_terminal_closure_event": {"synthetic": True},
            "fresh_six_family_capture": [
                {
                    "family": family,
                    "record_count": 0,
                    "actionable_count": 0,
                    "namespace_digest": "0" * 64,
                }
                for family in manifest.FAMILIES
            ],
            "fresh_actionable_item_count": 0,
            "fresh_context_extends_frozen_cut": True,
            "admission_cut_both_cut_verified": True,
            "bounded_source_derived_terminal_closure_verified": True,
            "machine_only": True,
            "bounded_official_workset_drained": True,
            "old_work_admission_fence_implemented": False,
            "old_epoch_drained": False,
            "canonical_old_issue_route_fence_implemented": False,
            "direct_filesystem_writer_fence_implemented": False,
            "active_epoch_switch_implemented": False,
            "lifecycle_authority": False,
            "transition_authority": False,
            "recorded_at_utc": transition._utc_text(NOW),  # noqa: SLF001
        }
        payload = {
            **static,
            "entry_sha256": transition._sha256(  # noqa: SLF001
                registry._canonical_bytes(static)  # noqa: SLF001
            ),
        }
        path = paths.completion.events / f"{1:020d}-{payload['entry_sha256']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(registry._canonical_bytes(payload))  # noqa: SLF001
        return transition._CompletionEvidence(  # noqa: SLF001
            payload=payload,
            snapshot=self._snapshot(path),
            registry_event_sequence_id=4,
            registry_event_entry_sha256="4" * 64,
            preparation_event_sequence_id=6,
            preparation_event_entry_sha256="6" * 64,
        )

    def _authority(
        self, *, candidate_id: str | None = None
    ) -> transition._PreparedAuthority:  # noqa: SLF001
        candidate = candidate_id or self.candidate_id
        runtime_root = self.registry_root / "slots" / self.slot_id / "live"
        tree = self.registry_root / "executable_trees" / ("e" * 64)
        script = tree / transition.CYCLE_SCRIPT_LOGICAL_PATH
        config = tree / transition.CYCLE_CONFIG_LOGICAL_PATH
        script.parent.mkdir(parents=True, exist_ok=True)
        config.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("# synthetic cycle-v3\n", encoding="utf-8")
        config.write_text("{}\n", encoding="utf-8")
        return transition._PreparedAuthority(  # noqa: SLF001
            registry_event={
                "sequence_id": 4,
                "entry_sha256": "4" * 64,
                "candidate_id": candidate,
                "slot_id": self.slot_id,
            },
            preparation_event={
                "sequence_id": 6,
                "entry_sha256": "6" * 64,
                "registry_event_sequence_id": 4,
                "registry_event_entry_sha256": "4" * 64,
                "candidate_id": candidate,
                "slot_id": self.slot_id,
            },
            candidate_receipt={
                "candidate_id": candidate,
                "slot_id": self.slot_id,
                "slot_live_root": str(runtime_root),
                "candidate_build": {"live_epoch_id": self.new_epoch_id},
            },
            executable_capsule={
                "candidate_id": candidate,
                "slot_id": self.slot_id,
                "slot_live_root": str(runtime_root),
                "candidate_live_epoch_id": self.new_epoch_id,
                "tree_sha256": "e" * 64,
            },
            executable_tree_root=tree,
            cycle_script=self._snapshot(script),
            cycle_config=self._snapshot(config),
        )

    def _run(self, **kwargs: object) -> transition.ActiveTransitionResult:
        handle = object()
        coverage = transition.completion.closure.current.coverage
        with (
            mock.patch.object(coverage, "_acquire_locks", return_value=(handle,)),
            mock.patch.object(coverage, "_release_locks") as release,
        ):
            result = transition._coordinate_epoch_active_transition(  # noqa: SLF001
                runtime_root=self.registry_root,
                active_runtime_root=self.old_active_root,
                shadow_runtime_root=self.old_shadow_root,
                **kwargs,
            )
        release.assert_called_once_with((handle,))
        return result

    def test_commit_is_byte_idempotent_rebuilds_cache_and_lease_replays(self) -> None:
        bounded = self._completion()
        authority = self._authority()

        def historical(
            _paths: transition.ActiveTransitionPaths,
            registry_hash: str,
            preparation_hash: str,
        ) -> transition._PreparedAuthority:  # noqa: SLF001
            self.assertEqual(registry_hash, "4" * 64)
            self.assertEqual(preparation_hash, "6" * 64)
            return authority

        first = self._run(
            clock=lambda: NOW,
            load_current_completion=lambda _paths, _now: bounded,
            load_current_authority=lambda _paths, _bounded: authority,
            load_historical_authority=historical,
            scheduler_adapter_sha256=self.adapter_sha256,
        )

        self.assertEqual(first.status, "active_transition_committed")
        self.assertTrue(first.active_epoch_switch_implemented)
        self.assertTrue(first.scheduler_entrypoint_authorization_implemented)
        self.assertIsNotNone(first.event_path)
        event_path = first.event_path
        assert event_path is not None
        event_raw = event_path.read_bytes()
        event = json.loads(event_raw)
        self.assertEqual(event["old_official_scheduler_state"], "SEALED")
        self.assertEqual(event["new_official_scheduler_state"], "ACTIVE")
        self.assertFalse(event["new_epoch_genesis_initialized"])
        for claim in transition.TRUE_CLAIMS:
            self.assertTrue(event[claim], claim)
        for claim in transition.FALSE_CLAIMS:
            self.assertFalse(event[claim], claim)

        first.status_path.unlink()
        new_ledger = Path(event["runtime_root"]) / "ledger.sqlite3"
        new_ledger.parent.mkdir(parents=True, exist_ok=True)
        new_ledger.write_bytes(b"initialized-after-active")
        replay = self._run(
            clock=lambda: NOW + timedelta(minutes=1),
            load_current_completion=lambda _paths, _now: self.fail(
                "repoll must not rescan current completion"
            ),
            load_current_authority=lambda _paths, _bounded: self.fail(
                "repoll must not require current-empty candidate"
            ),
            load_historical_authority=historical,
            scheduler_adapter_sha256=self.adapter_sha256,
        )

        self.assertEqual(replay.status, "active_transition_current")
        self.assertEqual(event_path.read_bytes(), event_raw)
        self.assertTrue(replay.status_path.exists())
        with transition._scheduler_authorization_lease(  # noqa: SLF001
            runtime_root=self.registry_root,
            active_runtime_root=self.old_active_root,
            shadow_runtime_root=self.old_shadow_root,
            load_historical_authority=historical,
            scheduler_adapter_sha256=self.adapter_sha256,
        ) as authorization:
            self.assertEqual(authorization.transition_entry_path, event_path)
            self.assertEqual(authorization.new_live_epoch_id, self.new_epoch_id)
            self.assertEqual(authorization.runtime_root, Path(event["runtime_root"]))
            self.assertEqual(authorization.cycle_script, authority.cycle_script.path)
        self.assertEqual(
            tuple(
                inspect.signature(
                    transition.coordinate_epoch_active_transition
                ).parameters
            ),
            (),
        )
        self.assertEqual(
            tuple(
                inspect.signature(transition.scheduler_authorization_lease).parameters
            ),
            (),
        )

    def test_missing_completion_waits_and_lease_reports_not_ready(self) -> None:
        result = self._run(
            clock=lambda: NOW,
            load_current_completion=lambda _paths, _now: None,
            load_current_authority=lambda _paths, _bounded: self.fail(
                "authority must not load before completion"
            ),
            scheduler_adapter_sha256=self.adapter_sha256,
        )

        self.assertEqual(result.status, "waiting_for_bounded_drain_completion")
        self.assertFalse(result.active_epoch_switch_implemented)
        self.assertEqual(tuple(self._paths().events.glob("*.json")), ())
        with self.assertRaises(transition.ActiveTransitionNotReadyError):
            with transition._scheduler_authorization_lease(  # noqa: SLF001
                runtime_root=self.registry_root,
                active_runtime_root=self.old_active_root,
                shadow_runtime_root=self.old_shadow_root,
                scheduler_adapter_sha256=self.adapter_sha256,
            ):
                self.fail("missing transition cannot authorize a scheduler")

    def test_candidate_binding_mismatch_fails_before_publication(self) -> None:
        bounded = self._completion()
        mismatched = self._authority(candidate_id="candidate-b")

        with self.assertRaisesRegex(
            transition.ActiveTransitionIntegrityError,
            "Completion and prepared candidate binding differ",
        ):
            self._run(
                clock=lambda: NOW,
                load_current_completion=lambda _paths, _now: bounded,
                load_current_authority=lambda _paths, _bounded: mismatched,
                scheduler_adapter_sha256=self.adapter_sha256,
            )
        self.assertEqual(tuple(self._paths().events.glob("*.json")), ())

    def test_busy_candidate_writer_blocks_transition(self) -> None:
        bounded = self._completion()
        candidate_paths = transition._candidate_drain_paths(  # noqa: SLF001
            self._paths(), bounded
        )
        handle = transition.drain._acquire_lock(  # noqa: SLF001
            candidate_paths.cycle_lock, label="test candidate cycle"
        )
        try:
            with self.assertRaises(transition.ActiveTransitionBusyError):
                self._run(
                    clock=lambda: NOW,
                    load_current_completion=lambda _paths, _now: bounded,
                    load_current_authority=lambda _paths, _bounded: self.fail(
                        "busy candidate lock must block before the empty-state check"
                    ),
                    scheduler_adapter_sha256=self.adapter_sha256,
                )
        finally:
            transition.drain._release_locks((handle,))  # noqa: SLF001
        self.assertEqual(tuple(self._paths().events.glob("*.json")), ())

    def test_old_locks_release_when_candidate_release_fails(self) -> None:
        bounded = self._completion()
        authority = self._authority()
        old_handle = object()
        coverage = transition.completion.closure.current.coverage

        def fail_candidate_release(handles) -> None:
            for handle in reversed(handles):
                handle.close()
            raise transition.drain.EpochDrainIntegrityError("candidate release failed")

        with (
            mock.patch.object(coverage, "_acquire_locks", return_value=(old_handle,)),
            mock.patch.object(coverage, "_release_locks") as release_old,
            mock.patch.object(
                transition.drain,
                "_release_locks",
                side_effect=fail_candidate_release,
            ),
            self.assertRaisesRegex(
                transition.ActiveTransitionIntegrityError,
                "candidate release failed",
            ),
        ):
            transition._coordinate_epoch_active_transition(  # noqa: SLF001
                runtime_root=self.registry_root,
                active_runtime_root=self.old_active_root,
                shadow_runtime_root=self.old_shadow_root,
                clock=lambda: NOW,
                load_current_completion=lambda _paths, _now: bounded,
                load_current_authority=lambda _paths, _bounded: authority,
                scheduler_adapter_sha256=self.adapter_sha256,
            )

        release_old.assert_called_once_with((old_handle,))


if __name__ == "__main__":
    unittest.main()
