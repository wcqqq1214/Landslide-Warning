"""Focused contracts for dependency-gated source-derived outcome dispatch."""

from __future__ import annotations

from datetime import timedelta
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
    ootang_epoch_source_derived_dependent_outcome_dispatch as dependent_dispatch,
)
from monitoring import ootang_epoch_workset_manifest as manifest  # noqa: E402
from monitoring import ootang_epoch_workset_recovery as recovery  # noqa: E402
from monitoring import ootang_outcome_materializer as outcomes  # noqa: E402
from monitoring import ootang_prequential_live as live  # noqa: E402
from tests import (  # noqa: E402
    test_ootang_epoch_source_derived_outcome_consumption as consumption_test,
)


NOW = consumption_test.NOW + timedelta(seconds=1)


class SourceDerivedDependentOutcomeDispatchTests(unittest.TestCase):
    """Exercise the exact D1 terminal event -> D2 materialization edge."""

    def setUp(self) -> None:
        self.fixture = consumption_test.SourceDerivedOutcomeConsumptionTests(
            "test_first_d_fresh_cas_is_terminal_only_for_its_effective_key"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def _paths(
        self,
    ) -> dependent_dispatch.SourceDerivedDependentOutcomeDispatchPaths:
        fixture = self.fixture.dispatch_fixture.fixture.fixture
        return dependent_dispatch.source_derived_dependent_outcome_dispatch_paths(
            dependent_dispatch.load_source_derived_dependent_outcome_dispatch_profile(),
            registry_root=fixture.manifest_fixture.registry_root,
            active_root=fixture.source_fixture.root,
            shadow_root=fixture.manifest_fixture.shadow_root,
        )

    def _run(
        self, *, fault_hook: object | None = None
    ) -> dependent_dispatch.SourceDerivedDependentOutcomeDispatchResult:
        dispatch_fixture = self.fixture.dispatch_fixture
        fixture = dispatch_fixture.fixture.fixture
        assert dispatch_fixture.fixture.binding is not None
        with (
            mock.patch.object(
                manifest,
                "_default_admission_cut_binding",
                return_value=dispatch_fixture.fixture.binding,
            ),
            mock.patch.object(
                recovery,
                "_frozen_live_prefix",
                side_effect=self.fixture._frozen_live_prefix,  # noqa: SLF001
            ),
            mock.patch.object(
                live,
                "_epoch_id",
                return_value=consumption_test.OLD_EPOCH_ID,
            ),
            mock.patch.object(
                outcomes,
                "_select_target",
                side_effect=AssertionError(
                    "dependent dispatch must not run the current-source selector"
                ),
            ),
            mock.patch.object(
                outcomes,
                "materialize_outcome",
                side_effect=AssertionError(
                    "dependent dispatch must use the exact historical writer"
                ),
            ),
        ):
            return dependent_dispatch._coordinate_source_derived_dependent_outcome_dispatch(  # noqa: SLF001
                registry_root=fixture.manifest_fixture.registry_root,
                active_root=fixture.source_fixture.root,
                shadow_root=fixture.manifest_fixture.shadow_root,
                clock=lambda: NOW,
                fault_hook=fault_hook,
                load_derived_authority=(
                    dispatch_fixture.fixture._load_derived  # noqa: SLF001
                ),
            )

    def _materialize_and_consume_d1(self) -> None:
        self.fixture._publish_materialization()  # noqa: SLF001
        consumed = self.fixture._run()  # noqa: SLF001
        self.assertTrue(consumed.terminal_for_effective_key)

    @staticmethod
    def _counts(
        paths: dependent_dispatch.SourceDerivedDependentOutcomeDispatchPaths,
    ) -> tuple[int, int, int]:
        return (
            len(tuple(paths.intents.glob("*.json"))),
            len(tuple(paths.receipts.glob("*.json"))),
            len(tuple(paths.events.glob("*.json"))),
        )

    @staticmethod
    def _only_payload(directory: Path) -> dict[str, object]:
        entries = tuple(directory.glob("*.json"))
        if len(entries) != 1:
            raise AssertionError(f"expected one JSON record in {directory}: {entries}")
        return json.loads(entries[0].read_bytes())

    @staticmethod
    def _tree_bytes(*roots: Path) -> dict[Path, bytes]:
        return {
            path: path.read_bytes()
            for root in roots
            for path in root.rglob("*")
            if path.is_file()
        }

    def _immutable_materializer_receipts(self) -> tuple[Path, ...]:
        fixture = self.fixture.dispatch_fixture.fixture.fixture
        receipt_root = outcomes._runtime_path(  # noqa: SLF001
            outcomes.load_config(),
            fixture.source_fixture.root,
            "outcome_receipts",
        )
        return tuple(
            path for path in receipt_root.rglob("*.json") if path.name != "active.json"
        )

    def test_d1_terminal_event_materializes_d2_once_without_upstream_mutation(
        self,
    ) -> None:
        self._materialize_and_consume_d1()
        paths = self._paths()
        source_dispatch_paths = self.fixture.dispatch_fixture._paths()  # noqa: SLF001
        consumption_paths = self.fixture._paths()  # noqa: SLF001
        upstream_before = self._tree_bytes(
            source_dispatch_paths.root,
            consumption_paths.root,
        )

        result = self._run()

        self.assertTrue(result.outcome_materialization_performed)
        self.assertFalse(result.terminal_for_effective_key)
        self.assertEqual(self._counts(paths), (1, 1, 1))
        self.assertEqual(len(self._immutable_materializer_receipts()), 2)
        intent = self._only_payload(paths.intents)
        self.assertEqual(intent["dri_kind"], "D")
        self.assertEqual(
            intent["materialization_contract"]["target_date"], "2020-07-03"
        )
        proof = intent["dependency_proof"]
        self.assertTrue(proof["source_expansion_gate"]["satisfies_dependency"])
        self.assertEqual(len(proof["terminal_consumption_dependencies"]), 1)
        consumed_dependency = proof["terminal_consumption_dependencies"][0]
        self.assertIn("2020-07-02", consumed_dependency["natural_key"])
        self.assertIn("receipt", consumed_dependency)
        self.assertIn("event", consumed_dependency)
        self.assertEqual(
            {path: path.read_bytes() for path in upstream_before}, upstream_before
        )

        replay = self._run()

        self.assertFalse(replay.outcome_materialization_performed)
        self.assertEqual(self._counts(paths), (1, 1, 1))
        self.assertEqual(len(self._immutable_materializer_receipts()), 2)
        self.assertEqual(
            {path: path.read_bytes() for path in upstream_before}, upstream_before
        )

    def test_nonterminal_d1_states_never_create_dependent_authority(self) -> None:
        self.fixture._publish_materialization()  # noqa: SLF001
        paths = self._paths()

        materialization_only = self._run()

        self.assertFalse(materialization_only.outcome_materialization_performed)
        self.assertIn("waiting", materialization_only.status)
        self.assertEqual(self._counts(paths), (0, 0, 0))
        self.assertEqual(len(self._immutable_materializer_receipts()), 1)

        def crash(stage: str) -> None:
            if stage == "after_receipt":
                raise RuntimeError("synthetic consumption receipt-only crash")

        with self.assertRaisesRegex(
            consumption_test.consumption.SourceDerivedOutcomeConsumptionIntegrityError,
            "consumption receipt-only crash",
        ):
            self.fixture._run(fault_hook=crash)  # noqa: SLF001
        consumption_paths = self.fixture._paths()  # noqa: SLF001
        self.assertEqual(len(tuple(consumption_paths.receipts.glob("*.json"))), 1)
        self.assertEqual(tuple(consumption_paths.events.glob("*.json")), ())

        receipt_without_event = self._run()

        self.assertFalse(receipt_without_event.outcome_materialization_performed)
        self.assertIn("waiting", receipt_without_event.status)
        self.assertEqual(self._counts(paths), (0, 0, 0))
        self.assertEqual(len(self._immutable_materializer_receipts()), 1)

    def test_post_materializer_crash_adopts_exact_d2_publication(self) -> None:
        self._materialize_and_consume_d1()
        paths = self._paths()

        def crash(stage: str) -> None:
            if stage == "after_materializer_action":
                raise RuntimeError("synthetic dependent post-materializer crash")

        with self.assertRaisesRegex(
            dependent_dispatch.SourceDerivedDependentOutcomeDispatchIntegrityError,
            "post-materializer crash",
        ):
            self._run(fault_hook=crash)

        self.assertEqual(self._counts(paths), (1, 0, 0))
        self.assertEqual(len(self._immutable_materializer_receipts()), 2)

        with mock.patch.object(
            outcomes,
            "_publish_candidate",
            side_effect=AssertionError(
                "an exact committed D2 materializer receipt must be adopted"
            ),
        ) as publish:
            adopted = self._run()

        publish.assert_not_called()
        self.assertTrue(adopted.outcome_materialization_performed)
        self.assertEqual(
            self._only_payload(paths.intents)["materialization_contract"][
                "target_date"
            ],
            "2020-07-03",
        )
        self.assertEqual(self._counts(paths), (1, 1, 1))
        self.assertEqual(len(self._immutable_materializer_receipts()), 2)

    def test_receipt_only_crash_appends_only_dependent_event(self) -> None:
        self._materialize_and_consume_d1()
        paths = self._paths()

        def crash(stage: str) -> None:
            if stage == "after_receipt":
                raise RuntimeError("synthetic dependent receipt-only crash")

        with self.assertRaisesRegex(
            dependent_dispatch.SourceDerivedDependentOutcomeDispatchIntegrityError,
            "receipt-only crash",
        ):
            self._run(fault_hook=crash)

        self.assertEqual(self._counts(paths), (1, 1, 0))
        self.assertEqual(len(self._immutable_materializer_receipts()), 2)

        with mock.patch.object(
            outcomes,
            "_publish_candidate",
            side_effect=AssertionError(
                "receipt-only healing must not touch the materializer"
            ),
        ) as publish:
            healed = self._run()

        publish.assert_not_called()
        self.assertTrue(healed.outcome_materialization_performed)
        self.assertEqual(
            self._only_payload(paths.intents)["materialization_contract"][
                "target_date"
            ],
            "2020-07-03",
        )
        self.assertEqual(self._counts(paths), (1, 1, 1))
        self.assertEqual(len(self._immutable_materializer_receipts()), 2)


if __name__ == "__main__":
    unittest.main()
