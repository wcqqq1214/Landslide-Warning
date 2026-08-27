"""Observe machine-current clean drain eligibility without authorizing transition.

R2b-2a is an explicit, non-default machine stage.  Its append-only observations
remain in ``DRAINING`` and never declare the old epoch drained or activate a new
epoch.  Any future transition must re-evaluate machine-current state.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, BinaryIO


ROOT = Path(__file__).resolve().parents[2]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_epoch_drain as drain  # noqa: E402
from monitoring import ootang_epoch_registry as registry  # noqa: E402


DEFAULT_CONFIG_PATH = ROOT / "config" / "ootang_epoch_drain_eligibility.v1.json"
DEFAULT_CONFIG_SHA256 = (
    "2dbda86a747ef486bc06d5c4901e3356404c0079ec2b8aecf91eb61afc91413a"
)
IMPLEMENTATION_LOGICAL_PATH = "code/monitoring/ootang_epoch_drain_eligibility.py"
ZERO_HASH = "0" * 64
MAX_CONTROL_BYTES = 4 * 1024 * 1024
MAX_OBSERVATION_BYTES = 64 * 1024 * 1024
LOCK_ORDER = ("manager", "cycle", "deploy", "runner", "replay", "shadow")

EXPECTED_UPSTREAM = {
    "drain_profile": {
        "path": "config/ootang_epoch_drain.v1.json",
        "expected_sha256": drain.DEFAULT_CONFIG_SHA256,
    },
    "drain_implementation": {
        "path": "code/monitoring/ootang_epoch_drain.py",
        "expected_sha256": (
            "c4c226da087d354195fc3955ff60ff3b12af13f111d03cb59c5d64d0195a1602"
        ),
    },
}

EXPECTED_RUNTIME = {
    "root": "runtime/ootang_epoch_registry_v1",
    "manager_lock": "manager.lock",
    "events": "drain_eligibility_events",
    "head": "drain_eligibility_head.json",
    "status": "drain_eligibility_status.json",
    "objects": "objects/sha256",
    "active_root": "runtime/ootang_prequential_live_v1",
    "shadow_root": "runtime/ootang_prequential_calibration_shadow_v1",
    "cycle_lock": "prequential_cycle.lock",
    "deploy_lock": "deploy_cycle.lock",
    "runner_lock": "runner.lock",
    "replay_lock": "issue_replay.lock",
    "shadow_lock": "runner.lock",
}

EXPECTED_PROTOCOL = {
    "event_schema_version": "ootang_epoch_drain_eligibility_event_v1",
    "observation_schema_version": "ootang_epoch_drain_eligibility_observation_v1",
    "head_schema_version": "ootang_epoch_drain_eligibility_head_v1",
    "status_schema_version": "ootang_epoch_drain_eligibility_status_v1",
    "maximum_observation_bytes": MAX_OBSERVATION_BYTES,
    "initial_previous_entry_sha256": ZERO_HASH,
    "canonical_json": "utf8_sort_keys_compact_no_nan_trailing_lf",
    "lock_order": list(LOCK_ORDER),
    "event_type": "drain_eligibility_observed",
    "lifecycle_state": "DRAINING",
    "same_current_observation": "idempotent_preserve_first_bytes",
    "settled_extension": "append_new_observation_and_event",
    "pending_after_observation": "status_current_false_no_event",
    "oversize_observation": "status_current_false_no_event_wait_for_v2",
    "integrity_failure_status": "best_effort_current_false_not_authority",
    "future_transition_policy": "must_recheck_machine_current_state",
    "authority_role": (
        "publication_time_observation_only_not_lifecycle_or_transition_authority"
    ),
}

EXPECTED_CAPABILITIES = {
    "epoch_drain_authority_replayed": True,
    "current_clean_observation_implemented": True,
    "stale_observation_detection_implemented": True,
    "observation_authority_only": True,
    "lifecycle_authority": False,
    "transition_authority": False,
    "drained_eligibility_current": False,
    "old_epoch_drained": False,
    "old_epoch_drain_implemented": False,
    "activation_candidate_selected": False,
    "active_epoch_switch_implemented": False,
    "automatic_epoch_rotation_implemented": False,
    "trusted_anchor_receipt_verified": False,
    "e2_live_evidence_eligible": False,
    "real_activation_ready": False,
    "formal_warning_output": False,
}

FALSE_CLAIMS = {
    "lifecycle_authority",
    "transition_authority",
    "old_epoch_drained",
    "old_epoch_drain_implemented",
    "activation_candidate_selected",
    "active_epoch_switch_implemented",
    "automatic_epoch_rotation_implemented",
    "trusted_anchor_receipt_verified",
    "e2_live_evidence_eligible",
    "real_activation_ready",
    "formal_warning_output",
}

OBSERVATION_SUFFIX = ".epoch-drain-eligibility-observation.json"
IMPLEMENTATION_SUFFIX = ".epoch-drain-eligibility-implementation.py"
EVENT_NAME = re.compile(r"^(?P<sequence>[0-9]{20})-(?P<entry>[0-9a-f]{64})\.json$")
REFERENCE_KEYS = {"path", "sha256", "size_bytes"}
CLEAN_STATE_KEYS = {
    "old_live_epoch_id",
    "live_event_count",
    "live_terminal_sha256",
    "guard_record_count",
    "trusted_time_record_count",
    "shadow_event_count",
    "shadow_terminal_sha256",
    "issue_route_inventory_sha256",
    "issue_route_inventory",
    "outcome_registry_inventory",
    "guard_inventory",
    "trusted_time_inventory",
    "source_authority",
    "live_entry_sha256s",
    "shadow_entry_sha256s",
}


class DrainEligibilityError(RuntimeError):
    """Base R2b-2a eligibility observer error."""


class DrainEligibilityConfigError(DrainEligibilityError):
    """The reviewed eligibility profile or frozen R2b binding changed."""


class DrainEligibilityIntegrityError(DrainEligibilityError):
    """Eligibility authority, observation, or runtime state failed closed."""


class DrainEligibilityBusyError(DrainEligibilityError):
    """A machine writer owns one of the globally ordered locks."""


@dataclass(frozen=True)
class EligibilityPaths:
    root: Path
    manager_lock: Path
    events: Path
    head: Path
    status: Path
    objects: Path
    active_root: Path
    shadow_root: Path
    cycle_lock: Path
    deploy_lock: Path
    runner_lock: Path
    replay_lock: Path
    shadow_lock: Path
    drain: drain.DrainPaths


@dataclass(frozen=True)
class DrainEligibilityResult:
    status: str
    status_path: Path
    event_path: Path | None


@dataclass(frozen=True)
class _DrainingAssessment:
    authority: object
    event: dict[str, Any]
    event_snapshot: registry.ArtifactSnapshot
    current: drain._CleanState | drain._Waiting  # noqa: SLF001


@dataclass
class _ObservedClock:
    source: Clock
    last: datetime | None = None

    def sample(self) -> datetime:
        try:
            value = self.source()
        except Exception as exc:
            raise DrainEligibilityIntegrityError(
                "Drain eligibility machine clock failed"
            ) from exc
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise DrainEligibilityIntegrityError(
                "Drain eligibility machine clock must be timezone-aware"
            )
        current = value.astimezone(timezone.utc)
        if self.last is not None and current < self.last:
            raise DrainEligibilityIntegrityError(
                "Drain eligibility machine clock moved backwards"
            )
        self.last = current
        return current


Clock = Callable[[], datetime]


def _canonical_bytes(value: Mapping[str, object]) -> bytes:
    return registry._canonical_bytes(dict(value))  # noqa: SLF001


def _exact(value: object, keys: set[str], *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise DrainEligibilityIntegrityError(f"{name} keys changed")
    return value


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _hash(value: object, *, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise DrainEligibilityIntegrityError(f"{name} is not a lowercase SHA-256")
    return value


def _text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise DrainEligibilityIntegrityError(f"{name} is not canonical text")
    return value


def load_eligibility_profile(
    path: Path = DEFAULT_CONFIG_PATH, *, project_root: Path = ROOT
) -> dict[str, Any]:
    """Load only the reviewed R2b-2a profile and exact frozen R2b bytes."""

    root = project_root.resolve()
    resolved = path if path.is_absolute() else root / path
    resolved = registry._absolute_lexical(resolved)  # noqa: SLF001
    try:
        resolved.relative_to(root)
        registry._ensure_existing_parents_not_symlinks(root, resolved)  # noqa: SLF001
    except (ValueError, registry.EpochRegistryError) as exc:
        raise DrainEligibilityConfigError(
            "Eligibility profile escapes the project root"
        ) from exc
    if root != ROOT.resolve() or resolved != registry._absolute_lexical(  # noqa: SLF001
        DEFAULT_CONFIG_PATH
    ):
        raise DrainEligibilityConfigError(
            "Only the reviewed default eligibility profile is accepted"
        )
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            resolved,
            name="epoch drain eligibility profile",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
        if snapshot.sha256 != DEFAULT_CONFIG_SHA256:
            raise DrainEligibilityIntegrityError(
                "Reviewed eligibility profile digest changed"
            )
        profile = registry._decode_json(  # noqa: SLF001
            snapshot.raw, name="epoch drain eligibility profile"
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
                "independent_label_used",
                "confirmatory_external_validation",
                "vajont_used",
                "default_pipeline_member",
                "upstream",
                "runtime",
                "protocol",
                "engineering_capabilities",
            },
            name="epoch drain eligibility profile",
        )
        if (
            profile["schema_version"],
            profile["profile_id"],
            profile["profile_version"],
            profile["case"],
            profile["artifact_status"],
        ) != (
            "ootang_epoch_drain_eligibility_profile_v1",
            "ootang-epoch-drain-eligibility-v1",
            "1.0.0-engineering",
            "ootang",
            "drain_eligibility_observation_engineering_only_not_transition_authority",
        ):
            raise DrainEligibilityIntegrityError("Eligibility profile identity changed")
        for flag in (
            "formal_warning_output",
            "independent_label_used",
            "confirmatory_external_validation",
            "vajont_used",
            "default_pipeline_member",
        ):
            if profile[flag] is not False:
                raise DrainEligibilityIntegrityError(
                    f"Eligibility profile {flag} must remain false"
                )
        if profile["upstream"] != EXPECTED_UPSTREAM:
            raise DrainEligibilityIntegrityError(
                "Eligibility upstream bindings changed"
            )
        if profile["runtime"] != EXPECTED_RUNTIME:
            raise DrainEligibilityIntegrityError("Eligibility runtime mapping changed")
        if profile["protocol"] != EXPECTED_PROTOCOL:
            raise DrainEligibilityIntegrityError("Eligibility protocol changed")
        if profile["engineering_capabilities"] != EXPECTED_CAPABILITIES:
            raise DrainEligibilityIntegrityError(
                "Eligibility capability boundary changed"
            )
        for binding in EXPECTED_UPSTREAM.values():
            bound = registry._contained(  # noqa: SLF001
                root, binding["path"], name="eligibility upstream binding"
            )
            captured = registry._read_regular(  # noqa: SLF001
                bound, name="eligibility upstream binding"
            )
            if captured.sha256 != binding["expected_sha256"]:
                raise DrainEligibilityIntegrityError(
                    f"Frozen upstream changed:{binding['path']}"
                )
        drain_profile = drain.load_drain_profile()
        if (
            drain_profile["_profile_sha256"]
            != EXPECTED_UPSTREAM["drain_profile"]["expected_sha256"]
        ):
            raise DrainEligibilityIntegrityError("Frozen R2b profile changed")
        implementation = registry._read_regular(  # noqa: SLF001
            registry._contained(  # noqa: SLF001
                root,
                IMPLEMENTATION_LOGICAL_PATH,
                name="eligibility implementation",
            ),
            name="eligibility implementation",
        )
    except (
        DrainEligibilityIntegrityError,
        drain.EpochDrainError,
        registry.EpochRegistryError,
    ) as exc:
        raise DrainEligibilityConfigError(str(exc)) from exc
    profile["_profile_path"] = str(resolved)
    profile["_profile_sha256"] = snapshot.sha256
    profile["_implementation_sha256"] = implementation.sha256
    profile["_project_root"] = str(root)
    profile["_drain_profile"] = drain_profile
    return profile


def eligibility_paths(
    profile: Mapping[str, Any],
    *,
    runtime_root: Path | None = None,
    active_runtime_root: Path | None = None,
    shadow_runtime_root: Path | None = None,
) -> EligibilityPaths:
    drain_profile = profile.get("_drain_profile")
    if not isinstance(drain_profile, dict):
        raise DrainEligibilityConfigError("Eligibility profile lost frozen R2b")
    drain_paths = drain.drain_paths(
        drain_profile,
        runtime_root=runtime_root,
        active_runtime_root=active_runtime_root,
        shadow_runtime_root=shadow_runtime_root,
    )
    root = drain_paths.root

    def child(base: Path, value: object, *, name: str) -> Path:
        try:
            return registry._contained(base, value, name=name)  # noqa: SLF001
        except registry.EpochRegistryError as exc:
            raise DrainEligibilityConfigError(str(exc)) from exc

    paths = EligibilityPaths(
        root=root,
        manager_lock=child(
            root, profile["runtime"]["manager_lock"], name="manager lock"
        ),
        events=child(root, profile["runtime"]["events"], name="eligibility events"),
        head=child(root, profile["runtime"]["head"], name="eligibility head"),
        status=child(root, profile["runtime"]["status"], name="eligibility status"),
        objects=child(root, profile["runtime"]["objects"], name="shared objects"),
        active_root=drain_paths.active_root,
        shadow_root=drain_paths.shadow_root,
        cycle_lock=drain_paths.cycle_lock,
        deploy_lock=drain_paths.deploy_lock,
        runner_lock=drain_paths.runner_lock,
        replay_lock=drain_paths.replay_lock,
        shadow_lock=drain_paths.shadow_lock,
        drain=drain_paths,
    )
    if (
        paths.manager_lock != drain_paths.manager_lock
        or paths.objects != drain_paths.objects
    ):
        raise DrainEligibilityConfigError(
            "R2b and R2b-2a do not share one manager/object store"
        )
    return paths


def _utc_text(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _parse_utc(value: object, *, name: str) -> datetime:
    text = _text(value, name=name)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DrainEligibilityIntegrityError(
            f"{name} is not an ISO-8601 UTC time"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DrainEligibilityIntegrityError(f"{name} is not timezone-aware")
    parsed = parsed.astimezone(timezone.utc)
    if _utc_text(parsed) != text:
        raise DrainEligibilityIntegrityError(f"{name} is not canonical UTC")
    return parsed


def _integer(value: object, *, name: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise DrainEligibilityIntegrityError(f"{name} is invalid")
    return value


def _reference(value: object, *, name: str) -> dict[str, object]:
    record = _exact(value, REFERENCE_KEYS, name=name)
    _text(record["path"], name=f"{name}.path")
    _hash(record["sha256"], name=f"{name}.sha256")
    _integer(record["size_bytes"], name=f"{name}.size_bytes")
    return copy.deepcopy(record)


def _read_reference(
    value: object,
    *,
    paths: EligibilityPaths,
    name: str,
    maximum_bytes: int = MAX_CONTROL_BYTES,
) -> registry.ArtifactSnapshot:
    try:
        return drain._read_reference(  # noqa: SLF001
            value, root=paths.root, name=name, maximum_bytes=maximum_bytes
        )
    except drain.EpochDrainError as exc:
        raise DrainEligibilityIntegrityError(str(exc)) from exc


def _artifact_reference(
    snapshot: registry.ArtifactSnapshot, paths: EligibilityPaths
) -> dict[str, object]:
    try:
        return drain._artifact_reference(snapshot, paths.root)  # noqa: SLF001
    except drain.EpochDrainError as exc:
        raise DrainEligibilityIntegrityError(str(exc)) from exc


def _clean_payload(clean: drain._CleanState) -> dict[str, object]:  # noqa: SLF001
    return {
        "old_live_epoch_id": clean.old_live_epoch_id,
        "live_event_count": clean.live_event_count,
        "live_terminal_sha256": clean.live_terminal_sha256,
        "guard_record_count": clean.guard_record_count,
        "trusted_time_record_count": clean.trusted_time_record_count,
        "shadow_event_count": clean.shadow_event_count,
        "shadow_terminal_sha256": clean.shadow_terminal_sha256,
        "issue_route_inventory_sha256": clean.issue_route_inventory_sha256,
        "issue_route_inventory": copy.deepcopy(list(clean.issue_route_inventory)),
        "outcome_registry_inventory": copy.deepcopy(
            list(clean.outcome_registry_inventory)
        ),
        "guard_inventory": copy.deepcopy(list(clean.guard_inventory)),
        "trusted_time_inventory": copy.deepcopy(list(clean.trusted_time_inventory)),
        "source_authority": copy.deepcopy(clean.source_authority),
        "live_entry_sha256s": list(clean.live_entry_sha256s),
        "shadow_entry_sha256s": list(clean.shadow_entry_sha256s),
    }


def _hash_sequence(value: object, *, name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise DrainEligibilityIntegrityError(f"{name} is not a list")
    return tuple(_hash(item, name=f"{name}[]") for item in value)


def _inventory(value: object, *, name: str) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise DrainEligibilityIntegrityError(f"{name} is not a record list")
    return tuple(copy.deepcopy(value))


def _clean_from_payload(value: object) -> drain._CleanState:  # noqa: SLF001
    record = _exact(value, CLEAN_STATE_KEYS, name="eligibility clean state")
    live_hashes = _hash_sequence(
        record["live_entry_sha256s"], name="clean live entries"
    )
    shadow_hashes = _hash_sequence(
        record["shadow_entry_sha256s"], name="clean shadow entries"
    )
    live_count = _integer(record["live_event_count"], name="clean live count")
    shadow_count = _integer(record["shadow_event_count"], name="clean shadow count")
    if live_count != len(live_hashes) or shadow_count != len(shadow_hashes):
        raise DrainEligibilityIntegrityError("Clean ledger count changed")
    live_terminal = _hash(record["live_terminal_sha256"], name="clean live terminal")
    shadow_terminal = _hash(
        record["shadow_terminal_sha256"], name="clean shadow terminal"
    )
    if live_terminal != (live_hashes[-1] if live_hashes else ZERO_HASH):
        raise DrainEligibilityIntegrityError("Clean live terminal changed")
    if shadow_terminal != (shadow_hashes[-1] if shadow_hashes else ZERO_HASH):
        raise DrainEligibilityIntegrityError("Clean shadow terminal changed")
    issue_inventory = _inventory(
        record["issue_route_inventory"], name="clean issue inventory"
    )
    outcome_inventory = _inventory(
        record["outcome_registry_inventory"], name="clean outcome inventory"
    )
    guard_inventory = _inventory(
        record["guard_inventory"], name="clean guard inventory"
    )
    trusted_inventory = _inventory(
        record["trusted_time_inventory"], name="clean trusted-time inventory"
    )
    guard_count = _integer(record["guard_record_count"], name="clean guard count")
    trusted_count = _integer(
        record["trusted_time_record_count"], name="clean trusted-time count"
    )
    if guard_count != len(guard_inventory) or trusted_count != len(trusted_inventory):
        raise DrainEligibilityIntegrityError("Clean record count changed")
    issue_digest = _hash(
        record["issue_route_inventory_sha256"], name="clean issue inventory digest"
    )
    if issue_digest != drain._inventory_manifest(issue_inventory)["sha256"]:  # noqa: SLF001
        raise DrainEligibilityIntegrityError("Clean issue inventory digest changed")
    source = _exact(
        record["source_authority"],
        {
            "outcome_source_id",
            "watermark",
            "exported_at_utc",
            "semantic_manifest",
            "activation_manifest",
            "snapshot_receipt",
            "snapshot_sequence_id",
            "records",
            "revision_ids_by_date",
        },
        name="clean source authority",
    )
    clean = drain._CleanState(  # noqa: SLF001
        old_live_epoch_id=_text(
            record["old_live_epoch_id"], name="clean old live epoch"
        ),
        live_event_count=live_count,
        live_terminal_sha256=live_terminal,
        guard_record_count=guard_count,
        trusted_time_record_count=trusted_count,
        shadow_event_count=shadow_count,
        shadow_terminal_sha256=shadow_terminal,
        issue_route_inventory_sha256=issue_digest,
        issue_route_inventory=issue_inventory,
        outcome_registry_inventory=outcome_inventory,
        guard_inventory=guard_inventory,
        trusted_time_inventory=trusted_inventory,
        source_authority=copy.deepcopy(source),
        live_entry_sha256s=live_hashes,
        shadow_entry_sha256s=shadow_hashes,
    )
    try:
        canonical = drain._clean_state_extends(clean, clean)  # noqa: SLF001
    except (TypeError, ValueError) as exc:
        raise DrainEligibilityIntegrityError("Clean state is malformed") from exc
    if not canonical:
        raise DrainEligibilityIntegrityError("Clean state is not self-consistent")
    return clean


def _clean_state_strictly_extends(
    first: drain._CleanState,
    second: drain._CleanState,  # noqa: SLF001
) -> bool:
    """Require the frozen R2b extension rules plus an immutable issue archive."""

    return (
        drain._clean_state_extends(first, second)  # noqa: SLF001
        and second.issue_route_inventory_sha256 == first.issue_route_inventory_sha256
        and second.issue_route_inventory == first.issue_route_inventory
    )


def _load_draining_assessment(
    profile: Mapping[str, Any],
    paths: EligibilityPaths,
    *,
    machine_now: datetime,
) -> _DrainingAssessment | drain._Waiting:  # noqa: SLF001
    """Replay persisted historical R2b authority, its fence, and current old state."""

    drain_profile = profile["_drain_profile"]
    try:
        binding = drain._persisted_authority_binding(paths.drain)  # noqa: SLF001
        if binding is None:
            return drain._Waiting(  # noqa: SLF001
                "waiting_for_epoch_draining",
                "R2b has no replayable epoch_drain_started authority",
            )
        authority = drain._load_authority(  # noqa: SLF001
            drain_profile,
            paths.drain,
            registry_entry_sha256=binding[0],
            preparation_entry_sha256=binding[1],
        )
        if isinstance(authority, drain._Waiting):  # noqa: SLF001
            raise DrainEligibilityIntegrityError(
                "Persisted R2b authority no longer replays"
            )
        events = drain.replay_drain(drain_profile, paths.drain, authority, None)
        if not events:
            return drain._Waiting(  # noqa: SLF001
                "waiting_for_epoch_draining",
                "R2b transaction has not published epoch_drain_started",
            )
        if len(events) != 1:
            raise DrainEligibilityIntegrityError(
                "R2b has more than one epoch_drain_started event"
            )
        event_path = drain._event_path(paths.drain)  # noqa: SLF001
        event_snapshot = registry._read_regular(  # noqa: SLF001
            event_path, name="epoch drain event", maximum_bytes=MAX_CONTROL_BYTES
        )
        event_payload = registry._decode_json(  # noqa: SLF001
            event_snapshot.raw, name="epoch drain event"
        )
        if (
            event_snapshot.raw != _canonical_bytes(event_payload)
            or event_payload != events[0]
        ):
            raise DrainEligibilityIntegrityError(
                "R2b event changed after authoritative replay"
            )
        intent = drain._load_single_intent(paths.drain)  # noqa: SLF001
        if intent is None:
            raise DrainEligibilityIntegrityError(
                "R2b DRAINING authority lost its fence intent"
            )
        intent_payload, capsule, _ = drain._verify_intent(  # noqa: SLF001
            drain_profile,
            paths.drain,
            authority,
            None,
            intent,
            require_current_implementation=False,
        )
        _, boundary_clean = drain._verify_boundary(  # noqa: SLF001
            drain_profile,
            paths.drain,
            authority,
            intent,
            capsule,
            event_payload["drain_boundary"],
        )
        archive = Path(str(intent_payload["archive_route"]))
        persisted_archive = drain._persisted_issue_archive(paths.drain)  # noqa: SLF001
        if persisted_archive != archive:
            raise DrainEligibilityIntegrityError(
                "R2b DRAINING authority changed its archived issue route"
            )
        drain._verify_draining_runtime_prefix(  # noqa: SLF001
            paths.drain, boundary_clean, archive
        )
        current = drain._load_clean_state(  # noqa: SLF001
            paths.drain,
            machine_now=machine_now,
            archived_issue_route=archive,
            allow_queued_incoming=True,
        )
        if not isinstance(current, drain._Waiting):  # noqa: SLF001
            replayed = drain.replay_drain(
                drain_profile, paths.drain, authority, current
            )
            if replayed != events:
                raise DrainEligibilityIntegrityError(
                    "R2b current replay changed its authority event"
                )
        return _DrainingAssessment(
            authority=authority,
            event=copy.deepcopy(event_payload),
            event_snapshot=event_snapshot,
            current=current,
        )
    except DrainEligibilityError:
        raise
    except (drain.EpochDrainError, registry.EpochRegistryError) as exc:
        raise DrainEligibilityIntegrityError(str(exc)) from exc


def _same_snapshot(
    first: registry.ArtifactSnapshot, second: registry.ArtifactSnapshot
) -> bool:
    return (
        first.path == second.path
        and first.raw == second.raw
        and first.sha256 == second.sha256
        and first.size_bytes == second.size_bytes
    )


def _capture_implementation(
    profile: Mapping[str, Any], paths: EligibilityPaths
) -> registry.ArtifactSnapshot:
    try:
        current = registry._read_regular(  # noqa: SLF001
            Path(profile["_project_root"]) / IMPLEMENTATION_LOGICAL_PATH,
            name="drain eligibility implementation",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
        if current.sha256 != profile["_implementation_sha256"]:
            raise DrainEligibilityIntegrityError(
                "Eligibility implementation changed during invocation"
            )
        return drain._publish_once_durable(  # noqa: SLF001
            paths.objects / f"{current.sha256}{IMPLEMENTATION_SUFFIX}",
            current.raw,
            root=paths.root,
            name="drain eligibility implementation object",
        )
    except DrainEligibilityError:
        raise
    except (drain.EpochDrainError, registry.EpochRegistryError) as exc:
        raise DrainEligibilityIntegrityError(str(exc)) from exc


def _observation_payload(
    profile: Mapping[str, Any],
    paths: EligibilityPaths,
    assessment: _DrainingAssessment,
    clean: drain._CleanState,  # noqa: SLF001
    implementation: registry.ArtifactSnapshot,
) -> dict[str, object]:
    event = assessment.event
    required = {
        "entry_sha256",
        "candidate_id",
        "old_live_epoch_id",
        "lifecycle_state",
        "canonical_old_issue_route_fenced",
        "drain_boundary",
        "exchange_attempt",
        "fence_intent",
    }
    if not required.issubset(event):
        raise DrainEligibilityIntegrityError("R2b event lost eligibility bindings")
    if event["lifecycle_state"] != "DRAINING":
        raise DrainEligibilityIntegrityError("R2b event is not DRAINING")
    if event["canonical_old_issue_route_fenced"] is not True:
        raise DrainEligibilityIntegrityError("R2b canonical route is not fenced")
    payload: dict[str, object] = {
        "schema_version": profile["protocol"]["observation_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "implementation": _artifact_reference(implementation, paths),
        "drain_profile_sha256": profile["upstream"]["drain_profile"]["expected_sha256"],
        "drain_implementation_sha256": profile["upstream"]["drain_implementation"][
            "expected_sha256"
        ],
        "drain_event": _artifact_reference(assessment.event_snapshot, paths),
        "drain_event_entry_sha256": _hash(
            event["entry_sha256"], name="R2b event entry"
        ),
        "candidate_id": _hash(event["candidate_id"], name="R2b candidate"),
        "old_live_epoch_id": _text(
            event["old_live_epoch_id"], name="R2b old live epoch"
        ),
        "drain_boundary": _reference(
            event["drain_boundary"], name="R2b drain boundary"
        ),
        "exchange_attempt": _reference(
            event["exchange_attempt"], name="R2b exchange attempt"
        ),
        "fence_intent": _reference(event["fence_intent"], name="R2b fence intent"),
        "lifecycle_state": "DRAINING",
        "clean_state": _clean_payload(clean),
        "staged_incoming_excluded_from_old_epoch_observation": True,
        "drained_eligibility_current_at_capture": True,
        "future_transition_requires_exact_recheck": True,
        "machine_only": True,
        "observation_authority_only": True,
        "lifecycle_authority": False,
        "transition_authority": False,
        "old_epoch_drained": False,
        "old_epoch_drain_implemented": False,
        "activation_candidate_selected": False,
        "active_epoch_switch_implemented": False,
        "automatic_epoch_rotation_implemented": False,
        "trusted_anchor_receipt_verified": False,
        "e2_live_evidence_eligible": False,
        "real_activation_ready": False,
        "formal_warning_output": False,
    }
    return payload


def _publish_observation(
    profile: Mapping[str, Any],
    paths: EligibilityPaths,
    assessment: _DrainingAssessment,
    clean: drain._CleanState,  # noqa: SLF001
) -> registry.ArtifactSnapshot | drain._Waiting:  # noqa: SLF001
    implementation = _capture_implementation(profile, paths)
    raw = _canonical_bytes(
        _observation_payload(profile, paths, assessment, clean, implementation)
    )
    if len(raw) > profile["protocol"]["maximum_observation_bytes"]:
        return drain._Waiting(  # noqa: SLF001
            "waiting_for_drain_eligibility_capacity",
            "machine-current clean observation exceeds the reviewed v1 capacity",
        )
    try:
        return drain._publish_once_durable(  # noqa: SLF001
            paths.objects / f"{_sha256(raw)}{OBSERVATION_SUFFIX}",
            raw,
            root=paths.root,
            name="drain eligibility observation",
        )
    except drain.EpochDrainError as exc:
        raise DrainEligibilityIntegrityError(str(exc)) from exc


def _verify_observation(
    profile: Mapping[str, Any],
    paths: EligibilityPaths,
    assessment: _DrainingAssessment,
    value: object,
) -> tuple[registry.ArtifactSnapshot, drain._CleanState]:  # noqa: SLF001
    snapshot = _read_reference(
        value,
        paths=paths,
        name="drain eligibility observation",
        maximum_bytes=MAX_OBSERVATION_BYTES,
    )
    if snapshot.path != paths.objects / f"{snapshot.sha256}{OBSERVATION_SUFFIX}":
        raise DrainEligibilityIntegrityError(
            "Drain eligibility observation path changed"
        )
    try:
        payload = registry._decode_json(  # noqa: SLF001
            snapshot.raw, name="drain eligibility observation"
        )
    except registry.EpochRegistryError as exc:
        raise DrainEligibilityIntegrityError(str(exc)) from exc
    if snapshot.raw != _canonical_bytes(payload):
        raise DrainEligibilityIntegrityError(
            "Drain eligibility observation is not canonical"
        )
    _exact(
        payload,
        {
            "schema_version",
            "profile_id",
            "profile_sha256",
            "implementation",
            "drain_profile_sha256",
            "drain_implementation_sha256",
            "drain_event",
            "drain_event_entry_sha256",
            "candidate_id",
            "old_live_epoch_id",
            "drain_boundary",
            "exchange_attempt",
            "fence_intent",
            "lifecycle_state",
            "clean_state",
            "staged_incoming_excluded_from_old_epoch_observation",
            "drained_eligibility_current_at_capture",
            "future_transition_requires_exact_recheck",
            "machine_only",
            "observation_authority_only",
            "lifecycle_authority",
            "transition_authority",
            "old_epoch_drained",
            "old_epoch_drain_implemented",
            "activation_candidate_selected",
            "active_epoch_switch_implemented",
            "automatic_epoch_rotation_implemented",
            "trusted_anchor_receipt_verified",
            "e2_live_evidence_eligible",
            "real_activation_ready",
            "formal_warning_output",
        },
        name="drain eligibility observation",
    )
    implementation = _read_reference(
        payload["implementation"],
        paths=paths,
        name="drain eligibility implementation object",
    )
    if implementation.path != (
        paths.objects / f"{implementation.sha256}{IMPLEMENTATION_SUFFIX}"
    ):
        raise DrainEligibilityIntegrityError(
            "Drain eligibility implementation object path changed"
        )
    drain_event = _read_reference(
        payload["drain_event"], paths=paths, name="bound R2b drain event"
    )
    if not _same_snapshot(drain_event, assessment.event_snapshot):
        raise DrainEligibilityIntegrityError(
            "Drain eligibility observation changed its R2b event"
        )
    clean = _clean_from_payload(payload["clean_state"])
    try:
        drain._verify_source_authority_objects(  # noqa: SLF001
            paths.drain, clean.source_authority
        )
    except drain.EpochDrainError as exc:
        raise DrainEligibilityIntegrityError(str(exc)) from exc
    expected = _observation_payload(profile, paths, assessment, clean, implementation)
    if payload != expected:
        raise DrainEligibilityIntegrityError(
            "Drain eligibility observation semantics changed"
        )
    try:
        return (
            drain._adopt_snapshot_durable(  # noqa: SLF001
                snapshot,
                root=paths.root,
                name="adopted drain eligibility observation",
            ),
            clean,
        )
    except drain.EpochDrainError as exc:
        raise DrainEligibilityIntegrityError(str(exc)) from exc


def _event_unsigned(
    profile: Mapping[str, Any],
    paths: EligibilityPaths,
    assessment: _DrainingAssessment,
    observation: registry.ArtifactSnapshot,
    *,
    sequence_id: int,
    previous_entry_sha256: str,
    recorded_at_utc: str,
) -> dict[str, object]:
    event = assessment.event
    payload: dict[str, object] = {
        "schema_version": profile["protocol"]["event_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "sequence_id": sequence_id,
        "previous_entry_sha256": previous_entry_sha256,
        "recorded_at_utc": recorded_at_utc,
        "event_type": profile["protocol"]["event_type"],
        "observation": _artifact_reference(observation, paths),
        "drain_event": _artifact_reference(assessment.event_snapshot, paths),
        "drain_event_entry_sha256": event["entry_sha256"],
        "candidate_id": event["candidate_id"],
        "old_live_epoch_id": event["old_live_epoch_id"],
        "lifecycle_state": "DRAINING",
        "drained_eligibility_current_at_publication": True,
        "future_transition_requires_exact_recheck": True,
        "machine_only": True,
        "observation_authority_only": True,
        "lifecycle_authority": False,
        "transition_authority": False,
        "old_epoch_drained": False,
        "old_epoch_drain_implemented": False,
        "activation_candidate_selected": False,
        "active_epoch_switch_implemented": False,
        "automatic_epoch_rotation_implemented": False,
        "trusted_anchor_receipt_verified": False,
        "e2_live_evidence_eligible": False,
        "real_activation_ready": False,
        "formal_warning_output": False,
    }
    return payload


def _event_path(paths: EligibilityPaths, sequence_id: int, entry: str) -> Path:
    return paths.events / f"{sequence_id:020d}-{entry}.json"


def _append_event(
    profile: Mapping[str, Any],
    paths: EligibilityPaths,
    assessment: _DrainingAssessment,
    observation: registry.ArtifactSnapshot,
    events: Sequence[Mapping[str, Any]],
    *,
    recorded_at_utc: str,
) -> tuple[dict[str, Any], Path]:
    sequence = len(events) + 1
    previous = events[-1]["entry_sha256"] if events else ZERO_HASH
    unsigned = _event_unsigned(
        profile,
        paths,
        assessment,
        observation,
        sequence_id=sequence,
        previous_entry_sha256=previous,
        recorded_at_utc=recorded_at_utc,
    )
    event: dict[str, Any] = {
        **unsigned,
        "entry_sha256": _sha256(_canonical_bytes(unsigned)),
    }
    path = _event_path(paths, sequence, event["entry_sha256"])
    try:
        drain._publish_once_durable(  # noqa: SLF001
            path,
            _canonical_bytes(event),
            root=paths.root,
            name="drain eligibility event",
        )
    except drain.EpochDrainError as exc:
        raise DrainEligibilityIntegrityError(str(exc)) from exc
    return event, path


def _event_entries(paths: EligibilityPaths) -> list[Path]:
    if not paths.events.exists() and not paths.events.is_symlink():
        return []
    try:
        mode = os.lstat(paths.events).st_mode
        if not stat.S_ISDIR(mode) or stat.S_ISLNK(mode):
            raise DrainEligibilityIntegrityError(
                "Drain eligibility events path is not a real directory"
            )
        return sorted(paths.events.iterdir(), key=lambda item: item.name)
    except DrainEligibilityError:
        raise
    except OSError as exc:
        raise DrainEligibilityIntegrityError(
            "Cannot inspect drain eligibility events"
        ) from exc


def _replay_events(
    profile: Mapping[str, Any],
    paths: EligibilityPaths,
    assessment: _DrainingAssessment,
) -> tuple[tuple[dict[str, Any], ...], tuple[drain._CleanState, ...]]:  # noqa: SLF001
    # V1 intentionally replays every content-addressed observation on every
    # poll.  This is O(K^2) across K successive polls; checkpoint/chunk/Merkle
    # acceleration requires a new protocol version and must not reinterpret V1.
    events: list[dict[str, Any]] = []
    states: list[drain._CleanState] = []  # noqa: SLF001
    previous_entry = ZERO_HASH
    previous_recorded = _parse_utc(
        assessment.event.get("recorded_at_utc"),
        name="R2b event recorded_at_utc",
    )
    for expected_sequence, path in enumerate(_event_entries(paths), start=1):
        match = EVENT_NAME.fullmatch(path.name)
        if match is None or int(match.group("sequence")) != expected_sequence:
            raise DrainEligibilityIntegrityError(
                "Drain eligibility event chain has a gap, branch, or unknown entry"
            )
        try:
            snapshot = registry._read_regular(  # noqa: SLF001
                path,
                name="drain eligibility event",
                maximum_bytes=MAX_CONTROL_BYTES,
            )
            event = registry._decode_json(  # noqa: SLF001
                snapshot.raw, name="drain eligibility event"
            )
        except registry.EpochRegistryError as exc:
            raise DrainEligibilityIntegrityError(str(exc)) from exc
        if snapshot.raw != _canonical_bytes(event):
            raise DrainEligibilityIntegrityError(
                "Drain eligibility event is not canonical"
            )
        _exact(
            event,
            {
                "schema_version",
                "profile_id",
                "profile_sha256",
                "sequence_id",
                "previous_entry_sha256",
                "recorded_at_utc",
                "event_type",
                "observation",
                "drain_event",
                "drain_event_entry_sha256",
                "candidate_id",
                "old_live_epoch_id",
                "lifecycle_state",
                "drained_eligibility_current_at_publication",
                "future_transition_requires_exact_recheck",
                "machine_only",
                "observation_authority_only",
                "lifecycle_authority",
                "transition_authority",
                "old_epoch_drained",
                "old_epoch_drain_implemented",
                "activation_candidate_selected",
                "active_epoch_switch_implemented",
                "automatic_epoch_rotation_implemented",
                "trusted_anchor_receipt_verified",
                "e2_live_evidence_eligible",
                "real_activation_ready",
                "formal_warning_output",
                "entry_sha256",
            },
            name="drain eligibility event",
        )
        sequence = _integer(
            event["sequence_id"], name="eligibility event sequence", minimum=1
        )
        if sequence != expected_sequence:
            raise DrainEligibilityIntegrityError(
                "Drain eligibility event sequence changed"
            )
        entry = _hash(event["entry_sha256"], name="eligibility event entry")
        if match.group("entry") != entry:
            raise DrainEligibilityIntegrityError(
                "Drain eligibility event filename changed"
            )
        if (
            _hash(
                event["previous_entry_sha256"],
                name="eligibility previous event entry",
            )
            != previous_entry
        ):
            raise DrainEligibilityIntegrityError(
                "Drain eligibility event chain branched or rolled back"
            )
        unsigned = {key: value for key, value in event.items() if key != "entry_sha256"}
        if entry != _sha256(_canonical_bytes(unsigned)):
            raise DrainEligibilityIntegrityError("Drain eligibility event hash changed")
        recorded = _parse_utc(
            event["recorded_at_utc"], name="eligibility event recorded_at_utc"
        )
        if recorded < previous_recorded:
            raise DrainEligibilityIntegrityError(
                "Drain eligibility event time moved backwards"
            )
        observation, clean = _verify_observation(
            profile, paths, assessment, event["observation"]
        )
        if states and (
            clean == states[-1] or not _clean_state_strictly_extends(states[-1], clean)
        ):
            raise DrainEligibilityIntegrityError(
                "Drain eligibility observations are not strict append-only extensions"
            )
        expected = {
            **_event_unsigned(
                profile,
                paths,
                assessment,
                observation,
                sequence_id=sequence,
                previous_entry_sha256=previous_entry,
                recorded_at_utc=event["recorded_at_utc"],
            ),
            "entry_sha256": entry,
        }
        if event != expected:
            raise DrainEligibilityIntegrityError(
                "Drain eligibility event semantics changed"
            )
        try:
            drain._adopt_snapshot_durable(  # noqa: SLF001
                snapshot, root=paths.root, name="adopted drain eligibility event"
            )
        except drain.EpochDrainError as exc:
            raise DrainEligibilityIntegrityError(str(exc)) from exc
        events.append(event)
        states.append(clean)
        previous_entry = entry
        previous_recorded = recorded
    return tuple(events), tuple(states)


def _head_payload(
    profile: Mapping[str, Any], event: Mapping[str, Any]
) -> dict[str, object]:
    return {
        "schema_version": profile["protocol"]["head_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "sequence_id": event["sequence_id"],
        "entry_sha256": event["entry_sha256"],
        "lifecycle_state": "DRAINING",
    }


def _read_cache(path: Path, *, name: str) -> dict[str, Any] | None:
    if not path.exists() and not path.is_symlink():
        return None
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            path, name=name, maximum_bytes=MAX_CONTROL_BYTES
        )
        payload = registry._decode_json(snapshot.raw, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise DrainEligibilityIntegrityError(str(exc)) from exc
    if snapshot.raw != _canonical_bytes(payload):
        raise DrainEligibilityIntegrityError(f"{name} is not canonical")
    return payload


def _strongest_rollback_witness(
    profile: Mapping[str, Any],
    paths: EligibilityPaths,
    events: Sequence[Mapping[str, Any]],
) -> tuple[int, str]:
    """Preserve an ahead mutable witness while replacing stale current=true."""

    candidates: list[tuple[int, int, str]] = [
        (
            len(events),
            0,
            events[-1]["entry_sha256"] if events else ZERO_HASH,
        )
    ]
    for priority, path, validator, name in (
        (1, paths.head, _head_witness, "drain eligibility head cache"),
        (2, paths.status, _status_witness, "drain eligibility status cache"),
    ):
        try:
            payload = _read_cache(path, name=name)
            if payload is None:
                continue
            sequence, entry = validator(profile, payload, events)
            candidates.append((sequence, priority, entry))
        except DrainEligibilityError:
            continue
    sequence, _, entry = max(candidates, key=lambda item: (item[0], item[1]))
    return sequence, entry


def _head_witness(
    profile: Mapping[str, Any],
    head: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> tuple[int, str]:
    _exact(
        head,
        {
            "schema_version",
            "profile_id",
            "profile_sha256",
            "sequence_id",
            "entry_sha256",
            "lifecycle_state",
        },
        name="drain eligibility head cache",
    )
    sequence = _integer(
        head["sequence_id"], name="eligibility head sequence", minimum=1
    )
    entry = _hash(head["entry_sha256"], name="eligibility head entry")
    if (
        head["schema_version"] != profile["protocol"]["head_schema_version"]
        or head["profile_id"] != profile["profile_id"]
        or head["profile_sha256"] != profile["_profile_sha256"]
        or head["lifecycle_state"] != "DRAINING"
    ):
        raise DrainEligibilityIntegrityError(
            "Drain eligibility head cache semantics changed"
        )
    if sequence <= len(events) and entry != events[sequence - 1]["entry_sha256"]:
        raise DrainEligibilityIntegrityError("Drain eligibility head cache changed")
    return sequence, entry


def _status_witness(
    profile: Mapping[str, Any],
    status: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> tuple[int, str]:
    _exact(
        status,
        {
            "schema_version",
            "profile_id",
            "profile_sha256",
            "checked_at_utc",
            "eligibility_status",
            "reason",
            "lifecycle_state",
            "event_count",
            "terminal_entry_sha256",
            "drained_eligibility_current",
            "machine_only",
            "cache_authority",
            "lifecycle_authority",
            "transition_authority",
            "old_epoch_drained",
            "old_epoch_drain_implemented",
            "activation_candidate_selected",
            "active_epoch_switch_implemented",
            "automatic_epoch_rotation_implemented",
            "trusted_anchor_receipt_verified",
            "e2_live_evidence_eligible",
            "real_activation_ready",
            "formal_warning_output",
        },
        name="drain eligibility status cache",
    )
    checked_at = _parse_utc(
        status["checked_at_utc"], name="eligibility status checked_at_utc"
    )
    event_count = _integer(status["event_count"], name="eligibility status event count")
    terminal = _hash(
        status["terminal_entry_sha256"], name="eligibility status terminal"
    )
    eligibility_status = status["eligibility_status"]
    current_statuses = {
        "drain_eligibility_observed",
        "drain_eligibility_current_idempotent",
        "drain_eligibility_observation_refreshed",
    }
    reachable_status = (
        (
            eligibility_status == "waiting_for_epoch_draining"
            and status["lifecycle_state"] == "PREPARING"
            and event_count == 0
        )
        or (
            eligibility_status
            in {
                "waiting_for_clean_drain_eligibility",
                "waiting_for_drain_eligibility_capacity",
            }
            and status["lifecycle_state"] == "DRAINING"
        )
        or (
            eligibility_status == "drain_eligibility_observed"
            and status["lifecycle_state"] == "DRAINING"
            and event_count == 1
        )
        or (
            eligibility_status == "drain_eligibility_current_idempotent"
            and status["lifecycle_state"] == "DRAINING"
            and event_count >= 1
        )
        or (
            eligibility_status == "drain_eligibility_observation_refreshed"
            and status["lifecycle_state"] == "DRAINING"
            and event_count >= 2
        )
        or (
            eligibility_status == "blocked_integrity"
            and (
                status["lifecycle_state"] == "DRAINING"
                or (status["lifecycle_state"] == "PREPARING" and event_count == 0)
            )
        )
    )
    if (
        status["schema_version"] != profile["protocol"]["status_schema_version"]
        or status["profile_id"] != profile["profile_id"]
        or status["profile_sha256"] != profile["_profile_sha256"]
        or status["lifecycle_state"] not in {"PREPARING", "DRAINING"}
        or eligibility_status
        not in {
            "waiting_for_epoch_draining",
            "waiting_for_clean_drain_eligibility",
            "waiting_for_drain_eligibility_capacity",
            "drain_eligibility_observed",
            "drain_eligibility_current_idempotent",
            "drain_eligibility_observation_refreshed",
            "blocked_integrity",
        }
        or _text(status["reason"], name="eligibility status reason") != status["reason"]
        or type(status["drained_eligibility_current"]) is not bool
        or status["drained_eligibility_current"]
        != (eligibility_status in current_statuses)
        or not reachable_status
        or status["machine_only"] is not True
        or status["cache_authority"] is not False
        or any(status[claim] is not False for claim in FALSE_CLAIMS)
    ):
        raise DrainEligibilityIntegrityError(
            "Drain eligibility status cache semantics changed"
        )
    if event_count == 0 and terminal != ZERO_HASH:
        raise DrainEligibilityIntegrityError(
            "Drain eligibility empty status terminal changed"
        )
    if events and event_count > 0 and eligibility_status != "blocked_integrity":
        referenced = events[min(event_count, len(events)) - 1]
        if checked_at < _parse_utc(
            referenced["recorded_at_utc"],
            name="eligibility status referenced event recorded_at_utc",
        ):
            raise DrainEligibilityIntegrityError(
                "Drain eligibility status predates its referenced event"
            )
    if event_count <= len(events):
        expected = events[event_count - 1]["entry_sha256"] if event_count else ZERO_HASH
        if terminal != expected:
            raise DrainEligibilityIntegrityError(
                "Drain eligibility status cache terminal changed"
            )
    return event_count, terminal


def _validate_caches(
    profile: Mapping[str, Any],
    paths: EligibilityPaths,
    events: Sequence[Mapping[str, Any]],
) -> None:
    # Head/status are repairable cache witnesses.  They detect an event suffix
    # deletion while either witness remains, but cannot prove against an
    # adversary deleting the suffix and mutable witnesses together.
    head = _read_cache(paths.head, name="drain eligibility head cache")
    if head is not None:
        sequence, _ = _head_witness(profile, head, events)
        if sequence > len(events):
            raise DrainEligibilityIntegrityError(
                "Drain eligibility event suffix was rolled back behind its head"
            )

    status = _read_cache(paths.status, name="drain eligibility status cache")
    if status is None:
        return
    event_count, _ = _status_witness(profile, status, events)
    if event_count > len(events):
        raise DrainEligibilityIntegrityError(
            "Drain eligibility event suffix was rolled back behind its status"
        )


def _refresh_head(
    profile: Mapping[str, Any],
    paths: EligibilityPaths,
    events: Sequence[Mapping[str, Any]],
) -> None:
    if not events:
        return
    try:
        registry._atomic_cache(  # noqa: SLF001
            paths.head,
            _canonical_bytes(_head_payload(profile, events[-1])),
            root=paths.root,
            name="drain eligibility head cache",
        )
    except registry.EpochRegistryError as exc:
        raise DrainEligibilityIntegrityError(str(exc)) from exc


def _status_payload(
    profile: Mapping[str, Any],
    *,
    checked_at_utc: str,
    status: str,
    reason: str,
    lifecycle_state: str,
    events: Sequence[Mapping[str, Any]],
    drained_eligibility_current: bool,
    witnessed_event_count: int | None = None,
    witnessed_terminal_entry_sha256: str | None = None,
) -> dict[str, object]:
    tip = events[-1] if events else None
    event_count = len(events)
    terminal_entry = tip["entry_sha256"] if tip else ZERO_HASH
    if witnessed_event_count is not None:
        if witnessed_terminal_entry_sha256 is None:
            raise DrainEligibilityIntegrityError(
                "Blocked status witness lost its terminal entry"
            )
        event_count = witnessed_event_count
        terminal_entry = witnessed_terminal_entry_sha256
    return {
        "schema_version": profile["protocol"]["status_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "checked_at_utc": checked_at_utc,
        "eligibility_status": status,
        "reason": reason,
        "lifecycle_state": lifecycle_state,
        "event_count": event_count,
        "terminal_entry_sha256": terminal_entry,
        "drained_eligibility_current": drained_eligibility_current,
        "machine_only": True,
        "cache_authority": False,
        "lifecycle_authority": False,
        "transition_authority": False,
        "old_epoch_drained": False,
        "old_epoch_drain_implemented": False,
        "activation_candidate_selected": False,
        "active_epoch_switch_implemented": False,
        "automatic_epoch_rotation_implemented": False,
        "trusted_anchor_receipt_verified": False,
        "e2_live_evidence_eligible": False,
        "real_activation_ready": False,
        "formal_warning_output": False,
    }


def _write_status(
    profile: Mapping[str, Any], paths: EligibilityPaths, payload: Mapping[str, object]
) -> Path:
    try:
        registry._atomic_cache(  # noqa: SLF001
            paths.status,
            _canonical_bytes(payload),
            root=paths.root,
            name="drain eligibility status cache",
        )
    except registry.EpochRegistryError as exc:
        raise DrainEligibilityIntegrityError(str(exc)) from exc
    return paths.status


def _waiting_result(
    profile: Mapping[str, Any],
    paths: EligibilityPaths,
    *,
    checked_at_utc: str,
    status: str,
    reason: str,
    lifecycle_state: str = "PREPARING",
    events: Sequence[Mapping[str, Any]] = (),
) -> DrainEligibilityResult:
    _refresh_head(profile, paths, events)
    status_path = _write_status(
        profile,
        paths,
        _status_payload(
            profile,
            checked_at_utc=checked_at_utc,
            status=status,
            reason=reason,
            lifecycle_state=lifecycle_state,
            events=events,
            drained_eligibility_current=False,
        ),
    )
    return DrainEligibilityResult(status, status_path, None)


def _current_result(
    profile: Mapping[str, Any],
    paths: EligibilityPaths,
    *,
    checked_at_utc: str,
    status: str,
    reason: str,
    events: Sequence[Mapping[str, Any]],
    event_path: Path | None,
) -> DrainEligibilityResult:
    _refresh_head(profile, paths, events)
    status_path = _write_status(
        profile,
        paths,
        _status_payload(
            profile,
            checked_at_utc=checked_at_utc,
            status=status,
            reason=reason,
            lifecycle_state="DRAINING",
            events=events,
            drained_eligibility_current=True,
        ),
    )
    return DrainEligibilityResult(status, status_path, event_path)


def _same_draining_authority(
    first: _DrainingAssessment, second: _DrainingAssessment
) -> bool:
    return first.event == second.event and _same_snapshot(
        first.event_snapshot, second.event_snapshot
    )


def _require_not_before_authority(
    observed: datetime,
    assessment: _DrainingAssessment,
    events: Sequence[Mapping[str, Any]],
) -> None:
    lower_bound = _parse_utc(
        assessment.event.get("recorded_at_utc"),
        name="R2b event recorded_at_utc",
    )
    if events:
        lower_bound = max(
            lower_bound,
            _parse_utc(
                events[-1]["recorded_at_utc"],
                name="eligibility tip recorded_at_utc",
            ),
        )
    if observed < lower_bound:
        raise DrainEligibilityIntegrityError(
            "Drain eligibility machine time precedes its bound authority"
        )


def _validate_private_roots(
    runtime_root: Path | None,
    active_runtime_root: Path | None,
    shadow_runtime_root: Path | None,
    clock: Clock | None,
) -> None:
    try:
        drain._validate_private_roots(  # noqa: SLF001
            runtime_root, active_runtime_root, shadow_runtime_root, clock
        )
    except drain.EpochDrainError as exc:
        raise DrainEligibilityConfigError(str(exc)) from exc


def _observe_drain_eligibility(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    runtime_root: Path | None = None,
    active_runtime_root: Path | None = None,
    shadow_runtime_root: Path | None = None,
    clock: Clock | None = None,
) -> DrainEligibilityResult:
    _validate_private_roots(
        runtime_root, active_runtime_root, shadow_runtime_root, clock
    )
    profile = load_eligibility_profile(config_path)
    paths = eligibility_paths(
        profile,
        runtime_root=runtime_root,
        active_runtime_root=active_runtime_root,
        shadow_runtime_root=shadow_runtime_root,
    )
    observed_clock = _ObservedClock(clock or (lambda: datetime.now(timezone.utc)))
    try:
        registry._mkdir(paths.root, root=paths.root)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise DrainEligibilityIntegrityError(str(exc)) from exc
    handles: list[BinaryIO] = []
    status_time: datetime | None = None
    status_lifecycle = "PREPARING"
    status_events: tuple[dict[str, Any], ...] = ()
    try:
        for label, path in (
            ("manager", paths.manager_lock),
            ("cycle", paths.cycle_lock),
            ("deploy", paths.deploy_lock),
            ("runner", paths.runner_lock),
            ("replay", paths.replay_lock),
            ("shadow", paths.shadow_lock),
        ):
            try:
                handles.append(drain._acquire_lock(path, label=label))  # noqa: SLF001
            except drain.EpochDrainBusyError as exc:
                raise DrainEligibilityBusyError(str(exc)) from exc
            except drain.EpochDrainError as exc:
                raise DrainEligibilityIntegrityError(str(exc)) from exc
        try:
            registry._cleanup_temporary_namespace(paths.root)  # noqa: SLF001
        except registry.EpochRegistryError as exc:
            raise DrainEligibilityIntegrityError(str(exc)) from exc
        initial_now = observed_clock.sample()
        status_time = initial_now
        assessment = _load_draining_assessment(profile, paths, machine_now=initial_now)
        if isinstance(assessment, drain._Waiting):  # noqa: SLF001
            entries = _event_entries(paths)
            if entries:
                raise DrainEligibilityIntegrityError(
                    "Eligibility observations outlived their R2b authority"
                )
            _validate_caches(profile, paths, ())
            return _waiting_result(
                profile,
                paths,
                checked_at_utc=_utc_text(initial_now),
                status="waiting_for_epoch_draining",
                reason=assessment.reason,
            )
        status_lifecycle = "DRAINING"
        events, states = _replay_events(profile, paths, assessment)
        status_events = events
        _validate_caches(profile, paths, events)
        _require_not_before_authority(initial_now, assessment, events)
        if isinstance(assessment.current, drain._Waiting):  # noqa: SLF001
            return _waiting_result(
                profile,
                paths,
                checked_at_utc=_utc_text(initial_now),
                status="waiting_for_clean_drain_eligibility",
                reason=assessment.current.reason,
                lifecycle_state="DRAINING",
                events=events,
            )

        current = assessment.current
        if states:
            prior = states[-1]
            if current == prior:
                return _current_result(
                    profile,
                    paths,
                    checked_at_utc=_utc_text(initial_now),
                    status="drain_eligibility_current_idempotent",
                    reason="machine-current old runtime exactly matches the terminal observation",
                    events=events,
                    event_path=None,
                )
            if not _clean_state_strictly_extends(prior, current):
                raise DrainEligibilityIntegrityError(
                    "Machine-current old runtime rolled back or replaced an observation"
                )

        observation = _publish_observation(profile, paths, assessment, current)
        if isinstance(observation, drain._Waiting):  # noqa: SLF001
            return _waiting_result(
                profile,
                paths,
                checked_at_utc=_utc_text(initial_now),
                status=observation.status,
                reason=observation.reason,
                lifecycle_state="DRAINING",
                events=events,
            )
        final_now = observed_clock.sample()
        status_time = final_now
        final_assessment = _load_draining_assessment(
            profile, paths, machine_now=final_now
        )
        if isinstance(final_assessment, drain._Waiting):  # noqa: SLF001
            raise DrainEligibilityIntegrityError(
                "R2b authority disappeared during eligibility capture"
            )
        if not _same_draining_authority(assessment, final_assessment):
            raise DrainEligibilityIntegrityError(
                "R2b authority changed during eligibility capture"
            )
        _require_not_before_authority(final_now, final_assessment, events)
        if isinstance(final_assessment.current, drain._Waiting):  # noqa: SLF001
            return _waiting_result(
                profile,
                paths,
                checked_at_utc=_utc_text(final_now),
                status="waiting_for_clean_drain_eligibility",
                reason=final_assessment.current.reason,
                lifecycle_state="DRAINING",
                events=events,
            )
        final_clean = final_assessment.current
        if final_clean != current:
            if _clean_state_strictly_extends(current, final_clean):
                return _waiting_result(
                    profile,
                    paths,
                    checked_at_utc=_utc_text(final_now),
                    status="waiting_for_clean_drain_eligibility",
                    reason="old runtime advanced during exact eligibility capture",
                    lifecycle_state="DRAINING",
                    events=events,
                )
            raise DrainEligibilityIntegrityError(
                "Old runtime rolled back or changed during eligibility capture"
            )
        # Re-read the executing implementation before creating authority.  The
        # observation already captured these bytes in CAS; a concurrent source
        # edit must leave only an orphan object, never an eligibility event.
        _capture_implementation(profile, paths)
        event, event_path = _append_event(
            profile,
            paths,
            final_assessment,
            observation,
            events,
            recorded_at_utc=_utc_text(final_now),
        )
        replayed, replayed_states = _replay_events(profile, paths, final_assessment)
        if (
            len(replayed) != len(events) + 1
            or replayed[-1] != event
            or replayed_states[-1] != final_clean
        ):
            raise DrainEligibilityIntegrityError(
                "Published drain eligibility event failed replay"
            )
        status = (
            "drain_eligibility_observed"
            if not events
            else "drain_eligibility_observation_refreshed"
        )
        return _current_result(
            profile,
            paths,
            checked_at_utc=_utc_text(final_now),
            status=status,
            reason="machine-current clean DRAINING state was observed without transition authority",
            events=replayed,
            event_path=event_path,
        )
    except DrainEligibilityBusyError:
        raise
    except Exception as exc:
        normalized = (
            exc
            if isinstance(exc, DrainEligibilityError)
            else DrainEligibilityIntegrityError(
                f"Drain eligibility observer failed:{type(exc).__name__}:{exc}"
            )
        )
        if status_time is not None:
            try:
                witnessed_count, witnessed_terminal = _strongest_rollback_witness(
                    profile, paths, status_events
                )
                witnessed_lifecycle = (
                    "DRAINING" if witnessed_count > 0 else status_lifecycle
                )
                _write_status(
                    profile,
                    paths,
                    _status_payload(
                        profile,
                        checked_at_utc=_utc_text(status_time),
                        status="blocked_integrity",
                        reason=f"{type(normalized).__name__}:{normalized}",
                        lifecycle_state=witnessed_lifecycle,
                        events=status_events,
                        drained_eligibility_current=False,
                        witnessed_event_count=witnessed_count,
                        witnessed_terminal_entry_sha256=witnessed_terminal,
                    ),
                )
            except DrainEligibilityError:
                pass
        raise normalized
    finally:
        try:
            drain._release_locks(handles)  # noqa: SLF001
        except drain.EpochDrainError as exc:
            raise DrainEligibilityIntegrityError(str(exc)) from exc


def observe_drain_eligibility(
    *, config_path: Path = DEFAULT_CONFIG_PATH
) -> DrainEligibilityResult:
    """Observe eligibility from reviewed machine-owned configuration only."""

    return _observe_drain_eligibility(config_path=config_path)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = observe_drain_eligibility(config_path=args.config)
    except DrainEligibilityBusyError as exc:
        print(f"[ootang-epoch-drain-eligibility] busy: {exc}", file=sys.stderr)
        return 3
    except DrainEligibilityError as exc:
        print(f"[ootang-epoch-drain-eligibility] blocked: {exc}", file=sys.stderr)
        return 2
    print(result.status_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
