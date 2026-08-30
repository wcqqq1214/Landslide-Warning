"""Publish the scoped V2 completion of one reserved official-machine workset.

This is deliberately not a global old-epoch drain or activation authority.  It
joins the existing source-derived bounded closure with a fresh empty six-family
inspection under the four surviving V2 locks, then publishes one immutable
singleton event for that exact cut.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
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
from monitoring import ootang_epoch_admission_cut as admission_cut  # noqa: E402
from monitoring import ootang_epoch_drain_v2 as drain_v2  # noqa: E402
from monitoring import ootang_epoch_registry as registry  # noqa: E402
from monitoring import (  # noqa: E402
    ootang_epoch_source_derived_bounded_terminal_closure as closure,
)
from monitoring import ootang_epoch_workset_manifest as manifest  # noqa: E402


NAMESPACE = "bounded_drain_completion_v1"
ZERO_HASH = "0" * 64
EVENT_SCHEMA_VERSION = "ootang_epoch_bounded_drain_completion_event_v1"
STATUS_SCHEMA_VERSION = "ootang_epoch_bounded_drain_completion_status_v1"
EVENT_TYPE = "epoch_bounded_official_workset_drained"
EVENT_NAME = re.compile(r"^00000000000000000001-(?P<digest>[0-9a-f]{64})\.json$")


class BoundedDrainCompletionError(RuntimeError):
    """Base scoped V2 drain-completion failure."""


class BoundedDrainCompletionConfigError(BoundedDrainCompletionError):
    """The existing reviewed V2 path contracts no longer agree."""


class BoundedDrainCompletionIntegrityError(BoundedDrainCompletionError):
    """An upstream cut, fresh capture, or singleton event failed closed."""


class BoundedDrainCompletionBusyError(BoundedDrainCompletionError):
    """One of the four surviving machine coordinator locks is busy."""


@dataclass(frozen=True)
class BoundedDrainCompletionPaths:
    registry_root: Path
    root: Path
    events: Path
    status: Path
    closure: closure.SourceDerivedBoundedTerminalClosurePaths


@dataclass(frozen=True)
class BoundedDrainCompletionResult:
    status: str
    reason: str
    status_path: Path
    event_path: Path | None = None
    bounded_official_workset_drained: bool = False


@dataclass(frozen=True)
class _ValidatedCut:
    context: drain_v2.WorksetContext
    manifest_paths: manifest.WorksetManifestPaths
    binding: manifest.AdmissionCutBinding
    closure_proof: registry.ArtifactSnapshot
    closure_event: registry.ArtifactSnapshot


LoadClosure = Callable[[BoundedDrainCompletionPaths, datetime], _ValidatedCut | None]
LoadCurrentContext = Callable[
    [BoundedDrainCompletionPaths, _ValidatedCut, datetime], drain_v2.WorksetContext
]
InspectFreshWorkset = Callable[
    [manifest.WorksetManifestPaths, manifest.AdmissionCutBinding, datetime],
    manifest.WorksetInspection,
]


def _canonical_bytes(value: object) -> bytes:
    return registry._canonical_bytes(value)  # type: ignore[arg-type]  # noqa: SLF001


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise BoundedDrainCompletionIntegrityError(
            "Bounded drain completion clock must be timezone-aware"
        )
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def bounded_drain_completion_paths(
    *,
    runtime_root: Path | None = None,
    active_runtime_root: Path | None = None,
    shadow_runtime_root: Path | None = None,
) -> BoundedDrainCompletionPaths:
    """Resolve this cache/event namespace from the existing reviewed profiles."""

    try:
        closure_profile = closure.load_source_derived_bounded_terminal_closure_profile()
        closure_paths = closure.source_derived_bounded_terminal_closure_paths(
            closure_profile,
            registry_root=runtime_root,
            active_root=active_runtime_root,
            shadow_root=shadow_runtime_root,
        )
        root = registry._contained(  # noqa: SLF001
            closure_paths.root.parent, NAMESPACE, name="bounded drain completion root"
        )
        events = registry._contained(  # noqa: SLF001
            root, "events", name="bounded drain completion events"
        )
        status = registry._contained(  # noqa: SLF001
            root, "status.json", name="bounded drain completion status"
        )
    except (
        closure.SourceDerivedBoundedTerminalClosureError,
        registry.EpochRegistryError,
    ) as exc:
        raise BoundedDrainCompletionConfigError(str(exc)) from exc
    return BoundedDrainCompletionPaths(
        closure_paths.registry_root,
        root,
        events,
        status,
        closure_paths,
    )


def _reference(
    snapshot: registry.ArtifactSnapshot, registry_root: Path
) -> dict[str, object]:
    try:
        relative = snapshot.path.relative_to(registry_root).as_posix()
    except ValueError as exc:
        raise BoundedDrainCompletionIntegrityError(
            "Upstream closure reference escaped the registry root"
        ) from exc
    return {
        "path": relative,
        "sha256": snapshot.sha256,
        "size_bytes": snapshot.size_bytes,
    }


def _event_entries(paths: BoundedDrainCompletionPaths) -> tuple[Path, ...]:
    entries = closure._strict_entries(  # noqa: SLF001
        paths.events, name="bounded drain completion events"
    )
    if len(entries) > 1:
        raise BoundedDrainCompletionIntegrityError(
            "Bounded drain completion singleton namespace branched"
        )
    return entries


def _default_closure_loader(
    paths: BoundedDrainCompletionPaths, now: datetime
) -> _ValidatedCut | None:
    """Deep-replay the closure without invoking its lock-taking coordinator."""

    profile = closure.load_source_derived_bounded_terminal_closure_profile()
    inputs, _, _ = closure._load_inputs(  # noqa: SLF001
        paths.closure, now, load_derived_authority=None
    )
    if inputs is None:
        if closure._own_children_present(paths.closure):  # noqa: SLF001
            raise BoundedDrainCompletionIntegrityError(
                "Bounded closure authority outlived its exact current cut"
            )
        return None
    assessment = closure._assessment(inputs)  # noqa: SLF001
    expected = closure._proof_payload(  # noqa: SLF001
        profile, paths.closure, inputs, assessment
    )
    proof, event = closure._load_state(  # noqa: SLF001
        profile, paths.closure, expected
    )
    if proof is None or event is None:
        return None
    reservation = inputs.current_inputs.computation.reservation
    return _ValidatedCut(
        context=reservation.binding.context,
        manifest_paths=reservation.paths,
        binding=reservation.binding,
        closure_proof=proof[1],
        closure_event=event[1],
    )


def _default_current_context(
    paths: BoundedDrainCompletionPaths,
    cut: _ValidatedCut,
    now: datetime,
) -> drain_v2.WorksetContext:
    """Read the machine-current live tip without opening a cut writer lock."""

    profile = admission_cut.load_admission_cut_profile()
    cut_paths = admission_cut.admission_cut_paths(
        profile,
        runtime_root=paths.registry_root,
        active_runtime_root=cut.manifest_paths.active_root,
        shadow_runtime_root=cut.manifest_paths.shadow_root,
    )
    binding = admission_cut._load_v2_binding(cut_paths)  # noqa: SLF001
    if binding is None:
        raise BoundedDrainCompletionIntegrityError(
            "Bounded closure lost its V2 first-blocker authority"
        )
    return admission_cut._machine_context(binding, now)  # noqa: SLF001


def _fresh_capture(
    current_context: drain_v2.WorksetContext,
    manifest_paths: manifest.WorksetManifestPaths,
    inspection: manifest.WorksetInspection,
) -> tuple[tuple[dict[str, object], ...], int]:
    profile = manifest.load_workset_manifest_profile()
    items = manifest._normalize_inspection(  # noqa: SLF001
        profile, manifest_paths, inspection
    )
    if inspection.context != current_context:
        raise BoundedDrainCompletionIntegrityError(
            "Fresh workset capture changed the current old-epoch context"
        )
    families = tuple(
        {
            "family": family.family,
            "record_count": family.record_count,
            "actionable_count": family.actionable_count,
            "namespace_digest": family.namespace_digest,
        }
        for family in inspection.families
    )
    return families, len(items)


def _event_static_payload(
    paths: BoundedDrainCompletionPaths,
    cut: _ValidatedCut,
    current_context: drain_v2.WorksetContext,
    families: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    family_rows = [dict(value) for value in families]
    return {
        "schema_version": EVENT_SCHEMA_VERSION,
        "sequence_id": 1,
        "previous_entry_sha256": ZERO_HASH,
        "event_type": EVENT_TYPE,
        "branch": "v2_non_clean_recovery",
        "authority_scope": "official_machine_reserved_workset",
        "candidate_id": cut.context.candidate_id,
        "slot_id": cut.context.slot_id,
        "old_live_epoch_id": cut.context.old_live_epoch_id,
        "frozen_live_event_count": cut.context.live_event_count,
        "frozen_live_terminal_sha256": cut.context.live_terminal_sha256,
        "fresh_live_event_count": current_context.live_event_count,
        "fresh_live_terminal_sha256": current_context.live_terminal_sha256,
        "admission_cut_event": manifest._artifact_payload(  # noqa: SLF001
            cut.binding.event
        ),
        "admission_cut_latest_attempt": manifest._artifact_payload(  # noqa: SLF001
            cut.binding.latest_attempt
        ),
        "source_derived_bounded_terminal_closure_proof": _reference(
            cut.closure_proof, paths.registry_root
        ),
        "source_derived_bounded_terminal_closure_event": _reference(
            cut.closure_event, paths.registry_root
        ),
        "fresh_six_family_capture": family_rows,
        "fresh_actionable_item_count": 0,
        "fresh_context_extends_frozen_cut": True,
        "admission_cut_both_cut_verified": True,
        "bounded_source_derived_terminal_closure_verified": True,
        "machine_only": True,
        "bounded_official_workset_drained": True,
        "old_work_admission_fence_implemented": False,
        "old_epoch_drained": False,
        "canonical_old_issue_route_fence_implemented": False,
        "direct_filesystem_writer_fence_implemented": False,
        "active_epoch_switch_implemented": False,
        "lifecycle_authority": False,
        "transition_authority": False,
    }


def _load_event(
    paths: BoundedDrainCompletionPaths,
    expected_static: Mapping[str, object],
) -> tuple[dict[str, Any], registry.ArtifactSnapshot] | None:
    entries = _event_entries(paths)
    if not entries:
        return None
    payload, snapshot = closure._strict_json(  # noqa: SLF001
        entries[0], name="bounded drain completion event"
    )
    recorded_at = payload.get("recorded_at_utc")
    registry._utc_timestamp(  # noqa: SLF001
        recorded_at, name="bounded drain completion event time"
    )
    entry_sha256 = payload.get("entry_sha256")
    body = dict(payload)
    body.pop("entry_sha256", None)
    body.pop("recorded_at_utc", None)
    match = EVENT_NAME.fullmatch(entries[0].name)
    if (
        body != dict(expected_static)
        or match is None
        or match.group("digest") != entry_sha256
        or entry_sha256
        != _sha256(
            _canonical_bytes({**dict(expected_static), "recorded_at_utc": recorded_at})
        )
    ):
        raise BoundedDrainCompletionIntegrityError(
            "Bounded drain completion event differs from current upstream capture"
        )
    return payload, snapshot


def _publish_event(
    paths: BoundedDrainCompletionPaths,
    expected_static: Mapping[str, object],
    now: datetime,
) -> registry.ArtifactSnapshot:
    body = {**dict(expected_static), "recorded_at_utc": _utc_text(now)}
    payload = {**body, "entry_sha256": _sha256(_canonical_bytes(body))}
    path = paths.events / f"{1:020d}-{payload['entry_sha256']}.json"
    published = drain._publish_once_durable(  # noqa: SLF001
        path,
        _canonical_bytes(payload),
        root=paths.root,
        name="bounded drain completion event",
    )
    replayed = _load_event(paths, expected_static)
    if replayed is None or replayed[1] != published:
        raise BoundedDrainCompletionIntegrityError(
            "Bounded drain completion event did not replay"
        )
    return published


def _finish(
    paths: BoundedDrainCompletionPaths,
    now: datetime,
    status: str,
    reason: str,
    cut: _ValidatedCut | None,
    *,
    event: Path | None = None,
    actionable_item_count: int = 0,
) -> BoundedDrainCompletionResult:
    context = cut.context if cut else None
    payload = {
        "schema_version": STATUS_SCHEMA_VERSION,
        "observed_at_utc": _utc_text(now),
        "status": status,
        "reason": reason,
        "authority_scope": "official_machine_reserved_workset",
        "candidate_id": context.candidate_id if context else None,
        "slot_id": context.slot_id if context else None,
        "old_live_epoch_id": context.old_live_epoch_id if context else None,
        "actionable_item_count": actionable_item_count,
        "event_path": event.relative_to(paths.root).as_posix() if event else None,
        "cache_authority": False,
        "bounded_official_workset_drained": event is not None,
        "old_epoch_drained": False,
        "canonical_old_issue_route_fence_implemented": False,
        "direct_filesystem_writer_fence_implemented": False,
        "active_epoch_switch_implemented": False,
    }
    registry._atomic_cache(  # noqa: SLF001
        paths.status,
        _canonical_bytes(payload),
        root=paths.root,
        name="bounded drain completion status",
    )
    return BoundedDrainCompletionResult(
        status, reason, paths.status, event, event is not None
    )


def _coordinate_epoch_bounded_drain_completion(
    *,
    runtime_root: Path | None = None,
    active_runtime_root: Path | None = None,
    shadow_runtime_root: Path | None = None,
    clock: Callable[[], datetime] | None = None,
    load_closure: LoadClosure | None = None,
    load_current_context: LoadCurrentContext | None = None,
    inspect_fresh_workset: InspectFreshWorkset | None = None,
) -> BoundedDrainCompletionResult:
    """Join the existing V2 authorities under their four surviving locks."""

    paths = bounded_drain_completion_paths(
        runtime_root=runtime_root,
        active_runtime_root=active_runtime_root,
        shadow_runtime_root=shadow_runtime_root,
    )
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    _utc_text(now)
    handles: Sequence[BinaryIO] = ()
    try:
        try:
            handles = closure.current.coverage._acquire_locks(  # noqa: SLF001
                paths.closure.current.coverage
            )
        except closure.current.coverage.SourceDerivedEffectiveOutcomeTerminalCoverageBusyError as exc:
            raise BoundedDrainCompletionBusyError(str(exc)) from exc
        except (
            closure.current.coverage.SourceDerivedEffectiveOutcomeTerminalCoverageError
        ) as exc:
            raise BoundedDrainCompletionIntegrityError(str(exc)) from exc

        own_event_exists = bool(_event_entries(paths))
        cut = (load_closure or _default_closure_loader)(paths, now)
        if cut is None:
            if own_event_exists:
                raise BoundedDrainCompletionIntegrityError(
                    "Scoped completion event outlived its bounded closure"
                )
            status = "waiting_for_bounded_terminal_closure"
            reason = "matching source-derived bounded closure proof/event is pending"
            return _finish(paths, now, status, reason, None)
        if not isinstance(cut, _ValidatedCut) or cut.binding.context != cut.context:
            raise BoundedDrainCompletionIntegrityError(
                "Closure loader changed the admission-cut context"
            )
        current_context = (load_current_context or _default_current_context)(
            paths, cut, now
        )
        try:
            admission_cut._context_successor(  # noqa: SLF001
                cut.context, current_context, name="Fresh bounded-drain context"
            )
        except admission_cut.AdmissionCutError as exc:
            raise BoundedDrainCompletionIntegrityError(str(exc)) from exc
        current_binding = replace(cut.binding, context=current_context)
        inspection = (inspect_fresh_workset or manifest._default_inspection)(  # noqa: SLF001
            cut.manifest_paths, current_binding, now
        )
        families, actionable_count = _fresh_capture(
            current_context, cut.manifest_paths, inspection
        )
        if actionable_count:
            if own_event_exists:
                raise BoundedDrainCompletionIntegrityError(
                    "Scoped completion event coexists with fresh actionable work"
                )
            status = "waiting_for_fresh_workset_drain"
            reason = "fresh six-family capture still contains actionable work"
            return _finish(
                paths,
                now,
                status,
                reason,
                cut,
                actionable_item_count=actionable_count,
            )

        expected = _event_static_payload(paths, cut, current_context, families)
        existing = _load_event(paths, expected)
        if existing is None:
            event = _publish_event(paths, expected, now)
            status = "bounded_official_workset_drained"
            reason = "bounded official-machine reserved workset completed"
        else:
            event = existing[1]
            status = "bounded_official_workset_drain_current"
            reason = "bounded official-machine workset completion event is current"
        return _finish(paths, now, status, reason, cut, event=event.path)
    except BoundedDrainCompletionError:
        raise
    except (
        closure.SourceDerivedBoundedTerminalClosureError,
        admission_cut.AdmissionCutError,
        manifest.WorksetManifestError,
        drain.EpochDrainError,
        registry.EpochRegistryError,
    ) as exc:
        raise BoundedDrainCompletionIntegrityError(str(exc)) from exc
    finally:
        if handles:
            try:
                closure.current.coverage._release_locks(handles)  # noqa: SLF001
            except closure.current.coverage.SourceDerivedEffectiveOutcomeTerminalCoverageError as exc:
                raise BoundedDrainCompletionIntegrityError(str(exc)) from exc


def coordinate_epoch_bounded_drain_completion() -> BoundedDrainCompletionResult:
    """Run one machine-only scoped V2 drain-completion poll."""

    return _coordinate_epoch_bounded_drain_completion()


def main() -> int:
    try:
        result = coordinate_epoch_bounded_drain_completion()
    except BoundedDrainCompletionBusyError as exc:
        print(json.dumps({"status": "busy", "reason": str(exc)}, sort_keys=True))
        return 3
    except BoundedDrainCompletionError as exc:
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
                "bounded_official_workset_drained": result.bounded_official_workset_drained,
                "old_epoch_drained": False,
                "active_epoch_switch_implemented": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
