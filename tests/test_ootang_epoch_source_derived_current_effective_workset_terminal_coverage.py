"""Focused contracts for the exact current-effective terminal union."""

from __future__ import annotations

from contextlib import contextmanager
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
    ootang_epoch_source_derived_current_effective_workset_terminal_coverage as current,
)
from monitoring import (  # noqa: E402
    ootang_epoch_source_derived_retained_base_terminal_coverage as retained,
)
from monitoring import ootang_epoch_workset_manifest as manifest  # noqa: E402
from monitoring import ootang_epoch_workset_recovery as recovery  # noqa: E402
from monitoring import ootang_outcome_materializer as outcomes  # noqa: E402
from monitoring import ootang_prequential_live as live  # noqa: E402
from tests import (  # noqa: E402
    test_ootang_epoch_source_derived_outcome_consumption as source_consumption_test,
)
from tests import (  # noqa: E402
    test_ootang_epoch_source_derived_source_parent_terminal_aggregate as parent_test,
)


NOW = parent_test.NOW + timedelta(seconds=1)


class SourceDerivedCurrentEffectiveWorksetTerminalCoverageTests(unittest.TestCase):
    """Join P, retained Q, and current D/R only by exact current identity."""

    def setUp(self) -> None:
        self.fixture = parent_test.SourceDerivedSourceParentTerminalAggregateTests(
            "test_complete_coverage_publishes_exact_parent_and_replays_idempotently"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def _runtime(self) -> tuple[object, object]:
        coverage_fixture = self.fixture.fixture
        source_dispatch_fixture = (
            coverage_fixture.fixture.fixture.fixture.dispatch_fixture
        )
        base_fixture = source_dispatch_fixture.fixture.fixture
        return source_dispatch_fixture, base_fixture

    @contextmanager
    def _patched_runtime(self):
        coverage_fixture = self.fixture.fixture
        source_dispatch_fixture, base_fixture = self._runtime()
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
                    coverage_fixture.fixture.fixture.fixture._frozen_live_prefix  # noqa: SLF001
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
                    "current-effective coverage must not run the source selector"
                ),
            ),
            mock.patch.object(
                outcomes,
                "materialize_outcome",
                side_effect=AssertionError(
                    "current-effective coverage must not materialize an outcome"
                ),
            ),
        ):
            yield source_dispatch_fixture, base_fixture

    def _paths(
        self,
    ) -> current.SourceDerivedCurrentEffectiveWorksetTerminalCoveragePaths:
        parent_paths = self.fixture._paths()  # noqa: SLF001
        return current.source_derived_current_effective_workset_terminal_coverage_paths(
            current.load_source_derived_current_effective_workset_terminal_coverage_profile(),
            registry_root=parent_paths.coverage.registry_root,
            active_root=parent_paths.coverage.active_root,
            shadow_root=parent_paths.coverage.shadow_root,
        )

    def _run_retained(
        self,
    ) -> retained.SourceDerivedRetainedBaseTerminalCoverageResult:
        with self._patched_runtime() as (source_dispatch_fixture, base_fixture):
            return retained._coordinate_source_derived_retained_base_terminal_coverage(  # noqa: SLF001
                registry_root=base_fixture.manifest_fixture.registry_root,
                active_root=base_fixture.source_fixture.root,
                shadow_root=base_fixture.manifest_fixture.shadow_root,
                clock=lambda: NOW,
                load_derived_authority=(
                    source_dispatch_fixture.fixture._load_derived  # noqa: SLF001
                ),
            )

    def _run(
        self, *, fault_hook: object | None = None
    ) -> current.SourceDerivedCurrentEffectiveWorksetTerminalCoverageResult:
        with self._patched_runtime() as (source_dispatch_fixture, base_fixture):
            return current._coordinate_source_derived_current_effective_workset_terminal_coverage(  # noqa: SLF001
                registry_root=base_fixture.manifest_fixture.registry_root,
                active_root=base_fixture.source_fixture.root,
                shadow_root=base_fixture.manifest_fixture.shadow_root,
                clock=lambda: NOW,
                fault_hook=fault_hook,
                load_derived_authority=(
                    source_dispatch_fixture.fixture._load_derived  # noqa: SLF001
                ),
            )

    def _complete_all_families(self) -> None:
        self.fixture._complete_coverage()  # noqa: SLF001
        parent_result = self.fixture._run()  # noqa: SLF001
        retained_result = self._run_retained()
        self.assertTrue(parent_result.current_source_ingest_parent_terminal)
        self.assertTrue(retained_result.all_current_retained_base_items_terminal)

    @staticmethod
    def _counts(
        paths: current.SourceDerivedCurrentEffectiveWorksetTerminalCoveragePaths,
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

    def test_complete_three_family_union_publishes_and_replays_exactly(self) -> None:
        self._complete_all_families()
        paths = self._paths()
        upstream_before = self._tree_bytes(
            paths.registry_root,
            paths.coverage.active_root,
            paths.coverage.shadow_root,
            excluded=paths.root,
        )

        result = self._run()

        self.assertTrue(result.current_effective_workset_terminal_coverage)
        self.assertEqual(result.effective_item_count, 3)
        self.assertEqual(result.source_parent_count, 1)
        self.assertEqual(result.retained_base_count, 0)
        self.assertEqual(result.current_dri_count, 2)
        self.assertEqual(self._counts(paths), (1, 1))
        proof_path = next(paths.proofs.glob("*.json"))
        event_path = next(paths.events.glob("*.json"))
        proof = json.loads(proof_path.read_bytes())
        event = json.loads(event_path.read_bytes())
        self.assertEqual(
            proof_path.name,
            f"{hashlib.sha256(proof_path.read_bytes()).hexdigest()}.json",
        )
        self.assertTrue(proof["pairwise_disjoint_identity_union"])
        self.assertTrue(proof["exact_current_effective_identity_bijection"])
        self.assertEqual(proof["effective_item_count"], 3)
        self.assertEqual(
            proof["family_counts"],
            {
                "source_parent_count": 1,
                "retained_base_count": 0,
                "current_dri_count": 2,
            },
        )
        rows = proof["ordered_effective_terminal_rows"]
        self.assertEqual([row["topological_index"] for row in rows], [0, 1, 2])
        self.assertEqual(
            [row["terminal_authority_kind"] for row in rows].count(
                "source_parent_terminal_aggregate"
            ),
            1,
        )
        self.assertEqual(
            [row["terminal_authority_kind"] for row in rows].count(
                "current_dri_terminal_coverage"
            ),
            2,
        )
        self.assertEqual(event["proof"], self._reference(proof_path, paths.root))
        self.assertEqual(
            proof["source_parent_terminal_event"],
            self._reference(next(paths.source_parent.events.glob("*.json")), paths.source_parent.root),
        )
        self.assertEqual(
            proof["retained_base_terminal_event"],
            self._reference(next(paths.retained.events.glob("*.json")), paths.retained.root),
        )
        self.assertEqual(
            proof["current_dri_terminal_event"],
            self._reference(next(paths.coverage.events.glob("*.json")), paths.coverage.root),
        )
        for payload in (proof, event):
            self.assertTrue(payload["current_effective_workset_terminal_coverage"])
            for claim in current.FALSE_CLAIMS:
                self.assertFalse(payload[claim], claim)
        self.assertFalse(result.all_effective_items_terminal)
        self.assertFalse(result.terminal_transition_closure_implemented)
        self.assertFalse(result.lifecycle_authority)
        self.assertFalse(json.loads(paths.status.read_bytes())["cache_authority"])
        self.assertEqual(
            {path: path.read_bytes() for path in upstream_before}, upstream_before
        )

        proof_before = proof_path.read_bytes()
        event_before = event_path.read_bytes()
        paths.status.unlink()
        replay = self._run()

        self.assertEqual(
            replay.status, "current_effective_workset_terminal_coverage_current"
        )
        self.assertEqual(proof_path.read_bytes(), proof_before)
        self.assertEqual(event_path.read_bytes(), event_before)
        self.assertFalse(json.loads(paths.status.read_bytes())["cache_authority"])
        self.assertEqual(
            {path: path.read_bytes() for path in upstream_before}, upstream_before
        )

    def test_missing_source_parent_family_waits_without_own_authority(self) -> None:
        self.fixture._complete_coverage()  # noqa: SLF001
        retained_result = self._run_retained()
        self.assertTrue(retained_result.all_current_retained_base_items_terminal)
        paths = self._paths()

        result = self._run()

        self.assertEqual(
            result.status, "waiting_for_source_parent_terminal_aggregate"
        )
        self.assertFalse(result.current_effective_workset_terminal_coverage)
        self.assertEqual(self._counts(paths), (0, 0))
        self.assertFalse(json.loads(paths.status.read_bytes())["cache_authority"])

    def test_proof_only_retry_appends_only_matching_event(self) -> None:
        self._complete_all_families()
        paths = self._paths()

        def crash(stage: str) -> None:
            if stage == "after_proof":
                raise RuntimeError("synthetic current-effective proof-only crash")

        with self.assertRaisesRegex(
            current.SourceDerivedCurrentEffectiveWorksetTerminalCoverageIntegrityError,
            "proof-only crash",
        ):
            self._run(fault_hook=crash)

        self.assertEqual(self._counts(paths), (1, 0))
        proof_path = next(paths.proofs.glob("*.json"))
        proof_before = proof_path.read_bytes()
        original_publish = current._publish  # noqa: SLF001

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

        with mock.patch.object(current, "_publish", side_effect=publish_event_only):
            healed = self._run()

        self.assertTrue(healed.current_effective_workset_terminal_coverage)
        self.assertEqual(self._counts(paths), (1, 1))
        self.assertEqual(proof_path.read_bytes(), proof_before)


if __name__ == "__main__":
    unittest.main()
