"""Focused contracts for the source-derived source-parent terminal aggregate."""

from __future__ import annotations

from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import (  # noqa: E402
    ootang_epoch_source_derived_source_parent_terminal_aggregate as aggregate,
)
from monitoring import ootang_epoch_workset_manifest as manifest  # noqa: E402
from monitoring import ootang_epoch_workset_recovery as recovery  # noqa: E402
from monitoring import ootang_outcome_materializer as outcomes  # noqa: E402
from monitoring import ootang_prequential_live as live  # noqa: E402
from tests import (  # noqa: E402
    test_ootang_epoch_source_derived_effective_outcome_terminal_coverage as coverage_test,
)
from tests import (  # noqa: E402
    test_ootang_epoch_source_derived_outcome_consumption as source_consumption_test,
)


NOW = coverage_test.NOW + timedelta(seconds=1)


class SourceDerivedSourceParentTerminalAggregateTests(unittest.TestCase):
    """Aggregate one exact source parent only after its current D/R children."""

    def setUp(self) -> None:
        self.fixture = coverage_test.SourceDerivedEffectiveOutcomeTerminalCoverageTests(
            "test_complete_d1_d2_publishes_exact_coverage_and_replays_idempotently"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def _paths(self) -> aggregate.SourceDerivedSourceParentTerminalAggregatePaths:
        coverage_paths = self.fixture._paths()  # noqa: SLF001
        return aggregate.source_derived_source_parent_terminal_aggregate_paths(
            aggregate.load_source_derived_source_parent_terminal_aggregate_profile(),
            registry_root=coverage_paths.registry_root,
            active_root=coverage_paths.active_root,
            shadow_root=coverage_paths.shadow_root,
        )

    def _run(
        self, *, fault_hook: object | None = None
    ) -> aggregate.SourceDerivedSourceParentTerminalAggregateResult:
        source_dispatch_fixture = self.fixture.fixture.fixture.fixture.dispatch_fixture
        base_fixture = source_dispatch_fixture.fixture.fixture
        assert source_dispatch_fixture.fixture.binding is not None
        with (
            mock.patch.object(
                manifest,
                "_default_admission_cut_binding",
                return_value=source_dispatch_fixture.fixture.binding,
            ),
            mock.patch.object(
                recovery,
                "_frozen_live_prefix",
                side_effect=(
                    self.fixture.fixture.fixture.fixture._frozen_live_prefix  # noqa: SLF001
                ),
            ),
            mock.patch.object(
                live,
                "_epoch_id",
                return_value=source_consumption_test.OLD_EPOCH_ID,
            ),
            mock.patch.object(
                outcomes,
                "_select_target",
                side_effect=AssertionError(
                    "source-parent aggregate must not run the current-source selector"
                ),
            ),
            mock.patch.object(
                outcomes,
                "materialize_outcome",
                side_effect=AssertionError(
                    "source-parent aggregate must not republish an outcome"
                ),
            ),
        ):
            return (
                aggregate._coordinate_source_derived_source_parent_terminal_aggregate(  # noqa: SLF001
                    registry_root=base_fixture.manifest_fixture.registry_root,
                    active_root=base_fixture.source_fixture.root,
                    shadow_root=base_fixture.manifest_fixture.shadow_root,
                    clock=lambda: NOW,
                    fault_hook=fault_hook,
                    load_derived_authority=(
                        source_dispatch_fixture.fixture._load_derived  # noqa: SLF001
                    ),
                )
            )

    def _complete_coverage(self) -> None:
        self.fixture._complete_d1_d2()  # noqa: SLF001
        result = self.fixture._run()  # noqa: SLF001
        self.assertTrue(result.all_current_effective_d_or_r_terminal)

    @staticmethod
    def _counts(
        paths: aggregate.SourceDerivedSourceParentTerminalAggregatePaths,
    ) -> tuple[int, int]:
        return (
            len(tuple(paths.proofs.glob("*.json"))),
            len(tuple(paths.events.glob("*.json"))),
        )

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

    def _upstream_bytes(self) -> dict[Path, bytes]:
        before = self.fixture._parent_authority_bytes()  # noqa: SLF001
        coverage_root = self.fixture._paths().root  # noqa: SLF001
        before.update(
            {
                path: path.read_bytes()
                for path in coverage_root.rglob("*")
                if path.is_file()
            }
        )
        return before

    @staticmethod
    def _assert_bytes_unchanged(before: dict[Path, bytes]) -> None:
        actual = {path: path.read_bytes() for path in before}
        if actual != before:
            raise AssertionError("upstream authority bytes changed")

    def test_complete_coverage_publishes_exact_parent_and_replays_idempotently(
        self,
    ) -> None:
        self._complete_coverage()
        paths = self._paths()
        upstream_before = self._upstream_bytes()
        live_before = self.fixture._live_events()  # noqa: SLF001

        result = self._run()

        self.assertEqual(
            result.status, "current_source_ingest_parent_terminal_published"
        )
        self.assertTrue(result.current_source_ingest_parent_terminal)
        self.assertEqual(self._counts(paths), (1, 1))
        proof_path = next(paths.proofs.glob("*.json"))
        event_path = next(paths.events.glob("*.json"))
        proof = json.loads(proof_path.read_bytes())
        event = json.loads(event_path.read_bytes())
        cross_paths = paths.coverage.consumption.dispatch.dispatch.overlay.cross
        coverage_paths = paths.coverage
        cross_receipt_path = next(cross_paths.receipts.glob("*.json"))
        cross_event_path = next(cross_paths.events.glob("*.json"))
        coverage_proof_path = next(coverage_paths.proofs.glob("*.json"))
        coverage_event_path = next(coverage_paths.events.glob("*.json"))
        cross_event = json.loads(cross_event_path.read_bytes())
        coverage_proof = json.loads(coverage_proof_path.read_bytes())
        self.assertEqual(
            proof_path.name,
            f"{hashlib.sha256(proof_path.read_bytes()).hexdigest()}.json",
        )
        self.assertEqual(event["proof"], self._reference(proof_path, paths.root))
        self.assertEqual(
            proof["cross_completion_receipt"],
            self._reference(cross_receipt_path, cross_paths.root),
        )
        self.assertEqual(
            proof["cross_completion_event"],
            self._reference(cross_event_path, cross_paths.root),
        )
        self.assertEqual(
            proof["dri_terminal_coverage_proof"],
            self._reference(coverage_proof_path, coverage_paths.root),
        )
        self.assertEqual(
            proof["dri_terminal_coverage_event"],
            self._reference(coverage_event_path, coverage_paths.root),
        )
        self.assertEqual(coverage_proof["required_key_count"], 2)
        self.assertEqual(
            proof["dri_terminal_coverage_required_key_count"],
            coverage_proof["required_key_count"],
        )
        self.assertEqual(
            proof["dri_terminal_coverage_proof_sha256"],
            hashlib.sha256(coverage_proof_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            coverage_proof["ordered_coverage_rows_sha256"],
            hashlib.sha256(
                coverage_test.coverage._canonical_bytes(  # noqa: SLF001
                    coverage_proof["ordered_coverage_rows"]
                )
            ).hexdigest(),
        )
        self.assertEqual(
            proof["dri_terminal_coverage_ordered_rows_sha256"],
            coverage_proof["ordered_coverage_rows_sha256"],
        )
        self.assertEqual(
            proof["source_parent_identity"]["key_id"],
            cross_event["source_key_id"],
        )
        self.assertEqual(
            proof["source_parent_transition_plan"]["key_id"],
            proof["source_parent_identity"]["key_id"],
        )
        self.assertEqual(
            proof["source_parent_transition_plan"]["initial_action"],
            "source_snapshot_ingested",
        )
        self.assertFalse(proof["source_parent_transition_plan"]["closure_resolved"])
        self.assertEqual(proof["source_parent_transition_plan"]["terminal_actions"], [])
        derivation = proof["source_terminal_derivation"]
        self.assertEqual(derivation["source_key_id"], result.source_key_id)
        self.assertEqual(derivation["canonical_step_index"], 1)
        self.assertEqual(derivation["previous_step_index"], 0)
        self.assertEqual(derivation["previous_action"], "source_snapshot_ingested")
        self.assertEqual(
            derivation["previous_step_id"],
            recovery._step_id(  # noqa: SLF001
                result.source_key_id, 0, "source_snapshot_ingested"
            ),
        )
        self.assertEqual(derivation["action"], "derived_outcome_items_required")
        self.assertEqual(derivation["next_actions"], [])
        self.assertFalse(derivation["original_closure_resolved"])
        self.assertEqual(derivation["original_terminal_actions"], [])
        self.assertFalse(derivation["terminal_for_recovery_v6_key"])
        self.assertEqual(
            derivation["canonical_step_id"],
            recovery._step_id(  # noqa: SLF001
                result.source_key_id, 1, "derived_outcome_items_required"
            ),
        )
        parent_natural_key = proof["source_parent_identity"]["natural_key"]
        audit = proof["derived_reservation_identity_audit"]
        self.assertEqual(
            set(audit),
            {
                "derived_outcome_items",
                "rebound_existing_items",
                "invalidated_existing_items",
            },
        )
        self.assertTrue(
            all(
                row["natural_key"] != parent_natural_key
                for rows in audit.values()
                for row in rows
            )
        )
        for payload in (proof, event):
            self.assertTrue(payload["current_source_ingest_parent_terminal"])
            for claim in aggregate.FALSE_CLAIMS:
                self.assertFalse(payload[claim], claim)
        self.assertFalse(result.source_parent_terminal)
        self.assertFalse(result.terminal_for_recovery_v6_key)
        self.assertFalse(result.all_effective_items_terminal)
        self.assertFalse(result.terminal_transition_closure_implemented)
        status = json.loads(paths.status.read_bytes())
        self.assertFalse(status["cache_authority"])
        self.assertEqual(self.fixture._live_events(), live_before)  # noqa: SLF001
        self._assert_bytes_unchanged(upstream_before)

        proof_before = proof_path.read_bytes()
        event_before = event_path.read_bytes()
        paths.status.unlink()
        self.assertFalse(paths.status.exists())
        replay = self._run()

        self.assertEqual(replay.status, "current_source_ingest_parent_terminal_current")
        self.assertTrue(replay.current_source_ingest_parent_terminal)
        self.assertEqual(self._counts(paths), (1, 1))
        self.assertEqual(proof_path.read_bytes(), proof_before)
        self.assertEqual(event_path.read_bytes(), event_before)
        self.assertFalse(json.loads(paths.status.read_bytes())["cache_authority"])
        self.assertEqual(self.fixture._live_events(), live_before)  # noqa: SLF001
        self._assert_bytes_unchanged(upstream_before)

    def test_upstream_coverage_proof_only_waits_without_aggregate_authority(
        self,
    ) -> None:
        self.fixture._complete_d1_d2()  # noqa: SLF001

        def crash(stage: str) -> None:
            if stage == "after_proof":
                raise RuntimeError("synthetic upstream coverage proof-only crash")

        with self.assertRaisesRegex(
            coverage_test.coverage.SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError,
            "proof-only crash",
        ):
            self.fixture._run(fault_hook=crash)  # noqa: SLF001
        coverage_paths = self.fixture._paths()  # noqa: SLF001
        self.assertEqual(self.fixture._counts(coverage_paths), (1, 0))  # noqa: SLF001
        paths = self._paths()
        upstream_before = self._upstream_bytes()
        live_before = self.fixture._live_events()  # noqa: SLF001

        result = self._run()

        self.assertIn("waiting", result.status)
        self.assertFalse(result.current_source_ingest_parent_terminal)
        self.assertEqual(self._counts(paths), (0, 0))
        self.assertEqual(self.fixture._live_events(), live_before)  # noqa: SLF001
        self._assert_bytes_unchanged(upstream_before)

    def test_aggregate_proof_only_retry_appends_only_the_matching_event(self) -> None:
        self._complete_coverage()
        paths = self._paths()
        upstream_before = self._upstream_bytes()
        live_before = self.fixture._live_events()  # noqa: SLF001

        def crash(stage: str) -> None:
            if stage == "after_proof":
                raise RuntimeError("synthetic parent aggregate proof-only crash")

        with self.assertRaisesRegex(
            aggregate.SourceDerivedSourceParentTerminalAggregateIntegrityError,
            "proof-only crash",
        ):
            self._run(fault_hook=crash)

        self.assertEqual(self._counts(paths), (1, 0))
        proof_path = next(paths.proofs.glob("*.json"))
        proof_before = proof_path.read_bytes()
        original_publish = coverage_test.coverage._publish  # noqa: SLF001

        def publish_event_only(
            path: Path,
            payload: dict[str, object],
            *,
            root: Path,
            name: str,
        ) -> object:
            if path.parent == paths.proofs:
                raise AssertionError("forward adoption must not republish the proof")
            return original_publish(path, payload, root=root, name=name)

        with mock.patch.object(
            coverage_test.coverage, "_publish", side_effect=publish_event_only
        ):
            healed = self._run()

        self.assertTrue(healed.current_source_ingest_parent_terminal)
        self.assertEqual(self._counts(paths), (1, 1))
        self.assertEqual(proof_path.read_bytes(), proof_before)
        self.assertEqual(self.fixture._live_events(), live_before)  # noqa: SLF001
        self._assert_bytes_unchanged(upstream_before)


if __name__ == "__main__":
    unittest.main()
