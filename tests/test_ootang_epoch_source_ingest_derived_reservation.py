"""Fast contracts for source-ingest derived-key reservations."""

from __future__ import annotations

from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_epoch_source_ingest_derived_reservation as derived  # noqa: E402
from monitoring import ootang_epoch_workset_inventory as inventory  # noqa: E402
from monitoring import ootang_epoch_workset_manifest as manifest  # noqa: E402
from monitoring import ootang_live_source as source  # noqa: E402
from monitoring import ootang_outcome_materializer as materializer  # noqa: E402
from tests import test_ootang_epoch_workset_manifest as manifest_test  # noqa: E402
from tests import test_ootang_live_source as source_test  # noqa: E402


SOURCE_NOW = manifest_test.FIXED_NOW + timedelta(seconds=1)
AUTHORITY_NOW = SOURCE_NOW + timedelta(seconds=1)


class EpochSourceIngestDerivedReservationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source_fixture = source_test._Fixture()  # noqa: SLF001
        self.addCleanup(self.source_fixture.close)
        self.manifest_fixture = manifest_test.WorksetManifestSyntheticTests(
            "test_six_families_publish_one_exact_reservation_and_replay_idempotently"
        )
        self.manifest_fixture.setUp()
        self.addCleanup(self.manifest_fixture.doCleanups)
        self._bind_manifest_to_source_runtime()

        baseline_result = self.source_fixture.ingest(count=1)
        self.assertEqual(baseline_result.status, "ready")
        self.assertIsNotNone(baseline_result.source)
        self.baseline = baseline_result.source
        assert self.baseline is not None
        self.assertEqual(self.baseline.snapshot_sequence_id, 1)
        self.source_fixture.write(self.source_fixture.payload(count=3))
        self.source_item = self._source_ingest_item()

    def _bind_manifest_to_source_runtime(self) -> None:
        self.manifest_fixture.active_root = self.source_fixture.root.resolve()
        self.manifest_fixture.shadow_root = (
            self.source_fixture.root / "shadow"
        ).resolve()
        self.manifest_fixture.shadow_root.mkdir(parents=True, exist_ok=True)

    def _source_ingest_item(
        self,
        *,
        expected_changed: tuple[tuple[str, str], ...] = (
            ("2020-07-02", "source-revision-2"),
            ("2020-07-03", "source-revision-3"),
        ),
    ) -> inventory.InventoryItem:
        activation = source.load_activation_source(
            self.source_fixture.profile,
            runtime_root=self.source_fixture.root,
        )
        state = inventory._VerifiedState(  # noqa: SLF001
            active_root=self.source_fixture.root.resolve(),
            shadow_root=self.manifest_fixture.shadow_root,
            maximum_bytes=64 * 1024 * 1024,
            now=SOURCE_NOW,
            identity={"old_live_epoch_id": "old-epoch-a"},
            source_module=source,
            source_profile=self.source_fixture.profile,
            source=self.baseline,
            activation_source=activation,
            live_module=None,
            live_profile={},
            live_paths=None,
            prerequisites=None,
            live_projection=None,
            shadow_module=None,
            shadow_profile={},
            shadow_paths=None,
            shadow_projection=None,
        )
        builders = {
            family: inventory._FamilyBuilder([], [], {})  # noqa: SLF001
            for family in inventory.FAMILIES
        }
        items: list[inventory.InventoryItem] = []
        # Shared-object closure is outside this focused contract. The source
        # pointer, activation manifest, feed and changed-record derivation stay real.
        with (
            mock.patch.object(inventory, "_shared_root_documents", return_value=()),
            mock.patch.object(inventory, "_shared_object_closure", return_value=()),
        ):
            inventory._inventory_source_authority(  # noqa: SLF001
                state, builders, items
            )
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.canonical_successor_state, "source_snapshot_ingested")
        self.assertEqual(
            tuple(
                (row["target_date"], row["source_revision_id"])
                for row in item.authority["changed_or_appended_revisions"]
            ),
            expected_changed,
        )
        self.assertEqual(
            [
                (
                    row["observed_at_utc"],
                    row["available_at_utc"],
                    row["finalized_at_utc"],
                )
                for row in item.authority["changed_or_appended_revisions"]
            ],
            [
                (
                    f"{target}T04:00:00Z",
                    f"{target}T05:00:00Z",
                    f"{target}T06:00:00Z",
                )
                for target, _revision in expected_changed
            ],
        )
        return item

    def _outstanding_item(self) -> inventory.InventoryItem:
        target = "2020-07-02"
        issue_id = "issue-a"
        seal = "a" * 64
        issue_sha = "b" * 64
        authority = {
            "record_type": "outstanding_live_lifecycle",
            "target_date": target,
            "old_live_epoch_id": "old-epoch-a",
            "issue_id": issue_id,
            "issue_sha256": issue_sha,
            "input_manifest_sha256": "c" * 64,
            "seal_event": {
                "sequence_id": 10,
                "entry_sha256": seal,
                "event_type": "issue_batch_sealed",
                "target_date": target,
                "issue_id": issue_id,
            },
            "anchor_confirmed_event": None,
            "frozen_live_upper_tip": "3" * 64,
            "terminal": False,
            "action": "anchor_request_recorded",
        }
        artifact_path = self.manifest_fixture._write(  # noqa: SLF001
            self.source_fixture.root,
            "inventory/outstanding-live.json",
            {"target_date": target, "seal_entry_sha256": seal},
        )
        artifact = inventory._artifact(  # noqa: SLF001
            artifact_path,
            role="outstanding_live_issue",
            root_label="active",
            root=self.source_fixture.root,
            maximum_bytes=inventory.DEFAULT_MAXIMUM_ARTIFACT_BYTES,
        )
        return inventory._item(  # noqa: SLF001
            "live_outstanding",
            inventory._key(  # noqa: SLF001
                "live_outstanding",
                "old-epoch-a",
                target,
                issue_id,
                seal,
                issue_sha,
            ),
            "anchor_request_recorded",
            (artifact,),
            authority,
        )

    def _anchor_repair_item(self) -> inventory.InventoryItem:
        """Return the other real actionable shape in ``live_outstanding``."""
        target = "2020-07-01"
        issue_id = "issue-confirmed"
        seal = "e" * 64
        confirmed = "f" * 64
        authority = {
            "record_type": "anchor_confirmation",
            "target_date": target,
            "old_live_epoch_id": "old-epoch-a",
            "issue_id": issue_id,
            "seal_entry_sha256": seal,
            "confirmed_event": {
                "sequence_id": 9,
                "entry_sha256": confirmed,
                "event_type": "anchor_confirmed",
                "target_date": target,
                "issue_id": issue_id,
            },
            "receipt_present": False,
            "terminal": False,
            "action": "repair_from_confirmed_event",
        }
        artifact = inventory._artifact(  # noqa: SLF001
            self.source_fixture.pointer_path,
            role="anchor_repair_live_prerequisite_source_pointer",
            root_label="active",
            root=self.source_fixture.root,
            maximum_bytes=inventory.DEFAULT_MAXIMUM_ARTIFACT_BYTES,
        )
        return inventory._item(  # noqa: SLF001
            "live_outstanding",
            inventory._key(  # noqa: SLF001
                "live_outstanding",
                "old-epoch-a",
                target,
                issue_id,
                seal,
                "anchor-receipt-repair",
            ),
            "anchor_receipt_repaired",
            (artifact,),
            authority,
        )

    def _baseline_machine_outcome_item(self) -> inventory.InventoryItem:
        """Frozen K0 item whose key survives N+1 but namespace must rebound."""
        target = date(2020, 7, 1)
        record = next(row for row in self.baseline.records if row.day == target)
        natural_key = inventory._key(  # noqa: SLF001
            "outcome_revision",
            "old-epoch-a",
            target.isoformat(),
            record.revision_id,
            inventory.ZERO_HASH,
            self.baseline.outcome_source_id,
        )
        authority = {
            "record_type": "machine_selected_source_outcome",
            "selection_kind": "backfill",
            "target_date": target.isoformat(),
            "old_live_epoch_id": "old-epoch-a",
            "outcome_source_id": self.baseline.outcome_source_id,
            "source_revision_id": record.revision_id,
            "previous_revision_id": None,
            "previous_outcome_sha256": None,
            "source_snapshot_sequence_id": self.baseline.snapshot_sequence_id,
            "source_snapshot_receipt_sha256": self.baseline.snapshot_receipt.sha256,
            "live_issue_seal_entry_sha256": inventory.ZERO_HASH,
            "terminal": False,
            "action": "outcome_materialized",
        }
        pointer_raw = self.source_fixture.pointer_path.read_bytes()
        historical_pointer = source.ArtifactRef(
            path=self.source_fixture.pointer_path,
            sha256=hashlib.sha256(pointer_raw).hexdigest(),
            size_bytes=len(pointer_raw),
        )
        return inventory._item(  # noqa: SLF001
            "outcome_revision",
            natural_key,
            "outcome_materialized",
            derived._source_artifacts_for_record(  # noqa: SLF001
                self._paths(),
                source.load_deploy_profile(),
                self.baseline,
                target,
                historical_pointer=historical_pointer,
            ),
            authority,
            dependency_keys=(self.source_item.natural_key,),
        )

    @staticmethod
    def _frozen_prefix(
        *,
        last_finalized: date = date(2020, 7, 1),
        outstanding: date | None = None,
        events: tuple[SimpleNamespace, ...] = (),
    ) -> SimpleNamespace:
        projection = SimpleNamespace(
            epoch_id="old-epoch-a",
            revision_ids={"2020-07-01": {"source-revision-1": "d" * 64}},
            outstanding_target_date=outstanding,
            last_finalized_date=last_finalized,
            ledger_events=events,
        )
        return SimpleNamespace(projection=projection, frozen_events=events)

    @staticmethod
    def _manifest_item(value: inventory.InventoryItem) -> manifest.WorksetItem:
        artifacts = tuple(
            manifest.ArtifactObligation(
                **inventory._artifact_payload(artifact)  # noqa: SLF001
            )
            for artifact in value.artifacts
        )
        return manifest.WorksetItem(
            value.family,
            value.natural_key,
            value.canonical_successor_state,
            value.dependency_keys,
            artifacts,
            value.authority,
            value.namespace_digest,
        )

    def _inspection(
        self, values: tuple[inventory.InventoryItem, ...]
    ) -> manifest.WorksetInspection:
        items = tuple(
            sorted(
                (self._manifest_item(value) for value in values),
                key=lambda item: (
                    manifest.FAMILIES.index(item.family),
                    item.natural_key,
                ),
            )
        )
        families = tuple(
            manifest._make_family(  # noqa: SLF001
                family,
                tuple(
                    {
                        (artifact.root, artifact.path, artifact.role): artifact
                        for item in items
                        if item.family == family
                        for artifact in item.artifacts
                    }.values()
                ),
                record_count=sum(item.family == family for item in items),
                actionable_count=sum(item.family == family for item in items),
                authority={"terminal_and_pending_records_replayed": True},
            )
            for family in manifest.FAMILIES
        )
        return manifest.WorksetInspection(
            self.manifest_fixture._context(),  # noqa: SLF001
            items,
            families,
        )

    def _publish_manifest(
        self,
        values: tuple[inventory.InventoryItem, ...] | None = None,
        *,
        namespace: str | None = None,
    ) -> manifest.AdmissionCutBinding:
        if namespace is not None:
            self.manifest_fixture._use_namespace(namespace)  # noqa: SLF001
            self._bind_manifest_to_source_runtime()
        binding = self.manifest_fixture._binding()  # noqa: SLF001
        result = self.manifest_fixture._run(  # noqa: SLF001
            self._inspection(values or (self.source_item,)), binding=binding
        )
        self.assertEqual(result.status, "closed_workset_reserved")
        return binding

    def _publish_seq2(self) -> source.CanonicalSource:
        result = source.ingest_source(
            self.source_fixture.profile,
            runtime_root=self.source_fixture.root,
            now=SOURCE_NOW,
        )
        self.assertEqual(result.status, "ready")
        self.assertIsNotNone(result.source)
        current = result.source
        assert current is not None
        self.assertEqual(current.snapshot_sequence_id, 2)
        receipt = json.loads(current.snapshot_receipt.path.read_bytes())
        self.assertEqual(
            receipt["predecessor_snapshot_receipt"]["sha256"],
            self.baseline.snapshot_receipt.sha256,
        )
        return current

    def _paths(self) -> derived.SourceIngestDerivedPaths:
        profile = derived.load_source_ingest_derived_profile()
        return derived.source_ingest_derived_paths(
            profile,
            registry_root=self.manifest_fixture.registry_root,
            active_root=self.source_fixture.root,
            shadow_root=self.manifest_fixture.shadow_root,
        )

    def _load_authority(
        self,
        binding: manifest.AdmissionCutBinding,
        *,
        frozen_prefix: SimpleNamespace | None = None,
    ) -> derived.SourceIngestDerivedAuthority | None:
        prefix = frozen_prefix or self._frozen_prefix()
        with mock.patch.object(
            manifest, "_default_admission_cut_binding", return_value=binding
        ):
            return derived._load_source_ingest_authority(  # noqa: SLF001
                self._paths(),
                AUTHORITY_NOW,
                load_frozen_prefix=lambda _reservation: prefix,
            )

    def _coordinate(
        self, authority: derived.SourceIngestDerivedAuthority | None
    ) -> derived.SourceIngestDerivedResult:
        return derived._coordinate_source_ingest_derived_reservation(  # noqa: SLF001
            registry_root=self.manifest_fixture.registry_root,
            active_root=self.source_fixture.root,
            shadow_root=self.manifest_fixture.shadow_root,
            clock=lambda: AUTHORITY_NOW,
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

    def _upstream_bytes(
        self, paths: derived.SourceIngestDerivedPaths
    ) -> dict[Path, bytes]:
        excluded = paths.root.resolve()
        roots = (
            self.source_fixture.root.resolve(),
            self.manifest_fixture.registry_root.resolve(),
        )
        return {
            path: path.read_bytes()
            for root in roots
            for path in root.rglob("*")
            if path.is_file() and excluded not in path.resolve().parents
        }

    def test_published_seq2_reserves_two_derived_keys_content_addressably(
        self,
    ) -> None:
        binding = self._publish_manifest()
        current = self._publish_seq2()
        authority = self._load_authority(binding)
        self.assertIsNotNone(authority)
        paths = self._paths()
        before = self._upstream_bytes(paths)
        with (
            mock.patch.object(
                source,
                "ingest_source",
                side_effect=AssertionError("reservation must not ingest source"),
            ),
            mock.patch.object(
                materializer,
                "_publish_candidate",
                side_effect=AssertionError("reservation must not materialize outcome"),
            ),
        ):
            result = self._coordinate(authority)

        self.assertEqual(result.status, "source_ingest_derived_keys_reserved")
        self.assertEqual(result.derived_key_count, 2)
        self.assertEqual(result.rebound_item_count, 0)
        self.assertEqual(result.invalidated_item_count, 0)
        self.assertIsNotNone(result.reservation_path)
        self.assertIsNotNone(result.event_path)
        reservation_path = result.reservation_path  # type: ignore[assignment]
        event_path = result.event_path  # type: ignore[assignment]
        reservation_raw = reservation_path.read_bytes()
        reservation = json.loads(reservation_raw)
        event = json.loads(event_path.read_bytes())
        reservation_sha256 = hashlib.sha256(reservation_raw).hexdigest()
        self.assertEqual(reservation_path.name, f"{reservation_sha256}.json")
        self.assertEqual(
            event["reservation"], self._reference(reservation_path, paths.root)
        )
        self.assertEqual(
            reservation["source_ingest"]["natural_key"],
            self.source_item.natural_key,
        )
        self.assertEqual(reservation["successor_snapshot"]["snapshot_sequence_id"], 2)
        self.assertEqual(
            reservation["successor_snapshot"]["snapshot_receipt_sha256"],
            current.snapshot_receipt.sha256,
        )
        rows = reservation["derived_outcome_items"]
        self.assertEqual(
            [
                (
                    row["authority"]["target_date"],
                    row["authority"]["source_revision_id"],
                )
                for row in rows
            ],
            [
                ("2020-07-02", "source-revision-2"),
                ("2020-07-03", "source-revision-3"),
            ],
        )
        expected_keys = [
            "outcome_revision:old-epoch-a:2020-07-02:source-revision-2:"
            f"{inventory.ZERO_HASH}:ootang-machine-source-v1",
            "outcome_revision:old-epoch-a:2020-07-03:source-revision-3:"
            f"{inventory.ZERO_HASH}:ootang-machine-source-v1",
        ]
        self.assertEqual([row["natural_key"] for row in rows], expected_keys)
        self.assertEqual(
            [row["dependency_keys"] for row in rows],
            [
                [self.source_item.natural_key],
                [expected_keys[0], self.source_item.natural_key],
            ],
        )
        self.assertTrue(
            all(
                row["canonical_successor_state"] == "outcome_materialized"
                for row in rows
            )
        )
        self.assertEqual(reservation["rebound_existing_items"], [])
        self.assertEqual(reservation["invalidated_existing_items"], [])
        for payload in (
            reservation,
            event,
            json.loads(result.status_path.read_bytes()),
        ):
            for claim in derived.TRUE_CAPABILITIES:
                self.assertTrue(payload[claim], claim)
            for claim in derived.FALSE_CLAIMS:
                self.assertFalse(payload[claim], claim)
        self.assertEqual({path: path.read_bytes() for path in before}, before)

        reservation_before = reservation_path.read_bytes()
        event_before = event_path.read_bytes()
        replay = self._coordinate(authority)
        self.assertEqual(replay.derived_key_count, 2)
        self.assertEqual(reservation_path.read_bytes(), reservation_before)
        self.assertEqual(event_path.read_bytes(), event_before)
        self.assertEqual(len(tuple(paths.reservation_objects.glob("*.json"))), 1)
        self.assertEqual(len(tuple(paths.events.glob("*.json"))), 1)

    def test_object_before_event_crash_only_forward_adopts_event(self) -> None:
        binding = self._publish_manifest()
        self._publish_seq2()
        authority = self._load_authority(binding)
        self.assertIsNotNone(authority)
        paths = self._paths()

        with (
            mock.patch.object(
                derived,
                "_append_event",
                side_effect=RuntimeError("synthetic derived object-only crash"),
            ),
            self.assertRaisesRegex(RuntimeError, "derived object-only crash"),
        ):
            self._coordinate(authority)

        objects = tuple(paths.reservation_objects.glob("*.json"))
        self.assertEqual(len(objects), 1)
        object_before = objects[0].read_bytes()
        self.assertEqual(tuple(paths.events.glob("*.json")), ())
        upstream_before = self._upstream_bytes(paths)

        with (
            mock.patch.object(
                source,
                "ingest_source",
                side_effect=AssertionError("reservation must not ingest source"),
            ),
            mock.patch.object(
                materializer,
                "_publish_candidate",
                side_effect=AssertionError("reservation must not materialize outcome"),
            ),
        ):
            result = self._coordinate(authority)

        self.assertEqual(result.status, "source_ingest_derived_event_forward_adopted")
        self.assertEqual(result.derived_key_count, 2)
        self.assertEqual(objects[0].read_bytes(), object_before)
        self.assertEqual(
            {path: path.read_bytes() for path in upstream_before}, upstream_before
        )
        self.assertEqual(len(tuple(paths.reservation_objects.glob("*.json"))), 1)
        self.assertEqual(len(tuple(paths.events.glob("*.json"))), 1)

    def test_seq2_not_published_waits_without_reservation(self) -> None:
        binding = self._publish_manifest()
        authority = self._load_authority(binding)
        paths = self._paths()

        result = self._coordinate(authority)

        self.assertEqual(result.status, "waiting_for_exact_next_source_snapshot")
        self.assertEqual(result.derived_key_count, 0)
        self.assertEqual(result.rebound_item_count, 0)
        self.assertEqual(result.invalidated_item_count, 0)
        self.assertIsNone(result.reservation_path)
        self.assertIsNone(result.event_path)
        self.assertEqual(tuple(paths.reservation_objects.glob("*.json")), ())
        self.assertEqual(tuple(paths.events.glob("*.json")), ())
        current = source.load_current_source(
            self.source_fixture.profile, runtime_root=self.source_fixture.root
        )
        self.assertEqual(current.snapshot_sequence_id, 1)

    def test_source_diff_mismatch_fails_closed(self) -> None:
        changed = list(self.source_item.authority["changed_or_appended_revisions"])
        tampered = inventory._item(  # noqa: SLF001
            self.source_item.family,
            self.source_item.natural_key,
            self.source_item.canonical_successor_state,
            self.source_item.artifacts,
            {
                **self.source_item.authority,
                "changed_or_appended_revisions": [changed[0], changed[0]],
            },
        )
        binding = self._publish_manifest((tampered,))
        self._publish_seq2()
        paths = self._paths()

        with self.assertRaisesRegex(RuntimeError, "(?i:diff|changed|snapshot)"):
            authority = self._load_authority(binding)
            self._coordinate(authority)

        self.assertEqual(tuple(paths.reservation_objects.glob("*.json")), ())
        self.assertEqual(tuple(paths.events.glob("*.json")), ())

    def test_unchanged_pending_key_rebinds_to_successor_snapshot_namespace(
        self,
    ) -> None:
        frozen_machine = self._baseline_machine_outcome_item()
        binding = self._publish_manifest((frozen_machine, self.source_item))
        current = self._publish_seq2()
        authority = self._load_authority(
            binding,
            frozen_prefix=self._frozen_prefix(
                last_finalized=date(2020, 6, 30),
            ),
        )
        self.assertIsNotNone(authority)

        result = self._coordinate(authority)

        self.assertEqual(result.derived_key_count, 2)
        self.assertEqual(result.rebound_item_count, 1)
        self.assertEqual(result.invalidated_item_count, 0)
        reservation = json.loads(result.reservation_path.read_bytes())
        self.assertEqual(
            [
                row["authority"]["target_date"]
                for row in reservation["derived_outcome_items"]
            ],
            ["2020-07-02", "2020-07-03"],
        )
        rebound = reservation["rebound_existing_items"]
        self.assertEqual(len(rebound), 1)
        self.assertEqual(rebound[0]["natural_key"], frozen_machine.natural_key)
        self.assertNotEqual(
            rebound[0]["namespace_digest"], frozen_machine.namespace_digest
        )
        self.assertEqual(rebound[0]["authority"]["source_snapshot_sequence_id"], 2)
        self.assertEqual(
            rebound[0]["authority"]["source_snapshot_receipt_sha256"],
            current.snapshot_receipt.sha256,
        )

    def test_revised_pending_key_invalidates_k0_without_d_or_r_dependency(
        self,
    ) -> None:
        payload = self.source_fixture.payload(count=3)
        records = payload["records"]
        self.assertIsInstance(records, list)
        first = records[0]
        self.assertIsInstance(first, dict)
        first["revision_id"] = "source-revision-1b"
        self.source_fixture.write(payload)
        self.source_item = self._source_ingest_item(
            expected_changed=(
                ("2020-07-01", "source-revision-1b"),
                ("2020-07-02", "source-revision-2"),
                ("2020-07-03", "source-revision-3"),
            )
        )
        frozen_machine = self._baseline_machine_outcome_item()
        old_key = frozen_machine.natural_key
        binding = self._publish_manifest((frozen_machine, self.source_item))
        self._publish_seq2()
        authority = self._load_authority(binding)
        self.assertIsNotNone(authority)

        result = self._coordinate(authority)

        self.assertEqual(result.derived_key_count, 3)
        self.assertEqual(result.rebound_item_count, 0)
        self.assertEqual(result.invalidated_item_count, 1)
        reservation = json.loads(result.reservation_path.read_bytes())
        invalidated = reservation["invalidated_existing_items"]
        self.assertEqual([row["natural_key"] for row in invalidated], [old_key])
        revised = next(
            row
            for row in reservation["derived_outcome_items"]
            if row["authority"]["target_date"] == "2020-07-01"
        )
        self.assertEqual(
            revised["authority"]["source_revision_id"], "source-revision-1b"
        )
        self.assertEqual(revised["authority"]["selection_kind"], "revision")
        self.assertEqual(
            revised["authority"]["previous_revision_id"], "source-revision-1"
        )
        self.assertEqual(revised["authority"]["previous_outcome_sha256"], "d" * 64)
        self.assertNotEqual(revised["natural_key"], old_key)
        for row in (
            *reservation["derived_outcome_items"],
            *reservation["rebound_existing_items"],
        ):
            self.assertNotIn(old_key, row["dependency_keys"])

    def test_published_seq2_sidecar_replays_after_source_advances_to_seq3(
        self,
    ) -> None:
        binding = self._publish_manifest()
        seq2 = self._publish_seq2()
        authority = self._load_authority(binding)
        self.assertIsNotNone(authority)
        first = self._coordinate(authority)
        paths = self._paths()
        reservation_before = first.reservation_path.read_bytes()
        event_before = first.event_path.read_bytes()

        self.source_fixture.write(self.source_fixture.payload(count=4))
        advanced = source.ingest_source(
            self.source_fixture.profile,
            runtime_root=self.source_fixture.root,
            now=AUTHORITY_NOW + timedelta(seconds=1),
        )
        self.assertEqual(advanced.status, "ready")
        self.assertIsNotNone(advanced.source)
        self.assertEqual(advanced.source.snapshot_sequence_id, 3)

        replay_authority = self._load_authority(binding)
        self.assertIsNotNone(replay_authority)
        self.assertEqual(
            replay_authority.successor_source.snapshot_sequence_id,
            seq2.snapshot_sequence_id,
        )
        self.assertEqual(
            replay_authority.successor_source.snapshot_receipt.sha256,
            seq2.snapshot_receipt.sha256,
        )
        replay = self._coordinate(replay_authority)

        self.assertEqual(replay.status, "source_ingest_derived_keys_already_reserved")
        self.assertEqual(replay.reservation_path.read_bytes(), reservation_before)
        self.assertEqual(replay.event_path.read_bytes(), event_before)
        self.assertEqual(len(tuple(paths.reservation_objects.glob("*.json"))), 1)
        self.assertEqual(len(tuple(paths.events.glob("*.json"))), 1)
        current = source.load_current_source(
            self.source_fixture.profile,
            runtime_root=self.source_fixture.root,
        )
        self.assertEqual(current.snapshot_sequence_id, 3)

    def test_real_outstanding_seal_ignores_anchor_repair_family_peer(self) -> None:
        outstanding = self._outstanding_item()
        repair = self._anchor_repair_item()
        binding = self._publish_manifest((repair, outstanding, self.source_item))
        self._publish_seq2()
        seal_event = SimpleNamespace(
            event_type="issue_batch_sealed",
            target_date="2020-07-02",
            entry_sha256="a" * 64,
        )
        authority = self._load_authority(
            binding,
            frozen_prefix=self._frozen_prefix(
                outstanding=date(2020, 7, 2),
                events=(seal_event,),
            ),
        )
        self.assertIsNotNone(authority)

        result = self._coordinate(authority)

        self.assertEqual(result.derived_key_count, 1)
        reservation = json.loads(result.reservation_path.read_bytes())
        row = reservation["derived_outcome_items"][0]
        self.assertEqual(row["authority"]["selection_kind"], "outstanding")
        self.assertEqual(row["authority"]["target_date"], "2020-07-02")
        self.assertEqual(row["authority"]["live_issue_seal_entry_sha256"], "a" * 64)
        self.assertNotIn("2020-07-03", row["natural_key"])


if __name__ == "__main__":
    unittest.main()
