"""Start the machine-only Ootang epoch drain barrier.

R2b deliberately stops at ``DRAINING``.  It neither declares the old epoch
drained nor selects, activates, or rotates to the prepared candidate.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
import copy
import ctypes
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
import errno
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import sys
from typing import Any, BinaryIO


ROOT = Path(__file__).resolve().parents[2]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_epoch_preparation as preparation  # noqa: E402
from monitoring import ootang_epoch_registry as registry  # noqa: E402


DEFAULT_CONFIG_PATH = ROOT / "config" / "ootang_epoch_drain.v1.json"
DEFAULT_CONFIG_SHA256 = (
    "1da0056c8cbdc0fe30b8adca5b72cf52e211b679aae8b8981216e44c0f16d105"
)
IMPLEMENTATION_LOGICAL_PATH = "code/monitoring/ootang_epoch_drain.py"
ZERO_HASH = "0" * 64
MAX_CONTROL_BYTES = 4 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
RENAME_SWAP = 0x00000002
ACL_TYPE_EXTENDED = 0x00000100
FENCE_ACL_TEXT = (
    b"!#acl 1\ngroup:ABCDEFAB-CDEF-ABCD-EFAB-CDEF0000000C:everyone:12:deny:write\n"
)
FENCE_ACL_SHA256 = hashlib.sha256(FENCE_ACL_TEXT).hexdigest()
ARMED_ATTEMPT_MARKER_NAME = ".epoch-drain-armed-attempt.v1.json"
ARMED_ATTEMPT_MARKER_MODE = 0o444
LOCK_ORDER = ("manager", "cycle", "deploy", "runner", "replay", "shadow")

EXPECTED_UPSTREAM = {
    "registry_profile": {
        "path": "config/ootang_epoch_registry.v1.json",
        "expected_sha256": registry.DEFAULT_CONFIG_SHA256,
    },
    "registry_implementation": {
        "path": "code/monitoring/ootang_epoch_registry.py",
        "expected_sha256": (
            "1418b754012b71b296c200539efa846cea63e5a4374e44d216bd656f8b047b9c"
        ),
    },
    "preparation_profile": {
        "path": "config/ootang_epoch_preparation.v1.json",
        "expected_sha256": preparation.DEFAULT_CONFIG_SHA256,
    },
    "preparation_implementation": {
        "path": "code/monitoring/ootang_epoch_preparation.py",
        "expected_sha256": (
            "b03182accc3e8d482683eda29c7c07bdb66f7a316dfa99a41f29e1b99b3fe209"
        ),
    },
    "cycle_v1_profile": {
        "path": "config/ootang_prequential_cycle.v1.json",
        "expected_sha256": (
            "2e4a0da22034a3063f612a723f007bf20b600c1dbdb7c62761368aa7a37810ef"
        ),
    },
    "cycle_v3_profile": {
        "path": "config/ootang_prequential_cycle.v3.json",
        "expected_sha256": (
            "6852876db121027e82aedfb2b65c9cb1d9b40106b19c7068ba8764b317e1db24"
        ),
    },
    "deploy_profile": {
        "path": "config/ootang_prequential_deploy.v1.json",
        "expected_sha256": (
            "60f17602998e976f06d590b7611dfb4480505c21d41a9b05420bd93cf831f940"
        ),
    },
    "live_profile": {
        "path": "config/ootang_prequential_live.v1.json",
        "expected_sha256": (
            "bf7c60a19e26e9a54fc4e1980b3556d6e6d1e3fec4b3a3a7f3de0dbb9b83cf00"
        ),
    },
    "issue_replay_profile": {
        "path": "config/ootang_issue_replay.v1.json",
        "expected_sha256": (
            "c42a56a547691654f9281f44b94e5d79ef66a8ff0064b255a59d67c6939e6fd5"
        ),
    },
    "verified_live_profile": {
        "path": "config/ootang_verified_live.v1.json",
        "expected_sha256": (
            "081af2dfd4b95f28b750d915a2ff74d508381e62f5d539aaaaa62b8add44992b"
        ),
    },
    "calibration_shadow_profile": {
        "path": "config/ootang_prequential_calibration_shadow.v1.json",
        "expected_sha256": (
            "28c02510f81e1832220913d4bfde69bc8abe9a2269aa8279aabc297f60113857"
        ),
    },
    "trusted_time_profile": {
        "path": "config/ootang_trusted_time_shadow.v1.json",
        "expected_sha256": (
            "2d804c887c33e1038f8089de99b08ae65596a02a201eea5aba3ea9034f664b63"
        ),
    },
}

EXPECTED_RUNTIME = {
    "root": "runtime/ootang_epoch_registry_v1",
    "manager_lock": "manager.lock",
    "events": "drain_events",
    "head": "drain_head.json",
    "status": "drain_status.json",
    "intents": "drain_intents",
    "fence_prepares": "drain_fence_prepares",
    "exchange_attempts": "drain_exchange_attempts",
    "capsules": "drain_capsules/sha256",
    "objects": "objects/sha256",
    "tombstones": "drain_tombstones",
    "active_root": "runtime/ootang_prequential_live_v1",
    "shadow_root": "runtime/ootang_prequential_calibration_shadow_v1",
    "cycle_lock": "prequential_cycle.lock",
    "deploy_lock": "deploy_cycle.lock",
    "runner_lock": "runner.lock",
    "replay_lock": "issue_replay.lock",
    "shadow_lock": "runner.lock",
    "issue_inbox": "issue_inbox",
}

EXPECTED_PROTOCOL = {
    "event_schema_version": "ootang_epoch_drain_event_v1",
    "head_schema_version": "ootang_epoch_drain_head_v1",
    "status_schema_version": "ootang_epoch_drain_status_v1",
    "intent_schema_version": "ootang_epoch_drain_fence_intent_v1",
    "fence_prepare_schema_version": "ootang_epoch_drain_fence_prepare_v1",
    "exchange_attempt_schema_version": "ootang_epoch_drain_exchange_attempt_v1",
    "armed_attempt_marker_schema_version": "ootang_epoch_drain_armed_attempt_v1",
    "capsule_schema_version": "ootang_epoch_drain_capsule_v1",
    "intent_prefix_schema_version": "ootang_epoch_drain_intent_prefix_v1",
    "boundary_schema_version": "ootang_epoch_drain_boundary_v1",
    "maximum_manifest_bytes": MAX_MANIFEST_BYTES,
    "maximum_staged_feed_bytes": registry.MAX_FEED_BYTES,
    "initial_previous_entry_sha256": ZERO_HASH,
    "canonical_json": "utf8_sort_keys_compact_no_nan_trailing_lf",
    "lock_order": list(LOCK_ORDER),
    "atomic_exchange": (
        "darwin_acl_fenced_renameatx_np_RENAME_SWAP_0x00000002_no_fallback"
    ),
    "pre_swap_fence": "darwin_extended_acl_everyone_deny_write_exact_and_probed",
    "post_swap_fence": "acl_retained_mode_0555_exact_and_probed",
    "lifecycle_after_first_event": "DRAINING",
    "same_candidate_same_semantics": "idempotent_preserve_first_bytes",
    "orphan_intent_policy": "machine_complete_exact_inode_pair_or_block",
    "candidate_binding_role": (
        "durable_drain_reservation_candidate_at_intent_not_activation_selection"
    ),
    "active_switch": "not_implemented_r2b_drain_start_only",
}

EXPECTED_CAPABILITIES = {
    "immutable_candidate_registry_verified": True,
    "executable_candidate_preparation_verified": True,
    "all_known_writer_locks_serialized": True,
    "canonical_old_issue_route_fence_implemented": True,
    "scheduler_old_issue_creation_fenced": True,
    "epoch_drain_started_implemented": True,
    "old_epoch_drain_implemented": False,
    "active_epoch_switch_implemented": False,
    "automatic_epoch_rotation_implemented": False,
    "activation_candidate_selected": False,
    "trusted_anchor_receipt_verified": False,
    "e2_live_evidence_eligible": False,
    "real_activation_ready": False,
    "formal_warning_output": False,
}

FALSE_CLAIMS = {
    "old_epoch_drain_implemented",
    "active_epoch_switch_implemented",
    "automatic_epoch_rotation_implemented",
    "activation_candidate_selected",
    "trusted_anchor_receipt_verified",
    "e2_live_evidence_eligible",
    "real_activation_ready",
    "formal_warning_output",
}
REFERENCE_KEYS = {"path", "sha256", "size_bytes"}


class EpochDrainError(RuntimeError):
    """Base R2b drain coordinator error."""


class EpochDrainConfigError(EpochDrainError):
    """The reviewed R2b profile or a fixed binding changed."""


class EpochDrainIntegrityError(EpochDrainError):
    """Authority, lifecycle, route, or durable bytes failed closed."""


class EpochDrainBusyError(EpochDrainError):
    """A machine writer owns one of the globally ordered locks."""


@dataclass(frozen=True)
class DrainPaths:
    root: Path
    manager_lock: Path
    events: Path
    head: Path
    status: Path
    intents: Path
    fence_prepares: Path
    exchange_attempts: Path
    capsules: Path
    objects: Path
    tombstones: Path
    active_root: Path
    shadow_root: Path
    cycle_lock: Path
    deploy_lock: Path
    runner_lock: Path
    replay_lock: Path
    shadow_lock: Path
    issue_inbox: Path


@dataclass(frozen=True)
class EpochDrainResult:
    status: str
    status_path: Path
    event_path: Path | None


@dataclass(frozen=True)
class _Authority:
    r1_profile: dict[str, Any]
    r1_paths: registry.RegistryPaths
    feed_observation_events: tuple[dict[str, Any], ...]
    feed_semantics: tuple[registry.FeedSemantics, ...]
    r1_events: tuple[dict[str, Any], ...]
    preparation_profile: dict[str, Any]
    preparation_paths: preparation.PreparationPaths
    preparation_events: tuple[dict[str, Any], ...]
    r1_event: dict[str, Any]
    preparation_event: dict[str, Any]
    candidate_receipt: dict[str, Any]
    r1_capsule: dict[str, Any]
    executable_capsule: dict[str, Any]


@dataclass(frozen=True)
class _CleanState:
    old_live_epoch_id: str
    live_event_count: int
    live_terminal_sha256: str
    guard_record_count: int
    trusted_time_record_count: int
    shadow_event_count: int
    shadow_terminal_sha256: str
    issue_route_inventory_sha256: str
    issue_route_inventory: tuple[dict[str, object], ...]
    outcome_registry_inventory: tuple[dict[str, object], ...]
    guard_inventory: tuple[dict[str, object], ...]
    trusted_time_inventory: tuple[dict[str, object], ...]
    source_authority: dict[str, object]
    live_entry_sha256s: tuple[str, ...]
    shadow_entry_sha256s: tuple[str, ...]


@dataclass(frozen=True)
class _Waiting:
    status: str
    reason: str


@dataclass(frozen=True)
class _CurrentSourceState:
    deploy: dict[str, Any]
    current: object
    exported_at: datetime


@dataclass(frozen=True)
class _ExchangeAttempt:
    snapshot: registry.ArtifactSnapshot
    payload: dict[str, Any]
    boundary: registry.ArtifactSnapshot
    boundary_clean: _CleanState


Clock = Callable[[], datetime]


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _require_manifest_size(raw: bytes, *, name: str) -> None:
    if len(raw) > MAX_MANIFEST_BYTES:
        raise EpochDrainIntegrityError(
            f"{name} exceeds the reviewed manifest size limit"
        )


def _canonical_bytes(value: Mapping[str, object]) -> bytes:
    return registry._canonical_bytes(dict(value))  # noqa: SLF001


def _exact(value: object, keys: set[str], *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise EpochDrainIntegrityError(f"{name} keys changed")
    return value


def _hash(value: object, *, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise EpochDrainIntegrityError(f"{name} is not a lowercase SHA-256")
    return value


def _text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise EpochDrainIntegrityError(f"{name} is not canonical text")
    return value


def _artifact_reference(
    snapshot: registry.ArtifactSnapshot, root: Path
) -> dict[str, object]:
    try:
        relative = snapshot.path.relative_to(root)
    except ValueError as exc:
        raise EpochDrainIntegrityError("Artifact escaped the drain root") from exc
    return {
        "path": relative.as_posix(),
        "sha256": snapshot.sha256,
        "size_bytes": snapshot.size_bytes,
    }


def _read_reference(
    value: object,
    *,
    root: Path,
    name: str,
    maximum_bytes: int = MAX_CONTROL_BYTES,
) -> registry.ArtifactSnapshot:
    record = _exact(value, REFERENCE_KEYS, name=name)
    digest = _hash(record["sha256"], name=f"{name}.sha256")
    size = record["size_bytes"]
    if type(size) is not int or size < 0 or size > maximum_bytes:
        raise EpochDrainIntegrityError(f"{name}.size_bytes is invalid")
    try:
        path = registry._contained(root, record["path"], name=name)  # noqa: SLF001
        snapshot = registry._read_regular(  # noqa: SLF001
            path, name=name, maximum_bytes=maximum_bytes
        )
    except registry.EpochRegistryError as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc
    if snapshot.sha256 != digest or snapshot.size_bytes != size:
        raise EpochDrainIntegrityError(f"{name} reference changed")
    return snapshot


def load_drain_profile(
    path: Path = DEFAULT_CONFIG_PATH, *, project_root: Path = ROOT
) -> dict[str, Any]:
    """Load only the reviewed R2b profile and every exact upstream binding."""

    root = project_root.resolve()
    resolved = path if path.is_absolute() else root / path
    resolved = registry._absolute_lexical(resolved)  # noqa: SLF001
    try:
        resolved.relative_to(root)
        registry._ensure_existing_parents_not_symlinks(root, resolved)  # noqa: SLF001
    except (ValueError, registry.EpochRegistryError) as exc:
        raise EpochDrainConfigError("Drain profile escapes the project root") from exc
    if root != ROOT.resolve() or resolved != registry._absolute_lexical(  # noqa: SLF001
        DEFAULT_CONFIG_PATH
    ):
        raise EpochDrainConfigError(
            "Only the reviewed default drain profile is accepted"
        )
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            resolved, name="epoch drain profile", maximum_bytes=MAX_CONTROL_BYTES
        )
        if snapshot.sha256 != DEFAULT_CONFIG_SHA256:
            raise EpochDrainIntegrityError("Reviewed drain profile digest changed")
        profile = registry._decode_json(snapshot.raw, name="epoch drain profile")  # noqa: SLF001
        _exact(
            profile,
            {
                "schema_version",
                "profile_id",
                "profile_version",
                "case",
                "artifact_status",
                "formal_warning_output",
                "independent_label_used",
                "confirmatory_external_validation",
                "vajont_used",
                "default_pipeline_member",
                "upstream",
                "runtime",
                "protocol",
                "engineering_capabilities",
            },
            name="epoch drain profile",
        )
        if (
            profile["schema_version"],
            profile["profile_id"],
            profile["profile_version"],
            profile["case"],
            profile["artifact_status"],
        ) != (
            "ootang_epoch_drain_profile_v1",
            "ootang-epoch-drain-v1",
            "1.0.0-engineering",
            "ootang",
            "epoch_drain_barrier_engineering_only_not_live_evidence",
        ):
            raise EpochDrainIntegrityError("Drain profile identity changed")
        for flag in (
            "formal_warning_output",
            "independent_label_used",
            "confirmatory_external_validation",
            "vajont_used",
            "default_pipeline_member",
        ):
            if profile[flag] is not False:
                raise EpochDrainIntegrityError(
                    f"Drain profile {flag} must remain false"
                )
        if profile["upstream"] != EXPECTED_UPSTREAM:
            raise EpochDrainIntegrityError("Drain upstream bindings changed")
        if profile["runtime"] != EXPECTED_RUNTIME:
            raise EpochDrainIntegrityError("Drain runtime mapping changed")
        if profile["protocol"] != EXPECTED_PROTOCOL:
            raise EpochDrainIntegrityError("Drain protocol changed")
        if profile["engineering_capabilities"] != EXPECTED_CAPABILITIES:
            raise EpochDrainIntegrityError("Drain capability boundary changed")
        for binding in EXPECTED_UPSTREAM.values():
            bound = registry._contained(root, binding["path"], name="drain binding")  # noqa: SLF001
            captured = registry._read_regular(bound, name="drain binding")  # noqa: SLF001
            if captured.sha256 != binding["expected_sha256"]:
                raise EpochDrainIntegrityError(
                    f"Bound artifact changed: {binding['path']}"
                )
        implementation_path = registry._contained(  # noqa: SLF001
            root, IMPLEMENTATION_LOGICAL_PATH, name="drain implementation"
        )
        implementation = registry._read_regular(  # noqa: SLF001
            implementation_path, name="drain implementation"
        )
    except (EpochDrainIntegrityError, registry.EpochRegistryError) as exc:
        raise EpochDrainConfigError(str(exc)) from exc
    profile["_profile_path"] = str(resolved)
    profile["_profile_sha256"] = snapshot.sha256
    profile["_project_root"] = str(root)
    profile["_implementation_sha256"] = implementation.sha256
    return profile


def _override_root(value: Path, *, name: str) -> Path:
    result = registry._absolute_lexical(value.parent.resolve(strict=False) / value.name)  # noqa: SLF001
    try:
        registry._ensure_existing_parents_not_symlinks(  # noqa: SLF001
            result.anchor and Path(result.anchor) or result.parent, result
        )
    except registry.EpochRegistryError as exc:
        raise EpochDrainConfigError(f"{name} contains a symlink") from exc
    return result


def drain_paths(
    profile: Mapping[str, Any],
    *,
    runtime_root: Path | None = None,
    active_runtime_root: Path | None = None,
    shadow_runtime_root: Path | None = None,
) -> DrainPaths:
    project_root = Path(str(profile["_project_root"]))
    root = (
        registry._contained(
            project_root, profile["runtime"]["root"], name="runtime.root"
        )  # noqa: SLF001
        if runtime_root is None
        else _override_root(runtime_root, name="runtime_root")
    )
    active_root = (
        registry._contained(  # noqa: SLF001
            project_root, profile["runtime"]["active_root"], name="runtime.active_root"
        )
        if active_runtime_root is None
        else _override_root(active_runtime_root, name="active_runtime_root")
    )
    shadow_root = (
        registry._contained(  # noqa: SLF001
            project_root, profile["runtime"]["shadow_root"], name="runtime.shadow_root"
        )
        if shadow_runtime_root is None
        else _override_root(shadow_runtime_root, name="shadow_runtime_root")
    )

    def child(base: Path, value: str, *, name: str) -> Path:
        try:
            return registry._contained(base, value, name=name)  # noqa: SLF001
        except registry.EpochRegistryError as exc:
            raise EpochDrainConfigError(str(exc)) from exc

    paths = DrainPaths(
        root=root,
        manager_lock=child(
            root, profile["runtime"]["manager_lock"], name="manager lock"
        ),
        events=child(root, profile["runtime"]["events"], name="drain events"),
        head=child(root, profile["runtime"]["head"], name="drain head"),
        status=child(root, profile["runtime"]["status"], name="drain status"),
        intents=child(root, profile["runtime"]["intents"], name="drain intents"),
        fence_prepares=child(
            root,
            profile["runtime"]["fence_prepares"],
            name="drain fence prepares",
        ),
        exchange_attempts=child(
            root,
            profile["runtime"]["exchange_attempts"],
            name="drain exchange attempts",
        ),
        capsules=child(root, profile["runtime"]["capsules"], name="drain capsules"),
        objects=child(root, profile["runtime"]["objects"], name="shared objects"),
        tombstones=child(
            root, profile["runtime"]["tombstones"], name="drain tombstones"
        ),
        active_root=active_root,
        shadow_root=shadow_root,
        cycle_lock=child(
            active_root, profile["runtime"]["cycle_lock"], name="cycle lock"
        ),
        deploy_lock=child(
            active_root, profile["runtime"]["deploy_lock"], name="deploy lock"
        ),
        runner_lock=child(
            active_root, profile["runtime"]["runner_lock"], name="runner lock"
        ),
        replay_lock=child(
            active_root, profile["runtime"]["replay_lock"], name="replay lock"
        ),
        shadow_lock=child(
            shadow_root, profile["runtime"]["shadow_lock"], name="shadow lock"
        ),
        issue_inbox=child(
            active_root, profile["runtime"]["issue_inbox"], name="issue inbox"
        ),
    )
    if (
        len(
            {
                paths.manager_lock,
                paths.cycle_lock,
                paths.deploy_lock,
                paths.runner_lock,
                paths.replay_lock,
                paths.shadow_lock,
            }
        )
        != 6
    ):
        raise EpochDrainConfigError("Globally ordered drain locks collide")
    return paths


def _acquire_lock(path: Path, *, label: str) -> BinaryIO:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor: int | None = None
    handle: BinaryIO | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        handle = os.fdopen(descriptor, "a+b", buffering=0)
        descriptor = None
        opened = os.fstat(handle.fileno())
        named = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise EpochDrainIntegrityError(f"{label} lock is not one stable file")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = os.fstat(handle.fileno())
        named = os.stat(path, follow_symlinks=False)
        if (locked.st_dev, locked.st_ino) != (named.st_dev, named.st_ino):
            raise EpochDrainIntegrityError(f"{label} lock changed while acquired")
        return handle
    except BlockingIOError as exc:
        if handle is not None:
            handle.close()
        raise EpochDrainBusyError(f"{label} lock is busy") from exc
    except EpochDrainError:
        if handle is not None:
            handle.close()
        raise
    except OSError as exc:
        if handle is not None:
            handle.close()
        raise EpochDrainIntegrityError(f"Cannot safely acquire {label} lock") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _release_locks(handles: Sequence[BinaryIO]) -> None:
    first: OSError | None = None
    for handle in reversed(handles):
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
        except OSError as exc:
            first = first or exc
    if first is not None and sys.exc_info()[0] is None:
        raise EpochDrainIntegrityError("Cannot release the drain lock set") from first


def _publish_once_durable(
    path: Path, raw: bytes, *, root: Path, name: str
) -> registry.ArtifactSnapshot:
    """Create or adopt immutable bytes and durably confirm their full path."""

    try:
        snapshot = registry._publish_once(  # noqa: SLF001
            path, raw, root=root, name=name
        )
        root = registry._absolute_lexical(root)  # noqa: SLF001
        path = registry._absolute_lexical(path)  # noqa: SLF001
        path.relative_to(root)
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            opened = os.fstat(descriptor)
            named = os.lstat(path)
            if (
                not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(named.st_mode)
                or stat.S_ISLNK(named.st_mode)
                or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
            ):
                raise EpochDrainIntegrityError(
                    f"{name} durable adoption is not one stable regular file"
                )
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

        directory = path.parent
        while True:
            registry._fsync_directory(  # noqa: SLF001
                directory, name=f"{name} durable adoption"
            )
            if directory == root:
                break
            directory = directory.parent
        # Persist a newly created registry-root directory entry as well.  This
        # is deliberately repeated on identical-byte adoption: the prior poll
        # may have crashed after link(2) or mkdir(2) but before either parent
        # directory became durable.
        if root.parent != root:
            registry._fsync_directory(  # noqa: SLF001
                root.parent, name=f"{name} registry-root parent"
            )
        adopted = registry._read_regular(  # noqa: SLF001
            path, name=name, maximum_bytes=max(MAX_MANIFEST_BYTES, len(raw))
        )
    except (OSError, ValueError, registry.EpochRegistryError) as exc:
        raise EpochDrainIntegrityError(
            f"Cannot durably publish {name}:{type(exc).__name__}:{exc}"
        ) from exc
    if (
        adopted.raw != raw
        or adopted.sha256 != snapshot.sha256
        or adopted.size_bytes != snapshot.size_bytes
    ):
        raise EpochDrainIntegrityError(f"{name} durable adoption changed bytes")
    return adopted


def _adopt_snapshot_durable(
    snapshot: registry.ArtifactSnapshot, *, root: Path, name: str
) -> registry.ArtifactSnapshot:
    adopted = _publish_once_durable(snapshot.path, snapshot.raw, root=root, name=name)
    if not _same_snapshot(adopted, snapshot):
        raise EpochDrainIntegrityError(f"{name} durable snapshot identity changed")
    return adopted


def _load_authority(
    profile: Mapping[str, Any],
    paths: DrainPaths,
    *,
    registry_entry_sha256: str | None = None,
    preparation_entry_sha256: str | None = None,
) -> _Authority | _Waiting:
    """Replay R1/R2a and reverify the current prepared tip and all its evidence."""

    try:
        r1_profile = registry.load_registry_profile()
        preparation_profile = preparation.load_preparation_profile()
        r1_paths = registry.registry_paths(r1_profile, runtime_root=paths.root)
        preparation_paths = preparation.preparation_paths(
            preparation_profile, runtime_root=paths.root
        )
        if (
            r1_paths.root != paths.root
            or r1_paths.manager_lock != paths.manager_lock
            or r1_paths.objects != paths.objects
            or preparation_paths.root != paths.root
            or preparation_paths.manager_lock != paths.manager_lock
            or preparation_paths.objects != paths.objects
        ):
            raise EpochDrainIntegrityError(
                "R1, R2a, and R2b do not share one manager and object store"
            )
        observation_events, semantics = registry.replay_feed_observations(
            r1_profile, r1_paths
        )
        r1_events = registry.replay_registry(
            r1_profile, r1_paths, _observed_feed_semantics=semantics
        )
        preparation_events = preparation.replay_preparations(
            preparation_profile,
            preparation_paths,
            r1_profile,
            r1_paths,
            r1_events,
        )
    except (registry.EpochRegistryError, preparation.EpochPreparationError) as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc
    selected = registry_entry_sha256 is not None or preparation_entry_sha256 is not None
    if selected and (registry_entry_sha256 is None or preparation_entry_sha256 is None):
        raise EpochDrainIntegrityError("Historical authority selector is incomplete")
    if not r1_events:
        if selected:
            raise EpochDrainIntegrityError("Drain-bound R1 authority was rolled back")
        return _Waiting(
            "waiting_for_candidate_ready",
            "R1 has no replayed candidate_ready authority event",
        )
    if not preparation_events:
        if selected:
            raise EpochDrainIntegrityError("Drain-bound R2a authority was rolled back")
        return _Waiting(
            "waiting_for_candidate_prepared",
            "R2a has no replayed candidate_prepared authority event",
        )
    if selected:
        r1_matches = [
            event
            for event in r1_events
            if event["entry_sha256"] == registry_entry_sha256
        ]
        preparation_matches = [
            event
            for event in preparation_events
            if event["entry_sha256"] == preparation_entry_sha256
        ]
        if len(r1_matches) != 1 or len(preparation_matches) != 1:
            raise EpochDrainIntegrityError(
                "Drain-bound R1/R2a authority is absent or duplicated"
            )
        r1_event = dict(r1_matches[0])
        preparation_event = dict(preparation_matches[0])
    else:
        r1_event = dict(r1_events[-1])
        preparation_event = dict(preparation_events[-1])
    if (
        preparation_event["registry_event_sequence_id"] != r1_event["sequence_id"]
        or preparation_event["registry_event_entry_sha256"] != r1_event["entry_sha256"]
        or preparation_event["candidate_id"] != r1_event["candidate_id"]
        or preparation_event["slot_id"] != r1_event["slot_id"]
    ):
        if selected:
            raise EpochDrainIntegrityError(
                "Drain-bound R1/R2a authority relation changed"
            )
        return _Waiting(
            "waiting_for_candidate_prepared",
            "latest R1 candidate tip has not become the latest R2a prepared tip",
        )
    try:
        candidate_receipt = registry._verify_receipt(  # noqa: SLF001
            r1_profile,
            r1_paths,
            r1_event["candidate_receipt"],
            require_namespaces_empty=True,
            verify_current_artifacts=True,
        )
        r1_capsule = registry._verify_capsule(  # noqa: SLF001
            r1_profile, r1_paths, candidate_receipt["capsule"]
        )
        executable_capsule = preparation._verify_executable_capsule(  # noqa: SLF001
            preparation_profile,
            preparation_paths,
            preparation_event["executable_capsule"],
            r1_paths=r1_paths,
            r1_manifest=r1_capsule,
            expected_candidate=candidate_receipt,
            expected_registry_event=r1_event,
            require_current_implementation=True,
        )
        tree = preparation._tree_root(  # noqa: SLF001
            preparation_paths, executable_capsule
        )
        preparation._verify_materialized_tree(  # noqa: SLF001
            preparation_paths, executable_capsule, tree
        )
        capsule_reference = _exact(
            preparation_event["executable_capsule"],
            REFERENCE_KEYS,
            name="R2a executable capsule reference",
        )
        preparation._verify_smoke_receipt(  # noqa: SLF001
            preparation_profile,
            preparation_paths,
            preparation_event["smoke_receipt"],
            capsule_manifest=executable_capsule,
            capsule_sha256=_hash(
                capsule_reference["sha256"], name="R2a capsule SHA-256"
            ),
        )
    except (registry.EpochRegistryError, preparation.EpochPreparationError) as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc
    if (
        profile["upstream"]["registry_profile"]["expected_sha256"]
        != r1_profile["_profile_sha256"]
        or profile["upstream"]["preparation_profile"]["expected_sha256"]
        != preparation_profile["_profile_sha256"]
        or profile["upstream"]["preparation_implementation"]["expected_sha256"]
        != preparation_profile["_implementation_sha256"]
    ):
        raise EpochDrainIntegrityError("Replayed authority differs from R2b bindings")
    return _Authority(
        r1_profile=r1_profile,
        r1_paths=r1_paths,
        feed_observation_events=tuple(dict(item) for item in observation_events),
        feed_semantics=tuple(semantics),
        r1_events=tuple(dict(item) for item in r1_events),
        preparation_profile=preparation_profile,
        preparation_paths=preparation_paths,
        preparation_events=tuple(dict(item) for item in preparation_events),
        r1_event=r1_event,
        preparation_event=preparation_event,
        candidate_receipt=candidate_receipt,
        r1_capsule=r1_capsule,
        executable_capsule=executable_capsule,
    )


def _directory_records(
    directory: Path,
    *,
    suffix: str,
    name: str,
) -> dict[date, Path]:
    if not directory.exists() and not directory.is_symlink():
        return {}
    try:
        mode = os.lstat(directory).st_mode
    except OSError as exc:
        raise EpochDrainIntegrityError(f"Cannot inspect {name}") from exc
    if not stat.S_ISDIR(mode):
        raise EpochDrainIntegrityError(f"{name} is not a real directory")
    result: dict[date, Path] = {}
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if path.name.startswith(".") or path.suffix != suffix:
            raise EpochDrainIntegrityError(f"{name} contains an unknown entry")
        try:
            target = date.fromisoformat(path.name[: -len(suffix)])
        except ValueError as exc:
            raise EpochDrainIntegrityError(f"{name} contains a non-date entry") from exc
        if target in result:
            raise EpochDrainIntegrityError(f"{name} contains a duplicate target")
        try:
            if not stat.S_ISREG(os.lstat(path).st_mode):
                raise EpochDrainIntegrityError(f"{name} record is not regular")
        except OSError as exc:
            raise EpochDrainIntegrityError(f"Cannot inspect {name} record") from exc
        result[target] = path
    return result


def _runtime_artifact_record(path: Path, *, root: Path, name: str) -> dict[str, object]:
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            path, name=name, maximum_bytes=MAX_CONTROL_BYTES
        )
        relative = snapshot.path.relative_to(root)
    except (ValueError, registry.EpochRegistryError) as exc:
        raise EpochDrainIntegrityError(f"Cannot inventory {name}") from exc
    return {
        "path": relative.as_posix(),
        "sha256": snapshot.sha256,
        "size_bytes": snapshot.size_bytes,
    }


def _validate_issue_route_and_receipts(
    paths: DrainPaths,
    projection: object,
    live_profile: dict[str, Any],
    prerequisites: object,
    *,
    archived_issue_route: Path | None,
) -> tuple[
    _Waiting | None,
    frozenset[date],
    str,
    tuple[dict[str, object], ...],
]:
    from monitoring import ootang_issue_producer as producer
    from monitoring import ootang_issue_replay as replay
    from monitoring import ootang_prequential_live as live

    try:
        deploy = producer.load_config()
        receipt_root = producer.required_path(
            paths.active_root, deploy["runtime"]["issue_receipts"]
        )
        object_root = producer.required_path(
            paths.active_root, deploy["runtime"]["objects"]
        )
        physical_route = archived_issue_route or paths.issue_inbox
        route_records = _directory_records(
            physical_route,
            suffix=".json",
            name=(
                "archived canonical issue route"
                if archived_issue_route is not None
                else "canonical issue route"
            ),
        )
        receipt_records = _directory_records(
            receipt_root, suffix=".json", name="issue producer receipts"
        )
        inventory: list[dict[str, object]] = []
        for target, issue_path in sorted(route_records.items()):
            if target > projection.last_finalized_date:
                return (
                    _Waiting(
                        "waiting_for_pending_issue_route",
                        f"issue route target is not finalized:{target.isoformat()}",
                    ),
                    frozenset(),
                    ZERO_HASH,
                    (),
                )
            target_text = target.isoformat()
            if target_text not in projection.settled_events:
                return (
                    _Waiting(
                        "waiting_for_pending_issue_route",
                        f"issue route target is not settled by live ledger:{target_text}",
                    ),
                    frozenset(),
                    ZERO_HASH,
                    (),
                )
            logical_issue_path = paths.issue_inbox / issue_path.name
            receipt_path = producer._issue_receipt_path(  # noqa: SLF001
                receipt_root, target.isoformat()
            )
            _, _, _, registered_raw, _ = producer._load_issue_receipt(  # noqa: SLF001
                receipt_path,
                issue_path=logical_issue_path,
                object_root=object_root,
            )
            actual = registry._read_regular(  # noqa: SLF001
                issue_path,
                name="registered canonical issue route record",
                maximum_bytes=MAX_CONTROL_BYTES,
            )
            if actual.raw != registered_raw:
                raise EpochDrainIntegrityError(
                    "Archived issue route differs from producer receipt/object"
                )
            batch = live.load_issue_batch(issue_path, live_profile, prerequisites)
            if (
                batch.target_date != target
                or batch.sha256 != actual.sha256
                or batch.path != issue_path.resolve()
            ):
                raise EpochDrainIntegrityError(
                    "Strict issue loader and registered route bytes differ"
                )
            opened = [
                event
                for event in projection.ledger_events
                if event.event_type == "issue_batch_opened"
                and event.target_date == target_text
            ]
            sealed = [
                event
                for event in projection.ledger_events
                if event.event_type == "issue_batch_sealed"
                and event.target_date == target_text
            ]
            settled = [
                event
                for event in projection.ledger_events
                if event.event_type == "outcome_batch_settled"
                and event.target_date == target_text
            ]
            if len(opened) != 1 or len(sealed) != 1 or len(settled) != 1:
                return (
                    _Waiting(
                        "waiting_for_pending_issue_route",
                        f"issue route lifecycle is not uniquely settled:{target_text}",
                    ),
                    frozenset(),
                    ZERO_HASH,
                    (),
                )
            opened_event = opened[0]
            sealed_event = sealed[0]
            settled_event = settled[0]
            expected_issue_id = live._issue_batch_id(  # noqa: SLF001
                projection.epoch_id, target
            )
            if (
                projection.settled_events[target_text] != settled_event
                or opened_event.issue_id != expected_issue_id
                or sealed_event.issue_id != expected_issue_id
                or settled_event.issue_id != expected_issue_id
                or opened_event.payload.get("issue_batch_id") != expected_issue_id
                or sealed_event.payload.get("issue_batch_id") != expected_issue_id
                or opened_event.payload.get("issue_feed_sha256") != actual.sha256
                or opened_event.payload.get("input_manifest_path")
                != str(batch.input_manifest.path)
                or opened_event.input_manifest_sha256 != batch.input_manifest.sha256
                or sealed_event.input_manifest_sha256 != batch.input_manifest.sha256
                or settled_event.payload.get("issue_batch_sealed_entry_sha256")
                != sealed_event.entry_sha256
                or not (
                    opened_event.sequence_id
                    < sealed_event.sequence_id
                    < settled_event.sequence_id
                )
            ):
                raise EpochDrainIntegrityError(
                    f"Issue route lifecycle binding changed:{target_text}"
                )
            inventory.append(
                {
                    "name": issue_path.name,
                    "target_date": target_text,
                    "sha256": actual.sha256,
                    "size_bytes": actual.size_bytes,
                    "opened_entry_sha256": opened_event.entry_sha256,
                    "sealed_entry_sha256": sealed_event.entry_sha256,
                    "settled_entry_sha256": settled_event.entry_sha256,
                }
            )
        orphan_receipts = sorted(set(receipt_records) - set(route_records))
        if orphan_receipts:
            return (
                _Waiting(
                    "waiting_for_orphan_issue_receipt",
                    f"issue producer receipt lacks canonical route:{orphan_receipts[0]}",
                ),
                frozenset(),
                ZERO_HASH,
                (),
            )
        replay_profile = replay.load_replay_profile()
        replay_paths = replay.runtime_paths(
            replay_profile, runtime_root=paths.active_root
        )
        replay_receipts = _directory_records(
            replay_paths.receipts,
            suffix=".json",
            name="issue replay receipts",
        )
    except (
        producer.IssueProducerError,
        replay.IssueReplayError,
        live.LiveConfigError,
        live.LiveInputError,
        live.LiveIntegrityError,
        registry.EpochRegistryError,
    ) as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc
    inventory_sha256 = _sha256(_canonical_bytes({"records": inventory}))
    return None, frozenset(replay_receipts), inventory_sha256, tuple(inventory)


def _validate_outcome_registry(
    paths: DrainPaths,
    projection: object,
    prerequisites: object,
    source: object,
) -> tuple[_Waiting | None, tuple[dict[str, object], ...]]:
    from monitoring import ootang_outcome_materializer as outcomes
    from monitoring import ootang_prequential_live as live

    profile = outcomes.load_config()
    receipt_root = outcomes._runtime_path(  # noqa: SLF001
        profile, paths.active_root, "outcome_receipts"
    )
    active_root = outcomes._runtime_path(  # noqa: SLF001
        profile, paths.active_root, "outcome_inbox"
    )
    targets = _strict_outcome_receipt_targets(receipt_root)
    chains: dict[str, object] = {}
    inventory: list[dict[str, object]] = []
    try:
        for target in targets:
            chain = outcomes._scan_receipt_chain(  # noqa: SLF001
                target=target,
                profile=profile,
                root=paths.active_root,
                live_module=live,
                live_profile=profile["_live_profile"],
                prerequisites=prerequisites,
            )
            if chain is None:
                raise EpochDrainIntegrityError(
                    "Outcome receipt target has no unique chain"
                )
            active_path = outcomes._outcome_inbox_path(  # noqa: SLF001
                profile, paths.active_root, target
            )
            active_raw = outcomes._legal_active_bytes(chain, active_path)  # noqa: SLF001
            if (
                active_raw != chain.tip.raw
                or chain.pointed is None
                or chain.pointed.receipt.sha256 != chain.tip.receipt.sha256
            ):
                return (
                    _Waiting(
                        "waiting_for_pending_outcome_or_revision",
                        "outcome active copy is not the registered ledger tip:"
                        f"{target}",
                    ),
                    (),
                )
            chains[target.isoformat()] = chain
            inventory.append(
                {
                    "target_date": target.isoformat(),
                    "receipts": [
                        {
                            "source_revision_id": item.payload["source_revision_id"],
                            "revision_sequence_id": item.revision_sequence_id,
                            "receipt_sha256": item.receipt.sha256,
                            "receipt_size_bytes": item.receipt.size_bytes,
                            "exact_outcome_sha256": item.exact_object.sha256,
                            "exact_outcome_size_bytes": item.exact_object.size_bytes,
                            "source_manifest": dict(item.payload["source_manifest"]),
                            "previous_receipt_sha256": (
                                item.previous_receipt.sha256
                                if item.previous_receipt is not None
                                else None
                            ),
                        }
                        for item in chain.receipts
                    ],
                    "active_sha256": _sha256(active_raw),
                    "active_size_bytes": len(active_raw),
                    "active_receipt_sha256": chain.tip.receipt.sha256,
                }
            )
        active = _directory_records(active_root, suffix=".json", name="outcome inbox")
        if {target.isoformat() for target in active} != set(chains):
            return (
                _Waiting(
                    "waiting_for_pending_outcome_or_revision",
                    "outcome inbox and verified receipt targets differ",
                ),
                (),
            )
        pending = outcomes._pending_registered_tip(  # noqa: SLF001
            chains,
            projection=projection,
            source=source,
            profile=profile,
        )
        if pending is not None:
            return (
                _Waiting(
                    "waiting_for_pending_outcome_or_revision",
                    "registered outcome receipt tip has not been consumed by live ledger",
                ),
                (),
            )

        deploy = profile["_deploy_profile"]
        stations = list(deploy["source_feed"]["station_order_live"])

        def validate_known(
            target: date,
            record: object,
            revision_id: str,
            expected_sha: str,
        ) -> None:
            active_path = outcomes._outcome_inbox_path(  # noqa: SLF001
                profile, paths.active_root, target
            )
            receipt_path = outcomes._receipt_path(  # noqa: SLF001
                receipt_root, target, revision_id
            )
            if not receipt_path.is_file() or not active_path.is_file():
                raise outcomes.OutcomeMaterializerConflict(
                    "Ledger-known revision lost its receipt or active outcome"
                )
            registered = outcomes._load_registered_outcome(  # noqa: SLF001
                receipt_path=receipt_path,
                active_path=active_path,
                object_root=outcomes._runtime_path(  # noqa: SLF001
                    profile, paths.active_root, "objects"
                ),
                receipt_root=receipt_root,
                profile=profile,
                root=paths.active_root,
                live_module=live,
                live_profile=profile["_live_profile"],
                prerequisites=prerequisites,
            )
            if registered.exact_object.sha256 != expected_sha:
                raise outcomes.OutcomeMaterializerConflict(
                    "Ledger-known outcome differs from receipt archive"
                )
            if not outcomes._record_matches_outcome(  # noqa: SLF001
                record, registered.payload, stations=stations
            ):
                raise outcomes.OutcomeMaterializerConflict(
                    "Known source revision changed outcome semantics"
                )
            manifest = outcomes._artifact_from_mapping(  # noqa: SLF001
                registered.payload["source_manifest"],
                name="known outcome source manifest",
            )
            manifest_payload = outcomes._validate_input_manifest(  # noqa: SLF001
                manifest, profile=profile, root=paths.active_root
            )
            if not outcomes._record_matches_manifest(  # noqa: SLF001
                record,
                manifest_payload,
                record_schema_version=deploy["source_feed"]["record_schema_version"],
                stations=stations,
            ):
                raise outcomes.OutcomeMaterializerConflict(
                    "Known source revision changed full-record semantics"
                )
            active_snapshot = registry._read_regular(  # noqa: SLF001
                active_path,
                name="ledger-known active outcome",
                maximum_bytes=MAX_CONTROL_BYTES,
            )
            if active_snapshot.raw != registered.raw:
                raise outcomes.OutcomeMaterializerConflict(
                    "Ledger-known active outcome bytes changed"
                )

        selection = outcomes._select_target(  # noqa: SLF001
            source, projection, known_validator=validate_known
        )
        if selection is not None:
            return (
                _Waiting(
                    "waiting_for_pending_outcome_or_revision",
                    "outcome materializer still has a machine-selected target:"
                    f"{selection.kind}:{selection.target_date}",
                ),
                (),
            )
    except (outcomes.OutcomeMaterializerError, registry.EpochRegistryError) as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc
    return None, tuple(inventory)


def _strict_outcome_receipt_targets(receipt_root: Path) -> tuple[date, ...]:
    if not receipt_root.exists() and not receipt_root.is_symlink():
        return ()
    try:
        if not stat.S_ISDIR(os.lstat(receipt_root).st_mode):
            raise EpochDrainIntegrityError(
                "Outcome receipt registry root is not a real directory"
            )
    except OSError as exc:
        raise EpochDrainIntegrityError(
            "Cannot inspect outcome receipt registry root"
        ) from exc
    targets: list[date] = []
    for directory in sorted(receipt_root.iterdir(), key=lambda item: item.name):
        if directory.name.startswith("."):
            raise EpochDrainIntegrityError(
                "Outcome receipt registry contains a hidden entry"
            )
        try:
            target = date.fromisoformat(directory.name)
        except ValueError as exc:
            raise EpochDrainIntegrityError(
                "Outcome receipt registry contains a non-date entry"
            ) from exc
        if target.isoformat() != directory.name:
            raise EpochDrainIntegrityError(
                "Outcome receipt registry date is not canonical"
            )
        try:
            if not stat.S_ISDIR(os.lstat(directory).st_mode):
                raise EpochDrainIntegrityError(
                    "Outcome receipt date registry is not a real directory"
                )
        except OSError as exc:
            raise EpochDrainIntegrityError(
                "Cannot inspect outcome receipt date registry"
            ) from exc
        for entry in sorted(directory.iterdir(), key=lambda item: item.name):
            valid_name = entry.name == "active.json" or (
                entry.suffix == ".json"
                and len(entry.stem) == 64
                and all(character in "0123456789abcdef" for character in entry.stem)
            )
            try:
                regular = stat.S_ISREG(os.lstat(entry).st_mode)
            except OSError as exc:
                raise EpochDrainIntegrityError(
                    "Cannot inspect outcome receipt registry entry"
                ) from exc
            if entry.name.startswith(".") or not valid_name or not regular:
                raise EpochDrainIntegrityError(
                    "Outcome receipt date registry contains an unknown entry"
                )
        targets.append(target)
    return tuple(targets)


def _load_current_source_gate(
    paths: DrainPaths, *, machine_now: datetime
) -> _CurrentSourceState | _Waiting:
    """Recursively verify machine-current source authority, excluding its queue."""

    from monitoring import ootang_live_source as source_module
    from monitoring import ootang_outcome_materializer as outcomes

    try:
        deploy = source_module.load_deploy_profile()
        current = source_module.load_current_source(
            deploy, runtime_root=paths.active_root, project_root=ROOT
        )
        activation = source_module.load_activation_source(
            deploy, runtime_root=paths.active_root, project_root=ROOT
        )
        if (
            deploy["_profile_sha256"]
            != EXPECTED_UPSTREAM["deploy_profile"]["expected_sha256"]
        ):
            raise EpochDrainIntegrityError("Source deploy profile binding changed")
        exported = source_module._utc(  # noqa: SLF001
            current.exported_at_utc, name="current source exported_at_utc"
        )
        if exported > machine_now.astimezone(timezone.utc):
            raise EpochDrainIntegrityError("Current source is future-dated")
        changed = outcomes._activation_change_date(current, activation)  # noqa: SLF001
        if changed is not None:
            return _Waiting(
                "waiting_epoch_rotation_required",
                "current source changed at/before activation watermark:"
                f"{changed.isoformat()}",
            )
    except (
        source_module.SourceConfigError,
        source_module.SourceInputError,
        source_module.SourceIntegrityError,
        outcomes.OutcomeMaterializerError,
    ) as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc
    return _CurrentSourceState(deploy=deploy, current=current, exported_at=exported)


def _load_source_gate(paths: DrainPaths, *, machine_now: datetime) -> object | _Waiting:
    """Verify current source authority and any not-yet-absorbed incoming feed."""

    from monitoring import ootang_live_source as source_module

    current_state = _load_current_source_gate(paths, machine_now=machine_now)
    if isinstance(current_state, _Waiting):
        return current_state
    deploy = current_state.deploy
    current = current_state.current
    exported = current_state.exported_at
    try:
        incoming = source_module._runtime_path(  # noqa: SLF001
            deploy, "incoming_feed", root=paths.active_root
        )
        if not incoming.exists() and not incoming.is_symlink():
            return current
        snapshot = registry._read_regular(  # noqa: SLF001
            incoming,
            name="incoming finalized source feed",
            maximum_bytes=registry.MAX_FEED_BYTES,
        )
        feed = source_module._parse_feed(  # noqa: SLF001
            snapshot.raw, deploy, now=machine_now
        )
        if not source_module._validate_feed_extension(feed, deploy):  # noqa: SLF001
            return _Waiting(
                "waiting_for_source_ingest",
                "incoming feed has no post-baseline finalized extension",
            )
    except (
        source_module.SourceConfigError,
        source_module.SourceInputError,
        source_module.SourceIntegrityError,
        registry.EpochRegistryError,
    ) as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc

    if feed.outcome_source_id != current.outcome_source_id:
        raise EpochDrainIntegrityError("Incoming feed source id changed")
    current_records = tuple(current.records)
    incoming_records = tuple(feed.records)
    if incoming_records == current_records:
        return current
    if feed.exported_at <= exported:
        raise EpochDrainIntegrityError(
            "Changed incoming feed did not advance its export time"
        )
    if len(incoming_records) < len(current_records):
        raise EpochDrainIntegrityError(
            "Incoming feed rolled back current source history"
        )
    known_revision_ids = {
        day: set(revisions)
        for day, revisions in getattr(current, "revision_ids_by_date", ())
    }
    for current_record, incoming_record in zip(
        current_records, incoming_records, strict=False
    ):
        if incoming_record.day != current_record.day:
            raise EpochDrainIntegrityError("Incoming feed changed source date history")
        if incoming_record == current_record:
            continue
        if incoming_record.revision_id == current_record.revision_id:
            raise EpochDrainIntegrityError(
                "Incoming feed reused a revision id with changed semantics"
            )
        if incoming_record.revision_id in known_revision_ids.get(
            incoming_record.day.isoformat(), set()
        ):
            raise EpochDrainIntegrityError(
                "Incoming feed rolled back to a previously known revision"
            )
        return _Waiting(
            "waiting_for_source_ingest",
            f"incoming source revision is not absorbed:{incoming_record.day}",
        )
    return _Waiting(
        "waiting_for_source_ingest",
        "incoming finalized source extension has not been absorbed",
    )


def _validate_archived_guard_intent(
    profile: Mapping[str, Any],
    guard_paths: object,
    *,
    target: date,
    payload: Mapping[str, Any],
    archive_route: Path,
) -> None:
    """Replay a guard intent after its logical issue route was exchanged.

    Guard and replay receipts keep the canonical issue pathname as provenance.
    The immutable bytes now live under ``archive_route``.  This verifier keeps
    the original receipt bytes and logical path intact, but substitutes the
    archived pathname only in an in-memory copy passed to replay's recursive
    consumer verifier.  The copy is never hashed, persisted, or used as progress
    authority because artifact paths are receipt semantics.
    """

    from monitoring import ootang_issue_replay as replay
    from monitoring import ootang_verified_live as guard

    try:
        if set(payload) != guard._INTENT_RECORD_KEYS:  # noqa: SLF001
            raise EpochDrainIntegrityError("Verified-live intent keys changed")
        if (
            payload.get("schema_version")
            != profile["protocol"]["intent_schema_version"]
            or payload.get("profile_id") != profile["profile_id"]
            or payload.get("profile_sha256") != profile["_profile_sha256"]
            or payload.get("artifact_status") != profile["artifact_status"]
            or payload.get("target_date") != target.isoformat()
            or payload.get("outcome_read") is not False
            or payload.get("old_live_v1_direct_entrypoint_disabled") is not False
            or any(
                payload.get(key) is not value
                for key, value in guard._FALSE_RECORD_FLAGS.items()  # noqa: SLF001
            )
        ):
            raise EpochDrainIntegrityError("Verified-live intent identity changed")

        created_at = guard._parse_record_utc(  # noqa: SLF001
            payload.get("created_at_utc"), name="intent.created_at_utc"
        )
        model_manifest_sha256 = guard._record_sha256(  # noqa: SLF001
            payload.get("model_manifest_sha256"),
            name="intent.model_manifest_sha256",
        )
        implementation_sha256 = guard._record_sha256(  # noqa: SLF001
            payload.get("guard_implementation_sha256"),
            name="intent.guard_implementation_sha256",
        )
        if (
            implementation_sha256
            != guard._read_snapshot(  # noqa: SLF001
                Path(str(guard.__file__)), name="verified-live implementation"
            ).sha256
        ):
            raise EpochDrainIntegrityError(
                "Verified-live intent implementation binding changed"
            )

        pre_head = payload.get("live_pre_head")
        if not isinstance(pre_head, dict) or set(pre_head) != {
            "epoch_id",
            "event_count",
            "sequence_id",
            "entry_sha256",
        }:
            raise EpochDrainIntegrityError("Guard intent live pre-head changed")
        guard._record_text(  # noqa: SLF001
            pre_head.get("epoch_id"), name="intent.live_pre_head.epoch_id"
        )
        event_count = guard._record_positive_int(  # noqa: SLF001
            pre_head.get("event_count"), name="intent.live_pre_head.event_count"
        )
        sequence_id = guard._record_positive_int(  # noqa: SLF001
            pre_head.get("sequence_id"), name="intent.live_pre_head.sequence_id"
        )
        entry_sha256 = guard._record_sha256(  # noqa: SLF001
            pre_head.get("entry_sha256"), name="intent.live_pre_head.entry_sha256"
        )
        if event_count != sequence_id or entry_sha256 == ZERO_HASH:
            raise EpochDrainIntegrityError("Guard intent live pre-head is inconsistent")

        issue_ref = payload.get("issue")
        if not isinstance(issue_ref, dict) or set(issue_ref) != {
            "path",
            "sha256",
            "size_bytes",
        }:
            raise EpochDrainIntegrityError("intent.issue artifact keys changed")
        logical_issue_path = guard_paths.live.issue_inbox / f"{target.isoformat()}.json"
        try:
            expected_relative = logical_issue_path.relative_to(
                guard_paths.root
            ).as_posix()
        except ValueError as exc:
            raise EpochDrainIntegrityError(
                "Canonical guard issue path escaped its runtime"
            ) from exc
        if issue_ref.get("path") != expected_relative:
            raise EpochDrainIntegrityError(
                "Guard intent logical issue pathname changed"
            )
        physical_issue = registry._read_regular(  # noqa: SLF001
            archive_route / logical_issue_path.name,
            name="archived guard issue",
            maximum_bytes=guard.MAX_SNAPSHOT_BYTES,
        )
        issue_sha256 = guard._record_sha256(  # noqa: SLF001
            issue_ref.get("sha256"), name="intent.issue.sha256"
        )
        issue_size = issue_ref.get("size_bytes")
        if (
            type(issue_size) is not int
            or issue_size <= 0
            or physical_issue.sha256 != issue_sha256
            or physical_issue.size_bytes != issue_size
        ):
            raise EpochDrainIntegrityError(
                "Archived guard issue differs from its logical intent binding"
            )
        logical_issue = guard.ArtifactSnapshot(
            path=logical_issue_path,
            sha256=physical_issue.sha256,
            size_bytes=physical_issue.size_bytes,
            raw=physical_issue.raw,
        )
        input_manifest = guard._validate_artifact_reference(  # noqa: SLF001
            payload.get("input_manifest"),
            root=guard_paths.root,
            name="intent.input_manifest",
        )
        replay_receipt = guard._validate_artifact_reference(  # noqa: SLF001
            payload.get("replay_receipt"),
            root=guard_paths.root,
            name="intent.replay_receipt",
        )
        replay_payload = guard._validate_replay_receipt_identity(  # noqa: SLF001
            replay_receipt,
            root=guard_paths.root,
            target=target,
            expected_pre_head=pre_head,
            issue=logical_issue,
            input_manifest=input_manifest,
        )
        replay_verified_at = guard._parse_record_utc(  # noqa: SLF001
            replay_payload.get("verified_at_utc"),
            name="replay_receipt.verified_at_utc",
        )
        if replay_verified_at > created_at:
            raise EpochDrainIntegrityError(
                "Guard intent predates its independent replay receipt"
            )

        project_root = Path(str(profile["_project_root"]))
        replay_profile = replay.load_replay_profile(
            guard_paths.replay_profile_path, project_root=project_root
        )
        replay_paths = replay.runtime_paths(
            replay_profile, runtime_root=guard_paths.root
        )
        if replay_receipt.path != replay._receipt_path(  # noqa: SLF001
            replay_paths, target
        ):
            raise EpochDrainIntegrityError(
                "Guard intent links a relocated replay receipt"
            )
        archived_payload = copy.deepcopy(replay_payload)
        archived_issue_ref = dict(archived_payload["issue"])
        archived_issue_ref["path"] = str(physical_issue.path)
        archived_payload["issue"] = archived_issue_ref
        replay._validate_receipt_payload(  # noqa: SLF001
            archived_payload,
            profile=replay_profile,
            paths=replace(replay_paths, issue_inbox=archive_route),
            target=target,
            expected_issue_sha256=physical_issue.sha256,
            expected_input_manifest_sha256=input_manifest.sha256,
            expected_model_manifest_sha256=model_manifest_sha256,
            expected_ledger_pre_head=pre_head,
        )
    except EpochDrainError:
        raise
    except (
        guard.VerifiedLiveError,
        replay.IssueReplayError,
        registry.EpochRegistryError,
    ) as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc


def _load_clean_state(
    paths: DrainPaths,
    *,
    machine_now: datetime,
    archived_issue_route: Path | None = None,
    allow_queued_incoming: bool = False,
) -> _CleanState | _Waiting:
    """Strictly replay old runtime state and permit only a clean drain start."""

    from monitoring import ootang_prequential_calibration_shadow as shadow
    from monitoring import ootang_prequential_live as live
    from monitoring import ootang_trusted_time_shadow_core as trusted
    from monitoring import ootang_verified_live as guard

    if allow_queued_incoming:
        current_source = _load_current_source_gate(paths, machine_now=machine_now)
        if isinstance(current_source, _Waiting):
            return current_source
        source = current_source.current
    else:
        source = _load_source_gate(paths, machine_now=machine_now)
        if isinstance(source, _Waiting):
            return source

    try:
        live_profile = live.load_config()
        live_paths = live.runtime_paths(live_profile, runtime_root=paths.active_root)
        if (
            Path(live_paths.lock) != paths.runner_lock
            or Path(live_paths.issue_inbox) != paths.issue_inbox
        ):
            raise EpochDrainIntegrityError("Live runtime paths differ from R2b locks")
        prerequisites = live.load_prerequisites(live_profile, live_paths)
        if prerequisites is None:
            return _Waiting(
                "waiting_for_live_prerequisites",
                "old live source/model prerequisites are not machine-verifiable",
            )
        projection = live.load_verified_ledger_projection(
            live_profile, live_paths, prerequisites
        )
    except live.LivePrerequisiteError as exc:
        return _Waiting("waiting_for_live_prerequisites", str(exc))
    except (live.LiveConfigError, live.LiveInputError, live.LiveIntegrityError) as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc
    if projection.outstanding_target_date is not None:
        return _Waiting(
            "waiting_for_outstanding_issue",
            f"old live issue remains outstanding:{projection.outstanding_target_date}",
        )
    issue_waiting, replay_targets, issue_inventory_sha256, issue_inventory = (
        _validate_issue_route_and_receipts(
            paths,
            projection,
            live_profile,
            prerequisites,
            archived_issue_route=archived_issue_route,
        )
    )
    if issue_waiting is not None:
        return issue_waiting
    outcome_waiting, outcome_inventory = _validate_outcome_registry(
        paths, projection, prerequisites, source
    )
    if outcome_waiting is not None:
        return outcome_waiting

    try:
        guard_profile = guard.load_verified_live_profile()
        guard_paths = guard.runtime_paths(guard_profile, runtime_root=paths.active_root)
        intents = _directory_records(
            guard_paths.intents, suffix=".json", name="guard intents"
        )
        completions = _directory_records(
            guard_paths.completions, suffix=".json", name="guard completions"
        )
        missing = sorted(set(intents) - set(completions))
        if missing:
            return _Waiting(
                "waiting_for_pending_guard",
                f"guard intent lacks completion:{missing[0].isoformat()}",
            )
        if set(completions) - set(intents):
            raise EpochDrainIntegrityError("Guard completion exists without intent")
        if replay_targets != frozenset(intents):
            return _Waiting(
                "waiting_for_pending_guard",
                "issue replay receipts and verified guard intents differ",
            )
        guard_inventory: list[dict[str, object]] = []
        for target in sorted(intents):
            intent_payload, intent_snapshot = guard._load_record(  # noqa: SLF001
                intents[target], name="verified-live intent"
            )
            if archived_issue_route is None:
                guard._validate_intent_record(  # noqa: SLF001
                    guard_profile,
                    guard_paths,
                    target=target,
                    payload=intent_payload,
                )
            else:
                _validate_archived_guard_intent(
                    guard_profile,
                    guard_paths,
                    target=target,
                    payload=intent_payload,
                    archive_route=archived_issue_route,
                )
            guard._validate_pre_head_against_events(  # noqa: SLF001
                intent_payload["live_pre_head"], projection.ledger_events
            )
            completion_payload, completion_snapshot = guard._load_record(  # noqa: SLF001
                completions[target], name="verified-live completion"
            )
            guard._validate_completion_record(  # noqa: SLF001
                guard_profile,
                guard_paths,
                target=target,
                payload=completion_payload,
                intent_payload=intent_payload,
                intent_snapshot=intent_snapshot,
                events=projection.ledger_events,
            )
            guard_inventory.append(
                {
                    "target_date": target.isoformat(),
                    "intent": _runtime_artifact_record(
                        intent_snapshot.path,
                        root=paths.active_root,
                        name="verified-live intent inventory",
                    ),
                    "completion": _runtime_artifact_record(
                        completion_snapshot.path,
                        root=paths.active_root,
                        name="verified-live completion inventory",
                    ),
                }
            )
    except guard.VerifiedLiveError as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc

    try:
        trusted_profile = trusted.load_trusted_time_profile()
        trusted_paths = trusted.trusted_time_paths(
            trusted_profile, runtime_root=paths.active_root
        )
        requests = _directory_records(
            trusted_paths.requests, suffix=".json", name="trusted-time requests"
        )
        receipts = _directory_records(
            trusted_paths.receipts, suffix=".json", name="trusted-time receipts"
        )
        missing_receipts = sorted(set(requests) - set(receipts))
        if missing_receipts:
            return _Waiting(
                "waiting_for_pending_trusted_time",
                f"trusted-time request lacks receipt:{missing_receipts[0].isoformat()}",
            )
        if set(receipts) - set(requests):
            raise EpochDrainIntegrityError(
                "Trusted-time receipt exists without request"
            )
        request_der = _directory_records(
            trusted_paths.request_der,
            suffix=".tsq",
            name="trusted-time request DER",
        )
        response_links = _directory_records(
            trusted_paths.response_links,
            suffix=".json",
            name="trusted-time response links",
        )
        if set(request_der) != set(requests) or set(response_links) != set(requests):
            raise EpochDrainIntegrityError(
                "Trusted-time request/DER/response-link namespaces differ"
            )
        trusted_inventory: list[dict[str, object]] = []
        for target in sorted(requests):
            trusted.load_verified_trusted_time_receipt(
                target, runtime_root=paths.active_root
            )
            trusted_inventory.append(
                {
                    "target_date": target.isoformat(),
                    "request": _runtime_artifact_record(
                        requests[target],
                        root=paths.active_root,
                        name="trusted-time request inventory",
                    ),
                    "request_der": _runtime_artifact_record(
                        request_der[target],
                        root=paths.active_root,
                        name="trusted-time DER inventory",
                    ),
                    "receipt": _runtime_artifact_record(
                        receipts[target],
                        root=paths.active_root,
                        name="trusted-time receipt inventory",
                    ),
                    "response_link": _runtime_artifact_record(
                        response_links[target],
                        root=paths.active_root,
                        name="trusted-time response-link inventory",
                    ),
                }
            )
    except trusted.TrustedTimeError as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc

    try:
        shadow_profile = shadow.load_shadow_profile()
        shadow_paths = shadow.runtime_paths(
            shadow_profile, runtime_root=paths.shadow_root
        )
        if shadow_paths.lock != paths.shadow_lock:
            raise EpochDrainIntegrityError("Shadow runtime lock differs from R2b")
        shadow_projection = shadow.load_verified_shadow_projection(
            shadow_profile, shadow_paths
        )
    except shadow.CalibrationShadowError as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc
    if shadow_projection is not None:
        if shadow_projection.outstanding_target_date is not None:
            return _Waiting(
                "waiting_for_pending_shadow",
                "calibration shadow has an outstanding transaction",
            )
        if not shadow_projection.epoch_open:
            raise EpochDrainIntegrityError(
                "Calibration shadow epoch is already closed before drain start"
            )
        if shadow_projection.upstream_live_epoch_id != projection.epoch_id:
            raise EpochDrainIntegrityError("Shadow epoch binds a different live epoch")
        if (
            shadow_projection.live_cursor_sequence_id
            != projection.ledger_terminal_sequence_id
            or shadow_projection.live_cursor_sha256 != projection.ledger_terminal_sha256
        ):
            return _Waiting(
                "waiting_for_pending_shadow",
                "calibration shadow has not classified the live ledger tip",
            )
        shadow_count = shadow_projection.ledger_event_count
        shadow_terminal = shadow_projection.ledger_terminal_sha256
        shadow_entries = tuple(
            str(event.entry_sha256) for event in shadow_projection.ledger_events
        )
    else:
        shadow_count = 0
        shadow_terminal = ZERO_HASH
        shadow_entries = ()
    semantic_manifest = getattr(source, "semantic_manifest", None)
    activation_manifest = getattr(source, "activation_manifest", None)
    if semantic_manifest is None or activation_manifest is None:
        raise EpochDrainIntegrityError("Current source lost its manifest authority")
    snapshot_receipt = getattr(source, "snapshot_receipt", None)
    source_records = []
    for record in getattr(source, "records", ()):
        source_records.append(
            {
                "date": record.day.isoformat(),
                "revision_id": record.revision_id,
                "observed_at_utc": record.observed_at_utc,
                "available_at_utc": record.available_at_utc,
                "finalized_at_utc": record.finalized_at_utc,
                "rainfall_mm": record.rainfall_mm,
                "reservoir_water_level_m": record.reservoir_water_level_m,
                "displacement_mm": dict(record.displacement_mm),
            }
        )
    source_authority = {
        "outcome_source_id": _text(
            getattr(source, "outcome_source_id", None),
            name="current outcome source id",
        ),
        "watermark": getattr(source, "watermark").isoformat(),
        "exported_at_utc": _text(
            getattr(source, "exported_at_utc", None),
            name="current source exported_at_utc",
        ),
        # Upstream source ArtifactRef.as_dict() intentionally emits an absolute
        # resolved path.  Drain authority is portable only within the reviewed
        # active runtime, so persist a contained-relative reference instead and
        # make every replay pass back through registry._contained().
        "semantic_manifest": _artifact_reference(semantic_manifest, paths.active_root),
        "activation_manifest": _artifact_reference(
            activation_manifest, paths.active_root
        ),
        "snapshot_receipt": (
            _artifact_reference(snapshot_receipt, paths.active_root)
            if snapshot_receipt is not None
            else None
        ),
        "snapshot_sequence_id": getattr(source, "snapshot_sequence_id", 0),
        "records": source_records,
        "revision_ids_by_date": [
            {"date": day, "revision_ids": list(revisions)}
            for day, revisions in getattr(source, "revision_ids_by_date", ())
        ],
    }
    return _CleanState(
        old_live_epoch_id=projection.epoch_id,
        live_event_count=projection.ledger_event_count,
        live_terminal_sha256=projection.ledger_terminal_sha256,
        guard_record_count=len(intents),
        trusted_time_record_count=len(requests),
        shadow_event_count=shadow_count,
        shadow_terminal_sha256=shadow_terminal,
        issue_route_inventory_sha256=issue_inventory_sha256,
        issue_route_inventory=issue_inventory,
        outcome_registry_inventory=outcome_inventory,
        guard_inventory=tuple(guard_inventory),
        trusted_time_inventory=tuple(trusted_inventory),
        source_authority=source_authority,
        live_entry_sha256s=tuple(
            str(event.entry_sha256) for event in projection.ledger_events
        ),
        shadow_entry_sha256s=shadow_entries,
    )


def _verify_draining_runtime_prefix(
    paths: DrainPaths, start: _CleanState, archived_issue_route: Path
) -> None:
    """Verify mutable old-runtime state extends the sealed drain-start prefix."""

    from monitoring import ootang_prequential_live as live
    from monitoring import ootang_live_source as source_module

    try:
        live_profile = live.load_config()
        live_paths = live.runtime_paths(live_profile, runtime_root=paths.active_root)
        prerequisites = live.load_prerequisites(live_profile, live_paths)
        if prerequisites is None:
            raise EpochDrainIntegrityError(
                "DRAINING old runtime lost its live prerequisites"
            )
        projection = live.load_verified_ledger_projection(
            live_profile, live_paths, prerequisites
        )
        deploy = source_module.load_deploy_profile()
        source = source_module.load_current_source(
            deploy, runtime_root=paths.active_root, project_root=ROOT
        )
    except (live.LiveConfigError, live.LiveInputError, live.LiveIntegrityError) as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc
    except source_module.SourceError as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc
    entries = tuple(str(event.entry_sha256) for event in projection.ledger_events)
    if (
        projection.epoch_id != start.old_live_epoch_id
        or projection.ledger_event_count < start.live_event_count
        or len(entries) < start.live_event_count
        or entries[start.live_event_count - 1] != start.live_terminal_sha256
    ):
        raise EpochDrainIntegrityError(
            "Current old live ledger does not extend the drain-start prefix"
        )
    if projection.outstanding_target_date is not None:
        raise EpochDrainIntegrityError(
            "A new outstanding issue appeared after the canonical route fence"
        )
    issue_waiting, _, inventory_sha256, _ = _validate_issue_route_and_receipts(
        paths,
        projection,
        live_profile,
        prerequisites,
        archived_issue_route=archived_issue_route,
    )
    if issue_waiting is not None:
        raise EpochDrainIntegrityError(issue_waiting.reason)
    if inventory_sha256 != start.issue_route_inventory_sha256:
        raise EpochDrainIntegrityError(
            "Archived issue-route inventory differs from the event boundary"
        )
    _validate_outcome_registry(paths, projection, prerequisites, source)


def _capture_bound_object(
    paths: DrainPaths, source: Path, *, suffix: str, name: str
) -> registry.ArtifactSnapshot:
    try:
        source_snapshot = registry._read_regular(  # noqa: SLF001
            source, name=name, maximum_bytes=MAX_CONTROL_BYTES
        )
        target = paths.objects / f"{source_snapshot.sha256}.{suffix}"
        captured = _publish_once_durable(
            target, source_snapshot.raw, root=paths.root, name=name
        )
    except registry.EpochRegistryError as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc
    if captured.sha256 != source_snapshot.sha256:
        raise EpochDrainIntegrityError(f"{name} object capture changed bytes")
    return captured


def _validate_epoch_transition(authority: _Authority, clean: _CleanState) -> None:
    build = authority.candidate_receipt.get("candidate_build")
    if not isinstance(build, dict):
        raise EpochDrainIntegrityError("Candidate receipt lost its build binding")
    candidate_live_epoch_id = _hash(
        build.get("live_epoch_id"), name="candidate live epoch id"
    )
    if candidate_live_epoch_id == clean.old_live_epoch_id:
        raise EpochDrainIntegrityError(
            "Prepared candidate repeats the currently active live epoch"
        )


def _pre_intent_feed_gate(
    paths: DrainPaths,
    authority: _Authority,
    current_source: object,
    *,
    machine_now: datetime,
) -> _Waiting | None:
    """Require the prepared candidate feed to match current source semantics."""

    from monitoring import ootang_live_source as source_module

    if not authority.feed_semantics or not authority.feed_observation_events:
        raise EpochDrainIntegrityError("Candidate authority has no observed feed chain")
    receipt_feed = authority.candidate_receipt.get("feed")
    if not isinstance(receipt_feed, dict):
        raise EpochDrainIntegrityError("Candidate receipt lost its feed binding")
    candidate_feed_sha256 = _hash(receipt_feed.get("sha256"), name="candidate feed")
    observed_tip = authority.feed_semantics[-1]
    try:
        candidate_path = registry._contained(  # noqa: SLF001
            paths.root, receipt_feed.get("path"), name="candidate receipt feed"
        )
        candidate_snapshot = registry._read_regular(  # noqa: SLF001
            candidate_path,
            name="prepared candidate feed",
            maximum_bytes=registry.MAX_FEED_BYTES,
        )
        if candidate_snapshot.sha256 != candidate_feed_sha256:
            raise EpochDrainIntegrityError("Prepared candidate feed bytes changed")
        candidate_registry_semantics = registry._feed_semantics(  # noqa: SLF001
            candidate_snapshot
        )
        deploy = source_module.load_deploy_profile()
        candidate_source_semantics = source_module._parse_feed(  # noqa: SLF001
            candidate_snapshot.raw, deploy, now=machine_now
        )
    except (registry.EpochRegistryError, source_module.SourceError) as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc
    if (
        observed_tip.outcome_source_id != candidate_registry_semantics.outcome_source_id
        or observed_tip.records != candidate_registry_semantics.records
        or getattr(current_source, "outcome_source_id", None)
        != candidate_source_semantics.outcome_source_id
        or tuple(getattr(current_source, "records", ()))
        != candidate_source_semantics.records
    ):
        return _Waiting(
            "waiting_for_candidate_feed",
            "current/observed source semantics have not become prepared authority",
        )
    return None


def _intent_prefix_payload(
    profile: Mapping[str, Any], clean: _CleanState
) -> dict[str, object]:
    return {
        "schema_version": profile["protocol"]["intent_prefix_schema_version"],
        "snapshot_role": "durable_drain_reservation_lower_bound",
        "old_live_epoch_id": clean.old_live_epoch_id,
        "live_event_count": clean.live_event_count,
        "live_terminal_sha256": clean.live_terminal_sha256,
        "live_entry_sha256s": list(clean.live_entry_sha256s),
        "shadow_event_count": clean.shadow_event_count,
        "shadow_terminal_sha256": clean.shadow_terminal_sha256,
        "shadow_entry_sha256s": list(clean.shadow_entry_sha256s),
        "issue_route_inventory_sha256": clean.issue_route_inventory_sha256,
        "issue_route_inventory": _inventory_manifest(clean.issue_route_inventory),
        "outcome_registry_inventory": _inventory_manifest(
            clean.outcome_registry_inventory
        ),
        "guard_inventory": _inventory_manifest(clean.guard_inventory),
        "trusted_time_inventory": _inventory_manifest(clean.trusted_time_inventory),
        "source_authority": copy.deepcopy(clean.source_authority),
    }


def _publish_intent_prefix(
    profile: Mapping[str, Any], paths: DrainPaths, clean: _CleanState
) -> registry.ArtifactSnapshot:
    raw = _canonical_bytes(_intent_prefix_payload(profile, clean))
    _require_manifest_size(raw, name="Epoch drain intent prefix")
    try:
        return _publish_once_durable(
            paths.objects / f"{_sha256(raw)}.epoch-drain-intent-prefix.json",
            raw,
            root=paths.root,
            name="epoch drain intent prefix",
        )
    except registry.EpochRegistryError as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc


def _capsule_payload(
    profile: Mapping[str, Any],
    paths: DrainPaths,
    authority: _Authority,
    clean: _CleanState,
    profile_object: registry.ArtifactSnapshot,
    implementation_object: registry.ArtifactSnapshot,
    intent_prefix: registry.ArtifactSnapshot,
) -> dict[str, object]:
    _validate_epoch_transition(authority, clean)
    prep = authority.preparation_event
    return {
        "schema_version": profile["protocol"]["capsule_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "profile_object": _artifact_reference(profile_object, paths.root),
        "implementation_sha256": implementation_object.sha256,
        "implementation_object": _artifact_reference(implementation_object, paths.root),
        "intent_prefix": _artifact_reference(intent_prefix, paths.root),
        "registry_event_sequence_id": authority.r1_event["sequence_id"],
        "registry_event_entry_sha256": authority.r1_event["entry_sha256"],
        "candidate_id": authority.r1_event["candidate_id"],
        "slot_id": authority.r1_event["slot_id"],
        "candidate_receipt": authority.r1_event["candidate_receipt"],
        "r1_capsule": authority.candidate_receipt["capsule"],
        "preparation_event_sequence_id": prep["sequence_id"],
        "preparation_event_entry_sha256": prep["entry_sha256"],
        "preparation_event_type": prep["event_type"],
        "executable_capsule": prep["executable_capsule"],
        "executable_tree_sha256": authority.executable_capsule["tree_sha256"],
        "smoke_receipt": prep["smoke_receipt"],
        "old_live_epoch_id": clean.old_live_epoch_id,
        "live_event_count": clean.live_event_count,
        "live_terminal_sha256": clean.live_terminal_sha256,
        "live_entry_sha256s": list(clean.live_entry_sha256s),
        "guard_record_count": clean.guard_record_count,
        "trusted_time_record_count": clean.trusted_time_record_count,
        "shadow_event_count": clean.shadow_event_count,
        "shadow_terminal_sha256": clean.shadow_terminal_sha256,
        "shadow_entry_sha256s": list(clean.shadow_entry_sha256s),
        "issue_route_inventory_sha256": clean.issue_route_inventory_sha256,
        "issue_route_inventory": _inventory_manifest(clean.issue_route_inventory),
        "outcome_registry_inventory": _inventory_manifest(
            clean.outcome_registry_inventory
        ),
        "guard_inventory": _inventory_manifest(clean.guard_inventory),
        "trusted_time_inventory": _inventory_manifest(clean.trusted_time_inventory),
        "source_authority": copy.deepcopy(clean.source_authority),
        "canonical_issue_route": str(paths.issue_inbox),
        "lock_order": list(LOCK_ORDER),
        "machine_only": True,
        "snapshot_role": "drain_reservation_candidate_at_intent_prefix",
        "candidate_at_intent": True,
        "activation_candidate_selected": False,
        "lifecycle_state": "DRAINING",
        "old_epoch_drain_implemented": False,
        "active_epoch_switch_implemented": False,
        "automatic_epoch_rotation_implemented": False,
        "trusted_anchor_receipt_verified": False,
        "e2_live_evidence_eligible": False,
        "real_activation_ready": False,
        "formal_warning_output": False,
    }


def _publish_capsule(
    profile: Mapping[str, Any],
    paths: DrainPaths,
    authority: _Authority,
    clean: _CleanState,
) -> registry.ArtifactSnapshot:
    profile_object = _capture_bound_object(
        paths,
        Path(str(profile["_profile_path"])),
        suffix="epoch-drain-profile.json",
        name="epoch drain profile object",
    )
    implementation_object = _capture_bound_object(
        paths,
        ROOT / IMPLEMENTATION_LOGICAL_PATH,
        suffix="epoch-drain-implementation.py",
        name="epoch drain implementation object",
    )
    intent_prefix = _publish_intent_prefix(profile, paths, clean)
    payload = _capsule_payload(
        profile,
        paths,
        authority,
        clean,
        profile_object,
        implementation_object,
        intent_prefix,
    )
    raw = _canonical_bytes(payload)
    _require_manifest_size(raw, name="Epoch drain capsule")
    try:
        return _publish_once_durable(
            paths.capsules / f"{_sha256(raw)}.json",
            raw,
            root=paths.root,
            name="epoch drain capsule",
        )
    except registry.EpochRegistryError as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc


def _verify_capsule(
    profile: Mapping[str, Any],
    paths: DrainPaths,
    authority: _Authority,
    clean: _CleanState | None,
    value: object,
    *,
    require_current_implementation: bool,
) -> tuple[registry.ArtifactSnapshot, _CleanState]:
    snapshot = _read_reference(
        value,
        root=paths.root,
        name="epoch drain capsule",
        maximum_bytes=MAX_MANIFEST_BYTES,
    )
    try:
        payload = registry._decode_json(snapshot.raw, name="epoch drain capsule")  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc
    if snapshot.raw != _canonical_bytes(payload):
        raise EpochDrainIntegrityError("Epoch drain capsule is not canonical")
    if snapshot.path != paths.capsules / f"{snapshot.sha256}.json":
        raise EpochDrainIntegrityError("Epoch drain capsule path changed")
    expected_keys = {
        "schema_version",
        "profile_id",
        "profile_sha256",
        "profile_object",
        "implementation_sha256",
        "implementation_object",
        "intent_prefix",
        "registry_event_sequence_id",
        "registry_event_entry_sha256",
        "candidate_id",
        "slot_id",
        "candidate_receipt",
        "r1_capsule",
        "preparation_event_sequence_id",
        "preparation_event_entry_sha256",
        "preparation_event_type",
        "executable_capsule",
        "executable_tree_sha256",
        "smoke_receipt",
        "old_live_epoch_id",
        "live_event_count",
        "live_terminal_sha256",
        "live_entry_sha256s",
        "guard_record_count",
        "trusted_time_record_count",
        "shadow_event_count",
        "shadow_terminal_sha256",
        "shadow_entry_sha256s",
        "issue_route_inventory_sha256",
        "issue_route_inventory",
        "outcome_registry_inventory",
        "guard_inventory",
        "trusted_time_inventory",
        "source_authority",
        "canonical_issue_route",
        "lock_order",
        "machine_only",
        "snapshot_role",
        "candidate_at_intent",
        "activation_candidate_selected",
        "lifecycle_state",
        "old_epoch_drain_implemented",
        "active_epoch_switch_implemented",
        "automatic_epoch_rotation_implemented",
        "trusted_anchor_receipt_verified",
        "e2_live_evidence_eligible",
        "real_activation_ready",
        "formal_warning_output",
    }
    _exact(payload, expected_keys, name="epoch drain capsule")
    profile_object = _read_reference(
        payload["profile_object"], root=paths.root, name="drain profile object"
    )
    implementation_object = _read_reference(
        payload["implementation_object"],
        root=paths.root,
        name="drain implementation object",
    )
    intent_prefix = _read_reference(
        payload["intent_prefix"],
        root=paths.root,
        name="drain intent prefix",
        maximum_bytes=MAX_MANIFEST_BYTES,
    )
    expected_profile_object = (
        paths.objects / f"{profile_object.sha256}.epoch-drain-profile.json"
    )
    expected_implementation_object = (
        paths.objects / f"{implementation_object.sha256}.epoch-drain-implementation.py"
    )
    expected_intent_prefix = (
        paths.objects / f"{intent_prefix.sha256}.epoch-drain-intent-prefix.json"
    )
    if (
        profile_object.path != expected_profile_object
        or implementation_object.path != expected_implementation_object
        or intent_prefix.path != expected_intent_prefix
        or profile_object.sha256 != profile["_profile_sha256"]
        or payload["profile_sha256"] != profile["_profile_sha256"]
        or implementation_object.sha256 != payload["implementation_sha256"]
    ):
        raise EpochDrainIntegrityError("Drain capsule provenance binding changed")
    if require_current_implementation and (
        implementation_object.sha256 != profile["_implementation_sha256"]
    ):
        raise EpochDrainIntegrityError("Current drain implementation changed")
    count_names = (
        "live_event_count",
        "guard_record_count",
        "trusted_time_record_count",
        "shadow_event_count",
    )
    for name in count_names:
        if type(payload[name]) is not int or payload[name] < 0:
            raise EpochDrainIntegrityError(f"Drain capsule {name} is invalid")
    live_entries_value = payload["live_entry_sha256s"]
    shadow_entries_value = payload["shadow_entry_sha256s"]
    if not isinstance(live_entries_value, list) or not isinstance(
        shadow_entries_value, list
    ):
        raise EpochDrainIntegrityError("Drain capsule ledger entries changed")
    live_entries = tuple(
        _hash(value, name="capsule live entry") for value in live_entries_value
    )
    shadow_entries = tuple(
        _hash(value, name="capsule shadow entry") for value in shadow_entries_value
    )
    if (
        len(live_entries) != payload["live_event_count"]
        or len(shadow_entries) != payload["shadow_event_count"]
        or not live_entries
        or live_entries[-1] != payload["live_terminal_sha256"]
        or (shadow_entries[-1] if shadow_entries else ZERO_HASH)
        != payload["shadow_terminal_sha256"]
    ):
        raise EpochDrainIntegrityError("Drain capsule ledger terminal changed")
    issue_inventory = _manifest_records(
        payload["issue_route_inventory"], name="capsule issue inventory"
    )
    outcome_inventory = _manifest_records(
        payload["outcome_registry_inventory"], name="capsule outcome inventory"
    )
    guard_inventory = _manifest_records(
        payload["guard_inventory"], name="capsule guard inventory"
    )
    trusted_inventory = _manifest_records(
        payload["trusted_time_inventory"], name="capsule trusted-time inventory"
    )
    source_authority = _exact(
        payload["source_authority"],
        {
            "outcome_source_id",
            "watermark",
            "exported_at_utc",
            "semantic_manifest",
            "activation_manifest",
            "snapshot_receipt",
            "snapshot_sequence_id",
            "records",
            "revision_ids_by_date",
        },
        name="capsule source authority",
    )
    if (
        not _inventory_subset_by_key(
            issue_inventory, issue_inventory, key="target_date"
        )
        or not _outcome_inventory_extends(outcome_inventory, outcome_inventory)
        or not _inventory_subset_by_key(
            guard_inventory, guard_inventory, key="target_date"
        )
        or not _inventory_subset_by_key(
            trusted_inventory, trusted_inventory, key="target_date"
        )
        or not _source_authority_extends(source_authority, source_authority)
    ):
        raise EpochDrainIntegrityError("Drain capsule inventory changed")
    _verify_source_authority_objects(paths, source_authority)
    issue_inventory_sha256 = _hash(
        payload["issue_route_inventory_sha256"],
        name="capsule issue-route inventory",
    )
    if issue_inventory_sha256 != payload["issue_route_inventory"]["sha256"]:
        raise EpochDrainIntegrityError("Drain capsule issue inventory digest changed")
    start_clean = _CleanState(
        old_live_epoch_id=_text(
            payload["old_live_epoch_id"], name="capsule old live epoch"
        ),
        live_event_count=payload["live_event_count"],
        live_terminal_sha256=_hash(
            payload["live_terminal_sha256"], name="capsule live terminal"
        ),
        guard_record_count=payload["guard_record_count"],
        trusted_time_record_count=payload["trusted_time_record_count"],
        shadow_event_count=payload["shadow_event_count"],
        shadow_terminal_sha256=_hash(
            payload["shadow_terminal_sha256"], name="capsule shadow terminal"
        ),
        issue_route_inventory_sha256=issue_inventory_sha256,
        issue_route_inventory=issue_inventory,
        outcome_registry_inventory=outcome_inventory,
        guard_inventory=guard_inventory,
        trusted_time_inventory=trusted_inventory,
        source_authority=copy.deepcopy(source_authority),
        live_entry_sha256s=live_entries,
        shadow_entry_sha256s=shadow_entries,
    )
    if start_clean.live_event_count < 1:
        raise EpochDrainIntegrityError("Drain-start snapshot has no live genesis")
    if clean is not None and not _clean_state_extends(start_clean, clean):
        raise EpochDrainIntegrityError(
            "Current old-runtime state does not extend the drain-start snapshot"
        )
    expected_intent_prefix_raw = _canonical_bytes(
        _intent_prefix_payload(profile, start_clean)
    )
    if intent_prefix.raw != expected_intent_prefix_raw:
        raise EpochDrainIntegrityError("Drain intent prefix semantics changed")
    expected = _capsule_payload(
        profile,
        paths,
        authority,
        start_clean,
        profile_object,
        implementation_object,
        intent_prefix,
    )
    if payload != expected:
        raise EpochDrainIntegrityError("Drain capsule semantics changed")
    for captured, name in (
        (profile_object, "adopted epoch drain profile object"),
        (implementation_object, "adopted epoch drain implementation object"),
        (intent_prefix, "adopted epoch drain intent prefix"),
    ):
        _adopt_snapshot_durable(captured, root=paths.root, name=name)
    snapshot = _adopt_snapshot_durable(
        snapshot, root=paths.root, name="adopted epoch drain capsule"
    )
    return snapshot, start_clean


def _directory_identity(path: Path, *, name: str) -> dict[str, int]:
    try:
        value = os.lstat(path)
    except OSError as exc:
        raise EpochDrainIntegrityError(f"Cannot inspect {name}") from exc
    if not stat.S_ISDIR(value.st_mode):
        raise EpochDrainIntegrityError(f"{name} is not a real directory")
    return {
        "device": value.st_dev,
        "inode": value.st_ino,
        "mode": stat.S_IMODE(value.st_mode),
    }


def _acl_library() -> ctypes.CDLL:
    library = ctypes.CDLL(None, use_errno=True)
    required = (
        "acl_from_text",
        "acl_init",
        "acl_set_fd",
        "acl_set_fd_np",
        "acl_get_fd_np",
        "acl_to_text",
        "acl_free",
    )
    if any(getattr(library, name, None) is None for name in required):
        raise EpochDrainIntegrityError(
            "Darwin extended ACL API is unavailable; an unfenced fallback is forbidden"
        )
    library.acl_from_text.argtypes = [ctypes.c_char_p]
    library.acl_from_text.restype = ctypes.c_void_p
    library.acl_init.argtypes = [ctypes.c_int]
    library.acl_init.restype = ctypes.c_void_p
    library.acl_set_fd.argtypes = [ctypes.c_int, ctypes.c_void_p]
    library.acl_set_fd.restype = ctypes.c_int
    library.acl_set_fd_np.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    library.acl_set_fd_np.restype = ctypes.c_int
    library.acl_get_fd_np.argtypes = [ctypes.c_int, ctypes.c_int]
    library.acl_get_fd_np.restype = ctypes.c_void_p
    library.acl_to_text.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ssize_t),
    ]
    library.acl_to_text.restype = ctypes.c_void_p
    library.acl_free.argtypes = [ctypes.c_void_p]
    library.acl_free.restype = ctypes.c_int
    return library


def _read_fence_acl(descriptor: int, *, allow_missing: bool = False) -> bytes | None:
    library = _acl_library()
    ctypes.set_errno(0)
    acl = library.acl_get_fd_np(descriptor, ACL_TYPE_EXTENDED)
    if not acl:
        error = ctypes.get_errno()
        if allow_missing and error == errno.ENOENT:
            return None
        raise EpochDrainIntegrityError(
            f"Cannot read the issue-route fence ACL (errno {error})"
        )
    text_pointer: int | None = None
    try:
        length = ctypes.c_ssize_t()
        ctypes.set_errno(0)
        text_pointer = library.acl_to_text(acl, ctypes.byref(length))
        if not text_pointer or length.value < 0:
            error = ctypes.get_errno()
            raise EpochDrainIntegrityError(
                f"Cannot serialize the issue-route fence ACL (errno {error})"
            )
        return ctypes.string_at(text_pointer, length.value)
    finally:
        if text_pointer:
            library.acl_free(text_pointer)
        library.acl_free(acl)


def _install_fence_acl(descriptor: int) -> None:
    library = _acl_library()
    ctypes.set_errno(0)
    acl = library.acl_from_text(FENCE_ACL_TEXT)
    if not acl:
        error = ctypes.get_errno()
        raise EpochDrainIntegrityError(
            f"Cannot parse the reviewed issue-route fence ACL (errno {error})"
        )
    try:
        ctypes.set_errno(0)
        if library.acl_set_fd(descriptor, acl) != 0:
            error = ctypes.get_errno()
            raise EpochDrainIntegrityError(
                f"Cannot install the issue-route fence ACL (errno {error})"
            )
    finally:
        library.acl_free(acl)
    if _read_fence_acl(descriptor) != FENCE_ACL_TEXT:
        raise EpochDrainIntegrityError("Issue-route fence ACL changed after install")


def _remove_fence_acl(descriptor: int) -> None:
    existing = _read_fence_acl(descriptor, allow_missing=True)
    if existing is None:
        return
    if existing != FENCE_ACL_TEXT:
        raise EpochDrainIntegrityError("Cannot remove an unreviewed issue-route ACL")
    library = _acl_library()
    ctypes.set_errno(0)
    empty_acl = library.acl_init(1)
    if not empty_acl:
        error = ctypes.get_errno()
        raise EpochDrainIntegrityError(
            f"Cannot allocate an empty issue-route ACL (errno {error})"
        )
    try:
        result = library.acl_set_fd_np(descriptor, empty_acl, ACL_TYPE_EXTENDED)
        error = ctypes.get_errno()
    finally:
        library.acl_free(empty_acl)
    if result != 0:
        raise EpochDrainIntegrityError(
            f"Cannot remove the issue-route fence ACL (errno {error})"
        )
    if _read_fence_acl(descriptor, allow_missing=True) is not None:
        raise EpochDrainIntegrityError("Issue-route fence ACL remained after removal")


def _probe_add_file_denied(path: Path, *, name: str) -> None:
    descriptor = _open_directory(path, name=name)
    probe = f".drain-write-probe-{os.getpid()}"
    created: int | None = None
    try:
        try:
            created = os.open(
                probe,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=descriptor,
            )
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EPERM, errno.EROFS}:
                raise EpochDrainIntegrityError(
                    f"{name} add-file probe failed ambiguously"
                ) from exc
        else:
            os.close(created)
            created = None
            os.unlink(probe, dir_fd=descriptor)
            raise EpochDrainIntegrityError(f"{name} accepted a new file")
    finally:
        if created is not None:
            os.close(created)
        os.close(descriptor)


def _verify_fence_acl(path: Path, *, name: str) -> None:
    descriptor = _open_directory(path, name=name)
    try:
        if _read_fence_acl(descriptor) != FENCE_ACL_TEXT:
            raise EpochDrainIntegrityError(f"{name} ACL changed")
    finally:
        os.close(descriptor)
    _probe_add_file_denied(path, name=name)


def _unfenced_route_identity(paths: DrainPaths) -> dict[str, int]:
    try:
        registry._mkdir(paths.active_root, root=paths.active_root)  # noqa: SLF001
        if not paths.issue_inbox.exists() and not paths.issue_inbox.is_symlink():
            registry._mkdir(paths.issue_inbox, root=paths.active_root)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc
    route = _directory_identity(paths.issue_inbox, name="canonical issue route")
    descriptor = _open_directory(
        paths.issue_inbox, name="unregistered canonical issue route"
    )
    try:
        if _read_fence_acl(descriptor, allow_missing=True) is not None:
            raise EpochDrainIntegrityError(
                "Canonical issue route has an unregistered extended ACL"
            )
    finally:
        os.close(descriptor)
    return route


def _fence_prepare_path(paths: DrainPaths, candidate_id: str) -> Path:
    _hash(candidate_id, name="candidate id")
    return paths.fence_prepares / f"{candidate_id}.json"


def _capsule_implementation_sha256(
    capsule: registry.ArtifactSnapshot,
) -> str:
    try:
        payload = registry._decode_json(capsule.raw, name="epoch drain capsule")  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc
    if capsule.raw != _canonical_bytes(payload):
        raise EpochDrainIntegrityError("Epoch drain capsule is not canonical")
    return _hash(
        payload.get("implementation_sha256"),
        name="capsule captured implementation",
    )


def _fence_prepare_payload(
    profile: Mapping[str, Any],
    paths: DrainPaths,
    authority: _Authority,
    capsule: registry.ArtifactSnapshot,
    route_identity: Mapping[str, int],
) -> dict[str, object]:
    candidate_id = authority.r1_event["candidate_id"]
    return {
        "schema_version": profile["protocol"]["fence_prepare_schema_version"],
        "profile_sha256": profile["_profile_sha256"],
        # The durable transaction follows the implementation captured in the
        # verified capsule.  Requiring the current module here would make a
        # committed event or exchanged orphan unreplayable after a code update;
        # prepared transactions enforce current code separately in
        # _verify_capsule(require_current_implementation=True).
        "implementation_sha256": _capsule_implementation_sha256(capsule),
        "candidate_id": candidate_id,
        "slot_id": authority.r1_event["slot_id"],
        "registry_event_entry_sha256": authority.r1_event["entry_sha256"],
        "preparation_event_entry_sha256": authority.preparation_event["entry_sha256"],
        "drain_capsule": _artifact_reference(capsule, paths.root),
        "canonical_issue_route": str(paths.issue_inbox),
        "old_route_identity": dict(route_identity),
        "tombstone_path": str(paths.tombstones / candidate_id),
        "fence_acl_sha256": FENCE_ACL_SHA256,
        "pre_swap_mode": 0o755,
        "atomic_exchange": profile["protocol"]["atomic_exchange"],
        "machine_only": True,
        "candidate_at_intent": True,
        "activation_candidate_selected": False,
    }


def _load_single_fence_prepare(
    paths: DrainPaths,
) -> registry.ArtifactSnapshot | None:
    if not paths.fence_prepares.exists() and not paths.fence_prepares.is_symlink():
        return None
    try:
        if not stat.S_ISDIR(os.lstat(paths.fence_prepares).st_mode):
            raise EpochDrainIntegrityError(
                "Drain fence-prepare path is not a real directory"
            )
        entries = sorted(paths.fence_prepares.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise EpochDrainIntegrityError("Cannot inspect drain fence prepares") from exc
    if not entries:
        return None
    if len(entries) != 1:
        raise EpochDrainIntegrityError("Drain fence-prepare registry is branched")
    try:
        return registry._read_regular(  # noqa: SLF001
            entries[0], name="drain fence prepare", maximum_bytes=MAX_CONTROL_BYTES
        )
    except registry.EpochRegistryError as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc


def _publish_fence_prepare(
    profile: Mapping[str, Any],
    paths: DrainPaths,
    authority: _Authority,
    capsule: registry.ArtifactSnapshot,
    route_identity: Mapping[str, int],
) -> registry.ArtifactSnapshot:
    existing = _load_single_fence_prepare(paths)
    if existing is None and (
        paths.tombstones.exists() or paths.tombstones.is_symlink()
    ):
        try:
            if not stat.S_ISDIR(os.lstat(paths.tombstones).st_mode) or any(
                paths.tombstones.iterdir()
            ):
                raise EpochDrainIntegrityError(
                    "Unmarked drain tombstone cannot become transaction authority"
                )
        except OSError as exc:
            raise EpochDrainIntegrityError(
                "Cannot inspect pre-transaction tombstones"
            ) from exc
    payload = _fence_prepare_payload(profile, paths, authority, capsule, route_identity)
    try:
        return _publish_once_durable(
            _fence_prepare_path(paths, authority.r1_event["candidate_id"]),
            _canonical_bytes(payload),
            root=paths.root,
            name="drain fence prepare",
        )
    except registry.EpochRegistryError as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc


def _verify_fence_prepare(
    profile: Mapping[str, Any],
    paths: DrainPaths,
    authority: _Authority,
    capsule: registry.ArtifactSnapshot,
    snapshot: registry.ArtifactSnapshot,
    *,
    require_unexchanged_route: bool,
) -> dict[str, Any]:
    registered = _load_single_fence_prepare(paths)
    if registered is None or registered != snapshot:
        raise EpochDrainIntegrityError(
            "Drain fence-prepare namespace lost its unique registered authority"
        )
    if snapshot.path != _fence_prepare_path(paths, authority.r1_event["candidate_id"]):
        raise EpochDrainIntegrityError("Drain fence-prepare path changed")
    try:
        payload = registry._decode_json(snapshot.raw, name="drain fence prepare")  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc
    if snapshot.raw != _canonical_bytes(payload):
        raise EpochDrainIntegrityError("Drain fence prepare is not canonical")
    route_identity = _exact(
        payload.get("old_route_identity"),
        {"device", "inode", "mode"},
        name="fence-prepare old route identity",
    )
    if any(type(value) is not int or value < 0 for value in route_identity.values()):
        raise EpochDrainIntegrityError("Fence-prepare route identity changed")
    expected = _fence_prepare_payload(
        profile, paths, authority, capsule, route_identity
    )
    if payload != expected:
        raise EpochDrainIntegrityError("Drain fence-prepare semantics changed")
    if require_unexchanged_route and _unfenced_route_identity(paths) != route_identity:
        raise EpochDrainIntegrityError(
            "Canonical route changed after drain fence preparation"
        )
    _adopt_snapshot_durable(
        snapshot, root=paths.root, name="adopted drain fence prepare"
    )
    return payload


def _ensure_route_and_tombstone(
    paths: DrainPaths,
    candidate_id: str,
    prepare_payload: Mapping[str, Any],
) -> tuple[Path, dict[str, int], dict[str, int]]:
    registered_prepare = _load_single_fence_prepare(paths)
    if registered_prepare is None:
        raise EpochDrainIntegrityError(
            "Drain tombstone creation lacks a durable fence prepare"
        )
    try:
        registered_payload = registry._decode_json(  # noqa: SLF001
            registered_prepare.raw, name="drain fence prepare"
        )
    except registry.EpochRegistryError as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc
    if registered_payload != prepare_payload:
        raise EpochDrainIntegrityError(
            "Drain tombstone fence prepare changed before recovery"
        )
    try:
        route = _unfenced_route_identity(paths)
        if route != prepare_payload.get("old_route_identity"):
            raise EpochDrainIntegrityError(
                "Fence prepare does not authorize the current canonical route"
            )
        registry._mkdir(paths.tombstones, root=paths.root)  # noqa: SLF001
        tombstone_parent = _open_directory(
            paths.tombstones, name="drain tombstone namespace"
        )
        try:
            if _read_fence_acl(tombstone_parent, allow_missing=True) is not None:
                raise EpochDrainIntegrityError(
                    "Drain tombstone namespace has an inherited extended ACL"
                )
        finally:
            os.close(tombstone_parent)
        existing_tombstones = sorted(
            paths.tombstones.iterdir(), key=lambda item: item.name
        )
        tombstone = paths.tombstones / candidate_id
        if str(tombstone) != prepare_payload.get("tombstone_path"):
            raise EpochDrainIntegrityError("Fence prepare tombstone binding changed")
        created = not existing_tombstones
        if existing_tombstones and existing_tombstones != [tombstone]:
            raise EpochDrainIntegrityError(
                "Unregistered drain tombstone namespace is not the current candidate"
            )
        if created:
            tombstone.mkdir(mode=0o755)
            registry._fsync_directory(  # noqa: SLF001
                tombstone.parent, name="drain tombstone creation"
            )
        recovered_identity = _directory_identity(
            tombstone, name="empty issue-route tombstone"
        )
        if recovered_identity["mode"] & ~0o755 or any(tombstone.iterdir()):
            raise EpochDrainIntegrityError(
                "Crash-orphan drain tombstone is not an empty permission subset"
            )
        descriptor = _open_directory(tombstone, name="empty issue-route tombstone")
        try:
            if recovered_identity["mode"] != 0o755:
                os.fchmod(descriptor, 0o755)
            existing_acl = _read_fence_acl(descriptor, allow_missing=True)
            if existing_acl is None:
                _install_fence_acl(descriptor)
            elif existing_acl != FENCE_ACL_TEXT:
                raise EpochDrainIntegrityError(
                    "Crash-orphan drain tombstone ACL is not the reviewed fence"
                )
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        registry._fsync_directory(  # noqa: SLF001
            tombstone.parent, name="drain tombstone recovery"
        )
        fence = _directory_identity(tombstone, name="empty issue-route tombstone")
    except (OSError, registry.EpochRegistryError) as exc:
        raise EpochDrainIntegrityError(
            "Cannot prepare the issue-route tombstone"
        ) from exc
    if any(tombstone.iterdir()):
        raise EpochDrainIntegrityError(
            "Pre-exchange issue-route tombstone is not empty"
        )
    if fence["mode"] != 0o755:
        raise EpochDrainIntegrityError("Pre-exchange tombstone mode changed")
    _verify_fence_acl(tombstone, name="pre-exchange issue-route tombstone")
    if route["device"] != fence["device"]:
        raise EpochDrainIntegrityError("Issue route and tombstone are cross-filesystem")
    return tombstone, route, fence


def _intent_payload(
    profile: Mapping[str, Any],
    paths: DrainPaths,
    authority: _Authority,
    clean: _CleanState,
    capsule: registry.ArtifactSnapshot,
    fence_prepare: registry.ArtifactSnapshot,
    tombstone: Path,
    route_identity: Mapping[str, int],
    fence_identity: Mapping[str, int],
) -> dict[str, object]:
    return {
        "schema_version": profile["protocol"]["intent_schema_version"],
        "profile_sha256": profile["_profile_sha256"],
        "candidate_id": authority.r1_event["candidate_id"],
        "slot_id": authority.r1_event["slot_id"],
        "registry_event_entry_sha256": authority.r1_event["entry_sha256"],
        "preparation_event_entry_sha256": authority.preparation_event["entry_sha256"],
        "drain_capsule": _artifact_reference(capsule, paths.root),
        "fence_prepare": _artifact_reference(fence_prepare, paths.root),
        "old_live_epoch_id": clean.old_live_epoch_id,
        "issue_route_inventory_sha256": clean.issue_route_inventory_sha256,
        "canonical_issue_route": str(paths.issue_inbox),
        "archive_route": str(tombstone),
        "old_route_identity": dict(route_identity),
        "fence_identity": dict(fence_identity),
        "fence_acl_sha256": FENCE_ACL_SHA256,
        "atomic_exchange": profile["protocol"]["atomic_exchange"],
        "lock_order": list(LOCK_ORDER),
        "machine_only": True,
        "candidate_at_intent": True,
        "activation_candidate_selected": False,
        "active_epoch_switch_implemented": False,
        "automatic_epoch_rotation_implemented": False,
        "trusted_anchor_receipt_verified": False,
        "e2_live_evidence_eligible": False,
        "real_activation_ready": False,
        "formal_warning_output": False,
    }


def _intent_path(paths: DrainPaths, candidate_id: str) -> Path:
    _hash(candidate_id, name="candidate id")
    return paths.intents / f"{candidate_id}.json"


def _publish_intent(
    profile: Mapping[str, Any],
    paths: DrainPaths,
    authority: _Authority,
    clean: _CleanState,
    capsule: registry.ArtifactSnapshot,
    fence_prepare: registry.ArtifactSnapshot,
    tombstone: Path,
    route_identity: Mapping[str, int],
    fence_identity: Mapping[str, int],
) -> registry.ArtifactSnapshot:
    payload = _intent_payload(
        profile,
        paths,
        authority,
        clean,
        capsule,
        fence_prepare,
        tombstone,
        route_identity,
        fence_identity,
    )
    try:
        return _publish_once_durable(
            _intent_path(paths, authority.r1_event["candidate_id"]),
            _canonical_bytes(payload),
            root=paths.root,
            name="epoch drain fence intent",
        )
    except registry.EpochRegistryError as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc


def _load_single_intent(paths: DrainPaths) -> registry.ArtifactSnapshot | None:
    if not paths.intents.exists() and not paths.intents.is_symlink():
        return None
    try:
        mode = os.lstat(paths.intents).st_mode
    except OSError as exc:
        raise EpochDrainIntegrityError("Cannot inspect drain intents") from exc
    if not stat.S_ISDIR(mode):
        raise EpochDrainIntegrityError("Drain intents path is not a real directory")
    entries = sorted(paths.intents.iterdir(), key=lambda item: item.name)
    if not entries:
        return None
    if len(entries) != 1:
        raise EpochDrainIntegrityError("Drain intent registry is branched or polluted")
    try:
        return registry._read_regular(  # noqa: SLF001
            entries[0], name="epoch drain fence intent", maximum_bytes=MAX_CONTROL_BYTES
        )
    except registry.EpochRegistryError as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc


def _verify_registered_tombstone_namespace(paths: DrainPaths, registered: Path) -> None:
    if registered.parent != paths.tombstones:
        raise EpochDrainIntegrityError("Registered tombstone escaped its namespace")
    try:
        if not stat.S_ISDIR(os.lstat(paths.tombstones).st_mode):
            raise EpochDrainIntegrityError(
                "Registered tombstone namespace is not a real directory"
            )
        entries = sorted(paths.tombstones.iterdir(), key=lambda item: item.name)
        if entries != [registered]:
            raise EpochDrainIntegrityError(
                "Registered tombstone is not the unique namespace entry"
            )
        if not stat.S_ISDIR(os.lstat(registered).st_mode):
            raise EpochDrainIntegrityError(
                "Registered tombstone is not a real directory"
            )
    except EpochDrainError:
        raise
    except OSError as exc:
        raise EpochDrainIntegrityError(
            "Cannot inspect the registered tombstone namespace"
        ) from exc


def _verify_intent(
    profile: Mapping[str, Any],
    paths: DrainPaths,
    authority: _Authority,
    clean: _CleanState | None,
    snapshot: registry.ArtifactSnapshot,
    *,
    require_current_implementation: bool = True,
) -> tuple[dict[str, Any], registry.ArtifactSnapshot, _CleanState]:
    expected_path = _intent_path(paths, authority.r1_event["candidate_id"])
    if snapshot.path != expected_path:
        raise EpochDrainIntegrityError("Drain intent path changed")
    try:
        payload = registry._decode_json(snapshot.raw, name="epoch drain fence intent")  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc
    capsule, start_clean = _verify_capsule(
        profile,
        paths,
        authority,
        clean,
        payload.get("drain_capsule"),
        require_current_implementation=require_current_implementation,
    )
    fence_prepare = _read_reference(
        payload.get("fence_prepare"), root=paths.root, name="drain fence prepare"
    )
    _verify_fence_prepare(
        profile,
        paths,
        authority,
        capsule,
        fence_prepare,
        require_unexchanged_route=False,
    )
    route_identity = _exact(
        payload.get("old_route_identity"),
        {"device", "inode", "mode"},
        name="intent old route identity",
    )
    fence_identity = _exact(
        payload.get("fence_identity"),
        {"device", "inode", "mode"},
        name="intent fence identity",
    )
    for identity in (route_identity, fence_identity):
        if any(type(value) is not int or value < 0 for value in identity.values()):
            raise EpochDrainIntegrityError("Intent directory identity changed")
    if (
        _hash(payload.get("fence_acl_sha256"), name="intent fence ACL")
        != FENCE_ACL_SHA256
    ):
        raise EpochDrainIntegrityError("Intent fence ACL binding changed")
    tombstone = Path(_text(payload.get("archive_route"), name="intent archive route"))
    _verify_registered_tombstone_namespace(paths, tombstone)
    expected = _intent_payload(
        profile,
        paths,
        authority,
        start_clean,
        capsule,
        fence_prepare,
        tombstone,
        route_identity,
        fence_identity,
    )
    if (
        payload != expected
        or tombstone != paths.tombstones / authority.r1_event["candidate_id"]
    ):
        raise EpochDrainIntegrityError("Drain intent semantics changed")
    _adopt_snapshot_durable(
        snapshot, root=paths.root, name="adopted epoch drain fence intent"
    )
    return payload, capsule, start_clean


def _same_identity(actual: Mapping[str, int], expected: object) -> bool:
    return isinstance(expected, dict) and all(
        actual.get(key) == expected.get(key) for key in ("device", "inode")
    )


def _route_state(paths: DrainPaths, intent: Mapping[str, Any]) -> str:
    archive = Path(str(intent["archive_route"]))
    current = _directory_identity(paths.issue_inbox, name="canonical issue route")
    archived = _directory_identity(archive, name="archived old issue route")
    if _same_identity(current, intent["old_route_identity"]) and _same_identity(
        archived, intent["fence_identity"]
    ):
        return "prepared"
    if _same_identity(current, intent["fence_identity"]) and _same_identity(
        archived, intent["old_route_identity"]
    ):
        return "exchanged"
    raise EpochDrainIntegrityError(
        "Issue-route intent does not match either exact exchange state"
    )


def _verify_fence_marker_shape(directory: Path, *, allow_absent: bool) -> None:
    try:
        entries = sorted(directory.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise EpochDrainIntegrityError(
            "Cannot inspect issue-route fence contents"
        ) from exc
    if not entries and allow_absent:
        return
    marker = directory / ARMED_ATTEMPT_MARKER_NAME
    if entries != [marker]:
        raise EpochDrainIntegrityError(
            "Issue-route fence does not contain exactly its armed-attempt marker"
        )
    try:
        marker_mode = os.lstat(marker).st_mode
    except OSError as exc:
        raise EpochDrainIntegrityError("Cannot inspect armed-attempt marker") from exc
    if (
        not stat.S_ISREG(marker_mode)
        or stat.S_IMODE(marker_mode) != ARMED_ATTEMPT_MARKER_MODE
    ):
        raise EpochDrainIntegrityError("Armed-attempt marker mode changed")


def _verify_prepared_fence(
    paths: DrainPaths,
    intent: Mapping[str, Any],
    *,
    recover_hardened_fence: bool = False,
) -> None:
    if _route_state(paths, intent) != "prepared":
        raise EpochDrainIntegrityError("Issue-route fence is not in prepared state")
    archive = Path(str(intent["archive_route"]))
    current = _directory_identity(paths.issue_inbox, name="canonical issue route")
    fence = _directory_identity(archive, name="prepared issue-route fence")
    if (
        current["mode"] != intent["old_route_identity"]["mode"]
        or intent["fence_identity"]["mode"] != 0o755
    ):
        raise EpochDrainIntegrityError("Prepared issue-route modes changed")
    _verify_fence_marker_shape(archive, allow_absent=True)
    _verify_fence_acl(archive, name="prepared issue-route fence")
    if recover_hardened_fence and fence["mode"] == 0o555:
        descriptor = _open_directory(archive, name="rolled-back hardened fence")
        try:
            os.fchmod(descriptor, 0o755)
            os.fsync(descriptor)
        except OSError as exc:
            raise EpochDrainIntegrityError(
                "Cannot prepare rolled-back fence for atomic re-exchange"
            ) from exc
        finally:
            os.close(descriptor)
        fence = _directory_identity(archive, name="rolled-back prepared fence")
        _verify_fence_acl(archive, name="rolled-back prepared fence")
    if fence["mode"] != 0o755:
        raise EpochDrainIntegrityError("Prepared issue-route fence mode changed")


def _open_directory(path: Path, *, name: str) -> int:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        named = os.lstat(path)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not stat.S_ISDIR(named.st_mode)
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise EpochDrainIntegrityError(f"{name} is not one stable directory")
        return descriptor
    except EpochDrainError:
        try:
            os.close(descriptor)
        except (NameError, OSError):
            pass
        raise
    except OSError as exc:
        try:
            os.close(descriptor)
        except (NameError, OSError):
            pass
        raise EpochDrainIntegrityError(f"Cannot open {name}") from exc


def _rename_exchange(first: Path, second: Path) -> None:
    """Use Darwin's atomic directory exchange; there is intentionally no fallback."""

    first_parent = _open_directory(first.parent, name="issue-route parent")
    second_parent = _open_directory(second.parent, name="tombstone parent")
    try:
        first_before = os.stat(first.name, dir_fd=first_parent, follow_symlinks=False)
        second_before = os.stat(
            second.name, dir_fd=second_parent, follow_symlinks=False
        )
        if (
            not stat.S_ISDIR(first_before.st_mode)
            or not stat.S_ISDIR(second_before.st_mode)
            or first_before.st_dev != second_before.st_dev
        ):
            raise EpochDrainIntegrityError(
                "Atomic exchange operands are not same-device directories"
            )
        library = ctypes.CDLL(None, use_errno=True)
        function = getattr(library, "renameatx_np", None)
        if function is None:
            raise EpochDrainIntegrityError(
                "Darwin renameatx_np is unavailable; ordinary rename is forbidden"
            )
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        function.restype = ctypes.c_int
        ctypes.set_errno(0)
        result = function(
            first_parent,
            os.fsencode(first.name),
            second_parent,
            os.fsencode(second.name),
            RENAME_SWAP,
        )
        if result != 0:
            error = ctypes.get_errno()
            raise EpochDrainIntegrityError(
                f"renameatx_np(RENAME_SWAP) failed with errno {error}"
            )
        first_after = os.stat(first.name, dir_fd=first_parent, follow_symlinks=False)
        second_after = os.stat(second.name, dir_fd=second_parent, follow_symlinks=False)
        if (first_after.st_dev, first_after.st_ino) != (
            second_before.st_dev,
            second_before.st_ino,
        ) or (second_after.st_dev, second_after.st_ino) != (
            first_before.st_dev,
            first_before.st_ino,
        ):
            raise EpochDrainIntegrityError("Atomic exchange inode postcondition failed")
        os.fsync(first_parent)
        if second_parent != first_parent:
            os.fsync(second_parent)
    except EpochDrainError:
        raise
    except OSError as exc:
        raise EpochDrainIntegrityError(
            "Cannot execute atomic issue-route exchange"
        ) from exc
    finally:
        os.close(second_parent)
        os.close(first_parent)


def _verify_fenced_route(paths: DrainPaths, intent: Mapping[str, Any]) -> None:
    if _route_state(paths, intent) != "exchanged":
        raise EpochDrainIntegrityError("Canonical issue route is not exchanged")
    identity = _directory_identity(paths.issue_inbox, name="canonical fenced route")
    archive = Path(str(intent["archive_route"]))
    archived_identity = _directory_identity(archive, name="archived old issue route")
    if identity["mode"] != 0o555:
        raise EpochDrainIntegrityError("Canonical issue route mode is not exact 0555")
    if archived_identity["mode"] != intent["old_route_identity"]["mode"]:
        raise EpochDrainIntegrityError("Archived old issue route mode changed")
    _verify_fence_marker_shape(paths.issue_inbox, allow_absent=False)
    _verify_fence_acl(paths.issue_inbox, name="canonical fenced issue route")


def _durably_reverify_exchange(paths: DrainPaths, intent: Mapping[str, Any]) -> None:
    """Re-open the exchanged operands and make their directory entries durable."""

    _verify_fenced_route(paths, intent)
    archive = Path(str(intent["archive_route"]))
    issue_descriptor = _open_directory(
        paths.issue_inbox, name="durable canonical issue-route fence"
    )
    archive_descriptor = _open_directory(
        archive, name="durable archived old issue route"
    )
    first_parent = _open_directory(
        paths.issue_inbox.parent, name="durable issue-route parent"
    )
    second_parent = _open_directory(archive.parent, name="durable tombstone parent")
    try:
        issue_stat = os.fstat(issue_descriptor)
        archive_stat = os.fstat(archive_descriptor)
        if (
            (issue_stat.st_dev, issue_stat.st_ino)
            != (
                intent["fence_identity"]["device"],
                intent["fence_identity"]["inode"],
            )
            or stat.S_IMODE(issue_stat.st_mode) != 0o555
            or (archive_stat.st_dev, archive_stat.st_ino)
            != (
                intent["old_route_identity"]["device"],
                intent["old_route_identity"]["inode"],
            )
            or stat.S_IMODE(archive_stat.st_mode)
            != intent["old_route_identity"]["mode"]
        ):
            raise EpochDrainIntegrityError(
                "Exchanged issue-route identities changed before commit"
            )
        named_issue = os.stat(
            paths.issue_inbox.name,
            dir_fd=first_parent,
            follow_symlinks=False,
        )
        named_archive = os.stat(
            archive.name,
            dir_fd=second_parent,
            follow_symlinks=False,
        )
        if (named_issue.st_dev, named_issue.st_ino) != (
            issue_stat.st_dev,
            issue_stat.st_ino,
        ) or (named_archive.st_dev, named_archive.st_ino) != (
            archive_stat.st_dev,
            archive_stat.st_ino,
        ):
            raise EpochDrainIntegrityError(
                "Exchanged issue-route pathname changed before commit"
            )
        os.fsync(issue_descriptor)
        os.fsync(archive_descriptor)
        os.fsync(first_parent)
        if (
            os.fstat(second_parent).st_dev,
            os.fstat(second_parent).st_ino,
        ) != (
            os.fstat(first_parent).st_dev,
            os.fstat(first_parent).st_ino,
        ):
            os.fsync(second_parent)
    except EpochDrainError:
        raise
    except OSError as exc:
        raise EpochDrainIntegrityError(
            "Cannot durably reverify the exchanged issue route"
        ) from exc
    finally:
        os.close(second_parent)
        os.close(first_parent)
        os.close(archive_descriptor)
        os.close(issue_descriptor)


def _harden_fenced_route(paths: DrainPaths, intent: Mapping[str, Any]) -> None:
    """Finish the post-swap mode fence without ever undoing the exchange."""

    if _route_state(paths, intent) != "exchanged":
        raise EpochDrainIntegrityError("Canonical issue route is not exchanged")
    _verify_fence_marker_shape(paths.issue_inbox, allow_absent=False)
    _verify_fence_acl(paths.issue_inbox, name="exchanged issue-route fence")
    descriptor = _open_directory(paths.issue_inbox, name="canonical fenced route")
    try:
        if _read_fence_acl(descriptor) != FENCE_ACL_TEXT:
            raise EpochDrainIntegrityError(
                "Canonical fence ACL changed before hardening"
            )
        os.fchmod(descriptor, 0o555)
        os.fsync(descriptor)
    except OSError as exc:
        raise EpochDrainIntegrityError(
            "Cannot harden the exchanged canonical route; ACL fence remains active"
        ) from exc
    finally:
        os.close(descriptor)
    _verify_fenced_route(paths, intent)


def _inventory_manifest(
    records: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    materialized = [copy.deepcopy(dict(record)) for record in records]
    return {
        "records": materialized,
        "sha256": _sha256(_canonical_bytes({"records": materialized})),
    }


def _staged_next_epoch_snapshot(
    paths: DrainPaths, *, machine_now: datetime
) -> dict[str, object]:
    """Observe, but never consume, the external next-epoch delivery queue."""

    from monitoring import ootang_live_source as source_module

    first = _load_source_gate(paths, machine_now=machine_now)
    if isinstance(first, _Waiting) and first.status != "waiting_for_source_ingest":
        raise EpochDrainIntegrityError(first.reason)
    current = _load_current_source_gate(paths, machine_now=machine_now)
    if isinstance(current, _Waiting):
        raise EpochDrainIntegrityError(current.reason)
    incoming = source_module._runtime_path(  # noqa: SLF001
        current.deploy, "incoming_feed", root=paths.active_root
    )
    if not incoming.exists() and not incoming.is_symlink():
        return {
            "role": "staged_next_epoch_not_old_source_authority",
            "present": False,
            "artifact": None,
        }
    try:
        before = registry._read_regular(  # noqa: SLF001
            incoming,
            name="staged next-epoch source feed",
            maximum_bytes=registry.MAX_FEED_BYTES,
        )
        second = _load_source_gate(paths, machine_now=machine_now)
        if (
            isinstance(second, _Waiting)
            and second.status != "waiting_for_source_ingest"
        ):
            raise EpochDrainIntegrityError(second.reason)
        after = registry._read_regular(  # noqa: SLF001
            incoming,
            name="staged next-epoch source feed",
            maximum_bytes=registry.MAX_FEED_BYTES,
        )
    except (ValueError, registry.EpochRegistryError) as exc:
        raise EpochDrainIntegrityError(
            "Cannot bind the staged next-epoch source feed"
        ) from exc
    if before.raw != after.raw:
        raise EpochDrainIntegrityError(
            "Staged next-epoch source feed changed during boundary observation"
        )
    try:
        captured = _publish_once_durable(
            paths.objects / f"{after.sha256}.epoch-drain-staged-feed.json",
            after.raw,
            root=paths.root,
            name="staged next-epoch source feed object",
        )
    except registry.EpochRegistryError as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc
    return {
        "role": "staged_next_epoch_not_old_source_authority",
        "present": True,
        "artifact": _artifact_reference(captured, paths.root),
    }


def _boundary_payload(
    profile: Mapping[str, Any],
    paths: DrainPaths,
    authority: _Authority,
    clean: _CleanState,
    intent: registry.ArtifactSnapshot,
    fence_prepare: registry.ArtifactSnapshot,
    capsule: registry.ArtifactSnapshot,
    staged_incoming: Mapping[str, object],
    *,
    captured_at_utc: str,
) -> dict[str, object]:
    return {
        "schema_version": profile["protocol"]["boundary_schema_version"],
        "profile_sha256": profile["_profile_sha256"],
        "captured_at_utc": captured_at_utc,
        "candidate_id": authority.r1_event["candidate_id"],
        "slot_id": authority.r1_event["slot_id"],
        "candidate_at_intent": True,
        "activation_candidate_selected": False,
        "registry_event_entry_sha256": authority.r1_event["entry_sha256"],
        "preparation_event_entry_sha256": authority.preparation_event["entry_sha256"],
        "fence_intent": _artifact_reference(intent, paths.root),
        "fence_prepare": _artifact_reference(fence_prepare, paths.root),
        "drain_capsule": _artifact_reference(capsule, paths.root),
        "live_ledger": {
            "epoch_id": clean.old_live_epoch_id,
            "event_count": clean.live_event_count,
            "terminal_sha256": clean.live_terminal_sha256,
            "entry_sha256s": list(clean.live_entry_sha256s),
        },
        "issue_route_inventory": _inventory_manifest(clean.issue_route_inventory),
        "outcome_registry_inventory": _inventory_manifest(
            clean.outcome_registry_inventory
        ),
        "guard_inventory": _inventory_manifest(clean.guard_inventory),
        "trusted_time_inventory": _inventory_manifest(clean.trusted_time_inventory),
        "shadow_ledger": {
            "event_count": clean.shadow_event_count,
            "terminal_sha256": clean.shadow_terminal_sha256,
            "entry_sha256s": list(clean.shadow_entry_sha256s),
        },
        "source_authority": copy.deepcopy(clean.source_authority),
        "staged_incoming": copy.deepcopy(dict(staged_incoming)),
        "canonical_old_issue_route_fenced_before_event_required": True,
        "machine_only": True,
        "old_epoch_drained": False,
        "active_epoch_switch_implemented": False,
        "automatic_epoch_rotation_implemented": False,
        "trusted_anchor_receipt_verified": False,
        "e2_live_evidence_eligible": False,
        "real_activation_ready": False,
        "formal_warning_output": False,
    }


def _maximum_staged_next_epoch_reference(paths: DrainPaths) -> dict[str, object]:
    digest = "f" * 64
    synthetic = registry.ArtifactSnapshot(
        path=paths.objects / f"{digest}.epoch-drain-staged-feed.json",
        raw=b"",
        sha256=digest,
        size_bytes=registry.MAX_FEED_BYTES,
    )
    return {
        "role": "staged_next_epoch_not_old_source_authority",
        "present": True,
        "artifact": _artifact_reference(synthetic, paths.root),
    }


def _boundary_raw(
    profile: Mapping[str, Any],
    paths: DrainPaths,
    authority: _Authority,
    clean: _CleanState,
    intent: registry.ArtifactSnapshot,
    fence_prepare: registry.ArtifactSnapshot,
    capsule: registry.ArtifactSnapshot,
    staged_incoming: Mapping[str, object],
    *,
    captured_at_utc: str,
) -> bytes:
    return _canonical_bytes(
        _boundary_payload(
            profile,
            paths,
            authority,
            clean,
            intent,
            fence_prepare,
            capsule,
            staged_incoming,
            captured_at_utc=captured_at_utc,
        )
    )


def _preflight_boundary_capacity(
    profile: Mapping[str, Any],
    paths: DrainPaths,
    authority: _Authority,
    clean: _CleanState,
    intent: registry.ArtifactSnapshot,
    fence_prepare: registry.ArtifactSnapshot,
    capsule: registry.ArtifactSnapshot,
    *,
    captured_at_utc: str,
    staged_incoming: Mapping[str, object] | None = None,
) -> _Waiting | None:
    # Use a present, maximum-size CAS reference by default.  Feed content remains
    # outside the boundary object, so this is a strict upper bound for every
    # absent/present delivery and closes the external queue race on capacity.
    staged = (
        _maximum_staged_next_epoch_reference(paths)
        if staged_incoming is None
        else staged_incoming
    )
    raw = _boundary_raw(
        profile,
        paths,
        authority,
        clean,
        intent,
        fence_prepare,
        capsule,
        staged,
        captured_at_utc=captured_at_utc,
    )
    try:
        _require_manifest_size(raw, name="Epoch drain boundary")
    except EpochDrainIntegrityError:
        return _Waiting(
            "waiting_for_drain_boundary_capacity",
            "verified old-runtime boundary exceeds the reviewed CAS capacity",
        )
    return None


def _publish_boundary(
    profile: Mapping[str, Any],
    paths: DrainPaths,
    authority: _Authority,
    clean: _CleanState,
    intent: registry.ArtifactSnapshot,
    capsule: registry.ArtifactSnapshot,
    *,
    machine_now: datetime,
    captured_at_utc: str,
    staged_incoming: Mapping[str, object] | None = None,
) -> registry.ArtifactSnapshot:
    fence_prepare = _load_single_fence_prepare(paths)
    if fence_prepare is None:
        raise EpochDrainIntegrityError("Drain boundary lost its fence prepare")
    _verify_fence_prepare(
        profile,
        paths,
        authority,
        capsule,
        fence_prepare,
        require_unexchanged_route=False,
    )
    staged = (
        _staged_next_epoch_snapshot(paths, machine_now=machine_now)
        if staged_incoming is None
        else copy.deepcopy(dict(staged_incoming))
    )
    raw = _boundary_raw(
        profile,
        paths,
        authority,
        clean,
        intent,
        fence_prepare,
        capsule,
        staged,
        captured_at_utc=captured_at_utc,
    )
    _require_manifest_size(raw, name="Epoch drain boundary")
    try:
        return _publish_once_durable(
            paths.objects / f"{_sha256(raw)}.epoch-drain-boundary.json",
            raw,
            root=paths.root,
            name="epoch drain boundary",
        )
    except registry.EpochRegistryError as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc


def _manifest_records(value: object, *, name: str) -> tuple[dict[str, object], ...]:
    manifest = _exact(value, {"records", "sha256"}, name=name)
    records = manifest["records"]
    if not isinstance(records, list) or any(
        not isinstance(item, dict) for item in records
    ):
        raise EpochDrainIntegrityError(f"{name} records changed")
    expected = _sha256(_canonical_bytes({"records": records}))
    if _hash(manifest["sha256"], name=f"{name}.sha256") != expected:
        raise EpochDrainIntegrityError(f"{name} digest changed")
    return tuple(copy.deepcopy(records))


def _verify_source_authority_objects(
    paths: DrainPaths, source_authority: Mapping[str, object]
) -> None:
    for field in ("semantic_manifest", "activation_manifest", "snapshot_receipt"):
        value = source_authority[field]
        if value is None and field == "snapshot_receipt":
            continue
        record = _exact(value, REFERENCE_KEYS, name=f"boundary source {field}")
        digest = _hash(record["sha256"], name=f"boundary source {field}.sha256")
        size = record["size_bytes"]
        if type(size) is not int or size < 0 or size > MAX_CONTROL_BYTES:
            raise EpochDrainIntegrityError(f"Boundary source {field} size changed")
        try:
            artifact_path = registry._contained(  # noqa: SLF001
                paths.active_root,
                record["path"],
                name=f"boundary source {field}",
            )
            snapshot = registry._read_regular(  # noqa: SLF001
                artifact_path,
                name=f"boundary source {field}",
                maximum_bytes=MAX_CONTROL_BYTES,
            )
        except registry.EpochRegistryError as exc:
            raise EpochDrainIntegrityError(str(exc)) from exc
        if snapshot.sha256 != digest or snapshot.size_bytes != size:
            raise EpochDrainIntegrityError(f"Boundary source {field} object changed")


def _verify_boundary(
    profile: Mapping[str, Any],
    paths: DrainPaths,
    authority: _Authority,
    intent: registry.ArtifactSnapshot,
    capsule: registry.ArtifactSnapshot,
    value: object,
    *,
    clean: _CleanState | None = None,
) -> tuple[registry.ArtifactSnapshot, _CleanState]:
    snapshot = _read_reference(
        value,
        root=paths.root,
        name="epoch drain boundary",
        maximum_bytes=MAX_MANIFEST_BYTES,
    )
    try:
        payload = registry._decode_json(snapshot.raw, name="epoch drain boundary")  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc
    if snapshot.raw != _canonical_bytes(payload):
        raise EpochDrainIntegrityError("Epoch drain boundary is not canonical")
    if snapshot.path != paths.objects / f"{snapshot.sha256}.epoch-drain-boundary.json":
        raise EpochDrainIntegrityError("Epoch drain boundary path changed")
    _exact(
        payload,
        {
            "schema_version",
            "profile_sha256",
            "captured_at_utc",
            "candidate_id",
            "slot_id",
            "candidate_at_intent",
            "activation_candidate_selected",
            "registry_event_entry_sha256",
            "preparation_event_entry_sha256",
            "fence_intent",
            "fence_prepare",
            "drain_capsule",
            "live_ledger",
            "issue_route_inventory",
            "outcome_registry_inventory",
            "guard_inventory",
            "trusted_time_inventory",
            "shadow_ledger",
            "source_authority",
            "staged_incoming",
            "canonical_old_issue_route_fenced_before_event_required",
            "machine_only",
            "old_epoch_drained",
            "active_epoch_switch_implemented",
            "automatic_epoch_rotation_implemented",
            "trusted_anchor_receipt_verified",
            "e2_live_evidence_eligible",
            "real_activation_ready",
            "formal_warning_output",
        },
        name="epoch drain boundary",
    )
    fence_prepare = _read_reference(
        payload["fence_prepare"], root=paths.root, name="drain fence prepare"
    )
    _verify_fence_prepare(
        profile,
        paths,
        authority,
        capsule,
        fence_prepare,
        require_unexchanged_route=False,
    )
    live_record = _exact(
        payload["live_ledger"],
        {"epoch_id", "event_count", "terminal_sha256", "entry_sha256s"},
        name="boundary live ledger",
    )
    shadow_record = _exact(
        payload["shadow_ledger"],
        {"event_count", "terminal_sha256", "entry_sha256s"},
        name="boundary shadow ledger",
    )
    live_entries = live_record["entry_sha256s"]
    shadow_entries = shadow_record["entry_sha256s"]
    if not isinstance(live_entries, list) or not isinstance(shadow_entries, list):
        raise EpochDrainIntegrityError("Boundary ledger entries changed")
    live_hashes = tuple(
        _hash(item, name="boundary live entry") for item in live_entries
    )
    shadow_hashes = tuple(
        _hash(item, name="boundary shadow entry") for item in shadow_entries
    )
    live_count = live_record["event_count"]
    shadow_count = shadow_record["event_count"]
    if (
        type(live_count) is not int
        or live_count < 1
        or live_count != len(live_hashes)
        or type(shadow_count) is not int
        or shadow_count < 0
        or shadow_count != len(shadow_hashes)
    ):
        raise EpochDrainIntegrityError("Boundary ledger counts changed")
    if live_hashes[-1] != _hash(
        live_record["terminal_sha256"], name="boundary live terminal"
    ) or (
        (shadow_hashes[-1] if shadow_hashes else ZERO_HASH)
        != _hash(shadow_record["terminal_sha256"], name="boundary shadow terminal")
    ):
        raise EpochDrainIntegrityError("Boundary ledger terminal changed")
    source_authority = _exact(
        payload["source_authority"],
        {
            "outcome_source_id",
            "watermark",
            "exported_at_utc",
            "semantic_manifest",
            "activation_manifest",
            "snapshot_receipt",
            "snapshot_sequence_id",
            "records",
            "revision_ids_by_date",
        },
        name="boundary source authority",
    )
    if not _source_authority_extends(source_authority, source_authority):
        raise EpochDrainIntegrityError("Boundary source authority is not canonical")
    _verify_source_authority_objects(paths, source_authority)
    staged = _exact(
        payload["staged_incoming"],
        {"role", "present", "artifact"},
        name="boundary staged incoming",
    )
    if (
        staged["role"] != "staged_next_epoch_not_old_source_authority"
        or type(staged["present"]) is not bool
        or (staged["present"] and not isinstance(staged["artifact"], dict))
        or (not staged["present"] and staged["artifact"] is not None)
    ):
        raise EpochDrainIntegrityError("Boundary staged incoming semantics changed")
    if staged["present"]:
        staged_object = _read_reference(
            staged["artifact"],
            root=paths.root,
            name="staged next-epoch feed object",
            maximum_bytes=registry.MAX_FEED_BYTES,
        )
        if staged_object.path != (
            paths.objects / f"{staged_object.sha256}.epoch-drain-staged-feed.json"
        ):
            raise EpochDrainIntegrityError("Staged next-epoch feed object path changed")
    issue_inventory = _manifest_records(
        payload["issue_route_inventory"], name="boundary issue inventory"
    )
    outcome_inventory = _manifest_records(
        payload["outcome_registry_inventory"], name="boundary outcome inventory"
    )
    guard_inventory = _manifest_records(
        payload["guard_inventory"], name="boundary guard inventory"
    )
    trusted_inventory = _manifest_records(
        payload["trusted_time_inventory"], name="boundary trusted-time inventory"
    )
    if (
        not _inventory_subset_by_key(
            issue_inventory, issue_inventory, key="target_date"
        )
        or not _outcome_inventory_extends(outcome_inventory, outcome_inventory)
        or not _inventory_subset_by_key(
            guard_inventory, guard_inventory, key="target_date"
        )
        or not _inventory_subset_by_key(
            trusted_inventory, trusted_inventory, key="target_date"
        )
    ):
        raise EpochDrainIntegrityError("Boundary inventory natural keys changed")
    boundary_clean = _CleanState(
        old_live_epoch_id=_text(live_record["epoch_id"], name="boundary live epoch"),
        live_event_count=live_count,
        live_terminal_sha256=live_hashes[-1],
        guard_record_count=len(guard_inventory),
        trusted_time_record_count=len(trusted_inventory),
        shadow_event_count=shadow_count,
        shadow_terminal_sha256=shadow_hashes[-1] if shadow_hashes else ZERO_HASH,
        issue_route_inventory_sha256=_hash(
            payload["issue_route_inventory"]["sha256"],
            name="boundary issue inventory",
        ),
        issue_route_inventory=issue_inventory,
        outcome_registry_inventory=outcome_inventory,
        guard_inventory=guard_inventory,
        trusted_time_inventory=trusted_inventory,
        source_authority=copy.deepcopy(source_authority),
        live_entry_sha256s=live_hashes,
        shadow_entry_sha256s=shadow_hashes,
    )
    expected = _boundary_payload(
        profile,
        paths,
        authority,
        boundary_clean,
        intent,
        fence_prepare,
        capsule,
        staged,
        captured_at_utc=_text(
            payload["captured_at_utc"], name="boundary captured_at_utc"
        ),
    )
    if payload != expected:
        raise EpochDrainIntegrityError("Epoch drain boundary semantics changed")
    if clean is not None and (
        not _clean_state_extends(boundary_clean, clean)
        or clean.issue_route_inventory_sha256
        != boundary_clean.issue_route_inventory_sha256
    ):
        raise EpochDrainIntegrityError(
            "Current old runtime does not extend the authoritative drain boundary"
        )
    snapshot = _adopt_snapshot_durable(
        snapshot, root=paths.root, name="adopted epoch drain boundary"
    )
    return snapshot, boundary_clean


def _exchange_attempt_unsigned(
    profile: Mapping[str, Any],
    paths: DrainPaths,
    authority: _Authority,
    intent: registry.ArtifactSnapshot,
    intent_payload: Mapping[str, Any],
    fence_prepare: registry.ArtifactSnapshot,
    capsule: registry.ArtifactSnapshot,
    boundary: registry.ArtifactSnapshot,
    *,
    sequence_id: int,
    previous_entry_sha256: str,
) -> dict[str, object]:
    return {
        "schema_version": profile["protocol"]["exchange_attempt_schema_version"],
        "profile_sha256": profile["_profile_sha256"],
        "sequence_id": sequence_id,
        "candidate_id": authority.r1_event["candidate_id"],
        "slot_id": authority.r1_event["slot_id"],
        "registry_event_entry_sha256": authority.r1_event["entry_sha256"],
        "preparation_event_entry_sha256": authority.preparation_event["entry_sha256"],
        "fence_intent": _artifact_reference(intent, paths.root),
        "fence_prepare": _artifact_reference(fence_prepare, paths.root),
        "drain_capsule": _artifact_reference(capsule, paths.root),
        "drain_boundary": _artifact_reference(boundary, paths.root),
        "canonical_issue_route": str(paths.issue_inbox),
        "archive_route": str(intent_payload["archive_route"]),
        "old_route_identity": copy.deepcopy(intent_payload["old_route_identity"]),
        "fence_identity": copy.deepcopy(intent_payload["fence_identity"]),
        "fence_acl_sha256": FENCE_ACL_SHA256,
        "atomic_exchange": profile["protocol"]["atomic_exchange"],
        "previous_attempt_entry_sha256": previous_entry_sha256,
        "machine_only": True,
        "candidate_at_intent": True,
        "activation_candidate_selected": False,
        "canonical_old_issue_route_fenced_before_event_required": True,
        "lifecycle_authority": False,
    }


def _exchange_attempt_path(
    paths: DrainPaths, sequence_id: int, entry_sha256: str
) -> Path:
    if type(sequence_id) is not int or sequence_id < 1:
        raise EpochDrainIntegrityError("Exchange attempt sequence is invalid")
    _hash(entry_sha256, name="exchange attempt entry")
    return paths.exchange_attempts / f"{sequence_id:020d}-{entry_sha256}.json"


def _same_snapshot(
    first: registry.ArtifactSnapshot, second: registry.ArtifactSnapshot
) -> bool:
    return (
        first.path == second.path
        and first.raw == second.raw
        and first.sha256 == second.sha256
        and first.size_bytes == second.size_bytes
    )


def _exchange_attempt_entries(paths: DrainPaths) -> tuple[Path, ...]:
    if (
        not paths.exchange_attempts.exists()
        and not paths.exchange_attempts.is_symlink()
    ):
        return ()
    try:
        if not stat.S_ISDIR(os.lstat(paths.exchange_attempts).st_mode):
            raise EpochDrainIntegrityError(
                "Drain exchange-attempt path is not a real directory"
            )
        return tuple(
            sorted(paths.exchange_attempts.iterdir(), key=lambda item: item.name)
        )
    except EpochDrainError:
        raise
    except OSError as exc:
        raise EpochDrainIntegrityError(
            "Cannot inspect drain exchange attempts"
        ) from exc


def _scan_exchange_attempts(
    profile: Mapping[str, Any],
    paths: DrainPaths,
    authority: _Authority,
    intent: registry.ArtifactSnapshot,
    intent_payload: Mapping[str, Any],
    capsule: registry.ArtifactSnapshot,
) -> tuple[_ExchangeAttempt, ...]:
    entries = _exchange_attempt_entries(paths)
    if not entries:
        return ()
    fence_prepare = _load_single_fence_prepare(paths)
    if fence_prepare is None:
        raise EpochDrainIntegrityError("Exchange-attempt chain lost its fence prepare")
    _verify_fence_prepare(
        profile,
        paths,
        authority,
        capsule,
        fence_prepare,
        require_unexchanged_route=False,
    )
    attempts: list[_ExchangeAttempt] = []
    previous_entry = ZERO_HASH
    previous_clean: _CleanState | None = None
    expected_keys = {
        "schema_version",
        "profile_sha256",
        "sequence_id",
        "candidate_id",
        "slot_id",
        "registry_event_entry_sha256",
        "preparation_event_entry_sha256",
        "fence_intent",
        "fence_prepare",
        "drain_capsule",
        "drain_boundary",
        "canonical_issue_route",
        "archive_route",
        "old_route_identity",
        "fence_identity",
        "fence_acl_sha256",
        "atomic_exchange",
        "previous_attempt_entry_sha256",
        "machine_only",
        "candidate_at_intent",
        "activation_candidate_selected",
        "canonical_old_issue_route_fenced_before_event_required",
        "lifecycle_authority",
        "entry_sha256",
    }
    for sequence_id, path in enumerate(entries, start=1):
        try:
            snapshot = registry._read_regular(  # noqa: SLF001
                path,
                name="drain exchange attempt",
                maximum_bytes=MAX_CONTROL_BYTES,
            )
            payload = registry._decode_json(  # noqa: SLF001
                snapshot.raw, name="drain exchange attempt"
            )
        except registry.EpochRegistryError as exc:
            raise EpochDrainIntegrityError(str(exc)) from exc
        if snapshot.raw != _canonical_bytes(payload):
            raise EpochDrainIntegrityError("Drain exchange attempt is not canonical")
        _exact(payload, expected_keys, name="drain exchange attempt")
        entry_sha256 = _hash(payload["entry_sha256"], name="exchange attempt entry")
        unsigned = {
            key: value for key, value in payload.items() if key != "entry_sha256"
        }
        if (
            payload["sequence_id"] != sequence_id
            or path != _exchange_attempt_path(paths, sequence_id, entry_sha256)
            or entry_sha256 != _sha256(_canonical_bytes(unsigned))
        ):
            raise EpochDrainIntegrityError(
                "Drain exchange-attempt chain has a gap, branch, or rollback"
            )
        referenced_intent = _read_reference(
            payload["fence_intent"], root=paths.root, name="attempt fence intent"
        )
        referenced_prepare = _read_reference(
            payload["fence_prepare"], root=paths.root, name="attempt fence prepare"
        )
        referenced_capsule = _read_reference(
            payload["drain_capsule"],
            root=paths.root,
            name="attempt drain capsule",
            maximum_bytes=MAX_MANIFEST_BYTES,
        )
        if not (
            _same_snapshot(referenced_intent, intent)
            and _same_snapshot(referenced_prepare, fence_prepare)
            and _same_snapshot(referenced_capsule, capsule)
        ):
            raise EpochDrainIntegrityError(
                "Drain exchange attempt changed its transaction provenance"
            )
        boundary, boundary_clean = _verify_boundary(
            profile,
            paths,
            authority,
            intent,
            capsule,
            payload["drain_boundary"],
        )
        expected = _exchange_attempt_unsigned(
            profile,
            paths,
            authority,
            intent,
            intent_payload,
            fence_prepare,
            capsule,
            boundary,
            sequence_id=sequence_id,
            previous_entry_sha256=previous_entry,
        )
        if payload != {**expected, "entry_sha256": entry_sha256}:
            raise EpochDrainIntegrityError("Drain exchange-attempt semantics changed")
        if previous_clean is not None and not _clean_state_extends(
            previous_clean, boundary_clean
        ):
            raise EpochDrainIntegrityError(
                "Drain exchange-attempt boundary rolled back"
            )
        durable_boundary = _publish_once_durable(
            boundary.path,
            boundary.raw,
            root=paths.root,
            name="adopted drain exchange boundary",
        )
        durable_attempt = _publish_once_durable(
            snapshot.path,
            snapshot.raw,
            root=paths.root,
            name="adopted drain exchange attempt",
        )
        if not (
            _same_snapshot(durable_boundary, boundary)
            and _same_snapshot(durable_attempt, snapshot)
        ):
            raise EpochDrainIntegrityError(
                "Drain exchange-attempt durability adoption changed bytes"
            )
        attempts.append(
            _ExchangeAttempt(durable_attempt, payload, durable_boundary, boundary_clean)
        )
        previous_entry = entry_sha256
        previous_clean = boundary_clean
    return tuple(attempts)


def _boundary_staged_incoming(
    boundary: registry.ArtifactSnapshot,
) -> dict[str, object]:
    try:
        payload = registry._decode_json(boundary.raw, name="epoch drain boundary")  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc
    staged = payload.get("staged_incoming")
    if not isinstance(staged, dict):
        raise EpochDrainIntegrityError("Boundary staged incoming changed")
    return copy.deepcopy(staged)


def _append_exchange_attempt(
    profile: Mapping[str, Any],
    paths: DrainPaths,
    authority: _Authority,
    intent: registry.ArtifactSnapshot,
    intent_payload: Mapping[str, Any],
    capsule: registry.ArtifactSnapshot,
    boundary: registry.ArtifactSnapshot,
    clean: _CleanState,
) -> _ExchangeAttempt:
    if _route_state(paths, intent_payload) != "prepared":
        raise EpochDrainIntegrityError(
            "Cannot append an exchange attempt after the route exchange"
        )
    verified_boundary, boundary_clean = _verify_boundary(
        profile,
        paths,
        authority,
        intent,
        capsule,
        _artifact_reference(boundary, paths.root),
        clean=clean,
    )
    if not _same_snapshot(verified_boundary, boundary) or boundary_clean != clean:
        raise EpochDrainIntegrityError(
            "Prepared exchange attempt does not bind the exact clean boundary"
        )
    attempts = _scan_exchange_attempts(
        profile, paths, authority, intent, intent_payload, capsule
    )
    sequence_id = len(attempts) + 1
    previous_entry = attempts[-1].payload["entry_sha256"] if attempts else ZERO_HASH
    fence_prepare = _load_single_fence_prepare(paths)
    if fence_prepare is None:
        raise EpochDrainIntegrityError("Exchange attempt lost its fence prepare")
    unsigned = _exchange_attempt_unsigned(
        profile,
        paths,
        authority,
        intent,
        intent_payload,
        fence_prepare,
        capsule,
        boundary,
        sequence_id=sequence_id,
        previous_entry_sha256=previous_entry,
    )
    entry_sha256 = _sha256(_canonical_bytes(unsigned))
    payload = {**unsigned, "entry_sha256": entry_sha256}
    try:
        _publish_once_durable(
            _exchange_attempt_path(paths, sequence_id, entry_sha256),
            _canonical_bytes(payload),
            root=paths.root,
            name="drain exchange attempt",
        )
    except registry.EpochRegistryError as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc
    replayed = _scan_exchange_attempts(
        profile, paths, authority, intent, intent_payload, capsule
    )
    if len(replayed) != sequence_id or replayed[-1].payload != payload:
        raise EpochDrainIntegrityError(
            "Published exchange attempt is not the unique chain tip"
        )
    return replayed[-1]


def _prepare_exchange_attempt(
    profile: Mapping[str, Any],
    paths: DrainPaths,
    authority: _Authority,
    clean: _CleanState,
    intent: registry.ArtifactSnapshot,
    intent_payload: Mapping[str, Any],
    capsule: registry.ArtifactSnapshot,
    staged_incoming: Mapping[str, object],
    *,
    machine_now: datetime,
    captured_at_utc: str,
) -> _ExchangeAttempt:
    attempts = _scan_exchange_attempts(
        profile, paths, authority, intent, intent_payload, capsule
    )
    if attempts:
        tip = attempts[-1]
        if (
            tip.boundary_clean == clean
            and _boundary_staged_incoming(tip.boundary) == staged_incoming
        ):
            return tip
    boundary = _publish_boundary(
        profile,
        paths,
        authority,
        clean,
        intent,
        capsule,
        machine_now=machine_now,
        captured_at_utc=captured_at_utc,
        staged_incoming=staged_incoming,
    )
    return _append_exchange_attempt(
        profile,
        paths,
        authority,
        intent,
        intent_payload,
        capsule,
        boundary,
        clean,
    )


def _armed_attempt_marker_payload(
    profile: Mapping[str, Any],
    paths: DrainPaths,
    authority: _Authority,
    intent: registry.ArtifactSnapshot,
    intent_payload: Mapping[str, Any],
    attempt: _ExchangeAttempt,
) -> dict[str, object]:
    return {
        "schema_version": profile["protocol"]["armed_attempt_marker_schema_version"],
        "profile_sha256": profile["_profile_sha256"],
        "candidate_id": authority.r1_event["candidate_id"],
        "fence_intent": _artifact_reference(intent, paths.root),
        "exchange_attempt": _artifact_reference(attempt.snapshot, paths.root),
        "drain_boundary": _artifact_reference(attempt.boundary, paths.root),
        "attempt_sequence_id": attempt.payload["sequence_id"],
        "attempt_entry_sha256": attempt.payload["entry_sha256"],
        "fence_identity": copy.deepcopy(intent_payload["fence_identity"]),
        "machine_only": True,
        "lifecycle_authority": False,
    }


def _read_armed_attempt_marker(
    profile: Mapping[str, Any],
    paths: DrainPaths,
    authority: _Authority,
    intent: registry.ArtifactSnapshot,
    intent_payload: Mapping[str, Any],
    attempts: Sequence[_ExchangeAttempt],
    marker: Path,
) -> _ExchangeAttempt:
    try:
        marker_mode = os.lstat(marker).st_mode
        if (
            not stat.S_ISREG(marker_mode)
            or stat.S_IMODE(marker_mode) != ARMED_ATTEMPT_MARKER_MODE
        ):
            raise EpochDrainIntegrityError("Armed-attempt marker mode changed")
        snapshot = registry._read_regular(  # noqa: SLF001
            marker,
            name="armed exchange-attempt marker",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
        payload = registry._decode_json(  # noqa: SLF001
            snapshot.raw, name="armed exchange-attempt marker"
        )
    except EpochDrainError:
        raise
    except (OSError, registry.EpochRegistryError) as exc:
        raise EpochDrainIntegrityError(
            "Cannot read armed exchange-attempt marker"
        ) from exc
    if snapshot.raw != _canonical_bytes(payload):
        raise EpochDrainIntegrityError("Armed-attempt marker is not canonical")
    entry_sha256 = _hash(
        payload.get("attempt_entry_sha256"), name="armed attempt entry"
    )
    matches = [
        attempt
        for attempt in attempts
        if attempt.payload["entry_sha256"] == entry_sha256
    ]
    if len(matches) != 1:
        raise EpochDrainIntegrityError(
            "Armed marker references a missing or ambiguous WAL attempt"
        )
    attempt = matches[0]
    expected = _armed_attempt_marker_payload(
        profile, paths, authority, intent, intent_payload, attempt
    )
    if payload != expected:
        raise EpochDrainIntegrityError("Armed-attempt marker semantics changed")
    referenced = _read_reference(
        payload["exchange_attempt"],
        root=paths.root,
        name="armed exchange attempt",
    )
    if not _same_snapshot(referenced, attempt.snapshot):
        raise EpochDrainIntegrityError("Armed-attempt marker reference changed")
    referenced_boundary = _read_reference(
        payload["drain_boundary"],
        root=paths.root,
        name="armed drain boundary",
        maximum_bytes=MAX_MANIFEST_BYTES,
    )
    if not _same_snapshot(referenced_boundary, attempt.boundary):
        raise EpochDrainIntegrityError("Armed-attempt boundary reference changed")
    return attempt


def _select_armed_attempt(
    profile: Mapping[str, Any],
    paths: DrainPaths,
    authority: _Authority,
    intent: registry.ArtifactSnapshot,
    intent_payload: Mapping[str, Any],
    attempts: Sequence[_ExchangeAttempt],
) -> _ExchangeAttempt:
    if not attempts:
        raise EpochDrainIntegrityError("Issue-route fence has no exchange attempt")
    state = _route_state(paths, intent_payload)
    fence_directory = (
        Path(str(intent_payload["archive_route"]))
        if state == "prepared"
        else paths.issue_inbox
    )
    _verify_fence_marker_shape(fence_directory, allow_absent=False)
    selected = _read_armed_attempt_marker(
        profile,
        paths,
        authority,
        intent,
        intent_payload,
        attempts,
        fence_directory / ARMED_ATTEMPT_MARKER_NAME,
    )
    if not _same_snapshot(selected.snapshot, attempts[-1].snapshot):
        raise EpochDrainIntegrityError(
            "Armed fence marker does not bind the WAL terminal"
        )
    return selected


def _write_armed_marker_temp(
    directory_descriptor: int,
    *,
    temp_name: str,
    raw: bytes,
) -> None:
    flags = os.O_WRONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        existing = os.stat(
            temp_name, dir_fd=directory_descriptor, follow_symlinks=False
        )
    except FileNotFoundError:
        existing = None
    except OSError as exc:
        raise EpochDrainIntegrityError("Cannot inspect armed-marker temp") from exc
    if existing is not None:
        if not stat.S_ISREG(existing.st_mode) or stat.S_IMODE(existing.st_mode) not in {
            0o600,
            ARMED_ATTEMPT_MARKER_MODE,
        }:
            raise EpochDrainIntegrityError("Armed-marker temp type or mode changed")
        try:
            recovery_descriptor = os.open(
                temp_name,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_descriptor,
            )
            os.fchmod(recovery_descriptor, 0o600)
        except OSError as exc:
            raise EpochDrainIntegrityError(
                "Cannot prepare armed-marker temp recovery"
            ) from exc
        finally:
            try:
                os.close(recovery_descriptor)
            except (NameError, OSError):
                pass
        flags |= os.O_TRUNC
    else:
        flags |= os.O_CREAT | os.O_EXCL
    descriptor: int | None = None
    try:
        descriptor = os.open(temp_name, flags, 0o600, dir_fd=directory_descriptor)
        opened = os.fstat(descriptor)
        named = os.stat(temp_name, dir_fd=directory_descriptor, follow_symlinks=False)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
            named.st_dev,
            named.st_ino,
        ):
            raise EpochDrainIntegrityError(
                "Armed-marker temp is not one stable regular file"
            )
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise OSError(errno.EIO, "short armed-marker write")
            offset += written
        os.fchmod(descriptor, ARMED_ATTEMPT_MARKER_MODE)
        os.fsync(descriptor)
    except EpochDrainError:
        raise
    except OSError as exc:
        raise EpochDrainIntegrityError("Cannot write armed-marker temp") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _arm_exchange_attempt(
    profile: Mapping[str, Any],
    paths: DrainPaths,
    authority: _Authority,
    intent: registry.ArtifactSnapshot,
    intent_payload: Mapping[str, Any],
    capsule: registry.ArtifactSnapshot,
    attempt: _ExchangeAttempt,
) -> _ExchangeAttempt:
    attempts = _scan_exchange_attempts(
        profile, paths, authority, intent, intent_payload, capsule
    )
    if not attempts or not _same_snapshot(attempts[-1].snapshot, attempt.snapshot):
        raise EpochDrainIntegrityError("Cannot arm a non-terminal exchange attempt")
    if _route_state(paths, intent_payload) != "prepared":
        raise EpochDrainIntegrityError("Cannot arm an exchanged issue-route fence")
    fence = Path(str(intent_payload["archive_route"]))
    identity = _directory_identity(fence, name="prepared armed issue-route fence")
    if (
        identity["device"] != intent_payload["fence_identity"]["device"]
        or identity["inode"] != intent_payload["fence_identity"]["inode"]
        or identity["mode"] not in {0o755, 0o555}
    ):
        raise EpochDrainIntegrityError("Armed issue-route fence identity changed")
    if identity["mode"] == 0o555:
        recovery_descriptor = _open_directory(
            fence, name="hardened prepared armed fence"
        )
        try:
            os.fchmod(recovery_descriptor, 0o755)
            os.fsync(recovery_descriptor)
        except OSError as exc:
            raise EpochDrainIntegrityError(
                "Cannot recover hardened prepared fence mode"
            ) from exc
        finally:
            os.close(recovery_descriptor)
    marker = fence / ARMED_ATTEMPT_MARKER_NAME
    temp_name = f"{ARMED_ATTEMPT_MARKER_NAME}.tmp-{attempt.payload['entry_sha256']}"
    temp = fence / temp_name
    try:
        entries = sorted(fence.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise EpochDrainIntegrityError("Cannot inspect fence arming state") from exc
    temp_prefix = f"{ARMED_ATTEMPT_MARKER_NAME}.tmp-"
    temp_entries = [entry for entry in entries if entry.name.startswith(temp_prefix)]
    if len(temp_entries) > 1:
        raise EpochDrainIntegrityError("Fence arming state has multiple temp files")
    recovered_temp = temp_entries[0] if temp_entries else None
    if recovered_temp is not None:
        recovered_entry = recovered_temp.name.removeprefix(temp_prefix)
        _hash(recovered_entry, name="armed-marker temp attempt")
        if (
            sum(item.payload["entry_sha256"] == recovered_entry for item in attempts)
            != 1
        ):
            raise EpochDrainIntegrityError(
                "Armed-marker temp does not belong to the WAL chain"
            )
    allowed = {marker}
    if recovered_temp is not None:
        allowed.add(recovered_temp)
    if any(entry not in allowed for entry in entries):
        raise EpochDrainIntegrityError("Fence arming state contains an unknown entry")
    if marker in entries:
        selected = _read_armed_attempt_marker(
            profile,
            paths,
            authority,
            intent,
            intent_payload,
            attempts,
            marker,
        )
        if (
            _same_snapshot(selected.snapshot, attempt.snapshot)
            and recovered_temp is None
        ):
            descriptor = _open_directory(fence, name="prepared armed fence")
            try:
                acl = _read_fence_acl(descriptor, allow_missing=True)
                if acl is None:
                    _install_fence_acl(descriptor)
                elif acl != FENCE_ACL_TEXT:
                    raise EpochDrainIntegrityError("Armed fence ACL changed")
                # Repeat this durability barrier even when the exact ACL was
                # already present.  A prior invocation may have installed it
                # and crashed before fsync; marker equality alone cannot prove
                # that the inode metadata reached stable storage.
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            _verify_fence_acl(fence, name="prepared armed fence")
            return _select_armed_attempt(
                profile, paths, authority, intent, intent_payload, attempts
            )
    if recovered_temp is not None:
        try:
            temp_mode = os.lstat(recovered_temp).st_mode
        except OSError as exc:
            raise EpochDrainIntegrityError("Cannot inspect armed-marker temp") from exc
        if not stat.S_ISREG(temp_mode) or stat.S_IMODE(temp_mode) not in {
            0o600,
            ARMED_ATTEMPT_MARKER_MODE,
        }:
            raise EpochDrainIntegrityError("Armed-marker temp type or mode changed")
    descriptor = _open_directory(fence, name="issue-route fence arming directory")
    raw = _canonical_bytes(
        _armed_attempt_marker_payload(
            profile, paths, authority, intent, intent_payload, attempt
        )
    )
    try:
        acl = _read_fence_acl(descriptor, allow_missing=True)
        if acl not in {None, FENCE_ACL_TEXT}:
            raise EpochDrainIntegrityError("Fence arming encountered an unknown ACL")
        _remove_fence_acl(descriptor)
        os.fsync(descriptor)
        if recovered_temp is not None and recovered_temp != temp:
            os.unlink(recovered_temp.name, dir_fd=descriptor)
            os.fsync(descriptor)
        _write_armed_marker_temp(descriptor, temp_name=temp_name, raw=raw)
        os.replace(
            temp_name,
            ARMED_ATTEMPT_MARKER_NAME,
            src_dir_fd=descriptor,
            dst_dir_fd=descriptor,
        )
        os.fsync(descriptor)
        _install_fence_acl(descriptor)
        os.fsync(descriptor)
    except EpochDrainError:
        raise
    except OSError as exc:
        raise EpochDrainIntegrityError(
            "Cannot atomically arm exchange attempt"
        ) from exc
    finally:
        os.close(descriptor)
    _verify_fence_acl(fence, name="prepared armed fence")
    return _select_armed_attempt(
        profile, paths, authority, intent, intent_payload, attempts
    )


def _event_unsigned(
    profile: Mapping[str, Any],
    paths: DrainPaths,
    authority: _Authority,
    clean: _CleanState,
    intent: registry.ArtifactSnapshot,
    fence_prepare: registry.ArtifactSnapshot,
    capsule: registry.ArtifactSnapshot,
    boundary: registry.ArtifactSnapshot,
    exchange_attempt: registry.ArtifactSnapshot,
    *,
    recorded_at_utc: str,
) -> dict[str, object]:
    return {
        "schema_version": profile["protocol"]["event_schema_version"],
        "profile_sha256": profile["_profile_sha256"],
        "sequence_id": 1,
        "event_type": "epoch_drain_started",
        "lifecycle_state": "DRAINING",
        "recorded_at_utc": recorded_at_utc,
        "registry_event_sequence_id": authority.r1_event["sequence_id"],
        "registry_event_entry_sha256": authority.r1_event["entry_sha256"],
        "preparation_event_sequence_id": authority.preparation_event["sequence_id"],
        "preparation_event_entry_sha256": authority.preparation_event["entry_sha256"],
        "candidate_id": authority.r1_event["candidate_id"],
        "slot_id": authority.r1_event["slot_id"],
        "candidate_at_intent": True,
        "activation_candidate_selected": False,
        "old_live_epoch_id": clean.old_live_epoch_id,
        "drain_capsule": _artifact_reference(capsule, paths.root),
        "fence_intent": _artifact_reference(intent, paths.root),
        "fence_prepare": _artifact_reference(fence_prepare, paths.root),
        "drain_boundary": _artifact_reference(boundary, paths.root),
        "exchange_attempt": _artifact_reference(exchange_attempt, paths.root),
        "previous_entry_sha256": ZERO_HASH,
        "canonical_old_issue_route_fenced": True,
        "scheduler_old_issue_creation_fenced": True,
        "old_epoch_drained": False,
        "old_epoch_drain_implemented": False,
        "active_epoch_switch_implemented": False,
        "automatic_epoch_rotation_implemented": False,
        "trusted_anchor_receipt_verified": False,
        "e2_live_evidence_eligible": False,
        "real_activation_ready": False,
        "formal_warning_output": False,
    }


def _event_path(paths: DrainPaths) -> Path:
    return paths.events / "00000000000000000001.json"


def _append_event(
    profile: Mapping[str, Any],
    paths: DrainPaths,
    authority: _Authority,
    clean: _CleanState,
    intent: registry.ArtifactSnapshot,
    capsule: registry.ArtifactSnapshot,
    boundary: registry.ArtifactSnapshot,
    exchange_attempt: _ExchangeAttempt,
    *,
    recorded_at_utc: str,
) -> dict[str, object]:
    fence_prepare = _load_single_fence_prepare(paths)
    if fence_prepare is None:
        raise EpochDrainIntegrityError("Epoch drain event lost its fence prepare")
    _verify_fence_prepare(
        profile,
        paths,
        authority,
        capsule,
        fence_prepare,
        require_unexchanged_route=False,
    )
    verified_boundary, verified_clean = _verify_boundary(
        profile,
        paths,
        authority,
        intent,
        capsule,
        _artifact_reference(boundary, paths.root),
        clean=clean,
    )
    verified_intent_payload, verified_capsule, _ = _verify_intent(
        profile,
        paths,
        authority,
        clean,
        intent,
        require_current_implementation=False,
    )
    if not _same_snapshot(verified_capsule, capsule):
        raise EpochDrainIntegrityError("Epoch drain event changed its captured capsule")
    attempts = _scan_exchange_attempts(
        profile,
        paths,
        authority,
        intent,
        verified_intent_payload,
        capsule,
    )
    if (
        not _same_snapshot(verified_boundary, boundary)
        or not attempts
        or not _same_snapshot(attempts[-1].snapshot, exchange_attempt.snapshot)
        or not _same_snapshot(attempts[-1].boundary, boundary)
        or verified_clean != exchange_attempt.boundary_clean
    ):
        raise EpochDrainIntegrityError(
            "Epoch drain boundary failed its pre-event self-verification"
        )
    unsigned = _event_unsigned(
        profile,
        paths,
        authority,
        verified_clean,
        intent,
        fence_prepare,
        capsule,
        boundary,
        exchange_attempt.snapshot,
        recorded_at_utc=recorded_at_utc,
    )
    event = {**unsigned, "entry_sha256": _sha256(_canonical_bytes(unsigned))}
    try:
        _publish_once_durable(
            _event_path(paths),
            _canonical_bytes(event),
            root=paths.root,
            name="epoch drain event",
        )
    except registry.EpochRegistryError as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc
    return event


def replay_drain(
    profile: Mapping[str, Any],
    paths: DrainPaths,
    authority: _Authority,
    clean: _CleanState | None,
    *,
    verify_fence: bool = True,
) -> tuple[dict[str, Any], ...]:
    """Replay the sole R2b authority event; head and status are never inputs."""

    if not paths.events.exists() and not paths.events.is_symlink():
        return ()
    try:
        if not stat.S_ISDIR(os.lstat(paths.events).st_mode):
            raise EpochDrainIntegrityError("Drain events path is not a real directory")
    except OSError as exc:
        raise EpochDrainIntegrityError("Cannot inspect drain events") from exc
    entries = sorted(paths.events.iterdir(), key=lambda item: item.name)
    if not entries:
        return ()
    if [item.name for item in entries] != ["00000000000000000001.json"]:
        raise EpochDrainIntegrityError(
            "Drain event chain has a gap, branch, or rollback"
        )
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            entries[0], name="epoch drain event", maximum_bytes=MAX_CONTROL_BYTES
        )
        event = registry._decode_json(snapshot.raw, name="epoch drain event")  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc
    if snapshot.raw != _canonical_bytes(event):
        raise EpochDrainIntegrityError("Drain event is not canonical")
    entry = _hash(event.get("entry_sha256"), name="drain event entry")
    unsigned = {key: value for key, value in event.items() if key != "entry_sha256"}
    if entry != _sha256(_canonical_bytes(unsigned)):
        raise EpochDrainIntegrityError("Drain event hash changed")
    recorded = _text(event.get("recorded_at_utc"), name="event recorded_at_utc")
    intent = _read_reference(
        event.get("fence_intent"), root=paths.root, name="fence intent"
    )
    intent_payload, capsule, _ = _verify_intent(
        profile,
        paths,
        authority,
        clean,
        intent,
        require_current_implementation=False,
    )
    fence_prepare = _read_reference(
        event.get("fence_prepare"), root=paths.root, name="drain fence prepare"
    )
    _verify_fence_prepare(
        profile,
        paths,
        authority,
        capsule,
        fence_prepare,
        require_unexchanged_route=False,
    )
    attempts = _scan_exchange_attempts(
        profile, paths, authority, intent, intent_payload, capsule
    )
    event_attempt = _read_reference(
        event.get("exchange_attempt"),
        root=paths.root,
        name="event exchange attempt",
    )
    if not attempts or not _same_snapshot(attempts[-1].snapshot, event_attempt):
        raise EpochDrainIntegrityError(
            "Drain event does not bind the terminal exchange attempt"
        )
    armed_attempt = _select_armed_attempt(
        profile, paths, authority, intent, intent_payload, attempts
    )
    if not _same_snapshot(armed_attempt.snapshot, event_attempt):
        raise EpochDrainIntegrityError(
            "Drain event differs from the attempt armed in the fence inode"
        )
    boundary, boundary_clean = _verify_boundary(
        profile,
        paths,
        authority,
        intent,
        capsule,
        event.get("drain_boundary"),
        clean=clean,
    )
    if (
        not _same_snapshot(attempts[-1].boundary, boundary)
        or attempts[-1].boundary_clean != boundary_clean
    ):
        raise EpochDrainIntegrityError(
            "Drain event boundary differs from its exchange attempt"
        )
    expected = {
        **_event_unsigned(
            profile,
            paths,
            authority,
            boundary_clean,
            intent,
            fence_prepare,
            capsule,
            boundary,
            event_attempt,
            recorded_at_utc=recorded,
        ),
        "entry_sha256": entry,
    }
    if event != expected:
        raise EpochDrainIntegrityError("Drain event semantics changed")
    if verify_fence:
        _verify_fenced_route(paths, intent_payload)
    _adopt_snapshot_durable(snapshot, root=paths.root, name="adopted epoch drain event")
    return (event,)


def _persisted_authority_binding(
    paths: DrainPaths,
) -> tuple[str, str] | None:
    """Read only immutable binding fields needed to select historical authority."""

    event_path = _event_path(paths)
    if event_path.exists() or event_path.is_symlink():
        try:
            snapshot = registry._read_regular(  # noqa: SLF001
                event_path, name="epoch drain event", maximum_bytes=MAX_CONTROL_BYTES
            )
            payload = registry._decode_json(snapshot.raw, name="epoch drain event")  # noqa: SLF001
        except registry.EpochRegistryError as exc:
            raise EpochDrainIntegrityError(str(exc)) from exc
        if snapshot.raw != _canonical_bytes(payload):
            raise EpochDrainIntegrityError("Drain event is not canonical")
        entry = _hash(payload.get("entry_sha256"), name="drain event entry")
        unsigned = {
            key: value for key, value in payload.items() if key != "entry_sha256"
        }
        if entry != _sha256(_canonical_bytes(unsigned)):
            raise EpochDrainIntegrityError("Drain event hash changed")
        return (
            _hash(
                payload.get("registry_event_entry_sha256"),
                name="event R1 entry",
            ),
            _hash(
                payload.get("preparation_event_entry_sha256"),
                name="event R2a entry",
            ),
        )
    intent = _load_single_intent(paths)
    persisted = intent or _load_single_fence_prepare(paths)
    if persisted is None:
        return None
    name = "epoch drain fence intent" if intent is not None else "drain fence prepare"
    try:
        payload = registry._decode_json(persisted.raw, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc
    if persisted.raw != _canonical_bytes(payload):
        raise EpochDrainIntegrityError(f"{name} is not canonical")
    return (
        _hash(payload.get("registry_event_entry_sha256"), name=f"{name} R1 entry"),
        _hash(
            payload.get("preparation_event_entry_sha256"),
            name=f"{name} R2a entry",
        ),
    )


def _persisted_issue_archive(paths: DrainPaths) -> Path | None:
    intent = _load_single_intent(paths)
    if intent is None:
        return None
    try:
        payload = registry._decode_json(intent.raw, name="epoch drain fence intent")  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc
    if intent.raw != _canonical_bytes(payload):
        raise EpochDrainIntegrityError("Drain intent is not canonical")
    state = _route_state(paths, payload)
    if state == "prepared":
        return None
    return Path(_text(payload.get("archive_route"), name="intent archive route"))


def _utc_text(clock: Clock) -> str:
    try:
        value = clock()
    except Exception as exc:
        raise EpochDrainIntegrityError("Drain machine clock failed") from exc
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise EpochDrainIntegrityError("Drain machine clock must be timezone-aware")
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _head_payload(
    profile: Mapping[str, Any], event: Mapping[str, Any]
) -> dict[str, object]:
    return {
        "schema_version": profile["protocol"]["head_schema_version"],
        "profile_sha256": profile["_profile_sha256"],
        "sequence_id": 1,
        "entry_sha256": event["entry_sha256"],
        "candidate_id": event["candidate_id"],
        "lifecycle_state": "DRAINING",
    }


def _refresh_head(
    profile: Mapping[str, Any], paths: DrainPaths, events: Sequence[Mapping[str, Any]]
) -> None:
    if not events:
        return
    try:
        registry._atomic_cache(  # noqa: SLF001
            paths.head,
            _canonical_bytes(_head_payload(profile, events[-1])),
            root=paths.root,
            name="epoch drain head cache",
        )
    except registry.EpochRegistryError as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc


def _status_payload(
    profile: Mapping[str, Any],
    *,
    checked_at_utc: str,
    status: str,
    reason: str,
    authority: _Authority | None,
    events: Sequence[Mapping[str, Any]],
    candidate_at_intent: bool,
) -> dict[str, object]:
    event = events[-1] if events else None
    current_fence_verified = event is not None and status != "blocked_integrity"
    return {
        "schema_version": profile["protocol"]["status_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "checked_at_utc": checked_at_utc,
        "drain_status": status,
        "reason": reason,
        "lifecycle_state": "DRAINING" if event else "PREPARING",
        "event_count": len(events),
        "terminal_entry_sha256": event["entry_sha256"] if event else ZERO_HASH,
        "candidate_id": (
            event["candidate_id"]
            if event
            else authority.r1_event["candidate_id"]
            if authority is not None
            else None
        ),
        "machine_only": True,
        "immutable_candidate_registry_verified": authority is not None,
        "executable_candidate_preparation_verified": authority is not None,
        "all_known_writer_locks_serialized": bool(event),
        "canonical_old_issue_route_fenced": current_fence_verified,
        "scheduler_old_issue_creation_fenced": current_fence_verified,
        "epoch_drain_started": bool(event),
        "candidate_at_intent": candidate_at_intent,
        "activation_candidate_selected": False,
        "old_epoch_drained": False,
        "old_epoch_drain_implemented": False,
        "active_epoch_switch_implemented": False,
        "automatic_epoch_rotation_implemented": False,
        "trusted_anchor_receipt_verified": False,
        "e2_live_evidence_eligible": False,
        "real_activation_ready": False,
        "formal_warning_output": False,
    }


def _write_status(
    profile: Mapping[str, Any], paths: DrainPaths, payload: Mapping[str, object]
) -> Path:
    try:
        registry._atomic_cache(  # noqa: SLF001
            paths.status,
            _canonical_bytes(payload),
            root=paths.root,
            name="epoch drain status cache",
        )
    except registry.EpochRegistryError as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc
    return paths.status


def _waiting_result(
    profile: Mapping[str, Any],
    paths: DrainPaths,
    waiting: _Waiting,
    *,
    checked_at_utc: str,
    authority: _Authority | None,
) -> EpochDrainResult:
    candidate_at_intent = _load_single_intent(paths) is not None
    status_path = _write_status(
        profile,
        paths,
        _status_payload(
            profile,
            checked_at_utc=checked_at_utc,
            status=waiting.status,
            reason=waiting.reason,
            authority=authority,
            events=(),
            candidate_at_intent=candidate_at_intent,
        ),
    )
    return EpochDrainResult(waiting.status, status_path, None)


def _same_authority(first: _Authority, second: _Authority) -> bool:
    return (
        first.r1_event["entry_sha256"] == second.r1_event["entry_sha256"]
        and first.preparation_event["entry_sha256"]
        == second.preparation_event["entry_sha256"]
        and first.r1_event["candidate_id"] == second.r1_event["candidate_id"]
    )


def _current_authority_gate(
    profile: Mapping[str, Any], paths: DrainPaths, expected: _Authority
) -> _Authority | _Waiting:
    latest = _load_authority(profile, paths)
    if isinstance(latest, _Waiting):
        return latest
    if not _same_authority(expected, latest):
        return _Waiting(
            "waiting_for_current_candidate_authority",
            "prepared drain authority is no longer the replayed R1/R2a tip",
        )
    return latest


def _source_authority_extends(
    first: Mapping[str, object], second: Mapping[str, object]
) -> bool:
    if first.get("outcome_source_id") != second.get("outcome_source_id") or first.get(
        "activation_manifest"
    ) != second.get("activation_manifest"):
        return False
    first_records = first.get("records")
    second_records = second.get("records")
    first_revisions = first.get("revision_ids_by_date")
    second_revisions = second.get("revision_ids_by_date")
    if not all(
        isinstance(value, list)
        for value in (
            first_records,
            second_records,
            first_revisions,
            second_revisions,
        )
    ):
        return False
    if len(second_records) < len(first_records):
        return False
    revision_history: dict[str, list[object]] = {}
    for item in second_revisions:
        if not isinstance(item, dict) or not isinstance(item.get("date"), str):
            return False
        revisions = item.get("revision_ids")
        if not isinstance(revisions, list):
            return False
        revision_history[item["date"]] = revisions
    for previous, current in zip(first_records, second_records, strict=False):
        if not isinstance(previous, dict) or not isinstance(current, dict):
            return False
        if previous.get("date") != current.get("date"):
            return False
        if previous == current:
            continue
        if previous.get("revision_id") == current.get("revision_id") or previous.get(
            "revision_id"
        ) not in revision_history.get(str(previous.get("date")), []):
            return False
    for previous in first_revisions:
        if not isinstance(previous, dict):
            return False
        known = revision_history.get(str(previous.get("date")))
        revisions = previous.get("revision_ids")
        if not isinstance(revisions, list) or known is None:
            return False
        if known[: len(revisions)] != revisions:
            return False
    try:
        if date.fromisoformat(str(second["watermark"])) < date.fromisoformat(
            str(first["watermark"])
        ):
            return False
        if int(second["snapshot_sequence_id"]) < int(first["snapshot_sequence_id"]):
            return False
    except (KeyError, TypeError, ValueError):
        return False
    return True


def _outcome_inventory_extends(
    first: Sequence[Mapping[str, object]],
    second: Sequence[Mapping[str, object]],
) -> bool:
    current = {
        record.get("target_date"): record
        for record in second
        if isinstance(record.get("target_date"), str)
    }
    if len(current) != len(second):
        return False
    for previous in first:
        target = previous.get("target_date")
        latest = current.get(target)
        if latest is None:
            return False
        previous_receipts = previous.get("receipts")
        latest_receipts = latest.get("receipts")
        if not isinstance(previous_receipts, list) or not isinstance(
            latest_receipts, list
        ):
            return False
        if latest_receipts[: len(previous_receipts)] != previous_receipts:
            return False
    return True


def _inventory_subset_by_key(
    first: Sequence[Mapping[str, object]],
    second: Sequence[Mapping[str, object]],
    *,
    key: str,
) -> bool:
    previous = {
        record.get(key): record for record in first if isinstance(record.get(key), str)
    }
    current = {
        record.get(key): record for record in second if isinstance(record.get(key), str)
    }
    return (
        len(previous) == len(first)
        and len(current) == len(second)
        and all(
            current.get(natural_key) == record
            for natural_key, record in previous.items()
        )
    )


def _clean_state_extends(first: _CleanState, second: _CleanState) -> bool:
    return (
        first.old_live_epoch_id == second.old_live_epoch_id
        and second.live_event_count >= first.live_event_count
        and second.live_entry_sha256s[: first.live_event_count]
        == first.live_entry_sha256s
        and second.guard_record_count >= first.guard_record_count
        and _inventory_subset_by_key(
            first.guard_inventory, second.guard_inventory, key="target_date"
        )
        and second.trusted_time_record_count >= first.trusted_time_record_count
        and _inventory_subset_by_key(
            first.trusted_time_inventory,
            second.trusted_time_inventory,
            key="target_date",
        )
        and second.shadow_event_count >= first.shadow_event_count
        and second.shadow_entry_sha256s[: first.shadow_event_count]
        == first.shadow_entry_sha256s
        and _inventory_subset_by_key(
            first.issue_route_inventory,
            second.issue_route_inventory,
            key="target_date",
        )
        and _outcome_inventory_extends(
            first.outcome_registry_inventory, second.outcome_registry_inventory
        )
        and _source_authority_extends(first.source_authority, second.source_authority)
    )


def _draining_result(
    profile: Mapping[str, Any],
    paths: DrainPaths,
    authority: _Authority,
    events: Sequence[Mapping[str, Any]],
    *,
    checked_at_utc: str,
    status: str,
    reason: str,
) -> EpochDrainResult:
    if len(events) != 1:
        raise EpochDrainIntegrityError("DRAINING status requires one authority event")
    _refresh_head(profile, paths, events)
    status_path = _write_status(
        profile,
        paths,
        _status_payload(
            profile,
            checked_at_utc=checked_at_utc,
            status=status,
            reason=reason,
            authority=authority,
            events=events,
            candidate_at_intent=True,
        ),
    )
    return EpochDrainResult(status, status_path, _event_path(paths))


def _validate_private_roots(
    runtime_root: Path | None,
    active_runtime_root: Path | None,
    shadow_runtime_root: Path | None,
    clock: Clock | None,
) -> None:
    injected = any(
        value is not None
        for value in (runtime_root, active_runtime_root, shadow_runtime_root, clock)
    )
    if not injected:
        return
    if (
        runtime_root is None
        or active_runtime_root is None
        or shadow_runtime_root is None
    ):
        raise EpochDrainConfigError(
            "Private drain injection requires all three isolated runtime roots"
        )
    production = registry._absolute_lexical(ROOT / "runtime")  # noqa: SLF001
    for value in (runtime_root, active_runtime_root, shadow_runtime_root):
        candidate = registry._absolute_lexical(  # noqa: SLF001
            value.parent.resolve(strict=False) / value.name
        )
        try:
            candidate.relative_to(production)
        except ValueError:
            continue
        raise EpochDrainConfigError(
            "Private drain overrides cannot target the production runtime tree"
        )


def _start_epoch_drain(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    runtime_root: Path | None = None,
    active_runtime_root: Path | None = None,
    shadow_runtime_root: Path | None = None,
    clock: Clock | None = None,
) -> EpochDrainResult:
    _validate_private_roots(
        runtime_root, active_runtime_root, shadow_runtime_root, clock
    )
    profile = load_drain_profile(config_path)
    paths = drain_paths(
        profile,
        runtime_root=runtime_root,
        active_runtime_root=active_runtime_root,
        shadow_runtime_root=shadow_runtime_root,
    )
    status_clock = clock or (lambda: datetime.now(timezone.utc))
    checked_at = _utc_text(status_clock)
    machine_now = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    try:
        registry._mkdir(paths.root, root=paths.root)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise EpochDrainIntegrityError(str(exc)) from exc
    handles: list[BinaryIO] = []
    durable_authority: _Authority | None = None
    durable_intent = False
    durable_events: tuple[dict[str, Any], ...] = ()
    try:
        for label, path in (
            ("manager", paths.manager_lock),
            ("cycle", paths.cycle_lock),
            ("deploy", paths.deploy_lock),
            ("runner", paths.runner_lock),
            ("replay", paths.replay_lock),
            ("shadow", paths.shadow_lock),
        ):
            handles.append(_acquire_lock(path, label=label))
        try:
            registry._cleanup_temporary_namespace(paths.root)  # noqa: SLF001
            binding = _persisted_authority_binding(paths)
            loaded = _load_authority(
                profile,
                paths,
                registry_entry_sha256=binding[0] if binding else None,
                preparation_entry_sha256=binding[1] if binding else None,
            )
            if isinstance(loaded, _Waiting):
                return _waiting_result(
                    profile,
                    paths,
                    loaded,
                    checked_at_utc=checked_at,
                    authority=None,
                )
            authority = loaded
            durable_authority = authority

            # A committed event is the sole lifecycle authority.  Replay it from
            # its immutable capsule before consulting mutable assessor state.
            events = replay_drain(profile, paths, authority, None, verify_fence=False)
            if events:
                durable_events = events
                intent = _load_single_intent(paths)
                if intent is None:
                    raise EpochDrainIntegrityError(
                        "DRAINING event lost its fence intent"
                    )
                intent_payload, capsule, _ = _verify_intent(
                    profile,
                    paths,
                    authority,
                    None,
                    intent,
                    require_current_implementation=False,
                )
                _, boundary_clean = _verify_boundary(
                    profile,
                    paths,
                    authority,
                    intent,
                    capsule,
                    events[0].get("drain_boundary"),
                )
                archive = Path(str(intent_payload["archive_route"]))
                route_state = _route_state(paths, intent_payload)
                if route_state == "prepared":
                    # All known issue creators are excluded by the six locks.
                    # The archive fence retains its deny-add ACL while its mode is
                    # made exchangeable; atomic swap then restores only DRAINING.
                    _verify_prepared_fence(
                        paths,
                        intent_payload,
                        recover_hardened_fence=True,
                    )
                    _rename_exchange(paths.issue_inbox, archive)
                    _harden_fenced_route(paths, intent_payload)
                else:
                    _verify_fenced_route(paths, intent_payload)
                _durably_reverify_exchange(paths, intent_payload)
                _verify_draining_runtime_prefix(paths, boundary_clean, archive)
                current = _load_clean_state(
                    paths,
                    machine_now=machine_now,
                    archived_issue_route=archive,
                    allow_queued_incoming=True,
                )
                if isinstance(current, _Waiting):
                    return _draining_result(
                        profile,
                        paths,
                        authority,
                        events,
                        checked_at_utc=checked_at,
                        status="epoch_draining_assessment_waiting",
                        reason=f"{current.status}:{current.reason}",
                    )
                replay_drain(profile, paths, authority, current)
                return _draining_result(
                    profile,
                    paths,
                    authority,
                    events,
                    checked_at_utc=checked_at,
                    status="already_epoch_draining_idempotent",
                    reason="replayed epoch_drain_started remains the unique tip",
                )

            intent = _load_single_intent(paths)
            fence_prepare = _load_single_fence_prepare(paths)
            if intent is None and _exchange_attempt_entries(paths):
                raise EpochDrainIntegrityError(
                    "Exchange-attempt chain exists without its fence intent"
                )
            intent_payload: dict[str, Any] | None = None
            capsule: registry.ArtifactSnapshot | None = None
            start_clean: _CleanState | None = None
            state: str | None = None
            if intent is not None:
                intent_payload, capsule, start_clean = _verify_intent(
                    profile,
                    paths,
                    authority,
                    None,
                    intent,
                    require_current_implementation=False,
                )
                durable_intent = True
                state = _route_state(paths, intent_payload)
                if state == "prepared":
                    intent_payload, capsule, start_clean = _verify_intent(
                        profile,
                        paths,
                        authority,
                        None,
                        intent,
                        require_current_implementation=True,
                    )
                    prepared_attempts = _scan_exchange_attempts(
                        profile,
                        paths,
                        authority,
                        intent,
                        intent_payload,
                        capsule,
                    )
                    if prepared_attempts:
                        _arm_exchange_attempt(
                            profile,
                            paths,
                            authority,
                            intent,
                            intent_payload,
                            capsule,
                            prepared_attempts[-1],
                        )

            # A crash after exchange is completed from the sealed start snapshot;
            # mutable assessor lag cannot undo an already-active route fence.
            if state == "exchanged":
                assert intent is not None
                assert intent_payload is not None
                assert capsule is not None
                assert start_clean is not None
                attempts = _scan_exchange_attempts(
                    profile,
                    paths,
                    authority,
                    intent,
                    intent_payload,
                    capsule,
                )
                if not attempts:
                    raise EpochDrainIntegrityError(
                        "Exchanged issue route has no durable exchange attempt"
                    )
                exchange_attempt = _select_armed_attempt(
                    profile,
                    paths,
                    authority,
                    intent,
                    intent_payload,
                    attempts,
                )
                _harden_fenced_route(paths, intent_payload)
                archive = Path(str(intent_payload["archive_route"]))
                _durably_reverify_exchange(paths, intent_payload)
                recovery_clean = _load_clean_state(
                    paths,
                    machine_now=machine_now,
                    archived_issue_route=archive,
                    allow_queued_incoming=True,
                )
                if isinstance(recovery_clean, _Waiting):
                    return _waiting_result(
                        profile,
                        paths,
                        recovery_clean,
                        checked_at_utc=checked_at,
                        authority=authority,
                    )
                intent_payload, capsule, _ = _verify_intent(
                    profile,
                    paths,
                    authority,
                    recovery_clean,
                    intent,
                    require_current_implementation=False,
                )
                final_authority = _load_authority(
                    profile,
                    paths,
                    registry_entry_sha256=authority.r1_event["entry_sha256"],
                    preparation_entry_sha256=authority.preparation_event[
                        "entry_sha256"
                    ],
                )
                if not isinstance(final_authority, _Authority) or not _same_authority(
                    authority, final_authority
                ):
                    raise EpochDrainIntegrityError(
                        "R1/R2a authority changed during orphan recovery"
                    )
                boundary, boundary_clean = _verify_boundary(
                    profile,
                    paths,
                    authority,
                    intent,
                    capsule,
                    _artifact_reference(exchange_attempt.boundary, paths.root),
                    clean=recovery_clean,
                )
                if (
                    not _same_snapshot(boundary, exchange_attempt.boundary)
                    or boundary_clean != exchange_attempt.boundary_clean
                ):
                    raise EpochDrainIntegrityError(
                        "Exchanged recovery lost its exact pre-swap boundary"
                    )
                event = _append_event(
                    profile,
                    paths,
                    authority,
                    recovery_clean,
                    intent,
                    capsule,
                    boundary,
                    exchange_attempt,
                    recorded_at_utc=checked_at,
                )
                replayed = replay_drain(profile, paths, authority, recovery_clean)
                if len(replayed) != 1 or replayed[0] != event:
                    raise EpochDrainIntegrityError(
                        "Recovered epoch_drain_started is not the unique authority tip"
                    )
                durable_events = replayed
                return _draining_result(
                    profile,
                    paths,
                    authority,
                    replayed,
                    checked_at_utc=checked_at,
                    status="already_epoch_draining_idempotent",
                    reason=(
                        "recovered exchanged fence and committed epoch_drain_started"
                    ),
                )

            # No exchange has occurred.  Full clean-state assessment is therefore
            # mandatory before publishing or acting on a prepared intent.
            if intent is None and fence_prepare is None:
                source_gate = _load_source_gate(paths, machine_now=machine_now)
                if isinstance(source_gate, _Waiting):
                    return _waiting_result(
                        profile,
                        paths,
                        source_gate,
                        checked_at_utc=checked_at,
                        authority=authority,
                    )
                feed_waiting = _pre_intent_feed_gate(
                    paths,
                    authority,
                    source_gate,
                    machine_now=machine_now,
                )
                if feed_waiting is not None:
                    return _waiting_result(
                        profile,
                        paths,
                        feed_waiting,
                        checked_at_utc=checked_at,
                        authority=authority,
                    )
            clean_loaded = _load_clean_state(
                paths,
                machine_now=machine_now,
                allow_queued_incoming=(intent is not None or fence_prepare is not None),
            )
            if isinstance(clean_loaded, _Waiting):
                return _waiting_result(
                    profile,
                    paths,
                    clean_loaded,
                    checked_at_utc=checked_at,
                    authority=authority,
                )
            clean = clean_loaded
            if intent is None:
                if fence_prepare is None:
                    capsule = _publish_capsule(profile, paths, authority, clean)
                    route_identity = _unfenced_route_identity(paths)
                    fence_prepare = _publish_fence_prepare(
                        profile,
                        paths,
                        authority,
                        capsule,
                        route_identity,
                    )
                else:
                    try:
                        prepare_record = registry._decode_json(  # noqa: SLF001
                            fence_prepare.raw, name="drain fence prepare"
                        )
                    except registry.EpochRegistryError as exc:
                        raise EpochDrainIntegrityError(str(exc)) from exc
                    capsule_ref = _read_reference(
                        prepare_record.get("drain_capsule"),
                        root=paths.root,
                        name="prepared drain capsule",
                        maximum_bytes=MAX_MANIFEST_BYTES,
                    )
                    capsule, _ = _verify_capsule(
                        profile,
                        paths,
                        authority,
                        clean,
                        _artifact_reference(capsule_ref, paths.root),
                        require_current_implementation=True,
                    )
                prepare_payload = _verify_fence_prepare(
                    profile,
                    paths,
                    authority,
                    capsule,
                    fence_prepare,
                    require_unexchanged_route=True,
                )
                tombstone, old_identity, fence_identity = _ensure_route_and_tombstone(
                    paths,
                    authority.r1_event["candidate_id"],
                    prepare_payload,
                )
                intent = _publish_intent(
                    profile,
                    paths,
                    authority,
                    clean,
                    capsule,
                    fence_prepare,
                    tombstone,
                    old_identity,
                    fence_identity,
                )
            intent_payload, capsule, _ = _verify_intent(
                profile, paths, authority, clean, intent
            )
            durable_intent = True
            state = _route_state(paths, intent_payload)
            if state != "prepared":
                raise EpochDrainIntegrityError("Drain intent changed exchange state")
            _verify_prepared_fence(paths, intent_payload)

            # The create-only intent is a durable transaction reservation, not
            # an activation choice or a producer freeze.  Re-run the complete
            # assessor under all six locks and permit only a recursively verified
            # append-only extension of the initial clean prefix.  A queued feed is
            # staged for the next epoch and is outside old-source authority.
            final_pre_swap_clean = _load_clean_state(
                paths,
                machine_now=machine_now,
                allow_queued_incoming=True,
            )
            if isinstance(final_pre_swap_clean, _Waiting):
                return _waiting_result(
                    profile,
                    paths,
                    final_pre_swap_clean,
                    checked_at_utc=checked_at,
                    authority=authority,
                )
            _verify_intent(
                profile,
                paths,
                authority,
                final_pre_swap_clean,
                intent,
                require_current_implementation=True,
            )
            if not _clean_state_extends(clean, final_pre_swap_clean):
                raise EpochDrainIntegrityError(
                    "Old runtime state rolled back during drain reservation"
                )
            if fence_prepare is None:
                raise EpochDrainIntegrityError(
                    "Prepared drain lost its fence-prepare authority"
                )
            capacity = _preflight_boundary_capacity(
                profile,
                paths,
                authority,
                final_pre_swap_clean,
                intent,
                fence_prepare,
                capsule,
                captured_at_utc=checked_at,
            )
            if capacity is not None:
                return _waiting_result(
                    profile,
                    paths,
                    capacity,
                    checked_at_utc=checked_at,
                    authority=authority,
                )

            _verify_prepared_fence(paths, intent_payload)

            # Capture and validate the actual external delivery after the final
            # fence identity check and immediately before the irreversible
            # exchange.  Only the fixed-size CAS reference enters the boundary;
            # the original queue remains intact.
            #
            # Known issue writers are excluded by the six locks.  The feed queue
            # is external, but it cannot mutate either exchange operand.
            staged_before_swap = _staged_next_epoch_snapshot(
                paths, machine_now=machine_now
            )
            actual_capacity = _preflight_boundary_capacity(
                profile,
                paths,
                authority,
                final_pre_swap_clean,
                intent,
                fence_prepare,
                capsule,
                captured_at_utc=checked_at,
                staged_incoming=staged_before_swap,
            )
            if actual_capacity is not None:
                return _waiting_result(
                    profile,
                    paths,
                    actual_capacity,
                    checked_at_utc=checked_at,
                    authority=authority,
                )
            exchange_attempt = _prepare_exchange_attempt(
                profile,
                paths,
                authority,
                final_pre_swap_clean,
                intent,
                intent_payload,
                capsule,
                staged_before_swap,
                machine_now=machine_now,
                captured_at_utc=checked_at,
            )
            exchange_attempt = _arm_exchange_attempt(
                profile,
                paths,
                authority,
                intent,
                intent_payload,
                capsule,
                exchange_attempt,
            )
            _rename_exchange(
                paths.issue_inbox, Path(str(intent_payload["archive_route"]))
            )
            _harden_fenced_route(paths, intent_payload)
            _durably_reverify_exchange(paths, intent_payload)

            final_authority = _load_authority(
                profile,
                paths,
                registry_entry_sha256=authority.r1_event["entry_sha256"],
                preparation_entry_sha256=authority.preparation_event["entry_sha256"],
            )
            final_clean = _load_clean_state(
                paths,
                machine_now=machine_now,
                archived_issue_route=Path(str(intent_payload["archive_route"])),
                allow_queued_incoming=True,
            )
            if not isinstance(final_authority, _Authority) or not _same_authority(
                authority, final_authority
            ):
                raise EpochDrainIntegrityError(
                    "R1/R2a authority changed during the drain barrier"
                )
            if isinstance(final_clean, _Waiting):
                return _waiting_result(
                    profile,
                    paths,
                    final_clean,
                    checked_at_utc=checked_at,
                    authority=authority,
                )
            if final_clean != final_pre_swap_clean:
                raise EpochDrainIntegrityError(
                    "Old runtime logical state changed across the atomic drain barrier"
                )
            intent_payload, capsule, _ = _verify_intent(
                profile,
                paths,
                authority,
                final_clean,
                intent,
                require_current_implementation=True,
            )
            boundary = exchange_attempt.boundary
            if exchange_attempt.boundary_clean != final_clean:
                raise EpochDrainIntegrityError(
                    "Exchange attempt differs from the physical drain boundary"
                )
            event = _append_event(
                profile,
                paths,
                authority,
                final_clean,
                intent,
                capsule,
                boundary,
                exchange_attempt,
                recorded_at_utc=checked_at,
            )
            replayed = replay_drain(profile, paths, authority, final_clean)
            if len(replayed) != 1 or replayed[0] != event:
                raise EpochDrainIntegrityError(
                    "epoch_drain_started did not become the unique authority tip"
                )
            durable_events = replayed
            return _draining_result(
                profile,
                paths,
                authority,
                replayed,
                checked_at_utc=checked_at,
                status="epoch_draining",
                reason=(
                    "canonical old issue route atomically fenced; old epoch "
                    "entered DRAINING and is not declared drained"
                ),
            )
        except (
            registry.EpochRegistryError,
            preparation.EpochPreparationError,
        ) as exc:
            raise EpochDrainIntegrityError(str(exc)) from exc
    except EpochDrainBusyError:
        raise
    except Exception as exc:
        normalized = (
            exc
            if isinstance(exc, EpochDrainError)
            else EpochDrainIntegrityError(
                f"Drain coordinator failed:{type(exc).__name__}:{exc}"
            )
        )
        try:
            _write_status(
                profile,
                paths,
                _status_payload(
                    profile,
                    checked_at_utc=checked_at,
                    status="blocked_integrity",
                    reason=f"{type(normalized).__name__}:{normalized}",
                    authority=durable_authority,
                    events=durable_events,
                    candidate_at_intent=bool(durable_events) or durable_intent,
                ),
            )
        except EpochDrainError:
            pass
        raise normalized
    finally:
        _release_locks(handles)


def start_epoch_drain(*, config_path: Path = DEFAULT_CONFIG_PATH) -> EpochDrainResult:
    """Run R2b from reviewed configuration and machine-owned state only."""

    return _start_epoch_drain(config_path=config_path)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = start_epoch_drain(config_path=args.config)
    except EpochDrainBusyError as exc:
        print(f"[ootang-epoch-drain] busy: {exc}", file=sys.stderr)
        return 3
    except EpochDrainError as exc:
        print(f"[ootang-epoch-drain] blocked: {exc}", file=sys.stderr)
        return 2
    print(result.status_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
