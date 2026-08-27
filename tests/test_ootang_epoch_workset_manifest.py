"""Fast contracts for bounded closed-workset reservation publication."""

from __future__ import annotations

from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_epoch_drain as drain  # noqa: E402
from monitoring import ootang_epoch_drain_v2 as drain_v2  # noqa: E402
from monitoring import ootang_epoch_workset_inventory as inventory  # noqa: E402
from monitoring import ootang_epoch_workset_manifest as manifest  # noqa: E402


FIXED_NOW = datetime(2030, 1, 2, 12, 0, tzinfo=timezone.utc)


class WorksetManifestSyntheticTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(
            prefix="ootang-workset-manifest-", dir=ROOT
        )
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self._use_namespace("default")

    def _use_namespace(self, name: str) -> None:
        self.registry_root = self.base / name / "registry"
        self.active_root = self.base / name / "active"
        self.shadow_root = self.base / name / "shadow"
        self.registry_root.mkdir(parents=True)
        self.active_root.mkdir(parents=True)
        self.shadow_root.mkdir(parents=True)

    @staticmethod
    def _context() -> drain_v2.WorksetContext:
        return drain_v2.WorksetContext(
            registry_event_sequence_id=7,
            registry_event_entry_sha256="1" * 64,
            preparation_event_sequence_id=3,
            preparation_event_entry_sha256="2" * 64,
            candidate_id="candidate-a",
            slot_id="slot-a",
            old_live_epoch_id="old-epoch-a",
            live_event_count=11,
            live_terminal_sha256="3" * 64,
        )

    def _paths(self) -> manifest.WorksetManifestPaths:
        return manifest.workset_manifest_paths(
            manifest.load_workset_manifest_profile(),
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
        )

    def _write(self, root: Path, relative: str, payload: object) -> Path:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(manifest._canonical_bytes(payload))  # noqa: SLF001
        return path

    def _binding(self) -> manifest.AdmissionCutBinding:
        paths = self._paths()
        event_path = self._write(
            self.registry_root,
            "admission_cut_v1/events/cut.json",
            {"kind": "synthetic-cut"},
        )
        attempt_path = self._write(
            self.registry_root,
            "admission_cut_v1/attempts/attempt.json",
            {"kind": "synthetic-attempt"},
        )
        return manifest.AdmissionCutBinding(
            context=self._context(),
            event=manifest._artifact(  # noqa: SLF001
                event_path,
                role="admission_cut_event",
                root_label="registry",
                paths=paths,
                maximum_bytes=manifest.MAX_CONTROL_BYTES,
            ),
            latest_attempt=manifest._artifact(  # noqa: SLF001
                attempt_path,
                role="admission_cut_latest_attempt",
                root_label="registry",
                paths=paths,
                maximum_bytes=manifest.MAX_CONTROL_BYTES,
            ),
            recorded_at_utc=manifest._utc_text(FIXED_NOW),  # noqa: SLF001
        )

    def _inspection(self) -> manifest.WorksetInspection:
        paths = self._paths()
        items: list[manifest.WorksetItem] = []
        for index, family in enumerate(manifest.FAMILIES):
            root_label = "shadow" if family == "shadow" else "active"
            root = self.shadow_root if root_label == "shadow" else self.active_root
            artifact_path = self._write(
                root,
                f"inventory/{family}.json",
                {"family": family, "index": index},
            )
            artifact = manifest._artifact(  # noqa: SLF001
                artifact_path,
                role=f"{family}_obligation",
                root_label=root_label,
                paths=paths,
                maximum_bytes=manifest.MAX_CONTROL_BYTES,
            )
            items.append(
                manifest._make_item(  # noqa: SLF001
                    family, f"{family}:old-epoch-a:key-{index}", (artifact,)
                )
            )
        families = tuple(
            manifest._make_family(  # noqa: SLF001
                family,
                tuple(
                    artifact
                    for item in items
                    if item.family == family
                    for artifact in item.artifacts
                ),
                record_count=1,
                actionable_count=1,
                authority={"terminal_and_pending_records_replayed": True},
            )
            for family in manifest.FAMILIES
        )
        return manifest.WorksetInspection(self._context(), tuple(items), families)

    def _run(
        self,
        inspection: manifest.WorksetInspection | None = None,
        *,
        binding: manifest.AdmissionCutBinding | None = None,
        now: datetime = FIXED_NOW,
    ) -> manifest.WorksetManifestResult:
        selected_binding = binding or self._binding()
        selected_inspection = inspection or self._inspection()
        return manifest._coordinate_epoch_workset_manifest(  # noqa: SLF001
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
            clock=lambda: now,
            inspect_admission_cut=lambda _paths: selected_binding,
            inspect_workset=lambda _paths, _binding, _now: selected_inspection,
        )

    def test_six_families_publish_one_exact_reservation_and_replay_idempotently(
        self,
    ) -> None:
        binding = self._binding()
        inspection = self._inspection()
        first = self._run(inspection, binding=binding)
        self.assertEqual(first.status, "closed_workset_reserved")
        self.assertTrue(first.complete_workset_enumeration)
        self.assertTrue(first.bounded_workset_reservation_implemented)
        event_raw = first.event_path.read_bytes()  # type: ignore[union-attr]
        manifest_raw = first.manifest_path.read_bytes()  # type: ignore[union-attr]
        payload = json.loads(manifest_raw)
        self.assertEqual(
            [item["family"] for item in payload["families"]], list(manifest.FAMILIES)
        )
        self.assertEqual(payload["family_count"], 6)
        self.assertEqual(payload["item_count"], 6)
        self.assertTrue(payload["complete_workset_enumeration"])
        self.assertFalse(payload["bounded_workset_recovery_implemented"])

        second = self._run(
            inspection, binding=binding, now=FIXED_NOW + timedelta(minutes=1)
        )
        self.assertEqual(second.status, "closed_workset_reservation_idempotent")
        self.assertEqual(second.event_path.read_bytes(), event_raw)  # type: ignore[union-attr]
        self.assertEqual(second.manifest_path.read_bytes(), manifest_raw)  # type: ignore[union-attr]

        predecessor = inspection.items[0].artifacts[0]
        predecessor_path = (
            self.shadow_root if predecessor.root == "shadow" else self.active_root
        ) / predecessor.path
        predecessor_path.write_bytes(b'{"legitimate_successor":true}\n')
        replayed = manifest._coordinate_epoch_workset_manifest(  # noqa: SLF001
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
            clock=lambda: FIXED_NOW + timedelta(minutes=2),
            inspect_admission_cut=lambda _paths: binding,
            inspect_workset=lambda *_args: self.fail(
                "immutable reservation replay must not recapture predecessors"
            ),
        )
        self.assertEqual(replayed.status, "closed_workset_reservation_idempotent")
        self.assertEqual(replayed.event_path.read_bytes(), event_raw)  # type: ignore[union-attr]
        self.assertEqual(replayed.manifest_path.read_bytes(), manifest_raw)  # type: ignore[union-attr]

    def test_exact_orphan_manifest_resumes_forward_but_mismatch_fails_closed(
        self,
    ) -> None:
        binding = self._binding()
        inspection = self._inspection()
        profile = manifest.load_workset_manifest_profile()
        paths = self._paths()
        items = manifest._normalize_inspection(profile, paths, inspection)  # noqa: SLF001
        raw = manifest._canonical_bytes(  # noqa: SLF001
            manifest._manifest_payload(  # noqa: SLF001
                profile, binding, items, inspection.families
            )
        )
        digest = hashlib.sha256(raw).hexdigest()
        drain._publish_once_durable(  # noqa: SLF001
            paths.manifests / f"{digest}.json",
            raw,
            root=paths.root,
            name="synthetic crash manifest",
        )
        result = self._run(inspection, binding=binding)
        self.assertEqual(result.status, "closed_workset_reserved")
        self.assertTrue(result.event_path.is_file())  # type: ignore[union-attr]

        self._use_namespace("mismatch")
        binding = self._binding()
        inspection = self._inspection()
        paths = self._paths()
        orphan = paths.manifests / f"{'a' * 64}.json"
        self._write(paths.manifests, orphan.name, {"wrong": True})
        with self.assertRaisesRegex(
            manifest.WorksetManifestIntegrityError, "Orphan manifest"
        ):
            self._run(inspection, binding=binding)
        self.assertFalse(paths.events.exists())

    def test_combined_unknown_duplicate_missing_dependency_and_overflow_fail_closed(
        self,
    ) -> None:
        for name in ("unknown", "duplicate", "missing", "overflow"):
            with self.subTest(name=name):
                self._use_namespace(name)
                binding = self._binding()
                clean = self._inspection()
                if name == "unknown":
                    first = clean.items[0]
                    broken = manifest.WorksetItem(
                        "unknown",
                        "unknown:key",
                        first.canonical_successor_state,
                        (),
                        first.artifacts,
                        first.authority,
                        first.namespace_digest,
                    )
                    inspection = manifest.WorksetInspection(
                        clean.context, (broken,), clean.families
                    )
                elif name == "duplicate":
                    inspection = manifest.WorksetInspection(
                        clean.context, (clean.items[0], clean.items[0]), clean.families
                    )
                elif name == "missing":
                    first = clean.items[0]
                    broken = manifest.WorksetItem(
                        first.family,
                        first.natural_key,
                        first.canonical_successor_state,
                        ("guard:absent",),
                        first.artifacts,
                        first.authority,
                        first.namespace_digest,
                    )
                    inspection = manifest.WorksetInspection(
                        clean.context, (broken,), clean.families
                    )
                else:
                    first = clean.items[0]
                    values = tuple(
                        manifest.WorksetItem(
                            first.family,
                            f"{first.family}:overflow:{index}",
                            first.canonical_successor_state,
                            (),
                            first.artifacts,
                            first.authority,
                            first.namespace_digest,
                        )
                        for index in range(4097)
                    )
                    inspection = manifest.WorksetInspection(
                        clean.context, values, clean.families
                    )
                with self.assertRaises(manifest.WorksetManifestIntegrityError):
                    self._run(inspection, binding=binding)
                paths = self._paths()
                self.assertFalse(paths.events.exists())
                self.assertFalse(paths.manifests.exists())

    def test_tamper_and_clock_rollback_are_blocked_before_new_authority(self) -> None:
        binding = self._binding()
        inspection = self._inspection()
        result = self._run(inspection, binding=binding)
        result.manifest_path.write_bytes(b"{}\n")  # type: ignore[union-attr]
        with self.assertRaises(manifest.WorksetManifestIntegrityError):
            self._run(inspection, binding=binding)

        self._use_namespace("rollback")
        binding = self._binding()
        inspection = self._inspection()
        with self.assertRaisesRegex(
            manifest.WorksetManifestIntegrityError, "predate admission cut"
        ):
            self._run(
                inspection,
                binding=binding,
                now=FIXED_NOW - timedelta(seconds=1),
            )
        paths = self._paths()
        self.assertFalse(paths.events.exists())
        self.assertFalse(paths.manifests.exists())

    def test_cut_missing_and_busy_write_no_reservation_authority(self) -> None:
        paths = self._paths()
        result = manifest._coordinate_epoch_workset_manifest(  # noqa: SLF001
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
            clock=lambda: FIXED_NOW,
            inspect_admission_cut=lambda _paths: None,
            inspect_workset=lambda *_args: self.fail("workset must not be inspected"),
        )
        self.assertEqual(result.status, "waiting_for_admission_cut")
        self.assertFalse(result.complete_workset_enumeration)
        self.assertFalse(result.bounded_workset_reservation_implemented)
        self.assertFalse(paths.events.exists())
        self.assertFalse(paths.manifests.exists())
        status = json.loads(paths.status.read_bytes())
        self.assertFalse(status["complete_workset_enumeration"])
        self.assertFalse(status["bounded_workset_reservation_implemented"])
        self.assertEqual(status["event_observation"], "absent")

        self._use_namespace("busy")
        paths = self._paths()
        handle = drain._acquire_lock(paths.manager_lock, label="test")  # noqa: SLF001
        try:
            with self.assertRaises(manifest.WorksetManifestBusyError):
                self._run()
        finally:
            drain._release_locks([handle])  # noqa: SLF001
        self.assertFalse(paths.events.exists())
        self.assertFalse(paths.manifests.exists())

    def test_profile_and_cli_keep_machine_only_boundary(self) -> None:
        profile = manifest.load_workset_manifest_profile()
        self.assertEqual(
            hashlib.sha256(manifest.DEFAULT_CONFIG_PATH.read_bytes()).hexdigest(),
            manifest.DEFAULT_CONFIG_SHA256,
        )
        self.assertEqual(
            profile["protocol"]["workset_families"], list(manifest.FAMILIES)
        )
        self.assertTrue(
            profile["engineering_capabilities"]["complete_workset_enumeration"]
        )
        self.assertFalse(profile["engineering_capabilities"]["lifecycle_authority"])
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            manifest._parse_args(["--freeze", "2030-01-01"])  # noqa: SLF001
        parser_source = Path(manifest.__file__).read_text(encoding="utf-8")
        for forbidden in ("--force", "--approve", "--cleanup", "--backdate"):
            self.assertNotIn(f'add_argument("{forbidden}"', parser_source)

    def test_default_inventory_adapter_preserves_the_frozen_context(self) -> None:
        expected = self._inspection()
        helper_result = inventory.InventoryResult(
            context_epoch_id=expected.context.old_live_epoch_id,
            frozen_live_event_count=expected.context.live_event_count,
            frozen_live_terminal_sha256=expected.context.live_terminal_sha256,
            items=tuple(
                inventory.InventoryItem(**vars(item)) for item in expected.items
            ),
            families=tuple(
                inventory.FamilyInventory(**vars(item)) for item in expected.families
            ),
        )
        with mock.patch.object(
            inventory, "inspect_closed_workset", return_value=helper_result
        ):
            adapted = manifest._default_inspection(  # noqa: SLF001
                self._paths(), self._binding(), FIXED_NOW
            )
        self.assertEqual(adapted, expected)


if __name__ == "__main__":
    unittest.main()
