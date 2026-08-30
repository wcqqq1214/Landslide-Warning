"""Atomically authorize the prepared epoch and seal the old scheduler scope.

The lifecycle commit is one immutable event.  It does not move a slot or write
either epoch ledger: ``ACTIVE`` authorizes the scheduler to initialize the new
ledger genesis while the same event makes the old official scheduler scope
``SEALED``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, BinaryIO


ROOT = Path(__file__).resolve().parents[2]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_epoch_admission_cut as admission_cut  # noqa: E402
from monitoring import ootang_epoch_bounded_drain_completion as completion  # noqa: E402
from monitoring import ootang_epoch_drain as drain  # noqa: E402
from monitoring import ootang_epoch_preparation as preparation  # noqa: E402
from monitoring import ootang_epoch_registry as registry  # noqa: E402
from monitoring import ootang_epoch_workset_manifest as manifest  # noqa: E402


NAMESPACE = "active_transition_v1"
EVENT_SCHEMA_VERSION = "ootang_epoch_active_transition_event_v1"
STATUS_SCHEMA_VERSION = "ootang_epoch_active_transition_status_v1"
EVENT_TYPE = "epoch_official_scheduler_transition"
ZERO_HASH = "0" * 64
EVENT_NAME = re.compile(r"^00000000000000000001-(?P<digest>[0-9a-f]{64})\.json$")
SCHEDULER_ADAPTER_LOGICAL_PATH = "code/monitoring/ootang_prequential_cycle_v4.py"
SCHEDULER_ADAPTER_PATH = ROOT / SCHEDULER_ADAPTER_LOGICAL_PATH
CYCLE_SCRIPT_LOGICAL_PATH = "code/monitoring/ootang_prequential_cycle_v3.py"
CYCLE_CONFIG_LOGICAL_PATH = "config/ootang_prequential_cycle.v3.json"

TRUE_CLAIMS = {
    "machine_only",
    "lifecycle_authority",
    "transition_authority",
    "activation_candidate_selected",
    "active_epoch_switch_implemented",
    "scheduler_entrypoint_authorization_implemented",
    "bounded_official_workset_drained",
}
FALSE_CLAIMS = {
    "old_epoch_drained",
    "old_epoch_drain_implemented",
    "old_work_admission_fence_implemented",
    "canonical_old_issue_route_fence_implemented",
    "direct_filesystem_writer_fence_implemented",
    "anti_rollback_authority_implemented",
    "automatic_epoch_rotation_implemented",
    "old_live_v1_entrypoint_disabled",
    "trusted_anchor_receipt_verified",
    "e2_live_evidence_eligible",
    "real_activation_ready",
    "formal_warning_output",
    "automatic_calibration_promotion_implemented",
    "cross_ledger_single_database_atomicity_implemented",
    "new_epoch_genesis_initialized",
}
REFERENCE_KEYS = {"path", "sha256", "size_bytes"}
COMPLETION_EVENT_KEYS = {
    "schema_version",
    "sequence_id",
    "previous_entry_sha256",
    "event_type",
    "branch",
    "authority_scope",
    "candidate_id",
    "slot_id",
    "old_live_epoch_id",
    "frozen_live_event_count",
    "frozen_live_terminal_sha256",
    "fresh_live_event_count",
    "fresh_live_terminal_sha256",
    "admission_cut_event",
    "admission_cut_latest_attempt",
    "source_derived_bounded_terminal_closure_proof",
    "source_derived_bounded_terminal_closure_event",
    "fresh_six_family_capture",
    "fresh_actionable_item_count",
    "fresh_context_extends_frozen_cut",
    "admission_cut_both_cut_verified",
    "bounded_source_derived_terminal_closure_verified",
    "machine_only",
    "bounded_official_workset_drained",
    "old_work_admission_fence_implemented",
    "old_epoch_drained",
    "canonical_old_issue_route_fence_implemented",
    "direct_filesystem_writer_fence_implemented",
    "active_epoch_switch_implemented",
    "lifecycle_authority",
    "transition_authority",
    "recorded_at_utc",
    "entry_sha256",
}


class ActiveTransitionError(RuntimeError):
    """Base machine lifecycle-transition failure."""


class ActiveTransitionConfigError(ActiveTransitionError):
    """The profile-free namespace no longer matches its reviewed upstreams."""


class ActiveTransitionIntegrityError(ActiveTransitionError):
    """A completion, candidate, transition event, or authorization failed closed."""


class ActiveTransitionBusyError(ActiveTransitionError):
    """A required machine coordinator lock is busy."""


class ActiveTransitionNotReadyError(ActiveTransitionError):
    """No authoritative transition event exists yet."""


@dataclass(frozen=True)
class ActiveTransitionPaths:
    registry_root: Path
    root: Path
    events: Path
    status: Path
    manager_lock: Path
    old_active_root: Path
    old_shadow_root: Path
    completion: completion.BoundedDrainCompletionPaths


@dataclass(frozen=True)
class ActiveTransitionResult:
    status: str
    reason: str
    status_path: Path
    event_path: Path | None = None
    active_epoch_switch_implemented: bool = False
    scheduler_entrypoint_authorization_implemented: bool = False


@dataclass(frozen=True)
class SchedulerAuthorization:
    transition_entry_sha256: str
    transition_entry_path: Path
    candidate_id: str
    slot_id: str
    old_live_epoch_id: str
    new_live_epoch_id: str
    registry_root: Path
    runtime_root: Path
    shadow_runtime_root: Path
    executable_tree_root: Path
    cycle_script: Path
    cycle_config: Path
    scheduler_adapter_sha256: str


@dataclass(frozen=True)
class _CompletionEvidence:
    payload: dict[str, Any]
    snapshot: registry.ArtifactSnapshot
    registry_event_sequence_id: int | None = None
    registry_event_entry_sha256: str | None = None
    preparation_event_sequence_id: int | None = None
    preparation_event_entry_sha256: str | None = None


@dataclass(frozen=True)
class _PreparedAuthority:
    registry_event: dict[str, Any]
    preparation_event: dict[str, Any]
    candidate_receipt: dict[str, Any]
    executable_capsule: dict[str, Any]
    executable_tree_root: Path
    cycle_script: registry.ArtifactSnapshot
    cycle_config: registry.ArtifactSnapshot


LoadCurrentCompletion = Callable[
    [ActiveTransitionPaths, datetime], _CompletionEvidence | None
]
LoadCurrentAuthority = Callable[
    [ActiveTransitionPaths, _CompletionEvidence], _PreparedAuthority | None
]
LoadHistoricalAuthority = Callable[
    [ActiveTransitionPaths, str, str], _PreparedAuthority
]


def _canonical_bytes(value: object) -> bytes:
    return registry._canonical_bytes(value)  # type: ignore[arg-type]  # noqa: SLF001


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _hash(value: object, *, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ActiveTransitionIntegrityError(f"{name} is not a lowercase SHA-256")
    return value


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ActiveTransitionIntegrityError(
            "Active transition clock must be timezone-aware"
        )
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def active_transition_paths(
    *,
    runtime_root: Path | None = None,
    active_runtime_root: Path | None = None,
    shadow_runtime_root: Path | None = None,
) -> ActiveTransitionPaths:
    """Resolve the profile-free transition namespace from the V2 lock chain."""

    try:
        completion_paths = completion.bounded_drain_completion_paths(
            runtime_root=runtime_root,
            active_runtime_root=active_runtime_root,
            shadow_runtime_root=shadow_runtime_root,
        )
        coverage = completion_paths.closure.current.coverage
        root = registry._contained(  # noqa: SLF001
            completion_paths.registry_root, NAMESPACE, name="active transition root"
        )
        events = registry._contained(  # noqa: SLF001
            root, "events", name="active transition events"
        )
        status = registry._contained(  # noqa: SLF001
            root, "status.json", name="active transition status"
        )
    except (completion.BoundedDrainCompletionError, registry.EpochRegistryError) as exc:
        raise ActiveTransitionConfigError(str(exc)) from exc
    return ActiveTransitionPaths(
        registry_root=completion_paths.registry_root,
        root=root,
        events=events,
        status=status,
        manager_lock=coverage.manager_lock,
        old_active_root=coverage.active_root,
        old_shadow_root=coverage.shadow_root,
        completion=completion_paths,
    )


def _strict_entries(directory: Path, *, name: str) -> tuple[Path, ...]:
    try:
        return completion.closure._strict_entries(directory, name=name)  # noqa: SLF001
    except completion.closure.SourceDerivedBoundedTerminalClosureError as exc:
        raise ActiveTransitionIntegrityError(str(exc)) from exc


def _event_entries(paths: ActiveTransitionPaths) -> tuple[Path, ...]:
    entries = _strict_entries(paths.events, name="active transition events")
    if len(entries) > 1:
        raise ActiveTransitionIntegrityError(
            "Active transition singleton namespace branched"
        )
    return entries


def _artifact_reference(
    snapshot: registry.ArtifactSnapshot, root: Path
) -> dict[str, object]:
    try:
        relative = snapshot.path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ActiveTransitionIntegrityError(
            "Artifact reference escaped its root"
        ) from exc
    return {
        "path": relative,
        "sha256": snapshot.sha256,
        "size_bytes": snapshot.size_bytes,
    }


def _read_reference(
    value: object,
    *,
    root: Path,
    expected_parent: Path,
    name: str,
) -> registry.ArtifactSnapshot:
    if not isinstance(value, Mapping) or set(value) != REFERENCE_KEYS:
        raise ActiveTransitionIntegrityError(f"{name} reference shape changed")
    digest = _hash(value.get("sha256"), name=f"{name} SHA-256")
    size = value.get("size_bytes")
    relative = value.get("path")
    if not isinstance(relative, str) or type(size) is not int or size < 0:
        raise ActiveTransitionIntegrityError(f"{name} size changed")
    try:
        path = registry._contained(root, relative, name=name)  # noqa: SLF001
        snapshot = registry._read_regular(  # noqa: SLF001
            path, name=name, maximum_bytes=completion.manifest.MAX_CONTROL_BYTES
        )
    except registry.EpochRegistryError as exc:
        raise ActiveTransitionIntegrityError(str(exc)) from exc
    if (
        path.parent != expected_parent
        or snapshot.sha256 != digest
        or snapshot.size_bytes != size
    ):
        raise ActiveTransitionIntegrityError(f"{name} reference changed")
    return snapshot


def _validate_completion_snapshot(
    paths: ActiveTransitionPaths, snapshot: registry.ArtifactSnapshot
) -> _CompletionEvidence:
    try:
        payload = registry._decode_json(  # noqa: SLF001
            snapshot.raw, name="bounded drain completion event"
        )
    except registry.EpochRegistryError as exc:
        raise ActiveTransitionIntegrityError(str(exc)) from exc
    match = completion.EVENT_NAME.fullmatch(snapshot.path.name)
    entry_sha256 = payload.get("entry_sha256")
    unsigned = dict(payload)
    unsigned.pop("entry_sha256", None)
    families = payload.get("fresh_six_family_capture")
    if (
        set(payload) != COMPLETION_EVENT_KEYS
        or snapshot.raw != _canonical_bytes(payload)
        or match is None
        or match.group("digest") != entry_sha256
        or entry_sha256 != _sha256(_canonical_bytes(unsigned))
        or payload.get("schema_version") != completion.EVENT_SCHEMA_VERSION
        or payload.get("sequence_id") != 1
        or payload.get("previous_entry_sha256") != ZERO_HASH
        or payload.get("event_type") != completion.EVENT_TYPE
        or payload.get("branch") != "v2_non_clean_recovery"
        or payload.get("authority_scope") != "official_machine_reserved_workset"
        or payload.get("machine_only") is not True
        or payload.get("bounded_official_workset_drained") is not True
        or payload.get("fresh_actionable_item_count") != 0
        or payload.get("fresh_context_extends_frozen_cut") is not True
        or payload.get("admission_cut_both_cut_verified") is not True
        or payload.get("bounded_source_derived_terminal_closure_verified") is not True
        or payload.get("old_work_admission_fence_implemented") is not False
        or payload.get("old_epoch_drained") is not False
        or payload.get("canonical_old_issue_route_fence_implemented") is not False
        or payload.get("direct_filesystem_writer_fence_implemented") is not False
        or payload.get("active_epoch_switch_implemented") is not False
        or payload.get("lifecycle_authority") is not False
        or payload.get("transition_authority") is not False
        or not isinstance(families, list)
        or len(families) != len(manifest.FAMILIES)
        or {row.get("family") for row in families if isinstance(row, Mapping)}
        != set(manifest.FAMILIES)
        or any(
            not isinstance(row, Mapping)
            or set(row)
            != {"family", "record_count", "actionable_count", "namespace_digest"}
            or row.get("actionable_count") != 0
            for row in families
        )
    ):
        raise ActiveTransitionIntegrityError(
            "Bounded drain completion event scoped semantics changed"
        )
    _hash(payload.get("fresh_live_terminal_sha256"), name="fresh live terminal")
    _hash(payload.get("frozen_live_terminal_sha256"), name="frozen live terminal")
    if (
        not isinstance(payload.get("candidate_id"), str)
        or not isinstance(payload.get("slot_id"), str)
        or not isinstance(payload.get("old_live_epoch_id"), str)
        or type(payload.get("fresh_live_event_count")) is not int
        or payload["fresh_live_event_count"] < 1
        or type(payload.get("frozen_live_event_count")) is not int
        or payload["frozen_live_event_count"] < 1
        or payload["fresh_live_event_count"] < payload["frozen_live_event_count"]
    ):
        raise ActiveTransitionIntegrityError(
            "Bounded drain completion identity changed"
        )
    registry._utc_timestamp(  # noqa: SLF001
        payload.get("recorded_at_utc"), name="bounded completion event time"
    )
    return _CompletionEvidence(payload=dict(payload), snapshot=snapshot)


def _load_referenced_completion(
    paths: ActiveTransitionPaths, event: Mapping[str, Any]
) -> _CompletionEvidence:
    snapshot = _read_reference(
        event.get("bounded_drain_completion_event"),
        root=paths.registry_root,
        expected_parent=paths.completion.events,
        name="bounded drain completion event",
    )
    return _validate_completion_snapshot(paths, snapshot)


def _default_current_completion(
    paths: ActiveTransitionPaths, now: datetime
) -> _CompletionEvidence | None:
    cut = completion._default_closure_loader(paths.completion, now)  # noqa: SLF001
    if cut is None:
        return None
    current_context = completion._default_current_context(  # noqa: SLF001
        paths.completion, cut, now
    )
    try:
        admission_cut._context_successor(  # noqa: SLF001
            cut.context, current_context, name="Active-transition completion context"
        )
    except admission_cut.AdmissionCutError as exc:
        raise ActiveTransitionIntegrityError(str(exc)) from exc
    current_binding = replace(cut.binding, context=current_context)
    inspection = manifest._default_inspection(  # noqa: SLF001
        cut.manifest_paths, current_binding, now
    )
    families, actionable_count = completion._fresh_capture(  # noqa: SLF001
        cut.context, current_context, cut.manifest_paths, inspection
    )
    if actionable_count:
        raise ActiveTransitionIntegrityError(
            "Bounded completion coexists with fresh actionable work"
        )
    expected = completion._event_static_payload(  # noqa: SLF001
        paths.completion, cut, current_context, families
    )
    loaded = completion._load_event(paths.completion, expected)  # noqa: SLF001
    if loaded is None:
        return None
    evidence = _validate_completion_snapshot(paths, loaded[1])
    return _CompletionEvidence(
        payload=evidence.payload,
        snapshot=evidence.snapshot,
        registry_event_sequence_id=cut.context.registry_event_sequence_id,
        registry_event_entry_sha256=cut.context.registry_event_entry_sha256,
        preparation_event_sequence_id=cut.context.preparation_event_sequence_id,
        preparation_event_entry_sha256=cut.context.preparation_event_entry_sha256,
    )


def _cycle_artifact(
    authority: drain._Authority,
    logical_path: str,
    *,
    name: str,
) -> registry.ArtifactSnapshot:
    matches = [
        artifact
        for artifact in authority.executable_capsule["artifacts"]
        if artifact.get("logical_path") == logical_path
    ]
    if len(matches) != 1:
        raise ActiveTransitionIntegrityError(f"Prepared {name} binding changed")
    artifact = matches[0]
    path = registry._contained(  # noqa: SLF001
        preparation._tree_root(  # noqa: SLF001
            authority.preparation_paths, authority.executable_capsule
        ),
        logical_path,
        name=name,
    )
    try:
        snapshot = registry._read_regular(path, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise ActiveTransitionIntegrityError(str(exc)) from exc
    if snapshot.sha256 != artifact.get("sha256") or snapshot.size_bytes != artifact.get(
        "size_bytes"
    ):
        raise ActiveTransitionIntegrityError(f"Prepared {name} changed")
    return snapshot


def _from_drain_authority(authority: drain._Authority) -> _PreparedAuthority:
    tree = preparation._tree_root(  # noqa: SLF001
        authority.preparation_paths, authority.executable_capsule
    )
    return _PreparedAuthority(
        registry_event=dict(authority.r1_event),
        preparation_event=dict(authority.preparation_event),
        candidate_receipt=dict(authority.candidate_receipt),
        executable_capsule=dict(authority.executable_capsule),
        executable_tree_root=tree,
        cycle_script=_cycle_artifact(
            authority, CYCLE_SCRIPT_LOGICAL_PATH, name="prepared cycle-v3 script"
        ),
        cycle_config=_cycle_artifact(
            authority, CYCLE_CONFIG_LOGICAL_PATH, name="prepared cycle-v3 config"
        ),
    )


def _default_current_authority(
    paths: ActiveTransitionPaths, bounded: _CompletionEvidence
) -> _PreparedAuthority | None:
    selectors = (
        bounded.registry_event_entry_sha256,
        bounded.preparation_event_entry_sha256,
    )
    if any(value is None for value in selectors):
        raise ActiveTransitionIntegrityError(
            "Current completion omitted exact R1/R2a selectors"
        )
    profile = drain.load_drain_profile()
    drain_paths = drain.drain_paths(
        profile,
        runtime_root=paths.registry_root,
        active_runtime_root=paths.old_active_root,
        shadow_runtime_root=paths.old_shadow_root,
    )
    loaded = drain._load_authority(  # noqa: SLF001
        profile,
        drain_paths,
        registry_entry_sha256=selectors[0],
        preparation_entry_sha256=selectors[1],
    )
    if isinstance(loaded, drain._Waiting):  # noqa: SLF001
        return None
    return _from_drain_authority(loaded)


def _default_historical_authority(
    paths: ActiveTransitionPaths,
    registry_entry_sha256: str,
    preparation_entry_sha256: str,
) -> _PreparedAuthority:
    """Replay immutable R1/R2a history without requiring current-empty slots."""

    try:
        r1_profile = registry.load_registry_profile()
        prep_profile = preparation.load_preparation_profile()
        r1_paths = registry.registry_paths(r1_profile, runtime_root=paths.registry_root)
        prep_paths = preparation.preparation_paths(
            prep_profile, runtime_root=paths.registry_root
        )
        observations, semantics = registry.replay_feed_observations(
            r1_profile, r1_paths
        )
        del observations
        r1_events = registry.replay_registry(
            r1_profile, r1_paths, _observed_feed_semantics=semantics
        )
        prep_events = preparation.replay_preparations(
            prep_profile, prep_paths, r1_profile, r1_paths, r1_events
        )
        r1_matches = [
            event
            for event in r1_events
            if event.get("entry_sha256") == registry_entry_sha256
        ]
        prep_matches = [
            event
            for event in prep_events
            if event.get("entry_sha256") == preparation_entry_sha256
        ]
        if len(r1_matches) != 1 or len(prep_matches) != 1:
            raise ActiveTransitionIntegrityError(
                "Transition-bound R1/R2a authority was rolled back or duplicated"
            )
        r1_event = dict(r1_matches[0])
        prep_event = dict(prep_matches[0])
        if (
            prep_event["registry_event_sequence_id"] != r1_event["sequence_id"]
            or prep_event["registry_event_entry_sha256"] != r1_event["entry_sha256"]
            or prep_event["candidate_id"] != r1_event["candidate_id"]
            or prep_event["slot_id"] != r1_event["slot_id"]
        ):
            raise ActiveTransitionIntegrityError(
                "Transition-bound R1/R2a relation changed"
            )
        receipt = registry._verify_receipt(  # noqa: SLF001
            r1_profile, r1_paths, r1_event["candidate_receipt"]
        )
        r1_capsule = registry._verify_capsule(  # noqa: SLF001
            r1_profile, r1_paths, receipt["capsule"]
        )
        executable = preparation._verify_executable_capsule(  # noqa: SLF001
            prep_profile,
            prep_paths,
            prep_event["executable_capsule"],
            r1_paths=r1_paths,
            r1_manifest=r1_capsule,
            expected_candidate=receipt,
            expected_registry_event=r1_event,
        )
        tree = preparation._tree_root(prep_paths, executable)  # noqa: SLF001
        preparation._verify_materialized_tree(prep_paths, executable, tree)  # noqa: SLF001
        capsule_ref = prep_event["executable_capsule"]
        if not isinstance(capsule_ref, Mapping):
            raise ActiveTransitionIntegrityError(
                "Transition-bound executable capsule reference changed"
            )
        preparation._verify_smoke_receipt(  # noqa: SLF001
            prep_profile,
            prep_paths,
            prep_event["smoke_receipt"],
            capsule_manifest=executable,
            capsule_sha256=_hash(
                capsule_ref.get("sha256"), name="executable capsule SHA-256"
            ),
        )
        synthetic = drain._Authority(  # noqa: SLF001
            r1_profile=r1_profile,
            r1_paths=r1_paths,
            feed_observation_events=(),
            feed_semantics=tuple(semantics),
            r1_events=tuple(dict(event) for event in r1_events),
            preparation_profile=prep_profile,
            preparation_paths=prep_paths,
            preparation_events=tuple(dict(event) for event in prep_events),
            r1_event=r1_event,
            preparation_event=prep_event,
            candidate_receipt=receipt,
            r1_capsule=r1_capsule,
            executable_capsule=executable,
        )
        return _from_drain_authority(synthetic)
    except ActiveTransitionError:
        raise
    except (registry.EpochRegistryError, preparation.EpochPreparationError) as exc:
        raise ActiveTransitionIntegrityError(str(exc)) from exc


def _require_candidate_binding(
    bounded: _CompletionEvidence, authority: _PreparedAuthority
) -> None:
    payload = bounded.payload
    build = authority.candidate_receipt.get("candidate_build")
    if not isinstance(build, Mapping):
        raise ActiveTransitionIntegrityError("Prepared candidate build changed")
    expected = (
        payload["candidate_id"],
        payload["slot_id"],
    )
    if (
        (
            authority.registry_event.get("candidate_id"),
            authority.registry_event.get("slot_id"),
        )
        != expected
        or (
            authority.preparation_event.get("candidate_id"),
            authority.preparation_event.get("slot_id"),
        )
        != expected
        or (
            authority.candidate_receipt.get("candidate_id"),
            authority.candidate_receipt.get("slot_id"),
        )
        != expected
        or (
            authority.executable_capsule.get("candidate_id"),
            authority.executable_capsule.get("slot_id"),
        )
        != expected
        or authority.executable_capsule.get("candidate_live_epoch_id")
        != build.get("live_epoch_id")
        or build.get("live_epoch_id") == payload["old_live_epoch_id"]
    ):
        raise ActiveTransitionIntegrityError(
            "Completion and prepared candidate binding differ"
        )
    current_selectors = (
        bounded.registry_event_sequence_id,
        bounded.registry_event_entry_sha256,
        bounded.preparation_event_sequence_id,
        bounded.preparation_event_entry_sha256,
    )
    if any(value is not None for value in current_selectors) and (
        any(value is None for value in current_selectors)
        or current_selectors
        != (
            authority.registry_event.get("sequence_id"),
            authority.registry_event.get("entry_sha256"),
            authority.preparation_event.get("sequence_id"),
            authority.preparation_event.get("entry_sha256"),
        )
    ):
        raise ActiveTransitionIntegrityError(
            "Completion and exact R1/R2a selectors differ"
        )


def _runtime_roots(authority: _PreparedAuthority) -> tuple[Path, Path]:
    runtime_root = Path(str(authority.candidate_receipt["slot_live_root"]))
    if (
        authority.executable_capsule.get("slot_live_root") != str(runtime_root)
        or runtime_root.name != "live"
        or runtime_root.parent.name != authority.candidate_receipt.get("slot_id")
    ):
        raise ActiveTransitionIntegrityError("Executable stable slot root changed")
    try:
        runtime_root = registry._absolute_lexical(runtime_root)  # noqa: SLF001
        shadow_root = registry._contained(  # noqa: SLF001
            runtime_root.parent, "shadow", name="candidate shadow runtime root"
        )
    except registry.EpochRegistryError as exc:
        raise ActiveTransitionIntegrityError(str(exc)) from exc
    return runtime_root, shadow_root


def _candidate_drain_paths(
    paths: ActiveTransitionPaths, bounded: _CompletionEvidence
) -> drain.DrainPaths:
    """Derive the candidate writer locks before trusting its current emptiness."""

    try:
        slot_id = _hash(bounded.payload.get("slot_id"), name="candidate slot id")
        r1_profile = registry.load_registry_profile()
        r1_paths = registry.registry_paths(r1_profile, runtime_root=paths.registry_root)
        slot_root = registry._contained(  # noqa: SLF001
            r1_paths.slots, slot_id, name="candidate stable slot"
        )
        live_root = registry._contained(  # noqa: SLF001
            slot_root, "live", name="candidate live runtime root"
        )
        shadow_root = registry._contained(  # noqa: SLF001
            slot_root, "shadow", name="candidate shadow runtime root"
        )
        candidate = drain.drain_paths(
            drain.load_drain_profile(),
            runtime_root=paths.registry_root,
            active_runtime_root=live_root,
            shadow_runtime_root=shadow_root,
        )
    except (registry.EpochRegistryError, drain.EpochDrainError) as exc:
        raise ActiveTransitionIntegrityError(str(exc)) from exc
    if candidate.manager_lock != paths.manager_lock:
        raise ActiveTransitionIntegrityError(
            "Candidate writer locks do not share the transition manager"
        )
    return candidate


def _require_empty_new_shadow(shadow_root: Path, *, lock_path: Path) -> None:
    if not shadow_root.exists() and not shadow_root.is_symlink():
        return
    try:
        mode = os.lstat(shadow_root).st_mode
        if not stat.S_ISDIR(mode) or stat.S_ISLNK(mode):
            raise ActiveTransitionIntegrityError(
                "Candidate shadow runtime root is not a real directory"
            )
        entries = tuple(sorted(shadow_root.iterdir(), key=lambda path: path.name))
        lock_mode = os.lstat(lock_path).st_mode
        if (
            entries != (lock_path,)
            or not stat.S_ISREG(lock_mode)
            or stat.S_ISLNK(lock_mode)
        ):
            raise ActiveTransitionIntegrityError(
                "Candidate shadow runtime root contains data before transition"
            )
    except OSError as exc:
        raise ActiveTransitionIntegrityError(
            "Cannot inspect candidate shadow runtime root"
        ) from exc


def _adapter_sha256(value: str | None = None) -> str | None:
    if value is not None:
        return _hash(value, name="scheduler adapter SHA-256")
    if not SCHEDULER_ADAPTER_PATH.exists() and not SCHEDULER_ADAPTER_PATH.is_symlink():
        return None
    try:
        return registry._read_regular(  # noqa: SLF001
            SCHEDULER_ADAPTER_PATH, name="cycle-v4 scheduler adapter"
        ).sha256
    except registry.EpochRegistryError as exc:
        raise ActiveTransitionIntegrityError(str(exc)) from exc


def _event_static_payload(
    paths: ActiveTransitionPaths,
    bounded: _CompletionEvidence,
    authority: _PreparedAuthority,
    scheduler_adapter_sha256: str,
) -> dict[str, object]:
    _require_candidate_binding(bounded, authority)
    runtime_root, shadow_root = _runtime_roots(authority)
    build = authority.candidate_receipt["candidate_build"]
    payload: dict[str, object] = {
        "schema_version": EVENT_SCHEMA_VERSION,
        "sequence_id": 1,
        "previous_entry_sha256": ZERO_HASH,
        "event_type": EVENT_TYPE,
        "authority_scope": "official_machine_scheduler_lifecycle",
        "candidate_id": bounded.payload["candidate_id"],
        "slot_id": bounded.payload["slot_id"],
        "old_live_epoch_id": bounded.payload["old_live_epoch_id"],
        "new_live_epoch_id": build["live_epoch_id"],
        "old_official_scheduler_state": "SEALED",
        "new_official_scheduler_state": "ACTIVE",
        "active_state_semantics": (
            "authorized_for_machine_genesis_initialization_not_initialized"
        ),
        "registry_event_sequence_id": authority.registry_event["sequence_id"],
        "registry_event_entry_sha256": authority.registry_event["entry_sha256"],
        "preparation_event_sequence_id": authority.preparation_event["sequence_id"],
        "preparation_event_entry_sha256": authority.preparation_event["entry_sha256"],
        "bounded_drain_completion_event": _artifact_reference(
            bounded.snapshot, paths.registry_root
        ),
        "bounded_completion_fresh_live_event_count": bounded.payload[
            "fresh_live_event_count"
        ],
        "bounded_completion_fresh_live_terminal_sha256": bounded.payload[
            "fresh_live_terminal_sha256"
        ],
        "runtime_root": str(runtime_root),
        "shadow_runtime_root": str(shadow_root),
        "executable_tree_root": str(authority.executable_tree_root),
        "executable_tree_sha256": authority.executable_capsule["tree_sha256"],
        "cycle_script": str(authority.cycle_script.path),
        "cycle_script_sha256": authority.cycle_script.sha256,
        "cycle_script_size_bytes": authority.cycle_script.size_bytes,
        "cycle_config": str(authority.cycle_config.path),
        "cycle_config_sha256": authority.cycle_config.sha256,
        "cycle_config_size_bytes": authority.cycle_config.size_bytes,
        "scheduler_adapter_path": SCHEDULER_ADAPTER_LOGICAL_PATH,
        "scheduler_adapter_sha256": scheduler_adapter_sha256,
    }
    payload.update({claim: True for claim in TRUE_CLAIMS})
    payload.update({claim: False for claim in FALSE_CLAIMS})
    return payload


def _read_transition_event(
    paths: ActiveTransitionPaths,
) -> tuple[dict[str, Any], registry.ArtifactSnapshot] | None:
    entries = _event_entries(paths)
    if not entries:
        return None
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            entries[0], name="active transition event"
        )
        payload = registry._decode_json(  # noqa: SLF001
            snapshot.raw, name="active transition event"
        )
    except registry.EpochRegistryError as exc:
        raise ActiveTransitionIntegrityError(str(exc)) from exc
    match = EVENT_NAME.fullmatch(snapshot.path.name)
    entry_sha256 = payload.get("entry_sha256")
    unsigned = dict(payload)
    unsigned.pop("entry_sha256", None)
    if (
        snapshot.raw != _canonical_bytes(payload)
        or match is None
        or match.group("digest") != entry_sha256
        or entry_sha256 != _sha256(_canonical_bytes(unsigned))
        or payload.get("schema_version") != EVENT_SCHEMA_VERSION
        or payload.get("sequence_id") != 1
        or payload.get("previous_entry_sha256") != ZERO_HASH
        or payload.get("event_type") != EVENT_TYPE
        or payload.get("authority_scope") != "official_machine_scheduler_lifecycle"
        or payload.get("old_official_scheduler_state") != "SEALED"
        or payload.get("new_official_scheduler_state") != "ACTIVE"
        or payload.get("active_state_semantics")
        != "authorized_for_machine_genesis_initialization_not_initialized"
        or any(payload.get(claim) is not True for claim in TRUE_CLAIMS)
        or any(payload.get(claim) is not False for claim in FALSE_CLAIMS)
    ):
        raise ActiveTransitionIntegrityError("Active transition event contract changed")
    _hash(entry_sha256, name="active transition entry SHA-256")
    _hash(
        payload.get("scheduler_adapter_sha256"),
        name="scheduler adapter SHA-256",
    )
    registry._utc_timestamp(  # noqa: SLF001
        payload.get("recorded_at_utc"), name="active transition event time"
    )
    return dict(payload), snapshot


def _replay_authorization(
    paths: ActiveTransitionPaths,
    *,
    scheduler_adapter_sha256: str,
    load_historical_authority: LoadHistoricalAuthority,
) -> SchedulerAuthorization:
    loaded = _read_transition_event(paths)
    if loaded is None:
        raise ActiveTransitionNotReadyError(
            "Active transition event has not been published"
        )
    payload, snapshot = loaded
    if payload["scheduler_adapter_sha256"] != scheduler_adapter_sha256:
        raise ActiveTransitionIntegrityError(
            "Cycle-v4 scheduler adapter differs from transition authority"
        )
    bounded = _load_referenced_completion(paths, payload)
    authority = load_historical_authority(
        paths,
        _hash(
            payload.get("registry_event_entry_sha256"),
            name="registry event SHA-256",
        ),
        _hash(
            payload.get("preparation_event_entry_sha256"),
            name="preparation event SHA-256",
        ),
    )
    expected = _event_static_payload(
        paths, bounded, authority, scheduler_adapter_sha256
    )
    body = dict(payload)
    body.pop("recorded_at_utc", None)
    body.pop("entry_sha256", None)
    if body != expected:
        raise ActiveTransitionIntegrityError(
            "Active transition differs from historical R1/R2a/completion authority"
        )
    runtime_root, shadow_root = _runtime_roots(authority)
    return SchedulerAuthorization(
        transition_entry_sha256=str(payload["entry_sha256"]),
        transition_entry_path=snapshot.path,
        candidate_id=str(payload["candidate_id"]),
        slot_id=str(payload["slot_id"]),
        old_live_epoch_id=str(payload["old_live_epoch_id"]),
        new_live_epoch_id=str(payload["new_live_epoch_id"]),
        registry_root=paths.registry_root,
        runtime_root=runtime_root,
        shadow_runtime_root=shadow_root,
        executable_tree_root=authority.executable_tree_root,
        cycle_script=authority.cycle_script.path,
        cycle_config=authority.cycle_config.path,
        scheduler_adapter_sha256=scheduler_adapter_sha256,
    )


def _publish_event(
    paths: ActiveTransitionPaths,
    expected_static: Mapping[str, object],
    now: datetime,
) -> registry.ArtifactSnapshot:
    body = {**dict(expected_static), "recorded_at_utc": _utc_text(now)}
    payload = {**body, "entry_sha256": _sha256(_canonical_bytes(body))}
    path = paths.events / f"{1:020d}-{payload['entry_sha256']}.json"
    try:
        return drain._publish_once_durable(  # noqa: SLF001
            path,
            _canonical_bytes(payload),
            root=paths.root,
            name="active transition event",
        )
    except drain.EpochDrainError as exc:
        raise ActiveTransitionIntegrityError(str(exc)) from exc


def _finish(
    paths: ActiveTransitionPaths,
    now: datetime,
    *,
    status: str,
    reason: str,
    event: registry.ArtifactSnapshot | None = None,
    candidate_id: str | None = None,
    old_live_epoch_id: str | None = None,
    new_live_epoch_id: str | None = None,
) -> ActiveTransitionResult:
    active = event is not None
    payload = {
        "schema_version": STATUS_SCHEMA_VERSION,
        "observed_at_utc": _utc_text(now),
        "status": status,
        "reason": reason,
        "event_path": (
            event.path.relative_to(paths.root).as_posix() if event else None
        ),
        "candidate_id": candidate_id,
        "old_live_epoch_id": old_live_epoch_id,
        "new_live_epoch_id": new_live_epoch_id,
        "old_official_scheduler_state": "SEALED" if active else None,
        "new_official_scheduler_state": "ACTIVE" if active else None,
        "cache_authority": False,
    }
    payload.update(
        {claim: True if claim == "machine_only" else active for claim in TRUE_CLAIMS}
    )
    payload.update({claim: False for claim in FALSE_CLAIMS})
    try:
        registry._atomic_cache(  # noqa: SLF001
            paths.status,
            _canonical_bytes(payload),
            root=paths.root,
            name="active transition status",
        )
    except registry.EpochRegistryError as exc:
        raise ActiveTransitionIntegrityError(str(exc)) from exc
    return ActiveTransitionResult(
        status=status,
        reason=reason,
        status_path=paths.status,
        event_path=event.path if event else None,
        active_epoch_switch_implemented=active,
        scheduler_entrypoint_authorization_implemented=active,
    )


def _coordinate_epoch_active_transition(
    *,
    runtime_root: Path | None = None,
    active_runtime_root: Path | None = None,
    shadow_runtime_root: Path | None = None,
    clock: Callable[[], datetime] | None = None,
    load_current_completion: LoadCurrentCompletion | None = None,
    load_current_authority: LoadCurrentAuthority | None = None,
    load_historical_authority: LoadHistoricalAuthority | None = None,
    scheduler_adapter_sha256: str | None = None,
) -> ActiveTransitionResult:
    """Publish or replay the one-event scheduler lifecycle transition."""

    paths = active_transition_paths(
        runtime_root=runtime_root,
        active_runtime_root=active_runtime_root,
        shadow_runtime_root=shadow_runtime_root,
    )
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    _utc_text(now)
    handles: Sequence[BinaryIO] = ()
    candidate_handles: list[BinaryIO] = []
    try:
        try:
            handles = completion.closure.current.coverage._acquire_locks(  # noqa: SLF001
                paths.completion.closure.current.coverage
            )
        except completion.closure.current.coverage.SourceDerivedEffectiveOutcomeTerminalCoverageBusyError as exc:
            raise ActiveTransitionBusyError(str(exc)) from exc
        except completion.closure.current.coverage.SourceDerivedEffectiveOutcomeTerminalCoverageError as exc:
            raise ActiveTransitionIntegrityError(str(exc)) from exc

        adapter_sha = _adapter_sha256(scheduler_adapter_sha256)
        existing = _read_transition_event(paths)
        historical_loader = load_historical_authority or _default_historical_authority
        if existing is not None:
            if adapter_sha is None:
                raise ActiveTransitionIntegrityError(
                    "Transition-bound cycle-v4 scheduler adapter is absent"
                )
            authorization = _replay_authorization(
                paths,
                scheduler_adapter_sha256=adapter_sha,
                load_historical_authority=historical_loader,
            )
            return _finish(
                paths,
                now,
                status="active_transition_current",
                reason="atomic scheduler lifecycle transition is current",
                event=existing[1],
                candidate_id=authorization.candidate_id,
                old_live_epoch_id=authorization.old_live_epoch_id,
                new_live_epoch_id=authorization.new_live_epoch_id,
            )

        bounded = (load_current_completion or _default_current_completion)(paths, now)
        if bounded is None:
            return _finish(
                paths,
                now,
                status="waiting_for_bounded_drain_completion",
                reason="scoped V2 bounded-drain completion event is pending",
            )
        if adapter_sha is None:
            return _finish(
                paths,
                now,
                status="waiting_for_scheduler_adapter",
                reason="cycle-v4 scheduler authorization adapter is pending",
                candidate_id=str(bounded.payload["candidate_id"]),
                old_live_epoch_id=str(bounded.payload["old_live_epoch_id"]),
            )
        candidate_paths = _candidate_drain_paths(paths, bounded)
        try:
            for label, path in (
                ("candidate cycle", candidate_paths.cycle_lock),
                ("candidate deploy", candidate_paths.deploy_lock),
                ("candidate runner", candidate_paths.runner_lock),
                ("candidate replay", candidate_paths.replay_lock),
                ("candidate shadow", candidate_paths.shadow_lock),
            ):
                candidate_handles.append(drain._acquire_lock(path, label=label))  # noqa: SLF001
        except drain.EpochDrainBusyError as exc:
            raise ActiveTransitionBusyError(str(exc)) from exc
        except drain.EpochDrainError as exc:
            raise ActiveTransitionIntegrityError(str(exc)) from exc
        authority = (load_current_authority or _default_current_authority)(
            paths, bounded
        )
        if authority is None:
            return _finish(
                paths,
                now,
                status="waiting_for_prepared_candidate",
                reason="exact R1/R2a prepared candidate authority is pending",
                candidate_id=str(bounded.payload["candidate_id"]),
                old_live_epoch_id=str(bounded.payload["old_live_epoch_id"]),
            )
        _require_candidate_binding(bounded, authority)
        runtime_root, shadow_root = _runtime_roots(authority)
        if (
            runtime_root != candidate_paths.active_root
            or shadow_root != candidate_paths.shadow_root
        ):
            raise ActiveTransitionIntegrityError(
                "Completion-derived writer locks differ from prepared candidate roots"
            )
        _require_empty_new_shadow(shadow_root, lock_path=candidate_paths.shadow_lock)
        expected = _event_static_payload(paths, bounded, authority, adapter_sha)
        event = _publish_event(paths, expected, now)
        loaded = _read_transition_event(paths)
        if loaded is None or loaded[1] != event:
            raise ActiveTransitionIntegrityError(
                "Active transition event did not replay after publication"
            )
        return _finish(
            paths,
            now,
            status="active_transition_committed",
            reason="old scheduler sealed and prepared epoch activated atomically",
            event=event,
            candidate_id=str(expected["candidate_id"]),
            old_live_epoch_id=str(expected["old_live_epoch_id"]),
            new_live_epoch_id=str(expected["new_live_epoch_id"]),
        )
    except ActiveTransitionError:
        raise
    except (
        completion.BoundedDrainCompletionError,
        admission_cut.AdmissionCutError,
        manifest.WorksetManifestError,
        drain.EpochDrainError,
        registry.EpochRegistryError,
        preparation.EpochPreparationError,
    ) as exc:
        raise ActiveTransitionIntegrityError(str(exc)) from exc
    finally:
        active_exception = sys.exc_info()[1]
        release_error: ActiveTransitionIntegrityError | None = None
        if candidate_handles:
            try:
                drain._release_locks(candidate_handles)  # noqa: SLF001
            except drain.EpochDrainError as exc:
                release_error = ActiveTransitionIntegrityError(str(exc))
                release_error.__cause__ = exc
        if handles:
            try:
                completion.closure.current.coverage._release_locks(handles)  # noqa: SLF001
            except completion.closure.current.coverage.SourceDerivedEffectiveOutcomeTerminalCoverageError as exc:
                old_release_error = ActiveTransitionIntegrityError(str(exc))
                old_release_error.__cause__ = exc
                if release_error is None:
                    release_error = old_release_error
        if release_error is not None and active_exception is None:
            raise release_error


def coordinate_epoch_active_transition() -> ActiveTransitionResult:
    """Run one machine-only lifecycle transition poll."""

    return _coordinate_epoch_active_transition()


@contextmanager
def _scheduler_authorization_lease(
    *,
    runtime_root: Path | None = None,
    active_runtime_root: Path | None = None,
    shadow_runtime_root: Path | None = None,
    load_historical_authority: LoadHistoricalAuthority | None = None,
    scheduler_adapter_sha256: str | None = None,
) -> Iterator[SchedulerAuthorization]:
    paths = active_transition_paths(
        runtime_root=runtime_root,
        active_runtime_root=active_runtime_root,
        shadow_runtime_root=shadow_runtime_root,
    )
    handle: BinaryIO | None = None
    try:
        try:
            handle = drain._acquire_lock(  # noqa: SLF001
                paths.manager_lock, label="epoch manager"
            )
        except drain.EpochDrainBusyError as exc:
            raise ActiveTransitionBusyError(str(exc)) from exc
        if not _event_entries(paths):
            raise ActiveTransitionNotReadyError(
                "Active transition event has not been published"
            )
        adapter_sha = _adapter_sha256(scheduler_adapter_sha256)
        if adapter_sha is None:
            raise ActiveTransitionIntegrityError(
                "Transition-bound cycle-v4 scheduler adapter is absent"
            )
        yield _replay_authorization(
            paths,
            scheduler_adapter_sha256=adapter_sha,
            load_historical_authority=(
                load_historical_authority or _default_historical_authority
            ),
        )
    except ActiveTransitionError:
        raise
    except (drain.EpochDrainError, registry.EpochRegistryError) as exc:
        raise ActiveTransitionIntegrityError(str(exc)) from exc
    finally:
        if handle is not None:
            try:
                drain._release_locks((handle,))  # noqa: SLF001
            except drain.EpochDrainError as exc:
                raise ActiveTransitionIntegrityError(str(exc)) from exc


@contextmanager
def scheduler_authorization_lease() -> Iterator[SchedulerAuthorization]:
    """Hold the manager lock while yielding the authoritative scheduler grant."""

    with _scheduler_authorization_lease() as authorization:
        yield authorization


def main() -> int:
    try:
        result = coordinate_epoch_active_transition()
    except ActiveTransitionBusyError as exc:
        print(json.dumps({"status": "busy", "reason": str(exc)}, sort_keys=True))
        return 3
    except ActiveTransitionError as exc:
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
                "active_epoch_switch_implemented": (
                    result.active_epoch_switch_implemented
                ),
                "scheduler_entrypoint_authorization_implemented": (
                    result.scheduler_entrypoint_authorization_implemented
                ),
                "old_epoch_drained": False,
                "new_epoch_genesis_initialized": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
