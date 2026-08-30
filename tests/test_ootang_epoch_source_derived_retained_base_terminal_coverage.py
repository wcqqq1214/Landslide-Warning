"""Focused contracts for exact retained frozen-base terminal coverage."""

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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_epoch_registry as registry  # noqa: E402
from monitoring import ootang_epoch_manifest_terminal_coverage as manifest_coverage  # noqa: E402
from monitoring import (  # noqa: E402
    ootang_epoch_source_derived_retained_base_terminal_coverage as retained,
)
from monitoring import ootang_epoch_step_dependency_reservation as sidecar  # noqa: E402
from monitoring import ootang_epoch_workset_manifest as manifest  # noqa: E402
from monitoring import ootang_epoch_workset_recovery as recovery  # noqa: E402
from tests import test_ootang_epoch_source_derived_workset_overlay as overlay_test  # noqa: E402
from tests import test_ootang_epoch_manifest_terminal_coverage as manifest_coverage_test  # noqa: E402


NOW = overlay_test.NOW + timedelta(seconds=1)


class SourceDerivedRetainedBaseTerminalCoverageTests(unittest.TestCase):
    """Keep source parent, D/R/I, and unchanged frozen rows disjoint."""

    def setUp(self) -> None:
        self.fixture = overlay_test.EpochSourceDerivedWorksetOverlayTests(
            "test_d_items_publish_one_compact_effective_workset_without_upstream_writes"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def _paths(self) -> retained.SourceDerivedRetainedBaseTerminalCoveragePaths:
        return retained.source_derived_retained_base_terminal_coverage_paths(
            retained.load_source_derived_retained_base_terminal_coverage_profile(),
            registry_root=self.fixture.fixture.manifest_fixture.registry_root,
            active_root=self.fixture.fixture.source_fixture.root,
            shadow_root=self.fixture.fixture.manifest_fixture.shadow_root,
        )

    def _complete_overlay(
        self,
        values: tuple[object, ...],
        *,
        frozen_prefix: object | None = None,
    ) -> None:
        result = self.fixture._complete_cross_freeze(  # noqa: SLF001
            values, frozen_prefix=frozen_prefix
        )
        self.assertEqual(result.status, "source_ingest_cross_freeze_completed")
        published = self.fixture._run()  # noqa: SLF001
        self.assertTrue(published.event_path and published.event_path.is_file())

    def _run(
        self, *, fault_hook: object | None = None
    ) -> retained.SourceDerivedRetainedBaseTerminalCoverageResult:
        assert self.fixture.binding is not None
        with mock.patch.object(
            manifest,
            "_default_admission_cut_binding",
            return_value=self.fixture.binding,
        ):
            return retained._coordinate_source_derived_retained_base_terminal_coverage(  # noqa: SLF001
                registry_root=self.fixture.fixture.manifest_fixture.registry_root,
                active_root=self.fixture.fixture.source_fixture.root,
                shadow_root=self.fixture.fixture.manifest_fixture.shadow_root,
                clock=lambda: NOW,
                fault_hook=fault_hook,
                load_derived_authority=self.fixture._load_derived,  # noqa: SLF001
            )

    def _computation(self) -> object:
        assert self.fixture.binding is not None
        paths = self._paths()
        with mock.patch.object(
            manifest,
            "_default_admission_cut_binding",
            return_value=self.fixture.binding,
        ):
            computation, waiting = retained.overlay._load_computation(  # noqa: SLF001
                retained.overlay.load_source_derived_workset_overlay_profile(),
                paths.overlay,
                NOW,
                load_derived_authority=self.fixture._load_derived,  # noqa: SLF001
            )
        self.assertEqual(waiting, "")
        self.assertIsNotNone(computation)
        return computation

    @staticmethod
    def _only_payload(directory: Path) -> dict[str, object]:
        entries = tuple(directory.glob("*.json"))
        if len(entries) != 1:
            raise AssertionError(f"expected one JSON record in {directory}: {entries}")
        return json.loads(entries[0].read_bytes())

    @staticmethod
    def _reference(path: Path, root: Path) -> dict[str, object]:
        raw = path.read_bytes()
        return {
            "path": path.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
        }

    @staticmethod
    def _tree_bytes(*roots: Path, excluded: Path) -> dict[Path, bytes]:
        excluded = excluded.resolve()
        return {
            path: path.read_bytes()
            for root in roots
            for path in root.rglob("*")
            if path.is_file()
            and path.resolve() != excluded
            and excluded not in path.resolve().parents
        }

    def test_empty_retained_subset_publishes_exact_vacuous_proof_and_replays(
        self,
    ) -> None:
        self._complete_overlay((self.fixture.fixture.source_item,))
        paths = self._paths()
        upstream_before = self._tree_bytes(
            self.fixture.fixture.manifest_fixture.registry_root,
            self.fixture.fixture.source_fixture.root,
            self.fixture.fixture.manifest_fixture.shadow_root,
            excluded=paths.root,
        )

        result = self._run()

        self.assertTrue(result.all_current_retained_base_items_terminal)
        self.assertEqual(result.required_key_count, 0)
        self.assertEqual(result.covered_key_count, 0)
        self.assertEqual(result.missing_key_ids, ())
        proof_path = next(paths.proofs.glob("*.json"))
        event_path = next(paths.events.glob("*.json"))
        proof = json.loads(proof_path.read_bytes())
        event = json.loads(event_path.read_bytes())
        self.assertEqual(
            proof_path.name,
            f"{hashlib.sha256(proof_path.read_bytes()).hexdigest()}.json",
        )
        self.assertTrue(proof["empty_subset_vacuously_covered"])
        self.assertEqual(proof["retained_target_rows"], [])
        self.assertEqual(proof["terminal_coverage_rows"], [])
        self.assertEqual(proof["partition_counts"]["source_parent_excluded_count"], 1)
        self.assertEqual(
            proof["partition_counts"]["derived_addition_excluded_count"], 2
        )
        self.assertEqual(event["proof"], self._reference(proof_path, paths.root))
        for payload in (proof, event):
            self.assertTrue(payload["all_current_retained_base_items_terminal"])
            for claim in retained.FALSE_CLAIMS:
                self.assertFalse(payload[claim], claim)
        status = json.loads(paths.status.read_bytes())
        self.assertFalse(status["cache_authority"])
        self.assertEqual(
            {path: path.read_bytes() for path in upstream_before}, upstream_before
        )

        proof_before = proof_path.read_bytes()
        event_before = event_path.read_bytes()
        paths.status.unlink()
        replay = self._run()

        self.assertEqual(replay.status, "retained_base_terminal_coverage_current")
        self.assertEqual(proof_path.read_bytes(), proof_before)
        self.assertEqual(event_path.read_bytes(), event_before)
        self.assertFalse(json.loads(paths.status.read_bytes())["cache_authority"])

    def test_old_rebound_identity_never_enters_retained_denominator(self) -> None:
        frozen = self.fixture.fixture._baseline_machine_outcome_item()  # noqa: SLF001
        self._complete_overlay(
            (frozen, self.fixture.fixture.source_item),
            frozen_prefix=self.fixture.fixture._frozen_prefix(  # noqa: SLF001
                last_finalized=date(2020, 6, 30)
            ),
        )

        result = self._run()

        self.assertTrue(result.all_current_retained_base_items_terminal)
        self.assertEqual(result.required_key_count, 0)
        proof = self._only_payload(self._paths().proofs)
        rows = proof["excluded_rebound_old_rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["old_identity"]["natural_key"], frozen.natural_key)
        self.assertEqual(
            rows[0]["replacement_identity"]["natural_key"], frozen.natural_key
        )
        self.assertNotEqual(
            rows[0]["old_identity"]["key_id"],
            rows[0]["replacement_identity"]["key_id"],
        )
        self.assertEqual(proof["partition_counts"]["rebound_old_excluded_count"], 1)

    def test_invalidated_identity_never_enters_retained_denominator(self) -> None:
        payload = self.fixture.fixture.source_fixture.payload(count=3)
        payload["records"][0]["revision_id"] = "source-revision-1b"
        self.fixture.fixture.source_fixture.write(payload)
        self.fixture.fixture.source_item = self.fixture.fixture._source_ingest_item(  # noqa: SLF001
            expected_changed=(
                ("2020-07-01", "source-revision-1b"),
                ("2020-07-02", "source-revision-2"),
                ("2020-07-03", "source-revision-3"),
            )
        )
        frozen = self.fixture.fixture._baseline_machine_outcome_item()  # noqa: SLF001
        self._complete_overlay((frozen, self.fixture.fixture.source_item))

        result = self._run()

        self.assertTrue(result.all_current_retained_base_items_terminal)
        self.assertEqual(result.required_key_count, 0)
        proof = self._only_payload(self._paths().proofs)
        rows = proof["excluded_invalidated_rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["invalidated_identity"]["natural_key"], frozen.natural_key
        )
        self.assertEqual(proof["partition_counts"]["invalidated_excluded_count"], 1)

    def test_unchanged_base_without_terminal_event_waits_without_proof(self) -> None:
        repair = self.fixture.fixture._anchor_repair_item()  # noqa: SLF001
        self._complete_overlay((repair, self.fixture.fixture.source_item))
        paths = self._paths()

        result = self._run()

        self.assertEqual(result.status, "waiting_for_retained_base_terminal_evidence")
        self.assertFalse(result.all_current_retained_base_items_terminal)
        self.assertEqual(result.required_key_count, 1)
        self.assertEqual(result.covered_key_count, 0)
        self.assertEqual(len(result.missing_key_ids), 1)
        self.assertEqual(tuple(paths.proofs.glob("*.json")), ())
        self.assertEqual(tuple(paths.events.glob("*.json")), ())

    def test_nonempty_terminal_receipt_event_and_proof_only_crash_forward_adopt(
        self,
    ) -> None:
        repair = self.fixture.fixture._anchor_repair_item()  # noqa: SLF001
        self._complete_overlay((repair, self.fixture.fixture.source_item))
        paths = self._paths()
        computation = self._computation()
        partition = retained._retained_partition(computation)  # noqa: SLF001
        self.assertEqual(len(partition.target_rows), 1)
        target = partition.target_rows[0]
        ordered, _, _ = recovery._dag(computation.reservation)  # noqa: SLF001
        item = next(row for row in ordered if row["key_id"] == target["key_id"])
        plan = recovery._transition_plan(item)  # noqa: SLF001
        action = plan["terminal_actions"][0]
        step_id = recovery._step_id(str(target["key_id"]), 0, action)  # noqa: SLF001

        global_payload = {"schema_version": "synthetic-retained-global-intent-v1"}
        paths.sidecar.recovery.global_intent.parent.mkdir(parents=True, exist_ok=True)
        paths.sidecar.recovery.global_intent.write_bytes(
            retained._canonical_bytes(global_payload)  # noqa: SLF001
        )
        global_snapshot = registry._read_regular(  # noqa: SLF001
            paths.sidecar.recovery.global_intent,
            name="synthetic retained global intent",
            maximum_bytes=retained.MAX_CONTROL_BYTES,
        )
        receipt_payload = {
            "step_id": step_id,
            "step_index": 0,
            "key_id": target["key_id"],
            "natural_key": target["natural_key"],
            "namespace_digest": target["namespace_digest"],
            "action": action,
            "transition_plan_sha256": target["transition_plan_sha256"],
            "next_actions": recovery._plan_edges(plan)[action],  # noqa: SLF001
            "terminal_for_key": True,
        }
        receipt_path = paths.sidecar.recovery.receipts / f"{step_id}.json"
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_bytes(retained._canonical_bytes(receipt_payload))  # noqa: SLF001
        receipt_snapshot = registry._read_regular(  # noqa: SLF001
            receipt_path,
            name="synthetic retained terminal receipt",
            maximum_bytes=retained.MAX_CONTROL_BYTES,
        )
        event_payload = {
            "step_id": step_id,
            "key_id": target["key_id"],
            "receipt": retained._reference(  # noqa: SLF001
                receipt_snapshot, paths.sidecar.recovery.root
            ),
        }
        event_path = paths.sidecar.recovery.events / "synthetic-terminal-event.json"
        event_path.parent.mkdir(parents=True, exist_ok=True)
        event_path.write_bytes(retained._canonical_bytes(event_payload))  # noqa: SLF001
        event_snapshot = registry._read_regular(  # noqa: SLF001
            event_path,
            name="synthetic retained terminal event",
            maximum_bytes=retained.MAX_CONTROL_BYTES,
        )
        authority = sidecar.StepDependencyAuthority(
            recovery_profile=recovery.load_workset_recovery_profile(),
            reservation=computation.reservation,
            global_intent=global_payload,
            global_snapshot=global_snapshot,
            ordered_items=tuple(ordered),
            chains={str(target["key_id"]): [(receipt_payload, receipt_snapshot)]},
            events_by_step={step_id: (event_payload, event_snapshot)},
        )

        def crash(stage: str) -> None:
            if stage == "after_proof":
                raise RuntimeError("synthetic retained proof-only crash")

        with (
            mock.patch.object(
                sidecar, "_load_recovery_authority", return_value=authority
            ),
            self.assertRaisesRegex(
                retained.SourceDerivedRetainedBaseTerminalCoverageIntegrityError,
                "proof-only crash",
            ),
        ):
            self._run(fault_hook=crash)

        self.assertEqual(len(tuple(paths.proofs.glob("*.json"))), 1)
        self.assertEqual(tuple(paths.events.glob("*.json")), ())
        proof_path = next(paths.proofs.glob("*.json"))
        proof_before = proof_path.read_bytes()
        with mock.patch.object(
            sidecar, "_load_recovery_authority", return_value=authority
        ):
            healed = self._run()

        self.assertTrue(healed.all_current_retained_base_items_terminal)
        self.assertEqual(healed.required_key_count, 1)
        self.assertEqual(healed.covered_key_count, 1)
        self.assertEqual(proof_path.read_bytes(), proof_before)
        self.assertEqual(len(tuple(paths.events.glob("*.json"))), 1)
        proof = json.loads(proof_before)
        self.assertEqual(
            proof["terminal_coverage_rows"][0]["evidence_kind"],
            "recovery_v6_terminal_receipt_event",
        )

    def test_published_source_terminal_event_is_supported_per_identity(self) -> None:
        legacy = manifest_coverage_test.EpochManifestTerminalCoverageTests(
            "test_ordinary_and_aggregate_terminals_cover_exact_manifest"
        )
        legacy.setUp()
        self.addCleanup(legacy.doCleanups)
        aggregate_paths, terminal_authority = legacy._complete_upstream()  # noqa: SLF001
        manifest_authority = legacy._coverage_authority(  # noqa: SLF001
            aggregate_paths, terminal_authority
        )
        recovered = terminal_authority.overlay_authority.recovery
        source_item = next(
            item
            for item in recovered.ordered_items
            if item["key_id"] == legacy.overlay_fixture.source_item["key_id"]
        )
        plan = recovery._transition_plan(source_item)  # noqa: SLF001
        target = {
            "base_position": 0,
            **retained._identity(source_item),  # noqa: SLF001
            "family": source_item["family"],
            "transition_plan_sha256": plan["plan_sha256"],
        }
        profile = retained.load_source_derived_retained_base_terminal_coverage_profile()
        paths = retained.source_derived_retained_base_terminal_coverage_paths(
            profile,
            registry_root=legacy.overlay_fixture.registry_root,
            active_root=legacy.overlay_fixture.active_root,
            shadow_root=legacy.overlay_fixture.shadow_root,
        )

        with mock.patch.object(
            manifest_coverage,
            "_load_coverage_authority",
            return_value=manifest_authority,
        ):
            rows, identity = retained._source_terminal_rows(  # noqa: SLF001
                paths,
                SimpleNamespace(reservation=recovered.reservation),
                {retained._identity_tuple(target): target},  # noqa: SLF001
                NOW,
            )

        self.assertEqual(len(rows), 1)
        self.assertEqual(
            next(iter(rows.values()))["evidence_kind"],
            "source_terminal_aggregate_proof_event",
        )
        self.assertEqual(identity["selected_source_terminal_identity_count"], 1)


if __name__ == "__main__":
    unittest.main()
