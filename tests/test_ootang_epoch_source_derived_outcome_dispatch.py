"""Focused contracts for historical source-derived outcome dispatch."""

from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import (  # noqa: E402
    ootang_epoch_source_derived_outcome_dispatch as dispatch,
)
from monitoring import ootang_epoch_workset_manifest as manifest  # noqa: E402
from monitoring import ootang_epoch_workset_recovery as recovery  # noqa: E402
from monitoring import ootang_live_source as source  # noqa: E402
from monitoring import ootang_outcome_materializer as outcomes  # noqa: E402
from monitoring import ootang_prequential_live as live  # noqa: E402
from tests import (  # noqa: E402
    test_ootang_epoch_source_derived_workset_overlay as overlay_test,
)


NOW = overlay_test.NOW + timedelta(seconds=1)


class SourceDerivedOutcomeDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = overlay_test.EpochSourceDerivedWorksetOverlayTests(
            "test_d_items_publish_one_compact_effective_workset_without_upstream_writes"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    @staticmethod
    def _frozen_context() -> SimpleNamespace:
        """Supply only the real materializer consumer fields this synthetic cut lacks."""

        return SimpleNamespace(
            profile=live.load_config(),
            prerequisites=SimpleNamespace(
                source=SimpleNamespace(outcome_source_id="ootang-machine-source-v1")
            ),
            projection=SimpleNamespace(epoch_id="old-epoch-a"),
            frozen_events=(),
            current_events=(),
        )

    def _paths(self) -> dispatch.SourceDerivedOutcomeDispatchPaths:
        return dispatch.source_derived_outcome_dispatch_paths(
            dispatch.load_source_derived_outcome_dispatch_profile(),
            registry_root=self.fixture.fixture.manifest_fixture.registry_root,
            active_root=self.fixture.fixture.source_fixture.root,
            shadow_root=self.fixture.fixture.manifest_fixture.shadow_root,
        )

    def _run(
        self, *, fault_hook: object | None = None
    ) -> dispatch.SourceDerivedOutcomeDispatchResult:
        assert self.fixture.binding is not None
        with (
            mock.patch.object(
                manifest,
                "_default_admission_cut_binding",
                return_value=self.fixture.binding,
            ),
            mock.patch.object(
                recovery,
                "_frozen_live_prefix",
                return_value=self._frozen_context(),
            ),
            mock.patch.object(
                outcomes,
                "_select_target",
                side_effect=AssertionError(
                    "historical dispatch must not run the current-source selector"
                ),
            ),
            mock.patch.object(
                outcomes,
                "materialize_outcome",
                side_effect=AssertionError(
                    "historical dispatch must use the exact internal materializer action"
                ),
            ),
        ):
            return dispatch._coordinate_source_derived_outcome_dispatch(  # noqa: SLF001
                registry_root=self.fixture.fixture.manifest_fixture.registry_root,
                active_root=self.fixture.fixture.source_fixture.root,
                shadow_root=self.fixture.fixture.manifest_fixture.shadow_root,
                clock=lambda: NOW,
                fault_hook=fault_hook,
                load_derived_authority=self.fixture._load_derived,  # noqa: SLF001
            )

    def _authority_bytes(
        self, paths: dispatch.SourceDerivedOutcomeDispatchPaths
    ) -> dict[Path, bytes]:
        excluded = paths.root.resolve()
        result = {
            path: path.read_bytes()
            for path in paths.recovery.root.rglob("*")
            if path.is_file() and excluded not in path.resolve().parents
        }
        pointer = self.fixture.fixture.source_fixture.pointer_path
        result[pointer] = pointer.read_bytes()
        return result

    @staticmethod
    def _only_payload(directory: Path) -> dict[str, object]:
        entries = tuple(directory.glob("*.json"))
        if len(entries) != 1:
            raise AssertionError(f"expected one JSON record in {directory}: {entries}")
        return json.loads(entries[0].read_bytes())

    @staticmethod
    def _contract_input(intent: dict[str, object]) -> dict[str, object]:
        contract = intent["materialization_contract"]
        assert isinstance(contract, dict)
        artifact = contract["input_manifest"]
        assert isinstance(artifact, dict)
        return json.loads(Path(str(artifact["path"])).read_bytes())

    def test_first_d_materializes_historical_n_plus_one_without_upstream_writes(
        self,
    ) -> None:
        self.fixture._complete_cross_freeze(  # noqa: SLF001
            (self.fixture.fixture.source_item,)
        )
        self.fixture._run()  # noqa: SLF001
        paths = self._paths()
        before = self._authority_bytes(paths)

        result = self._run()

        self.assertTrue(result.outcome_materialization_performed)
        self.assertFalse(result.terminal_for_effective_key)
        self.assertFalse(result.terminal_for_recovery_v6_key)
        self.assertEqual(len(tuple(paths.intents.glob("*.json"))), 1)
        self.assertEqual(len(tuple(paths.receipts.glob("*.json"))), 1)
        self.assertEqual(len(tuple(paths.events.glob("*.json"))), 1)
        intent = self._only_payload(paths.intents)
        receipt = self._only_payload(paths.receipts)
        self.assertEqual(intent["dri_kind"], "D")
        self.assertEqual(
            intent["materialization_contract"]["target_date"], "2020-07-02"
        )
        self.assertEqual(
            intent["materialization_contract"]["source_snapshot_sequence_id"], 2
        )
        self.assertEqual(receipt["next_action"], "outcome_or_revision_consumed")
        self.assertFalse(receipt["terminal_for_effective_key"])
        self.assertFalse(receipt["terminal_for_recovery_v6_key"])
        input_payload = self._contract_input(intent)
        snapshot = input_payload["source"]["snapshot_receipt"]
        snapshot_payload = json.loads(Path(snapshot["path"]).read_bytes())
        self.assertEqual(snapshot_payload["snapshot_sequence_id"], 2)
        self.assertEqual(
            {path: path.read_bytes() for path in before},
            before,
        )

        replay = self._run()

        self.assertEqual(
            replay.status, "source_derived_materialization_dispatch_current"
        )
        self.assertFalse(replay.outcome_materialization_performed)
        self.assertEqual(len(tuple(paths.intents.glob("*.json"))), 1)
        self.assertEqual(len(tuple(paths.receipts.glob("*.json"))), 1)
        self.assertEqual(len(tuple(paths.events.glob("*.json"))), 1)

    def test_public_n_plus_two_tip_does_not_replace_historical_n_plus_one(self) -> None:
        self.fixture._complete_cross_freeze(  # noqa: SLF001
            (self.fixture.fixture.source_item,)
        )
        self.fixture._run()  # noqa: SLF001
        self.fixture.fixture.source_fixture.write(
            self.fixture.fixture.source_fixture.payload(count=4)
        )
        advanced = source.ingest_source(
            self.fixture.fixture.source_fixture.profile,
            runtime_root=self.fixture.fixture.source_fixture.root,
            now=NOW,
        )
        self.assertIsNotNone(advanced.source)
        assert advanced.source is not None
        self.assertEqual(advanced.source.snapshot_sequence_id, 3)
        pointer = self.fixture.fixture.source_fixture.pointer_path
        pointer_before = pointer.read_bytes()

        result = self._run()

        self.assertTrue(result.outcome_materialization_performed)
        intent = self._only_payload(self._paths().intents)
        contract = intent["materialization_contract"]
        self.assertEqual(contract["source_snapshot_sequence_id"], 2)
        input_payload = self._contract_input(intent)
        snapshot = input_payload["source"]["snapshot_receipt"]
        snapshot_payload = json.loads(Path(snapshot["path"]).read_bytes())
        self.assertEqual(snapshot_payload["snapshot_sequence_id"], 2)
        self.assertEqual(pointer.read_bytes(), pointer_before)
        current = source.load_current_source(
            self.fixture.fixture.source_fixture.profile,
            runtime_root=self.fixture.fixture.source_fixture.root,
        )
        self.assertEqual(current.snapshot_sequence_id, 3)

    def test_materializer_commit_crash_is_adopted_without_second_publication(
        self,
    ) -> None:
        self.fixture._complete_cross_freeze(  # noqa: SLF001
            (self.fixture.fixture.source_item,)
        )
        self.fixture._run()  # noqa: SLF001
        paths = self._paths()

        def crash(stage: str) -> None:
            if stage == "after_materializer_action":
                raise RuntimeError("synthetic post-materializer crash")

        with self.assertRaisesRegex(
            dispatch.SourceDerivedOutcomeDispatchIntegrityError,
            "post-materializer crash",
        ):
            self._run(fault_hook=crash)

        self.assertEqual(len(tuple(paths.intents.glob("*.json"))), 1)
        self.assertEqual(tuple(paths.receipts.glob("*.json")), ())
        self.assertEqual(tuple(paths.events.glob("*.json")), ())
        materializer_profile = outcomes.load_config()
        receipt_root = outcomes._runtime_path(  # noqa: SLF001
            materializer_profile,
            self.fixture.fixture.source_fixture.root,
            "outcome_receipts",
        )
        immutable_receipts = tuple(
            path for path in receipt_root.rglob("*.json") if path.name != "active.json"
        )
        self.assertEqual(len(immutable_receipts), 1)
        committed_before = immutable_receipts[0].read_bytes()

        with mock.patch.object(
            outcomes,
            "_publish_candidate",
            side_effect=AssertionError(
                "an exact committed materializer receipt must be adopted"
            ),
        ) as publish:
            result = self._run()

        publish.assert_not_called()
        self.assertTrue(result.outcome_materialization_performed)
        self.assertEqual(immutable_receipts[0].read_bytes(), committed_before)
        self.assertEqual(len(tuple(paths.intents.glob("*.json"))), 1)
        self.assertEqual(len(tuple(paths.receipts.glob("*.json"))), 1)
        self.assertEqual(len(tuple(paths.events.glob("*.json"))), 1)

    def test_r_uses_the_new_effective_key_id(self) -> None:
        frozen = self.fixture.fixture._baseline_machine_outcome_item()  # noqa: SLF001
        self.fixture._complete_cross_freeze(  # noqa: SLF001
            (frozen, self.fixture.fixture.source_item),
            frozen_prefix=self.fixture.fixture._frozen_prefix(  # noqa: SLF001
                last_finalized=date(2020, 6, 30)
            ),
        )
        self.fixture._run()  # noqa: SLF001
        authority = self.fixture._load_derived(None, None)  # noqa: SLF001
        rebound = authority.rebound_items[0]
        old_key_id = overlay_test.recovery_key_id(
            authority.reservation.manifest_snapshot.sha256,
            frozen.natural_key,
            frozen.namespace_digest,
        )
        new_key_id = overlay_test.recovery_key_id(
            authority.reservation.manifest_snapshot.sha256,
            rebound["natural_key"],
            rebound["namespace_digest"],
        )

        result = self._run()

        self.assertNotEqual(result.key_id, old_key_id)
        self.assertEqual(result.key_id, new_key_id)
        intent = self._only_payload(self._paths().intents)
        self.assertEqual(intent["dri_kind"], "R")
        self.assertEqual(intent["key_id"], new_key_id)

    def test_missing_overlay_event_waits_without_a_dispatch_intent(self) -> None:
        self.fixture._complete_cross_freeze(  # noqa: SLF001
            (self.fixture.fixture.source_item,)
        )
        paths = self._paths()

        result = self._run()

        self.assertIn("waiting", result.status)
        self.assertFalse(result.outcome_materialization_performed)
        self.assertEqual(tuple(paths.intents.glob("*.json")), ())
        self.assertEqual(tuple(paths.receipts.glob("*.json")), ())
        self.assertEqual(tuple(paths.events.glob("*.json")), ())


if __name__ == "__main__":
    unittest.main()
