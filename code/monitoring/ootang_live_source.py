"""Strict, content-addressed source ingestion for the Ootang E2-B path.

The module accepts only raw finalized daily observations.  Model features are
derived again inside this trusted boundary, and every durable scientific
artifact is addressed by the SHA-256 of its bytes.  This is engineering
infrastructure: it does not alter the E2-A evidence-eligibility boundary.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import fcntl
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DEPLOY_CONFIG_PATH = (
    ROOT / "config" / "ootang_prequential_deploy.v1.json"
)
HEX_DIGITS = frozenset("0123456789abcdef")
UTC_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)

DATASET_SCHEMA_VERSION = "ootang_canonical_model_source_v1"
SEMANTIC_MANIFEST_SCHEMA_VERSION = "ootang_source_semantic_manifest_v1"
CURRENT_POINTER_SCHEMA_VERSION = "ootang_source_current_pointer_v1"
STATUS_SCHEMA_VERSION = "ootang_source_ingest_status_v1"
RUNTIME_RELATIVE_PATH_KEYS = frozenset(
    {
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
)


class SourceError(RuntimeError):
    """Base class for fail-closed source-ingestion errors."""


class SourceConfigError(SourceError):
    """Raised when a deployment contract or bound profile has changed."""


class SourceInputError(SourceError):
    """Raised when source bytes violate their strict semantic contract."""


class SourceIntegrityError(SourceError):
    """Raised when a materialized content-addressed object is inconsistent."""


class SourceBusyError(SourceError):
    """Raised when another deploy cycle owns the machine-ingest lock."""


@dataclass(frozen=True)
class ArtifactRef:
    path: Path
    sha256: str
    size_bytes: int

    def as_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path.resolve()),
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class CanonicalSource:
    frame: pd.DataFrame
    watermark: date
    outcome_source_id: str
    exported_at_utc: str
    records: tuple[DailySourceRecord, ...]
    dataset: ArtifactRef
    semantic_manifest: ArtifactRef
    activation_manifest: ArtifactRef


@dataclass(frozen=True)
class SourceIngestResult:
    status_path: Path
    status: str
    source: CanonicalSource | None


@dataclass(frozen=True)
class DailySourceRecord:
    """Immutable per-natural-day raw values and source timing provenance."""

    day: date
    revision_id: str
    observed_at_utc: str
    available_at_utc: str
    finalized_at_utc: str
    rainfall_mm: float
    reservoir_water_level_m: float
    displacement_mm: dict[str, float]


@dataclass(frozen=True)
class _Feed:
    raw: bytes
    sha256: str
    outcome_source_id: str
    exported_at_text: str
    exported_at: datetime
    records: tuple[DailySourceRecord, ...]


def _reject_constant(value: str) -> None:
    raise SourceInputError(f"forbidden non-finite JSON constant: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SourceInputError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _decode_json(raw: bytes, *, name: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise SourceInputError(f"{name} is not UTF-8 JSON") from exc
    except json.JSONDecodeError as exc:
        raise SourceInputError(f"{name} is not strict JSON") from exc
    if not isinstance(value, dict):
        raise SourceInputError(f"{name} must be a JSON object")
    _reject_nonfinite_tree(value, name=name)
    return value


def _reject_nonfinite_tree(value: object, *, name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise SourceInputError(f"{name} contains a non-finite JSON number")
    if isinstance(value, dict):
        for key, child in value.items():
            _reject_nonfinite_tree(child, name=f"{name}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_nonfinite_tree(child, name=f"{name}[{index}]")


def _load_json_snapshot(path: Path, *, name: str) -> tuple[dict[str, Any], bytes, str]:
    try:
        with path.open("rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise SourceInputError(f"cannot read {name}: {path}") from exc
    return _decode_json(raw, name=name), raw, hashlib.sha256(raw).hexdigest()


def _canonical_bytes(payload: object) -> bytes:
    try:
        return (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise SourceIntegrityError("artifact is not canonical finite JSON") from exc


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _exact_object(value: object, keys: set[str], *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SourceInputError(f"{name} must be an object")
    actual = set(value)
    if actual != keys:
        raise SourceInputError(
            f"{name} keys changed; missing={sorted(keys - actual)}, "
            f"extra={sorted(actual - keys)}"
        )
    return value


def _nonempty_string(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise SourceInputError(f"{name} must be a nonempty trimmed string")
    return value


def _finite_float(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SourceInputError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise SourceInputError(f"{name} must be finite")
    return result


def _canonical_date(value: object, *, name: str) -> date:
    if not isinstance(value, str):
        raise SourceInputError(f"{name} must be canonical YYYY-MM-DD")
    try:
        result = date.fromisoformat(value)
    except ValueError as exc:
        raise SourceInputError(f"{name} must be canonical YYYY-MM-DD") from exc
    if result.isoformat() != value:
        raise SourceInputError(f"{name} must be canonical YYYY-MM-DD")
    return result


def _utc(value: object, *, name: str) -> datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise SourceInputError(f"{name} must be canonical RFC 3339 UTC")
    try:
        result = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise SourceInputError(f"{name} is not a valid UTC timestamp") from exc
    if result.utcoffset() != timedelta(0):
        raise SourceInputError(f"{name} must be UTC")
    return result


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise SourceInputError("injected machine time must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _resolve(path: str | Path, *, base: Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate.resolve()


def _require_sha256(value: object, *, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in HEX_DIGITS for character in value)
    ):
        raise SourceInputError(f"{name} must be a lowercase SHA-256")
    return value


def _artifact_from_payload(value: object, *, name: str) -> ArtifactRef:
    record = _exact_object(
        value, {"path", "sha256", "size_bytes"}, name=name
    )
    path = Path(_nonempty_string(record["path"], name=f"{name}.path")).resolve()
    sha256 = _require_sha256(record["sha256"], name=f"{name}.sha256")
    size = record["size_bytes"]
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise SourceInputError(f"{name}.size_bytes must be a nonnegative integer")
    if not path.is_file():
        raise SourceIntegrityError(f"{name} is missing: {path}")
    actual_sha, actual_size = _sha256_file(path)
    if (actual_sha, actual_size) != (sha256, size):
        raise SourceIntegrityError(f"{name} bytes do not match their artifact record")
    return ArtifactRef(path=path, sha256=sha256, size_bytes=size)


def _read_verified_artifact(artifact: ArtifactRef, *, name: str) -> bytes:
    try:
        with artifact.path.open("rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise SourceIntegrityError(f"cannot read {name}: {artifact.path}") from exc
    if len(raw) != artifact.size_bytes or _sha256_bytes(raw) != artifact.sha256:
        raise SourceIntegrityError(f"{name} changed while being loaded")
    return raw


def _load_json_artifact(
    artifact: ArtifactRef, *, name: str
) -> dict[str, Any]:
    return _decode_json(_read_verified_artifact(artifact, name=name), name=name)


def _runtime_root(profile: dict[str, Any], runtime_root: Path | None, project_root: Path) -> Path:
    try:
        if runtime_root is not None:
            return runtime_root.resolve()
        value = _nonempty_string(profile["runtime"]["root"], name="runtime.root")
        return _resolve(value, base=project_root)
    except (OSError, RuntimeError) as exc:
        raise SourceConfigError("runtime.root cannot be safely resolved") from exc


def _runtime_path(profile: dict[str, Any], key: str, *, root: Path) -> Path:
    """Resolve one configured runtime child without permitting root escape."""

    if key not in RUNTIME_RELATIVE_PATH_KEYS:
        raise SourceConfigError(f"unknown runtime path key: {key}")
    value = _nonempty_string(profile["runtime"][key], name=f"runtime.{key}")
    relative = Path(value)
    if relative.is_absolute():
        raise SourceConfigError(f"runtime.{key} must be relative to runtime.root")
    if not relative.parts or ".." in relative.parts:
        raise SourceConfigError(
            f"runtime.{key} must not be empty or contain parent traversal"
        )
    try:
        resolved_root = root.resolve()
        resolved = (resolved_root / relative).resolve(strict=False)
        resolved.relative_to(resolved_root)
    except (OSError, RuntimeError) as exc:
        raise SourceConfigError(
            f"runtime.{key} cannot be safely resolved below runtime.root"
        ) from exc
    except ValueError as exc:
        raise SourceConfigError(
            f"runtime.{key} escapes runtime.root through traversal or symlink"
        ) from exc
    return resolved


def _validate_runtime_paths(profile: dict[str, Any], *, root: Path) -> None:
    for key in RUNTIME_RELATIVE_PATH_KEYS:
        _runtime_path(profile, key, root=root)


def _content_object_from_payload(
    value: object,
    *,
    name: str,
    directory: Path,
    suffix: str,
) -> ArtifactRef:
    """Load an artifact only from its canonical SHA-named object location."""

    record = _exact_object(
        value, {"path", "sha256", "size_bytes"}, name=name
    )
    sha256 = _require_sha256(record["sha256"], name=f"{name}.sha256")
    expected = _runtime_child_path(
        directory,
        f"{sha256}.{suffix}",
        name=name,
    )
    configured_path = _nonempty_string(record["path"], name=f"{name}.path")
    if configured_path != str(expected):
        raise SourceIntegrityError(
            f"{name} is not at its canonical content-addressed path"
        )
    return _artifact_from_payload(record, name=name)


def _runtime_child_path(root: Path, relative: str | Path, *, name: str) -> Path:
    """Resolve a generated child while detecting existing-parent symlink escape."""

    candidate = Path(relative)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        raise SourceIntegrityError(f"{name} is not a confined relative child")
    try:
        resolved_root = root.resolve()
        resolved = (resolved_root / candidate).resolve(strict=False)
        resolved.relative_to(resolved_root)
    except (OSError, RuntimeError) as exc:
        raise SourceIntegrityError(f"{name} cannot be safely resolved") from exc
    except ValueError as exc:
        raise SourceIntegrityError(f"{name} escapes its trusted directory") from exc
    return resolved


def load_deploy_profile(
    path: Path = DEFAULT_DEPLOY_CONFIG_PATH,
    *,
    project_root: Path = ROOT,
) -> dict[str, Any]:
    """Load the strict E2-B contract and verify its bound E2-A profile bytes."""

    resolved_path = path.resolve()
    payload, _, profile_sha256 = _load_json_snapshot(
        resolved_path, name="E2-B deploy profile"
    )
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
    try:
        _exact_object(payload, expected_top, name="E2-B deploy profile")
        if payload["schema_version"] != "ootang_prequential_deploy_profile_v1":
            raise SourceConfigError("E2-B deploy profile schema changed")
        if payload["profile_id"] != "ootang-prequential-deploy-v1":
            raise SourceConfigError("E2-B deploy profile id changed")
        if payload["profile_version"] != "1.0.0-engineering":
            raise SourceConfigError("E2-B deploy profile version changed")
        if payload["case"] != "ootang":
            raise SourceConfigError("E2-B deploy profile must remain Ootang-only")
        if payload["artifact_status"] != "e2b_engineering_only_not_live_evidence":
            raise SourceConfigError("E2-B evidence boundary changed")
        for flag in (
            "formal_warning_output",
            "independent_label_used",
            "confirmatory_external_validation",
            "vajont_used",
            "default_pipeline_member",
        ):
            if payload[flag] is not False:
                raise SourceConfigError(f"E2-B flag {flag} must remain false")
        live = _exact_object(
            payload["live_profile"], {"path", "expected_sha256"}, name="live_profile"
        )
        expected_sha = _require_sha256(
            live["expected_sha256"], name="live_profile.expected_sha256"
        )
        live_path = _resolve(
            _nonempty_string(live["path"], name="live_profile.path"),
            base=project_root,
        )
        try:
            actual_sha, _ = _sha256_file(live_path)
        except OSError as exc:
            raise SourceConfigError("bound E2-A live profile is missing") from exc
        if actual_sha != expected_sha:
            raise SourceConfigError("bound E2-A live profile SHA-256 changed")
        runtime = _exact_object(
            payload["runtime"],
            {
                "root", "incoming_feed", "source_status", "bundle_status",
                "issue_status", "current_source_pointer", "activation_source_manifest",
                "model_manifest", "issue_inbox", "issue_receipts", "objects",
                "deploy_lock",
            },
            name="runtime",
        )
        _nonempty_string(runtime["root"], name="runtime.root")
        configured_root = _runtime_root(payload, None, project_root)
        _validate_runtime_paths(payload, root=configured_root)
        historical = _exact_object(
            payload["historical_base"],
            {"path", "sha256", "last_date", "rows", "role"},
            name="historical_base",
        )
        _require_sha256(historical["sha256"], name="historical_base.sha256")
        _canonical_date(historical["last_date"], name="historical_base.last_date")
        if (
            isinstance(historical["rows"], bool)
            or not isinstance(historical["rows"], int)
            or historical["rows"] <= 0
        ):
            raise SourceConfigError("historical_base.rows must be positive")
        geometry = _exact_object(
            payload["station_geometry"], {"path", "sha256"}, name="station_geometry"
        )
        _require_sha256(geometry["sha256"], name="station_geometry.sha256")
        source = _exact_object(
            payload["source_feed"],
            {
                "schema_version", "record_schema_version", "date_timezone",
                "recorded_time_timezone", "expected_frequency",
                "require_contiguous_extension", "require_all_records_finalized",
                "require_revision_id", "require_finalized_before_next_natural_day",
                "station_order_live", "station_order_model", "raw_fields", "units",
                "derived_features",
            },
            name="source_feed",
        )
        if source.get("expected_frequency") != "P1D":
            raise SourceConfigError("source frequency must remain P1D")
        if source.get("date_timezone") != "Asia/Shanghai":
            raise SourceConfigError("source natural-day timezone changed")
        if source.get("recorded_time_timezone") != "UTC":
            raise SourceConfigError("source timestamp timezone changed")
        if source.get("station_order_live") != [
            "ATU1", "ATU2", "ATU3", "ATU4", "ATU5", "MJ1", "MJ3", "MJ9"
        ]:
            raise SourceConfigError("live station order changed")
        if source.get("station_order_model") != [
            "MJ9", "MJ1", "MJ3", "ATU1", "ATU2", "ATU3", "ATU4", "ATU5"
        ]:
            raise SourceConfigError("model station order changed")
        for flag in (
            "require_contiguous_extension",
            "require_all_records_finalized",
            "require_revision_id",
            "require_finalized_before_next_natural_day",
        ):
            if source.get(flag) is not True:
                raise SourceConfigError(f"source_feed.{flag} must remain true")
        if source.get("raw_fields") != {
            "rainfall": "rainfall_mm",
            "reservoir_water_level": "reservoir_water_level_m",
            "displacement": "displacement_mm",
        }:
            raise SourceConfigError("raw source fields changed")
        if source.get("units") != {
            "rainfall": "mm_per_natural_day",
            "reservoir_water_level": "m",
            "displacement": "mm",
        }:
            raise SourceConfigError("raw source units changed")
        if source.get("derived_features") != {
            "rwl_rate": "elapsed_day_first_difference",
            "rain_windows_days": [7, 15, 30],
            "derive_inside_trusted_code": True,
            "accept_external_derived_columns": False,
        }:
            raise SourceConfigError("trusted feature derivation contract changed")
        model = payload["model"]
        expected_displacement = [
            f"{station}_disp" for station in source["station_order_model"]
        ]
        if model.get("displacement_columns") != expected_displacement:
            raise SourceConfigError("model displacement columns changed")
        if model.get("exogenous_columns") != [
            "RWL", "RWL_rate", "Rain_cum7", "Rain_cum15", "Rain_cum30"
        ]:
            raise SourceConfigError("model exogenous columns changed")
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
        if payload["engineering_capabilities"] != expected_capabilities:
            raise SourceConfigError("E2-B engineering capability boundary changed")
    except SourceInputError as exc:
        raise SourceConfigError(str(exc)) from exc
    payload["_profile_path"] = str(resolved_path)
    payload["_profile_sha256"] = profile_sha256
    payload["_project_root"] = str(project_root.resolve())
    return payload


def _parse_feed(raw: bytes, profile: dict[str, Any], *, now: datetime | None) -> _Feed:
    payload = _decode_json(raw, name="daily finalized source feed")
    feed = _exact_object(
        payload,
        {"schema_version", "outcome_source_id", "exported_at_utc", "records"},
        name="daily finalized source feed",
    )
    contract = profile["source_feed"]
    if feed["schema_version"] != contract["schema_version"]:
        raise SourceInputError("daily source feed schema changed")
    source_id = _nonempty_string(
        feed["outcome_source_id"], name="feed.outcome_source_id"
    )
    exported_text = _nonempty_string(
        feed["exported_at_utc"], name="feed.exported_at_utc"
    )
    exported = _utc(exported_text, name="feed.exported_at_utc")
    if now is not None:
        if now.tzinfo is None:
            raise SourceInputError("injected machine time must be timezone-aware")
        if exported > now.astimezone(timezone.utc):
            raise SourceInputError("feed export is future-dated")
    raw_records = feed["records"]
    if not isinstance(raw_records, list):
        raise SourceInputError("feed.records must be a list")
    stations = contract["station_order_live"]
    rainfall_key = contract["raw_fields"]["rainfall"]
    rwl_key = contract["raw_fields"]["reservoir_water_level"]
    displacement_key = contract["raw_fields"]["displacement"]
    record_keys = {
        "schema_version",
        "date",
        "revision_id",
        "observed_at_utc",
        "available_at_utc",
        "finalized_at_utc",
        "finalized",
        rainfall_key,
        rwl_key,
        displacement_key,
    }
    parsed: list[DailySourceRecord] = []
    prior_day: date | None = None
    prior_observed: datetime | None = None
    prior_available: datetime | None = None
    prior_finalized: datetime | None = None
    zone = ZoneInfo(contract["date_timezone"])
    for index, raw_record in enumerate(raw_records):
        record = _exact_object(
            raw_record, record_keys, name=f"feed.records[{index}]"
        )
        if record["schema_version"] != contract["record_schema_version"]:
            raise SourceInputError(f"feed.records[{index}] schema changed")
        day = _canonical_date(record["date"], name=f"feed.records[{index}].date")
        if prior_day is not None and day <= prior_day:
            raise SourceInputError("feed dates must be unique and strictly increasing")
        revision_id = _nonempty_string(
            record["revision_id"], name=f"feed.records[{index}].revision_id"
        )
        if record["finalized"] is not True:
            raise SourceInputError(f"feed.records[{index}] is not finalized")
        observed = _utc(
            record["observed_at_utc"], name=f"feed.records[{index}].observed_at_utc"
        )
        available = _utc(
            record["available_at_utc"], name=f"feed.records[{index}].available_at_utc"
        )
        finalized = _utc(
            record["finalized_at_utc"], name=f"feed.records[{index}].finalized_at_utc"
        )
        if not (observed <= available <= finalized <= exported):
            raise SourceInputError(
                f"feed.records[{index}] timestamps/export are not monotone"
            )
        day_start = datetime.combine(day, time.min, tzinfo=zone).astimezone(timezone.utc)
        next_boundary = datetime.combine(
            day + timedelta(days=1), time.min, tzinfo=zone
        ).astimezone(timezone.utc)
        if observed < day_start:
            raise SourceInputError(f"feed.records[{index}] observed before its natural day")
        if finalized >= next_boundary:
            raise SourceInputError(
                f"feed.records[{index}] was not finalized before the next natural day"
            )
        if prior_observed is not None and observed <= prior_observed:
            raise SourceInputError("observed timestamps must be strictly increasing")
        if prior_available is not None and available <= prior_available:
            raise SourceInputError("available timestamps must be strictly increasing")
        if prior_finalized is not None and finalized <= prior_finalized:
            raise SourceInputError("finalized timestamps must be strictly increasing")
        rainfall = _finite_float(
            record[rainfall_key], name=f"feed.records[{index}].{rainfall_key}"
        )
        if rainfall < 0:
            raise SourceInputError(f"feed.records[{index}] rainfall must be nonnegative")
        rwl = _finite_float(record[rwl_key], name=f"feed.records[{index}].{rwl_key}")
        displacement = _exact_object(
            record[displacement_key], set(stations), name=f"feed.records[{index}].{displacement_key}"
        )
        if list(displacement) != stations:
            raise SourceInputError(
                f"feed.records[{index}] displacement station order changed"
            )
        displacements = {
            station: _finite_float(
                displacement[station],
                name=f"feed.records[{index}].{displacement_key}.{station}",
            )
            for station in stations
        }
        parsed.append(
            DailySourceRecord(
                day=day,
                revision_id=revision_id,
                observed_at_utc=record["observed_at_utc"],
                available_at_utc=record["available_at_utc"],
                finalized_at_utc=record["finalized_at_utc"],
                rainfall_mm=rainfall,
                reservoir_water_level_m=rwl,
                displacement_mm=displacements,
            )
        )
        prior_day = day
        prior_observed = observed
        prior_available = available
        prior_finalized = finalized
    return _Feed(
        raw=raw,
        sha256=_sha256_bytes(raw),
        outcome_source_id=source_id,
        exported_at_text=exported_text,
        exported_at=exported,
        records=tuple(parsed),
    )


def _load_historical(profile: dict[str, Any], *, project_root: Path) -> tuple[pd.DataFrame, ArtifactRef]:
    contract = profile["historical_base"]
    path = _resolve(contract["path"], base=project_root)
    try:
        with path.open("rb") as handle:
            source_raw = handle.read()
    except OSError as exc:
        raise SourceIntegrityError("cannot read frozen historical base") from exc
    actual_sha = _sha256_bytes(source_raw)
    size = len(source_raw)
    if actual_sha != contract["sha256"]:
        raise SourceIntegrityError("frozen historical base SHA-256 changed")
    try:
        raw = pd.read_csv(io.BytesIO(source_raw), encoding="utf-8-sig")
    except Exception as exc:
        raise SourceInputError("cannot parse frozen historical base CSV") from exc
    if len(raw) != contract["rows"]:
        raise SourceIntegrityError("frozen historical row count changed")
    required = ["Date", "Rainfall/mm", "RWL/m"] + [
        f"{station}/mm" for station in profile["source_feed"]["station_order_model"]
    ]
    missing = [column for column in required if column not in raw.columns]
    if missing:
        raise SourceIntegrityError(f"frozen historical columns missing: {missing}")
    parsed_dates = pd.to_datetime(raw["Date"], format="%Y-%m-%d", errors="raise")
    expected_dates = pd.date_range(parsed_dates.iloc[0], parsed_dates.iloc[-1], freq="D")
    if len(expected_dates) != len(parsed_dates) or not parsed_dates.reset_index(drop=True).equals(
        pd.Series(expected_dates)
    ):
        raise SourceIntegrityError("frozen historical dates are not exact daily data")
    if parsed_dates.iloc[-1].date().isoformat() != contract["last_date"]:
        raise SourceIntegrityError("frozen historical last date changed")
    numeric = raw[required[1:]].apply(pd.to_numeric, errors="raise").astype(float)
    if not all(math.isfinite(value) for value in numeric.to_numpy().ravel()):
        raise SourceIntegrityError("frozen historical model inputs are non-finite")
    frame = pd.DataFrame({"Date": parsed_dates.dt.strftime("%Y-%m-%d")})
    for station in profile["source_feed"]["station_order_model"]:
        frame[f"{station}_disp"] = numeric[f"{station}/mm"]
    frame["RWL"] = numeric["RWL/m"]
    frame["Rain"] = numeric["Rainfall/mm"]
    return frame, ArtifactRef(path=path, sha256=actual_sha, size_bytes=size)


def derive_model_frame(raw_frame: pd.DataFrame, profile: dict[str, Any]) -> pd.DataFrame:
    """Derive the exact trusted model columns from contiguous raw daily rows."""

    expected_raw = [
        "Date",
        *profile["model"]["displacement_columns"],
        "RWL",
        "Rain",
    ]
    if list(raw_frame.columns) != expected_raw:
        raise SourceInputError("trusted raw frame columns or order changed")
    frame = raw_frame.copy()
    parsed_dates = pd.to_datetime(frame["Date"], format="%Y-%m-%d", errors="raise")
    elapsed_days = parsed_dates.diff().dt.total_seconds() / 86_400.0
    if not elapsed_days.iloc[1:].eq(1.0).all():
        raise SourceInputError("trusted raw frame must be exactly daily and contiguous")
    frame["RWL_rate"] = frame["RWL"].diff() / elapsed_days
    for window in profile["source_feed"]["derived_features"]["rain_windows_days"]:
        frame[f"Rain_cum{window}"] = frame["Rain"].rolling(window).sum()
    columns = [
        "Date",
        *profile["model"]["displacement_columns"],
        "RWL",
        "RWL_rate",
        "Rain",
        *[
            f"Rain_cum{window}"
            for window in profile["source_feed"]["derived_features"]["rain_windows_days"]
        ],
    ]
    frame = frame.loc[:, columns].dropna().reset_index(drop=True)
    numeric = frame.loc[:, columns[1:]].to_numpy(dtype=float)
    if not all(math.isfinite(value) for value in numeric.ravel()):
        raise SourceIntegrityError("derived model frame contains non-finite values")
    return frame


def _combine_source(
    historical: pd.DataFrame, feed: _Feed, profile: dict[str, Any]
) -> pd.DataFrame:
    extension_rows: list[dict[str, object]] = []
    for record in feed.records:
        row: dict[str, object] = {"Date": record.day.isoformat()}
        for station in profile["source_feed"]["station_order_model"]:
            row[f"{station}_disp"] = record.displacement_mm[station]
        row["RWL"] = record.reservoir_water_level_m
        row["Rain"] = record.rainfall_mm
        extension_rows.append(row)
    extension = pd.DataFrame(extension_rows, columns=historical.columns)
    return derive_model_frame(
        pd.concat([historical, extension], ignore_index=True), profile
    )


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
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


def _atomic_write_once(path: Path, raw: bytes) -> None:
    """Publish immutable bytes without replacing an already published path."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != raw:
                raise SourceIntegrityError(
                    "immutable activation manifest already exists with different bytes"
                )
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_content_object(directory: Path, raw: bytes, *, suffix: str) -> ArtifactRef:
    sha256 = _sha256_bytes(raw)
    path = directory / f"{sha256}.{suffix}"
    directory.mkdir(parents=True, exist_ok=True)
    if path.exists():
        actual_sha, actual_size = _sha256_file(path)
        if (actual_sha, actual_size) != (sha256, len(raw)):
            raise SourceIntegrityError(f"content-addressed object is corrupt: {path}")
    else:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{sha256}.", dir=directory
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                actual_sha, actual_size = _sha256_file(path)
                if (actual_sha, actual_size) != (sha256, len(raw)):
                    raise SourceIntegrityError(
                        f"raced content-addressed object is corrupt: {path}"
                    )
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary.exists():
                temporary.unlink()
    return ArtifactRef(path=path.resolve(), sha256=sha256, size_bytes=len(raw))


def _dataset_payload(frame: pd.DataFrame, profile: dict[str, Any], watermark: date) -> dict[str, object]:
    rows: list[list[object]] = []
    for values in frame.itertuples(index=False, name=None):
        rows.append([values[0], *[float(value) for value in values[1:]]])
    return {
        "schema_version": DATASET_SCHEMA_VERSION,
        "case": "ootang",
        "date_timezone": profile["source_feed"]["date_timezone"],
        "columns": list(frame.columns),
        "maximum_complete_finalized_date": watermark.isoformat(),
        "rows": rows,
    }


def _semantic_payload(
    profile: dict[str, Any],
    *,
    frame: pd.DataFrame,
    feed: _Feed,
    feed_artifact: ArtifactRef,
    historical_artifact: ArtifactRef,
    dataset_artifact: ArtifactRef,
) -> dict[str, object]:
    first_extension = feed.records[0].day
    last_extension = feed.records[-1].day
    revision_digest = hashlib.sha256(
        _canonical_bytes(
            [[record.day.isoformat(), record.revision_id] for record in feed.records]
        )
    ).hexdigest()
    return {
        "schema_version": SEMANTIC_MANIFEST_SCHEMA_VERSION,
        "case": "ootang",
        "outcome_source_id": feed.outcome_source_id,
        "maximum_complete_finalized_date": last_extension.isoformat(),
        "canonical_dataset": dataset_artifact.as_dict(),
        "lineage": {
            "historical_base": {
                **historical_artifact.as_dict(),
                "rows": profile["historical_base"]["rows"],
                "last_date": profile["historical_base"]["last_date"],
                "role": profile["historical_base"]["role"],
            },
            "daily_feed_snapshot": {
                **feed_artifact.as_dict(),
                "schema_version": profile["source_feed"]["schema_version"],
                "exported_at_utc": feed.exported_at_text,
                "first_date": first_extension.isoformat(),
                "last_date": last_extension.isoformat(),
                "rows": len(feed.records),
                "date_revision_pairs_sha256": revision_digest,
            },
        },
        "semantics": {
            "date_timezone": profile["source_feed"]["date_timezone"],
            "recorded_time_timezone": profile["source_feed"]["recorded_time_timezone"],
            "frequency": profile["source_feed"]["expected_frequency"],
            "station_order_live": profile["source_feed"]["station_order_live"],
            "station_order_model": profile["source_feed"]["station_order_model"],
            "model_columns": list(frame.columns),
            "units": profile["source_feed"]["units"],
            "derived_features": profile["source_feed"]["derived_features"],
            "external_derived_columns_accepted": False,
            "all_records_finalized": True,
            "daily_continuity_verified": True,
            "finite_values_verified": True,
        },
        "dataset_summary": {
            "rows": len(frame),
            "first_date": str(frame.iloc[0]["Date"]),
            "last_date": str(frame.iloc[-1]["Date"]),
        },
    }


def _activation_payload(
    profile: dict[str, Any], feed: _Feed, semantic: ArtifactRef
) -> dict[str, object]:
    latest = feed.records[-1]
    return {
        "schema_version": "ootang_live_source_snapshot_v1",
        "case": "ootang",
        "captured_at_utc": feed.exported_at_text,
        "maximum_complete_finalized_date": latest.day.isoformat(),
        "stations": profile["source_feed"]["station_order_live"],
        "outcome_source_id": feed.outcome_source_id,
        "data_manifest": semantic.as_dict(),
        "latest_finalized_displacement_mm": latest.displacement_mm,
    }


def _load_activation(
    path: Path, profile: dict[str, Any], *, objects: Path
) -> ArtifactRef:
    payload, raw, sha256 = _load_json_snapshot(path, name="activation source manifest")
    record = _exact_object(
        payload,
        {
            "schema_version", "case", "captured_at_utc",
            "maximum_complete_finalized_date", "stations", "outcome_source_id",
            "data_manifest", "latest_finalized_displacement_mm",
        },
        name="activation source manifest",
    )
    if record["schema_version"] != "ootang_live_source_snapshot_v1":
        raise SourceIntegrityError("activation source schema changed")
    if record["case"] != "ootang":
        raise SourceIntegrityError("activation source case changed")
    _utc(record["captured_at_utc"], name="activation.captured_at_utc")
    baseline = date.fromisoformat(profile["historical_base"]["last_date"])
    if _canonical_date(
        record["maximum_complete_finalized_date"], name="activation.watermark"
    ) <= baseline:
        raise SourceIntegrityError("activation watermark is not post-baseline")
    stations = profile["source_feed"]["station_order_live"]
    if record["stations"] != stations:
        raise SourceIntegrityError("activation stations changed")
    _nonempty_string(record["outcome_source_id"], name="activation.outcome_source_id")
    _content_object_from_payload(
        record["data_manifest"],
        name="activation.data_manifest",
        directory=objects,
        suffix="source-manifest.json",
    )
    displacement = _exact_object(
        record["latest_finalized_displacement_mm"], set(stations),
        name="activation.latest_finalized_displacement_mm",
    )
    if list(displacement) != stations:
        raise SourceIntegrityError("activation displacement station order changed")
    for station in stations:
        _finite_float(displacement[station], name=f"activation.displacement.{station}")
    return ArtifactRef(path=path.resolve(), sha256=sha256, size_bytes=len(raw))


def _write_status(
    path: Path,
    profile: dict[str, Any],
    *,
    status: str,
    reason: str,
    checked_at: datetime,
    source: CanonicalSource | None = None,
    activation_created: bool = False,
) -> Path:
    payload: dict[str, object] = {
        "schema_version": STATUS_SCHEMA_VERSION,
        "profile_id": profile["profile_id"],
        "deploy_profile_sha256": profile["_profile_sha256"],
        "artifact_status": profile["artifact_status"],
        "status": status,
        "reason": reason,
        "checked_at_utc": _utc_text(checked_at),
        "formal_warning_output": False,
        "e2_live_evidence_eligible": False,
        "real_activation_ready": False,
        "source_manifest_semantics_verified_by_producer": status == "ready",
        "activation_manifest_created_this_poll": activation_created,
    }
    if source is not None:
        payload.update(
            {
                "maximum_complete_finalized_date": source.watermark.isoformat(),
                "outcome_source_id": source.outcome_source_id,
                "dataset": source.dataset.as_dict(),
                "semantic_manifest": source.semantic_manifest.as_dict(),
                "activation_source_manifest": source.activation_manifest.as_dict(),
            }
        )
    _atomic_write(path, _canonical_bytes(payload))
    return path


def _write_blocked_status_best_effort(
    path: Path,
    profile: dict[str, Any],
    *,
    checked_at: datetime,
    error: Exception,
) -> None:
    """Record a fail-closed ingest result without masking the root failure."""

    try:
        _write_status(
            path,
            profile,
            status="blocked_integrity",
            reason=f"{type(error).__name__}:{error}",
            checked_at=checked_at,
        )
    except Exception:
        # The original validation/I/O error remains the authoritative failure.
        # A status-path failure cannot safely be recovered inside this poll.
        pass


def _validate_feed_extension(feed: _Feed, profile: dict[str, Any]) -> bool:
    baseline = date.fromisoformat(profile["historical_base"]["last_date"])
    post = [record for record in feed.records if record.day > baseline]
    if not post:
        return False
    if len(post) != len(feed.records):
        raise SourceInputError("feed must contain only the post-baseline extension")
    expected = baseline + timedelta(days=1)
    for record in feed.records:
        if record.day != expected:
            raise SourceInputError(
                f"feed extension is not contiguous; expected {expected}, got {record.day}"
            )
        expected += timedelta(days=1)
    return True


def _validate_dataset_payload(
    payload: dict[str, Any], profile: dict[str, Any]
) -> tuple[pd.DataFrame, date]:
    record = _exact_object(
        payload,
        {"schema_version", "case", "date_timezone", "columns", "maximum_complete_finalized_date", "rows"},
        name="canonical source dataset",
    )
    if record["schema_version"] != DATASET_SCHEMA_VERSION or record["case"] != "ootang":
        raise SourceIntegrityError("canonical source dataset scope changed")
    if record["date_timezone"] != profile["source_feed"]["date_timezone"]:
        raise SourceIntegrityError("canonical source date timezone changed")
    columns = [
        "Date", *profile["model"]["displacement_columns"], "RWL", "RWL_rate", "Rain",
        *[f"Rain_cum{window}" for window in profile["source_feed"]["derived_features"]["rain_windows_days"]],
    ]
    if record["columns"] != columns:
        raise SourceIntegrityError("canonical source dataset columns changed")
    rows = record["rows"]
    if not isinstance(rows, list) or not rows:
        raise SourceIntegrityError("canonical source dataset has no rows")
    frame = pd.DataFrame(rows, columns=columns)
    dates = pd.to_datetime(frame["Date"], format="%Y-%m-%d", errors="raise")
    if not dates.diff().iloc[1:].eq(pd.Timedelta(days=1)).all():
        raise SourceIntegrityError("canonical source dataset dates are not daily")
    numeric = frame[columns[1:]].apply(pd.to_numeric, errors="raise").astype(float)
    if not all(math.isfinite(value) for value in numeric.to_numpy().ravel()):
        raise SourceIntegrityError("canonical source dataset has non-finite values")
    frame[columns[1:]] = numeric
    watermark = _canonical_date(
        record["maximum_complete_finalized_date"], name="dataset.watermark"
    )
    if dates.iloc[-1].date() != watermark:
        raise SourceIntegrityError("canonical source dataset watermark mismatch")
    return frame, watermark


def _load_semantic_source(
    profile: dict[str, Any],
    *,
    semantic: ArtifactRef,
    activation: ArtifactRef,
    objects: Path,
    expected_watermark: date,
    expected_source_id: str,
    expected_exported_at: str,
    project_root: Path,
) -> CanonicalSource:
    semantic_payload = _load_json_artifact(
        semantic, name="source semantic manifest"
    )
    semantic_record = _exact_object(
        semantic_payload,
        {
            "schema_version", "case", "outcome_source_id",
            "maximum_complete_finalized_date", "canonical_dataset", "lineage",
            "semantics", "dataset_summary",
        },
        name="source semantic manifest",
    )
    if semantic_record["schema_version"] != SEMANTIC_MANIFEST_SCHEMA_VERSION:
        raise SourceIntegrityError("source semantic manifest schema changed")
    if (
        semantic_record["case"] != "ootang"
        or semantic_record["outcome_source_id"] != expected_source_id
    ):
        raise SourceIntegrityError("source semantic manifest scope changed")
    if (
        semantic_record["maximum_complete_finalized_date"]
        != expected_watermark.isoformat()
    ):
        raise SourceIntegrityError("source semantic manifest watermark mismatch")
    dataset = _content_object_from_payload(
        semantic_record["canonical_dataset"],
        name="semantic.canonical_dataset",
        directory=objects,
        suffix="source.json",
    )
    dataset_raw = _read_verified_artifact(dataset, name="canonical source dataset")
    dataset_payload = _decode_json(dataset_raw, name="canonical source dataset")
    frame, dataset_watermark = _validate_dataset_payload(dataset_payload, profile)
    if dataset_watermark != expected_watermark:
        raise SourceIntegrityError("semantic and dataset watermarks differ")
    lineage = _exact_object(
        semantic_record["lineage"],
        {"historical_base", "daily_feed_snapshot"},
        name="semantic.lineage",
    )
    historical_record = _exact_object(
        lineage["historical_base"],
        {"path", "sha256", "size_bytes", "rows", "last_date", "role"},
        name="semantic.historical_base",
    )
    historical_artifact = _artifact_from_payload(
        {key: historical_record[key] for key in ("path", "sha256", "size_bytes")},
        name="semantic.historical_base.artifact",
    )
    historical_contract = profile["historical_base"]
    if (
        historical_artifact.sha256 != historical_contract["sha256"]
        or historical_record["rows"] != historical_contract["rows"]
        or historical_record["last_date"] != historical_contract["last_date"]
        or historical_record["role"] != historical_contract["role"]
    ):
        raise SourceIntegrityError("semantic historical lineage changed")
    feed_record = _exact_object(
        lineage["daily_feed_snapshot"],
        {
            "path", "sha256", "size_bytes", "schema_version", "exported_at_utc",
            "first_date", "last_date", "rows", "date_revision_pairs_sha256",
        },
        name="semantic.daily_feed_snapshot",
    )
    feed_artifact = _content_object_from_payload(
        {key: feed_record[key] for key in ("path", "sha256", "size_bytes")},
        name="semantic.daily_feed_snapshot.artifact",
        directory=objects,
        suffix="feed.json",
    )
    feed = _parse_feed(
        _read_verified_artifact(feed_artifact, name="daily feed snapshot"),
        profile,
        now=None,
    )
    if not _validate_feed_extension(feed, profile):
        raise SourceIntegrityError("semantic feed has no post-baseline extension")
    revision_digest = hashlib.sha256(
        _canonical_bytes(
            [[record.day.isoformat(), record.revision_id] for record in feed.records]
        )
    ).hexdigest()
    if feed_record != {
        **feed_artifact.as_dict(),
        "schema_version": profile["source_feed"]["schema_version"],
        "exported_at_utc": feed.exported_at_text,
        "first_date": feed.records[0].day.isoformat(),
        "last_date": feed.records[-1].day.isoformat(),
        "rows": len(feed.records),
        "date_revision_pairs_sha256": revision_digest,
    }:
        raise SourceIntegrityError("semantic feed lineage metadata changed")
    if (
        feed.outcome_source_id != expected_source_id
        or feed.exported_at_text != expected_exported_at
        or feed.records[-1].day != expected_watermark
    ):
        raise SourceIntegrityError("source pointer does not match nested feed semantics")
    expected_semantics = {
        "date_timezone": profile["source_feed"]["date_timezone"],
        "recorded_time_timezone": profile["source_feed"]["recorded_time_timezone"],
        "frequency": profile["source_feed"]["expected_frequency"],
        "station_order_live": profile["source_feed"]["station_order_live"],
        "station_order_model": profile["source_feed"]["station_order_model"],
        "model_columns": list(frame.columns),
        "units": profile["source_feed"]["units"],
        "derived_features": profile["source_feed"]["derived_features"],
        "external_derived_columns_accepted": False,
        "all_records_finalized": True,
        "daily_continuity_verified": True,
        "finite_values_verified": True,
    }
    if semantic_record["semantics"] != expected_semantics:
        raise SourceIntegrityError("source semantic declarations changed")
    expected_summary = {
        "rows": len(frame),
        "first_date": str(frame.iloc[0]["Date"]),
        "last_date": str(frame.iloc[-1]["Date"]),
    }
    if semantic_record["dataset_summary"] != expected_summary:
        raise SourceIntegrityError("source dataset summary changed")
    historical, _ = _load_historical(profile, project_root=project_root)
    reproduced = _combine_source(historical, feed, profile)
    expected_dataset = _canonical_bytes(
        _dataset_payload(reproduced, profile, feed.records[-1].day)
    )
    if expected_dataset != dataset_raw:
        raise SourceIntegrityError(
            "canonical dataset does not reproduce from nested lineage"
        )
    _load_activation(activation.path, profile, objects=objects)
    return CanonicalSource(
        frame=frame,
        watermark=expected_watermark,
        outcome_source_id=expected_source_id,
        exported_at_utc=expected_exported_at,
        records=feed.records,
        dataset=dataset,
        semantic_manifest=semantic,
        activation_manifest=activation,
    )


def load_current_source(
    profile: dict[str, Any],
    *,
    runtime_root: Path | None = None,
    project_root: Path = ROOT,
) -> CanonicalSource:
    """Load and recursively verify the current content-addressed source."""

    root = _runtime_root(profile, runtime_root, project_root)
    _validate_runtime_paths(profile, root=root)
    pointer_path = _runtime_path(
        profile, "current_source_pointer", root=root
    )
    objects = _runtime_path(profile, "objects", root=root)
    activation_path = _runtime_path(
        profile, "activation_source_manifest", root=root
    )
    pointer, _, _ = _load_json_snapshot(pointer_path, name="current source pointer")
    record = _exact_object(
        pointer,
        {
            "schema_version", "case", "profile_id", "updated_at_utc",
            "maximum_complete_finalized_date", "outcome_source_id", "dataset",
            "semantic_manifest", "activation_source_manifest",
        },
        name="current source pointer",
    )
    if record["schema_version"] != CURRENT_POINTER_SCHEMA_VERSION:
        raise SourceIntegrityError("current source pointer schema changed")
    if record["case"] != "ootang" or record["profile_id"] != profile["profile_id"]:
        raise SourceIntegrityError("current source pointer scope changed")
    exported_at = _nonempty_string(record["updated_at_utc"], name="pointer.updated_at_utc")
    _utc(exported_at, name="pointer.updated_at_utc")
    source_id = _nonempty_string(record["outcome_source_id"], name="pointer.outcome_source_id")
    pointer_watermark = _canonical_date(
        record["maximum_complete_finalized_date"], name="pointer.watermark"
    )
    dataset = _content_object_from_payload(
        record["dataset"],
        name="pointer.dataset",
        directory=objects,
        suffix="source.json",
    )
    semantic = _content_object_from_payload(
        record["semantic_manifest"],
        name="pointer.semantic_manifest",
        directory=objects,
        suffix="source-manifest.json",
    )
    immutable_activation = _load_activation(
        activation_path, profile, objects=objects
    )
    activation_record = _exact_object(
        record["activation_source_manifest"],
        {"path", "sha256", "size_bytes"},
        name="pointer.activation_source_manifest",
    )
    if activation_record != immutable_activation.as_dict():
        raise SourceIntegrityError(
            "current source pointer activation does not match immutable activation"
        )
    loaded = _load_semantic_source(
        profile,
        semantic=semantic,
        activation=immutable_activation,
        objects=objects,
        expected_watermark=pointer_watermark,
        expected_source_id=source_id,
        expected_exported_at=exported_at,
        project_root=project_root,
    )
    if loaded.dataset != dataset:
        raise SourceIntegrityError("semantic manifest dataset reference mismatch")
    activation_source = load_activation_source(
        profile,
        runtime_root=root,
        project_root=project_root,
    )
    if activation_source.outcome_source_id != loaded.outcome_source_id:
        raise SourceIntegrityError("current and activation source ids differ")
    if activation_source.watermark > loaded.watermark:
        raise SourceIntegrityError("current source predates immutable activation")
    return loaded


def load_activation_source(
    profile: dict[str, Any],
    *,
    runtime_root: Path | None = None,
    project_root: Path = ROOT,
) -> CanonicalSource:
    """Recover the immutable source bytes that fixed the live epoch cutoff."""

    root = _runtime_root(profile, runtime_root, project_root)
    _validate_runtime_paths(profile, root=root)
    path = _runtime_path(profile, "activation_source_manifest", root=root)
    objects = _runtime_path(profile, "objects", root=root)
    activation = _load_activation(path, profile, objects=objects)
    payload, _, _ = _load_json_snapshot(path, name="activation source manifest")
    semantic = _content_object_from_payload(
        payload["data_manifest"],
        name="activation.data_manifest",
        directory=objects,
        suffix="source-manifest.json",
    )
    watermark = _canonical_date(
        payload["maximum_complete_finalized_date"], name="activation.watermark"
    )
    source_id = _nonempty_string(
        payload["outcome_source_id"], name="activation.outcome_source_id"
    )
    exported = _nonempty_string(
        payload["captured_at_utc"], name="activation.captured_at_utc"
    )
    result = _load_semantic_source(
        profile,
        semantic=semantic,
        activation=activation,
        objects=objects,
        expected_watermark=watermark,
        expected_source_id=source_id,
        expected_exported_at=exported,
        project_root=project_root,
    )
    latest = result.records[-1]
    if payload["latest_finalized_displacement_mm"] != latest.displacement_mm:
        raise SourceIntegrityError("activation latest displacement does not match lineage")
    return result


def _ingest_source_locked(
    profile: dict[str, Any],
    *,
    runtime_root: Path | None = None,
    project_root: Path = ROOT,
    now: datetime | None = None,
) -> SourceIngestResult:
    """Ingest one complete machine feed snapshot, or wait/fail closed."""

    root = _runtime_root(profile, runtime_root, project_root)
    _validate_runtime_paths(profile, root=root)
    status_path = _runtime_path(profile, "source_status", root=root)
    feed_path = _runtime_path(profile, "incoming_feed", root=root)
    checked_at = now or datetime.now(timezone.utc)
    try:
        if checked_at.tzinfo is None:
            raise SourceInputError("injected machine time must be timezone-aware")
        if not feed_path.is_file():
            _write_status(
                status_path, profile, status="waiting_for_daily_finalized_feed",
                reason="configured_daily_feed_is_missing", checked_at=checked_at,
            )
            return SourceIngestResult(status_path, "waiting_for_daily_finalized_feed", None)
        with feed_path.open("rb") as handle:
            feed_raw = handle.read()
        feed = _parse_feed(feed_raw, profile, now=checked_at)
        if not _validate_feed_extension(feed, profile):
            _write_status(
                status_path, profile, status="waiting_for_post_baseline_row",
                reason="feed_has_no_row_after_frozen_historical_watermark",
                checked_at=checked_at,
            )
            return SourceIngestResult(status_path, "waiting_for_post_baseline_row", None)

        pointer_path = _runtime_path(
            profile, "current_source_pointer", root=root
        )
        current: CanonicalSource | None = None
        if pointer_path.exists():
            current = load_current_source(
                profile, runtime_root=root, project_root=project_root
            )
            if feed.records[-1].day < current.watermark:
                raise SourceIntegrityError("incoming feed would roll back source_current")
            if feed.outcome_source_id != current.outcome_source_id:
                raise SourceIntegrityError("incoming feed outcome source id changed")
            if (
                feed.outcome_source_id == current.outcome_source_id
                and feed.records == current.records
            ):
                _write_status(
                    status_path,
                    profile,
                    status="ready",
                    reason="semantically_identical_feed_preserved_existing_pointer",
                    checked_at=checked_at,
                    source=current,
                )
                return SourceIngestResult(status_path, "ready", current)
            if feed.exported_at <= _utc(
                current.exported_at_utc, name="current.exported_at_utc"
            ):
                raise SourceIntegrityError(
                    "changed source semantics require a later export timestamp"
                )
            for old, new in zip(current.records, feed.records):
                if old.revision_id == new.revision_id and old != new:
                    raise SourceIntegrityError(
                        f"source revision id reused for changed date {old.day}"
                    )

        activation_path = _runtime_path(
            profile, "activation_source_manifest", root=root
        )
        objects = _runtime_path(profile, "objects", root=root)
        existing_activation: ArtifactRef | None = None
        if activation_path.exists():
            existing_activation = _load_activation(
                activation_path, profile, objects=objects
            )
            activation_source = load_activation_source(
                profile, runtime_root=root, project_root=project_root
            )
            if feed.outcome_source_id != activation_source.outcome_source_id:
                raise SourceIntegrityError("incoming feed differs from activation source id")

        historical, historical_artifact = _load_historical(
            profile, project_root=project_root
        )
        frame = _combine_source(historical, feed, profile)
        feed_artifact = _write_content_object(objects, feed.raw, suffix="feed.json")
        dataset_raw = _canonical_bytes(
            _dataset_payload(frame, profile, feed.records[-1].day)
        )
        dataset_artifact = _write_content_object(
            objects, dataset_raw, suffix="source.json"
        )
        semantic_raw = _canonical_bytes(
            _semantic_payload(
                profile,
                frame=frame,
                feed=feed,
                feed_artifact=feed_artifact,
                historical_artifact=historical_artifact,
                dataset_artifact=dataset_artifact,
            )
        )
        semantic_artifact = _write_content_object(
            objects, semantic_raw, suffix="source-manifest.json"
        )
        activation_created = False
        if existing_activation is None:
            _atomic_write_once(
                activation_path,
                _canonical_bytes(_activation_payload(profile, feed, semantic_artifact)),
            )
            existing_activation = _load_activation(
                activation_path, profile, objects=objects
            )
            activation_created = True

        pointer_payload = {
            "schema_version": CURRENT_POINTER_SCHEMA_VERSION,
            "case": "ootang",
            "profile_id": profile["profile_id"],
            "updated_at_utc": feed.exported_at_text,
            "maximum_complete_finalized_date": feed.records[-1].day.isoformat(),
            "outcome_source_id": feed.outcome_source_id,
            "dataset": dataset_artifact.as_dict(),
            "semantic_manifest": semantic_artifact.as_dict(),
            "activation_source_manifest": existing_activation.as_dict(),
        }
        _atomic_write(pointer_path, _canonical_bytes(pointer_payload))
        source = load_current_source(
            profile, runtime_root=root, project_root=project_root
        )
        _write_status(
            status_path, profile, status="ready",
            reason="source_extension_validated_and_materialized",
            checked_at=checked_at, source=source,
            activation_created=activation_created,
        )
        return SourceIngestResult(status_path, "ready", source)
    except SourceError as exc:
        _write_blocked_status_best_effort(
            status_path, profile, checked_at=checked_at, error=exc
        )
        raise
    except Exception as exc:
        normalized = SourceIntegrityError(
            "source validation/materialization failed: "
            f"{type(exc).__name__}:{exc}"
        )
        _write_blocked_status_best_effort(
            status_path, profile, checked_at=checked_at, error=normalized
        )
        raise normalized from exc


def ingest_source(
    profile: dict[str, Any],
    *,
    runtime_root: Path | None = None,
    project_root: Path = ROOT,
    now: datetime | None = None,
) -> SourceIngestResult:
    """Acquire the deploy-cycle lock and ingest one source snapshot."""

    root = _runtime_root(profile, runtime_root, project_root)
    _validate_runtime_paths(profile, root=root)
    lock_path = _runtime_path(profile, "deploy_lock", root=root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_handle = lock_path.open("a+b")
    try:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SourceBusyError("another machine deploy cycle owns the lock") from exc
        return _ingest_source_locked(
            profile,
            runtime_root=root,
            project_root=project_root,
            now=now,
        )
    finally:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        finally:
            lock_handle.close()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_DEPLOY_CONFIG_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        profile = load_deploy_profile(args.config)
        result = ingest_source(profile)
    except SourceBusyError as exc:
        print(f"[ootang-live-source] busy: {exc}", file=sys.stderr)
        return 3
    except SourceError as exc:
        print(f"[ootang-live-source] blocked: {exc}", file=sys.stderr)
        return 2
    print(f"[ootang-live-source] status={result.status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ArtifactRef",
    "CanonicalSource",
    "DailySourceRecord",
    "DEFAULT_DEPLOY_CONFIG_PATH",
    "SourceConfigError",
    "SourceBusyError",
    "SourceError",
    "SourceIngestResult",
    "SourceInputError",
    "SourceIntegrityError",
    "derive_model_frame",
    "ingest_source",
    "load_current_source",
    "load_activation_source",
    "load_deploy_profile",
    "main",
]
