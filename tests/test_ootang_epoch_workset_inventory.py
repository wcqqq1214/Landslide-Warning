"""Sub-second orchestration contracts for the read-only workset inventory."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_epoch_drain_v2 as drain_v2  # noqa: E402
from monitoring import ootang_epoch_workset_inventory as inventory  # noqa: E402


FIXED_NOW = datetime(2030, 1, 2, 12, 0, tzinfo=timezone.utc)


class WorksetInventoryOrchestrationTests(unittest.TestCase):
    @staticmethod
    def _context() -> drain_v2.WorksetContext:
        return drain_v2.WorksetContext(
            registry_event_sequence_id=7,
            registry_event_entry_sha256="1" * 64,
            preparation_event_sequence_id=3,
            preparation_event_entry_sha256="2" * 64,
            candidate_id="candidate-a",
            slot_id="slot-a",
            old_live_epoch_id="old-epoch-a",
            live_event_count=11,
            live_terminal_sha256="3" * 64,
        )

    @staticmethod
    def _seam(
        name: str,
        family: str,
        called: list[str],
    ):
        def inspect(_state, builders, items) -> None:
            called.append(name)
            authority = {"seam": name, "terminal": False}
            builders[family].records.append(authority)
            artifact = inventory.ArtifactRef(
                role=f"{name}_fixture",
                root="active",
                path=f"fixtures/{name}.json",
                sha256=(str(len(called)) * 64)[:64],
                size_bytes=1,
            )
            builders[family].artifacts.append(artifact)
            items.append(
                inventory._item(  # noqa: SLF001
                    family,
                    f"{family}:{name}",
                    inventory.ALLOWED_SUCCESSORS[family][0],
                    (artifact,),
                    authority,
                )
            )

        return inspect

    def test_all_seams_produce_six_canonical_family_descriptors(self) -> None:
        called: list[str] = []
        seams = {
            "_inventory_source_authority": self._seam(
                "source", "outcome_revision", called
            ),
            "_inventory_issue_routes": self._seam(
                "issue", "issue_route_replay", called
            ),
            "_inventory_live_anchors": self._seam("live", "live_outstanding", called),
            "_inventory_outcome_registry": self._seam(
                "outcome", "outcome_revision", called
            ),
            "_inventory_guard_records": self._seam("guard", "guard", called),
            "_inventory_trusted_records": self._seam("trusted", "trusted_time", called),
            "_inventory_shadow_actions": self._seam("shadow", "shadow", called),
        }
        patches = [
            mock.patch.object(inventory, name, side_effect=seam)
            for name, seam in seams.items()
        ]
        with mock.patch.object(
            inventory, "_load_verified_state", return_value=object()
        ):
            for patcher in patches:
                patcher.start()
                self.addCleanup(patcher.stop)
            result = inventory.inspect_closed_workset(
                active_root=Path("/tmp/workset-inventory-active"),
                shadow_root=Path("/tmp/workset-inventory-shadow"),
                context=self._context(),
                machine_now=FIXED_NOW,
            )

        self.assertEqual(
            called,
            ["source", "issue", "live", "outcome", "guard", "trusted", "shadow"],
        )
        self.assertEqual(
            tuple(item.family for item in result.families), inventory.FAMILIES
        )
        self.assertEqual(len(result.families), 6)
        self.assertEqual(sum(item.actionable_count for item in result.families), 7)
        self.assertTrue(
            all(item.record_count >= item.actionable_count for item in result.families)
        )
        identities = [
            (inventory.FAMILIES.index(item.family), item.natural_key)
            for item in result.items
        ]
        self.assertEqual(identities, sorted(identities))
        self.assertEqual(len({item.natural_key for item in result.items}), 7)
        self.assertEqual(len({item.namespace_digest for item in result.items}), 7)
        self.assertEqual(len({item.namespace_digest for item in result.families}), 6)

    def test_unknown_shared_object_suffix_fails_closed(self) -> None:
        with self.assertRaises(inventory.WorksetInventoryIntegrityError):
            inventory._shared_suffix(Path("not-content-addressed.json"))  # noqa: SLF001

    def test_request_without_der_is_repairable_only_before_response_chain(self) -> None:
        target = date(2030, 1, 2)
        self.assertEqual(
            inventory._trusted_der_repair_targets(  # noqa: SLF001
                requests={target},
                request_der=set(),
                links=set(),
                receipts=set(),
            ),
            (target,),
        )
        with self.assertRaises(inventory.WorksetInventoryIntegrityError):
            inventory._trusted_der_repair_targets(  # noqa: SLF001
                requests={target},
                request_der=set(),
                links={target},
                receipts=set(),
            )
        with self.assertRaises(inventory.WorksetInventoryIntegrityError):
            inventory._trusted_der_repair_targets(  # noqa: SLF001
                requests=set(),
                request_der={target},
                links=set(),
                receipts=set(),
            )

    def test_guard_supersedes_only_without_a_live_issue_boundary(self) -> None:
        target = date(2030, 1, 2)
        before_target = datetime(2030, 1, 1, 15, 59, 59, tzinfo=timezone.utc)
        at_target = datetime(2030, 1, 1, 16, 0, 0, tzinfo=timezone.utc)
        decide = inventory._guard_pending_successor  # noqa: SLF001
        self.assertEqual(
            decide(
                target=target,
                machine_now=before_target,
                target_timezone="Asia/Shanghai",
                opened_events=(),
                sealed_events=(),
                target_outcome_or_backfill_present=False,
            ),
            "guard_completion_recorded",
        )
        self.assertEqual(
            decide(
                target=target,
                machine_now=at_target,
                target_timezone="Asia/Shanghai",
                opened_events=(),
                sealed_events=(),
                target_outcome_or_backfill_present=False,
            ),
            "superseded_by_backfill",
        )
        self.assertEqual(
            decide(
                target=target,
                machine_now=before_target,
                target_timezone="Asia/Shanghai",
                opened_events=(),
                sealed_events=(),
                target_outcome_or_backfill_present=True,
            ),
            "superseded_by_backfill",
        )
        self.assertEqual(
            decide(
                target=target,
                machine_now=at_target,
                target_timezone="Asia/Shanghai",
                opened_events=(object(),),
                sealed_events=(object(),),
                target_outcome_or_backfill_present=True,
            ),
            "guard_completion_recorded",
        )
        with self.assertRaises(inventory.WorksetInventoryIntegrityError):
            decide(
                target=target,
                machine_now=before_target,
                target_timezone="Asia/Shanghai",
                opened_events=(object(),),
                sealed_events=(),
                target_outcome_or_backfill_present=False,
            )

    def test_published_unconsumed_outcome_chain_blocks_same_date_settlement(
        self,
    ) -> None:
        target = date(2030, 1, 2)
        target_text = target.isoformat()
        revision = "revision-a"
        seed = inventory.ArtifactRef(
            role="live_fixture",
            root="active",
            path="fixtures/live.json",
            sha256="a" * 64,
            size_bytes=1,
        )
        live_key = "live_outstanding:published-outcome-fixture"
        items = [
            inventory._item(  # noqa: SLF001
                "live_outstanding",
                live_key,
                "outcome_batch_settled",
                (seed,),
                {"target_date": target_text, "terminal": False},
            )
        ]
        receipt = SimpleNamespace(path=Path("/active/receipt.json"), sha256="1" * 64)
        exact = SimpleNamespace(path=Path("/active/outcome.json"), sha256="2" * 64)
        source_manifest = SimpleNamespace(
            path=Path("/active/source.json"), sha256="3" * 64
        )
        registered = SimpleNamespace(
            receipt=receipt,
            exact_object=exact,
            payload={
                "source_revision_id": revision,
                "source_manifest": {
                    "path": str(source_manifest.path),
                    "sha256": source_manifest.sha256,
                    "size_bytes": 1,
                },
            },
            raw=b"published-outcome\n",
            revision_sequence_id=1,
        )
        chain = SimpleNamespace(
            receipts=(registered,),
            tip=registered,
            pointed=registered,
            active_pointer_path=Path("/absent/active.json"),
        )
        projection = SimpleNamespace(
            epoch_id="old-epoch-a",
            revision_ids={},
            ledger_events=(),
            outstanding_target_date=target,
        )
        source_record = SimpleNamespace(day=target, revision_id=revision)
        state = SimpleNamespace(
            active_root=Path("/active"),
            maximum_bytes=1024,
            live_module=object(),
            live_projection=projection,
            prerequisites=object(),
            source=SimpleNamespace(records=(source_record,)),
        )

        def artifact(_path, *, role, root_label, root, maximum_bytes):
            del root, maximum_bytes
            return inventory.ArtifactRef(
                role=role,
                root=root_label,
                path=f"fixtures/{role}.json",
                sha256="b" * 64,
                size_bytes=1,
            )

        with (
            mock.patch(
                "monitoring.ootang_outcome_materializer.load_config",
                return_value={"_live_profile": {}},
            ),
            mock.patch(
                "monitoring.ootang_outcome_materializer._runtime_path",
                side_effect=lambda _profile, _root, name: Path(f"/active/{name}"),
            ),
            mock.patch(
                "monitoring.ootang_epoch_drain._strict_outcome_receipt_targets",
                return_value=(target,),
            ),
            mock.patch(
                "monitoring.ootang_outcome_materializer._scan_receipt_chain",
                return_value=chain,
            ),
            mock.patch.object(inventory, "_strict_dated_records", return_value={}),
            mock.patch(
                "monitoring.ootang_outcome_materializer._pending_registered_tip"
            ),
            mock.patch(
                "monitoring.ootang_outcome_materializer._outcome_inbox_path",
                return_value=Path("/absent/outcome.json"),
            ),
            mock.patch(
                "monitoring.ootang_outcome_materializer._legal_active_bytes",
                return_value=registered.raw,
            ),
            mock.patch(
                "monitoring.ootang_outcome_materializer._artifact_from_mapping",
                return_value=source_manifest,
            ),
            mock.patch.object(inventory, "_artifact", side_effect=artifact),
        ):
            inventory._inventory_outcome_registry(  # noqa: SLF001
                state,
                inventory._new_builders(),
                items,  # noqa: SLF001
            )

        outcome_items = [item for item in items if item.family == "outcome_revision"]
        self.assertEqual(len(outcome_items), 1)
        outcome_item = outcome_items[0]
        self.assertEqual(
            outcome_item.canonical_successor_state, "outcome_or_revision_consumed"
        )
        self.assertTrue(outcome_item.authority["tip_published"])
        self.assertFalse(outcome_item.authority["tip_ledger_consumed"])
        live_item = next(item for item in items if item.natural_key == live_key)
        self.assertEqual(live_item.dependency_keys, (outcome_item.natural_key,))


if __name__ == "__main__":
    unittest.main()
