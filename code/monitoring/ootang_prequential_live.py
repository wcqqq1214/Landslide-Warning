"""Machine-poll E2-A prequential runner with separated issue/outcome feeds.

This module implements engineering infrastructure only.  It cannot create
future forecasts from the historical OOF CSV, replay checkpoint inference, or
cryptographically verify a trusted time provider.  Therefore v1 never promotes
its machine-ordering candidates to live E2 evidence; those gates require a new
deployment/activation version.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields, replace
from datetime import date, datetime, timedelta, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import sqlite3
import stat
import sys
import tempfile
from types import MappingProxyType
from typing import Any, Callable, Iterable
from urllib import error as urlerror
from urllib import request as urlrequest
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring.ootang_live_ledger import (  # noqa: E402
    AppendOnlyLedger,
    EventSpec,
    LedgerError,
    LedgerEvent,
)
from monitoring.prequential_core import (  # noqa: E402
    SiteAggregateV1,
    StationIssueV1,
    StationStateV1,
    aggregate_site_scores,
    decode_station_state_v1,
    encode_station_state_v1,
    issue_station,
    new_station_state_v1,
    reveal_station,
    station_state_sha256_v1,
)

DEFAULT_CONFIG_PATH = ROOT / "config" / "ootang_prequential_live.v1.json"
DEFAULT_CONFIG_SHA256 = (
    "bf7c60a19e26e9a54fc4e1980b3556d6e6d1e3fec4b3a3a7f3de0dbb9b83cf00"
)
ZERO_HASH = "0" * 64
HEX_DIGITS = frozenset("0123456789abcdef")


class LiveConfigError(ValueError):
    """Raised when the E2-A profile is missing or has changed."""


class LivePrerequisiteError(RuntimeError):
    """Raised when a present activation prerequisite is invalid."""


class LiveInputError(RuntimeError):
    """Raised when a live issue or outcome batch violates its contract."""


class LiveIntegrityError(RuntimeError):
    """Raised when durable live state no longer matches its hash contracts."""


class LiveRunnerBusy(RuntimeError):
    """Raised when another machine poll already owns the runtime lock."""


@dataclass(frozen=True)
class ArtifactRecord:
    path: Path
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class SourceSnapshot:
    path: Path
    sha256: str
    captured_at_utc: str
    watermark: date
    outcome_source_id: str
    latest_displacement_mm: dict[str, float]
    data_manifest: ArtifactRecord


@dataclass(frozen=True)
class ModelBundle:
    path: Path
    sha256: str
    model_version: str
    created_at_utc: str
    training_cutoff: date
    input_schema_sha256: str
    training_manifest: ArtifactRecord
    checkpoints: tuple[ArtifactRecord, ...]


@dataclass(frozen=True)
class IssueBatch:
    path: Path
    sha256: str
    target_date: date
    generated_at_utc: str
    source_as_of_at_utc: str
    source_snapshot_sha256: str
    model_manifest_sha256: str
    input_manifest: ArtifactRecord
    experts_by_station: dict[str, tuple[float, ...]]


@dataclass(frozen=True)
class OutcomeBatch:
    path: Path
    sha256: str
    target_date: date
    outcome_source_id: str
    source_revision_id: str
    source_observed_at_utc: str
    source_available_at_utc: str
    finalized_at_utc: str
    source_manifest: ArtifactRecord
    actual_by_station: dict[str, float]


@dataclass(frozen=True)
class RuntimePaths:
    root: Path
    ledger: Path
    status: Path
    lock: Path
    issue_inbox: Path
    outcome_inbox: Path
    anchors: Path


@dataclass(frozen=True)
class Prerequisites:
    source: SourceSnapshot
    model: ModelBundle
    algorithm_profile: dict[str, Any]
    algorithm_profile_sha256: str
    implementation_sha256: str
    environment_sha256: str
    environment_record: dict[str, Any]


@dataclass
class LiveProjection:
    """Scientific state reconstructed solely from the verified event ledger."""

    epoch_id: str
    states: dict[str, StationStateV1]
    last_finalized_date: date
    latest_displacement_mm: dict[str, float]
    outstanding_target_date: date | None
    outstanding_issue_id: str | None
    issue_events: dict[str, LedgerEvent]
    seal_event: LedgerEvent | None
    anchored_seal_hashes: dict[str, dict[str, Any]]
    settled_events: dict[str, LedgerEvent]
    backfill_events: dict[str, LedgerEvent]
    revision_ids: dict[str, dict[str, str]]
    latest_actuals_by_date: dict[str, dict[str, float]]
    blind_settled_count: int
    engineering_blind_candidate_count: int
    backfill_count: int


@dataclass(frozen=True)
class VerifiedLedgerProjection:
    """Deeply read-only scientific replay of one existing verified ledger."""

    epoch_id: str
    states: Mapping[str, StationStateV1]
    last_finalized_date: date
    latest_displacement_mm: Mapping[str, float]
    outstanding_target_date: date | None
    outstanding_issue_id: str | None
    issue_events: Mapping[str, LedgerEvent]
    seal_event: LedgerEvent | None
    anchored_seal_hashes: Mapping[str, Mapping[str, Any]]
    settled_events: Mapping[str, LedgerEvent]
    backfill_events: Mapping[str, LedgerEvent]
    revision_ids: Mapping[str, Mapping[str, str]]
    latest_actuals_by_date: Mapping[str, Mapping[str, float]]
    blind_settled_count: int
    engineering_blind_candidate_count: int
    backfill_count: int
    ledger_events: tuple[LedgerEvent, ...]
    ledger_event_count: int
    ledger_terminal_sequence_id: int
    ledger_terminal_sha256: str


AnchorClient = Callable[[str, dict[str, Any]], dict[str, Any]]
Clock = Callable[[], datetime]


def _reject_json_constant(value: str) -> None:
    raise LiveConfigError(f"Forbidden JSON constant: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LiveConfigError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _decode_json(raw: bytes, *, path: Path, name: str) -> dict[str, Any]:
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiveConfigError(f"{name} is not strict UTF-8 JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise LiveConfigError(f"{name} must be a JSON object")
    return payload


def _read_file_snapshot(path: Path) -> bytes:
    # Reading through one open descriptor binds parsing and hashing to the same
    # inode even if a producer atomically replaces the pathname concurrently.
    with path.open("rb") as handle:
        return handle.read()


def _load_json_snapshot(path: Path, *, name: str) -> tuple[dict[str, Any], str]:
    raw = _read_file_snapshot(path)
    return _decode_json(raw, path=path, name=name), hashlib.sha256(raw).hexdigest()


def _load_json(path: Path, *, name: str) -> dict[str, Any]:
    payload, _ = _load_json_snapshot(path, name=name)
    return payload


def _sha256_file_and_size(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _sha256_file(path: Path) -> str:
    sha256, _ = _sha256_file_and_size(path)
    return sha256


def _canonical_json(payload: Any) -> str:
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise LiveIntegrityError("Payload is not canonical finite JSON") from exc


def _canonical_sha256(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _require_exact_keys(value: object, expected: set[str], *, name: str) -> dict:
    if not isinstance(value, dict):
        raise LiveInputError(f"{name} must be an object")
    keys = set(value)
    if keys != expected:
        missing = sorted(expected - keys)
        extra = sorted(keys - expected)
        raise LiveInputError(f"{name} keys changed; missing={missing}, extra={extra}")
    return value


def _require_sha256(value: object, *, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in HEX_DIGITS for character in value)
    ):
        raise LiveInputError(f"{name} must be a lowercase SHA-256")
    return value


def _require_nonempty_string(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise LiveInputError(f"{name} must be a nonblank trimmed string")
    return value


def _require_finite_float(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LiveInputError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise LiveInputError(f"{name} must be finite")
    return result


def _parse_date(value: object, *, name: str) -> date:
    if not isinstance(value, str):
        raise LiveInputError(f"{name} must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise LiveInputError(f"{name} must be YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise LiveInputError(f"{name} must use canonical YYYY-MM-DD")
    return parsed


def _parse_utc(value: object, *, name: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise LiveInputError(f"{name} must be canonical RFC 3339 UTC ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise LiveInputError(f"{name} is not a valid UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise LiveInputError(f"{name} must be UTC")
    return parsed


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise LiveIntegrityError("Machine clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _resolve(path: str | Path, *, base: Path = ROOT) -> Path:
    result = Path(path)
    return (base / result).resolve() if not result.is_absolute() else result.resolve()


def _artifact_record(value: object, *, name: str, base: Path = ROOT) -> ArtifactRecord:
    record = _require_exact_keys(
        value, {"path", "sha256", "size_bytes"}, name=name
    )
    raw_path = _require_nonempty_string(record["path"], name=f"{name}.path")
    path = _resolve(raw_path, base=base)
    sha256 = _require_sha256(record["sha256"], name=f"{name}.sha256")
    size = record["size_bytes"]
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise LiveInputError(f"{name}.size_bytes must be a nonnegative integer")
    if not path.is_file():
        raise LiveInputError(f"{name} does not exist: {path}")
    actual_sha256, actual_size = _sha256_file_and_size(path)
    if actual_size != size:
        raise LiveInputError(f"{name} size does not match")
    if actual_sha256 != sha256:
        raise LiveInputError(f"{name} SHA-256 does not match")
    return ArtifactRecord(path=path, sha256=sha256, size_bytes=size)


def _walk_keys(value: object) -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    path = path.resolve()
    profile, file_sha256 = _load_json_snapshot(path, name="E2-A live profile")
    if file_sha256 != DEFAULT_CONFIG_SHA256:
        raise LiveConfigError(
            "E2-A profile differs from the reviewed v1 file; create a new version"
        )
    if profile.get("schema_version") != "ootang_prequential_live_profile_v1":
        raise LiveConfigError("E2-A profile schema changed")
    if profile.get("profile_id") != "ootang-prequential-live-v1":
        raise LiveConfigError("E2-A profile id changed")
    if profile.get("artifact_status") != "e2a_engineering_only_not_live_evidence":
        raise LiveConfigError("E2-A artifact status changed")
    for flag in (
        "formal_warning_output",
        "independent_label_used",
        "confirmatory_external_validation",
        "vajont_used",
        "default_pipeline_member",
    ):
        if profile.get(flag) is not False:
            raise LiveConfigError(f"E2-A flag {flag} must remain false")
    capabilities = profile.get("engineering_capabilities")
    expected_capabilities = {
        "issue_predictions_source": "external_precomputed_feed_not_replayed",
        "input_manifest_semantics_verified": False,
        "checkpoint_inference_replayed": False,
        "trusted_anchor_receipt_verified": False,
        "automatic_epoch_rotation_implemented": False,
        "prerequisite_change_behavior": (
            "fail_closed_pending_machine_epoch_manager"
        ),
        "real_activation_ready": False,
    }
    if capabilities != expected_capabilities:
        raise LiveConfigError("E2-A engineering capability boundary changed")
    return profile


def runtime_paths(
    profile: dict[str, Any], *, runtime_root: Path | None = None
) -> RuntimePaths:
    contract = profile["runtime"]
    root = (
        runtime_root.resolve()
        if runtime_root is not None
        else _resolve(contract["root"])
    )
    return RuntimePaths(
        root=root,
        ledger=root / contract["ledger"],
        status=root / contract["status"],
        lock=root / contract["lock"],
        issue_inbox=root / contract["issue_inbox"],
        outcome_inbox=root / contract["outcome_inbox"],
        anchors=root / contract["anchor_receipts"],
    )


def _environment_record() -> tuple[dict[str, Any], str]:
    project = ROOT / "pyproject.toml"
    lock = ROOT / "uv.lock"
    record = {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "project_sha256": _sha256_file(project),
        "dependency_lock_sha256": _sha256_file(lock),
    }
    return record, _canonical_sha256(record)


def _load_algorithm_profile(profile: dict[str, Any]) -> tuple[dict[str, Any], str]:
    contract = profile["algorithm_profile"]
    path = _resolve(contract["path"])
    algorithm_profile, file_sha256 = _load_json_snapshot(
        path, name="E1 algorithm profile"
    )
    if file_sha256 != contract["expected_file_sha256"]:
        raise LivePrerequisiteError("E1 algorithm profile file SHA-256 changed")
    content_sha256 = _canonical_sha256(algorithm_profile)
    if content_sha256 != contract["expected_content_sha256"]:
        raise LivePrerequisiteError("E1 algorithm profile content SHA-256 changed")
    return algorithm_profile, content_sha256


def _validate_prior_evidence(profile: dict[str, Any]) -> None:
    contract = profile["prior_evidence"]
    path = _resolve(contract["e1_manifest"])
    manifest, file_sha256 = _load_json_snapshot(path, name="E1 evidence manifest")
    if file_sha256 != contract["expected_e1_manifest_sha256"]:
        raise LivePrerequisiteError("E1 evidence manifest SHA-256 changed")
    terminal = manifest.get("issue_hash_chain", {}).get("terminal_hash")
    if terminal != contract["expected_e1_issue_terminal_sha256"]:
        raise LivePrerequisiteError("E1 issue terminal hash changed")
    if contract["chain_policy"] != "reference_only_new_namespace_and_zero_hash_genesis":
        raise LivePrerequisiteError("E2 chain separation policy changed")


def load_source_snapshot(path: Path, profile: dict[str, Any]) -> SourceSnapshot:
    payload, file_sha256 = _load_json_snapshot(path, name="live source snapshot")
    record = _require_exact_keys(
        payload,
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
        name="live source snapshot",
    )
    activation = profile["activation"]
    if record["schema_version"] != activation["source_snapshot_schema_version"]:
        raise LiveInputError("Live source snapshot schema changed")
    if record["case"] != "ootang":
        raise LiveInputError("Live source snapshot must be Ootang-only")
    stations = profile["stations"]
    if record["stations"] != stations:
        raise LiveInputError("Live source snapshot stations or order changed")
    captured = _require_nonempty_string(
        record["captured_at_utc"], name="source.captured_at_utc"
    )
    _parse_utc(captured, name="source.captured_at_utc")
    watermark = _parse_date(
        record["maximum_complete_finalized_date"],
        name="source.maximum_complete_finalized_date",
    )
    minimum = date.fromisoformat(activation["historical_minimum_watermark_exclusive"])
    if watermark <= minimum:
        raise LiveInputError(
            "Live source watermark must advance beyond the historical evidence"
        )
    source_id = _require_nonempty_string(
        record["outcome_source_id"], name="source.outcome_source_id"
    )
    displacement = _require_exact_keys(
        record["latest_finalized_displacement_mm"],
        set(stations),
        name="source.latest_finalized_displacement_mm",
    )
    latest = {
        station: _require_finite_float(
            displacement[station],
            name=f"source.latest_finalized_displacement_mm.{station}",
        )
        for station in stations
    }
    data_manifest = _artifact_record(
        record["data_manifest"], name="source.data_manifest"
    )
    return SourceSnapshot(
        path=path.resolve(),
        sha256=file_sha256,
        captured_at_utc=captured,
        watermark=watermark,
        outcome_source_id=source_id,
        latest_displacement_mm=latest,
        data_manifest=data_manifest,
    )


def load_model_bundle(
    path: Path, profile: dict[str, Any], source: SourceSnapshot
) -> ModelBundle:
    payload, file_sha256 = _load_json_snapshot(
        path, name="five-seed production model bundle"
    )
    record = _require_exact_keys(
        payload,
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
        name="five-seed production model bundle",
    )
    contract = profile["production_model"]
    if record["schema_version"] != contract["schema_version"]:
        raise LiveInputError("Production model bundle schema changed")
    if record["case"] != "ootang" or record["stations"] != profile["stations"]:
        raise LiveInputError("Production model bundle scope changed")
    if record["seeds"] != contract["required_seeds"]:
        raise LiveInputError("Production model seeds changed")
    if record["best_seed_selected"] is not False:
        raise LiveInputError("Production bundle must not select a best seed")
    target = _require_exact_keys(
        record["target"], {"name", "unit", "horizon"}, name="model.target"
    )
    expected_target = profile["target"]
    if target != {
        "name": expected_target["name"],
        "unit": expected_target["unit"],
        "horizon": expected_target["horizon"],
    }:
        raise LiveInputError("Production model target changed")
    cutoff = _parse_date(record["training_cutoff_date"], name="model.training_cutoff")
    if cutoff != source.watermark:
        raise LiveInputError("Production model cutoff must equal the genesis watermark")
    created = _require_nonempty_string(
        record["created_at_utc"], name="model.created_at_utc"
    )
    _parse_utc(created, name="model.created_at_utc")
    model_version = _require_nonempty_string(
        record["model_version"], name="model.model_version"
    )
    input_schema_sha256 = _require_sha256(
        record["input_schema_sha256"], name="model.input_schema_sha256"
    )
    training_manifest = _artifact_record(
        record["training_manifest"], name="model.training_manifest"
    )
    checkpoints_value = record["checkpoints"]
    if not isinstance(checkpoints_value, list) or len(checkpoints_value) != 5:
        raise LiveInputError("Production model must declare five checkpoints")
    checkpoints: list[ArtifactRecord] = []
    seen_seeds: list[int] = []
    for index, value in enumerate(checkpoints_value):
        item = _require_exact_keys(
            value, {"seed", "artifact"}, name=f"model.checkpoints[{index}]"
        )
        seed = item["seed"]
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise LiveInputError("Checkpoint seed must be an integer")
        seen_seeds.append(seed)
        checkpoints.append(
            _artifact_record(
                item["artifact"], name=f"model.checkpoints[{index}].artifact"
            )
        )
    if seen_seeds != contract["required_seeds"]:
        raise LiveInputError("Checkpoint seeds or order changed")
    return ModelBundle(
        path=path.resolve(),
        sha256=file_sha256,
        model_version=model_version,
        created_at_utc=created,
        training_cutoff=cutoff,
        input_schema_sha256=input_schema_sha256,
        training_manifest=training_manifest,
        checkpoints=tuple(checkpoints),
    )


def load_prerequisites(
    profile: dict[str, Any], paths: RuntimePaths
) -> Prerequisites | None:
    source_path = _resolve(profile["activation"]["source_snapshot_manifest"])
    model_path = _resolve(profile["production_model"]["manifest"])
    if paths.root != _resolve(profile["runtime"]["root"]):
        source_path = paths.root / "source_snapshot" / "manifest.json"
        model_path = paths.root / "model_bundle" / "manifest.json"
    if not source_path.is_file() or not model_path.is_file():
        return None
    _validate_prior_evidence(profile)
    algorithm_profile, algorithm_sha256 = _load_algorithm_profile(profile)
    source = load_source_snapshot(source_path, profile)
    model = load_model_bundle(model_path, profile, source)
    environment_record, environment_sha256 = _environment_record()
    implementation_sha256 = _runner_code_sha256()
    return Prerequisites(
        source=source,
        model=model,
        algorithm_profile=algorithm_profile,
        algorithm_profile_sha256=algorithm_sha256,
        implementation_sha256=implementation_sha256,
        environment_sha256=environment_sha256,
        environment_record=environment_record,
    )


def load_issue_batch(
    path: Path,
    profile: dict[str, Any],
    prerequisites: Prerequisites,
) -> IssueBatch:
    payload, file_sha256 = _load_json_snapshot(path, name="live issue batch")
    forbidden = {value.casefold() for value in profile["issue_feed"]["forbidden_fields"]}
    present_forbidden = sorted(
        {key for key in _walk_keys(payload) if key.casefold() in forbidden}
    )
    if present_forbidden:
        raise LiveInputError(f"Issue batch contains forbidden fields: {present_forbidden}")
    record = _require_exact_keys(
        payload,
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
        name="live issue batch",
    )
    if record["schema_version"] != profile["issue_feed"]["schema_version"]:
        raise LiveInputError("Issue batch schema changed")
    target = _parse_date(record["target_date"], name="issue.target_date")
    generated = _require_nonempty_string(
        record["generated_at_utc"], name="issue.generated_at_utc"
    )
    source_as_of = _require_nonempty_string(
        record["source_as_of_at_utc"], name="issue.source_as_of_at_utc"
    )
    generated_dt = _parse_utc(generated, name="issue.generated_at_utc")
    source_as_of_dt = _parse_utc(source_as_of, name="issue.source_as_of_at_utc")
    if source_as_of_dt > generated_dt:
        raise LiveInputError("Issue source_as_of_at_utc cannot follow generation")
    model_created_dt = _parse_utc(
        prerequisites.model.created_at_utc, name="model.created_at_utc"
    )
    if generated_dt < model_created_dt:
        raise LiveInputError("Issue generation predates the production model bundle")
    target_start = datetime.combine(
        target, datetime.min.time(), tzinfo=ZoneInfo(profile["target"]["date_timezone"])
    ).astimezone(timezone.utc)
    if source_as_of_dt >= target_start or generated_dt >= target_start:
        raise LiveInputError("Issue timestamps must predate the target date")
    source_sha = _require_sha256(
        record["source_snapshot_sha256"], name="issue.source_snapshot_sha256"
    )
    model_sha = _require_sha256(
        record["model_manifest_sha256"], name="issue.model_manifest_sha256"
    )
    if source_sha != prerequisites.source.sha256:
        raise LiveInputError("Issue source snapshot does not match the live epoch")
    if model_sha != prerequisites.model.sha256:
        raise LiveInputError("Issue model manifest does not match the live epoch")
    input_manifest = _artifact_record(
        record["input_manifest"], name="issue.input_manifest"
    )
    station_values = record["stations"]
    if not isinstance(station_values, list) or len(station_values) != 8:
        raise LiveInputError("Issue batch must contain exactly eight stations")
    expert_names = profile["algorithm_profile"]["expert_columns"]
    station_keys = {"station", *[f"{name}_mm" for name in expert_names]}
    experts: dict[str, tuple[float, ...]] = {}
    for index, value in enumerate(station_values):
        item = _require_exact_keys(value, station_keys, name=f"issue.stations[{index}]")
        station = _require_nonempty_string(
            item["station"], name=f"issue.stations[{index}].station"
        )
        if station in experts:
            raise LiveInputError(f"Duplicate issue station: {station}")
        experts[station] = tuple(
            _require_finite_float(
                item[f"{name}_mm"], name=f"issue.{station}.{name}_mm"
            )
            for name in expert_names
        )
    if list(experts) != profile["stations"]:
        raise LiveInputError("Issue stations or order changed")
    return IssueBatch(
        path=path.resolve(),
        sha256=file_sha256,
        target_date=target,
        generated_at_utc=generated,
        source_as_of_at_utc=source_as_of,
        source_snapshot_sha256=source_sha,
        model_manifest_sha256=model_sha,
        input_manifest=input_manifest,
        experts_by_station=experts,
    )


def load_outcome_batch(
    path: Path,
    profile: dict[str, Any],
    prerequisites: Prerequisites,
) -> OutcomeBatch:
    payload, file_sha256 = _load_json_snapshot(path, name="live outcome batch")
    record = _require_exact_keys(
        payload,
        {
            "schema_version",
            "target_date",
            "outcome_source_id",
            "source_revision_id",
            "source_observed_at_utc",
            "source_available_at_utc",
            "finalized_at_utc",
            "finalized",
            "source_manifest",
            "stations",
        },
        name="live outcome batch",
    )
    if record["schema_version"] != profile["outcome_feed"]["schema_version"]:
        raise LiveInputError("Outcome batch schema changed")
    if record["finalized"] is not True:
        raise LiveInputError("Outcome batch must be finalized before reveal")
    target = _parse_date(record["target_date"], name="outcome.target_date")
    source_id = _require_nonempty_string(
        record["outcome_source_id"], name="outcome.outcome_source_id"
    )
    if source_id != prerequisites.source.outcome_source_id:
        raise LiveInputError("Outcome source changed within the live epoch")
    revision = _require_nonempty_string(
        record["source_revision_id"], name="outcome.source_revision_id"
    )
    observed = _require_nonempty_string(
        record["source_observed_at_utc"], name="outcome.source_observed_at_utc"
    )
    available = _require_nonempty_string(
        record["source_available_at_utc"], name="outcome.source_available_at_utc"
    )
    finalized = _require_nonempty_string(
        record["finalized_at_utc"], name="outcome.finalized_at_utc"
    )
    observed_dt = _parse_utc(observed, name="outcome.source_observed_at_utc")
    available_dt = _parse_utc(available, name="outcome.source_available_at_utc")
    finalized_dt = _parse_utc(finalized, name="outcome.finalized_at_utc")
    if available_dt < observed_dt or finalized_dt < available_dt:
        raise LiveInputError("Outcome timestamps are not monotone")
    target_start = datetime.combine(
        target,
        datetime.min.time(),
        tzinfo=ZoneInfo(profile["target"]["date_timezone"]),
    ).astimezone(timezone.utc)
    if observed_dt < target_start:
        raise LiveInputError("Outcome observation predates the target natural day")
    source_manifest = _artifact_record(
        record["source_manifest"], name="outcome.source_manifest"
    )
    station_values = record["stations"]
    if not isinstance(station_values, list) or len(station_values) != 8:
        raise LiveInputError("Outcome batch must contain exactly eight stations")
    actuals: dict[str, float] = {}
    for index, value in enumerate(station_values):
        item = _require_exact_keys(
            value, {"station", "actual_mm"}, name=f"outcome.stations[{index}]"
        )
        station = _require_nonempty_string(
            item["station"], name=f"outcome.stations[{index}].station"
        )
        if station in actuals:
            raise LiveInputError(f"Duplicate outcome station: {station}")
        actuals[station] = _require_finite_float(
            item["actual_mm"], name=f"outcome.{station}.actual_mm"
        )
    if list(actuals) != profile["stations"]:
        raise LiveInputError("Outcome stations or order changed")
    return OutcomeBatch(
        path=path.resolve(),
        sha256=file_sha256,
        target_date=target,
        outcome_source_id=source_id,
        source_revision_id=revision,
        source_observed_at_utc=observed,
        source_available_at_utc=available,
        finalized_at_utc=finalized,
        source_manifest=source_manifest,
        actual_by_station=actuals,
    )


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(
        payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
    ) + "\n"
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            handle.write(serialized)
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


def _base_status(
    profile: dict[str, Any],
    paths: RuntimePaths,
    *,
    now: datetime,
    runner_status: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "schema_version": "ootang_prequential_live_status_v1",
        "profile_id": profile["profile_id"],
        "profile_file_sha256": DEFAULT_CONFIG_SHA256,
        "artifact_status": profile["artifact_status"],
        "runner_status": runner_status,
        "reason": reason,
        "recorded_at_utc": _format_utc(now),
        "runtime_root": str(paths.root),
        "ledger_path": str(paths.ledger),
        "ledger_event_count": 0,
        "ledger_terminal_sha256": ZERO_HASH,
        "production_model_ready": False,
        "source_snapshot_ready": False,
        "external_anchor_confirmed": False,
        "e2_live_evidence_accumulated": False,
        "formal_warning_output": False,
        "independent_label_used": False,
        "confirmatory_external_validation": False,
        "vajont_used": False,
        **profile["engineering_capabilities"],
    }


def _write_waiting_for_prerequisites(
    profile: dict[str, Any], paths: RuntimePaths, *, now: datetime
) -> Path:
    source_path = paths.root / "source_snapshot" / "manifest.json"
    model_path = paths.root / "model_bundle" / "manifest.json"
    missing = [
        name
        for name, path in (
            ("source_snapshot", source_path),
            ("five_seed_production_model_bundle", model_path),
        )
        if not path.is_file()
    ]
    status = _base_status(
        profile,
        paths,
        now=now,
        runner_status=profile["runtime"]["missing_prerequisite_status"],
        reason="missing:" + ",".join(missing),
    )
    status["source_snapshot_ready"] = source_path.is_file()
    status["production_model_ready"] = model_path.is_file()
    status["missing_prerequisites"] = missing
    _atomic_write_json(paths.status, status)
    return paths.status


def _default_anchor_client(endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
    token = os.environ.get("OOTANG_TIME_ANCHOR_TOKEN")
    if not token:
        raise LivePrerequisiteError("Time-anchor token environment variable is missing")
    body = _canonical_json(payload).encode("utf-8")
    request = urlrequest.Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlrequest.urlopen(request, timeout=10) as response:
            response_payload = response.read()
    except (OSError, urlerror.URLError) as exc:
        raise LivePrerequisiteError("Independent time-anchor request failed") from exc
    try:
        result = json.loads(response_payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LivePrerequisiteError("Time-anchor response is not strict JSON") from exc
    if not isinstance(result, dict):
        raise LivePrerequisiteError("Time-anchor response must be an object")
    return result


def _ensure_runtime_directories(paths: RuntimePaths) -> None:
    for path in (paths.root, paths.issue_inbox, paths.outcome_inbox, paths.anchors):
        path.mkdir(parents=True, exist_ok=True)


def _acquire_runner_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise LiveRunnerBusy("Another E2-A machine poll is already running") from exc
    return handle


def _state_payload(state: StationStateV1) -> dict[str, Any]:
    return json.loads(encode_station_state_v1(state).decode("utf-8"))


def _states_payload(
    states: dict[str, StationStateV1], stations: list[str]
) -> dict[str, dict[str, Any]]:
    return {station: _state_payload(states[station]) for station in stations}


def _states_sha256(states: dict[str, StationStateV1], stations: list[str]) -> str:
    return _canonical_sha256(_states_payload(states, stations))


def _decode_states(
    value: object, stations: list[str], *, name: str
) -> dict[str, StationStateV1]:
    mapping = _require_exact_keys(value, set(stations), name=name)
    result: dict[str, StationStateV1] = {}
    for station in stations:
        try:
            encoded = _canonical_json(mapping[station]).encode("utf-8")
            result[station] = decode_station_state_v1(encoded)
        except Exception as exc:
            raise LiveIntegrityError(f"{name}.{station} is not a valid v1 state") from exc
    return result


def _issue_payload(issue: StationIssueV1) -> dict[str, Any]:
    return asdict(issue)


def _decode_issue(value: object, *, name: str) -> StationIssueV1:
    # ``__dataclass_fields__`` also contains the ClassVar ``core_version``;
    # ``asdict`` correctly omits it, so replay must use only instance fields.
    expected = {field.name for field in fields(StationIssueV1)}
    payload = _require_exact_keys(value, expected, name=name)
    try:
        return StationIssueV1(
            state_before_sha256=str(payload["state_before_sha256"]),
            state_reset_reason=str(payload["state_reset_reason"]),
            history_count=int(payload["history_count"]),
            forecast_action=str(payload["forecast_action"]),
            uncertainty_action=str(payload["uncertainty_action"]),
            eta=float(payload["eta"]),
            experts_mm=tuple(float(value) for value in payload["experts_mm"]),
            weights=tuple(float(value) for value in payload["weights"]),
            point_forecast_mm=float(payload["point_forecast_mm"]),
            fallback_persistence_mm=float(payload["fallback_persistence_mm"]),
            aci_alpha=float(payload["aci_alpha"]),
            conformal_history_count=int(payload["conformal_history_count"]),
            conformal_q_mm=(
                None
                if payload["conformal_q_mm"] is None
                else float(payload["conformal_q_mm"])
            ),
            interval_lower_mm=(
                None
                if payload["interval_lower_mm"] is None
                else float(payload["interval_lower_mm"])
            ),
            interval_upper_mm=(
                None
                if payload["interval_upper_mm"] is None
                else float(payload["interval_upper_mm"])
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LiveIntegrityError(f"{name} cannot reconstruct StationIssueV1") from exc


def _site_payload(site: SiteAggregateV1) -> dict[str, Any]:
    return asdict(site)


def _runner_code_sha256() -> str:
    monitored_modules = (
        Path(__file__),
        ROOT / "code" / "monitoring" / "prequential_core.py",
        ROOT / "code" / "monitoring" / "ootang_live_ledger.py",
    )
    return _canonical_sha256(
        {
            str(path.relative_to(ROOT)): _sha256_file(path)
            for path in monitored_modules
        }
    )


def _epoch_id(profile: dict[str, Any], prerequisites: Prerequisites) -> str:
    return _canonical_sha256(
        {
            "profile_id": profile["profile_id"],
            "profile_sha256": DEFAULT_CONFIG_SHA256,
            "source_snapshot_sha256": prerequisites.source.sha256,
            "model_manifest_sha256": prerequisites.model.sha256,
            "watermark": prerequisites.source.watermark.isoformat(),
            "implementation_sha256": prerequisites.implementation_sha256,
            "environment_sha256": prerequisites.environment_sha256,
        }
    )


def _event_spec(
    *,
    event_key: str,
    event_type: str,
    prerequisites: Prerequisites,
    payload: dict[str, Any],
    target_date_value: date | None = None,
    station: str | None = None,
    issue_id: str | None = None,
    input_manifest_sha256: str = ZERO_HASH,
    state_before_sha256: str = ZERO_HASH,
    state_after_sha256: str = ZERO_HASH,
) -> EventSpec:
    return EventSpec(
        event_key=event_key,
        event_type=event_type,
        target_date=(
            target_date_value.isoformat() if target_date_value is not None else None
        ),
        station=station,
        issue_id=issue_id,
        protocol_config_sha256=DEFAULT_CONFIG_SHA256,
        code_sha256=prerequisites.implementation_sha256,
        environment_sha256=prerequisites.environment_sha256,
        input_manifest_sha256=input_manifest_sha256,
        model_manifest_sha256=prerequisites.model.sha256,
        state_before_sha256=state_before_sha256,
        state_after_sha256=state_after_sha256,
        payload=payload,
    )


def _append_genesis(
    ledger: AppendOnlyLedger,
    profile: dict[str, Any],
    prerequisites: Prerequisites,
) -> LedgerEvent:
    stations = profile["stations"]
    states = {
        station: new_station_state_v1(reset_reason="live_epoch_start")
        for station in stations
    }
    epoch_id = _epoch_id(profile, prerequisites)
    state_after = _states_sha256(states, stations)
    payload = {
        "event_schema_version": profile["ledger"]["event_schema_version"],
        "ledger_namespace": "ootang-prequential-live-v1",
        "live_epoch_id": epoch_id,
        "profile_id": profile["profile_id"],
        "profile_version": profile["profile_version"],
        "artifact_status": profile["artifact_status"],
        "activation_watermark": prerequisites.source.watermark.isoformat(),
        "source_snapshot_sha256": prerequisites.source.sha256,
        "source_captured_at_utc": prerequisites.source.captured_at_utc,
        "outcome_source_id": prerequisites.source.outcome_source_id,
        "latest_displacement_mm": prerequisites.source.latest_displacement_mm,
        "model_version": prerequisites.model.model_version,
        "model_training_cutoff": prerequisites.model.training_cutoff.isoformat(),
        "algorithm_profile_sha256": prerequisites.algorithm_profile_sha256,
        "implementation_sha256": prerequisites.implementation_sha256,
        "environment": prerequisites.environment_record,
        "initial_state": "cold_start_all_stations",
        "station_states": _states_payload(states, stations),
        "prior_e1_manifest_sha256": profile["prior_evidence"][
            "expected_e1_manifest_sha256"
        ],
        "prior_e1_chain_linked": False,
        "formal_warning_output": False,
        "independent_label_used": False,
        "confirmatory_external_validation": False,
        "vajont_used": False,
        "engineering_capabilities": profile["engineering_capabilities"],
    }
    events = ledger.append_transaction(
        [
            _event_spec(
                event_key=f"{epoch_id}:epoch_genesis",
                event_type="epoch_genesis",
                prerequisites=prerequisites,
                payload=payload,
                input_manifest_sha256=prerequisites.source.data_manifest.sha256,
                state_before_sha256=ZERO_HASH,
                state_after_sha256=state_after,
            )
        ]
    )
    return events[0]


def _validate_event_context(
    event: LedgerEvent,
    prerequisites: Prerequisites,
) -> None:
    expected = {
        "protocol_config_sha256": DEFAULT_CONFIG_SHA256,
        "code_sha256": prerequisites.implementation_sha256,
        "environment_sha256": prerequisites.environment_sha256,
        "model_manifest_sha256": prerequisites.model.sha256,
    }
    for field, value in expected.items():
        if getattr(event, field) != value:
            raise LiveIntegrityError(
                f"Ledger event {event.sequence_id} {field} changed; open a new epoch"
            )


def _validate_issue_lifecycle_events(
    events: tuple[LedgerEvent, ...],
    *,
    target: date,
    stations: list[str],
    states: dict[str, StationStateV1],
    issue_events: dict[str, LedgerEvent],
    seal_event: LedgerEvent,
    end_sequence_id: int,
) -> None:
    target_text = target.isoformat()
    batch = [
        event
        for event in events
        if event.target_date == target_text and event.sequence_id <= end_sequence_id
    ]
    opened = [event for event in batch if event.event_type == "issue_batch_opened"]
    seals = [event for event in batch if event.event_type == "issue_batch_sealed"]
    fallbacks = [
        event for event in batch if event.event_type == "fallback_or_abstain_recorded"
    ]
    if len(opened) != 1 or seals != [seal_event]:
        raise LiveIntegrityError("Issue lifecycle open/seal cardinality changed")
    station_rows = [event for event in batch if event.event_type == "station_issue"]
    if [event.station for event in station_rows] != stations:
        raise LiveIntegrityError("Issue lifecycle station order changed")
    if [event.station for event in fallbacks] != stations:
        raise LiveIntegrityError("Fallback lifecycle station order changed")
    if not (
        opened[0].sequence_id
        < min(event.sequence_id for event in station_rows)
        <= max(event.sequence_id for event in station_rows)
        < seal_event.sequence_id
        < min(event.sequence_id for event in fallbacks)
    ):
        raise LiveIntegrityError("Issue lifecycle sequence changed")
    aggregate_state = _states_sha256(states, stations)
    issue_input_sha256 = opened[0].input_manifest_sha256
    for boundary in (opened[0], seal_event):
        if (
            boundary.state_before_sha256 != aggregate_state
            or boundary.state_after_sha256 != aggregate_state
            or boundary.input_manifest_sha256 != issue_input_sha256
        ):
            raise LiveIntegrityError("Issue batch boundary changed online state")
    for station, fallback in zip(stations, fallbacks, strict=True):
        issue_event = issue_events[station]
        issue = _decode_issue(
            issue_event.payload.get("issue"), name=f"lifecycle.issue.{station}"
        )
        expected_state = station_state_sha256_v1(states[station])
        if (
            fallback.issue_id != issue_event.issue_id
            or issue_event.input_manifest_sha256 != issue_input_sha256
            or fallback.input_manifest_sha256 != issue_input_sha256
            or fallback.state_before_sha256 != expected_state
            or fallback.state_after_sha256 != expected_state
            or fallback.payload.get("station") != station
            or fallback.payload.get("forecast_action") != issue.forecast_action
            or fallback.payload.get("uncertainty_action") != issue.uncertainty_action
            or fallback.payload.get("fallback_persistence_mm")
            != issue.fallback_persistence_mm
        ):
            raise LiveIntegrityError("Fallback record does not replay from station issue")


def _validate_settled_lifecycle_events(
    events: tuple[LedgerEvent, ...],
    *,
    target: date,
    stations: list[str],
    states: dict[str, StationStateV1],
    issue_events: dict[str, LedgerEvent],
    seal_event: LedgerEvent,
    settlement: LedgerEvent,
    actuals: dict[str, float],
) -> dict[str, StationStateV1]:
    _validate_issue_lifecycle_events(
        events,
        target=target,
        stations=stations,
        states=states,
        issue_events=issue_events,
        seal_event=seal_event,
        end_sequence_id=settlement.sequence_id,
    )
    target_text = target.isoformat()
    batch = [
        event
        for event in events
        if event.target_date == target_text
        and event.sequence_id <= settlement.sequence_id
    ]
    opened = [event for event in batch if event.event_type == "outcome_batch_opened"]
    revealed = [event for event in batch if event.event_type == "outcome_revealed"]
    scores = [event for event in batch if event.event_type == "score_recorded"]
    expert_updates = [
        event for event in batch if event.event_type == "expert_state_updated"
    ]
    conformal_updates = [
        event for event in batch if event.event_type == "conformal_state_updated"
    ]
    drift_updates = [
        event for event in batch if event.event_type == "drift_state_updated"
    ]
    site_rows = [event for event in batch if event.event_type == "site_score_recorded"]
    if len(opened) != 1 or len(site_rows) != 1:
        raise LiveIntegrityError("Outcome lifecycle boundary cardinality changed")
    for rows, name in (
        (revealed, "reveal"),
        (scores, "score"),
        (expert_updates, "expert update"),
        (conformal_updates, "conformal update"),
        (drift_updates, "drift update"),
    ):
        if [event.station for event in rows] != stations:
            raise LiveIntegrityError(f"Outcome {name} station order changed")
    if not (
        seal_event.sequence_id
        < opened[0].sequence_id
        < min(event.sequence_id for event in revealed)
        <= max(event.sequence_id for event in revealed)
        < min(event.sequence_id for event in scores)
        and max(event.sequence_id for event in drift_updates)
        < site_rows[0].sequence_id
        < settlement.sequence_id
    ):
        raise LiveIntegrityError("Outcome lifecycle global sequence changed")
    revision_id = settlement.payload.get("source_revision_id")
    batch_sha256 = settlement.payload.get("outcome_batch_sha256")
    outcome_input_sha256 = opened[0].input_manifest_sha256
    outcome_source_id = opened[0].payload.get("outcome_source_id")
    source_available_at_utc = opened[0].payload.get("source_available_at_utc")
    finalized_at_utc = opened[0].payload.get("finalized_at_utc")
    sealed_entry_sha256 = opened[0].payload.get(
        "issue_batch_sealed_entry_sha256"
    )
    initial_state_hash = _states_sha256(states, stations)
    if (
        opened[0].payload.get("source_revision_id") != revision_id
        or opened[0].payload.get("outcome_batch_sha256") != batch_sha256
        or sealed_entry_sha256 != seal_event.entry_sha256
        or settlement.payload.get("outcome_source_id") != outcome_source_id
        or settlement.payload.get("source_available_at_utc")
        != source_available_at_utc
        or settlement.payload.get("issue_batch_sealed_entry_sha256")
        != sealed_entry_sha256
        or settlement.input_manifest_sha256 != outcome_input_sha256
        or opened[0].state_before_sha256 != initial_state_hash
        or opened[0].state_after_sha256 != initial_state_hash
    ):
        raise LiveIntegrityError("Outcome batch opening does not match settlement")

    issues = {
        station: _decode_issue(
            issue_events[station].payload.get("issue"),
            name=f"settlement.issue.{station}",
        )
        for station in stations
    }
    reveals = {
        station: reveal_station(states[station], issues[station], actuals[station])
        for station in stations
    }
    next_states = {station: reveals[station].next_state for station in stations}
    for index, station in enumerate(stations):
        issue_event = issue_events[station]
        issue = issues[station]
        reveal = reveals[station]
        reveal_event = revealed[index]
        score_event = scores[index]
        expert_event = expert_updates[index]
        conformal_event = conformal_updates[index]
        drift_event = drift_updates[index]
        expected_score = {
            key: value
            for key, value in asdict(reveal).items()
            if key not in {"updated_state", "next_state"}
        }
        if (
            reveal_event.issue_id != issue_event.issue_id
            or reveal_event.payload.get("actual_mm") != actuals[station]
            or reveal_event.payload.get("source_revision_id") != revision_id
            or reveal_event.payload.get("outcome_batch_sha256") != batch_sha256
            or reveal_event.payload.get("outcome_source_id") != outcome_source_id
            or reveal_event.payload.get("source_available_at_utc")
            != source_available_at_utc
            or reveal_event.payload.get("finalized_at_utc") != finalized_at_utc
            or reveal_event.payload.get("issue_batch_sealed_entry_sha256")
            != sealed_entry_sha256
            or reveal_event.input_manifest_sha256 != outcome_input_sha256
            or reveal_event.state_before_sha256 != issue.state_before_sha256
            or reveal_event.state_after_sha256 != issue.state_before_sha256
        ):
            raise LiveIntegrityError("Outcome reveal does not match its station issue")
        if (
            score_event.payload.get("score") != expected_score
            or score_event.input_manifest_sha256 != outcome_input_sha256
            or score_event.state_before_sha256 != issue.state_before_sha256
            or score_event.state_after_sha256 != issue.state_before_sha256
        ):
            raise LiveIntegrityError("Station score does not replay")
        if (
            expert_event.payload.get("cumulative_loss_after")
            != list(reveal.updated_state.cumulative_loss)
            or expert_event.input_manifest_sha256 != outcome_input_sha256
            or expert_event.state_before_sha256 != issue.state_before_sha256
            or expert_event.state_after_sha256 != reveal.updated_state_sha256
        ):
            raise LiveIntegrityError("Expert-state update does not replay")
        if (
            conformal_event.payload.get("alpha_after")
            != reveal.aci_alpha_after_update
            or conformal_event.input_manifest_sha256 != outcome_input_sha256
            or conformal_event.payload.get("history_count_after")
            != reveal.history_count_after_update
            or conformal_event.payload.get("absolute_residual_history")
            != list(reveal.updated_state.absolute_residuals)
            or conformal_event.payload.get("underprediction_residual_history")
            != list(reveal.updated_state.underprediction_residuals)
            or conformal_event.state_before_sha256 != issue.state_before_sha256
            or conformal_event.state_after_sha256 != reveal.updated_state_sha256
        ):
            raise LiveIntegrityError("Conformal-state update does not replay")
        if (
            drift_event.payload.get("drift_detected") != reveal.drift_detected
            or drift_event.input_manifest_sha256 != outcome_input_sha256
            or drift_event.payload.get("drift_cut_index") != reveal.drift_cut_index
            or drift_event.payload.get("updated_state")
            != _state_payload(reveal.updated_state)
            or drift_event.payload.get("effective_next_state")
            != _state_payload(reveal.next_state)
            or drift_event.state_before_sha256 != reveal.updated_state_sha256
            or drift_event.state_after_sha256 != reveal.state_after_sha256
        ):
            raise LiveIntegrityError("Drift-state update does not replay")
        if not (
            score_event.sequence_id
            < expert_event.sequence_id
            < conformal_event.sequence_id
            < drift_event.sequence_id
        ):
            raise LiveIntegrityError("Per-station state-update sequence changed")

    expected_site = aggregate_site_scores(
        {station: reveals[station].anomaly_score for station in stations}
    )
    next_state_hash = _states_sha256(next_states, stations)
    if (
        _canonical_json(site_rows[0].payload.get("site"))
        != _canonical_json(_site_payload(expected_site))
        or site_rows[0].input_manifest_sha256 != outcome_input_sha256
        or site_rows[0].state_before_sha256 != initial_state_hash
        or site_rows[0].state_after_sha256 != next_state_hash
    ):
        raise LiveIntegrityError("Site score does not replay")
    return next_states


def _reconstruct_projection(
    events: tuple[LedgerEvent, ...],
    profile: dict[str, Any],
    prerequisites: Prerequisites,
) -> LiveProjection:
    if not events or events[0].event_type != "epoch_genesis":
        raise LiveIntegrityError("Live ledger must begin with exactly one epoch_genesis")
    if sum(event.event_type == "epoch_genesis" for event in events) != 1:
        raise LiveIntegrityError("Live ledger contains multiple genesis events")
    for event in events:
        _validate_event_context(event, prerequisites)

    stations = profile["stations"]
    genesis = events[0]
    expected_epoch = _epoch_id(profile, prerequisites)
    expected_genesis_fields = {
        "event_schema_version": profile["ledger"]["event_schema_version"],
        "ledger_namespace": "ootang-prequential-live-v1",
        "live_epoch_id": expected_epoch,
        "profile_id": profile["profile_id"],
        "profile_version": profile["profile_version"],
        "artifact_status": profile["artifact_status"],
        "activation_watermark": prerequisites.source.watermark.isoformat(),
        "source_snapshot_sha256": prerequisites.source.sha256,
        "source_captured_at_utc": prerequisites.source.captured_at_utc,
        "outcome_source_id": prerequisites.source.outcome_source_id,
        "model_version": prerequisites.model.model_version,
        "model_training_cutoff": prerequisites.model.training_cutoff.isoformat(),
        "algorithm_profile_sha256": prerequisites.algorithm_profile_sha256,
        "implementation_sha256": prerequisites.implementation_sha256,
        "environment": prerequisites.environment_record,
        "initial_state": "cold_start_all_stations",
        "prior_e1_manifest_sha256": profile["prior_evidence"][
            "expected_e1_manifest_sha256"
        ],
        "prior_e1_chain_linked": False,
        "formal_warning_output": False,
        "independent_label_used": False,
        "confirmatory_external_validation": False,
        "vajont_used": False,
        "engineering_capabilities": profile["engineering_capabilities"],
    }
    for field, expected_value in expected_genesis_fields.items():
        if genesis.payload.get(field) != expected_value:
            raise LiveIntegrityError(f"Ledger genesis field changed: {field}")
    if (
        genesis.state_before_sha256 != ZERO_HASH
        or genesis.input_manifest_sha256
        != prerequisites.source.data_manifest.sha256
    ):
        raise LiveIntegrityError("Ledger genesis envelope changed")
    states = _decode_states(
        genesis.payload.get("station_states"), stations, name="genesis.station_states"
    )
    if genesis.state_after_sha256 != _states_sha256(states, stations):
        raise LiveIntegrityError("Genesis station-state aggregate hash changed")
    expected_cold_states = {
        station: new_station_state_v1(reset_reason="live_epoch_start")
        for station in stations
    }
    if states != expected_cold_states:
        raise LiveIntegrityError("Ledger genesis is not an eight-station cold start")
    last_date = _parse_date(
        genesis.payload.get("activation_watermark"), name="genesis.watermark"
    )
    displacement_payload = _require_exact_keys(
        genesis.payload.get("latest_displacement_mm"),
        set(stations),
        name="genesis.latest_displacement_mm",
    )
    latest = {
        station: _require_finite_float(
            displacement_payload[station], name=f"genesis.latest.{station}"
        )
        for station in stations
    }
    if latest != prerequisites.source.latest_displacement_mm:
        raise LiveIntegrityError("Genesis displacement baseline changed")
    outstanding_target: date | None = None
    outstanding_issue_id: str | None = None
    issue_events: dict[str, LedgerEvent] = {}
    seal_event: LedgerEvent | None = None
    anchors: dict[str, dict[str, Any]] = {}
    settled_events: dict[str, LedgerEvent] = {}
    settled_issue_events: dict[str, dict[str, LedgerEvent]] = {}
    backfill_events: dict[str, LedgerEvent] = {}
    revision_ids: dict[str, dict[str, str]] = {}
    latest_actuals_by_date: dict[str, dict[str, float]] = {}
    revision_stations: dict[tuple[str, str], set[str]] = {}
    revision_rescore_stations: dict[tuple[str, str], set[str]] = {}
    revision_input_hashes: dict[tuple[str, str], str] = {}
    blind_count = 0
    engineering_blind_candidate_count = 0
    backfill_count = 0
    lifecycle_counts: dict[str, Counter[str]] = {}
    outcome_transaction_open = False
    active_lifecycle_types = {
        "issue_batch_opened",
        "station_issue",
        "issue_batch_sealed",
        "fallback_or_abstain_recorded",
        "anchor_requested",
        "anchor_confirmed",
        "anchor_failed",
        "outcome_batch_opened",
        "outcome_revealed",
        "score_recorded",
        "expert_state_updated",
        "conformal_state_updated",
        "drift_state_updated",
        "site_score_recorded",
        "outcome_batch_settled",
    }
    outcome_after_open_types = {
        "outcome_revealed",
        "score_recorded",
        "expert_state_updated",
        "conformal_state_updated",
        "drift_state_updated",
        "site_score_recorded",
        "outcome_batch_settled",
    }

    for event in events[1:]:
        event_date = (
            _parse_date(event.target_date, name=f"event[{event.sequence_id}].date")
            if event.target_date is not None
            else None
        )
        if event.event_type == "backfill_not_blind":
            if outstanding_target is not None or event_date != last_date + timedelta(days=1):
                raise LiveIntegrityError("Backfill date or outstanding-state order changed")
            actuals = _require_exact_keys(
                event.payload.get("actual_by_station"),
                set(stations),
                name="backfill.actual_by_station",
            )
            latest = {
                station: _require_finite_float(
                    actuals[station], name=f"backfill.actual.{station}"
                )
                for station in stations
            }
            last_date = event_date
            target_key = event_date.isoformat()
            backfill_events[target_key] = event
            revision_ids[target_key] = {
                str(event.payload.get("source_revision_id")): str(
                    event.payload.get("outcome_batch_sha256")
                )
            }
            latest_actuals_by_date[target_key] = dict(latest)
            expected_state = _states_sha256(states, stations)
            if event.state_before_sha256 != event.state_after_sha256:
                raise LiveIntegrityError("Backfill must not update online state")
            if event.state_after_sha256 != expected_state:
                raise LiveIntegrityError("Backfill state hash changed")
            backfill_count += 1
            continue

        if event.event_type == "issue_batch_opened":
            if outstanding_target is not None or event_date != last_date + timedelta(days=1):
                raise LiveIntegrityError("Issue batch opened out of natural-day order")
            outstanding_target = event_date
            outstanding_issue_id = event.issue_id
            issue_events = {}
            seal_event = None
            outcome_transaction_open = False
            lifecycle_counts[event_date.isoformat()] = Counter()
        elif event.event_type in active_lifecycle_types:
            if outstanding_target is None or event_date != outstanding_target:
                raise LiveIntegrityError(
                    "Lifecycle event is outside the outstanding natural day"
                )
            if event.event_type in {
                "fallback_or_abstain_recorded",
                "anchor_requested",
                "anchor_confirmed",
                "anchor_failed",
                "outcome_batch_opened",
                *outcome_after_open_types,
            } and seal_event is None:
                raise LiveIntegrityError("Post-issue lifecycle event precedes the seal")
            if event.event_type == "outcome_batch_opened":
                counts = lifecycle_counts[event_date.isoformat()]
                if (
                    outcome_transaction_open
                    or counts["fallback_or_abstain_recorded"] != 8
                    or counts["anchor_requested"] < 1
                ):
                    raise LiveIntegrityError(
                        "Outcome batch opened before the complete issue transaction"
                    )
                outcome_transaction_open = True
            elif event.event_type in {
                "anchor_requested",
                "anchor_confirmed",
                "anchor_failed",
            }:
                counts = lifecycle_counts[event_date.isoformat()]
                if (
                    counts["fallback_or_abstain_recorded"] != 8
                    or outcome_transaction_open
                    or event.payload.get("sealed_entry_sha256")
                    != seal_event.entry_sha256
                    or event.issue_id != seal_event.issue_id
                    or event.input_manifest_sha256
                    != seal_event.input_manifest_sha256
                ):
                    raise LiveIntegrityError("Time-anchor event is out of lifecycle order")
                if event.event_type in {"anchor_confirmed", "anchor_failed"} and (
                    counts["anchor_requested"]
                    <= counts["anchor_confirmed"] + counts["anchor_failed"]
                ):
                    raise LiveIntegrityError(
                        "Time-anchor result has no unmatched request"
                    )
            elif (
                event.event_type in outcome_after_open_types
                and not outcome_transaction_open
            ):
                raise LiveIntegrityError("Outcome lifecycle event precedes batch opening")
        elif event.event_type not in {
            "outcome_revision",
            "revision_rescore_recorded",
        }:
            raise LiveIntegrityError(
                f"Unsupported event in active E2-A epoch: {event.event_type}"
            )
        if event_date is not None and event_date.isoformat() in lifecycle_counts:
            lifecycle_counts[event_date.isoformat()][event.event_type] += 1
        if event.event_type == "station_issue":
            if event_date != outstanding_target or event.station not in stations:
                raise LiveIntegrityError("Station issue is outside the open batch")
            if event.station in issue_events:
                raise LiveIntegrityError("Duplicate station issue in one batch")
            issue = _decode_issue(event.payload.get("issue"), name="station_issue.issue")
            expected_station_state = station_state_sha256_v1(states[event.station])
            if issue.state_before_sha256 != expected_station_state:
                raise LiveIntegrityError("Station issue does not match recovered state")
            if (
                event.state_before_sha256 != expected_station_state
                or event.state_after_sha256 != expected_station_state
            ):
                raise LiveIntegrityError("Station issue event changed online state")
            if issue.experts_mm[0] != latest[event.station]:
                raise LiveIntegrityError(
                    "Station issue persistence does not match the latest known outcome"
                )
            if issue != issue_station(states[event.station], issue.experts_mm):
                raise LiveIntegrityError("Station issue math does not replay")
            issue_events[event.station] = event
        elif event.event_type == "issue_batch_sealed":
            if event_date != outstanding_target or list(issue_events) != stations:
                raise LiveIntegrityError("Issue seal occurred before all eight station issues")
            if event.issue_id != outstanding_issue_id:
                raise LiveIntegrityError("Issue seal id changed")
            expected_root = _canonical_sha256(
                [issue_events[station].payload["issue"] for station in stations]
            )
            if event.payload.get("issue_batch_sha256") != expected_root:
                raise LiveIntegrityError("Issue batch root does not replay")
            seal_event = event
        elif event.event_type == "anchor_confirmed":
            root = event.payload.get("sealed_entry_sha256")
            if (
                not isinstance(root, str)
                or seal_event is None
                or root != seal_event.entry_sha256
            ):
                raise LiveIntegrityError(
                    "Anchor confirmation does not cover the outstanding issue seal"
                )
            _stored_anchor_payload(event.payload, sealed_entry_sha256=root)
            anchors[root] = event.payload
        elif event.event_type == "outcome_batch_settled":
            if event_date != outstanding_target or seal_event is None:
                raise LiveIntegrityError("Outcome settled without a sealed issue batch")
            counts = lifecycle_counts[event_date.isoformat()]
            required = {
                "issue_batch_opened": 1,
                "station_issue": 8,
                "issue_batch_sealed": 1,
                "fallback_or_abstain_recorded": 8,
                "outcome_batch_opened": 1,
                "outcome_revealed": 8,
                "score_recorded": 8,
                "expert_state_updated": 8,
                "conformal_state_updated": 8,
                "drift_state_updated": 8,
                "site_score_recorded": 1,
                "outcome_batch_settled": 1,
            }
            if any(counts[name] != count for name, count in required.items()):
                raise LiveIntegrityError("Settled outcome lifecycle event counts changed")
            settled_states = _decode_states(
                event.payload.get("states_after"),
                stations,
                name="settlement.states_after",
            )
            actuals = _require_exact_keys(
                event.payload.get("actual_by_station"),
                set(stations),
                name="settlement.actual_by_station",
            )
            latest = {
                station: _require_finite_float(
                    actuals[station], name=f"settlement.actual.{station}"
                )
                for station in stations
            }
            expected_states = _validate_settled_lifecycle_events(
                events,
                target=event_date,
                stations=stations,
                states=states,
                issue_events=issue_events,
                seal_event=seal_event,
                settlement=event,
                actuals=latest,
            )
            if settled_states != expected_states:
                raise LiveIntegrityError("Settled station states do not replay from outcomes")
            if event.state_before_sha256 != _states_sha256(states, stations):
                raise LiveIntegrityError("Settlement state-before aggregate hash changed")
            if event.state_after_sha256 != _states_sha256(settled_states, stations):
                raise LiveIntegrityError("Settled station-state aggregate hash changed")
            anchor_payload = anchors.get(seal_event.entry_sha256)
            expected_candidate = False
            if anchor_payload is not None:
                seal_time = _parse_utc(
                    seal_event.recorded_at_utc, name="issue_seal.recorded_at_utc"
                )
                anchor_time = _parse_utc(
                    anchor_payload.get("anchored_at_utc"),
                    name="anchor.anchored_at_utc",
                )
                outcome_available = _parse_utc(
                    event.payload.get("source_available_at_utc"),
                    name="settlement.source_available_at_utc",
                )
                target_start = datetime.combine(
                    event_date,
                    datetime.min.time(),
                    tzinfo=ZoneInfo(profile["target"]["date_timezone"]),
                ).astimezone(timezone.utc)
                expected_candidate = (
                    seal_time <= anchor_time < target_start
                    and anchor_time < outcome_available
                )
            if (
                event.payload.get("engineering_blind_time_order_candidate")
                != expected_candidate
            ):
                raise LiveIntegrityError("Settlement blind time-order flag does not replay")
            if (
                event.payload.get("trusted_anchor_receipt_verified") is not False
                or event.payload.get("e2_live_evidence_eligible") is not False
            ):
                raise LiveIntegrityError(
                    "E2-A cannot promote an unverified receipt to live evidence"
                )
            states = settled_states
            last_date = event_date
            settled_events[event_date.isoformat()] = event
            settled_issue_events[event_date.isoformat()] = dict(issue_events)
            revision_ids[event_date.isoformat()] = {
                str(event.payload.get("source_revision_id")): str(
                    event.payload.get("outcome_batch_sha256")
                )
            }
            latest_actuals_by_date[event_date.isoformat()] = dict(latest)
            if event.payload.get("engineering_blind_time_order_candidate") is True:
                engineering_blind_candidate_count += 1
            if event.payload.get("e2_live_evidence_eligible") is True:
                blind_count += 1
            outstanding_target = None
            outstanding_issue_id = None
            issue_events = {}
            seal_event = None
            outcome_transaction_open = False
        elif event.event_type == "outcome_revision":
            target_key = event_date.isoformat() if event_date is not None else ""
            if target_key not in settled_events and target_key not in backfill_events:
                raise LiveIntegrityError("Outcome revision has no settled original")
            revision_id = str(event.payload.get("source_revision_id"))
            batch_sha = str(event.payload.get("outcome_batch_sha256"))
            if event.station not in stations:
                raise LiveIntegrityError("Outcome revision station is outside the profile")
            known = revision_ids.setdefault(target_key, {})
            if revision_id in known and known[revision_id] != batch_sha:
                raise LiveIntegrityError("Outcome revision id changed content")
            known[revision_id] = batch_sha
            revision_key = (target_key, revision_id)
            expected_input_hash = revision_input_hashes.setdefault(
                revision_key, event.input_manifest_sha256
            )
            if event.input_manifest_sha256 != expected_input_hash:
                raise LiveIntegrityError("Outcome revision input hash changed")
            seen = revision_stations.setdefault(revision_key, set())
            if event.station in seen:
                raise LiveIntegrityError("Outcome revision repeats one station")
            seen.add(event.station)
            current_actuals = latest_actuals_by_date.get(target_key)
            if current_actuals is None:
                raise LiveIntegrityError("Outcome revision lost its prior actual values")
            previous_actual = _require_finite_float(
                event.payload.get("previous_actual_mm"),
                name=f"revision.previous_actual.{event.station}",
            )
            if previous_actual != current_actuals[event.station]:
                raise LiveIntegrityError("Outcome revision supersession lineage changed")
            revised_actual = _require_finite_float(
                event.payload.get("revised_actual_mm"),
                name=f"revision.actual.{event.station}",
            )
            current_actuals[event.station] = revised_actual
            expected_state = station_state_sha256_v1(states[event.station])
            if (
                event.state_before_sha256 != expected_state
                or event.state_after_sha256 != expected_state
            ):
                raise LiveIntegrityError("Outcome revision rewrote live online state")
            if event_date == last_date and outstanding_target is None:
                latest[event.station] = revised_actual
        elif event.event_type == "revision_rescore_recorded":
            target_key = event_date.isoformat() if event_date is not None else ""
            revision_id = str(event.payload.get("source_revision_id"))
            revision_key = (target_key, revision_id)
            if revision_key not in revision_stations or event.station not in stations:
                raise LiveIntegrityError("Revision rescore has no matching revision")
            seen = revision_rescore_stations.setdefault(revision_key, set())
            if event.station in seen:
                raise LiveIntegrityError("Revision rescore repeats one station")
            seen.add(event.station)
            if event.input_manifest_sha256 != revision_input_hashes.get(revision_key):
                raise LiveIntegrityError("Revision rescore input hash changed")
            expected_state = station_state_sha256_v1(states[event.station])
            issue_event = settled_issue_events.get(target_key, {}).get(event.station)
            current_actuals = latest_actuals_by_date.get(target_key)
            if issue_event is None or current_actuals is None:
                raise LiveIntegrityError("Revision rescore lost its original issue")
            issue = _decode_issue(
                issue_event.payload.get("issue"),
                name=f"revision_rescore.issue.{event.station}",
            )
            actual = current_actuals[event.station]
            expected_interval_covered = (
                None
                if issue.interval_lower_mm is None or issue.interval_upper_mm is None
                else issue.interval_lower_mm <= actual <= issue.interval_upper_mm
            )
            if (
                event.state_before_sha256 != expected_state
                or event.state_after_sha256 != expected_state
                or event.payload.get("updates_live_state") is not False
                or event.payload.get("point_absolute_error_mm")
                != abs(actual - issue.point_forecast_mm)
                or event.payload.get("persistence_absolute_error_mm")
                != abs(actual - issue.fallback_persistence_mm)
                or event.payload.get("underprediction_residual_mm")
                != max(actual - issue.point_forecast_mm, 0.0)
                or event.payload.get("interval_covered")
                != expected_interval_covered
            ):
                raise LiveIntegrityError("Revision rescore does not replay")

    if outstanding_target is not None:
        if seal_event is None:
            raise LiveIntegrityError("Atomic issue transaction lacks its seal")
        if list(issue_events) != stations:
            raise LiveIntegrityError("Outstanding issue does not contain all stations")
        if outcome_transaction_open:
            raise LiveIntegrityError("Atomic outcome transaction is incomplete")
        _validate_issue_lifecycle_events(
            events,
            target=outstanding_target,
            stations=stations,
            states=states,
            issue_events=issue_events,
            seal_event=seal_event,
            end_sequence_id=events[-1].sequence_id,
        )
    for revision_key, seen in revision_stations.items():
        if seen != set(stations):
            raise LiveIntegrityError("Outcome revision is not complete for all stations")
        target_key, _ = revision_key
        expected_rescores = set(stations) if target_key in settled_events else set()
        if revision_rescore_stations.get(revision_key, set()) != expected_rescores:
            raise LiveIntegrityError("Outcome revision rescore set changed")
    return LiveProjection(
        epoch_id=expected_epoch,
        states=states,
        last_finalized_date=last_date,
        latest_displacement_mm=latest,
        outstanding_target_date=outstanding_target,
        outstanding_issue_id=outstanding_issue_id,
        issue_events=issue_events,
        seal_event=seal_event,
        anchored_seal_hashes=anchors,
        settled_events=settled_events,
        backfill_events=backfill_events,
        revision_ids=revision_ids,
        latest_actuals_by_date=latest_actuals_by_date,
        blind_settled_count=blind_count,
        engineering_blind_candidate_count=engineering_blind_candidate_count,
        backfill_count=backfill_count,
    )


class _ReadOnlyAppendOnlyLedger(AppendOnlyLedger):
    """Use the ledger's full verifier without its create/initialize path."""

    def __init__(self, path: Path, *, timeout_seconds: float = 10.0) -> None:
        self.path = path
        self.timeout_seconds = timeout_seconds

    def _connect(self) -> sqlite3.Connection:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                f"{self.path.resolve().as_uri()}?mode=ro",
                timeout=self.timeout_seconds,
                isolation_level=None,
                uri=True,
            )
            connection.row_factory = sqlite3.Row
            connection.execute(
                f"PRAGMA busy_timeout={int(self.timeout_seconds * 1000)}"
            )
            connection.execute("PRAGMA query_only=ON")
            if int(connection.execute("PRAGMA query_only").fetchone()[0]) != 1:
                raise LedgerError("ledger connection did not remain query-only")
            return connection
        except LedgerError:
            if connection is not None:
                connection.close()
            raise
        except sqlite3.Error as exc:
            if connection is not None:
                connection.close()
            raise LedgerError("cannot open existing ledger read-only") from exc


def _freeze_ledger_value(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_ledger_value(child) for key, child in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_ledger_value(child) for child in value)
    return value


def _freeze_ledger_event(event: LedgerEvent) -> LedgerEvent:
    return replace(event, payload=_freeze_ledger_value(event.payload))


def load_verified_ledger_projection(
    profile: dict[str, Any],
    paths: RuntimePaths,
    prerequisites: Prerequisites,
) -> VerifiedLedgerProjection:
    """Read and scientifically replay an existing ledger without modifying it.

    The caller must first load the reviewed profile, runtime paths, and activation
    prerequisites.  Missing and empty ledgers are rejected rather than initialized.
    """

    try:
        ledger_stat = paths.ledger.stat()
    except FileNotFoundError as exc:
        raise LivePrerequisiteError("Live ledger does not exist") from exc
    except OSError as exc:
        raise LiveIntegrityError("Live ledger metadata cannot be read") from exc
    if not stat.S_ISREG(ledger_stat.st_mode):
        raise LiveIntegrityError("Live ledger path is not a regular file")
    if ledger_stat.st_size == 0:
        raise LiveIntegrityError("Live ledger file is empty")

    ledger = _ReadOnlyAppendOnlyLedger(paths.ledger)
    try:
        events = ledger.read_events()
    except LedgerError as exc:
        raise LiveIntegrityError(
            f"Append-only ledger failed read-only verification: {exc}"
        ) from exc
    if not events:
        raise LiveIntegrityError("Live ledger has no epoch genesis event")
    projection = _reconstruct_projection(events, profile, prerequisites)

    immutable_events = tuple(_freeze_ledger_event(event) for event in events)
    immutable_by_hash = {
        event.entry_sha256: event for event in immutable_events
    }

    def immutable_event(event: LedgerEvent | None) -> LedgerEvent | None:
        return None if event is None else immutable_by_hash[event.entry_sha256]

    head = immutable_events[-1]
    return VerifiedLedgerProjection(
        epoch_id=projection.epoch_id,
        states=MappingProxyType(dict(projection.states)),
        last_finalized_date=projection.last_finalized_date,
        latest_displacement_mm=MappingProxyType(
            dict(projection.latest_displacement_mm)
        ),
        outstanding_target_date=projection.outstanding_target_date,
        outstanding_issue_id=projection.outstanding_issue_id,
        issue_events=MappingProxyType(
            {
                key: immutable_by_hash[event.entry_sha256]
                for key, event in projection.issue_events.items()
            }
        ),
        seal_event=immutable_event(projection.seal_event),
        anchored_seal_hashes=_freeze_ledger_value(
            projection.anchored_seal_hashes
        ),
        settled_events=MappingProxyType(
            {
                key: immutable_by_hash[event.entry_sha256]
                for key, event in projection.settled_events.items()
            }
        ),
        backfill_events=MappingProxyType(
            {
                key: immutable_by_hash[event.entry_sha256]
                for key, event in projection.backfill_events.items()
            }
        ),
        revision_ids=_freeze_ledger_value(projection.revision_ids),
        latest_actuals_by_date=_freeze_ledger_value(
            projection.latest_actuals_by_date
        ),
        blind_settled_count=projection.blind_settled_count,
        engineering_blind_candidate_count=(
            projection.engineering_blind_candidate_count
        ),
        backfill_count=projection.backfill_count,
        ledger_events=immutable_events,
        ledger_event_count=len(immutable_events),
        ledger_terminal_sequence_id=head.sequence_id,
        ledger_terminal_sha256=head.entry_sha256,
    )


def _issue_batch_id(epoch_id: str, target: date) -> str:
    return _canonical_sha256(
        {"live_epoch_id": epoch_id, "target_date": target.isoformat(), "horizon": "P1D"}
    )


def _station_issue_id(batch_id: str, station: str) -> str:
    return _canonical_sha256({"issue_batch_id": batch_id, "station": station})


def _append_backfill(
    ledger: AppendOnlyLedger,
    profile: dict[str, Any],
    prerequisites: Prerequisites,
    projection: LiveProjection,
    outcome: OutcomeBatch,
) -> None:
    if outcome.target_date != projection.last_finalized_date + timedelta(days=1):
        raise LiveInputError("Backfill must advance exactly one natural day")
    state_hash = _states_sha256(projection.states, profile["stations"])
    payload = {
        "classification": "backfill_not_blind",
        "reason": "outcome_was_available_before_any_durable_issue",
        "target_date": outcome.target_date.isoformat(),
        "outcome_batch_sha256": outcome.sha256,
        "outcome_source_id": outcome.outcome_source_id,
        "source_revision_id": outcome.source_revision_id,
        "source_observed_at_utc": outcome.source_observed_at_utc,
        "source_available_at_utc": outcome.source_available_at_utc,
        "finalized_at_utc": outcome.finalized_at_utc,
        "actual_by_station": outcome.actual_by_station,
        "online_state_updated": False,
        "blind_metric_eligible": False,
    }
    ledger.append_transaction(
        [
            _event_spec(
                event_key=(
                    f"{projection.epoch_id}:{outcome.target_date.isoformat()}:"
                    "backfill_not_blind"
                ),
                event_type="backfill_not_blind",
                prerequisites=prerequisites,
                payload=payload,
                target_date_value=outcome.target_date,
                input_manifest_sha256=outcome.source_manifest.sha256,
                state_before_sha256=state_hash,
                state_after_sha256=state_hash,
            )
        ]
    )


def _append_issue_batch(
    ledger: AppendOnlyLedger,
    profile: dict[str, Any],
    prerequisites: Prerequisites,
    projection: LiveProjection,
    issue_batch: IssueBatch,
) -> tuple[LedgerEvent, ...]:
    stations = profile["stations"]
    target = issue_batch.target_date
    if target != projection.last_finalized_date + timedelta(days=1):
        raise LiveInputError("Issue target must be the next natural day")
    for station in stations:
        persistence = issue_batch.experts_by_station[station][0]
        if persistence != projection.latest_displacement_mm[station]:
            raise LiveInputError(
                f"Issue persistence for {station} is not the previous natural day"
            )
    batch_id = _issue_batch_id(projection.epoch_id, target)
    state_hash = _states_sha256(projection.states, stations)
    station_issues = {
        station: issue_station(
            projection.states[station], issue_batch.experts_by_station[station]
        )
        for station in stations
    }
    issue_root = _canonical_sha256(
        [_issue_payload(station_issues[station]) for station in stations]
    )
    prefix = f"{projection.epoch_id}:{target.isoformat()}"
    specs: list[EventSpec] = [
        _event_spec(
            event_key=f"{prefix}:issue_batch_opened",
            event_type="issue_batch_opened",
            prerequisites=prerequisites,
            payload={
                "issue_batch_id": batch_id,
                "target_date": target.isoformat(),
                "issue_feed_sha256": issue_batch.sha256,
                "generated_at_utc": issue_batch.generated_at_utc,
                "source_as_of_at_utc": issue_batch.source_as_of_at_utc,
                "source_snapshot_sha256": issue_batch.source_snapshot_sha256,
                "input_manifest_path": str(issue_batch.input_manifest.path),
                "all_station_issues_prepared_before_append": True,
            },
            target_date_value=target,
            issue_id=batch_id,
            input_manifest_sha256=issue_batch.input_manifest.sha256,
            state_before_sha256=state_hash,
            state_after_sha256=state_hash,
        )
    ]
    for station in stations:
        station_issue = station_issues[station]
        station_id = _station_issue_id(batch_id, station)
        specs.append(
            _event_spec(
                event_key=f"{prefix}:station_issue:{station}",
                event_type="station_issue",
                prerequisites=prerequisites,
                payload={
                    "issue_batch_id": batch_id,
                    "station_issue_id": station_id,
                    "station": station,
                    "issue": _issue_payload(station_issue),
                    "actual_field_present": False,
                },
                target_date_value=target,
                station=station,
                issue_id=station_id,
                input_manifest_sha256=issue_batch.input_manifest.sha256,
                state_before_sha256=station_issue.state_before_sha256,
                state_after_sha256=station_issue.state_before_sha256,
            )
        )
    specs.append(
        _event_spec(
            event_key=f"{prefix}:issue_batch_sealed",
            event_type="issue_batch_sealed",
            prerequisites=prerequisites,
            payload={
                "issue_batch_id": batch_id,
                "issue_batch_sha256": issue_root,
                "station_issue_keys": [
                    f"{prefix}:station_issue:{station}" for station in stations
                ],
                "station_count": len(stations),
                "same_date_actual_excluded": True,
                "evidence_status": "locally_sealed_unanchored",
            },
            target_date_value=target,
            issue_id=batch_id,
            input_manifest_sha256=issue_batch.input_manifest.sha256,
            state_before_sha256=state_hash,
            state_after_sha256=state_hash,
        )
    )
    for station in stations:
        station_issue = station_issues[station]
        specs.append(
            _event_spec(
                event_key=f"{prefix}:fallback_or_abstain:{station}",
                event_type="fallback_or_abstain_recorded",
                prerequisites=prerequisites,
                payload={
                    "station": station,
                    "forecast_action": station_issue.forecast_action,
                    "uncertainty_action": station_issue.uncertainty_action,
                    "fallback_persistence_mm": station_issue.fallback_persistence_mm,
                    "reason": (
                        "minimum_history_not_reached"
                        if station_issue.forecast_action == "abstain"
                        else "active"
                    ),
                },
                target_date_value=target,
                station=station,
                issue_id=_station_issue_id(batch_id, station),
                input_manifest_sha256=issue_batch.input_manifest.sha256,
                state_before_sha256=station_issue.state_before_sha256,
                state_after_sha256=station_issue.state_before_sha256,
            )
        )
    return ledger.append_transaction(specs)


def _anchor_response(
    response: dict[str, Any], *, sealed_entry_sha256: str
) -> dict[str, Any]:
    record = _require_exact_keys(
        response,
        {"provider", "receipt_id", "anchored_at_utc", "root_sha256", "receipt"},
        name="time-anchor response",
    )
    provider = _require_nonempty_string(record["provider"], name="anchor.provider")
    receipt_id = _require_nonempty_string(
        record["receipt_id"], name="anchor.receipt_id"
    )
    anchored = _require_nonempty_string(
        record["anchored_at_utc"], name="anchor.anchored_at_utc"
    )
    _parse_utc(anchored, name="anchor.anchored_at_utc")
    root = _require_sha256(record["root_sha256"], name="anchor.root_sha256")
    if root != sealed_entry_sha256:
        raise LiveIntegrityError("Time-anchor receipt covers a different ledger root")
    if not isinstance(record["receipt"], dict):
        raise LiveInputError("Time-anchor receipt payload must be an object")
    _canonical_json(record["receipt"])
    return {
        "provider": provider,
        "receipt_id": receipt_id,
        "anchored_at_utc": anchored,
        "sealed_entry_sha256": root,
        "receipt": record["receipt"],
    }


def _stored_anchor_payload(
    payload: dict[str, Any], *, sealed_entry_sha256: str
) -> dict[str, Any]:
    root = _require_sha256(
        payload.get("sealed_entry_sha256"), name="anchor.sealed_entry_sha256"
    )
    if root != sealed_entry_sha256:
        raise LiveIntegrityError("Stored time-anchor event covers a different root")
    provider = _require_nonempty_string(
        payload.get("provider"), name="anchor.provider"
    )
    receipt_id = _require_nonempty_string(
        payload.get("receipt_id"), name="anchor.receipt_id"
    )
    anchored = _require_nonempty_string(
        payload.get("anchored_at_utc"), name="anchor.anchored_at_utc"
    )
    _parse_utc(anchored, name="anchor.anchored_at_utc")
    receipt = payload.get("receipt")
    if not isinstance(receipt, dict):
        raise LiveIntegrityError("Stored time-anchor receipt payload is not an object")
    _canonical_json(receipt)
    return {
        "provider": provider,
        "receipt_id": receipt_id,
        "anchored_at_utc": anchored,
        "sealed_entry_sha256": root,
        "receipt": receipt,
    }


def _ensure_anchor_receipt_file(
    paths: RuntimePaths,
    seal: LedgerEvent,
    confirmed: dict[str, Any],
) -> None:
    stored = _stored_anchor_payload(
        confirmed, sealed_entry_sha256=seal.entry_sha256
    )
    receipt_path = paths.anchors / f"{seal.target_date}_{seal.entry_sha256}.json"
    if receipt_path.exists():
        existing_receipt = _load_json(receipt_path, name="stored anchor receipt")
        if existing_receipt != stored:
            raise LiveIntegrityError("Stored time-anchor receipt changed")
        return
    # The ledger is the source of truth.  This also repairs a crash after the
    # confirmed event committed but before its redundant receipt file did.
    _atomic_write_json(receipt_path, stored)


def _repair_and_verify_anchor_receipts(
    paths: RuntimePaths, events: tuple[LedgerEvent, ...]
) -> None:
    seals = {
        event.entry_sha256: event
        for event in events
        if event.event_type == "issue_batch_sealed"
    }
    for event in events:
        if event.event_type != "anchor_confirmed":
            continue
        root = event.payload.get("sealed_entry_sha256")
        seal = seals.get(root) if isinstance(root, str) else None
        if seal is None:
            raise LiveIntegrityError(
                "Confirmed time-anchor event has no matching issue seal"
            )
        _ensure_anchor_receipt_file(paths, seal, event.payload)


def _attempt_anchor(
    ledger: AppendOnlyLedger,
    paths: RuntimePaths,
    profile: dict[str, Any],
    prerequisites: Prerequisites,
    projection: LiveProjection,
    *,
    anchor_client: AnchorClient | None,
) -> None:
    seal = projection.seal_event
    if seal is None:
        return
    prior_confirmation = projection.anchored_seal_hashes.get(seal.entry_sha256)
    if prior_confirmation is not None:
        _ensure_anchor_receipt_file(paths, seal, prior_confirmation)
        return
    existing = ledger.read_events()
    relevant = [
        event
        for event in existing
        if event.event_type in {"anchor_requested", "anchor_failed", "anchor_confirmed"}
        and event.payload.get("sealed_entry_sha256") == seal.entry_sha256
    ]
    endpoint_name = profile["external_anchor"]["endpoint_environment_variable"]
    endpoint = os.environ.get(endpoint_name)
    prior_missing = any(
        event.event_type == "anchor_failed"
        and event.payload.get("reason_code") == "endpoint_not_configured"
        for event in relevant
    )
    if endpoint is None and prior_missing:
        return
    attempt = 1 + sum(event.event_type == "anchor_requested" for event in relevant)
    prefix = f"{projection.epoch_id}:{seal.target_date}:anchor:{attempt}"
    request_payload = {
        "live_epoch_id": projection.epoch_id,
        "target_date": seal.target_date,
        "sealed_sequence_id": seal.sequence_id,
        "sealed_entry_sha256": seal.entry_sha256,
        "attempt": attempt,
    }
    ledger.append_transaction(
        [
            _event_spec(
                event_key=f"{prefix}:requested",
                event_type="anchor_requested",
                prerequisites=prerequisites,
                payload=request_payload,
                target_date_value=date.fromisoformat(str(seal.target_date)),
                issue_id=seal.issue_id,
                input_manifest_sha256=seal.input_manifest_sha256,
                state_before_sha256=seal.state_after_sha256,
                state_after_sha256=seal.state_after_sha256,
            )
        ]
    )
    if endpoint is None:
        ledger.append_transaction(
            [
                _event_spec(
                    event_key=f"{prefix}:failed",
                    event_type="anchor_failed",
                    prerequisites=prerequisites,
                    payload={
                        **request_payload,
                        "reason_code": "endpoint_not_configured",
                        "retry_policy": "automatic_when_endpoint_becomes_available",
                    },
                    target_date_value=date.fromisoformat(str(seal.target_date)),
                    issue_id=seal.issue_id,
                    input_manifest_sha256=seal.input_manifest_sha256,
                    state_before_sha256=seal.state_after_sha256,
                    state_after_sha256=seal.state_after_sha256,
                )
            ]
        )
        return
    client = anchor_client or _default_anchor_client
    try:
        if not endpoint.startswith("https://"):
            raise LivePrerequisiteError("Time-anchor endpoint must use HTTPS")
        response = client(endpoint, request_payload)
        confirmed = _anchor_response(response, sealed_entry_sha256=seal.entry_sha256)
    except Exception as exc:
        ledger.append_transaction(
            [
                _event_spec(
                    event_key=f"{prefix}:failed",
                    event_type="anchor_failed",
                    prerequisites=prerequisites,
                    payload={
                        **request_payload,
                        "reason_code": "request_or_receipt_validation_failed",
                        "error_type": type(exc).__name__,
                        "retry_policy": "automatic_next_poll",
                    },
                    target_date_value=date.fromisoformat(str(seal.target_date)),
                    issue_id=seal.issue_id,
                    input_manifest_sha256=seal.input_manifest_sha256,
                    state_before_sha256=seal.state_after_sha256,
                    state_after_sha256=seal.state_after_sha256,
                )
            ]
        )
        return
    ledger.append_transaction(
        [
            _event_spec(
                event_key=f"{prefix}:confirmed",
                event_type="anchor_confirmed",
                prerequisites=prerequisites,
                payload={**request_payload, **confirmed},
                target_date_value=date.fromisoformat(str(seal.target_date)),
                issue_id=seal.issue_id,
                input_manifest_sha256=seal.input_manifest_sha256,
                state_before_sha256=seal.state_after_sha256,
                state_after_sha256=seal.state_after_sha256,
            )
        ]
    )
    _ensure_anchor_receipt_file(paths, seal, confirmed)


def _append_outcome_batch(
    ledger: AppendOnlyLedger,
    profile: dict[str, Any],
    prerequisites: Prerequisites,
    projection: LiveProjection,
    outcome: OutcomeBatch,
) -> None:
    target = projection.outstanding_target_date
    seal = projection.seal_event
    if target is None or seal is None or projection.outstanding_issue_id is None:
        raise LiveIntegrityError("Cannot reveal an outcome without a durable issue seal")
    if outcome.target_date != target:
        raise LiveInputError("Outcome target does not match the outstanding issue")
    stations = profile["stations"]
    if list(projection.issue_events) != stations:
        raise LiveIntegrityError("Outstanding issue station order changed")
    initial_states = projection.states
    initial_state_hash = _states_sha256(initial_states, stations)
    issues = {
        station: _decode_issue(
            projection.issue_events[station].payload["issue"],
            name=f"outstanding.issue.{station}",
        )
        for station in stations
    }
    reveals = {
        station: reveal_station(
            initial_states[station], issues[station], outcome.actual_by_station[station]
        )
        for station in stations
    }
    next_states = {station: reveals[station].next_state for station in stations}
    anomaly_scores = {
        station: reveals[station].anomaly_score for station in stations
    }
    site = aggregate_site_scores(anomaly_scores)
    next_state_hash = _states_sha256(next_states, stations)
    anchor_payload = projection.anchored_seal_hashes.get(seal.entry_sha256)
    engineering_blind_candidate = False
    seal_precedes_target = False
    anchor_follows_seal = False
    anchor_precedes_target = False
    if anchor_payload is not None:
        seal_time = _parse_utc(
            seal.recorded_at_utc, name="issue_seal.recorded_at_utc"
        )
        anchor_time = _parse_utc(
            anchor_payload.get("anchored_at_utc"), name="anchor.anchored_at_utc"
        )
        outcome_available = _parse_utc(
            outcome.source_available_at_utc, name="outcome.source_available_at_utc"
        )
        target_start = datetime.combine(
            target,
            datetime.min.time(),
            tzinfo=ZoneInfo(profile["target"]["date_timezone"]),
        ).astimezone(timezone.utc)
        seal_precedes_target = seal_time < target_start
        anchor_follows_seal = anchor_time >= seal_time
        anchor_precedes_target = anchor_time < target_start
        engineering_blind_candidate = (
            seal_precedes_target
            and anchor_follows_seal
            and anchor_precedes_target
            and anchor_time < outcome_available
        )
    # E2-A validates machine ordering and the returned JSON shape only.  It has
    # no pinned provider or cryptographic receipt verifier, so it must not turn
    # an arbitrary HTTPS response into scientific live evidence.
    trusted_anchor_receipt_verified = False
    e2_live_evidence_eligible = (
        engineering_blind_candidate and trusted_anchor_receipt_verified
    )
    prefix = f"{projection.epoch_id}:{target.isoformat()}"
    specs: list[EventSpec] = [
        _event_spec(
            event_key=f"{prefix}:outcome_batch_opened:{outcome.source_revision_id}",
            event_type="outcome_batch_opened",
            prerequisites=prerequisites,
            payload={
                "outcome_batch_sha256": outcome.sha256,
                "outcome_source_id": outcome.outcome_source_id,
                "source_revision_id": outcome.source_revision_id,
                "source_available_at_utc": outcome.source_available_at_utc,
                "finalized_at_utc": outcome.finalized_at_utc,
                "all_eight_outcomes_loaded_before_append": True,
                "issue_batch_sealed_entry_sha256": seal.entry_sha256,
            },
            target_date_value=target,
            issue_id=projection.outstanding_issue_id,
            input_manifest_sha256=outcome.source_manifest.sha256,
            state_before_sha256=initial_state_hash,
            state_after_sha256=initial_state_hash,
        )
    ]
    # The outcome batch is already fully loaded and validated above.  Append all
    # eight reveal records before any score or state update event.
    for station in stations:
        issue_event = projection.issue_events[station]
        specs.append(
            _event_spec(
                event_key=(
                    f"{prefix}:outcome_revealed:{outcome.source_revision_id}:{station}"
                ),
                event_type="outcome_revealed",
                prerequisites=prerequisites,
                payload={
                    "station": station,
                    "actual_mm": outcome.actual_by_station[station],
                    "outcome_batch_sha256": outcome.sha256,
                    "outcome_source_id": outcome.outcome_source_id,
                    "source_revision_id": outcome.source_revision_id,
                    "source_observed_at_utc": outcome.source_observed_at_utc,
                    "source_available_at_utc": outcome.source_available_at_utc,
                    "finalized_at_utc": outcome.finalized_at_utc,
                    "issue_batch_sealed_entry_sha256": seal.entry_sha256,
                },
                target_date_value=target,
                station=station,
                issue_id=issue_event.issue_id,
                input_manifest_sha256=outcome.source_manifest.sha256,
                state_before_sha256=issues[station].state_before_sha256,
                state_after_sha256=issues[station].state_before_sha256,
            )
        )
    for station in stations:
        issue_event = projection.issue_events[station]
        issue = issues[station]
        reveal = reveals[station]
        score_payload = {
            key: value
            for key, value in asdict(reveal).items()
            if key not in {"updated_state", "next_state"}
        }
        specs.extend(
            [
                _event_spec(
                    event_key=(
                        f"{prefix}:score:{outcome.source_revision_id}:{station}"
                    ),
                    event_type="score_recorded",
                    prerequisites=prerequisites,
                    payload={
                        "station": station,
                        "source_revision_id": outcome.source_revision_id,
                        "score": score_payload,
                        "artifact_status": profile["artifact_status"],
                        "formal_warning_output": False,
                    },
                    target_date_value=target,
                    station=station,
                    issue_id=issue_event.issue_id,
                    input_manifest_sha256=outcome.source_manifest.sha256,
                    state_before_sha256=issue.state_before_sha256,
                    state_after_sha256=issue.state_before_sha256,
                ),
                _event_spec(
                    event_key=(
                        f"{prefix}:expert_state:{outcome.source_revision_id}:{station}"
                    ),
                    event_type="expert_state_updated",
                    prerequisites=prerequisites,
                    payload={
                        "station": station,
                        "cumulative_loss_after": list(
                            reveal.updated_state.cumulative_loss
                        ),
                        "update_affects_future_dates_only": True,
                    },
                    target_date_value=target,
                    station=station,
                    issue_id=issue_event.issue_id,
                    input_manifest_sha256=outcome.source_manifest.sha256,
                    state_before_sha256=issue.state_before_sha256,
                    state_after_sha256=reveal.updated_state_sha256,
                ),
                _event_spec(
                    event_key=(
                        f"{prefix}:conformal_state:{outcome.source_revision_id}:{station}"
                    ),
                    event_type="conformal_state_updated",
                    prerequisites=prerequisites,
                    payload={
                        "station": station,
                        "alpha_after": reveal.aci_alpha_after_update,
                        "history_count_after": reveal.history_count_after_update,
                        "absolute_residual_history": list(
                            reveal.updated_state.absolute_residuals
                        ),
                        "underprediction_residual_history": list(
                            reveal.updated_state.underprediction_residuals
                        ),
                    },
                    target_date_value=target,
                    station=station,
                    issue_id=issue_event.issue_id,
                    input_manifest_sha256=outcome.source_manifest.sha256,
                    state_before_sha256=issue.state_before_sha256,
                    state_after_sha256=reveal.updated_state_sha256,
                ),
                _event_spec(
                    event_key=(
                        f"{prefix}:drift_state:{outcome.source_revision_id}:{station}"
                    ),
                    event_type="drift_state_updated",
                    prerequisites=prerequisites,
                    payload={
                        "station": station,
                        "drift_detected": reveal.drift_detected,
                        "drift_cut_index": reveal.drift_cut_index,
                        "updated_state": _state_payload(reveal.updated_state),
                        "effective_next_state": _state_payload(reveal.next_state),
                        "reset_applies_next_target_date": True,
                    },
                    target_date_value=target,
                    station=station,
                    issue_id=issue_event.issue_id,
                    input_manifest_sha256=outcome.source_manifest.sha256,
                    state_before_sha256=reveal.updated_state_sha256,
                    state_after_sha256=reveal.state_after_sha256,
                ),
            ]
        )
    specs.extend(
        [
            _event_spec(
                event_key=f"{prefix}:site_score:{outcome.source_revision_id}",
                event_type="site_score_recorded",
                prerequisites=prerequisites,
                payload={
                    "site": _site_payload(site),
                    "continuous_score_only": True,
                    "warning_color_output": False,
                },
                target_date_value=target,
                issue_id=projection.outstanding_issue_id,
                input_manifest_sha256=outcome.source_manifest.sha256,
                state_before_sha256=initial_state_hash,
                state_after_sha256=next_state_hash,
            ),
            _event_spec(
                event_key=f"{prefix}:outcome_batch_settled:{outcome.source_revision_id}",
                event_type="outcome_batch_settled",
                prerequisites=prerequisites,
                payload={
                    "outcome_batch_sha256": outcome.sha256,
                    "outcome_source_id": outcome.outcome_source_id,
                    "source_revision_id": outcome.source_revision_id,
                    "source_available_at_utc": outcome.source_available_at_utc,
                    "actual_by_station": outcome.actual_by_station,
                    "states_after": _states_payload(next_states, stations),
                    "issue_batch_sealed_entry_sha256": seal.entry_sha256,
                    "anchor_confirmed": anchor_payload is not None,
                    "seal_precedes_target_natural_day": seal_precedes_target,
                    "anchor_follows_durable_seal": anchor_follows_seal,
                    "anchor_precedes_target_natural_day": anchor_precedes_target,
                    "externally_anchored_before_outcome": engineering_blind_candidate,
                    "engineering_blind_time_order_candidate": (
                        engineering_blind_candidate
                    ),
                    "trusted_anchor_receipt_verified": (
                        trusted_anchor_receipt_verified
                    ),
                    "e2_live_evidence_eligible": e2_live_evidence_eligible,
                    "formal_warning_output": False,
                },
                target_date_value=target,
                issue_id=projection.outstanding_issue_id,
                input_manifest_sha256=outcome.source_manifest.sha256,
                state_before_sha256=initial_state_hash,
                state_after_sha256=next_state_hash,
            ),
        ]
    )
    ledger.append_transaction(specs)


def _find_original_issue_events(
    events: tuple[LedgerEvent, ...], target: date, stations: list[str]
) -> dict[str, LedgerEvent]:
    result = {
        str(event.station): event
        for event in events
        if event.event_type == "station_issue"
        and event.target_date == target.isoformat()
    }
    if list(result) != stations:
        raise LiveIntegrityError("Settled issue projection lacks eight station issues")
    return result


def _append_revision(
    ledger: AppendOnlyLedger,
    profile: dict[str, Any],
    prerequisites: Prerequisites,
    projection: LiveProjection,
    outcome: OutcomeBatch,
) -> None:
    target_key = outcome.target_date.isoformat()
    known = projection.revision_ids[target_key]
    if outcome.source_revision_id in known:
        if known[outcome.source_revision_id] != outcome.sha256:
            raise LiveIntegrityError("Outcome revision id was reused with changed bytes")
        return
    events = ledger.read_events()
    issue_events = _find_original_issue_events(events, outcome.target_date, profile["stations"])
    previous_actuals = _require_exact_keys(
        projection.latest_actuals_by_date.get(target_key),
        set(profile["stations"]),
        name="revision.previous_actuals",
    )
    current_states = projection.states
    prefix = f"{projection.epoch_id}:{target_key}:revision:{outcome.source_revision_id}"
    specs: list[EventSpec] = []
    for station in profile["stations"]:
        issue = _decode_issue(
            issue_events[station].payload["issue"], name=f"revision.issue.{station}"
        )
        actual = outcome.actual_by_station[station]
        state_hash = station_state_sha256_v1(current_states[station])
        interval_covered = (
            None
            if issue.interval_lower_mm is None or issue.interval_upper_mm is None
            else issue.interval_lower_mm <= actual <= issue.interval_upper_mm
        )
        specs.extend(
            [
                _event_spec(
                    event_key=f"{prefix}:outcome_revision:{station}",
                    event_type="outcome_revision",
                    prerequisites=prerequisites,
                    payload={
                        "station": station,
                        "source_revision_id": outcome.source_revision_id,
                        "outcome_batch_sha256": outcome.sha256,
                        "previous_actual_mm": _require_finite_float(
                            previous_actuals[station],
                            name=f"previous_actual.{station}",
                        ),
                        "revised_actual_mm": actual,
                        "source_available_at_utc": outcome.source_available_at_utc,
                        "live_online_state_rewritten": False,
                    },
                    target_date_value=outcome.target_date,
                    station=station,
                    issue_id=issue_events[station].issue_id,
                    input_manifest_sha256=outcome.source_manifest.sha256,
                    state_before_sha256=state_hash,
                    state_after_sha256=state_hash,
                ),
                _event_spec(
                    event_key=f"{prefix}:revision_rescore:{station}",
                    event_type="revision_rescore_recorded",
                    prerequisites=prerequisites,
                    payload={
                        "station": station,
                        "source_revision_id": outcome.source_revision_id,
                        "view": "revised_retrospective_view",
                        "point_absolute_error_mm": abs(
                            actual - issue.point_forecast_mm
                        ),
                        "persistence_absolute_error_mm": abs(
                            actual - issue.fallback_persistence_mm
                        ),
                        "underprediction_residual_mm": max(
                            actual - issue.point_forecast_mm, 0.0
                        ),
                        "interval_covered": interval_covered,
                        "updates_live_state": False,
                        "blind_metric_eligible": False,
                    },
                    target_date_value=outcome.target_date,
                    station=station,
                    issue_id=issue_events[station].issue_id,
                    input_manifest_sha256=outcome.source_manifest.sha256,
                    state_before_sha256=state_hash,
                    state_after_sha256=state_hash,
                ),
            ]
        )
    ledger.append_transaction(specs)


def _append_backfill_revision(
    ledger: AppendOnlyLedger,
    profile: dict[str, Any],
    prerequisites: Prerequisites,
    projection: LiveProjection,
    outcome: OutcomeBatch,
) -> None:
    target_key = outcome.target_date.isoformat()
    known = projection.revision_ids[target_key]
    if outcome.source_revision_id in known:
        if known[outcome.source_revision_id] != outcome.sha256:
            raise LiveIntegrityError("Outcome revision id was reused with changed bytes")
        return
    previous_actuals = _require_exact_keys(
        projection.latest_actuals_by_date.get(target_key),
        set(profile["stations"]),
        name="backfill_revision.previous_actuals",
    )
    prefix = f"{projection.epoch_id}:{target_key}:revision:{outcome.source_revision_id}"
    specs: list[EventSpec] = []
    for station in profile["stations"]:
        state_hash = station_state_sha256_v1(projection.states[station])
        specs.append(
            _event_spec(
                event_key=f"{prefix}:outcome_revision:{station}",
                event_type="outcome_revision",
                prerequisites=prerequisites,
                payload={
                    "station": station,
                    "source_revision_id": outcome.source_revision_id,
                    "outcome_batch_sha256": outcome.sha256,
                    "previous_actual_mm": _require_finite_float(
                        previous_actuals[station],
                        name=f"previous_backfill_actual.{station}",
                    ),
                    "revised_actual_mm": outcome.actual_by_station[station],
                    "source_available_at_utc": outcome.source_available_at_utc,
                    "original_classification": "backfill_not_blind",
                    "live_online_state_rewritten": False,
                    "retrospective_score_available": False,
                },
                target_date_value=outcome.target_date,
                station=station,
                input_manifest_sha256=outcome.source_manifest.sha256,
                state_before_sha256=state_hash,
                state_after_sha256=state_hash,
            )
        )
    ledger.append_transaction(specs)


def _process_revisions(
    ledger: AppendOnlyLedger,
    paths: RuntimePaths,
    profile: dict[str, Any],
    prerequisites: Prerequisites,
    projection: LiveProjection,
) -> None:
    originals = {
        **projection.backfill_events,
        **projection.settled_events,
    }
    for target_key, original in sorted(originals.items()):
        path = paths.outcome_inbox / f"{target_key}.json"
        if not path.is_file():
            continue
        outcome = load_outcome_batch(path, profile, prerequisites)
        if outcome.sha256 == original.payload.get("outcome_batch_sha256"):
            continue
        if outcome.target_date.isoformat() != target_key:
            raise LiveInputError("Revised outcome filename/date mismatch")
        if target_key in projection.settled_events:
            _append_revision(ledger, profile, prerequisites, projection, outcome)
        else:
            _append_backfill_revision(
                ledger, profile, prerequisites, projection, outcome
            )


def _has_later_dated_input(paths: RuntimePaths, expected: date) -> bool:
    for directory in (paths.issue_inbox, paths.outcome_inbox):
        for path in directory.glob("*.json"):
            try:
                candidate = date.fromisoformat(path.stem)
            except ValueError:
                continue
            if candidate > expected:
                return True
    return False


def _write_active_status(
    profile: dict[str, Any],
    paths: RuntimePaths,
    prerequisites: Prerequisites,
    ledger: AppendOnlyLedger,
    projection: LiveProjection,
    *,
    now: datetime,
    runner_status: str,
    reason: str,
) -> Path:
    events = ledger.read_events()
    head = events[-1] if events else None
    counts = Counter(event.event_type for event in events)
    seal_hash = (
        projection.seal_event.entry_sha256
        if projection.seal_event is not None
        else None
    )
    outstanding_anchor_confirmed = bool(
        seal_hash is not None and seal_hash in projection.anchored_seal_hashes
    )
    status = _base_status(
        profile,
        paths,
        now=now,
        runner_status=runner_status,
        reason=reason,
    )
    status.update(
        {
            "ledger_event_count": len(events),
            "ledger_terminal_sha256": (
                head.entry_sha256 if head is not None else ZERO_HASH
            ),
            "ledger_terminal_sequence_id": head.sequence_id if head else 0,
            "event_counts": dict(sorted(counts.items())),
            "live_epoch_id": projection.epoch_id,
            "source_snapshot_ready": True,
            "source_snapshot_sha256": prerequisites.source.sha256,
            "source_activation_watermark": prerequisites.source.watermark.isoformat(),
            "production_model_ready": True,
            "production_model_manifest_sha256": prerequisites.model.sha256,
            "production_model_version": prerequisites.model.model_version,
            "last_finalized_date": projection.last_finalized_date.isoformat(),
            "next_target_date": (
                projection.last_finalized_date + timedelta(days=1)
            ).isoformat(),
            "outstanding_target_date": (
                projection.outstanding_target_date.isoformat()
                if projection.outstanding_target_date is not None
                else None
            ),
            "external_anchor_confirmed": counts["anchor_confirmed"] > 0,
            "outstanding_issue_anchor_confirmed": outstanding_anchor_confirmed,
            "blind_settled_batch_count": projection.blind_settled_count,
            "engineering_blind_candidate_count": (
                projection.engineering_blind_candidate_count
            ),
            "backfill_not_blind_count": projection.backfill_count,
            "e2_live_evidence_accumulated": projection.blind_settled_count > 0,
            "formal_warning_output": False,
            "independent_label_used": False,
            "confirmatory_external_validation": False,
            "vajont_used": False,
        }
    )
    _atomic_write_json(paths.status, status)
    return paths.status


def _validate_activation_time(
    profile: dict[str, Any], prerequisites: Prerequisites, *, now: datetime
) -> None:
    if now.tzinfo is None:
        raise LiveIntegrityError("Machine clock must be timezone-aware")
    now_utc = now.astimezone(timezone.utc)
    local_today = now_utc.astimezone(
        ZoneInfo(profile["target"]["date_timezone"])
    ).date()
    captured = _parse_utc(
        prerequisites.source.captured_at_utc, name="source.captured_at_utc"
    )
    model_created = _parse_utc(
        prerequisites.model.created_at_utc, name="model.created_at_utc"
    )
    if prerequisites.source.watermark > local_today:
        raise LivePrerequisiteError("Source watermark is in the future")
    if captured > now_utc or model_created > now_utc:
        raise LivePrerequisiteError("Activation prerequisite is future-dated")
    if model_created < captured:
        raise LivePrerequisiteError(
            "Production model bundle predates its source snapshot"
        )


def _validate_issue_ready(issue: IssueBatch, *, now: datetime) -> None:
    generated = _parse_utc(issue.generated_at_utc, name="issue.generated_at_utc")
    if generated > now.astimezone(timezone.utc):
        raise LiveInputError("Issue batch is future-dated relative to the machine poll")


def _validate_outcome_ready(outcome: OutcomeBatch, *, now: datetime) -> None:
    finalized = _parse_utc(outcome.finalized_at_utc, name="outcome.finalized_at_utc")
    if finalized > now.astimezone(timezone.utc):
        raise LiveInputError("Outcome batch is not yet finalized at machine poll time")


def _poll_activated_epoch(
    profile: dict[str, Any],
    paths: RuntimePaths,
    prerequisites: Prerequisites,
    *,
    now: datetime,
    anchor_client: AnchorClient | None,
) -> Path:
    _validate_activation_time(profile, prerequisites, now=now)
    ledger = AppendOnlyLedger(paths.ledger)
    if ledger.head() is None:
        _append_genesis(ledger, profile, prerequisites)
    events = ledger.read_events()
    projection = _reconstruct_projection(events, profile, prerequisites)
    _repair_and_verify_anchor_receipts(paths, events)

    # Revisions are checked before a new issue but never rewrite online state.
    _process_revisions(ledger, paths, profile, prerequisites, projection)
    projection = _reconstruct_projection(
        ledger.read_events(), profile, prerequisites
    )

    if projection.outstanding_target_date is not None:
        _attempt_anchor(
            ledger,
            paths,
            profile,
            prerequisites,
            projection,
            anchor_client=anchor_client,
        )
        projection = _reconstruct_projection(
            ledger.read_events(), profile, prerequisites
        )
        outcome_path = (
            paths.outcome_inbox
            / f"{projection.outstanding_target_date.isoformat()}.json"
        )
        if not outcome_path.is_file():
            return _write_active_status(
                profile,
                paths,
                prerequisites,
                ledger,
                projection,
                now=now,
                runner_status="waiting_for_outcome",
                reason="durable_issue_batch_has_no_finalized_outcome",
            )
        outcome = load_outcome_batch(outcome_path, profile, prerequisites)
        _validate_outcome_ready(outcome, now=now)
        _append_outcome_batch(
            ledger, profile, prerequisites, projection, outcome
        )
        projection = _reconstruct_projection(
            ledger.read_events(), profile, prerequisites
        )

    # Consume contiguous backfill automatically.  It advances the accepted
    # source date but never creates a retrospective issue or online update.
    while projection.outstanding_target_date is None:
        expected = projection.last_finalized_date + timedelta(days=1)
        issue_path = paths.issue_inbox / f"{expected.isoformat()}.json"
        outcome_path = paths.outcome_inbox / f"{expected.isoformat()}.json"
        if outcome_path.is_file():
            outcome = load_outcome_batch(outcome_path, profile, prerequisites)
            _validate_outcome_ready(outcome, now=now)
            _append_backfill(
                ledger, profile, prerequisites, projection, outcome
            )
            projection = _reconstruct_projection(
                ledger.read_events(), profile, prerequisites
            )
            continue
        if not issue_path.is_file():
            status = (
                "waiting_for_missing_natural_day"
                if _has_later_dated_input(paths, expected)
                else "waiting_for_new_data"
            )
            reason = (
                "later_input_exists_but_next_natural_day_is_missing"
                if status == "waiting_for_missing_natural_day"
                else "no_issue_or_outcome_for_next_natural_day"
            )
            return _write_active_status(
                profile,
                paths,
                prerequisites,
                ledger,
                projection,
                now=now,
                runner_status=status,
                reason=reason,
            )
        local_today = now.astimezone(
            ZoneInfo(profile["target"]["date_timezone"])
        ).date()
        if expected <= local_today:
            return _write_active_status(
                profile,
                paths,
                prerequisites,
                ledger,
                projection,
                now=now,
                runner_status="waiting_for_backfill_outcome",
                reason="historical_target_cannot_be_retroactively_issued",
            )
        issue_batch = load_issue_batch(issue_path, profile, prerequisites)
        _validate_issue_ready(issue_batch, now=now)
        if issue_batch.target_date != expected or issue_path.stem != expected.isoformat():
            raise LiveInputError("Issue filename/date does not match the next target")
        _append_issue_batch(
            ledger, profile, prerequisites, projection, issue_batch
        )
        projection = _reconstruct_projection(
            ledger.read_events(), profile, prerequisites
        )
        _attempt_anchor(
            ledger,
            paths,
            profile,
            prerequisites,
            projection,
            anchor_client=anchor_client,
        )
        projection = _reconstruct_projection(
            ledger.read_events(), profile, prerequisites
        )
        # The outcome path was absent before durable issue creation.  Only now
        # is it permitted to be loaded if it arrived concurrently after seal.
        if not outcome_path.is_file():
            return _write_active_status(
                profile,
                paths,
                prerequisites,
                ledger,
                projection,
                now=now,
                runner_status="waiting_for_outcome",
                reason="issue_sealed_anchor_attempted_outcome_not_available",
            )
        outcome = load_outcome_batch(outcome_path, profile, prerequisites)
        _validate_outcome_ready(outcome, now=now)
        _append_outcome_batch(
            ledger, profile, prerequisites, projection, outcome
        )
        projection = _reconstruct_projection(
            ledger.read_events(), profile, prerequisites
        )

    raise LiveIntegrityError("Live poll reached an impossible lifecycle state")


def poll_live_runner(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    runtime_root: Path | None = None,
    clock: Clock | None = None,
    anchor_client: AnchorClient | None = None,
) -> Path:
    """Run one automatic E2-A poll and return the atomically refreshed status."""

    profile = load_config(config_path)
    paths = runtime_paths(profile, runtime_root=runtime_root)
    _ensure_runtime_directories(paths)
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    lock_handle = _acquire_runner_lock(paths.lock)
    try:
        try:
            prerequisites = load_prerequisites(profile, paths)
            if prerequisites is None:
                if paths.ledger.exists():
                    raise LivePrerequisiteError(
                        "An activated ledger exists but an epoch prerequisite disappeared"
                    )
                return _write_waiting_for_prerequisites(profile, paths, now=now)
            return _poll_activated_epoch(
                profile,
                paths,
                prerequisites,
                now=now,
                anchor_client=anchor_client,
            )
        except (
            LedgerError,
            LiveConfigError,
            LivePrerequisiteError,
            LiveInputError,
            LiveIntegrityError,
        ) as exc:
            normalized: Exception = exc
            if isinstance(exc, LedgerError):
                normalized = LiveIntegrityError(
                    f"Append-only ledger rejected the poll: {exc}"
                )
            status = _base_status(
                profile,
                paths,
                now=now,
                runner_status="blocked_integrity",
                reason=f"{type(normalized).__name__}:{normalized}",
            )
            if paths.ledger.is_file():
                try:
                    ledger = AppendOnlyLedger(paths.ledger)
                    head = ledger.head()
                    events = ledger.read_events()
                    status["ledger_event_count"] = len(events)
                    status["ledger_terminal_sha256"] = (
                        head.entry_sha256 if head is not None else ZERO_HASH
                    )
                except LedgerError:
                    status["ledger_validation_failed"] = True
            _atomic_write_json(paths.status, status)
            if normalized is not exc:
                raise normalized from exc
            raise
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        status_path = poll_live_runner(config_path=args.config)
    except (LiveConfigError, LivePrerequisiteError, LiveInputError, LiveIntegrityError) as exc:
        print(f"[prequential-live] blocked: {exc}", file=sys.stderr)
        return 2
    except LiveRunnerBusy as exc:
        print(f"[prequential-live] busy: {exc}", file=sys.stderr)
        return 3
    payload = _load_json(status_path, name="live status")
    print(
        "[prequential-live] "
        f"status={payload['runner_status']} ledger_events={payload['ledger_event_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ArtifactRecord",
    "IssueBatch",
    "LiveConfigError",
    "LiveInputError",
    "LiveIntegrityError",
    "LivePrerequisiteError",
    "LiveRunnerBusy",
    "ModelBundle",
    "OutcomeBatch",
    "Prerequisites",
    "RuntimePaths",
    "SourceSnapshot",
    "VerifiedLedgerProjection",
    "load_config",
    "load_issue_batch",
    "load_model_bundle",
    "load_outcome_batch",
    "load_prerequisites",
    "load_source_snapshot",
    "load_verified_ledger_projection",
    "poll_live_runner",
    "runtime_paths",
]
