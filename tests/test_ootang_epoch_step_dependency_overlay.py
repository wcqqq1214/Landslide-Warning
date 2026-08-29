"""Fast contracts for the isolated step-dependency settlement overlay."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_epoch_registry as registry  # noqa: E402
from monitoring import ootang_epoch_step_dependency_overlay as overlay  # noqa: E402
from monitoring import ootang_epoch_step_dependency_reservation as sidecar  # noqa: E402
from monitoring import ootang_epoch_workset_recovery as recovery  # noqa: E402


NOW = datetime(2031, 2, 3, 4, 5, tzinfo=timezone.utc)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


class EpochStepDependencyOverlayTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="ootang-step-overlay-", dir=ROOT)
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.registry_root = self.base / "registry"
        self.active_root = self.base / "active"
        self.shadow_root = self.base / "shadow"
        for path in (self.registry_root, self.active_root, self.shadow_root):
            path.mkdir(parents=True)
        self.authority, self.upstream_paths = self._authority()
        self.source_item = self.authority.recovery.ordered_items[0]
        self.dependency_item = self.authority.recovery.ordered_items[1]

    @staticmethod
    def _snapshot(path: Path, payload: object) -> registry.ArtifactSnapshot:
        raw = (
            json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return registry.ArtifactSnapshot(
            path=path,
            raw=raw,
            sha256=hashlib.sha256(raw).hexdigest(),
            size_bytes=len(raw),
        )

    def _authority(self) -> tuple[overlay.OverlayAuthority, tuple[Path, ...]]:
        recovery_root = self.registry_root / "workset_recovery_v1"
        manifest_root = self.registry_root / "workset_manifest_v1"
        source_key = _digest("overlay-source-key")
        dependency_key = _digest("overlay-dependency-key")
        source_step = _digest("overlay-source-step")
        dependency_step = _digest("overlay-dependency-step")
        scope = {
            "old_live_epoch_id": "old-live-epoch",
            "target_date": "2031-02-04",
            "issue_id": "issue-2031-02-04",
            "sealed_entry_sha256": _digest("overlay-seal"),
        }
        source_item = {
            "key_id": source_key,
            "family": "live_outstanding",
            "natural_key": "live-outstanding:2031-02-04",
            "canonical_successor_state": "anchor_result_recorded",
            "dependency_keys": [],
            "namespace_digest": _digest("overlay-source-namespace"),
            "artifacts": [],
            "authority": {
                "old_live_epoch_id": scope["old_live_epoch_id"],
                "target_date": scope["target_date"],
                "issue_id": scope["issue_id"],
                "seal_event": {"entry_sha256": scope["sealed_entry_sha256"]},
            },
        }
        dependency_item = {
            "key_id": dependency_key,
            "family": "outcome_revision",
            "natural_key": "outcome-revision:2031-02-04",
            "canonical_successor_state": "outcome_or_revision_consumed",
            "dependency_keys": [],
            "namespace_digest": _digest("overlay-dependency-namespace"),
            "artifacts": [],
            "authority": {"target_date": scope["target_date"]},
        }
        confirmation = {
            "sequence_id": 4,
            "event_key": "old-live-epoch:2031-02-04:anchor:1:confirmed",
            "entry_sha256": _digest("overlay-confirmation-entry"),
        }
        terminal_event = {
            "sequence_id": 47,
            "event_key": "old-live-epoch:2031-02-04:outcome:settled",
            "entry_sha256": _digest("overlay-settlement-entry"),
        }
        source_receipt = {
            "step_id": source_step,
            "step_index": 0,
            "key_id": source_key,
            "action": "anchor_result_recorded",
            "next_actions": ["outcome_batch_settled"],
            "terminal_for_key": False,
            "action_output_kind": "live_anchor_result_event",
            "action_output": None,
            "action_semantics": {
                "schema_version": "ootang_live_anchor_result_action_output_v1",
                "result_outcome": "candidate_confirmed",
                "event_type": "anchor_confirmed",
                "selected_next_action": "outcome_batch_settled",
                "live_ledger_event_recorded": True,
                "trusted_anchor_receipt_verified": False,
                "e2_live_evidence_eligible": False,
                "live_epoch_id": scope["old_live_epoch_id"],
                "sealed_entry_sha256": scope["sealed_entry_sha256"],
                **confirmation,
                "previous_entry_sha256": _digest("overlay-confirmation-previous"),
            },
        }
        dependency_receipt = {
            "step_id": dependency_step,
            "step_index": 0,
            "key_id": dependency_key,
            "action": "outcome_or_revision_consumed",
            "next_actions": [],
            "terminal_for_key": True,
            "action_output_kind": "live_outcome_consumption_transaction",
            "action_output": None,
            "action_semantics": {
                "schema_version": "ootang_live_outcome_consumption_action_output_v1",
                "writer_branch": "outstanding_settlement",
                "live_ledger_events_recorded": True,
                "canonical_frozen_writer_reused": True,
                "trusted_anchor_receipt_verified": False,
                "e2_live_evidence_eligible": False,
                "formal_warning_output": False,
                "live_epoch_id": scope["old_live_epoch_id"],
                "target_date": scope["target_date"],
                "issue_id": scope["issue_id"],
                "sealed_entry_sha256": scope["sealed_entry_sha256"],
                "source_revision_id": "source-revision-1",
                "exact_outcome_sha256": _digest("overlay-exact-outcome"),
                "outcome_source_id": "synthetic-outcome-source",
                "terminal_event": terminal_event,
            },
        }
        manifest_payload = {
            "workset_keyset_sha256": _digest("overlay-workset"),
            "items": [source_item, dependency_item],
        }
        manifest_snapshot = self._snapshot(
            manifest_root / "manifests/frozen.json", manifest_payload
        )
        manifest_event_snapshot = self._snapshot(
            manifest_root / "events/0001.json", {"reservation": True}
        )
        global_snapshot = self._snapshot(
            recovery_root / "intent.json", {"intent": "frozen-v6"}
        )
        source_snapshot = self._snapshot(
            recovery_root / f"receipts/{source_step}.json", source_receipt
        )
        dependency_snapshot = self._snapshot(
            recovery_root / f"receipts/{dependency_step}.json", dependency_receipt
        )
        source_event_snapshot = self._snapshot(
            recovery_root / "events/0001.json",
            {"step_id": source_step, "key_id": source_key},
        )
        dependency_event_snapshot = self._snapshot(
            recovery_root / "events/0002.json",
            {"step_id": dependency_step, "key_id": dependency_key},
        )
        reservation = recovery.Reservation(
            profile={},
            paths=SimpleNamespace(root=manifest_root),  # type: ignore[arg-type]
            binding=mock.sentinel.binding,
            event={"reservation": True},
            event_snapshot=manifest_event_snapshot,
            manifest=manifest_payload,
            manifest_snapshot=manifest_snapshot,
        )
        recovered = sidecar.StepDependencyAuthority(
            recovery_profile={
                "profile_id": "synthetic-recovery-v6",
                "_profile_sha256": _digest("overlay-recovery-profile"),
            },
            reservation=reservation,
            global_intent={"intent": "frozen-v6"},
            global_snapshot=global_snapshot,
            ordered_items=(source_item, dependency_item),
            chains={
                source_key: [(source_receipt, source_snapshot)],
                dependency_key: [(dependency_receipt, dependency_snapshot)],
            },
            events_by_step={
                source_step: (
                    {"step_id": source_step, "key_id": source_key},
                    source_event_snapshot,
                ),
                dependency_step: (
                    {"step_id": dependency_step, "key_id": dependency_key},
                    dependency_event_snapshot,
                ),
            },
        )
        candidate = sidecar._candidate_from_pair(  # noqa: SLF001
            recovered,
            source_item,
            (source_receipt, source_snapshot),
            dependency_item,
            (dependency_receipt, dependency_snapshot),
        )
        sidecar_profile = sidecar.load_step_dependency_profile()
        sidecar_paths = sidecar.step_dependency_paths(
            sidecar_profile,
            registry_root=self.registry_root,
            active_root=self.active_root,
            shadow_root=self.shadow_root,
        )
        reservation_payload, sidecar_reservation_snapshot = (
            sidecar._publish_reservation(  # noqa: SLF001
                sidecar_profile, sidecar_paths, recovered, candidate, now=NOW
            )
        )
        sidecar_event, sidecar_event_snapshot = sidecar._append_event(  # noqa: SLF001
            sidecar_profile,
            sidecar_paths,
            candidate,
            sidecar_reservation_snapshot,
            sequence_id=1,
            previous=sidecar.ZERO_HASH,
            now=NOW,
        )
        authority = overlay.OverlayAuthority(
            recovery=recovered,
            sidecar_profile=sidecar_profile,
            sidecar_objects={
                candidate.slot_id: (
                    reservation_payload,
                    sidecar_reservation_snapshot,
                    candidate,
                )
            },
            sidecar_events=((sidecar_event, sidecar_event_snapshot),),
            orphaned_sidecar_slot_ids=frozenset(),
        )
        upstream_paths = (
            ROOT / "config/ootang_epoch_workset_recovery.v1.json",
            ROOT / "code/monitoring/ootang_epoch_workset_recovery.py",
            ROOT / "config/ootang_epoch_step_dependency_reservation.v1.json",
            ROOT / "code/monitoring/ootang_epoch_step_dependency_reservation.py",
            manifest_snapshot.path,
            manifest_event_snapshot.path,
            global_snapshot.path,
            source_snapshot.path,
            dependency_snapshot.path,
            source_event_snapshot.path,
            dependency_event_snapshot.path,
            sidecar_reservation_snapshot.path,
            sidecar_event_snapshot.path,
        )
        return authority, upstream_paths

    def _coordinate(self) -> overlay.OverlayResult:
        return overlay._coordinate_epoch_step_dependency_overlay(  # noqa: SLF001
            registry_root=self.registry_root,
            active_root=self.active_root,
            shadow_root=self.shadow_root,
            clock=lambda: NOW,
            load_authority=lambda _paths, _now: self.authority,
        )

    def _contract(self, item: object, reservation: object, previous: object) -> dict:
        slot = next(iter(self.authority.sidecar_objects.values()))
        reservation_payload = slot[0]
        source_record = reservation_payload["source_step"]
        dependency_record = reservation_payload["dependency"]
        self.assertIs(reservation, self.authority.recovery.reservation)
        self.assertIs(previous, slot[2].source_receipt)
        self.assertIsNot(item, self.source_item)
        self.assertEqual(self.source_item["dependency_keys"], [])
        self.assertEqual(
            item["dependency_keys"],  # type: ignore[index]
            [dependency_record["natural_key"]],
        )
        return {
            "expected_pre_head": {"epoch_id": "old-live-epoch"},
            "target_date": dependency_record["target_date"],
            "issue_id": dependency_record["issue_id"],
            "sealed_entry_sha256": dependency_record["sealed_entry_sha256"],
            "confirmation_event": source_record["confirmation_event"],
            "outcome_dependency": {
                "natural_key": dependency_record["natural_key"],
                "namespace_digest": dependency_record["namespace_digest"],
                "record_type": "outcome_receipt_chain",
                "source_revision_id": dependency_record["source_revision_id"],
                "outcome_batch_sha256": dependency_record["exact_outcome_sha256"],
                "outcome_source_id": None,
                "target_date": dependency_record["target_date"],
                "old_live_epoch_id": dependency_record["old_live_epoch_id"],
            },
            "schema_version": recovery.OUTCOME_SETTLEMENT_ADOPTION_CONTRACT_SCHEMA,
            "source_revision_id": dependency_record["source_revision_id"],
            "outcome_batch_sha256": dependency_record["exact_outcome_sha256"],
            "outcome_source_id": dependency_record["outcome_source_id"],
            "terminal_event": dependency_record["ledger_terminal_event"],
        }

    @staticmethod
    def _action_from_contract(
        _item: object, _reservation: object, contract: object, _previous: object
    ) -> recovery.ActionOutput:
        return overlay._recorded_action_for_contract(  # noqa: SLF001
            {"recovery_contract": contract}
        )

    def _patched_contract_and_action(self):
        return (
            mock.patch.object(
                recovery,
                "_outcome_settlement_adoption_contract",
                side_effect=self._contract,
            ),
            mock.patch.object(
                recovery,
                "_outcome_settlement_adoption_action",
                side_effect=self._action_from_contract,
            ),
        )

    def test_completed_sidecar_event_is_consumed_without_upstream_mutation(
        self,
    ) -> None:
        before = {path: path.read_bytes() for path in self.upstream_paths}
        contract_patch, action_patch = self._patched_contract_and_action()
        with contract_patch as contract_mock, action_patch as action_mock:
            result = self._coordinate()

        self.assertEqual(result.status, "overlay_slot_completed")
        self.assertIsNotNone(result.intent_path)
        self.assertIsNotNone(result.receipt_path)
        self.assertIsNotNone(result.event_path)
        intent = json.loads(result.intent_path.read_bytes())  # type: ignore[union-attr]
        receipt = json.loads(result.receipt_path.read_bytes())  # type: ignore[union-attr]
        event = json.loads(result.event_path.read_bytes())  # type: ignore[union-attr]
        status = json.loads(result.status_path.read_bytes())
        sidecar_payload, sidecar_snapshot = self.authority.sidecar_events[0]
        reservation_snapshot = next(iter(self.authority.sidecar_objects.values()))[1]
        dependency_natural_key = self.dependency_item["natural_key"]
        self.assertEqual(
            intent["action_contract"]["effective_dependency_keys"],
            [dependency_natural_key],
        )
        self.assertEqual(self.source_item["dependency_keys"], [])
        self.assertEqual(intent["sidecar_event"]["sha256"], sidecar_snapshot.sha256)
        self.assertEqual(
            intent["sidecar_reservation"]["sha256"], reservation_snapshot.sha256
        )
        self.assertEqual(receipt["sidecar_event"], intent["sidecar_event"])
        self.assertEqual(receipt["sidecar_reservation"], intent["sidecar_reservation"])
        self.assertEqual(event["slot_id"], sidecar_payload["slot_id"])
        self.assertEqual(
            event["receipt"]["sha256"],
            hashlib.sha256(
                result.receipt_path.read_bytes()  # type: ignore[union-attr]
            ).hexdigest(),
        )
        self.assertTrue(receipt["terminal_for_overlay_slot"])
        self.assertNotIn("terminal_for_key", receipt)
        for payload in (intent, receipt, event, status):
            for claim in overlay.FALSE_CLAIMS:
                self.assertFalse(payload[claim], claim)
        self.assertFalse(result.original_recovery_key_terminal)
        self.assertFalse(result.terminal_transition_closure_implemented)
        self.assertEqual(
            {path: path.read_bytes() for path in self.upstream_paths}, before
        )
        self.assertGreaterEqual(contract_mock.call_count, 2)
        action_mock.assert_called_once()

    def test_intent_before_receipt_crash_replays_exact_intent(self) -> None:
        contract_patch, action_patch = self._patched_contract_and_action()
        with (
            contract_patch,
            action_patch,
            mock.patch.object(
                overlay,
                "_ensure_receipt",
                side_effect=RuntimeError("synthetic crash after intent"),
            ),
            self.assertRaisesRegex(RuntimeError, "synthetic crash after intent"),
        ):
            self._coordinate()

        profile = overlay.load_overlay_profile()
        paths = overlay.overlay_paths(
            profile,
            registry_root=self.registry_root,
            active_root=self.active_root,
            shadow_root=self.shadow_root,
        )
        intents = tuple(paths.intents.glob("*.json"))
        self.assertEqual(len(intents), 1)
        intent_before = intents[0].read_bytes()
        self.assertFalse(paths.receipts.exists())
        self.assertFalse(paths.events.exists())

        contract_patch, action_patch = self._patched_contract_and_action()
        with contract_patch, action_patch:
            result = self._coordinate()

        self.assertEqual(result.status, "overlay_slot_completed")
        self.assertEqual(intents[0].read_bytes(), intent_before)
        self.assertEqual(len(tuple(paths.intents.glob("*.json"))), 1)
        self.assertEqual(len(tuple(paths.receipts.glob("*.json"))), 1)
        self.assertEqual(len(tuple(paths.events.glob("*.json"))), 1)

    def test_receipt_before_event_crash_only_forward_adopts_event(self) -> None:
        contract_patch, action_patch = self._patched_contract_and_action()
        with (
            contract_patch,
            action_patch,
            mock.patch.object(
                overlay,
                "_append_event",
                side_effect=RuntimeError("synthetic crash after receipt"),
            ),
            self.assertRaisesRegex(RuntimeError, "synthetic crash after receipt"),
        ):
            self._coordinate()

        profile = overlay.load_overlay_profile()
        paths = overlay.overlay_paths(
            profile,
            registry_root=self.registry_root,
            active_root=self.active_root,
            shadow_root=self.shadow_root,
        )
        intents = tuple(paths.intents.glob("*.json"))
        receipts = tuple(paths.receipts.glob("*.json"))
        self.assertEqual((len(intents), len(receipts)), (1, 1))
        intent_before = intents[0].read_bytes()
        receipt_before = receipts[0].read_bytes()
        self.assertFalse(paths.events.exists())

        with (
            mock.patch.object(
                recovery,
                "_outcome_settlement_adoption_contract",
                side_effect=self._contract,
            ),
            mock.patch.object(
                overlay,
                "_action_for_contract",
                side_effect=AssertionError(
                    "receipt-to-event adoption must not repeat the action"
                ),
            ),
        ):
            result = self._coordinate()

        self.assertEqual(result.status, "overlay_event_forward_adopted")
        self.assertEqual(intents[0].read_bytes(), intent_before)
        self.assertEqual(receipts[0].read_bytes(), receipt_before)
        self.assertEqual(len(tuple(paths.events.glob("*.json"))), 1)


if __name__ == "__main__":
    unittest.main()
