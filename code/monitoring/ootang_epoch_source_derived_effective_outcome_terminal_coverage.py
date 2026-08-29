"""Prove terminal coverage of every current source-derived effective D/R row."""

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
from monitoring import (  # noqa: E402
    ootang_epoch_source_derived_dependent_outcome_consumption as dependent_consumption,
)
from monitoring import (  # noqa: E402
    ootang_epoch_source_derived_dependent_outcome_dispatch as dependent_dispatch,
)
from monitoring import (  # noqa: E402
    ootang_epoch_source_derived_outcome_consumption as source_consumption,
)
from monitoring import (  # noqa: E402
    ootang_epoch_source_derived_workset_overlay as overlay,
)
from monitoring import ootang_epoch_workset_manifest as manifest  # noqa: E402
from monitoring import ootang_epoch_workset_recovery as recovery  # noqa: E402


DEFAULT_CONFIG_PATH = (
    ROOT
    / "config"
    / "ootang_epoch_source_derived_effective_outcome_terminal_coverage.v1.json"
)
DEFAULT_CONFIG_SHA256 = (
    "32eca2b5de227f77d42de79fb0be62b7a0a824712ee9bf19489675b8716ede5c"
)
IMPLEMENTATION_LOGICAL_PATH = (
    "code/monitoring/ootang_epoch_source_derived_effective_outcome_terminal_coverage.py"
)
MAX_CONTROL_BYTES = 4 * 1024 * 1024
ZERO_HASH = "0" * 64
PROOF_NAME = re.compile(r"^(?P<digest>[0-9a-f]{64})\.json$")
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
    "dependent_dispatch_profile": {
        "path": "config/ootang_epoch_source_derived_dependent_outcome_dispatch.v1.json",
        "expected_sha256": dependent_dispatch.DEFAULT_CONFIG_SHA256,
    },
    "dependent_dispatch_implementation": {
        "path": "code/monitoring/ootang_epoch_source_derived_dependent_outcome_dispatch.py",
        "expected_sha256": "c172c08ef48e0153b139542998bb6cf0cd382f5e1940593107ff7424713a2c59",
    },
    "source_consumption_profile": {
        "path": "config/ootang_epoch_source_derived_outcome_consumption.v1.json",
        "expected_sha256": source_consumption.DEFAULT_CONFIG_SHA256,
    },
    "source_consumption_implementation": {
        "path": "code/monitoring/ootang_epoch_source_derived_outcome_consumption.py",
        "expected_sha256": "fb03dfa502d7402b824cec16b36698d0de6349e419feac8a34981ff9e05b0789",
    },
    "dependent_consumption_profile": {
        "path": "config/ootang_epoch_source_derived_dependent_outcome_consumption.v1.json",
        "expected_sha256": dependent_consumption.DEFAULT_CONFIG_SHA256,
    },
    "dependent_consumption_implementation": {
        "path": "code/monitoring/ootang_epoch_source_derived_dependent_outcome_consumption.py",
        "expected_sha256": "83c32fd80e07bed0cf2ec95d173f3152dd8d0c8d1e0aa3283803481495af860a",
    },
    "drain_profile": {
        "path": "config/ootang_epoch_drain.v1.json",
        "expected_sha256": drain.DEFAULT_CONFIG_SHA256,
    },
    "drain_implementation": {
        "path": "code/monitoring/ootang_epoch_drain.py",
        "expected_sha256": "c4c226da087d354195fc3955ff60ff3b12af13f111d03cb59c5d64d0195a1602",
    },
    "recovery_profile": {
        "path": "config/ootang_epoch_workset_recovery.v1.json",
        "expected_sha256": recovery.DEFAULT_CONFIG_SHA256,
    },
    "recovery_implementation": {
        "path": "code/monitoring/ootang_epoch_workset_recovery.py",
        "expected_sha256": "b9f25133eef9cb94fb255bab588edcd573f0fa792ef82c4ddb9068d0a44a6c51",
    },
}
EXPECTED_RUNTIME = {
    "registry_root": "runtime/ootang_epoch_registry_v1",
    "recovery_namespace": "workset_recovery_v1",
    "namespace": "source_derived_effective_outcome_terminal_coverage_v1",
    "proofs": "proofs",
    "events": "events",
    "status": "status.json",
    "manager_lock": "manager.lock",
    "active_root": "runtime/ootang_prequential_live_v1",
    "shadow_root": "runtime/ootang_prequential_calibration_shadow_v1",
}
EXPECTED_PROTOCOL = {
    "proof_schema_version": "ootang_epoch_source_derived_effective_outcome_terminal_coverage_proof_v1",
    "event_schema_version": "ootang_epoch_source_derived_effective_outcome_terminal_coverage_event_v1",
    "status_schema_version": "ootang_epoch_source_derived_effective_outcome_terminal_coverage_status_v1",
    "event_type": "epoch_source_derived_effective_outcome_terminal_coverage_proved",
    "canonical_json": "utf8_sort_keys_compact_no_nan_trailing_lf",
    "initial_previous_entry_sha256": ZERO_HASH,
    "maximum_items": 4096,
    "maximum_control_bytes": MAX_CONTROL_BYTES,
    "publication_policy": "deterministic_content_addressed_proof_then_singleton_event",
    "coverage_policy": "current_overlay_all_normalized_d_or_r_equal_disjoint_source_and_dependent_terminal_events",
}
TRUE_CAPABILITIES = (
    "machine_only",
    "current_effective_dri_denominator_deep_verified",
    "source_terminal_consumption_events_deep_verified",
    "dependent_terminal_consumption_events_deep_verified",
    "exact_current_effective_identity_bijection_implemented",
    "dependency_class_provenance_verified",
    "content_addressed_effective_terminal_proof_implemented",
    "singleton_effective_terminal_event_implemented",
    "proof_event_forward_adoption_implemented",
    "current_effective_dri_terminal_coverage_implemented",
)
FALSE_CLAIMS = (
    "all_effective_items_terminal",
    "terminal_for_recovery_v6_key",
    "source_parent_terminal",
    "recovery_v6_mutated",
    "effective_workset_overlay_mutated",
    "source_consumption_mutated",
    "dependent_dispatch_mutated",
    "dependent_consumption_mutated",
    "terminal_transition_closure_implemented",
    "bounded_workset_recovery_implemented",
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


class SourceDerivedEffectiveOutcomeTerminalCoverageError(RuntimeError):
    """Base effective D/R terminal-coverage error."""


class SourceDerivedEffectiveOutcomeTerminalCoverageConfigError(
    SourceDerivedEffectiveOutcomeTerminalCoverageError
):
    """The reviewed profile or a direct upstream changed."""


class SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError(
    SourceDerivedEffectiveOutcomeTerminalCoverageError
):
    """An upstream or persisted coverage authority failed closed."""


class SourceDerivedEffectiveOutcomeTerminalCoverageBusyError(
    SourceDerivedEffectiveOutcomeTerminalCoverageError
):
    """A surviving coordinator lock is busy."""


@dataclass(frozen=True)
class SourceDerivedEffectiveOutcomeTerminalCoveragePaths:
    registry_root: Path
    root: Path
    proofs: Path
    events: Path
    status: Path
    manager_lock: Path
    active_root: Path
    shadow_root: Path
    consumption: dependent_consumption.SourceDerivedDependentOutcomeConsumptionPaths


@dataclass(frozen=True)
class SourceDerivedEffectiveOutcomeTerminalCoverageResult:
    status: str
    reason: str
    status_path: Path
    proof_path: Path | None = None
    event_path: Path | None = None
    required_key_count: int = 0
    covered_key_count: int = 0
    missing_key_count: int = 0
    missing_key_ids: tuple[str, ...] = ()
    all_current_effective_d_or_r_terminal: bool = False
    all_effective_items_terminal: bool = False
    terminal_transition_closure_implemented: bool = False
    lifecycle_authority: bool = False


@dataclass(frozen=True)
class _Assessment:
    rows: tuple[dict[str, Any], ...]
    missing_key_ids: tuple[str, ...]
    source_only_count: int
    dependent_count: int
    dependent_supported_count: int
    unsupported_count: int
    upstream_frontiers_complete: bool


FaultHook = Callable[[str], None]


def _canonical_bytes(value: object) -> bytes:
    try:
        return registry._canonical_bytes(value)  # type: ignore[arg-type]  # noqa: SLF001
    except (TypeError, ValueError) as exc:
        raise SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError(
            "Value is not canonical JSON"
        ) from exc


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _claims(*, complete: bool = False) -> dict[str, bool]:
    return {
        **{name: True for name in TRUE_CAPABILITIES},
        "all_current_effective_d_or_r_terminal": complete,
        **{name: False for name in FALSE_CLAIMS},
    }


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError(
            "Coverage clock must be timezone-aware"
        )
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: object, *, name: str) -> None:
    try:
        registry._utc_timestamp(value, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError(
            str(exc)
        ) from exc


def _child(root: Path, relative: str, *, name: str) -> Path:
    try:
        return registry._contained(root, relative, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise SourceDerivedEffectiveOutcomeTerminalCoverageConfigError(
            str(exc)
        ) from exc


def _strict_json(
    path: Path, *, name: str
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    try:
        return manifest._read_json(  # noqa: SLF001
            path, name=name, maximum_bytes=MAX_CONTROL_BYTES
        )
    except manifest.WorksetManifestError as exc:
        raise SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError(
            str(exc)
        ) from exc


def _strict_entries(directory: Path, *, name: str) -> tuple[Path, ...]:
    try:
        return manifest._strict_entries(directory, name=name)  # noqa: SLF001
    except manifest.WorksetManifestError as exc:
        raise SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError(
            str(exc)
        ) from exc


def _reference(snapshot: registry.ArtifactSnapshot, root: Path) -> dict[str, object]:
    try:
        relative = snapshot.path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError(
            "Referenced authority escaped its root"
        ) from exc
    return {
        "path": relative,
        "sha256": snapshot.sha256,
        "size_bytes": snapshot.size_bytes,
    }


def _publish(path: Path, payload: Mapping[str, Any], *, root: Path, name: str):
    raw = _canonical_bytes(payload)
    if len(raw) > MAX_CONTROL_BYTES:
        raise SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError(
            f"{name} capacity exceeded"
        )
    try:
        return drain._publish_once_durable(path, raw, root=root, name=name)  # noqa: SLF001
    except drain.EpochDrainError as exc:
        raise SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError(
            str(exc)
        ) from exc


def _implementation_reference() -> dict[str, object]:
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            Path(__file__),
            name="coverage implementation",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
    except registry.EpochRegistryError as exc:
        raise SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError(
            str(exc)
        ) from exc
    return {
        "path": IMPLEMENTATION_LOGICAL_PATH,
        "sha256": snapshot.sha256,
        "size_bytes": snapshot.size_bytes,
    }


def load_source_derived_effective_outcome_terminal_coverage_profile(
    path: Path = DEFAULT_CONFIG_PATH, *, project_root: Path = ROOT
) -> dict[str, Any]:
    root = project_root.resolve()
    resolved = path if path.is_absolute() else root / path
    if root != ROOT.resolve() or resolved.resolve() != DEFAULT_CONFIG_PATH.resolve():
        raise SourceDerivedEffectiveOutcomeTerminalCoverageConfigError(
            "Only the reviewed default coverage profile is accepted"
        )
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            resolved, name="coverage profile", maximum_bytes=MAX_CONTROL_BYTES
        )
        profile = registry._decode_json(snapshot.raw, name="coverage profile")  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise SourceDerivedEffectiveOutcomeTerminalCoverageConfigError(
            str(exc)
        ) from exc
    if snapshot.sha256 != DEFAULT_CONFIG_SHA256:
        raise SourceDerivedEffectiveOutcomeTerminalCoverageConfigError(
            "Coverage profile SHA-256 changed"
        )
    if (
        not isinstance(profile, dict)
        or set(profile)
        != {
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
        }
        or profile["schema_version"]
        != "ootang_epoch_source_derived_effective_outcome_terminal_coverage_profile_v1"
        or profile["profile_id"]
        != "ootang-epoch-source-derived-effective-outcome-terminal-coverage-v1"
        or profile["profile_version"] != "1.0.0-current-effective-dri-bijection"
        or profile["case"] != "ootang"
        or profile["artifact_status"]
        != "current_effective_dri_terminal_coverage_only_no_recovery_source_closure_or_lifecycle_authority"
        or profile["formal_warning_output"] is not False
        or profile["default_pipeline_member"] is not False
        or profile["upstream"] != EXPECTED_UPSTREAM
        or profile["runtime"] != EXPECTED_RUNTIME
        or profile["protocol"] != EXPECTED_PROTOCOL
        or profile["engineering_capabilities"] != _claims()
    ):
        raise SourceDerivedEffectiveOutcomeTerminalCoverageConfigError(
            "Coverage profile semantics changed"
        )
    for binding in EXPECTED_UPSTREAM.values():
        upstream = _child(root, binding["path"], name="coverage upstream")
        try:
            actual = registry._read_regular(  # noqa: SLF001
                upstream, name="coverage upstream", maximum_bytes=64 * 1024 * 1024
            )
        except registry.EpochRegistryError as exc:
            raise SourceDerivedEffectiveOutcomeTerminalCoverageConfigError(
                str(exc)
            ) from exc
        if actual.sha256 != binding["expected_sha256"]:
            raise SourceDerivedEffectiveOutcomeTerminalCoverageConfigError(
                f"Pinned coverage upstream changed:{binding['path']}"
            )
    overlay.load_source_derived_workset_overlay_profile()
    dependent_dispatch.load_source_derived_dependent_outcome_dispatch_profile()
    source_consumption.load_source_derived_outcome_consumption_profile()
    dependent_consumption.load_source_derived_dependent_outcome_consumption_profile()
    drain.load_drain_profile()
    recovery.load_workset_recovery_profile()
    profile["_profile_sha256"] = snapshot.sha256
    return profile


def source_derived_effective_outcome_terminal_coverage_paths(
    profile: Mapping[str, Any],
    *,
    registry_root: Path | None = None,
    active_root: Path | None = None,
    shadow_root: Path | None = None,
) -> SourceDerivedEffectiveOutcomeTerminalCoveragePaths:
    registry_path = (
        registry_root or ROOT / profile["runtime"]["registry_root"]
    ).resolve()
    recovery_root = _child(
        registry_path, profile["runtime"]["recovery_namespace"], name="recovery root"
    )
    root = _child(recovery_root, profile["runtime"]["namespace"], name="coverage root")
    active = (active_root or ROOT / profile["runtime"]["active_root"]).resolve()
    shadow = (shadow_root or ROOT / profile["runtime"]["shadow_root"]).resolve()
    consumption_profile = dependent_consumption.load_source_derived_dependent_outcome_consumption_profile()
    consumption_paths = (
        dependent_consumption.source_derived_dependent_outcome_consumption_paths(
            consumption_profile,
            registry_root=registry_path,
            active_root=active,
            shadow_root=shadow,
        )
    )
    return SourceDerivedEffectiveOutcomeTerminalCoveragePaths(
        registry_root=registry_path,
        root=root,
        proofs=_child(root, profile["runtime"]["proofs"], name="coverage proofs"),
        events=_child(root, profile["runtime"]["events"], name="coverage events"),
        status=_child(root, profile["runtime"]["status"], name="coverage status"),
        manager_lock=_child(
            registry_path, profile["runtime"]["manager_lock"], name="manager lock"
        ),
        active_root=active,
        shadow_root=shadow,
        consumption=consumption_paths,
    )


def _acquire_locks(
    paths: SourceDerivedEffectiveOutcomeTerminalCoveragePaths,
) -> list[BinaryIO]:
    try:
        return dependent_dispatch._acquire_locks(paths.consumption.dispatch)  # noqa: SLF001
    except dependent_dispatch.SourceDerivedDependentOutcomeDispatchBusyError as exc:
        raise SourceDerivedEffectiveOutcomeTerminalCoverageBusyError(str(exc)) from exc
    except dependent_dispatch.SourceDerivedDependentOutcomeDispatchError as exc:
        raise SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError(
            str(exc)
        ) from exc


def _release_locks(handles: Sequence[BinaryIO]) -> None:
    try:
        dependent_dispatch._release_locks(handles)  # noqa: SLF001
    except dependent_dispatch.SourceDerivedDependentOutcomeDispatchError as exc:
        raise SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError(
            str(exc)
        ) from exc


def _event_steps(
    events: Sequence[tuple[Mapping[str, Any], registry.ArtifactSnapshot]],
) -> set[str]:
    return {str(payload["step_id"]) for payload, _ in events}


def _require_complete_frontier(
    intents: Mapping[str, Any],
    receipts: Mapping[str, Any],
    events: Sequence[tuple[Mapping[str, Any], registry.ArtifactSnapshot]],
    *,
    name: str,
) -> bool:
    event_steps = _event_steps(events)
    return set(intents) == set(receipts) == event_steps


def _terminal_evidence(
    receipts: Mapping[str, tuple[dict[str, Any], registry.ArtifactSnapshot]],
    events: Sequence[tuple[dict[str, Any], registry.ArtifactSnapshot]],
    *,
    root: Path,
    provenance: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for event, event_snapshot in events:
        step = str(event.get("step_id"))
        receipt_record = receipts.get(step)
        if receipt_record is None:
            raise SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError(
                f"{provenance} terminal event lost its receipt"
            )
        receipt, receipt_snapshot = receipt_record
        identity = (
            event.get("key_id"),
            event.get("natural_key"),
            event.get("namespace_digest"),
        )
        if (
            identity
            != (
                receipt.get("key_id"),
                receipt.get("natural_key"),
                receipt.get("namespace_digest"),
            )
            or event.get("action") != "outcome_or_revision_consumed"
            or event.get("terminal_for_effective_key") is not True
            or event.get("terminal_for_recovery_v6_key") is not False
            or event.get("source_parent_terminal") is not False
        ):
            raise SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError(
                f"{provenance} terminal scope or identity changed"
            )
        key_id = str(identity[0])
        if key_id in result:
            raise SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError(
                f"{provenance} contains duplicate terminal evidence"
            )
        result[key_id] = {
            "evidence_kind": provenance,
            "step_id": step,
            "key_id": identity[0],
            "natural_key": identity[1],
            "namespace_digest": identity[2],
            "receipt": _reference(receipt_snapshot, root),
            "event": _reference(event_snapshot, root),
        }
    return result


def _load_assessment(
    paths: SourceDerivedEffectiveOutcomeTerminalCoveragePaths,
    now: datetime,
    *,
    load_derived_authority: overlay.LoadDerivedAuthority | None = None,
) -> tuple[
    overlay.SourceDerivedWorksetOverlayComputation | None,
    registry.ArtifactSnapshot | None,
    _Assessment | None,
    str,
]:
    overlay_paths = paths.consumption.dispatch.dispatch.overlay
    overlay_profile = overlay.load_source_derived_workset_overlay_profile()
    try:
        computation, waiting = overlay._load_computation(  # noqa: SLF001
            overlay_profile,
            overlay_paths,
            now,
            load_derived_authority=load_derived_authority,
        )
        if computation is None:
            return None, None, None, waiting
        stored, published = overlay._load_overlay_state(  # noqa: SLF001
            overlay_profile, overlay_paths, computation
        )
    except overlay.SourceDerivedWorksetOverlayError as exc:
        raise SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError(
            str(exc)
        ) from exc
    if stored is None or published is None:
        return computation, None, None, "the current effective overlay event is pending"
    overlay_event = published[1]
    dependent_paths = paths.consumption.dispatch
    try:
        source_receipts, source_events = dependent_dispatch._deep_replay_prerequisites(  # noqa: SLF001
            computation, overlay_event, dependent_paths, now=now
        )
        source_intents = source_consumption._named_records(  # noqa: SLF001
            dependent_paths.consumption.intents, name="coverage source intents"
        )
        source_frontier_complete = _require_complete_frontier(
            source_intents,
            source_receipts,
            source_events,
            name="source consumption",
        )
        terminals = dependent_dispatch._terminal_consumption_dependencies(  # noqa: SLF001
            computation, source_receipts, source_events, dependent_paths
        )
        dependent_candidates = dependent_dispatch._dependent_candidates(  # noqa: SLF001
            computation, terminals, dependent_paths
        )
        dependent_profile = (
            dependent_dispatch.load_source_derived_dependent_outcome_dispatch_profile()
        )
        dep_intents, dep_receipts, dep_events, _ = (
            dependent_dispatch._load_durable_state(  # noqa: SLF001
                dependent_profile,
                dependent_paths,
                computation,
                overlay_event,
                dependent_candidates,
                now=now,
                build_plan=dependent_dispatch._build_plan,  # noqa: SLF001
                verify_action=dependent_dispatch._recorded_materialization_output,  # noqa: SLF001
            )
        )
        dispatch_frontier_complete = _require_complete_frontier(
            dep_intents, dep_receipts, dep_events, name="dependent dispatch"
        )
    except dependent_dispatch.SourceDerivedDependentOutcomeDispatchBusyError as exc:
        raise SourceDerivedEffectiveOutcomeTerminalCoverageBusyError(str(exc)) from exc
    except dependent_dispatch.SourceDerivedDependentOutcomeDispatchError as exc:
        raise SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError(
            str(exc)
        ) from exc
    try:
        materialized = dependent_consumption._materialized_candidates(  # noqa: SLF001
            dependent_candidates, dep_intents, dep_receipts, dep_events
        )
        dependent_consumption_profile = dependent_consumption.load_source_derived_dependent_outcome_consumption_profile()
        cons_intents, cons_receipts, cons_events, _ = (
            dependent_consumption._load_durable_state(  # noqa: SLF001
                dependent_consumption_profile,
                paths.consumption,
                computation,
                overlay_event,
                materialized,
                build_plan=dependent_consumption._build_consumption_plan,  # noqa: SLF001
                verify_action=dependent_consumption._recorded_consumption_output,  # noqa: SLF001
            )
        )
        consumption_frontier_complete = _require_complete_frontier(
            cons_intents, cons_receipts, cons_events, name="dependent consumption"
        )
    except (
        dependent_consumption.SourceDerivedDependentOutcomeConsumptionBusyError
    ) as exc:
        raise SourceDerivedEffectiveOutcomeTerminalCoverageBusyError(str(exc)) from exc
    except dependent_consumption.SourceDerivedDependentOutcomeConsumptionError as exc:
        raise SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError(
            str(exc)
        ) from exc

    source_evidence = _terminal_evidence(
        source_receipts,
        source_events,
        root=dependent_paths.consumption.root,
        provenance="source_consumption_terminal_event",
    )
    dependent_evidence = _terminal_evidence(
        cons_receipts,
        cons_events,
        root=paths.consumption.root,
        provenance="dependent_consumption_terminal_event",
    )
    if set(source_evidence) & set(dependent_evidence):
        raise SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError(
            "Source and dependent terminal evidence overlap"
        )
    normalized = dependent_dispatch._effective_dri_rows(computation)  # noqa: SLF001
    if not normalized or len(normalized) > 4096:
        raise SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError(
            "Current effective D/R denominator is empty or oversized"
        )
    source_key = str(computation.cross_authority.source_item["natural_key"])
    dependent_by_key = {
        str(candidate.item["key_id"]): candidate for candidate in materialized
    }
    current_dri_natural_keys = {
        str(item["natural_key"]) for item, _, _ in normalized
    }
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    expected_source: set[str] = set()
    expected_dependent: set[str] = set()
    supported_dependent: set[str] = set()
    for item, dri_kind, index in normalized:
        key_id = str(item["key_id"])
        dependencies = item.get("dependency_keys")
        if not isinstance(dependencies, list):
            raise SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError(
                "Effective D/R dependency keys changed type"
            )
        proof_sha256: str | None = None
        if dependencies == [source_key]:
            dependency_class = "source_only"
            expected_source.add(key_id)
            evidence = source_evidence.get(key_id)
        elif (
            source_key in dependencies
            and any(dependency != source_key for dependency in dependencies)
            and all(
                dependency == source_key
                or dependency in current_dri_natural_keys
                for dependency in dependencies
            )
        ):
            dependency_class = "dependent"
            expected_dependent.add(key_id)
            supported_dependent.add(key_id)
            evidence = dependent_evidence.get(key_id)
            candidate = dependent_by_key.get(key_id)
            proof_sha256 = (
                candidate.dependency_proof_sha256 if candidate is not None else None
            )
        else:
            dependency_class = "unsupported"
            expected_dependent.add(key_id)
            evidence = None
        identity = (item["key_id"], item["natural_key"], item["namespace_digest"])
        if (
            evidence is not None
            and (
                evidence["key_id"],
                evidence["natural_key"],
                evidence["namespace_digest"],
            )
            != identity
        ):
            raise SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError(
                "Terminal evidence uses an old or different effective identity"
            )
        if evidence is None:
            missing.append(key_id)
        transition = recovery._transition_plan(item)  # noqa: SLF001
        row = {
            "topological_index": index,
            "dri_kind": dri_kind,
            "dependency_class": dependency_class,
            "key_id": item["key_id"],
            "natural_key": item["natural_key"],
            "namespace_digest": item["namespace_digest"],
            "dependency_keys": list(dependencies),
            "transition_plan_sha256": transition["plan_sha256"],
            "dependent_dependency_proof_sha256": proof_sha256,
            "terminal_evidence": evidence,
        }
        rows.append({**row, "coverage_row_sha256": _sha256(_canonical_bytes(row))})
    if set(source_evidence) != expected_source & set(source_evidence):
        extra = set(source_evidence) - expected_source
        if extra:
            raise SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError(
                "Source terminal evidence is outside the source-only denominator"
            )
    if set(dependent_evidence) - supported_dependent:
        raise SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError(
            "Dependent terminal evidence is outside the dependent denominator"
        )
    return (
        computation,
        overlay_event,
        _Assessment(
            rows=tuple(rows),
            missing_key_ids=tuple(missing),
            source_only_count=len(expected_source),
            dependent_count=len(expected_dependent),
            dependent_supported_count=len(supported_dependent),
            unsupported_count=len(expected_dependent - supported_dependent),
            upstream_frontiers_complete=(
                source_frontier_complete
                and dispatch_frontier_complete
                and consumption_frontier_complete
            ),
        ),
        "assessment ready",
    )


def _proof_payload(
    profile: Mapping[str, Any],
    paths: SourceDerivedEffectiveOutcomeTerminalCoveragePaths,
    computation: overlay.SourceDerivedWorksetOverlayComputation,
    overlay_event: registry.ArtifactSnapshot,
    assessment: _Assessment,
) -> dict[str, object]:
    effective = computation.effective_workset
    rows = [dict(row) for row in assessment.rows]
    return {
        "schema_version": profile["protocol"]["proof_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "assessment_scope": "current_overlay_normalized_d_or_r_only",
        "overlay_slot_id": computation.slot_id,
        "overlay_event": _reference(
            overlay_event, paths.consumption.dispatch.dispatch.overlay.root
        ),
        "effective_workset_keyset_sha256": effective["natural_keyset_sha256"],
        "effective_item_identity_set_sha256": effective["item_identity_set_sha256"],
        "effective_dependency_graph_sha256": effective["dependency_graph_sha256"],
        "required_key_count": len(rows),
        "covered_key_count": len(rows),
        "missing_key_count": 0,
        "missing_key_ids": [],
        "source_only_key_count": assessment.source_only_count,
        "dependent_key_count": assessment.dependent_count,
        "dependent_supported_key_count": assessment.dependent_supported_count,
        "unsupported_key_count": assessment.unsupported_count,
        "ordered_coverage_rows": rows,
        "ordered_coverage_rows_sha256": _sha256(_canonical_bytes(rows)),
        "implementation": _implementation_reference(),
        **_claims(complete=True),
    }


def _load_state(
    profile: Mapping[str, Any],
    paths: SourceDerivedEffectiveOutcomeTerminalCoveragePaths,
    expected_proof: Mapping[str, Any],
) -> tuple[
    tuple[dict[str, Any], registry.ArtifactSnapshot] | None,
    tuple[dict[str, Any], registry.ArtifactSnapshot] | None,
]:
    proof_entries = _strict_entries(paths.proofs, name="coverage proofs")
    if len(proof_entries) > 1:
        raise SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError(
            "Coverage proof namespace contains multiple assessments"
        )
    proof = None
    if proof_entries:
        matched = PROOF_NAME.fullmatch(proof_entries[0].name)
        payload, snapshot = _strict_json(proof_entries[0], name="coverage proof")
        if (
            matched is None
            or matched.group("digest") != snapshot.sha256
            or payload != expected_proof
        ):
            raise SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError(
                "Coverage proof differs from the current assessment"
            )
        proof = (payload, snapshot)
    event_entries = _strict_entries(paths.events, name="coverage events")
    if len(event_entries) > 1:
        raise SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError(
            "Coverage event namespace contains multiple assessments"
        )
    event = None
    if event_entries:
        if proof is None:
            raise SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError(
                "Coverage event is orphaned from its proof"
            )
        matched = EVENT_NAME.fullmatch(event_entries[0].name)
        payload, snapshot = _strict_json(event_entries[0], name="coverage event")
        body = dict(payload)
        entry = body.pop("entry_sha256", None)
        comparable = dict(body)
        recorded = comparable.pop("recorded_at_utc", None)
        expected = {
            "schema_version": profile["protocol"]["event_schema_version"],
            "profile_id": profile["profile_id"],
            "profile_sha256": profile["_profile_sha256"],
            "sequence_id": 1,
            "previous_entry_sha256": ZERO_HASH,
            "event_type": profile["protocol"]["event_type"],
            "proof": _reference(proof[1], paths.root),
            "overlay_slot_id": expected_proof["overlay_slot_id"],
            "required_key_count": expected_proof["required_key_count"],
            "covered_key_count": expected_proof["covered_key_count"],
            **_claims(complete=True),
        }
        if (
            matched is None
            or matched.group("sequence") != "00000000000000000001"
            or matched.group("entry") != entry
            or comparable != expected
            or entry != _sha256(_canonical_bytes(body))
        ):
            raise SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError(
                "Coverage singleton event changed"
            )
        _parse_utc(recorded, name="coverage event time")
        event = (payload, snapshot)
    return proof, event


def _append_event(
    profile: Mapping[str, Any],
    paths: SourceDerivedEffectiveOutcomeTerminalCoveragePaths,
    proof: registry.ArtifactSnapshot,
    proof_payload: Mapping[str, Any],
    *,
    now: datetime,
) -> registry.ArtifactSnapshot:
    body = {
        "schema_version": profile["protocol"]["event_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "sequence_id": 1,
        "previous_entry_sha256": ZERO_HASH,
        "event_type": profile["protocol"]["event_type"],
        "recorded_at_utc": _utc_text(now),
        "proof": _reference(proof, paths.root),
        "overlay_slot_id": proof_payload["overlay_slot_id"],
        "required_key_count": proof_payload["required_key_count"],
        "covered_key_count": proof_payload["covered_key_count"],
        **_claims(complete=True),
    }
    payload = {**body, "entry_sha256": _sha256(_canonical_bytes(body))}
    return _publish(
        paths.events / f"{1:020d}-{payload['entry_sha256']}.json",
        payload,
        root=paths.root,
        name="coverage event",
    )


def _write_status(
    profile: Mapping[str, Any],
    paths: SourceDerivedEffectiveOutcomeTerminalCoveragePaths,
    *,
    now: datetime,
    status: str,
    reason: str,
    assessment: _Assessment | None,
    proof: Path | None,
    event: Path | None,
) -> None:
    required = len(assessment.rows) if assessment else 0
    missing = len(assessment.missing_key_ids) if assessment else 0
    payload = {
        "schema_version": profile["protocol"]["status_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "recorded_at_utc": _utc_text(now),
        "status": status,
        "reason": reason,
        "proof_path": str(proof) if proof else None,
        "event_path": str(event) if event else None,
        "required_key_count": required,
        "covered_key_count": required - missing,
        "missing_key_count": missing,
        "missing_key_ids": list(assessment.missing_key_ids) if assessment else [],
        "all_current_effective_d_or_r_terminal": event is not None,
        "cache_authority": False,
        **{name: False for name in FALSE_CLAIMS},
    }
    try:
        registry._atomic_cache(  # noqa: SLF001
            paths.status,
            _canonical_bytes(payload),
            root=paths.root,
            name="coverage status",
        )
    except registry.EpochRegistryError as exc:
        raise SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError(
            str(exc)
        ) from exc


def _result(
    paths: SourceDerivedEffectiveOutcomeTerminalCoveragePaths,
    status: str,
    reason: str,
    assessment: _Assessment | None,
    *,
    proof: Path | None = None,
    event: Path | None = None,
) -> SourceDerivedEffectiveOutcomeTerminalCoverageResult:
    required = len(assessment.rows) if assessment else 0
    missing = len(assessment.missing_key_ids) if assessment else 0
    return SourceDerivedEffectiveOutcomeTerminalCoverageResult(
        status=status,
        reason=reason,
        status_path=paths.status,
        proof_path=proof,
        event_path=event,
        required_key_count=required,
        covered_key_count=required - missing,
        missing_key_count=missing,
        missing_key_ids=assessment.missing_key_ids if assessment else (),
        all_current_effective_d_or_r_terminal=event is not None,
    )


def _coordinate_source_derived_effective_outcome_terminal_coverage(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    registry_root: Path | None = None,
    active_root: Path | None = None,
    shadow_root: Path | None = None,
    clock: Callable[[], datetime] | None = None,
    fault_hook: FaultHook | None = None,
    load_derived_authority: overlay.LoadDerivedAuthority | None = None,
) -> SourceDerivedEffectiveOutcomeTerminalCoverageResult:
    profile = load_source_derived_effective_outcome_terminal_coverage_profile(
        config_path
    )
    paths = source_derived_effective_outcome_terminal_coverage_paths(
        profile,
        registry_root=registry_root,
        active_root=active_root,
        shadow_root=shadow_root,
    )
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    _utc_text(now)
    handles: list[BinaryIO] = []
    assessment = None
    try:
        handles = _acquire_locks(paths)
        computation, overlay_event, assessment, waiting = _load_assessment(
            paths, now, load_derived_authority=load_derived_authority
        )
        if computation is None or overlay_event is None or assessment is None:
            if _strict_entries(paths.proofs, name="coverage proofs") or _strict_entries(
                paths.events, name="coverage events"
            ):
                raise SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError(
                    "Coverage bytes outlived their exact overlay authority"
                )
            status = "waiting_for_current_effective_overlay"
            _write_status(
                profile,
                paths,
                now=now,
                status=status,
                reason=waiting,
                assessment=assessment,
                proof=None,
                event=None,
            )
            return _result(paths, status, waiting, assessment)
        if assessment.missing_key_ids or not assessment.upstream_frontiers_complete:
            if _strict_entries(paths.proofs, name="coverage proofs") or _strict_entries(
                paths.events, name="coverage events"
            ):
                raise SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError(
                    "Published coverage no longer covers the current denominator"
                )
            reason = "current effective D/R terminal evidence is incomplete"
            status = "waiting_for_effective_dri_terminal_coverage"
            _write_status(
                profile,
                paths,
                now=now,
                status=status,
                reason=reason,
                assessment=assessment,
                proof=None,
                event=None,
            )
            return _result(paths, status, reason, assessment)
        proof_payload = _proof_payload(
            profile, paths, computation, overlay_event, assessment
        )
        proof, event = _load_state(profile, paths, proof_payload)
        if event is not None:
            reason = "the exact current effective D/R terminal coverage is current"
            status = "effective_dri_terminal_coverage_current"
            _write_status(
                profile,
                paths,
                now=now,
                status=status,
                reason=reason,
                assessment=assessment,
                proof=proof[1].path if proof else None,
                event=event[1].path,
            )
            return _result(
                paths,
                status,
                reason,
                assessment,
                proof=proof[1].path if proof else None,
                event=event[1].path,
            )
        if proof is None:
            digest = _sha256(_canonical_bytes(proof_payload))
            proof_snapshot = _publish(
                paths.proofs / f"{digest}.json",
                proof_payload,
                root=paths.root,
                name="coverage proof",
            )
            if proof_snapshot.sha256 != digest:
                raise SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError(
                    "Coverage proof content address changed"
                )
            if fault_hook is not None:
                fault_hook("after_proof")
            status = "effective_dri_terminal_coverage_proof_published"
        else:
            proof_snapshot = proof[1]
            status = "effective_dri_terminal_coverage_event_forward_adopted"
        event_snapshot = _append_event(
            profile, paths, proof_snapshot, proof_payload, now=now
        )
        reason = (
            "the deterministic proof was published or adopted into its singleton event"
        )
        _write_status(
            profile,
            paths,
            now=now,
            status=status,
            reason=reason,
            assessment=assessment,
            proof=proof_snapshot.path,
            event=event_snapshot.path,
        )
        return _result(
            paths,
            status,
            reason,
            assessment,
            proof=proof_snapshot.path,
            event=event_snapshot.path,
        )
    except SourceDerivedEffectiveOutcomeTerminalCoverageBusyError:
        raise
    except SourceDerivedEffectiveOutcomeTerminalCoverageError as exc:
        if handles:
            try:
                _write_status(
                    profile,
                    paths,
                    now=now,
                    status="blocked_integrity",
                    reason=f"{type(exc).__name__}:{exc}",
                    assessment=assessment,
                    proof=None,
                    event=None,
                )
            except SourceDerivedEffectiveOutcomeTerminalCoverageError:
                pass
        raise
    except Exception as exc:
        raise SourceDerivedEffectiveOutcomeTerminalCoverageIntegrityError(
            f"Effective terminal coverage failed:{type(exc).__name__}:{exc}"
        ) from exc
    finally:
        if handles:
            _release_locks(handles)


def coordinate_source_derived_effective_outcome_terminal_coverage(
    *, config_path: Path = DEFAULT_CONFIG_PATH
) -> SourceDerivedEffectiveOutcomeTerminalCoverageResult:
    """Run one machine-only poll that is read-only toward upstream authorities."""

    return _coordinate_source_derived_effective_outcome_terminal_coverage(
        config_path=config_path
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = coordinate_source_derived_effective_outcome_terminal_coverage(
            config_path=args.config
        )
    except SourceDerivedEffectiveOutcomeTerminalCoverageBusyError as exc:
        print(json.dumps({"status": "busy", "reason": str(exc)}, sort_keys=True))
        return 3
    except SourceDerivedEffectiveOutcomeTerminalCoverageError as exc:
        print(
            json.dumps(
                {"status": "blocked_integrity", "reason": str(exc)}, sort_keys=True
            )
        )
        return 2
    print(json.dumps(result.__dict__, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
