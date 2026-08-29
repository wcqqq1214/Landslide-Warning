"""Fast contracts for the source-derived effective-workset overlay."""

from __future__ import annotations

from datetime import date, timedelta
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

from monitoring import ootang_epoch_source_ingest_cross_freeze as cross  # noqa: E402
from monitoring import ootang_epoch_source_derived_workset_overlay as overlay  # noqa: E402
from monitoring import ootang_epoch_workset_inventory as inventory  # noqa: E402
from monitoring import ootang_epoch_workset_manifest as manifest  # noqa: E402
from monitoring import ootang_live_source as source  # noqa: E402
from tests import (  # noqa: E402
    test_ootang_epoch_source_ingest_cross_freeze as cross_test,
)
from tests import (  # noqa: E402
    test_ootang_epoch_source_ingest_derived_reservation as derived_test,
)


NOW = cross_test.NOW + timedelta(seconds=1)


class EpochSourceDerivedWorksetOverlayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = derived_test.EpochSourceIngestDerivedReservationTests(
            "test_seq2_not_published_waits_without_reservation"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.binding: manifest.AdmissionCutBinding | None = None
        self.frozen_prefix: object | None = None

    def _load_derived(self, _paths: object, _now: object) -> object:
        assert self.binding is not None
        return self.fixture._load_authority(  # noqa: SLF001
            self.binding,
            frozen_prefix=self.frozen_prefix,
        )

    def _ensure_derived(self, *_args: object, **_kwargs: object) -> object:
        authority = self._load_derived(None, None)
        self.assertIsNotNone(authority)
        return self.fixture._coordinate(authority)  # noqa: SLF001

    def _complete_cross_freeze(
        self,
        values: tuple[inventory.InventoryItem, ...],
        *,
        frozen_prefix: object | None = None,
    ) -> cross.SourceIngestCrossFreezeResult:
        self.binding = self.fixture._publish_manifest(values)  # noqa: SLF001
        self.frozen_prefix = frozen_prefix
        with (
            mock.patch.object(
                manifest, "_default_admission_cut_binding", return_value=self.binding
            ),
            mock.patch.object(
                source,
                "ingest_source",
                side_effect=AssertionError(
                    "cross-freeze completion must use its private machine writer"
                ),
            ),
        ):
            result = cross._coordinate_source_ingest_cross_freeze(  # noqa: SLF001
                registry_root=self.fixture.manifest_fixture.registry_root,
                active_root=self.fixture.source_fixture.root,
                shadow_root=self.fixture.manifest_fixture.shadow_root,
                clock=lambda: cross_test.NOW,
                ensure_derived=self._ensure_derived,
                load_derived_authority=self._load_derived,
            )
        self.assertEqual(result.status, "source_ingest_cross_freeze_completed")
        return result

    def _paths(self) -> overlay.SourceDerivedWorksetOverlayPaths:
        return overlay.source_derived_workset_overlay_paths(
            overlay.load_source_derived_workset_overlay_profile(),
            registry_root=self.fixture.manifest_fixture.registry_root,
            active_root=self.fixture.source_fixture.root,
            shadow_root=self.fixture.manifest_fixture.shadow_root,
        )

    def _run(
        self, *, fault_hook: object | None = None
    ) -> overlay.SourceDerivedWorksetOverlayResult:
        assert self.binding is not None
        with (
            mock.patch.object(
                manifest, "_default_admission_cut_binding", return_value=self.binding
            ),
            mock.patch.object(
                source,
                "_recover_current_pointer_from_snapshot_registry",
                side_effect=AssertionError(
                    "effective overlay must never repair the public source pointer"
                ),
            ),
        ):
            return overlay._coordinate_source_derived_workset_overlay(  # noqa: SLF001
                registry_root=self.fixture.manifest_fixture.registry_root,
                active_root=self.fixture.source_fixture.root,
                shadow_root=self.fixture.manifest_fixture.shadow_root,
                clock=lambda: NOW,
                fault_hook=fault_hook,
                load_derived_authority=self._load_derived,
            )

    def _upstream_bytes(
        self, paths: overlay.SourceDerivedWorksetOverlayPaths
    ) -> dict[Path, bytes]:
        excluded = paths.root.resolve()
        roots = {
            self.fixture.manifest_fixture.registry_root.resolve(),
            self.fixture.source_fixture.root.resolve(),
            self.fixture.manifest_fixture.shadow_root.resolve(),
        }
        return {
            path: path.read_bytes()
            for root in roots
            for path in root.rglob("*")
            if path.is_file()
            and path.resolve() != excluded
            and excluded not in path.resolve().parents
        }

    def test_d_items_publish_one_compact_effective_workset_without_upstream_writes(
        self,
    ) -> None:
        self._complete_cross_freeze((self.fixture.source_item,))
        paths = self._paths()
        before = self._upstream_bytes(paths)

        result = self._run()

        self.assertEqual(result.status, "source_derived_workset_overlay_published")
        self.assertEqual(result.base_item_count, 1)
        self.assertEqual(result.effective_item_count, 3)
        self.assertEqual(result.derived_added_count, 2)
        self.assertEqual(result.rebound_replaced_count, 0)
        self.assertEqual(result.invalidated_superseded_count, 0)
        self.assertIsNotNone(result.overlay_path)
        self.assertIsNotNone(result.event_path)
        assert result.overlay_path is not None
        assert result.event_path is not None
        payload = json.loads(result.overlay_path.read_bytes())
        self.assertNotIn("effective_items", payload)
        self.assertFalse(payload["effective_rows_embedded"])
        self.assertEqual(payload["effective_workset"]["item_count"], 3)
        self.assertEqual(
            payload["effective_workset"]["natural_keyset_sha256"],
            result.effective_workset_keyset_sha256,
        )
        self.assertEqual(
            payload["effective_workset"]["dependency_graph_sha256"],
            result.effective_dependency_graph_sha256,
        )
        self.assertEqual(
            payload["effective_workset"]["item_identity_set_sha256"],
            result.effective_item_identity_set_sha256,
        )
        event = json.loads(result.event_path.read_bytes())
        self.assertEqual(
            event["effective_item_identity_set_sha256"],
            result.effective_item_identity_set_sha256,
        )
        for name in overlay.TRUE_CAPABILITIES:
            self.assertTrue(payload[name], name)
        for name in overlay.FALSE_CLAIMS:
            self.assertFalse(payload[name], name)
        raw = result.overlay_path.read_bytes()
        self.assertEqual(
            result.overlay_path.name, f"{hashlib.sha256(raw).hexdigest()}.json"
        )
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_r_replaces_the_whole_item_and_changes_its_frozen_manifest_key_id(
        self,
    ) -> None:
        frozen = self.fixture._baseline_machine_outcome_item()  # noqa: SLF001
        self._complete_cross_freeze(
            (frozen, self.fixture.source_item),
            frozen_prefix=self.fixture._frozen_prefix(  # noqa: SLF001
                last_finalized=date(2020, 6, 30)
            ),
        )

        result = self._run()

        self.assertEqual(result.base_item_count, 2)
        self.assertEqual(result.effective_item_count, 4)
        self.assertEqual(result.derived_added_count, 2)
        self.assertEqual(result.rebound_replaced_count, 1)
        self.assertEqual(result.invalidated_superseded_count, 0)
        authority = self._load_derived(None, None)
        rebound = authority.rebound_items[0]
        manifest_sha = authority.reservation.manifest_snapshot.sha256
        self.assertNotEqual(
            recovery_key_id(manifest_sha, frozen.natural_key, frozen.namespace_digest),
            recovery_key_id(
                manifest_sha,
                rebound["natural_key"],
                rebound["namespace_digest"],
            ),
        )

    def test_i_is_tombstoned_and_removed_from_the_effective_count(self) -> None:
        payload = self.fixture.source_fixture.payload(count=3)
        payload["records"][0]["revision_id"] = "source-revision-1b"
        self.fixture.source_fixture.write(payload)
        self.fixture.source_item = self.fixture._source_ingest_item(  # noqa: SLF001
            expected_changed=(
                ("2020-07-01", "source-revision-1b"),
                ("2020-07-02", "source-revision-2"),
                ("2020-07-03", "source-revision-3"),
            )
        )
        frozen = self.fixture._baseline_machine_outcome_item()  # noqa: SLF001
        self._complete_cross_freeze((frozen, self.fixture.source_item))

        result = self._run()

        self.assertEqual(result.base_item_count, 2)
        self.assertEqual(result.effective_item_count, 4)
        self.assertEqual(result.derived_added_count, 3)
        self.assertEqual(result.rebound_replaced_count, 0)
        self.assertEqual(result.invalidated_superseded_count, 1)
        assert result.overlay_path is not None
        application = json.loads(result.overlay_path.read_bytes())["application"]
        self.assertEqual(application["invalidated_superseded_count"], 1)
        self.assertEqual(application["effective_item_count"], 4)

    def test_surviving_base_dependency_on_i_fails_closed_without_overlay(self) -> None:
        payload = self.fixture.source_fixture.payload(count=3)
        payload["records"][0]["revision_id"] = "source-revision-1b"
        self.fixture.source_fixture.write(payload)
        self.fixture.source_item = self.fixture._source_ingest_item(  # noqa: SLF001
            expected_changed=(
                ("2020-07-01", "source-revision-1b"),
                ("2020-07-02", "source-revision-2"),
                ("2020-07-03", "source-revision-3"),
            )
        )
        frozen = self.fixture._baseline_machine_outcome_item()  # noqa: SLF001
        dependent = self.fixture._anchor_repair_item()  # noqa: SLF001
        dependent = inventory._item(  # noqa: SLF001
            dependent.family,
            dependent.natural_key,
            dependent.canonical_successor_state,
            dependent.artifacts,
            dependent.authority,
            dependency_keys=(frozen.natural_key,),
        )
        self._complete_cross_freeze((dependent, frozen, self.fixture.source_item))
        paths = self._paths()

        with self.assertRaisesRegex(
            overlay.SourceDerivedWorksetOverlayIntegrityError,
            "(?i:dependency|invalidated|unknown)",
        ):
            self._run()

        self.assertEqual(tuple(paths.overlay_objects.glob("*.json")), ())
        self.assertEqual(tuple(paths.events.glob("*.json")), ())

    def test_object_only_crash_forward_adopts_only_the_event(self) -> None:
        self._complete_cross_freeze((self.fixture.source_item,))
        paths = self._paths()

        def crash(stage: str) -> None:
            if stage == "after_overlay_object":
                raise RuntimeError("synthetic overlay object-only crash")

        with self.assertRaisesRegex(RuntimeError, "object-only"):
            self._run(fault_hook=crash)

        objects = tuple(paths.overlay_objects.glob("*.json"))
        self.assertEqual(len(objects), 1)
        object_before = objects[0].read_bytes()
        self.assertEqual(tuple(paths.events.glob("*.json")), ())
        upstream_before = self._upstream_bytes(paths)

        result = self._run()

        self.assertEqual(
            result.status, "source_derived_workset_overlay_event_forward_adopted"
        )
        self.assertEqual(objects[0].read_bytes(), object_before)
        self.assertEqual(
            {path: path.read_bytes() for path in upstream_before}, upstream_before
        )
        self.assertEqual(len(tuple(paths.overlay_objects.glob("*.json"))), 1)
        self.assertEqual(len(tuple(paths.events.glob("*.json"))), 1)

    def test_pointer_loss_after_gate_fails_without_cross_freeze_repair(self) -> None:
        self._complete_cross_freeze((self.fixture.source_item,))
        paths = self._paths()
        pointer = self.fixture.source_fixture.pointer_path
        original_gate = overlay._assert_public_pointer_is_registry_tip  # noqa: SLF001

        def remove_after_gate(value: overlay.SourceDerivedWorksetOverlayPaths) -> None:
            original_gate(value)
            pointer.unlink()

        assert self.binding is not None
        with (
            mock.patch.object(
                manifest, "_default_admission_cut_binding", return_value=self.binding
            ),
            mock.patch.object(
                overlay,
                "_assert_public_pointer_is_registry_tip",
                side_effect=remove_after_gate,
            ),
            mock.patch.object(
                source, "_recover_current_pointer_from_snapshot_registry"
            ) as recover,
            self.assertRaisesRegex(
                overlay.SourceDerivedWorksetOverlayIntegrityError,
                "(?i:pointer|current source|read-only cross-freeze)",
            ),
        ):
            overlay._coordinate_source_derived_workset_overlay(  # noqa: SLF001
                registry_root=self.fixture.manifest_fixture.registry_root,
                active_root=self.fixture.source_fixture.root,
                shadow_root=self.fixture.manifest_fixture.shadow_root,
                clock=lambda: NOW,
                load_derived_authority=self._load_derived,
            )

        self.assertEqual(recover.call_count, 0)
        self.assertFalse(pointer.exists())
        self.assertEqual(tuple(paths.overlay_objects.glob("*.json")), ())
        self.assertEqual(tuple(paths.events.glob("*.json")), ())


def recovery_key_id(manifest_sha: str, natural_key: str, namespace: str) -> str:
    """Mirror the public identity formula without constructing a fake reservation."""

    raw = (
        json.dumps(
            {
                "manifest_sha256": manifest_sha,
                "namespace_digest": namespace,
                "natural_key": natural_key,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
    return hashlib.sha256(raw).hexdigest()


if __name__ == "__main__":
    unittest.main()
