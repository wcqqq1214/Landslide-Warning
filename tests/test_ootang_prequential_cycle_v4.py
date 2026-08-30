"""Focused contracts for the scoped scheduler cycle-v4 adapter."""

from __future__ import annotations

from contextlib import contextmanager, redirect_stderr, redirect_stdout
import hashlib
import inspect
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in os.sys.path:
    os.sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_prequential_cycle_v4 as cycle  # noqa: E402


class AuthorizedCycleV4Tests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[SimpleNamespace, Path]:
        tree = (root / "tree").resolve()
        script = tree / cycle.CYCLE_SCRIPT_RELATIVE
        config = tree / cycle.CYCLE_CONFIG_RELATIVE
        script.parent.mkdir(parents=True)
        config.parent.mkdir(parents=True)
        script.write_text("# frozen cycle v3\n", encoding="utf-8")
        config.write_text("{}\n", encoding="utf-8")
        live = (root / "live").resolve()
        shadow = (root / "shadow").resolve()
        live.mkdir()
        shadow.mkdir()
        transition_root = (root / "registry" / "active_transition_v1").resolve()
        event_path = transition_root / "events" / "00000000000000000001-event.json"
        event_path.parent.mkdir(parents=True)
        event_path.write_text("{}\n", encoding="utf-8")
        adapter_sha256 = hashlib.sha256(Path(cycle.__file__).read_bytes()).hexdigest()
        authorization = SimpleNamespace(
            transition_entry_sha256="a" * 64,
            transition_entry_path=event_path,
            candidate_id="candidate-1",
            slot_id="slot-1",
            old_live_epoch_id="old-epoch",
            new_live_epoch_id="new-epoch",
            runtime_root=live,
            shadow_runtime_root=shadow,
            executable_tree_root=tree,
            cycle_script=script,
            cycle_config=config,
            scheduler_adapter_sha256=adapter_sha256,
        )
        return authorization, transition_root

    def test_exact_authorized_argv_runs_inside_manager_lease(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cycle-v4-success-") as raw:
            authorization, transition_root = self._fixture(Path(raw))
            state = {"inside": False, "exited": False}
            observed: list[list[str]] = []

            @contextmanager
            def lease():
                state["inside"] = True
                try:
                    yield authorization
                finally:
                    state["inside"] = False
                    state["exited"] = True

            def runner(argv):
                self.assertTrue(state["inside"])
                self.assertFalse(state["exited"])
                observed.append(list(argv))
                return SimpleNamespace(returncode=0)

            with (
                mock.patch.object(
                    cycle.transition,
                    "active_transition_paths",
                    return_value=SimpleNamespace(
                        root=transition_root,
                        registry_root=transition_root.parent,
                    ),
                ),
                mock.patch.object(
                    cycle.transition,
                    "scheduler_authorization_lease",
                    side_effect=lease,
                ),
            ):
                result = cycle._run_authorized_cycle_v4(runner=runner)

            self.assertTrue(state["exited"])
            self.assertEqual(
                observed,
                [
                    [
                        os.sys.executable,
                        str(authorization.cycle_script),
                        "--config",
                        str(authorization.cycle_config),
                        "--runtime-root",
                        str(authorization.runtime_root),
                        "--shadow-runtime-root",
                        str(authorization.shadow_runtime_root),
                    ]
                ],
            )
            self.assertEqual(result.status, "child_completed")
            self.assertEqual(result.child_returncode, 0)
            payload = json.loads(result.status_path.read_bytes())
            self.assertEqual(payload["transition_entry_sha256"], "a" * 64)
            self.assertEqual(payload["runtime_root"], str(authorization.runtime_root))
            self.assertEqual(
                payload["shadow_runtime_root"],
                str(authorization.shadow_runtime_root),
            )
            self.assertFalse(payload["cache_authority"])

    def test_child_busy_and_failure_are_mapped_and_recorded(self) -> None:
        cases = (
            (3, cycle.AuthorizedCycleV4BusyError, "child_busy"),
            (7, cycle.AuthorizedCycleV4IntegrityError, "blocked_integrity"),
        )
        for returncode, expected_error, expected_status in cases:
            with self.subTest(returncode=returncode):
                with tempfile.TemporaryDirectory(prefix="cycle-v4-child-") as raw:
                    authorization, transition_root = self._fixture(Path(raw))

                    @contextmanager
                    def lease():
                        yield authorization

                    with (
                        mock.patch.object(
                            cycle.transition,
                            "active_transition_paths",
                            return_value=SimpleNamespace(
                                root=transition_root,
                                registry_root=transition_root.parent,
                            ),
                        ),
                        mock.patch.object(
                            cycle.transition,
                            "scheduler_authorization_lease",
                            side_effect=lease,
                        ),
                        self.assertRaises(expected_error),
                    ):
                        cycle._run_authorized_cycle_v4(
                            runner=lambda _argv: SimpleNamespace(returncode=returncode)
                        )

                    status_path = (
                        transition_root.parent
                        / cycle.STATUS_NAMESPACE
                        / cycle.STATUS_FILENAME
                    )
                    payload = json.loads(status_path.read_bytes())
                    self.assertEqual(payload["status"], expected_status)
                    self.assertEqual(payload["child_returncode"], returncode)
                    self.assertFalse(payload["cache_authority"])

    def test_no_transition_waits_and_public_cli_has_no_root_override(self) -> None:
        self.assertEqual(
            len(inspect.signature(cycle.run_authorized_cycle_v4).parameters), 0
        )
        cycle._parse_args([])
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cycle._parse_args(["--runtime-root", "/tmp/forbidden"])

        with tempfile.TemporaryDirectory(prefix="cycle-v4-waiting-") as raw:
            transition_root = (
                Path(raw) / "registry" / "active_transition_v1"
            ).resolve()

            @contextmanager
            def not_ready_lease():
                raise cycle.transition.ActiveTransitionNotReadyError(
                    "no transition event"
                )
                yield  # pragma: no cover

            with (
                mock.patch.object(
                    cycle.transition,
                    "active_transition_paths",
                    return_value=SimpleNamespace(
                        root=transition_root,
                        registry_root=transition_root.parent,
                    ),
                ),
                mock.patch.object(
                    cycle.transition,
                    "scheduler_authorization_lease",
                    side_effect=not_ready_lease,
                ),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(cycle.main([]), 0)

            payload = json.loads(
                (
                    transition_root.parent
                    / cycle.STATUS_NAMESPACE
                    / cycle.STATUS_FILENAME
                ).read_bytes()
            )
            self.assertEqual(payload["status"], "waiting_for_active_transition")
            self.assertIsNone(payload["transition_entry_sha256"])
            self.assertFalse(payload["cache_authority"])


if __name__ == "__main__":
    unittest.main()
