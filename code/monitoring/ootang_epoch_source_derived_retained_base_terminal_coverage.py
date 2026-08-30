"""Prove terminal coverage of the exact retained frozen-base subset.

The authority is deliberately narrower than whole-effective-workset coverage.
It reconstructs the current source-derived overlay, removes the separately
handled source-ingest parent plus D/R/I identities, and accepts terminal
evidence only for frozen identities that survive unchanged.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
from typing import Any, BinaryIO

from monitoring import ootang_epoch_drain as drain
from monitoring import ootang_epoch_manifest_terminal_coverage as manifest_coverage
from monitoring import ootang_epoch_registry as registry
from monitoring import ootang_epoch_source_derived_workset_overlay as overlay
from monitoring import ootang_epoch_step_dependency_reservation as sidecar
from monitoring import ootang_epoch_workset_recovery as recovery


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = (
    ROOT
    / "config"
    / "ootang_epoch_source_derived_retained_base_terminal_coverage.v1.json"
)
DEFAULT_CONFIG_SHA256 = (
    "8db880133d09d785784aec029f7b84e4410b042af39a50c2eda7635e74d11456"
)
IMPLEMENTATION_LOGICAL_PATH = (
    "code/monitoring/ootang_epoch_source_derived_retained_base_terminal_coverage.py"
)
MAX_CONTROL_BYTES = 4 * 1024 * 1024
ZERO_HASH = "0" * 64
PROOF_NAME = re.compile(r"^(?P<digest>[0-9a-f]{64})\.json$")
EVENT_NAME = re.compile(r"^00000000000000000001-(?P<digest>[0-9a-f]{64})\.json$")

EXPECTED_UPSTREAM = {
    "overlay_profile": {
        "path": "config/ootang_epoch_source_derived_workset_overlay.v1.json",
        "expected_sha256": overlay.DEFAULT_CONFIG_SHA256,
    },
    "overlay_implementation": {
        "path": "code/monitoring/ootang_epoch_source_derived_workset_overlay.py",
        "expected_sha256": "54ea77652bc5f020146b777d98cd34e1ec26895f363d35a4cb5933b93ef11be7",
    },
    "step_dependency_profile": {
        "path": "config/ootang_epoch_step_dependency_reservation.v1.json",
        "expected_sha256": sidecar.DEFAULT_CONFIG_SHA256,
    },
    "step_dependency_implementation": {
        "path": "code/monitoring/ootang_epoch_step_dependency_reservation.py",
        "expected_sha256": "c385b7c8783d86831561d5c1179b05efc78e3f5ef99a912b44382143625e5190",
    },
    "manifest_coverage_profile": {
        "path": "config/ootang_epoch_manifest_terminal_coverage.v1.json",
        "expected_sha256": manifest_coverage.DEFAULT_CONFIG_SHA256,
    },
    "manifest_coverage_implementation": {
        "path": "code/monitoring/ootang_epoch_manifest_terminal_coverage.py",
        "expected_sha256": "5cb928ab7ee8d3ab15b6da8be29d9b597598b200744ce30d0ae01a8568fa15b2",
    },
    "recovery_profile": {
        "path": "config/ootang_epoch_workset_recovery.v1.json",
        "expected_sha256": recovery.DEFAULT_CONFIG_SHA256,
    },
    "recovery_implementation": {
        "path": "code/monitoring/ootang_epoch_workset_recovery.py",
        "expected_sha256": "b9f25133eef9cb94fb255bab588edcd573f0fa792ef82c4ddb9068d0a44a6c51",
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
    "namespace": "source_derived_retained_base_terminal_coverage_v1",
    "proofs": "proofs",
    "events": "events",
    "status": "status.json",
    "active_root": "runtime/ootang_prequential_live_v1",
    "shadow_root": "runtime/ootang_prequential_calibration_shadow_v1",
}
EXPECTED_PROTOCOL = {
    "proof_schema_version": (
        "ootang_epoch_source_derived_retained_base_terminal_coverage_proof_v1"
    ),
    "event_schema_version": (
        "ootang_epoch_source_derived_retained_base_terminal_coverage_event_v1"
    ),
    "status_schema_version": (
        "ootang_epoch_source_derived_retained_base_terminal_coverage_status_v1"
    ),
    "event_type": "epoch_source_derived_retained_base_terminal_coverage_proved",
    "canonical_json": "utf8_sort_keys_compact_no_nan_trailing_lf",
    "initial_previous_entry_sha256": ZERO_HASH,
    "maximum_items": 4096,
    "maximum_control_bytes": MAX_CONTROL_BYTES,
    "publication_policy": (
        "deterministic_content_addressed_proof_then_singleton_event"
    ),
    "coverage_policy": (
        "exact_unchanged_frozen_base_minus_current_source_parent_minus_rebound_"
        "old_minus_invalidated_equals_per_item_terminal_evidence"
    ),
    "empty_subset_policy": "publish_exact_vacuous_coverage",
}
TRUE_CAPABILITIES = (
    "machine_only",
    "current_effective_overlay_deep_verified",
    "exact_retained_base_subset_implemented",
    "old_rebound_and_invalidated_identity_exclusion_implemented",
    "per_item_recovery_terminal_evidence_deep_verified",
    "published_source_terminal_evidence_supported",
    "content_addressed_retained_base_proof_implemented",
    "singleton_retained_base_event_implemented",
    "proof_event_forward_adoption_implemented",
    "empty_retained_subset_coverage_implemented",
)
FALSE_CLAIMS = (
    "frozen_manifest_key_coverage",
    "full_workset_terminal",
    "current_effective_workset_terminal_coverage",
    "source_parent_terminal",
    "current_source_ingest_parent_terminal",
    "all_current_effective_d_or_r_terminal",
    "all_effective_items_terminal",
    "terminal_for_recovery_v6_key",
    "terminal_transition_closure_implemented",
    "transitive_terminal_closure_implemented",
    "bounded_workset_recovery_implemented",
    "all_reserved_items_settled",
    "all_reserved_successors_supported",
    "all_content_dependent_lanes_reserved",
    "all_transition_branches_supported",
    "derived_future_work_reservation_implemented",
    "network_recovery_implemented",
    "frozen_manifest_mutated",
    "manifest_namespace_mutated",
    "recovery_v6_mutated",
    "step_dependency_sidecar_mutated",
    "settlement_overlay_mutated",
    "effective_workset_overlay_mutated",
    "source_terminal_aggregate_mutated",
    "source_ingest_executed",
    "outcome_materialization_performed",
    "outcome_or_revision_consumed",
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


class SourceDerivedRetainedBaseTerminalCoverageError(RuntimeError):
    """Base retained-base terminal-coverage error."""


class SourceDerivedRetainedBaseTerminalCoverageConfigError(
    SourceDerivedRetainedBaseTerminalCoverageError
):
    """The reviewed profile or one direct upstream pin changed."""


class SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
    SourceDerivedRetainedBaseTerminalCoverageError
):
    """An upstream or persisted retained-base authority failed closed."""


class SourceDerivedRetainedBaseTerminalCoverageBusyError(
    SourceDerivedRetainedBaseTerminalCoverageError
):
    """One surviving coordinator lock is owned elsewhere."""


@dataclass(frozen=True)
class SourceDerivedRetainedBaseTerminalCoveragePaths:
    registry_root: Path
    root: Path
    proofs: Path
    events: Path
    status: Path
    overlay: overlay.SourceDerivedWorksetOverlayPaths
    sidecar: sidecar.StepDependencyPaths
    manifest_coverage: manifest_coverage.ManifestTerminalCoveragePaths


@dataclass(frozen=True)
class SourceDerivedRetainedBaseTerminalCoverageResult:
    status: str
    reason: str
    status_path: Path
    proof_path: Path | None = None
    event_path: Path | None = None
    required_key_count: int = 0
    covered_key_count: int = 0
    missing_key_ids: tuple[str, ...] = ()
    all_current_retained_base_items_terminal: bool = False
    all_effective_items_terminal: bool = False
    terminal_transition_closure_implemented: bool = False
    lifecycle_authority: bool = False


@dataclass(frozen=True)
class _RetainedPartition:
    source_parent: Mapping[str, Any]
    target_rows: tuple[Mapping[str, Any], ...]
    excluded_rebound_rows: tuple[Mapping[str, Any], ...]
    excluded_invalidated_rows: tuple[Mapping[str, Any], ...]
    derived_rows: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class _RetainedAssessment:
    partition: _RetainedPartition
    coverage_rows: tuple[Mapping[str, Any], ...]
    missing_key_ids: tuple[str, ...]
    recovery_global_intent: registry.ArtifactSnapshot | None
    source_terminal_assessment_identity: Mapping[str, Any] | None

    @property
    def complete(self) -> bool:
        return not self.missing_key_ids and len(self.coverage_rows) == len(
            self.partition.target_rows
        )


FaultHook = Callable[[str], None]


def _canonical_bytes(value: object) -> bytes:
    try:
        return registry._canonical_bytes(value)  # type: ignore[arg-type]  # noqa: SLF001
    except (TypeError, ValueError) as exc:
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
            "Value is not canonical JSON"
        ) from exc


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _claims(*, complete: bool = False) -> dict[str, bool]:
    return {
        "all_current_retained_base_items_terminal": complete,
        **{name: True for name in TRUE_CAPABILITIES},
        **{name: False for name in FALSE_CLAIMS},
    }


def _hash(value: object, *, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
            f"{name} is not a lowercase SHA-256"
        )
    return value


def _text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
            f"{name} is not canonical text"
        )
    return value


def _identity(item: Mapping[str, Any]) -> dict[str, str]:
    return {
        "key_id": _hash(item.get("key_id"), name="item key id"),
        "natural_key": _text(item.get("natural_key"), name="item natural key"),
        "namespace_digest": _hash(
            item.get("namespace_digest"), name="item namespace digest"
        ),
    }


def _identity_tuple(item: Mapping[str, Any]) -> tuple[str, str, str]:
    identity = _identity(item)
    return (
        identity["key_id"],
        identity["natural_key"],
        identity["namespace_digest"],
    )


def _reference(snapshot: registry.ArtifactSnapshot, root: Path) -> dict[str, object]:
    try:
        relative = snapshot.path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
            "Referenced artifact escaped its authority root"
        ) from exc
    return {
        "path": relative,
        "sha256": snapshot.sha256,
        "size_bytes": snapshot.size_bytes,
    }


def _replay_snapshot(
    snapshot: registry.ArtifactSnapshot, *, name: str
) -> dict[str, Any]:
    try:
        payload, checked = overlay._strict_json(snapshot.path, name=name)  # noqa: SLF001
    except overlay.SourceDerivedWorksetOverlayError as exc:
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(str(exc)) from exc
    if checked != snapshot:
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
            f"{name} differs from its durable bytes"
        )
    return payload


def _implementation_reference() -> dict[str, object]:
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            ROOT / IMPLEMENTATION_LOGICAL_PATH,
            name="retained-base terminal coverage implementation",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
    except registry.EpochRegistryError as exc:
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(str(exc)) from exc
    return {
        "logical_path": IMPLEMENTATION_LOGICAL_PATH,
        "sha256": snapshot.sha256,
        "size_bytes": snapshot.size_bytes,
    }


def load_source_derived_retained_base_terminal_coverage_profile(
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> dict[str, Any]:
    if config_path.resolve() != DEFAULT_CONFIG_PATH.resolve():
        raise SourceDerivedRetainedBaseTerminalCoverageConfigError(
            "Only the reviewed default retained-base profile is accepted"
        )
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            config_path,
            name="retained-base terminal coverage profile",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
        payload = registry._decode_json(  # noqa: SLF001
            snapshot.raw, name="retained-base terminal coverage profile"
        )
    except registry.EpochRegistryError as exc:
        raise SourceDerivedRetainedBaseTerminalCoverageConfigError(str(exc)) from exc
    if snapshot.sha256 != DEFAULT_CONFIG_SHA256:
        raise SourceDerivedRetainedBaseTerminalCoverageConfigError(
            "Default retained-base profile changed without a version/hash update"
        )
    expected_keys = {
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
    capabilities = payload.get("engineering_capabilities")
    if (
        not isinstance(payload, dict)
        or set(payload) != expected_keys
        or payload.get("schema_version")
        != "ootang_epoch_source_derived_retained_base_terminal_coverage_profile_v1"
        or payload.get("profile_id")
        != "ootang-epoch-source-derived-retained-base-terminal-coverage-v1"
        or payload.get("profile_version") != "1.0.0-exact-unchanged-frozen-subset"
        or payload.get("case") != "ootang"
        or payload.get("artifact_status")
        != (
            "retained_frozen_base_exact_subset_terminal_coverage_only_no_"
            "source_parent_dri_recovery_closure_or_lifecycle_authority"
        )
        or payload.get("formal_warning_output") is not False
        or payload.get("default_pipeline_member") is not False
        or payload.get("upstream") != EXPECTED_UPSTREAM
        or payload.get("runtime") != EXPECTED_RUNTIME
        or payload.get("protocol") != EXPECTED_PROTOCOL
        or not isinstance(capabilities, dict)
        or capabilities != _claims()
    ):
        raise SourceDerivedRetainedBaseTerminalCoverageConfigError(
            "Retained-base profile contract changed"
        )
    for pin in EXPECTED_UPSTREAM.values():
        path = ROOT / str(pin["path"])
        try:
            checked = registry._read_regular(  # noqa: SLF001
                path,
                name="retained-base pinned upstream",
                maximum_bytes=64 * 1024 * 1024,
            )
        except registry.EpochRegistryError as exc:
            raise SourceDerivedRetainedBaseTerminalCoverageConfigError(
                str(exc)
            ) from exc
        if checked.sha256 != pin["expected_sha256"]:
            raise SourceDerivedRetainedBaseTerminalCoverageConfigError(
                f"Pinned upstream changed: {pin['path']}"
            )
    overlay.load_source_derived_workset_overlay_profile()
    sidecar.load_step_dependency_profile()
    manifest_coverage.load_manifest_coverage_profile()
    recovery.load_workset_recovery_profile()
    drain.load_drain_profile()
    return {**payload, "_profile_sha256": snapshot.sha256}


def source_derived_retained_base_terminal_coverage_paths(
    profile: Mapping[str, Any],
    *,
    registry_root: Path | None = None,
    active_root: Path | None = None,
    shadow_root: Path | None = None,
) -> SourceDerivedRetainedBaseTerminalCoveragePaths:
    registry_path = (
        registry_root or ROOT / str(profile["runtime"]["registry_root"])
    ).resolve()
    active = (active_root or ROOT / str(profile["runtime"]["active_root"])).resolve()
    shadow = (shadow_root or ROOT / str(profile["runtime"]["shadow_root"])).resolve()
    overlay_paths = overlay.source_derived_workset_overlay_paths(
        overlay.load_source_derived_workset_overlay_profile(),
        registry_root=registry_path,
        active_root=active,
        shadow_root=shadow,
    )
    sidecar_paths = sidecar.step_dependency_paths(
        sidecar.load_step_dependency_profile(),
        registry_root=registry_path,
        active_root=active,
        shadow_root=shadow,
    )
    manifest_paths = manifest_coverage.manifest_coverage_paths(
        manifest_coverage.load_manifest_coverage_profile(),
        registry_root=registry_path,
        active_root=active,
        shadow_root=shadow,
    )
    recovery_root = overlay_paths.recovery.root
    if (
        sidecar_paths.recovery.root != recovery_root
        or manifest_paths.aggregate.overlay.recovery.root != recovery_root
        or recovery_root.name != profile["runtime"]["recovery_namespace"]
    ):
        raise SourceDerivedRetainedBaseTerminalCoverageConfigError(
            "Retained-base upstream recovery roots diverged"
        )
    root = recovery_root / str(profile["runtime"]["namespace"])
    return SourceDerivedRetainedBaseTerminalCoveragePaths(
        registry_root=registry_path,
        root=root,
        proofs=root / str(profile["runtime"]["proofs"]),
        events=root / str(profile["runtime"]["events"]),
        status=root / str(profile["runtime"]["status"]),
        overlay=overlay_paths,
        sidecar=sidecar_paths,
        manifest_coverage=manifest_paths,
    )


def _retained_partition(computation: Any) -> _RetainedPartition:
    try:
        base_items, _, _ = recovery._dag(computation.reservation)  # noqa: SLF001
    except recovery.WorksetRecoveryError as exc:
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(str(exc)) from exc
    base = tuple(dict(item) for item in base_items)
    if not 0 < len(base) <= EXPECTED_PROTOCOL["maximum_items"]:
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
            "Frozen base count is outside the reviewed bound"
        )
    base_by_natural = {_identity(item)["natural_key"]: item for item in base}
    if len(base_by_natural) != len(base):
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
            "Frozen base natural-key identity is not unique"
        )
    effective = tuple(dict(item) for item in computation.effective_items)
    effective_identity_set = {_identity_tuple(item) for item in effective}
    if len(effective_identity_set) != len(effective):
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
            "Current effective identity set is not unique"
        )
    parent = dict(computation.cross_authority.source_item)
    parent_identity = _identity_tuple(parent)
    parent_candidates = [
        item
        for item in base
        if item.get("family") == "outcome_revision"
        and item.get("canonical_successor_state") == "source_snapshot_ingested"
    ]
    if (
        len(parent_candidates) != 1
        or parent_candidates[0] != parent
        or parent.get("family") != "outcome_revision"
        or parent.get("canonical_successor_state") != "source_snapshot_ingested"
        or parent_identity not in {_identity_tuple(item) for item in base}
        or parent_identity not in effective_identity_set
    ):
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
            "Current source parent is not one exact retained frozen identity"
        )
    derived_payload = computation.derived_evidence.reservation_payload
    fields = (
        "derived_outcome_items",
        "rebound_existing_items",
        "invalidated_existing_items",
    )
    if any(not isinstance(derived_payload.get(field), list) for field in fields):
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
            "Current D/R/I row sets changed type"
        )
    manifest_sha256 = computation.reservation.manifest_snapshot.sha256
    try:
        additions = tuple(
            overlay._normalize_item(  # noqa: SLF001
                row, manifest_sha256, name=f"retained derived addition[{index}]"
            )
            for index, row in enumerate(derived_payload[fields[0]])
        )
        rebounds = tuple(
            overlay._normalize_item(  # noqa: SLF001
                row, manifest_sha256, name=f"retained rebound replacement[{index}]"
            )
            for index, row in enumerate(derived_payload[fields[1]])
        )
        invalidations = tuple(
            overlay._normalize_item(  # noqa: SLF001
                row, manifest_sha256, name=f"retained invalidation[{index}]"
            )
            for index, row in enumerate(derived_payload[fields[2]])
        )
    except overlay.SourceDerivedWorksetOverlayError as exc:
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(str(exc)) from exc
    d_by_natural = {_identity(row)["natural_key"]: row for row in additions}
    r_by_natural = {_identity(row)["natural_key"]: row for row in rebounds}
    i_by_natural = {_identity(row)["natural_key"]: row for row in invalidations}
    if (
        len(d_by_natural) != len(additions)
        or len(r_by_natural) != len(rebounds)
        or len(i_by_natural) != len(invalidations)
        or set(d_by_natural) & set(r_by_natural)
        or set(d_by_natural) & set(i_by_natural)
        or set(r_by_natural) & set(i_by_natural)
        or set(d_by_natural) & set(base_by_natural)
        or not set(r_by_natural) <= set(base_by_natural)
        or not set(i_by_natural) <= set(base_by_natural)
        or parent["natural_key"]
        in (set(d_by_natural) | set(r_by_natural) | set(i_by_natural))
    ):
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
            "Current D/R/I partition no longer matches the frozen base"
        )

    targets: list[dict[str, Any]] = []
    excluded_r: list[dict[str, Any]] = []
    excluded_i: list[dict[str, Any]] = []
    for position, item in enumerate(base):
        identity = _identity(item)
        natural = identity["natural_key"]
        triple = _identity_tuple(item)
        if triple == parent_identity:
            continue
        if natural in r_by_natural:
            replacement = r_by_natural[natural]
            if (
                triple in effective_identity_set
                or _identity_tuple(replacement) not in effective_identity_set
            ):
                raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
                    "Old rebound identity survived or replacement disappeared"
                )
            excluded_r.append(
                {
                    "base_position": position,
                    "old_identity": identity,
                    "replacement_identity": _identity(replacement),
                }
            )
            continue
        if natural in i_by_natural:
            if dict(i_by_natural[natural]) != item or triple in effective_identity_set:
                raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
                    "Invalidated frozen identity was not exactly removed"
                )
            excluded_i.append(
                {"base_position": position, "invalidated_identity": identity}
            )
            continue
        if triple not in effective_identity_set:
            raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
                "Unclassified frozen identity disappeared from the current overlay"
            )
        plan = recovery._transition_plan(item)  # noqa: SLF001
        targets.append(
            {
                "base_position": position,
                **identity,
                "family": _text(item.get("family"), name="base family"),
                "transition_plan_sha256": _hash(
                    plan.get("plan_sha256"), name="base transition plan"
                ),
            }
        )

    expected_effective = {
        parent_identity,
        *(_identity_tuple(row) for row in targets),
        *(_identity_tuple(row) for row in additions),
        *(_identity_tuple(row) for row in rebounds),
    }
    application = computation.application
    if (
        expected_effective != effective_identity_set
        or application.get("base_item_count") != len(base)
        or application.get("retained_base_item_count") != len(targets) + 1
        or application.get("derived_added_count") != len(additions)
        or application.get("rebound_replaced_count") != len(excluded_r)
        or application.get("invalidated_superseded_count") != len(excluded_i)
    ):
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
            "Retained-base partition counts or effective identity set changed"
        )
    return _RetainedPartition(
        source_parent={**_identity(parent), "family": parent["family"]},
        target_rows=tuple(targets),
        excluded_rebound_rows=tuple(excluded_r),
        excluded_invalidated_rows=tuple(excluded_i),
        derived_rows=tuple(_identity(row) for row in additions),
    )


def _validate_recovery_authority(
    computation: Any,
    authority: sidecar.StepDependencyAuthority,
) -> None:
    reservation = computation.reservation
    if (
        authority.reservation.manifest_snapshot != reservation.manifest_snapshot
        or authority.reservation.event_snapshot != reservation.event_snapshot
        or authority.reservation.manifest != reservation.manifest
        or _replay_snapshot(
            authority.reservation.manifest_snapshot, name="retained frozen manifest"
        )
        != reservation.manifest
        or _replay_snapshot(
            authority.reservation.event_snapshot,
            name="retained manifest reservation event",
        )
        != reservation.event
        or _replay_snapshot(
            authority.global_snapshot, name="retained recovery global intent"
        )
        != authority.global_intent
    ):
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
            "Retained recovery authority diverged from the current overlay base"
        )
    ordered, _, _ = recovery._dag(reservation)  # noqa: SLF001
    if tuple(authority.ordered_items) != tuple(ordered):
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
            "Retained recovery ordered items differ from durable manifest replay"
        )


def _recovery_terminal_row(
    authority: sidecar.StepDependencyAuthority,
    target: Mapping[str, Any],
    recovery_root: Path,
) -> dict[str, Any] | None:
    key_id = str(target["key_id"])
    item = next(
        (row for row in authority.ordered_items if row.get("key_id") == key_id),
        None,
    )
    if item is None or _identity_tuple(item) != _identity_tuple(target):
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
            "Retained target is absent from the recovery authority"
        )
    rows = tuple(authority.chains.get(key_id, ()))
    if not rows or rows[-1][0].get("terminal_for_key") is not True:
        return None
    receipt, receipt_snapshot = rows[-1]
    if _replay_snapshot(receipt_snapshot, name="retained terminal receipt") != receipt:
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
            "Retained terminal receipt changed"
        )
    plan = recovery._transition_plan(item)  # noqa: SLF001
    edges = recovery._plan_edges(plan)  # noqa: SLF001
    action = receipt.get("action")
    step_index = receipt.get("step_index")
    step_id = receipt.get("step_id")
    if (
        plan.get("closure_resolved") is not True
        or action not in plan.get("terminal_actions", [])
        or not isinstance(action, str)
        or not isinstance(step_index, int)
        or isinstance(step_index, bool)
        or step_index != len(rows) - 1
        or step_id != recovery._step_id(key_id, step_index, action)  # noqa: SLF001
        or receipt.get("key_id") != target["key_id"]
        or receipt.get("natural_key") != target["natural_key"]
        or receipt.get("namespace_digest") != target["namespace_digest"]
        or receipt.get("transition_plan_sha256") != target["transition_plan_sha256"]
        or receipt.get("next_actions") != edges.get(action)
    ):
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
            "Retained terminal receipt no longer matches its exact plan tip"
        )
    event_row = authority.events_by_step.get(str(step_id))
    if event_row is None:
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
            "Retained terminal receipt lacks its published recovery event"
        )
    event, event_snapshot = event_row
    if (
        _replay_snapshot(event_snapshot, name="retained terminal event") != event
        or event.get("key_id") != key_id
        or event.get("step_id") != step_id
        or event.get("receipt") != _reference(receipt_snapshot, recovery_root)
    ):
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
            "Retained recovery event no longer binds the terminal receipt"
        )
    body = {
        **dict(target),
        "evidence_kind": "recovery_v6_terminal_receipt_event",
        "terminal_action": action,
        "terminal_step_index": step_index,
        "terminal_step_id": step_id,
        "terminal_receipt": _reference(receipt_snapshot, recovery_root),
        "terminal_event": _reference(event_snapshot, recovery_root),
    }
    return {**body, "coverage_row_sha256": _sha256(_canonical_bytes(body))}


def _source_terminal_rows(
    paths: SourceDerivedRetainedBaseTerminalCoveragePaths,
    computation: Any,
    targets: Mapping[tuple[str, str, str], Mapping[str, Any]],
    now: datetime,
) -> tuple[dict[tuple[str, str, str], dict[str, Any]], Mapping[str, Any] | None]:
    try:
        authority = manifest_coverage._load_coverage_authority(  # noqa: SLF001
            paths.manifest_coverage, now
        )
    except manifest_coverage.ManifestTerminalCoverageError as exc:
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(str(exc)) from exc
    if authority is None:
        if manifest_coverage.aggregate._authority_children_present(  # noqa: SLF001
            paths.manifest_coverage.aggregate
        ):
            raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
                "Source-terminal aggregate bytes lack their complete upstream authority"
            )
        return {}, None
    try:
        assessment = manifest_coverage._coverage_assessment(  # noqa: SLF001
            manifest_coverage.load_manifest_coverage_profile(),
            paths.manifest_coverage,
            authority,
        )
    except manifest_coverage.ManifestTerminalCoverageError as exc:
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(str(exc)) from exc
    reservation = computation.reservation
    expected_manifest = _reference(
        reservation.manifest_snapshot, reservation.paths.root
    )
    if assessment.global_identity.get(
        "manifest"
    ) != expected_manifest or assessment.global_identity.get(
        "workset_keyset_sha256"
    ) != reservation.manifest.get("workset_keyset_sha256"):
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
            "Source-terminal assessment belongs to another frozen manifest"
        )
    result: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in assessment.rows:
        identity = _identity_tuple(row)
        if row.get("evidence_kind") != "source_terminal_aggregate_event":
            continue
        if identity not in targets:
            continue
        body = {
            **dict(targets[identity]),
            "evidence_kind": "source_terminal_aggregate_proof_event",
            "source_terminal_proof": row["source_terminal_proof"],
            "source_terminal_event": row["source_terminal_event"],
            "terminal_action": row["terminal_action"],
            "terminal_step_id": row["terminal_step_id"],
            "terminal_step_index": row["terminal_step_index"],
        }
        if identity in result:
            raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
                "Duplicate source-terminal evidence covers one retained identity"
            )
        result[identity] = {
            **body,
            "coverage_row_sha256": _sha256(_canonical_bytes(body)),
        }
    selected = [
        {
            **_identity(row),
            "source_terminal_proof": row["source_terminal_proof"],
            "source_terminal_event": row["source_terminal_event"],
        }
        for _, row in sorted(result.items())
    ]
    identity = (
        {
            "manifest_key_ids_sha256": assessment.global_identity[
                "manifest_key_ids_sha256"
            ],
            "selected_source_terminal_identity_count": len(selected),
            "selected_source_terminal_identity_rows_sha256": _sha256(
                _canonical_bytes(selected)
            ),
        }
        if selected
        else None
    )
    return result, identity


def _retained_assessment(
    paths: SourceDerivedRetainedBaseTerminalCoveragePaths,
    computation: Any,
    partition: _RetainedPartition,
    now: datetime,
) -> _RetainedAssessment:
    targets = {_identity_tuple(row): row for row in partition.target_rows}
    if len(targets) != len(partition.target_rows):
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
            "Retained denominator contains duplicate identities"
        )
    if not targets:
        return _RetainedAssessment(partition, (), (), None, None)
    try:
        authority = sidecar._load_recovery_authority(paths.sidecar, now)  # noqa: SLF001
    except sidecar.StepDependencyError as exc:
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(str(exc)) from exc
    if authority is None:
        return _RetainedAssessment(
            partition,
            (),
            tuple(str(row["key_id"]) for row in partition.target_rows),
            None,
            None,
        )
    _validate_recovery_authority(computation, authority)
    recovery_rows: dict[tuple[str, str, str], dict[str, Any]] = {}
    for identity, target in targets.items():
        row = _recovery_terminal_row(authority, target, paths.sidecar.recovery.root)
        if row is not None:
            recovery_rows[identity] = row
    aggregate_rows, aggregate_identity = _source_terminal_rows(
        paths, computation, targets, now
    )
    overlap = set(recovery_rows) & set(aggregate_rows)
    if overlap:
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
            "One retained identity has two terminal evidence provenances"
        )
    evidence = {**recovery_rows, **aggregate_rows}
    ordered = tuple(
        evidence[_identity_tuple(target)]
        for target in partition.target_rows
        if _identity_tuple(target) in evidence
    )
    missing = tuple(
        str(target["key_id"])
        for target in partition.target_rows
        if _identity_tuple(target) not in evidence
    )
    return _RetainedAssessment(
        partition,
        ordered,
        missing,
        authority.global_snapshot,
        aggregate_identity,
    )


def _proof_payload(
    profile: Mapping[str, Any],
    paths: SourceDerivedRetainedBaseTerminalCoveragePaths,
    computation: Any,
    overlay_object: registry.ArtifactSnapshot,
    overlay_event: registry.ArtifactSnapshot,
    assessment: _RetainedAssessment,
) -> dict[str, object]:
    if not assessment.complete:
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
            "Incomplete retained-base evidence cannot publish a proof"
        )
    partition = assessment.partition
    targets = [dict(row) for row in partition.target_rows]
    coverage_rows = [dict(row) for row in assessment.coverage_rows]
    excluded_rebound = [dict(row) for row in partition.excluded_rebound_rows]
    excluded_invalidated = [dict(row) for row in partition.excluded_invalidated_rows]
    derived_rows = [dict(row) for row in partition.derived_rows]
    reservation = computation.reservation
    evidence = computation.derived_evidence
    return {
        "schema_version": profile["protocol"]["proof_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "authority_scope": (
            "current_retained_frozen_base_exact_subset_only_not_source_parent_"
            "dri_or_whole_effective_terminality"
        ),
        "manifest": _reference(reservation.manifest_snapshot, reservation.paths.root),
        "manifest_reservation_event": _reference(
            reservation.event_snapshot, reservation.paths.root
        ),
        "overlay_object": _reference(overlay_object, paths.overlay.root),
        "overlay_event": _reference(overlay_event, paths.overlay.root),
        "derived_reservation": _reference(
            evidence.reservation_snapshot, paths.overlay.derived.root
        ),
        "derived_reservation_event": _reference(
            evidence.event_snapshot, paths.overlay.derived.root
        ),
        "effective_item_identity_set_sha256": computation.effective_workset[
            "item_identity_set_sha256"
        ],
        "effective_dependency_graph_sha256": computation.effective_workset[
            "dependency_graph_sha256"
        ],
        "source_parent_excluded_identity": dict(partition.source_parent),
        "retained_target_rows": targets,
        "retained_target_rows_sha256": _sha256(_canonical_bytes(targets)),
        "required_key_count": len(targets),
        "terminal_coverage_rows": coverage_rows,
        "terminal_coverage_rows_sha256": _sha256(_canonical_bytes(coverage_rows)),
        "covered_key_count": len(coverage_rows),
        "missing_key_ids": [],
        "excluded_rebound_old_rows": excluded_rebound,
        "excluded_rebound_old_rows_sha256": _sha256(_canonical_bytes(excluded_rebound)),
        "excluded_invalidated_rows": excluded_invalidated,
        "excluded_invalidated_rows_sha256": _sha256(
            _canonical_bytes(excluded_invalidated)
        ),
        "derived_addition_rows": derived_rows,
        "derived_addition_rows_sha256": _sha256(_canonical_bytes(derived_rows)),
        "partition_counts": {
            "base_item_count": computation.application["base_item_count"],
            "source_parent_excluded_count": 1,
            "retained_target_count": len(targets),
            "rebound_old_excluded_count": len(excluded_rebound),
            "invalidated_excluded_count": len(excluded_invalidated),
            "derived_addition_excluded_count": len(derived_rows),
            "current_rebound_replacement_excluded_count": len(excluded_rebound),
            "effective_item_count": computation.application["effective_item_count"],
        },
        "recovery_global_intent": (
            _reference(
                assessment.recovery_global_intent,
                paths.overlay.recovery.root,
            )
            if assessment.recovery_global_intent is not None
            else None
        ),
        "source_terminal_assessment_identity": (
            dict(assessment.source_terminal_assessment_identity)
            if assessment.source_terminal_assessment_identity is not None
            else None
        ),
        "exact_retained_identity_bijection": True,
        "empty_subset_vacuously_covered": not targets,
        "implementation": _implementation_reference(),
        **_claims(complete=True),
    }


def _strict_entries(directory: Path, *, name: str) -> tuple[Path, ...]:
    try:
        return overlay._strict_entries(directory, name=name)  # noqa: SLF001
    except overlay.SourceDerivedWorksetOverlayError as exc:
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(str(exc)) from exc


def _strict_json(
    path: Path, *, name: str
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    try:
        return overlay._strict_json(path, name=name)  # noqa: SLF001
    except overlay.SourceDerivedWorksetOverlayError as exc:
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(str(exc)) from exc


def _publish(
    path: Path, payload: Mapping[str, Any], *, root: Path, name: str
) -> registry.ArtifactSnapshot:
    try:
        return drain._publish_once_durable(  # noqa: SLF001
            path, _canonical_bytes(dict(payload)), root=root, name=name
        )
    except drain.EpochDrainError as exc:
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(str(exc)) from exc


def _load_state(
    profile: Mapping[str, Any],
    paths: SourceDerivedRetainedBaseTerminalCoveragePaths,
    expected: Mapping[str, Any],
) -> tuple[
    tuple[dict[str, Any], registry.ArtifactSnapshot] | None,
    tuple[dict[str, Any], registry.ArtifactSnapshot] | None,
]:
    proofs = _strict_entries(paths.proofs, name="retained-base proofs")
    events = _strict_entries(paths.events, name="retained-base events")
    if len(proofs) > 1 or len(events) > 1:
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
            "Retained-base singleton authority branched"
        )
    proof = None
    if proofs:
        payload, snapshot = _strict_json(proofs[0], name="retained-base proof")
        match = PROOF_NAME.fullmatch(proofs[0].name)
        if (
            match is None
            or match.group("digest") != snapshot.sha256
            or payload != expected
        ):
            raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
                "Retained-base proof differs from current authority"
            )
        proof = (payload, snapshot)
    event = None
    if events:
        if proof is None:
            raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
                "Retained-base event is orphaned"
            )
        payload, snapshot = _strict_json(events[0], name="retained-base event")
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
            "retained_target_rows_sha256": expected["retained_target_rows_sha256"],
            "required_key_count": expected["required_key_count"],
            "covered_key_count": expected["covered_key_count"],
            **_claims(complete=True),
        }
        match = EVENT_NAME.fullmatch(events[0].name)
        if (
            match is None
            or match.group("digest") != entry
            or body != expected_body
            or entry != _sha256(_canonical_bytes({**body, "recorded_at_utc": recorded}))
        ):
            raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
                "Retained-base event changed"
            )
        try:
            registry._utc_timestamp(recorded, name="retained-base event time")  # noqa: SLF001
        except registry.EpochRegistryError as exc:
            raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
                str(exc)
            ) from exc
        event = (payload, snapshot)
    return proof, event


def _append_event(
    profile: Mapping[str, Any],
    paths: SourceDerivedRetainedBaseTerminalCoveragePaths,
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
        "recorded_at_utc": now.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "proof": _reference(proof, paths.root),
        "retained_target_rows_sha256": expected["retained_target_rows_sha256"],
        "required_key_count": expected["required_key_count"],
        "covered_key_count": expected["covered_key_count"],
        **_claims(complete=True),
    }
    payload = {**body, "entry_sha256": _sha256(_canonical_bytes(body))}
    return _publish(
        paths.events / f"{1:020d}-{payload['entry_sha256']}.json",
        payload,
        root=paths.root,
        name="retained-base event",
    )


def _write_status(
    profile: Mapping[str, Any],
    paths: SourceDerivedRetainedBaseTerminalCoveragePaths,
    *,
    now: datetime,
    status: str,
    reason: str,
    assessment: _RetainedAssessment | None,
    proof: Path | None,
    event: Path | None,
) -> None:
    required = len(assessment.partition.target_rows) if assessment else 0
    covered = len(assessment.coverage_rows) if assessment else 0
    payload = {
        "schema_version": profile["protocol"]["status_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "observed_at_utc": now.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "status": status,
        "reason": reason,
        "required_key_count": required,
        "covered_key_count": covered,
        "missing_key_ids": list(assessment.missing_key_ids) if assessment else [],
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
            name="retained-base status",
        )
    except registry.EpochRegistryError as exc:
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(str(exc)) from exc


def _result(
    paths: SourceDerivedRetainedBaseTerminalCoveragePaths,
    status: str,
    reason: str,
    assessment: _RetainedAssessment | None,
    *,
    proof: Path | None = None,
    event: Path | None = None,
) -> SourceDerivedRetainedBaseTerminalCoverageResult:
    return SourceDerivedRetainedBaseTerminalCoverageResult(
        status=status,
        reason=reason,
        status_path=paths.status,
        proof_path=proof,
        event_path=event,
        required_key_count=(len(assessment.partition.target_rows) if assessment else 0),
        covered_key_count=len(assessment.coverage_rows) if assessment else 0,
        missing_key_ids=assessment.missing_key_ids if assessment else (),
        all_current_retained_base_items_terminal=event is not None,
    )


def _own_children_present(
    paths: SourceDerivedRetainedBaseTerminalCoveragePaths,
) -> bool:
    return bool(
        _strict_entries(paths.proofs, name="retained-base proofs")
        or _strict_entries(paths.events, name="retained-base events")
    )


def _coordinate_source_derived_retained_base_terminal_coverage(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    registry_root: Path | None = None,
    active_root: Path | None = None,
    shadow_root: Path | None = None,
    clock: Callable[[], datetime] | None = None,
    fault_hook: FaultHook | None = None,
    load_derived_authority: Any = None,
) -> SourceDerivedRetainedBaseTerminalCoverageResult:
    profile = load_source_derived_retained_base_terminal_coverage_profile(config_path)
    paths = source_derived_retained_base_terminal_coverage_paths(
        profile,
        registry_root=registry_root,
        active_root=active_root,
        shadow_root=shadow_root,
    )
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    if now.tzinfo is None or now.utcoffset() is None:
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
            "Retained-base clock must be timezone-aware"
        )
    handles: list[BinaryIO] = []
    assessment: _RetainedAssessment | None = None
    try:
        handles = overlay._acquire_locks(paths.overlay)  # noqa: SLF001
        computation, waiting = overlay._load_computation(  # noqa: SLF001
            overlay.load_source_derived_workset_overlay_profile(),
            paths.overlay,
            now,
            load_derived_authority=load_derived_authority,
        )
        if computation is None:
            if _own_children_present(paths):
                raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
                    "Retained-base authority outlived its current overlay"
                )
            reason = waiting or "current effective overlay is unavailable"
            _write_status(
                profile,
                paths,
                now=now,
                status="waiting_for_current_effective_overlay",
                reason=reason,
                assessment=None,
                proof=None,
                event=None,
            )
            return _result(paths, "waiting_for_current_effective_overlay", reason, None)
        overlay_object, overlay_event = overlay._load_overlay_state(  # noqa: SLF001
            overlay.load_source_derived_workset_overlay_profile(),
            paths.overlay,
            computation,
        )
        if overlay_object is None or overlay_event is None:
            if _own_children_present(paths):
                raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
                    "Retained-base authority exists before overlay publication"
                )
            reason = "matching current effective overlay object/event is pending"
            _write_status(
                profile,
                paths,
                now=now,
                status="waiting_for_published_effective_overlay",
                reason=reason,
                assessment=None,
                proof=None,
                event=None,
            )
            return _result(
                paths, "waiting_for_published_effective_overlay", reason, None
            )
        partition = _retained_partition(computation)
        assessment = _retained_assessment(paths, computation, partition, now)
        if not assessment.complete:
            if _own_children_present(paths):
                raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
                    "Published retained-base authority lost exact per-item evidence"
                )
            reason = "one or more exact retained frozen identities lack terminal events"
            _write_status(
                profile,
                paths,
                now=now,
                status="waiting_for_retained_base_terminal_evidence",
                reason=reason,
                assessment=assessment,
                proof=None,
                event=None,
            )
            return _result(
                paths,
                "waiting_for_retained_base_terminal_evidence",
                reason,
                assessment,
            )
        expected = _proof_payload(
            profile,
            paths,
            computation,
            overlay_object[1],
            overlay_event[1],
            assessment,
        )
        proof, event = _load_state(profile, paths, expected)
        if event is not None:
            reason = "exact retained-base terminal coverage event is current"
            _write_status(
                profile,
                paths,
                now=now,
                status="retained_base_terminal_coverage_current",
                reason=reason,
                assessment=assessment,
                proof=proof[1].path if proof else None,
                event=event[1].path,
            )
            return _result(
                paths,
                "retained_base_terminal_coverage_current",
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
                name="retained-base proof",
            )
            if fault_hook is not None:
                fault_hook("after_proof")
        else:
            proof_snapshot = proof[1]
        event_snapshot = _append_event(profile, paths, proof_snapshot, expected, now)
        reason = "exact retained-base proof was published or forward-adopted"
        _write_status(
            profile,
            paths,
            now=now,
            status="retained_base_terminal_coverage_published",
            reason=reason,
            assessment=assessment,
            proof=proof_snapshot.path,
            event=event_snapshot.path,
        )
        return _result(
            paths,
            "retained_base_terminal_coverage_published",
            reason,
            assessment,
            proof=proof_snapshot.path,
            event=event_snapshot.path,
        )
    except overlay.SourceDerivedWorksetOverlayBusyError as exc:
        raise SourceDerivedRetainedBaseTerminalCoverageBusyError(str(exc)) from exc
    except overlay.SourceDerivedWorksetOverlayError as exc:
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(str(exc)) from exc
    except SourceDerivedRetainedBaseTerminalCoverageError:
        raise
    except Exception as exc:
        raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
            f"Retained-base coordination failed:{type(exc).__name__}:{exc}"
        ) from exc
    finally:
        if handles:
            try:
                overlay._release_locks(handles)  # noqa: SLF001
            except overlay.SourceDerivedWorksetOverlayError as exc:
                raise SourceDerivedRetainedBaseTerminalCoverageIntegrityError(
                    str(exc)
                ) from exc


def coordinate_source_derived_retained_base_terminal_coverage(
    **kwargs: Any,
) -> SourceDerivedRetainedBaseTerminalCoverageResult:
    """Run one machine-only retained-base terminal-coverage poll."""

    return _coordinate_source_derived_retained_base_terminal_coverage(**kwargs)
