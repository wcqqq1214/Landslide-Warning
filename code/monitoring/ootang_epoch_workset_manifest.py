"""Reserve the complete frozen-observation old-epoch workset after the cut.

This explicit engineering stage only publishes a content-addressed manifest and
one create-only reservation event with transition seeds for the work visible at
that observation.  It does not enumerate a terminal transition closure or
reserve work derived by future transitions.  It never runs an old writer,
creates an outcome or RFC 3161 nonce, performs recovery, or advances lifecycle
state.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
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

from monitoring import ootang_epoch_admission_cut as admission_cut  # noqa: E402
from monitoring import ootang_epoch_drain as drain  # noqa: E402
from monitoring import ootang_epoch_drain_v2 as drain_v2  # noqa: E402
from monitoring import ootang_epoch_registry as registry  # noqa: E402


DEFAULT_CONFIG_PATH = ROOT / "config" / "ootang_epoch_workset_manifest.v1.json"
DEFAULT_CONFIG_SHA256 = (
    "338b8e4c90bf1bf241a255148c4352b3a9fa197658dd08d1dd745ada604dd39e"
)
MAX_CONTROL_BYTES = 4 * 1024 * 1024
ZERO_HASH = "0" * 64
FAMILIES = (
    "issue_route_replay",
    "live_outstanding",
    "outcome_revision",
    "guard",
    "trusted_time",
    "shadow",
)
LOCK_ORDER = ("manager", "cycle", "replay", "shadow")
ALLOWED_SUCCESSORS = {
    "issue_route_replay": (
        "issue_replay_receipt_verified",
        "issue_route_replay_consumed",
    ),
    "live_outstanding": (
        "anchor_receipt_repaired",
        "anchor_request_recorded",
        "anchor_result_recorded",
        "outcome_batch_settled",
    ),
    "outcome_revision": (
        "source_snapshot_ingested",
        "outcome_materialized",
        "outcome_or_revision_consumed",
    ),
    "guard": ("guard_completion_recorded", "superseded_by_backfill"),
    "trusted_time": (
        "trusted_time_request_der_repaired",
        "trusted_time_response_link_recorded",
        "trusted_time_receipt_verified",
    ),
    "shadow": (
        "shadow_outstanding_settled",
        "shadow_live_event_classified",
        "shadow_cursor_at_frozen_live_upper_tip",
    ),
}
TRUE_CAPABILITIES = (
    "machine_only",
    "admission_cut_binding_verified",
    "content_addressed_manifest_implemented",
    "complete_workset_enumeration",
    "bounded_workset_reservation_implemented",
)
FALSE_CLAIMS = (
    "terminal_transition_closure_enumerated",
    "derived_future_work_reservation_implemented",
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
    "activation_candidate_selected",
    "active_epoch_switch_implemented",
    "automatic_epoch_rotation_implemented",
    "trusted_anchor_receipt_verified",
    "e2_live_evidence_eligible",
    "real_activation_ready",
    "formal_warning_output",
)
EXPECTED_UPSTREAM = {
    "admission_cut_profile": {
        "path": "config/ootang_epoch_admission_cut.v1.json",
        "expected_sha256": admission_cut.DEFAULT_CONFIG_SHA256,
    },
    "admission_cut_implementation": {
        "path": "code/monitoring/ootang_epoch_admission_cut.py",
        "expected_sha256": (
            "95b675b132c5051cbbc4d34041b9686d122a64c6368f04c0bff8d6dddf1effcf"
        ),
    },
    "inventory_implementation": {
        "path": "code/monitoring/ootang_epoch_workset_inventory.py",
        "expected_sha256": (
            "64e7feaf295689e444803fd20eda2d3a754b2ec75932e85987fcba603281bb01"
        ),
    },
}
EXPECTED_RUNTIME = {
    "root": "runtime/ootang_epoch_registry_v1",
    "namespace": "workset_manifest_v1",
    "manager_lock": "manager.lock",
    "manifests": "manifests/sha256",
    "events": "events",
    "status": "status.json",
    "active_root": "runtime/ootang_prequential_live_v1",
    "shadow_root": "runtime/ootang_prequential_calibration_shadow_v1",
    "cycle_lock": "prequential_cycle.lock",
    "replay_lock": "issue_replay.lock",
    "shadow_lock": "runner.lock",
}
EXPECTED_PROTOCOL = {
    "manifest_schema_version": "ootang_epoch_frozen_observation_workset_manifest_v2",
    "event_schema_version": "ootang_epoch_frozen_observation_workset_reserved_event_v2",
    "event_type": "epoch_frozen_observation_workset_reserved",
    "maximum_items": 4096,
    "maximum_artifacts": 16384,
    "maximum_manifest_bytes": MAX_CONTROL_BYTES,
    "maximum_artifact_bytes": 64 * 1024 * 1024,
    "initial_previous_entry_sha256": ZERO_HASH,
    "canonical_json": "utf8_sort_keys_compact_no_nan_trailing_lf",
    "surviving_lock_order": list(LOCK_ORDER),
    "workset_families": list(FAMILIES),
    "allowed_successor_states": {
        family: list(states) for family, states in ALLOWED_SUCCESSORS.items()
    },
    "manifest_policy": (
        "complete_frozen_observation_six_family_workset_with_transition_seeds_no_truncation"
    ),
    "reservation_policy": ("singleton_event_exactly_reserves_manifest_natural_keys"),
    "cut_policy": (
        "exact_admission_cut_event_attempt_context_and_physical_boundary_required"
    ),
    "recovery_action": "not_implemented_manifest_only",
}


class WorksetManifestError(RuntimeError):
    """Base failure for frozen-observation workset reservation."""


class WorksetManifestConfigError(WorksetManifestError):
    """The reviewed profile or a frozen upstream byte changed."""


class WorksetManifestIntegrityError(WorksetManifestError):
    """A cut, namespace, dependency, or artifact failed closed."""


class WorksetManifestBusyError(WorksetManifestError):
    """A surviving globally ordered lock is busy."""


@dataclass(frozen=True)
class WorksetManifestPaths:
    registry_root: Path
    root: Path
    manager_lock: Path
    manifests: Path
    events: Path
    status: Path
    active_root: Path
    shadow_root: Path
    cycle_lock: Path
    replay_lock: Path
    shadow_lock: Path


@dataclass(frozen=True)
class ArtifactObligation:
    role: str
    root: str
    path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class WorksetItem:
    family: str
    natural_key: str
    canonical_successor_state: str
    dependency_keys: tuple[str, ...]
    artifacts: tuple[ArtifactObligation, ...]
    authority: Mapping[str, object]
    namespace_digest: str


@dataclass(frozen=True)
class FamilyInventory:
    family: str
    record_count: int
    actionable_count: int
    artifacts: tuple[ArtifactObligation, ...]
    authority: Mapping[str, object]
    namespace_digest: str


@dataclass(frozen=True)
class WorksetInspection:
    context: drain_v2.WorksetContext
    items: tuple[WorksetItem, ...]
    families: tuple[FamilyInventory, ...]


@dataclass(frozen=True)
class AdmissionCutBinding:
    context: drain_v2.WorksetContext
    event: ArtifactObligation
    latest_attempt: ArtifactObligation
    recorded_at_utc: str


@dataclass(frozen=True)
class WorksetManifestResult:
    status: str
    reason: str
    status_path: Path
    event_path: Path | None
    manifest_path: Path | None
    complete_workset_enumeration: bool = False
    bounded_workset_reservation_implemented: bool = False
    bounded_workset_recovery_implemented: bool = False
    lifecycle_authority: bool = False
    transition_authority: bool = False


InspectWorkset = Callable[
    [WorksetManifestPaths, AdmissionCutBinding, datetime], WorksetInspection
]
InspectAdmissionCut = Callable[[WorksetManifestPaths], AdmissionCutBinding | None]


def _canonical_bytes(value: object) -> bytes:
    try:
        return registry._canonical_bytes(value)  # type: ignore[arg-type]  # noqa: SLF001
    except (TypeError, ValueError) as exc:
        raise WorksetManifestIntegrityError("Value is not canonical JSON") from exc


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _exact(value: object, keys: set[str], *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise WorksetManifestIntegrityError(f"{name} keys changed")
    return value


def _text(value: object, *, name: str, maximum: int = 1024) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value.encode("utf-8")) > maximum
        or "\x00" in value
    ):
        raise WorksetManifestIntegrityError(f"{name} is not canonical text")
    return value


def _hash(value: object, *, name: str) -> str:
    text = _text(value, name=name, maximum=64)
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise WorksetManifestIntegrityError(f"{name} is not lowercase SHA-256")
    return text


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise WorksetManifestIntegrityError("Manifest clock must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _claims() -> dict[str, bool]:
    return {
        **{name: True for name in TRUE_CAPABILITIES},
        **{name: False for name in FALSE_CLAIMS},
    }


def load_workset_manifest_profile(
    path: Path = DEFAULT_CONFIG_PATH, *, project_root: Path = ROOT
) -> dict[str, Any]:
    """Load the only reviewed profile and verify its admission-cut bindings."""

    root = project_root.resolve()
    resolved = path if path.is_absolute() else root / path
    try:
        resolved = registry._absolute_lexical(resolved)  # noqa: SLF001
        if root != ROOT.resolve() or resolved != DEFAULT_CONFIG_PATH.resolve():
            raise WorksetManifestConfigError(
                "Only the reviewed default workset-manifest profile is accepted"
            )
        snapshot = registry._read_regular(  # noqa: SLF001
            resolved, name="workset-manifest profile", maximum_bytes=MAX_CONTROL_BYTES
        )
        if snapshot.sha256 != DEFAULT_CONFIG_SHA256:
            raise WorksetManifestConfigError(
                "Reviewed workset-manifest profile digest changed"
            )
        profile = registry._decode_json(  # noqa: SLF001
            snapshot.raw, name="workset-manifest profile"
        )
    except registry.EpochRegistryError as exc:
        raise WorksetManifestConfigError(str(exc)) from exc
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
        name="workset-manifest profile",
    )
    identity = (
        profile["schema_version"],
        profile["profile_id"],
        profile["profile_version"],
        profile["case"],
        profile["artifact_status"],
    )
    expected_identity = (
        "ootang_epoch_workset_manifest_profile_v1",
        "ootang-epoch-workset-manifest-v1",
        "1.2.0-outcome-settlement-dependency",
        "ootang",
        "bounded_frozen_observation_workset_and_transition_seed_reservation_only_no_terminal_closure_recovery_lifecycle_or_transition_authority",
    )
    if identity != expected_identity:
        raise WorksetManifestConfigError("Workset-manifest identity changed")
    for flag in (
        "formal_warning_output",
        "independent_label_used",
        "confirmatory_external_validation",
        "vajont_used",
        "default_pipeline_member",
    ):
        if profile[flag] is not False:
            raise WorksetManifestConfigError(f"{flag} must remain false")
    if (
        profile["upstream"] != EXPECTED_UPSTREAM
        or profile["runtime"] != EXPECTED_RUNTIME
        or profile["protocol"] != EXPECTED_PROTOCOL
        or profile["engineering_capabilities"] != _claims()
    ):
        raise WorksetManifestConfigError("Reviewed workset-manifest contract changed")
    try:
        for binding in EXPECTED_UPSTREAM.values():
            bound = registry._contained(  # noqa: SLF001
                root, binding["path"], name="workset-manifest upstream"
            )
            captured = registry._read_regular(  # noqa: SLF001
                bound, name="workset-manifest upstream"
            )
            if captured.sha256 != binding["expected_sha256"]:
                raise WorksetManifestConfigError(
                    f"Frozen upstream changed:{binding['path']}"
                )
    except registry.EpochRegistryError as exc:
        raise WorksetManifestConfigError(str(exc)) from exc
    profile["_project_root"] = str(root)
    profile["_profile_sha256"] = snapshot.sha256
    return profile


def workset_manifest_paths(
    profile: Mapping[str, Any],
    *,
    runtime_root: Path | None = None,
    active_runtime_root: Path | None = None,
    shadow_runtime_root: Path | None = None,
) -> WorksetManifestPaths:
    """Resolve the private reservation namespace and surviving locks."""

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
            raise WorksetManifestConfigError(str(exc)) from exc

    root = child(registry_root, profile["runtime"]["namespace"], name="manifest root")
    paths = WorksetManifestPaths(
        registry_root=registry_root,
        root=root,
        manager_lock=child(
            registry_root, profile["runtime"]["manager_lock"], name="manager lock"
        ),
        manifests=child(root, profile["runtime"]["manifests"], name="manifests"),
        events=child(root, profile["runtime"]["events"], name="events"),
        status=child(root, profile["runtime"]["status"], name="status"),
        active_root=active_root,
        shadow_root=shadow_root,
        cycle_lock=child(
            active_root, profile["runtime"]["cycle_lock"], name="cycle lock"
        ),
        replay_lock=child(
            active_root, profile["runtime"]["replay_lock"], name="replay lock"
        ),
        shadow_lock=child(
            shadow_root, profile["runtime"]["shadow_lock"], name="shadow lock"
        ),
    )
    if len(
        {
            paths.manager_lock,
            paths.cycle_lock,
            paths.replay_lock,
            paths.shadow_lock,
        }
    ) != len(LOCK_ORDER):
        raise WorksetManifestConfigError("Surviving manifest locks collide")
    return paths


def _root_path(paths: WorksetManifestPaths, label: str) -> Path:
    roots = {
        "registry": paths.registry_root,
        "active": paths.active_root,
        "shadow": paths.shadow_root,
    }
    try:
        return roots[label]
    except KeyError as exc:
        raise WorksetManifestIntegrityError("Artifact root label is unknown") from exc


def _artifact(
    path: Path,
    *,
    role: str,
    root_label: str,
    paths: WorksetManifestPaths,
    maximum_bytes: int,
) -> ArtifactObligation:
    root = _root_path(paths, root_label)
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            path, name=role, maximum_bytes=maximum_bytes
        )
        relative = snapshot.path.relative_to(root).as_posix()
    except (ValueError, registry.EpochRegistryError) as exc:
        raise WorksetManifestIntegrityError(
            f"Artifact escaped or changed:{role}"
        ) from exc
    return ArtifactObligation(
        _text(role, name="artifact role"),
        root_label,
        relative,
        snapshot.sha256,
        snapshot.size_bytes,
    )


def _artifact_from_snapshot(
    snapshot: registry.ArtifactSnapshot,
    *,
    role: str,
    root_label: str,
    paths: WorksetManifestPaths,
) -> ArtifactObligation:
    root = _root_path(paths, root_label)
    try:
        relative = snapshot.path.relative_to(root).as_posix()
    except ValueError as exc:
        raise WorksetManifestIntegrityError("Cut artifact escaped its root") from exc
    return ArtifactObligation(
        role, root_label, relative, snapshot.sha256, snapshot.size_bytes
    )


def _artifact_payload(value: ArtifactObligation) -> dict[str, object]:
    return {
        "role": value.role,
        "root": value.root,
        "path": value.path,
        "sha256": value.sha256,
        "size_bytes": value.size_bytes,
    }


def _artifact_from_payload(value: object) -> ArtifactObligation:
    payload = _exact(
        value,
        {"role", "root", "path", "sha256", "size_bytes"},
        name="artifact obligation",
    )
    return ArtifactObligation(**payload)


def _validate_artifact(
    value: ArtifactObligation,
    *,
    paths: WorksetManifestPaths,
    maximum_bytes: int,
) -> None:
    path = _validate_artifact_metadata(value, paths=paths, maximum_bytes=maximum_bytes)
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            path, name=value.role, maximum_bytes=maximum_bytes
        )
    except registry.EpochRegistryError as exc:
        raise WorksetManifestIntegrityError(str(exc)) from exc
    if snapshot.sha256 != value.sha256 or snapshot.size_bytes != value.size_bytes:
        raise WorksetManifestIntegrityError(f"Artifact reference changed:{value.role}")


def _validate_artifact_metadata(
    value: ArtifactObligation,
    *,
    paths: WorksetManifestPaths,
    maximum_bytes: int,
) -> Path:
    """Validate a historical obligation without requiring predecessor bytes."""

    if not isinstance(value, ArtifactObligation):
        raise WorksetManifestIntegrityError("Artifact obligation type changed")
    _text(value.role, name="artifact role")
    _hash(value.sha256, name="artifact SHA-256")
    if type(value.size_bytes) is not int or not 0 <= value.size_bytes <= maximum_bytes:
        raise WorksetManifestIntegrityError("Artifact size is outside the bound")
    root = _root_path(paths, value.root)
    try:
        path = registry._contained(root, value.path, name="artifact path")  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise WorksetManifestIntegrityError(str(exc)) from exc
    return path


def _authority(value: Mapping[str, object], *, name: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise WorksetManifestIntegrityError(f"{name} is not a mapping")
    try:
        decoded = json.loads(_canonical_bytes(dict(value)))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise WorksetManifestIntegrityError(f"{name} is not canonical JSON") from exc
    if not isinstance(decoded, dict):
        raise WorksetManifestIntegrityError(f"{name} is not an object")
    return decoded


def _item_namespace_digest(
    *,
    family: str,
    natural_key: str,
    successor: str,
    dependencies: Sequence[str],
    artifacts: Sequence[ArtifactObligation],
    authority: Mapping[str, object],
) -> str:
    return _sha256(
        _canonical_bytes(
            {
                "family": family,
                "natural_key": natural_key,
                "canonical_successor_state": successor,
                "dependency_keys": list(dependencies),
                "artifacts": [_artifact_payload(value) for value in artifacts],
                "authority": _authority(authority, name="item authority"),
            }
        )
    )


def _family_namespace_digest(
    *,
    family: str,
    record_count: int,
    actionable_count: int,
    artifacts: Sequence[ArtifactObligation],
    authority: Mapping[str, object],
) -> str:
    return _sha256(
        _canonical_bytes(
            {
                "family": family,
                "record_count": record_count,
                "actionable_count": actionable_count,
                "artifacts": [_artifact_payload(value) for value in artifacts],
                "authority": _authority(authority, name="family authority"),
            }
        )
    )


def _make_item(
    family: str,
    natural_key: str,
    artifacts: Sequence[ArtifactObligation],
    *,
    dependency_keys: Sequence[str] = (),
    successor_state: str | None = None,
    authority: Mapping[str, object] | None = None,
) -> WorksetItem:
    ordered_artifacts = tuple(
        sorted(artifacts, key=lambda item: (item.root, item.path, item.role))
    )
    successor = successor_state or ALLOWED_SUCCESSORS[family][-1]
    dependencies = tuple(sorted(dependency_keys))
    item_authority = _authority(authority or {}, name="item authority")
    return WorksetItem(
        family=family,
        natural_key=natural_key,
        canonical_successor_state=successor,
        dependency_keys=dependencies,
        artifacts=ordered_artifacts,
        authority=item_authority,
        namespace_digest=_item_namespace_digest(
            family=family,
            natural_key=natural_key,
            successor=successor,
            dependencies=dependencies,
            artifacts=ordered_artifacts,
            authority=item_authority,
        ),
    )


def _make_family(
    family: str,
    artifacts: Sequence[ArtifactObligation],
    *,
    record_count: int,
    actionable_count: int,
    authority: Mapping[str, object],
) -> FamilyInventory:
    ordered = tuple(
        sorted(artifacts, key=lambda item: (item.root, item.path, item.role))
    )
    normalized_authority = _authority(authority, name="family authority")
    return FamilyInventory(
        family=family,
        record_count=record_count,
        actionable_count=actionable_count,
        artifacts=ordered,
        authority=normalized_authority,
        namespace_digest=_family_namespace_digest(
            family=family,
            record_count=record_count,
            actionable_count=actionable_count,
            artifacts=ordered,
            authority=normalized_authority,
        ),
    )


def _context_payload(context: drain_v2.WorksetContext) -> dict[str, object]:
    try:
        return drain_v2._context_payload(context)  # noqa: SLF001
    except drain_v2.DrainV2Error as exc:
        raise WorksetManifestIntegrityError(str(exc)) from exc


def _context_from_payload(value: object) -> drain_v2.WorksetContext:
    try:
        return drain_v2._context_from_payload(value)  # noqa: SLF001
    except drain_v2.DrainV2Error as exc:
        raise WorksetManifestIntegrityError(str(exc)) from exc


def _binding_payload(binding: AdmissionCutBinding) -> dict[str, object]:
    return {
        "event": _artifact_payload(binding.event),
        "latest_attempt": _artifact_payload(binding.latest_attempt),
        "recorded_at_utc": binding.recorded_at_utc,
        "authority_context": _context_payload(binding.context),
    }


def _publisher_implementation() -> dict[str, object]:
    """Capture producer provenance without creating a self-hash profile cycle."""

    implementation_path = Path(__file__).resolve()
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            implementation_path,
            name="workset-manifest publisher implementation",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
        relative = snapshot.path.relative_to(ROOT.resolve()).as_posix()
    except (ValueError, registry.EpochRegistryError) as exc:
        raise WorksetManifestIntegrityError(
            "Cannot capture workset-manifest publisher implementation"
        ) from exc
    return {
        "path": relative,
        "sha256": snapshot.sha256,
        "size_bytes": snapshot.size_bytes,
    }


def _publisher_from_payload(value: object) -> dict[str, object]:
    payload = _exact(
        value,
        {"path", "sha256", "size_bytes"},
        name="publisher implementation",
    )
    if (
        payload["path"] != "code/monitoring/ootang_epoch_workset_manifest.py"
        or type(payload["size_bytes"]) is not int
        or not 0 < payload["size_bytes"] <= MAX_CONTROL_BYTES
    ):
        raise WorksetManifestIntegrityError(
            "Publisher implementation reference changed"
        )
    _hash(payload["sha256"], name="publisher implementation SHA-256")
    return payload


def _normalize_inspection(
    profile: Mapping[str, Any],
    paths: WorksetManifestPaths,
    inspection: WorksetInspection,
    *,
    dereference_artifacts: bool = True,
) -> tuple[WorksetItem, ...]:
    if not isinstance(inspection, WorksetInspection):
        raise WorksetManifestIntegrityError("Inspector returned an unknown result")
    _context_payload(inspection.context)
    if len(inspection.items) > profile["protocol"]["maximum_items"]:
        raise WorksetManifestIntegrityError("Workset item bound exceeded")
    natural_keys: set[str] = set()
    artifact_count = 0
    normalized: list[WorksetItem] = []
    for item in inspection.items:
        if not isinstance(item, WorksetItem) or item.family not in FAMILIES:
            raise WorksetManifestIntegrityError("Workset contains an unknown family")
        natural_key = _text(item.natural_key, name="natural key", maximum=4096)
        if not natural_key.startswith(f"{item.family}:"):
            raise WorksetManifestIntegrityError("Natural key has the wrong family")
        if natural_key in natural_keys:
            raise WorksetManifestIntegrityError("Duplicate natural key")
        natural_keys.add(natural_key)
        if item.canonical_successor_state not in ALLOWED_SUCCESSORS[item.family]:
            raise WorksetManifestIntegrityError(
                "Canonical successor state is outside the reviewed graph"
            )
        dependencies = tuple(sorted(item.dependency_keys))
        if dependencies != item.dependency_keys or len(set(dependencies)) != len(
            dependencies
        ):
            raise WorksetManifestIntegrityError("Dependency keys are not canonical")
        for dependency in dependencies:
            _text(dependency, name="dependency key", maximum=4096)
            if dependency == natural_key:
                raise WorksetManifestIntegrityError("Item depends on itself")
        if not item.artifacts:
            raise WorksetManifestIntegrityError(
                "Workset item has no frozen-observation artifact seed"
            )
        ordered_artifacts = tuple(
            sorted(
                item.artifacts, key=lambda value: (value.root, value.path, value.role)
            )
        )
        if ordered_artifacts != item.artifacts:
            raise WorksetManifestIntegrityError("Artifact obligations are not ordered")
        identities: set[tuple[str, str, str]] = set()
        for artifact in ordered_artifacts:
            identity = (artifact.root, artifact.path, artifact.role)
            if identity in identities:
                raise WorksetManifestIntegrityError("Artifact obligation repeats")
            identities.add(identity)
            validator = (
                _validate_artifact
                if dereference_artifacts
                else _validate_artifact_metadata
            )
            validator(
                artifact,
                paths=paths,
                maximum_bytes=profile["protocol"]["maximum_artifact_bytes"],
            )
        artifact_count += len(ordered_artifacts)
        if artifact_count > profile["protocol"]["maximum_artifacts"]:
            raise WorksetManifestIntegrityError("Artifact obligation bound exceeded")
        normalized_authority = _authority(item.authority, name="item authority")
        if dict(item.authority) != normalized_authority:
            raise WorksetManifestIntegrityError("Item authority is not canonical")
        if item.namespace_digest != _item_namespace_digest(
            family=item.family,
            natural_key=natural_key,
            successor=item.canonical_successor_state,
            dependencies=dependencies,
            artifacts=ordered_artifacts,
            authority=normalized_authority,
        ):
            raise WorksetManifestIntegrityError("Namespace digest changed")
        normalized.append(item)
    missing = {
        dependency
        for item in normalized
        for dependency in item.dependency_keys
        if dependency not in natural_keys
    }
    if missing:
        raise WorksetManifestIntegrityError(
            "Workset dependency is missing:" + sorted(missing)[0]
        )
    ordered = tuple(
        sorted(
            normalized, key=lambda item: (FAMILIES.index(item.family), item.natural_key)
        )
    )
    if ordered != inspection.items:
        raise WorksetManifestIntegrityError("Workset items are not canonical")
    if tuple(value.family for value in inspection.families) != FAMILIES:
        raise WorksetManifestIntegrityError(
            "Six-family namespace inventories are missing or reordered"
        )
    for family_inventory in inspection.families:
        if (
            type(family_inventory.record_count) is not int
            or type(family_inventory.actionable_count) is not int
            or family_inventory.record_count < 0
            or family_inventory.actionable_count < 0
            or family_inventory.actionable_count > family_inventory.record_count
        ):
            raise WorksetManifestIntegrityError("Family inventory counts are invalid")
        actionable = sum(item.family == family_inventory.family for item in ordered)
        if family_inventory.actionable_count != actionable:
            raise WorksetManifestIntegrityError(
                "Family actionable count differs from reserved items"
            )
        family_artifacts = tuple(
            sorted(
                family_inventory.artifacts,
                key=lambda value: (value.root, value.path, value.role),
            )
        )
        if family_artifacts != family_inventory.artifacts:
            raise WorksetManifestIntegrityError(
                "Family namespace artifacts are not ordered"
            )
        seen_family_artifacts: set[tuple[str, str, str]] = set()
        for artifact in family_artifacts:
            identity = (artifact.root, artifact.path, artifact.role)
            if identity in seen_family_artifacts:
                raise WorksetManifestIntegrityError("Family namespace artifact repeats")
            seen_family_artifacts.add(identity)
            validator = (
                _validate_artifact
                if dereference_artifacts
                else _validate_artifact_metadata
            )
            validator(
                artifact,
                paths=paths,
                maximum_bytes=profile["protocol"]["maximum_artifact_bytes"],
            )
        artifact_count += len(family_artifacts)
        if artifact_count > profile["protocol"]["maximum_artifacts"]:
            raise WorksetManifestIntegrityError("Artifact obligation bound exceeded")
        family_authority = _authority(
            family_inventory.authority, name="family authority"
        )
        if dict(family_inventory.authority) != family_authority:
            raise WorksetManifestIntegrityError("Family authority is not canonical")
        if family_inventory.namespace_digest != _family_namespace_digest(
            family=family_inventory.family,
            record_count=family_inventory.record_count,
            actionable_count=family_inventory.actionable_count,
            artifacts=family_artifacts,
            authority=family_authority,
        ):
            raise WorksetManifestIntegrityError("Family namespace digest changed")
    return ordered


def _family_payload(value: FamilyInventory) -> dict[str, object]:
    return {
        "family": value.family,
        "record_count": value.record_count,
        "actionable_count": value.actionable_count,
        "artifacts": [_artifact_payload(item) for item in value.artifacts],
        "authority": dict(value.authority),
        "namespace_digest": value.namespace_digest,
    }


def _family_from_payload(value: object) -> FamilyInventory:
    payload = _exact(
        value,
        {
            "family",
            "record_count",
            "actionable_count",
            "artifacts",
            "authority",
            "namespace_digest",
        },
        name="family inventory",
    )
    if not isinstance(payload["artifacts"], list) or not isinstance(
        payload["authority"], dict
    ):
        raise WorksetManifestIntegrityError("Family inventory collections changed")
    return FamilyInventory(
        family=payload["family"],
        record_count=payload["record_count"],
        actionable_count=payload["actionable_count"],
        artifacts=tuple(_artifact_from_payload(item) for item in payload["artifacts"]),
        authority=payload["authority"],
        namespace_digest=payload["namespace_digest"],
    )


def _item_payload(item: WorksetItem) -> dict[str, object]:
    return {
        "family": item.family,
        "natural_key": item.natural_key,
        "canonical_successor_state": item.canonical_successor_state,
        "dependency_keys": list(item.dependency_keys),
        "artifacts": [_artifact_payload(value) for value in item.artifacts],
        "authority": dict(item.authority),
        "namespace_digest": item.namespace_digest,
    }


def _item_from_payload(value: object) -> WorksetItem:
    payload = _exact(
        value,
        {
            "family",
            "natural_key",
            "canonical_successor_state",
            "dependency_keys",
            "artifacts",
            "authority",
            "namespace_digest",
        },
        name="manifest item",
    )
    if (
        not isinstance(payload["dependency_keys"], list)
        or not isinstance(payload["artifacts"], list)
        or not isinstance(payload["authority"], dict)
    ):
        raise WorksetManifestIntegrityError("Manifest item collections changed")
    return WorksetItem(
        family=payload["family"],
        natural_key=payload["natural_key"],
        canonical_successor_state=payload["canonical_successor_state"],
        dependency_keys=tuple(payload["dependency_keys"]),
        artifacts=tuple(_artifact_from_payload(item) for item in payload["artifacts"]),
        authority=payload["authority"],
        namespace_digest=payload["namespace_digest"],
    )


def _manifest_payload(
    profile: Mapping[str, Any],
    binding: AdmissionCutBinding,
    items: Sequence[WorksetItem],
    families: Sequence[FamilyInventory],
    *,
    publisher_implementation: Mapping[str, object] | None = None,
) -> dict[str, object]:
    keyset = [item.natural_key for item in items]
    publisher = _publisher_from_payload(
        publisher_implementation or _publisher_implementation()
    )
    return {
        "schema_version": profile["protocol"]["manifest_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "manifest_role": (
            "complete_frozen_observation_workset_and_transition_seed_reservation_only"
        ),
        "publisher_implementation": publisher,
        "admission_cut": _binding_payload(binding),
        "frozen_live_upper_tip": {
            "old_live_epoch_id": binding.context.old_live_epoch_id,
            "live_event_count": binding.context.live_event_count,
            "live_terminal_sha256": binding.context.live_terminal_sha256,
        },
        "family_count": len(FAMILIES),
        "families": [_family_payload(value) for value in families],
        "item_count": len(items),
        "actionable_count": len(items),
        "artifact_count": sum(len(item.artifacts) for item in items)
        + sum(len(family.artifacts) for family in families),
        "workset_keyset_sha256": _sha256(_canonical_bytes({"natural_keys": keyset})),
        "items": [_item_payload(item) for item in items],
        **_claims(),
    }


def _strict_entries(path: Path, *, name: str) -> tuple[Path, ...]:
    if not path.exists() and not path.is_symlink():
        return ()
    try:
        if not stat.S_ISDIR(os.lstat(path).st_mode):
            raise WorksetManifestIntegrityError(f"{name} is not a real directory")
        entries = tuple(sorted(path.iterdir(), key=lambda item: item.name))
        for entry in entries:
            mode = os.lstat(entry).st_mode
            if (
                entry.name.startswith(".")
                or entry.suffix != ".json"
                or not stat.S_ISREG(mode)
            ):
                raise WorksetManifestIntegrityError(f"{name} contains an unknown entry")
        return entries
    except OSError as exc:
        raise WorksetManifestIntegrityError(f"Cannot inspect {name}") from exc


def _read_json(
    path: Path, *, name: str, maximum_bytes: int
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            path, name=name, maximum_bytes=maximum_bytes
        )
        payload = registry._decode_json(snapshot.raw, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise WorksetManifestIntegrityError(str(exc)) from exc
    if snapshot.raw != _canonical_bytes(payload):
        raise WorksetManifestIntegrityError(f"{name} is not canonical JSON")
    return payload, snapshot


def _load_manifest_reference(
    profile: Mapping[str, Any],
    paths: WorksetManifestPaths,
    value: object,
    binding: AdmissionCutBinding,
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    reference = _exact(
        value, {"path", "sha256", "size_bytes"}, name="manifest reference"
    )
    digest = _hash(reference["sha256"], name="manifest SHA-256")
    expected_path = f"manifests/sha256/{digest}.json"
    size = reference["size_bytes"]
    if (
        reference["path"] != expected_path
        or type(size) is not int
        or not 0 < size <= profile["protocol"]["maximum_manifest_bytes"]
    ):
        raise WorksetManifestIntegrityError("Manifest reference changed")
    payload, snapshot = _read_json(
        paths.root / expected_path,
        name="frozen-observation workset manifest",
        maximum_bytes=profile["protocol"]["maximum_manifest_bytes"],
    )
    if snapshot.sha256 != digest or snapshot.size_bytes != size:
        raise WorksetManifestIntegrityError("Manifest reference did not dereference")
    expected_keys = {
        "schema_version",
        "profile_id",
        "profile_sha256",
        "manifest_role",
        "publisher_implementation",
        "admission_cut",
        "frozen_live_upper_tip",
        "family_count",
        "families",
        "item_count",
        "actionable_count",
        "artifact_count",
        "workset_keyset_sha256",
        "items",
        *TRUE_CAPABILITIES,
        *FALSE_CLAIMS,
    }
    _exact(payload, expected_keys, name="frozen-observation workset manifest")
    if (
        payload["schema_version"] != profile["protocol"]["manifest_schema_version"]
        or payload["profile_id"] != profile["profile_id"]
        or payload["profile_sha256"] != profile["_profile_sha256"]
        or payload["manifest_role"]
        != ("complete_frozen_observation_workset_and_transition_seed_reservation_only")
        or payload["admission_cut"] != _binding_payload(binding)
        or any(payload.get(name) is not True for name in TRUE_CAPABILITIES)
        or any(payload.get(name) is not False for name in FALSE_CLAIMS)
        or not isinstance(payload["items"], list)
        or not isinstance(payload["families"], list)
    ):
        raise WorksetManifestIntegrityError("Manifest semantics changed")
    items = tuple(_item_from_payload(item) for item in payload["items"])
    families = tuple(_family_from_payload(item) for item in payload["families"])
    publisher = _publisher_from_payload(payload["publisher_implementation"])
    normalized = _normalize_inspection(
        profile,
        paths,
        WorksetInspection(binding.context, items, families),
        dereference_artifacts=False,
    )
    expected = _manifest_payload(
        profile,
        binding,
        normalized,
        families,
        publisher_implementation=publisher,
    )
    if payload != expected:
        raise WorksetManifestIntegrityError("Manifest frozen observation changed")
    return payload, snapshot


def _event_payload(
    profile: Mapping[str, Any],
    paths: WorksetManifestPaths,
    binding: AdmissionCutBinding,
    manifest: registry.ArtifactSnapshot,
    *,
    recorded_at: str,
    publisher_implementation: Mapping[str, object],
) -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": profile["protocol"]["event_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "sequence_id": 1,
        "previous_entry_sha256": ZERO_HASH,
        "event_type": profile["protocol"]["event_type"],
        "recorded_at_utc": recorded_at,
        "event_role": (
            "singleton_frozen_observation_workset_and_transition_seed_reservation_only"
        ),
        "publisher_implementation": _publisher_from_payload(publisher_implementation),
        "admission_cut": _binding_payload(binding),
        "manifest": {
            "path": manifest.path.relative_to(paths.root).as_posix(),
            "sha256": manifest.sha256,
            "size_bytes": manifest.size_bytes,
        },
        **_claims(),
    }
    return {**body, "entry_sha256": _sha256(_canonical_bytes(body))}


def _load_existing_event(
    profile: Mapping[str, Any],
    paths: WorksetManifestPaths,
    binding: AdmissionCutBinding,
) -> tuple[dict[str, Any], registry.ArtifactSnapshot, registry.ArtifactSnapshot] | None:
    events = _strict_entries(paths.events, name="workset reservation events")
    manifests = _strict_entries(
        paths.manifests, name="frozen-observation workset manifests"
    )
    if not events:
        if manifests:
            raise WorksetManifestIntegrityError(
                "Orphan manifest exists without reservation event"
            )
        return None
    if len(events) != 1 or len(manifests) != 1:
        raise WorksetManifestIntegrityError("Workset reservation namespace branched")
    payload, event_snapshot = _read_json(
        events[0], name="workset reservation event", maximum_bytes=MAX_CONTROL_BYTES
    )
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
            "publisher_implementation",
            "admission_cut",
            "manifest",
            *TRUE_CAPABILITIES,
            *FALSE_CLAIMS,
            "entry_sha256",
        },
        name="workset reservation event",
    )
    body = dict(payload)
    entry = body.pop("entry_sha256", None)
    if (
        entry != _sha256(_canonical_bytes(body))
        or events[0].name != f"{1:020d}-{entry}.json"
        or payload["schema_version"] != profile["protocol"]["event_schema_version"]
        or payload["profile_id"] != profile["profile_id"]
        or payload["profile_sha256"] != profile["_profile_sha256"]
        or payload["sequence_id"] != 1
        or payload["previous_entry_sha256"] != ZERO_HASH
        or payload["event_type"] != profile["protocol"]["event_type"]
        or payload["event_role"]
        != ("singleton_frozen_observation_workset_and_transition_seed_reservation_only")
        or payload["admission_cut"] != _binding_payload(binding)
        or any(payload.get(name) is not True for name in TRUE_CAPABILITIES)
        or any(payload.get(name) is not False for name in FALSE_CLAIMS)
    ):
        raise WorksetManifestIntegrityError("Reservation event semantics changed")
    try:
        event_time = registry._utc_timestamp(  # noqa: SLF001
            payload["recorded_at_utc"], name="reservation recorded_at"
        )
        cut_time = registry._utc_timestamp(  # noqa: SLF001
            binding.recorded_at_utc, name="cut recorded_at"
        )
    except registry.EpochRegistryError as exc:
        raise WorksetManifestIntegrityError(str(exc)) from exc
    if event_time < cut_time:
        raise WorksetManifestIntegrityError("Reservation event predates admission cut")
    manifest_payload, manifest = _load_manifest_reference(
        profile, paths, payload["manifest"], binding
    )
    if (
        _publisher_from_payload(payload["publisher_implementation"])
        != (manifest_payload["publisher_implementation"])
    ):
        raise WorksetManifestIntegrityError(
            "Reservation publisher provenance differs from manifest"
        )
    if manifests[0] != manifest.path:
        raise WorksetManifestIntegrityError(
            "Reservation references a different manifest"
        )
    return payload, event_snapshot, manifest


def _default_admission_cut_binding(
    paths: WorksetManifestPaths,
) -> AdmissionCutBinding | None:
    """Replay the cut's private validators without opening a cut writer lock."""

    try:
        profile = admission_cut.load_admission_cut_profile()
        cut_paths = admission_cut.admission_cut_paths(
            profile,
            runtime_root=paths.registry_root,
            active_runtime_root=paths.active_root,
            shadow_runtime_root=paths.shadow_root,
        )
        v2_binding = admission_cut._load_v2_binding(cut_paths)  # noqa: SLF001
        if v2_binding is None:
            return None
        prepare = admission_cut._load_prepare(  # noqa: SLF001
            profile, cut_paths, v2_binding
        )
        if prepare is None:
            return None
        intent = admission_cut._load_intent(  # noqa: SLF001
            profile, cut_paths, prepare[1]
        )
        if intent is None:
            return None
        physical = admission_cut._physical_state(  # noqa: SLF001
            profile, cut_paths, prepare[1], intent[0]
        )
        attempts = admission_cut._load_attempts(  # noqa: SLF001
            profile, cut_paths, prepare[1], intent[1]
        )
        event = admission_cut._load_event(  # noqa: SLF001
            profile, cut_paths, prepare[1], intent[1], attempts
        )
    except admission_cut.AdmissionCutError as exc:
        raise WorksetManifestIntegrityError(str(exc)) from exc
    if physical != "both_cut" or event is None:
        return None
    if not attempts:
        raise WorksetManifestIntegrityError("Admission cut event lacks its attempt")
    context = _context_from_payload(attempts[-1][0]["authority_context"])
    return AdmissionCutBinding(
        context=context,
        event=_artifact_from_snapshot(
            event[1], role="admission_cut_event", root_label="registry", paths=paths
        ),
        latest_attempt=_artifact_from_snapshot(
            attempts[-1][1],
            role="admission_cut_latest_attempt",
            root_label="registry",
            paths=paths,
        ),
        recorded_at_utc=event[0]["recorded_at_utc"],
    )


def _default_inspection(
    paths: WorksetManifestPaths,
    binding: AdmissionCutBinding,
    machine_now: datetime,
) -> WorksetInspection:
    """Adapt the independent read-only six-family production inventory."""

    from monitoring import ootang_epoch_workset_inventory as inventory

    try:
        result = inventory.inspect_closed_workset(
            active_root=paths.active_root,
            shadow_root=paths.shadow_root,
            context=binding.context,
            machine_now=machine_now,
            maximum_artifact_bytes=64 * 1024 * 1024,
        )
    except inventory.WorksetInventoryError as exc:
        raise WorksetManifestIntegrityError(str(exc)) from exc
    if (
        result.context_epoch_id != binding.context.old_live_epoch_id
        or result.frozen_live_event_count != binding.context.live_event_count
        or result.frozen_live_terminal_sha256 != binding.context.live_terminal_sha256
    ):
        raise WorksetManifestIntegrityError(
            "Independent inventory changed the frozen admission-cut context"
        )

    def artifact(value: object) -> ArtifactObligation:
        return ArtifactObligation(
            role=str(getattr(value, "role")),
            root=str(getattr(value, "root")),
            path=str(getattr(value, "path")),
            sha256=str(getattr(value, "sha256")),
            size_bytes=getattr(value, "size_bytes"),
        )

    items = tuple(
        WorksetItem(
            family=value.family,
            natural_key=value.natural_key,
            canonical_successor_state=value.canonical_successor_state,
            dependency_keys=tuple(value.dependency_keys),
            artifacts=tuple(artifact(item) for item in value.artifacts),
            authority=dict(value.authority),
            namespace_digest=value.namespace_digest,
        )
        for value in result.items
    )
    families = tuple(
        FamilyInventory(
            family=value.family,
            record_count=value.record_count,
            actionable_count=value.actionable_count,
            artifacts=tuple(artifact(item) for item in value.artifacts),
            authority=dict(value.authority),
            namespace_digest=value.namespace_digest,
        )
        for value in result.families
    )
    return WorksetInspection(binding.context, items, families)


def _write_status(
    profile: Mapping[str, Any],
    paths: WorksetManifestPaths,
    *,
    checked_at: str,
    status: str,
    reason: str,
    event: Mapping[str, Any] | None,
    complete_workset_enumeration: bool = False,
    bounded_workset_reservation_implemented: bool = False,
) -> None:
    event_observation = (
        "verified"
        if event is not None
        else ("absent" if status == "waiting_for_admission_cut" else "unknown")
    )
    payload = {
        "schema_version": "ootang_epoch_workset_manifest_status_v2",
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "checked_at_utc": checked_at,
        "status": status,
        "reason": reason,
        "event_count": (
            1 if event is not None else (0 if event_observation == "absent" else None)
        ),
        "terminal_entry_sha256": event["entry_sha256"]
        if event is not None
        else (ZERO_HASH if event_observation == "absent" else None),
        "event_observation": event_observation,
        "cache_authority": False,
        **_claims(),
        "complete_workset_enumeration": complete_workset_enumeration,
        "bounded_workset_reservation_implemented": (
            bounded_workset_reservation_implemented
        ),
    }
    try:
        registry._atomic_cache(  # noqa: SLF001
            paths.status,
            _canonical_bytes(payload),
            root=paths.root,
            name="workset-manifest status",
        )
    except registry.EpochRegistryError as exc:
        raise WorksetManifestIntegrityError(str(exc)) from exc


def _publish_reservation(
    profile: Mapping[str, Any],
    paths: WorksetManifestPaths,
    binding: AdmissionCutBinding,
    items: Sequence[WorksetItem],
    families: Sequence[FamilyInventory],
    *,
    recorded_at: str,
) -> tuple[dict[str, Any], registry.ArtifactSnapshot, registry.ArtifactSnapshot, bool]:
    publisher = _publisher_implementation()
    raw = _canonical_bytes(
        _manifest_payload(
            profile,
            binding,
            items,
            families,
            publisher_implementation=publisher,
        )
    )
    if len(raw) > profile["protocol"]["maximum_manifest_bytes"]:
        raise WorksetManifestIntegrityError(
            "Closed-workset manifest exceeds byte bound"
        )
    digest = _sha256(raw)
    manifest_path = paths.manifests / f"{digest}.json"
    try:
        proposed_time = registry._utc_timestamp(  # noqa: SLF001
            recorded_at, name="new reservation recorded_at"
        )
        cut_time = registry._utc_timestamp(  # noqa: SLF001
            binding.recorded_at_utc, name="admission cut recorded_at"
        )
    except registry.EpochRegistryError as exc:
        raise WorksetManifestIntegrityError(str(exc)) from exc
    if proposed_time < cut_time:
        raise WorksetManifestIntegrityError(
            "New reservation event would predate admission cut"
        )

    event_entries = _strict_entries(paths.events, name="workset reservation events")
    manifest_entries = _strict_entries(
        paths.manifests, name="frozen-observation workset manifests"
    )
    if event_entries:
        existing = _load_existing_event(profile, paths, binding)
        if existing is None:
            raise WorksetManifestIntegrityError("Reservation event disappeared")
        return existing[0], existing[1], existing[2], False
    if manifest_entries:
        if len(manifest_entries) != 1 or manifest_entries[0] != manifest_path:
            raise WorksetManifestIntegrityError(
                "Orphan manifest does not match the current frozen observation"
            )
        payload, manifest = _read_json(
            manifest_path,
            name="recoverable orphan frozen-observation workset manifest",
            maximum_bytes=profile["protocol"]["maximum_manifest_bytes"],
        )
        if manifest.raw != raw or payload != _manifest_payload(
            profile,
            binding,
            items,
            families,
            publisher_implementation=publisher,
        ):
            raise WorksetManifestIntegrityError(
                "Orphan manifest bytes differ from current enumeration"
            )
    else:
        manifest = None
    try:
        if manifest is None:
            manifest = drain._publish_once_durable(  # noqa: SLF001
                manifest_path,
                raw,
                root=paths.root,
                name="frozen-observation workset manifest",
            )
        event = _event_payload(
            profile,
            paths,
            binding,
            manifest,
            recorded_at=recorded_at,
            publisher_implementation=publisher,
        )
        event_path = paths.events / f"{1:020d}-{event['entry_sha256']}.json"
        event_snapshot = drain._publish_once_durable(  # noqa: SLF001
            event_path,
            _canonical_bytes(event),
            root=paths.root,
            name="workset reservation event",
        )
    except drain.EpochDrainError as exc:
        raise WorksetManifestIntegrityError(str(exc)) from exc
    replayed = _load_existing_event(profile, paths, binding)
    if replayed is None or replayed[0] != event:
        raise WorksetManifestIntegrityError("Reservation event did not replay")
    return event, event_snapshot, manifest, True


def _coordinate_epoch_workset_manifest(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    runtime_root: Path | None = None,
    active_runtime_root: Path | None = None,
    shadow_runtime_root: Path | None = None,
    clock: Callable[[], datetime] | None = None,
    inspect_workset: InspectWorkset | None = None,
    inspect_admission_cut: InspectAdmissionCut | None = None,
) -> WorksetManifestResult:
    """Inspect under surviving locks and publish one exact reservation."""

    profile = load_workset_manifest_profile(config_path)
    paths = workset_manifest_paths(
        profile,
        runtime_root=runtime_root,
        active_runtime_root=active_runtime_root,
        shadow_runtime_root=shadow_runtime_root,
    )
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    checked_at = _utc_text(now)
    handles: list[BinaryIO] = []
    all_locks_acquired = False
    event: dict[str, Any] | None = None
    try:
        for label, lock in zip(
            LOCK_ORDER,
            (
                paths.manager_lock,
                paths.cycle_lock,
                paths.replay_lock,
                paths.shadow_lock,
            ),
            strict=True,
        ):
            try:
                handles.append(drain._acquire_lock(lock, label=label))  # noqa: SLF001
            except drain.EpochDrainBusyError as exc:
                raise WorksetManifestBusyError(str(exc)) from exc
            except drain.EpochDrainError as exc:
                raise WorksetManifestIntegrityError(str(exc)) from exc
        all_locks_acquired = True
        binding = (inspect_admission_cut or _default_admission_cut_binding)(paths)
        if binding is None:
            reason = (
                "exact admission-cut event/attempt/physical boundary is unavailable"
            )
            _write_status(
                profile,
                paths,
                checked_at=checked_at,
                status="waiting_for_admission_cut",
                reason=reason,
                event=None,
            )
            return WorksetManifestResult(
                "waiting_for_admission_cut", reason, paths.status, None, None
            )
        _validate_artifact(
            binding.event,
            paths=paths,
            maximum_bytes=profile["protocol"]["maximum_artifact_bytes"],
        )
        _validate_artifact(
            binding.latest_attempt,
            paths=paths,
            maximum_bytes=profile["protocol"]["maximum_artifact_bytes"],
        )
        if _strict_entries(paths.events, name="workset reservation events"):
            existing = _load_existing_event(profile, paths, binding)
            if existing is None:
                raise WorksetManifestIntegrityError(
                    "Reservation event disappeared during replay"
                )
            event, event_snapshot, manifest_snapshot = existing
            reason = (
                "immutable frozen-observation workset reservation replayed; predecessor bytes "
                "are checked only by exact-key recovery CAS"
            )
            _write_status(
                profile,
                paths,
                checked_at=checked_at,
                status="closed_workset_reservation_idempotent",
                reason=reason,
                event=event,
                complete_workset_enumeration=True,
                bounded_workset_reservation_implemented=True,
            )
            return WorksetManifestResult(
                "closed_workset_reservation_idempotent",
                reason,
                paths.status,
                event_snapshot.path,
                manifest_snapshot.path,
                complete_workset_enumeration=True,
                bounded_workset_reservation_implemented=True,
            )
        inspection = (inspect_workset or _default_inspection)(paths, binding, now)
        if inspection.context != binding.context:
            raise WorksetManifestIntegrityError(
                "Workset inspection changed the admission-cut context"
            )
        items = _normalize_inspection(profile, paths, inspection)
        event, event_snapshot, manifest_snapshot, created = _publish_reservation(
            profile,
            paths,
            binding,
            items,
            inspection.families,
            recorded_at=checked_at,
        )
        status = (
            "closed_workset_reserved"
            if created
            else "closed_workset_reservation_idempotent"
        )
        reason = (
            "all six frozen-observation families and their transition seeds are "
            "bounded and reserved; no terminal/derived-future closure, recovery, "
            "lifecycle, drained, active, trusted, E2, or formal authority"
        )
        _write_status(
            profile,
            paths,
            checked_at=checked_at,
            status=status,
            reason=reason,
            event=event,
            complete_workset_enumeration=True,
            bounded_workset_reservation_implemented=True,
        )
        return WorksetManifestResult(
            status,
            reason,
            paths.status,
            event_snapshot.path,
            manifest_snapshot.path,
            complete_workset_enumeration=True,
            bounded_workset_reservation_implemented=True,
        )
    except WorksetManifestBusyError:
        raise
    except WorksetManifestError as exc:
        if all_locks_acquired:
            try:
                _write_status(
                    profile,
                    paths,
                    checked_at=checked_at,
                    status="blocked_integrity",
                    reason=f"{type(exc).__name__}:{exc}",
                    event=event,
                )
            except WorksetManifestError:
                pass
        raise
    finally:
        try:
            drain._release_locks(handles)  # noqa: SLF001
        except drain.EpochDrainError as exc:
            if sys.exc_info()[0] is None:
                raise WorksetManifestIntegrityError(str(exc)) from exc


def coordinate_epoch_workset_manifest(
    *, config_path: Path = DEFAULT_CONFIG_PATH
) -> WorksetManifestResult:
    """Run the reviewed machine-only frozen-observation reservation poll."""

    return _coordinate_epoch_workset_manifest(config_path=config_path)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = coordinate_epoch_workset_manifest(config_path=args.config)
    except WorksetManifestBusyError as exc:
        print(json.dumps({"status": "busy", "reason": str(exc)}, sort_keys=True))
        return 3
    except WorksetManifestError as exc:
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
                "manifest_path": (
                    str(result.manifest_path) if result.manifest_path else None
                ),
                "complete_workset_enumeration": (result.complete_workset_enumeration),
                "bounded_workset_reservation_implemented": (
                    result.bounded_workset_reservation_implemented
                ),
                "bounded_workset_recovery_implemented": (
                    result.bounded_workset_recovery_implemented
                ),
                "lifecycle_authority": result.lifecycle_authority,
                "transition_authority": result.transition_authority,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
