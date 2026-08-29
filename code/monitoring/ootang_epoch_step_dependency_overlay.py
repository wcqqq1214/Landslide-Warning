"""Consume step-dependency sidecar events through an isolated settlement overlay.

The overlay never writes recovery-v6 or step-dependency-sidecar authority.  Under
the same four coordinator locks it deep-verifies both immutable chains, then
consumes at most one sidecar event-prefix slot into its own create-only
intent/receipt and append-only event chain.  An overlay receipt is terminal only
for that overlay slot; it is deliberately not a terminal recovery-v6 receipt.
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
from monitoring import ootang_epoch_step_dependency_reservation as sidecar  # noqa: E402
from monitoring import ootang_epoch_workset_manifest as manifest  # noqa: E402
from monitoring import ootang_epoch_workset_recovery as recovery  # noqa: E402


DEFAULT_CONFIG_PATH = ROOT / "config" / "ootang_epoch_step_dependency_overlay.v1.json"
DEFAULT_CONFIG_SHA256 = (
    "4da333ef6233059aedb6ff1cd52bb6de31bae18aa14f1e571e97ee3018f52496"
)
IMPLEMENTATION_LOGICAL_PATH = "code/monitoring/ootang_epoch_step_dependency_overlay.py"
MAX_CONTROL_BYTES = 4 * 1024 * 1024
ZERO_HASH = "0" * 64
LOCK_ORDER = recovery.LOCK_ORDER
SLOT_NAME = re.compile(r"^(?P<slot>[0-9a-f]{64})\.json$")
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
}
EXPECTED_RUNTIME = {
    "registry_root": "runtime/ootang_epoch_registry_v1",
    "recovery_namespace": "workset_recovery_v1",
    "step_dependency_namespace": "step_dependencies_v1",
    "namespace": "step_dependency_overlay_v1",
    "intents": "intents",
    "receipts": "receipts",
    "events": "events",
    "status": "status.json",
    "manager_lock": "manager.lock",
    "active_root": "runtime/ootang_prequential_live_v1",
    "shadow_root": "runtime/ootang_prequential_calibration_shadow_v1",
}
EXPECTED_PROTOCOL = {
    "intent_schema_version": "ootang_epoch_step_dependency_overlay_intent_v1",
    "receipt_schema_version": "ootang_epoch_step_dependency_overlay_receipt_v1",
    "event_schema_version": "ootang_epoch_step_dependency_overlay_event_v1",
    "status_schema_version": "ootang_epoch_step_dependency_overlay_status_v1",
    "event_type": "epoch_step_dependency_overlay_completed",
    "sidecar_event_type": "epoch_step_dependency_reserved",
    "action": "outcome_batch_settled",
    "canonical_json": "utf8_sort_keys_compact_no_nan_trailing_lf",
    "initial_previous_entry_sha256": ZERO_HASH,
    "maximum_items": 4096,
    "maximum_control_bytes": MAX_CONTROL_BYTES,
    "poll_policy": (
        "replay_or_forward_adopt_or_consume_at_most_one_sidecar_event_prefix_slot"
    ),
}
TRUE_CAPABILITIES = (
    "machine_only",
    "recovery_v6_authority_deep_verified",
    "step_dependency_sidecar_authority_deep_verified",
    "sidecar_event_prefix_order_enforced",
    "create_only_overlay_intents_implemented",
    "create_only_overlay_receipts_implemented",
    "append_only_overlay_events_implemented",
    "intent_crash_replay_implemented",
    "receipt_event_forward_adoption_implemented",
    "cross_freeze_settlement_overlay_implemented",
    "one_ready_slot_per_poll",
)
FALSE_CLAIMS = (
    "bounded_workset_recovery_implemented",
    "all_reserved_items_settled",
    "all_reserved_successors_supported",
    "terminal_transition_closure_implemented",
    "derived_future_work_reservation_implemented",
    "all_transition_branches_supported",
    "network_recovery_implemented",
    "lifecycle_authority",
    "transition_authority",
    "drained_eligibility_current",
    "old_epoch_drained",
    "active_epoch_switch_implemented",
    "automatic_epoch_rotation_implemented",
    "original_recovery_key_terminal",
    "recovery_v6_namespace_mutated",
    "step_dependency_sidecar_mutated",
    "new_settlement_transaction_created",
    "trusted_anchor_receipt_verified",
    "e2_live_evidence_eligible",
    "real_activation_ready",
    "formal_warning_output",
)


class OverlayError(RuntimeError):
    """Base overlay error."""


class OverlayConfigError(OverlayError):
    """The reviewed overlay profile or a pinned upstream changed."""


class OverlayIntegrityError(OverlayError):
    """Persisted overlay or upstream authority failed closed."""


class OverlayBusyError(OverlayError):
    """A surviving coordinator writer owns one of the four locks."""


@dataclass(frozen=True)
class OverlayPaths:
    registry_root: Path
    root: Path
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
    sidecar: sidecar.StepDependencyPaths


@dataclass(frozen=True)
class OverlayResult:
    status: str
    reason: str
    status_path: Path
    slot_id: str | None = None
    source_key_id: str | None = None
    dependency_key_id: str | None = None
    intent_path: Path | None = None
    receipt_path: Path | None = None
    event_path: Path | None = None
    terminal_transition_closure_implemented: bool = False
    derived_future_work_reservation_implemented: bool = False
    lifecycle_authority: bool = False
    old_epoch_drained: bool = False
    original_recovery_key_terminal: bool = False


@dataclass(frozen=True)
class OverlayAuthority:
    """Deep-verified recovery and sidecar authority captured under four locks."""

    recovery: sidecar.StepDependencyAuthority
    sidecar_profile: Mapping[str, Any]
    sidecar_objects: Mapping[
        str,
        tuple[dict[str, Any], registry.ArtifactSnapshot, Any],
    ]
    sidecar_events: tuple[tuple[dict[str, Any], registry.ArtifactSnapshot], ...]
    orphaned_sidecar_slot_ids: frozenset[str]


LoadAuthority = Callable[[OverlayPaths, datetime], OverlayAuthority | None]


@dataclass(frozen=True)
class _Slot:
    sidecar_event: Mapping[str, Any]
    sidecar_event_snapshot: registry.ArtifactSnapshot
    reservation_payload: Mapping[str, Any]
    reservation_snapshot: registry.ArtifactSnapshot
    candidate: Any

    @property
    def slot_id(self) -> str:
        return str(self.sidecar_event["slot_id"])


@dataclass(frozen=True)
class _OverlayState:
    intents: Mapping[str, tuple[dict[str, Any], registry.ArtifactSnapshot]]
    receipts: Mapping[str, tuple[dict[str, Any], registry.ArtifactSnapshot]]
    events: tuple[dict[str, Any], ...]
    previous_entry_sha256: str
    pending_intent_slot_id: str | None
    pending_receipt_slot_id: str | None


def _canonical_bytes(value: object) -> bytes:
    try:
        return registry._canonical_bytes(value)  # type: ignore[arg-type]  # noqa: SLF001
    except (TypeError, ValueError) as exc:
        raise OverlayIntegrityError("Value is not canonical JSON") from exc


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _claims() -> dict[str, bool]:
    return {
        **{name: True for name in TRUE_CAPABILITIES},
        **{name: False for name in FALSE_CLAIMS},
    }


def _exact(value: object, keys: set[str], *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise OverlayIntegrityError(f"{name} keys changed")
    return value


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise OverlayIntegrityError("Overlay clock must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: object, *, name: str) -> datetime:
    try:
        return registry._utc_timestamp(value, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise OverlayIntegrityError(str(exc)) from exc


def _child(root: Path, relative: str, *, name: str) -> Path:
    try:
        return registry._contained(root, relative, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise OverlayConfigError(str(exc)) from exc


def _strict_json(
    path: Path, *, name: str
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    try:
        return manifest._read_json(  # noqa: SLF001
            path, name=name, maximum_bytes=MAX_CONTROL_BYTES
        )
    except manifest.WorksetManifestError as exc:
        raise OverlayIntegrityError(str(exc)) from exc


def _strict_entries(directory: Path, *, name: str) -> tuple[Path, ...]:
    try:
        return manifest._strict_entries(directory, name=name)  # noqa: SLF001
    except manifest.WorksetManifestError as exc:
        raise OverlayIntegrityError(str(exc)) from exc


def _publish(
    path: Path, raw: bytes, *, root: Path, name: str
) -> registry.ArtifactSnapshot:
    try:
        return drain._publish_once_durable(path, raw, root=root, name=name)  # noqa: SLF001
    except drain.EpochDrainError as exc:
        raise OverlayIntegrityError(str(exc)) from exc


def _reference(snapshot: registry.ArtifactSnapshot, root: Path) -> dict[str, object]:
    try:
        relative = snapshot.path.relative_to(root).as_posix()
    except ValueError as exc:
        raise OverlayIntegrityError("Referenced artifact escaped its root") from exc
    return {
        "path": relative,
        "sha256": snapshot.sha256,
        "size_bytes": snapshot.size_bytes,
    }


def _implementation_reference() -> dict[str, object]:
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            Path(__file__),
            name="step-dependency overlay implementation",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
    except registry.EpochRegistryError as exc:
        raise OverlayIntegrityError(str(exc)) from exc
    return {
        "path": IMPLEMENTATION_LOGICAL_PATH,
        "sha256": snapshot.sha256,
        "size_bytes": snapshot.size_bytes,
    }


def load_overlay_profile(
    path: Path = DEFAULT_CONFIG_PATH, *, project_root: Path = ROOT
) -> dict[str, Any]:
    root = project_root.resolve()
    resolved = path if path.is_absolute() else root / path
    if root != ROOT.resolve() or resolved.resolve() != DEFAULT_CONFIG_PATH.resolve():
        raise OverlayConfigError(
            "Only the reviewed default overlay profile is accepted"
        )
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            resolved,
            name="step-dependency overlay profile",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
        profile = registry._decode_json(  # noqa: SLF001
            snapshot.raw, name="step-dependency overlay profile"
        )
    except registry.EpochRegistryError as exc:
        raise OverlayConfigError(str(exc)) from exc
    if snapshot.sha256 != DEFAULT_CONFIG_SHA256:
        raise OverlayConfigError("Step-dependency overlay profile SHA-256 changed")
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
        name="step-dependency overlay profile",
    )
    if (
        profile["schema_version"] != "ootang_epoch_step_dependency_overlay_profile_v1"
        or profile["profile_id"] != "ootang-epoch-step-dependency-overlay-v1"
        or profile["case"] != "ootang"
        or profile["formal_warning_output"] is not False
        or profile["default_pipeline_member"] is not False
        or profile["upstream"] != EXPECTED_UPSTREAM
        or profile["runtime"] != EXPECTED_RUNTIME
        or profile["protocol"] != EXPECTED_PROTOCOL
        or profile["engineering_capabilities"] != _claims()
    ):
        raise OverlayConfigError("Step-dependency overlay profile semantics changed")
    for binding in EXPECTED_UPSTREAM.values():
        upstream = _child(root, binding["path"], name="frozen overlay upstream")
        try:
            checked = registry._read_regular(  # noqa: SLF001
                upstream,
                name="frozen overlay upstream",
                maximum_bytes=64 * 1024 * 1024,
            )
        except registry.EpochRegistryError as exc:
            raise OverlayConfigError(str(exc)) from exc
        if checked.sha256 != binding["expected_sha256"]:
            raise OverlayConfigError(
                f"Frozen overlay upstream changed:{binding['path']}"
            )
    profile["_profile_sha256"] = snapshot.sha256
    return profile


def overlay_paths(
    profile: Mapping[str, Any],
    *,
    registry_root: Path | None = None,
    active_root: Path | None = None,
    shadow_root: Path | None = None,
) -> OverlayPaths:
    registry_path = (
        registry_root or ROOT / profile["runtime"]["registry_root"]
    ).resolve()
    active = (active_root or ROOT / profile["runtime"]["active_root"]).resolve()
    shadow = (shadow_root or ROOT / profile["runtime"]["shadow_root"]).resolve()
    sidecar_profile = sidecar.load_step_dependency_profile()
    sidecar_paths = sidecar.step_dependency_paths(
        sidecar_profile,
        registry_root=registry_path,
        active_root=active,
        shadow_root=shadow,
    )
    if (
        sidecar_paths.recovery.root.name != profile["runtime"]["recovery_namespace"]
        or sidecar_paths.root.name != profile["runtime"]["step_dependency_namespace"]
    ):
        raise OverlayConfigError("Overlay upstream namespaces changed")
    root = _child(
        sidecar_paths.recovery.root,
        profile["runtime"]["namespace"],
        name="step-dependency overlay root",
    )
    return OverlayPaths(
        registry_root=registry_path,
        root=root,
        intents=_child(root, profile["runtime"]["intents"], name="overlay intents"),
        receipts=_child(root, profile["runtime"]["receipts"], name="overlay receipts"),
        events=_child(root, profile["runtime"]["events"], name="overlay events"),
        status=_child(root, profile["runtime"]["status"], name="overlay status"),
        manager_lock=sidecar_paths.manager_lock,
        active_root=active,
        shadow_root=shadow,
        cycle_lock=sidecar_paths.cycle_lock,
        replay_lock=sidecar_paths.replay_lock,
        shadow_lock=sidecar_paths.shadow_lock,
        recovery=sidecar_paths.recovery,
        sidecar=sidecar_paths,
    )


def _load_overlay_authority(
    paths: OverlayPaths, now: datetime
) -> OverlayAuthority | None:
    """Deep-verify recovery v6 and the full sidecar object/event state."""

    try:
        recovered = sidecar._load_recovery_authority(paths.sidecar, now)  # noqa: SLF001
        if recovered is None:
            return None
        sidecar_profile = sidecar.load_step_dependency_profile()
        objects = sidecar._reservation_objects(  # noqa: SLF001
            sidecar_profile, paths.sidecar, recovered
        )
        payloads, _, seen = sidecar._replay_events(  # noqa: SLF001
            sidecar_profile, paths.sidecar, objects
        )
    except sidecar.StepDependencyError as exc:
        raise OverlayIntegrityError(
            f"Step-dependency authority validation failed:{type(exc).__name__}:{exc}"
        ) from exc
    events: list[tuple[dict[str, Any], registry.ArtifactSnapshot]] = []
    for payload in payloads:
        event_path = paths.sidecar.events / (
            f"{payload['sequence_id']:020d}-{payload['entry_sha256']}.json"
        )
        checked, snapshot = _strict_json(
            event_path, name="step-dependency sidecar event authority"
        )
        if checked != payload:
            raise OverlayIntegrityError("Sidecar event changed during overlay replay")
        events.append((payload, snapshot))
    return OverlayAuthority(
        recovery=recovered,
        sidecar_profile=sidecar_profile,
        sidecar_objects=objects,
        sidecar_events=tuple(events),
        orphaned_sidecar_slot_ids=frozenset(set(objects) - seen),
    )


def _slot_for_event(
    authority: OverlayAuthority,
    event_row: tuple[dict[str, Any], registry.ArtifactSnapshot],
) -> _Slot:
    payload, snapshot = event_row
    slot_id = payload.get("slot_id")
    stored = (
        authority.sidecar_objects.get(slot_id) if isinstance(slot_id, str) else None
    )
    if stored is None:
        raise OverlayIntegrityError("Sidecar event lost its reservation object")
    reservation_payload, reservation_snapshot, candidate = stored
    return _Slot(
        payload,
        snapshot,
        reservation_payload,
        reservation_snapshot,
        candidate,
    )


def _effective_source_item(slot: _Slot) -> dict[str, Any]:
    dependency = slot.reservation_payload.get("dependency")
    if not isinstance(dependency, Mapping):
        raise OverlayIntegrityError("Sidecar reservation lost its dependency")
    natural_key = dependency.get("natural_key")
    source = dict(slot.candidate.source_item)
    dependencies = source.get("dependency_keys")
    if (
        not isinstance(natural_key, str)
        or not natural_key
        or not isinstance(dependencies, list)
        or natural_key in dependencies
    ):
        raise OverlayIntegrityError(
            "Overlay dependency edge is not one new natural key"
        )
    source["dependency_keys"] = [*dependencies, natural_key]
    return source


def _cross_checked_contract(
    authority: OverlayAuthority, slot: _Slot
) -> tuple[dict[str, object], dict[str, Any]]:
    source_record = slot.reservation_payload.get("source_step")
    dependency_record = slot.reservation_payload.get("dependency")
    if not isinstance(source_record, Mapping) or not isinstance(
        dependency_record, Mapping
    ):
        raise OverlayIntegrityError("Sidecar slot binding changed type")
    effective_item = _effective_source_item(slot)
    try:
        base = recovery._outcome_settlement_adoption_contract(  # noqa: SLF001
            effective_item,
            authority.recovery.reservation,
            slot.candidate.source_receipt,
        )
    except recovery.WorksetRecoveryError as exc:
        raise OverlayIntegrityError(
            f"Settlement overlay contract failed:{type(exc).__name__}:{exc}"
        ) from exc
    binding = {
        "source_revision_id": dependency_record.get("source_revision_id"),
        "outcome_batch_sha256": dependency_record.get("exact_outcome_sha256"),
        "outcome_source_id": dependency_record.get("outcome_source_id"),
        "target_date": dependency_record.get("target_date"),
        "issue_id": dependency_record.get("issue_id"),
        "sealed_entry_sha256": dependency_record.get("sealed_entry_sha256"),
        "terminal_event": dependency_record.get("ledger_terminal_event"),
    }
    _verify_recovery_contract_binding(base, source_record, dependency_record, binding)
    sidecar_root = slot.sidecar_event_snapshot.path.parent.parent
    wrapper = {
        "schema_version": "ootang_epoch_step_dependency_overlay_contract_v1",
        "slot_id": slot.slot_id,
        "action": "outcome_batch_settled",
        "effective_dependency_keys": effective_item["dependency_keys"],
        "recovery_transition_plan_sha256": recovery._transition_plan(  # noqa: SLF001
            effective_item
        )["plan_sha256"],
        "sidecar_binding": binding,
        "sidecar_reservation": _reference(slot.reservation_snapshot, sidecar_root),
        "sidecar_event": _reference(slot.sidecar_event_snapshot, sidecar_root),
        "source_previous_receipt": source_record.get("previous_receipt"),
        "source_previous_event": source_record.get("previous_event"),
        "dependency_terminal_receipt": dependency_record.get("terminal_receipt"),
        "dependency_terminal_event": dependency_record.get("terminal_event"),
        "recovery_contract": base,
        "recovery_contract_sha256": _sha256(_canonical_bytes(base)),
    }
    return wrapper, effective_item


def _verify_recovery_contract_binding(
    base: Mapping[str, Any],
    source_record: Mapping[str, Any],
    dependency_record: Mapping[str, Any],
    binding: Mapping[str, Any],
) -> None:
    outcome_dependency = base.get("outcome_dependency")
    if not isinstance(outcome_dependency, Mapping):
        raise OverlayIntegrityError("Recovery settlement contract lost its dependency")
    record_type = outcome_dependency.get("record_type")
    if record_type == "outcome_receipt_chain":
        optional_source_binding_valid = (
            outcome_dependency.get("outcome_batch_sha256")
            == binding["outcome_batch_sha256"]
            and outcome_dependency.get("outcome_source_id") is None
        )
    elif record_type == "machine_selected_source_outcome":
        optional_source_binding_valid = (
            outcome_dependency.get("outcome_batch_sha256") is None
            and outcome_dependency.get("outcome_source_id")
            == binding["outcome_source_id"]
        )
    else:
        optional_source_binding_valid = False
    if (
        not optional_source_binding_valid
        or base.get("schema_version")
        != recovery.OUTCOME_SETTLEMENT_ADOPTION_CONTRACT_SCHEMA
        or base.get("source_revision_id") != binding["source_revision_id"]
        or base.get("outcome_batch_sha256") != binding["outcome_batch_sha256"]
        or base.get("outcome_source_id") != binding["outcome_source_id"]
        or base.get("target_date") != binding["target_date"]
        or base.get("issue_id") != binding["issue_id"]
        or base.get("sealed_entry_sha256") != binding["sealed_entry_sha256"]
        or base.get("terminal_event") != binding["terminal_event"]
        or source_record.get("target_date") != binding["target_date"]
        or source_record.get("issue_id") != binding["issue_id"]
        or source_record.get("sealed_entry_sha256") != binding["sealed_entry_sha256"]
        or outcome_dependency.get("natural_key") != dependency_record.get("natural_key")
        or outcome_dependency.get("namespace_digest")
        != dependency_record.get("namespace_digest")
        or outcome_dependency.get("source_revision_id") != binding["source_revision_id"]
        or outcome_dependency.get("target_date") != binding["target_date"]
        or outcome_dependency.get("old_live_epoch_id")
        != dependency_record.get("old_live_epoch_id")
        or base.get("confirmation_event") != source_record.get("confirmation_event")
    ):
        raise OverlayIntegrityError(
            "Recovery settlement contract disagrees with sidecar authority"
        )


def _stored_wrapper_contract(
    slot: _Slot, value: object
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Replay a durable wrapper without rerunning the settlement action."""

    wrapper = _exact(
        value,
        {
            "schema_version",
            "slot_id",
            "action",
            "effective_dependency_keys",
            "recovery_transition_plan_sha256",
            "sidecar_binding",
            "sidecar_reservation",
            "sidecar_event",
            "source_previous_receipt",
            "source_previous_event",
            "dependency_terminal_receipt",
            "dependency_terminal_event",
            "recovery_contract",
            "recovery_contract_sha256",
        },
        name="overlay settlement wrapper contract",
    )
    source_record = slot.reservation_payload.get("source_step")
    dependency_record = slot.reservation_payload.get("dependency")
    base = wrapper.get("recovery_contract")
    binding = wrapper.get("sidecar_binding")
    if (
        not isinstance(source_record, Mapping)
        or not isinstance(dependency_record, Mapping)
        or not isinstance(base, Mapping)
        or not isinstance(binding, Mapping)
    ):
        raise OverlayIntegrityError("Stored overlay wrapper changed type")
    effective_item = _effective_source_item(slot)
    sidecar_root = slot.sidecar_event_snapshot.path.parent.parent
    if (
        wrapper.get("schema_version")
        != "ootang_epoch_step_dependency_overlay_contract_v1"
        or wrapper.get("slot_id") != slot.slot_id
        or wrapper.get("action") != "outcome_batch_settled"
        or wrapper.get("effective_dependency_keys") != effective_item["dependency_keys"]
        or wrapper.get("recovery_transition_plan_sha256")
        != recovery._transition_plan(effective_item)["plan_sha256"]  # noqa: SLF001
        or wrapper.get("sidecar_reservation")
        != _reference(slot.reservation_snapshot, sidecar_root)
        or wrapper.get("sidecar_event")
        != _reference(slot.sidecar_event_snapshot, sidecar_root)
        or wrapper.get("source_previous_receipt")
        != source_record.get("previous_receipt")
        or wrapper.get("source_previous_event") != source_record.get("previous_event")
        or wrapper.get("dependency_terminal_receipt")
        != dependency_record.get("terminal_receipt")
        or wrapper.get("dependency_terminal_event")
        != dependency_record.get("terminal_event")
        or wrapper.get("recovery_contract_sha256")
        != _sha256(_canonical_bytes(dict(base)))
    ):
        raise OverlayIntegrityError("Stored overlay wrapper binding changed")
    expected_binding = {
        "source_revision_id": dependency_record.get("source_revision_id"),
        "outcome_batch_sha256": dependency_record.get("exact_outcome_sha256"),
        "outcome_source_id": dependency_record.get("outcome_source_id"),
        "target_date": dependency_record.get("target_date"),
        "issue_id": dependency_record.get("issue_id"),
        "sealed_entry_sha256": dependency_record.get("sealed_entry_sha256"),
        "terminal_event": dependency_record.get("ledger_terminal_event"),
    }
    if dict(binding) != expected_binding:
        raise OverlayIntegrityError("Stored overlay sidecar binding changed")
    _verify_recovery_contract_binding(
        base, source_record, dependency_record, expected_binding
    )
    return wrapper, effective_item


def _recorded_action_for_contract(wrapper: Mapping[str, Any]) -> recovery.ActionOutput:
    """Rebuild deterministic action output from a previously verified contract."""

    base = wrapper.get("recovery_contract")
    if not isinstance(base, Mapping):
        raise OverlayIntegrityError("Overlay wrapper lost its recovery contract")
    expected_pre_head = base.get("expected_pre_head")
    confirmation = base.get("confirmation_event")
    if not isinstance(expected_pre_head, Mapping) or not isinstance(
        confirmation, Mapping
    ):
        raise OverlayIntegrityError("Recorded settlement contract changed type")
    return recovery.ActionOutput(
        "live_outcome_settlement_transaction",
        None,
        {
            "schema_version": "ootang_live_outcome_settlement_action_output_v1",
            "live_epoch_id": expected_pre_head.get("epoch_id"),
            "target_date": base.get("target_date"),
            "issue_id": base.get("issue_id"),
            "sealed_entry_sha256": base.get("sealed_entry_sha256"),
            "confirmation_entry_sha256": confirmation.get("entry_sha256"),
            "outcome_dependency": base.get("outcome_dependency"),
            "outcome_batch_sha256": base.get("outcome_batch_sha256"),
            "outcome_source_id": base.get("outcome_source_id"),
            "source_revision_id": base.get("source_revision_id"),
            "outcome_input_manifest_sha256": base.get("outcome_input_manifest_sha256"),
            "event_count": base.get("event_count"),
            "ordered_entries_sha256": base.get("ordered_entries_sha256"),
            "first_event": base.get("first_event"),
            "terminal_event": base.get("terminal_event"),
            "state_before_sha256": base.get("state_before_sha256"),
            "state_after_sha256": base.get("state_after_sha256"),
            "engineering_blind_time_order_candidate": base.get(
                "engineering_blind_time_order_candidate"
            ),
            "live_ledger_events_recorded": True,
            "contiguous_exact_slice_verified": True,
            "network_action_performed": False,
            "trusted_anchor_receipt_verified": False,
            "e2_live_evidence_eligible": False,
            "formal_warning_output": False,
        },
    )


def _action_for_contract(
    authority: OverlayAuthority,
    slot: _Slot,
    wrapper: Mapping[str, Any],
) -> recovery.ActionOutput:
    rebuilt, effective_item = _cross_checked_contract(authority, slot)
    if dict(wrapper) != rebuilt:
        raise OverlayIntegrityError("Overlay settlement wrapper contract changed")
    contract = wrapper.get("recovery_contract")
    if not isinstance(contract, Mapping):
        raise OverlayIntegrityError("Overlay wrapper lost its recovery contract")
    try:
        return recovery._outcome_settlement_adoption_action(  # noqa: SLF001
            effective_item,
            authority.recovery.reservation,
            contract,
            slot.candidate.source_receipt,
        )
    except recovery.WorksetRecoveryError as exc:
        raise OverlayIntegrityError(
            f"Settlement overlay replay failed:{type(exc).__name__}:{exc}"
        ) from exc


def _intent_stable(
    profile: Mapping[str, Any],
    paths: OverlayPaths,
    authority: OverlayAuthority,
    slot: _Slot,
    action_contract: Mapping[str, Any],
) -> dict[str, object]:
    return {
        "schema_version": profile["protocol"]["intent_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "slot_id": slot.slot_id,
        "sidecar_sequence_id": slot.sidecar_event["sequence_id"],
        "source_key_id": slot.sidecar_event["source_key_id"],
        "dependency_key_id": slot.sidecar_event["dependency_key_id"],
        "action": profile["protocol"]["action"],
        "source_step_id": slot.reservation_payload["source_step"]["step_id"],
        "sidecar_reservation": _reference(
            slot.reservation_snapshot, paths.sidecar.root
        ),
        "sidecar_event": _reference(slot.sidecar_event_snapshot, paths.sidecar.root),
        "recovery_global_intent": _reference(
            authority.recovery.global_snapshot, paths.recovery.root
        ),
        "action_contract": dict(action_contract),
        "adapter_provenance": _implementation_reference(),
        **_claims(),
    }


def _ensure_intent(
    profile: Mapping[str, Any],
    paths: OverlayPaths,
    authority: OverlayAuthority,
    slot: _Slot,
    action_contract: Mapping[str, Any],
    *,
    now: datetime,
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    """Create or exactly replay one per-slot overlay intent."""

    path = paths.intents / f"{slot.slot_id}.json"
    stable = _intent_stable(profile, paths, authority, slot, action_contract)
    if path.exists():
        payload, snapshot = _strict_json(path, name="step-dependency overlay intent")
        if set(payload) != {*stable, "created_at_utc"} or {
            **payload,
            "created_at_utc": "",
        } != {**stable, "created_at_utc": ""}:
            raise OverlayIntegrityError("Step-dependency overlay intent changed")
        _parse_utc(payload["created_at_utc"], name="overlay intent time")
        return payload, snapshot
    payload = {**stable, "created_at_utc": _utc_text(now)}
    snapshot = _publish(
        path, _canonical_bytes(payload), root=paths.root, name="overlay intent"
    )
    return payload, snapshot


def _receipt_stable(
    profile: Mapping[str, Any],
    paths: OverlayPaths,
    slot: _Slot,
    intent_snapshot: registry.ArtifactSnapshot,
    action: recovery.ActionOutput,
) -> dict[str, object]:
    return {
        "schema_version": profile["protocol"]["receipt_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "slot_id": slot.slot_id,
        "sidecar_sequence_id": slot.sidecar_event["sequence_id"],
        "source_key_id": slot.sidecar_event["source_key_id"],
        "dependency_key_id": slot.sidecar_event["dependency_key_id"],
        "action": profile["protocol"]["action"],
        "intent": _reference(intent_snapshot, paths.root),
        "sidecar_reservation": _reference(
            slot.reservation_snapshot, paths.sidecar.root
        ),
        "sidecar_event": _reference(slot.sidecar_event_snapshot, paths.sidecar.root),
        "action_output_kind": action.kind,
        "action_output": action.reference,
        "action_semantics": dict(action.semantics),
        "terminal_for_overlay_slot": True,
        **_claims(),
    }


def _ensure_receipt(
    profile: Mapping[str, Any],
    paths: OverlayPaths,
    slot: _Slot,
    intent_snapshot: registry.ArtifactSnapshot,
    action: recovery.ActionOutput,
    *,
    now: datetime,
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    """Create or exactly replay one per-slot, overlay-only terminal receipt."""

    path = paths.receipts / f"{slot.slot_id}.json"
    stable = _receipt_stable(profile, paths, slot, intent_snapshot, action)
    if path.exists():
        payload, snapshot = _strict_json(path, name="step-dependency overlay receipt")
        if set(payload) != {*stable, "completed_at_utc"} or {
            **payload,
            "completed_at_utc": "",
        } != {**stable, "completed_at_utc": ""}:
            raise OverlayIntegrityError("Step-dependency overlay receipt changed")
        _parse_utc(payload["completed_at_utc"], name="overlay receipt time")
        return payload, snapshot
    payload = {**stable, "completed_at_utc": _utc_text(now)}
    snapshot = _publish(
        path, _canonical_bytes(payload), root=paths.root, name="overlay receipt"
    )
    return payload, snapshot


def _event_unsigned(
    profile: Mapping[str, Any],
    paths: OverlayPaths,
    slot: _Slot,
    receipt_snapshot: registry.ArtifactSnapshot,
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
        "sidecar_sequence_id": slot.sidecar_event["sequence_id"],
        "source_key_id": slot.sidecar_event["source_key_id"],
        "dependency_key_id": slot.sidecar_event["dependency_key_id"],
        "sidecar_event": _reference(slot.sidecar_event_snapshot, paths.sidecar.root),
        "receipt": _reference(receipt_snapshot, paths.root),
        **_claims(),
    }


def _append_event(
    profile: Mapping[str, Any],
    paths: OverlayPaths,
    slot: _Slot,
    receipt_snapshot: registry.ArtifactSnapshot,
    events: Sequence[Mapping[str, Any]],
    previous: str,
    *,
    now: datetime,
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    """Append one receipt-backed overlay event in sidecar-prefix order."""

    sequence = len(events) + 1
    if slot.sidecar_event.get("sequence_id") != sequence:
        raise OverlayIntegrityError("Overlay attempted to skip a sidecar event slot")
    body = _event_unsigned(
        profile,
        paths,
        slot,
        receipt_snapshot,
        sequence_id=sequence,
        previous=previous,
        recorded_at=_utc_text(now),
    )
    payload = {**body, "entry_sha256": _sha256(_canonical_bytes(body))}
    path = paths.events / f"{sequence:020d}-{payload['entry_sha256']}.json"
    snapshot = _publish(
        path, _canonical_bytes(payload), root=paths.root, name="overlay event"
    )
    return payload, snapshot


def _named_slot_records(
    directory: Path, *, name: str
) -> dict[str, tuple[dict[str, Any], registry.ArtifactSnapshot]]:
    result: dict[str, tuple[dict[str, Any], registry.ArtifactSnapshot]] = {}
    for path in _strict_entries(directory, name=name):
        matched = SLOT_NAME.fullmatch(path.name)
        if matched is None:
            raise OverlayIntegrityError(f"{name} filename changed")
        slot_id = matched.group("slot")
        if slot_id in result:
            raise OverlayIntegrityError(f"{name} branched")
        result[slot_id] = _strict_json(path, name=name)
    return result


def _verify_intent(
    profile: Mapping[str, Any],
    paths: OverlayPaths,
    authority: OverlayAuthority,
    slot: _Slot,
    payload: Mapping[str, Any],
    snapshot: registry.ArtifactSnapshot,
) -> Mapping[str, Any]:
    wrapper, _ = _cross_checked_contract(authority, slot)
    stable = _intent_stable(profile, paths, authority, slot, wrapper)
    if (
        set(payload) != {*stable, "created_at_utc"}
        or {**dict(payload), "created_at_utc": ""} != {**stable, "created_at_utc": ""}
        or snapshot.path.name != f"{slot.slot_id}.json"
    ):
        raise OverlayIntegrityError("Overlay intent failed immutable replay")
    _parse_utc(payload.get("created_at_utc"), name="overlay intent time")
    return wrapper


def _verify_receipt(
    profile: Mapping[str, Any],
    paths: OverlayPaths,
    authority: OverlayAuthority,
    slot: _Slot,
    intent_payload: Mapping[str, Any],
    intent_snapshot: registry.ArtifactSnapshot,
    payload: Mapping[str, Any],
    snapshot: registry.ArtifactSnapshot,
) -> None:
    del authority
    wrapper = intent_payload.get("action_contract")
    if not isinstance(wrapper, Mapping):
        raise OverlayIntegrityError("Overlay intent lost its wrapper contract")
    _stored_wrapper_contract(slot, wrapper)
    action = _recorded_action_for_contract(wrapper)
    stable = _receipt_stable(profile, paths, slot, intent_snapshot, action)
    if (
        set(payload) != {*stable, "completed_at_utc"}
        or {**dict(payload), "completed_at_utc": ""}
        != {**stable, "completed_at_utc": ""}
        or payload.get("terminal_for_overlay_slot") is not True
        or "terminal_for_key" in payload
        or snapshot.path.name != f"{slot.slot_id}.json"
    ):
        raise OverlayIntegrityError("Overlay receipt failed immutable replay")
    _parse_utc(payload.get("completed_at_utc"), name="overlay receipt time")


def _load_overlay_state(
    profile: Mapping[str, Any],
    paths: OverlayPaths,
    authority: OverlayAuthority,
) -> _OverlayState:
    intents = _named_slot_records(paths.intents, name="overlay intents")
    receipts = _named_slot_records(paths.receipts, name="overlay receipts")
    sidecar_slots = [str(row[0]["slot_id"]) for row in authority.sidecar_events]
    if len(set(sidecar_slots)) != len(sidecar_slots):
        raise OverlayIntegrityError("Sidecar event prefix repeats a slot")
    slot_by_id = {
        slot_id: _slot_for_event(authority, row)
        for slot_id, row in zip(sidecar_slots, authority.sidecar_events, strict=True)
    }
    if not set(receipts) <= set(intents):
        raise OverlayIntegrityError("Overlay receipt is orphaned from its intent")
    if not set(intents) <= set(sidecar_slots):
        raise OverlayIntegrityError("Overlay intent is orphaned from sidecar events")
    for slot_id, (payload, snapshot) in intents.items():
        _verify_intent(
            profile, paths, authority, slot_by_id[slot_id], payload, snapshot
        )
    for slot_id, (payload, snapshot) in receipts.items():
        intent_payload, intent_snapshot = intents[slot_id]
        _verify_receipt(
            profile,
            paths,
            authority,
            slot_by_id[slot_id],
            intent_payload,
            intent_snapshot,
            payload,
            snapshot,
        )

    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    previous = ZERO_HASH
    for sequence, path in enumerate(
        _strict_entries(paths.events, name="overlay events"), 1
    ):
        matched = EVENT_NAME.fullmatch(path.name)
        if (
            matched is None
            or int(matched.group("sequence")) != sequence
            or sequence > len(authority.sidecar_events)
        ):
            raise OverlayIntegrityError("Overlay event sequence/path changed")
        payload, _ = _strict_json(path, name="overlay event")
        expected_slot_id = sidecar_slots[sequence - 1]
        receipt = receipts.get(expected_slot_id)
        if receipt is None:
            raise OverlayIntegrityError("Overlay event is orphaned from its receipt")
        slot = slot_by_id[expected_slot_id]
        body = dict(payload)
        entry = body.pop("entry_sha256", None)
        expected = _event_unsigned(
            profile,
            paths,
            slot,
            receipt[1],
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
            or payload.get("slot_id") != expected_slot_id
            or payload.get("previous_entry_sha256") != previous
            or entry != _sha256(_canonical_bytes(body))
            or matched.group("entry") != entry
            or expected_slot_id in seen
        ):
            raise OverlayIntegrityError("Overlay event chain changed")
        _parse_utc(payload.get("recorded_at_utc"), name="overlay event time")
        seen.add(expected_slot_id)
        previous = str(entry)
        events.append(payload)

    expected_prefix = set(sidecar_slots[: len(events)])
    if seen != expected_prefix:
        raise OverlayIntegrityError("Overlay events are not a sidecar event prefix")
    extra_receipts = set(receipts) - seen
    extra_intents = set(intents) - set(receipts)
    next_slot_id = (
        sidecar_slots[len(events)] if len(events) < len(sidecar_slots) else None
    )
    if (
        len(extra_receipts) > 1
        or len(extra_intents) > 1
        or (extra_receipts and extra_intents)
        or (extra_receipts and next(iter(extra_receipts)) != next_slot_id)
        or (extra_intents and next(iter(extra_intents)) != next_slot_id)
        or set(intents) - seen - extra_receipts - extra_intents
    ):
        raise OverlayIntegrityError(
            "Overlay pending authority branched or skipped a slot"
        )
    return _OverlayState(
        intents=intents,
        receipts=receipts,
        events=tuple(events),
        previous_entry_sha256=previous,
        pending_intent_slot_id=(next(iter(extra_intents)) if extra_intents else None),
        pending_receipt_slot_id=(
            next(iter(extra_receipts)) if extra_receipts else None
        ),
    )


def _write_status(
    profile: Mapping[str, Any],
    paths: OverlayPaths,
    *,
    now: datetime,
    status: str,
    reason: str,
    slot: _Slot | None,
    intent: Path | None,
    receipt: Path | None,
    event: Path | None,
) -> None:
    payload = {
        "schema_version": profile["protocol"]["status_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "recorded_at_utc": _utc_text(now),
        "status": status,
        "reason": reason,
        "slot_id": slot.slot_id if slot is not None else None,
        "source_key_id": (
            slot.sidecar_event["source_key_id"] if slot is not None else None
        ),
        "dependency_key_id": (
            slot.sidecar_event["dependency_key_id"] if slot is not None else None
        ),
        "intent_path": str(intent) if intent else None,
        "receipt_path": str(receipt) if receipt else None,
        "event_path": str(event) if event else None,
        "cache_authority": False,
        **_claims(),
    }
    try:
        registry._atomic_cache(  # noqa: SLF001
            paths.status,
            _canonical_bytes(payload),
            root=paths.root,
            name="step-dependency overlay status",
        )
    except registry.EpochRegistryError as exc:
        raise OverlayIntegrityError(str(exc)) from exc


def _authority_children_present(paths: OverlayPaths) -> bool:
    return bool(
        _strict_entries(paths.intents, name="overlay intents")
        or _strict_entries(paths.receipts, name="overlay receipts")
        or _strict_entries(paths.events, name="overlay events")
    )


def _result(
    paths: OverlayPaths,
    status: str,
    reason: str,
    slot: _Slot | None = None,
    intent: Path | None = None,
    receipt: Path | None = None,
    event: Path | None = None,
) -> OverlayResult:
    return OverlayResult(
        status=status,
        reason=reason,
        status_path=paths.status,
        slot_id=slot.slot_id if slot is not None else None,
        source_key_id=(
            str(slot.sidecar_event["source_key_id"]) if slot is not None else None
        ),
        dependency_key_id=(
            str(slot.sidecar_event["dependency_key_id"]) if slot is not None else None
        ),
        intent_path=intent,
        receipt_path=receipt,
        event_path=event,
    )


def _complete_slot(
    profile: Mapping[str, Any],
    paths: OverlayPaths,
    authority: OverlayAuthority,
    state: _OverlayState,
    slot: _Slot,
    *,
    now: datetime,
) -> OverlayResult:
    wrapper, _ = _cross_checked_contract(authority, slot)
    intent_payload, intent_snapshot = _ensure_intent(
        profile, paths, authority, slot, wrapper, now=now
    )
    stored_wrapper = intent_payload.get("action_contract")
    if not isinstance(stored_wrapper, Mapping):
        raise OverlayIntegrityError("Overlay intent lost its action contract")
    action = _action_for_contract(authority, slot, stored_wrapper)
    _, receipt_snapshot = _ensure_receipt(
        profile, paths, slot, intent_snapshot, action, now=now
    )
    _, event_snapshot = _append_event(
        profile,
        paths,
        slot,
        receipt_snapshot,
        state.events,
        state.previous_entry_sha256,
        now=now,
    )
    reason = (
        "one sidecar event-prefix slot was replayed into an overlay-only "
        "settlement adoption receipt"
    )
    _write_status(
        profile,
        paths,
        now=now,
        status="overlay_slot_completed",
        reason=reason,
        slot=slot,
        intent=intent_snapshot.path,
        receipt=receipt_snapshot.path,
        event=event_snapshot.path,
    )
    return _result(
        paths,
        "overlay_slot_completed",
        reason,
        slot,
        intent_snapshot.path,
        receipt_snapshot.path,
        event_snapshot.path,
    )


def _coordinate_epoch_step_dependency_overlay(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    registry_root: Path | None = None,
    active_root: Path | None = None,
    shadow_root: Path | None = None,
    clock: Callable[[], datetime] | None = None,
    load_authority: LoadAuthority | None = None,
) -> OverlayResult:
    profile = load_overlay_profile(config_path)
    paths = overlay_paths(
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
                raise OverlayBusyError(str(exc)) from exc
            except drain.EpochDrainError as exc:
                raise OverlayIntegrityError(str(exc)) from exc
        acquired = True
        authority = (load_authority or _load_overlay_authority)(paths, now)
        if authority is None:
            if _authority_children_present(paths) or sidecar._sidecar_children_present(  # noqa: SLF001
                paths.sidecar
            ):
                raise OverlayIntegrityError(
                    "Overlay or sidecar authority exists without complete recovery authority"
                )
            reason = "complete recovery-v6 and step-dependency authority is unavailable"
            _write_status(
                profile,
                paths,
                now=now,
                status="waiting_for_complete_upstream_authority",
                reason=reason,
                slot=None,
                intent=None,
                receipt=None,
                event=None,
            )
            return _result(paths, "waiting_for_complete_upstream_authority", reason)
        state = _load_overlay_state(profile, paths, authority)
        sidecar_slot_rows = {
            str(row[0]["slot_id"]): row for row in authority.sidecar_events
        }
        if state.pending_receipt_slot_id is not None:
            slot = _slot_for_event(
                authority, sidecar_slot_rows[state.pending_receipt_slot_id]
            )
            intent_snapshot = state.intents[slot.slot_id][1]
            receipt_snapshot = state.receipts[slot.slot_id][1]
            _, event_snapshot = _append_event(
                profile,
                paths,
                slot,
                receipt_snapshot,
                state.events,
                state.previous_entry_sha256,
                now=now,
            )
            reason = (
                "one exact overlay receipt was forward-adopted into the "
                "append-only event chain"
            )
            _write_status(
                profile,
                paths,
                now=now,
                status="overlay_event_forward_adopted",
                reason=reason,
                slot=slot,
                intent=intent_snapshot.path,
                receipt=receipt_snapshot.path,
                event=event_snapshot.path,
            )
            return _result(
                paths,
                "overlay_event_forward_adopted",
                reason,
                slot,
                intent_snapshot.path,
                receipt_snapshot.path,
                event_snapshot.path,
            )
        if state.pending_intent_slot_id is not None:
            slot = _slot_for_event(
                authority, sidecar_slot_rows[state.pending_intent_slot_id]
            )
            return _complete_slot(profile, paths, authority, state, slot, now=now)
        if len(state.events) < len(authority.sidecar_events):
            slot = _slot_for_event(
                authority, authority.sidecar_events[len(state.events)]
            )
            return _complete_slot(profile, paths, authority, state, slot, now=now)
        if authority.orphaned_sidecar_slot_ids:
            reason = (
                "one create-only sidecar reservation object is waiting for its "
                "sidecar event publisher; overlay did not adopt it"
            )
            _write_status(
                profile,
                paths,
                now=now,
                status="waiting_for_sidecar_event",
                reason=reason,
                slot=None,
                intent=None,
                receipt=None,
                event=None,
            )
            return _result(paths, "waiting_for_sidecar_event", reason)
        reason = "no unconsumed step-dependency sidecar event-prefix slot is available"
        _write_status(
            profile,
            paths,
            now=now,
            status="waiting_for_sidecar_reservation",
            reason=reason,
            slot=None,
            intent=None,
            receipt=None,
            event=None,
        )
        return _result(paths, "waiting_for_sidecar_reservation", reason)
    except OverlayBusyError:
        raise
    except OverlayError as exc:
        if acquired:
            try:
                _write_status(
                    profile,
                    paths,
                    now=now,
                    status="blocked_integrity",
                    reason=f"{type(exc).__name__}:{exc}",
                    slot=None,
                    intent=None,
                    receipt=None,
                    event=None,
                )
            except OverlayError:
                pass
        raise
    finally:
        try:
            drain._release_locks(handles)  # noqa: SLF001
        except drain.EpochDrainError as exc:
            if sys.exc_info()[0] is None:
                raise OverlayIntegrityError(str(exc)) from exc


def coordinate_epoch_step_dependency_overlay(
    *, config_path: Path = DEFAULT_CONFIG_PATH
) -> OverlayResult:
    """Run one reviewed machine-only overlay poll."""

    return _coordinate_epoch_step_dependency_overlay(config_path=config_path)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = coordinate_epoch_step_dependency_overlay(config_path=args.config)
    except OverlayError as exc:
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
                "intent_path": str(result.intent_path) if result.intent_path else None,
                "receipt_path": (
                    str(result.receipt_path) if result.receipt_path else None
                ),
                "event_path": str(result.event_path) if result.event_path else None,
                "terminal_transition_closure_implemented": False,
                "derived_future_work_reservation_implemented": False,
                "lifecycle_authority": False,
                "old_epoch_drained": False,
                "original_recovery_key_terminal": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
