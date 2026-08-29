"""Consume one materialized source-derived outcome into the live ledger.

This sibling authority deep-replays the exact published effective-workset
overlay and its materialization dispatcher.  It binds one completed D/R
materialization to the canonical recovery outcome-consumption plan, freezes the
exact live-ledger pre-head and complete EventSpecs in a create-only intent, and
then runs or adopts the canonical CAS transaction.  Its terminal statement is
strictly scoped to the exact effective key; it never settles recovery-v6, the
source parent, the whole effective workset, or any lifecycle authority.
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
from monitoring import ootang_epoch_workset_recovery as recovery  # noqa: E402
from monitoring import (  # noqa: E402
    ootang_epoch_source_derived_outcome_dispatch as dispatch,
)
from monitoring import (  # noqa: E402
    ootang_epoch_source_derived_workset_overlay as overlay,
)


DEFAULT_CONFIG_PATH = (
    ROOT / "config" / "ootang_epoch_source_derived_outcome_consumption.v1.json"
)
DEFAULT_CONFIG_SHA256 = (
    "efd33c6c3e9cb386d64cd1e44720b4019c3468a774d695ddc3f77cca3efa863b"
)
IMPLEMENTATION_LOGICAL_PATH = (
    "code/monitoring/ootang_epoch_source_derived_outcome_consumption.py"
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
    "recovery_profile": {
        "path": "config/ootang_epoch_workset_recovery.v1.json",
        "expected_sha256": recovery.DEFAULT_CONFIG_SHA256,
    },
    "recovery_implementation": {
        "path": "code/monitoring/ootang_epoch_workset_recovery.py",
        "expected_sha256": (
            "b9f25133eef9cb94fb255bab588edcd573f0fa792ef82c4ddb9068d0a44a6c51"
        ),
    },
}
EXPECTED_RUNTIME = {
    "registry_root": "runtime/ootang_epoch_registry_v1",
    "recovery_namespace": "workset_recovery_v1",
    "namespace": "source_derived_outcome_consumption_v1",
    "intents": "intents",
    "receipts": "receipts",
    "events": "events",
    "status": "status.json",
    "manager_lock": "manager.lock",
    "active_root": "runtime/ootang_prequential_live_v1",
    "shadow_root": "runtime/ootang_prequential_calibration_shadow_v1",
}
EXPECTED_PROTOCOL = {
    "intent_schema_version": "ootang_epoch_source_derived_outcome_consumption_intent_v1",
    "receipt_schema_version": (
        "ootang_epoch_source_derived_outcome_consumption_receipt_v1"
    ),
    "event_schema_version": "ootang_epoch_source_derived_outcome_consumption_event_v1",
    "status_schema_version": (
        "ootang_epoch_source_derived_outcome_consumption_status_v1"
    ),
    "event_type": "epoch_source_derived_outcome_consumption_recorded",
    "action": "outcome_or_revision_consumed",
    "canonical_json": "utf8_sort_keys_compact_no_nan_trailing_lf",
    "initial_previous_entry_sha256": ZERO_HASH,
    "surviving_lock_order": list(LOCK_ORDER),
    "readiness_policy": (
        "exact_materialization_dispatch_event_for_source_only_effective_d_or_r"
    ),
    "poll_policy": (
        "heal_or_consume_at_most_one_materialized_source_derived_effective_key"
    ),
    "terminal_scope": "exact_effective_workset_key_only",
    "maximum_control_bytes": MAX_CONTROL_BYTES,
}
TRUE_CAPABILITIES = (
    "machine_only",
    "published_effective_overlay_deep_verified",
    "materialization_dispatch_deep_verified",
    "recovery_previous_step_adapter_bound",
    "exact_expected_pre_head_bound",
    "canonical_event_specs_bound",
    "canonical_live_ledger_cas_reused",
    "source_derived_outcome_consumption_implemented",
    "create_only_consumption_intents_implemented",
    "create_only_consumption_receipts_implemented",
    "append_only_consumption_events_implemented",
    "crash_forward_adoption_implemented",
)
FALSE_CLAIMS = (
    "terminal_for_recovery_v6_key",
    "source_parent_terminal",
    "recovery_v6_mutated",
    "effective_workset_overlay_mutated",
    "materialization_dispatch_mutated",
    "source_derived_reservation_mutated",
    "cross_freeze_authority_mutated",
    "source_ingest_executed",
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


class SourceDerivedOutcomeConsumptionError(RuntimeError):
    """Base error for the source-derived outcome consumption bridge."""


class SourceDerivedOutcomeConsumptionConfigError(SourceDerivedOutcomeConsumptionError):
    """The reviewed profile or a pinned dependency changed."""


class SourceDerivedOutcomeConsumptionIntegrityError(
    SourceDerivedOutcomeConsumptionError
):
    """An authority, plan, CAS slice, or durable record failed closed."""


class SourceDerivedOutcomeConsumptionBusyError(SourceDerivedOutcomeConsumptionError):
    """A surviving global lock or live-ledger CAS lock is held."""


@dataclass(frozen=True)
class SourceDerivedOutcomeConsumptionPaths:
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


@dataclass(frozen=True)
class SourceDerivedOutcomeConsumptionResult:
    status: str
    reason: str
    status_path: Path
    intent_path: Path | None = None
    receipt_path: Path | None = None
    event_path: Path | None = None
    step_id: str | None = None
    key_id: str | None = None
    natural_key: str | None = None
    outcome_or_revision_consumed: bool = False
    terminal_for_effective_key: bool = False
    terminal_for_recovery_v6_key: bool = False


@dataclass(frozen=True)
class _MaterializedCandidate:
    item: Mapping[str, Any]
    dri_kind: str
    topological_index: int
    materialization_intent: Mapping[str, Any]
    materialization_intent_snapshot: registry.ArtifactSnapshot
    materialization_receipt: Mapping[str, Any]
    materialization_receipt_snapshot: registry.ArtifactSnapshot
    materialization_event: Mapping[str, Any]
    materialization_event_snapshot: registry.ArtifactSnapshot
    previous_step_adapter: Mapping[str, Any]


FaultHook = Callable[[str], None]
BuildPlan = Callable[
    [Mapping[str, Any], recovery.Reservation, Mapping[str, Any]],
    recovery.OutcomeConsumptionPlan,
]
ConsumeAction = Callable[
    [Mapping[str, Any], recovery.Reservation, Mapping[str, Any], Mapping[str, Any]],
    recovery.ActionOutput,
]


def _canonical_bytes(value: object) -> bytes:
    try:
        return registry._canonical_bytes(value)  # type: ignore[arg-type]  # noqa: SLF001
    except (TypeError, ValueError) as exc:
        raise SourceDerivedOutcomeConsumptionIntegrityError(
            "Value is not canonical JSON"
        ) from exc


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _claims(*, consumed: bool) -> dict[str, bool]:
    return {
        **{name: True for name in TRUE_CAPABILITIES},
        "outcome_or_revision_consumed": consumed,
        "terminal_for_effective_key": consumed,
        "live_ledger_consumption_recorded": consumed,
        **{name: False for name in FALSE_CLAIMS},
    }


def _exact(value: object, keys: set[str], *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise SourceDerivedOutcomeConsumptionIntegrityError(f"{name} keys changed")
    return value


def _text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise SourceDerivedOutcomeConsumptionIntegrityError(f"{name} changed")
    return value


def _hash(value: object, *, name: str) -> str:
    result = _text(value, name=name)
    if len(result) != 64 or any(char not in "0123456789abcdef" for char in result):
        raise SourceDerivedOutcomeConsumptionIntegrityError(
            f"{name} is not a lowercase SHA-256"
        )
    return result


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SourceDerivedOutcomeConsumptionIntegrityError(
            "Outcome-consumption clock must be timezone-aware"
        )
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: object, *, name: str) -> datetime:
    try:
        return registry._utc_timestamp(value, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise SourceDerivedOutcomeConsumptionIntegrityError(str(exc)) from exc


def _child(root: Path, relative: str, *, name: str) -> Path:
    try:
        return registry._contained(root, relative, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise SourceDerivedOutcomeConsumptionConfigError(str(exc)) from exc


def _strict_json(
    path: Path, *, name: str
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    try:
        return dispatch._strict_json(path, name=name)  # noqa: SLF001
    except dispatch.SourceDerivedOutcomeDispatchError as exc:
        raise SourceDerivedOutcomeConsumptionIntegrityError(str(exc)) from exc


def _strict_entries(directory: Path, *, name: str) -> tuple[Path, ...]:
    try:
        return dispatch._strict_entries(directory, name=name)  # noqa: SLF001
    except dispatch.SourceDerivedOutcomeDispatchError as exc:
        raise SourceDerivedOutcomeConsumptionIntegrityError(str(exc)) from exc


def _publish(
    path: Path, raw: bytes, *, root: Path, name: str
) -> registry.ArtifactSnapshot:
    try:
        return dispatch._publish(path, raw, root=root, name=name)  # noqa: SLF001
    except dispatch.SourceDerivedOutcomeDispatchError as exc:
        raise SourceDerivedOutcomeConsumptionIntegrityError(str(exc)) from exc


def _reference(snapshot: registry.ArtifactSnapshot, root: Path) -> dict[str, object]:
    try:
        relative = snapshot.path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise SourceDerivedOutcomeConsumptionIntegrityError(
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
            name="source-derived outcome consumption implementation",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
    except registry.EpochRegistryError as exc:
        raise SourceDerivedOutcomeConsumptionIntegrityError(str(exc)) from exc
    return {
        "path": IMPLEMENTATION_LOGICAL_PATH,
        "sha256": snapshot.sha256,
        "size_bytes": snapshot.size_bytes,
    }


def load_source_derived_outcome_consumption_profile(
    path: Path = DEFAULT_CONFIG_PATH, *, project_root: Path = ROOT
) -> dict[str, Any]:
    root = project_root.resolve()
    resolved = path if path.is_absolute() else root / path
    if root != ROOT.resolve() or resolved.resolve() != DEFAULT_CONFIG_PATH.resolve():
        raise SourceDerivedOutcomeConsumptionConfigError(
            "Only the reviewed default source-derived consumption profile is accepted"
        )
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            resolved,
            name="source-derived outcome consumption profile",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
        profile = registry._decode_json(  # noqa: SLF001
            snapshot.raw, name="source-derived outcome consumption profile"
        )
    except registry.EpochRegistryError as exc:
        raise SourceDerivedOutcomeConsumptionConfigError(str(exc)) from exc
    if snapshot.sha256 != DEFAULT_CONFIG_SHA256:
        raise SourceDerivedOutcomeConsumptionConfigError(
            "Source-derived outcome consumption profile SHA-256 changed"
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
        name="source-derived outcome consumption profile",
    )
    if (
        profile["schema_version"]
        != "ootang_epoch_source_derived_outcome_consumption_profile_v1"
        or profile["profile_id"] != "ootang-epoch-source-derived-outcome-consumption-v1"
        or profile["profile_version"] != "1.0.0-effective-key-terminal-consumption"
        or profile["case"] != "ootang"
        or profile["artifact_status"]
        != "narrow_source_derived_effective_key_outcome_consumption_only"
        or profile["formal_warning_output"] is not False
        or profile["default_pipeline_member"] is not False
        or profile["upstream"] != EXPECTED_UPSTREAM
        or profile["runtime"] != EXPECTED_RUNTIME
        or profile["protocol"] != EXPECTED_PROTOCOL
        or profile["engineering_capabilities"] != _claims(consumed=False)
    ):
        raise SourceDerivedOutcomeConsumptionConfigError(
            "Source-derived outcome consumption profile semantics changed"
        )
    for binding in EXPECTED_UPSTREAM.values():
        upstream = _child(root, binding["path"], name="consumption upstream")
        try:
            actual = registry._read_regular(  # noqa: SLF001
                upstream,
                name="consumption upstream",
                maximum_bytes=64 * 1024 * 1024,
            )
        except registry.EpochRegistryError as exc:
            raise SourceDerivedOutcomeConsumptionConfigError(str(exc)) from exc
        if actual.sha256 != binding["expected_sha256"]:
            raise SourceDerivedOutcomeConsumptionConfigError(
                f"Pinned consumption upstream changed:{binding['path']}"
            )
    # These loaders transitively verify the overlay/materializer and the exact
    # live/live-ledger/CAS implementation graph used below.
    dispatch.load_source_derived_outcome_dispatch_profile()
    recovery.load_workset_recovery_profile()
    profile["_profile_sha256"] = snapshot.sha256
    return profile


def source_derived_outcome_consumption_paths(
    profile: Mapping[str, Any],
    *,
    registry_root: Path | None = None,
    active_root: Path | None = None,
    shadow_root: Path | None = None,
) -> SourceDerivedOutcomeConsumptionPaths:
    registry_path = (
        registry_root or ROOT / profile["runtime"]["registry_root"]
    ).resolve()
    recovery_root = _child(
        registry_path, profile["runtime"]["recovery_namespace"], name="recovery root"
    )
    root = _child(
        recovery_root, profile["runtime"]["namespace"], name="consumption root"
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
    return SourceDerivedOutcomeConsumptionPaths(
        registry_root=registry_path,
        root=root,
        intents=_child(root, profile["runtime"]["intents"], name="consumption intents"),
        receipts=_child(
            root, profile["runtime"]["receipts"], name="consumption receipts"
        ),
        events=_child(root, profile["runtime"]["events"], name="consumption events"),
        status=_child(root, profile["runtime"]["status"], name="consumption status"),
        manager_lock=_child(
            registry_path, profile["runtime"]["manager_lock"], name="manager lock"
        ),
        active_root=active,
        shadow_root=shadow,
        dispatch=dispatch_paths,
    )


def _acquire_locks(paths: SourceDerivedOutcomeConsumptionPaths) -> list[BinaryIO]:
    try:
        return dispatch._acquire_locks(paths.dispatch)  # noqa: SLF001
    except dispatch.SourceDerivedOutcomeDispatchBusyError as exc:
        raise SourceDerivedOutcomeConsumptionBusyError(str(exc)) from exc
    except dispatch.SourceDerivedOutcomeDispatchError as exc:
        raise SourceDerivedOutcomeConsumptionIntegrityError(str(exc)) from exc


def _release_locks(handles: Sequence[BinaryIO]) -> None:
    try:
        dispatch._release_locks(handles)  # noqa: SLF001
    except dispatch.SourceDerivedOutcomeDispatchError as exc:
        raise SourceDerivedOutcomeConsumptionIntegrityError(str(exc)) from exc


def _previous_step_adapter(
    receipt: Mapping[str, Any],
) -> dict[str, object]:
    output = _exact(
        receipt.get("output"),
        {"kind", "reference", "semantics"},
        name="materialization dispatcher output",
    )
    if not isinstance(output["semantics"], dict):
        raise SourceDerivedOutcomeConsumptionIntegrityError(
            "Materialization dispatcher semantics changed type"
        )
    reference = output["reference"]
    if reference is not None and not isinstance(reference, dict):
        raise SourceDerivedOutcomeConsumptionIntegrityError(
            "Materialization dispatcher output reference changed type"
        )
    adapter: dict[str, object] = {
        "key_id": receipt.get("key_id"),
        "natural_key": receipt.get("natural_key"),
        "namespace_digest": receipt.get("namespace_digest"),
        "action": receipt.get("action"),
        "action_output_kind": output["kind"],
        "action_output": dict(reference) if isinstance(reference, dict) else None,
        "action_semantics": dict(output["semantics"]),
        "next_actions": ["outcome_or_revision_consumed"],
        "terminal_for_key": False,
    }
    if (
        adapter["action"] != "outcome_materialized"
        or receipt.get("next_action") != "outcome_or_revision_consumed"
        or receipt.get("terminal_for_effective_key") is not False
        or receipt.get("terminal_for_recovery_v6_key") is not False
    ):
        raise SourceDerivedOutcomeConsumptionIntegrityError(
            "Materialization dispatcher receipt is not a non-terminal handoff"
        )
    return adapter


def _step_id(
    overlay_event_sha256: str, materialization_event_sha256: str, key_id: str
) -> str:
    return _sha256(
        _canonical_bytes(
            {
                "overlay_event_sha256": overlay_event_sha256,
                "materialization_dispatch_event_sha256": (materialization_event_sha256),
                "key_id": key_id,
                "step_index": 1,
                "action": "outcome_or_revision_consumed",
            }
        )
    )


def _materialized_candidates(
    candidates: Sequence[dispatch._Candidate],  # noqa: SLF001
    intents: Mapping[str, tuple[dict[str, Any], registry.ArtifactSnapshot]],
    receipts: Mapping[str, tuple[dict[str, Any], registry.ArtifactSnapshot]],
    events: Sequence[tuple[dict[str, Any], registry.ArtifactSnapshot]],
) -> tuple[_MaterializedCandidate, ...]:
    by_key = {str(candidate.item["key_id"]): candidate for candidate in candidates}
    if len(by_key) != len(candidates):
        raise SourceDerivedOutcomeConsumptionIntegrityError(
            "Materialization candidate key set changed"
        )
    result: list[_MaterializedCandidate] = []
    seen: set[str] = set()
    for event, event_snapshot in events:
        materialization_step = _hash(
            event.get("step_id"), name="materialization event step"
        )
        if materialization_step in seen:
            raise SourceDerivedOutcomeConsumptionIntegrityError(
                "Materialization event step was duplicated"
            )
        seen.add(materialization_step)
        intent_record = intents.get(materialization_step)
        receipt_record = receipts.get(materialization_step)
        if intent_record is None or receipt_record is None:
            raise SourceDerivedOutcomeConsumptionIntegrityError(
                "Materialization event lost its intent or receipt"
            )
        materialization_intent, materialization_intent_snapshot = intent_record
        materialization_receipt, materialization_receipt_snapshot = receipt_record
        key_id = _text(event.get("key_id"), name="materialization event key")
        candidate = by_key.get(key_id)
        if candidate is None:
            raise SourceDerivedOutcomeConsumptionIntegrityError(
                "Materialization event escaped the exact source-only D/R set"
            )
        item = candidate.item
        if (
            event.get("natural_key") != item.get("natural_key")
            or event.get("namespace_digest") != item.get("namespace_digest")
            or materialization_intent.get("key_id") != key_id
            or materialization_receipt.get("key_id") != key_id
        ):
            raise SourceDerivedOutcomeConsumptionIntegrityError(
                "Materialization event effective identity changed"
            )
        result.append(
            _MaterializedCandidate(
                item=item,
                dri_kind=candidate.kind,
                topological_index=candidate.topological_index,
                materialization_intent=materialization_intent,
                materialization_intent_snapshot=materialization_intent_snapshot,
                materialization_receipt=materialization_receipt,
                materialization_receipt_snapshot=materialization_receipt_snapshot,
                materialization_event=event,
                materialization_event_snapshot=event_snapshot,
                previous_step_adapter=_previous_step_adapter(materialization_receipt),
            )
        )
    return tuple(result)


def _build_consumption_plan(
    item: Mapping[str, Any],
    reservation: recovery.Reservation,
    previous_step_adapter: Mapping[str, Any],
) -> recovery.OutcomeConsumptionPlan:
    try:
        return recovery._outcome_consumption_plan(  # noqa: SLF001
            item, reservation, previous_step_adapter
        )
    except recovery.WorksetRecoveryBusyError as exc:
        raise SourceDerivedOutcomeConsumptionBusyError(str(exc)) from exc
    except recovery.WorksetRecoveryError as exc:
        raise SourceDerivedOutcomeConsumptionIntegrityError(str(exc)) from exc


def _recorded_consumption_plan(
    item: Mapping[str, Any],
    reservation: recovery.Reservation,
    contract: Mapping[str, Any],
    previous_step_adapter: Mapping[str, Any],
    *,
    build_plan: BuildPlan = _build_consumption_plan,
) -> recovery.OutcomeConsumptionPlan:
    try:
        if recovery._outcome_consumption_transaction_committed(  # noqa: SLF001
            reservation, contract
        ):
            return recovery._recorded_outcome_consumption_plan(  # noqa: SLF001
                item, reservation, contract, previous_step_adapter
            )
    except recovery.WorksetRecoveryBusyError as exc:
        raise SourceDerivedOutcomeConsumptionBusyError(str(exc)) from exc
    except recovery.WorksetRecoveryError as exc:
        raise SourceDerivedOutcomeConsumptionIntegrityError(str(exc)) from exc
    plan = build_plan(item, reservation, previous_step_adapter)
    if dict(plan.contract) != dict(contract):
        raise SourceDerivedOutcomeConsumptionIntegrityError(
            "Consumption intent cannot rebase onto a changed live-ledger pre-head"
        )
    return plan


def _default_consume_action(
    item: Mapping[str, Any],
    reservation: recovery.Reservation,
    contract: Mapping[str, Any],
    previous_step_adapter: Mapping[str, Any],
) -> recovery.ActionOutput:
    try:
        return recovery._outcome_consumption_action(  # noqa: SLF001
            item, reservation, contract, previous_step_adapter
        )
    except recovery.WorksetRecoveryBusyError as exc:
        raise SourceDerivedOutcomeConsumptionBusyError(str(exc)) from exc
    except recovery.WorksetRecoveryError as exc:
        raise SourceDerivedOutcomeConsumptionIntegrityError(str(exc)) from exc


def _recorded_consumption_output(
    item: Mapping[str, Any],
    reservation: recovery.Reservation,
    contract: Mapping[str, Any],
    previous_step_adapter: Mapping[str, Any],
) -> recovery.ActionOutput:
    try:
        committed = recovery._outcome_consumption_transaction_committed(  # noqa: SLF001
            reservation, contract
        )
    except recovery.WorksetRecoveryError as exc:
        raise SourceDerivedOutcomeConsumptionIntegrityError(str(exc)) from exc
    if not committed:
        raise SourceDerivedOutcomeConsumptionIntegrityError(
            "Consumption receipt exists without its exact live-ledger CAS slice"
        )
    return _default_consume_action(item, reservation, contract, previous_step_adapter)


def _plan_bindings(
    plan: recovery.OutcomeConsumptionPlan,
) -> tuple[dict[str, object], list[dict[str, object]], str]:
    try:
        pre_head = recovery._pre_head_payload(plan.expected_pre_head)  # noqa: SLF001
        specs = [
            recovery._event_spec_payload(spec)  # noqa: SLF001
            for spec in plan.specs
        ]
    except recovery.WorksetRecoveryError as exc:
        raise SourceDerivedOutcomeConsumptionIntegrityError(str(exc)) from exc
    specs_sha256 = _sha256(_canonical_bytes(specs))
    if plan.contract.get("event_specs_sha256") != specs_sha256:
        raise SourceDerivedOutcomeConsumptionIntegrityError(
            "Canonical EventSpecs no longer match the recovery contract"
        )
    return pre_head, specs, specs_sha256


def _intent_payload(
    profile: Mapping[str, Any],
    paths: SourceDerivedOutcomeConsumptionPaths,
    computation: overlay.SourceDerivedWorksetOverlayComputation,
    overlay_event: registry.ArtifactSnapshot,
    candidate: _MaterializedCandidate,
    plan: recovery.OutcomeConsumptionPlan,
    *,
    recorded_at: str,
) -> dict[str, object]:
    item = candidate.item
    step_id = _step_id(
        overlay_event.sha256,
        candidate.materialization_event_snapshot.sha256,
        str(item["key_id"]),
    )
    adapter = dict(candidate.previous_step_adapter)
    contract = dict(plan.contract)
    expected_pre_head, event_specs, event_specs_sha256 = _plan_bindings(plan)
    item_payload = dict(item)
    transition_plan = recovery._transition_plan(item)  # noqa: SLF001
    return {
        "schema_version": profile["protocol"]["intent_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "recorded_at_utc": recorded_at,
        "step_id": step_id,
        "step_index": 1,
        "action": "outcome_or_revision_consumed",
        "next_actions": [],
        "dri_kind": candidate.dri_kind,
        "topological_index": candidate.topological_index,
        "key_id": item["key_id"],
        "natural_key": item["natural_key"],
        "namespace_digest": item["namespace_digest"],
        "overlay_slot_id": computation.slot_id,
        "overlay_event": _reference(overlay_event, paths.dispatch.overlay.root),
        "materialization_dispatch": {
            "step_id": candidate.materialization_event["step_id"],
            "intent": _reference(
                candidate.materialization_intent_snapshot, paths.dispatch.root
            ),
            "receipt": _reference(
                candidate.materialization_receipt_snapshot, paths.dispatch.root
            ),
            "event": _reference(
                candidate.materialization_event_snapshot, paths.dispatch.root
            ),
        },
        "effective_item": item_payload,
        "effective_item_sha256": _sha256(_canonical_bytes(item_payload)),
        "transition_plan_sha256": transition_plan["plan_sha256"],
        "recovery_previous_step_adapter": adapter,
        "recovery_previous_step_adapter_sha256": _sha256(_canonical_bytes(adapter)),
        "expected_pre_head": expected_pre_head,
        "consumption_contract": contract,
        "consumption_contract_sha256": _sha256(_canonical_bytes(contract)),
        "canonical_event_specs": event_specs,
        "canonical_event_specs_sha256": event_specs_sha256,
        "implementation": _implementation_reference(),
        **_claims(consumed=False),
    }


def _receipt_payload(
    profile: Mapping[str, Any],
    paths: SourceDerivedOutcomeConsumptionPaths,
    intent: Mapping[str, Any],
    intent_snapshot: registry.ArtifactSnapshot,
    output: recovery.ActionOutput,
    *,
    recorded_at: str,
) -> dict[str, object]:
    if output.kind != "live_outcome_consumption_transaction":
        raise SourceDerivedOutcomeConsumptionIntegrityError(
            "Canonical recovery writer returned an unexpected output kind"
        )
    if output.reference is not None:
        raise SourceDerivedOutcomeConsumptionIntegrityError(
            "Canonical live-ledger consumption unexpectedly returned a reference"
        )
    semantics = dict(output.semantics)
    if (
        semantics.get("expected_pre_head") != intent.get("expected_pre_head")
        or semantics.get("event_specs_sha256")
        != intent.get("canonical_event_specs_sha256")
        or semantics.get("live_ledger_events_recorded") is not True
        or semantics.get("canonical_frozen_writer_reused") is not True
        or semantics.get("contiguous_exact_slice_verified") is not True
        or semantics.get("network_action_performed") is not False
        or semantics.get("trusted_anchor_receipt_verified") is not False
        or semantics.get("e2_live_evidence_eligible") is not False
        or semantics.get("formal_warning_output") is not False
    ):
        raise SourceDerivedOutcomeConsumptionIntegrityError(
            "Canonical outcome-consumption output semantics changed"
        )
    terminal_identity = {
        "scope": "effective_workset_key",
        "key_id": intent["key_id"],
        "natural_key": intent["natural_key"],
        "namespace_digest": intent["namespace_digest"],
    }
    return {
        "schema_version": profile["protocol"]["receipt_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "recorded_at_utc": recorded_at,
        "step_id": intent["step_id"],
        "step_index": 1,
        "action": "outcome_or_revision_consumed",
        "next_actions": [],
        "key_id": intent["key_id"],
        "natural_key": intent["natural_key"],
        "namespace_digest": intent["namespace_digest"],
        "intent": _reference(intent_snapshot, paths.root),
        "materialization_dispatch_event": intent["materialization_dispatch"]["event"],
        "consumption_contract_sha256": intent["consumption_contract_sha256"],
        "canonical_event_specs_sha256": intent["canonical_event_specs_sha256"],
        "output": {
            "kind": output.kind,
            "reference": None,
            "semantics": semantics,
        },
        "terminal_identity": terminal_identity,
        "terminal_for_effective_key": True,
        "terminal_for_recovery_v6_key": False,
        "source_parent_terminal": False,
        **_claims(consumed=True),
    }


def _event_payload(
    profile: Mapping[str, Any],
    paths: SourceDerivedOutcomeConsumptionPaths,
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
        "step_index": 1,
        "action": "outcome_or_revision_consumed",
        "next_actions": [],
        "key_id": receipt["key_id"],
        "natural_key": receipt["natural_key"],
        "namespace_digest": receipt["namespace_digest"],
        "receipt": _reference(receipt_snapshot, paths.root),
        "materialization_dispatch_event": receipt["materialization_dispatch_event"],
        "consumption_contract_sha256": receipt["consumption_contract_sha256"],
        "canonical_event_specs_sha256": receipt["canonical_event_specs_sha256"],
        "terminal_identity": receipt["terminal_identity"],
        "terminal_for_effective_key": True,
        "terminal_for_recovery_v6_key": False,
        "source_parent_terminal": False,
        **_claims(consumed=True),
    }
    return {**body, "entry_sha256": _sha256(_canonical_bytes(body))}


def _same_except_time(
    actual: Mapping[str, Any], expected: Mapping[str, Any], *, name: str
) -> None:
    if set(actual) != set(expected):
        raise SourceDerivedOutcomeConsumptionIntegrityError(f"{name} keys changed")
    _parse_utc(actual.get("recorded_at_utc"), name=f"{name} time")
    comparable = {
        key: value for key, value in actual.items() if key != "recorded_at_utc"
    }
    expected_comparable = {
        key: value for key, value in expected.items() if key != "recorded_at_utc"
    }
    if comparable != expected_comparable:
        raise SourceDerivedOutcomeConsumptionIntegrityError(f"{name} semantics changed")


def _named_records(
    directory: Path, *, name: str
) -> dict[str, tuple[dict[str, Any], registry.ArtifactSnapshot]]:
    result: dict[str, tuple[dict[str, Any], registry.ArtifactSnapshot]] = {}
    for path in _strict_entries(directory, name=name):
        matched = RECORD_NAME.fullmatch(path.name)
        if matched is None:
            raise SourceDerivedOutcomeConsumptionIntegrityError(
                f"{name} contains an unexpected entry"
            )
        step = matched.group("step")
        if step in result:
            raise SourceDerivedOutcomeConsumptionIntegrityError(
                f"{name} contains a duplicate step"
            )
        result[step] = _strict_json(path, name=f"{name} record")
    return result


def _event_records(
    profile: Mapping[str, Any],
    paths: SourceDerivedOutcomeConsumptionPaths,
    receipts: Mapping[str, tuple[dict[str, Any], registry.ArtifactSnapshot]],
) -> tuple[tuple[dict[str, Any], registry.ArtifactSnapshot], ...]:
    result: list[tuple[dict[str, Any], registry.ArtifactSnapshot]] = []
    previous = ZERO_HASH
    seen: set[str] = set()
    for index, path in enumerate(
        _strict_entries(paths.events, name="source-derived consumption events"),
        start=1,
    ):
        matched = EVENT_NAME.fullmatch(path.name)
        if matched is None or int(matched.group("sequence")) != index:
            raise SourceDerivedOutcomeConsumptionIntegrityError(
                "Source-derived consumption event sequence branched"
            )
        payload, snapshot = _strict_json(path, name="source-derived consumption event")
        step = _text(payload.get("step_id"), name="consumption event step")
        if step in seen or step not in receipts:
            raise SourceDerivedOutcomeConsumptionIntegrityError(
                "Source-derived consumption event receipt binding branched"
            )
        receipt, receipt_snapshot = receipts[step]
        recorded_at = payload.get("recorded_at_utc")
        _parse_utc(recorded_at, name="source-derived consumption event time")
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
            raise SourceDerivedOutcomeConsumptionIntegrityError(
                "Source-derived consumption event semantics changed"
            )
        entry = _hash(payload.get("entry_sha256"), name="consumption event entry")
        if matched.group("entry") != entry:
            raise SourceDerivedOutcomeConsumptionIntegrityError(
                "Source-derived consumption event filename changed"
            )
        previous = entry
        seen.add(step)
        result.append((payload, snapshot))
    return tuple(result)


def _load_durable_state(
    profile: Mapping[str, Any],
    paths: SourceDerivedOutcomeConsumptionPaths,
    computation: overlay.SourceDerivedWorksetOverlayComputation,
    overlay_event: registry.ArtifactSnapshot,
    materialized: Sequence[_MaterializedCandidate],
    *,
    build_plan: BuildPlan,
    verify_action: ConsumeAction,
) -> tuple[
    dict[str, tuple[dict[str, Any], registry.ArtifactSnapshot]],
    dict[str, tuple[dict[str, Any], registry.ArtifactSnapshot]],
    tuple[tuple[dict[str, Any], registry.ArtifactSnapshot], ...],
    dict[str, recovery.OutcomeConsumptionPlan],
]:
    intents = _named_records(paths.intents, name="source-derived consumption intents")
    receipts = _named_records(
        paths.receipts, name="source-derived consumption receipts"
    )
    if not set(receipts) <= set(intents):
        raise SourceDerivedOutcomeConsumptionIntegrityError(
            "Source-derived consumption receipt is orphaned"
        )
    by_step = {
        _step_id(
            overlay_event.sha256,
            candidate.materialization_event_snapshot.sha256,
            str(candidate.item["key_id"]),
        ): candidate
        for candidate in materialized
    }
    if len(by_step) != len(materialized):
        raise SourceDerivedOutcomeConsumptionIntegrityError(
            "Source-derived consumption eligible step set collided"
        )
    if not set(intents) <= set(by_step):
        raise SourceDerivedOutcomeConsumptionIntegrityError(
            "Consumption state is outside the exact materialization event set"
        )
    plans: dict[str, recovery.OutcomeConsumptionPlan] = {}
    for step, (intent, intent_snapshot) in intents.items():
        candidate = by_step[step]
        contract = intent.get("consumption_contract")
        if not isinstance(contract, dict):
            raise SourceDerivedOutcomeConsumptionIntegrityError(
                "Consumption intent contract changed type"
            )
        plan = _recorded_consumption_plan(
            candidate.item,
            computation.reservation,
            contract,
            candidate.previous_step_adapter,
            build_plan=build_plan,
        )
        plans[step] = plan
        expected_intent = _intent_payload(
            profile,
            paths,
            computation,
            overlay_event,
            candidate,
            plan,
            recorded_at="",
        )
        _same_except_time(
            intent, expected_intent, name="source-derived consumption intent"
        )
        if intent_snapshot.path.name != f"{step}.json":
            raise SourceDerivedOutcomeConsumptionIntegrityError(
                "Source-derived consumption intent filename changed"
            )
        if step in receipts:
            receipt, receipt_snapshot = receipts[step]
            output = verify_action(
                candidate.item,
                computation.reservation,
                plan.contract,
                candidate.previous_step_adapter,
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
                receipt, expected_receipt, name="source-derived consumption receipt"
            )
            if receipt_snapshot.path.name != f"{step}.json":
                raise SourceDerivedOutcomeConsumptionIntegrityError(
                    "Source-derived consumption receipt filename changed"
                )
    incomplete = set(intents) - set(receipts)
    if len(incomplete) > 1:
        raise SourceDerivedOutcomeConsumptionIntegrityError(
            "Consumption authority contains branched incomplete intents"
        )
    events = _event_records(profile, paths, receipts)
    event_steps = {str(payload["step_id"]) for payload, _ in events}
    pending_receipts = set(receipts) - event_steps
    if len(pending_receipts) > 1:
        raise SourceDerivedOutcomeConsumptionIntegrityError(
            "Consumption authority contains branched receipt-only commits"
        )
    if incomplete and pending_receipts:
        raise SourceDerivedOutcomeConsumptionIntegrityError(
            "Consumption authority contains competing crash frontiers"
        )
    return intents, receipts, events, plans


def _publish_intent(
    paths: SourceDerivedOutcomeConsumptionPaths, payload: Mapping[str, Any]
) -> registry.ArtifactSnapshot:
    step = _hash(payload.get("step_id"), name="consumption intent step")
    return _publish(
        paths.intents / f"{step}.json",
        _canonical_bytes(payload),
        root=paths.root,
        name="source-derived consumption intent",
    )


def _publish_receipt(
    paths: SourceDerivedOutcomeConsumptionPaths, payload: Mapping[str, Any]
) -> registry.ArtifactSnapshot:
    step = _hash(payload.get("step_id"), name="consumption receipt step")
    return _publish(
        paths.receipts / f"{step}.json",
        _canonical_bytes(payload),
        root=paths.root,
        name="source-derived consumption receipt",
    )


def _append_event(
    profile: Mapping[str, Any],
    paths: SourceDerivedOutcomeConsumptionPaths,
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
        name="source-derived consumption event",
    )


def _write_status(
    profile: Mapping[str, Any],
    paths: SourceDerivedOutcomeConsumptionPaths,
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
    consumed = event_path is not None
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
        "terminal_scope": ("exact_effective_workset_key" if consumed else None),
        "cache_authority": False,
        **_claims(consumed=consumed),
    }
    try:
        registry._atomic_cache(  # noqa: SLF001
            paths.status,
            _canonical_bytes(payload),
            root=paths.root,
            name="source-derived consumption status",
        )
    except registry.EpochRegistryError as exc:
        raise SourceDerivedOutcomeConsumptionIntegrityError(str(exc)) from exc


def _result(
    paths: SourceDerivedOutcomeConsumptionPaths,
    status: str,
    reason: str,
    *,
    step_id: str | None = None,
    item: Mapping[str, Any] | None = None,
    intent_path: Path | None = None,
    receipt_path: Path | None = None,
    event_path: Path | None = None,
) -> SourceDerivedOutcomeConsumptionResult:
    consumed = event_path is not None
    return SourceDerivedOutcomeConsumptionResult(
        status=status,
        reason=reason,
        status_path=paths.status,
        intent_path=intent_path,
        receipt_path=receipt_path,
        event_path=event_path,
        step_id=step_id,
        key_id=str(item["key_id"]) if item else None,
        natural_key=str(item["natural_key"]) if item else None,
        outcome_or_revision_consumed=consumed,
        terminal_for_effective_key=consumed,
    )


def _coordinate_source_derived_outcome_consumption(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    registry_root: Path | None = None,
    active_root: Path | None = None,
    shadow_root: Path | None = None,
    clock: Callable[[], datetime] | None = None,
    fault_hook: FaultHook | None = None,
    load_derived_authority: overlay.LoadDerivedAuthority | None = None,
    build_plan: BuildPlan = _build_consumption_plan,
    consume_action: ConsumeAction = _default_consume_action,
    verify_action: ConsumeAction = _recorded_consumption_output,
) -> SourceDerivedOutcomeConsumptionResult:
    profile = load_source_derived_outcome_consumption_profile(config_path)
    paths = source_derived_outcome_consumption_paths(
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
            raise SourceDerivedOutcomeConsumptionIntegrityError(str(exc)) from exc
        if computation is None:
            if any(
                _strict_entries(directory, name="consumption durable namespace")
                for directory in (paths.intents, paths.receipts, paths.events)
            ):
                raise SourceDerivedOutcomeConsumptionIntegrityError(
                    "Consumption bytes outlived their exact overlay authority"
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
            raise SourceDerivedOutcomeConsumptionIntegrityError(str(exc)) from exc
        if stored_overlay is None or published_overlay is None:
            if any(
                _strict_entries(directory, name="consumption durable namespace")
                for directory in (paths.intents, paths.receipts, paths.events)
            ):
                raise SourceDerivedOutcomeConsumptionIntegrityError(
                    "Consumption bytes exist before overlay publication"
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
        overlay_event_snapshot = published_overlay[1]
        candidates = dispatch._candidate_set(computation)  # noqa: SLF001
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
                overlay_event_snapshot,
                candidates,
                now=now,
                build_plan=dispatch._historical_materialization_plan,  # noqa: SLF001
                verify_action=dispatch._recorded_materialization_output,  # noqa: SLF001
            )
        except dispatch.SourceDerivedOutcomeDispatchBusyError as exc:
            raise SourceDerivedOutcomeConsumptionBusyError(str(exc)) from exc
        except dispatch.SourceDerivedOutcomeDispatchError as exc:
            raise SourceDerivedOutcomeConsumptionIntegrityError(str(exc)) from exc
        materialized = _materialized_candidates(
            candidates,
            materialization_intents,
            materialization_receipts,
            materialization_events,
        )
        intents, receipts, events, plans = _load_durable_state(
            profile,
            paths,
            computation,
            overlay_event_snapshot,
            materialized,
            build_plan=build_plan,
            verify_action=verify_action,
        )
        event_steps = {str(payload["step_id"]) for payload, _ in events}

        pending_receipts = [step for step in receipts if step not in event_steps]
        if pending_receipts:
            current_step = pending_receipts[0]
            receipt, receipt_snapshot = receipts[current_step]
            intent, intent_snapshot = intents[current_step]
            selected = next(
                candidate
                for candidate in materialized
                if str(candidate.item["key_id"]) == str(intent["key_id"])
            )
            current_item = selected.item
            event_snapshot = _append_event(
                profile, paths, receipt, receipt_snapshot, events, now=now
            )
            status = "source_derived_consumption_event_forward_adopted"
            reason = "the terminal receipt was adopted into its append-only event"
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
                for candidate in materialized
                if str(candidate.item["key_id"]) == str(intent["key_id"])
            )
            current_item = selected.item
            plan = plans[current_step]
            output = consume_action(
                current_item,
                computation.reservation,
                plan.contract,
                selected.previous_step_adapter,
            )
            if fault_hook is not None:
                fault_hook("after_live_ledger_cas")
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
            status = "source_derived_consumption_forward_adopted"
            reason = "the durable intent adopted the exact canonical CAS transaction"
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
                for candidate in materialized
                if _step_id(
                    overlay_event_snapshot.sha256,
                    candidate.materialization_event_snapshot.sha256,
                    str(candidate.item["key_id"]),
                )
                not in event_steps
            ),
            None,
        )
        if selected is None:
            if materialized:
                status = "source_derived_outcome_consumption_current"
                reason = "all materialization-dispatched effective keys are consumed"
            else:
                status = "waiting_for_materialization_dispatch_event"
                reason = "no exact source-derived D/R materialization event is ready"
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
        current_step = _step_id(
            overlay_event_snapshot.sha256,
            selected.materialization_event_snapshot.sha256,
            str(current_item["key_id"]),
        )
        plan = build_plan(
            current_item,
            computation.reservation,
            selected.previous_step_adapter,
        )
        intent_payload = _intent_payload(
            profile,
            paths,
            computation,
            overlay_event_snapshot,
            selected,
            plan,
            recorded_at=_utc_text(now),
        )
        intent_snapshot = _publish_intent(paths, intent_payload)
        if fault_hook is not None:
            fault_hook("after_intent")
        output = consume_action(
            current_item,
            computation.reservation,
            plan.contract,
            selected.previous_step_adapter,
        )
        if fault_hook is not None:
            fault_hook("after_live_ledger_cas")
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
        status = "source_derived_outcome_consumed"
        reason = "one exact materialized source-derived D/R key was consumed"
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
    except SourceDerivedOutcomeConsumptionBusyError:
        raise
    except SourceDerivedOutcomeConsumptionError as exc:
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
            except SourceDerivedOutcomeConsumptionError:
                pass
        raise
    except Exception as exc:
        raise SourceDerivedOutcomeConsumptionIntegrityError(
            f"Source-derived consumption failed:{type(exc).__name__}:{exc}"
        ) from exc
    finally:
        if handles:
            _release_locks(handles)


def coordinate_source_derived_outcome_consumption(
    *, config_path: Path = DEFAULT_CONFIG_PATH
) -> SourceDerivedOutcomeConsumptionResult:
    """Run one machine-only source-derived outcome consumption poll."""

    return _coordinate_source_derived_outcome_consumption(config_path=config_path)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result = coordinate_source_derived_outcome_consumption(config_path=args.config)
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
                "outcome_or_revision_consumed": (result.outcome_or_revision_consumed),
                "terminal_for_effective_key": result.terminal_for_effective_key,
                "terminal_for_recovery_v6_key": (result.terminal_for_recovery_v6_key),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
