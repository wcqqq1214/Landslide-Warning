"""Record one old-runtime drain blocker without recovering or transitioning it.

This additive R2b-2b foundation never adopts v1 transaction bytes and
deliberately implements no admission fence, complete workset enumeration, or
recovery action.  Its immutable event is only a context-bound observation of
the first pending family returned by the frozen v1 clean-state gate.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any, BinaryIO


ROOT = Path(__file__).resolve().parents[2]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_epoch_drain as drain  # noqa: E402
from monitoring import ootang_epoch_registry as registry  # noqa: E402


DEFAULT_CONFIG_PATH = ROOT / "config" / "ootang_epoch_drain.v2.json"
DEFAULT_CONFIG_SHA256 = (
    "aa12082e32b9b94fc4ad4b08232ed586b49c8c1bf1d2ccdaf3587b096c047d17"
)
MAX_CONTROL_BYTES = 4 * 1024 * 1024
ZERO_HASH = "0" * 64
LOCK_ORDER = ("manager", "cycle", "deploy", "runner", "replay", "shadow")
FAMILIES = (
    "issue_route_replay",
    "live_outstanding",
    "outcome_revision",
    "guard",
    "trusted_time",
    "shadow",
)
FALSE_CLAIMS = (
    "bounded_workset_reservation_implemented",
    "bounded_workset_recovery_implemented",
    "complete_workset_enumeration",
    "old_work_admission_fence_implemented",
    "epoch_drain_started_implemented",
    "canonical_old_issue_route_fence_implemented",
    "v1_v2_mutual_exclusion_implemented",
    "anti_rollback_authority_implemented",
    "lifecycle_authority",
    "transition_authority",
    "drained_eligibility_current",
    "old_epoch_drained",
    "old_epoch_drain_implemented",
    "activation_candidate_selected",
    "active_epoch_switch_implemented",
    "automatic_epoch_rotation_implemented",
    "trusted_anchor_receipt_verified",
    "e2_live_evidence_eligible",
    "real_activation_ready",
    "formal_warning_output",
)

EXPECTED_UPSTREAM = {
    "drain_v1_profile": {
        "path": "config/ootang_epoch_drain.v1.json",
        "expected_sha256": drain.DEFAULT_CONFIG_SHA256,
    },
    "drain_v1_implementation": {
        "path": "code/monitoring/ootang_epoch_drain.py",
        "expected_sha256": (
            "c4c226da087d354195fc3955ff60ff3b12af13f111d03cb59c5d64d0195a1602"
        ),
    },
    "eligibility_v1_profile": {
        "path": "config/ootang_epoch_drain_eligibility.v1.json",
        "expected_sha256": (
            "2dbda86a747ef486bc06d5c4901e3356404c0079ec2b8aecf91eb61afc91413a"
        ),
    },
    "eligibility_v1_implementation": {
        "path": "code/monitoring/ootang_epoch_drain_eligibility.py",
        "expected_sha256": (
            "46b036aa5e530d0dce50e87b6e4988d67eae1ba4ca67a2d4a09e990f4eaa29bc"
        ),
    },
}
EXPECTED_RUNTIME = {
    "root": "runtime/ootang_epoch_registry_v1",
    "namespace": "drain_v2",
    "manager_lock": "manager.lock",
    "events": "events",
    "observations": "observations/sha256",
    "head": "head.json",
    "status": "status.json",
    "active_root": "runtime/ootang_prequential_live_v1",
    "shadow_root": "runtime/ootang_prequential_calibration_shadow_v1",
    "cycle_lock": "prequential_cycle.lock",
    "deploy_lock": "deploy_cycle.lock",
    "runner_lock": "runner.lock",
    "replay_lock": "issue_replay.lock",
    "shadow_lock": "runner.lock",
    "issue_inbox": "issue_inbox",
}
EXPECTED_PROTOCOL = {
    "observation_schema_version": "ootang_epoch_drain_first_blocker_observation_v2",
    "event_schema_version": "ootang_epoch_drain_first_blocker_observed_event_v2",
    "head_schema_version": "ootang_epoch_drain_head_v2",
    "status_schema_version": "ootang_epoch_drain_status_v2",
    "event_type": "epoch_drain_first_blocker_observed",
    "maximum_items": 1,
    "maximum_manifest_bytes": MAX_CONTROL_BYTES,
    "initial_previous_entry_sha256": ZERO_HASH,
    "canonical_json": "utf8_sort_keys_compact_no_nan_trailing_lf",
    "lock_order": list(LOCK_ORDER),
    "workset_families": list(FAMILIES),
    "v1_authority_policy": "v1_precedence_observation_inert_never_adopt_or_rewrite",
    "clean_policy": "not_applicable_clean_use_v1_without_v2_authority",
    "observation_policy": "content_addressed_create_only_fixed_sort_and_natural_key",
    "observation_scope": "first_v1_clean_gate_blocker_only",
    "recovery_action": "not_implemented_foundation_only",
}
EXPECTED_CAPABILITIES = {
    "machine_only": True,
    "v1_nonreinterpretation_enforced": True,
    "first_blocker_observation_implemented": True,
    "observation_binds_r1_r2a_authority": True,
    "observation_binds_old_epoch_context": True,
    **{claim: False for claim in FALSE_CLAIMS},
}


class DrainV2Error(RuntimeError):
    """Base failure for the first-blocker observation foundation."""


class DrainV2ConfigError(DrainV2Error):
    """The reviewed v2 profile or a frozen upstream binding changed."""


class DrainV2IntegrityError(DrainV2Error):
    """A durable namespace or inspected workset failed closed."""


class DrainV2BusyError(DrainV2Error):
    """One of the six globally ordered writer locks is busy."""


@dataclass(frozen=True)
class DrainV2Paths:
    registry_root: Path
    root: Path
    manager_lock: Path
    events: Path
    observations: Path
    head: Path
    status: Path
    active_root: Path
    shadow_root: Path
    cycle_lock: Path
    deploy_lock: Path
    runner_lock: Path
    replay_lock: Path
    shadow_lock: Path
    issue_inbox: Path


@dataclass(frozen=True)
class WorksetItem:
    family: str
    natural_key: str
    observed_status: str
    reason: str


@dataclass(frozen=True)
class WorksetContext:
    registry_event_sequence_id: int
    registry_event_entry_sha256: str
    preparation_event_sequence_id: int
    preparation_event_entry_sha256: str
    candidate_id: str
    slot_id: str
    old_live_epoch_id: str
    live_event_count: int
    live_terminal_sha256: str


@dataclass(frozen=True)
class WorksetInspection:
    status: str
    reason: str
    items: tuple[WorksetItem, ...] = ()
    context: WorksetContext | None = None


@dataclass(frozen=True)
class DrainV2Result:
    status: str
    reason: str
    status_path: Path
    event_path: Path | None
    observation_path: Path | None
    lifecycle_authority: bool = False
    transition_authority: bool = False
    bounded_workset_recovery_implemented: bool = False
    first_blocker_observation_implemented: bool = True
    bounded_workset_reservation_implemented: bool = False
    complete_workset_enumeration: bool = False
    old_work_admission_fence_implemented: bool = False


InspectWorkset = Callable[[DrainV2Paths, datetime], WorksetInspection]


def _canonical_bytes(value: Mapping[str, object]) -> bytes:
    return registry._canonical_bytes(dict(value))  # noqa: SLF001


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _hash_text(value: object, *, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise DrainV2IntegrityError(f"{name} is not a lowercase SHA-256")
    return value


def _canonical_text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise DrainV2IntegrityError(f"{name} is not canonical text")
    return value


def _exact(value: object, keys: set[str], *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise DrainV2IntegrityError(f"{name} keys changed")
    return value


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DrainV2IntegrityError("Drain v2 machine clock must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def load_drain_v2_profile(
    path: Path = DEFAULT_CONFIG_PATH, *, project_root: Path = ROOT
) -> dict[str, Any]:
    """Load the one reviewed v2 profile and verify all frozen inputs."""

    root = project_root.resolve()
    resolved = path if path.is_absolute() else root / path
    resolved = registry._absolute_lexical(resolved)  # noqa: SLF001
    if root != ROOT.resolve() or resolved != DEFAULT_CONFIG_PATH.resolve():
        raise DrainV2ConfigError(
            "Only the reviewed default drain v2 profile is accepted"
        )
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            resolved, name="epoch drain v2 profile", maximum_bytes=MAX_CONTROL_BYTES
        )
        if snapshot.sha256 != DEFAULT_CONFIG_SHA256:
            raise DrainV2ConfigError("Reviewed drain v2 profile digest changed")
        profile = registry._decode_json(snapshot.raw, name="epoch drain v2 profile")  # noqa: SLF001
        _exact(
            profile,
            {
                "schema_version",
                "profile_id",
                "profile_version",
                "case",
                "artifact_status",
                "formal_warning_output",
                "independent_label_used",
                "confirmatory_external_validation",
                "vajont_used",
                "default_pipeline_member",
                "upstream",
                "runtime",
                "protocol",
                "engineering_capabilities",
            },
            name="epoch drain v2 profile",
        )
        identity = (
            profile["schema_version"],
            profile["profile_id"],
            profile["profile_version"],
            profile["case"],
            profile["artifact_status"],
        )
        if identity != (
            "ootang_epoch_drain_profile_v2",
            "ootang-epoch-drain-v2",
            "2.0.0-engineering-foundation",
            "ootang",
            "first_blocker_observation_engineering_only_not_reservation_recovery_or_transition_authority",
        ):
            raise DrainV2ConfigError("Drain v2 profile identity changed")
        for flag in (
            "formal_warning_output",
            "independent_label_used",
            "confirmatory_external_validation",
            "vajont_used",
            "default_pipeline_member",
        ):
            if profile[flag] is not False:
                raise DrainV2ConfigError(f"Drain v2 profile {flag} must remain false")
        if profile["upstream"] != EXPECTED_UPSTREAM:
            raise DrainV2ConfigError("Drain v2 upstream bindings changed")
        if profile["runtime"] != EXPECTED_RUNTIME:
            raise DrainV2ConfigError("Drain v2 runtime mapping changed")
        if profile["protocol"] != EXPECTED_PROTOCOL:
            raise DrainV2ConfigError("Drain v2 protocol changed")
        if profile["engineering_capabilities"] != EXPECTED_CAPABILITIES:
            raise DrainV2ConfigError("Drain v2 capability boundary changed")
        for binding in EXPECTED_UPSTREAM.values():
            bound = registry._contained(root, binding["path"], name="v2 upstream")  # noqa: SLF001
            captured = registry._read_regular(bound, name="v2 upstream")  # noqa: SLF001
            if captured.sha256 != binding["expected_sha256"]:
                raise DrainV2ConfigError(f"Frozen upstream changed:{binding['path']}")
    except registry.EpochRegistryError as exc:
        raise DrainV2ConfigError(str(exc)) from exc
    profile["_project_root"] = str(root)
    profile["_profile_sha256"] = snapshot.sha256
    return profile


def drain_v2_paths(
    profile: Mapping[str, Any],
    *,
    runtime_root: Path | None = None,
    active_runtime_root: Path | None = None,
    shadow_runtime_root: Path | None = None,
) -> DrainV2Paths:
    """Resolve the independent v2 namespace and the unchanged six locks."""

    project_root = Path(str(profile["_project_root"]))
    registry_root = (
        registry._contained(project_root, profile["runtime"]["root"], name="v2 root")  # noqa: SLF001
        if runtime_root is None
        else drain._override_root(runtime_root, name="runtime_root")  # noqa: SLF001
    )
    active_root = (
        registry._contained(
            project_root, profile["runtime"]["active_root"], name="active root"
        )  # noqa: SLF001
        if active_runtime_root is None
        else drain._override_root(active_runtime_root, name="active_runtime_root")  # noqa: SLF001
    )
    shadow_root = (
        registry._contained(
            project_root, profile["runtime"]["shadow_root"], name="shadow root"
        )  # noqa: SLF001
        if shadow_runtime_root is None
        else drain._override_root(shadow_runtime_root, name="shadow_runtime_root")  # noqa: SLF001
    )

    def child(base: Path, value: str, *, name: str) -> Path:
        try:
            return registry._contained(base, value, name=name)  # noqa: SLF001
        except registry.EpochRegistryError as exc:
            raise DrainV2ConfigError(str(exc)) from exc

    root = child(registry_root, profile["runtime"]["namespace"], name="v2 namespace")
    paths = DrainV2Paths(
        registry_root=registry_root,
        root=root,
        manager_lock=child(
            registry_root, profile["runtime"]["manager_lock"], name="manager lock"
        ),
        events=child(root, profile["runtime"]["events"], name="v2 events"),
        observations=child(
            root, profile["runtime"]["observations"], name="v2 observations"
        ),
        head=child(root, profile["runtime"]["head"], name="v2 head"),
        status=child(root, profile["runtime"]["status"], name="v2 status"),
        active_root=active_root,
        shadow_root=shadow_root,
        cycle_lock=child(
            active_root, profile["runtime"]["cycle_lock"], name="cycle lock"
        ),
        deploy_lock=child(
            active_root, profile["runtime"]["deploy_lock"], name="deploy lock"
        ),
        runner_lock=child(
            active_root, profile["runtime"]["runner_lock"], name="runner lock"
        ),
        replay_lock=child(
            active_root, profile["runtime"]["replay_lock"], name="replay lock"
        ),
        shadow_lock=child(
            shadow_root, profile["runtime"]["shadow_lock"], name="shadow lock"
        ),
        issue_inbox=child(
            active_root, profile["runtime"]["issue_inbox"], name="issue inbox"
        ),
    )
    locks = (
        paths.manager_lock,
        paths.cycle_lock,
        paths.deploy_lock,
        paths.runner_lock,
        paths.replay_lock,
        paths.shadow_lock,
    )
    if len(set(locks)) != len(LOCK_ORDER):
        raise DrainV2ConfigError("Globally ordered drain v2 locks collide")
    return paths


def _strict_entries(path: Path, *, name: str) -> tuple[Path, ...]:
    if not path.exists() and not path.is_symlink():
        return ()
    try:
        if not stat.S_ISDIR(os.lstat(path).st_mode):
            raise DrainV2IntegrityError(f"{name} is not a real directory")
        entries = tuple(sorted(path.iterdir(), key=lambda item: item.name))
        for entry in entries:
            mode = os.lstat(entry).st_mode
            if entry.name.startswith(".") or not stat.S_ISREG(mode):
                raise DrainV2IntegrityError(f"{name} contains an unknown entry")
        return entries
    except OSError as exc:
        raise DrainV2IntegrityError(f"Cannot inspect {name}") from exc


def _v1_authority_witnesses(paths: DrainV2Paths) -> tuple[str, ...]:
    """Detect authority bytes without parsing, adopting, or modifying them."""

    witnesses: list[str] = []
    v1 = (
        (paths.registry_root / "drain_fence_prepares", "fence_prepare"),
        (paths.registry_root / "drain_intents", "intent"),
        (paths.registry_root / "drain_exchange_attempts", "wal"),
        (paths.registry_root / "drain_events", "event"),
        (paths.registry_root / "drain_eligibility_events", "eligibility"),
    )
    for directory, label in v1:
        if _strict_entries(directory, name=f"v1 {label} authority"):
            witnesses.append(label)
    if _strict_entries(
        paths.registry_root / "drain_capsules" / "sha256",
        name="v1 drain capsule authority",
    ):
        witnesses.append("capsule")
    object_root = paths.registry_root / "objects" / "sha256"
    if object_root.exists() or object_root.is_symlink():
        try:
            if not stat.S_ISDIR(os.lstat(object_root).st_mode):
                raise DrainV2IntegrityError(
                    "Shared object store is not a real directory"
                )
            v1_suffixes = (
                ".epoch-drain-intent-prefix.json",
                ".epoch-drain-profile.json",
                ".epoch-drain-implementation.py",
                ".epoch-drain-boundary.json",
                ".epoch-drain-staged-feed.json",
            )
            for entry in sorted(object_root.iterdir(), key=lambda item: item.name):
                if not entry.name.endswith(v1_suffixes):
                    continue
                if entry.name.startswith(".") or not stat.S_ISREG(
                    os.lstat(entry).st_mode
                ):
                    raise DrainV2IntegrityError(
                        "V1 drain object witness is not one regular file"
                    )
                witnesses.append("object")
                break
        except OSError as exc:
            raise DrainV2IntegrityError("Cannot inspect shared object store") from exc
    marker = paths.issue_inbox / drain.ARMED_ATTEMPT_MARKER_NAME
    if marker.exists() or marker.is_symlink():
        try:
            if not stat.S_ISREG(os.lstat(marker).st_mode):
                raise DrainV2IntegrityError("v1 armed marker is not regular")
        except OSError as exc:
            raise DrainV2IntegrityError("Cannot inspect v1 armed marker") from exc
        witnesses.append("armed")
    return tuple(witnesses)


def _natural_key(family: str, status: str, reason: str) -> str:
    for token in reversed(reason.replace(":", " ").split()):
        try:
            target = date.fromisoformat(token)
        except ValueError:
            continue
        return f"{family}:{target.isoformat()}"
    singleton = {
        "issue_route_replay": "route",
        "live_outstanding": "ledger",
        "outcome_revision": "registry",
        "guard": "guard",
        "trusted_time": "trusted-time",
        "shadow": "shadow",
    }[family]
    return f"{family}:{singleton}:{_sha256(_canonical_bytes({'status': status, 'reason': reason}))}"


def _context_payload(context: WorksetContext | None) -> dict[str, object]:
    if not isinstance(context, WorksetContext):
        raise DrainV2IntegrityError("Dirty workset lacks its authority context")
    for name, value in (
        ("registry event sequence", context.registry_event_sequence_id),
        ("preparation event sequence", context.preparation_event_sequence_id),
        ("live event count", context.live_event_count),
    ):
        if type(value) is not int or value <= 0:
            raise DrainV2IntegrityError(f"{name} must be a positive integer")
    return {
        "registry_event_sequence_id": context.registry_event_sequence_id,
        "registry_event_entry_sha256": _hash_text(
            context.registry_event_entry_sha256,
            name="registry event entry SHA-256",
        ),
        "preparation_event_sequence_id": context.preparation_event_sequence_id,
        "preparation_event_entry_sha256": _hash_text(
            context.preparation_event_entry_sha256,
            name="preparation event entry SHA-256",
        ),
        "candidate_id": _canonical_text(context.candidate_id, name="candidate id"),
        "slot_id": _canonical_text(context.slot_id, name="slot id"),
        "old_live_epoch_id": _canonical_text(
            context.old_live_epoch_id, name="old live epoch id"
        ),
        "live_event_count": context.live_event_count,
        "live_terminal_sha256": _hash_text(
            context.live_terminal_sha256, name="live terminal SHA-256"
        ),
    }


def _context_from_payload(value: object) -> WorksetContext:
    payload = _exact(
        value,
        {
            "registry_event_sequence_id",
            "registry_event_entry_sha256",
            "preparation_event_sequence_id",
            "preparation_event_entry_sha256",
            "candidate_id",
            "slot_id",
            "old_live_epoch_id",
            "live_event_count",
            "live_terminal_sha256",
        },
        name="v2 workset authority context",
    )
    context = WorksetContext(**payload)
    if _context_payload(context) != payload:
        raise DrainV2IntegrityError("V2 workset authority context is not canonical")
    return context


def _default_inspection(
    paths: DrainV2Paths, machine_now: datetime
) -> WorksetInspection:
    from monitoring import ootang_prequential_live as live

    try:
        v1_profile = drain.load_drain_profile()
        v1_paths = drain.drain_paths(
            v1_profile,
            runtime_root=paths.registry_root,
            active_runtime_root=paths.active_root,
            shadow_runtime_root=paths.shadow_root,
        )
        authority = drain._load_authority(v1_profile, v1_paths)  # noqa: SLF001
        if isinstance(authority, drain._Waiting):  # noqa: SLF001
            return WorksetInspection(authority.status, authority.reason)
        assessed = drain._load_clean_state(  # noqa: SLF001
            v1_paths, machine_now=machine_now
        )
    except drain.EpochDrainError as exc:
        raise DrainV2IntegrityError(str(exc)) from exc
    if isinstance(assessed, drain._CleanState):  # noqa: SLF001
        return WorksetInspection(
            "not_applicable_clean_use_v1",
            "old runtime is clean; the reviewed v1 barrier remains the applicable path",
        )
    family_by_status = {
        "waiting_for_pending_issue_route": "issue_route_replay",
        "waiting_for_orphan_issue_receipt": "issue_route_replay",
        "waiting_for_outstanding_issue": "live_outstanding",
        "waiting_for_pending_outcome_or_revision": "outcome_revision",
        "waiting_for_pending_guard": "guard",
        "waiting_for_pending_trusted_time": "trusted_time",
        "waiting_for_pending_shadow": "shadow",
    }
    family = family_by_status.get(assessed.status)
    if family is None:
        known_non_workset = {
            "waiting_for_live_prerequisites",
            "waiting_for_source_ingest",
            "waiting_for_candidate_feed",
            "waiting_for_current_candidate_authority",
        }
        if assessed.status in known_non_workset:
            return WorksetInspection("waiting_for_reservable_workset", assessed.reason)
        raise DrainV2IntegrityError(
            f"Unknown or ambiguous v1 waiting family:{assessed.status}"
        )
    try:
        live_profile = live.load_config()
        live_paths = live.runtime_paths(live_profile, runtime_root=paths.active_root)
        if (
            Path(live_paths.lock) != paths.runner_lock
            or Path(live_paths.issue_inbox) != paths.issue_inbox
        ):
            raise DrainV2IntegrityError(
                "Live runtime paths differ from the frozen v2 lock binding"
            )
        prerequisites = live.load_prerequisites(live_profile, live_paths)
        if prerequisites is None:
            raise DrainV2IntegrityError(
                "Dirty workset lost its machine-verifiable live prerequisites"
            )
        projection = live.load_verified_ledger_projection(
            live_profile, live_paths, prerequisites
        )
    except (
        live.LiveConfigError,
        live.LivePrerequisiteError,
        live.LiveInputError,
        live.LiveIntegrityError,
    ) as exc:
        raise DrainV2IntegrityError(str(exc)) from exc
    context = WorksetContext(
        registry_event_sequence_id=authority.r1_event["sequence_id"],
        registry_event_entry_sha256=authority.r1_event["entry_sha256"],
        preparation_event_sequence_id=authority.preparation_event["sequence_id"],
        preparation_event_entry_sha256=authority.preparation_event["entry_sha256"],
        candidate_id=authority.r1_event["candidate_id"],
        slot_id=authority.r1_event["slot_id"],
        old_live_epoch_id=projection.epoch_id,
        live_event_count=projection.ledger_event_count,
        live_terminal_sha256=projection.ledger_terminal_sha256,
    )
    item = WorksetItem(
        family=family,
        natural_key=_natural_key(family, assessed.status, assessed.reason),
        observed_status=assessed.status,
        reason=assessed.reason,
    )
    return WorksetInspection(
        "dirty_workset_observed", assessed.reason, (item,), context
    )


def _normalize_inspection(
    profile: Mapping[str, Any], inspection: WorksetInspection
) -> tuple[WorksetItem, ...]:
    if not isinstance(inspection, WorksetInspection):
        raise DrainV2IntegrityError("Workset inspector returned an unknown result")
    _canonical_text(inspection.status, name="workset inspection status")
    _canonical_text(inspection.reason, name="workset inspection reason")
    if len(inspection.items) > profile["protocol"]["maximum_items"]:
        raise DrainV2IntegrityError("Bounded workset exceeds the reviewed item limit")
    if inspection.status == "not_applicable_clean_use_v1":
        if inspection.items or inspection.context is not None:
            raise DrainV2IntegrityError("Clean inspection returned workset items")
        return ()
    if inspection.status != "dirty_workset_observed":
        if inspection.items or inspection.context is not None:
            raise DrainV2IntegrityError("Non-dirty inspection returned workset items")
        return ()
    if not inspection.items:
        raise DrainV2IntegrityError("Dirty inspection returned no workset items")
    _context_payload(inspection.context)
    seen: set[tuple[str, str]] = set()
    normalized: list[WorksetItem] = []
    for item in inspection.items:
        if not isinstance(item, WorksetItem) or item.family not in FAMILIES:
            raise DrainV2IntegrityError("Workset contains an unknown family")
        _canonical_text(item.natural_key, name="workset natural key")
        _canonical_text(item.observed_status, name="workset observed status")
        _canonical_text(item.reason, name="workset reason")
        identity = (item.family, item.natural_key)
        if identity in seen:
            raise DrainV2IntegrityError("Workset contains a duplicate natural key")
        seen.add(identity)
        normalized.append(item)
    ordered = sorted(
        normalized, key=lambda item: (FAMILIES.index(item.family), item.natural_key)
    )
    return tuple(ordered)


def _observation_payload(
    profile: Mapping[str, Any],
    items: Sequence[WorksetItem],
    context: WorksetContext | None,
) -> dict[str, object]:
    return {
        "schema_version": profile["protocol"]["observation_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "observation_role": "context_bound_first_blocker_only_no_reservation_or_recovery_action",
        "observation_scope": "first_v1_clean_gate_blocker_only",
        "authority_context": _context_payload(context),
        "item_count": len(items),
        "items": [
            {
                "family": item.family,
                "natural_key": item.natural_key,
                "observed_status": item.observed_status,
                "reason": item.reason,
            }
            for item in items
        ],
        "first_blocker_observation_implemented": True,
        "observation_binds_r1_r2a_authority": True,
        "observation_binds_old_epoch_context": True,
        **{claim: False for claim in FALSE_CLAIMS},
    }


def _event_payload(
    profile: Mapping[str, Any], observation: registry.ArtifactSnapshot, recorded: str
) -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": profile["protocol"]["event_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "sequence_id": 1,
        "previous_entry_sha256": ZERO_HASH,
        "event_type": profile["protocol"]["event_type"],
        "recorded_at_utc": recorded,
        "observation_scope": "first_v1_clean_gate_blocker_only",
        "observation": {
            "path": observation.path.name,
            "sha256": observation.sha256,
            "size_bytes": observation.size_bytes,
        },
        "first_blocker_observation_implemented": True,
        **{claim: False for claim in FALSE_CLAIMS},
    }
    return {**body, "entry_sha256": _sha256(_canonical_bytes(body))}


def _load_observation_reference(
    profile: Mapping[str, Any], paths: DrainV2Paths, value: object
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    reference = _exact(
        value,
        {"path", "sha256", "size_bytes"},
        name="v2 first-blocker observation reference",
    )
    digest = _hash_text(reference["sha256"], name="v2 observation SHA-256")
    if reference["path"] != f"{digest}.json":
        raise DrainV2IntegrityError("V2 observation reference path changed")
    size = reference["size_bytes"]
    if (
        type(size) is not int
        or size <= 0
        or size > profile["protocol"]["maximum_manifest_bytes"]
    ):
        raise DrainV2IntegrityError("V2 observation reference size is invalid")
    path = paths.observations / reference["path"]
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            path,
            name="v2 first-blocker observation",
            maximum_bytes=profile["protocol"]["maximum_manifest_bytes"],
        )
        payload = registry._decode_json(  # noqa: SLF001
            snapshot.raw, name="v2 first-blocker observation"
        )
    except registry.EpochRegistryError as exc:
        raise DrainV2IntegrityError(str(exc)) from exc
    if (
        snapshot.sha256 != digest
        or snapshot.size_bytes != size
        or snapshot.raw != _canonical_bytes(payload)
    ):
        raise DrainV2IntegrityError(
            "V2 observation reference did not dereference exactly"
        )
    _exact(
        payload,
        {
            "schema_version",
            "profile_id",
            "profile_sha256",
            "observation_role",
            "observation_scope",
            "authority_context",
            "item_count",
            "items",
            "first_blocker_observation_implemented",
            "observation_binds_r1_r2a_authority",
            "observation_binds_old_epoch_context",
            *FALSE_CLAIMS,
        },
        name="v2 first-blocker observation",
    )
    if (
        payload["schema_version"] != profile["protocol"]["observation_schema_version"]
        or payload["profile_id"] != profile["profile_id"]
        or payload["profile_sha256"] != profile["_profile_sha256"]
        or payload["observation_role"]
        != "context_bound_first_blocker_only_no_reservation_or_recovery_action"
        or payload["observation_scope"] != "first_v1_clean_gate_blocker_only"
        or payload["first_blocker_observation_implemented"] is not True
        or payload["observation_binds_r1_r2a_authority"] is not True
        or payload["observation_binds_old_epoch_context"] is not True
        or any(payload.get(claim) is not False for claim in FALSE_CLAIMS)
    ):
        raise DrainV2IntegrityError("V2 observation semantics changed")
    context = _context_from_payload(payload["authority_context"])
    raw_items = payload["items"]
    if not isinstance(raw_items, list):
        raise DrainV2IntegrityError("V2 observation items are not a list")
    items: list[WorksetItem] = []
    for raw_item in raw_items:
        item_payload = _exact(
            raw_item,
            {"family", "natural_key", "observed_status", "reason"},
            name="v2 observation item",
        )
        items.append(WorksetItem(**item_payload))
    normalized = _normalize_inspection(
        profile,
        WorksetInspection(
            "dirty_workset_observed",
            "replayed immutable first-blocker observation",
            tuple(items),
            context,
        ),
    )
    if (
        tuple(items) != normalized
        or type(payload["item_count"]) is not int
        or payload["item_count"] != len(items)
    ):
        raise DrainV2IntegrityError("V2 observation item ordering/count changed")
    return payload, snapshot


def _load_existing_event(
    profile: Mapping[str, Any], paths: DrainV2Paths
) -> tuple[dict[str, Any], registry.ArtifactSnapshot, registry.ArtifactSnapshot] | None:
    entries = _strict_entries(paths.events, name="v2 observation events")
    if not entries:
        return None
    if len(entries) != 1:
        raise DrainV2IntegrityError("V2 observation event chain is not singleton")
    try:
        snapshot = registry._read_regular(entries[0], name="v2 observation event")  # noqa: SLF001
        payload = registry._decode_json(snapshot.raw, name="v2 observation event")  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise DrainV2IntegrityError(str(exc)) from exc
    if snapshot.raw != _canonical_bytes(payload):
        raise DrainV2IntegrityError("V2 observation event is not canonical")
    _exact(
        payload,
        {
            "schema_version",
            "profile_id",
            "profile_sha256",
            "sequence_id",
            "previous_entry_sha256",
            "entry_sha256",
            "event_type",
            "recorded_at_utc",
            "observation_scope",
            "observation",
            "first_blocker_observation_implemented",
            *FALSE_CLAIMS,
        },
        name="v2 observation event",
    )
    entry = payload.get("entry_sha256")
    body = dict(payload)
    body.pop("entry_sha256", None)
    if (
        entry != _sha256(_canonical_bytes(body))
        or entries[0].name != f"{1:020d}-{entry}.json"
        or payload.get("schema_version") != profile["protocol"]["event_schema_version"]
        or payload.get("profile_id") != profile["profile_id"]
        or payload.get("profile_sha256") != profile["_profile_sha256"]
        or type(payload.get("sequence_id")) is not int
        or payload.get("sequence_id") != 1
        or payload.get("previous_entry_sha256") != ZERO_HASH
        or payload.get("event_type") != profile["protocol"]["event_type"]
        or payload.get("observation_scope") != "first_v1_clean_gate_blocker_only"
        or payload.get("first_blocker_observation_implemented") is not True
        or any(payload.get(claim) is not False for claim in FALSE_CLAIMS)
    ):
        raise DrainV2IntegrityError("V2 observation event semantics changed")
    try:
        registry._utc_timestamp(  # noqa: SLF001
            payload["recorded_at_utc"], name="v2 observation event recorded_at_utc"
        )
    except registry.EpochRegistryError as exc:
        raise DrainV2IntegrityError(str(exc)) from exc
    _, observation = _load_observation_reference(profile, paths, payload["observation"])
    return payload, snapshot, observation


def _write_cache(
    path: Path, payload: Mapping[str, object], *, root: Path, name: str
) -> None:
    try:
        registry._atomic_cache(path, _canonical_bytes(payload), root=root, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise DrainV2IntegrityError(str(exc)) from exc


def _status_payload(
    profile: Mapping[str, Any],
    *,
    checked_at: str,
    status: str,
    reason: str,
    event: Mapping[str, Any] | None,
) -> dict[str, object]:
    return {
        "schema_version": profile["protocol"]["status_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "checked_at_utc": checked_at,
        "status": status,
        "reason": reason,
        "event_count": 1 if event else 0,
        "terminal_entry_sha256": event["entry_sha256"] if event else ZERO_HASH,
        "machine_only": True,
        "cache_authority": False,
        "observation_scope": "first_v1_clean_gate_blocker_only",
        "first_blocker_observation_implemented": True,
        **{claim: False for claim in FALSE_CLAIMS},
    }


def _publish_observation(
    profile: Mapping[str, Any],
    paths: DrainV2Paths,
    items: Sequence[WorksetItem],
    context: WorksetContext | None,
    recorded: str,
) -> tuple[registry.ArtifactSnapshot, dict[str, Any], registry.ArtifactSnapshot, bool]:
    raw = _canonical_bytes(_observation_payload(profile, items, context))
    if len(raw) > profile["protocol"]["maximum_manifest_bytes"]:
        raise DrainV2IntegrityError("First-blocker observation exceeds the byte limit")
    digest = _sha256(raw)
    observation_path = paths.observations / f"{digest}.json"
    existing = _load_existing_event(profile, paths)
    if existing is not None:
        event, event_snapshot, observation = existing
        if observation.raw != raw:
            raise DrainV2IntegrityError(
                "First blocker or its authority context changed after observation"
            )
        return observation, event, event_snapshot, False
    try:
        observation = drain._publish_once_durable(  # noqa: SLF001
            observation_path,
            raw,
            root=paths.root,
            name="v2 first-blocker observation",
        )
    except drain.EpochDrainError as exc:
        raise DrainV2IntegrityError(str(exc)) from exc
    event = _event_payload(profile, observation, recorded)
    event_path = paths.events / f"{1:020d}-{event['entry_sha256']}.json"
    try:
        event_snapshot = drain._publish_once_durable(  # noqa: SLF001
            event_path,
            _canonical_bytes(event),
            root=paths.root,
            name="v2 observation event",
        )
    except drain.EpochDrainError as exc:
        raise DrainV2IntegrityError(str(exc)) from exc
    replayed = _load_existing_event(profile, paths)
    if replayed is None or replayed[0] != event:
        raise DrainV2IntegrityError("V2 observation event did not replay exactly")
    return observation, event, event_snapshot, True


def _coordinate_epoch_drain_v2(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    runtime_root: Path | None = None,
    active_runtime_root: Path | None = None,
    shadow_runtime_root: Path | None = None,
    clock: Callable[[], datetime] | None = None,
    inspect_workset: InspectWorkset | None = None,
) -> DrainV2Result:
    """Record one context-bound blocker under all locks; perform no recovery."""

    profile = load_drain_v2_profile(config_path)
    paths = drain_v2_paths(
        profile,
        runtime_root=runtime_root,
        active_runtime_root=active_runtime_root,
        shadow_runtime_root=shadow_runtime_root,
    )
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    checked_at = _utc_text(now)
    handles: list[BinaryIO] = []
    event: dict[str, Any] | None = None
    event_snapshot: registry.ArtifactSnapshot | None = None
    observation_snapshot: registry.ArtifactSnapshot | None = None
    all_locks_acquired = False
    try:
        for label, path in zip(
            LOCK_ORDER,
            (
                paths.manager_lock,
                paths.cycle_lock,
                paths.deploy_lock,
                paths.runner_lock,
                paths.replay_lock,
                paths.shadow_lock,
            ),
            strict=True,
        ):
            try:
                handles.append(drain._acquire_lock(path, label=label))  # noqa: SLF001
            except drain.EpochDrainBusyError as exc:
                raise DrainV2BusyError(str(exc)) from exc
            except drain.EpochDrainError as exc:
                raise DrainV2IntegrityError(str(exc)) from exc
        all_locks_acquired = True
        existing = _load_existing_event(profile, paths)
        if existing is not None:
            event, event_snapshot, observation_snapshot = existing
        witnesses = _v1_authority_witnesses(paths)
        if witnesses:
            status = (
                "v1_authority_supersedes_observation"
                if event is not None
                else "not_applicable_v1_authority_present"
            )
            reason = (
                "v1 durable authority has precedence; the v2 observation is inert:"
                + ",".join(witnesses)
            )
            payload = _status_payload(
                profile,
                checked_at=checked_at,
                status=status,
                reason=reason,
                event=event,
            )
            _write_cache(paths.status, payload, root=paths.root, name="v2 status")
            return DrainV2Result(
                status,
                reason,
                paths.status,
                event_snapshot.path if event_snapshot is not None else None,
                (
                    observation_snapshot.path
                    if observation_snapshot is not None
                    else None
                ),
            )
        inspection = (inspect_workset or _default_inspection)(paths, now)
        items = _normalize_inspection(profile, inspection)
        if inspection.status != "dirty_workset_observed":
            if event is not None:
                status = "first_blocker_observation_stale"
                reason = (
                    "the immutable first-blocker observation no longer describes "
                    "the current v1 clean gate; it has no recovery authority"
                )
                _write_cache(
                    paths.status,
                    _status_payload(
                        profile,
                        checked_at=checked_at,
                        status=status,
                        reason=reason,
                        event=event,
                    ),
                    root=paths.root,
                    name="v2 status",
                )
                return DrainV2Result(
                    status,
                    reason,
                    paths.status,
                    event_snapshot.path if event_snapshot is not None else None,
                    (
                        observation_snapshot.path
                        if observation_snapshot is not None
                        else None
                    ),
                )
            payload = _status_payload(
                profile,
                checked_at=checked_at,
                status=inspection.status,
                reason=inspection.reason,
                event=None,
            )
            _write_cache(paths.status, payload, root=paths.root, name="v2 status")
            return DrainV2Result(
                inspection.status, inspection.reason, paths.status, None, None
            )
        candidate_raw = _canonical_bytes(
            _observation_payload(profile, items, inspection.context)
        )
        if (
            observation_snapshot is not None
            and observation_snapshot.raw != candidate_raw
        ):
            status = "first_blocker_observation_stale"
            reason = (
                "the v1 clean gate now reports a different first blocker or authority "
                "context; the immutable observation is inert"
            )
            _write_cache(
                paths.status,
                _status_payload(
                    profile,
                    checked_at=checked_at,
                    status=status,
                    reason=reason,
                    event=event,
                ),
                root=paths.root,
                name="v2 status",
            )
            return DrainV2Result(
                status,
                reason,
                paths.status,
                event_snapshot.path if event_snapshot is not None else None,
                observation_snapshot.path,
            )
        observation, event, event_snapshot, created = _publish_observation(
            profile, paths, items, inspection.context, checked_at
        )
        status = (
            "first_blocker_observed"
            if created
            else "first_blocker_observation_idempotent"
        )
        reason = (
            "one context-bound v1 clean-gate blocker was observed; no complete "
            "workset, admission fence, or recovery action is implemented"
        )
        _write_cache(
            paths.head,
            {
                "schema_version": profile["protocol"]["head_schema_version"],
                "profile_id": profile["profile_id"],
                "profile_sha256": profile["_profile_sha256"],
                "sequence_id": 1,
                "entry_sha256": event["entry_sha256"],
                "cache_authority": False,
            },
            root=paths.root,
            name="v2 head",
        )
        _write_cache(
            paths.status,
            _status_payload(
                profile,
                checked_at=checked_at,
                status=status,
                reason=reason,
                event=event,
            ),
            root=paths.root,
            name="v2 status",
        )
        return DrainV2Result(
            status,
            reason,
            paths.status,
            event_snapshot.path,
            observation.path,
        )
    except DrainV2BusyError:
        raise
    except DrainV2Error as exc:
        if all_locks_acquired:
            try:
                _write_cache(
                    paths.status,
                    _status_payload(
                        profile,
                        checked_at=checked_at,
                        status="blocked_integrity",
                        reason=f"{type(exc).__name__}:{exc}",
                        event=event,
                    ),
                    root=paths.root,
                    name="v2 status",
                )
            except DrainV2Error:
                pass
        raise
    finally:
        try:
            drain._release_locks(handles)  # noqa: SLF001
        except drain.EpochDrainError as exc:
            if sys.exc_info()[0] is None:
                raise DrainV2IntegrityError(str(exc)) from exc


def coordinate_epoch_drain_v2(
    *, config_path: Path = DEFAULT_CONFIG_PATH
) -> DrainV2Result:
    """Run the reviewed machine-only first-blocker observation poll."""

    return _coordinate_epoch_drain_v2(config_path=config_path)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = coordinate_epoch_drain_v2(config_path=args.config)
    except DrainV2BusyError as exc:
        print(json.dumps({"status": "busy", "reason": str(exc)}, sort_keys=True))
        return 3
    except DrainV2Error as exc:
        print(
            json.dumps(
                {
                    "status": "blocked_integrity",
                    "reason": f"{type(exc).__name__}:{exc}",
                },
                sort_keys=True,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "status": result.status,
                "reason": result.reason,
                "status_path": str(result.status_path),
                "event_path": str(result.event_path) if result.event_path else None,
                "observation_path": (
                    str(result.observation_path) if result.observation_path else None
                ),
                "first_blocker_observation_implemented": True,
                "bounded_workset_reservation_implemented": False,
                "bounded_workset_recovery_implemented": False,
                "complete_workset_enumeration": False,
                "old_work_admission_fence_implemented": False,
                "lifecycle_authority": False,
                "transition_authority": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
