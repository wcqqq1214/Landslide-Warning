"""Publish a source-derived effective-workset overlay.

The overlay consumes only a fully published cross-freeze completion and its
exact source-derived D/R/I reservation.  It never rewrites the frozen manifest
or recovery-v6 authority: it deterministically adds D, replaces R, removes I,
rebuilds the effective DAG, and publishes a compact content-addressed proof.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, BinaryIO


ROOT = Path(__file__).resolve().parents[2]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_epoch_drain as drain  # noqa: E402
from monitoring import ootang_epoch_registry as registry  # noqa: E402
from monitoring import (  # noqa: E402
    ootang_epoch_source_ingest_cross_freeze as cross,
)
from monitoring import (  # noqa: E402
    ootang_epoch_source_ingest_derived_reservation as derived,
)
from monitoring import ootang_epoch_workset_manifest as manifest  # noqa: E402
from monitoring import ootang_epoch_workset_recovery as recovery  # noqa: E402
from monitoring import ootang_live_source as source  # noqa: E402


DEFAULT_CONFIG_PATH = (
    ROOT / "config" / "ootang_epoch_source_derived_workset_overlay.v1.json"
)
DEFAULT_CONFIG_SHA256 = (
    "d80504e58393d58f284665ed471f19e51b09e847153df7f4143ab99bdf70e8d3"
)
IMPLEMENTATION_LOGICAL_PATH = (
    "code/monitoring/ootang_epoch_source_derived_workset_overlay.py"
)
MAX_CONTROL_BYTES = 4 * 1024 * 1024
ZERO_HASH = "0" * 64
LOCK_ORDER = recovery.LOCK_ORDER
OBJECT_NAME = re.compile(r"^(?P<digest>[0-9a-f]{64})\.json$")
EVENT_NAME = re.compile(r"^(?P<sequence>[0-9]{20})-(?P<entry>[0-9a-f]{64})\.json$")

EXPECTED_UPSTREAM = {
    "manifest_profile": {
        "path": "config/ootang_epoch_workset_manifest.v1.json",
        "expected_sha256": manifest.DEFAULT_CONFIG_SHA256,
    },
    "manifest_implementation": {
        "path": "code/monitoring/ootang_epoch_workset_manifest.py",
        "expected_sha256": "0ca331c6827cd896b4c3261792eed5f933e104d4c40c4fbf3b7e416e9b1fd424",
    },
    "recovery_profile": {
        "path": "config/ootang_epoch_workset_recovery.v1.json",
        "expected_sha256": recovery.DEFAULT_CONFIG_SHA256,
    },
    "recovery_implementation": {
        "path": "code/monitoring/ootang_epoch_workset_recovery.py",
        "expected_sha256": "b9f25133eef9cb94fb255bab588edcd573f0fa792ef82c4ddb9068d0a44a6c51",
    },
    "source_profile": {
        "path": "config/ootang_prequential_deploy.v1.json",
        "expected_sha256": "60f17602998e976f06d590b7611dfb4480505c21d41a9b05420bd93cf831f940",
    },
    "source_implementation": {
        "path": "code/monitoring/ootang_live_source.py",
        "expected_sha256": "7513a214a563a7f26c345c855748f28643df88685ae1d0006f84d8c339c85110",
    },
    "derived_profile": {
        "path": "config/ootang_epoch_source_ingest_derived_reservation.v1.json",
        "expected_sha256": derived.DEFAULT_CONFIG_SHA256,
    },
    "derived_implementation": {
        "path": "code/monitoring/ootang_epoch_source_ingest_derived_reservation.py",
        "expected_sha256": "1d620fbe20d936a27f55d1e03204d87be3ff8d94a892b2f9c966c061e2fa59d2",
    },
    "cross_freeze_profile": {
        "path": "config/ootang_epoch_source_ingest_cross_freeze.v1.json",
        "expected_sha256": cross.DEFAULT_CONFIG_SHA256,
    },
    "cross_freeze_implementation": {
        "path": "code/monitoring/ootang_epoch_source_ingest_cross_freeze.py",
        "expected_sha256": "14b975d4198716d0699ae80925ed907f431454243e2d47899a3e6fa9896e25f4",
    },
}
EXPECTED_RUNTIME = {
    "registry_root": "runtime/ootang_epoch_registry_v1",
    "recovery_namespace": "workset_recovery_v1",
    "namespace": "source_derived_workset_overlay_v1",
    "overlay_objects": "overlays/sha256",
    "events": "events",
    "status": "status.json",
    "manager_lock": "manager.lock",
    "active_root": "runtime/ootang_prequential_live_v1",
    "shadow_root": "runtime/ootang_prequential_calibration_shadow_v1",
}
EXPECTED_PROTOCOL = {
    "overlay_schema_version": "ootang_epoch_source_derived_workset_overlay_v1",
    "event_schema_version": "ootang_epoch_source_derived_workset_overlay_event_v1",
    "status_schema_version": "ootang_epoch_source_derived_workset_overlay_status_v1",
    "event_type": "epoch_source_derived_effective_workset_published",
    "canonical_json": "utf8_sort_keys_compact_no_nan_trailing_lf",
    "initial_previous_entry_sha256": ZERO_HASH,
    "maximum_items": 4096,
    "maximum_control_bytes": MAX_CONTROL_BYTES,
    "surviving_lock_order": list(LOCK_ORDER),
    "application_policy": "effective_equals_base_minus_i_with_whole_row_r_replacement_plus_d",
    "poll_policy": "publish_or_forward_adopt_one_exact_cross_freeze_effective_workset_overlay",
}
TRUE_CAPABILITIES = (
    "machine_only",
    "frozen_manifest_deep_verified",
    "source_derived_dri_deep_verified",
    "cross_freeze_completion_deep_verified",
    "effective_workset_overlay_implemented",
    "derived_future_work_reservation_implemented",
    "effective_dependency_graph_rebuilt",
    "create_only_content_addressed_overlays_implemented",
    "append_only_overlay_events_implemented",
    "crash_forward_adoption_implemented",
)
FALSE_CLAIMS = (
    "frozen_manifest_mutated",
    "recovery_v6_mutated",
    "source_derived_reservation_mutated",
    "cross_freeze_authority_mutated",
    "source_ingest_executed",
    "outcome_materialization_performed",
    "outcome_or_revision_consumed",
    "source_parent_terminal",
    "effective_items_terminal",
    "terminal_transition_closure_implemented",
    "bounded_workset_recovery_implemented",
    "all_content_dependent_lanes_reserved",
    "all_reserved_items_settled",
    "all_reserved_successors_supported",
    "lifecycle_authority",
    "transition_authority",
    "drained_eligibility_current",
    "old_epoch_drained",
    "active_epoch_switch_implemented",
    "automatic_epoch_rotation_implemented",
    "trusted_anchor_receipt_verified",
    "e2_live_evidence_eligible",
    "real_activation_ready",
    "formal_warning_output",
)


class SourceDerivedWorksetOverlayError(RuntimeError):
    """Base error for effective-workset overlay publication."""


class SourceDerivedWorksetOverlayConfigError(SourceDerivedWorksetOverlayError):
    """The reviewed profile or a direct pinned dependency changed."""


class SourceDerivedWorksetOverlayIntegrityError(SourceDerivedWorksetOverlayError):
    """An upstream authority, transform, or persisted overlay failed closed."""


class SourceDerivedWorksetOverlayBusyError(SourceDerivedWorksetOverlayError):
    """One of the surviving coordinator locks is owned elsewhere."""


@dataclass(frozen=True)
class SourceDerivedWorksetOverlayPaths:
    registry_root: Path
    root: Path
    overlay_objects: Path
    events: Path
    status: Path
    manager_lock: Path
    active_root: Path
    shadow_root: Path
    cycle_lock: Path
    replay_lock: Path
    shadow_lock: Path
    recovery: recovery.RecoveryPaths
    derived: derived.SourceIngestDerivedPaths
    cross: cross.SourceIngestCrossFreezePaths


@dataclass(frozen=True)
class SourceDerivedWorksetOverlayResult:
    status: str
    reason: str
    status_path: Path
    overlay_path: Path | None = None
    event_path: Path | None = None
    slot_id: str | None = None
    source_key_id: str | None = None
    base_item_count: int = 0
    effective_item_count: int = 0
    derived_added_count: int = 0
    rebound_replaced_count: int = 0
    invalidated_superseded_count: int = 0
    effective_workset_keyset_sha256: str | None = None
    effective_item_identity_set_sha256: str | None = None
    effective_dependency_graph_sha256: str | None = None
    derived_future_work_reservation_implemented: bool = False
    outcome_materialization_performed: bool = False


@dataclass(frozen=True)
class SourceDerivedWorksetOverlayComputation:
    reservation: recovery.Reservation
    cross_authority: cross.SourceIngestCrossFreezeAuthority
    derived_evidence: Any
    cross_receipt_payload: Mapping[str, Any]
    cross_receipt_snapshot: registry.ArtifactSnapshot
    cross_event_payload: Mapping[str, Any]
    cross_event_snapshot: registry.ArtifactSnapshot
    effective_items: tuple[Mapping[str, Any], ...]
    effective_ids: Mapping[str, str]
    slot_id: str
    application: Mapping[str, Any]
    effective_workset: Mapping[str, Any]


FaultHook = Callable[[str], None]
LoadDerivedAuthority = cross.LoadDerivedAuthority


def _canonical_bytes(value: object) -> bytes:
    try:
        return registry._canonical_bytes(value)  # type: ignore[arg-type]  # noqa: SLF001
    except (TypeError, ValueError) as exc:
        raise SourceDerivedWorksetOverlayIntegrityError(
            "Value is not canonical JSON"
        ) from exc


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _claims() -> dict[str, bool]:
    return {
        **{name: True for name in TRUE_CAPABILITIES},
        **{name: False for name in FALSE_CLAIMS},
    }


def _exact(value: object, keys: set[str], *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise SourceDerivedWorksetOverlayIntegrityError(f"{name} keys changed")
    return value


def _text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise SourceDerivedWorksetOverlayIntegrityError(f"{name} changed")
    return value


def _hash(value: object, *, name: str) -> str:
    result = _text(value, name=name)
    if len(result) != 64 or any(char not in "0123456789abcdef" for char in result):
        raise SourceDerivedWorksetOverlayIntegrityError(
            f"{name} is not a lowercase SHA-256"
        )
    return result


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SourceDerivedWorksetOverlayIntegrityError(
            "Effective-overlay clock must be timezone-aware"
        )
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: object, *, name: str) -> datetime:
    try:
        return registry._utc_timestamp(value, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise SourceDerivedWorksetOverlayIntegrityError(str(exc)) from exc


def _child(root: Path, relative: str, *, name: str) -> Path:
    try:
        return registry._contained(root, relative, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise SourceDerivedWorksetOverlayConfigError(str(exc)) from exc


def _strict_json(
    path: Path, *, name: str
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    try:
        return manifest._read_json(  # noqa: SLF001
            path, name=name, maximum_bytes=MAX_CONTROL_BYTES
        )
    except manifest.WorksetManifestError as exc:
        raise SourceDerivedWorksetOverlayIntegrityError(str(exc)) from exc


def _strict_entries(directory: Path, *, name: str) -> tuple[Path, ...]:
    try:
        return manifest._strict_entries(directory, name=name)  # noqa: SLF001
    except manifest.WorksetManifestError as exc:
        raise SourceDerivedWorksetOverlayIntegrityError(str(exc)) from exc


def _publish(
    path: Path, raw: bytes, *, root: Path, name: str
) -> registry.ArtifactSnapshot:
    if len(raw) > MAX_CONTROL_BYTES:
        raise SourceDerivedWorksetOverlayIntegrityError(f"{name} capacity exceeded")
    try:
        return drain._publish_once_durable(  # noqa: SLF001
            path, raw, root=root, name=name
        )
    except drain.EpochDrainError as exc:
        raise SourceDerivedWorksetOverlayIntegrityError(str(exc)) from exc


def _reference(snapshot: registry.ArtifactSnapshot, root: Path) -> dict[str, object]:
    try:
        relative = snapshot.path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise SourceDerivedWorksetOverlayIntegrityError(
            "Referenced artifact escaped its authority root"
        ) from exc
    return {
        "path": relative,
        "sha256": snapshot.sha256,
        "size_bytes": snapshot.size_bytes,
    }


def _implementation_reference() -> dict[str, object]:
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            Path(__file__),
            name="effective-overlay implementation",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
    except registry.EpochRegistryError as exc:
        raise SourceDerivedWorksetOverlayIntegrityError(str(exc)) from exc
    return {
        "path": IMPLEMENTATION_LOGICAL_PATH,
        "sha256": snapshot.sha256,
        "size_bytes": snapshot.size_bytes,
    }


def load_source_derived_workset_overlay_profile(
    path: Path = DEFAULT_CONFIG_PATH, *, project_root: Path = ROOT
) -> dict[str, Any]:
    root = project_root.resolve()
    resolved = path if path.is_absolute() else root / path
    if root != ROOT.resolve() or resolved.resolve() != DEFAULT_CONFIG_PATH.resolve():
        raise SourceDerivedWorksetOverlayConfigError(
            "Only the reviewed default effective-overlay profile is accepted"
        )
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            resolved, name="effective-overlay profile", maximum_bytes=MAX_CONTROL_BYTES
        )
        profile = registry._decode_json(  # noqa: SLF001
            snapshot.raw, name="effective-overlay profile"
        )
    except registry.EpochRegistryError as exc:
        raise SourceDerivedWorksetOverlayConfigError(str(exc)) from exc
    if snapshot.sha256 != DEFAULT_CONFIG_SHA256:
        raise SourceDerivedWorksetOverlayConfigError(
            "Effective-overlay profile SHA-256 changed"
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
            "default_pipeline_member",
            "upstream",
            "runtime",
            "protocol",
            "engineering_capabilities",
        },
        name="effective-overlay profile",
    )
    if (
        profile["schema_version"]
        != "ootang_epoch_source_derived_workset_overlay_profile_v1"
        or profile["profile_id"] != "ootang-epoch-source-derived-workset-overlay-v1"
        or profile["case"] != "ootang"
        or profile["formal_warning_output"] is not False
        or profile["default_pipeline_member"] is not False
        or profile["upstream"] != EXPECTED_UPSTREAM
        or profile["runtime"] != EXPECTED_RUNTIME
        or profile["protocol"] != EXPECTED_PROTOCOL
        or profile["engineering_capabilities"] != _claims()
    ):
        raise SourceDerivedWorksetOverlayConfigError(
            "Effective-overlay profile semantics changed"
        )
    for binding in EXPECTED_UPSTREAM.values():
        upstream = _child(root, binding["path"], name="effective-overlay upstream")
        try:
            actual = registry._read_regular(  # noqa: SLF001
                upstream,
                name="effective-overlay upstream",
                maximum_bytes=64 * 1024 * 1024,
            )
        except registry.EpochRegistryError as exc:
            raise SourceDerivedWorksetOverlayConfigError(str(exc)) from exc
        if actual.sha256 != binding["expected_sha256"]:
            raise SourceDerivedWorksetOverlayConfigError(
                f"Pinned effective-overlay upstream changed:{binding['path']}"
            )
    profile["_profile_sha256"] = snapshot.sha256
    return profile


def source_derived_workset_overlay_paths(
    profile: Mapping[str, Any],
    *,
    registry_root: Path | None = None,
    active_root: Path | None = None,
    shadow_root: Path | None = None,
) -> SourceDerivedWorksetOverlayPaths:
    registry_path = (
        registry_root or ROOT / profile["runtime"]["registry_root"]
    ).resolve()
    recovery_root = _child(
        registry_path,
        profile["runtime"]["recovery_namespace"],
        name="recovery namespace",
    )
    root = _child(
        recovery_root, profile["runtime"]["namespace"], name="effective-overlay root"
    )
    active = (active_root or ROOT / profile["runtime"]["active_root"]).resolve()
    shadow = (shadow_root or ROOT / profile["runtime"]["shadow_root"]).resolve()
    recovery_profile = recovery.load_workset_recovery_profile()
    recovery_paths = recovery.recovery_paths(
        recovery_profile,
        registry_root=registry_path,
        active_root=active,
        shadow_root=shadow,
    )
    derived_profile = derived.load_source_ingest_derived_profile()
    derived_paths = derived.source_ingest_derived_paths(
        derived_profile,
        registry_root=registry_path,
        active_root=active,
        shadow_root=shadow,
    )
    cross_profile = cross.load_source_ingest_cross_freeze_profile()
    cross_paths = cross.source_ingest_cross_freeze_paths(
        cross_profile,
        registry_root=registry_path,
        active_root=active,
        shadow_root=shadow,
    )
    return SourceDerivedWorksetOverlayPaths(
        registry_root=registry_path,
        root=root,
        overlay_objects=_child(
            root, profile["runtime"]["overlay_objects"], name="overlay objects"
        ),
        events=_child(root, profile["runtime"]["events"], name="overlay events"),
        status=_child(root, profile["runtime"]["status"], name="overlay status"),
        manager_lock=_child(
            registry_path, profile["runtime"]["manager_lock"], name="manager lock"
        ),
        active_root=active,
        shadow_root=shadow,
        cycle_lock=_child(active, "prequential_cycle.lock", name="cycle lock"),
        replay_lock=_child(active, "issue_replay.lock", name="replay lock"),
        shadow_lock=_child(shadow, "runner.lock", name="shadow lock"),
        recovery=recovery_paths,
        derived=derived_paths,
        cross=cross_paths,
    )


def _acquire_locks(paths: SourceDerivedWorksetOverlayPaths) -> list[BinaryIO]:
    handles: list[BinaryIO] = []
    try:
        for label, path in zip(
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
                handles.append(drain._acquire_lock(path, label=label))  # noqa: SLF001
            except drain.EpochDrainBusyError as exc:
                raise SourceDerivedWorksetOverlayBusyError(str(exc)) from exc
            except drain.EpochDrainError as exc:
                raise SourceDerivedWorksetOverlayIntegrityError(str(exc)) from exc
        return handles
    except SourceDerivedWorksetOverlayError:
        try:
            drain._release_locks(handles)  # noqa: SLF001
        except drain.EpochDrainError:
            pass
        raise


def _release_locks(handles: Sequence[BinaryIO]) -> None:
    try:
        drain._release_locks(handles)  # noqa: SLF001
    except drain.EpochDrainError as exc:
        raise SourceDerivedWorksetOverlayIntegrityError(str(exc)) from exc


def _assert_public_pointer_is_registry_tip(
    paths: SourceDerivedWorksetOverlayPaths,
) -> None:
    """Read-only gate preventing the cross loader from repairing a pointer."""

    _assert_public_pointer_root_is_registry_tip(paths.active_root)


def _assert_public_pointer_root_is_registry_tip(active_root: Path) -> None:
    """Require a regular public pointer equal to the receipt-registry tip."""

    source_profile = source.load_deploy_profile()
    pointer = source._runtime_path(  # noqa: SLF001
        source_profile, "current_source_pointer", root=active_root
    )
    objects = source._runtime_path(  # noqa: SLF001
        source_profile, "objects", root=active_root
    )
    try:
        state = source._load_snapshot_receipt_registry(  # noqa: SLF001
            objects, required=False
        )
    except Exception as exc:
        raise SourceDerivedWorksetOverlayIntegrityError(
            f"Source registry replay failed:{type(exc).__name__}:{exc}"
        ) from exc
    if state is None:
        if pointer.exists() or pointer.is_symlink():
            raise SourceDerivedWorksetOverlayIntegrityError(
                "Public source pointer exists without a receipt registry"
            )
        return
    if not pointer.exists() or pointer.is_symlink():
        raise SourceDerivedWorksetOverlayIntegrityError(
            "Overlay refuses to repair a missing or symlinked public source pointer"
        )
    try:
        public = registry._read_regular(  # noqa: SLF001
            pointer,
            name="effective-overlay public source pointer",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
    except registry.EpochRegistryError as exc:
        raise SourceDerivedWorksetOverlayIntegrityError(str(exc)) from exc
    if (
        public.sha256 != state.head_pointer.sha256
        or public.raw != state.head_pointer_raw
    ):
        raise SourceDerivedWorksetOverlayIntegrityError(
            "Public source pointer is not the registry tip; overlay will not repair it"
        )


def _load_cross_freeze_authority_read_only(
    profile: Mapping[str, Any],
    paths: cross.SourceIngestCrossFreezePaths,
    now: datetime,
    *,
    load_derived_authority: LoadDerivedAuthority | None = None,
) -> cross.SourceIngestCrossFreezeAuthority | None:
    """Replay cross-freeze authority without entering its pointer-repair path."""

    try:
        reservation = recovery._load_reservation(paths.recovery)  # noqa: SLF001
        if reservation is None:
            return None
        selected = cross._source_item(reservation)  # noqa: SLF001
        if selected is None:
            return None
        ordered, graph_digest, item = selected
        source_profile = source.load_deploy_profile()
        predecessor = cross._predecessor_source(  # noqa: SLF001
            source_profile, paths, item
        )
        incoming = cross._incoming_artifact(item)  # noqa: SLF001
        incoming_sha = cross._hash(  # noqa: SLF001
            item["authority"].get("incoming_feed_sha256"),
            name="incoming feed digest",
        )
        slot_id = _sha256(
            _canonical_bytes(
                {
                    "manifest_sha256": reservation.manifest_snapshot.sha256,
                    "source_key_id": item["key_id"],
                    "predecessor_snapshot_receipt_sha256": (
                        predecessor.snapshot_receipt.sha256
                    ),
                    "incoming_feed_sha256": incoming_sha,
                }
            )
        )
        prepares = cross._named_records(  # noqa: SLF001
            paths.prepares, name="read-only cross-freeze prepares"
        )
        intents = cross._named_records(  # noqa: SLF001
            paths.intents, name="read-only cross-freeze intents"
        )
        receipts = cross._named_records(  # noqa: SLF001
            paths.receipts, name="read-only cross-freeze receipts"
        )
        event_entries = cross._strict_entries(  # noqa: SLF001
            paths.events, name="read-only cross-freeze events"
        )
        if (
            set(prepares) - {slot_id}
            or set(intents) - {slot_id}
            or set(receipts) - {slot_id}
        ):
            raise SourceDerivedWorksetOverlayIntegrityError(
                "Cross-freeze namespace belongs to another frozen source slot"
            )
        if (receipts or event_entries) and slot_id not in intents:
            raise SourceDerivedWorksetOverlayIntegrityError(
                "Cross-freeze completion bytes lack their durable writer intent"
            )
        if slot_id in intents:
            prepared_feed = cross._read_frozen_feed(  # noqa: SLF001
                paths, item, incoming, None
            )
            provisional = cross.SourceIngestCrossFreezeAuthority(
                reservation=reservation,
                ordered_items=ordered,
                dependency_graph_sha256=graph_digest,
                source_item=item,
                source_profile=source_profile,
                predecessor_source=predecessor,
                current_source=predecessor,
                incoming_artifact=incoming,
                feed_snapshot=prepared_feed,
                slot_id=slot_id,
                derived_authority=None,
            )
            prepare = prepares.get(slot_id)
            if prepare is None or prepare[0] != cross._prepare_payload(  # noqa: SLF001
                profile, paths, provisional, prepared_feed
            ):
                raise SourceDerivedWorksetOverlayIntegrityError(
                    "Cross-freeze intent lacks its exact immutable prepare"
                )
            if intents[slot_id][0] != cross._intent_payload(  # noqa: SLF001
                profile, paths, provisional, prepare[1], prepared_feed
            ):
                raise SourceDerivedWorksetOverlayIntegrityError(
                    "Cross-freeze intent semantics changed"
                )
        current = source.load_current_source(
            source_profile, runtime_root=paths.active_root, project_root=ROOT
        )
        if current.snapshot_sequence_id < predecessor.snapshot_sequence_id:
            raise SourceDerivedWorksetOverlayIntegrityError(
                "Current source predates the frozen predecessor"
            )
        loader = (
            load_derived_authority or derived._load_source_ingest_authority  # noqa: SLF001
        )
        derived_authority = (
            loader(paths.derived, now)
            if current.snapshot_sequence_id > predecessor.snapshot_sequence_id
            else None
        )
        if derived_authority is not None and (
            derived_authority.source_item.get("key_id") != item.get("key_id")
            or derived_authority.predecessor_source.snapshot_receipt
            != predecessor.snapshot_receipt
        ):
            raise SourceDerivedWorksetOverlayIntegrityError(
                "Derived authority belongs to another frozen source slot"
            )
        if (
            current.snapshot_sequence_id > predecessor.snapshot_sequence_id
            and derived_authority is None
        ):
            raise SourceDerivedWorksetOverlayIntegrityError(
                "Advanced source has no exact historical N+1 derived authority"
            )
        feed_snapshot = cross._read_frozen_feed(  # noqa: SLF001
            paths, item, incoming, derived_authority
        )
        if feed_snapshot.size_bytes > 64 * 1024 * 1024:
            raise SourceDerivedWorksetOverlayIntegrityError(
                "Prepared feed capacity exceeded"
            )
        if feed_snapshot.sha256 != incoming_sha:
            raise SourceDerivedWorksetOverlayIntegrityError(
                "Frozen feed digest changed while loading cross-freeze authority"
            )
        authority = cross.SourceIngestCrossFreezeAuthority(
            reservation=reservation,
            ordered_items=ordered,
            dependency_graph_sha256=graph_digest,
            source_item=item,
            source_profile=source_profile,
            predecessor_source=predecessor,
            current_source=current,
            incoming_artifact=incoming,
            feed_snapshot=feed_snapshot,
            slot_id=slot_id,
            derived_authority=derived_authority,
        )
    except SourceDerivedWorksetOverlayError:
        raise
    except cross.SourceIngestCrossFreezeError as exc:
        raise SourceDerivedWorksetOverlayIntegrityError(str(exc)) from exc
    except Exception as exc:
        raise SourceDerivedWorksetOverlayIntegrityError(
            f"Read-only cross-freeze replay failed:{type(exc).__name__}:{exc}"
        ) from exc

    # A concurrent pointer loss/change after the source replay remains a
    # fail-closed observation.  This helper never calls the cross-freeze repair
    # primitive, even when a durable writer intent exists.
    _assert_public_pointer_root_is_registry_tip(paths.active_root)
    return authority


def _normalize_item(
    raw: Mapping[str, Any], manifest_sha256: str, *, name: str
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise SourceDerivedWorksetOverlayIntegrityError(f"{name} changed type")
    value = dict(raw)
    supplied_key_id = value.pop("key_id", None)
    try:
        parsed = manifest._item_from_payload(value)  # noqa: SLF001
        roundtrip = manifest._item_payload(parsed)  # noqa: SLF001
        expected_namespace = manifest._item_namespace_digest(  # noqa: SLF001
            family=parsed.family,
            natural_key=parsed.natural_key,
            successor=parsed.canonical_successor_state,
            dependencies=parsed.dependency_keys,
            artifacts=parsed.artifacts,
            authority=parsed.authority,
        )
    except manifest.WorksetManifestError as exc:
        raise SourceDerivedWorksetOverlayIntegrityError(str(exc)) from exc
    if roundtrip != value or parsed.namespace_digest != expected_namespace:
        raise SourceDerivedWorksetOverlayIntegrityError(
            f"{name} namespace or canonical row changed"
        )
    if (
        parsed.family not in manifest.FAMILIES
        or not parsed.natural_key.startswith(f"{parsed.family}:")
        or parsed.canonical_successor_state
        not in manifest.ALLOWED_SUCCESSORS[parsed.family]
        or tuple(sorted(parsed.dependency_keys)) != parsed.dependency_keys
        or len(set(parsed.dependency_keys)) != len(parsed.dependency_keys)
        or parsed.natural_key in parsed.dependency_keys
        or not parsed.artifacts
        or tuple(
            sorted(parsed.artifacts, key=lambda item: (item.root, item.path, item.role))
        )
        != parsed.artifacts
    ):
        raise SourceDerivedWorksetOverlayIntegrityError(
            f"{name} is not a canonical workset row"
        )
    result = dict(value)
    result["key_id"] = recovery._key_id(manifest_sha256, result)  # noqa: SLF001
    if supplied_key_id is not None and supplied_key_id != result["key_id"]:
        raise SourceDerivedWorksetOverlayIntegrityError(f"{name} key id changed")
    try:
        recovery._transition_plan(result)  # noqa: SLF001
    except recovery.WorksetRecoveryError as exc:
        raise SourceDerivedWorksetOverlayIntegrityError(str(exc)) from exc
    return result


def _require_source_outcome(item: Mapping[str, Any], *, name: str) -> None:
    authority = item.get("authority")
    if (
        item.get("family") != "outcome_revision"
        or item.get("canonical_successor_state") != "outcome_materialized"
        or not isinstance(authority, Mapping)
        or authority.get("record_type") != "machine_selected_source_outcome"
        or authority.get("action") != "outcome_materialized"
    ):
        raise SourceDerivedWorksetOverlayIntegrityError(
            f"{name} is not a machine-selected source outcome"
        )


def _identity(item: Mapping[str, Any]) -> dict[str, object]:
    return {
        "natural_key": item["natural_key"],
        "namespace_digest": item["namespace_digest"],
        "key_id": item["key_id"],
    }


def _effective_overlay(
    reservation: recovery.Reservation,
    *,
    derived_items: Sequence[Mapping[str, Any]],
    rebound_items: Sequence[Mapping[str, Any]],
    invalidated_items: Sequence[Mapping[str, Any]],
    maximum_items: int = 4096,
) -> tuple[
    tuple[Mapping[str, Any], ...],
    dict[str, str],
    dict[str, Any],
    dict[str, Any],
]:
    """Purely apply D/R/I and return ordered rows, ids, application, graph proof."""

    try:
        base_ordered, _, base_graph = recovery._dag(reservation)  # noqa: SLF001
    except recovery.WorksetRecoveryError as exc:
        raise SourceDerivedWorksetOverlayIntegrityError(str(exc)) from exc
    manifest_sha = reservation.manifest_snapshot.sha256
    base = [
        _normalize_item(item, manifest_sha, name=f"base item[{index}]")
        for index, item in enumerate(base_ordered)
    ]
    base_by_key = {str(item["natural_key"]): item for item in base}
    if len(base_by_key) != len(base):
        raise SourceDerivedWorksetOverlayIntegrityError(
            "Frozen base contains duplicate natural keys"
        )

    def normalize_set(
        values: Sequence[Mapping[str, Any]], label: str
    ) -> list[dict[str, Any]]:
        if len(values) > maximum_items:
            raise SourceDerivedWorksetOverlayIntegrityError(
                f"{label} capacity exceeded"
            )
        result = [
            _normalize_item(value, manifest_sha, name=f"{label}[{index}]")
            for index, value in enumerate(values)
        ]
        keys = [str(item["natural_key"]) for item in result]
        if len(set(keys)) != len(keys):
            raise SourceDerivedWorksetOverlayIntegrityError(
                f"{label} contains duplicate natural keys"
            )
        for index, item in enumerate(result):
            _require_source_outcome(item, name=f"{label}[{index}]")
        return result

    additions = normalize_set(derived_items, "derived additions")
    rebounds = normalize_set(rebound_items, "rebound replacements")
    invalidations = normalize_set(invalidated_items, "invalidated tombstones")
    d_keys = {str(item["natural_key"]) for item in additions}
    r_keys = {str(item["natural_key"]) for item in rebounds}
    i_keys = {str(item["natural_key"]) for item in invalidations}
    if d_keys & r_keys or d_keys & i_keys or r_keys & i_keys:
        raise SourceDerivedWorksetOverlayIntegrityError(
            "D/R/I natural-key sets are not pairwise disjoint"
        )
    if d_keys & set(base_by_key):
        raise SourceDerivedWorksetOverlayIntegrityError(
            "Derived addition already exists in the frozen base"
        )
    if not r_keys <= set(base_by_key) or not i_keys <= set(base_by_key):
        raise SourceDerivedWorksetOverlayIntegrityError(
            "R/I does not exactly identify frozen base rows"
        )
    for item in rebounds:
        old = base_by_key[str(item["natural_key"])]
        _require_source_outcome(old, name="rebound frozen row")
        if item["namespace_digest"] == old["namespace_digest"]:
            raise SourceDerivedWorksetOverlayIntegrityError(
                "Rebound replacement did not change the whole-row namespace"
            )
    for item in invalidations:
        old = base_by_key[str(item["natural_key"])]
        _require_source_outcome(old, name="invalidated frozen row")
        if dict(item) != dict(old):
            raise SourceDerivedWorksetOverlayIntegrityError(
                "Invalidated tombstone does not exactly match its frozen row"
            )

    effective_by_key = {
        key: item for key, item in base_by_key.items() if key not in i_keys
    }
    for item in rebounds:
        effective_by_key[str(item["natural_key"])] = item
    for item in additions:
        effective_by_key[str(item["natural_key"])] = item
    if len(effective_by_key) > maximum_items:
        raise SourceDerivedWorksetOverlayIntegrityError(
            "Effective workset capacity exceeded"
        )
    canonical = sorted(
        effective_by_key.values(),
        key=lambda item: (
            manifest.FAMILIES.index(str(item["family"])),
            str(item["natural_key"]),
        ),
    )
    ids = {str(item["natural_key"]): str(item["key_id"]) for item in canonical}
    for item in canonical:
        dependencies = item.get("dependency_keys")
        if not isinstance(dependencies, list):
            raise SourceDerivedWorksetOverlayIntegrityError(
                "Effective dependency list changed"
            )
        if (
            len(dependencies) != len(set(dependencies))
            or item["natural_key"] in dependencies
            or any(dependency not in ids for dependency in dependencies)
        ):
            raise SourceDerivedWorksetOverlayIntegrityError(
                "Effective graph has unknown, duplicate, or self dependencies"
            )
    remaining = {str(item["natural_key"]): item for item in canonical}
    position = {str(item["natural_key"]): index for index, item in enumerate(canonical)}
    emitted: set[str] = set()
    ordered: list[dict[str, Any]] = []
    while remaining:
        ready = sorted(
            (
                item
                for item in remaining.values()
                if set(item["dependency_keys"]) <= emitted
            ),
            key=lambda item: position[str(item["natural_key"])],
        )
        if not ready:
            raise SourceDerivedWorksetOverlayIntegrityError(
                "Effective dependency graph is cyclic"
            )
        for item in ready:
            ordered.append(item)
            emitted.add(str(item["natural_key"]))
            remaining.pop(str(item["natural_key"]))

    graph_rows: list[dict[str, object]] = []
    plan_rows: list[dict[str, object]] = []
    dependency_edges = 0
    for index, item in enumerate(ordered):
        plan = recovery._transition_plan(item)  # noqa: SLF001
        dependency_ids = [ids[str(value)] for value in item["dependency_keys"]]
        dependency_edges += len(dependency_ids)
        graph_rows.append(
            {
                "topological_index": index,
                "key_id": item["key_id"],
                "natural_key": item["natural_key"],
                "namespace_digest": item["namespace_digest"],
                "family": item["family"],
                "dependency_key_ids": dependency_ids,
                "successor": item["canonical_successor_state"],
                "transition_plan_sha256": plan["plan_sha256"],
            }
        )
        plan_rows.append({"key_id": item["key_id"], "plan_sha256": plan["plan_sha256"]})

    addition_rows = [_identity(item) for item in additions]
    replacement_rows = [
        {
            "natural_key": item["natural_key"],
            "old_namespace_digest": base_by_key[str(item["natural_key"])][
                "namespace_digest"
            ],
            "old_key_id": base_by_key[str(item["natural_key"])]["key_id"],
            "new_namespace_digest": item["namespace_digest"],
            "new_key_id": item["key_id"],
        }
        for item in rebounds
    ]
    tombstone_rows = [_identity(item) for item in invalidations]
    application = {
        "policy": EXPECTED_PROTOCOL["application_policy"],
        "base_item_count": len(base),
        "retained_base_item_count": len(base) - len(rebounds) - len(invalidations),
        "derived_added_count": len(additions),
        "rebound_replaced_count": len(rebounds),
        "invalidated_superseded_count": len(invalidations),
        "effective_item_count": len(ordered),
        "derived_additions_sha256": _sha256(_canonical_bytes(addition_rows)),
        "rebound_replacements_sha256": _sha256(_canonical_bytes(replacement_rows)),
        "invalidated_tombstones_sha256": _sha256(_canonical_bytes(tombstone_rows)),
    }
    effective_workset = {
        "item_count": len(ordered),
        "natural_keyset_sha256": _sha256(
            _canonical_bytes([item["natural_key"] for item in canonical])
        ),
        "item_identity_set_sha256": _sha256(
            _canonical_bytes([_identity(item) for item in canonical])
        ),
        "dependency_edge_count": dependency_edges,
        "dependency_graph_sha256": _sha256(_canonical_bytes(graph_rows)),
        "transition_plan_set_sha256": _sha256(_canonical_bytes(plan_rows)),
        "base_dependency_graph_sha256": base_graph,
    }
    return tuple(ordered), ids, application, effective_workset


def _load_computation(
    profile: Mapping[str, Any],
    paths: SourceDerivedWorksetOverlayPaths,
    now: datetime,
    *,
    load_derived_authority: LoadDerivedAuthority | None = None,
) -> tuple[SourceDerivedWorksetOverlayComputation | None, str]:
    _assert_public_pointer_is_registry_tip(paths)
    cross_profile = cross.load_source_ingest_cross_freeze_profile()
    base = _load_cross_freeze_authority_read_only(
        cross_profile,
        paths.cross,
        now,
        load_derived_authority=load_derived_authority,
    )
    if base is None:
        return None, "no frozen source-ingest workset item is available"
    try:
        evidence = cross._load_derived_evidence(  # noqa: SLF001
            paths.cross,
            now,
            base,
            load_derived_authority=load_derived_authority,
        )
    except cross.SourceIngestCrossFreezeError as exc:
        raise SourceDerivedWorksetOverlayIntegrityError(str(exc)) from exc
    if evidence is None:
        return None, "the exact source-derived reservation event is pending"
    prepares = cross._named_records(  # noqa: SLF001
        paths.cross.prepares, name="effective-overlay cross-freeze prepares"
    )
    intents = cross._named_records(  # noqa: SLF001
        paths.cross.intents, name="effective-overlay cross-freeze intents"
    )
    if set(prepares) != {base.slot_id} or set(intents) != {base.slot_id}:
        raise SourceDerivedWorksetOverlayIntegrityError(
            "Cross-freeze prepare/intent authority is incomplete or branched"
        )
    try:
        receipt, event = cross._completion_state(  # noqa: SLF001
            cross_profile,
            paths.cross,
            base,
            prepares[base.slot_id][1],
            intents[base.slot_id][1],
            evidence,
        )
    except cross.SourceIngestCrossFreezeError as exc:
        raise SourceDerivedWorksetOverlayIntegrityError(str(exc)) from exc
    if receipt is None or event is None:
        return None, "the exact cross-freeze completion receipt/event is pending"

    reservation = recovery._load_reservation(paths.recovery)  # noqa: SLF001
    if reservation is None:
        raise SourceDerivedWorksetOverlayIntegrityError(
            "Frozen manifest authority disappeared"
        )
    ordered, _, graph = recovery._dag(reservation)  # noqa: SLF001
    if (
        reservation.manifest_snapshot != base.reservation.manifest_snapshot
        or reservation.event_snapshot != base.reservation.event_snapshot
        or reservation.manifest != base.reservation.manifest
        or tuple(ordered) != tuple(base.ordered_items)
        or graph != base.dependency_graph_sha256
        or evidence.authority.reservation.manifest_snapshot
        != reservation.manifest_snapshot
        or evidence.authority.reservation.event_snapshot != reservation.event_snapshot
    ):
        raise SourceDerivedWorksetOverlayIntegrityError(
            "Manifest, derived, and cross-freeze base authorities diverged"
        )
    payload = evidence.reservation_payload
    dri_keys = {
        "derived_outcome_items",
        "rebound_existing_items",
        "invalidated_existing_items",
    }
    if any(not isinstance(payload.get(key), list) for key in dri_keys):
        raise SourceDerivedWorksetOverlayIntegrityError(
            "Source-derived D/R/I rows changed type"
        )
    effective, ids, application, effective_workset = _effective_overlay(
        reservation,
        derived_items=payload["derived_outcome_items"],
        rebound_items=payload["rebound_existing_items"],
        invalidated_items=payload["invalidated_existing_items"],
        maximum_items=profile["protocol"]["maximum_items"],
    )
    if (
        application["derived_added_count"] != payload["derived_outcome_item_count"]
        or application["rebound_replaced_count"]
        != payload["rebound_existing_item_count"]
        or application["invalidated_superseded_count"]
        != payload["invalidated_existing_item_count"]
    ):
        raise SourceDerivedWorksetOverlayIntegrityError(
            "Effective D/R/I counts differ from the published batch"
        )
    slot_id = _sha256(
        _canonical_bytes(
            {
                "manifest_sha256": reservation.manifest_snapshot.sha256,
                "derived_slot_id": evidence.authority.slot_id,
                "derived_event_sha256": evidence.event_snapshot.sha256,
                "cross_freeze_slot_id": base.slot_id,
                "cross_freeze_event_sha256": event[1].sha256,
            }
        )
    )
    return (
        SourceDerivedWorksetOverlayComputation(
            reservation=reservation,
            cross_authority=base,
            derived_evidence=evidence,
            cross_receipt_payload=receipt[0],
            cross_receipt_snapshot=receipt[1],
            cross_event_payload=event[0],
            cross_event_snapshot=event[1],
            effective_items=effective,
            effective_ids=ids,
            slot_id=slot_id,
            application=application,
            effective_workset=effective_workset,
        ),
        "",
    )


def _overlay_payload(
    profile: Mapping[str, Any],
    paths: SourceDerivedWorksetOverlayPaths,
    computation: SourceDerivedWorksetOverlayComputation,
) -> dict[str, object]:
    reservation = computation.reservation
    evidence = computation.derived_evidence
    derived_payload = evidence.reservation_payload
    return {
        "schema_version": profile["protocol"]["overlay_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "slot_id": computation.slot_id,
        "authority_scope": (
            "effective_workset_identity_and_dependency_overlay_only_not_"
            "materialization_consumption_or_recovery_v6_mutation"
        ),
        "base_authority": {
            "manifest": _reference(
                reservation.manifest_snapshot, reservation.paths.root
            ),
            "reservation_event": _reference(
                reservation.event_snapshot, reservation.paths.root
            ),
            "workset_keyset_sha256": reservation.manifest["workset_keyset_sha256"],
            "dependency_graph_sha256": computation.effective_workset[
                "base_dependency_graph_sha256"
            ],
            "item_count": reservation.manifest["item_count"],
        },
        "derived_authority": {
            "slot_id": evidence.authority.slot_id,
            "reservation": _reference(
                evidence.reservation_snapshot, paths.derived.root
            ),
            "event": _reference(evidence.event_snapshot, paths.derived.root),
            "derived_outcome_item_count": derived_payload["derived_outcome_item_count"],
            "derived_outcome_keyset_sha256": derived_payload[
                "derived_outcome_keyset_sha256"
            ],
            "rebound_existing_item_count": derived_payload[
                "rebound_existing_item_count"
            ],
            "rebound_existing_keyset_sha256": derived_payload[
                "rebound_existing_keyset_sha256"
            ],
            "invalidated_existing_item_count": derived_payload[
                "invalidated_existing_item_count"
            ],
            "invalidated_existing_keyset_sha256": derived_payload[
                "invalidated_existing_keyset_sha256"
            ],
        },
        "cross_freeze_authority": {
            "slot_id": computation.cross_authority.slot_id,
            "source_key_id": computation.cross_authority.source_item["key_id"],
            "snapshot_sequence_id": computation.cross_receipt_payload[
                "snapshot_sequence_id"
            ],
            "completion_receipt": _reference(
                computation.cross_receipt_snapshot, paths.cross.root
            ),
            "completion_event": _reference(
                computation.cross_event_snapshot, paths.cross.root
            ),
        },
        "application": dict(computation.application),
        "effective_workset": dict(computation.effective_workset),
        "effective_rows_embedded": False,
        "implementation": _implementation_reference(),
        **_claims(),
    }


def _event_unsigned(
    profile: Mapping[str, Any],
    paths: SourceDerivedWorksetOverlayPaths,
    computation: SourceDerivedWorksetOverlayComputation,
    overlay_snapshot: registry.ArtifactSnapshot,
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
        "slot_id": computation.slot_id,
        "source_key_id": computation.cross_authority.source_item["key_id"],
        "overlay": _reference(overlay_snapshot, paths.root),
        "effective_item_count": computation.effective_workset["item_count"],
        "effective_workset_keyset_sha256": computation.effective_workset[
            "natural_keyset_sha256"
        ],
        "effective_item_identity_set_sha256": computation.effective_workset[
            "item_identity_set_sha256"
        ],
        "effective_dependency_graph_sha256": computation.effective_workset[
            "dependency_graph_sha256"
        ],
        "derived_added_count": computation.application["derived_added_count"],
        "rebound_replaced_count": computation.application["rebound_replaced_count"],
        "invalidated_superseded_count": computation.application[
            "invalidated_superseded_count"
        ],
        **_claims(),
    }


def _load_overlay_state(
    profile: Mapping[str, Any],
    paths: SourceDerivedWorksetOverlayPaths,
    computation: SourceDerivedWorksetOverlayComputation,
) -> tuple[
    tuple[dict[str, Any], registry.ArtifactSnapshot] | None,
    tuple[dict[str, Any], registry.ArtifactSnapshot] | None,
]:
    """Purely deep-verify the singleton overlay object/event durable state."""

    entries = _strict_entries(paths.overlay_objects, name="effective-overlay objects")
    if len(entries) > 1:
        raise SourceDerivedWorksetOverlayIntegrityError(
            "Effective-overlay object namespace branched"
        )
    stored: tuple[dict[str, Any], registry.ArtifactSnapshot] | None = None
    if entries:
        matched = OBJECT_NAME.fullmatch(entries[0].name)
        payload, snapshot = _strict_json(entries[0], name="effective-overlay object")
        expected = _overlay_payload(profile, paths, computation)
        if (
            matched is None
            or matched.group("digest") != snapshot.sha256
            or snapshot.sha256 != _sha256(snapshot.raw)
            or payload != expected
        ):
            raise SourceDerivedWorksetOverlayIntegrityError(
                "Effective-overlay object semantics changed"
            )
        stored = (payload, snapshot)

    events = _strict_entries(paths.events, name="effective-overlay events")
    if len(events) > 1:
        raise SourceDerivedWorksetOverlayIntegrityError(
            "Effective-overlay event namespace branched"
        )
    published: tuple[dict[str, Any], registry.ArtifactSnapshot] | None = None
    if events:
        if stored is None:
            raise SourceDerivedWorksetOverlayIntegrityError(
                "Effective-overlay event has no object"
            )
        matched = EVENT_NAME.fullmatch(events[0].name)
        payload, snapshot = _strict_json(events[0], name="effective-overlay event")
        body = dict(payload)
        entry = body.pop("entry_sha256", None)
        expected = _event_unsigned(
            profile, paths, computation, stored[1], recorded_at=""
        )
        comparable = {
            key: value for key, value in body.items() if key != "recorded_at_utc"
        }
        expected_comparable = {
            key: value for key, value in expected.items() if key != "recorded_at_utc"
        }
        if (
            matched is None
            or matched.group("sequence") != "00000000000000000001"
            or matched.group("entry") != entry
            or set(payload) != {*expected, "entry_sha256"}
            or comparable != expected_comparable
            or entry != _sha256(_canonical_bytes(body))
        ):
            raise SourceDerivedWorksetOverlayIntegrityError(
                "Effective-overlay event semantics changed"
            )
        _parse_utc(payload.get("recorded_at_utc"), name="effective-overlay event time")
        published = (payload, snapshot)
    return stored, published


def _publish_overlay(
    profile: Mapping[str, Any],
    paths: SourceDerivedWorksetOverlayPaths,
    computation: SourceDerivedWorksetOverlayComputation,
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    payload = _overlay_payload(profile, paths, computation)
    raw = _canonical_bytes(payload)
    snapshot = _publish(
        paths.overlay_objects / f"{_sha256(raw)}.json",
        raw,
        root=paths.root,
        name="effective-overlay object",
    )
    checked, replay = _strict_json(snapshot.path, name="effective-overlay object")
    if checked != payload or replay != snapshot:
        raise SourceDerivedWorksetOverlayIntegrityError(
            "Effective-overlay object did not replay exactly"
        )
    return payload, snapshot


def _append_event(
    profile: Mapping[str, Any],
    paths: SourceDerivedWorksetOverlayPaths,
    computation: SourceDerivedWorksetOverlayComputation,
    overlay_snapshot: registry.ArtifactSnapshot,
    *,
    now: datetime,
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    body = _event_unsigned(
        profile,
        paths,
        computation,
        overlay_snapshot,
        recorded_at=_utc_text(now),
    )
    payload = {**body, "entry_sha256": _sha256(_canonical_bytes(body))}
    snapshot = _publish(
        paths.events / f"{1:020d}-{payload['entry_sha256']}.json",
        _canonical_bytes(payload),
        root=paths.root,
        name="effective-overlay event",
    )
    return payload, snapshot


def _write_status(
    profile: Mapping[str, Any],
    paths: SourceDerivedWorksetOverlayPaths,
    *,
    now: datetime,
    status: str,
    reason: str,
    computation: SourceDerivedWorksetOverlayComputation | None,
    overlay_path: Path | None,
    event_path: Path | None,
) -> None:
    application = computation.application if computation else {}
    effective = computation.effective_workset if computation else {}
    payload = {
        "schema_version": profile["protocol"]["status_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "recorded_at_utc": _utc_text(now),
        "status": status,
        "reason": reason,
        "slot_id": computation.slot_id if computation else None,
        "source_key_id": (
            computation.cross_authority.source_item["key_id"] if computation else None
        ),
        "overlay_path": str(overlay_path) if overlay_path else None,
        "event_path": str(event_path) if event_path else None,
        "base_item_count": application.get("base_item_count", 0),
        "effective_item_count": effective.get("item_count", 0),
        "derived_added_count": application.get("derived_added_count", 0),
        "rebound_replaced_count": application.get("rebound_replaced_count", 0),
        "invalidated_superseded_count": application.get(
            "invalidated_superseded_count", 0
        ),
        "effective_workset_keyset_sha256": effective.get("natural_keyset_sha256"),
        "effective_item_identity_set_sha256": effective.get("item_identity_set_sha256"),
        "effective_dependency_graph_sha256": effective.get("dependency_graph_sha256"),
        "derived_future_work_reservation_implemented": event_path is not None,
        "outcome_materialization_performed": False,
        "cache_authority": False,
        **{name: False for name in FALSE_CLAIMS},
    }
    try:
        registry._atomic_cache(  # noqa: SLF001
            paths.status,
            _canonical_bytes(payload),
            root=paths.root,
            name="effective-overlay status",
        )
    except registry.EpochRegistryError as exc:
        raise SourceDerivedWorksetOverlayIntegrityError(str(exc)) from exc


def _result(
    paths: SourceDerivedWorksetOverlayPaths,
    status: str,
    reason: str,
    computation: SourceDerivedWorksetOverlayComputation | None,
    *,
    overlay_path: Path | None = None,
    event_path: Path | None = None,
) -> SourceDerivedWorksetOverlayResult:
    application = computation.application if computation else {}
    effective = computation.effective_workset if computation else {}
    return SourceDerivedWorksetOverlayResult(
        status=status,
        reason=reason,
        status_path=paths.status,
        overlay_path=overlay_path,
        event_path=event_path,
        slot_id=computation.slot_id if computation else None,
        source_key_id=(
            str(computation.cross_authority.source_item["key_id"])
            if computation
            else None
        ),
        base_item_count=int(application.get("base_item_count", 0)),
        effective_item_count=int(effective.get("item_count", 0)),
        derived_added_count=int(application.get("derived_added_count", 0)),
        rebound_replaced_count=int(application.get("rebound_replaced_count", 0)),
        invalidated_superseded_count=int(
            application.get("invalidated_superseded_count", 0)
        ),
        effective_workset_keyset_sha256=effective.get("natural_keyset_sha256"),
        effective_item_identity_set_sha256=effective.get("item_identity_set_sha256"),
        effective_dependency_graph_sha256=effective.get("dependency_graph_sha256"),
        derived_future_work_reservation_implemented=event_path is not None,
    )


def _coordinate_source_derived_workset_overlay(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    registry_root: Path | None = None,
    active_root: Path | None = None,
    shadow_root: Path | None = None,
    clock: Callable[[], datetime] | None = None,
    fault_hook: FaultHook | None = None,
    load_derived_authority: LoadDerivedAuthority | None = None,
) -> SourceDerivedWorksetOverlayResult:
    profile = load_source_derived_workset_overlay_profile(config_path)
    paths = source_derived_workset_overlay_paths(
        profile,
        registry_root=registry_root,
        active_root=active_root,
        shadow_root=shadow_root,
    )
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    if now.tzinfo is None or now.utcoffset() is None:
        raise SourceDerivedWorksetOverlayIntegrityError(
            "Effective-overlay clock must be timezone-aware"
        )
    handles: list[BinaryIO] = []
    computation: SourceDerivedWorksetOverlayComputation | None = None
    try:
        handles = _acquire_locks(paths)
        computation, waiting_reason = _load_computation(
            profile,
            paths,
            now,
            load_derived_authority=load_derived_authority,
        )
        if computation is None:
            if _strict_entries(
                paths.overlay_objects, name="effective-overlay objects"
            ) or _strict_entries(paths.events, name="effective-overlay events"):
                raise SourceDerivedWorksetOverlayIntegrityError(
                    "Overlay bytes outlived their exact upstream authority"
                )
            status = "waiting_for_cross_freeze_completion"
            _write_status(
                profile,
                paths,
                now=now,
                status=status,
                reason=waiting_reason,
                computation=None,
                overlay_path=None,
                event_path=None,
            )
            return _result(paths, status, waiting_reason, None)

        stored, published = _load_overlay_state(profile, paths, computation)
        if published is not None:
            reason = "the exact source-derived effective-workset overlay is current"
            _write_status(
                profile,
                paths,
                now=now,
                status="source_derived_workset_overlay_current",
                reason=reason,
                computation=computation,
                overlay_path=stored[1].path if stored else None,
                event_path=published[1].path,
            )
            return _result(
                paths,
                "source_derived_workset_overlay_current",
                reason,
                computation,
                overlay_path=stored[1].path if stored else None,
                event_path=published[1].path,
            )
        if stored is None:
            _, overlay_snapshot = _publish_overlay(profile, paths, computation)
            if fault_hook is not None:
                fault_hook("after_overlay_object")
            status = "source_derived_workset_overlay_published"
            reason = "the exact D/R/I batch was applied to one effective workset"
        else:
            overlay_snapshot = stored[1]
            status = "source_derived_workset_overlay_event_forward_adopted"
            reason = "the durable effective-overlay object was adopted into its event"
        _, event_snapshot = _append_event(
            profile, paths, computation, overlay_snapshot, now=now
        )
        _write_status(
            profile,
            paths,
            now=now,
            status=status,
            reason=reason,
            computation=computation,
            overlay_path=overlay_snapshot.path,
            event_path=event_snapshot.path,
        )
        return _result(
            paths,
            status,
            reason,
            computation,
            overlay_path=overlay_snapshot.path,
            event_path=event_snapshot.path,
        )
    except SourceDerivedWorksetOverlayBusyError:
        raise
    except SourceDerivedWorksetOverlayError as exc:
        if handles:
            try:
                _write_status(
                    profile,
                    paths,
                    now=now,
                    status="blocked_integrity",
                    reason=f"{type(exc).__name__}:{exc}",
                    computation=computation,
                    overlay_path=None,
                    event_path=None,
                )
            except SourceDerivedWorksetOverlayError:
                pass
        raise
    except Exception as exc:
        raise SourceDerivedWorksetOverlayIntegrityError(
            f"Effective-overlay coordination failed:{type(exc).__name__}:{exc}"
        ) from exc
    finally:
        if handles:
            _release_locks(handles)


def coordinate_source_derived_workset_overlay(
    *, config_path: Path = DEFAULT_CONFIG_PATH
) -> SourceDerivedWorksetOverlayResult:
    """Run one machine-only effective-workset overlay coordination poll."""

    return _coordinate_source_derived_workset_overlay(config_path=config_path)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result = coordinate_source_derived_workset_overlay(config_path=args.config)
    print(
        json.dumps(
            {
                "status": result.status,
                "reason": result.reason,
                "status_path": str(result.status_path),
                "overlay_path": (
                    str(result.overlay_path) if result.overlay_path else None
                ),
                "event_path": str(result.event_path) if result.event_path else None,
                "slot_id": result.slot_id,
                "source_key_id": result.source_key_id,
                "base_item_count": result.base_item_count,
                "effective_item_count": result.effective_item_count,
                "derived_added_count": result.derived_added_count,
                "rebound_replaced_count": result.rebound_replaced_count,
                "invalidated_superseded_count": (result.invalidated_superseded_count),
                "effective_workset_keyset_sha256": (
                    result.effective_workset_keyset_sha256
                ),
                "effective_item_identity_set_sha256": (
                    result.effective_item_identity_set_sha256
                ),
                "effective_dependency_graph_sha256": (
                    result.effective_dependency_graph_sha256
                ),
                "derived_future_work_reservation_implemented": (
                    result.derived_future_work_reservation_implemented
                ),
                "outcome_materialization_performed": (
                    result.outcome_materialization_performed
                ),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
