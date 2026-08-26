"""Replay-gated fixed-point orchestration for the Ootang machine cycle.

Version 3 is additive: it binds the reviewed cycle-v2 profile, inserts two
runner-independent issue replay checkpoints, and routes every live transition
through the verified-live entrypoint.  The legacy v1/v2 entrypoints and the
default pipeline remain unchanged.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_prequential_cycle as cycle_v1  # noqa: E402
from monitoring import ootang_prequential_cycle_v2 as cycle_v2  # noqa: E402


DEFAULT_CONFIG_PATH = ROOT / "config" / "ootang_prequential_cycle.v3.json"
DEFAULT_CONFIG_SHA256 = (
    "6852876db121027e82aedfb2b65c9cb1d9b40106b19c7068ba8764b317e1db24"
)
BASE_CYCLE_PROFILE_SHA256 = cycle_v2.DEFAULT_CONFIG_SHA256
ISSUE_REPLAY_PROFILE_SHA256 = (
    "c42a56a547691654f9281f44b94e5d79ef66a8ff0064b255a59d67c6939e6fd5"
)
VERIFIED_LIVE_PROFILE_SHA256 = (
    "081af2dfd4b95f28b750d915a2ff74d508381e62f5d539aaaaa62b8add44992b"
)
TEST_OVERRIDE_ENV = "OOTANG_E2B_ALLOW_TEST_CYCLE_V3_OVERRIDE"
HEX_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_RUNTIME_JSON_BYTES = 8 * 1024 * 1024


EXPECTED_STAGE_ORDER = (
    "issue_replay_before_source",
    "shadow_before_source",
    "source_ingest",
    "bundle_ensure",
    "verified_live_reconcile_before_outcome",
    "shadow_after_live_before_outcome",
    "outcome_materialize",
    "verified_live_reconcile_after_outcome",
    "shadow_after_outcome",
    "issue_produce",
    "issue_replay_after_issue",
    "verified_live_seal_issue",
    "shadow_after_issue",
)


EXPECTED_TOP_KEYS = frozenset(
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
        "base_cycle_profile",
        "issue_replay_profile",
        "verified_live_profile",
        "runtime",
        "cycle",
        "engineering_capabilities",
    }
)
EXPECTED_CAPABILITIES = {
    "calibration_shadow_orchestration_implemented": True,
    "runner_independent_checkpoint_inference_replayed": True,
    "designated_entrypoint_replay_gate_implemented": True,
    "old_live_v1_entrypoint_disabled": False,
    "scheduler_entrypoint_authorization_implemented": False,
    "cross_ledger_commit_is_single_database_atomic": False,
    "trusted_anchor_receipt_verified": False,
    "automatic_epoch_rotation_implemented": False,
    "automatic_calibration_promotion_implemented": False,
    "e2_live_evidence_eligible": False,
    "real_activation_ready": False,
}
REPLAY_STATUS_SCHEMA = "ootang_issue_replay_status_v1"
REPLAY_PROFILE_SCHEMA = "ootang_issue_replay_profile_v1"
REPLAY_PROFILE_ID = "ootang-issue-replay-v1"
REPLAY_STATUS_ALLOWLIST = frozenset(
    {
        "waiting_for_source_model_or_issue",
        "verified",
        "already_verified_idempotent",
    }
)
REPLAY_STATUS_KEYS = frozenset(
    {
        "schema_version",
        "profile_id",
        "artifact_status",
        "profile_sha256",
        "runtime_root",
        "recorded_at_utc",
        "replay_status",
        "reason",
        "target_date",
        "receipt",
        "runner_independent_checkpoint_inference_replayed",
        "input_manifest_semantics_verified",
        "outcome_read",
        "formal_warning_output",
        "e2_live_evidence_eligible",
        "real_activation_ready",
        "automatic_calibration_promotion",
    }
)
VERIFIED_LIVE_STATUS_SCHEMA = "ootang_verified_live_status_v1"
VERIFIED_LIVE_PROFILE_SCHEMA = "ootang_verified_live_profile_v1"
VERIFIED_LIVE_PROFILE_ID = "ootang-verified-live-v1"
VERIFIED_LIVE_STATUS_ALLOWLIST = frozenset(
    {
        "waiting_for_live_prerequisites",
        "waiting_for_new_data",
        "waiting_for_missing_natural_day",
        "waiting_for_backfill_outcome",
        "waiting_for_outcome",
        "work_remaining",
    }
)
VERIFIED_LIVE_BASE_STATUS_KEYS = frozenset(
    {
        "schema_version",
        "profile_id",
        "profile_sha256",
        "artifact_status",
        "runner_status",
        "reason",
        "recorded_at_utc",
        "runtime_root",
        "live_profile_sha256",
        "replay_profile_sha256",
        "designated_entrypoint_replay_gate_implemented",
        "runner_independent_checkpoint_inference_replayed",
        "guarded_issue_completed",
        "old_live_v1_entrypoint_disabled",
        "scheduler_entrypoint_authorization_implemented",
        "trusted_anchor_receipt_verified",
        "automatic_epoch_rotation_implemented",
        "automatic_calibration_promotion_implemented",
        "formal_warning_output",
        "independent_label_used",
        "confirmatory_external_validation",
        "vajont_used",
        "e2_live_evidence_eligible",
        "real_activation_ready",
        "guard_intent_sha256",
        "guard_completion_sha256",
        "replay_receipt_sha256",
    }
)
VERIFIED_LIVE_PROJECTION_STATUS_KEYS = frozenset(
    {
        "live_epoch_id",
        "live_ledger_event_count",
        "live_ledger_terminal_sequence_id",
        "live_ledger_terminal_sha256",
        "last_finalized_date",
        "next_target_date",
        "outstanding_target_date",
    }
)
FALSE_EVIDENCE_FLAGS = (
    "formal_warning_output",
    "independent_label_used",
    "confirmatory_external_validation",
    "vajont_used",
    "default_pipeline_member",
    "trusted_anchor_receipt_verified",
    "automatic_epoch_rotation_implemented",
    "automatic_calibration_promotion_implemented",
    "e2_live_evidence_eligible",
    "real_activation_ready",
    "promotion_performed",
)


class CycleV3Error(RuntimeError):
    """Base error for replay-gated orchestration."""


class CycleV3ConfigError(CycleV3Error):
    """The v3 profile or one of its exact bindings is invalid."""


class CycleV3IntegrityError(CycleV3Error):
    """A runtime artifact or transition failed closed."""


class CycleV3BusyError(CycleV3Error):
    """A machine writer owns or changed a required snapshot."""


@dataclass(frozen=True)
class CycleV3RuntimePaths:
    root: Path
    shadow_root: Path
    cycle_status: Path
    cycle_lock: Path
    replay_status: Path
    verified_live_status: Path
    base: cycle_v2.CycleV2RuntimePaths
    base_config: Path
    replay_config: Path
    verified_live_config: Path


@dataclass(frozen=True)
class CycleV3Dependencies:
    source_ingest: Callable[[], object]
    bundle_ensure: Callable[[], object]
    outcome_materialize: Callable[[], object]
    issue_produce: Callable[[], object]
    shadow_reconcile: Callable[[], object]
    issue_replay: Callable[[], object]
    verified_live_reconcile: Callable[[], object]


@dataclass(frozen=True)
class CycleV3Result:
    status_path: Path
    status: str
    iterations: int
    progress_token_sha256: str


@dataclass(frozen=True)
class _StageOutcome:
    status: str
    status_path: Path | None = None


Clock = Callable[[], datetime]
ProgressTokenBuilder = Callable[[], str]
V2TokenBuilder = Callable[[cycle_v2.CycleV2RuntimePaths, Mapping[str, Any]], str]
ScientificPayloadBuilder = Callable[..., Mapping[str, object]]


def _strict_json(path: Path, *, name: str) -> tuple[dict[str, Any], str]:
    try:
        return cycle_v2._strict_json(path, name=name)
    except cycle_v2.CycleV2Error as exc:
        raise CycleV3ConfigError(str(exc)) from exc


def _strict_runtime_json_snapshot(
    path: Path, *, name: str
) -> dict[str, Any] | None:
    """Read one runtime JSON snapshot without following its final component."""

    descriptor: int | None = None
    try:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError:
            return None
        opened_before = os.fstat(descriptor)
        named_before = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened_before.st_mode)
            or not stat.S_ISREG(named_before.st_mode)
            or (opened_before.st_dev, opened_before.st_ino)
            != (named_before.st_dev, named_before.st_ino)
        ):
            raise CycleV3IntegrityError(
                f"{name} is not one stable regular file"
            )
        if (
            opened_before.st_size <= 0
            or opened_before.st_size > MAX_RUNTIME_JSON_BYTES
        ):
            raise CycleV3IntegrityError(f"{name} size is outside safety bounds")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(MAX_RUNTIME_JSON_BYTES + 1)
        opened_after = os.fstat(descriptor)
        named_after = os.stat(path, follow_symlinks=False)
        stable_fields = (
            "st_dev",
            "st_ino",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if (
            len(raw) != opened_before.st_size
            or any(
                getattr(opened_before, field) != getattr(opened_after, field)
                for field in stable_fields
            )
            or not stat.S_ISREG(named_after.st_mode)
            or (opened_after.st_dev, opened_after.st_ino)
            != (named_after.st_dev, named_after.st_ino)
        ):
            raise CycleV3BusyError(f"{name} changed while being captured")
    except CycleV3Error:
        raise
    except OSError as exc:
        raise CycleV3IntegrityError(f"{name} cannot be read safely") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        return cycle_v1._decode_json(raw, name=name)
    except cycle_v1.CycleError as exc:
        raise CycleV3IntegrityError(str(exc)) from exc


def _exact_keys(
    value: object, expected: set[str] | frozenset[str], *, name: str
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(expected):
        raise CycleV3ConfigError(f"{name} keys changed")
    return value


def _required_string(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise CycleV3ConfigError(f"{name} must be a non-empty canonical string")
    return value


def _required_hash(value: object, *, name: str) -> str:
    text = _required_string(value, name=name)
    if HEX_SHA256_RE.fullmatch(text) is None:
        raise CycleV3ConfigError(f"{name} must be a lowercase SHA-256")
    return text


def _project_artifact(value: object, *, project_root: Path, name: str) -> Path:
    try:
        return cycle_v2._project_artifact(value, project_root=project_root, name=name)
    except cycle_v2.CycleV2Error as exc:
        raise CycleV3ConfigError(str(exc)) from exc


def _bound_profile(
    contract_value: object,
    *,
    expected_sha256: str,
    project_root: Path,
    name: str,
) -> tuple[Path, dict[str, Any]]:
    contract = _exact_keys(contract_value, {"path", "expected_sha256"}, name=name)
    if (
        _required_hash(contract["expected_sha256"], name=f"{name}.expected_sha256")
        != expected_sha256
    ):
        raise CycleV3ConfigError(f"{name} expected SHA-256 changed")
    path = _project_artifact(
        contract["path"], project_root=project_root, name=f"{name}.path"
    )
    payload, actual = _strict_json(path, name=f"bound {name}")
    if actual != expected_sha256:
        raise CycleV3ConfigError(f"bound {name} bytes changed")
    return path, payload


def load_cycle_v3_profile(
    path: Path = DEFAULT_CONFIG_PATH, *, project_root: Path = ROOT
) -> dict[str, Any]:
    """Load v3 and verify the exact v2, replay, and verified-live chain."""

    resolved = path.resolve()
    profile, profile_sha = _strict_json(resolved, name="cycle v3 profile")
    if (
        resolved == DEFAULT_CONFIG_PATH.resolve()
        and profile_sha != DEFAULT_CONFIG_SHA256
    ):
        raise CycleV3ConfigError(
            "default cycle v3 profile differs from the reviewed file; create a new version"
        )
    _exact_keys(profile, EXPECTED_TOP_KEYS, name="cycle v3 profile")
    expected_scalars = {
        "schema_version": "ootang_prequential_cycle_profile_v3",
        "profile_id": "ootang-prequential-cycle-v3",
        "profile_version": "3.0.0-engineering",
        "case": "ootang",
        "artifact_status": (
            "e2_replay_gated_calibration_shadow_engineering_only_not_live_evidence"
        ),
    }
    for key, expected in expected_scalars.items():
        if profile[key] != expected:
            raise CycleV3ConfigError(f"cycle v3 profile {key} changed")
    for flag in (
        "formal_warning_output",
        "independent_label_used",
        "confirmatory_external_validation",
        "vajont_used",
        "default_pipeline_member",
    ):
        if profile[flag] is not False:
            raise CycleV3ConfigError(f"cycle v3 profile {flag} must remain false")

    root = project_root.resolve()
    base_path, _ = _bound_profile(
        profile["base_cycle_profile"],
        expected_sha256=BASE_CYCLE_PROFILE_SHA256,
        project_root=root,
        name="base_cycle_profile",
    )
    try:
        base_profile = cycle_v2.load_cycle_v2_profile(base_path, project_root=root)
    except cycle_v2.CycleV2Error as exc:
        raise CycleV3ConfigError(f"bound cycle v2 is invalid: {exc}") from exc

    replay_path, replay_profile = _bound_profile(
        profile["issue_replay_profile"],
        expected_sha256=ISSUE_REPLAY_PROFILE_SHA256,
        project_root=root,
        name="issue_replay_profile",
    )
    if (
        replay_profile.get("schema_version") != REPLAY_PROFILE_SCHEMA
        or replay_profile.get("profile_id") != REPLAY_PROFILE_ID
    ):
        raise CycleV3ConfigError("bound issue replay profile identity changed")
    replay_runtime = replay_profile.get("runtime")
    if not isinstance(replay_runtime, dict):
        raise CycleV3ConfigError("bound issue replay runtime is missing")
    replay_artifact_status = _required_string(
        replay_profile.get("artifact_status"), name="issue replay artifact_status"
    )
    replay_status_relative = _required_string(
        replay_runtime.get("status"), name="issue replay runtime.status"
    )

    verified_path, verified_profile = _bound_profile(
        profile["verified_live_profile"],
        expected_sha256=VERIFIED_LIVE_PROFILE_SHA256,
        project_root=root,
        name="verified_live_profile",
    )
    if (
        verified_profile.get("schema_version") != VERIFIED_LIVE_PROFILE_SCHEMA
        or verified_profile.get("profile_id") != VERIFIED_LIVE_PROFILE_ID
    ):
        raise CycleV3ConfigError("bound verified-live profile identity changed")
    verified_runtime = verified_profile.get("runtime")
    if not isinstance(verified_runtime, dict):
        raise CycleV3ConfigError("bound verified-live runtime is missing")
    verified_artifact_status = _required_string(
        verified_profile.get("artifact_status"),
        name="verified-live artifact_status",
    )
    verified_status_relative = _required_string(
        verified_runtime.get("status"), name="verified-live runtime.status"
    )
    replay_binding = _exact_keys(
        verified_profile.get("replay_profile"),
        {"path", "expected_sha256"},
        name="verified-live replay_profile",
    )
    if (
        _required_hash(
            replay_binding["expected_sha256"],
            name="verified-live replay_profile.expected_sha256",
        )
        != ISSUE_REPLAY_PROFILE_SHA256
        or _project_artifact(
            replay_binding["path"],
            project_root=root,
            name="verified-live replay_profile.path",
        )
        != replay_path
    ):
        raise CycleV3ConfigError("verified-live profile binds another replay profile")

    runtime = _exact_keys(profile["runtime"], {"cycle_status"}, name="runtime")
    _required_string(runtime["cycle_status"], name="runtime.cycle_status")
    cycle = _exact_keys(
        profile["cycle"],
        {
            "stage_order",
            "max_iterations",
            "stable_iterations_to_stop",
            "progress_token_schema_version",
            "busy_exit_code",
            "blocked_exit_code",
        },
        name="cycle",
    )
    if tuple(cycle["stage_order"]) != EXPECTED_STAGE_ORDER:
        raise CycleV3ConfigError("cycle v3 stage order changed")
    fixed_cycle = {
        "max_iterations": 64,
        "stable_iterations_to_stop": 1,
        "progress_token_schema_version": "ootang_prequential_cycle_progress_v3",
        "busy_exit_code": 3,
        "blocked_exit_code": 2,
    }
    for key, expected in fixed_cycle.items():
        value = cycle[key]
        if value != expected or isinstance(value, bool) and isinstance(expected, int):
            raise CycleV3ConfigError(f"cycle v3 {key} changed")
    if profile["engineering_capabilities"] != EXPECTED_CAPABILITIES:
        raise CycleV3ConfigError("cycle v3 engineering capability boundary changed")

    profile["_profile_path"] = str(resolved)
    profile["_profile_sha256"] = profile_sha
    profile["_project_root"] = str(root)
    profile["_base_profile_path"] = str(base_path)
    profile["_base_profile_payload"] = base_profile
    profile["_replay_profile_path"] = str(replay_path)
    profile["_replay_profile_payload"] = replay_profile
    profile["_replay_artifact_status"] = replay_artifact_status
    profile["_replay_status_relative"] = replay_status_relative
    profile["_verified_profile_path"] = str(verified_path)
    profile["_verified_profile_payload"] = verified_profile
    profile["_verified_artifact_status"] = verified_artifact_status
    profile["_verified_status_relative"] = verified_status_relative
    return profile


def _runtime_child(root: Path, value: object, *, name: str) -> Path:
    try:
        return cycle_v2._relative_runtime_path(root, value, name=name)
    except cycle_v2.CycleV2Error as exc:
        raise CycleV3ConfigError(str(exc)) from exc


def runtime_paths_v3(
    profile: Mapping[str, Any],
    *,
    runtime_root: Path | None = None,
    shadow_runtime_root: Path | None = None,
) -> CycleV3RuntimePaths:
    base_profile = profile.get("_base_profile_payload")
    if not isinstance(base_profile, dict):
        raise CycleV3ConfigError("bound cycle v2 payload is missing")
    try:
        base = cycle_v2.runtime_paths_v2(
            base_profile,
            runtime_root=runtime_root,
            shadow_runtime_root=shadow_runtime_root,
        )
    except cycle_v2.CycleV2Error as exc:
        raise CycleV3ConfigError(str(exc)) from exc
    cycle_status = _runtime_child(
        base.root, profile["runtime"]["cycle_status"], name="runtime.cycle_status"
    )
    replay_status = _runtime_child(
        base.root,
        profile["_replay_status_relative"],
        name="issue replay runtime.status",
    )
    verified_status = _runtime_child(
        base.root,
        profile["_verified_status_relative"],
        name="verified-live runtime.status",
    )
    occupied = {value for value in vars(base).values() if isinstance(value, Path)}
    additions = {cycle_status, replay_status, verified_status}
    if len(additions) != 3 or additions & occupied:
        raise CycleV3ConfigError("cycle v3 runtime paths collide")
    return CycleV3RuntimePaths(
        root=base.root,
        shadow_root=base.shadow_root,
        cycle_status=cycle_status,
        cycle_lock=base.cycle_lock,
        replay_status=replay_status,
        verified_live_status=verified_status,
        base=base,
        base_config=Path(str(profile["_base_profile_path"])),
        replay_config=Path(str(profile["_replay_profile_path"])),
        verified_live_config=Path(str(profile["_verified_profile_path"])),
    )


def _canonical_bytes(value: object) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError, UnicodeEncodeError) as exc:
        raise CycleV3IntegrityError(
            "combined progress state is not canonical finite JSON"
        ) from exc


def _production_replay_payload(
    *,
    config_path: Path,
    runtime_root: Path,
    project_root: Path,
) -> Mapping[str, object]:
    from monitoring import ootang_issue_replay as replay

    return replay.replay_progress_payload(
        config_path=config_path,
        runtime_root=runtime_root,
        project_root=project_root,
    )


def _production_guard_payload(
    *,
    config_path: Path,
    runtime_root: Path,
    project_root: Path,
) -> Mapping[str, object]:
    from monitoring import ootang_verified_live as guard

    return guard.guard_progress_payload(
        config_path=config_path,
        runtime_root=runtime_root,
        project_root=project_root,
    )


def _canonical_mapping(value: object, *, name: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise CycleV3IntegrityError(f"{name} progress payload is not a mapping")
    try:
        materialized = dict(value)
    except (RecursionError, TypeError, ValueError) as exc:
        raise CycleV3IntegrityError(
            f"{name} progress payload cannot be materialized"
        ) from exc
    canonical = json.loads(_canonical_bytes(materialized))
    if not isinstance(canonical, dict):  # pragma: no cover - mapping guarantees it
        raise CycleV3IntegrityError(f"{name} progress payload is not an object")
    return canonical


def progress_token_payload_v3(
    paths: CycleV3RuntimePaths,
    profile: Mapping[str, Any],
    *,
    v2_token_builder: V2TokenBuilder | None = None,
    replay_payload_builder: ScientificPayloadBuilder | None = None,
    guard_payload_builder: ScientificPayloadBuilder | None = None,
) -> dict[str, object]:
    """Fence the replay and guard projections inside two cycle-v2 snapshots."""

    base_profile = profile.get("_base_profile_payload")
    if not isinstance(base_profile, dict):
        raise CycleV3ConfigError("bound cycle v2 payload is missing")
    v2_builder = v2_token_builder or cycle_v2.build_progress_token_v2
    replay_builder = replay_payload_builder or _production_replay_payload
    guard_builder = guard_payload_builder or _production_guard_payload
    kwargs = {
        "runtime_root": paths.root,
        "project_root": Path(str(profile["_project_root"])),
    }
    try:
        base_before = v2_builder(paths.base, base_profile)
        replay_before = replay_builder(config_path=paths.replay_config, **kwargs)
        guard_payload = guard_builder(config_path=paths.verified_live_config, **kwargs)
        replay_after = replay_builder(config_path=paths.replay_config, **kwargs)
        base_after = v2_builder(paths.base, base_profile)
    except CycleV3Error:
        raise
    except Exception as exc:
        if _is_busy(exc):
            raise CycleV3BusyError(
                f"combined progress snapshot is busy: {type(exc).__name__}:{exc}"
            ) from exc
        raise CycleV3IntegrityError(
            f"combined progress snapshot failed: {type(exc).__name__}:{exc}"
        ) from exc
    for name, value in (("base_before", base_before), ("base_after", base_after)):
        if not isinstance(value, str) or HEX_SHA256_RE.fullmatch(value) is None:
            raise CycleV3IntegrityError(f"{name} is not a SHA-256")
    if base_before != base_after:
        raise CycleV3BusyError(
            "cycle-v2 scientific state changed across replay/guard snapshots"
        )
    replay_before_canonical = _canonical_mapping(replay_before, name="replay")
    replay_after_canonical = _canonical_mapping(replay_after, name="replay")
    if replay_before_canonical != replay_after_canonical:
        raise CycleV3BusyError(
            "replay scientific state changed across the guard snapshot"
        )
    guard_canonical = _canonical_mapping(guard_payload, name="verified-live guard")
    return {
        "schema_version": "ootang_prequential_cycle_progress_v3",
        "base_cycle_v2_progress_sha256": base_after,
        "issue_replay_profile_sha256": ISSUE_REPLAY_PROFILE_SHA256,
        "verified_live_profile_sha256": VERIFIED_LIVE_PROFILE_SHA256,
        "verified_issue_replay_projection": replay_after_canonical,
        "verified_live_guard_projection": guard_canonical,
    }


def build_progress_token_v3(
    paths: CycleV3RuntimePaths,
    profile: Mapping[str, Any],
    *,
    v2_token_builder: V2TokenBuilder | None = None,
    replay_payload_builder: ScientificPayloadBuilder | None = None,
    guard_payload_builder: ScientificPayloadBuilder | None = None,
) -> str:
    payload = progress_token_payload_v3(
        paths,
        profile,
        v2_token_builder=v2_token_builder,
        replay_payload_builder=replay_payload_builder,
        guard_payload_builder=guard_payload_builder,
    )
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _read_bound_stage_status(
    path: Path,
    *,
    expected_path: Path,
    profile: Mapping[str, Any],
    paths: CycleV3RuntimePaths,
    kind: str,
) -> _StageOutcome:
    candidate = Path(os.path.abspath(os.fspath(path)))
    expected = Path(os.path.abspath(os.fspath(expected_path)))
    if candidate != expected:
        raise CycleV3IntegrityError(
            f"{kind} returned a status path outside its contract"
        )
    payload = _strict_runtime_json_snapshot(
        candidate, name=f"{kind} stage status"
    )
    if payload is None:
        raise CycleV3IntegrityError(f"{kind} returned a missing status path")
    if kind == "issue replay":
        expected_schema = REPLAY_STATUS_SCHEMA
        expected_id = REPLAY_PROFILE_ID
        expected_artifact = profile["_replay_artifact_status"]
        expected_profile_sha = ISSUE_REPLAY_PROFILE_SHA256
        status_key = "replay_status"
        allowlist = REPLAY_STATUS_ALLOWLIST
        if set(payload) != REPLAY_STATUS_KEYS:
            raise CycleV3IntegrityError("issue replay status keys changed")
    elif kind == "verified live":
        expected_schema = VERIFIED_LIVE_STATUS_SCHEMA
        expected_id = VERIFIED_LIVE_PROFILE_ID
        expected_artifact = profile["_verified_artifact_status"]
        expected_profile_sha = VERIFIED_LIVE_PROFILE_SHA256
        status_key = "runner_status"
        allowlist = VERIFIED_LIVE_STATUS_ALLOWLIST
        keys = frozenset(payload)
        if keys not in {
            VERIFIED_LIVE_BASE_STATUS_KEYS,
            VERIFIED_LIVE_BASE_STATUS_KEYS | VERIFIED_LIVE_PROJECTION_STATUS_KEYS,
        }:
            raise CycleV3IntegrityError("verified live status keys changed")
    else:  # pragma: no cover - private call sites are fixed
        raise CycleV3IntegrityError(f"unknown bound status kind {kind!r}")
    if payload.get("schema_version") != expected_schema:
        raise CycleV3IntegrityError(f"{kind} status schema changed")
    if payload.get("profile_id") != expected_id:
        raise CycleV3IntegrityError(f"{kind} status profile id changed")
    if payload.get("artifact_status") != expected_artifact:
        raise CycleV3IntegrityError(f"{kind} status artifact boundary changed")
    if payload.get("profile_sha256") != expected_profile_sha:
        raise CycleV3IntegrityError(f"{kind} status profile binding changed")
    if payload.get("runtime_root") != str(paths.root.resolve()):
        raise CycleV3IntegrityError(f"{kind} status runtime root changed")
    if not isinstance(payload.get("reason"), str) or not payload["reason"]:
        raise CycleV3IntegrityError(f"{kind} status reason changed")
    if not isinstance(payload.get("recorded_at_utc"), str) or not payload[
        "recorded_at_utc"
    ]:
        raise CycleV3IntegrityError(f"{kind} status timestamp changed")
    if kind == "verified live" and (
        payload.get("replay_profile_sha256") != ISSUE_REPLAY_PROFILE_SHA256
        or payload.get("live_profile_sha256") != cycle_v1.LIVE_PROFILE_SHA256
    ):
        raise CycleV3IntegrityError("verified live status dependency binding changed")
    value = payload.get(status_key)
    if value not in allowlist:
        raise CycleV3IntegrityError(
            f"{kind} returned unknown or fatal status {value!r}"
        )
    if kind == "issue replay":
        replayed = value in {"verified", "already_verified_idempotent"}
        if (
            payload.get("runner_independent_checkpoint_inference_replayed")
            is not replayed
            or payload.get("input_manifest_semantics_verified") is not replayed
            or payload.get("outcome_read") is not False
            or payload.get("automatic_calibration_promotion") is not False
        ):
            raise CycleV3IntegrityError("issue replay status evidence boundary changed")
        receipt = payload.get("receipt")
        target = payload.get("target_date")
        if replayed:
            if (
                not isinstance(target, str)
                or not target
                or not isinstance(receipt, dict)
                or set(receipt) != {"path", "sha256", "size_bytes"}
                or not isinstance(receipt.get("path"), str)
                or HEX_SHA256_RE.fullmatch(str(receipt.get("sha256"))) is None
                or type(receipt.get("size_bytes")) is not int
                or receipt["size_bytes"] <= 0
            ):
                raise CycleV3IntegrityError(
                    "issue replay verified status lacks its receipt binding"
                )
        else:
            if receipt is not None:
                raise CycleV3IntegrityError(
                    "issue replay waiting status claimed a receipt"
                )
            if target is not None:
                try:
                    parsed_target = date.fromisoformat(target)
                except (TypeError, ValueError) as exc:
                    raise CycleV3IntegrityError(
                        "issue replay waiting status target changed"
                    ) from exc
                if parsed_target.isoformat() != target:
                    raise CycleV3IntegrityError(
                        "issue replay waiting status target changed"
                    )
    else:
        if (
            payload.get("designated_entrypoint_replay_gate_implemented") is not True
            or payload.get("old_live_v1_entrypoint_disabled") is not False
            or payload.get("scheduler_entrypoint_authorization_implemented") is not False
        ):
            raise CycleV3IntegrityError(
                "verified live status entrypoint boundary changed"
            )
        replayed = payload.get(
            "runner_independent_checkpoint_inference_replayed"
        )
        completed = payload.get("guarded_issue_completed")
        intent_sha = payload.get("guard_intent_sha256")
        completion_sha = payload.get("guard_completion_sha256")
        replay_sha = payload.get("replay_receipt_sha256")
        for name, digest in (
            ("guard intent", intent_sha),
            ("guard completion", completion_sha),
            ("replay receipt", replay_sha),
        ):
            if digest is not None and (
                not isinstance(digest, str)
                or HEX_SHA256_RE.fullmatch(digest) is None
            ):
                raise CycleV3IntegrityError(
                    f"verified live {name} status binding changed"
                )
        if (
            type(replayed) is not bool
            or type(completed) is not bool
            or replayed is not completed
            or completed != (completion_sha is not None)
            or (completed and (intent_sha is None or replay_sha is None))
        ):
            raise CycleV3IntegrityError(
                "verified live completion/replay claim is inconsistent"
            )
    for flag in FALSE_EVIDENCE_FLAGS:
        if flag in payload and payload[flag] is not False:
            raise CycleV3IntegrityError(
                f"{kind} status claimed forbidden capability {flag}"
            )
    return _StageOutcome(str(value), candidate)


def _bound_stage_outcome(
    value: object,
    *,
    profile: Mapping[str, Any],
    paths: CycleV3RuntimePaths,
    kind: str,
    require_status_path: bool,
) -> _StageOutcome:
    expected_path = (
        paths.replay_status if kind == "issue replay" else paths.verified_live_status
    )
    allowlist = (
        REPLAY_STATUS_ALLOWLIST
        if kind == "issue replay"
        else VERIFIED_LIVE_STATUS_ALLOWLIST
    )
    if isinstance(value, _StageOutcome):
        outcome = value
    elif isinstance(value, Path):
        outcome = _read_bound_stage_status(
            value,
            expected_path=expected_path,
            profile=profile,
            paths=paths,
            kind=kind,
        )
    elif isinstance(value, str):
        outcome = _StageOutcome(value)
    else:
        status = getattr(value, "status", None)
        status_path = getattr(value, "status_path", None)
        if not isinstance(status, str) or not status:
            raise CycleV3IntegrityError(f"{kind} returned an unsupported result")
        if status_path is not None and not isinstance(status_path, Path):
            status_path = Path(status_path)
        outcome = _StageOutcome(status, status_path)
    if outcome.status not in allowlist:
        raise CycleV3IntegrityError(
            f"{kind} returned unknown or fatal status {outcome.status!r}"
        )
    if outcome.status_path is not None:
        verified = _read_bound_stage_status(
            outcome.status_path,
            expected_path=expected_path,
            profile=profile,
            paths=paths,
            kind=kind,
        )
        if verified.status != outcome.status:
            raise CycleV3IntegrityError(
                f"{kind} result disagrees with its durable status"
            )
        outcome = verified
    elif require_status_path:
        raise CycleV3IntegrityError(f"{kind} did not return a status path")
    return outcome


def _production_dependencies(
    profile: Mapping[str, Any], paths: CycleV3RuntimePaths
) -> CycleV3Dependencies:
    base_profile = profile.get("_base_profile_payload")
    if not isinstance(base_profile, dict):
        raise CycleV3ConfigError("bound cycle v2 payload is missing")
    try:
        base = cycle_v2._production_dependencies(base_profile, paths.base)
    except cycle_v2.CycleV2Error as exc:
        raise CycleV3IntegrityError(str(exc)) from exc
    project_root = Path(str(profile["_project_root"]))

    def issue_replay() -> object:
        from monitoring import ootang_issue_replay as replay

        return replay.verify_pending_issue(
            config_path=paths.replay_config,
            runtime_root=paths.root,
            project_root=project_root,
        )

    def verified_live_reconcile() -> object:
        from monitoring import ootang_verified_live as guard

        return guard.poll_verified_live_runner(
            config_path=paths.verified_live_config,
            runtime_root=paths.root,
            project_root=project_root,
        )

    return CycleV3Dependencies(
        source_ingest=base.source_ingest,
        bundle_ensure=base.bundle_ensure,
        outcome_materialize=base.outcome_materialize,
        issue_produce=base.issue_produce,
        shadow_reconcile=base.shadow_reconcile,
        issue_replay=issue_replay,
        verified_live_reconcile=verified_live_reconcile,
    )


def _stage_calls(
    dependencies: CycleV3Dependencies,
) -> tuple[tuple[str, str, str | None, Callable[[], object]], ...]:
    return (
        ("issue_replay_before_source", "replay", None, dependencies.issue_replay),
        ("shadow_before_source", "shadow", None, dependencies.shadow_reconcile),
        ("source_ingest", "base", "source_ingest", dependencies.source_ingest),
        ("bundle_ensure", "base", "bundle_ensure", dependencies.bundle_ensure),
        (
            "verified_live_reconcile_before_outcome",
            "verified_live",
            None,
            dependencies.verified_live_reconcile,
        ),
        (
            "shadow_after_live_before_outcome",
            "shadow",
            None,
            dependencies.shadow_reconcile,
        ),
        (
            "outcome_materialize",
            "base",
            "outcome_materialize",
            dependencies.outcome_materialize,
        ),
        (
            "verified_live_reconcile_after_outcome",
            "verified_live",
            None,
            dependencies.verified_live_reconcile,
        ),
        ("shadow_after_outcome", "shadow", None, dependencies.shadow_reconcile),
        ("issue_produce", "base", "issue_produce", dependencies.issue_produce),
        ("issue_replay_after_issue", "replay", None, dependencies.issue_replay),
        (
            "verified_live_seal_issue",
            "verified_live",
            None,
            dependencies.verified_live_reconcile,
        ),
        ("shadow_after_issue", "shadow", None, dependencies.shadow_reconcile),
    )


def _is_busy(exc: BaseException) -> bool:
    if isinstance(exc, (CycleV3BusyError, cycle_v2.CycleV2BusyError)):
        return True
    if isinstance(exc, cycle_v1.CycleBusyError):
        return True
    return cycle_v2._is_busy(exc) or type(exc).__name__ in {
        "IssueReplayBusy",
        "IssueReplayBusyError",
        "ReplayBusyError",
        "VerifiedLiveBusy",
        "VerifiedLiveBusyError",
    }


def _utc_text(clock: Clock) -> str:
    value = clock()
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise CycleV3IntegrityError(
            "machine clock must return a timezone-aware datetime"
        )
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _acquire_cycle_lock(path: Path):
    """Open the designated v3 outer lock without following a final symlink."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor: int | None = None
    handle = None
    try:
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(path, flags, 0o600)
        handle = os.fdopen(descriptor, "a+b")
        descriptor = None
        opened = os.fstat(handle.fileno())
        named = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise CycleV3IntegrityError(
                "the shared cycle lock is not one stable regular file"
            )
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        named_after = os.stat(path, follow_symlinks=False)
        opened_after = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(named_after.st_mode)
            or (opened_after.st_dev, opened_after.st_ino)
            != (named_after.st_dev, named_after.st_ino)
        ):
            raise CycleV3IntegrityError(
                "the shared cycle lock path changed while being acquired"
            )
        return handle
    except BlockingIOError as exc:
        raise CycleV3BusyError(
            "another v1, v2, or v3 machine cycle owns the shared outer lock"
        ) from exc
    except CycleV3Error:
        raise
    except OSError as exc:
        raise CycleV3IntegrityError("cannot acquire the shared cycle lock") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if handle is not None and sys.exc_info()[0] is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            handle.close()


def _status_payload(
    profile: Mapping[str, Any],
    paths: CycleV3RuntimePaths,
    *,
    clock: Clock,
    cycle_status: str,
    reason: str,
    iterations: int,
    stable_iterations: int,
    progress_token_sha256: str | None,
    iteration_records: list[dict[str, object]],
    continuation_token_history: list[str],
) -> dict[str, object]:
    return {
        "schema_version": "ootang_prequential_cycle_status_v3",
        "profile_id": profile["profile_id"],
        "cycle_profile_sha256": profile["_profile_sha256"],
        "base_cycle_v2_profile_sha256": BASE_CYCLE_PROFILE_SHA256,
        "issue_replay_profile_sha256": ISSUE_REPLAY_PROFILE_SHA256,
        "verified_live_profile_sha256": VERIFIED_LIVE_PROFILE_SHA256,
        "artifact_status": profile["artifact_status"],
        "cycle_status": cycle_status,
        "reason": reason,
        "recorded_at_utc": _utc_text(clock),
        "runtime_root": str(paths.root.resolve()),
        "shadow_runtime_root": str(paths.shadow_root.resolve()),
        "machine_only": True,
        "iterations": iterations,
        "stable_iterations": stable_iterations,
        "max_iterations": profile["cycle"]["max_iterations"],
        "progress_token_schema_version": profile["cycle"][
            "progress_token_schema_version"
        ],
        "progress_token_sha256": progress_token_sha256,
        "continuation_token_history": continuation_token_history,
        "iteration_records": iteration_records,
        "calibration_shadow_orchestration_implemented": True,
        "designated_entrypoint_replay_gate_implemented": True,
        "old_live_v1_entrypoint_disabled": False,
        "scheduler_entrypoint_authorization_implemented": False,
        "cross_ledger_commit_is_single_database_atomic": False,
        "formal_warning_output": False,
        "independent_label_used": False,
        "confirmatory_external_validation": False,
        "vajont_used": False,
        "default_pipeline_member": False,
        "runner_independent_checkpoint_inference_replayed": False,
        "trusted_anchor_receipt_verified": False,
        "automatic_epoch_rotation_implemented": False,
        "automatic_calibration_promotion_implemented": False,
        "promotion_performed": False,
        "e2_live_evidence_eligible": False,
        "real_activation_ready": False,
    }


def _write_status(
    profile: Mapping[str, Any], paths: CycleV3RuntimePaths, **kwargs: object
) -> None:
    payload = _status_payload(profile, paths, **kwargs)  # type: ignore[arg-type]
    try:
        cycle_v1._atomic_write_status(paths.cycle_status, payload)
    except OSError as exc:
        raise CycleV3IntegrityError(
            "cannot publish cycle v3 status atomically"
        ) from exc


def _continuation_contract(
    profile: Mapping[str, Any], paths: CycleV3RuntimePaths
) -> dict[str, object]:
    return {
        "schema_version": "ootang_prequential_cycle_status_v3",
        "profile_id": profile["profile_id"],
        "cycle_profile_sha256": profile["_profile_sha256"],
        "base_cycle_v2_profile_sha256": BASE_CYCLE_PROFILE_SHA256,
        "issue_replay_profile_sha256": ISSUE_REPLAY_PROFILE_SHA256,
        "verified_live_profile_sha256": VERIFIED_LIVE_PROFILE_SHA256,
        "artifact_status": profile["artifact_status"],
        "runtime_root": str(paths.root.resolve()),
        "shadow_runtime_root": str(paths.shadow_root.resolve()),
        "machine_only": True,
        "iterations": profile["cycle"]["max_iterations"],
        "stable_iterations": 0,
        "max_iterations": profile["cycle"]["max_iterations"],
        "progress_token_schema_version": profile["cycle"][
            "progress_token_schema_version"
        ],
        "calibration_shadow_orchestration_implemented": True,
        "designated_entrypoint_replay_gate_implemented": True,
        "old_live_v1_entrypoint_disabled": False,
        "scheduler_entrypoint_authorization_implemented": False,
        "cross_ledger_commit_is_single_database_atomic": False,
        "formal_warning_output": False,
        "independent_label_used": False,
        "confirmatory_external_validation": False,
        "vajont_used": False,
        "default_pipeline_member": False,
        "runner_independent_checkpoint_inference_replayed": False,
        "trusted_anchor_receipt_verified": False,
        "automatic_epoch_rotation_implemented": False,
        "automatic_calibration_promotion_implemented": False,
        "promotion_performed": False,
        "e2_live_evidence_eligible": False,
        "real_activation_ready": False,
    }


def _load_continuation_history(
    profile: Mapping[str, Any], paths: CycleV3RuntimePaths
) -> list[str]:
    payload = _strict_runtime_json_snapshot(
        paths.cycle_status, name="cycle v3 status"
    )
    if payload is None or payload.get("cycle_status") != "work_remaining":
        return []
    for key, expected in _continuation_contract(profile, paths).items():
        if payload.get(key) != expected:
            raise CycleV3IntegrityError(f"work-remaining v3 status changed {key}")
    history = payload.get("continuation_token_history")
    terminal = payload.get("progress_token_sha256")
    if (
        not isinstance(history, list)
        or not history
        or any(
            not isinstance(token, str) or HEX_SHA256_RE.fullmatch(token) is None
            for token in history
        )
        or len(history) != len(set(history))
        or history[-1] != terminal
    ):
        raise CycleV3IntegrityError(
            "work-remaining v3 continuation token history is invalid"
        )
    return list(history)


def run_cycle_v3(
    config_path: Path = DEFAULT_CONFIG_PATH,
    *,
    runtime_root: Path | None = None,
    shadow_runtime_root: Path | None = None,
    project_root: Path = ROOT,
    clock: Clock | None = None,
    dependencies: CycleV3Dependencies | None = None,
    progress_token_builder: ProgressTokenBuilder | None = None,
) -> CycleV3Result:
    """Run the thirteen-stage replay-gated cycle to a bounded fixed point."""

    profile = load_cycle_v3_profile(config_path, project_root=project_root)
    if (
        dependencies is not None
        or progress_token_builder is not None
        or clock is not None
    ) and os.environ.get(TEST_OVERRIDE_ENV) != "1":
        raise CycleV3ConfigError(
            "cycle v3 dependency, token, and clock injection is restricted to "
            "explicit test mode"
        )
    paths = runtime_paths_v3(
        profile,
        runtime_root=runtime_root,
        shadow_runtime_root=shadow_runtime_root,
    )
    status_clock = clock or (lambda: datetime.now(timezone.utc))
    lock_handle = _acquire_cycle_lock(paths.cycle_lock)
    lock_acquired = True
    try:
        production_mode = dependencies is None
        iteration_records: list[dict[str, object]] = []
        stable_iterations = 0
        completed_iterations = 0
        latest_token: str | None = None
        current_stages: list[dict[str, object]] = []
        token_history: list[str] = []
        seen_tokens: set[str] = set()
        try:
            token_history = _load_continuation_history(profile, paths)
            seen_tokens = set(token_history)
            latest_token = token_history[-1] if token_history else None
            stage_dependencies = dependencies or _production_dependencies(
                profile, paths
            )
            token_builder = progress_token_builder or (
                lambda: build_progress_token_v3(paths, profile)
            )
            for iteration in range(1, profile["cycle"]["max_iterations"] + 1):
                before = token_builder()
                if (
                    not isinstance(before, str)
                    or HEX_SHA256_RE.fullmatch(before) is None
                ):
                    raise CycleV3IntegrityError(
                        "progress token builder returned an invalid SHA-256"
                    )
                if (
                    latest_token is not None
                    and before != latest_token
                    and before in seen_tokens
                ):
                    raise CycleV3IntegrityError(
                        "scientific progress token oscillated between iterations"
                    )
                if before not in seen_tokens:
                    seen_tokens.add(before)
                    token_history.append(before)
                latest_token = before
                current_stages = []
                for stage_name, kind, base_contract, stage_call in _stage_calls(
                    stage_dependencies
                ):
                    try:
                        raw_outcome = stage_call()
                        if kind == "base":
                            if base_contract is None:  # pragma: no cover
                                raise CycleV3IntegrityError(
                                    f"stage {stage_name} lacks its base contract"
                                )
                            verified = cycle_v1._stage_outcome(
                                base_contract,
                                raw_outcome,
                                paths=paths.base.base,
                                require_status_path=production_mode,
                            )
                            outcome = _StageOutcome(
                                verified.status, verified.status_path
                            )
                        elif kind == "shadow":
                            shadow = cycle_v2._shadow_stage_outcome(
                                raw_outcome,
                                profile=profile["_base_profile_payload"],
                                paths=paths.base,
                                require_status_path=production_mode,
                            )
                            outcome = _StageOutcome(shadow.status, shadow.status_path)
                        elif kind == "replay":
                            outcome = _bound_stage_outcome(
                                raw_outcome,
                                profile=profile,
                                paths=paths,
                                kind="issue replay",
                                require_status_path=production_mode,
                            )
                        elif kind == "verified_live":
                            outcome = _bound_stage_outcome(
                                raw_outcome,
                                profile=profile,
                                paths=paths,
                                kind="verified live",
                                require_status_path=production_mode,
                            )
                        else:  # pragma: no cover - fixed stage table
                            raise CycleV3IntegrityError(
                                f"stage {stage_name} has unknown contract {kind}"
                            )
                    except Exception as exc:
                        if isinstance(exc, CycleV3Error):
                            raise
                        if _is_busy(exc):
                            raise CycleV3BusyError(
                                f"stage {stage_name} is busy: "
                                f"{type(exc).__name__}:{exc}"
                            ) from exc
                        raise CycleV3IntegrityError(
                            f"stage {stage_name} failed: {type(exc).__name__}:{exc}"
                        ) from exc
                    current_stages.append(
                        {
                            "stage": stage_name,
                            "status": outcome.status,
                            "status_path": (
                                str(outcome.status_path.resolve())
                                if outcome.status_path is not None
                                else None
                            ),
                        }
                    )
                after = token_builder()
                if not isinstance(after, str) or HEX_SHA256_RE.fullmatch(after) is None:
                    raise CycleV3IntegrityError(
                        "progress token builder returned an invalid SHA-256"
                    )
                if after != before and after in seen_tokens:
                    raise CycleV3IntegrityError(
                        "scientific progress token oscillated within the bounded cycle"
                    )
                if after not in seen_tokens:
                    seen_tokens.add(after)
                    token_history.append(after)
                completed_iterations = iteration
                latest_token = after
                stable_iterations = stable_iterations + 1 if before == after else 0
                reported_work_remaining = any(
                    record["status"] == "work_remaining" for record in current_stages
                )
                iteration_records.append(
                    {
                        "iteration": iteration,
                        "before_progress_token_sha256": before,
                        "after_progress_token_sha256": after,
                        "scientific_state_changed": before != after,
                        "stages": current_stages,
                    }
                )
                if stable_iterations and reported_work_remaining:
                    raise CycleV3IntegrityError(
                        "a stage reported work_remaining without changing the "
                        "scientific progress token"
                    )
                if stable_iterations >= profile["cycle"]["stable_iterations_to_stop"]:
                    waiting = any(
                        str(record["status"]).startswith("waiting")
                        for record in current_stages
                    )
                    status = "converged_waiting" if waiting else "converged"
                    _write_status(
                        profile,
                        paths,
                        clock=status_clock,
                        cycle_status=status,
                        reason=(
                            "replay_gated_combined_scientific_progress_token_"
                            "reached_fixed_point"
                        ),
                        iterations=completed_iterations,
                        stable_iterations=stable_iterations,
                        progress_token_sha256=latest_token,
                        iteration_records=iteration_records,
                        continuation_token_history=[],
                    )
                    return CycleV3Result(
                        paths.cycle_status,
                        status,
                        completed_iterations,
                        latest_token,
                    )
            _write_status(
                profile,
                paths,
                clock=status_clock,
                cycle_status="work_remaining",
                reason="bounded_cycle_made_unique_replay_gated_scientific_progress",
                iterations=completed_iterations,
                stable_iterations=stable_iterations,
                progress_token_sha256=latest_token,
                iteration_records=iteration_records,
                continuation_token_history=token_history,
            )
            if latest_token is None:  # pragma: no cover - fixed maximum is positive
                raise CycleV3IntegrityError("bounded cycle produced no progress token")
            return CycleV3Result(
                paths.cycle_status,
                "work_remaining",
                completed_iterations,
                latest_token,
            )
        except Exception as exc:
            if isinstance(exc, CycleV3Error):
                normalized = exc
            elif _is_busy(exc):
                normalized = CycleV3BusyError(f"{type(exc).__name__}:{exc}")
            else:
                normalized = CycleV3IntegrityError(
                    f"cycle v3 failed: {type(exc).__name__}:{exc}"
                )
            failure_status = (
                "busy_substage"
                if isinstance(normalized, CycleV3BusyError)
                else "blocked_integrity"
            )
            try:
                _write_status(
                    profile,
                    paths,
                    clock=status_clock,
                    cycle_status=failure_status,
                    reason=f"{type(normalized).__name__}:{normalized}",
                    iterations=completed_iterations,
                    stable_iterations=stable_iterations,
                    progress_token_sha256=latest_token,
                    iteration_records=(
                        iteration_records
                        + (
                            [
                                {
                                    "iteration": completed_iterations + 1,
                                    "before_progress_token_sha256": None,
                                    "after_progress_token_sha256": None,
                                    "scientific_state_changed": None,
                                    "stages": current_stages,
                                }
                            ]
                            if current_stages
                            else []
                        )
                    ),
                    continuation_token_history=token_history,
                )
            except CycleV3Error:
                if isinstance(normalized, CycleV3BusyError):
                    raise normalized
                raise
            raise normalized
    finally:
        active_exception = sys.exc_info()[0] is not None
        release_error: CycleV3IntegrityError | None = None
        try:
            if lock_acquired:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        except OSError as exc:
            release_error = CycleV3IntegrityError(
                "cannot release the shared cycle lock"
            )
            release_error.__cause__ = exc
        try:
            lock_handle.close()
        except OSError as exc:
            if release_error is None:
                release_error = CycleV3IntegrityError(
                    "cannot close the shared cycle lock"
                )
                release_error.__cause__ = exc
        if release_error is not None and not active_exception:
            raise release_error


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--shadow-runtime-root", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = run_cycle_v3(
            args.config,
            runtime_root=args.runtime_root,
            shadow_runtime_root=args.shadow_runtime_root,
        )
    except CycleV3BusyError as exc:
        print(f"[ootang-prequential-cycle-v3] busy: {exc}", file=sys.stderr)
        return 3
    except CycleV3Error as exc:
        print(f"[ootang-prequential-cycle-v3] blocked: {exc}", file=sys.stderr)
        return 2
    print(
        "[ootang-prequential-cycle-v3] "
        f"status={result.status} iterations={result.iterations} "
        f"token={result.progress_token_sha256}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BASE_CYCLE_PROFILE_SHA256",
    "CycleV3BusyError",
    "CycleV3ConfigError",
    "CycleV3Dependencies",
    "CycleV3Error",
    "CycleV3IntegrityError",
    "CycleV3Result",
    "CycleV3RuntimePaths",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_CONFIG_SHA256",
    "EXPECTED_STAGE_ORDER",
    "ISSUE_REPLAY_PROFILE_SHA256",
    "REPLAY_STATUS_ALLOWLIST",
    "TEST_OVERRIDE_ENV",
    "VERIFIED_LIVE_PROFILE_SHA256",
    "VERIFIED_LIVE_STATUS_ALLOWLIST",
    "build_progress_token_v3",
    "load_cycle_v3_profile",
    "main",
    "progress_token_payload_v3",
    "run_cycle_v3",
    "runtime_paths_v3",
]
