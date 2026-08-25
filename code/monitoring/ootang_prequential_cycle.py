"""Machine-only bounded fixed-point orchestration for Ootang E2-B2.

The cycle owns one outer, non-blocking lock and invokes each existing machine
stage through its public API without pre-holding a child lock.  Between stage
calls it briefly acquires deploy then live-runner lock to take an untorn
scientific snapshot.  Convergence excludes poll timestamps and status bytes.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

DEFAULT_CONFIG_PATH = ROOT / "config" / "ootang_prequential_cycle.v1.json"
DEFAULT_CONFIG_SHA256 = (
    "2e4a0da22034a3063f612a723f007bf20b600c1dbdb7c62761368aa7a37810ef"
)
DEPLOY_PROFILE_SHA256 = (
    "60f17602998e976f06d590b7611dfb4480505c21d41a9b05420bd93cf831f940"
)
LIVE_PROFILE_SHA256 = (
    "bf7c60a19e26e9a54fc4e1980b3556d6e6d1e3fec4b3a3a7f3de0dbb9b83cf00"
)
TEST_OVERRIDE_ENV = "OOTANG_E2B_ALLOW_TEST_CYCLE_OVERRIDE"
ZERO_HASH = "0" * 64
HEX_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

EXPECTED_STAGE_ORDER = (
    "source_ingest",
    "bundle_ensure",
    "live_reconcile_before_outcome",
    "outcome_materialize",
    "live_reconcile_after_outcome",
    "issue_produce",
    "live_seal_issue",
)
STAGE_STATUS_CONTRACTS = {
    "source_ingest": (
        "status",
        "ootang_source_ingest_status_v1",
        frozenset(
            {
                "waiting_for_daily_finalized_feed",
                "waiting_for_post_baseline_row",
                "ready",
            }
        ),
    ),
    "bundle_ensure": (
        "bundle_status",
        "ootang_model_bundle_status_v1",
        frozenset({"waiting_for_semantically_validated_source", "ready"}),
    ),
    "live_reconcile_before_outcome": (
        "runner_status",
        "ootang_prequential_live_status_v1",
        frozenset(
            {
                "waiting_for_production_bundle_or_source_snapshot",
                "waiting_for_outcome",
                "waiting_for_missing_natural_day",
                "waiting_for_new_data",
                "waiting_for_backfill_outcome",
            }
        ),
    ),
    "outcome_materialize": (
        "materializer_status",
        "ootang_outcome_materializer_status_v1",
        frozenset(
            {
                "waiting_for_source_model_or_ledger",
                "waiting_for_finalized_outcome",
                "waiting_epoch_rotation_required",
                "materialized",
                "already_materialized_idempotent",
            }
        ),
    ),
    "live_reconcile_after_outcome": (
        "runner_status",
        "ootang_prequential_live_status_v1",
        frozenset(
            {
                "waiting_for_production_bundle_or_source_snapshot",
                "waiting_for_outcome",
                "waiting_for_missing_natural_day",
                "waiting_for_new_data",
                "waiting_for_backfill_outcome",
            }
        ),
    ),
    "issue_produce": (
        "producer_status",
        "ootang_issue_producer_status_v1",
        frozenset(
            {
                "waiting_for_source_or_model",
                "waiting_for_issuable_future_target",
                "issued",
                "already_issued_idempotent",
            }
        ),
    ),
    "live_seal_issue": (
        "runner_status",
        "ootang_prequential_live_status_v1",
        frozenset(
            {
                "waiting_for_production_bundle_or_source_snapshot",
                "waiting_for_outcome",
                "waiting_for_missing_natural_day",
                "waiting_for_new_data",
                "waiting_for_backfill_outcome",
            }
        ),
    ),
}
STAGE_STATUS_METADATA = {
    "source_ingest": (
        "ootang-prequential-deploy-v1",
        "e2b_engineering_only_not_live_evidence",
        "deploy_profile_sha256",
        DEPLOY_PROFILE_SHA256,
    ),
    "bundle_ensure": (
        "ootang-prequential-deploy-v1",
        "e2b_engineering_only_not_live_evidence",
        "deploy_profile_sha256",
        DEPLOY_PROFILE_SHA256,
    ),
    "live_reconcile_before_outcome": (
        "ootang-prequential-live-v1",
        "e2a_engineering_only_not_live_evidence",
        "profile_file_sha256",
        LIVE_PROFILE_SHA256,
    ),
    "outcome_materialize": (
        "ootang-prequential-cycle-v1",
        "e2b2_engineering_only_not_live_evidence",
        "cycle_profile_sha256",
        DEFAULT_CONFIG_SHA256,
    ),
    "live_reconcile_after_outcome": (
        "ootang-prequential-live-v1",
        "e2a_engineering_only_not_live_evidence",
        "profile_file_sha256",
        LIVE_PROFILE_SHA256,
    ),
    "issue_produce": (
        "ootang-prequential-deploy-v1",
        "e2b_engineering_only_not_live_evidence",
        "deploy_profile_sha256",
        DEPLOY_PROFILE_SHA256,
    ),
    "live_seal_issue": (
        "ootang-prequential-live-v1",
        "e2a_engineering_only_not_live_evidence",
        "profile_file_sha256",
        LIVE_PROFILE_SHA256,
    ),
}
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
        "deploy_profile",
        "live_profile",
        "runtime",
        "outcome",
        "cycle",
        "engineering_capabilities",
    }
)
EXPECTED_RUNTIME_KEYS = frozenset(
    {
        "root",
        "outcome_inbox",
        "outcome_status",
        "outcome_receipts",
        "objects",
        "cycle_status",
        "cycle_lock",
    }
)
EXPECTED_OUTCOME_CONTRACT = {
    "schema_version": "ootang_live_outcome_batch_v1",
    "input_manifest_schema_version": "ootang_outcome_input_manifest_v1",
    "receipt_schema_version": "ootang_outcome_materializer_receipt_v1",
    "target_selection_policy": (
        "revision_first_then_outstanding_then_contiguous_backfill"
    ),
    "require_current_source_record": True,
    "require_finalized_at_or_before_machine_time": True,
    "same_revision_same_semantics": "idempotent_preserve_first_bytes",
    "same_revision_changed_semantics": "blocked_integrity",
    "active_date_pointer_policy": (
        "atomic_replace_after_prior_receipt_verification"
    ),
    "atomic_write": True,
}
EXPECTED_CAPABILITIES = {
    "outcome_materializer_implemented": True,
    "automatic_cycle_orchestration_implemented": True,
    "runner_independent_checkpoint_inference_replayed": False,
    "trusted_anchor_receipt_verified": False,
    "automatic_epoch_rotation_implemented": False,
    "e2_live_evidence_eligible": False,
    "real_activation_ready": False,
}

class CycleError(RuntimeError):
    """Base class for fail-closed cycle errors."""


class CycleConfigError(CycleError):
    """The versioned cycle contract or a bound profile changed."""


class CycleIntegrityError(CycleError):
    """A present runtime artifact or stage transition is unsafe."""


class CycleBusyError(CycleError):
    """Another process owns the cycle or one of its machine stages."""


@dataclass(frozen=True)
class CycleRuntimePaths:
    root: Path
    live_config: Path
    cycle_status: Path
    cycle_lock: Path
    deploy_lock: Path
    runner_lock: Path
    source_pointer: Path
    source_status: Path
    model_manifest: Path
    bundle_status: Path
    ledger: Path
    live_status: Path
    issue_inbox: Path
    issue_receipts: Path
    issue_status: Path
    outcome_inbox: Path
    outcome_receipts: Path
    outcome_status: Path
    objects: Path


@dataclass(frozen=True)
class StageOutcome:
    status: str
    status_path: Path | None = None


StageCall = Callable[[], object]


@dataclass(frozen=True)
class CycleDependencies:
    source_ingest: StageCall
    bundle_ensure: StageCall
    live_reconcile: StageCall
    outcome_materialize: StageCall
    issue_produce: StageCall


@dataclass(frozen=True)
class CycleResult:
    status_path: Path
    status: str
    iterations: int
    progress_token_sha256: str


Clock = Callable[[], datetime]
ProgressTokenBuilder = Callable[[], str]


def _reject_constant(value: str) -> None:
    raise CycleConfigError(f"forbidden non-finite JSON constant: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CycleConfigError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: object, *, name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise CycleConfigError(f"{name} contains a non-finite number")
    if isinstance(value, dict):
        for key, child in value.items():
            _reject_nonfinite(child, name=f"{name}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_nonfinite(child, name=f"{name}[{index}]")


def _decode_json(raw: bytes, *, name: str) -> dict[str, Any]:
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise CycleConfigError(f"{name} is not UTF-8 JSON") from exc
    except json.JSONDecodeError as exc:
        raise CycleConfigError(f"{name} is not strict JSON") from exc
    if not isinstance(payload, dict):
        raise CycleConfigError(f"{name} must be a JSON object")
    _reject_nonfinite(payload, name=name)
    return payload


def _read_snapshot(path: Path, *, name: str) -> tuple[bytes, str]:
    try:
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            raw = handle.read()
            after = os.fstat(handle.fileno())
    except OSError as exc:
        raise CycleIntegrityError(f"cannot read {name}: {path}") from exc
    stable = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) == (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )
    if not stable or len(raw) != after.st_size:
        raise CycleIntegrityError(f"{name} changed while being read")
    return raw, hashlib.sha256(raw).hexdigest()


def _sha256_path(path: Path, *, name: str) -> str:
    return _read_snapshot(path, name=name)[1]


def _exact_keys(
    value: object, expected: frozenset[str] | set[str], *, name: str
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(expected):
        raise CycleConfigError(f"{name} keys changed")
    return value


def _required_string(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise CycleConfigError(f"{name} must be a non-empty canonical string")
    return value


def _artifact_path(
    value: object, *, project_root: Path, name: str
) -> Path:
    text = _required_string(value, name=name)
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        raise CycleConfigError(f"{name} must be a project-relative path")
    root = project_root.resolve()
    try:
        resolved = (root / path).resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise CycleConfigError(f"{name} escapes the project root") from exc
    return resolved


def _load_bound_json(path: Path, *, name: str) -> tuple[dict[str, Any], str]:
    raw, digest = _read_snapshot(path, name=name)
    try:
        return _decode_json(raw, name=name), digest
    except CycleConfigError:
        raise
    except Exception as exc:  # pragma: no cover - defensive normalization
        raise CycleConfigError(f"cannot decode {name}") from exc


def load_cycle_profile(
    path: Path = DEFAULT_CONFIG_PATH, *, project_root: Path = ROOT
) -> dict[str, Any]:
    """Load the exact reviewed cycle profile and verify both bound profiles."""

    resolved = path.resolve()
    try:
        raw, profile_sha256 = _read_snapshot(resolved, name="cycle profile")
    except CycleIntegrityError as exc:
        raise CycleConfigError(str(exc)) from exc
    if profile_sha256 != DEFAULT_CONFIG_SHA256:
        raise CycleConfigError(
            "cycle profile differs from the reviewed v1 file; create a new version"
        )
    profile = _decode_json(raw, name="cycle profile")
    _exact_keys(profile, EXPECTED_TOP_KEYS, name="cycle profile")
    expected_scalars = {
        "schema_version": "ootang_prequential_cycle_profile_v1",
        "profile_id": "ootang-prequential-cycle-v1",
        "profile_version": "1.0.0-engineering",
        "case": "ootang",
        "artifact_status": "e2b2_engineering_only_not_live_evidence",
    }
    for key, expected in expected_scalars.items():
        if profile[key] != expected:
            raise CycleConfigError(f"cycle profile {key} changed")
    for key in (
        "formal_warning_output",
        "independent_label_used",
        "confirmatory_external_validation",
        "vajont_used",
        "default_pipeline_member",
    ):
        if profile[key] is not False:
            raise CycleConfigError(f"cycle profile {key} must remain false")

    root = project_root.resolve()
    bound_profiles: dict[str, tuple[str, str]] = {
        "deploy_profile": (DEPLOY_PROFILE_SHA256, "ootang_prequential_deploy_profile_v1"),
        "live_profile": (LIVE_PROFILE_SHA256, "ootang_prequential_live_profile_v1"),
    }
    loaded: dict[str, dict[str, Any]] = {}
    for key, (expected_sha, expected_schema) in bound_profiles.items():
        contract = _exact_keys(
            profile[key], {"path", "expected_sha256"}, name=key
        )
        if contract["expected_sha256"] != expected_sha:
            raise CycleConfigError(f"{key} expected SHA-256 changed")
        artifact = _artifact_path(
            contract["path"], project_root=root, name=f"{key}.path"
        )
        try:
            payload, actual_sha = _load_bound_json(artifact, name=key)
        except CycleIntegrityError as exc:
            raise CycleConfigError(str(exc)) from exc
        if actual_sha != expected_sha:
            raise CycleConfigError(f"bound {key} SHA-256 changed")
        if payload.get("schema_version") != expected_schema:
            raise CycleConfigError(f"bound {key} schema changed")
        loaded[key] = payload

    runtime = _exact_keys(profile["runtime"], EXPECTED_RUNTIME_KEYS, name="runtime")
    for key, value in runtime.items():
        _required_string(value, name=f"runtime.{key}")
    deploy_runtime = loaded["deploy_profile"].get("runtime")
    live_runtime = loaded["live_profile"].get("runtime")
    if not isinstance(deploy_runtime, dict) or not isinstance(live_runtime, dict):
        raise CycleConfigError("bound runtime contracts are missing")
    if not (
        runtime["root"] == deploy_runtime.get("root") == live_runtime.get("root")
    ):
        raise CycleConfigError("cycle/deploy/live runtime roots differ")
    if runtime["outcome_inbox"] != live_runtime.get("outcome_inbox"):
        raise CycleConfigError("cycle/live outcome inboxes differ")
    if runtime["objects"] != deploy_runtime.get("objects"):
        raise CycleConfigError("cycle/deploy object stores differ")

    if profile["outcome"] != EXPECTED_OUTCOME_CONTRACT:
        raise CycleConfigError("outcome materialization contract changed")
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
        raise CycleConfigError("cycle stage order changed")
    fixed_cycle = {
        "max_iterations": 64,
        "stable_iterations_to_stop": 1,
        "progress_token_schema_version": "ootang_prequential_cycle_progress_v1",
        "busy_exit_code": 3,
        "blocked_exit_code": 2,
    }
    for key, expected in fixed_cycle.items():
        if cycle[key] != expected or isinstance(cycle[key], bool) and isinstance(expected, int):
            raise CycleConfigError(f"cycle {key} changed")
    if profile["engineering_capabilities"] != EXPECTED_CAPABILITIES:
        raise CycleConfigError("cycle engineering capability boundary changed")

    profile["_profile_path"] = str(resolved)
    profile["_profile_sha256"] = profile_sha256
    profile["_project_root"] = str(root)
    profile["_deploy_profile_payload"] = loaded["deploy_profile"]
    profile["_live_profile_payload"] = loaded["live_profile"]
    return profile


def _confined_runtime_path(root: Path, value: object, *, name: str) -> Path:
    text = _required_string(value, name=name)
    pure = PurePosixPath(text)
    if (
        pure.is_absolute()
        or not pure.parts
        or any(part in {"", ".", ".."} for part in pure.parts)
        or "\\" in text
    ):
        raise CycleConfigError(f"{name} is not a confined relative path")
    candidate = root.joinpath(*pure.parts)
    current = root
    for part in pure.parts:
        current = current / part
        try:
            if current.is_symlink():
                raise CycleConfigError(
                    f"{name} contains a symbolic-link component"
                )
        except OSError as exc:
            raise CycleConfigError(f"{name} cannot be safely inspected") from exc
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise CycleConfigError(f"{name} escapes the runtime root") from exc
    if resolved == root:
        raise CycleConfigError(f"{name} must be a child of the runtime root")
    return candidate


def runtime_paths(
    profile: Mapping[str, Any], *, runtime_root: Path | None = None
) -> CycleRuntimePaths:
    """Resolve every token/status path under one symlink-confined root."""

    project_root = Path(str(profile["_project_root"])).resolve()
    runtime = profile["runtime"]
    root = (
        runtime_root.resolve()
        if runtime_root is not None
        else _artifact_path(
            runtime["root"], project_root=project_root, name="runtime.root"
        )
    )
    deploy_runtime = profile["_deploy_profile_payload"]["runtime"]
    live_runtime = profile["_live_profile_payload"]["runtime"]
    values = {
        "cycle_status": runtime["cycle_status"],
        "cycle_lock": runtime["cycle_lock"],
        "source_pointer": deploy_runtime["current_source_pointer"],
        "source_status": deploy_runtime["source_status"],
        "model_manifest": deploy_runtime["model_manifest"],
        "bundle_status": deploy_runtime["bundle_status"],
        "ledger": live_runtime["ledger"],
        "live_status": live_runtime["status"],
        "issue_inbox": deploy_runtime["issue_inbox"],
        "issue_receipts": deploy_runtime["issue_receipts"],
        "issue_status": deploy_runtime["issue_status"],
        "outcome_inbox": runtime["outcome_inbox"],
        "deploy_lock": deploy_runtime["deploy_lock"],
        "runner_lock": live_runtime["lock"],
        "outcome_status": runtime["outcome_status"],
        "outcome_receipts": runtime["outcome_receipts"],
        "objects": runtime["objects"],
    }
    resolved = {
        key: _confined_runtime_path(root, value, name=f"runtime.{key}")
        for key, value in values.items()
    }
    if len(set(resolved.values())) != len(resolved):
        raise CycleConfigError("runtime paths collide")
    return CycleRuntimePaths(
        root=root,
        live_config=_artifact_path(
            profile["live_profile"]["path"],
            project_root=project_root,
            name="live_profile.path",
        ),
        cycle_status=resolved["cycle_status"],
        cycle_lock=resolved["cycle_lock"],
        deploy_lock=resolved["deploy_lock"],
        runner_lock=resolved["runner_lock"],
        source_pointer=resolved["source_pointer"],
        source_status=resolved["source_status"],
        model_manifest=resolved["model_manifest"],
        bundle_status=resolved["bundle_status"],
        ledger=resolved["ledger"],
        live_status=resolved["live_status"],
        issue_inbox=resolved["issue_inbox"],
        issue_receipts=resolved["issue_receipts"],
        issue_status=resolved["issue_status"],
        outcome_inbox=resolved["outcome_inbox"],
        outcome_receipts=resolved["outcome_receipts"],
        outcome_status=resolved["outcome_status"],
        objects=resolved["objects"],
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
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise CycleIntegrityError("cycle state is not canonical finite JSON") from exc


def _artifact_identity(path: Path, *, name: str) -> dict[str, object] | None:
    if not path.exists():
        return None
    if not path.is_file():
        raise CycleIntegrityError(f"{name} is not a regular file")
    raw, digest = _read_snapshot(path, name=name)
    return {"sha256": digest, "size_bytes": len(raw)}


def _inbox_identity(path: Path, *, name: str) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    if not path.is_dir():
        raise CycleIntegrityError(f"{name} is not a directory")
    result: dict[str, dict[str, object]] = {}
    try:
        children = sorted(path.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise CycleIntegrityError(f"cannot enumerate {name}") from exc
    for child in children:
        if child.is_symlink():
            raise CycleIntegrityError(f"{name} contains a symbolic link")
        try:
            child.resolve(strict=False).relative_to(path.resolve())
        except (OSError, RuntimeError, ValueError) as exc:
            raise CycleIntegrityError(f"{name} entry escapes its inbox") from exc
        if not child.is_file():
            raise CycleIntegrityError(f"{name} contains a non-file entry")
        raw, digest = _read_snapshot(child, name=f"{name}/{child.name}")
        result[child.name] = {"sha256": digest, "size_bytes": len(raw)}
    return result


def _tree_identity(path: Path, *, name: str) -> dict[str, dict[str, object]]:
    """Digest a confined immutable registry tree, including nested receipts."""

    if not path.exists():
        return {}
    if not path.is_dir():
        raise CycleIntegrityError(f"{name} is not a directory")
    root = path.resolve()
    result: dict[str, dict[str, object]] = {}
    try:
        descendants = sorted(path.rglob("*"), key=lambda item: item.as_posix())
    except OSError as exc:
        raise CycleIntegrityError(f"cannot enumerate {name}") from exc
    for child in descendants:
        if child.is_symlink():
            raise CycleIntegrityError(f"{name} contains a symbolic link")
        try:
            resolved = child.resolve(strict=False)
            resolved.relative_to(root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise CycleIntegrityError(f"{name} entry escapes its registry") from exc
        if child.is_dir():
            continue
        if not child.is_file():
            raise CycleIntegrityError(f"{name} contains a non-file entry")
        relative = child.relative_to(path).as_posix()
        raw, digest = _read_snapshot(child, name=f"{name}/{relative}")
        result[relative] = {"sha256": digest, "size_bytes": len(raw)}
    return result


def _strict_runtime_json(path: Path, *, name: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    raw, _ = _read_snapshot(path, name=name)
    try:
        return _decode_json(raw, name=name)
    except CycleConfigError as exc:
        raise CycleIntegrityError(str(exc)) from exc


def _ledger_head(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    if not path.is_file():
        raise CycleIntegrityError("ledger path is not a regular file")
    try:
        uri = f"{path.resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=0.0)
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
            user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if application_id != 0x4F4F544C or user_version != 1:
                raise CycleIntegrityError("ledger schema identity/version changed")
            count = int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
            row = connection.execute(
                "SELECT sequence_id, entry_sha256 FROM events "
                "ORDER BY sequence_id DESC LIMIT 1"
            ).fetchone()
            connection.commit()
        finally:
            connection.close()
    except CycleIntegrityError:
        raise
    except sqlite3.Error as exc:
        raise CycleIntegrityError("cannot read the ledger scientific head") from exc
    if row is None:
        sequence_id = 0
        terminal = ZERO_HASH
    else:
        sequence_id, terminal = row
        if (
            not isinstance(sequence_id, int)
            or isinstance(sequence_id, bool)
            or sequence_id < 1
            or not isinstance(terminal, str)
            or HEX_SHA256_RE.fullmatch(terminal) is None
        ):
            raise CycleIntegrityError("ledger scientific head is malformed")
    if count != sequence_id:
        raise CycleIntegrityError("ledger sequence/count head is not contiguous")
    return {
        "event_count": count,
        "terminal_sequence_id": sequence_id,
        "terminal_sha256": terminal,
    }


def _ledger_audit_head(path: Path) -> dict[str, object] | None:
    try:
        return _ledger_head(path)
    except CycleError as exc:
        return {
            "read_failed": True,
            "reason": f"{type(exc).__name__}:{exc}",
        }


def _jsonable(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _jsonable(child)
            for key, child in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_jsonable(child) for child in value]
    if hasattr(value, "isoformat") and callable(value.isoformat):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise CycleIntegrityError(
        f"verified projection contains unsupported {type(value).__name__}"
    )


def _event_semantics(event: object | None) -> object | None:
    if event is None:
        return None
    fields = (
        "event_key",
        "event_type",
        "target_date",
        "station",
        "issue_id",
        "protocol_config_sha256",
        "code_sha256",
        "environment_sha256",
        "input_manifest_sha256",
        "model_manifest_sha256",
        "state_before_sha256",
        "state_after_sha256",
        "payload",
    )
    result: dict[str, object] = {}
    for field in fields:
        if not hasattr(event, field):
            raise CycleIntegrityError(f"verified projection event lacks {field}")
        result[field] = _jsonable(getattr(event, field))
    return result


def scientific_projection_payload(
    projection: object,
    *,
    station_state_hasher: Callable[[object], str],
) -> dict[str, object]:
    """Canonical science state, excluding raw head and failed anchor attempts."""

    required = (
        "epoch_id",
        "states",
        "last_finalized_date",
        "latest_displacement_mm",
        "outstanding_target_date",
        "outstanding_issue_id",
        "issue_events",
        "seal_event",
        "anchored_seal_hashes",
        "settled_events",
        "backfill_events",
        "revision_ids",
        "latest_actuals_by_date",
        "blind_settled_count",
        "engineering_blind_candidate_count",
        "backfill_count",
    )
    for field in required:
        if not hasattr(projection, field):
            raise CycleIntegrityError(f"verified projection lacks {field}")
    states = getattr(projection, "states")
    if not isinstance(states, Mapping):
        raise CycleIntegrityError("verified projection states are not a mapping")
    state_hashes: dict[str, str] = {}
    for station, state in sorted(states.items(), key=lambda pair: str(pair[0])):
        digest = station_state_hasher(state)
        if (
            not isinstance(digest, str)
            or HEX_SHA256_RE.fullmatch(digest) is None
        ):
            raise CycleIntegrityError(
                "station state hasher returned an invalid SHA-256"
            )
        state_hashes[str(station)] = digest

    def event_mapping(field: str) -> dict[str, object]:
        mapping = getattr(projection, field)
        if not isinstance(mapping, Mapping):
            raise CycleIntegrityError(
                f"verified projection {field} is not a mapping"
            )
        return {
            str(key): _event_semantics(event)
            for key, event in sorted(
                mapping.items(), key=lambda pair: str(pair[0])
            )
        }

    return {
        "epoch_id": _jsonable(getattr(projection, "epoch_id")),
        "station_state_sha256": state_hashes,
        "last_finalized_date": _jsonable(
            getattr(projection, "last_finalized_date")
        ),
        "latest_displacement_mm": _jsonable(
            getattr(projection, "latest_displacement_mm")
        ),
        "outstanding_target_date": _jsonable(
            getattr(projection, "outstanding_target_date")
        ),
        "outstanding_issue_id": _jsonable(
            getattr(projection, "outstanding_issue_id")
        ),
        "issue_events": event_mapping("issue_events"),
        "seal_event": _event_semantics(getattr(projection, "seal_event")),
        "confirmed_anchor_receipts": _jsonable(
            getattr(projection, "anchored_seal_hashes")
        ),
        "settled_events": event_mapping("settled_events"),
        "backfill_events": event_mapping("backfill_events"),
        "revision_ids": _jsonable(getattr(projection, "revision_ids")),
        "latest_actuals_by_date": _jsonable(
            getattr(projection, "latest_actuals_by_date")
        ),
        "blind_settled_count": getattr(projection, "blind_settled_count"),
        "engineering_blind_candidate_count": getattr(
            projection, "engineering_blind_candidate_count"
        ),
        "backfill_count": getattr(projection, "backfill_count"),
    }


def _verified_projection_identity(paths: CycleRuntimePaths) -> object | None:
    if not paths.ledger.exists():
        return None
    try:
        ledger_size = paths.ledger.stat().st_size
    except OSError as exc:
        raise CycleIntegrityError("live ledger metadata cannot be read") from exc
    if ledger_size == 0:
        return {"state": "recoverable_zero_byte_pre_genesis"}
    audit_head = _ledger_head(paths.ledger)
    if audit_head is not None and audit_head["event_count"] == 0:
        return {
            "state": "recoverable_verified_schema_pre_genesis",
            "ledger_audit_head": audit_head,
        }
    from monitoring import ootang_prequential_live

    try:
        live_profile = ootang_prequential_live.load_config(paths.live_config)
        live_paths = ootang_prequential_live.runtime_paths(
            live_profile, runtime_root=paths.root
        )
        prerequisites = ootang_prequential_live.load_prerequisites(
            live_profile, live_paths
        )
        if prerequisites is None:
            raise CycleIntegrityError(
                "an existing ledger lacks its bound source/model prerequisites"
            )
        projection = ootang_prequential_live.load_verified_ledger_projection(
            live_profile, live_paths, prerequisites
        )
        payload = scientific_projection_payload(
            projection,
            station_state_hasher=ootang_prequential_live.station_state_sha256_v1,
        )
    except CycleError:
        raise
    except Exception as exc:
        raise CycleIntegrityError(
            "ledger failed verified scientific projection replay: "
            f"{type(exc).__name__}:{exc}"
        ) from exc
    raw = _canonical_bytes(payload)
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "payload": payload,
    }


def progress_token_payload(paths: CycleRuntimePaths) -> dict[str, object]:
    """Snapshot only scientific state; operational timestamps are excluded."""
    return {
        "schema_version": "ootang_prequential_cycle_progress_v1",
        "source_pointer": _artifact_identity(
            paths.source_pointer, name="current source pointer"
        ),
        "model_manifest": _artifact_identity(
            paths.model_manifest, name="model manifest"
        ),
        "verified_scientific_projection": _verified_projection_identity(paths),
        "issue_inbox": _inbox_identity(paths.issue_inbox, name="issue inbox"),
        "issue_receipts": _tree_identity(
            paths.issue_receipts, name="issue receipt registry"
        ),
        "outcome_inbox": _inbox_identity(
            paths.outcome_inbox, name="outcome inbox"
        ),
        "outcome_receipts": _tree_identity(
            paths.outcome_receipts, name="outcome receipt registry"
        ),
    }


def build_progress_token(paths: CycleRuntimePaths) -> str:
    """Take one untorn snapshot while briefly holding deploy then runner lock."""

    handles: list[object] = []
    try:
        for name, path in (
            ("deploy", paths.deploy_lock),
            ("runner", paths.runner_lock),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = None
            try:
                handle = path.open("a+b")
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                if handle is not None:
                    handle.close()
                raise CycleBusyError(
                    f"{name} lock is busy during scientific snapshot"
                ) from exc
            except OSError as exc:
                if handle is not None:
                    handle.close()
                raise CycleIntegrityError(
                    f"cannot acquire {name} lock for scientific snapshot"
                ) from exc
            handles.append(handle)
        payload = progress_token_payload(paths)
        return hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    finally:
        for handle in reversed(handles):
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()


def _expected_stage_status_path(
    paths: CycleRuntimePaths, stage_name: str
) -> Path:
    mapping = {
        "source_ingest": paths.source_status,
        "bundle_ensure": paths.bundle_status,
        "live_reconcile_before_outcome": paths.live_status,
        "outcome_materialize": paths.outcome_status,
        "live_reconcile_after_outcome": paths.live_status,
        "issue_produce": paths.issue_status,
        "live_seal_issue": paths.live_status,
    }
    return mapping[stage_name]


def _read_stage_status(
    stage_name: str, path: Path, *, expected_path: Path
) -> StageOutcome:
    try:
        resolved = path.resolve(strict=True)
        expected_resolved = expected_path.resolve(strict=False)
    except OSError as exc:
        raise CycleIntegrityError(
            f"{stage_name} returned an unreadable status path"
        ) from exc
    if resolved != expected_resolved or not resolved.is_file():
        raise CycleIntegrityError(
            f"{stage_name} returned a status path outside its contract"
        )
    payload = _strict_runtime_json(path, name="stage status")
    if payload is None:
        raise CycleIntegrityError(f"stage returned a missing status path: {path}")
    status_key, schema, allowed = STAGE_STATUS_CONTRACTS[stage_name]
    if payload.get("schema_version") != schema:
        raise CycleIntegrityError(f"{stage_name} status schema changed")
    profile_id, artifact_status, hash_key, expected_hash = (
        STAGE_STATUS_METADATA[stage_name]
    )
    if (
        payload.get("profile_id") != profile_id
        or payload.get("artifact_status") != artifact_status
        or payload.get(hash_key) != expected_hash
    ):
        raise CycleIntegrityError(f"{stage_name} status provenance changed")
    if (
        "runtime_root" in payload
        and payload["runtime_root"] != str(expected_path.parent.resolve())
    ):
        raise CycleIntegrityError(f"{stage_name} status runtime root changed")
    if stage_name == "outcome_materialize" and (
        payload.get("deploy_profile_sha256") != DEPLOY_PROFILE_SHA256
        or payload.get("live_profile_sha256") != LIVE_PROFILE_SHA256
    ):
        raise CycleIntegrityError("outcome materializer status bindings changed")
    value = payload.get(status_key)
    if value not in allowed:
        raise CycleIntegrityError(
            f"{stage_name} returned unknown or fatal status {value!r}"
        )
    for flag in (
        "formal_warning_output",
        "independent_label_used",
        "confirmatory_external_validation",
        "vajont_used",
        "e2_live_evidence_eligible",
        "real_activation_ready",
    ):
        if flag in payload and payload[flag] is not False:
            raise CycleIntegrityError(
                f"{stage_name} status claimed forbidden capability {flag}"
            )
    return StageOutcome(value, resolved)


def _stage_outcome(
    stage_name: str,
    value: object,
    *,
    paths: CycleRuntimePaths,
    require_status_path: bool,
) -> StageOutcome:
    if isinstance(value, StageOutcome):
        outcome = value
    elif isinstance(value, Path):
        outcome = _read_stage_status(
            stage_name,
            value,
            expected_path=_expected_stage_status_path(paths, stage_name),
        )
    elif isinstance(value, str):
        outcome = StageOutcome(value)
    else:
        status = getattr(value, "status", None)
        status_path = getattr(value, "status_path", None)
        if not isinstance(status, str) or not status:
            raise CycleIntegrityError("stage returned an unsupported result")
        if status_path is not None and not isinstance(status_path, Path):
            status_path = Path(status_path)
        outcome = StageOutcome(status, status_path)
    allowed = STAGE_STATUS_CONTRACTS[stage_name][2]
    if outcome.status not in allowed:
        raise CycleIntegrityError(
            f"{stage_name} returned unknown or fatal status {outcome.status!r}"
        )
    if outcome.status_path is not None:
        verified = _read_stage_status(
            stage_name,
            outcome.status_path,
            expected_path=_expected_stage_status_path(paths, stage_name),
        )
        if verified.status != outcome.status:
            raise CycleIntegrityError(
                f"{stage_name} result disagrees with its durable status"
            )
        outcome = verified
    elif require_status_path:
        raise CycleIntegrityError(f"{stage_name} did not return a status path")
    return outcome


def _production_dependencies(
    profile: Mapping[str, Any],
    *,
    config_path: Path,
    paths: CycleRuntimePaths,
    project_root: Path,
) -> CycleDependencies:
    # Imports are deliberately lazy: an empty runtime must remain a cheap,
    # successful waiting poll and the outcome module can be deployed jointly.
    from convlstm import ootang_production_bundle
    from monitoring import ootang_issue_producer
    from monitoring import ootang_live_source
    from monitoring import ootang_outcome_materializer
    from monitoring import ootang_prequential_live

    deploy_path = _artifact_path(
        profile["deploy_profile"]["path"],
        project_root=project_root,
        name="deploy_profile.path",
    )
    live_path = _artifact_path(
        profile["live_profile"]["path"],
        project_root=project_root,
        name="live_profile.path",
    )
    deploy_profile = ootang_live_source.load_deploy_profile(
        deploy_path, project_root=project_root
    )

    def source_ingest() -> object:
        return ootang_live_source.ingest_source(
            deploy_profile,
            runtime_root=paths.root,
            project_root=project_root,
        )

    def bundle_ensure() -> object:
        return ootang_production_bundle.run_once(
            deploy_path, runtime_root=paths.root
        )

    def live_reconcile() -> object:
        return ootang_prequential_live.poll_live_runner(
            config_path=live_path, runtime_root=paths.root
        )

    def outcome_materialize() -> object:
        return ootang_outcome_materializer.materialize_outcome(
            config_path=config_path,
            runtime_root=paths.root,
            project_root=project_root,
        )

    def issue_produce() -> object:
        return ootang_issue_producer.produce_issue(
            config_path=deploy_path,
            runtime_root=paths.root,
            project_root=project_root,
        )

    return CycleDependencies(
        source_ingest=source_ingest,
        bundle_ensure=bundle_ensure,
        live_reconcile=live_reconcile,
        outcome_materialize=outcome_materialize,
        issue_produce=issue_produce,
    )


def _is_busy(exc: BaseException) -> bool:
    return type(exc).__name__ in {
        "SourceBusyError",
        "ProductionBundleBusyError",
        "LiveRunnerBusy",
        "OutcomeMaterializerBusy",
        "IssueProducerBusy",
        "CycleBusyError",
    }


def _stage_calls(dependencies: CycleDependencies) -> tuple[tuple[str, StageCall], ...]:
    return (
        ("source_ingest", dependencies.source_ingest),
        ("bundle_ensure", dependencies.bundle_ensure),
        ("live_reconcile_before_outcome", dependencies.live_reconcile),
        ("outcome_materialize", dependencies.outcome_materialize),
        ("live_reconcile_after_outcome", dependencies.live_reconcile),
        ("issue_produce", dependencies.issue_produce),
        ("live_seal_issue", dependencies.live_reconcile),
    )


def _utc_text(clock: Clock) -> str:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise CycleIntegrityError("machine clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _status_payload(
    profile: Mapping[str, Any],
    paths: CycleRuntimePaths,
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
        "schema_version": "ootang_prequential_cycle_status_v1",
        "profile_id": profile["profile_id"],
        "cycle_profile_sha256": profile["_profile_sha256"],
        "deploy_profile_sha256": DEPLOY_PROFILE_SHA256,
        "live_profile_sha256": LIVE_PROFILE_SHA256,
        "artifact_status": profile["artifact_status"],
        "cycle_status": cycle_status,
        "reason": reason,
        "recorded_at_utc": _utc_text(clock),
        "runtime_root": str(paths.root),
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
        "ledger_audit_head": _ledger_audit_head(paths.ledger),
        "formal_warning_output": False,
        "independent_label_used": False,
        "confirmatory_external_validation": False,
        "vajont_used": False,
        "default_pipeline_member": False,
        "runner_independent_checkpoint_inference_replayed": False,
        "trusted_anchor_receipt_verified": False,
        "automatic_epoch_rotation_implemented": False,
        "e2_live_evidence_eligible": False,
        "real_activation_ready": False,
    }


def _atomic_write_status(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _write_cycle_status(
    profile: Mapping[str, Any],
    paths: CycleRuntimePaths,
    **kwargs: object,
) -> None:
    payload = _status_payload(profile, paths, **kwargs)  # type: ignore[arg-type]
    try:
        _atomic_write_status(paths.cycle_status, payload)
    except OSError as exc:
        raise CycleIntegrityError("cannot publish cycle status atomically") from exc


def _load_continuation_history(
    profile: Mapping[str, Any], paths: CycleRuntimePaths
) -> list[str]:
    payload = _strict_runtime_json(paths.cycle_status, name="cycle status")
    if payload is None:
        return []
    if payload.get("schema_version") != "ootang_prequential_cycle_status_v1":
        raise CycleIntegrityError("existing cycle status schema changed")
    if payload.get("cycle_status") != "work_remaining":
        return []
    expected = {
        "profile_id": profile["profile_id"],
        "cycle_profile_sha256": profile["_profile_sha256"],
        "deploy_profile_sha256": DEPLOY_PROFILE_SHA256,
        "live_profile_sha256": LIVE_PROFILE_SHA256,
        "artifact_status": profile["artifact_status"],
        "runtime_root": str(paths.root),
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
        "e2_live_evidence_eligible": False,
        "real_activation_ready": False,
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            raise CycleIntegrityError(
                f"work-remaining cycle status changed {key}"
            )
    history = payload.get("continuation_token_history")
    terminal = payload.get("progress_token_sha256")
    if (
        not isinstance(history, list)
        or not history
        or any(
            not isinstance(token, str)
            or HEX_SHA256_RE.fullmatch(token) is None
            for token in history
        )
        or len(history) != len(set(history))
        or history[-1] != terminal
    ):
        raise CycleIntegrityError(
            "work-remaining continuation token history is invalid"
        )
    return list(history)


def run_cycle(
    config_path: Path = DEFAULT_CONFIG_PATH,
    *,
    runtime_root: Path | None = None,
    project_root: Path = ROOT,
    clock: Clock | None = None,
    dependencies: CycleDependencies | None = None,
    progress_token_builder: ProgressTokenBuilder | None = None,
) -> CycleResult:
    """Run seven-stage iterations until the scientific state reaches a fixed point."""

    profile = load_cycle_profile(config_path, project_root=project_root)
    if (
        dependencies is not None
        or progress_token_builder is not None
        or clock is not None
    ) and os.environ.get(TEST_OVERRIDE_ENV) != "1":
        raise CycleConfigError(
            "Cycle dependency, token, and clock injection is restricted to "
            "explicit test mode"
        )
    paths = runtime_paths(profile, runtime_root=runtime_root)
    status_clock = clock or (lambda: datetime.now(timezone.utc))
    paths.cycle_lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock_handle = paths.cycle_lock.open("a+b")
    except OSError as exc:
        raise CycleIntegrityError("cannot open the cycle lock") from exc
    try:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CycleBusyError("another machine cycle owns the outer lock") from exc

        production_mode = dependencies is None
        iteration_records: list[dict[str, object]] = []
        stable_iterations = 0
        completed_iterations = 0
        latest_token: str | None = None
        current_stages: list[dict[str, object]] = []
        seen_tokens: set[str] = set()
        token_history: list[str] = []
        try:
            token_history = _load_continuation_history(profile, paths)
            seen_tokens = set(token_history)
            latest_token = token_history[-1] if token_history else None
            stage_dependencies = dependencies or _production_dependencies(
                profile,
                config_path=config_path.resolve(),
                paths=paths,
                project_root=project_root.resolve(),
            )
            token_builder = progress_token_builder or (
                lambda: build_progress_token(paths)
            )
            for iteration in range(1, profile["cycle"]["max_iterations"] + 1):
                before = token_builder()
                if not isinstance(before, str) or HEX_SHA256_RE.fullmatch(before) is None:
                    raise CycleIntegrityError("progress token builder returned an invalid SHA-256")
                if (
                    latest_token is not None
                    and before != latest_token
                    and before in seen_tokens
                ):
                    raise CycleIntegrityError(
                        "scientific progress token oscillated between iterations"
                    )
                if before not in seen_tokens:
                    seen_tokens.add(before)
                    token_history.append(before)
                latest_token = before
                current_stages = []
                for stage_name, stage_call in _stage_calls(stage_dependencies):
                    try:
                        outcome = _stage_outcome(
                            stage_name,
                            stage_call(),
                            paths=paths,
                            require_status_path=production_mode,
                        )
                    except Exception as exc:
                        if isinstance(exc, CycleError):
                            raise
                        if _is_busy(exc):
                            raise CycleBusyError(
                                f"stage {stage_name} is busy: {type(exc).__name__}:{exc}"
                            ) from exc
                        raise CycleIntegrityError(
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
                    raise CycleIntegrityError("progress token builder returned an invalid SHA-256")
                if after != before and after in seen_tokens:
                    raise CycleIntegrityError(
                        "scientific progress token oscillated within the bounded cycle"
                    )
                if after not in seen_tokens:
                    seen_tokens.add(after)
                    token_history.append(after)
                completed_iterations = iteration
                latest_token = after
                stable_iterations = stable_iterations + 1 if before == after else 0
                iteration_records.append(
                    {
                        "iteration": iteration,
                        "before_progress_token_sha256": before,
                        "after_progress_token_sha256": after,
                        "scientific_state_changed": before != after,
                        "stages": current_stages,
                    }
                )
                if stable_iterations >= profile["cycle"]["stable_iterations_to_stop"]:
                    waiting = any(
                        str(record["status"]).startswith("waiting")
                        for record in current_stages
                    )
                    status = "converged_waiting" if waiting else "converged"
                    _write_cycle_status(
                        profile,
                        paths,
                        clock=status_clock,
                        cycle_status=status,
                        reason="scientific_progress_token_reached_fixed_point",
                        iterations=completed_iterations,
                        stable_iterations=stable_iterations,
                        progress_token_sha256=latest_token,
                        iteration_records=iteration_records,
                        continuation_token_history=[],
                    )
                    return CycleResult(
                        paths.cycle_status,
                        status,
                        completed_iterations,
                        latest_token,
                    )
            _write_cycle_status(
                profile,
                paths,
                clock=status_clock,
                cycle_status="work_remaining",
                reason="bounded_cycle_made_unique_scientific_progress",
                iterations=completed_iterations,
                stable_iterations=stable_iterations,
                progress_token_sha256=latest_token,
                iteration_records=iteration_records,
                continuation_token_history=token_history,
            )
            if latest_token is None:  # pragma: no cover - fixed max is positive
                raise CycleIntegrityError("bounded cycle produced no progress token")
            return CycleResult(
                paths.cycle_status,
                "work_remaining",
                completed_iterations,
                latest_token,
            )
        except Exception as exc:
            normalized: CycleError
            if isinstance(exc, CycleError):
                normalized = exc
            else:
                normalized = CycleIntegrityError(
                    f"cycle failed: {type(exc).__name__}:{exc}"
                )
            if isinstance(normalized, CycleBusyError):
                blocked_status = "busy_substage"
            else:
                blocked_status = "blocked_integrity"
            try:
                _write_cycle_status(
                    profile,
                    paths,
                    clock=status_clock,
                    cycle_status=blocked_status,
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
            except CycleError:
                if isinstance(normalized, CycleBusyError):
                    raise normalized
                raise
            raise normalized
    finally:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        finally:
            lock_handle.close()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--runtime-root", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = run_cycle(args.config, runtime_root=args.runtime_root)
    except CycleBusyError as exc:
        print(f"[ootang-prequential-cycle] busy: {exc}", file=sys.stderr)
        return 3
    except CycleError as exc:
        print(f"[ootang-prequential-cycle] blocked: {exc}", file=sys.stderr)
        return 2
    print(
        "[ootang-prequential-cycle] "
        f"status={result.status} iterations={result.iterations} "
        f"token={result.progress_token_sha256}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CycleBusyError",
    "CycleConfigError",
    "CycleDependencies",
    "CycleError",
    "CycleIntegrityError",
    "CycleResult",
    "CycleRuntimePaths",
    "DEFAULT_CONFIG_PATH",
    "StageOutcome",
    "TEST_OVERRIDE_ENV",
    "build_progress_token",
    "load_cycle_profile",
    "main",
    "progress_token_payload",
    "run_cycle",
    "runtime_paths",
    "scientific_projection_payload",
]
