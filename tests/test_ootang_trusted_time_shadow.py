import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_trusted_time_shadow as launcher


RUNTIME_PROJECT = ROOT / "tools" / "ootang_trusted_time_runtime"


class TrustedTimeLauncherTests(unittest.TestCase):
    def test_uv_version_check_accepts_build_metadata_but_rejects_version_drift(self):
        accepted = SimpleNamespace(
            returncode=0,
            stdout="uv 0.12.5 (Homebrew build metadata)\n",
        )
        with (
            mock.patch.object(launcher.shutil, "which", return_value="/opt/bin/uv"),
            mock.patch.object(launcher.subprocess, "run", return_value=accepted),
        ):
            self.assertEqual(launcher._uv_executable(), "/opt/bin/uv")  # noqa: SLF001

        for result in (
            SimpleNamespace(returncode=0, stdout="uv 0.12.4\n"),
            SimpleNamespace(returncode=1, stdout="uv 0.12.5\n"),
            SimpleNamespace(returncode=0, stdout="unexpected\n"),
        ):
            with (
                self.subTest(result=result),
                mock.patch.object(launcher.shutil, "which", return_value="/opt/bin/uv"),
                mock.patch.object(launcher.subprocess, "run", return_value=result),
                self.assertRaises(RuntimeError),
            ):
                launcher._uv_executable()  # noqa: SLF001

        with (
            mock.patch.object(launcher.shutil, "which", return_value=None),
            self.assertRaises(RuntimeError),
        ):
            launcher._uv_executable()  # noqa: SLF001

    def test_command_uses_only_the_frozen_isolated_runtime(self):
        with mock.patch.object(launcher, "_uv_executable", return_value="/opt/bin/uv"):
            command = launcher.command(["--runtime-root", "/tmp/runtime"])

        self.assertEqual(
            command,
            [
                "/opt/bin/uv",
                "--no-config",
                "run",
                "--project",
                str(RUNTIME_PROJECT),
                "--isolated",
                "--frozen",
                "--python",
                "3.10.20",
                "python",
                "-I",
                str(
                    ROOT / "code" / "monitoring" / "ootang_trusted_time_shadow_core.py"
                ),
                "--runtime-root",
                "/tmp/runtime",
            ],
        )

    def test_launcher_maps_bootstrap_failure_and_forwards_core_exit(self):
        with mock.patch.object(
            launcher, "command", side_effect=RuntimeError("version drift")
        ):
            self.assertEqual(launcher.main([]), 2)

        with (
            mock.patch.object(launcher, "command", return_value=["uv", "run"]),
            mock.patch.object(
                launcher.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=3),
            ) as run,
        ):
            self.assertEqual(launcher.main([]), 3)
        run.assert_called_once_with(
            ["uv", "run"],
            cwd=ROOT,
            env=launcher._child_environment(),  # noqa: SLF001
            check=False,
        )

    def test_child_environment_and_isolated_python_block_module_injection(self):
        hostile = {
            "PYTHONPATH": "/tmp/attacker",
            "PYTHONHOME": "/tmp/fake-python",
            "PYTHONSTARTUP": "/tmp/startup.py",
            "UV_PROJECT_ENVIRONMENT": "/tmp/fake-venv",
            "UV_PYTHON": "/tmp/fake-python",
            "VIRTUAL_ENV": "/tmp/active-venv",
            "SAFE_PROXY": "preserved",
        }
        with mock.patch.dict(launcher.os.environ, hostile, clear=True):
            child = launcher._child_environment()  # noqa: SLF001

        self.assertEqual(child, {"SAFE_PROXY": "preserved"})
        with (
            mock.patch.object(launcher, "_artifact_sha256", return_value="0" * 64),
            self.assertRaises(RuntimeError),
        ):
            launcher.command([])

    def test_frozen_core_contract_suite_passes_in_isolated_runtime(self):
        result = subprocess.run(
            [
                "uv",
                "--no-config",
                "run",
                "--project",
                str(RUNTIME_PROJECT),
                "--isolated",
                "--frozen",
                "--python",
                "3.10.20",
                "python",
                "-I",
                str(ROOT / "tests" / "ootang_trusted_time_core_checks.py"),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_real_launcher_empty_runtime_is_machine_waiting_and_never_eligible(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "code" / "monitoring" / "ootang_trusted_time_shadow.py"),
                    "--runtime-root",
                    tmp_dir,
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=180,
            )
            status_path = Path(tmp_dir) / "trusted_time_shadow_status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(status["runner_status"], "waiting_for_live_prerequisites")
        for key in (
            "rfc3161_receipt_verified",
            "cryptographic_time_shadow_verified",
            "trusted_anchor_receipt_verified",
            "e2_live_evidence_eligible",
            "real_activation_ready",
            "formal_warning_output",
        ):
            self.assertFalse(status[key])

    def test_real_launcher_ignores_hostile_pythonpath_module(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            attacker = root / "attacker"
            attacker.mkdir()
            marker = root / "injected.txt"
            (attacker / "rfc3161_client.py").write_text(
                "import os\n"
                "from pathlib import Path\n"
                "Path(os.environ['TRUSTED_TIME_ATTACK_MARKER']).write_text('loaded')\n",
                encoding="utf-8",
            )
            environment = dict(launcher.os.environ)
            environment["PYTHONPATH"] = str(attacker)
            environment["TRUSTED_TIME_ATTACK_MARKER"] = str(marker)
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "code" / "monitoring" / "ootang_trusted_time_shadow.py"),
                    "--runtime-root",
                    str(root / "runtime"),
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=180,
            )
            injected = marker.exists()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(injected)

    def test_old_root_lock_is_unchanged_and_new_runtime_is_hash_bound(self):
        expected_root = {
            "pyproject.toml": "bcc6b1e10534d0f2ed2c5e7510ee1761c7be4ca7a52fc743f4b266afedcf15f0",
            "uv.lock": "f1d880ae806b501cd946f0c7564a552e288c7f3b2833a1801132675f5ec8841c",
        }
        for relative, expected in expected_root.items():
            with self.subTest(relative=relative):
                self.assertEqual(
                    hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(),
                    expected,
                )

        profile = json.loads(
            (ROOT / "config" / "ootang_trusted_time_shadow.v1.json").read_text(
                encoding="utf-8"
            )
        )
        implementation = profile["implementation"]
        bindings = (
            ("launcher_path", "launcher_sha256"),
            ("core_path", "core_sha256"),
            ("runtime_project_path", "runtime_project_sha256"),
            ("runtime_lock_path", "runtime_lock_sha256"),
        )
        for path_key, sha_key in bindings:
            with self.subTest(path_key=path_key):
                self.assertEqual(
                    hashlib.sha256(
                        (ROOT / implementation[path_key]).read_bytes()
                    ).hexdigest(),
                    implementation[sha_key],
                )
        self.assertIn(
            'name = "rfc3161-client"\nversion = "1.0.8"',
            (RUNTIME_PROJECT / "uv.lock").read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
