"""Focused contracts for dependent source-derived outcome consumption."""

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
    ootang_epoch_source_derived_dependent_outcome_consumption as consumption,
)
from monitoring import ootang_epoch_workset_manifest as manifest  # noqa: E402
from monitoring import ootang_epoch_workset_recovery as recovery  # noqa: E402
from monitoring import ootang_outcome_materializer as outcomes  # noqa: E402
from monitoring import ootang_prequential_live as live  # noqa: E402
from tests import (  # noqa: E402
    test_ootang_epoch_source_derived_dependent_outcome_dispatch as dispatch_test,
)
from tests import (  # noqa: E402
    test_ootang_epoch_source_derived_outcome_consumption as source_consumption_test,
)


NOW = dispatch_test.NOW + timedelta(seconds=1)


class SourceDerivedDependentOutcomeConsumptionTests(unittest.TestCase):
    """Exercise the exact dependent-dispatch event -> D2 CAS edge."""

    def setUp(self) -> None:
        self.fixture = dispatch_test.SourceDerivedDependentOutcomeDispatchTests(
            "test_d1_terminal_event_materializes_d2_once_without_upstream_mutation"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def _paths(
        self,
    ) -> consumption.SourceDerivedDependentOutcomeConsumptionPaths:
        fixture = self.fixture.fixture.dispatch_fixture.fixture.fixture
        return consumption.source_derived_dependent_outcome_consumption_paths(
            consumption.load_source_derived_dependent_outcome_consumption_profile(),
            registry_root=fixture.manifest_fixture.registry_root,
            active_root=fixture.source_fixture.root,
            shadow_root=fixture.manifest_fixture.shadow_root,
        )

    def _run(
        self,
        *,
        fault_hook: object | None = None,
        consume_action: object | None = None,
    ) -> consumption.SourceDerivedDependentOutcomeConsumptionResult:
        source_dispatch_fixture = self.fixture.fixture.dispatch_fixture
        fixture = source_dispatch_fixture.fixture.fixture
        assert source_dispatch_fixture.fixture.binding is not None
        kwargs: dict[str, object] = {}
        if consume_action is not None:
            kwargs["consume_action"] = consume_action
        with (
            mock.patch.object(
                manifest,
                "_default_admission_cut_binding",
                return_value=source_dispatch_fixture.fixture.binding,
            ),
            mock.patch.object(
                recovery,
                "_frozen_live_prefix",
                side_effect=self.fixture.fixture._frozen_live_prefix,  # noqa: SLF001
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
                    "dependent consumption must not run the current-source selector"
                ),
            ),
            mock.patch.object(
                outcomes,
                "materialize_outcome",
                side_effect=AssertionError(
                    "dependent consumption must not republish an outcome"
                ),
            ),
        ):
            return consumption._coordinate_source_derived_dependent_outcome_consumption(  # noqa: SLF001
                registry_root=fixture.manifest_fixture.registry_root,
                active_root=fixture.source_fixture.root,
                shadow_root=fixture.manifest_fixture.shadow_root,
                clock=lambda: NOW,
                fault_hook=fault_hook,
                load_derived_authority=(
                    source_dispatch_fixture.fixture._load_derived  # noqa: SLF001
                ),
                **kwargs,
            )

    def _publish_d2(self) -> dict[str, object]:
        self.fixture._materialize_and_consume_d1()  # noqa: SLF001
        published = self.fixture._run()  # noqa: SLF001
        self.assertTrue(published.outcome_materialization_performed)
        return self._only_payload(self.fixture._paths().intents)  # noqa: SLF001

    @staticmethod
    def _counts(
        paths: consumption.SourceDerivedDependentOutcomeConsumptionPaths,
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

    @staticmethod
    def _assert_bytes_unchanged(before: dict[Path, bytes]) -> None:
        actual = {path: path.read_bytes() for path in before}
        if actual != before:
            raise AssertionError("upstream authority bytes changed")

    def _parent_authority_bytes(self) -> dict[Path, bytes]:
        return self._tree_bytes(
            self.fixture.fixture.dispatch_fixture._paths().root,  # noqa: SLF001
            self.fixture.fixture._paths().root,  # noqa: SLF001
            self.fixture._paths().root,  # noqa: SLF001
        )

    def test_d2_fresh_cas_binds_exact_pre_head_and_event_specs(self) -> None:
        dependent_intent = self._publish_d2()
        paths = self._paths()
        live_before = tuple(self.fixture.fixture.live_fixture.events())
        self.assertEqual(len(live_before), 2)
        parents_before = self._parent_authority_bytes()

        original_append = recovery.live_cas.append_transaction_at_pre_head_v1
        with mock.patch.object(
            recovery.live_cas,
            "append_transaction_at_pre_head_v1",
            wraps=original_append,
        ) as append:
            result = self._run()

        append.assert_called_once()
        expected = append.call_args.kwargs["expected_pre_head"]
        self.assertEqual(expected.event_count, 2)
        self.assertEqual(expected.sequence_id, live_before[-1].sequence_id)
        self.assertEqual(expected.entry_sha256, live_before[-1].entry_sha256)
        live_after = tuple(self.fixture.fixture.live_fixture.events())
        self.assertEqual(len(live_after), 3)
        self.assertEqual(live_after[:-1], live_before)
        self.assertEqual(live_after[-1].event_type, "backfill_not_blind")
        self.assertEqual(live_after[-1].payload["target_date"], "2020-07-03")
        self.assertTrue(result.outcome_or_revision_consumed)
        self.assertTrue(result.terminal_for_effective_key)
        self.assertFalse(result.terminal_for_recovery_v6_key)
        self.assertEqual(result.key_id, dependent_intent["key_id"])
        self.assertEqual(self._counts(paths), (1, 1, 1))

        intent = self._only_payload(paths.intents)
        receipt = self._only_payload(paths.receipts)
        event = self._only_payload(paths.events)
        self.assertEqual(intent["key_id"], dependent_intent["key_id"])
        self.assertEqual(intent["step_index"], 3)
        self.assertEqual(
            intent["dependent_dispatch"]["step_id"], dependent_intent["step_id"]
        )
        for name in ("intent", "receipt", "event"):
            self.assertIn(name, intent["dependent_dispatch"])
        self.assertEqual(
            intent["dependency_proof"], dependent_intent["dependency_proof"]
        )
        self.assertEqual(
            intent["dependency_proof_sha256"],
            dependent_intent["dependency_proof_sha256"],
        )
        self.assertEqual(intent["consumption_contract"]["target_date"], "2020-07-03")
        self.assertEqual(intent["expected_pre_head"]["event_count"], 2)
        self.assertEqual(
            intent["expected_pre_head"]["entry_sha256"],
            live_before[-1].entry_sha256,
        )
        self.assertEqual(len(intent["canonical_event_specs"]), 1)
        self.assertEqual(
            intent["consumption_contract"]["event_specs_sha256"],
            intent["canonical_event_specs_sha256"],
        )
        semantics = receipt["output"]["semantics"]
        self.assertEqual(semantics["writer_branch"], "first_backfill")
        self.assertEqual(semantics["previous_last_finalized_date"], "2020-07-02")
        self.assertEqual(semantics["backfill_count_before"], 1)
        self.assertEqual(semantics["backfill_count_after"], 2)
        self.assertEqual(semantics["event_count"], 1)
        for payload in (receipt, event):
            self.assertTrue(payload["terminal_for_effective_key"])
            self.assertFalse(payload["terminal_for_recovery_v6_key"])
            self.assertFalse(payload["source_parent_terminal"])
        self._assert_bytes_unchanged(parents_before)
        self.assertEqual(len(self.fixture._immutable_materializer_receipts()), 2)  # noqa: SLF001

        with mock.patch.object(
            recovery.live_cas,
            "append_transaction_at_pre_head_v1",
            side_effect=AssertionError("terminal replay must not append again"),
        ) as append:
            replay = self._run()

        append.assert_not_called()
        self.assertFalse(replay.outcome_or_revision_consumed)
        self.assertEqual(tuple(self.fixture.fixture.live_fixture.events()), live_after)
        self.assertEqual(self._counts(paths), (1, 1, 1))
        self._assert_bytes_unchanged(parents_before)

    def test_post_cas_crash_adopts_without_a_second_d2_append(self) -> None:
        dependent_intent = self._publish_d2()
        paths = self._paths()
        live_before = tuple(self.fixture.fixture.live_fixture.events())
        parents_before = self._parent_authority_bytes()

        def crash(stage: str) -> None:
            if stage == "after_live_ledger_cas":
                raise RuntimeError("synthetic dependent-consumption post-CAS crash")

        with self.assertRaisesRegex(
            consumption.SourceDerivedDependentOutcomeConsumptionIntegrityError,
            "post-CAS crash",
        ):
            self._run(fault_hook=crash)

        committed = tuple(self.fixture.fixture.live_fixture.events())
        self.assertEqual(len(committed), len(live_before) + 1)
        self.assertEqual(committed[:-1], live_before)
        self.assertEqual(committed[-1].payload["target_date"], "2020-07-03")
        self.assertEqual(self._counts(paths), (1, 0, 0))

        with mock.patch.object(
            recovery.live_cas,
            "append_transaction_at_pre_head_v1",
            side_effect=AssertionError("an exact committed D2 CAS must be adopted"),
        ) as append:
            adopted = self._run()

        append.assert_not_called()
        self.assertTrue(adopted.terminal_for_effective_key)
        self.assertEqual(adopted.key_id, dependent_intent["key_id"])
        self.assertEqual(tuple(self.fixture.fixture.live_fixture.events()), committed)
        self.assertEqual(self._counts(paths), (1, 1, 1))
        self._assert_bytes_unchanged(parents_before)

    def test_receipt_only_crash_appends_only_the_consumption_event(self) -> None:
        dependent_intent = self._publish_d2()
        paths = self._paths()
        parents_before = self._parent_authority_bytes()

        def crash(stage: str) -> None:
            if stage == "after_receipt":
                raise RuntimeError("synthetic dependent-consumption receipt-only crash")

        with self.assertRaisesRegex(
            consumption.SourceDerivedDependentOutcomeConsumptionIntegrityError,
            "receipt-only crash",
        ):
            self._run(fault_hook=crash)

        committed = tuple(self.fixture.fixture.live_fixture.events())
        self.assertEqual(len(committed), 3)
        self.assertEqual(self._counts(paths), (1, 1, 0))

        def forbidden_consume(*_args: object, **_kwargs: object) -> object:
            raise AssertionError("receipt-only healing must not consume again")

        with mock.patch.object(
            recovery.live_cas,
            "append_transaction_at_pre_head_v1",
            side_effect=AssertionError("receipt-only healing must not append"),
        ) as append:
            healed = self._run(consume_action=forbidden_consume)

        append.assert_not_called()
        self.assertTrue(healed.terminal_for_effective_key)
        self.assertEqual(healed.key_id, dependent_intent["key_id"])
        self.assertEqual(tuple(self.fixture.fixture.live_fixture.events()), committed)
        self.assertEqual(self._counts(paths), (1, 1, 1))
        self._assert_bytes_unchanged(parents_before)

    def test_dependent_dispatch_receipt_without_event_cannot_authorize_d2(
        self,
    ) -> None:
        self.fixture._materialize_and_consume_d1()  # noqa: SLF001
        paths = self._paths()

        def crash(stage: str) -> None:
            if stage == "after_receipt":
                raise RuntimeError("synthetic dependent-dispatch receipt-only crash")

        with self.assertRaisesRegex(
            dispatch_test.dependent_dispatch.SourceDerivedDependentOutcomeDispatchIntegrityError,
            "receipt-only crash",
        ):
            self.fixture._run(fault_hook=crash)  # noqa: SLF001

        dispatch_paths = self.fixture._paths()  # noqa: SLF001
        self.assertEqual(
            self.fixture._counts(dispatch_paths),  # noqa: SLF001
            (1, 1, 0),
        )
        parent_before = self._parent_authority_bytes()
        live_before = tuple(self.fixture.fixture.live_fixture.events())
        self.assertEqual(len(live_before), 2)
        self.assertEqual(len(self.fixture._immutable_materializer_receipts()), 2)  # noqa: SLF001

        result = self._run()

        self.assertIn("waiting", result.status)
        self.assertFalse(result.outcome_or_revision_consumed)
        self.assertFalse(result.terminal_for_effective_key)
        self.assertEqual(self._counts(paths), (0, 0, 0))
        self.assertEqual(tuple(self.fixture.fixture.live_fixture.events()), live_before)
        self.assertEqual(
            self.fixture._counts(dispatch_paths),  # noqa: SLF001
            (1, 1, 0),
        )
        self._assert_bytes_unchanged(parent_before)

    def test_dependent_prerequisite_busy_remains_machine_retryable(self) -> None:
        paths = self._paths()
        dependent_dispatch = dispatch_test.dependent_dispatch
        busy_error = dependent_dispatch.SourceDerivedDependentOutcomeDispatchBusyError(
            "synthetic dependent prerequisite busy"
        )
        with (
            mock.patch.object(
                dependent_dispatch,
                "_deep_replay_prerequisites",
                side_effect=busy_error,
            ),
            self.assertRaisesRegex(
                consumption.SourceDerivedDependentOutcomeConsumptionBusyError,
                "dependent prerequisite busy",
            ),
        ):
            self._run()

        self.assertEqual(self._counts(paths), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
