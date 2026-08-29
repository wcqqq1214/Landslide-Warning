"""Reserve outcome keys derived by one committed source-snapshot edge.

The sidecar is deliberately read-only with respect to the source, frozen
manifest, recovery and live-ledger namespaces.  It adopts only the exact
immutable N -> N+1 source edge selected by a frozen source-ingest item, even
when the verified source chain has advanced, and publishes one content-addressed
batch followed by one append-only event.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
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
from monitoring import ootang_epoch_workset_inventory as inventory  # noqa: E402
from monitoring import ootang_epoch_workset_manifest as manifest  # noqa: E402
from monitoring import ootang_epoch_workset_recovery as recovery  # noqa: E402
from monitoring import ootang_live_source as source  # noqa: E402


DEFAULT_CONFIG_PATH = (
    ROOT / "config" / "ootang_epoch_source_ingest_derived_reservation.v1.json"
)
DEFAULT_CONFIG_SHA256 = (
    "916b72d8cf2726afcde289f58b5b8f9729c382bb93ee03bad3b6f8d34caadb18"
)
IMPLEMENTATION_LOGICAL_PATH = (
    "code/monitoring/ootang_epoch_source_ingest_derived_reservation.py"
)
MAX_CONTROL_BYTES = 4 * 1024 * 1024
ZERO_HASH = "0" * 64
LOCK_ORDER = recovery.LOCK_ORDER
OBJECT_NAME = re.compile(r"^(?P<digest>[0-9a-f]{64})\.json$")
EVENT_NAME = re.compile(r"^(?P<sequence>[0-9]{20})-(?P<entry>[0-9a-f]{64})\.json$")

EXPECTED_RUNTIME = {
    "registry_root": "runtime/ootang_epoch_registry_v1",
    "recovery_namespace": "workset_recovery_v1",
    "namespace": "source_ingest_derived_reservation_v1",
    "reservation_objects": "reservations/sha256",
    "events": "events",
    "status": "status.json",
    "manager_lock": "manager.lock",
    "active_root": "runtime/ootang_prequential_live_v1",
    "shadow_root": "runtime/ootang_prequential_calibration_shadow_v1",
}
EXPECTED_PROTOCOL = {
    "reservation_schema_version": ("ootang_epoch_source_ingest_derived_reservation_v1"),
    "event_schema_version": ("ootang_epoch_source_ingest_derived_reservation_event_v1"),
    "status_schema_version": (
        "ootang_epoch_source_ingest_derived_reservation_status_v1"
    ),
    "event_type": "epoch_source_ingest_derived_outcome_keys_reserved",
    "source_successor": "source_snapshot_ingested",
    "derived_successor": "outcome_materialized",
    "canonical_json": "utf8_sort_keys_compact_no_nan_trailing_lf",
    "initial_previous_entry_sha256": ZERO_HASH,
    "maximum_items": 4096,
    "maximum_control_bytes": MAX_CONTROL_BYTES,
    "poll_policy": (
        "adopt_at_most_one_exact_next_published_source_snapshot_and_reserve_"
        "its_complete_derived_outcome_batch"
    ),
}
TRUE_CAPABILITIES = (
    "machine_only",
    "post_ingest_snapshot_adoption_implemented",
    "create_only_content_addressed_reservations_implemented",
    "append_only_reservation_events_implemented",
    "crash_forward_adoption_implemented",
    "complete_per_snapshot_derived_outcome_batch_recorded",
)
FALSE_CLAIMS = (
    "source_ingest_executed",
    "source_ingest_writer_implemented",
    "outcome_materialization_performed",
    "materializer_receipt_consumed",
    "source_parent_recovery_receipt_created",
    "terminal_for_recovery_v6_key",
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


class SourceIngestDerivedError(RuntimeError):
    """Base error for source-ingest derived-key reservation."""


class SourceIngestDerivedConfigError(SourceIngestDerivedError):
    """The reviewed profile or one of its direct upstreams changed."""


class SourceIngestDerivedIntegrityError(SourceIngestDerivedError):
    """A source edge, frozen authority or persisted sidecar failed closed."""


class SourceIngestDerivedBusyError(SourceIngestDerivedError):
    """A surviving coordinator lock is owned by another machine process."""


@dataclass(frozen=True)
class SourceIngestDerivedPaths:
    registry_root: Path
    root: Path
    reservation_objects: Path
    events: Path
    status: Path
    manager_lock: Path
    active_root: Path
    shadow_root: Path
    cycle_lock: Path
    replay_lock: Path
    shadow_lock: Path
    recovery: recovery.RecoveryPaths


@dataclass(frozen=True)
class SourceIngestDerivedResult:
    status: str
    reason: str
    status_path: Path
    reservation_path: Path | None = None
    event_path: Path | None = None
    source_key_id: str | None = None
    derived_key_count: int = 0
    rebound_item_count: int = 0
    invalidated_item_count: int = 0
    source_ingest_writer_implemented: bool = False
    terminal_transition_closure_implemented: bool = False
    derived_future_work_reservation_implemented: bool = False
    old_epoch_drained: bool = False


@dataclass(frozen=True)
class SourceIngestDerivedAuthority:
    reservation: recovery.Reservation
    ordered_items: tuple[Mapping[str, Any], ...]
    dependency_graph_sha256: str
    source_item: Mapping[str, Any]
    predecessor_source: source.CanonicalSource
    successor_source: source.CanonicalSource
    successor_receipt_payload: Mapping[str, Any]
    derived_items: tuple[Mapping[str, Any], ...]
    rebound_items: tuple[Mapping[str, Any], ...]
    invalidated_items: tuple[Mapping[str, Any], ...]
    slot_id: str


LoadAuthority = Callable[
    [SourceIngestDerivedPaths, datetime], SourceIngestDerivedAuthority | None
]


def _canonical_bytes(value: object) -> bytes:
    try:
        return registry._canonical_bytes(value)  # type: ignore[arg-type]  # noqa: SLF001
    except (TypeError, ValueError) as exc:
        raise SourceIngestDerivedIntegrityError("Value is not canonical JSON") from exc


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _claims() -> dict[str, bool]:
    return {
        **{name: True for name in TRUE_CAPABILITIES},
        **{name: False for name in FALSE_CLAIMS},
    }


def _exact(value: object, keys: set[str], *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise SourceIngestDerivedIntegrityError(f"{name} keys changed")
    return value


def _text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise SourceIngestDerivedIntegrityError(f"{name} changed")
    return value


def _hash(value: object, *, name: str) -> str:
    text = _text(value, name=name)
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise SourceIngestDerivedIntegrityError(f"{name} is not a lowercase SHA-256")
    return text


def _positive_int(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SourceIngestDerivedIntegrityError(f"{name} changed")
    return value


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SourceIngestDerivedIntegrityError(
            "Source-derived reservation clock must be timezone-aware"
        )
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: object, *, name: str) -> datetime:
    try:
        return registry._utc_timestamp(value, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise SourceIngestDerivedIntegrityError(str(exc)) from exc


def _child(root: Path, relative: str, *, name: str) -> Path:
    try:
        return registry._contained(root, relative, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise SourceIngestDerivedConfigError(str(exc)) from exc


def _strict_json(
    path: Path, *, name: str
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    try:
        return manifest._read_json(  # noqa: SLF001
            path, name=name, maximum_bytes=MAX_CONTROL_BYTES
        )
    except manifest.WorksetManifestError as exc:
        raise SourceIngestDerivedIntegrityError(str(exc)) from exc


def _strict_entries(directory: Path, *, name: str) -> tuple[Path, ...]:
    try:
        return manifest._strict_entries(directory, name=name)  # noqa: SLF001
    except manifest.WorksetManifestError as exc:
        raise SourceIngestDerivedIntegrityError(str(exc)) from exc


def _publish(
    path: Path, raw: bytes, *, root: Path, name: str
) -> registry.ArtifactSnapshot:
    if len(raw) > MAX_CONTROL_BYTES:
        raise SourceIngestDerivedIntegrityError(
            f"{name} exceeds the reviewed control-byte limit"
        )
    try:
        return drain._publish_once_durable(path, raw, root=root, name=name)  # noqa: SLF001
    except drain.EpochDrainError as exc:
        raise SourceIngestDerivedIntegrityError(str(exc)) from exc


def load_source_ingest_derived_profile(
    path: Path = DEFAULT_CONFIG_PATH, *, project_root: Path = ROOT
) -> dict[str, Any]:
    root = project_root.resolve()
    resolved = path if path.is_absolute() else root / path
    if root != ROOT.resolve() or resolved.resolve() != DEFAULT_CONFIG_PATH.resolve():
        raise SourceIngestDerivedConfigError(
            "Only the reviewed default source-derived profile is accepted"
        )
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            resolved, name="source-derived profile", maximum_bytes=MAX_CONTROL_BYTES
        )
        profile = registry._decode_json(  # noqa: SLF001
            snapshot.raw, name="source-derived profile"
        )
    except registry.EpochRegistryError as exc:
        raise SourceIngestDerivedConfigError(str(exc)) from exc
    if snapshot.sha256 != DEFAULT_CONFIG_SHA256:
        raise SourceIngestDerivedConfigError("Source-derived profile SHA-256 changed")
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
        name="source-derived profile",
    )
    if (
        profile["schema_version"]
        != "ootang_epoch_source_ingest_derived_reservation_profile_v1"
        or profile["profile_id"] != "ootang-epoch-source-ingest-derived-reservation-v1"
        or profile["case"] != "ootang"
        or profile["formal_warning_output"] is not False
        or profile["default_pipeline_member"] is not False
        or profile["runtime"] != EXPECTED_RUNTIME
        or profile["protocol"] != EXPECTED_PROTOCOL
        or profile["engineering_capabilities"] != _claims()
        or not isinstance(profile["upstream"], dict)
    ):
        raise SourceIngestDerivedConfigError("Source-derived profile semantics changed")
    for binding in profile["upstream"].values():
        if not isinstance(binding, dict) or set(binding) != {
            "path",
            "expected_sha256",
        }:
            raise SourceIngestDerivedConfigError(
                "Source-derived upstream binding changed"
            )
        upstream = _child(root, binding["path"], name="source-derived upstream")
        try:
            actual = registry._read_regular(  # noqa: SLF001
                upstream,
                name="source-derived upstream",
                maximum_bytes=64 * 1024 * 1024,
            )
        except registry.EpochRegistryError as exc:
            raise SourceIngestDerivedConfigError(str(exc)) from exc
        if actual.sha256 != binding["expected_sha256"]:
            raise SourceIngestDerivedConfigError(
                f"Frozen source-derived upstream changed:{binding['path']}"
            )
    profile["_profile_sha256"] = snapshot.sha256
    return profile


def source_ingest_derived_paths(
    profile: Mapping[str, Any],
    *,
    registry_root: Path | None = None,
    active_root: Path | None = None,
    shadow_root: Path | None = None,
) -> SourceIngestDerivedPaths:
    registry_path = (
        registry_root or ROOT / profile["runtime"]["registry_root"]
    ).resolve()
    recovery_root = _child(
        registry_path,
        profile["runtime"]["recovery_namespace"],
        name="recovery namespace",
    )
    root = _child(
        recovery_root, profile["runtime"]["namespace"], name="source-derived root"
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
    return SourceIngestDerivedPaths(
        registry_root=registry_path,
        root=root,
        reservation_objects=_child(
            root,
            profile["runtime"]["reservation_objects"],
            name="source-derived reservation objects",
        ),
        events=_child(root, profile["runtime"]["events"], name="source-derived events"),
        status=_child(root, profile["runtime"]["status"], name="source-derived status"),
        manager_lock=_child(
            registry_path, profile["runtime"]["manager_lock"], name="manager lock"
        ),
        active_root=active,
        shadow_root=shadow,
        cycle_lock=_child(active, "prequential_cycle.lock", name="cycle lock"),
        replay_lock=_child(active, "issue_replay.lock", name="replay lock"),
        shadow_lock=_child(shadow, "runner.lock", name="shadow lock"),
        recovery=recovery_paths,
    )


def _reference(snapshot: registry.ArtifactSnapshot, root: Path) -> dict[str, object]:
    try:
        relative = snapshot.path.relative_to(root).as_posix()
    except ValueError as exc:
        raise SourceIngestDerivedIntegrityError(
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
            name="source-derived implementation",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
    except registry.EpochRegistryError as exc:
        raise SourceIngestDerivedIntegrityError(str(exc)) from exc
    return {
        "path": IMPLEMENTATION_LOGICAL_PATH,
        "sha256": snapshot.sha256,
        "size_bytes": snapshot.size_bytes,
    }


def _source_artifact_reference(
    artifact: source.ArtifactRef, root: Path
) -> dict[str, object]:
    try:
        relative = artifact.path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise SourceIngestDerivedIntegrityError(
            "Source artifact escaped the active runtime"
        ) from exc
    return {
        "path": relative,
        "sha256": artifact.sha256,
        "size_bytes": artifact.size_bytes,
    }


def _load_source_from_snapshot_receipt(
    profile: dict[str, Any],
    *,
    root: Path,
    receipt_artifact: source.ArtifactRef,
    receipt_payload: Mapping[str, Any],
) -> source.CanonicalSource:
    objects = source._runtime_path(profile, "objects", root=root)  # noqa: SLF001
    try:
        pointer_artifact = source._content_object_from_payload(  # noqa: SLF001
            receipt_payload["source_pointer"],
            name="source-derived snapshot pointer",
            directory=objects,
            suffix="source-pointer.json",
        )
        pointer_raw = source._read_verified_artifact(  # noqa: SLF001
            pointer_artifact, name="source-derived snapshot pointer"
        )
        pointer = source._decode_json(  # noqa: SLF001
            pointer_raw, name="source-derived snapshot pointer"
        )
        semantic = source._content_object_from_payload(  # noqa: SLF001
            pointer["semantic_manifest"],
            name="source-derived snapshot semantic manifest",
            directory=objects,
            suffix="source-manifest.json",
        )
        activation = source._artifact_from_payload(  # noqa: SLF001
            pointer["activation_source_manifest"],
            name="source-derived snapshot activation",
        )
        watermark = source._canonical_date(  # noqa: SLF001
            pointer["maximum_complete_finalized_date"],
            name="source-derived snapshot watermark",
        )
        loaded = source._load_semantic_source(  # noqa: SLF001
            profile,
            semantic=semantic,
            activation=activation,
            objects=objects,
            expected_watermark=watermark,
            expected_source_id=pointer["outcome_source_id"],
            expected_exported_at=pointer["updated_at_utc"],
            project_root=ROOT,
        )
        dataset = source._content_object_from_payload(  # noqa: SLF001
            pointer["dataset"],
            name="source-derived snapshot dataset",
            directory=objects,
            suffix="source.json",
        )
        if loaded.dataset != dataset:
            raise SourceIngestDerivedIntegrityError(
                "Historical source dataset binding changed"
            )
        heads, histories = source._load_revision_heads(  # noqa: SLF001
            pointer["revision_heads"], source=loaded, objects=objects
        )
    except SourceIngestDerivedError:
        raise
    except Exception as exc:
        raise SourceIngestDerivedIntegrityError(
            f"Historical source replay failed:{type(exc).__name__}:{exc}"
        ) from exc
    sequence_id = _positive_int(
        receipt_payload.get("snapshot_sequence_id"),
        name="historical source snapshot sequence",
    )
    return replace(
        loaded,
        revision_heads=heads,
        revision_ids_by_date=histories,
        snapshot_receipt=receipt_artifact,
        snapshot_sequence_id=sequence_id,
    )


def _load_predecessor_source(
    profile: dict[str, Any],
    *,
    root: Path,
    successor_payload: Mapping[str, Any],
) -> source.CanonicalSource:
    objects = source._runtime_path(profile, "objects", root=root)  # noqa: SLF001
    predecessor_value = successor_payload.get("predecessor_snapshot_receipt")
    if not isinstance(predecessor_value, dict):
        raise SourceIngestDerivedIntegrityError(
            "Successor source snapshot lost its predecessor"
        )
    try:
        receipt_artifact = source._content_object_from_payload(  # noqa: SLF001
            predecessor_value,
            name="source-derived predecessor receipt",
            directory=objects,
            suffix="source-snapshot-receipt.json",
        )
        receipt_payload = source._load_json_artifact(  # noqa: SLF001
            receipt_artifact, name="source-derived predecessor receipt"
        )
    except Exception as exc:
        raise SourceIngestDerivedIntegrityError(
            f"Predecessor receipt replay failed:{type(exc).__name__}:{exc}"
        ) from exc
    return _load_source_from_snapshot_receipt(
        profile,
        root=root,
        receipt_artifact=receipt_artifact,
        receipt_payload=receipt_payload,
    )


def _exact_source_snapshot_child(
    objects: Path,
    *,
    predecessor_sequence: int,
    predecessor_sha256: str,
    outcome_source_id: str,
) -> tuple[source.ArtifactRef, Mapping[str, Any]] | None:
    """Find the immutable N+1 receipt after the full registry passed replay."""

    try:
        candidates = sorted(objects.glob("*.source-snapshot-receipt.json"))
    except OSError as exc:
        raise SourceIngestDerivedIntegrityError(
            "Cannot enumerate source snapshot receipts"
        ) from exc
    matched: list[tuple[source.ArtifactRef, Mapping[str, Any]]] = []
    for index, path in enumerate(candidates):
        try:
            artifact = source._artifact_from_object_path(  # noqa: SLF001
                path,
                objects=objects,
                suffix="source-snapshot-receipt.json",
                name=f"source-derived snapshot receipt[{index}]",
            )
            payload = source._load_json_artifact(  # noqa: SLF001
                artifact, name=f"source-derived snapshot receipt[{index}]"
            )
        except Exception as exc:
            raise SourceIngestDerivedIntegrityError(
                f"Source snapshot receipt replay failed:{type(exc).__name__}:{exc}"
            ) from exc
        predecessor = payload.get("predecessor_snapshot_receipt")
        if (
            payload.get("snapshot_sequence_id") == predecessor_sequence + 1
            and payload.get("outcome_source_id") == outcome_source_id
            and isinstance(predecessor, Mapping)
            and predecessor.get("sha256") == predecessor_sha256
        ):
            matched.append((artifact, payload))
    if len(matched) > 1:
        raise SourceIngestDerivedIntegrityError(
            "Frozen predecessor has multiple exact source-snapshot children"
        )
    return matched[0] if matched else None


def _changed_rows(
    predecessor: source.CanonicalSource, successor: source.CanonicalSource
) -> list[dict[str, object]]:
    old_records = tuple(predecessor.records)
    new_records = tuple(successor.records)
    if len(new_records) < len(old_records):
        raise SourceIngestDerivedIntegrityError(
            "Successor source rolled back predecessor history"
        )
    changed: list[dict[str, object]] = []
    known = {day: set(values) for day, values in predecessor.revision_ids_by_date}
    for old, new in zip(old_records, new_records, strict=False):
        if old.day != new.day:
            raise SourceIngestDerivedIntegrityError(
                "Successor source changed predecessor date order"
            )
        if old == new:
            continue
        if old.revision_id == new.revision_id:
            raise SourceIngestDerivedIntegrityError(
                "Successor source reused a revision id with changed semantics"
            )
        if new.revision_id in known.get(new.day.isoformat(), set()):
            raise SourceIngestDerivedIntegrityError(
                "Successor source rolled back a known revision"
            )
        changed.append(inventory._source_record_payload(new))  # noqa: SLF001
    changed.extend(
        inventory._source_record_payload(record)  # noqa: SLF001
        for record in new_records[len(old_records) :]
    )
    return changed


def _manifest_family_records(
    reservation: recovery.Reservation, family: str
) -> tuple[Mapping[str, Any], ...]:
    families = reservation.manifest.get("families")
    if not isinstance(families, list):
        raise SourceIngestDerivedIntegrityError("Manifest families changed")
    matched = [value for value in families if value.get("family") == family]
    if len(matched) != 1:
        raise SourceIngestDerivedIntegrityError(
            "Manifest outcome family is missing or ambiguous"
        )
    authority = matched[0].get("authority")
    records = authority.get("records") if isinstance(authority, dict) else None
    if not isinstance(records, list):
        return ()
    if not all(isinstance(value, dict) for value in records):
        raise SourceIngestDerivedIntegrityError(
            "Manifest outcome family records changed"
        )
    return tuple(records)


def _source_artifacts_for_record(
    paths: SourceIngestDerivedPaths,
    source_profile: Mapping[str, Any],
    current: source.CanonicalSource,
    target: date,
    *,
    historical_pointer: source.ArtifactRef,
) -> tuple[inventory.ArtifactRef, ...]:
    public_pointer = source._runtime_path(  # noqa: SLF001
        source_profile, "current_source_pointer", root=paths.active_root
    )
    values = [
        inventory.ArtifactRef(
            role="current_source_pointer",
            root="active",
            path=public_pointer.relative_to(paths.active_root).as_posix(),
            sha256=historical_pointer.sha256,
            size_bytes=historical_pointer.size_bytes,
        ),
        inventory._artifact(  # noqa: SLF001
            source._runtime_path(  # noqa: SLF001
                source_profile, "activation_source_manifest", root=paths.active_root
            ),
            role="activation_source_manifest",
            root_label="active",
            root=paths.active_root,
            maximum_bytes=64 * 1024 * 1024,
        ),
        inventory._artifact(  # noqa: SLF001
            current.semantic_manifest.path,
            role="current_source_semantic_manifest",
            root_label="active",
            root=paths.active_root,
            maximum_bytes=64 * 1024 * 1024,
        ),
    ]
    for record, head in zip(current.records, current.revision_heads, strict=True):
        if record.day == target:
            values.append(
                inventory._artifact(  # noqa: SLF001
                    head.path,
                    role="current_source_revision_head",
                    root_label="active",
                    root=paths.active_root,
                    maximum_bytes=64 * 1024 * 1024,
                )
            )
            break
    if current.snapshot_receipt is None:
        raise SourceIngestDerivedIntegrityError(
            "Successor source lacks its snapshot receipt"
        )
    values.append(
        inventory._artifact(  # noqa: SLF001
            current.snapshot_receipt.path,
            role="current_source_snapshot_receipt",
            root_label="active",
            root=paths.active_root,
            maximum_bytes=64 * 1024 * 1024,
        )
    )
    return tuple(values)


def _frozen_outstanding(
    ordered: Sequence[Mapping[str, Any]],
) -> tuple[str, str] | None:
    rows: list[tuple[str, str]] = []
    for item in ordered:
        if item.get("family") != "live_outstanding":
            continue
        authority = item.get("authority")
        if not isinstance(authority, Mapping):
            continue
        if authority.get("record_type") != "outstanding_live_lifecycle":
            # The same family also contains anchor-confirmation repair work.
            # It is not an outstanding source-selection authority.
            continue
        target = authority.get("target_date")
        seal_event = authority.get("seal_event")
        if not isinstance(target, str) or not isinstance(seal_event, Mapping):
            raise SourceIngestDerivedIntegrityError(
                "Frozen outstanding item lost its seal event"
            )
        seal_target = seal_event.get("target_date")
        seal = seal_event.get("entry_sha256")
        if (
            seal_event.get("event_type") != "issue_batch_sealed"
            or seal_target != target
        ):
            raise SourceIngestDerivedIntegrityError(
                "Frozen outstanding item seal scope changed"
            )
        rows.append((target, _hash(seal, name="frozen outstanding seal")))
    if len(rows) > 1:
        raise SourceIngestDerivedIntegrityError(
            "Frozen manifest has ambiguous outstanding source targets"
        )
    return rows[0] if rows else None


def _seal_for_target(frozen_events: Sequence[object], target: date) -> str:
    target_text = target.isoformat()
    seal = next(
        (
            event
            for event in frozen_events
            if getattr(event, "event_type", None) == "issue_batch_sealed"
            and getattr(event, "target_date", None) == target_text
        ),
        None,
    )
    return (
        _hash(getattr(seal, "entry_sha256", None), name="frozen issue seal")
        if seal is not None
        else ZERO_HASH
    )


def _prospective_outcome_items(
    paths: SourceIngestDerivedPaths,
    reservation: recovery.Reservation,
    ordered: Sequence[Mapping[str, Any]],
    source_item: Mapping[str, Any],
    successor: source.CanonicalSource,
    successor_receipt_payload: Mapping[str, Any],
    *,
    projection: object,
    frozen_events: Sequence[object],
) -> tuple[Mapping[str, Any], ...]:
    tip = reservation.manifest["frozen_live_upper_tip"]
    old_epoch = _text(tip["old_live_epoch_id"], name="frozen live epoch")
    projection_epoch = _text(
        getattr(projection, "epoch_id", None), name="frozen projection epoch"
    )
    if projection_epoch != old_epoch:
        raise SourceIngestDerivedIntegrityError(
            "Frozen live projection epoch differs from the manifest"
        )
    revision_ids = getattr(projection, "revision_ids", None)
    if not isinstance(revision_ids, Mapping):
        raise SourceIngestDerivedIntegrityError(
            "Frozen live projection revision ids changed"
        )
    outstanding = getattr(projection, "outstanding_target_date", None)
    if outstanding is not None and not isinstance(outstanding, date):
        raise SourceIngestDerivedIntegrityError(
            "Frozen live outstanding target changed"
        )
    last_finalized = getattr(projection, "last_finalized_date", None)
    if not isinstance(last_finalized, date):
        raise SourceIngestDerivedIntegrityError("Frozen live finalized date changed")

    manifest_outstanding = _frozen_outstanding(ordered)
    if outstanding is None:
        if manifest_outstanding is not None:
            raise SourceIngestDerivedIntegrityError(
                "Manifest outstanding item differs from the frozen live projection"
            )
    else:
        expected_outstanding = (
            outstanding.isoformat(),
            _seal_for_target(frozen_events, outstanding),
        )
        if manifest_outstanding != expected_outstanding:
            raise SourceIngestDerivedIntegrityError(
                "Manifest outstanding seal differs from the frozen live projection"
            )

    records_by_date = {record.day: record for record in successor.records}
    existing_records = _manifest_family_records(reservation, "outcome_revision")
    existing_identities = {
        (record.get("target_date"), record.get("tip_source_revision_id"))
        for record in existing_records
        if record.get("record_type") == "outcome_receipt_chain"
    }
    pending_by_date: dict[date, tuple[str, str, str]] = {}
    pending_items: list[Mapping[str, Any]] = []
    for item in ordered:
        authority = item.get("authority")
        if (
            item.get("family") == "outcome_revision"
            and isinstance(authority, Mapping)
            and authority.get("record_type") == "outcome_receipt_chain"
        ):
            target = date.fromisoformat(
                _text(authority.get("target_date"), name="pending outcome date")
            )
            if target in pending_by_date:
                raise SourceIngestDerivedIntegrityError(
                    "Frozen manifest has multiple pending outcome tips for one date"
                )
            pending = (
                _text(item.get("natural_key"), name="pending outcome key"),
                _text(
                    authority.get("tip_source_revision_id"),
                    name="pending outcome revision",
                ),
                _hash(
                    authority.get("tip_exact_outcome_sha256"),
                    name="pending exact outcome",
                ),
            )
            pending_by_date[target] = pending
            pending_items.append(item)
    pending_items.sort(
        key=lambda item: (
            str(item["authority"].get("target_date", "")),
            str(item.get("natural_key", "")),
        )
    )
    prior_key = (
        _text(pending_items[-1].get("natural_key"), name="pending outcome tip")
        if pending_items
        else None
    )

    candidate_specs: list[
        tuple[str, date, object, str | None, str | None, str | None]
    ] = []
    for target_text in sorted(revision_ids):
        target = date.fromisoformat(
            _text(target_text, name="ledger-known outcome date")
        )
        record = records_by_date.get(target)
        if record is None:
            if target <= successor.watermark:
                raise SourceIngestDerivedIntegrityError(
                    "Successor source lost a ledger-known outcome date"
                )
            continue
        known = revision_ids[target_text]
        if not isinstance(known, Mapping) or not known:
            raise SourceIngestDerivedIntegrityError(
                "Frozen ledger-known revision map changed"
            )
        if record.revision_id not in known:
            previous_revision = _text(
                tuple(known)[-1], name="previous ledger-known source revision"
            )
            candidate_specs.append(
                (
                    "revision",
                    target,
                    record,
                    previous_revision,
                    _hash(
                        known[previous_revision],
                        name="previous ledger-known exact outcome",
                    ),
                    None,
                )
            )
    if outstanding is not None and outstanding in records_by_date:
        candidate_specs.append(
            (
                "outstanding",
                outstanding,
                records_by_date[outstanding],
                None,
                None,
                None,
            )
        )
    if outstanding is None:
        target = last_finalized + timedelta(days=1)
        while target <= successor.watermark:
            record = records_by_date.get(target)
            if record is None:
                raise SourceIngestDerivedIntegrityError(
                    "Successor source skipped a contiguous backfill date"
                )
            candidate_specs.append(("backfill", target, record, None, None, None))
            target += timedelta(days=1)

    source_profile = source.load_deploy_profile()
    objects = source._runtime_path(  # noqa: SLF001
        source_profile, "objects", root=paths.active_root
    )
    try:
        historical_pointer = source._content_object_from_payload(  # noqa: SLF001
            successor_receipt_payload.get("source_pointer"),
            name="source-derived successor pointer",
            directory=objects,
            suffix="source-pointer.json",
        )
    except Exception as exc:
        raise SourceIngestDerivedIntegrityError(
            f"Successor pointer replay failed:{type(exc).__name__}:{exc}"
        ) from exc

    result: list[Mapping[str, Any]] = []
    result_keys: set[str] = set()
    for (
        selection_kind,
        target,
        record,
        previous_revision,
        previous_outcome,
        predecessor_key,
    ) in candidate_specs:
        revision = _text(
            getattr(record, "revision_id", None), name="selected source revision"
        )
        if (target.isoformat(), revision) in existing_identities:
            continue
        pending = pending_by_date.get(target)
        if pending is not None and revision != pending[1]:
            selection_kind = "revision"
            predecessor_key, previous_revision, previous_outcome = pending
        seal_hash = _seal_for_target(frozen_events, target)
        natural_key = inventory._key(  # noqa: SLF001
            "outcome_revision",
            old_epoch,
            target.isoformat(),
            revision,
            seal_hash,
            successor.outcome_source_id,
        )
        dependencies = tuple(
            dict.fromkeys(
                value
                for value in (
                    prior_key,
                    predecessor_key,
                    _text(source_item.get("natural_key"), name="source-ingest key"),
                )
                if value is not None
            )
        )
        authority = {
            "record_type": "machine_selected_source_outcome",
            "selection_kind": selection_kind,
            "target_date": target.isoformat(),
            "old_live_epoch_id": old_epoch,
            "outcome_source_id": successor.outcome_source_id,
            "source_revision_id": revision,
            "previous_revision_id": previous_revision,
            "previous_outcome_sha256": previous_outcome,
            "source_snapshot_sequence_id": successor.snapshot_sequence_id,
            "source_snapshot_receipt_sha256": successor.snapshot_receipt.sha256,
            "live_issue_seal_entry_sha256": seal_hash,
            "terminal": False,
            "action": "outcome_materialized",
        }
        item = inventory._item(  # noqa: SLF001
            "outcome_revision",
            natural_key,
            "outcome_materialized",
            _source_artifacts_for_record(
                paths,
                source_profile,
                successor,
                target,
                historical_pointer=historical_pointer,
            ),
            authority,
            dependency_keys=dependencies,
        )
        result.append(
            {
                "family": item.family,
                "natural_key": item.natural_key,
                "canonical_successor_state": item.canonical_successor_state,
                "dependency_keys": list(item.dependency_keys),
                "artifacts": [
                    inventory._artifact_payload(value)  # noqa: SLF001
                    for value in item.artifacts
                ],
                "authority": dict(item.authority),
                "namespace_digest": item.namespace_digest,
            }
        )
        if natural_key in result_keys:
            raise SourceIngestDerivedIntegrityError(
                "Prospective source-derived outcomes contain a duplicate key"
            )
        result_keys.add(natural_key)
        prior_key = natural_key
    return tuple(result)


def _load_source_ingest_authority(
    paths: SourceIngestDerivedPaths,
    now: datetime,
    *,
    load_frozen_prefix: Callable[[recovery.Reservation], object] | None = None,
) -> SourceIngestDerivedAuthority | None:
    del now
    reservation = recovery._load_reservation(paths.recovery)  # noqa: SLF001
    if reservation is None:
        return None
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
        raise SourceIngestDerivedIntegrityError(
            "Source-ingest manifest edge is ambiguous"
        )
    source_item = candidates[0]
    authority = source_item.get("authority")
    if not isinstance(authority, Mapping) or authority.get("action") != (
        "ingest_incoming_finalized_feed"
    ):
        raise SourceIngestDerivedIntegrityError(
            "Source-ingest manifest authority changed"
        )
    plan = recovery._transition_plan(source_item)  # noqa: SLF001
    if (
        plan.get("initial_action") != "source_snapshot_ingested"
        or plan.get("terminal_actions") != []
        or plan.get("closure_resolved") is not False
    ):
        raise SourceIngestDerivedIntegrityError(
            "Source-ingest transition plan was reinterpreted"
        )
    predecessor_sequence = _positive_int(
        authority.get("predecessor_snapshot_sequence_id"),
        name="source predecessor sequence",
    )
    predecessor_sha = _hash(
        authority.get("predecessor_snapshot_receipt_sha256"),
        name="source predecessor receipt",
    )
    source_profile = source.load_deploy_profile()
    current = source.load_current_source(
        source_profile, runtime_root=paths.active_root, project_root=ROOT
    )
    if current.snapshot_sequence_id == predecessor_sequence:
        return None
    if current.snapshot_sequence_id < predecessor_sequence:
        raise SourceIngestDerivedIntegrityError(
            "Current source predates the frozen predecessor snapshot"
        )
    if current.outcome_source_id != authority.get("outcome_source_id"):
        raise SourceIngestDerivedIntegrityError("Source id changed across ingest edge")
    objects = source._runtime_path(  # noqa: SLF001
        source_profile, "objects", root=paths.active_root
    )
    registry_state = source._load_snapshot_receipt_registry(  # noqa: SLF001
        objects, required=True
    )
    if registry_state is None or registry_state.head != current.snapshot_receipt:
        raise SourceIngestDerivedIntegrityError(
            "Current source snapshot receipt is not the unique registry tip"
        )
    exact_child = _exact_source_snapshot_child(
        objects,
        predecessor_sequence=predecessor_sequence,
        predecessor_sha256=predecessor_sha,
        outcome_source_id=_text(
            authority.get("outcome_source_id"), name="source-ingest source id"
        ),
    )
    if exact_child is None:
        raise SourceIngestDerivedIntegrityError(
            "Frozen predecessor has no immutable exact next source snapshot"
        )
    successor_receipt_artifact, successor_receipt = exact_child
    predecessor_reference = successor_receipt.get("predecessor_snapshot_receipt")
    if (
        not isinstance(predecessor_reference, Mapping)
        or predecessor_reference.get("sha256") != predecessor_sha
        or successor_receipt.get("snapshot_sequence_id") != predecessor_sequence + 1
    ):
        raise SourceIngestDerivedIntegrityError(
            "Current source receipt is not the frozen predecessor's exact child"
        )
    successor = _load_source_from_snapshot_receipt(
        source_profile,
        root=paths.active_root,
        receipt_artifact=successor_receipt_artifact,
        receipt_payload=successor_receipt,
    )
    if (
        successor.snapshot_sequence_id != predecessor_sequence + 1
        or successor.snapshot_receipt != successor_receipt_artifact
        or successor.outcome_source_id != current.outcome_source_id
    ):
        raise SourceIngestDerivedIntegrityError(
            "Exact successor source identity changed"
        )
    predecessor = _load_predecessor_source(
        source_profile,
        root=paths.active_root,
        successor_payload=successor_receipt,
    )
    if (
        predecessor.snapshot_sequence_id != predecessor_sequence
        or predecessor.snapshot_receipt is None
        or predecessor.snapshot_receipt.sha256 != predecessor_sha
    ):
        raise SourceIngestDerivedIntegrityError(
            "Frozen predecessor source identity changed"
        )
    incoming = [
        value
        for value in source_item.get("artifacts", [])
        if isinstance(value, Mapping) and value.get("role") == "incoming_finalized_feed"
    ]
    if len(incoming) != 1 or incoming[0].get("root") != "active":
        raise SourceIngestDerivedIntegrityError(
            "Source-ingest item lacks one incoming feed artifact"
        )
    incoming_relative = _text(incoming[0].get("path"), name="incoming feed path")
    try:
        registry._contained(  # noqa: SLF001
            paths.active_root, incoming_relative, name="frozen incoming feed"
        )
        semantic_payload = source._load_json_artifact(  # noqa: SLF001
            successor.semantic_manifest, name="successor source semantic manifest"
        )
        daily_feed = semantic_payload.get("lineage", {}).get("daily_feed_snapshot", {})
        immutable_feed = source._content_object_from_payload(  # noqa: SLF001
            {key: daily_feed[key] for key in ("path", "sha256", "size_bytes")},
            name="source-derived immutable successor feed",
            directory=objects,
            suffix="feed.json",
        )
        feed_snapshot = registry._read_regular(  # noqa: SLF001
            immutable_feed.path,
            name="immutable successor feed",
            maximum_bytes=64 * 1024 * 1024,
        )
        feed = source._parse_feed(  # noqa: SLF001
            feed_snapshot.raw, source_profile, now=None
        )
    except Exception as exc:
        raise SourceIngestDerivedIntegrityError(
            f"Successor source feed replay failed:{type(exc).__name__}:{exc}"
        ) from exc
    incoming_sha = _hash(
        authority.get("incoming_feed_sha256"), name="incoming feed digest"
    )
    if (
        feed_snapshot.sha256 != incoming_sha
        or feed_snapshot.sha256 != incoming[0].get("sha256")
        or feed_snapshot.size_bytes != incoming[0].get("size_bytes")
    ):
        raise SourceIngestDerivedIntegrityError("Frozen incoming feed changed")
    if (
        feed.sha256 != incoming_sha
        or tuple(feed.records) != tuple(successor.records)
        or feed.outcome_source_id != successor.outcome_source_id
        or feed.exported_at_text != successor.exported_at_utc
        or daily_feed.get("sha256") != incoming_sha
        or daily_feed.get("exported_at_utc")
        != authority.get("incoming_exported_at_utc")
    ):
        raise SourceIngestDerivedIntegrityError(
            "Successor snapshot does not bind the frozen incoming feed"
        )
    changed = _changed_rows(predecessor, successor)
    declared_changed = authority.get("changed_or_appended_revisions")
    if declared_changed != changed or not changed:
        raise SourceIngestDerivedIntegrityError(
            "Frozen changed/appended revisions differ from the committed snapshot diff"
        )
    revisions_text = ",".join(
        f"{value['target_date']}={value['source_revision_id']}" for value in changed
    )
    expected_natural_key = inventory._key(  # noqa: SLF001
        "outcome_revision",
        reservation.manifest["frozen_live_upper_tip"]["old_live_epoch_id"],
        "source-ingest",
        successor.outcome_source_id,
        predecessor_sequence,
        incoming_sha,
        revisions_text,
    )
    if source_item.get("natural_key") != expected_natural_key:
        raise SourceIngestDerivedIntegrityError(
            "Source-ingest natural key differs from its exact source diff"
        )
    try:
        frozen = (load_frozen_prefix or recovery._frozen_live_prefix)(  # noqa: SLF001
            reservation
        )
        projection = getattr(frozen, "projection")
        frozen_events = tuple(getattr(frozen, "frozen_events"))
    except SourceIngestDerivedError:
        raise
    except Exception as exc:
        raise SourceIngestDerivedIntegrityError(
            f"Frozen live prefix replay failed:{type(exc).__name__}:{exc}"
        ) from exc
    prospective = _prospective_outcome_items(
        paths,
        reservation,
        ordered,
        source_item,
        successor,
        successor_receipt,
        projection=projection,
        frozen_events=frozen_events,
    )
    if len(prospective) > 4096:
        raise SourceIngestDerivedIntegrityError("Source-derived item capacity exceeded")
    frozen_by_key = {
        str(item["natural_key"]): item
        for item in ordered
        if item.get("family") == "outcome_revision"
        and isinstance(item.get("authority"), Mapping)
        and item["authority"].get("record_type") == "machine_selected_source_outcome"
    }
    derived = tuple(
        item for item in prospective if item["natural_key"] not in frozen_by_key
    )
    rebound = tuple(
        item
        for item in prospective
        if item["natural_key"] in frozen_by_key
        and item["namespace_digest"]
        != frozen_by_key[str(item["natural_key"])]["namespace_digest"]
    )
    prospective_keys = {str(item["natural_key"]) for item in prospective}
    invalidated = tuple(
        item for key, item in frozen_by_key.items() if key not in prospective_keys
    )
    if len(derived) + len(rebound) + len(invalidated) > 4096:
        raise SourceIngestDerivedIntegrityError(
            "Source-derived D/R/I classification capacity exceeded"
        )
    invalidated_keys = {str(item["natural_key"]) for item in invalidated}
    if any(
        invalidated_keys.intersection(item.get("dependency_keys", ()))
        for item in (*derived, *rebound)
    ):
        raise SourceIngestDerivedIntegrityError(
            "Prospective source-derived outcome depends on an invalidated key"
        )
    slot_id = _sha256(
        _canonical_bytes(
            {
                "manifest_sha256": reservation.manifest_snapshot.sha256,
                "source_key_id": source_item["key_id"],
                "predecessor_snapshot_receipt_sha256": predecessor_sha,
                "successor_snapshot_receipt_sha256": successor.snapshot_receipt.sha256,
            }
        )
    )
    return SourceIngestDerivedAuthority(
        reservation=reservation,
        ordered_items=tuple(ordered),
        dependency_graph_sha256=graph_digest,
        source_item=source_item,
        predecessor_source=predecessor,
        successor_source=successor,
        successor_receipt_payload=successor_receipt,
        derived_items=derived,
        rebound_items=rebound,
        invalidated_items=invalidated,
        slot_id=slot_id,
    )


def _identity_rows(values: Sequence[Mapping[str, Any]]) -> list[dict[str, object]]:
    return [
        {
            "natural_key": value["natural_key"],
            "namespace_digest": value["namespace_digest"],
        }
        for value in values
    ]


def _reservation_payload(
    profile: Mapping[str, Any],
    authority: SourceIngestDerivedAuthority,
) -> dict[str, object]:
    reservation = authority.reservation
    source_item = authority.source_item
    predecessor = authority.predecessor_source
    successor = authority.successor_source
    if predecessor.snapshot_receipt is None or successor.snapshot_receipt is None:
        raise SourceIngestDerivedIntegrityError("Source edge lost a snapshot receipt")
    derived_rows = [dict(item) for item in authority.derived_items]
    rebound_rows = [dict(item) for item in authority.rebound_items]
    invalidated_rows = [dict(item) for item in authority.invalidated_items]
    return {
        "schema_version": profile["protocol"]["reservation_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "slot_id": authority.slot_id,
        "reservation_policy": (
            "exact_next_published_source_snapshot_complete_new_key_batch"
        ),
        "base_authority": {
            "manifest": _reference(
                reservation.manifest_snapshot, reservation.paths.root
            ),
            "reservation_event": _reference(
                reservation.event_snapshot, reservation.paths.root
            ),
            "workset_keyset_sha256": reservation.manifest["workset_keyset_sha256"],
            "dependency_graph_sha256": authority.dependency_graph_sha256,
        },
        "source_ingest": {
            "key_id": source_item["key_id"],
            "natural_key": source_item["natural_key"],
            "namespace_digest": source_item["namespace_digest"],
            "transition_plan_sha256": recovery._transition_plan(  # noqa: SLF001
                source_item
            )["plan_sha256"],
            "incoming_feed_sha256": source_item["authority"]["incoming_feed_sha256"],
            "changed_or_appended_revisions": source_item["authority"][
                "changed_or_appended_revisions"
            ],
        },
        "predecessor_snapshot": {
            "snapshot_sequence_id": predecessor.snapshot_sequence_id,
            "snapshot_receipt_sha256": predecessor.snapshot_receipt.sha256,
            "snapshot_receipt": _source_artifact_reference(
                predecessor.snapshot_receipt, reservation.paths.active_root
            ),
        },
        "successor_snapshot": {
            "snapshot_sequence_id": successor.snapshot_sequence_id,
            "snapshot_receipt_sha256": successor.snapshot_receipt.sha256,
            "snapshot_receipt": _source_artifact_reference(
                successor.snapshot_receipt, reservation.paths.active_root
            ),
            "source_semantic_manifest": _source_artifact_reference(
                successor.semantic_manifest, reservation.paths.active_root
            ),
            "canonical_dataset": _source_artifact_reference(
                successor.dataset, reservation.paths.active_root
            ),
            "source_pointer": dict(
                authority.successor_receipt_payload["source_pointer"]
            ),
            "outcome_source_id": successor.outcome_source_id,
            "exported_at_utc": successor.exported_at_utc,
            "maximum_complete_finalized_date": successor.watermark.isoformat(),
        },
        "derived_outcome_items": derived_rows,
        "derived_outcome_item_count": len(derived_rows),
        "derived_outcome_keyset_sha256": _sha256(
            _canonical_bytes(_identity_rows(authority.derived_items))
        ),
        "rebound_existing_items": rebound_rows,
        "rebound_existing_item_count": len(rebound_rows),
        "rebound_existing_keyset_sha256": _sha256(
            _canonical_bytes(_identity_rows(authority.rebound_items))
        ),
        "invalidated_existing_items": invalidated_rows,
        "invalidated_existing_item_count": len(invalidated_rows),
        "invalidated_existing_keyset_sha256": _sha256(
            _canonical_bytes(_identity_rows(authority.invalidated_items))
        ),
        "complete_for_exact_source_snapshot_edge": True,
        "implementation": _implementation_reference(),
        **_claims(),
    }


def _publish_reservation(
    profile: Mapping[str, Any],
    paths: SourceIngestDerivedPaths,
    authority: SourceIngestDerivedAuthority,
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    payload = _reservation_payload(profile, authority)
    raw = _canonical_bytes(payload)
    digest = _sha256(raw)
    snapshot = _publish(
        paths.reservation_objects / f"{digest}.json",
        raw,
        root=paths.root,
        name="source-derived reservation object",
    )
    checked, replay = _strict_json(
        snapshot.path, name="source-derived reservation object"
    )
    if checked != payload or replay != snapshot:
        raise SourceIngestDerivedIntegrityError(
            "Source-derived reservation did not replay exactly"
        )
    return payload, snapshot


def _verify_reservation(
    profile: Mapping[str, Any],
    authority: SourceIngestDerivedAuthority,
    payload: Mapping[str, Any],
    snapshot: registry.ArtifactSnapshot,
) -> None:
    expected = _reservation_payload(profile, authority)
    if (
        dict(payload) != expected
        or snapshot.sha256 != _sha256(snapshot.raw)
        or payload.get("slot_id") != authority.slot_id
    ):
        raise SourceIngestDerivedIntegrityError(
            "Stored source-derived reservation semantics changed"
        )


def _reservation_objects(
    profile: Mapping[str, Any],
    paths: SourceIngestDerivedPaths,
    authority: SourceIngestDerivedAuthority,
) -> dict[str, tuple[dict[str, Any], registry.ArtifactSnapshot]]:
    result: dict[str, tuple[dict[str, Any], registry.ArtifactSnapshot]] = {}
    for path in _strict_entries(
        paths.reservation_objects, name="source-derived reservation objects"
    ):
        matched = OBJECT_NAME.fullmatch(path.name)
        if matched is None:
            raise SourceIngestDerivedIntegrityError(
                "Source-derived reservation filename changed"
            )
        payload, snapshot = _strict_json(path, name="source-derived reservation object")
        if matched.group("digest") != snapshot.sha256:
            raise SourceIngestDerivedIntegrityError(
                "Source-derived reservation is not content-addressed"
            )
        _verify_reservation(profile, authority, payload, snapshot)
        slot_id = _text(payload.get("slot_id"), name="source-derived slot")
        if slot_id in result:
            raise SourceIngestDerivedIntegrityError(
                "One source edge has multiple reservation objects"
            )
        result[slot_id] = (payload, snapshot)
    return result


def _event_unsigned(
    profile: Mapping[str, Any],
    paths: SourceIngestDerivedPaths,
    authority: SourceIngestDerivedAuthority,
    reservation_snapshot: registry.ArtifactSnapshot,
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
        "slot_id": authority.slot_id,
        "source_key_id": authority.source_item["key_id"],
        "reservation": _reference(reservation_snapshot, paths.root),
        "derived_key_count": len(authority.derived_items),
        "rebound_item_count": len(authority.rebound_items),
        "invalidated_item_count": len(authority.invalidated_items),
        **_claims(),
    }


def _append_event(
    profile: Mapping[str, Any],
    paths: SourceIngestDerivedPaths,
    authority: SourceIngestDerivedAuthority,
    reservation_snapshot: registry.ArtifactSnapshot,
    *,
    sequence_id: int,
    previous: str,
    now: datetime,
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    body = _event_unsigned(
        profile,
        paths,
        authority,
        reservation_snapshot,
        sequence_id=sequence_id,
        previous=previous,
        recorded_at=_utc_text(now),
    )
    payload = {**body, "entry_sha256": _sha256(_canonical_bytes(body))}
    snapshot = _publish(
        paths.events / f"{sequence_id:020d}-{payload['entry_sha256']}.json",
        _canonical_bytes(payload),
        root=paths.root,
        name="source-derived reservation event",
    )
    return payload, snapshot


def _replay_events(
    profile: Mapping[str, Any],
    paths: SourceIngestDerivedPaths,
    authority: SourceIngestDerivedAuthority,
    objects: Mapping[str, tuple[dict[str, Any], registry.ArtifactSnapshot]],
) -> tuple[list[dict[str, Any]], str, set[str]]:
    events: list[dict[str, Any]] = []
    previous = ZERO_HASH
    seen: set[str] = set()
    for expected_sequence, path in enumerate(
        _strict_entries(paths.events, name="source-derived events"), 1
    ):
        matched = EVENT_NAME.fullmatch(path.name)
        if matched is None or int(matched.group("sequence")) != expected_sequence:
            raise SourceIngestDerivedIntegrityError(
                "Source-derived event sequence/path changed"
            )
        payload, _ = _strict_json(path, name="source-derived event")
        body = dict(payload)
        entry = body.pop("entry_sha256", None)
        slot_id = payload.get("slot_id")
        stored = objects.get(slot_id) if isinstance(slot_id, str) else None
        if stored is None:
            raise SourceIngestDerivedIntegrityError(
                "Source-derived event has no reservation object"
            )
        _, reservation_snapshot = stored
        expected = _event_unsigned(
            profile,
            paths,
            authority,
            reservation_snapshot,
            sequence_id=expected_sequence,
            previous=previous,
            recorded_at="",
        )
        comparable = {
            key: value for key, value in body.items() if key != "recorded_at_utc"
        }
        expected_comparable = {
            key: value for key, value in expected.items() if key != "recorded_at_utc"
        }
        if (
            set(payload) != {*expected, "entry_sha256"}
            or comparable != expected_comparable
            or payload.get("previous_entry_sha256") != previous
            or entry != _sha256(_canonical_bytes(body))
            or matched.group("entry") != entry
            or slot_id in seen
        ):
            raise SourceIngestDerivedIntegrityError(
                "Source-derived event chain changed"
            )
        _parse_utc(payload.get("recorded_at_utc"), name="source-derived event time")
        seen.add(str(slot_id))
        previous = str(entry)
        events.append(payload)
    missing = set(objects) - seen
    if len(missing) > 1:
        raise SourceIngestDerivedIntegrityError(
            "Source-derived reservation objects branched before publication"
        )
    return events, previous, seen


def _select_candidate(
    authority: SourceIngestDerivedAuthority, reserved_slots: set[str]
) -> SourceIngestDerivedAuthority | None:
    return authority if authority.slot_id not in reserved_slots else None


def _write_status(
    profile: Mapping[str, Any],
    paths: SourceIngestDerivedPaths,
    *,
    now: datetime,
    status: str,
    reason: str,
    authority: SourceIngestDerivedAuthority | None,
    reservation: Path | None,
    event: Path | None,
) -> None:
    payload = {
        "schema_version": profile["protocol"]["status_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "recorded_at_utc": _utc_text(now),
        "status": status,
        "reason": reason,
        "reservation_path": str(reservation) if reservation else None,
        "event_path": str(event) if event else None,
        "source_key_id": (str(authority.source_item["key_id"]) if authority else None),
        "derived_key_count": len(authority.derived_items) if authority else 0,
        "rebound_item_count": len(authority.rebound_items) if authority else 0,
        "invalidated_item_count": (
            len(authority.invalidated_items) if authority else 0
        ),
        "cache_authority": False,
        **_claims(),
    }
    raw = _canonical_bytes(payload)
    if len(raw) > MAX_CONTROL_BYTES:
        raise SourceIngestDerivedIntegrityError(
            "Source-derived status exceeds the reviewed control-byte limit"
        )
    try:
        registry._atomic_cache(  # noqa: SLF001
            paths.status,
            raw,
            root=paths.root,
            name="source-derived status",
        )
    except registry.EpochRegistryError as exc:
        raise SourceIngestDerivedIntegrityError(str(exc)) from exc


def _result(
    status: str,
    reason: str,
    paths: SourceIngestDerivedPaths,
    authority: SourceIngestDerivedAuthority | None,
    reservation: Path | None = None,
    event: Path | None = None,
) -> SourceIngestDerivedResult:
    return SourceIngestDerivedResult(
        status,
        reason,
        paths.status,
        reservation,
        event,
        str(authority.source_item["key_id"]) if authority else None,
        len(authority.derived_items) if authority else 0,
        len(authority.rebound_items) if authority else 0,
        len(authority.invalidated_items) if authority else 0,
    )


def _coordinate_source_ingest_derived_reservation(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    registry_root: Path | None = None,
    active_root: Path | None = None,
    shadow_root: Path | None = None,
    clock: Callable[[], datetime] | None = None,
    load_authority: LoadAuthority | None = None,
) -> SourceIngestDerivedResult:
    profile = load_source_ingest_derived_profile(config_path)
    paths = source_ingest_derived_paths(
        profile,
        registry_root=registry_root,
        active_root=active_root,
        shadow_root=shadow_root,
    )
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    handles: list[BinaryIO] = []
    acquired = False
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
                raise SourceIngestDerivedBusyError(str(exc)) from exc
            except drain.EpochDrainError as exc:
                raise SourceIngestDerivedIntegrityError(str(exc)) from exc
        acquired = True
        authority = (load_authority or _load_source_ingest_authority)(paths, now)
        children = bool(
            _strict_entries(
                paths.reservation_objects, name="source-derived reservation objects"
            )
            or _strict_entries(paths.events, name="source-derived events")
        )
        if authority is None:
            if children:
                raise SourceIngestDerivedIntegrityError(
                    "Source-derived authority disappeared below persisted sidecar bytes"
                )
            reason = (
                "the frozen source-ingest item has no exact next published source "
                "snapshot"
            )
            _write_status(
                profile,
                paths,
                now=now,
                status="waiting_for_exact_next_source_snapshot",
                reason=reason,
                authority=None,
                reservation=None,
                event=None,
            )
            return _result(
                "waiting_for_exact_next_source_snapshot", reason, paths, None
            )
        objects = _reservation_objects(profile, paths, authority)
        events, previous, seen = _replay_events(profile, paths, authority, objects)
        orphaned = set(objects) - seen
        if orphaned:
            slot_id = next(iter(orphaned))
            _, reservation_snapshot = objects[slot_id]
            _, event_snapshot = _append_event(
                profile,
                paths,
                authority,
                reservation_snapshot,
                sequence_id=len(events) + 1,
                previous=previous,
                now=now,
            )
            reason = (
                "the exact create-only derived-key batch was forward-adopted "
                "into its append-only event chain"
            )
            _write_status(
                profile,
                paths,
                now=now,
                status="source_ingest_derived_event_forward_adopted",
                reason=reason,
                authority=authority,
                reservation=reservation_snapshot.path,
                event=event_snapshot.path,
            )
            return _result(
                "source_ingest_derived_event_forward_adopted",
                reason,
                paths,
                authority,
                reservation_snapshot.path,
                event_snapshot.path,
            )
        candidate = _select_candidate(authority, set(objects))
        if candidate is None:
            stored = objects[authority.slot_id][1]
            event_path = paths.events / (
                f"{events[-1]['sequence_id']:020d}-{events[-1]['entry_sha256']}.json"
            )
            reason = "the exact source-snapshot derived-key batch is already published"
            _write_status(
                profile,
                paths,
                now=now,
                status="source_ingest_derived_keys_already_reserved",
                reason=reason,
                authority=authority,
                reservation=stored.path,
                event=event_path,
            )
            return _result(
                "source_ingest_derived_keys_already_reserved",
                reason,
                paths,
                authority,
                stored.path,
                event_path,
            )
        _, reservation_snapshot = _publish_reservation(profile, paths, candidate)
        _, event_snapshot = _append_event(
            profile,
            paths,
            candidate,
            reservation_snapshot,
            sequence_id=len(events) + 1,
            previous=previous,
            now=now,
        )
        reason = (
            "one exact next source snapshot was adopted and its complete "
            "content-dependent outcome-key batch was reserved"
        )
        _write_status(
            profile,
            paths,
            now=now,
            status="source_ingest_derived_keys_reserved",
            reason=reason,
            authority=candidate,
            reservation=reservation_snapshot.path,
            event=event_snapshot.path,
        )
        return _result(
            "source_ingest_derived_keys_reserved",
            reason,
            paths,
            candidate,
            reservation_snapshot.path,
            event_snapshot.path,
        )
    except SourceIngestDerivedBusyError:
        raise
    except SourceIngestDerivedError as exc:
        if acquired:
            try:
                _write_status(
                    profile,
                    paths,
                    now=now,
                    status="blocked_integrity",
                    reason=f"{type(exc).__name__}:{exc}",
                    authority=None,
                    reservation=None,
                    event=None,
                )
            except SourceIngestDerivedError:
                pass
        raise
    finally:
        try:
            drain._release_locks(handles)  # noqa: SLF001
        except drain.EpochDrainError as exc:
            if sys.exc_info()[0] is None:
                raise SourceIngestDerivedIntegrityError(str(exc)) from exc


def coordinate_source_ingest_derived_reservation(
    *, config_path: Path = DEFAULT_CONFIG_PATH
) -> SourceIngestDerivedResult:
    """Run one machine-only source-snapshot adoption/reservation poll."""

    return _coordinate_source_ingest_derived_reservation(config_path=config_path)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result = coordinate_source_ingest_derived_reservation(config_path=args.config)
    print(
        json.dumps(
            {
                "status": result.status,
                "reason": result.reason,
                "status_path": str(result.status_path),
                "reservation_path": (
                    str(result.reservation_path) if result.reservation_path else None
                ),
                "event_path": str(result.event_path) if result.event_path else None,
                "source_key_id": result.source_key_id,
                "derived_key_count": result.derived_key_count,
                "rebound_item_count": result.rebound_item_count,
                "invalidated_item_count": result.invalidated_item_count,
                "source_ingest_writer_implemented": False,
                "terminal_transition_closure_implemented": False,
                "derived_future_work_reservation_implemented": False,
                "old_epoch_drained": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
