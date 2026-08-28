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
from monitoring import ootang_live_source as live_source  # noqa: E402
from monitoring import ootang_outcome_materializer as outcomes  # noqa: E402
from monitoring import ootang_prequential_live as live  # noqa: E402
from monitoring import ootang_trusted_time_shadow_core as trusted  # noqa: E402
from monitoring import ootang_verified_live as guard  # noqa: E402


DEFAULT_CONFIG_PATH = ROOT / "config" / "ootang_epoch_workset_recovery.v1.json"
DEFAULT_CONFIG_SHA256 = (
    "ef601148860ef5d4781e934f5cafb046eb82e0bac447c3d3f41ae05c19613c37"
)
MAX_CONTROL_BYTES = 4 * 1024 * 1024
ZERO_HASH = "0" * 64
LOCK_ORDER = ("manager", "cycle", "replay", "shadow")
SUPPORTED_SUCCESSORS = (
    "trusted_time_request_der_repaired",
    "anchor_receipt_repaired",
    "anchor_request_recorded",
    "anchor_result_recorded",
    "outcome_materialized",
    "outcome_or_revision_consumed",
    "outcome_batch_settled",
    "superseded_by_backfill",
)
INTENT_PREPARATION_SUCCESSORS = ("anchor_result_recorded",)
ANCHOR_REQUEST_CONTRACT_SCHEMA = "ootang_live_anchor_request_action_contract_v1"
ANCHOR_RESULT_REQUEST_CONTRACT_SCHEMA = (
    "ootang_live_anchor_result_request_action_contract_v1"
)
ANCHOR_RESULT_MAXIMUM_RESPONSE_BYTES = 1024 * 1024
ANCHOR_RESULT_USER_AGENT = "ootang-workset-recovery/1"
OUTCOME_SETTLEMENT_ADOPTION_CONTRACT_SCHEMA = (
    "ootang_live_outcome_settlement_adoption_contract_v1"
)
OUTCOME_MATERIALIZATION_CONTRACT_SCHEMA = (
    "ootang_outcome_materialization_action_contract_v1"
)
OUTCOME_SETTLEMENT_EVENT_COUNT = 43
OUTCOME_SETTLEMENT_EVENT_TYPES = (
    "outcome_batch_opened",
    *("outcome_revealed",) * 8,
    *(
        (
            "score_recorded",
            "expert_state_updated",
            "conformal_state_updated",
            "drift_state_updated",
        )
        * 8
    ),
    "site_score_recorded",
    "outcome_batch_settled",
)
OUTCOME_CONSUMPTION_CONTRACT_SCHEMA = (
    "ootang_live_outcome_consumption_action_contract_v2"
)
OUTCOME_REVISION_CONSUMPTION_CONTRACT_SCHEMA = (
    "ootang_live_settled_revision_consumption_action_contract_v1"
)
OUTCOME_CONSUMPTION_EVENT_COUNT = 43
OUTCOME_CONSUMPTION_EVENT_TYPES = OUTCOME_SETTLEMENT_EVENT_TYPES
OUTCOME_SETTLED_REVISION_EVENT_TYPES = (
    "outcome_revision",
    "revision_rescore_recorded",
) * 8
OUTCOME_SETTLED_REVISION_EVENT_COUNT = len(OUTCOME_SETTLED_REVISION_EVENT_TYPES)
ANCHOR_RESULT_FAILURE_TAXONOMY = {
    ("response_body", "response_too_large"),
    ("http_status", "redirect_rejected"),
    ("http_status", "request_rejected"),
    ("http_status", "unexpected_http_status"),
    ("transport", "final_url_changed"),
    ("transport", "unexpected_content_encoding"),
    ("transport", "unexpected_media_type"),
    ("transport", "unexpected_charset"),
    ("response_json", "invalid_strict_json"),
    ("response_contract", "invalid_anchor_response"),
}
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
    "live_anchor_result_adapter_implemented",
    "outcome_materialization_adapter_implemented",
    "live_outstanding_outcome_consumption_adapter_implemented",
    "live_settled_revision_consumption_adapter_implemented",
    "live_outcome_settlement_adoption_implemented",
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
        "expected_sha256": "857ae1ff031289d51c0a2947beeb2e47ceb9d48a3769db707c8f7f75750d48dc",
    },
    "manifest_implementation": {
        "path": "code/monitoring/ootang_epoch_workset_manifest.py",
        "expected_sha256": "0ca331c6827cd896b4c3261792eed5f933e104d4c40c4fbf3b7e416e9b1fd424",
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
    "outcome_materializer_implementation": {
        "path": "code/monitoring/ootang_outcome_materializer.py",
        "expected_sha256": "2a6478c191fdf1cd0df7d78324c6e4d7637241683956de621fd319feb3bcb642",
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
    "intent_schema_version": "ootang_epoch_workset_recovery_intent_v6",
    "item_intent_schema_version": "ootang_epoch_workset_recovery_step_intent_v6",
    "receipt_schema_version": "ootang_epoch_workset_recovery_step_receipt_v6",
    "event_schema_version": "ootang_epoch_workset_recovery_step_event_v2",
    "status_schema_version": "ootang_epoch_workset_recovery_status_v6",
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
class AnchorResultObservationEvidence:
    plan: AnchorResultDispatchPlan
    observation: Mapping[str, Any]
    object_snapshot: registry.ArtifactSnapshot
    link_snapshot: registry.ArtifactSnapshot | None


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


@dataclass(frozen=True)
class OutcomeConsumptionContext:
    frozen: FrozenLivePrefix
    outcome: live.OutcomeBatch
    selection_kind: str
    tip_receipt: outcomes.Artifact
    source_manifest: outcomes.Artifact
    previous_revision_id: str | None
    previous_outcome_sha256: str | None
    ledger_consumed_at_freeze: bool


@dataclass(frozen=True)
class OutcomeConsumptionPlan:
    context: OutcomeConsumptionContext
    expected_pre_head: live_cas.LiveLedgerPreHeadV1
    specs: tuple[live_ledger.EventSpec, ...]
    stored_events: tuple[live_ledger.LedgerEvent, ...]
    contract: Mapping[str, object]


@dataclass(frozen=True)
class OutcomeMaterializationPlan:
    materializer_profile: Mapping[str, Any]
    frozen: FrozenLivePrefix
    selection: outcomes._Selection | None
    contract: Mapping[str, object]


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


def _item_adapter_supported(
    item: Mapping[str, Any],
    action: str | None = None,
    previous_step_receipt: Mapping[str, Any] | None = None,
) -> bool:
    selected = action or item.get("canonical_successor_state")
    if selected not in SUPPORTED_SUCCESSORS:
        return False
    if selected == "outcome_materialized":
        authority = item.get("authority")
        if item.get("family") != "outcome_revision" or not isinstance(
            authority, Mapping
        ):
            return False
        record_type = authority.get("record_type")
        if record_type == "machine_selected_source_outcome":
            return (
                authority.get("action") == "outcome_materialized"
                and authority.get("selection_kind")
                in {"revision", "outstanding", "backfill"}
                and authority.get("terminal") is False
            )
        if record_type == "outcome_receipt_chain":
            return (
                authority.get("action") == "outcome_materialized"
                and authority.get("tip_published") is False
                and isinstance(authority.get("tip_ledger_consumed"), bool)
                and authority.get("terminal") is False
            )
        return False
    if selected == "outcome_or_revision_consumed":
        authority = item.get("authority")
        direct = (
            item.get("family") == "outcome_revision"
            and isinstance(authority, Mapping)
            and authority.get("record_type") == "outcome_receipt_chain"
            and authority.get("action") == "outcome_or_revision_consumed"
            and authority.get("tip_published") is True
            and authority.get("tip_ledger_consumed") is False
            and authority.get("terminal") is False
        )
        materialized = (
            previous_step_receipt is not None
            and previous_step_receipt.get("action") == "outcome_materialized"
            and previous_step_receipt.get("next_actions")
            == ["outcome_or_revision_consumed"]
            and previous_step_receipt.get("terminal_for_key") is False
            and _item_adapter_supported(item, "outcome_materialized")
        )
        return direct or materialized
    if selected == "outcome_batch_settled":
        dependencies = item.get("dependency_keys")
        return isinstance(dependencies, list) and bool(dependencies)
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
            objects = _strict_named_json(
                directory,
                suffix=".json",
                name="anchor result response object set",
            )
            # mkdir may become durable before the content-addressed object is
            # published.  An empty per-step directory is therefore crash
            # residue, not response authority.  A link for that empty step is
            # still rejected below as an orphan.
            if objects:
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


_OUTCOME_RECEIPT_AUTHORITY_KEYS = {
    "record_type",
    "target_date",
    "old_live_epoch_id",
    "tip_source_revision_id",
    "tip_receipt_sha256",
    "tip_exact_outcome_sha256",
    "tip_published",
    "tip_ledger_consumed",
    "receipts",
    "terminal",
    "action",
}
_OUTCOME_RECEIPT_RECORD_KEYS = {
    "source_revision_id",
    "revision_sequence_id",
    "receipt_sha256",
    "exact_outcome_sha256",
    "source_manifest_sha256",
    "ledger_consumed",
}
_OUTCOME_SELECTED_AUTHORITY_KEYS = {
    "record_type",
    "selection_kind",
    "target_date",
    "old_live_epoch_id",
    "outcome_source_id",
    "source_revision_id",
    "previous_revision_id",
    "previous_outcome_sha256",
    "source_snapshot_sequence_id",
    "source_snapshot_receipt_sha256",
    "live_issue_seal_entry_sha256",
    "terminal",
    "action",
}
_OUTCOME_MATERIALIZATION_CONTRACT_KEYS = {
    "schema_version",
    "writer_branch",
    "authority_record_type",
    "selection_kind",
    "old_live_epoch_id",
    "target_date",
    "outcome_source_id",
    "source_revision_id",
    "source_snapshot_sequence_id",
    "source_snapshot_receipt_sha256",
    "live_issue_seal_entry_sha256",
    "source_exported_at_utc",
    "previous_revision_id",
    "previous_outcome_sha256",
    "revision_sequence_id",
    "previous_receipt",
    "input_manifest_payload",
    "input_manifest",
    "outcome_payload",
    "exact_outcome_object",
    "receipt_payload",
    "receipt",
    "item_artifacts_sha256",
}
_OUTCOME_MATERIALIZATION_OUTPUT_KEYS = {
    "writer_branch",
    "selection_kind",
    "target_date",
    "outcome_source_id",
    "source_revision_id",
    "source_snapshot_sequence_id",
    "source_snapshot_receipt_sha256",
    "input_manifest_sha256",
    "exact_outcome_sha256",
    "receipt_sha256",
    "materializer_receipt",
    "input_manifest",
    "immutable_receipt_verified",
    "fully_published_verified",
    "canonical_materializer_writer_reused",
    "current_pointer_required_for_historical_replay",
    "live_ledger_event_count",
    "live_ledger_mutation_performed",
    "network_action_performed",
}
_MANIFEST_ARTIFACT_KEYS = {"role", "root", "path", "sha256", "size_bytes"}


class _EventSpecCollector:
    """Capture one frozen canonical writer transaction without mutating SQLite."""

    def __init__(self, prefix: Sequence[live_ledger.LedgerEvent]) -> None:
        self._prefix = tuple(prefix)
        self.specs: tuple[live_ledger.EventSpec, ...] | None = None

    def read_events(self) -> tuple[live_ledger.LedgerEvent, ...]:
        return self._prefix

    def append_transaction(
        self, specs: Sequence[live_ledger.EventSpec]
    ) -> tuple[live_ledger.LedgerEvent, ...]:
        if self.specs is not None:
            raise WorksetRecoveryIntegrityError(
                "Canonical outcome writer emitted multiple transactions"
            )
        captured = tuple(specs)
        if not captured:
            raise WorksetRecoveryIntegrityError(
                "Canonical outcome writer emitted an empty transaction"
            )
        try:
            for spec in captured:
                live_ledger._prepare_spec(spec)  # noqa: SLF001
        except live_ledger.LedgerError as exc:
            raise WorksetRecoveryIntegrityError(
                "Canonical outcome writer emitted an invalid EventSpec"
            ) from exc
        self.specs = captured
        return ()


def _artifact_identity(
    *,
    role: str,
    path: Path,
    sha256: str,
    size_bytes: int,
    active_root: Path,
) -> tuple[str, str, str, str, int]:
    try:
        relative = path.resolve().relative_to(active_root.resolve()).as_posix()
    except (TypeError, ValueError) as exc:
        raise WorksetRecoveryIntegrityError(
            "Outcome receipt artifact escaped the active runtime"
        ) from exc
    return role, "active", relative, sha256, size_bytes


def _materializer_artifact_payload(
    artifact: outcomes.Artifact,
) -> dict[str, object]:
    return {
        "path": str(artifact.path.resolve()),
        "sha256": artifact.sha256,
        "size_bytes": artifact.size_bytes,
    }


def _declared_materializer_artifact(value: object, *, name: str) -> outcomes.Artifact:
    record = _exact(value, {"path", "sha256", "size_bytes"}, name=name)
    path = record["path"]
    size = record["size_bytes"]
    digest = _hash_text(record["sha256"], name=f"{name} digest")
    if (
        not isinstance(path, str)
        or not path
        or not Path(path).is_absolute()
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size <= 0
    ):
        raise WorksetRecoveryIntegrityError(f"{name} identity changed")
    return outcomes.Artifact(Path(path).resolve(), digest, size)


def _expected_materializer_object(
    profile: Mapping[str, Any],
    root: Path,
    payload: Mapping[str, Any],
    *,
    suffix: str,
) -> outcomes.Artifact:
    raw = outcomes._canonical_bytes(dict(payload))  # noqa: SLF001
    digest = _sha256(raw)
    return outcomes.Artifact(
        path=outcomes._object_path(  # noqa: SLF001
            outcomes._runtime_path(profile, root, "objects"),  # noqa: SLF001
            digest,
            suffix=suffix,
        ),
        sha256=digest,
        size_bytes=len(raw),
    )


def _active_artifact_reference(
    artifact: outcomes.Artifact, active_root: Path
) -> dict[str, object]:
    try:
        relative = artifact.path.resolve().relative_to(active_root.resolve())
    except ValueError as exc:
        raise WorksetRecoveryIntegrityError(
            "Outcome materialization output escaped the active runtime"
        ) from exc
    return {
        "path": relative.as_posix(),
        "sha256": artifact.sha256,
        "size_bytes": artifact.size_bytes,
    }


def _active_artifact_from_reference(
    value: object,
    reservation: Reservation,
    *,
    name: str,
) -> outcomes.Artifact:
    reference = _exact(
        value,
        {"path", "sha256", "size_bytes"},
        name=name,
    )
    try:
        path = registry._contained(  # noqa: SLF001
            reservation.paths.active_root,
            reference["path"],
            name=name,
        )
        artifact = outcomes._artifact_from_mapping(  # noqa: SLF001
            {**reference, "path": str(path)},
            name=name,
        )
    except (registry.EpochRegistryError, outcomes.OutcomeMaterializerError) as exc:
        raise WorksetRecoveryIntegrityError(str(exc)) from exc
    if _active_artifact_reference(artifact, reservation.paths.active_root) != reference:
        raise WorksetRecoveryIntegrityError(f"{name} active-root binding changed")
    return artifact


def _outcome_materialization_item_artifacts_sha256(
    item: Mapping[str, Any],
) -> str:
    artifacts = item.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise WorksetRecoveryIntegrityError(
            "Outcome materialization item lost its artifact bindings"
        )
    for artifact in artifacts:
        _exact(
            artifact,
            _MANIFEST_ARTIFACT_KEYS,
            name="outcome materialization artifact",
        )
    return _sha256(_canonical_bytes(artifacts))


def _validate_selected_source_artifact_bindings(
    item: Mapping[str, Any],
    reservation: Reservation,
    profile: Mapping[str, Any],
    input_manifest: Mapping[str, Any],
) -> None:
    raw_artifacts = item.get("artifacts")
    if not isinstance(raw_artifacts, list):
        raise WorksetRecoveryIntegrityError(
            "Selected source outcome artifacts changed type"
        )
    by_role: dict[str, list[dict[str, Any]]] = {}
    for raw in raw_artifacts:
        artifact = _exact(raw, _MANIFEST_ARTIFACT_KEYS, name="selected source artifact")
        role = artifact["role"]
        if not isinstance(role, str):
            raise WorksetRecoveryIntegrityError(
                "Selected source artifact role changed type"
            )
        by_role.setdefault(role, []).append(artifact)
    expected_roles = {
        "current_source_pointer",
        "activation_source_manifest",
        "current_source_semantic_manifest",
        "current_source_revision_head",
        "current_source_snapshot_receipt",
    }
    if set(by_role) != expected_roles or any(
        len(values) != 1 for values in by_role.values()
    ):
        raise WorksetRecoveryIntegrityError("Selected source artifact role set changed")
    source = input_manifest.get("source")
    if not isinstance(source, Mapping):
        raise WorksetRecoveryIntegrityError(
            "Selected outcome input source bindings changed"
        )
    mapping = {
        "activation_source_manifest": "activation_source_manifest",
        "current_source_semantic_manifest": "semantic_manifest",
        "current_source_revision_head": "target_revision_receipt",
        "current_source_snapshot_receipt": "snapshot_receipt",
    }
    for role, source_name in mapping.items():
        try:
            bound = outcomes._artifact_from_mapping(  # noqa: SLF001
                source[source_name], name=f"selected source {source_name}"
            )
        except outcomes.OutcomeMaterializerError as exc:
            raise WorksetRecoveryIntegrityError(str(exc)) from exc
        obligation = by_role[role][0]
        expected = _artifact_identity(
            role=role,
            path=bound.path,
            sha256=bound.sha256,
            size_bytes=bound.size_bytes,
            active_root=reservation.paths.active_root,
        )
        actual = (
            obligation["role"],
            obligation["root"],
            obligation["path"],
            obligation["sha256"],
            obligation["size_bytes"],
        )
        if actual != expected:
            raise WorksetRecoveryIntegrityError(
                f"Selected source {role} binding changed"
            )
    pointer_path = live_source._runtime_path(  # noqa: SLF001
        profile["_deploy_profile"],
        "current_source_pointer",
        root=reservation.paths.active_root,
    )
    pointer = by_role["current_source_pointer"][0]
    if _artifact_path(pointer, reservation) != pointer_path.resolve():
        raise WorksetRecoveryIntegrityError(
            "Selected current source pointer path changed"
        )


def _outcome_materialization_receipt_payload(
    profile: Mapping[str, Any],
    root: Path,
    *,
    target: date,
    revision: str,
    exact_object: outcomes.Artifact,
    input_manifest: outcomes.Artifact,
    input_manifest_payload: Mapping[str, Any],
    outcome_payload: Mapping[str, Any],
    previous_receipt: outcomes.Artifact | None,
    revision_sequence_id: int,
) -> tuple[dict[str, object], outcomes.Artifact]:
    active_path = outcomes._outcome_inbox_path(  # noqa: SLF001
        profile, root, target
    )
    scientific_semantics = input_manifest_payload.get("scientific_semantics_sha256")
    _hash_text(
        scientific_semantics,
        name="materialization input scientific semantics",
    )
    semantic_outcome = dict(outcome_payload)
    semantic_outcome["source_manifest"] = {
        "scientific_semantics_sha256": scientific_semantics,
        "exact_manifest_sha256": input_manifest.sha256,
    }
    payload = {
        "schema_version": profile["outcome"]["receipt_schema_version"],
        "target_date": target.isoformat(),
        "source_revision_id": revision,
        "active_outcome_path": str(active_path.resolve()),
        "scientific_semantics_sha256": outcomes._canonical_digest(  # noqa: SLF001
            semantic_outcome
        ),
        "input_manifest_sha256": input_manifest.sha256,
        "exact_outcome_object": _materializer_artifact_payload(exact_object),
        "revision_sequence_id": revision_sequence_id,
        "previous_receipt": (
            _materializer_artifact_payload(previous_receipt)
            if previous_receipt is not None
            else None
        ),
    }
    raw = outcomes._canonical_bytes(payload)  # noqa: SLF001
    path = outcomes._receipt_path(  # noqa: SLF001
        outcomes._runtime_path(profile, root, "outcome_receipts"),  # noqa: SLF001
        target,
        revision,
    )
    return payload, outcomes.Artifact(path.resolve(), _sha256(raw), len(raw))


def _outcome_materialization_contract_record(
    item: Mapping[str, Any],
    reservation: Reservation,
    contract: Mapping[str, Any],
) -> tuple[
    dict[str, Any],
    outcomes.Artifact,
    outcomes.Artifact,
    outcomes.Artifact,
]:
    checked = _exact(
        dict(contract),
        _OUTCOME_MATERIALIZATION_CONTRACT_KEYS,
        name="outcome materialization contract",
    )
    if (
        checked["schema_version"] != OUTCOME_MATERIALIZATION_CONTRACT_SCHEMA
        or checked["writer_branch"]
        not in {"registered_tip_reconciliation", "machine_selected_source"}
        or checked["authority_record_type"]
        not in {"outcome_receipt_chain", "machine_selected_source_outcome"}
        or checked["selection_kind"] not in {"revision", "outstanding", "backfill"}
        or checked["writer_branch"]
        != {
            "outcome_receipt_chain": "registered_tip_reconciliation",
            "machine_selected_source_outcome": "machine_selected_source",
        }[checked["authority_record_type"]]
    ):
        raise WorksetRecoveryIntegrityError(
            "Outcome materialization contract branch changed"
        )
    try:
        target = date.fromisoformat(checked["target_date"])
    except (TypeError, ValueError) as exc:
        raise WorksetRecoveryIntegrityError(
            "Outcome materialization target changed"
        ) from exc
    revision = checked["source_revision_id"]
    source_id = checked["outcome_source_id"]
    snapshot_sequence = checked["source_snapshot_sequence_id"]
    if (
        target.isoformat() != checked["target_date"]
        or not isinstance(revision, str)
        or not revision
        or revision != revision.strip()
        or not isinstance(source_id, str)
        or not source_id
        or source_id != source_id.strip()
        or not isinstance(snapshot_sequence, int)
        or isinstance(snapshot_sequence, bool)
        or snapshot_sequence <= 0
        or not isinstance(checked["revision_sequence_id"], int)
        or isinstance(checked["revision_sequence_id"], bool)
        or checked["revision_sequence_id"] <= 0
    ):
        raise WorksetRecoveryIntegrityError("Outcome materialization identity changed")
    for name in (
        "source_snapshot_receipt_sha256",
        "live_issue_seal_entry_sha256",
        "item_artifacts_sha256",
    ):
        _hash_text(checked[name], name=f"outcome materialization {name}")
    _parse_utc(
        checked["source_exported_at_utc"],
        name="outcome materialization source export",
    )
    if checked["previous_revision_id"] is None:
        if checked["previous_outcome_sha256"] is not None:
            raise WorksetRecoveryIntegrityError(
                "Outcome materialization previous outcome lost its revision"
            )
    else:
        if (
            not isinstance(checked["previous_revision_id"], str)
            or not checked["previous_revision_id"]
        ):
            raise WorksetRecoveryIntegrityError(
                "Outcome materialization previous revision changed"
            )
        _hash_text(
            checked["previous_outcome_sha256"],
            name="outcome materialization previous outcome",
        )
    if (checked["selection_kind"] == "revision") is not (
        checked["previous_revision_id"] is not None
    ):
        raise WorksetRecoveryIntegrityError(
            "Outcome materialization predecessor branch changed"
        )

    input_manifest = _declared_materializer_artifact(
        checked["input_manifest"], name="materialization input manifest"
    )
    exact_object = _declared_materializer_artifact(
        checked["exact_outcome_object"], name="materialization exact outcome"
    )
    receipt = _declared_materializer_artifact(
        checked["receipt"], name="materialization receipt"
    )
    input_payload = checked["input_manifest_payload"]
    outcome_payload = checked["outcome_payload"]
    receipt_payload = checked["receipt_payload"]
    if not all(
        isinstance(value, dict)
        for value in (input_payload, outcome_payload, receipt_payload)
    ):
        raise WorksetRecoveryIntegrityError(
            "Outcome materialization payload changed type"
        )
    expected_input = _expected_materializer_object(
        outcomes.load_config(),
        reservation.paths.active_root,
        input_payload,
        suffix="outcome-input.json",
    )
    expected_exact = _expected_materializer_object(
        outcomes.load_config(),
        reservation.paths.active_root,
        outcome_payload,
        suffix="outcome.json",
    )
    expected_receipt_raw = outcomes._canonical_bytes(receipt_payload)  # noqa: SLF001
    expected_receipt = outcomes.Artifact(
        outcomes._receipt_path(  # noqa: SLF001
            outcomes._runtime_path(  # noqa: SLF001
                outcomes.load_config(),
                reservation.paths.active_root,
                "outcome_receipts",
            ),
            target,
            revision,
        ).resolve(),
        _sha256(expected_receipt_raw),
        len(expected_receipt_raw),
    )
    source = input_payload.get("source")
    source_record = input_payload.get("source_record")
    if (
        input_manifest != expected_input
        or exact_object != expected_exact
        or receipt != expected_receipt
        or input_payload.get("target_date") != target.isoformat()
        or input_payload.get("selection_kind") != checked["selection_kind"]
        or input_payload.get("outcome_source_id") != source_id
        or not isinstance(source, dict)
        or not isinstance(source_record, dict)
        or source_record.get("date") != target.isoformat()
        or source_record.get("revision_id") != revision
        or outcome_payload.get("target_date") != target.isoformat()
        or outcome_payload.get("outcome_source_id") != source_id
        or outcome_payload.get("source_revision_id") != revision
        or outcome_payload.get("source_manifest")
        != _materializer_artifact_payload(input_manifest)
        or receipt_payload.get("target_date") != target.isoformat()
        or receipt_payload.get("source_revision_id") != revision
        or receipt_payload.get("input_manifest_sha256") != input_manifest.sha256
        or receipt_payload.get("exact_outcome_object")
        != _materializer_artifact_payload(exact_object)
        or receipt_payload.get("revision_sequence_id")
        != checked["revision_sequence_id"]
        or receipt_payload.get("previous_receipt") != checked["previous_receipt"]
        or checked["item_artifacts_sha256"]
        != _outcome_materialization_item_artifacts_sha256(item)
    ):
        raise WorksetRecoveryIntegrityError(
            "Outcome materialization deterministic payload changed"
        )
    snapshot = source.get("snapshot_receipt")
    try:
        snapshot_artifact = outcomes._artifact_from_mapping(  # noqa: SLF001
            snapshot, name="materialization source snapshot receipt"
        )
        snapshot_payload, _ = outcomes._read_json(  # noqa: SLF001
            snapshot_artifact.path,
            name="materialization source snapshot receipt",
        )
    except outcomes.OutcomeMaterializerError as exc:
        raise WorksetRecoveryIntegrityError(str(exc)) from exc
    if (
        snapshot_artifact.sha256 != checked["source_snapshot_receipt_sha256"]
        or snapshot_payload.get("schema_version") != "ootang_source_snapshot_receipt_v1"
        or snapshot_payload.get("snapshot_sequence_id") != snapshot_sequence
        or snapshot_payload.get("outcome_source_id") != source_id
    ):
        raise WorksetRecoveryIntegrityError(
            "Outcome materialization source snapshot changed"
        )

    authority_type = checked["authority_record_type"]
    if authority_type == "machine_selected_source_outcome":
        authority = _exact(
            item.get("authority"),
            _OUTCOME_SELECTED_AUTHORITY_KEYS,
            name="selected outcome materialization authority",
        )
        expected_authority = {
            "record_type": authority_type,
            "selection_kind": checked["selection_kind"],
            "target_date": checked["target_date"],
            "old_live_epoch_id": checked["old_live_epoch_id"],
            "outcome_source_id": checked["outcome_source_id"],
            "source_revision_id": checked["source_revision_id"],
            "previous_revision_id": checked["previous_revision_id"],
            "previous_outcome_sha256": checked["previous_outcome_sha256"],
            "source_snapshot_sequence_id": checked["source_snapshot_sequence_id"],
            "source_snapshot_receipt_sha256": checked["source_snapshot_receipt_sha256"],
            "live_issue_seal_entry_sha256": checked["live_issue_seal_entry_sha256"],
            "terminal": False,
            "action": "outcome_materialized",
        }
        if authority != expected_authority:
            raise WorksetRecoveryIntegrityError(
                "Selected outcome materialization authority changed"
            )
    else:
        authority = _exact(
            item.get("authority"),
            _OUTCOME_RECEIPT_AUTHORITY_KEYS,
            name="registered outcome materialization authority",
        )
        if (
            authority["record_type"] != authority_type
            or authority["action"] != "outcome_materialized"
            or authority["target_date"] != checked["target_date"]
            or authority["old_live_epoch_id"] != checked["old_live_epoch_id"]
            or authority["tip_source_revision_id"] != checked["source_revision_id"]
            or authority["tip_receipt_sha256"] != receipt.sha256
            or authority["tip_exact_outcome_sha256"] != exact_object.sha256
            or authority["tip_published"] is not False
            or not isinstance(authority["tip_ledger_consumed"], bool)
            or authority["terminal"] is not False
        ):
            raise WorksetRecoveryIntegrityError(
                "Registered outcome materialization authority changed"
            )
    return checked, input_manifest, exact_object, receipt


def _machine_selected_outcome_materialization_plan(
    item: Mapping[str, Any], reservation: Reservation
) -> OutcomeMaterializationPlan:
    authority = _exact(
        item.get("authority"),
        _OUTCOME_SELECTED_AUTHORITY_KEYS,
        name="machine-selected outcome authority",
    )
    if (
        item.get("family") != "outcome_revision"
        or item.get("canonical_successor_state") != "outcome_materialized"
        or authority["record_type"] != "machine_selected_source_outcome"
        or authority["action"] != "outcome_materialized"
        or authority["selection_kind"] not in {"revision", "outstanding", "backfill"}
        or authority["terminal"] is not False
    ):
        raise WorksetRecoveryIntegrityError(
            "Machine-selected outcome materialization scope changed"
        )
    try:
        target = date.fromisoformat(authority["target_date"])
    except (TypeError, ValueError) as exc:
        raise WorksetRecoveryIntegrityError(
            "Machine-selected outcome target changed"
        ) from exc
    if target.isoformat() != authority["target_date"]:
        raise WorksetRecoveryIntegrityError(
            "Machine-selected outcome target is not canonical"
        )
    revision = authority["source_revision_id"]
    source_id = authority["outcome_source_id"]
    previous_revision = authority["previous_revision_id"]
    previous_outcome = authority["previous_outcome_sha256"]
    if (
        not isinstance(revision, str)
        or not revision
        or revision != revision.strip()
        or not isinstance(source_id, str)
        or not source_id
        or source_id != source_id.strip()
        or not isinstance(authority["source_snapshot_sequence_id"], int)
        or isinstance(authority["source_snapshot_sequence_id"], bool)
        or authority["source_snapshot_sequence_id"] <= 0
    ):
        raise WorksetRecoveryIntegrityError(
            "Machine-selected outcome source identity changed"
        )
    if authority["selection_kind"] == "revision":
        if (
            not isinstance(previous_revision, str)
            or not previous_revision
            or previous_revision != previous_revision.strip()
        ):
            raise WorksetRecoveryIntegrityError(
                "Machine-selected outcome predecessor revision changed"
            )
        _hash_text(
            previous_outcome,
            name="machine-selected previous outcome",
        )
    elif previous_revision is not None or previous_outcome is not None:
        raise WorksetRecoveryIntegrityError(
            "First outcome selection unexpectedly gained a predecessor"
        )
    _hash_text(
        authority["source_snapshot_receipt_sha256"],
        name="machine-selected source snapshot receipt",
    )
    _hash_text(
        authority["live_issue_seal_entry_sha256"],
        name="machine-selected live issue seal",
    )
    frozen = _frozen_live_prefix(reservation)
    if authority["old_live_epoch_id"] != frozen.projection.epoch_id:
        raise WorksetRecoveryIntegrityError(
            "Machine-selected outcome old epoch changed"
        )
    try:
        current_projection = live._reconstruct_projection(  # noqa: SLF001
            frozen.current_events, frozen.profile, frozen.prerequisites
        )
        profile = outcomes.load_config()
        source = live_source.load_current_source(
            profile["_deploy_profile"],
            runtime_root=reservation.paths.active_root,
            project_root=ROOT,
        )
        selection = outcomes._select_target(source, current_projection)  # noqa: SLF001
    except (
        outcomes.OutcomeMaterializerError,
        live_source.SourceError,
        live.LiveInputError,
        live.LiveIntegrityError,
    ) as exc:
        raise WorksetRecoveryIntegrityError(
            f"Frozen source outcome selection failed:{type(exc).__name__}:{exc}"
        ) from exc
    if selection is None:
        raise WorksetRecoveryIntegrityError(
            "Frozen source no longer selects the reserved outcome"
        )
    selected_revision = getattr(selection.record, "revision_id", None)
    if (
        selection.kind != authority["selection_kind"]
        or selection.target_date != target
        or selected_revision != revision
        or selection.previous_revision_id != previous_revision
        or selection.previous_outcome_sha256 != previous_outcome
        or source.outcome_source_id != source_id
        or source.snapshot_sequence_id != authority["source_snapshot_sequence_id"]
        or source.snapshot_receipt is None
        or source.snapshot_receipt.sha256 != authority["source_snapshot_receipt_sha256"]
    ):
        raise WorksetRecoveryIntegrityError(
            "Frozen source selector disagrees with the reserved outcome"
        )
    seal = next(
        (
            event
            for event in frozen.current_events
            if event.event_type == "issue_batch_sealed"
            and event.target_date == target.isoformat()
        ),
        None,
    )
    seal_hash = seal.entry_sha256 if seal is not None else ZERO_HASH
    if seal_hash != authority["live_issue_seal_entry_sha256"]:
        raise WorksetRecoveryIntegrityError(
            "Machine-selected outcome seal binding changed"
        )
    try:
        input_payload = outcomes._build_input_manifest(  # noqa: SLF001
            profile, source, selection
        )
        _validate_selected_source_artifact_bindings(
            item, reservation, profile, input_payload
        )
        input_manifest = _expected_materializer_object(
            profile,
            reservation.paths.active_root,
            input_payload,
            suffix="outcome-input.json",
        )
        outcome_payload = outcomes._outcome_payload(  # noqa: SLF001
            profile,
            selection,
            input_manifest,
            outcome_source_id=source_id,
        )
        exact_object = _expected_materializer_object(
            profile,
            reservation.paths.active_root,
            outcome_payload,
            suffix="outcome.json",
        )
        chain = outcomes._scan_receipt_chain(  # noqa: SLF001
            target=target,
            profile=profile,
            root=reservation.paths.active_root,
            live_module=live,
            live_profile=frozen.profile,
            prerequisites=frozen.prerequisites,
        )
    except outcomes.OutcomeMaterializerError as exc:
        raise WorksetRecoveryIntegrityError(
            f"Frozen source outcome plan failed:{type(exc).__name__}:{exc}"
        ) from exc
    previous_registered: outcomes._RegisteredOutcome | None = None
    if selection.previous_revision_id is None:
        if chain is not None:
            raise WorksetRecoveryIntegrityError(
                "Reserved first outcome already has a receipt chain"
            )
        revision_sequence_id = 1
    else:
        if (
            chain is None
            or chain.tip.payload.get("source_revision_id")
            != selection.previous_revision_id
            or chain.tip.exact_object.sha256 != selection.previous_outcome_sha256
        ):
            raise WorksetRecoveryIntegrityError(
                "Reserved outcome revision lost its registered predecessor"
            )
        previous_registered = chain.tip
        revision_sequence_id = chain.tip.revision_sequence_id + 1
    receipt_payload, receipt = _outcome_materialization_receipt_payload(
        profile,
        reservation.paths.active_root,
        target=target,
        revision=revision,
        exact_object=exact_object,
        input_manifest=input_manifest,
        input_manifest_payload=input_payload,
        outcome_payload=outcome_payload,
        previous_receipt=(
            previous_registered.receipt if previous_registered is not None else None
        ),
        revision_sequence_id=revision_sequence_id,
    )
    contract: dict[str, object] = {
        "schema_version": OUTCOME_MATERIALIZATION_CONTRACT_SCHEMA,
        "writer_branch": "machine_selected_source",
        "authority_record_type": "machine_selected_source_outcome",
        "selection_kind": selection.kind,
        "old_live_epoch_id": frozen.projection.epoch_id,
        "target_date": target.isoformat(),
        "outcome_source_id": source_id,
        "source_revision_id": revision,
        "source_snapshot_sequence_id": source.snapshot_sequence_id,
        "source_snapshot_receipt_sha256": source.snapshot_receipt.sha256,
        "live_issue_seal_entry_sha256": seal_hash,
        "source_exported_at_utc": source.exported_at_utc,
        "previous_revision_id": selection.previous_revision_id,
        "previous_outcome_sha256": selection.previous_outcome_sha256,
        "revision_sequence_id": revision_sequence_id,
        "previous_receipt": (
            _materializer_artifact_payload(previous_registered.receipt)
            if previous_registered is not None
            else None
        ),
        "input_manifest_payload": input_payload,
        "input_manifest": _materializer_artifact_payload(input_manifest),
        "outcome_payload": outcome_payload,
        "exact_outcome_object": _materializer_artifact_payload(exact_object),
        "receipt_payload": receipt_payload,
        "receipt": _materializer_artifact_payload(receipt),
        "item_artifacts_sha256": (_outcome_materialization_item_artifacts_sha256(item)),
    }
    _outcome_materialization_contract_record(item, reservation, contract)
    return OutcomeMaterializationPlan(profile, frozen, selection, contract)


def _registered_tip_outcome_materialization_plan(
    item: Mapping[str, Any], reservation: Reservation
) -> OutcomeMaterializationPlan:
    authority = _exact(
        item.get("authority"),
        _OUTCOME_RECEIPT_AUTHORITY_KEYS,
        name="registered outcome materialization authority",
    )
    if (
        item.get("family") != "outcome_revision"
        or item.get("canonical_successor_state") != "outcome_materialized"
        or authority["record_type"] != "outcome_receipt_chain"
        or authority["action"] != "outcome_materialized"
        or authority["tip_published"] is not False
        or not isinstance(authority["tip_ledger_consumed"], bool)
        or authority["terminal"] is not False
    ):
        raise WorksetRecoveryIntegrityError(
            "Registered outcome materialization scope changed"
        )
    try:
        target = date.fromisoformat(authority["target_date"])
    except (TypeError, ValueError) as exc:
        raise WorksetRecoveryIntegrityError(
            "Registered outcome materialization target changed"
        ) from exc
    revision = authority["tip_source_revision_id"]
    if (
        target.isoformat() != authority["target_date"]
        or not isinstance(revision, str)
        or not revision
        or revision != revision.strip()
    ):
        raise WorksetRecoveryIntegrityError(
            "Registered outcome materialization identity changed"
        )
    _hash_text(authority["tip_receipt_sha256"], name="registered outcome receipt")
    _hash_text(authority["tip_exact_outcome_sha256"], name="registered exact outcome")
    frozen = _frozen_live_prefix(reservation)
    if authority["old_live_epoch_id"] != frozen.projection.epoch_id:
        raise WorksetRecoveryIntegrityError(
            "Registered outcome materialization epoch changed"
        )
    try:
        profile = outcomes.load_config()
        chain = outcomes._scan_receipt_chain(  # noqa: SLF001
            target=target,
            profile=profile,
            root=reservation.paths.active_root,
            live_module=live,
            live_profile=frozen.profile,
            prerequisites=frozen.prerequisites,
        )
        if chain is None:
            raise WorksetRecoveryIntegrityError(
                "Registered outcome receipt chain disappeared"
            )
        active_path = outcomes._outcome_inbox_path(  # noqa: SLF001
            profile, reservation.paths.active_root, target
        )
        active_raw = outcomes._legal_active_bytes(chain, active_path)  # noqa: SLF001
    except outcomes.OutcomeMaterializerError as exc:
        raise WorksetRecoveryIntegrityError(
            f"Registered outcome chain replay failed:{type(exc).__name__}:{exc}"
        ) from exc
    if (
        chain.tip.payload.get("source_revision_id") != revision
        or chain.tip.receipt.sha256 != authority["tip_receipt_sha256"]
        or chain.tip.exact_object.sha256 != authority["tip_exact_outcome_sha256"]
        or (
            chain.pointed is not None
            and chain.pointed.receipt.sha256 == chain.tip.receipt.sha256
            and active_raw == chain.tip.raw
        )
    ):
        raise WorksetRecoveryIntegrityError(
            "Registered outcome tip state changed before intent"
        )
    known_at_freeze = frozen.projection.revision_ids.get(target.isoformat(), {})
    receipt_records = []
    for registered in chain.receipts:
        source_manifest = outcomes._artifact_from_mapping(  # noqa: SLF001
            registered.payload["source_manifest"],
            name="registered materialization source manifest",
        )
        registered_revision = registered.payload.get("source_revision_id")
        receipt_records.append(
            {
                "source_revision_id": registered_revision,
                "revision_sequence_id": registered.revision_sequence_id,
                "receipt_sha256": registered.receipt.sha256,
                "exact_outcome_sha256": registered.exact_object.sha256,
                "source_manifest_sha256": source_manifest.sha256,
                "ledger_consumed": registered_revision in known_at_freeze,
            }
        )
    if authority["receipts"] != receipt_records:
        raise WorksetRecoveryIntegrityError(
            "Registered outcome materialization receipt history changed"
        )
    tip = chain.tip
    try:
        input_manifest = outcomes._artifact_from_mapping(  # noqa: SLF001
            tip.payload["source_manifest"],
            name="registered materialization input manifest",
        )
        input_payload = outcomes._validate_input_manifest(  # noqa: SLF001
            input_manifest,
            profile=profile,
            root=reservation.paths.active_root,
        )
        snapshot = outcomes._artifact_from_mapping(  # noqa: SLF001
            input_payload["source"]["snapshot_receipt"],
            name="registered materialization snapshot receipt",
        )
        snapshot_payload, _ = outcomes._read_json(  # noqa: SLF001
            snapshot.path, name="registered materialization snapshot receipt"
        )
        receipt_payload, _ = outcomes._read_json(  # noqa: SLF001
            tip.receipt.path, name="registered materialization receipt"
        )
    except outcomes.OutcomeMaterializerError as exc:
        raise WorksetRecoveryIntegrityError(str(exc)) from exc
    selection_kind = input_payload.get("selection_kind")
    if selection_kind not in {"revision", "outstanding", "backfill"}:
        raise WorksetRecoveryIntegrityError(
            "Registered outcome materialization selection kind changed"
        )
    previous = chain.receipts[-2] if len(chain.receipts) > 1 else None
    seal = next(
        (
            event
            for event in frozen.frozen_events
            if event.event_type == "issue_batch_sealed"
            and event.target_date == target.isoformat()
        ),
        None,
    )
    contract: dict[str, object] = {
        "schema_version": OUTCOME_MATERIALIZATION_CONTRACT_SCHEMA,
        "writer_branch": "registered_tip_reconciliation",
        "authority_record_type": "outcome_receipt_chain",
        "selection_kind": selection_kind,
        "old_live_epoch_id": frozen.projection.epoch_id,
        "target_date": target.isoformat(),
        "outcome_source_id": input_payload["outcome_source_id"],
        "source_revision_id": revision,
        "source_snapshot_sequence_id": snapshot_payload["snapshot_sequence_id"],
        "source_snapshot_receipt_sha256": snapshot.sha256,
        "live_issue_seal_entry_sha256": (
            seal.entry_sha256 if seal is not None else ZERO_HASH
        ),
        "source_exported_at_utc": input_payload["source_exported_at_utc"],
        "previous_revision_id": (
            previous.payload.get("source_revision_id") if previous is not None else None
        ),
        "previous_outcome_sha256": (
            previous.exact_object.sha256 if previous is not None else None
        ),
        "revision_sequence_id": tip.revision_sequence_id,
        "previous_receipt": (
            _materializer_artifact_payload(previous.receipt)
            if previous is not None
            else None
        ),
        "input_manifest_payload": input_payload,
        "input_manifest": _materializer_artifact_payload(input_manifest),
        "outcome_payload": tip.payload,
        "exact_outcome_object": _materializer_artifact_payload(tip.exact_object),
        "receipt_payload": receipt_payload,
        "receipt": _materializer_artifact_payload(tip.receipt),
        "item_artifacts_sha256": (_outcome_materialization_item_artifacts_sha256(item)),
    }
    _outcome_materialization_contract_record(item, reservation, contract)
    return OutcomeMaterializationPlan(profile, frozen, None, contract)


def _outcome_materialization_plan(
    item: Mapping[str, Any], reservation: Reservation
) -> OutcomeMaterializationPlan:
    authority = item.get("authority")
    record_type = (
        authority.get("record_type") if isinstance(authority, Mapping) else None
    )
    if record_type == "machine_selected_source_outcome":
        return _machine_selected_outcome_materialization_plan(item, reservation)
    if record_type == "outcome_receipt_chain":
        return _registered_tip_outcome_materialization_plan(item, reservation)
    raise WorksetRecoveryIntegrityError(
        "Outcome materialization item has no reviewed authority branch"
    )


def _outcome_materialization_contract(
    item: Mapping[str, Any], reservation: Reservation
) -> dict[str, object]:
    return dict(_outcome_materialization_plan(item, reservation).contract)


def _registered_outcome_predecessor(
    chain: Sequence[outcomes._RegisteredOutcome],
    *,
    receipt: outcomes.Artifact,
    exact: outcomes.Artifact,
) -> tuple[str | None, str | None]:
    """Return the direct immutable predecessor of one exact registered outcome."""

    positions = [
        index
        for index, candidate in enumerate(chain)
        if candidate.receipt == receipt and candidate.exact_object == exact
    ]
    if len(positions) != 1:
        raise WorksetRecoveryIntegrityError(
            "Outcome consumption receipt is not unique in its immutable chain"
        )
    position = positions[0]
    if position == 0:
        return None, None
    previous = chain[position - 1]
    revision = previous.payload.get("source_revision_id")
    if not isinstance(revision, str) or not revision or revision != revision.strip():
        raise WorksetRecoveryIntegrityError(
            "Outcome consumption predecessor revision changed"
        )
    return revision, previous.exact_object.sha256


def _validate_outcome_predecessor_scope(
    *,
    selection_kind: str,
    previous_revision_id: str | None,
    previous_outcome_sha256: str | None,
) -> None:
    if selection_kind == "revision":
        if (
            not isinstance(previous_revision_id, str)
            or not previous_revision_id
            or previous_revision_id != previous_revision_id.strip()
        ):
            raise WorksetRecoveryIntegrityError(
                "Outcome revision consumption lost its predecessor revision"
            )
        _hash_text(
            previous_outcome_sha256,
            name="outcome revision consumption predecessor",
        )
    elif previous_revision_id is not None or previous_outcome_sha256 is not None:
        raise WorksetRecoveryIntegrityError(
            "First outcome consumption unexpectedly gained a predecessor"
        )


def _materialized_outcome_consumption_context(
    item: Mapping[str, Any],
    reservation: Reservation,
    previous_step_receipt: Mapping[str, Any],
) -> OutcomeConsumptionContext:
    """Resolve a newly published outcome solely from its prior immutable step."""

    semantics = _exact(
        previous_step_receipt.get("action_semantics"),
        _OUTCOME_MATERIALIZATION_OUTPUT_KEYS,
        name="previous outcome materialization semantics",
    )
    if (
        previous_step_receipt.get("key_id") != item.get("key_id")
        or previous_step_receipt.get("natural_key") != item.get("natural_key")
        or previous_step_receipt.get("namespace_digest") != item.get("namespace_digest")
        or previous_step_receipt.get("action") != "outcome_materialized"
        or previous_step_receipt.get("action_output_kind")
        != "outcome_materializer_publication"
        or previous_step_receipt.get("next_actions") != ["outcome_or_revision_consumed"]
        or previous_step_receipt.get("terminal_for_key") is not False
        or semantics["selection_kind"] not in {"revision", "outstanding", "backfill"}
        or semantics["immutable_receipt_verified"] is not True
        or semantics["fully_published_verified"] is not True
        or semantics["canonical_materializer_writer_reused"] is not True
        or semantics["current_pointer_required_for_historical_replay"] is not False
        or semantics["live_ledger_event_count"] != 0
        or semantics["live_ledger_mutation_performed"] is not False
        or semantics["network_action_performed"] is not False
    ):
        raise WorksetRecoveryIntegrityError(
            "Previous outcome materialization receipt changed semantics"
        )
    try:
        target = date.fromisoformat(semantics["target_date"])
    except (TypeError, ValueError) as exc:
        raise WorksetRecoveryIntegrityError(
            "Previous outcome materialization target changed"
        ) from exc
    revision = semantics["source_revision_id"]
    source_id = semantics["outcome_source_id"]
    snapshot_sequence = semantics["source_snapshot_sequence_id"]
    if (
        target.isoformat() != semantics["target_date"]
        or not isinstance(revision, str)
        or not revision
        or revision != revision.strip()
        or not isinstance(source_id, str)
        or not source_id
        or source_id != source_id.strip()
        or not isinstance(snapshot_sequence, int)
        or isinstance(snapshot_sequence, bool)
        or snapshot_sequence <= 0
    ):
        raise WorksetRecoveryIntegrityError(
            "Previous outcome materialization identity changed"
        )
    for name in (
        "source_snapshot_receipt_sha256",
        "input_manifest_sha256",
        "exact_outcome_sha256",
        "receipt_sha256",
    ):
        _hash_text(semantics[name], name=f"previous materialization {name}")

    exact = _active_artifact_from_reference(
        previous_step_receipt.get("action_output"),
        reservation,
        name="previous materialized exact outcome",
    )
    receipt = _active_artifact_from_reference(
        semantics["materializer_receipt"],
        reservation,
        name="previous materializer receipt",
    )
    source_manifest = _active_artifact_from_reference(
        semantics["input_manifest"],
        reservation,
        name="previous materializer input manifest",
    )
    profile = outcomes.load_config()
    object_root = outcomes._runtime_path(  # noqa: SLF001
        profile, reservation.paths.active_root, "objects"
    )
    receipt_root = outcomes._runtime_path(  # noqa: SLF001
        profile, reservation.paths.active_root, "outcome_receipts"
    )
    if (
        exact.sha256 != semantics["exact_outcome_sha256"]
        or receipt.sha256 != semantics["receipt_sha256"]
        or source_manifest.sha256 != semantics["input_manifest_sha256"]
        or exact.path
        != outcomes._object_path(  # noqa: SLF001
            object_root, exact.sha256, suffix="outcome.json"
        )
        or source_manifest.path
        != outcomes._object_path(  # noqa: SLF001
            object_root, source_manifest.sha256, suffix="outcome-input.json"
        )
        or receipt.path != outcomes._receipt_path(receipt_root, target, revision)  # noqa: SLF001
    ):
        raise WorksetRecoveryIntegrityError(
            "Previous outcome materialization artifact identity changed"
        )

    frozen = _frozen_live_prefix(reservation)
    authority = item.get("authority")
    if not isinstance(authority, Mapping):
        raise WorksetRecoveryIntegrityError(
            "Materialized outcome item lost its original authority"
        )
    authority_type = authority.get("record_type")
    ledger_consumed_at_freeze = False
    if authority_type == "machine_selected_source_outcome":
        selected = _exact(
            authority,
            _OUTCOME_SELECTED_AUTHORITY_KEYS,
            name="materialized selected-source authority",
        )
        if (
            semantics["writer_branch"] != "machine_selected_source"
            or selected["action"] != "outcome_materialized"
            or selected["selection_kind"] != semantics["selection_kind"]
            or selected["target_date"] != target.isoformat()
            or selected["outcome_source_id"] != source_id
            or selected["source_revision_id"] != revision
            or selected["source_snapshot_sequence_id"] != snapshot_sequence
            or selected["source_snapshot_receipt_sha256"]
            != semantics["source_snapshot_receipt_sha256"]
            or selected["terminal"] is not False
        ):
            raise WorksetRecoveryIntegrityError(
                "Materialized selected-source authority changed"
            )
        if selected["selection_kind"] == "revision":
            if (
                not isinstance(selected["previous_revision_id"], str)
                or not selected["previous_revision_id"]
            ):
                raise WorksetRecoveryIntegrityError(
                    "Materialized selected-source predecessor changed"
                )
            _hash_text(
                selected["previous_outcome_sha256"],
                name="materialized selected-source previous outcome",
            )
        elif (
            selected["previous_revision_id"] is not None
            or selected["previous_outcome_sha256"] is not None
        ):
            raise WorksetRecoveryIntegrityError(
                "Materialized first source selection gained a predecessor"
            )
        _hash_text(
            selected["live_issue_seal_entry_sha256"],
            name="materialized selected-source seal",
        )
    elif authority_type == "outcome_receipt_chain":
        registered_authority = _exact(
            authority,
            _OUTCOME_RECEIPT_AUTHORITY_KEYS,
            name="materialized receipt-chain authority",
        )
        if (
            semantics["writer_branch"] != "registered_tip_reconciliation"
            or registered_authority["action"] != "outcome_materialized"
            or registered_authority["target_date"] != target.isoformat()
            or registered_authority["tip_source_revision_id"] != revision
            or registered_authority["tip_receipt_sha256"] != receipt.sha256
            or registered_authority["tip_exact_outcome_sha256"] != exact.sha256
            or registered_authority["tip_published"] is not False
            or not isinstance(registered_authority["tip_ledger_consumed"], bool)
            or registered_authority["terminal"] is not False
        ):
            raise WorksetRecoveryIntegrityError(
                "Materialized receipt-chain authority changed"
            )
        ledger_consumed_at_freeze = registered_authority["tip_ledger_consumed"]
    else:
        raise WorksetRecoveryIntegrityError(
            "Materialized outcome authority branch changed"
        )
    if authority.get("old_live_epoch_id") != frozen.projection.epoch_id:
        raise WorksetRecoveryIntegrityError(
            "Materialized outcome frozen live epoch changed"
        )

    try:
        registered = outcomes._load_registered_outcome(  # noqa: SLF001
            receipt_path=receipt.path,
            active_path=outcomes._outcome_inbox_path(  # noqa: SLF001
                profile, reservation.paths.active_root, target
            ),
            object_root=object_root,
            receipt_root=receipt_root,
            profile=profile,
            root=reservation.paths.active_root,
            live_module=live,
            live_profile=frozen.profile,
            prerequisites=frozen.prerequisites,
        )
        materialized_manifest = outcomes._artifact_from_mapping(  # noqa: SLF001
            registered.payload["source_manifest"],
            name="previous materialized source manifest",
        )
        input_manifest = outcomes._validate_input_manifest(  # noqa: SLF001
            materialized_manifest,
            profile=profile,
            root=reservation.paths.active_root,
        )
        outcome = live.load_outcome_batch(
            exact.path, frozen.profile, frozen.prerequisites
        )
        source = input_manifest.get("source")
        if not isinstance(source, Mapping):
            raise WorksetRecoveryIntegrityError(
                "Previous materialized input source changed"
            )
        snapshot = outcomes._artifact_from_mapping(  # noqa: SLF001
            source.get("snapshot_receipt"),
            name="previous materialized source snapshot",
        )
        snapshot_payload, _ = outcomes._read_json(  # noqa: SLF001
            snapshot.path,
            name="previous materialized source snapshot",
        )
    except WorksetRecoveryError:
        raise
    except (
        outcomes.OutcomeMaterializerError,
        live.LiveConfigError,
        live.LivePrerequisiteError,
        live.LiveInputError,
        live.LiveIntegrityError,
    ) as exc:
        raise WorksetRecoveryIntegrityError(
            f"Previous materialized outcome replay failed:{type(exc).__name__}:{exc}"
        ) from exc
    if (
        registered.receipt != receipt
        or registered.exact_object != exact
        or materialized_manifest != source_manifest
        or registered.payload.get("target_date") != target.isoformat()
        or registered.payload.get("source_revision_id") != revision
        or input_manifest.get("selection_kind") != semantics["selection_kind"]
        or input_manifest.get("target_date") != target.isoformat()
        or input_manifest.get("outcome_source_id") != source_id
        or input_manifest.get("source_record", {}).get("revision_id") != revision
        or outcome.sha256 != exact.sha256
        or outcome.target_date != target
        or outcome.source_revision_id != revision
        or outcome.outcome_source_id != source_id
        or outcome.source_manifest.sha256 != source_manifest.sha256
        or snapshot.sha256 != semantics["source_snapshot_receipt_sha256"]
        or snapshot_payload.get("schema_version") != "ootang_source_snapshot_receipt_v1"
        or snapshot_payload.get("snapshot_sequence_id") != snapshot_sequence
        or snapshot_payload.get("outcome_source_id") != source_id
    ):
        raise WorksetRecoveryIntegrityError(
            "Previous materialized outcome immutable binding changed"
        )
    immutable_chain = _immutable_materializer_receipt_chain(
        target=target,
        profile=profile,
        reservation=reservation,
        frozen=frozen,
    )
    previous_revision_id, previous_outcome_sha256 = _registered_outcome_predecessor(
        immutable_chain,
        receipt=receipt,
        exact=exact,
    )
    _validate_outcome_predecessor_scope(
        selection_kind=semantics["selection_kind"],
        previous_revision_id=previous_revision_id,
        previous_outcome_sha256=previous_outcome_sha256,
    )
    if authority_type == "machine_selected_source_outcome" and (
        authority.get("previous_revision_id") != previous_revision_id
        or authority.get("previous_outcome_sha256") != previous_outcome_sha256
    ):
        raise WorksetRecoveryIntegrityError(
            "Materialized selected outcome predecessor binding changed"
        )
    frozen_outcome_sha256 = frozen.projection.revision_ids.get(
        target.isoformat(), {}
    ).get(revision)
    if ledger_consumed_at_freeze:
        if frozen_outcome_sha256 != exact.sha256:
            raise WorksetRecoveryIntegrityError(
                "Materialized outcome consumed-at-freeze authority changed"
            )
    elif frozen_outcome_sha256 is not None:
        raise WorksetRecoveryIntegrityError(
            "Materialized outcome was already consumed at manifest freeze"
        )
    return OutcomeConsumptionContext(
        frozen=frozen,
        outcome=outcome,
        selection_kind=semantics["selection_kind"],
        tip_receipt=receipt,
        source_manifest=source_manifest,
        previous_revision_id=previous_revision_id,
        previous_outcome_sha256=previous_outcome_sha256,
        ledger_consumed_at_freeze=ledger_consumed_at_freeze,
    )


def _outcome_consumption_context(
    item: Mapping[str, Any],
    reservation: Reservation,
    previous_step_receipt: Mapping[str, Any] | None = None,
) -> OutcomeConsumptionContext:
    """Resolve one frozen, already-published receipt tip without mutable selection."""

    if previous_step_receipt is not None:
        return _materialized_outcome_consumption_context(
            item, reservation, previous_step_receipt
        )

    if (
        item.get("family") != "outcome_revision"
        or item.get("canonical_successor_state") != "outcome_or_revision_consumed"
    ):
        raise WorksetRecoveryIntegrityError("Outcome consumption item scope changed")
    authority = _exact(
        item.get("authority"),
        _OUTCOME_RECEIPT_AUTHORITY_KEYS,
        name="outcome receipt-chain authority",
    )
    if (
        authority["record_type"] != "outcome_receipt_chain"
        or authority["action"] != "outcome_or_revision_consumed"
        or authority["tip_published"] is not True
        or authority["tip_ledger_consumed"] is not False
        or authority["terminal"] is not False
    ):
        raise WorksetRecoveryIntegrityError(
            "Outcome receipt-chain authority changed state"
        )
    try:
        target = date.fromisoformat(str(authority["target_date"]))
    except ValueError as exc:
        raise WorksetRecoveryIntegrityError(
            "Outcome receipt-chain target date changed"
        ) from exc
    if target.isoformat() != authority["target_date"]:
        raise WorksetRecoveryIntegrityError(
            "Outcome receipt-chain target date is not canonical"
        )
    revision = authority["tip_source_revision_id"]
    if not isinstance(revision, str) or not revision or revision != revision.strip():
        raise WorksetRecoveryIntegrityError(
            "Outcome receipt-chain tip revision changed"
        )
    _hash_text(authority["tip_receipt_sha256"], name="outcome tip receipt")
    _hash_text(authority["tip_exact_outcome_sha256"], name="outcome tip object")

    frozen = _frozen_live_prefix(reservation)
    if authority["old_live_epoch_id"] != frozen.projection.epoch_id:
        raise WorksetRecoveryIntegrityError("Outcome receipt-chain epoch changed")
    try:
        materializer_profile = outcomes.load_config()
        chain = outcomes._scan_receipt_chain(  # noqa: SLF001
            target=target,
            profile=materializer_profile,
            root=reservation.paths.active_root,
            live_module=live,
            live_profile=frozen.profile,
            prerequisites=frozen.prerequisites,
        )
        if chain is None:
            raise WorksetRecoveryIntegrityError(
                "Frozen outcome receipt chain disappeared"
            )
        active_path = outcomes._outcome_inbox_path(  # noqa: SLF001
            materializer_profile, reservation.paths.active_root, target
        )
        active_raw = outcomes._legal_active_bytes(chain, active_path)  # noqa: SLF001
        if (
            chain.pointed is None
            or chain.pointed.receipt.sha256 != chain.tip.receipt.sha256
            or active_raw != chain.tip.raw
        ):
            raise WorksetRecoveryIntegrityError(
                "Frozen outcome receipt tip is no longer fully published"
            )
        source_manifest = outcomes._artifact_from_mapping(  # noqa: SLF001
            chain.tip.payload["source_manifest"], name="outcome tip source manifest"
        )
        input_manifest = outcomes._validate_input_manifest(  # noqa: SLF001
            source_manifest,
            profile=materializer_profile,
            root=reservation.paths.active_root,
        )
        outcome = live.load_outcome_batch(
            chain.tip.exact_object.path, frozen.profile, frozen.prerequisites
        )
    except WorksetRecoveryError:
        raise
    except (
        outcomes.OutcomeMaterializerError,
        live.LiveConfigError,
        live.LivePrerequisiteError,
        live.LiveInputError,
        live.LiveIntegrityError,
    ) as exc:
        raise WorksetRecoveryIntegrityError(
            f"Frozen outcome receipt replay failed:{type(exc).__name__}:{exc}"
        ) from exc

    known_at_freeze = frozen.projection.revision_ids.get(target.isoformat(), {})
    receipt_records: list[dict[str, object]] = []
    expected_artifacts: set[tuple[str, str, str, str, int]] = set()
    for registered in chain.receipts:
        registered_manifest = outcomes._artifact_from_mapping(  # noqa: SLF001
            registered.payload["source_manifest"],
            name="registered outcome source manifest",
        )
        registered_revision = registered.payload.get("source_revision_id")
        if not isinstance(registered_revision, str) or not registered_revision:
            raise WorksetRecoveryIntegrityError("Registered outcome revision changed")
        receipt_records.append(
            {
                "source_revision_id": registered_revision,
                "revision_sequence_id": registered.revision_sequence_id,
                "receipt_sha256": registered.receipt.sha256,
                "exact_outcome_sha256": registered.exact_object.sha256,
                "source_manifest_sha256": registered_manifest.sha256,
                "ledger_consumed": registered_revision in known_at_freeze,
            }
        )
        expected_artifacts.add(
            _artifact_identity(
                role="outcome_receipt",
                path=registered.receipt.path,
                sha256=registered.receipt.sha256,
                size_bytes=registered.receipt.size_bytes,
                active_root=reservation.paths.active_root,
            )
        )
        expected_artifacts.add(
            _artifact_identity(
                role="exact_outcome_object",
                path=registered.exact_object.path,
                sha256=registered.exact_object.sha256,
                size_bytes=registered.exact_object.size_bytes,
                active_root=reservation.paths.active_root,
            )
        )
        expected_artifacts.add(
            _artifact_identity(
                role="outcome_source_manifest",
                path=registered_manifest.path,
                sha256=registered_manifest.sha256,
                size_bytes=registered_manifest.size_bytes,
                active_root=reservation.paths.active_root,
            )
        )
    try:
        pointer_snapshot = registry._read_regular(  # noqa: SLF001
            chain.active_pointer_path,
            name="active outcome receipt pointer",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
        active_snapshot = registry._read_regular(  # noqa: SLF001
            active_path,
            name="active outcome",
            maximum_bytes=64 * 1024 * 1024,
        )
    except registry.EpochRegistryError as exc:
        raise WorksetRecoveryIntegrityError(str(exc)) from exc
    expected_artifacts.add(
        _artifact_identity(
            role="active_outcome_receipt_pointer",
            path=pointer_snapshot.path,
            sha256=pointer_snapshot.sha256,
            size_bytes=pointer_snapshot.size_bytes,
            active_root=reservation.paths.active_root,
        )
    )
    expected_artifacts.add(
        _artifact_identity(
            role="active_outcome",
            path=active_snapshot.path,
            sha256=active_snapshot.sha256,
            size_bytes=active_snapshot.size_bytes,
            active_root=reservation.paths.active_root,
        )
    )

    raw_artifacts = item.get("artifacts")
    if not isinstance(raw_artifacts, list):
        raise WorksetRecoveryIntegrityError(
            "Outcome receipt-chain artifacts changed type"
        )
    actual_artifacts: set[tuple[str, str, str, str, int]] = set()
    for raw in raw_artifacts:
        artifact = _exact(raw, _MANIFEST_ARTIFACT_KEYS, name="outcome artifact")
        if (
            not isinstance(artifact["role"], str)
            or artifact["root"] != "active"
            or not isinstance(artifact["path"], str)
            or not isinstance(artifact["size_bytes"], int)
            or isinstance(artifact["size_bytes"], bool)
            or artifact["size_bytes"] < 0
        ):
            raise WorksetRecoveryIntegrityError(
                "Outcome receipt-chain artifact identity changed"
            )
        _hash_text(artifact["sha256"], name="outcome artifact")
        actual_artifacts.add(
            (
                artifact["role"],
                artifact["root"],
                artifact["path"],
                artifact["sha256"],
                artifact["size_bytes"],
            )
        )
    raw_receipts = authority["receipts"]
    if not isinstance(raw_receipts, list):
        raise WorksetRecoveryIntegrityError(
            "Outcome receipt-chain records changed type"
        )
    for raw in raw_receipts:
        _exact(raw, _OUTCOME_RECEIPT_RECORD_KEYS, name="outcome receipt record")
    if actual_artifacts != expected_artifacts or raw_receipts != receipt_records:
        raise WorksetRecoveryIntegrityError(
            "Outcome receipt-chain frozen manifest binding changed"
        )
    if (
        chain.tip.payload.get("source_revision_id") != revision
        or chain.tip.receipt.sha256 != authority["tip_receipt_sha256"]
        or chain.tip.exact_object.sha256 != authority["tip_exact_outcome_sha256"]
        or outcome.target_date != target
        or outcome.source_revision_id != revision
        or outcome.sha256 != authority["tip_exact_outcome_sha256"]
        or outcome.source_manifest.sha256 != source_manifest.sha256
        or input_manifest.get("target_date") != target.isoformat()
        or input_manifest.get("outcome_source_id") != outcome.outcome_source_id
        or input_manifest.get("source_record", {}).get("revision_id") != revision
        or revision in known_at_freeze
    ):
        raise WorksetRecoveryIntegrityError(
            "Outcome receipt-chain tip identity changed"
        )
    selection_kind = input_manifest.get("selection_kind")
    if not isinstance(selection_kind, str):
        raise WorksetRecoveryIntegrityError(
            "Outcome receipt-chain selection kind changed"
        )
    previous_revision_id, previous_outcome_sha256 = _registered_outcome_predecessor(
        chain.receipts,
        receipt=chain.tip.receipt,
        exact=chain.tip.exact_object,
    )
    _validate_outcome_predecessor_scope(
        selection_kind=selection_kind,
        previous_revision_id=previous_revision_id,
        previous_outcome_sha256=previous_outcome_sha256,
    )
    return OutcomeConsumptionContext(
        frozen=frozen,
        outcome=outcome,
        selection_kind=selection_kind,
        tip_receipt=chain.tip.receipt,
        source_manifest=source_manifest,
        previous_revision_id=previous_revision_id,
        previous_outcome_sha256=previous_outcome_sha256,
        ledger_consumed_at_freeze=False,
    )


def _outcome_event_matches_spec(
    event: live_ledger.LedgerEvent, spec: live_ledger.EventSpec
) -> bool:
    stored = {
        "event_key": event.event_key,
        "event_type": event.event_type,
        "target_date": event.target_date,
        "station": event.station,
        "issue_id": event.issue_id,
        "protocol_config_sha256": event.protocol_config_sha256,
        "code_sha256": event.code_sha256,
        "environment_sha256": event.environment_sha256,
        "input_manifest_sha256": event.input_manifest_sha256,
        "model_manifest_sha256": event.model_manifest_sha256,
        "state_before_sha256": event.state_before_sha256,
        "state_after_sha256": event.state_after_sha256,
        "payload": event.payload,
    }
    return _canonical_bytes(stored) == _canonical_bytes(_event_spec_payload(spec))


def _pre_head_payload(
    expected: live_cas.LiveLedgerPreHeadV1,
) -> dict[str, object]:
    return {
        "epoch_id": expected.epoch_id,
        "event_count": expected.event_count,
        "sequence_id": expected.sequence_id,
        "entry_sha256": expected.entry_sha256,
    }


def _outstanding_outcome_consumption_plan(
    item: Mapping[str, Any],
    reservation: Reservation,
    previous_step_receipt: Mapping[str, Any] | None = None,
    *,
    context: OutcomeConsumptionContext | None = None,
) -> OutcomeConsumptionPlan:
    context = context or _outcome_consumption_context(
        item, reservation, previous_step_receipt
    )
    frozen = context.frozen
    outcome = context.outcome
    if context.selection_kind != "outstanding":
        raise WorksetRecoveryExternalWait(
            "the published outcome tip is not the reviewed outstanding-settlement branch"
        )
    _validate_outcome_predecessor_scope(
        selection_kind=context.selection_kind,
        previous_revision_id=context.previous_revision_id,
        previous_outcome_sha256=context.previous_outcome_sha256,
    )
    target_text = outcome.target_date.isoformat()
    prefix = f"{frozen.projection.epoch_id}:{target_text}"
    first_key = f"{prefix}:outcome_batch_opened:{outcome.source_revision_id}"
    terminal_key = f"{prefix}:outcome_batch_settled:{outcome.source_revision_id}"
    try:
        current_projection = live._reconstruct_projection(  # noqa: SLF001
            frozen.current_events, frozen.profile, frozen.prerequisites
        )
    except (live.LiveIntegrityError, live.LiveInputError) as exc:
        raise WorksetRecoveryIntegrityError(
            "Outcome consumption current ledger no longer replays"
        ) from exc
    known = current_projection.revision_ids.get(target_text, {})
    known_hash = known.get(outcome.source_revision_id)
    if known_hash is not None and known_hash != outcome.sha256:
        raise WorksetRecoveryIntegrityError(
            "Outcome revision id was already consumed with different bytes"
        )

    stored_events: tuple[live_ledger.LedgerEvent, ...] = ()
    if known_hash is not None:
        first_positions = [
            index
            for index, event in enumerate(frozen.current_events)
            if event.event_key == first_key
        ]
        terminal_positions = [
            index
            for index, event in enumerate(frozen.current_events)
            if event.event_key == terminal_key
        ]
        if len(first_positions) != 1 or len(terminal_positions) != 1:
            raise WorksetRecoveryIntegrityError(
                "Consumed outcome lost its canonical settlement transaction"
            )
        first_index = first_positions[0]
        terminal_index = terminal_positions[0]
        if terminal_index - first_index + 1 != OUTCOME_CONSUMPTION_EVENT_COUNT:
            raise WorksetRecoveryIntegrityError(
                "Consumed outcome settlement transaction changed length"
            )
        stored_events = tuple(frozen.current_events[first_index : terminal_index + 1])
        prefix_events = frozen.current_events[:first_index]
    else:
        prefix_events = frozen.current_events
    if not prefix_events or (
        len(prefix_events) < frozen.expected_pre_head.event_count
        and not context.ledger_consumed_at_freeze
    ):
        raise WorksetRecoveryIntegrityError(
            "Outcome consumption pre-head precedes the frozen reservation"
        )
    pre_head_event = prefix_events[-1]
    expected = live_cas.LiveLedgerPreHeadV1(
        epoch_id=frozen.projection.epoch_id,
        event_count=len(prefix_events),
        sequence_id=pre_head_event.sequence_id,
        entry_sha256=pre_head_event.entry_sha256,
    )
    try:
        live_cas._validate_pre_head(expected)  # noqa: SLF001
        projection = live._reconstruct_projection(  # noqa: SLF001
            prefix_events, frozen.profile, frozen.prerequisites
        )
    except live_ledger.LedgerError as exc:
        raise WorksetRecoveryIntegrityError(
            "Outcome consumption pre-head is invalid"
        ) from exc
    except (live.LiveIntegrityError, live.LiveInputError) as exc:
        raise WorksetRecoveryIntegrityError(
            "Outcome consumption pre-head no longer replays"
        ) from exc
    seal = projection.seal_event
    if (
        projection.outstanding_target_date != outcome.target_date
        or projection.outstanding_issue_id is None
        or seal is None
        or seal.target_date != target_text
        or seal.entry_sha256 not in projection.anchored_seal_hashes
        or outcome.source_revision_id in projection.revision_ids.get(target_text, {})
    ):
        raise WorksetRecoveryIntegrityError(
            "Published outcome does not match one confirmed outstanding lifecycle"
        )

    collector = _EventSpecCollector(prefix_events)
    try:
        live._append_outcome_batch(  # noqa: SLF001
            collector,
            frozen.profile,
            frozen.prerequisites,
            projection,
            outcome,
        )
    except (live.LiveIntegrityError, live.LiveInputError) as exc:
        raise WorksetRecoveryIntegrityError(
            f"Canonical outcome writer rejected the frozen tip:{exc}"
        ) from exc
    specs = collector.specs
    if (
        specs is None
        or len(specs) != OUTCOME_CONSUMPTION_EVENT_COUNT
        or tuple(spec.event_type for spec in specs) != OUTCOME_CONSUMPTION_EVENT_TYPES
        or specs[0].event_key != first_key
        or specs[-1].event_key != terminal_key
        or len({spec.event_key for spec in specs}) != len(specs)
    ):
        raise WorksetRecoveryIntegrityError(
            "Canonical outstanding outcome transaction shape changed"
        )
    if stored_events:
        if (
            tuple(event.event_type for event in stored_events)
            != OUTCOME_CONSUMPTION_EVENT_TYPES
            or any(
                not _outcome_event_matches_spec(event, spec)
                for event, spec in zip(stored_events, specs, strict=True)
            )
            or [event.sequence_id for event in stored_events]
            != list(
                range(
                    expected.sequence_id + 1,
                    expected.sequence_id + OUTCOME_CONSUMPTION_EVENT_COUNT + 1,
                )
            )
            or stored_events[0].previous_entry_sha256 != expected.entry_sha256
        ):
            raise WorksetRecoveryIntegrityError(
                "Stored outcome settlement is not the exact canonical retry"
            )
    else:
        current_keys = {event.event_key for event in frozen.current_events}
        if any(spec.event_key in current_keys for spec in specs):
            raise WorksetRecoveryIntegrityError(
                "Fresh outcome consumption has a partial canonical transaction"
            )
    spec_payloads = [_event_spec_payload(spec) for spec in specs]
    contract: dict[str, object] = {
        "schema_version": OUTCOME_CONSUMPTION_CONTRACT_SCHEMA,
        "writer_branch": (
            "preexisting_consumed_adoption"
            if context.ledger_consumed_at_freeze
            else "outstanding_settlement"
        ),
        "expected_pre_head": _pre_head_payload(expected),
        "live_epoch_id": frozen.projection.epoch_id,
        "target_date": target_text,
        "issue_id": projection.outstanding_issue_id,
        "sealed_entry_sha256": seal.entry_sha256,
        "tip_receipt_sha256": context.tip_receipt.sha256,
        "exact_outcome_sha256": outcome.sha256,
        "outcome_source_manifest_sha256": context.source_manifest.sha256,
        "outcome_source_id": outcome.outcome_source_id,
        "source_revision_id": outcome.source_revision_id,
        "ledger_consumed_at_freeze": context.ledger_consumed_at_freeze,
        "event_count": OUTCOME_CONSUMPTION_EVENT_COUNT,
        "first_event_key": first_key,
        "terminal_event_key": terminal_key,
        "ordered_event_keys_sha256": _sha256(
            _canonical_bytes([spec.event_key for spec in specs])
        ),
        "event_specs_sha256": _sha256(_canonical_bytes(spec_payloads)),
    }
    return OutcomeConsumptionPlan(
        context=context,
        expected_pre_head=expected,
        specs=specs,
        stored_events=stored_events,
        contract=contract,
    )


def _settled_revision_projection_authority(
    context: OutcomeConsumptionContext,
    projection: live.LiveProjection,
) -> tuple[live_ledger.LedgerEvent, str, str]:
    """Validate the exact settled predecessor used by the canonical revision writer."""

    outcome = context.outcome
    target_text = outcome.target_date.isoformat()
    if target_text in projection.backfill_events:
        raise WorksetRecoveryExternalWait(
            "the published revision belongs to the unimplemented backfill branch"
        )
    settlement = projection.settled_events.get(target_text)
    if settlement is None:
        raise WorksetRecoveryIntegrityError(
            "Settled revision consumption lost its original settlement"
        )
    _validate_outcome_predecessor_scope(
        selection_kind=context.selection_kind,
        previous_revision_id=context.previous_revision_id,
        previous_outcome_sha256=context.previous_outcome_sha256,
    )
    known = projection.revision_ids.get(target_text)
    if not isinstance(known, Mapping) or not known:
        raise WorksetRecoveryIntegrityError(
            "Settled revision consumption lost its predecessor registry"
        )
    if outcome.source_revision_id in known:
        raise WorksetRecoveryIntegrityError(
            "Settled revision was already present before its canonical transaction"
        )
    latest_revision = next(reversed(tuple(known)))
    if (
        latest_revision != context.previous_revision_id
        or known[latest_revision] != context.previous_outcome_sha256
    ):
        raise WorksetRecoveryIntegrityError(
            "Settled revision predecessor disagrees with the live ledger tip"
        )
    issue_id = settlement.issue_id
    sealed_entry_sha256 = settlement.payload.get("issue_batch_sealed_entry_sha256")
    if (
        not isinstance(issue_id, str)
        or not issue_id
        or _hash_text(
            sealed_entry_sha256,
            name="settled revision original issue seal",
        )
        != sealed_entry_sha256
    ):
        raise WorksetRecoveryIntegrityError(
            "Settled revision original issue authority changed"
        )
    return settlement, issue_id, sealed_entry_sha256


def _capture_settled_revision_specs(
    context: OutcomeConsumptionContext,
    prefix_events: Sequence[live_ledger.LedgerEvent],
) -> tuple[
    live.LiveProjection,
    tuple[live_ledger.EventSpec, ...],
    live_ledger.LedgerEvent,
    str,
    str,
]:
    frozen = context.frozen
    try:
        projection = live._reconstruct_projection(  # noqa: SLF001
            prefix_events, frozen.profile, frozen.prerequisites
        )
    except (live.LiveIntegrityError, live.LiveInputError) as exc:
        raise WorksetRecoveryIntegrityError(
            "Settled revision consumption pre-head no longer replays"
        ) from exc
    settlement, issue_id, sealed_entry_sha256 = _settled_revision_projection_authority(
        context, projection
    )
    collector = _EventSpecCollector(prefix_events)
    try:
        live._append_revision(  # noqa: SLF001
            collector,
            frozen.profile,
            frozen.prerequisites,
            projection,
            context.outcome,
        )
    except (live.LiveIntegrityError, live.LiveInputError) as exc:
        raise WorksetRecoveryIntegrityError(
            f"Canonical settled revision writer rejected the frozen tip:{exc}"
        ) from exc
    specs = collector.specs
    if specs is None:
        raise WorksetRecoveryIntegrityError(
            "Canonical settled revision writer emitted no transaction"
        )
    return projection, specs, settlement, issue_id, sealed_entry_sha256


def _settled_revision_consumption_plan(
    item: Mapping[str, Any],
    reservation: Reservation,
    previous_step_receipt: Mapping[str, Any] | None = None,
    *,
    context: OutcomeConsumptionContext | None = None,
) -> OutcomeConsumptionPlan:
    context = context or _outcome_consumption_context(
        item, reservation, previous_step_receipt
    )
    if context.selection_kind != "revision":
        raise WorksetRecoveryExternalWait(
            "the published outcome tip is not the reviewed settled revision branch"
        )
    frozen = context.frozen
    outcome = context.outcome
    target_text = outcome.target_date.isoformat()
    stations = frozen.profile["stations"]
    prefix = (
        f"{frozen.projection.epoch_id}:{target_text}:revision:"
        f"{outcome.source_revision_id}"
    )
    first_key = f"{prefix}:outcome_revision:{stations[0]}"
    terminal_key = f"{prefix}:revision_rescore:{stations[-1]}"
    try:
        current_projection = live._reconstruct_projection(  # noqa: SLF001
            frozen.current_events, frozen.profile, frozen.prerequisites
        )
    except (live.LiveIntegrityError, live.LiveInputError) as exc:
        raise WorksetRecoveryIntegrityError(
            "Settled revision current ledger no longer replays"
        ) from exc
    if target_text in current_projection.backfill_events:
        raise WorksetRecoveryExternalWait(
            "the published revision belongs to the unimplemented backfill branch"
        )
    if target_text not in current_projection.settled_events:
        raise WorksetRecoveryIntegrityError(
            "Settled revision current ledger lost its original settlement"
        )
    known = current_projection.revision_ids.get(target_text, {})
    known_hash = known.get(outcome.source_revision_id)
    if known_hash is not None and known_hash != outcome.sha256:
        raise WorksetRecoveryIntegrityError(
            "Settled revision id was already consumed with different bytes"
        )

    stored_events: tuple[live_ledger.LedgerEvent, ...] = ()
    if known_hash is not None:
        first_positions = [
            index
            for index, event in enumerate(frozen.current_events)
            if event.event_key == first_key
        ]
        terminal_positions = [
            index
            for index, event in enumerate(frozen.current_events)
            if event.event_key == terminal_key
        ]
        if len(first_positions) != 1 or len(terminal_positions) != 1:
            raise WorksetRecoveryIntegrityError(
                "Consumed settled revision lost its canonical transaction"
            )
        first_index = first_positions[0]
        terminal_index = terminal_positions[0]
        if terminal_index - first_index + 1 != OUTCOME_SETTLED_REVISION_EVENT_COUNT:
            raise WorksetRecoveryIntegrityError(
                "Consumed settled revision transaction changed length"
            )
        stored_events = tuple(frozen.current_events[first_index : terminal_index + 1])
        prefix_events = frozen.current_events[:first_index]
    else:
        prefix_events = frozen.current_events
    if not prefix_events or (
        len(prefix_events) < frozen.expected_pre_head.event_count
        and not context.ledger_consumed_at_freeze
    ):
        raise WorksetRecoveryIntegrityError(
            "Settled revision pre-head precedes the frozen reservation"
        )
    pre_head_event = prefix_events[-1]
    expected = live_cas.LiveLedgerPreHeadV1(
        epoch_id=frozen.projection.epoch_id,
        event_count=len(prefix_events),
        sequence_id=pre_head_event.sequence_id,
        entry_sha256=pre_head_event.entry_sha256,
    )
    try:
        live_cas._validate_pre_head(expected)  # noqa: SLF001
    except live_ledger.LedgerError as exc:
        raise WorksetRecoveryIntegrityError(
            "Settled revision consumption pre-head is invalid"
        ) from exc
    projection, specs, settlement, issue_id, sealed_entry_sha256 = (
        _capture_settled_revision_specs(context, prefix_events)
    )
    if (
        len(specs) != OUTCOME_SETTLED_REVISION_EVENT_COUNT
        or tuple(spec.event_type for spec in specs)
        != OUTCOME_SETTLED_REVISION_EVENT_TYPES
        or specs[0].event_key != first_key
        or specs[-1].event_key != terminal_key
        or len({spec.event_key for spec in specs}) != len(specs)
    ):
        raise WorksetRecoveryIntegrityError(
            "Canonical settled revision transaction shape changed"
        )
    if stored_events:
        if (
            tuple(event.event_type for event in stored_events)
            != OUTCOME_SETTLED_REVISION_EVENT_TYPES
            or any(
                not _outcome_event_matches_spec(event, spec)
                for event, spec in zip(stored_events, specs, strict=True)
            )
            or [event.sequence_id for event in stored_events]
            != list(
                range(
                    expected.sequence_id + 1,
                    expected.sequence_id + OUTCOME_SETTLED_REVISION_EVENT_COUNT + 1,
                )
            )
            or stored_events[0].previous_entry_sha256 != expected.entry_sha256
        ):
            raise WorksetRecoveryIntegrityError(
                "Stored settled revision is not the exact canonical retry"
            )
    else:
        current_keys = {event.event_key for event in frozen.current_events}
        if any(spec.event_key in current_keys for spec in specs):
            raise WorksetRecoveryIntegrityError(
                "Fresh settled revision has a partial canonical transaction"
            )
    spec_payloads = [_event_spec_payload(spec) for spec in specs]
    contract: dict[str, object] = {
        "schema_version": OUTCOME_REVISION_CONSUMPTION_CONTRACT_SCHEMA,
        "writer_branch": (
            "preexisting_settled_revision_adoption"
            if context.ledger_consumed_at_freeze
            else "settled_revision"
        ),
        "expected_pre_head": _pre_head_payload(expected),
        "live_epoch_id": frozen.projection.epoch_id,
        "target_date": target_text,
        "original_settlement_entry_sha256": settlement.entry_sha256,
        "issue_id": issue_id,
        "sealed_entry_sha256": sealed_entry_sha256,
        "previous_revision_id": context.previous_revision_id,
        "previous_outcome_sha256": context.previous_outcome_sha256,
        "tip_receipt_sha256": context.tip_receipt.sha256,
        "exact_outcome_sha256": outcome.sha256,
        "outcome_source_manifest_sha256": context.source_manifest.sha256,
        "outcome_source_id": outcome.outcome_source_id,
        "source_revision_id": outcome.source_revision_id,
        "ledger_consumed_at_freeze": context.ledger_consumed_at_freeze,
        "online_states_sha256": live._states_sha256(  # noqa: SLF001
            projection.states, stations
        ),
        "last_finalized_date": projection.last_finalized_date.isoformat(),
        "outstanding_target_date": (
            projection.outstanding_target_date.isoformat()
            if projection.outstanding_target_date is not None
            else None
        ),
        "event_count": OUTCOME_SETTLED_REVISION_EVENT_COUNT,
        "first_event_key": first_key,
        "terminal_event_key": terminal_key,
        "ordered_event_keys_sha256": _sha256(
            _canonical_bytes([spec.event_key for spec in specs])
        ),
        "event_specs_sha256": _sha256(_canonical_bytes(spec_payloads)),
    }
    return OutcomeConsumptionPlan(
        context=context,
        expected_pre_head=expected,
        specs=specs,
        stored_events=stored_events,
        contract=contract,
    )


def _outcome_consumption_plan(
    item: Mapping[str, Any],
    reservation: Reservation,
    previous_step_receipt: Mapping[str, Any] | None = None,
) -> OutcomeConsumptionPlan:
    context = _outcome_consumption_context(item, reservation, previous_step_receipt)
    if context.selection_kind == "outstanding":
        return _outstanding_outcome_consumption_plan(
            item,
            reservation,
            previous_step_receipt,
            context=context,
        )
    if context.selection_kind == "revision":
        return _settled_revision_consumption_plan(
            item,
            reservation,
            previous_step_receipt,
            context=context,
        )
    raise WorksetRecoveryExternalWait(
        "the published outcome tip is not a reviewed consumption branch"
    )


def _outcome_consumption_contract(
    item: Mapping[str, Any],
    reservation: Reservation,
    previous_step_receipt: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    return dict(
        _outcome_consumption_plan(item, reservation, previous_step_receipt).contract
    )


def _immutable_outcome_artifact(
    item: Mapping[str, Any],
    reservation: Reservation,
    *,
    role: str,
    sha256: str,
) -> outcomes.Artifact:
    raw_artifacts = item.get("artifacts")
    if not isinstance(raw_artifacts, list):
        raise WorksetRecoveryIntegrityError(
            "Recorded outcome item lost its frozen artifacts"
        )
    candidates = [
        raw
        for raw in raw_artifacts
        if isinstance(raw, Mapping)
        and raw.get("role") == role
        and raw.get("sha256") == sha256
    ]
    if len(candidates) != 1:
        raise WorksetRecoveryIntegrityError(
            f"Recorded outcome item has no unique immutable {role}"
        )
    obligation = _exact(
        candidates[0], _MANIFEST_ARTIFACT_KEYS, name=f"recorded {role} artifact"
    )
    path = _artifact_path(obligation, reservation)
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            path, name=f"recorded {role}", maximum_bytes=64 * 1024 * 1024
        )
    except registry.EpochRegistryError as exc:
        raise WorksetRecoveryIntegrityError(str(exc)) from exc
    if snapshot.sha256 != sha256 or snapshot.size_bytes != obligation["size_bytes"]:
        raise WorksetRecoveryIntegrityError(
            f"Recorded immutable {role} binding changed"
        )
    return outcomes.Artifact(
        path=snapshot.path,
        sha256=snapshot.sha256,
        size_bytes=snapshot.size_bytes,
    )


def _recorded_outcome_consumption_context(
    item: Mapping[str, Any],
    reservation: Reservation,
    previous_step_receipt: Mapping[str, Any] | None = None,
) -> OutcomeConsumptionContext:
    """Replay immutable tip objects without consulting current active publication."""

    if previous_step_receipt is not None:
        return _materialized_outcome_consumption_context(
            item, reservation, previous_step_receipt
        )

    if (
        item.get("family") != "outcome_revision"
        or item.get("canonical_successor_state") != "outcome_or_revision_consumed"
    ):
        raise WorksetRecoveryIntegrityError(
            "Recorded outcome consumption item scope changed"
        )
    authority = _exact(
        item.get("authority"),
        _OUTCOME_RECEIPT_AUTHORITY_KEYS,
        name="recorded outcome receipt-chain authority",
    )
    if (
        authority["record_type"] != "outcome_receipt_chain"
        or authority["action"] != "outcome_or_revision_consumed"
        or authority["tip_published"] is not True
        or authority["tip_ledger_consumed"] is not False
        or authority["terminal"] is not False
    ):
        raise WorksetRecoveryIntegrityError(
            "Recorded outcome receipt-chain authority changed state"
        )
    try:
        target = date.fromisoformat(authority["target_date"])
    except (TypeError, ValueError) as exc:
        raise WorksetRecoveryIntegrityError(
            "Recorded outcome target date changed"
        ) from exc
    revision = authority["tip_source_revision_id"]
    if (
        target.isoformat() != authority["target_date"]
        or not isinstance(revision, str)
        or not revision
        or revision != revision.strip()
    ):
        raise WorksetRecoveryIntegrityError("Recorded outcome tip identity changed")
    receipt_sha256 = _hash_text(
        authority["tip_receipt_sha256"], name="recorded outcome tip receipt"
    )
    outcome_sha256 = _hash_text(
        authority["tip_exact_outcome_sha256"], name="recorded exact outcome"
    )
    frozen = _frozen_live_prefix(reservation)
    if authority[
        "old_live_epoch_id"
    ] != frozen.projection.epoch_id or revision in frozen.projection.revision_ids.get(
        target.isoformat(), {}
    ):
        raise WorksetRecoveryIntegrityError(
            "Recorded outcome frozen epoch/consumption state changed"
        )
    receipt = _immutable_outcome_artifact(
        item,
        reservation,
        role="outcome_receipt",
        sha256=receipt_sha256,
    )
    exact = _immutable_outcome_artifact(
        item,
        reservation,
        role="exact_outcome_object",
        sha256=outcome_sha256,
    )
    try:
        materializer_profile = outcomes.load_config()
        receipt_root = outcomes._runtime_path(  # noqa: SLF001
            materializer_profile,
            reservation.paths.active_root,
            "outcome_receipts",
        )
        registered = outcomes._load_registered_outcome(  # noqa: SLF001
            receipt_path=receipt.path,
            active_path=outcomes._outcome_inbox_path(  # noqa: SLF001
                materializer_profile, reservation.paths.active_root, target
            ),
            object_root=outcomes._runtime_path(  # noqa: SLF001
                materializer_profile, reservation.paths.active_root, "objects"
            ),
            receipt_root=receipt_root,
            profile=materializer_profile,
            root=reservation.paths.active_root,
            live_module=live,
            live_profile=frozen.profile,
            prerequisites=frozen.prerequisites,
        )
        source_manifest = outcomes._artifact_from_mapping(  # noqa: SLF001
            registered.payload["source_manifest"],
            name="recorded outcome source manifest",
        )
        input_manifest = outcomes._validate_input_manifest(  # noqa: SLF001
            source_manifest,
            profile=materializer_profile,
            root=reservation.paths.active_root,
        )
        outcome = live.load_outcome_batch(
            exact.path, frozen.profile, frozen.prerequisites
        )
    except (
        outcomes.OutcomeMaterializerError,
        live.LiveConfigError,
        live.LivePrerequisiteError,
        live.LiveInputError,
        live.LiveIntegrityError,
    ) as exc:
        raise WorksetRecoveryIntegrityError(
            f"Recorded outcome immutable replay failed:{type(exc).__name__}:{exc}"
        ) from exc
    bound_manifest = _immutable_outcome_artifact(
        item,
        reservation,
        role="outcome_source_manifest",
        sha256=source_manifest.sha256,
    )
    raw_receipts = authority["receipts"]
    if not isinstance(raw_receipts, list):
        raise WorksetRecoveryIntegrityError(
            "Recorded outcome receipt records changed type"
        )
    receipt_records = [
        _exact(raw, _OUTCOME_RECEIPT_RECORD_KEYS, name="recorded outcome receipt")
        for raw in raw_receipts
    ]
    tip_records = [
        raw
        for raw in receipt_records
        if raw.get("source_revision_id") == revision
        and raw.get("receipt_sha256") == receipt_sha256
    ]
    if (
        len(tip_records) != 1
        or tip_records[0]["exact_outcome_sha256"] != outcome_sha256
        or tip_records[0]["source_manifest_sha256"] != source_manifest.sha256
        or tip_records[0]["ledger_consumed"] is not False
        or registered.receipt != receipt
        or registered.exact_object != exact
        or registered.payload.get("source_revision_id") != revision
        or bound_manifest.path != source_manifest.path
        or bound_manifest.size_bytes != source_manifest.size_bytes
        or outcome.sha256 != outcome_sha256
        or outcome.target_date != target
        or outcome.source_revision_id != revision
        or outcome.source_manifest.sha256 != source_manifest.sha256
        or input_manifest.get("target_date") != target.isoformat()
        or input_manifest.get("source_record", {}).get("revision_id") != revision
    ):
        raise WorksetRecoveryIntegrityError(
            "Recorded outcome immutable tip binding changed"
        )
    selection_kind = input_manifest.get("selection_kind")
    if not isinstance(selection_kind, str):
        raise WorksetRecoveryIntegrityError("Recorded outcome selection kind changed")
    immutable_chain = _immutable_materializer_receipt_chain(
        target=target,
        profile=materializer_profile,
        reservation=reservation,
        frozen=frozen,
    )
    previous_revision_id, previous_outcome_sha256 = _registered_outcome_predecessor(
        immutable_chain,
        receipt=receipt,
        exact=exact,
    )
    _validate_outcome_predecessor_scope(
        selection_kind=selection_kind,
        previous_revision_id=previous_revision_id,
        previous_outcome_sha256=previous_outcome_sha256,
    )
    return OutcomeConsumptionContext(
        frozen=frozen,
        outcome=outcome,
        selection_kind=selection_kind,
        tip_receipt=receipt,
        source_manifest=source_manifest,
        previous_revision_id=previous_revision_id,
        previous_outcome_sha256=previous_outcome_sha256,
        ledger_consumed_at_freeze=False,
    )


def _recorded_outstanding_outcome_consumption_plan(
    item: Mapping[str, Any],
    reservation: Reservation,
    contract: Mapping[str, Any],
    previous_step_receipt: Mapping[str, Any] | None = None,
) -> OutcomeConsumptionPlan:
    context = _recorded_outcome_consumption_context(
        item, reservation, previous_step_receipt
    )
    checked = _exact(
        contract,
        {
            "schema_version",
            "writer_branch",
            "expected_pre_head",
            "live_epoch_id",
            "target_date",
            "issue_id",
            "sealed_entry_sha256",
            "tip_receipt_sha256",
            "exact_outcome_sha256",
            "outcome_source_manifest_sha256",
            "outcome_source_id",
            "source_revision_id",
            "ledger_consumed_at_freeze",
            "event_count",
            "first_event_key",
            "terminal_event_key",
            "ordered_event_keys_sha256",
            "event_specs_sha256",
        },
        name="recorded outcome consumption contract",
    )
    if (
        checked["schema_version"] != OUTCOME_CONSUMPTION_CONTRACT_SCHEMA
        or checked["writer_branch"]
        not in {"outstanding_settlement", "preexisting_consumed_adoption"}
        or context.selection_kind != "outstanding"
        or checked["ledger_consumed_at_freeze"] is not context.ledger_consumed_at_freeze
        or (checked["writer_branch"] == "preexisting_consumed_adoption")
        is not context.ledger_consumed_at_freeze
    ):
        raise WorksetRecoveryIntegrityError(
            "Recorded outcome consumption contract branch changed"
        )
    pre_head = _exact(
        checked["expected_pre_head"],
        {"epoch_id", "event_count", "sequence_id", "entry_sha256"},
        name="recorded outcome consumption pre-head",
    )
    try:
        expected = live_cas.LiveLedgerPreHeadV1(**pre_head)
        live_cas._validate_pre_head(expected)  # noqa: SLF001
    except (TypeError, live_ledger.LedgerError) as exc:
        raise WorksetRecoveryIntegrityError(
            "Recorded outcome consumption pre-head changed"
        ) from exc
    frozen = context.frozen
    if (
        expected.epoch_id != frozen.projection.epoch_id
        or (
            expected.event_count < frozen.expected_pre_head.event_count
            and not context.ledger_consumed_at_freeze
        )
        or len(frozen.current_events)
        < expected.event_count + OUTCOME_CONSUMPTION_EVENT_COUNT
    ):
        raise WorksetRecoveryIntegrityError(
            "Recorded outcome consumption ledger slice disappeared"
        )
    prefix_events = frozen.current_events[: expected.event_count]
    pre_head_event = prefix_events[-1]
    if (
        pre_head_event.sequence_id != expected.sequence_id
        or pre_head_event.entry_sha256 != expected.entry_sha256
    ):
        raise WorksetRecoveryIntegrityError(
            "Recorded outcome consumption pre-head moved"
        )
    try:
        projection = live._reconstruct_projection(  # noqa: SLF001
            prefix_events, frozen.profile, frozen.prerequisites
        )
    except (live.LiveIntegrityError, live.LiveInputError) as exc:
        raise WorksetRecoveryIntegrityError(
            "Recorded outcome consumption pre-head no longer replays"
        ) from exc
    outcome = context.outcome
    target_text = outcome.target_date.isoformat()
    seal = projection.seal_event
    if (
        projection.outstanding_target_date != outcome.target_date
        or projection.outstanding_issue_id is None
        or seal is None
        or seal.target_date != target_text
        or seal.entry_sha256 not in projection.anchored_seal_hashes
    ):
        raise WorksetRecoveryIntegrityError(
            "Recorded outcome consumption lost its confirmed lifecycle"
        )
    collector = _EventSpecCollector(prefix_events)
    try:
        live._append_outcome_batch(  # noqa: SLF001
            collector,
            frozen.profile,
            frozen.prerequisites,
            projection,
            outcome,
        )
    except (live.LiveIntegrityError, live.LiveInputError) as exc:
        raise WorksetRecoveryIntegrityError(
            "Recorded canonical outcome writer no longer reproduces"
        ) from exc
    specs = collector.specs
    if specs is None or len(specs) != OUTCOME_CONSUMPTION_EVENT_COUNT:
        raise WorksetRecoveryIntegrityError(
            "Recorded canonical outcome transaction changed length"
        )
    spec_payloads = [_event_spec_payload(spec) for spec in specs]
    rebuilt = {
        "schema_version": OUTCOME_CONSUMPTION_CONTRACT_SCHEMA,
        "writer_branch": (
            "preexisting_consumed_adoption"
            if context.ledger_consumed_at_freeze
            else "outstanding_settlement"
        ),
        "expected_pre_head": _pre_head_payload(expected),
        "live_epoch_id": frozen.projection.epoch_id,
        "target_date": target_text,
        "issue_id": projection.outstanding_issue_id,
        "sealed_entry_sha256": seal.entry_sha256,
        "tip_receipt_sha256": context.tip_receipt.sha256,
        "exact_outcome_sha256": outcome.sha256,
        "outcome_source_manifest_sha256": context.source_manifest.sha256,
        "outcome_source_id": outcome.outcome_source_id,
        "source_revision_id": outcome.source_revision_id,
        "ledger_consumed_at_freeze": context.ledger_consumed_at_freeze,
        "event_count": OUTCOME_CONSUMPTION_EVENT_COUNT,
        "first_event_key": specs[0].event_key,
        "terminal_event_key": specs[-1].event_key,
        "ordered_event_keys_sha256": _sha256(
            _canonical_bytes([spec.event_key for spec in specs])
        ),
        "event_specs_sha256": _sha256(_canonical_bytes(spec_payloads)),
    }
    stored_events = tuple(
        frozen.current_events[
            expected.event_count : expected.event_count
            + OUTCOME_CONSUMPTION_EVENT_COUNT
        ]
    )
    if (
        checked != rebuilt
        or tuple(event.event_type for event in stored_events)
        != OUTCOME_CONSUMPTION_EVENT_TYPES
        or any(
            not _outcome_event_matches_spec(event, spec)
            for event, spec in zip(stored_events, specs, strict=True)
        )
        or stored_events[0].previous_entry_sha256 != expected.entry_sha256
    ):
        raise WorksetRecoveryIntegrityError(
            "Recorded outcome consumption transaction changed"
        )
    return OutcomeConsumptionPlan(
        context=context,
        expected_pre_head=expected,
        specs=specs,
        stored_events=stored_events,
        contract=dict(checked),
    )


def _recorded_settled_revision_consumption_plan(
    item: Mapping[str, Any],
    reservation: Reservation,
    contract: Mapping[str, Any],
    previous_step_receipt: Mapping[str, Any] | None = None,
) -> OutcomeConsumptionPlan:
    context = _recorded_outcome_consumption_context(
        item, reservation, previous_step_receipt
    )
    checked = _exact(
        contract,
        {
            "schema_version",
            "writer_branch",
            "expected_pre_head",
            "live_epoch_id",
            "target_date",
            "original_settlement_entry_sha256",
            "issue_id",
            "sealed_entry_sha256",
            "previous_revision_id",
            "previous_outcome_sha256",
            "tip_receipt_sha256",
            "exact_outcome_sha256",
            "outcome_source_manifest_sha256",
            "outcome_source_id",
            "source_revision_id",
            "ledger_consumed_at_freeze",
            "online_states_sha256",
            "last_finalized_date",
            "outstanding_target_date",
            "event_count",
            "first_event_key",
            "terminal_event_key",
            "ordered_event_keys_sha256",
            "event_specs_sha256",
        },
        name="recorded settled revision consumption contract",
    )
    expected_branch = (
        "preexisting_settled_revision_adoption"
        if context.ledger_consumed_at_freeze
        else "settled_revision"
    )
    if (
        checked["schema_version"] != OUTCOME_REVISION_CONSUMPTION_CONTRACT_SCHEMA
        or checked["writer_branch"] != expected_branch
        or context.selection_kind != "revision"
        or checked["previous_revision_id"] != context.previous_revision_id
        or checked["previous_outcome_sha256"] != context.previous_outcome_sha256
        or checked["ledger_consumed_at_freeze"] is not context.ledger_consumed_at_freeze
        or checked["event_count"] != OUTCOME_SETTLED_REVISION_EVENT_COUNT
    ):
        raise WorksetRecoveryIntegrityError(
            "Recorded settled revision consumption contract branch changed"
        )
    _validate_outcome_predecessor_scope(
        selection_kind=context.selection_kind,
        previous_revision_id=context.previous_revision_id,
        previous_outcome_sha256=context.previous_outcome_sha256,
    )
    pre_head = _exact(
        checked["expected_pre_head"],
        {"epoch_id", "event_count", "sequence_id", "entry_sha256"},
        name="recorded settled revision consumption pre-head",
    )
    try:
        expected = live_cas.LiveLedgerPreHeadV1(**pre_head)
        live_cas._validate_pre_head(expected)  # noqa: SLF001
    except (TypeError, live_ledger.LedgerError) as exc:
        raise WorksetRecoveryIntegrityError(
            "Recorded settled revision consumption pre-head changed"
        ) from exc
    frozen = context.frozen
    if (
        expected.epoch_id != frozen.projection.epoch_id
        or (
            expected.event_count < frozen.expected_pre_head.event_count
            and not context.ledger_consumed_at_freeze
        )
        or len(frozen.current_events)
        < expected.event_count + OUTCOME_SETTLED_REVISION_EVENT_COUNT
    ):
        raise WorksetRecoveryIntegrityError(
            "Recorded settled revision consumption ledger slice disappeared"
        )
    prefix_events = frozen.current_events[: expected.event_count]
    pre_head_event = prefix_events[-1]
    if (
        pre_head_event.sequence_id != expected.sequence_id
        or pre_head_event.entry_sha256 != expected.entry_sha256
    ):
        raise WorksetRecoveryIntegrityError(
            "Recorded settled revision consumption pre-head moved"
        )
    projection, specs, settlement, issue_id, sealed_entry_sha256 = (
        _capture_settled_revision_specs(context, prefix_events)
    )
    if (
        len(specs) != OUTCOME_SETTLED_REVISION_EVENT_COUNT
        or tuple(spec.event_type for spec in specs)
        != OUTCOME_SETTLED_REVISION_EVENT_TYPES
    ):
        raise WorksetRecoveryIntegrityError(
            "Recorded canonical settled revision transaction changed shape"
        )
    outcome = context.outcome
    stations = frozen.profile["stations"]
    spec_payloads = [_event_spec_payload(spec) for spec in specs]
    rebuilt = {
        "schema_version": OUTCOME_REVISION_CONSUMPTION_CONTRACT_SCHEMA,
        "writer_branch": expected_branch,
        "expected_pre_head": _pre_head_payload(expected),
        "live_epoch_id": frozen.projection.epoch_id,
        "target_date": outcome.target_date.isoformat(),
        "original_settlement_entry_sha256": settlement.entry_sha256,
        "issue_id": issue_id,
        "sealed_entry_sha256": sealed_entry_sha256,
        "previous_revision_id": context.previous_revision_id,
        "previous_outcome_sha256": context.previous_outcome_sha256,
        "tip_receipt_sha256": context.tip_receipt.sha256,
        "exact_outcome_sha256": outcome.sha256,
        "outcome_source_manifest_sha256": context.source_manifest.sha256,
        "outcome_source_id": outcome.outcome_source_id,
        "source_revision_id": outcome.source_revision_id,
        "ledger_consumed_at_freeze": context.ledger_consumed_at_freeze,
        "online_states_sha256": live._states_sha256(  # noqa: SLF001
            projection.states, stations
        ),
        "last_finalized_date": projection.last_finalized_date.isoformat(),
        "outstanding_target_date": (
            projection.outstanding_target_date.isoformat()
            if projection.outstanding_target_date is not None
            else None
        ),
        "event_count": OUTCOME_SETTLED_REVISION_EVENT_COUNT,
        "first_event_key": specs[0].event_key,
        "terminal_event_key": specs[-1].event_key,
        "ordered_event_keys_sha256": _sha256(
            _canonical_bytes([spec.event_key for spec in specs])
        ),
        "event_specs_sha256": _sha256(_canonical_bytes(spec_payloads)),
    }
    stored_events = tuple(
        frozen.current_events[
            expected.event_count : expected.event_count
            + OUTCOME_SETTLED_REVISION_EVENT_COUNT
        ]
    )
    if (
        checked != rebuilt
        or tuple(event.event_type for event in stored_events)
        != OUTCOME_SETTLED_REVISION_EVENT_TYPES
        or any(
            not _outcome_event_matches_spec(event, spec)
            for event, spec in zip(stored_events, specs, strict=True)
        )
        or stored_events[0].previous_entry_sha256 != expected.entry_sha256
    ):
        raise WorksetRecoveryIntegrityError(
            "Recorded settled revision consumption transaction changed"
        )
    return OutcomeConsumptionPlan(
        context=context,
        expected_pre_head=expected,
        specs=specs,
        stored_events=stored_events,
        contract=dict(checked),
    )


def _recorded_outcome_consumption_plan(
    item: Mapping[str, Any],
    reservation: Reservation,
    contract: Mapping[str, Any],
    previous_step_receipt: Mapping[str, Any] | None = None,
) -> OutcomeConsumptionPlan:
    schema = contract.get("schema_version")
    if schema == OUTCOME_CONSUMPTION_CONTRACT_SCHEMA:
        return _recorded_outstanding_outcome_consumption_plan(
            item, reservation, contract, previous_step_receipt
        )
    if schema == OUTCOME_REVISION_CONSUMPTION_CONTRACT_SCHEMA:
        return _recorded_settled_revision_consumption_plan(
            item, reservation, contract, previous_step_receipt
        )
    raise WorksetRecoveryIntegrityError(
        "Recorded outcome consumption contract schema changed"
    )


def _outstanding_outcome_consumption_transaction_committed(
    reservation: Reservation, contract: Mapping[str, Any]
) -> bool:
    """Classify only an exact-position complete transaction as post-CAS state."""

    checked = _exact(
        contract,
        {
            "schema_version",
            "writer_branch",
            "expected_pre_head",
            "live_epoch_id",
            "target_date",
            "issue_id",
            "sealed_entry_sha256",
            "tip_receipt_sha256",
            "exact_outcome_sha256",
            "outcome_source_manifest_sha256",
            "outcome_source_id",
            "source_revision_id",
            "ledger_consumed_at_freeze",
            "event_count",
            "first_event_key",
            "terminal_event_key",
            "ordered_event_keys_sha256",
            "event_specs_sha256",
        },
        name="pending outcome consumption contract",
    )
    pre_head = _exact(
        checked["expected_pre_head"],
        {"epoch_id", "event_count", "sequence_id", "entry_sha256"},
        name="pending outcome consumption pre-head",
    )
    try:
        expected = live_cas.LiveLedgerPreHeadV1(**pre_head)
        live_cas._validate_pre_head(expected)  # noqa: SLF001
    except (TypeError, live_ledger.LedgerError) as exc:
        raise WorksetRecoveryIntegrityError(
            "Pending outcome consumption pre-head changed"
        ) from exc
    if (
        checked["schema_version"] != OUTCOME_CONSUMPTION_CONTRACT_SCHEMA
        or checked["writer_branch"]
        not in {"outstanding_settlement", "preexisting_consumed_adoption"}
        or checked["event_count"] != OUTCOME_CONSUMPTION_EVENT_COUNT
        or checked["live_epoch_id"] != expected.epoch_id
        or not isinstance(checked["ledger_consumed_at_freeze"], bool)
        or (checked["writer_branch"] == "preexisting_consumed_adoption")
        is not checked["ledger_consumed_at_freeze"]
        or not isinstance(checked["first_event_key"], str)
        or not isinstance(checked["terminal_event_key"], str)
    ):
        raise WorksetRecoveryIntegrityError(
            "Pending outcome consumption contract identity changed"
        )
    frozen = _frozen_live_prefix(reservation)
    if (
        expected.epoch_id != frozen.projection.epoch_id
        or (
            expected.event_count < frozen.expected_pre_head.event_count
            and not checked["ledger_consumed_at_freeze"]
        )
        or len(frozen.current_events) < expected.event_count
    ):
        raise WorksetRecoveryIntegrityError(
            "Pending outcome consumption pre-head disappeared"
        )
    observed_pre_head = frozen.current_events[expected.event_count - 1]
    if (
        observed_pre_head.sequence_id != expected.sequence_id
        or observed_pre_head.entry_sha256 != expected.entry_sha256
    ):
        raise WorksetRecoveryIntegrityError(
            "Pending outcome consumption pre-head moved"
        )
    start = expected.event_count
    stop = start + OUTCOME_CONSUMPTION_EVENT_COUNT
    events = frozen.current_events
    first_key = checked["first_event_key"]
    terminal_key = checked["terminal_event_key"]
    complete = (
        len(events) >= stop
        and events[start].event_key == first_key
        and events[stop - 1].event_key == terminal_key
    )
    if complete:
        return True
    if any(event.event_key in {first_key, terminal_key} for event in events[start:]):
        raise WorksetRecoveryIntegrityError(
            "Pending outcome consumption transaction is partial or displaced"
        )
    return False


def _settled_revision_consumption_transaction_committed(
    reservation: Reservation, contract: Mapping[str, Any]
) -> bool:
    checked = _exact(
        contract,
        {
            "schema_version",
            "writer_branch",
            "expected_pre_head",
            "live_epoch_id",
            "target_date",
            "original_settlement_entry_sha256",
            "issue_id",
            "sealed_entry_sha256",
            "previous_revision_id",
            "previous_outcome_sha256",
            "tip_receipt_sha256",
            "exact_outcome_sha256",
            "outcome_source_manifest_sha256",
            "outcome_source_id",
            "source_revision_id",
            "ledger_consumed_at_freeze",
            "online_states_sha256",
            "last_finalized_date",
            "outstanding_target_date",
            "event_count",
            "first_event_key",
            "terminal_event_key",
            "ordered_event_keys_sha256",
            "event_specs_sha256",
        },
        name="pending settled revision consumption contract",
    )
    pre_head = _exact(
        checked["expected_pre_head"],
        {"epoch_id", "event_count", "sequence_id", "entry_sha256"},
        name="pending settled revision consumption pre-head",
    )
    try:
        expected = live_cas.LiveLedgerPreHeadV1(**pre_head)
        live_cas._validate_pre_head(expected)  # noqa: SLF001
    except (TypeError, live_ledger.LedgerError) as exc:
        raise WorksetRecoveryIntegrityError(
            "Pending settled revision consumption pre-head changed"
        ) from exc
    expected_branch = (
        "preexisting_settled_revision_adoption"
        if checked["ledger_consumed_at_freeze"] is True
        else "settled_revision"
    )
    if (
        checked["schema_version"] != OUTCOME_REVISION_CONSUMPTION_CONTRACT_SCHEMA
        or checked["writer_branch"] != expected_branch
        or not isinstance(checked["ledger_consumed_at_freeze"], bool)
        or checked["event_count"] != OUTCOME_SETTLED_REVISION_EVENT_COUNT
        or checked["live_epoch_id"] != expected.epoch_id
        or not isinstance(checked["previous_revision_id"], str)
        or not checked["previous_revision_id"]
        or _hash_text(
            checked["previous_outcome_sha256"],
            name="pending settled revision predecessor",
        )
        != checked["previous_outcome_sha256"]
        or not isinstance(checked["first_event_key"], str)
        or not isinstance(checked["terminal_event_key"], str)
    ):
        raise WorksetRecoveryIntegrityError(
            "Pending settled revision consumption contract identity changed"
        )
    frozen = _frozen_live_prefix(reservation)
    if (
        expected.epoch_id != frozen.projection.epoch_id
        or (
            expected.event_count < frozen.expected_pre_head.event_count
            and not checked["ledger_consumed_at_freeze"]
        )
        or len(frozen.current_events) < expected.event_count
    ):
        raise WorksetRecoveryIntegrityError(
            "Pending settled revision consumption pre-head disappeared"
        )
    observed_pre_head = frozen.current_events[expected.event_count - 1]
    if (
        observed_pre_head.sequence_id != expected.sequence_id
        or observed_pre_head.entry_sha256 != expected.entry_sha256
    ):
        raise WorksetRecoveryIntegrityError(
            "Pending settled revision consumption pre-head moved"
        )
    start = expected.event_count
    stop = start + OUTCOME_SETTLED_REVISION_EVENT_COUNT
    events = frozen.current_events
    first_key = checked["first_event_key"]
    terminal_key = checked["terminal_event_key"]
    complete = (
        len(events) >= stop
        and events[start].event_key == first_key
        and events[stop - 1].event_key == terminal_key
    )
    if complete:
        return True
    if any(event.event_key in {first_key, terminal_key} for event in events[start:]):
        raise WorksetRecoveryIntegrityError(
            "Pending settled revision transaction is partial or displaced"
        )
    return False


def _outcome_consumption_transaction_committed(
    reservation: Reservation, contract: Mapping[str, Any]
) -> bool:
    schema = contract.get("schema_version")
    if schema == OUTCOME_CONSUMPTION_CONTRACT_SCHEMA:
        return _outstanding_outcome_consumption_transaction_committed(
            reservation, contract
        )
    if schema == OUTCOME_REVISION_CONSUMPTION_CONTRACT_SCHEMA:
        return _settled_revision_consumption_transaction_committed(
            reservation, contract
        )
    raise WorksetRecoveryIntegrityError(
        "Pending outcome consumption contract schema changed"
    )


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
    item: Mapping[str, Any],
    reservation: Reservation,
    previous_step_receipt: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    if previous_step_receipt is not None:
        return _anchor_retry_request_contract(item, reservation, previous_step_receipt)
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


def _anchor_retry_request_contract(
    item: Mapping[str, Any],
    reservation: Reservation,
    previous_step_receipt: Mapping[str, Any],
) -> dict[str, object]:
    frozen, seal, _ = _anchor_result_context(item, reservation)
    semantics = previous_step_receipt.get("action_semantics")
    if (
        previous_step_receipt.get("action") != "anchor_result_recorded"
        or previous_step_receipt.get("action_output_kind") != "live_anchor_result_event"
        or previous_step_receipt.get("action_output") is not None
        or previous_step_receipt.get("next_actions") != ["anchor_request_recorded"]
        or previous_step_receipt.get("terminal_for_key") is not False
        or not isinstance(semantics, Mapping)
        or semantics.get("schema_version")
        != "ootang_live_anchor_result_action_output_v1"
        or semantics.get("result_outcome") != "deterministic_failure"
        or semantics.get("event_type") != "anchor_failed"
        or semantics.get("selected_next_action") != "anchor_request_recorded"
        or semantics.get("live_ledger_event_recorded") is not True
        or semantics.get("network_action_performed") is not False
        or semantics.get("external_response_network_action_performed") is not True
    ):
        raise WorksetRecoveryIntegrityError(
            "Anchor retry lost its failed-result receipt authority"
        )
    sequence_id = semantics.get("sequence_id")
    attempt = semantics.get("attempt")
    if (
        not isinstance(sequence_id, int)
        or isinstance(sequence_id, bool)
        or sequence_id < 1
        or not isinstance(attempt, int)
        or isinstance(attempt, bool)
        or attempt < 1
        or len(frozen.current_events) < sequence_id
    ):
        raise WorksetRecoveryIntegrityError(
            "Anchor retry failed-result position changed"
        )
    failed = frozen.current_events[sequence_id - 1]
    if (
        failed.sequence_id != sequence_id
        or failed.entry_sha256 != semantics.get("entry_sha256")
        or failed.previous_entry_sha256 != semantics.get("previous_entry_sha256")
        or failed.event_key != semantics.get("event_key")
        or failed.event_type != "anchor_failed"
        or failed.payload.get("attempt") != attempt
        or failed.payload.get("sealed_entry_sha256") != seal.entry_sha256
        or failed.payload.get("reason_code") != "request_or_receipt_validation_failed"
        or failed.payload.get("error_type") != "AnchorResultProtocolFailure"
        or failed.payload.get("retry_policy") != "automatic_next_poll"
        or semantics.get("live_epoch_id") != frozen.projection.epoch_id
        or semantics.get("sealed_entry_sha256") != seal.entry_sha256
    ):
        raise WorksetRecoveryIntegrityError("Anchor retry failed-result event changed")
    next_attempt = attempt + 1
    request_payload = {
        "live_epoch_id": frozen.projection.epoch_id,
        "target_date": seal.target_date,
        "sealed_sequence_id": seal.sequence_id,
        "sealed_entry_sha256": seal.entry_sha256,
        "attempt": next_attempt,
    }
    spec = live._event_spec(  # noqa: SLF001
        event_key=(
            f"{frozen.projection.epoch_id}:{seal.target_date}:"
            f"anchor:{next_attempt}:requested"
        ),
        event_type="anchor_requested",
        prerequisites=frozen.prerequisites,
        payload=request_payload,
        target_date_value=date.fromisoformat(str(seal.target_date)),
        issue_id=seal.issue_id,
        input_manifest_sha256=seal.input_manifest_sha256,
        state_before_sha256=seal.state_after_sha256,
        state_after_sha256=seal.state_after_sha256,
    )
    spec_payload = _event_spec_payload(spec)
    contract: dict[str, object] = {
        "schema_version": ANCHOR_REQUEST_CONTRACT_SCHEMA,
        "expected_pre_head": {
            "epoch_id": frozen.projection.epoch_id,
            "event_count": failed.sequence_id,
            "sequence_id": failed.sequence_id,
            "entry_sha256": failed.entry_sha256,
        },
        "attempt": next_attempt,
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


def _verify_anchor_result_previous_request_receipt(
    previous_step_receipt: Mapping[str, Any],
    request_event: live_ledger.LedgerEvent,
) -> None:
    semantics = previous_step_receipt.get("action_semantics")
    if (
        previous_step_receipt.get("action") != "anchor_request_recorded"
        or previous_step_receipt.get("action_output_kind")
        != "live_anchor_request_event"
        or previous_step_receipt.get("action_output") is not None
        or previous_step_receipt.get("next_actions") != ["anchor_result_recorded"]
        or previous_step_receipt.get("terminal_for_key") is not False
        or not isinstance(semantics, Mapping)
        or semantics.get("schema_version")
        != "ootang_live_anchor_request_action_output_v1"
        or semantics.get("live_epoch_id") != request_event.payload.get("live_epoch_id")
        or semantics.get("event_key") != request_event.event_key
        or semantics.get("event_type") != request_event.event_type
        or semantics.get("sequence_id") != request_event.sequence_id
        or semantics.get("entry_sha256") != request_event.entry_sha256
        or semantics.get("previous_entry_sha256") != request_event.previous_entry_sha256
        or semantics.get("sealed_entry_sha256")
        != request_event.payload.get("sealed_entry_sha256")
        or semantics.get("attempt") != request_event.payload.get("attempt")
        or semantics.get("live_ledger_event_recorded") is not True
        or semantics.get("network_action_performed") is not False
    ):
        raise WorksetRecoveryIntegrityError(
            "Anchor result lost its preceding request receipt"
        )


def _anchor_result_request_contract(
    item: Mapping[str, Any],
    reservation: Reservation,
    previous_step_receipt: Mapping[str, Any] | None = None,
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
    if previous_step_receipt is not None:
        _verify_anchor_result_previous_request_receipt(
            previous_step_receipt, request_event
        )
    elif authority["action"] == "anchor_result_recorded":
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
    _verify_anchor_result_request_contract(
        item,
        reservation,
        contract,
        previous_step_receipt=previous_step_receipt,
    )
    return contract


def _verify_anchor_result_request_contract(
    item: Mapping[str, Any],
    reservation: Reservation,
    contract: Mapping[str, Any],
    previous_step_receipt: Mapping[str, Any] | None = None,
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
    if previous_step_receipt is not None:
        _verify_anchor_result_previous_request_receipt(
            previous_step_receipt, request_event
        )
    elif authority["action"] == "anchor_result_recorded":
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
    previous_step_receipt: Mapping[str, Any] | None = None,
) -> None:
    _verify_anchor_result_request_contract(
        item,
        reservation,
        contract,
        previous_step_receipt=previous_step_receipt,
    )
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


def _anchor_result_expected_pre_head(
    contract: Mapping[str, Any],
) -> live_cas.LiveLedgerPreHeadV1:
    record = _exact(
        contract.get("expected_result_pre_head"),
        {"epoch_id", "event_count", "sequence_id", "entry_sha256"},
        name="anchor result expected pre-head",
    )
    try:
        expected = live_cas.LiveLedgerPreHeadV1(**record)
        live_cas._validate_pre_head(expected)  # noqa: SLF001
    except (TypeError, live_ledger.LedgerError) as exc:
        raise WorksetRecoveryIntegrityError(
            "Anchor result expected pre-head changed"
        ) from exc
    request = _exact(
        contract.get("request_event"),
        _ANCHOR_RESULT_REQUEST_EVENT_KEYS,
        name="anchor result request event",
    )
    body = contract.get("request_body")
    if (
        not isinstance(body, Mapping)
        or expected.epoch_id != body.get("live_epoch_id")
        or expected.event_count != request.get("sequence_id")
        or expected.sequence_id != request.get("sequence_id")
        or expected.entry_sha256 != request.get("entry_sha256")
    ):
        raise WorksetRecoveryIntegrityError(
            "Anchor result expected pre-head lost its request binding"
        )
    return expected


def _anchor_result_event_spec(
    item: Mapping[str, Any],
    reservation: Reservation,
    contract: Mapping[str, Any],
    observation: Mapping[str, Any],
    previous_step_receipt: Mapping[str, Any] | None = None,
) -> tuple[live_ledger.EventSpec, live_cas.LiveLedgerPreHeadV1]:
    """Rebuild the unique frozen-writer-shaped result event from linked evidence."""

    _verify_anchor_result_request_contract(
        item,
        reservation,
        contract,
        previous_step_receipt=previous_step_receipt,
    )
    frozen, seal, _ = _anchor_result_context(item, reservation)
    expected = _anchor_result_expected_pre_head(contract)
    request = _exact(
        contract.get("request_body"),
        {
            "live_epoch_id",
            "target_date",
            "sealed_sequence_id",
            "sealed_entry_sha256",
            "attempt",
        },
        name="anchor result request body",
    )
    request_event = _exact(
        contract.get("request_event"),
        _ANCHOR_RESULT_REQUEST_EVENT_KEYS,
        name="anchor result request event",
    )
    event_key = request_event.get("event_key")
    if (
        not isinstance(event_key, str)
        or not event_key.endswith(":requested")
        or event_key.removesuffix(":requested") + ":requested" != event_key
    ):
        raise WorksetRecoveryIntegrityError("Anchor result request event key changed")
    prefix = event_key.removesuffix(":requested")
    outcome = observation.get("outcome")
    if outcome == "candidate_confirmed":
        candidate = _exact(
            observation.get("validated_response"),
            {
                "provider",
                "receipt_id",
                "anchored_at_utc",
                "sealed_entry_sha256",
                "receipt",
            },
            name="anchor result validated response",
        )
        if observation.get("failure") is not None:
            raise WorksetRecoveryIntegrityError(
                "Confirmed anchor result retained failure evidence"
            )
        try:
            stored = live._stored_anchor_payload(  # noqa: SLF001
                dict(candidate), sealed_entry_sha256=seal.entry_sha256
            )
        except (live.LiveInputError, live.LiveIntegrityError) as exc:
            raise WorksetRecoveryIntegrityError(
                "Confirmed anchor result changed after observation"
            ) from exc
        if stored != candidate:
            raise WorksetRecoveryIntegrityError(
                "Confirmed anchor result normalization changed"
            )
        event_type = "anchor_confirmed"
        suffix = "confirmed"
        payload = {**request, **candidate}
    elif outcome == "deterministic_failure":
        failure = _exact(
            observation.get("failure"),
            {"stage", "code", "error_type", "retry_policy"},
            name="anchor result deterministic failure",
        )
        if (
            observation.get("validated_response") is not None
            or (failure.get("stage"), failure.get("code"))
            not in ANCHOR_RESULT_FAILURE_TAXONOMY
            or failure.get("error_type") != "AnchorResultProtocolFailure"
            or failure.get("retry_policy") != "record_failure_then_automatic_next_poll"
        ):
            raise WorksetRecoveryIntegrityError(
                "Anchor result deterministic failure taxonomy changed"
            )
        event_type = "anchor_failed"
        suffix = "failed"
        payload = {
            **request,
            "reason_code": "request_or_receipt_validation_failed",
            "error_type": failure["error_type"],
            "retry_policy": "automatic_next_poll",
        }
    else:
        raise WorksetRecoveryIntegrityError("Anchor result outcome changed")
    try:
        target = date.fromisoformat(str(seal.target_date))
    except ValueError as exc:
        raise WorksetRecoveryIntegrityError(
            "Anchor result target date changed"
        ) from exc
    spec = live._event_spec(  # noqa: SLF001
        event_key=f"{prefix}:{suffix}",
        event_type=event_type,
        prerequisites=frozen.prerequisites,
        payload=payload,
        target_date_value=target,
        issue_id=seal.issue_id,
        input_manifest_sha256=seal.input_manifest_sha256,
        state_before_sha256=seal.state_after_sha256,
        state_after_sha256=seal.state_after_sha256,
    )
    try:
        live_ledger._prepare_spec(spec)  # noqa: SLF001
    except live_ledger.LedgerError as exc:
        raise WorksetRecoveryIntegrityError(
            "Anchor result EventSpec is invalid"
        ) from exc
    return spec, expected


_OUTSTANDING_LIFECYCLE_AUTHORITY_KEYS = {
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
}


def _settlement_confirmation_context(
    item: Mapping[str, Any],
    reservation: Reservation,
    previous_step_receipt: Mapping[str, Any] | None,
) -> tuple[
    FrozenLivePrefix,
    live_ledger.LedgerEvent,
    live_ledger.LedgerEvent,
]:
    """Resolve the exact confirmed-event pre-head without reading outcome files."""

    if item.get("family") != "live_outstanding":
        raise WorksetRecoveryIntegrityError("Settlement item family changed")
    authority = _exact(
        item.get("authority"),
        _OUTSTANDING_LIFECYCLE_AUTHORITY_KEYS,
        name="outcome settlement authority",
    )
    if (
        authority["record_type"] != "outstanding_live_lifecycle"
        or authority["terminal"] is not False
    ):
        raise WorksetRecoveryIntegrityError("Settlement authority state changed")
    frozen = _frozen_live_prefix(reservation)
    seal = frozen.projection.seal_event
    seal_record = _exact(
        authority["seal_event"],
        {"sequence_id", "entry_sha256", "event_type", "target_date", "issue_id"},
        name="outcome settlement seal record",
    )
    if (
        seal is None
        or seal.event_type != "issue_batch_sealed"
        or authority["target_date"] != seal.target_date
        or authority["issue_id"] != seal.issue_id
        or authority["old_live_epoch_id"] != frozen.projection.epoch_id
        or authority["input_manifest_sha256"] != seal.input_manifest_sha256
        or authority["frozen_live_upper_tip"] != frozen.expected_pre_head.entry_sha256
        or seal_record
        != {
            "sequence_id": seal.sequence_id,
            "entry_sha256": seal.entry_sha256,
            "event_type": seal.event_type,
            "target_date": seal.target_date,
            "issue_id": seal.issue_id,
        }
    ):
        raise WorksetRecoveryIntegrityError("Settlement frozen lifecycle changed")
    _hash_text(authority["issue_sha256"], name="outcome settlement issue")
    _hash_text(
        authority["input_manifest_sha256"],
        name="outcome settlement input manifest",
    )

    if previous_step_receipt is None:
        confirmed_record = _exact(
            authority["anchor_confirmed_event"],
            {"sequence_id", "entry_sha256", "event_type", "target_date", "issue_id"},
            name="frozen settlement confirmation",
        )
        position = confirmed_record["sequence_id"]
        if (
            authority["action"] != "outcome_batch_settled"
            or not isinstance(position, int)
            or isinstance(position, bool)
            or position < 1
            or position > frozen.expected_pre_head.event_count
        ):
            raise WorksetRecoveryIntegrityError(
                "Frozen settlement confirmation is outside the frozen prefix"
            )
        confirmed = frozen.frozen_events[position - 1]
        if confirmed_record != {
            "sequence_id": confirmed.sequence_id,
            "entry_sha256": confirmed.entry_sha256,
            "event_type": confirmed.event_type,
            "target_date": confirmed.target_date,
            "issue_id": confirmed.issue_id,
        }:
            raise WorksetRecoveryIntegrityError(
                "Frozen settlement confirmation event changed"
            )
    else:
        # The manifest predates confirmation.  The preceding immutable result
        # receipt is the only authority allowed to extend that frozen branch.
        if (
            authority["action"]
            not in {"anchor_request_recorded", "anchor_result_recorded"}
            or authority["anchor_confirmed_event"] is not None
            or previous_step_receipt.get("action") != "anchor_result_recorded"
            or previous_step_receipt.get("action_output_kind")
            != "live_anchor_result_event"
            or previous_step_receipt.get("action_output") is not None
            or previous_step_receipt.get("next_actions") != ["outcome_batch_settled"]
            or previous_step_receipt.get("terminal_for_key") is not False
        ):
            raise WorksetRecoveryIntegrityError(
                "Settlement lost its confirmed-result receipt authority"
            )
        semantics = previous_step_receipt.get("action_semantics")
        if (
            not isinstance(semantics, Mapping)
            or semantics.get("schema_version")
            != "ootang_live_anchor_result_action_output_v1"
            or semantics.get("result_outcome") != "candidate_confirmed"
            or semantics.get("event_type") != "anchor_confirmed"
            or semantics.get("selected_next_action") != "outcome_batch_settled"
            or semantics.get("sealed_entry_sha256") != seal.entry_sha256
            or semantics.get("live_epoch_id") != frozen.projection.epoch_id
            or semantics.get("live_ledger_event_recorded") is not True
            or semantics.get("trusted_anchor_receipt_verified") is not False
            or semantics.get("e2_live_evidence_eligible") is not False
        ):
            raise WorksetRecoveryIntegrityError(
                "Settlement confirmed-result semantics changed"
            )
        position = semantics.get("sequence_id")
        if (
            not isinstance(position, int)
            or isinstance(position, bool)
            or position < 1
            or len(frozen.current_events) < position
        ):
            raise WorksetRecoveryIntegrityError(
                "Settlement confirmation position changed"
            )
        confirmed = frozen.current_events[position - 1]
        if (
            confirmed.sequence_id != position
            or confirmed.event_type != "anchor_confirmed"
            or confirmed.entry_sha256 != semantics.get("entry_sha256")
            or confirmed.previous_entry_sha256 != semantics.get("previous_entry_sha256")
            or confirmed.event_key != semantics.get("event_key")
            or confirmed.target_date != seal.target_date
            or confirmed.issue_id != seal.issue_id
            or confirmed.payload.get("sealed_entry_sha256") != seal.entry_sha256
        ):
            raise WorksetRecoveryIntegrityError(
                "Settlement confirmation ledger event changed"
            )

    if (
        confirmed.event_type != "anchor_confirmed"
        or confirmed.target_date != seal.target_date
        or confirmed.issue_id != seal.issue_id
        or confirmed.payload.get("sealed_entry_sha256") != seal.entry_sha256
    ):
        raise WorksetRecoveryIntegrityError(
            "Settlement confirmation does not cover the frozen issue"
        )
    return frozen, seal, confirmed


def _settlement_event_record(event: live_ledger.LedgerEvent) -> dict[str, object]:
    return {
        "sequence_id": event.sequence_id,
        "event_key": event.event_key,
        "entry_sha256": event.entry_sha256,
    }


def _settlement_outcome_dependency(
    item: Mapping[str, Any], reservation: Reservation
) -> dict[str, object]:
    """Resolve one manifest-reserved outcome item for the live terminal proof."""

    dependencies = item.get("dependency_keys")
    raw_items = reservation.manifest.get("items")
    if not isinstance(dependencies, list) or not isinstance(raw_items, list):
        raise WorksetRecoveryIntegrityError(
            "Settlement outcome dependency authority changed type"
        )
    candidates: list[dict[str, object]] = []
    for candidate in raw_items:
        if (
            not isinstance(candidate, dict)
            or candidate.get("natural_key") not in dependencies
            or candidate.get("family") != "outcome_revision"
        ):
            continue
        authority = candidate.get("authority")
        if not isinstance(authority, dict):
            raise WorksetRecoveryIntegrityError(
                "Settlement outcome dependency lost its authority"
            )
        record_type = authority.get("record_type")
        if record_type == "outcome_receipt_chain":
            revision = authority.get("tip_source_revision_id")
            batch_sha256 = authority.get("tip_exact_outcome_sha256")
            source_id = None
            successor = candidate.get("canonical_successor_state")
            if (
                successor
                not in {"outcome_materialized", "outcome_or_revision_consumed"}
                or authority.get("tip_published")
                is not (successor == "outcome_or_revision_consumed")
                or authority.get("tip_ledger_consumed") is not False
                or authority.get("terminal") is not False
            ):
                raise WorksetRecoveryIntegrityError(
                    "Settlement outcome receipt dependency changed state"
                )
            _hash_text(batch_sha256, name="settlement dependency outcome batch")
        elif record_type == "machine_selected_source_outcome":
            revision = authority.get("source_revision_id")
            batch_sha256 = None
            source_id = authority.get("outcome_source_id")
            if (
                candidate.get("canonical_successor_state") != "outcome_materialized"
                or authority.get("selection_kind") != "outstanding"
                or authority.get("terminal") is not False
                or not isinstance(source_id, str)
                or not source_id
            ):
                raise WorksetRecoveryIntegrityError(
                    "Settlement selected-outcome dependency changed state"
                )
        else:
            continue
        natural_key = candidate.get("natural_key")
        namespace_digest = candidate.get("namespace_digest")
        if (
            not isinstance(natural_key, str)
            or not natural_key
            or not isinstance(namespace_digest, str)
            or _hash_text(namespace_digest, name="settlement dependency namespace")
            != namespace_digest
            or not isinstance(revision, str)
            or not revision
        ):
            raise WorksetRecoveryIntegrityError(
                "Settlement outcome dependency identity changed"
            )
        candidates.append(
            {
                "natural_key": natural_key,
                "namespace_digest": namespace_digest,
                "record_type": record_type,
                "target_date": authority.get("target_date"),
                "old_live_epoch_id": authority.get("old_live_epoch_id"),
                "source_revision_id": revision,
                "outcome_batch_sha256": batch_sha256,
                "outcome_source_id": source_id,
            }
        )
    if not candidates:
        raise WorksetRecoveryExternalWait(
            "the live item has no manifest-reserved outcome dependency"
        )
    if len(candidates) != 1:
        raise WorksetRecoveryIntegrityError(
            "Settlement outcome dependency is not unique"
        )
    dependency = candidates[0]
    authority = item.get("authority")
    if (
        not isinstance(authority, Mapping)
        or dependency["target_date"] != authority.get("target_date")
        or dependency["old_live_epoch_id"] != authority.get("old_live_epoch_id")
    ):
        raise WorksetRecoveryIntegrityError(
            "Settlement outcome dependency scope changed"
        )
    return dependency


def _outcome_settlement_adoption_contract(
    item: Mapping[str, Any],
    reservation: Reservation,
    previous_step_receipt: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    """Bind a receipt to one already-existing, fully replayed 43-event batch."""

    dependency = _settlement_outcome_dependency(item, reservation)
    frozen, seal, confirmed = _settlement_confirmation_context(
        item, reservation, previous_step_receipt
    )
    revision = dependency["source_revision_id"]
    matching_settlements = [
        event
        for event in frozen.current_events[frozen.expected_pre_head.event_count :]
        if event.event_type == "outcome_batch_settled"
        and event.target_date == seal.target_date
        and event.payload.get("source_revision_id") == revision
        and (
            dependency["outcome_batch_sha256"] is None
            or event.payload.get("outcome_batch_sha256")
            == dependency["outcome_batch_sha256"]
        )
    ]
    if not matching_settlements:
        raise WorksetRecoveryExternalWait(
            "the reserved outcome dependency has no durable settlement transaction yet"
        )
    if len(matching_settlements) != 1:
        raise WorksetRecoveryIntegrityError(
            "Reserved outcome dependency has multiple settlement transactions"
        )
    settled = matching_settlements[0]
    first_index = settled.sequence_id - OUTCOME_SETTLEMENT_EVENT_COUNT
    if first_index < frozen.expected_pre_head.event_count:
        raise WorksetRecoveryIntegrityError(
            "Outcome settlement transaction precedes its frozen reservation"
        )
    batch = tuple(
        frozen.current_events[
            first_index : first_index + OUTCOME_SETTLEMENT_EVENT_COUNT
        ]
    )
    if len(batch) != OUTCOME_SETTLEMENT_EVENT_COUNT or batch[-1] != settled:
        raise WorksetRecoveryIntegrityError(
            "Outcome settlement transaction is a partial ledger slice"
        )
    pre_head = frozen.current_events[first_index - 1]
    try:
        pre_settlement_projection = live._reconstruct_projection(  # noqa: SLF001
            frozen.current_events[: pre_head.sequence_id],
            frozen.profile,
            frozen.prerequisites,
        )
    except (live.LiveIntegrityError, live.LiveInputError) as exc:
        raise WorksetRecoveryIntegrityError(
            "Settlement pre-head no longer replays"
        ) from exc
    if (
        pre_settlement_projection.outstanding_target_date
        != date.fromisoformat(str(seal.target_date))
        or pre_settlement_projection.outstanding_issue_id != seal.issue_id
        or seal.entry_sha256 not in pre_settlement_projection.anchored_seal_hashes
        or confirmed.sequence_id >= batch[0].sequence_id
    ):
        raise WorksetRecoveryIntegrityError(
            "Settlement pre-head changed the confirmed outstanding lifecycle"
        )
    if tuple(event.event_type for event in batch) != OUTCOME_SETTLEMENT_EVENT_TYPES:
        raise WorksetRecoveryIntegrityError(
            "Outcome settlement transaction type/order changed"
        )
    first = batch[0]
    if (
        first.sequence_id != pre_head.sequence_id + 1
        or first.previous_entry_sha256 != pre_head.entry_sha256
        or settled.sequence_id != pre_head.sequence_id + OUTCOME_SETTLEMENT_EVENT_COUNT
        or any(event.target_date != seal.target_date for event in batch)
        or first.issue_id != seal.issue_id
        or settled.issue_id != seal.issue_id
        or batch[-2].issue_id != seal.issue_id
        or first.payload.get("issue_batch_sealed_entry_sha256") != seal.entry_sha256
        or settled.payload.get("issue_batch_sealed_entry_sha256") != seal.entry_sha256
        or settled.payload.get("anchor_confirmed") is not True
        or settled.payload.get("trusted_anchor_receipt_verified") is not False
        or settled.payload.get("e2_live_evidence_eligible") is not False
        or settled.payload.get("formal_warning_output") is not False
        or settled.payload.get("externally_anchored_before_outcome")
        is not settled.payload.get("engineering_blind_time_order_candidate")
        or any(
            event.payload.get("formal_warning_output") is not False
            for event in batch
            if event.event_type == "score_recorded"
        )
        or batch[-2].payload.get("warning_color_output") is not False
    ):
        raise WorksetRecoveryIntegrityError(
            "Outcome settlement transaction safety semantics changed"
        )
    settled_revision = settled.payload.get("source_revision_id")
    batch_sha256 = settled.payload.get("outcome_batch_sha256")
    source_id = settled.payload.get("outcome_source_id")
    input_manifest_sha256 = settled.input_manifest_sha256
    if (
        not isinstance(settled_revision, str)
        or settled_revision != revision
        or not isinstance(source_id, str)
        or not source_id
        or _hash_text(batch_sha256, name="settled outcome batch") != batch_sha256
        or _hash_text(input_manifest_sha256, name="settled outcome input manifest")
        != input_manifest_sha256
        or first.payload.get("source_revision_id") != settled_revision
        or first.payload.get("outcome_batch_sha256") != batch_sha256
        or first.payload.get("outcome_source_id") != source_id
        or first.input_manifest_sha256 != input_manifest_sha256
        or (
            dependency["outcome_source_id"] is not None
            and dependency["outcome_source_id"] != source_id
        )
    ):
        raise WorksetRecoveryIntegrityError(
            "Outcome settlement transaction identity changed"
        )
    try:
        settled_projection = live._reconstruct_projection(  # noqa: SLF001
            frozen.current_events[: settled.sequence_id],
            frozen.profile,
            frozen.prerequisites,
        )
    except (live.LiveIntegrityError, live.LiveInputError) as exc:
        raise WorksetRecoveryIntegrityError(
            "Outcome settlement transaction no longer fully replays"
        ) from exc
    replayed = settled_projection.settled_events.get(str(seal.target_date))
    if (
        replayed is None
        or replayed.entry_sha256 != settled.entry_sha256
        or settled_projection.outstanding_target_date is not None
        or settled_projection.last_finalized_date
        != date.fromisoformat(str(seal.target_date))
    ):
        raise WorksetRecoveryIntegrityError(
            "Outcome settlement transaction did not close the frozen lifecycle"
        )
    ordered_entries_sha256 = _sha256(
        _canonical_bytes([event.entry_sha256 for event in batch])
    )
    return {
        "schema_version": OUTCOME_SETTLEMENT_ADOPTION_CONTRACT_SCHEMA,
        "expected_pre_head": {
            "epoch_id": frozen.projection.epoch_id,
            "event_count": pre_head.sequence_id,
            "sequence_id": pre_head.sequence_id,
            "entry_sha256": pre_head.entry_sha256,
        },
        "target_date": seal.target_date,
        "issue_id": seal.issue_id,
        "sealed_entry_sha256": seal.entry_sha256,
        "confirmation_event": _settlement_event_record(confirmed),
        "outcome_dependency": dependency,
        "outcome_batch_sha256": batch_sha256,
        "outcome_source_id": source_id,
        "source_revision_id": settled_revision,
        "outcome_input_manifest_sha256": input_manifest_sha256,
        "event_count": OUTCOME_SETTLEMENT_EVENT_COUNT,
        "ordered_entries_sha256": ordered_entries_sha256,
        "first_event": _settlement_event_record(first),
        "terminal_event": _settlement_event_record(settled),
        "state_before_sha256": settled.state_before_sha256,
        "state_after_sha256": settled.state_after_sha256,
        "engineering_blind_time_order_candidate": settled.payload.get(
            "engineering_blind_time_order_candidate"
        ),
    }


def _outcome_settlement_adoption_action(
    item: Mapping[str, Any],
    reservation: Reservation,
    contract: Mapping[str, Any] | None,
    previous_step_receipt: Mapping[str, Any] | None = None,
) -> ActionOutput:
    if contract is None:
        raise WorksetRecoveryIntegrityError(
            "Outcome settlement adoption intent lost its contract"
        )
    rebuilt = _outcome_settlement_adoption_contract(
        item, reservation, previous_step_receipt
    )
    if contract != rebuilt:
        raise WorksetRecoveryIntegrityError(
            "Outcome settlement adoption contract changed"
        )
    return ActionOutput(
        "live_outcome_settlement_transaction",
        None,
        {
            "schema_version": "ootang_live_outcome_settlement_action_output_v1",
            "live_epoch_id": rebuilt["expected_pre_head"]["epoch_id"],
            "target_date": rebuilt["target_date"],
            "issue_id": rebuilt["issue_id"],
            "sealed_entry_sha256": rebuilt["sealed_entry_sha256"],
            "confirmation_entry_sha256": rebuilt["confirmation_event"]["entry_sha256"],
            "outcome_dependency": rebuilt["outcome_dependency"],
            "outcome_batch_sha256": rebuilt["outcome_batch_sha256"],
            "outcome_source_id": rebuilt["outcome_source_id"],
            "source_revision_id": rebuilt["source_revision_id"],
            "outcome_input_manifest_sha256": rebuilt["outcome_input_manifest_sha256"],
            "event_count": rebuilt["event_count"],
            "ordered_entries_sha256": rebuilt["ordered_entries_sha256"],
            "first_event": rebuilt["first_event"],
            "terminal_event": rebuilt["terminal_event"],
            "state_before_sha256": rebuilt["state_before_sha256"],
            "state_after_sha256": rebuilt["state_after_sha256"],
            "engineering_blind_time_order_candidate": rebuilt[
                "engineering_blind_time_order_candidate"
            ],
            "live_ledger_events_recorded": True,
            "contiguous_exact_slice_verified": True,
            "network_action_performed": False,
            "trusted_anchor_receipt_verified": False,
            "e2_live_evidence_eligible": False,
            "formal_warning_output": False,
        },
    )


def _outstanding_outcome_consumption_output(
    plan: OutcomeConsumptionPlan,
    committed: tuple[live_ledger.LedgerEvent, ...],
    current_events: Sequence[live_ledger.LedgerEvent],
) -> ActionOutput:
    """Purely verify and describe one already-stored canonical transaction."""

    if len(committed) != OUTCOME_CONSUMPTION_EVENT_COUNT or any(
        not _outcome_event_matches_spec(event, spec)
        for event, spec in zip(committed, plan.specs, strict=True)
    ):
        raise WorksetRecoveryIntegrityError(
            "Outcome consumption stored transaction changed"
        )
    start = plan.expected_pre_head.event_count
    stop = start + OUTCOME_CONSUMPTION_EVENT_COUNT
    if tuple(current_events[start:stop]) != committed:
        raise WorksetRecoveryIntegrityError(
            "Outcome consumption transaction disappeared"
        )
    frozen = plan.context.frozen
    try:
        post_projection = live._reconstruct_projection(  # noqa: SLF001
            current_events[:stop],
            frozen.profile,
            frozen.prerequisites,
        )
    except (live.LiveIntegrityError, live.LiveInputError) as exc:
        raise WorksetRecoveryIntegrityError(
            "Outcome consumption post-state no longer replays"
        ) from exc
    outcome = plan.context.outcome
    target_text = outcome.target_date.isoformat()
    terminal = committed[-1]
    settled = post_projection.settled_events.get(target_text)
    if (
        settled is None
        or settled.entry_sha256 != terminal.entry_sha256
        or post_projection.outstanding_target_date is not None
        or post_projection.last_finalized_date != outcome.target_date
        or post_projection.revision_ids.get(target_text, {}).get(
            outcome.source_revision_id
        )
        != outcome.sha256
        or terminal.payload.get("outcome_batch_sha256") != outcome.sha256
        or terminal.payload.get("outcome_source_id") != outcome.outcome_source_id
        or terminal.payload.get("source_revision_id") != outcome.source_revision_id
        or terminal.payload.get("anchor_confirmed") is not True
        or terminal.payload.get("trusted_anchor_receipt_verified") is not False
        or terminal.payload.get("e2_live_evidence_eligible") is not False
        or terminal.payload.get("formal_warning_output") is not False
    ):
        raise WorksetRecoveryIntegrityError(
            "Outcome consumption transaction did not close its exact lifecycle"
        )
    return ActionOutput(
        "live_outcome_consumption_transaction",
        None,
        {
            "schema_version": "ootang_live_outcome_consumption_action_output_v1",
            "writer_branch": plan.contract["writer_branch"],
            "live_epoch_id": plan.expected_pre_head.epoch_id,
            "target_date": target_text,
            "issue_id": plan.contract["issue_id"],
            "sealed_entry_sha256": plan.contract["sealed_entry_sha256"],
            "expected_pre_head": _pre_head_payload(plan.expected_pre_head),
            "tip_receipt_sha256": plan.context.tip_receipt.sha256,
            "exact_outcome_sha256": outcome.sha256,
            "outcome_source_manifest_sha256": (plan.context.source_manifest.sha256),
            "outcome_source_id": outcome.outcome_source_id,
            "source_revision_id": outcome.source_revision_id,
            "ledger_consumed_at_freeze": (plan.context.ledger_consumed_at_freeze),
            "event_count": len(committed),
            "event_specs_sha256": plan.contract["event_specs_sha256"],
            "ordered_event_keys_sha256": plan.contract["ordered_event_keys_sha256"],
            "ordered_entries_sha256": _sha256(
                _canonical_bytes([event.entry_sha256 for event in committed])
            ),
            "first_event": _settlement_event_record(committed[0]),
            "terminal_event": _settlement_event_record(terminal),
            "state_before_sha256": committed[0].state_before_sha256,
            "state_after_sha256": terminal.state_after_sha256,
            "engineering_blind_time_order_candidate": terminal.payload.get(
                "engineering_blind_time_order_candidate"
            ),
            "live_ledger_events_recorded": True,
            "canonical_frozen_writer_reused": True,
            "contiguous_exact_slice_verified": True,
            "network_action_performed": False,
            "trusted_anchor_receipt_verified": False,
            "e2_live_evidence_eligible": False,
            "formal_warning_output": False,
        },
    )


def _settled_revision_consumption_output(
    plan: OutcomeConsumptionPlan,
    committed: tuple[live_ledger.LedgerEvent, ...],
    current_events: Sequence[live_ledger.LedgerEvent],
) -> ActionOutput:
    if (
        plan.contract.get("schema_version")
        != OUTCOME_REVISION_CONSUMPTION_CONTRACT_SCHEMA
        or len(committed) != OUTCOME_SETTLED_REVISION_EVENT_COUNT
        or tuple(event.event_type for event in committed)
        != OUTCOME_SETTLED_REVISION_EVENT_TYPES
        or any(
            not _outcome_event_matches_spec(event, spec)
            for event, spec in zip(committed, plan.specs, strict=True)
        )
    ):
        raise WorksetRecoveryIntegrityError(
            "Settled revision stored transaction changed"
        )
    start = plan.expected_pre_head.event_count
    stop = start + OUTCOME_SETTLED_REVISION_EVENT_COUNT
    if tuple(current_events[start:stop]) != committed:
        raise WorksetRecoveryIntegrityError("Settled revision transaction disappeared")
    frozen = plan.context.frozen
    try:
        prefix_projection = live._reconstruct_projection(  # noqa: SLF001
            current_events[:start], frozen.profile, frozen.prerequisites
        )
        post_projection = live._reconstruct_projection(  # noqa: SLF001
            current_events[:stop], frozen.profile, frozen.prerequisites
        )
    except (live.LiveIntegrityError, live.LiveInputError) as exc:
        raise WorksetRecoveryIntegrityError(
            "Settled revision post-state no longer replays"
        ) from exc
    outcome = plan.context.outcome
    target_text = outcome.target_date.isoformat()
    settlement_before = prefix_projection.settled_events.get(target_text)
    settlement_after = post_projection.settled_events.get(target_text)
    stations = frozen.profile["stations"]
    online_states_sha256 = live._states_sha256(  # noqa: SLF001
        post_projection.states, stations
    )
    expected_outstanding = plan.contract["outstanding_target_date"]
    actual_outstanding = (
        post_projection.outstanding_target_date.isoformat()
        if post_projection.outstanding_target_date is not None
        else None
    )
    revision_events = tuple(
        event for event in committed if event.event_type == "outcome_revision"
    )
    rescore_events = tuple(
        event for event in committed if event.event_type == "revision_rescore_recorded"
    )
    if (
        settlement_before is None
        or settlement_after is None
        or settlement_before.entry_sha256
        != plan.contract["original_settlement_entry_sha256"]
        or settlement_after.entry_sha256 != settlement_before.entry_sha256
        or prefix_projection.states != post_projection.states
        or online_states_sha256 != plan.contract["online_states_sha256"]
        or post_projection.last_finalized_date.isoformat()
        != plan.contract["last_finalized_date"]
        or prefix_projection.last_finalized_date != post_projection.last_finalized_date
        or actual_outstanding != expected_outstanding
        or prefix_projection.outstanding_target_date
        != post_projection.outstanding_target_date
        or post_projection.revision_ids.get(target_text, {}).get(
            outcome.source_revision_id
        )
        != outcome.sha256
        or dict(post_projection.latest_actuals_by_date.get(target_text, {}))
        != outcome.actual_by_station
        or len(revision_events) != len(stations)
        or len(rescore_events) != len(stations)
        or committed[0].payload.get("outcome_batch_sha256") != outcome.sha256
        or committed[0].payload.get("source_revision_id") != outcome.source_revision_id
        or committed[-1].payload.get("source_revision_id") != outcome.source_revision_id
        or any(
            event.state_before_sha256 != event.state_after_sha256 for event in committed
        )
        or any(
            event.payload.get("live_online_state_rewritten") is not False
            for event in revision_events
        )
        or any(
            event.payload.get("updates_live_state") is not False
            or event.payload.get("blind_metric_eligible") is not False
            or event.payload.get("view") != "revised_retrospective_view"
            for event in rescore_events
        )
    ):
        raise WorksetRecoveryIntegrityError(
            "Settled revision transaction changed online or retrospective authority"
        )
    return ActionOutput(
        "live_outcome_consumption_transaction",
        None,
        {
            "schema_version": (
                "ootang_live_settled_revision_consumption_action_output_v1"
            ),
            "writer_branch": plan.contract["writer_branch"],
            "live_epoch_id": plan.expected_pre_head.epoch_id,
            "target_date": target_text,
            "original_settlement_entry_sha256": plan.contract[
                "original_settlement_entry_sha256"
            ],
            "issue_id": plan.contract["issue_id"],
            "sealed_entry_sha256": plan.contract["sealed_entry_sha256"],
            "previous_revision_id": plan.context.previous_revision_id,
            "previous_outcome_sha256": plan.context.previous_outcome_sha256,
            "tip_receipt_sha256": plan.context.tip_receipt.sha256,
            "exact_outcome_sha256": outcome.sha256,
            "outcome_source_manifest_sha256": plan.context.source_manifest.sha256,
            "outcome_source_id": outcome.outcome_source_id,
            "source_revision_id": outcome.source_revision_id,
            "ledger_consumed_at_freeze": plan.context.ledger_consumed_at_freeze,
            "expected_pre_head": _pre_head_payload(plan.expected_pre_head),
            "online_states_sha256": online_states_sha256,
            "last_finalized_date": plan.contract["last_finalized_date"],
            "outstanding_target_date": expected_outstanding,
            "event_count": len(committed),
            "event_specs_sha256": plan.contract["event_specs_sha256"],
            "ordered_event_keys_sha256": plan.contract["ordered_event_keys_sha256"],
            "ordered_entries_sha256": _sha256(
                _canonical_bytes([event.entry_sha256 for event in committed])
            ),
            "first_event": _settlement_event_record(committed[0]),
            "terminal_event": _settlement_event_record(committed[-1]),
            "revised_retrospective_view": True,
            "live_online_state_rewritten": False,
            "blind_metric_eligible": False,
            "live_ledger_events_recorded": True,
            "canonical_frozen_writer_reused": True,
            "contiguous_exact_slice_verified": True,
            "network_action_performed": False,
            "trusted_anchor_receipt_verified": False,
            "e2_live_evidence_eligible": False,
            "formal_warning_output": False,
        },
    )


def _outcome_consumption_output(
    plan: OutcomeConsumptionPlan,
    committed: tuple[live_ledger.LedgerEvent, ...],
    current_events: Sequence[live_ledger.LedgerEvent],
) -> ActionOutput:
    schema = plan.contract.get("schema_version")
    if schema == OUTCOME_CONSUMPTION_CONTRACT_SCHEMA:
        return _outstanding_outcome_consumption_output(plan, committed, current_events)
    if schema == OUTCOME_REVISION_CONSUMPTION_CONTRACT_SCHEMA:
        return _settled_revision_consumption_output(plan, committed, current_events)
    raise WorksetRecoveryIntegrityError(
        "Outcome consumption output contract schema changed"
    )


def _outcome_consumption_action(
    item: Mapping[str, Any],
    reservation: Reservation,
    contract: Mapping[str, Any] | None,
    previous_step_receipt: Mapping[str, Any] | None = None,
) -> ActionOutput:
    """Append or exactly adopt one manifest-bound canonical outcome batch."""

    if contract is None:
        raise WorksetRecoveryIntegrityError(
            "Outcome consumption intent lost its contract"
        )
    if _outcome_consumption_transaction_committed(reservation, contract):
        recorded_plan = _recorded_outcome_consumption_plan(
            item, reservation, contract, previous_step_receipt
        )
        return _outcome_consumption_output(
            recorded_plan,
            recorded_plan.stored_events,
            recorded_plan.context.frozen.current_events,
        )
    plan = _outcome_consumption_plan(item, reservation, previous_step_receipt)
    if contract != plan.contract:
        raise WorksetRecoveryIntegrityError(
            "Outcome consumption intent contract changed"
        )
    try:
        ledger = live_ledger.AppendOnlyLedger(
            plan.context.frozen.paths.ledger,
            timeout_seconds=LIVE_LEDGER_CAS_TIMEOUT_SECONDS,
        )
        appended = live_cas.append_transaction_at_pre_head_v1(
            ledger,
            expected_pre_head=plan.expected_pre_head,
            specs=plan.specs,
        )
    except live_cas.LiveLedgerCasBusyErrorV1 as exc:
        raise WorksetRecoveryBusyError(str(exc)) from exc
    except live_ledger.LedgerError as exc:
        if _is_sqlite_busy(exc):
            raise WorksetRecoveryBusyError(
                "Live ledger outcome CAS write lock is busy"
            ) from exc
        raise WorksetRecoveryIntegrityError(
            f"Outcome consumption CAS failed:{type(exc).__name__}:{exc}"
        ) from exc
    committed = appended.events
    verified = _frozen_live_prefix(reservation)
    return _outcome_consumption_output(
        plan,
        committed,
        verified.current_events,
    )


def _immutable_materializer_receipt_chain(
    *,
    target: date,
    profile: Mapping[str, Any],
    reservation: Reservation,
    frozen: FrozenLivePrefix,
) -> tuple[outcomes._RegisteredOutcome, ...]:
    """Replay the linear immutable chain without consulting its mutable pointer."""

    receipt_root = outcomes._runtime_path(  # noqa: SLF001
        profile, reservation.paths.active_root, "outcome_receipts"
    )
    directory = receipt_root / target.isoformat()
    if not directory.exists():
        return ()
    if not directory.is_dir():
        raise WorksetRecoveryIntegrityError(
            "Outcome materialization receipt registry changed type"
        )
    try:
        paths = sorted(
            path.resolve()
            for path in directory.iterdir()
            if path.name != "active.json" and not path.name.startswith(".")
        )
    except OSError as exc:
        raise WorksetRecoveryIntegrityError(
            "Outcome materialization receipt registry cannot be enumerated"
        ) from exc
    if any(
        not path.is_file()
        or path.suffix != ".json"
        or len(path.stem) != 64
        or any(character not in "0123456789abcdef" for character in path.stem)
        for path in paths
    ):
        raise WorksetRecoveryIntegrityError(
            "Outcome materialization receipt registry contains an unexpected entry"
        )
    active_path = outcomes._outcome_inbox_path(  # noqa: SLF001
        profile, reservation.paths.active_root, target
    )
    try:
        registered = tuple(
            outcomes._load_registered_outcome(  # noqa: SLF001
                receipt_path=path,
                active_path=active_path,
                object_root=outcomes._runtime_path(  # noqa: SLF001
                    profile, reservation.paths.active_root, "objects"
                ),
                receipt_root=receipt_root,
                profile=profile,
                root=reservation.paths.active_root,
                live_module=live,
                live_profile=frozen.profile,
                prerequisites=frozen.prerequisites,
            )
            for path in paths
        )
    except outcomes.OutcomeMaterializerError as exc:
        raise WorksetRecoveryIntegrityError(
            f"Immutable outcome receipt replay failed:{type(exc).__name__}:{exc}"
        ) from exc
    if not registered:
        return ()
    ordered = tuple(sorted(registered, key=lambda value: value.revision_sequence_id))
    if ordered[0].previous_receipt is not None or [
        value.revision_sequence_id for value in ordered
    ] != list(range(1, len(ordered) + 1)):
        raise WorksetRecoveryIntegrityError(
            "Immutable outcome receipts do not have one contiguous root"
        )
    for previous, current in zip(ordered, ordered[1:]):
        if (
            current.previous_receipt is None
            or current.previous_receipt.path != previous.receipt.path
            or current.previous_receipt.sha256 != previous.receipt.sha256
        ):
            raise WorksetRecoveryIntegrityError(
                "Immutable outcome receipts do not form one linear history"
            )
    return ordered


def _recorded_outcome_materialization_output(
    item: Mapping[str, Any],
    reservation: Reservation,
    contract: Mapping[str, Any],
) -> ActionOutput:
    checked, input_manifest, exact_object, receipt = (
        _outcome_materialization_contract_record(item, reservation, contract)
    )
    frozen = _frozen_live_prefix(reservation)
    profile = outcomes.load_config()
    try:
        registered = outcomes._load_registered_outcome(  # noqa: SLF001
            receipt_path=receipt.path,
            active_path=outcomes._outcome_inbox_path(  # noqa: SLF001
                profile,
                reservation.paths.active_root,
                date.fromisoformat(checked["target_date"]),
            ),
            object_root=outcomes._runtime_path(  # noqa: SLF001
                profile, reservation.paths.active_root, "objects"
            ),
            receipt_root=outcomes._runtime_path(  # noqa: SLF001
                profile, reservation.paths.active_root, "outcome_receipts"
            ),
            profile=profile,
            root=reservation.paths.active_root,
            live_module=live,
            live_profile=frozen.profile,
            prerequisites=frozen.prerequisites,
        )
        materialized_manifest = outcomes._artifact_from_mapping(  # noqa: SLF001
            registered.payload["source_manifest"],
            name="recorded materialization input manifest",
        )
        manifest_payload = outcomes._validate_input_manifest(  # noqa: SLF001
            materialized_manifest,
            profile=profile,
            root=reservation.paths.active_root,
        )
    except outcomes.OutcomeMaterializerError as exc:
        raise WorksetRecoveryIntegrityError(
            f"Recorded outcome materialization replay failed:{type(exc).__name__}:{exc}"
        ) from exc
    if (
        registered.receipt != receipt
        or registered.exact_object != exact_object
        or materialized_manifest != input_manifest
        or registered.payload != checked["outcome_payload"]
        or manifest_payload != checked["input_manifest_payload"]
        or registered.revision_sequence_id != checked["revision_sequence_id"]
        or (
            _materializer_artifact_payload(registered.previous_receipt)
            if registered.previous_receipt is not None
            else None
        )
        != checked["previous_receipt"]
    ):
        raise WorksetRecoveryIntegrityError(
            "Recorded outcome materialization immutable binding changed"
        )
    return ActionOutput(
        "outcome_materializer_publication",
        _active_artifact_reference(exact_object, reservation.paths.active_root),
        {
            "writer_branch": checked["writer_branch"],
            "selection_kind": checked["selection_kind"],
            "target_date": checked["target_date"],
            "outcome_source_id": checked["outcome_source_id"],
            "source_revision_id": checked["source_revision_id"],
            "source_snapshot_sequence_id": checked["source_snapshot_sequence_id"],
            "source_snapshot_receipt_sha256": checked["source_snapshot_receipt_sha256"],
            "input_manifest_sha256": input_manifest.sha256,
            "exact_outcome_sha256": exact_object.sha256,
            "receipt_sha256": receipt.sha256,
            "materializer_receipt": _active_artifact_reference(
                receipt, reservation.paths.active_root
            ),
            "input_manifest": _active_artifact_reference(
                input_manifest, reservation.paths.active_root
            ),
            "immutable_receipt_verified": True,
            "fully_published_verified": True,
            "canonical_materializer_writer_reused": True,
            "current_pointer_required_for_historical_replay": False,
            "live_ledger_event_count": 0,
            "live_ledger_mutation_performed": False,
            "network_action_performed": False,
        },
    )


def _outcome_materialization_action(
    item: Mapping[str, Any],
    reservation: Reservation,
    contract: Mapping[str, Any] | None,
    *,
    now: datetime,
) -> ActionOutput:
    """Publish or repair one exact manifest-bound materializer candidate."""

    if contract is None:
        raise WorksetRecoveryIntegrityError(
            "Outcome materialization intent lost its contract"
        )
    checked, expected_manifest, expected_exact, expected_receipt = (
        _outcome_materialization_contract_record(item, reservation, contract)
    )
    _utc_text(now)
    profile = outcomes.load_config()
    frozen = _frozen_live_prefix(reservation)
    target = date.fromisoformat(checked["target_date"])
    try:
        if expected_receipt.path.exists():
            immutable_chain = _immutable_materializer_receipt_chain(
                target=target,
                profile=profile,
                reservation=reservation,
                frozen=frozen,
            )
            matching = [
                (index, candidate)
                for index, candidate in enumerate(immutable_chain)
                if candidate.receipt == expected_receipt
                and candidate.exact_object == expected_exact
            ]
            if len(matching) != 1:
                raise WorksetRecoveryIntegrityError(
                    "Committed outcome materialization left its receipt chain"
                )
            if matching[0][0] == len(immutable_chain) - 1:
                chain = outcomes._scan_receipt_chain(  # noqa: SLF001
                    target=target,
                    profile=profile,
                    root=reservation.paths.active_root,
                    live_module=live,
                    live_profile=frozen.profile,
                    prerequisites=frozen.prerequisites,
                )
                if chain is None or chain.tip.receipt != expected_receipt:
                    raise WorksetRecoveryIntegrityError(
                        "Current outcome materialization tip changed"
                    )
                outcomes._reconcile_chain(  # noqa: SLF001
                    chain,
                    profile=profile,
                    root=reservation.paths.active_root,
                    now=now,
                    live_module=live,
                    live_profile=frozen.profile,
                    prerequisites=frozen.prerequisites,
                )
            return _recorded_outcome_materialization_output(item, reservation, contract)

        if checked["writer_branch"] != "machine_selected_source":
            raise WorksetRecoveryIntegrityError(
                "Registered outcome materialization receipt disappeared"
            )

        if checked["writer_branch"] == "machine_selected_source":
            source_record = checked["input_manifest_payload"]["source_record"]
            record = live_source.DailySourceRecord(
                day=target,
                revision_id=source_record["revision_id"],
                observed_at_utc=source_record["observed_at_utc"],
                available_at_utc=source_record["available_at_utc"],
                finalized_at_utc=source_record["finalized_at_utc"],
                rainfall_mm=source_record["rainfall_mm"],
                reservoir_water_level_m=source_record["reservoir_water_level_m"],
                displacement_mm=dict(source_record["displacement_mm"]),
            )
            outcomes._validate_finalization(  # noqa: SLF001
                record,
                source_exported_at_utc=checked["source_exported_at_utc"],
                now=now,
            )
            input_manifest = outcomes._materialize_input_manifest(  # noqa: SLF001
                profile,
                reservation.paths.active_root,
                checked["input_manifest_payload"],
            )
            if input_manifest != expected_manifest:
                raise WorksetRecoveryIntegrityError(
                    "Materialized outcome input manifest changed identity"
                )
            selection = outcomes._Selection(  # noqa: SLF001
                kind=checked["selection_kind"],
                target_date=target,
                record=record,
                previous_revision_id=checked["previous_revision_id"],
                previous_outcome_sha256=checked["previous_outcome_sha256"],
            )
            _, registered, _, _ = outcomes._publish_candidate(  # noqa: SLF001
                profile=profile,
                root=reservation.paths.active_root,
                selection=selection,
                source_exported_at_utc=checked["source_exported_at_utc"],
                input_manifest=input_manifest,
                payload=dict(checked["outcome_payload"]),
                clock=lambda: now,
                live_module=live,
                live_profile=frozen.profile,
                prerequisites=frozen.prerequisites,
            )
        chain = outcomes._scan_receipt_chain(  # noqa: SLF001
            target=target,
            profile=profile,
            root=reservation.paths.active_root,
            live_module=live,
            live_profile=frozen.profile,
            prerequisites=frozen.prerequisites,
        )
        active_path = outcomes._outcome_inbox_path(  # noqa: SLF001
            profile, reservation.paths.active_root, target
        )
        active_raw = (
            outcomes._legal_active_bytes(chain, active_path)  # noqa: SLF001
            if chain is not None
            else None
        )
    except WorksetRecoveryError:
        raise
    except outcomes.OutcomeMaterializerError as exc:
        raise WorksetRecoveryIntegrityError(
            f"Outcome materialization failed:{type(exc).__name__}:{exc}"
        ) from exc
    if (
        registered.receipt != expected_receipt
        or registered.exact_object != expected_exact
        or chain is None
        or chain.tip.receipt.sha256 != expected_receipt.sha256
        or chain.pointed is None
        or chain.pointed.receipt.sha256 != expected_receipt.sha256
        or active_raw != registered.raw
    ):
        raise WorksetRecoveryIntegrityError(
            "Outcome materialization did not publish the exact contract tip"
        )
    return _recorded_outcome_materialization_output(item, reservation, contract)


def _action_contract(
    item: Mapping[str, Any],
    reservation: Reservation,
    action: str,
    *,
    previous_step_receipt: Mapping[str, Any] | None = None,
) -> dict[str, object] | None:
    if action == "anchor_request_recorded":
        return _anchor_request_contract(item, reservation, previous_step_receipt)
    if action == "anchor_result_recorded":
        return _anchor_result_request_contract(item, reservation, previous_step_receipt)
    if action == "outcome_materialized":
        return _outcome_materialization_contract(item, reservation)
    if action == "outcome_or_revision_consumed":
        return _outcome_consumption_contract(item, reservation, previous_step_receipt)
    if action == "outcome_batch_settled":
        return _outcome_settlement_adoption_contract(
            item, reservation, previous_step_receipt
        )
    return None


def _verify_item_intent_action_contract(
    item: Mapping[str, Any],
    reservation: Reservation,
    action: str,
    contract: object,
    *,
    previous_step_receipt: Mapping[str, Any] | None = None,
    recorded: bool = False,
) -> None:
    if action == "outcome_materialized":
        if not isinstance(contract, Mapping):
            raise WorksetRecoveryIntegrityError(
                "Outcome materialization item intent contract changed"
            )
        _outcome_materialization_contract_record(item, reservation, contract)
        return
    if action == "outcome_or_revision_consumed":
        if not isinstance(contract, Mapping):
            raise WorksetRecoveryIntegrityError(
                "Outcome consumption item intent contract changed"
            )
        transaction_committed = recorded or _outcome_consumption_transaction_committed(
            reservation, contract
        )
        rebuilt = (
            _recorded_outcome_consumption_plan(
                item,
                reservation,
                contract,
                previous_step_receipt,
            ).contract
            if transaction_committed
            else _outcome_consumption_contract(item, reservation, previous_step_receipt)
        )
        if contract != rebuilt:
            raise WorksetRecoveryIntegrityError(
                "Outcome consumption item intent contract changed"
            )
        return
    if action == "outcome_batch_settled":
        if not isinstance(contract, Mapping) or contract != (
            _outcome_settlement_adoption_contract(
                item, reservation, previous_step_receipt
            )
        ):
            raise WorksetRecoveryIntegrityError(
                "Outcome settlement item intent contract changed"
            )
        return
    if action == "anchor_result_recorded":
        if not isinstance(contract, Mapping):
            raise WorksetRecoveryIntegrityError(
                "Anchor result item intent lost its request contract"
            )
        _verify_anchor_result_request_contract(
            item,
            reservation,
            contract,
            previous_step_receipt=previous_step_receipt,
        )
        return
    if contract != _action_contract(
        item,
        reservation,
        action,
        previous_step_receipt=previous_step_receipt,
    ):
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


def _anchor_result_plan_previous_receipt(
    plan: AnchorResultDispatchPlan,
    intent_payload: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    reference = intent_payload.get("previous_step_receipt")
    if reference is None:
        return None
    checked = _exact(
        reference,
        {"path", "sha256", "size_bytes"},
        name="anchor result previous step receipt reference",
    )
    try:
        path = registry._contained(  # noqa: SLF001
            plan.paths.root,
            checked["path"],
            name="anchor result previous step receipt",
        )
    except registry.EpochRegistryError as exc:
        raise WorksetRecoveryIntegrityError(str(exc)) from exc
    if path.parent != plan.paths.receipts or path.suffix != ".json":
        raise WorksetRecoveryIntegrityError(
            "Anchor result previous step receipt path changed"
        )
    payload, snapshot = _strict_json(path, name="anchor result previous step receipt")
    if _reference(snapshot, plan.paths.root) != checked:
        raise WorksetRecoveryIntegrityError(
            "Anchor result previous step receipt reference changed"
        )
    return payload


def _verify_anchor_result_dispatch_plan(
    plan: AnchorResultDispatchPlan,
) -> Mapping[str, Any] | None:
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
    return _anchor_result_plan_previous_receipt(plan, payload)


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
    adopted_object = _publish(
        object_snapshot.path,
        object_snapshot.raw,
        root=plan.paths.root,
        name="anchor result response object forward adoption",
    )
    if adopted_object != object_snapshot:
        raise WorksetRecoveryIntegrityError(
            "Anchor result response object changed during forward adoption"
        )
    link_path = plan.paths.anchor_result_response_links / f"{plan.step_id}.json"
    expected_link = _anchor_result_link_payload(plan, observation, object_snapshot)
    link_snapshot = _publish(
        link_path,
        _canonical_bytes(expected_link),
        root=plan.paths.root,
        name="anchor result response link",
    )
    return observation, object_snapshot, link_snapshot, "forward_adopted"


def _durably_fence_anchor_result_link(
    evidence: AnchorResultObservationEvidence,
) -> bool:
    """Confirm the exact response link after its publisher releases the lock."""

    if evidence.link_snapshot is None:
        raise WorksetRecoveryIntegrityError(
            "Anchor result durability fence lost its response link"
        )
    handle: BinaryIO | None = None
    try:
        try:
            handle = drain._acquire_lock(  # noqa: SLF001
                evidence.plan.paths.anchor_result_dispatch_lock,
                label="anchor result link durability",
            )
        except drain.EpochDrainBusyError:
            return False
        except drain.EpochDrainError as exc:
            raise WorksetRecoveryIntegrityError(str(exc)) from exc

        _verify_anchor_result_dispatch_plan(evidence.plan)
        inspected = _inspect_anchor_result_observation(evidence.plan)
        if inspected is None or inspected[2] is None:
            raise WorksetRecoveryIntegrityError(
                "Anchor result response link disappeared before its durability fence"
            )
        observation, object_snapshot, link_snapshot = inspected
        if (
            observation != evidence.observation
            or object_snapshot != evidence.object_snapshot
            or link_snapshot != evidence.link_snapshot
        ):
            raise WorksetRecoveryIntegrityError(
                "Anchor result response evidence changed before its durability fence"
            )
        adopted_object = _publish(
            object_snapshot.path,
            object_snapshot.raw,
            root=evidence.plan.paths.root,
            name="anchor result response object durability fence",
        )
        if adopted_object != object_snapshot:
            raise WorksetRecoveryIntegrityError(
                "Anchor result response object changed during its durability fence"
            )
        adopted_link = _publish(
            link_snapshot.path,
            link_snapshot.raw,
            root=evidence.plan.paths.root,
            name="anchor result response link durability fence",
        )
        if adopted_link != link_snapshot:
            raise WorksetRecoveryIntegrityError(
                "Anchor result response link changed during its durability fence"
            )
        return True
    finally:
        if handle is not None:
            try:
                drain._release_locks([handle])  # noqa: SLF001
            except drain.EpochDrainError as exc:
                raise WorksetRecoveryIntegrityError(str(exc)) from exc


def _deep_verify_locked_anchor_result_observations(
    profile: Mapping[str, Any],
    paths: RecoveryPaths,
    reservation: Reservation,
    items: Mapping[str, Mapping[str, Any]],
    intents: Mapping[str, Path],
    receipts: Mapping[str, tuple[dict[str, Any], registry.ArtifactSnapshot]],
    observation_steps: set[str],
) -> dict[str, AnchorResultObservationEvidence]:
    if not observation_steps:
        return {}
    pending_steps = set(intents) - set(receipts)
    evidence_by_step: dict[str, AnchorResultObservationEvidence] = {}
    for step_id in sorted(observation_steps):
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
        plan = AnchorResultDispatchPlan(
            profile=profile,
            paths=paths,
            key_id=key_id,
            step_id=step_id,
            item_intent=intent_snapshot,
            action_contract=dict(contract),
        )
        previous_step_receipt = _verify_anchor_result_dispatch_plan(plan)
        inspected = _inspect_anchor_result_observation(plan)
        if inspected is None:
            raise WorksetRecoveryIntegrityError(
                "Anchor result response observation disappeared"
            )
        observation, object_snapshot, link_snapshot = inspected
        evidence = AnchorResultObservationEvidence(
            plan,
            observation,
            object_snapshot,
            link_snapshot,
        )
        if step_id in receipts:
            if link_snapshot is None:
                raise WorksetRecoveryIntegrityError(
                    "Recorded anchor result lost its response link"
                )
            _verify_anchor_result_evidence_position(
                item,
                reservation,
                evidence,
                result_required=True,
                previous_step_receipt=previous_step_receipt,
            )
        elif step_id in pending_steps:
            if link_snapshot is None:
                _verify_pending_anchor_result_head(
                    item,
                    reservation,
                    contract,
                    previous_step_receipt=previous_step_receipt,
                )
            else:
                _verify_anchor_result_evidence_position(
                    item,
                    reservation,
                    evidence,
                    result_required=False,
                    previous_step_receipt=previous_step_receipt,
                )
        else:
            raise WorksetRecoveryIntegrityError(
                "Anchor result response observation is not bound to its step"
            )
        evidence_by_step[step_id] = evidence
    return evidence_by_step


def _verify_anchor_result_evidence_position(
    item: Mapping[str, Any],
    reservation: Reservation,
    evidence: AnchorResultObservationEvidence,
    *,
    result_required: bool,
    previous_step_receipt: Mapping[str, Any] | None = None,
) -> None:
    if evidence.link_snapshot is None:
        raise WorksetRecoveryIntegrityError("Anchor result response link is missing")
    spec, expected = _anchor_result_event_spec(
        item,
        reservation,
        evidence.plan.action_contract,
        evidence.observation,
        previous_step_receipt,
    )
    frozen = _frozen_live_prefix(reservation)
    position = expected.event_count
    if len(frozen.current_events) == position:
        if result_required:
            raise WorksetRecoveryIntegrityError(
                "Recorded anchor result ledger event disappeared"
            )
        head = frozen.current_events[-1]
        if (
            head.sequence_id != expected.sequence_id
            or head.entry_sha256 != expected.entry_sha256
        ):
            raise WorksetRecoveryIntegrityError(
                "Pending anchor result pre-head changed"
            )
        return
    if len(frozen.current_events) <= position:
        raise WorksetRecoveryIntegrityError("Anchor result ledger position disappeared")
    _anchor_result_output(
        evidence.plan.action_contract,
        evidence.observation,
        evidence.object_snapshot,
        evidence.link_snapshot,
        frozen.current_events[position],
        spec,
        expected,
        recovery_root=evidence.plan.paths.root,
    )


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
                status = "waiting_for_locked_anchor_result_consumption"
                reason = (
                    "one exact anchor response observation already exists; "
                    "the next coordinator poll will consume it under the four locks"
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


def _anchor_result_output(
    contract: Mapping[str, Any],
    observation: Mapping[str, Any],
    object_snapshot: registry.ArtifactSnapshot,
    link_snapshot: registry.ArtifactSnapshot,
    event: live_ledger.LedgerEvent,
    spec: live_ledger.EventSpec,
    expected: live_cas.LiveLedgerPreHeadV1,
    *,
    recovery_root: Path,
) -> ActionOutput:
    if (
        event.sequence_id != expected.sequence_id + 1
        or event.previous_entry_sha256 != expected.entry_sha256
        or not _event_matches_spec(event, spec)
    ):
        raise WorksetRecoveryIntegrityError(
            "Anchor result ledger event changed at its frozen position"
        )
    outcome = observation.get("outcome")
    selected_next = {
        "candidate_confirmed": "outcome_batch_settled",
        "deterministic_failure": "anchor_request_recorded",
    }.get(outcome)
    if selected_next is None:
        raise WorksetRecoveryIntegrityError("Anchor result branch changed")
    failure = observation.get("failure")
    failure_stage = failure.get("stage") if isinstance(failure, Mapping) else None
    failure_code = failure.get("code") if isinstance(failure, Mapping) else None
    request = contract.get("request_body")
    request_event = contract.get("request_event")
    if not isinstance(request, Mapping) or not isinstance(request_event, Mapping):
        raise WorksetRecoveryIntegrityError("Anchor result request identity changed")
    return ActionOutput(
        "live_anchor_result_event",
        None,
        {
            "schema_version": "ootang_live_anchor_result_action_output_v1",
            "live_epoch_id": expected.epoch_id,
            "result_outcome": outcome,
            "event_key": event.event_key,
            "event_type": event.event_type,
            "sequence_id": event.sequence_id,
            "previous_entry_sha256": event.previous_entry_sha256,
            "entry_sha256": event.entry_sha256,
            "request_event_entry_sha256": request_event.get("entry_sha256"),
            "sealed_entry_sha256": request.get("sealed_entry_sha256"),
            "attempt": request.get("attempt"),
            "idempotency_key": contract.get("idempotency_key"),
            "event_spec_sha256": _sha256(_canonical_bytes(_event_spec_payload(spec))),
            "response_observation": _reference(object_snapshot, recovery_root),
            "response_observation_link": _reference(link_snapshot, recovery_root),
            "failure_stage": failure_stage,
            "failure_code": failure_code,
            "selected_next_action": selected_next,
            "live_ledger_event_recorded": True,
            "network_action_performed": False,
            "external_response_network_action_performed": True,
            "remote_exactly_once": False,
            "trusted_anchor_receipt_verified": False,
            "e2_live_evidence_eligible": False,
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
    previous_step_receipt: Mapping[str, Any] | None = None,
) -> ActionOutput:
    if action_contract is None:
        raise WorksetRecoveryIntegrityError("Anchor request intent lost its contract")
    rebuilt = _anchor_request_contract(item, reservation, previous_step_receipt)
    if action_contract != rebuilt:
        raise WorksetRecoveryIntegrityError("Anchor request intent contract changed")
    spec, expected = _event_spec_from_contract(action_contract)
    frozen = _frozen_live_prefix(reservation)
    if previous_step_receipt is None and frozen.expected_pre_head != expected:
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


def _anchor_result_action(
    item: Mapping[str, Any],
    reservation: Reservation,
    evidence: AnchorResultObservationEvidence,
) -> ActionOutput:
    if evidence.link_snapshot is None:
        raise WorksetRecoveryIntegrityError(
            "Anchor result cannot consume an unlinked response observation"
        )
    previous_step_receipt = _verify_anchor_result_dispatch_plan(evidence.plan)
    spec, expected = _anchor_result_event_spec(
        item,
        reservation,
        evidence.plan.action_contract,
        evidence.observation,
        previous_step_receipt,
    )
    try:
        ledger = live_ledger.AppendOnlyLedger(
            _frozen_live_prefix(reservation).paths.ledger,
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
            f"Anchor result CAS failed:{type(exc).__name__}:{exc}"
        ) from exc
    verified = _frozen_live_prefix(reservation)
    position = expected.event_count
    if len(verified.current_events) <= position:
        raise WorksetRecoveryIntegrityError("Anchor result CAS event disappeared")
    return _anchor_result_output(
        evidence.plan.action_contract,
        evidence.observation,
        evidence.object_snapshot,
        evidence.link_snapshot,
        verified.current_events[position],
        spec,
        expected,
        recovery_root=evidence.plan.paths.root,
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
    action_contract: Mapping[str, object] | None = None,
    previous_step_receipt: Mapping[str, Any] | None = None,
    now: datetime,
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
            return _anchor_request_action(
                item,
                reservation,
                action_contract,
                previous_step_receipt,
            )
        if successor == "outcome_materialized":
            return _outcome_materialization_action(
                item,
                reservation,
                action_contract,
                now=now,
            )
        if successor == "outcome_or_revision_consumed":
            return _outcome_consumption_action(
                item,
                reservation,
                action_contract,
                previous_step_receipt,
            )
        if successor == "outcome_batch_settled":
            return _outcome_settlement_adoption_action(
                item,
                reservation,
                action_contract,
                previous_step_receipt,
            )
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
    profile: Mapping[str, Any] | None = None,
    previous_step_receipt: Mapping[str, Any] | None = None,
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
            item, reservation, previous_step_receipt
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
    elif action == "anchor_result_recorded":
        if reservation is None or item_intent is None or profile is None:
            raise WorksetRecoveryIntegrityError(
                "Recorded anchor result lost its frozen intent authority"
            )
        contract = item_intent.get("action_contract")
        step_id = payload.get("step_id")
        key_id = payload.get("key_id")
        if (
            not isinstance(contract, Mapping)
            or not isinstance(step_id, str)
            or not isinstance(key_id, str)
        ):
            raise WorksetRecoveryIntegrityError(
                "Recorded anchor result request contract changed"
            )
        _verify_anchor_result_request_contract(
            item,
            reservation,
            contract,
            previous_step_receipt=previous_step_receipt,
        )
        intent_path = paths.item_intents / f"{step_id}.json"
        _, intent_snapshot = _strict_json(
            intent_path, name="recorded anchor result item intent"
        )
        plan = AnchorResultDispatchPlan(
            profile=profile,
            paths=paths,
            key_id=key_id,
            step_id=step_id,
            item_intent=intent_snapshot,
            action_contract=dict(contract),
        )
        plan_previous_step_receipt = _verify_anchor_result_dispatch_plan(plan)
        if plan_previous_step_receipt != previous_step_receipt:
            raise WorksetRecoveryIntegrityError(
                "Recorded anchor result previous receipt changed"
            )
        inspected = _inspect_anchor_result_observation(plan)
        if inspected is None or inspected[2] is None:
            raise WorksetRecoveryIntegrityError(
                "Recorded anchor result lost its linked response observation"
            )
        observation, object_snapshot, link_snapshot = inspected
        spec, expected_pre_head = _anchor_result_event_spec(
            item,
            reservation,
            contract,
            observation,
            previous_step_receipt,
        )
        frozen = _frozen_live_prefix(reservation)
        position = expected_pre_head.event_count
        if len(frozen.current_events) <= position:
            raise WorksetRecoveryIntegrityError(
                "Recorded anchor result event disappeared"
            )
        expected = _anchor_result_output(
            contract,
            observation,
            object_snapshot,
            link_snapshot,
            frozen.current_events[position],
            spec,
            expected_pre_head,
            recovery_root=paths.root,
        )
    elif action == "outcome_materialized":
        if reservation is None or item_intent is None:
            raise WorksetRecoveryIntegrityError(
                "Recorded outcome materialization lost its frozen intent authority"
            )
        contract = item_intent.get("action_contract")
        if not isinstance(contract, Mapping):
            raise WorksetRecoveryIntegrityError(
                "Recorded outcome materialization contract changed"
            )
        expected = _recorded_outcome_materialization_output(
            item,
            reservation,
            contract,
        )
    elif action == "outcome_or_revision_consumed":
        if reservation is None or item_intent is None:
            raise WorksetRecoveryIntegrityError(
                "Recorded outcome consumption lost its frozen intent authority"
            )
        contract = item_intent.get("action_contract")
        if not isinstance(contract, Mapping):
            raise WorksetRecoveryIntegrityError(
                "Recorded outcome consumption contract changed"
            )
        recorded_plan = _recorded_outcome_consumption_plan(
            item,
            reservation,
            contract,
            previous_step_receipt,
        )
        expected = _outcome_consumption_output(
            recorded_plan,
            recorded_plan.stored_events,
            recorded_plan.context.frozen.current_events,
        )
    elif action == "outcome_batch_settled":
        if reservation is None or item_intent is None:
            raise WorksetRecoveryIntegrityError(
                "Recorded outcome settlement lost its frozen intent authority"
            )
        contract = item_intent.get("action_contract")
        if not isinstance(contract, Mapping):
            raise WorksetRecoveryIntegrityError(
                "Recorded outcome settlement contract changed"
            )
        expected = _outcome_settlement_adoption_action(
            item,
            reservation,
            contract,
            previous_step_receipt,
        )
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


def _branch_selected_next_actions(
    plan: Mapping[str, Any],
    transition_action: str,
    semantics: object,
) -> list[str]:
    edges = _plan_edges(plan)
    allowed = edges.get(transition_action)
    if allowed is None:
        raise WorksetRecoveryIntegrityError("Transition action is outside its plan")
    if transition_action != "anchor_result_recorded":
        return allowed
    if not isinstance(semantics, Mapping):
        raise WorksetRecoveryIntegrityError("Anchor result semantics changed type")
    selected = {
        "candidate_confirmed": "outcome_batch_settled",
        "deterministic_failure": "anchor_request_recorded",
    }.get(semantics.get("result_outcome"))
    if (
        selected is None
        or semantics.get("selected_next_action") != selected
        or selected not in allowed
    ):
        raise WorksetRecoveryIntegrityError("Anchor result branch selection changed")
    return [selected]


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
    next_actions = _branch_selected_next_actions(
        plan, transition_action, action.semantics
    )
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
            expected_next = (
                _branch_selected_next_actions(
                    plan, action, payload.get("action_semantics")
                )
                if isinstance(action, str) and action in edges
                else None
            )
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
            previous_step_receipt=(previous[1] if previous is not None else None),
            recorded=step_index < len(rows),
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
            previous_payload = None
            if payload["step_index"] > 0:
                previous_payload = next(
                    candidate_payload
                    for _, candidate_payload, _ in groups[payload["key_id"]]
                    if candidate_payload["step_index"] == payload["step_index"] - 1
                )
            _verify_recorded_action_contract(
                item,
                payload,
                paths,
                reservation=reservation,
                item_intent=intent_record[0],
                profile=profile,
                previous_step_receipt=previous_payload,
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
        anchor_result_evidence = _deep_verify_locked_anchor_result_observations(
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
                rows = chains.get(key_id, [])
                previous_step_payload = rows[-1][0] if rows else None
                evidence = anchor_result_evidence.get(pending_step_id)
                if evidence is not None and evidence.link_snapshot is not None:
                    item = item_by_id[key_id]
                    step_index = pending_payload.get("step_index")
                    if (
                        not isinstance(step_index, int)
                        or isinstance(step_index, bool)
                        or step_index != len(rows)
                    ):
                        raise WorksetRecoveryIntegrityError(
                            "Pending anchor result step position changed"
                        )
                    if not _durably_fence_anchor_result_link(evidence):
                        reason = (
                            "the exact anchor response link is still owned by its "
                            "publisher; a later machine poll will durably fence and "
                            "consume it"
                        )
                        _write_status(
                            profile,
                            paths,
                            now=now,
                            status="waiting_for_anchor_result_link_durability",
                            reason=reason,
                            key_id=key_id,
                            receipt=None,
                            event=None,
                        )
                        return RecoveryResult(
                            "waiting_for_anchor_result_link_durability",
                            reason,
                            paths.status,
                            key_id,
                        )
                    _cas_item_artifacts(item, reservation)
                    action = _anchor_result_action(item, reservation, evidence)
                    previous_step_receipt = rows[-1][1] if rows else None
                    receipt_payload, receipt_snapshot = _ensure_receipt(
                        profile,
                        paths,
                        reservation,
                        item,
                        pending_snapshot,
                        action,
                        step_index=step_index,
                        transition_action="anchor_result_recorded",
                        previous_step_receipt=previous_step_receipt,
                        now=now,
                    )
                    event, event_snapshot = _append_event(
                        profile,
                        paths,
                        receipt_payload["step_id"],
                        key_id,
                        receipt_snapshot,
                        events,
                        previous,
                        now=now,
                    )
                    reason = (
                        "one linked anchor response was committed through the "
                        "expected-pre-head result CAS"
                    )
                    _write_status(
                        profile,
                        paths,
                        now=now,
                        status="recovery_step_completed",
                        reason=reason,
                        key_id=key_id,
                        receipt=receipt_snapshot.path,
                        event=event_snapshot.path,
                    )
                    return RecoveryResult(
                        "recovery_step_completed",
                        reason,
                        paths.status,
                        key_id,
                        receipt_snapshot.path,
                        event_snapshot.path,
                    )
                if evidence is None:
                    _verify_pending_anchor_result_head(
                        item_by_id[key_id],
                        reservation,
                        pending_contract,
                        previous_step_payload,
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
                _item_adapter_supported(
                    item,
                    transition_action,
                    rows[-1][0] if rows else None,
                )
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
        item_rows = chains.get(item["key_id"], [])
        previous_step_payload = item_rows[-1][0] if item_rows else None
        materialized_consumption = (
            transition_action == "outcome_or_revision_consumed"
            and previous_step_payload is not None
            and previous_step_payload.get("action") == "outcome_materialized"
        )
        committed_outcome_contract: Mapping[str, Any] | None = None
        persisted_materialization_contract: Mapping[str, Any] | None = None
        if transition_action == "outcome_materialized":
            pending_step_id = _step_id(item["key_id"], step_index, transition_action)
            pending_path = intents.get(pending_step_id)
            if pending_path is not None:
                pending_payload, _ = _strict_json(
                    pending_path, name="pending outcome materialization item intent"
                )
                pending_contract = pending_payload.get("action_contract")
                if not isinstance(pending_contract, Mapping):
                    raise WorksetRecoveryIntegrityError(
                        "Pending outcome materialization intent lost its contract"
                    )
                _outcome_materialization_contract_record(
                    item, reservation, pending_contract
                )
                persisted_materialization_contract = pending_contract
        if transition_action == "outcome_or_revision_consumed":
            pending_step_id = _step_id(item["key_id"], step_index, transition_action)
            pending_path = intents.get(pending_step_id)
            if pending_path is not None:
                pending_payload, _ = _strict_json(
                    pending_path, name="pending outcome consumption item intent"
                )
                pending_contract = pending_payload.get("action_contract")
                if not isinstance(pending_contract, Mapping):
                    raise WorksetRecoveryIntegrityError(
                        "Pending outcome consumption intent lost its contract"
                    )
                if _outcome_consumption_transaction_committed(
                    reservation, pending_contract
                ):
                    committed_outcome_contract = pending_contract
        inputs = (
            set()
            if committed_outcome_contract is not None
            or persisted_materialization_contract is not None
            or materialized_consumption
            else _cas_item_artifacts(item, reservation)
        )
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
            action_contract = (
                dict(committed_outcome_contract or persisted_materialization_contract)
                if (
                    committed_outcome_contract is not None
                    or persisted_materialization_contract is not None
                )
                else _action_contract(
                    item,
                    reservation,
                    transition_action,
                    previous_step_receipt=previous_step_payload,
                )
            )
        except WorksetRecoveryExternalWait as exc:
            reason = str(exc)
            waiting_for_settlement = transition_action == "outcome_batch_settled"
            waiting_for_outcome_branch = (
                transition_action == "outcome_or_revision_consumed"
            )
            if waiting_for_settlement:
                status = "waiting_for_durable_outcome_settlement"
            elif waiting_for_outcome_branch:
                status = "waiting_for_supported_outcome_consumption_branch"
            else:
                status = "waiting_for_external_anchor_endpoint"
            _write_status(
                profile,
                paths,
                now=now,
                status=status,
                reason=reason,
                key_id=item["key_id"],
                receipt=None,
                event=None,
            )
            return RecoveryResult(
                status,
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
            _verify_pending_anchor_result_head(
                item,
                reservation,
                action_contract,
                previous_step_payload,
            )
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
            previous_step_receipt=previous_step_payload,
            now=now,
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
