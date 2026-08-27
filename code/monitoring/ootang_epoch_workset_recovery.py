"""Advance one deterministic manifest-keyed old-epoch recovery action."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, BinaryIO


ROOT = Path(__file__).resolve().parents[2]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_epoch_drain as drain  # noqa: E402
from monitoring import ootang_epoch_registry as registry  # noqa: E402
from monitoring import ootang_epoch_workset_manifest as manifest  # noqa: E402
from monitoring import ootang_prequential_live as live  # noqa: E402
from monitoring import ootang_trusted_time_shadow_core as trusted  # noqa: E402
from monitoring import ootang_verified_live as guard  # noqa: E402


DEFAULT_CONFIG_PATH = ROOT / "config" / "ootang_epoch_workset_recovery.v1.json"
DEFAULT_CONFIG_SHA256 = (
    "246dbf18bbc24cdecb7c85d289cdf4edd069fe5e47cbcbbbfb9e27e438a4ee04"
)
MAX_CONTROL_BYTES = 4 * 1024 * 1024
ZERO_HASH = "0" * 64
LOCK_ORDER = ("manager", "cycle", "replay", "shadow")
SUPPORTED_SUCCESSORS = (
    "trusted_time_request_der_repaired",
    "anchor_receipt_repaired",
    "superseded_by_backfill",
)
TRANSITION_CONTRACT = {
    "schema_version": "ootang_epoch_workset_transition_contract_v1",
    "families": {
        "issue_route_replay": {
            "mutation_lanes": ["issue_replay_registry", "live_ledger"],
            "edges": {
                "issue_replay_receipt_verified": ["issue_route_replay_consumed"],
                "issue_route_replay_consumed": [],
                "derived_live_outstanding_required": [],
            },
            "terminal_actions": ["issue_route_replay_consumed"],
        },
        "live_outstanding": {
            "mutation_lanes": ["live_ledger", "trusted_time_outbox"],
            "edges": {
                "anchor_receipt_repaired": [],
                "anchor_request_recorded": ["anchor_result_recorded"],
                "anchor_result_recorded": [
                    "anchor_request_recorded",
                    "outcome_batch_settled",
                ],
                "outcome_batch_settled": [],
            },
            "terminal_actions": [
                "anchor_receipt_repaired",
                "outcome_batch_settled",
            ],
        },
        "outcome_revision": {
            "mutation_lanes": ["source_registry", "outcome_registry", "live_ledger"],
            "edges": {
                "source_snapshot_ingested": ["derived_outcome_items_required"],
                "derived_outcome_items_required": [],
                "outcome_materialized": ["outcome_or_revision_consumed"],
                "outcome_or_revision_consumed": [],
            },
            "terminal_actions": ["outcome_or_revision_consumed"],
        },
        "guard": {
            "mutation_lanes": ["guard_registry"],
            "edges": {
                "guard_completion_recorded": [],
                "superseded_by_backfill": [],
            },
            "terminal_actions": [
                "guard_completion_recorded",
                "superseded_by_backfill",
            ],
        },
        "trusted_time": {
            "mutation_lanes": ["trusted_time_outbox"],
            "edges": {
                "trusted_time_request_der_repaired": [
                    "trusted_time_response_link_recorded"
                ],
                "trusted_time_response_link_recorded": [
                    "trusted_time_receipt_verified"
                ],
                "trusted_time_receipt_verified": [],
            },
            "terminal_actions": ["trusted_time_receipt_verified"],
        },
        "shadow": {
            "mutation_lanes": ["shadow_ledger"],
            "edges": {
                "shadow_epoch_closed": ["shadow_epoch_genesis_created"],
                "shadow_epoch_genesis_created": ["shadow_live_event_classified"],
                "shadow_live_event_classified": [],
                "derived_shadow_outstanding_required": ["shadow_outstanding_settled"],
                "shadow_outstanding_settled": [],
                "shadow_cursor_at_frozen_live_upper_tip": [],
            },
            "terminal_actions": [
                "shadow_live_event_classified",
                "shadow_outstanding_settled",
                "shadow_cursor_at_frozen_live_upper_tip",
            ],
        },
    },
    "dependency_policy": "only_terminal_step_receipts_satisfy_dependencies",
    "receipt_policy": "one_create_only_receipt_per_transition_step",
    "branch_policy": "branch_selection_requires_reviewed_evidence_adapter",
    "derived_work_policy": "unresolved_derived_work_never_counts_as_terminal",
}
TRANSITION_CONTRACT_SHA256 = hashlib.sha256(
    (
        json.dumps(
            TRANSITION_CONTRACT,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
).hexdigest()
TRUE_CAPABILITIES = (
    "machine_only",
    "manifest_keyed_dispatcher_implemented",
    "deterministic_local_adapters_implemented",
    "crash_forward_adoption_implemented",
    "transition_plan_binding_implemented",
    "step_receipt_chain_implemented",
    "terminal_receipt_dependency_gate_implemented",
    "live_ledger_expected_pre_head_cas_implemented",
)
FALSE_CLAIMS = (
    "bounded_workset_recovery_implemented",
    "all_reserved_items_settled",
    "all_reserved_successors_supported",
    "terminal_transition_closure_implemented",
    "derived_future_work_reservation_implemented",
    "all_transition_branches_supported",
    "network_recovery_implemented",
    "ledger_mutation_recovery_implemented",
    "network_action_performed",
    "live_ledger_mutated",
    "legacy_guard_completion_created",
    "old_work_admission_fence_implemented",
    "direct_filesystem_writer_fence_implemented",
    "anti_rollback_authority_implemented",
    "external_implementation_trust_anchor_implemented",
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
EXPECTED_UPSTREAM = {
    "manifest_profile": {
        "path": "config/ootang_epoch_workset_manifest.v1.json",
        "expected_sha256": "ca3f24c91492d4cbd415331c1abf1e90bd2b971aac8016e69183652e3fb850dc",
    },
    "manifest_implementation": {
        "path": "code/monitoring/ootang_epoch_workset_manifest.py",
        "expected_sha256": "8868075715188effe708fe353bf18cbefdee9060abd90f92ede8120cf5313ef2",
    },
    "trusted_implementation": {
        "path": "code/monitoring/ootang_trusted_time_shadow_core.py",
        "expected_sha256": "797cedbc1e24fac6e4cbf042f48981786b662ce8b0fa913b988bce12818023c7",
    },
    "live_implementation": {
        "path": "code/monitoring/ootang_prequential_live.py",
        "expected_sha256": "086638e87ded39eed9b84311fa8a49898bf1c34d48327b41ac6e1d4633f843d8",
    },
    "live_ledger_implementation": {
        "path": "code/monitoring/ootang_live_ledger.py",
        "expected_sha256": "087356f33e43996411d3524ec99256d4b5c390f09dd0dbfca083cc7a63245b14",
    },
    "live_ledger_cas_implementation": {
        "path": "code/monitoring/ootang_live_ledger_cas_v1.py",
        "expected_sha256": "23ca29356ef23483a0846e701376850745400082e607a16e2479c1057b6befa4",
    },
    "guard_implementation": {
        "path": "code/monitoring/ootang_verified_live.py",
        "expected_sha256": "e8fc8264c291294207f35416b921940edb8324fdd3f526b9bacacde0988e5979",
    },
}
EXPECTED_RUNTIME = {
    "registry_root": "runtime/ootang_epoch_registry_v1",
    "namespace": "workset_recovery_v1",
    "global_intent": "intent.json",
    "item_intents": "item_intents",
    "receipts": "receipts",
    "events": "events",
    "status": "status.json",
    "manager_lock": "manager.lock",
    "active_root": "runtime/ootang_prequential_live_v1",
    "shadow_root": "runtime/ootang_prequential_calibration_shadow_v1",
    "cycle_lock": "prequential_cycle.lock",
    "replay_lock": "issue_replay.lock",
    "shadow_lock": "runner.lock",
}
EXPECTED_PROTOCOL = {
    "intent_schema_version": "ootang_epoch_workset_recovery_intent_v2",
    "item_intent_schema_version": "ootang_epoch_workset_recovery_step_intent_v2",
    "receipt_schema_version": "ootang_epoch_workset_recovery_step_receipt_v2",
    "event_schema_version": "ootang_epoch_workset_recovery_step_event_v2",
    "status_schema_version": "ootang_epoch_workset_recovery_status_v2",
    "event_type": "epoch_workset_transition_step_recorded",
    "transition_contract_schema_version": TRANSITION_CONTRACT["schema_version"],
    "transition_contract_sha256": TRANSITION_CONTRACT_SHA256,
    "terminal_completion_policy": ("only_terminal_step_receipts_satisfy_dependencies"),
    "canonical_json": "utf8_sort_keys_compact_no_nan_trailing_lf",
    "surviving_lock_order": list(LOCK_ORDER),
    "supported_successors": list(SUPPORTED_SUCCESSORS),
    "maximum_items": 4096,
    "maximum_control_bytes": MAX_CONTROL_BYTES,
    "initial_previous_entry_sha256": ZERO_HASH,
    "poll_policy": "advance_at_most_one_ready_supported_key",
}


class WorksetRecoveryError(RuntimeError):
    """Base recovery error."""


class WorksetRecoveryConfigError(WorksetRecoveryError):
    """The reviewed recovery profile changed."""


class WorksetRecoveryIntegrityError(WorksetRecoveryError):
    """Immutable recovery authority or a local predecessor changed."""


class WorksetRecoveryBusyError(WorksetRecoveryError):
    """A surviving lock is held."""


@dataclass(frozen=True)
class RecoveryPaths:
    registry_root: Path
    root: Path
    global_intent: Path
    item_intents: Path
    receipts: Path
    events: Path
    status: Path
    manager_lock: Path
    active_root: Path
    shadow_root: Path
    cycle_lock: Path
    replay_lock: Path
    shadow_lock: Path


@dataclass(frozen=True)
class RecoveryResult:
    status: str
    reason: str
    status_path: Path
    key_id: str | None = None
    receipt_path: Path | None = None
    event_path: Path | None = None
    bounded_workset_recovery_implemented: bool = False
    lifecycle_authority: bool = False
    transition_authority: bool = False


@dataclass(frozen=True)
class Reservation:
    profile: Mapping[str, Any]
    paths: manifest.WorksetManifestPaths
    binding: manifest.AdmissionCutBinding
    event: Mapping[str, Any]
    event_snapshot: registry.ArtifactSnapshot
    manifest: Mapping[str, Any]
    manifest_snapshot: registry.ArtifactSnapshot


@dataclass(frozen=True)
class ActionOutput:
    kind: str
    reference: Mapping[str, object] | None
    semantics: Mapping[str, object]


ActionHook = Callable[[Mapping[str, Any], Reservation, RecoveryPaths], ActionOutput]
LoadReservation = Callable[[RecoveryPaths], Reservation | None]


def _canonical_bytes(value: object) -> bytes:
    try:
        return registry._canonical_bytes(value)  # type: ignore[arg-type]  # noqa: SLF001
    except (TypeError, ValueError) as exc:
        raise WorksetRecoveryIntegrityError("Value is not canonical JSON") from exc


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _claims() -> dict[str, bool]:
    return {
        **{key: True for key in TRUE_CAPABILITIES},
        **{key: False for key in FALSE_CLAIMS},
    }


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise WorksetRecoveryIntegrityError("Recovery clock must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: object, *, name: str) -> datetime:
    try:
        return registry._utc_timestamp(value, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise WorksetRecoveryIntegrityError(str(exc)) from exc


def _exact(value: object, keys: set[str], *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise WorksetRecoveryIntegrityError(f"{name} keys changed")
    return value


def _strict_json(
    path: Path, *, name: str
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    try:
        payload, snapshot = manifest._read_json(  # noqa: SLF001
            path, name=name, maximum_bytes=MAX_CONTROL_BYTES
        )
    except manifest.WorksetManifestError as exc:
        raise WorksetRecoveryIntegrityError(str(exc)) from exc
    return payload, snapshot


def _publish(
    path: Path, raw: bytes, *, root: Path, name: str
) -> registry.ArtifactSnapshot:
    try:
        return drain._publish_once_durable(path, raw, root=root, name=name)  # noqa: SLF001
    except drain.EpochDrainError as exc:
        raise WorksetRecoveryIntegrityError(str(exc)) from exc


def _child(root: Path, relative: str, *, name: str) -> Path:
    try:
        return registry._contained(root, relative, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise WorksetRecoveryConfigError(str(exc)) from exc


def load_workset_recovery_profile(
    path: Path = DEFAULT_CONFIG_PATH, *, project_root: Path = ROOT
) -> dict[str, Any]:
    root = project_root.resolve()
    resolved = path if path.is_absolute() else root / path
    if root != ROOT.resolve() or resolved.resolve() != DEFAULT_CONFIG_PATH.resolve():
        raise WorksetRecoveryConfigError(
            "Only the reviewed default recovery profile is accepted"
        )
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            resolved, name="workset recovery profile", maximum_bytes=MAX_CONTROL_BYTES
        )
        profile = registry._decode_json(  # noqa: SLF001
            snapshot.raw, name="recovery profile"
        )
    except registry.EpochRegistryError as exc:
        raise WorksetRecoveryConfigError(str(exc)) from exc
    if snapshot.sha256 != DEFAULT_CONFIG_SHA256:
        raise WorksetRecoveryConfigError("Recovery profile SHA-256 changed")
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
        name="recovery profile",
    )
    if (
        profile["schema_version"] != "ootang_epoch_workset_recovery_profile_v1"
        or profile["profile_id"] != "ootang-epoch-workset-recovery-v1"
        or profile["case"] != "ootang"
        or profile["formal_warning_output"] is not False
        or profile["default_pipeline_member"] is not False
        or profile["upstream"] != EXPECTED_UPSTREAM
        or profile["runtime"] != EXPECTED_RUNTIME
        or profile["protocol"] != EXPECTED_PROTOCOL
        or profile["engineering_capabilities"] != _claims()
    ):
        raise WorksetRecoveryConfigError("Recovery profile semantics changed")
    for binding in EXPECTED_UPSTREAM.values():
        upstream = _child(root, binding["path"], name="frozen upstream")
        try:
            actual = registry._read_regular(  # noqa: SLF001
                upstream, name="frozen upstream", maximum_bytes=64 * 1024 * 1024
            )
        except registry.EpochRegistryError as exc:
            raise WorksetRecoveryConfigError(str(exc)) from exc
        if actual.sha256 != binding["expected_sha256"]:
            raise WorksetRecoveryConfigError(
                f"Frozen upstream changed:{binding['path']}"
            )
    profile["_profile_sha256"] = snapshot.sha256
    return profile


def recovery_paths(
    profile: Mapping[str, Any],
    *,
    registry_root: Path | None = None,
    active_root: Path | None = None,
    shadow_root: Path | None = None,
) -> RecoveryPaths:
    registry_path = (
        registry_root or (ROOT / profile["runtime"]["registry_root"])
    ).resolve()
    root = _child(registry_path, profile["runtime"]["namespace"], name="recovery root")
    active = (active_root or (ROOT / profile["runtime"]["active_root"])).resolve()
    shadow = (shadow_root or (ROOT / profile["runtime"]["shadow_root"])).resolve()
    return RecoveryPaths(
        registry_path,
        root,
        _child(root, profile["runtime"]["global_intent"], name="global intent"),
        _child(root, profile["runtime"]["item_intents"], name="item intents"),
        _child(root, profile["runtime"]["receipts"], name="receipts"),
        _child(root, profile["runtime"]["events"], name="events"),
        _child(root, profile["runtime"]["status"], name="status"),
        _child(registry_path, profile["runtime"]["manager_lock"], name="manager lock"),
        active,
        shadow,
        _child(active, profile["runtime"]["cycle_lock"], name="cycle lock"),
        _child(active, profile["runtime"]["replay_lock"], name="replay lock"),
        _child(shadow, profile["runtime"]["shadow_lock"], name="shadow lock"),
    )


def _manifest_paths(paths: RecoveryPaths) -> manifest.WorksetManifestPaths:
    profile = manifest.load_workset_manifest_profile()
    return manifest.workset_manifest_paths(
        profile,
        runtime_root=paths.registry_root,
        active_runtime_root=paths.active_root,
        shadow_runtime_root=paths.shadow_root,
    )


def _load_reservation(paths: RecoveryPaths) -> Reservation | None:
    try:
        profile = manifest.load_workset_manifest_profile()
        manifest_paths = _manifest_paths(paths)
        binding = manifest._default_admission_cut_binding(  # noqa: SLF001
            manifest_paths
        )
        if binding is None:
            return None
        loaded = manifest._load_existing_event(profile, manifest_paths, binding)  # noqa: SLF001
    except manifest.WorksetManifestError as exc:
        raise WorksetRecoveryIntegrityError(str(exc)) from exc
    if loaded is None:
        return None
    event, event_snapshot, manifest_snapshot = loaded
    manifest_payload, checked = manifest._load_manifest_reference(  # noqa: SLF001
        profile, manifest_paths, event["manifest"], binding
    )
    if checked != manifest_snapshot:
        raise WorksetRecoveryIntegrityError(
            "Manifest changed during reservation replay"
        )
    return Reservation(
        profile,
        manifest_paths,
        binding,
        event,
        event_snapshot,
        manifest_payload,
        manifest_snapshot,
    )


def _key_id(manifest_sha256: str, item: Mapping[str, Any]) -> str:
    return _sha256(
        _canonical_bytes(
            {
                "manifest_sha256": manifest_sha256,
                "natural_key": item["natural_key"],
                "namespace_digest": item["namespace_digest"],
            }
        )
    )


def _dag(reservation: Reservation) -> tuple[list[dict[str, Any]], dict[str, str], str]:
    raw_items = reservation.manifest.get("items")
    if not isinstance(raw_items, list) or len(raw_items) > 4096:
        raise WorksetRecoveryIntegrityError("Reserved item list changed")
    items: list[dict[str, Any]] = []
    by_natural: dict[str, dict[str, Any]] = {}
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise WorksetRecoveryIntegrityError("Reserved item changed type")
        natural = raw.get("natural_key")
        deps = raw.get("dependency_keys")
        if (
            not isinstance(natural, str)
            or natural in by_natural
            or not isinstance(deps, list)
        ):
            raise WorksetRecoveryIntegrityError(
                "Reserved natural key/dependencies changed"
            )
        item = dict(raw)
        item["key_id"] = _key_id(reservation.manifest_snapshot.sha256, item)
        by_natural[natural] = item
        items.append(item)
    for item in items:
        deps = item["dependency_keys"]
        if len(set(deps)) != len(deps) or any(dep not in by_natural for dep in deps):
            raise WorksetRecoveryIntegrityError(
                "Dependency graph has unknown/duplicate edges"
            )
    ready: list[dict[str, Any]] = []
    remaining = {item["natural_key"]: item for item in items}
    canonical_position = {
        item["natural_key"]: index for index, item in enumerate(items)
    }
    emitted: set[str] = set()
    while remaining:
        candidates = sorted(
            (
                item
                for item in remaining.values()
                if set(item["dependency_keys"]) <= emitted
            ),
            key=lambda item: canonical_position[item["natural_key"]],
        )
        if not candidates:
            raise WorksetRecoveryIntegrityError("Dependency graph is cyclic")
        for item in candidates:
            ready.append(item)
            emitted.add(item["natural_key"])
            remaining.pop(item["natural_key"])
    ids = {item["natural_key"]: item["key_id"] for item in items}
    graph = [
        {
            "key_id": item["key_id"],
            "natural_key": item["natural_key"],
            "dependency_key_ids": [ids[dep] for dep in item["dependency_keys"]],
            "successor": item["canonical_successor_state"],
        }
        for item in ready
    ]
    return ready, ids, _sha256(_canonical_bytes(graph))


def _transition_plan(item: Mapping[str, Any]) -> dict[str, object]:
    family = item.get("family")
    if not isinstance(family, str) or family not in TRANSITION_CONTRACT["families"]:
        raise WorksetRecoveryIntegrityError("Recovery item has no transition contract")
    family_contract = TRANSITION_CONTRACT["families"][family]
    edges = {
        action: list(next_actions)
        for action, next_actions in family_contract["edges"].items()
    }
    terminal_actions = list(family_contract["terminal_actions"])
    initial_action = item.get("canonical_successor_state")
    if not isinstance(initial_action, str) or initial_action not in edges:
        raise WorksetRecoveryIntegrityError(
            "Recovery item successor has no transition edge"
        )
    authority = item.get("authority")
    if not isinstance(authority, Mapping):
        raise WorksetRecoveryIntegrityError("Recovery item authority changed type")

    closure_resolved = True
    unresolved_reason: str | None = None
    if family == "issue_route_replay" and authority.get("seal_event") is None:
        edges["issue_route_replay_consumed"] = ["derived_live_outstanding_required"]
        terminal_actions = []
        closure_resolved = False
        unresolved_reason = "route consumption creates unreserved live outstanding work"
    elif family == "outcome_revision" and initial_action == "source_snapshot_ingested":
        terminal_actions = []
        closure_resolved = False
        unresolved_reason = (
            "source ingestion may create multiple content-dependent outcome obligations"
        )
    elif family == "shadow":
        action_variant = authority.get("action")
        record_type = authority.get("record_type")
        if action_variant == "create_shadow_epoch_genesis":
            initial_action = "shadow_epoch_genesis_created"
            terminal_actions = []
            closure_resolved = False
            unresolved_reason = (
                "shadow genesis can create an unreserved outstanding classification"
            )
        elif action_variant == "close_or_open_shadow_epoch":
            initial_action = "shadow_epoch_closed"
            terminal_actions = []
            closure_resolved = False
            unresolved_reason = (
                "shadow rotation requires close and genesis transactions"
            )
        elif record_type == "unclassified_live_issue":
            edges["shadow_live_event_classified"] = [
                "derived_shadow_outstanding_required"
            ]
            terminal_actions = []
            closure_resolved = False
            unresolved_reason = (
                "shadow issue classification creates an unreserved outstanding target"
            )

    body: dict[str, object] = {
        "schema_version": "ootang_epoch_workset_item_transition_plan_v1",
        "key_id": item["key_id"],
        "family": family,
        "natural_key": item["natural_key"],
        "initial_action": initial_action,
        "edges": [
            {"action": action, "next_actions": next_actions}
            for action, next_actions in edges.items()
        ],
        "terminal_actions": terminal_actions,
        "closure_resolved": closure_resolved,
        "unresolved_reason": unresolved_reason,
        "mutation_lanes": list(family_contract["mutation_lanes"]),
        "read_set_namespace_digest": item["namespace_digest"],
        "write_set_policy": "action_specific_create_only_or_head_cas_required",
    }
    return {**body, "plan_sha256": _sha256(_canonical_bytes(body))}


def _plan_edges(plan: Mapping[str, Any]) -> dict[str, list[str]]:
    raw = plan.get("edges")
    if not isinstance(raw, list):
        raise WorksetRecoveryIntegrityError("Transition plan edges changed type")
    result: dict[str, list[str]] = {}
    for edge in raw:
        if (
            not isinstance(edge, dict)
            or set(edge) != {"action", "next_actions"}
            or not isinstance(edge.get("action"), str)
            or not isinstance(edge.get("next_actions"), list)
            or not all(isinstance(value, str) for value in edge["next_actions"])
            or edge["action"] in result
        ):
            raise WorksetRecoveryIntegrityError("Transition plan edge changed")
        result[edge["action"]] = list(edge["next_actions"])
    return result


def _step_id(key_id: str, step_index: int, action: str) -> str:
    return _sha256(
        _canonical_bytes({"key_id": key_id, "step_index": step_index, "action": action})
    )


def _reference(snapshot: registry.ArtifactSnapshot, root: Path) -> dict[str, object]:
    return {
        "path": snapshot.path.relative_to(root).as_posix(),
        "sha256": snapshot.sha256,
        "size_bytes": snapshot.size_bytes,
    }


def _provenance() -> dict[str, object]:
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            Path(__file__),
            name="recovery implementation",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
    except registry.EpochRegistryError as exc:
        raise WorksetRecoveryIntegrityError(str(exc)) from exc
    return {
        "path": "code/monitoring/ootang_epoch_workset_recovery.py",
        "sha256": snapshot.sha256,
        "size_bytes": snapshot.size_bytes,
    }


def _item_adapter_supported(item: Mapping[str, Any], action: str | None = None) -> bool:
    selected = action or item.get("canonical_successor_state")
    if selected not in SUPPORTED_SUCCESSORS:
        return False
    if selected != "superseded_by_backfill":
        return True
    authority = item.get("authority")
    return isinstance(authority, Mapping) and isinstance(
        authority.get("superseding_live_event"), Mapping
    )


def _global_intent_payload(
    profile: Mapping[str, Any],
    reservation: Reservation,
    ordered: Sequence[Mapping[str, Any]],
    graph_digest: str,
    *,
    created_at: str,
) -> dict[str, object]:
    plans = [_transition_plan(item) for item in ordered]
    unsupported_initial_steps = [
        {
            "key_id": item["key_id"],
            "action": plan["initial_action"],
        }
        for item, plan in zip(ordered, plans, strict=True)
        if not _item_adapter_supported(item, str(plan["initial_action"]))
    ]
    return {
        "schema_version": profile["protocol"]["intent_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "created_at_utc": created_at,
        "reservation_event": _reference(
            reservation.event_snapshot, reservation.paths.root
        ),
        "manifest": _reference(reservation.manifest_snapshot, reservation.paths.root),
        "workset_keyset_sha256": reservation.manifest["workset_keyset_sha256"],
        "dependency_graph_sha256": graph_digest,
        "transition_contract_schema_version": TRANSITION_CONTRACT["schema_version"],
        "transition_contract_sha256": TRANSITION_CONTRACT_SHA256,
        "transition_plans": plans,
        "transition_plans_sha256": _sha256(_canonical_bytes(plans)),
        "ordered_key_ids": [item["key_id"] for item in ordered],
        "initial_step_supported_key_ids": [
            item["key_id"]
            for item, plan in zip(ordered, plans, strict=True)
            if _item_adapter_supported(item, str(plan["initial_action"]))
        ],
        "unsupported_initial_steps": unsupported_initial_steps,
        "adapter_provenance": _provenance(),
        **_claims(),
    }


def _ensure_global_intent(
    profile: Mapping[str, Any],
    paths: RecoveryPaths,
    reservation: Reservation,
    ordered: Sequence[Mapping[str, Any]],
    graph_digest: str,
    *,
    now: datetime,
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    stable = _global_intent_payload(
        profile, reservation, ordered, graph_digest, created_at=""
    )
    if paths.global_intent.exists():
        payload, snapshot = _strict_json(
            paths.global_intent, name="recovery global intent"
        )
        comparable = {**payload, "created_at_utc": ""}
        if comparable != stable:
            raise WorksetRecoveryIntegrityError("Recovery global intent changed")
        _parse_utc(payload["created_at_utc"], name="intent time")
        return payload, snapshot
    payload = _global_intent_payload(
        profile, reservation, ordered, graph_digest, created_at=_utc_text(now)
    )
    snapshot = _publish(
        paths.global_intent,
        _canonical_bytes(payload),
        root=paths.root,
        name="recovery global intent",
    )
    replay, checked = _strict_json(paths.global_intent, name="recovery global intent")
    if replay != payload or checked != snapshot:
        raise WorksetRecoveryIntegrityError("Recovery global intent did not replay")
    return payload, snapshot


def _strict_named_json(directory: Path, *, suffix: str, name: str) -> dict[str, Path]:
    if not directory.exists():
        return {}
    try:
        entries = manifest._strict_entries(directory, name=name)  # noqa: SLF001
    except manifest.WorksetManifestError as exc:
        raise WorksetRecoveryIntegrityError(str(exc)) from exc
    result: dict[str, Path] = {}
    for path in entries:
        if not path.name.endswith(suffix):
            raise WorksetRecoveryIntegrityError(f"{name} contains an unknown entry")
        stem = path.name[: -len(suffix)]
        if len(stem) != 64 or any(char not in "0123456789abcdef" for char in stem):
            raise WorksetRecoveryIntegrityError(f"{name} filename changed")
        result[stem] = path
    return result


def _recovery_authority_state(paths: RecoveryPaths) -> tuple[bool, bool]:
    global_present = paths.global_intent.exists() or paths.global_intent.is_symlink()
    child_present = False
    for directory, name in (
        (paths.item_intents, "recovery item intents"),
        (paths.receipts, "recovery receipts"),
        (paths.events, "recovery events"),
    ):
        try:
            child_present = child_present or bool(
                manifest._strict_entries(directory, name=name)  # noqa: SLF001
            )
        except manifest.WorksetManifestError as exc:
            raise WorksetRecoveryIntegrityError(str(exc)) from exc
    return global_present, child_present


def _artifact_path(obligation: Mapping[str, Any], reservation: Reservation) -> Path:
    root = obligation.get("root")
    base = (
        reservation.paths.active_root
        if root == "active"
        else reservation.paths.shadow_root
        if root == "shadow"
        else None
    )
    if base is None or not isinstance(obligation.get("path"), str):
        raise WorksetRecoveryIntegrityError("Manifest artifact root/path changed")
    try:
        return registry._contained(base, obligation["path"], name="item artifact")  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise WorksetRecoveryIntegrityError(str(exc)) from exc


def _cas_item_artifacts(item: Mapping[str, Any], reservation: Reservation) -> set[Path]:
    artifacts = item.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise WorksetRecoveryIntegrityError("Recovery item lost its artifact CAS")
    paths: set[Path] = set()
    for obligation in artifacts:
        if not isinstance(obligation, dict):
            raise WorksetRecoveryIntegrityError("Item artifact changed type")
        path = _artifact_path(obligation, reservation)
        try:
            snapshot = registry._read_regular(
                path, name="item predecessor", maximum_bytes=64 * 1024 * 1024
            )  # noqa: SLF001
        except registry.EpochRegistryError as exc:
            raise WorksetRecoveryIntegrityError(str(exc)) from exc
        if snapshot.sha256 != obligation.get(
            "sha256"
        ) or snapshot.size_bytes != obligation.get("size_bytes"):
            raise WorksetRecoveryIntegrityError("Item predecessor failed exact CAS")
        paths.add(path)
    return paths


def _ensure_item_intent(
    profile: Mapping[str, Any],
    paths: RecoveryPaths,
    reservation: Reservation,
    global_snapshot: registry.ArtifactSnapshot,
    item: Mapping[str, Any],
    dependency_receipts: Sequence[registry.ArtifactSnapshot],
    previous_step_receipt: registry.ArtifactSnapshot | None,
    *,
    step_index: int,
    action: str,
    now: datetime,
) -> registry.ArtifactSnapshot:
    step_id = _step_id(item["key_id"], step_index, action)
    path = paths.item_intents / f"{step_id}.json"
    plan = _transition_plan(item)
    stable = {
        "schema_version": profile["protocol"]["item_intent_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "step_id": step_id,
        "step_index": step_index,
        "key_id": item["key_id"],
        "natural_key": item["natural_key"],
        "namespace_digest": item["namespace_digest"],
        "action": action,
        "transition_plan_sha256": plan["plan_sha256"],
        "previous_step_receipt": (
            _reference(previous_step_receipt, paths.root)
            if previous_step_receipt is not None
            else None
        ),
        "dependency_keys": item["dependency_keys"],
        "dependency_receipts": [
            _reference(snapshot, paths.root) for snapshot in dependency_receipts
        ],
        "global_intent": _reference(global_snapshot, paths.root),
        "manifest": _reference(reservation.manifest_snapshot, reservation.paths.root),
        "adapter_provenance": _provenance(),
    }
    if path.exists():
        payload, snapshot = _strict_json(path, name="recovery item intent")
        comparable = {
            key: value for key, value in payload.items() if key != "created_at_utc"
        }
        if set(payload) != {*stable, "created_at_utc"} or comparable != stable:
            raise WorksetRecoveryIntegrityError("Recovery item intent changed")
        _parse_utc(payload["created_at_utc"], name="item intent time")
        return snapshot
    payload = {**stable, "created_at_utc": _utc_text(now)}
    return _publish(
        path, _canonical_bytes(payload), root=paths.root, name="recovery item intent"
    )


def _live_projection(
    reservation: Reservation,
) -> tuple[Mapping[str, Any], Any, tuple[Any, ...]]:
    profile = live.load_config()
    paths = live.runtime_paths(profile, runtime_root=reservation.paths.active_root)
    prerequisites = live.load_prerequisites(profile, paths)
    if prerequisites is None:
        raise WorksetRecoveryIntegrityError("Frozen live prerequisites disappeared")
    try:
        projection = live.load_verified_ledger_projection(profile, paths, prerequisites)
        events = projection.ledger_events
    except Exception as exc:
        raise WorksetRecoveryIntegrityError(
            f"Frozen live replay failed:{type(exc).__name__}:{exc}"
        ) from exc
    tip = reservation.manifest["frozen_live_upper_tip"]
    if (
        not events
        or projection.ledger_event_count != tip["live_event_count"]
        or projection.ledger_terminal_sha256 != tip["live_terminal_sha256"]
        or projection.epoch_id != tip["old_live_epoch_id"]
    ):
        raise WorksetRecoveryIntegrityError("Frozen live upper tip changed")
    return profile, paths, events


def _publish_additive(
    path: Path, raw: bytes, *, root: Path, name: str, inputs: set[Path]
) -> registry.ArtifactSnapshot:
    if path in inputs:
        raise WorksetRecoveryIntegrityError("Recovery output overlaps a manifest input")
    return _publish(path, raw, root=root, name=name)


def _trusted_der_action(
    item: Mapping[str, Any],
    reservation: Reservation,
    paths: RecoveryPaths,
    inputs: set[Path],
) -> ActionOutput:
    authority = item["authority"]
    target = date.fromisoformat(authority["target_date"])
    profile = trusted.load_trusted_time_profile()
    trusted_paths = trusted.trusted_time_paths(profile, runtime_root=paths.active_root)
    record_path = trusted_paths.requests / f"{target.isoformat()}.json"
    record_snapshot = trusted._read_regular(
        record_path, name="timestamp request record"
    )  # noqa: SLF001
    record = trusted._decode_json(record_snapshot.raw, name="timestamp request record")  # noqa: SLF001
    if record_snapshot.raw != trusted._canonical_bytes(record):  # noqa: SLF001
        raise WorksetRecoveryIntegrityError("Timestamp request is not canonical")
    nonce_text = record.get("nonce_hex")
    envelope = record.get("evidence_envelope")
    if (
        not isinstance(nonce_text, str)
        or len(nonce_text) != 64
        or not isinstance(envelope, dict)
    ):
        raise WorksetRecoveryIntegrityError("Timestamp request identity changed")
    message = trusted._canonical_bytes(envelope)  # noqa: SLF001
    nonce = int(nonce_text, 16)
    der = trusted.build_timestamp_request(
        message, nonce, profile["rfc3161"]["policy_oid"]
    )
    der_path = trusted_paths.request_der / f"{target.isoformat()}.tsq"
    link = trusted_paths.response_links / f"{target.isoformat()}.json"
    receipt = trusted_paths.receipts / f"{target.isoformat()}.json"
    expected = record.get("request_der")
    expected_ref = {
        "path": der_path.relative_to(paths.active_root).as_posix(),
        "sha256": _sha256(der),
        "size_bytes": len(der),
    }
    if (
        record_snapshot.sha256 != authority.get("request_sha256")
        or nonce_text != authority.get("tsa_nonce_hex")
        or hashlib.sha256(message).hexdigest()
        != authority.get("message_imprint_sha256")
        or _sha256(der) != authority.get("request_der_sha256")
        or expected != expected_ref
        or link.exists()
        or link.is_symlink()
        or receipt.exists()
        or receipt.is_symlink()
    ):
        raise WorksetRecoveryIntegrityError("Trusted DER repair predecessor changed")
    snapshot = _publish_additive(
        der_path,
        der,
        root=paths.active_root,
        name="recovered timestamp request DER",
        inputs=inputs,
    )
    if snapshot.raw != der:
        raise WorksetRecoveryIntegrityError("Recovered timestamp DER changed")
    return ActionOutput(
        "trusted_time_request_der",
        _reference(snapshot, paths.active_root),
        {
            "new_nonce_created": False,
            "network_action_performed": False,
            "message_imprint_sha256": hashlib.sha256(message).hexdigest(),
        },
    )


def _anchor_action(
    item: Mapping[str, Any],
    reservation: Reservation,
    paths: RecoveryPaths,
    inputs: set[Path],
) -> ActionOutput:
    authority = item["authority"]
    _, live_paths, events = _live_projection(reservation)
    confirmed_record = authority.get("confirmed_event")
    if not isinstance(confirmed_record, dict):
        raise WorksetRecoveryIntegrityError("Anchor repair lost confirmed event")
    seal_sha = authority.get("seal_entry_sha256")
    target = authority.get("target_date")
    seals = [
        event
        for event in events
        if event.event_type == "issue_batch_sealed"
        and event.entry_sha256 == seal_sha
        and event.target_date == target
    ]
    confirmed = [
        event
        for event in events
        if event.event_type == "anchor_confirmed"
        and event.entry_sha256 == confirmed_record.get("entry_sha256")
        and event.payload.get("sealed_entry_sha256") == seal_sha
    ]
    if len(seals) != 1 or len(confirmed) != 1:
        raise WorksetRecoveryIntegrityError("Anchor repair live predecessor changed")
    stored = live._stored_anchor_payload(
        dict(confirmed[0].payload), sealed_entry_sha256=seal_sha
    )  # noqa: SLF001
    raw = (
        json.dumps(
            stored, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
        )
        + "\n"
    ).encode()
    output = live_paths.anchors / f"{target}_{seal_sha}.json"
    snapshot = _publish_additive(
        output,
        raw,
        root=paths.active_root,
        name="recovered live anchor receipt",
        inputs=inputs,
    )
    if snapshot.raw != raw:
        raise WorksetRecoveryIntegrityError("Recovered anchor receipt changed")
    return ActionOutput(
        "live_anchor_receipt",
        _reference(snapshot, paths.active_root),
        {
            "seal_entry_sha256": seal_sha,
            "confirmed_entry_sha256": confirmed[0].entry_sha256,
            "trusted_anchor_receipt_verified": False,
        },
    )


def _guard_action(
    item: Mapping[str, Any],
    reservation: Reservation,
    paths: RecoveryPaths,
    inputs: set[Path],
) -> ActionOutput:
    del inputs
    authority = item["authority"]
    target = authority.get("target_date")
    _, _, events = _live_projection(reservation)
    boundaries = [
        event
        for event in events
        if event.target_date == target
        and event.event_type in {"issue_batch_opened", "issue_batch_sealed"}
    ]
    profile = guard.load_verified_live_profile()
    guard_paths = guard.runtime_paths(profile, runtime_root=paths.active_root)
    completion = guard_paths.completions / f"{target}.json"
    if boundaries or completion.exists() or completion.is_symlink():
        raise WorksetRecoveryIntegrityError(
            "Guard supersession has an issue boundary/completion"
        )
    evidence = [
        event
        for event in events
        if event.target_date == target
        and event.event_type in {"backfill_not_blind", "outcome_batch_settled"}
    ]
    recorded = authority.get("superseding_live_event")
    if (
        len(evidence) != 1
        or not isinstance(recorded, dict)
        or evidence[0].entry_sha256 != recorded.get("entry_sha256")
    ):
        raise WorksetRecoveryIntegrityError(
            "Guard supersession lacks unique durable backfill evidence"
        )
    return ActionOutput(
        "recovery_receipt_only",
        None,
        {
            "superseding_event_type": evidence[0].event_type,
            "superseding_entry_sha256": evidence[0].entry_sha256,
            "legacy_guard_completion_created": False,
            "guarded_issue_completed": False,
            "superseded_for_old_epoch_recovery": True,
        },
    )


def _perform_action(
    item: Mapping[str, Any],
    reservation: Reservation,
    paths: RecoveryPaths,
    inputs: set[Path],
    hook: ActionHook | None,
    *,
    action: str | None = None,
) -> ActionOutput:
    if hook is not None:
        hooked_item = dict(item)
        hooked_item["transition_action"] = action or item["canonical_successor_state"]
        return hook(hooked_item, reservation, paths)
    successor = action or item["canonical_successor_state"]
    try:
        if successor == "trusted_time_request_der_repaired":
            return _trusted_der_action(item, reservation, paths, inputs)
        if successor == "anchor_receipt_repaired":
            return _anchor_action(item, reservation, paths, inputs)
        if successor == "superseded_by_backfill":
            return _guard_action(item, reservation, paths, inputs)
        raise WorksetRecoveryIntegrityError("Unsupported successor was dispatched")
    except WorksetRecoveryError:
        raise
    except Exception as exc:
        raise WorksetRecoveryIntegrityError(
            f"Local recovery adapter failed:{successor}:{type(exc).__name__}:{exc}"
        ) from exc


def _verify_recorded_action_contract(
    item: Mapping[str, Any], payload: Mapping[str, Any], paths: RecoveryPaths
) -> None:
    """Verify immutable step evidence without replaying obsolete preconditions."""

    action = payload.get("action")
    output = payload.get("action_output")
    authority = item.get("authority")
    if not isinstance(authority, Mapping):
        raise WorksetRecoveryIntegrityError("Recovery action authority changed")
    if action == "trusted_time_request_der_repaired":
        target = authority.get("target_date")
        try:
            trusted_paths = trusted.trusted_time_paths(
                trusted.load_trusted_time_profile(), runtime_root=paths.active_root
            )
            canonical_path = (
                (trusted_paths.request_der / f"{date.fromisoformat(target)}.tsq")
                .relative_to(paths.active_root)
                .as_posix()
            )
        except Exception as exc:
            raise WorksetRecoveryIntegrityError(
                "Recorded trusted-time DER identity changed"
            ) from exc
        expected = ActionOutput(
            "trusted_time_request_der",
            output if isinstance(output, Mapping) else None,
            {
                "new_nonce_created": False,
                "network_action_performed": False,
                "message_imprint_sha256": authority.get("message_imprint_sha256"),
            },
        )
        if (
            not isinstance(output, Mapping)
            or output.get("path") != canonical_path
            or output.get("sha256") != authority.get("request_der_sha256")
        ):
            raise WorksetRecoveryIntegrityError(
                "Recorded trusted-time DER evidence changed"
            )
    elif action == "anchor_receipt_repaired":
        confirmed = authority.get("confirmed_event")
        if not isinstance(confirmed, Mapping) or not isinstance(output, Mapping):
            raise WorksetRecoveryIntegrityError(
                "Recorded anchor repair evidence changed"
            )
        target = authority.get("target_date")
        seal_sha = authority.get("seal_entry_sha256")
        try:
            live_paths = live.runtime_paths(
                live.load_config(), runtime_root=paths.active_root
            )
            canonical_path = (
                (live_paths.anchors / f"{target}_{seal_sha}.json")
                .relative_to(paths.active_root)
                .as_posix()
            )
        except Exception as exc:
            raise WorksetRecoveryIntegrityError(
                "Recorded anchor repair identity changed"
            ) from exc
        if output.get("path") != canonical_path:
            raise WorksetRecoveryIntegrityError(
                "Recorded anchor repair output path changed"
            )
        expected = ActionOutput(
            "live_anchor_receipt",
            output,
            {
                "seal_entry_sha256": authority.get("seal_entry_sha256"),
                "confirmed_entry_sha256": confirmed.get("entry_sha256"),
                "trusted_anchor_receipt_verified": False,
            },
        )
    elif action == "superseded_by_backfill":
        superseding = authority.get("superseding_live_event")
        if not isinstance(superseding, Mapping) or output is not None:
            raise WorksetRecoveryIntegrityError(
                "Recorded guard supersession evidence changed"
            )
        expected = ActionOutput(
            "recovery_receipt_only",
            None,
            {
                "superseding_event_type": superseding.get("event_type"),
                "superseding_entry_sha256": superseding.get("entry_sha256"),
                "legacy_guard_completion_created": False,
                "guarded_issue_completed": False,
                "superseded_for_old_epoch_recovery": True,
            },
        )
    else:
        raise WorksetRecoveryIntegrityError(
            "Recorded recovery action has no postcondition verifier"
        )
    if (
        payload.get("action_output_kind") != expected.kind
        or payload.get("action_output") != expected.reference
        or payload.get("action_semantics") != dict(expected.semantics)
    ):
        raise WorksetRecoveryIntegrityError(
            "Recovery action evidence failed immutable replay"
        )


def _receipt_payload(
    profile: Mapping[str, Any],
    paths: RecoveryPaths,
    reservation: Reservation,
    item: Mapping[str, Any],
    item_intent: registry.ArtifactSnapshot,
    action: ActionOutput,
    *,
    step_index: int,
    transition_action: str,
    previous_step_receipt: registry.ArtifactSnapshot | None,
    completed_at: str,
) -> dict[str, object]:
    plan = _transition_plan(item)
    edges = _plan_edges(plan)
    if transition_action not in edges:
        raise WorksetRecoveryIntegrityError("Transition action is outside its plan")
    next_actions = edges[transition_action]
    terminal = bool(plan["closure_resolved"]) and transition_action in set(
        plan["terminal_actions"]
    )
    step_id = _step_id(item["key_id"], step_index, transition_action)
    return {
        "schema_version": profile["protocol"]["receipt_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "completed_at_utc": completed_at,
        "step_id": step_id,
        "step_index": step_index,
        "key_id": item["key_id"],
        "natural_key": item["natural_key"],
        "namespace_digest": item["namespace_digest"],
        "action": transition_action,
        "transition_plan_sha256": plan["plan_sha256"],
        "previous_step_receipt": (
            _reference(previous_step_receipt, paths.root)
            if previous_step_receipt is not None
            else None
        ),
        "next_actions": next_actions,
        "terminal_for_key": terminal,
        "item_intent": _reference(item_intent, paths.root),
        "manifest": _reference(reservation.manifest_snapshot, reservation.paths.root),
        "action_output_kind": action.kind,
        "action_output": action.reference,
        "action_semantics": dict(action.semantics),
        **_claims(),
    }


def _ensure_receipt(
    profile: Mapping[str, Any],
    paths: RecoveryPaths,
    reservation: Reservation,
    item: Mapping[str, Any],
    item_intent: registry.ArtifactSnapshot,
    action: ActionOutput,
    *,
    step_index: int,
    transition_action: str,
    previous_step_receipt: registry.ArtifactSnapshot | None,
    now: datetime,
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    step_id = _step_id(item["key_id"], step_index, transition_action)
    path = paths.receipts / f"{step_id}.json"
    stable = _receipt_payload(
        profile,
        paths,
        reservation,
        item,
        item_intent,
        action,
        step_index=step_index,
        transition_action=transition_action,
        previous_step_receipt=previous_step_receipt,
        completed_at="",
    )
    if path.exists():
        payload, snapshot = _strict_json(path, name="recovery receipt")
        if {**payload, "completed_at_utc": ""} != stable:
            raise WorksetRecoveryIntegrityError("Recovery receipt changed")
        return payload, snapshot
    payload = _receipt_payload(
        profile,
        paths,
        reservation,
        item,
        item_intent,
        action,
        step_index=step_index,
        transition_action=transition_action,
        previous_step_receipt=previous_step_receipt,
        completed_at=_utc_text(now),
    )
    snapshot = _publish(
        path, _canonical_bytes(payload), root=paths.root, name="recovery receipt"
    )
    return payload, snapshot


def _load_receipts(
    profile: Mapping[str, Any],
    paths: RecoveryPaths,
    reservation: Reservation,
    global_snapshot: registry.ArtifactSnapshot,
    items: Mapping[str, Mapping[str, Any]],
    intents: Mapping[str, Path],
    *,
    probe_actions: bool = True,
) -> dict[str, tuple[dict[str, Any], registry.ArtifactSnapshot]]:
    records = _strict_named_json(
        paths.receipts, suffix=".json", name="recovery step receipts"
    )
    loaded = {
        step_id: _strict_json(path, name="recovery step receipt")
        for step_id, path in records.items()
    }
    natural_to_id = {item["natural_key"]: key_id for key_id, item in items.items()}
    groups: dict[str, list[tuple[str, dict[str, Any], registry.ArtifactSnapshot]]] = {}
    for filename_step_id, (payload, snapshot) in loaded.items():
        _exact(
            payload,
            {
                "schema_version",
                "profile_id",
                "profile_sha256",
                "completed_at_utc",
                "step_id",
                "step_index",
                "key_id",
                "natural_key",
                "namespace_digest",
                "action",
                "transition_plan_sha256",
                "previous_step_receipt",
                "next_actions",
                "terminal_for_key",
                "item_intent",
                "manifest",
                "action_output_kind",
                "action_output",
                "action_semantics",
                *TRUE_CAPABILITIES,
                *FALSE_CLAIMS,
            },
            name="recovery step receipt",
        )
        key_id = payload.get("key_id")
        item = items.get(key_id) if isinstance(key_id, str) else None
        step_index = payload.get("step_index")
        action = payload.get("action")
        if (
            item is None
            or not isinstance(step_index, int)
            or isinstance(step_index, bool)
            or step_index < 0
            or not isinstance(action, str)
            or payload.get("schema_version")
            != profile["protocol"]["receipt_schema_version"]
            or payload.get("profile_id") != profile["profile_id"]
            or payload.get("profile_sha256") != profile["_profile_sha256"]
            or payload.get("step_id") != filename_step_id
            or payload.get("natural_key") != item["natural_key"]
            or payload.get("namespace_digest") != item["namespace_digest"]
            or payload.get("manifest")
            != _reference(reservation.manifest_snapshot, reservation.paths.root)
            or any(payload.get(name) is not True for name in TRUE_CAPABILITIES)
            or any(payload.get(name) is not False for name in FALSE_CLAIMS)
        ):
            raise WorksetRecoveryIntegrityError(
                "Recovery step receipt semantics changed"
            )
        _parse_utc(payload["completed_at_utc"], name="recovery step receipt time")
        groups.setdefault(key_id, []).append((filename_step_id, payload, snapshot))

    result: dict[str, tuple[dict[str, Any], registry.ArtifactSnapshot]] = {}
    for key_id, rows in groups.items():
        item = items[key_id]
        plan = _transition_plan(item)
        edges = _plan_edges(plan)
        ordered_rows = sorted(rows, key=lambda row: row[1].get("step_index", -1))
        previous: tuple[dict[str, Any], registry.ArtifactSnapshot] | None = None
        for expected_index, (step_id, payload, snapshot) in enumerate(ordered_rows):
            action = payload.get("action")
            index = payload.get("step_index")
            allowed = (
                action == plan["initial_action"]
                if expected_index == 0
                else previous is not None and action in previous[0]["next_actions"]
            )
            expected_next = edges.get(action) if isinstance(action, str) else None
            expected_terminal = bool(plan["closure_resolved"]) and action in set(
                plan["terminal_actions"]
            )
            expected_previous = (
                _reference(previous[1], paths.root) if previous is not None else None
            )
            if (
                not isinstance(index, int)
                or isinstance(index, bool)
                or index != expected_index
                or not isinstance(action, str)
                or not allowed
                or expected_next is None
                or step_id != _step_id(key_id, expected_index, action)
                or payload.get("transition_plan_sha256") != plan["plan_sha256"]
                or payload.get("previous_step_receipt") != expected_previous
                or payload.get("next_actions") != expected_next
                or payload.get("terminal_for_key") is not expected_terminal
                or (previous is not None and previous[0]["terminal_for_key"] is True)
            ):
                raise WorksetRecoveryIntegrityError(
                    "Recovery step receipt chain changed"
                )
            result[step_id] = (payload, snapshot)
            previous = (payload, snapshot)

    terminal_by_key: dict[str, tuple[dict[str, Any], registry.ArtifactSnapshot]] = {}
    for key_id, rows in groups.items():
        latest = max(rows, key=lambda row: row[1]["step_index"])
        if latest[1]["terminal_for_key"] is True:
            terminal_by_key[key_id] = (latest[1], latest[2])

    def dependency_references(item: Mapping[str, Any]) -> list[dict[str, object]]:
        expected = []
        for natural_key in item["dependency_keys"]:
            dependency_id = natural_to_id[natural_key]
            dependency = terminal_by_key.get(dependency_id)
            if dependency is None:
                raise WorksetRecoveryIntegrityError(
                    "Recovery step dependency lacks a terminal receipt"
                )
            expected.append(_reference(dependency[1], paths.root))
        return expected

    intent_records: dict[str, tuple[dict[str, Any], registry.ArtifactSnapshot]] = {}
    intent_positions: set[tuple[str, int]] = set()
    pending_intents = 0
    for filename_step_id, intent_path in intents.items():
        intent_payload, intent_snapshot = _strict_json(
            intent_path, name="recovery step intent"
        )
        _exact(
            intent_payload,
            {
                "schema_version",
                "profile_id",
                "profile_sha256",
                "step_id",
                "step_index",
                "key_id",
                "natural_key",
                "namespace_digest",
                "action",
                "transition_plan_sha256",
                "previous_step_receipt",
                "dependency_keys",
                "dependency_receipts",
                "global_intent",
                "manifest",
                "adapter_provenance",
                "created_at_utc",
            },
            name="recovery step intent",
        )
        key_id = intent_payload.get("key_id")
        item = items.get(key_id) if isinstance(key_id, str) else None
        step_index = intent_payload.get("step_index")
        action = intent_payload.get("action")
        if (
            item is None
            or not isinstance(step_index, int)
            or isinstance(step_index, bool)
            or step_index < 0
            or not isinstance(action, str)
        ):
            raise WorksetRecoveryIntegrityError("Recovery step intent is orphaned")
        plan = _transition_plan(item)
        rows = sorted(groups.get(key_id, []), key=lambda row: row[1]["step_index"])
        previous = (
            rows[step_index - 1] if step_index > 0 and step_index <= len(rows) else None
        )
        allowed = (
            action == plan["initial_action"]
            if step_index == 0
            else previous is not None and action in previous[1]["next_actions"]
        )
        expected_previous = (
            _reference(previous[2], paths.root) if previous is not None else None
        )
        position = (key_id, step_index)
        if (
            position in intent_positions
            or not allowed
            or step_index > len(rows)
            or intent_payload.get("schema_version")
            != profile["protocol"]["item_intent_schema_version"]
            or intent_payload.get("profile_id") != profile["profile_id"]
            or intent_payload.get("profile_sha256") != profile["_profile_sha256"]
            or intent_payload.get("step_id") != filename_step_id
            or filename_step_id != _step_id(key_id, step_index, action)
            or intent_payload.get("natural_key") != item["natural_key"]
            or intent_payload.get("namespace_digest") != item["namespace_digest"]
            or intent_payload.get("transition_plan_sha256") != plan["plan_sha256"]
            or intent_payload.get("previous_step_receipt") != expected_previous
            or intent_payload.get("dependency_keys") != item["dependency_keys"]
            or intent_payload.get("dependency_receipts") != dependency_references(item)
            or intent_payload.get("global_intent")
            != _reference(global_snapshot, paths.root)
            or intent_payload.get("manifest")
            != _reference(reservation.manifest_snapshot, reservation.paths.root)
            or intent_payload.get("adapter_provenance") != _provenance()
        ):
            raise WorksetRecoveryIntegrityError(
                "Recovery step intent semantics changed"
            )
        _parse_utc(intent_payload["created_at_utc"], name="recovery step intent time")
        if step_index == len(rows):
            pending_intents += 1
        intent_positions.add(position)
        intent_records[filename_step_id] = (intent_payload, intent_snapshot)

    if pending_intents > 1:
        raise WorksetRecoveryIntegrityError(
            "Recovery has branched pending step intents"
        )

    for step_id, (payload, _) in result.items():
        intent_record = intent_records.get(step_id)
        if intent_record is None:
            raise WorksetRecoveryIntegrityError("Recovery step receipt is orphaned")
        _, intent_snapshot = intent_record
        if payload.get("item_intent") != _reference(intent_snapshot, paths.root):
            raise WorksetRecoveryIntegrityError("Recovery receipt intent changed")
        output = payload["action_output"]
        if output is not None:
            reference = _exact(
                output, {"path", "sha256", "size_bytes"}, name="action output"
            )
            try:
                output_path = registry._contained(  # noqa: SLF001
                    paths.active_root, reference["path"], name="action output"
                )
                checked_output = registry._read_regular(  # noqa: SLF001
                    output_path,
                    name="action output",
                    maximum_bytes=64 * 1024 * 1024,
                )
            except registry.EpochRegistryError as exc:
                raise WorksetRecoveryIntegrityError(str(exc)) from exc
            if (
                checked_output.sha256 != reference["sha256"]
                or checked_output.size_bytes != reference["size_bytes"]
            ):
                raise WorksetRecoveryIntegrityError(
                    "Recovery action output failed replay"
                )
        if probe_actions:
            item = items[payload["key_id"]]
            _verify_recorded_action_contract(item, payload, paths)
    return result


def _receipt_chains(
    receipts: Mapping[str, tuple[dict[str, Any], registry.ArtifactSnapshot]],
) -> dict[str, list[tuple[dict[str, Any], registry.ArtifactSnapshot]]]:
    result: dict[str, list[tuple[dict[str, Any], registry.ArtifactSnapshot]]] = {}
    for payload, snapshot in receipts.values():
        result.setdefault(payload["key_id"], []).append((payload, snapshot))
    for rows in result.values():
        rows.sort(key=lambda row: row[0]["step_index"])
    return result


def _load_events(
    profile: Mapping[str, Any],
    paths: RecoveryPaths,
    receipts: Mapping[str, tuple[dict[str, Any], registry.ArtifactSnapshot]],
) -> tuple[list[dict[str, Any]], str]:
    if paths.events.is_symlink():
        raise WorksetRecoveryIntegrityError("Recovery events path is a symlink")
    if paths.events.exists():
        try:
            entries = manifest._strict_entries(  # noqa: SLF001
                paths.events, name="recovery events"
            )
        except manifest.WorksetManifestError as exc:
            raise WorksetRecoveryIntegrityError(str(exc)) from exc
    else:
        entries = ()
    events: list[dict[str, Any]] = []
    previous = ZERO_HASH
    seen: set[str] = set()
    for sequence, path in enumerate(entries, 1):
        payload, _ = _strict_json(path, name="recovery event")
        body = dict(payload)
        entry = body.pop("entry_sha256", None)
        step_id = payload.get("step_id")
        receipt = receipts.get(step_id) if isinstance(step_id, str) else None
        if (
            set(payload)
            != {
                "schema_version",
                "profile_id",
                "profile_sha256",
                "sequence_id",
                "previous_entry_sha256",
                "event_type",
                "recorded_at_utc",
                "step_id",
                "key_id",
                "receipt",
                "entry_sha256",
            }
            or payload.get("schema_version")
            != profile["protocol"]["event_schema_version"]
            or payload.get("profile_id") != profile["profile_id"]
            or payload.get("profile_sha256") != profile["_profile_sha256"]
            or payload.get("sequence_id") != sequence
            or payload.get("previous_entry_sha256") != previous
            or payload.get("event_type") != profile["protocol"]["event_type"]
            or entry != _sha256(_canonical_bytes(body))
            or path.name != f"{sequence:020d}-{entry}.json"
            or receipt is None
            or payload.get("receipt") != _reference(receipt[1], paths.root)
            or step_id in seen
            or payload.get("key_id") != receipt[0].get("key_id")
        ):
            raise WorksetRecoveryIntegrityError("Recovery event chain changed")
        _parse_utc(payload["recorded_at_utc"], name="recovery event time")
        seen.add(step_id)
        previous = entry
        events.append(payload)
    if seen != set(receipts):
        missing = set(receipts) - seen
        if len(missing) != 1:
            raise WorksetRecoveryIntegrityError("Recovery receipts/events branched")
    return events, previous


def _append_event(
    profile: Mapping[str, Any],
    paths: RecoveryPaths,
    step_id: str,
    key_id: str,
    receipt: registry.ArtifactSnapshot,
    events: Sequence[Mapping[str, Any]],
    previous: str,
    *,
    now: datetime,
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    sequence = len(events) + 1
    body = {
        "schema_version": profile["protocol"]["event_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "sequence_id": sequence,
        "previous_entry_sha256": previous,
        "event_type": profile["protocol"]["event_type"],
        "recorded_at_utc": _utc_text(now),
        "step_id": step_id,
        "key_id": key_id,
        "receipt": _reference(receipt, paths.root),
    }
    payload = {**body, "entry_sha256": _sha256(_canonical_bytes(body))}
    path = paths.events / f"{sequence:020d}-{payload['entry_sha256']}.json"
    snapshot = _publish(
        path, _canonical_bytes(payload), root=paths.root, name="recovery event"
    )
    return payload, snapshot


def _write_status(
    profile: Mapping[str, Any],
    paths: RecoveryPaths,
    *,
    now: datetime,
    status: str,
    reason: str,
    key_id: str | None,
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
        "key_id": key_id,
        "receipt_path": str(receipt) if receipt else None,
        "event_path": str(event) if event else None,
        "cache_authority": False,
        **_claims(),
    }
    try:
        registry._atomic_cache(
            paths.status,
            _canonical_bytes(payload),
            root=paths.root,
            name="recovery status",
        )  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise WorksetRecoveryIntegrityError(str(exc)) from exc


def _coordinate_epoch_workset_recovery(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    registry_root: Path | None = None,
    active_root: Path | None = None,
    shadow_root: Path | None = None,
    clock: Callable[[], datetime] | None = None,
    action_hook: ActionHook | None = None,
    load_reservation: LoadReservation | None = None,
) -> RecoveryResult:
    profile = load_workset_recovery_profile(config_path)
    paths = recovery_paths(
        profile,
        registry_root=registry_root,
        active_root=active_root,
        shadow_root=shadow_root,
    )
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    handles: list[BinaryIO] = []
    all_locks_acquired = False
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
                raise WorksetRecoveryBusyError(str(exc)) from exc
            except drain.EpochDrainError as exc:
                raise WorksetRecoveryIntegrityError(str(exc)) from exc
        all_locks_acquired = True
        reservation = (load_reservation or _load_reservation)(paths)
        if reservation is None:
            global_present, child_present = _recovery_authority_state(paths)
            if global_present or child_present:
                raise WorksetRecoveryIntegrityError(
                    "Recovery authority exists without its immutable reservation"
                )
            reason = (
                "exact admission cut and immutable workset reservation are unavailable"
            )
            _write_status(
                profile,
                paths,
                now=now,
                status="waiting_for_workset_reservation",
                reason=reason,
                key_id=None,
                receipt=None,
                event=None,
            )
            return RecoveryResult(
                "waiting_for_workset_reservation", reason, paths.status
            )
        ordered, _, graph_digest = _dag(reservation)
        global_present, child_present = _recovery_authority_state(paths)
        if child_present and not global_present:
            raise WorksetRecoveryIntegrityError(
                "Recovery child authority exists without the global intent"
            )
        _, global_snapshot = _ensure_global_intent(
            profile, paths, reservation, ordered, graph_digest, now=now
        )
        item_by_id = {item["key_id"]: item for item in ordered}
        intents = _strict_named_json(
            paths.item_intents, suffix=".json", name="recovery item intents"
        )
        receipts = _load_receipts(
            profile,
            paths,
            reservation,
            global_snapshot,
            item_by_id,
            intents,
            probe_actions=action_hook is None,
        )
        chains = _receipt_chains(receipts)
        events, previous = _load_events(profile, paths, receipts)
        event_keys = {event["step_id"] for event in events}
        missing_event = set(receipts) - event_keys
        if missing_event:
            step_id = next(iter(missing_event))
            receipt_payload, receipt_snapshot = receipts[step_id]
            key_id = receipt_payload["key_id"]
            event, event_snapshot = _append_event(
                profile,
                paths,
                step_id,
                key_id,
                receipt_snapshot,
                events,
                previous,
                now=now,
            )
            reason = "exact recovery receipt forward-adopted into the event chain"
            _write_status(
                profile,
                paths,
                now=now,
                status="recovery_event_forward_adopted",
                reason=reason,
                key_id=key_id,
                receipt=receipt_snapshot.path,
                event=event_snapshot.path,
            )
            return RecoveryResult(
                "recovery_event_forward_adopted",
                reason,
                paths.status,
                key_id,
                receipt_snapshot.path,
                event_snapshot.path,
            )
        completed_natural = {
            item_by_id[key_id]["natural_key"]
            for key_id, rows in chains.items()
            if rows[-1][0]["terminal_for_key"] is True
        }
        candidates: list[
            tuple[
                Mapping[str, Any],
                int,
                str,
                registry.ArtifactSnapshot | None,
            ]
        ] = []
        for item in ordered:
            if not set(item["dependency_keys"]) <= completed_natural:
                continue
            rows = chains.get(item["key_id"], [])
            if rows and rows[-1][0]["terminal_for_key"] is True:
                continue
            plan = _transition_plan(item)
            if not rows:
                next_actions = [plan["initial_action"]]
            else:
                next_actions = rows[-1][0]["next_actions"]
            if len(next_actions) != 1:
                continue
            transition_action = next_actions[0]
            if not _item_adapter_supported(item, transition_action):
                continue
            candidates.append(
                (
                    item,
                    len(rows),
                    transition_action,
                    rows[-1][1] if rows else None,
                )
            )
        supported = candidates
        if not supported:
            reason = (
                "no dependency-ready transition step has one reviewed deterministic "
                "adapter and unambiguous next edge"
            )
            _write_status(
                profile,
                paths,
                now=now,
                status="waiting_for_supported_ready_key",
                reason=reason,
                key_id=None,
                receipt=None,
                event=None,
            )
            return RecoveryResult(
                "waiting_for_supported_ready_key", reason, paths.status
            )
        item, step_index, transition_action, previous_step_receipt = supported[0]
        inputs = _cas_item_artifacts(item, reservation)
        dependency_snapshots: list[registry.ArtifactSnapshot] = []
        for dependency in item["dependency_keys"]:
            dependency_item = next(
                candidate
                for candidate in ordered
                if candidate["natural_key"] == dependency
            )
            dependency_rows = chains.get(dependency_item["key_id"], [])
            if (
                not dependency_rows
                or dependency_rows[-1][0]["terminal_for_key"] is not True
            ):
                raise WorksetRecoveryIntegrityError(
                    "Ready key lost a deep-verified terminal dependency receipt"
                )
            dependency_snapshots.append(dependency_rows[-1][1])
        item_intent = _ensure_item_intent(
            profile,
            paths,
            reservation,
            global_snapshot,
            item,
            dependency_snapshots,
            previous_step_receipt,
            step_index=step_index,
            action=transition_action,
            now=now,
        )
        action = _perform_action(
            item,
            reservation,
            paths,
            inputs,
            action_hook,
            action=transition_action,
        )
        receipt_payload, receipt_snapshot = _ensure_receipt(
            profile,
            paths,
            reservation,
            item,
            item_intent,
            action,
            step_index=step_index,
            transition_action=transition_action,
            previous_step_receipt=previous_step_receipt,
            now=now,
        )
        if receipt_payload["key_id"] != item["key_id"]:
            raise WorksetRecoveryIntegrityError("Recovery receipt key changed")
        event, event_snapshot = _append_event(
            profile,
            paths,
            receipt_payload["step_id"],
            item["key_id"],
            receipt_snapshot,
            events,
            previous,
            now=now,
        )
        terminal = receipt_payload["terminal_for_key"] is True
        reason = (
            "one manifest-keyed terminal recovery step completed"
            if terminal
            else "one manifest-keyed nonterminal transition step completed"
        )
        status = "recovery_item_completed" if terminal else "recovery_step_completed"
        _write_status(
            profile,
            paths,
            now=now,
            status=status,
            reason=reason,
            key_id=item["key_id"],
            receipt=receipt_snapshot.path,
            event=event_snapshot.path,
        )
        return RecoveryResult(
            status,
            reason,
            paths.status,
            item["key_id"],
            receipt_snapshot.path,
            event_snapshot.path,
        )
    except WorksetRecoveryBusyError:
        raise
    except WorksetRecoveryError as exc:
        if all_locks_acquired:
            try:
                _write_status(
                    profile,
                    paths,
                    now=now,
                    status="blocked_integrity",
                    reason=f"{type(exc).__name__}:{exc}",
                    key_id=None,
                    receipt=None,
                    event=None,
                )
            except WorksetRecoveryError:
                pass
        raise
    except (registry.EpochRegistryError, manifest.WorksetManifestError) as exc:
        wrapped = WorksetRecoveryIntegrityError(
            f"Recovery authority validation failed:{type(exc).__name__}:{exc}"
        )
        if all_locks_acquired:
            try:
                _write_status(
                    profile,
                    paths,
                    now=now,
                    status="blocked_integrity",
                    reason=f"{type(wrapped).__name__}:{wrapped}",
                    key_id=None,
                    receipt=None,
                    event=None,
                )
            except WorksetRecoveryError:
                pass
        raise wrapped from exc
    finally:
        try:
            drain._release_locks(handles)  # noqa: SLF001
        except drain.EpochDrainError as exc:
            if sys.exc_info()[0] is None:
                raise WorksetRecoveryIntegrityError(str(exc)) from exc


def coordinate_epoch_workset_recovery(
    *, config_path: Path = DEFAULT_CONFIG_PATH
) -> RecoveryResult:
    """Run one reviewed machine-only deterministic local recovery step."""
    return _coordinate_epoch_workset_recovery(config_path=config_path)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = coordinate_epoch_workset_recovery(config_path=args.config)
    except WorksetRecoveryBusyError as exc:
        print(json.dumps({"status": "busy", "reason": str(exc)}, sort_keys=True))
        return 3
    except WorksetRecoveryError as exc:
        print(
            json.dumps(
                {"status": "blocked_integrity", "reason": str(exc)}, sort_keys=True
            )
        )
        return 2
    print(
        json.dumps(
            {
                "status": result.status,
                "reason": result.reason,
                "status_path": str(result.status_path),
                "key_id": result.key_id,
                "receipt_path": str(result.receipt_path)
                if result.receipt_path
                else None,
                "event_path": str(result.event_path) if result.event_path else None,
                "bounded_workset_recovery_implemented": result.bounded_workset_recovery_implemented,
                "lifecycle_authority": result.lifecycle_authority,
                "transition_authority": result.transition_authority,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
