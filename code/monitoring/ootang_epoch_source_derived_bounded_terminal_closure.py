"""Prove bounded terminal-or-supersession closure for one source edge.

The authority joins a frozen workset inventory, its exact current-effective
terminal coverage, and the complete source-derived D/R/I reservation under one
immutable source-snapshot edge.  It is not generic recovery closure, drain
eligibility, or lifecycle authority.
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
from monitoring import ootang_epoch_source_ingest_derived_reservation as derived
from monitoring import (
    ootang_epoch_source_derived_current_effective_workset_terminal_coverage as current,
)
from monitoring import ootang_epoch_workset_recovery as recovery


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = (
    ROOT
    / "config"
    / "ootang_epoch_source_derived_bounded_terminal_closure.v1.json"
)
DEFAULT_CONFIG_SHA256 = (
    "6f149b0d4aea0906de3e9df27a6159d16bafc57d5fa531602849ae6126a52261"
)
IMPLEMENTATION_LOGICAL_PATH = (
    "code/monitoring/ootang_epoch_source_derived_bounded_terminal_closure.py"
)
MAX_CONTROL_BYTES = 4 * 1024 * 1024
ZERO_HASH = "0" * 64
PROOF_NAME = re.compile(r"^(?P<digest>[0-9a-f]{64})\.json$")
EVENT_NAME = re.compile(r"^00000000000000000001-(?P<digest>[0-9a-f]{64})\.json$")

EXPECTED_UPSTREAM = {
    "current_effective_coverage_profile": {
        "path": "config/ootang_epoch_source_derived_current_effective_workset_terminal_coverage.v1.json",
        "expected_sha256": current.DEFAULT_CONFIG_SHA256,
    },
    "current_effective_coverage_implementation": {
        "path": "code/monitoring/ootang_epoch_source_derived_current_effective_workset_terminal_coverage.py",
        "expected_sha256": "80f84db4e88dc5fdcc8e768eda2ea11f93e89c89c12726522ed9efb015361ac3",
    },
    "derived_reservation_profile": {
        "path": "config/ootang_epoch_source_ingest_derived_reservation.v1.json",
        "expected_sha256": derived.DEFAULT_CONFIG_SHA256,
    },
    "derived_reservation_implementation": {
        "path": "code/monitoring/ootang_epoch_source_ingest_derived_reservation.py",
        "expected_sha256": "1d620fbe20d936a27f55d1e03204d87be3ff8d94a892b2f9c966c061e2fa59d2",
    },
    "overlay_profile": {
        "path": "config/ootang_epoch_source_derived_workset_overlay.v1.json",
        "expected_sha256": current.overlay.DEFAULT_CONFIG_SHA256,
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
    "namespace": "source_derived_bounded_terminal_closure_v1",
    "proofs": "proofs",
    "events": "events",
    "status": "status.json",
    "active_root": "runtime/ootang_prequential_live_v1",
    "shadow_root": "runtime/ootang_prequential_calibration_shadow_v1",
}
EXPECTED_PROTOCOL = {
    "proof_schema_version": (
        "ootang_epoch_source_derived_bounded_terminal_closure_proof_v1"
    ),
    "event_schema_version": (
        "ootang_epoch_source_derived_bounded_terminal_closure_event_v1"
    ),
    "status_schema_version": (
        "ootang_epoch_source_derived_bounded_terminal_closure_status_v1"
    ),
    "event_type": "epoch_source_derived_bounded_terminal_closure_proved",
    "canonical_json": "utf8_sort_keys_compact_no_nan_trailing_lf",
    "initial_previous_entry_sha256": ZERO_HASH,
    "maximum_items": 4096,
    "maximum_control_bytes": MAX_CONTROL_BYTES,
    "publication_policy": (
        "deterministic_content_addressed_proof_then_singleton_event"
    ),
    "closure_policy": (
        "exact_frozen_base_equals_parent_retained_rebound_old_invalidated_and_"
        "current_effective_equals_parent_retained_derived_rebound_new"
    ),
}
TRUE_CAPABILITIES = (
    "machine_only",
    "immutable_source_edge_deep_verified",
    "frozen_base_inventory_bijection_implemented",
    "current_effective_terminal_coverage_deep_verified",
    "complete_dri_successor_inventory_deep_verified",
    "exact_base_terminal_or_supersession_resolution_implemented",
    "exact_successor_terminal_or_supersession_resolution_implemented",
    "content_addressed_bounded_closure_proof_implemented",
    "singleton_bounded_closure_event_implemented",
    "proof_event_forward_adoption_implemented",
)
FALSE_CLAIMS = (
    "bounded_workset_recovery_implemented",
    "all_reserved_items_settled",
    "all_reserved_successors_supported",
    "all_content_dependent_lanes_reserved",
    "terminal_transition_closure_implemented",
    "transitive_terminal_closure_implemented",
    "full_workset_terminal",
    "all_effective_items_terminal",
    "current_effective_workset_terminal_coverage",
    "source_parent_terminal",
    "terminal_for_recovery_v6_key",
    "recovery_v6_mutated",
    "derived_reservation_mutated",
    "effective_workset_overlay_mutated",
    "current_effective_coverage_mutated",
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


class SourceDerivedBoundedTerminalClosureError(RuntimeError):
    """Base bounded source-derived closure error."""


class SourceDerivedBoundedTerminalClosureConfigError(
    SourceDerivedBoundedTerminalClosureError
):
    """The reviewed profile or a direct upstream pin changed."""


class SourceDerivedBoundedTerminalClosureIntegrityError(
    SourceDerivedBoundedTerminalClosureError
):
    """An immutable cut, inventory equation, or durable record failed closed."""


class SourceDerivedBoundedTerminalClosureBusyError(
    SourceDerivedBoundedTerminalClosureError
):
    """A shared machine coordinator lock is busy."""


@dataclass(frozen=True)
class SourceDerivedBoundedTerminalClosurePaths:
    registry_root: Path
    root: Path
    proofs: Path
    events: Path
    status: Path
    current: current.SourceDerivedCurrentEffectiveWorksetTerminalCoveragePaths


@dataclass(frozen=True)
class SourceDerivedBoundedTerminalClosureResult:
    status: str
    reason: str
    status_path: Path
    proof_path: Path | None = None
    event_path: Path | None = None
    frozen_base_item_count: int = 0
    current_effective_item_count: int = 0
    derived_item_count: int = 0
    rebound_item_count: int = 0
    invalidated_item_count: int = 0
    current_source_derived_bounded_terminal_closure: bool = False
    bounded_workset_recovery_implemented: bool = False
    old_epoch_drained: bool = False
    lifecycle_authority: bool = False


@dataclass(frozen=True)
class _Inputs:
    current_inputs: current._Inputs  # noqa: SLF001
    current_assessment: current._Assessment  # noqa: SLF001
    current_payload: Mapping[str, Any]
    current_proof: registry.ArtifactSnapshot
    current_event: registry.ArtifactSnapshot


@dataclass(frozen=True)
class _Assessment:
    base_resolution_rows: tuple[dict[str, Any], ...]
    successor_resolution_rows: tuple[dict[str, Any], ...]
    frozen_base_item_count: int
    current_effective_item_count: int
    derived_item_count: int
    rebound_item_count: int
    invalidated_item_count: int


FaultHook = Callable[[str], None]


def _canonical_bytes(value: object) -> bytes:
    try:
        return registry._canonical_bytes(value)  # type: ignore[arg-type]  # noqa: SLF001
    except (TypeError, ValueError) as exc:
        raise SourceDerivedBoundedTerminalClosureIntegrityError(
            "Value is not canonical JSON"
        ) from exc


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _claims(*, complete: bool = False) -> dict[str, bool]:
    return {
        **{name: True for name in TRUE_CAPABILITIES},
        "current_source_derived_bounded_terminal_closure": complete,
        **{name: False for name in FALSE_CLAIMS},
    }


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SourceDerivedBoundedTerminalClosureIntegrityError(
            "Closure clock must be timezone-aware"
        )
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _child(root: Path, relative: str, *, name: str) -> Path:
    try:
        return registry._contained(root, relative, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise SourceDerivedBoundedTerminalClosureConfigError(str(exc)) from exc


def _reference(snapshot: registry.ArtifactSnapshot, root: Path) -> dict[str, object]:
    try:
        relative = snapshot.path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise SourceDerivedBoundedTerminalClosureIntegrityError(
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
            name="bounded closure implementation",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
    except registry.EpochRegistryError as exc:
        raise SourceDerivedBoundedTerminalClosureIntegrityError(str(exc)) from exc
    return {
        "path": IMPLEMENTATION_LOGICAL_PATH,
        "sha256": snapshot.sha256,
        "size_bytes": snapshot.size_bytes,
    }


def load_source_derived_bounded_terminal_closure_profile(
    path: Path = DEFAULT_CONFIG_PATH, *, project_root: Path = ROOT
) -> dict[str, Any]:
    root = project_root.resolve()
    resolved = path if path.is_absolute() else root / path
    if root != ROOT.resolve() or resolved.resolve() != DEFAULT_CONFIG_PATH.resolve():
        raise SourceDerivedBoundedTerminalClosureConfigError(
            "Only the reviewed default bounded-closure profile is accepted"
        )
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            resolved,
            name="bounded closure profile",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
        profile = registry._decode_json(  # noqa: SLF001
            snapshot.raw, name="bounded closure profile"
        )
    except registry.EpochRegistryError as exc:
        raise SourceDerivedBoundedTerminalClosureConfigError(str(exc)) from exc
    if snapshot.sha256 != DEFAULT_CONFIG_SHA256:
        raise SourceDerivedBoundedTerminalClosureConfigError(
            "Bounded closure profile SHA-256 changed"
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
        != "ootang_epoch_source_derived_bounded_terminal_closure_profile_v1"
        or profile["profile_id"]
        != "ootang-epoch-source-derived-bounded-terminal-closure-v1"
        or profile["profile_version"]
        != "1.0.0-exact-frozen-effective-successor-cut"
        or profile["case"] != "ootang"
        or profile["artifact_status"]
        != "one_immutable_source_edge_bounded_terminal_or_supersession_closure_only"
        or profile["formal_warning_output"] is not False
        or profile["default_pipeline_member"] is not False
        or profile["upstream"] != EXPECTED_UPSTREAM
        or profile["runtime"] != EXPECTED_RUNTIME
        or profile["protocol"] != EXPECTED_PROTOCOL
        or profile["engineering_capabilities"] != _claims()
    ):
        raise SourceDerivedBoundedTerminalClosureConfigError(
            "Bounded closure profile semantics changed"
        )
    for binding in EXPECTED_UPSTREAM.values():
        upstream = _child(root, str(binding["path"]), name="closure upstream")
        try:
            actual = registry._read_regular(  # noqa: SLF001
                upstream,
                name="bounded closure upstream",
                maximum_bytes=64 * 1024 * 1024,
            )
        except registry.EpochRegistryError as exc:
            raise SourceDerivedBoundedTerminalClosureConfigError(str(exc)) from exc
        if actual.sha256 != binding["expected_sha256"]:
            raise SourceDerivedBoundedTerminalClosureConfigError(
                f"Pinned bounded-closure upstream changed:{binding['path']}"
            )
    current.load_source_derived_current_effective_workset_terminal_coverage_profile()
    derived.load_source_ingest_derived_profile()
    current.overlay.load_source_derived_workset_overlay_profile()
    recovery.load_workset_recovery_profile()
    drain.load_drain_profile()
    profile["_profile_sha256"] = snapshot.sha256
    return profile


def source_derived_bounded_terminal_closure_paths(
    profile: Mapping[str, Any],
    *,
    registry_root: Path | None = None,
    active_root: Path | None = None,
    shadow_root: Path | None = None,
) -> SourceDerivedBoundedTerminalClosurePaths:
    registry_path = (
        registry_root or ROOT / str(profile["runtime"]["registry_root"])
    ).resolve()
    current_paths = (
        current.source_derived_current_effective_workset_terminal_coverage_paths(
            current.load_source_derived_current_effective_workset_terminal_coverage_profile(),
            registry_root=registry_path,
            active_root=active_root,
            shadow_root=shadow_root,
        )
    )
    recovery_root = _child(
        registry_path,
        str(profile["runtime"]["recovery_namespace"]),
        name="bounded closure recovery root",
    )
    root = _child(
        recovery_root,
        str(profile["runtime"]["namespace"]),
        name="bounded closure root",
    )
    return SourceDerivedBoundedTerminalClosurePaths(
        registry_root=registry_path,
        root=root,
        proofs=_child(root, str(profile["runtime"]["proofs"]), name="proofs"),
        events=_child(root, str(profile["runtime"]["events"]), name="events"),
        status=_child(root, str(profile["runtime"]["status"]), name="status"),
        current=current_paths,
    )


def _identity(item: Mapping[str, Any]) -> dict[str, str]:
    try:
        return current._identity(item)  # noqa: SLF001
    except current.SourceDerivedCurrentEffectiveWorksetTerminalCoverageError as exc:
        raise SourceDerivedBoundedTerminalClosureIntegrityError(str(exc)) from exc


def _identity_tuple(item: Mapping[str, Any]) -> tuple[str, str, str]:
    identity = _identity(item)
    return (
        identity["key_id"],
        identity["natural_key"],
        identity["namespace_digest"],
    )


def _strict_entries(directory: Path, *, name: str) -> tuple[Path, ...]:
    try:
        return current._strict_entries(directory, name=name)  # noqa: SLF001
    except current.SourceDerivedCurrentEffectiveWorksetTerminalCoverageError as exc:
        raise SourceDerivedBoundedTerminalClosureIntegrityError(str(exc)) from exc


def _strict_json(
    path: Path, *, name: str
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    try:
        return current._strict_json(path, name=name)  # noqa: SLF001
    except current.SourceDerivedCurrentEffectiveWorksetTerminalCoverageError as exc:
        raise SourceDerivedBoundedTerminalClosureIntegrityError(str(exc)) from exc


def _own_children_present(paths: SourceDerivedBoundedTerminalClosurePaths) -> bool:
    return bool(
        _strict_entries(paths.proofs, name="bounded closure proofs")
        or _strict_entries(paths.events, name="bounded closure events")
    )


def _load_inputs(
    paths: SourceDerivedBoundedTerminalClosurePaths,
    now: datetime,
    *,
    load_derived_authority: current.overlay.LoadDerivedAuthority | None,
) -> tuple[_Inputs | None, str, str]:
    leaf_inputs, status, reason = current._load_inputs(  # noqa: SLF001
        paths.current,
        now,
        load_derived_authority=load_derived_authority,
    )
    if leaf_inputs is None:
        return None, status, reason
    assessment = current._assessment(leaf_inputs)  # noqa: SLF001
    current_profile = (
        current.load_source_derived_current_effective_workset_terminal_coverage_profile()
    )
    payload = current._proof_payload(  # noqa: SLF001
        current_profile, paths.current, leaf_inputs, assessment
    )
    proof, event = current._load_state(  # noqa: SLF001
        current_profile, paths.current, payload
    )
    if proof is None or event is None:
        return (
            None,
            "waiting_for_current_effective_terminal_coverage",
            "matching current-effective terminal coverage proof/event is pending",
        )
    return (
        _Inputs(
            current_inputs=leaf_inputs,
            current_assessment=assessment,
            current_payload=payload,
            current_proof=proof[1],
            current_event=event[1],
        ),
        "inputs_current",
        "immutable cut and current-effective coverage are current",
    )


def _pairwise_disjoint(values: Sequence[set[tuple[str, str, str]]]) -> bool:
    seen: set[tuple[str, str, str]] = set()
    for value in values:
        if seen & value:
            return False
        seen.update(value)
    return True


def _assessment(inputs: _Inputs) -> _Assessment:
    computation = inputs.current_inputs.computation
    try:
        base_items, _, base_graph = recovery._dag(  # noqa: SLF001
            computation.reservation
        )
    except recovery.WorksetRecoveryError as exc:
        raise SourceDerivedBoundedTerminalClosureIntegrityError(str(exc)) from exc
    if not 0 < len(base_items) <= EXPECTED_PROTOCOL["maximum_items"]:
        raise SourceDerivedBoundedTerminalClosureIntegrityError(
            "Frozen base inventory is empty or oversized"
        )
    base_identities = tuple(_identity_tuple(item) for item in base_items)
    base_set = set(base_identities)
    if (
        len(base_set) != len(base_identities)
        or len({identity[1] for identity in base_identities})
        != len(base_identities)
    ):
        raise SourceDerivedBoundedTerminalClosureIntegrityError(
            "Frozen base identity inventory is not unique"
        )

    terminal_rows = inputs.current_assessment.rows
    terminal_by_identity = {_identity_tuple(row): row for row in terminal_rows}
    current_set = set(terminal_by_identity)
    if (
        len(current_set) != len(terminal_rows)
        or inputs.current_payload.get("current_effective_workset_terminal_coverage")
        is not True
        or inputs.current_payload.get("effective_item_count") != len(current_set)
    ):
        raise SourceDerivedBoundedTerminalClosureIntegrityError(
            "Current-effective terminal authority is not an exact identity bijection"
        )

    retained_payload = inputs.current_inputs.retained_payload
    parent_identity = _identity_tuple(
        retained_payload["source_parent_excluded_identity"]
    )
    retained_targets = tuple(retained_payload["retained_target_rows"])
    retained_set = {_identity_tuple(row) for row in retained_targets}
    rebound_pairs = tuple(retained_payload["excluded_rebound_old_rows"])
    invalidated_rows = tuple(retained_payload["excluded_invalidated_rows"])
    rebound_old_set = {
        _identity_tuple(row["old_identity"]) for row in rebound_pairs
    }
    rebound_new_set = {
        _identity_tuple(row["replacement_identity"]) for row in rebound_pairs
    }
    invalidated_set = {
        _identity_tuple(row["invalidated_identity"]) for row in invalidated_rows
    }
    if (
        len(retained_set) != len(retained_targets)
        or len(rebound_old_set) != len(rebound_pairs)
        or len(rebound_new_set) != len(rebound_pairs)
        or len(invalidated_set) != len(invalidated_rows)
        or not _pairwise_disjoint(
            [{parent_identity}, retained_set, rebound_old_set, invalidated_set]
        )
        or {parent_identity} | retained_set | rebound_old_set | invalidated_set
        != base_set
    ):
        raise SourceDerivedBoundedTerminalClosureIntegrityError(
            "Frozen base is not exactly parent plus retained plus R-old plus I"
        )

    evidence = computation.derived_evidence
    payload = evidence.reservation_payload
    fields = (
        "derived_outcome_items",
        "rebound_existing_items",
        "invalidated_existing_items",
    )
    if any(not isinstance(payload.get(field), list) for field in fields):
        raise SourceDerivedBoundedTerminalClosureIntegrityError(
            "Source-derived D/R/I inventory changed type"
        )
    manifest_sha256 = computation.reservation.manifest_snapshot.sha256
    try:
        d_rows = tuple(
            current.overlay._normalize_item(  # noqa: SLF001
                row, manifest_sha256, name=f"bounded derived addition[{index}]"
            )
            for index, row in enumerate(payload[fields[0]])
        )
        r_rows = tuple(
            current.overlay._normalize_item(  # noqa: SLF001
                row, manifest_sha256, name=f"bounded rebound replacement[{index}]"
            )
            for index, row in enumerate(payload[fields[1]])
        )
        i_rows = tuple(
            current.overlay._normalize_item(  # noqa: SLF001
                row, manifest_sha256, name=f"bounded invalidation[{index}]"
            )
            for index, row in enumerate(payload[fields[2]])
        )
    except current.overlay.SourceDerivedWorksetOverlayError as exc:
        raise SourceDerivedBoundedTerminalClosureIntegrityError(str(exc)) from exc
    d_set = {_identity_tuple(row) for row in d_rows}
    r_set = {_identity_tuple(row) for row in r_rows}
    i_set = {_identity_tuple(row) for row in i_rows}
    d_natural = {identity[1] for identity in d_set}
    r_natural = {identity[1] for identity in r_set}
    i_natural = {identity[1] for identity in i_set}
    if (
        len(d_set) != len(d_rows)
        or len(r_set) != len(r_rows)
        or len(i_set) != len(i_rows)
        or len(d_natural) != len(d_rows)
        or len(r_natural) != len(r_rows)
        or len(i_natural) != len(i_rows)
        or d_natural & r_natural
        or d_natural & i_natural
        or r_natural & i_natural
        or payload.get("complete_for_exact_source_snapshot_edge") is not True
        or payload.get("slot_id") != evidence.authority.slot_id
        or payload.get("derived_outcome_item_count") != len(d_rows)
        or payload.get("rebound_existing_item_count") != len(r_rows)
        or payload.get("invalidated_existing_item_count") != len(i_rows)
    ):
        raise SourceDerivedBoundedTerminalClosureIntegrityError(
            "Source-derived successor classification is incomplete or overlapping"
        )
    for rows, count_field, digest_field in (
        (
            d_rows,
            "derived_outcome_item_count",
            "derived_outcome_keyset_sha256",
        ),
        (
            r_rows,
            "rebound_existing_item_count",
            "rebound_existing_keyset_sha256",
        ),
        (
            i_rows,
            "invalidated_existing_item_count",
            "invalidated_existing_keyset_sha256",
        ),
    ):
        identity_rows = [
            {
                "natural_key": row["natural_key"],
                "namespace_digest": row["namespace_digest"],
            }
            for row in rows
        ]
        if (
            payload[count_field] != len(rows)
            or payload[digest_field] != _sha256(_canonical_bytes(identity_rows))
        ):
            raise SourceDerivedBoundedTerminalClosureIntegrityError(
                "Source-derived classification count or digest changed"
            )

    source_identity = _identity_tuple(payload["source_ingest"])
    cross_identity = _identity_tuple(computation.cross_authority.source_item)
    retained_d_set = {
        _identity_tuple(row) for row in retained_payload["derived_addition_rows"]
    }
    retained_r_pairs = {
        (
            _identity_tuple(row["old_identity"]),
            _identity_tuple(row["replacement_identity"]),
        )
        for row in rebound_pairs
    }
    raw_r_pairs = {
        (
            next(
                identity
                for identity in base_set
                if identity[1] == _identity_tuple(row)[1]
            ),
            _identity_tuple(row),
        )
        for row in r_rows
    }
    if (
        source_identity != parent_identity
        or cross_identity != parent_identity
        or retained_d_set != d_set
        or retained_r_pairs != raw_r_pairs
        or invalidated_set != i_set
    ):
        raise SourceDerivedBoundedTerminalClosureIntegrityError(
            "Frozen, derived, cross-freeze, and retained partitions disagree"
        )
    expected_current = {parent_identity} | retained_set | d_set | r_set
    if (
        expected_current != current_set
        or rebound_old_set & current_set
        or invalidated_set & current_set
        or not (d_set | r_set) <= current_set
        or computation.application.get("base_item_count") != len(base_items)
        or computation.application.get("effective_item_count") != len(current_set)
        or computation.application.get("derived_added_count") != len(d_rows)
        or computation.application.get("rebound_replaced_count") != len(r_rows)
        or computation.application.get("invalidated_superseded_count")
        != len(i_rows)
        or computation.effective_workset.get("base_dependency_graph_sha256")
        != base_graph
    ):
        raise SourceDerivedBoundedTerminalClosureIntegrityError(
            "Current effective inventory is not the exact source-derived replacement"
        )

    rebound_by_old = {old: new for old, new in raw_r_pairs}
    base_resolution_rows: list[dict[str, Any]] = []
    for index, item in enumerate(base_items):
        identity = _identity_tuple(item)
        if identity == parent_identity:
            kind = "source_parent_terminal_in_current_effective"
            successor = identity
            terminal_sha = terminal_by_identity[identity][
                "effective_terminal_row_sha256"
            ]
        elif identity in retained_set:
            kind = "retained_identity_terminal_in_current_effective"
            successor = identity
            terminal_sha = terminal_by_identity[identity][
                "effective_terminal_row_sha256"
            ]
        elif identity in rebound_by_old:
            kind = "rebound_old_superseded_by_terminal_current_identity"
            successor = rebound_by_old[identity]
            terminal_sha = terminal_by_identity[successor][
                "effective_terminal_row_sha256"
            ]
        elif identity in invalidated_set:
            kind = "invalidated_by_exact_source_edge"
            successor = None
            terminal_sha = None
        else:
            raise SourceDerivedBoundedTerminalClosureIntegrityError(
                "Frozen identity has no bounded resolution"
            )
        body = {
            "base_topological_index": index,
            "frozen_identity": _identity(item),
            "resolution_kind": kind,
            "current_successor_identity": (
                {
                    "key_id": successor[0],
                    "natural_key": successor[1],
                    "namespace_digest": successor[2],
                }
                if successor is not None
                else None
            ),
            "current_effective_terminal_row_sha256": terminal_sha,
        }
        base_resolution_rows.append(
            {**body, "base_resolution_row_sha256": _sha256(_canonical_bytes(body))}
        )

    old_by_natural = {identity[1]: identity for identity in rebound_old_set}
    successor_resolution_rows: list[dict[str, Any]] = []
    classified = (
        (("D", row) for row in d_rows),
        (("R", row) for row in r_rows),
        (("I", row) for row in i_rows),
    )
    position = 0
    for group in classified:
        for kind, item in group:
            identity = _identity_tuple(item)
            if kind == "D":
                resolution = "derived_addition_terminal_in_current_effective"
                previous = None
                terminal_sha = terminal_by_identity[identity][
                    "effective_terminal_row_sha256"
                ]
            elif kind == "R":
                resolution = "rebound_replacement_terminal_in_current_effective"
                previous_tuple = old_by_natural[identity[1]]
                previous = {
                    "key_id": previous_tuple[0],
                    "natural_key": previous_tuple[1],
                    "namespace_digest": previous_tuple[2],
                }
                terminal_sha = terminal_by_identity[identity][
                    "effective_terminal_row_sha256"
                ]
            else:
                resolution = "invalidated_frozen_identity_superseded"
                previous = _identity(item)
                terminal_sha = None
            body = {
                "classification_index": position,
                "classification_kind": kind,
                "classified_identity": _identity(item),
                "previous_frozen_identity": previous,
                "resolution_kind": resolution,
                "current_effective_terminal_row_sha256": terminal_sha,
            }
            successor_resolution_rows.append(
                {
                    **body,
                    "successor_resolution_row_sha256": _sha256(
                        _canonical_bytes(body)
                    ),
                }
            )
            position += 1
    if (
        len(base_resolution_rows) != len(base_items)
        or len(successor_resolution_rows) != len(d_rows) + len(r_rows) + len(i_rows)
    ):
        raise SourceDerivedBoundedTerminalClosureIntegrityError(
            "Bounded resolution row counts changed"
        )
    return _Assessment(
        tuple(base_resolution_rows),
        tuple(successor_resolution_rows),
        len(base_items),
        len(current_set),
        len(d_rows),
        len(r_rows),
        len(i_rows),
    )


def _proof_payload(
    profile: Mapping[str, Any],
    paths: SourceDerivedBoundedTerminalClosurePaths,
    inputs: _Inputs,
    assessment: _Assessment,
) -> dict[str, object]:
    computation = inputs.current_inputs.computation
    reservation = computation.reservation
    evidence = computation.derived_evidence
    base_rows = [dict(row) for row in assessment.base_resolution_rows]
    successor_rows = [dict(row) for row in assessment.successor_resolution_rows]
    return {
        "schema_version": profile["protocol"]["proof_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "authority_scope": (
            "one_immutable_source_edge_frozen_effective_successor_bounded_"
            "terminal_or_supersession_closure_only"
        ),
        "overlay_slot_id": computation.slot_id,
        "derived_slot_id": evidence.authority.slot_id,
        "manifest": _reference(reservation.manifest_snapshot, reservation.paths.root),
        "manifest_reservation_event": _reference(
            reservation.event_snapshot, reservation.paths.root
        ),
        "cross_completion_receipt": _reference(
            computation.cross_receipt_snapshot, paths.current.overlay.cross.root
        ),
        "cross_completion_event": _reference(
            computation.cross_event_snapshot, paths.current.overlay.cross.root
        ),
        "derived_reservation": _reference(
            evidence.reservation_snapshot, paths.current.overlay.derived.root
        ),
        "derived_reservation_event": _reference(
            evidence.event_snapshot, paths.current.overlay.derived.root
        ),
        "overlay_object": _reference(
            inputs.current_inputs.overlay_object, paths.current.overlay.root
        ),
        "overlay_event": _reference(
            inputs.current_inputs.overlay_event, paths.current.overlay.root
        ),
        "current_effective_terminal_coverage_proof": _reference(
            inputs.current_proof, paths.current.root
        ),
        "current_effective_terminal_coverage_event": _reference(
            inputs.current_event, paths.current.root
        ),
        "source_parent_identity": dict(
            inputs.current_inputs.parent_payload["source_parent_identity"]
        ),
        "frozen_base_item_count": assessment.frozen_base_item_count,
        "current_effective_item_count": assessment.current_effective_item_count,
        "derived_item_count": assessment.derived_item_count,
        "rebound_item_count": assessment.rebound_item_count,
        "invalidated_item_count": assessment.invalidated_item_count,
        "base_resolution_rows": base_rows,
        "base_resolution_rows_sha256": _sha256(_canonical_bytes(base_rows)),
        "successor_resolution_rows": successor_rows,
        "successor_resolution_rows_sha256": _sha256(
            _canonical_bytes(successor_rows)
        ),
        "frozen_base_partition_exact": True,
        "current_effective_partition_exact": True,
        "source_derived_successor_inventory_complete": True,
        "all_frozen_base_identities_resolved_under_source_edge": True,
        "all_source_derived_successors_resolved_under_source_edge": True,
        "effective_item_identity_set_sha256": computation.effective_workset[
            "item_identity_set_sha256"
        ],
        "effective_dependency_graph_sha256": computation.effective_workset[
            "dependency_graph_sha256"
        ],
        "current_effective_terminal_rows_sha256": inputs.current_payload[
            "ordered_effective_terminal_rows_sha256"
        ],
        "derived_inventory_digests": {
            name: evidence.reservation_payload[name]
            for name in (
                "derived_outcome_keyset_sha256",
                "rebound_existing_keyset_sha256",
                "invalidated_existing_keyset_sha256",
            )
        },
        "implementation": _implementation_reference(),
        **_claims(complete=True),
    }


def _publish(
    path: Path, payload: Mapping[str, Any], *, root: Path, name: str
) -> registry.ArtifactSnapshot:
    raw = _canonical_bytes(payload)
    if len(raw) > MAX_CONTROL_BYTES:
        raise SourceDerivedBoundedTerminalClosureIntegrityError(
            f"{name} capacity exceeded"
        )
    try:
        return drain._publish_once_durable(  # noqa: SLF001
            path, raw, root=root, name=name
        )
    except drain.EpochDrainError as exc:
        raise SourceDerivedBoundedTerminalClosureIntegrityError(str(exc)) from exc


def _load_state(
    profile: Mapping[str, Any],
    paths: SourceDerivedBoundedTerminalClosurePaths,
    expected: Mapping[str, Any],
) -> tuple[
    tuple[dict[str, Any], registry.ArtifactSnapshot] | None,
    tuple[dict[str, Any], registry.ArtifactSnapshot] | None,
]:
    proofs = _strict_entries(paths.proofs, name="bounded closure proofs")
    events = _strict_entries(paths.events, name="bounded closure events")
    if len(proofs) > 1 or len(events) > 1:
        raise SourceDerivedBoundedTerminalClosureIntegrityError(
            "Bounded closure singleton namespace branched"
        )
    proof = None
    if proofs:
        payload, snapshot = _strict_json(proofs[0], name="bounded closure proof")
        match = PROOF_NAME.fullmatch(proofs[0].name)
        if (
            match is None
            or match.group("digest") != snapshot.sha256
            or payload != expected
        ):
            raise SourceDerivedBoundedTerminalClosureIntegrityError(
                "Bounded closure proof differs from the current immutable cut"
            )
        proof = (payload, snapshot)
    event = None
    if events:
        if proof is None:
            raise SourceDerivedBoundedTerminalClosureIntegrityError(
                "Bounded closure event is orphaned"
            )
        payload, snapshot = _strict_json(events[0], name="bounded closure event")
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
            "frozen_base_item_count": expected["frozen_base_item_count"],
            "current_effective_item_count": expected[
                "current_effective_item_count"
            ],
            "base_resolution_rows_sha256": expected[
                "base_resolution_rows_sha256"
            ],
            "successor_resolution_rows_sha256": expected[
                "successor_resolution_rows_sha256"
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
            raise SourceDerivedBoundedTerminalClosureIntegrityError(
                "Bounded closure singleton event changed"
            )
        try:
            registry._utc_timestamp(recorded, name="bounded closure event time")  # noqa: SLF001
        except registry.EpochRegistryError as exc:
            raise SourceDerivedBoundedTerminalClosureIntegrityError(str(exc)) from exc
        event = (payload, snapshot)
    return proof, event


def _append_event(
    profile: Mapping[str, Any],
    paths: SourceDerivedBoundedTerminalClosurePaths,
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
        "frozen_base_item_count": expected["frozen_base_item_count"],
        "current_effective_item_count": expected["current_effective_item_count"],
        "base_resolution_rows_sha256": expected[
            "base_resolution_rows_sha256"
        ],
        "successor_resolution_rows_sha256": expected[
            "successor_resolution_rows_sha256"
        ],
        **_claims(complete=True),
    }
    payload = {**body, "entry_sha256": _sha256(_canonical_bytes(body))}
    return _publish(
        paths.events / f"{1:020d}-{payload['entry_sha256']}.json",
        payload,
        root=paths.root,
        name="bounded closure event",
    )


def _write_status(
    profile: Mapping[str, Any],
    paths: SourceDerivedBoundedTerminalClosurePaths,
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
        "frozen_base_item_count": (
            assessment.frozen_base_item_count if assessment else 0
        ),
        "current_effective_item_count": (
            assessment.current_effective_item_count if assessment else 0
        ),
        "derived_item_count": assessment.derived_item_count if assessment else 0,
        "rebound_item_count": assessment.rebound_item_count if assessment else 0,
        "invalidated_item_count": (
            assessment.invalidated_item_count if assessment else 0
        ),
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
            name="bounded closure status",
        )
    except registry.EpochRegistryError as exc:
        raise SourceDerivedBoundedTerminalClosureIntegrityError(str(exc)) from exc


def _result(
    paths: SourceDerivedBoundedTerminalClosurePaths,
    status: str,
    reason: str,
    assessment: _Assessment | None,
    *,
    proof: Path | None = None,
    event: Path | None = None,
) -> SourceDerivedBoundedTerminalClosureResult:
    return SourceDerivedBoundedTerminalClosureResult(
        status=status,
        reason=reason,
        status_path=paths.status,
        proof_path=proof,
        event_path=event,
        frozen_base_item_count=(
            assessment.frozen_base_item_count if assessment else 0
        ),
        current_effective_item_count=(
            assessment.current_effective_item_count if assessment else 0
        ),
        derived_item_count=assessment.derived_item_count if assessment else 0,
        rebound_item_count=assessment.rebound_item_count if assessment else 0,
        invalidated_item_count=(
            assessment.invalidated_item_count if assessment else 0
        ),
        current_source_derived_bounded_terminal_closure=event is not None,
    )


def _coordinate_source_derived_bounded_terminal_closure(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    registry_root: Path | None = None,
    active_root: Path | None = None,
    shadow_root: Path | None = None,
    clock: Callable[[], datetime] | None = None,
    fault_hook: FaultHook | None = None,
    load_derived_authority: current.overlay.LoadDerivedAuthority | None = None,
) -> SourceDerivedBoundedTerminalClosureResult:
    profile = load_source_derived_bounded_terminal_closure_profile(config_path)
    paths = source_derived_bounded_terminal_closure_paths(
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
        handles = current.coverage._acquire_locks(  # noqa: SLF001
            paths.current.coverage
        )
        inputs, status, reason = _load_inputs(
            paths, now, load_derived_authority=load_derived_authority
        )
        if inputs is None:
            if _own_children_present(paths):
                raise SourceDerivedBoundedTerminalClosureIntegrityError(
                    "Bounded closure authority outlived its exact current cut"
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
            status = "source_derived_bounded_terminal_closure_current"
            reason = "exact bounded source-derived closure event is current"
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
                name="bounded closure proof",
            )
            if proof_snapshot.sha256 != digest:
                raise SourceDerivedBoundedTerminalClosureIntegrityError(
                    "Bounded closure proof content address changed"
                )
            if fault_hook is not None:
                fault_hook("after_proof")
            status = "source_derived_bounded_terminal_closure_proof_published"
        else:
            proof_snapshot = proof[1]
            status = "source_derived_bounded_terminal_closure_event_forward_adopted"
        event_snapshot = _append_event(profile, paths, proof_snapshot, expected, now)
        reason = "bounded closure proof was published or forward-adopted"
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
    except current.coverage.SourceDerivedEffectiveOutcomeTerminalCoverageBusyError as exc:
        raise SourceDerivedBoundedTerminalClosureBusyError(str(exc)) from exc
    except SourceDerivedBoundedTerminalClosureError:
        raise
    except (
        current.SourceDerivedCurrentEffectiveWorksetTerminalCoverageError,
        recovery.WorksetRecoveryError,
    ) as exc:
        raise SourceDerivedBoundedTerminalClosureIntegrityError(str(exc)) from exc
    except Exception as exc:
        raise SourceDerivedBoundedTerminalClosureIntegrityError(
            f"Bounded closure coordination failed:{type(exc).__name__}:{exc}"
        ) from exc
    finally:
        if handles:
            try:
                current.coverage._release_locks(handles)  # noqa: SLF001
            except current.coverage.SourceDerivedEffectiveOutcomeTerminalCoverageError as exc:
                raise SourceDerivedBoundedTerminalClosureIntegrityError(str(exc)) from exc


def coordinate_source_derived_bounded_terminal_closure(
    **kwargs: Any,
) -> SourceDerivedBoundedTerminalClosureResult:
    """Run one machine-only bounded source-derived closure poll."""

    return _coordinate_source_derived_bounded_terminal_closure(**kwargs)
