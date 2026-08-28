"""Read-only, complete inventory of one admission-cut old-epoch workset.

The public inspector deliberately has no runtime writer, lock acquisition, clock,
nonce, outcome, or lifecycle side effect.  It reuses the reviewed recursive
loaders and turns their verified state into two complementary views:

* actionable ``InventoryItem`` records, reserved by the closed-workset manifest;
* one ``FamilyInventory`` per family, covering pending *and terminal* namespace
  records so an empty actionable set cannot hide a non-empty durable namespace.

SQLite path bytes are never treated as the ledger CAS.  Live and shadow ledger
authority is represented by the replayed schema/count/terminal/ordered-entry
chain stored in the canonical ``authority`` objects.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ROOT = Path(__file__).resolve().parents[2]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_epoch_registry as registry  # noqa: E402


ZERO_HASH = "0" * 64
DEFAULT_MAXIMUM_ARTIFACT_BYTES = 64 * 1024 * 1024
FAMILIES = (
    "issue_route_replay",
    "live_outstanding",
    "outcome_revision",
    "guard",
    "trusted_time",
    "shadow",
)
ALLOWED_SUCCESSORS: Mapping[str, tuple[str, ...]] = {
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


class WorksetInventoryError(RuntimeError):
    """Base failure for a read-only closed-workset inspection."""


class WorksetInventoryIntegrityError(WorksetInventoryError):
    """A durable namespace, reference, or semantic chain is inconsistent."""


class WorksetInventoryIncompleteError(WorksetInventoryError):
    """The frozen runtime lacks a prerequisite needed for complete enumeration."""


class WorksetInventoryCapacityError(WorksetInventoryError):
    """A reviewed bounded artifact or inventory capacity was exceeded."""


@dataclass(frozen=True)
class ArtifactRef:
    role: str
    root: str
    path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class InventoryItem:
    family: str
    natural_key: str
    canonical_successor_state: str
    dependency_keys: tuple[str, ...]
    artifacts: tuple[ArtifactRef, ...]
    authority: Mapping[str, object]
    namespace_digest: str


@dataclass(frozen=True)
class FamilyInventory:
    family: str
    record_count: int
    actionable_count: int
    artifacts: tuple[ArtifactRef, ...]
    authority: Mapping[str, object]
    namespace_digest: str


@dataclass(frozen=True)
class InventoryResult:
    context_epoch_id: str
    frozen_live_event_count: int
    frozen_live_terminal_sha256: str
    items: tuple[InventoryItem, ...]
    families: tuple[FamilyInventory, ...]


@dataclass
class _FamilyBuilder:
    records: list[dict[str, object]]
    artifacts: list[ArtifactRef]
    authority: dict[str, object]


def _canonical_bytes(value: object) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError, UnicodeEncodeError) as exc:
        raise WorksetInventoryIntegrityError(
            "Inventory value is not canonical finite JSON"
        ) from exc


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _hash(value: object, *, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise WorksetInventoryIntegrityError(f"{name} is not lowercase SHA-256")
    return value


def _text(value: object, *, name: str, maximum: int = 4096) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\x00" in value
        or len(value.encode("utf-8")) > maximum
    ):
        raise WorksetInventoryIntegrityError(f"{name} is not canonical text")
    return value


def _positive_int(value: object, *, name: str, allow_zero: bool = False) -> int:
    minimum = 0 if allow_zero else 1
    if type(value) is not int or value < minimum:
        raise WorksetInventoryIntegrityError(f"{name} is not a valid integer")
    return value


def _context_value(context: object, name: str) -> object:
    if not hasattr(context, name):
        raise WorksetInventoryIntegrityError(f"Frozen context lacks {name}")
    return getattr(context, name)


def _context_identity(context: object) -> dict[str, object]:
    return {
        "registry_event_sequence_id": _positive_int(
            _context_value(context, "registry_event_sequence_id"),
            name="context.registry_event_sequence_id",
        ),
        "registry_event_entry_sha256": _hash(
            _context_value(context, "registry_event_entry_sha256"),
            name="context.registry_event_entry_sha256",
        ),
        "preparation_event_sequence_id": _positive_int(
            _context_value(context, "preparation_event_sequence_id"),
            name="context.preparation_event_sequence_id",
        ),
        "preparation_event_entry_sha256": _hash(
            _context_value(context, "preparation_event_entry_sha256"),
            name="context.preparation_event_entry_sha256",
        ),
        "candidate_id": _text(
            _context_value(context, "candidate_id"), name="context.candidate_id"
        ),
        "slot_id": _text(_context_value(context, "slot_id"), name="context.slot_id"),
        "old_live_epoch_id": _text(
            _context_value(context, "old_live_epoch_id"),
            name="context.old_live_epoch_id",
        ),
        "live_event_count": _positive_int(
            _context_value(context, "live_event_count"),
            name="context.live_event_count",
        ),
        "live_terminal_sha256": _hash(
            _context_value(context, "live_terminal_sha256"),
            name="context.live_terminal_sha256",
        ),
    }


def _key(family: str, *parts: object) -> str:
    encoded = [quote(str(part), safe="-._") for part in parts]
    key = ":".join((family, *encoded))
    if len(key.encode("utf-8")) > 4096:
        identity = {"family": family, "parts": [str(part) for part in parts]}
        key = f"{family}:sha256:{_sha256(_canonical_bytes(identity))}"
    return _text(key, name="inventory natural key")


def _artifact_payload(value: ArtifactRef) -> dict[str, object]:
    return {
        "role": value.role,
        "root": value.root,
        "path": value.path,
        "sha256": value.sha256,
        "size_bytes": value.size_bytes,
    }


def _dedupe_artifacts(values: Sequence[ArtifactRef]) -> tuple[ArtifactRef, ...]:
    records: dict[tuple[str, str, str], ArtifactRef] = {}
    for value in values:
        identity = (value.root, value.path, value.role)
        previous = records.get(identity)
        if previous is not None and previous != value:
            raise WorksetInventoryIntegrityError("Artifact reference branched")
        records[identity] = value
    return tuple(
        sorted(records.values(), key=lambda item: (item.root, item.path, item.role))
    )


def _artifact(
    path: Path,
    *,
    role: str,
    root_label: str,
    root: Path,
    maximum_bytes: int,
) -> ArtifactRef:
    if root_label not in {"active", "shadow"}:
        raise WorksetInventoryIntegrityError("Unknown artifact root label")
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            path, name=role, maximum_bytes=maximum_bytes
        )
        relative = snapshot.path.relative_to(root.resolve()).as_posix()
    except (ValueError, registry.EpochRegistryError) as exc:
        raise WorksetInventoryIntegrityError(
            f"Cannot capture contained artifact {role}"
        ) from exc
    if not relative or relative.startswith("../"):
        raise WorksetInventoryIntegrityError(f"Artifact {role} escaped its root")
    return ArtifactRef(
        role=_text(role, name="artifact role"),
        root=root_label,
        path=relative,
        sha256=snapshot.sha256,
        size_bytes=snapshot.size_bytes,
    )


def _strict_dated_records(
    directory: Path, suffix: str, *, name: str
) -> dict[date, Path]:
    if not directory.exists() and not directory.is_symlink():
        return {}
    try:
        if not stat.S_ISDIR(os.lstat(directory).st_mode):
            raise WorksetInventoryIntegrityError(f"{name} is not a real directory")
        children = sorted(directory.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise WorksetInventoryIntegrityError(f"Cannot enumerate {name}") from exc
    result: dict[date, Path] = {}
    for child in children:
        if child.name.startswith(".") or not child.name.endswith(suffix):
            raise WorksetInventoryIntegrityError(f"{name} contains an unknown entry")
        stem = child.name[: -len(suffix)]
        try:
            target = date.fromisoformat(stem)
        except ValueError as exc:
            raise WorksetInventoryIntegrityError(
                f"{name} contains a non-date entry"
            ) from exc
        if target.isoformat() != stem or target in result:
            raise WorksetInventoryIntegrityError(
                f"{name} contains a noncanonical or duplicate date"
            )
        try:
            if not stat.S_ISREG(os.lstat(child).st_mode):
                raise WorksetInventoryIntegrityError(f"{name} entry is not regular")
        except OSError as exc:
            raise WorksetInventoryIntegrityError(f"Cannot inspect {name}") from exc
        result[target] = child
    return result


def _read_json(
    path: Path, *, name: str, maximum_bytes: int
) -> tuple[dict[str, Any], bytes]:
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            path, name=name, maximum_bytes=maximum_bytes
        )
        payload = registry._decode_json(snapshot.raw, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise WorksetInventoryIntegrityError(str(exc)) from exc
    if snapshot.raw != _canonical_bytes(payload):
        raise WorksetInventoryIntegrityError(f"{name} is not canonical JSON")
    return payload, snapshot.raw


def _logical_ledger_authority(
    projection: object, *, schema: str, epoch_field: str
) -> dict[str, object]:
    events = tuple(getattr(projection, "ledger_events", ()))
    hashes = [
        _hash(getattr(event, "entry_sha256", None), name=f"{schema} entry")
        for event in events
    ]
    count = _positive_int(
        getattr(projection, "ledger_event_count", None),
        name=f"{schema} event count",
        allow_zero=True,
    )
    terminal = _hash(
        getattr(projection, "ledger_terminal_sha256", None),
        name=f"{schema} terminal",
    )
    if count != len(hashes) or terminal != (hashes[-1] if hashes else ZERO_HASH):
        raise WorksetInventoryIntegrityError(f"{schema} logical chain changed")
    epoch = _text(getattr(projection, epoch_field, None), name=f"{schema} epoch")
    return {
        "schema": schema,
        "epoch_id": epoch,
        "event_count": count,
        "terminal_entry_sha256": terminal,
        "ordered_entry_sha256s": hashes,
    }


def _item(
    family: str,
    natural_key: str,
    successor: str,
    artifacts: Sequence[ArtifactRef],
    authority: Mapping[str, object],
    *,
    dependency_keys: Sequence[str] = (),
) -> InventoryItem:
    if family not in FAMILIES or successor not in ALLOWED_SUCCESSORS[family]:
        raise WorksetInventoryIntegrityError("Unknown family successor")
    deps = tuple(
        sorted({_text(value, name="dependency key") for value in dependency_keys})
    )
    refs = _dedupe_artifacts(artifacts)
    canonical_authority = json.loads(_canonical_bytes(dict(authority)))
    body = {
        "family": family,
        "natural_key": natural_key,
        "canonical_successor_state": successor,
        "dependency_keys": list(deps),
        "artifacts": [_artifact_payload(value) for value in refs],
        "authority": canonical_authority,
    }
    return InventoryItem(
        family=family,
        natural_key=natural_key,
        canonical_successor_state=successor,
        dependency_keys=deps,
        artifacts=refs,
        authority=canonical_authority,
        namespace_digest=_sha256(_canonical_bytes(body)),
    )


def _family(
    name: str, builder: _FamilyBuilder, actionable: Sequence[InventoryItem]
) -> FamilyInventory:
    refs = _dedupe_artifacts(builder.artifacts)
    authority = {
        **builder.authority,
        "records": sorted(builder.records, key=lambda value: _canonical_bytes(value)),
    }
    canonical_authority = json.loads(_canonical_bytes(authority))
    body = {
        "family": name,
        "record_count": len(builder.records),
        "actionable_count": len(actionable),
        "artifacts": [_artifact_payload(value) for value in refs],
        "authority": canonical_authority,
    }
    return FamilyInventory(
        family=name,
        record_count=len(builder.records),
        actionable_count=len(actionable),
        artifacts=refs,
        authority=canonical_authority,
        namespace_digest=_sha256(_canonical_bytes(body)),
    )


def _event_record(event: object) -> dict[str, object]:
    return {
        "sequence_id": _positive_int(
            getattr(event, "sequence_id", None), name="event sequence"
        ),
        "entry_sha256": _hash(getattr(event, "entry_sha256", None), name="event entry"),
        "event_type": _text(getattr(event, "event_type", None), name="event type"),
        "target_date": getattr(event, "target_date", None),
        "issue_id": getattr(event, "issue_id", None),
    }


def _new_builders() -> dict[str, _FamilyBuilder]:
    return {
        family: _FamilyBuilder(records=[], artifacts=[], authority={})
        for family in FAMILIES
    }


def inspect_closed_workset(
    *,
    active_root: Path,
    shadow_root: Path,
    context: object,
    machine_now: datetime,
    maximum_artifact_bytes: int = DEFAULT_MAXIMUM_ARTIFACT_BYTES,
) -> InventoryResult:
    """Return the complete read-only six-family inventory at a frozen live tip."""

    if machine_now.tzinfo is None or machine_now.utcoffset() is None:
        raise WorksetInventoryIntegrityError("Inventory clock must be timezone-aware")
    machine_now = machine_now.astimezone(timezone.utc)
    if type(maximum_artifact_bytes) is not int or maximum_artifact_bytes <= 0:
        raise WorksetInventoryCapacityError("Artifact byte bound is invalid")
    active = Path(os.path.abspath(active_root))
    shadow = Path(os.path.abspath(shadow_root))
    if active == shadow:
        raise WorksetInventoryIntegrityError("Active and shadow roots must differ")
    identity = _context_identity(context)
    builders = _new_builders()
    items: list[InventoryItem] = []

    state = _load_verified_state(
        active,
        shadow,
        identity,
        machine_now=machine_now,
        maximum_bytes=maximum_artifact_bytes,
    )
    _inventory_sources_and_objects(state, builders, items)
    _inventory_issues_and_live(state, builders, items)
    _inventory_outcomes(state, builders, items)
    _inventory_guard(state, builders, items)
    _inventory_trusted_time(state, builders, items)
    _inventory_shadow(state, builders, items)

    ordered_items = tuple(
        sorted(
            items, key=lambda value: (FAMILIES.index(value.family), value.natural_key)
        )
    )
    if len({item.natural_key for item in ordered_items}) != len(ordered_items):
        raise WorksetInventoryIntegrityError("Inventory natural key is not unique")
    families = tuple(
        _family(
            family,
            builders[family],
            [item for item in ordered_items if item.family == family],
        )
        for family in FAMILIES
    )
    return InventoryResult(
        context_epoch_id=str(identity["old_live_epoch_id"]),
        frozen_live_event_count=int(identity["live_event_count"]),
        frozen_live_terminal_sha256=str(identity["live_terminal_sha256"]),
        items=ordered_items,
        families=families,
    )


# The functions below are intentionally private assembly steps.  Keeping one
# public inspector prevents callers from mixing observations made at different
# frozen tips.


@dataclass(frozen=True)
class _VerifiedState:
    active_root: Path
    shadow_root: Path
    maximum_bytes: int
    now: datetime
    identity: Mapping[str, object]
    source_module: Any
    source_profile: Mapping[str, Any]
    source: Any
    activation_source: Any
    live_module: Any
    live_profile: Mapping[str, Any]
    live_paths: Any
    prerequisites: Any
    live_projection: Any
    shadow_module: Any
    shadow_profile: Mapping[str, Any]
    shadow_paths: Any
    shadow_projection: Any


def _load_verified_state(
    active_root: Path,
    shadow_root: Path,
    identity: Mapping[str, object],
    *,
    machine_now: datetime,
    maximum_bytes: int,
) -> _VerifiedState:
    from monitoring import ootang_live_source as source_module
    from monitoring import ootang_prequential_calibration_shadow as shadow_module
    from monitoring import ootang_prequential_live as live_module

    try:
        source_profile = source_module.load_deploy_profile()
        source = source_module.load_current_source(
            source_profile, runtime_root=active_root, project_root=ROOT
        )
        activation_source = source_module.load_activation_source(
            source_profile, runtime_root=active_root, project_root=ROOT
        )
        exported = source_module._utc(  # noqa: SLF001
            source.exported_at_utc, name="current source exported_at_utc"
        )
        if exported > machine_now:
            raise WorksetInventoryIntegrityError("Current source is future-dated")
        live_profile = live_module.load_config()
        live_paths = live_module.runtime_paths(live_profile, runtime_root=active_root)
        prerequisites = live_module.load_prerequisites(live_profile, live_paths)
        if prerequisites is None:
            raise WorksetInventoryIncompleteError("Live prerequisites are missing")
        projection = live_module.load_verified_ledger_projection(
            live_profile, live_paths, prerequisites
        )
        shadow_profile = shadow_module.load_shadow_profile()
        shadow_paths = shadow_module.runtime_paths(
            shadow_profile, runtime_root=shadow_root
        )
        shadow_projection = shadow_module.load_verified_shadow_projection(
            shadow_profile, shadow_paths
        )
    except WorksetInventoryError:
        raise
    except Exception as exc:
        raise WorksetInventoryIntegrityError(
            f"Cannot recursively replay frozen runtime:{type(exc).__name__}:{exc}"
        ) from exc
    live_authority = _logical_ledger_authority(
        projection,
        schema="ootang_prequential_live_logical_chain_v1",
        epoch_field="epoch_id",
    )
    if (
        live_authority["epoch_id"] != identity["old_live_epoch_id"]
        or live_authority["event_count"] != identity["live_event_count"]
        or live_authority["terminal_entry_sha256"] != identity["live_terminal_sha256"]
    ):
        raise WorksetInventoryIntegrityError(
            "Verified live chain differs from frozen admission-cut tip"
        )
    return _VerifiedState(
        active_root=active_root,
        shadow_root=shadow_root,
        maximum_bytes=maximum_bytes,
        now=machine_now,
        identity=identity,
        source_module=source_module,
        source_profile=source_profile,
        source=source,
        activation_source=activation_source,
        live_module=live_module,
        live_profile=live_profile,
        live_paths=live_paths,
        prerequisites=prerequisites,
        live_projection=projection,
        shadow_module=shadow_module,
        shadow_profile=shadow_profile,
        shadow_paths=shadow_paths,
        shadow_projection=shadow_projection,
    )


def _inventory_sources_and_objects(
    state: _VerifiedState,
    builders: dict[str, _FamilyBuilder],
    items: list[InventoryItem],
) -> None:
    """Implemented below: source authority, incoming feed and shared-object DFS."""

    _inventory_source_authority(state, builders, items)


_SHARED_OBJECT_SUFFIXES = (
    ".source-snapshot-receipt.json",
    ".source-revision.json",
    ".source-manifest.json",
    ".source-pointer.json",
    ".outcome-input.json",
    ".issue.json",
    ".outcome.json",
    ".source.json",
    ".feed.json",
    ".json",
)


def _direct_regular_files(directory: Path, *, name: str) -> tuple[Path, ...]:
    if not directory.exists() and not directory.is_symlink():
        return ()
    try:
        if not stat.S_ISDIR(os.lstat(directory).st_mode):
            raise WorksetInventoryIntegrityError(f"{name} is not a real directory")
        children = sorted(directory.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        raise WorksetInventoryIntegrityError(f"Cannot enumerate {name}") from exc
    for child in children:
        try:
            if child.name.startswith(".") or not stat.S_ISREG(os.lstat(child).st_mode):
                raise WorksetInventoryIntegrityError(
                    f"{name} contains an unknown entry"
                )
        except OSError as exc:
            raise WorksetInventoryIntegrityError(f"Cannot inspect {name}") from exc
    return tuple(children)


def _recursive_regular_files(directory: Path, *, name: str) -> tuple[Path, ...]:
    if not directory.exists() and not directory.is_symlink():
        return ()
    result: list[Path] = []
    pending = [directory]
    while pending:
        current = pending.pop()
        try:
            if not stat.S_ISDIR(os.lstat(current).st_mode):
                raise WorksetInventoryIntegrityError(f"{name} root is not regular")
            children = sorted(current.iterdir(), key=lambda path: path.name)
        except OSError as exc:
            raise WorksetInventoryIntegrityError(f"Cannot enumerate {name}") from exc
        for child in children:
            if child.name.startswith("."):
                raise WorksetInventoryIntegrityError(f"{name} has a hidden entry")
            try:
                mode = os.lstat(child).st_mode
            except OSError as exc:
                raise WorksetInventoryIntegrityError(f"Cannot inspect {name}") from exc
            if stat.S_ISDIR(mode):
                pending.append(child)
            elif stat.S_ISREG(mode):
                result.append(child)
            else:
                raise WorksetInventoryIntegrityError(
                    f"{name} contains a non-regular entry"
                )
    return tuple(sorted(result, key=lambda path: path.as_posix()))


def _shared_suffix(path: Path) -> str:
    for suffix in _SHARED_OBJECT_SUFFIXES:
        ending = suffix
        if path.name.endswith(ending):
            prefix = path.name[: -len(ending)]
            if len(prefix) == 64 and all(char in "0123456789abcdef" for char in prefix):
                return suffix
    raise WorksetInventoryIntegrityError(
        "Shared object namespace contains a non-allowlisted name"
    )


def _json_artifact_references(
    payload: object, *, active_root: Path, object_root: Path
) -> tuple[Path, ...]:
    result: list[Path] = []

    def visit(value: object) -> None:
        if isinstance(value, dict):
            if set(value) == {"path", "sha256", "size_bytes"}:
                path_value = value.get("path")
                sha_value = value.get("sha256")
                size_value = value.get("size_bytes")
                if not isinstance(path_value, str):
                    raise WorksetInventoryIntegrityError(
                        "Artifact reference path changed type"
                    )
                candidate = Path(path_value)
                if not candidate.is_absolute():
                    candidate = active_root / candidate
                candidate = Path(os.path.abspath(candidate))
                try:
                    candidate.relative_to(object_root)
                except ValueError:
                    return
                _hash(sha_value, name="artifact reference SHA-256")
                _positive_int(
                    size_value, name="artifact reference size", allow_zero=True
                )
                result.append(candidate)
                return
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return tuple(result)


def _shared_object_closure(
    state: _VerifiedState, root_documents: Sequence[Path]
) -> tuple[ArtifactRef, ...]:
    source_module = state.source_module
    object_root = source_module._runtime_path(  # noqa: SLF001
        state.source_profile, "objects", root=state.active_root
    )
    object_root = Path(os.path.abspath(object_root))
    namespace_files = _direct_regular_files(
        object_root, name="active shared object namespace"
    )
    namespace = {
        Path(os.path.abspath(path)): _shared_suffix(path) for path in namespace_files
    }
    reachable: set[Path] = set()
    queue: list[Path] = [Path(os.path.abspath(path)) for path in root_documents]
    inspected_roots: set[Path] = set()
    while queue:
        path = queue.pop()
        if path in inspected_roots:
            continue
        inspected_roots.add(path)
        try:
            snapshot = registry._read_regular(  # noqa: SLF001
                path,
                name="shared-object closure document",
                maximum_bytes=state.maximum_bytes,
            )
        except registry.EpochRegistryError as exc:
            raise WorksetInventoryIntegrityError(str(exc)) from exc
        if path in namespace:
            suffix = namespace[path]
            expected = path.name[: -len(suffix)]
            if snapshot.sha256 != expected:
                raise WorksetInventoryIntegrityError(
                    "Shared object filename/content digest changed"
                )
            reachable.add(path)
        try:
            payload = registry._decode_json(  # noqa: SLF001
                snapshot.raw, name="shared-object closure JSON"
            )
        except registry.EpochRegistryError as exc:
            raise WorksetInventoryIntegrityError(str(exc)) from exc
        for reference in _json_artifact_references(
            payload, active_root=state.active_root, object_root=object_root
        ):
            if reference not in namespace:
                raise WorksetInventoryIntegrityError(
                    "Artifact reference points outside the allowlisted shared objects"
                )
            queue.append(reference)
    orphaned = sorted(set(namespace) - reachable, key=lambda path: path.name)
    if orphaned:
        raise WorksetInventoryIntegrityError(
            f"Shared object namespace contains an orphan:{orphaned[0].name}"
        )
    refs: list[ArtifactRef] = []
    for path, suffix in sorted(namespace.items(), key=lambda pair: pair[0].name):
        role = {
            ".feed.json": "source_feed_object",
            ".source.json": "source_dataset_object",
            ".source-manifest.json": "source_semantic_manifest_object",
            ".source-revision.json": "source_revision_receipt_object",
            ".source-pointer.json": "source_pointer_object",
            ".source-snapshot-receipt.json": "source_snapshot_receipt_object",
            ".issue.json": "exact_issue_object",
            ".outcome-input.json": "outcome_source_manifest_object",
            ".outcome.json": "exact_outcome_object",
            ".json": "issue_input_manifest_object",
        }[suffix]
        refs.append(
            _artifact(
                path,
                role=role,
                root_label="active",
                root=state.active_root,
                maximum_bytes=state.maximum_bytes,
            )
        )
    return tuple(refs)


def _shared_root_documents(state: _VerifiedState) -> tuple[Path, ...]:
    source = state.source_module
    paths: list[Path] = [
        source._runtime_path(  # noqa: SLF001
            state.source_profile, "current_source_pointer", root=state.active_root
        ),
        source._runtime_path(  # noqa: SLF001
            state.source_profile, "activation_source_manifest", root=state.active_root
        ),
    ]
    snapshot = getattr(state.source, "snapshot_receipt", None)
    if snapshot is not None:
        paths.append(Path(snapshot.path))
    runtime_directories = (
        state.active_root / state.source_profile["runtime"]["issue_inbox"],
        state.active_root / state.source_profile["runtime"]["issue_receipts"],
        state.active_root / "issue_replay_receipts",
        state.active_root / "outcome_inbox",
        state.active_root / "outcome_receipts",
        state.active_root / "verified_live_intents",
        state.active_root / "verified_live_completions",
    )
    for directory in runtime_directories:
        for path in _recursive_regular_files(
            directory, name=f"shared object root registry {directory.name}"
        ):
            if path.suffix == ".json":
                paths.append(path)
    return tuple(dict.fromkeys(Path(os.path.abspath(path)) for path in paths))


def _source_record_payload(record: object) -> dict[str, object]:
    return {
        "target_date": getattr(record, "day").isoformat(),
        "source_revision_id": _text(
            getattr(record, "revision_id", None), name="source revision id"
        ),
        "observed_at_utc": _text(
            getattr(record, "observed_at_utc", None), name="source observed time"
        ),
        "available_at_utc": _text(
            getattr(record, "available_at_utc", None), name="source available time"
        ),
        "finalized_at_utc": _text(
            getattr(record, "finalized_at_utc", None), name="source finalized time"
        ),
    }


def _live_prerequisite_artifacts(state: _VerifiedState) -> tuple[ArtifactRef, ...]:
    source = state.prerequisites.source
    model = state.prerequisites.model
    values = [
        _artifact(
            source.path,
            role="live_activation_source",
            root_label="active",
            root=state.active_root,
            maximum_bytes=state.maximum_bytes,
        ),
        _artifact(
            source.data_manifest.path,
            role="live_activation_data_manifest",
            root_label="active",
            root=state.active_root,
            maximum_bytes=state.maximum_bytes,
        ),
        _artifact(
            model.path,
            role="live_model_manifest",
            root_label="active",
            root=state.active_root,
            maximum_bytes=state.maximum_bytes,
        ),
        _artifact(
            model.training_manifest.path,
            role="live_model_training_manifest",
            root_label="active",
            root=state.active_root,
            maximum_bytes=state.maximum_bytes,
        ),
    ]
    values.extend(
        _artifact(
            checkpoint.path,
            role=f"live_model_checkpoint_{index}",
            root_label="active",
            root=state.active_root,
            maximum_bytes=state.maximum_bytes,
        )
        for index, checkpoint in enumerate(model.checkpoints)
    )
    return _dedupe_artifacts(values)


def _inventory_source_authority(
    state: _VerifiedState,
    builders: dict[str, _FamilyBuilder],
    items: list[InventoryItem],
) -> None:
    builder = builders["outcome_revision"]
    source_module = state.source_module
    pointer = source_module._runtime_path(  # noqa: SLF001
        state.source_profile, "current_source_pointer", root=state.active_root
    )
    activation = source_module._runtime_path(  # noqa: SLF001
        state.source_profile, "activation_source_manifest", root=state.active_root
    )
    public_artifacts = [
        _artifact(
            pointer,
            role="current_source_pointer",
            root_label="active",
            root=state.active_root,
            maximum_bytes=state.maximum_bytes,
        ),
        _artifact(
            activation,
            role="activation_source_manifest",
            root_label="active",
            root=state.active_root,
            maximum_bytes=state.maximum_bytes,
        ),
    ]
    incoming_path = source_module._runtime_path(  # noqa: SLF001
        state.source_profile, "incoming_feed", root=state.active_root
    )
    incoming_artifact: ArtifactRef | None = None
    feed = None
    if incoming_path.exists() or incoming_path.is_symlink():
        incoming_artifact = _artifact(
            incoming_path,
            role="incoming_finalized_feed",
            root_label="active",
            root=state.active_root,
            maximum_bytes=state.maximum_bytes,
        )
        try:
            snapshot = registry._read_regular(  # noqa: SLF001
                incoming_path,
                name="incoming finalized feed",
                maximum_bytes=state.maximum_bytes,
            )
            feed = source_module._parse_feed(  # noqa: SLF001
                snapshot.raw, state.source_profile, now=state.now
            )
            if not source_module._validate_feed_extension(  # noqa: SLF001
                feed, state.source_profile
            ):
                raise WorksetInventoryIncompleteError(
                    "Incoming feed has no post-baseline finalized extension"
                )
        except WorksetInventoryError:
            raise
        except Exception as exc:
            raise WorksetInventoryIntegrityError(
                f"Incoming finalized feed failed replay:{type(exc).__name__}:{exc}"
            ) from exc
    roots = list(_shared_root_documents(state))
    if incoming_artifact is not None:
        roots.append(incoming_path)
    shared = _shared_object_closure(state, roots)
    source_suffixes = (
        "source_feed_object",
        "source_dataset_object",
        "source_semantic_manifest_object",
        "source_revision_receipt_object",
        "source_pointer_object",
        "source_snapshot_receipt_object",
    )
    source_objects = tuple(value for value in shared if value.role in source_suffixes)
    issue_objects = tuple(
        value
        for value in shared
        if value.role in {"exact_issue_object", "issue_input_manifest_object"}
    )
    outcome_objects = tuple(
        value
        for value in shared
        if value.role in {"outcome_source_manifest_object", "exact_outcome_object"}
    )
    builder.artifacts.extend((*public_artifacts, *source_objects, *outcome_objects))
    builders["issue_route_replay"].artifacts.extend(issue_objects)
    current_records = tuple(
        _source_record_payload(value) for value in state.source.records
    )
    activation_records = tuple(
        _source_record_payload(value) for value in state.activation_source.records
    )
    snapshot_receipt = getattr(state.source, "snapshot_receipt", None)
    authority = {
        "outcome_source_id": _text(
            state.source.outcome_source_id, name="current outcome source id"
        ),
        "current_watermark": state.source.watermark.isoformat(),
        "activation_watermark": state.activation_source.watermark.isoformat(),
        "current_exported_at_utc": state.source.exported_at_utc,
        "snapshot_sequence_id": _positive_int(
            state.source.snapshot_sequence_id, name="source snapshot sequence"
        ),
        "snapshot_receipt_sha256": (
            _hash(snapshot_receipt.sha256, name="source snapshot receipt")
            if snapshot_receipt is not None
            else None
        ),
        "current_records": list(current_records),
        "activation_records": list(activation_records),
        "revision_ids_by_date": [
            {"target_date": day, "revision_ids": list(revisions)}
            for day, revisions in state.source.revision_ids_by_date
        ],
        "shared_object_count": len(shared),
        "shared_object_namespace_sha256": _sha256(
            _canonical_bytes([_artifact_payload(value) for value in shared])
        ),
    }
    builder.authority["source_authority"] = authority
    builder.records.append(
        {
            "record_type": "source_authority",
            "snapshot_sequence_id": state.source.snapshot_sequence_id,
            "snapshot_receipt_sha256": authority["snapshot_receipt_sha256"],
            "terminal": True,
        }
    )
    if feed is None:
        return
    if feed.outcome_source_id != state.source.outcome_source_id:
        raise WorksetInventoryIntegrityError("Incoming feed source id changed")
    old_records = tuple(state.source.records)
    new_records = tuple(feed.records)
    changed: list[dict[str, object]] = []
    if len(new_records) < len(old_records):
        raise WorksetInventoryIntegrityError("Incoming feed rolled back source history")
    known = {day: set(values) for day, values in state.source.revision_ids_by_date}
    for old, new in zip(old_records, new_records, strict=False):
        if old.day != new.day:
            raise WorksetInventoryIntegrityError(
                "Incoming feed changed source date order"
            )
        if old == new:
            continue
        if old.revision_id == new.revision_id:
            raise WorksetInventoryIntegrityError(
                "Incoming feed reused a revision id with changed semantics"
            )
        if new.revision_id in known.get(new.day.isoformat(), set()):
            raise WorksetInventoryIntegrityError(
                "Incoming feed rolled back to a known source revision"
            )
        changed.append(_source_record_payload(new))
    for record in new_records[len(old_records) :]:
        changed.append(_source_record_payload(record))
    if not changed and new_records == old_records:
        builder.records.append(
            {
                "record_type": "incoming_finalized_feed",
                "feed_sha256": incoming_artifact.sha256,
                "terminal": True,
                "semantic_state": "already_current",
            }
        )
        builder.artifacts.append(incoming_artifact)
        return
    exported = source_module._utc(feed.exported_at_text, name="incoming exported_at")  # noqa: SLF001
    current_exported = source_module._utc(  # noqa: SLF001
        state.source.exported_at_utc, name="current exported_at"
    )
    if exported <= current_exported:
        raise WorksetInventoryIntegrityError(
            "Changed incoming feed did not advance export time"
        )
    revisions_text = ",".join(
        f"{value['target_date']}={value['source_revision_id']}" for value in changed
    )
    natural_key = _key(
        "outcome_revision",
        state.identity["old_live_epoch_id"],
        "source-ingest",
        state.source.outcome_source_id,
        state.source.snapshot_sequence_id,
        incoming_artifact.sha256,
        revisions_text,
    )
    item = _item(
        "outcome_revision",
        natural_key,
        "source_snapshot_ingested",
        (*public_artifacts, incoming_artifact, *source_objects),
        {
            "action": "ingest_incoming_finalized_feed",
            "old_live_epoch_id": state.identity["old_live_epoch_id"],
            "outcome_source_id": state.source.outcome_source_id,
            "predecessor_snapshot_sequence_id": state.source.snapshot_sequence_id,
            "predecessor_snapshot_receipt_sha256": authority["snapshot_receipt_sha256"],
            "incoming_feed_sha256": incoming_artifact.sha256,
            "incoming_exported_at_utc": feed.exported_at_text,
            "changed_or_appended_revisions": changed,
        },
    )
    items.append(item)
    builder.artifacts.append(incoming_artifact)
    builder.records.append(
        {
            "record_type": "incoming_finalized_feed",
            "feed_sha256": incoming_artifact.sha256,
            "terminal": False,
            "natural_key": natural_key,
            "changed_or_appended_revisions": changed,
        }
    )


def _inventory_issues_and_live(
    state: _VerifiedState,
    builders: dict[str, _FamilyBuilder],
    items: list[InventoryItem],
) -> None:
    _inventory_issue_routes(state, builders, items)
    _inventory_live_anchors(state, builders, items)


def _inventory_issue_routes(
    state: _VerifiedState,
    builders: dict[str, _FamilyBuilder],
    items: list[InventoryItem],
) -> None:
    from monitoring import ootang_issue_producer as producer
    from monitoring import ootang_issue_replay as replay

    builder = builders["issue_route_replay"]
    try:
        profile = producer.load_config()
        receipt_root = producer.required_path(
            state.active_root, profile["runtime"]["issue_receipts"]
        )
        object_root = producer.required_path(
            state.active_root, profile["runtime"]["objects"]
        )
        routes = _strict_dated_records(
            state.live_paths.issue_inbox, ".json", name="canonical issue routes"
        )
        producer_receipts = _strict_dated_records(
            receipt_root, ".json", name="issue producer receipts"
        )
        replay_profile = replay.load_replay_profile()
        replay_paths = replay.runtime_paths(
            replay_profile, runtime_root=state.active_root
        )
        replay_receipts = _strict_dated_records(
            replay_paths.receipts, ".json", name="issue replay receipts"
        )
    except WorksetInventoryError:
        raise
    except Exception as exc:
        raise WorksetInventoryIntegrityError(
            f"Cannot enumerate issue route namespaces:{type(exc).__name__}:{exc}"
        ) from exc
    if set(producer_receipts) - set(routes):
        raise WorksetInventoryIntegrityError(
            "Issue producer receipt exists without a canonical route"
        )
    if set(replay_receipts) - set(routes):
        raise WorksetInventoryIntegrityError(
            "Issue replay receipt exists without a canonical route"
        )
    events = tuple(state.live_projection.ledger_events)
    builder.authority["live_logical_chain"] = _logical_ledger_authority(
        state.live_projection,
        schema="ootang_prequential_live_logical_chain_v1",
        epoch_field="epoch_id",
    )
    for target, issue_path in sorted(routes.items()):
        producer_path = producer_receipts.get(target)
        if producer_path is None:
            raise WorksetInventoryIntegrityError(
                f"Canonical issue route lacks producer receipt:{target}"
            )
        try:
            receipt, exact_object, _, registered_raw, _ = producer._load_issue_receipt(  # noqa: SLF001
                producer_path, issue_path=issue_path, object_root=object_root
            )
            issue_snapshot = registry._read_regular(  # noqa: SLF001
                issue_path,
                name="canonical issue route",
                maximum_bytes=state.maximum_bytes,
            )
            if issue_snapshot.raw != registered_raw:
                raise WorksetInventoryIntegrityError(
                    "Canonical issue differs from registered exact object"
                )
            batch = state.live_module.load_issue_batch(
                issue_path, state.live_profile, state.prerequisites
            )
        except WorksetInventoryError:
            raise
        except Exception as exc:
            raise WorksetInventoryIntegrityError(
                f"Issue route failed recursive replay:{target}:{type(exc).__name__}:{exc}"
            ) from exc
        target_text = target.isoformat()
        expected_issue_id = state.live_module._issue_batch_id(  # noqa: SLF001
            state.live_projection.epoch_id, target
        )
        opened = [
            event
            for event in events
            if event.event_type == "issue_batch_opened"
            and event.target_date == target_text
        ]
        sealed = [
            event
            for event in events
            if event.event_type == "issue_batch_sealed"
            and event.target_date == target_text
        ]
        settled = [
            event
            for event in events
            if event.event_type == "outcome_batch_settled"
            and event.target_date == target_text
        ]
        if any(len(group) > 1 for group in (opened, sealed, settled)):
            raise WorksetInventoryIntegrityError(
                f"Issue route has a non-unique live lifecycle:{target_text}"
            )
        if opened:
            if (
                opened[0].issue_id != expected_issue_id
                or opened[0].payload.get("issue_feed_sha256") != batch.sha256
                or opened[0].input_manifest_sha256 != batch.input_manifest.sha256
            ):
                raise WorksetInventoryIntegrityError(
                    f"Issue route/opened-event binding changed:{target_text}"
                )
        if sealed and (
            not opened
            or sealed[0].issue_id != expected_issue_id
            or sealed[0].input_manifest_sha256 != batch.input_manifest.sha256
            or sealed[0].sequence_id <= opened[0].sequence_id
        ):
            raise WorksetInventoryIntegrityError(
                f"Issue route/seal binding changed:{target_text}"
            )
        if settled and (
            not sealed
            or settled[0].issue_id != expected_issue_id
            or settled[0].payload.get("issue_batch_sealed_entry_sha256")
            != sealed[0].entry_sha256
            or settled[0].sequence_id <= sealed[0].sequence_id
        ):
            raise WorksetInventoryIntegrityError(
                f"Issue route/settlement binding changed:{target_text}"
            )
        artifacts = [
            _artifact(
                issue_path,
                role="canonical_issue",
                root_label="active",
                root=state.active_root,
                maximum_bytes=state.maximum_bytes,
            ),
            _artifact(
                receipt.path,
                role="issue_producer_receipt",
                root_label="active",
                root=state.active_root,
                maximum_bytes=state.maximum_bytes,
            ),
            _artifact(
                exact_object.path,
                role="exact_issue_object",
                root_label="active",
                root=state.active_root,
                maximum_bytes=state.maximum_bytes,
            ),
            _artifact(
                batch.input_manifest.path,
                role="issue_input_manifest",
                root_label="active",
                root=state.active_root,
                maximum_bytes=state.maximum_bytes,
            ),
        ]
        artifacts.extend(_live_prerequisite_artifacts(state))
        replay_path = replay_receipts.get(target)
        replay_verified = False
        if replay_path is not None:
            try:
                replay.load_verified_replay_receipt(
                    target,
                    runtime_root=state.active_root,
                    expected_issue_sha256=batch.sha256,
                    expected_input_manifest_sha256=batch.input_manifest.sha256,
                    expected_model_manifest_sha256=batch.model_manifest_sha256,
                )
            except Exception as exc:
                raise WorksetInventoryIntegrityError(
                    f"Issue replay receipt failed verification:{target}:{exc}"
                ) from exc
            artifacts.append(
                _artifact(
                    replay_path,
                    role="issue_replay_receipt",
                    root_label="active",
                    root=state.active_root,
                    maximum_bytes=state.maximum_bytes,
                )
            )
            replay_verified = True
        seal_hash = sealed[0].entry_sha256 if sealed else ZERO_HASH
        terminal = bool(settled)
        record = {
            "target_date": target_text,
            "old_live_epoch_id": state.live_projection.epoch_id,
            "issue_id": expected_issue_id,
            "issue_sha256": batch.sha256,
            "input_manifest_sha256": batch.input_manifest.sha256,
            "replay_receipt_verified": replay_verified,
            "opened_event": _event_record(opened[0]) if opened else None,
            "seal_event": _event_record(sealed[0]) if sealed else None,
            "settled_event": _event_record(settled[0]) if settled else None,
            "terminal": terminal,
        }
        builder.records.append(record)
        builder.artifacts.extend(artifacts)
        if terminal:
            continue
        successor = (
            "issue_replay_receipt_verified"
            if not replay_verified
            else "issue_route_replay_consumed"
        )
        natural_key = _key(
            "issue_route_replay",
            state.live_projection.epoch_id,
            target_text,
            expected_issue_id,
            seal_hash,
            batch.sha256,
        )
        items.append(
            _item(
                "issue_route_replay",
                natural_key,
                successor,
                artifacts,
                {
                    **record,
                    "action": successor,
                    "terminal": False,
                },
            )
        )


def _inventory_live_anchors(
    state: _VerifiedState,
    builders: dict[str, _FamilyBuilder],
    items: list[InventoryItem],
) -> None:
    builder = builders["live_outstanding"]
    projection = state.live_projection
    events = tuple(projection.ledger_events)
    builder.authority["live_logical_chain"] = _logical_ledger_authority(
        projection,
        schema="ootang_prequential_live_logical_chain_v1",
        epoch_field="epoch_id",
    )
    seals = {
        event.entry_sha256: event
        for event in events
        if event.event_type == "issue_batch_sealed"
    }
    confirmations: dict[str, object] = {}
    for event in events:
        if event.event_type != "anchor_confirmed":
            continue
        root = event.payload.get("sealed_entry_sha256")
        if not isinstance(root, str) or root not in seals or root in confirmations:
            raise WorksetInventoryIntegrityError(
                "Anchor confirmation has no unique matching live seal"
            )
        state.live_module._stored_anchor_payload(  # noqa: SLF001
            dict(event.payload), sealed_entry_sha256=root
        )
        confirmations[root] = event
    anchor_files = _direct_regular_files(
        state.live_paths.anchors, name="live anchor receipt namespace"
    )
    by_name = {path.name: path for path in anchor_files}
    expected_names = {
        f"{seals[root].target_date}_{root}.json": root for root in confirmations
    }
    if set(by_name) - set(expected_names):
        raise WorksetInventoryIntegrityError(
            "Live anchor namespace contains an orphan receipt"
        )
    missing_repair_keys: dict[str, str] = {}
    for name, root in sorted(expected_names.items()):
        seal = seals[root]
        confirmed = confirmations[root]
        expected = state.live_module._stored_anchor_payload(  # noqa: SLF001
            dict(confirmed.payload), sealed_entry_sha256=root
        )
        path = by_name.get(name)
        artifacts: list[ArtifactRef] = []
        terminal = path is not None
        if path is not None:
            payload, _ = _read_json(
                path, name="live anchor receipt", maximum_bytes=state.maximum_bytes
            )
            if payload != expected:
                raise WorksetInventoryIntegrityError(
                    "Live anchor receipt differs from confirmed event"
                )
            artifact = _artifact(
                path,
                role="live_anchor_receipt",
                root_label="active",
                root=state.active_root,
                maximum_bytes=state.maximum_bytes,
            )
            artifacts.append(artifact)
            builder.artifacts.append(artifact)
        record = {
            "record_type": "anchor_confirmation",
            "target_date": seal.target_date,
            "old_live_epoch_id": projection.epoch_id,
            "issue_id": seal.issue_id,
            "seal_entry_sha256": root,
            "confirmed_event": _event_record(confirmed),
            "receipt_present": terminal,
            "terminal": terminal,
        }
        builder.records.append(record)
        if not terminal:
            source_pointer = state.source_module._runtime_path(  # noqa: SLF001
                state.source_profile,
                "current_source_pointer",
                root=state.active_root,
            )
            repair_artifacts = (
                _artifact(
                    source_pointer,
                    role="anchor_repair_live_prerequisite_source_pointer",
                    root_label="active",
                    root=state.active_root,
                    maximum_bytes=state.maximum_bytes,
                ),
            )
            natural_key = _key(
                "live_outstanding",
                projection.epoch_id,
                seal.target_date,
                seal.issue_id,
                root,
                "anchor-receipt-repair",
            )
            missing_repair_keys[root] = natural_key
            items.append(
                _item(
                    "live_outstanding",
                    natural_key,
                    "anchor_receipt_repaired",
                    repair_artifacts,
                    {**record, "action": "repair_from_confirmed_event"},
                )
            )
            builder.artifacts.extend(repair_artifacts)
    target = projection.outstanding_target_date
    if target is None:
        return
    seal = projection.seal_event
    issue_id = projection.outstanding_issue_id
    if seal is None or issue_id is None or seal.target_date != target.isoformat():
        raise WorksetInventoryIntegrityError(
            "Outstanding live target lacks its exact seal/issue identity"
        )
    issue_path = state.live_paths.issue_inbox / f"{target.isoformat()}.json"
    try:
        batch = state.live_module.load_issue_batch(
            issue_path, state.live_profile, state.prerequisites
        )
    except Exception as exc:
        raise WorksetInventoryIntegrityError(
            f"Outstanding live issue cannot be replayed:{exc}"
        ) from exc
    artifacts = [
        _artifact(
            issue_path,
            role="outstanding_live_issue",
            root_label="active",
            root=state.active_root,
            maximum_bytes=state.maximum_bytes,
        ),
        _artifact(
            batch.input_manifest.path,
            role="outstanding_issue_input_manifest",
            root_label="active",
            root=state.active_root,
            maximum_bytes=state.maximum_bytes,
        ),
    ]
    artifacts.extend(_live_prerequisite_artifacts(state))
    confirmation = confirmations.get(seal.entry_sha256)
    dependency_keys: tuple[str, ...] = ()
    if confirmation is not None:
        expected_name = f"{target.isoformat()}_{seal.entry_sha256}.json"
        anchor_path = by_name.get(expected_name)
        if anchor_path is not None:
            artifacts.append(
                _artifact(
                    anchor_path,
                    role="outstanding_live_anchor_receipt",
                    root_label="active",
                    root=state.active_root,
                    maximum_bytes=state.maximum_bytes,
                )
            )
        else:
            dependency_keys = (missing_repair_keys[seal.entry_sha256],)
        successor = "outcome_batch_settled"
    else:
        lifecycle = [
            event
            for event in events
            if event.target_date == target.isoformat()
            and event.payload.get("sealed_entry_sha256") == seal.entry_sha256
            and event.event_type
            in {"anchor_requested", "anchor_confirmed", "anchor_failed"}
        ]
        requested = sum(event.event_type == "anchor_requested" for event in lifecycle)
        results = sum(
            event.event_type in {"anchor_confirmed", "anchor_failed"}
            for event in lifecycle
        )
        successor = (
            "anchor_result_recorded"
            if requested > results
            else "anchor_request_recorded"
        )
    natural_key = _key(
        "live_outstanding",
        projection.epoch_id,
        target.isoformat(),
        issue_id,
        seal.entry_sha256,
        batch.sha256,
    )
    record = {
        "record_type": "outstanding_live_lifecycle",
        "target_date": target.isoformat(),
        "old_live_epoch_id": projection.epoch_id,
        "issue_id": issue_id,
        "issue_sha256": batch.sha256,
        "input_manifest_sha256": batch.input_manifest.sha256,
        "seal_event": _event_record(seal),
        "anchor_confirmed_event": (
            _event_record(confirmation) if confirmation is not None else None
        ),
        "frozen_live_upper_tip": state.identity["live_terminal_sha256"],
        "terminal": False,
    }
    builder.records.append(record)
    builder.artifacts.extend(artifacts)
    items.append(
        _item(
            "live_outstanding",
            natural_key,
            successor,
            artifacts,
            {**record, "action": successor},
            dependency_keys=dependency_keys,
        )
    )


def _inventory_outcomes(
    state: _VerifiedState,
    builders: dict[str, _FamilyBuilder],
    items: list[InventoryItem],
) -> None:
    _inventory_outcome_registry(state, builders, items)


def _add_dependency_to_item(
    items: list[InventoryItem], natural_key: str, dependency: str
) -> None:
    for index, item in enumerate(items):
        if item.natural_key != natural_key:
            continue
        items[index] = _item(
            item.family,
            item.natural_key,
            item.canonical_successor_state,
            item.artifacts,
            item.authority,
            dependency_keys=(*item.dependency_keys, dependency),
        )
        return


def _add_outcome_dependency_to_live_settlement(
    items: list[InventoryItem], *, target: date, dependency: str
) -> None:
    """Bind a same-date live settlement to its first reserved outcome key."""

    for item in tuple(items):
        if (
            item.family == "live_outstanding"
            and item.authority.get("target_date") == target.isoformat()
            and item.canonical_successor_state == "outcome_batch_settled"
        ):
            # Receipt-chain items are enumerated before later source candidates.
            # Once one exact outcome is reserved to settle the outstanding issue,
            # any newer same-date candidate is a post-settlement revision instead.
            existing = tuple(
                value
                for value in item.dependency_keys
                if value.startswith("outcome_revision:")
            )
            if dependency in existing:
                continue
            if existing and dependency not in existing:
                continue
            _add_dependency_to_item(items, item.natural_key, dependency)


def _source_artifacts_for_record(
    state: _VerifiedState, target: date
) -> tuple[ArtifactRef, ...]:
    source = state.source_module
    result = [
        _artifact(
            source._runtime_path(  # noqa: SLF001
                state.source_profile,
                "current_source_pointer",
                root=state.active_root,
            ),
            role="current_source_pointer",
            root_label="active",
            root=state.active_root,
            maximum_bytes=state.maximum_bytes,
        ),
        _artifact(
            source._runtime_path(  # noqa: SLF001
                state.source_profile,
                "activation_source_manifest",
                root=state.active_root,
            ),
            role="activation_source_manifest",
            root_label="active",
            root=state.active_root,
            maximum_bytes=state.maximum_bytes,
        ),
        _artifact(
            state.source.semantic_manifest.path,
            role="current_source_semantic_manifest",
            root_label="active",
            root=state.active_root,
            maximum_bytes=state.maximum_bytes,
        ),
    ]
    for record, head in zip(
        state.source.records, state.source.revision_heads, strict=True
    ):
        if record.day == target:
            result.append(
                _artifact(
                    head.path,
                    role="current_source_revision_head",
                    root_label="active",
                    root=state.active_root,
                    maximum_bytes=state.maximum_bytes,
                )
            )
            break
    snapshot = getattr(state.source, "snapshot_receipt", None)
    if snapshot is not None:
        result.append(
            _artifact(
                snapshot.path,
                role="current_source_snapshot_receipt",
                root_label="active",
                root=state.active_root,
                maximum_bytes=state.maximum_bytes,
            )
        )
    return _dedupe_artifacts(result)


def _inventory_outcome_registry(
    state: _VerifiedState,
    builders: dict[str, _FamilyBuilder],
    items: list[InventoryItem],
) -> None:
    from monitoring import ootang_epoch_drain as drain
    from monitoring import ootang_outcome_materializer as outcomes

    builder = builders["outcome_revision"]
    try:
        profile = outcomes.load_config()
        receipt_root = outcomes._runtime_path(  # noqa: SLF001
            profile, state.active_root, "outcome_receipts"
        )
        inbox_root = outcomes._runtime_path(  # noqa: SLF001
            profile, state.active_root, "outcome_inbox"
        )
        targets = drain._strict_outcome_receipt_targets(receipt_root)  # noqa: SLF001
        chains: dict[str, Any] = {}
        for target in targets:
            chain = outcomes._scan_receipt_chain(  # noqa: SLF001
                target=target,
                profile=profile,
                root=state.active_root,
                live_module=state.live_module,
                live_profile=profile["_live_profile"],
                prerequisites=state.prerequisites,
            )
            if chain is None:
                raise WorksetInventoryIntegrityError(
                    f"Outcome receipt date lacks a unique chain:{target}"
                )
            chains[target.isoformat()] = chain
        active_records = _strict_dated_records(
            inbox_root, ".json", name="outcome active inbox"
        )
        if set(active_records) - set(targets):
            raise WorksetInventoryIntegrityError(
                "Outcome inbox contains an orphan active outcome"
            )
        outcomes._pending_registered_tip(  # noqa: SLF001
            chains,
            projection=state.live_projection,
            source=state.source,
            profile=profile,
        )
    except WorksetInventoryError:
        raise
    except Exception as exc:
        raise WorksetInventoryIntegrityError(
            f"Outcome registry failed recursive replay:{type(exc).__name__}:{exc}"
        ) from exc
    pending_tip_key: str | None = None
    for target_text, chain in sorted(chains.items()):
        target = date.fromisoformat(target_text)
        active_path = outcomes._outcome_inbox_path(  # noqa: SLF001
            profile, state.active_root, target
        )
        try:
            active_raw = outcomes._legal_active_bytes(chain, active_path)  # noqa: SLF001
        except Exception as exc:
            raise WorksetInventoryIntegrityError(str(exc)) from exc
        artifacts: list[ArtifactRef] = []
        receipt_records: list[dict[str, object]] = []
        known = state.live_projection.revision_ids.get(target_text, {})
        for registered in chain.receipts:
            receipt_artifact = _artifact(
                registered.receipt.path,
                role="outcome_receipt",
                root_label="active",
                root=state.active_root,
                maximum_bytes=state.maximum_bytes,
            )
            exact_artifact = _artifact(
                registered.exact_object.path,
                role="exact_outcome_object",
                root_label="active",
                root=state.active_root,
                maximum_bytes=state.maximum_bytes,
            )
            source_manifest = outcomes._artifact_from_mapping(  # noqa: SLF001
                registered.payload["source_manifest"],
                name="registered outcome source manifest",
            )
            source_artifact = _artifact(
                source_manifest.path,
                role="outcome_source_manifest",
                root_label="active",
                root=state.active_root,
                maximum_bytes=state.maximum_bytes,
            )
            artifacts.extend((receipt_artifact, exact_artifact, source_artifact))
            revision = _text(
                registered.payload["source_revision_id"],
                name="registered outcome source revision",
            )
            receipt_records.append(
                {
                    "source_revision_id": revision,
                    "revision_sequence_id": registered.revision_sequence_id,
                    "receipt_sha256": registered.receipt.sha256,
                    "exact_outcome_sha256": registered.exact_object.sha256,
                    "source_manifest_sha256": source_manifest.sha256,
                    "ledger_consumed": revision in known,
                }
            )
        if chain.active_pointer_path.exists():
            artifacts.append(
                _artifact(
                    chain.active_pointer_path,
                    role="active_outcome_receipt_pointer",
                    root_label="active",
                    root=state.active_root,
                    maximum_bytes=state.maximum_bytes,
                )
            )
        if active_path.exists():
            artifacts.append(
                _artifact(
                    active_path,
                    role="active_outcome",
                    root_label="active",
                    root=state.active_root,
                    maximum_bytes=state.maximum_bytes,
                )
            )
        tip_revision = _text(
            chain.tip.payload["source_revision_id"], name="outcome tip revision"
        )
        published_tip = (
            chain.pointed is not None
            and chain.pointed.receipt.sha256 == chain.tip.receipt.sha256
            and active_raw == chain.tip.raw
        )
        consumed_tip = tip_revision in known
        terminal = published_tip and consumed_tip
        record = {
            "record_type": "outcome_receipt_chain",
            "target_date": target_text,
            "old_live_epoch_id": state.live_projection.epoch_id,
            "tip_source_revision_id": tip_revision,
            "tip_receipt_sha256": chain.tip.receipt.sha256,
            "tip_exact_outcome_sha256": chain.tip.exact_object.sha256,
            "tip_published": published_tip,
            "tip_ledger_consumed": consumed_tip,
            "receipts": receipt_records,
            "terminal": terminal,
        }
        builder.records.append(record)
        builder.artifacts.extend(artifacts)
        if terminal:
            continue
        successor = (
            "outcome_materialized"
            if not published_tip
            else "outcome_or_revision_consumed"
        )
        seal = next(
            (
                event
                for event in state.live_projection.ledger_events
                if event.event_type == "issue_batch_sealed"
                and event.target_date == target_text
            ),
            None,
        )
        seal_hash = seal.entry_sha256 if seal is not None else ZERO_HASH
        natural_key = _key(
            "outcome_revision",
            state.live_projection.epoch_id,
            target_text,
            tip_revision,
            seal_hash,
            chain.tip.exact_object.sha256,
        )
        pending_tip_key = natural_key
        items.append(
            _item(
                "outcome_revision",
                natural_key,
                successor,
                artifacts,
                {**record, "action": successor},
            )
        )
        _add_outcome_dependency_to_live_settlement(
            items, target=target, dependency=natural_key
        )

    records_by_date = {record.day: record for record in state.source.records}
    candidate_specs: list[tuple[str, date, object]] = []
    for target_text in sorted(state.live_projection.revision_ids):
        target = date.fromisoformat(target_text)
        record = records_by_date.get(target)
        if record is None:
            if target <= state.source.watermark:
                raise WorksetInventoryIntegrityError(
                    "Current source lost a ledger-known outcome date"
                )
            continue
        known = state.live_projection.revision_ids[target_text]
        if record.revision_id not in known:
            candidate_specs.append(("revision", target, record))
    outstanding = state.live_projection.outstanding_target_date
    if outstanding is not None and outstanding in records_by_date:
        candidate_specs.append(
            ("outstanding", outstanding, records_by_date[outstanding])
        )
    if outstanding is None:
        target = state.live_projection.last_finalized_date + timedelta(days=1)
        while target <= state.source.watermark:
            record = records_by_date.get(target)
            if record is None:
                raise WorksetInventoryIntegrityError(
                    "Current source skipped a contiguous backfill date"
                )
            candidate_specs.append(("backfill", target, record))
            target += timedelta(days=1)
    existing_identities = {
        (record["target_date"], record.get("tip_source_revision_id"))
        for record in builder.records
        if record.get("record_type") == "outcome_receipt_chain"
    }
    prior_key: str | None = pending_tip_key
    source_ingest_keys = [
        item.natural_key
        for item in items
        if item.family == "outcome_revision"
        and item.canonical_successor_state == "source_snapshot_ingested"
    ]
    for kind, target, source_record in candidate_specs:
        revision = _text(source_record.revision_id, name="selected source revision id")
        if (target.isoformat(), revision) in existing_identities:
            continue
        artifacts = list(_source_artifacts_for_record(state, target))
        seal = next(
            (
                event
                for event in state.live_projection.ledger_events
                if event.event_type == "issue_batch_sealed"
                and event.target_date == target.isoformat()
            ),
            None,
        )
        seal_hash = seal.entry_sha256 if seal is not None else ZERO_HASH
        natural_key = _key(
            "outcome_revision",
            state.live_projection.epoch_id,
            target.isoformat(),
            revision,
            seal_hash,
            state.source.outcome_source_id,
        )
        dependencies = tuple(
            value for value in (prior_key, *source_ingest_keys) if value is not None
        )
        authority = {
            "record_type": "machine_selected_source_outcome",
            "selection_kind": kind,
            "target_date": target.isoformat(),
            "old_live_epoch_id": state.live_projection.epoch_id,
            "outcome_source_id": state.source.outcome_source_id,
            "source_revision_id": revision,
            "source_snapshot_sequence_id": state.source.snapshot_sequence_id,
            "source_snapshot_receipt_sha256": state.source.snapshot_receipt.sha256,
            "live_issue_seal_entry_sha256": seal_hash,
            "terminal": False,
        }
        builder.records.append(authority)
        builder.artifacts.extend(artifacts)
        items.append(
            _item(
                "outcome_revision",
                natural_key,
                "outcome_materialized",
                artifacts,
                {**authority, "action": "outcome_materialized"},
                dependency_keys=dependencies,
            )
        )
        prior_key = natural_key
        if outstanding == target:
            _add_outcome_dependency_to_live_settlement(
                items, target=target, dependency=natural_key
            )


def _inventory_guard(
    state: _VerifiedState,
    builders: dict[str, _FamilyBuilder],
    items: list[InventoryItem],
) -> None:
    _inventory_guard_records(state, builders, items)


def _guard_pending_successor(
    *,
    target: date,
    machine_now: datetime,
    target_timezone: str,
    opened_events: Sequence[object],
    sealed_events: Sequence[object],
    target_outcome_or_backfill_present: bool,
) -> str:
    """Choose the only recoverable guard successor without mutating live state."""

    if machine_now.tzinfo is None or machine_now.utcoffset() is None:
        raise WorksetInventoryIntegrityError("Guard decision clock is naive")
    if len(opened_events) > 1 or len(sealed_events) > 1:
        raise WorksetInventoryIntegrityError(
            "Guard target has a non-unique live issue boundary"
        )
    if bool(opened_events) != bool(sealed_events):
        raise WorksetInventoryIntegrityError(
            "Guard target has an incomplete live issue boundary"
        )
    if opened_events:
        return "guard_completion_recorded"
    try:
        target_start = datetime.combine(
            target, time.min, tzinfo=ZoneInfo(target_timezone)
        ).astimezone(timezone.utc)
    except (TypeError, ValueError, ZoneInfoNotFoundError) as exc:
        raise WorksetInventoryIntegrityError(
            "Guard target timezone is invalid"
        ) from exc
    if (
        machine_now.astimezone(timezone.utc) >= target_start
        or target_outcome_or_backfill_present
    ):
        return "superseded_by_backfill"
    return "guard_completion_recorded"


def _inventory_guard_records(
    state: _VerifiedState,
    builders: dict[str, _FamilyBuilder],
    items: list[InventoryItem],
) -> None:
    from monitoring import ootang_verified_live as guard

    builder = builders["guard"]
    try:
        profile = guard.load_verified_live_profile()
        paths = guard.runtime_paths(profile, runtime_root=state.active_root)
        intents = _strict_dated_records(
            paths.intents, ".json", name="verified-live guard intents"
        )
        completions = _strict_dated_records(
            paths.completions, ".json", name="verified-live guard completions"
        )
    except WorksetInventoryError:
        raise
    except Exception as exc:
        raise WorksetInventoryIntegrityError(
            f"Cannot enumerate guard namespace:{type(exc).__name__}:{exc}"
        ) from exc
    if set(completions) - set(intents):
        raise WorksetInventoryIntegrityError("Guard completion exists without intent")
    builder.authority["live_logical_chain"] = _logical_ledger_authority(
        state.live_projection,
        schema="ootang_prequential_live_logical_chain_v1",
        epoch_field="epoch_id",
    )
    for target, intent_path in sorted(intents.items()):
        try:
            intent_payload, intent_snapshot = guard._load_record(  # noqa: SLF001
                intent_path, name="verified-live guard intent"
            )
            issue, input_manifest, replay_receipt = guard._validate_intent_record(  # noqa: SLF001
                profile,
                paths,
                target=target,
                payload=intent_payload,
            )
            guard._validate_pre_head_against_events(  # noqa: SLF001
                intent_payload["live_pre_head"],
                state.live_projection.ledger_events,
            )
        except Exception as exc:
            raise WorksetInventoryIntegrityError(
                f"Guard intent failed recursive replay:{target}:{type(exc).__name__}:{exc}"
            ) from exc
        artifacts = [
            _artifact(
                intent_snapshot.path,
                role="guard_intent",
                root_label="active",
                root=state.active_root,
                maximum_bytes=state.maximum_bytes,
            ),
            _artifact(
                issue.path,
                role="guard_issue",
                root_label="active",
                root=state.active_root,
                maximum_bytes=state.maximum_bytes,
            ),
            _artifact(
                input_manifest.path,
                role="guard_input_manifest",
                root_label="active",
                root=state.active_root,
                maximum_bytes=state.maximum_bytes,
            ),
            _artifact(
                replay_receipt.path,
                role="guard_replay_receipt",
                root_label="active",
                root=state.active_root,
                maximum_bytes=state.maximum_bytes,
            ),
        ]
        completion_path = completions.get(target)
        completion_payload: Mapping[str, object] | None = None
        terminal = completion_path is not None
        if completion_path is not None:
            try:
                completion_payload, completion_snapshot = guard._load_record(  # noqa: SLF001
                    completion_path, name="verified-live guard completion"
                )
                guard._validate_completion_record(  # noqa: SLF001
                    profile,
                    paths,
                    target=target,
                    payload=completion_payload,
                    intent_payload=intent_payload,
                    intent_snapshot=intent_snapshot,
                    events=state.live_projection.ledger_events,
                )
            except Exception as exc:
                raise WorksetInventoryIntegrityError(
                    f"Guard completion failed replay:{target}:{type(exc).__name__}:{exc}"
                ) from exc
            artifacts.append(
                _artifact(
                    completion_snapshot.path,
                    role="guard_completion",
                    root_label="active",
                    root=state.active_root,
                    maximum_bytes=state.maximum_bytes,
                )
            )
        pre_head = intent_payload["live_pre_head"]
        issue_sha = _hash(intent_payload["issue"]["sha256"], name="guard issue")
        record = {
            "target_date": target.isoformat(),
            "old_live_epoch_id": _text(
                pre_head["epoch_id"], name="guard pre-head epoch"
            ),
            "pre_head_sequence_id": _positive_int(
                pre_head["sequence_id"], name="guard pre-head sequence"
            ),
            "pre_head_entry_sha256": _hash(
                pre_head["entry_sha256"], name="guard pre-head entry"
            ),
            "issue_sha256": issue_sha,
            "intent_sha256": intent_snapshot.sha256,
            "completion_sha256": (
                completion_snapshot.sha256 if completion_path is not None else None
            ),
            "terminal": terminal,
        }
        builder.records.append(record)
        builder.artifacts.extend(artifacts)
        if terminal:
            continue
        target_text = target.isoformat()
        events = tuple(state.live_projection.ledger_events)
        opened = tuple(
            event
            for event in events
            if event.event_type == "issue_batch_opened"
            and event.target_date == target_text
        )
        sealed = tuple(
            event
            for event in events
            if event.event_type == "issue_batch_sealed"
            and event.target_date == target_text
        )
        target_work_present = (
            target_text in state.live_projection.backfill_events
            or target_text in state.live_projection.settled_events
            or any(
                item.family == "outcome_revision"
                and item.authority.get("target_date") == target_text
                for item in items
            )
        )
        successor = _guard_pending_successor(
            target=target,
            machine_now=state.now,
            target_timezone=state.live_profile["target"]["date_timezone"],
            opened_events=opened,
            sealed_events=sealed,
            target_outcome_or_backfill_present=target_work_present,
        )
        superseding_event = state.live_projection.backfill_events.get(
            target_text
        ) or state.live_projection.settled_events.get(target_text)
        natural_key = _key(
            "guard",
            pre_head["epoch_id"],
            target.isoformat(),
            pre_head["entry_sha256"],
            issue_sha,
            successor,
        )
        dependencies = tuple(
            item.natural_key
            for item in items
            if item.family == "issue_route_replay"
            and item.authority.get("target_date") == target.isoformat()
        )
        items.append(
            _item(
                "guard",
                natural_key,
                successor,
                artifacts,
                {
                    **record,
                    "action": successor,
                    "superseding_live_event": (
                        _event_record(superseding_event)
                        if superseding_event is not None
                        else None
                    ),
                },
                dependency_keys=dependencies,
            )
        )


def _inventory_trusted_time(
    state: _VerifiedState,
    builders: dict[str, _FamilyBuilder],
    items: list[InventoryItem],
) -> None:
    _inventory_trusted_records(state, builders, items)


def _trusted_object_records(directory: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for path in _direct_regular_files(directory, name="trusted-time TSR objects"):
        if (
            not path.name.endswith(".tsr")
            or len(path.name) != 68
            or any(char not in "0123456789abcdef" for char in path.name[:64])
        ):
            raise WorksetInventoryIntegrityError(
                "Trusted-time object namespace contains an unknown entry"
            )
        result[path.name[:64]] = path
    return result


@dataclass(frozen=True)
class _TrustedRequestIdentity:
    record: dict[str, Any]
    record_snapshot: Any
    message: bytes
    nonce: int
    expected_der: bytes
    der_path: Path


def _load_trusted_request_identity(
    trusted: Any,
    profile: Mapping[str, Any],
    paths: Any,
    candidate: Any,
) -> _TrustedRequestIdentity:
    """Replay an existing request and reconstruct its DER without writing it."""

    target = candidate.target
    record_path = paths.requests / f"{target.isoformat()}.json"
    der_path = paths.request_der / f"{target.isoformat()}.tsq"
    try:
        record_snapshot = trusted._read_regular(  # noqa: SLF001
            record_path, name="timestamp request record"
        )
        record = trusted._decode_json(  # noqa: SLF001
            record_snapshot.raw, name="timestamp request record"
        )
    except FileNotFoundError as exc:
        raise WorksetInventoryIntegrityError(
            "Trusted-time request changed during read-only replay"
        ) from exc
    if record_snapshot.raw != trusted._canonical_bytes(record):  # noqa: SLF001
        raise WorksetInventoryIntegrityError(
            "Trusted-time request record is not canonical"
        )
    expected_keys = {
        "schema_version",
        "profile_id",
        "profile_sha256",
        "artifact_status",
        "target_date",
        "created_at_utc",
        "evidence_envelope",
        "evidence_envelope_sha256",
        "nonce_hex",
        "policy_oid",
        "message_imprint_algorithm_oid",
        "message_imprint_sha256",
        "cert_req",
        "request_der",
        "outcome_read",
        "trusted_anchor_receipt_verified",
        "e2_live_evidence_eligible",
        "real_activation_ready",
        "formal_warning_output",
    }
    if set(record) != expected_keys:
        raise WorksetInventoryIntegrityError("Trusted-time request keys changed")
    message = trusted._canonical_bytes(candidate.envelope)  # noqa: SLF001
    nonce_text = record.get("nonce_hex")
    if (
        not isinstance(nonce_text, str)
        or len(nonce_text) != 64
        or any(char not in trusted.HEX_DIGITS for char in nonce_text)
    ):
        raise WorksetInventoryIntegrityError("Trusted-time nonce encoding changed")
    nonce = int(nonce_text, 16)
    if nonce.bit_length() != profile["rfc3161"]["nonce_bits"]:
        raise WorksetInventoryIntegrityError("Trusted-time nonce width changed")
    expected_der = trusted.build_timestamp_request(
        message, nonce, profile["rfc3161"]["policy_oid"]
    )
    expected_reference = {
        "path": der_path.relative_to(paths.root).as_posix(),
        "sha256": _sha256(expected_der),
        "size_bytes": len(expected_der),
    }
    if (
        record.get("schema_version") != profile["protocol"]["request_schema_version"]
        or record.get("profile_id") != profile["profile_id"]
        or record.get("profile_sha256") != profile["_profile_sha256"]
        or record.get("artifact_status") != profile["artifact_status"]
        or record.get("target_date") != target.isoformat()
        or record.get("evidence_envelope") != candidate.envelope
        or record.get("evidence_envelope_sha256") != _sha256(message)
        or record.get("policy_oid") != profile["rfc3161"]["policy_oid"]
        or record.get("message_imprint_algorithm_oid") != trusted.SHA256_OID
        or record.get("message_imprint_sha256") != _sha256(message)
        or record.get("cert_req") is not True
        or record.get("request_der") != expected_reference
        or any(
            record.get(key) is not False
            for key in (
                "outcome_read",
                "trusted_anchor_receipt_verified",
                "e2_live_evidence_eligible",
                "real_activation_ready",
                "formal_warning_output",
            )
        )
    ):
        raise WorksetInventoryIntegrityError("Trusted-time request semantics changed")
    trusted._parse_utc(record["created_at_utc"], name="request.created_at_utc")  # noqa: SLF001
    return _TrustedRequestIdentity(
        record=record,
        record_snapshot=record_snapshot,
        message=message,
        nonce=nonce,
        expected_der=expected_der,
        der_path=der_path,
    )


def _load_existing_trusted_request(
    trusted: Any,
    identity: _TrustedRequestIdentity,
) -> Any:
    """Replay an existing request/DER pair without invoking a repair writer."""

    try:
        der_snapshot = trusted._read_regular(  # noqa: SLF001
            identity.der_path, name="timestamp request DER"
        )
    except FileNotFoundError as exc:
        raise WorksetInventoryIntegrityError(
            "Trusted-time DER changed during read-only replay"
        ) from exc
    if der_snapshot.raw != identity.expected_der:
        raise WorksetInventoryIntegrityError("Trusted-time request DER changed")
    return trusted.RequestArtifacts(
        identity.record,
        identity.record_snapshot,
        der_snapshot,
        identity.message,
        identity.nonce,
    )


def _trusted_der_repair_targets(
    *,
    requests: set[date],
    request_der: set[date],
    links: set[date],
    receipts: set[date],
) -> tuple[date, ...]:
    """Validate trusted namespaces and return request-only DER repair targets."""

    if request_der - requests:
        raise WorksetInventoryIntegrityError(
            "Trusted-time DER exists without its request"
        )
    if links - requests:
        raise WorksetInventoryIntegrityError(
            "Trusted-time response link exists without request"
        )
    if receipts - links:
        raise WorksetInventoryIntegrityError(
            "Trusted-time receipt exists without response link"
        )
    missing = requests - request_der
    if missing & (links | receipts):
        raise WorksetInventoryIntegrityError(
            "Trusted-time response chain exists while request DER is missing"
        )
    return tuple(sorted(missing))


def _inventory_trusted_records(
    state: _VerifiedState,
    builders: dict[str, _FamilyBuilder],
    items: list[InventoryItem],
) -> None:
    from monitoring import ootang_trusted_time_shadow_core as trusted

    builder = builders["trusted_time"]
    try:
        profile = trusted.load_trusted_time_profile()
        paths = trusted.trusted_time_paths(profile, runtime_root=state.active_root)
        requests = _strict_dated_records(
            paths.requests, ".json", name="trusted-time requests"
        )
        request_der = _strict_dated_records(
            paths.request_der, ".tsq", name="trusted-time request DER"
        )
        links = _strict_dated_records(
            paths.response_links, ".json", name="trusted-time response links"
        )
        receipts = _strict_dated_records(
            paths.receipts, ".json", name="trusted-time receipts"
        )
        objects = _trusted_object_records(paths.objects)
    except WorksetInventoryError:
        raise
    except Exception as exc:
        raise WorksetInventoryIntegrityError(
            f"Cannot enumerate trusted-time namespaces:{type(exc).__name__}:{exc}"
        ) from exc
    repair_targets = set(
        _trusted_der_repair_targets(
            requests=set(requests),
            request_der=set(request_der),
            links=set(links),
            receipts=set(receipts),
        )
    )
    referenced_objects: set[str] = set()
    for target in sorted(requests):
        try:
            candidate, reason = trusted._candidate(  # noqa: SLF001
                profile, paths, requested_target=target
            )
            if candidate is None:
                raise WorksetInventoryIntegrityError(
                    f"Trusted-time request lost its evidence candidate:{reason}"
                )
            identity = _load_trusted_request_identity(
                trusted, profile, paths, candidate
            )
        except WorksetInventoryError:
            raise
        except Exception as exc:
            raise WorksetInventoryIntegrityError(
                f"Trusted-time request failed replay:{target}:{type(exc).__name__}:{exc}"
            ) from exc
        nonce = _text(identity.record["nonce_hex"], name="TSA nonce", maximum=64)
        imprint = _hash(
            identity.record["message_imprint_sha256"], name="TSA message imprint"
        )
        envelope = identity.record["evidence_envelope"]
        if not isinstance(envelope, dict):
            raise WorksetInventoryIntegrityError(
                "Trusted-time evidence envelope changed type"
            )
        issue_id = _text(
            envelope.get("outstanding_issue_id"), name="TSA outstanding issue id"
        )
        seal_hash = _hash(
            envelope.get("live_issue_seal_entry_sha256"), name="TSA live seal"
        )
        request_artifact = _artifact(
            identity.record_snapshot.path,
            role="trusted_time_request",
            root_label="active",
            root=state.active_root,
            maximum_bytes=state.maximum_bytes,
        )
        if target in repair_targets:
            artifacts = [request_artifact]
            record = {
                "target_date": target.isoformat(),
                "old_live_epoch_id": state.live_projection.epoch_id,
                "outstanding_issue_id": issue_id,
                "live_issue_seal_entry_sha256": seal_hash,
                "tsa_nonce_hex": nonce,
                "message_imprint_sha256": imprint,
                "request_sha256": identity.record_snapshot.sha256,
                "request_der_sha256": _sha256(identity.expected_der),
                "request_der_present": False,
                "response_link_present": False,
                "response_object_sha256": None,
                "receipt_sha256": None,
                "terminal": False,
            }
            natural_key = _key(
                "trusted_time",
                state.live_projection.epoch_id,
                target.isoformat(),
                issue_id,
                seal_hash,
                nonce,
                imprint,
            )
            guard_dependencies = tuple(
                item.natural_key
                for item in items
                if item.family == "guard"
                and item.authority.get("target_date") == target.isoformat()
            )
            builder.records.append(record)
            builder.artifacts.extend(artifacts)
            items.append(
                _item(
                    "trusted_time",
                    natural_key,
                    "trusted_time_request_der_repaired",
                    artifacts,
                    {
                        **record,
                        "action": "trusted_time_request_der_repaired",
                        "repair_bytes_reconstructed_read_only": True,
                    },
                    dependency_keys=guard_dependencies,
                )
            )
            continue
        request = _load_existing_trusted_request(trusted, identity)
        artifacts = [
            request_artifact,
            _artifact(
                request.der_snapshot.path,
                role="trusted_time_request_der",
                root_label="active",
                root=state.active_root,
                maximum_bytes=state.maximum_bytes,
            ),
        ]
        link_payload: Mapping[str, object] | None = None
        link_snapshot = None
        response_snapshot = None
        response_sha: str | None = None
        if target in links:
            try:
                link_payload, link_snapshot, response_snapshot = trusted._load_link(  # noqa: SLF001
                    profile, paths, request
                )
            except Exception as exc:
                raise WorksetInventoryIntegrityError(
                    f"Trusted-time link/TSR failed replay:{target}:{exc}"
                ) from exc
            response_sha = response_snapshot.sha256
            referenced_objects.add(response_sha)
            artifacts.extend(
                (
                    _artifact(
                        link_snapshot.path,
                        role="trusted_time_response_link",
                        root_label="active",
                        root=state.active_root,
                        maximum_bytes=state.maximum_bytes,
                    ),
                    _artifact(
                        response_snapshot.path,
                        role="trusted_time_response_object",
                        root_label="active",
                        root=state.active_root,
                        maximum_bytes=state.maximum_bytes,
                    ),
                )
            )
        terminal = target in receipts
        receipt_sha: str | None = None
        if terminal:
            try:
                if (
                    link_payload is None
                    or link_snapshot is None
                    or response_snapshot is None
                ):
                    raise WorksetInventoryIntegrityError(
                        "Trusted-time receipt lost its response chain"
                    )
                dependency = trusted._check_dependency(profile)  # noqa: SLF001
                trust = trusted.load_trust_material(profile, paths)
                expected_receipt = trusted._expected_receipt(  # noqa: SLF001
                    profile,
                    paths,
                    trust,
                    dependency,
                    request,
                    link_payload,
                    link_snapshot,
                    response_snapshot,
                )
                stored_receipt = trusted._read_regular(  # noqa: SLF001
                    receipts[target], name="timestamp receipt"
                )
                if stored_receipt.raw != trusted._canonical_bytes(  # noqa: SLF001
                    expected_receipt
                ):
                    raise WorksetInventoryIntegrityError(
                        "Trusted-time receipt differs from cryptographic replay"
                    )
            except Exception as exc:
                raise WorksetInventoryIntegrityError(
                    f"Trusted-time receipt failed cryptographic replay:{target}:{exc}"
                ) from exc
            receipt_artifact = _artifact(
                receipts[target],
                role="trusted_time_receipt",
                root_label="active",
                root=state.active_root,
                maximum_bytes=state.maximum_bytes,
            )
            receipt_sha = receipt_artifact.sha256
            artifacts.append(receipt_artifact)
        record = {
            "target_date": target.isoformat(),
            "old_live_epoch_id": state.live_projection.epoch_id,
            "outstanding_issue_id": issue_id,
            "live_issue_seal_entry_sha256": seal_hash,
            "tsa_nonce_hex": nonce,
            "message_imprint_sha256": imprint,
            "request_sha256": request.record_snapshot.sha256,
            "request_der_sha256": request.der_snapshot.sha256,
            "request_der_present": True,
            "response_link_present": link_payload is not None,
            "response_object_sha256": response_sha,
            "receipt_sha256": receipt_sha,
            "terminal": terminal,
        }
        builder.records.append(record)
        builder.artifacts.extend(artifacts)
        if terminal:
            continue
        successor = (
            "trusted_time_response_link_recorded"
            if link_payload is None
            else "trusted_time_receipt_verified"
        )
        natural_key = _key(
            "trusted_time",
            state.live_projection.epoch_id,
            target.isoformat(),
            issue_id,
            seal_hash,
            nonce,
            imprint,
        )
        guard_dependencies = tuple(
            item.natural_key
            for item in items
            if item.family == "guard"
            and item.authority.get("target_date") == target.isoformat()
        )
        items.append(
            _item(
                "trusted_time",
                natural_key,
                successor,
                artifacts,
                {**record, "action": successor},
                dependency_keys=guard_dependencies,
            )
        )
    orphan_objects = sorted(set(objects) - referenced_objects)
    if orphan_objects:
        raise WorksetInventoryIntegrityError(
            f"Trusted-time namespace contains an orphan TSR:{orphan_objects[0]}"
        )
    builder.authority["tsa_object_namespace"] = {
        "object_count": len(objects),
        "referenced_response_sha256s": sorted(referenced_objects),
        "namespace_digest": _sha256(_canonical_bytes(sorted(referenced_objects))),
    }


def _inventory_shadow(
    state: _VerifiedState,
    builders: dict[str, _FamilyBuilder],
    items: list[InventoryItem],
) -> None:
    _inventory_shadow_actions(state, builders, items)


def _shadow_prerequisite_artifact(state: _VerifiedState) -> ArtifactRef:
    path = state.source_module._runtime_path(  # noqa: SLF001
        state.source_profile, "current_source_pointer", root=state.active_root
    )
    return _artifact(
        path,
        role="shadow_action_live_source_pointer",
        root_label="active",
        root=state.active_root,
        maximum_bytes=state.maximum_bytes,
    )


def _inventory_shadow_actions(
    state: _VerifiedState,
    builders: dict[str, _FamilyBuilder],
    items: list[InventoryItem],
) -> None:
    builder = builders["shadow"]
    shadow = state.shadow_module
    projection = state.shadow_projection
    live_projection = state.live_projection
    live_chain = _logical_ledger_authority(
        live_projection,
        schema="ootang_prequential_live_logical_chain_v1",
        epoch_field="epoch_id",
    )
    builder.authority["frozen_live_logical_chain"] = live_chain
    prerequisite = _shadow_prerequisite_artifact(state)
    builder.artifacts.append(prerequisite)
    if projection is None:
        natural_key = _key(
            "shadow",
            live_projection.epoch_id,
            "genesis",
            live_projection.ledger_event_count,
            live_projection.ledger_terminal_sha256,
        )
        record = {
            "record_type": "shadow_genesis_missing",
            "upstream_live_epoch_id": live_projection.epoch_id,
            "frozen_live_cursor_sequence_id": live_projection.ledger_event_count,
            "frozen_live_cursor_sha256": live_projection.ledger_terminal_sha256,
            "terminal": False,
        }
        builder.records.append(record)
        items.append(
            _item(
                "shadow",
                natural_key,
                "shadow_live_event_classified",
                (prerequisite,),
                {**record, "action": "create_shadow_epoch_genesis"},
            )
        )
        return
    try:
        if projection.upstream_live_epoch_id == live_projection.epoch_id:
            shadow._validate_shadow_live_binding(  # noqa: SLF001
                projection, live_projection
            )
    except Exception as exc:
        raise WorksetInventoryIntegrityError(
            f"Shadow/live binding failed replay:{type(exc).__name__}:{exc}"
        ) from exc
    shadow_chain = _logical_ledger_authority(
        projection,
        schema="ootang_prequential_calibration_shadow_logical_chain_v1",
        epoch_field="shadow_epoch_id",
    )
    builder.authority["shadow_logical_chain"] = shadow_chain
    coverage: list[dict[str, object]] = []
    for target, records in sorted(projection.issues_by_target.items()):
        coverage.append(
            {
                "coverage_type": "shadow_issue",
                "target_date": target,
                "live_station_issue_entry_sha256s": sorted(
                    record.live_station_issue_entry_sha256
                    for record in records.values()
                ),
                "source_revision_id": None,
            }
        )
    for target, record in sorted(projection.settled.items()):
        coverage.append(
            {
                "coverage_type": "shadow_settlement",
                "target_date": target,
                "live_source_entry_sha256": record["live_settlement_entry_sha256"],
                "source_revision_id": record["source_revision_id"],
            }
        )
    for target, live_hash in sorted(projection.backfill_ineligible.items()):
        coverage.append(
            {
                "coverage_type": "shadow_backfill",
                "target_date": target,
                "live_source_entry_sha256": live_hash,
                "source_revision_id": None,
            }
        )
    for event in projection.ledger_events:
        if event.event_type != "shadow_outcome_revision_rescored":
            continue
        coverage.append(
            {
                "coverage_type": "shadow_revision",
                "target_date": event.target_date,
                "station": event.station,
                "live_source_entry_sha256": event.payload["live_revision_entry_sha256"],
                "source_revision_id": event.payload["source_revision_id"],
            }
        )
    builder.authority["per_live_source_revision_coverage"] = coverage
    builder.records.extend(
        {
            "record_type": "shadow_ledger_event",
            **_event_record(event),
            "terminal": True,
        }
        for event in projection.ledger_events
    )
    if projection.upstream_live_epoch_id != live_projection.epoch_id:
        if projection.outstanding_target_date is not None:
            raise WorksetInventoryIntegrityError(
                "Upstream live epoch changed with outstanding shadow work"
            )
        natural_key = _key(
            "shadow",
            projection.shadow_epoch_id,
            projection.upstream_live_epoch_id,
            live_projection.epoch_id,
            "epoch-rotation",
            live_projection.ledger_terminal_sha256,
        )
        authority = {
            "record_type": "shadow_epoch_rotation",
            "shadow_epoch_id": projection.shadow_epoch_id,
            "upstream_live_epoch_id": projection.upstream_live_epoch_id,
            "next_upstream_live_epoch_id": live_projection.epoch_id,
            "epoch_open": projection.epoch_open,
            "terminal": False,
        }
        builder.records.append(authority)
        items.append(
            _item(
                "shadow",
                natural_key,
                "shadow_live_event_classified",
                (prerequisite,),
                {**authority, "action": "close_or_open_shadow_epoch"},
            )
        )
        return
    if not projection.epoch_open:
        raise WorksetInventoryIntegrityError(
            "Shadow epoch is closed without an upstream epoch change"
        )
    try:
        settlements = shadow._live_settlements(live_projection)  # noqa: SLF001
        backfills = shadow._live_backfills(live_projection)  # noqa: SLF001
        revision_groups = shadow._live_revision_groups(live_projection)  # noqa: SLF001
        current_issue = shadow._current_live_issue(live_projection)  # noqa: SLF001
    except Exception as exc:
        raise WorksetInventoryIntegrityError(
            f"Cannot derive shadow action coverage:{type(exc).__name__}:{exc}"
        ) from exc
    pending: list[tuple[int, str, Mapping[str, object]]] = []
    classified = (
        set(projection.backfill_ineligible)
        | set(projection.settled)
        | set(projection.issues_by_target)
    )
    for target, source_event in {**backfills, **settlements}.items():
        if target in classified:
            continue
        payload = dict(source_event.payload)
        pending.append(
            (
                int(source_event.sequence_id),
                "shadow_live_event_classified",
                {
                    "record_type": "unclassified_live_source",
                    "target_date": target,
                    "live_source_event": _event_record(source_event),
                    "source_revision_id": payload.get("source_revision_id"),
                    "outcome_batch_sha256": payload.get("outcome_batch_sha256"),
                    "terminal": False,
                },
            )
        )
    for (target, revision), rows in revision_groups.items():
        if target not in projection.settled:
            continue
        if revision in projection.revisions.get(target, {}):
            continue
        ordered_rows = sorted(rows.values(), key=lambda event: event.sequence_id)
        pending.append(
            (
                int(ordered_rows[0].sequence_id),
                "shadow_live_event_classified",
                {
                    "record_type": "unclassified_live_revision",
                    "target_date": target,
                    "source_revision_id": revision,
                    "live_revision_entry_sha256s": [
                        event.entry_sha256 for event in ordered_rows
                    ],
                    "terminal": False,
                },
            )
        )
    if current_issue is not None:
        target, _, seal = current_issue
        if target not in classified:
            pending.append(
                (
                    int(seal.sequence_id),
                    "shadow_live_event_classified",
                    {
                        "record_type": "unclassified_live_issue",
                        "target_date": target,
                        "live_issue_id": seal.issue_id,
                        "live_issue_seal_entry_sha256": seal.entry_sha256,
                        "source_revision_id": None,
                        "terminal": False,
                    },
                )
            )
    if projection.outstanding_target_date is not None:
        target = projection.outstanding_target_date
        source = settlements.get(target)
        sequence = (
            int(source.sequence_id)
            if source is not None
            else live_projection.ledger_event_count
        )
        pending.append(
            (
                sequence,
                "shadow_outstanding_settled",
                {
                    "record_type": "shadow_outstanding",
                    "target_date": target,
                    "shadow_epoch_id": projection.shadow_epoch_id,
                    "shadow_issue_batch_sha256": projection.outstanding_issue_batch_sha256,
                    "live_issue_seal_entry_sha256": projection.outstanding_live_seal_entry_sha256,
                    "source_revision_id": (
                        source.payload.get("source_revision_id")
                        if source is not None
                        else None
                    ),
                    "live_settlement_entry_sha256": (
                        source.entry_sha256 if source is not None else None
                    ),
                    "terminal": False,
                },
            )
        )
    pending.sort(key=lambda value: (value[0], value[1], _canonical_bytes(value[2])))
    prior: str | None = None
    for sequence, successor, authority in pending:
        target = authority.get("target_date") or "none"
        revision = authority.get("source_revision_id") or "none"
        source_hash = (
            authority.get("live_settlement_entry_sha256")
            or authority.get("live_issue_seal_entry_sha256")
            or _sha256(_canonical_bytes(dict(authority)))
        )
        natural_key = _key(
            "shadow",
            projection.shadow_epoch_id,
            projection.upstream_live_epoch_id,
            target,
            revision,
            source_hash,
            sequence,
        )
        dependencies = [prior] if prior is not None else []
        dependencies.extend(
            item.natural_key
            for item in items
            if item.family in {"live_outstanding", "outcome_revision"}
            and item.authority.get("target_date") == target
        )
        items.append(
            _item(
                "shadow",
                natural_key,
                successor,
                (prerequisite,),
                {
                    **dict(authority),
                    "action": successor,
                    "shadow_cursor_sequence_id": projection.live_cursor_sequence_id,
                    "shadow_cursor_sha256": projection.live_cursor_sha256,
                    "frozen_live_upper_sequence_id": live_projection.ledger_event_count,
                    "frozen_live_upper_sha256": live_projection.ledger_terminal_sha256,
                },
                dependency_keys=dependencies,
            )
        )
        builder.records.append(dict(authority))
        prior = natural_key
    if (
        not pending
        and projection.live_cursor_sequence_id < live_projection.ledger_event_count
    ):
        natural_key = _key(
            "shadow",
            projection.shadow_epoch_id,
            "cursor",
            projection.live_cursor_sequence_id,
            projection.live_cursor_sha256,
            live_projection.ledger_event_count,
            live_projection.ledger_terminal_sha256,
        )
        authority = {
            "record_type": "shadow_cursor_lag",
            "shadow_epoch_id": projection.shadow_epoch_id,
            "upstream_live_epoch_id": projection.upstream_live_epoch_id,
            "shadow_cursor_sequence_id": projection.live_cursor_sequence_id,
            "shadow_cursor_sha256": projection.live_cursor_sha256,
            "frozen_live_upper_sequence_id": live_projection.ledger_event_count,
            "frozen_live_upper_sha256": live_projection.ledger_terminal_sha256,
            "terminal": False,
        }
        builder.records.append(authority)
        items.append(
            _item(
                "shadow",
                natural_key,
                "shadow_cursor_at_frozen_live_upper_tip",
                (prerequisite,),
                {**authority, "action": "advance_shadow_cursor"},
            )
        )
