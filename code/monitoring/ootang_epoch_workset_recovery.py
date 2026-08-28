"""Advance one deterministic manifest-keyed old-epoch recovery action."""

from __future__ import annotations

import argparse
import base64
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import stat
import sys
from typing import Any, BinaryIO
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[2]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_epoch_drain as drain  # noqa: E402
from monitoring import ootang_epoch_registry as registry  # noqa: E402
from monitoring import ootang_epoch_workset_manifest as manifest  # noqa: E402
from monitoring import ootang_live_ledger as live_ledger  # noqa: E402
from monitoring import ootang_live_ledger_cas_v1 as live_cas  # noqa: E402
from monitoring import ootang_prequential_live as live  # noqa: E402
from monitoring import ootang_trusted_time_shadow_core as trusted  # noqa: E402
from monitoring import ootang_verified_live as guard  # noqa: E402


DEFAULT_CONFIG_PATH = ROOT / "config" / "ootang_epoch_workset_recovery.v1.json"
DEFAULT_CONFIG_SHA256 = (
    "2fd37e48a5b3eeb8a321b559f9a4e162f0abb9de32f5e930bff9956b7e488177"
)
MAX_CONTROL_BYTES = 4 * 1024 * 1024
ZERO_HASH = "0" * 64
LOCK_ORDER = ("manager", "cycle", "replay", "shadow")
SUPPORTED_SUCCESSORS = (
    "trusted_time_request_der_repaired",
    "anchor_receipt_repaired",
    "anchor_request_recorded",
    "superseded_by_backfill",
)
INTENT_PREPARATION_SUCCESSORS = ("anchor_result_recorded",)
ANCHOR_REQUEST_CONTRACT_SCHEMA = "ootang_live_anchor_request_action_contract_v1"
ANCHOR_RESULT_REQUEST_CONTRACT_SCHEMA = (
    "ootang_live_anchor_result_request_action_contract_v1"
)
ANCHOR_RESULT_MAXIMUM_RESPONSE_BYTES = 1024 * 1024
ANCHOR_RESULT_USER_AGENT = "ootang-workset-recovery/1"
LIVE_LEDGER_CAS_TIMEOUT_SECONDS = 0.25
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
    "live_anchor_request_adapter_implemented",
    "live_anchor_result_request_intent_implemented",
    "live_anchor_result_response_observation_implemented",
    "ledger_mutation_recovery_implemented",
)
FALSE_CLAIMS = (
    "bounded_workset_recovery_implemented",
    "all_reserved_items_settled",
    "all_reserved_successors_supported",
    "terminal_transition_closure_implemented",
    "derived_future_work_reservation_implemented",
    "all_transition_branches_supported",
    "network_recovery_implemented",
    "live_anchor_result_adapter_implemented",
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
        "expected_sha256": "b443da5fd92eb2e48e33918c3e6090be0e53fe2182584f6bb0ccee050dfb327e",
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
    "anchor_result_response_objects": "external_anchor_response_objects",
    "anchor_result_response_links": "external_anchor_response_links",
    "receipts": "receipts",
    "events": "events",
    "status": "status.json",
    "anchor_result_dispatch_lock": "external_anchor_dispatch.lock",
    "manager_lock": "manager.lock",
    "active_root": "runtime/ootang_prequential_live_v1",
    "shadow_root": "runtime/ootang_prequential_calibration_shadow_v1",
    "cycle_lock": "prequential_cycle.lock",
    "replay_lock": "issue_replay.lock",
    "shadow_lock": "runner.lock",
}
EXPECTED_PROTOCOL = {
    "intent_schema_version": "ootang_epoch_workset_recovery_intent_v5",
    "item_intent_schema_version": "ootang_epoch_workset_recovery_step_intent_v5",
    "receipt_schema_version": "ootang_epoch_workset_recovery_step_receipt_v5",
    "event_schema_version": "ootang_epoch_workset_recovery_step_event_v2",
    "status_schema_version": "ootang_epoch_workset_recovery_status_v5",
    "anchor_result_response_observation_schema_version": (
        "ootang_epoch_workset_anchor_result_response_observation_v1"
    ),
    "anchor_result_response_link_schema_version": (
        "ootang_epoch_workset_anchor_result_response_link_v1"
    ),
    "event_type": "epoch_workset_transition_step_recorded",
    "transition_contract_schema_version": TRANSITION_CONTRACT["schema_version"],
    "transition_contract_sha256": TRANSITION_CONTRACT_SHA256,
    "terminal_completion_policy": ("only_terminal_step_receipts_satisfy_dependencies"),
    "canonical_json": "utf8_sort_keys_compact_no_nan_trailing_lf",
    "surviving_lock_order": list(LOCK_ORDER),
    "supported_successors": list(SUPPORTED_SUCCESSORS),
    "intent_preparation_successors": list(INTENT_PREPARATION_SUCCESSORS),
    "maximum_items": 4096,
    "maximum_control_bytes": MAX_CONTROL_BYTES,
    "initial_previous_entry_sha256": ZERO_HASH,
    "poll_policy": (
        "advance_at_most_one_ready_supported_key_or_prepare_one_external_intent"
    ),
}


class WorksetRecoveryError(RuntimeError):
    """Base recovery error."""


class WorksetRecoveryConfigError(WorksetRecoveryError):
    """The reviewed recovery profile changed."""


class WorksetRecoveryIntegrityError(WorksetRecoveryError):
    """Immutable recovery authority or a local predecessor changed."""


class WorksetRecoveryBusyError(WorksetRecoveryError):
    """A surviving lock is held."""


class WorksetRecoveryExternalWait(WorksetRecoveryError):
    """A mutable external prerequisite is not ready for intent freezing."""


class WorksetRecoveryNetworkWait(WorksetRecoveryError):
    """A bounded network attempt is retryable or has ambiguous delivery."""


class _AnchorResultProtocolFailure(RuntimeError):
    """A deterministic remote response cannot become an anchor candidate."""

    def __init__(self, stage: str, code: str) -> None:
        super().__init__(code)
        self.stage = stage
        self.code = code


@dataclass(frozen=True)
class RecoveryPaths:
    registry_root: Path
    root: Path
    global_intent: Path
    item_intents: Path
    anchor_result_response_objects: Path
    anchor_result_response_links: Path
    receipts: Path
    events: Path
    status: Path
    anchor_result_dispatch_lock: Path
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
    external_anchor_response_observation_path: Path | None = None
    network_action_performed: bool = False


@dataclass(frozen=True)
class AnchorResultDispatchPlan:
    profile: Mapping[str, Any]
    paths: RecoveryPaths
    key_id: str
    step_id: str
    item_intent: registry.ArtifactSnapshot
    action_contract: Mapping[str, Any]


@dataclass(frozen=True)
class AnchorResultTransportResponse:
    body: bytes
    status_code: int
    media_type: str
    charset: str | None
    content_encoding: str | None
    final_url: str


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


@dataclass(frozen=True)
class FrozenLivePrefix:
    profile: dict[str, Any]
    paths: live.RuntimePaths
    prerequisites: live.Prerequisites
    projection: live.LiveProjection
    frozen_events: tuple[live_ledger.LedgerEvent, ...]
    current_events: tuple[live_ledger.LedgerEvent, ...]
    expected_pre_head: live_cas.LiveLedgerPreHeadV1


ActionHook = Callable[[Mapping[str, Any], Reservation, RecoveryPaths], ActionOutput]
LoadReservation = Callable[[RecoveryPaths], Reservation | None]
AnchorResultTransport = Callable[
    [Mapping[str, Any], str], AnchorResultTransportResponse
]


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
        registry_root=registry_path,
        root=root,
        global_intent=_child(
            root, profile["runtime"]["global_intent"], name="global intent"
        ),
        item_intents=_child(
            root, profile["runtime"]["item_intents"], name="item intents"
        ),
        anchor_result_response_objects=_child(
            root,
            profile["runtime"]["anchor_result_response_objects"],
            name="anchor result response objects",
        ),
        anchor_result_response_links=_child(
            root,
            profile["runtime"]["anchor_result_response_links"],
            name="anchor result response links",
        ),
        receipts=_child(root, profile["runtime"]["receipts"], name="receipts"),
        events=_child(root, profile["runtime"]["events"], name="events"),
        status=_child(root, profile["runtime"]["status"], name="status"),
        anchor_result_dispatch_lock=_child(
            root,
            profile["runtime"]["anchor_result_dispatch_lock"],
            name="anchor result dispatch lock",
        ),
        manager_lock=_child(
            registry_path, profile["runtime"]["manager_lock"], name="manager lock"
        ),
        active_root=active,
        shadow_root=shadow,
        cycle_lock=_child(active, profile["runtime"]["cycle_lock"], name="cycle lock"),
        replay_lock=_child(
            active, profile["runtime"]["replay_lock"], name="replay lock"
        ),
        shadow_lock=_child(
            shadow, profile["runtime"]["shadow_lock"], name="shadow lock"
        ),
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


def _item_intent_preparable(item: Mapping[str, Any], action: str | None = None) -> bool:
    selected = action or item.get("canonical_successor_state")
    return (
        item.get("family") == "live_outstanding"
        and selected in INTENT_PREPARATION_SUCCESSORS
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
        if not (
            _item_adapter_supported(item, str(plan["initial_action"]))
            or _item_intent_preparable(item, str(plan["initial_action"]))
        )
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
        "initial_step_intent_preparable_key_ids": [
            item["key_id"]
            for item, plan in zip(ordered, plans, strict=True)
            if _item_intent_preparable(item, str(plan["initial_action"]))
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


def _anchor_result_observation_step_ids(
    paths: RecoveryPaths,
) -> tuple[set[str], set[str]]:
    links = set(
        _strict_named_json(
            paths.anchor_result_response_links,
            suffix=".json",
            name="anchor result response links",
        )
    )
    root = paths.anchor_result_response_objects
    if not root.exists() and not root.is_symlink():
        return links, set()
    try:
        if not stat.S_ISDIR(os.lstat(root).st_mode):
            raise WorksetRecoveryIntegrityError(
                "Anchor result response objects path is not a real directory"
            )
        object_steps: set[str] = set()
        for directory in sorted(root.iterdir(), key=lambda path: path.name):
            mode = os.lstat(directory).st_mode
            step_id = directory.name
            if (
                not stat.S_ISDIR(mode)
                or len(step_id) != 64
                or any(character not in "0123456789abcdef" for character in step_id)
            ):
                raise WorksetRecoveryIntegrityError(
                    "Anchor result response objects contain an unknown entry"
                )
            _strict_named_json(
                directory,
                suffix=".json",
                name="anchor result response object set",
            )
            object_steps.add(step_id)
    except WorksetRecoveryError:
        raise
    except OSError as exc:
        raise WorksetRecoveryIntegrityError(
            "Cannot inspect anchor result response objects"
        ) from exc
    return links, object_steps


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
    response_links, response_object_steps = _anchor_result_observation_step_ids(paths)
    child_present = child_present or bool(response_links or response_object_steps)
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


def _hash_text(value: object, *, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise WorksetRecoveryIntegrityError(f"{name} is not a lowercase SHA-256")
    return value


def _frozen_live_prefix(reservation: Reservation) -> FrozenLivePrefix:
    tip = _exact(
        reservation.manifest.get("frozen_live_upper_tip"),
        {"old_live_epoch_id", "live_event_count", "live_terminal_sha256"},
        name="frozen live upper tip",
    )
    epoch_id = tip["old_live_epoch_id"]
    count = tip["live_event_count"]
    terminal = _hash_text(tip["live_terminal_sha256"], name="frozen live terminal")
    if not isinstance(epoch_id, str) or not epoch_id or epoch_id.strip() != epoch_id:
        raise WorksetRecoveryIntegrityError("Frozen live epoch id changed")
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise WorksetRecoveryIntegrityError("Frozen live event count changed")

    try:
        profile = live.load_config()
        paths = live.runtime_paths(profile, runtime_root=reservation.paths.active_root)
        prerequisites = live.load_prerequisites(profile, paths)
        if prerequisites is None:
            raise WorksetRecoveryIntegrityError("Frozen live prerequisites disappeared")
        events = live._ReadOnlyAppendOnlyLedger(paths.ledger).read_events()  # noqa: SLF001
        if len(events) < count:
            raise WorksetRecoveryIntegrityError(
                "Current live chain is shorter than its frozen prefix"
            )
        frozen_events = events[:count]
        frozen_head = frozen_events[-1]
        if frozen_head.sequence_id != count or frozen_head.entry_sha256 != terminal:
            raise WorksetRecoveryIntegrityError(
                "Frozen live prefix tip changed at its original position"
            )
        live._reconstruct_projection(events, profile, prerequisites)  # noqa: SLF001
        projection = live._reconstruct_projection(  # noqa: SLF001
            frozen_events, profile, prerequisites
        )
    except WorksetRecoveryError:
        raise
    except Exception as exc:
        if _is_sqlite_busy(exc):
            raise WorksetRecoveryBusyError(
                "Live ledger verification lock is busy"
            ) from exc
        raise WorksetRecoveryIntegrityError(
            f"Frozen live prefix replay failed:{type(exc).__name__}:{exc}"
        ) from exc
    if projection.epoch_id != epoch_id:
        raise WorksetRecoveryIntegrityError("Frozen live prefix epoch changed")
    return FrozenLivePrefix(
        profile=profile,
        paths=paths,
        prerequisites=prerequisites,
        projection=projection,
        frozen_events=tuple(frozen_events),
        current_events=tuple(events),
        expected_pre_head=live_cas.LiveLedgerPreHeadV1(
            epoch_id=epoch_id,
            event_count=count,
            sequence_id=count,
            entry_sha256=terminal,
        ),
    )


_EVENT_SPEC_KEYS = {
    "event_key",
    "event_type",
    "target_date",
    "station",
    "issue_id",
    "protocol_config_sha256",
    "code_sha256",
    "environment_sha256",
    "input_manifest_sha256",
    "model_manifest_sha256",
    "state_before_sha256",
    "state_after_sha256",
    "payload",
}


def _event_spec_payload(spec: live_ledger.EventSpec) -> dict[str, object]:
    return {
        "event_key": spec.event_key,
        "event_type": spec.event_type,
        "target_date": spec.target_date,
        "station": spec.station,
        "issue_id": spec.issue_id,
        "protocol_config_sha256": spec.protocol_config_sha256,
        "code_sha256": spec.code_sha256,
        "environment_sha256": spec.environment_sha256,
        "input_manifest_sha256": spec.input_manifest_sha256,
        "model_manifest_sha256": spec.model_manifest_sha256,
        "state_before_sha256": spec.state_before_sha256,
        "state_after_sha256": spec.state_after_sha256,
        "payload": dict(spec.payload),
    }


def _event_spec_from_contract(
    contract: Mapping[str, Any],
) -> tuple[live_ledger.EventSpec, live_cas.LiveLedgerPreHeadV1]:
    checked = _exact(
        contract,
        {
            "schema_version",
            "expected_pre_head",
            "attempt",
            "event_spec",
            "event_spec_sha256",
        },
        name="anchor request action contract",
    )
    if checked["schema_version"] != ANCHOR_REQUEST_CONTRACT_SCHEMA:
        raise WorksetRecoveryIntegrityError("Anchor request contract version changed")
    pre_head = _exact(
        checked["expected_pre_head"],
        {"epoch_id", "event_count", "sequence_id", "entry_sha256"},
        name="anchor request expected pre-head",
    )
    spec_payload = _exact(
        checked["event_spec"], _EVENT_SPEC_KEYS, name="anchor request EventSpec"
    )
    if not isinstance(spec_payload["payload"], dict):
        raise WorksetRecoveryIntegrityError("Anchor request payload changed type")
    spec = live_ledger.EventSpec(**spec_payload)
    try:
        live_ledger._prepare_spec(spec)  # noqa: SLF001
        expected = live_cas.LiveLedgerPreHeadV1(**pre_head)
        live_cas._validate_pre_head(expected)  # noqa: SLF001
    except live_ledger.LedgerError as exc:
        raise WorksetRecoveryIntegrityError(str(exc)) from exc
    attempt = checked["attempt"]
    if (
        not isinstance(attempt, int)
        or isinstance(attempt, bool)
        or attempt < 1
        or spec.payload.get("attempt") != attempt
        or spec.payload.get("live_epoch_id") != expected.epoch_id
        or checked["event_spec_sha256"] != _sha256(_canonical_bytes(spec_payload))
    ):
        raise WorksetRecoveryIntegrityError("Anchor request contract digest changed")
    return spec, expected


def _anchor_request_contract(
    item: Mapping[str, Any], reservation: Reservation
) -> dict[str, object]:
    if item.get("family") != "live_outstanding":
        raise WorksetRecoveryIntegrityError("Anchor request item family changed")
    authority = _exact(
        item.get("authority"),
        {
            "record_type",
            "target_date",
            "old_live_epoch_id",
            "issue_id",
            "issue_sha256",
            "input_manifest_sha256",
            "seal_event",
            "anchor_confirmed_event",
            "frozen_live_upper_tip",
            "terminal",
            "action",
        },
        name="anchor request authority",
    )
    if (
        authority["record_type"] != "outstanding_live_lifecycle"
        or authority["action"] != "anchor_request_recorded"
        or authority["anchor_confirmed_event"] is not None
        or authority["terminal"] is not False
    ):
        raise WorksetRecoveryIntegrityError("Anchor request authority state changed")
    frozen = _frozen_live_prefix(reservation)
    projection = frozen.projection
    seal = projection.seal_event
    target_text = authority["target_date"]
    issue_id = authority["issue_id"]
    try:
        target = date.fromisoformat(target_text)
    except (TypeError, ValueError) as exc:
        raise WorksetRecoveryIntegrityError(
            "Anchor request target date changed"
        ) from exc
    if target.isoformat() != target_text:
        raise WorksetRecoveryIntegrityError("Anchor request target is not canonical")
    if (
        seal is None
        or projection.outstanding_target_date != target
        or projection.outstanding_issue_id != issue_id
        or authority["old_live_epoch_id"] != projection.epoch_id
        or authority["frozen_live_upper_tip"] != frozen.expected_pre_head.entry_sha256
        or authority["input_manifest_sha256"] != seal.input_manifest_sha256
    ):
        raise WorksetRecoveryIntegrityError("Anchor request frozen lifecycle changed")
    _hash_text(authority["issue_sha256"], name="anchor request issue")
    _hash_text(authority["input_manifest_sha256"], name="anchor request input manifest")
    seal_record = _exact(
        authority["seal_event"],
        {"sequence_id", "entry_sha256", "event_type", "target_date", "issue_id"},
        name="anchor request seal record",
    )
    expected_seal_record = {
        "sequence_id": seal.sequence_id,
        "entry_sha256": seal.entry_sha256,
        "event_type": seal.event_type,
        "target_date": seal.target_date,
        "issue_id": seal.issue_id,
    }
    if seal_record != expected_seal_record or seal.event_type != "issue_batch_sealed":
        raise WorksetRecoveryIntegrityError("Anchor request seal binding changed")
    lifecycle = [
        event
        for event in frozen.frozen_events
        if event.event_type in {"anchor_requested", "anchor_failed", "anchor_confirmed"}
        and event.payload.get("sealed_entry_sha256") == seal.entry_sha256
    ]
    requested = sum(event.event_type == "anchor_requested" for event in lifecycle)
    results = sum(
        event.event_type in {"anchor_failed", "anchor_confirmed"} for event in lifecycle
    )
    if requested != results or any(
        event.event_type == "anchor_confirmed" for event in lifecycle
    ):
        raise WorksetRecoveryIntegrityError(
            "Frozen anchor lifecycle does not permit a new request"
        )
    attempt = requested + 1
    request_payload = {
        "live_epoch_id": projection.epoch_id,
        "target_date": seal.target_date,
        "sealed_sequence_id": seal.sequence_id,
        "sealed_entry_sha256": seal.entry_sha256,
        "attempt": attempt,
    }
    spec = live._event_spec(  # noqa: SLF001
        event_key=(
            f"{projection.epoch_id}:{seal.target_date}:anchor:{attempt}:requested"
        ),
        event_type="anchor_requested",
        prerequisites=frozen.prerequisites,
        payload=request_payload,
        target_date_value=target,
        issue_id=seal.issue_id,
        input_manifest_sha256=seal.input_manifest_sha256,
        state_before_sha256=seal.state_after_sha256,
        state_after_sha256=seal.state_after_sha256,
    )
    spec_payload = _event_spec_payload(spec)
    contract: dict[str, object] = {
        "schema_version": ANCHOR_REQUEST_CONTRACT_SCHEMA,
        "expected_pre_head": {
            "epoch_id": frozen.expected_pre_head.epoch_id,
            "event_count": frozen.expected_pre_head.event_count,
            "sequence_id": frozen.expected_pre_head.sequence_id,
            "entry_sha256": frozen.expected_pre_head.entry_sha256,
        },
        "attempt": attempt,
        "event_spec": spec_payload,
        "event_spec_sha256": _sha256(_canonical_bytes(spec_payload)),
    }
    _event_spec_from_contract(contract)
    return contract


_EXTERNAL_ANCHOR_KEYS = {
    "mode",
    "endpoint_environment_variable",
    "bearer_token_environment_variable",
    "timeout_seconds",
    "anchor_issue_batch_seal",
    "missing_or_failed_status",
    "confirmed_status",
    "provider_allowlist",
    "receipt_verification_mode",
    "trusted_receipt_required_for_live_evidence",
    "e2a_receipts_count_as_live_evidence",
    "required_before_outcome_read",
    "claim_independent_time_proof_without_confirmed_receipt",
}
_ANCHOR_RESULT_REQUEST_EVENT_KEYS = {
    "event_key",
    "event_type",
    "sequence_id",
    "previous_entry_sha256",
    "entry_sha256",
    "event_spec_sha256",
}


def _normalized_https_anchor_endpoint(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise WorksetRecoveryExternalWait(
            "time-anchor endpoint is missing or is not a trimmed URL"
        )
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise WorksetRecoveryExternalWait("time-anchor endpoint contains control bytes")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise WorksetRecoveryExternalWait("time-anchor endpoint is malformed") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise WorksetRecoveryExternalWait(
            "time-anchor endpoint must be exact HTTPS without credentials, query, or fragment"
        )
    host = parsed.hostname.lower()
    netloc = f"[{host}]" if ":" in host else host
    if port not in {None, 443}:
        netloc = f"{netloc}:{port}"
    return urlunsplit(("https", netloc, parsed.path or "/", "", ""))


def _external_anchor_profile(frozen: FrozenLivePrefix) -> dict[str, Any]:
    external = _exact(
        frozen.profile.get("external_anchor"),
        _EXTERNAL_ANCHOR_KEYS,
        name="frozen external-anchor profile",
    )
    endpoint_name = external["endpoint_environment_variable"]
    token_name = external["bearer_token_environment_variable"]
    timeout = external["timeout_seconds"]
    allowlist = external["provider_allowlist"]
    if (
        external["mode"] != "https_json_post_from_environment"
        or not isinstance(endpoint_name, str)
        or not endpoint_name
        or endpoint_name != endpoint_name.strip()
        or not isinstance(token_name, str)
        or not token_name
        or token_name != token_name.strip()
        or not isinstance(timeout, int)
        or isinstance(timeout, bool)
        or timeout < 1
        or not isinstance(allowlist, list)
        or not all(
            isinstance(provider, str)
            and bool(provider)
            and provider == provider.strip()
            for provider in allowlist
        )
        or external["receipt_verification_mode"]
        != "interface_only_no_cryptographic_verifier_e2a"
        or external["trusted_receipt_required_for_live_evidence"] is not True
        or external["e2a_receipts_count_as_live_evidence"] is not False
    ):
        raise WorksetRecoveryIntegrityError("Frozen external-anchor semantics changed")
    return external


def _anchor_result_context(
    item: Mapping[str, Any], reservation: Reservation
) -> tuple[FrozenLivePrefix, live_ledger.LedgerEvent, Mapping[str, Any]]:
    if item.get("family") != "live_outstanding":
        raise WorksetRecoveryIntegrityError("Anchor result item family changed")
    authority = _exact(
        item.get("authority"),
        {
            "record_type",
            "target_date",
            "old_live_epoch_id",
            "issue_id",
            "issue_sha256",
            "input_manifest_sha256",
            "seal_event",
            "anchor_confirmed_event",
            "frozen_live_upper_tip",
            "terminal",
            "action",
        },
        name="anchor result authority",
    )
    if (
        authority["record_type"] != "outstanding_live_lifecycle"
        or authority["action"]
        not in {"anchor_request_recorded", "anchor_result_recorded"}
        or authority["anchor_confirmed_event"] is not None
        or authority["terminal"] is not False
    ):
        raise WorksetRecoveryIntegrityError("Anchor result authority state changed")
    frozen = _frozen_live_prefix(reservation)
    projection = frozen.projection
    seal = projection.seal_event
    if (
        seal is None
        or seal.event_type != "issue_batch_sealed"
        or authority["target_date"] != seal.target_date
        or authority["issue_id"] != seal.issue_id
        or authority["old_live_epoch_id"] != projection.epoch_id
        or authority["input_manifest_sha256"] != seal.input_manifest_sha256
        or authority["frozen_live_upper_tip"] != frozen.expected_pre_head.entry_sha256
    ):
        raise WorksetRecoveryIntegrityError("Anchor result frozen lifecycle changed")
    _hash_text(authority["issue_sha256"], name="anchor result issue")
    _hash_text(authority["input_manifest_sha256"], name="anchor result input manifest")
    seal_record = _exact(
        authority["seal_event"],
        {"sequence_id", "entry_sha256", "event_type", "target_date", "issue_id"},
        name="anchor result seal record",
    )
    if seal_record != {
        "sequence_id": seal.sequence_id,
        "entry_sha256": seal.entry_sha256,
        "event_type": seal.event_type,
        "target_date": seal.target_date,
        "issue_id": seal.issue_id,
    }:
        raise WorksetRecoveryIntegrityError("Anchor result seal binding changed")
    _external_anchor_profile(frozen)
    return frozen, seal, authority


def _canonical_anchor_request_spec(
    frozen: FrozenLivePrefix,
    seal: live_ledger.LedgerEvent,
    payload: object,
) -> live_ledger.EventSpec:
    request = _exact(
        payload,
        {
            "live_epoch_id",
            "target_date",
            "sealed_sequence_id",
            "sealed_entry_sha256",
            "attempt",
        },
        name="anchor result request payload",
    )
    attempt = request["attempt"]
    if (
        request["live_epoch_id"] != frozen.projection.epoch_id
        or request["target_date"] != seal.target_date
        or request["sealed_sequence_id"] != seal.sequence_id
        or request["sealed_entry_sha256"] != seal.entry_sha256
        or not isinstance(attempt, int)
        or isinstance(attempt, bool)
        or attempt < 1
    ):
        raise WorksetRecoveryIntegrityError("Anchor result request identity changed")
    return live._event_spec(  # noqa: SLF001
        event_key=(
            f"{frozen.projection.epoch_id}:{seal.target_date}:anchor:{attempt}:requested"
        ),
        event_type="anchor_requested",
        prerequisites=frozen.prerequisites,
        payload=request,
        target_date_value=date.fromisoformat(str(seal.target_date)),
        issue_id=seal.issue_id,
        input_manifest_sha256=seal.input_manifest_sha256,
        state_before_sha256=seal.state_after_sha256,
        state_after_sha256=seal.state_after_sha256,
    )


def _anchor_result_request_record(
    request_event: live_ledger.LedgerEvent, spec: live_ledger.EventSpec
) -> dict[str, object]:
    return {
        "event_key": request_event.event_key,
        "event_type": request_event.event_type,
        "sequence_id": request_event.sequence_id,
        "previous_entry_sha256": request_event.previous_entry_sha256,
        "entry_sha256": request_event.entry_sha256,
        "event_spec_sha256": _sha256(_canonical_bytes(_event_spec_payload(spec))),
    }


def _anchor_result_request_contract(
    item: Mapping[str, Any], reservation: Reservation
) -> dict[str, object]:
    frozen, seal, authority = _anchor_result_context(item, reservation)
    lifecycle = [
        event
        for event in frozen.current_events
        if event.event_type in {"anchor_requested", "anchor_failed", "anchor_confirmed"}
        and event.payload.get("sealed_entry_sha256") == seal.entry_sha256
    ]
    if not lifecycle or len(lifecycle) % 2 != 1:
        raise WorksetRecoveryIntegrityError(
            "Anchor result requires one canonical unmatched request at the live tip"
        )
    request_event = lifecycle[-1]
    for attempt, offset in enumerate(range(0, len(lifecycle), 2), 1):
        request = lifecycle[offset]
        if (
            request.event_type != "anchor_requested"
            or request.payload.get("attempt") != attempt
        ):
            raise WorksetRecoveryIntegrityError(
                "Anchor result lifecycle request pairing changed"
            )
        request_spec = _canonical_anchor_request_spec(frozen, seal, request.payload)
        if not _event_matches_spec(request, request_spec):
            raise WorksetRecoveryIntegrityError(
                "Anchor result lifecycle request changed"
            )
        if offset == len(lifecycle) - 1:
            continue
        result = lifecycle[offset + 1]
        prefix = request.event_key.removesuffix(":requested")
        request_payload = dict(request.payload)
        if (
            result.event_type not in {"anchor_failed", "anchor_confirmed"}
            or result.sequence_id != request.sequence_id + 1
            or result.previous_entry_sha256 != request.entry_sha256
            or result.event_key
            != f"{prefix}:{result.event_type.removeprefix('anchor_')}"
            or any(
                result.payload.get(key) != value
                for key, value in request_payload.items()
            )
        ):
            raise WorksetRecoveryIntegrityError(
                "Anchor result lifecycle result pairing changed"
            )
    if request_event != frozen.current_events[-1]:
        raise WorksetRecoveryIntegrityError(
            "Anchor result requires one canonical unmatched request at the live tip"
        )
    spec = _canonical_anchor_request_spec(frozen, seal, request_event.payload)
    if not _event_matches_spec(request_event, spec):
        raise WorksetRecoveryIntegrityError("Anchor result request event changed")
    if authority["action"] == "anchor_result_recorded":
        if (
            request_event.sequence_id != frozen.expected_pre_head.event_count
            or request_event.entry_sha256 != frozen.expected_pre_head.entry_sha256
        ):
            raise WorksetRecoveryIntegrityError(
                "Frozen anchor result request is not the manifest tip"
            )
    elif (
        request_event.sequence_id != frozen.expected_pre_head.event_count + 1
        or request_event.previous_entry_sha256 != frozen.expected_pre_head.entry_sha256
    ):
        raise WorksetRecoveryIntegrityError(
            "Recovered anchor request is not immediately after the manifest tip"
        )
    external = _external_anchor_profile(frozen)
    endpoint_name = external["endpoint_environment_variable"]
    endpoint = _normalized_https_anchor_endpoint(os.environ.get(endpoint_name))
    allowlist = {str(provider).lower() for provider in external["provider_allowlist"]}
    endpoint_host = urlsplit(endpoint).hostname
    if allowlist and endpoint_host not in allowlist:
        raise WorksetRecoveryExternalWait(
            "time-anchor endpoint host is outside the frozen allowlist"
        )
    request_body = dict(request_event.payload)
    request_body_raw = live._canonical_json(request_body).encode("utf-8")  # noqa: SLF001
    request_record = _anchor_result_request_record(request_event, spec)
    idempotency_identity = {
        "event_key": request_record["event_key"],
        "sequence_id": request_record["sequence_id"],
        "entry_sha256": request_record["entry_sha256"],
        "event_spec_sha256": request_record["event_spec_sha256"],
    }
    contract: dict[str, object] = {
        "schema_version": ANCHOR_RESULT_REQUEST_CONTRACT_SCHEMA,
        "endpoint": endpoint,
        "endpoint_sha256": _sha256(endpoint.encode("utf-8")),
        "endpoint_environment_variable": endpoint_name,
        "bearer_token_environment_variable": external[
            "bearer_token_environment_variable"
        ],
        "http_method": "POST",
        "timeout_seconds": external["timeout_seconds"],
        "maximum_response_bytes": ANCHOR_RESULT_MAXIMUM_RESPONSE_BYTES,
        "request_event": request_record,
        "request_body": request_body,
        "request_body_sha256": _sha256(request_body_raw),
        "idempotency_key": _sha256(_canonical_bytes(idempotency_identity)),
        "expected_result_pre_head": {
            "epoch_id": frozen.projection.epoch_id,
            "event_count": request_event.sequence_id,
            "sequence_id": request_event.sequence_id,
            "entry_sha256": request_event.entry_sha256,
        },
        "receipt_verification_mode": external["receipt_verification_mode"],
        "remote_delivery_semantics": (
            "at_least_once_unless_provider_honors_idempotency_key"
        ),
    }
    _verify_anchor_result_request_contract(item, reservation, contract)
    return contract


def _verify_anchor_result_request_contract(
    item: Mapping[str, Any],
    reservation: Reservation,
    contract: Mapping[str, Any],
) -> None:
    checked = _exact(
        contract,
        {
            "schema_version",
            "endpoint",
            "endpoint_sha256",
            "endpoint_environment_variable",
            "bearer_token_environment_variable",
            "http_method",
            "timeout_seconds",
            "maximum_response_bytes",
            "request_event",
            "request_body",
            "request_body_sha256",
            "idempotency_key",
            "expected_result_pre_head",
            "receipt_verification_mode",
            "remote_delivery_semantics",
        },
        name="anchor result request action contract",
    )
    frozen, seal, authority = _anchor_result_context(item, reservation)
    external = _external_anchor_profile(frozen)
    try:
        endpoint = _normalized_https_anchor_endpoint(checked["endpoint"])
    except WorksetRecoveryExternalWait as exc:
        raise WorksetRecoveryIntegrityError(
            "Recorded time-anchor endpoint changed"
        ) from exc
    request_record = _exact(
        checked["request_event"],
        _ANCHOR_RESULT_REQUEST_EVENT_KEYS,
        name="anchor result request event",
    )
    pre_head = _exact(
        checked["expected_result_pre_head"],
        {"epoch_id", "event_count", "sequence_id", "entry_sha256"},
        name="anchor result expected pre-head",
    )
    try:
        expected = live_cas.LiveLedgerPreHeadV1(**pre_head)
        live_cas._validate_pre_head(expected)  # noqa: SLF001
    except (TypeError, live_ledger.LedgerError) as exc:
        raise WorksetRecoveryIntegrityError(
            "Anchor result expected pre-head changed"
        ) from exc
    position = expected.event_count - 1
    if position < 0 or len(frozen.current_events) <= position:
        raise WorksetRecoveryIntegrityError("Anchor result request event disappeared")
    request_event = frozen.current_events[position]
    spec = _canonical_anchor_request_spec(frozen, seal, checked["request_body"])
    expected_record = _anchor_result_request_record(request_event, spec)
    idempotency_identity = {
        "event_key": expected_record["event_key"],
        "sequence_id": expected_record["sequence_id"],
        "entry_sha256": expected_record["entry_sha256"],
        "event_spec_sha256": expected_record["event_spec_sha256"],
    }
    request_body_raw = live._canonical_json(  # noqa: SLF001
        checked["request_body"]
    ).encode("utf-8")
    allowlist = {str(provider).lower() for provider in external["provider_allowlist"]}
    endpoint_host = urlsplit(endpoint).hostname
    if (
        checked["schema_version"] != ANCHOR_RESULT_REQUEST_CONTRACT_SCHEMA
        or request_record != expected_record
        or not _event_matches_spec(request_event, spec)
        or expected.epoch_id != frozen.projection.epoch_id
        or expected.sequence_id != request_event.sequence_id
        or expected.entry_sha256 != request_event.entry_sha256
        or checked["endpoint"] != endpoint
        or checked["endpoint_sha256"] != _sha256(endpoint.encode("utf-8"))
        or checked["endpoint_environment_variable"]
        != external["endpoint_environment_variable"]
        or checked["bearer_token_environment_variable"]
        != external["bearer_token_environment_variable"]
        or checked["http_method"] != "POST"
        or checked["timeout_seconds"] != external["timeout_seconds"]
        or checked["maximum_response_bytes"] != ANCHOR_RESULT_MAXIMUM_RESPONSE_BYTES
        or checked["request_body"] != dict(request_event.payload)
        or checked["request_body_sha256"] != _sha256(request_body_raw)
        or checked["idempotency_key"] != _sha256(_canonical_bytes(idempotency_identity))
        or checked["receipt_verification_mode"] != external["receipt_verification_mode"]
        or checked["remote_delivery_semantics"]
        != "at_least_once_unless_provider_honors_idempotency_key"
        or (allowlist and endpoint_host not in allowlist)
    ):
        raise WorksetRecoveryIntegrityError("Anchor result request contract changed")
    if authority["action"] == "anchor_result_recorded":
        if (
            request_event.sequence_id != frozen.expected_pre_head.event_count
            or request_event.entry_sha256 != frozen.expected_pre_head.entry_sha256
        ):
            raise WorksetRecoveryIntegrityError(
                "Frozen anchor result request position changed"
            )
    elif (
        request_event.sequence_id != frozen.expected_pre_head.event_count + 1
        or request_event.previous_entry_sha256 != frozen.expected_pre_head.entry_sha256
    ):
        raise WorksetRecoveryIntegrityError("Recovered anchor request position changed")


def _verify_pending_anchor_result_head(
    item: Mapping[str, Any],
    reservation: Reservation,
    contract: Mapping[str, Any],
) -> None:
    _verify_anchor_result_request_contract(item, reservation, contract)
    expected_record = _exact(
        contract["expected_result_pre_head"],
        {"epoch_id", "event_count", "sequence_id", "entry_sha256"},
        name="pending anchor result expected pre-head",
    )
    frozen = _frozen_live_prefix(reservation)
    if (
        len(frozen.current_events) != expected_record["event_count"]
        or frozen.current_events[-1].sequence_id != expected_record["sequence_id"]
        or frozen.current_events[-1].entry_sha256 != expected_record["entry_sha256"]
    ):
        raise WorksetRecoveryIntegrityError(
            "Pending anchor result request is no longer at the live-ledger head"
        )


def _action_contract(
    item: Mapping[str, Any], reservation: Reservation, action: str
) -> dict[str, object] | None:
    if action == "anchor_request_recorded":
        return _anchor_request_contract(item, reservation)
    if action == "anchor_result_recorded":
        return _anchor_result_request_contract(item, reservation)
    return None


def _verify_item_intent_action_contract(
    item: Mapping[str, Any],
    reservation: Reservation,
    action: str,
    contract: object,
) -> None:
    if action == "anchor_result_recorded":
        if not isinstance(contract, Mapping):
            raise WorksetRecoveryIntegrityError(
                "Anchor result item intent lost its request contract"
            )
        _verify_anchor_result_request_contract(item, reservation, contract)
        return
    if contract != _action_contract(item, reservation, action):
        raise WorksetRecoveryIntegrityError("Recovery step intent contract changed")


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
    action_contract: Mapping[str, object] | None,
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
        "action_contract": action_contract,
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


def _anchor_result_request_identity(
    contract: Mapping[str, Any],
) -> dict[str, str]:
    request_event = _exact(
        contract.get("request_event"),
        _ANCHOR_RESULT_REQUEST_EVENT_KEYS,
        name="anchor result observation request event",
    )
    return {
        "request_event_entry_sha256": _hash_text(
            request_event.get("entry_sha256"),
            name="anchor result observation request event",
        ),
        "request_body_sha256": _hash_text(
            contract.get("request_body_sha256"),
            name="anchor result observation request body",
        ),
        "endpoint_sha256": _hash_text(
            contract.get("endpoint_sha256"),
            name="anchor result observation endpoint",
        ),
        "idempotency_key": _hash_text(
            contract.get("idempotency_key"),
            name="anchor result observation idempotency key",
        ),
    }


def _anchor_result_sealed_entry_sha256(contract: Mapping[str, Any]) -> str:
    request_body = contract.get("request_body")
    if not isinstance(request_body, Mapping):
        raise WorksetRecoveryIntegrityError(
            "Anchor result request body changed before dispatch"
        )
    return _hash_text(
        request_body.get("sealed_entry_sha256"),
        name="anchor result sealed entry",
    )


def _anchor_result_token(contract: Mapping[str, Any]) -> str | None:
    name = contract.get("bearer_token_environment_variable")
    if (
        not isinstance(name, str)
        or not name
        or name != name.strip()
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in name)
    ):
        raise WorksetRecoveryIntegrityError(
            "Anchor result token environment-variable name changed"
        )
    token = os.environ.get(name)
    if (
        token is None
        or not token
        or token != token.strip()
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in token)
    ):
        return None
    return token


class _AnchorResultNoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _anchor_transport_response(
    response: Any,
    *,
    maximum_bytes: int,
) -> AnchorResultTransportResponse:
    raw_status = getattr(response, "status", None)
    if raw_status is None:
        raw_status = getattr(response, "code", None)
    status_code = int(raw_status)
    if status_code in {408, 425, 429} or status_code >= 500:
        raise WorksetRecoveryNetworkWait(
            "Anchor result transport is retryable or has ambiguous delivery"
        )
    headers = response.headers
    media_type = headers.get_content_type().lower()
    charset_value = headers.get_content_charset()
    charset = charset_value.lower() if charset_value is not None else None
    encoding_value = headers.get("Content-Encoding")
    content_encoding = (
        encoding_value.strip().lower() if encoding_value is not None else None
    )
    return AnchorResultTransportResponse(
        body=response.read(maximum_bytes + 1),
        status_code=status_code,
        media_type=media_type,
        charset=charset,
        content_encoding=content_encoding,
        final_url=response.geturl(),
    )


def _default_anchor_result_transport(
    contract: Mapping[str, Any], token: str
) -> AnchorResultTransportResponse:
    endpoint = contract.get("endpoint")
    timeout = contract.get("timeout_seconds")
    maximum_bytes = contract.get("maximum_response_bytes")
    if (
        not isinstance(endpoint, str)
        or not isinstance(timeout, int)
        or isinstance(timeout, bool)
        or timeout < 1
        or not isinstance(maximum_bytes, int)
        or isinstance(maximum_bytes, bool)
        or maximum_bytes != ANCHOR_RESULT_MAXIMUM_RESPONSE_BYTES
    ):
        raise WorksetRecoveryIntegrityError("Anchor result transport contract changed")
    try:
        body = live._canonical_json(contract.get("request_body")).encode("utf-8")  # noqa: SLF001
    except (UnicodeError, live.LiveIntegrityError) as exc:
        raise WorksetRecoveryIntegrityError(
            "Anchor result request body is not canonical JSON"
        ) from exc
    if _sha256(body) != contract.get("request_body_sha256"):
        raise WorksetRecoveryIntegrityError(
            "Anchor result request body digest changed before dispatch"
        )
    request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Idempotency-Key": str(contract.get("idempotency_key")),
            "User-Agent": ANCHOR_RESULT_USER_AGENT,
        },
    )
    try:
        blocked_signals = signal.pthread_sigmask(signal.SIG_BLOCK, set())
    except (AttributeError, OSError, ValueError) as exc:
        raise WorksetRecoveryIntegrityError(
            "Anchor result transport signal mask cannot be inspected"
        ) from exc
    if signal.SIGALRM in blocked_signals:
        raise WorksetRecoveryIntegrityError(
            "Anchor result transport deadline signal is blocked"
        )
    try:
        opener = urllib.request.build_opener(_AnchorResultNoRedirect())
    except Exception as exc:
        raise WorksetRecoveryIntegrityError(
            "Anchor result transport opener cannot be constructed"
        ) from exc
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    if previous_timer != (0.0, 0.0):
        raise WorksetRecoveryIntegrityError(
            "An unrelated process alarm is already active"
        )

    def deadline_exceeded(_signum, _frame):  # type: ignore[no-untyped-def]
        raise TimeoutError("Anchor result transport total deadline exceeded")

    try:
        signal.signal(signal.SIGALRM, deadline_exceeded)
        signal.setitimer(signal.ITIMER_REAL, timeout)
    except (OSError, ValueError) as exc:
        try:
            signal.signal(signal.SIGALRM, previous_handler)
        except (OSError, ValueError):
            pass
        raise WorksetRecoveryIntegrityError(
            "Anchor result transport deadline cannot be armed"
        ) from exc
    try:
        try:
            try:
                response = opener.open(request, timeout=timeout)
            except urllib.error.HTTPError as exc:
                response = exc
            try:
                return _anchor_transport_response(response, maximum_bytes=maximum_bytes)
            finally:
                response.close()
        except WorksetRecoveryNetworkWait:
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise WorksetRecoveryNetworkWait(
                "Anchor result transport is retryable or has ambiguous delivery"
            ) from exc
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)


def _bounded_transport_text(
    value: object, *, name: str, maximum_bytes: int, optional: bool = False
) -> str | None:
    if optional and value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum_bytes
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise WorksetRecoveryIntegrityError(
            f"Anchor result transport {name} is not bounded text"
        )
    return value


def _anchor_result_transport_record(
    response: AnchorResultTransportResponse,
    *,
    maximum_bytes: int,
) -> dict[str, object]:
    if (
        not isinstance(response, AnchorResultTransportResponse)
        or not isinstance(response.body, bytes)
        or len(response.body) > maximum_bytes + 1
        or not isinstance(response.status_code, int)
        or isinstance(response.status_code, bool)
        or not 100 <= response.status_code <= 599
    ):
        raise WorksetRecoveryIntegrityError(
            "Anchor result transport returned an invalid bounded response"
        )
    media_type = _bounded_transport_text(
        response.media_type, name="media type", maximum_bytes=256
    )
    charset = _bounded_transport_text(
        response.charset,
        name="charset",
        maximum_bytes=128,
        optional=True,
    )
    content_encoding = _bounded_transport_text(
        response.content_encoding,
        name="content encoding",
        maximum_bytes=128,
        optional=True,
    )
    final_url = _bounded_transport_text(
        response.final_url, name="final URL", maximum_bytes=4096
    )
    body = response.body
    return {
        "status_code": response.status_code,
        "media_type": media_type,
        "charset": charset,
        "content_encoding": content_encoding,
        "final_url": final_url,
        "body_complete": len(body) <= maximum_bytes,
        "observed_body_size_bytes": len(body),
        "observed_body_sha256": _sha256(body),
        "observed_body_base64": base64.b64encode(body).decode("ascii"),
    }


def _anchor_result_transport_from_record(
    value: object, *, maximum_bytes: int
) -> AnchorResultTransportResponse:
    record = _exact(
        value,
        {
            "status_code",
            "media_type",
            "charset",
            "content_encoding",
            "final_url",
            "body_complete",
            "observed_body_size_bytes",
            "observed_body_sha256",
            "observed_body_base64",
        },
        name="anchor result transport observation",
    )
    encoded = record["observed_body_base64"]
    if not isinstance(encoded, str):
        raise WorksetRecoveryIntegrityError(
            "Anchor result observed body encoding changed"
        )
    try:
        body = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise WorksetRecoveryIntegrityError(
            "Anchor result observed body encoding changed"
        ) from exc
    response = AnchorResultTransportResponse(
        body=body,
        status_code=record["status_code"],
        media_type=record["media_type"],
        charset=record["charset"],
        content_encoding=record["content_encoding"],
        final_url=record["final_url"],
    )
    rebuilt = _anchor_result_transport_record(response, maximum_bytes=maximum_bytes)
    if rebuilt != record:
        raise WorksetRecoveryIntegrityError(
            "Anchor result transport observation changed"
        )
    return response


def _strict_anchor_result_json(raw: bytes) -> dict[str, Any]:
    try:
        payload = registry._decode_json(raw, name="anchor result response")  # noqa: SLF001
        _canonical_bytes(payload)
    except (registry.EpochRegistryError, WorksetRecoveryError, UnicodeError) as exc:
        raise _AnchorResultProtocolFailure(
            "response_json", "invalid_strict_json"
        ) from exc
    return payload


def _validated_anchor_result_candidate(
    contract: Mapping[str, Any], response: AnchorResultTransportResponse
) -> dict[str, Any]:
    maximum_bytes = contract.get("maximum_response_bytes")
    endpoint = contract.get("endpoint")
    if not isinstance(maximum_bytes, int) or not isinstance(endpoint, str):
        raise WorksetRecoveryIntegrityError("Anchor result response contract changed")
    if response.status_code in {408, 425, 429} or response.status_code >= 500:
        raise WorksetRecoveryNetworkWait(
            "Anchor result transport is retryable or has ambiguous delivery"
        )
    if len(response.body) > maximum_bytes:
        raise _AnchorResultProtocolFailure("response_body", "response_too_large")
    if 300 <= response.status_code <= 399:
        raise _AnchorResultProtocolFailure("http_status", "redirect_rejected")
    if 400 <= response.status_code <= 499:
        raise _AnchorResultProtocolFailure("http_status", "request_rejected")
    if response.status_code != 200:
        raise _AnchorResultProtocolFailure("http_status", "unexpected_http_status")
    if response.final_url != endpoint:
        raise _AnchorResultProtocolFailure("transport", "final_url_changed")
    if response.content_encoding not in {None, "identity"}:
        raise _AnchorResultProtocolFailure("transport", "unexpected_content_encoding")
    if response.media_type != "application/json":
        raise _AnchorResultProtocolFailure("transport", "unexpected_media_type")
    if response.charset not in {None, "utf-8"}:
        raise _AnchorResultProtocolFailure("transport", "unexpected_charset")
    payload = _strict_anchor_result_json(response.body)
    try:
        candidate = live._anchor_response(  # noqa: SLF001
            payload,
            sealed_entry_sha256=_anchor_result_sealed_entry_sha256(contract),
        )
        _canonical_bytes(candidate)
    except (
        live.LiveInputError,
        live.LiveIntegrityError,
        WorksetRecoveryError,
        UnicodeError,
    ) as exc:
        raise _AnchorResultProtocolFailure(
            "response_contract", "invalid_anchor_response"
        ) from exc
    return candidate


def _anchor_result_observation_payload(
    plan: AnchorResultDispatchPlan,
    response: AnchorResultTransportResponse,
    *,
    token: str | None,
) -> dict[str, object]:
    maximum_bytes = plan.action_contract.get("maximum_response_bytes")
    if not isinstance(maximum_bytes, int):
        raise WorksetRecoveryIntegrityError("Anchor result response size limit changed")
    transport = _anchor_result_transport_record(response, maximum_bytes=maximum_bytes)
    if token is not None and token.encode("ascii") in response.body:
        raise WorksetRecoveryIntegrityError(
            "Anchor result response reflected bearer credentials"
        )
    try:
        candidate = _validated_anchor_result_candidate(plan.action_contract, response)
    except _AnchorResultProtocolFailure as exc:
        outcome = "deterministic_failure"
        candidate = None
        failure: dict[str, object] | None = {
            "stage": exc.stage,
            "code": exc.code,
            "error_type": "AnchorResultProtocolFailure",
            "retry_policy": "record_failure_then_automatic_next_poll",
        }
    else:
        outcome = "candidate_confirmed"
        failure = None
    return {
        "schema_version": plan.profile["protocol"][
            "anchor_result_response_observation_schema_version"
        ],
        "profile_id": plan.profile["profile_id"],
        "profile_sha256": plan.profile["_profile_sha256"],
        "step_id": plan.step_id,
        "key_id": plan.key_id,
        "item_intent": _reference(plan.item_intent, plan.paths.root),
        "request_identity": _anchor_result_request_identity(plan.action_contract),
        "outcome": outcome,
        "transport_response": transport,
        "validated_response": candidate,
        "failure": failure,
        "network_action_performed": True,
        "remote_exactly_once": False,
        "trusted_anchor_receipt_verified": False,
        "e2_live_evidence_eligible": False,
        "live_ledger_result_recorded": False,
        "recovery_receipt_created": False,
    }


def _anchor_result_link_payload(
    plan: AnchorResultDispatchPlan,
    observation: Mapping[str, Any],
    snapshot: registry.ArtifactSnapshot,
) -> dict[str, object]:
    return {
        "schema_version": plan.profile["protocol"][
            "anchor_result_response_link_schema_version"
        ],
        "profile_id": plan.profile["profile_id"],
        "profile_sha256": plan.profile["_profile_sha256"],
        "step_id": plan.step_id,
        "key_id": plan.key_id,
        "item_intent": _reference(plan.item_intent, plan.paths.root),
        "request_identity": _anchor_result_request_identity(plan.action_contract),
        "response_observation": _reference(snapshot, plan.paths.root),
        "outcome": observation["outcome"],
        "network_action_performed": True,
        "remote_exactly_once": False,
        "trusted_anchor_receipt_verified": False,
        "e2_live_evidence_eligible": False,
        "live_ledger_result_recorded": False,
        "recovery_receipt_created": False,
    }


def _verify_anchor_result_dispatch_plan(plan: AnchorResultDispatchPlan) -> None:
    expected_path = plan.paths.item_intents / f"{plan.step_id}.json"
    if plan.item_intent.path != expected_path:
        raise WorksetRecoveryIntegrityError(
            "Anchor result dispatch intent path changed"
        )
    payload, snapshot = _strict_json(
        expected_path, name="anchor result dispatch item intent"
    )
    if (
        snapshot != plan.item_intent
        or payload.get("profile_id") != plan.profile["profile_id"]
        or payload.get("profile_sha256") != plan.profile["_profile_sha256"]
        or payload.get("step_id") != plan.step_id
        or payload.get("key_id") != plan.key_id
        or payload.get("action") != "anchor_result_recorded"
        or payload.get("action_contract") != plan.action_contract
    ):
        raise WorksetRecoveryIntegrityError(
            "Anchor result dispatch intent changed after lock release"
        )
    _anchor_result_request_identity(plan.action_contract)
    _anchor_result_sealed_entry_sha256(plan.action_contract)


def _verify_anchor_result_observation(
    plan: AnchorResultDispatchPlan, path: Path
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    payload, snapshot = _strict_json(path, name="anchor result response observation")
    if path.name != f"{snapshot.sha256}.json":
        raise WorksetRecoveryIntegrityError(
            "Anchor result response observation is not content-addressed"
        )
    transport = _anchor_result_transport_from_record(
        payload.get("transport_response"),
        maximum_bytes=int(plan.action_contract["maximum_response_bytes"]),
    )
    try:
        expected = _anchor_result_observation_payload(plan, transport, token=None)
    except WorksetRecoveryNetworkWait as exc:
        raise WorksetRecoveryIntegrityError(
            "Retryable anchor response was incorrectly persisted"
        ) from exc
    if payload != expected:
        raise WorksetRecoveryIntegrityError(
            "Anchor result response observation changed"
        )
    return payload, snapshot


def _inspect_anchor_result_observation(
    plan: AnchorResultDispatchPlan,
) -> (
    tuple[
        dict[str, Any],
        registry.ArtifactSnapshot,
        registry.ArtifactSnapshot | None,
    ]
    | None
):
    object_directory = plan.paths.anchor_result_response_objects / plan.step_id
    objects = _strict_named_json(
        object_directory,
        suffix=".json",
        name="anchor result response object set",
    )
    if len(objects) > 1:
        raise WorksetRecoveryIntegrityError(
            "Anchor result response observation branched"
        )
    link_path = plan.paths.anchor_result_response_links / f"{plan.step_id}.json"
    link_present = link_path.exists() or link_path.is_symlink()
    if not objects:
        if link_present:
            raise WorksetRecoveryIntegrityError(
                "Anchor result response link is orphaned"
            )
        return None
    object_path = next(iter(objects.values()))
    observation, object_snapshot = _verify_anchor_result_observation(plan, object_path)
    expected_link = _anchor_result_link_payload(plan, observation, object_snapshot)
    if link_present:
        link, link_snapshot = _strict_json(
            link_path, name="anchor result response link"
        )
        if link != expected_link:
            raise WorksetRecoveryIntegrityError("Anchor result response link changed")
        return observation, object_snapshot, link_snapshot
    return observation, object_snapshot, None


def _load_or_adopt_anchor_result_observation(
    plan: AnchorResultDispatchPlan,
) -> (
    tuple[dict[str, Any], registry.ArtifactSnapshot, registry.ArtifactSnapshot, str]
    | None
):
    inspected = _inspect_anchor_result_observation(plan)
    if inspected is None:
        return None
    observation, object_snapshot, link_snapshot = inspected
    if link_snapshot is not None:
        return observation, object_snapshot, link_snapshot, "linked"
    link_path = plan.paths.anchor_result_response_links / f"{plan.step_id}.json"
    expected_link = _anchor_result_link_payload(plan, observation, object_snapshot)
    link_snapshot = _publish(
        link_path,
        _canonical_bytes(expected_link),
        root=plan.paths.root,
        name="anchor result response link",
    )
    return observation, object_snapshot, link_snapshot, "forward_adopted"


def _deep_verify_locked_anchor_result_observations(
    profile: Mapping[str, Any],
    paths: RecoveryPaths,
    reservation: Reservation,
    items: Mapping[str, Mapping[str, Any]],
    intents: Mapping[str, Path],
    receipts: Mapping[str, tuple[dict[str, Any], registry.ArtifactSnapshot]],
    observation_steps: set[str],
) -> None:
    if not observation_steps:
        return
    pending_steps = set(intents) - set(receipts)
    if len(observation_steps) != 1 or pending_steps != observation_steps:
        raise WorksetRecoveryIntegrityError(
            "Anchor result response observation is not bound to one pending intent"
        )
    step_id = next(iter(observation_steps))
    intent_payload, intent_snapshot = _strict_json(
        intents[step_id], name="observed anchor result item intent"
    )
    key_id = intent_payload.get("key_id")
    item = items.get(key_id) if isinstance(key_id, str) else None
    contract = intent_payload.get("action_contract")
    if (
        item is None
        or intent_payload.get("action") != "anchor_result_recorded"
        or not isinstance(contract, Mapping)
    ):
        raise WorksetRecoveryIntegrityError(
            "Anchor result response observation intent changed"
        )
    _verify_pending_anchor_result_head(item, reservation, contract)
    plan = AnchorResultDispatchPlan(
        profile=profile,
        paths=paths,
        key_id=key_id,
        step_id=step_id,
        item_intent=intent_snapshot,
        action_contract=dict(contract),
    )
    _verify_anchor_result_dispatch_plan(plan)
    _inspect_anchor_result_observation(plan)


def _publish_anchor_result_observation(
    plan: AnchorResultDispatchPlan,
    payload: Mapping[str, Any],
) -> tuple[registry.ArtifactSnapshot, registry.ArtifactSnapshot]:
    raw = _canonical_bytes(payload)
    digest = _sha256(raw)
    object_path = (
        plan.paths.anchor_result_response_objects / plan.step_id / f"{digest}.json"
    )
    object_snapshot = _publish(
        object_path,
        raw,
        root=plan.paths.root,
        name="anchor result response observation",
    )
    link = _anchor_result_link_payload(plan, payload, object_snapshot)
    link_snapshot = _publish(
        plan.paths.anchor_result_response_links / f"{plan.step_id}.json",
        _canonical_bytes(link),
        root=plan.paths.root,
        name="anchor result response link",
    )
    return object_snapshot, link_snapshot


def _dispatch_and_capture_anchor_result(
    plan: AnchorResultDispatchPlan,
    *,
    transport: AnchorResultTransport | None = None,
    clock: Callable[[], datetime] | None = None,
) -> RecoveryResult:
    """Dispatch one frozen request after the four coordinator locks are released."""

    now = (clock or (lambda: datetime.now(timezone.utc)))()
    handle: BinaryIO | None = None
    network_action_performed = False
    try:
        try:
            handle = drain._acquire_lock(  # noqa: SLF001
                plan.paths.anchor_result_dispatch_lock,
                label="anchor result dispatch",
            )
        except drain.EpochDrainBusyError as exc:
            raise WorksetRecoveryBusyError(str(exc)) from exc
        except drain.EpochDrainError as exc:
            raise WorksetRecoveryIntegrityError(str(exc)) from exc
        _verify_anchor_result_dispatch_plan(plan)
        existing = _load_or_adopt_anchor_result_observation(plan)
        if existing is not None:
            _, _, link_snapshot, adoption = existing
            if adoption == "forward_adopted":
                status = "external_anchor_response_forward_adopted"
                reason = (
                    "one content-addressed anchor response observation was "
                    "forward-adopted without another network action"
                )
            else:
                status = "waiting_for_anchor_result_adapter"
                reason = (
                    "one exact anchor response observation already exists; "
                    "live-ledger result CAS remains unimplemented"
                )
            _write_status(
                plan.profile,
                plan.paths,
                now=now,
                status=status,
                reason=reason,
                key_id=plan.key_id,
                receipt=None,
                event=None,
                external_anchor_response_observation=link_snapshot.path,
                network_action_performed=False,
            )
            return RecoveryResult(
                status,
                reason,
                plan.paths.status,
                key_id=plan.key_id,
                external_anchor_response_observation_path=link_snapshot.path,
                network_action_performed=False,
            )
        token = _anchor_result_token(plan.action_contract)
        if token is None:
            reason = (
                "the frozen anchor bearer-token environment variable is missing "
                "or invalid; the same immutable request remains pending"
            )
            _write_status(
                plan.profile,
                plan.paths,
                now=now,
                status="waiting_for_external_anchor_token",
                reason=reason,
                key_id=plan.key_id,
                receipt=None,
                event=None,
                network_action_performed=False,
            )
            return RecoveryResult(
                "waiting_for_external_anchor_token",
                reason,
                plan.paths.status,
                key_id=plan.key_id,
            )
        network_action_performed = True
        client = transport or _default_anchor_result_transport
        try:
            response = client(plan.action_contract, token)
        except WorksetRecoveryNetworkWait:
            raise
        except WorksetRecoveryError:
            raise
        except Exception as exc:
            raise WorksetRecoveryIntegrityError(
                "Anchor result transport violated its reviewed contract"
            ) from exc
        observation = _anchor_result_observation_payload(plan, response, token=token)
        _, link_snapshot = _publish_anchor_result_observation(plan, observation)
        reason = (
            "one bounded anchor response was durably observed outside the four "
            "coordinator locks; no live-ledger result or recovery receipt was created"
        )
        _write_status(
            plan.profile,
            plan.paths,
            now=now,
            status="external_anchor_response_observed",
            reason=reason,
            key_id=plan.key_id,
            receipt=None,
            event=None,
            external_anchor_response_observation=link_snapshot.path,
            network_action_performed=True,
        )
        return RecoveryResult(
            "external_anchor_response_observed",
            reason,
            plan.paths.status,
            key_id=plan.key_id,
            external_anchor_response_observation_path=link_snapshot.path,
            network_action_performed=True,
        )
    except WorksetRecoveryNetworkWait:
        reason = (
            "the bounded anchor transport was retryable or delivery was ambiguous; "
            "no response observation was created and the same idempotency key remains pending"
        )
        _write_status(
            plan.profile,
            plan.paths,
            now=now,
            status="waiting_for_external_anchor_retry",
            reason=reason,
            key_id=plan.key_id,
            receipt=None,
            event=None,
            network_action_performed=network_action_performed,
        )
        return RecoveryResult(
            "waiting_for_external_anchor_retry",
            reason,
            plan.paths.status,
            key_id=plan.key_id,
            network_action_performed=network_action_performed,
        )
    except WorksetRecoveryIntegrityError as exc:
        try:
            _write_status(
                plan.profile,
                plan.paths,
                now=now,
                status="blocked_integrity",
                reason=f"{type(exc).__name__}:{exc}",
                key_id=plan.key_id,
                receipt=None,
                event=None,
                network_action_performed=network_action_performed,
            )
        except WorksetRecoveryError:
            pass
        raise
    finally:
        if handle is not None:
            try:
                drain._release_locks([handle])  # noqa: SLF001
            except drain.EpochDrainError as exc:
                if sys.exc_info()[0] is None:
                    raise WorksetRecoveryIntegrityError(str(exc)) from exc


def _live_projection(
    reservation: Reservation,
) -> tuple[Mapping[str, Any], Any, tuple[Any, ...]]:
    frozen = _frozen_live_prefix(reservation)
    return frozen.profile, frozen.paths, frozen.current_events


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


def _event_matches_spec(
    event: live_ledger.LedgerEvent, spec: live_ledger.EventSpec
) -> bool:
    return all(
        (
            event.event_key == spec.event_key,
            event.event_type == spec.event_type,
            event.target_date == spec.target_date,
            event.station == spec.station,
            event.issue_id == spec.issue_id,
            event.protocol_config_sha256 == spec.protocol_config_sha256,
            event.code_sha256 == spec.code_sha256,
            event.environment_sha256 == spec.environment_sha256,
            event.input_manifest_sha256 == spec.input_manifest_sha256,
            event.model_manifest_sha256 == spec.model_manifest_sha256,
            event.state_before_sha256 == spec.state_before_sha256,
            event.state_after_sha256 == spec.state_after_sha256,
            event.payload == dict(spec.payload),
        )
    )


def _anchor_request_output(
    contract: Mapping[str, Any], event: live_ledger.LedgerEvent
) -> ActionOutput:
    spec, expected = _event_spec_from_contract(contract)
    if (
        event.sequence_id != expected.sequence_id + 1
        or event.previous_entry_sha256 != expected.entry_sha256
        or not _event_matches_spec(event, spec)
    ):
        raise WorksetRecoveryIntegrityError(
            "Anchor request ledger event changed at its frozen position"
        )
    return ActionOutput(
        "live_anchor_request_event",
        None,
        {
            "schema_version": "ootang_live_anchor_request_action_output_v1",
            "live_epoch_id": expected.epoch_id,
            "event_key": event.event_key,
            "event_type": event.event_type,
            "sequence_id": event.sequence_id,
            "previous_entry_sha256": event.previous_entry_sha256,
            "entry_sha256": event.entry_sha256,
            "sealed_entry_sha256": spec.payload["sealed_entry_sha256"],
            "attempt": contract["attempt"],
            "event_spec_sha256": contract["event_spec_sha256"],
            "live_ledger_event_recorded": True,
            "network_action_performed": False,
        },
    )


def _is_sqlite_busy(exc: BaseException) -> bool:
    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        code = getattr(current, "sqlite_errorcode", None)
        if isinstance(code, int) and (code & 0xFF) in {
            sqlite3.SQLITE_BUSY,
            sqlite3.SQLITE_LOCKED,
        }:
            return True
        if "locked" in str(current).lower() or "busy" in str(current).lower():
            return True
        current = current.__cause__ or current.__context__
    return False


def _anchor_request_action(
    item: Mapping[str, Any],
    reservation: Reservation,
    action_contract: Mapping[str, object] | None,
) -> ActionOutput:
    if action_contract is None:
        raise WorksetRecoveryIntegrityError("Anchor request intent lost its contract")
    rebuilt = _anchor_request_contract(item, reservation)
    if action_contract != rebuilt:
        raise WorksetRecoveryIntegrityError("Anchor request intent contract changed")
    spec, expected = _event_spec_from_contract(action_contract)
    frozen = _frozen_live_prefix(reservation)
    if frozen.expected_pre_head != expected:
        raise WorksetRecoveryIntegrityError("Anchor request expected pre-head changed")
    try:
        ledger = live_ledger.AppendOnlyLedger(
            frozen.paths.ledger,
            timeout_seconds=LIVE_LEDGER_CAS_TIMEOUT_SECONDS,
        )
        live_cas.append_transaction_at_pre_head_v1(
            ledger,
            expected_pre_head=expected,
            specs=[spec],
        )
    except live_cas.LiveLedgerCasBusyErrorV1 as exc:
        raise WorksetRecoveryBusyError(str(exc)) from exc
    except live_ledger.LedgerError as exc:
        if _is_sqlite_busy(exc):
            raise WorksetRecoveryBusyError(
                "Live ledger CAS write lock is busy"
            ) from exc
        raise WorksetRecoveryIntegrityError(
            f"Anchor request CAS failed:{type(exc).__name__}:{exc}"
        ) from exc
    verified = _frozen_live_prefix(reservation)
    position = expected.event_count
    if len(verified.current_events) <= position:
        raise WorksetRecoveryIntegrityError("Anchor request CAS event disappeared")
    return _anchor_request_output(action_contract, verified.current_events[position])


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
    action_contract: Mapping[str, object] | None = None,
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
        if successor == "anchor_request_recorded":
            return _anchor_request_action(item, reservation, action_contract)
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
    item: Mapping[str, Any],
    payload: Mapping[str, Any],
    paths: RecoveryPaths,
    *,
    reservation: Reservation | None = None,
    item_intent: Mapping[str, Any] | None = None,
) -> None:
    """Verify immutable step evidence without replaying obsolete preconditions."""

    action = payload.get("action")
    output = payload.get("action_output")
    authority = item.get("authority")
    if not isinstance(authority, Mapping):
        raise WorksetRecoveryIntegrityError("Recovery action authority changed")
    if action == "anchor_request_recorded":
        if reservation is None or item_intent is None:
            raise WorksetRecoveryIntegrityError(
                "Recorded anchor request lost its frozen intent authority"
            )
        contract = item_intent.get("action_contract")
        if not isinstance(contract, Mapping) or contract != _anchor_request_contract(
            item, reservation
        ):
            raise WorksetRecoveryIntegrityError(
                "Recorded anchor request contract changed"
            )
        frozen = _frozen_live_prefix(reservation)
        _, expected_pre_head = _event_spec_from_contract(contract)
        position = expected_pre_head.event_count
        if len(frozen.current_events) <= position:
            raise WorksetRecoveryIntegrityError(
                "Recorded anchor request event disappeared"
            )
        expected = _anchor_request_output(contract, frozen.current_events[position])
    elif action == "trusted_time_request_der_repaired":
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
                "action_contract",
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
        _verify_item_intent_action_contract(
            item,
            reservation,
            action,
            intent_payload.get("action_contract"),
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
            _verify_recorded_action_contract(
                item,
                payload,
                paths,
                reservation=reservation,
                item_intent=intent_record[0],
            )
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
    external_anchor_response_observation: Path | None = None,
    network_action_performed: bool = False,
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
        "external_anchor_response_observation_path": (
            str(external_anchor_response_observation)
            if external_anchor_response_observation
            else None
        ),
        "network_action_performed": network_action_performed,
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
) -> RecoveryResult | AnchorResultDispatchPlan:
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
        response_links, response_object_steps = _anchor_result_observation_step_ids(
            paths
        )
        observation_steps = response_links | response_object_steps
        if (
            not observation_steps <= set(intents)
            or not response_links <= response_object_steps
        ):
            raise WorksetRecoveryIntegrityError(
                "Anchor result response observation namespace is orphaned"
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
        _deep_verify_locked_anchor_result_observations(
            profile,
            paths,
            reservation,
            item_by_id,
            intents,
            receipts,
            observation_steps,
        )
        chains = _receipt_chains(receipts)
        events, previous = _load_events(profile, paths, receipts)
        event_keys = {event["step_id"] for event in events}
        missing_event = set(receipts) - event_keys
        if observation_steps and missing_event:
            raise WorksetRecoveryIntegrityError(
                "Anchor result observation coexists with an unindexed recovery receipt"
            )
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
        pending_step_ids = set(intents) - set(receipts)
        if len(pending_step_ids) > 1:
            raise WorksetRecoveryIntegrityError(
                "Recovery has branched pending step intents"
            )
        if pending_step_ids:
            pending_step_id = next(iter(pending_step_ids))
            pending_payload, pending_snapshot = _strict_json(
                intents[pending_step_id], name="pending recovery step intent"
            )
            if pending_payload.get("action") == "anchor_result_recorded":
                key_id = pending_payload.get("key_id")
                if not isinstance(key_id, str) or key_id not in item_by_id:
                    raise WorksetRecoveryIntegrityError(
                        "Pending anchor result intent lost its manifest key"
                    )
                pending_contract = pending_payload.get("action_contract")
                if not isinstance(pending_contract, Mapping):
                    raise WorksetRecoveryIntegrityError(
                        "Pending anchor result intent lost its request contract"
                    )
                _verify_pending_anchor_result_head(
                    item_by_id[key_id], reservation, pending_contract
                )
                reason = (
                    "one immutable anchor-result request intent fences recovery; "
                    "the bounded transport will run after all four coordinator locks release"
                )
                _write_status(
                    profile,
                    paths,
                    now=now,
                    status="waiting_for_external_anchor_dispatch",
                    reason=reason,
                    key_id=key_id,
                    receipt=None,
                    event=None,
                )
                return AnchorResultDispatchPlan(
                    profile=profile,
                    paths=paths,
                    key_id=key_id,
                    step_id=pending_step_id,
                    item_intent=pending_snapshot,
                    action_contract=dict(pending_contract),
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
            if not (
                _item_adapter_supported(item, transition_action)
                or _item_intent_preparable(item, transition_action)
            ):
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
        try:
            action_contract = _action_contract(item, reservation, transition_action)
        except WorksetRecoveryExternalWait as exc:
            reason = str(exc)
            _write_status(
                profile,
                paths,
                now=now,
                status="waiting_for_external_anchor_endpoint",
                reason=reason,
                key_id=item["key_id"],
                receipt=None,
                event=None,
            )
            return RecoveryResult(
                "waiting_for_external_anchor_endpoint",
                reason,
                paths.status,
                item["key_id"],
            )
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
            action_contract=action_contract,
            now=now,
        )
        if transition_action in INTENT_PREPARATION_SUCCESSORS:
            if not isinstance(action_contract, Mapping):
                raise WorksetRecoveryIntegrityError(
                    "Prepared anchor result intent lost its request contract"
                )
            _verify_pending_anchor_result_head(item, reservation, action_contract)
            reason = (
                "one immutable anchor-result request intent was prepared; "
                "bounded dispatch will begin only after the four coordinator locks release"
            )
            _write_status(
                profile,
                paths,
                now=now,
                status="external_anchor_request_prepared",
                reason=reason,
                key_id=item["key_id"],
                receipt=None,
                event=None,
            )
            return AnchorResultDispatchPlan(
                profile=profile,
                paths=paths,
                key_id=item["key_id"],
                step_id=_step_id(item["key_id"], step_index, transition_action),
                item_intent=item_intent,
                action_contract=dict(action_contract),
            )
        action = _perform_action(
            item,
            reservation,
            paths,
            inputs,
            action_hook,
            action=transition_action,
            action_contract=action_contract,
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
    """Run one reviewed machine-only recovery step and any unlocked dispatch."""
    decision = _coordinate_epoch_workset_recovery(config_path=config_path)
    if isinstance(decision, RecoveryResult):
        return decision
    return _dispatch_and_capture_anchor_result(decision)


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
                "external_anchor_response_observation_path": (
                    str(result.external_anchor_response_observation_path)
                    if result.external_anchor_response_observation_path
                    else None
                ),
                "network_action_performed": result.network_action_performed,
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
