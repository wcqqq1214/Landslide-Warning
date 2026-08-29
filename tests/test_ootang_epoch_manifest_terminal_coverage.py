"""Fast contracts for frozen-manifest terminal coverage."""

from __future__ import annotations

from dataclasses import replace
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

from monitoring import ootang_epoch_manifest_terminal_coverage as coverage  # noqa: E402
from monitoring import ootang_epoch_source_terminal_aggregate as aggregate  # noqa: E402
from tests import test_ootang_epoch_source_terminal_aggregate as aggregate_test  # noqa: E402
from tests import test_ootang_epoch_step_dependency_overlay as overlay_test  # noqa: E402


class EpochManifestTerminalCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        # Compose the reviewed aggregate fixture without inheriting its tests.
        self.aggregate_fixture = aggregate_test.EpochSourceTerminalAggregateTests(
            "test_completed_overlay_publishes_exact_source_terminal_proof"
        )
        self.aggregate_fixture.setUp()
        self.addCleanup(self.aggregate_fixture.doCleanups)
        self.overlay_fixture = self.aggregate_fixture.overlay_fixture

    def _complete_upstream(
        self,
    ) -> tuple[
        aggregate.SourceTerminalAggregatePaths,
        aggregate.SourceTerminalAggregateAuthority,
    ]:
        overlay_result = self.aggregate_fixture._complete_overlay()  # noqa: SLF001
        self.assertEqual(overlay_result.status, "overlay_slot_completed")
        paths, terminal_authority = self.aggregate_fixture._aggregate_authority()  # noqa: SLF001
        aggregate_result = self.aggregate_fixture._coordinate(  # noqa: SLF001
            terminal_authority
        )
        self.assertEqual(aggregate_result.status, "source_key_terminal_aggregated")
        self.assertTrue(aggregate_result.terminal_for_source_key)
        return paths, terminal_authority

    @staticmethod
    def _coverage_authority(
        aggregate_paths: aggregate.SourceTerminalAggregatePaths,
        terminal_authority: aggregate.SourceTerminalAggregateAuthority,
    ) -> coverage.ManifestTerminalCoverageAuthority:
        aggregate_profile = aggregate.load_source_terminal_profile()
        aggregate_state = aggregate._load_state(  # noqa: SLF001
            aggregate_profile, aggregate_paths, terminal_authority
        )
        return coverage.ManifestTerminalCoverageAuthority(
            terminal_authority, aggregate_state
        )

    def _coverage_paths(self) -> coverage.ManifestTerminalCoveragePaths:
        profile = coverage.load_manifest_coverage_profile()
        return coverage.manifest_coverage_paths(
            profile,
            registry_root=self.overlay_fixture.registry_root,
            active_root=self.overlay_fixture.active_root,
            shadow_root=self.overlay_fixture.shadow_root,
        )

    def _coordinate(
        self, authority: coverage.ManifestTerminalCoverageAuthority
    ) -> coverage.ManifestTerminalCoverageResult:
        return coverage._coordinate_epoch_manifest_terminal_coverage(  # noqa: SLF001
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

    def _existing_upstream_bytes(self) -> dict[Path, bytes]:
        return {
            path: path.read_bytes()
            for path in self.overlay_fixture.base.rglob("*")
            if path.is_file()
        }

    def test_ordinary_and_aggregate_terminals_cover_exact_manifest(self) -> None:
        aggregate_paths, terminal_authority = self._complete_upstream()
        authority = self._coverage_authority(aggregate_paths, terminal_authority)
        paths = self._coverage_paths()
        before = self._existing_upstream_bytes()

        result = self._coordinate(authority)

        manifest_items = terminal_authority.overlay_authority.recovery.ordered_items
        expected_key_ids = [str(item["key_id"]) for item in manifest_items]
        source_key_id = str(self.overlay_fixture.source_item["key_id"])
        dependency_key_id = str(self.overlay_fixture.dependency_item["key_id"])
        self.assertEqual(result.status, "manifest_terminal_coverage_proved")
        self.assertTrue(result.frozen_manifest_key_coverage)
        self.assertEqual(result.manifest_key_count, 2)
        self.assertEqual(result.covered_key_count, 2)
        self.assertEqual(result.missing_key_ids, ())
        self.assertIsNotNone(result.proof_path)
        self.assertIsNotNone(result.event_path)
        proof_path = result.proof_path  # type: ignore[assignment]
        event_path = result.event_path  # type: ignore[assignment]
        proof_raw = proof_path.read_bytes()
        proof = json.loads(proof_raw)
        event = json.loads(event_path.read_bytes())
        self.assertEqual(
            proof_path.name, f"{hashlib.sha256(proof_raw).hexdigest()}.json"
        )
        self.assertEqual(event["proof"], self._reference(proof_path, paths.root))
        self.assertEqual(
            proof["manifest_identity"]["manifest_key_ids"], expected_key_ids
        )
        rows = proof["coverage_rows"]
        self.assertEqual([row["key_id"] for row in rows], expected_key_ids)
        self.assertEqual(len({row["key_id"] for row in rows}), 2)
        by_key = {row["key_id"]: row for row in rows}
        self.assertEqual(
            by_key[source_key_id]["evidence_kind"],
            "source_terminal_aggregate_event",
        )
        self.assertEqual(
            by_key[dependency_key_id]["evidence_kind"],
            "recovery_v6_terminal_receipt",
        )
        recovered = terminal_authority.overlay_authority.recovery
        self.assertFalse(recovered.chains[source_key_id][-1][0]["terminal_for_key"])
        self.assertTrue(recovered.chains[dependency_key_id][-1][0]["terminal_for_key"])
        for payload in (proof, event, json.loads(result.status_path.read_bytes())):
            self.assertTrue(payload["frozen_manifest_key_coverage"])
            for claim in coverage.FALSE_CLAIMS:
                self.assertFalse(payload[claim], claim)
        self.assertEqual({path: path.read_bytes() for path in before}, before)

        proof_before = proof_path.read_bytes()
        event_before = event_path.read_bytes()
        replay = self._coordinate(authority)
        self.assertEqual(replay.status, "manifest_terminal_coverage_current")
        self.assertTrue(replay.frozen_manifest_key_coverage)
        self.assertEqual(proof_path.read_bytes(), proof_before)
        self.assertEqual(event_path.read_bytes(), event_before)
        self.assertEqual(len(tuple(paths.proofs.glob("*.json"))), 1)
        self.assertEqual(len(tuple(paths.events.glob("*.json"))), 1)
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_orphan_aggregate_proof_is_not_manifest_coverage(self) -> None:
        self.aggregate_fixture._complete_overlay()  # noqa: SLF001
        aggregate_paths, terminal_authority = (
            self.aggregate_fixture._aggregate_authority()  # noqa: SLF001
        )
        with (
            mock.patch.object(
                aggregate,
                "_append_event",
                side_effect=RuntimeError("synthetic aggregate proof-only crash"),
            ),
            self.assertRaisesRegex(RuntimeError, "aggregate proof-only crash"),
        ):
            self.aggregate_fixture._coordinate(terminal_authority)  # noqa: SLF001

        authority = self._coverage_authority(aggregate_paths, terminal_authority)
        paths = self._coverage_paths()
        source_key_id = str(self.overlay_fixture.source_item["key_id"])
        self.assertIsNotNone(authority.aggregate_state.pending_proof_slot_id)
        self.assertEqual(len(tuple(aggregate_paths.proofs.glob("*.json"))), 1)
        self.assertFalse(aggregate_paths.events.exists())

        result = self._coordinate(authority)

        self.assertEqual(result.status, "waiting_for_manifest_terminal_coverage")
        self.assertFalse(result.frozen_manifest_key_coverage)
        self.assertEqual(result.manifest_key_count, 2)
        self.assertEqual(result.covered_key_count, 1)
        self.assertEqual(result.missing_key_ids, (source_key_id,))
        self.assertIsNone(result.proof_path)
        self.assertIsNone(result.event_path)
        self.assertFalse(paths.proofs.exists())
        self.assertFalse(paths.events.exists())
        self.assertFalse(aggregate_paths.events.exists())

    def test_coverage_proof_before_event_only_forward_adopts_event(self) -> None:
        aggregate_paths, terminal_authority = self._complete_upstream()
        authority = self._coverage_authority(aggregate_paths, terminal_authority)
        paths = self._coverage_paths()
        with (
            mock.patch.object(
                coverage,
                "_append_event",
                side_effect=RuntimeError("synthetic coverage proof-only crash"),
            ),
            self.assertRaisesRegex(RuntimeError, "coverage proof-only crash"),
        ):
            self._coordinate(authority)

        proofs = tuple(paths.proofs.glob("*.json"))
        self.assertEqual(len(proofs), 1)
        proof_before = proofs[0].read_bytes()
        self.assertFalse(paths.events.exists())

        result = self._coordinate(authority)

        self.assertEqual(result.status, "manifest_coverage_event_forward_adopted")
        self.assertTrue(result.frozen_manifest_key_coverage)
        self.assertEqual(proofs[0].read_bytes(), proof_before)
        self.assertEqual(len(tuple(paths.proofs.glob("*.json"))), 1)
        self.assertEqual(len(tuple(paths.events.glob("*.json"))), 1)

    def test_duplicate_or_nonprefix_aggregate_authority_fails_closed(self) -> None:
        aggregate_paths, terminal_authority = self._complete_upstream()
        authority = self._coverage_authority(aggregate_paths, terminal_authority)
        paths = self._coverage_paths()
        slot = terminal_authority.completed_slots[0]
        invalid = {
            "duplicate source terminal event": coverage.ManifestTerminalCoverageAuthority(
                replace(
                    terminal_authority,
                    completed_slots=(slot, slot),
                ),
                replace(
                    authority.aggregate_state,
                    events=(
                        *authority.aggregate_state.events,
                        *authority.aggregate_state.events,
                    ),
                ),
            ),
            "non-prefix source terminal event": coverage.ManifestTerminalCoverageAuthority(
                replace(terminal_authority, completed_slots=()),
                authority.aggregate_state,
            ),
        }
        for label, malformed in invalid.items():
            with (
                self.subTest(label=label),
                self.assertRaisesRegex(
                    RuntimeError, "(?i:duplicate|prefix|authority|coverage)"
                ),
            ):
                self._coordinate(malformed)
        self.assertFalse(paths.proofs.exists())
        self.assertFalse(paths.events.exists())

    def test_in_memory_terminal_payload_must_match_durable_receipt(self) -> None:
        self.aggregate_fixture._complete_overlay()  # noqa: SLF001
        aggregate_paths, terminal_authority = (
            self.aggregate_fixture._aggregate_authority()  # noqa: SLF001
        )
        authority = self._coverage_authority(aggregate_paths, terminal_authority)
        recovered = terminal_authority.overlay_authority.recovery
        source_key_id = str(self.overlay_fixture.source_item["key_id"])
        source_receipt, source_snapshot = recovered.chains[source_key_id][-1]
        tampered_receipt = {**source_receipt, "terminal_for_key": True}
        tampered_recovery = replace(
            recovered,
            chains={
                **recovered.chains,
                source_key_id: [(tampered_receipt, source_snapshot)],
            },
        )
        malformed = coverage.ManifestTerminalCoverageAuthority(
            replace(
                terminal_authority,
                overlay_authority=replace(
                    terminal_authority.overlay_authority,
                    recovery=tampered_recovery,
                ),
            ),
            authority.aggregate_state,
        )

        with self.assertRaisesRegex(RuntimeError, "durable|snapshot|identity"):
            self._coordinate(malformed)

        paths = self._coverage_paths()
        self.assertFalse(paths.proofs.exists())
        self.assertFalse(paths.events.exists())


if __name__ == "__main__":
    unittest.main()
