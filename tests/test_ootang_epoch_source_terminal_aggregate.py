"""Fast contracts for overlay-backed source-key terminal aggregation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_epoch_source_terminal_aggregate as aggregate  # noqa: E402
from monitoring import ootang_epoch_step_dependency_overlay as overlay  # noqa: E402
from monitoring import ootang_epoch_workset_recovery as recovery  # noqa: E402
from tests import test_ootang_epoch_step_dependency_overlay as overlay_test  # noqa: E402


class EpochSourceTerminalAggregateTests(unittest.TestCase):
    def setUp(self) -> None:
        # Compose the reviewed overlay fixture without inheriting its three tests.
        self.overlay_fixture = overlay_test.EpochStepDependencyOverlayTests(
            "test_completed_sidecar_event_is_consumed_without_upstream_mutation"
        )
        self.overlay_fixture.setUp()
        self.addCleanup(self.overlay_fixture.doCleanups)

    def _settlement_contract(
        self, item: object, reservation: object, previous: object
    ) -> dict:
        contract = self.overlay_fixture._contract(item, reservation, previous)  # noqa: SLF001
        dependency = next(
            iter(self.overlay_fixture.authority.sidecar_objects.values())
        )[0]["dependency"]
        contract.update(
            {
                "event_count": recovery.OUTCOME_SETTLEMENT_EVENT_COUNT,
                "source_revision_id": dependency["source_revision_id"],
                "outcome_batch_sha256": dependency["exact_outcome_sha256"],
                "outcome_source_id": dependency["outcome_source_id"],
                "terminal_event": dependency["ledger_terminal_event"],
            }
        )
        return contract

    def _overlay_patches(self):
        return (
            mock.patch.object(
                recovery,
                "_outcome_settlement_adoption_contract",
                side_effect=self._settlement_contract,
            ),
            mock.patch.object(
                recovery,
                "_outcome_settlement_adoption_action",
                side_effect=self.overlay_fixture._action_from_contract,  # noqa: SLF001
            ),
        )

    def _complete_overlay(self) -> overlay.OverlayResult:
        contract_patch, action_patch = self._overlay_patches()
        with contract_patch, action_patch:
            return self.overlay_fixture._coordinate()  # noqa: SLF001

    def _aggregate_authority(
        self,
    ) -> tuple[
        aggregate.SourceTerminalAggregatePaths,
        aggregate.SourceTerminalAggregateAuthority,
    ]:
        profile = aggregate.load_source_terminal_profile()
        paths = aggregate.source_terminal_paths(
            profile,
            registry_root=self.overlay_fixture.registry_root,
            active_root=self.overlay_fixture.active_root,
            shadow_root=self.overlay_fixture.shadow_root,
        )
        with mock.patch.object(
            overlay,
            "_load_overlay_authority",
            return_value=self.overlay_fixture.authority,
        ):
            contract_patch, _ = self._overlay_patches()
            with contract_patch:
                authority = aggregate._load_terminal_authority(  # noqa: SLF001
                    paths, overlay_test.NOW
                )
        self.assertIsNotNone(authority)
        return paths, authority  # type: ignore[return-value]

    def _coordinate(
        self, authority: aggregate.SourceTerminalAggregateAuthority
    ) -> aggregate.SourceTerminalAggregateResult:
        return aggregate._coordinate_epoch_source_terminal_aggregate(  # noqa: SLF001
            registry_root=self.overlay_fixture.registry_root,
            active_root=self.overlay_fixture.active_root,
            shadow_root=self.overlay_fixture.shadow_root,
            clock=lambda: overlay_test.NOW,
            load_authority=lambda _paths, _now: authority,
        )

    @staticmethod
    def _reference(path: Path, root: Path) -> dict[str, object]:
        raw = path.read_bytes()
        return {
            "path": path.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
        }

    def test_completed_overlay_publishes_exact_source_terminal_proof(
        self,
    ) -> None:
        overlay_result = self._complete_overlay()
        self.assertEqual(overlay_result.status, "overlay_slot_completed")
        paths, authority = self._aggregate_authority()
        slot = authority.completed_slots[0]
        overlay_paths = (
            slot.overlay_intent_snapshot.path,
            slot.overlay_receipt_snapshot.path,
            slot.overlay_event_snapshot.path,
            paths.overlay.status,
            ROOT / "config/ootang_epoch_step_dependency_overlay.v1.json",
            ROOT / "code/monitoring/ootang_epoch_step_dependency_overlay.py",
        )
        upstream_paths = tuple(
            dict.fromkeys((*self.overlay_fixture.upstream_paths, *overlay_paths))
        )
        before = {path: path.read_bytes() for path in upstream_paths}

        result = self._coordinate(authority)

        self.assertEqual(result.status, "source_key_terminal_aggregated")
        self.assertTrue(result.terminal_for_source_key)
        self.assertIsNotNone(result.proof_path)
        self.assertIsNotNone(result.event_path)
        proof_path = result.proof_path  # type: ignore[assignment]
        event_path = result.event_path  # type: ignore[assignment]
        proof_raw = proof_path.read_bytes()
        proof = json.loads(proof_raw)
        event = json.loads(event_path.read_bytes())
        status = json.loads(result.status_path.read_bytes())
        proof_sha256 = hashlib.sha256(proof_raw).hexdigest()
        self.assertEqual(proof_path.name, f"{proof_sha256}.json")
        self.assertEqual(event["proof"], self._reference(proof_path, paths.root))
        self.assertTrue(proof["terminal_for_source_key"])
        self.assertNotIn("terminal_for_key", proof)
        self.assertEqual(
            proof["publication_condition"],
            "matching_source_terminal_aggregate_event_required",
        )
        derivation = proof["terminal_derivation"]
        sidecar_source = slot.sidecar_reservation["source_step"]
        sidecar_dependency = slot.sidecar_reservation["dependency"]
        wrapper = slot.overlay_intent["action_contract"]
        self.assertEqual(
            derivation["source_key_id"], self.overlay_fixture.source_item["key_id"]
        )
        self.assertEqual(
            derivation["previous_step_id"], slot.candidate.source_receipt["step_id"]
        )
        self.assertEqual(
            derivation["previous_step_index"],
            slot.candidate.source_receipt["step_index"],
        )
        self.assertEqual(derivation["step_id"], sidecar_source["step_id"])
        self.assertEqual(derivation["step_index"], sidecar_source["step_index"])
        self.assertEqual(derivation["next_actions"], [])
        self.assertEqual(
            derivation["transition_plan_sha256"],
            sidecar_source["transition_plan_sha256"],
        )
        self.assertEqual(
            derivation["transition_plan_sha256"],
            wrapper["recovery_transition_plan_sha256"],
        )
        recovery_refs = proof["recovery_authority"]
        self.assertEqual(
            recovery_refs["source_previous_receipt"],
            sidecar_source["previous_receipt"],
        )
        self.assertEqual(
            recovery_refs["source_previous_event"], sidecar_source["previous_event"]
        )
        self.assertEqual(
            recovery_refs["dependency_terminal_receipt"],
            sidecar_dependency["terminal_receipt"],
        )
        self.assertEqual(
            recovery_refs["dependency_terminal_event"],
            sidecar_dependency["terminal_event"],
        )
        self.assertEqual(
            proof["sidecar_authority"]["reservation"],
            self._reference(
                slot.sidecar_reservation_snapshot.path, paths.overlay.sidecar.root
            ),
        )
        self.assertEqual(
            proof["sidecar_authority"]["event"],
            self._reference(
                slot.sidecar_event_snapshot.path, paths.overlay.sidecar.root
            ),
        )
        self.assertEqual(
            proof["overlay_authority"]["intent"],
            self._reference(slot.overlay_intent_snapshot.path, paths.overlay.root),
        )
        self.assertEqual(
            proof["overlay_authority"]["receipt"],
            self._reference(slot.overlay_receipt_snapshot.path, paths.overlay.root),
        )
        self.assertEqual(
            proof["overlay_authority"]["event"],
            self._reference(slot.overlay_event_snapshot.path, paths.overlay.root),
        )
        self.assertEqual(
            proof["overlay_authority"]["effective_dependency_keys"],
            [self.overlay_fixture.dependency_item["natural_key"]],
        )
        source_receipt = json.loads(
            slot.candidate.source_receipt_snapshot.path.read_bytes()
        )
        self.assertFalse(source_receipt["terminal_for_key"])
        for payload in (proof, event, status):
            for claim in aggregate.FALSE_CLAIMS:
                self.assertFalse(payload[claim], claim)
        self.assertFalse(result.original_recovery_key_terminal)
        self.assertFalse(result.full_workset_terminal)
        self.assertFalse(result.terminal_transition_closure_implemented)
        self.assertFalse(result.old_epoch_drained)
        self.assertEqual({path: path.read_bytes() for path in upstream_paths}, before)

        proof_before = proof_path.read_bytes()
        event_before = event_path.read_bytes()
        replay = self._coordinate(authority)
        self.assertEqual(replay.status, "waiting_for_completed_overlay_event")
        self.assertEqual(proof_path.read_bytes(), proof_before)
        self.assertEqual(event_path.read_bytes(), event_before)
        self.assertEqual(len(tuple(paths.proofs.glob("*.json"))), 1)
        self.assertEqual(len(tuple(paths.events.glob("*.json"))), 1)
        self.assertEqual({path: path.read_bytes() for path in upstream_paths}, before)

    def test_proof_before_event_crash_only_forward_adopts_event(self) -> None:
        self._complete_overlay()
        paths, authority = self._aggregate_authority()
        with (
            mock.patch.object(
                aggregate,
                "_append_event",
                side_effect=RuntimeError("synthetic crash after terminal proof"),
            ),
            self.assertRaisesRegex(
                RuntimeError, "synthetic crash after terminal proof"
            ),
        ):
            self._coordinate(authority)

        proofs = tuple(paths.proofs.glob("*.json"))
        self.assertEqual(len(proofs), 1)
        proof_before = proofs[0].read_bytes()
        self.assertFalse(paths.events.exists())

        with (
            mock.patch.object(
                aggregate,
                "_select_candidate",
                side_effect=AssertionError("forward adoption must not reselect"),
            ),
            mock.patch.object(
                aggregate,
                "_ensure_proof",
                side_effect=AssertionError("forward adoption must not republish proof"),
            ),
            mock.patch.object(
                overlay,
                "_action_for_contract",
                side_effect=AssertionError(
                    "forward adoption must not repeat overlay action"
                ),
            ),
            mock.patch.object(
                recovery,
                "_outcome_settlement_adoption_action",
                side_effect=AssertionError("forward adoption must not settle again"),
            ),
        ):
            result = self._coordinate(authority)

        self.assertEqual(result.status, "source_terminal_event_forward_adopted")
        self.assertTrue(result.terminal_for_source_key)
        self.assertEqual(proofs[0].read_bytes(), proof_before)
        self.assertEqual(len(tuple(paths.proofs.glob("*.json"))), 1)
        self.assertEqual(len(tuple(paths.events.glob("*.json"))), 1)

    def test_pending_overlay_receipt_waits_without_terminal_proof(self) -> None:
        contract_patch, action_patch = self._overlay_patches()
        with (
            contract_patch,
            action_patch,
            mock.patch.object(
                overlay,
                "_append_event",
                side_effect=RuntimeError("synthetic crash before overlay event"),
            ),
            self.assertRaisesRegex(
                RuntimeError, "synthetic crash before overlay event"
            ),
        ):
            self.overlay_fixture._coordinate()  # noqa: SLF001

        paths, authority = self._aggregate_authority()
        self.assertTrue(authority.incomplete_overlay_authority)
        self.assertEqual(authority.completed_slots, ())
        self.assertEqual(len(tuple(paths.overlay.receipts.glob("*.json"))), 1)
        self.assertFalse(paths.overlay.events.exists())

        result = self._coordinate(authority)

        self.assertEqual(result.status, "waiting_for_completed_overlay_event")
        self.assertFalse(result.terminal_for_source_key)
        self.assertFalse(paths.proofs.exists())
        self.assertFalse(paths.events.exists())


if __name__ == "__main__":
    unittest.main()
