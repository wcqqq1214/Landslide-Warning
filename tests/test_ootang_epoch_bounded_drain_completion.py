"""Fast contracts for the scoped V2 bounded-drain completion boundary."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import replace
import hashlib
import inspect
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

from monitoring import ootang_epoch_bounded_drain_completion as completion  # noqa: E402
from monitoring import ootang_epoch_drain_v2 as drain_v2  # noqa: E402
from monitoring import ootang_epoch_registry as registry  # noqa: E402
from monitoring import ootang_epoch_workset_manifest as manifest  # noqa: E402


NOW = datetime(2030, 1, 2, 12, 0, tzinfo=timezone.utc)


class BoundedDrainCompletionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(
            prefix="ootang-bounded-drain-completion-", dir=ROOT
        )
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.registry_root = self.base / "registry"
        self.active_root = self.base / "active"
        self.shadow_root = self.base / "shadow"
        for root in (self.registry_root, self.active_root, self.shadow_root):
            root.mkdir(parents=True)

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

    @staticmethod
    def _write(root: Path, relative: str, payload: object) -> Path:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(registry._canonical_bytes(payload))  # noqa: SLF001
        return path

    @staticmethod
    def _snapshot(path: Path) -> registry.ArtifactSnapshot:
        raw = path.read_bytes()
        return registry.ArtifactSnapshot(
            path=path,
            raw=raw,
            sha256=hashlib.sha256(raw).hexdigest(),
            size_bytes=len(raw),
        )

    def _paths(self) -> completion.BoundedDrainCompletionPaths:
        return completion.bounded_drain_completion_paths(
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
        )

    def _cut(self) -> completion._ValidatedCut:  # noqa: SLF001
        context = self._context()
        manifest_paths = manifest.workset_manifest_paths(
            manifest.load_workset_manifest_profile(),
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
        )
        cut_event = self._write(
            self.registry_root,
            "admission_cut_v1/events/cut.json",
            {"kind": "synthetic-cut"},
        )
        cut_attempt = self._write(
            self.registry_root,
            "admission_cut_v1/attempts/attempt.json",
            {"kind": "synthetic-attempt"},
        )
        binding = manifest.AdmissionCutBinding(
            context=context,
            event=manifest._artifact(  # noqa: SLF001
                cut_event,
                role="admission_cut_event",
                root_label="registry",
                paths=manifest_paths,
                maximum_bytes=manifest.MAX_CONTROL_BYTES,
            ),
            latest_attempt=manifest._artifact(  # noqa: SLF001
                cut_attempt,
                role="admission_cut_latest_attempt",
                root_label="registry",
                paths=manifest_paths,
                maximum_bytes=manifest.MAX_CONTROL_BYTES,
            ),
            recorded_at_utc=manifest._utc_text(NOW),  # noqa: SLF001
        )
        proof = self._write(
            self.registry_root,
            "workset_recovery_v1/source_derived_bounded_terminal_closure_v1/"
            "proofs/proof.json",
            {"kind": "synthetic-bounded-closure-proof"},
        )
        event = self._write(
            self.registry_root,
            "workset_recovery_v1/source_derived_bounded_terminal_closure_v1/"
            "events/event.json",
            {"kind": "synthetic-bounded-closure-event"},
        )
        return completion._ValidatedCut(  # noqa: SLF001
            context=context,
            manifest_paths=manifest_paths,
            binding=binding,
            closure_proof=self._snapshot(proof),
            closure_event=self._snapshot(event),
        )

    @staticmethod
    def _inspection(
        cut: completion._ValidatedCut,  # noqa: SLF001
        *,
        context: drain_v2.WorksetContext | None = None,
        actionable: bool = False,
        prefix_matches: bool = True,
    ) -> manifest.WorksetInspection:
        inspected_context = context or cut.context
        live_hashes = [
            f"{index + 5:064x}" for index in range(inspected_context.live_event_count)
        ]
        live_hashes[cut.context.live_event_count - 1] = (
            cut.context.live_terminal_sha256 if prefix_matches else "f" * 64
        )
        live_hashes[-1] = inspected_context.live_terminal_sha256
        items: tuple[manifest.WorksetItem, ...] = ()
        actionable_artifacts: tuple[manifest.ArtifactObligation, ...] = ()
        if actionable:
            artifact_path = BoundedDrainCompletionTests._write(
                cut.manifest_paths.active_root,
                "inventory/pending-guard.json",
                {"pending": True},
            )
            artifact = manifest._artifact(  # noqa: SLF001
                artifact_path,
                role="pending_guard",
                root_label="active",
                paths=cut.manifest_paths,
                maximum_bytes=manifest.MAX_CONTROL_BYTES,
            )
            actionable_artifacts = (artifact,)
            items = (
                manifest._make_item(  # noqa: SLF001
                    "guard", "guard:2030-01-01", actionable_artifacts
                ),
            )
        families = tuple(
            manifest._make_family(  # noqa: SLF001
                family,
                actionable_artifacts if family == "guard" else (),
                record_count=1 if actionable and family == "guard" else 0,
                actionable_count=1 if actionable and family == "guard" else 0,
                authority={
                    "synthetic_complete_inventory": True,
                    **(
                        {
                            "frozen_live_logical_chain": {
                                "schema": "ootang_prequential_live_logical_chain_v1",
                                "epoch_id": inspected_context.old_live_epoch_id,
                                "event_count": inspected_context.live_event_count,
                                "terminal_entry_sha256": (
                                    inspected_context.live_terminal_sha256
                                ),
                                "ordered_entry_sha256s": live_hashes,
                            }
                        }
                        if family == "shadow"
                        else {}
                    ),
                },
            )
            for family in manifest.FAMILIES
        )
        return manifest.WorksetInspection(inspected_context, items, families)

    def _run(self, **kwargs: object) -> completion.BoundedDrainCompletionResult:
        handle = object()
        with (
            mock.patch.object(
                completion.closure.current.coverage,
                "_acquire_locks",
                return_value=(handle,),
            ) as acquire,
            mock.patch.object(
                completion.closure.current.coverage, "_release_locks"
            ) as release,
        ):
            try:
                return completion._coordinate_epoch_bounded_drain_completion(  # noqa: SLF001
                    runtime_root=self.registry_root,
                    active_runtime_root=self.active_root,
                    shadow_runtime_root=self.shadow_root,
                    **kwargs,
                )
            finally:
                acquire.assert_called_once()
                release.assert_called_once_with((handle,))

    def test_empty_fresh_capture_publishes_scoped_event_and_replays_bytes(self) -> None:
        cut = self._cut()
        current = replace(
            cut.context,
            live_event_count=cut.context.live_event_count + 5,
            live_terminal_sha256="4" * 64,
        )
        inspection = self._inspection(cut, context=current)

        first = self._run(
            clock=lambda: NOW,
            load_closure=lambda _paths, _now: cut,
            load_current_context=lambda _paths, _cut, _now: current,
            inspect_fresh_workset=lambda _paths, _binding, _now: inspection,
        )

        self.assertEqual(first.status, "bounded_official_workset_drained")
        self.assertTrue(first.bounded_official_workset_drained)
        self.assertEqual(
            first.status_path,
            self.registry_root
            / "workset_recovery_v1/bounded_drain_completion_v1/status.json",
        )
        self.assertIsNotNone(first.event_path)
        event_path = first.event_path
        assert event_path is not None
        event_raw = event_path.read_bytes()
        event = json.loads(event_raw)
        self.assertEqual(event["branch"], "v2_non_clean_recovery")
        self.assertEqual(event["authority_scope"], "official_machine_reserved_workset")
        self.assertTrue(event["machine_only"])
        self.assertTrue(event["bounded_official_workset_drained"])
        self.assertEqual(event["frozen_live_event_count"], cut.context.live_event_count)
        self.assertEqual(event["fresh_live_event_count"], current.live_event_count)
        for claim in (
            "old_epoch_drained",
            "canonical_old_issue_route_fence_implemented",
            "direct_filesystem_writer_fence_implemented",
            "active_epoch_switch_implemented",
            "lifecycle_authority",
        ):
            self.assertFalse(event[claim], claim)
        self.assertFalse(json.loads(first.status_path.read_bytes())["cache_authority"])

        replay = self._run(
            clock=lambda: NOW + timedelta(hours=1),
            load_closure=lambda _paths, _now: cut,
            load_current_context=lambda _paths, _cut, _now: current,
            inspect_fresh_workset=lambda _paths, _binding, _now: inspection,
        )

        self.assertEqual(replay.status, "bounded_official_workset_drain_current")
        self.assertEqual(replay.event_path, event_path)
        self.assertEqual(event_path.read_bytes(), event_raw)
        self.assertFalse(json.loads(replay.status_path.read_bytes())["cache_authority"])

    def test_actionable_fresh_capture_waits_without_event(self) -> None:
        cut = self._cut()
        current = replace(
            cut.context,
            live_event_count=cut.context.live_event_count + 1,
            live_terminal_sha256="4" * 64,
        )
        inspection = self._inspection(cut, context=current, actionable=True)

        result = self._run(
            clock=lambda: NOW,
            load_closure=lambda _paths, _now: cut,
            load_current_context=lambda _paths, _cut, _now: current,
            inspect_fresh_workset=lambda _paths, _binding, _now: inspection,
        )

        self.assertEqual(result.status, "waiting_for_fresh_workset_drain")
        self.assertFalse(result.bounded_official_workset_drained)
        self.assertIsNone(result.event_path)
        status = json.loads(result.status_path.read_bytes())
        self.assertEqual(status["actionable_item_count"], 1)
        self.assertFalse(status["bounded_official_workset_drained"])
        self.assertFalse(status["cache_authority"])
        self.assertEqual(tuple(self._paths().events.glob("*.json")), ())

    def test_fresh_capture_rejects_a_longer_forked_live_chain(self) -> None:
        cut = self._cut()
        current = replace(
            cut.context,
            live_event_count=cut.context.live_event_count + 1,
            live_terminal_sha256="4" * 64,
        )
        forked = self._inspection(cut, context=current, prefix_matches=False)

        with self.assertRaisesRegex(
            completion.BoundedDrainCompletionIntegrityError,
            "does not extend the frozen admission-cut prefix",
        ):
            self._run(
                clock=lambda: NOW,
                load_closure=lambda _paths, _now: cut,
                load_current_context=lambda _paths, _cut, _now: current,
                inspect_fresh_workset=lambda _paths, _binding, _now: forked,
            )

    def test_published_event_rejects_reappearing_actionable_work(self) -> None:
        cut = self._cut()
        current = replace(
            cut.context,
            live_event_count=cut.context.live_event_count + 1,
            live_terminal_sha256="4" * 64,
        )
        empty = self._inspection(cut, context=current)

        published = self._run(
            clock=lambda: NOW,
            load_closure=lambda _paths, _now: cut,
            load_current_context=lambda _paths, _cut, _now: current,
            inspect_fresh_workset=lambda _paths, _binding, _now: empty,
        )

        self.assertTrue(published.bounded_official_workset_drained)
        actionable = self._inspection(cut, context=current, actionable=True)
        with self.assertRaisesRegex(
            completion.BoundedDrainCompletionIntegrityError,
            "coexists with fresh actionable work",
        ):
            self._run(
                clock=lambda: NOW + timedelta(minutes=1),
                load_closure=lambda _paths, _now: cut,
                load_current_context=lambda _paths, _cut, _now: current,
                inspect_fresh_workset=lambda _paths, _binding, _now: actionable,
            )
        self.assertEqual(
            tuple(
                inspect.signature(
                    completion.coordinate_epoch_bounded_drain_completion
                ).parameters
            ),
            (),
        )


if __name__ == "__main__":
    unittest.main()
