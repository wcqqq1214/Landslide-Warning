"""Aggregate exact current source-ingest parent terminality from D/R coverage."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, BinaryIO

from monitoring import ootang_epoch_registry as registry
from monitoring import (
    ootang_epoch_source_derived_effective_outcome_terminal_coverage as coverage,
)
from monitoring import ootang_epoch_workset_recovery as recovery


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = (
    ROOT
    / "config"
    / "ootang_epoch_source_derived_source_parent_terminal_aggregate.v1.json"
)
DEFAULT_CONFIG_SHA256 = (
    "6c9f89bb0f2089f19df71fa9a0bd47a54db8df4638e596ddf98f4c513c2f0b4e"
)
IMPLEMENTATION_LOGICAL_PATH = (
    "code/monitoring/ootang_epoch_source_derived_source_parent_terminal_aggregate.py"
)
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
    "namespace": "source_derived_source_parent_terminal_aggregate_v1",
    "proofs": "proofs",
    "events": "events",
    "status": "status.json",
    "active_root": "runtime/ootang_prequential_live_v1",
    "shadow_root": "runtime/ootang_prequential_calibration_shadow_v1",
}
EXPECTED_PROTOCOL = {
    "proof_schema_version": "ootang_epoch_source_derived_source_parent_terminal_aggregate_proof_v1",
    "event_schema_version": "ootang_epoch_source_derived_source_parent_terminal_aggregate_event_v1",
    "status_schema_version": "ootang_epoch_source_derived_source_parent_terminal_aggregate_status_v1",
    "event_type": "epoch_source_derived_source_parent_terminal_aggregate_proved",
    "canonical_json": "utf8_sort_keys_compact_no_nan_trailing_lf",
    "initial_previous_entry_sha256": ZERO_HASH,
    "publication_policy": "deterministic_content_addressed_singleton_proof_then_singleton_event",
}
TRUE_CAPABILITIES = (
    "machine_only",
    "current_dri_coverage_deep_verified",
    "recovery_v6_authority_deep_verified",
    "exact_current_source_ingest_parent_verified",
    "content_addressed_source_parent_terminal_proof_implemented",
    "singleton_source_parent_terminal_event_implemented",
    "proof_event_forward_adoption_implemented",
)
FALSE_CLAIMS = (
    "source_parent_terminal",
    "terminal_for_recovery_v6_key",
    "all_effective_items_terminal",
    "terminal_transition_closure_implemented",
    "bounded_workset_recovery_implemented",
    "recovery_v6_mutated",
    "effective_workset_overlay_mutated",
    "lifecycle_authority",
    "transition_authority",
    "old_epoch_drained",
    "active_epoch_switch_implemented",
    "automatic_epoch_rotation_implemented",
    "formal_warning_output",
    "transitive_terminal_closure_implemented",
    "all_reserved_items_settled",
    "all_reserved_successors_supported",
    "all_content_dependent_lanes_reserved",
    "drained_eligibility_current",
    "trusted_anchor_receipt_verified",
    "e2_live_evidence_eligible",
    "real_activation_ready",
    "network_action_performed",
)


class SourceDerivedSourceParentTerminalAggregateError(RuntimeError):
    pass


class SourceDerivedSourceParentTerminalAggregateConfigError(
    SourceDerivedSourceParentTerminalAggregateError
):
    pass


class SourceDerivedSourceParentTerminalAggregateIntegrityError(
    SourceDerivedSourceParentTerminalAggregateError
):
    pass


class SourceDerivedSourceParentTerminalAggregateBusyError(
    SourceDerivedSourceParentTerminalAggregateError
):
    pass


@dataclass(frozen=True)
class SourceDerivedSourceParentTerminalAggregatePaths:
    registry_root: Path
    root: Path
    proofs: Path
    events: Path
    status: Path
    coverage: coverage.SourceDerivedEffectiveOutcomeTerminalCoveragePaths


@dataclass(frozen=True)
class SourceDerivedSourceParentTerminalAggregateResult:
    status: str
    reason: str
    status_path: Path
    source_key_id: str | None = None
    proof_path: Path | None = None
    event_path: Path | None = None
    current_source_ingest_parent_terminal: bool = False
    source_parent_terminal: bool = False
    terminal_for_recovery_v6_key: bool = False
    all_effective_items_terminal: bool = False
    terminal_transition_closure_implemented: bool = False
    lifecycle_authority: bool = False


FaultHook = Callable[[str], None]


def _canonical_bytes(value: object) -> bytes:
    return coverage._canonical_bytes(value)  # noqa: SLF001


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _claims(*, complete: bool = False) -> dict[str, bool]:
    return {
        "current_source_ingest_parent_terminal": complete,
        **{claim: False for claim in FALSE_CLAIMS},
    }


def _reference(snapshot: registry.ArtifactSnapshot, root: Path) -> dict[str, object]:
    return coverage._reference(snapshot, root)  # noqa: SLF001


def _implementation_reference() -> dict[str, object]:
    path = ROOT / IMPLEMENTATION_LOGICAL_PATH
    snapshot = registry._read_regular(  # noqa: SLF001
        path,
        name="source-parent aggregate implementation",
        maximum_bytes=4 * 1024 * 1024,
    )
    return {
        "logical_path": IMPLEMENTATION_LOGICAL_PATH,
        "sha256": snapshot.sha256,
        "size_bytes": snapshot.size_bytes,
    }


def load_source_derived_source_parent_terminal_aggregate_profile(
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> dict[str, Any]:
    if config_path.resolve() != DEFAULT_CONFIG_PATH.resolve():
        raise SourceDerivedSourceParentTerminalAggregateConfigError(
            "Only the reviewed default aggregate profile is accepted"
        )
    try:
        raw = config_path.read_bytes()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceDerivedSourceParentTerminalAggregateConfigError(str(exc)) from exc
    digest = _sha256(raw)
    if (
        config_path.resolve() == DEFAULT_CONFIG_PATH.resolve()
        and digest != DEFAULT_CONFIG_SHA256
    ):
        raise SourceDerivedSourceParentTerminalAggregateConfigError(
            "Default aggregate profile changed without a version/hash update"
        )
    if (
        not isinstance(payload, dict)
        or payload.get("upstream") != EXPECTED_UPSTREAM
        or payload.get("runtime") != EXPECTED_RUNTIME
        or payload.get("protocol") != EXPECTED_PROTOCOL
    ):
        raise SourceDerivedSourceParentTerminalAggregateConfigError(
            "Aggregate profile contract changed"
        )
    capabilities = payload.get("engineering_capabilities")
    if (
        not isinstance(capabilities, dict)
        or any(capabilities.get(name) is not True for name in TRUE_CAPABILITIES)
        or any(
            capabilities.get(name) is not False
            for name in (*FALSE_CLAIMS, "current_source_ingest_parent_terminal")
        )
    ):
        raise SourceDerivedSourceParentTerminalAggregateConfigError(
            "Aggregate capability boundary changed"
        )
    for pin in EXPECTED_UPSTREAM.values():
        path = ROOT / str(pin["path"])
        if (
            not path.is_file()
            or registry._read_regular(  # noqa: SLF001
                path,
                name="aggregate pinned upstream",
                maximum_bytes=4 * 1024 * 1024,
            ).sha256
            != pin["expected_sha256"]
        ):
            raise SourceDerivedSourceParentTerminalAggregateConfigError(
                f"Pinned upstream changed: {pin['path']}"
            )
    coverage.load_source_derived_effective_outcome_terminal_coverage_profile()
    recovery.load_workset_recovery_profile()
    return {**payload, "_profile_sha256": digest}


def source_derived_source_parent_terminal_aggregate_paths(
    profile: Mapping[str, Any],
    *,
    registry_root: Path | None = None,
    active_root: Path | None = None,
    shadow_root: Path | None = None,
) -> SourceDerivedSourceParentTerminalAggregatePaths:
    registry_path = (
        registry_root or ROOT / str(profile["runtime"]["registry_root"])
    ).resolve()
    coverage_profile = (
        coverage.load_source_derived_effective_outcome_terminal_coverage_profile()
    )
    coverage_paths = coverage.source_derived_effective_outcome_terminal_coverage_paths(
        coverage_profile,
        registry_root=registry_path,
        active_root=active_root,
        shadow_root=shadow_root,
    )
    root = (
        registry_path
        / str(profile["runtime"]["recovery_namespace"])
        / str(profile["runtime"]["namespace"])
    )
    return SourceDerivedSourceParentTerminalAggregatePaths(
        registry_root=registry_path,
        root=root,
        proofs=root / str(profile["runtime"]["proofs"]),
        events=root / str(profile["runtime"]["events"]),
        status=root / str(profile["runtime"]["status"]),
        coverage=coverage_paths,
    )


def _source_parent(computation: Any) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    reservation = computation.reservation
    ordered, _, _ = recovery._dag(reservation)  # noqa: SLF001
    candidates = [
        item
        for item in ordered
        if item.get("family") == "outcome_revision"
        and item.get("canonical_successor_state") == "source_snapshot_ingested"
    ]
    if len(candidates) != 1:
        raise SourceDerivedSourceParentTerminalAggregateIntegrityError(
            "Frozen authority does not contain exactly one source-ingest parent"
        )
    parent = candidates[0]
    if dict(parent) != dict(computation.cross_authority.source_item):
        raise SourceDerivedSourceParentTerminalAggregateIntegrityError(
            "Frozen source parent differs from the cross-freeze source item"
        )
    triple = (
        parent.get("key_id"),
        parent.get("natural_key"),
        parent.get("namespace_digest"),
    )
    effective_matches = [
        item
        for item in computation.effective_items
        if (item.get("key_id"), item.get("natural_key"), item.get("namespace_digest"))
        == triple
    ]
    if len(effective_matches) != 1:
        raise SourceDerivedSourceParentTerminalAggregateIntegrityError(
            "Exact frozen source parent is not uniquely retained by the current overlay"
        )
    derived_payload = computation.derived_evidence.reservation_payload
    reserved_rows = [
        row
        for field in (
            "derived_outcome_items",
            "rebound_existing_items",
            "invalidated_existing_items",
        )
        for row in derived_payload[field]
    ]
    parent_identity = (parent["natural_key"], parent["namespace_digest"])
    reserved_identities = {
        (row["natural_key"], row["namespace_digest"]) for row in reserved_rows
    }
    reserved_natural_keys = {row["natural_key"] for row in reserved_rows}
    if (
        parent_identity in reserved_identities
        or parent["natural_key"] in reserved_natural_keys
    ):
        raise SourceDerivedSourceParentTerminalAggregateIntegrityError(
            "Source parent was reclassified into D/R/I"
        )
    plan = recovery._transition_plan(parent)  # noqa: SLF001
    edges = {row["action"]: row["next_actions"] for row in plan["edges"]}
    if (
        plan["initial_action"] != "source_snapshot_ingested"
        or edges.get("source_snapshot_ingested") != ["derived_outcome_items_required"]
        or edges.get("derived_outcome_items_required") != []
        or plan["closure_resolved"] is not False
        or plan["terminal_actions"] != []
    ):
        raise SourceDerivedSourceParentTerminalAggregateIntegrityError(
            "Source parent recovery transition contract changed"
        )
    return parent, plan


def _proof_payload(
    profile: Mapping[str, Any],
    paths: SourceDerivedSourceParentTerminalAggregatePaths,
    computation: Any,
    overlay_event: registry.ArtifactSnapshot,
    coverage_proof: registry.ArtifactSnapshot,
    coverage_event: registry.ArtifactSnapshot,
    coverage_payload: Mapping[str, Any],
) -> dict[str, object]:
    parent, plan = _source_parent(computation)
    evidence = computation.derived_evidence
    derived_payload = evidence.reservation_payload
    derivation_body = {
        "source_key_id": parent["key_id"],
        "canonical_step_index": 1,
        "canonical_step_id": recovery._step_id(  # noqa: SLF001
            str(parent["key_id"]), 1, "derived_outcome_items_required"
        ),
        "action": "derived_outcome_items_required",
        "previous_action": "source_snapshot_ingested",
        "previous_step_index": 0,
        "previous_step_id": recovery._step_id(  # noqa: SLF001
            str(parent["key_id"]), 0, "source_snapshot_ingested"
        ),
        "next_actions": [],
    }
    source_terminal_derivation = {
        **derivation_body,
        "original_closure_resolved": False,
        "original_terminal_actions": [],
        "terminal_for_recovery_v6_key": False,
        "terminal_scope": "current_source_ingest_parent_after_exact_dri_terminal_coverage_only",
    }
    cross_root = paths.coverage.consumption.dispatch.dispatch.overlay.cross
    derived_root = paths.coverage.consumption.dispatch.dispatch.overlay.derived
    return {
        "schema_version": profile["protocol"]["proof_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "source_parent_identity": {
            "key_id": parent["key_id"],
            "natural_key": parent["natural_key"],
            "namespace_digest": parent["namespace_digest"],
        },
        "source_parent_transition_plan": plan,
        "source_terminal_derivation": source_terminal_derivation,
        "cross_completion_receipt": _reference(
            computation.cross_receipt_snapshot, cross_root.root
        ),
        "cross_completion_event": _reference(
            computation.cross_event_snapshot, cross_root.root
        ),
        "derived_reservation": _reference(
            evidence.reservation_snapshot, derived_root.root
        ),
        "derived_reservation_event": _reference(
            evidence.event_snapshot, derived_root.root
        ),
        "overlay_event": _reference(
            overlay_event, paths.coverage.consumption.dispatch.dispatch.overlay.root
        ),
        "dri_terminal_coverage_proof": _reference(coverage_proof, paths.coverage.root),
        "dri_terminal_coverage_event": _reference(coverage_event, paths.coverage.root),
        "dri_terminal_coverage_proof_sha256": coverage_proof.sha256,
        "dri_terminal_coverage_required_key_count": coverage_payload[
            "required_key_count"
        ],
        "dri_terminal_coverage_ordered_rows_sha256": coverage_payload[
            "ordered_coverage_rows_sha256"
        ],
        "derived_reservation_counts_and_digests": {
            name: derived_payload[name]
            for name in (
                "derived_outcome_item_count",
                "derived_outcome_keyset_sha256",
                "rebound_existing_item_count",
                "rebound_existing_keyset_sha256",
                "invalidated_existing_item_count",
                "invalidated_existing_keyset_sha256",
            )
        },
        "derived_reservation_identity_audit": {
            field: [
                {
                    "natural_key": row["natural_key"],
                    "namespace_digest": row["namespace_digest"],
                }
                for row in derived_payload[field]
            ]
            for field in (
                "derived_outcome_items",
                "rebound_existing_items",
                "invalidated_existing_items",
            )
        },
        "effective_item_identity_set_sha256": computation.effective_workset[
            "item_identity_set_sha256"
        ],
        "effective_dependency_graph_sha256": computation.effective_workset[
            "dependency_graph_sha256"
        ],
        "implementation": _implementation_reference(),
        **_claims(complete=True),
    }


def _load_state(
    profile: Mapping[str, Any],
    paths: SourceDerivedSourceParentTerminalAggregatePaths,
    expected: Mapping[str, Any],
):
    proofs = coverage._strict_entries(paths.proofs, name="source-parent proofs")  # noqa: SLF001
    events = coverage._strict_entries(paths.events, name="source-parent events")  # noqa: SLF001
    if len(proofs) > 1 or len(events) > 1:
        raise SourceDerivedSourceParentTerminalAggregateIntegrityError(
            "Aggregate singleton namespace branched"
        )
    proof = None
    if proofs:
        payload, snapshot = coverage._strict_json(proofs[0], name="source-parent proof")  # noqa: SLF001
        match = PROOF_NAME.fullmatch(proofs[0].name)
        if (
            match is None
            or match.group("digest") != snapshot.sha256
            or payload != expected
        ):
            raise SourceDerivedSourceParentTerminalAggregateIntegrityError(
                "Aggregate proof differs from current authority"
            )
        proof = (payload, snapshot)
    event = None
    if events:
        if proof is None:
            raise SourceDerivedSourceParentTerminalAggregateIntegrityError(
                "Aggregate event is orphaned"
            )
        payload, snapshot = coverage._strict_json(events[0], name="source-parent event")  # noqa: SLF001
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
            "source_key_id": expected["source_parent_identity"]["key_id"],
            **_claims(complete=True),
        }
        match = EVENT_NAME.fullmatch(events[0].name)
        if (
            match is None
            or match.group("digest") != entry
            or body != expected_body
            or entry != _sha256(_canonical_bytes({**body, "recorded_at_utc": recorded}))
        ):
            raise SourceDerivedSourceParentTerminalAggregateIntegrityError(
                "Aggregate event changed"
            )
        coverage._parse_utc(recorded, name="source-parent event time")  # noqa: SLF001
        event = (payload, snapshot)
    return proof, event


def _append_event(
    profile: Mapping[str, Any],
    paths: SourceDerivedSourceParentTerminalAggregatePaths,
    proof: registry.ArtifactSnapshot,
    expected: Mapping[str, Any],
    now: datetime,
):
    body = {
        "schema_version": profile["protocol"]["event_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "sequence_id": 1,
        "previous_entry_sha256": ZERO_HASH,
        "event_type": profile["protocol"]["event_type"],
        "recorded_at_utc": coverage._utc_text(now),  # noqa: SLF001
        "proof": _reference(proof, paths.root),
        "source_key_id": expected["source_parent_identity"]["key_id"],
        **_claims(complete=True),
    }
    payload = {**body, "entry_sha256": _sha256(_canonical_bytes(body))}
    return coverage._publish(
        paths.events / f"{1:020d}-{payload['entry_sha256']}.json",
        payload,
        root=paths.root,
        name="source-parent event",
    )  # noqa: SLF001


def _write_status(
    profile: Mapping[str, Any],
    paths: SourceDerivedSourceParentTerminalAggregatePaths,
    now: datetime,
    status: str,
    reason: str,
    source_key_id: str | None,
    proof: Path | None,
    event: Path | None,
) -> None:
    payload = {
        "schema_version": profile["protocol"]["status_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "observed_at_utc": coverage._utc_text(now),  # noqa: SLF001
        "status": status,
        "reason": reason,
        "source_key_id": source_key_id,
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
            name="source-parent status",
        )
    except registry.EpochRegistryError as exc:
        raise SourceDerivedSourceParentTerminalAggregateIntegrityError(
            str(exc)
        ) from exc


def _result(
    paths: SourceDerivedSourceParentTerminalAggregatePaths,
    status: str,
    reason: str,
    source_key_id: str | None = None,
    proof: Path | None = None,
    event: Path | None = None,
):
    return SourceDerivedSourceParentTerminalAggregateResult(
        status=status,
        reason=reason,
        status_path=paths.status,
        source_key_id=source_key_id,
        proof_path=proof,
        event_path=event,
        current_source_ingest_parent_terminal=event is not None,
    )


def _coordinate_source_derived_source_parent_terminal_aggregate(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    registry_root: Path | None = None,
    active_root: Path | None = None,
    shadow_root: Path | None = None,
    clock: Callable[[], datetime] | None = None,
    fault_hook: FaultHook | None = None,
    load_derived_authority: Any = None,
) -> SourceDerivedSourceParentTerminalAggregateResult:
    profile = load_source_derived_source_parent_terminal_aggregate_profile(config_path)
    paths = source_derived_source_parent_terminal_aggregate_paths(
        profile,
        registry_root=registry_root,
        active_root=active_root,
        shadow_root=shadow_root,
    )
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    handles: list[BinaryIO] = []
    try:
        handles = coverage._acquire_locks(paths.coverage)  # noqa: SLF001
        computation, overlay_event, assessment, waiting = coverage._load_assessment(
            paths.coverage, now, load_derived_authority=load_derived_authority
        )  # noqa: SLF001
        if computation is None or overlay_event is None or assessment is None:
            if coverage._strict_entries(
                paths.proofs, name="source-parent proofs"
            ) or coverage._strict_entries(paths.events, name="source-parent events"):  # noqa: SLF001
                raise SourceDerivedSourceParentTerminalAggregateIntegrityError(
                    "Aggregate bytes outlived current overlay authority"
                )
            _write_status(
                profile,
                paths,
                now,
                "waiting_for_current_overlay",
                waiting,
                None,
                None,
                None,
            )
            return _result(paths, "waiting_for_current_overlay", waiting)
        coverage_expected = coverage._proof_payload(
            coverage.load_source_derived_effective_outcome_terminal_coverage_profile(),
            paths.coverage,
            computation,
            overlay_event,
            assessment,
        )  # noqa: SLF001
        coverage_proof, coverage_event = coverage._load_state(
            coverage.load_source_derived_effective_outcome_terminal_coverage_profile(),
            paths.coverage,
            coverage_expected,
        )  # noqa: SLF001
        if (
            assessment.missing_key_ids
            or not assessment.upstream_frontiers_complete
            or coverage_proof is None
            or coverage_event is None
        ):
            if coverage._strict_entries(
                paths.proofs, name="source-parent proofs"
            ) or coverage._strict_entries(paths.events, name="source-parent events"):  # noqa: SLF001
                raise SourceDerivedSourceParentTerminalAggregateIntegrityError(
                    "Aggregate bytes exist without matching published D/R coverage"
                )
            reason = "matching current D/R terminal coverage proof/event is pending"
            _write_status(
                profile,
                paths,
                now,
                "waiting_for_dri_terminal_coverage",
                reason,
                None,
                None,
                None,
            )
            return _result(paths, "waiting_for_dri_terminal_coverage", reason)
        expected = _proof_payload(
            profile,
            paths,
            computation,
            overlay_event,
            coverage_proof[1],
            coverage_event[1],
            coverage_expected,
        )
        source_key_id = str(expected["source_parent_identity"]["key_id"])
        proof, event = _load_state(profile, paths, expected)
        if event is not None:
            reason = "exact current source-ingest parent terminal aggregate is current"
            _write_status(
                profile,
                paths,
                now,
                "current_source_ingest_parent_terminal_current",
                reason,
                source_key_id,
                proof[1].path,
                event[1].path,
            )
            return _result(
                paths,
                "current_source_ingest_parent_terminal_current",
                reason,
                source_key_id,
                proof[1].path,
                event[1].path,
            )
        if proof is None:
            digest = _sha256(_canonical_bytes(expected))
            proof_snapshot = coverage._publish(
                paths.proofs / f"{digest}.json",
                expected,
                root=paths.root,
                name="source-parent proof",
            )  # noqa: SLF001
            if fault_hook:
                fault_hook("after_proof")
        else:
            proof_snapshot = proof[1]
        event_snapshot = _append_event(profile, paths, proof_snapshot, expected, now)
        reason = "deterministic source-parent proof was published or forward-adopted"
        _write_status(
            profile,
            paths,
            now,
            "current_source_ingest_parent_terminal_published",
            reason,
            source_key_id,
            proof_snapshot.path,
            event_snapshot.path,
        )
        return _result(
            paths,
            "current_source_ingest_parent_terminal_published",
            reason,
            source_key_id,
            proof_snapshot.path,
            event_snapshot.path,
        )
    except coverage.SourceDerivedEffectiveOutcomeTerminalCoverageBusyError as exc:
        raise SourceDerivedSourceParentTerminalAggregateBusyError(str(exc)) from exc
    except coverage.SourceDerivedEffectiveOutcomeTerminalCoverageError as exc:
        raise SourceDerivedSourceParentTerminalAggregateIntegrityError(
            str(exc)
        ) from exc
    except SourceDerivedSourceParentTerminalAggregateError:
        raise
    except Exception as exc:
        raise SourceDerivedSourceParentTerminalAggregateIntegrityError(
            str(exc)
        ) from exc
    finally:
        if handles:
            coverage._release_locks(handles)  # noqa: SLF001


def coordinate_source_derived_source_parent_terminal_aggregate(
    **kwargs: Any,
) -> SourceDerivedSourceParentTerminalAggregateResult:
    return _coordinate_source_derived_source_parent_terminal_aggregate(**kwargs)
