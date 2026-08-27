"""Physically close the frozen old-epoch writer lock paths.

This explicit R2b-2b-2a engineering stage preserves every self-bound legacy
writer byte.  Under the reviewed global lock order it atomically exchanges the
canonical deploy and runner lock files with deny-write regular-file sentinels.
It does not enumerate, reserve, recover, drain, seal, or activate an epoch.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
import ctypes
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any, BinaryIO


ROOT = Path(__file__).resolve().parents[2]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_epoch_drain as drain  # noqa: E402
from monitoring import ootang_epoch_drain_v2 as drain_v2  # noqa: E402
from monitoring import ootang_epoch_registry as registry  # noqa: E402


DEFAULT_CONFIG_PATH = ROOT / "config" / "ootang_epoch_admission_cut.v1.json"
DEFAULT_CONFIG_SHA256 = (
    "fe4e91768c8558d887a34465fa6c9f4e8c05f8c1a7cf07e061bc602733136c1c"
)
MAX_CONTROL_BYTES = 4 * 1024 * 1024
ZERO_HASH = "0" * 64
RENAME_SWAP = 0x00000002
LOCK_ORDER = ("manager", "cycle", "deploy", "runner", "replay", "shadow")
CUT_ORDER = ("deploy", "runner")

TRUE_CAPABILITIES = (
    "machine_only",
    "frozen_writer_bytes_preserved",
    "official_writer_lock_path_cut_implemented",
    "deploy_lock_path_cut_implemented",
    "runner_lock_path_cut_implemented",
    "physical_cut_crash_resume_implemented",
)
FALSE_CLAIMS = (
    "complete_workset_enumeration",
    "bounded_workset_reservation_implemented",
    "bounded_workset_recovery_implemented",
    "old_work_admission_fence_implemented",
    "canonical_old_issue_route_fence_implemented",
    "direct_filesystem_writer_fence_implemented",
    "v1_v2_mutual_exclusion_implemented",
    "scheduler_entrypoint_authorization_implemented",
    "anti_rollback_authority_implemented",
    "epoch_drain_started_implemented",
    "lifecycle_authority",
    "transition_authority",
    "drained_eligibility_current",
    "old_epoch_drained",
    "old_epoch_drain_implemented",
    "active_epoch_switch_implemented",
    "automatic_epoch_rotation_implemented",
    "activation_candidate_selected",
    "trusted_anchor_receipt_verified",
    "e2_live_evidence_eligible",
    "real_activation_ready",
    "formal_warning_output",
)

EXPECTED_UPSTREAM = {
    "drain_v2_profile": {
        "path": "config/ootang_epoch_drain.v2.json",
        "expected_sha256": drain_v2.DEFAULT_CONFIG_SHA256,
    },
    "drain_v2_implementation": {
        "path": "code/monitoring/ootang_epoch_drain_v2.py",
        "expected_sha256": (
            "93a6463f514d73c4809287e1bbc8033984c82f550d5c55ce84c209475d7b604b"
        ),
    },
    "drain_v1_profile": {
        "path": "config/ootang_epoch_drain.v1.json",
        "expected_sha256": drain.DEFAULT_CONFIG_SHA256,
    },
    "drain_v1_implementation": {
        "path": "code/monitoring/ootang_epoch_drain.py",
        "expected_sha256": (
            "c4c226da087d354195fc3955ff60ff3b12af13f111d03cb59c5d64d0195a1602"
        ),
    },
}

EXPECTED_FROZEN_WRITERS = {
    "source_ingest": {
        "path": "code/monitoring/ootang_live_source.py",
        "expected_sha256": (
            "7513a214a563a7f26c345c855748f28643df88685ae1d0006f84d8c339c85110"
        ),
    },
    "issue_producer": {
        "path": "code/monitoring/ootang_issue_producer.py",
        "expected_sha256": (
            "88df17367beef9418502f1eba7e92b21955849fd6bfb36cfe2b79212291e45a0"
        ),
    },
    "outcome_materializer": {
        "path": "code/monitoring/ootang_outcome_materializer.py",
        "expected_sha256": (
            "2a6478c191fdf1cd0df7d78324c6e4d7637241683956de621fd319feb3bcb642"
        ),
    },
    "legacy_live_runner": {
        "path": "code/monitoring/ootang_prequential_live.py",
        "expected_sha256": (
            "086638e87ded39eed9b84311fa8a49898bf1c34d48327b41ac6e1d4633f843d8"
        ),
    },
    "verified_live_runner": {
        "path": "code/monitoring/ootang_verified_live.py",
        "expected_sha256": (
            "e8fc8264c291294207f35416b921940edb8324fdd3f526b9bacacde0988e5979"
        ),
    },
    "issue_replay": {
        "path": "code/monitoring/ootang_issue_replay.py",
        "expected_sha256": (
            "6dd550ad30cee34e431e2fea8dfbe2e8b43edb29eadfa1eaf279d2b177822381"
        ),
    },
    "trusted_time": {
        "path": "code/monitoring/ootang_trusted_time_shadow_core.py",
        "expected_sha256": (
            "797cedbc1e24fac6e4cbf042f48981786b662ce8b0fa913b988bce12818023c7"
        ),
    },
    "calibration_shadow": {
        "path": "code/monitoring/ootang_prequential_calibration_shadow.py",
        "expected_sha256": (
            "de5fd1a13b4a3b9e9a0d4bda68b4dfe21032f6a1194d30f32370facca74f1fe7"
        ),
    },
    "cycle_v1": {
        "path": "code/monitoring/ootang_prequential_cycle.py",
        "expected_sha256": (
            "448c57ac3686c9a7a43c7eb35b2d6204ccefb00908d26cd08f519d4e16ee4937"
        ),
    },
    "cycle_v2": {
        "path": "code/monitoring/ootang_prequential_cycle_v2.py",
        "expected_sha256": (
            "0fd7b3045fdd6ad45059448dea0ee9fe4f8db781e71680385de6eed7530ef0bb"
        ),
    },
    "cycle_v3": {
        "path": "code/monitoring/ootang_prequential_cycle_v3.py",
        "expected_sha256": (
            "0a5264fd6e324bf958fa98bd6416ca6ae687a9ac5a83b3fab6ec688c52b78b7b"
        ),
    },
}

EXPECTED_RUNTIME = {
    "root": "runtime/ootang_epoch_registry_v1",
    "namespace": "admission_cut_v1",
    "manager_lock": "manager.lock",
    "prepare": "prepare.json",
    "intent": "intent.json",
    "attempts": "attempts",
    "events": "events",
    "status": "status.json",
    "active_root": "runtime/ootang_prequential_live_v1",
    "shadow_root": "runtime/ootang_prequential_calibration_shadow_v1",
    "cycle_lock": "prequential_cycle.lock",
    "deploy_lock": "deploy_cycle.lock",
    "runner_lock": "runner.lock",
    "replay_lock": "issue_replay.lock",
    "shadow_lock": "runner.lock",
    "lock_archive_root": "epoch_admission_cut_v1/lock_archives",
    "deploy_lock_archive": "deploy_cycle.lock",
    "runner_lock_archive": "runner.lock",
}

EXPECTED_PROTOCOL = {
    "prepare_schema_version": "ootang_epoch_admission_cut_prepare_v1",
    "intent_schema_version": "ootang_epoch_admission_cut_intent_v1",
    "sentinel_schema_version": "ootang_epoch_admission_lock_sentinel_v1",
    "attempt_schema_version": "ootang_epoch_admission_cut_attempt_v1",
    "event_schema_version": "ootang_epoch_admission_cut_event_v1",
    "status_schema_version": "ootang_epoch_admission_cut_status_v1",
    "event_type": "official_writer_lock_paths_cut",
    "lock_order": list(LOCK_ORDER),
    "cut_order": list(CUT_ORDER),
    "atomic_exchange": (
        "darwin_regular_file_renameatx_np_RENAME_SWAP_0x00000002_no_fallback"
    ),
    "fence_acl_sha256": drain.FENCE_ACL_SHA256,
    "sentinel_mode": 0o444,
    "maximum_control_bytes": MAX_CONTROL_BYTES,
    "maximum_attempts": 1024,
    "initial_previous_entry_sha256": ZERO_HASH,
    "canonical_json": "utf8_sort_keys_compact_no_nan_trailing_lf",
    "v1_authority_policy": (
        "v1_precedence_before_first_physical_cut_then_official_writer_lock_paths_block_v1_progress"
    ),
    "recovery_policy": (
        "machine_resume_forward_never_restore_or_unfence_old_writer_lock_paths"
    ),
}


class AdmissionCutError(RuntimeError):
    """Base failure for the official-writer lock-path cut."""


class AdmissionCutConfigError(AdmissionCutError):
    """The reviewed profile or one frozen implementation changed."""


class AdmissionCutIntegrityError(AdmissionCutError):
    """A durable record or physical lock state failed closed."""


class AdmissionCutBusyError(AdmissionCutError):
    """A still-open globally ordered lock is busy."""


@dataclass(frozen=True)
class AdmissionCutPaths:
    registry_root: Path
    root: Path
    manager_lock: Path
    prepare: Path
    intent: Path
    attempts: Path
    events: Path
    status: Path
    active_root: Path
    shadow_root: Path
    cycle_lock: Path
    deploy_lock: Path
    runner_lock: Path
    replay_lock: Path
    shadow_lock: Path
    lock_archive_root: Path
    deploy_archive: Path
    runner_archive: Path


@dataclass(frozen=True)
class AdmissionCutResult:
    status: str
    reason: str
    status_path: Path
    event_path: Path | None
    prepare_path: Path | None
    intent_path: Path | None
    attempt_path: Path | None
    deploy_cut: bool = False
    runner_cut: bool = False
    lifecycle_authority: bool = False
    transition_authority: bool = False
    old_work_admission_fence_implemented: bool = False


InspectContext = Callable[[AdmissionCutPaths, datetime], drain_v2.WorksetContext]
Exchange = Callable[[Path, Path], None]
FaultHook = Callable[[str], None]


def _canonical_bytes(value: Mapping[str, object]) -> bytes:
    return registry._canonical_bytes(dict(value))  # noqa: SLF001


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _exact(value: object, keys: set[str], *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise AdmissionCutIntegrityError(f"{name} keys changed")
    return value


def _hash(value: object, *, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise AdmissionCutIntegrityError(f"{name} is not a lowercase SHA-256")
    return value


def _positive_integer(value: object, *, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise AdmissionCutIntegrityError(f"{name} is not a positive integer")
    return value


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise AdmissionCutIntegrityError("Admission-cut clock must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _claims() -> dict[str, bool]:
    return {
        **{name: True for name in TRUE_CAPABILITIES},
        **{name: False for name in FALSE_CLAIMS},
    }


def _call_fault_hook(fault_hook: FaultHook | None, point: str) -> None:
    if fault_hook is None:
        return
    try:
        fault_hook(point)
    except AdmissionCutError:
        raise
    except Exception as exc:
        raise AdmissionCutIntegrityError(
            f"Injected machine crash at {point}:{type(exc).__name__}:{exc}"
        ) from exc


def load_admission_cut_profile(
    path: Path = DEFAULT_CONFIG_PATH, *, project_root: Path = ROOT
) -> dict[str, Any]:
    """Load the single reviewed profile and verify every frozen byte binding."""

    root = project_root.resolve()
    resolved = path if path.is_absolute() else root / path
    try:
        resolved = registry._absolute_lexical(resolved)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise AdmissionCutConfigError(str(exc)) from exc
    if root != ROOT.resolve() or resolved != DEFAULT_CONFIG_PATH.resolve():
        raise AdmissionCutConfigError(
            "Only the reviewed default admission-cut profile is accepted"
        )
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            resolved,
            name="epoch admission-cut profile",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
        if snapshot.sha256 != DEFAULT_CONFIG_SHA256:
            raise AdmissionCutConfigError(
                "Reviewed admission-cut profile digest changed"
            )
        profile = registry._decode_json(  # noqa: SLF001
            snapshot.raw, name="epoch admission-cut profile"
        )
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
                "frozen_writers",
                "runtime",
                "protocol",
                "engineering_capabilities",
            },
            name="epoch admission-cut profile",
        )
        identity = (
            profile["schema_version"],
            profile["profile_id"],
            profile["profile_version"],
            profile["case"],
            profile["artifact_status"],
        )
        if identity != (
            "ootang_epoch_admission_cut_profile_v1",
            "ootang-epoch-admission-cut-v1",
            "1.0.0-writer-lock-cut",
            "ootang",
            "official_writer_lock_path_cut_engineering_only_no_closed_manifest_reservation_recovery_lifecycle_or_transition_authority",
        ):
            raise AdmissionCutConfigError("Admission-cut profile identity changed")
        for flag in (
            "formal_warning_output",
            "independent_label_used",
            "confirmatory_external_validation",
            "vajont_used",
            "default_pipeline_member",
        ):
            if profile[flag] is not False:
                raise AdmissionCutConfigError(f"Admission-cut {flag} must remain false")
        if profile["upstream"] != EXPECTED_UPSTREAM:
            raise AdmissionCutConfigError("Admission-cut upstream bindings changed")
        if profile["frozen_writers"] != EXPECTED_FROZEN_WRITERS:
            raise AdmissionCutConfigError(
                "Admission-cut frozen-writer bindings changed"
            )
        if profile["runtime"] != EXPECTED_RUNTIME:
            raise AdmissionCutConfigError("Admission-cut runtime mapping changed")
        if profile["protocol"] != EXPECTED_PROTOCOL:
            raise AdmissionCutConfigError("Admission-cut protocol changed")
        if profile["engineering_capabilities"] != _claims():
            raise AdmissionCutConfigError("Admission-cut capability boundary changed")
        for binding in (*EXPECTED_UPSTREAM.values(), *EXPECTED_FROZEN_WRITERS.values()):
            bound = registry._contained(  # noqa: SLF001
                root, binding["path"], name="admission-cut frozen input"
            )
            captured = registry._read_regular(  # noqa: SLF001
                bound, name="admission-cut frozen input"
            )
            if captured.sha256 != binding["expected_sha256"]:
                raise AdmissionCutConfigError(f"Frozen input changed:{binding['path']}")
    except registry.EpochRegistryError as exc:
        raise AdmissionCutConfigError(str(exc)) from exc
    profile["_project_root"] = str(root)
    profile["_profile_sha256"] = snapshot.sha256
    return profile


def admission_cut_paths(
    profile: Mapping[str, Any],
    *,
    runtime_root: Path | None = None,
    active_runtime_root: Path | None = None,
    shadow_runtime_root: Path | None = None,
) -> AdmissionCutPaths:
    """Resolve the private transaction namespace and the frozen writer locks."""

    project_root = Path(str(profile["_project_root"]))
    registry_root = (
        registry._contained(  # noqa: SLF001
            project_root, profile["runtime"]["root"], name="registry root"
        )
        if runtime_root is None
        else drain._override_root(runtime_root, name="runtime_root")  # noqa: SLF001
    )
    active_root = (
        registry._contained(  # noqa: SLF001
            project_root, profile["runtime"]["active_root"], name="active root"
        )
        if active_runtime_root is None
        else drain._override_root(  # noqa: SLF001
            active_runtime_root, name="active_runtime_root"
        )
    )
    shadow_root = (
        registry._contained(  # noqa: SLF001
            project_root, profile["runtime"]["shadow_root"], name="shadow root"
        )
        if shadow_runtime_root is None
        else drain._override_root(  # noqa: SLF001
            shadow_runtime_root, name="shadow_runtime_root"
        )
    )

    def child(base: Path, value: str, *, name: str) -> Path:
        try:
            return registry._contained(base, value, name=name)  # noqa: SLF001
        except registry.EpochRegistryError as exc:
            raise AdmissionCutConfigError(str(exc)) from exc

    root = child(
        registry_root, profile["runtime"]["namespace"], name="admission-cut root"
    )
    archive_root = child(
        active_root,
        profile["runtime"]["lock_archive_root"],
        name="admission-cut archive root",
    )
    paths = AdmissionCutPaths(
        registry_root=registry_root,
        root=root,
        manager_lock=child(
            registry_root, profile["runtime"]["manager_lock"], name="manager lock"
        ),
        prepare=child(root, profile["runtime"]["prepare"], name="cut prepare"),
        intent=child(root, profile["runtime"]["intent"], name="cut intent"),
        attempts=child(root, profile["runtime"]["attempts"], name="cut attempts"),
        events=child(root, profile["runtime"]["events"], name="cut events"),
        status=child(root, profile["runtime"]["status"], name="cut status"),
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
        lock_archive_root=archive_root,
        deploy_archive=child(
            archive_root,
            profile["runtime"]["deploy_lock_archive"],
            name="deploy lock archive",
        ),
        runner_archive=child(
            archive_root,
            profile["runtime"]["runner_lock_archive"],
            name="runner lock archive",
        ),
    )
    locks = (
        paths.manager_lock,
        paths.cycle_lock,
        paths.deploy_lock,
        paths.runner_lock,
        paths.replay_lock,
        paths.shadow_lock,
    )
    if len(set(locks)) != len(LOCK_ORDER):
        raise AdmissionCutConfigError("Globally ordered admission-cut locks collide")
    if (
        paths.deploy_archive == paths.deploy_lock
        or paths.runner_archive == paths.runner_lock
    ):
        raise AdmissionCutConfigError(
            "Admission-cut archives collide with canonical locks"
        )
    return paths


def _read_json(
    path: Path, *, name: str, maximum_bytes: int = MAX_CONTROL_BYTES
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            path, name=name, maximum_bytes=maximum_bytes
        )
        payload = registry._decode_json(snapshot.raw, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise AdmissionCutIntegrityError(str(exc)) from exc
    if snapshot.raw != _canonical_bytes(payload):
        raise AdmissionCutIntegrityError(f"{name} is not canonical JSON")
    return payload, snapshot


def _optional_json(
    path: Path, *, name: str
) -> tuple[dict[str, Any], registry.ArtifactSnapshot] | None:
    if not path.exists() and not path.is_symlink():
        return None
    return _read_json(path, name=name)


def _publish(
    path: Path, payload: Mapping[str, object], *, root: Path, name: str
) -> registry.ArtifactSnapshot:
    raw = _canonical_bytes(payload)
    if len(raw) > MAX_CONTROL_BYTES:
        raise AdmissionCutIntegrityError(f"{name} exceeds the control-byte limit")
    try:
        return drain._publish_once_durable(  # noqa: SLF001
            path, raw, root=root, name=name
        )
    except drain.EpochDrainError as exc:
        raise AdmissionCutIntegrityError(str(exc)) from exc


def _write_status(
    profile: Mapping[str, Any],
    paths: AdmissionCutPaths,
    *,
    checked_at: str,
    status: str,
    reason: str,
    event: Mapping[str, Any] | None,
    physical_state: str,
) -> None:
    payload: dict[str, object] = {
        "schema_version": profile["protocol"]["status_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "checked_at_utc": checked_at,
        "status": status,
        "reason": reason,
        "physical_state": physical_state,
        "event_count": 1 if event is not None else 0,
        "terminal_entry_sha256": (
            event["entry_sha256"] if event is not None else ZERO_HASH
        ),
        "cache_authority": False,
        **_claims(),
    }
    try:
        registry._atomic_cache(  # noqa: SLF001
            paths.status,
            _canonical_bytes(payload),
            root=paths.root,
            name="admission-cut status",
        )
    except registry.EpochRegistryError as exc:
        raise AdmissionCutIntegrityError(str(exc)) from exc


def _entry_payload(body: Mapping[str, object]) -> dict[str, object]:
    return {**body, "entry_sha256": _sha256(_canonical_bytes(body))}


def _validate_entry(payload: Mapping[str, Any], *, name: str) -> None:
    entry = _hash(payload.get("entry_sha256"), name=f"{name} entry SHA-256")
    body = dict(payload)
    body.pop("entry_sha256", None)
    if entry != _sha256(_canonical_bytes(body)):
        raise AdmissionCutIntegrityError(f"{name} self-hash changed")


def _reference(snapshot: registry.ArtifactSnapshot, *, path: str) -> dict[str, object]:
    return {
        "path": path,
        "sha256": snapshot.sha256,
        "size_bytes": snapshot.size_bytes,
    }


def _validate_reference(
    value: object,
    snapshot: registry.ArtifactSnapshot,
    *,
    path: str,
    name: str,
) -> None:
    reference = _exact(value, {"path", "sha256", "size_bytes"}, name=name)
    if reference != _reference(snapshot, path=path):
        raise AdmissionCutIntegrityError(f"{name} changed")


def _context_payload(context: drain_v2.WorksetContext) -> dict[str, object]:
    try:
        return drain_v2._context_payload(context)  # noqa: SLF001
    except drain_v2.DrainV2Error as exc:
        raise AdmissionCutIntegrityError(str(exc)) from exc


def _context_from_payload(value: object) -> drain_v2.WorksetContext:
    try:
        return drain_v2._context_from_payload(value)  # noqa: SLF001
    except drain_v2.DrainV2Error as exc:
        raise AdmissionCutIntegrityError(str(exc)) from exc


def _context_successor(
    previous: drain_v2.WorksetContext,
    current: drain_v2.WorksetContext,
    *,
    name: str,
) -> None:
    fixed_fields = (
        "registry_event_sequence_id",
        "registry_event_entry_sha256",
        "preparation_event_sequence_id",
        "preparation_event_entry_sha256",
        "candidate_id",
        "slot_id",
        "old_live_epoch_id",
    )
    if any(
        getattr(previous, field) != getattr(current, field) for field in fixed_fields
    ):
        raise AdmissionCutIntegrityError(f"{name} changed fixed old-epoch authority")
    if current.live_event_count < previous.live_event_count:
        raise AdmissionCutIntegrityError(f"{name} live ledger count rolled back")
    if (
        current.live_event_count == previous.live_event_count
        and current.live_terminal_sha256 != previous.live_terminal_sha256
    ):
        raise AdmissionCutIntegrityError(
            f"{name} live terminal changed without an append"
        )


def _snapshot_payload(
    snapshot: registry.ArtifactSnapshot, *, name: str
) -> dict[str, Any]:
    try:
        return registry._decode_json(snapshot.raw, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise AdmissionCutIntegrityError(str(exc)) from exc


def _snapshot_time(
    snapshot: registry.ArtifactSnapshot, *, field: str, name: str
) -> datetime:
    payload = _snapshot_payload(snapshot, name=name)
    try:
        return registry._utc_timestamp(payload.get(field), name=f"{name} {field}")  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise AdmissionCutIntegrityError(str(exc)) from exc


def _identity(path: Path, *, name: str) -> dict[str, int]:
    try:
        captured = os.lstat(path)
    except OSError as exc:
        raise AdmissionCutIntegrityError(f"Cannot inspect {name}") from exc
    if not stat.S_ISREG(captured.st_mode) or stat.S_ISLNK(captured.st_mode):
        raise AdmissionCutIntegrityError(f"{name} is not a real regular file")
    return {"device": captured.st_dev, "inode": captured.st_ino}


def _validated_identity(value: object, *, name: str) -> dict[str, int]:
    identity = _exact(value, {"device", "inode"}, name=name)
    for key in ("device", "inode"):
        _positive_integer(identity[key], name=f"{name} {key}")
    return identity


def _relative(path: Path, root: Path, *, name: str) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError as exc:
        raise AdmissionCutIntegrityError(f"{name} escaped its root") from exc


def _sentinel_payload(
    profile: Mapping[str, Any],
    paths: AdmissionCutPaths,
    prepare: registry.ArtifactSnapshot,
    *,
    label: str,
    canonical: Path,
    archive: Path,
) -> dict[str, object]:
    return {
        "schema_version": profile["protocol"]["sentinel_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "sentinel_role": "deny_write_official_writer_lock_path_exchange_operand",
        "lock_label": label,
        "canonical_path": _relative(canonical, paths.active_root, name=label),
        "archive_path": _relative(archive, paths.active_root, name=label),
        "prepare": _reference(prepare, path=paths.prepare.name),
        "fence_acl_sha256": drain.FENCE_ACL_SHA256,
        "sentinel_mode": profile["protocol"]["sentinel_mode"],
        **_claims(),
    }


def _probe_write_open_denied(
    path: Path,
    *,
    name: str,
    expected_identity: Mapping[str, int] | None = None,
) -> None:
    descriptor: int | None = None
    before = _identity(path, name=name)
    if expected_identity is not None and before != dict(expected_identity):
        raise AdmissionCutIntegrityError(f"{name} identity changed before denial probe")
    try:
        descriptor = os.open(
            path,
            os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        if exc.errno not in {errno.EACCES, errno.EPERM, errno.EROFS}:
            raise AdmissionCutIntegrityError(
                f"{name} write-open denial is ambiguous"
            ) from exc
    else:
        raise AdmissionCutIntegrityError(f"{name} accepted write-open")
    finally:
        if descriptor is not None:
            os.close(descriptor)
    after = _identity(path, name=name)
    if after != before:
        raise AdmissionCutIntegrityError(f"{name} identity changed during denial probe")


def _verify_sentinel(
    path: Path,
    raw: bytes,
    *,
    name: str,
    expected_identity: Mapping[str, int] | None = None,
) -> registry.ArtifactSnapshot:
    captured_raw = b""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size > MAX_CONTROL_BYTES:
            raise AdmissionCutIntegrityError(f"{name} is not an allowed regular file")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, min(65536, MAX_CONTROL_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_CONTROL_BYTES:
                raise AdmissionCutIntegrityError(f"{name} exceeds its byte limit")
        captured_raw = b"".join(chunks)
        after_read = os.fstat(descriptor)
        named = os.lstat(path)
        identity = {"device": opened.st_dev, "inode": opened.st_ino}
        if (
            captured_raw != raw
            or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
            != (
                after_read.st_dev,
                after_read.st_ino,
                after_read.st_size,
                after_read.st_mtime_ns,
            )
            or stat.S_ISLNK(named.st_mode)
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
            or stat.S_IMODE(opened.st_mode) != 0o444
            or drain._read_fence_acl(descriptor) != drain.FENCE_ACL_TEXT  # noqa: SLF001
        ):
            raise AdmissionCutIntegrityError(
                f"{name} bytes, ACL, mode, or inode changed"
            )
        if expected_identity is not None and identity != dict(expected_identity):
            raise AdmissionCutIntegrityError(f"{name} intent-bound inode changed")
    except drain.EpochDrainError as exc:
        raise AdmissionCutIntegrityError(str(exc)) from exc
    except OSError as exc:
        raise AdmissionCutIntegrityError(f"Cannot verify {name}") from exc
    finally:
        try:
            os.close(descriptor)
        except (NameError, OSError):
            pass
    _probe_write_open_denied(path, name=name, expected_identity=expected_identity)
    try:
        absolute = registry._absolute_lexical(path)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise AdmissionCutIntegrityError(str(exc)) from exc
    return registry.ArtifactSnapshot(
        absolute, captured_raw, _sha256(captured_raw), len(captured_raw)
    )


def _ensure_sentinel(
    path: Path,
    payload: Mapping[str, object],
    *,
    paths: AdmissionCutPaths,
    name: str,
) -> registry.ArtifactSnapshot:
    """Create or finish a pre-intent sentinel; never repair one after intent."""

    raw = _canonical_bytes(payload)
    try:
        registry._mkdir(path.parent, root=paths.active_root)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise AdmissionCutIntegrityError(str(exc)) from exc
    created = False
    descriptor: int | None = None
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
        created = True
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise AdmissionCutIntegrityError(f"Cannot write {name}")
            view = view[written:]
        os.fsync(descriptor)
    except FileExistsError:
        pass
    except OSError as exc:
        raise AdmissionCutIntegrityError(f"Cannot create {name}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if not created:
        try:
            adopted = registry._read_regular(  # noqa: SLF001
                path, name=name, maximum_bytes=MAX_CONTROL_BYTES
            )
        except registry.EpochRegistryError as exc:
            raise AdmissionCutIntegrityError(str(exc)) from exc
        if adopted.raw != raw:
            raise AdmissionCutIntegrityError(f"Existing {name} bytes changed")
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        acl = drain._read_fence_acl(descriptor, allow_missing=True)  # noqa: SLF001
        if acl is not None and acl != drain.FENCE_ACL_TEXT:
            raise AdmissionCutIntegrityError(f"{name} has an unknown ACL")
        if acl is None:
            # Mode is hardened first.  Therefore an exact ACL paired with a
            # later non-0444 mode is tamper, while a missing ACL is a uniquely
            # forward-recoverable pre-intent crash state.
            os.fchmod(descriptor, 0o444)
            drain._install_fence_acl(descriptor)  # noqa: SLF001
        elif stat.S_IMODE(before.st_mode) != 0o444:
            raise AdmissionCutIntegrityError(f"{name} mode changed after ACL install")
        os.fsync(descriptor)
        after = os.fstat(descriptor)
        named = os.lstat(path)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino) or (
            after.st_dev,
            after.st_ino,
        ) != (named.st_dev, named.st_ino):
            raise AdmissionCutIntegrityError(f"{name} changed while prepared")
    except (OSError, drain.EpochDrainError) as exc:
        if isinstance(exc, AdmissionCutError):
            raise
        raise AdmissionCutIntegrityError(f"Cannot finish {name}") from exc
    finally:
        try:
            os.close(descriptor)
        except (NameError, OSError):
            pass
    try:
        registry._fsync_directory(path.parent, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise AdmissionCutIntegrityError(str(exc)) from exc
    return _verify_sentinel(path, raw, name=name)


@dataclass(frozen=True)
class _V2Binding:
    profile: dict[str, Any]
    paths: drain_v2.DrainV2Paths
    event: dict[str, Any]
    event_snapshot: registry.ArtifactSnapshot
    observation_snapshot: registry.ArtifactSnapshot
    original_context: drain_v2.WorksetContext


def _load_v2_binding(paths: AdmissionCutPaths) -> _V2Binding | None:
    try:
        profile = drain_v2.load_drain_v2_profile()
        v2_paths = drain_v2.drain_v2_paths(
            profile,
            runtime_root=paths.registry_root,
            active_runtime_root=paths.active_root,
            shadow_runtime_root=paths.shadow_root,
        )
        existing = drain_v2._load_existing_event(profile, v2_paths)  # noqa: SLF001
    except drain_v2.DrainV2Error as exc:
        raise AdmissionCutIntegrityError(str(exc)) from exc
    if existing is None:
        return None
    event, event_snapshot, observation_snapshot = existing
    try:
        observation = registry._decode_json(  # noqa: SLF001
            observation_snapshot.raw, name="v2 first-blocker observation"
        )
    except registry.EpochRegistryError as exc:
        raise AdmissionCutIntegrityError(str(exc)) from exc
    context = _context_from_payload(observation.get("authority_context"))
    return _V2Binding(
        profile,
        v2_paths,
        event,
        event_snapshot,
        observation_snapshot,
        context,
    )


def _v2_references(binding: _V2Binding) -> dict[str, object]:
    return {
        "event": _reference(
            binding.event_snapshot,
            path=f"events/{binding.event_snapshot.path.name}",
        ),
        "observation": _reference(
            binding.observation_snapshot,
            path=(f"observations/sha256/{binding.observation_snapshot.path.name}"),
        ),
    }


def _prepare_body(
    profile: Mapping[str, Any],
    paths: AdmissionCutPaths,
    binding: _V2Binding,
    *,
    prepared_at: str,
) -> dict[str, object]:
    return {
        "schema_version": profile["protocol"]["prepare_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "prepared_at_utc": prepared_at,
        "transaction_role": "forward_only_official_writer_lock_path_cut",
        "v2_first_blocker": _v2_references(binding),
        "original_authority_context": _context_payload(binding.original_context),
        "lock_paths": {
            "deploy": {
                "canonical": _relative(
                    paths.deploy_lock, paths.active_root, name="deploy lock"
                ),
                "archive": _relative(
                    paths.deploy_archive, paths.active_root, name="deploy archive"
                ),
            },
            "runner": {
                "canonical": _relative(
                    paths.runner_lock, paths.active_root, name="runner lock"
                ),
                "archive": _relative(
                    paths.runner_archive, paths.active_root, name="runner archive"
                ),
            },
        },
        "lock_order": list(LOCK_ORDER),
        "cut_order": list(CUT_ORDER),
        "atomic_exchange": profile["protocol"]["atomic_exchange"],
        "recovery_policy": profile["protocol"]["recovery_policy"],
        **_claims(),
    }


def _load_prepare(
    profile: Mapping[str, Any],
    paths: AdmissionCutPaths,
    binding: _V2Binding,
) -> tuple[dict[str, Any], registry.ArtifactSnapshot] | None:
    existing = _optional_json(paths.prepare, name="admission-cut prepare")
    if existing is None:
        return None
    payload, snapshot = existing
    _exact(
        payload,
        {
            "schema_version",
            "profile_id",
            "profile_sha256",
            "prepared_at_utc",
            "transaction_role",
            "v2_first_blocker",
            "original_authority_context",
            "lock_paths",
            "lock_order",
            "cut_order",
            "atomic_exchange",
            "recovery_policy",
            *TRUE_CAPABILITIES,
            *FALSE_CLAIMS,
            "entry_sha256",
        },
        name="admission-cut prepare",
    )
    _validate_entry(payload, name="admission-cut prepare")
    try:
        registry._utc_timestamp(  # noqa: SLF001
            payload["prepared_at_utc"], name="admission-cut prepared_at_utc"
        )
    except registry.EpochRegistryError as exc:
        raise AdmissionCutIntegrityError(str(exc)) from exc
    expected = _prepare_body(
        profile,
        paths,
        binding,
        prepared_at=payload["prepared_at_utc"],
    )
    if payload != _entry_payload(expected):
        raise AdmissionCutIntegrityError("Admission-cut prepare semantics changed")
    return payload, snapshot


def _publish_prepare(
    profile: Mapping[str, Any],
    paths: AdmissionCutPaths,
    binding: _V2Binding,
    *,
    prepared_at: str,
) -> tuple[dict[str, Any], registry.ArtifactSnapshot, bool]:
    existing = _load_prepare(profile, paths, binding)
    if existing is not None:
        return existing[0], existing[1], False
    try:
        prepared = registry._utc_timestamp(  # noqa: SLF001
            prepared_at, name="admission-cut prepare time"
        )
        observed = registry._utc_timestamp(  # noqa: SLF001
            binding.event["recorded_at_utc"], name="v2 observation event time"
        )
    except registry.EpochRegistryError as exc:
        raise AdmissionCutIntegrityError(str(exc)) from exc
    if prepared < observed:
        raise AdmissionCutIntegrityError(
            "Admission-cut prepare predates its v2 observation"
        )
    payload = _entry_payload(
        _prepare_body(profile, paths, binding, prepared_at=prepared_at)
    )
    snapshot = _publish(
        paths.prepare,
        payload,
        root=paths.root,
        name="admission-cut prepare",
    )
    replayed = _load_prepare(profile, paths, binding)
    if replayed is None or replayed[0] != payload:
        raise AdmissionCutIntegrityError("Admission-cut prepare did not replay")
    return payload, snapshot, True


def _sentinel_raws(
    profile: Mapping[str, Any],
    paths: AdmissionCutPaths,
    prepare_snapshot: registry.ArtifactSnapshot,
) -> dict[str, bytes]:
    return {
        "deploy": _canonical_bytes(
            _sentinel_payload(
                profile,
                paths,
                prepare_snapshot,
                label="deploy",
                canonical=paths.deploy_lock,
                archive=paths.deploy_archive,
            )
        ),
        "runner": _canonical_bytes(
            _sentinel_payload(
                profile,
                paths,
                prepare_snapshot,
                label="runner",
                canonical=paths.runner_lock,
                archive=paths.runner_archive,
            )
        ),
    }


def _intent_body(
    profile: Mapping[str, Any],
    paths: AdmissionCutPaths,
    prepare_snapshot: registry.ArtifactSnapshot,
    sentinels: Mapping[str, registry.ArtifactSnapshot],
    *,
    recorded_at: str,
) -> dict[str, object]:
    bindings: list[dict[str, object]] = []
    for label, canonical, archive in (
        ("deploy", paths.deploy_lock, paths.deploy_archive),
        ("runner", paths.runner_lock, paths.runner_archive),
    ):
        bindings.append(
            {
                "lock_label": label,
                "canonical_path": _relative(
                    canonical, paths.active_root, name=f"{label} lock"
                ),
                "archive_path": _relative(
                    archive, paths.active_root, name=f"{label} archive"
                ),
                "old_lock_identity": _identity(canonical, name=f"old {label} lock"),
                "sentinel_identity": _identity(archive, name=f"{label} sentinel"),
                "sentinel_sha256": sentinels[label].sha256,
                "sentinel_size_bytes": sentinels[label].size_bytes,
            }
        )
    return {
        "schema_version": profile["protocol"]["intent_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "recorded_at_utc": recorded_at,
        "intent_role": "inode_bound_forward_only_lock_exchange_authorization",
        "prepare": _reference(prepare_snapshot, path=paths.prepare.name),
        "lock_bindings": bindings,
        "cut_order": list(CUT_ORDER),
        **_claims(),
    }


def _validate_intent_binding(
    value: object,
    *,
    label: str,
    canonical: Path,
    archive: Path,
    paths: AdmissionCutPaths,
    sentinel_raw: bytes,
) -> dict[str, Any]:
    binding = _exact(
        value,
        {
            "lock_label",
            "canonical_path",
            "archive_path",
            "old_lock_identity",
            "sentinel_identity",
            "sentinel_sha256",
            "sentinel_size_bytes",
        },
        name=f"{label} intent binding",
    )
    if (
        binding["lock_label"] != label
        or binding["canonical_path"]
        != _relative(canonical, paths.active_root, name=label)
        or binding["archive_path"] != _relative(archive, paths.active_root, name=label)
        or binding["sentinel_sha256"] != _sha256(sentinel_raw)
        or binding["sentinel_size_bytes"] != len(sentinel_raw)
    ):
        raise AdmissionCutIntegrityError(f"{label} intent binding changed")
    old_identity = _validated_identity(
        binding["old_lock_identity"], name=f"old {label} lock identity"
    )
    sentinel_identity = _validated_identity(
        binding["sentinel_identity"], name=f"{label} sentinel identity"
    )
    if old_identity == sentinel_identity or (
        old_identity["device"] != sentinel_identity["device"]
    ):
        raise AdmissionCutIntegrityError(
            f"{label} exchange operands are not distinct same-device files"
        )
    return binding


def _load_intent(
    profile: Mapping[str, Any],
    paths: AdmissionCutPaths,
    prepare_snapshot: registry.ArtifactSnapshot,
) -> tuple[dict[str, Any], registry.ArtifactSnapshot] | None:
    existing = _optional_json(paths.intent, name="admission-cut intent")
    if existing is None:
        return None
    payload, snapshot = existing
    _exact(
        payload,
        {
            "schema_version",
            "profile_id",
            "profile_sha256",
            "recorded_at_utc",
            "intent_role",
            "prepare",
            "lock_bindings",
            "cut_order",
            *TRUE_CAPABILITIES,
            *FALSE_CLAIMS,
            "entry_sha256",
        },
        name="admission-cut intent",
    )
    _validate_entry(payload, name="admission-cut intent")
    try:
        recorded = registry._utc_timestamp(  # noqa: SLF001
            payload["recorded_at_utc"], name="admission-cut intent recorded_at_utc"
        )
    except registry.EpochRegistryError as exc:
        raise AdmissionCutIntegrityError(str(exc)) from exc
    if recorded < _snapshot_time(
        prepare_snapshot,
        field="prepared_at_utc",
        name="admission-cut prepare",
    ):
        raise AdmissionCutIntegrityError("Admission-cut intent predates prepare")
    if (
        payload["schema_version"] != profile["protocol"]["intent_schema_version"]
        or payload["profile_id"] != profile["profile_id"]
        or payload["profile_sha256"] != profile["_profile_sha256"]
        or payload["intent_role"]
        != "inode_bound_forward_only_lock_exchange_authorization"
        or payload["cut_order"] != list(CUT_ORDER)
        or any(payload.get(name) is not True for name in TRUE_CAPABILITIES)
        or any(payload.get(name) is not False for name in FALSE_CLAIMS)
    ):
        raise AdmissionCutIntegrityError("Admission-cut intent semantics changed")
    _validate_reference(
        payload["prepare"],
        prepare_snapshot,
        path=paths.prepare.name,
        name="intent prepare reference",
    )
    raw_by_label = _sentinel_raws(profile, paths, prepare_snapshot)
    bindings = payload["lock_bindings"]
    if not isinstance(bindings, list) or len(bindings) != 2:
        raise AdmissionCutIntegrityError("Admission-cut intent bindings changed")
    for value, (label, canonical, archive) in zip(
        bindings,
        (
            ("deploy", paths.deploy_lock, paths.deploy_archive),
            ("runner", paths.runner_lock, paths.runner_archive),
        ),
        strict=True,
    ):
        _validate_intent_binding(
            value,
            label=label,
            canonical=canonical,
            archive=archive,
            paths=paths,
            sentinel_raw=raw_by_label[label],
        )
    return payload, snapshot


def _publish_intent(
    profile: Mapping[str, Any],
    paths: AdmissionCutPaths,
    prepare_snapshot: registry.ArtifactSnapshot,
    sentinels: Mapping[str, registry.ArtifactSnapshot],
    *,
    recorded_at: str,
) -> tuple[dict[str, Any], registry.ArtifactSnapshot, bool]:
    existing = _load_intent(profile, paths, prepare_snapshot)
    if existing is not None:
        return existing[0], existing[1], False
    try:
        recorded = registry._utc_timestamp(  # noqa: SLF001
            recorded_at, name="new admission-cut intent recorded_at"
        )
    except registry.EpochRegistryError as exc:
        raise AdmissionCutIntegrityError(str(exc)) from exc
    if recorded < _snapshot_time(
        prepare_snapshot,
        field="prepared_at_utc",
        name="admission-cut prepare",
    ):
        raise AdmissionCutIntegrityError(
            "New admission-cut intent would predate prepare"
        )
    payload = _entry_payload(
        _intent_body(
            profile,
            paths,
            prepare_snapshot,
            sentinels,
            recorded_at=recorded_at,
        )
    )
    snapshot = _publish(
        paths.intent, payload, root=paths.root, name="admission-cut intent"
    )
    replayed = _load_intent(profile, paths, prepare_snapshot)
    if replayed is None or replayed[0] != payload:
        raise AdmissionCutIntegrityError("Admission-cut intent did not replay")
    return payload, snapshot, True


def _binding_map(intent: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    bindings = intent.get("lock_bindings")
    if not isinstance(bindings, list):
        raise AdmissionCutIntegrityError("Admission-cut intent bindings are not a list")
    result: dict[str, dict[str, Any]] = {}
    for binding in bindings:
        if not isinstance(binding, dict) or binding.get("lock_label") not in CUT_ORDER:
            raise AdmissionCutIntegrityError("Admission-cut intent lock label changed")
        label = binding["lock_label"]
        if label in result:
            raise AdmissionCutIntegrityError("Admission-cut intent lock label repeats")
        result[label] = binding
    if tuple(result) != CUT_ORDER:
        raise AdmissionCutIntegrityError("Admission-cut intent lock order changed")
    return result


def _physical_state(
    profile: Mapping[str, Any],
    paths: AdmissionCutPaths,
    prepare_snapshot: registry.ArtifactSnapshot,
    intent: Mapping[str, Any],
) -> str:
    """Replay the two inode orientations and verify the sentinel at its live path."""

    raw_by_label = _sentinel_raws(profile, paths, prepare_snapshot)
    orientation: dict[str, str] = {}
    bindings = _binding_map(intent)
    for label, canonical, archive in (
        ("deploy", paths.deploy_lock, paths.deploy_archive),
        ("runner", paths.runner_lock, paths.runner_archive),
    ):
        binding = bindings[label]
        old = _validated_identity(
            binding["old_lock_identity"], name=f"old {label} identity"
        )
        sentinel = _validated_identity(
            binding["sentinel_identity"], name=f"{label} sentinel identity"
        )
        canonical_identity = _identity(canonical, name=f"canonical {label} lock")
        archive_identity = _identity(archive, name=f"archived {label} lock")
        if canonical_identity == old and archive_identity == sentinel:
            orientation[label] = "open"
            sentinel_path = archive
        elif canonical_identity == sentinel and archive_identity == old:
            orientation[label] = "cut"
            sentinel_path = canonical
        else:
            raise AdmissionCutIntegrityError(
                f"{label} lock inode orientation is neither prepared nor exchanged"
            )
        _verify_sentinel(
            sentinel_path,
            raw_by_label[label],
            name=f"{label} admission sentinel",
            expected_identity=sentinel,
        )
    state = (orientation["deploy"], orientation["runner"])
    if state == ("open", "open"):
        return "open"
    if state == ("cut", "open"):
        return "deploy_cut"
    if state == ("cut", "cut"):
        return "both_cut"
    raise AdmissionCutIntegrityError("Runner lock was cut before the deploy lock")


def _strict_json_entries(path: Path, *, name: str) -> tuple[Path, ...]:
    if not path.exists() and not path.is_symlink():
        return ()
    try:
        mode = os.lstat(path).st_mode
        if not stat.S_ISDIR(mode) or stat.S_ISLNK(mode):
            raise AdmissionCutIntegrityError(f"{name} is not a real directory")
        entries = tuple(sorted(path.iterdir(), key=lambda item: item.name))
        for entry in entries:
            entry_mode = os.lstat(entry).st_mode
            if (
                entry.name.startswith(".")
                or not entry.name.endswith(".json")
                or not stat.S_ISREG(entry_mode)
                or stat.S_ISLNK(entry_mode)
            ):
                raise AdmissionCutIntegrityError(f"{name} contains an unknown entry")
        return entries
    except OSError as exc:
        raise AdmissionCutIntegrityError(f"Cannot inspect {name}") from exc


def _attempt_body(
    profile: Mapping[str, Any],
    paths: AdmissionCutPaths,
    prepare_snapshot: registry.ArtifactSnapshot,
    intent_snapshot: registry.ArtifactSnapshot,
    *,
    sequence_id: int,
    previous_entry_sha256: str,
    recorded_at: str,
    context: drain_v2.WorksetContext,
    physical_state_before: str,
) -> dict[str, object]:
    return {
        "schema_version": profile["protocol"]["attempt_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "sequence_id": sequence_id,
        "previous_entry_sha256": previous_entry_sha256,
        "recorded_at_utc": recorded_at,
        "attempt_role": "machine_current_context_before_next_physical_cut",
        "physical_state_before": physical_state_before,
        "prepare": _reference(prepare_snapshot, path=paths.prepare.name),
        "intent": _reference(intent_snapshot, path=paths.intent.name),
        "authority_context": _context_payload(context),
        **_claims(),
    }


def _load_attempts(
    profile: Mapping[str, Any],
    paths: AdmissionCutPaths,
    prepare_snapshot: registry.ArtifactSnapshot,
    intent_snapshot: registry.ArtifactSnapshot,
) -> list[tuple[dict[str, Any], registry.ArtifactSnapshot]]:
    entries = _strict_json_entries(paths.attempts, name="admission-cut attempts")
    if len(entries) > profile["protocol"]["maximum_attempts"]:
        raise AdmissionCutIntegrityError("Admission-cut attempt limit exceeded")
    result: list[tuple[dict[str, Any], registry.ArtifactSnapshot]] = []
    previous_hash = ZERO_HASH
    previous_time = _snapshot_time(
        intent_snapshot,
        field="recorded_at_utc",
        name="admission-cut intent",
    )
    prepare_payload = _snapshot_payload(prepare_snapshot, name="admission-cut prepare")
    original_context = _context_from_payload(
        prepare_payload.get("original_authority_context")
    )
    previous_context = original_context
    deploy_boundary_seen = False
    for sequence_id, path in enumerate(entries, 1):
        payload, snapshot = _read_json(path, name="admission-cut attempt")
        _exact(
            payload,
            {
                "schema_version",
                "profile_id",
                "profile_sha256",
                "sequence_id",
                "previous_entry_sha256",
                "recorded_at_utc",
                "attempt_role",
                "physical_state_before",
                "prepare",
                "intent",
                "authority_context",
                *TRUE_CAPABILITIES,
                *FALSE_CLAIMS,
                "entry_sha256",
            },
            name="admission-cut attempt",
        )
        _validate_entry(payload, name="admission-cut attempt")
        try:
            recorded = registry._utc_timestamp(  # noqa: SLF001
                payload["recorded_at_utc"], name="admission-cut attempt recorded_at"
            )
        except registry.EpochRegistryError as exc:
            raise AdmissionCutIntegrityError(str(exc)) from exc
        if (
            payload["schema_version"] != profile["protocol"]["attempt_schema_version"]
            or payload["profile_id"] != profile["profile_id"]
            or payload["profile_sha256"] != profile["_profile_sha256"]
            or payload["sequence_id"] != sequence_id
            or payload["previous_entry_sha256"] != previous_hash
            or payload["attempt_role"]
            != "machine_current_context_before_next_physical_cut"
            or payload["physical_state_before"] not in {"open", "deploy_cut"}
            or any(payload.get(name) is not True for name in TRUE_CAPABILITIES)
            or any(payload.get(name) is not False for name in FALSE_CLAIMS)
            or recorded < previous_time
        ):
            raise AdmissionCutIntegrityError("Admission-cut attempt chain changed")
        _validate_reference(
            payload["prepare"],
            prepare_snapshot,
            path=paths.prepare.name,
            name="attempt prepare reference",
        )
        _validate_reference(
            payload["intent"],
            intent_snapshot,
            path=paths.intent.name,
            name="attempt intent reference",
        )
        context = _context_from_payload(payload["authority_context"])
        attempt_state = payload["physical_state_before"]
        if sequence_id == 1 and attempt_state != "open":
            raise AdmissionCutIntegrityError(
                "Admission-cut attempt chain does not start before deploy"
            )
        if deploy_boundary_seen and attempt_state == "open":
            raise AdmissionCutIntegrityError(
                "Admission-cut attempt state moved backward after deploy"
            )
        deploy_boundary_seen = deploy_boundary_seen or attempt_state == "deploy_cut"
        _context_successor(original_context, context, name="Admission-cut attempt")
        _context_successor(previous_context, context, name="Admission-cut attempt")
        if path.name != f"{sequence_id:020d}-{payload['entry_sha256']}.json":
            raise AdmissionCutIntegrityError("Admission-cut attempt filename changed")
        result.append((payload, snapshot))
        previous_hash = payload["entry_sha256"]
        previous_time = recorded
        previous_context = context
    return result


def _publish_attempt(
    profile: Mapping[str, Any],
    paths: AdmissionCutPaths,
    prepare_snapshot: registry.ArtifactSnapshot,
    intent_snapshot: registry.ArtifactSnapshot,
    *,
    recorded_at: str,
    context: drain_v2.WorksetContext,
    physical_state_before: str,
) -> tuple[dict[str, Any], registry.ArtifactSnapshot, bool]:
    attempts = _load_attempts(profile, paths, prepare_snapshot, intent_snapshot)
    context_payload = _context_payload(context)
    try:
        current_time = registry._utc_timestamp(  # noqa: SLF001
            recorded_at, name="new attempt recorded_at"
        )
    except registry.EpochRegistryError as exc:
        raise AdmissionCutIntegrityError(str(exc)) from exc
    if current_time < _snapshot_time(
        intent_snapshot,
        field="recorded_at_utc",
        name="admission-cut intent",
    ):
        raise AdmissionCutIntegrityError(
            "New admission-cut attempt would predate intent"
        )
    prepare_payload = _snapshot_payload(prepare_snapshot, name="admission-cut prepare")
    original_context = _context_from_payload(
        prepare_payload.get("original_authority_context")
    )
    _context_successor(original_context, context, name="Current admission-cut context")
    if attempts:
        previous_context = _context_from_payload(attempts[-1][0]["authority_context"])
        _context_successor(
            previous_context, context, name="Current admission-cut context"
        )
    if attempts:
        latest_payload, latest_snapshot = attempts[-1]
        if (
            latest_payload["physical_state_before"] == physical_state_before
            and latest_payload["authority_context"] == context_payload
        ):
            return latest_payload, latest_snapshot, False
        previous_time = registry._utc_timestamp(  # noqa: SLF001
            latest_payload["recorded_at_utc"], name="latest attempt recorded_at"
        )
        if current_time < previous_time:
            raise AdmissionCutIntegrityError(
                "Admission-cut machine clock moved backward"
            )
    if len(attempts) >= profile["protocol"]["maximum_attempts"]:
        raise AdmissionCutIntegrityError("Admission-cut attempt limit reached")
    sequence_id = len(attempts) + 1
    previous_hash = attempts[-1][0]["entry_sha256"] if attempts else ZERO_HASH
    payload = _entry_payload(
        _attempt_body(
            profile,
            paths,
            prepare_snapshot,
            intent_snapshot,
            sequence_id=sequence_id,
            previous_entry_sha256=previous_hash,
            recorded_at=recorded_at,
            context=context,
            physical_state_before=physical_state_before,
        )
    )
    path = paths.attempts / f"{sequence_id:020d}-{payload['entry_sha256']}.json"
    snapshot = _publish(path, payload, root=paths.root, name="admission-cut attempt")
    replayed = _load_attempts(profile, paths, prepare_snapshot, intent_snapshot)
    if replayed[-1][0] != payload:
        raise AdmissionCutIntegrityError("Admission-cut attempt did not replay")
    return payload, snapshot, True


def _event_body(
    profile: Mapping[str, Any],
    paths: AdmissionCutPaths,
    prepare_snapshot: registry.ArtifactSnapshot,
    intent_snapshot: registry.ArtifactSnapshot,
    attempt_snapshot: registry.ArtifactSnapshot,
    *,
    recorded_at: str,
) -> dict[str, object]:
    return {
        "schema_version": profile["protocol"]["event_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "sequence_id": 1,
        "previous_entry_sha256": ZERO_HASH,
        "event_type": profile["protocol"]["event_type"],
        "recorded_at_utc": recorded_at,
        "event_role": "physical_official_writer_lock_path_cut_only",
        "physical_state": "both_cut",
        "prepare": _reference(prepare_snapshot, path=paths.prepare.name),
        "intent": _reference(intent_snapshot, path=paths.intent.name),
        "latest_attempt": _reference(
            attempt_snapshot, path=f"attempts/{attempt_snapshot.path.name}"
        ),
        **_claims(),
    }


def _load_event(
    profile: Mapping[str, Any],
    paths: AdmissionCutPaths,
    prepare_snapshot: registry.ArtifactSnapshot,
    intent_snapshot: registry.ArtifactSnapshot,
    attempts: Sequence[tuple[dict[str, Any], registry.ArtifactSnapshot]],
) -> tuple[dict[str, Any], registry.ArtifactSnapshot] | None:
    entries = _strict_json_entries(paths.events, name="admission-cut events")
    if not entries:
        return None
    if len(entries) != 1 or not attempts:
        raise AdmissionCutIntegrityError("Admission-cut event chain is not singleton")
    payload, snapshot = _read_json(entries[0], name="admission-cut event")
    _exact(
        payload,
        {
            "schema_version",
            "profile_id",
            "profile_sha256",
            "sequence_id",
            "previous_entry_sha256",
            "event_type",
            "recorded_at_utc",
            "event_role",
            "physical_state",
            "prepare",
            "intent",
            "latest_attempt",
            *TRUE_CAPABILITIES,
            *FALSE_CLAIMS,
            "entry_sha256",
        },
        name="admission-cut event",
    )
    _validate_entry(payload, name="admission-cut event")
    try:
        recorded = registry._utc_timestamp(  # noqa: SLF001
            payload["recorded_at_utc"], name="admission-cut event recorded_at"
        )
    except registry.EpochRegistryError as exc:
        raise AdmissionCutIntegrityError(str(exc)) from exc
    if recorded < _snapshot_time(
        attempts[-1][1],
        field="recorded_at_utc",
        name="latest admission-cut attempt",
    ):
        raise AdmissionCutIntegrityError("Admission-cut event predates its attempt")
    if (
        payload["schema_version"] != profile["protocol"]["event_schema_version"]
        or payload["profile_id"] != profile["profile_id"]
        or payload["profile_sha256"] != profile["_profile_sha256"]
        or payload["sequence_id"] != 1
        or payload["previous_entry_sha256"] != ZERO_HASH
        or payload["event_type"] != profile["protocol"]["event_type"]
        or payload["event_role"] != "physical_official_writer_lock_path_cut_only"
        or payload["physical_state"] != "both_cut"
        or any(payload.get(name) is not True for name in TRUE_CAPABILITIES)
        or any(payload.get(name) is not False for name in FALSE_CLAIMS)
        or entries[0].name != f"{1:020d}-{payload['entry_sha256']}.json"
        or attempts[0][0]["physical_state_before"] != "open"
        or attempts[-1][0]["physical_state_before"] != "deploy_cut"
    ):
        raise AdmissionCutIntegrityError("Admission-cut event semantics changed")
    _validate_reference(
        payload["prepare"],
        prepare_snapshot,
        path=paths.prepare.name,
        name="event prepare reference",
    )
    _validate_reference(
        payload["intent"],
        intent_snapshot,
        path=paths.intent.name,
        name="event intent reference",
    )
    latest_snapshot = attempts[-1][1]
    _validate_reference(
        payload["latest_attempt"],
        latest_snapshot,
        path=f"attempts/{latest_snapshot.path.name}",
        name="event latest-attempt reference",
    )
    return payload, snapshot


def _publish_event(
    profile: Mapping[str, Any],
    paths: AdmissionCutPaths,
    prepare_snapshot: registry.ArtifactSnapshot,
    intent_snapshot: registry.ArtifactSnapshot,
    attempts: Sequence[tuple[dict[str, Any], registry.ArtifactSnapshot]],
    *,
    recorded_at: str,
) -> tuple[dict[str, Any], registry.ArtifactSnapshot, bool]:
    existing = _load_event(profile, paths, prepare_snapshot, intent_snapshot, attempts)
    if existing is not None:
        return existing[0], existing[1], False
    if not attempts:
        raise AdmissionCutIntegrityError("Cannot publish cut event without an attempt")
    if (
        attempts[0][0]["physical_state_before"] != "open"
        or attempts[-1][0]["physical_state_before"] != "deploy_cut"
    ):
        raise AdmissionCutIntegrityError(
            "Cannot publish cut event without open-to-deploy attempt boundaries"
        )
    try:
        recorded = registry._utc_timestamp(  # noqa: SLF001
            recorded_at, name="new admission-cut event recorded_at"
        )
    except registry.EpochRegistryError as exc:
        raise AdmissionCutIntegrityError(str(exc)) from exc
    if recorded < _snapshot_time(
        attempts[-1][1],
        field="recorded_at_utc",
        name="latest admission-cut attempt",
    ):
        raise AdmissionCutIntegrityError(
            "New admission-cut event would predate its attempt"
        )
    payload = _entry_payload(
        _event_body(
            profile,
            paths,
            prepare_snapshot,
            intent_snapshot,
            attempts[-1][1],
            recorded_at=recorded_at,
        )
    )
    event_path = paths.events / f"{1:020d}-{payload['entry_sha256']}.json"
    snapshot = _publish(
        event_path, payload, root=paths.root, name="admission-cut event"
    )
    replayed = _load_event(profile, paths, prepare_snapshot, intent_snapshot, attempts)
    if replayed is None or replayed[0] != payload:
        raise AdmissionCutIntegrityError("Admission-cut event did not replay")
    return payload, snapshot, True


def _open_parent(path: Path, *, name: str) -> int:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_DIRECTORY", 0)
    )
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        named = os.lstat(path)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or stat.S_ISLNK(named.st_mode)
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise AdmissionCutIntegrityError(f"{name} is not one stable directory")
        return descriptor
    except AdmissionCutError:
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
        raise AdmissionCutIntegrityError(f"Cannot open {name}") from exc


def _rename_exchange_regular(
    first: Path,
    second: Path,
    *,
    expected_first: Mapping[str, int] | None = None,
    expected_second: Mapping[str, int] | None = None,
) -> None:
    """Use Darwin's atomic regular-file exchange; no fallback is permitted."""

    first_parent = _open_parent(first.parent, name="canonical lock parent")
    second_parent = _open_parent(second.parent, name="lock archive parent")
    try:
        library = ctypes.CDLL(None, use_errno=True)
        function = getattr(library, "renameatx_np", None)
        if function is None:
            raise AdmissionCutIntegrityError(
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
        first_before = os.stat(first.name, dir_fd=first_parent, follow_symlinks=False)
        second_before = os.stat(
            second.name, dir_fd=second_parent, follow_symlinks=False
        )
        if (
            not stat.S_ISREG(first_before.st_mode)
            or not stat.S_ISREG(second_before.st_mode)
            or first_before.st_dev != second_before.st_dev
        ):
            raise AdmissionCutIntegrityError(
                "Atomic exchange operands are not same-device regular files"
            )
        first_identity = {
            "device": first_before.st_dev,
            "inode": first_before.st_ino,
        }
        second_identity = {
            "device": second_before.st_dev,
            "inode": second_before.st_ino,
        }
        if expected_first is not None and first_identity != dict(expected_first):
            raise AdmissionCutIntegrityError(
                "Canonical exchange operand changed from the intent-bound inode"
            )
        if expected_second is not None and second_identity != dict(expected_second):
            raise AdmissionCutIntegrityError(
                "Archive exchange operand changed from the intent-bound sentinel"
            )
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
            raise AdmissionCutIntegrityError(
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
            raise AdmissionCutIntegrityError(
                "Atomic regular-file exchange inode postcondition failed"
            )
        os.fsync(first_parent)
        if second_parent != first_parent:
            os.fsync(second_parent)
    except AdmissionCutError:
        raise
    except OSError as exc:
        raise AdmissionCutIntegrityError(
            "Cannot execute atomic regular-file exchange"
        ) from exc
    finally:
        os.close(second_parent)
        os.close(first_parent)


def _machine_context(
    binding: _V2Binding, machine_now: datetime
) -> drain_v2.WorksetContext:
    """Read current authority/ledger context without opening a cut writer lock."""

    from monitoring import ootang_prequential_live as live

    try:
        v1_profile = drain.load_drain_profile()
        v1_paths = drain.drain_paths(
            v1_profile,
            runtime_root=binding.paths.registry_root,
            active_runtime_root=binding.paths.active_root,
            shadow_runtime_root=binding.paths.shadow_root,
        )
        authority = drain._load_authority(v1_profile, v1_paths)  # noqa: SLF001
        if isinstance(authority, drain._Waiting):  # noqa: SLF001
            raise AdmissionCutIntegrityError(authority.reason)
        live_profile = live.load_config()
        live_paths = live.runtime_paths(
            live_profile, runtime_root=binding.paths.active_root
        )
        prerequisites = live.load_prerequisites(live_profile, live_paths)
        if prerequisites is None:
            raise AdmissionCutIntegrityError(
                "Current old runtime lacks machine-verifiable prerequisites"
            )
        projection = live.load_verified_ledger_projection(
            live_profile, live_paths, prerequisites
        )
    except AdmissionCutError:
        raise
    except (
        drain.EpochDrainError,
        live.LiveConfigError,
        live.LivePrerequisiteError,
        live.LiveInputError,
        live.LiveIntegrityError,
    ) as exc:
        raise AdmissionCutIntegrityError(str(exc)) from exc
    if machine_now.tzinfo is None or machine_now.utcoffset() is None:
        raise AdmissionCutIntegrityError("Admission-cut clock must be timezone-aware")
    return drain_v2.WorksetContext(
        registry_event_sequence_id=authority.r1_event["sequence_id"],
        registry_event_entry_sha256=authority.r1_event["entry_sha256"],
        preparation_event_sequence_id=authority.preparation_event["sequence_id"],
        preparation_event_entry_sha256=authority.preparation_event["entry_sha256"],
        candidate_id=authority.r1_event["candidate_id"],
        slot_id=authority.r1_event["slot_id"],
        old_live_epoch_id=projection.epoch_id,
        live_event_count=projection.ledger_event_count,
        live_terminal_sha256=projection.ledger_terminal_sha256,
    )


def _inspect_context(
    paths: AdmissionCutPaths,
    binding: _V2Binding,
    machine_now: datetime,
    *,
    physical_state: str,
    inspect_context: InspectContext | None,
) -> tuple[drain_v2.WorksetContext | None, str, str]:
    if inspect_context is not None:
        context = inspect_context(paths, machine_now)
        _context_payload(context)
        return context, "machine_context_current", "injected exact current context"
    try:
        inspection = drain_v2._default_inspection(  # noqa: SLF001
            binding.paths, machine_now
        )
    except drain_v2.DrainV2Error as exc:
        raise AdmissionCutIntegrityError(str(exc)) from exc
    if inspection.status == "dirty_workset_observed":
        if inspection.context is None:
            raise AdmissionCutIntegrityError("Dirty inspection lost its context")
        _context_payload(inspection.context)
        return inspection.context, inspection.status, inspection.reason
    if physical_state == "open":
        return None, inspection.status, inspection.reason
    context = _machine_context(binding, machine_now)
    _context_payload(context)
    return (
        context,
        "machine_context_current_after_partial_cut",
        "physical cut already started; current old-ledger context was read directly",
    )


def _acquire_for_state(paths: AdmissionCutPaths, physical_state: str) -> list[BinaryIO]:
    if physical_state == "open":
        ordered = (
            ("manager", paths.manager_lock),
            ("cycle", paths.cycle_lock),
            ("deploy", paths.deploy_lock),
            ("runner", paths.runner_lock),
            ("replay", paths.replay_lock),
            ("shadow", paths.shadow_lock),
        )
    elif physical_state == "deploy_cut":
        ordered = (
            ("manager", paths.manager_lock),
            ("cycle", paths.cycle_lock),
            ("runner", paths.runner_lock),
            ("replay", paths.replay_lock),
            ("shadow", paths.shadow_lock),
        )
    elif physical_state == "both_cut":
        ordered = (
            ("manager", paths.manager_lock),
            ("cycle", paths.cycle_lock),
        )
    else:
        raise AdmissionCutIntegrityError("Unknown admission-cut physical state")
    handles: list[BinaryIO] = []
    try:
        for label, path in ordered:
            try:
                handles.append(drain._acquire_lock(path, label=label))  # noqa: SLF001
            except drain.EpochDrainBusyError as exc:
                raise AdmissionCutBusyError(str(exc)) from exc
            except drain.EpochDrainError as exc:
                raise AdmissionCutIntegrityError(str(exc)) from exc
        return handles
    except AdmissionCutError:
        try:
            drain._release_locks(handles)  # noqa: SLF001
        except drain.EpochDrainError:
            pass
        raise


def _prelock_state(
    profile: Mapping[str, Any], paths: AdmissionCutPaths
) -> tuple[str, _V2Binding | None]:
    binding = _load_v2_binding(paths)
    any_prepare = paths.prepare.exists() or paths.prepare.is_symlink()
    any_intent = paths.intent.exists() or paths.intent.is_symlink()
    if binding is None:
        if any_prepare or any_intent:
            raise AdmissionCutIntegrityError(
                "Admission-cut records outlived their exact v2 observation"
            )
        return "open", None
    prepare = _load_prepare(profile, paths, binding)
    if prepare is None:
        if any_intent:
            raise AdmissionCutIntegrityError("Admission-cut intent lacks prepare")
        return "open", binding
    intent = _load_intent(profile, paths, prepare[1])
    if intent is None:
        return "open", binding
    return _physical_state(profile, paths, prepare[1], intent[0]), binding


def _result(
    paths: AdmissionCutPaths,
    *,
    status: str,
    reason: str,
    event_path: Path | None,
    prepare_path: Path | None,
    intent_path: Path | None,
    attempt_path: Path | None,
    physical_state: str,
) -> AdmissionCutResult:
    return AdmissionCutResult(
        status=status,
        reason=reason,
        status_path=paths.status,
        event_path=event_path,
        prepare_path=prepare_path,
        intent_path=intent_path,
        attempt_path=attempt_path,
        deploy_cut=physical_state in {"deploy_cut", "both_cut"},
        runner_cut=physical_state == "both_cut",
    )


def _coordinate_epoch_admission_cut(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    runtime_root: Path | None = None,
    active_runtime_root: Path | None = None,
    shadow_runtime_root: Path | None = None,
    clock: Callable[[], datetime] | None = None,
    inspect_context: InspectContext | None = None,
    exchange: Exchange | None = None,
    fault_hook: FaultHook | None = None,
) -> AdmissionCutResult:
    """Cut deploy then runner, resuming only forward after a machine crash."""

    profile = load_admission_cut_profile(config_path)
    paths = admission_cut_paths(
        profile,
        runtime_root=runtime_root,
        active_runtime_root=active_runtime_root,
        shadow_runtime_root=shadow_runtime_root,
    )
    machine_now = (clock or (lambda: datetime.now(timezone.utc)))()
    checked_at = _utc_text(machine_now)
    prelock_state, _ = _prelock_state(profile, paths)
    handles: list[BinaryIO] = []
    locks_acquired = False
    event_payload: dict[str, Any] | None = None
    physical_state = prelock_state
    prepare_snapshot: registry.ArtifactSnapshot | None = None
    intent_snapshot: registry.ArtifactSnapshot | None = None
    try:
        handles = _acquire_for_state(paths, prelock_state)
        locks_acquired = True
        binding = _load_v2_binding(paths)
        if binding is None:
            reason = "no exact v2 first-blocker observation is available"
            _write_status(
                profile,
                paths,
                checked_at=checked_at,
                status="waiting_for_first_blocker_observation",
                reason=reason,
                event=None,
                physical_state="open",
            )
            return _result(
                paths,
                status="waiting_for_first_blocker_observation",
                reason=reason,
                event_path=None,
                prepare_path=None,
                intent_path=None,
                attempt_path=None,
                physical_state="open",
            )
        prepare_record = _load_prepare(profile, paths, binding)
        intent_record = (
            _load_intent(profile, paths, prepare_record[1])
            if prepare_record is not None
            else None
        )
        physical_state = (
            _physical_state(profile, paths, prepare_record[1], intent_record[0])
            if prepare_record is not None and intent_record is not None
            else "open"
        )
        if physical_state != prelock_state:
            raise AdmissionCutIntegrityError(
                "Admission-cut physical state changed during lock acquisition"
            )
        if physical_state == "open":
            try:
                witnesses = drain_v2._v1_authority_witnesses(  # noqa: SLF001
                    binding.paths
                )
            except drain_v2.DrainV2Error as exc:
                raise AdmissionCutIntegrityError(str(exc)) from exc
            if witnesses:
                reason = "v1 authority has precedence before the first cut:" + ",".join(
                    witnesses
                )
                _write_status(
                    profile,
                    paths,
                    checked_at=checked_at,
                    status="not_applicable_v1_authority_present",
                    reason=reason,
                    event=None,
                    physical_state="open",
                )
                return _result(
                    paths,
                    status="not_applicable_v1_authority_present",
                    reason=reason,
                    event_path=None,
                    prepare_path=(prepare_record[1].path if prepare_record else None),
                    intent_path=(intent_record[1].path if intent_record else None),
                    attempt_path=None,
                    physical_state="open",
                )
        current_context: drain_v2.WorksetContext | None = None
        if physical_state != "both_cut":
            current_context, context_status, context_reason = _inspect_context(
                paths,
                binding,
                machine_now,
                physical_state=physical_state,
                inspect_context=inspect_context,
            )
            if current_context is None:
                _write_status(
                    profile,
                    paths,
                    checked_at=checked_at,
                    status=context_status,
                    reason=context_reason,
                    event=None,
                    physical_state="open",
                )
                return _result(
                    paths,
                    status=context_status,
                    reason=context_reason,
                    event_path=None,
                    prepare_path=(prepare_record[1].path if prepare_record else None),
                    intent_path=(intent_record[1].path if intent_record else None),
                    attempt_path=None,
                    physical_state="open",
                )
        if prepare_record is None:
            _, prepare_snapshot, created = _publish_prepare(
                profile, paths, binding, prepared_at=checked_at
            )
            if created:
                _call_fault_hook(fault_hook, "after_prepare")
        else:
            prepare_snapshot = prepare_record[1]
        raw_by_label = _sentinel_raws(profile, paths, prepare_snapshot)
        if intent_record is None:
            sentinels = {
                "deploy": _ensure_sentinel(
                    paths.deploy_archive,
                    registry._decode_json(  # noqa: SLF001
                        raw_by_label["deploy"], name="deploy sentinel"
                    ),
                    paths=paths,
                    name="deploy admission sentinel",
                ),
                "runner": _ensure_sentinel(
                    paths.runner_archive,
                    registry._decode_json(  # noqa: SLF001
                        raw_by_label["runner"], name="runner sentinel"
                    ),
                    paths=paths,
                    name="runner admission sentinel",
                ),
            }
            _call_fault_hook(fault_hook, "after_sentinels")
            _, intent_snapshot, created = _publish_intent(
                profile,
                paths,
                prepare_snapshot,
                sentinels,
                recorded_at=checked_at,
            )
            if created:
                _call_fault_hook(fault_hook, "after_intent")
            loaded_intent = _load_intent(profile, paths, prepare_snapshot)
            if loaded_intent is None:
                raise AdmissionCutIntegrityError("Admission-cut intent disappeared")
            intent_payload, intent_snapshot = loaded_intent
        else:
            intent_payload, intent_snapshot = intent_record
        physical_state = _physical_state(
            profile, paths, prepare_snapshot, intent_payload
        )
        attempts = _load_attempts(profile, paths, prepare_snapshot, intent_snapshot)
        if physical_state != "both_cut":
            if current_context is None:
                raise AdmissionCutIntegrityError(
                    "Physical cut lacks a machine-current authority context"
                )
            _, _, created = _publish_attempt(
                profile,
                paths,
                prepare_snapshot,
                intent_snapshot,
                recorded_at=checked_at,
                context=current_context,
                physical_state_before=physical_state,
            )
            if created:
                _call_fault_hook(fault_hook, "after_attempt")
            attempts = _load_attempts(profile, paths, prepare_snapshot, intent_snapshot)
        elif not attempts:
            raise AdmissionCutIntegrityError(
                "Both lock paths were cut without a durable attempt"
            )
        exchange_lock = exchange or _rename_exchange_regular
        if physical_state == "open":
            deploy_binding = _binding_map(intent_payload)["deploy"]
            if exchange is None:
                _rename_exchange_regular(
                    paths.deploy_lock,
                    paths.deploy_archive,
                    expected_first=deploy_binding["old_lock_identity"],
                    expected_second=deploy_binding["sentinel_identity"],
                )
            else:
                exchange_lock(paths.deploy_lock, paths.deploy_archive)
            physical_state = _physical_state(
                profile, paths, prepare_snapshot, intent_payload
            )
            if physical_state != "deploy_cut":
                raise AdmissionCutIntegrityError("Deploy cut did not advance exactly")
            _call_fault_hook(fault_hook, "after_deploy_swap")
        if physical_state == "deploy_cut":
            if current_context is None:
                raise AdmissionCutIntegrityError(
                    "Runner cut lacks a machine-current authority context"
                )
            # A normal same-poll path still records the second physical
            # boundary.  The runner lock is already held, so the context
            # captured before deploy remains current; a deploy-only crash
            # instead reaches this point with a freshly inspected context.
            _publish_attempt(
                profile,
                paths,
                prepare_snapshot,
                intent_snapshot,
                recorded_at=checked_at,
                context=current_context,
                physical_state_before="deploy_cut",
            )
            runner_binding = _binding_map(intent_payload)["runner"]
            if exchange is None:
                _rename_exchange_regular(
                    paths.runner_lock,
                    paths.runner_archive,
                    expected_first=runner_binding["old_lock_identity"],
                    expected_second=runner_binding["sentinel_identity"],
                )
            else:
                exchange_lock(paths.runner_lock, paths.runner_archive)
            physical_state = _physical_state(
                profile, paths, prepare_snapshot, intent_payload
            )
            if physical_state != "both_cut":
                raise AdmissionCutIntegrityError("Runner cut did not advance exactly")
            _call_fault_hook(fault_hook, "after_runner_swap")
        attempts = _load_attempts(profile, paths, prepare_snapshot, intent_snapshot)
        event_payload, event_snapshot, created = _publish_event(
            profile,
            paths,
            prepare_snapshot,
            intent_snapshot,
            attempts,
            recorded_at=checked_at,
        )
        if created:
            _call_fault_hook(fault_hook, "after_event")
        status = "official_writer_lock_paths_cut" if created else "cut_idempotent"
        reason = (
            "deploy and runner canonical lock paths deny write-open; no closed "
            "workset, recovery, lifecycle, or transition authority is claimed"
        )
        _write_status(
            profile,
            paths,
            checked_at=checked_at,
            status=status,
            reason=reason,
            event=event_payload,
            physical_state="both_cut",
        )
        return _result(
            paths,
            status=status,
            reason=reason,
            event_path=event_snapshot.path,
            prepare_path=prepare_snapshot.path,
            intent_path=intent_snapshot.path,
            attempt_path=attempts[-1][1].path,
            physical_state="both_cut",
        )
    except (AdmissionCutBusyError, KeyboardInterrupt, SystemExit):
        raise
    except AdmissionCutError as exc:
        if locks_acquired:
            try:
                _write_status(
                    profile,
                    paths,
                    checked_at=checked_at,
                    status="blocked_integrity",
                    reason=f"{type(exc).__name__}:{exc}",
                    event=event_payload,
                    physical_state=physical_state,
                )
            except AdmissionCutError:
                pass
        raise
    finally:
        try:
            drain._release_locks(handles)  # noqa: SLF001
        except drain.EpochDrainError as exc:
            if sys.exc_info()[0] is None:
                raise AdmissionCutIntegrityError(str(exc)) from exc


def coordinate_epoch_admission_cut(
    *, config_path: Path = DEFAULT_CONFIG_PATH
) -> AdmissionCutResult:
    """Run the reviewed machine-only writer-lock admission cut."""

    return _coordinate_epoch_admission_cut(config_path=config_path)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = coordinate_epoch_admission_cut(config_path=args.config)
    except AdmissionCutBusyError as exc:
        print(json.dumps({"status": "busy", "reason": str(exc)}, sort_keys=True))
        return 3
    except AdmissionCutError as exc:
        print(
            json.dumps(
                {
                    "status": "blocked_integrity",
                    "reason": f"{type(exc).__name__}:{exc}",
                },
                sort_keys=True,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "status": result.status,
                "reason": result.reason,
                "status_path": str(result.status_path),
                "event_path": str(result.event_path) if result.event_path else None,
                "deploy_cut": result.deploy_cut,
                "runner_cut": result.runner_cut,
                "lifecycle_authority": result.lifecycle_authority,
                "transition_authority": result.transition_authority,
                "old_work_admission_fence_implemented": (
                    result.old_work_admission_fence_implemented
                ),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
