"""Independent, machine-only replay of one pending Ootang issue.

The issue producer is intentionally not imported here.  This verifier captures
the producer's durable issue/input/checkpoint bytes, reconstructs preprocessing
and ConvLSTM inference locally, and publishes a create-only receipt before the
live runner may seal that issue.  It shares only the recursively verified source
authority with the producer; prediction construction is independently coded.

This is engineering infrastructure.  It neither reads outcomes nor promotes an
issue to live evidence, real activation, calibration selection, or warning use.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import fcntl
import hashlib
import io
import json
import math
import os
from pathlib import Path
import platform
import stat
import sys
import tempfile
from typing import Any, BinaryIO
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import torch
from torch import nn
from torch.nn import functional as torch_f


ROOT = Path(__file__).resolve().parents[2]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

DEFAULT_CONFIG_PATH = ROOT / "config" / "ootang_issue_replay.v1.json"
DEFAULT_CONFIG_SHA256 = (
    "c42a56a547691654f9281f44b94e5d79ef66a8ff0064b255a59d67c6939e6fd5"
)
ZERO_HASH = "0" * 64
HEX_DIGITS = frozenset("0123456789abcdef")
MAX_JSON_BYTES = 128 * 1024 * 1024
TEST_OVERRIDE_ENV = "OOTANG_ISSUE_REPLAY_ALLOW_TEST_OVERRIDE"

RECEIPT_TOP_KEYS = {
    "schema_version",
    "profile_id",
    "artifact_status",
    "target_date",
    "verified_at_utc",
    "ledger_pre_head",
    "issue",
    "input_manifest",
    "source",
    "model",
    "verification",
    "implementation",
    "formal_warning_output",
    "e2_live_evidence_eligible",
    "real_activation_ready",
    "automatic_calibration_promotion",
}
SUCCESS_STATUSES = frozenset(
    {
        "waiting_for_source_model_or_issue",
        "verified",
        "already_verified_idempotent",
    }
)


class IssueReplayError(RuntimeError):
    """Base error for the independent issue verifier."""


class IssueReplayConfigError(IssueReplayError):
    """The reviewed verifier configuration changed."""


class IssueReplayIntegrityError(IssueReplayError):
    """Present runtime material failed independent replay."""


class IssueReplayConflictError(IssueReplayIntegrityError):
    """An immutable receipt or target already has different semantics."""


class IssueReplayBusyError(IssueReplayError):
    """Another machine process owns a required lock."""


@dataclass(frozen=True)
class Artifact:
    path: Path
    sha256: str
    size_bytes: int

    def as_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class ReplayRuntimePaths:
    root: Path
    status: Path
    receipts: Path
    objects: Path
    issue_inbox: Path
    ledger: Path
    runner_lock: Path
    replay_lock: Path
    model_manifest: Path
    activation_manifest: Path


@dataclass(frozen=True)
class ReplayResult:
    status_path: Path
    status: str
    target_date: date | None
    receipt_path: Path | None

    @property
    def replay_status(self) -> str:
        """Compatibility name used by orchestration status adapters."""

        return self.status


@dataclass(frozen=True)
class VerifiedReplayReceipt:
    path: Path
    sha256: str
    size_bytes: int
    target_date: date
    payload: Mapping[str, Any]

    @property
    def artifact(self) -> Artifact:
        return Artifact(self.path, self.sha256, self.size_bytes)


@dataclass(frozen=True)
class _Checkpoint:
    seed: int
    artifact: Artifact
    model: nn.Module
    displacement_mean: np.ndarray
    displacement_scale: np.ndarray
    exogenous_mean: np.ndarray
    exogenous_scale: np.ndarray
    delta_scale: np.ndarray
    elevation_mean_m: float
    elevation_scale_m: float
    elevation_grid: np.ndarray
    readout_weights: np.ndarray


@dataclass(frozen=True)
class _VerifiedInputs:
    target: date
    issue: Artifact
    input_manifest: Artifact
    source: dict[str, Any]
    model: dict[str, Any]
    input_rows_sha256: str
    replayed_predictions_sha256: str
    persistence_values_verified: int
    p50_values_verified: int
    maximum_abs_difference_mm: float
    comparisons: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class _ActivationPreprocessing:
    semantic_manifest: Artifact
    canonical_dataset: Artifact
    displacement_mean: np.ndarray
    displacement_scale: np.ndarray
    exogenous_mean: np.ndarray
    exogenous_scale: np.ndarray
    delta_scale: np.ndarray


Clock = Callable[[], datetime]


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_bytes(value: object, *, newline: bool = False) -> bytes:
    try:
        suffix = "\n" if newline else ""
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + suffix
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError, UnicodeEncodeError) as exc:
        raise IssueReplayIntegrityError(
            "value is not canonical finite JSON"
        ) from exc


def _canonical_sha256(value: object) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _reject_constant(value: str) -> None:
    raise IssueReplayIntegrityError(f"forbidden JSON constant: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise IssueReplayIntegrityError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite_tree(value: object, *, name: str) -> None:
    try:
        if isinstance(value, float) and not math.isfinite(value):
            raise IssueReplayIntegrityError(f"{name} contains a non-finite number")
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str):
                    raise IssueReplayIntegrityError(f"{name} has a non-string key")
                _reject_nonfinite_tree(child, name=name)
        elif isinstance(value, list):
            for child in value:
                _reject_nonfinite_tree(child, name=name)
    except RecursionError as exc:
        raise IssueReplayIntegrityError(f"{name} is too deeply nested") from exc


def _decode_json(raw: bytes, *, name: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except IssueReplayError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise IssueReplayIntegrityError(f"{name} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise IssueReplayIntegrityError(f"{name} must be a JSON object")
    _reject_nonfinite_tree(value, name=name)
    return value


def _read_regular_once(path: Path, *, name: str, maximum_bytes: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise IssueReplayIntegrityError(f"cannot open {name}: {path}") from exc
    try:
        before = os.fstat(descriptor)
        path_before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or not stat.S_ISREG(path_before.st_mode)
            or (before.st_dev, before.st_ino)
            != (path_before.st_dev, path_before.st_ino)
        ):
            raise IssueReplayIntegrityError(
                f"{name} pathname changed before it was read"
            )
        if before.st_size <= 0 or before.st_size > maximum_bytes:
            raise IssueReplayIntegrityError(f"{name} size is outside safety bounds")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(maximum_bytes + 1)
        after = os.fstat(descriptor)
        path_after = path.lstat()
    except OSError as exc:
        raise IssueReplayIntegrityError(f"cannot read {name}: {path}") from exc
    finally:
        os.close(descriptor)
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if identity_before != identity_after or len(raw) != before.st_size:
        raise IssueReplayIntegrityError(f"{name} changed while being read")
    if (
        not stat.S_ISREG(path_after.st_mode)
        or (after.st_dev, after.st_ino)
        != (path_after.st_dev, path_after.st_ino)
        or (path_before.st_dev, path_before.st_ino)
        != (path_after.st_dev, path_after.st_ino)
    ):
        raise IssueReplayIntegrityError(f"{name} pathname changed while being read")
    if len(raw) > maximum_bytes:
        raise IssueReplayIntegrityError(f"{name} size is outside safety bounds")
    return raw


def _json_snapshot(path: Path, *, name: str) -> tuple[dict[str, Any], bytes]:
    raw = _read_regular_once(path, name=name, maximum_bytes=MAX_JSON_BYTES)
    return _decode_json(raw, name=name), raw


def _exact_keys(value: object, expected: set[str], *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise IssueReplayIntegrityError(f"{name} must be an object")
    if set(value) != expected or any(not isinstance(key, str) for key in value):
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        raise IssueReplayIntegrityError(
            f"{name} keys changed; missing={missing}, extra={extra}"
        )
    return value


def _required_string(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise IssueReplayIntegrityError(f"{name} must be a nonblank trimmed string")
    return value


def _required_sha256(value: object, *, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in HEX_DIGITS for character in value)
    ):
        raise IssueReplayIntegrityError(f"{name} must be lowercase SHA-256")
    return value


def _required_int(value: object, *, name: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise IssueReplayIntegrityError(f"{name} must be an integer >= {minimum}")
    return value


def _finite(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IssueReplayIntegrityError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise IssueReplayIntegrityError(f"{name} must be finite")
    return result


def _parse_date(value: object, *, name: str) -> date:
    if not isinstance(value, str):
        raise IssueReplayIntegrityError(f"{name} must be YYYY-MM-DD")
    try:
        result = date.fromisoformat(value)
    except ValueError as exc:
        raise IssueReplayIntegrityError(f"{name} must be YYYY-MM-DD") from exc
    if result.isoformat() != value:
        raise IssueReplayIntegrityError(f"{name} is not canonical YYYY-MM-DD")
    return result


def _parse_utc(value: object, *, name: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise IssueReplayIntegrityError(f"{name} must be RFC3339 UTC ending in Z")
    try:
        result = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise IssueReplayIntegrityError(f"{name} is not valid UTC") from exc
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        raise IssueReplayIntegrityError(f"{name} must be UTC")
    return result


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise IssueReplayIntegrityError("machine clock must be timezone-aware")
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _verified_completion_time(
    *, started_at: datetime, completed_at: datetime, target: date, timezone_name: str
) -> datetime:
    """Enforce the machine-clock causal fence immediately before publication."""

    _utc_text(started_at)
    _utc_text(completed_at)
    started_utc = started_at.astimezone(timezone.utc)
    completed_utc = completed_at.astimezone(timezone.utc)
    if completed_utc < started_utc:
        raise IssueReplayIntegrityError("machine clock moved backward during replay")
    target_start = datetime.combine(
        target, time.min, tzinfo=ZoneInfo(timezone_name)
    ).astimezone(timezone.utc)
    if completed_utc >= target_start:
        raise IssueReplayIntegrityError(
            "independent replay did not complete before target start"
        )
    return completed_utc


def _validate_declared_causal_time_chain(
    *,
    current_exported_at: datetime,
    current_record_causal_times: Sequence[tuple[datetime, datetime]],
    activation_exported_at: datetime,
    activation_captured_at: datetime,
    model_created_at: datetime,
    issue_source_as_of: datetime,
    issue_generated_at: datetime,
    verified_at: datetime,
    error_type: type[IssueReplayError],
) -> None:
    """Validate declared source/model/issue times without claiming trusted time."""

    if not current_record_causal_times:
        raise error_type("declared current source has no causal record times")
    if issue_source_as_of > issue_generated_at or issue_generated_at > verified_at:
        raise error_type("declared issue/verification causal time ordering changed")
    if current_exported_at > issue_source_as_of or any(
        available_at > issue_source_as_of or finalized_at > issue_source_as_of
        for available_at, finalized_at in current_record_causal_times
    ):
        raise error_type(
            "declared current source was not available by issue source as-of"
        )
    if (
        activation_exported_at > model_created_at
        or activation_captured_at > model_created_at
        or model_created_at > issue_generated_at
    ):
        raise error_type(
            "declared activation/model/issue causal time ordering changed"
        )


def _project_path(value: object, *, project_root: Path, name: str) -> Path:
    text = _required_string(value, name=name)
    candidate = Path(text)
    path = candidate.resolve() if candidate.is_absolute() else (project_root / candidate).resolve()
    try:
        path.relative_to(project_root.resolve())
    except ValueError as exc:
        raise IssueReplayConfigError(f"{name} escapes project root") from exc
    return path


def _relative_child(
    root: Path,
    value: object,
    *,
    name: str,
    leaf_directory: bool = False,
) -> Path:
    text = _required_string(value, name=name)
    relative = Path(text)
    if relative.is_absolute() or ".." in relative.parts or relative == Path("."):
        raise IssueReplayConfigError(f"{name} must be a safe relative path")
    if not root.is_absolute():
        raise IssueReplayConfigError("runtime root must be canonical absolute")
    path = root.joinpath(*relative.parts)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise IssueReplayConfigError(f"{name} escapes runtime root") from exc

    current = root
    missing_tail = False
    for index, component in enumerate(relative.parts):
        current /= component
        if missing_tail:
            continue
        try:
            observed = current.lstat()
        except FileNotFoundError:
            # A machine-created tail is allowed.  Once one component is
            # absent, no deeper component can already exist lexically.
            missing_tail = True
            continue
        except OSError as exc:
            raise IssueReplayConfigError(f"{name} cannot be inspected") from exc
        if stat.S_ISLNK(observed.st_mode):
            raise IssueReplayConfigError(f"{name} contains a symbolic link")
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(observed.st_mode):
            raise IssueReplayConfigError(
                f"{name} contains a non-directory parent"
            )
        if index == len(relative.parts) - 1:
            expected_mode = (
                stat.S_ISDIR(observed.st_mode)
                if leaf_directory
                else stat.S_ISREG(observed.st_mode)
            )
            if not expected_mode:
                expected = "directory" if leaf_directory else "regular file"
                raise IssueReplayConfigError(
                    f"{name} existing leaf is not a {expected}"
                )
    return path


def _verified_static_artifact(
    value: object, *, project_root: Path, name: str
) -> Artifact:
    record = _exact_keys(value, {"path", "expected_sha256"}, name=name)
    path = _project_path(record["path"], project_root=project_root, name=f"{name}.path")
    expected = _required_sha256(
        record["expected_sha256"], name=f"{name}.expected_sha256"
    )
    raw = _read_regular_once(path, name=name, maximum_bytes=MAX_JSON_BYTES)
    if _sha256_bytes(raw) != expected:
        raise IssueReplayConfigError(f"{name} SHA-256 changed")
    return Artifact(path, expected, len(raw))


def _validate_profile(profile: dict[str, Any], *, project_root: Path) -> None:
    expected_top = {
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
        "station_geometry",
        "shared_source_authority",
        "runtime",
        "verification",
        "implementation_bindings",
        "engineering_capabilities",
    }
    _exact_keys(profile, expected_top, name="issue replay profile")
    identity = (
        profile["schema_version"],
        profile["profile_id"],
        profile["profile_version"],
        profile["case"],
        profile["artifact_status"],
    )
    if identity != (
        "ootang_issue_replay_profile_v1",
        "ootang-issue-replay-v1",
        "1.0.0-engineering",
        "ootang",
        "e2_checkpoint_input_replay_engineering_only_not_live_evidence",
    ):
        raise IssueReplayConfigError("issue replay profile identity changed")
    for flag in (
        "formal_warning_output",
        "independent_label_used",
        "confirmatory_external_validation",
        "vajont_used",
        "default_pipeline_member",
    ):
        if profile[flag] is not False:
            raise IssueReplayConfigError(f"{flag} must remain false")

    expected_capabilities = {
        "shared_source_authority_used": True,
        "independent_checkpoint_loader_implemented": True,
        "independent_preprocessing_implemented": True,
        "independent_convlstm_forward_implemented": True,
        "independent_issue_prediction_replay_implemented": True,
        "trusted_anchor_receipt_verified": False,
        "automatic_epoch_rotation_implemented": False,
        "automatic_calibration_promotion_implemented": False,
        "e2_live_evidence_eligible": False,
        "real_activation_ready": False,
    }
    if profile["engineering_capabilities"] != expected_capabilities:
        raise IssueReplayConfigError("engineering capability boundary changed")

    deploy = _verified_static_artifact(
        profile["deploy_profile"], project_root=project_root, name="deploy profile"
    )
    live = _verified_static_artifact(
        profile["live_profile"], project_root=project_root, name="live profile"
    )
    geometry = _verified_static_artifact(
        profile["station_geometry"], project_root=project_root, name="station geometry"
    )
    authority = _exact_keys(
        profile["shared_source_authority"],
        {"path", "expected_sha256", "public_loader", "role"},
        name="shared_source_authority",
    )
    authority_artifact = _verified_static_artifact(
        {"path": authority["path"], "expected_sha256": authority["expected_sha256"]},
        project_root=project_root,
        name="shared source authority",
    )
    if (
        authority["public_loader"] != "load_current_source"
        or authority["role"]
        != "recursive_source_lineage_authority_not_prediction_implementation"
    ):
        raise IssueReplayConfigError("shared source authority contract changed")

    bindings = _exact_keys(
        profile["implementation_bindings"],
        {"pyproject", "uv_lock"},
        name="implementation_bindings",
    )
    pyproject = _verified_static_artifact(
        bindings["pyproject"], project_root=project_root, name="pyproject binding"
    )
    uv_lock = _verified_static_artifact(
        bindings["uv_lock"], project_root=project_root, name="uv lock binding"
    )

    verification = profile["verification"]
    expected_verification = {
        "issue_schema_version": "ootang_live_issue_batch_v1",
        "input_manifest_schema_version": "ootang_issue_input_manifest_v1",
        "outer_manifest_schema_version": "ootang_live_five_seed_model_bundle_v1",
        "training_manifest_schema_version": "ootang_five_seed_training_manifest_v1",
        "checkpoint_schema_version": "ootang_convlstm_safe_checkpoint_v1",
        "checkpoint_format": "pytorch_weights_only_tensor_bundle_v1",
        "receipt_schema_version": "ootang_issue_replay_receipt_v1",
        "status_schema_version": "ootang_issue_replay_status_v1",
        "progress_schema_version": "ootang_issue_replay_progress_v1",
        "source_dataset_schema_version": "ootang_canonical_model_source_v1",
        "seeds": [0, 1, 2, 3, 4],
        "lookback_days": 7,
        "horizon_days": 1,
        "grid_height": 4,
        "grid_width": 7,
        "input_channels": 7,
        "hidden_channels": 16,
        "kernel_size": 3,
        "quantiles": [0.1, 0.5, 0.9],
        "station_order_model": [
            "MJ9",
            "MJ1",
            "MJ3",
            "ATU1",
            "ATU2",
            "ATU3",
            "ATU4",
            "ATU5",
        ],
        "station_order_live": [
            "ATU1",
            "ATU2",
            "ATU3",
            "ATU4",
            "ATU5",
            "MJ1",
            "MJ3",
            "MJ9",
        ],
        "displacement_columns": [
            "MJ9_disp",
            "MJ1_disp",
            "MJ3_disp",
            "ATU1_disp",
            "ATU2_disp",
            "ATU3_disp",
            "ATU4_disp",
            "ATU5_disp",
        ],
        "exogenous_columns": [
            "RWL",
            "RWL_rate",
            "Rain_cum7",
            "Rain_cum15",
            "Rain_cum30",
        ],
        "static_spatial_columns": ["elev_m"],
        "input_schema": "displacement_elevation_exog_v1",
        "prediction_absolute_tolerance_mm": 1e-6,
        "prediction_relative_tolerance": 0.0,
        "checkpoint_max_bytes": 64 * 1024 * 1024,
        "device": "cpu",
        "torch_threads": 1,
        "deterministic_algorithms": True,
        "mkldnn_enabled": False,
        "outcome_read": False,
    }
    if verification != expected_verification:
        raise IssueReplayConfigError("verification scientific contract changed")

    runtime = _exact_keys(
        profile["runtime"],
        {
            "root",
            "status",
            "receipts",
            "objects",
            "issue_inbox",
            "ledger",
            "runner_lock",
            "replay_lock",
        },
        name="runtime",
    )
    for key, value in runtime.items():
        _required_string(value, name=f"runtime.{key}")

    deploy_payload = _decode_json(
        _read_regular_once(deploy.path, name="deploy profile", maximum_bytes=MAX_JSON_BYTES),
        name="deploy profile",
    )
    live_payload = _decode_json(
        _read_regular_once(live.path, name="live profile", maximum_bytes=MAX_JSON_BYTES),
        name="live profile",
    )
    if deploy_payload.get("profile_id") != "ootang-prequential-deploy-v1":
        raise IssueReplayConfigError("bound deploy profile identity changed")
    if live_payload.get("profile_id") != "ootang-prequential-live-v1":
        raise IssueReplayConfigError("bound live profile identity changed")
    deploy_runtime = deploy_payload.get("runtime")
    live_runtime = live_payload.get("runtime")
    if not isinstance(deploy_runtime, dict) or not isinstance(live_runtime, dict):
        raise IssueReplayConfigError("bound runtime contract is missing")
    expected_runtime_links = {
        "root": deploy_runtime.get("root"),
        "objects": deploy_runtime.get("objects"),
        "issue_inbox": deploy_runtime.get("issue_inbox"),
        "ledger": live_runtime.get("ledger"),
        "runner_lock": live_runtime.get("lock"),
    }
    for key, expected in expected_runtime_links.items():
        if runtime[key] != expected:
            raise IssueReplayConfigError(f"runtime.{key} differs from bound profiles")
    model = deploy_payload.get("model")
    source_feed = deploy_payload.get("source_feed")
    if not isinstance(model, dict) or not isinstance(source_feed, dict):
        raise IssueReplayConfigError("bound deploy scientific contract is missing")
    for key in (
        "seeds",
        "lookback_days",
        "horizon_days",
        "input_channels",
        "hidden_channels",
        "kernel_size",
        "quantiles",
        "displacement_columns",
        "exogenous_columns",
        "static_spatial_columns",
        "input_schema",
    ):
        if model.get(key) != verification[key]:
            raise IssueReplayConfigError(f"verification.{key} differs from deploy model")
    if (
        source_feed.get("station_order_model") != verification["station_order_model"]
        or source_feed.get("station_order_live") != verification["station_order_live"]
    ):
        raise IssueReplayConfigError("verification station orders changed")

    profile["_deploy_path"] = str(deploy.path)
    profile["_deploy_sha256"] = deploy.sha256
    profile["_deploy_payload"] = deploy_payload
    profile["_live_path"] = str(live.path)
    profile["_live_sha256"] = live.sha256
    profile["_live_payload"] = live_payload
    profile["_geometry"] = geometry.as_dict()
    profile["_source_authority"] = authority_artifact.as_dict()
    profile["_pyproject"] = pyproject.as_dict()
    profile["_uv_lock"] = uv_lock.as_dict()


def load_replay_profile(
    path: Path = DEFAULT_CONFIG_PATH, *, project_root: Path = ROOT
) -> dict[str, Any]:
    """Load and validate the exact versioned independent-replay contract."""

    project_root = project_root.resolve()
    resolved = path.resolve() if path.is_absolute() else (project_root / path).resolve()
    raw = _read_regular_once(
        resolved, name="issue replay profile", maximum_bytes=MAX_JSON_BYTES
    )
    profile_sha256 = _sha256_bytes(raw)
    if resolved == DEFAULT_CONFIG_PATH.resolve() and profile_sha256 != DEFAULT_CONFIG_SHA256:
        raise IssueReplayConfigError("default issue replay profile bytes changed")
    profile = _decode_json(raw, name="issue replay profile")
    _validate_profile(profile, project_root=project_root)
    profile["_profile_path"] = str(resolved)
    profile["_profile_sha256"] = profile_sha256
    profile["_project_root"] = str(project_root)
    return profile


# Short alias used by generic stage adapters.
load_config = load_replay_profile


def runtime_paths(
    profile: Mapping[str, Any], runtime_root: Path | None = None
) -> ReplayRuntimePaths:
    project_root = Path(str(profile["_project_root"])).resolve()
    runtime = profile["runtime"]
    root = (
        runtime_root.resolve()
        if runtime_root is not None
        else _project_path(runtime["root"], project_root=project_root, name="runtime.root")
    )
    deploy = profile["_deploy_payload"]
    model_relative = deploy["runtime"]["model_manifest"]
    activation_relative = deploy["runtime"]["activation_source_manifest"]
    paths = ReplayRuntimePaths(
        root=root,
        status=_relative_child(root, runtime["status"], name="runtime.status"),
        receipts=_relative_child(
            root,
            runtime["receipts"],
            name="runtime.receipts",
            leaf_directory=True,
        ),
        objects=_relative_child(
            root,
            runtime["objects"],
            name="runtime.objects",
            leaf_directory=True,
        ),
        issue_inbox=_relative_child(
            root,
            runtime["issue_inbox"],
            name="runtime.issue_inbox",
            leaf_directory=True,
        ),
        ledger=_relative_child(root, runtime["ledger"], name="runtime.ledger"),
        runner_lock=_relative_child(
            root, runtime["runner_lock"], name="runtime.runner_lock"
        ),
        replay_lock=_relative_child(
            root, runtime["replay_lock"], name="runtime.replay_lock"
        ),
        model_manifest=_relative_child(
            root, model_relative, name="deploy.runtime.model_manifest"
        ),
        activation_manifest=_relative_child(
            root,
            activation_relative,
            name="deploy.runtime.activation_source_manifest",
        ),
    )
    occupied = [
        paths.status,
        paths.receipts,
        paths.objects,
        paths.issue_inbox,
        paths.ledger,
        paths.runner_lock,
        paths.replay_lock,
        paths.model_manifest,
        paths.activation_manifest,
    ]
    if len(set(occupied)) != len(occupied):
        raise IssueReplayConfigError("issue replay runtime paths collide")
    return paths


def _acquire_lock(path: Path, *, name: str) -> BinaryIO:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
        handle = os.fdopen(descriptor, "a+b")
    except OSError as exc:
        raise IssueReplayIntegrityError(f"cannot open {name}: {path}") from exc
    try:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise IssueReplayIntegrityError(f"{name} must be a regular file")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        handle_stat = os.fstat(handle.fileno())
        path_stat = path.lstat()
        if (
            not stat.S_ISREG(path_stat.st_mode)
            or (handle_stat.st_dev, handle_stat.st_ino)
            != (path_stat.st_dev, path_stat.st_ino)
        ):
            raise IssueReplayIntegrityError(
                f"{name} pathname changed while acquiring lock"
            )
    except BlockingIOError as exc:
        handle.close()
        raise IssueReplayBusyError(f"another machine process owns {name}") from exc
    except Exception:
        handle.close()
        raise
    return handle


def _release_lock(handle: BinaryIO) -> None:
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _validate_runner_handle(handle: BinaryIO, expected_path: Path) -> None:
    try:
        handle_stat = os.fstat(handle.fileno())
        path_stat = expected_path.lstat()
    except (AttributeError, OSError, ValueError) as exc:
        raise IssueReplayIntegrityError("runner lock handle is invalid") from exc
    if (
        not stat.S_ISREG(handle_stat.st_mode)
        or not stat.S_ISREG(path_stat.st_mode)
        or (handle_stat.st_dev, handle_stat.st_ino)
        != (path_stat.st_dev, path_stat.st_ino)
    ):
        raise IssueReplayIntegrityError("runner lock handle does not match runner.lock")


def _atomic_replace(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as exc:
        raise IssueReplayIntegrityError(f"cannot atomically write {path}") from exc
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_create(path: Path, raw: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.parent.parent / ".issue_replay_receipt_scratch"
    try:
        scratch.mkdir(mode=0o700, exist_ok=True)
        scratch_stat = scratch.lstat()
    except OSError as exc:
        raise IssueReplayIntegrityError(
            "cannot prepare immutable receipt scratch directory"
        ) from exc
    if not stat.S_ISDIR(scratch_stat.st_mode) or stat.S_ISLNK(
        scratch_stat.st_mode
    ):
        raise IssueReplayIntegrityError(
            "immutable receipt scratch path is not a real directory"
        )
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=scratch
    )
    temporary = Path(name)
    created = True
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            created = False
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as exc:
        raise IssueReplayIntegrityError(f"cannot create immutable receipt {path}") from exc
    finally:
        if temporary.exists():
            temporary.unlink()
    return created


def _revoke_exact_created_receipt(path: Path, expected_raw: bytes) -> None:
    """Remove only bytes proven to be the receipt created by this invocation."""

    observed = _read_regular_once(
        path, name="newly created replay receipt", maximum_bytes=MAX_JSON_BYTES
    )
    try:
        path_stat = path.lstat()
    except OSError as exc:
        raise IssueReplayConflictError(
            "cannot inspect newly created replay receipt for revocation"
        ) from exc
    if observed != expected_raw or not stat.S_ISREG(path_stat.st_mode):
        raise IssueReplayConflictError(
            "newly created replay receipt changed before safe revocation"
        )
    try:
        path.unlink()
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as exc:
        raise IssueReplayConflictError(
            "cannot revoke newly created replay receipt"
        ) from exc


def _post_create_time_fence(
    *,
    receipt_path: Path,
    receipt_raw: bytes,
    verified_at: datetime,
    target: date,
    timezone_name: str,
    clock: Clock,
) -> datetime:
    """Confirm durable publication remained before target start or revoke it."""

    try:
        return _verified_completion_time(
            started_at=verified_at,
            completed_at=clock(),
            target=target,
            timezone_name=timezone_name,
        )
    except Exception:
        _revoke_exact_created_receipt(receipt_path, receipt_raw)
        raise


def _artifact_from_reference(
    value: object,
    *,
    name: str,
    expected_root: Path | None = None,
    exact_path: Path | None = None,
    maximum_bytes: int = MAX_JSON_BYTES,
) -> tuple[Artifact, bytes]:
    record = _exact_keys(value, {"path", "sha256", "size_bytes"}, name=name)
    candidate = Path(_required_string(record["path"], name=f"{name}.path"))
    if not candidate.is_absolute():
        raise IssueReplayIntegrityError(f"{name} path must be absolute")
    current = Path(candidate.anchor)
    try:
        for index, component in enumerate(candidate.parts[1:], start=1):
            current /= component
            observed = current.lstat()
            if stat.S_ISLNK(observed.st_mode):
                raise IssueReplayIntegrityError(
                    f"{name} path contains a symbolic link"
                )
            if index < len(candidate.parts) - 1 and not stat.S_ISDIR(
                observed.st_mode
            ):
                raise IssueReplayIntegrityError(
                    f"{name} path parent is not a directory"
                )
        final_stat = candidate.lstat()
    except IssueReplayError:
        raise
    except OSError as exc:
        raise IssueReplayIntegrityError(f"cannot inspect {name} path") from exc
    if not stat.S_ISREG(final_stat.st_mode):
        raise IssueReplayIntegrityError(f"{name} path is not a regular file")
    path = candidate.resolve(strict=True)
    sha256 = _required_sha256(record["sha256"], name=f"{name}.sha256")
    size = _required_int(record["size_bytes"], name=f"{name}.size_bytes")
    if exact_path is not None and path != exact_path.resolve():
        raise IssueReplayIntegrityError(f"{name} path changed")
    if expected_root is not None:
        resolved_root = expected_root.resolve()
        try:
            path.relative_to(resolved_root)
        except ValueError as exc:
            raise IssueReplayIntegrityError(f"{name} escapes content object root") from exc
        if path.parent != resolved_root:
            raise IssueReplayIntegrityError(
                f"{name} is not a direct content object child"
            )
    raw = _read_regular_once(path, name=name, maximum_bytes=maximum_bytes)
    if len(raw) != size or _sha256_bytes(raw) != sha256:
        raise IssueReplayIntegrityError(f"{name} hash or size mismatch")
    return Artifact(path, sha256, size), raw


def _artifact_from_path(path: Path, *, name: str) -> tuple[Artifact, bytes]:
    raw = _read_regular_once(path, name=name, maximum_bytes=MAX_JSON_BYTES)
    return Artifact(path.resolve(), _sha256_bytes(raw), len(raw)), raw


def _require_content_name(artifact: Artifact, *, suffix: str) -> None:
    expected = f"{artifact.sha256}{suffix}"
    if artifact.path.name != expected:
        raise IssueReplayIntegrityError(
            f"content-addressed artifact name changed: expected {expected}"
        )


def _runtime_environment() -> dict[str, str]:
    return {
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "numpy_version": str(np.__version__),
        "pandas_version": str(pd.__version__),
        "torch_version": str(torch.__version__),
    }


def _input_schema_sha256(profile: Mapping[str, Any]) -> str:
    contract = profile["verification"]
    geometry = profile["_geometry"]
    return _canonical_sha256(
        {
            "schema_version": "ootang_convlstm_input_schema_binding_v1",
            "input_schema": contract["input_schema"],
            "input_channels": contract["input_channels"],
            "displacement_columns": contract["displacement_columns"],
            "exogenous_columns": contract["exogenous_columns"],
            "static_spatial_columns": contract["static_spatial_columns"],
            "stations_model": contract["station_order_model"],
            "stations_live": contract["station_order_live"],
            "station_geometry_sha256": geometry["sha256"],
            "grid_height": contract["grid_height"],
            "grid_width": contract["grid_width"],
            "interpolation": "horizontal_idw_power_2",
            "elevation": "station_zscore_then_horizontal_idw_static_channel",
            "normalization_scope": "all_rows_available_at_source_watermark",
        }
    )


def _load_geometry(profile: Mapping[str, Any]) -> tuple[list[str], np.ndarray, np.ndarray]:
    artifact_record = profile["_geometry"]
    artifact, raw = _artifact_from_reference(
        artifact_record,
        name="station geometry",
        exact_path=Path(str(artifact_record["path"])),
    )
    if artifact.sha256 != artifact_record["sha256"]:
        raise IssueReplayIntegrityError("station geometry binding changed")
    try:
        frame = pd.read_csv(io.BytesIO(raw))
    except Exception as exc:
        raise IssueReplayIntegrityError("cannot parse station geometry") from exc
    required = {"station", "disp_col", "x_m", "y_m", "elev_m"}
    if not required.issubset(frame.columns):
        raise IssueReplayIntegrityError("station geometry columns changed")
    if frame["station"].duplicated().any() or frame["disp_col"].duplicated().any():
        raise IssueReplayIntegrityError("station geometry identifiers are not unique")
    columns = list(profile["verification"]["displacement_columns"])
    indexed = frame.set_index("disp_col", drop=False)
    if any(column not in indexed.index for column in columns):
        raise IssueReplayIntegrityError("station geometry lacks a model station")
    ordered = indexed.loc[columns]
    stations = ordered["station"].tolist()
    geometry = ordered[["x_m", "y_m", "elev_m"]].to_numpy(dtype=np.float64)
    if stations != profile["verification"]["station_order_model"]:
        raise IssueReplayIntegrityError("station geometry order changed")
    if geometry.shape != (8, 3) or not np.isfinite(geometry).all():
        raise IssueReplayIntegrityError("station geometry values are invalid")
    return stations, geometry[:, :2], geometry[:, 2]


def _grid(xy: np.ndarray, *, height: int, width: int) -> tuple[np.ndarray, np.ndarray]:
    minimum = xy.min(axis=0)
    maximum = xy.max(axis=0)
    padding = (maximum - minimum) * 0.05 + 1e-6
    xs = np.linspace(minimum[0] - padding[0], maximum[0] + padding[0], width)
    ys = np.linspace(maximum[1] + padding[1], minimum[1] - padding[1], height)
    return np.meshgrid(xs, ys)


def _interpolation_weights(
    xy: np.ndarray, grid_x: np.ndarray, grid_y: np.ndarray
) -> np.ndarray:
    points = np.column_stack([grid_x.ravel(), grid_y.ravel()])
    distance = np.linalg.norm(points[:, None, :] - xy[None, :, :], axis=2)
    distance = np.maximum(distance, 1e-6)
    weights = 1.0 / distance**2.0
    weights /= weights.sum(axis=1, keepdims=True)
    return weights


def _station_readout_weights(
    xy: np.ndarray, grid_x: np.ndarray, grid_y: np.ndarray
) -> np.ndarray:
    points = np.column_stack([grid_x.ravel(), grid_y.ravel()])
    distance = np.linalg.norm(xy[:, None, :] - points[None, :, :], axis=2)
    distance = np.maximum(distance, 1e-6)
    weights = 1.0 / distance**2.0
    weights /= weights.sum(axis=1, keepdims=True)
    return weights.astype(np.float32)


def _decode_canonical_dataset_frame(
    raw: bytes,
    *,
    expected_watermark: date,
    profile: Mapping[str, Any],
    name: str,
) -> pd.DataFrame:
    contract = profile["verification"]
    payload = _decode_json(raw, name=name)
    record = _exact_keys(
        payload,
        {
            "schema_version",
            "case",
            "date_timezone",
            "columns",
            "maximum_complete_finalized_date",
            "rows",
        },
        name=name,
    )
    columns = [
        "Date",
        *contract["displacement_columns"],
        "RWL",
        "RWL_rate",
        "Rain",
        "Rain_cum7",
        "Rain_cum15",
        "Rain_cum30",
    ]
    if (
        record["schema_version"] != contract["source_dataset_schema_version"]
        or record["case"] != "ootang"
        or record["date_timezone"]
        != profile["_deploy_payload"]["source_feed"]["date_timezone"]
        or record["columns"] != columns
        or record["maximum_complete_finalized_date"]
        != expected_watermark.isoformat()
    ):
        raise IssueReplayIntegrityError(f"{name} identity changed")
    rows = record["rows"]
    if not isinstance(rows, list) or not rows:
        raise IssueReplayIntegrityError(f"{name} rows are malformed")
    normalized: list[list[Any]] = []
    prior: date | None = None
    for index, row in enumerate(rows):
        if not isinstance(row, list) or len(row) != len(columns):
            raise IssueReplayIntegrityError(f"{name} row shape changed")
        day = _parse_date(row[0], name=f"{name}.rows[{index}].Date")
        if prior is not None and day != prior + timedelta(days=1):
            raise IssueReplayIntegrityError(f"{name} dates are not contiguous")
        normalized.append(
            [
                day.isoformat(),
                *[
                    _finite(value, name=f"{name}.rows[{index}].{column}")
                    for column, value in zip(columns[1:], row[1:], strict=True)
                ],
            ]
        )
        prior = day
    if prior != expected_watermark:
        raise IssueReplayIntegrityError(f"{name} watermark changed")
    frame = pd.DataFrame(normalized, columns=columns)
    frame[columns[1:]] = (
        frame[columns[1:]].apply(pd.to_numeric, errors="raise").astype(float)
    )
    return frame


def _validate_semantic_manifest_metadata(
    record: Mapping[str, Any],
    *,
    dataset: Artifact,
    frame: pd.DataFrame,
    expected_watermark: date,
    profile: Mapping[str, Any],
    paths: ReplayRuntimePaths,
    name: str,
) -> None:
    lineage = _exact_keys(
        record["lineage"],
        {"historical_base", "daily_feed_snapshot"},
        name=f"{name}.lineage",
    )
    historical = _exact_keys(
        lineage["historical_base"],
        {"path", "sha256", "size_bytes", "rows", "last_date", "role"},
        name=f"{name}.historical_base",
    )
    historical_contract = profile["_deploy_payload"]["historical_base"]
    historical_path = _project_path(
        historical_contract["path"],
        project_root=Path(str(profile["_project_root"])),
        name="historical_base.path",
    )
    historical_artifact, _ = _artifact_from_reference(
        {key: historical[key] for key in ("path", "sha256", "size_bytes")},
        name=f"{name}.historical_base.artifact",
        exact_path=historical_path,
    )
    if (
        historical_artifact.sha256 != historical_contract["sha256"]
        or historical["rows"] != historical_contract["rows"]
        or historical["last_date"] != historical_contract["last_date"]
        or historical["role"] != historical_contract["role"]
    ):
        raise IssueReplayIntegrityError(f"{name} historical lineage changed")
    feed = _exact_keys(
        lineage["daily_feed_snapshot"],
        {
            "path",
            "sha256",
            "size_bytes",
            "schema_version",
            "exported_at_utc",
            "first_date",
            "last_date",
            "rows",
            "date_revision_pairs_sha256",
        },
        name=f"{name}.daily_feed_snapshot",
    )
    feed_artifact, _ = _artifact_from_reference(
        {key: feed[key] for key in ("path", "sha256", "size_bytes")},
        name=f"{name}.daily_feed_snapshot.artifact",
        expected_root=paths.objects,
    )
    _require_content_name(feed_artifact, suffix=".feed.json")
    first_feed_date = _parse_date(feed["first_date"], name=f"{name}.feed.first")
    last_feed_date = _parse_date(feed["last_date"], name=f"{name}.feed.last")
    feed_rows = _required_int(feed["rows"], name=f"{name}.feed.rows", minimum=1)
    _parse_utc(feed["exported_at_utc"], name=f"{name}.feed.exported_at")
    _required_sha256(
        feed["date_revision_pairs_sha256"], name=f"{name}.feed.revision digest"
    )
    if (
        feed["schema_version"]
        != profile["_deploy_payload"]["source_feed"]["schema_version"]
        or last_feed_date != expected_watermark
        or feed_rows != (last_feed_date - first_feed_date).days + 1
    ):
        raise IssueReplayIntegrityError(f"{name} feed lineage changed")
    semantics = _exact_keys(
        record["semantics"],
        {
            "date_timezone",
            "recorded_time_timezone",
            "frequency",
            "station_order_live",
            "station_order_model",
            "model_columns",
            "units",
            "derived_features",
            "external_derived_columns_accepted",
            "all_records_finalized",
            "daily_continuity_verified",
            "finite_values_verified",
        },
        name=f"{name}.semantics",
    )
    source_contract = profile["_deploy_payload"]["source_feed"]
    if semantics != {
        "date_timezone": source_contract["date_timezone"],
        "recorded_time_timezone": source_contract["recorded_time_timezone"],
        "frequency": source_contract["expected_frequency"],
        "station_order_live": source_contract["station_order_live"],
        "station_order_model": source_contract["station_order_model"],
        "model_columns": list(frame.columns),
        "units": source_contract["units"],
        "derived_features": source_contract["derived_features"],
        "external_derived_columns_accepted": False,
        "all_records_finalized": True,
        "daily_continuity_verified": True,
        "finite_values_verified": True,
    }:
        raise IssueReplayIntegrityError(f"{name} scientific semantics changed")
    summary = _exact_keys(
        record["dataset_summary"],
        {"rows", "first_date", "last_date"},
        name=f"{name}.dataset_summary",
    )
    if summary != {
        "rows": len(frame),
        "first_date": str(frame.iloc[0]["Date"]),
        "last_date": str(frame.iloc[-1]["Date"]),
    } or record["canonical_dataset"] != dataset.as_dict():
        raise IssueReplayIntegrityError(f"{name} dataset summary changed")


class _IndependentConvLSTMCell(nn.Module):
    def __init__(self, input_channels: int, hidden_channels: int, kernel_size: int):
        super().__init__()
        self.hidden_channels = hidden_channels
        self.conv = nn.Conv2d(
            input_channels + hidden_channels,
            4 * hidden_channels,
            kernel_size,
            padding=kernel_size // 2,
        )

    def forward(
        self, value: torch.Tensor, hidden: torch.Tensor, cell: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor]:
        gates = self.conv(torch.cat([value, hidden], dim=1))
        input_gate, forget_gate, output_gate, candidate = torch.chunk(gates, 4, dim=1)
        input_gate = torch.sigmoid(input_gate)
        forget_gate = torch.sigmoid(forget_gate)
        output_gate = torch.sigmoid(output_gate)
        candidate = torch.tanh(candidate)
        next_cell = forget_gate * cell + input_gate * candidate
        next_hidden = output_gate * torch.tanh(next_cell)
        return next_hidden, next_cell


class _IndependentConvLSTMForecast(nn.Module):
    def __init__(self, input_channels: int, hidden_channels: int, kernel_size: int):
        super().__init__()
        self.cell = _IndependentConvLSTMCell(
            input_channels, hidden_channels, kernel_size
        )
        self.head = nn.Conv2d(hidden_channels, 3, 1)

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        if value.ndim != 5:
            raise IssueReplayIntegrityError("independent model input rank changed")
        batch, steps, _, height, width = value.shape
        hidden = value.new_zeros(batch, self.cell.hidden_channels, height, width)
        cell = value.new_zeros(batch, self.cell.hidden_channels, height, width)
        for step in range(steps):
            hidden, cell = self.cell(value[:, step], hidden, cell)
        raw = self.head(hidden)
        low_width = torch_f.softplus(raw[:, 0:1])
        median = raw[:, 1:2]
        high_width = torch_f.softplus(raw[:, 2:3])
        return torch.cat([median - low_width, median, median + high_width], dim=1)


def _require_tensor(
    value: object,
    *,
    name: str,
    shape: tuple[int, ...],
    dtype: torch.dtype,
    positive: bool = False,
) -> torch.Tensor:
    if type(value) is not torch.Tensor:
        raise IssueReplayIntegrityError(f"{name} must be a plain tensor")
    tensor = value
    if (
        tensor.device.type != "cpu"
        or tensor.layout != torch.strided
        or tensor.dtype != dtype
        or tuple(tensor.shape) != shape
        or tensor.requires_grad
        or not tensor.is_contiguous()
        or tensor.storage_offset() != 0
        or tensor.untyped_storage().nbytes()
        != tensor.numel() * tensor.element_size()
    ):
        raise IssueReplayIntegrityError(f"{name} tensor metadata changed")
    if not torch.isfinite(tensor).all():
        raise IssueReplayIntegrityError(f"{name} must be finite")
    if positive and not torch.all(tensor > 0):
        raise IssueReplayIntegrityError(f"{name} must be strictly positive")
    return tensor.detach().clone().contiguous()


def _load_checkpoint(
    artifact: Artifact,
    raw: bytes,
    *,
    expected_seed: int,
    expected_source: Mapping[str, Any],
    expected_preprocessing: _ActivationPreprocessing,
    profile: Mapping[str, Any],
) -> _Checkpoint:
    contract = profile["verification"]
    try:
        payload = torch.load(io.BytesIO(raw), map_location="cpu", weights_only=True)
    except Exception as exc:
        raise IssueReplayIntegrityError(
            "checkpoint is not a safe weights-only tensor bundle"
        ) from exc
    record = _exact_keys(
        payload,
        {
            "schema_version",
            "checkpoint_format",
            "case",
            "seed",
            "source",
            "architecture",
            "feature_schema",
            "normalization",
            "spatial",
            "state_dict",
        },
        name=f"checkpoint[{expected_seed}]",
    )
    if (
        record["schema_version"] != contract["checkpoint_schema_version"]
        or record["checkpoint_format"] != contract["checkpoint_format"]
        or record["case"] != "ootang"
        or record["seed"] != expected_seed
    ):
        raise IssueReplayIntegrityError("checkpoint identity or seed changed")
    source = _exact_keys(
        record["source"],
        {
            "training_cutoff_date",
            "activation_captured_at_utc",
            "activation_source_manifest_sha256",
            "dataset_sha256",
            "semantic_manifest_sha256",
        },
        name=f"checkpoint[{expected_seed}].source",
    )
    if source != expected_source:
        raise IssueReplayIntegrityError("checkpoint source binding changed")
    architecture = _exact_keys(
        record["architecture"],
        {
            "model_class",
            "input_channels",
            "hidden_channels",
            "kernel_size",
            "quantiles",
            "lookback_days",
            "horizon_days",
            "grid_h",
            "grid_w",
        },
        name=f"checkpoint[{expected_seed}].architecture",
    )
    if architecture != {
        "model_class": "ConvLSTMForecast",
        "input_channels": contract["input_channels"],
        "hidden_channels": contract["hidden_channels"],
        "kernel_size": contract["kernel_size"],
        "quantiles": contract["quantiles"],
        "lookback_days": contract["lookback_days"],
        "horizon_days": contract["horizon_days"],
        "grid_h": contract["grid_height"],
        "grid_w": contract["grid_width"],
    }:
        raise IssueReplayIntegrityError("checkpoint architecture changed")
    feature = _exact_keys(
        record["feature_schema"],
        {
            "input_schema",
            "displacement_columns",
            "exogenous_columns",
            "static_spatial_columns",
            "stations_model",
            "station_geometry_sha256",
        },
        name=f"checkpoint[{expected_seed}].feature_schema",
    )
    if feature != {
        "input_schema": contract["input_schema"],
        "displacement_columns": contract["displacement_columns"],
        "exogenous_columns": contract["exogenous_columns"],
        "static_spatial_columns": contract["static_spatial_columns"],
        "stations_model": contract["station_order_model"],
        "station_geometry_sha256": profile["_geometry"]["sha256"],
    }:
        raise IssueReplayIntegrityError("checkpoint feature schema changed")

    normalization = _exact_keys(
        record["normalization"],
        {
            "displacement_mean",
            "displacement_scale",
            "exogenous_mean",
            "exogenous_scale",
            "delta_scale",
            "elevation_mean_m",
            "elevation_scale_m",
        },
        name=f"checkpoint[{expected_seed}].normalization",
    )
    displacement_mean = _require_tensor(
        normalization["displacement_mean"],
        name="normalization.displacement_mean",
        shape=(8,),
        dtype=torch.float64,
    ).numpy()
    displacement_scale = _require_tensor(
        normalization["displacement_scale"],
        name="normalization.displacement_scale",
        shape=(8,),
        dtype=torch.float64,
        positive=True,
    ).numpy()
    exogenous_mean = _require_tensor(
        normalization["exogenous_mean"],
        name="normalization.exogenous_mean",
        shape=(5,),
        dtype=torch.float64,
    ).numpy()
    exogenous_scale = _require_tensor(
        normalization["exogenous_scale"],
        name="normalization.exogenous_scale",
        shape=(5,),
        dtype=torch.float64,
        positive=True,
    ).numpy()
    delta_scale = _require_tensor(
        normalization["delta_scale"],
        name="normalization.delta_scale",
        shape=(8,),
        dtype=torch.float64,
        positive=True,
    ).numpy()
    elevation_mean = _finite(
        normalization["elevation_mean_m"], name="normalization.elevation_mean_m"
    )
    elevation_scale = _finite(
        normalization["elevation_scale_m"], name="normalization.elevation_scale_m"
    )
    if elevation_scale <= 0:
        raise IssueReplayIntegrityError("checkpoint elevation scale is not positive")
    for name, observed, expected in (
        (
            "displacement_mean",
            displacement_mean,
            expected_preprocessing.displacement_mean,
        ),
        (
            "displacement_scale",
            displacement_scale,
            expected_preprocessing.displacement_scale,
        ),
        ("exogenous_mean", exogenous_mean, expected_preprocessing.exogenous_mean),
        (
            "exogenous_scale",
            exogenous_scale,
            expected_preprocessing.exogenous_scale,
        ),
        ("delta_scale", delta_scale, expected_preprocessing.delta_scale),
    ):
        if not np.array_equal(observed, expected):
            raise IssueReplayIntegrityError(
                f"checkpoint {name} differs from immutable activation preprocessing"
            )

    spatial = _exact_keys(
        record["spatial"],
        {"elevation_grid", "readout_weights"},
        name=f"checkpoint[{expected_seed}].spatial",
    )
    elevation_grid = _require_tensor(
        spatial["elevation_grid"],
        name="spatial.elevation_grid",
        shape=(contract["grid_height"], contract["grid_width"]),
        dtype=torch.float32,
    ).numpy()
    readout_weights = _require_tensor(
        spatial["readout_weights"],
        name="spatial.readout_weights",
        shape=(8, contract["grid_height"] * contract["grid_width"]),
        dtype=torch.float32,
        positive=True,
    ).numpy()

    _, xy, elevation = _load_geometry(profile)
    grid_x, grid_y = _grid(
        xy, height=contract["grid_height"], width=contract["grid_width"]
    )
    interpolation = _interpolation_weights(xy, grid_x, grid_y)
    expected_elevation = (
        ((elevation - float(elevation.mean())) / float(elevation.std()))
        @ interpolation.T
    ).reshape(contract["grid_height"], contract["grid_width"]).astype(np.float32)
    expected_readout = _station_readout_weights(xy, grid_x, grid_y)
    if (
        not np.array_equal(elevation_grid, expected_elevation)
        or not np.array_equal(readout_weights, expected_readout)
        or elevation_mean != float(elevation.mean())
        or elevation_scale != float(elevation.std())
    ):
        raise IssueReplayIntegrityError("checkpoint spatial preprocessing changed")
    row_sums = readout_weights.astype(np.float64).sum(axis=1)
    if not np.allclose(row_sums, np.ones(8), rtol=0, atol=1e-6):
        raise IssueReplayIntegrityError("checkpoint readout weights are not normalized")

    model = _IndependentConvLSTMForecast(
        contract["input_channels"],
        contract["hidden_channels"],
        contract["kernel_size"],
    )
    state = record["state_dict"]
    if not isinstance(state, dict) or any(not isinstance(key, str) for key in state):
        raise IssueReplayIntegrityError("checkpoint state_dict is malformed")
    expected_state = model.state_dict()
    if set(state) != set(expected_state):
        raise IssueReplayIntegrityError("checkpoint state_dict keys changed")
    checked_state: dict[str, torch.Tensor] = {}
    for key, template in expected_state.items():
        checked_state[key] = _require_tensor(
            state[key],
            name=f"state_dict.{key}",
            shape=tuple(template.shape),
            dtype=template.dtype,
        )
    try:
        model.load_state_dict(checked_state, strict=True)
    except RuntimeError as exc:
        raise IssueReplayIntegrityError("checkpoint state_dict was rejected") from exc
    model.eval()
    return _Checkpoint(
        seed=expected_seed,
        artifact=artifact,
        model=model,
        displacement_mean=displacement_mean,
        displacement_scale=displacement_scale,
        exogenous_mean=exogenous_mean,
        exogenous_scale=exogenous_scale,
        delta_scale=delta_scale,
        elevation_mean_m=elevation_mean,
        elevation_scale_m=elevation_scale,
        elevation_grid=elevation_grid,
        readout_weights=readout_weights,
    )


def _activation_preprocessing(
    *,
    activation: Artifact,
    activation_payload: Mapping[str, Any],
    training_source: Mapping[str, Any],
    prerequisite_data_manifest: object,
    paths: ReplayRuntimePaths,
    profile: Mapping[str, Any],
) -> _ActivationPreprocessing:
    """Rebuild frozen training normalization from immutable activation bytes."""

    contract = profile["verification"]
    activation_record = _exact_keys(
        activation_payload,
        {
            "schema_version",
            "case",
            "captured_at_utc",
            "maximum_complete_finalized_date",
            "stations",
            "outcome_source_id",
            "data_manifest",
            "latest_finalized_displacement_mm",
        },
        name="activation source manifest",
    )
    if (
        activation_record["schema_version"]
        != profile["_live_payload"]["activation"]["source_snapshot_schema_version"]
        or activation_record["case"] != "ootang"
        or activation_record["stations"] != contract["station_order_live"]
    ):
        raise IssueReplayIntegrityError("activation source identity changed")
    captured_at = _required_string(
        activation_record["captured_at_utc"], name="activation.captured_at_utc"
    )
    _parse_utc(captured_at, name="activation.captured_at_utc")
    cutoff = _parse_date(
        activation_record["maximum_complete_finalized_date"],
        name="activation.maximum_complete_finalized_date",
    )
    _required_string(
        activation_record["outcome_source_id"], name="activation.outcome_source_id"
    )
    latest = _exact_keys(
        activation_record["latest_finalized_displacement_mm"],
        set(contract["station_order_live"]),
        name="activation.latest_finalized_displacement_mm",
    )
    for station in contract["station_order_live"]:
        _finite(latest[station], name=f"activation.latest.{station}")

    try:
        prerequisite_manifest = {
            "path": str(prerequisite_data_manifest.path),
            "sha256": prerequisite_data_manifest.sha256,
            "size_bytes": prerequisite_data_manifest.size_bytes,
        }
    except (AttributeError, TypeError) as exc:
        raise IssueReplayIntegrityError(
            "activation prerequisite data manifest is malformed"
        ) from exc
    if activation_record["data_manifest"] != prerequisite_manifest:
        raise IssueReplayIntegrityError("activation data manifest binding changed")
    semantic_artifact, semantic_raw = _artifact_from_reference(
        activation_record["data_manifest"],
        name="activation semantic manifest",
        expected_root=paths.objects,
    )
    _require_content_name(semantic_artifact, suffix=".source-manifest.json")
    semantic = _decode_json(semantic_raw, name="activation semantic manifest")
    semantic_record = _exact_keys(
        semantic,
        {
            "schema_version",
            "case",
            "outcome_source_id",
            "maximum_complete_finalized_date",
            "canonical_dataset",
            "lineage",
            "semantics",
            "dataset_summary",
        },
        name="activation semantic manifest",
    )
    if (
        semantic_record["schema_version"] != "ootang_source_semantic_manifest_v1"
        or semantic_record["case"] != "ootang"
        or semantic_record["outcome_source_id"]
        != activation_record["outcome_source_id"]
        or semantic_record["maximum_complete_finalized_date"] != cutoff.isoformat()
    ):
        raise IssueReplayIntegrityError("activation semantic identity changed")
    dataset_artifact, dataset_raw = _artifact_from_reference(
        semantic_record["canonical_dataset"],
        name="activation canonical dataset",
        expected_root=paths.objects,
    )
    _require_content_name(dataset_artifact, suffix=".source.json")
    if (
        activation.sha256
        != training_source["activation_source_manifest_sha256"]
        or captured_at != training_source["activation_captured_at_utc"]
        or cutoff.isoformat() != training_source["training_cutoff_date"]
        or semantic_artifact.sha256 != training_source["semantic_manifest_sha256"]
        or dataset_artifact.sha256 != training_source["dataset_sha256"]
    ):
        raise IssueReplayIntegrityError("training source differs from activation lineage")

    activation_frame = _decode_canonical_dataset_frame(
        dataset_raw,
        expected_watermark=cutoff,
        profile=profile,
        name="activation canonical dataset",
    )
    _validate_semantic_manifest_metadata(
        semantic_record,
        dataset=dataset_artifact,
        frame=activation_frame,
        expected_watermark=cutoff,
        profile=profile,
        paths=paths,
        name="activation semantic manifest",
    )
    minimum_windows = profile["_deploy_payload"]["model"][
        "minimum_training_windows"
    ]
    expected_windows = (
        len(activation_frame)
        - contract["lookback_days"]
        - contract["horizon_days"]
        + 1
    )
    if expected_windows < minimum_windows:
        raise IssueReplayIntegrityError("activation dataset lacks frozen training windows")
    # The frozen producer selects columns from a pandas frame before numpy's
    # reductions.  Reconstructing that independently is intentional: its
    # Fortran-strided column blocks determine bit-level float64 accumulation,
    # and a row-major shortcut differs by roughly 1e-13 on real data.
    displacement = activation_frame[contract["displacement_columns"]].to_numpy(
        dtype=np.float64
    )
    exogenous = activation_frame[contract["exogenous_columns"]].to_numpy(
        dtype=np.float64
    )
    # Match the frozen producer's explicit window loop.  Building a fresh
    # row-major array is scientifically significant here: subtracting two
    # strided pandas-derived views produces a differently-strided array and
    # can change float64 std reduction in the final bits.
    delta = np.asarray(
        [
            displacement[
                index
                + contract["lookback_days"]
                + contract["horizon_days"]
                - 1
            ]
            - displacement[index + contract["lookback_days"] - 1]
            for index in range(expected_windows)
        ],
        dtype=np.float64,
    )
    if delta.shape != (expected_windows, len(contract["displacement_columns"])):
        raise IssueReplayIntegrityError("activation delta windows changed")
    floor = profile["_deploy_payload"]["model"]["delta_scale_floor_mm"]
    return _ActivationPreprocessing(
        semantic_manifest=semantic_artifact,
        canonical_dataset=dataset_artifact,
        displacement_mean=displacement.mean(axis=0),
        displacement_scale=np.maximum(displacement.std(axis=0), 1.0),
        exogenous_mean=exogenous.mean(axis=0),
        exogenous_scale=np.maximum(exogenous.std(axis=0), 1e-6),
        delta_scale=np.maximum(delta.std(axis=0), floor),
    )


def _configure_inference(profile: Mapping[str, Any]) -> None:
    contract = profile["verification"]
    if contract["device"] != "cpu":
        raise IssueReplayConfigError("independent replay is CPU-only")
    try:
        torch.set_num_threads(contract["torch_threads"])
        torch.use_deterministic_algorithms(True)
        torch.backends.mkldnn.enabled = False
    except (RuntimeError, ValueError) as exc:
        raise IssueReplayIntegrityError(
            "cannot configure deterministic CPU inference"
        ) from exc
    if (
        torch.get_num_threads() != 1
        or not torch.are_deterministic_algorithms_enabled()
        or torch.backends.mkldnn.enabled
    ):
        raise IssueReplayIntegrityError("deterministic inference settings did not apply")


def _predict_checkpoint(
    checkpoint: _Checkpoint,
    rows: Sequence[Mapping[str, Any]],
    profile: Mapping[str, Any],
) -> dict[str, float]:
    contract = profile["verification"]
    displacement = np.asarray(
        [
            [_finite(row[column], name=f"input.{column}") for column in contract["displacement_columns"]]
            for row in rows
        ],
        dtype=np.float64,
    )
    exogenous = np.asarray(
        [
            [_finite(row[column], name=f"input.{column}") for column in contract["exogenous_columns"]]
            for row in rows
        ],
        dtype=np.float64,
    )
    _, xy, _ = _load_geometry(profile)
    grid_x, grid_y = _grid(
        xy, height=contract["grid_height"], width=contract["grid_width"]
    )
    weights = _interpolation_weights(xy, grid_x, grid_y)
    displacement_normalized = (
        displacement - checkpoint.displacement_mean
    ) / checkpoint.displacement_scale
    displacement_grid = (displacement_normalized @ weights.T).reshape(
        contract["lookback_days"],
        1,
        contract["grid_height"],
        contract["grid_width"],
    ).astype(np.float32)
    elevation_channel = np.broadcast_to(
        checkpoint.elevation_grid[None, None, :, :],
        (
            contract["lookback_days"],
            1,
            contract["grid_height"],
            contract["grid_width"],
        ),
    )
    exogenous_normalized = (
        (exogenous - checkpoint.exogenous_mean) / checkpoint.exogenous_scale
    ).astype(np.float32)
    exogenous_grid = np.broadcast_to(
        exogenous_normalized[:, :, None, None],
        (
            contract["lookback_days"],
            len(contract["exogenous_columns"]),
            contract["grid_height"],
            contract["grid_width"],
        ),
    )
    inputs = np.concatenate(
        [displacement_grid, elevation_channel, exogenous_grid], axis=1
    ).astype(np.float32)
    if inputs.shape != (7, 7, 4, 7) or not np.isfinite(inputs).all():
        raise IssueReplayIntegrityError("independent model inputs are invalid")
    tensor = torch.from_numpy(inputs[None, :, :, :, :])
    with torch.no_grad():
        grid_prediction = checkpoint.model(tensor)
        flattened = grid_prediction.reshape(1, 3, 4 * 7)
        station_prediction = torch.einsum(
            "bqm,nm->bqn",
            flattened,
            torch.from_numpy(checkpoint.readout_weights),
        )[0]
    median_delta = (
        station_prediction[1].to(torch.float64).numpy() * checkpoint.delta_scale
    )
    cumulative = displacement[-1] + median_delta
    if cumulative.shape != (8,) or not np.isfinite(cumulative).all():
        raise IssueReplayIntegrityError("independent checkpoint inference is invalid")
    return {
        station: float(cumulative[index])
        for index, station in enumerate(contract["station_order_model"])
    }


def _validate_issue_implementation(
    value: object, *, profile: Mapping[str, Any]
) -> dict[str, Any]:
    record = _exact_keys(
        value,
        {
            "schema_version",
            "deploy_profile",
            "issue_producer",
            "pyproject",
            "uv_lock",
            "runtime",
        },
        name="input.implementation",
    )
    if record["schema_version"] != "ootang_issue_producer_implementation_v1":
        raise IssueReplayIntegrityError("issue implementation schema changed")
    project_root = Path(str(profile["_project_root"]))
    expected_paths = {
        "deploy_profile": Path(str(profile["_deploy_path"])),
        "issue_producer": project_root / "code/monitoring/ootang_issue_producer.py",
        "pyproject": project_root / "pyproject.toml",
        "uv_lock": project_root / "uv.lock",
    }
    artifacts: dict[str, Artifact] = {}
    for key, expected_path in expected_paths.items():
        artifact, _ = _artifact_from_reference(
            record[key], name=f"input.implementation.{key}", exact_path=expected_path
        )
        artifacts[key] = artifact
    if artifacts["deploy_profile"].sha256 != profile["_deploy_sha256"]:
        raise IssueReplayIntegrityError("issue deploy profile binding changed")
    if artifacts["pyproject"].sha256 != profile["_pyproject"]["sha256"]:
        raise IssueReplayIntegrityError("issue pyproject binding changed")
    if artifacts["uv_lock"].sha256 != profile["_uv_lock"]["sha256"]:
        raise IssueReplayIntegrityError("issue dependency lock binding changed")
    runtime = _exact_keys(
        record["runtime"], set(_runtime_environment()), name="input.implementation.runtime"
    )
    if runtime != _runtime_environment():
        raise IssueReplayIntegrityError("issue runtime versions changed")
    return {
        "schema_version": record["schema_version"],
        "deploy_profile_sha256": artifacts["deploy_profile"].sha256,
        "issue_producer_sha256": artifacts["issue_producer"].sha256,
        "pyproject_sha256": artifacts["pyproject"].sha256,
        "uv_lock_sha256": artifacts["uv_lock"].sha256,
        **runtime,
    }


def _scientific_semantics(
    *, manifest: Mapping[str, Any], issue_stations: Sequence[Mapping[str, Any]], implementation: Mapping[str, Any]
) -> dict[str, Any]:
    source = manifest["source"]
    model = manifest["model"]
    return {
        "schema_version": "ootang_issue_scientific_semantics_v1",
        "case": "ootang",
        "target_date": manifest["target_date"],
        "source_watermark": manifest["source_watermark"],
        "canonical_dataset_sha256": source["canonical_dataset"]["sha256"],
        "activation_source_manifest_sha256": source["activation_source_manifest"]["sha256"],
        "model_manifest_sha256": model["outer_manifest"]["sha256"],
        "training_manifest_sha256": model["training_manifest"]["sha256"],
        "checkpoint_sha256_by_seed": {
            str(row["seed"]): row["artifact"]["sha256"]
            for row in model["checkpoints"]
        },
        "model_rows": [dict(row) for row in manifest["model_rows"]],
        "issued_station_experts": [dict(row) for row in issue_stations],
        "station_order_model": list(manifest["station_order_model"]),
        "station_order_live": list(manifest["station_order_live"]),
        "expert_order": list(manifest["expert_order"]),
        "implementation": dict(implementation),
    }


def _validate_training_manifest(
    training: Mapping[str, Any],
    *,
    outer: Mapping[str, Any],
    checkpoints: Sequence[dict[str, Any]],
    profile: Mapping[str, Any],
) -> Mapping[str, Any]:
    expected_keys = {
        "schema_version",
        "case",
        "model_version",
        "created_at_utc",
        "training_cutoff_date",
        "source_bindings",
        "input_schema_sha256",
        "training_policy",
        "stations",
        "seeds",
        "best_seed_selected",
        "training_windows",
        "seed_training",
        "checkpoints",
        "reload_replay",
        "implementation",
    }
    record = _exact_keys(training, expected_keys, name="training manifest")
    contract = profile["verification"]
    if (
        record["schema_version"] != contract["training_manifest_schema_version"]
        or record["case"] != "ootang"
        or record["model_version"] != outer["model_version"]
        or record["created_at_utc"] != outer["created_at_utc"]
        or record["training_cutoff_date"] != outer["training_cutoff_date"]
        or record["input_schema_sha256"] != _input_schema_sha256(profile)
        or record["stations"]
        != {
            "live": contract["station_order_live"],
            "model": contract["station_order_model"],
        }
        or record["seeds"] != contract["seeds"]
        or record["best_seed_selected"] is not False
        or record["checkpoints"] != list(checkpoints)
    ):
        raise IssueReplayIntegrityError("training manifest identity changed")
    source = _exact_keys(
        record["source_bindings"],
        {
            "training_cutoff_date",
            "activation_captured_at_utc",
            "activation_source_manifest_sha256",
            "dataset_sha256",
            "semantic_manifest_sha256",
        },
        name="training.source_bindings",
    )
    _parse_date(source["training_cutoff_date"], name="training cutoff")
    _parse_utc(source["activation_captured_at_utc"], name="activation capture")
    for key in (
        "activation_source_manifest_sha256",
        "dataset_sha256",
        "semantic_manifest_sha256",
    ):
        _required_sha256(source[key], name=f"training.source.{key}")
    policy = _exact_keys(
        record["training_policy"],
        {
            "fit_policy",
            "online_calibration_policy",
            "device",
            "deterministic_algorithms",
            "torch_threads",
            "epochs",
            "learning_rate",
            "holdout_windows",
            "best_seed_selected",
            "mkldnn_enabled",
            "test_epoch_override",
        },
        name="training.policy",
    )
    deploy_model = profile["_deploy_payload"]["model"]
    if (
        policy["fit_policy"] != deploy_model["fit_policy"]
        or policy["online_calibration_policy"] != deploy_model["online_calibration_policy"]
        or policy["device"] != "cpu"
        or policy["deterministic_algorithms"] is not True
        or policy["torch_threads"] != 1
        or policy["epochs"] != deploy_model["epochs"]
        or policy["learning_rate"] != deploy_model["learning_rate"]
        or policy["holdout_windows"] != 0
        or policy["best_seed_selected"] is not False
        or policy["mkldnn_enabled"] is not False
        or policy["test_epoch_override"] is not False
    ):
        raise IssueReplayIntegrityError("training policy changed")
    windows = _required_int(record["training_windows"], name="training_windows", minimum=365)
    histories = record["seed_training"]
    if not isinstance(histories, list) or len(histories) != 5:
        raise IssueReplayIntegrityError("training histories are incomplete")
    for seed, row in enumerate(histories):
        history = _exact_keys(
            row,
            {"seed", "epochs", "training_windows", "pinball_loss"},
            name=f"seed_training[{seed}]",
        )
        if (
            history["seed"] != seed
            or history["epochs"] != policy["epochs"]
            or history["training_windows"] != windows
            or not isinstance(history["pinball_loss"], list)
            or len(history["pinball_loss"]) != policy["epochs"]
        ):
            raise IssueReplayIntegrityError("training history changed")
        for loss in history["pinball_loss"]:
            _finite(loss, name=f"seed_training[{seed}].loss")
    replay = record["reload_replay"]
    if not isinstance(replay, list) or len(replay) != 5:
        raise IssueReplayIntegrityError("training reload replay is incomplete")
    for seed, row in enumerate(replay):
        replay_row = _exact_keys(
            row,
            {
                "seed",
                "in_memory_p50_mm",
                "reloaded_p50_mm",
                "max_abs_difference_mm",
                "absolute_tolerance_mm",
                "equivalent",
            },
            name=f"reload_replay[{seed}]",
        )
        if (
            replay_row["seed"] != seed
            or replay_row["equivalent"] is not True
            or _finite(replay_row["absolute_tolerance_mm"], name="reload tolerance")
            != contract["prediction_absolute_tolerance_mm"]
            or _finite(replay_row["max_abs_difference_mm"], name="reload difference")
            > contract["prediction_absolute_tolerance_mm"]
        ):
            raise IssueReplayIntegrityError("training reload replay changed")
        for key in ("in_memory_p50_mm", "reloaded_p50_mm"):
            values = replay_row[key]
            if not isinstance(values, list) or len(values) != 8:
                raise IssueReplayIntegrityError("reload prediction vector changed")
            for value in values:
                _finite(value, name="reload prediction")

    implementation = _exact_keys(
        record["implementation"],
        {
            "deploy_profile_sha256",
            "base_model_sha256",
            "producer_sha256",
            "pyproject_sha256",
            "uv_lock_sha256",
            "torch_version",
            "numpy_version",
        },
        name="training.implementation",
    )
    project_root = Path(str(profile["_project_root"]))
    expected_implementation = {
        "deploy_profile_sha256": profile["_deploy_sha256"],
        "base_model_sha256": _sha256_bytes(
            _read_regular_once(
                project_root / "code/convlstm/model.py",
                name="base model implementation",
                maximum_bytes=MAX_JSON_BYTES,
            )
        ),
        "producer_sha256": _sha256_bytes(
            _read_regular_once(
                project_root / "code/convlstm/ootang_production_bundle.py",
                name="bundle implementation",
                maximum_bytes=MAX_JSON_BYTES,
            )
        ),
        "pyproject_sha256": profile["_pyproject"]["sha256"],
        "uv_lock_sha256": profile["_uv_lock"]["sha256"],
        "torch_version": str(torch.__version__),
        "numpy_version": str(np.__version__),
    }
    if implementation != expected_implementation:
        raise IssueReplayIntegrityError("training implementation binding changed")
    identity_checkpoints = [
        {
            "seed": row["seed"],
            "sha256": row["artifact"]["sha256"],
            "size_bytes": row["artifact"]["size_bytes"],
        }
        for row in checkpoints
    ]
    identity = _canonical_sha256(
        {
            "schema_version": "ootang_five_seed_bundle_identity_v1",
            "source_bindings": dict(source),
            "input_schema_sha256": _input_schema_sha256(profile),
            "fit_policy": deploy_model["fit_policy"],
            "seeds": contract["seeds"],
            "epochs": policy["epochs"],
            "training_windows": windows,
            "checkpoints": identity_checkpoints,
        }
    )
    expected_version = f"{deploy_model['model_version_prefix']}-{identity[:16]}"
    if record["model_version"] != expected_version:
        raise IssueReplayIntegrityError("model version identity changed")
    return source


def _verify_materials(
    *,
    profile: Mapping[str, Any],
    paths: ReplayRuntimePaths,
    source: object,
    projection: object,
    prerequisites: object,
    issue_path: Path,
    now: datetime,
) -> _VerifiedInputs:
    contract = profile["verification"]
    target = projection.last_finalized_date + timedelta(days=1)
    if issue_path != paths.issue_inbox / f"{target.isoformat()}.json":
        raise IssueReplayIntegrityError("issue path is not the next ledger target")
    issue_artifact, issue_raw = _artifact_from_path(issue_path, name="pending issue")
    issue = _decode_json(issue_raw, name="pending issue")
    issue_record = _exact_keys(
        issue,
        {
            "schema_version",
            "target_date",
            "generated_at_utc",
            "source_as_of_at_utc",
            "source_snapshot_sha256",
            "model_manifest_sha256",
            "input_manifest",
            "stations",
        },
        name="pending issue",
    )
    if (
        issue_record["schema_version"] != contract["issue_schema_version"]
        or _parse_date(issue_record["target_date"], name="issue.target_date") != target
    ):
        raise IssueReplayIntegrityError("issue identity or target changed")
    generated = _parse_utc(issue_record["generated_at_utc"], name="issue.generated_at")
    source_as_of = _parse_utc(issue_record["source_as_of_at_utc"], name="issue.source_as_of")
    target_start = datetime.combine(
        target,
        time.min,
        tzinfo=ZoneInfo(profile["_deploy_payload"]["source_feed"]["date_timezone"]),
    ).astimezone(timezone.utc)
    machine_now = now.astimezone(timezone.utc)
    if source_as_of > generated or generated > machine_now:
        raise IssueReplayIntegrityError("issue time ordering changed")
    if generated >= target_start or source_as_of >= target_start or machine_now >= target_start:
        raise IssueReplayIntegrityError("issue replay did not complete before target start")
    if issue_record["source_snapshot_sha256"] != prerequisites.source.sha256:
        raise IssueReplayIntegrityError("issue activation source binding changed")
    if issue_record["model_manifest_sha256"] != prerequisites.model.sha256:
        raise IssueReplayIntegrityError("issue model binding changed")

    forbidden = {
        "actual",
        "actual_mm",
        "outcome",
        "reveal_actual_mm",
        "warning_color",
        "landslide_probability",
        "event_recall",
        "far",
    }
    stack: list[object] = [issue]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            present = {str(key).casefold() for key in value} & forbidden
            if present:
                raise IssueReplayIntegrityError(
                    "issue contains forbidden outcome fields: " + ",".join(sorted(present))
                )
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)

    input_artifact, input_raw = _artifact_from_reference(
        issue_record["input_manifest"],
        name="issue input manifest",
        expected_root=paths.objects,
    )
    _require_content_name(input_artifact, suffix=".json")
    manifest = _decode_json(input_raw, name="issue input manifest")
    manifest_record = _exact_keys(
        manifest,
        {
            "schema_version",
            "case",
            "target_date",
            "source_watermark",
            "scientific_semantics_sha256",
            "source",
            "model",
            "implementation",
            "model_rows",
            "station_order_model",
            "station_order_live",
            "expert_order",
        },
        name="issue input manifest",
    )
    watermark = _parse_date(
        manifest_record["source_watermark"], name="input.source_watermark"
    )
    if (
        manifest_record["schema_version"] != contract["input_manifest_schema_version"]
        or manifest_record["case"] != "ootang"
        or manifest_record["target_date"] != target.isoformat()
        or watermark != target - timedelta(days=1)
        or watermark != source.watermark
        or watermark != projection.last_finalized_date
    ):
        raise IssueReplayIntegrityError("input manifest date/source identity changed")
    if (
        manifest_record["station_order_model"] != contract["station_order_model"]
        or manifest_record["station_order_live"] != contract["station_order_live"]
        or manifest_record["expert_order"]
        != ["persistence", "seed0_p50", "seed1_p50", "seed2_p50", "seed3_p50", "seed4_p50"]
    ):
        raise IssueReplayIntegrityError("input station or expert order changed")

    source_value = _exact_keys(
        manifest_record["source"],
        {"canonical_dataset", "semantic_manifest", "activation_source_manifest"},
        name="input.source",
    )
    source_artifacts: dict[str, Artifact] = {}
    source_raws: dict[str, bytes] = {}
    suffixes = {
        "canonical_dataset": ".source.json",
        "semantic_manifest": ".source-manifest.json",
    }
    for key in ("canonical_dataset", "semantic_manifest"):
        artifact, raw = _artifact_from_reference(
            source_value[key], name=f"input.source.{key}", expected_root=paths.objects
        )
        _require_content_name(artifact, suffix=suffixes[key])
        source_artifacts[key] = artifact
        source_raws[key] = raw
    activation, activation_raw = _artifact_from_reference(
        source_value["activation_source_manifest"],
        name="input.source.activation_source_manifest",
        exact_path=paths.activation_manifest,
    )
    source_artifacts["activation_source_manifest"] = activation
    source_raws["activation_source_manifest"] = activation_raw
    expected_source_artifacts = {
        "canonical_dataset": source.dataset.as_dict(),
        "semantic_manifest": source.semantic_manifest.as_dict(),
        "activation_source_manifest": source.activation_manifest.as_dict(),
    }
    if {
        key: artifact.as_dict() for key, artifact in source_artifacts.items()
    } != expected_source_artifacts:
        raise IssueReplayIntegrityError("input source artifacts differ from source authority")

    dataset = _decode_json(source_raws["canonical_dataset"], name="canonical dataset")
    dataset_record = _exact_keys(
        dataset,
        {
            "schema_version",
            "case",
            "date_timezone",
            "columns",
            "maximum_complete_finalized_date",
            "rows",
        },
        name="canonical dataset",
    )
    expected_dataset_columns = [
        "Date",
        *contract["displacement_columns"],
        "RWL",
        "RWL_rate",
        "Rain",
        "Rain_cum7",
        "Rain_cum15",
        "Rain_cum30",
    ]
    if (
        dataset_record["schema_version"] != contract["source_dataset_schema_version"]
        or dataset_record["case"] != "ootang"
        or dataset_record["date_timezone"]
        != profile["_deploy_payload"]["source_feed"]["date_timezone"]
        or dataset_record["columns"] != expected_dataset_columns
        or dataset_record["maximum_complete_finalized_date"] != watermark.isoformat()
    ):
        raise IssueReplayIntegrityError("canonical dataset contract changed")
    dataset_rows = dataset_record["rows"]
    if not isinstance(dataset_rows, list) or len(dataset_rows) < 7:
        raise IssueReplayIntegrityError("canonical dataset lacks seven inference rows")
    selected_dataset = dataset_rows[-7:]
    input_columns = [*contract["displacement_columns"], *contract["exogenous_columns"]]
    manifest_rows = manifest_record["model_rows"]
    if not isinstance(manifest_rows, list) or len(manifest_rows) != 7:
        raise IssueReplayIntegrityError("input manifest must contain seven model rows")
    canonical_manifest_rows: list[dict[str, Any]] = []
    prior_day: date | None = None
    for index, (manifest_row, dataset_row) in enumerate(
        zip(manifest_rows, selected_dataset, strict=True)
    ):
        row = _exact_keys(
            manifest_row, {"date", *input_columns}, name=f"model_rows[{index}]"
        )
        if not isinstance(dataset_row, list) or len(dataset_row) != len(expected_dataset_columns):
            raise IssueReplayIntegrityError("canonical dataset row shape changed")
        dataset_mapping = dict(zip(expected_dataset_columns, dataset_row, strict=True))
        day = _parse_date(row["date"], name=f"model_rows[{index}].date")
        if dataset_mapping["Date"] != day.isoformat():
            raise IssueReplayIntegrityError("model row date differs from canonical dataset")
        if prior_day is not None and day != prior_day + timedelta(days=1):
            raise IssueReplayIntegrityError("model rows are not contiguous")
        if index == 6 and day != watermark:
            raise IssueReplayIntegrityError("model rows do not end at source watermark")
        normalized: dict[str, Any] = {"date": day.isoformat()}
        for column in input_columns:
            observed = _finite(row[column], name=f"model_rows[{index}].{column}")
            expected = _finite(
                dataset_mapping[column], name=f"canonical_dataset[{index}].{column}"
            )
            if observed != expected:
                raise IssueReplayIntegrityError(
                    f"model row {column} differs from canonical dataset"
                )
            normalized[column] = observed
        canonical_manifest_rows.append(normalized)
        prior_day = day
    # The shared authority has already replayed raw feed -> derived features.  This
    # separate equality check prevents the producer from substituting self-consistent
    # rows in its manifest.
    authoritative_tail = source.frame.tail(7)
    for index, row in enumerate(canonical_manifest_rows):
        authority = authoritative_tail.iloc[index]
        if str(authority["Date"]) != row["date"]:
            raise IssueReplayIntegrityError("source authority dates differ from input rows")
        for column in input_columns:
            if float(authority[column]) != row[column]:
                raise IssueReplayIntegrityError(
                    "source authority values differ from input rows"
                )

    semantic = _decode_json(source_raws["semantic_manifest"], name="source semantic manifest")
    semantic_dataset = semantic.get("canonical_dataset")
    if semantic_dataset != source_artifacts["canonical_dataset"].as_dict():
        raise IssueReplayIntegrityError("source semantic manifest dataset binding changed")
    activation_payload = _decode_json(activation_raw, name="activation source manifest")

    model_value = _exact_keys(
        manifest_record["model"],
        {"outer_manifest", "training_manifest", "checkpoints"},
        name="input.model",
    )
    outer_artifact, outer_raw = _artifact_from_reference(
        model_value["outer_manifest"],
        name="input.model.outer_manifest",
        exact_path=paths.model_manifest,
    )
    if outer_artifact.sha256 != issue_record["model_manifest_sha256"]:
        raise IssueReplayIntegrityError("input outer manifest binding changed")
    outer = _decode_json(outer_raw, name="outer model manifest")
    outer_record = _exact_keys(
        outer,
        {
            "schema_version",
            "case",
            "model_version",
            "created_at_utc",
            "training_cutoff_date",
            "stations",
            "seeds",
            "best_seed_selected",
            "target",
            "input_schema_sha256",
            "training_manifest",
            "checkpoints",
        },
        name="outer model manifest",
    )
    if (
        outer_record["schema_version"] != contract["outer_manifest_schema_version"]
        or outer_record["case"] != "ootang"
        or outer_record["stations"] != contract["station_order_live"]
        or outer_record["seeds"] != contract["seeds"]
        or outer_record["best_seed_selected"] is not False
        or outer_record["input_schema_sha256"] != _input_schema_sha256(profile)
    ):
        raise IssueReplayIntegrityError("outer model manifest identity changed")
    model_created_at = _parse_utc(
        outer_record["created_at_utc"], name="model.created_at_utc"
    )
    _parse_date(outer_record["training_cutoff_date"], name="model.training_cutoff_date")
    if outer_record["target"] != {
        "name": "next_natural_day_cumulative_displacement",
        "unit": "mm",
        "horizon": "P1D",
    }:
        raise IssueReplayIntegrityError("outer model target changed")
    training_artifact, training_raw = _artifact_from_reference(
        model_value["training_manifest"],
        name="input.model.training_manifest",
        expected_root=paths.objects,
    )
    _require_content_name(training_artifact, suffix="")
    if outer_record["training_manifest"] != training_artifact.as_dict():
        raise IssueReplayIntegrityError("outer/training artifact binding changed")
    training = _decode_json(training_raw, name="training manifest")

    input_checkpoints = model_value["checkpoints"]
    outer_checkpoints = outer_record["checkpoints"]
    if (
        not isinstance(input_checkpoints, list)
        or len(input_checkpoints) != 5
        or input_checkpoints != outer_checkpoints
    ):
        raise IssueReplayIntegrityError("checkpoint lists differ or are incomplete")
    checkpoint_artifacts: list[tuple[Artifact, bytes]] = []
    normalized_checkpoint_rows: list[dict[str, Any]] = []
    for expected_seed, row in enumerate(input_checkpoints):
        checkpoint_row = _exact_keys(
            row, {"seed", "artifact"}, name=f"input.model.checkpoints[{expected_seed}]"
        )
        if checkpoint_row["seed"] != expected_seed:
            raise IssueReplayIntegrityError("checkpoint seed order changed")
        artifact, raw = _artifact_from_reference(
            checkpoint_row["artifact"],
            name=f"checkpoint[{expected_seed}]",
            expected_root=paths.objects,
            maximum_bytes=contract["checkpoint_max_bytes"],
        )
        _require_content_name(artifact, suffix="")
        checkpoint_artifacts.append((artifact, raw))
        normalized_checkpoint_rows.append(
            {"seed": expected_seed, "artifact": artifact.as_dict()}
        )
    training_source = _validate_training_manifest(
        training,
        outer=outer_record,
        checkpoints=normalized_checkpoint_rows,
        profile=profile,
    )
    if (
        training_source["activation_source_manifest_sha256"] != activation.sha256
        or training_source["training_cutoff_date"]
        != outer_record["training_cutoff_date"]
    ):
        raise IssueReplayIntegrityError("model epoch source binding changed")
    activation_preprocessing = _activation_preprocessing(
        activation=activation,
        activation_payload=activation_payload,
        training_source=training_source,
        prerequisite_data_manifest=prerequisites.source.data_manifest,
        paths=paths,
        profile=profile,
    )
    from monitoring import ootang_live_source as source_authority

    deploy_profile = dict(profile["_deploy_payload"])
    deploy_profile["_profile_path"] = str(profile["_deploy_path"])
    deploy_profile["_profile_sha256"] = profile["_deploy_sha256"]
    deploy_profile["_project_root"] = str(profile["_project_root"])
    try:
        activation_authority = source_authority.load_activation_source(
            deploy_profile,
            runtime_root=paths.root,
            project_root=Path(str(profile["_project_root"])),
        )
        current_exported_at = _parse_utc(
            source.exported_at_utc, name="current source export"
        )
        current_record_causal_times = tuple(
            (
                _parse_utc(
                    source_record.available_at_utc,
                    name="current source record availability",
                ),
                _parse_utc(
                    source_record.finalized_at_utc,
                    name="current source record finalization",
                ),
            )
            for source_record in source.records
        )
    except IssueReplayError:
        raise
    except Exception as exc:
        raise IssueReplayIntegrityError(
            "cannot recursively verify activation/current causal source lineage"
        ) from exc
    if (
        activation_authority.activation_manifest.as_dict()
        != activation.as_dict()
        or activation_authority.semantic_manifest.as_dict()
        != activation_preprocessing.semantic_manifest.as_dict()
        or activation_authority.dataset.as_dict()
        != activation_preprocessing.canonical_dataset.as_dict()
        or activation_authority.outcome_source_id != source.outcome_source_id
    ):
        raise IssueReplayIntegrityError(
            "activation source differs from recursive shared authority"
        )
    _validate_declared_causal_time_chain(
        current_exported_at=current_exported_at,
        current_record_causal_times=current_record_causal_times,
        activation_exported_at=_parse_utc(
            activation_authority.exported_at_utc,
            name="activation feed export",
        ),
        activation_captured_at=_parse_utc(
            activation_payload["captured_at_utc"],
            name="activation capture",
        ),
        model_created_at=model_created_at,
        issue_source_as_of=source_as_of,
        issue_generated_at=generated,
        verified_at=machine_now,
        error_type=IssueReplayIntegrityError,
    )

    issue_stations = issue_record["stations"]
    expected_station_keys = {
        "station",
        "persistence_mm",
        "seed0_p50_mm",
        "seed1_p50_mm",
        "seed2_p50_mm",
        "seed3_p50_mm",
        "seed4_p50_mm",
    }
    if not isinstance(issue_stations, list) or len(issue_stations) != 8:
        raise IssueReplayIntegrityError("issue station rows are incomplete")
    normalized_issue_stations: list[dict[str, Any]] = []
    for station, row in zip(contract["station_order_live"], issue_stations, strict=True):
        station_row = _exact_keys(row, expected_station_keys, name=f"issue.{station}")
        if station_row["station"] != station:
            raise IssueReplayIntegrityError("issue station order changed")
        normalized_issue_stations.append(
            {
                "station": station,
                **{
                    key: _finite(station_row[key], name=f"issue.{station}.{key}")
                    for key in expected_station_keys - {"station"}
                },
            }
        )
    implementation = _validate_issue_implementation(
        manifest_record["implementation"], profile=profile
    )
    expected_science = _scientific_semantics(
        manifest=manifest_record,
        issue_stations=normalized_issue_stations,
        implementation=implementation,
    )
    if manifest_record["scientific_semantics_sha256"] != _canonical_sha256(expected_science):
        raise IssueReplayIntegrityError("input scientific semantics digest changed")

    _configure_inference(profile)
    checkpoints = [
        _load_checkpoint(
            artifact,
            raw,
            expected_seed=seed,
            expected_source=training_source,
            expected_preprocessing=activation_preprocessing,
            profile=profile,
        )
        for seed, (artifact, raw) in enumerate(checkpoint_artifacts)
    ]
    predictions = {
        checkpoint.seed: _predict_checkpoint(
            checkpoint, canonical_manifest_rows, profile
        )
        for checkpoint in checkpoints
    }
    latest = canonical_manifest_rows[-1]
    comparisons: list[dict[str, Any]] = []
    maximum_difference = 0.0
    for station_row in normalized_issue_stations:
        station = station_row["station"]
        persistence = latest[f"{station}_disp"]
        if station_row["persistence_mm"] != persistence:
            raise IssueReplayIntegrityError(
                f"issue persistence differs from canonical input for {station}"
            )
        for seed in contract["seeds"]:
            observed = station_row[f"seed{seed}_p50_mm"]
            replayed = predictions[seed][station]
            difference = abs(observed - replayed)
            if not math.isfinite(difference):
                raise IssueReplayIntegrityError("prediction difference is non-finite")
            maximum_difference = max(maximum_difference, difference)
            comparisons.append(
                {
                    "station": station,
                    "seed": seed,
                    "issued_p50_mm": observed,
                    "replayed_p50_mm": replayed,
                    "absolute_difference_mm": difference,
                }
            )
    if len(comparisons) != 40:
        raise IssueReplayIntegrityError("independent replay did not compare 40 P50 values")
    if maximum_difference > contract["prediction_absolute_tolerance_mm"]:
        raise IssueReplayIntegrityError(
            "independent P50 replay exceeded absolute tolerance"
        )
    replay_projection = [
        {
            "station": station,
            **{f"seed{seed}_p50_mm": predictions[seed][station] for seed in contract["seeds"]},
        }
        for station in contract["station_order_live"]
    ]
    source_receipt = {
        "source_watermark": watermark.isoformat(),
        **{key: artifact.as_dict() for key, artifact in source_artifacts.items()},
        "activation_semantic_manifest": (
            activation_preprocessing.semantic_manifest.as_dict()
        ),
        "activation_canonical_dataset": (
            activation_preprocessing.canonical_dataset.as_dict()
        ),
    }
    model_receipt = {
        "outer_manifest": outer_artifact.as_dict(),
        "training_manifest": training_artifact.as_dict(),
        "checkpoints": normalized_checkpoint_rows,
        "model_version": outer_record["model_version"],
        "input_schema_sha256": outer_record["input_schema_sha256"],
    }
    return _VerifiedInputs(
        target=target,
        issue=issue_artifact,
        input_manifest=input_artifact,
        source=source_receipt,
        model=model_receipt,
        input_rows_sha256=_canonical_sha256(canonical_manifest_rows),
        replayed_predictions_sha256=_canonical_sha256(replay_projection),
        persistence_values_verified=8,
        p50_values_verified=40,
        maximum_abs_difference_mm=maximum_difference,
        comparisons=tuple(comparisons),
    )


def _implementation_record(profile: Mapping[str, Any]) -> dict[str, Any]:
    project_root = Path(str(profile["_project_root"]))
    artifacts: dict[str, Artifact] = {}
    for key, path in {
        "replay_profile": Path(str(profile["_profile_path"])),
        "verifier": Path(__file__).resolve(),
        "shared_source_authority": Path(profile["_source_authority"]["path"]),
        "live_runner": project_root / "code/monitoring/ootang_prequential_live.py",
        "pyproject": project_root / "pyproject.toml",
        "uv_lock": project_root / "uv.lock",
    }.items():
        artifact, _ = _artifact_from_path(path, name=f"implementation.{key}")
        artifacts[key] = artifact
    if artifacts["replay_profile"].sha256 != profile["_profile_sha256"]:
        raise IssueReplayIntegrityError("replay profile changed after validation")
    if artifacts["shared_source_authority"].sha256 != profile["_source_authority"]["sha256"]:
        raise IssueReplayIntegrityError("shared source authority changed after validation")
    return {
        "schema_version": "ootang_issue_replay_implementation_v1",
        **{key: artifact.as_dict() for key, artifact in artifacts.items()},
        "runtime": _runtime_environment(),
        "prediction_implementation_independent_from_issue_producer": True,
        "source_authority_shared_with_issue_producer": True,
    }


def _ledger_pre_head(projection: object) -> dict[str, Any]:
    return {
        "epoch_id": _required_string(projection.epoch_id, name="ledger epoch id"),
        "event_count": _required_int(
            projection.ledger_event_count, name="ledger event count", minimum=1
        ),
        "sequence_id": _required_int(
            projection.ledger_terminal_sequence_id,
            name="ledger terminal sequence",
            minimum=1,
        ),
        "entry_sha256": _required_sha256(
            projection.ledger_terminal_sha256, name="ledger terminal SHA-256"
        ),
    }


def _receipt_payload(
    verified: _VerifiedInputs,
    *,
    profile: Mapping[str, Any],
    projection: object,
    verified_at: datetime,
) -> dict[str, Any]:
    contract = profile["verification"]
    return {
        "schema_version": contract["receipt_schema_version"],
        "profile_id": profile["profile_id"],
        "artifact_status": profile["artifact_status"],
        "target_date": verified.target.isoformat(),
        "verified_at_utc": _utc_text(verified_at),
        "ledger_pre_head": _ledger_pre_head(projection),
        "issue": verified.issue.as_dict(),
        "input_manifest": verified.input_manifest.as_dict(),
        "source": verified.source,
        "model": verified.model,
        "verification": {
            "mode": "runner_preseal_independent_checkpoint_input_replay_v1",
            "input_rows_sha256": verified.input_rows_sha256,
            "replayed_predictions_sha256": verified.replayed_predictions_sha256,
            "persistence_exact": True,
            "persistence_values_verified": verified.persistence_values_verified,
            "p50_values_verified": verified.p50_values_verified,
            "prediction_absolute_tolerance_mm": contract[
                "prediction_absolute_tolerance_mm"
            ],
            "prediction_relative_tolerance": 0.0,
            "maximum_abs_difference_mm": verified.maximum_abs_difference_mm,
            "comparisons": list(verified.comparisons),
            "outcome_read": False,
            "shared_source_authority_used": True,
            "independent_checkpoint_loader_used": True,
            "independent_preprocessing_used": True,
            "independent_convlstm_forward_used": True,
        },
        "implementation": _implementation_record(profile),
        "formal_warning_output": False,
        "e2_live_evidence_eligible": False,
        "real_activation_ready": False,
        "automatic_calibration_promotion": False,
    }


def _receipt_semantics(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result.pop("verified_at_utc", None)
    return result


def _receipt_path(paths: ReplayRuntimePaths, target: date) -> Path:
    # Keep the final component lexical.  Resolving it here would follow a
    # receipt symlink before `_read_regular_once` can enforce O_NOFOLLOW.
    if not paths.receipts.is_absolute():
        raise IssueReplayConfigError("receipt root must be absolute")
    path = paths.receipts / f"{target.isoformat()}.json"
    if path.parent != paths.receipts:
        raise IssueReplayConfigError("receipt path escapes receipt root")
    return path


def _validate_receipt_payload(
    payload: Mapping[str, Any],
    *,
    profile: Mapping[str, Any],
    paths: ReplayRuntimePaths,
    target: date,
    expected_issue_sha256: str | None = None,
    expected_input_manifest_sha256: str | None = None,
    expected_model_manifest_sha256: str | None = None,
    expected_ledger_pre_head: Mapping[str, Any] | None = None,
) -> None:
    record = _exact_keys(payload, RECEIPT_TOP_KEYS, name="issue replay receipt")
    if (
        record["schema_version"] != profile["verification"]["receipt_schema_version"]
        or record["profile_id"] != profile["profile_id"]
        or record["artifact_status"] != profile["artifact_status"]
        or _parse_date(record["target_date"], name="receipt.target_date") != target
    ):
        raise IssueReplayConflictError("issue replay receipt identity changed")
    verified_at = _parse_utc(
        record["verified_at_utc"], name="receipt.verified_at_utc"
    )
    target_start = datetime.combine(
        target,
        time.min,
        tzinfo=ZoneInfo(profile["_deploy_payload"]["source_feed"]["date_timezone"]),
    ).astimezone(timezone.utc)
    if verified_at >= target_start:
        raise IssueReplayConflictError("receipt was not verified before target start")
    for flag in (
        "formal_warning_output",
        "e2_live_evidence_eligible",
        "real_activation_ready",
        "automatic_calibration_promotion",
    ):
        if record[flag] is not False:
            raise IssueReplayConflictError(f"receipt claimed forbidden flag {flag}")
    pre_head = _exact_keys(
        record["ledger_pre_head"],
        {"epoch_id", "event_count", "sequence_id", "entry_sha256"},
        name="receipt.ledger_pre_head",
    )
    _required_string(pre_head["epoch_id"], name="receipt epoch")
    _required_int(pre_head["event_count"], name="receipt event count", minimum=1)
    _required_int(pre_head["sequence_id"], name="receipt sequence", minimum=1)
    _required_sha256(pre_head["entry_sha256"], name="receipt entry hash")
    if pre_head["event_count"] != pre_head["sequence_id"]:
        raise IssueReplayConflictError("receipt ledger pre-head is not contiguous")
    if expected_ledger_pre_head is not None and dict(pre_head) != dict(
        expected_ledger_pre_head
    ):
        raise IssueReplayConflictError("receipt ledger pre-head changed")
    issue_artifact, issue_raw = _artifact_from_reference(
        record["issue"],
        name="receipt.issue",
        exact_path=paths.issue_inbox / f"{target.isoformat()}.json",
    )
    input_artifact, input_raw = _artifact_from_reference(
        record["input_manifest"],
        name="receipt.input_manifest",
        expected_root=paths.objects,
    )
    _require_content_name(input_artifact, suffix=".json")
    if (
        expected_issue_sha256 is not None
        and issue_artifact.sha256 != expected_issue_sha256
    ):
        raise IssueReplayConflictError("receipt exact issue binding changed")
    if (
        expected_input_manifest_sha256 is not None
        and input_artifact.sha256 != expected_input_manifest_sha256
    ):
        raise IssueReplayConflictError("receipt input manifest binding changed")

    source_record = _exact_keys(
        record["source"],
        {
            "source_watermark",
            "canonical_dataset",
            "semantic_manifest",
            "activation_source_manifest",
            "activation_semantic_manifest",
            "activation_canonical_dataset",
        },
        name="receipt.source",
    )
    if _parse_date(source_record["source_watermark"], name="receipt watermark") != (
        target - timedelta(days=1)
    ):
        raise IssueReplayConflictError("receipt source watermark changed")
    source_artifacts: dict[str, Artifact] = {}
    source_raws: dict[str, bytes] = {}
    for key, suffix in (
        ("canonical_dataset", ".source.json"),
        ("semantic_manifest", ".source-manifest.json"),
    ):
        artifact, raw = _artifact_from_reference(
            source_record[key], name=f"receipt.source.{key}", expected_root=paths.objects
        )
        _require_content_name(artifact, suffix=suffix)
        source_artifacts[key] = artifact
        source_raws[key] = raw
    activation_artifact, activation_raw = _artifact_from_reference(
        source_record["activation_source_manifest"],
        name="receipt.source.activation_source_manifest",
        exact_path=paths.activation_manifest,
    )
    source_artifacts["activation_source_manifest"] = activation_artifact
    source_raws["activation_source_manifest"] = activation_raw
    current_semantic = _decode_json(
        source_raws["semantic_manifest"], name="receipt current semantic manifest"
    )
    current_semantic_record = _exact_keys(
        current_semantic,
        {
            "schema_version",
            "case",
            "outcome_source_id",
            "maximum_complete_finalized_date",
            "canonical_dataset",
            "lineage",
            "semantics",
            "dataset_summary",
        },
        name="receipt current semantic manifest",
    )
    if (
        current_semantic_record["schema_version"]
        != "ootang_source_semantic_manifest_v1"
        or current_semantic_record["case"] != "ootang"
        or current_semantic_record["maximum_complete_finalized_date"]
        != source_record["source_watermark"]
        or current_semantic_record["canonical_dataset"]
        != source_artifacts["canonical_dataset"].as_dict()
    ):
        raise IssueReplayConflictError("receipt current source lineage changed")
    _required_string(
        current_semantic_record["outcome_source_id"],
        name="receipt current outcome source id",
    )
    current_frame = _decode_canonical_dataset_frame(
        source_raws["canonical_dataset"],
        expected_watermark=target - timedelta(days=1),
        profile=profile,
        name="receipt current canonical dataset",
    )
    _validate_semantic_manifest_metadata(
        current_semantic_record,
        dataset=source_artifacts["canonical_dataset"],
        frame=current_frame,
        expected_watermark=target - timedelta(days=1),
        profile=profile,
        paths=paths,
        name="receipt current semantic manifest",
    )
    from monitoring import ootang_live_source as source_authority

    deploy_profile = dict(profile["_deploy_payload"])
    deploy_profile["_profile_path"] = str(profile["_deploy_path"])
    deploy_profile["_profile_sha256"] = profile["_deploy_sha256"]
    deploy_profile["_project_root"] = str(profile["_project_root"])
    feed_metadata = current_semantic_record["lineage"]["daily_feed_snapshot"]
    try:
        recursively_loaded_source = source_authority._load_semantic_source(
            deploy_profile,
            semantic=source_authority.ArtifactRef(
                path=source_artifacts["semantic_manifest"].path,
                sha256=source_artifacts["semantic_manifest"].sha256,
                size_bytes=source_artifacts["semantic_manifest"].size_bytes,
            ),
            activation=source_authority.ArtifactRef(
                path=activation_artifact.path,
                sha256=activation_artifact.sha256,
                size_bytes=activation_artifact.size_bytes,
            ),
            objects=paths.objects,
            expected_watermark=target - timedelta(days=1),
            expected_source_id=current_semantic_record["outcome_source_id"],
            expected_exported_at=feed_metadata["exported_at_utc"],
            project_root=Path(str(profile["_project_root"])),
        )
    except Exception as exc:
        raise IssueReplayConflictError(
            "receipt source lineage failed recursive shared-authority replay"
        ) from exc
    if (
        recursively_loaded_source.dataset.path
        != source_artifacts["canonical_dataset"].path
        or recursively_loaded_source.semantic_manifest.path
        != source_artifacts["semantic_manifest"].path
        or recursively_loaded_source.activation_manifest.path
        != activation_artifact.path
        or recursively_loaded_source.frame["Date"].tolist()
        != current_frame["Date"].tolist()
        or not np.array_equal(
            recursively_loaded_source.frame.iloc[:, 1:].to_numpy(dtype=np.float64),
            current_frame.iloc[:, 1:].to_numpy(dtype=np.float64),
        )
    ):
        raise IssueReplayConflictError(
            "receipt source differs from recursive shared-authority replay"
        )
    current_exported_at = _parse_utc(
        recursively_loaded_source.exported_at_utc,
        name="receipt current feed export",
    )
    current_record_causal_times = tuple(
        (
            _parse_utc(
                source_record_value.available_at_utc,
                name="receipt current record availability",
            ),
            _parse_utc(
                source_record_value.finalized_at_utc,
                name="receipt current record finalization",
            ),
        )
        for source_record_value in recursively_loaded_source.records
    )
    activation_payload = _decode_json(
        activation_raw, name="receipt activation source manifest"
    )

    model = _exact_keys(
        record["model"],
        {
            "outer_manifest",
            "training_manifest",
            "checkpoints",
            "model_version",
            "input_schema_sha256",
        },
        name="receipt.model",
    )
    outer_artifact, outer_raw = _artifact_from_reference(
        model["outer_manifest"],
        name="receipt.model.outer_manifest",
        exact_path=paths.model_manifest,
    )
    if (
        expected_model_manifest_sha256 is not None
        and outer_artifact.sha256 != expected_model_manifest_sha256
    ):
        raise IssueReplayConflictError("receipt model manifest binding changed")
    training_artifact, training_raw = _artifact_from_reference(
        model["training_manifest"],
        name="receipt.model.training_manifest",
        expected_root=paths.objects,
    )
    _require_content_name(training_artifact, suffix="")
    checkpoint_rows = model["checkpoints"]
    if not isinstance(checkpoint_rows, list) or len(checkpoint_rows) != 5:
        raise IssueReplayConflictError("receipt checkpoint list changed")
    normalized_checkpoints: list[dict[str, Any]] = []
    checkpoint_materials: list[tuple[Artifact, bytes]] = []
    for seed, value in enumerate(checkpoint_rows):
        row = _exact_keys(
            value, {"seed", "artifact"}, name=f"receipt.checkpoints[{seed}]"
        )
        if row["seed"] != seed:
            raise IssueReplayConflictError("receipt checkpoint seed order changed")
        artifact, checkpoint_raw = _artifact_from_reference(
            row["artifact"],
            name=f"receipt.checkpoint[{seed}]",
            expected_root=paths.objects,
            maximum_bytes=profile["verification"]["checkpoint_max_bytes"],
        )
        _require_content_name(artifact, suffix="")
        normalized_checkpoints.append({"seed": seed, "artifact": artifact.as_dict()})
        checkpoint_materials.append((artifact, checkpoint_raw))
    model_version = _required_string(model["model_version"], name="receipt.model_version")
    input_schema = _required_sha256(
        model["input_schema_sha256"], name="receipt.input_schema_sha256"
    )
    if input_schema != _input_schema_sha256(profile):
        raise IssueReplayConflictError("receipt input schema changed")
    outer_payload = _decode_json(outer_raw, name="receipt outer model manifest")
    outer_record = _exact_keys(
        outer_payload,
        {
            "schema_version",
            "case",
            "model_version",
            "created_at_utc",
            "training_cutoff_date",
            "stations",
            "seeds",
            "best_seed_selected",
            "target",
            "input_schema_sha256",
            "training_manifest",
            "checkpoints",
        },
        name="receipt outer model manifest",
    )
    if (
        outer_record["schema_version"]
        != profile["verification"]["outer_manifest_schema_version"]
        or outer_record["case"] != "ootang"
        or outer_record["model_version"] != model_version
        or outer_record["input_schema_sha256"] != input_schema
        or outer_record["training_manifest"] != training_artifact.as_dict()
        or outer_record["checkpoints"] != normalized_checkpoints
        or outer_record["stations"]
        != profile["verification"]["station_order_live"]
        or outer_record["seeds"] != profile["verification"]["seeds"]
        or outer_record["best_seed_selected"] is not False
        or outer_record["target"]
        != {
            "name": "next_natural_day_cumulative_displacement",
            "unit": "mm",
            "horizon": "P1D",
        }
    ):
        raise IssueReplayConflictError("receipt model lineage changed")
    model_created_at = _parse_utc(
        outer_record["created_at_utc"], name="receipt model creation"
    )
    _parse_date(outer_record["training_cutoff_date"], name="receipt model cutoff")
    training_payload = _decode_json(training_raw, name="receipt training manifest")
    training_source = _validate_training_manifest(
        training_payload,
        outer=outer_record,
        checkpoints=normalized_checkpoints,
        profile=profile,
    )
    activation_semantic_reference = _exact_keys(
        source_record["activation_semantic_manifest"],
        {"path", "sha256", "size_bytes"},
        name="receipt.source.activation_semantic_manifest",
    )
    expected_activation_semantic = Artifact(
        Path(
            _required_string(
                activation_semantic_reference["path"],
                name="receipt activation semantic path",
            )
        ),
        _required_sha256(
            activation_semantic_reference["sha256"],
            name="receipt activation semantic hash",
        ),
        _required_int(
            activation_semantic_reference["size_bytes"],
            name="receipt activation semantic size",
        ),
    )
    activation_preprocessing = _activation_preprocessing(
        activation=activation_artifact,
        activation_payload=activation_payload,
        training_source=training_source,
        prerequisite_data_manifest=expected_activation_semantic,
        paths=paths,
        profile=profile,
    )
    if (
        activation_preprocessing.semantic_manifest.as_dict()
        != expected_activation_semantic.as_dict()
        or activation_preprocessing.canonical_dataset.as_dict()
        != source_record["activation_canonical_dataset"]
    ):
        raise IssueReplayConflictError("receipt activation lineage changed")
    activation_dataset_artifact, activation_dataset_raw = _artifact_from_reference(
        source_record["activation_canonical_dataset"],
        name="receipt.source.activation_canonical_dataset",
        expected_root=paths.objects,
    )
    _require_content_name(activation_dataset_artifact, suffix=".source.json")
    activation_watermark = _parse_date(
        activation_payload["maximum_complete_finalized_date"],
        name="receipt activation watermark",
    )
    activation_frame = _decode_canonical_dataset_frame(
        activation_dataset_raw,
        expected_watermark=activation_watermark,
        profile=profile,
        name="receipt activation canonical dataset",
    )
    try:
        recursively_loaded_activation = source_authority.load_activation_source(
            deploy_profile,
            runtime_root=paths.root,
            project_root=Path(str(profile["_project_root"])),
        )
    except Exception as exc:
        raise IssueReplayConflictError(
            "receipt activation lineage failed recursive shared-authority replay"
        ) from exc
    if (
        recursively_loaded_activation.activation_manifest.as_dict()
        != activation_artifact.as_dict()
        or recursively_loaded_activation.semantic_manifest.as_dict()
        != expected_activation_semantic.as_dict()
        or recursively_loaded_activation.dataset.as_dict()
        != activation_dataset_artifact.as_dict()
        or recursively_loaded_activation.watermark != activation_watermark
        or recursively_loaded_activation.outcome_source_id
        != current_semantic_record["outcome_source_id"]
        or recursively_loaded_activation.frame["Date"].tolist()
        != activation_frame["Date"].tolist()
        or not np.array_equal(
            recursively_loaded_activation.frame.iloc[:, 1:].to_numpy(
                dtype=np.float64
            ),
            activation_frame.iloc[:, 1:].to_numpy(dtype=np.float64),
        )
    ):
        raise IssueReplayConflictError(
            "receipt activation differs from recursive shared-authority replay"
        )
    activation_exported_at = _parse_utc(
        recursively_loaded_activation.exported_at_utc,
        name="receipt activation feed export",
    )
    activation_captured_at = _parse_utc(
        activation_payload["captured_at_utc"],
        name="receipt activation capture",
    )
    verified_checkpoints = [
        _load_checkpoint(
            artifact,
            raw,
            expected_seed=seed,
            expected_source=training_source,
            expected_preprocessing=activation_preprocessing,
            profile=profile,
        )
        for seed, (artifact, raw) in enumerate(checkpoint_materials)
    ]

    issue_payload = _decode_json(issue_raw, name="receipt-bound issue")
    issue_record = _exact_keys(
        issue_payload,
        {
            "schema_version",
            "target_date",
            "generated_at_utc",
            "source_as_of_at_utc",
            "source_snapshot_sha256",
            "model_manifest_sha256",
            "input_manifest",
            "stations",
        },
        name="receipt-bound issue",
    )
    if (
        issue_record["schema_version"]
        != profile["verification"]["issue_schema_version"]
        or issue_record["target_date"] != target.isoformat()
        or issue_record["input_manifest"] != input_artifact.as_dict()
        or issue_record["source_snapshot_sha256"] != activation_artifact.sha256
        or issue_record["model_manifest_sha256"] != outer_artifact.sha256
    ):
        raise IssueReplayConflictError("receipt issue semantics changed")
    generated_at = _parse_utc(
        issue_record["generated_at_utc"], name="receipt issue generated"
    )
    source_as_of = _parse_utc(
        issue_record["source_as_of_at_utc"], name="receipt issue source as-of"
    )
    _validate_declared_causal_time_chain(
        current_exported_at=current_exported_at,
        current_record_causal_times=current_record_causal_times,
        activation_exported_at=activation_exported_at,
        activation_captured_at=activation_captured_at,
        model_created_at=model_created_at,
        issue_source_as_of=source_as_of,
        issue_generated_at=generated_at,
        verified_at=verified_at,
        error_type=IssueReplayConflictError,
    )
    input_payload = _decode_json(input_raw, name="receipt-bound input manifest")
    input_record = _exact_keys(
        input_payload,
        {
            "schema_version",
            "case",
            "target_date",
            "source_watermark",
            "scientific_semantics_sha256",
            "source",
            "model",
            "implementation",
            "model_rows",
            "station_order_model",
            "station_order_live",
            "expert_order",
        },
        name="receipt-bound input manifest",
    )
    expected_input_source = {
        key: source_artifacts[key].as_dict()
        for key in (
            "canonical_dataset",
            "semantic_manifest",
            "activation_source_manifest",
        )
    }
    expected_input_model = {
        "outer_manifest": outer_artifact.as_dict(),
        "training_manifest": training_artifact.as_dict(),
        "checkpoints": normalized_checkpoints,
    }
    if (
        input_record["schema_version"]
        != profile["verification"]["input_manifest_schema_version"]
        or input_record["case"] != "ootang"
        or input_record["target_date"] != target.isoformat()
        or input_record["source_watermark"] != source_record["source_watermark"]
        or input_record["source"] != expected_input_source
        or input_record["model"] != expected_input_model
        or input_record["station_order_model"]
        != profile["verification"]["station_order_model"]
        or input_record["station_order_live"]
        != profile["verification"]["station_order_live"]
        or input_record["expert_order"]
        != [
            "persistence",
            "seed0_p50",
            "seed1_p50",
            "seed2_p50",
            "seed3_p50",
            "seed4_p50",
        ]
    ):
        raise IssueReplayConflictError("receipt input semantics changed")
    input_rows = input_record["model_rows"]
    if not isinstance(input_rows, list) or len(input_rows) != 7:
        raise IssueReplayConflictError("receipt input row count changed")
    normalized_rows: list[dict[str, Any]] = []
    input_columns = [
        *profile["verification"]["displacement_columns"],
        *profile["verification"]["exogenous_columns"],
    ]
    prior_input_day: date | None = None
    for index, value in enumerate(input_rows):
        row = _exact_keys(
            value, {"date", *input_columns}, name=f"receipt.model_rows[{index}]"
        )
        day = _parse_date(row["date"], name=f"receipt.model_rows[{index}].date")
        if prior_input_day is not None and day != prior_input_day + timedelta(days=1):
            raise IssueReplayConflictError("receipt input rows are not contiguous")
        normalized_rows.append(
            {
                "date": day.isoformat(),
                **{
                    column: _finite(
                        row[column], name=f"receipt.model_rows[{index}].{column}"
                    )
                    for column in input_columns
                },
            }
        )
        prior_input_day = day
    if prior_input_day != target - timedelta(days=1) or len(current_frame) < 7:
        raise IssueReplayConflictError("receipt input rows do not end at source watermark")
    current_tail = current_frame.tail(7)
    for index, row in enumerate(normalized_rows):
        authority = current_tail.iloc[index]
        if str(authority["Date"]) != row["date"]:
            raise IssueReplayConflictError("receipt input dates differ from source tail")
        for column in input_columns:
            if float(authority[column]) != row[column]:
                raise IssueReplayConflictError(
                    "receipt input values differ from source tail"
                )
    _configure_inference(profile)
    forward_predictions = {
        checkpoint.seed: _predict_checkpoint(
            checkpoint, normalized_rows, profile
        )
        for checkpoint in verified_checkpoints
    }

    verification = _exact_keys(
        record["verification"],
        {
            "mode",
            "input_rows_sha256",
            "replayed_predictions_sha256",
            "persistence_exact",
            "persistence_values_verified",
            "p50_values_verified",
            "prediction_absolute_tolerance_mm",
            "prediction_relative_tolerance",
            "maximum_abs_difference_mm",
            "comparisons",
            "outcome_read",
            "shared_source_authority_used",
            "independent_checkpoint_loader_used",
            "independent_preprocessing_used",
            "independent_convlstm_forward_used",
        },
        name="receipt.verification",
    )
    if (
        verification["mode"]
        != "runner_preseal_independent_checkpoint_input_replay_v1"
        or _required_sha256(
            verification["input_rows_sha256"], name="receipt input rows hash"
        )
        != _canonical_sha256(normalized_rows)
        or not isinstance(verification["replayed_predictions_sha256"], str)
        or verification.get("outcome_read") is not False
        or verification.get("persistence_exact") is not True
        or verification.get("persistence_values_verified") != 8
        or verification.get("p50_values_verified") != 40
        or verification.get("prediction_relative_tolerance") != 0.0
        or verification.get("prediction_absolute_tolerance_mm")
        != profile["verification"]["prediction_absolute_tolerance_mm"]
        or verification.get("shared_source_authority_used") is not True
        or verification.get("independent_checkpoint_loader_used") is not True
        or verification.get("independent_preprocessing_used") is not True
        or verification.get("independent_convlstm_forward_used") is not True
    ):
        raise IssueReplayConflictError("receipt verification boundary changed")
    comparisons = verification.get("comparisons")
    if not isinstance(comparisons, list) or len(comparisons) != 40:
        raise IssueReplayConflictError("receipt comparison count changed")
    issue_station_rows = issue_record["stations"]
    if not isinstance(issue_station_rows, list) or len(issue_station_rows) != 8:
        raise IssueReplayConflictError("receipt issue station rows changed")
    issue_by_station: dict[str, dict[str, Any]] = {}
    for expected_station, value in zip(
        profile["verification"]["station_order_live"],
        issue_station_rows,
        strict=True,
    ):
        row = _exact_keys(
            value,
            {
                "station",
                "persistence_mm",
                "seed0_p50_mm",
                "seed1_p50_mm",
                "seed2_p50_mm",
                "seed3_p50_mm",
                "seed4_p50_mm",
            },
            name=f"receipt.issue.{expected_station}",
        )
        if row["station"] != expected_station:
            raise IssueReplayConflictError("receipt issue station order changed")
        issue_by_station[expected_station] = row
        model_index = profile["verification"]["station_order_model"].index(
            expected_station
        )
        displacement_column = profile["verification"]["displacement_columns"][
            model_index
        ]
        if _finite(row["persistence_mm"], name="receipt persistence") != normalized_rows[
            -1
        ][displacement_column]:
            raise IssueReplayConflictError("receipt persistence equality changed")
    normalized_comparisons: list[dict[str, Any]] = []
    replay_projection: list[dict[str, Any]] = []
    differences: list[float] = []
    comparison_index = 0
    for station in profile["verification"]["station_order_live"]:
        projection_row: dict[str, Any] = {"station": station}
        for seed in profile["verification"]["seeds"]:
            value = _exact_keys(
                comparisons[comparison_index],
                {
                    "station",
                    "seed",
                    "issued_p50_mm",
                    "replayed_p50_mm",
                    "absolute_difference_mm",
                },
                name=f"receipt.comparisons[{comparison_index}]",
            )
            if value["station"] != station or value["seed"] != seed:
                raise IssueReplayConflictError("receipt comparison order changed")
            issued = _finite(value["issued_p50_mm"], name="receipt issued P50")
            recorded_replay = _finite(
                value["replayed_p50_mm"], name="receipt replayed P50"
            )
            recorded_difference = _finite(
                value["absolute_difference_mm"], name="receipt P50 difference"
            )
            replayed = forward_predictions[seed][station]
            difference = abs(issued - replayed)
            if (
                issued != _finite(
                    issue_by_station[station][f"seed{seed}_p50_mm"],
                    name="receipt issue P50",
                )
                or recorded_replay != replayed
                or recorded_difference != difference
                or difference
                > profile["verification"]["prediction_absolute_tolerance_mm"]
            ):
                raise IssueReplayConflictError(
                    "receipt comparison differs from independent forward replay"
                )
            normalized_comparisons.append(
                {
                    "station": station,
                    "seed": seed,
                    "issued_p50_mm": issued,
                    "replayed_p50_mm": replayed,
                    "absolute_difference_mm": difference,
                }
            )
            projection_row[f"seed{seed}_p50_mm"] = replayed
            differences.append(difference)
            comparison_index += 1
        replay_projection.append(projection_row)
    maximum = _finite(
        verification.get("maximum_abs_difference_mm"), name="receipt max difference"
    )
    if maximum != max(differences) or verification[
        "replayed_predictions_sha256"
    ] != _canonical_sha256(replay_projection):
        raise IssueReplayConflictError("receipt replay digest or maximum changed")
    implementation = _exact_keys(
        record["implementation"],
        {
            "schema_version",
            "replay_profile",
            "verifier",
            "shared_source_authority",
            "live_runner",
            "pyproject",
            "uv_lock",
            "runtime",
            "prediction_implementation_independent_from_issue_producer",
            "source_authority_shared_with_issue_producer",
        },
        name="receipt.implementation",
    )
    if (
        implementation["schema_version"]
        != "ootang_issue_replay_implementation_v1"
        or implementation[
            "prediction_implementation_independent_from_issue_producer"
        ]
        is not True
        or implementation["source_authority_shared_with_issue_producer"] is not True
        or implementation["runtime"] != _runtime_environment()
    ):
        raise IssueReplayConflictError("receipt independence declaration changed")
    project_root = Path(str(profile["_project_root"]))
    implementation_paths = {
        "replay_profile": Path(str(profile["_profile_path"])),
        "verifier": Path(__file__).resolve(),
        "shared_source_authority": Path(profile["_source_authority"]["path"]),
        "live_runner": project_root / "code/monitoring/ootang_prequential_live.py",
        "pyproject": project_root / "pyproject.toml",
        "uv_lock": project_root / "uv.lock",
    }
    for key, exact_path in implementation_paths.items():
        _artifact_from_reference(
            implementation[key],
            name=f"receipt.implementation.{key}",
            exact_path=exact_path,
        )

    issue_implementation = _validate_issue_implementation(
        input_record["implementation"], profile=profile
    )
    expected_science = _scientific_semantics(
        manifest=input_record,
        issue_stations=[issue_by_station[station] for station in profile["verification"]["station_order_live"]],
        implementation=issue_implementation,
    )
    if input_record["scientific_semantics_sha256"] != _canonical_sha256(
        expected_science
    ):
        raise IssueReplayConflictError("receipt-bound scientific semantics changed")


def load_verified_replay_receipt(
    target_date: date,
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    runtime_root: Path | None = None,
    project_root: Path = ROOT,
    expected_issue_sha256: str | None = None,
    expected_input_manifest_sha256: str | None = None,
    expected_model_manifest_sha256: str | None = None,
    expected_ledger_pre_head: Mapping[str, Any] | None = None,
) -> VerifiedReplayReceipt:
    """Load one immutable replay receipt and check caller-supplied seal bindings."""

    if not isinstance(target_date, date) or isinstance(target_date, datetime):
        raise IssueReplayIntegrityError("target_date must be a date")
    profile = load_replay_profile(config_path, project_root=project_root)
    paths = runtime_paths(profile, runtime_root)
    path = _receipt_path(paths, target_date)
    artifact, raw = _artifact_from_path(path, name="issue replay receipt")
    payload = _decode_json(raw, name="issue replay receipt")
    if raw != _canonical_bytes(payload, newline=True):
        raise IssueReplayConflictError("issue replay receipt bytes are not canonical")
    _validate_receipt_payload(
        payload,
        profile=profile,
        paths=paths,
        target=target_date,
        expected_issue_sha256=expected_issue_sha256,
        expected_input_manifest_sha256=expected_input_manifest_sha256,
        expected_model_manifest_sha256=expected_model_manifest_sha256,
        expected_ledger_pre_head=expected_ledger_pre_head,
    )
    return VerifiedReplayReceipt(
        path=artifact.path,
        sha256=artifact.sha256,
        size_bytes=artifact.size_bytes,
        target_date=target_date,
        payload=payload,
    )


def _status_payload(
    profile: Mapping[str, Any],
    paths: ReplayRuntimePaths,
    *,
    now: datetime,
    status: str,
    reason: str,
    target: date | None,
    receipt: Artifact | None,
) -> dict[str, Any]:
    return {
        "schema_version": profile["verification"]["status_schema_version"],
        "profile_id": profile["profile_id"],
        "artifact_status": profile["artifact_status"],
        "profile_sha256": profile["_profile_sha256"],
        "runtime_root": str(paths.root),
        "recorded_at_utc": _utc_text(now),
        "replay_status": status,
        "reason": reason,
        "target_date": target.isoformat() if target is not None else None,
        "receipt": receipt.as_dict() if receipt is not None else None,
        "runner_independent_checkpoint_inference_replayed": status
        in {"verified", "already_verified_idempotent"},
        "input_manifest_semantics_verified": status
        in {"verified", "already_verified_idempotent"},
        "outcome_read": False,
        "formal_warning_output": False,
        "e2_live_evidence_eligible": False,
        "real_activation_ready": False,
        "automatic_calibration_promotion": False,
    }


def _write_status(
    profile: Mapping[str, Any],
    paths: ReplayRuntimePaths,
    *,
    now: datetime,
    status: str,
    reason: str,
    target: date | None = None,
    receipt: Artifact | None = None,
) -> Path:
    payload = _status_payload(
        profile,
        paths,
        now=now,
        status=status,
        reason=reason,
        target=target,
        receipt=receipt,
    )
    _atomic_replace(paths.status, _canonical_bytes(payload, newline=True))
    return paths.status


def _run_locked(
    *,
    profile: Mapping[str, Any],
    paths: ReplayRuntimePaths,
    clock: Clock,
    project_root: Path,
    source_loader: Callable[..., object] | None,
    projection_loader: Callable[..., object] | None,
) -> ReplayResult:
    from monitoring import ootang_live_source as source_module
    from monitoring import ootang_prequential_live as live_module

    now = clock()
    _utc_text(now)
    if not paths.activation_manifest.is_file() or not paths.model_manifest.is_file():
        status_path = _write_status(
            profile,
            paths,
            now=now,
            status="waiting_for_source_model_or_issue",
            reason="activation_source_or_model_manifest_missing",
        )
        return ReplayResult(status_path, "waiting_for_source_model_or_issue", None, None)
    live_profile = live_module.load_config(Path(str(profile["_live_path"])))
    live_paths = live_module.runtime_paths(live_profile, runtime_root=paths.root)
    prerequisites = live_module.load_prerequisites(live_profile, live_paths)
    if prerequisites is None or not paths.ledger.is_file():
        status_path = _write_status(
            profile,
            paths,
            now=now,
            status="waiting_for_source_model_or_issue",
            reason="live_prerequisites_or_ledger_missing",
        )
        return ReplayResult(status_path, "waiting_for_source_model_or_issue", None, None)
    load_projection = projection_loader or live_module.load_verified_ledger_projection
    projection = load_projection(live_profile, live_paths, prerequisites)
    if projection.outstanding_target_date is not None:
        status_path = _write_status(
            profile,
            paths,
            now=now,
            status="waiting_for_source_model_or_issue",
            reason="live_ledger_already_has_an_outstanding_sealed_issue",
            target=projection.outstanding_target_date,
        )
        return ReplayResult(
            status_path,
            "waiting_for_source_model_or_issue",
            projection.outstanding_target_date,
            None,
        )
    target = projection.last_finalized_date + timedelta(days=1)
    issue_path = paths.issue_inbox / f"{target.isoformat()}.json"
    if not issue_path.is_file():
        status_path = _write_status(
            profile,
            paths,
            now=now,
            status="waiting_for_source_model_or_issue",
            reason="next_unsealed_issue_is_missing",
            target=target,
        )
        return ReplayResult(
            status_path, "waiting_for_source_model_or_issue", target, None
        )
    deploy_profile = dict(profile["_deploy_payload"])
    deploy_profile["_profile_path"] = str(profile["_deploy_path"])
    deploy_profile["_profile_sha256"] = profile["_deploy_sha256"]
    deploy_profile["_project_root"] = str(project_root.resolve())
    load_source = source_loader or source_module.load_current_source
    source = load_source(
        deploy_profile, runtime_root=paths.root, project_root=project_root
    )
    verified = _verify_materials(
        profile=profile,
        paths=paths,
        source=source,
        projection=projection,
        prerequisites=prerequisites,
        issue_path=issue_path,
        now=now,
    )
    verified_at = _verified_completion_time(
        started_at=now,
        completed_at=clock(),
        target=target,
        timezone_name=profile["_deploy_payload"]["source_feed"]["date_timezone"],
    )
    payload = _receipt_payload(
        verified, profile=profile, projection=projection, verified_at=verified_at
    )
    receipt_path = _receipt_path(paths, target)
    receipt_raw = _canonical_bytes(payload, newline=True)
    created = _atomic_create(receipt_path, receipt_raw)
    if created:
        _post_create_time_fence(
            receipt_path=receipt_path,
            receipt_raw=receipt_raw,
            verified_at=verified_at,
            target=target,
            timezone_name=profile["_deploy_payload"]["source_feed"][
                "date_timezone"
            ],
            clock=clock,
        )
    if not created:
        existing_artifact, existing_raw = _artifact_from_path(
            receipt_path, name="existing issue replay receipt"
        )
        existing = _decode_json(existing_raw, name="existing issue replay receipt")
        if existing_raw != _canonical_bytes(existing, newline=True):
            raise IssueReplayConflictError(
                "existing issue replay receipt bytes are not canonical"
            )
        _validate_receipt_payload(
            existing,
            profile=profile,
            paths=paths,
            target=target,
            expected_issue_sha256=verified.issue.sha256,
            expected_input_manifest_sha256=verified.input_manifest.sha256,
            expected_model_manifest_sha256=verified.model["outer_manifest"]["sha256"],
            expected_ledger_pre_head=_ledger_pre_head(projection),
        )
        if _receipt_semantics(existing) != _receipt_semantics(payload):
            raise IssueReplayConflictError(
                "existing replay receipt has different verified semantics"
            )
        receipt_artifact = existing_artifact
        status = "already_verified_idempotent"
        reason = "same_target_same_replay_preserved_first_receipt_bytes"
    else:
        receipt_artifact, durable_raw = _artifact_from_path(
            receipt_path, name="durable issue replay receipt"
        )
        if durable_raw != receipt_raw:
            raise IssueReplayConflictError("durable replay receipt bytes changed")
        status = "verified"
        reason = "independent_checkpoint_input_replay_receipt_created"
    status_path = _write_status(
        profile,
        paths,
        now=clock(),
        status=status,
        reason=reason,
        target=target,
        receipt=receipt_artifact,
    )
    return ReplayResult(status_path, status, target, receipt_path)


def verify_pending_issue_under_runner_lock(
    *,
    runner_lock_handle: BinaryIO,
    config_path: Path = DEFAULT_CONFIG_PATH,
    runtime_root: Path | None = None,
    project_root: Path = ROOT,
    clock: Clock | None = None,
    _source_loader: Callable[..., object] | None = None,
    _projection_loader: Callable[..., object] | None = None,
) -> ReplayResult:
    """Verify one pending issue while the caller retains the live runner lock."""

    if (
        (_source_loader is not None or _projection_loader is not None)
        and os.environ.get(TEST_OVERRIDE_ENV) != "1"
    ):
        raise IssueReplayConfigError("private loader injection is test-only")
    profile = load_replay_profile(config_path, project_root=project_root)
    paths = runtime_paths(profile, runtime_root)
    _validate_runner_handle(runner_lock_handle, paths.runner_lock)
    replay_lock = _acquire_lock(paths.replay_lock, name="issue replay lock")
    observed_clock = clock or (lambda: datetime.now(timezone.utc))
    try:
        return _run_locked(
            profile=profile,
            paths=paths,
            clock=observed_clock,
            project_root=project_root.resolve(),
            source_loader=_source_loader,
            projection_loader=_projection_loader,
        )
    except IssueReplayBusyError:
        raise
    except IssueReplayError as exc:
        try:
            _write_status(
                profile,
                paths,
                now=observed_clock(),
                status="blocked_integrity",
                reason=f"{type(exc).__name__}:{exc}",
            )
        except Exception:
            pass
        raise
    except Exception as exc:
        normalized = IssueReplayIntegrityError(
            f"independent replay failed: {type(exc).__name__}:{exc}"
        )
        try:
            _write_status(
                profile,
                paths,
                now=observed_clock(),
                status="blocked_integrity",
                reason=f"{type(normalized).__name__}:{normalized}",
            )
        except Exception:
            pass
        raise normalized from exc
    finally:
        _release_lock(replay_lock)


def verify_pending_issue(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    runtime_root: Path | None = None,
    project_root: Path = ROOT,
    clock: Clock | None = None,
    _source_loader: Callable[..., object] | None = None,
    _projection_loader: Callable[..., object] | None = None,
) -> ReplayResult:
    """Acquire runner -> replay locks and independently verify one pending issue."""

    profile = load_replay_profile(config_path, project_root=project_root)
    paths = runtime_paths(profile, runtime_root)
    runner_lock = _acquire_lock(paths.runner_lock, name="live runner lock")
    try:
        return verify_pending_issue_under_runner_lock(
            runner_lock_handle=runner_lock,
            config_path=config_path,
            runtime_root=runtime_root,
            project_root=project_root,
            clock=clock,
            _source_loader=_source_loader,
            _projection_loader=_projection_loader,
        )
    finally:
        _release_lock(runner_lock)


def _receipt_progress_record(receipt: VerifiedReplayReceipt) -> dict[str, Any]:
    payload = receipt.payload
    progress_semantics = _receipt_semantics(payload)
    progress_semantics["ledger_pre_head"] = {
        "epoch_id": payload["ledger_pre_head"]["epoch_id"]
    }

    def storage_independent(value: object) -> object:
        if isinstance(value, Mapping):
            materialized = dict(value)
            if set(materialized) == {"path", "sha256", "size_bytes"}:
                return {
                    "sha256": materialized["sha256"],
                    "size_bytes": materialized["size_bytes"],
                }
            return {
                key: storage_independent(child)
                for key, child in materialized.items()
            }
        if isinstance(value, list):
            return [storage_independent(child) for child in value]
        if isinstance(value, tuple):
            return [storage_independent(child) for child in value]
        return value

    return {
        "target_date": receipt.target_date.isoformat(),
        "receipt_semantics_sha256": _canonical_sha256(
            storage_independent(progress_semantics)
        ),
        "issue_sha256": payload["issue"]["sha256"],
        "input_manifest_sha256": payload["input_manifest"]["sha256"],
        "model_manifest_sha256": payload["model"]["outer_manifest"]["sha256"],
        "ledger_epoch_id": payload["ledger_pre_head"]["epoch_id"],
        "input_rows_sha256": payload["verification"]["input_rows_sha256"],
        "replayed_predictions_sha256": payload["verification"][
            "replayed_predictions_sha256"
        ],
    }


def replay_progress_payload(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    runtime_root: Path | None = None,
    project_root: Path = ROOT,
) -> Mapping[str, object]:
    """Return deterministic verified-receipt state, excluding clocks/status bytes."""

    profile = load_replay_profile(config_path, project_root=project_root)
    paths = runtime_paths(profile, runtime_root)
    runner_lock = _acquire_lock(paths.runner_lock, name="live runner lock")
    try:
        replay_lock = _acquire_lock(paths.replay_lock, name="issue replay lock")
        try:
            records: list[dict[str, Any]] = []
            if paths.receipts.exists():
                if not paths.receipts.is_dir():
                    raise IssueReplayIntegrityError("replay receipt root is not a directory")
                for path in sorted(paths.receipts.iterdir(), key=lambda value: value.name):
                    if path.name.startswith("."):
                        raise IssueReplayIntegrityError(
                            "unknown hidden file in replay receipt root"
                        )
                    if path.suffix != ".json":
                        raise IssueReplayIntegrityError("unknown file in replay receipt root")
                    target = _parse_date(path.stem, name="replay receipt filename")
                    artifact, raw = _artifact_from_path(path, name="issue replay receipt")
                    payload = _decode_json(raw, name="issue replay receipt")
                    if raw != _canonical_bytes(payload, newline=True):
                        raise IssueReplayConflictError(
                            "issue replay receipt bytes are not canonical"
                        )
                    _validate_receipt_payload(
                        payload, profile=profile, paths=paths, target=target
                    )
                    records.append(
                        _receipt_progress_record(
                            VerifiedReplayReceipt(
                                artifact.path,
                                artifact.sha256,
                                artifact.size_bytes,
                                target,
                                payload,
                            )
                        )
                    )
            return {
                "schema_version": profile["verification"]["progress_schema_version"],
                "profile_sha256": profile["_profile_sha256"],
                "verified_receipt_count": len(records),
                "verified_receipts": records,
                "formal_warning_output": False,
                "e2_live_evidence_eligible": False,
                "real_activation_ready": False,
            }
        finally:
            _release_lock(replay_lock)
    finally:
        _release_lock(runner_lock)


# Compatibility spelling used by token builders that expect a *_token_payload API.
replay_progress_token_payload = replay_progress_payload


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--runtime-root", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = verify_pending_issue(
            config_path=args.config, runtime_root=args.runtime_root
        )
    except IssueReplayBusyError as exc:
        print(f"busy: {exc}", file=sys.stderr)
        return 3
    except IssueReplayError as exc:
        print(f"blocked: {exc}", file=sys.stderr)
        return 2
    print(result.status_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
