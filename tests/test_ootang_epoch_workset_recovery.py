"""Fast coordinator contracts for manifest-keyed epoch recovery."""

from __future__ import annotations

from contextlib import redirect_stdout
from datetime import date, datetime, timezone
import hashlib
import io
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
from monitoring import ootang_epoch_workset_manifest as manifest  # noqa: E402
from monitoring import ootang_epoch_workset_recovery as recovery  # noqa: E402
from monitoring import ootang_trusted_time_shadow_core as trusted  # noqa: E402


NOW = datetime(2031, 2, 3, 4, 5, tzinfo=timezone.utc)


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
            self.registry_root,
            self.registry_root / "workset_recovery_v1",
            self.registry_root / "workset_recovery_v1/intent.json",
            self.registry_root / "workset_recovery_v1/item_intents",
            self.registry_root / "workset_recovery_v1/receipts",
            self.registry_root / "workset_recovery_v1/events",
            self.registry_root / "workset_recovery_v1/status.json",
            self.registry_root / "manager.lock",
            self.active_root,
            self.shadow_root,
            self.active_root / "cycle.lock",
            self.active_root / "replay.lock",
            self.shadow_root / "runner.lock",
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
                "epoch_id": "old-epoch",
                "event_count": 1,
                "terminal_entry_sha256": "c" * 64,
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
    ) -> recovery.RecoveryResult:
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
            self.registry_root,
            self.registry_root / "recovery",
            self.registry_root / "recovery/intent.json",
            self.registry_root / "recovery/item_intents",
            self.registry_root / "recovery/receipts",
            self.registry_root / "recovery/events",
            self.registry_root / "recovery/status.json",
            self.registry_root / "manager.lock",
            self.active_root,
            self.shadow_root,
            self.active_root / "cycle.lock",
            self.active_root / "replay.lock",
            self.shadow_root / "runner.lock",
        )

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
