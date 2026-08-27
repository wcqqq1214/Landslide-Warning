"""Fast Darwin contracts for the machine-only official-writer lock-path cut."""

from __future__ import annotations

from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_epoch_admission_cut as cut  # noqa: E402
from monitoring import ootang_epoch_drain as drain  # noqa: E402
from monitoring import ootang_epoch_drain_v2 as drain_v2  # noqa: E402


FIXED_NOW = datetime(2030, 1, 2, 12, 0, tzinfo=timezone.utc)


class AdmissionCutSyntheticTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(
            prefix="ootang-admission-cut-", dir=ROOT
        )
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self._use_namespace("default")

    def _use_namespace(self, name: str) -> None:
        self.registry_root = self.base / name / "registry"
        self.active_root = self.base / name / "active"
        self.shadow_root = self.base / name / "shadow"

    @staticmethod
    def _context(**changes: object) -> drain_v2.WorksetContext:
        values: dict[str, object] = {
            "registry_event_sequence_id": 7,
            "registry_event_entry_sha256": "1" * 64,
            "preparation_event_sequence_id": 3,
            "preparation_event_entry_sha256": "2" * 64,
            "candidate_id": "candidate-a",
            "slot_id": "slot-a",
            "old_live_epoch_id": "old-epoch-a",
            "live_event_count": 11,
            "live_terminal_sha256": "3" * 64,
        }
        values.update(changes)
        return drain_v2.WorksetContext(**values)  # type: ignore[arg-type]

    def _paths(self) -> cut.AdmissionCutPaths:
        return cut.admission_cut_paths(
            cut.load_admission_cut_profile(),
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
        )

    def _seed_v2(self, context: drain_v2.WorksetContext | None = None) -> None:
        selected = context or self._context()
        inspection = drain_v2.WorksetInspection(
            "dirty_workset_observed",
            "synthetic pending old work",
            (
                drain_v2.WorksetItem(
                    "guard",
                    "guard:2030-01-01",
                    "waiting_for_pending_guard",
                    "existing intent",
                ),
            ),
            selected,
        )
        result = drain_v2._coordinate_epoch_drain_v2(  # noqa: SLF001
            runtime_root=self.registry_root,
            active_runtime_root=self.active_root,
            shadow_runtime_root=self.shadow_root,
            clock=lambda: FIXED_NOW,
            inspect_workset=lambda _paths, _now: inspection,
        )
        self.assertEqual(result.status, "first_blocker_observed")

    def _run(
        self,
        *,
        fault_hook=None,
        exchange=None,
        context: drain_v2.WorksetContext | None = None,
        now: datetime = FIXED_NOW,
    ) -> cut.AdmissionCutResult:
        kwargs = {
            "runtime_root": self.registry_root,
            "active_runtime_root": self.active_root,
            "shadow_runtime_root": self.shadow_root,
            "clock": lambda: now,
            "inspect_context": lambda _paths, _now: context or self._context(),
            "fault_hook": fault_hook,
        }
        if exchange is not None:
            kwargs["exchange"] = exchange
        return cut._coordinate_epoch_admission_cut(**kwargs)  # type: ignore[arg-type]  # noqa: SLF001

    @staticmethod
    def _snapshot(*paths: Path) -> dict[Path, tuple[int, bytes, int]]:
        return {
            path: (os.stat(path).st_ino, path.read_bytes(), os.stat(path).st_mode)
            for path in paths
            if path.exists()
        }

    @staticmethod
    def _crash_at(target: str):
        def hook(point: str) -> None:
            if point == target:
                raise RuntimeError(f"synthetic-crash:{target}")

        return hook

    def test_profile_hashes_and_negative_capability_boundary_are_exact(self) -> None:
        profile = cut.load_admission_cut_profile()

        self.assertEqual(
            hashlib.sha256(cut.DEFAULT_CONFIG_PATH.read_bytes()).hexdigest(),
            cut.DEFAULT_CONFIG_SHA256,
        )
        self.assertEqual(profile["protocol"]["lock_order"], list(cut.LOCK_ORDER))
        self.assertEqual(profile["protocol"]["cut_order"], list(cut.CUT_ORDER))
        self.assertEqual(profile["protocol"]["sentinel_mode"], 0o444)
        self.assertIn("RENAME_SWAP", profile["protocol"]["atomic_exchange"])
        for capability in cut.TRUE_CAPABILITIES:
            self.assertTrue(profile["engineering_capabilities"][capability])
        for claim in cut.FALSE_CLAIMS:
            self.assertFalse(profile["engineering_capabilities"][claim], claim)
        self.assertEqual(profile["frozen_writers"], cut.EXPECTED_FROZEN_WRITERS)
        with mock.patch.object(cut, "DEFAULT_CONFIG_SHA256", "0" * 64):
            with self.assertRaises(cut.AdmissionCutConfigError):
                cut.load_admission_cut_profile()

    def _assert_physical_cut(self, result: cut.AdmissionCutResult) -> None:
        paths = self._paths()
        self.assertTrue(result.deploy_cut)
        self.assertTrue(result.runner_cut)
        self.assertTrue(result.event_path.is_file())
        for path in (paths.deploy_lock, paths.runner_lock):
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o444)
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                self.assertEqual(
                    drain._read_fence_acl(descriptor),  # noqa: SLF001
                    drain.FENCE_ACL_TEXT,
                )
            finally:
                os.close(descriptor)
            cut._probe_write_open_denied(  # noqa: SLF001
                path, name="test admission sentinel"
            )
        self.assertTrue(paths.deploy_archive.is_file())
        self.assertTrue(paths.runner_archive.is_file())

    def test_real_darwin_swap_cuts_both_lock_paths_and_repoll_is_idempotent(
        self,
    ) -> None:
        self._seed_v2()
        paths = self._paths()
        original = self._snapshot(paths.deploy_lock, paths.runner_lock)

        first = self._run()
        first_bytes = {
            "prepare": paths.prepare.read_bytes(),
            "intent": paths.intent.read_bytes(),
            "attempts": tuple(path.read_bytes() for path in paths.attempts.iterdir()),
            "event": first.event_path.read_bytes(),
        }
        second = self._run()

        self._assert_physical_cut(first)
        self._assert_physical_cut(second)
        self.assertEqual(first.event_path, second.event_path)
        self.assertEqual(paths.prepare.read_bytes(), first_bytes["prepare"])
        self.assertEqual(paths.intent.read_bytes(), first_bytes["intent"])
        self.assertEqual(
            tuple(path.read_bytes() for path in paths.attempts.iterdir()),
            first_bytes["attempts"],
        )
        self.assertEqual(first.event_path.read_bytes(), first_bytes["event"])
        self.assertEqual(
            os.stat(paths.deploy_archive).st_ino, original[paths.deploy_lock][0]
        )
        self.assertEqual(
            os.stat(paths.runner_archive).st_ino, original[paths.runner_lock][0]
        )

        event = json.loads(first.event_path.read_bytes())
        attempt_paths = sorted(paths.attempts.iterdir())
        self.assertEqual(len(attempt_paths), 2)
        latest_attempt = json.loads(attempt_paths[-1].read_bytes())
        self.assertEqual(latest_attempt["physical_state_before"], "deploy_cut")
        self.assertEqual(
            event["latest_attempt"]["path"], f"attempts/{attempt_paths[-1].name}"
        )
        status = json.loads(paths.status.read_bytes())
        for document in (event, status):
            for claim in cut.FALSE_CLAIMS:
                self.assertFalse(document[claim], claim)

    def test_every_transaction_crash_point_recovers_forward(self) -> None:
        points = (
            "after_prepare",
            "after_sentinels",
            "after_intent",
            "after_attempt",
            "after_deploy_swap",
            "after_runner_swap",
            "after_event",
        )
        for index, point in enumerate(points):
            with self.subTest(point=point):
                self._use_namespace(f"crash-{index}")
                self._seed_v2()
                with self.assertRaises(cut.AdmissionCutIntegrityError):
                    self._run(fault_hook=self._crash_at(point))

                recovered = self._run()
                self._assert_physical_cut(recovered)
                paths = self._paths()
                self.assertEqual(len(tuple(paths.events.iterdir())), 1)
                self.assertEqual(len(tuple(paths.attempts.iterdir())), 2)
                replayed = self._run()
                self.assertEqual(replayed.event_path, recovered.event_path)

    def test_busy_and_preexisting_v1_authority_leave_lock_paths_open(self) -> None:
        for index, lock_name in enumerate(("manager_lock", "runner_lock")):
            self._use_namespace(f"busy-{index}")
            self._seed_v2()
            paths = self._paths()
            before = self._snapshot(paths.deploy_lock, paths.runner_lock)
            handle = drain._acquire_lock(  # noqa: SLF001
                getattr(paths, lock_name), label=lock_name
            )
            try:
                with self.assertRaises(cut.AdmissionCutBusyError):
                    self._run()
            finally:
                drain._release_locks([handle])  # noqa: SLF001
            self.assertEqual(
                self._snapshot(paths.deploy_lock, paths.runner_lock), before
            )
            self.assertFalse(paths.prepare.exists())
            self.assertFalse(paths.events.exists())

        self._use_namespace("v1-precedence")
        self._seed_v2()
        paths = self._paths()
        before = self._snapshot(paths.deploy_lock, paths.runner_lock)
        witness = self.registry_root / "drain_events" / "synthetic.json"
        witness.parent.mkdir(parents=True)
        witness.write_bytes(b'{"v1":"opaque-authority"}\n')
        witness_before = witness.read_bytes()
        result = self._run()
        self.assertIn("v1", result.status)
        self.assertEqual(self._snapshot(paths.deploy_lock, paths.runner_lock), before)
        self.assertEqual(witness.read_bytes(), witness_before)
        self.assertFalse(paths.prepare.exists())
        self.assertFalse(paths.events.exists())

    def test_prepare_intent_and_sentinel_tamper_fail_closed(self) -> None:
        cases = (
            ("after_prepare", "prepare"),
            ("after_sentinels", "sentinel"),
            ("after_intent", "intent"),
        )
        for index, (point, target) in enumerate(cases):
            with self.subTest(target=target):
                self._use_namespace(f"tamper-{index}")
                self._seed_v2()
                with self.assertRaises(cut.AdmissionCutIntegrityError):
                    self._run(fault_hook=self._crash_at(point))
                paths = self._paths()
                if target == "prepare":
                    paths.prepare.write_bytes(paths.prepare.read_bytes() + b" ")
                elif target == "intent":
                    paths.intent.write_bytes(paths.intent.read_bytes() + b" ")
                else:
                    os.chmod(paths.deploy_archive, 0o600)
                with self.assertRaises(cut.AdmissionCutIntegrityError):
                    self._run()
                self.assertFalse(paths.events.exists())

    def test_attempt_event_tamper_and_post_deploy_context_extension(self) -> None:
        self._use_namespace("tamper-attempt")
        self._seed_v2()
        with self.assertRaises(cut.AdmissionCutIntegrityError):
            self._run(fault_hook=self._crash_at("after_attempt"))
        paths = self._paths()
        attempt_path = next(paths.attempts.iterdir())
        attempt_path.write_bytes(attempt_path.read_bytes() + b" ")
        with self.assertRaises(cut.AdmissionCutIntegrityError):
            self._run()
        self.assertFalse(paths.events.exists())

        self._use_namespace("tamper-event")
        self._seed_v2()
        completed = self._run()
        event_path = completed.event_path
        self.assertIsNotNone(event_path)
        assert event_path is not None
        event_path.write_bytes(event_path.read_bytes() + b" ")
        with self.assertRaises(cut.AdmissionCutIntegrityError):
            self._run()

        self._use_namespace("post-deploy-context")
        original_context = self._context()
        self._seed_v2(original_context)
        with self.assertRaises(cut.AdmissionCutIntegrityError):
            self._run(
                context=original_context,
                fault_hook=self._crash_at("after_deploy_swap"),
            )
        current_context = self._context(
            live_event_count=12,
            live_terminal_sha256="4" * 64,
        )
        recovered = self._run(context=current_context)
        paths = self._paths()
        attempt_paths = sorted(paths.attempts.iterdir())
        self.assertEqual(len(attempt_paths), 2)
        first_attempt = json.loads(attempt_paths[0].read_bytes())
        second_attempt = json.loads(attempt_paths[1].read_bytes())
        self.assertEqual(first_attempt["physical_state_before"], "open")
        self.assertEqual(second_attempt["physical_state_before"], "deploy_cut")
        self.assertEqual(
            second_attempt["previous_entry_sha256"], first_attempt["entry_sha256"]
        )
        self.assertEqual(second_attempt["authority_context"]["live_event_count"], 12)
        self.assertEqual(
            second_attempt["authority_context"]["live_terminal_sha256"],
            "4" * 64,
        )
        recovered_event_path = recovered.event_path
        self.assertIsNotNone(recovered_event_path)
        assert recovered_event_path is not None
        event = json.loads(recovered_event_path.read_bytes())
        second_bytes = attempt_paths[1].read_bytes()
        self.assertEqual(
            event["latest_attempt"],
            {
                "path": f"attempts/{attempt_paths[1].name}",
                "sha256": hashlib.sha256(second_bytes).hexdigest(),
                "size_bytes": len(second_bytes),
            },
        )

    def test_machine_clock_rollback_cannot_publish_next_child_record(self) -> None:
        rolled_back = FIXED_NOW - timedelta(seconds=1)
        resumed_at = FIXED_NOW + timedelta(seconds=1)
        cases = (
            ("after_prepare", "intent"),
            ("after_intent", "attempt"),
            ("after_runner_swap", "event"),
        )
        for index, (point, child) in enumerate(cases):
            with self.subTest(child=child):
                self._use_namespace(f"clock-rollback-{index}")
                self._seed_v2()
                with self.assertRaises(cut.AdmissionCutIntegrityError):
                    self._run(fault_hook=self._crash_at(point))
                paths = self._paths()
                attempts_before = (
                    tuple(path.read_bytes() for path in paths.attempts.iterdir())
                    if paths.attempts.exists()
                    else ()
                )

                with self.assertRaises(cut.AdmissionCutIntegrityError):
                    self._run(now=rolled_back)

                if child == "intent":
                    self.assertFalse(paths.intent.exists())
                    self.assertFalse(paths.attempts.exists())
                    self.assertFalse(paths.events.exists())
                elif child == "attempt":
                    self.assertFalse(paths.attempts.exists())
                    self.assertFalse(paths.events.exists())
                else:
                    self.assertEqual(len(attempts_before), 2)
                    self.assertFalse(paths.events.exists())
                    self.assertEqual(
                        tuple(path.read_bytes() for path in paths.attempts.iterdir()),
                        attempts_before,
                    )

                recovered = self._run(now=resumed_at)
                self._assert_physical_cut(recovered)
                self.assertEqual(len(tuple(paths.attempts.iterdir())), 2)
                self.assertEqual(len(tuple(paths.events.iterdir())), 1)

    def test_public_surface_has_no_human_cut_controls(self) -> None:
        signature = inspect.signature(cut.coordinate_epoch_admission_cut)
        self.assertEqual(list(signature.parameters), ["config_path"])
        for forbidden in (
            "--date",
            "--freeze",
            "--approval",
            "--cleanup",
            "--force",
            "--backdate",
            "--restore",
            "--unfence",
        ):
            with self.assertRaises(SystemExit):
                with redirect_stderr(io.StringIO()):
                    cut._parse_args([forbidden])  # noqa: SLF001
        parsed = cut._parse_args(  # noqa: SLF001
            ["--config", str(cut.DEFAULT_CONFIG_PATH)]
        )
        self.assertEqual(parsed.config, cut.DEFAULT_CONFIG_PATH)


if __name__ == "__main__":
    unittest.main()
