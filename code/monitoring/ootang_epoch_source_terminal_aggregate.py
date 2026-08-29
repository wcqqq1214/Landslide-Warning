"""Publish aggregate-only source-key terminal proofs from completed overlay events.

The assessor is read-only with respect to recovery v6, the step-dependency
sidecar, the settlement overlay, and the live ledger.  It consumes at most one
completed overlay-event prefix slot into one deterministic content-addressed
proof and one append-only aggregate event.
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
from monitoring import ootang_epoch_step_dependency_overlay as overlay  # noqa: E402
from monitoring import ootang_epoch_step_dependency_reservation as sidecar  # noqa: E402
from monitoring import ootang_epoch_workset_manifest as manifest  # noqa: E402
from monitoring import ootang_epoch_workset_recovery as recovery  # noqa: E402


DEFAULT_CONFIG_PATH = ROOT / "config" / "ootang_epoch_source_terminal_aggregate.v1.json"
DEFAULT_CONFIG_SHA256 = (
    "80779ecdb582d2dde53576668037597ac29bf56f486ac926a9754f103eb6604c"
)
IMPLEMENTATION_LOGICAL_PATH = (
    "code/monitoring/ootang_epoch_source_terminal_aggregate.py"
)
MAX_CONTROL_BYTES = 4 * 1024 * 1024
ZERO_HASH = "0" * 64
LOCK_ORDER = recovery.LOCK_ORDER
PROOF_NAME = re.compile(r"^(?P<digest>[0-9a-f]{64})\.json$")
EVENT_NAME = re.compile(r"^(?P<sequence>[0-9]{20})-(?P<entry>[0-9a-f]{64})\.json$")

EXPECTED_UPSTREAM = {
    "recovery_profile": {
        "path": "config/ootang_epoch_workset_recovery.v1.json",
        "expected_sha256": (
            "5c50d168d389c286d0940a00884369ae8f65fc399f8726f0f64300999dd2de01"
        ),
    },
    "recovery_implementation": {
        "path": "code/monitoring/ootang_epoch_workset_recovery.py",
        "expected_sha256": (
            "b9f25133eef9cb94fb255bab588edcd573f0fa792ef82c4ddb9068d0a44a6c51"
        ),
    },
    "step_dependency_profile": {
        "path": "config/ootang_epoch_step_dependency_reservation.v1.json",
        "expected_sha256": (
            "8b10a9642610b76911813d80ee8e31205c0e3c490aa05a13f0ba8287d457670e"
        ),
    },
    "step_dependency_implementation": {
        "path": "code/monitoring/ootang_epoch_step_dependency_reservation.py",
        "expected_sha256": (
            "c385b7c8783d86831561d5c1179b05efc78e3f5ef99a912b44382143625e5190"
        ),
    },
    "overlay_profile": {
        "path": "config/ootang_epoch_step_dependency_overlay.v1.json",
        "expected_sha256": (
            "4da333ef6233059aedb6ff1cd52bb6de31bae18aa14f1e571e97ee3018f52496"
        ),
    },
    "overlay_implementation": {
        "path": "code/monitoring/ootang_epoch_step_dependency_overlay.py",
        "expected_sha256": (
            "53932a5ebd095d98f08fe68aaa3891ba9569b51950da34bfbf662882d97fff53"
        ),
    },
}
EXPECTED_RUNTIME = {
    "registry_root": "runtime/ootang_epoch_registry_v1",
    "recovery_namespace": "workset_recovery_v1",
    "overlay_namespace": "step_dependency_overlay_v1",
    "namespace": "source_terminal_aggregate_v1",
    "proofs": "proofs",
    "events": "events",
    "status": "status.json",
    "manager_lock": "manager.lock",
    "active_root": "runtime/ootang_prequential_live_v1",
    "shadow_root": "runtime/ootang_prequential_calibration_shadow_v1",
}
EXPECTED_PROTOCOL = {
    "proof_schema_version": "ootang_epoch_source_terminal_aggregate_proof_v1",
    "event_schema_version": "ootang_epoch_source_terminal_aggregate_event_v1",
    "status_schema_version": "ootang_epoch_source_terminal_aggregate_status_v1",
    "event_type": "epoch_source_terminal_aggregate_proved",
    "source_action": "anchor_result_recorded",
    "terminal_successor": "outcome_batch_settled",
    "canonical_json": "utf8_sort_keys_compact_no_nan_trailing_lf",
    "initial_previous_entry_sha256": ZERO_HASH,
    "maximum_items": 4096,
    "maximum_control_bytes": MAX_CONTROL_BYTES,
    "poll_policy": (
        "forward_adopt_or_prove_at_most_one_completed_overlay_event_prefix_slot"
    ),
}
TRUE_CAPABILITIES = (
    "machine_only",
    "recovery_v6_authority_deep_verified",
    "step_dependency_sidecar_authority_deep_verified",
    "settlement_overlay_authority_deep_verified",
    "overlay_event_prefix_order_enforced",
    "aggregate_event_commit_boundary_enforced",
    "content_addressed_source_terminal_proofs_implemented",
    "append_only_source_terminal_events_implemented",
    "proof_event_forward_adoption_implemented",
    "source_live_outstanding_terminal_aggregate_proof_implemented",
    "one_ready_slot_per_poll",
)
FALSE_CLAIMS = (
    "bounded_workset_recovery_implemented",
    "full_workset_terminal",
    "all_reserved_items_settled",
    "all_reserved_successors_supported",
    "terminal_transition_closure_implemented",
    "transitive_terminal_closure_implemented",
    "derived_future_work_reservation_implemented",
    "all_transition_branches_supported",
    "network_recovery_implemented",
    "original_recovery_key_terminal",
    "recovery_v6_namespace_mutated",
    "step_dependency_sidecar_mutated",
    "settlement_overlay_mutated",
    "new_settlement_transaction_created",
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


class SourceTerminalAggregateError(RuntimeError):
    """Base source-terminal aggregate error."""


class SourceTerminalAggregateConfigError(SourceTerminalAggregateError):
    """The reviewed aggregate profile or a pinned upstream changed."""


class SourceTerminalAggregateIntegrityError(SourceTerminalAggregateError):
    """Persisted aggregate or upstream authority failed closed."""


class SourceTerminalAggregateBusyError(SourceTerminalAggregateError):
    """A surviving coordinator writer owns one of the four locks."""


@dataclass(frozen=True)
class SourceTerminalAggregatePaths:
    registry_root: Path
    root: Path
    proofs: Path
    events: Path
    status: Path
    manager_lock: Path
    active_root: Path
    shadow_root: Path
    cycle_lock: Path
    replay_lock: Path
    shadow_lock: Path
    overlay: overlay.OverlayPaths


@dataclass(frozen=True)
class SourceTerminalAggregateResult:
    status: str
    reason: str
    status_path: Path
    slot_id: str | None = None
    source_key_id: str | None = None
    dependency_key_id: str | None = None
    proof_path: Path | None = None
    event_path: Path | None = None
    terminal_for_source_key: bool = False
    original_recovery_key_terminal: bool = False
    full_workset_terminal: bool = False
    terminal_transition_closure_implemented: bool = False
    derived_future_work_reservation_implemented: bool = False
    lifecycle_authority: bool = False
    old_epoch_drained: bool = False


@dataclass(frozen=True)
class SourceTerminalSlot:
    overlay_event: Mapping[str, Any]
    overlay_event_snapshot: registry.ArtifactSnapshot
    overlay_intent: Mapping[str, Any]
    overlay_intent_snapshot: registry.ArtifactSnapshot
    overlay_receipt: Mapping[str, Any]
    overlay_receipt_snapshot: registry.ArtifactSnapshot
    sidecar_event: Mapping[str, Any]
    sidecar_event_snapshot: registry.ArtifactSnapshot
    sidecar_reservation: Mapping[str, Any]
    sidecar_reservation_snapshot: registry.ArtifactSnapshot
    candidate: Any

    @property
    def slot_id(self) -> str:
        return str(self.overlay_event["slot_id"])

    @property
    def source_key_id(self) -> str:
        return str(self.overlay_event["source_key_id"])

    @property
    def dependency_key_id(self) -> str:
        return str(self.overlay_event["dependency_key_id"])


@dataclass(frozen=True)
class SourceTerminalAggregateAuthority:
    overlay_profile: Mapping[str, Any]
    overlay_authority: overlay.OverlayAuthority
    overlay_state: Any
    completed_slots: tuple[SourceTerminalSlot, ...]
    incomplete_overlay_authority: bool


LoadAuthority = Callable[
    [SourceTerminalAggregatePaths, datetime],
    SourceTerminalAggregateAuthority | None,
]


@dataclass(frozen=True)
class _AggregateState:
    proofs: Mapping[
        str, tuple[dict[str, Any], registry.ArtifactSnapshot, SourceTerminalSlot]
    ]
    events: tuple[dict[str, Any], ...]
    previous_entry_sha256: str
    pending_proof_slot_id: str | None


def _canonical_bytes(value: object) -> bytes:
    try:
        return registry._canonical_bytes(value)  # type: ignore[arg-type]  # noqa: SLF001
    except (TypeError, ValueError) as exc:
        raise SourceTerminalAggregateIntegrityError(
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
        raise SourceTerminalAggregateIntegrityError(f"{name} keys changed")
    return value


def _hash(value: object, *, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise SourceTerminalAggregateIntegrityError(
            f"{name} is not a lowercase SHA-256"
        )
    return value


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SourceTerminalAggregateIntegrityError(
            "Aggregate clock must be timezone-aware"
        )
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: object, *, name: str) -> datetime:
    try:
        return registry._utc_timestamp(value, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise SourceTerminalAggregateIntegrityError(str(exc)) from exc


def _child(root: Path, relative: str, *, name: str) -> Path:
    try:
        return registry._contained(root, relative, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise SourceTerminalAggregateConfigError(str(exc)) from exc


def _strict_json(
    path: Path, *, name: str
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    try:
        return manifest._read_json(  # noqa: SLF001
            path, name=name, maximum_bytes=MAX_CONTROL_BYTES
        )
    except manifest.WorksetManifestError as exc:
        raise SourceTerminalAggregateIntegrityError(str(exc)) from exc


def _strict_entries(directory: Path, *, name: str) -> tuple[Path, ...]:
    try:
        return manifest._strict_entries(directory, name=name)  # noqa: SLF001
    except manifest.WorksetManifestError as exc:
        raise SourceTerminalAggregateIntegrityError(str(exc)) from exc


def _publish(
    path: Path, raw: bytes, *, root: Path, name: str
) -> registry.ArtifactSnapshot:
    try:
        return drain._publish_once_durable(path, raw, root=root, name=name)  # noqa: SLF001
    except drain.EpochDrainError as exc:
        raise SourceTerminalAggregateIntegrityError(str(exc)) from exc


def _reference(snapshot: registry.ArtifactSnapshot, root: Path) -> dict[str, object]:
    try:
        relative = snapshot.path.relative_to(root).as_posix()
    except ValueError as exc:
        raise SourceTerminalAggregateIntegrityError(
            "Referenced artifact escaped its root"
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
            name="source-terminal aggregate implementation",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
    except registry.EpochRegistryError as exc:
        raise SourceTerminalAggregateIntegrityError(str(exc)) from exc
    return {
        "path": IMPLEMENTATION_LOGICAL_PATH,
        "sha256": snapshot.sha256,
        "size_bytes": snapshot.size_bytes,
    }


def load_source_terminal_profile(
    path: Path = DEFAULT_CONFIG_PATH, *, project_root: Path = ROOT
) -> dict[str, Any]:
    root = project_root.resolve()
    resolved = path if path.is_absolute() else root / path
    if root != ROOT.resolve() or resolved.resolve() != DEFAULT_CONFIG_PATH.resolve():
        raise SourceTerminalAggregateConfigError(
            "Only the reviewed default source-terminal profile is accepted"
        )
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            resolved,
            name="source-terminal aggregate profile",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
        profile = registry._decode_json(  # noqa: SLF001
            snapshot.raw, name="source-terminal aggregate profile"
        )
    except registry.EpochRegistryError as exc:
        raise SourceTerminalAggregateConfigError(str(exc)) from exc
    if snapshot.sha256 != DEFAULT_CONFIG_SHA256:
        raise SourceTerminalAggregateConfigError(
            "Source-terminal aggregate profile SHA-256 changed"
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
        name="source-terminal aggregate profile",
    )
    if (
        profile["schema_version"] != "ootang_epoch_source_terminal_aggregate_profile_v1"
        or profile["profile_id"] != "ootang-epoch-source-terminal-aggregate-v1"
        or profile["case"] != "ootang"
        or profile["formal_warning_output"] is not False
        or profile["default_pipeline_member"] is not False
        or profile["upstream"] != EXPECTED_UPSTREAM
        or profile["runtime"] != EXPECTED_RUNTIME
        or profile["protocol"] != EXPECTED_PROTOCOL
        or profile["engineering_capabilities"] != _claims()
    ):
        raise SourceTerminalAggregateConfigError(
            "Source-terminal aggregate profile semantics changed"
        )
    for binding in EXPECTED_UPSTREAM.values():
        upstream = _child(root, binding["path"], name="frozen aggregate upstream")
        try:
            checked = registry._read_regular(  # noqa: SLF001
                upstream,
                name="frozen aggregate upstream",
                maximum_bytes=64 * 1024 * 1024,
            )
        except registry.EpochRegistryError as exc:
            raise SourceTerminalAggregateConfigError(str(exc)) from exc
        if checked.sha256 != binding["expected_sha256"]:
            raise SourceTerminalAggregateConfigError(
                f"Frozen aggregate upstream changed:{binding['path']}"
            )
    profile["_profile_sha256"] = snapshot.sha256
    return profile


def source_terminal_paths(
    profile: Mapping[str, Any],
    *,
    registry_root: Path | None = None,
    active_root: Path | None = None,
    shadow_root: Path | None = None,
) -> SourceTerminalAggregatePaths:
    registry_path = (
        registry_root or ROOT / profile["runtime"]["registry_root"]
    ).resolve()
    active = (active_root or ROOT / profile["runtime"]["active_root"]).resolve()
    shadow = (shadow_root or ROOT / profile["runtime"]["shadow_root"]).resolve()
    overlay_profile = overlay.load_overlay_profile()
    overlay_paths = overlay.overlay_paths(
        overlay_profile,
        registry_root=registry_path,
        active_root=active,
        shadow_root=shadow,
    )
    if (
        overlay_paths.recovery.root.name != profile["runtime"]["recovery_namespace"]
        or overlay_paths.root.name != profile["runtime"]["overlay_namespace"]
    ):
        raise SourceTerminalAggregateConfigError(
            "Source-terminal upstream namespaces changed"
        )
    root = _child(
        overlay_paths.recovery.root,
        profile["runtime"]["namespace"],
        name="source-terminal aggregate root",
    )
    return SourceTerminalAggregatePaths(
        registry_root=registry_path,
        root=root,
        proofs=_child(root, profile["runtime"]["proofs"], name="terminal proofs"),
        events=_child(root, profile["runtime"]["events"], name="terminal events"),
        status=_child(root, profile["runtime"]["status"], name="terminal status"),
        manager_lock=overlay_paths.manager_lock,
        active_root=active,
        shadow_root=shadow,
        cycle_lock=overlay_paths.cycle_lock,
        replay_lock=overlay_paths.replay_lock,
        shadow_lock=overlay_paths.shadow_lock,
        overlay=overlay_paths,
    )


def _load_terminal_authority(
    paths: SourceTerminalAggregatePaths, now: datetime
) -> SourceTerminalAggregateAuthority | None:
    """Deep-verify all frozen upstreams and normalize completed overlay rows."""

    try:
        upstream = overlay._load_overlay_authority(paths.overlay, now)  # noqa: SLF001
        if upstream is None:
            return None
        overlay_profile = overlay.load_overlay_profile()
        state = overlay._load_overlay_state(  # noqa: SLF001
            overlay_profile, paths.overlay, upstream
        )
    except overlay.OverlayError as exc:
        raise SourceTerminalAggregateIntegrityError(
            f"Settlement overlay validation failed:{type(exc).__name__}:{exc}"
        ) from exc

    sidecar_by_slot = {str(row[0]["slot_id"]): row for row in upstream.sidecar_events}
    completed: list[SourceTerminalSlot] = []
    for event_payload in state.events:
        sequence = event_payload.get("sequence_id")
        entry = event_payload.get("entry_sha256")
        slot_id = event_payload.get("slot_id")
        if (
            not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or not isinstance(entry, str)
            or not isinstance(slot_id, str)
        ):
            raise SourceTerminalAggregateIntegrityError(
                "Completed overlay event identity changed"
            )
        event_path = paths.overlay.events / f"{sequence:020d}-{entry}.json"
        checked_event, event_snapshot = _strict_json(
            event_path, name="completed settlement overlay event"
        )
        if checked_event != event_payload:
            raise SourceTerminalAggregateIntegrityError(
                "Completed overlay event changed during aggregate replay"
            )
        intent = state.intents.get(slot_id)
        receipt = state.receipts.get(slot_id)
        stored = upstream.sidecar_objects.get(slot_id)
        sidecar_row = sidecar_by_slot.get(slot_id)
        if intent is None or receipt is None or stored is None or sidecar_row is None:
            raise SourceTerminalAggregateIntegrityError(
                "Completed overlay event lost its upstream authority"
            )
        completed.append(
            SourceTerminalSlot(
                overlay_event=event_payload,
                overlay_event_snapshot=event_snapshot,
                overlay_intent=intent[0],
                overlay_intent_snapshot=intent[1],
                overlay_receipt=receipt[0],
                overlay_receipt_snapshot=receipt[1],
                sidecar_event=sidecar_row[0],
                sidecar_event_snapshot=sidecar_row[1],
                sidecar_reservation=stored[0],
                sidecar_reservation_snapshot=stored[1],
                candidate=stored[2],
            )
        )
    incomplete = bool(
        state.pending_intent_slot_id
        or state.pending_receipt_slot_id
        or len(state.events) < len(upstream.sidecar_events)
        or upstream.orphaned_sidecar_slot_ids
    )
    return SourceTerminalAggregateAuthority(
        overlay_profile=overlay_profile,
        overlay_authority=upstream,
        overlay_state=state,
        completed_slots=tuple(completed),
        incomplete_overlay_authority=incomplete,
    )


def _source_terminal_derivation(
    authority: SourceTerminalAggregateAuthority,
    slot: SourceTerminalSlot,
) -> tuple[dict[str, object], Mapping[str, Any], Mapping[str, Any]]:
    recovered = authority.overlay_authority.recovery
    candidate = slot.candidate
    source_item = candidate.source_item
    dependency_item = candidate.dependency_item
    source_key = str(source_item.get("key_id"))
    dependency_key = str(dependency_item.get("key_id"))
    source_rows = recovered.chains.get(source_key, ())
    dependency_rows = recovered.chains.get(dependency_key, ())
    if (
        not source_rows
        or source_rows[-1][0] != candidate.source_receipt
        or source_rows[-1][1] != candidate.source_receipt_snapshot
        or not dependency_rows
        or dependency_rows[-1][0] != candidate.dependency_receipt
        or dependency_rows[-1][1] != candidate.dependency_receipt_snapshot
    ):
        raise SourceTerminalAggregateIntegrityError(
            "Aggregate source/dependency receipt is not the current recovery tip"
        )
    source_event = recovered.events_by_step.get(
        str(candidate.source_receipt["step_id"])
    )
    dependency_event = recovered.events_by_step.get(
        str(candidate.dependency_receipt["step_id"])
    )
    if (
        source_event is None
        or source_event[0] != candidate.source_event
        or source_event[1] != candidate.source_event_snapshot
        or dependency_event is None
        or dependency_event[0] != candidate.dependency_event
        or dependency_event[1] != candidate.dependency_event_snapshot
    ):
        raise SourceTerminalAggregateIntegrityError(
            "Aggregate receipt tip lost its recovery event"
        )
    source_receipt = candidate.source_receipt
    dependency_receipt = candidate.dependency_receipt
    source_semantics = source_receipt.get("action_semantics")
    action_semantics = slot.overlay_receipt.get("action_semantics")
    if not isinstance(source_semantics, Mapping) or not isinstance(
        action_semantics, Mapping
    ):
        raise SourceTerminalAggregateIntegrityError(
            "Aggregate action semantics changed type"
        )
    plan = recovery._transition_plan(source_item)  # noqa: SLF001
    edges = recovery._plan_edges(plan)  # noqa: SLF001
    sidecar_source = slot.sidecar_reservation.get("source_step")
    sidecar_dependency = slot.sidecar_reservation.get("dependency")
    wrapper = slot.overlay_intent.get("action_contract")
    if (
        not isinstance(sidecar_source, Mapping)
        or not isinstance(sidecar_dependency, Mapping)
        or not isinstance(wrapper, Mapping)
    ):
        raise SourceTerminalAggregateIntegrityError(
            "Aggregate upstream binding changed type"
        )
    source_index = source_receipt.get("step_index")
    terminal_step = recovery._step_id(  # noqa: SLF001
        source_key, candidate.step_index, "outcome_batch_settled"
    )
    if (
        source_item.get("family") != "live_outstanding"
        or slot.source_key_id != source_key
        or slot.dependency_key_id != dependency_key
        or slot.sidecar_event.get("slot_id") != slot.slot_id
        or slot.sidecar_event.get("source_key_id") != source_key
        or slot.sidecar_event.get("dependency_key_id") != dependency_key
        or slot.sidecar_reservation.get("slot_id") != slot.slot_id
        or source_receipt.get("key_id") != source_key
        or source_receipt.get("action") != "anchor_result_recorded"
        or source_receipt.get("next_actions") != ["outcome_batch_settled"]
        or source_receipt.get("terminal_for_key") is not False
        or source_semantics.get("result_outcome") != "candidate_confirmed"
        or source_semantics.get("selected_next_action") != "outcome_batch_settled"
        or not isinstance(source_index, int)
        or isinstance(source_index, bool)
        or candidate.step_index != source_index + 1
        or candidate.step_id != terminal_step
        or sidecar_source.get("step_id") != terminal_step
        or sidecar_source.get("step_index") != candidate.step_index
        or sidecar_source.get("action") != "outcome_batch_settled"
        or plan.get("closure_resolved") is not True
        or "outcome_batch_settled" not in plan.get("terminal_actions", [])
        or edges.get("outcome_batch_settled") != []
        or sidecar_source.get("transition_plan_sha256") != plan.get("plan_sha256")
        or wrapper.get("recovery_transition_plan_sha256") != plan.get("plan_sha256")
        or slot.overlay_intent.get("source_step_id") != terminal_step
        or slot.overlay_intent.get("action") != "outcome_batch_settled"
        or slot.overlay_receipt.get("action") != "outcome_batch_settled"
        or slot.overlay_receipt.get("action_output_kind")
        != "live_outcome_settlement_transaction"
        or slot.overlay_receipt.get("action_output") is not None
        or slot.overlay_receipt.get("terminal_for_overlay_slot") is not True
        or "terminal_for_key" in slot.overlay_receipt
        or slot.overlay_event.get("slot_id") != slot.slot_id
        or slot.overlay_event.get("sidecar_sequence_id")
        != slot.sidecar_event.get("sequence_id")
        or sidecar_dependency.get("key_id") != dependency_key
        or dependency_receipt.get("terminal_for_key") is not True
        or dependency_receipt.get("next_actions") != []
        or action_semantics.get("schema_version")
        != "ootang_live_outcome_settlement_action_output_v1"
        or action_semantics.get("live_ledger_events_recorded") is not True
        or action_semantics.get("contiguous_exact_slice_verified") is not True
        or action_semantics.get("network_action_performed") is not False
        or action_semantics.get("event_count")
        != recovery.OUTCOME_SETTLEMENT_EVENT_COUNT
        or action_semantics.get("source_revision_id")
        != sidecar_dependency.get("source_revision_id")
        or action_semantics.get("outcome_batch_sha256")
        != sidecar_dependency.get("exact_outcome_sha256")
        or action_semantics.get("outcome_source_id")
        != sidecar_dependency.get("outcome_source_id")
        or action_semantics.get("terminal_event")
        != sidecar_dependency.get("ledger_terminal_event")
    ):
        raise SourceTerminalAggregateIntegrityError(
            "Completed overlay does not prove the source terminal transition"
        )
    effective_dependencies = wrapper.get("effective_dependency_keys")
    source_dependencies = source_item.get("dependency_keys")
    dependency_natural = dependency_item.get("natural_key")
    recovery_contract = wrapper.get("recovery_contract")
    if (
        not isinstance(source_dependencies, list)
        or effective_dependencies != [*source_dependencies, dependency_natural]
        or not isinstance(recovery_contract, Mapping)
        or wrapper.get("recovery_contract_sha256")
        != _sha256(_canonical_bytes(dict(recovery_contract)))
    ):
        raise SourceTerminalAggregateIntegrityError(
            "Aggregate effective dependency contract changed"
        )
    derivation = {
        "family": "live_outstanding",
        "source_key_id": source_key,
        "source_natural_key": source_item["natural_key"],
        "source_namespace_digest": source_item["namespace_digest"],
        "previous_action": source_receipt["action"],
        "previous_step_id": source_receipt["step_id"],
        "previous_step_index": source_index,
        "previous_recovery_v6_receipt_terminal": False,
        "action": "outcome_batch_settled",
        "step_id": terminal_step,
        "step_index": candidate.step_index,
        "transition_plan_sha256": plan["plan_sha256"],
        "closure_resolved": True,
        "terminal_action": True,
        "next_actions": [],
    }
    return derivation, wrapper, action_semantics


def _proof_payload(
    profile: Mapping[str, Any],
    paths: SourceTerminalAggregatePaths,
    authority: SourceTerminalAggregateAuthority,
    slot: SourceTerminalSlot,
) -> dict[str, object]:
    derivation, wrapper, action_semantics = _source_terminal_derivation(authority, slot)
    recovered = authority.overlay_authority.recovery
    manifest_root = recovered.reservation.paths.root
    recovery_root = paths.overlay.recovery.root
    sidecar_root = paths.overlay.sidecar.root
    overlay_root = paths.overlay.root
    candidate = slot.candidate
    sidecar_dependency = slot.sidecar_reservation["dependency"]
    return {
        "schema_version": profile["protocol"]["proof_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "slot_id": slot.slot_id,
        "source_key_id": slot.source_key_id,
        "dependency_key_id": slot.dependency_key_id,
        "authority_scope": "source_terminal_aggregate_v1_not_recovery_v6",
        "publication_condition": "matching_source_terminal_aggregate_event_required",
        "terminal_derivation": derivation,
        "recovery_authority": {
            "global_intent": _reference(recovered.global_snapshot, recovery_root),
            "manifest": _reference(
                recovered.reservation.manifest_snapshot, manifest_root
            ),
            "manifest_reservation_event": _reference(
                recovered.reservation.event_snapshot, manifest_root
            ),
            "source_previous_receipt": _reference(
                candidate.source_receipt_snapshot, recovery_root
            ),
            "source_previous_event": _reference(
                candidate.source_event_snapshot, recovery_root
            ),
            "dependency_terminal_receipt": _reference(
                candidate.dependency_receipt_snapshot, recovery_root
            ),
            "dependency_terminal_event": _reference(
                candidate.dependency_event_snapshot, recovery_root
            ),
        },
        "sidecar_authority": {
            "reservation": _reference(slot.sidecar_reservation_snapshot, sidecar_root),
            "event": _reference(slot.sidecar_event_snapshot, sidecar_root),
            "sequence_id": slot.sidecar_event["sequence_id"],
            "entry_sha256": slot.sidecar_event["entry_sha256"],
            "source_transition_plan_sha256": slot.sidecar_reservation["source_step"][
                "transition_plan_sha256"
            ],
            "dependency_natural_key": sidecar_dependency["natural_key"],
            "dependency_terminal_receipt": sidecar_dependency["terminal_receipt"],
            "dependency_terminal_event": sidecar_dependency["terminal_event"],
            "ledger_terminal_event": sidecar_dependency["ledger_terminal_event"],
        },
        "overlay_authority": {
            "intent": _reference(slot.overlay_intent_snapshot, overlay_root),
            "receipt": _reference(slot.overlay_receipt_snapshot, overlay_root),
            "event": _reference(slot.overlay_event_snapshot, overlay_root),
            "sequence_id": slot.overlay_event["sequence_id"],
            "entry_sha256": slot.overlay_event["entry_sha256"],
            "effective_dependency_keys": wrapper["effective_dependency_keys"],
            "recovery_contract_sha256": wrapper["recovery_contract_sha256"],
        },
        "settlement_action": {
            "action_output_kind": slot.overlay_receipt["action_output_kind"],
            "action_output": slot.overlay_receipt["action_output"],
            "action_semantics": dict(action_semantics),
        },
        "terminal_for_source_key": True,
        "implementation": _implementation_reference(),
        **_claims(),
    }


def _ensure_proof(
    profile: Mapping[str, Any],
    paths: SourceTerminalAggregatePaths,
    authority: SourceTerminalAggregateAuthority,
    slot: SourceTerminalSlot,
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    payload = _proof_payload(profile, paths, authority, slot)
    raw = _canonical_bytes(payload)
    digest = _sha256(raw)
    path = paths.proofs / f"{digest}.json"
    snapshot = _publish(path, raw, root=paths.root, name="source-terminal proof")
    checked, replay = _strict_json(path, name="source-terminal proof")
    if checked != payload or replay != snapshot or replay.sha256 != digest:
        raise SourceTerminalAggregateIntegrityError(
            "Source-terminal proof did not replay exactly"
        )
    return payload, snapshot


def _proof_objects(
    profile: Mapping[str, Any],
    paths: SourceTerminalAggregatePaths,
    authority: SourceTerminalAggregateAuthority,
) -> dict[str, tuple[dict[str, Any], registry.ArtifactSnapshot, SourceTerminalSlot]]:
    by_slot = {slot.slot_id: slot for slot in authority.completed_slots}
    result: dict[
        str, tuple[dict[str, Any], registry.ArtifactSnapshot, SourceTerminalSlot]
    ] = {}
    for path in _strict_entries(paths.proofs, name="source-terminal proofs"):
        matched = PROOF_NAME.fullmatch(path.name)
        if matched is None:
            raise SourceTerminalAggregateIntegrityError(
                "Source-terminal proof filename changed"
            )
        payload, snapshot = _strict_json(path, name="source-terminal proof")
        if matched.group("digest") != snapshot.sha256:
            raise SourceTerminalAggregateIntegrityError(
                "Source-terminal proof path is not content-addressed"
            )
        slot_id = payload.get("slot_id")
        slot = by_slot.get(slot_id) if isinstance(slot_id, str) else None
        if slot is None:
            raise SourceTerminalAggregateIntegrityError(
                "Source-terminal proof has no completed overlay event"
            )
        rebuilt = _proof_payload(profile, paths, authority, slot)
        if payload != rebuilt or snapshot.sha256 != _sha256(snapshot.raw):
            raise SourceTerminalAggregateIntegrityError(
                "Source-terminal proof semantics changed"
            )
        if slot_id in result:
            raise SourceTerminalAggregateIntegrityError(
                "One overlay slot has multiple source-terminal proofs"
            )
        result[slot_id] = (payload, snapshot, slot)
    return result


def _event_unsigned(
    profile: Mapping[str, Any],
    paths: SourceTerminalAggregatePaths,
    slot: SourceTerminalSlot,
    proof_snapshot: registry.ArtifactSnapshot,
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
        "slot_id": slot.slot_id,
        "source_key_id": slot.source_key_id,
        "dependency_key_id": slot.dependency_key_id,
        "overlay_sequence_id": slot.overlay_event["sequence_id"],
        "overlay_event": _reference(slot.overlay_event_snapshot, paths.overlay.root),
        "proof": _reference(proof_snapshot, paths.root),
        "terminal_for_source_key": True,
        **_claims(),
    }


def _append_event(
    profile: Mapping[str, Any],
    paths: SourceTerminalAggregatePaths,
    slot: SourceTerminalSlot,
    proof_snapshot: registry.ArtifactSnapshot,
    events: Sequence[Mapping[str, Any]],
    previous: str,
    *,
    now: datetime,
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    sequence = len(events) + 1
    if slot.overlay_event.get("sequence_id") != sequence:
        raise SourceTerminalAggregateIntegrityError(
            "Aggregate attempted to skip an overlay event slot"
        )
    body = _event_unsigned(
        profile,
        paths,
        slot,
        proof_snapshot,
        sequence_id=sequence,
        previous=previous,
        recorded_at=_utc_text(now),
    )
    payload = {**body, "entry_sha256": _sha256(_canonical_bytes(body))}
    path = paths.events / f"{sequence:020d}-{payload['entry_sha256']}.json"
    snapshot = _publish(
        path, _canonical_bytes(payload), root=paths.root, name="source-terminal event"
    )
    return payload, snapshot


def _load_state(
    profile: Mapping[str, Any],
    paths: SourceTerminalAggregatePaths,
    authority: SourceTerminalAggregateAuthority,
) -> _AggregateState:
    source_ids = [slot.source_key_id for slot in authority.completed_slots]
    if len(source_ids) != len(set(source_ids)):
        raise SourceTerminalAggregateIntegrityError(
            "One source key has multiple completed overlay slots"
        )
    proofs = _proof_objects(profile, paths, authority)
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    previous = ZERO_HASH
    for sequence, path in enumerate(
        _strict_entries(paths.events, name="source-terminal events"), 1
    ):
        matched = EVENT_NAME.fullmatch(path.name)
        if (
            matched is None
            or int(matched.group("sequence")) != sequence
            or sequence > len(authority.completed_slots)
        ):
            raise SourceTerminalAggregateIntegrityError(
                "Source-terminal event sequence/path changed"
            )
        slot = authority.completed_slots[sequence - 1]
        proof = proofs.get(slot.slot_id)
        if proof is None:
            raise SourceTerminalAggregateIntegrityError(
                "Source-terminal event is orphaned from its proof"
            )
        payload, _ = _strict_json(path, name="source-terminal event")
        body = dict(payload)
        entry = body.pop("entry_sha256", None)
        expected = _event_unsigned(
            profile,
            paths,
            slot,
            proof[1],
            sequence_id=sequence,
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
            or slot.slot_id in seen
        ):
            raise SourceTerminalAggregateIntegrityError(
                "Source-terminal event chain changed"
            )
        _parse_utc(payload.get("recorded_at_utc"), name="terminal event time")
        seen.add(slot.slot_id)
        previous = str(entry)
        events.append(payload)
    expected_prefix = {
        slot.slot_id for slot in authority.completed_slots[: len(events)]
    }
    if seen != expected_prefix:
        raise SourceTerminalAggregateIntegrityError(
            "Source-terminal events are not an overlay event prefix"
        )
    orphan_proofs = set(proofs) - seen
    next_slot_id = (
        authority.completed_slots[len(events)].slot_id
        if len(events) < len(authority.completed_slots)
        else None
    )
    if len(orphan_proofs) > 1 or (
        orphan_proofs and next(iter(orphan_proofs)) != next_slot_id
    ):
        raise SourceTerminalAggregateIntegrityError(
            "Source-terminal proof authority branched or skipped a slot"
        )
    return _AggregateState(
        proofs=proofs,
        events=tuple(events),
        previous_entry_sha256=previous,
        pending_proof_slot_id=(next(iter(orphan_proofs)) if orphan_proofs else None),
    )


def _select_candidate(
    authority: SourceTerminalAggregateAuthority,
    state: _AggregateState,
) -> SourceTerminalSlot | None:
    if len(state.events) >= len(authority.completed_slots):
        return None
    return authority.completed_slots[len(state.events)]


def _write_status(
    profile: Mapping[str, Any],
    paths: SourceTerminalAggregatePaths,
    *,
    now: datetime,
    status: str,
    reason: str,
    slot: SourceTerminalSlot | None,
    proof: Path | None,
    event: Path | None,
    terminal_for_source_key: bool,
) -> None:
    payload = {
        "schema_version": profile["protocol"]["status_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "recorded_at_utc": _utc_text(now),
        "status": status,
        "reason": reason,
        "slot_id": slot.slot_id if slot is not None else None,
        "source_key_id": slot.source_key_id if slot is not None else None,
        "dependency_key_id": slot.dependency_key_id if slot is not None else None,
        "proof_path": str(proof) if proof else None,
        "event_path": str(event) if event else None,
        "terminal_for_source_key": terminal_for_source_key,
        "cache_authority": False,
        **_claims(),
    }
    try:
        registry._atomic_cache(  # noqa: SLF001
            paths.status,
            _canonical_bytes(payload),
            root=paths.root,
            name="source-terminal aggregate status",
        )
    except registry.EpochRegistryError as exc:
        raise SourceTerminalAggregateIntegrityError(str(exc)) from exc


def _authority_children_present(paths: SourceTerminalAggregatePaths) -> bool:
    return bool(
        _strict_entries(paths.proofs, name="source-terminal proofs")
        or _strict_entries(paths.events, name="source-terminal events")
    )


def _result(
    paths: SourceTerminalAggregatePaths,
    status: str,
    reason: str,
    slot: SourceTerminalSlot | None = None,
    proof: Path | None = None,
    event: Path | None = None,
    *,
    terminal_for_source_key: bool = False,
) -> SourceTerminalAggregateResult:
    return SourceTerminalAggregateResult(
        status=status,
        reason=reason,
        status_path=paths.status,
        slot_id=slot.slot_id if slot is not None else None,
        source_key_id=slot.source_key_id if slot is not None else None,
        dependency_key_id=slot.dependency_key_id if slot is not None else None,
        proof_path=proof,
        event_path=event,
        terminal_for_source_key=terminal_for_source_key,
    )


def _coordinate_epoch_source_terminal_aggregate(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    registry_root: Path | None = None,
    active_root: Path | None = None,
    shadow_root: Path | None = None,
    clock: Callable[[], datetime] | None = None,
    load_authority: LoadAuthority | None = None,
) -> SourceTerminalAggregateResult:
    profile = load_source_terminal_profile(config_path)
    paths = source_terminal_paths(
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
                raise SourceTerminalAggregateBusyError(str(exc)) from exc
            except drain.EpochDrainError as exc:
                raise SourceTerminalAggregateIntegrityError(str(exc)) from exc
        acquired = True
        authority = (load_authority or _load_terminal_authority)(paths, now)
        if authority is None:
            if (
                _authority_children_present(paths)
                or overlay._authority_children_present(paths.overlay)  # noqa: SLF001
                or sidecar._sidecar_children_present(  # noqa: SLF001
                    paths.overlay.sidecar
                )
            ):
                raise SourceTerminalAggregateIntegrityError(
                    "Aggregate or overlay authority exists without complete recovery authority"
                )
            reason = "complete recovery, sidecar, and overlay authority is unavailable"
            _write_status(
                profile,
                paths,
                now=now,
                status="waiting_for_complete_upstream_authority",
                reason=reason,
                slot=None,
                proof=None,
                event=None,
                terminal_for_source_key=False,
            )
            return _result(paths, "waiting_for_complete_upstream_authority", reason)
        state = _load_state(profile, paths, authority)
        by_slot = {slot.slot_id: slot for slot in authority.completed_slots}
        if state.pending_proof_slot_id is not None:
            slot = by_slot[state.pending_proof_slot_id]
            proof_snapshot = state.proofs[slot.slot_id][1]
            _, event_snapshot = _append_event(
                profile,
                paths,
                slot,
                proof_snapshot,
                state.events,
                state.previous_entry_sha256,
                now=now,
            )
            reason = (
                "one exact source-terminal proof was forward-adopted into the "
                "aggregate event chain"
            )
            _write_status(
                profile,
                paths,
                now=now,
                status="source_terminal_event_forward_adopted",
                reason=reason,
                slot=slot,
                proof=proof_snapshot.path,
                event=event_snapshot.path,
                terminal_for_source_key=True,
            )
            return _result(
                paths,
                "source_terminal_event_forward_adopted",
                reason,
                slot,
                proof_snapshot.path,
                event_snapshot.path,
                terminal_for_source_key=True,
            )
        slot = _select_candidate(authority, state)
        if slot is not None:
            _, proof_snapshot = _ensure_proof(profile, paths, authority, slot)
            _, event_snapshot = _append_event(
                profile,
                paths,
                slot,
                proof_snapshot,
                state.events,
                state.previous_entry_sha256,
                now=now,
            )
            reason = (
                "one completed overlay event was aggregated into an independent "
                "source-key terminal proof"
            )
            _write_status(
                profile,
                paths,
                now=now,
                status="source_key_terminal_aggregated",
                reason=reason,
                slot=slot,
                proof=proof_snapshot.path,
                event=event_snapshot.path,
                terminal_for_source_key=True,
            )
            return _result(
                paths,
                "source_key_terminal_aggregated",
                reason,
                slot,
                proof_snapshot.path,
                event_snapshot.path,
                terminal_for_source_key=True,
            )
        reason = "no unconsumed completed settlement-overlay event is available"
        status = "waiting_for_completed_overlay_event"
        _write_status(
            profile,
            paths,
            now=now,
            status=status,
            reason=reason,
            slot=None,
            proof=None,
            event=None,
            terminal_for_source_key=False,
        )
        return _result(paths, status, reason)
    except SourceTerminalAggregateBusyError:
        raise
    except SourceTerminalAggregateError as exc:
        if acquired:
            try:
                _write_status(
                    profile,
                    paths,
                    now=now,
                    status="blocked_integrity",
                    reason=f"{type(exc).__name__}:{exc}",
                    slot=None,
                    proof=None,
                    event=None,
                    terminal_for_source_key=False,
                )
            except SourceTerminalAggregateError:
                pass
        raise
    finally:
        try:
            drain._release_locks(handles)  # noqa: SLF001
        except drain.EpochDrainError as exc:
            if sys.exc_info()[0] is None:
                raise SourceTerminalAggregateIntegrityError(str(exc)) from exc


def coordinate_epoch_source_terminal_aggregate(
    *, config_path: Path = DEFAULT_CONFIG_PATH
) -> SourceTerminalAggregateResult:
    """Run one reviewed machine-only source-terminal aggregate poll."""

    return _coordinate_epoch_source_terminal_aggregate(config_path=config_path)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = coordinate_epoch_source_terminal_aggregate(config_path=args.config)
    except SourceTerminalAggregateError as exc:
        print(
            json.dumps(
                {"status": "error", "error": f"{type(exc).__name__}:{exc}"},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1
    print(
        json.dumps(
            {
                "status": result.status,
                "reason": result.reason,
                "status_path": str(result.status_path),
                "slot_id": result.slot_id,
                "source_key_id": result.source_key_id,
                "proof_path": str(result.proof_path) if result.proof_path else None,
                "event_path": str(result.event_path) if result.event_path else None,
                "terminal_for_source_key": result.terminal_for_source_key,
                "original_recovery_key_terminal": False,
                "full_workset_terminal": False,
                "terminal_transition_closure_implemented": False,
                "derived_future_work_reservation_implemented": False,
                "lifecycle_authority": False,
                "old_epoch_drained": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
