"""Contracts for the machine-only E2-B2 outcome materializer."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_live_source as source_module  # noqa: E402
from monitoring import ootang_outcome_materializer as materializer  # noqa: E402
from monitoring import ootang_prequential_live as live_module  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class _FakeArtifact:
    path: Path
    sha256: str
    size_bytes: int


class _FakeLiveError(RuntimeError):
    pass


class _FakeLive:
    LiveInputError = _FakeLiveError
    LiveIntegrityError = _FakeLiveError
    LivePrerequisiteError = _FakeLiveError

    def __init__(self) -> None:
        self.loads = 0

    def load_outcome_batch(self, path, _profile, _prerequisites):
        self.loads += 1
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return SimpleNamespace(
            target_date=date.fromisoformat(payload["target_date"]),
            sha256=_sha256(Path(path)),
        )


class OutcomeMaterializerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = materializer.load_config()
        temporary = tempfile.TemporaryDirectory(
            prefix="ootang-outcome-materializer-test-", dir=ROOT
        )
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.objects = self.root / "objects" / "sha256"
        self.objects.mkdir(parents=True)
        self.live = _FakeLive()
        self.live_profile: dict[str, object] = {}
        self.prerequisites = object()
        self.stations = self.profile["_deploy_profile"]["source_feed"][
            "station_order_live"
        ]

    def _artifact(self, path: Path, raw: bytes) -> _FakeArtifact:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return _FakeArtifact(path.resolve(), _sha256(path), len(raw))

    def _record(
        self,
        day: date,
        revision: str = "revision-1",
        *,
        offset: float = 0.0,
        finalized: str | None = None,
        rainfall: float = 2.0,
    ) -> source_module.DailySourceRecord:
        return source_module.DailySourceRecord(
            day=day,
            revision_id=revision,
            observed_at_utc=f"{day.isoformat()}T01:00:00Z",
            available_at_utc=f"{day.isoformat()}T02:00:00Z",
            finalized_at_utc=finalized or f"{day.isoformat()}T03:00:00Z",
            rainfall_mm=rainfall,
            reservoir_water_level_m=151.0,
            displacement_mm={
                station: offset + float(index)
                for index, station in enumerate(self.stations)
            },
        )

    def _source(
        self,
        records: tuple[source_module.DailySourceRecord, ...],
        *,
        watermark: date | None = None,
        exported: str | None = None,
    ) -> SimpleNamespace:
        dataset_raw = b"dataset\n"
        dataset = self._artifact(
            self.objects
            / f"{hashlib.sha256(dataset_raw).hexdigest()}.source.json",
            dataset_raw,
        )
        semantic_raw = b"semantic\n"
        semantic = self._artifact(
            self.objects
            / (
                f"{hashlib.sha256(semantic_raw).hexdigest()}."
                "source-manifest.json"
            ),
            semantic_raw,
        )
        activation = self._artifact(
            self.root / "source_snapshot" / "manifest.json", b"activation\n"
        )
        revision_heads: list[_FakeArtifact] = []
        for record in records:
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
            raw = materializer._canonical_bytes(
                {
                    "schema_version": "ootang_source_revision_receipt_v1",
                    "case": "ootang",
                    "outcome_source_id": "machine-source-1",
                    "target_date": record.day.isoformat(),
                    "revision_id": record.revision_id,
                    "revision_sequence_id": 1,
                    "record_sha256": materializer._canonical_digest(
                        receipt_record
                    ),
                    "record": receipt_record,
                    "accepted_source_semantic_manifest": {
                        "path": str(semantic.path),
                        "sha256": semantic.sha256,
                        "size_bytes": semantic.size_bytes,
                    },
                    "predecessor_revision_receipt": None,
                }
            )
            revision_heads.append(
                self._artifact(
                    self.objects
                    / f"{hashlib.sha256(raw).hexdigest()}.source-revision.json",
                    raw,
                )
            )
        snapshot_raw = materializer._canonical_bytes(
            {
                "schema_version": "ootang_source_snapshot_receipt_v1",
                "case": "ootang",
                "profile_id": "ootang-prequential-deploy-v1",
                "outcome_source_id": "machine-source-1",
                "snapshot_sequence_id": 1,
                "source_pointer": {},
                "source_semantic_manifest": {},
                "predecessor_snapshot_receipt": None,
            }
        )
        snapshot_receipt = self._artifact(
            self.objects
            / (
                f"{hashlib.sha256(snapshot_raw).hexdigest()}."
                "source-snapshot-receipt.json"
            ),
            snapshot_raw,
        )
        last = watermark or records[-1].day
        return SimpleNamespace(
            watermark=last,
            outcome_source_id="machine-source-1",
            exported_at_utc=exported or f"{last.isoformat()}T04:00:00Z",
            records=records,
            dataset=dataset,
            semantic_manifest=semantic,
            activation_manifest=activation,
            revision_heads=tuple(revision_heads),
            snapshot_receipt=snapshot_receipt,
        )

    def _projection(
        self,
        *,
        last: date,
        outstanding: date | None = None,
        revisions: dict[str, dict[str, str]] | None = None,
        sealed: bool = True,
    ) -> SimpleNamespace:
        seal = (
            SimpleNamespace(target_date=outstanding.isoformat())
            if outstanding is not None and sealed
            else None
        )
        return SimpleNamespace(
            last_finalized_date=last,
            outstanding_target_date=outstanding,
            seal_event=seal,
            revision_ids=revisions or {},
        )

    def _candidate(
        self,
        source: SimpleNamespace,
        selection: materializer._Selection,
    ) -> tuple[materializer.Artifact, dict[str, object]]:
        manifest_payload = materializer._build_input_manifest(
            self.profile, source, selection
        )
        manifest = materializer._materialize_input_manifest(
            self.profile, self.root, manifest_payload
        )
        payload = materializer._outcome_payload(
            self.profile,
            selection,
            manifest,
            outcome_source_id=source.outcome_source_id,
        )
        return manifest, payload

    def _publish(
        self,
        source: SimpleNamespace,
        selection: materializer._Selection,
        manifest: materializer.Artifact,
        payload: dict[str, object],
        *,
        times: list[datetime] | None = None,
    ):
        values = iter(
            times
            or [
                datetime(2030, 1, 3, 5, tzinfo=timezone.utc),
                datetime(2030, 1, 3, 6, tzinfo=timezone.utc),
            ]
        )
        return materializer._publish_candidate(
            profile=self.profile,
            root=self.root,
            selection=selection,
            source_exported_at_utc=source.exported_at_utc,
            input_manifest=manifest,
            payload=payload,
            clock=lambda: next(values),
            live_module=self.live,
            live_profile=self.live_profile,
            prerequisites=self.prerequisites,
        )

    def _run_materializer(
        self,
        source: SimpleNamespace,
        projection: SimpleNamespace,
        *,
        clock,
    ) -> materializer.OutcomeMaterializerResult:
        with (
            mock.patch.object(
                materializer, "_missing_prerequisites", return_value=[]
            ),
            mock.patch.object(
                source_module, "load_current_source", return_value=source
            ),
            mock.patch.object(
                source_module, "load_activation_source", return_value=source
            ),
            mock.patch.object(
                live_module,
                "load_prerequisites",
                return_value=self.prerequisites,
            ),
            mock.patch.object(
                live_module,
                "load_verified_ledger_projection",
                return_value=projection,
            ),
            mock.patch.object(
                live_module,
                "load_outcome_batch",
                side_effect=self.live.load_outcome_batch,
            ),
        ):
            return materializer.materialize_outcome(
                runtime_root=self.root, clock=clock
            )

    def test_cycle_profile_is_exact_and_binds_both_profiles(self) -> None:
        self.assertEqual(
            self.profile["_profile_sha256"], materializer.DEFAULT_CONFIG_SHA256
        )
        self.assertEqual(
            self.profile["_deploy_profile"]["profile_id"],
            "ootang-prequential-deploy-v1",
        )
        self.assertEqual(
            self.profile["_live_profile"]["profile_id"],
            "ootang-prequential-live-v1",
        )

    def test_cycle_profile_rejects_changed_fixed_field_and_path_escape(self) -> None:
        payload = json.loads(materializer.DEFAULT_CONFIG_PATH.read_text())
        payload["runtime"]["outcome_receipts"] = "../escaped"
        path = self.root / "altered-cycle.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(materializer.OutcomeMaterializerConfigError):
            materializer.load_config(path)
        with self.assertRaises(materializer.OutcomeMaterializerConfigError):
            materializer._confined_child(
                self.root, "../escaped", name="runtime.outcome_receipts"
            )

    def test_empty_runtime_waits_without_creating_scientific_artifacts(self) -> None:
        result = materializer.materialize_outcome(
            runtime_root=self.root,
            clock=lambda: datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(result.status, "waiting_for_source_model_or_ledger")
        status = json.loads(result.status_path.read_text(encoding="utf-8"))
        self.assertEqual(status["materializer_status"], result.status)
        self.assertFalse(status["e2_live_evidence_eligible"])
        self.assertFalse((self.root / "outcome_inbox").exists())
        self.assertFalse((self.root / "outcome_receipts").exists())

    def test_nonblocking_deploy_lock_reports_busy_without_status_write(self) -> None:
        lock_path = self.root / "deploy_cycle.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock = lock_path.open("a+", encoding="utf-8")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.addCleanup(lock.close)
        with self.assertRaises(materializer.OutcomeMaterializerBusy):
            materializer.materialize_outcome(
                runtime_root=self.root,
                clock=lambda: datetime(2030, 1, 1, tzinfo=timezone.utc),
            )
        self.assertFalse(
            (self.root / "outcome_materializer_status.json").exists()
        )

    def test_revision_selection_precedes_outstanding_target(self) -> None:
        first = self._record(date(2030, 1, 1), "revision-2")
        second = self._record(date(2030, 1, 2), "revision-1")
        source = self._source((first, second))
        projection = self._projection(
            last=date(2030, 1, 1),
            outstanding=date(2030, 1, 2),
            revisions={"2030-01-01": {"revision-1": "1" * 64}},
        )
        selection = materializer._select_target(source, projection)
        self.assertEqual(selection.kind, "revision")
        self.assertEqual(selection.target_date, date(2030, 1, 1))
        self.assertEqual(selection.previous_revision_id, "revision-1")

    def test_outstanding_precedes_contiguous_backfill_and_requires_seal(self) -> None:
        first = self._record(date(2030, 1, 1))
        source = self._source((first,))
        projection = self._projection(
            last=date(2029, 12, 31), outstanding=date(2030, 1, 1)
        )
        self.assertEqual(
            materializer._select_target(source, projection).kind, "outstanding"
        )
        unsealed = self._projection(
            last=date(2029, 12, 31),
            outstanding=date(2030, 1, 1),
            sealed=False,
        )
        with self.assertRaises(materializer.OutcomeMaterializerInputError):
            materializer._select_target(source, unsealed)

    def test_contiguous_backfill_is_selected_without_outstanding(self) -> None:
        record = self._record(date(2030, 1, 2))
        source = self._source((record,))
        projection = self._projection(last=date(2030, 1, 1))
        selection = materializer._select_target(source, projection)
        self.assertEqual(selection.kind, "backfill")
        self.assertEqual(selection.target_date, date(2030, 1, 2))

    def test_known_old_revision_is_blocked_as_rollback(self) -> None:
        record = self._record(date(2030, 1, 1), "revision-1")
        source = self._source((record,))
        projection = self._projection(
            last=date(2030, 1, 1),
            revisions={
                "2030-01-01": {
                    "revision-1": "1" * 64,
                    "revision-2": "2" * 64,
                }
            },
        )
        with self.assertRaisesRegex(
            materializer.OutcomeMaterializerConflict, "rolled back"
        ):
            materializer._select_target(source, projection)

    def test_known_latest_revision_invokes_full_semantic_validator(self) -> None:
        record = self._record(date(2030, 1, 1), "revision-2")
        source = self._source((record,))
        projection = self._projection(
            last=date(2030, 1, 1),
            revisions={"2030-01-01": {"revision-2": "2" * 64}},
        )
        seen: list[tuple[date, str, str]] = []

        def validator(target, _record, revision, sha256):
            seen.append((target, revision, sha256))

        self.assertIsNone(
            materializer._select_target(
                source, projection, known_validator=validator
            )
        )
        self.assertEqual(
            seen, [(date(2030, 1, 1), "revision-2", "2" * 64)]
        )

    def test_first_publication_preflights_then_registers_exact_bytes(self) -> None:
        record = self._record(date(2030, 1, 2))
        source = self._source((record,))
        selection = materializer._Selection("outstanding", record.day, record)
        manifest, payload = self._candidate(source, selection)
        status, registered, _, state = self._publish(
            source, selection, manifest, payload
        )
        active = self.root / "outcome_inbox" / "2030-01-02.json"
        self.assertEqual(status, "materialized")
        self.assertTrue(state.changed)
        self.assertIsNone(state.previous_active_raw)
        self.assertEqual(active.read_bytes(), registered.raw)
        self.assertGreaterEqual(self.live.loads, 2)
        receipt = materializer._receipt_path(
            self.root / "outcome_receipts", record.day, record.revision_id
        )
        self.assertTrue(receipt.is_file())

    def test_same_revision_is_idempotent_and_preserves_first_bytes(self) -> None:
        record = self._record(date(2030, 1, 2))
        source = self._source((record,))
        selection = materializer._Selection("outstanding", record.day, record)
        manifest, payload = self._candidate(source, selection)
        self._publish(source, selection, manifest, payload)
        active = self.root / "outcome_inbox" / "2030-01-02.json"
        first = active.read_bytes()
        status, _, _, state = self._publish(
            source, selection, manifest, payload
        )
        self.assertEqual(status, "already_materialized_idempotent")
        self.assertFalse(state.changed)
        self.assertEqual(active.read_bytes(), first)

    def test_receipt_to_inbox_crash_is_recovered_from_exact_first_bytes(self) -> None:
        record = self._record(date(2030, 1, 2))
        source = self._source((record,))
        selection = materializer._Selection("outstanding", record.day, record)
        manifest, payload = self._candidate(source, selection)
        _, first_registered, _, _ = self._publish(
            source, selection, manifest, payload
        )
        active = self.root / "outcome_inbox" / "2030-01-02.json"
        active.unlink()
        existing = materializer._existing_candidate(
            profile=self.profile,
            root=self.root,
            source=source,
            selection=selection,
            live_module=self.live,
            live_profile=self.live_profile,
            prerequisites=self.prerequisites,
        )
        self.assertIsNotNone(existing)
        recovered_manifest, recovered_payload = existing
        status, _, _, state = self._publish(
            source, selection, recovered_manifest, recovered_payload
        )
        self.assertEqual(status, "materialized")
        self.assertTrue(state.changed)
        self.assertEqual(active.read_bytes(), first_registered.raw)

    def test_same_revision_changed_content_conflicts(self) -> None:
        record = self._record(date(2030, 1, 2))
        source = self._source((record,))
        selection = materializer._Selection("outstanding", record.day, record)
        manifest, payload = self._candidate(source, selection)
        self._publish(source, selection, manifest, payload)
        changed = json.loads(json.dumps(payload))
        changed["stations"][0]["actual_mm"] += 1.0
        with self.assertRaises(materializer.OutcomeMaterializerConflict):
            self._publish(source, selection, manifest, changed)

    def test_receipt_and_active_tamper_are_blocked(self) -> None:
        record = self._record(date(2030, 1, 2))
        source = self._source((record,))
        selection = materializer._Selection("outstanding", record.day, record)
        manifest, payload = self._candidate(source, selection)
        self._publish(source, selection, manifest, payload)
        receipt = materializer._receipt_path(
            self.root / "outcome_receipts", record.day, record.revision_id
        )
        original_receipt = receipt.read_bytes()
        receipt_payload = json.loads(original_receipt)
        receipt_payload["input_manifest_sha256"] = "f" * 64
        receipt.write_text(json.dumps(receipt_payload), encoding="utf-8")
        with self.assertRaises(materializer.OutcomeMaterializerConflict):
            self._publish(source, selection, manifest, payload)
        receipt.write_bytes(original_receipt)
        active = self.root / "outcome_inbox" / "2030-01-02.json"
        active.write_text("{}\n", encoding="utf-8")
        with self.assertRaises(materializer.OutcomeMaterializerConflict):
            self._publish(source, selection, manifest, payload)

    def test_new_revision_replaces_active_and_chains_immutable_archive(self) -> None:
        first_record = self._record(date(2030, 1, 2), "revision-1")
        first_source = self._source((first_record,))
        first_selection = materializer._Selection(
            "outstanding", first_record.day, first_record
        )
        first_manifest, first_payload = self._candidate(
            first_source, first_selection
        )
        _, first_registered, _, _ = self._publish(
            first_source, first_selection, first_manifest, first_payload
        )

        revised = replace(
            first_record,
            revision_id="revision-2",
            displacement_mm={station: 20.0 for station in self.stations},
        )
        revised_source = self._source((revised,))
        revised_selection = materializer._Selection(
            "revision",
            revised.day,
            revised,
            previous_revision_id="revision-1",
            previous_outcome_sha256=first_registered.exact_object.sha256,
        )
        revised_manifest, revised_payload = self._candidate(
            revised_source, revised_selection
        )
        status, revised_registered, _, state = self._publish(
            revised_source,
            revised_selection,
            revised_manifest,
            revised_payload,
        )
        self.assertEqual(status, "materialized")
        self.assertTrue(state.changed)
        self.assertEqual(state.previous_active_raw, first_registered.raw)
        self.assertEqual(
            revised_registered.previous_receipt.sha256,
            first_registered.receipt.sha256,
        )
        self.assertTrue(first_registered.receipt.path.is_file())
        self.assertTrue(first_registered.exact_object.path.is_file())

    def test_source_extension_reuses_target_scoped_first_manifest(self) -> None:
        record = self._record(date(2030, 1, 2))
        source = self._source((record,))
        selection = materializer._Selection("outstanding", record.day, record)
        manifest, payload = self._candidate(source, selection)
        self._publish(source, selection, manifest, payload)
        later = self._record(date(2030, 1, 3), "revision-later")
        extended = self._source(
            (record, later),
            exported="2030-01-03T04:30:00Z",
        )
        existing = materializer._existing_candidate(
            profile=self.profile,
            root=self.root,
            source=extended,
            selection=selection,
            live_module=self.live,
            live_profile=self.live_profile,
            prerequisites=self.prerequisites,
        )
        self.assertIsNotNone(existing)
        preserved_manifest, preserved_payload = existing
        self.assertEqual(preserved_manifest.sha256, manifest.sha256)
        self.assertEqual(
            materializer._canonical_bytes(preserved_payload),
            materializer._canonical_bytes(payload),
        )

    def test_revision_id_never_becomes_a_path_component(self) -> None:
        path = materializer._receipt_path(
            self.root / "outcome_receipts",
            date(2030, 1, 2),
            "../../arbitrary/revision",
        )
        self.assertEqual(path.parent.name, "2030-01-02")
        self.assertEqual(len(path.stem), 64)
        path.relative_to((self.root / "outcome_receipts").resolve())

    def test_post_publish_clock_regression_withdraws_new_active_copy(self) -> None:
        record = self._record(
            date(2030, 1, 2), finalized="2030-01-02T03:00:00Z"
        )
        source = self._source((record,), exported="2030-01-02T04:00:00Z")
        selection = materializer._Selection("outstanding", record.day, record)
        manifest, payload = self._candidate(source, selection)
        with self.assertRaisesRegex(
            materializer.OutcomeMaterializerInputError, "moved backwards"
        ):
            self._publish(
                source,
                selection,
                manifest,
                payload,
                times=[
                    datetime(2030, 1, 2, 6, tzinfo=timezone.utc),
                    datetime(2030, 1, 2, 5, tzinfo=timezone.utc),
                ],
            )
        self.assertFalse(
            (self.root / "outcome_inbox" / "2030-01-02.json").exists()
        )
        receipt = materializer._receipt_path(
            self.root / "outcome_receipts", record.day, record.revision_id
        )
        self.assertTrue(receipt.is_file())

    def test_cross_chain_clock_regression_rolls_back_every_recovery(self) -> None:
        records: list[source_module.DailySourceRecord] = []
        mutable_paths: list[Path] = []
        for offset in range(2):
            target = date(2030, 1, 2 + offset)
            record = self._record(target)
            source = self._source((record,))
            selection = materializer._Selection("outstanding", target, record)
            manifest, payload = self._candidate(source, selection)
            self._publish(source, selection, manifest, payload)
            active = self.root / "outcome_inbox" / f"{target.isoformat()}.json"
            pointer = materializer._active_pointer_path(
                self.root / "outcome_receipts", target
            )
            active.unlink()
            pointer.unlink()
            records.append(record)
            mutable_paths.extend((active, pointer))

        current = self._source(tuple(records), exported="2030-01-03T04:00:00Z")
        projection = self._projection(
            last=date(2030, 1, 1), outstanding=date(2030, 1, 2)
        )
        values = iter(
            [
                datetime(2030, 1, 4, 9, tzinfo=timezone.utc),
                datetime(2030, 1, 4, 10, tzinfo=timezone.utc),
                datetime(2030, 1, 4, 11, tzinfo=timezone.utc),
                datetime(2030, 1, 4, 9, tzinfo=timezone.utc),
                datetime(2030, 1, 4, 10, tzinfo=timezone.utc),
            ]
        )
        with self.assertRaisesRegex(
            materializer.OutcomeMaterializerInputError, "moved backwards"
        ):
            self._run_materializer(
                current, projection, clock=lambda: next(values)
            )
        self.assertTrue(all(not path.exists() for path in mutable_paths))
        status = json.loads(
            (self.root / "outcome_materializer_status.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            status["recorded_at_utc"], "2030-01-04T11:00:00.000000Z"
        )

    def test_initial_to_prepublish_clock_regression_never_activates(self) -> None:
        target = date(2030, 1, 2)
        record = self._record(target)
        source = self._source((record,))
        projection = self._projection(
            last=date(2030, 1, 1), outstanding=target
        )
        values = iter(
            [
                datetime(2030, 1, 2, 10, tzinfo=timezone.utc),
                datetime(2030, 1, 2, 9, tzinfo=timezone.utc),
                datetime(2030, 1, 2, 10, tzinfo=timezone.utc),
            ]
        )
        with self.assertRaisesRegex(
            materializer.OutcomeMaterializerInputError, "moved backwards"
        ):
            self._run_materializer(
                source, projection, clock=lambda: next(values)
            )
        active = self.root / "outcome_inbox" / f"{target.isoformat()}.json"
        pointer = materializer._active_pointer_path(
            self.root / "outcome_receipts", target
        )
        receipt = materializer._receipt_path(
            self.root / "outcome_receipts", target, record.revision_id
        )
        self.assertFalse(active.exists())
        self.assertFalse(pointer.exists())
        self.assertFalse(receipt.exists())

    def test_future_finalization_never_reaches_active_inbox(self) -> None:
        record = self._record(
            date(2030, 1, 2), finalized="2030-01-02T08:00:00Z"
        )
        source = self._source((record,), exported="2030-01-02T08:00:00Z")
        selection = materializer._Selection("outstanding", record.day, record)
        manifest, payload = self._candidate(source, selection)
        with self.assertRaisesRegex(
            materializer.OutcomeMaterializerInputError, "future-dated"
        ):
            self._publish(
                source,
                selection,
                manifest,
                payload,
                times=[datetime(2030, 1, 2, 7, tzinfo=timezone.utc)],
            )
        self.assertFalse(
            (self.root / "outcome_inbox" / "2030-01-02.json").exists()
        )

    def test_receipt_only_r2_is_recovered_before_current_r3_can_branch(self) -> None:
        target = date(2030, 1, 2)
        first = self._record(target, "revision-1")
        first_source = self._source((first,))
        first_selection = materializer._Selection("outstanding", target, first)
        first_manifest, first_payload = self._candidate(
            first_source, first_selection
        )
        _, first_registered, _, _ = self._publish(
            first_source, first_selection, first_manifest, first_payload
        )

        second = replace(
            first,
            revision_id="revision-2",
            displacement_mm={station: 12.0 for station in self.stations},
        )
        second_source = self._source((second,))
        second_selection = materializer._Selection(
            "revision",
            target,
            second,
            previous_revision_id="revision-1",
            previous_outcome_sha256=first_registered.exact_object.sha256,
        )
        second_manifest, second_payload = self._candidate(
            second_source, second_selection
        )
        second_raw = materializer._canonical_bytes(second_payload)
        second_object = materializer._materialize_object(
            self.objects, second_raw, suffix="outcome.json"
        )
        active = self.root / "outcome_inbox" / f"{target.isoformat()}.json"
        materializer._create_or_validate_receipt(
            profile=self.profile,
            root=self.root,
            active_path=active,
            exact_object=second_object,
            payload=second_payload,
            input_manifest=second_manifest,
            previous_receipt=first_registered.receipt,
            revision_sequence_id=2,
            live_module=self.live,
            live_profile=self.live_profile,
            prerequisites=self.prerequisites,
        )
        self.assertEqual(active.read_bytes(), first_registered.raw)

        third = replace(
            second,
            revision_id="revision-3",
            displacement_mm={station: 13.0 for station in self.stations},
        )
        current = self._source((third,), exported="2030-01-03T04:00:00Z")
        times = iter(
            [
                datetime(2030, 1, 3, 5, tzinfo=timezone.utc),
                datetime(2030, 1, 3, 6, tzinfo=timezone.utc),
            ]
        )
        chains, states, _ = materializer._reconcile_receipt_registry(
            profile=self.profile,
            root=self.root,
            clock=lambda: next(times),
            live_module=self.live,
            live_profile=self.live_profile,
            prerequisites=self.prerequisites,
        )
        projection = self._projection(
            last=target,
            revisions={target.isoformat(): {"revision-1": first_registered.exact_object.sha256}},
        )
        pending = materializer._pending_registered_tip(
            chains, projection=projection, source=current, profile=self.profile
        )
        self.assertEqual(pending.payload["source_revision_id"], "revision-2")
        self.assertTrue(any(state.changed for state in states))
        self.assertEqual(active.read_bytes(), pending.raw)
        third_receipt = materializer._receipt_path(
            self.root / "outcome_receipts", target, "revision-3"
        )
        self.assertFalse(third_receipt.exists())

    def test_pointer_committed_before_inbox_recovers_verified_previous_bytes(self) -> None:
        target = date(2030, 1, 2)
        first = self._record(target, "revision-1")
        first_source = self._source((first,))
        first_selection = materializer._Selection("outstanding", target, first)
        first_manifest, first_payload = self._candidate(first_source, first_selection)
        _, first_registered, _, _ = self._publish(
            first_source, first_selection, first_manifest, first_payload
        )
        second = replace(first, revision_id="revision-2")
        second_source = self._source((second,))
        second_selection = materializer._Selection(
            "revision",
            target,
            second,
            previous_revision_id="revision-1",
            previous_outcome_sha256=first_registered.exact_object.sha256,
        )
        second_manifest, second_payload = self._candidate(second_source, second_selection)
        _, second_registered, _, _ = self._publish(
            second_source, second_selection, second_manifest, second_payload
        )
        active = self.root / "outcome_inbox" / f"{target.isoformat()}.json"
        active.write_bytes(first_registered.raw)
        chain = materializer._scan_receipt_chain(
            target=target,
            profile=self.profile,
            root=self.root,
            live_module=self.live,
            live_profile=self.live_profile,
            prerequisites=self.prerequisites,
        )
        state, _ = materializer._reconcile_chain(
            chain,
            profile=self.profile,
            root=self.root,
            now=datetime(2030, 1, 3, tzinfo=timezone.utc),
            live_module=self.live,
            live_profile=self.live_profile,
            prerequisites=self.prerequisites,
        )
        self.assertTrue(state.changed)
        self.assertEqual(active.read_bytes(), second_registered.raw)

    def test_old_pointer_with_new_tip_inbox_is_blocked_as_rollback(self) -> None:
        target = date(2030, 1, 2)
        first = self._record(target, "revision-1")
        source = self._source((first,))
        selection = materializer._Selection("outstanding", target, first)
        manifest, payload = self._candidate(source, selection)
        _, first_registered, _, _ = self._publish(source, selection, manifest, payload)
        second = replace(first, revision_id="revision-2")
        second_source = self._source((second,))
        second_selection = materializer._Selection(
            "revision",
            target,
            second,
            previous_revision_id="revision-1",
            previous_outcome_sha256=first_registered.exact_object.sha256,
        )
        second_manifest, second_payload = self._candidate(second_source, second_selection)
        self._publish(second_source, second_selection, second_manifest, second_payload)
        pointer = materializer._active_pointer_path(
            self.root / "outcome_receipts", target
        )
        pointer.write_bytes(
            materializer._canonical_bytes(
                materializer._active_pointer_payload(target, first_registered.receipt)
            )
        )
        chain = materializer._scan_receipt_chain(
            target=target,
            profile=self.profile,
            root=self.root,
            live_module=self.live,
            live_profile=self.live_profile,
            prerequisites=self.prerequisites,
        )
        with self.assertRaisesRegex(
            materializer.OutcomeMaterializerConflict, "neither active receipt"
        ):
            materializer._reconcile_chain(
                chain,
                profile=self.profile,
                root=self.root,
                now=datetime(2030, 1, 3, tzinfo=timezone.utc),
                live_module=self.live,
                live_profile=self.live_profile,
                prerequisites=self.prerequisites,
            )

    def test_third_inbox_bytes_are_never_adopted(self) -> None:
        target = date(2030, 1, 2)
        record = self._record(target)
        source = self._source((record,))
        selection = materializer._Selection("outstanding", target, record)
        manifest, payload = self._candidate(source, selection)
        self._publish(source, selection, manifest, payload)
        active = self.root / "outcome_inbox" / f"{target.isoformat()}.json"
        active.write_bytes(b"{\"third\":true}\n")
        chain = materializer._scan_receipt_chain(
            target=target,
            profile=self.profile,
            root=self.root,
            live_module=self.live,
            live_profile=self.live_profile,
            prerequisites=self.prerequisites,
        )
        with self.assertRaises(materializer.OutcomeMaterializerConflict):
            materializer._reconcile_chain(
                chain,
                profile=self.profile,
                root=self.root,
                now=datetime(2030, 1, 3, tzinfo=timezone.utc),
                live_module=self.live,
                live_profile=self.live_profile,
                prerequisites=self.prerequisites,
            )

    def test_receipt_branch_is_rejected_even_when_each_receipt_is_valid(self) -> None:
        target = date(2030, 1, 2)
        first = self._record(target, "revision-1")
        source = self._source((first,))
        selection = materializer._Selection("outstanding", target, first)
        manifest, payload = self._candidate(source, selection)
        _, first_registered, _, _ = self._publish(source, selection, manifest, payload)
        second = replace(first, revision_id="revision-2")
        second_source = self._source((second,))
        second_selection = materializer._Selection(
            "revision", target, second,
            previous_revision_id="revision-1",
            previous_outcome_sha256=first_registered.exact_object.sha256,
        )
        second_manifest, second_payload = self._candidate(second_source, second_selection)
        self._publish(second_source, second_selection, second_manifest, second_payload)
        third = replace(first, revision_id="revision-branch")
        third_source = self._source((third,))
        third_selection = materializer._Selection("revision", target, third)
        third_manifest, third_payload = self._candidate(third_source, third_selection)
        third_raw = materializer._canonical_bytes(third_payload)
        third_object = materializer._materialize_object(
            self.objects, third_raw, suffix="outcome.json"
        )
        materializer._create_or_validate_receipt(
            profile=self.profile,
            root=self.root,
            active_path=self.root / "outcome_inbox" / f"{target.isoformat()}.json",
            exact_object=third_object,
            payload=third_payload,
            input_manifest=third_manifest,
            previous_receipt=first_registered.receipt,
            revision_sequence_id=2,
            live_module=self.live,
            live_profile=self.live_profile,
            prerequisites=self.prerequisites,
        )
        with self.assertRaisesRegex(
            materializer.OutcomeMaterializerConflict, "forked|gap or duplicate"
        ):
            materializer._scan_receipt_chain(
                target=target,
                profile=self.profile,
                root=self.root,
                live_module=self.live,
                live_profile=self.live_profile,
                prerequisites=self.prerequisites,
            )

    def test_activation_era_change_requires_machine_epoch_rotation(self) -> None:
        activation_record = self._record(date(2030, 1, 1), "activation-r1")
        activation = self._source((activation_record,))
        unchanged = self._source((activation_record,))
        self.assertIsNone(
            materializer._activation_change_date(unchanged, activation)
        )
        changed = replace(activation_record, revision_id="activation-r2")
        current = self._source((changed,))
        self.assertEqual(
            materializer._activation_change_date(current, activation),
            date(2030, 1, 1),
        )

    def test_symlinked_active_inbox_cannot_overwrite_an_object(self) -> None:
        target = date(2030, 1, 2)
        record = self._record(target)
        source = self._source((record,))
        selection = materializer._Selection("outstanding", target, record)
        manifest, payload = self._candidate(source, selection)
        protected = self._artifact(
            self.objects / "protected.outcome.json", b"protected-object\n"
        )
        protected_bytes = protected.path.read_bytes()
        active = self.root / "outcome_inbox" / f"{target.isoformat()}.json"
        active.parent.mkdir(parents=True, exist_ok=True)
        active.symlink_to(protected.path)
        with self.assertRaises(materializer.OutcomeMaterializerConfigError):
            self._publish(source, selection, manifest, payload)
        self.assertEqual(protected.path.read_bytes(), protected_bytes)

    def test_symlinked_status_cannot_overwrite_an_object(self) -> None:
        protected = self._artifact(
            self.objects / "protected-status-object.json", b"protected-status\n"
        )
        protected_bytes = protected.path.read_bytes()
        status = self.root / "outcome_materializer_status.json"
        status.symlink_to(protected.path)
        with self.assertRaises(materializer.OutcomeMaterializerConfigError):
            materializer.materialize_outcome(
                runtime_root=self.root,
                clock=lambda: datetime(2030, 1, 1, tzinfo=timezone.utc),
            )
        self.assertEqual(protected.path.read_bytes(), protected_bytes)

    def test_ledger_predecessor_rejection_rolls_back_recovered_pointer_and_inbox(
        self,
    ) -> None:
        target = date(2030, 1, 2)
        first = self._record(target, "revision-1")
        first_source = self._source((first,))
        first_selection = materializer._Selection("outstanding", target, first)
        first_manifest, first_payload = self._candidate(first_source, first_selection)
        _, first_registered, _, _ = self._publish(
            first_source, first_selection, first_manifest, first_payload
        )
        active = self.root / "outcome_inbox" / f"{target.isoformat()}.json"
        pointer = materializer._active_pointer_path(
            self.root / "outcome_receipts", target
        )
        first_active = active.read_bytes()
        first_pointer = pointer.read_bytes()

        second = replace(first, revision_id="revision-2")
        second_source = self._source((second,))
        second_selection = materializer._Selection("revision", target, second)
        second_manifest, second_payload = self._candidate(second_source, second_selection)
        second_raw = materializer._canonical_bytes(second_payload)
        second_object = materializer._materialize_object(
            self.objects, second_raw, suffix="outcome.json"
        )
        materializer._create_or_validate_receipt(
            profile=self.profile,
            root=self.root,
            active_path=active,
            exact_object=second_object,
            payload=second_payload,
            input_manifest=second_manifest,
            previous_receipt=first_registered.receipt,
            revision_sequence_id=2,
            live_module=self.live,
            live_profile=self.live_profile,
            prerequisites=self.prerequisites,
        )
        bad_projection = self._projection(
            last=target,
            revisions={target.isoformat(): {"unrelated-ledger-revision": "f" * 64}},
        )
        values = iter(
            [
                datetime(2030, 1, 3, 5, tzinfo=timezone.utc),
                datetime(2030, 1, 3, 6, tzinfo=timezone.utc),
            ]
        )
        with self.assertRaisesRegex(
            materializer.OutcomeMaterializerConflict,
            "not identical|branched from an older",
        ):
            materializer._reconcile_and_classify_registry(
                profile=self.profile,
                root=self.root,
                clock=lambda: next(values),
                live_module=self.live,
                live_profile=self.live_profile,
                prerequisites=self.prerequisites,
                projection=bad_projection,
                source=second_source,
            )
        self.assertEqual(active.read_bytes(), first_active)
        self.assertEqual(pointer.read_bytes(), first_pointer)

    def test_multiple_ledger_unconsumed_tips_are_rejected(self) -> None:
        chains: dict[str, materializer._ReceiptChain] = {}
        revisions: dict[str, dict[str, str]] = {}
        current_records: list[source_module.DailySourceRecord] = []
        for offset in range(2):
            target = date(2030, 1, 2 + offset)
            first = self._record(target, "revision-1")
            source = self._source((first,))
            selection = materializer._Selection("outstanding", target, first)
            manifest, payload = self._candidate(source, selection)
            _, first_registered, _, _ = self._publish(
                source, selection, manifest, payload
            )
            second = replace(first, revision_id="revision-2")
            revised_source = self._source((second,))
            revised_selection = materializer._Selection(
                "revision",
                target,
                second,
                previous_revision_id="revision-1",
                previous_outcome_sha256=first_registered.exact_object.sha256,
            )
            revised_manifest, revised_payload = self._candidate(
                revised_source, revised_selection
            )
            self._publish(
                revised_source,
                revised_selection,
                revised_manifest,
                revised_payload,
            )
            chains[target.isoformat()] = materializer._scan_receipt_chain(
                target=target,
                profile=self.profile,
                root=self.root,
                live_module=self.live,
                live_profile=self.live_profile,
                prerequisites=self.prerequisites,
            )
            revisions[target.isoformat()] = {
                "revision-1": first_registered.exact_object.sha256
            }
            current_records.append(second)
        current = self._source(tuple(current_records))
        projection = self._projection(
            last=date(2030, 1, 3), revisions=revisions
        )
        with self.assertRaisesRegex(
            materializer.OutcomeMaterializerConflict, "Multiple ledger-unconsumed"
        ):
            materializer._pending_registered_tip(
                chains,
                projection=projection,
                source=current,
                profile=self.profile,
            )


if __name__ == "__main__":
    unittest.main()
