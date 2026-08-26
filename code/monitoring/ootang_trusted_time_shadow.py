"""Launch the isolated, frozen RFC 3161 trusted-time shadow runtime."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_PROJECT = ROOT / "tools" / "ootang_trusted_time_runtime"
CORE = ROOT / "code" / "monitoring" / "ootang_trusted_time_shadow_core.py"
REQUIRED_UV_VERSION = "0.12.5"
REQUIRED_PYTHON_VERSION = "3.10.20"
EXPECTED_CORE_SHA256 = (
    "797cedbc1e24fac6e4cbf042f48981786b662ce8b0fa913b988bce12818023c7"
)
EXPECTED_RUNTIME_PROJECT_SHA256 = (
    "236606b46ed945fbbce46868a1a8ab5aac9a1131f39352f324e95a6001b59625"
)
EXPECTED_RUNTIME_LOCK_SHA256 = (
    "aebfc5d498735f694572ee8b53c328da5fa66a84da05d202605a2500e8b78f93"
)
MAX_BOOTSTRAP_ARTIFACT_BYTES = 4 * 1024 * 1024


def _artifact_sha256(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError(f"cannot safely open bootstrap artifact {path}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size > MAX_BOOTSTRAP_ARTIFACT_BYTES
        ):
            raise RuntimeError(f"invalid bootstrap artifact {path}")
        raw = b""
        while len(raw) <= MAX_BOOTSTRAP_ARTIFACT_BYTES:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            raw += chunk
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        pathname = path.lstat()
    except OSError as exc:
        raise RuntimeError(f"bootstrap artifact path changed {path}") from exc
    if (
        len(raw) > MAX_BOOTSTRAP_ARTIFACT_BYTES
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or (pathname.st_dev, pathname.st_ino) != (after.st_dev, after.st_ino)
    ):
        raise RuntimeError(f"bootstrap artifact changed while reading {path}")
    return hashlib.sha256(raw).hexdigest()


def _verify_bootstrap() -> None:
    expected = {
        CORE: EXPECTED_CORE_SHA256,
        RUNTIME_PROJECT / "pyproject.toml": EXPECTED_RUNTIME_PROJECT_SHA256,
        RUNTIME_PROJECT / "uv.lock": EXPECTED_RUNTIME_LOCK_SHA256,
    }
    for path, expected_sha256 in expected.items():
        if _artifact_sha256(path) != expected_sha256:
            raise RuntimeError(f"bootstrap artifact hash changed: {path}")


def _child_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if key != "VIRTUAL_ENV"
        and not key.startswith("PYTHON")
        and not key.startswith("UV_")
    }


def _uv_executable() -> str:
    executable = shutil.which("uv")
    if executable is None:
        raise RuntimeError("uv is required for the frozen trusted-time runtime")
    result = subprocess.run(
        [executable, "--version"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    version_fields = result.stdout.strip().split(maxsplit=2)
    if (
        result.returncode != 0
        or len(version_fields) < 2
        or version_fields[0] != "uv"
        or version_fields[1] != REQUIRED_UV_VERSION
    ):
        raise RuntimeError("uv version differs from the trusted-time v1 runtime")
    return executable


def command(argv: list[str] | None = None) -> list[str]:
    _verify_bootstrap()
    arguments = list(sys.argv[1:] if argv is None else argv)
    return [
        _uv_executable(),
        "--no-config",
        "run",
        "--project",
        str(RUNTIME_PROJECT),
        "--isolated",
        "--frozen",
        "--python",
        REQUIRED_PYTHON_VERSION,
        "python",
        "-I",
        str(CORE),
        *arguments,
    ]


def main(argv: list[str] | None = None) -> int:
    try:
        result = subprocess.run(
            command(argv),
            cwd=ROOT,
            env=_child_environment(),
            check=False,
        )
    except (OSError, RuntimeError) as exc:
        print(f"[trusted-time-shadow-launcher] blocked: {exc}", file=sys.stderr)
        return 2
    return int(result.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
