"""Run the frozen cycle-v3 tree through the active-epoch scheduler lease.

This adapter is the scoped official scheduler entrypoint.  It does not alter
cycle-v3 scientific behavior or claim automatic epoch rotation.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import subprocess
import sys
from typing import Protocol


ROOT = Path(__file__).resolve().parents[2]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_epoch_active_transition as transition  # noqa: E402
from monitoring import ootang_epoch_registry as registry  # noqa: E402


STATUS_SCHEMA_VERSION = "ootang_scheduler_cycle_v4_status_v1"
STATUS_NAMESPACE = "scheduler_cycle_v4_v1"
STATUS_FILENAME = "status.json"
CYCLE_SCRIPT_RELATIVE = Path("code/monitoring/ootang_prequential_cycle_v3.py")
CYCLE_CONFIG_RELATIVE = Path("config/ootang_prequential_cycle.v3.json")
HEX_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class AuthorizedCycleV4Error(RuntimeError):
    """Base error for the scoped scheduler adapter."""


class AuthorizedCycleV4IntegrityError(AuthorizedCycleV4Error):
    """The authorization or child execution failed closed."""


class AuthorizedCycleV4BusyError(AuthorizedCycleV4Error):
    """The manager lease or authorized child is busy."""


@dataclass(frozen=True)
class AuthorizedCycleV4Result:
    status_path: Path
    status: str
    transition_entry_sha256: str | None
    child_returncode: int | None


class _CompletedChild(Protocol):
    returncode: int


Runner = Callable[[Sequence[str]], _CompletedChild]


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _scheduler_status_path() -> tuple[Path, Path]:
    namespace = transition.active_transition_paths().registry_root / STATUS_NAMESPACE
    return namespace, namespace / STATUS_FILENAME


def _authorization_command(
    authorization: transition.SchedulerAuthorization,
) -> list[str]:
    adapter_path = Path(__file__).resolve()
    try:
        adapter_sha256 = _sha256(adapter_path.read_bytes())
    except OSError as exc:
        raise AuthorizedCycleV4IntegrityError(
            "scheduler adapter cannot be read"
        ) from exc
    if (
        not isinstance(authorization.scheduler_adapter_sha256, str)
        or HEX_SHA256_RE.fullmatch(authorization.scheduler_adapter_sha256) is None
        or authorization.scheduler_adapter_sha256 != adapter_sha256
    ):
        raise AuthorizedCycleV4IntegrityError(
            "scheduler adapter differs from the active transition binding"
        )

    tree_root = authorization.executable_tree_root
    cycle_script = authorization.cycle_script
    cycle_config = authorization.cycle_config
    runtime_root = authorization.runtime_root
    shadow_runtime_root = authorization.shadow_runtime_root
    for name, path in (
        ("executable tree root", tree_root),
        ("cycle script", cycle_script),
        ("cycle config", cycle_config),
        ("runtime root", runtime_root),
        ("shadow runtime root", shadow_runtime_root),
    ):
        if not isinstance(path, Path) or path != path.resolve():
            raise AuthorizedCycleV4IntegrityError(
                f"authorized {name} is not one canonical absolute path"
            )
    if not tree_root.is_dir():
        raise AuthorizedCycleV4IntegrityError(
            "authorized executable tree root is unavailable"
        )
    if cycle_script != tree_root / CYCLE_SCRIPT_RELATIVE:
        raise AuthorizedCycleV4IntegrityError(
            "authorized cycle script is outside the exact executable tree binding"
        )
    if cycle_config != tree_root / CYCLE_CONFIG_RELATIVE:
        raise AuthorizedCycleV4IntegrityError(
            "authorized cycle config is outside the exact executable tree binding"
        )
    if not cycle_script.is_file() or not cycle_config.is_file():
        raise AuthorizedCycleV4IntegrityError(
            "authorized frozen cycle script or config is unavailable"
        )
    if runtime_root == shadow_runtime_root:
        raise AuthorizedCycleV4IntegrityError(
            "authorized live and shadow runtime roots must remain distinct"
        )
    return [
        sys.executable,
        str(cycle_script),
        "--config",
        str(cycle_config),
        "--runtime-root",
        str(runtime_root),
        "--shadow-runtime-root",
        str(shadow_runtime_root),
    ]


def _status_payload(
    *,
    status: str,
    reason: str,
    authorization: transition.SchedulerAuthorization | None,
    child_returncode: int | None,
) -> dict[str, object]:
    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "observed_at_utc": datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z"),
        "status": status,
        "reason": reason,
        "authority_scope": "scoped_official_scheduler_entrypoint_only",
        "transition_entry_sha256": (
            authorization.transition_entry_sha256 if authorization else None
        ),
        "transition_entry_path": (
            str(authorization.transition_entry_path) if authorization else None
        ),
        "candidate_id": authorization.candidate_id if authorization else None,
        "slot_id": authorization.slot_id if authorization else None,
        "old_live_epoch_id": (
            authorization.old_live_epoch_id if authorization else None
        ),
        "new_live_epoch_id": (
            authorization.new_live_epoch_id if authorization else None
        ),
        "runtime_root": str(authorization.runtime_root) if authorization else None,
        "shadow_runtime_root": (
            str(authorization.shadow_runtime_root) if authorization else None
        ),
        "executable_tree_root": (
            str(authorization.executable_tree_root) if authorization else None
        ),
        "cycle_script": str(authorization.cycle_script) if authorization else None,
        "cycle_config": str(authorization.cycle_config) if authorization else None,
        "scheduler_adapter_sha256": (
            authorization.scheduler_adapter_sha256 if authorization else None
        ),
        "child_returncode": child_returncode,
        "machine_only": True,
        "scheduler_entrypoint_authorization_implemented": authorization is not None,
        "automatic_epoch_rotation_implemented": False,
        "trusted_anchor_receipt_verified": False,
        "formal_warning_output": False,
        "e2_live_evidence_eligible": False,
        "transition_authority": False,
        "cache_authority": False,
    }


def _publish_status(
    namespace: Path,
    path: Path,
    *,
    status: str,
    reason: str,
    authorization: transition.SchedulerAuthorization | None = None,
    child_returncode: int | None = None,
) -> AuthorizedCycleV4Result:
    try:
        registry._atomic_cache(  # noqa: SLF001
            path,
            registry._canonical_bytes(  # noqa: SLF001
                _status_payload(
                    status=status,
                    reason=reason,
                    authorization=authorization,
                    child_returncode=child_returncode,
                )
            ),
            root=namespace,
            name="authorized cycle v4 status",
        )
    except (OSError, registry.EpochRegistryError) as exc:
        raise AuthorizedCycleV4IntegrityError(
            "authorized cycle v4 status cannot be published"
        ) from exc
    return AuthorizedCycleV4Result(
        path,
        status,
        authorization.transition_entry_sha256 if authorization else None,
        child_returncode,
    )


def _subprocess_runner(argv: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(argv, check=False)  # noqa: S603


def _run_authorized_cycle_v4(
    *, runner: Runner = _subprocess_runner
) -> AuthorizedCycleV4Result:
    """Run exactly one authorized cycle-v3 child while its manager lease is held."""

    namespace, status_path = _scheduler_status_path()
    try:
        with transition.scheduler_authorization_lease() as authorization:
            try:
                command = _authorization_command(authorization)
                completed = runner(command)
            except AuthorizedCycleV4IntegrityError as exc:
                _publish_status(
                    namespace,
                    status_path,
                    status="blocked_integrity",
                    reason=f"scheduler_authorization_invalid:{exc}",
                    authorization=authorization,
                )
                raise
            except OSError as exc:
                _publish_status(
                    namespace,
                    status_path,
                    status="blocked_integrity",
                    reason=f"child_process_start_failed:{type(exc).__name__}:{exc}",
                    authorization=authorization,
                )
                raise AuthorizedCycleV4IntegrityError(
                    "authorized cycle-v3 child could not start"
                ) from exc
            returncode = getattr(completed, "returncode", None)
            if not isinstance(returncode, int) or isinstance(returncode, bool):
                _publish_status(
                    namespace,
                    status_path,
                    status="blocked_integrity",
                    reason="child_process_returncode_invalid",
                    authorization=authorization,
                )
                raise AuthorizedCycleV4IntegrityError(
                    "authorized cycle-v3 child returned an invalid status"
                )
            if returncode == 0:
                return _publish_status(
                    namespace,
                    status_path,
                    status="child_completed",
                    reason="authorized_frozen_cycle_v3_child_completed",
                    authorization=authorization,
                    child_returncode=returncode,
                )
            if returncode == 3:
                _publish_status(
                    namespace,
                    status_path,
                    status="child_busy",
                    reason="authorized_frozen_cycle_v3_child_busy",
                    authorization=authorization,
                    child_returncode=returncode,
                )
                raise AuthorizedCycleV4BusyError(
                    "authorized cycle-v3 child reported busy"
                )
            _publish_status(
                namespace,
                status_path,
                status="blocked_integrity",
                reason="authorized_frozen_cycle_v3_child_failed",
                authorization=authorization,
                child_returncode=returncode,
            )
            raise AuthorizedCycleV4IntegrityError(
                f"authorized cycle-v3 child exited with status {returncode}"
            )
    except transition.ActiveTransitionNotReadyError:
        return _publish_status(
            namespace,
            status_path,
            status="waiting_for_active_transition",
            reason="no_active_transition_authorizes_the_official_scheduler",
        )
    except transition.ActiveTransitionBusyError as exc:
        _publish_status(
            namespace,
            status_path,
            status="manager_busy",
            reason=f"active_transition_manager_busy:{exc}",
        )
        raise AuthorizedCycleV4BusyError(str(exc)) from exc
    except transition.ActiveTransitionError as exc:
        _publish_status(
            namespace,
            status_path,
            status="blocked_integrity",
            reason=f"active_transition_invalid:{type(exc).__name__}:{exc}",
        )
        raise AuthorizedCycleV4IntegrityError(str(exc)) from exc


def run_authorized_cycle_v4() -> AuthorizedCycleV4Result:
    """Run the scoped official scheduler entrypoint with no root overrides."""

    return _run_authorized_cycle_v4()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return argparse.ArgumentParser(description=__doc__).parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _parse_args(argv)
    try:
        result = run_authorized_cycle_v4()
    except AuthorizedCycleV4BusyError as exc:
        print(f"[ootang-prequential-cycle-v4] busy: {exc}", file=sys.stderr)
        return 3
    except AuthorizedCycleV4Error as exc:
        print(f"[ootang-prequential-cycle-v4] blocked: {exc}", file=sys.stderr)
        return 2
    print(
        "[ootang-prequential-cycle-v4] "
        f"status={result.status} transition={result.transition_entry_sha256} "
        f"child_returncode={result.child_returncode}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AuthorizedCycleV4BusyError",
    "AuthorizedCycleV4Error",
    "AuthorizedCycleV4IntegrityError",
    "AuthorizedCycleV4Result",
    "run_authorized_cycle_v4",
]
