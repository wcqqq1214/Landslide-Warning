"""Focused contracts for source-derived effective outcome consumption."""

from __future__ import annotations

from datetime import date, timedelta
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
    ootang_epoch_source_derived_outcome_consumption as consumption,
)
from monitoring import (  # noqa: E402
    ootang_epoch_source_derived_outcome_dispatch as materialization_dispatch,
)
from monitoring import ootang_epoch_workset_manifest as manifest  # noqa: E402
from monitoring import ootang_epoch_workset_recovery as recovery  # noqa: E402
from monitoring import ootang_outcome_materializer as outcomes  # noqa: E402
from monitoring import ootang_prequential_live as live  # noqa: E402
from tests import (  # noqa: E402
    test_ootang_epoch_source_derived_outcome_dispatch as dispatch_test,
)
from tests.test_ootang_prequential_live import _LiveFixture  # noqa: E402


NOW = dispatch_test.NOW + timedelta(seconds=1)
OLD_EPOCH_ID = "old-epoch-a"
MACHINE_SOURCE_ID = "ootang-machine-source-v1"


class SourceDerivedOutcomeConsumptionTests(unittest.TestCase):
    """Exercise the new authority against a real append-only live ledger."""

    def setUp(self) -> None:
        self.dispatch_fixture = dispatch_test.SourceDerivedOutcomeDispatchTests(
            "test_first_d_materializes_historical_n_plus_one_without_upstream_writes"
        )
        self.dispatch_fixture.setUp()
        self.addCleanup(self.dispatch_fixture.doCleanups)
        self.dispatch_fixture.fixture._complete_cross_freeze(  # noqa: SLF001
            (self.dispatch_fixture.fixture.fixture.source_item,)
        )
        self.dispatch_fixture.fixture._run()  # noqa: SLF001

        self.live_fixture = _LiveFixture()
        self.addCleanup(self.live_fixture.close)
        self.live_fixture.install_prerequisites(watermark=date(2020, 7, 1))
        source_payload = json.loads(self.live_fixture.source_manifest.read_bytes())
        source_payload["outcome_source_id"] = MACHINE_SOURCE_ID
        self.live_fixture._write_json(  # noqa: SLF001
            self.live_fixture.source_manifest, source_payload
        )
        with mock.patch.object(live, "_epoch_id", return_value=OLD_EPOCH_ID):
            self.live_fixture.poll()
        self.assertEqual(
            [event.event_type for event in self.live_fixture.events()],
            ["epoch_genesis"],
        )

    def _paths(self) -> consumption.SourceDerivedOutcomeConsumptionPaths:
        return consumption.source_derived_outcome_consumption_paths(
            consumption.load_source_derived_outcome_consumption_profile(),
            registry_root=(
                self.dispatch_fixture.fixture.fixture.manifest_fixture.registry_root
            ),
            active_root=self.dispatch_fixture.fixture.fixture.source_fixture.root,
            shadow_root=(
                self.dispatch_fixture.fixture.fixture.manifest_fixture.shadow_root
            ),
        )

    def _frozen_live_prefix(
        self, _reservation: recovery.Reservation
    ) -> recovery.FrozenLivePrefix:
        prerequisites = live.load_prerequisites(
            self.live_fixture.profile, self.live_fixture.paths
        )
        self.assertIsNotNone(prerequisites)
        assert prerequisites is not None
        current_events = tuple(self.live_fixture.events())
        frozen_events = current_events[:1]
        projection = live._reconstruct_projection(  # noqa: SLF001
            frozen_events, self.live_fixture.profile, prerequisites
        )
        live._reconstruct_projection(  # noqa: SLF001
            current_events, self.live_fixture.profile, prerequisites
        )
        genesis = frozen_events[0]
        return recovery.FrozenLivePrefix(
            profile=self.live_fixture.profile,
            paths=self.live_fixture.paths,
            prerequisites=prerequisites,
            projection=projection,
            frozen_events=frozen_events,
            current_events=current_events,
            expected_pre_head=recovery.live_cas.LiveLedgerPreHeadV1(
                epoch_id=OLD_EPOCH_ID,
                event_count=1,
                sequence_id=genesis.sequence_id,
                entry_sha256=genesis.entry_sha256,
            ),
        )

    def _run(
        self, *, fault_hook: object | None = None
    ) -> consumption.SourceDerivedOutcomeConsumptionResult:
        fixture = self.dispatch_fixture.fixture.fixture
        assert self.dispatch_fixture.fixture.binding is not None
        with (
            mock.patch.object(
                manifest,
                "_default_admission_cut_binding",
                return_value=self.dispatch_fixture.fixture.binding,
            ),
            mock.patch.object(
                recovery,
                "_frozen_live_prefix",
                side_effect=self._frozen_live_prefix,
            ),
            mock.patch.object(live, "_epoch_id", return_value=OLD_EPOCH_ID),
            mock.patch.object(
                outcomes,
                "_select_target",
                side_effect=AssertionError(
                    "effective consumption must not run the current-source selector"
                ),
            ),
            mock.patch.object(
                outcomes,
                "materialize_outcome",
                side_effect=AssertionError(
                    "effective consumption must not republish the outcome"
                ),
            ),
        ):
            return consumption._coordinate_source_derived_outcome_consumption(  # noqa: SLF001
                registry_root=fixture.manifest_fixture.registry_root,
                active_root=fixture.source_fixture.root,
                shadow_root=fixture.manifest_fixture.shadow_root,
                clock=lambda: NOW,
                fault_hook=fault_hook,
                load_derived_authority=(
                    self.dispatch_fixture.fixture._load_derived  # noqa: SLF001
                ),
            )

    def _publish_materialization(self, *, fault_hook: object | None = None) -> None:
        self.dispatch_fixture._run(fault_hook=fault_hook)  # noqa: SLF001

    @staticmethod
    def _only_payload(directory: Path) -> dict[str, object]:
        entries = tuple(directory.glob("*.json"))
        if len(entries) != 1:
            raise AssertionError(f"expected one JSON record in {directory}: {entries}")
        return json.loads(entries[0].read_bytes())

    def test_first_d_fresh_cas_is_terminal_only_for_its_effective_key(self) -> None:
        self._publish_materialization()
        paths = self._paths()

        result = self._run()

        events = tuple(self.live_fixture.events())
        self.assertTrue(result.outcome_or_revision_consumed)
        self.assertTrue(result.terminal_for_effective_key)
        self.assertFalse(result.terminal_for_recovery_v6_key)
        self.assertEqual(
            [event.event_type for event in events],
            ["epoch_genesis", "backfill_not_blind"],
        )
        self.assertEqual(len(tuple(paths.intents.glob("*.json"))), 1)
        self.assertEqual(len(tuple(paths.receipts.glob("*.json"))), 1)
        self.assertEqual(len(tuple(paths.events.glob("*.json"))), 1)
        receipt = self._only_payload(paths.receipts)
        event = self._only_payload(paths.events)
        for payload in (receipt, event):
            self.assertTrue(payload["terminal_for_effective_key"])
            self.assertFalse(payload["terminal_for_recovery_v6_key"])
            self.assertFalse(payload["source_parent_terminal"])
        self.assertEqual(
            receipt["output"]["semantics"]["writer_branch"], "first_backfill"
        )
        self.assertEqual(receipt["output"]["semantics"]["event_count"], 1)

        replay = self._run()

        self.assertEqual(replay.status, "source_derived_outcome_consumption_current")
        self.assertFalse(replay.terminal_for_effective_key)
        self.assertEqual(tuple(self.live_fixture.events()), events)
        self.assertEqual(len(tuple(paths.intents.glob("*.json"))), 1)
        self.assertEqual(len(tuple(paths.receipts.glob("*.json"))), 1)
        self.assertEqual(len(tuple(paths.events.glob("*.json"))), 1)

    def test_post_cas_crash_adopts_without_a_second_live_append(self) -> None:
        self._publish_materialization()
        paths = self._paths()

        def crash(stage: str) -> None:
            if stage == "after_live_ledger_cas":
                raise RuntimeError("synthetic post-CAS crash")

        with self.assertRaisesRegex(
            consumption.SourceDerivedOutcomeConsumptionIntegrityError,
            "post-CAS crash",
        ):
            self._run(fault_hook=crash)

        committed = tuple(self.live_fixture.events())
        self.assertEqual(
            [event.event_type for event in committed],
            ["epoch_genesis", "backfill_not_blind"],
        )
        self.assertEqual(len(tuple(paths.intents.glob("*.json"))), 1)
        self.assertEqual(tuple(paths.receipts.glob("*.json")), ())
        self.assertEqual(tuple(paths.events.glob("*.json")), ())

        with mock.patch.object(
            recovery.live_cas,
            "append_transaction_at_pre_head_v1",
            side_effect=AssertionError("an exact committed CAS must be adopted"),
        ) as append:
            adopted = self._run()

        append.assert_not_called()
        self.assertTrue(adopted.terminal_for_effective_key)
        self.assertEqual(tuple(self.live_fixture.events()), committed)
        self.assertEqual(len(tuple(paths.receipts.glob("*.json"))), 1)
        self.assertEqual(len(tuple(paths.events.glob("*.json"))), 1)

    def test_receipt_only_crash_appends_only_the_missing_control_event(self) -> None:
        self._publish_materialization()
        paths = self._paths()

        def crash(stage: str) -> None:
            if stage == "after_receipt":
                raise RuntimeError("synthetic receipt-only crash")

        with self.assertRaisesRegex(
            consumption.SourceDerivedOutcomeConsumptionIntegrityError,
            "receipt-only crash",
        ):
            self._run(fault_hook=crash)

        committed = tuple(self.live_fixture.events())
        self.assertEqual(len(tuple(paths.receipts.glob("*.json"))), 1)
        self.assertEqual(tuple(paths.events.glob("*.json")), ())
        with mock.patch.object(
            recovery.live_cas,
            "append_transaction_at_pre_head_v1",
            side_effect=AssertionError(
                "receipt-only healing must not touch the ledger"
            ),
        ) as append:
            healed = self._run()

        append.assert_not_called()
        self.assertTrue(healed.terminal_for_effective_key)
        self.assertEqual(tuple(self.live_fixture.events()), committed)
        self.assertEqual(len(tuple(paths.events.glob("*.json"))), 1)

    def test_dispatch_receipt_without_event_does_not_authorize_consumption(
        self,
    ) -> None:
        def crash(stage: str) -> None:
            if stage == "after_receipt":
                raise RuntimeError("synthetic dispatcher receipt-only crash")

        with self.assertRaisesRegex(
            materialization_dispatch.SourceDerivedOutcomeDispatchIntegrityError,
            "dispatcher receipt-only crash",
        ):
            self._publish_materialization(fault_hook=crash)
        dispatcher_paths = self.dispatch_fixture._paths()  # noqa: SLF001
        self.assertEqual(len(tuple(dispatcher_paths.receipts.glob("*.json"))), 1)
        self.assertEqual(tuple(dispatcher_paths.events.glob("*.json")), ())
        before = tuple(self.live_fixture.events())
        paths = self._paths()

        result = self._run()

        self.assertFalse(result.outcome_or_revision_consumed)
        self.assertFalse(result.terminal_for_effective_key)
        self.assertEqual(tuple(self.live_fixture.events()), before)
        self.assertEqual(tuple(paths.intents.glob("*.json")), ())
        self.assertEqual(tuple(paths.receipts.glob("*.json")), ())
        self.assertEqual(tuple(paths.events.glob("*.json")), ())


if __name__ == "__main__":
    unittest.main()
