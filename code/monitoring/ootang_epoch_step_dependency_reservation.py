"""Reserve one cross-freeze manifest-sibling step dependency.

This sidecar is intentionally read-only with respect to the persisted workset
recovery v6 namespace.  It deep-verifies that authority, then records a narrow
dependency discovered after an anchor result selected the settlement branch.
It does not execute the reserved step or claim terminal/lifecycle closure.
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
from monitoring import ootang_epoch_workset_manifest as manifest  # noqa: E402
from monitoring import ootang_epoch_workset_recovery as recovery  # noqa: E402


DEFAULT_CONFIG_PATH = (
    ROOT / "config" / "ootang_epoch_step_dependency_reservation.v1.json"
)
DEFAULT_CONFIG_SHA256 = (
    "8b10a9642610b76911813d80ee8e31205c0e3c490aa05a13f0ba8287d457670e"
)
IMPLEMENTATION_LOGICAL_PATH = (
    "code/monitoring/ootang_epoch_step_dependency_reservation.py"
)
MAX_CONTROL_BYTES = 4 * 1024 * 1024
ZERO_HASH = "0" * 64
LOCK_ORDER = recovery.LOCK_ORDER
EVENT_NAME = re.compile(r"^(?P<sequence>[0-9]{20})-(?P<entry>[0-9a-f]{64})\.json$")
OBJECT_NAME = re.compile(r"^(?P<digest>[0-9a-f]{64})\.json$")

EXPECTED_UPSTREAM = {
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
    "namespace": "step_dependencies_v1",
    "reservation_objects": "reservations/sha256",
    "events": "events",
    "status": "status.json",
    "manager_lock": "manager.lock",
    "active_root": "runtime/ootang_prequential_live_v1",
    "shadow_root": "runtime/ootang_prequential_calibration_shadow_v1",
}
EXPECTED_PROTOCOL = {
    "reservation_schema_version": "ootang_epoch_step_dependency_reservation_v1",
    "event_schema_version": "ootang_epoch_step_dependency_reservation_event_v1",
    "status_schema_version": "ootang_epoch_step_dependency_reservation_status_v1",
    "event_type": "epoch_step_dependency_reserved",
    "source_action": "anchor_result_recorded",
    "source_outcome": "candidate_confirmed",
    "reserved_successor": "outcome_batch_settled",
    "dependency_action": "outcome_or_revision_consumed",
    "accepted_dependency_writer_branches": [
        "outstanding_settlement",
        "preexisting_consumed_adoption",
    ],
    "canonical_json": "utf8_sort_keys_compact_no_nan_trailing_lf",
    "initial_previous_entry_sha256": ZERO_HASH,
    "maximum_items": 4096,
    "maximum_control_bytes": MAX_CONTROL_BYTES,
    "poll_policy": (
        "reserve_or_forward_adopt_at_most_one_ready_manifest_sibling_step_dependency"
    ),
}
TRUE_CAPABILITIES = (
    "machine_only",
    "create_only_content_addressed_reservations_implemented",
    "append_only_reservation_events_implemented",
    "crash_forward_adoption_implemented",
    "recovery_v6_authority_deep_verified",
    "cross_freeze_manifest_sibling_step_dependency_reservation_implemented",
    "one_ready_slot_per_poll",
)
FALSE_CLAIMS = (
    "bounded_workset_recovery_implemented",
    "all_reserved_items_settled",
    "all_reserved_successors_supported",
    "terminal_transition_closure_implemented",
    "derived_future_work_reservation_implemented",
    "all_transition_branches_supported",
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


class StepDependencyError(RuntimeError):
    """Base sidecar error."""


class StepDependencyConfigError(StepDependencyError):
    """The reviewed sidecar profile or frozen upstream changed."""


class StepDependencyIntegrityError(StepDependencyError):
    """A persisted authority or dependency relation failed closed."""


class StepDependencyBusyError(StepDependencyError):
    """A surviving recovery writer lock is held."""


@dataclass(frozen=True)
class StepDependencyPaths:
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
class StepDependencyResult:
    status: str
    reason: str
    status_path: Path
    reservation_path: Path | None = None
    event_path: Path | None = None
    source_key_id: str | None = None
    dependency_key_id: str | None = None
    bounded_workset_recovery_implemented: bool = False
    terminal_transition_closure_implemented: bool = False
    derived_future_work_reservation_implemented: bool = False
    lifecycle_authority: bool = False
    old_epoch_drained: bool = False


@dataclass(frozen=True)
class StepDependencyAuthority:
    recovery_profile: Mapping[str, Any]
    reservation: recovery.Reservation
    global_intent: Mapping[str, Any]
    global_snapshot: registry.ArtifactSnapshot
    ordered_items: tuple[Mapping[str, Any], ...]
    chains: Mapping[str, Sequence[tuple[Mapping[str, Any], registry.ArtifactSnapshot]]]
    events_by_step: Mapping[str, tuple[Mapping[str, Any], registry.ArtifactSnapshot]]


@dataclass(frozen=True)
class _Candidate:
    source_item: Mapping[str, Any]
    source_receipt: Mapping[str, Any]
    source_receipt_snapshot: registry.ArtifactSnapshot
    source_event: Mapping[str, Any]
    source_event_snapshot: registry.ArtifactSnapshot
    dependency_item: Mapping[str, Any]
    dependency_receipt: Mapping[str, Any]
    dependency_receipt_snapshot: registry.ArtifactSnapshot
    dependency_event: Mapping[str, Any]
    dependency_event_snapshot: registry.ArtifactSnapshot
    step_index: int
    step_id: str
    slot_id: str


LoadAuthority = Callable[
    [StepDependencyPaths, datetime], StepDependencyAuthority | None
]


def _canonical_bytes(value: object) -> bytes:
    try:
        return registry._canonical_bytes(value)  # type: ignore[arg-type]  # noqa: SLF001
    except (TypeError, ValueError) as exc:
        raise StepDependencyIntegrityError("Value is not canonical JSON") from exc


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _claims() -> dict[str, bool]:
    return {
        **{name: True for name in TRUE_CAPABILITIES},
        **{name: False for name in FALSE_CLAIMS},
    }


def _exact(value: object, keys: set[str], *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise StepDependencyIntegrityError(f"{name} keys changed")
    return value


def _hash(value: object, *, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise StepDependencyIntegrityError(f"{name} is not a lowercase SHA-256")
    return value


def _text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise StepDependencyIntegrityError(f"{name} is not canonical text")
    return value


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise StepDependencyIntegrityError("Sidecar clock must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: object, *, name: str) -> datetime:
    try:
        return registry._utc_timestamp(value, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise StepDependencyIntegrityError(str(exc)) from exc


def _child(root: Path, relative: str, *, name: str) -> Path:
    try:
        return registry._contained(root, relative, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise StepDependencyConfigError(str(exc)) from exc


def _strict_json(
    path: Path, *, name: str
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    try:
        return manifest._read_json(  # noqa: SLF001
            path, name=name, maximum_bytes=MAX_CONTROL_BYTES
        )
    except manifest.WorksetManifestError as exc:
        raise StepDependencyIntegrityError(str(exc)) from exc


def _publish(
    path: Path, raw: bytes, *, root: Path, name: str
) -> registry.ArtifactSnapshot:
    try:
        return drain._publish_once_durable(path, raw, root=root, name=name)  # noqa: SLF001
    except drain.EpochDrainError as exc:
        raise StepDependencyIntegrityError(str(exc)) from exc


def load_step_dependency_profile(
    path: Path = DEFAULT_CONFIG_PATH, *, project_root: Path = ROOT
) -> dict[str, Any]:
    root = project_root.resolve()
    resolved = path if path.is_absolute() else root / path
    if root != ROOT.resolve() or resolved.resolve() != DEFAULT_CONFIG_PATH.resolve():
        raise StepDependencyConfigError(
            "Only the reviewed default step-dependency profile is accepted"
        )
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            resolved, name="step-dependency profile", maximum_bytes=MAX_CONTROL_BYTES
        )
        profile = registry._decode_json(  # noqa: SLF001
            snapshot.raw, name="step-dependency profile"
        )
    except registry.EpochRegistryError as exc:
        raise StepDependencyConfigError(str(exc)) from exc
    if snapshot.sha256 != DEFAULT_CONFIG_SHA256:
        raise StepDependencyConfigError("Step-dependency profile SHA-256 changed")
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
        name="step-dependency profile",
    )
    if (
        profile["schema_version"]
        != "ootang_epoch_step_dependency_reservation_profile_v1"
        or profile["profile_id"] != "ootang-epoch-step-dependency-reservation-v1"
        or profile["case"] != "ootang"
        or profile["formal_warning_output"] is not False
        or profile["default_pipeline_member"] is not False
        or profile["upstream"] != EXPECTED_UPSTREAM
        or profile["runtime"] != EXPECTED_RUNTIME
        or profile["protocol"] != EXPECTED_PROTOCOL
        or profile["engineering_capabilities"] != _claims()
    ):
        raise StepDependencyConfigError("Step-dependency profile semantics changed")
    for binding in EXPECTED_UPSTREAM.values():
        upstream = _child(root, binding["path"], name="frozen recovery upstream")
        try:
            actual = registry._read_regular(  # noqa: SLF001
                upstream,
                name="frozen recovery upstream",
                maximum_bytes=64 * 1024 * 1024,
            )
        except registry.EpochRegistryError as exc:
            raise StepDependencyConfigError(str(exc)) from exc
        if actual.sha256 != binding["expected_sha256"]:
            raise StepDependencyConfigError(
                f"Frozen recovery upstream changed:{binding['path']}"
            )
    profile["_profile_sha256"] = snapshot.sha256
    return profile


def step_dependency_paths(
    profile: Mapping[str, Any],
    *,
    registry_root: Path | None = None,
    active_root: Path | None = None,
    shadow_root: Path | None = None,
) -> StepDependencyPaths:
    registry_path = (
        registry_root or ROOT / profile["runtime"]["registry_root"]
    ).resolve()
    recovery_root = _child(
        registry_path,
        profile["runtime"]["recovery_namespace"],
        name="recovery namespace",
    )
    root = _child(
        recovery_root, profile["runtime"]["namespace"], name="step-dependency root"
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
    return StepDependencyPaths(
        registry_root=registry_path,
        root=root,
        reservation_objects=_child(
            root,
            profile["runtime"]["reservation_objects"],
            name="step-dependency reservation objects",
        ),
        events=_child(
            root, profile["runtime"]["events"], name="step-dependency events"
        ),
        status=_child(
            root, profile["runtime"]["status"], name="step-dependency status"
        ),
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
        raise StepDependencyIntegrityError(
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
            name="step-dependency implementation",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
    except registry.EpochRegistryError as exc:
        raise StepDependencyIntegrityError(str(exc)) from exc
    return {
        "path": IMPLEMENTATION_LOGICAL_PATH,
        "sha256": snapshot.sha256,
        "size_bytes": snapshot.size_bytes,
    }


def _strict_entries(directory: Path, *, name: str) -> tuple[Path, ...]:
    try:
        return manifest._strict_entries(directory, name=name)  # noqa: SLF001
    except manifest.WorksetManifestError as exc:
        raise StepDependencyIntegrityError(str(exc)) from exc


def _load_recovery_authority(
    paths: StepDependencyPaths, now: datetime
) -> StepDependencyAuthority | None:
    """Deep-verify a complete recovery v6 receipt/event prefix without mutating it."""

    recovery_profile = recovery.load_workset_recovery_profile()
    reservation = recovery._load_reservation(paths.recovery)  # noqa: SLF001
    if reservation is None or not paths.recovery.global_intent.is_file():
        return None
    ordered, _, graph_digest = recovery._dag(reservation)  # noqa: SLF001
    global_intent, global_snapshot = recovery._ensure_global_intent(  # noqa: SLF001
        recovery_profile,
        paths.recovery,
        reservation,
        ordered,
        graph_digest,
        now=now,
    )
    items = {item["key_id"]: item for item in ordered}
    intents = recovery._strict_named_json(  # noqa: SLF001
        paths.recovery.item_intents,
        suffix=".json",
        name="recovery item intents",
    )
    response_links, response_objects = recovery._anchor_result_observation_step_ids(  # noqa: SLF001
        paths.recovery
    )
    observation_steps = response_links | response_objects
    if not observation_steps <= set(intents) or not response_links <= response_objects:
        raise StepDependencyIntegrityError(
            "Recovery anchor response observation namespace is orphaned"
        )
    receipts = recovery._load_receipts(  # noqa: SLF001
        recovery_profile,
        paths.recovery,
        reservation,
        global_snapshot,
        items,
        intents,
        probe_actions=True,
    )
    recovery._deep_verify_locked_anchor_result_observations(  # noqa: SLF001
        recovery_profile,
        paths.recovery,
        reservation,
        items,
        intents,
        receipts,
        observation_steps,
    )
    events, _ = recovery._load_events(  # noqa: SLF001
        recovery_profile, paths.recovery, receipts
    )
    if set(intents) != set(receipts) or len(events) != len(receipts):
        return None
    events_by_step: dict[str, tuple[Mapping[str, Any], registry.ArtifactSnapshot]] = {}
    for payload in events:
        event_path = paths.recovery.events / (
            f"{payload['sequence_id']:020d}-{payload['entry_sha256']}.json"
        )
        checked, snapshot = recovery._strict_json(  # noqa: SLF001
            event_path, name="recovery event authority"
        )
        if checked != payload:
            raise StepDependencyIntegrityError("Recovery event changed during replay")
        events_by_step[payload["step_id"]] = (payload, snapshot)
    return StepDependencyAuthority(
        recovery_profile=recovery_profile,
        reservation=reservation,
        global_intent=global_intent,
        global_snapshot=global_snapshot,
        ordered_items=tuple(ordered),
        chains=recovery._receipt_chains(receipts),  # noqa: SLF001
        events_by_step=events_by_step,
    )


def _item_authority(item: Mapping[str, Any], *, name: str) -> Mapping[str, Any]:
    authority = item.get("authority")
    if not isinstance(authority, Mapping):
        raise StepDependencyIntegrityError(f"{name} authority changed type")
    return authority


def _latest(
    authority: StepDependencyAuthority, item: Mapping[str, Any]
) -> tuple[Mapping[str, Any], registry.ArtifactSnapshot] | None:
    rows = authority.chains.get(str(item.get("key_id")), ())
    return rows[-1] if rows else None


def _source_scope(
    item: Mapping[str, Any], receipt: Mapping[str, Any]
) -> dict[str, object] | None:
    if item.get("family") != "live_outstanding":
        return None
    item_authority = _item_authority(item, name="source live item")
    semantics = receipt.get("action_semantics")
    if (
        receipt.get("action") != "anchor_result_recorded"
        or receipt.get("terminal_for_key") is not False
        or receipt.get("next_actions") != ["outcome_batch_settled"]
        or receipt.get("action_output_kind") != "live_anchor_result_event"
        or receipt.get("action_output") is not None
        or not isinstance(semantics, Mapping)
        or semantics.get("schema_version")
        != "ootang_live_anchor_result_action_output_v1"
        or semantics.get("result_outcome") != "candidate_confirmed"
        or semantics.get("event_type") != "anchor_confirmed"
        or semantics.get("selected_next_action") != "outcome_batch_settled"
        or semantics.get("live_ledger_event_recorded") is not True
        or semantics.get("trusted_anchor_receipt_verified") is not False
        or semantics.get("e2_live_evidence_eligible") is not False
    ):
        return None
    seal = item_authority.get("seal_event")
    if not isinstance(seal, Mapping):
        raise StepDependencyIntegrityError("Source live item lost its seal authority")
    scope = {
        "old_live_epoch_id": item_authority.get("old_live_epoch_id"),
        "target_date": item_authority.get("target_date"),
        "issue_id": item_authority.get("issue_id"),
        "sealed_entry_sha256": seal.get("entry_sha256"),
    }
    if (
        any(not isinstance(value, str) or not value for value in scope.values())
        or semantics.get("live_epoch_id") != scope["old_live_epoch_id"]
        or semantics.get("sealed_entry_sha256") != scope["sealed_entry_sha256"]
        or not isinstance(semantics.get("sequence_id"), int)
        or isinstance(semantics.get("sequence_id"), bool)
        or int(semantics["sequence_id"]) < 1
        or not isinstance(semantics.get("event_key"), str)
        or not semantics["event_key"]
        or _hash(semantics.get("entry_sha256"), name="confirmation entry")
        != semantics.get("entry_sha256")
        or _hash(
            semantics.get("previous_entry_sha256"),
            name="confirmation predecessor",
        )
        != semantics.get("previous_entry_sha256")
    ):
        raise StepDependencyIntegrityError(
            "Confirmed source receipt left its frozen live scope"
        )
    return scope


def _dependency_scope(
    item: Mapping[str, Any], receipt: Mapping[str, Any]
) -> dict[str, object] | None:
    if item.get("family") != "outcome_revision":
        return None
    semantics = receipt.get("action_semantics")
    if (
        receipt.get("action") != "outcome_or_revision_consumed"
        or receipt.get("terminal_for_key") is not True
        or receipt.get("next_actions") != []
        or receipt.get("action_output_kind") != "live_outcome_consumption_transaction"
        or receipt.get("action_output") is not None
        or not isinstance(semantics, Mapping)
        or semantics.get("schema_version")
        != "ootang_live_outcome_consumption_action_output_v1"
        or semantics.get("writer_branch")
        not in {"outstanding_settlement", "preexisting_consumed_adoption"}
        or semantics.get("live_ledger_events_recorded") is not True
        or semantics.get("canonical_frozen_writer_reused") is not True
        or semantics.get("trusted_anchor_receipt_verified") is not False
        or semantics.get("e2_live_evidence_eligible") is not False
        or semantics.get("formal_warning_output") is not False
    ):
        return None
    scope = {
        "old_live_epoch_id": semantics.get("live_epoch_id"),
        "target_date": semantics.get("target_date"),
        "issue_id": semantics.get("issue_id"),
        "sealed_entry_sha256": semantics.get("sealed_entry_sha256"),
    }
    if any(not isinstance(value, str) or not value for value in scope.values()):
        raise StepDependencyIntegrityError(
            "Terminal outcome receipt lost its settlement scope"
        )
    terminal_event = semantics.get("terminal_event")
    if (
        not isinstance(semantics.get("source_revision_id"), str)
        or not semantics["source_revision_id"]
        or _hash(semantics.get("exact_outcome_sha256"), name="dependency exact outcome")
        != semantics.get("exact_outcome_sha256")
        or not isinstance(semantics.get("outcome_source_id"), str)
        or not semantics["outcome_source_id"]
        or not isinstance(terminal_event, Mapping)
        or not isinstance(terminal_event.get("sequence_id"), int)
        or isinstance(terminal_event.get("sequence_id"), bool)
        or int(terminal_event["sequence_id"]) < 1
        or not isinstance(terminal_event.get("event_key"), str)
        or not terminal_event["event_key"]
        or _hash(terminal_event.get("entry_sha256"), name="dependency terminal entry")
        != terminal_event.get("entry_sha256")
    ):
        raise StepDependencyIntegrityError(
            "Terminal outcome receipt lost its immutable outcome identity"
        )
    return scope


def _candidate_from_pair(
    authority: StepDependencyAuthority,
    source_item: Mapping[str, Any],
    source_row: tuple[Mapping[str, Any], registry.ArtifactSnapshot],
    dependency_item: Mapping[str, Any],
    dependency_row: tuple[Mapping[str, Any], registry.ArtifactSnapshot],
) -> _Candidate:
    source_receipt, source_snapshot = source_row
    dependency_receipt, dependency_snapshot = dependency_row
    source_scope = _source_scope(source_item, source_receipt)
    dependency_scope = _dependency_scope(dependency_item, dependency_receipt)
    if (
        source_scope is None
        or dependency_scope is None
        or source_scope != dependency_scope
    ):
        raise StepDependencyIntegrityError(
            "Step dependency candidate left its exact settlement scope"
        )
    source_step_id = _text(source_receipt.get("step_id"), name="source step id")
    dependency_step_id = _text(
        dependency_receipt.get("step_id"), name="dependency step id"
    )
    source_event = authority.events_by_step.get(source_step_id)
    dependency_event = authority.events_by_step.get(dependency_step_id)
    if source_event is None or dependency_event is None:
        raise StepDependencyIntegrityError(
            "Step dependency receipt lacks its recovery event"
        )
    source_index = source_receipt.get("step_index")
    if not isinstance(source_index, int) or isinstance(source_index, bool):
        raise StepDependencyIntegrityError("Source step index changed type")
    step_index = source_index + 1
    source_key_id = _text(source_item.get("key_id"), name="source key id")
    dependency_key_id = _text(dependency_item.get("key_id"), name="dependency key id")
    if source_key_id == dependency_key_id:
        raise StepDependencyIntegrityError("Step dependency is self-referential")
    step_id = recovery._step_id(  # noqa: SLF001
        source_key_id, step_index, "outcome_batch_settled"
    )
    slot_id = _sha256(
        _canonical_bytes(
            {
                "manifest_sha256": authority.reservation.manifest_snapshot.sha256,
                "source_key_id": source_key_id,
                "source_step_receipt_sha256": source_snapshot.sha256,
                "step_id": step_id,
            }
        )
    )
    return _Candidate(
        source_item=source_item,
        source_receipt=source_receipt,
        source_receipt_snapshot=source_snapshot,
        source_event=source_event[0],
        source_event_snapshot=source_event[1],
        dependency_item=dependency_item,
        dependency_receipt=dependency_receipt,
        dependency_receipt_snapshot=dependency_snapshot,
        dependency_event=dependency_event[0],
        dependency_event_snapshot=dependency_event[1],
        step_index=step_index,
        step_id=step_id,
        slot_id=slot_id,
    )


def _select_candidate(
    authority: StepDependencyAuthority, reserved_slot_ids: set[str]
) -> _Candidate | None:
    """Select at most one canonical ready manifest-sibling dependency."""

    for source_item in authority.ordered_items:
        source_row = _latest(authority, source_item)
        if source_row is None or _source_scope(source_item, source_row[0]) is None:
            continue
        manifest_dependencies = source_item.get("dependency_keys")
        if not isinstance(manifest_dependencies, list):
            raise StepDependencyIntegrityError(
                "Source manifest dependency list changed type"
            )
        item_by_natural = {
            str(item.get("natural_key")): item for item in authority.ordered_items
        }
        if any(
            item_by_natural.get(str(value), {}).get("family") == "outcome_revision"
            for value in manifest_dependencies
        ):
            continue
        source_scope = _source_scope(source_item, source_row[0])
        matches: list[
            tuple[
                Mapping[str, Any],
                tuple[Mapping[str, Any], registry.ArtifactSnapshot],
            ]
        ] = []
        for dependency_item in authority.ordered_items:
            dependency_row = _latest(authority, dependency_item)
            if dependency_row is None:
                continue
            dependency_scope = _dependency_scope(dependency_item, dependency_row[0])
            if dependency_scope is not None and dependency_scope == source_scope:
                matches.append((dependency_item, dependency_row))
        if len(matches) > 1:
            raise StepDependencyIntegrityError(
                "Confirmed settlement has multiple terminal outcome siblings"
            )
        if not matches:
            continue
        candidate = _candidate_from_pair(
            authority, source_item, source_row, matches[0][0], matches[0][1]
        )
        if candidate.slot_id not in reserved_slot_ids:
            return candidate
    return None


def _reservation_payload(
    profile: Mapping[str, Any],
    authority: StepDependencyAuthority,
    candidate: _Candidate,
    *,
    reserved_at: str,
) -> dict[str, object]:
    source_authority = _item_authority(candidate.source_item, name="source live item")
    source_semantics = candidate.source_receipt["action_semantics"]
    dependency_semantics = candidate.dependency_receipt["action_semantics"]
    if not isinstance(source_semantics, Mapping) or not isinstance(
        dependency_semantics, Mapping
    ):
        raise StepDependencyIntegrityError("Candidate semantics changed type")
    seal = source_authority.get("seal_event")
    if not isinstance(seal, Mapping):
        raise StepDependencyIntegrityError("Candidate seal authority changed type")
    manifest_root = authority.reservation.paths.root
    recovery_root = authority.global_snapshot.path.parent
    return {
        "schema_version": profile["protocol"]["reservation_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "reserved_at_utc": reserved_at,
        "slot_id": candidate.slot_id,
        "reservation_policy": (
            "one_existing_frozen_manifest_outcome_sibling_terminal_receipt_"
            "authorizes_one_post_freeze_settlement_step_dependency"
        ),
        "base_authority": {
            "recovery_global_intent": _reference(
                authority.global_snapshot, recovery_root
            ),
            "manifest": _reference(
                authority.reservation.manifest_snapshot, manifest_root
            ),
            "manifest_reservation_event": _reference(
                authority.reservation.event_snapshot, manifest_root
            ),
        },
        "source_step": {
            "key_id": candidate.source_item["key_id"],
            "natural_key": candidate.source_item["natural_key"],
            "namespace_digest": candidate.source_item["namespace_digest"],
            "step_id": candidate.step_id,
            "step_index": candidate.step_index,
            "action": "outcome_batch_settled",
            "transition_plan_sha256": recovery._transition_plan(  # noqa: SLF001
                candidate.source_item
            )["plan_sha256"],
            "previous_receipt": _reference(
                candidate.source_receipt_snapshot, recovery_root
            ),
            "previous_event": _reference(
                candidate.source_event_snapshot, recovery_root
            ),
            "confirmation_event": {
                "sequence_id": source_semantics.get("sequence_id"),
                "event_key": source_semantics.get("event_key"),
                "entry_sha256": source_semantics.get("entry_sha256"),
            },
            "old_live_epoch_id": source_authority.get("old_live_epoch_id"),
            "target_date": source_authority.get("target_date"),
            "issue_id": source_authority.get("issue_id"),
            "sealed_entry_sha256": seal.get("entry_sha256"),
        },
        "dependency": {
            "key_id": candidate.dependency_item["key_id"],
            "natural_key": candidate.dependency_item["natural_key"],
            "namespace_digest": candidate.dependency_item["namespace_digest"],
            "action": "outcome_or_revision_consumed",
            "terminal_receipt": _reference(
                candidate.dependency_receipt_snapshot, recovery_root
            ),
            "terminal_event": _reference(
                candidate.dependency_event_snapshot, recovery_root
            ),
            "terminal_for_key": True,
            "writer_branch": dependency_semantics.get("writer_branch"),
            "old_live_epoch_id": dependency_semantics.get("live_epoch_id"),
            "target_date": dependency_semantics.get("target_date"),
            "issue_id": dependency_semantics.get("issue_id"),
            "sealed_entry_sha256": dependency_semantics.get("sealed_entry_sha256"),
            "source_revision_id": dependency_semantics.get("source_revision_id"),
            "exact_outcome_sha256": dependency_semantics.get("exact_outcome_sha256"),
            "outcome_source_id": dependency_semantics.get("outcome_source_id"),
            "ledger_terminal_event": dependency_semantics.get("terminal_event"),
        },
        "dependency_policy": (
            "only_deep_verified_terminal_step_receipts_satisfy_step_dependencies"
        ),
        "implementation": _implementation_reference(),
        **_claims(),
    }


def _publish_reservation(
    profile: Mapping[str, Any],
    paths: StepDependencyPaths,
    authority: StepDependencyAuthority,
    candidate: _Candidate,
    *,
    now: datetime,
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    payload = _reservation_payload(
        profile, authority, candidate, reserved_at=_utc_text(now)
    )
    raw = _canonical_bytes(payload)
    digest = _sha256(raw)
    path = paths.reservation_objects / f"{digest}.json"
    snapshot = _publish(
        path,
        raw,
        root=paths.root,
        name="step-dependency reservation object",
    )
    checked, replay = _strict_json(path, name="step-dependency reservation object")
    if checked != payload or replay != snapshot or replay.sha256 != digest:
        raise StepDependencyIntegrityError(
            "Step-dependency reservation did not replay exactly"
        )
    return payload, snapshot


def _candidate_for_stored_payload(
    authority: StepDependencyAuthority, payload: Mapping[str, Any]
) -> _Candidate:
    source_record = payload.get("source_step")
    dependency_record = payload.get("dependency")
    if not isinstance(source_record, Mapping) or not isinstance(
        dependency_record, Mapping
    ):
        raise StepDependencyIntegrityError(
            "Stored reservation lost its source/dependency records"
        )
    by_id = {str(item.get("key_id")): item for item in authority.ordered_items}
    source_item = by_id.get(str(source_record.get("key_id")))
    dependency_item = by_id.get(str(dependency_record.get("key_id")))
    if source_item is None or dependency_item is None:
        raise StepDependencyIntegrityError(
            "Stored reservation references an unknown manifest sibling"
        )
    source_rows = authority.chains.get(str(source_item["key_id"]), ())
    dependency_rows = authority.chains.get(str(dependency_item["key_id"]), ())
    source_previous = source_record.get("previous_receipt")
    dependency_terminal = dependency_record.get("terminal_receipt")
    source_match = [
        row
        for row in source_rows
        if isinstance(source_previous, Mapping)
        and source_previous.get("sha256") == row[1].sha256
        and source_previous.get("size_bytes") == row[1].size_bytes
    ]
    dependency_match = [
        row
        for row in dependency_rows
        if isinstance(dependency_terminal, Mapping)
        and dependency_terminal.get("sha256") == row[1].sha256
        and dependency_terminal.get("size_bytes") == row[1].size_bytes
    ]
    if len(source_match) != 1 or len(dependency_match) != 1:
        raise StepDependencyIntegrityError(
            "Stored reservation receipt authority is missing or ambiguous"
        )
    return _candidate_from_pair(
        authority,
        source_item,
        source_match[0],
        dependency_item,
        dependency_match[0],
    )


def _verify_reservation(
    profile: Mapping[str, Any],
    authority: StepDependencyAuthority,
    payload: Mapping[str, Any],
    snapshot: registry.ArtifactSnapshot,
) -> _Candidate:
    expected_keys = {
        "schema_version",
        "profile_id",
        "profile_sha256",
        "reserved_at_utc",
        "slot_id",
        "reservation_policy",
        "base_authority",
        "source_step",
        "dependency",
        "dependency_policy",
        "implementation",
        *TRUE_CAPABILITIES,
        *FALSE_CLAIMS,
    }
    _exact(dict(payload), expected_keys, name="step-dependency reservation")
    candidate = _candidate_for_stored_payload(authority, payload)
    rebuilt = _reservation_payload(profile, authority, candidate, reserved_at="")
    comparable = {**dict(payload), "reserved_at_utc": ""}
    if (
        comparable != rebuilt
        or payload.get("slot_id") != candidate.slot_id
        or any(payload.get(name) is not True for name in TRUE_CAPABILITIES)
        or any(payload.get(name) is not False for name in FALSE_CLAIMS)
    ):
        raise StepDependencyIntegrityError(
            "Step-dependency reservation semantics changed"
        )
    _parse_utc(payload.get("reserved_at_utc"), name="reservation time")
    if snapshot.sha256 != _sha256(snapshot.raw):
        raise StepDependencyIntegrityError("Reservation object digest changed")
    return candidate


def _reservation_objects(
    profile: Mapping[str, Any],
    paths: StepDependencyPaths,
    authority: StepDependencyAuthority,
) -> dict[str, tuple[dict[str, Any], registry.ArtifactSnapshot, _Candidate]]:
    result: dict[str, tuple[dict[str, Any], registry.ArtifactSnapshot, _Candidate]] = {}
    for path in _strict_entries(
        paths.reservation_objects, name="step-dependency reservation objects"
    ):
        matched = OBJECT_NAME.fullmatch(path.name)
        if matched is None:
            raise StepDependencyIntegrityError(
                "Step-dependency reservation filename changed"
            )
        payload, snapshot = _strict_json(
            path, name="step-dependency reservation object"
        )
        if matched.group("digest") != snapshot.sha256:
            raise StepDependencyIntegrityError(
                "Step-dependency reservation path is not content-addressed"
            )
        candidate = _verify_reservation(profile, authority, payload, snapshot)
        slot_id = candidate.slot_id
        if slot_id in result:
            raise StepDependencyIntegrityError(
                "Step-dependency derivation slot has multiple reservation objects"
            )
        result[slot_id] = (payload, snapshot, candidate)
    return result


def _event_unsigned(
    profile: Mapping[str, Any],
    paths: StepDependencyPaths,
    candidate: _Candidate,
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
        "slot_id": candidate.slot_id,
        "source_key_id": candidate.source_item["key_id"],
        "dependency_key_id": candidate.dependency_item["key_id"],
        "reservation": _reference(reservation_snapshot, paths.root),
        **_claims(),
    }


def _append_event(
    profile: Mapping[str, Any],
    paths: StepDependencyPaths,
    candidate: _Candidate,
    reservation_snapshot: registry.ArtifactSnapshot,
    *,
    sequence_id: int,
    previous: str,
    now: datetime,
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    body = _event_unsigned(
        profile,
        paths,
        candidate,
        reservation_snapshot,
        sequence_id=sequence_id,
        previous=previous,
        recorded_at=_utc_text(now),
    )
    payload = {**body, "entry_sha256": _sha256(_canonical_bytes(body))}
    path = paths.events / f"{sequence_id:020d}-{payload['entry_sha256']}.json"
    snapshot = _publish(
        path, _canonical_bytes(payload), root=paths.root, name="step-dependency event"
    )
    return payload, snapshot


def _replay_events(
    profile: Mapping[str, Any],
    paths: StepDependencyPaths,
    objects: Mapping[str, tuple[dict[str, Any], registry.ArtifactSnapshot, _Candidate]],
) -> tuple[list[dict[str, Any]], str, set[str]]:
    events: list[dict[str, Any]] = []
    previous = ZERO_HASH
    seen: set[str] = set()
    for expected_sequence, path in enumerate(
        _strict_entries(paths.events, name="step-dependency events"), 1
    ):
        matched = EVENT_NAME.fullmatch(path.name)
        if matched is None or int(matched.group("sequence")) != expected_sequence:
            raise StepDependencyIntegrityError(
                "Step-dependency event sequence/path changed"
            )
        payload, _ = _strict_json(path, name="step-dependency event")
        body = dict(payload)
        entry = body.pop("entry_sha256", None)
        slot_id = payload.get("slot_id")
        stored = objects.get(slot_id) if isinstance(slot_id, str) else None
        if stored is None:
            raise StepDependencyIntegrityError(
                "Step-dependency event references no reservation object"
            )
        _, reservation_snapshot, candidate = stored
        expected = _event_unsigned(
            profile,
            paths,
            candidate,
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
            or payload.get("recorded_at_utc") is None
            or payload.get("previous_entry_sha256") != previous
            or entry != _sha256(_canonical_bytes(body))
            or matched.group("entry") != entry
            or slot_id in seen
            or any(payload.get(name) is not True for name in TRUE_CAPABILITIES)
            or any(payload.get(name) is not False for name in FALSE_CLAIMS)
        ):
            raise StepDependencyIntegrityError("Step-dependency event chain changed")
        _parse_utc(payload["recorded_at_utc"], name="step-dependency event time")
        seen.add(slot_id)
        previous = str(entry)
        events.append(payload)
    missing = set(objects) - seen
    if len(missing) > 1:
        raise StepDependencyIntegrityError(
            "Step-dependency reservation objects branched before event publication"
        )
    return events, previous, seen


def _write_status(
    profile: Mapping[str, Any],
    paths: StepDependencyPaths,
    *,
    now: datetime,
    status: str,
    reason: str,
    reservation: Path | None,
    event: Path | None,
    source_key_id: str | None,
    dependency_key_id: str | None,
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
        "source_key_id": source_key_id,
        "dependency_key_id": dependency_key_id,
        "cache_authority": False,
        **_claims(),
    }
    try:
        registry._atomic_cache(  # noqa: SLF001
            paths.status,
            _canonical_bytes(payload),
            root=paths.root,
            name="step-dependency status",
        )
    except registry.EpochRegistryError as exc:
        raise StepDependencyIntegrityError(str(exc)) from exc


def _sidecar_children_present(paths: StepDependencyPaths) -> bool:
    return bool(
        _strict_entries(
            paths.reservation_objects, name="step-dependency reservation objects"
        )
        or _strict_entries(paths.events, name="step-dependency events")
    )


def _coordinate_step_dependency_reservation(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    registry_root: Path | None = None,
    active_root: Path | None = None,
    shadow_root: Path | None = None,
    clock: Callable[[], datetime] | None = None,
    load_authority: LoadAuthority | None = None,
) -> StepDependencyResult:
    profile = load_step_dependency_profile(config_path)
    paths = step_dependency_paths(
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
                raise StepDependencyBusyError(str(exc)) from exc
            except drain.EpochDrainError as exc:
                raise StepDependencyIntegrityError(str(exc)) from exc
        acquired = True
        authority = (load_authority or _load_recovery_authority)(paths, now)
        if authority is None:
            if _sidecar_children_present(paths):
                raise StepDependencyIntegrityError(
                    "Sidecar authority exists without a complete recovery v6 prefix"
                )
            reason = "a complete recovery v6 intent/receipt/event prefix is not yet available"
            _write_status(
                profile,
                paths,
                now=now,
                status="waiting_for_complete_recovery_authority",
                reason=reason,
                reservation=None,
                event=None,
                source_key_id=None,
                dependency_key_id=None,
            )
            return StepDependencyResult(
                "waiting_for_complete_recovery_authority", reason, paths.status
            )
        objects = _reservation_objects(profile, paths, authority)
        events, previous, seen = _replay_events(profile, paths, objects)
        orphaned = set(objects) - seen
        if orphaned:
            slot_id = next(iter(orphaned))
            _, reservation_snapshot, candidate = objects[slot_id]
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
                "an exact create-only step-dependency reservation was forward-adopted "
                "into its append-only event chain"
            )
            _write_status(
                profile,
                paths,
                now=now,
                status="step_dependency_event_forward_adopted",
                reason=reason,
                reservation=reservation_snapshot.path,
                event=event_snapshot.path,
                source_key_id=str(candidate.source_item["key_id"]),
                dependency_key_id=str(candidate.dependency_item["key_id"]),
            )
            return StepDependencyResult(
                "step_dependency_event_forward_adopted",
                reason,
                paths.status,
                reservation_snapshot.path,
                event_snapshot.path,
                str(candidate.source_item["key_id"]),
                str(candidate.dependency_item["key_id"]),
            )
        candidate = _select_candidate(authority, set(objects))
        if candidate is None:
            reason = (
                "no unreserved post-freeze confirmed settlement has one unique "
                "terminal frozen-manifest outcome sibling"
            )
            _write_status(
                profile,
                paths,
                now=now,
                status="waiting_for_ready_step_dependency",
                reason=reason,
                reservation=None,
                event=None,
                source_key_id=None,
                dependency_key_id=None,
            )
            return StepDependencyResult(
                "waiting_for_ready_step_dependency", reason, paths.status
            )
        _, reservation_snapshot = _publish_reservation(
            profile, paths, authority, candidate, now=now
        )
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
            "one post-freeze settlement step dependency was reserved from exact "
            "recovery receipt/event authority"
        )
        _write_status(
            profile,
            paths,
            now=now,
            status="step_dependency_reserved",
            reason=reason,
            reservation=reservation_snapshot.path,
            event=event_snapshot.path,
            source_key_id=str(candidate.source_item["key_id"]),
            dependency_key_id=str(candidate.dependency_item["key_id"]),
        )
        return StepDependencyResult(
            "step_dependency_reserved",
            reason,
            paths.status,
            reservation_snapshot.path,
            event_snapshot.path,
            str(candidate.source_item["key_id"]),
            str(candidate.dependency_item["key_id"]),
        )
    except StepDependencyBusyError:
        raise
    except StepDependencyError as exc:
        if acquired:
            try:
                _write_status(
                    profile,
                    paths,
                    now=now,
                    status="blocked_integrity",
                    reason=f"{type(exc).__name__}:{exc}",
                    reservation=None,
                    event=None,
                    source_key_id=None,
                    dependency_key_id=None,
                )
            except StepDependencyError:
                pass
        raise
    except (
        registry.EpochRegistryError,
        manifest.WorksetManifestError,
        recovery.WorksetRecoveryError,
    ) as exc:
        wrapped = StepDependencyIntegrityError(
            f"Step-dependency authority validation failed:{type(exc).__name__}:{exc}"
        )
        if acquired:
            try:
                _write_status(
                    profile,
                    paths,
                    now=now,
                    status="blocked_integrity",
                    reason=f"{type(wrapped).__name__}:{wrapped}",
                    reservation=None,
                    event=None,
                    source_key_id=None,
                    dependency_key_id=None,
                )
            except StepDependencyError:
                pass
        raise wrapped from exc
    finally:
        try:
            drain._release_locks(handles)  # noqa: SLF001
        except drain.EpochDrainError as exc:
            if sys.exc_info()[0] is None:
                raise StepDependencyIntegrityError(str(exc)) from exc


def coordinate_step_dependency_reservation(
    *, config_path: Path = DEFAULT_CONFIG_PATH
) -> StepDependencyResult:
    """Run one machine-only sidecar reservation/adoption poll."""

    return _coordinate_step_dependency_reservation(config_path=config_path)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result = coordinate_step_dependency_reservation(config_path=args.config)
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
                "dependency_key_id": result.dependency_key_id,
                "bounded_workset_recovery_implemented": False,
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
