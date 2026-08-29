"""Run one reviewed source ingest across the frozen-writer boundary.

The adapter never opens the admission-cut deploy or runner sentinel.  Under the
four surviving coordinator locks it freezes the manifest-bound feed, records a
deterministic intent, and invokes the pinned source implementation against only
that immutable feed copy.  The source snapshot receipt remains the source
commit point.  A separate completion event is published only after the existing
derived-key sidecar has durably classified the exact snapshot edge.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
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
    ootang_epoch_source_ingest_derived_reservation as derived,
)
from monitoring import ootang_epoch_workset_manifest as manifest  # noqa: E402
from monitoring import ootang_epoch_workset_recovery as recovery  # noqa: E402
from monitoring import ootang_live_source as source  # noqa: E402


DEFAULT_CONFIG_PATH = (
    ROOT / "config" / "ootang_epoch_source_ingest_cross_freeze.v1.json"
)
DEFAULT_CONFIG_SHA256 = (
    "d5ebf200bacae7debfc0a20d3b431f108e75e54617e4d421d12bf998e18a420e"
)
IMPLEMENTATION_LOGICAL_PATH = (
    "code/monitoring/ootang_epoch_source_ingest_cross_freeze.py"
)
MAX_CONTROL_BYTES = 4 * 1024 * 1024
ZERO_HASH = "0" * 64
LOCK_ORDER = recovery.LOCK_ORDER
SLOT_NAME = re.compile(r"^(?P<slot>[0-9a-f]{64})\.json$")
EVENT_NAME = re.compile(r"^(?P<sequence>[0-9]{20})-(?P<entry>[0-9a-f]{64})\.json$")

EXPECTED_UPSTREAM = {
    "admission_cut_profile": {
        "path": "config/ootang_epoch_admission_cut.v1.json",
        "expected_sha256": "fe4e91768c8558d887a34465fa6c9f4e8c05f8c1a7cf07e061bc602733136c1c",
    },
    "admission_cut_implementation": {
        "path": "code/monitoring/ootang_epoch_admission_cut.py",
        "expected_sha256": "95b675b132c5051cbbc4d34041b9686d122a64c6368f04c0bff8d6dddf1effcf",
    },
    "manifest_profile": {
        "path": "config/ootang_epoch_workset_manifest.v1.json",
        "expected_sha256": "857ae1ff031289d51c0a2947beeb2e47ceb9d48a3769db707c8f7f75750d48dc",
    },
    "manifest_implementation": {
        "path": "code/monitoring/ootang_epoch_workset_manifest.py",
        "expected_sha256": "0ca331c6827cd896b4c3261792eed5f933e104d4c40c4fbf3b7e416e9b1fd424",
    },
    "inventory_implementation": {
        "path": "code/monitoring/ootang_epoch_workset_inventory.py",
        "expected_sha256": "2b56de3f36da3a08d34bf3a9509b0492c5d989cd1f391491c0574bed2723bd52",
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
}
EXPECTED_RUNTIME = {
    "registry_root": "runtime/ootang_epoch_registry_v1",
    "recovery_namespace": "workset_recovery_v1",
    "namespace": "source_ingest_cross_freeze_v1",
    "prepared_feed_objects": "source_ingest_cross_freeze_v1/prepared_feeds/sha256",
    "prepares": "prepares",
    "intents": "intents",
    "receipts": "receipts",
    "events": "events",
    "status": "status.json",
    "manager_lock": "manager.lock",
    "active_root": "runtime/ootang_prequential_live_v1",
    "shadow_root": "runtime/ootang_prequential_calibration_shadow_v1",
}
EXPECTED_PROTOCOL = {
    "prepare_schema_version": "ootang_epoch_source_ingest_cross_freeze_prepare_v1",
    "intent_schema_version": "ootang_epoch_source_ingest_cross_freeze_intent_v1",
    "receipt_schema_version": "ootang_epoch_source_ingest_cross_freeze_receipt_v1",
    "event_schema_version": "ootang_epoch_source_ingest_cross_freeze_event_v1",
    "status_schema_version": "ootang_epoch_source_ingest_cross_freeze_status_v1",
    "event_type": "epoch_source_ingest_cross_freeze_completed",
    "source_action": "source_snapshot_ingested",
    "derived_action": "derived_batch_classified",
    "canonical_json": "utf8_sort_keys_compact_no_nan_trailing_lf",
    "initial_previous_entry_sha256": ZERO_HASH,
    "maximum_items": 4096,
    "maximum_control_bytes": MAX_CONTROL_BYTES,
    "maximum_feed_bytes": 64 * 1024 * 1024,
    "poll_policy": (
        "prepare_ingest_or_adopt_drive_derived_then_publish_at_most_one_frozen_source_slot"
    ),
}
TRUE_CAPABILITIES = (
    "machine_only",
    "cross_freeze_source_writer_implemented",
    "immutable_feed_prepare_implemented",
    "deterministic_intent_implemented",
    "source_receipt_crash_adoption_implemented",
    "derived_coordinator_automatic",
    "create_only_completion_receipts_implemented",
    "append_only_completion_events_implemented",
    "source_snapshot_ingested",
    "derived_batch_classified",
)
FALSE_CLAIMS = (
    "terminal_for_recovery_v6_key",
    "legacy_deploy_writer_reactivated",
    "recovery_v6_receipt_created",
    "outcome_materialization_performed",
    "outcome_or_revision_consumed",
    "derived_future_work_reservation_implemented",
    "all_content_dependent_lanes_reserved",
    "terminal_transition_closure_implemented",
    "bounded_workset_recovery_implemented",
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


class SourceIngestCrossFreezeError(RuntimeError):
    """Base error for the cross-freeze source adapter."""


class SourceIngestCrossFreezeConfigError(SourceIngestCrossFreezeError):
    """The reviewed profile or a direct pinned upstream changed."""


class SourceIngestCrossFreezeIntegrityError(SourceIngestCrossFreezeError):
    """A source, manifest, derived, or adapter authority failed closed."""


class SourceIngestCrossFreezeBusyError(SourceIngestCrossFreezeError):
    """A surviving globally ordered lock is held."""


@dataclass(frozen=True)
class SourceIngestCrossFreezePaths:
    registry_root: Path
    root: Path
    prepared_feed_objects: Path
    prepares: Path
    intents: Path
    receipts: Path
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


@dataclass(frozen=True)
class SourceIngestCrossFreezeResult:
    status: str
    reason: str
    status_path: Path
    prepare_path: Path | None = None
    intent_path: Path | None = None
    receipt_path: Path | None = None
    event_path: Path | None = None
    source_key_id: str | None = None
    snapshot_sequence_id: int | None = None
    derived_key_count: int = 0
    rebound_item_count: int = 0
    invalidated_item_count: int = 0
    source_snapshot_ingested: bool = False
    derived_batch_classified: bool = False
    terminal_for_recovery_v6_key: bool = False


@dataclass(frozen=True)
class SourceIngestCrossFreezeAuthority:
    reservation: recovery.Reservation
    ordered_items: tuple[Mapping[str, Any], ...]
    dependency_graph_sha256: str
    source_item: Mapping[str, Any]
    source_profile: dict[str, Any]
    predecessor_source: source.CanonicalSource
    current_source: source.CanonicalSource
    incoming_artifact: Mapping[str, Any]
    feed_snapshot: registry.ArtifactSnapshot
    slot_id: str
    derived_authority: derived.SourceIngestDerivedAuthority | None


@dataclass(frozen=True)
class _DerivedEvidence:
    authority: derived.SourceIngestDerivedAuthority
    reservation_payload: Mapping[str, Any]
    reservation_snapshot: registry.ArtifactSnapshot
    event_payload: Mapping[str, Any]
    event_snapshot: registry.ArtifactSnapshot


FaultHook = Callable[[str], None]
EnsureDerived = Callable[[derived.SourceIngestDerivedPaths, datetime], object]
LoadDerivedAuthority = Callable[
    [derived.SourceIngestDerivedPaths, datetime],
    derived.SourceIngestDerivedAuthority | None,
]


def _canonical_bytes(value: object) -> bytes:
    try:
        return registry._canonical_bytes(value)  # type: ignore[arg-type]  # noqa: SLF001
    except (TypeError, ValueError) as exc:
        raise SourceIngestCrossFreezeIntegrityError(
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
        raise SourceIngestCrossFreezeIntegrityError(f"{name} keys changed")
    return value


def _text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise SourceIngestCrossFreezeIntegrityError(f"{name} changed")
    return value


def _hash(value: object, *, name: str) -> str:
    text = _text(value, name=name)
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise SourceIngestCrossFreezeIntegrityError(
            f"{name} is not a lowercase SHA-256"
        )
    return text


def _positive_int(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SourceIngestCrossFreezeIntegrityError(f"{name} is not positive")
    return value


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SourceIngestCrossFreezeIntegrityError(
            "Cross-freeze clock must be timezone-aware"
        )
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: object, *, name: str) -> datetime:
    try:
        return registry._utc_timestamp(value, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise SourceIngestCrossFreezeIntegrityError(str(exc)) from exc


def _child(root: Path, relative: str, *, name: str) -> Path:
    try:
        return registry._contained(root, relative, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise SourceIngestCrossFreezeConfigError(str(exc)) from exc


def _strict_json(
    path: Path, *, name: str
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    try:
        return manifest._read_json(  # noqa: SLF001
            path, name=name, maximum_bytes=MAX_CONTROL_BYTES
        )
    except manifest.WorksetManifestError as exc:
        raise SourceIngestCrossFreezeIntegrityError(str(exc)) from exc


def _strict_entries(directory: Path, *, name: str) -> tuple[Path, ...]:
    try:
        return manifest._strict_entries(directory, name=name)  # noqa: SLF001
    except manifest.WorksetManifestError as exc:
        raise SourceIngestCrossFreezeIntegrityError(str(exc)) from exc


def _publish(
    path: Path,
    raw: bytes,
    *,
    root: Path,
    name: str,
    maximum_bytes: int = MAX_CONTROL_BYTES,
) -> registry.ArtifactSnapshot:
    if len(raw) > maximum_bytes:
        raise SourceIngestCrossFreezeIntegrityError(f"{name} capacity exceeded")
    try:
        return drain._publish_once_durable(  # noqa: SLF001
            path, raw, root=root, name=name
        )
    except drain.EpochDrainError as exc:
        raise SourceIngestCrossFreezeIntegrityError(str(exc)) from exc


def _call_fault_hook(fault_hook: FaultHook | None, stage: str) -> None:
    if fault_hook is not None:
        fault_hook(stage)


def load_source_ingest_cross_freeze_profile(
    path: Path = DEFAULT_CONFIG_PATH, *, project_root: Path = ROOT
) -> dict[str, Any]:
    root = project_root.resolve()
    resolved = path if path.is_absolute() else root / path
    if root != ROOT.resolve() or resolved.resolve() != DEFAULT_CONFIG_PATH.resolve():
        raise SourceIngestCrossFreezeConfigError(
            "Only the reviewed default cross-freeze profile is accepted"
        )
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            resolved, name="cross-freeze profile", maximum_bytes=MAX_CONTROL_BYTES
        )
        profile = registry._decode_json(  # noqa: SLF001
            snapshot.raw, name="cross-freeze profile"
        )
    except registry.EpochRegistryError as exc:
        raise SourceIngestCrossFreezeConfigError(str(exc)) from exc
    if snapshot.sha256 != DEFAULT_CONFIG_SHA256:
        raise SourceIngestCrossFreezeConfigError("Cross-freeze profile SHA-256 changed")
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
        name="cross-freeze profile",
    )
    if (
        profile["schema_version"]
        != "ootang_epoch_source_ingest_cross_freeze_profile_v1"
        or profile["profile_id"] != "ootang-epoch-source-ingest-cross-freeze-v1"
        or profile["case"] != "ootang"
        or profile["formal_warning_output"] is not False
        or profile["default_pipeline_member"] is not False
        or profile["upstream"] != EXPECTED_UPSTREAM
        or profile["runtime"] != EXPECTED_RUNTIME
        or profile["protocol"] != EXPECTED_PROTOCOL
        or profile["engineering_capabilities"] != _claims()
    ):
        raise SourceIngestCrossFreezeConfigError(
            "Cross-freeze profile semantics changed"
        )
    for binding in EXPECTED_UPSTREAM.values():
        upstream = _child(root, binding["path"], name="cross-freeze upstream")
        try:
            checked = registry._read_regular(  # noqa: SLF001
                upstream,
                name="cross-freeze upstream",
                maximum_bytes=64 * 1024 * 1024,
            )
        except registry.EpochRegistryError as exc:
            raise SourceIngestCrossFreezeConfigError(str(exc)) from exc
        if checked.sha256 != binding["expected_sha256"]:
            raise SourceIngestCrossFreezeConfigError(
                f"Pinned upstream changed:{binding['path']}"
            )
    profile["_profile_sha256"] = snapshot.sha256
    return profile


def source_ingest_cross_freeze_paths(
    profile: Mapping[str, Any],
    *,
    registry_root: Path | None = None,
    active_root: Path | None = None,
    shadow_root: Path | None = None,
) -> SourceIngestCrossFreezePaths:
    registry_path = (
        registry_root or ROOT / profile["runtime"]["registry_root"]
    ).resolve()
    recovery_root = _child(
        registry_path,
        profile["runtime"]["recovery_namespace"],
        name="recovery namespace",
    )
    root = _child(
        recovery_root, profile["runtime"]["namespace"], name="cross-freeze root"
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
    return SourceIngestCrossFreezePaths(
        registry_root=registry_path,
        root=root,
        prepared_feed_objects=_child(
            active,
            profile["runtime"]["prepared_feed_objects"],
            name="prepared feed objects",
        ),
        prepares=_child(root, profile["runtime"]["prepares"], name="prepares"),
        intents=_child(root, profile["runtime"]["intents"], name="intents"),
        receipts=_child(root, profile["runtime"]["receipts"], name="receipts"),
        events=_child(root, profile["runtime"]["events"], name="events"),
        status=_child(root, profile["runtime"]["status"], name="status"),
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
    )


def _reference(snapshot: registry.ArtifactSnapshot, root: Path) -> dict[str, object]:
    try:
        relative = snapshot.path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise SourceIngestCrossFreezeIntegrityError(
            "Referenced artifact escaped its root"
        ) from exc
    return {
        "path": relative,
        "sha256": snapshot.sha256,
        "size_bytes": snapshot.size_bytes,
    }


def _source_reference(artifact: source.ArtifactRef, root: Path) -> dict[str, object]:
    try:
        relative = artifact.path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise SourceIngestCrossFreezeIntegrityError(
            "Source artifact escaped active runtime"
        ) from exc
    return {
        "path": relative,
        "sha256": artifact.sha256,
        "size_bytes": artifact.size_bytes,
    }


def _implementation_reference() -> dict[str, object]:
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            Path(__file__),
            name="cross-freeze implementation",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
    except registry.EpochRegistryError as exc:
        raise SourceIngestCrossFreezeIntegrityError(str(exc)) from exc
    return {
        "path": IMPLEMENTATION_LOGICAL_PATH,
        "sha256": snapshot.sha256,
        "size_bytes": snapshot.size_bytes,
    }


def _acquire_locks(paths: SourceIngestCrossFreezePaths) -> list[BinaryIO]:
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
                raise SourceIngestCrossFreezeBusyError(str(exc)) from exc
            except drain.EpochDrainError as exc:
                raise SourceIngestCrossFreezeIntegrityError(str(exc)) from exc
        return handles
    except SourceIngestCrossFreezeError:
        try:
            drain._release_locks(handles)  # noqa: SLF001
        except drain.EpochDrainError:
            pass
        raise


def _release_locks(handles: Sequence[BinaryIO]) -> None:
    try:
        drain._release_locks(handles)  # noqa: SLF001
    except drain.EpochDrainError as exc:
        raise SourceIngestCrossFreezeIntegrityError(str(exc)) from exc


def _source_item(
    reservation: recovery.Reservation,
) -> tuple[tuple[Mapping[str, Any], ...], str, Mapping[str, Any]] | None:
    ordered, _, graph_digest = recovery._dag(reservation)  # noqa: SLF001
    candidates = [
        item
        for item in ordered
        if item.get("family") == "outcome_revision"
        and item.get("canonical_successor_state") == "source_snapshot_ingested"
    ]
    if not candidates:
        return None
    if len(candidates) != 1:
        raise SourceIngestCrossFreezeIntegrityError(
            "Frozen manifest has multiple source-ingest candidates"
        )
    item = candidates[0]
    authority = item.get("authority")
    plan = recovery._transition_plan(item)  # noqa: SLF001
    if (
        not isinstance(authority, Mapping)
        or authority.get("action") != "ingest_incoming_finalized_feed"
        or plan.get("initial_action") != "source_snapshot_ingested"
        or plan.get("terminal_actions") != []
        or plan.get("closure_resolved") is not False
    ):
        raise SourceIngestCrossFreezeIntegrityError(
            "Frozen source-ingest transition contract changed"
        )
    return tuple(ordered), graph_digest, item


def _predecessor_source(
    source_profile: dict[str, Any],
    paths: SourceIngestCrossFreezePaths,
    item: Mapping[str, Any],
) -> source.CanonicalSource:
    authority = item["authority"]
    predecessor_sha = _hash(
        authority.get("predecessor_snapshot_receipt_sha256"),
        name="predecessor snapshot receipt",
    )
    objects = source._runtime_path(  # noqa: SLF001
        source_profile, "objects", root=paths.active_root
    )
    path = objects / f"{predecessor_sha}.source-snapshot-receipt.json"
    try:
        artifact = source._artifact_from_object_path(  # noqa: SLF001
            path,
            objects=objects,
            suffix="source-snapshot-receipt.json",
            name="frozen predecessor source receipt",
        )
        payload = source._load_json_artifact(  # noqa: SLF001
            artifact, name="frozen predecessor source receipt"
        )
        predecessor = derived._load_source_from_snapshot_receipt(  # noqa: SLF001
            source_profile,
            root=paths.active_root,
            receipt_artifact=artifact,
            receipt_payload=payload,
        )
    except SourceIngestCrossFreezeError:
        raise
    except Exception as exc:
        raise SourceIngestCrossFreezeIntegrityError(
            f"Cannot replay frozen predecessor source:{type(exc).__name__}:{exc}"
        ) from exc
    expected_sequence = _positive_int(
        authority.get("predecessor_snapshot_sequence_id"),
        name="predecessor snapshot sequence",
    )
    if (
        predecessor.snapshot_sequence_id != expected_sequence
        or predecessor.snapshot_receipt is None
        or predecessor.snapshot_receipt.sha256 != predecessor_sha
        or predecessor.outcome_source_id != authority.get("outcome_source_id")
    ):
        raise SourceIngestCrossFreezeIntegrityError(
            "Frozen predecessor source identity changed"
        )
    return predecessor


def _incoming_artifact(item: Mapping[str, Any]) -> Mapping[str, Any]:
    artifacts = item.get("artifacts")
    if not isinstance(artifacts, list):
        raise SourceIngestCrossFreezeIntegrityError(
            "Frozen source item artifacts changed type"
        )
    values = [
        value
        for value in artifacts
        if isinstance(value, Mapping)
        and value.get("role") == "incoming_finalized_feed"
        and value.get("root") == "active"
    ]
    if len(values) != 1:
        raise SourceIngestCrossFreezeIntegrityError(
            "Frozen source item lacks one active incoming feed"
        )
    return values[0]


def _feed_from_derived(
    paths: SourceIngestCrossFreezePaths,
    authority: derived.SourceIngestDerivedAuthority,
) -> registry.ArtifactSnapshot:
    semantic = authority.successor_source.semantic_manifest
    objects = source._runtime_path(  # noqa: SLF001
        source.load_deploy_profile(), "objects", root=paths.active_root
    )
    try:
        payload = source._load_json_artifact(  # noqa: SLF001
            semantic, name="cross-freeze successor semantic manifest"
        )
        lineage = payload.get("lineage")
        daily = (
            lineage.get("daily_feed_snapshot") if isinstance(lineage, Mapping) else None
        )
        if not isinstance(daily, Mapping):
            raise SourceIngestCrossFreezeIntegrityError(
                "Successor semantic manifest lost daily feed lineage"
            )
        artifact = source._content_object_from_payload(  # noqa: SLF001
            {key: daily[key] for key in ("path", "sha256", "size_bytes")},
            name="cross-freeze immutable successor feed",
            directory=objects,
            suffix="feed.json",
        )
        return registry._read_regular(  # noqa: SLF001
            artifact.path,
            name="cross-freeze immutable successor feed",
            maximum_bytes=64 * 1024 * 1024,
        )
    except SourceIngestCrossFreezeError:
        raise
    except Exception as exc:
        raise SourceIngestCrossFreezeIntegrityError(
            f"Cannot replay successor feed:{type(exc).__name__}:{exc}"
        ) from exc


def _read_frozen_feed(
    paths: SourceIngestCrossFreezePaths,
    item: Mapping[str, Any],
    artifact: Mapping[str, Any],
    derived_authority: derived.SourceIngestDerivedAuthority | None,
) -> registry.ArtifactSnapshot:
    expected_sha = _hash(
        item["authority"].get("incoming_feed_sha256"), name="incoming feed digest"
    )
    expected_size = artifact.get("size_bytes")
    if (
        isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or not 0 < expected_size <= 64 * 1024 * 1024
        or artifact.get("sha256") != expected_sha
    ):
        raise SourceIngestCrossFreezeIntegrityError(
            "Frozen incoming feed reference changed"
        )
    prepared_path = paths.prepared_feed_objects / f"{expected_sha}.json"
    if prepared_path.exists() or prepared_path.is_symlink():
        try:
            prepared = registry._read_regular(  # noqa: SLF001
                prepared_path,
                name="existing cross-freeze prepared feed",
                maximum_bytes=64 * 1024 * 1024,
            )
        except registry.EpochRegistryError as exc:
            raise SourceIngestCrossFreezeIntegrityError(str(exc)) from exc
        if prepared.sha256 != expected_sha or prepared.size_bytes != expected_size:
            raise SourceIngestCrossFreezeIntegrityError(
                "Existing prepared feed changed"
            )
        return prepared
    try:
        path = registry._contained(  # noqa: SLF001
            paths.active_root,
            _text(artifact.get("path"), name="incoming feed path"),
            name="frozen incoming feed",
        )
        snapshot = registry._read_regular(  # noqa: SLF001
            path,
            name="frozen incoming feed",
            maximum_bytes=64 * 1024 * 1024,
        )
    except (registry.EpochRegistryError, SourceIngestCrossFreezeError):
        if derived_authority is None:
            raise SourceIngestCrossFreezeIntegrityError(
                "Frozen incoming feed is unavailable before source commit"
            )
        snapshot = _feed_from_derived(paths, derived_authority)
    if snapshot.sha256 != expected_sha or snapshot.size_bytes != expected_size:
        if derived_authority is None:
            raise SourceIngestCrossFreezeIntegrityError(
                "Frozen incoming feed bytes changed before source commit"
            )
        snapshot = _feed_from_derived(paths, derived_authority)
    if snapshot.sha256 != expected_sha or snapshot.size_bytes != expected_size:
        raise SourceIngestCrossFreezeIntegrityError(
            "Committed successor does not preserve the frozen feed bytes"
        )
    return snapshot


def _recover_pointer_for_exact_intent_child(
    paths: SourceIngestCrossFreezePaths,
    source_profile: dict[str, Any],
    predecessor: source.CanonicalSource,
    item: Mapping[str, Any],
    prepared_feed: registry.ArtifactSnapshot,
    *,
    pointer: Path,
    objects: Path,
) -> None:
    """Repair only the exact frozen-feed N+1 child committed by this intent."""

    state = source._load_snapshot_receipt_registry(  # noqa: SLF001
        objects, required=True
    )
    if state is None:
        raise SourceIngestCrossFreezeIntegrityError(
            "Durable writer intent has no source snapshot registry"
        )
    public_current = False
    if pointer.exists() or pointer.is_symlink():
        public = registry._read_regular(  # noqa: SLF001
            pointer,
            name="cross-freeze public source pointer",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
        public_current = (
            public.sha256 == state.head_pointer.sha256
            and public.raw == state.head_pointer_raw
        )
    if public_current:
        return

    predecessor_receipt = predecessor.snapshot_receipt
    predecessor_reference = state.head_payload.get("predecessor_snapshot_receipt")
    if (
        predecessor_receipt is None
        or state.head_payload.get("profile_id") != source_profile.get("profile_id")
        or state.head_payload.get("snapshot_sequence_id")
        != predecessor.snapshot_sequence_id + 1
        or state.head_payload.get("outcome_source_id") != predecessor.outcome_source_id
        or not isinstance(predecessor_reference, Mapping)
        or predecessor_reference.get("sha256") != predecessor_receipt.sha256
    ):
        raise SourceIngestCrossFreezeIntegrityError(
            "Stale pointer registry head is not the intent's exact N+1 child"
        )
    try:
        successor = derived._load_source_from_snapshot_receipt(  # noqa: SLF001
            source_profile,
            root=paths.active_root,
            receipt_artifact=state.head,
            receipt_payload=state.head_payload,
        )
        semantic = source._load_json_artifact(  # noqa: SLF001
            successor.semantic_manifest,
            name="cross-freeze recovery successor semantic manifest",
        )
        lineage = semantic.get("lineage")
        daily = (
            lineage.get("daily_feed_snapshot") if isinstance(lineage, Mapping) else None
        )
        if not isinstance(daily, Mapping):
            raise SourceIngestCrossFreezeIntegrityError(
                "Recovery child lost immutable daily-feed lineage"
            )
        immutable_feed = source._content_object_from_payload(  # noqa: SLF001
            {key: daily[key] for key in ("path", "sha256", "size_bytes")},
            name="cross-freeze recovery successor feed",
            directory=objects,
            suffix="feed.json",
        )
        successor_feed = registry._read_regular(  # noqa: SLF001
            immutable_feed.path,
            name="cross-freeze recovery successor feed",
            maximum_bytes=64 * 1024 * 1024,
        )
        changed = derived._changed_rows(predecessor, successor)  # noqa: SLF001
    except SourceIngestCrossFreezeError:
        raise
    except Exception as exc:
        raise SourceIngestCrossFreezeIntegrityError(
            f"Intent child replay failed:{type(exc).__name__}:{exc}"
        ) from exc
    item_authority = item["authority"]
    if (
        successor.snapshot_receipt != state.head
        or successor.snapshot_sequence_id != predecessor.snapshot_sequence_id + 1
        or successor.outcome_source_id != predecessor.outcome_source_id
        or successor_feed.sha256 != prepared_feed.sha256
        or successor_feed.size_bytes != prepared_feed.size_bytes
        or successor_feed.raw != prepared_feed.raw
        or daily.get("exported_at_utc")
        != item_authority.get("incoming_exported_at_utc")
        or changed != item_authority.get("changed_or_appended_revisions")
    ):
        raise SourceIngestCrossFreezeIntegrityError(
            "Committed N+1 child does not match the durable frozen-feed intent"
        )
    source._recover_current_pointer_from_snapshot_registry(  # noqa: SLF001
        pointer, objects=objects
    )


def _load_cross_freeze_authority(
    profile: Mapping[str, Any],
    paths: SourceIngestCrossFreezePaths,
    now: datetime,
    *,
    load_derived_authority: LoadDerivedAuthority | None = None,
) -> SourceIngestCrossFreezeAuthority | None:
    reservation = recovery._load_reservation(paths.recovery)  # noqa: SLF001
    if reservation is None:
        return None
    selected = _source_item(reservation)
    if selected is None:
        return None
    ordered, graph_digest, item = selected
    source_profile = source.load_deploy_profile()
    predecessor = _predecessor_source(source_profile, paths, item)
    incoming = _incoming_artifact(item)
    incoming_sha = _hash(
        item["authority"].get("incoming_feed_sha256"), name="incoming feed digest"
    )
    slot_id = _sha256(
        _canonical_bytes(
            {
                "manifest_sha256": reservation.manifest_snapshot.sha256,
                "source_key_id": item["key_id"],
                "predecessor_snapshot_receipt_sha256": predecessor.snapshot_receipt.sha256,
                "incoming_feed_sha256": incoming_sha,
            }
        )
    )
    prepare_records = _named_records(paths.prepares, name="cross-freeze prepares")
    intent_records = _named_records(paths.intents, name="cross-freeze intents")
    receipt_records = _named_records(paths.receipts, name="cross-freeze receipts")
    event_entries = _strict_entries(paths.events, name="cross-freeze events")
    if (
        set(prepare_records) - {slot_id}
        or set(intent_records) - {slot_id}
        or set(receipt_records) - {slot_id}
    ):
        raise SourceIngestCrossFreezeIntegrityError(
            "Cross-freeze namespace belongs to another frozen source slot"
        )
    if (receipt_records or event_entries) and slot_id not in intent_records:
        raise SourceIngestCrossFreezeIntegrityError(
            "Cross-freeze completion bytes lack their durable writer intent"
        )
    pointer = source._runtime_path(  # noqa: SLF001
        source_profile, "current_source_pointer", root=paths.active_root
    )
    objects = source._runtime_path(  # noqa: SLF001
        source_profile, "objects", root=paths.active_root
    )
    try:
        if slot_id in intent_records:
            prepared_feed = _read_frozen_feed(paths, item, incoming, None)
            provisional = SourceIngestCrossFreezeAuthority(
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
            prepare = prepare_records.get(slot_id)
            if prepare is None or prepare[0] != _prepare_payload(
                profile, paths, provisional, prepared_feed
            ):
                raise SourceIngestCrossFreezeIntegrityError(
                    "Pointer recovery intent lacks its exact immutable prepare"
                )
            if intent_records[slot_id][0] != _intent_payload(
                profile, paths, provisional, prepare[1], prepared_feed
            ):
                raise SourceIngestCrossFreezeIntegrityError(
                    "Pointer recovery intent semantics changed"
                )
            _recover_pointer_for_exact_intent_child(
                paths,
                source_profile,
                predecessor,
                item,
                prepared_feed,
                pointer=pointer,
                objects=objects,
            )
        current = source.load_current_source(
            source_profile, runtime_root=paths.active_root, project_root=ROOT
        )
    except Exception as exc:
        raise SourceIngestCrossFreezeIntegrityError(
            f"Current source replay failed:{type(exc).__name__}:{exc}"
        ) from exc
    if current.snapshot_sequence_id < predecessor.snapshot_sequence_id:
        raise SourceIngestCrossFreezeIntegrityError(
            "Current source predates the frozen predecessor"
        )
    loader = load_derived_authority or derived._load_source_ingest_authority  # noqa: SLF001
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
        raise SourceIngestCrossFreezeIntegrityError(
            "Derived authority belongs to another frozen source slot"
        )
    if (
        current.snapshot_sequence_id > predecessor.snapshot_sequence_id
        and derived_authority is None
    ):
        raise SourceIngestCrossFreezeIntegrityError(
            "Advanced source has no exact historical N+1 derived authority"
        )
    feed_snapshot = _read_frozen_feed(paths, item, incoming, derived_authority)
    if feed_snapshot.size_bytes > 64 * 1024 * 1024:
        raise SourceIngestCrossFreezeIntegrityError("Prepared feed capacity exceeded")
    if feed_snapshot.sha256 != incoming_sha:
        raise SourceIngestCrossFreezeIntegrityError(
            "Frozen feed digest changed while loading cross-freeze authority"
        )
    return SourceIngestCrossFreezeAuthority(
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


def _ensure_prepared_feed(
    profile: Mapping[str, Any],
    paths: SourceIngestCrossFreezePaths,
    authority: SourceIngestCrossFreezeAuthority,
) -> registry.ArtifactSnapshot:
    raw = authority.feed_snapshot.raw
    if len(raw) > profile["protocol"]["maximum_feed_bytes"]:
        raise SourceIngestCrossFreezeIntegrityError("Prepared feed capacity exceeded")
    path = paths.prepared_feed_objects / f"{authority.feed_snapshot.sha256}.json"
    snapshot = _publish(
        path,
        raw,
        root=paths.active_root,
        name="cross-freeze prepared feed",
        maximum_bytes=profile["protocol"]["maximum_feed_bytes"],
    )
    if snapshot.sha256 != authority.feed_snapshot.sha256:
        raise SourceIngestCrossFreezeIntegrityError(
            "Prepared feed differs from frozen input"
        )
    return snapshot


def _prepare_payload(
    profile: Mapping[str, Any],
    paths: SourceIngestCrossFreezePaths,
    authority: SourceIngestCrossFreezeAuthority,
    prepared_feed: registry.ArtifactSnapshot,
) -> dict[str, object]:
    item_authority = authority.source_item["authority"]
    predecessor = authority.predecessor_source.snapshot_receipt
    if predecessor is None:
        raise SourceIngestCrossFreezeIntegrityError(
            "Frozen predecessor lost its snapshot receipt"
        )
    return {
        "schema_version": profile["protocol"]["prepare_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "slot_id": authority.slot_id,
        "prepare_policy": "immutable_copy_of_manifest_bound_incoming_feed",
        "manifest": _reference(
            authority.reservation.manifest_snapshot, authority.reservation.paths.root
        ),
        "manifest_reservation_event": _reference(
            authority.reservation.event_snapshot, authority.reservation.paths.root
        ),
        "source_key_id": authority.source_item["key_id"],
        "source_natural_key": authority.source_item["natural_key"],
        "source_namespace_digest": authority.source_item["namespace_digest"],
        "source_transition_plan_sha256": recovery._transition_plan(  # noqa: SLF001
            authority.source_item
        )["plan_sha256"],
        "predecessor_snapshot_sequence_id": authority.predecessor_source.snapshot_sequence_id,
        "predecessor_snapshot_receipt": _source_reference(
            predecessor, paths.active_root
        ),
        "incoming_feed": dict(authority.incoming_artifact),
        "incoming_exported_at_utc": item_authority["incoming_exported_at_utc"],
        "changed_or_appended_revisions": item_authority[
            "changed_or_appended_revisions"
        ],
        "prepared_feed": _reference(prepared_feed, paths.active_root),
        "terminal_for_recovery_v6_key": False,
    }


def _ensure_prepare(
    profile: Mapping[str, Any],
    paths: SourceIngestCrossFreezePaths,
    authority: SourceIngestCrossFreezeAuthority,
    prepared_feed: registry.ArtifactSnapshot,
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    payload = _prepare_payload(profile, paths, authority, prepared_feed)
    raw = _canonical_bytes(payload)
    path = paths.prepares / f"{authority.slot_id}.json"
    snapshot = _publish(path, raw, root=paths.root, name="cross-freeze prepare")
    checked, replay = _strict_json(path, name="cross-freeze prepare")
    if checked != payload or replay != snapshot:
        raise SourceIngestCrossFreezeIntegrityError(
            "Cross-freeze prepare did not replay exactly"
        )
    return payload, snapshot


def _intent_payload(
    profile: Mapping[str, Any],
    paths: SourceIngestCrossFreezePaths,
    authority: SourceIngestCrossFreezeAuthority,
    prepare_snapshot: registry.ArtifactSnapshot,
    prepared_feed: registry.ArtifactSnapshot,
) -> dict[str, object]:
    relative_feed = (
        prepared_feed.path.resolve().relative_to(paths.active_root.resolve()).as_posix()
    )
    return {
        "schema_version": profile["protocol"]["intent_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "slot_id": authority.slot_id,
        "intent_policy": (
            "execute_pinned_source_ingest_only_against_prepare_bound_immutable_feed"
        ),
        "prepare": _reference(prepare_snapshot, paths.root),
        "source_key_id": authority.source_item["key_id"],
        "source_action": profile["protocol"]["source_action"],
        "prepared_incoming_feed_runtime_path": relative_feed,
        "prepared_feed": _reference(prepared_feed, paths.active_root),
        "source_profile": {
            "path": EXPECTED_UPSTREAM["source_profile"]["path"],
            "sha256": EXPECTED_UPSTREAM["source_profile"]["expected_sha256"],
        },
        "source_implementation": {
            "path": EXPECTED_UPSTREAM["source_implementation"]["path"],
            "sha256": EXPECTED_UPSTREAM["source_implementation"]["expected_sha256"],
        },
        "terminal_for_recovery_v6_key": False,
    }


def _ensure_intent(
    profile: Mapping[str, Any],
    paths: SourceIngestCrossFreezePaths,
    authority: SourceIngestCrossFreezeAuthority,
    prepare_snapshot: registry.ArtifactSnapshot,
    prepared_feed: registry.ArtifactSnapshot,
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    payload = _intent_payload(
        profile, paths, authority, prepare_snapshot, prepared_feed
    )
    path = paths.intents / f"{authority.slot_id}.json"
    snapshot = _publish(
        path,
        _canonical_bytes(payload),
        root=paths.root,
        name="cross-freeze intent",
    )
    checked, replay = _strict_json(path, name="cross-freeze intent")
    if checked != payload or replay != snapshot:
        raise SourceIngestCrossFreezeIntegrityError(
            "Cross-freeze intent did not replay exactly"
        )
    return payload, snapshot


def _named_records(
    directory: Path, *, name: str
) -> dict[str, tuple[dict[str, Any], registry.ArtifactSnapshot]]:
    result: dict[str, tuple[dict[str, Any], registry.ArtifactSnapshot]] = {}
    for path in _strict_entries(directory, name=name):
        matched = SLOT_NAME.fullmatch(path.name)
        if matched is None:
            raise SourceIngestCrossFreezeIntegrityError(f"{name} filename changed")
        slot_id = matched.group("slot")
        if slot_id in result:
            raise SourceIngestCrossFreezeIntegrityError(f"{name} branched")
        result[slot_id] = _strict_json(path, name=name)
    return result


def _run_source_ingest(
    paths: SourceIngestCrossFreezePaths,
    authority: SourceIngestCrossFreezeAuthority,
    prepared_feed: registry.ArtifactSnapshot,
    *,
    now: datetime,
) -> source.SourceIngestResult:
    profile = deepcopy(authority.source_profile)
    runtime = deepcopy(profile["runtime"])
    runtime["incoming_feed"] = (
        prepared_feed.path.resolve().relative_to(paths.active_root.resolve()).as_posix()
    )
    profile["runtime"] = runtime
    try:
        result = source._ingest_source_locked(  # noqa: SLF001
            profile,
            runtime_root=paths.active_root,
            project_root=ROOT,
            now=now,
        )
    except Exception as exc:
        raise SourceIngestCrossFreezeIntegrityError(
            f"Pinned source ingest failed:{type(exc).__name__}:{exc}"
        ) from exc
    if result.source is None or result.status != "ready":
        raise SourceIngestCrossFreezeIntegrityError(
            "Pinned source ingest did not commit a ready snapshot"
        )
    return result


def _default_ensure_derived(
    paths: derived.SourceIngestDerivedPaths, now: datetime
) -> object:
    return derived._coordinate_source_ingest_derived_reservation(  # noqa: SLF001
        registry_root=paths.registry_root,
        active_root=paths.active_root,
        shadow_root=paths.shadow_root,
        clock=lambda: now,
    )


def _load_derived_evidence(
    paths: SourceIngestCrossFreezePaths,
    now: datetime,
    base: SourceIngestCrossFreezeAuthority,
    *,
    load_derived_authority: LoadDerivedAuthority | None,
) -> _DerivedEvidence | None:
    loader = load_derived_authority or derived._load_source_ingest_authority  # noqa: SLF001
    authority = loader(paths.derived, now)
    if authority is None:
        return None
    if (
        authority.source_item.get("key_id") != base.source_item.get("key_id")
        or authority.predecessor_source.snapshot_receipt
        != base.predecessor_source.snapshot_receipt
        or len(authority.derived_items)
        + len(authority.rebound_items)
        + len(authority.invalidated_items)
        > 4096
    ):
        raise SourceIngestCrossFreezeIntegrityError(
            "Derived authority does not match the prepared source slot"
        )
    derived_profile = derived.load_source_ingest_derived_profile()
    objects = derived._reservation_objects(  # noqa: SLF001
        derived_profile, paths.derived, authority
    )
    events, _, seen = derived._replay_events(  # noqa: SLF001
        derived_profile, paths.derived, authority, objects
    )
    if authority.slot_id not in objects or authority.slot_id not in seen:
        return None
    if set(objects) != {authority.slot_id} or seen != {authority.slot_id}:
        raise SourceIngestCrossFreezeIntegrityError(
            "Derived authority contains another source slot"
        )
    reservation_payload, reservation_snapshot = objects[authority.slot_id]
    matching_events = [
        event for event in events if event.get("slot_id") == authority.slot_id
    ]
    if len(matching_events) != 1:
        raise SourceIngestCrossFreezeIntegrityError(
            "Derived source slot lacks one published event"
        )
    event_payload = matching_events[0]
    event_path = paths.derived.events / (
        f"{event_payload['sequence_id']:020d}-{event_payload['entry_sha256']}.json"
    )
    checked_event, event_snapshot = derived._strict_json(  # noqa: SLF001
        event_path, name="cross-freeze matching derived event"
    )
    if checked_event != event_payload:
        raise SourceIngestCrossFreezeIntegrityError(
            "Derived event changed during cross-freeze replay"
        )
    return _DerivedEvidence(
        authority=authority,
        reservation_payload=reservation_payload,
        reservation_snapshot=reservation_snapshot,
        event_payload=event_payload,
        event_snapshot=event_snapshot,
    )


def _receipt_payload(
    profile: Mapping[str, Any],
    paths: SourceIngestCrossFreezePaths,
    base: SourceIngestCrossFreezeAuthority,
    prepare_snapshot: registry.ArtifactSnapshot,
    intent_snapshot: registry.ArtifactSnapshot,
    evidence: _DerivedEvidence,
) -> dict[str, object]:
    successor = evidence.authority.successor_source
    successor_receipt = successor.snapshot_receipt
    if successor_receipt is None:
        raise SourceIngestCrossFreezeIntegrityError(
            "Derived authority lost the committed source receipt"
        )
    derived_count = len(evidence.authority.derived_items)
    rebound_count = len(evidence.authority.rebound_items)
    invalidated_count = len(evidence.authority.invalidated_items)
    return {
        "schema_version": profile["protocol"]["receipt_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "slot_id": base.slot_id,
        "authority_scope": (
            "cross_freeze_source_snapshot_and_derived_batch_completion_only_"
            "not_recovery_v6_terminal"
        ),
        "prepare": _reference(prepare_snapshot, paths.root),
        "intent": _reference(intent_snapshot, paths.root),
        "source_key_id": base.source_item["key_id"],
        "source_natural_key": base.source_item["natural_key"],
        "source_snapshot_receipt": _source_reference(
            successor_receipt, paths.active_root
        ),
        "snapshot_sequence_id": successor.snapshot_sequence_id,
        "source_pointer": dict(
            evidence.authority.successor_receipt_payload["source_pointer"]
        ),
        "source_semantic_manifest": _source_reference(
            successor.semantic_manifest, paths.active_root
        ),
        "derived_slot_id": evidence.authority.slot_id,
        "derived_reservation": _reference(
            evidence.reservation_snapshot, paths.derived.root
        ),
        "derived_event": _reference(evidence.event_snapshot, paths.derived.root),
        "derived_key_count": derived_count,
        "rebound_item_count": rebound_count,
        "invalidated_item_count": invalidated_count,
        "rebound_existing_items_required": rebound_count > 0,
        "invalidated_existing_items_required": invalidated_count > 0,
        "source_snapshot_ingested": True,
        "derived_batch_classified": True,
        "terminal_for_recovery_v6_key": False,
        "implementation": _implementation_reference(),
        **_claims(),
    }


def _ensure_receipt(
    profile: Mapping[str, Any],
    paths: SourceIngestCrossFreezePaths,
    base: SourceIngestCrossFreezeAuthority,
    prepare_snapshot: registry.ArtifactSnapshot,
    intent_snapshot: registry.ArtifactSnapshot,
    evidence: _DerivedEvidence,
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    payload = _receipt_payload(
        profile,
        paths,
        base,
        prepare_snapshot,
        intent_snapshot,
        evidence,
    )
    path = paths.receipts / f"{base.slot_id}.json"
    snapshot = _publish(
        path,
        _canonical_bytes(payload),
        root=paths.root,
        name="cross-freeze completion receipt",
    )
    checked, replay = _strict_json(path, name="cross-freeze completion receipt")
    if checked != payload or replay != snapshot:
        raise SourceIngestCrossFreezeIntegrityError(
            "Cross-freeze completion receipt did not replay exactly"
        )
    return payload, snapshot


def _event_unsigned(
    profile: Mapping[str, Any],
    paths: SourceIngestCrossFreezePaths,
    base: SourceIngestCrossFreezeAuthority,
    receipt_snapshot: registry.ArtifactSnapshot,
    evidence: _DerivedEvidence,
    *,
    sequence_id: int,
    previous: str,
    recorded_at: str,
) -> dict[str, object]:
    return {
        "schema_version": profile["protocol"]["event_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "sequence_id": sequence_id,
        "previous_entry_sha256": previous,
        "event_type": profile["protocol"]["event_type"],
        "recorded_at_utc": recorded_at,
        "slot_id": base.slot_id,
        "source_key_id": base.source_item["key_id"],
        "snapshot_sequence_id": evidence.authority.successor_source.snapshot_sequence_id,
        "derived_slot_id": evidence.authority.slot_id,
        "derived_event": _reference(evidence.event_snapshot, paths.derived.root),
        "completion_receipt": _reference(receipt_snapshot, paths.root),
        "derived_key_count": len(evidence.authority.derived_items),
        "rebound_item_count": len(evidence.authority.rebound_items),
        "invalidated_item_count": len(evidence.authority.invalidated_items),
        "rebound_existing_items_required": bool(evidence.authority.rebound_items),
        "invalidated_existing_items_required": bool(
            evidence.authority.invalidated_items
        ),
        "source_snapshot_ingested": True,
        "derived_batch_classified": True,
        "terminal_for_recovery_v6_key": False,
        **_claims(),
    }


def _append_event(
    profile: Mapping[str, Any],
    paths: SourceIngestCrossFreezePaths,
    base: SourceIngestCrossFreezeAuthority,
    receipt_snapshot: registry.ArtifactSnapshot,
    evidence: _DerivedEvidence,
    *,
    sequence_id: int,
    previous: str,
    now: datetime,
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    body = _event_unsigned(
        profile,
        paths,
        base,
        receipt_snapshot,
        evidence,
        sequence_id=sequence_id,
        previous=previous,
        recorded_at=_utc_text(now),
    )
    payload = {**body, "entry_sha256": _sha256(_canonical_bytes(body))}
    path = paths.events / f"{sequence_id:020d}-{payload['entry_sha256']}.json"
    snapshot = _publish(
        path,
        _canonical_bytes(payload),
        root=paths.root,
        name="cross-freeze completion event",
    )
    return payload, snapshot


def _completion_state(
    profile: Mapping[str, Any],
    paths: SourceIngestCrossFreezePaths,
    base: SourceIngestCrossFreezeAuthority,
    prepare_snapshot: registry.ArtifactSnapshot,
    intent_snapshot: registry.ArtifactSnapshot,
    evidence: _DerivedEvidence,
) -> tuple[
    tuple[dict[str, Any], registry.ArtifactSnapshot] | None,
    tuple[dict[str, Any], registry.ArtifactSnapshot] | None,
]:
    prepares = _named_records(paths.prepares, name="cross-freeze prepares")
    intents = _named_records(paths.intents, name="cross-freeze intents")
    receipts = _named_records(paths.receipts, name="cross-freeze receipts")
    if set(prepares) != {base.slot_id} or set(intents) != {base.slot_id}:
        raise SourceIngestCrossFreezeIntegrityError(
            "Cross-freeze prepare/intent namespace branched or skipped"
        )
    if (
        prepares[base.slot_id][1] != prepare_snapshot
        or intents[base.slot_id][1] != intent_snapshot
    ):
        raise SourceIngestCrossFreezeIntegrityError(
            "Cross-freeze prepare/intent snapshot changed"
        )
    if set(receipts) - {base.slot_id}:
        raise SourceIngestCrossFreezeIntegrityError(
            "Cross-freeze receipt belongs to another slot"
        )
    receipt = receipts.get(base.slot_id)
    if receipt is not None:
        expected = _receipt_payload(
            profile,
            paths,
            base,
            prepare_snapshot,
            intent_snapshot,
            evidence,
        )
        if receipt[0] != expected:
            raise SourceIngestCrossFreezeIntegrityError(
                "Cross-freeze completion receipt semantics changed"
            )
    entries = _strict_entries(paths.events, name="cross-freeze events")
    if len(entries) > 1:
        raise SourceIngestCrossFreezeIntegrityError(
            "Cross-freeze event namespace branched"
        )
    event: tuple[dict[str, Any], registry.ArtifactSnapshot] | None = None
    if entries:
        if receipt is None:
            raise SourceIngestCrossFreezeIntegrityError(
                "Cross-freeze event is orphaned from its receipt"
            )
        matched = EVENT_NAME.fullmatch(entries[0].name)
        payload, snapshot = _strict_json(entries[0], name="cross-freeze event")
        body = dict(payload)
        entry = body.pop("entry_sha256", None)
        expected = _event_unsigned(
            profile,
            paths,
            base,
            receipt[1],
            evidence,
            sequence_id=1,
            previous=ZERO_HASH,
            recorded_at="",
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
            raise SourceIngestCrossFreezeIntegrityError(
                "Cross-freeze completion event semantics changed"
            )
        _parse_utc(payload.get("recorded_at_utc"), name="completion event time")
        event = (payload, snapshot)
    return receipt, event


def _committed_source(
    base: SourceIngestCrossFreezeAuthority | None,
    evidence: _DerivedEvidence | None,
) -> source.CanonicalSource | None:
    if evidence is not None:
        return evidence.authority.successor_source
    if base is not None and base.derived_authority is not None:
        return base.derived_authority.successor_source
    return None


def _write_status(
    profile: Mapping[str, Any],
    paths: SourceIngestCrossFreezePaths,
    *,
    now: datetime,
    status: str,
    reason: str,
    base: SourceIngestCrossFreezeAuthority | None,
    prepare: Path | None,
    intent: Path | None,
    receipt: Path | None,
    event: Path | None,
    evidence: _DerivedEvidence | None,
) -> None:
    committed_source = _committed_source(base, evidence)
    payload = {
        "schema_version": profile["protocol"]["status_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "recorded_at_utc": _utc_text(now),
        "status": status,
        "reason": reason,
        "slot_id": base.slot_id if base else None,
        "source_key_id": base.source_item["key_id"] if base else None,
        "prepare_path": str(prepare) if prepare else None,
        "intent_path": str(intent) if intent else None,
        "receipt_path": str(receipt) if receipt else None,
        "event_path": str(event) if event else None,
        "snapshot_sequence_id": (
            committed_source.snapshot_sequence_id
            if committed_source is not None
            else None
        ),
        "derived_key_count": len(evidence.authority.derived_items) if evidence else 0,
        "rebound_item_count": len(evidence.authority.rebound_items) if evidence else 0,
        "invalidated_item_count": (
            len(evidence.authority.invalidated_items) if evidence else 0
        ),
        "source_snapshot_ingested": committed_source is not None,
        "derived_batch_classified": evidence is not None,
        "terminal_for_recovery_v6_key": False,
        "cache_authority": False,
        **{name: False for name in FALSE_CLAIMS},
    }
    try:
        registry._atomic_cache(  # noqa: SLF001
            paths.status,
            _canonical_bytes(payload),
            root=paths.root,
            name="cross-freeze status",
        )
    except registry.EpochRegistryError as exc:
        raise SourceIngestCrossFreezeIntegrityError(str(exc)) from exc


def _result(
    paths: SourceIngestCrossFreezePaths,
    status: str,
    reason: str,
    base: SourceIngestCrossFreezeAuthority | None,
    *,
    prepare: Path | None = None,
    intent: Path | None = None,
    receipt: Path | None = None,
    event: Path | None = None,
    evidence: _DerivedEvidence | None = None,
) -> SourceIngestCrossFreezeResult:
    committed_source = _committed_source(base, evidence)
    return SourceIngestCrossFreezeResult(
        status=status,
        reason=reason,
        status_path=paths.status,
        prepare_path=prepare,
        intent_path=intent,
        receipt_path=receipt,
        event_path=event,
        source_key_id=str(base.source_item["key_id"]) if base else None,
        snapshot_sequence_id=(
            committed_source.snapshot_sequence_id
            if committed_source is not None
            else None
        ),
        derived_key_count=len(evidence.authority.derived_items) if evidence else 0,
        rebound_item_count=len(evidence.authority.rebound_items) if evidence else 0,
        invalidated_item_count=(
            len(evidence.authority.invalidated_items) if evidence else 0
        ),
        source_snapshot_ingested=committed_source is not None,
        derived_batch_classified=evidence is not None,
    )


def _coordinate_source_ingest_cross_freeze(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    registry_root: Path | None = None,
    active_root: Path | None = None,
    shadow_root: Path | None = None,
    clock: Callable[[], datetime] | None = None,
    fault_hook: FaultHook | None = None,
    ensure_derived: EnsureDerived | None = None,
    load_derived_authority: LoadDerivedAuthority | None = None,
) -> SourceIngestCrossFreezeResult:
    profile = load_source_ingest_cross_freeze_profile(config_path)
    paths = source_ingest_cross_freeze_paths(
        profile,
        registry_root=registry_root,
        active_root=active_root,
        shadow_root=shadow_root,
    )
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    if now.tzinfo is None or now.utcoffset() is None:
        raise SourceIngestCrossFreezeIntegrityError(
            "Cross-freeze clock must be timezone-aware"
        )
    handles: list[BinaryIO] = []
    base: SourceIngestCrossFreezeAuthority | None = None
    prepare_snapshot: registry.ArtifactSnapshot | None = None
    intent_snapshot: registry.ArtifactSnapshot | None = None
    try:
        handles = _acquire_locks(paths)
        base = _load_cross_freeze_authority(
            profile, paths, now, load_derived_authority=load_derived_authority
        )
        if base is None:
            if (
                _strict_entries(paths.prepares, name="cross-freeze prepares")
                or _strict_entries(paths.intents, name="cross-freeze intents")
                or _strict_entries(paths.receipts, name="cross-freeze receipts")
                or _strict_entries(paths.events, name="cross-freeze events")
            ):
                raise SourceIngestCrossFreezeIntegrityError(
                    "Adapter authority exists without one frozen source-ingest slot"
                )
            reason = "no frozen source-ingest manifest item is available"
            _write_status(
                profile,
                paths,
                now=now,
                status="waiting_for_frozen_source_ingest_item",
                reason=reason,
                base=None,
                prepare=None,
                intent=None,
                receipt=None,
                event=None,
                evidence=None,
            )
            return _result(paths, "waiting_for_frozen_source_ingest_item", reason, None)

        prepared_feed = _ensure_prepared_feed(profile, paths, base)
        _, prepare_snapshot = _ensure_prepare(profile, paths, base, prepared_feed)
        _call_fault_hook(fault_hook, "after_prepare")
        _, intent_snapshot = _ensure_intent(
            profile, paths, base, prepare_snapshot, prepared_feed
        )
        _call_fault_hook(fault_hook, "after_intent")

        evidence = _load_derived_evidence(
            paths,
            now,
            base,
            load_derived_authority=load_derived_authority,
        )
        receipt_entries = _named_records(paths.receipts, name="cross-freeze receipts")
        event_entries = _strict_entries(paths.events, name="cross-freeze events")
        if receipt_entries or event_entries:
            if evidence is None:
                raise SourceIngestCrossFreezeIntegrityError(
                    "Completion bytes outlived their matching derived publication"
                )
            receipt, event = _completion_state(
                profile,
                paths,
                base,
                prepare_snapshot,
                intent_snapshot,
                evidence,
            )
            if event is not None:
                reason = "the exact cross-freeze source/derived completion is current"
                _write_status(
                    profile,
                    paths,
                    now=now,
                    status="source_ingest_cross_freeze_current",
                    reason=reason,
                    base=base,
                    prepare=prepare_snapshot.path,
                    intent=intent_snapshot.path,
                    receipt=receipt[1].path if receipt else None,
                    event=event[1].path,
                    evidence=evidence,
                )
                return _result(
                    paths,
                    "source_ingest_cross_freeze_current",
                    reason,
                    base,
                    prepare=prepare_snapshot.path,
                    intent=intent_snapshot.path,
                    receipt=receipt[1].path if receipt else None,
                    event=event[1].path,
                    evidence=evidence,
                )
            if receipt is None:
                raise SourceIngestCrossFreezeIntegrityError(
                    "Cross-freeze persisted completion state is inconsistent"
                )
            _, event_snapshot = _append_event(
                profile,
                paths,
                base,
                receipt[1],
                evidence,
                sequence_id=1,
                previous=ZERO_HASH,
                now=now,
            )
            reason = "the exact completion receipt was forward-adopted into its event"
            _write_status(
                profile,
                paths,
                now=now,
                status="source_ingest_cross_freeze_event_forward_adopted",
                reason=reason,
                base=base,
                prepare=prepare_snapshot.path,
                intent=intent_snapshot.path,
                receipt=receipt[1].path,
                event=event_snapshot.path,
                evidence=evidence,
            )
            return _result(
                paths,
                "source_ingest_cross_freeze_event_forward_adopted",
                reason,
                base,
                prepare=prepare_snapshot.path,
                intent=intent_snapshot.path,
                receipt=receipt[1].path,
                event=event_snapshot.path,
                evidence=evidence,
            )

        if evidence is not None:
            receipt, event = _completion_state(
                profile,
                paths,
                base,
                prepare_snapshot,
                intent_snapshot,
                evidence,
            )
            if receipt is not None or event is not None:
                raise SourceIngestCrossFreezeIntegrityError(
                    "Unexpected completion state appeared during replay"
                )
            _, receipt_snapshot = _ensure_receipt(
                profile,
                paths,
                base,
                prepare_snapshot,
                intent_snapshot,
                evidence,
            )
            _call_fault_hook(fault_hook, "after_receipt")
            _, event_snapshot = _append_event(
                profile,
                paths,
                base,
                receipt_snapshot,
                evidence,
                sequence_id=1,
                previous=ZERO_HASH,
                now=now,
            )
            reason = (
                "an existing historical source snapshot and derived batch were adopted"
            )
            _write_status(
                profile,
                paths,
                now=now,
                status="source_ingest_cross_freeze_completed",
                reason=reason,
                base=base,
                prepare=prepare_snapshot.path,
                intent=intent_snapshot.path,
                receipt=receipt_snapshot.path,
                event=event_snapshot.path,
                evidence=evidence,
            )
            return _result(
                paths,
                "source_ingest_cross_freeze_completed",
                reason,
                base,
                prepare=prepare_snapshot.path,
                intent=intent_snapshot.path,
                receipt=receipt_snapshot.path,
                event=event_snapshot.path,
                evidence=evidence,
            )

        predecessor_sequence = base.predecessor_source.snapshot_sequence_id
        if base.current_source.snapshot_sequence_id == predecessor_sequence:
            _run_source_ingest(paths, base, prepared_feed, now=now)
        elif base.current_source.snapshot_sequence_id < predecessor_sequence:
            raise SourceIngestCrossFreezeIntegrityError(
                "Current source moved behind the frozen predecessor"
            )
        _call_fault_hook(fault_hook, "after_source_commit")

        _release_locks(handles)
        handles = []
        try:
            (ensure_derived or _default_ensure_derived)(paths.derived, now)
        except derived.SourceIngestDerivedBusyError as exc:
            raise SourceIngestCrossFreezeBusyError(str(exc)) from exc
        except derived.SourceIngestDerivedError as exc:
            raise SourceIngestCrossFreezeIntegrityError(
                f"Derived classification failed:{type(exc).__name__}:{exc}"
            ) from exc
        handles = _acquire_locks(paths)

        reloaded = _load_cross_freeze_authority(
            profile, paths, now, load_derived_authority=load_derived_authority
        )
        if reloaded is None or reloaded.slot_id != base.slot_id:
            raise SourceIngestCrossFreezeIntegrityError(
                "Frozen source slot changed while the derived coordinator ran"
            )
        base = reloaded
        prepared_feed = _ensure_prepared_feed(profile, paths, base)
        _, replay_prepare = _ensure_prepare(profile, paths, base, prepared_feed)
        _, replay_intent = _ensure_intent(
            profile, paths, base, replay_prepare, prepared_feed
        )
        if replay_prepare != prepare_snapshot or replay_intent != intent_snapshot:
            raise SourceIngestCrossFreezeIntegrityError(
                "Prepare or intent changed across the unlocked derived phase"
            )
        evidence = _load_derived_evidence(
            paths,
            now,
            base,
            load_derived_authority=load_derived_authority,
        )
        if evidence is None:
            reason = "source snapshot is committed; exact derived classification event is pending"
            _write_status(
                profile,
                paths,
                now=now,
                status="waiting_for_derived_batch_classification",
                reason=reason,
                base=base,
                prepare=prepare_snapshot.path,
                intent=intent_snapshot.path,
                receipt=None,
                event=None,
                evidence=None,
            )
            return _result(
                paths,
                "waiting_for_derived_batch_classification",
                reason,
                base,
                prepare=prepare_snapshot.path,
                intent=intent_snapshot.path,
            )
        receipt, event = _completion_state(
            profile,
            paths,
            base,
            prepare_snapshot,
            intent_snapshot,
            evidence,
        )
        if event is not None:
            raise SourceIngestCrossFreezeIntegrityError(
                "Completion event appeared without this coordinator publishing it"
            )
        if receipt is None:
            _, receipt_snapshot = _ensure_receipt(
                profile,
                paths,
                base,
                prepare_snapshot,
                intent_snapshot,
                evidence,
            )
            _call_fault_hook(fault_hook, "after_receipt")
        else:
            receipt_snapshot = receipt[1]
        _, event_snapshot = _append_event(
            profile,
            paths,
            base,
            receipt_snapshot,
            evidence,
            sequence_id=1,
            previous=ZERO_HASH,
            now=now,
        )
        reason = "one frozen feed was ingested or adopted and its exact derived batch classified"
        _write_status(
            profile,
            paths,
            now=now,
            status="source_ingest_cross_freeze_completed",
            reason=reason,
            base=base,
            prepare=prepare_snapshot.path,
            intent=intent_snapshot.path,
            receipt=receipt_snapshot.path,
            event=event_snapshot.path,
            evidence=evidence,
        )
        return _result(
            paths,
            "source_ingest_cross_freeze_completed",
            reason,
            base,
            prepare=prepare_snapshot.path,
            intent=intent_snapshot.path,
            receipt=receipt_snapshot.path,
            event=event_snapshot.path,
            evidence=evidence,
        )
    except SourceIngestCrossFreezeBusyError:
        raise
    except SourceIngestCrossFreezeError as exc:
        if handles:
            try:
                _write_status(
                    profile,
                    paths,
                    now=now,
                    status="blocked_integrity",
                    reason=f"{type(exc).__name__}:{exc}",
                    base=base,
                    prepare=prepare_snapshot.path if prepare_snapshot else None,
                    intent=intent_snapshot.path if intent_snapshot else None,
                    receipt=None,
                    event=None,
                    evidence=None,
                )
            except SourceIngestCrossFreezeError:
                pass
        raise
    except Exception as exc:
        raise SourceIngestCrossFreezeIntegrityError(
            f"Cross-freeze coordination failed:{type(exc).__name__}:{exc}"
        ) from exc
    finally:
        if handles:
            _release_locks(handles)


def coordinate_source_ingest_cross_freeze(
    *, config_path: Path = DEFAULT_CONFIG_PATH
) -> SourceIngestCrossFreezeResult:
    """Run one machine-only cross-freeze source/derived coordination poll."""

    return _coordinate_source_ingest_cross_freeze(config_path=config_path)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result = coordinate_source_ingest_cross_freeze(config_path=args.config)
    print(
        json.dumps(
            {
                "status": result.status,
                "reason": result.reason,
                "status_path": str(result.status_path),
                "prepare_path": str(result.prepare_path)
                if result.prepare_path
                else None,
                "intent_path": str(result.intent_path) if result.intent_path else None,
                "receipt_path": str(result.receipt_path)
                if result.receipt_path
                else None,
                "event_path": str(result.event_path) if result.event_path else None,
                "source_key_id": result.source_key_id,
                "snapshot_sequence_id": result.snapshot_sequence_id,
                "derived_key_count": result.derived_key_count,
                "rebound_item_count": result.rebound_item_count,
                "invalidated_item_count": result.invalidated_item_count,
                "source_snapshot_ingested": result.source_snapshot_ingested,
                "derived_batch_classified": result.derived_batch_classified,
                "terminal_for_recovery_v6_key": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
