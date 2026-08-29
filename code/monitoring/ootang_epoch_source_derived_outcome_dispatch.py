"""Materialize one source-derived historical outcome from a published overlay.

This coordinator is deliberately narrower than recovery-v6.  It deep-replays
the exact N+1 source and the published effective-workset overlay, dispatches at
most one D/R row whose sole dependency is the source-ingest expansion gate,
and records its own non-terminal receipt.  It never fabricates a recovery
reservation and never treats the source parent or the effective key as terminal.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
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
from monitoring import ootang_live_source as live_source  # noqa: E402
from monitoring import ootang_outcome_materializer as outcomes  # noqa: E402
from monitoring import (  # noqa: E402
    ootang_epoch_source_derived_workset_overlay as overlay,
)


DEFAULT_CONFIG_PATH = (
    ROOT / "config" / "ootang_epoch_source_derived_outcome_dispatch.v1.json"
)
DEFAULT_CONFIG_SHA256 = (
    "5d9d85174620cd29b3b216fdd3cec9a9b61d05176cb8a16697f5be3af112a995"
)
IMPLEMENTATION_LOGICAL_PATH = (
    "code/monitoring/ootang_epoch_source_derived_outcome_dispatch.py"
)
MAX_CONTROL_BYTES = 4 * 1024 * 1024
ZERO_HASH = "0" * 64
LOCK_ORDER = recovery.LOCK_ORDER
RECORD_NAME = re.compile(r"^(?P<step>[0-9a-f]{64})\.json$")
EVENT_NAME = re.compile(r"^(?P<sequence>[0-9]{20})-(?P<entry>[0-9a-f]{64})\.json$")

EXPECTED_UPSTREAM = {
    "overlay_profile": {
        "path": "config/ootang_epoch_source_derived_workset_overlay.v1.json",
        "expected_sha256": overlay.DEFAULT_CONFIG_SHA256,
    },
    "overlay_implementation": {
        "path": "code/monitoring/ootang_epoch_source_derived_workset_overlay.py",
        "expected_sha256": "54ea77652bc5f020146b777d98cd34e1ec26895f363d35a4cb5933b93ef11be7",
    },
    "recovery_profile": {
        "path": "config/ootang_epoch_workset_recovery.v1.json",
        "expected_sha256": recovery.DEFAULT_CONFIG_SHA256,
    },
    "recovery_implementation": {
        "path": "code/monitoring/ootang_epoch_workset_recovery.py",
        "expected_sha256": "b9f25133eef9cb94fb255bab588edcd573f0fa792ef82c4ddb9068d0a44a6c51",
    },
    "materializer_profile": {
        "path": "config/ootang_prequential_cycle.v1.json",
        "expected_sha256": outcomes.DEFAULT_CONFIG_SHA256,
    },
    "materializer_implementation": {
        "path": "code/monitoring/ootang_outcome_materializer.py",
        "expected_sha256": "2a6478c191fdf1cd0df7d78324c6e4d7637241683956de621fd319feb3bcb642",
    },
}
EXPECTED_RUNTIME = {
    "registry_root": "runtime/ootang_epoch_registry_v1",
    "recovery_namespace": "workset_recovery_v1",
    "namespace": "source_derived_outcome_dispatch_v1",
    "intents": "intents",
    "receipts": "receipts",
    "events": "events",
    "status": "status.json",
    "manager_lock": "manager.lock",
    "active_root": "runtime/ootang_prequential_live_v1",
    "shadow_root": "runtime/ootang_prequential_calibration_shadow_v1",
}
EXPECTED_PROTOCOL = {
    "intent_schema_version": "ootang_epoch_source_derived_outcome_dispatch_intent_v1",
    "receipt_schema_version": "ootang_epoch_source_derived_outcome_dispatch_receipt_v1",
    "event_schema_version": "ootang_epoch_source_derived_outcome_dispatch_event_v1",
    "status_schema_version": "ootang_epoch_source_derived_outcome_dispatch_status_v1",
    "event_type": "epoch_source_derived_outcome_materialization_recorded",
    "action": "outcome_materialized",
    "next_action": "outcome_or_revision_consumed",
    "canonical_json": "utf8_sort_keys_compact_no_nan_trailing_lf",
    "initial_previous_entry_sha256": ZERO_HASH,
    "surviving_lock_order": list(LOCK_ORDER),
    "readiness_policy": "effective_d_or_r_with_exact_source_parent_as_sole_dependency",
    "poll_policy": "heal_or_materialize_at_most_one_source_derived_effective_key",
    "maximum_control_bytes": MAX_CONTROL_BYTES,
}
TRUE_CAPABILITIES = (
    "machine_only",
    "published_effective_overlay_deep_verified",
    "historical_successor_source_deep_verified",
    "source_expansion_gate_verified",
    "source_derived_d_or_r_materialization_dispatch_implemented",
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
    "source_derived_reservation_mutated",
    "cross_freeze_authority_mutated",
    "source_ingest_executed",
    "current_source_pointer_used_as_historical_cas",
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


class SourceDerivedOutcomeDispatchError(RuntimeError):
    """Base error for the source-derived outcome dispatcher."""


class SourceDerivedOutcomeDispatchConfigError(SourceDerivedOutcomeDispatchError):
    """The reviewed dispatcher profile or a pinned dependency changed."""


class SourceDerivedOutcomeDispatchIntegrityError(SourceDerivedOutcomeDispatchError):
    """An authority, historical plan, or durable dispatcher record failed closed."""


class SourceDerivedOutcomeDispatchBusyError(SourceDerivedOutcomeDispatchError):
    """A surviving globally ordered lock is held."""


@dataclass(frozen=True)
class SourceDerivedOutcomeDispatchPaths:
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
    overlay: overlay.SourceDerivedWorksetOverlayPaths
    recovery: recovery.RecoveryPaths


@dataclass(frozen=True)
class SourceDerivedOutcomeDispatchResult:
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
class HistoricalMaterializationPlan:
    item: Mapping[str, Any]
    contract: Mapping[str, Any]


@dataclass(frozen=True)
class _Candidate:
    item: Mapping[str, Any]
    kind: str
    topological_index: int


FaultHook = Callable[[str], None]
BuildPlan = Callable[
    [Mapping[str, Any], recovery.Reservation, object, datetime],
    HistoricalMaterializationPlan,
]
MaterializeAction = Callable[
    [Mapping[str, Any], recovery.Reservation, Mapping[str, Any], datetime],
    recovery.ActionOutput,
]


def _canonical_bytes(value: object) -> bytes:
    try:
        return registry._canonical_bytes(value)  # type: ignore[arg-type]  # noqa: SLF001
    except (TypeError, ValueError) as exc:
        raise SourceDerivedOutcomeDispatchIntegrityError(
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
        raise SourceDerivedOutcomeDispatchIntegrityError(f"{name} keys changed")
    return value


def _text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise SourceDerivedOutcomeDispatchIntegrityError(f"{name} changed")
    return value


def _hash(value: object, *, name: str) -> str:
    result = _text(value, name=name)
    if len(result) != 64 or any(char not in "0123456789abcdef" for char in result):
        raise SourceDerivedOutcomeDispatchIntegrityError(
            f"{name} is not a lowercase SHA-256"
        )
    return result


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SourceDerivedOutcomeDispatchIntegrityError(
            "Source-derived outcome dispatch clock must be timezone-aware"
        )
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: object, *, name: str) -> datetime:
    try:
        return registry._utc_timestamp(value, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise SourceDerivedOutcomeDispatchIntegrityError(str(exc)) from exc


def _child(root: Path, relative: str, *, name: str) -> Path:
    try:
        return registry._contained(root, relative, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise SourceDerivedOutcomeDispatchConfigError(str(exc)) from exc


def _strict_json(
    path: Path, *, name: str
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    try:
        return manifest._read_json(  # noqa: SLF001
            path, name=name, maximum_bytes=MAX_CONTROL_BYTES
        )
    except manifest.WorksetManifestError as exc:
        raise SourceDerivedOutcomeDispatchIntegrityError(str(exc)) from exc


def _strict_entries(directory: Path, *, name: str) -> tuple[Path, ...]:
    try:
        return manifest._strict_entries(directory, name=name)  # noqa: SLF001
    except manifest.WorksetManifestError as exc:
        raise SourceDerivedOutcomeDispatchIntegrityError(str(exc)) from exc


def _publish(
    path: Path, raw: bytes, *, root: Path, name: str
) -> registry.ArtifactSnapshot:
    if len(raw) > MAX_CONTROL_BYTES:
        raise SourceDerivedOutcomeDispatchIntegrityError(f"{name} capacity exceeded")
    try:
        return overlay.drain._publish_once_durable(  # type: ignore[attr-defined]  # noqa: SLF001
            path, raw, root=root, name=name
        )
    except Exception as exc:
        raise SourceDerivedOutcomeDispatchIntegrityError(
            f"{name} publication failed:{type(exc).__name__}:{exc}"
        ) from exc


def _reference(snapshot: registry.ArtifactSnapshot, root: Path) -> dict[str, object]:
    try:
        relative = snapshot.path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise SourceDerivedOutcomeDispatchIntegrityError(
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
            name="source-derived outcome dispatcher implementation",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
    except registry.EpochRegistryError as exc:
        raise SourceDerivedOutcomeDispatchIntegrityError(str(exc)) from exc
    return {
        "path": IMPLEMENTATION_LOGICAL_PATH,
        "sha256": snapshot.sha256,
        "size_bytes": snapshot.size_bytes,
    }


def load_source_derived_outcome_dispatch_profile(
    path: Path = DEFAULT_CONFIG_PATH, *, project_root: Path = ROOT
) -> dict[str, Any]:
    root = project_root.resolve()
    resolved = path if path.is_absolute() else root / path
    if root != ROOT.resolve() or resolved.resolve() != DEFAULT_CONFIG_PATH.resolve():
        raise SourceDerivedOutcomeDispatchConfigError(
            "Only the reviewed default source-derived dispatcher profile is accepted"
        )
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            resolved,
            name="source-derived outcome dispatcher profile",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
        profile = registry._decode_json(  # noqa: SLF001
            snapshot.raw, name="source-derived outcome dispatcher profile"
        )
    except registry.EpochRegistryError as exc:
        raise SourceDerivedOutcomeDispatchConfigError(str(exc)) from exc
    if snapshot.sha256 != DEFAULT_CONFIG_SHA256:
        raise SourceDerivedOutcomeDispatchConfigError(
            "Source-derived outcome dispatcher profile SHA-256 changed"
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
        name="source-derived outcome dispatcher profile",
    )
    if (
        profile["schema_version"]
        != "ootang_epoch_source_derived_outcome_dispatch_profile_v1"
        or profile["profile_id"] != "ootang-epoch-source-derived-outcome-dispatch-v1"
        or profile["case"] != "ootang"
        or profile["formal_warning_output"] is not False
        or profile["default_pipeline_member"] is not False
        or profile["upstream"] != EXPECTED_UPSTREAM
        or profile["runtime"] != EXPECTED_RUNTIME
        or profile["protocol"] != EXPECTED_PROTOCOL
        or profile["engineering_capabilities"] != _claims(materialized=False)
    ):
        raise SourceDerivedOutcomeDispatchConfigError(
            "Source-derived outcome dispatcher profile semantics changed"
        )
    for binding in EXPECTED_UPSTREAM.values():
        upstream = _child(root, binding["path"], name="dispatcher upstream")
        try:
            actual = registry._read_regular(  # noqa: SLF001
                upstream, name="dispatcher upstream", maximum_bytes=64 * 1024 * 1024
            )
        except registry.EpochRegistryError as exc:
            raise SourceDerivedOutcomeDispatchConfigError(str(exc)) from exc
        if actual.sha256 != binding["expected_sha256"]:
            raise SourceDerivedOutcomeDispatchConfigError(
                f"Pinned dispatcher upstream changed:{binding['path']}"
            )
    profile["_profile_sha256"] = snapshot.sha256
    return profile


def source_derived_outcome_dispatch_paths(
    profile: Mapping[str, Any],
    *,
    registry_root: Path | None = None,
    active_root: Path | None = None,
    shadow_root: Path | None = None,
) -> SourceDerivedOutcomeDispatchPaths:
    registry_path = (
        registry_root or ROOT / profile["runtime"]["registry_root"]
    ).resolve()
    recovery_root = _child(
        registry_path, profile["runtime"]["recovery_namespace"], name="recovery root"
    )
    root = _child(recovery_root, profile["runtime"]["namespace"], name="dispatch root")
    active = (active_root or ROOT / profile["runtime"]["active_root"]).resolve()
    shadow = (shadow_root or ROOT / profile["runtime"]["shadow_root"]).resolve()
    overlay_profile = overlay.load_source_derived_workset_overlay_profile()
    overlay_paths = overlay.source_derived_workset_overlay_paths(
        overlay_profile,
        registry_root=registry_path,
        active_root=active,
        shadow_root=shadow,
    )
    return SourceDerivedOutcomeDispatchPaths(
        registry_root=registry_path,
        root=root,
        intents=_child(root, profile["runtime"]["intents"], name="dispatch intents"),
        receipts=_child(root, profile["runtime"]["receipts"], name="dispatch receipts"),
        events=_child(root, profile["runtime"]["events"], name="dispatch events"),
        status=_child(root, profile["runtime"]["status"], name="dispatch status"),
        manager_lock=_child(
            registry_path, profile["runtime"]["manager_lock"], name="manager lock"
        ),
        active_root=active,
        shadow_root=shadow,
        cycle_lock=_child(active, "prequential_cycle.lock", name="cycle lock"),
        replay_lock=_child(active, "issue_replay.lock", name="replay lock"),
        shadow_lock=_child(shadow, "runner.lock", name="shadow lock"),
        overlay=overlay_paths,
        recovery=overlay_paths.recovery,
    )


def _acquire_locks(paths: SourceDerivedOutcomeDispatchPaths) -> list[BinaryIO]:
    proxy = paths.overlay
    try:
        return overlay._acquire_locks(proxy)  # noqa: SLF001
    except overlay.SourceDerivedWorksetOverlayBusyError as exc:
        raise SourceDerivedOutcomeDispatchBusyError(str(exc)) from exc
    except overlay.SourceDerivedWorksetOverlayError as exc:
        raise SourceDerivedOutcomeDispatchIntegrityError(str(exc)) from exc


def _release_locks(handles: Sequence[BinaryIO]) -> None:
    try:
        overlay._release_locks(handles)  # noqa: SLF001
    except overlay.SourceDerivedWorksetOverlayError as exc:
        raise SourceDerivedOutcomeDispatchIntegrityError(str(exc)) from exc


def _artifact_payload(artifact: object) -> dict[str, object]:
    return {
        "path": str(Path(getattr(artifact, "path")).resolve()),
        "sha256": _hash(getattr(artifact, "sha256"), name="artifact digest"),
        "size_bytes": getattr(artifact, "size_bytes"),
    }


def _read_exact_artifact(artifact: object, *, name: str) -> None:
    path = Path(getattr(artifact, "path")).resolve()
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            path, name=name, maximum_bytes=64 * 1024 * 1024
        )
    except registry.EpochRegistryError as exc:
        raise SourceDerivedOutcomeDispatchIntegrityError(str(exc)) from exc
    if snapshot.sha256 != getattr(artifact, "sha256") or snapshot.size_bytes != getattr(
        artifact, "size_bytes"
    ):
        raise SourceDerivedOutcomeDispatchIntegrityError(f"{name} CAS changed")


def _validate_historical_artifacts(
    item: Mapping[str, Any],
    reservation: recovery.Reservation,
    successor: object,
    successor_receipt_payload: Mapping[str, Any],
    input_payload: Mapping[str, Any],
    *,
    target: date,
) -> None:
    """Verify immutable N+1 bindings without reading the mutable public pointer."""

    artifacts = item.get("artifacts")
    if not isinstance(artifacts, list):
        raise SourceDerivedOutcomeDispatchIntegrityError(
            "Historical outcome item artifacts changed type"
        )
    by_role: dict[str, list[Mapping[str, Any]]] = {}
    for value in artifacts:
        checked = _exact(
            value,
            {"role", "root", "path", "sha256", "size_bytes"},
            name="historical outcome artifact",
        )
        by_role.setdefault(_text(checked["role"], name="artifact role"), []).append(
            checked
        )
    roles = {
        "current_source_pointer",
        "activation_source_manifest",
        "current_source_semantic_manifest",
        "current_source_revision_head",
        "current_source_snapshot_receipt",
    }
    if set(by_role) != roles or any(len(values) != 1 for values in by_role.values()):
        raise SourceDerivedOutcomeDispatchIntegrityError(
            "Historical outcome artifact role set changed"
        )
    source_profile = outcomes.load_config()["_deploy_profile"]
    public_pointer = live_source._runtime_path(  # noqa: SLF001
        source_profile, "current_source_pointer", root=reservation.paths.active_root
    ).resolve()
    pointer = by_role["current_source_pointer"][0]
    if (
        pointer["root"] != "active"
        or recovery._artifact_path(pointer, reservation) != public_pointer  # noqa: SLF001
    ):
        raise SourceDerivedOutcomeDispatchIntegrityError(
            "Historical pointer logical path changed"
        )
    pointer_object = successor_receipt_payload.get("source_pointer")
    if not isinstance(pointer_object, Mapping):
        raise SourceDerivedOutcomeDispatchIntegrityError(
            "Historical pointer object binding changed"
        )
    try:
        objects = live_source._runtime_path(  # noqa: SLF001
            source_profile, "objects", root=reservation.paths.active_root
        )
        historical_pointer = live_source._content_object_from_payload(  # noqa: SLF001
            pointer_object,
            name="historical source pointer object",
            directory=objects,
            suffix="source-pointer.json",
        )
        live_source._read_verified_artifact(  # noqa: SLF001
            historical_pointer, name="historical source pointer object"
        )
    except live_source.SourceError as exc:
        raise SourceDerivedOutcomeDispatchIntegrityError(str(exc)) from exc
    if (
        pointer["sha256"],
        pointer["size_bytes"],
    ) != (historical_pointer.sha256, historical_pointer.size_bytes):
        raise SourceDerivedOutcomeDispatchIntegrityError(
            "Historical pointer object CAS changed"
        )

    revision_heads = [
        head
        for record, head in zip(
            getattr(successor, "records"),
            getattr(successor, "revision_heads"),
            strict=True,
        )
        if record.day == target
    ]
    if len(revision_heads) != 1:
        raise SourceDerivedOutcomeDispatchIntegrityError(
            "Historical target revision head is missing or ambiguous"
        )
    expected = {
        "activation_source_manifest": getattr(successor, "activation_manifest"),
        "current_source_semantic_manifest": getattr(successor, "semantic_manifest"),
        "current_source_revision_head": revision_heads[0],
        "current_source_snapshot_receipt": getattr(successor, "snapshot_receipt"),
    }
    for role, artifact in expected.items():
        obligation = by_role[role][0]
        if artifact is None:
            raise SourceDerivedOutcomeDispatchIntegrityError(
                f"Historical {role} disappeared"
            )
        try:
            relative = (
                Path(artifact.path)
                .resolve()
                .relative_to(reservation.paths.active_root.resolve())
                .as_posix()
            )
        except ValueError as exc:
            raise SourceDerivedOutcomeDispatchIntegrityError(
                f"Historical {role} escaped active runtime"
            ) from exc
        if (
            obligation["root"],
            obligation["path"],
            obligation["sha256"],
            obligation["size_bytes"],
        ) != (
            "active",
            relative,
            artifact.sha256,
            artifact.size_bytes,
        ):
            raise SourceDerivedOutcomeDispatchIntegrityError(
                f"Historical {role} binding changed"
            )
        _read_exact_artifact(artifact, name=f"historical {role}")
    _read_exact_artifact(getattr(successor, "dataset"), name="historical dataset")
    manifest_source = input_payload.get("source")
    if not isinstance(manifest_source, Mapping):
        raise SourceDerivedOutcomeDispatchIntegrityError(
            "Historical input-manifest source changed type"
        )
    expected_manifest = {
        "canonical_dataset": getattr(successor, "dataset"),
        "semantic_manifest": getattr(successor, "semantic_manifest"),
        "activation_source_manifest": getattr(successor, "activation_manifest"),
        "snapshot_receipt": getattr(successor, "snapshot_receipt"),
        "target_revision_receipt": revision_heads[0],
    }
    if any(
        manifest_source.get(name) != _artifact_payload(artifact)
        for name, artifact in expected_manifest.items()
    ):
        raise SourceDerivedOutcomeDispatchIntegrityError(
            "Historical input-manifest artifact bindings changed"
        )


def _historical_materialization_plan(
    item: Mapping[str, Any],
    reservation: recovery.Reservation,
    derived_evidence: object,
    now: datetime,
) -> HistoricalMaterializationPlan:
    authority = _exact(
        item.get("authority"),
        recovery._OUTCOME_SELECTED_AUTHORITY_KEYS,  # noqa: SLF001
        name="historical machine-selected authority",
    )
    if (
        item.get("family") != "outcome_revision"
        or item.get("canonical_successor_state") != "outcome_materialized"
        or authority["record_type"] != "machine_selected_source_outcome"
        or authority["action"] != "outcome_materialized"
        or authority["selection_kind"] not in {"revision", "outstanding", "backfill"}
        or authority["terminal"] is not False
    ):
        raise SourceDerivedOutcomeDispatchIntegrityError(
            "Historical materialization item scope changed"
        )
    try:
        target = date.fromisoformat(authority["target_date"])
    except (TypeError, ValueError) as exc:
        raise SourceDerivedOutcomeDispatchIntegrityError(
            "Historical target date changed"
        ) from exc
    if target.isoformat() != authority["target_date"]:
        raise SourceDerivedOutcomeDispatchIntegrityError(
            "Historical target date is not canonical"
        )
    successor = getattr(getattr(derived_evidence, "authority"), "successor_source")
    successor_receipt_payload = getattr(
        getattr(derived_evidence, "authority"), "successor_receipt_payload"
    )
    snapshot_receipt = getattr(successor, "snapshot_receipt")
    if (
        snapshot_receipt is None
        or successor.outcome_source_id != authority["outcome_source_id"]
        or successor.snapshot_sequence_id != authority["source_snapshot_sequence_id"]
        or snapshot_receipt.sha256 != authority["source_snapshot_receipt_sha256"]
        or successor_receipt_payload.get("snapshot_sequence_id")
        != successor.snapshot_sequence_id
        or successor_receipt_payload.get("outcome_source_id")
        != successor.outcome_source_id
    ):
        raise SourceDerivedOutcomeDispatchIntegrityError(
            "Exact historical N+1 source identity changed"
        )
    records = [record for record in successor.records if record.day == target]
    if len(records) != 1 or records[0].revision_id != authority["source_revision_id"]:
        raise SourceDerivedOutcomeDispatchIntegrityError(
            "Historical target record changed"
        )
    previous_revision = authority["previous_revision_id"]
    previous_outcome = authority["previous_outcome_sha256"]
    if authority["selection_kind"] == "revision":
        _text(previous_revision, name="historical predecessor revision")
        _hash(previous_outcome, name="historical predecessor outcome")
    elif previous_revision is not None or previous_outcome is not None:
        raise SourceDerivedOutcomeDispatchIntegrityError(
            "Historical first selection gained a predecessor"
        )
    frozen = recovery._frozen_live_prefix(reservation)  # noqa: SLF001
    if authority["old_live_epoch_id"] != frozen.projection.epoch_id:
        raise SourceDerivedOutcomeDispatchIntegrityError(
            "Historical outcome old epoch changed"
        )
    seals = [
        event
        for event in frozen.current_events
        if event.event_type == "issue_batch_sealed"
        and event.target_date == target.isoformat()
    ]
    if len(seals) > 1:
        raise SourceDerivedOutcomeDispatchIntegrityError(
            "Historical live issue seal is ambiguous"
        )
    seal_hash = seals[0].entry_sha256 if seals else ZERO_HASH
    if seal_hash != authority["live_issue_seal_entry_sha256"]:
        raise SourceDerivedOutcomeDispatchIntegrityError(
            "Historical live issue seal binding changed"
        )
    selection = outcomes._Selection(  # noqa: SLF001
        kind=authority["selection_kind"],
        target_date=target,
        record=records[0],
        previous_revision_id=previous_revision,
        previous_outcome_sha256=previous_outcome,
    )
    materializer_profile = outcomes.load_config()
    try:
        input_payload = outcomes._build_input_manifest(  # noqa: SLF001
            materializer_profile, successor, selection
        )
        _validate_historical_artifacts(
            item,
            reservation,
            successor,
            successor_receipt_payload,
            input_payload,
            target=target,
        )
        input_manifest = recovery._expected_materializer_object(  # noqa: SLF001
            materializer_profile,
            reservation.paths.active_root,
            input_payload,
            suffix="outcome-input.json",
        )
        outcome_payload = outcomes._outcome_payload(  # noqa: SLF001
            materializer_profile,
            selection,
            input_manifest,
            outcome_source_id=successor.outcome_source_id,
        )
        exact_object = recovery._expected_materializer_object(  # noqa: SLF001
            materializer_profile,
            reservation.paths.active_root,
            outcome_payload,
            suffix="outcome.json",
        )
        chain = outcomes._scan_receipt_chain(  # noqa: SLF001
            target=target,
            profile=materializer_profile,
            root=reservation.paths.active_root,
            live_module=recovery.live,
            live_profile=frozen.profile,
            prerequisites=frozen.prerequisites,
        )
    except (outcomes.OutcomeMaterializerError, recovery.WorksetRecoveryError) as exc:
        raise SourceDerivedOutcomeDispatchIntegrityError(
            f"Historical materialization plan failed:{type(exc).__name__}:{exc}"
        ) from exc

    existing = None
    if chain is not None:
        same_revision = [
            value
            for value in chain.receipts
            if value.payload.get("source_revision_id")
            == authority["source_revision_id"]
        ]
        matches = [
            value for value in same_revision if value.exact_object == exact_object
        ]
        if same_revision and not matches:
            raise SourceDerivedOutcomeDispatchIntegrityError(
                "Historical revision already has a different immutable outcome"
            )
        if len(matches) > 1:
            raise SourceDerivedOutcomeDispatchIntegrityError(
                "Historical outcome receipt identity is ambiguous"
            )
        existing = matches[0] if matches else None
        if existing is not None and existing.receipt != chain.tip.receipt:
            active_path = outcomes._outcome_inbox_path(  # noqa: SLF001
                materializer_profile, reservation.paths.active_root, target
            )
            try:
                active_raw = outcomes._legal_active_bytes(  # noqa: SLF001
                    chain, active_path
                )
            except outcomes.OutcomeMaterializerError as exc:
                raise SourceDerivedOutcomeDispatchIntegrityError(
                    "Historical interior receipt has an illegal active outcome state"
                ) from exc
            if chain.pointed is None or active_raw is None:
                raise SourceDerivedOutcomeDispatchIntegrityError(
                    "Historical interior receipt lost the legal current pointer/inbox"
                )
    previous_registered = None
    if existing is not None:
        revision_sequence_id = existing.revision_sequence_id
        if existing.previous_receipt is not None:
            predecessors = [
                value
                for value in chain.receipts
                if value.receipt == existing.previous_receipt
            ]
            if len(predecessors) != 1:
                raise SourceDerivedOutcomeDispatchIntegrityError(
                    "Historical existing receipt predecessor changed"
                )
            previous_registered = predecessors[0]
            if (
                previous_registered.payload.get("source_revision_id")
                != previous_revision
                or previous_registered.exact_object.sha256 != previous_outcome
            ):
                raise SourceDerivedOutcomeDispatchIntegrityError(
                    "Historical existing receipt predecessor authority changed"
                )
        elif previous_revision is not None or previous_outcome is not None:
            raise SourceDerivedOutcomeDispatchIntegrityError(
                "Historical existing genesis gained a predecessor authority"
            )
    elif previous_revision is None:
        if chain is not None:
            raise SourceDerivedOutcomeDispatchIntegrityError(
                "Historical first outcome conflicts with a receipt chain"
            )
        revision_sequence_id = 1
    else:
        if (
            chain is None
            or chain.tip.payload.get("source_revision_id") != previous_revision
            or chain.tip.exact_object.sha256 != previous_outcome
        ):
            raise SourceDerivedOutcomeDispatchIntegrityError(
                "Historical outcome lost its exact registered predecessor"
            )
        previous_registered = chain.tip
        revision_sequence_id = chain.tip.revision_sequence_id + 1
    expected_previous = (
        recovery._materializer_artifact_payload(previous_registered.receipt)  # noqa: SLF001
        if previous_registered is not None
        else None
    )
    if existing is not None and (
        existing.revision_sequence_id != revision_sequence_id
        or (
            recovery._materializer_artifact_payload(existing.previous_receipt)  # noqa: SLF001
            if existing.previous_receipt is not None
            else None
        )
        != expected_previous
    ):
        raise SourceDerivedOutcomeDispatchIntegrityError(
            "Historical existing receipt lineage changed"
        )
    receipt_payload, receipt = recovery._outcome_materialization_receipt_payload(  # noqa: SLF001
        materializer_profile,
        reservation.paths.active_root,
        target=target,
        revision=authority["source_revision_id"],
        exact_object=exact_object,
        input_manifest=input_manifest,
        input_manifest_payload=input_payload,
        outcome_payload=outcome_payload,
        previous_receipt=(
            previous_registered.receipt if previous_registered is not None else None
        ),
        revision_sequence_id=revision_sequence_id,
    )
    if existing is not None:
        try:
            stored_receipt_payload, stored_receipt_raw = outcomes._read_json(  # noqa: SLF001
                existing.receipt.path, name="historical existing outcome receipt"
            )
        except outcomes.OutcomeMaterializerError as exc:
            raise SourceDerivedOutcomeDispatchIntegrityError(str(exc)) from exc
        if (
            existing.receipt != receipt
            or stored_receipt_payload != receipt_payload
            or stored_receipt_raw != outcomes._canonical_bytes(receipt_payload)  # noqa: SLF001
        ):
            raise SourceDerivedOutcomeDispatchIntegrityError(
                "Historical existing receipt bytes differ from the exact contract"
            )
    contract: dict[str, object] = {
        "schema_version": recovery.OUTCOME_MATERIALIZATION_CONTRACT_SCHEMA,
        "writer_branch": "machine_selected_source",
        "authority_record_type": "machine_selected_source_outcome",
        "selection_kind": selection.kind,
        "old_live_epoch_id": frozen.projection.epoch_id,
        "target_date": target.isoformat(),
        "outcome_source_id": successor.outcome_source_id,
        "source_revision_id": authority["source_revision_id"],
        "source_snapshot_sequence_id": successor.snapshot_sequence_id,
        "source_snapshot_receipt_sha256": snapshot_receipt.sha256,
        "live_issue_seal_entry_sha256": seal_hash,
        "source_exported_at_utc": successor.exported_at_utc,
        "previous_revision_id": previous_revision,
        "previous_outcome_sha256": previous_outcome,
        "revision_sequence_id": revision_sequence_id,
        "previous_receipt": expected_previous,
        "input_manifest_payload": input_payload,
        "input_manifest": recovery._materializer_artifact_payload(input_manifest),  # noqa: SLF001
        "outcome_payload": outcome_payload,
        "exact_outcome_object": recovery._materializer_artifact_payload(exact_object),  # noqa: SLF001
        "receipt_payload": receipt_payload,
        "receipt": recovery._materializer_artifact_payload(receipt),  # noqa: SLF001
        "item_artifacts_sha256": recovery._outcome_materialization_item_artifacts_sha256(  # noqa: SLF001
            item
        ),
    }
    try:
        recovery._outcome_materialization_contract_record(  # noqa: SLF001
            item, reservation, contract
        )
    except recovery.WorksetRecoveryError as exc:
        raise SourceDerivedOutcomeDispatchIntegrityError(str(exc)) from exc
    _parse_utc(successor.exported_at_utc, name="historical source export")
    _utc_text(now)
    return HistoricalMaterializationPlan(item=item, contract=contract)


def _default_materialize_action(
    item: Mapping[str, Any],
    reservation: recovery.Reservation,
    contract: Mapping[str, Any],
    now: datetime,
) -> recovery.ActionOutput:
    try:
        return recovery._outcome_materialization_action(  # noqa: SLF001
            item, reservation, contract, now=now
        )
    except recovery.WorksetRecoveryError as exc:
        raise SourceDerivedOutcomeDispatchIntegrityError(str(exc)) from exc


def _candidate_set(
    computation: overlay.SourceDerivedWorksetOverlayComputation,
) -> tuple[_Candidate, ...]:
    payload = computation.derived_evidence.reservation_payload
    additions = payload.get("derived_outcome_items")
    rebounds = payload.get("rebound_existing_items")
    invalidations = payload.get("invalidated_existing_items")
    if not all(
        isinstance(value, list) for value in (additions, rebounds, invalidations)
    ):
        raise SourceDerivedOutcomeDispatchIntegrityError(
            "Published overlay D/R/I collections changed type"
        )
    effective_by_natural_key = {
        str(item["natural_key"]): item for item in computation.effective_items
    }
    if len(effective_by_natural_key) != len(computation.effective_items):
        raise SourceDerivedOutcomeDispatchIntegrityError(
            "Effective workset contains duplicate natural keys"
        )
    kind_by_id: dict[str, str] = {}
    for kind, values in (("D", additions), ("R", rebounds)):
        for value in values:
            if not isinstance(value, Mapping):
                raise SourceDerivedOutcomeDispatchIntegrityError(
                    "Published overlay D/R row changed type"
                )
            try:
                normalized = overlay._normalize_item(  # noqa: SLF001
                    value,
                    computation.reservation.manifest_snapshot.sha256,
                    name=f"published {kind} row",
                )
            except overlay.SourceDerivedWorksetOverlayError as exc:
                raise SourceDerivedOutcomeDispatchIntegrityError(str(exc)) from exc
            effective = effective_by_natural_key.get(str(normalized["natural_key"]))
            if effective is None or dict(effective) != normalized:
                raise SourceDerivedOutcomeDispatchIntegrityError(
                    "Published D/R row does not exactly match its effective row"
                )
            key_id = str(normalized["key_id"])
            if key_id in kind_by_id:
                raise SourceDerivedOutcomeDispatchIntegrityError(
                    "Published overlay D/R key duplicated"
                )
            kind_by_id[key_id] = kind
    invalid_ids: set[str] = set()
    for value in invalidations:
        if not isinstance(value, Mapping):
            raise SourceDerivedOutcomeDispatchIntegrityError(
                "Published I row changed type"
            )
        try:
            normalized = overlay._normalize_item(  # noqa: SLF001
                value,
                computation.reservation.manifest_snapshot.sha256,
                name="published I row",
            )
        except overlay.SourceDerivedWorksetOverlayError as exc:
            raise SourceDerivedOutcomeDispatchIntegrityError(str(exc)) from exc
        invalid_ids.add(str(normalized["key_id"]))
    source_key = computation.cross_authority.source_item["natural_key"]
    result: list[_Candidate] = []
    seen: set[str] = set()
    for index, item in enumerate(computation.effective_items):
        key_id = _text(item.get("key_id"), name="effective key id")
        if key_id in invalid_ids:
            raise SourceDerivedOutcomeDispatchIntegrityError(
                "Invalidated I row survived the effective overlay"
            )
        kind = kind_by_id.get(key_id)
        if kind is None:
            continue
        if key_id in seen:
            raise SourceDerivedOutcomeDispatchIntegrityError(
                "Effective D/R row duplicated"
            )
        seen.add(key_id)
        dependencies = item.get("dependency_keys")
        if dependencies == [source_key]:
            result.append(_Candidate(item=item, kind=kind, topological_index=index))
    if seen != set(kind_by_id):
        raise SourceDerivedOutcomeDispatchIntegrityError(
            "Published D/R row did not appear exactly once in the effective overlay"
        )
    return tuple(result)


def _step_id(overlay_event_sha256: str, key_id: str) -> str:
    return _sha256(
        _canonical_bytes(
            {
                "overlay_event_sha256": overlay_event_sha256,
                "key_id": key_id,
                "step_index": 0,
                "action": "outcome_materialized",
            }
        )
    )


def _intent_payload(
    profile: Mapping[str, Any],
    paths: SourceDerivedOutcomeDispatchPaths,
    computation: overlay.SourceDerivedWorksetOverlayComputation,
    overlay_event: registry.ArtifactSnapshot,
    candidate: _Candidate,
    contract: Mapping[str, Any],
    *,
    recorded_at: str,
) -> dict[str, object]:
    item = candidate.item
    step_id = _step_id(overlay_event.sha256, str(item["key_id"]))
    return {
        "schema_version": profile["protocol"]["intent_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "recorded_at_utc": recorded_at,
        "step_id": step_id,
        "step_index": 0,
        "action": "outcome_materialized",
        "next_action": "outcome_or_revision_consumed",
        "dri_kind": candidate.kind,
        "topological_index": candidate.topological_index,
        "key_id": item["key_id"],
        "natural_key": item["natural_key"],
        "namespace_digest": item["namespace_digest"],
        "transition_plan_sha256": recovery._transition_plan(item)[  # noqa: SLF001
            "plan_sha256"
        ],
        "overlay_slot_id": computation.slot_id,
        "overlay_event": _reference(overlay_event, paths.overlay.root),
        "source_expansion_gate": {
            "source_key_id": computation.cross_authority.source_item["key_id"],
            "source_natural_key": computation.cross_authority.source_item[
                "natural_key"
            ],
            "cross_freeze_receipt": _reference(
                computation.cross_receipt_snapshot, paths.overlay.cross.root
            ),
            "cross_freeze_event": _reference(
                computation.cross_event_snapshot, paths.overlay.cross.root
            ),
            "terminal_for_source_parent": False,
        },
        "materialization_contract_sha256": _sha256(_canonical_bytes(contract)),
        "materialization_contract": dict(contract),
        "implementation": _implementation_reference(),
        **_claims(materialized=False),
    }


def _receipt_payload(
    profile: Mapping[str, Any],
    paths: SourceDerivedOutcomeDispatchPaths,
    intent: Mapping[str, Any],
    intent_snapshot: registry.ArtifactSnapshot,
    output: recovery.ActionOutput,
    *,
    recorded_at: str,
) -> dict[str, object]:
    if output.kind != "outcome_materializer_publication":
        raise SourceDerivedOutcomeDispatchIntegrityError(
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
        raise SourceDerivedOutcomeDispatchIntegrityError(
            "Canonical materializer output semantics changed"
        )
    return {
        "schema_version": profile["protocol"]["receipt_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "recorded_at_utc": recorded_at,
        "step_id": intent["step_id"],
        "step_index": 0,
        "action": "outcome_materialized",
        "next_action": "outcome_or_revision_consumed",
        "key_id": intent["key_id"],
        "natural_key": intent["natural_key"],
        "namespace_digest": intent["namespace_digest"],
        "intent": _reference(intent_snapshot, paths.root),
        "output": {
            "kind": output.kind,
            "reference": dict(output.reference)
            if output.reference is not None
            else None,
            "semantics": semantics,
        },
        "terminal_for_effective_key": False,
        "terminal_for_recovery_v6_key": False,
        **_claims(materialized=True),
    }


def _event_payload(
    profile: Mapping[str, Any],
    paths: SourceDerivedOutcomeDispatchPaths,
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
        "step_index": 0,
        "action": "outcome_materialized",
        "next_action": "outcome_or_revision_consumed",
        "key_id": receipt["key_id"],
        "natural_key": receipt["natural_key"],
        "namespace_digest": receipt["namespace_digest"],
        "receipt": _reference(receipt_snapshot, paths.root),
        "terminal_for_effective_key": False,
        "terminal_for_recovery_v6_key": False,
        **_claims(materialized=True),
    }
    return {**body, "entry_sha256": _sha256(_canonical_bytes(body))}


def _same_except_time(
    actual: Mapping[str, Any], expected: Mapping[str, Any], *, name: str
) -> None:
    if set(actual) != set(expected):
        raise SourceDerivedOutcomeDispatchIntegrityError(f"{name} keys changed")
    _parse_utc(actual.get("recorded_at_utc"), name=f"{name} time")
    comparable = {
        key: value for key, value in actual.items() if key != "recorded_at_utc"
    }
    expected_comparable = {
        key: value for key, value in expected.items() if key != "recorded_at_utc"
    }
    if comparable != expected_comparable:
        raise SourceDerivedOutcomeDispatchIntegrityError(f"{name} semantics changed")


def _named_records(
    directory: Path, *, name: str
) -> dict[str, tuple[dict[str, Any], registry.ArtifactSnapshot]]:
    result: dict[str, tuple[dict[str, Any], registry.ArtifactSnapshot]] = {}
    for path in _strict_entries(directory, name=name):
        matched = RECORD_NAME.fullmatch(path.name)
        if matched is None:
            raise SourceDerivedOutcomeDispatchIntegrityError(
                f"{name} contains an unexpected entry"
            )
        step = matched.group("step")
        if step in result:
            raise SourceDerivedOutcomeDispatchIntegrityError(
                f"{name} contains a duplicate step"
            )
        result[step] = _strict_json(path, name=f"{name} record")
    return result


def _event_records(
    profile: Mapping[str, Any],
    paths: SourceDerivedOutcomeDispatchPaths,
    receipts: Mapping[str, tuple[dict[str, Any], registry.ArtifactSnapshot]],
) -> tuple[tuple[dict[str, Any], registry.ArtifactSnapshot], ...]:
    result: list[tuple[dict[str, Any], registry.ArtifactSnapshot]] = []
    previous = ZERO_HASH
    seen: set[str] = set()
    for index, path in enumerate(
        _strict_entries(paths.events, name="source-derived dispatch events"), start=1
    ):
        matched = EVENT_NAME.fullmatch(path.name)
        if matched is None or int(matched.group("sequence")) != index:
            raise SourceDerivedOutcomeDispatchIntegrityError(
                "Source-derived dispatch event sequence branched"
            )
        payload, snapshot = _strict_json(path, name="source-derived dispatch event")
        step = _text(payload.get("step_id"), name="dispatch event step")
        if step in seen or step not in receipts:
            raise SourceDerivedOutcomeDispatchIntegrityError(
                "Source-derived dispatch event receipt binding branched"
            )
        receipt, receipt_snapshot = receipts[step]
        recorded_at = payload.get("recorded_at_utc")
        _parse_utc(recorded_at, name="source-derived dispatch event time")
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
            raise SourceDerivedOutcomeDispatchIntegrityError(
                "Source-derived dispatch event semantics changed"
            )
        entry = _hash(payload.get("entry_sha256"), name="dispatch event entry")
        if matched.group("entry") != entry:
            raise SourceDerivedOutcomeDispatchIntegrityError(
                "Source-derived dispatch event filename changed"
            )
        previous = entry
        seen.add(step)
        result.append((payload, snapshot))
    return tuple(result)


def _load_durable_state(
    profile: Mapping[str, Any],
    paths: SourceDerivedOutcomeDispatchPaths,
    computation: overlay.SourceDerivedWorksetOverlayComputation,
    overlay_event: registry.ArtifactSnapshot,
    candidates: Sequence[_Candidate],
    *,
    now: datetime,
    build_plan: BuildPlan,
    verify_action: MaterializeAction,
) -> tuple[
    dict[str, tuple[dict[str, Any], registry.ArtifactSnapshot]],
    dict[str, tuple[dict[str, Any], registry.ArtifactSnapshot]],
    tuple[tuple[dict[str, Any], registry.ArtifactSnapshot], ...],
    dict[str, HistoricalMaterializationPlan],
]:
    intents = _named_records(paths.intents, name="source-derived dispatch intents")
    receipts = _named_records(paths.receipts, name="source-derived dispatch receipts")
    if not set(receipts) <= set(intents):
        raise SourceDerivedOutcomeDispatchIntegrityError(
            "Source-derived dispatch receipt is orphaned"
        )
    by_step = {
        _step_id(overlay_event.sha256, str(candidate.item["key_id"])): candidate
        for candidate in candidates
    }
    if not set(intents) <= set(by_step):
        raise SourceDerivedOutcomeDispatchIntegrityError(
            "Dispatcher state is outside the published source-only D/R candidate set"
        )
    plans: dict[str, HistoricalMaterializationPlan] = {}
    for step, (intent, intent_snapshot) in intents.items():
        candidate = by_step[step]
        plan = build_plan(
            candidate.item, computation.reservation, computation.derived_evidence, now
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
        _same_except_time(
            intent, expected_intent, name="source-derived dispatch intent"
        )
        if intent_snapshot.path.name != f"{step}.json":
            raise SourceDerivedOutcomeDispatchIntegrityError(
                "Source-derived dispatch intent filename changed"
            )
        if step in receipts:
            receipt, receipt_snapshot = receipts[step]
            try:
                output = verify_action(
                    candidate.item,
                    computation.reservation,
                    plan.contract,
                    now,
                )
            except SourceDerivedOutcomeDispatchError:
                raise
            except Exception as exc:
                raise SourceDerivedOutcomeDispatchIntegrityError(
                    f"Recorded materialization verification failed:{type(exc).__name__}:{exc}"
                ) from exc
            expected_receipt = _receipt_payload(
                profile,
                paths,
                intent,
                intent_snapshot,
                output,
                recorded_at="",
            )
            _same_except_time(
                receipt, expected_receipt, name="source-derived dispatch receipt"
            )
            if receipt_snapshot.path.name != f"{step}.json":
                raise SourceDerivedOutcomeDispatchIntegrityError(
                    "Source-derived dispatch receipt filename changed"
                )
    incomplete_intents = set(intents) - set(receipts)
    if len(incomplete_intents) > 1:
        raise SourceDerivedOutcomeDispatchIntegrityError(
            "Dispatcher contains branched incomplete intents"
        )
    events = _event_records(profile, paths, receipts)
    event_steps = {str(payload["step_id"]) for payload, _ in events}
    pending_receipts = set(receipts) - event_steps
    if len(pending_receipts) > 1:
        raise SourceDerivedOutcomeDispatchIntegrityError(
            "Dispatcher contains branched receipt-only commits"
        )
    if incomplete_intents and pending_receipts:
        raise SourceDerivedOutcomeDispatchIntegrityError(
            "Dispatcher contains competing crash frontiers"
        )
    return intents, receipts, events, plans


def _recorded_materialization_output(
    item: Mapping[str, Any],
    reservation: recovery.Reservation,
    contract: Mapping[str, Any],
    now: datetime,
) -> recovery.ActionOutput:
    del now
    try:
        return recovery._recorded_outcome_materialization_output(  # noqa: SLF001
            item, reservation, contract
        )
    except recovery.WorksetRecoveryError as exc:
        raise SourceDerivedOutcomeDispatchIntegrityError(str(exc)) from exc


def _publish_intent(
    profile: Mapping[str, Any],
    paths: SourceDerivedOutcomeDispatchPaths,
    payload: Mapping[str, Any],
) -> registry.ArtifactSnapshot:
    step = _hash(payload.get("step_id"), name="dispatch intent step")
    return _publish(
        paths.intents / f"{step}.json",
        _canonical_bytes(payload),
        root=paths.root,
        name="source-derived dispatch intent",
    )


def _publish_receipt(
    paths: SourceDerivedOutcomeDispatchPaths,
    payload: Mapping[str, Any],
) -> registry.ArtifactSnapshot:
    step = _hash(payload.get("step_id"), name="dispatch receipt step")
    return _publish(
        paths.receipts / f"{step}.json",
        _canonical_bytes(payload),
        root=paths.root,
        name="source-derived dispatch receipt",
    )


def _append_event(
    profile: Mapping[str, Any],
    paths: SourceDerivedOutcomeDispatchPaths,
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
        name="source-derived dispatch event",
    )


def _write_status(
    profile: Mapping[str, Any],
    paths: SourceDerivedOutcomeDispatchPaths,
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
            name="source-derived dispatch status",
        )
    except registry.EpochRegistryError as exc:
        raise SourceDerivedOutcomeDispatchIntegrityError(str(exc)) from exc


def _result(
    paths: SourceDerivedOutcomeDispatchPaths,
    status: str,
    reason: str,
    *,
    step_id: str | None = None,
    item: Mapping[str, Any] | None = None,
    intent_path: Path | None = None,
    receipt_path: Path | None = None,
    event_path: Path | None = None,
) -> SourceDerivedOutcomeDispatchResult:
    return SourceDerivedOutcomeDispatchResult(
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


def _coordinate_source_derived_outcome_dispatch(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    registry_root: Path | None = None,
    active_root: Path | None = None,
    shadow_root: Path | None = None,
    clock: Callable[[], datetime] | None = None,
    fault_hook: FaultHook | None = None,
    load_derived_authority: overlay.LoadDerivedAuthority | None = None,
    build_plan: BuildPlan = _historical_materialization_plan,
    materialize_action: MaterializeAction = _default_materialize_action,
    verify_action: MaterializeAction = _recorded_materialization_output,
) -> SourceDerivedOutcomeDispatchResult:
    profile = load_source_derived_outcome_dispatch_profile(config_path)
    paths = source_derived_outcome_dispatch_paths(
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
                paths.overlay,
                now,
                load_derived_authority=load_derived_authority,
            )
        except overlay.SourceDerivedWorksetOverlayError as exc:
            raise SourceDerivedOutcomeDispatchIntegrityError(str(exc)) from exc
        if computation is None:
            if any(
                _strict_entries(directory, name="dispatcher durable namespace")
                for directory in (paths.intents, paths.receipts, paths.events)
            ):
                raise SourceDerivedOutcomeDispatchIntegrityError(
                    "Dispatcher bytes outlived their exact overlay authority"
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
                overlay_profile, paths.overlay, computation
            )
        except overlay.SourceDerivedWorksetOverlayError as exc:
            raise SourceDerivedOutcomeDispatchIntegrityError(str(exc)) from exc
        if stored_overlay is None or published_overlay is None:
            if any(
                _strict_entries(directory, name="dispatcher durable namespace")
                for directory in (paths.intents, paths.receipts, paths.events)
            ):
                raise SourceDerivedOutcomeDispatchIntegrityError(
                    "Dispatcher bytes exist before overlay publication"
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
        candidates = _candidate_set(computation)
        intents, receipts, events, plans = _load_durable_state(
            profile,
            paths,
            computation,
            overlay_event_snapshot,
            candidates,
            now=now,
            build_plan=build_plan,
            verify_action=verify_action,
        )
        event_steps = {str(payload["step_id"]) for payload, _ in events}

        pending_receipts = [step for step in receipts if step not in event_steps]
        if pending_receipts:
            current_step = pending_receipts[0]
            receipt, receipt_snapshot = receipts[current_step]
            intent, intent_snapshot = intents[current_step]
            current_item = next(
                candidate.item
                for candidate in candidates
                if str(candidate.item["key_id"]) == str(intent["key_id"])
            )
            event_snapshot = _append_event(
                profile, paths, receipt, receipt_snapshot, events, now=now
            )
            status = "source_derived_dispatch_event_forward_adopted"
            reason = "the durable dispatcher receipt was adopted into its event"
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
            candidate = next(
                value
                for value in candidates
                if str(value.item["key_id"]) == str(intent["key_id"])
            )
            current_item = candidate.item
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
                profile,
                paths,
                receipt_payload,
                receipt_snapshot,
                events,
                now=now,
            )
            status = "source_derived_materialization_forward_adopted"
            reason = (
                "the durable dispatcher intent adopted the exact materializer commit"
            )
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
                    overlay_event_snapshot.sha256, str(candidate.item["key_id"])
                )
                not in event_steps
            ),
            None,
        )
        if selected is None:
            if candidates:
                status = "source_derived_materialization_dispatch_current"
                reason = "all source-only effective D/R candidates are materialized"
            else:
                status = "waiting_for_source_only_effective_candidate"
                reason = (
                    "published D/R rows require non-source terminal dependencies; "
                    "the consumption bridge is not implemented in this dispatcher"
                )
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
            overlay_event_snapshot.sha256, str(current_item["key_id"])
        )
        plan = build_plan(
            current_item, computation.reservation, computation.derived_evidence, now
        )
        intent_payload = _intent_payload(
            profile,
            paths,
            computation,
            overlay_event_snapshot,
            selected,
            plan.contract,
            recorded_at=_utc_text(now),
        )
        intent_snapshot = _publish_intent(profile, paths, intent_payload)
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
        status = "source_derived_outcome_materialized"
        reason = "one exact historical N+1 effective D/R outcome was materialized"
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
    except SourceDerivedOutcomeDispatchBusyError:
        raise
    except SourceDerivedOutcomeDispatchError as exc:
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
            except SourceDerivedOutcomeDispatchError:
                pass
        raise
    except Exception as exc:
        raise SourceDerivedOutcomeDispatchIntegrityError(
            f"Source-derived dispatch failed:{type(exc).__name__}:{exc}"
        ) from exc
    finally:
        if handles:
            _release_locks(handles)


def coordinate_source_derived_outcome_dispatch(
    *, config_path: Path = DEFAULT_CONFIG_PATH
) -> SourceDerivedOutcomeDispatchResult:
    """Run one machine-only source-derived outcome materialization poll."""

    return _coordinate_source_derived_outcome_dispatch(config_path=config_path)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result = coordinate_source_derived_outcome_dispatch(config_path=args.config)
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
                "terminal_for_recovery_v6_key": result.terminal_for_recovery_v6_key,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
