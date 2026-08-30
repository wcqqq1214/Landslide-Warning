"""Prove exact terminal coverage of one current source-derived effective workset.

This authority joins three narrower published authorities by exact
``(key_id, natural_key, namespace_digest)`` identity: the source-ingest
parent, unchanged retained frozen-base rows, and current D/R rows.  It does
not claim recovery transition closure, epoch drain, lifecycle eligibility,
or terminality for any other effective-workset generation.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
from typing import Any, BinaryIO

from monitoring import ootang_epoch_drain as drain
from monitoring import ootang_epoch_registry as registry
from monitoring import (
    ootang_epoch_source_derived_effective_outcome_terminal_coverage as coverage,
)
from monitoring import (
    ootang_epoch_source_derived_retained_base_terminal_coverage as retained,
)
from monitoring import (
    ootang_epoch_source_derived_source_parent_terminal_aggregate as source_parent,
)
from monitoring import ootang_epoch_source_derived_workset_overlay as overlay


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = (
    ROOT
    / "config"
    / "ootang_epoch_source_derived_current_effective_workset_terminal_coverage.v1.json"
)
DEFAULT_CONFIG_SHA256 = (
    "f82269a8d2c60d9c41bf0d48dab626444084aae2df481f58c19a12d056bfc1b7"
)
IMPLEMENTATION_LOGICAL_PATH = (
    "code/monitoring/"
    "ootang_epoch_source_derived_current_effective_workset_terminal_coverage.py"
)
MAX_CONTROL_BYTES = 4 * 1024 * 1024
ZERO_HASH = "0" * 64
PROOF_NAME = re.compile(r"^(?P<digest>[0-9a-f]{64})\.json$")
EVENT_NAME = re.compile(r"^00000000000000000001-(?P<digest>[0-9a-f]{64})\.json$")

EXPECTED_UPSTREAM = {
    "coverage_profile": {
        "path": "config/ootang_epoch_source_derived_effective_outcome_terminal_coverage.v1.json",
        "expected_sha256": coverage.DEFAULT_CONFIG_SHA256,
    },
    "coverage_implementation": {
        "path": "code/monitoring/ootang_epoch_source_derived_effective_outcome_terminal_coverage.py",
        "expected_sha256": "2cf201b5f2b9e439b37e7b5880b39776f442ed189093b6d866c3276f7f6e4bf6",
    },
    "source_parent_profile": {
        "path": "config/ootang_epoch_source_derived_source_parent_terminal_aggregate.v1.json",
        "expected_sha256": source_parent.DEFAULT_CONFIG_SHA256,
    },
    "source_parent_implementation": {
        "path": "code/monitoring/ootang_epoch_source_derived_source_parent_terminal_aggregate.py",
        "expected_sha256": "43d541e0eb17144eddc127fdfb7b9c826c5038877d9ae4a9cad08cea5c92447d",
    },
    "retained_base_profile": {
        "path": "config/ootang_epoch_source_derived_retained_base_terminal_coverage.v1.json",
        "expected_sha256": retained.DEFAULT_CONFIG_SHA256,
    },
    "retained_base_implementation": {
        "path": "code/monitoring/ootang_epoch_source_derived_retained_base_terminal_coverage.py",
        "expected_sha256": "2db6b5bf204990aa3879cf90d8492a57c04e098e5ee3d8ede824af973e1308c8",
    },
    "overlay_profile": {
        "path": "config/ootang_epoch_source_derived_workset_overlay.v1.json",
        "expected_sha256": overlay.DEFAULT_CONFIG_SHA256,
    },
    "overlay_implementation": {
        "path": "code/monitoring/ootang_epoch_source_derived_workset_overlay.py",
        "expected_sha256": "54ea77652bc5f020146b777d98cd34e1ec26895f363d35a4cb5933b93ef11be7",
    },
    "drain_profile": {
        "path": "config/ootang_epoch_drain.v1.json",
        "expected_sha256": drain.DEFAULT_CONFIG_SHA256,
    },
    "drain_implementation": {
        "path": "code/monitoring/ootang_epoch_drain.py",
        "expected_sha256": "c4c226da087d354195fc3955ff60ff3b12af13f111d03cb59c5d64d0195a1602",
    },
}
EXPECTED_RUNTIME = {
    "registry_root": "runtime/ootang_epoch_registry_v1",
    "recovery_namespace": "workset_recovery_v1",
    "namespace": "source_derived_current_effective_workset_terminal_coverage_v1",
    "proofs": "proofs",
    "events": "events",
    "status": "status.json",
    "active_root": "runtime/ootang_prequential_live_v1",
    "shadow_root": "runtime/ootang_prequential_calibration_shadow_v1",
}
EXPECTED_PROTOCOL = {
    "proof_schema_version": (
        "ootang_epoch_source_derived_current_effective_workset_terminal_coverage_proof_v1"
    ),
    "event_schema_version": (
        "ootang_epoch_source_derived_current_effective_workset_terminal_coverage_event_v1"
    ),
    "status_schema_version": (
        "ootang_epoch_source_derived_current_effective_workset_terminal_coverage_status_v1"
    ),
    "event_type": (
        "epoch_source_derived_current_effective_workset_terminal_coverage_proved"
    ),
    "canonical_json": "utf8_sort_keys_compact_no_nan_trailing_lf",
    "initial_previous_entry_sha256": ZERO_HASH,
    "maximum_items": 4096,
    "maximum_control_bytes": MAX_CONTROL_BYTES,
    "publication_policy": (
        "deterministic_content_addressed_proof_then_singleton_event"
    ),
    "coverage_policy": (
        "exact_current_source_parent_plus_retained_base_plus_current_d_or_r_"
        "pairwise_disjoint_identity_union"
    ),
}
TRUE_CAPABILITIES = (
    "machine_only",
    "current_effective_overlay_deep_verified",
    "exact_source_parent_authority_deep_verified",
    "exact_retained_base_authority_deep_verified",
    "exact_current_dri_authority_deep_verified",
    "exact_disjoint_identity_union_implemented",
    "topological_effective_identity_bijection_implemented",
    "content_addressed_effective_workset_proof_implemented",
    "singleton_effective_workset_event_implemented",
    "proof_event_forward_adoption_implemented",
)
FALSE_CLAIMS = (
    "full_workset_terminal",
    "all_effective_items_terminal",
    "source_parent_terminal",
    "current_source_ingest_parent_terminal",
    "all_current_retained_base_items_terminal",
    "all_current_effective_d_or_r_terminal",
    "terminal_for_recovery_v6_key",
    "terminal_transition_closure_implemented",
    "transitive_terminal_closure_implemented",
    "bounded_workset_recovery_implemented",
    "all_reserved_items_settled",
    "all_reserved_successors_supported",
    "all_content_dependent_lanes_reserved",
    "recovery_v6_mutated",
    "effective_workset_overlay_mutated",
    "source_terminal_aggregate_mutated",
    "retained_base_coverage_mutated",
    "dri_terminal_coverage_mutated",
    "lifecycle_authority",
    "transition_authority",
    "drained_eligibility_current",
    "old_epoch_drained",
    "active_epoch_switch_implemented",
    "automatic_epoch_rotation_implemented",
    "trusted_anchor_receipt_verified",
    "e2_live_evidence_eligible",
    "real_activation_ready",
    "network_action_performed",
    "formal_warning_output",
)


class SourceDerivedCurrentEffectiveWorksetTerminalCoverageError(RuntimeError):
    """Base exact current-effective terminal-coverage error."""


class SourceDerivedCurrentEffectiveWorksetTerminalCoverageConfigError(
    SourceDerivedCurrentEffectiveWorksetTerminalCoverageError
):
    """The reviewed profile or a direct upstream pin changed."""


class SourceDerivedCurrentEffectiveWorksetTerminalCoverageIntegrityError(
    SourceDerivedCurrentEffectiveWorksetTerminalCoverageError
):
    """A leaf authority, exact union, or durable record failed closed."""


class SourceDerivedCurrentEffectiveWorksetTerminalCoverageBusyError(
    SourceDerivedCurrentEffectiveWorksetTerminalCoverageError
):
    """A shared machine coordinator lock is busy."""


@dataclass(frozen=True)
class SourceDerivedCurrentEffectiveWorksetTerminalCoveragePaths:
    registry_root: Path
    root: Path
    proofs: Path
    events: Path
    status: Path
    coverage: coverage.SourceDerivedEffectiveOutcomeTerminalCoveragePaths
    source_parent: source_parent.SourceDerivedSourceParentTerminalAggregatePaths
    retained: retained.SourceDerivedRetainedBaseTerminalCoveragePaths
    overlay: overlay.SourceDerivedWorksetOverlayPaths


@dataclass(frozen=True)
class SourceDerivedCurrentEffectiveWorksetTerminalCoverageResult:
    status: str
    reason: str
    status_path: Path
    proof_path: Path | None = None
    event_path: Path | None = None
    effective_item_count: int = 0
    source_parent_count: int = 0
    retained_base_count: int = 0
    current_dri_count: int = 0
    current_effective_workset_terminal_coverage: bool = False
    all_effective_items_terminal: bool = False
    terminal_transition_closure_implemented: bool = False
    lifecycle_authority: bool = False


@dataclass(frozen=True)
class _Assessment:
    rows: tuple[dict[str, Any], ...]
    source_parent_count: int
    retained_base_count: int
    current_dri_count: int


@dataclass(frozen=True)
class _Inputs:
    computation: overlay.SourceDerivedWorksetOverlayComputation
    overlay_object: registry.ArtifactSnapshot
    overlay_event: registry.ArtifactSnapshot
    coverage_payload: Mapping[str, Any]
    coverage_proof: registry.ArtifactSnapshot
    coverage_event: registry.ArtifactSnapshot
    parent_payload: Mapping[str, Any]
    parent_proof: registry.ArtifactSnapshot
    parent_event: registry.ArtifactSnapshot
    retained_payload: Mapping[str, Any]
    retained_proof: registry.ArtifactSnapshot
    retained_event: registry.ArtifactSnapshot


FaultHook = Callable[[str], None]


def _canonical_bytes(value: object) -> bytes:
    try:
        return registry._canonical_bytes(value)  # type: ignore[arg-type]  # noqa: SLF001
    except (TypeError, ValueError) as exc:
        raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageIntegrityError(
            "Value is not canonical JSON"
        ) from exc


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _claims(*, complete: bool = False) -> dict[str, bool]:
    return {
        **{name: True for name in TRUE_CAPABILITIES},
        "current_effective_workset_terminal_coverage": complete,
        **{name: False for name in FALSE_CLAIMS},
    }


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageIntegrityError(
            "Coverage clock must be timezone-aware"
        )
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _child(root: Path, relative: str, *, name: str) -> Path:
    try:
        return registry._contained(root, relative, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageConfigError(
            str(exc)
        ) from exc


def _reference(snapshot: registry.ArtifactSnapshot, root: Path) -> dict[str, object]:
    try:
        relative = snapshot.path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageIntegrityError(
            "Referenced authority escaped its root"
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
            name="current-effective coverage implementation",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
    except registry.EpochRegistryError as exc:
        raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageIntegrityError(
            str(exc)
        ) from exc
    return {
        "path": IMPLEMENTATION_LOGICAL_PATH,
        "sha256": snapshot.sha256,
        "size_bytes": snapshot.size_bytes,
    }


def load_source_derived_current_effective_workset_terminal_coverage_profile(
    path: Path = DEFAULT_CONFIG_PATH, *, project_root: Path = ROOT
) -> dict[str, Any]:
    root = project_root.resolve()
    resolved = path if path.is_absolute() else root / path
    if root != ROOT.resolve() or resolved.resolve() != DEFAULT_CONFIG_PATH.resolve():
        raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageConfigError(
            "Only the reviewed default current-effective coverage profile is accepted"
        )
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            resolved,
            name="current-effective coverage profile",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
        profile = registry._decode_json(  # noqa: SLF001
            snapshot.raw, name="current-effective coverage profile"
        )
    except registry.EpochRegistryError as exc:
        raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageConfigError(
            str(exc)
        ) from exc
    if snapshot.sha256 != DEFAULT_CONFIG_SHA256:
        raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageConfigError(
            "Current-effective coverage profile SHA-256 changed"
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
        != "ootang_epoch_source_derived_current_effective_workset_terminal_coverage_profile_v1"
        or profile["profile_id"]
        != "ootang-epoch-source-derived-current-effective-workset-terminal-coverage-v1"
        or profile["profile_version"] != "1.0.0-exact-current-effective-union"
        or profile["case"] != "ootang"
        or profile["artifact_status"]
        != "current_effective_workset_exact_terminal_coverage_only_no_recovery_closure_or_lifecycle_authority"
        or profile["formal_warning_output"] is not False
        or profile["default_pipeline_member"] is not False
        or profile["upstream"] != EXPECTED_UPSTREAM
        or profile["runtime"] != EXPECTED_RUNTIME
        or profile["protocol"] != EXPECTED_PROTOCOL
        or profile["engineering_capabilities"] != _claims()
    ):
        raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageConfigError(
            "Current-effective coverage profile semantics changed"
        )
    for binding in EXPECTED_UPSTREAM.values():
        upstream = _child(root, str(binding["path"]), name="coverage upstream")
        try:
            actual = registry._read_regular(  # noqa: SLF001
                upstream,
                name="current-effective coverage upstream",
                maximum_bytes=64 * 1024 * 1024,
            )
        except registry.EpochRegistryError as exc:
            raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageConfigError(
                str(exc)
            ) from exc
        if actual.sha256 != binding["expected_sha256"]:
            raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageConfigError(
                f"Pinned current-effective upstream changed:{binding['path']}"
            )
    coverage.load_source_derived_effective_outcome_terminal_coverage_profile()
    source_parent.load_source_derived_source_parent_terminal_aggregate_profile()
    retained.load_source_derived_retained_base_terminal_coverage_profile()
    overlay.load_source_derived_workset_overlay_profile()
    drain.load_drain_profile()
    profile["_profile_sha256"] = snapshot.sha256
    return profile


def source_derived_current_effective_workset_terminal_coverage_paths(
    profile: Mapping[str, Any],
    *,
    registry_root: Path | None = None,
    active_root: Path | None = None,
    shadow_root: Path | None = None,
) -> SourceDerivedCurrentEffectiveWorksetTerminalCoveragePaths:
    registry_path = (
        registry_root or ROOT / str(profile["runtime"]["registry_root"])
    ).resolve()
    coverage_paths = coverage.source_derived_effective_outcome_terminal_coverage_paths(
        coverage.load_source_derived_effective_outcome_terminal_coverage_profile(),
        registry_root=registry_path,
        active_root=active_root,
        shadow_root=shadow_root,
    )
    parent_paths = source_parent.source_derived_source_parent_terminal_aggregate_paths(
        source_parent.load_source_derived_source_parent_terminal_aggregate_profile(),
        registry_root=registry_path,
        active_root=coverage_paths.active_root,
        shadow_root=coverage_paths.shadow_root,
    )
    retained_paths = retained.source_derived_retained_base_terminal_coverage_paths(
        retained.load_source_derived_retained_base_terminal_coverage_profile(),
        registry_root=registry_path,
        active_root=coverage_paths.active_root,
        shadow_root=coverage_paths.shadow_root,
    )
    overlay_paths = coverage_paths.consumption.dispatch.dispatch.overlay
    if (
        parent_paths.coverage.root.resolve() != coverage_paths.root.resolve()
        or retained_paths.overlay.root.resolve() != overlay_paths.root.resolve()
    ):
        raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageConfigError(
            "Leaf authorities do not share one current recovery root"
        )
    recovery_root = _child(
        registry_path,
        str(profile["runtime"]["recovery_namespace"]),
        name="current-effective recovery root",
    )
    root = _child(
        recovery_root,
        str(profile["runtime"]["namespace"]),
        name="current-effective coverage root",
    )
    return SourceDerivedCurrentEffectiveWorksetTerminalCoveragePaths(
        registry_root=registry_path,
        root=root,
        proofs=_child(root, str(profile["runtime"]["proofs"]), name="proofs"),
        events=_child(root, str(profile["runtime"]["events"]), name="events"),
        status=_child(root, str(profile["runtime"]["status"]), name="status"),
        coverage=coverage_paths,
        source_parent=parent_paths,
        retained=retained_paths,
        overlay=overlay_paths,
    )


def _identity(item: Mapping[str, Any]) -> dict[str, str]:
    try:
        return retained._identity(item)  # noqa: SLF001
    except retained.SourceDerivedRetainedBaseTerminalCoverageError as exc:
        raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageIntegrityError(
            str(exc)
        ) from exc


def _identity_tuple(item: Mapping[str, Any]) -> tuple[str, str, str]:
    identity = _identity(item)
    return (
        identity["key_id"],
        identity["natural_key"],
        identity["namespace_digest"],
    )


def _same_snapshot(
    left: registry.ArtifactSnapshot, right: registry.ArtifactSnapshot
) -> bool:
    return (
        left.path.resolve() == right.path.resolve()
        and left.sha256 == right.sha256
        and left.size_bytes == right.size_bytes
        and left.raw == right.raw
    )


def _strict_entries(directory: Path, *, name: str) -> tuple[Path, ...]:
    try:
        return retained._strict_entries(directory, name=name)  # noqa: SLF001
    except retained.SourceDerivedRetainedBaseTerminalCoverageError as exc:
        raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageIntegrityError(
            str(exc)
        ) from exc


def _strict_json(
    path: Path, *, name: str
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    try:
        return retained._strict_json(path, name=name)  # noqa: SLF001
    except retained.SourceDerivedRetainedBaseTerminalCoverageError as exc:
        raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageIntegrityError(
            str(exc)
        ) from exc


def _own_children_present(
    paths: SourceDerivedCurrentEffectiveWorksetTerminalCoveragePaths,
) -> bool:
    return bool(
        _strict_entries(paths.proofs, name="current-effective proofs")
        or _strict_entries(paths.events, name="current-effective events")
    )


def _load_inputs(
    paths: SourceDerivedCurrentEffectiveWorksetTerminalCoveragePaths,
    now: datetime,
    *,
    load_derived_authority: overlay.LoadDerivedAuthority | None,
) -> tuple[_Inputs | None, str, str]:
    coverage_profile = (
        coverage.load_source_derived_effective_outcome_terminal_coverage_profile()
    )
    computation, coverage_overlay_event, coverage_assessment, waiting = (
        coverage._load_assessment(  # noqa: SLF001
            paths.coverage,
            now,
            load_derived_authority=load_derived_authority,
        )
    )
    if (
        computation is None
        or coverage_overlay_event is None
        or coverage_assessment is None
    ):
        return None, "waiting_for_current_effective_overlay", waiting

    overlay_profile = overlay.load_source_derived_workset_overlay_profile()
    overlay_object_record, overlay_event_record = overlay._load_overlay_state(  # noqa: SLF001
        overlay_profile, paths.overlay, computation
    )
    if overlay_object_record is None or overlay_event_record is None:
        return (
            None,
            "waiting_for_published_effective_overlay",
            "matching current effective overlay object/event is pending",
        )
    if not _same_snapshot(overlay_event_record[1], coverage_overlay_event):
        raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageIntegrityError(
            "D/R coverage and retained-base partition observed different overlay events"
        )

    coverage_payload = coverage._proof_payload(  # noqa: SLF001
        coverage_profile,
        paths.coverage,
        computation,
        coverage_overlay_event,
        coverage_assessment,
    )
    coverage_proof_record, coverage_event_record = coverage._load_state(  # noqa: SLF001
        coverage_profile, paths.coverage, coverage_payload
    )
    if (
        coverage_assessment.missing_key_ids
        or not coverage_assessment.upstream_frontiers_complete
        or coverage_proof_record is None
        or coverage_event_record is None
    ):
        return (
            None,
            "waiting_for_dri_terminal_coverage",
            "matching exact current D/R terminal coverage proof/event is pending",
        )

    partition = retained._retained_partition(computation)  # noqa: SLF001
    retained_assessment = retained._retained_assessment(  # noqa: SLF001
        paths.retained, computation, partition, now
    )
    retained_profile = (
        retained.load_source_derived_retained_base_terminal_coverage_profile()
    )
    retained_payload = retained._proof_payload(  # noqa: SLF001
        retained_profile,
        paths.retained,
        computation,
        overlay_object_record[1],
        overlay_event_record[1],
        retained_assessment,
    ) if retained_assessment.complete else None
    if retained_payload is None:
        return (
            None,
            "waiting_for_retained_base_terminal_coverage",
            "exact retained frozen-base subset lacks terminal evidence",
        )
    retained_proof_record, retained_event_record = retained._load_state(  # noqa: SLF001
        retained_profile, paths.retained, retained_payload
    )
    if retained_proof_record is None or retained_event_record is None:
        return (
            None,
            "waiting_for_retained_base_terminal_coverage",
            "matching retained-base terminal coverage proof/event is pending",
        )

    parent_profile = (
        source_parent.load_source_derived_source_parent_terminal_aggregate_profile()
    )
    parent_payload = source_parent._proof_payload(  # noqa: SLF001
        parent_profile,
        paths.source_parent,
        computation,
        coverage_overlay_event,
        coverage_proof_record[1],
        coverage_event_record[1],
        coverage_payload,
    )
    parent_proof_record, parent_event_record = source_parent._load_state(  # noqa: SLF001
        parent_profile, paths.source_parent, parent_payload
    )
    if parent_proof_record is None or parent_event_record is None:
        return (
            None,
            "waiting_for_source_parent_terminal_aggregate",
            "matching current source-ingest parent terminal proof/event is pending",
        )

    return (
        _Inputs(
            computation=computation,
            overlay_object=overlay_object_record[1],
            overlay_event=overlay_event_record[1],
            coverage_payload=coverage_payload,
            coverage_proof=coverage_proof_record[1],
            coverage_event=coverage_event_record[1],
            parent_payload=parent_payload,
            parent_proof=parent_proof_record[1],
            parent_event=parent_event_record[1],
            retained_payload=retained_payload,
            retained_proof=retained_proof_record[1],
            retained_event=retained_event_record[1],
        ),
        "inputs_current",
        "all exact current family authorities are published",
    )


def _assessment(inputs: _Inputs) -> _Assessment:
    effective_items = tuple(inputs.computation.effective_items)
    if not 0 < len(effective_items) <= EXPECTED_PROTOCOL["maximum_items"]:
        raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageIntegrityError(
            "Current effective denominator is empty or oversized"
        )
    effective_identities = tuple(_identity_tuple(item) for item in effective_items)
    effective_set = set(effective_identities)
    if len(effective_set) != len(effective_identities):
        raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageIntegrityError(
            "Current effective identity denominator is not unique"
        )

    parent_identity = _identity_tuple(
        inputs.parent_payload["source_parent_identity"]
    )
    retained_targets = tuple(inputs.retained_payload["retained_target_rows"])
    retained_rows = tuple(inputs.retained_payload["terminal_coverage_rows"])
    dri_rows = tuple(inputs.coverage_payload["ordered_coverage_rows"])
    retained_set = {_identity_tuple(row) for row in retained_targets}
    retained_terminal_by_identity = {
        _identity_tuple(row): row for row in retained_rows
    }
    dri_by_identity = {_identity_tuple(row): row for row in dri_rows}
    if (
        len(retained_set) != len(retained_targets)
        or len(retained_terminal_by_identity) != len(retained_rows)
        or len(dri_by_identity) != len(dri_rows)
        or set(retained_terminal_by_identity) != retained_set
    ):
        raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageIntegrityError(
            "One leaf authority is not an exact identity bijection"
        )
    parent_set = {parent_identity}
    dri_set = set(dri_by_identity)
    if (
        parent_set & retained_set
        or parent_set & dri_set
        or retained_set & dri_set
        or parent_set | retained_set | dri_set != effective_set
    ):
        raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageIntegrityError(
            "Source parent, retained base, and current D/R are not an exact disjoint union"
        )
    if (
        inputs.parent_payload.get("current_source_ingest_parent_terminal") is not True
        or inputs.retained_payload.get("all_current_retained_base_items_terminal")
        is not True
        or inputs.coverage_payload.get("all_current_effective_d_or_r_terminal")
        is not True
        or inputs.retained_payload.get("required_key_count") != len(retained_set)
        or inputs.retained_payload.get("covered_key_count") != len(retained_set)
        or inputs.retained_payload.get("missing_key_ids") != []
        or inputs.coverage_payload.get("required_key_count") != len(dri_set)
        or inputs.coverage_payload.get("covered_key_count") != len(dri_set)
        or inputs.coverage_payload.get("missing_key_count") != 0
        or inputs.coverage_payload.get("missing_key_ids") != []
    ):
        raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageIntegrityError(
            "Leaf terminal claims or exact counts changed"
        )
    partition_parent = _identity_tuple(
        inputs.retained_payload["source_parent_excluded_identity"]
    )
    if partition_parent != parent_identity:
        raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageIntegrityError(
            "Retained-base exclusion and source-parent authority disagree"
        )

    rows: list[dict[str, Any]] = []
    for index, item in enumerate(effective_items):
        identity = _identity_tuple(item)
        if identity == parent_identity:
            authority_kind = "source_parent_terminal_aggregate"
            proof = inputs.parent_proof
            event = inputs.parent_event
            leaf_row_sha256 = None
        elif identity in retained_set:
            authority_kind = "retained_base_terminal_coverage"
            proof = inputs.retained_proof
            event = inputs.retained_event
            leaf_row_sha256 = retained_terminal_by_identity[identity][
                "coverage_row_sha256"
            ]
        else:
            authority_kind = "current_dri_terminal_coverage"
            proof = inputs.coverage_proof
            event = inputs.coverage_event
            leaf_row_sha256 = dri_by_identity[identity]["coverage_row_sha256"]
        body = {
            "topological_index": index,
            **_identity(item),
            "family": item["family"],
            "terminal_authority_kind": authority_kind,
            "terminal_authority_proof_sha256": proof.sha256,
            "terminal_authority_event_sha256": event.sha256,
            "leaf_coverage_row_sha256": leaf_row_sha256,
        }
        rows.append(
            {**body, "effective_terminal_row_sha256": _sha256(_canonical_bytes(body))}
        )
    if tuple(_identity_tuple(row) for row in rows) != effective_identities:
        raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageIntegrityError(
            "Topological effective coverage order changed"
        )
    expected_count = inputs.computation.effective_workset["item_count"]
    if (
        expected_count != len(rows)
        or 1 + len(retained_set) + len(dri_set) != len(rows)
    ):
        raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageIntegrityError(
            "Effective workset and family counts disagree"
        )
    return _Assessment(tuple(rows), 1, len(retained_set), len(dri_set))


def _proof_payload(
    profile: Mapping[str, Any],
    paths: SourceDerivedCurrentEffectiveWorksetTerminalCoveragePaths,
    inputs: _Inputs,
    assessment: _Assessment,
) -> dict[str, object]:
    rows = [dict(row) for row in assessment.rows]
    effective = inputs.computation.effective_workset
    return {
        "schema_version": profile["protocol"]["proof_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "authority_scope": (
            "one_current_source_derived_effective_overlay_exact_identity_union_only"
        ),
        "overlay_slot_id": inputs.computation.slot_id,
        "overlay_object": _reference(inputs.overlay_object, paths.overlay.root),
        "overlay_event": _reference(inputs.overlay_event, paths.overlay.root),
        "source_parent_terminal_proof": _reference(
            inputs.parent_proof, paths.source_parent.root
        ),
        "source_parent_terminal_event": _reference(
            inputs.parent_event, paths.source_parent.root
        ),
        "retained_base_terminal_proof": _reference(
            inputs.retained_proof, paths.retained.root
        ),
        "retained_base_terminal_event": _reference(
            inputs.retained_event, paths.retained.root
        ),
        "current_dri_terminal_proof": _reference(
            inputs.coverage_proof, paths.coverage.root
        ),
        "current_dri_terminal_event": _reference(
            inputs.coverage_event, paths.coverage.root
        ),
        "effective_item_count": len(rows),
        "family_counts": {
            "source_parent_count": assessment.source_parent_count,
            "retained_base_count": assessment.retained_base_count,
            "current_dri_count": assessment.current_dri_count,
        },
        "effective_workset_keyset_sha256": effective["natural_keyset_sha256"],
        "effective_item_identity_set_sha256": effective[
            "item_identity_set_sha256"
        ],
        "effective_dependency_graph_sha256": effective[
            "dependency_graph_sha256"
        ],
        "source_parent_identity": dict(
            inputs.parent_payload["source_parent_identity"]
        ),
        "retained_target_rows_sha256": inputs.retained_payload[
            "retained_target_rows_sha256"
        ],
        "retained_terminal_coverage_rows_sha256": inputs.retained_payload[
            "terminal_coverage_rows_sha256"
        ],
        "current_dri_ordered_coverage_rows_sha256": inputs.coverage_payload[
            "ordered_coverage_rows_sha256"
        ],
        "ordered_effective_terminal_rows": rows,
        "ordered_effective_terminal_rows_sha256": _sha256(
            _canonical_bytes(rows)
        ),
        "pairwise_disjoint_identity_union": True,
        "exact_current_effective_identity_bijection": True,
        "implementation": _implementation_reference(),
        **_claims(complete=True),
    }


def _publish(
    path: Path, payload: Mapping[str, Any], *, root: Path, name: str
) -> registry.ArtifactSnapshot:
    raw = _canonical_bytes(payload)
    if len(raw) > MAX_CONTROL_BYTES:
        raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageIntegrityError(
            f"{name} capacity exceeded"
        )
    try:
        return drain._publish_once_durable(  # noqa: SLF001
            path, raw, root=root, name=name
        )
    except drain.EpochDrainError as exc:
        raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageIntegrityError(
            str(exc)
        ) from exc


def _load_state(
    profile: Mapping[str, Any],
    paths: SourceDerivedCurrentEffectiveWorksetTerminalCoveragePaths,
    expected: Mapping[str, Any],
) -> tuple[
    tuple[dict[str, Any], registry.ArtifactSnapshot] | None,
    tuple[dict[str, Any], registry.ArtifactSnapshot] | None,
]:
    proofs = _strict_entries(paths.proofs, name="current-effective proofs")
    events = _strict_entries(paths.events, name="current-effective events")
    if len(proofs) > 1 or len(events) > 1:
        raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageIntegrityError(
            "Current-effective singleton authority branched"
        )
    proof = None
    if proofs:
        payload, snapshot = _strict_json(proofs[0], name="current-effective proof")
        match = PROOF_NAME.fullmatch(proofs[0].name)
        if (
            match is None
            or match.group("digest") != snapshot.sha256
            or payload != expected
        ):
            raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageIntegrityError(
                "Current-effective proof differs from current exact union"
            )
        proof = (payload, snapshot)
    event = None
    if events:
        if proof is None:
            raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageIntegrityError(
                "Current-effective event is orphaned"
            )
        payload, snapshot = _strict_json(events[0], name="current-effective event")
        body = dict(payload)
        entry = body.pop("entry_sha256", None)
        recorded = body.pop("recorded_at_utc", None)
        expected_body = {
            "schema_version": profile["protocol"]["event_schema_version"],
            "profile_id": profile["profile_id"],
            "profile_sha256": profile["_profile_sha256"],
            "sequence_id": 1,
            "previous_entry_sha256": ZERO_HASH,
            "event_type": profile["protocol"]["event_type"],
            "proof": _reference(proof[1], paths.root),
            "overlay_slot_id": expected["overlay_slot_id"],
            "effective_item_count": expected["effective_item_count"],
            "ordered_effective_terminal_rows_sha256": expected[
                "ordered_effective_terminal_rows_sha256"
            ],
            **_claims(complete=True),
        }
        match = EVENT_NAME.fullmatch(events[0].name)
        if (
            match is None
            or match.group("digest") != entry
            or body != expected_body
            or entry
            != _sha256(_canonical_bytes({**body, "recorded_at_utc": recorded}))
        ):
            raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageIntegrityError(
                "Current-effective singleton event changed"
            )
        try:
            registry._utc_timestamp(  # noqa: SLF001
                recorded, name="current-effective event time"
            )
        except registry.EpochRegistryError as exc:
            raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageIntegrityError(
                str(exc)
            ) from exc
        event = (payload, snapshot)
    return proof, event


def _append_event(
    profile: Mapping[str, Any],
    paths: SourceDerivedCurrentEffectiveWorksetTerminalCoveragePaths,
    proof: registry.ArtifactSnapshot,
    expected: Mapping[str, Any],
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
        "overlay_slot_id": expected["overlay_slot_id"],
        "effective_item_count": expected["effective_item_count"],
        "ordered_effective_terminal_rows_sha256": expected[
            "ordered_effective_terminal_rows_sha256"
        ],
        **_claims(complete=True),
    }
    payload = {**body, "entry_sha256": _sha256(_canonical_bytes(body))}
    return _publish(
        paths.events / f"{1:020d}-{payload['entry_sha256']}.json",
        payload,
        root=paths.root,
        name="current-effective event",
    )


def _write_status(
    profile: Mapping[str, Any],
    paths: SourceDerivedCurrentEffectiveWorksetTerminalCoveragePaths,
    *,
    now: datetime,
    status: str,
    reason: str,
    assessment: _Assessment | None,
    proof: Path | None,
    event: Path | None,
) -> None:
    payload = {
        "schema_version": profile["protocol"]["status_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "observed_at_utc": _utc_text(now),
        "status": status,
        "reason": reason,
        "effective_item_count": len(assessment.rows) if assessment else 0,
        "source_parent_count": assessment.source_parent_count if assessment else 0,
        "retained_base_count": assessment.retained_base_count if assessment else 0,
        "current_dri_count": assessment.current_dri_count if assessment else 0,
        "proof_path": str(proof.relative_to(paths.root)) if proof else None,
        "event_path": str(event.relative_to(paths.root)) if event else None,
        "cache_authority": False,
        **_claims(complete=event is not None),
    }
    try:
        registry._atomic_cache(  # noqa: SLF001
            paths.status,
            _canonical_bytes(payload),
            root=paths.root,
            name="current-effective coverage status",
        )
    except registry.EpochRegistryError as exc:
        raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageIntegrityError(
            str(exc)
        ) from exc


def _result(
    paths: SourceDerivedCurrentEffectiveWorksetTerminalCoveragePaths,
    status: str,
    reason: str,
    assessment: _Assessment | None,
    *,
    proof: Path | None = None,
    event: Path | None = None,
) -> SourceDerivedCurrentEffectiveWorksetTerminalCoverageResult:
    return SourceDerivedCurrentEffectiveWorksetTerminalCoverageResult(
        status=status,
        reason=reason,
        status_path=paths.status,
        proof_path=proof,
        event_path=event,
        effective_item_count=len(assessment.rows) if assessment else 0,
        source_parent_count=assessment.source_parent_count if assessment else 0,
        retained_base_count=assessment.retained_base_count if assessment else 0,
        current_dri_count=assessment.current_dri_count if assessment else 0,
        current_effective_workset_terminal_coverage=event is not None,
    )


def _coordinate_source_derived_current_effective_workset_terminal_coverage(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    registry_root: Path | None = None,
    active_root: Path | None = None,
    shadow_root: Path | None = None,
    clock: Callable[[], datetime] | None = None,
    fault_hook: FaultHook | None = None,
    load_derived_authority: overlay.LoadDerivedAuthority | None = None,
) -> SourceDerivedCurrentEffectiveWorksetTerminalCoverageResult:
    profile = load_source_derived_current_effective_workset_terminal_coverage_profile(
        config_path
    )
    paths = source_derived_current_effective_workset_terminal_coverage_paths(
        profile,
        registry_root=registry_root,
        active_root=active_root,
        shadow_root=shadow_root,
    )
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    _utc_text(now)
    handles: Sequence[BinaryIO] = ()
    assessment = None
    try:
        handles = coverage._acquire_locks(paths.coverage)  # noqa: SLF001
        inputs, status, reason = _load_inputs(
            paths, now, load_derived_authority=load_derived_authority
        )
        if inputs is None:
            if _own_children_present(paths):
                raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageIntegrityError(
                    "Current-effective authority outlived one exact family authority"
                )
            _write_status(
                profile,
                paths,
                now=now,
                status=status,
                reason=reason,
                assessment=None,
                proof=None,
                event=None,
            )
            return _result(paths, status, reason, None)
        assessment = _assessment(inputs)
        expected = _proof_payload(profile, paths, inputs, assessment)
        proof, event = _load_state(profile, paths, expected)
        if event is not None:
            status = "current_effective_workset_terminal_coverage_current"
            reason = "exact current effective terminal-coverage event is current"
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
            digest = _sha256(_canonical_bytes(expected))
            proof_snapshot = _publish(
                paths.proofs / f"{digest}.json",
                expected,
                root=paths.root,
                name="current-effective proof",
            )
            if proof_snapshot.sha256 != digest:
                raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageIntegrityError(
                    "Current-effective proof content address changed"
                )
            if fault_hook is not None:
                fault_hook("after_proof")
            status = "current_effective_workset_terminal_coverage_proof_published"
        else:
            proof_snapshot = proof[1]
            status = "current_effective_workset_terminal_coverage_event_forward_adopted"
        event_snapshot = _append_event(profile, paths, proof_snapshot, expected, now)
        reason = "exact current effective proof was published or forward-adopted"
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
    except coverage.SourceDerivedEffectiveOutcomeTerminalCoverageBusyError as exc:
        raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageBusyError(
            str(exc)
        ) from exc
    except SourceDerivedCurrentEffectiveWorksetTerminalCoverageError:
        raise
    except (
        coverage.SourceDerivedEffectiveOutcomeTerminalCoverageError,
        source_parent.SourceDerivedSourceParentTerminalAggregateError,
        retained.SourceDerivedRetainedBaseTerminalCoverageError,
        overlay.SourceDerivedWorksetOverlayError,
    ) as exc:
        raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageIntegrityError(
            str(exc)
        ) from exc
    except Exception as exc:
        raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageIntegrityError(
            f"Current-effective coordination failed:{type(exc).__name__}:{exc}"
        ) from exc
    finally:
        if handles:
            try:
                coverage._release_locks(handles)  # noqa: SLF001
            except coverage.SourceDerivedEffectiveOutcomeTerminalCoverageError as exc:
                raise SourceDerivedCurrentEffectiveWorksetTerminalCoverageIntegrityError(
                    str(exc)
                ) from exc


def coordinate_source_derived_current_effective_workset_terminal_coverage(
    **kwargs: Any,
) -> SourceDerivedCurrentEffectiveWorksetTerminalCoverageResult:
    """Run one machine-only exact current-effective coverage poll."""

    return _coordinate_source_derived_current_effective_workset_terminal_coverage(
        **kwargs
    )
