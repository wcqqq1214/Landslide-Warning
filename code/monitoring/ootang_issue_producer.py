"""Create future-only E2-B issue batches from verified source and model artifacts.

The producer is deliberately unable to consume an outcome inbox.  It derives
one target from the current finalized source watermark, replays every frozen
seed checkpoint, and publishes the issue batch with an atomic no-replace
operation while a machine lock is held.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import sys
import tempfile
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

DEFAULT_CONFIG_PATH = ROOT / "config" / "ootang_prequential_deploy.v1.json"
HEX_DIGITS = frozenset("0123456789abcdef")
ISSUE_RECEIPT_SCHEMA_VERSION = "ootang_issue_producer_receipt_v1"
ISSUE_IMPLEMENTATION_SCHEMA_VERSION = "ootang_issue_producer_implementation_v1"


class IssueProducerError(RuntimeError):
    """Base class for fail-closed producer errors."""


class IssueProducerConfigError(IssueProducerError):
    """The deployment profile is invalid or has changed semantics."""


class IssueProducerInputError(IssueProducerError):
    """A present source/model artifact violates its declared contract."""


class IssueProducerConflict(IssueProducerError):
    """A target already exists with different semantic content."""


class IssueProducerBusy(IssueProducerError):
    """Another deployment cycle currently owns the machine lock."""


class _NoIssuableTarget(IssueProducerError):
    """The verified watermark does not yet permit a future issue."""


class _MachineWaiting(IssueProducerError):
    """Safe automatic progress requires another machine stage first."""


@dataclass(frozen=True)
class Artifact:
    path: Path
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class ProducerResult:
    status_path: Path
    status: str
    target_date: date | None
    issue_path: Path | None


Clock = Callable[[], datetime]
BundleLoader = Callable[[dict[str, Any], Path], object]
TEST_EPOCH_ENV = "OOTANG_E2B_ALLOW_TEST_EPOCH_OVERRIDE"


class _ObservedClock:
    """Keep the most recent real clock observation available to error status."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self.last: datetime | None = None

    def sample(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise IssueProducerInputError("Machine clock must be timezone-aware")
        if value.utcoffset() is None:
            raise IssueProducerInputError("Machine clock has an invalid timezone")
        self.last = value
        return value


@dataclass(frozen=True)
class _TargetAuthority:
    target_date: date
    latest_displacement_mm: Mapping[str, float] | None


def _reject_constant(value: str) -> None:
    raise IssueProducerInputError(f"Forbidden JSON constant: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise IssueProducerInputError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _decode_json(raw: bytes, *, name: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IssueProducerInputError(f"{name} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise IssueProducerInputError(f"{name} must be a JSON object")
    return value


def _read_json(path: Path, *, name: str) -> tuple[dict[str, Any], bytes]:
    try:
        with path.open("rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise IssueProducerInputError(f"Cannot read {name}: {path}") from exc
    return _decode_json(raw, name=name), raw


def _canonical_bytes(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise IssueProducerInputError("Payload is not finite JSON") from exc


def _canonical_digest(value: Any) -> str:
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise IssueProducerInputError("Payload is not canonical finite JSON") from exc
    return hashlib.sha256(serialized).hexdigest()


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1_048_576), b""):
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        raise IssueProducerInputError(f"Cannot hash artifact: {path}") from exc
    return digest.hexdigest(), size


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise IssueProducerInputError("Machine clock must be timezone-aware")
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _parse_utc(value: object, *, name: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise IssueProducerInputError(f"{name} must be RFC 3339 UTC ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise IssueProducerInputError(f"{name} is not a valid UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise IssueProducerInputError(f"{name} must be UTC")
    return parsed


def _finite(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IssueProducerInputError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise IssueProducerInputError(f"{name} must be finite")
    return result


def _resolve(value: str | Path, *, project_root: Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (project_root / path).resolve()


def _atomic_write_bytes(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    _atomic_write_bytes(path, _canonical_bytes(dict(payload)))


def _atomic_create_bytes(path: Path, raw: bytes) -> bool:
    """Atomically create without ever replacing a concurrently created path."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_path, path)
            created = True
        except FileExistsError:
            created = False
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return created
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _acquire_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise IssueProducerBusy("Another deployment cycle is running") from exc
    return handle


def _validate_fixed_mapping(
    value: object, expected: Mapping[str, Any], *, name: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise IssueProducerConfigError(f"{name} must be an object")
    if set(value) != set(expected):
        raise IssueProducerConfigError(f"{name} keys changed")
    changed = sorted(
        key for key, expected_value in expected.items() if value[key] != expected_value
    )
    if changed:
        raise IssueProducerConfigError(
            f"{name} scientific values changed: " + ",".join(changed)
        )
    return value


def _validate_profile(profile: dict[str, Any]) -> None:
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
        "live_profile",
        "runtime",
        "historical_base",
        "station_geometry",
        "source_feed",
        "model",
        "issue",
        "engineering_capabilities",
    }
    if set(profile) != expected_top:
        raise IssueProducerConfigError("Deployment profile keys changed")
    if profile.get("schema_version") != "ootang_prequential_deploy_profile_v1":
        raise IssueProducerConfigError("Deployment profile schema changed")
    if profile.get("profile_id") != "ootang-prequential-deploy-v1":
        raise IssueProducerConfigError("Deployment profile id changed")
    if profile.get("profile_version") != "1.0.0-engineering":
        raise IssueProducerConfigError("Deployment profile version changed")
    if profile.get("case") != "ootang":
        raise IssueProducerConfigError("Deployment profile must be Ootang-only")
    if profile.get("artifact_status") != "e2b_engineering_only_not_live_evidence":
        raise IssueProducerConfigError("Deployment artifact status changed")
    for flag in (
        "formal_warning_output",
        "independent_label_used",
        "confirmatory_external_validation",
        "vajont_used",
        "default_pipeline_member",
    ):
        if profile.get(flag) is not False:
            raise IssueProducerConfigError(f"Deployment flag {flag} must remain false")
    expected_capabilities = {
        "source_manifest_semantic_validator_implemented": True,
        "safe_checkpoint_loader_implemented": True,
        "producer_checkpoint_inference_replay_implemented": True,
        "runner_independent_checkpoint_inference_replayed": False,
        "trusted_anchor_receipt_verified": False,
        "automatic_epoch_rotation_implemented": False,
        "e2_live_evidence_eligible": False,
        "real_activation_ready": False,
    }
    if profile.get("engineering_capabilities") != expected_capabilities:
        raise IssueProducerConfigError(
            "Deployment engineering capability boundary changed"
        )
    runtime = profile.get("runtime")
    if not isinstance(runtime, dict):
        raise IssueProducerConfigError("runtime must be an object")
    expected_runtime = {
        "root",
        "incoming_feed",
        "source_status",
        "bundle_status",
        "issue_status",
        "current_source_pointer",
        "activation_source_manifest",
        "model_manifest",
        "issue_inbox",
        "issue_receipts",
        "objects",
        "deploy_lock",
    }
    if set(runtime) != expected_runtime:
        raise IssueProducerConfigError("Deployment runtime keys changed")
    for key in expected_runtime:
        if not isinstance(runtime.get(key), str) or not runtime[key]:
            raise IssueProducerConfigError(f"runtime.{key} must be configured")
    live_order = [
        "ATU1",
        "ATU2",
        "ATU3",
        "ATU4",
        "ATU5",
        "MJ1",
        "MJ3",
        "MJ9",
    ]
    model_order = [
        "MJ9",
        "MJ1",
        "MJ3",
        "ATU1",
        "ATU2",
        "ATU3",
        "ATU4",
        "ATU5",
    ]
    expert_order = [
        "persistence",
        "seed0_p50",
        "seed1_p50",
        "seed2_p50",
        "seed3_p50",
        "seed4_p50",
    ]
    _validate_fixed_mapping(
        profile.get("historical_base"),
        {
            "path": "data/monitoring_data.csv",
            "sha256": "ee63480ad9b8065dea359d49873182b1554013f910bec1c6988c0b152bede118",
            "last_date": "2020-06-30",
            "rows": 1461,
            "role": "legacy_model_training_context_not_blind_live_evidence",
        },
        name="historical_base",
    )
    _validate_fixed_mapping(
        profile.get("station_geometry"),
        {
            "path": "data/station_coords.csv",
            "sha256": "17dac7c5985891f27905e7056b45626a0d6f4dbf34f336534c17bb76ecd32f09",
        },
        name="station_geometry",
    )
    _validate_fixed_mapping(
        profile.get("source_feed"),
        {
            "schema_version": "ootang_daily_finalized_feed_v1",
            "record_schema_version": "ootang_daily_finalized_record_v1",
            "date_timezone": "Asia/Shanghai",
            "recorded_time_timezone": "UTC",
            "expected_frequency": "P1D",
            "require_contiguous_extension": True,
            "require_all_records_finalized": True,
            "require_revision_id": True,
            "require_finalized_before_next_natural_day": True,
            "station_order_live": live_order,
            "station_order_model": model_order,
            "raw_fields": {
                "rainfall": "rainfall_mm",
                "reservoir_water_level": "reservoir_water_level_m",
                "displacement": "displacement_mm",
            },
            "units": {
                "rainfall": "mm_per_natural_day",
                "reservoir_water_level": "m",
                "displacement": "mm",
            },
            "derived_features": {
                "rwl_rate": "elapsed_day_first_difference",
                "rain_windows_days": [7, 15, 30],
                "derive_inside_trusted_code": True,
                "accept_external_derived_columns": False,
            },
        },
        name="source_feed",
    )
    _validate_fixed_mapping(
        profile.get("model"),
        {
            "bundle_schema_version": "ootang_live_five_seed_model_bundle_v1",
            "training_manifest_schema_version": "ootang_five_seed_training_manifest_v1",
            "checkpoint_schema_version": "ootang_convlstm_safe_checkpoint_v1",
            "checkpoint_format": "pytorch_weights_only_tensor_bundle_v1",
            "model_version_prefix": "ootang-convlstm-five-seed-v1",
            "seeds": [0, 1, 2, 3, 4],
            "best_seed_selected": False,
            "fit_policy": "all_as_of_windows_fixed_epochs_no_holdout_selection",
            "online_calibration_policy": "cold_start_prequential_core_not_checkpoint_calibration",
            "device": "cpu",
            "deterministic_algorithms": True,
            "torch_threads": 1,
            "lookback_days": 7,
            "horizon_days": 1,
            "input_schema": "displacement_elevation_exog_v1",
            "input_channels": 7,
            "displacement_columns": [f"{station}_disp" for station in model_order],
            "exogenous_columns": [
                "RWL",
                "RWL_rate",
                "Rain_cum7",
                "Rain_cum15",
                "Rain_cum30",
            ],
            "static_spatial_columns": ["elev_m"],
            "quantiles": [0.1, 0.5, 0.9],
            "hidden_channels": 16,
            "kernel_size": 3,
            "epochs": 120,
            "learning_rate": 0.001,
            "minimum_training_windows": 365,
            "delta_scale_floor_mm": 0.05,
        },
        name="model",
    )
    _validate_fixed_mapping(
        profile.get("issue"),
        {
            "schema_version": "ootang_live_issue_batch_v1",
            "input_manifest_schema_version": "ootang_issue_input_manifest_v1",
            "target_rule": "ledger_next_target_equals_current_source_watermark_plus_one",
            "require_no_outstanding_issue": True,
            "require_generated_before_target_start": True,
            "expert_order": expert_order,
            "same_target_same_semantics": "idempotent_preserve_first_generation_time",
            "same_target_changed_semantics": "blocked_integrity",
            "atomic_write": True,
        },
        name="issue",
    )


def load_config(
    path: Path = DEFAULT_CONFIG_PATH, *, project_root: Path = ROOT
) -> dict[str, Any]:
    """Load the strict deployment profile from one descriptor snapshot."""

    resolved_path = path.resolve()
    profile, raw = _read_json(resolved_path, name="E2-B deployment profile")
    _validate_profile(profile)
    live = profile.get("live_profile")
    if not isinstance(live, dict) or set(live) != {"path", "expected_sha256"}:
        raise IssueProducerConfigError("live_profile contract changed")
    live_path = _resolve(live["path"], project_root=project_root)
    actual_sha, _ = _sha256_file(live_path)
    if actual_sha != live["expected_sha256"]:
        raise IssueProducerConfigError("E2-A live profile SHA-256 changed")
    for contract_name in ("historical_base", "station_geometry"):
        contract = profile[contract_name]
        artifact_path = _resolve(contract["path"], project_root=project_root)
        try:
            artifact_sha, _ = _sha256_file(artifact_path)
        except IssueProducerInputError as exc:
            raise IssueProducerConfigError(
                f"Bound {contract_name} artifact is unavailable"
            ) from exc
        if artifact_sha != contract["sha256"]:
            raise IssueProducerConfigError(f"Bound {contract_name} SHA-256 changed")
    # Shared E2-B loaders use these private, non-serialized resolution fields.
    profile["_profile_path"] = str(resolved_path)
    profile["_profile_sha256"] = _sha256_bytes(raw)
    profile["_project_root"] = str(project_root.resolve())
    return profile


def _runtime_root(
    profile: Mapping[str, Any], runtime_root: Path | None, *, project_root: Path
) -> Path:
    if runtime_root is not None:
        return runtime_root.resolve()
    return _resolve(profile["runtime"]["root"], project_root=project_root)


def _status_payload(
    profile: Mapping[str, Any],
    root: Path,
    *,
    now: datetime,
    status: str,
    reason: str,
    target: date | None = None,
    issue_path: Path | None = None,
    input_manifest: Artifact | None = None,
    issue_receipt: Artifact | None = None,
    exact_issue_object: Artifact | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "ootang_issue_producer_status_v1",
        "profile_id": profile["profile_id"],
        "deploy_profile_sha256": profile["_profile_sha256"],
        "artifact_status": profile["artifact_status"],
        "producer_status": status,
        "reason": reason,
        "recorded_at_utc": _format_utc(now),
        "runtime_root": str(root),
        "target_date": target.isoformat() if target is not None else None,
        "issue_path": str(issue_path) if issue_path is not None else None,
        "input_manifest_sha256": (
            input_manifest.sha256 if input_manifest is not None else None
        ),
        "issue_receipt": (
            _artifact_payload(issue_receipt) if issue_receipt is not None else None
        ),
        "exact_issue_object": (
            _artifact_payload(exact_issue_object)
            if exact_issue_object is not None
            else None
        ),
        "formal_warning_output": False,
        "independent_label_used": False,
        "confirmatory_external_validation": False,
        "vajont_used": False,
        **profile["engineering_capabilities"],
        "source_manifest_semantics_verified_by_producer": status
        in {
            "waiting_for_issuable_future_target",
            "issued",
            "already_issued_idempotent",
        },
        "safe_checkpoint_loading_exercised": status
        in {
            "waiting_for_issuable_future_target",
            "issued",
            "already_issued_idempotent",
        },
        "producer_checkpoint_inference_replayed": status
        in {"issued", "already_issued_idempotent"},
    }
    return payload


def _write_status(
    profile: Mapping[str, Any],
    root: Path,
    *,
    now: datetime,
    status: str,
    reason: str,
    target: date | None = None,
    issue_path: Path | None = None,
    input_manifest: Artifact | None = None,
    issue_receipt: Artifact | None = None,
    exact_issue_object: Artifact | None = None,
) -> Path:
    path = required_path(root, profile["runtime"]["issue_status"])
    _atomic_write_json(
        path,
        _status_payload(
            profile,
            root,
            now=now,
            status=status,
            reason=reason,
            target=target,
            issue_path=issue_path,
            input_manifest=input_manifest,
            issue_receipt=issue_receipt,
            exact_issue_object=exact_issue_object,
        ),
    )
    return path


def _target_and_times(
    target: date,
    source_as_of_at_utc: str,
    *,
    now: datetime,
    timezone_name: str,
) -> tuple[date, str]:
    if now.tzinfo is None:
        raise IssueProducerInputError("Machine clock must be timezone-aware")
    now_utc = now.astimezone(timezone.utc)
    local_zone = ZoneInfo(timezone_name)
    local_today = now_utc.astimezone(local_zone).date()
    if target <= local_today:
        raise _NoIssuableTarget(
            "No issuable future target: watermark plus one is on/before local today"
        )
    target_start = datetime.combine(target, time.min, tzinfo=local_zone).astimezone(
        timezone.utc
    )
    source_as_of = _parse_utc(source_as_of_at_utc, name="source_as_of_at_utc")
    if source_as_of >= target_start or now_utc >= target_start:
        raise IssueProducerInputError(
            "Source as-of and generation time must precede target start"
        )
    if source_as_of > now_utc:
        raise IssueProducerInputError("Source as-of time cannot follow generation")
    return target, _format_utc(now_utc)


def _parse_date(value: object, *, name: str) -> date:
    if not isinstance(value, str):
        raise IssueProducerInputError(f"{name} must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise IssueProducerInputError(f"{name} must be YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise IssueProducerInputError(f"{name} must be canonical YYYY-MM-DD")
    return parsed


def _coerce_day(value: object, *, name: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return _parse_date(value, name=name)


def _target_authority(
    *,
    source_watermark: date,
    activation_watermark: date,
    ledger_path: Path,
    live_status_path: Path,
    live_profile: Mapping[str, Any] | None = None,
    live_paths: object | None = None,
    prerequisites: object | None = None,
) -> _TargetAuthority:
    """Bind source progression to a fully replayed E2-A ledger projection.

    The caller must hold E2-A's runner lock from this read until publication.
    Status JSON is treated only as a cached projection: every scientific field
    used here is first reconstructed from the verified append-only ledger, then
    required to match the status snapshot exactly.
    """

    source_next = source_watermark + timedelta(days=1)
    if not ledger_path.exists():
        activation_next = activation_watermark + timedelta(days=1)
        if source_next != activation_next:
            raise _MachineWaiting(
                "pre-genesis current source moved beyond activation watermark"
            )
        activation_source = getattr(prerequisites, "source", None)
        activation_latest = getattr(activation_source, "latest_displacement_mm", None)
        return _TargetAuthority(activation_next, activation_latest)
    if not ledger_path.is_file():
        raise IssueProducerInputError("E2-A ledger path is not a regular file")
    if live_profile is None or live_paths is None or prerequisites is None:
        raise IssueProducerInputError(
            "Activated ledger requires E2-A prerequisites for verified replay"
        )
    if not live_status_path.is_file():
        raise IssueProducerInputError("Activated ledger has no E2-A status projection")

    # Lazy imports preserve the missing-prerequisite waiting path without
    # importing the E2-A stack, while ensuring an existing SQLite file is never
    # accepted merely because it has the configured name.
    from monitoring import ootang_prequential_live as live_module

    try:
        projection = live_module.load_verified_ledger_projection(
            dict(live_profile), live_paths, prerequisites
        )
    except (
        live_module.LiveInputError,
        live_module.LiveIntegrityError,
        live_module.LivePrerequisiteError,
    ) as exc:
        raise IssueProducerInputError(
            f"E2-A ledger projection failed complete replay: {exc}"
        ) from exc

    status, _ = _read_json(live_status_path, name="E2-A live status")
    if status.get("schema_version") != "ootang_prequential_live_status_v1":
        raise IssueProducerInputError("E2-A status schema changed")
    outstanding = (
        projection.outstanding_target_date.isoformat()
        if projection.outstanding_target_date is not None
        else None
    )
    next_target = (projection.last_finalized_date + timedelta(days=1)).isoformat()
    expected_status = {
        "ledger_event_count": projection.ledger_event_count,
        "ledger_terminal_sha256": projection.ledger_terminal_sha256,
        "ledger_terminal_sequence_id": projection.ledger_terminal_sequence_id,
        "live_epoch_id": projection.epoch_id,
        "last_finalized_date": projection.last_finalized_date.isoformat(),
        "next_target_date": next_target,
        "outstanding_target_date": outstanding,
        "source_snapshot_sha256": prerequisites.source.sha256,
        "production_model_manifest_sha256": prerequisites.model.sha256,
    }
    mismatches = sorted(
        key for key, expected in expected_status.items() if status.get(key) != expected
    )
    if mismatches:
        raise IssueProducerInputError(
            "E2-A status does not match verified ledger projection: "
            + ",".join(mismatches)
        )
    if outstanding is not None:
        raise _MachineWaiting("E2-A already has an outstanding issue")
    ledger_next = _parse_date(next_target, name="ledger.next_target_date")
    if ledger_next != source_next:
        raise _MachineWaiting(
            "E2-A ledger and current source watermark require catch-up before issue"
        )
    return _TargetAuthority(ledger_next, projection.latest_displacement_mm)


def _required_target(
    *,
    source_watermark: date,
    activation_watermark: date,
    ledger_path: Path,
    live_status_path: Path,
    live_profile: Mapping[str, Any] | None = None,
    live_paths: object | None = None,
    prerequisites: object | None = None,
) -> date:
    """Compatibility wrapper returning only the verified next target date."""

    return _target_authority(
        source_watermark=source_watermark,
        activation_watermark=activation_watermark,
        ledger_path=ledger_path,
        live_status_path=live_status_path,
        live_profile=live_profile,
        live_paths=live_paths,
        prerequisites=prerequisites,
    ).target_date


def _artifact_payload(artifact: Artifact) -> dict[str, Any]:
    return {
        "path": str(artifact.path),
        "sha256": artifact.sha256,
        "size_bytes": artifact.size_bytes,
    }


def _coerce_artifact(value: object, *, name: str) -> Artifact:
    try:
        path = Path(getattr(value, "path")).resolve()
        sha256 = str(getattr(value, "sha256"))
        size_bytes = int(getattr(value, "size_bytes"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise IssueProducerInputError(f"{name} is not an artifact reference") from exc
    if (
        len(sha256) != 64
        or any(character not in HEX_DIGITS for character in sha256)
        or size_bytes < 0
    ):
        raise IssueProducerInputError(f"{name} metadata is invalid")
    actual_sha, actual_size = _sha256_file(path)
    if actual_sha != sha256 or actual_size != size_bytes:
        raise IssueProducerInputError(f"{name} no longer matches its artifact bytes")
    return Artifact(path, sha256, size_bytes)


def _artifact_from_path(path: Path) -> Artifact:
    sha256, size_bytes = _sha256_file(path)
    return Artifact(path.resolve(), sha256, size_bytes)


def _artifact_from_mapping(value: object, *, name: str) -> Artifact:
    if not isinstance(value, dict) or set(value) != {"path", "sha256", "size_bytes"}:
        raise IssueProducerConflict(f"{name} artifact reference changed schema")
    path_value = value["path"]
    sha256 = value["sha256"]
    size_bytes = value["size_bytes"]
    if not isinstance(path_value, str) or not path_value:
        raise IssueProducerConflict(f"{name}.path is invalid")
    if (
        not isinstance(sha256, str)
        or len(sha256) != 64
        or any(character not in HEX_DIGITS for character in sha256)
    ):
        raise IssueProducerConflict(f"{name}.sha256 is invalid")
    if (
        isinstance(size_bytes, bool)
        or not isinstance(size_bytes, int)
        or size_bytes < 0
    ):
        raise IssueProducerConflict(f"{name}.size_bytes is invalid")
    artifact = Artifact(Path(path_value).resolve(), sha256, size_bytes)
    try:
        actual_sha, actual_size = _sha256_file(artifact.path)
    except IssueProducerInputError as exc:
        raise IssueProducerConflict(f"{name} artifact cannot be read") from exc
    if actual_sha != artifact.sha256 or actual_size != artifact.size_bytes:
        raise IssueProducerConflict(f"{name} artifact bytes changed")
    return artifact


def _runtime_version_record() -> dict[str, str]:
    try:
        import numpy as np
        import pandas as pd
        import torch
    except ImportError as exc:
        raise IssueProducerInputError(
            "Issue producer runtime dependency is unavailable"
        ) from exc
    return {
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "numpy_version": str(np.__version__),
        "pandas_version": str(pd.__version__),
        "torch_version": str(torch.__version__),
    }


def _implementation_record(profile: Mapping[str, Any]) -> dict[str, Any]:
    deploy_profile = _artifact_from_path(Path(profile["_profile_path"]).resolve())
    if deploy_profile.sha256 != profile["_profile_sha256"]:
        raise IssueProducerInputError(
            "Deployment profile changed after its validated snapshot"
        )
    return {
        "schema_version": ISSUE_IMPLEMENTATION_SCHEMA_VERSION,
        "deploy_profile": _artifact_payload(deploy_profile),
        "issue_producer": _artifact_payload(_artifact_from_path(Path(__file__))),
        "pyproject": _artifact_payload(_artifact_from_path(ROOT / "pyproject.toml")),
        "uv_lock": _artifact_payload(_artifact_from_path(ROOT / "uv.lock")),
        "runtime": _runtime_version_record(),
    }


def _validate_implementation_record(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "deploy_profile",
        "issue_producer",
        "pyproject",
        "uv_lock",
        "runtime",
    }:
        raise IssueProducerConflict("Issue implementation record keys changed")
    if value["schema_version"] != ISSUE_IMPLEMENTATION_SCHEMA_VERSION:
        raise IssueProducerConflict("Issue implementation record schema changed")
    deploy_profile = _artifact_from_mapping(
        value["deploy_profile"], name="implementation.deploy_profile"
    )
    issue_producer = _artifact_from_mapping(
        value["issue_producer"], name="implementation.issue_producer"
    )
    pyproject = _artifact_from_mapping(
        value["pyproject"], name="implementation.pyproject"
    )
    uv_lock = _artifact_from_mapping(value["uv_lock"], name="implementation.uv_lock")
    expected_paths = {
        "issue_producer": Path(__file__).resolve(),
        "pyproject": (ROOT / "pyproject.toml").resolve(),
        "uv_lock": (ROOT / "uv.lock").resolve(),
    }
    actual_paths = {
        "issue_producer": issue_producer.path,
        "pyproject": pyproject.path,
        "uv_lock": uv_lock.path,
    }
    changed_paths = sorted(
        name for name, path in actual_paths.items() if path != expected_paths[name]
    )
    if changed_paths:
        raise IssueProducerConflict(
            "Issue implementation artifact paths changed: " + ",".join(changed_paths)
        )
    runtime = value["runtime"]
    expected_runtime = _runtime_version_record()
    if not isinstance(runtime, dict) or set(runtime) != set(expected_runtime):
        raise IssueProducerConflict("Issue implementation runtime keys changed")
    if runtime != expected_runtime:
        raise IssueProducerConflict("Issue implementation runtime versions changed")
    return {
        "schema_version": ISSUE_IMPLEMENTATION_SCHEMA_VERSION,
        "deploy_profile_sha256": deploy_profile.sha256,
        "issue_producer_sha256": issue_producer.sha256,
        "pyproject_sha256": pyproject.sha256,
        "uv_lock_sha256": uv_lock.sha256,
        **expected_runtime,
    }


def _scientific_semantics(
    *,
    target_date: str,
    source_watermark: str,
    dataset_sha256: str,
    activation_sha256: str,
    model_manifest_sha256: str,
    training_manifest_sha256: str,
    checkpoints: Sequence[tuple[int, str]],
    model_rows: Sequence[Mapping[str, Any]],
    issued_stations: Sequence[Mapping[str, Any]],
    station_order_model: Sequence[str],
    station_order_live: Sequence[str],
    expert_order: Sequence[str],
    implementation: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "ootang_issue_scientific_semantics_v1",
        "case": "ootang",
        "target_date": target_date,
        "source_watermark": source_watermark,
        "canonical_dataset_sha256": dataset_sha256,
        "activation_source_manifest_sha256": activation_sha256,
        "model_manifest_sha256": model_manifest_sha256,
        "training_manifest_sha256": training_manifest_sha256,
        "checkpoint_sha256_by_seed": {
            str(seed): sha256 for seed, sha256 in checkpoints
        },
        "model_rows": [dict(row) for row in model_rows],
        "issued_station_experts": [dict(row) for row in issued_stations],
        "station_order_model": list(station_order_model),
        "station_order_live": list(station_order_live),
        "expert_order": list(expert_order),
        "implementation": dict(implementation),
    }


def _model_row_records(frame: Any, profile: Mapping[str, Any]) -> list[dict[str, Any]]:
    columns = [
        *profile["model"]["displacement_columns"],
        *profile["model"]["exogenous_columns"],
    ]
    expected = {"Date", *columns}
    if not expected.issubset(frame.columns):
        raise IssueProducerInputError("Canonical source lacks model input columns")
    records: list[dict[str, Any]] = []
    for row_index, (_, row) in enumerate(frame.iterrows()):
        row_day = _coerce_day(row["Date"], name=f"model_rows[{row_index}].Date")
        record: dict[str, Any] = {"date": row_day.isoformat()}
        for column in columns:
            record[column] = _finite(
                row[column], name=f"model_rows[{row_index}].{column}"
            )
        records.append(record)
    return records


def _select_model_rows(source: object, profile: Mapping[str, Any]) -> Any:
    """Return the only seven rows visible to checkpoint inference."""

    frame = source.frame
    if "Date" not in frame.columns:
        raise IssueProducerInputError("Canonical source lacks Date")
    days = [
        _coerce_day(value, name=f"source.Date[{index}]")
        for index, value in enumerate(frame["Date"])
    ]
    eligible = frame.loc[[day <= source.watermark for day in days]]
    lookback = int(profile["model"]["lookback_days"])
    rows = eligible.tail(lookback).copy()
    if len(rows) != lookback:
        raise IssueProducerInputError("Exactly seven source rows are required")
    dates = [
        _coerce_day(value, name=f"selected.Date[{index}]")
        for index, value in enumerate(rows["Date"])
    ]
    if dates[-1] != source.watermark or any(
        right - left != timedelta(days=1) for left, right in zip(dates, dates[1:])
    ):
        raise IssueProducerInputError(
            "Seven model rows must end at a contiguous watermark"
        )
    return rows


def _validate_authoritative_latest_displacement(
    model_rows: Any,
    profile: Mapping[str, Any],
    authoritative_latest: object,
) -> dict[str, float]:
    """Mirror E2-A's exact persistence baseline gate before inference.

    The current source row is useful for model input only when its watermark
    displacement is exactly the state reconstructed by E2-A.  Before genesis,
    the immutable activation snapshot is that authority.
    """

    live_order = list(profile["source_feed"]["station_order_live"])
    if not isinstance(authoritative_latest, Mapping) or set(
        authoritative_latest
    ) != set(live_order):
        raise IssueProducerInputError(
            "Authoritative latest displacement station set changed"
        )
    latest_row = model_rows.iloc[-1]
    current = {
        station: _finite(
            latest_row[f"{station}_disp"], name=f"current.latest.{station}"
        )
        for station in live_order
    }
    authoritative = {
        station: _finite(
            authoritative_latest[station], name=f"authoritative.latest.{station}"
        )
        for station in live_order
    }
    mismatches = [
        station for station in live_order if current[station] != authoritative[station]
    ]
    if mismatches:
        raise _MachineWaiting(
            "current source watermark displacement does not match the "
            "authoritative E2-A baseline: " + ",".join(mismatches)
        )
    return current


def _checkpoint_artifacts(bundle: object) -> list[tuple[int, Artifact]]:
    values = getattr(bundle, "checkpoints", None)
    if not isinstance(values, Sequence) or len(values) != 5:
        raise IssueProducerInputError("Bundle must expose exactly five checkpoints")
    result: list[tuple[int, Artifact]] = []
    for index, checkpoint in enumerate(values):
        seed = getattr(checkpoint, "seed", index)
        if seed != index:
            raise IssueProducerInputError("Checkpoint seeds or order changed")
        artifact_value = getattr(checkpoint, "artifact", checkpoint)
        result.append(
            (seed, _coerce_artifact(artifact_value, name=f"checkpoint[{index}]"))
        )
    return result


def _build_input_manifest(
    *,
    profile: Mapping[str, Any],
    target: date,
    source: object,
    bundle: object,
    model_rows: Any,
    stations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    dataset = _coerce_artifact(source.dataset, name="source.dataset")
    semantic_manifest = _coerce_artifact(
        source.semantic_manifest, name="source.semantic_manifest"
    )
    activation_manifest = _coerce_artifact(
        source.activation_manifest, name="source.activation_manifest"
    )
    model_manifest_path = Path(bundle.manifest_path).resolve()
    model_manifest = _artifact_from_path(model_manifest_path)
    if model_manifest.sha256 != bundle.manifest_sha256:
        raise IssueProducerInputError("Bundle manifest hash changed after validation")
    training_value = getattr(bundle, "training_manifest", None)
    if training_value is None:
        raise IssueProducerInputError("Bundle does not expose its training manifest")
    training_manifest = _coerce_artifact(
        getattr(training_value, "artifact", training_value),
        name="model.training_manifest",
    )
    checkpoints = _checkpoint_artifacts(bundle)
    row_records = _model_row_records(model_rows, profile)
    if len(row_records) != 7:
        raise IssueProducerInputError("Input manifest must bind exactly seven rows")
    station_order_model = list(profile["source_feed"]["station_order_model"])
    station_order_live = list(profile["source_feed"]["station_order_live"])
    expert_order = list(profile["issue"]["expert_order"])
    implementation_record = _implementation_record(profile)
    implementation_semantics = _validate_implementation_record(implementation_record)
    stable_science = _scientific_semantics(
        target_date=target.isoformat(),
        source_watermark=source.watermark.isoformat(),
        dataset_sha256=dataset.sha256,
        activation_sha256=activation_manifest.sha256,
        model_manifest_sha256=model_manifest.sha256,
        training_manifest_sha256=training_manifest.sha256,
        checkpoints=[(seed, artifact.sha256) for seed, artifact in checkpoints],
        model_rows=row_records,
        issued_stations=stations,
        station_order_model=station_order_model,
        station_order_live=station_order_live,
        expert_order=expert_order,
        implementation=implementation_semantics,
    )
    return {
        "schema_version": profile["issue"]["input_manifest_schema_version"],
        "case": "ootang",
        "target_date": target.isoformat(),
        "source_watermark": source.watermark.isoformat(),
        "scientific_semantics_sha256": _canonical_digest(stable_science),
        "source": {
            "canonical_dataset": _artifact_payload(dataset),
            "semantic_manifest": _artifact_payload(semantic_manifest),
            "activation_source_manifest": _artifact_payload(activation_manifest),
        },
        "model": {
            "outer_manifest": _artifact_payload(model_manifest),
            "training_manifest": _artifact_payload(training_manifest),
            "checkpoints": [
                {"seed": seed, "artifact": _artifact_payload(artifact)}
                for seed, artifact in checkpoints
            ],
        },
        "implementation": implementation_record,
        "model_rows": row_records,
        "station_order_model": station_order_model,
        "station_order_live": station_order_live,
        "expert_order": expert_order,
    }


def _materialize_content_addressed_json(
    root: Path, profile: Mapping[str, Any], payload: Mapping[str, Any]
) -> Artifact:
    raw = _canonical_bytes(dict(payload))
    digest = _sha256_bytes(raw)
    object_root = required_path(root, profile["runtime"]["objects"])
    path = object_root / f"{digest}.json"
    if path.exists():
        actual, size = _sha256_file(path)
        if actual != digest or size != len(raw) or path.read_bytes() != raw:
            raise IssueProducerConflict("Content-addressed input manifest is corrupted")
    else:
        if not _atomic_create_bytes(path, raw):
            actual, size = _sha256_file(path)
            if actual != digest or size != len(raw) or path.read_bytes() != raw:
                raise IssueProducerConflict(
                    "Concurrent content-addressed manifest conflicts"
                )
    return Artifact(path=path.resolve(), sha256=digest, size_bytes=len(raw))


def _input_scientific_hash(value: object, issue: Mapping[str, Any]) -> str:
    reference = _artifact_from_mapping(value, name="issue.input_manifest")
    manifest, _ = _read_json(reference.path, name="issue input manifest")
    expected_top = {
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
    }
    if set(manifest) != expected_top:
        raise IssueProducerConflict("Issue input manifest keys changed")
    if (
        manifest["schema_version"] != "ootang_issue_input_manifest_v1"
        or manifest["case"] != "ootang"
        or manifest["target_date"] != issue.get("target_date")
    ):
        raise IssueProducerConflict("Issue input manifest identity changed")
    watermark = _parse_date(manifest["source_watermark"], name="input.source_watermark")
    target = _parse_date(manifest["target_date"], name="input.target_date")
    if target != watermark + timedelta(days=1):
        raise IssueProducerConflict("Issue input target is not watermark plus one")

    source_value = manifest["source"]
    if not isinstance(source_value, dict) or set(source_value) != {
        "canonical_dataset",
        "semantic_manifest",
        "activation_source_manifest",
    }:
        raise IssueProducerConflict("Issue input source contract changed")
    dataset = _artifact_from_mapping(
        source_value["canonical_dataset"], name="input.source.canonical_dataset"
    )
    _artifact_from_mapping(
        source_value["semantic_manifest"], name="input.source.semantic_manifest"
    )
    activation = _artifact_from_mapping(
        source_value["activation_source_manifest"],
        name="input.source.activation_source_manifest",
    )

    model_value = manifest["model"]
    if not isinstance(model_value, dict) or set(model_value) != {
        "outer_manifest",
        "training_manifest",
        "checkpoints",
    }:
        raise IssueProducerConflict("Issue input model contract changed")
    outer = _artifact_from_mapping(
        model_value["outer_manifest"], name="input.model.outer_manifest"
    )
    training = _artifact_from_mapping(
        model_value["training_manifest"], name="input.model.training_manifest"
    )
    checkpoint_values = model_value["checkpoints"]
    if not isinstance(checkpoint_values, list) or len(checkpoint_values) != 5:
        raise IssueProducerConflict("Issue input checkpoints are incomplete")
    checkpoints: list[tuple[int, str]] = []
    for seed, checkpoint_value in enumerate(checkpoint_values):
        if not isinstance(checkpoint_value, dict) or set(checkpoint_value) != {
            "seed",
            "artifact",
        }:
            raise IssueProducerConflict("Issue input checkpoint schema changed")
        if checkpoint_value["seed"] != seed:
            raise IssueProducerConflict("Issue input checkpoint order changed")
        artifact = _artifact_from_mapping(
            checkpoint_value["artifact"], name=f"input.model.checkpoint[{seed}]"
        )
        checkpoints.append((seed, artifact.sha256))

    implementation = _validate_implementation_record(manifest["implementation"])

    model_order = [
        "MJ9",
        "MJ1",
        "MJ3",
        "ATU1",
        "ATU2",
        "ATU3",
        "ATU4",
        "ATU5",
    ]
    live_order = ["ATU1", "ATU2", "ATU3", "ATU4", "ATU5", "MJ1", "MJ3", "MJ9"]
    experts = [
        "persistence",
        "seed0_p50",
        "seed1_p50",
        "seed2_p50",
        "seed3_p50",
        "seed4_p50",
    ]
    if (
        manifest["station_order_model"] != model_order
        or manifest["station_order_live"] != live_order
        or manifest["expert_order"] != experts
    ):
        raise IssueProducerConflict("Issue input station/expert order changed")
    model_rows = manifest["model_rows"]
    if not isinstance(model_rows, list) or len(model_rows) != 7:
        raise IssueProducerConflict("Issue input must contain seven model rows")
    expected_row_keys = {
        "date",
        *[f"{station}_disp" for station in model_order],
        "RWL",
        "RWL_rate",
        "Rain_cum7",
        "Rain_cum15",
        "Rain_cum30",
    }
    row_dates: list[date] = []
    for index, row in enumerate(model_rows):
        if not isinstance(row, dict) or set(row) != expected_row_keys:
            raise IssueProducerConflict("Issue input model row schema changed")
        row_dates.append(
            _parse_date(row["date"], name=f"input.model_rows[{index}].date")
        )
        for key in expected_row_keys - {"date"}:
            _finite(row[key], name=f"input.model_rows[{index}].{key}")
    if row_dates[-1] != watermark or any(
        right - left != timedelta(days=1)
        for left, right in zip(row_dates, row_dates[1:])
    ):
        raise IssueProducerConflict("Issue input model dates changed")

    issued_stations = issue.get("stations")
    if not isinstance(issued_stations, list) or len(issued_stations) != 8:
        raise IssueProducerConflict("Issue stations are incomplete")
    if [
        row.get("station") for row in issued_stations if isinstance(row, dict)
    ] != live_order:
        raise IssueProducerConflict("Issue station order changed")
    expected_station_keys = {
        "station",
        "persistence_mm",
        "seed0_p50_mm",
        "seed1_p50_mm",
        "seed2_p50_mm",
        "seed3_p50_mm",
        "seed4_p50_mm",
    }
    for index, row in enumerate(issued_stations):
        if not isinstance(row, dict) or set(row) != expected_station_keys:
            raise IssueProducerConflict("Issue station expert schema changed")
        for key in expected_station_keys - {"station"}:
            _finite(row[key], name=f"issue.stations[{index}].{key}")

    if (
        issue.get("source_snapshot_sha256") != activation.sha256
        or issue.get("model_manifest_sha256") != outer.sha256
    ):
        raise IssueProducerConflict("Issue artifact bindings changed")
    expected_science = _scientific_semantics(
        target_date=manifest["target_date"],
        source_watermark=manifest["source_watermark"],
        dataset_sha256=dataset.sha256,
        activation_sha256=activation.sha256,
        model_manifest_sha256=outer.sha256,
        training_manifest_sha256=training.sha256,
        checkpoints=checkpoints,
        model_rows=model_rows,
        issued_stations=issued_stations,
        station_order_model=model_order,
        station_order_live=live_order,
        expert_order=experts,
        implementation=implementation,
    )
    scientific = manifest["scientific_semantics_sha256"]
    if scientific != _canonical_digest(expected_science):
        raise IssueProducerConflict("Issue scientific semantics digest is invalid")
    return scientific


def _semantic_issue(payload: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "schema_version",
        "target_date",
        "generated_at_utc",
        "source_as_of_at_utc",
        "source_snapshot_sha256",
        "model_manifest_sha256",
        "input_manifest",
        "stations",
    }
    if set(payload) != expected:
        raise IssueProducerConflict("Issue batch keys changed")
    if payload.get("schema_version") != "ootang_live_issue_batch_v1":
        raise IssueProducerConflict("Issue batch schema changed")
    semantic = dict(payload)
    semantic.pop("generated_at_utc", None)
    # Capture/export time can advance when a byte-identical canonical source is
    # republished.  It is a temporal eligibility check, not scientific content.
    semantic.pop("source_as_of_at_utc", None)
    semantic["input_manifest"] = {
        "scientific_semantics_sha256": _input_scientific_hash(
            semantic.get("input_manifest"), payload
        )
    }
    return semantic


def _issue_receipt_path(receipt_root: Path, target: str) -> Path:
    parsed = _parse_date(target, name="issue.target_date")
    path = (receipt_root / f"{parsed.isoformat()}.json").resolve()
    try:
        path.relative_to(receipt_root.resolve())
    except ValueError as exc:
        raise IssueProducerConfigError(
            "Issue receipt path escapes its registry"
        ) from exc
    return path


def _exact_issue_object_path(object_root: Path, digest: str) -> Path:
    if len(digest) != 64 or any(character not in HEX_DIGITS for character in digest):
        raise IssueProducerConflict("Exact issue object digest is invalid")
    return (object_root / f"{digest}.issue.json").resolve()


def _materialize_exact_issue_object(object_root: Path, raw: bytes) -> Artifact:
    digest = _sha256_bytes(raw)
    path = _exact_issue_object_path(object_root, digest)
    if not path.exists() and _atomic_create_bytes(path, raw):
        return Artifact(path, digest, len(raw))
    if not path.is_file():
        raise IssueProducerConflict("Exact issue object path is not a regular file")
    _, existing_raw = _read_json(path, name="exact issue object")
    if existing_raw != raw or _sha256_bytes(existing_raw) != digest:
        raise IssueProducerConflict("Content-addressed exact issue object is corrupted")
    return Artifact(path, digest, len(raw))


def _load_issue_receipt(
    receipt_path: Path,
    *,
    issue_path: Path,
    object_root: Path,
) -> tuple[Artifact, Artifact, dict[str, Any], bytes, str]:
    receipt, receipt_raw = _read_json(receipt_path, name="issue producer receipt")
    expected = {
        "schema_version",
        "target_date",
        "issue_path",
        "scientific_semantics_sha256",
        "generated_at_utc",
        "source_as_of_at_utc",
        "exact_issue_object",
    }
    if set(receipt) != expected:
        raise IssueProducerConflict("Issue producer receipt keys changed")
    if receipt_raw != _canonical_bytes(receipt):
        raise IssueProducerConflict("Issue producer receipt bytes are not canonical")
    if receipt["schema_version"] != ISSUE_RECEIPT_SCHEMA_VERSION:
        raise IssueProducerConflict("Issue producer receipt schema changed")
    target = _parse_date(receipt["target_date"], name="receipt.target_date")
    if issue_path.name != f"{target.isoformat()}.json":
        raise IssueProducerConflict("Issue receipt target/path binding changed")
    if receipt["issue_path"] != str(issue_path.resolve()):
        raise IssueProducerConflict("Issue receipt absolute path changed")
    scientific = receipt["scientific_semantics_sha256"]
    if (
        not isinstance(scientific, str)
        or len(scientific) != 64
        or any(character not in HEX_DIGITS for character in scientific)
    ):
        raise IssueProducerConflict("Issue receipt scientific digest is invalid")
    exact_object = _artifact_from_mapping(
        receipt["exact_issue_object"], name="receipt.exact_issue_object"
    )
    expected_object_path = _exact_issue_object_path(object_root, exact_object.sha256)
    if exact_object.path != expected_object_path:
        raise IssueProducerConflict(
            "Issue receipt object escaped the configured registry"
        )
    stored_issue, stored_raw = _read_json(
        exact_object.path, name="registered exact issue object"
    )
    if (
        stored_raw != _canonical_bytes(stored_issue)
        or _sha256_bytes(stored_raw) != exact_object.sha256
        or len(stored_raw) != exact_object.size_bytes
    ):
        raise IssueProducerConflict("Registered exact issue bytes are not canonical")
    stored_scientific = _canonical_digest(_semantic_issue(stored_issue))
    if stored_scientific != scientific:
        raise IssueProducerConflict("Issue receipt scientific binding changed")
    if receipt["generated_at_utc"] != stored_issue.get("generated_at_utc") or receipt[
        "source_as_of_at_utc"
    ] != stored_issue.get("source_as_of_at_utc"):
        raise IssueProducerConflict("Issue receipt time binding changed")
    receipt_artifact = Artifact(
        receipt_path.resolve(), _sha256_bytes(receipt_raw), len(receipt_raw)
    )
    return receipt_artifact, exact_object, stored_issue, stored_raw, scientific


def _registered_issue_artifacts(
    *,
    issue_path: Path,
    receipt_root: Path,
    object_root: Path,
) -> tuple[Artifact, Artifact]:
    target = issue_path.stem
    receipt_path = _issue_receipt_path(receipt_root, target)
    receipt, exact_object, _, registered_raw, _ = _load_issue_receipt(
        receipt_path, issue_path=issue_path, object_root=object_root
    )
    if not issue_path.is_file():
        raise IssueProducerConflict("Registered issue batch is missing")
    _, durable_raw = _read_json(issue_path, name="durable registered issue batch")
    if durable_raw != registered_raw:
        raise IssueProducerConflict(
            "Durable issue bytes differ from first-publication receipt"
        )
    return receipt, exact_object


def _withdraw_registered_issue(
    *,
    issue_path: Path,
    receipt_root: Path,
    object_root: Path,
) -> None:
    """Remove a just-published inbox copy while preserving its receipt/object."""

    registry_error: IssueProducerError | None = None
    try:
        _registered_issue_artifacts(
            issue_path=issue_path,
            receipt_root=receipt_root,
            object_root=object_root,
        )
    except IssueProducerError as exc:
        # Removal is still mandatory: a just-created consumer-facing file is
        # less safe than its receipt/object registry when validation has failed.
        registry_error = exc
    if issue_path.exists():
        try:
            issue_path.unlink()
        except OSError as exc:
            raise IssueProducerConflict(
                "Late issue inbox could not be withdrawn before releasing runner lock"
            ) from exc
        directory = os.open(issue_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    if registry_error is not None:
        raise IssueProducerConflict(
            "Published issue was withdrawn after registry validation failed"
        ) from registry_error


def _restore_registered_issue(
    *,
    issue_path: Path,
    receipt_path: Path,
    object_root: Path,
) -> tuple[bool, str]:
    """Restore/verify the receipt's exact bytes before inspecting a retry."""

    _, _, _, registered_raw, registered_scientific = _load_issue_receipt(
        receipt_path, issue_path=issue_path, object_root=object_root
    )
    if issue_path.exists():
        if not issue_path.is_file():
            raise IssueProducerConflict("Registered issue path is not a regular file")
        existing, existing_raw = _read_json(issue_path, name="existing issue batch")
        # Revalidate nested artifacts, then require exact first-publication bytes.
        _semantic_issue(existing)
        if existing_raw != registered_raw:
            raise IssueProducerConflict(
                "Existing issue bytes differ from first-publication receipt"
            )
        return False, registered_scientific
    if _atomic_create_bytes(issue_path, registered_raw):
        return True, registered_scientific
    if issue_path.is_file():
        _, existing_raw = _read_json(issue_path, name="concurrent issue batch")
        if existing_raw == registered_raw:
            return False, registered_scientific
    raise IssueProducerConflict("Issue target conflicted during registered publication")


def _reconcile_registered_issue(
    *,
    issue_path: Path,
    receipt_path: Path,
    object_root: Path,
    candidate_payload: Mapping[str, Any],
) -> bool:
    restored, registered_scientific = _restore_registered_issue(
        issue_path=issue_path,
        receipt_path=receipt_path,
        object_root=object_root,
    )
    # Receipt creation is the commit point.  A missing inbox is repaired from
    # its exact object even when this retry later proves scientifically different.
    candidate_scientific = _canonical_digest(_semantic_issue(candidate_payload))
    target_value = candidate_payload.get("target_date")
    if not isinstance(target_value, str) or issue_path.name != f"{target_value}.json":
        raise IssueProducerConflict("Issue filename does not match its target date")
    if registered_scientific != candidate_scientific:
        raise IssueProducerConflict(
            "Same target already has a different semantic issue payload"
        )
    return restored


def _publish_issue_idempotently(
    path: Path,
    payload: Mapping[str, Any],
    *,
    receipt_root: Path,
    object_root: Path,
) -> bool:
    """Register exact first bytes, then publish/replay the durable issue batch."""

    # Derive the registry lookup from the durable destination, not from an
    # untrusted retry.  This lets a valid receipt repair its missing inbox before
    # any malformed or scientifically changed candidate is evaluated.
    target_value = _parse_date(path.stem, name="issue filename").isoformat()
    receipt_path = _issue_receipt_path(receipt_root, target_value)
    if receipt_path.exists():
        return _reconcile_registered_issue(
            issue_path=path,
            receipt_path=receipt_path,
            object_root=object_root,
            candidate_payload=payload,
        )

    candidate_scientific = _canonical_digest(_semantic_issue(payload))
    if payload.get("target_date") != target_value:
        raise IssueProducerConflict("Issue filename does not match its target date")
    raw = _canonical_bytes(dict(payload))
    # An inbox file without the producer-owned first-publication receipt cannot
    # be safely adopted: its ignored time fields may already have been altered.
    if path.exists():
        raise IssueProducerConflict("Existing issue has no first-publication receipt")

    exact_object = _materialize_exact_issue_object(object_root, raw)
    receipt_payload = {
        "schema_version": ISSUE_RECEIPT_SCHEMA_VERSION,
        "target_date": target_value,
        "issue_path": str(path.resolve()),
        "scientific_semantics_sha256": candidate_scientific,
        "generated_at_utc": payload.get("generated_at_utc"),
        "source_as_of_at_utc": payload.get("source_as_of_at_utc"),
        "exact_issue_object": _artifact_payload(exact_object),
    }
    _parse_utc(receipt_payload["generated_at_utc"], name="issue.generated_at_utc")
    _parse_utc(receipt_payload["source_as_of_at_utc"], name="issue.source_as_of_at_utc")
    if not _atomic_create_bytes(receipt_path, _canonical_bytes(receipt_payload)):
        return _reconcile_registered_issue(
            issue_path=path,
            receipt_path=receipt_path,
            object_root=object_root,
            candidate_payload=payload,
        )
    return _reconcile_registered_issue(
        issue_path=path,
        receipt_path=receipt_path,
        object_root=object_root,
        candidate_payload=payload,
    )


def _station_rows(
    profile: Mapping[str, Any],
    *,
    latest_displacement: Mapping[str, float],
    seed_predictions: Mapping[int, Mapping[str, float]] | Sequence[Sequence[float]],
) -> list[dict[str, Any]]:
    model_order = list(profile["source_feed"]["station_order_model"])
    live_order = list(profile["source_feed"]["station_order_live"])
    if set(latest_displacement) != set(live_order):
        raise IssueProducerInputError("Latest displacement station set changed")
    if len(seed_predictions) != 5:
        raise IssueProducerInputError("Exactly five seed predictions are required")
    predictions: list[dict[str, float]] = []
    for seed in range(5):
        values = seed_predictions[seed]
        if isinstance(values, Mapping):
            if set(values) != set(model_order):
                raise IssueProducerInputError(
                    f"Seed {seed} prediction station names changed"
                )
            predictions.append(
                {
                    station: _finite(values[station], name=f"seed{seed}.{station}")
                    for station in model_order
                }
            )
        else:
            if len(values) != len(model_order):
                raise IssueProducerInputError(
                    f"Seed {seed} prediction station dimension changed"
                )
            predictions.append(
                {
                    station: _finite(value, name=f"seed{seed}.{station}")
                    for station, value in zip(model_order, values, strict=True)
                }
            )
    rows: list[dict[str, Any]] = []
    for station in live_order:
        persistence = _finite(
            latest_displacement[station], name=f"persistence.{station}"
        )
        rows.append(
            {
                "station": station,
                "persistence_mm": persistence,
                **{
                    f"seed{seed}_p50_mm": predictions[seed][station]
                    for seed in range(5)
                },
            }
        )
    return rows


def produce_issue(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    runtime_root: Path | None = None,
    project_root: Path = ROOT,
    clock: Clock | None = None,
    _bundle_loader: BundleLoader | None = None,
) -> ProducerResult:
    """Run one machine-only issue-production cycle.

    Source/model loading and checkpoint inference are wired below through the
    E2-B source and bundle modules.  Missing prerequisites are a normal waiting
    state; malformed present artifacts are a blocked-integrity state.
    """

    profile = load_config(config_path, project_root=project_root)
    root = _runtime_root(profile, runtime_root, project_root=project_root)
    observed_clock = _ObservedClock(clock or (lambda: datetime.now(timezone.utc)))
    now = observed_clock.sample()
    if _bundle_loader is not None and os.environ.get(TEST_EPOCH_ENV) != "1":
        raise IssueProducerConfigError(
            "Private bundle-loader injection is restricted to explicit test mode"
        )
    runtime = profile["runtime"]
    lock = _acquire_lock(required_path(root, runtime["deploy_lock"]))
    try:
        required = {
            "current_source": required_path(root, runtime["current_source_pointer"]),
            "activation_source_manifest": required_path(
                root, runtime["activation_source_manifest"]
            ),
            "five_seed_model_bundle": required_path(root, runtime["model_manifest"]),
        }
        missing = [name for name, path in required.items() if not path.is_file()]
        if missing:
            status_path = _write_status(
                profile,
                root,
                now=now,
                status="waiting_for_source_or_model",
                reason="missing:" + ",".join(missing),
            )
            return ProducerResult(
                status_path, "waiting_for_source_or_model", None, None
            )
        return _produce_locked(
            profile,
            root,
            project_root=project_root,
            clock=observed_clock.sample,
            bundle_loader=_bundle_loader,
        )
    except IssueProducerBusy:
        raise
    except IssueProducerError as exc:
        _write_status(
            profile,
            root,
            now=observed_clock.last or now,
            status="blocked_integrity",
            reason=f"{type(exc).__name__}:{exc}",
        )
        raise
    except Exception as exc:
        normalized = IssueProducerInputError(
            f"Validated source/model operation failed: {type(exc).__name__}:{exc}"
        )
        _write_status(
            profile,
            root,
            now=observed_clock.last or now,
            status="blocked_integrity",
            reason=f"{type(normalized).__name__}:{normalized}",
        )
        raise normalized from exc
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


def _produce_locked(
    profile: dict[str, Any],
    root: Path,
    *,
    project_root: Path,
    clock: Clock,
    bundle_loader: BundleLoader | None,
) -> ProducerResult:
    # Imported lazily so a missing runtime prerequisite can safely return a
    # waiting status without importing heavyweight torch/pandas code.
    from convlstm import ootang_production_bundle as bundle_module
    from monitoring import ootang_prequential_live as live_module
    from monitoring import ootang_live_source as source_module

    source = source_module.load_current_source(
        profile, runtime_root=root, project_root=project_root
    )
    # The fixed production model remains bound to the activation snapshot;
    # current source is allowed to advance as the prequential ledger advances.
    bundle = (
        bundle_module.load_deploy_bundle(profile, runtime_root=root)
        if bundle_loader is None
        else bundle_loader(profile, root)
    )
    live_profile_path = _resolve(
        profile["live_profile"]["path"], project_root=project_root
    )
    live_profile = live_module.load_config(live_profile_path)
    live_paths = live_module.runtime_paths(live_profile, runtime_root=root)
    prerequisites = live_module.load_prerequisites(live_profile, live_paths)
    if prerequisites is None:
        raise IssueProducerInputError("E2-A activation prerequisites disappeared")

    try:
        runner_lock = _acquire_lock(live_paths.lock)
    except IssueProducerBusy as exc:
        raise IssueProducerBusy("Another E2-A ledger cycle is running") from exc
    try:
        return _produce_with_stable_ledger(
            profile,
            root,
            clock=clock,
            source=source,
            bundle=bundle,
            bundle_module=bundle_module,
            live_module=live_module,
            live_profile=live_profile,
            live_paths=live_paths,
            prerequisites=prerequisites,
        )
    finally:
        fcntl.flock(runner_lock.fileno(), fcntl.LOCK_UN)
        runner_lock.close()


def _produce_with_stable_ledger(
    profile: dict[str, Any],
    root: Path,
    *,
    clock: Clock,
    source: object,
    bundle: object,
    bundle_module: object,
    live_module: object,
    live_profile: dict[str, Any],
    live_paths: object,
    prerequisites: object,
) -> ProducerResult:
    preflight_now = clock()
    try:
        authority = _target_authority(
            source_watermark=source.watermark,
            activation_watermark=prerequisites.source.watermark,
            ledger_path=live_paths.ledger,
            live_status_path=live_paths.status,
            live_profile=live_profile,
            live_paths=live_paths,
            prerequisites=prerequisites,
        )
        target, _ = _target_and_times(
            authority.target_date,
            source.exported_at_utc,
            now=preflight_now,
            timezone_name=profile["source_feed"]["date_timezone"],
        )
        rows = _select_model_rows(source, profile)
        latest_displacement = _validate_authoritative_latest_displacement(
            rows,
            profile,
            authority.latest_displacement_mm,
        )
    except (_MachineWaiting, _NoIssuableTarget) as exc:
        status_path = _write_status(
            profile,
            root,
            now=preflight_now,
            status="waiting_for_issuable_future_target",
            reason=str(exc),
            target=source.watermark + timedelta(days=1),
        )
        return ProducerResult(
            status_path,
            "waiting_for_issuable_future_target",
            source.watermark + timedelta(days=1),
            None,
        )

    seed_predictions = bundle_module.predict_p50(bundle, rows, profile=profile)
    stations = _station_rows(
        profile,
        latest_displacement=latest_displacement,
        seed_predictions=seed_predictions,
    )
    input_payload = _build_input_manifest(
        profile=profile,
        target=target,
        source=source,
        bundle=bundle,
        model_rows=rows,
        stations=stations,
    )
    if (
        input_payload.get("schema_version")
        != profile["issue"]["input_manifest_schema_version"]
    ):
        raise IssueProducerInputError("Issue input manifest schema changed")
    input_manifest = _materialize_content_addressed_json(root, profile, input_payload)

    if prerequisites.source.path != required_path(
        root, profile["runtime"]["activation_source_manifest"]
    ):
        raise IssueProducerInputError("E2-A activation source path changed")
    if prerequisites.model.path != required_path(
        root, profile["runtime"]["model_manifest"]
    ):
        raise IssueProducerInputError("E2-A model manifest path changed")
    generated_now = clock()
    target, generated_at = _target_and_times(
        target,
        source.exported_at_utc,
        now=generated_now,
        timezone_name=profile["source_feed"]["date_timezone"],
    )
    issue_payload = {
        "schema_version": profile["issue"]["schema_version"],
        "target_date": target.isoformat(),
        "generated_at_utc": generated_at,
        "source_as_of_at_utc": source.exported_at_utc,
        "source_snapshot_sha256": prerequisites.source.sha256,
        "model_manifest_sha256": prerequisites.model.sha256,
        "input_manifest": _artifact_payload(input_manifest),
        "stations": stations,
    }
    issue_path = required_path(
        root,
        str(Path(profile["runtime"]["issue_inbox"]) / f"{target.isoformat()}.json"),
    )
    receipt_root = required_path(root, profile["runtime"]["issue_receipts"])
    object_root = required_path(root, profile["runtime"]["objects"])
    receipt_path = _issue_receipt_path(receipt_root, target.isoformat())
    if not receipt_path.exists():
        # Exercise the exact E2-A consumer contract before the receipt commit
        # point.  The content-addressed candidate may remain orphaned on failure;
        # no consumer-facing inbox or producer receipt is created.
        candidate_object = _materialize_exact_issue_object(
            object_root, _canonical_bytes(issue_payload)
        )
        candidate = live_module.load_issue_batch(
            candidate_object.path, live_profile, prerequisites
        )
        if candidate.target_date != target:
            raise IssueProducerInputError(
                "Candidate issue failed consumer target validation"
            )
    # The consumer preflight itself may be long.  Sample again immediately
    # before the receipt/inbox commit and never carry the generation sample
    # through that operation as if time had stood still.
    publish_now = clock()
    _target_and_times(
        target,
        source.exported_at_utc,
        now=publish_now,
        timezone_name=profile["source_feed"]["date_timezone"],
    )
    if publish_now.astimezone(timezone.utc) < generated_now.astimezone(timezone.utc):
        raise IssueProducerInputError(
            "Machine clock moved backwards before issue publication"
        )
    created = _publish_issue_idempotently(
        issue_path,
        issue_payload,
        receipt_root=receipt_root,
        object_root=object_root,
    )
    try:
        # Parse the exact durable bytes through the consumer contract before
        # advertising a successful issue status.
        parsed = live_module.load_issue_batch(issue_path, live_profile, prerequisites)
        if parsed.target_date != target:
            raise IssueProducerInputError(
                "Durable issue failed consumer target validation"
            )
        durable_input_manifest = _coerce_artifact(
            parsed.input_manifest, name="durable issue input manifest"
        )
        issue_receipt, exact_issue_object = _registered_issue_artifacts(
            issue_path=issue_path,
            receipt_root=receipt_root,
            object_root=object_root,
        )
        completion_now = clock()
        try:
            _target_and_times(
                target,
                source.exported_at_utc,
                now=completion_now,
                timezone_name=profile["source_feed"]["date_timezone"],
            )
            if completion_now.astimezone(timezone.utc) < publish_now.astimezone(
                timezone.utc
            ):
                raise IssueProducerInputError(
                    "Machine clock moved backwards during issue publication"
                )
        except (_NoIssuableTarget, IssueProducerInputError) as exc:
            raise IssueProducerInputError(
                "Issue publication did not complete before target start"
            ) from exc
    except Exception:
        if created:
            _withdraw_registered_issue(
                issue_path=issue_path,
                receipt_root=receipt_root,
                object_root=object_root,
            )
        raise
    status = "issued" if created else "already_issued_idempotent"
    status_path = _write_status(
        profile,
        root,
        now=completion_now,
        status=status,
        reason=(
            "atomic_issue_batch_created"
            if created
            else "same_target_same_semantics_preserved_first_bytes"
        ),
        target=target,
        issue_path=issue_path,
        input_manifest=durable_input_manifest,
        issue_receipt=issue_receipt,
        exact_issue_object=exact_issue_object,
    )
    return ProducerResult(status_path, status, target, issue_path)


def required_path(root: Path, relative: str) -> Path:
    """Resolve a configured runtime-relative path without accepting traversal."""

    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise IssueProducerConfigError("Runtime path escapes the runtime root") from exc
    return path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = produce_issue(config_path=args.config)
    except IssueProducerBusy as exc:
        print(f"[ootang-issue-producer] busy: {exc}", file=sys.stderr)
        return 3
    except IssueProducerError as exc:
        print(f"[ootang-issue-producer] blocked: {exc}", file=sys.stderr)
        return 2
    print(
        f"[ootang-issue-producer] status={result.status} "
        f"target={result.target_date or 'none'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "Artifact",
    "DEFAULT_CONFIG_PATH",
    "IssueProducerBusy",
    "IssueProducerConfigError",
    "IssueProducerConflict",
    "IssueProducerError",
    "IssueProducerInputError",
    "ProducerResult",
    "load_config",
    "main",
    "produce_issue",
]
