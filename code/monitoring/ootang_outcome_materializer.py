"""Machine-only E2-B2 outcome materialization for the Ootang live ledger.

The materializer owns no scientific judgement.  It selects exactly one finalized
record from the recursively verified current source, checks the existing E2-A
ledger through its public read-only replay API, and publishes canonical outcome
bytes through an immutable receipt registry.  A date-named inbox file is only an
active copy; the per-(date, revision) receipt and exact object are the durable
history used for crash recovery and revision lineage.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
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


ROOT = Path(__file__).resolve().parents[2]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

DEFAULT_CONFIG_PATH = ROOT / "config" / "ootang_prequential_cycle.v1.json"
DEFAULT_CONFIG_SHA256 = (
    "2e4a0da22034a3063f612a723f007bf20b600c1dbdb7c62761368aa7a37810ef"
)
HEX_DIGITS = frozenset("0123456789abcdef")
STATUS_SCHEMA_VERSION = "ootang_outcome_materializer_status_v1"
IMPLEMENTATION_SCHEMA_VERSION = "ootang_outcome_materializer_implementation_v1"
SCIENTIFIC_SEMANTICS_SCHEMA_VERSION = "ootang_outcome_scientific_semantics_v1"
ACTIVE_RECEIPT_POINTER_SCHEMA_VERSION = "ootang_outcome_active_receipt_pointer_v1"


class OutcomeMaterializerError(RuntimeError):
    """Base class for fail-closed outcome materialization errors."""


class OutcomeMaterializerConfigError(OutcomeMaterializerError):
    """The E2-B2 cycle contract or a bound profile changed."""


class OutcomeMaterializerInputError(OutcomeMaterializerError):
    """A source, ledger, clock, or registered artifact is invalid."""


class OutcomeMaterializerConflict(OutcomeMaterializerError):
    """Immutable revision history conflicts with current candidate bytes."""


class OutcomeMaterializerBusy(OutcomeMaterializerError):
    """Another deploy or E2-A ledger writer currently owns a required lock."""


@dataclass(frozen=True)
class Artifact:
    path: Path
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class OutcomeMaterializerResult:
    status_path: Path
    status: str
    target_date: date | None
    outcome_path: Path | None


@dataclass(frozen=True)
class _Selection:
    kind: str
    target_date: date
    record: object
    previous_revision_id: str | None = None
    previous_outcome_sha256: str | None = None


@dataclass(frozen=True)
class _RegisteredOutcome:
    receipt: Artifact
    exact_object: Artifact
    payload: dict[str, Any]
    raw: bytes
    scientific_semantics_sha256: str
    revision_sequence_id: int
    previous_receipt: Artifact | None


@dataclass(frozen=True)
class _ReceiptChain:
    target_date: date
    receipts: tuple[_RegisteredOutcome, ...]
    tip: _RegisteredOutcome
    active_pointer_path: Path
    active_pointer_raw: bytes | None
    pointed: _RegisteredOutcome | None


@dataclass(frozen=True)
class _PublicationState:
    active_path: Path
    previous_active_raw: bytes | None
    published_active_raw: bytes
    pointer_path: Path
    previous_pointer_raw: bytes | None
    published_pointer_raw: bytes
    changed: bool


Clock = Callable[[], datetime]


class _MonotonicClock:
    """Enforce non-decreasing UTC observations across one full invocation."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self.last: datetime | None = None

    def sample(self) -> datetime:
        observed = _clock_value(self._clock()).astimezone(timezone.utc)
        if self.last is not None and observed < self.last:
            raise OutcomeMaterializerInputError(
                "Machine clock moved backwards during outcome materializer invocation"
            )
        self.last = observed
        return observed


_EXPECTED_CYCLE_PROFILE: dict[str, Any] = {
    "schema_version": "ootang_prequential_cycle_profile_v1",
    "profile_id": "ootang-prequential-cycle-v1",
    "profile_version": "1.0.0-engineering",
    "case": "ootang",
    "artifact_status": "e2b2_engineering_only_not_live_evidence",
    "formal_warning_output": False,
    "independent_label_used": False,
    "confirmatory_external_validation": False,
    "vajont_used": False,
    "default_pipeline_member": False,
    "deploy_profile": {
        "path": "config/ootang_prequential_deploy.v1.json",
        "expected_sha256": (
            "60f17602998e976f06d590b7611dfb4480505c21d41a9b05420bd93cf831f940"
        ),
    },
    "live_profile": {
        "path": "config/ootang_prequential_live.v1.json",
        "expected_sha256": (
            "bf7c60a19e26e9a54fc4e1980b3556d6e6d1e3fec4b3a3a7f3de0dbb9b83cf00"
        ),
    },
    "runtime": {
        "root": "runtime/ootang_prequential_live_v1",
        "outcome_inbox": "outcome_inbox",
        "outcome_status": "outcome_materializer_status.json",
        "outcome_receipts": "outcome_receipts",
        "objects": "objects/sha256",
        "cycle_status": "cycle_status.json",
        "cycle_lock": "prequential_cycle.lock",
    },
    "outcome": {
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
    },
    "cycle": {
        "stage_order": [
            "source_ingest",
            "bundle_ensure",
            "live_reconcile_before_outcome",
            "outcome_materialize",
            "live_reconcile_after_outcome",
            "issue_produce",
            "live_seal_issue",
        ],
        "max_iterations": 64,
        "stable_iterations_to_stop": 1,
        "progress_token_schema_version": "ootang_prequential_cycle_progress_v1",
        "busy_exit_code": 3,
        "blocked_exit_code": 2,
    },
    "engineering_capabilities": {
        "outcome_materializer_implemented": True,
        "automatic_cycle_orchestration_implemented": True,
        "runner_independent_checkpoint_inference_replayed": False,
        "trusted_anchor_receipt_verified": False,
        "automatic_epoch_rotation_implemented": False,
        "e2_live_evidence_eligible": False,
        "real_activation_ready": False,
    },
}


def _reject_constant(value: str) -> None:
    raise OutcomeMaterializerInputError(f"Forbidden JSON constant: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OutcomeMaterializerInputError(f"Duplicate JSON key: {key}")
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
        raise OutcomeMaterializerInputError(
            f"{name} is not strict UTF-8 JSON"
        ) from exc
    if not isinstance(value, dict):
        raise OutcomeMaterializerInputError(f"{name} must be a JSON object")
    _reject_nonfinite(value, name=name)
    return value


def _reject_nonfinite(value: object, *, name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise OutcomeMaterializerInputError(f"{name} contains a non-finite number")
    if isinstance(value, dict):
        for key, child in value.items():
            _reject_nonfinite(child, name=f"{name}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_nonfinite(child, name=f"{name}[{index}]")


def _read_json(path: Path, *, name: str) -> tuple[dict[str, Any], bytes]:
    try:
        with path.open("rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise OutcomeMaterializerInputError(f"Cannot read {name}: {path}") from exc
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
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise OutcomeMaterializerInputError(
            "Payload is not canonical finite JSON"
        ) from exc


def _canonical_digest(value: Any) -> str:
    try:
        raw = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise OutcomeMaterializerInputError(
            "Payload is not canonical finite JSON"
        ) from exc
    return hashlib.sha256(raw).hexdigest()


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
        raise OutcomeMaterializerInputError(f"Cannot hash artifact: {path}") from exc
    return digest.hexdigest(), size


def _artifact_from_path(path: Path) -> Artifact:
    resolved = path.resolve()
    sha256, size = _sha256_file(resolved)
    return Artifact(resolved, sha256, size)


def _artifact_payload(artifact: Artifact | object) -> dict[str, Any]:
    path = getattr(artifact, "path", None)
    sha256 = getattr(artifact, "sha256", None)
    size = getattr(artifact, "size_bytes", None)
    if not isinstance(path, Path):
        path = Path(path) if isinstance(path, str) else None
    if (
        path is None
        or not isinstance(sha256, str)
        or isinstance(size, bool)
        or not isinstance(size, int)
    ):
        raise OutcomeMaterializerInputError("Artifact record is incomplete")
    return {
        "path": str(path.resolve()),
        "sha256": sha256,
        "size_bytes": size,
    }


def _require_exact_keys(value: object, expected: set[str], *, name: str) -> dict:
    if not isinstance(value, dict):
        raise OutcomeMaterializerInputError(f"{name} must be an object")
    actual = set(value)
    if actual != expected:
        raise OutcomeMaterializerInputError(
            f"{name} keys changed; missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )
    return value


def _require_string(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise OutcomeMaterializerInputError(
            f"{name} must be a nonempty trimmed string"
        )
    return value


def _require_sha256(value: object, *, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in HEX_DIGITS for character in value)
    ):
        raise OutcomeMaterializerInputError(f"{name} must be a lowercase SHA-256")
    return value


def _parse_date(value: object, *, name: str) -> date:
    if not isinstance(value, str):
        raise OutcomeMaterializerInputError(f"{name} must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise OutcomeMaterializerInputError(f"{name} must be YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise OutcomeMaterializerInputError(
            f"{name} must be canonical YYYY-MM-DD"
        )
    return parsed


def _parse_utc(value: object, *, name: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise OutcomeMaterializerInputError(
            f"{name} must be RFC 3339 UTC ending in Z"
        )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise OutcomeMaterializerInputError(f"{name} is not a UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise OutcomeMaterializerInputError(f"{name} must be UTC")
    return parsed


def _format_utc(value: datetime) -> str:
    checked = _clock_value(value)
    return (
        checked.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _clock_value(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise OutcomeMaterializerInputError(
            "Machine clock must return a timezone-aware datetime"
        )
    if value.utcoffset() is None:
        raise OutcomeMaterializerInputError("Machine clock timezone is invalid")
    return value


def _finite(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OutcomeMaterializerInputError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise OutcomeMaterializerInputError(f"{name} must be finite")
    return result


def _resolve(path: str | Path, *, base: Path) -> Path:
    candidate = Path(path)
    return candidate.resolve() if candidate.is_absolute() else (base / candidate).resolve()


def _confined_child(root: Path, relative: object, *, name: str) -> Path:
    text = (
        str(relative)
        if isinstance(relative, Path)
        else _require_string(relative, name=name)
    )
    candidate = Path(text)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        raise OutcomeMaterializerConfigError(
            f"{name} must be a confined relative runtime path"
        )
    try:
        resolved_root = root.resolve()
        lexical = resolved_root / candidate
        cursor = resolved_root
        for part in candidate.parts:
            cursor /= part
            if cursor.is_symlink():
                raise OutcomeMaterializerConfigError(
                    f"{name} must not traverse a symlink"
                )
        resolved = lexical.resolve(strict=False)
        resolved.relative_to(resolved_root)
    except OutcomeMaterializerConfigError:
        raise
    except (OSError, RuntimeError) as exc:
        raise OutcomeMaterializerConfigError(
            f"{name} cannot be safely resolved below its trusted root"
        ) from exc
    except ValueError as exc:
        raise OutcomeMaterializerConfigError(
            f"{name} escapes the runtime root"
        ) from exc
    return resolved


def _artifact_from_mapping(value: object, *, name: str) -> Artifact:
    record = _require_exact_keys(
        value, {"path", "sha256", "size_bytes"}, name=name
    )
    path = Path(_require_string(record["path"], name=f"{name}.path")).resolve()
    sha256 = _require_sha256(record["sha256"], name=f"{name}.sha256")
    size = record["size_bytes"]
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise OutcomeMaterializerInputError(
            f"{name}.size_bytes must be a nonnegative integer"
        )
    actual_sha, actual_size = _sha256_file(path)
    if (actual_sha, actual_size) != (sha256, size):
        raise OutcomeMaterializerConflict(f"{name} artifact bytes changed")
    return Artifact(path, sha256, size)


def load_config(
    path: Path = DEFAULT_CONFIG_PATH, *, project_root: Path = ROOT
) -> dict[str, Any]:
    """Load the exact E2-B2 contract and both profiles bound by its SHA values."""

    resolved = path.resolve()
    payload, raw = _read_json(resolved, name="E2-B2 cycle profile")
    if payload != _EXPECTED_CYCLE_PROFILE:
        raise OutcomeMaterializerConfigError(
            "E2-B2 cycle profile fixed fields changed"
        )
    profile_sha = _sha256_bytes(raw)
    if profile_sha != DEFAULT_CONFIG_SHA256:
        raise OutcomeMaterializerConfigError(
            "E2-B2 cycle profile bytes differ from the reviewed v1 file"
        )
    runtime = payload["runtime"]
    configured_root = _resolve(runtime["root"], base=project_root)
    for key in (
        "outcome_inbox",
        "outcome_status",
        "outcome_receipts",
        "objects",
        "cycle_status",
        "cycle_lock",
    ):
        _confined_child(configured_root, runtime[key], name=f"runtime.{key}")

    from monitoring import ootang_live_source as source_module
    from monitoring import ootang_prequential_live as live_module

    deploy_contract = payload["deploy_profile"]
    deploy_path = _resolve(deploy_contract["path"], base=project_root)
    deploy_artifact = _artifact_from_path(deploy_path)
    if deploy_artifact.sha256 != deploy_contract["expected_sha256"]:
        raise OutcomeMaterializerConfigError("Bound E2-B deploy profile SHA changed")
    try:
        deploy_profile = source_module.load_deploy_profile(
            deploy_path, project_root=project_root
        )
    except source_module.SourceError as exc:
        raise OutcomeMaterializerConfigError(
            f"Bound E2-B deploy profile is invalid: {exc}"
        ) from exc

    live_contract = payload["live_profile"]
    live_path = _resolve(live_contract["path"], base=project_root)
    live_artifact = _artifact_from_path(live_path)
    if live_artifact.sha256 != live_contract["expected_sha256"]:
        raise OutcomeMaterializerConfigError("Bound E2-A live profile SHA changed")
    try:
        live_profile = live_module.load_config(live_path)
    except live_module.LiveConfigError as exc:
        raise OutcomeMaterializerConfigError(
            f"Bound E2-A live profile is invalid: {exc}"
        ) from exc

    deploy_runtime = deploy_profile["runtime"]
    live_runtime = live_profile["runtime"]
    if (
        runtime["root"] != deploy_runtime["root"]
        or runtime["root"] != live_runtime["root"]
        or runtime["outcome_inbox"] != live_runtime["outcome_inbox"]
        or runtime["objects"] != deploy_runtime["objects"]
    ):
        raise OutcomeMaterializerConfigError(
            "E2-B2 runtime is not identical to its deploy/live namespace"
        )

    payload["_profile_path"] = str(resolved)
    payload["_profile_sha256"] = profile_sha
    payload["_project_root"] = str(project_root.resolve())
    payload["_deploy_path"] = str(deploy_path)
    payload["_live_path"] = str(live_path)
    payload["_deploy_profile"] = deploy_profile
    payload["_live_profile"] = live_profile
    return payload


def _runtime_root(
    profile: Mapping[str, Any], runtime_root: Path | None, *, project_root: Path
) -> Path:
    if runtime_root is not None:
        return runtime_root.resolve()
    return _resolve(profile["runtime"]["root"], base=project_root)


def _runtime_path(profile: Mapping[str, Any], root: Path, key: str) -> Path:
    return _confined_child(root, profile["runtime"][key], name=f"runtime.{key}")


def _atomic_write_bytes(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_create_bytes(path: Path, raw: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
            created = True
        except FileExistsError:
            created = False
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return created
    finally:
        if temporary.exists():
            temporary.unlink()


def _acquire_lock(path: Path, *, name: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise OutcomeMaterializerBusy(f"Another {name} cycle is running") from exc
    return handle


def _object_path(object_root: Path, digest: str, *, suffix: str) -> Path:
    _require_sha256(digest, name="object digest")
    return _confined_child(
        object_root,
        f"{digest}.{suffix}",
        name="content-addressed object",
    )


def _materialize_object(object_root: Path, raw: bytes, *, suffix: str) -> Artifact:
    digest = _sha256_bytes(raw)
    path = _object_path(object_root, digest, suffix=suffix)
    if not path.exists() and _atomic_create_bytes(path, raw):
        return Artifact(path, digest, len(raw))
    if not path.is_file():
        raise OutcomeMaterializerConflict(
            "Content-addressed object path is not a regular file"
        )
    try:
        existing = path.read_bytes()
    except OSError as exc:
        raise OutcomeMaterializerConflict(
            "Content-addressed object cannot be read"
        ) from exc
    if existing != raw or _sha256_bytes(existing) != digest:
        raise OutcomeMaterializerConflict("Content-addressed object is corrupted")
    return Artifact(path, digest, len(raw))


def _runtime_version_record() -> dict[str, str]:
    try:
        import numpy as np
        import pandas as pd
        import torch
    except ImportError as exc:
        raise OutcomeMaterializerInputError(
            "Outcome materializer runtime dependency is unavailable"
        ) from exc
    return {
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "numpy_version": str(np.__version__),
        "pandas_version": str(pd.__version__),
        "torch_version": str(torch.__version__),
    }


def _implementation_record(profile: Mapping[str, Any]) -> dict[str, Any]:
    artifacts = {
        "cycle_profile": _artifact_from_path(Path(profile["_profile_path"])),
        "deploy_profile": _artifact_from_path(Path(profile["_deploy_path"])),
        "live_profile": _artifact_from_path(Path(profile["_live_path"])),
        "outcome_materializer": _artifact_from_path(Path(__file__)),
        "pyproject": _artifact_from_path(ROOT / "pyproject.toml"),
        "uv_lock": _artifact_from_path(ROOT / "uv.lock"),
    }
    if artifacts["cycle_profile"].sha256 != profile["_profile_sha256"]:
        raise OutcomeMaterializerInputError(
            "Cycle profile changed after its validated snapshot"
        )
    if artifacts["deploy_profile"].sha256 != profile["deploy_profile"]["expected_sha256"]:
        raise OutcomeMaterializerInputError(
            "Deploy profile changed after cycle validation"
        )
    if artifacts["live_profile"].sha256 != profile["live_profile"]["expected_sha256"]:
        raise OutcomeMaterializerInputError(
            "Live profile changed after cycle validation"
        )
    return {
        "schema_version": IMPLEMENTATION_SCHEMA_VERSION,
        **{name: _artifact_payload(value) for name, value in artifacts.items()},
        "runtime": _runtime_version_record(),
    }


def _validate_implementation(value: object) -> dict[str, Any]:
    names = {
        "cycle_profile",
        "deploy_profile",
        "live_profile",
        "outcome_materializer",
        "pyproject",
        "uv_lock",
    }
    record = _require_exact_keys(
        value, {"schema_version", "runtime", *names}, name="implementation"
    )
    if record["schema_version"] != IMPLEMENTATION_SCHEMA_VERSION:
        raise OutcomeMaterializerConflict("Implementation schema changed")
    artifacts = {
        name: _artifact_from_mapping(record[name], name=f"implementation.{name}")
        for name in names
    }
    expected_paths = {
        "outcome_materializer": Path(__file__).resolve(),
        "pyproject": (ROOT / "pyproject.toml").resolve(),
        "uv_lock": (ROOT / "uv.lock").resolve(),
    }
    for name, expected in expected_paths.items():
        if artifacts[name].path != expected:
            raise OutcomeMaterializerConflict(
                f"Implementation {name} path changed"
            )
    runtime = record["runtime"]
    expected_runtime = _runtime_version_record()
    if runtime != expected_runtime:
        raise OutcomeMaterializerConflict(
            "Outcome implementation runtime versions changed"
        )
    return {
        "schema_version": IMPLEMENTATION_SCHEMA_VERSION,
        **{f"{name}_sha256": artifact.sha256 for name, artifact in artifacts.items()},
        **expected_runtime,
    }


def _source_record_payload(
    record: object, *, record_schema_version: str, stations: Sequence[str]
) -> dict[str, Any]:
    displacement = getattr(record, "displacement_mm", None)
    if not isinstance(displacement, Mapping) or list(displacement) != list(stations):
        raise OutcomeMaterializerInputError(
            "Selected source record station order changed"
        )
    return {
        "schema_version": record_schema_version,
        "date": getattr(record, "day").isoformat(),
        "revision_id": _require_string(
            getattr(record, "revision_id", None), name="source.record.revision_id"
        ),
        "finalized": True,
        "observed_at_utc": _require_string(
            getattr(record, "observed_at_utc", None), name="source.record.observed_at"
        ),
        "available_at_utc": _require_string(
            getattr(record, "available_at_utc", None), name="source.record.available_at"
        ),
        "finalized_at_utc": _require_string(
            getattr(record, "finalized_at_utc", None), name="source.record.finalized_at"
        ),
        "rainfall_mm": _finite(
            getattr(record, "rainfall_mm", None), name="source.record.rainfall_mm"
        ),
        "reservoir_water_level_m": _finite(
            getattr(record, "reservoir_water_level_m", None),
            name="source.record.reservoir_water_level_m",
        ),
        "displacement_mm": {
            station: _finite(
                displacement[station], name=f"source.record.displacement.{station}"
            )
            for station in stations
        },
    }


def _manifest_science(manifest: Mapping[str, Any]) -> dict[str, Any]:
    source = manifest["source"]
    implementation = _validate_implementation(manifest["implementation"])
    return {
        "schema_version": SCIENTIFIC_SEMANTICS_SCHEMA_VERSION,
        "case": "ootang",
        "target_date": manifest["target_date"],
        "selection_kind": manifest["selection_kind"],
        "source_watermark": manifest["source_watermark"],
        "source_exported_at_utc": manifest["source_exported_at_utc"],
        "outcome_source_id": manifest["outcome_source_id"],
        "source_artifact_sha256": {
            name: source[name]["sha256"]
            for name in (
                "canonical_dataset",
                "semantic_manifest",
                "activation_source_manifest",
                "snapshot_receipt",
                "target_revision_receipt",
            )
        },
        "source_record": manifest["source_record"],
        "station_order": manifest["station_order"],
        "implementation": implementation,
    }


def _build_input_manifest(
    profile: Mapping[str, Any], source: object, selection: _Selection
) -> dict[str, Any]:
    deploy = profile["_deploy_profile"]
    stations = list(deploy["source_feed"]["station_order_live"])
    snapshot_receipt = getattr(source, "snapshot_receipt", None)
    if snapshot_receipt is None:
        raise OutcomeMaterializerInputError(
            "Current source does not expose its snapshot receipt tip"
        )
    target_revision_receipt: object | None = None
    for candidate in getattr(source, "revision_heads", ()):
        candidate_payload, _ = _read_json(
            Path(candidate.path), name="source revision receipt head"
        )
        if candidate_payload.get("target_date") == selection.target_date.isoformat():
            if target_revision_receipt is not None:
                raise OutcomeMaterializerInputError(
                    "Current source exposes duplicate target revision heads"
                )
            target_revision_receipt = candidate
    if target_revision_receipt is None:
        raise OutcomeMaterializerInputError(
            "Selected record has no append-only revision receipt head"
        )
    source_artifacts = {
        "canonical_dataset": _artifact_payload(getattr(source, "dataset")),
        "semantic_manifest": _artifact_payload(
            getattr(source, "semantic_manifest")
        ),
        "activation_source_manifest": _artifact_payload(
            getattr(source, "activation_manifest")
        ),
        "snapshot_receipt": _artifact_payload(snapshot_receipt),
        "target_revision_receipt": _artifact_payload(target_revision_receipt),
    }
    payload: dict[str, Any] = {
        "schema_version": profile["outcome"]["input_manifest_schema_version"],
        "case": "ootang",
        "target_date": selection.target_date.isoformat(),
        "selection_kind": selection.kind,
        "source_watermark": getattr(source, "watermark").isoformat(),
        "source_exported_at_utc": _require_string(
            getattr(source, "exported_at_utc", None), name="source.exported_at_utc"
        ),
        "outcome_source_id": _require_string(
            getattr(source, "outcome_source_id", None), name="source.outcome_source_id"
        ),
        "source": source_artifacts,
        "source_record": _source_record_payload(
            selection.record,
            record_schema_version=deploy["source_feed"]["record_schema_version"],
            stations=stations,
        ),
        "station_order": stations,
        "implementation": _implementation_record(profile),
    }
    payload["scientific_semantics_sha256"] = _canonical_digest(
        _manifest_science(payload)
    )
    return payload


def _validate_input_manifest(
    artifact: Artifact,
    *,
    profile: Mapping[str, Any],
    root: Path,
) -> dict[str, Any]:
    expected_manifest_path = _object_path(
        _runtime_path(profile, root, "objects"),
        artifact.sha256,
        suffix="outcome-input.json",
    )
    if artifact.path.resolve() != expected_manifest_path:
        raise OutcomeMaterializerConflict(
            "Outcome input manifest is outside its canonical object path"
        )
    manifest, raw = _read_json(artifact.path, name="outcome input manifest")
    if raw != _canonical_bytes(manifest):
        raise OutcomeMaterializerConflict(
            "Outcome input manifest bytes are not canonical"
        )
    if _sha256_bytes(raw) != artifact.sha256 or len(raw) != artifact.size_bytes:
        raise OutcomeMaterializerConflict("Outcome input manifest binding changed")
    record = _require_exact_keys(
        manifest,
        {
            "schema_version",
            "case",
            "target_date",
            "selection_kind",
            "source_watermark",
            "source_exported_at_utc",
            "outcome_source_id",
            "source",
            "source_record",
            "station_order",
            "implementation",
            "scientific_semantics_sha256",
        },
        name="outcome input manifest",
    )
    if (
        record["schema_version"]
        != profile["outcome"]["input_manifest_schema_version"]
        or record["case"] != "ootang"
        or record["selection_kind"] not in {"revision", "outstanding", "backfill"}
    ):
        raise OutcomeMaterializerConflict("Outcome input manifest contract changed")
    implementation_record = record["implementation"]
    if not isinstance(implementation_record, dict):
        raise OutcomeMaterializerConflict("Outcome implementation is not an object")
    expected_implementation_artifacts = {
        "cycle_profile": (
            Path(profile["_profile_path"]).resolve(), profile["_profile_sha256"]
        ),
        "deploy_profile": (
            Path(profile["_deploy_path"]).resolve(),
            profile["deploy_profile"]["expected_sha256"],
        ),
        "live_profile": (
            Path(profile["_live_path"]).resolve(),
            profile["live_profile"]["expected_sha256"],
        ),
    }
    for name, (expected_path, expected_sha) in expected_implementation_artifacts.items():
        artifact = _artifact_from_mapping(
            implementation_record.get(name), name=f"implementation.{name}"
        )
        if artifact.path != expected_path or artifact.sha256 != expected_sha:
            raise OutcomeMaterializerConflict(
                f"Outcome implementation {name} binding changed"
            )
    target = _parse_date(record["target_date"], name="manifest.target_date")
    watermark = _parse_date(
        record["source_watermark"], name="manifest.source_watermark"
    )
    _parse_utc(
        record["source_exported_at_utc"], name="manifest.source_exported_at_utc"
    )
    if target > watermark:
        raise OutcomeMaterializerConflict("Outcome target exceeds source watermark")
    _require_string(record["outcome_source_id"], name="manifest.outcome_source_id")
    source_record = _require_exact_keys(
        record["source_record"],
        {
            "schema_version",
            "date",
            "revision_id",
            "finalized",
            "observed_at_utc",
            "available_at_utc",
            "finalized_at_utc",
            "rainfall_mm",
            "reservoir_water_level_m",
            "displacement_mm",
        },
        name="manifest.source_record",
    )
    deploy = profile["_deploy_profile"]
    if (
        source_record["schema_version"]
        != deploy["source_feed"]["record_schema_version"]
        or source_record["date"] != target.isoformat()
        or source_record["finalized"] is not True
    ):
        raise OutcomeMaterializerConflict("Manifest source record scope changed")
    observed = _parse_utc(
        source_record["observed_at_utc"], name="manifest.record.observed"
    )
    available = _parse_utc(
        source_record["available_at_utc"], name="manifest.record.available"
    )
    finalized = _parse_utc(
        source_record["finalized_at_utc"], name="manifest.record.finalized"
    )
    if not observed <= available <= finalized:
        raise OutcomeMaterializerConflict("Manifest record timestamps changed order")
    stations = list(deploy["source_feed"]["station_order_live"])
    if record["station_order"] != stations:
        raise OutcomeMaterializerConflict("Manifest station order changed")
    displacement = source_record["displacement_mm"]
    if not isinstance(displacement, dict) or list(displacement) != stations:
        raise OutcomeMaterializerConflict(
            "Manifest record displacement stations changed"
        )
    for station in stations:
        _finite(displacement[station], name=f"manifest.record.{station}")
    _finite(source_record["rainfall_mm"], name="manifest.record.rainfall")
    _finite(
        source_record["reservoir_water_level_m"], name="manifest.record.rwl"
    )
    source_artifacts = _require_exact_keys(
        record["source"],
        {
            "canonical_dataset",
            "semantic_manifest",
            "activation_source_manifest",
            "snapshot_receipt",
            "target_revision_receipt",
        },
        name="manifest.source",
    )
    artifacts = {
        name: _artifact_from_mapping(value, name=f"manifest.source.{name}")
        for name, value in source_artifacts.items()
    }
    object_root = _runtime_path(profile, root, "objects").resolve()
    source_suffixes = {
        "canonical_dataset": "source.json",
        "semantic_manifest": "source-manifest.json",
        "snapshot_receipt": "source-snapshot-receipt.json",
        "target_revision_receipt": "source-revision.json",
    }
    for name, suffix in source_suffixes.items():
        expected_path = _object_path(
            object_root, artifacts[name].sha256, suffix=suffix
        )
        if artifacts[name].path != expected_path:
            raise OutcomeMaterializerConflict(
                f"Manifest {name} is outside its canonical object path"
            )
    activation_path = (
        root.resolve()
        / profile["_deploy_profile"]["runtime"]["activation_source_manifest"]
    ).resolve()
    if artifacts["activation_source_manifest"].path != activation_path:
        raise OutcomeMaterializerConflict(
            "Manifest activation source path changed"
        )
    revision_head, _ = _read_json(
        artifacts["target_revision_receipt"].path,
        name="manifest target source revision receipt",
    )
    if (
        revision_head.get("schema_version") != "ootang_source_revision_receipt_v1"
        or revision_head.get("target_date") != target.isoformat()
        or revision_head.get("revision_id") != source_record["revision_id"]
        or revision_head.get("outcome_source_id") != record["outcome_source_id"]
    ):
        raise OutcomeMaterializerConflict(
            "Target source revision receipt scope changed"
        )
    receipt_record = revision_head.get("record")
    expected_receipt_record = {
        key: source_record[key]
        for key in (
            "date",
            "revision_id",
            "observed_at_utc",
            "available_at_utc",
            "finalized_at_utc",
            "rainfall_mm",
            "reservoir_water_level_m",
            "displacement_mm",
        )
    }
    if receipt_record != expected_receipt_record:
        raise OutcomeMaterializerConflict(
            "Target source revision receipt record changed"
        )
    snapshot_receipt, _ = _read_json(
        artifacts["snapshot_receipt"].path,
        name="manifest source snapshot receipt",
    )
    if (
        snapshot_receipt.get("schema_version")
        != "ootang_source_snapshot_receipt_v1"
        or snapshot_receipt.get("outcome_source_id")
        != record["outcome_source_id"]
        or not isinstance(snapshot_receipt.get("snapshot_sequence_id"), int)
        or snapshot_receipt.get("snapshot_sequence_id", 0) <= 0
    ):
        raise OutcomeMaterializerConflict("Source snapshot receipt scope changed")
    scientific = _require_sha256(
        record["scientific_semantics_sha256"],
        name="manifest.scientific_semantics_sha256",
    )
    if scientific != _canonical_digest(_manifest_science(record)):
        raise OutcomeMaterializerConflict(
            "Outcome input scientific semantics digest changed"
        )
    return record


def _materialize_input_manifest(
    profile: Mapping[str, Any], root: Path, payload: Mapping[str, Any]
) -> Artifact:
    raw = _canonical_bytes(dict(payload))
    return _materialize_object(
        _runtime_path(profile, root, "objects"),
        raw,
        suffix="outcome-input.json",
    )


def _outcome_payload(
    profile: Mapping[str, Any],
    selection: _Selection,
    input_manifest: Artifact,
    *,
    outcome_source_id: str,
) -> dict[str, Any]:
    deploy = profile["_deploy_profile"]
    stations = list(deploy["source_feed"]["station_order_live"])
    record = selection.record
    displacement = getattr(record, "displacement_mm")
    return {
        "schema_version": profile["outcome"]["schema_version"],
        "target_date": selection.target_date.isoformat(),
        "outcome_source_id": _require_string(
            outcome_source_id, name="source.outcome_source_id"
        ),
        "source_revision_id": _require_string(
            getattr(record, "revision_id", None), name="record.revision_id"
        ),
        "source_observed_at_utc": _require_string(
            getattr(record, "observed_at_utc", None), name="record.observed_at_utc"
        ),
        "source_available_at_utc": _require_string(
            getattr(record, "available_at_utc", None), name="record.available_at_utc"
        ),
        "finalized_at_utc": _require_string(
            getattr(record, "finalized_at_utc", None), name="record.finalized_at_utc"
        ),
        "finalized": True,
        "source_manifest": _artifact_payload(input_manifest),
        "stations": [
            {
                "station": station,
                "actual_mm": _finite(
                    displacement[station], name=f"record.displacement.{station}"
                ),
            }
            for station in stations
        ],
    }


def _semantic_outcome(payload: Mapping[str, Any]) -> dict[str, Any]:
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
        name="outcome batch",
    )
    manifest = _artifact_from_mapping(
        record["source_manifest"], name="outcome.source_manifest"
    )
    manifest_payload, _ = _read_json(manifest.path, name="outcome input manifest")
    scientific = _require_sha256(
        manifest_payload.get("scientific_semantics_sha256"),
        name="manifest.scientific_semantics_sha256",
    )
    semantic = dict(record)
    semantic["source_manifest"] = {
        "scientific_semantics_sha256": scientific,
        "exact_manifest_sha256": manifest.sha256,
    }
    return semantic


def _receipt_path(receipt_root: Path, target: date, revision_id: str) -> Path:
    revision = _require_string(revision_id, name="source_revision_id")
    token = hashlib.sha256(revision.encode("utf-8")).hexdigest()
    return _confined_child(
        receipt_root,
        Path(target.isoformat()) / f"{token}.json",
        name="outcome receipt path",
    )


def _load_registered_outcome(
    *,
    receipt_path: Path,
    active_path: Path,
    object_root: Path,
    receipt_root: Path,
    profile: Mapping[str, Any],
    root: Path,
    live_module: object,
    live_profile: dict[str, Any],
    prerequisites: object,
) -> _RegisteredOutcome:
    receipt, receipt_raw = _read_json(
        receipt_path, name="outcome materializer receipt"
    )
    if receipt_raw != _canonical_bytes(receipt):
        raise OutcomeMaterializerConflict("Outcome receipt bytes are not canonical")
    record = _require_exact_keys(
        receipt,
        {
            "schema_version",
            "target_date",
            "source_revision_id",
            "active_outcome_path",
            "scientific_semantics_sha256",
            "input_manifest_sha256",
            "exact_outcome_object",
            "revision_sequence_id",
            "previous_receipt",
        },
        name="outcome receipt",
    )
    if record["schema_version"] != profile["outcome"]["receipt_schema_version"]:
        raise OutcomeMaterializerConflict("Outcome receipt schema changed")
    target = _parse_date(record["target_date"], name="receipt.target_date")
    revision = _require_string(
        record["source_revision_id"], name="receipt.source_revision_id"
    )
    expected_path = _receipt_path(receipt_root, target, revision)
    if receipt_path.resolve() != expected_path:
        raise OutcomeMaterializerConflict("Outcome receipt registry binding changed")
    if (
        active_path.name != f"{target.isoformat()}.json"
        or record["active_outcome_path"] != str(active_path.resolve())
    ):
        raise OutcomeMaterializerConflict("Outcome receipt active path changed")
    exact = _artifact_from_mapping(
        record["exact_outcome_object"], name="receipt.exact_outcome_object"
    )
    expected_object_path = _object_path(
        object_root, exact.sha256, suffix="outcome.json"
    )
    if exact.path != expected_object_path:
        raise OutcomeMaterializerConflict(
            "Exact outcome object escaped the configured registry"
        )
    payload, raw = _read_json(exact.path, name="registered exact outcome")
    if raw != _canonical_bytes(payload) or _sha256_bytes(raw) != exact.sha256:
        raise OutcomeMaterializerConflict(
            "Registered exact outcome bytes are not canonical"
        )
    if (
        payload.get("target_date") != target.isoformat()
        or payload.get("source_revision_id") != revision
    ):
        raise OutcomeMaterializerConflict("Outcome receipt target/revision changed")
    try:
        parsed = live_module.load_outcome_batch(
            exact.path, live_profile, prerequisites
        )
    except (
        live_module.LiveInputError,
        live_module.LiveIntegrityError,
        live_module.LivePrerequisiteError,
    ) as exc:
        raise OutcomeMaterializerConflict(
            f"Registered outcome fails E2-A consumer validation: {exc}"
        ) from exc
    if parsed.sha256 != exact.sha256:
        raise OutcomeMaterializerConflict("E2-A parsed outcome hash changed")
    input_manifest = _artifact_from_mapping(
        payload["source_manifest"], name="registered outcome input manifest"
    )
    _validate_input_manifest(input_manifest, profile=profile, root=root)
    if record["input_manifest_sha256"] != input_manifest.sha256:
        raise OutcomeMaterializerConflict("Outcome receipt input manifest changed")
    scientific = _canonical_digest(_semantic_outcome(payload))
    if record["scientific_semantics_sha256"] != scientific:
        raise OutcomeMaterializerConflict(
            "Outcome receipt scientific semantics changed"
        )
    sequence_id = record["revision_sequence_id"]
    if (
        isinstance(sequence_id, bool)
        or not isinstance(sequence_id, int)
        or sequence_id <= 0
    ):
        raise OutcomeMaterializerConflict(
            "Outcome receipt revision sequence id is invalid"
        )
    previous_value = record["previous_receipt"]
    previous: Artifact | None = None
    if previous_value is not None:
        previous = _artifact_from_mapping(
            previous_value, name="receipt.previous_receipt"
        )
        try:
            previous.path.relative_to(receipt_root.resolve())
        except ValueError as exc:
            raise OutcomeMaterializerConflict(
                "Previous receipt escaped the receipt registry"
            ) from exc
        if previous.path == receipt_path.resolve():
            raise OutcomeMaterializerConflict("Outcome receipt points to itself")
    return _RegisteredOutcome(
        receipt=Artifact(
            receipt_path.resolve(), _sha256_bytes(receipt_raw), len(receipt_raw)
        ),
        exact_object=exact,
        payload=payload,
        raw=raw,
        scientific_semantics_sha256=scientific,
        revision_sequence_id=sequence_id,
        previous_receipt=previous,
    )


def _active_pointer_path(receipt_root: Path, target: date) -> Path:
    return _confined_child(
        receipt_root,
        Path(target.isoformat()) / "active.json",
        name="active outcome receipt pointer",
    )


def _outcome_inbox_path(
    profile: Mapping[str, Any], root: Path, target: date
) -> Path:
    return _confined_child(
        _runtime_path(profile, root, "outcome_inbox"),
        f"{target.isoformat()}.json",
        name="active outcome inbox path",
    )


def _active_pointer_payload(
    target: date, receipt: Artifact
) -> dict[str, Any]:
    return {
        "schema_version": ACTIVE_RECEIPT_POINTER_SCHEMA_VERSION,
        "target_date": target.isoformat(),
        "active_receipt": _artifact_payload(receipt),
    }


def _scan_receipt_chain(
    *,
    target: date,
    profile: Mapping[str, Any],
    root: Path,
    live_module: object,
    live_profile: dict[str, Any],
    prerequisites: object,
) -> _ReceiptChain | None:
    """Validate one date's complete immutable receipt graph and unique tip."""

    receipt_root = _runtime_path(profile, root, "outcome_receipts")
    directory = _confined_child(
        receipt_root, target.isoformat(), name="outcome receipt date registry"
    )
    active_path = _outcome_inbox_path(profile, root, target)
    pointer_path = _active_pointer_path(receipt_root, target)
    if not directory.exists():
        if pointer_path.exists() or active_path.exists():
            raise OutcomeMaterializerConflict(
                "Outcome active state exists without a receipt registry"
            )
        return None
    if not directory.is_dir():
        raise OutcomeMaterializerConflict(
            "Outcome receipt date registry is not a directory"
        )
    receipt_paths: list[Path] = []
    for path in sorted(directory.iterdir()):
        if path.name == "active.json" or path.name.startswith("."):
            continue
        if (
            not path.is_file()
            or path.suffix != ".json"
            or len(path.stem) != 64
            or any(character not in HEX_DIGITS for character in path.stem)
        ):
            raise OutcomeMaterializerConflict(
                "Outcome receipt registry contains an unexpected entry"
            )
        receipt_paths.append(path.resolve())
    if not receipt_paths:
        if pointer_path.exists() or active_path.exists():
            raise OutcomeMaterializerConflict(
                "Outcome active state exists without an immutable receipt"
            )
        return None

    receipts = [
        _load_registered_outcome(
            receipt_path=path,
            active_path=active_path,
            object_root=_runtime_path(profile, root, "objects"),
            receipt_root=receipt_root,
            profile=profile,
            root=root,
            live_module=live_module,
            live_profile=live_profile,
            prerequisites=prerequisites,
        )
        for path in receipt_paths
    ]
    by_path = {receipt.receipt.path: receipt for receipt in receipts}
    if len(by_path) != len(receipts):
        raise OutcomeMaterializerConflict("Outcome receipt paths are not unique")
    roots = [receipt for receipt in receipts if receipt.previous_receipt is None]
    if len(roots) != 1 or roots[0].revision_sequence_id != 1:
        raise OutcomeMaterializerConflict(
            "Outcome receipt graph does not have one sequence-1 root"
        )
    children: dict[Path, list[_RegisteredOutcome]] = {}
    for receipt in receipts:
        previous = receipt.previous_receipt
        if previous is None:
            continue
        parent = by_path.get(previous.path)
        if parent is None or parent.receipt.sha256 != previous.sha256:
            raise OutcomeMaterializerConflict(
                "Outcome receipt previous link is outside this date chain"
            )
        if receipt.revision_sequence_id != parent.revision_sequence_id + 1:
            raise OutcomeMaterializerConflict(
                "Outcome receipt sequence is not contiguous"
            )
        children.setdefault(parent.receipt.path, []).append(receipt)
    if any(len(values) != 1 for values in children.values()):
        raise OutcomeMaterializerConflict("Outcome receipt history forked")
    ordered = sorted(receipts, key=lambda item: item.revision_sequence_id)
    if [item.revision_sequence_id for item in ordered] != list(
        range(1, len(ordered) + 1)
    ):
        raise OutcomeMaterializerConflict(
            "Outcome receipt sequence has a gap or duplicate"
        )
    for previous, current in zip(ordered, ordered[1:]):
        if (
            current.previous_receipt is None
            or current.previous_receipt.path != previous.receipt.path
            or current.previous_receipt.sha256 != previous.receipt.sha256
        ):
            raise OutcomeMaterializerConflict(
                "Outcome receipts do not form one linear history"
            )
    tip = ordered[-1]

    pointer_raw: bytes | None = None
    pointed: _RegisteredOutcome | None = None
    if pointer_path.exists():
        if not pointer_path.is_file():
            raise OutcomeMaterializerConflict(
                "Active receipt pointer is not a regular file"
            )
        pointer, pointer_raw = _read_json(
            pointer_path, name="active outcome receipt pointer"
        )
        if pointer_raw != _canonical_bytes(pointer):
            raise OutcomeMaterializerConflict(
                "Active receipt pointer bytes are not canonical"
            )
        pointer_record = _require_exact_keys(
            pointer,
            {"schema_version", "target_date", "active_receipt"},
            name="active outcome receipt pointer",
        )
        if (
            pointer_record["schema_version"]
            != ACTIVE_RECEIPT_POINTER_SCHEMA_VERSION
            or pointer_record["target_date"] != target.isoformat()
        ):
            raise OutcomeMaterializerConflict(
                "Active receipt pointer scope changed"
            )
        pointer_artifact = _artifact_from_mapping(
            pointer_record["active_receipt"], name="pointer.active_receipt"
        )
        pointed = by_path.get(pointer_artifact.path)
        if pointed is None or pointed.receipt.sha256 != pointer_artifact.sha256:
            raise OutcomeMaterializerConflict(
                "Active receipt pointer does not reference this date chain"
            )
        lag = tip.revision_sequence_id - pointed.revision_sequence_id
        if lag not in {0, 1}:
            raise OutcomeMaterializerConflict(
                "Active receipt pointer rolled back or skipped multiple receipts"
            )
    elif len(ordered) != 1:
        raise OutcomeMaterializerConflict(
            "Multi-revision chain lost its active receipt pointer"
        )

    return _ReceiptChain(
        target_date=target,
        receipts=tuple(ordered),
        tip=tip,
        active_pointer_path=pointer_path,
        active_pointer_raw=pointer_raw,
        pointed=pointed,
    )


def _legal_active_bytes(chain: _ReceiptChain, active_path: Path) -> bytes | None:
    if not active_path.exists():
        return None
    if not active_path.is_file():
        raise OutcomeMaterializerConflict(
            "Outcome inbox active path is not a regular file"
        )
    try:
        raw = active_path.read_bytes()
    except OSError as exc:
        raise OutcomeMaterializerConflict(
            "Outcome inbox active bytes cannot be read"
        ) from exc
    allowed: list[bytes] = []
    if chain.pointed is not None:
        allowed.append(chain.pointed.raw)
        previous = chain.pointed.previous_receipt
        if previous is not None:
            matches = [
                receipt
                for receipt in chain.receipts
                if receipt.receipt.path == previous.path
                and receipt.receipt.sha256 == previous.sha256
            ]
            if len(matches) != 1:
                raise OutcomeMaterializerConflict(
                    "Active pointer previous receipt cannot be reconstructed"
                )
            allowed.append(matches[0].raw)
    if raw not in allowed:
        raise OutcomeMaterializerConflict(
            "Outcome inbox is neither active receipt bytes nor verified previous bytes"
        )
    return raw


def _pointer_bytes(chain: _ReceiptChain) -> bytes:
    return _canonical_bytes(
        _active_pointer_payload(chain.target_date, chain.tip.receipt)
    )


def _validate_registered_time(
    registered: _RegisteredOutcome, *, now: datetime
) -> datetime:
    manifest_artifact = _artifact_from_mapping(
        registered.payload["source_manifest"],
        name="registered outcome source manifest",
    )
    manifest, _ = _read_json(
        manifest_artifact.path, name="registered outcome input manifest"
    )
    checked = _clock_value(now).astimezone(timezone.utc)
    finalized = _parse_utc(
        registered.payload["finalized_at_utc"],
        name="registered outcome finalized_at_utc",
    )
    exported = _parse_utc(
        manifest.get("source_exported_at_utc"),
        name="registered source_exported_at_utc",
    )
    if finalized > checked or exported > checked:
        raise OutcomeMaterializerInputError(
            "Registered outcome is future-dated relative to machine time"
        )
    return checked


def _restore_file_state(
    path: Path, *, previous_raw: bytes | None, published_raw: bytes
) -> None:
    _restore_active_bytes(
        path, previous_raw=previous_raw, published_raw=published_raw
    )


def _reconcile_chain(
    chain: _ReceiptChain,
    *,
    profile: Mapping[str, Any],
    root: Path,
    now: datetime,
    live_module: object,
    live_profile: dict[str, Any],
    prerequisites: object,
) -> tuple[_PublicationState, datetime]:
    """Advance pointer then inbox to the unique receipt tip, with rollback."""

    checked = _validate_registered_time(chain.tip, now=now)
    active_path = _outcome_inbox_path(profile, root, chain.target_date)
    previous_active = _legal_active_bytes(chain, active_path)
    previous_pointer = chain.active_pointer_raw
    pointer_raw = _pointer_bytes(chain)
    changed_pointer = previous_pointer != pointer_raw
    changed_active = previous_active != chain.tip.raw
    try:
        if changed_pointer:
            _atomic_write_bytes(chain.active_pointer_path, pointer_raw)
        if changed_active:
            _atomic_write_bytes(active_path, chain.tip.raw)
        durable = _active_registered(
            active_path,
            profile=profile,
            root=root,
            live_module=live_module,
            live_profile=live_profile,
            prerequisites=prerequisites,
        )
        if durable.receipt.sha256 != chain.tip.receipt.sha256:
            raise OutcomeMaterializerConflict(
                "Reconciled inbox does not match the unique chain tip"
            )
        pointer, durable_pointer_raw = _read_json(
            chain.active_pointer_path, name="durable active receipt pointer"
        )
        if durable_pointer_raw != pointer_raw or pointer != _active_pointer_payload(
            chain.target_date, chain.tip.receipt
        ):
            raise OutcomeMaterializerConflict(
                "Durable active receipt pointer differs from the unique tip"
            )
    except Exception as exc:
        try:
            if changed_active:
                _restore_file_state(
                    active_path,
                    previous_raw=previous_active,
                    published_raw=chain.tip.raw,
                )
            if changed_pointer:
                _restore_file_state(
                    chain.active_pointer_path,
                    previous_raw=previous_pointer,
                    published_raw=pointer_raw,
                )
        except Exception as rollback_exc:
            raise OutcomeMaterializerConflict(
                "Receipt pointer/inbox reconciliation could not be rolled back"
            ) from rollback_exc
        raise exc
    return (
        _PublicationState(
            active_path=active_path,
            previous_active_raw=previous_active,
            published_active_raw=chain.tip.raw,
            pointer_path=chain.active_pointer_path,
            previous_pointer_raw=previous_pointer,
            published_pointer_raw=pointer_raw,
            changed=changed_pointer or changed_active,
        ),
        checked,
    )


def _rollback_publication(state: _PublicationState) -> None:
    if not state.changed:
        return
    _restore_file_state(
        state.active_path,
        previous_raw=state.previous_active_raw,
        published_raw=state.published_active_raw,
    )
    _restore_file_state(
        state.pointer_path,
        previous_raw=state.previous_pointer_raw,
        published_raw=state.published_pointer_raw,
    )


def _record_matches_outcome(
    record: object, outcome: Mapping[str, Any], *, stations: Sequence[str]
) -> bool:
    if (
        outcome.get("target_date") != getattr(record, "day").isoformat()
        or outcome.get("source_revision_id") != getattr(record, "revision_id")
        or outcome.get("source_observed_at_utc")
        != getattr(record, "observed_at_utc")
        or outcome.get("source_available_at_utc")
        != getattr(record, "available_at_utc")
        or outcome.get("finalized_at_utc") != getattr(record, "finalized_at_utc")
        or outcome.get("finalized") is not True
    ):
        return False
    values = outcome.get("stations")
    if not isinstance(values, list) or len(values) != len(stations):
        return False
    actuals: dict[str, float] = {}
    for value in values:
        if not isinstance(value, dict) or set(value) != {"station", "actual_mm"}:
            return False
        station = value["station"]
        if not isinstance(station, str) or station in actuals:
            return False
        actuals[station] = _finite(value["actual_mm"], name=f"outcome.{station}")
    displacement = getattr(record, "displacement_mm", None)
    return list(actuals) == list(stations) and all(
        actuals[station] == displacement[station] for station in stations
    )


def _record_matches_manifest(
    record: object,
    manifest: Mapping[str, Any],
    *,
    record_schema_version: str,
    stations: Sequence[str],
) -> bool:
    """Compare the complete target-scoped DailySourceRecord semantics."""

    expected = _source_record_payload(
        record,
        record_schema_version=record_schema_version,
        stations=stations,
    )
    return manifest.get("source_record") == expected


def _select_target(
    source: object,
    projection: object,
    *,
    known_validator: Callable[[date, object, str, str], None] | None = None,
) -> _Selection | None:
    records = tuple(getattr(source, "records", ()))
    record_by_date: dict[date, object] = {}
    for record in records:
        day = getattr(record, "day", None)
        if not isinstance(day, date) or day in record_by_date:
            raise OutcomeMaterializerInputError(
                "Current source records have invalid or duplicate dates"
            )
        record_by_date[day] = record

    revisions = getattr(projection, "revision_ids", None)
    if not isinstance(revisions, Mapping):
        raise OutcomeMaterializerInputError(
            "Verified ledger projection has no revision registry"
        )
    # Every accepted date is checked before a new outcome/backfill.  Mapping
    # insertion order is ledger order, so the last key is the latest revision.
    for target_text in sorted(revisions):
        target = _parse_date(target_text, name="ledger.revision target")
        record = record_by_date.get(target)
        if record is None:
            if target <= getattr(source, "watermark"):
                raise OutcomeMaterializerInputError(
                    "Current source lost an already accepted target record"
                )
            continue
        known = revisions[target_text]
        if not isinstance(known, Mapping) or not known:
            raise OutcomeMaterializerInputError(
                "Verified ledger revision registry is empty or invalid"
            )
        revision_id = _require_string(
            getattr(record, "revision_id", None), name="source.record.revision_id"
        )
        latest_revision = next(reversed(tuple(known)))
        latest_sha = _require_sha256(
            known[latest_revision], name="ledger.latest outcome SHA"
        )
        if revision_id in known:
            if revision_id != latest_revision:
                raise OutcomeMaterializerConflict(
                    f"Source revision rolled back for {target.isoformat()}"
                )
            if known_validator is not None:
                known_validator(target, record, latest_revision, latest_sha)
            continue
        return _Selection(
            "revision",
            target,
            record,
            previous_revision_id=latest_revision,
            previous_outcome_sha256=latest_sha,
        )

    outstanding = getattr(projection, "outstanding_target_date", None)
    if outstanding is not None:
        if not isinstance(outstanding, date):
            raise OutcomeMaterializerInputError(
                "Outstanding ledger target has invalid type"
            )
        seal = getattr(projection, "seal_event", None)
        if seal is None or getattr(seal, "target_date", None) != outstanding.isoformat():
            raise OutcomeMaterializerInputError(
                "Outstanding target is not backed by its sealed issue transaction"
            )
        record = record_by_date.get(outstanding)
        if record is None:
            if outstanding <= getattr(source, "watermark"):
                raise OutcomeMaterializerInputError(
                    "Current source watermark skipped the outstanding target"
                )
            return None
        return _Selection("outstanding", outstanding, record)

    expected = getattr(projection, "last_finalized_date", None)
    if not isinstance(expected, date):
        raise OutcomeMaterializerInputError(
            "Verified ledger last finalized date is invalid"
        )
    expected += timedelta(days=1)
    if expected > getattr(source, "watermark"):
        return None
    record = record_by_date.get(expected)
    if record is None:
        raise OutcomeMaterializerInputError(
            "Current source skipped the next contiguous backfill date"
        )
    return _Selection("backfill", expected, record)


def _status_payload(
    profile: Mapping[str, Any],
    root: Path,
    *,
    now: datetime,
    status: str,
    reason: str,
    target: date | None = None,
    outcome_path: Path | None = None,
    input_manifest: Artifact | None = None,
    receipt: Artifact | None = None,
    exact_object: Artifact | None = None,
    active_receipt_pointer: Artifact | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "profile_id": profile["profile_id"],
        "cycle_profile_sha256": profile["_profile_sha256"],
        "deploy_profile_sha256": profile["deploy_profile"]["expected_sha256"],
        "live_profile_sha256": profile["live_profile"]["expected_sha256"],
        "artifact_status": profile["artifact_status"],
        "materializer_status": status,
        "reason": reason,
        "recorded_at_utc": _format_utc(now),
        "runtime_root": str(root.resolve()),
        "target_date": target.isoformat() if target is not None else None,
        "outcome_path": str(outcome_path.resolve()) if outcome_path else None,
        "input_manifest": (
            _artifact_payload(input_manifest) if input_manifest else None
        ),
        "outcome_receipt": _artifact_payload(receipt) if receipt else None,
        "active_receipt_pointer": (
            _artifact_payload(active_receipt_pointer)
            if active_receipt_pointer
            else None
        ),
        "exact_outcome_object": (
            _artifact_payload(exact_object) if exact_object else None
        ),
        "formal_warning_output": False,
        "independent_label_used": False,
        "confirmatory_external_validation": False,
        "vajont_used": False,
        "runner_independent_checkpoint_inference_replayed": False,
        "trusted_anchor_receipt_verified": False,
        "automatic_epoch_rotation_implemented": False,
        "e2_live_evidence_eligible": False,
        "real_activation_ready": False,
    }


def _write_status(
    profile: Mapping[str, Any], root: Path, **kwargs: Any
) -> Path:
    path = _runtime_path(profile, root, "outcome_status")
    _atomic_write_bytes(path, _canonical_bytes(_status_payload(profile, root, **kwargs)))
    return path


def _validate_finalization(
    record: object, *, source_exported_at_utc: str, now: datetime
) -> datetime:
    checked = _clock_value(now).astimezone(timezone.utc)
    finalized = _parse_utc(
        getattr(record, "finalized_at_utc", None), name="source.record.finalized_at_utc"
    )
    exported = _parse_utc(
        source_exported_at_utc, name="source.exported_at_utc"
    )
    if finalized > checked or exported > checked:
        raise OutcomeMaterializerInputError(
            "Selected source record/export is future-dated relative to machine time"
        )
    return checked


def _active_registered(
    active_path: Path,
    *,
    profile: Mapping[str, Any],
    root: Path,
    live_module: object,
    live_profile: dict[str, Any],
    prerequisites: object,
) -> _RegisteredOutcome:
    try:
        payload, _ = _read_json(active_path, name="active outcome batch")
        target = _parse_date(payload.get("target_date"), name="active.target_date")
        revision = _require_string(
            payload.get("source_revision_id"), name="active.source_revision_id"
        )
    except OutcomeMaterializerInputError as exc:
        raise OutcomeMaterializerConflict(
            "Active outcome bytes violate their registered contract"
        ) from exc
    if active_path.name != f"{target.isoformat()}.json":
        raise OutcomeMaterializerConflict("Active outcome filename/date changed")
    receipt_root = _runtime_path(profile, root, "outcome_receipts")
    receipt_path = _receipt_path(receipt_root, target, revision)
    if not receipt_path.is_file():
        raise OutcomeMaterializerConflict(
            "Active outcome has no immutable materializer receipt"
        )
    registered = _load_registered_outcome(
        receipt_path=receipt_path,
        active_path=active_path,
        object_root=_runtime_path(profile, root, "objects"),
        receipt_root=receipt_root,
        profile=profile,
        root=root,
        live_module=live_module,
        live_profile=live_profile,
        prerequisites=prerequisites,
    )
    try:
        active_raw = active_path.read_bytes()
    except OSError as exc:
        raise OutcomeMaterializerConflict("Active outcome cannot be read") from exc
    if active_raw != registered.raw:
        raise OutcomeMaterializerConflict(
            "Active outcome differs from its immutable receipt"
        )
    return registered


def _create_or_validate_receipt(
    *,
    profile: Mapping[str, Any],
    root: Path,
    active_path: Path,
    exact_object: Artifact,
    payload: Mapping[str, Any],
    input_manifest: Artifact,
    previous_receipt: Artifact | None,
    revision_sequence_id: int,
    live_module: object,
    live_profile: dict[str, Any],
    prerequisites: object,
) -> tuple[_RegisteredOutcome, bool]:
    target = _parse_date(payload["target_date"], name="outcome.target_date")
    revision = _require_string(
        payload["source_revision_id"], name="outcome.source_revision_id"
    )
    receipt_root = _runtime_path(profile, root, "outcome_receipts")
    receipt_path = _receipt_path(receipt_root, target, revision)
    scientific = _canonical_digest(_semantic_outcome(payload))
    receipt_payload = {
        "schema_version": profile["outcome"]["receipt_schema_version"],
        "target_date": target.isoformat(),
        "source_revision_id": revision,
        "active_outcome_path": str(active_path.resolve()),
        "scientific_semantics_sha256": scientific,
        "input_manifest_sha256": input_manifest.sha256,
        "exact_outcome_object": _artifact_payload(exact_object),
        "revision_sequence_id": revision_sequence_id,
        "previous_receipt": (
            _artifact_payload(previous_receipt)
            if previous_receipt is not None
            else None
        ),
    }
    created = _atomic_create_bytes(receipt_path, _canonical_bytes(receipt_payload))
    registered = _load_registered_outcome(
        receipt_path=receipt_path,
        active_path=active_path,
        object_root=_runtime_path(profile, root, "objects"),
        receipt_root=receipt_root,
        profile=profile,
        root=root,
        live_module=live_module,
        live_profile=live_profile,
        prerequisites=prerequisites,
    )
    if (
        registered.raw != _canonical_bytes(dict(payload))
        or registered.exact_object != exact_object
        or registered.scientific_semantics_sha256 != scientific
        or registered.receipt.sha256 != _sha256_bytes(_canonical_bytes(receipt_payload))
    ):
        raise OutcomeMaterializerConflict(
            "Same revision already has changed outcome semantics"
        )
    expected_previous = (
        previous_receipt.sha256 if previous_receipt is not None else None
    )
    actual_previous = (
        registered.previous_receipt.sha256
        if registered.previous_receipt is not None
        else None
    )
    if (
        actual_previous != expected_previous
        or registered.revision_sequence_id != revision_sequence_id
    ):
        raise OutcomeMaterializerConflict("Outcome revision lineage changed")
    return registered, created


def _restore_active_bytes(
    active_path: Path, *, previous_raw: bytes | None, published_raw: bytes
) -> None:
    """Restore the pre-publication active state without deleting unknown bytes."""

    if previous_raw is not None:
        _atomic_write_bytes(active_path, previous_raw)
        return
    if not active_path.exists():
        return
    try:
        current = active_path.read_bytes()
    except OSError as exc:
        raise OutcomeMaterializerConflict(
            "Changed active outcome cannot be inspected for withdrawal"
        ) from exc
    if current != published_raw:
        raise OutcomeMaterializerConflict(
            "Changed active outcome cannot be safely withdrawn"
        )
    try:
        active_path.unlink()
    except OSError as exc:
        raise OutcomeMaterializerConflict(
            "Changed active outcome cannot be withdrawn"
        ) from exc
    directory_fd = os.open(active_path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _publish_candidate(
    *,
    profile: Mapping[str, Any],
    root: Path,
    selection: _Selection,
    source_exported_at_utc: str,
    input_manifest: Artifact,
    payload: dict[str, Any],
    clock: Clock,
    live_module: object,
    live_profile: dict[str, Any],
    prerequisites: object,
) -> tuple[str, _RegisteredOutcome, datetime, _PublicationState]:
    target = selection.target_date
    active_path = _outcome_inbox_path(profile, root, target)
    raw = _canonical_bytes(payload)
    exact_object = _materialize_object(
        _runtime_path(profile, root, "objects"), raw, suffix="outcome.json"
    )
    # Consumer preflight happens before the immutable receipt commit point.
    try:
        parsed = live_module.load_outcome_batch(
            exact_object.path, live_profile, prerequisites
        )
    except (
        live_module.LiveInputError,
        live_module.LiveIntegrityError,
        live_module.LivePrerequisiteError,
    ) as exc:
        raise OutcomeMaterializerInputError(
            f"Candidate outcome fails E2-A consumer validation: {exc}"
        ) from exc
    if parsed.target_date != target or parsed.sha256 != exact_object.sha256:
        raise OutcomeMaterializerInputError(
            "E2-A consumer parsed a different outcome candidate"
        )
    _validate_input_manifest(input_manifest, profile=profile, root=root)
    prepublish = _validate_finalization(
        selection.record,
        source_exported_at_utc=source_exported_at_utc,
        now=clock(),
    )
    chain = _scan_receipt_chain(
        target=target,
        profile=profile,
        root=root,
        live_module=live_module,
        live_profile=live_profile,
        prerequisites=prerequisites,
    )
    candidate_revision = _require_string(
        payload["source_revision_id"], name="outcome.source_revision_id"
    )
    previous_for_candidate: _RegisteredOutcome | None = None
    existing_candidate: _RegisteredOutcome | None = None
    if chain is not None:
        if chain.tip.payload.get("source_revision_id") == candidate_revision:
            existing_candidate = chain.tip
            previous_artifact = existing_candidate.previous_receipt
            if previous_artifact is not None:
                matches = [
                    receipt
                    for receipt in chain.receipts
                    if receipt.receipt.path == previous_artifact.path
                    and receipt.receipt.sha256 == previous_artifact.sha256
                ]
                if len(matches) != 1:
                    raise OutcomeMaterializerConflict(
                        "Candidate previous receipt is not unique"
                    )
                previous_for_candidate = matches[0]
        else:
            candidate_path = _receipt_path(
                _runtime_path(profile, root, "outcome_receipts"),
                target,
                candidate_revision,
            )
            if candidate_path.exists():
                raise OutcomeMaterializerConflict(
                    "Candidate revision is not the unique receipt-chain tip"
                )
            previous_for_candidate = chain.tip

    if selection.previous_revision_id is None:
        if previous_for_candidate is not None:
            raise OutcomeMaterializerConflict(
                "Unsettled target already has a different registered revision"
            )
    else:
        if (
            previous_for_candidate is None
            or previous_for_candidate.payload.get("source_revision_id")
            != selection.previous_revision_id
            or previous_for_candidate.exact_object.sha256
            != selection.previous_outcome_sha256
        ):
            raise OutcomeMaterializerConflict(
                "Receipt-chain tip disagrees with the verified ledger predecessor"
            )

    if existing_candidate is None:
        registered, _ = _create_or_validate_receipt(
            profile=profile,
            root=root,
            active_path=active_path,
            exact_object=exact_object,
            payload=payload,
            input_manifest=input_manifest,
            previous_receipt=(
                previous_for_candidate.receipt
                if previous_for_candidate is not None
                else None
            ),
            revision_sequence_id=(
                previous_for_candidate.revision_sequence_id + 1
                if previous_for_candidate is not None
                else 1
            ),
            live_module=live_module,
            live_profile=live_profile,
            prerequisites=prerequisites,
        )
    else:
        registered = existing_candidate
        if registered.raw != raw or registered.exact_object != exact_object:
            raise OutcomeMaterializerConflict(
                "Same revision already has changed outcome semantics"
            )

    committed_chain = _scan_receipt_chain(
        target=target,
        profile=profile,
        root=root,
        live_module=live_module,
        live_profile=live_profile,
        prerequisites=prerequisites,
    )
    if (
        committed_chain is None
        or committed_chain.tip.receipt.sha256 != registered.receipt.sha256
    ):
        raise OutcomeMaterializerConflict(
            "Committed receipt is not the unique revision-chain tip"
        )
    state, _ = _reconcile_chain(
        committed_chain,
        profile=profile,
        root=root,
        now=prepublish,
        live_module=live_module,
        live_profile=live_profile,
        prerequisites=prerequisites,
    )
    try:
        completed = _validate_finalization(
            selection.record,
            source_exported_at_utc=source_exported_at_utc,
            now=clock(),
        )
        if completed < prepublish:
            raise OutcomeMaterializerInputError(
                "Machine clock moved backwards during outcome publication"
            )
    except Exception as exc:
        try:
            _rollback_publication(state)
        except Exception as rollback_exc:
            raise OutcomeMaterializerConflict(
                "Outcome publication failed and active state could not be restored"
            ) from rollback_exc
        raise exc
    return (
        "materialized" if state.changed else "already_materialized_idempotent",
        registered,
        completed,
        state,
    )


def _missing_prerequisites(
    profile: Mapping[str, Any], root: Path, live_paths: object
) -> list[str]:
    deploy = profile["_deploy_profile"]
    values = {
        "current_source": (
            root / deploy["runtime"]["current_source_pointer"]
        ).resolve(),
        "activation_source": (
            root / deploy["runtime"]["activation_source_manifest"]
        ).resolve(),
        "production_model": (
            root / deploy["runtime"]["model_manifest"]
        ).resolve(),
        "live_ledger": Path(live_paths.ledger).resolve(),
    }
    return sorted(name for name, path in values.items() if not path.is_file())


def _activation_change_date(
    current_source: object, activation_source: object
) -> date | None:
    """Return the first immutable-epoch date changed by the current source."""

    if (
        getattr(current_source, "outcome_source_id", None)
        != getattr(activation_source, "outcome_source_id", None)
    ):
        return getattr(activation_source, "watermark")
    activation_watermark = getattr(activation_source, "watermark", None)
    if not isinstance(activation_watermark, date):
        raise OutcomeMaterializerInputError(
            "Activation source watermark is invalid"
        )
    current = {
        record.day: record
        for record in getattr(current_source, "records", ())
        if record.day <= activation_watermark
    }
    activation = {
        record.day: record
        for record in getattr(activation_source, "records", ())
        if record.day <= activation_watermark
    }
    for day in sorted(set(current) | set(activation)):
        if current.get(day) != activation.get(day):
            return day
    return None


def _receipt_registry_targets(
    profile: Mapping[str, Any], root: Path
) -> tuple[date, ...]:
    receipt_root = _runtime_path(profile, root, "outcome_receipts")
    if not receipt_root.exists():
        return ()
    if not receipt_root.is_dir():
        raise OutcomeMaterializerConflict(
            "Outcome receipt registry root is not a directory"
        )
    targets: list[date] = []
    for path in sorted(receipt_root.iterdir()):
        if path.name.startswith("."):
            continue
        if not path.is_dir():
            raise OutcomeMaterializerConflict(
                "Outcome receipt registry contains a non-date entry"
            )
        targets.append(_parse_date(path.name, name="receipt registry date"))
    return tuple(targets)


def _reconcile_receipt_registry(
    *,
    profile: Mapping[str, Any],
    root: Path,
    clock: Clock,
    live_module: object,
    live_profile: dict[str, Any],
    prerequisites: object,
) -> tuple[
    dict[str, _ReceiptChain], tuple[_PublicationState, ...], datetime | None
]:
    """Recover receipt-first crashes, never accepting a fork or third inbox bytes."""

    result: dict[str, _ReceiptChain] = {}
    states: list[_PublicationState] = []
    latest_checked: datetime | None = None
    try:
        for target in _receipt_registry_targets(profile, root):
            chain = _scan_receipt_chain(
                target=target,
                profile=profile,
                root=root,
                live_module=live_module,
                live_profile=live_profile,
                prerequisites=prerequisites,
            )
            if chain is None:
                raise OutcomeMaterializerConflict(
                    "Outcome receipt date directory has no chain"
                )
            before = _clock_value(clock()).astimezone(timezone.utc)
            state, _ = _reconcile_chain(
                chain,
                profile=profile,
                root=root,
                now=before,
                live_module=live_module,
                live_profile=live_profile,
                prerequisites=prerequisites,
            )
            states.append(state)
            after = _validate_registered_time(chain.tip, now=clock())
            if after < before:
                raise OutcomeMaterializerInputError(
                    "Machine clock moved backwards during receipt recovery"
                )
            latest_checked = after
            durable = _scan_receipt_chain(
                target=target,
                profile=profile,
                root=root,
                live_module=live_module,
                live_profile=live_profile,
                prerequisites=prerequisites,
            )
            if (
                durable is None
                or durable.pointed is None
                or durable.pointed.receipt.sha256 != durable.tip.receipt.sha256
            ):
                raise OutcomeMaterializerConflict(
                    "Receipt recovery did not leave one active chain tip"
                )
            result[target.isoformat()] = durable
    except Exception as exc:
        try:
            for state in reversed(states):
                _rollback_publication(state)
        except Exception as rollback_exc:
            raise OutcomeMaterializerConflict(
                "Receipt-registry recovery could not be rolled back"
            ) from rollback_exc
        raise exc
    return result, tuple(states), latest_checked


def _pending_registered_tip(
    chains: Mapping[str, _ReceiptChain],
    *,
    projection: object,
    source: object,
    profile: Mapping[str, Any],
) -> _RegisteredOutcome | None:
    """Return the sole ledger-unconsumed tip; reject branches and tip rollback."""

    source_records = {
        record.day.isoformat(): record for record in getattr(source, "records", ())
    }
    ledger_revisions = getattr(projection, "revision_ids", None)
    if not isinstance(ledger_revisions, Mapping):
        raise OutcomeMaterializerInputError(
            "Verified ledger projection has no revision registry"
        )
    pending: list[_RegisteredOutcome] = []
    stations = list(
        profile["_deploy_profile"]["source_feed"]["station_order_live"]
    )
    for target_key in sorted(chains):
        chain = chains[target_key]
        tip = chain.tip
        revision = _require_string(
            tip.payload.get("source_revision_id"), name="tip.source_revision_id"
        )
        known = ledger_revisions.get(target_key, {})
        if not isinstance(known, Mapping):
            raise OutcomeMaterializerInputError(
                "Ledger revision registry entry is invalid"
            )
        known_items = list(known.items())
        committed_receipts = (
            chain.receipts if revision in known else chain.receipts[:-1]
        )
        if len(committed_receipts) != len(known_items) or any(
            receipt.payload.get("source_revision_id") != known_revision
            or receipt.exact_object.sha256 != known_sha
            for receipt, (known_revision, known_sha) in zip(
                committed_receipts, known_items
            )
        ):
            raise OutcomeMaterializerConflict(
                "Receipt history is not identical to verified ledger revision order"
            )
        if revision in known:
            latest = next(reversed(tuple(known)))
            if revision != latest:
                raise OutcomeMaterializerConflict(
                    "Active receipt pointer rolled back behind ledger latest revision"
                )
            if tip.exact_object.sha256 != known[revision]:
                raise OutcomeMaterializerConflict(
                    "Receipt-chain tip bytes disagree with verified ledger"
                )
            continue

        previous = tip.previous_receipt
        if known:
            latest = next(reversed(tuple(known)))
            if previous is None:
                raise OutcomeMaterializerConflict(
                    "Pending revision skipped the ledger-known predecessor"
                )
            matches = [
                receipt
                for receipt in chain.receipts
                if receipt.receipt.path == previous.path
                and receipt.receipt.sha256 == previous.sha256
            ]
            if (
                len(matches) != 1
                or matches[0].payload.get("source_revision_id") != latest
                or matches[0].exact_object.sha256 != known[latest]
            ):
                raise OutcomeMaterializerConflict(
                    "Pending receipt tip branched from an older ledger revision"
                )
        elif tip.revision_sequence_id != 1:
            raise OutcomeMaterializerConflict(
                "Multiple unconsumed revisions exist before the first ledger outcome"
            )
        elif (
            getattr(projection, "outstanding_target_date", None)
            != chain.target_date
            and not (
                getattr(projection, "outstanding_target_date", None) is None
                and chain.target_date
                == getattr(projection, "last_finalized_date") + timedelta(days=1)
            )
        ):
            raise OutcomeMaterializerConflict(
                "First receipt tip is outside outstanding/backfill ledger authority"
            )

        current_record = source_records.get(target_key)
        if (
            current_record is None
            and chain.target_date <= getattr(source, "watermark")
        ):
            raise OutcomeMaterializerConflict(
                "Current source lost the pending receipt target record"
            )
        if current_record is not None:
            current_revision = getattr(current_record, "revision_id", None)
            if current_revision in known:
                raise OutcomeMaterializerConflict(
                    "Current source rolled back behind the unconsumed receipt tip"
                )
            if current_revision == revision:
                manifest_artifact = _artifact_from_mapping(
                    tip.payload["source_manifest"],
                    name="pending tip source manifest",
                )
                manifest, _ = _read_json(
                    manifest_artifact.path, name="pending tip input manifest"
                )
                if (
                    not _record_matches_outcome(
                        current_record, tip.payload, stations=stations
                    )
                    or not _record_matches_manifest(
                        current_record,
                        manifest,
                        record_schema_version=profile["_deploy_profile"][
                            "source_feed"
                        ]["record_schema_version"],
                        stations=stations,
                    )
                ):
                    raise OutcomeMaterializerConflict(
                        "Pending revision id was reused with changed source semantics"
                    )
        pending.append(tip)
    if len(pending) > 1:
        raise OutcomeMaterializerConflict(
            "Multiple ledger-unconsumed outcome tips exist"
        )
    return pending[0] if pending else None


def _reconcile_and_classify_registry(
    *,
    profile: Mapping[str, Any],
    root: Path,
    clock: Clock,
    live_module: object,
    live_profile: dict[str, Any],
    prerequisites: object,
    projection: object,
    source: object,
) -> tuple[
    dict[str, _ReceiptChain],
    tuple[_PublicationState, ...],
    datetime | None,
    _RegisteredOutcome | None,
]:
    """Reconcile bytes and roll them back if ledger lineage rejects the tip."""

    chains, states, checked = _reconcile_receipt_registry(
        profile=profile,
        root=root,
        clock=clock,
        live_module=live_module,
        live_profile=live_profile,
        prerequisites=prerequisites,
    )
    try:
        pending = _pending_registered_tip(
            chains, projection=projection, source=source, profile=profile
        )
    except Exception as exc:
        try:
            for state in reversed(states):
                _rollback_publication(state)
        except Exception as rollback_exc:
            raise OutcomeMaterializerConflict(
                "Ledger rejected a receipt tip and prior active state "
                "could not be restored"
            ) from rollback_exc
        raise exc
    return chains, states, checked, pending


def _existing_candidate(
    *,
    profile: Mapping[str, Any],
    root: Path,
    source: object,
    selection: _Selection,
    live_module: object,
    live_profile: dict[str, Any],
    prerequisites: object,
) -> tuple[Artifact, dict[str, Any]] | None:
    """Reuse first bytes when a receipt committed before inbox/status publication."""

    revision = _require_string(
        getattr(selection.record, "revision_id", None),
        name="source.record.revision_id",
    )
    receipt_root = _runtime_path(profile, root, "outcome_receipts")
    receipt_path = _receipt_path(receipt_root, selection.target_date, revision)
    if not receipt_path.exists():
        return None
    active_path = _outcome_inbox_path(profile, root, selection.target_date)
    registered = _load_registered_outcome(
        receipt_path=receipt_path,
        active_path=active_path,
        object_root=_runtime_path(profile, root, "objects"),
        receipt_root=receipt_root,
        profile=profile,
        root=root,
        live_module=live_module,
        live_profile=live_profile,
        prerequisites=prerequisites,
    )
    manifest = _artifact_from_mapping(
        registered.payload["source_manifest"],
        name="registered outcome source manifest",
    )
    manifest_payload = _validate_input_manifest(
        manifest, profile=profile, root=root
    )
    stations = list(
        profile["_deploy_profile"]["source_feed"]["station_order_live"]
    )
    if (
        registered.payload.get("outcome_source_id")
        != getattr(source, "outcome_source_id")
        or not _record_matches_outcome(
            selection.record, registered.payload, stations=stations
        )
        or not _record_matches_manifest(
            selection.record,
            manifest_payload,
            record_schema_version=profile["_deploy_profile"]["source_feed"][
                "record_schema_version"
            ],
            stations=stations,
        )
    ):
        raise OutcomeMaterializerConflict(
            "Source reused a received revision id with changed DailySourceRecord semantics"
        )
    return manifest, registered.payload


def materialize_outcome(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    runtime_root: Path | None = None,
    project_root: Path = ROOT,
    clock: Clock = lambda: datetime.now(timezone.utc),
) -> OutcomeMaterializerResult:
    """Materialize at most one machine-selected outcome while both locks are held."""

    profile = load_config(config_path, project_root=project_root)
    root = _runtime_root(profile, runtime_root, project_root=project_root)
    deploy = profile["_deploy_profile"]
    live_profile = profile["_live_profile"]

    from monitoring import ootang_live_source as source_module
    from monitoring import ootang_prequential_live as live_module

    live_paths = live_module.runtime_paths(live_profile, runtime_root=root)
    deploy_lock_path = _confined_child(
        root, deploy["runtime"]["deploy_lock"], name="deploy.runtime.deploy_lock"
    )
    deploy_lock = _acquire_lock(deploy_lock_path, name="deployment")
    runner_lock = None
    observed_clock = _MonotonicClock(clock)
    last_now: datetime | None = None
    recovery_states: tuple[_PublicationState, ...] = ()
    try:
        runner_lock_path = _confined_child(
            root,
            live_profile["runtime"]["lock"],
            name="live.runtime.lock",
        )
        if runner_lock_path != Path(live_paths.lock):
            raise OutcomeMaterializerConfigError(
                "E2-A runner lock path differs from the confined live runtime"
            )
        runner_lock = _acquire_lock(runner_lock_path, name="E2-A ledger")
        try:
            last_now = observed_clock.sample()
            missing = _missing_prerequisites(profile, root, live_paths)
            if missing:
                status_path = _write_status(
                    profile,
                    root,
                    now=last_now,
                    status="waiting_for_source_model_or_ledger",
                    reason="missing:" + ",".join(missing),
                )
                return OutcomeMaterializerResult(
                    status_path,
                    "waiting_for_source_model_or_ledger",
                    None,
                    None,
                )
            source = source_module.load_current_source(
                deploy, runtime_root=root, project_root=project_root
            )
            if _parse_utc(
                source.exported_at_utc, name="current source exported_at_utc"
            ) > last_now.astimezone(timezone.utc):
                raise OutcomeMaterializerInputError(
                    "Current source snapshot is future-dated relative to machine time"
                )
            prerequisites = live_module.load_prerequisites(live_profile, live_paths)
            if prerequisites is None:
                raise OutcomeMaterializerInputError(
                    "E2-A activation prerequisites disappeared"
                )
            projection = live_module.load_verified_ledger_projection(
                live_profile, live_paths, prerequisites
            )
            activation_source = source_module.load_activation_source(
                deploy, runtime_root=root, project_root=project_root
            )
            changed_activation_date = _activation_change_date(
                source, activation_source
            )
            if changed_activation_date is not None:
                status_now = observed_clock.sample()
                if status_now.astimezone(timezone.utc) < last_now.astimezone(
                    timezone.utc
                ):
                    raise OutcomeMaterializerInputError(
                        "Machine clock moved backwards during epoch-rotation wait"
                    )
                status_path = _write_status(
                    profile,
                    root,
                    now=status_now,
                    status="waiting_epoch_rotation_required",
                    reason=(
                        "current_source_changed_at_or_before_activation_watermark:"
                        f"{changed_activation_date.isoformat()}"
                    ),
                    target=changed_activation_date,
                )
                return OutcomeMaterializerResult(
                    status_path,
                    "waiting_epoch_rotation_required",
                    changed_activation_date,
                    None,
                )

            (
                chains,
                recovery_states,
                recovery_checked,
                pending_tip,
            ) = _reconcile_and_classify_registry(
                profile=profile,
                root=root,
                clock=observed_clock.sample,
                live_module=live_module,
                live_profile=live_profile,
                prerequisites=prerequisites,
                projection=projection,
                source=source,
            )
            if pending_tip is not None:
                pending_target = _parse_date(
                    pending_tip.payload["target_date"], name="pending.target_date"
                )
                pending_path = _outcome_inbox_path(
                    profile, root, pending_target
                )
                pending_manifest = _artifact_from_mapping(
                    pending_tip.payload["source_manifest"],
                    name="pending outcome source manifest",
                )
                pending_now = observed_clock.sample()
                lower_bound = recovery_checked or last_now.astimezone(timezone.utc)
                if pending_now.astimezone(timezone.utc) < lower_bound:
                    raise OutcomeMaterializerInputError(
                        "Machine clock moved backwards after receipt recovery"
                    )
                pending_status = (
                    "materialized"
                    if any(state.changed for state in recovery_states)
                    else "already_materialized_idempotent"
                )
                status_path = _write_status(
                    profile,
                    root,
                    now=pending_now,
                    status=pending_status,
                    reason=(
                        "receipt_chain_tip_recovered_before_new_revision"
                        if pending_status == "materialized"
                        else "ledger_must_consume_registered_chain_tip_before_new_revision"
                    ),
                    target=pending_target,
                    outcome_path=pending_path,
                    input_manifest=pending_manifest,
                    receipt=pending_tip.receipt,
                    exact_object=pending_tip.exact_object,
                    active_receipt_pointer=_artifact_from_path(
                        chains[pending_target.isoformat()].active_pointer_path
                    ),
                )
                return OutcomeMaterializerResult(
                    status_path, pending_status, pending_target, pending_path
                )
            stations = list(deploy["source_feed"]["station_order_live"])
            record_by_date = {record.day: record for record in source.records}

            def validate_known(
                target: date,
                record: object,
                revision_id: str,
                expected_sha: str,
            ) -> None:
                active_path = _outcome_inbox_path(profile, root, target)
                receipt_path = _receipt_path(
                    _runtime_path(profile, root, "outcome_receipts"),
                    target,
                    revision_id,
                )
                if not receipt_path.is_file() or not active_path.is_file():
                    raise OutcomeMaterializerConflict(
                        "Ledger-known latest revision lost its receipt or active copy"
                    )
                registered = _load_registered_outcome(
                    receipt_path=receipt_path,
                    active_path=active_path,
                    object_root=_runtime_path(profile, root, "objects"),
                    receipt_root=_runtime_path(profile, root, "outcome_receipts"),
                    profile=profile,
                    root=root,
                    live_module=live_module,
                    live_profile=live_profile,
                    prerequisites=prerequisites,
                )
                if registered.exact_object.sha256 != expected_sha:
                    raise OutcomeMaterializerConflict(
                        "Ledger-known outcome differs from its receipt archive"
                    )
                if not _record_matches_outcome(
                    record, registered.payload, stations=stations
                ):
                    raise OutcomeMaterializerConflict(
                        "Source reused a known revision id with changed content"
                    )
                input_manifest = _artifact_from_mapping(
                    registered.payload["source_manifest"],
                    name="known outcome source manifest",
                )
                manifest_payload = _validate_input_manifest(
                    input_manifest, profile=profile, root=root
                )
                if not _record_matches_manifest(
                    record,
                    manifest_payload,
                    record_schema_version=deploy["source_feed"][
                        "record_schema_version"
                    ],
                    stations=stations,
                ):
                    raise OutcomeMaterializerConflict(
                        "Source reused a known revision id with changed full record"
                    )
                if active_path.read_bytes() != registered.raw:
                    raise OutcomeMaterializerConflict(
                        "Ledger-known outcome active copy changed"
                    )

            selection = _select_target(
                source, projection, known_validator=validate_known
            )
            if selection is None:
                status_now = observed_clock.sample()
                lower_bound = recovery_checked or last_now.astimezone(timezone.utc)
                if status_now.astimezone(timezone.utc) < lower_bound:
                    raise OutcomeMaterializerInputError(
                        "Machine clock moved backwards during waiting poll"
                    )
                status_path = _write_status(
                    profile,
                    root,
                    now=status_now,
                    status="waiting_for_finalized_outcome",
                    reason="no_pending_revision_outstanding_target_or_backfill",
                )
                return OutcomeMaterializerResult(
                    status_path, "waiting_for_finalized_outcome", None, None
                )
            # The source id is attached only after recursive source verification;
            # it is not accepted from an external outcome payload.
            selected_record = record_by_date[selection.target_date]
            if selected_record is not selection.record:
                raise OutcomeMaterializerInputError(
                    "Selected outcome record changed identity"
                )
            _validate_finalization(
                selection.record,
                source_exported_at_utc=source.exported_at_utc,
                now=last_now,
            )
            existing_candidate = _existing_candidate(
                profile=profile,
                root=root,
                source=source,
                selection=selection,
                live_module=live_module,
                live_profile=live_profile,
                prerequisites=prerequisites,
            )
            if existing_candidate is None:
                manifest_payload = _build_input_manifest(profile, source, selection)
                input_manifest = _materialize_input_manifest(
                    profile, root, manifest_payload
                )
                payload = _outcome_payload(
                    profile,
                    selection,
                    input_manifest,
                    outcome_source_id=source.outcome_source_id,
                )
            else:
                input_manifest, payload = existing_candidate
            status, registered, completed, publication_state = _publish_candidate(
                profile=profile,
                root=root,
                selection=selection,
                source_exported_at_utc=source.exported_at_utc,
                input_manifest=input_manifest,
                payload=payload,
                clock=observed_clock.sample,
                live_module=live_module,
                live_profile=live_profile,
                prerequisites=prerequisites,
            )
            outcome_path = _outcome_inbox_path(
                profile, root, selection.target_date
            )
            try:
                status_path = _write_status(
                    profile,
                    root,
                    now=completed,
                    status=status,
                    reason=(
                        f"atomic_{selection.kind}_outcome_materialized"
                        if status == "materialized"
                        else "same_target_revision_exact_bytes_already_active"
                    ),
                    target=selection.target_date,
                    outcome_path=outcome_path,
                    input_manifest=input_manifest,
                    receipt=registered.receipt,
                    exact_object=registered.exact_object,
                    active_receipt_pointer=_artifact_from_path(
                        publication_state.pointer_path
                    ),
                )
            except Exception:
                _rollback_publication(publication_state)
                raise
            return OutcomeMaterializerResult(
                status_path, status, selection.target_date, outcome_path
            )
        except OutcomeMaterializerBusy:
            raise
        except Exception as exc:
            rollback_error: Exception | None = None
            try:
                for state in reversed(recovery_states):
                    _rollback_publication(state)
            except Exception as rollback_exc:
                rollback_error = rollback_exc
            normalized = (
                exc
                if isinstance(exc, OutcomeMaterializerError)
                else OutcomeMaterializerInputError(
                    f"Validated outcome operation failed: {type(exc).__name__}:{exc}"
                )
            )
            if rollback_error is not None:
                normalized = OutcomeMaterializerConflict(
                    "Invalid receipt tip was rejected but prior active state "
                    "could not be restored"
                )
            blocked_now = (
                observed_clock.last or last_now or datetime.now(timezone.utc)
            )
            try:
                sampled = observed_clock.sample()
                if sampled.astimezone(timezone.utc) >= blocked_now.astimezone(
                    timezone.utc
                ):
                    blocked_now = sampled
            except Exception:
                pass
            _write_status(
                profile,
                root,
                now=blocked_now,
                status="blocked_integrity",
                reason=f"{type(normalized).__name__}:{normalized}",
            )
            raise normalized
        finally:
            if runner_lock is not None:
                fcntl.flock(runner_lock.fileno(), fcntl.LOCK_UN)
                runner_lock.close()
    finally:
        fcntl.flock(deploy_lock.fileno(), fcntl.LOCK_UN)
        deploy_lock.close()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--runtime-root", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = materialize_outcome(
            config_path=args.config, runtime_root=args.runtime_root
        )
    except OutcomeMaterializerBusy as exc:
        print(f"[ootang-outcome-materializer] busy: {exc}", file=sys.stderr)
        return 3
    except OutcomeMaterializerError as exc:
        print(f"[ootang-outcome-materializer] blocked: {exc}", file=sys.stderr)
        return 2
    print(
        f"[ootang-outcome-materializer] status={result.status} "
        f"target={result.target_date or 'none'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
