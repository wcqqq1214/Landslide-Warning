"""Focused contracts for current effective D/R terminal coverage."""

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
    ootang_epoch_source_derived_effective_outcome_terminal_coverage as coverage,
)
from monitoring import ootang_epoch_workset_manifest as manifest  # noqa: E402
from monitoring import ootang_epoch_workset_recovery as recovery  # noqa: E402
from monitoring import ootang_outcome_materializer as outcomes  # noqa: E402
from monitoring import ootang_prequential_live as live  # noqa: E402
from tests import (  # noqa: E402
    test_ootang_epoch_source_derived_dependent_outcome_consumption as consumption_test,
)
from tests import (  # noqa: E402
    test_ootang_epoch_source_derived_outcome_consumption as source_consumption_test,
)


NOW = consumption_test.NOW + timedelta(seconds=1)


class SourceDerivedEffectiveOutcomeTerminalCoverageTests(unittest.TestCase):
    """Prove exact coverage of the current D1/D2 effective identity set."""

    def setUp(self) -> None:
        self.fixture = consumption_test.SourceDerivedDependentOutcomeConsumptionTests(
            "test_d2_fresh_cas_binds_exact_pre_head_and_event_specs"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def _paths(
        self,
    ) -> coverage.SourceDerivedEffectiveOutcomeTerminalCoveragePaths:
        fixture = self.fixture.fixture.fixture.dispatch_fixture.fixture.fixture
        return coverage.source_derived_effective_outcome_terminal_coverage_paths(
            coverage.load_source_derived_effective_outcome_terminal_coverage_profile(),
            registry_root=fixture.manifest_fixture.registry_root,
            active_root=fixture.source_fixture.root,
            shadow_root=fixture.manifest_fixture.shadow_root,
        )

    def _run(
        self, *, fault_hook: object | None = None
    ) -> coverage.SourceDerivedEffectiveOutcomeTerminalCoverageResult:
        source_dispatch_fixture = self.fixture.fixture.fixture.dispatch_fixture
        fixture = source_dispatch_fixture.fixture.fixture
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
                    self.fixture.fixture.fixture._frozen_live_prefix  # noqa: SLF001
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
                    "terminal coverage must not run the current-source selector"
                ),
            ),
            mock.patch.object(
                outcomes,
                "materialize_outcome",
                side_effect=AssertionError(
                    "terminal coverage must not republish an outcome"
                ),
            ),
        ):
            return (
                coverage._coordinate_source_derived_effective_outcome_terminal_coverage(  # noqa: SLF001
                    registry_root=fixture.manifest_fixture.registry_root,
                    active_root=fixture.source_fixture.root,
                    shadow_root=fixture.manifest_fixture.shadow_root,
                    clock=lambda: NOW,
                    fault_hook=fault_hook,
                    load_derived_authority=(
                        source_dispatch_fixture.fixture._load_derived  # noqa: SLF001
                    ),
                )
            )

    def _complete_d1_d2(self) -> None:
        self.fixture._publish_d2()  # noqa: SLF001
        consumed = self.fixture._run()  # noqa: SLF001
        self.assertTrue(consumed.terminal_for_effective_key)

    @staticmethod
    def _counts(
        paths: coverage.SourceDerivedEffectiveOutcomeTerminalCoveragePaths,
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

    @staticmethod
    def _tree_bytes(*roots: Path) -> dict[Path, bytes]:
        return {
            path: path.read_bytes()
            for root in roots
            for path in root.rglob("*")
            if path.is_file()
        }

    @staticmethod
    def _assert_bytes_unchanged(before: dict[Path, bytes]) -> None:
        actual = {path: path.read_bytes() for path in before}
        if actual != before:
            raise AssertionError("upstream authority bytes changed")

    def _parent_authority_bytes(self) -> dict[Path, bytes]:
        source_dispatch = self.fixture.fixture.fixture.dispatch_fixture._paths()  # noqa: SLF001
        return self._tree_bytes(
            source_dispatch.overlay.root,
            source_dispatch.root,
            self.fixture.fixture.fixture._paths().root,  # noqa: SLF001
            self.fixture.fixture._paths().root,  # noqa: SLF001
            self.fixture._paths().root,  # noqa: SLF001
        )

    def _live_events(self) -> tuple[object, ...]:
        return tuple(self.fixture.fixture.fixture.live_fixture.events())

    def test_complete_d1_d2_publishes_exact_coverage_and_replays_idempotently(
        self,
    ) -> None:
        self._complete_d1_d2()
        paths = self._paths()
        source_event = self._only_payload(
            self.fixture.fixture.fixture._paths().events  # noqa: SLF001
        )
        dependent_event = self._only_payload(
            self.fixture._paths().events  # noqa: SLF001
        )
        expected_identities = {
            (
                payload["key_id"],
                payload["natural_key"],
                payload["namespace_digest"],
            )
            for payload in (source_event, dependent_event)
        }
        self.assertEqual(len(expected_identities), 2)
        live_before = self._live_events()
        parents_before = self._parent_authority_bytes()
        materializer_count = len(
            self.fixture.fixture._immutable_materializer_receipts()  # noqa: SLF001
        )

        result = self._run()

        self.assertTrue(result.all_current_effective_d_or_r_terminal)
        self.assertEqual(result.required_key_count, 2)
        self.assertEqual(result.covered_key_count, 2)
        self.assertEqual(result.missing_key_count, 0)
        self.assertEqual(self._counts(paths), (1, 1))
        proof = self._only_payload(paths.proofs)
        event = self._only_payload(paths.events)
        proof_path = next(paths.proofs.glob("*.json"))
        proof_raw = proof_path.read_bytes()
        self.assertEqual(
            proof_path.name, f"{hashlib.sha256(proof_raw).hexdigest()}.json"
        )
        self.assertEqual(proof["required_key_count"], 2)
        self.assertEqual(proof["covered_key_count"], 2)
        self.assertEqual(proof["missing_key_count"], 0)
        self.assertEqual(proof["missing_key_ids"], [])
        self.assertEqual(proof["source_only_key_count"], 1)
        self.assertEqual(proof["dependent_key_count"], 1)
        self.assertEqual(proof["dependent_supported_key_count"], 1)
        self.assertEqual(proof["unsupported_key_count"], 0)
        self.assertEqual(
            proof["ordered_coverage_rows_sha256"],
            hashlib.sha256(
                coverage._canonical_bytes(proof["ordered_coverage_rows"])  # noqa: SLF001
            ).hexdigest(),
        )
        rows = proof["ordered_coverage_rows"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            {
                (row["key_id"], row["natural_key"], row["namespace_digest"])
                for row in rows
            },
            expected_identities,
        )
        self.assertEqual(
            {row["dependency_class"] for row in rows},
            {"source_only", "dependent"},
        )
        evidence = [row["terminal_evidence"] for row in rows]
        self.assertEqual(
            {item["evidence_kind"] for item in evidence},
            {
                "source_consumption_terminal_event",
                "dependent_consumption_terminal_event",
            },
        )
        for row in rows:
            item = row["terminal_evidence"]
            self.assertEqual(
                (item["key_id"], item["natural_key"], item["namespace_digest"]),
                (row["key_id"], row["natural_key"], row["namespace_digest"]),
            )
            body = dict(row)
            digest = body.pop("coverage_row_sha256")
            self.assertEqual(
                digest,
                hashlib.sha256(coverage._canonical_bytes(body)).hexdigest(),  # noqa: SLF001
            )
        source_paths = self.fixture.fixture.fixture._paths()  # noqa: SLF001
        dependent_paths = self.fixture._paths()  # noqa: SLF001
        by_kind = {item["evidence_kind"]: item for item in evidence}
        self.assertEqual(
            by_kind["source_consumption_terminal_event"]["receipt"],
            self._reference(
                next(source_paths.receipts.glob("*.json")), source_paths.root
            ),
        )
        self.assertEqual(
            by_kind["source_consumption_terminal_event"]["event"],
            self._reference(
                next(source_paths.events.glob("*.json")), source_paths.root
            ),
        )
        self.assertEqual(
            by_kind["dependent_consumption_terminal_event"]["receipt"],
            self._reference(
                next(dependent_paths.receipts.glob("*.json")), dependent_paths.root
            ),
        )
        self.assertEqual(
            by_kind["dependent_consumption_terminal_event"]["event"],
            self._reference(
                next(dependent_paths.events.glob("*.json")), dependent_paths.root
            ),
        )
        self.assertEqual(event["proof"], self._reference(proof_path, paths.root))
        for payload in (proof, event):
            self.assertTrue(payload["all_current_effective_d_or_r_terminal"])
            for claim in coverage.FALSE_CLAIMS:
                self.assertFalse(payload[claim], claim)
        self.assertEqual(self._live_events(), live_before)
        self.assertEqual(
            len(self.fixture.fixture._immutable_materializer_receipts()),  # noqa: SLF001
            materializer_count,
        )
        self._assert_bytes_unchanged(parents_before)

        proof_before = proof_path.read_bytes()
        event_path = next(paths.events.glob("*.json"))
        event_before = event_path.read_bytes()
        replay = self._run()

        self.assertTrue(replay.all_current_effective_d_or_r_terminal)
        self.assertEqual(self._counts(paths), (1, 1))
        self.assertEqual(proof_path.read_bytes(), proof_before)
        self.assertEqual(event_path.read_bytes(), event_before)
        self.assertEqual(self._live_events(), live_before)
        self._assert_bytes_unchanged(parents_before)

    def test_d1_only_and_d2_receipt_only_both_wait_without_a_proof(self) -> None:
        self.fixture.fixture._materialize_and_consume_d1()  # noqa: SLF001
        paths = self._paths()

        d1_only = self._run()

        self.assertIn("waiting", d1_only.status)
        self.assertFalse(d1_only.all_current_effective_d_or_r_terminal)
        self.assertEqual(d1_only.required_key_count, 2)
        self.assertEqual(d1_only.covered_key_count, 1)
        self.assertEqual(d1_only.missing_key_count, 1)
        self.assertEqual(self._counts(paths), (0, 0))

        published = self.fixture.fixture._run()  # noqa: SLF001
        self.assertTrue(published.outcome_materialization_performed)

        def crash(stage: str) -> None:
            if stage == "after_receipt":
                raise RuntimeError("synthetic D2 consumption receipt-only crash")

        with self.assertRaisesRegex(
            consumption_test.consumption.SourceDerivedDependentOutcomeConsumptionIntegrityError,
            "receipt-only crash",
        ):
            self.fixture._run(fault_hook=crash)  # noqa: SLF001
        dependent_paths = self.fixture._paths()  # noqa: SLF001
        self.assertEqual(
            self.fixture._counts(dependent_paths),  # noqa: SLF001
            (1, 1, 0),
        )
        live_before = self._live_events()
        parents_before = self._parent_authority_bytes()

        receipt_only = self._run()

        self.assertIn("waiting", receipt_only.status)
        self.assertFalse(receipt_only.all_current_effective_d_or_r_terminal)
        self.assertEqual(receipt_only.required_key_count, 2)
        self.assertEqual(receipt_only.covered_key_count, 1)
        self.assertEqual(receipt_only.missing_key_count, 1)
        self.assertEqual(self._counts(paths), (0, 0))
        self.assertEqual(self._live_events(), live_before)
        self._assert_bytes_unchanged(parents_before)

    def test_proof_only_crash_forward_adopts_only_the_matching_event(self) -> None:
        self._complete_d1_d2()
        paths = self._paths()
        live_before = self._live_events()
        parents_before = self._parent_authority_bytes()

        def crash(stage: str) -> None:
            if stage == "after_proof":
                raise RuntimeError("synthetic effective coverage proof-only crash")

        with self.assertRaisesRegex(
            coverage.SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError,
            "proof-only crash",
        ):
            self._run(fault_hook=crash)

        self.assertEqual(self._counts(paths), (1, 0))
        proof_path = next(paths.proofs.glob("*.json"))
        proof_before = proof_path.read_bytes()

        original_publish = coverage._publish  # noqa: SLF001

        def publish_event_only(
            path: Path,
            payload: dict[str, object],
            *,
            root: Path,
            name: str,
        ) -> object:
            if path.parent == paths.proofs:
                raise AssertionError("proof adoption must not republish the proof")
            return original_publish(path, payload, root=root, name=name)

        with mock.patch.object(coverage, "_publish", side_effect=publish_event_only):
            healed = self._run()

        self.assertTrue(healed.all_current_effective_d_or_r_terminal)
        self.assertEqual(self._counts(paths), (1, 1))
        self.assertEqual(proof_path.read_bytes(), proof_before)
        self.assertEqual(self._live_events(), live_before)
        self._assert_bytes_unchanged(parents_before)


if __name__ == "__main__":
    unittest.main()
