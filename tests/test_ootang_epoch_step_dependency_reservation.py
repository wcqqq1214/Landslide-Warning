"""Fast contracts for cross-freeze manifest-sibling step dependencies."""

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
from monitoring import ootang_epoch_step_dependency_reservation as dependency  # noqa: E402
from monitoring import ootang_epoch_workset_recovery as recovery  # noqa: E402


NOW = datetime(2031, 2, 3, 4, 5, tzinfo=timezone.utc)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


class EpochStepDependencyReservationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(
            prefix="ootang-step-dependency-", dir=ROOT
        )
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.registry_root = self.base / "registry"
        self.active_root = self.base / "active"
        self.shadow_root = self.base / "shadow"
        for path in (self.registry_root, self.active_root, self.shadow_root):
            path.mkdir(parents=True)

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

    def _authority(
        self,
        *,
        dependency_terminal: bool = True,
        duplicate_dependency: bool = False,
    ) -> tuple[dependency.StepDependencyAuthority, tuple[Path, ...]]:
        recovery_root = self.registry_root / "workset_recovery_v1"
        manifest_root = self.registry_root / "workset_manifest_v1"
        source_key = _digest("source-key")
        outcome_key = _digest("outcome-key")
        source_step = _digest("source-step")
        outcome_step = _digest("outcome-step")
        scope = {
            "old_live_epoch_id": "old-live-epoch",
            "target_date": "2031-02-04",
            "issue_id": "issue-2031-02-04",
            "sealed_entry_sha256": _digest("seal"),
        }
        source_item = {
            "key_id": source_key,
            "family": "live_outstanding",
            "natural_key": "live-outstanding:2031-02-04",
            "canonical_successor_state": "anchor_result_recorded",
            "dependency_keys": [],
            "namespace_digest": _digest("source-namespace"),
            "artifacts": [],
            "authority": {
                "old_live_epoch_id": scope["old_live_epoch_id"],
                "target_date": scope["target_date"],
                "issue_id": scope["issue_id"],
                "seal_event": {
                    "entry_sha256": scope["sealed_entry_sha256"],
                },
            },
        }
        outcome_item = {
            "key_id": outcome_key,
            "family": "outcome_revision",
            "natural_key": "outcome-revision:2031-02-04",
            "canonical_successor_state": "outcome_or_revision_consumed",
            "dependency_keys": [],
            "namespace_digest": _digest("outcome-namespace"),
            "artifacts": [],
            "authority": {"target_date": scope["target_date"]},
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
                "sequence_id": 4,
                "event_key": "old-live-epoch:2031-02-04:anchor:1:confirmed",
                "entry_sha256": _digest("confirmation-entry"),
                "previous_entry_sha256": _digest("confirmation-previous"),
            },
        }
        outcome_receipt = {
            "step_id": outcome_step,
            "step_index": 0,
            "key_id": outcome_key,
            "action": "outcome_or_revision_consumed",
            "next_actions": [],
            "terminal_for_key": dependency_terminal,
            "action_output_kind": "live_outcome_consumption_transaction",
            "action_output": None,
            "action_semantics": {
                "schema_version": ("ootang_live_outcome_consumption_action_output_v1"),
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
                "exact_outcome_sha256": _digest("exact-outcome"),
                "outcome_source_id": "synthetic-outcome-source",
                "terminal_event": {
                    "sequence_id": 47,
                    "event_key": "old-live-epoch:2031-02-04:outcome:settled",
                    "entry_sha256": _digest("settlement-entry"),
                },
            },
        }
        manifest_payload = {
            "workset_keyset_sha256": _digest("workset"),
            "items": [source_item, outcome_item],
        }
        manifest_snapshot = self._snapshot(
            manifest_root / "manifests/frozen.json", manifest_payload
        )
        reservation_event_snapshot = self._snapshot(
            manifest_root / "events/0001.json", {"reservation": True}
        )
        global_snapshot = self._snapshot(
            recovery_root / "intent.json", {"intent": "frozen-v6"}
        )
        source_snapshot = self._snapshot(
            recovery_root / f"receipts/{source_step}.json", source_receipt
        )
        outcome_snapshot = self._snapshot(
            recovery_root / f"receipts/{outcome_step}.json", outcome_receipt
        )
        source_event_snapshot = self._snapshot(
            recovery_root / "events/0001.json",
            {"step_id": source_step, "key_id": source_key},
        )
        outcome_event_snapshot = self._snapshot(
            recovery_root / "events/0002.json",
            {"step_id": outcome_step, "key_id": outcome_key},
        )
        items = [source_item, outcome_item]
        chains = {
            source_key: [(source_receipt, source_snapshot)],
            outcome_key: [(outcome_receipt, outcome_snapshot)],
        }
        events = {
            source_step: (
                {"step_id": source_step, "key_id": source_key},
                source_event_snapshot,
            ),
            outcome_step: (
                {"step_id": outcome_step, "key_id": outcome_key},
                outcome_event_snapshot,
            ),
        }
        if duplicate_dependency:
            duplicate_item = {
                **outcome_item,
                "key_id": _digest("duplicate-outcome-key"),
                "natural_key": "outcome-revision:2031-02-04:duplicate",
                "namespace_digest": _digest("duplicate-outcome-namespace"),
            }
            duplicate_receipt = {
                **outcome_receipt,
                "step_id": _digest("duplicate-outcome-step"),
                "key_id": duplicate_item["key_id"],
            }
            duplicate_snapshot = self._snapshot(
                recovery_root / f"receipts/{duplicate_receipt['step_id']}.json",
                duplicate_receipt,
            )
            duplicate_event_snapshot = self._snapshot(
                recovery_root / "events/0003.json",
                {
                    "step_id": duplicate_receipt["step_id"],
                    "key_id": duplicate_item["key_id"],
                },
            )
            items.append(duplicate_item)
            chains[duplicate_item["key_id"]] = [(duplicate_receipt, duplicate_snapshot)]
            events[duplicate_receipt["step_id"]] = (
                {
                    "step_id": duplicate_receipt["step_id"],
                    "key_id": duplicate_item["key_id"],
                },
                duplicate_event_snapshot,
            )
            manifest_payload["items"] = items
        reservation = recovery.Reservation(
            profile={},
            paths=SimpleNamespace(root=manifest_root),  # type: ignore[arg-type]
            binding=mock.sentinel.binding,
            event={"reservation": True},
            event_snapshot=reservation_event_snapshot,
            manifest=manifest_payload,
            manifest_snapshot=manifest_snapshot,
        )
        authority = dependency.StepDependencyAuthority(
            recovery_profile={
                "profile_id": "synthetic-recovery-v6",
                "_profile_sha256": _digest("recovery-profile"),
            },
            reservation=reservation,
            global_intent={"intent": "frozen-v6"},
            global_snapshot=global_snapshot,
            ordered_items=tuple(items),
            chains=chains,
            events_by_step=events,
        )
        base_paths = tuple(
            path for path in recovery_root.rglob("*") if path.is_file()
        ) + (manifest_snapshot.path, reservation_event_snapshot.path)
        return authority, base_paths

    def _coordinate(
        self, authority: dependency.StepDependencyAuthority
    ) -> dependency.StepDependencyResult:
        return dependency._coordinate_step_dependency_reservation(  # noqa: SLF001
            registry_root=self.registry_root,
            active_root=self.active_root,
            shadow_root=self.shadow_root,
            clock=lambda: NOW,
            load_authority=lambda _paths, _now: authority,
        )

    def test_unique_terminal_manifest_sibling_is_reserved_content_addressably(
        self,
    ) -> None:
        authority, base_paths = self._authority()
        before = {path: path.read_bytes() for path in base_paths}

        result = self._coordinate(authority)

        self.assertEqual(result.status, "step_dependency_reserved")
        self.assertIsNotNone(result.reservation_path)
        self.assertIsNotNone(result.event_path)
        reservation_raw = result.reservation_path.read_bytes()  # type: ignore[union-attr]
        reservation_sha = hashlib.sha256(reservation_raw).hexdigest()
        self.assertEqual(
            result.reservation_path.name,  # type: ignore[union-attr]
            f"{reservation_sha}.json",
        )
        reservation_payload = json.loads(reservation_raw)
        source_item, dependency_item = authority.ordered_items
        self.assertEqual(
            reservation_payload["source_step"]["key_id"], source_item["key_id"]
        )
        self.assertEqual(
            reservation_payload["source_step"]["action"],
            "outcome_batch_settled",
        )
        self.assertEqual(
            reservation_payload["dependency"]["key_id"],
            dependency_item["key_id"],
        )
        self.assertTrue(reservation_payload["dependency"]["terminal_for_key"])
        event_payload = json.loads(result.event_path.read_bytes())  # type: ignore[union-attr]
        self.assertEqual(event_payload["reservation"]["sha256"], reservation_sha)
        status = json.loads(result.status_path.read_bytes())
        for claim in (
            "bounded_workset_recovery_implemented",
            "terminal_transition_closure_implemented",
            "derived_future_work_reservation_implemented",
            "lifecycle_authority",
            "old_epoch_drained",
        ):
            self.assertFalse(reservation_payload[claim])
            self.assertFalse(event_payload[claim])
            self.assertFalse(status[claim])
            self.assertFalse(getattr(result, claim))
        self.assertEqual(
            {path: path.read_bytes() for path in base_paths},
            before,
            "the sidecar must not rewrite frozen recovery v6 artifacts",
        )

    def test_object_before_event_crash_is_forward_adopted_without_reselection(
        self,
    ) -> None:
        authority, base_paths = self._authority()
        before = {path: path.read_bytes() for path in base_paths}

        with (
            mock.patch.object(
                dependency,
                "_append_event",
                side_effect=RuntimeError("synthetic crash before event"),
            ),
            self.assertRaisesRegex(RuntimeError, "synthetic crash before event"),
        ):
            self._coordinate(authority)

        profile = dependency.load_step_dependency_profile()
        paths = dependency.step_dependency_paths(
            profile,
            registry_root=self.registry_root,
            active_root=self.active_root,
            shadow_root=self.shadow_root,
        )
        objects = tuple(paths.reservation_objects.glob("*.json"))
        self.assertEqual(len(objects), 1)
        self.assertFalse(paths.events.exists())

        with mock.patch.object(
            dependency,
            "_select_candidate",
            side_effect=AssertionError("orphan reservation must not be reselected"),
        ):
            result = self._coordinate(authority)

        self.assertEqual(result.status, "step_dependency_event_forward_adopted")
        self.assertEqual(result.reservation_path, objects[0])
        self.assertEqual(len(tuple(paths.reservation_objects.glob("*.json"))), 1)
        self.assertEqual(len(tuple(paths.events.glob("*.json"))), 1)
        self.assertEqual(
            {path: path.read_bytes() for path in base_paths},
            before,
            "forward adoption must not rewrite recovery v6 artifacts",
        )

    def test_nonterminal_sibling_waits_and_ambiguous_terminal_siblings_block(
        self,
    ) -> None:
        nonterminal, _ = self._authority(dependency_terminal=False)
        self.assertIsNone(dependency._select_candidate(nonterminal, set()))  # noqa: SLF001

        ambiguous, _ = self._authority(duplicate_dependency=True)
        with self.assertRaisesRegex(
            dependency.StepDependencyIntegrityError,
            "multiple terminal outcome siblings",
        ):
            dependency._select_candidate(ambiguous, set())  # noqa: SLF001
