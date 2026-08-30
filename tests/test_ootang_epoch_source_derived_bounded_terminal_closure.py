"""Focused contracts for bounded source-derived terminal closure."""

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
    ootang_epoch_source_derived_bounded_terminal_closure as closure,
)
from tests import (  # noqa: E402
    test_ootang_epoch_source_derived_current_effective_workset_terminal_coverage as current_test,
)


NOW = current_test.NOW + timedelta(seconds=1)


class SourceDerivedBoundedTerminalClosureTests(unittest.TestCase):
    """Resolve frozen and D/R/I inventories under one immutable source edge."""

    def setUp(self) -> None:
        self.fixture = (
            current_test.SourceDerivedCurrentEffectiveWorksetTerminalCoverageTests(
                "test_complete_three_family_union_publishes_and_replays_exactly"
            )
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def _paths(self) -> closure.SourceDerivedBoundedTerminalClosurePaths:
        current_paths = self.fixture._paths()  # noqa: SLF001
        return closure.source_derived_bounded_terminal_closure_paths(
            closure.load_source_derived_bounded_terminal_closure_profile(),
            registry_root=current_paths.registry_root,
            active_root=current_paths.coverage.active_root,
            shadow_root=current_paths.coverage.shadow_root,
        )

    def _run(
        self, *, fault_hook: object | None = None
    ) -> closure.SourceDerivedBoundedTerminalClosureResult:
        with self.fixture._patched_runtime() as (  # noqa: SLF001
            source_dispatch_fixture,
            base_fixture,
        ):
            return closure._coordinate_source_derived_bounded_terminal_closure(  # noqa: SLF001
                registry_root=base_fixture.manifest_fixture.registry_root,
                active_root=base_fixture.source_fixture.root,
                shadow_root=base_fixture.manifest_fixture.shadow_root,
                clock=lambda: NOW,
                fault_hook=fault_hook,
                load_derived_authority=(
                    source_dispatch_fixture.fixture._load_derived  # noqa: SLF001
                ),
            )

    def _complete_current_effective(self) -> None:
        self.fixture._complete_all_families()  # noqa: SLF001
        result = self.fixture._run()  # noqa: SLF001
        self.assertTrue(result.current_effective_workset_terminal_coverage)

    @staticmethod
    def _counts(
        paths: closure.SourceDerivedBoundedTerminalClosurePaths,
    ) -> tuple[int, int]:
        return (
            len(tuple(paths.proofs.glob("*.json"))),
            len(tuple(paths.events.glob("*.json"))),
        )

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

    def test_exact_frozen_effective_successor_closure_publishes_and_replays(
        self,
    ) -> None:
        self._complete_current_effective()
        paths = self._paths()
        upstream_before = self._tree_bytes(
            paths.registry_root,
            paths.current.coverage.active_root,
            paths.current.coverage.shadow_root,
            excluded=paths.root,
        )

        result = self._run()

        self.assertTrue(result.current_source_derived_bounded_terminal_closure)
        self.assertEqual(result.frozen_base_item_count, 1)
        self.assertEqual(result.current_effective_item_count, 3)
        self.assertEqual(result.derived_item_count, 2)
        self.assertEqual(result.rebound_item_count, 0)
        self.assertEqual(result.invalidated_item_count, 0)
        self.assertFalse(result.bounded_workset_recovery_implemented)
        self.assertFalse(result.old_epoch_drained)
        self.assertFalse(result.lifecycle_authority)
        self.assertEqual(self._counts(paths), (1, 1))
        proof_path = next(paths.proofs.glob("*.json"))
        event_path = next(paths.events.glob("*.json"))
        proof = json.loads(proof_path.read_bytes())
        event = json.loads(event_path.read_bytes())
        self.assertEqual(
            proof_path.name,
            f"{hashlib.sha256(proof_path.read_bytes()).hexdigest()}.json",
        )
        self.assertTrue(proof["frozen_base_partition_exact"])
        self.assertTrue(proof["current_effective_partition_exact"])
        self.assertTrue(proof["source_derived_successor_inventory_complete"])
        self.assertTrue(
            proof["all_frozen_base_identities_resolved_under_source_edge"]
        )
        self.assertTrue(
            proof["all_source_derived_successors_resolved_under_source_edge"]
        )
        self.assertEqual(len(proof["base_resolution_rows"]), 1)
        self.assertEqual(
            proof["base_resolution_rows"][0]["resolution_kind"],
            "source_parent_terminal_in_current_effective",
        )
        self.assertEqual(len(proof["successor_resolution_rows"]), 2)
        self.assertEqual(
            {row["classification_kind"] for row in proof["successor_resolution_rows"]},
            {"D"},
        )
        self.assertEqual(
            proof["current_effective_terminal_coverage_event"],
            self._reference(
                next(paths.current.events.glob("*.json")), paths.current.root
            ),
        )
        self.assertEqual(event["proof"], self._reference(proof_path, paths.root))
        for payload in (proof, event):
            self.assertTrue(
                payload["current_source_derived_bounded_terminal_closure"]
            )
            for claim in closure.FALSE_CLAIMS:
                self.assertFalse(payload[claim], claim)
        self.assertFalse(json.loads(paths.status.read_bytes())["cache_authority"])
        self.assertEqual(
            {path: path.read_bytes() for path in upstream_before}, upstream_before
        )

        proof_before = proof_path.read_bytes()
        event_before = event_path.read_bytes()
        paths.status.unlink()
        replay = self._run()

        self.assertEqual(
            replay.status, "source_derived_bounded_terminal_closure_current"
        )
        self.assertEqual(proof_path.read_bytes(), proof_before)
        self.assertEqual(event_path.read_bytes(), event_before)
        self.assertFalse(json.loads(paths.status.read_bytes())["cache_authority"])
        self.assertEqual(
            {path: path.read_bytes() for path in upstream_before}, upstream_before
        )

    def test_missing_current_effective_event_waits_without_own_authority(self) -> None:
        self.fixture._complete_all_families()  # noqa: SLF001
        paths = self._paths()

        result = self._run()

        self.assertEqual(
            result.status, "waiting_for_current_effective_terminal_coverage"
        )
        self.assertFalse(result.current_source_derived_bounded_terminal_closure)
        self.assertEqual(self._counts(paths), (0, 0))
        self.assertFalse(json.loads(paths.status.read_bytes())["cache_authority"])

    def test_proof_only_retry_appends_only_matching_event(self) -> None:
        self._complete_current_effective()
        paths = self._paths()

        def crash(stage: str) -> None:
            if stage == "after_proof":
                raise RuntimeError("synthetic bounded closure proof-only crash")

        with self.assertRaisesRegex(
            closure.SourceDerivedBoundedTerminalClosureIntegrityError,
            "proof-only crash",
        ):
            self._run(fault_hook=crash)

        self.assertEqual(self._counts(paths), (1, 0))
        proof_path = next(paths.proofs.glob("*.json"))
        proof_before = proof_path.read_bytes()
        original_publish = closure._publish  # noqa: SLF001

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

        with mock.patch.object(closure, "_publish", side_effect=publish_event_only):
            healed = self._run()

        self.assertTrue(healed.current_source_derived_bounded_terminal_closure)
        self.assertEqual(self._counts(paths), (1, 1))
        self.assertEqual(proof_path.read_bytes(), proof_before)


if __name__ == "__main__":
    unittest.main()
