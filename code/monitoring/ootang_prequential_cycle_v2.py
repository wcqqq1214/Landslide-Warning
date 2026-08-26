"""Machine-only fixed-point orchestration for the Ootang calibration shadow.

Version 2 is an additive orchestration layer.  It does not alter the reviewed
deploy, live, or cycle-v1 profiles and it continues to use the cycle-v1 outer
lock.  The calibration shadow is reconciled around every live transition so a
crash cannot silently turn a late replay into a prospective issue.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_prequential_cycle as cycle_v1  # noqa: E402


DEFAULT_CONFIG_PATH = ROOT / "config" / "ootang_prequential_cycle.v2.json"
DEFAULT_CONFIG_SHA256 = (
    "875daa95416e58e3c80a4a68f59874047daa1bbb5b395878f6a9265605279242"
)
BASE_CYCLE_PROFILE_SHA256 = (
    "2e4a0da22034a3063f612a723f007bf20b600c1dbdb7c62761368aa7a37810ef"
)
TEST_OVERRIDE_ENV = "OOTANG_E2B_ALLOW_TEST_CYCLE_V2_OVERRIDE"
HEX_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

EXPECTED_STAGE_ORDER = (
    "shadow_before_source",
    "source_ingest",
    "bundle_ensure",
    "live_reconcile_before_outcome",
    "shadow_after_live_before_outcome",
    "outcome_materialize",
    "live_reconcile_after_outcome",
    "shadow_after_outcome",
    "issue_produce",
    "live_seal_issue",
    "shadow_after_issue",
)

SHADOW_STATUS_SCHEMA = "ootang_prequential_calibration_shadow_status_v1"
SHADOW_PROFILE_SCHEMA = "ootang_prequential_calibration_shadow_profile_v1"
SHADOW_PROFILE_ID = "ootang-prequential-calibration-shadow-v1"
SHADOW_STATUS_ALLOWLIST = frozenset(
    {
        "waiting_for_live_prerequisites",
        "waiting_for_live_issue",
        "waiting_for_live_outcome",
        "work_remaining",
        "reconciled",
    }
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
        "calibration_shadow_profile",
        "runtime",
        "cycle",
        "engineering_capabilities",
    }
)
EXPECTED_CAPABILITIES = {
    "calibration_shadow_orchestration_implemented": True,
    "runner_independent_checkpoint_inference_replayed": False,
    "trusted_anchor_receipt_verified": False,
    "automatic_epoch_rotation_implemented": False,
    "automatic_calibration_promotion_implemented": False,
    "e2_live_evidence_eligible": False,
    "real_activation_ready": False,
}
FALSE_EVIDENCE_FLAGS = (
    "formal_warning_output",
    "independent_label_used",
    "confirmatory_external_validation",
    "vajont_used",
    "default_pipeline_member",
    "e2_live_evidence_eligible",
    "real_activation_ready",
    "promotion_performed",
)


class CycleV2Error(RuntimeError):
    """Base error for calibration-shadow orchestration."""


class CycleV2ConfigError(CycleV2Error):
    """The v2 profile or one of its exact bindings is invalid."""


class CycleV2IntegrityError(CycleV2Error):
    """A runtime artifact or transition failed closed."""


class CycleV2BusyError(CycleV2Error):
    """A machine writer changed or owns part of the combined snapshot."""


@dataclass(frozen=True)
class CycleV2RuntimePaths:
    root: Path
    shadow_root: Path
    cycle_status: Path
    cycle_lock: Path
    shadow_status: Path
    base: cycle_v1.CycleRuntimePaths
    base_config: Path
    shadow_config: Path


@dataclass(frozen=True)
class CycleV2Dependencies:
    source_ingest: Callable[[], object]
    bundle_ensure: Callable[[], object]
    live_reconcile: Callable[[], object]
    outcome_materialize: Callable[[], object]
    issue_produce: Callable[[], object]
    shadow_reconcile: Callable[[], object]


@dataclass(frozen=True)
class CycleV2Result:
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
BaseTokenBuilder = Callable[[cycle_v1.CycleRuntimePaths], str]
ShadowPayloadBuilder = Callable[..., Mapping[str, object]]


def _strict_json(path: Path, *, name: str) -> tuple[dict[str, Any], str]:
    try:
        raw, digest = cycle_v1._read_snapshot(path, name=name)
        payload = cycle_v1._decode_json(raw, name=name)
    except (cycle_v1.CycleError, RecursionError) as exc:
        raise CycleV2ConfigError(str(exc)) from exc
    return payload, digest


def _exact_keys(
    value: object, expected: set[str] | frozenset[str], *, name: str
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(expected):
        raise CycleV2ConfigError(f"{name} keys changed")
    return value


def _required_string(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise CycleV2ConfigError(f"{name} must be a non-empty canonical string")
    return value


def _required_hash(value: object, *, name: str) -> str:
    text = _required_string(value, name=name)
    if HEX_SHA256_RE.fullmatch(text) is None:
        raise CycleV2ConfigError(f"{name} must be a lowercase SHA-256")
    return text


def _project_artifact(value: object, *, project_root: Path, name: str) -> Path:
    text = _required_string(value, name=name)
    path = Path(text)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise CycleV2ConfigError(f"{name} must be project-relative")
    root = project_root.resolve()
    try:
        resolved = (root / path).resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise CycleV2ConfigError(f"{name} escapes the project root") from exc
    return resolved


def _relative_runtime_path(root: Path, value: object, *, name: str) -> Path:
    text = _required_string(value, name=name)
    pure = PurePosixPath(text)
    if (
        pure.is_absolute()
        or not pure.parts
        or any(part in {"", ".", ".."} for part in pure.parts)
        or "\\" in text
    ):
        raise CycleV2ConfigError(f"{name} is not a confined relative path")
    current = root
    for part in pure.parts:
        current /= part
        try:
            if current.is_symlink():
                raise CycleV2ConfigError(f"{name} contains a symbolic-link component")
        except OSError as exc:
            raise CycleV2ConfigError(f"{name} cannot be safely inspected") from exc
    try:
        resolved = current.resolve(strict=False)
        resolved.relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError) as exc:
        raise CycleV2ConfigError(f"{name} escapes the runtime root") from exc
    if resolved == root.resolve():
        raise CycleV2ConfigError(f"{name} must be a child of the runtime root")
    return current


def load_cycle_v2_profile(
    path: Path = DEFAULT_CONFIG_PATH, *, project_root: Path = ROOT
) -> dict[str, Any]:
    """Load an exact-key v2 profile and verify both immutable profile bindings."""

    resolved = path.resolve()
    profile, profile_sha256 = _strict_json(resolved, name="cycle v2 profile")
    if (
        resolved == DEFAULT_CONFIG_PATH.resolve()
        and profile_sha256 != DEFAULT_CONFIG_SHA256
    ):
        raise CycleV2ConfigError(
            "default cycle v2 profile differs from the reviewed file; "
            "create a new version"
        )
    _exact_keys(profile, EXPECTED_TOP_KEYS, name="cycle v2 profile")
    expected_scalars = {
        "schema_version": "ootang_prequential_cycle_profile_v2",
        "profile_id": "ootang-prequential-cycle-v2",
        "profile_version": "2.0.0-engineering",
        "case": "ootang",
        "artifact_status": "e2_calibration_shadow_engineering_only_not_live_evidence",
    }
    for key, expected in expected_scalars.items():
        if profile[key] != expected:
            raise CycleV2ConfigError(f"cycle v2 profile {key} changed")
    for flag in (
        "formal_warning_output",
        "independent_label_used",
        "confirmatory_external_validation",
        "vajont_used",
        "default_pipeline_member",
    ):
        if profile[flag] is not False:
            raise CycleV2ConfigError(f"cycle v2 profile {flag} must remain false")

    root = project_root.resolve()
    base_contract = _exact_keys(
        profile["base_cycle_profile"],
        {"path", "expected_sha256"},
        name="base_cycle_profile",
    )
    if (
        _required_hash(
            base_contract["expected_sha256"],
            name="base_cycle_profile.expected_sha256",
        )
        != BASE_CYCLE_PROFILE_SHA256
    ):
        raise CycleV2ConfigError("base cycle v1 expected SHA-256 changed")
    base_path = _project_artifact(
        base_contract["path"], project_root=root, name="base_cycle_profile.path"
    )
    try:
        _, actual_base_sha = cycle_v1._read_snapshot(
            base_path, name="bound base cycle v1 profile"
        )
    except cycle_v1.CycleError as exc:
        raise CycleV2ConfigError(str(exc)) from exc
    if actual_base_sha != BASE_CYCLE_PROFILE_SHA256:
        raise CycleV2ConfigError("bound base cycle v1 profile bytes changed")
    try:
        base_profile = cycle_v1.load_cycle_profile(base_path, project_root=root)
    except cycle_v1.CycleError as exc:
        raise CycleV2ConfigError(f"bound base cycle v1 is invalid: {exc}") from exc

    shadow_contract = _exact_keys(
        profile["calibration_shadow_profile"],
        {"path", "expected_sha256"},
        name="calibration_shadow_profile",
    )
    expected_shadow_sha = _required_hash(
        shadow_contract["expected_sha256"],
        name="calibration_shadow_profile.expected_sha256",
    )
    shadow_path = _project_artifact(
        shadow_contract["path"],
        project_root=root,
        name="calibration_shadow_profile.path",
    )
    shadow_profile, actual_shadow_sha = _strict_json(
        shadow_path, name="bound calibration shadow profile"
    )
    if actual_shadow_sha != expected_shadow_sha:
        raise CycleV2ConfigError("bound calibration shadow profile SHA-256 changed")
    if shadow_profile.get("schema_version") != SHADOW_PROFILE_SCHEMA:
        raise CycleV2ConfigError("bound calibration shadow profile schema changed")
    if shadow_profile.get("profile_id") != SHADOW_PROFILE_ID:
        raise CycleV2ConfigError("bound calibration shadow profile id changed")
    shadow_artifact_status = _required_string(
        shadow_profile.get("artifact_status"), name="shadow artifact_status"
    )
    for flag in (
        "formal_warning_output",
        "independent_label_used",
        "confirmatory_external_validation",
        "vajont_used",
        "default_pipeline_member",
    ):
        if shadow_profile.get(flag) is not False:
            raise CycleV2ConfigError(f"shadow profile {flag} must remain false")
    shadow_runtime = shadow_profile.get("runtime")
    if not isinstance(shadow_runtime, dict):
        raise CycleV2ConfigError("shadow profile runtime is missing")
    shadow_root_relative = _required_string(
        shadow_runtime.get("root"), name="shadow runtime.root"
    )
    shadow_status_relative = _required_string(
        shadow_runtime.get("status"), name="shadow runtime.status"
    )

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
        raise CycleV2ConfigError("cycle v2 stage order changed")
    fixed_cycle = {
        "max_iterations": 64,
        "stable_iterations_to_stop": 1,
        "progress_token_schema_version": "ootang_prequential_cycle_progress_v2",
        "busy_exit_code": 3,
        "blocked_exit_code": 2,
    }
    for key, expected in fixed_cycle.items():
        value = cycle[key]
        if value != expected or isinstance(value, bool) and isinstance(expected, int):
            raise CycleV2ConfigError(f"cycle v2 {key} changed")
    if profile["engineering_capabilities"] != EXPECTED_CAPABILITIES:
        raise CycleV2ConfigError("cycle v2 engineering capability boundary changed")

    profile["_profile_path"] = str(resolved)
    profile["_profile_sha256"] = profile_sha256
    profile["_project_root"] = str(root)
    profile["_base_profile_path"] = str(base_path)
    profile["_base_profile_payload"] = base_profile
    profile["_shadow_profile_path"] = str(shadow_path)
    profile["_shadow_profile_sha256"] = actual_shadow_sha
    profile["_shadow_profile_payload"] = shadow_profile
    profile["_shadow_artifact_status"] = shadow_artifact_status
    profile["_shadow_root_relative"] = shadow_root_relative
    profile["_shadow_status_relative"] = shadow_status_relative
    return profile


def runtime_paths_v2(
    profile: Mapping[str, Any],
    *,
    runtime_root: Path | None = None,
    shadow_runtime_root: Path | None = None,
) -> CycleV2RuntimePaths:
    base_profile = profile["_base_profile_payload"]
    if not isinstance(base_profile, dict):
        raise CycleV2ConfigError("bound base profile payload is missing")
    try:
        base = cycle_v1.runtime_paths(base_profile, runtime_root=runtime_root)
    except cycle_v1.CycleError as exc:
        raise CycleV2ConfigError(str(exc)) from exc
    cycle_status = _relative_runtime_path(
        base.root, profile["runtime"]["cycle_status"], name="runtime.cycle_status"
    )
    project_root = Path(str(profile["_project_root"])).resolve()
    shadow_root = (
        shadow_runtime_root.resolve()
        if shadow_runtime_root is not None
        else _project_artifact(
            profile["_shadow_root_relative"],
            project_root=project_root,
            name="shadow runtime.root",
        )
    )
    shadow_status = _relative_runtime_path(
        shadow_root,
        profile["_shadow_status_relative"],
        name="shadow runtime.status",
    )
    occupied = {value for value in vars(base).values() if isinstance(value, Path)}
    if (
        cycle_status in occupied
        or shadow_status in occupied
        or cycle_status == shadow_status
    ):
        raise CycleV2ConfigError("cycle v2 and shadow runtime paths collide")
    return CycleV2RuntimePaths(
        root=base.root,
        shadow_root=shadow_root,
        cycle_status=cycle_status,
        cycle_lock=base.cycle_lock,
        shadow_status=shadow_status,
        base=base,
        base_config=Path(str(profile["_base_profile_path"])),
        shadow_config=Path(str(profile["_shadow_profile_path"])),
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
        raise CycleV2IntegrityError(
            "combined progress state is not canonical finite JSON"
        ) from exc


def _production_shadow_payload(
    *,
    config_path: Path,
    runtime_root: Path,
    live_runtime_root: Path,
    project_root: Path,
) -> Mapping[str, object]:
    from monitoring import ootang_prequential_calibration_shadow as shadow

    return shadow.shadow_progress_token_payload(
        config_path=config_path,
        runtime_root=runtime_root,
        live_runtime_root=live_runtime_root,
        project_root=project_root,
    )


def progress_token_payload_v2(
    paths: CycleV2RuntimePaths,
    profile: Mapping[str, Any],
    *,
    base_token_builder: BaseTokenBuilder | None = None,
    shadow_payload_builder: ShadowPayloadBuilder | None = None,
) -> dict[str, object]:
    """Build one combined snapshot with a base-before/shadow/base-after fence."""

    base_builder = base_token_builder or cycle_v1.build_progress_token
    shadow_builder = shadow_payload_builder or _production_shadow_payload
    try:
        base_before = base_builder(paths.base)
        shadow_payload = shadow_builder(
            config_path=paths.shadow_config,
            runtime_root=paths.shadow_root,
            live_runtime_root=paths.root,
            project_root=Path(str(profile["_project_root"])),
        )
        base_after = base_builder(paths.base)
    except CycleV2Error:
        raise
    except Exception as exc:
        if _is_busy(exc):
            raise CycleV2BusyError(
                f"combined progress snapshot is busy: {type(exc).__name__}:{exc}"
            ) from exc
        raise CycleV2IntegrityError(
            f"combined progress snapshot failed: {type(exc).__name__}:{exc}"
        ) from exc
    for name, value in (("base_before", base_before), ("base_after", base_after)):
        if not isinstance(value, str) or HEX_SHA256_RE.fullmatch(value) is None:
            raise CycleV2IntegrityError(f"{name} is not a SHA-256")
    if base_before != base_after:
        raise CycleV2BusyError(
            "base scientific state changed across the shadow snapshot"
        )
    if not isinstance(shadow_payload, Mapping):
        raise CycleV2IntegrityError("shadow progress payload is not a mapping")
    try:
        shadow_copy = dict(shadow_payload)
    except (RecursionError, TypeError, ValueError) as exc:
        raise CycleV2IntegrityError(
            "shadow progress payload cannot be materialized"
        ) from exc
    canonical_shadow = json.loads(_canonical_bytes(shadow_copy))
    return {
        "schema_version": "ootang_prequential_cycle_progress_v2",
        "base_cycle_progress_sha256": base_after,
        "calibration_shadow_profile_sha256": profile["_shadow_profile_sha256"],
        "verified_calibration_shadow_projection": canonical_shadow,
    }


def build_progress_token_v2(
    paths: CycleV2RuntimePaths,
    profile: Mapping[str, Any],
    *,
    base_token_builder: BaseTokenBuilder | None = None,
    shadow_payload_builder: ShadowPayloadBuilder | None = None,
) -> str:
    payload = progress_token_payload_v2(
        paths,
        profile,
        base_token_builder=base_token_builder,
        shadow_payload_builder=shadow_payload_builder,
    )
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _read_shadow_stage_status(
    path: Path,
    *,
    expected_path: Path,
    profile: Mapping[str, Any],
    paths: CycleV2RuntimePaths,
) -> _StageOutcome:
    try:
        resolved = path.resolve(strict=True)
        expected = expected_path.resolve(strict=False)
    except OSError as exc:
        raise CycleV2IntegrityError(
            "shadow returned an unreadable status path"
        ) from exc
    if resolved != expected or not resolved.is_file():
        raise CycleV2IntegrityError(
            "shadow returned a status path outside its contract"
        )
    try:
        payload = cycle_v1._strict_runtime_json(path, name="shadow stage status")
    except cycle_v1.CycleError as exc:
        raise CycleV2IntegrityError(str(exc)) from exc
    if payload is None:
        raise CycleV2IntegrityError("shadow returned a missing status path")
    if payload.get("schema_version") != SHADOW_STATUS_SCHEMA:
        raise CycleV2IntegrityError("shadow status schema changed")
    if payload.get("profile_id") != SHADOW_PROFILE_ID:
        raise CycleV2IntegrityError("shadow status profile id changed")
    if payload.get("artifact_status") != profile["_shadow_artifact_status"]:
        raise CycleV2IntegrityError("shadow status artifact boundary changed")
    if payload.get("profile_sha256") != profile["_shadow_profile_sha256"]:
        raise CycleV2IntegrityError("shadow status profile binding changed")
    if "runtime_root" in payload and payload["runtime_root"] != str(
        paths.shadow_root.resolve()
    ):
        raise CycleV2IntegrityError("shadow status runtime root changed")
    value = payload.get("runner_status")
    if value not in SHADOW_STATUS_ALLOWLIST:
        raise CycleV2IntegrityError(
            f"shadow returned unknown or fatal status {value!r}"
        )
    for flag in FALSE_EVIDENCE_FLAGS:
        if flag in payload and payload[flag] is not False:
            raise CycleV2IntegrityError(
                f"shadow status claimed forbidden capability {flag}"
            )
    return _StageOutcome(str(value), resolved)


def _shadow_stage_outcome(
    value: object,
    *,
    profile: Mapping[str, Any],
    paths: CycleV2RuntimePaths,
    require_status_path: bool,
) -> _StageOutcome:
    if isinstance(value, _StageOutcome):
        outcome = value
    elif isinstance(value, cycle_v1.StageOutcome):
        outcome = _StageOutcome(value.status, value.status_path)
    elif isinstance(value, Path):
        outcome = _read_shadow_stage_status(
            value,
            expected_path=paths.shadow_status,
            profile=profile,
            paths=paths,
        )
    elif isinstance(value, str):
        outcome = _StageOutcome(value)
    else:
        status = getattr(value, "status", None)
        status_path = getattr(value, "status_path", None)
        if not isinstance(status, str) or not status:
            raise CycleV2IntegrityError("shadow returned an unsupported result")
        if status_path is not None and not isinstance(status_path, Path):
            status_path = Path(status_path)
        outcome = _StageOutcome(status, status_path)
    if outcome.status not in SHADOW_STATUS_ALLOWLIST:
        raise CycleV2IntegrityError(
            f"shadow returned unknown or fatal status {outcome.status!r}"
        )
    if outcome.status_path is not None:
        verified = _read_shadow_stage_status(
            outcome.status_path,
            expected_path=paths.shadow_status,
            profile=profile,
            paths=paths,
        )
        if verified.status != outcome.status:
            raise CycleV2IntegrityError(
                "shadow result disagrees with its durable status"
            )
        outcome = verified
    elif require_status_path:
        raise CycleV2IntegrityError("shadow did not return a status path")
    return outcome


def _production_dependencies(
    profile: Mapping[str, Any],
    paths: CycleV2RuntimePaths,
) -> CycleV2Dependencies:
    try:
        base = cycle_v1._production_dependencies(
            profile["_base_profile_payload"],
            config_path=paths.base_config,
            paths=paths.base,
            project_root=Path(str(profile["_project_root"])),
        )
    except cycle_v1.CycleError as exc:
        raise CycleV2IntegrityError(str(exc)) from exc

    def shadow_reconcile() -> object:
        from monitoring import ootang_prequential_calibration_shadow as shadow

        return shadow.reconcile_calibration_shadow(
            config_path=paths.shadow_config,
            runtime_root=paths.shadow_root,
            live_runtime_root=paths.root,
            project_root=Path(str(profile["_project_root"])),
        )

    return CycleV2Dependencies(
        source_ingest=base.source_ingest,
        bundle_ensure=base.bundle_ensure,
        live_reconcile=base.live_reconcile,
        outcome_materialize=base.outcome_materialize,
        issue_produce=base.issue_produce,
        shadow_reconcile=shadow_reconcile,
    )


def _stage_calls(
    dependencies: CycleV2Dependencies,
) -> tuple[tuple[str, str | None, Callable[[], object]], ...]:
    return (
        ("shadow_before_source", None, dependencies.shadow_reconcile),
        ("source_ingest", "source_ingest", dependencies.source_ingest),
        ("bundle_ensure", "bundle_ensure", dependencies.bundle_ensure),
        (
            "live_reconcile_before_outcome",
            "live_reconcile_before_outcome",
            dependencies.live_reconcile,
        ),
        (
            "shadow_after_live_before_outcome",
            None,
            dependencies.shadow_reconcile,
        ),
        (
            "outcome_materialize",
            "outcome_materialize",
            dependencies.outcome_materialize,
        ),
        (
            "live_reconcile_after_outcome",
            "live_reconcile_after_outcome",
            dependencies.live_reconcile,
        ),
        ("shadow_after_outcome", None, dependencies.shadow_reconcile),
        ("issue_produce", "issue_produce", dependencies.issue_produce),
        ("live_seal_issue", "live_seal_issue", dependencies.live_reconcile),
        ("shadow_after_issue", None, dependencies.shadow_reconcile),
    )


def _is_busy(exc: BaseException) -> bool:
    if isinstance(exc, CycleV2BusyError):
        return True
    if isinstance(exc, cycle_v1.CycleBusyError):
        return True
    return cycle_v1._is_busy(exc) or type(exc).__name__ in {
        "CalibrationShadowBusy",
        "CalibrationShadowBusyError",
        "ShadowLedgerBusyError",
    }


def _utc_text(clock: Clock) -> str:
    value = clock()
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise CycleV2IntegrityError(
            "machine clock must return a timezone-aware datetime"
        )
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _status_payload(
    profile: Mapping[str, Any],
    paths: CycleV2RuntimePaths,
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
        "schema_version": "ootang_prequential_cycle_status_v2",
        "profile_id": profile["profile_id"],
        "cycle_profile_sha256": profile["_profile_sha256"],
        "base_cycle_profile_sha256": BASE_CYCLE_PROFILE_SHA256,
        "calibration_shadow_profile_sha256": profile["_shadow_profile_sha256"],
        "artifact_status": profile["artifact_status"],
        "cycle_status": cycle_status,
        "reason": reason,
        "recorded_at_utc": _utc_text(clock),
        "runtime_root": str(paths.root.resolve()),
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
    profile: Mapping[str, Any], paths: CycleV2RuntimePaths, **kwargs: object
) -> None:
    payload = _status_payload(profile, paths, **kwargs)  # type: ignore[arg-type]
    try:
        cycle_v1._atomic_write_status(paths.cycle_status, payload)
    except OSError as exc:
        raise CycleV2IntegrityError(
            "cannot publish cycle v2 status atomically"
        ) from exc


def _load_continuation_history(
    profile: Mapping[str, Any], paths: CycleV2RuntimePaths
) -> list[str]:
    try:
        payload = cycle_v1._strict_runtime_json(
            paths.cycle_status, name="cycle v2 status"
        )
    except cycle_v1.CycleError as exc:
        raise CycleV2IntegrityError(str(exc)) from exc
    if payload is None or payload.get("cycle_status") != "work_remaining":
        return []
    expected = {
        "schema_version": "ootang_prequential_cycle_status_v2",
        "profile_id": profile["profile_id"],
        "cycle_profile_sha256": profile["_profile_sha256"],
        "base_cycle_profile_sha256": BASE_CYCLE_PROFILE_SHA256,
        "calibration_shadow_profile_sha256": profile["_shadow_profile_sha256"],
        "artifact_status": profile["artifact_status"],
        "runtime_root": str(paths.root.resolve()),
        "machine_only": True,
        "iterations": profile["cycle"]["max_iterations"],
        "stable_iterations": 0,
        "max_iterations": profile["cycle"]["max_iterations"],
        "progress_token_schema_version": profile["cycle"][
            "progress_token_schema_version"
        ],
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
    for key, expected_value in expected.items():
        if payload.get(key) != expected_value:
            raise CycleV2IntegrityError(f"work-remaining v2 status changed {key}")
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
        raise CycleV2IntegrityError(
            "work-remaining v2 continuation token history is invalid"
        )
    return list(history)


def run_cycle_v2(
    config_path: Path = DEFAULT_CONFIG_PATH,
    *,
    runtime_root: Path | None = None,
    shadow_runtime_root: Path | None = None,
    project_root: Path = ROOT,
    clock: Clock | None = None,
    dependencies: CycleV2Dependencies | None = None,
    progress_token_builder: ProgressTokenBuilder | None = None,
) -> CycleV2Result:
    """Run the eleven-stage calibration-shadow cycle to a bounded fixed point."""

    profile = load_cycle_v2_profile(config_path, project_root=project_root)
    if (
        dependencies is not None
        or progress_token_builder is not None
        or clock is not None
    ) and os.environ.get(TEST_OVERRIDE_ENV) != "1":
        raise CycleV2ConfigError(
            "cycle v2 dependency, token, and clock injection is restricted to "
            "explicit test mode"
        )
    paths = runtime_paths_v2(
        profile,
        runtime_root=runtime_root,
        shadow_runtime_root=shadow_runtime_root,
    )
    status_clock = clock or (lambda: datetime.now(timezone.utc))
    try:
        paths.cycle_lock.parent.mkdir(parents=True, exist_ok=True)
        lock_handle = paths.cycle_lock.open("a+b")
    except OSError as exc:
        raise CycleV2IntegrityError("cannot open the shared cycle lock") from exc
    lock_acquired = False
    try:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock_acquired = True
        except BlockingIOError as exc:
            raise CycleV2BusyError(
                "another v1 or v2 machine cycle owns the shared outer lock"
            ) from exc
        except OSError as exc:
            raise CycleV2IntegrityError("cannot acquire the shared cycle lock") from exc

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
                lambda: build_progress_token_v2(paths, profile)
            )
            for iteration in range(1, profile["cycle"]["max_iterations"] + 1):
                before = token_builder()
                if (
                    not isinstance(before, str)
                    or HEX_SHA256_RE.fullmatch(before) is None
                ):
                    raise CycleV2IntegrityError(
                        "progress token builder returned an invalid SHA-256"
                    )
                if (
                    latest_token is not None
                    and before != latest_token
                    and before in seen_tokens
                ):
                    raise CycleV2IntegrityError(
                        "scientific progress token oscillated between iterations"
                    )
                if before not in seen_tokens:
                    seen_tokens.add(before)
                    token_history.append(before)
                latest_token = before
                current_stages = []
                for stage_name, base_contract, stage_call in _stage_calls(
                    stage_dependencies
                ):
                    try:
                        raw_outcome = stage_call()
                        if base_contract is None:
                            outcome = _shadow_stage_outcome(
                                raw_outcome,
                                profile=profile,
                                paths=paths,
                                require_status_path=production_mode,
                            )
                        else:
                            verified = cycle_v1._stage_outcome(
                                base_contract,
                                raw_outcome,
                                paths=paths.base,
                                require_status_path=production_mode,
                            )
                            outcome = _StageOutcome(
                                verified.status, verified.status_path
                            )
                    except Exception as exc:
                        if isinstance(exc, CycleV2Error):
                            raise
                        if _is_busy(exc):
                            raise CycleV2BusyError(
                                f"stage {stage_name} is busy: "
                                f"{type(exc).__name__}:{exc}"
                            ) from exc
                        raise CycleV2IntegrityError(
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
                    raise CycleV2IntegrityError(
                        "progress token builder returned an invalid SHA-256"
                    )
                if after != before and after in seen_tokens:
                    raise CycleV2IntegrityError(
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
                    raise CycleV2IntegrityError(
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
                        reason="combined_scientific_progress_token_reached_fixed_point",
                        iterations=completed_iterations,
                        stable_iterations=stable_iterations,
                        progress_token_sha256=latest_token,
                        iteration_records=iteration_records,
                        continuation_token_history=[],
                    )
                    return CycleV2Result(
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
                reason="bounded_cycle_made_unique_combined_scientific_progress",
                iterations=completed_iterations,
                stable_iterations=stable_iterations,
                progress_token_sha256=latest_token,
                iteration_records=iteration_records,
                continuation_token_history=token_history,
            )
            if latest_token is None:  # pragma: no cover - max_iterations is positive
                raise CycleV2IntegrityError("bounded cycle produced no progress token")
            return CycleV2Result(
                paths.cycle_status,
                "work_remaining",
                completed_iterations,
                latest_token,
            )
        except Exception as exc:
            if isinstance(exc, CycleV2Error):
                normalized = exc
            elif _is_busy(exc):
                normalized = CycleV2BusyError(f"{type(exc).__name__}:{exc}")
            else:
                normalized = CycleV2IntegrityError(
                    f"cycle v2 failed: {type(exc).__name__}:{exc}"
                )
            failure_status = (
                "busy_substage"
                if isinstance(normalized, CycleV2BusyError)
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
            except CycleV2Error:
                if isinstance(normalized, CycleV2BusyError):
                    raise normalized
                raise
            raise normalized
    finally:
        active_exception = sys.exc_info()[0] is not None
        release_error: CycleV2IntegrityError | None = None
        try:
            if lock_acquired:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        except OSError as exc:
            release_error = CycleV2IntegrityError(
                "cannot release the shared cycle lock"
            )
            release_error.__cause__ = exc
        try:
            lock_handle.close()
        except OSError as exc:
            if release_error is None:
                release_error = CycleV2IntegrityError(
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
        result = run_cycle_v2(
            args.config,
            runtime_root=args.runtime_root,
            shadow_runtime_root=args.shadow_runtime_root,
        )
    except CycleV2BusyError as exc:
        print(f"[ootang-prequential-cycle-v2] busy: {exc}", file=sys.stderr)
        return 3
    except CycleV2Error as exc:
        print(f"[ootang-prequential-cycle-v2] blocked: {exc}", file=sys.stderr)
        return 2
    print(
        "[ootang-prequential-cycle-v2] "
        f"status={result.status} iterations={result.iterations} "
        f"token={result.progress_token_sha256}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BASE_CYCLE_PROFILE_SHA256",
    "CycleV2BusyError",
    "CycleV2ConfigError",
    "CycleV2Dependencies",
    "CycleV2Error",
    "CycleV2IntegrityError",
    "CycleV2Result",
    "CycleV2RuntimePaths",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_CONFIG_SHA256",
    "EXPECTED_STAGE_ORDER",
    "SHADOW_STATUS_ALLOWLIST",
    "TEST_OVERRIDE_ENV",
    "build_progress_token_v2",
    "load_cycle_v2_profile",
    "main",
    "progress_token_payload_v2",
    "run_cycle_v2",
    "runtime_paths_v2",
]
