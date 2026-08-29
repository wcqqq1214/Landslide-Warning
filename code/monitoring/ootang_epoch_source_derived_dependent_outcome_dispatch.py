"""Materialize one dependency-ready source-derived historical outcome.

This sibling coordinator keeps the source-only materialization and consumption
authorities immutable.  It deep-replays both, proves every dependency against
the currently published effective identity, and directly invokes the pinned
historical materializer for at most one non-source-only D/R frontier per poll.
Its receipts remain non-terminal until a separate consumption authority records
the exact live-ledger transaction.
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

from monitoring import ootang_epoch_registry as registry  # noqa: E402
from monitoring import ootang_epoch_workset_manifest as manifest  # noqa: E402
from monitoring import ootang_epoch_workset_recovery as recovery  # noqa: E402
from monitoring import (  # noqa: E402
    ootang_epoch_source_derived_outcome_consumption as consumption,
)
from monitoring import (  # noqa: E402
    ootang_epoch_source_derived_outcome_dispatch as dispatch,
)
from monitoring import (  # noqa: E402
    ootang_epoch_source_derived_workset_overlay as overlay,
)


DEFAULT_CONFIG_PATH = (
    ROOT / "config" / "ootang_epoch_source_derived_dependent_outcome_dispatch.v1.json"
)
DEFAULT_CONFIG_SHA256 = (
    "d374df70debab962143b584c0167661d005b9d7ee36495be44db84bbcb6beb3b"
)
IMPLEMENTATION_LOGICAL_PATH = (
    "code/monitoring/ootang_epoch_source_derived_dependent_outcome_dispatch.py"
)
MAX_CONTROL_BYTES = 4 * 1024 * 1024
ZERO_HASH = "0" * 64
LOCK_ORDER = recovery.LOCK_ORDER
RECORD_NAME = re.compile(r"^(?P<step>[0-9a-f]{64})\.json$")
EVENT_NAME = re.compile(r"^(?P<sequence>[0-9]{20})-(?P<entry>[0-9a-f]{64})\.json$")

EXPECTED_UPSTREAM = {
    "materialization_dispatch_profile": {
        "path": "config/ootang_epoch_source_derived_outcome_dispatch.v1.json",
        "expected_sha256": dispatch.DEFAULT_CONFIG_SHA256,
    },
    "materialization_dispatch_implementation": {
        "path": "code/monitoring/ootang_epoch_source_derived_outcome_dispatch.py",
        "expected_sha256": (
            "5d51174c16bdff0dabc88af357bf54ba0e1281aa5b69e06d198b54d4a5941fab"
        ),
    },
    "consumption_profile": {
        "path": "config/ootang_epoch_source_derived_outcome_consumption.v1.json",
        "expected_sha256": consumption.DEFAULT_CONFIG_SHA256,
    },
    "consumption_implementation": {
        "path": "code/monitoring/ootang_epoch_source_derived_outcome_consumption.py",
        "expected_sha256": (
            "fb03dfa502d7402b824cec16b36698d0de6349e419feac8a34981ff9e05b0789"
        ),
    },
}
EXPECTED_RUNTIME = {
    "registry_root": "runtime/ootang_epoch_registry_v1",
    "recovery_namespace": "workset_recovery_v1",
    "namespace": "source_derived_dependent_outcome_dispatch_v1",
    "intents": "intents",
    "receipts": "receipts",
    "events": "events",
    "status": "status.json",
    "manager_lock": "manager.lock",
    "active_root": "runtime/ootang_prequential_live_v1",
    "shadow_root": "runtime/ootang_prequential_calibration_shadow_v1",
}
EXPECTED_PROTOCOL = {
    "intent_schema_version": (
        "ootang_epoch_source_derived_dependent_outcome_dispatch_intent_v1"
    ),
    "receipt_schema_version": (
        "ootang_epoch_source_derived_dependent_outcome_dispatch_receipt_v1"
    ),
    "event_schema_version": (
        "ootang_epoch_source_derived_dependent_outcome_dispatch_event_v1"
    ),
    "status_schema_version": (
        "ootang_epoch_source_derived_dependent_outcome_dispatch_status_v1"
    ),
    "event_type": ("epoch_source_derived_dependent_outcome_materialization_recorded"),
    "action": "outcome_materialized",
    "next_action": "outcome_or_revision_consumed",
    "canonical_json": "utf8_sort_keys_compact_no_nan_trailing_lf",
    "initial_previous_entry_sha256": ZERO_HASH,
    "surviving_lock_order": list(LOCK_ORDER),
    "readiness_policy": (
        "current_effective_non_source_only_d_or_r_with_exact_source_gate_and_"
        "consumption_terminal_dependencies"
    ),
    "poll_policy": ("heal_or_materialize_at_most_one_dependency_ready_effective_key"),
    "maximum_control_bytes": MAX_CONTROL_BYTES,
}
TRUE_CAPABILITIES = (
    "machine_only",
    "published_effective_overlay_deep_verified",
    "source_only_dispatch_deep_verified",
    "source_only_consumption_deep_verified",
    "exact_dependency_terminal_events_required",
    "current_effective_identity_required",
    "dependent_d_or_r_materialization_dispatch_implemented",
    "canonical_materializer_writer_reused",
    "create_only_dispatch_intents_implemented",
    "create_only_dispatch_receipts_implemented",
    "append_only_dispatch_events_implemented",
    "crash_forward_adoption_implemented",
)
FALSE_CLAIMS = (
    "outcome_or_revision_consumed",
    "terminal_for_effective_key",
    "terminal_for_recovery_v6_key",
    "source_parent_terminal",
    "recovery_v6_mutated",
    "effective_workset_overlay_mutated",
    "source_only_dispatch_mutated",
    "source_only_consumption_mutated",
    "cross_freeze_authority_mutated",
    "source_ingest_executed",
    "materialization_receipt_used_as_terminal_dependency",
    "consumption_receipt_without_event_used_as_terminal_dependency",
    "old_effective_identity_used_as_terminal_dependency",
    "invalidation_item_materialized",
    "retained_base_dependency_supported",
    "all_effective_items_terminal",
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
    "network_action_performed",
)


class SourceDerivedDependentOutcomeDispatchError(RuntimeError):
    """Base error for dependency-gated historical materialization."""


class SourceDerivedDependentOutcomeDispatchConfigError(
    SourceDerivedDependentOutcomeDispatchError
):
    """The reviewed profile or a pinned dependency changed."""


class SourceDerivedDependentOutcomeDispatchIntegrityError(
    SourceDerivedDependentOutcomeDispatchError
):
    """An authority, proof, plan, or durable record failed closed."""


class SourceDerivedDependentOutcomeDispatchBusyError(
    SourceDerivedDependentOutcomeDispatchError
):
    """A surviving globally ordered lock is held."""


@dataclass(frozen=True)
class SourceDerivedDependentOutcomeDispatchPaths:
    registry_root: Path
    root: Path
    intents: Path
    receipts: Path
    events: Path
    status: Path
    manager_lock: Path
    active_root: Path
    shadow_root: Path
    dispatch: dispatch.SourceDerivedOutcomeDispatchPaths
    consumption: consumption.SourceDerivedOutcomeConsumptionPaths


@dataclass(frozen=True)
class SourceDerivedDependentOutcomeDispatchResult:
    status: str
    reason: str
    status_path: Path
    intent_path: Path | None = None
    receipt_path: Path | None = None
    event_path: Path | None = None
    step_id: str | None = None
    key_id: str | None = None
    natural_key: str | None = None
    outcome_materialization_performed: bool = False
    terminal_for_effective_key: bool = False
    terminal_for_recovery_v6_key: bool = False


@dataclass(frozen=True)
class _DependentCandidate:
    item: Mapping[str, Any]
    dri_kind: str
    topological_index: int
    dependency_proof: Mapping[str, Any]


FaultHook = Callable[[str], None]
BuildPlan = dispatch.BuildPlan
MaterializeAction = dispatch.MaterializeAction


def _canonical_bytes(value: object) -> bytes:
    try:
        return registry._canonical_bytes(value)  # type: ignore[arg-type]  # noqa: SLF001
    except (TypeError, ValueError) as exc:
        raise SourceDerivedDependentOutcomeDispatchIntegrityError(
            "Value is not canonical JSON"
        ) from exc


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _claims(*, materialized: bool) -> dict[str, bool]:
    return {
        **{name: True for name in TRUE_CAPABILITIES},
        "outcome_materialization_performed": materialized,
        **{name: False for name in FALSE_CLAIMS},
    }


def _exact(value: object, keys: set[str], *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise SourceDerivedDependentOutcomeDispatchIntegrityError(
            f"{name} keys changed"
        )
    return value


def _text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise SourceDerivedDependentOutcomeDispatchIntegrityError(f"{name} changed")
    return value


def _hash(value: object, *, name: str) -> str:
    result = _text(value, name=name)
    if len(result) != 64 or any(char not in "0123456789abcdef" for char in result):
        raise SourceDerivedDependentOutcomeDispatchIntegrityError(
            f"{name} is not a lowercase SHA-256"
        )
    return result


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SourceDerivedDependentOutcomeDispatchIntegrityError(
            "Dependent dispatcher clock must be timezone-aware"
        )
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: object, *, name: str) -> datetime:
    try:
        return registry._utc_timestamp(value, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise SourceDerivedDependentOutcomeDispatchIntegrityError(str(exc)) from exc


def _child(root: Path, relative: str, *, name: str) -> Path:
    try:
        return registry._contained(root, relative, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise SourceDerivedDependentOutcomeDispatchConfigError(str(exc)) from exc


def _strict_json(
    path: Path, *, name: str
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    try:
        return manifest._read_json(  # noqa: SLF001
            path, name=name, maximum_bytes=MAX_CONTROL_BYTES
        )
    except manifest.WorksetManifestError as exc:
        raise SourceDerivedDependentOutcomeDispatchIntegrityError(str(exc)) from exc


def _strict_entries(directory: Path, *, name: str) -> tuple[Path, ...]:
    try:
        return manifest._strict_entries(directory, name=name)  # noqa: SLF001
    except manifest.WorksetManifestError as exc:
        raise SourceDerivedDependentOutcomeDispatchIntegrityError(str(exc)) from exc


def _publish(
    path: Path, raw: bytes, *, root: Path, name: str
) -> registry.ArtifactSnapshot:
    if len(raw) > MAX_CONTROL_BYTES:
        raise SourceDerivedDependentOutcomeDispatchIntegrityError(
            f"{name} capacity exceeded"
        )
    try:
        return overlay.drain._publish_once_durable(  # type: ignore[attr-defined]  # noqa: SLF001
            path, raw, root=root, name=name
        )
    except Exception as exc:
        raise SourceDerivedDependentOutcomeDispatchIntegrityError(
            f"{name} publication failed:{type(exc).__name__}:{exc}"
        ) from exc


def _reference(snapshot: registry.ArtifactSnapshot, root: Path) -> dict[str, object]:
    try:
        relative = snapshot.path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise SourceDerivedDependentOutcomeDispatchIntegrityError(
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
            name="dependent outcome dispatcher implementation",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
    except registry.EpochRegistryError as exc:
        raise SourceDerivedDependentOutcomeDispatchIntegrityError(str(exc)) from exc
    return {
        "path": IMPLEMENTATION_LOGICAL_PATH,
        "sha256": snapshot.sha256,
        "size_bytes": snapshot.size_bytes,
    }


def load_source_derived_dependent_outcome_dispatch_profile(
    path: Path = DEFAULT_CONFIG_PATH, *, project_root: Path = ROOT
) -> dict[str, Any]:
    root = project_root.resolve()
    resolved = path if path.is_absolute() else root / path
    if root != ROOT.resolve() or resolved.resolve() != DEFAULT_CONFIG_PATH.resolve():
        raise SourceDerivedDependentOutcomeDispatchConfigError(
            "Only the reviewed default dependent dispatcher profile is accepted"
        )
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            resolved,
            name="dependent outcome dispatcher profile",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
        profile = registry._decode_json(  # noqa: SLF001
            snapshot.raw, name="dependent outcome dispatcher profile"
        )
    except registry.EpochRegistryError as exc:
        raise SourceDerivedDependentOutcomeDispatchConfigError(str(exc)) from exc
    if snapshot.sha256 != DEFAULT_CONFIG_SHA256:
        raise SourceDerivedDependentOutcomeDispatchConfigError(
            "Dependent outcome dispatcher profile SHA-256 changed"
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
        name="dependent outcome dispatcher profile",
    )
    if (
        profile["schema_version"]
        != "ootang_epoch_source_derived_dependent_outcome_dispatch_profile_v1"
        or profile["profile_id"]
        != "ootang-epoch-source-derived-dependent-outcome-dispatch-v1"
        or profile["profile_version"] != "1.0.0-dependent-materialization-only"
        or profile["case"] != "ootang"
        or profile["artifact_status"]
        != "narrow_dependency_gated_historical_materialization_dispatch_only"
        or profile["formal_warning_output"] is not False
        or profile["default_pipeline_member"] is not False
        or profile["upstream"] != EXPECTED_UPSTREAM
        or profile["runtime"] != EXPECTED_RUNTIME
        or profile["protocol"] != EXPECTED_PROTOCOL
        or profile["engineering_capabilities"] != _claims(materialized=False)
    ):
        raise SourceDerivedDependentOutcomeDispatchConfigError(
            "Dependent outcome dispatcher profile semantics changed"
        )
    for binding in EXPECTED_UPSTREAM.values():
        upstream = _child(root, binding["path"], name="dependent dispatcher upstream")
        try:
            actual = registry._read_regular(  # noqa: SLF001
                upstream,
                name="dependent dispatcher upstream",
                maximum_bytes=64 * 1024 * 1024,
            )
        except registry.EpochRegistryError as exc:
            raise SourceDerivedDependentOutcomeDispatchConfigError(str(exc)) from exc
        if actual.sha256 != binding["expected_sha256"]:
            raise SourceDerivedDependentOutcomeDispatchConfigError(
                f"Pinned dependent dispatcher upstream changed:{binding['path']}"
            )
    dispatch.load_source_derived_outcome_dispatch_profile()
    consumption.load_source_derived_outcome_consumption_profile()
    profile["_profile_sha256"] = snapshot.sha256
    return profile


def source_derived_dependent_outcome_dispatch_paths(
    profile: Mapping[str, Any],
    *,
    registry_root: Path | None = None,
    active_root: Path | None = None,
    shadow_root: Path | None = None,
) -> SourceDerivedDependentOutcomeDispatchPaths:
    registry_path = (
        registry_root or ROOT / profile["runtime"]["registry_root"]
    ).resolve()
    recovery_root = _child(
        registry_path, profile["runtime"]["recovery_namespace"], name="recovery root"
    )
    root = _child(
        recovery_root, profile["runtime"]["namespace"], name="dependent dispatch root"
    )
    active = (active_root or ROOT / profile["runtime"]["active_root"]).resolve()
    shadow = (shadow_root or ROOT / profile["runtime"]["shadow_root"]).resolve()
    dispatch_profile = dispatch.load_source_derived_outcome_dispatch_profile()
    dispatch_paths = dispatch.source_derived_outcome_dispatch_paths(
        dispatch_profile,
        registry_root=registry_path,
        active_root=active,
        shadow_root=shadow,
    )
    consumption_profile = consumption.load_source_derived_outcome_consumption_profile()
    consumption_paths = consumption.source_derived_outcome_consumption_paths(
        consumption_profile,
        registry_root=registry_path,
        active_root=active,
        shadow_root=shadow,
    )
    return SourceDerivedDependentOutcomeDispatchPaths(
        registry_root=registry_path,
        root=root,
        intents=_child(root, profile["runtime"]["intents"], name="dependent intents"),
        receipts=_child(
            root, profile["runtime"]["receipts"], name="dependent receipts"
        ),
        events=_child(root, profile["runtime"]["events"], name="dependent events"),
        status=_child(root, profile["runtime"]["status"], name="dependent status"),
        manager_lock=_child(
            registry_path, profile["runtime"]["manager_lock"], name="manager lock"
        ),
        active_root=active,
        shadow_root=shadow,
        dispatch=dispatch_paths,
        consumption=consumption_paths,
    )


def _acquire_locks(
    paths: SourceDerivedDependentOutcomeDispatchPaths,
) -> list[BinaryIO]:
    try:
        return dispatch._acquire_locks(paths.dispatch)  # noqa: SLF001
    except dispatch.SourceDerivedOutcomeDispatchBusyError as exc:
        raise SourceDerivedDependentOutcomeDispatchBusyError(str(exc)) from exc
    except dispatch.SourceDerivedOutcomeDispatchError as exc:
        raise SourceDerivedDependentOutcomeDispatchIntegrityError(str(exc)) from exc


def _release_locks(handles: Sequence[BinaryIO]) -> None:
    try:
        dispatch._release_locks(handles)  # noqa: SLF001
    except dispatch.SourceDerivedOutcomeDispatchError as exc:
        raise SourceDerivedDependentOutcomeDispatchIntegrityError(str(exc)) from exc


def _effective_dri_rows(
    computation: overlay.SourceDerivedWorksetOverlayComputation,
) -> tuple[tuple[Mapping[str, Any], str, int], ...]:
    # The source-only candidate builder performs the complete D/R/I partition
    # validation.  Reconstructing the full D/R set here therefore cannot make I
    # eligible or admit a row absent from the effective overlay.
    try:
        dispatch._candidate_set(computation)  # noqa: SLF001
    except dispatch.SourceDerivedOutcomeDispatchError as exc:
        raise SourceDerivedDependentOutcomeDispatchIntegrityError(str(exc)) from exc
    payload = computation.derived_evidence.reservation_payload
    additions = payload.get("derived_outcome_items")
    rebounds = payload.get("rebound_existing_items")
    if not isinstance(additions, list) or not isinstance(rebounds, list):
        raise SourceDerivedDependentOutcomeDispatchIntegrityError(
            "Published overlay D/R collections changed type"
        )
    effective_by_key = {
        str(item["key_id"]): (item, index)
        for index, item in enumerate(computation.effective_items)
    }
    if len(effective_by_key) != len(computation.effective_items):
        raise SourceDerivedDependentOutcomeDispatchIntegrityError(
            "Effective key identities are not unique"
        )
    result: list[tuple[Mapping[str, Any], str, int]] = []
    seen: set[str] = set()
    for kind, values in (("D", additions), ("R", rebounds)):
        for value in values:
            if not isinstance(value, Mapping):
                raise SourceDerivedDependentOutcomeDispatchIntegrityError(
                    "Published D/R row changed type"
                )
            try:
                normalized = overlay._normalize_item(  # noqa: SLF001
                    value,
                    computation.reservation.manifest_snapshot.sha256,
                    name=f"published {kind} row",
                )
            except overlay.SourceDerivedWorksetOverlayError as exc:
                raise SourceDerivedDependentOutcomeDispatchIntegrityError(
                    str(exc)
                ) from exc
            key_id = str(normalized["key_id"])
            current = effective_by_key.get(key_id)
            if current is None or dict(current[0]) != normalized or key_id in seen:
                raise SourceDerivedDependentOutcomeDispatchIntegrityError(
                    "Published D/R row lost its unique current effective identity"
                )
            seen.add(key_id)
            result.append((current[0], kind, current[1]))
    result.sort(key=lambda value: value[2])
    return tuple(result)


def _terminal_consumption_dependencies(
    computation: overlay.SourceDerivedWorksetOverlayComputation,
    receipts: Mapping[str, tuple[dict[str, Any], registry.ArtifactSnapshot]],
    events: Sequence[tuple[dict[str, Any], registry.ArtifactSnapshot]],
    paths: SourceDerivedDependentOutcomeDispatchPaths,
) -> dict[str, dict[str, object]]:
    current_by_natural = {
        str(item["natural_key"]): item for item in computation.effective_items
    }
    if len(current_by_natural) != len(computation.effective_items):
        raise SourceDerivedDependentOutcomeDispatchIntegrityError(
            "Effective natural keys are not unique"
        )
    result: dict[str, dict[str, object]] = {}
    for event, event_snapshot in events:
        if (
            event.get("action") != "outcome_or_revision_consumed"
            or event.get("terminal_for_effective_key") is not True
            or event.get("terminal_for_recovery_v6_key") is not False
            or event.get("source_parent_terminal") is not False
        ):
            raise SourceDerivedDependentOutcomeDispatchIntegrityError(
                "Consumption event terminal scope changed"
            )
        step = _hash(event.get("step_id"), name="consumption terminal step")
        receipt_record = receipts.get(step)
        if receipt_record is None:
            raise SourceDerivedDependentOutcomeDispatchIntegrityError(
                "Consumption terminal event lost its receipt"
            )
        receipt, receipt_snapshot = receipt_record
        natural_key = _text(
            event.get("natural_key"), name="consumption terminal natural key"
        )
        current = current_by_natural.get(natural_key)
        if current is None:
            raise SourceDerivedDependentOutcomeDispatchIntegrityError(
                "Consumption terminal event escaped the current effective workset"
            )
        identity = (
            event.get("key_id"),
            event.get("natural_key"),
            event.get("namespace_digest"),
        )
        current_identity = (
            current.get("key_id"),
            current.get("natural_key"),
            current.get("namespace_digest"),
        )
        receipt_identity = (
            receipt.get("key_id"),
            receipt.get("natural_key"),
            receipt.get("namespace_digest"),
        )
        if identity != current_identity or receipt_identity != current_identity:
            raise SourceDerivedDependentOutcomeDispatchIntegrityError(
                "Consumption terminal event uses an old effective identity"
            )
        if natural_key in result:
            raise SourceDerivedDependentOutcomeDispatchIntegrityError(
                "Current effective dependency has multiple terminal events"
            )
        result[natural_key] = {
            "step_id": step,
            "key_id": current["key_id"],
            "natural_key": current["natural_key"],
            "namespace_digest": current["namespace_digest"],
            "receipt": _reference(receipt_snapshot, paths.consumption.root),
            "event": _reference(event_snapshot, paths.consumption.root),
        }
    return result


def _dependent_candidates(
    computation: overlay.SourceDerivedWorksetOverlayComputation,
    terminal_dependencies: Mapping[str, Mapping[str, Any]],
    paths: SourceDerivedDependentOutcomeDispatchPaths,
) -> tuple[_DependentCandidate, ...]:
    rows = _effective_dri_rows(computation)
    dri_ids = {str(item["key_id"]) for item, _, _ in rows}
    current_by_natural = {
        str(item["natural_key"]): item for item in computation.effective_items
    }
    source = computation.cross_authority.source_item
    source_key = _text(source.get("natural_key"), name="source dependency key")
    source_gate = {
        "source_key_id": source["key_id"],
        "source_natural_key": source_key,
        "source_namespace_digest": source["namespace_digest"],
        "cross_freeze_receipt": _reference(
            computation.cross_receipt_snapshot, paths.dispatch.overlay.cross.root
        ),
        "cross_freeze_event": _reference(
            computation.cross_event_snapshot, paths.dispatch.overlay.cross.root
        ),
        "terminal_for_source_parent": False,
    }
    result: list[_DependentCandidate] = []
    for item, kind, index in rows:
        dependencies = item.get("dependency_keys")
        if not isinstance(dependencies, list) or not all(
            isinstance(value, str) and value for value in dependencies
        ):
            raise SourceDerivedDependentOutcomeDispatchIntegrityError(
                "Effective dependency keys changed type"
            )
        # Exact source-only rows remain solely owned by the v1 dispatcher.  A
        # dependent row must still be source-derived: rows with no exact source
        # edge, or with no additional D/R edge, stay unsupported and cannot
        # acquire bytes in this namespace.
        if source_key not in dependencies or not any(
            dependency != source_key for dependency in dependencies
        ):
            continue
        consumed: list[dict[str, object]] = []
        ready = True
        for dependency in dependencies:
            if dependency == source_key:
                continue
            current = current_by_natural.get(dependency)
            terminal = terminal_dependencies.get(dependency)
            if (
                current is None
                or str(current["key_id"]) not in dri_ids
                or terminal is None
                or (
                    terminal.get("key_id"),
                    terminal.get("natural_key"),
                    terminal.get("namespace_digest"),
                )
                != (
                    current.get("key_id"),
                    current.get("natural_key"),
                    current.get("namespace_digest"),
                )
            ):
                # Retained base dependencies and non-event evidence are not yet
                # supported; they wait without being turned into authority.
                ready = False
                break
            consumed.append(dict(terminal))
        if not ready:
            continue
        proof = {
            "dependency_keys": list(dependencies),
            "source_expansion_gate": {
                **source_gate,
                "satisfies_dependency": True,
            },
            "terminal_consumption_dependencies": consumed,
        }
        result.append(
            _DependentCandidate(
                item=item,
                dri_kind=kind,
                topological_index=index,
                dependency_proof=proof,
            )
        )
    return tuple(result)


def _step_id(
    overlay_event_sha256: str,
    dependency_proof_sha256: str,
    key_id: str,
) -> str:
    return _sha256(
        _canonical_bytes(
            {
                "overlay_event_sha256": overlay_event_sha256,
                "dependency_proof_sha256": dependency_proof_sha256,
                "key_id": key_id,
                "step_index": 2,
                "action": "outcome_materialized",
            }
        )
    )


def _intent_payload(
    profile: Mapping[str, Any],
    paths: SourceDerivedDependentOutcomeDispatchPaths,
    computation: overlay.SourceDerivedWorksetOverlayComputation,
    overlay_event: registry.ArtifactSnapshot,
    candidate: _DependentCandidate,
    contract: Mapping[str, Any],
    *,
    recorded_at: str,
) -> dict[str, object]:
    item = candidate.item
    proof = dict(candidate.dependency_proof)
    proof_sha256 = _sha256(_canonical_bytes(proof))
    item_payload = dict(item)
    transition = recovery._transition_plan(item)  # noqa: SLF001
    step_id = _step_id(overlay_event.sha256, proof_sha256, str(item["key_id"]))
    return {
        "schema_version": profile["protocol"]["intent_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "recorded_at_utc": recorded_at,
        "step_id": step_id,
        "step_index": 2,
        "action": "outcome_materialized",
        "next_action": "outcome_or_revision_consumed",
        "dri_kind": candidate.dri_kind,
        "topological_index": candidate.topological_index,
        "key_id": item["key_id"],
        "natural_key": item["natural_key"],
        "namespace_digest": item["namespace_digest"],
        "overlay_slot_id": computation.slot_id,
        "overlay_event": _reference(overlay_event, paths.dispatch.overlay.root),
        "dependency_proof": proof,
        "dependency_proof_sha256": proof_sha256,
        "effective_item": item_payload,
        "effective_item_sha256": _sha256(_canonical_bytes(item_payload)),
        "transition_plan_sha256": transition["plan_sha256"],
        "materialization_contract": dict(contract),
        "materialization_contract_sha256": _sha256(_canonical_bytes(contract)),
        "implementation": _implementation_reference(),
        **_claims(materialized=False),
    }


def _receipt_payload(
    profile: Mapping[str, Any],
    paths: SourceDerivedDependentOutcomeDispatchPaths,
    intent: Mapping[str, Any],
    intent_snapshot: registry.ArtifactSnapshot,
    output: recovery.ActionOutput,
    *,
    recorded_at: str,
) -> dict[str, object]:
    if output.kind != "outcome_materializer_publication":
        raise SourceDerivedDependentOutcomeDispatchIntegrityError(
            "Canonical materializer returned an unexpected output kind"
        )
    semantics = dict(output.semantics)
    if (
        semantics.get("immutable_receipt_verified") is not True
        or semantics.get("fully_published_verified") is not True
        or semantics.get("canonical_materializer_writer_reused") is not True
        or semantics.get("current_pointer_required_for_historical_replay") is not False
        or semantics.get("live_ledger_mutation_performed") is not False
        or semantics.get("network_action_performed") is not False
    ):
        raise SourceDerivedDependentOutcomeDispatchIntegrityError(
            "Canonical materializer output semantics changed"
        )
    return {
        "schema_version": profile["protocol"]["receipt_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "recorded_at_utc": recorded_at,
        "step_id": intent["step_id"],
        "step_index": 2,
        "action": "outcome_materialized",
        "next_action": "outcome_or_revision_consumed",
        "key_id": intent["key_id"],
        "natural_key": intent["natural_key"],
        "namespace_digest": intent["namespace_digest"],
        "intent": _reference(intent_snapshot, paths.root),
        "dependency_proof_sha256": intent["dependency_proof_sha256"],
        "materialization_contract_sha256": intent["materialization_contract_sha256"],
        "output": {
            "kind": output.kind,
            "reference": dict(output.reference)
            if output.reference is not None
            else None,
            "semantics": semantics,
        },
        "terminal_for_effective_key": False,
        "terminal_for_recovery_v6_key": False,
        "source_parent_terminal": False,
        **_claims(materialized=True),
    }


def _event_payload(
    profile: Mapping[str, Any],
    paths: SourceDerivedDependentOutcomeDispatchPaths,
    receipt: Mapping[str, Any],
    receipt_snapshot: registry.ArtifactSnapshot,
    *,
    sequence_id: int,
    previous_entry_sha256: str,
    recorded_at: str,
) -> dict[str, object]:
    body = {
        "schema_version": profile["protocol"]["event_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "sequence_id": sequence_id,
        "previous_entry_sha256": previous_entry_sha256,
        "event_type": profile["protocol"]["event_type"],
        "recorded_at_utc": recorded_at,
        "step_id": receipt["step_id"],
        "step_index": 2,
        "action": "outcome_materialized",
        "next_action": "outcome_or_revision_consumed",
        "key_id": receipt["key_id"],
        "natural_key": receipt["natural_key"],
        "namespace_digest": receipt["namespace_digest"],
        "receipt": _reference(receipt_snapshot, paths.root),
        "dependency_proof_sha256": receipt["dependency_proof_sha256"],
        "materialization_contract_sha256": receipt["materialization_contract_sha256"],
        "terminal_for_effective_key": False,
        "terminal_for_recovery_v6_key": False,
        "source_parent_terminal": False,
        **_claims(materialized=True),
    }
    return {**body, "entry_sha256": _sha256(_canonical_bytes(body))}


def _same_except_time(
    actual: Mapping[str, Any], expected: Mapping[str, Any], *, name: str
) -> None:
    if set(actual) != set(expected):
        raise SourceDerivedDependentOutcomeDispatchIntegrityError(
            f"{name} keys changed"
        )
    _parse_utc(actual.get("recorded_at_utc"), name=f"{name} time")
    comparable = {
        key: value for key, value in actual.items() if key != "recorded_at_utc"
    }
    expected_comparable = {
        key: value for key, value in expected.items() if key != "recorded_at_utc"
    }
    if comparable != expected_comparable:
        raise SourceDerivedDependentOutcomeDispatchIntegrityError(
            f"{name} semantics changed"
        )


def _named_records(
    directory: Path, *, name: str
) -> dict[str, tuple[dict[str, Any], registry.ArtifactSnapshot]]:
    result: dict[str, tuple[dict[str, Any], registry.ArtifactSnapshot]] = {}
    for path in _strict_entries(directory, name=name):
        matched = RECORD_NAME.fullmatch(path.name)
        if matched is None:
            raise SourceDerivedDependentOutcomeDispatchIntegrityError(
                f"{name} contains an unexpected entry"
            )
        step = matched.group("step")
        if step in result:
            raise SourceDerivedDependentOutcomeDispatchIntegrityError(
                f"{name} contains a duplicate step"
            )
        result[step] = _strict_json(path, name=f"{name} record")
    return result


def _event_records(
    profile: Mapping[str, Any],
    paths: SourceDerivedDependentOutcomeDispatchPaths,
    receipts: Mapping[str, tuple[dict[str, Any], registry.ArtifactSnapshot]],
) -> tuple[tuple[dict[str, Any], registry.ArtifactSnapshot], ...]:
    result: list[tuple[dict[str, Any], registry.ArtifactSnapshot]] = []
    previous = ZERO_HASH
    seen: set[str] = set()
    for index, path in enumerate(
        _strict_entries(paths.events, name="dependent dispatch events"), start=1
    ):
        matched = EVENT_NAME.fullmatch(path.name)
        if matched is None or int(matched.group("sequence")) != index:
            raise SourceDerivedDependentOutcomeDispatchIntegrityError(
                "Dependent dispatch event sequence branched"
            )
        payload, snapshot = _strict_json(path, name="dependent dispatch event")
        step = _text(payload.get("step_id"), name="dependent event step")
        if step in seen or step not in receipts:
            raise SourceDerivedDependentOutcomeDispatchIntegrityError(
                "Dependent dispatch event receipt binding branched"
            )
        receipt, receipt_snapshot = receipts[step]
        recorded_at = payload.get("recorded_at_utc")
        _parse_utc(recorded_at, name="dependent dispatch event time")
        expected = _event_payload(
            profile,
            paths,
            receipt,
            receipt_snapshot,
            sequence_id=index,
            previous_entry_sha256=previous,
            recorded_at=str(recorded_at),
        )
        if payload != expected:
            raise SourceDerivedDependentOutcomeDispatchIntegrityError(
                "Dependent dispatch event semantics changed"
            )
        entry = _hash(payload.get("entry_sha256"), name="dependent event entry")
        if matched.group("entry") != entry:
            raise SourceDerivedDependentOutcomeDispatchIntegrityError(
                "Dependent dispatch event filename changed"
            )
        previous = entry
        seen.add(step)
        result.append((payload, snapshot))
    return tuple(result)


def _build_plan(
    item: Mapping[str, Any],
    reservation: recovery.Reservation,
    derived_evidence: object,
    now: datetime,
) -> dispatch.HistoricalMaterializationPlan:
    try:
        return dispatch._historical_materialization_plan(  # noqa: SLF001
            item, reservation, derived_evidence, now
        )
    except dispatch.SourceDerivedOutcomeDispatchBusyError as exc:
        raise SourceDerivedDependentOutcomeDispatchBusyError(str(exc)) from exc
    except dispatch.SourceDerivedOutcomeDispatchError as exc:
        raise SourceDerivedDependentOutcomeDispatchIntegrityError(str(exc)) from exc


def _materialize_action(
    item: Mapping[str, Any],
    reservation: recovery.Reservation,
    contract: Mapping[str, Any],
    now: datetime,
) -> recovery.ActionOutput:
    try:
        return dispatch._default_materialize_action(  # noqa: SLF001
            item, reservation, contract, now
        )
    except dispatch.SourceDerivedOutcomeDispatchBusyError as exc:
        raise SourceDerivedDependentOutcomeDispatchBusyError(str(exc)) from exc
    except dispatch.SourceDerivedOutcomeDispatchError as exc:
        raise SourceDerivedDependentOutcomeDispatchIntegrityError(str(exc)) from exc


def _recorded_materialization_output(
    item: Mapping[str, Any],
    reservation: recovery.Reservation,
    contract: Mapping[str, Any],
    now: datetime,
) -> recovery.ActionOutput:
    try:
        return dispatch._recorded_materialization_output(  # noqa: SLF001
            item, reservation, contract, now
        )
    except dispatch.SourceDerivedOutcomeDispatchError as exc:
        raise SourceDerivedDependentOutcomeDispatchIntegrityError(str(exc)) from exc


def _load_durable_state(
    profile: Mapping[str, Any],
    paths: SourceDerivedDependentOutcomeDispatchPaths,
    computation: overlay.SourceDerivedWorksetOverlayComputation,
    overlay_event: registry.ArtifactSnapshot,
    candidates: Sequence[_DependentCandidate],
    *,
    now: datetime,
    build_plan: BuildPlan,
    verify_action: MaterializeAction,
) -> tuple[
    dict[str, tuple[dict[str, Any], registry.ArtifactSnapshot]],
    dict[str, tuple[dict[str, Any], registry.ArtifactSnapshot]],
    tuple[tuple[dict[str, Any], registry.ArtifactSnapshot], ...],
    dict[str, dispatch.HistoricalMaterializationPlan],
]:
    intents = _named_records(paths.intents, name="dependent dispatch intents")
    receipts = _named_records(paths.receipts, name="dependent dispatch receipts")
    if not set(receipts) <= set(intents):
        raise SourceDerivedDependentOutcomeDispatchIntegrityError(
            "Dependent dispatch receipt is orphaned"
        )
    by_step: dict[str, _DependentCandidate] = {}
    for candidate in candidates:
        proof_sha256 = _sha256(_canonical_bytes(candidate.dependency_proof))
        step = _step_id(
            overlay_event.sha256, proof_sha256, str(candidate.item["key_id"])
        )
        if step in by_step:
            raise SourceDerivedDependentOutcomeDispatchIntegrityError(
                "Dependency-ready step identity collided"
            )
        by_step[step] = candidate
    if not set(intents) <= set(by_step):
        raise SourceDerivedDependentOutcomeDispatchIntegrityError(
            "Dependent dispatcher state is outside the current ready set"
        )
    plans: dict[str, dispatch.HistoricalMaterializationPlan] = {}
    for step, (intent, intent_snapshot) in intents.items():
        candidate = by_step[step]
        plan = build_plan(
            candidate.item,
            computation.reservation,
            computation.derived_evidence,
            now,
        )
        plans[step] = plan
        expected_intent = _intent_payload(
            profile,
            paths,
            computation,
            overlay_event,
            candidate,
            plan.contract,
            recorded_at="",
        )
        _same_except_time(intent, expected_intent, name="dependent dispatch intent")
        if intent_snapshot.path.name != f"{step}.json":
            raise SourceDerivedDependentOutcomeDispatchIntegrityError(
                "Dependent dispatch intent filename changed"
            )
        if step in receipts:
            receipt, receipt_snapshot = receipts[step]
            output = verify_action(
                candidate.item,
                computation.reservation,
                plan.contract,
                now,
            )
            expected_receipt = _receipt_payload(
                profile,
                paths,
                intent,
                intent_snapshot,
                output,
                recorded_at="",
            )
            _same_except_time(
                receipt, expected_receipt, name="dependent dispatch receipt"
            )
            if receipt_snapshot.path.name != f"{step}.json":
                raise SourceDerivedDependentOutcomeDispatchIntegrityError(
                    "Dependent dispatch receipt filename changed"
                )
    incomplete = set(intents) - set(receipts)
    if len(incomplete) > 1:
        raise SourceDerivedDependentOutcomeDispatchIntegrityError(
            "Dependent dispatcher contains branched incomplete intents"
        )
    events = _event_records(profile, paths, receipts)
    event_steps = {str(payload["step_id"]) for payload, _ in events}
    pending = set(receipts) - event_steps
    if len(pending) > 1:
        raise SourceDerivedDependentOutcomeDispatchIntegrityError(
            "Dependent dispatcher contains branched receipt-only commits"
        )
    if incomplete and pending:
        raise SourceDerivedDependentOutcomeDispatchIntegrityError(
            "Dependent dispatcher contains competing crash frontiers"
        )
    return intents, receipts, events, plans


def _publish_intent(
    paths: SourceDerivedDependentOutcomeDispatchPaths, payload: Mapping[str, Any]
) -> registry.ArtifactSnapshot:
    step = _hash(payload.get("step_id"), name="dependent intent step")
    return _publish(
        paths.intents / f"{step}.json",
        _canonical_bytes(payload),
        root=paths.root,
        name="dependent dispatch intent",
    )


def _publish_receipt(
    paths: SourceDerivedDependentOutcomeDispatchPaths, payload: Mapping[str, Any]
) -> registry.ArtifactSnapshot:
    step = _hash(payload.get("step_id"), name="dependent receipt step")
    return _publish(
        paths.receipts / f"{step}.json",
        _canonical_bytes(payload),
        root=paths.root,
        name="dependent dispatch receipt",
    )


def _append_event(
    profile: Mapping[str, Any],
    paths: SourceDerivedDependentOutcomeDispatchPaths,
    receipt: Mapping[str, Any],
    receipt_snapshot: registry.ArtifactSnapshot,
    events: Sequence[tuple[Mapping[str, Any], registry.ArtifactSnapshot]],
    *,
    now: datetime,
) -> registry.ArtifactSnapshot:
    sequence = len(events) + 1
    previous = str(events[-1][0]["entry_sha256"]) if events else ZERO_HASH
    payload = _event_payload(
        profile,
        paths,
        receipt,
        receipt_snapshot,
        sequence_id=sequence,
        previous_entry_sha256=previous,
        recorded_at=_utc_text(now),
    )
    return _publish(
        paths.events / f"{sequence:020d}-{payload['entry_sha256']}.json",
        _canonical_bytes(payload),
        root=paths.root,
        name="dependent dispatch event",
    )


def _write_status(
    profile: Mapping[str, Any],
    paths: SourceDerivedDependentOutcomeDispatchPaths,
    *,
    now: datetime,
    status: str,
    reason: str,
    step_id: str | None,
    item: Mapping[str, Any] | None,
    intent_path: Path | None,
    receipt_path: Path | None,
    event_path: Path | None,
) -> None:
    materialized = event_path is not None
    payload = {
        "schema_version": profile["protocol"]["status_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "recorded_at_utc": _utc_text(now),
        "status": status,
        "reason": reason,
        "step_id": step_id,
        "key_id": item.get("key_id") if item else None,
        "natural_key": item.get("natural_key") if item else None,
        "intent_path": str(intent_path) if intent_path else None,
        "receipt_path": str(receipt_path) if receipt_path else None,
        "event_path": str(event_path) if event_path else None,
        "next_action": "outcome_or_revision_consumed" if materialized else None,
        "cache_authority": False,
        **_claims(materialized=materialized),
    }
    try:
        registry._atomic_cache(  # noqa: SLF001
            paths.status,
            _canonical_bytes(payload),
            root=paths.root,
            name="dependent dispatch status",
        )
    except registry.EpochRegistryError as exc:
        raise SourceDerivedDependentOutcomeDispatchIntegrityError(str(exc)) from exc


def _result(
    paths: SourceDerivedDependentOutcomeDispatchPaths,
    status: str,
    reason: str,
    *,
    step_id: str | None = None,
    item: Mapping[str, Any] | None = None,
    intent_path: Path | None = None,
    receipt_path: Path | None = None,
    event_path: Path | None = None,
) -> SourceDerivedDependentOutcomeDispatchResult:
    return SourceDerivedDependentOutcomeDispatchResult(
        status=status,
        reason=reason,
        status_path=paths.status,
        intent_path=intent_path,
        receipt_path=receipt_path,
        event_path=event_path,
        step_id=step_id,
        key_id=str(item["key_id"]) if item else None,
        natural_key=str(item["natural_key"]) if item else None,
        outcome_materialization_performed=event_path is not None,
    )


def _deep_replay_prerequisites(
    computation: overlay.SourceDerivedWorksetOverlayComputation,
    overlay_event: registry.ArtifactSnapshot,
    paths: SourceDerivedDependentOutcomeDispatchPaths,
    *,
    now: datetime,
) -> tuple[
    dict[str, tuple[dict[str, Any], registry.ArtifactSnapshot]],
    tuple[tuple[dict[str, Any], registry.ArtifactSnapshot], ...],
]:
    source_candidates = dispatch._candidate_set(computation)  # noqa: SLF001
    dispatch_profile = dispatch.load_source_derived_outcome_dispatch_profile()
    try:
        (
            materialization_intents,
            materialization_receipts,
            materialization_events,
            _,
        ) = dispatch._load_durable_state(  # noqa: SLF001
            dispatch_profile,
            paths.dispatch,
            computation,
            overlay_event,
            source_candidates,
            now=now,
            build_plan=dispatch._historical_materialization_plan,  # noqa: SLF001
            verify_action=dispatch._recorded_materialization_output,  # noqa: SLF001
        )
        materialized = consumption._materialized_candidates(  # noqa: SLF001
            source_candidates,
            materialization_intents,
            materialization_receipts,
            materialization_events,
        )
        consumption_profile = (
            consumption.load_source_derived_outcome_consumption_profile()
        )
        _, consumption_receipts, consumption_events, _ = (
            consumption._load_durable_state(  # noqa: SLF001
                consumption_profile,
                paths.consumption,
                computation,
                overlay_event,
                materialized,
                build_plan=consumption._build_consumption_plan,  # noqa: SLF001
                verify_action=consumption._recorded_consumption_output,  # noqa: SLF001
            )
        )
    except dispatch.SourceDerivedOutcomeDispatchBusyError as exc:
        raise SourceDerivedDependentOutcomeDispatchBusyError(str(exc)) from exc
    except consumption.SourceDerivedOutcomeConsumptionBusyError as exc:
        raise SourceDerivedDependentOutcomeDispatchBusyError(str(exc)) from exc
    except (
        dispatch.SourceDerivedOutcomeDispatchError,
        consumption.SourceDerivedOutcomeConsumptionError,
    ) as exc:
        raise SourceDerivedDependentOutcomeDispatchIntegrityError(str(exc)) from exc
    return consumption_receipts, consumption_events


def _coordinate_source_derived_dependent_outcome_dispatch(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    registry_root: Path | None = None,
    active_root: Path | None = None,
    shadow_root: Path | None = None,
    clock: Callable[[], datetime] | None = None,
    fault_hook: FaultHook | None = None,
    load_derived_authority: overlay.LoadDerivedAuthority | None = None,
    build_plan: BuildPlan = _build_plan,
    materialize_action: MaterializeAction = _materialize_action,
    verify_action: MaterializeAction = _recorded_materialization_output,
) -> SourceDerivedDependentOutcomeDispatchResult:
    profile = load_source_derived_dependent_outcome_dispatch_profile(config_path)
    paths = source_derived_dependent_outcome_dispatch_paths(
        profile,
        registry_root=registry_root,
        active_root=active_root,
        shadow_root=shadow_root,
    )
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    _utc_text(now)
    handles: list[BinaryIO] = []
    current_item: Mapping[str, Any] | None = None
    current_step: str | None = None
    try:
        handles = _acquire_locks(paths)
        overlay_profile = overlay.load_source_derived_workset_overlay_profile()
        try:
            computation, waiting = overlay._load_computation(  # noqa: SLF001
                overlay_profile,
                paths.dispatch.overlay,
                now,
                load_derived_authority=load_derived_authority,
            )
        except overlay.SourceDerivedWorksetOverlayError as exc:
            raise SourceDerivedDependentOutcomeDispatchIntegrityError(str(exc)) from exc
        if computation is None:
            if any(
                _strict_entries(directory, name="dependent durable namespace")
                for directory in (paths.intents, paths.receipts, paths.events)
            ):
                raise SourceDerivedDependentOutcomeDispatchIntegrityError(
                    "Dependent dispatcher bytes outlived their overlay authority"
                )
            status = "waiting_for_published_effective_overlay"
            _write_status(
                profile,
                paths,
                now=now,
                status=status,
                reason=waiting,
                step_id=None,
                item=None,
                intent_path=None,
                receipt_path=None,
                event_path=None,
            )
            return _result(paths, status, waiting)
        try:
            stored_overlay, published_overlay = overlay._load_overlay_state(  # noqa: SLF001
                overlay_profile, paths.dispatch.overlay, computation
            )
        except overlay.SourceDerivedWorksetOverlayError as exc:
            raise SourceDerivedDependentOutcomeDispatchIntegrityError(str(exc)) from exc
        if stored_overlay is None or published_overlay is None:
            if any(
                _strict_entries(directory, name="dependent durable namespace")
                for directory in (paths.intents, paths.receipts, paths.events)
            ):
                raise SourceDerivedDependentOutcomeDispatchIntegrityError(
                    "Dependent dispatcher bytes exist before overlay publication"
                )
            reason = "the exact effective-workset overlay event is pending"
            status = "waiting_for_published_effective_overlay"
            _write_status(
                profile,
                paths,
                now=now,
                status=status,
                reason=reason,
                step_id=None,
                item=None,
                intent_path=None,
                receipt_path=None,
                event_path=None,
            )
            return _result(paths, status, reason)
        overlay_event = published_overlay[1]
        consumption_receipts, consumption_events = _deep_replay_prerequisites(
            computation, overlay_event, paths, now=now
        )
        terminals = _terminal_consumption_dependencies(
            computation,
            consumption_receipts,
            consumption_events,
            paths,
        )
        candidates = _dependent_candidates(computation, terminals, paths)
        intents, receipts, events, plans = _load_durable_state(
            profile,
            paths,
            computation,
            overlay_event,
            candidates,
            now=now,
            build_plan=build_plan,
            verify_action=verify_action,
        )
        event_steps = {str(payload["step_id"]) for payload, _ in events}

        pending = [step for step in receipts if step not in event_steps]
        if pending:
            current_step = pending[0]
            receipt, receipt_snapshot = receipts[current_step]
            intent, intent_snapshot = intents[current_step]
            selected = next(
                candidate
                for candidate in candidates
                if str(candidate.item["key_id"]) == str(intent["key_id"])
            )
            current_item = selected.item
            event_snapshot = _append_event(
                profile, paths, receipt, receipt_snapshot, events, now=now
            )
            status = "dependent_dispatch_event_forward_adopted"
            reason = "the durable dependent receipt was adopted into its event"
            _write_status(
                profile,
                paths,
                now=now,
                status=status,
                reason=reason,
                step_id=current_step,
                item=current_item,
                intent_path=intent_snapshot.path,
                receipt_path=receipt_snapshot.path,
                event_path=event_snapshot.path,
            )
            return _result(
                paths,
                status,
                reason,
                step_id=current_step,
                item=current_item,
                intent_path=intent_snapshot.path,
                receipt_path=receipt_snapshot.path,
                event_path=event_snapshot.path,
            )

        incomplete = [step for step in intents if step not in receipts]
        if incomplete:
            current_step = incomplete[0]
            intent, intent_snapshot = intents[current_step]
            selected = next(
                candidate
                for candidate in candidates
                if str(candidate.item["key_id"]) == str(intent["key_id"])
            )
            current_item = selected.item
            plan = plans[current_step]
            output = materialize_action(
                current_item, computation.reservation, plan.contract, now
            )
            if fault_hook is not None:
                fault_hook("after_materializer_action")
            receipt_payload = _receipt_payload(
                profile,
                paths,
                intent,
                intent_snapshot,
                output,
                recorded_at=_utc_text(now),
            )
            receipt_snapshot = _publish_receipt(paths, receipt_payload)
            if fault_hook is not None:
                fault_hook("after_receipt")
            event_snapshot = _append_event(
                profile, paths, receipt_payload, receipt_snapshot, events, now=now
            )
            status = "dependent_materialization_forward_adopted"
            reason = "the durable intent adopted the exact materializer commit"
            _write_status(
                profile,
                paths,
                now=now,
                status=status,
                reason=reason,
                step_id=current_step,
                item=current_item,
                intent_path=intent_snapshot.path,
                receipt_path=receipt_snapshot.path,
                event_path=event_snapshot.path,
            )
            return _result(
                paths,
                status,
                reason,
                step_id=current_step,
                item=current_item,
                intent_path=intent_snapshot.path,
                receipt_path=receipt_snapshot.path,
                event_path=event_snapshot.path,
            )

        selected = next(
            (
                candidate
                for candidate in candidates
                if _step_id(
                    overlay_event.sha256,
                    _sha256(_canonical_bytes(candidate.dependency_proof)),
                    str(candidate.item["key_id"]),
                )
                not in event_steps
            ),
            None,
        )
        if selected is None:
            all_rows = _effective_dri_rows(computation)
            non_source_rows = [
                item
                for item, _, _ in all_rows
                if item.get("dependency_keys")
                != [computation.cross_authority.source_item["natural_key"]]
            ]
            if candidates:
                status = "dependent_materialization_dispatch_current"
                reason = "all currently dependency-ready D/R keys are materialized"
            elif non_source_rows:
                status = "waiting_for_terminal_dependencies"
                reason = (
                    "no non-source-only D/R row has exact terminal consumption "
                    "events for every supported dependency"
                )
            else:
                status = "waiting_for_dependent_effective_candidate"
                reason = "the published overlay has no non-source-only D/R row"
            _write_status(
                profile,
                paths,
                now=now,
                status=status,
                reason=reason,
                step_id=None,
                item=None,
                intent_path=None,
                receipt_path=None,
                event_path=None,
            )
            return _result(paths, status, reason)

        current_item = selected.item
        proof_sha256 = _sha256(_canonical_bytes(selected.dependency_proof))
        current_step = _step_id(
            overlay_event.sha256, proof_sha256, str(current_item["key_id"])
        )
        plan = build_plan(
            current_item, computation.reservation, computation.derived_evidence, now
        )
        intent_payload = _intent_payload(
            profile,
            paths,
            computation,
            overlay_event,
            selected,
            plan.contract,
            recorded_at=_utc_text(now),
        )
        intent_snapshot = _publish_intent(paths, intent_payload)
        if fault_hook is not None:
            fault_hook("after_intent")
        output = materialize_action(
            current_item, computation.reservation, plan.contract, now
        )
        if fault_hook is not None:
            fault_hook("after_materializer_action")
        receipt_payload = _receipt_payload(
            profile,
            paths,
            intent_payload,
            intent_snapshot,
            output,
            recorded_at=_utc_text(now),
        )
        receipt_snapshot = _publish_receipt(paths, receipt_payload)
        if fault_hook is not None:
            fault_hook("after_receipt")
        event_snapshot = _append_event(
            profile, paths, receipt_payload, receipt_snapshot, events, now=now
        )
        status = "dependent_source_derived_outcome_materialized"
        reason = "one dependency-ready historical N+1 effective D/R was materialized"
        _write_status(
            profile,
            paths,
            now=now,
            status=status,
            reason=reason,
            step_id=current_step,
            item=current_item,
            intent_path=intent_snapshot.path,
            receipt_path=receipt_snapshot.path,
            event_path=event_snapshot.path,
        )
        return _result(
            paths,
            status,
            reason,
            step_id=current_step,
            item=current_item,
            intent_path=intent_snapshot.path,
            receipt_path=receipt_snapshot.path,
            event_path=event_snapshot.path,
        )
    except SourceDerivedDependentOutcomeDispatchBusyError:
        raise
    except SourceDerivedDependentOutcomeDispatchError as exc:
        if handles:
            try:
                _write_status(
                    profile,
                    paths,
                    now=now,
                    status="blocked_integrity",
                    reason=f"{type(exc).__name__}:{exc}",
                    step_id=current_step,
                    item=current_item,
                    intent_path=None,
                    receipt_path=None,
                    event_path=None,
                )
            except SourceDerivedDependentOutcomeDispatchError:
                pass
        raise
    except Exception as exc:
        raise SourceDerivedDependentOutcomeDispatchIntegrityError(
            f"Dependent dispatch failed:{type(exc).__name__}:{exc}"
        ) from exc
    finally:
        if handles:
            _release_locks(handles)


def coordinate_source_derived_dependent_outcome_dispatch(
    *, config_path: Path = DEFAULT_CONFIG_PATH
) -> SourceDerivedDependentOutcomeDispatchResult:
    """Run one machine-only dependency-gated materialization poll."""

    return _coordinate_source_derived_dependent_outcome_dispatch(
        config_path=config_path
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result = coordinate_source_derived_dependent_outcome_dispatch(
        config_path=args.config
    )
    print(
        json.dumps(
            {
                "status": result.status,
                "reason": result.reason,
                "status_path": str(result.status_path),
                "intent_path": str(result.intent_path) if result.intent_path else None,
                "receipt_path": (
                    str(result.receipt_path) if result.receipt_path else None
                ),
                "event_path": str(result.event_path) if result.event_path else None,
                "step_id": result.step_id,
                "key_id": result.key_id,
                "natural_key": result.natural_key,
                "outcome_materialization_performed": (
                    result.outcome_materialization_performed
                ),
                "terminal_for_effective_key": result.terminal_for_effective_key,
                "terminal_for_recovery_v6_key": (result.terminal_for_recovery_v6_key),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
