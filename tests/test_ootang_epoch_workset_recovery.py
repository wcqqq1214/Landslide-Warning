"""Fast coordinator contracts for manifest-keyed epoch recovery."""

from __future__ import annotations

from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from email.message import Message
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
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
from monitoring import ootang_epoch_workset_inventory as inventory  # noqa: E402
from monitoring import ootang_epoch_workset_manifest as manifest  # noqa: E402
from monitoring import ootang_epoch_workset_recovery as recovery  # noqa: E402
from monitoring import ootang_live_ledger as live_ledger  # noqa: E402
from monitoring import ootang_live_source as source_module  # noqa: E402
from monitoring import ootang_outcome_materializer as outcomes  # noqa: E402
from monitoring import ootang_trusted_time_shadow_core as trusted  # noqa: E402
from tests.test_ootang_prequential_live import (  # noqa: E402
    ANCHOR_URL,
    FIRST_TARGET,
    ISSUE_CLOCK,
    OUTCOME_CLOCK,
    SECOND_ISSUE_CLOCK,
    _LiveFixture,
)


NOW = datetime(2031, 2, 3, 4, 5, tzinfo=timezone.utc)


def _digest(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()


def _external_anchor_profile() -> dict[str, object]:
    return {
        "mode": "https_json_post_from_environment",
        "endpoint_environment_variable": "OOTANG_TIME_ANCHOR_URL",
        "bearer_token_environment_variable": "OOTANG_TIME_ANCHOR_TOKEN",
        "timeout_seconds": 10,
        "anchor_issue_batch_seal": True,
        "missing_or_failed_status": "locally_sealed_unanchored",
        "confirmed_status": "externally_anchored_blind_candidate",
        "provider_allowlist": [],
        "receipt_verification_mode": ("interface_only_no_cryptographic_verifier_e2a"),
        "trusted_receipt_required_for_live_evidence": True,
        "e2a_receipts_count_as_live_evidence": False,
        "required_before_outcome_read": False,
        "claim_independent_time_proof_without_confirmed_receipt": False,
    }


class WorksetRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="ootang-recovery-", dir=ROOT)
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.registry_root = self.base / "registry"
        self.active_root = self.base / "active"
        self.shadow_root = self.base / "shadow"
        for path in (self.registry_root, self.active_root, self.shadow_root):
            path.mkdir(parents=True)
        self.paths = recovery.RecoveryPaths(
            registry_root=self.registry_root,
            root=self.registry_root / "workset_recovery_v1",
            global_intent=self.registry_root / "workset_recovery_v1/intent.json",
            item_intents=self.registry_root / "workset_recovery_v1/item_intents",
            anchor_result_response_objects=(
                self.registry_root
                / "workset_recovery_v1/external_anchor_response_objects"
            ),
            anchor_result_response_links=(
                self.registry_root
                / "workset_recovery_v1/external_anchor_response_links"
            ),
            receipts=self.registry_root / "workset_recovery_v1/receipts",
            events=self.registry_root / "workset_recovery_v1/events",
            status=self.registry_root / "workset_recovery_v1/status.json",
            anchor_result_dispatch_lock=(
                self.registry_root / "workset_recovery_v1/external_anchor_dispatch.lock"
            ),
            manager_lock=self.registry_root / "manager.lock",
            active_root=self.active_root,
            shadow_root=self.shadow_root,
            cycle_lock=self.active_root / "cycle.lock",
            replay_lock=self.active_root / "replay.lock",
            shadow_lock=self.shadow_root / "runner.lock",
        )
        self.profile = {
            "profile_id": "synthetic-recovery-v1",
            "_profile_sha256": "a" * 64,
            "protocol": {
                "intent_schema_version": "intent-v2",
                "item_intent_schema_version": "step-intent-v2",
                "receipt_schema_version": "step-receipt-v2",
                "event_schema_version": "step-event-v2",
                "status_schema_version": "status-v2",
                "anchor_result_response_observation_schema_version": (
                    "synthetic-anchor-result-response-observation-v1"
                ),
                "anchor_result_response_link_schema_version": (
                    "synthetic-anchor-result-response-link-v1"
                ),
                "event_type": "epoch_workset_transition_step_recorded",
            },
        }

    @staticmethod
    def _snapshot(path: Path) -> registry.ArtifactSnapshot:
        raw = path.read_bytes()
        return registry.ArtifactSnapshot(
            path, raw, hashlib.sha256(raw).hexdigest(), len(raw)
        )

    def _reservation(
        self, specs: list[tuple[str, str, list[str]]] | None = None
    ) -> recovery.Reservation:
        manifest_root = self.registry_root / "workset_manifest_v1"
        manifest_root.mkdir(parents=True, exist_ok=True)
        event_path = manifest_root / "reservation-event.json"
        event_path.write_bytes(b'{"reservation":true}\n')
        items = []
        for index, (natural, successor, dependencies) in enumerate(
            specs or [("natural-a", "anchor_receipt_repaired", [])]
        ):
            predecessor = self.active_root / f"inputs/{index}.json"
            predecessor.parent.mkdir(parents=True, exist_ok=True)
            predecessor.write_bytes(f'{{"index":{index}}}\n'.encode())
            raw = predecessor.read_bytes()
            items.append(
                {
                    "family": {
                        "issue_replay_receipt_verified": "issue_route_replay",
                        "issue_route_replay_consumed": "issue_route_replay",
                        "anchor_receipt_repaired": "live_outstanding",
                        "anchor_request_recorded": "live_outstanding",
                        "anchor_result_recorded": "live_outstanding",
                        "outcome_batch_settled": "live_outstanding",
                        "source_snapshot_ingested": "outcome_revision",
                        "outcome_materialized": "outcome_revision",
                        "outcome_or_revision_consumed": "outcome_revision",
                        "guard_completion_recorded": "guard",
                        "superseded_by_backfill": "guard",
                        "trusted_time_request_der_repaired": "trusted_time",
                        "trusted_time_response_link_recorded": "trusted_time",
                        "trusted_time_receipt_verified": "trusted_time",
                        "shadow_outstanding_settled": "shadow",
                        "shadow_live_event_classified": "shadow",
                        "shadow_cursor_at_frozen_live_upper_tip": "shadow",
                    }[successor],
                    "natural_key": natural,
                    "canonical_successor_state": successor,
                    "dependency_keys": dependencies,
                    "namespace_digest": hashlib.sha256(natural.encode()).hexdigest(),
                    "artifacts": [
                        {
                            "role": "predecessor",
                            "root": "active",
                            "path": predecessor.relative_to(
                                self.active_root
                            ).as_posix(),
                            "sha256": hashlib.sha256(raw).hexdigest(),
                            "size_bytes": len(raw),
                        }
                    ],
                    "authority": {"synthetic": True},
                }
            )
        payload = {
            "workset_keyset_sha256": "b" * 64,
            "frozen_live_upper_tip": {
                "old_live_epoch_id": "old-epoch",
                "live_event_count": 1,
                "live_terminal_sha256": "c" * 64,
            },
            "items": items,
        }
        manifest_path = manifest_root / "manifest.json"
        manifest_path.write_bytes(recovery._canonical_bytes(payload))  # noqa: SLF001
        manifest_paths = manifest.WorksetManifestPaths(
            self.registry_root,
            manifest_root,
            self.registry_root / "manager.lock",
            manifest_root / "manifests",
            manifest_root / "events",
            manifest_root / "status.json",
            self.active_root,
            self.shadow_root,
            self.active_root / "cycle.lock",
            self.active_root / "replay.lock",
            self.shadow_root / "runner.lock",
        )
        return recovery.Reservation(
            {},
            manifest_paths,
            mock.sentinel.binding,
            {"synthetic": True},
            self._snapshot(event_path),
            payload,
            self._snapshot(manifest_path),
        )

    def _run(
        self,
        reservation: recovery.Reservation | None,
        hook: recovery.ActionHook | None = None,
    ) -> recovery.RecoveryResult | recovery.AnchorResultDispatchPlan:
        with (
            mock.patch.object(
                recovery, "load_workset_recovery_profile", return_value=self.profile
            ),
            mock.patch.object(recovery, "recovery_paths", return_value=self.paths),
            mock.patch.object(recovery, "_load_reservation", return_value=reservation),
        ):
            return recovery._coordinate_epoch_workset_recovery(  # noqa: SLF001
                clock=lambda: NOW, action_hook=hook
            )

    def _anchor_result_dispatch_plan(self) -> recovery.AnchorResultDispatchPlan:
        key_id = _digest("synthetic-anchor-result-key")
        step_id = _digest("synthetic-anchor-result-step")
        endpoint = "https://anchor.invalid/v1/receipts"
        seal_sha256 = _digest("synthetic-anchor-result-seal")
        request_body = {
            "live_epoch_id": "synthetic-old-live-epoch",
            "target_date": "2031-02-04",
            "sealed_sequence_id": 7,
            "sealed_entry_sha256": seal_sha256,
            "attempt": 1,
        }
        request_body_raw = recovery.live._canonical_json(request_body).encode(  # noqa: SLF001
            "utf-8"
        )
        contract = {
            "schema_version": "synthetic-anchor-result-request-v1",
            "endpoint": endpoint,
            "endpoint_sha256": hashlib.sha256(endpoint.encode()).hexdigest(),
            "endpoint_environment_variable": "OOTANG_TEST_ANCHOR_URL",
            "bearer_token_environment_variable": "OOTANG_TEST_ANCHOR_TOKEN",
            "http_method": "POST",
            "timeout_seconds": 1,
            "maximum_response_bytes": recovery.ANCHOR_RESULT_MAXIMUM_RESPONSE_BYTES,
            "request_event": {
                "event_key": "synthetic-old-live-epoch:2031-02-04:anchor:1:requested",
                "event_type": "anchor_requested",
                "sequence_id": 8,
                "previous_entry_sha256": _digest("synthetic-anchor-predecessor"),
                "entry_sha256": _digest("synthetic-anchor-request-event"),
                "event_spec_sha256": _digest("synthetic-anchor-request-spec"),
            },
            "request_body": request_body,
            "request_body_sha256": hashlib.sha256(request_body_raw).hexdigest(),
            "idempotency_key": _digest("synthetic-anchor-idempotency-key"),
            "expected_result_pre_head": {
                "epoch_id": "synthetic-old-live-epoch",
                "event_count": 8,
                "sequence_id": 8,
                "entry_sha256": _digest("synthetic-anchor-request-event"),
            },
            "receipt_verification_mode": (
                "interface_only_no_cryptographic_verifier_e2a"
            ),
            "remote_delivery_semantics": (
                "at_least_once_unless_provider_honors_idempotency_key"
            ),
        }
        intent_path = self.paths.item_intents / f"{step_id}.json"
        intent_path.parent.mkdir(parents=True, exist_ok=True)
        intent_path.write_bytes(
            recovery._canonical_bytes(  # noqa: SLF001
                {
                    "profile_id": self.profile["profile_id"],
                    "profile_sha256": self.profile["_profile_sha256"],
                    "step_id": step_id,
                    "key_id": key_id,
                    "action": "anchor_result_recorded",
                    "action_contract": contract,
                }
            )
        )
        return recovery.AnchorResultDispatchPlan(
            self.profile,
            self.paths,
            key_id,
            step_id,
            self._snapshot(intent_path),
            contract,
        )

    @staticmethod
    def _anchor_result_success_response(
        plan: recovery.AnchorResultDispatchPlan,
    ) -> recovery.AnchorResultTransportResponse:
        body = json.dumps(
            {
                "provider": "synthetic-provider",
                "receipt_id": "synthetic-receipt",
                "anchored_at_utc": "2031-02-04T05:06:07Z",
                "root_sha256": plan.action_contract["request_body"][
                    "sealed_entry_sha256"
                ],
                "receipt": {"proof": "synthetic-proof"},
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return recovery.AnchorResultTransportResponse(
            body=body,
            status_code=200,
            media_type="application/json",
            charset="utf-8",
            content_encoding=None,
            final_url=str(plan.action_contract["endpoint"]),
        )

    @staticmethod
    def _hook(
        item: dict[str, object],
        _reservation: recovery.Reservation,
        _paths: recovery.RecoveryPaths,
    ) -> recovery.ActionOutput:
        return recovery.ActionOutput(
            "synthetic_local_output",
            None,
            {"natural_key": item["natural_key"], "network_action_performed": False},
        )

    def test_no_reservation_waits_without_authority(self) -> None:
        result = self._run(None, self._hook)

        self.assertEqual(result.status, "waiting_for_workset_reservation")
        self.assertIsNone(result.key_id)
        self.assertFalse(result.bounded_workset_recovery_implemented)
        self.assertFalse(result.lifecycle_authority)
        self.assertFalse(result.transition_authority)

        self.paths.global_intent.parent.mkdir(parents=True, exist_ok=True)
        self.paths.global_intent.write_bytes(b"{}\n")
        with self.assertRaisesRegex(
            recovery.WorksetRecoveryIntegrityError,
            "authority exists without its immutable reservation",
        ):
            self._run(None, self._hook)

    def test_empty_anchor_result_object_directory_is_not_authority(self) -> None:
        empty_step = self.paths.anchor_result_response_objects / _digest("empty-step")
        empty_step.mkdir(parents=True)

        result = self._run(None, self._hook)

        self.assertEqual(result.status, "waiting_for_workset_reservation")
        self.assertFalse(self.paths.global_intent.exists())
        self.assertFalse(self.paths.receipts.exists())
        self.assertFalse(self.paths.events.exists())

    def test_supported_key_progresses_intent_output_receipt_event_idempotently(
        self,
    ) -> None:
        reservation = self._reservation()
        calls: list[str] = []

        def hook(item, reserved, paths):  # type: ignore[no-untyped-def]
            calls.append(item["natural_key"])
            return self._hook(item, reserved, paths)

        first = self._run(reservation, hook)
        self.assertEqual(first.status, "recovery_item_completed")
        self.assertEqual(calls, ["natural-a"])
        self.assertTrue(first.receipt_path.is_file())  # type: ignore[union-attr]
        self.assertTrue(first.event_path.is_file())  # type: ignore[union-attr]
        receipt = json.loads(first.receipt_path.read_bytes())  # type: ignore[union-attr]
        self.assertTrue(
            (self.paths.item_intents / f"{receipt['step_id']}.json").is_file()
        )
        self.assertTrue(receipt["terminal_for_key"])

        second = self._run(reservation, hook)
        self.assertEqual(second.status, "waiting_for_supported_ready_key")
        self.assertEqual(calls, ["natural-a"])
        self.assertEqual(len(list(self.paths.events.iterdir())), 1)

    def test_dependencies_are_ordered_and_each_poll_advances_one_key(self) -> None:
        reservation = self._reservation(
            [
                ("natural-b", "anchor_receipt_repaired", ["natural-a"]),
                ("natural-a", "anchor_receipt_repaired", []),
            ]
        )
        calls: list[str] = []

        def hook(item, reserved, paths):  # type: ignore[no-untyped-def]
            calls.append(item["natural_key"])
            return self._hook(item, reserved, paths)

        self._run(reservation, hook)
        self.assertEqual(calls, ["natural-a"])
        self._run(reservation, hook)
        self.assertEqual(calls, ["natural-a", "natural-b"])
        self.assertEqual(len(list(self.paths.events.iterdir())), 2)

    def test_nonterminal_step_does_not_unlock_a_dependent_key(self) -> None:
        reservation = self._reservation(
            [
                ("natural-b", "anchor_receipt_repaired", ["natural-a"]),
                ("natural-a", "trusted_time_request_der_repaired", []),
            ]
        )
        calls: list[str] = []

        def hook(item, reserved, paths):  # type: ignore[no-untyped-def]
            calls.append(item["natural_key"])
            return self._hook(item, reserved, paths)

        first = self._run(reservation, hook)
        self.assertEqual(first.status, "recovery_step_completed")
        receipt = json.loads(first.receipt_path.read_bytes())  # type: ignore[union-attr]
        self.assertFalse(receipt["terminal_for_key"])
        self.assertEqual(receipt["action"], "trusted_time_request_der_repaired")
        self.assertEqual(
            receipt["next_actions"], ["trusted_time_response_link_recorded"]
        )
        global_intent = json.loads(self.paths.global_intent.read_bytes())
        self.assertNotIn("supported_key_ids", global_intent)
        self.assertIn(first.key_id, global_intent["initial_step_supported_key_ids"])
        self.assertFalse(global_intent["terminal_transition_closure_implemented"])

        second = self._run(reservation, hook)
        self.assertEqual(second.status, "waiting_for_supported_ready_key")
        self.assertEqual(calls, ["natural-a"])
        self.assertEqual(len(list(self.paths.events.iterdir())), 1)

    def test_output_after_action_crash_is_forward_adopted(self) -> None:
        reservation = self._reservation()
        output = self.active_root / "recovered/output.bin"
        attempts = 0

        def hook(item, reserved, paths):  # type: ignore[no-untyped-def]
            nonlocal attempts
            attempts += 1
            output.parent.mkdir(parents=True, exist_ok=True)
            if not output.exists():
                output.write_bytes(b"deterministic-output")
            if attempts == 1:
                raise RuntimeError("synthetic crash after action")
            snapshot = self._snapshot(output)
            return recovery.ActionOutput(
                "synthetic_local_output",
                recovery._reference(snapshot, paths.active_root),  # noqa: SLF001
                {"adopted": True},
            )

        with self.assertRaisesRegex(RuntimeError, "synthetic crash"):
            self._run(reservation, hook)
        result = self._run(reservation, hook)
        self.assertEqual(result.status, "recovery_item_completed")
        self.assertEqual(attempts, 2)
        self.assertEqual(output.read_bytes(), b"deterministic-output")

    def test_receipt_before_event_crash_is_forward_adopted_without_reexecution(
        self,
    ) -> None:
        reservation = self._reservation()
        calls = 0

        def hook(item, reserved, paths):  # type: ignore[no-untyped-def]
            nonlocal calls
            calls += 1
            return self._hook(item, reserved, paths)

        with (
            mock.patch.object(
                recovery,
                "_append_event",
                side_effect=RuntimeError("synthetic crash before event"),
            ),
            self.assertRaisesRegex(RuntimeError, "synthetic crash before event"),
        ):
            self._run(reservation, hook)
        self.assertEqual(calls, 1)
        self.assertEqual(len(list(self.paths.receipts.iterdir())), 1)
        self.assertFalse(self.paths.events.exists())

        result = self._run(reservation, hook)
        self.assertEqual(result.status, "recovery_event_forward_adopted")
        self.assertEqual(calls, 1)
        self.assertEqual(len(list(self.paths.events.iterdir())), 1)

    def test_multiple_receipts_without_events_are_rejected_as_a_branch(self) -> None:
        authority = self.registry_root / "authority.json"
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.write_bytes(b"{}\n")
        snapshot = self._snapshot(authority)
        receipts = {
            "a" * 64: ({"key_id": "key-a"}, snapshot),
            "b" * 64: ({"key_id": "key-b"}, snapshot),
        }

        with self.assertRaisesRegex(
            recovery.WorksetRecoveryIntegrityError,
            "receipts/events branched",
        ):
            recovery._load_events(  # noqa: SLF001
                self.profile, self.paths, receipts
            )

    def test_predecessor_receipt_and_event_tampering_fail_closed(self) -> None:
        reservation = self._reservation()
        reservation.paths.active_root.joinpath("inputs/0.json").write_bytes(b"tampered")
        with self.assertRaisesRegex(
            recovery.WorksetRecoveryIntegrityError, "predecessor failed exact CAS"
        ):
            self._run(reservation, self._hook)

        self._use_fresh_namespace()
        reservation = self._reservation()
        first = self._run(reservation, self._hook)
        first.receipt_path.write_bytes(b'{"tampered":true}\n')  # type: ignore[union-attr]
        with self.assertRaises(recovery.WorksetRecoveryIntegrityError):
            self._run(reservation, self._hook)

        self._use_fresh_namespace()
        reservation = self._reservation()
        first = self._run(reservation, self._hook)
        intent_path = next(self.paths.item_intents.glob("*.json"))
        intent = json.loads(intent_path.read_bytes())
        intent["action"] = "tampered-action"
        intent_path.write_bytes(recovery._canonical_bytes(intent))  # noqa: SLF001
        with self.assertRaisesRegex(
            recovery.WorksetRecoveryIntegrityError, "intent semantics"
        ):
            self._run(reservation, self._hook)

        self._use_fresh_namespace()
        reservation = self._reservation()
        first = self._run(reservation, self._hook)
        first.event_path.write_bytes(b'{"tampered":true}\n')  # type: ignore[union-attr]
        with self.assertRaises(recovery.WorksetRecoveryIntegrityError):
            self._run(reservation, self._hook)

    def _use_fresh_namespace(self) -> None:
        suffix = len(list(self.base.glob("registry-*"))) + 1
        self.registry_root = self.base / f"registry-{suffix}"
        self.active_root = self.base / f"active-{suffix}"
        self.shadow_root = self.base / f"shadow-{suffix}"
        for path in (self.registry_root, self.active_root, self.shadow_root):
            path.mkdir(parents=True)
        self.setUp_paths_only()

    def setUp_paths_only(self) -> None:
        self.paths = recovery.RecoveryPaths(
            registry_root=self.registry_root,
            root=self.registry_root / "recovery",
            global_intent=self.registry_root / "recovery/intent.json",
            item_intents=self.registry_root / "recovery/item_intents",
            anchor_result_response_objects=(
                self.registry_root / "recovery/external_anchor_response_objects"
            ),
            anchor_result_response_links=(
                self.registry_root / "recovery/external_anchor_response_links"
            ),
            receipts=self.registry_root / "recovery/receipts",
            events=self.registry_root / "recovery/events",
            status=self.registry_root / "recovery/status.json",
            anchor_result_dispatch_lock=(
                self.registry_root / "recovery/external_anchor_dispatch.lock"
            ),
            manager_lock=self.registry_root / "manager.lock",
            active_root=self.active_root,
            shadow_root=self.shadow_root,
            cycle_lock=self.active_root / "cycle.lock",
            replay_lock=self.active_root / "replay.lock",
            shadow_lock=self.shadow_root / "runner.lock",
        )

    @staticmethod
    def _synthetic_live_spec(
        event_key: str,
        *,
        event_type: str,
        target: str | None,
        issue_id: str | None,
        payload: dict[str, object],
        input_manifest_sha256: str,
        state_sha256: str,
    ) -> live_ledger.EventSpec:
        return live_ledger.EventSpec(
            event_key=event_key,
            event_type=event_type,
            target_date=target,
            station=None,
            issue_id=issue_id,
            protocol_config_sha256=_digest("synthetic-live-protocol"),
            code_sha256=_digest("synthetic-live-code"),
            environment_sha256=_digest("synthetic-live-environment"),
            input_manifest_sha256=input_manifest_sha256,
            model_manifest_sha256=_digest("synthetic-live-model"),
            state_before_sha256=(
                live_ledger.ZERO_HASH if event_type == "epoch_genesis" else state_sha256
            ),
            state_after_sha256=state_sha256,
            payload=payload,
        )

    def _anchor_request_fixture(self, *, completed_attempts: int = 0):
        epoch_id = "synthetic-live-epoch-anchor-request"
        target = "2031-02-04"
        issue_id = "synthetic-issue-batch"
        input_manifest_sha256 = _digest("synthetic-issue-input-manifest")
        state_sha256 = _digest("synthetic-station-state")
        ledger_path = self.active_root / "live-ledger.sqlite3"
        ledger = live_ledger.AppendOnlyLedger(ledger_path)
        ledger.append_transaction(
            [
                self._synthetic_live_spec(
                    f"{epoch_id}:epoch_genesis",
                    event_type="epoch_genesis",
                    target=None,
                    issue_id=None,
                    payload={"live_epoch_id": epoch_id},
                    input_manifest_sha256=input_manifest_sha256,
                    state_sha256=state_sha256,
                )
            ]
        )[0]
        seal = ledger.append_transaction(
            [
                self._synthetic_live_spec(
                    f"{epoch_id}:{target}:issue_batch_sealed",
                    event_type="issue_batch_sealed",
                    target=target,
                    issue_id=issue_id,
                    payload={"issue_batch_sha256": _digest("synthetic-issue-batch")},
                    input_manifest_sha256=input_manifest_sha256,
                    state_sha256=state_sha256,
                )
            ]
        )[0]
        for attempt in range(1, completed_attempts + 1):
            request_payload = {
                "live_epoch_id": epoch_id,
                "target_date": target,
                "sealed_sequence_id": seal.sequence_id,
                "sealed_entry_sha256": seal.entry_sha256,
                "attempt": attempt,
            }
            prefix = f"{epoch_id}:{target}:anchor:{attempt}"
            ledger.append_transaction(
                [
                    self._synthetic_live_spec(
                        f"{prefix}:requested",
                        event_type="anchor_requested",
                        target=target,
                        issue_id=issue_id,
                        payload=request_payload,
                        input_manifest_sha256=input_manifest_sha256,
                        state_sha256=state_sha256,
                    )
                ]
            )
            ledger.append_transaction(
                [
                    self._synthetic_live_spec(
                        f"{prefix}:failed",
                        event_type="anchor_failed",
                        target=target,
                        issue_id=issue_id,
                        payload={
                            **request_payload,
                            "reason_code": "endpoint_not_configured",
                            "retry_policy": (
                                "automatic_when_endpoint_becomes_available"
                            ),
                        },
                        input_manifest_sha256=input_manifest_sha256,
                        state_sha256=state_sha256,
                    )
                ]
            )
        frozen_events = ledger.read_events()
        frozen_head = frozen_events[-1]
        prerequisites = SimpleNamespace(
            implementation_sha256=_digest("synthetic-live-code"),
            environment_sha256=_digest("synthetic-live-environment"),
            model=SimpleNamespace(sha256=_digest("synthetic-live-model")),
        )
        projection = SimpleNamespace(
            epoch_id=epoch_id,
            outstanding_target_date=date.fromisoformat(target),
            outstanding_issue_id=issue_id,
            seal_event=seal,
        )
        expected_pre_head = recovery.live_cas.LiveLedgerPreHeadV1(
            epoch_id=epoch_id,
            event_count=len(frozen_events),
            sequence_id=len(frozen_events),
            entry_sha256=frozen_head.entry_sha256,
        )
        live_paths = SimpleNamespace(ledger=ledger_path)

        def load_frozen(_reservation):  # type: ignore[no-untyped-def]
            return recovery.FrozenLivePrefix(
                profile={},
                paths=live_paths,
                prerequisites=prerequisites,
                projection=projection,
                frozen_events=frozen_events,
                current_events=ledger.read_events(),
                expected_pre_head=expected_pre_head,
            )

        reservation = self._reservation(
            [("anchor-live", "anchor_request_recorded", [])]
        )
        reservation.manifest["frozen_live_upper_tip"] = {
            "old_live_epoch_id": epoch_id,
            "live_event_count": len(frozen_events),
            "live_terminal_sha256": frozen_head.entry_sha256,
        }
        item = {
            "family": "live_outstanding",
            "natural_key": "anchor-live",
            "canonical_successor_state": "anchor_request_recorded",
            "dependency_keys": [],
            "namespace_digest": _digest("anchor-live"),
            "authority": {
                "record_type": "outstanding_live_lifecycle",
                "target_date": target,
                "old_live_epoch_id": epoch_id,
                "issue_id": issue_id,
                "issue_sha256": _digest("synthetic-live-issue-file"),
                "input_manifest_sha256": input_manifest_sha256,
                "seal_event": {
                    "sequence_id": seal.sequence_id,
                    "entry_sha256": seal.entry_sha256,
                    "event_type": seal.event_type,
                    "target_date": seal.target_date,
                    "issue_id": seal.issue_id,
                },
                "anchor_confirmed_event": None,
                "frozen_live_upper_tip": frozen_head.entry_sha256,
                "terminal": False,
                "action": "anchor_request_recorded",
            },
        }
        return ledger, reservation, item, load_frozen, seal

    def _anchor_result_coordinator_fixture(self):
        ledger, reservation, item, load_frozen, seal = self._anchor_request_fixture()
        reserved_item = reservation.manifest["items"][0]
        reserved_item.update(
            {
                "family": item["family"],
                "natural_key": item["natural_key"],
                "canonical_successor_state": item["canonical_successor_state"],
                "dependency_keys": item["dependency_keys"],
                "namespace_digest": item["namespace_digest"],
                "authority": item["authority"],
            }
        )

        def load_external(reserved):  # type: ignore[no-untyped-def]
            frozen = load_frozen(reserved)
            return recovery.FrozenLivePrefix(
                profile={"external_anchor": _external_anchor_profile()},
                paths=frozen.paths,
                prerequisites=frozen.prerequisites,
                projection=frozen.projection,
                frozen_events=frozen.frozen_events,
                current_events=frozen.current_events,
                expected_pre_head=frozen.expected_pre_head,
            )

        return ledger, reservation, load_external, seal

    def _published_outstanding_outcome_fixture(
        self, fixture: _LiveFixture
    ) -> tuple[
        recovery.Reservation,
        tuple[live_ledger.LedgerEvent, ...],
        outcomes._RegisteredOutcome,
        recovery.live.Prerequisites,
    ]:
        """Freeze an outstanding tip, then publish it through the real materializer."""

        fixture.install_prerequisites()
        fixture.write_issue()
        with mock.patch.dict(
            os.environ, {"OOTANG_TIME_ANCHOR_URL": ANCHOR_URL}, clear=False
        ):
            fixture.poll(now=ISSUE_CLOCK, anchor_client=fixture.anchor_client())
        frozen_events = tuple(fixture.events())
        frozen_head = frozen_events[-1]
        self.assertEqual(frozen_head.event_type, "anchor_confirmed")
        prerequisites = recovery.live.load_prerequisites(fixture.profile, fixture.paths)
        self.assertIsNotNone(prerequisites)
        assert prerequisites is not None
        projection = recovery.live._reconstruct_projection(  # noqa: SLF001
            frozen_events, fixture.profile, prerequisites
        )
        seal = projection.seal_event
        self.assertIsNotNone(seal)
        assert seal is not None

        profile = outcomes.load_config()
        object_root = outcomes._runtime_path(  # noqa: SLF001
            profile, fixture.root, "objects"
        )
        dataset = outcomes._materialize_object(  # noqa: SLF001
            object_root, b"recovery-test-dataset\n", suffix="source.json"
        )
        semantic = outcomes._materialize_object(  # noqa: SLF001
            object_root,
            b"recovery-test-semantic-manifest\n",
            suffix="source-manifest.json",
        )
        activation = outcomes._artifact_from_path(  # noqa: SLF001
            fixture.source_manifest
        )
        record = source_module.DailySourceRecord(
            day=FIRST_TARGET,
            revision_id="revision-1",
            observed_at_utc="2030-01-02T01:00:00Z",
            available_at_utc="2030-01-02T02:00:00Z",
            finalized_at_utc="2030-01-02T04:00:00Z",
            rainfall_mm=2.0,
            reservoir_water_level_m=151.0,
            displacement_mm={
                station: fixture.latest[station] + 1.0 for station in fixture.stations
            },
        )
        receipt_record = {
            "date": record.day.isoformat(),
            "revision_id": record.revision_id,
            "observed_at_utc": record.observed_at_utc,
            "available_at_utc": record.available_at_utc,
            "finalized_at_utc": record.finalized_at_utc,
            "rainfall_mm": record.rainfall_mm,
            "reservoir_water_level_m": record.reservoir_water_level_m,
            "displacement_mm": record.displacement_mm,
        }
        revision_raw = outcomes._canonical_bytes(  # noqa: SLF001
            {
                "schema_version": "ootang_source_revision_receipt_v1",
                "case": "ootang",
                "outcome_source_id": "ootang-survey-source-v1",
                "target_date": FIRST_TARGET.isoformat(),
                "revision_id": record.revision_id,
                "revision_sequence_id": 1,
                "record_sha256": outcomes._canonical_digest(  # noqa: SLF001
                    receipt_record
                ),
                "record": receipt_record,
                "accepted_source_semantic_manifest": outcomes._artifact_payload(  # noqa: SLF001
                    semantic
                ),
                "predecessor_revision_receipt": None,
            }
        )
        revision_head = outcomes._materialize_object(  # noqa: SLF001
            object_root, revision_raw, suffix="source-revision.json"
        )
        snapshot_raw = outcomes._canonical_bytes(  # noqa: SLF001
            {
                "schema_version": "ootang_source_snapshot_receipt_v1",
                "case": "ootang",
                "profile_id": "ootang-prequential-deploy-v1",
                "outcome_source_id": "ootang-survey-source-v1",
                "snapshot_sequence_id": 1,
                "source_pointer": {},
                "source_semantic_manifest": {},
                "predecessor_snapshot_receipt": None,
            }
        )
        snapshot_receipt = outcomes._materialize_object(  # noqa: SLF001
            object_root, snapshot_raw, suffix="source-snapshot-receipt.json"
        )
        source = SimpleNamespace(
            watermark=FIRST_TARGET,
            outcome_source_id="ootang-survey-source-v1",
            exported_at_utc="2030-01-02T05:00:00Z",
            records=(record,),
            dataset=dataset,
            semantic_manifest=semantic,
            activation_manifest=activation,
            revision_heads=(revision_head,),
            snapshot_receipt=snapshot_receipt,
        )
        selection = outcomes._Selection(  # noqa: SLF001
            kind="outstanding", target_date=FIRST_TARGET, record=record
        )
        input_manifest = outcomes._materialize_input_manifest(  # noqa: SLF001
            profile,
            fixture.root,
            outcomes._build_input_manifest(profile, source, selection),  # noqa: SLF001
        )
        payload = outcomes._outcome_payload(  # noqa: SLF001
            profile,
            selection,
            input_manifest,
            outcome_source_id=source.outcome_source_id,
        )
        times = iter(
            (
                datetime(2030, 1, 2, 6, tzinfo=timezone.utc),
                datetime(2030, 1, 2, 7, tzinfo=timezone.utc),
            )
        )
        status, registered, _, _ = outcomes._publish_candidate(  # noqa: SLF001
            profile=profile,
            root=fixture.root,
            selection=selection,
            source_exported_at_utc=source.exported_at_utc,
            input_manifest=input_manifest,
            payload=payload,
            clock=lambda: next(times),
            live_module=recovery.live,
            live_profile=fixture.profile,
            prerequisites=prerequisites,
        )
        self.assertEqual(status, "materialized")
        self.assertEqual(tuple(fixture.events()), frozen_events)

        chain = outcomes._scan_receipt_chain(  # noqa: SLF001
            target=FIRST_TARGET,
            profile=profile,
            root=fixture.root,
            live_module=recovery.live,
            live_profile=fixture.profile,
            prerequisites=prerequisites,
        )
        self.assertIsNotNone(chain)
        assert chain is not None
        active_path = outcomes._outcome_inbox_path(  # noqa: SLF001
            profile, fixture.root, FIRST_TARGET
        )
        self.assertEqual(
            outcomes._legal_active_bytes(chain, active_path),
            chain.tip.raw,  # noqa: SLF001
        )
        artifact_specs = [
            (registered.receipt.path, "outcome_receipt"),
            (registered.exact_object.path, "exact_outcome_object"),
            (input_manifest.path, "outcome_source_manifest"),
            (chain.active_pointer_path, "active_outcome_receipt_pointer"),
            (active_path, "active_outcome"),
        ]
        artifacts = [
            inventory._artifact(  # noqa: SLF001
                path,
                role=role,
                root_label="active",
                root=fixture.root,
                maximum_bytes=64 * 1024 * 1024,
            )
            for path, role in artifact_specs
        ]
        authority = {
            "record_type": "outcome_receipt_chain",
            "target_date": FIRST_TARGET.isoformat(),
            "old_live_epoch_id": projection.epoch_id,
            "tip_source_revision_id": record.revision_id,
            "tip_receipt_sha256": registered.receipt.sha256,
            "tip_exact_outcome_sha256": registered.exact_object.sha256,
            "tip_published": True,
            "tip_ledger_consumed": False,
            "receipts": [
                {
                    "source_revision_id": record.revision_id,
                    "revision_sequence_id": 1,
                    "receipt_sha256": registered.receipt.sha256,
                    "exact_outcome_sha256": registered.exact_object.sha256,
                    "source_manifest_sha256": input_manifest.sha256,
                    "ledger_consumed": False,
                }
            ],
            "terminal": False,
            "action": "outcome_or_revision_consumed",
        }
        natural_key = inventory._key(  # noqa: SLF001
            "outcome_revision",
            projection.epoch_id,
            FIRST_TARGET.isoformat(),
            record.revision_id,
            seal.entry_sha256,
            registered.exact_object.sha256,
        )
        inventoried = inventory._item(  # noqa: SLF001
            "outcome_revision",
            natural_key,
            "outcome_or_revision_consumed",
            artifacts,
            authority,
        )

        self.active_root = fixture.root.resolve()
        self.setUp_paths_only()
        reservation = self._reservation(
            [(natural_key, "outcome_or_revision_consumed", [])]
        )
        reservation.manifest["frozen_live_upper_tip"] = {
            "old_live_epoch_id": projection.epoch_id,
            "live_event_count": len(frozen_events),
            "live_terminal_sha256": frozen_head.entry_sha256,
        }
        reservation.manifest["items"][0].update(
            {
                "family": inventoried.family,
                "natural_key": inventoried.natural_key,
                "canonical_successor_state": (inventoried.canonical_successor_state),
                "dependency_keys": list(inventoried.dependency_keys),
                "artifacts": [
                    inventory._artifact_payload(value)  # noqa: SLF001
                    for value in inventoried.artifacts
                ],
                "authority": dict(inventoried.authority),
                "namespace_digest": inventoried.namespace_digest,
            }
        )
        return reservation, frozen_events, registered, prerequisites

    def _unpublished_receipt_tip_fixture(
        self, fixture: _LiveFixture
    ) -> tuple[
        recovery.Reservation,
        tuple[live_ledger.LedgerEvent, ...],
        outcomes._RegisteredOutcome,
    ]:
        """Leave a valid immutable receipt tip before pointer/inbox publication."""

        reservation, frozen_events, registered, _ = (
            self._published_outstanding_outcome_fixture(fixture)
        )
        profile = outcomes.load_config()
        pointer_path = outcomes._active_pointer_path(  # noqa: SLF001
            outcomes._runtime_path(  # noqa: SLF001
                profile, fixture.root, "outcome_receipts"
            ),
            FIRST_TARGET,
        )
        active_path = outcomes._outcome_inbox_path(  # noqa: SLF001
            profile, fixture.root, FIRST_TARGET
        )
        pointer_path.unlink()
        active_path.unlink()

        item = reservation.manifest["items"][0]
        item["canonical_successor_state"] = "outcome_materialized"
        item["artifacts"] = [
            artifact
            for artifact in item["artifacts"]
            if artifact["role"]
            in {
                "outcome_receipt",
                "exact_outcome_object",
                "outcome_source_manifest",
            }
        ]
        item["authority"] = {
            **item["authority"],
            "tip_published": False,
            "action": "outcome_materialized",
        }
        return reservation, frozen_events, registered

    def _machine_selected_source_outcome_fixture(
        self, fixture: _LiveFixture
    ) -> tuple[
        recovery.Reservation,
        tuple[live_ledger.LedgerEvent, ...],
        date,
        source_module.CanonicalSource,
    ]:
        """Freeze one real source-selected outstanding outcome candidate."""

        source_profile = source_module.load_deploy_profile()
        baseline = date.fromisoformat(source_profile["historical_base"]["last_date"])
        activation_day = baseline + timedelta(days=1)
        target = activation_day + timedelta(days=1)
        fixture.install_prerequisites(watermark=activation_day)
        model = json.loads(fixture.model_manifest.read_bytes())
        model["created_at_utc"] = "2020-07-01T11:00:00Z"
        fixture._write_json(fixture.model_manifest, model)  # noqa: SLF001
        fixture.source_manifest.unlink()

        stations = list(source_profile["source_feed"]["station_order_live"])

        def source_record(day: date, revision: str, offset: float) -> dict[str, object]:
            return {
                "schema_version": source_profile["source_feed"][
                    "record_schema_version"
                ],
                "date": day.isoformat(),
                "revision_id": revision,
                "observed_at_utc": f"{day.isoformat()}T04:00:00Z",
                "available_at_utc": f"{day.isoformat()}T05:00:00Z",
                "finalized_at_utc": f"{day.isoformat()}T06:00:00Z",
                "finalized": True,
                "rainfall_mm": offset,
                "reservoir_water_level_m": 151.0 + offset,
                "displacement_mm": {
                    station: 200.0 + offset + index
                    for index, station in enumerate(stations)
                },
            }

        activation_record = source_record(activation_day, "revision-1", 1.0)
        target_record = source_record(target, "revision-2", 2.0)
        feed_path = source_module._runtime_path(  # noqa: SLF001
            source_profile, "incoming_feed", root=fixture.root
        )
        feed_path.parent.mkdir(parents=True, exist_ok=True)

        def ingest(records: list[dict[str, object]], exported_at: str) -> None:
            payload = {
                "schema_version": source_profile["source_feed"]["schema_version"],
                "outcome_source_id": "ootang-survey-source-v1",
                "exported_at_utc": exported_at,
                "records": records,
            }
            feed_path.write_bytes(source_module._canonical_bytes(payload))  # noqa: SLF001
            result = source_module.ingest_source(
                source_profile,
                runtime_root=fixture.root,
                now=datetime.fromisoformat(exported_at.replace("Z", "+00:00"))
                + timedelta(hours=1),
            )
            self.assertEqual(result.status, "ready")

        ingest([activation_record], "2020-07-01T07:00:00Z")
        fixture.write_issue(
            target,
            persistence=activation_record["displacement_mm"],  # type: ignore[arg-type]
        )
        with mock.patch.dict(
            os.environ, {"OOTANG_TIME_ANCHOR_URL": ANCHOR_URL}, clear=False
        ):
            fixture.poll(
                now=datetime(2020, 7, 1, 15, 45, tzinfo=timezone.utc),
                anchor_client=fixture.anchor_client("2020-07-01T15:50:00Z"),
            )
        frozen_events = tuple(fixture.events())
        frozen_head = frozen_events[-1]
        self.assertEqual(frozen_head.event_type, "anchor_confirmed")

        ingest(
            [activation_record, target_record],
            "2020-07-02T07:00:00Z",
        )
        source = source_module.load_current_source(
            source_profile, runtime_root=fixture.root
        )
        self.assertEqual(source.watermark, target)
        prerequisites = recovery.live.load_prerequisites(fixture.profile, fixture.paths)
        self.assertIsNotNone(prerequisites)
        assert prerequisites is not None
        projection = recovery.live._reconstruct_projection(  # noqa: SLF001
            frozen_events, fixture.profile, prerequisites
        )
        seal = projection.seal_event
        self.assertIsNotNone(seal)
        assert seal is not None

        target_index = next(
            index for index, record in enumerate(source.records) if record.day == target
        )
        artifact_specs = (
            (
                source_module._runtime_path(  # noqa: SLF001
                    source_profile, "current_source_pointer", root=fixture.root
                ),
                "current_source_pointer",
            ),
            (source.activation_manifest.path, "activation_source_manifest"),
            (source.semantic_manifest.path, "current_source_semantic_manifest"),
            (
                source.revision_heads[target_index].path,
                "current_source_revision_head",
            ),
            (source.snapshot_receipt.path, "current_source_snapshot_receipt"),
        )
        artifacts = [
            inventory._artifact(  # noqa: SLF001
                path,
                role=role,
                root_label="active",
                root=fixture.root,
                maximum_bytes=64 * 1024 * 1024,
            )
            for path, role in artifact_specs
        ]
        authority = {
            "record_type": "machine_selected_source_outcome",
            "selection_kind": "outstanding",
            "target_date": target.isoformat(),
            "old_live_epoch_id": projection.epoch_id,
            "outcome_source_id": source.outcome_source_id,
            "source_revision_id": source.records[target_index].revision_id,
            "previous_revision_id": None,
            "previous_outcome_sha256": None,
            "source_snapshot_sequence_id": source.snapshot_sequence_id,
            "source_snapshot_receipt_sha256": source.snapshot_receipt.sha256,
            "live_issue_seal_entry_sha256": seal.entry_sha256,
            "terminal": False,
            "action": "outcome_materialized",
        }
        natural_key = inventory._key(  # noqa: SLF001
            "outcome_revision",
            projection.epoch_id,
            target.isoformat(),
            source.records[target_index].revision_id,
            seal.entry_sha256,
            source.outcome_source_id,
        )
        inventoried = inventory._item(  # noqa: SLF001
            "outcome_revision",
            natural_key,
            "outcome_materialized",
            artifacts,
            authority,
        )

        self.active_root = fixture.root.resolve()
        self.setUp_paths_only()
        reservation = self._reservation([(natural_key, "outcome_materialized", [])])
        reservation.manifest["frozen_live_upper_tip"] = {
            "old_live_epoch_id": projection.epoch_id,
            "live_event_count": len(frozen_events),
            "live_terminal_sha256": frozen_head.entry_sha256,
        }
        reservation.manifest["items"][0].update(
            {
                "family": inventoried.family,
                "natural_key": inventoried.natural_key,
                "canonical_successor_state": (inventoried.canonical_successor_state),
                "dependency_keys": list(inventoried.dependency_keys),
                "artifacts": [
                    inventory._artifact_payload(value)  # noqa: SLF001
                    for value in inventoried.artifacts
                ],
                "authority": dict(inventoried.authority),
                "namespace_digest": inventoried.namespace_digest,
            }
        )
        return reservation, frozen_events, target, source

    def _pending_previous_revision_fixture(
        self, fixture: _LiveFixture
    ) -> tuple[
        recovery.Reservation,
        tuple[live_ledger.LedgerEvent, ...],
        date,
        outcomes._RegisteredOutcome,
        source_module.CanonicalSource,
    ]:
        """Build a real rev1 receipt plus a current-source rev2 reservation."""

        selected_reservation, frozen_events, target, source = (
            self._machine_selected_source_outcome_fixture(fixture)
        )
        prerequisites = recovery.live.load_prerequisites(fixture.profile, fixture.paths)
        self.assertIsNotNone(prerequisites)
        assert prerequisites is not None
        profile = outcomes.load_config()
        target_record = next(
            record for record in source.records if record.day == target
        )
        previous_record = source_module.DailySourceRecord(
            day=target_record.day,
            revision_id="revision-1",
            observed_at_utc=target_record.observed_at_utc,
            available_at_utc=target_record.available_at_utc,
            finalized_at_utc=target_record.finalized_at_utc,
            rainfall_mm=target_record.rainfall_mm - 1.0,
            reservoir_water_level_m=target_record.reservoir_water_level_m - 1.0,
            displacement_mm={
                station: value - 1.0
                for station, value in target_record.displacement_mm.items()
            },
        )
        receipt_record = {
            "date": previous_record.day.isoformat(),
            "revision_id": previous_record.revision_id,
            "observed_at_utc": previous_record.observed_at_utc,
            "available_at_utc": previous_record.available_at_utc,
            "finalized_at_utc": previous_record.finalized_at_utc,
            "rainfall_mm": previous_record.rainfall_mm,
            "reservoir_water_level_m": previous_record.reservoir_water_level_m,
            "displacement_mm": previous_record.displacement_mm,
        }
        revision_raw = outcomes._canonical_bytes(  # noqa: SLF001
            {
                "schema_version": "ootang_source_revision_receipt_v1",
                "case": "ootang",
                "outcome_source_id": source.outcome_source_id,
                "target_date": target.isoformat(),
                "revision_id": previous_record.revision_id,
                "revision_sequence_id": 1,
                "record_sha256": outcomes._canonical_digest(  # noqa: SLF001
                    receipt_record
                ),
                "record": receipt_record,
                "accepted_source_semantic_manifest": outcomes._artifact_payload(  # noqa: SLF001
                    source.semantic_manifest
                ),
                "predecessor_revision_receipt": None,
            }
        )
        object_root = outcomes._runtime_path(  # noqa: SLF001
            profile, fixture.root, "objects"
        )
        revision_head = outcomes._materialize_object(  # noqa: SLF001
            object_root, revision_raw, suffix="source-revision.json"
        )
        previous_source = SimpleNamespace(
            watermark=source.watermark,
            outcome_source_id=source.outcome_source_id,
            exported_at_utc=source.exported_at_utc,
            records=(previous_record,),
            dataset=source.dataset,
            semantic_manifest=source.semantic_manifest,
            activation_manifest=source.activation_manifest,
            revision_heads=(revision_head,),
            snapshot_receipt=source.snapshot_receipt,
        )
        selection = outcomes._Selection(  # noqa: SLF001
            kind="outstanding", target_date=target, record=previous_record
        )
        input_manifest = outcomes._materialize_input_manifest(  # noqa: SLF001
            profile,
            fixture.root,
            outcomes._build_input_manifest(  # noqa: SLF001
                profile, previous_source, selection
            ),
        )
        payload = outcomes._outcome_payload(  # noqa: SLF001
            profile,
            selection,
            input_manifest,
            outcome_source_id=source.outcome_source_id,
        )
        times = iter(
            (
                datetime(2020, 7, 2, 8, tzinfo=timezone.utc),
                datetime(2020, 7, 2, 9, tzinfo=timezone.utc),
            )
        )
        status, registered, _, _ = outcomes._publish_candidate(  # noqa: SLF001
            profile=profile,
            root=fixture.root,
            selection=selection,
            source_exported_at_utc=previous_source.exported_at_utc,
            input_manifest=input_manifest,
            payload=payload,
            clock=lambda: next(times),
            live_module=recovery.live,
            live_profile=fixture.profile,
            prerequisites=prerequisites,
        )
        self.assertEqual(status, "materialized")
        projection = recovery.live._reconstruct_projection(  # noqa: SLF001
            frozen_events, fixture.profile, prerequisites
        )
        seal = projection.seal_event
        self.assertIsNotNone(seal)
        assert seal is not None
        pointer_path = outcomes._active_pointer_path(  # noqa: SLF001
            outcomes._runtime_path(profile, fixture.root, "outcome_receipts"), target
        )
        active_path = outcomes._outcome_inbox_path(  # noqa: SLF001
            profile, fixture.root, target
        )
        previous_artifacts = tuple(
            inventory._artifact(  # noqa: SLF001
                path,
                role=role,
                root_label="active",
                root=fixture.root,
                maximum_bytes=64 * 1024 * 1024,
            )
            for path, role in (
                (registered.receipt.path, "outcome_receipt"),
                (registered.exact_object.path, "exact_outcome_object"),
                (input_manifest.path, "outcome_source_manifest"),
                (pointer_path, "active_outcome_receipt_pointer"),
                (active_path, "active_outcome"),
            )
        )
        previous_authority = {
            "record_type": "outcome_receipt_chain",
            "target_date": target.isoformat(),
            "old_live_epoch_id": projection.epoch_id,
            "tip_source_revision_id": previous_record.revision_id,
            "tip_receipt_sha256": registered.receipt.sha256,
            "tip_exact_outcome_sha256": registered.exact_object.sha256,
            "tip_published": True,
            "tip_ledger_consumed": False,
            "receipts": [
                {
                    "source_revision_id": previous_record.revision_id,
                    "revision_sequence_id": 1,
                    "receipt_sha256": registered.receipt.sha256,
                    "exact_outcome_sha256": registered.exact_object.sha256,
                    "source_manifest_sha256": input_manifest.sha256,
                    "ledger_consumed": False,
                }
            ],
            "terminal": False,
            "action": "outcome_or_revision_consumed",
        }
        previous_key = inventory._key(  # noqa: SLF001
            "outcome_revision",
            projection.epoch_id,
            target.isoformat(),
            previous_record.revision_id,
            seal.entry_sha256,
            registered.exact_object.sha256,
        )
        previous_item = inventory._item(  # noqa: SLF001
            "outcome_revision",
            previous_key,
            "outcome_or_revision_consumed",
            previous_artifacts,
            previous_authority,
        )
        selected_manifest_item = selected_reservation.manifest["items"][0]
        current_authority = {
            **selected_manifest_item["authority"],
            "selection_kind": "revision",
            "previous_revision_id": previous_record.revision_id,
            "previous_outcome_sha256": registered.exact_object.sha256,
        }
        current_item = inventory._item(  # noqa: SLF001
            "outcome_revision",
            selected_manifest_item["natural_key"],
            "outcome_materialized",
            tuple(
                inventory.ArtifactRef(**artifact)
                for artifact in selected_manifest_item["artifacts"]
            ),
            current_authority,
            dependency_keys=(previous_key,),
        )

        def item_payload(value: inventory.InventoryItem) -> dict[str, object]:
            return {
                "family": value.family,
                "natural_key": value.natural_key,
                "canonical_successor_state": value.canonical_successor_state,
                "dependency_keys": list(value.dependency_keys),
                "artifacts": [
                    inventory._artifact_payload(artifact)  # noqa: SLF001
                    for artifact in value.artifacts
                ],
                "authority": dict(value.authority),
                "namespace_digest": value.namespace_digest,
            }

        selected_reservation.manifest["items"] = [
            item_payload(previous_item),
            item_payload(current_item),
        ]
        selected_reservation.manifest["workset_keyset_sha256"] = hashlib.sha256(
            recovery._canonical_bytes(  # noqa: SLF001
                {
                    "natural_keys": sorted(
                        (previous_item.natural_key, current_item.natural_key)
                    )
                }
            )
        ).hexdigest()
        return selected_reservation, frozen_events, target, registered, source

    def _publish_newer_materializer_revision(
        self,
        fixture: _LiveFixture,
        prerequisites: recovery.live.Prerequisites,
        previous: outcomes._RegisteredOutcome,
    ) -> outcomes._RegisteredOutcome:
        profile = outcomes.load_config()
        previous_manifest = outcomes._artifact_from_mapping(  # noqa: SLF001
            previous.payload["source_manifest"],
            name="previous recovery-test outcome manifest",
        )
        manifest_payload = json.loads(previous_manifest.path.read_bytes())
        source_artifacts = manifest_payload["source"]
        record = source_module.DailySourceRecord(
            day=FIRST_TARGET,
            revision_id="revision-2",
            observed_at_utc="2030-01-02T04:30:00Z",
            available_at_utc="2030-01-02T05:30:00Z",
            finalized_at_utc="2030-01-02T06:30:00Z",
            rainfall_mm=2.5,
            reservoir_water_level_m=151.5,
            displacement_mm={
                station: fixture.latest[station] + 2.0 for station in fixture.stations
            },
        )
        receipt_record = {
            "date": record.day.isoformat(),
            "revision_id": record.revision_id,
            "observed_at_utc": record.observed_at_utc,
            "available_at_utc": record.available_at_utc,
            "finalized_at_utc": record.finalized_at_utc,
            "rainfall_mm": record.rainfall_mm,
            "reservoir_water_level_m": record.reservoir_water_level_m,
            "displacement_mm": record.displacement_mm,
        }
        revision_raw = outcomes._canonical_bytes(  # noqa: SLF001
            {
                "schema_version": "ootang_source_revision_receipt_v1",
                "case": "ootang",
                "outcome_source_id": manifest_payload["outcome_source_id"],
                "target_date": FIRST_TARGET.isoformat(),
                "revision_id": record.revision_id,
                "revision_sequence_id": 2,
                "record_sha256": outcomes._canonical_digest(  # noqa: SLF001
                    receipt_record
                ),
                "record": receipt_record,
                "accepted_source_semantic_manifest": source_artifacts[
                    "semantic_manifest"
                ],
                "predecessor_revision_receipt": source_artifacts[
                    "target_revision_receipt"
                ],
            }
        )
        object_root = outcomes._runtime_path(  # noqa: SLF001
            profile, fixture.root, "objects"
        )
        revision_head = outcomes._materialize_object(  # noqa: SLF001
            object_root, revision_raw, suffix="source-revision.json"
        )
        source = SimpleNamespace(
            watermark=FIRST_TARGET,
            outcome_source_id=manifest_payload["outcome_source_id"],
            exported_at_utc="2030-01-02T07:00:00Z",
            records=(record,),
            dataset=outcomes._artifact_from_mapping(  # noqa: SLF001
                source_artifacts["canonical_dataset"], name="revision dataset"
            ),
            semantic_manifest=outcomes._artifact_from_mapping(  # noqa: SLF001
                source_artifacts["semantic_manifest"],
                name="revision semantic manifest",
            ),
            activation_manifest=outcomes._artifact_from_mapping(  # noqa: SLF001
                source_artifacts["activation_source_manifest"],
                name="revision activation manifest",
            ),
            revision_heads=(revision_head,),
            snapshot_receipt=outcomes._artifact_from_mapping(  # noqa: SLF001
                source_artifacts["snapshot_receipt"],
                name="revision snapshot receipt",
            ),
        )
        selection = outcomes._Selection(  # noqa: SLF001
            kind="revision",
            target_date=FIRST_TARGET,
            record=record,
            previous_revision_id=previous.payload["source_revision_id"],
            previous_outcome_sha256=previous.exact_object.sha256,
        )
        input_manifest = outcomes._materialize_input_manifest(  # noqa: SLF001
            profile,
            fixture.root,
            outcomes._build_input_manifest(profile, source, selection),  # noqa: SLF001
        )
        payload = outcomes._outcome_payload(  # noqa: SLF001
            profile,
            selection,
            input_manifest,
            outcome_source_id=source.outcome_source_id,
        )
        times = iter(
            (
                datetime(2030, 1, 2, 8, tzinfo=timezone.utc),
                datetime(2030, 1, 2, 9, tzinfo=timezone.utc),
            )
        )
        status, registered, _, _ = outcomes._publish_candidate(  # noqa: SLF001
            profile=profile,
            root=fixture.root,
            selection=selection,
            source_exported_at_utc=source.exported_at_utc,
            input_manifest=input_manifest,
            payload=payload,
            clock=lambda: next(times),
            live_module=recovery.live,
            live_profile=fixture.profile,
            prerequisites=prerequisites,
        )
        self.assertEqual(status, "materialized")
        return registered

    def _published_backfill_revision_fixture(
        self, fixture: _LiveFixture
    ) -> tuple[
        recovery.Reservation,
        tuple[live_ledger.LedgerEvent, ...],
        outcomes._RegisteredOutcome,
        outcomes._RegisteredOutcome,
        recovery.live.Prerequisites,
    ]:
        """Freeze a canonical first backfill, then publish its direct revision."""

        fixture.install_prerequisites()
        fixture.poll(now=ISSUE_CLOCK)
        genesis_events = tuple(fixture.events())
        self.assertEqual(
            tuple(event.event_type for event in genesis_events), ("epoch_genesis",)
        )
        prerequisites = recovery.live.load_prerequisites(fixture.profile, fixture.paths)
        self.assertIsNotNone(prerequisites)
        assert prerequisites is not None

        profile = outcomes.load_config()
        object_root = outcomes._runtime_path(  # noqa: SLF001
            profile, fixture.root, "objects"
        )
        dataset = outcomes._materialize_object(  # noqa: SLF001
            object_root, b"recovery-test-backfill-dataset\n", suffix="source.json"
        )
        semantic = outcomes._materialize_object(  # noqa: SLF001
            object_root,
            b"recovery-test-backfill-semantic-manifest\n",
            suffix="source-manifest.json",
        )
        activation = outcomes._artifact_from_path(  # noqa: SLF001
            fixture.source_manifest
        )
        record = source_module.DailySourceRecord(
            day=FIRST_TARGET,
            revision_id="revision-1",
            observed_at_utc="2030-01-02T01:00:00Z",
            available_at_utc="2030-01-02T02:00:00Z",
            finalized_at_utc="2030-01-02T04:00:00Z",
            rainfall_mm=2.0,
            reservoir_water_level_m=151.0,
            displacement_mm={
                station: fixture.latest[station] + 1.0 for station in fixture.stations
            },
        )
        receipt_record = {
            "date": record.day.isoformat(),
            "revision_id": record.revision_id,
            "observed_at_utc": record.observed_at_utc,
            "available_at_utc": record.available_at_utc,
            "finalized_at_utc": record.finalized_at_utc,
            "rainfall_mm": record.rainfall_mm,
            "reservoir_water_level_m": record.reservoir_water_level_m,
            "displacement_mm": record.displacement_mm,
        }
        revision_raw = outcomes._canonical_bytes(  # noqa: SLF001
            {
                "schema_version": "ootang_source_revision_receipt_v1",
                "case": "ootang",
                "outcome_source_id": "ootang-survey-source-v1",
                "target_date": FIRST_TARGET.isoformat(),
                "revision_id": record.revision_id,
                "revision_sequence_id": 1,
                "record_sha256": outcomes._canonical_digest(  # noqa: SLF001
                    receipt_record
                ),
                "record": receipt_record,
                "accepted_source_semantic_manifest": (
                    outcomes._artifact_payload(semantic)  # noqa: SLF001
                ),
                "predecessor_revision_receipt": None,
            }
        )
        revision_head = outcomes._materialize_object(  # noqa: SLF001
            object_root, revision_raw, suffix="source-revision.json"
        )
        snapshot_raw = outcomes._canonical_bytes(  # noqa: SLF001
            {
                "schema_version": "ootang_source_snapshot_receipt_v1",
                "case": "ootang",
                "profile_id": "ootang-prequential-deploy-v1",
                "outcome_source_id": "ootang-survey-source-v1",
                "snapshot_sequence_id": 1,
                "source_pointer": {},
                "source_semantic_manifest": {},
                "predecessor_snapshot_receipt": None,
            }
        )
        snapshot_receipt = outcomes._materialize_object(  # noqa: SLF001
            object_root, snapshot_raw, suffix="source-snapshot-receipt.json"
        )
        source = SimpleNamespace(
            watermark=FIRST_TARGET,
            outcome_source_id="ootang-survey-source-v1",
            exported_at_utc="2030-01-02T05:00:00Z",
            records=(record,),
            dataset=dataset,
            semantic_manifest=semantic,
            activation_manifest=activation,
            revision_heads=(revision_head,),
            snapshot_receipt=snapshot_receipt,
        )
        selection = outcomes._Selection(  # noqa: SLF001
            kind="backfill", target_date=FIRST_TARGET, record=record
        )
        input_manifest = outcomes._materialize_input_manifest(  # noqa: SLF001
            profile,
            fixture.root,
            outcomes._build_input_manifest(profile, source, selection),  # noqa: SLF001
        )
        payload = outcomes._outcome_payload(  # noqa: SLF001
            profile,
            selection,
            input_manifest,
            outcome_source_id=source.outcome_source_id,
        )
        times = iter(
            (
                datetime(2030, 1, 2, 6, tzinfo=timezone.utc),
                datetime(2030, 1, 2, 7, tzinfo=timezone.utc),
            )
        )
        status, first, _, _ = outcomes._publish_candidate(  # noqa: SLF001
            profile=profile,
            root=fixture.root,
            selection=selection,
            source_exported_at_utc=source.exported_at_utc,
            input_manifest=input_manifest,
            payload=payload,
            clock=lambda: next(times),
            live_module=recovery.live,
            live_profile=fixture.profile,
            prerequisites=prerequisites,
        )
        self.assertEqual(status, "materialized")

        fixture.poll(now=OUTCOME_CLOCK)
        frozen_events = tuple(fixture.events())
        self.assertEqual(
            tuple(event.event_type for event in frozen_events),
            ("epoch_genesis", "backfill_not_blind"),
        )
        frozen_head = frozen_events[-1]
        projection = recovery.live._reconstruct_projection(  # noqa: SLF001
            frozen_events, fixture.profile, prerequisites
        )
        self.assertEqual(
            projection.revision_ids[FIRST_TARGET.isoformat()][record.revision_id],
            first.exact_object.sha256,
        )

        second = self._publish_newer_materializer_revision(
            fixture, prerequisites, first
        )
        chain = outcomes._scan_receipt_chain(  # noqa: SLF001
            target=FIRST_TARGET,
            profile=profile,
            root=fixture.root,
            live_module=recovery.live,
            live_profile=fixture.profile,
            prerequisites=prerequisites,
        )
        self.assertIsNotNone(chain)
        assert chain is not None
        self.assertEqual(tuple(chain.receipts), (first, second))

        active_path = outcomes._outcome_inbox_path(  # noqa: SLF001
            profile, fixture.root, FIRST_TARGET
        )
        artifacts: list[inventory.ArtifactRef] = []
        receipt_records: list[dict[str, object]] = []
        known = projection.revision_ids[FIRST_TARGET.isoformat()]
        for registered in chain.receipts:
            registered_manifest = outcomes._artifact_from_mapping(  # noqa: SLF001
                registered.payload["source_manifest"],
                name="backfill revision source manifest",
            )
            artifacts.extend(
                inventory._artifact(  # noqa: SLF001
                    path,
                    role=role,
                    root_label="active",
                    root=fixture.root,
                    maximum_bytes=64 * 1024 * 1024,
                )
                for path, role in (
                    (registered.receipt.path, "outcome_receipt"),
                    (registered.exact_object.path, "exact_outcome_object"),
                    (registered_manifest.path, "outcome_source_manifest"),
                )
            )
            registered_revision = registered.payload["source_revision_id"]
            receipt_records.append(
                {
                    "source_revision_id": registered_revision,
                    "revision_sequence_id": registered.revision_sequence_id,
                    "receipt_sha256": registered.receipt.sha256,
                    "exact_outcome_sha256": registered.exact_object.sha256,
                    "source_manifest_sha256": registered_manifest.sha256,
                    "ledger_consumed": registered_revision in known,
                }
            )
        artifacts.extend(
            inventory._artifact(  # noqa: SLF001
                path,
                role=role,
                root_label="active",
                root=fixture.root,
                maximum_bytes=64 * 1024 * 1024,
            )
            for path, role in (
                (chain.active_pointer_path, "active_outcome_receipt_pointer"),
                (active_path, "active_outcome"),
            )
        )
        authority = {
            "record_type": "outcome_receipt_chain",
            "target_date": FIRST_TARGET.isoformat(),
            "old_live_epoch_id": projection.epoch_id,
            "tip_source_revision_id": second.payload["source_revision_id"],
            "tip_receipt_sha256": second.receipt.sha256,
            "tip_exact_outcome_sha256": second.exact_object.sha256,
            "tip_published": True,
            "tip_ledger_consumed": False,
            "receipts": receipt_records,
            "terminal": False,
            "action": "outcome_or_revision_consumed",
        }
        natural_key = inventory._key(  # noqa: SLF001
            "outcome_revision",
            projection.epoch_id,
            FIRST_TARGET.isoformat(),
            second.payload["source_revision_id"],
            live_ledger.ZERO_HASH,
            second.exact_object.sha256,
        )
        inventoried = inventory._item(  # noqa: SLF001
            "outcome_revision",
            natural_key,
            "outcome_or_revision_consumed",
            artifacts,
            authority,
        )

        self.active_root = fixture.root.resolve()
        self.setUp_paths_only()
        reservation = self._reservation(
            [(natural_key, "outcome_or_revision_consumed", [])]
        )
        reservation.manifest["frozen_live_upper_tip"] = {
            "old_live_epoch_id": projection.epoch_id,
            "live_event_count": len(frozen_events),
            "live_terminal_sha256": frozen_head.entry_sha256,
        }
        reservation.manifest["items"][0].update(
            {
                "family": inventoried.family,
                "natural_key": inventoried.natural_key,
                "canonical_successor_state": inventoried.canonical_successor_state,
                "dependency_keys": list(inventoried.dependency_keys),
                "artifacts": [
                    inventory._artifact_payload(value)  # noqa: SLF001
                    for value in inventoried.artifacts
                ],
                "authority": dict(inventoried.authority),
                "namespace_digest": inventoried.namespace_digest,
            }
        )
        return reservation, frozen_events, first, second, prerequisites

    def _outcome_settlement_fixture(
        self,
        fixture: _LiveFixture,
        *,
        write_outcome: bool,
        intervening_revision: bool = False,
    ) -> tuple[
        recovery.Reservation,
        dict[str, object],
        recovery.FrozenLivePrefix,
    ]:
        fixture.install_prerequisites()
        target = FIRST_TARGET
        if intervening_revision:
            fixture.write_outcome(
                FIRST_TARGET,
                revision_id="revision-1",
                actual_offset=1.0,
            )
            fixture.poll(now=OUTCOME_CLOCK)
            target = FIRST_TARGET + timedelta(days=1)
            issue_path = fixture.write_issue(
                target,
                persistence={
                    station: value + 1.0 for station, value in fixture.latest.items()
                },
            )
            anchor_clock = SECOND_ISSUE_CLOCK
        else:
            issue_path = fixture.write_issue()
            anchor_clock = ISSUE_CLOCK
        with mock.patch.dict(
            os.environ, {"OOTANG_TIME_ANCHOR_URL": ANCHOR_URL}, clear=False
        ):
            fixture.poll(
                now=anchor_clock,
                anchor_client=fixture.anchor_client(),
            )

        frozen_events = tuple(fixture.events())
        confirmed = frozen_events[-1]
        self.assertEqual(confirmed.event_type, "anchor_confirmed")
        prerequisites = recovery.live.load_prerequisites(fixture.profile, fixture.paths)
        self.assertIsNotNone(prerequisites)
        assert prerequisites is not None
        projection = recovery.live._reconstruct_projection(  # noqa: SLF001
            frozen_events, fixture.profile, prerequisites
        )
        seal = projection.seal_event
        self.assertIsNotNone(seal)
        assert seal is not None

        if write_outcome:
            if intervening_revision:
                fixture.write_outcome(
                    FIRST_TARGET,
                    revision_id="revision-2",
                    actual_offset=2.0,
                )
                fixture.write_outcome(
                    target,
                    revision_id="revision-1",
                    actual_offset=3.0,
                )
                outcome_clock = datetime(2030, 1, 3, 8, tzinfo=timezone.utc)
            else:
                fixture.write_outcome()
                outcome_clock = OUTCOME_CLOCK
            with mock.patch.dict(
                os.environ, {"OOTANG_TIME_ANCHOR_URL": ANCHOR_URL}, clear=False
            ):
                fixture.poll(
                    now=outcome_clock,
                    anchor_client=fixture.anchor_client(),
                )

        current_events = tuple(fixture.events())
        settlement = next(
            (
                event
                for event in current_events
                if event.event_type == "outcome_batch_settled"
                and event.target_date == seal.target_date
            ),
            None,
        )
        reserved_batch_sha256 = (
            settlement.payload["outcome_batch_sha256"]
            if settlement is not None
            else _digest("reserved-revision-1-outcome")
        )
        outcome_natural_key = "settle-outcome-revision-1"
        reservation = self._reservation(
            [
                (
                    "settle-live",
                    "outcome_batch_settled",
                    [outcome_natural_key],
                ),
                (
                    outcome_natural_key,
                    "outcome_or_revision_consumed",
                    [],
                ),
            ]
        )
        reservation.manifest["frozen_live_upper_tip"] = {
            "old_live_epoch_id": projection.epoch_id,
            "live_event_count": len(frozen_events),
            "live_terminal_sha256": confirmed.entry_sha256,
        }
        reservation.manifest["items"][1].update(
            {
                "family": "outcome_revision",
                "canonical_successor_state": "outcome_or_revision_consumed",
                "authority": {
                    "record_type": "outcome_receipt_chain",
                    "target_date": seal.target_date,
                    "old_live_epoch_id": projection.epoch_id,
                    "tip_source_revision_id": "revision-1",
                    "tip_exact_outcome_sha256": reserved_batch_sha256,
                    "tip_published": True,
                    "tip_ledger_consumed": False,
                    "terminal": False,
                },
            }
        )
        item = {
            "family": "live_outstanding",
            "natural_key": "settle-live",
            "canonical_successor_state": "outcome_batch_settled",
            "dependency_keys": [outcome_natural_key],
            "namespace_digest": _digest("settle-live"),
            "authority": {
                "record_type": "outstanding_live_lifecycle",
                "target_date": seal.target_date,
                "old_live_epoch_id": projection.epoch_id,
                "issue_id": seal.issue_id,
                "issue_sha256": hashlib.sha256(issue_path.read_bytes()).hexdigest(),
                "input_manifest_sha256": seal.input_manifest_sha256,
                "seal_event": {
                    "sequence_id": seal.sequence_id,
                    "entry_sha256": seal.entry_sha256,
                    "event_type": seal.event_type,
                    "target_date": seal.target_date,
                    "issue_id": seal.issue_id,
                },
                "anchor_confirmed_event": {
                    "sequence_id": confirmed.sequence_id,
                    "entry_sha256": confirmed.entry_sha256,
                    "event_type": confirmed.event_type,
                    "target_date": confirmed.target_date,
                    "issue_id": confirmed.issue_id,
                },
                "frozen_live_upper_tip": confirmed.entry_sha256,
                "terminal": False,
                "action": "outcome_batch_settled",
            },
        }
        frozen = recovery.FrozenLivePrefix(
            profile=fixture.profile,
            paths=fixture.paths,
            prerequisites=prerequisites,
            projection=projection,
            frozen_events=frozen_events,
            current_events=current_events,
            expected_pre_head=recovery.live_cas.LiveLedgerPreHeadV1(
                epoch_id=projection.epoch_id,
                event_count=len(frozen_events),
                sequence_id=confirmed.sequence_id,
                entry_sha256=confirmed.entry_sha256,
            ),
        )
        return reservation, item, frozen

    def _prepare_linked_anchor_result(
        self,
        reservation: recovery.Reservation,
        *,
        deterministic_failure: bool,
    ) -> recovery.AnchorResultDispatchPlan:
        first = self._run(reservation)
        self.assertEqual(first.status, "recovery_step_completed")
        plan = self._run(reservation)
        self.assertIsInstance(plan, recovery.AnchorResultDispatchPlan)
        assert isinstance(plan, recovery.AnchorResultDispatchPlan)
        response = self._anchor_result_success_response(plan)
        if deterministic_failure:
            response = recovery.AnchorResultTransportResponse(
                body=response.body.replace(
                    plan.action_contract["request_body"][
                        "sealed_entry_sha256"
                    ].encode(),
                    b"f" * 64,
                ),
                status_code=response.status_code,
                media_type=response.media_type,
                charset=response.charset,
                content_encoding=response.content_encoding,
                final_url=response.final_url,
            )
        observation = recovery._anchor_result_observation_payload(  # noqa: SLF001
            plan, response, token=None
        )
        recovery._publish_anchor_result_observation(plan, observation)  # noqa: SLF001
        return plan

    def test_published_outstanding_outcome_is_consumed_as_exact_transaction(
        self,
    ) -> None:
        with _LiveFixture() as fixture:
            reservation, before, registered, _ = (
                self._published_outstanding_outcome_fixture(fixture)
            )

            result = self._run(reservation)

            after = tuple(fixture.events())
            committed = after[len(before) :]
            self.assertEqual(result.status, "recovery_item_completed")
            self.assertEqual(len(committed), 43)
            self.assertEqual(
                tuple(event.event_type for event in committed),
                recovery.OUTCOME_CONSUMPTION_EVENT_TYPES,
            )
            self.assertEqual(
                committed[-1].payload["outcome_batch_sha256"],
                registered.exact_object.sha256,
            )
            self.assertEqual(committed[-1].payload["source_revision_id"], "revision-1")
            self.assertIsNotNone(result.receipt_path)
            assert result.receipt_path is not None
            receipt = json.loads(result.receipt_path.read_bytes())
            self.assertEqual(receipt["action"], "outcome_or_revision_consumed")
            self.assertEqual(
                receipt["action_output_kind"],
                "live_outcome_consumption_transaction",
            )
            self.assertIsNone(receipt["action_output"])
            self.assertTrue(receipt["terminal_for_key"])
            self.assertEqual(receipt["next_actions"], [])
            semantics = receipt["action_semantics"]
            self.assertEqual(semantics["event_count"], 43)
            self.assertEqual(
                semantics["exact_outcome_sha256"], registered.exact_object.sha256
            )
            self.assertTrue(semantics["canonical_frozen_writer_reused"])
            self.assertTrue(semantics["contiguous_exact_slice_verified"])
            for claim in (
                "network_action_performed",
                "trusted_anchor_receipt_verified",
                "e2_live_evidence_eligible",
                "formal_warning_output",
            ):
                self.assertFalse(semantics[claim])

    def test_unpublished_receipt_tip_is_forward_reconciled_without_live_events(
        self,
    ) -> None:
        with _LiveFixture() as fixture:
            reservation, before, registered = self._unpublished_receipt_tip_fixture(
                fixture
            )
            profile = outcomes.load_config()
            pointer_path = outcomes._active_pointer_path(  # noqa: SLF001
                outcomes._runtime_path(  # noqa: SLF001
                    profile, fixture.root, "outcome_receipts"
                ),
                FIRST_TARGET,
            )
            active_path = outcomes._outcome_inbox_path(  # noqa: SLF001
                profile, fixture.root, FIRST_TARGET
            )
            self.assertFalse(pointer_path.exists())
            self.assertFalse(active_path.exists())

            result = self._run(reservation)

            self.assertEqual(result.status, "recovery_step_completed")
            self.assertEqual(tuple(fixture.events()), before)
            self.assertEqual(active_path.read_bytes(), registered.raw)
            pointer = json.loads(pointer_path.read_bytes())
            self.assertEqual(
                pointer["active_receipt"]["sha256"], registered.receipt.sha256
            )
            self.assertIsNotNone(result.receipt_path)
            assert result.receipt_path is not None
            receipt = json.loads(result.receipt_path.read_bytes())
            self.assertEqual(receipt["action"], "outcome_materialized")
            self.assertEqual(
                receipt["action_output_kind"], "outcome_materializer_publication"
            )
            self.assertEqual(
                receipt["action_output"]["sha256"],
                registered.exact_object.sha256,
            )
            self.assertFalse(receipt["terminal_for_key"])
            self.assertEqual(receipt["next_actions"], ["outcome_or_revision_consumed"])
            semantics = receipt["action_semantics"]
            self.assertEqual(semantics["selection_kind"], "outstanding")
            self.assertEqual(semantics["target_date"], FIRST_TARGET.isoformat())
            self.assertEqual(semantics["source_revision_id"], "revision-1")
            self.assertEqual(semantics["receipt_sha256"], registered.receipt.sha256)
            self.assertEqual(
                semantics["input_manifest_sha256"],
                registered.payload["source_manifest"]["sha256"],
            )
            self.assertEqual(
                semantics["exact_outcome_sha256"],
                registered.exact_object.sha256,
            )
            self.assertTrue(semantics["immutable_receipt_verified"])
            self.assertTrue(semantics["fully_published_verified"])
            self.assertFalse(semantics["live_ledger_mutation_performed"])
            self.assertFalse(semantics["network_action_performed"])

    def test_machine_selected_outcome_crash_adopts_one_registration(
        self,
    ) -> None:
        with _LiveFixture() as fixture:
            reservation, before, target, source = (
                self._machine_selected_source_outcome_fixture(fixture)
            )
            profile = outcomes.load_config()
            object_root = outcomes._runtime_path(  # noqa: SLF001
                profile, fixture.root, "objects"
            )
            receipt_root = outcomes._runtime_path(  # noqa: SLF001
                profile, fixture.root, "outcome_receipts"
            )
            pointer_path = outcomes._active_pointer_path(  # noqa: SLF001
                receipt_root, target
            )
            active_path = outcomes._outcome_inbox_path(  # noqa: SLF001
                profile, fixture.root, target
            )

            with (
                mock.patch.object(
                    recovery,
                    "_ensure_receipt",
                    side_effect=RuntimeError("synthetic post-publication crash"),
                ),
                self.assertRaisesRegex(RuntimeError, "post-publication crash"),
            ):
                self._run(reservation)

            self.assertEqual(tuple(fixture.events()), before)
            self.assertEqual(len(list(self.paths.item_intents.glob("*.json"))), 1)
            self.assertEqual(len(list(self.paths.receipts.glob("*.json"))), 0)
            immutable_receipts = sorted(
                path
                for path in (receipt_root / target.isoformat()).glob("*.json")
                if path.name != "active.json"
            )
            input_manifests = sorted(object_root.glob("*.outcome-input.json"))
            exact_outcomes = sorted(object_root.glob("*.outcome.json"))
            self.assertEqual(len(immutable_receipts), 1)
            self.assertEqual(len(input_manifests), 1)
            self.assertEqual(len(exact_outcomes), 1)
            self.assertTrue(pointer_path.is_file())
            self.assertTrue(active_path.is_file())
            publication_before = {
                path: path.read_bytes()
                for path in (
                    *immutable_receipts,
                    *input_manifests,
                    *exact_outcomes,
                    pointer_path,
                    active_path,
                )
            }

            with mock.patch.object(
                outcomes,
                "_create_or_validate_receipt",
                side_effect=AssertionError(
                    "a committed materializer receipt must be adopted"
                ),
            ) as register:
                result = self._run(reservation)

            register.assert_not_called()
            self.assertEqual(result.status, "recovery_step_completed")
            self.assertEqual(tuple(fixture.events()), before)
            self.assertEqual(
                sorted(
                    path
                    for path in (receipt_root / target.isoformat()).glob("*.json")
                    if path.name != "active.json"
                ),
                immutable_receipts,
            )
            self.assertEqual(
                sorted(object_root.glob("*.outcome-input.json")), input_manifests
            )
            self.assertEqual(sorted(object_root.glob("*.outcome.json")), exact_outcomes)
            for path, raw in publication_before.items():
                self.assertEqual(path.read_bytes(), raw)

            self.assertIsNotNone(result.receipt_path)
            assert result.receipt_path is not None
            receipt = json.loads(result.receipt_path.read_bytes())
            registered = json.loads(immutable_receipts[0].read_bytes())
            self.assertEqual(receipt["action"], "outcome_materialized")
            self.assertEqual(
                receipt["action_output_kind"], "outcome_materializer_publication"
            )
            self.assertEqual(
                receipt["action_output"]["sha256"],
                hashlib.sha256(exact_outcomes[0].read_bytes()).hexdigest(),
            )
            self.assertFalse(receipt["terminal_for_key"])
            self.assertEqual(receipt["next_actions"], ["outcome_or_revision_consumed"])
            semantics = receipt["action_semantics"]
            self.assertEqual(semantics["selection_kind"], "outstanding")
            self.assertEqual(semantics["target_date"], target.isoformat())
            self.assertEqual(
                semantics["source_revision_id"],
                source.records[-1].revision_id,
            )
            self.assertEqual(
                semantics["receipt_sha256"],
                hashlib.sha256(immutable_receipts[0].read_bytes()).hexdigest(),
            )
            self.assertEqual(
                semantics["input_manifest_sha256"],
                hashlib.sha256(input_manifests[0].read_bytes()).hexdigest(),
            )
            self.assertEqual(
                semantics["exact_outcome_sha256"],
                registered["exact_outcome_object"]["sha256"],
            )
            self.assertTrue(semantics["immutable_receipt_verified"])
            self.assertTrue(semantics["fully_published_verified"])
            self.assertFalse(semantics["live_ledger_mutation_performed"])
            self.assertFalse(semantics["network_action_performed"])

    def test_machine_selected_outcome_materializes_then_consumes_next_poll(
        self,
    ) -> None:
        with _LiveFixture() as fixture:
            reservation, before, _, _ = self._machine_selected_source_outcome_fixture(
                fixture
            )

            materialized = self._run(reservation)
            consumed = self._run(reservation)

            committed = tuple(fixture.events())[len(before) :]
            self.assertEqual(materialized.status, "recovery_step_completed")
            self.assertEqual(consumed.status, "recovery_item_completed")
            self.assertEqual(len(committed), 43)
            self.assertEqual(
                tuple(event.event_type for event in committed),
                recovery.OUTCOME_CONSUMPTION_EVENT_TYPES,
            )
            receipts = sorted(
                (
                    json.loads(path.read_bytes())
                    for path in self.paths.receipts.glob("*.json")
                ),
                key=lambda payload: payload["step_index"],
            )
            self.assertEqual(
                [receipt["action"] for receipt in receipts],
                ["outcome_materialized", "outcome_or_revision_consumed"],
            )
            self.assertFalse(receipts[0]["terminal_for_key"])
            self.assertTrue(receipts[1]["terminal_for_key"])
            self.assertEqual(
                receipts[1]["action_semantics"]["writer_branch"],
                "outstanding_settlement",
            )

    def test_pending_outcome_then_newer_source_materializes_as_revision(
        self,
    ) -> None:
        with _LiveFixture() as fixture:
            reservation, before, target, previous, source = (
                self._pending_previous_revision_fixture(fixture)
            )
            current_record = next(
                record for record in source.records if record.day == target
            )

            consumed = self._run(reservation)
            after_consumption = tuple(fixture.events())
            prerequisites = recovery.live.load_prerequisites(
                fixture.profile, fixture.paths
            )
            self.assertIsNotNone(prerequisites)
            assert prerequisites is not None
            settled_projection = recovery.live._reconstruct_projection(  # noqa: SLF001
                after_consumption, fixture.profile, prerequisites
            )
            settled_state_hashes = {
                station: recovery.live.station_state_sha256_v1(
                    settled_projection.states[station]
                )
                for station in fixture.stations
            }
            materialized = self._run(reservation)
            after_materialization = tuple(fixture.events())

            self.assertEqual(consumed.status, "recovery_item_completed")
            self.assertEqual(len(after_consumption) - len(before), 43)
            self.assertEqual(materialized.status, "recovery_step_completed")
            self.assertEqual(after_materialization, after_consumption)
            self.assertIsNotNone(materialized.receipt_path)
            assert materialized.receipt_path is not None
            materialization_receipt = json.loads(materialized.receipt_path.read_bytes())
            semantics = materialization_receipt["action_semantics"]
            self.assertEqual(materialization_receipt["action"], "outcome_materialized")
            self.assertEqual(semantics["selection_kind"], "revision")
            self.assertEqual(
                semantics["source_revision_id"], current_record.revision_id
            )

            profile = outcomes.load_config()
            prerequisites = recovery.live.load_prerequisites(
                fixture.profile, fixture.paths
            )
            self.assertIsNotNone(prerequisites)
            assert prerequisites is not None
            chain = outcomes._scan_receipt_chain(  # noqa: SLF001
                target=target,
                profile=profile,
                root=fixture.root,
                live_module=recovery.live,
                live_profile=fixture.profile,
                prerequisites=prerequisites,
            )
            self.assertIsNotNone(chain)
            assert chain is not None
            self.assertEqual(len(chain.receipts), 2)
            self.assertEqual(chain.tip.revision_sequence_id, 2)
            self.assertEqual(
                chain.tip.payload["source_revision_id"], current_record.revision_id
            )
            self.assertIsNotNone(chain.tip.previous_receipt)
            assert chain.tip.previous_receipt is not None
            self.assertEqual(
                chain.tip.previous_receipt.sha256,
                previous.receipt.sha256,
            )

            revision_consumed = self._run(reservation)

            after_revision = tuple(fixture.events())
            revision_events = after_revision[len(after_materialization) :]
            expected_types = tuple(
                event_type
                for _station in fixture.stations
                for event_type in (
                    "outcome_revision",
                    "revision_rescore_recorded",
                )
            )
            self.assertEqual(revision_consumed.status, "recovery_item_completed")
            self.assertEqual(len(revision_events), 16)
            self.assertEqual(
                tuple(event.event_type for event in revision_events), expected_types
            )
            for index, station in enumerate(fixture.stations):
                outcome_revision = revision_events[index * 2]
                rescore = revision_events[index * 2 + 1]
                self.assertEqual(outcome_revision.station, station)
                self.assertEqual(rescore.station, station)
                self.assertEqual(
                    outcome_revision.payload["source_revision_id"],
                    current_record.revision_id,
                )
                self.assertEqual(
                    outcome_revision.payload["outcome_batch_sha256"],
                    chain.tip.exact_object.sha256,
                )
                self.assertFalse(
                    outcome_revision.payload["live_online_state_rewritten"]
                )
                self.assertFalse(rescore.payload["updates_live_state"])
                self.assertFalse(rescore.payload["blind_metric_eligible"])
                self.assertEqual(
                    outcome_revision.state_before_sha256,
                    outcome_revision.state_after_sha256,
                )
                self.assertEqual(
                    rescore.state_before_sha256,
                    rescore.state_after_sha256,
                )
            revised_projection = recovery.live._reconstruct_projection(  # noqa: SLF001
                after_revision, fixture.profile, prerequisites
            )
            self.assertEqual(
                {
                    station: recovery.live.station_state_sha256_v1(
                        revised_projection.states[station]
                    )
                    for station in fixture.stations
                },
                settled_state_hashes,
            )
            self.assertIsNotNone(revision_consumed.receipt_path)
            assert revision_consumed.receipt_path is not None
            consumption_receipt = json.loads(
                revision_consumed.receipt_path.read_bytes()
            )
            self.assertEqual(
                consumption_receipt["action"], "outcome_or_revision_consumed"
            )
            self.assertEqual(
                consumption_receipt["action_output_kind"],
                "live_outcome_consumption_transaction",
            )
            self.assertTrue(consumption_receipt["terminal_for_key"])
            self.assertEqual(consumption_receipt["next_actions"], [])
            consumption_semantics = consumption_receipt["action_semantics"]
            self.assertEqual(consumption_semantics["writer_branch"], "settled_revision")
            self.assertEqual(consumption_semantics["event_count"], 16)
            self.assertEqual(
                consumption_semantics["source_revision_id"],
                current_record.revision_id,
            )
            self.assertEqual(
                consumption_semantics["previous_revision_id"],
                previous.payload["source_revision_id"],
            )
            self.assertEqual(
                consumption_semantics["previous_outcome_sha256"],
                previous.exact_object.sha256,
            )
            self.assertEqual(
                consumption_semantics["exact_outcome_sha256"],
                chain.tip.exact_object.sha256,
            )
            self.assertTrue(consumption_semantics["live_ledger_events_recorded"])
            self.assertTrue(consumption_semantics["canonical_frozen_writer_reused"])
            self.assertTrue(consumption_semantics["contiguous_exact_slice_verified"])
            self.assertTrue(consumption_semantics["revised_retrospective_view"])
            self.assertFalse(consumption_semantics["live_online_state_rewritten"])
            self.assertFalse(consumption_semantics["blind_metric_eligible"])
            self.assertFalse(consumption_semantics["network_action_performed"])

            fixture.write_issue(
                target + timedelta(days=1),
                persistence=current_record.displacement_mm,
            )
            fixture.poll(now=datetime(2020, 7, 2, 15, 45, tzinfo=timezone.utc))
            with_suffix = tuple(fixture.events())
            self.assertGreater(len(with_suffix), len(after_revision))
            recovery_receipts_before = {
                path.name: path.read_bytes()
                for path in self.paths.receipts.glob("*.json")
            }
            recovery_events_before = {
                path.name: path.read_bytes()
                for path in self.paths.events.glob("*.json")
            }
            with mock.patch.object(
                recovery,
                "_outcome_consumption_action",
                side_effect=AssertionError(
                    "completed revision verification must be read-only"
                ),
            ) as action:
                replayed = self._run(reservation)

            action.assert_not_called()
            self.assertEqual(replayed.status, "waiting_for_supported_ready_key")
            self.assertEqual(tuple(fixture.events()), with_suffix)
            self.assertEqual(
                {
                    path.name: path.read_bytes()
                    for path in self.paths.receipts.glob("*.json")
                },
                recovery_receipts_before,
            )
            self.assertEqual(
                {
                    path.name: path.read_bytes()
                    for path in self.paths.events.glob("*.json")
                },
                recovery_events_before,
            )

    def test_revision_consumption_commit_before_receipt_is_exactly_adopted(
        self,
    ) -> None:
        with _LiveFixture() as fixture:
            reservation, before, target, previous, source = (
                self._pending_previous_revision_fixture(fixture)
            )
            current_record = next(
                record for record in source.records if record.day == target
            )
            rev1_consumed = self._run(reservation)
            materialized = self._run(reservation)
            before_revision = tuple(fixture.events())
            self.assertEqual(rev1_consumed.status, "recovery_item_completed")
            self.assertEqual(materialized.status, "recovery_step_completed")
            self.assertEqual(len(before_revision) - len(before), 43)

            with (
                mock.patch.object(
                    recovery,
                    "_ensure_receipt",
                    side_effect=RuntimeError("synthetic revision post-CAS crash"),
                ),
                self.assertRaisesRegex(RuntimeError, "revision post-CAS crash"),
            ):
                self._run(reservation)

            committed = tuple(fixture.events())
            revision_events = committed[len(before_revision) :]
            self.assertEqual(len(revision_events), 16)
            self.assertEqual(
                tuple(event.event_type for event in revision_events),
                tuple(
                    event_type
                    for _station in fixture.stations
                    for event_type in (
                        "outcome_revision",
                        "revision_rescore_recorded",
                    )
                ),
            )
            self.assertEqual(
                {event.payload["source_revision_id"] for event in revision_events},
                {current_record.revision_id},
            )

            with mock.patch.object(
                recovery.live_cas,
                "append_transaction_at_pre_head_v1",
                side_effect=AssertionError(
                    "committed revision intent must be receipt-only"
                ),
            ) as ledger_cas:
                adopted = self._run(reservation)

            ledger_cas.assert_not_called()
            self.assertEqual(adopted.status, "recovery_item_completed")
            self.assertEqual(tuple(fixture.events()), committed)
            self.assertIsNotNone(adopted.receipt_path)
            assert adopted.receipt_path is not None
            receipt = json.loads(adopted.receipt_path.read_bytes())
            self.assertTrue(receipt["terminal_for_key"])
            self.assertEqual(receipt["next_actions"], [])
            semantics = receipt["action_semantics"]
            self.assertEqual(semantics["writer_branch"], "settled_revision")
            self.assertEqual(semantics["event_count"], 16)
            self.assertEqual(
                semantics["previous_revision_id"],
                previous.payload["source_revision_id"],
            )
            self.assertEqual(
                semantics["previous_outcome_sha256"],
                previous.exact_object.sha256,
            )
            self.assertTrue(semantics["contiguous_exact_slice_verified"])

    def test_backfill_revision_is_consumed_by_canonical_writer(self) -> None:
        with _LiveFixture() as fixture:
            reservation, before, first, second, prerequisites = (
                self._published_backfill_revision_fixture(fixture)
            )
            before_projection = recovery.live._reconstruct_projection(  # noqa: SLF001
                before, fixture.profile, prerequisites
            )
            target_text = FIRST_TARGET.isoformat()
            original_backfill = before_projection.backfill_events[target_text]
            original_states = dict(before_projection.states)
            first_outcome = recovery.live.load_outcome_batch(
                first.exact_object.path, fixture.profile, prerequisites
            )
            second_outcome = recovery.live.load_outcome_batch(
                second.exact_object.path, fixture.profile, prerequisites
            )

            consumed = self._run(reservation)

            committed = tuple(fixture.events())
            revision_events = committed[len(before) :]
            self.assertEqual(consumed.status, "recovery_item_completed")
            self.assertEqual(len(revision_events), 8)
            self.assertEqual(
                tuple(event.event_type for event in revision_events),
                ("outcome_revision",) * 8,
            )
            for event, station in zip(revision_events, fixture.stations, strict=True):
                self.assertEqual(event.station, station)
                self.assertEqual(
                    event.event_key,
                    (
                        f"{before_projection.epoch_id}:{target_text}:revision:"
                        f"{second.payload['source_revision_id']}:"
                        f"outcome_revision:{station}"
                    ),
                )
                self.assertIsNone(event.issue_id)
                self.assertEqual(
                    event.payload["source_revision_id"],
                    second.payload["source_revision_id"],
                )
                self.assertEqual(
                    event.payload["outcome_batch_sha256"],
                    second.exact_object.sha256,
                )
                self.assertEqual(
                    event.payload["previous_actual_mm"],
                    first_outcome.actual_by_station[station],
                )
                self.assertEqual(
                    event.payload["revised_actual_mm"],
                    second_outcome.actual_by_station[station],
                )
                self.assertEqual(
                    event.payload["original_classification"],
                    "backfill_not_blind",
                )
                self.assertFalse(event.payload["retrospective_score_available"])
                self.assertFalse(event.payload["live_online_state_rewritten"])
                self.assertEqual(event.state_before_sha256, event.state_after_sha256)

            after_projection = recovery.live._reconstruct_projection(  # noqa: SLF001
                committed, fixture.profile, prerequisites
            )
            self.assertEqual(
                after_projection.backfill_events[target_text], original_backfill
            )
            self.assertNotIn(target_text, after_projection.settled_events)
            self.assertEqual(dict(after_projection.states), original_states)
            self.assertEqual(
                after_projection.last_finalized_date,
                before_projection.last_finalized_date,
            )
            self.assertEqual(
                after_projection.outstanding_target_date,
                before_projection.outstanding_target_date,
            )
            self.assertEqual(
                after_projection.revision_ids[target_text],
                {
                    first.payload["source_revision_id"]: first.exact_object.sha256,
                    second.payload["source_revision_id"]: second.exact_object.sha256,
                },
            )
            self.assertEqual(
                dict(after_projection.latest_actuals_by_date[target_text]),
                second_outcome.actual_by_station,
            )
            self.assertEqual(
                dict(after_projection.latest_displacement_mm),
                second_outcome.actual_by_station,
            )
            self.assertEqual(
                after_projection.backfill_count, before_projection.backfill_count
            )

            self.assertIsNotNone(consumed.receipt_path)
            assert consumed.receipt_path is not None
            receipt = json.loads(consumed.receipt_path.read_bytes())
            self.assertEqual(receipt["action"], "outcome_or_revision_consumed")
            self.assertEqual(
                receipt["action_output_kind"],
                "live_outcome_consumption_transaction",
            )
            self.assertTrue(receipt["terminal_for_key"])
            self.assertEqual(receipt["next_actions"], [])
            semantics = receipt["action_semantics"]
            self.assertEqual(
                semantics["schema_version"],
                "ootang_live_backfill_revision_consumption_action_output_v1",
            )
            self.assertEqual(semantics["writer_branch"], "backfill_revision")
            self.assertEqual(semantics["event_count"], 8)
            self.assertEqual(
                semantics["previous_revision_id"],
                first.payload["source_revision_id"],
            )
            self.assertEqual(
                semantics["previous_outcome_sha256"], first.exact_object.sha256
            )
            self.assertEqual(
                semantics["exact_outcome_sha256"], second.exact_object.sha256
            )
            self.assertEqual(semantics["original_classification"], "backfill_not_blind")
            self.assertFalse(semantics["retrospective_score_available"])
            self.assertFalse(semantics["live_online_state_rewritten"])
            self.assertFalse(semantics["blind_metric_eligible"])
            self.assertTrue(semantics["latest_persistence_baseline_updated"])
            self.assertTrue(semantics["canonical_frozen_writer_reused"])
            self.assertTrue(semantics["contiguous_exact_slice_verified"])

    def test_backfill_revision_commit_before_receipt_is_exactly_adopted(
        self,
    ) -> None:
        with _LiveFixture() as fixture:
            reservation, before, first, second, _ = (
                self._published_backfill_revision_fixture(fixture)
            )

            with (
                mock.patch.object(
                    recovery,
                    "_ensure_receipt",
                    side_effect=RuntimeError(
                        "synthetic backfill revision post-CAS crash"
                    ),
                ),
                self.assertRaisesRegex(
                    RuntimeError, "backfill revision post-CAS crash"
                ),
            ):
                self._run(reservation)

            committed = tuple(fixture.events())
            revision_events = committed[len(before) :]
            self.assertEqual(len(revision_events), 8)
            self.assertEqual(
                tuple(event.event_type for event in revision_events),
                ("outcome_revision",) * 8,
            )
            self.assertEqual(
                {event.payload["source_revision_id"] for event in revision_events},
                {second.payload["source_revision_id"]},
            )

            with mock.patch.object(
                recovery.live_cas,
                "append_transaction_at_pre_head_v1",
                side_effect=AssertionError(
                    "committed backfill revision intent must be receipt-only"
                ),
            ) as ledger_cas:
                adopted = self._run(reservation)

            ledger_cas.assert_not_called()
            self.assertEqual(adopted.status, "recovery_item_completed")
            self.assertEqual(tuple(fixture.events()), committed)
            self.assertIsNotNone(adopted.receipt_path)
            assert adopted.receipt_path is not None
            receipt = json.loads(adopted.receipt_path.read_bytes())
            self.assertTrue(receipt["terminal_for_key"])
            self.assertEqual(receipt["next_actions"], [])
            semantics = receipt["action_semantics"]
            self.assertEqual(
                semantics["schema_version"],
                "ootang_live_backfill_revision_consumption_action_output_v1",
            )
            self.assertEqual(semantics["writer_branch"], "backfill_revision")
            self.assertEqual(semantics["event_count"], 8)
            self.assertEqual(
                semantics["previous_revision_id"],
                first.payload["source_revision_id"],
            )
            self.assertEqual(
                semantics["previous_outcome_sha256"], first.exact_object.sha256
            )
            self.assertTrue(semantics["contiguous_exact_slice_verified"])

    def test_materialization_crash_adopts_after_pointer_advances_then_consumes(
        self,
    ) -> None:
        with _LiveFixture() as fixture:
            reservation, before, first = self._unpublished_receipt_tip_fixture(fixture)
            prerequisites = recovery.live.load_prerequisites(
                fixture.profile, fixture.paths
            )
            self.assertIsNotNone(prerequisites)
            assert prerequisites is not None

            with (
                mock.patch.object(
                    recovery,
                    "_ensure_receipt",
                    side_effect=RuntimeError("synthetic post-materialization crash"),
                ),
                self.assertRaisesRegex(RuntimeError, "post-materialization crash"),
            ):
                self._run(reservation)

            newer = self._publish_newer_materializer_revision(
                fixture, prerequisites, first
            )
            profile = outcomes.load_config()
            active_path = outcomes._outcome_inbox_path(  # noqa: SLF001
                profile, fixture.root, FIRST_TARGET
            )
            pointer_path = outcomes._active_pointer_path(  # noqa: SLF001
                outcomes._runtime_path(  # noqa: SLF001
                    profile, fixture.root, "outcome_receipts"
                ),
                FIRST_TARGET,
            )
            newer_publication = {
                active_path: active_path.read_bytes(),
                pointer_path: pointer_path.read_bytes(),
            }
            self.assertEqual(active_path.read_bytes(), newer.raw)

            with mock.patch.object(
                outcomes,
                "_reconcile_chain",
                side_effect=AssertionError(
                    "historical materialization must not rewrite the current tip"
                ),
            ) as reconcile:
                adopted = self._run(reservation)

            reconcile.assert_not_called()
            self.assertEqual(adopted.status, "recovery_step_completed")
            self.assertEqual(tuple(fixture.events()), before)
            for path, raw in newer_publication.items():
                self.assertEqual(path.read_bytes(), raw)

            consumed = self._run(reservation)

            committed = tuple(fixture.events())[len(before) :]
            self.assertEqual(consumed.status, "recovery_item_completed")
            self.assertEqual(len(committed), 43)
            self.assertEqual(
                committed[-1].payload["outcome_batch_sha256"],
                first.exact_object.sha256,
            )
            self.assertNotEqual(
                committed[-1].payload["outcome_batch_sha256"],
                newer.exact_object.sha256,
            )
            for path, raw in newer_publication.items():
                self.assertEqual(path.read_bytes(), raw)

    def test_repaired_already_consumed_tip_is_terminally_adopted_without_cas(
        self,
    ) -> None:
        with _LiveFixture() as fixture:
            reservation, _, registered, _ = self._published_outstanding_outcome_fixture(
                fixture
            )
            contract = recovery._outcome_consumption_contract(  # noqa: SLF001
                reservation.manifest["items"][0], reservation
            )
            recovery._outcome_consumption_action(  # noqa: SLF001
                reservation.manifest["items"][0], reservation, contract
            )
            consumed_events = tuple(fixture.events())
            consumed_head = consumed_events[-1]
            self.assertEqual(consumed_head.event_type, "outcome_batch_settled")

            profile = outcomes.load_config()
            pointer_path = outcomes._active_pointer_path(  # noqa: SLF001
                outcomes._runtime_path(  # noqa: SLF001
                    profile, fixture.root, "outcome_receipts"
                ),
                FIRST_TARGET,
            )
            active_path = outcomes._outcome_inbox_path(  # noqa: SLF001
                profile, fixture.root, FIRST_TARGET
            )
            pointer_path.unlink()
            active_path.unlink()
            item = reservation.manifest["items"][0]
            item["canonical_successor_state"] = "outcome_materialized"
            item["artifacts"] = [
                artifact
                for artifact in item["artifacts"]
                if artifact["role"]
                in {
                    "outcome_receipt",
                    "exact_outcome_object",
                    "outcome_source_manifest",
                }
            ]
            item["authority"]["tip_published"] = False
            item["authority"]["tip_ledger_consumed"] = True
            item["authority"]["receipts"][0]["ledger_consumed"] = True
            item["authority"]["action"] = "outcome_materialized"
            reservation.manifest["frozen_live_upper_tip"] = {
                "old_live_epoch_id": item["authority"]["old_live_epoch_id"],
                "live_event_count": len(consumed_events),
                "live_terminal_sha256": consumed_head.entry_sha256,
            }

            materialized = self._run(reservation)
            with mock.patch.object(
                recovery.live_cas,
                "append_transaction_at_pre_head_v1",
                side_effect=AssertionError(
                    "a frozen consumed batch must be receipt-only adoption"
                ),
            ) as ledger_cas:
                adopted = self._run(reservation)

            ledger_cas.assert_not_called()
            self.assertEqual(materialized.status, "recovery_step_completed")
            self.assertEqual(adopted.status, "recovery_item_completed")
            self.assertEqual(tuple(fixture.events()), consumed_events)
            self.assertEqual(
                json.loads(adopted.receipt_path.read_bytes())["action_semantics"][  # type: ignore[union-attr]
                    "writer_branch"
                ],
                "preexisting_consumed_adoption",
            )
            self.assertEqual(
                consumed_head.payload["outcome_batch_sha256"],
                registered.exact_object.sha256,
            )

    def test_outcome_consumption_commit_before_receipt_is_exactly_adopted(
        self,
    ) -> None:
        with _LiveFixture() as fixture:
            reservation, before, first, prerequisites = (
                self._published_outstanding_outcome_fixture(fixture)
            )
            with (
                mock.patch.object(
                    recovery,
                    "_ensure_receipt",
                    side_effect=RuntimeError("synthetic post-CAS crash"),
                ),
                self.assertRaisesRegex(RuntimeError, "post-CAS crash"),
            ):
                self._run(reservation)
            committed = tuple(fixture.events())
            self.assertEqual(len(committed) - len(before), 43)
            self.assertEqual(
                sum(event.event_type == "outcome_batch_settled" for event in committed),
                1,
            )
            self.assertEqual(len(list(self.paths.item_intents.glob("*.json"))), 1)
            self.assertEqual(len(list(self.paths.receipts.glob("*.json"))), 0)

            newer = self._publish_newer_materializer_revision(
                fixture, prerequisites, first
            )
            profile = outcomes.load_config()
            active_path = outcomes._outcome_inbox_path(  # noqa: SLF001
                profile, fixture.root, FIRST_TARGET
            )
            pointer_path = outcomes._active_pointer_path(  # noqa: SLF001
                outcomes._runtime_path(  # noqa: SLF001
                    profile, fixture.root, "outcome_receipts"
                ),
                FIRST_TARGET,
            )
            published_revision_two = {
                active_path: active_path.read_bytes(),
                pointer_path: pointer_path.read_bytes(),
            }
            self.assertEqual(active_path.read_bytes(), newer.raw)
            with mock.patch.object(
                recovery.live_cas,
                "append_transaction_at_pre_head_v1",
                side_effect=AssertionError(
                    "committed pending intent must be receipt-only"
                ),
            ) as ledger_cas:
                result = self._run(reservation)

            ledger_cas.assert_not_called()
            self.assertEqual(result.status, "recovery_item_completed")
            self.assertEqual(tuple(fixture.events()), committed)
            self.assertEqual(len(list(self.paths.receipts.glob("*.json"))), 1)
            self.assertEqual(len(list(self.paths.events.glob("*.json"))), 1)
            assert result.receipt_path is not None
            receipt = json.loads(result.receipt_path.read_bytes())
            self.assertEqual(
                receipt["action_semantics"]["tip_receipt_sha256"],
                first.receipt.sha256,
            )
            self.assertEqual(
                receipt["action_semantics"]["exact_outcome_sha256"],
                first.exact_object.sha256,
            )
            self.assertNotEqual(
                receipt["action_semantics"]["exact_outcome_sha256"],
                newer.exact_object.sha256,
            )
            for path, raw in published_revision_two.items():
                self.assertEqual(path.read_bytes(), raw)
            projection = recovery.live._reconstruct_projection(  # noqa: SLF001
                tuple(fixture.events()), fixture.profile, prerequisites
            )
            known = projection.revision_ids[FIRST_TARGET.isoformat()]
            self.assertEqual(known, {"revision-1": first.exact_object.sha256})
            self.assertNotIn("revision-2", known)

    def test_outcome_consumption_intent_rejects_legal_foreign_suffix(
        self,
    ) -> None:
        with _LiveFixture() as fixture:
            reservation, frozen_events, _, prerequisites = (
                self._published_outstanding_outcome_fixture(fixture)
            )

            def stop_after_intent(*_args, **_kwargs):  # type: ignore[no-untyped-def]
                raise RuntimeError("synthetic action pause")

            with self.assertRaisesRegex(RuntimeError, "action pause"):
                self._run(reservation, stop_after_intent)
            self.assertEqual(tuple(fixture.events()), frozen_events)
            self.assertEqual(len(list(self.paths.item_intents.glob("*.json"))), 1)

            projection = recovery.live._reconstruct_projection(  # noqa: SLF001
                frozen_events, fixture.profile, prerequisites
            )
            seal = projection.seal_event
            self.assertIsNotNone(seal)
            assert seal is not None
            attempt = 1 + sum(
                event.event_type == "anchor_requested" for event in frozen_events
            )
            request_payload = {
                "live_epoch_id": projection.epoch_id,
                "target_date": seal.target_date,
                "sealed_sequence_id": seal.sequence_id,
                "sealed_entry_sha256": seal.entry_sha256,
                "attempt": attempt,
            }
            foreign = recovery.live._event_spec(  # noqa: SLF001
                event_key=(
                    f"{projection.epoch_id}:{seal.target_date}:"
                    f"anchor:{attempt}:requested"
                ),
                event_type="anchor_requested",
                prerequisites=prerequisites,
                payload=request_payload,
                target_date_value=FIRST_TARGET,
                issue_id=seal.issue_id,
                input_manifest_sha256=seal.input_manifest_sha256,
                state_before_sha256=seal.state_after_sha256,
                state_after_sha256=seal.state_after_sha256,
            )
            live_ledger.AppendOnlyLedger(fixture.paths.ledger).append_transaction(
                [foreign]
            )
            with_suffix = tuple(fixture.events())
            self.assertEqual(len(with_suffix), len(frozen_events) + 1)
            self.assertEqual(with_suffix[-1].event_type, "anchor_requested")
            recovery.live._reconstruct_projection(  # noqa: SLF001
                with_suffix, fixture.profile, prerequisites
            )

            with self.assertRaisesRegex(
                recovery.WorksetRecoveryIntegrityError,
                "Outcome consumption item intent contract changed",
            ):
                self._run(reservation)

            self.assertEqual(tuple(fixture.events()), with_suffix)
            self.assertFalse(
                any(
                    event.event_type == "outcome_batch_opened"
                    for event in fixture.events()
                )
            )
            self.assertEqual(len(list(self.paths.receipts.glob("*.json"))), 0)
            self.assertEqual(len(list(self.paths.events.glob("*.json"))), 0)

    def test_recorded_outcome_consumption_never_reappends_a_missing_slice(
        self,
    ) -> None:
        with _LiveFixture() as fixture:
            reservation, before, _, _ = self._published_outstanding_outcome_fixture(
                fixture
            )
            result = self._run(reservation)
            self.assertEqual(result.status, "recovery_item_completed")
            self.assertEqual(len(fixture.events()) - len(before), 43)
            intent_path = next(self.paths.item_intents.glob("*.json"))
            intent = json.loads(intent_path.read_bytes())
            pre_head_count = intent["action_contract"]["expected_pre_head"][
                "event_count"
            ]

            connection = sqlite3.connect(fixture.paths.ledger)
            try:
                connection.execute("DROP TRIGGER events_no_delete")
                deleted = connection.execute(
                    "DELETE FROM events WHERE sequence_id > ?", (pre_head_count,)
                ).rowcount
                connection.execute(live_ledger._NO_DELETE_TRIGGER_SQL)  # noqa: SLF001
                connection.commit()
            finally:
                connection.close()
            self.assertEqual(deleted, 43)
            rolled_back = tuple(fixture.events())
            self.assertEqual(len(rolled_back), pre_head_count)

            with (
                mock.patch.object(
                    recovery,
                    "_outcome_consumption_action",
                    side_effect=AssertionError(
                        "historical verification must never write"
                    ),
                ) as action,
                self.assertRaisesRegex(
                    recovery.WorksetRecoveryIntegrityError,
                    "ledger slice disappeared",
                ),
            ):
                self._run(reservation)

            action.assert_not_called()
            self.assertEqual(tuple(fixture.events()), rolled_back)
            self.assertFalse(
                any(
                    event.event_type == "outcome_batch_opened"
                    for event in fixture.events()
                )
            )

    def test_recorded_outcome_consumption_ignores_a_newer_active_revision(
        self,
    ) -> None:
        with _LiveFixture() as fixture:
            reservation, _, first, prerequisites = (
                self._published_outstanding_outcome_fixture(fixture)
            )
            completed = self._run(reservation)
            self.assertEqual(completed.status, "recovery_item_completed")
            ledger_before = tuple(fixture.events())
            immutable_before = {
                first.receipt.path: first.receipt.path.read_bytes(),
                first.exact_object.path: first.exact_object.path.read_bytes(),
                Path(first.payload["source_manifest"]["path"]): Path(
                    first.payload["source_manifest"]["path"]
                ).read_bytes(),
            }
            profile = outcomes.load_config()
            pointer_path = outcomes._active_pointer_path(  # noqa: SLF001
                outcomes._runtime_path(  # noqa: SLF001
                    profile, fixture.root, "outcome_receipts"
                ),
                FIRST_TARGET,
            )
            first_pointer_raw = pointer_path.read_bytes()

            newer = self._publish_newer_materializer_revision(
                fixture, prerequisites, first
            )
            active_path = outcomes._outcome_inbox_path(  # noqa: SLF001
                profile, fixture.root, FIRST_TARGET
            )
            self.assertEqual(active_path.read_bytes(), newer.raw)
            self.assertNotEqual(active_path.read_bytes(), first.raw)
            self.assertNotEqual(pointer_path.read_bytes(), first_pointer_raw)
            self.assertEqual(
                json.loads(pointer_path.read_bytes())["active_receipt"]["sha256"],
                newer.receipt.sha256,
            )
            mutable_before = {
                active_path: active_path.read_bytes(),
                pointer_path: pointer_path.read_bytes(),
            }
            recovery_receipts_before = {
                path.name: path.read_bytes()
                for path in self.paths.receipts.glob("*.json")
            }
            recovery_events_before = {
                path.name: path.read_bytes()
                for path in self.paths.events.glob("*.json")
            }

            with mock.patch.object(
                recovery,
                "_outcome_consumption_action",
                side_effect=AssertionError("historical verification must be read-only"),
            ) as action:
                replayed = self._run(reservation)

            action.assert_not_called()
            self.assertEqual(replayed.status, "waiting_for_supported_ready_key")
            self.assertEqual(tuple(fixture.events()), ledger_before)
            for path, raw in immutable_before.items():
                self.assertEqual(path.read_bytes(), raw)
            for path, raw in mutable_before.items():
                self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(
                {
                    path.name: path.read_bytes()
                    for path in self.paths.receipts.glob("*.json")
                },
                recovery_receipts_before,
            )
            self.assertEqual(
                {
                    path.name: path.read_bytes()
                    for path in self.paths.events.glob("*.json")
                },
                recovery_events_before,
            )

    def test_outcome_settlement_adopts_complete_transaction_without_writing(
        self,
    ) -> None:
        with _LiveFixture() as fixture:
            reservation, item, frozen = self._outcome_settlement_fixture(
                fixture, write_outcome=True
            )
            before = tuple(fixture.events())
            with mock.patch.object(
                recovery, "_frozen_live_prefix", return_value=frozen
            ):
                contract = recovery._outcome_settlement_adoption_contract(  # noqa: SLF001
                    item, reservation
                )
                output = recovery._outcome_settlement_adoption_action(  # noqa: SLF001
                    item, reservation, contract
                )

            self.assertEqual(tuple(fixture.events()), before)
            self.assertEqual(contract["event_count"], 43)
            first_sequence = contract["first_event"][  # type: ignore[index]
                "sequence_id"
            ]
            transaction_predecessor = before[first_sequence - 2]
            self.assertEqual(
                contract["expected_pre_head"],
                {
                    "epoch_id": frozen.projection.epoch_id,
                    "event_count": transaction_predecessor.sequence_id,
                    "sequence_id": transaction_predecessor.sequence_id,
                    "entry_sha256": transaction_predecessor.entry_sha256,
                },
            )
            self.assertEqual(
                first_sequence,
                contract["confirmation_event"]["sequence_id"] + 1,  # type: ignore[index,operator]
            )
            self.assertEqual(
                contract["terminal_event"]["sequence_id"],  # type: ignore[index]
                contract["confirmation_event"]["sequence_id"] + 43,  # type: ignore[index,operator]
            )
            self.assertEqual(output.kind, "live_outcome_settlement_transaction")
            self.assertIsNone(output.reference)
            self.assertEqual(output.semantics["event_count"], 43)
            self.assertTrue(output.semantics["live_ledger_events_recorded"])
            self.assertTrue(output.semantics["contiguous_exact_slice_verified"])
            for claim in (
                "network_action_performed",
                "trusted_anchor_receipt_verified",
                "e2_live_evidence_eligible",
                "formal_warning_output",
            ):
                self.assertFalse(output.semantics[claim])

    def test_outcome_settlement_adopts_after_manifest_frozen_anchor_request(
        self,
    ) -> None:
        with _LiveFixture() as fixture:
            reservation, item, confirmed_frozen = self._outcome_settlement_fixture(
                fixture, write_outcome=True
            )
            confirmed = confirmed_frozen.frozen_events[-1]
            requested = confirmed_frozen.frozen_events[-2]
            self.assertEqual(requested.event_type, "anchor_requested")
            requested_events = confirmed_frozen.frozen_events[:-1]
            requested_projection = recovery.live._reconstruct_projection(  # noqa: SLF001
                requested_events,
                confirmed_frozen.profile,
                confirmed_frozen.prerequisites,
            )
            requested_frozen = recovery.FrozenLivePrefix(
                profile=confirmed_frozen.profile,
                paths=confirmed_frozen.paths,
                prerequisites=confirmed_frozen.prerequisites,
                projection=requested_projection,
                frozen_events=requested_events,
                current_events=confirmed_frozen.current_events,
                expected_pre_head=recovery.live_cas.LiveLedgerPreHeadV1(
                    epoch_id=requested_projection.epoch_id,
                    event_count=len(requested_events),
                    sequence_id=requested.sequence_id,
                    entry_sha256=requested.entry_sha256,
                ),
            )
            reservation.manifest["frozen_live_upper_tip"] = {
                "old_live_epoch_id": requested_projection.epoch_id,
                "live_event_count": len(requested_events),
                "live_terminal_sha256": requested.entry_sha256,
            }
            authority = dict(item["authority"])  # type: ignore[arg-type]
            authority.update(
                {
                    "anchor_confirmed_event": None,
                    "frozen_live_upper_tip": requested.entry_sha256,
                    "action": "anchor_result_recorded",
                }
            )
            item = {**item, "authority": authority}
            previous_result_receipt = {
                "action": "anchor_result_recorded",
                "action_output_kind": "live_anchor_result_event",
                "action_output": None,
                "next_actions": ["outcome_batch_settled"],
                "terminal_for_key": False,
                "action_semantics": {
                    "schema_version": "ootang_live_anchor_result_action_output_v1",
                    "result_outcome": "candidate_confirmed",
                    "event_type": "anchor_confirmed",
                    "selected_next_action": "outcome_batch_settled",
                    "sealed_entry_sha256": requested_projection.seal_event.entry_sha256,
                    "live_epoch_id": requested_projection.epoch_id,
                    "live_ledger_event_recorded": True,
                    "trusted_anchor_receipt_verified": False,
                    "e2_live_evidence_eligible": False,
                    "sequence_id": confirmed.sequence_id,
                    "entry_sha256": confirmed.entry_sha256,
                    "previous_entry_sha256": confirmed.previous_entry_sha256,
                    "event_key": confirmed.event_key,
                },
            }

            with mock.patch.object(
                recovery, "_frozen_live_prefix", return_value=requested_frozen
            ):
                contract = recovery._outcome_settlement_adoption_contract(  # noqa: SLF001
                    item,
                    reservation,
                    previous_result_receipt,
                )

            self.assertEqual(contract["event_count"], 43)
            first_sequence = contract["first_event"][  # type: ignore[index]
                "sequence_id"
            ]
            transaction_predecessor = requested_frozen.current_events[
                first_sequence - 2
            ]
            self.assertEqual(
                contract["expected_pre_head"],
                {
                    "epoch_id": requested_projection.epoch_id,
                    "event_count": transaction_predecessor.sequence_id,
                    "sequence_id": transaction_predecessor.sequence_id,
                    "entry_sha256": transaction_predecessor.entry_sha256,
                },
            )

    def test_outcome_settlement_allows_revisions_after_confirmation(self) -> None:
        with _LiveFixture() as fixture:
            reservation, item, frozen = self._outcome_settlement_fixture(
                fixture,
                write_outcome=True,
                intervening_revision=True,
            )
            with mock.patch.object(
                recovery, "_frozen_live_prefix", return_value=frozen
            ):
                contract = recovery._outcome_settlement_adoption_contract(  # noqa: SLF001
                    item, reservation
                )

            confirmation_sequence = contract["confirmation_event"][  # type: ignore[index]
                "sequence_id"
            ]
            pre_head_sequence = contract["expected_pre_head"][  # type: ignore[index]
                "sequence_id"
            ]
            self.assertGreater(pre_head_sequence, confirmation_sequence)
            self.assertEqual(
                [
                    event.event_type
                    for event in frozen.current_events[
                        confirmation_sequence:pre_head_sequence
                    ]
                ],
                ["outcome_revision"] * 8,
            )

    def test_outcome_settlement_waits_until_transaction_is_durable(self) -> None:
        with _LiveFixture() as fixture:
            reservation, item, frozen = self._outcome_settlement_fixture(
                fixture, write_outcome=False
            )
            before = tuple(fixture.events())
            with (
                mock.patch.object(recovery, "_frozen_live_prefix", return_value=frozen),
                self.assertRaisesRegex(
                    recovery.WorksetRecoveryExternalWait,
                    "no durable settlement transaction yet",
                ),
            ):
                recovery._outcome_settlement_adoption_contract(  # noqa: SLF001
                    item, reservation
                )

            self.assertEqual(tuple(fixture.events()), before)

    def test_outcome_settlement_rejects_partial_transaction_suffix(self) -> None:
        with _LiveFixture() as fixture:
            reservation, item, frozen = self._outcome_settlement_fixture(
                fixture, write_outcome=True
            )
            settled = next(
                event
                for event in frozen.current_events
                if event.event_type == "outcome_batch_settled"
            )
            first_index = settled.sequence_id - 43
            partial_events = list(frozen.current_events)
            del partial_events[first_index + 1]
            partial = recovery.FrozenLivePrefix(
                profile=frozen.profile,
                paths=frozen.paths,
                prerequisites=frozen.prerequisites,
                projection=frozen.projection,
                frozen_events=frozen.frozen_events,
                current_events=tuple(partial_events),
                expected_pre_head=frozen.expected_pre_head,
            )
            with (
                mock.patch.object(
                    recovery, "_frozen_live_prefix", return_value=partial
                ),
                self.assertRaisesRegex(
                    recovery.WorksetRecoveryIntegrityError,
                    "partial ledger slice",
                ),
            ):
                recovery._outcome_settlement_adoption_contract(  # noqa: SLF001
                    item, reservation
                )

    def test_anchor_request_fresh_cas_rebuilds_exact_event_without_network(
        self,
    ) -> None:
        ledger, reservation, item, load_frozen, seal = self._anchor_request_fixture()
        network = mock.Mock(side_effect=AssertionError("network must stay unreachable"))

        with (
            mock.patch.object(recovery, "_frozen_live_prefix", side_effect=load_frozen),
            mock.patch.object(recovery.live, "_default_anchor_client", network),
        ):
            contract = recovery._anchor_request_contract(  # noqa: SLF001
                item, reservation
            )
            output = recovery._anchor_request_action(  # noqa: SLF001
                item, reservation, contract
            )

        network.assert_not_called()
        events = ledger.read_events()
        self.assertEqual(len(events), 3)
        request = events[-1]
        self.assertEqual(request.sequence_id, seal.sequence_id + 1)
        self.assertEqual(request.previous_entry_sha256, seal.entry_sha256)
        self.assertEqual(
            request.event_key,
            ("synthetic-live-epoch-anchor-request:2031-02-04:anchor:1:requested"),
        )
        self.assertEqual(
            request.payload,
            {
                "live_epoch_id": "synthetic-live-epoch-anchor-request",
                "target_date": "2031-02-04",
                "sealed_sequence_id": seal.sequence_id,
                "sealed_entry_sha256": seal.entry_sha256,
                "attempt": 1,
            },
        )
        self.assertEqual(request.issue_id, seal.issue_id)
        self.assertEqual(request.input_manifest_sha256, seal.input_manifest_sha256)
        self.assertEqual(request.state_before_sha256, seal.state_after_sha256)
        self.assertEqual(request.state_after_sha256, seal.state_after_sha256)
        self.assertIsNone(output.reference)
        self.assertTrue(output.semantics["live_ledger_event_recorded"])
        self.assertFalse(output.semantics["network_action_performed"])
        self.assertNotIn("created", output.semantics)

    def test_anchor_request_commit_before_receipt_adopts_the_same_event(self) -> None:
        ledger, reservation, item, load_frozen, _ = self._anchor_request_fixture()

        with mock.patch.object(
            recovery, "_frozen_live_prefix", side_effect=load_frozen
        ):
            contract = recovery._anchor_request_contract(  # noqa: SLF001
                item, reservation
            )
            first = recovery._anchor_request_action(  # noqa: SLF001
                item, reservation, contract
            )
            stored = ledger.read_events()[-1]
            adopted = recovery._anchor_request_action(  # noqa: SLF001
                item, reservation, contract
            )

        self.assertEqual(adopted, first)
        self.assertEqual(len(ledger.read_events()), 3)
        self.assertEqual(ledger.read_events()[-1], stored)

    def test_anchor_request_stale_head_fails_without_appending(self) -> None:
        ledger, reservation, item, load_frozen, seal = self._anchor_request_fixture()
        with mock.patch.object(
            recovery, "_frozen_live_prefix", side_effect=load_frozen
        ):
            contract = recovery._anchor_request_contract(  # noqa: SLF001
                item, reservation
            )
            ledger.append_transaction(
                [
                    self._synthetic_live_spec(
                        "foreign-live-ledger-suffix",
                        event_type="integrity_blocked",
                        target=seal.target_date,
                        issue_id=seal.issue_id,
                        payload={"foreign": True},
                        input_manifest_sha256=seal.input_manifest_sha256,
                        state_sha256=seal.state_after_sha256,
                    )
                ]
            )
            before = ledger.read_events()
            with self.assertRaisesRegex(
                recovery.WorksetRecoveryIntegrityError,
                "Anchor request CAS failed",
            ):
                recovery._anchor_request_action(  # noqa: SLF001
                    item, reservation, contract
                )

        self.assertEqual(ledger.read_events(), before)
        self.assertNotIn(
            contract["event_spec"]["event_key"],
            [event.event_key for event in ledger.read_events()],
        )

    def test_anchor_request_attempt_comes_only_from_balanced_frozen_prefix(
        self,
    ) -> None:
        ledger, reservation, item, load_frozen, seal = self._anchor_request_fixture(
            completed_attempts=1
        )

        with mock.patch.object(
            recovery, "_frozen_live_prefix", side_effect=load_frozen
        ):
            contract = recovery._anchor_request_contract(  # noqa: SLF001
                item, reservation
            )
            output = recovery._anchor_request_action(  # noqa: SLF001
                item, reservation, contract
            )

        self.assertEqual(contract["attempt"], 2)
        self.assertEqual(
            contract["event_spec"]["event_key"],
            ("synthetic-live-epoch-anchor-request:2031-02-04:anchor:2:requested"),
        )
        request = ledger.read_events()[-1]
        self.assertEqual(request.payload["attempt"], 2)
        self.assertEqual(request.payload["sealed_entry_sha256"], seal.entry_sha256)
        self.assertEqual(output.semantics["attempt"], 2)

    def test_anchor_request_historical_receipt_survives_a_later_suffix(self) -> None:
        ledger, reservation, item, load_frozen, seal = self._anchor_request_fixture()

        with mock.patch.object(
            recovery, "_frozen_live_prefix", side_effect=load_frozen
        ):
            contract = recovery._anchor_request_contract(  # noqa: SLF001
                item, reservation
            )
            output = recovery._anchor_request_action(  # noqa: SLF001
                item, reservation, contract
            )
            ledger.append_transaction(
                [
                    self._synthetic_live_spec(
                        "later-reviewed-live-suffix",
                        event_type="anchor_failed",
                        target=seal.target_date,
                        issue_id=seal.issue_id,
                        payload={
                            **ledger.read_events()[-1].payload,
                            "reason_code": "request_or_receipt_validation_failed",
                            "error_type": "SyntheticFailure",
                            "retry_policy": "automatic_next_poll",
                        },
                        input_manifest_sha256=seal.input_manifest_sha256,
                        state_sha256=seal.state_after_sha256,
                    )
                ]
            )
            recorded = {
                "action": "anchor_request_recorded",
                "action_output_kind": output.kind,
                "action_output": output.reference,
                "action_semantics": dict(output.semantics),
            }
            recovery._verify_recorded_action_contract(  # noqa: SLF001
                item,
                recorded,
                self.paths,
                reservation=reservation,
                item_intent={"action_contract": contract},
            )
            tampered = {
                **recorded,
                "action_semantics": {**output.semantics, "attempt": 99},
            }
            with self.assertRaisesRegex(
                recovery.WorksetRecoveryIntegrityError, "immutable replay"
            ):
                recovery._verify_recorded_action_contract(  # noqa: SLF001
                    item,
                    tampered,
                    self.paths,
                    reservation=reservation,
                    item_intent={"action_contract": contract},
                )

        self.assertEqual(len(ledger.read_events()), 4)

    def test_anchor_result_request_contract_freezes_tip_endpoint_and_idempotency(
        self,
    ) -> None:
        ledger, reservation, item, load_frozen, _ = self._anchor_request_fixture()

        def load_external(reserved):  # type: ignore[no-untyped-def]
            frozen = load_frozen(reserved)
            return recovery.FrozenLivePrefix(
                profile={"external_anchor": _external_anchor_profile()},
                paths=frozen.paths,
                prerequisites=frozen.prerequisites,
                projection=frozen.projection,
                frozen_events=frozen.frozen_events,
                current_events=frozen.current_events,
                expected_pre_head=frozen.expected_pre_head,
            )

        network = mock.Mock(side_effect=AssertionError("network must remain unlocked"))
        with (
            mock.patch.object(
                recovery, "_frozen_live_prefix", side_effect=load_external
            ),
            mock.patch.object(recovery.live, "_default_anchor_client", network),
            mock.patch.dict(
                os.environ,
                {
                    "OOTANG_TIME_ANCHOR_URL": (
                        "https://Anchor.Invalid:443/v1/receipts"
                    ),
                    "OOTANG_TIME_ANCHOR_TOKEN": "synthetic-secret-token",
                },
                clear=False,
            ),
        ):
            request_contract = recovery._anchor_request_contract(  # noqa: SLF001
                item, reservation
            )
            recovery._anchor_request_action(  # noqa: SLF001
                item, reservation, request_contract
            )
            result_contract = recovery._anchor_result_request_contract(  # noqa: SLF001
                item, reservation
            )

        network.assert_not_called()
        request_event = ledger.read_events()[-1]
        self.assertEqual(
            result_contract["endpoint"], "https://anchor.invalid/v1/receipts"
        )
        self.assertEqual(result_contract["request_body"], request_event.payload)
        self.assertEqual(
            result_contract["expected_result_pre_head"]["entry_sha256"],
            request_event.entry_sha256,
        )
        self.assertEqual(len(result_contract["idempotency_key"]), 64)
        self.assertNotIn("synthetic-secret-token", json.dumps(result_contract))

    def test_anchor_result_request_contract_replays_after_suffix_and_rejects_drift(
        self,
    ) -> None:
        ledger, reservation, item, load_frozen, seal = self._anchor_request_fixture()

        def load_external(reserved):  # type: ignore[no-untyped-def]
            frozen = load_frozen(reserved)
            return recovery.FrozenLivePrefix(
                profile={"external_anchor": _external_anchor_profile()},
                paths=frozen.paths,
                prerequisites=frozen.prerequisites,
                projection=frozen.projection,
                frozen_events=frozen.frozen_events,
                current_events=frozen.current_events,
                expected_pre_head=frozen.expected_pre_head,
            )

        with (
            mock.patch.object(
                recovery, "_frozen_live_prefix", side_effect=load_external
            ),
            mock.patch.dict(
                os.environ,
                {"OOTANG_TIME_ANCHOR_URL": "https://anchor.invalid/v1/receipts"},
                clear=False,
            ),
        ):
            request_contract = recovery._anchor_request_contract(  # noqa: SLF001
                item, reservation
            )
            recovery._anchor_request_action(  # noqa: SLF001
                item, reservation, request_contract
            )
            contract = recovery._anchor_result_request_contract(  # noqa: SLF001
                item, reservation
            )
            request = ledger.read_events()[-1]
            ledger.append_transaction(
                [
                    self._synthetic_live_spec(
                        request.event_key.removesuffix(":requested") + ":failed",
                        event_type="anchor_failed",
                        target=seal.target_date,
                        issue_id=seal.issue_id,
                        payload={
                            **request.payload,
                            "reason_code": "request_or_receipt_validation_failed",
                            "error_type": "SyntheticFailure",
                            "retry_policy": "automatic_next_poll",
                        },
                        input_manifest_sha256=seal.input_manifest_sha256,
                        state_sha256=seal.state_after_sha256,
                    )
                ]
            )
            recovery._verify_anchor_result_request_contract(  # noqa: SLF001
                item, reservation, contract
            )
            with self.assertRaisesRegex(
                recovery.WorksetRecoveryIntegrityError,
                "no longer at the live-ledger head",
            ):
                recovery._verify_pending_anchor_result_head(  # noqa: SLF001
                    item, reservation, contract
                )
            changed = {**contract, "idempotency_key": "f" * 64}
            with self.assertRaisesRegex(
                recovery.WorksetRecoveryIntegrityError,
                "contract changed",
            ):
                recovery._verify_anchor_result_request_contract(  # noqa: SLF001
                    item, reservation, changed
                )

    def test_anchor_result_endpoint_wait_does_not_freeze_an_intent(self) -> None:
        reservation = self._reservation(
            [("anchor-result", "anchor_result_recorded", [])]
        )
        with mock.patch.object(
            recovery,
            "_action_contract",
            side_effect=recovery.WorksetRecoveryExternalWait(
                "time-anchor endpoint is missing"
            ),
        ):
            result = self._run(reservation)

        self.assertEqual(result.status, "waiting_for_external_anchor_endpoint")
        self.assertFalse(self.paths.item_intents.exists())
        self.assertFalse(self.paths.receipts.exists())
        self.assertFalse(self.paths.events.exists())

    def test_anchor_result_pending_intent_fences_without_network_or_receipt(
        self,
    ) -> None:
        reservation = self._reservation(
            [("anchor-result", "anchor_result_recorded", [])]
        )
        contract = {"schema_version": "synthetic-anchor-result-request"}
        network = mock.Mock(side_effect=AssertionError("network must stay unreachable"))
        with (
            mock.patch.object(recovery, "_action_contract", return_value=contract),
            mock.patch.object(
                recovery, "_verify_item_intent_action_contract", return_value=None
            ),
            mock.patch.object(
                recovery, "_verify_pending_anchor_result_head", return_value=None
            ),
            mock.patch.object(recovery.live, "_default_anchor_client", network),
        ):
            first = self._run(reservation)
            first_status = json.loads(self.paths.status.read_bytes())
            second = self._run(reservation)
            second_status = json.loads(self.paths.status.read_bytes())

        self.assertIsInstance(first, recovery.AnchorResultDispatchPlan)
        self.assertIsInstance(second, recovery.AnchorResultDispatchPlan)
        self.assertEqual(first_status["status"], "external_anchor_request_prepared")
        self.assertEqual(
            second_status["status"], "waiting_for_external_anchor_dispatch"
        )
        self.assertEqual(first.item_intent, second.item_intent)
        network.assert_not_called()
        released = [
            recovery.drain._acquire_lock(path, label=label)  # noqa: SLF001
            for label, path in zip(
                recovery.LOCK_ORDER,
                (
                    self.paths.manager_lock,
                    self.paths.cycle_lock,
                    self.paths.replay_lock,
                    self.paths.shadow_lock,
                ),
                strict=True,
            )
        ]
        recovery.drain._release_locks(released)  # noqa: SLF001
        self.assertEqual(len(list(self.paths.item_intents.glob("*.json"))), 1)
        self.assertFalse(self.paths.receipts.exists())
        self.assertFalse(self.paths.events.exists())

    def test_anchor_result_dispatch_observes_response_without_receipt_or_event(
        self,
    ) -> None:
        plan = self._anchor_result_dispatch_plan()
        token = "synthetic-secret-anchor-token"

        def transport(contract, received_token):  # type: ignore[no-untyped-def]
            self.assertEqual(contract, plan.action_contract)
            self.assertEqual(received_token, token)
            return self._anchor_result_success_response(plan)

        with mock.patch.dict(
            os.environ, {"OOTANG_TEST_ANCHOR_TOKEN": token}, clear=False
        ):
            result = recovery._dispatch_and_capture_anchor_result(  # noqa: SLF001
                plan, transport=transport, clock=lambda: NOW
            )

        self.assertEqual(result.status, "external_anchor_response_observed")
        self.assertTrue(result.network_action_performed)
        self.assertIsNotNone(result.external_anchor_response_observation_path)
        self.assertFalse(self.paths.receipts.exists())
        self.assertFalse(self.paths.events.exists())
        persisted = b"".join(
            path.read_bytes() for path in self.paths.root.rglob("*") if path.is_file()
        )
        self.assertNotIn(token.encode(), persisted)

    def test_anchor_result_existing_link_is_zero_network(self) -> None:
        plan = self._anchor_result_dispatch_plan()
        with mock.patch.dict(
            os.environ, {"OOTANG_TEST_ANCHOR_TOKEN": "first-token"}, clear=False
        ):
            first = recovery._dispatch_and_capture_anchor_result(  # noqa: SLF001
                plan,
                transport=lambda *_args: self._anchor_result_success_response(plan),
                clock=lambda: NOW,
            )
        network = mock.Mock(side_effect=AssertionError("linked response must replay"))

        second = recovery._dispatch_and_capture_anchor_result(  # noqa: SLF001
            plan, transport=network, clock=lambda: NOW
        )

        self.assertEqual(first.status, "external_anchor_response_observed")
        self.assertEqual(second.status, "waiting_for_locked_anchor_result_consumption")
        self.assertFalse(second.network_action_performed)
        self.assertEqual(
            second.external_anchor_response_observation_path,
            first.external_anchor_response_observation_path,
        )
        network.assert_not_called()

    def test_anchor_result_deterministic_contract_failure_is_observed(self) -> None:
        plan = self._anchor_result_dispatch_plan()
        response = self._anchor_result_success_response(plan)
        invalid = recovery.AnchorResultTransportResponse(
            body=response.body.replace(
                plan.action_contract["request_body"]["sealed_entry_sha256"].encode(),
                b"f" * 64,
            ),
            status_code=response.status_code,
            media_type=response.media_type,
            charset=response.charset,
            content_encoding=response.content_encoding,
            final_url=response.final_url,
        )
        with mock.patch.dict(
            os.environ, {"OOTANG_TEST_ANCHOR_TOKEN": "failure-token"}, clear=False
        ):
            result = recovery._dispatch_and_capture_anchor_result(  # noqa: SLF001
                plan, transport=lambda *_args: invalid, clock=lambda: NOW
            )

        link = json.loads(
            result.external_anchor_response_observation_path.read_bytes()  # type: ignore[union-attr]
        )
        object_path = self.paths.root / link["response_observation"]["path"]
        observation = json.loads(object_path.read_bytes())
        self.assertEqual(result.status, "external_anchor_response_observed")
        self.assertEqual(observation["outcome"], "deterministic_failure")
        self.assertEqual(observation["failure"]["code"], "invalid_anchor_response")
        self.assertIsNone(observation["validated_response"])
        self.assertFalse(self.paths.receipts.exists())
        self.assertFalse(self.paths.events.exists())

    def test_anchor_result_object_before_link_is_forward_adopted_without_network(
        self,
    ) -> None:
        plan = self._anchor_result_dispatch_plan()
        with mock.patch.dict(
            os.environ, {"OOTANG_TEST_ANCHOR_TOKEN": "first-token"}, clear=False
        ):
            recovery._dispatch_and_capture_anchor_result(  # noqa: SLF001
                plan,
                transport=lambda *_args: self._anchor_result_success_response(plan),
                clock=lambda: NOW,
            )
        link_path = self.paths.anchor_result_response_links / f"{plan.step_id}.json"
        object_path = next(
            (self.paths.anchor_result_response_objects / plan.step_id).glob("*.json")
        )
        link_path.unlink()
        network = mock.Mock(side_effect=AssertionError("orphan object must be adopted"))

        with mock.patch.object(
            recovery, "_publish", wraps=recovery._publish
        ) as durable_publish:
            adopted = recovery._dispatch_and_capture_anchor_result(  # noqa: SLF001
                plan, transport=network, clock=lambda: NOW
            )

        self.assertEqual(adopted.status, "external_anchor_response_forward_adopted")
        self.assertFalse(adopted.network_action_performed)
        self.assertEqual(adopted.external_anchor_response_observation_path, link_path)
        self.assertTrue(link_path.is_file())
        adopted_paths = [
            call.args[0]
            for call in durable_publish.call_args_list
            if call.args[0] in {object_path, link_path}
        ]
        self.assertEqual(adopted_paths, [object_path, link_path])
        network.assert_not_called()

    def test_anchor_result_retryable_transport_persists_no_observation(self) -> None:
        plan = self._anchor_result_dispatch_plan()
        network = mock.Mock(
            side_effect=recovery.WorksetRecoveryNetworkWait("synthetic timeout")
        )
        with mock.patch.dict(
            os.environ, {"OOTANG_TEST_ANCHOR_TOKEN": "retry-token"}, clear=False
        ):
            result = recovery._dispatch_and_capture_anchor_result(  # noqa: SLF001
                plan, transport=network, clock=lambda: NOW
            )

        self.assertEqual(result.status, "waiting_for_external_anchor_retry")
        self.assertTrue(result.network_action_performed)
        network.assert_called_once_with(plan.action_contract, "retry-token")
        self.assertFalse(self.paths.anchor_result_response_objects.exists())
        self.assertFalse(self.paths.anchor_result_response_links.exists())
        self.assertFalse(self.paths.receipts.exists())
        self.assertFalse(self.paths.events.exists())

    def test_anchor_result_http_error_body_timeout_remains_retryable(self) -> None:
        plan = self._anchor_result_dispatch_plan()

        class TimeoutBody(io.BytesIO):
            def read(self, _size: int = -1) -> bytes:
                raise TimeoutError("synthetic slow error body")

        headers = Message()
        headers["Content-Type"] = "application/json"
        error = recovery.urllib.error.HTTPError(
            str(plan.action_contract["endpoint"]),
            400,
            "synthetic rejection",
            headers,
            TimeoutBody(),
        )
        opener = mock.Mock()
        opener.open.side_effect = error

        with (
            mock.patch.object(
                recovery.urllib.request, "build_opener", return_value=opener
            ),
            self.assertRaises(recovery.WorksetRecoveryNetworkWait),
        ):
            recovery._default_anchor_result_transport(  # noqa: SLF001
                plan.action_contract, "synthetic-token"
            )

    def test_anchor_result_missing_token_is_zero_network_and_persists_nothing(
        self,
    ) -> None:
        plan = self._anchor_result_dispatch_plan()
        network = mock.Mock(side_effect=AssertionError("missing token must wait"))
        with mock.patch.dict(os.environ, {}, clear=True):
            result = recovery._dispatch_and_capture_anchor_result(  # noqa: SLF001
                plan, transport=network, clock=lambda: NOW
            )

        self.assertEqual(result.status, "waiting_for_external_anchor_token")
        self.assertFalse(result.network_action_performed)
        network.assert_not_called()
        self.assertFalse(self.paths.anchor_result_response_objects.exists())
        self.assertFalse(self.paths.anchor_result_response_links.exists())
        self.assertFalse(self.paths.receipts.exists())
        self.assertFalse(self.paths.events.exists())

    def test_linked_anchor_result_consumes_exact_branch_without_network(self) -> None:
        for deterministic_failure in (False, True):
            with self.subTest(deterministic_failure=deterministic_failure):
                self._use_fresh_namespace()
                ledger, reservation, load_external, _ = (
                    self._anchor_result_coordinator_fixture()
                )
                network = mock.Mock(
                    side_effect=AssertionError(
                        "locked consumption must be zero-network"
                    )
                )
                with (
                    mock.patch.object(
                        recovery, "_frozen_live_prefix", side_effect=load_external
                    ),
                    mock.patch.object(
                        recovery, "_default_anchor_result_transport", network
                    ),
                    mock.patch.dict(
                        os.environ,
                        {
                            "OOTANG_TIME_ANCHOR_URL": (
                                "https://anchor.invalid/v1/receipts"
                            )
                        },
                        clear=False,
                    ),
                ):
                    plan = self._prepare_linked_anchor_result(
                        reservation,
                        deterministic_failure=deterministic_failure,
                    )
                    result = self._run(reservation)
                    follow_up = self._run(reservation)
                    retry_plan = (
                        self._run(reservation) if deterministic_failure else None
                    )

                network.assert_not_called()
                self.assertEqual(result.status, "recovery_step_completed")
                events = ledger.read_events()
                request, recorded = (
                    events[-3:-1] if deterministic_failure else events[-2:]
                )
                self.assertEqual(recorded.sequence_id, request.sequence_id + 1)
                self.assertEqual(recorded.previous_entry_sha256, request.entry_sha256)
                receipt = json.loads(result.receipt_path.read_bytes())  # type: ignore[union-attr]
                semantics = receipt["action_semantics"]
                self.assertIsNone(receipt["action_output"])
                self.assertFalse(semantics["network_action_performed"])
                self.assertTrue(semantics["live_ledger_event_recorded"])
                self.assertNotIn("created", semantics)
                self.assertEqual(
                    semantics["response_observation_link"]["path"],
                    plan.paths.anchor_result_response_links.joinpath(
                        f"{plan.step_id}.json"
                    )
                    .relative_to(plan.paths.root)
                    .as_posix(),
                )
                if deterministic_failure:
                    self.assertEqual(len(events), 5)
                    self.assertEqual(follow_up.status, "recovery_step_completed")
                    self.assertIsInstance(retry_plan, recovery.AnchorResultDispatchPlan)
                    self.assertEqual(recorded.event_type, "anchor_failed")
                    self.assertEqual(
                        recorded.event_key,
                        request.event_key.removesuffix(":requested") + ":failed",
                    )
                    self.assertEqual(
                        recorded.payload,
                        {
                            **request.payload,
                            "reason_code": ("request_or_receipt_validation_failed"),
                            "error_type": "AnchorResultProtocolFailure",
                            "retry_policy": "automatic_next_poll",
                        },
                    )
                    self.assertEqual(
                        receipt["next_actions"], ["anchor_request_recorded"]
                    )
                    retry_request = events[-1]
                    self.assertEqual(retry_request.event_type, "anchor_requested")
                    self.assertEqual(retry_request.payload["attempt"], 2)
                    self.assertEqual(
                        retry_request.previous_entry_sha256, recorded.entry_sha256
                    )
                    retry_receipt = json.loads(  # type: ignore[union-attr]
                        follow_up.receipt_path.read_bytes()
                    )
                    assert result.receipt_path is not None
                    self.assertEqual(
                        retry_receipt["previous_step_receipt"],
                        recovery._reference(  # noqa: SLF001
                            self._snapshot(result.receipt_path),
                            self.paths.root,
                        ),
                    )
                    self.assertEqual(
                        retry_receipt["next_actions"], ["anchor_result_recorded"]
                    )
                    assert isinstance(retry_plan, recovery.AnchorResultDispatchPlan)
                    self.assertEqual(
                        retry_plan.action_contract["request_body"]["attempt"], 2
                    )
                    self.assertEqual(
                        retry_plan.action_contract["expected_result_pre_head"][
                            "entry_sha256"
                        ],
                        retry_request.entry_sha256,
                    )
                else:
                    self.assertEqual(len(events), 4)
                    self.assertEqual(
                        follow_up.status, "waiting_for_supported_ready_key"
                    )
                    self.assertEqual(recorded.event_type, "anchor_confirmed")
                    self.assertEqual(
                        recorded.event_key,
                        request.event_key.removesuffix(":requested") + ":confirmed",
                    )
                    self.assertEqual(recorded.payload["provider"], "synthetic-provider")
                    self.assertEqual(receipt["next_actions"], ["outcome_batch_settled"])

    def test_anchor_result_commit_before_receipt_is_exactly_adopted(self) -> None:
        self._use_fresh_namespace()
        ledger, reservation, load_external, _ = (
            self._anchor_result_coordinator_fixture()
        )
        with (
            mock.patch.object(
                recovery, "_frozen_live_prefix", side_effect=load_external
            ),
            mock.patch.dict(
                os.environ,
                {"OOTANG_TIME_ANCHOR_URL": "https://anchor.invalid/v1/receipts"},
                clear=False,
            ),
        ):
            self._prepare_linked_anchor_result(reservation, deterministic_failure=False)
            with (
                mock.patch.object(
                    recovery,
                    "_ensure_receipt",
                    side_effect=RuntimeError("synthetic post-CAS crash"),
                ),
                self.assertRaisesRegex(RuntimeError, "post-CAS crash"),
            ):
                self._run(reservation)
            committed = ledger.read_events()[-1]
            self.assertEqual(committed.event_type, "anchor_confirmed")
            self.assertEqual(len(list(self.paths.receipts.glob("*.json"))), 1)

            result = self._run(reservation)

        self.assertEqual(result.status, "recovery_step_completed")
        self.assertEqual(len(ledger.read_events()), 4)
        self.assertEqual(ledger.read_events()[-1], committed)
        self.assertEqual(len(list(self.paths.receipts.glob("*.json"))), 2)
        self.assertEqual(len(list(self.paths.events.glob("*.json"))), 2)

    def test_linked_anchor_result_waits_for_dispatch_link_durability(self) -> None:
        self._use_fresh_namespace()
        ledger, reservation, load_external, _ = (
            self._anchor_result_coordinator_fixture()
        )
        network = mock.Mock(
            side_effect=AssertionError("durability fencing must be zero-network")
        )
        with (
            mock.patch.object(
                recovery, "_frozen_live_prefix", side_effect=load_external
            ),
            mock.patch.object(recovery, "_default_anchor_result_transport", network),
            mock.patch.dict(
                os.environ,
                {"OOTANG_TIME_ANCHOR_URL": "https://anchor.invalid/v1/receipts"},
                clear=False,
            ),
        ):
            plan = self._prepare_linked_anchor_result(
                reservation, deterministic_failure=False
            )
            object_path = next(
                (self.paths.anchor_result_response_objects / plan.step_id).glob(
                    "*.json"
                )
            )
            link_path = self.paths.anchor_result_response_links / f"{plan.step_id}.json"
            before = ledger.read_events()
            handle = recovery.drain._acquire_lock(  # noqa: SLF001
                self.paths.anchor_result_dispatch_lock,
                label="synthetic anchor response publisher",
            )
            try:
                waiting = self._run(reservation)
            finally:
                recovery.drain._release_locks([handle])  # noqa: SLF001

            self.assertEqual(ledger.read_events(), before)
            self.assertEqual(len(list(self.paths.receipts.glob("*.json"))), 1)
            self.assertEqual(len(list(self.paths.events.glob("*.json"))), 1)
            with mock.patch.object(
                recovery, "_publish", wraps=recovery._publish
            ) as durable_publish:
                consumed = self._run(reservation)

        network.assert_not_called()
        self.assertEqual(waiting.status, "waiting_for_anchor_result_link_durability")
        self.assertEqual(before, ledger.read_events()[:-1])
        self.assertEqual(consumed.status, "recovery_step_completed")
        fenced_paths = [
            call.args[0]
            for call in durable_publish.call_args_list
            if call.args[0] in {object_path, link_path}
        ]
        self.assertEqual(fenced_paths, [object_path, link_path])
        self.assertEqual(ledger.read_events()[-1].event_type, "anchor_confirmed")
        self.assertEqual(len(list(self.paths.receipts.glob("*.json"))), 2)
        self.assertEqual(len(list(self.paths.events.glob("*.json"))), 2)

    def test_anchor_result_receipt_before_event_is_forward_adopted(self) -> None:
        self._use_fresh_namespace()
        ledger, reservation, load_external, _ = (
            self._anchor_result_coordinator_fixture()
        )
        with (
            mock.patch.object(
                recovery, "_frozen_live_prefix", side_effect=load_external
            ),
            mock.patch.dict(
                os.environ,
                {"OOTANG_TIME_ANCHOR_URL": "https://anchor.invalid/v1/receipts"},
                clear=False,
            ),
        ):
            self._prepare_linked_anchor_result(reservation, deterministic_failure=False)
            with (
                mock.patch.object(
                    recovery,
                    "_append_event",
                    side_effect=RuntimeError("synthetic post-receipt crash"),
                ),
                self.assertRaisesRegex(RuntimeError, "post-receipt crash"),
            ):
                self._run(reservation)
            committed = ledger.read_events()[-1]
            action = mock.Mock(
                side_effect=AssertionError(
                    "result action must not replay after receipt"
                )
            )
            with mock.patch.object(recovery, "_anchor_result_action", action):
                result = self._run(reservation)

        self.assertEqual(result.status, "recovery_event_forward_adopted")
        self.assertEqual(ledger.read_events()[-1], committed)
        self.assertEqual(len(ledger.read_events()), 4)
        self.assertEqual(len(list(self.paths.events.glob("*.json"))), 2)
        action.assert_not_called()

    def test_anchor_result_pre_head_drift_fails_without_mutation(self) -> None:
        self._use_fresh_namespace()
        ledger, reservation, load_external, seal = (
            self._anchor_result_coordinator_fixture()
        )
        with (
            mock.patch.object(
                recovery, "_frozen_live_prefix", side_effect=load_external
            ),
            mock.patch.dict(
                os.environ,
                {"OOTANG_TIME_ANCHOR_URL": "https://anchor.invalid/v1/receipts"},
                clear=False,
            ),
        ):
            self._prepare_linked_anchor_result(reservation, deterministic_failure=False)
            ledger.append_transaction(
                [
                    self._synthetic_live_spec(
                        "foreign-result-position",
                        event_type="integrity_blocked",
                        target=seal.target_date,
                        issue_id=seal.issue_id,
                        payload={"foreign": True},
                        input_manifest_sha256=seal.input_manifest_sha256,
                        state_sha256=seal.state_after_sha256,
                    )
                ]
            )
            before = ledger.read_events()
            with self.assertRaises(recovery.WorksetRecoveryIntegrityError):
                self._run(reservation)

        self.assertEqual(ledger.read_events(), before)
        self.assertEqual(len(list(self.paths.receipts.glob("*.json"))), 1)

    def test_frozen_live_prefix_accepts_suffix_but_rejects_tip_drift(self) -> None:
        ledger, reservation, _, _, seal = self._anchor_request_fixture()
        projection = SimpleNamespace(epoch_id="synthetic-live-epoch-anchor-request")
        prerequisites = SimpleNamespace()
        live_paths = SimpleNamespace(ledger=ledger.path)
        ledger.append_transaction(
            [
                self._synthetic_live_spec(
                    "post-freeze-live-suffix",
                    event_type="integrity_blocked",
                    target=seal.target_date,
                    issue_id=seal.issue_id,
                    payload={"post_freeze": True},
                    input_manifest_sha256=seal.input_manifest_sha256,
                    state_sha256=seal.state_after_sha256,
                )
            ]
        )

        with (
            mock.patch.object(recovery.live, "load_config", return_value={}),
            mock.patch.object(recovery.live, "runtime_paths", return_value=live_paths),
            mock.patch.object(
                recovery.live, "load_prerequisites", return_value=prerequisites
            ),
            mock.patch.object(
                recovery.live, "_reconstruct_projection", return_value=projection
            ),
        ):
            frozen = recovery._frozen_live_prefix(reservation)  # noqa: SLF001
            self.assertEqual(len(frozen.frozen_events), 2)
            self.assertEqual(len(frozen.current_events), 3)
            reservation.manifest["frozen_live_upper_tip"]["live_terminal_sha256"] = (
                "f" * 64
            )
            with self.assertRaisesRegex(
                recovery.WorksetRecoveryIntegrityError,
                "prefix tip changed",
            ):
                recovery._frozen_live_prefix(reservation)  # noqa: SLF001

    def test_frozen_live_prefix_lock_is_reported_as_busy(self) -> None:
        _, reservation, _, _, _ = self._anchor_request_fixture()
        locked = sqlite3.OperationalError("database is locked")
        failure = live_ledger.LedgerSchemaError("cannot open ledger database safely")
        failure.__cause__ = locked
        reader = mock.Mock()
        reader.read_events.side_effect = failure

        with (
            mock.patch.object(recovery.live, "load_config", return_value={}),
            mock.patch.object(
                recovery.live,
                "runtime_paths",
                return_value=SimpleNamespace(ledger=self.active_root / "live.sqlite3"),
            ),
            mock.patch.object(
                recovery.live, "load_prerequisites", return_value=SimpleNamespace()
            ),
            mock.patch.object(
                recovery.live, "_ReadOnlyAppendOnlyLedger", return_value=reader
            ),
            self.assertRaises(recovery.WorksetRecoveryBusyError),
        ):
            recovery._frozen_live_prefix(reservation)  # noqa: SLF001

    def test_unsupported_item_waits_and_never_claims_bounded_recovery(self) -> None:
        reservation = self._reservation(
            [("network-key", "trusted_time_response_link_recorded", [])]
        )
        hook = mock.Mock(side_effect=AssertionError("unsupported item dispatched"))
        result = self._run(reservation, hook)

        self.assertEqual(result.status, "waiting_for_supported_ready_key")
        hook.assert_not_called()
        status = json.loads(result.status_path.read_bytes())
        self.assertFalse(status["bounded_workset_recovery_implemented"])
        self.assertFalse(status["network_action_performed"])

        self._use_fresh_namespace()
        reservation = self._reservation(
            [("clock-only-guard", "superseded_by_backfill", [])]
        )
        hook = mock.Mock(side_effect=AssertionError("clock-only guard dispatched"))
        result = self._run(reservation, hook)
        self.assertEqual(result.status, "waiting_for_supported_ready_key")
        hook.assert_not_called()

    def test_cyclic_dependency_is_rejected(self) -> None:
        reservation = self._reservation(
            [
                ("natural-a", "anchor_receipt_repaired", ["natural-b"]),
                ("natural-b", "anchor_receipt_repaired", ["natural-a"]),
            ]
        )
        with self.assertRaisesRegex(recovery.WorksetRecoveryIntegrityError, "cyclic"):
            recovery._dag(reservation)  # noqa: SLF001

    def test_machine_only_cli_boundary_and_der_builder_are_deterministic(self) -> None:
        claims = recovery._claims()  # noqa: SLF001
        self.assertTrue(claims["machine_only"])
        self.assertTrue(claims["step_receipt_chain_implemented"])
        self.assertTrue(claims["terminal_receipt_dependency_gate_implemented"])
        self.assertTrue(claims["live_ledger_expected_pre_head_cas_implemented"])
        self.assertTrue(claims["live_anchor_result_request_intent_implemented"])
        self.assertTrue(claims["live_anchor_result_adapter_implemented"])
        self.assertFalse(claims["bounded_workset_recovery_implemented"])
        self.assertFalse(claims["terminal_transition_closure_implemented"])
        result = recovery.RecoveryResult("waiting", "no authority", Path("status"))
        output = io.StringIO()
        with (
            mock.patch.object(
                recovery, "coordinate_epoch_workset_recovery", return_value=result
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(recovery.main([]), 0)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["bounded_workset_recovery_implemented"])
        self.assertFalse(payload["lifecycle_authority"])
        self.assertFalse(payload["transition_authority"])

        message = b"frozen-envelope"
        nonce = (1 << 255) + 17
        first = trusted.build_timestamp_request(message, nonce, "1.2.3.4")
        self.assertEqual(
            first, trusted.build_timestamp_request(message, nonce, "1.2.3.4")
        )

    def test_production_local_adapters_rebuild_exact_bytes_and_require_evidence(
        self,
    ) -> None:
        reservation = self._reservation()

        target = date(2031, 2, 4)
        trusted_profile = trusted.load_trusted_time_profile()
        trusted_paths = trusted.trusted_time_paths(
            trusted_profile, runtime_root=self.active_root
        )
        envelope = {"synthetic_envelope": True}
        message = trusted._canonical_bytes(envelope)  # noqa: SLF001
        nonce = (1 << 255) + 19
        der = trusted.build_timestamp_request(
            message, nonce, trusted_profile["rfc3161"]["policy_oid"]
        )
        der_path = trusted_paths.request_der / f"{target}.tsq"
        record_path = trusted_paths.requests / f"{target}.json"
        record = {
            "evidence_envelope": envelope,
            "nonce_hex": f"{nonce:064x}",
            "request_der": {
                "path": der_path.relative_to(self.active_root).as_posix(),
                "sha256": hashlib.sha256(der).hexdigest(),
                "size_bytes": len(der),
            },
        }
        record_path.parent.mkdir(parents=True)
        record_path.write_bytes(trusted._canonical_bytes(record))  # noqa: SLF001
        trusted_item = {
            "authority": {
                "target_date": target.isoformat(),
                "request_sha256": hashlib.sha256(record_path.read_bytes()).hexdigest(),
                "tsa_nonce_hex": f"{nonce:064x}",
                "message_imprint_sha256": hashlib.sha256(message).hexdigest(),
                "request_der_sha256": hashlib.sha256(der).hexdigest(),
            }
        }
        first_der = recovery._trusted_der_action(  # noqa: SLF001
            trusted_item, reservation, self.paths, {record_path}
        )
        second_der = recovery._trusted_der_action(  # noqa: SLF001
            trusted_item, reservation, self.paths, {record_path}
        )
        self.assertEqual(der_path.read_bytes(), der)
        self.assertEqual(first_der, second_der)

        trusted_paths.response_links.mkdir(parents=True, exist_ok=True)
        trusted_paths.response_links.joinpath(f"{target}.json").write_bytes(
            b'{"later_step":true}\n'
        )
        recovery._verify_recorded_action_contract(  # noqa: SLF001
            {
                "authority": trusted_item["authority"],
            },
            {
                "action": "trusted_time_request_der_repaired",
                "action_output_kind": first_der.kind,
                "action_output": first_der.reference,
                "action_semantics": dict(first_der.semantics),
            },
            self.paths,
        )
        wrong_output = dict(first_der.reference)  # type: ignore[arg-type]
        wrong_output["path"] = "unrelated/output.tsq"
        with self.assertRaisesRegex(
            recovery.WorksetRecoveryIntegrityError,
            "DER evidence changed",
        ):
            recovery._verify_recorded_action_contract(  # noqa: SLF001
                {"authority": trusted_item["authority"]},
                {
                    "action": "trusted_time_request_der_repaired",
                    "action_output_kind": first_der.kind,
                    "action_output": wrong_output,
                    "action_semantics": dict(first_der.semantics),
                },
                self.paths,
            )

        seal_sha = "d" * 64
        confirmed_sha = "e" * 64
        anchor_target = "2031-02-05"
        anchor_payload = {
            "provider": "synthetic-provider",
            "receipt_id": "synthetic-receipt",
            "anchored_at_utc": "2031-02-05T01:00:00Z",
            "sealed_entry_sha256": seal_sha,
            "receipt": {"proof": "synthetic"},
        }
        seal = SimpleNamespace(
            event_type="issue_batch_sealed",
            entry_sha256=seal_sha,
            target_date=anchor_target,
            payload={},
        )
        confirmed = SimpleNamespace(
            event_type="anchor_confirmed",
            entry_sha256=confirmed_sha,
            target_date=anchor_target,
            payload=anchor_payload,
        )
        live_paths = SimpleNamespace(anchors=self.active_root / "anchors")
        anchor_item = {
            "authority": {
                "target_date": anchor_target,
                "seal_entry_sha256": seal_sha,
                "confirmed_event": {"entry_sha256": confirmed_sha},
            }
        }
        with mock.patch.object(
            recovery,
            "_live_projection",
            return_value=({}, live_paths, (seal, confirmed)),
        ):
            first_anchor = recovery._anchor_action(  # noqa: SLF001
                anchor_item, reservation, self.paths, set()
            )
            second_anchor = recovery._anchor_action(  # noqa: SLF001
                anchor_item, reservation, self.paths, set()
            )
        self.assertEqual(first_anchor, second_anchor)
        stored_anchor = live_paths.anchors / f"{anchor_target}_{seal_sha}.json"
        self.assertEqual(json.loads(stored_anchor.read_bytes()), anchor_payload)
        anchor_receipt = {
            "action": "anchor_receipt_repaired",
            "action_output_kind": first_anchor.kind,
            "action_output": first_anchor.reference,
            "action_semantics": dict(first_anchor.semantics),
        }
        recovery._verify_recorded_action_contract(  # noqa: SLF001
            anchor_item, anchor_receipt, self.paths
        )
        wrong_anchor_receipt = dict(anchor_receipt)
        wrong_anchor_receipt["action_output"] = {
            **first_anchor.reference,  # type: ignore[misc]
            "path": "unrelated/anchor.json",
        }
        with self.assertRaisesRegex(
            recovery.WorksetRecoveryIntegrityError,
            "anchor repair output path changed",
        ):
            recovery._verify_recorded_action_contract(  # noqa: SLF001
                anchor_item, wrong_anchor_receipt, self.paths
            )

        guard_item = {
            "authority": {
                "target_date": "2031-02-06",
                "superseding_live_event": None,
            }
        }
        with (
            mock.patch.object(
                recovery, "_live_projection", return_value=({}, live_paths, ())
            ),
            self.assertRaisesRegex(
                recovery.WorksetRecoveryIntegrityError,
                "unique durable backfill evidence",
            ),
        ):
            recovery._guard_action(  # noqa: SLF001
                guard_item, reservation, self.paths, set()
            )


if __name__ == "__main__":
    unittest.main()
