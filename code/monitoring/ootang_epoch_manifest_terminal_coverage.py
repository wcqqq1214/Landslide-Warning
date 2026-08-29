"""Prove exact terminal coverage of one immutable frozen manifest key set.

The assessor is read-only with respect to every upstream authority. It combines
current recovery-v6 terminal receipt/events with published source-terminal
aggregate events and writes only one deterministic proof plus one singleton
publication event in its own versioned namespace.
"""

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
from monitoring import ootang_epoch_source_terminal_aggregate as aggregate  # noqa: E402
from monitoring import ootang_epoch_workset_manifest as manifest  # noqa: E402
from monitoring import ootang_epoch_workset_recovery as recovery  # noqa: E402


DEFAULT_CONFIG_PATH = (
    ROOT / "config" / "ootang_epoch_manifest_terminal_coverage.v1.json"
)
DEFAULT_CONFIG_SHA256 = (
    "4edfc6a9386452393197af827d325f0f877e60a32b6c7bd5ed9e37af250deecb"
)
IMPLEMENTATION_LOGICAL_PATH = (
    "code/monitoring/ootang_epoch_manifest_terminal_coverage.py"
)
MAX_CONTROL_BYTES = 4 * 1024 * 1024
ZERO_HASH = "0" * 64
LOCK_ORDER = recovery.LOCK_ORDER
PROOF_NAME = re.compile(r"^(?P<digest>[0-9a-f]{64})\.json$")
EVENT_NAME = re.compile(r"^(?P<sequence>[0-9]{20})-(?P<entry>[0-9a-f]{64})\.json$")

EXPECTED_UPSTREAM = {
    "manifest_profile": {
        "path": "config/ootang_epoch_workset_manifest.v1.json",
        "expected_sha256": (
            "857ae1ff031289d51c0a2947beeb2e47ceb9d48a3769db707c8f7f75750d48dc"
        ),
    },
    "manifest_implementation": {
        "path": "code/monitoring/ootang_epoch_workset_manifest.py",
        "expected_sha256": (
            "0ca331c6827cd896b4c3261792eed5f933e104d4c40c4fbf3b7e416e9b1fd424"
        ),
    },
    "recovery_profile": {
        "path": "config/ootang_epoch_workset_recovery.v1.json",
        "expected_sha256": (
            "5c50d168d389c286d0940a00884369ae8f65fc399f8726f0f64300999dd2de01"
        ),
    },
    "recovery_implementation": {
        "path": "code/monitoring/ootang_epoch_workset_recovery.py",
        "expected_sha256": (
            "b9f25133eef9cb94fb255bab588edcd573f0fa792ef82c4ddb9068d0a44a6c51"
        ),
    },
    "step_dependency_profile": {
        "path": "config/ootang_epoch_step_dependency_reservation.v1.json",
        "expected_sha256": (
            "8b10a9642610b76911813d80ee8e31205c0e3c490aa05a13f0ba8287d457670e"
        ),
    },
    "step_dependency_implementation": {
        "path": "code/monitoring/ootang_epoch_step_dependency_reservation.py",
        "expected_sha256": (
            "c385b7c8783d86831561d5c1179b05efc78e3f5ef99a912b44382143625e5190"
        ),
    },
    "overlay_profile": {
        "path": "config/ootang_epoch_step_dependency_overlay.v1.json",
        "expected_sha256": (
            "4da333ef6233059aedb6ff1cd52bb6de31bae18aa14f1e571e97ee3018f52496"
        ),
    },
    "overlay_implementation": {
        "path": "code/monitoring/ootang_epoch_step_dependency_overlay.py",
        "expected_sha256": (
            "53932a5ebd095d98f08fe68aaa3891ba9569b51950da34bfbf662882d97fff53"
        ),
    },
    "source_terminal_profile": {
        "path": "config/ootang_epoch_source_terminal_aggregate.v1.json",
        "expected_sha256": (
            "80779ecdb582d2dde53576668037597ac29bf56f486ac926a9754f103eb6604c"
        ),
    },
    "source_terminal_implementation": {
        "path": "code/monitoring/ootang_epoch_source_terminal_aggregate.py",
        "expected_sha256": (
            "8d7b03a7480f3bf647f75694631031b3d11200462f4a4887979a982ba1e1a296"
        ),
    },
}
EXPECTED_RUNTIME = {
    "registry_root": "runtime/ootang_epoch_registry_v1",
    "recovery_namespace": "workset_recovery_v1",
    "source_terminal_namespace": "source_terminal_aggregate_v1",
    "namespace": "manifest_terminal_coverage_v1",
    "proofs": "proofs",
    "events": "events",
    "status": "status.json",
    "manager_lock": "manager.lock",
    "active_root": "runtime/ootang_prequential_live_v1",
    "shadow_root": "runtime/ootang_prequential_calibration_shadow_v1",
}
EXPECTED_PROTOCOL = {
    "proof_schema_version": "ootang_epoch_manifest_terminal_coverage_proof_v1",
    "event_schema_version": "ootang_epoch_manifest_terminal_coverage_event_v1",
    "status_schema_version": "ootang_epoch_manifest_terminal_coverage_status_v1",
    "event_type": "epoch_manifest_terminal_coverage_proved",
    "canonical_json": "utf8_sort_keys_compact_no_nan_trailing_lf",
    "initial_previous_entry_sha256": ZERO_HASH,
    "maximum_items": 4096,
    "maximum_control_bytes": MAX_CONTROL_BYTES,
    "publication_policy": "deterministic_proof_then_singleton_event",
    "coverage_policy": (
        "manifest_keyset_equals_disjoint_union_of_v6_terminal_and_"
        "published_source_aggregate_keys"
    ),
}
TRUE_CAPABILITIES = (
    "machine_only",
    "complete_upstream_authority_deep_verified",
    "exact_frozen_manifest_key_bijection_implemented",
    "ordinary_v6_terminal_evidence_implemented",
    "published_source_terminal_evidence_implemented",
    "content_addressed_manifest_coverage_proof_implemented",
    "singleton_manifest_coverage_event_implemented",
    "proof_event_forward_adoption_implemented",
    "frozen_manifest_key_coverage_implemented",
)
FALSE_CLAIMS = (
    "bounded_workset_recovery_implemented",
    "full_workset_terminal",
    "all_reserved_items_settled",
    "all_reserved_successors_supported",
    "terminal_transition_closure_enumerated",
    "terminal_transition_closure_implemented",
    "transitive_terminal_closure_implemented",
    "derived_future_work_reservation_implemented",
    "all_transition_branches_supported",
    "network_recovery_implemented",
    "manifest_namespace_mutated",
    "recovery_v6_namespace_mutated",
    "step_dependency_sidecar_mutated",
    "settlement_overlay_mutated",
    "source_terminal_aggregate_mutated",
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


class ManifestTerminalCoverageError(RuntimeError):
    """Base frozen-manifest terminal coverage error."""


class ManifestTerminalCoverageConfigError(ManifestTerminalCoverageError):
    """The reviewed profile or one directly pinned upstream changed."""


class ManifestTerminalCoverageIntegrityError(ManifestTerminalCoverageError):
    """Persisted coverage or upstream authority failed closed."""


class ManifestTerminalCoverageBusyError(ManifestTerminalCoverageError):
    """A surviving coordinator writer owns one of the four locks."""


@dataclass(frozen=True)
class ManifestTerminalCoveragePaths:
    registry_root: Path
    root: Path
    proofs: Path
    events: Path
    status: Path
    manager_lock: Path
    active_root: Path
    shadow_root: Path
    cycle_lock: Path
    replay_lock: Path
    shadow_lock: Path
    aggregate: aggregate.SourceTerminalAggregatePaths


@dataclass(frozen=True)
class ManifestTerminalCoverageResult:
    status: str
    reason: str
    status_path: Path
    proof_path: Path | None = None
    event_path: Path | None = None
    frozen_manifest_key_coverage: bool = False
    manifest_key_count: int = 0
    covered_key_count: int = 0
    missing_key_ids: tuple[str, ...] = ()
    full_workset_terminal: bool = False
    terminal_transition_closure_implemented: bool = False
    derived_future_work_reservation_implemented: bool = False
    lifecycle_authority: bool = False
    old_epoch_drained: bool = False


@dataclass(frozen=True)
class ManifestTerminalCoverageAuthority:
    terminal_authority: aggregate.SourceTerminalAggregateAuthority
    aggregate_state: Any


@dataclass(frozen=True)
class _CoverageAssessment:
    rows: tuple[dict[str, Any], ...]
    manifest_key_ids: tuple[str, ...]
    missing_key_ids: tuple[str, ...]
    ordinary_count: int
    aggregate_count: int
    upstream_publications_complete: bool
    global_identity: Mapping[str, Any]

    @property
    def complete(self) -> bool:
        return not self.missing_key_ids and len(self.rows) == len(self.manifest_key_ids)


@dataclass(frozen=True)
class _CoverageState:
    proof: tuple[dict[str, Any], registry.ArtifactSnapshot] | None
    event: tuple[dict[str, Any], registry.ArtifactSnapshot] | None


LoadAuthority = Callable[
    [ManifestTerminalCoveragePaths, datetime],
    ManifestTerminalCoverageAuthority | None,
]


def _canonical_bytes(value: object) -> bytes:
    try:
        return registry._canonical_bytes(value)  # type: ignore[arg-type]  # noqa: SLF001
    except (TypeError, ValueError) as exc:
        raise ManifestTerminalCoverageIntegrityError(
            "Value is not canonical JSON"
        ) from exc


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _claims() -> dict[str, bool]:
    return {
        **{name: True for name in TRUE_CAPABILITIES},
        **{name: False for name in FALSE_CLAIMS},
    }


def _exact(value: object, keys: set[str], *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ManifestTerminalCoverageIntegrityError(f"{name} keys changed")
    return value


def _text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ManifestTerminalCoverageIntegrityError(f"{name} changed")
    return value


def _hash(value: object, *, name: str) -> str:
    text = _text(value, name=name)
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise ManifestTerminalCoverageIntegrityError(
            f"{name} is not a lowercase SHA-256"
        )
    return text


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ManifestTerminalCoverageIntegrityError(
            "Coverage clock must be timezone-aware"
        )
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: object, *, name: str) -> datetime:
    try:
        return registry._utc_timestamp(value, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise ManifestTerminalCoverageIntegrityError(str(exc)) from exc


def _child(root: Path, relative: str, *, name: str) -> Path:
    try:
        return registry._contained(root, relative, name=name)  # noqa: SLF001
    except registry.EpochRegistryError as exc:
        raise ManifestTerminalCoverageConfigError(str(exc)) from exc


def _strict_json(
    path: Path, *, name: str
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    try:
        return manifest._read_json(  # noqa: SLF001
            path, name=name, maximum_bytes=MAX_CONTROL_BYTES
        )
    except manifest.WorksetManifestError as exc:
        raise ManifestTerminalCoverageIntegrityError(str(exc)) from exc


def _strict_entries(directory: Path, *, name: str) -> tuple[Path, ...]:
    try:
        return manifest._strict_entries(directory, name=name)  # noqa: SLF001
    except manifest.WorksetManifestError as exc:
        raise ManifestTerminalCoverageIntegrityError(str(exc)) from exc


def _publish(
    path: Path, raw: bytes, *, root: Path, name: str
) -> registry.ArtifactSnapshot:
    try:
        return drain._publish_once_durable(path, raw, root=root, name=name)  # noqa: SLF001
    except drain.EpochDrainError as exc:
        raise ManifestTerminalCoverageIntegrityError(str(exc)) from exc


def _reference(snapshot: registry.ArtifactSnapshot, root: Path) -> dict[str, object]:
    try:
        relative = snapshot.path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ManifestTerminalCoverageIntegrityError(
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
    payload, checked = _strict_json(snapshot.path, name=name)
    if checked != snapshot:
        raise ManifestTerminalCoverageIntegrityError(
            f"{name} snapshot differs from its durable bytes"
        )
    return payload


def _implementation_reference() -> dict[str, object]:
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            Path(__file__),
            name="manifest terminal coverage implementation",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
    except registry.EpochRegistryError as exc:
        raise ManifestTerminalCoverageIntegrityError(str(exc)) from exc
    return {
        "path": IMPLEMENTATION_LOGICAL_PATH,
        "sha256": snapshot.sha256,
        "size_bytes": snapshot.size_bytes,
    }


def load_manifest_coverage_profile(
    path: Path = DEFAULT_CONFIG_PATH, *, project_root: Path = ROOT
) -> dict[str, Any]:
    root = project_root.resolve()
    resolved = path if path.is_absolute() else root / path
    if root != ROOT.resolve() or resolved.resolve() != DEFAULT_CONFIG_PATH.resolve():
        raise ManifestTerminalCoverageConfigError(
            "Only the reviewed default manifest-coverage profile is accepted"
        )
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            resolved,
            name="manifest terminal coverage profile",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
        profile = registry._decode_json(  # noqa: SLF001
            snapshot.raw, name="manifest terminal coverage profile"
        )
    except registry.EpochRegistryError as exc:
        raise ManifestTerminalCoverageConfigError(str(exc)) from exc
    if snapshot.sha256 != DEFAULT_CONFIG_SHA256:
        raise ManifestTerminalCoverageConfigError(
            "Manifest terminal coverage profile SHA-256 changed"
        )
    try:
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
            name="manifest terminal coverage profile",
        )
    except ManifestTerminalCoverageIntegrityError as exc:
        raise ManifestTerminalCoverageConfigError(str(exc)) from exc
    if (
        profile["schema_version"]
        != "ootang_epoch_manifest_terminal_coverage_profile_v1"
        or profile["profile_id"] != "ootang-epoch-manifest-terminal-coverage-v1"
        or profile["profile_version"] != "1.0.0-exact-frozen-key-bijection"
        or profile["case"] != "ootang"
        or profile["formal_warning_output"] is not False
        or profile["default_pipeline_member"] is not False
        or profile["upstream"] != EXPECTED_UPSTREAM
        or profile["runtime"] != EXPECTED_RUNTIME
        or profile["protocol"] != EXPECTED_PROTOCOL
        or profile["engineering_capabilities"] != _claims()
    ):
        raise ManifestTerminalCoverageConfigError(
            "Manifest terminal coverage profile semantics changed"
        )
    for binding in EXPECTED_UPSTREAM.values():
        upstream = _child(root, binding["path"], name="frozen coverage upstream")
        try:
            checked = registry._read_regular(  # noqa: SLF001
                upstream,
                name="frozen coverage upstream",
                maximum_bytes=64 * 1024 * 1024,
            )
        except registry.EpochRegistryError as exc:
            raise ManifestTerminalCoverageConfigError(str(exc)) from exc
        if checked.sha256 != binding["expected_sha256"]:
            raise ManifestTerminalCoverageConfigError(
                f"Frozen coverage upstream changed:{binding['path']}"
            )
    profile["_profile_sha256"] = snapshot.sha256
    return profile


def manifest_coverage_paths(
    profile: Mapping[str, Any],
    *,
    registry_root: Path | None = None,
    active_root: Path | None = None,
    shadow_root: Path | None = None,
) -> ManifestTerminalCoveragePaths:
    registry_path = (
        registry_root or ROOT / profile["runtime"]["registry_root"]
    ).resolve()
    active = (active_root or ROOT / profile["runtime"]["active_root"]).resolve()
    shadow = (shadow_root or ROOT / profile["runtime"]["shadow_root"]).resolve()
    aggregate_profile = aggregate.load_source_terminal_profile()
    aggregate_paths = aggregate.source_terminal_paths(
        aggregate_profile,
        registry_root=registry_path,
        active_root=active,
        shadow_root=shadow,
    )
    recovery_root = aggregate_paths.overlay.recovery.root
    if (
        recovery_root.name != profile["runtime"]["recovery_namespace"]
        or aggregate_paths.root.name != profile["runtime"]["source_terminal_namespace"]
    ):
        raise ManifestTerminalCoverageConfigError(
            "Manifest coverage upstream namespaces changed"
        )
    root = _child(
        recovery_root,
        profile["runtime"]["namespace"],
        name="manifest terminal coverage root",
    )
    return ManifestTerminalCoveragePaths(
        registry_root=registry_path,
        root=root,
        proofs=_child(root, profile["runtime"]["proofs"], name="coverage proofs"),
        events=_child(root, profile["runtime"]["events"], name="coverage events"),
        status=_child(root, profile["runtime"]["status"], name="coverage status"),
        manager_lock=aggregate_paths.manager_lock,
        active_root=active,
        shadow_root=shadow,
        cycle_lock=aggregate_paths.cycle_lock,
        replay_lock=aggregate_paths.replay_lock,
        shadow_lock=aggregate_paths.shadow_lock,
        aggregate=aggregate_paths,
    )


def _load_coverage_authority(
    paths: ManifestTerminalCoveragePaths, now: datetime
) -> ManifestTerminalCoverageAuthority | None:
    try:
        terminal = aggregate._load_terminal_authority(  # noqa: SLF001
            paths.aggregate, now
        )
        if terminal is None:
            return None
        aggregate_profile = aggregate.load_source_terminal_profile()
        state = aggregate._load_state(  # noqa: SLF001
            aggregate_profile, paths.aggregate, terminal
        )
    except aggregate.SourceTerminalAggregateError as exc:
        raise ManifestTerminalCoverageIntegrityError(
            f"Source-terminal aggregate validation failed:{type(exc).__name__}:{exc}"
        ) from exc
    return ManifestTerminalCoverageAuthority(terminal, state)


def _event_snapshot(
    paths: ManifestTerminalCoveragePaths,
    event: Mapping[str, Any],
    *,
    sequence: int,
) -> registry.ArtifactSnapshot:
    if event.get("sequence_id") != sequence:
        raise ManifestTerminalCoverageIntegrityError(
            "Source-terminal aggregate event prefix changed sequence"
        )
    entry = _hash(event.get("entry_sha256"), name="aggregate event entry")
    path = paths.aggregate.events / f"{sequence:020d}-{entry}.json"
    checked, snapshot = _strict_json(path, name="published source-terminal event")
    if checked != event:
        raise ManifestTerminalCoverageIntegrityError(
            "Published source-terminal event changed during coverage replay"
        )
    return snapshot


def _transition_plans(
    ordered_items: Sequence[Mapping[str, Any]],
) -> tuple[tuple[dict[str, object], ...], str, str]:
    try:
        plans = tuple(recovery._transition_plan(item) for item in ordered_items)  # noqa: SLF001
    except recovery.WorksetRecoveryError as exc:
        raise ManifestTerminalCoverageIntegrityError(
            f"Manifest transition plan replay failed:{exc}"
        ) from exc
    by_natural = {
        _text(item.get("natural_key"), name="manifest natural key"): _hash(
            item.get("key_id"), name="manifest key id"
        )
        for item in ordered_items
    }
    graph = []
    for item in ordered_items:
        dependencies = item.get("dependency_keys")
        if not isinstance(dependencies, list) or any(
            not isinstance(value, str) or value not in by_natural
            for value in dependencies
        ):
            raise ManifestTerminalCoverageIntegrityError(
                "Manifest dependency graph changed"
            )
        graph.append(
            {
                "key_id": item["key_id"],
                "natural_key": item["natural_key"],
                "dependency_key_ids": [by_natural[value] for value in dependencies],
                "successor": item.get("canonical_successor_state"),
            }
        )
    return (
        plans,
        _sha256(_canonical_bytes(list(plans))),
        _sha256(_canonical_bytes(graph)),
    )


def _ordered_durable_manifest_items(
    reservation: recovery.Reservation, *, maximum_items: int
) -> tuple[dict[str, Any], ...]:
    raw_items = reservation.manifest.get("items")
    if not isinstance(raw_items, list) or not 0 < len(raw_items) <= maximum_items:
        raise ManifestTerminalCoverageIntegrityError(
            "Durable manifest item set is outside the reviewed bound"
        )
    by_natural: dict[str, dict[str, Any]] = {}
    positions: dict[str, int] = {}
    for position, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise ManifestTerminalCoverageIntegrityError(
                "Durable manifest item changed type"
            )
        item = dict(raw)
        natural = _text(item.get("natural_key"), name="durable manifest natural key")
        dependencies = item.get("dependency_keys")
        if natural in by_natural or not isinstance(dependencies, list):
            raise ManifestTerminalCoverageIntegrityError(
                "Durable manifest key/dependency identity changed"
            )
        embedded_key = item.get("key_id")
        if embedded_key is None:
            item["key_id"] = recovery._key_id(  # noqa: SLF001
                reservation.manifest_snapshot.sha256, item
            )
        else:
            item["key_id"] = _hash(
                embedded_key, name="durable synthetic manifest key id"
            )
        by_natural[natural] = item
        positions[natural] = position
    for item in by_natural.values():
        dependencies = item["dependency_keys"]
        if len(set(dependencies)) != len(dependencies) or any(
            not isinstance(value, str) or value not in by_natural
            for value in dependencies
        ):
            raise ManifestTerminalCoverageIntegrityError(
                "Durable manifest dependency graph changed"
            )
    emitted: set[str] = set()
    remaining = dict(by_natural)
    ordered: list[dict[str, Any]] = []
    while remaining:
        ready = sorted(
            (
                item
                for item in remaining.values()
                if set(item["dependency_keys"]) <= emitted
            ),
            key=lambda item: positions[str(item["natural_key"])],
        )
        if not ready:
            raise ManifestTerminalCoverageIntegrityError(
                "Durable manifest dependency graph is cyclic"
            )
        for item in ready:
            natural = str(item["natural_key"])
            ordered.append(item)
            emitted.add(natural)
            remaining.pop(natural)
    return tuple(ordered)


def _coverage_assessment(
    profile: Mapping[str, Any],
    paths: ManifestTerminalCoveragePaths,
    authority: ManifestTerminalCoverageAuthority,
) -> _CoverageAssessment:
    terminal = authority.terminal_authority
    state = authority.aggregate_state
    try:
        replayed_aggregate_state = aggregate._load_state(  # noqa: SLF001
            aggregate.load_source_terminal_profile(), paths.aggregate, terminal
        )
    except aggregate.SourceTerminalAggregateError as exc:
        raise ManifestTerminalCoverageIntegrityError(
            "Source-terminal aggregate authority state replay failed:"
            f"{type(exc).__name__}:{exc}"
        ) from exc
    if state != replayed_aggregate_state:
        raise ManifestTerminalCoverageIntegrityError(
            "Injected aggregate state differs from its durable proof/event authority"
        )
    recovered = terminal.overlay_authority.recovery
    reservation = recovered.reservation
    if (
        _replay_snapshot(
            reservation.manifest_snapshot, name="frozen manifest authority"
        )
        != reservation.manifest
        or _replay_snapshot(
            reservation.event_snapshot, name="manifest reservation event authority"
        )
        != reservation.event
        or _replay_snapshot(
            recovered.global_snapshot, name="recovery global intent authority"
        )
        != recovered.global_intent
    ):
        raise ManifestTerminalCoverageIntegrityError(
            "Manifest or recovery root authority differs from durable bytes"
        )
    ordered_items = tuple(recovered.ordered_items)
    durable_ordered_items = _ordered_durable_manifest_items(
        reservation,
        maximum_items=profile["protocol"]["maximum_items"],
    )
    if ordered_items != durable_ordered_items:
        raise ManifestTerminalCoverageIntegrityError(
            "Recovered ordered items differ from the durable manifest key set"
        )
    if not 0 < len(ordered_items) <= profile["protocol"]["maximum_items"]:
        raise ManifestTerminalCoverageIntegrityError(
            "Frozen manifest key count is outside the reviewed bound"
        )
    key_ids = tuple(
        _hash(item.get("key_id"), name="manifest key id") for item in ordered_items
    )
    natural_keys = tuple(
        _text(item.get("natural_key"), name="manifest natural key")
        for item in ordered_items
    )
    if len(set(key_ids)) != len(key_ids) or len(set(natural_keys)) != len(natural_keys):
        raise ManifestTerminalCoverageIntegrityError(
            "Frozen manifest key identity is not unique"
        )
    manifest_key_set = set(key_ids)
    plans, plans_digest, graph_digest = _transition_plans(ordered_items)
    recovery_root = paths.aggregate.overlay.recovery.root
    global_checks = {
        "ordered_key_ids": list(key_ids),
        "dependency_graph_sha256": graph_digest,
        "transition_plans_sha256": plans_digest,
        "transition_contract_sha256": recovery.TRANSITION_CONTRACT_SHA256,
    }
    for key, expected in global_checks.items():
        if key in recovered.global_intent and recovered.global_intent[key] != expected:
            raise ManifestTerminalCoverageIntegrityError(
                f"Recovery global intent {key} differs from durable manifest replay"
            )

    ordinary: dict[str, dict[str, Any]] = {}
    for position, (item, plan) in enumerate(zip(ordered_items, plans, strict=True)):
        key_id = key_ids[position]
        rows = recovered.chains.get(key_id, ())
        if not rows:
            continue
        receipt, receipt_snapshot = rows[-1]
        if (
            _replay_snapshot(
                receipt_snapshot, name="current recovery receipt authority"
            )
            != receipt
            or receipt.get("key_id") != key_id
        ):
            raise ManifestTerminalCoverageIntegrityError(
                "Current recovery receipt tip changed durable identity"
            )
        if receipt.get("terminal_for_key") is not True:
            continue
        step_id = _hash(receipt.get("step_id"), name="terminal recovery step id")
        event_row = recovered.events_by_step.get(step_id)
        if event_row is None:
            raise ManifestTerminalCoverageIntegrityError(
                "Current terminal recovery receipt lacks its published event"
            )
        event, event_snapshot = event_row
        action = receipt.get("action")
        try:
            plan_edges = recovery._plan_edges(plan)  # noqa: SLF001
        except recovery.WorksetRecoveryError as exc:
            raise ManifestTerminalCoverageIntegrityError(
                f"Terminal recovery plan no longer replays:{exc}"
            ) from exc
        if (
            _replay_snapshot(event_snapshot, name="current recovery event authority")
            != event
            or event.get("step_id") != step_id
            or event.get("key_id") != key_id
            or plan.get("closure_resolved") is not True
            or action not in plan.get("terminal_actions", [])
            or not isinstance(action, str)
            or receipt.get("next_actions") != plan_edges.get(action)
            or not isinstance(receipt.get("step_index"), int)
            or isinstance(receipt.get("step_index"), bool)
            or (
                "transition_plan_sha256" in receipt
                and receipt.get("transition_plan_sha256") != plan.get("plan_sha256")
            )
            or (
                "receipt" in event
                and event.get("receipt") != _reference(receipt_snapshot, recovery_root)
            )
        ):
            raise ManifestTerminalCoverageIntegrityError(
                "Recovery event does not match one current terminal plan tip"
            )
        ordinary[key_id] = {
            "manifest_position": position,
            "key_id": key_id,
            "natural_key": natural_keys[position],
            "namespace_digest": _hash(
                item.get("namespace_digest"), name="manifest namespace digest"
            ),
            "family": _text(item.get("family"), name="manifest family"),
            "transition_plan_sha256": plan["plan_sha256"],
            "terminal_action": receipt.get("action"),
            "terminal_step_id": step_id,
            "terminal_step_index": receipt.get("step_index"),
            "evidence_kind": "recovery_v6_terminal_receipt",
            "terminal_receipt": _reference(receipt_snapshot, recovery_root),
            "terminal_event": _reference(event_snapshot, recovery_root),
        }

    slots = tuple(terminal.completed_slots)
    slot_sources = tuple(slot.source_key_id for slot in slots)
    if len(slot_sources) != len(set(slot_sources)):
        raise ManifestTerminalCoverageIntegrityError(
            "Source-terminal aggregate authority has duplicate source slots"
        )
    events = tuple(state.events)
    if len(events) > len(slots):
        raise ManifestTerminalCoverageIntegrityError(
            "Source-terminal aggregate events are not a completed-overlay prefix"
        )
    aggregate_rows: dict[str, dict[str, Any]] = {}
    for sequence, event in enumerate(events, 1):
        slot = slots[sequence - 1]
        if (
            event.get("sequence_id") != sequence
            or event.get("slot_id") != slot.slot_id
            or event.get("source_key_id") != slot.source_key_id
        ):
            raise ManifestTerminalCoverageIntegrityError(
                "Source-terminal aggregate event prefix changed"
            )
        key_id = _hash(event.get("source_key_id"), name="aggregate source key id")
        if key_id not in manifest_key_set or key_id in aggregate_rows:
            raise ManifestTerminalCoverageIntegrityError(
                "Source-terminal aggregate evidence is duplicate or outside manifest"
            )
        proof_row = state.proofs.get(slot.slot_id)
        if proof_row is None:
            raise ManifestTerminalCoverageIntegrityError(
                "Published aggregate event lost its terminal proof"
            )
        proof, proof_snapshot, proof_slot = proof_row
        if (
            _replay_snapshot(proof_snapshot, name="source-terminal proof authority")
            != proof
            or proof_slot != slot
            or proof.get("source_key_id") != key_id
            or proof.get("terminal_for_source_key") is not True
            or event.get("proof") != _reference(proof_snapshot, paths.aggregate.root)
        ):
            raise ManifestTerminalCoverageIntegrityError(
                "Published aggregate proof/event binding changed"
            )
        event_snapshot = _event_snapshot(paths, event, sequence=sequence)
        position = key_ids.index(key_id)
        item = ordered_items[position]
        derivation = proof.get("terminal_derivation")
        if not isinstance(derivation, Mapping):
            raise ManifestTerminalCoverageIntegrityError(
                "Aggregate proof lost its terminal derivation"
            )
        source_rows = recovered.chains.get(key_id, ())
        if not source_rows:
            raise ManifestTerminalCoverageIntegrityError(
                "Aggregate-covered source lost its current recovery tip"
            )
        source_receipt, source_receipt_snapshot = source_rows[-1]
        source_step_id = _hash(
            source_receipt.get("step_id"), name="aggregate source recovery step"
        )
        source_event_row = recovered.events_by_step.get(source_step_id)
        recovery_authority = proof.get("recovery_authority")
        if source_event_row is None or not isinstance(recovery_authority, Mapping):
            raise ManifestTerminalCoverageIntegrityError(
                "Aggregate-covered source lost recovery event authority"
            )
        source_event, source_event_snapshot = source_event_row
        if (
            _replay_snapshot(
                source_receipt_snapshot,
                name="aggregate source current recovery receipt",
            )
            != source_receipt
            or _replay_snapshot(
                source_event_snapshot, name="aggregate source current recovery event"
            )
            != source_event
            or source_receipt.get("terminal_for_key") is not False
            or derivation.get("previous_step_id") != source_step_id
            or recovery_authority.get("source_previous_receipt")
            != _reference(source_receipt_snapshot, recovery_root)
            or recovery_authority.get("source_previous_event")
            != _reference(source_event_snapshot, recovery_root)
            or derivation.get("transition_plan_sha256")
            != plans[position].get("plan_sha256")
            or derivation.get("closure_resolved") is not True
            or derivation.get("terminal_action") is not True
            or derivation.get("next_actions") != []
        ):
            raise ManifestTerminalCoverageIntegrityError(
                "Aggregate source is not bound to its current nonterminal v6 tip"
            )
        aggregate_rows[key_id] = {
            "manifest_position": position,
            "key_id": key_id,
            "natural_key": natural_keys[position],
            "namespace_digest": _hash(
                item.get("namespace_digest"), name="manifest namespace digest"
            ),
            "family": _text(item.get("family"), name="manifest family"),
            "transition_plan_sha256": _hash(
                derivation.get("transition_plan_sha256"),
                name="aggregate transition plan",
            ),
            "terminal_action": derivation.get("action"),
            "terminal_step_id": _hash(
                derivation.get("step_id"), name="aggregate terminal step id"
            ),
            "terminal_step_index": derivation.get("step_index"),
            "evidence_kind": "source_terminal_aggregate_event",
            "source_terminal_proof": _reference(proof_snapshot, paths.aggregate.root),
            "source_terminal_event": _reference(event_snapshot, paths.aggregate.root),
        }

    overlap = set(ordinary) & set(aggregate_rows)
    if overlap:
        raise ManifestTerminalCoverageIntegrityError(
            "One manifest key has both ordinary and aggregate terminal evidence"
        )
    evidence = {**ordinary, **aggregate_rows}
    if not set(evidence) <= manifest_key_set:
        raise ManifestTerminalCoverageIntegrityError(
            "Manifest coverage contains unknown terminal evidence"
        )
    rows = tuple(evidence[key_id] for key_id in key_ids if key_id in evidence)
    missing = tuple(key_id for key_id in key_ids if key_id not in evidence)
    pending = state.pending_proof_slot_id
    expected_pending = slots[len(events)].slot_id if len(events) < len(slots) else None
    if pending is not None and pending != expected_pending:
        raise ManifestTerminalCoverageIntegrityError(
            "Source-terminal pending proof skipped its aggregate prefix"
        )
    published_slot_ids = {slots[index].slot_id for index in range(len(events))}
    expected_proof_slot_ids = set(published_slot_ids)
    if pending is not None:
        expected_proof_slot_ids.add(pending)
    if set(state.proofs) != expected_proof_slot_ids:
        raise ManifestTerminalCoverageIntegrityError(
            "Source-terminal proof set differs from its published event boundary"
        )
    upstream_complete = (
        terminal.incomplete_overlay_authority is False
        and pending is None
        and len(events) == len(slots)
    )
    recovery_step_ids = list(recovered.events_by_step)
    global_identity = {
        "manifest": _reference(reservation.manifest_snapshot, reservation.paths.root),
        "manifest_reservation_event": _reference(
            reservation.event_snapshot, reservation.paths.root
        ),
        "recovery_global_intent": _reference(recovered.global_snapshot, recovery_root),
        "workset_keyset_sha256": _hash(
            reservation.manifest.get("workset_keyset_sha256"),
            name="workset keyset digest",
        ),
        "manifest_key_ids": list(key_ids),
        "manifest_key_ids_sha256": _sha256(_canonical_bytes(list(key_ids))),
        "dependency_graph_sha256": graph_digest,
        "transition_plans_sha256": plans_digest,
        "transition_contract_sha256": recovery.TRANSITION_CONTRACT_SHA256,
        "recovery_event_count": len(recovery_step_ids),
        "recovery_event_step_ids_sha256": _sha256(_canonical_bytes(recovery_step_ids)),
        "completed_overlay_event_count": len(slots),
        "source_terminal_aggregate_event_count": len(events),
        "source_terminal_aggregate_tip_sha256": (
            events[-1]["entry_sha256"] if events else ZERO_HASH
        ),
    }
    return _CoverageAssessment(
        rows=rows,
        manifest_key_ids=key_ids,
        missing_key_ids=missing,
        ordinary_count=len(ordinary),
        aggregate_count=len(aggregate_rows),
        upstream_publications_complete=upstream_complete,
        global_identity=global_identity,
    )


def _proof_payload(
    profile: Mapping[str, Any], assessment: _CoverageAssessment
) -> dict[str, object]:
    if not assessment.complete or not assessment.upstream_publications_complete:
        raise ManifestTerminalCoverageIntegrityError(
            "Incomplete authority cannot publish a manifest coverage proof"
        )
    rows = [dict(row) for row in assessment.rows]
    return {
        "schema_version": profile["protocol"]["proof_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "authority_scope": (
            "frozen_manifest_key_coverage_v1_not_terminal_transitive_closure"
        ),
        "publication_condition": "matching_manifest_coverage_event_required",
        "manifest_identity": dict(assessment.global_identity),
        "coverage_rows": rows,
        "coverage_rows_sha256": _sha256(_canonical_bytes(rows)),
        "manifest_key_count": len(assessment.manifest_key_ids),
        "ordinary_v6_terminal_count": assessment.ordinary_count,
        "source_terminal_aggregate_count": assessment.aggregate_count,
        "covered_key_count": len(rows),
        "missing_key_ids": [],
        "exact_disjoint_key_bijection": True,
        "frozen_manifest_key_coverage": True,
        "implementation": _implementation_reference(),
        **_claims(),
    }


def _ensure_proof(
    profile: Mapping[str, Any],
    paths: ManifestTerminalCoveragePaths,
    assessment: _CoverageAssessment,
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    payload = _proof_payload(profile, assessment)
    raw = _canonical_bytes(payload)
    digest = _sha256(raw)
    path = paths.proofs / f"{digest}.json"
    snapshot = _publish(path, raw, root=paths.root, name="manifest coverage proof")
    checked, replay = _strict_json(path, name="manifest coverage proof")
    if checked != payload or replay != snapshot or replay.sha256 != digest:
        raise ManifestTerminalCoverageIntegrityError(
            "Manifest coverage proof did not replay exactly"
        )
    return payload, snapshot


def _event_unsigned(
    profile: Mapping[str, Any],
    paths: ManifestTerminalCoveragePaths,
    proof_snapshot: registry.ArtifactSnapshot,
    assessment: _CoverageAssessment,
    *,
    recorded_at: str,
) -> dict[str, object]:
    return {
        "schema_version": profile["protocol"]["event_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "sequence_id": 1,
        "previous_entry_sha256": ZERO_HASH,
        "event_type": profile["protocol"]["event_type"],
        "recorded_at_utc": recorded_at,
        "manifest": assessment.global_identity["manifest"],
        "manifest_key_ids_sha256": assessment.global_identity[
            "manifest_key_ids_sha256"
        ],
        "manifest_key_count": len(assessment.manifest_key_ids),
        "covered_key_count": len(assessment.rows),
        "proof": _reference(proof_snapshot, paths.root),
        "frozen_manifest_key_coverage": True,
        **_claims(),
    }


def _append_event(
    profile: Mapping[str, Any],
    paths: ManifestTerminalCoveragePaths,
    proof_snapshot: registry.ArtifactSnapshot,
    assessment: _CoverageAssessment,
    *,
    now: datetime,
) -> tuple[dict[str, Any], registry.ArtifactSnapshot]:
    body = _event_unsigned(
        profile,
        paths,
        proof_snapshot,
        assessment,
        recorded_at=_utc_text(now),
    )
    payload = {**body, "entry_sha256": _sha256(_canonical_bytes(body))}
    path = paths.events / f"{1:020d}-{payload['entry_sha256']}.json"
    snapshot = _publish(
        path, _canonical_bytes(payload), root=paths.root, name="manifest coverage event"
    )
    return payload, snapshot


def _load_state(
    profile: Mapping[str, Any],
    paths: ManifestTerminalCoveragePaths,
    assessment: _CoverageAssessment,
) -> _CoverageState:
    proof_entries = _strict_entries(paths.proofs, name="manifest coverage proofs")
    event_entries = _strict_entries(paths.events, name="manifest coverage events")
    if len(proof_entries) > 1 or len(event_entries) > 1:
        raise ManifestTerminalCoverageIntegrityError(
            "Manifest coverage authority is not singleton"
        )
    proof_row: tuple[dict[str, Any], registry.ArtifactSnapshot] | None = None
    if proof_entries:
        matched = PROOF_NAME.fullmatch(proof_entries[0].name)
        payload, snapshot = _strict_json(
            proof_entries[0], name="manifest coverage proof"
        )
        if (
            matched is None
            or matched.group("digest") != snapshot.sha256
            or payload != _proof_payload(profile, assessment)
        ):
            raise ManifestTerminalCoverageIntegrityError(
                "Manifest coverage proof semantics changed"
            )
        proof_row = (payload, snapshot)
    event_row: tuple[dict[str, Any], registry.ArtifactSnapshot] | None = None
    if event_entries:
        if proof_row is None:
            raise ManifestTerminalCoverageIntegrityError(
                "Manifest coverage event is orphaned from its proof"
            )
        matched = EVENT_NAME.fullmatch(event_entries[0].name)
        payload, snapshot = _strict_json(
            event_entries[0], name="manifest coverage event"
        )
        body = dict(payload)
        entry = body.pop("entry_sha256", None)
        expected = _event_unsigned(
            profile,
            paths,
            proof_row[1],
            assessment,
            recorded_at="",
        )
        comparable = {
            key: value for key, value in body.items() if key != "recorded_at_utc"
        }
        expected_comparable = {
            key: value for key, value in expected.items() if key != "recorded_at_utc"
        }
        if (
            matched is None
            or matched.group("sequence") != "00000000000000000001"
            or matched.group("entry") != entry
            or set(payload) != {*expected, "entry_sha256"}
            or comparable != expected_comparable
            or entry != _sha256(_canonical_bytes(body))
        ):
            raise ManifestTerminalCoverageIntegrityError(
                "Manifest coverage event semantics changed"
            )
        _parse_utc(payload.get("recorded_at_utc"), name="coverage event time")
        event_row = (payload, snapshot)
    return _CoverageState(proof_row, event_row)


def _authority_children_present(paths: ManifestTerminalCoveragePaths) -> bool:
    return bool(
        _strict_entries(paths.proofs, name="manifest coverage proofs")
        or _strict_entries(paths.events, name="manifest coverage events")
    )


def _upstream_children_present(paths: ManifestTerminalCoveragePaths) -> bool:
    return bool(
        aggregate._authority_children_present(paths.aggregate)  # noqa: SLF001
        or aggregate.overlay._authority_children_present(  # noqa: SLF001
            paths.aggregate.overlay
        )
        or aggregate.sidecar._sidecar_children_present(  # noqa: SLF001
            paths.aggregate.overlay.sidecar
        )
    )


def _write_status(
    profile: Mapping[str, Any],
    paths: ManifestTerminalCoveragePaths,
    *,
    now: datetime,
    status: str,
    reason: str,
    assessment: _CoverageAssessment | None,
    proof: Path | None,
    event: Path | None,
    published: bool,
) -> None:
    manifest_count = len(assessment.manifest_key_ids) if assessment else 0
    covered_count = len(assessment.rows) if assessment else 0
    missing = list(assessment.missing_key_ids) if assessment else []
    payload = {
        "schema_version": profile["protocol"]["status_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "recorded_at_utc": _utc_text(now),
        "status": status,
        "reason": reason,
        "manifest_key_count": manifest_count,
        "covered_key_count": covered_count,
        "missing_key_ids": missing,
        "proof_path": str(proof) if proof else None,
        "event_path": str(event) if event else None,
        "upstream_publications_complete": (
            assessment.upstream_publications_complete if assessment else False
        ),
        "frozen_manifest_key_coverage": published,
        "cache_authority": False,
        **_claims(),
    }
    try:
        registry._atomic_cache(  # noqa: SLF001
            paths.status,
            _canonical_bytes(payload),
            root=paths.root,
            name="manifest coverage status",
        )
    except registry.EpochRegistryError as exc:
        raise ManifestTerminalCoverageIntegrityError(str(exc)) from exc


def _result(
    paths: ManifestTerminalCoveragePaths,
    status: str,
    reason: str,
    assessment: _CoverageAssessment | None,
    *,
    proof: Path | None = None,
    event: Path | None = None,
    published: bool = False,
) -> ManifestTerminalCoverageResult:
    return ManifestTerminalCoverageResult(
        status=status,
        reason=reason,
        status_path=paths.status,
        proof_path=proof,
        event_path=event,
        frozen_manifest_key_coverage=published,
        manifest_key_count=(len(assessment.manifest_key_ids) if assessment else 0),
        covered_key_count=(len(assessment.rows) if assessment else 0),
        missing_key_ids=(assessment.missing_key_ids if assessment else ()),
    )


def _coordinate_epoch_manifest_terminal_coverage(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    registry_root: Path | None = None,
    active_root: Path | None = None,
    shadow_root: Path | None = None,
    clock: Callable[[], datetime] | None = None,
    load_authority: LoadAuthority | None = None,
) -> ManifestTerminalCoverageResult:
    profile = load_manifest_coverage_profile(config_path)
    paths = manifest_coverage_paths(
        profile,
        registry_root=registry_root,
        active_root=active_root,
        shadow_root=shadow_root,
    )
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    handles: list[BinaryIO] = []
    acquired = False
    assessment: _CoverageAssessment | None = None
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
                raise ManifestTerminalCoverageBusyError(str(exc)) from exc
            except drain.EpochDrainError as exc:
                raise ManifestTerminalCoverageIntegrityError(str(exc)) from exc
        acquired = True
        authority = (load_authority or _load_coverage_authority)(paths, now)
        if authority is None:
            if _authority_children_present(paths) or _upstream_children_present(paths):
                raise ManifestTerminalCoverageIntegrityError(
                    "Coverage authority exists without complete upstream authority"
                )
            reason = "complete recovery and source-terminal aggregate authority is unavailable"
            _write_status(
                profile,
                paths,
                now=now,
                status="waiting_for_complete_upstream_authority",
                reason=reason,
                assessment=None,
                proof=None,
                event=None,
                published=False,
            )
            return _result(
                paths, "waiting_for_complete_upstream_authority", reason, None
            )
        assessment = _coverage_assessment(profile, paths, authority)
        if not assessment.complete or not assessment.upstream_publications_complete:
            if _authority_children_present(paths):
                raise ManifestTerminalCoverageIntegrityError(
                    "Published coverage authority no longer has complete exact evidence"
                )
            reason = (
                "the frozen manifest still has uncovered keys or pending upstream "
                "publication boundaries"
            )
            _write_status(
                profile,
                paths,
                now=now,
                status="waiting_for_manifest_terminal_coverage",
                reason=reason,
                assessment=assessment,
                proof=None,
                event=None,
                published=False,
            )
            return _result(
                paths,
                "waiting_for_manifest_terminal_coverage",
                reason,
                assessment,
            )
        state = _load_state(profile, paths, assessment)
        if state.event is not None:
            reason = "the exact frozen-manifest terminal coverage event is current"
            _write_status(
                profile,
                paths,
                now=now,
                status="manifest_terminal_coverage_current",
                reason=reason,
                assessment=assessment,
                proof=state.proof[1].path if state.proof else None,
                event=state.event[1].path,
                published=True,
            )
            return _result(
                paths,
                "manifest_terminal_coverage_current",
                reason,
                assessment,
                proof=state.proof[1].path if state.proof else None,
                event=state.event[1].path,
                published=True,
            )
        if state.proof is not None:
            _, event_snapshot = _append_event(
                profile, paths, state.proof[1], assessment, now=now
            )
            reason = (
                "the exact manifest coverage proof was forward-adopted into its "
                "singleton event"
            )
            _write_status(
                profile,
                paths,
                now=now,
                status="manifest_coverage_event_forward_adopted",
                reason=reason,
                assessment=assessment,
                proof=state.proof[1].path,
                event=event_snapshot.path,
                published=True,
            )
            return _result(
                paths,
                "manifest_coverage_event_forward_adopted",
                reason,
                assessment,
                proof=state.proof[1].path,
                event=event_snapshot.path,
                published=True,
            )
        _, proof_snapshot = _ensure_proof(profile, paths, assessment)
        _, event_snapshot = _append_event(
            profile, paths, proof_snapshot, assessment, now=now
        )
        reason = (
            "ordinary recovery and published aggregate terminals exactly cover "
            "the frozen manifest keys"
        )
        _write_status(
            profile,
            paths,
            now=now,
            status="manifest_terminal_coverage_proved",
            reason=reason,
            assessment=assessment,
            proof=proof_snapshot.path,
            event=event_snapshot.path,
            published=True,
        )
        return _result(
            paths,
            "manifest_terminal_coverage_proved",
            reason,
            assessment,
            proof=proof_snapshot.path,
            event=event_snapshot.path,
            published=True,
        )
    except ManifestTerminalCoverageBusyError:
        raise
    except ManifestTerminalCoverageError as exc:
        if acquired:
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
                    published=False,
                )
            except ManifestTerminalCoverageError:
                pass
        raise
    finally:
        try:
            drain._release_locks(handles)  # noqa: SLF001
        except drain.EpochDrainError as exc:
            if sys.exc_info()[0] is None:
                raise ManifestTerminalCoverageIntegrityError(str(exc)) from exc


def coordinate_epoch_manifest_terminal_coverage(
    *, config_path: Path = DEFAULT_CONFIG_PATH
) -> ManifestTerminalCoverageResult:
    """Run one reviewed machine-only frozen-manifest coverage poll."""

    return _coordinate_epoch_manifest_terminal_coverage(config_path=config_path)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = coordinate_epoch_manifest_terminal_coverage(config_path=args.config)
    except ManifestTerminalCoverageError as exc:
        print(
            json.dumps(
                {"status": "error", "error": f"{type(exc).__name__}:{exc}"},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1
    print(
        json.dumps(
            {
                "status": result.status,
                "reason": result.reason,
                "status_path": str(result.status_path),
                "proof_path": (str(result.proof_path) if result.proof_path else None),
                "event_path": (str(result.event_path) if result.event_path else None),
                "manifest_key_count": result.manifest_key_count,
                "covered_key_count": result.covered_key_count,
                "missing_key_ids": list(result.missing_key_ids),
                "frozen_manifest_key_coverage": (result.frozen_manifest_key_coverage),
                "full_workset_terminal": False,
                "terminal_transition_closure_implemented": False,
                "derived_future_work_reservation_implemented": False,
                "lifecycle_authority": False,
                "old_epoch_drained": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
