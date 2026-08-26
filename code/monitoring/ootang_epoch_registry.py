"""Prebuild immutable Ootang epoch candidates in a standalone R1 registry.

R1 never selects an active epoch, opens a live ledger, reads an outcome, or
authorizes E2 evidence.  It builds a candidate directly in its final stable
slot, captures a content-addressed archival byte capsule, and appends a fully
replayable ``candidate_ready`` event.
"""

from __future__ import annotations

import argparse
import base64
import binascii
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

DEFAULT_CONFIG_PATH = ROOT / "config" / "ootang_epoch_registry.v1.json"
DEFAULT_CONFIG_SHA256 = (
    "c56004649689ee8aa4beeca6a7c61bbdd5529ec62706880ea8eac65a8ca18edd"
)
IMPLEMENTATION_LOGICAL_PATH = "code/monitoring/ootang_epoch_registry.py"
ZERO_HASH = "0" * 64
MAX_CONTROL_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_BYTES = 128 * 1024 * 1024
MAX_FEED_BYTES = 16 * 1024 * 1024
MAX_FEED_OBSERVATION_BYTES = 24 * 1024 * 1024
HEX_DIGITS = frozenset("0123456789abcdef")
UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
OOTANG_LIVE_STATIONS = (
    "ATU1",
    "ATU2",
    "ATU3",
    "ATU4",
    "ATU5",
    "MJ1",
    "MJ3",
    "MJ9",
)

UPSTREAM_KEYS = {
    "deploy",
    "live",
    "cycle_v3",
    "issue_replay",
    "verified_live",
    "calibration_shadow",
    "trusted_time",
}
ROOT_ENVIRONMENT_KEYS = {"pyproject", "uv_lock"}
TRUSTED_TIME_DEPENDENCY_KEYS = {
    "trust_manifest",
    "leaf_certificate",
    "root_certificate",
    "runtime_project",
    "runtime_lock",
}
EXPECTED_RUNTIME = {
    "root": "runtime/ootang_epoch_registry_v1",
    "source_feed": (
        "runtime/ootang_prequential_live_v1/incoming/daily_finalized_feed.json"
    ),
    "manager_lock": "manager.lock",
    "status": "registry_status.json",
    "head": "registry_head.json",
    "feed_observations": "feed_observations",
    "feed_observation_head": "feed_observation_head.json",
    "events": "events",
    "objects": "objects/sha256",
    "capsules": "capsules",
    "candidate_receipts": "candidate_receipts/sha256",
    "slots": "slots",
}
EXPECTED_PROTOCOL = {
    "event_schema_version": "ootang_epoch_registry_event_v1",
    "feed_observation_schema_version": "ootang_epoch_feed_observation_v1",
    "feed_observation_head_schema_version": "ootang_epoch_feed_observation_head_v1",
    "candidate_receipt_schema_version": "ootang_epoch_candidate_receipt_v1",
    "capsule_manifest_schema_version": "ootang_epoch_capsule_manifest_v1",
    "status_schema_version": "ootang_epoch_registry_status_v1",
    "head_schema_version": "ootang_epoch_registry_head_v1",
    "initial_previous_entry_sha256": ZERO_HASH,
    "canonical_json": "utf8_sort_keys_compact_no_nan_trailing_lf",
    "slot_identity_domain": ("org.ootang.landslide-warning/epoch-candidate-slot/v1"),
    "live_epoch_identity_contract": "ootang_prequential_live_v1_exact_formula",
    "same_slot_same_semantics": "idempotent_preserve_first_bytes",
    "same_slot_changed_semantics": "blocked_integrity",
    "active_switch": "not_implemented_r1_candidate_ready_only",
}
EXPECTED_CAPABILITIES = {
    "immutable_candidate_registry_implemented": True,
    "candidate_prebuild_implemented": True,
    "active_epoch_switch_implemented": False,
    "automatic_epoch_rotation_implemented": False,
    "trusted_anchor_receipt_verified": False,
    "automatic_calibration_promotion_implemented": False,
    "e2_live_evidence_eligible": False,
    "real_activation_ready": False,
    "formal_warning_output": False,
}
CLAIM_KEYS = {
    "active_epoch_switch_implemented",
    "automatic_epoch_rotation_implemented",
    "trusted_anchor_receipt_verified",
    "e2_live_evidence_eligible",
    "real_activation_ready",
    "formal_warning_output",
}
ARTIFACT_KEYS = {"path", "sha256", "size_bytes"}
FEED_OBSERVATION_PAYLOAD_KEYS = {"sha256", "size_bytes", "raw_base64"}
CAPSULE_ARTIFACT_KEYS = {
    "logical_path",
    "sha256",
    "size_bytes",
    "object_path",
}
CANDIDATE_SNAPSHOT_KEYS = {
    "logical_id",
    "original_path",
    "sha256",
    "size_bytes",
    "object_path",
}
BUILD_KEYS = {
    "live_epoch_id",
    "watermark",
    "outcome_source_id",
    "source_artifacts",
    "model_manifest",
    "training_manifest",
    "checkpoints",
    "algorithm_profile_sha256",
    "implementation_sha256",
    "environment_sha256",
}


class EpochRegistryError(RuntimeError):
    """Base R1 registry error."""


class EpochRegistryConfigError(EpochRegistryError):
    """The reviewed R1 profile or a bound artifact changed."""


class EpochRegistryIntegrityError(EpochRegistryError):
    """A persisted registry, capsule, slot, or candidate failed closed."""


class EpochRegistryBusyError(EpochRegistryError):
    """Another machine process owns the R1 manager or an upstream lock."""


@dataclass(frozen=True)
class ArtifactSnapshot:
    path: Path
    raw: bytes
    sha256: str
    size_bytes: int

    def record(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class RegistryPaths:
    root: Path
    source_feed: Path
    manager_lock: Path
    status: Path
    head: Path
    feed_observations: Path
    feed_observation_head: Path
    events: Path
    objects: Path
    capsules: Path
    candidate_receipts: Path
    slots: Path


@dataclass(frozen=True)
class CandidateBuild:
    """Verified candidate evidence returned by a prebuilder."""

    live_epoch_id: str
    watermark: str
    outcome_source_id: str
    source_artifacts: tuple[dict[str, object], ...]
    model_manifest: dict[str, object]
    training_manifest: dict[str, object]
    checkpoints: tuple[dict[str, object], ...]
    algorithm_profile_sha256: str
    implementation_sha256: str
    environment_sha256: str

    def payload(self) -> dict[str, object]:
        return {
            "live_epoch_id": self.live_epoch_id,
            "watermark": self.watermark,
            "outcome_source_id": self.outcome_source_id,
            "source_artifacts": list(self.source_artifacts),
            "model_manifest": self.model_manifest,
            "training_manifest": self.training_manifest,
            "checkpoints": list(self.checkpoints),
            "algorithm_profile_sha256": self.algorithm_profile_sha256,
            "implementation_sha256": self.implementation_sha256,
            "environment_sha256": self.environment_sha256,
        }


@dataclass(frozen=True)
class FeedRecordSemantics:
    day: date
    revision_id: str
    record_sha256: str


@dataclass(frozen=True)
class FeedSemantics:
    sha256: str
    outcome_source_id: str
    exported_at: datetime
    records: tuple[FeedRecordSemantics, ...]

    @property
    def watermark(self) -> date:
        return self.records[-1].day


Prebuilder = Callable[
    [Mapping[str, Any], RegistryPaths, Path, datetime, Path], CandidateBuild | None
]


def _reject_constant(value: str) -> None:
    raise EpochRegistryIntegrityError(f"Forbidden JSON constant: {value}")


def _pairs_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise EpochRegistryIntegrityError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _decode_json(raw: bytes, *, name: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs_object,
            parse_constant=_reject_constant,
        )
    except EpochRegistryIntegrityError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EpochRegistryIntegrityError(f"{name} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise EpochRegistryIntegrityError(f"{name} must be a JSON object")
    return value


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise EpochRegistryIntegrityError("Value is not canonical finite JSON") from exc


def _canonical_bytes(value: object) -> bytes:
    return _canonical_json_bytes(value) + b"\n"


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _live_canonical_sha256(value: object) -> str:
    """Match ``ootang_prequential_live._canonical_sha256`` exactly."""

    return _sha256(_canonical_json_bytes(value))


def _utc_timestamp(value: object, *, name: str) -> datetime:
    text = _nonempty_text(value, name=name)
    if UTC_TIMESTAMP_RE.fullmatch(text) is None:
        raise EpochRegistryIntegrityError(f"{name} must be canonical RFC 3339 UTC")
    try:
        result = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise EpochRegistryIntegrityError(f"{name} is invalid") from exc
    if result.utcoffset() != timedelta(0):
        raise EpochRegistryIntegrityError(f"{name} must be UTC")
    return result


def _finite_number(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EpochRegistryIntegrityError(f"{name} must be numeric")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise EpochRegistryIntegrityError(f"{name} must be finite") from exc
    if not math.isfinite(result):
        raise EpochRegistryIntegrityError(f"{name} must be finite")
    return result


def _feed_semantics(
    snapshot: ArtifactSnapshot, *, now: datetime | None = None
) -> FeedSemantics:
    payload = _decode_json(snapshot.raw, name="candidate finalized feed")
    feed = _exact_object(
        payload,
        {"schema_version", "outcome_source_id", "exported_at_utc", "records"},
        name="candidate finalized feed",
    )
    if feed["schema_version"] != "ootang_daily_finalized_feed_v1":
        raise EpochRegistryIntegrityError("Candidate feed schema changed")
    source_id = _nonempty_text(
        feed["outcome_source_id"], name="candidate feed outcome source"
    )
    exported_at = _utc_timestamp(
        feed["exported_at_utc"], name="candidate feed export time"
    )
    if now is not None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise EpochRegistryIntegrityError(
                "Candidate feed validation clock must be timezone-aware"
            )
        if exported_at > now.astimezone(timezone.utc):
            raise EpochRegistryIntegrityError("Candidate feed is future-dated")
    raw_records = feed["records"]
    if not isinstance(raw_records, list) or not raw_records:
        raise EpochRegistryIntegrityError("Candidate feed records must be non-empty")
    records: list[FeedRecordSemantics] = []
    prior_day: date | None = None
    prior_observed: datetime | None = None
    prior_available: datetime | None = None
    prior_finalized: datetime | None = None
    expected_day = date(2020, 7, 1)
    zone = ZoneInfo("Asia/Shanghai")
    record_keys = {
        "schema_version",
        "date",
        "revision_id",
        "observed_at_utc",
        "available_at_utc",
        "finalized_at_utc",
        "finalized",
        "rainfall_mm",
        "reservoir_water_level_m",
        "displacement_mm",
    }
    for index, record in enumerate(raw_records):
        record = _exact_object(
            record, record_keys, name=f"candidate feed record {index}"
        )
        if record["schema_version"] != "ootang_daily_finalized_record_v1":
            raise EpochRegistryIntegrityError(
                f"Candidate feed record {index} schema changed"
            )
        day_text = _nonempty_text(
            record["date"], name=f"candidate feed record {index} date"
        )
        try:
            day = date.fromisoformat(day_text)
        except ValueError as exc:
            raise EpochRegistryIntegrityError(
                f"Candidate feed record {index} date is invalid"
            ) from exc
        if day.isoformat() != day_text:
            raise EpochRegistryIntegrityError(
                f"Candidate feed record {index} date is not canonical"
            )
        if prior_day is not None and day <= prior_day:
            raise EpochRegistryIntegrityError(
                "Candidate feed dates are not strictly increasing"
            )
        if day != expected_day:
            raise EpochRegistryIntegrityError(
                f"Candidate feed extension is not contiguous at {expected_day}"
            )
        revision_id = _nonempty_text(
            record["revision_id"],
            name=f"candidate feed record {index} revision id",
        )
        if record["finalized"] is not True:
            raise EpochRegistryIntegrityError(
                f"Candidate feed record {index} is not finalized"
            )
        observed = _utc_timestamp(
            record["observed_at_utc"],
            name=f"candidate feed record {index} observed time",
        )
        available = _utc_timestamp(
            record["available_at_utc"],
            name=f"candidate feed record {index} available time",
        )
        finalized = _utc_timestamp(
            record["finalized_at_utc"],
            name=f"candidate feed record {index} finalized time",
        )
        if not observed <= available <= finalized <= exported_at:
            raise EpochRegistryIntegrityError(
                f"Candidate feed record {index} timestamps are not monotone"
            )
        day_start = datetime.combine(day, time.min, tzinfo=zone).astimezone(
            timezone.utc
        )
        next_boundary = datetime.combine(
            day + timedelta(days=1), time.min, tzinfo=zone
        ).astimezone(timezone.utc)
        if observed < day_start or finalized >= next_boundary:
            raise EpochRegistryIntegrityError(
                f"Candidate feed record {index} violates its natural-day boundary"
            )
        if prior_observed is not None and observed <= prior_observed:
            raise EpochRegistryIntegrityError(
                "Candidate feed observed times are not strictly increasing"
            )
        if prior_available is not None and available <= prior_available:
            raise EpochRegistryIntegrityError(
                "Candidate feed available times are not strictly increasing"
            )
        if prior_finalized is not None and finalized <= prior_finalized:
            raise EpochRegistryIntegrityError(
                "Candidate feed finalized times are not strictly increasing"
            )
        rainfall = _finite_number(
            record["rainfall_mm"],
            name=f"candidate feed record {index} rainfall",
        )
        if rainfall < 0:
            raise EpochRegistryIntegrityError(
                f"Candidate feed record {index} rainfall is negative"
            )
        _finite_number(
            record["reservoir_water_level_m"],
            name=f"candidate feed record {index} reservoir level",
        )
        displacement = _exact_object(
            record["displacement_mm"],
            set(OOTANG_LIVE_STATIONS),
            name=f"candidate feed record {index} displacement",
        )
        if list(displacement) != list(OOTANG_LIVE_STATIONS):
            raise EpochRegistryIntegrityError(
                f"Candidate feed record {index} station order changed"
            )
        for station in OOTANG_LIVE_STATIONS:
            _finite_number(
                displacement[station],
                name=f"candidate feed record {index} displacement {station}",
            )
        records.append(
            FeedRecordSemantics(
                day=day,
                revision_id=revision_id,
                record_sha256=_sha256(_canonical_bytes(record)),
            )
        )
        prior_day = day
        prior_observed = observed
        prior_available = available
        prior_finalized = finalized
        expected_day += timedelta(days=1)
    return FeedSemantics(
        sha256=snapshot.sha256,
        outcome_source_id=source_id,
        exported_at=exported_at,
        records=tuple(records),
    )


def _assert_feed_successor(
    previous: FeedSemantics,
    current: FeedSemantics,
    *,
    seen_revision_ids: Mapping[date, set[str]],
    seen_record_hashes: Mapping[date, set[str]],
) -> None:
    if current.sha256 == previous.sha256:
        return
    if current.outcome_source_id != previous.outcome_source_id:
        raise EpochRegistryIntegrityError("Candidate outcome source identity changed")
    if current.exported_at <= previous.exported_at:
        raise EpochRegistryIntegrityError("Candidate feed export time rolled back")
    if len(current.records) < len(previous.records):
        raise EpochRegistryIntegrityError("Candidate feed history shrank")
    for index, prior in enumerate(previous.records):
        candidate = current.records[index]
        if candidate.day != prior.day:
            raise EpochRegistryIntegrityError(
                "Candidate feed no longer preserves its dated prefix"
            )
        if candidate.record_sha256 == prior.record_sha256:
            continue
        if candidate.revision_id in seen_revision_ids.get(
            candidate.day, set()
        ) or candidate.record_sha256 in seen_record_hashes.get(candidate.day, set()):
            raise EpochRegistryIntegrityError(
                "Candidate feed revision would roll back known history"
            )


def _extend_feed_history(
    semantics: FeedSemantics,
    revision_ids: dict[date, set[str]],
    record_hashes: dict[date, set[str]],
) -> None:
    for record in semantics.records:
        revision_ids.setdefault(record.day, set()).add(record.revision_id)
        record_hashes.setdefault(record.day, set()).add(record.record_sha256)


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str) and len(value) == 64 and set(value).issubset(HEX_DIGITS)
    )


def _require_hash(value: object, *, name: str) -> str:
    if not _is_sha256(value):
        raise EpochRegistryIntegrityError(f"{name} is not a lowercase SHA-256")
    return str(value)


def _exact_object(value: object, expected: set[str], *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise EpochRegistryIntegrityError(f"{name} keys changed")
    return value


def _nonempty_text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise EpochRegistryIntegrityError(f"{name} must be non-empty canonical text")
    return value


def _relative_path(value: object, *, name: str) -> PurePosixPath:
    text = _nonempty_text(value, name=name)
    candidate = PurePosixPath(text)
    if candidate.is_absolute() or ".." in candidate.parts or "." in candidate.parts:
        raise EpochRegistryIntegrityError(f"{name} must be a contained relative path")
    return candidate


def _absolute_lexical(path: Path) -> Path:
    """Return an absolute normalized path without following symbolic links."""

    return Path(os.path.abspath(os.fspath(path)))


def _contained(root: Path, relative: object, *, name: str) -> Path:
    root = _absolute_lexical(root)
    child = _absolute_lexical(root.joinpath(*_relative_path(relative, name=name).parts))
    try:
        child.relative_to(root)
    except ValueError as exc:
        raise EpochRegistryIntegrityError(f"{name} escapes its root") from exc
    _ensure_existing_parents_not_symlinks(root, child)
    return child


def _ensure_existing_parents_not_symlinks(root: Path, path: Path) -> None:
    root = _absolute_lexical(root)
    path = _absolute_lexical(path)
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise EpochRegistryIntegrityError(f"Path escapes root: {path}") from exc
    components = (
        root,
        *(
            root.joinpath(*relative.parts[:index])
            for index in range(1, len(relative.parts))
        ),
    )
    for current in components:
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise EpochRegistryIntegrityError(
                f"Cannot inspect path component: {current}"
            ) from exc
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise EpochRegistryIntegrityError(
                f"Path parent is not a real directory: {current}"
            )


def _read_regular(
    path: Path, *, name: str, maximum_bytes: int = MAX_ARTIFACT_BYTES
) -> ArtifactSnapshot:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise EpochRegistryIntegrityError(f"Cannot safely open {name}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum_bytes:
            raise EpochRegistryIntegrityError(f"{name} is not an allowed regular file")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, min(65536, maximum_bytes + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > maximum_bytes:
                raise EpochRegistryIntegrityError(f"{name} exceeds its size limit")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        pathname = os.lstat(path)
    except OSError as exc:
        raise EpochRegistryIntegrityError(f"{name} path changed while reading") from exc
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
    if (
        identity_before != identity_after
        or not stat.S_ISREG(pathname.st_mode)
        or (pathname.st_dev, pathname.st_ino) != (after.st_dev, after.st_ino)
    ):
        raise EpochRegistryIntegrityError(f"{name} changed while reading")
    raw = b"".join(chunks)
    return ArtifactSnapshot(_absolute_lexical(path), raw, _sha256(raw), len(raw))


def _mkdir(path: Path, *, root: Path) -> None:
    _ensure_existing_parents_not_symlinks(root, path)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise EpochRegistryIntegrityError(f"Cannot create directory {path}") from exc
    try:
        mode = os.lstat(path).st_mode
    except OSError as exc:
        raise EpochRegistryIntegrityError(f"Cannot inspect directory {path}") from exc
    if not stat.S_ISDIR(mode) or stat.S_ISLNK(mode):
        raise EpochRegistryIntegrityError(f"Path is not a real directory: {path}")


def _temporary_directory(root: Path) -> Path:
    temporary = _contained(root, ".tmp", name="temporary directory")
    _mkdir(temporary, root=root)
    return temporary


def _cleanup_temporary_namespace(root: Path) -> None:
    temporary = _contained(root, ".tmp", name="temporary directory")
    if not temporary.exists() and not temporary.is_symlink():
        return
    try:
        mode = os.lstat(temporary).st_mode
    except OSError as exc:
        raise EpochRegistryIntegrityError("Cannot inspect temporary directory") from exc
    if not stat.S_ISDIR(mode) or stat.S_ISLNK(mode):
        raise EpochRegistryIntegrityError("Temporary namespace is not a real directory")
    removed = False
    for path in sorted(temporary.iterdir(), key=lambda item: item.name):
        name = path.name
        try:
            entry_mode = os.lstat(path).st_mode
        except OSError as exc:
            raise EpochRegistryIntegrityError("Cannot inspect crash temporary") from exc
        if (
            not stat.S_ISREG(entry_mode)
            or stat.S_ISLNK(entry_mode)
            or not name.startswith(".")
            or name.count(".") < 3
            or not name.endswith((".create", ".cache"))
        ):
            raise EpochRegistryIntegrityError(
                "Temporary namespace contains an unknown entry"
            )
        try:
            path.unlink()
        except OSError as exc:
            raise EpochRegistryIntegrityError("Cannot remove crash temporary") from exc
        removed = True
    if removed:
        _fsync_directory(temporary, name="temporary cleanup")


def _fsync_directory(path: Path, *, name: str) -> None:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_DIRECTORY", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise EpochRegistryIntegrityError(f"Cannot open {name} directory") from exc
    try:
        opened = os.fstat(descriptor)
        pathname = os.lstat(path)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not stat.S_ISDIR(pathname.st_mode)
            or stat.S_ISLNK(pathname.st_mode)
            or (opened.st_dev, opened.st_ino) != (pathname.st_dev, pathname.st_ino)
        ):
            raise EpochRegistryIntegrityError(f"{name} directory changed")
        os.fsync(descriptor)
    except OSError as exc:
        raise EpochRegistryIntegrityError(f"Cannot fsync {name} directory") from exc
    finally:
        os.close(descriptor)


def _publish_once(path: Path, raw: bytes, *, root: Path, name: str) -> ArtifactSnapshot:
    _mkdir(path.parent, root=root)
    if path.exists() or path.is_symlink():
        existing = _read_regular(path, name=name)
        if existing.raw != raw:
            raise EpochRegistryIntegrityError(f"{name} already has different bytes")
        return existing
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".create",
        dir=_temporary_directory(root),
    )
    temporary = Path(temporary_name)
    linked = False
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
            linked = True
        except FileExistsError:
            pass
        except OSError as exc:
            raise EpochRegistryIntegrityError(f"Cannot publish {name}") from exc
        if linked:
            _fsync_directory(path.parent, name=f"{name} publication")
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    existing = _read_regular(path, name=name)
    if existing.raw != raw:
        raise EpochRegistryIntegrityError(f"{name} publication conflicted")
    return existing


def _atomic_cache(path: Path, raw: bytes, *, root: Path, name: str) -> None:
    _mkdir(path.parent, root=root)
    if path.is_symlink():
        raise EpochRegistryIntegrityError(f"{name} cannot be a symlink")
    if path.exists():
        existing_mode = os.lstat(path).st_mode
        if not stat.S_ISREG(existing_mode):
            raise EpochRegistryIntegrityError(f"{name} must be a regular file")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".cache",
        dir=_temporary_directory(root),
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent, name=f"{name} refresh")
    except OSError as exc:
        raise EpochRegistryIntegrityError(f"Cannot refresh {name}") from exc
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _binding(value: object, *, name: str) -> dict[str, str]:
    record = _exact_object(value, {"path", "expected_sha256"}, name=name)
    path = str(_relative_path(record["path"], name=f"{name}.path"))
    expected = _require_hash(record["expected_sha256"], name=f"{name}.sha256")
    return {"path": path, "expected_sha256": expected}


def load_registry_profile(
    path: Path = DEFAULT_CONFIG_PATH, *, project_root: Path = ROOT
) -> dict[str, Any]:
    project_root = project_root.resolve()
    path = path if path.is_absolute() else project_root / path
    path = _absolute_lexical(path)
    try:
        path.relative_to(project_root)
        _ensure_existing_parents_not_symlinks(project_root, path)
    except (EpochRegistryIntegrityError, ValueError) as exc:
        raise EpochRegistryConfigError(
            "Registry profile path escapes project root"
        ) from exc
    if project_root != ROOT.resolve() or path != _absolute_lexical(DEFAULT_CONFIG_PATH):
        raise EpochRegistryConfigError(
            "Only the reviewed default epoch registry profile is accepted"
        )
    try:
        snapshot = _read_regular(
            path, name="epoch registry profile", maximum_bytes=MAX_CONTROL_BYTES
        )
        if snapshot.sha256 != DEFAULT_CONFIG_SHA256:
            raise EpochRegistryIntegrityError(
                "Reviewed epoch registry profile digest changed"
            )
        profile = _decode_json(snapshot.raw, name="epoch registry profile")
        _exact_object(
            profile,
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
                "upstream_profiles",
                "root_environment",
                "trusted_time_dependencies",
                "capsule_files",
                "runtime",
                "protocol",
                "candidate",
                "engineering_capabilities",
            },
            name="epoch registry profile",
        )
        identity = (
            profile["schema_version"],
            profile["profile_id"],
            profile["profile_version"],
            profile["case"],
            profile["artifact_status"],
        )
        if identity != (
            "ootang_epoch_registry_profile_v1",
            "ootang-epoch-registry-v1",
            "1.0.0-engineering",
            "ootang",
            "immutable_epoch_candidate_registry_engineering_only_not_live_evidence",
        ):
            raise EpochRegistryIntegrityError("Registry profile identity changed")
        for key in (
            "formal_warning_output",
            "independent_label_used",
            "confirmatory_external_validation",
            "vajont_used",
            "default_pipeline_member",
        ):
            if profile[key] is not False:
                raise EpochRegistryIntegrityError(
                    f"Registry flag {key} must remain false"
                )
        upstream = _exact_object(
            profile["upstream_profiles"], UPSTREAM_KEYS, name="upstream_profiles"
        )
        for key in sorted(UPSTREAM_KEYS):
            upstream[key] = _binding(upstream[key], name=f"upstream_profiles.{key}")
        implementation_path = _contained(
            project_root,
            IMPLEMENTATION_LOGICAL_PATH,
            name="epoch registry implementation",
        )
        implementation = _read_regular(
            implementation_path, name="epoch registry implementation"
        )
        profile["implementation"] = {
            "path": IMPLEMENTATION_LOGICAL_PATH,
            "expected_sha256": implementation.sha256,
        }
        root_environment = _exact_object(
            profile["root_environment"],
            ROOT_ENVIRONMENT_KEYS,
            name="root_environment",
        )
        for key in sorted(ROOT_ENVIRONMENT_KEYS):
            root_environment[key] = _binding(
                root_environment[key], name=f"root_environment.{key}"
            )
        dependencies = _exact_object(
            profile["trusted_time_dependencies"],
            TRUSTED_TIME_DEPENDENCY_KEYS,
            name="trusted_time_dependencies",
        )
        for key in sorted(TRUSTED_TIME_DEPENDENCY_KEYS):
            dependencies[key] = _binding(
                dependencies[key], name=f"trusted_time_dependencies.{key}"
            )
        capsule_files = profile["capsule_files"]
        if not isinstance(capsule_files, list) or not capsule_files:
            raise EpochRegistryIntegrityError("capsule_files must be a non-empty list")
        normalized_capsule: list[dict[str, str]] = []
        seen: set[str] = set()
        for index, item in enumerate(capsule_files):
            binding = _binding(item, name=f"capsule_files[{index}]")
            if binding["path"] in seen:
                raise EpochRegistryIntegrityError("capsule_files contains duplicates")
            seen.add(binding["path"])
            normalized_capsule.append(binding)
        profile["capsule_files"] = normalized_capsule
        if profile["runtime"] != EXPECTED_RUNTIME:
            raise EpochRegistryIntegrityError("Registry runtime mapping changed")
        if profile["protocol"] != EXPECTED_PROTOCOL:
            raise EpochRegistryIntegrityError("Registry protocol mapping changed")
        candidate = _exact_object(
            profile["candidate"],
            {"live_runtime_directory", "feed_relative_path", "forbidden_namespaces"},
            name="candidate",
        )
        if (
            candidate["live_runtime_directory"] != "live"
            or candidate["feed_relative_path"] != "incoming/daily_finalized_feed.json"
            or not isinstance(candidate["forbidden_namespaces"], list)
            or not candidate["forbidden_namespaces"]
            or len(candidate["forbidden_namespaces"])
            != len(set(candidate["forbidden_namespaces"]))
        ):
            raise EpochRegistryIntegrityError("Candidate namespace contract changed")
        for index, item in enumerate(candidate["forbidden_namespaces"]):
            _relative_path(item, name=f"candidate.forbidden_namespaces[{index}]")
        if profile["engineering_capabilities"] != EXPECTED_CAPABILITIES:
            raise EpochRegistryIntegrityError("Registry capability boundary changed")

        all_bindings: list[dict[str, str]] = [
            *upstream.values(),
            profile["implementation"],
            *root_environment.values(),
            *dependencies.values(),
            *normalized_capsule,
        ]
        expected_by_path: dict[str, str] = {}
        for binding in all_bindings:
            prior = expected_by_path.setdefault(
                binding["path"], binding["expected_sha256"]
            )
            if prior != binding["expected_sha256"]:
                raise EpochRegistryIntegrityError(
                    f"Conflicting expected hash for {binding['path']}"
                )
        for logical_path, expected in sorted(expected_by_path.items()):
            artifact_path = _contained(
                project_root, logical_path, name=f"bound artifact {logical_path}"
            )
            captured = _read_regular(
                artifact_path, name=f"bound artifact {logical_path}"
            )
            if captured.sha256 != expected:
                raise EpochRegistryIntegrityError(
                    f"Bound artifact changed: {logical_path}"
                )
    except EpochRegistryIntegrityError as exc:
        raise EpochRegistryConfigError(str(exc)) from exc
    profile["_profile_path"] = str(path)
    profile["_profile_sha256"] = snapshot.sha256
    profile["_project_root"] = str(project_root)
    return profile


def registry_paths(
    profile: Mapping[str, Any],
    *,
    runtime_root: Path | None = None,
    source_feed_path: Path | None = None,
) -> RegistryPaths:
    project_root = Path(str(profile["_project_root"])).resolve()
    root = (
        _contained(project_root, profile["runtime"]["root"], name="runtime.root")
        if runtime_root is None
        else _absolute_lexical(
            runtime_root.parent.resolve(strict=False) / runtime_root.name
        )
    )
    source_feed = (
        _contained(
            project_root,
            profile["runtime"]["source_feed"],
            name="runtime.source_feed",
        )
        if source_feed_path is None
        else _absolute_lexical(
            source_feed_path.parent.resolve(strict=False) / source_feed_path.name
        )
    )
    return RegistryPaths(
        root=root,
        source_feed=source_feed,
        manager_lock=_contained(
            root, profile["runtime"]["manager_lock"], name="manager_lock"
        ),
        status=_contained(root, profile["runtime"]["status"], name="status"),
        head=_contained(root, profile["runtime"]["head"], name="head"),
        feed_observations=_contained(
            root,
            profile["runtime"]["feed_observations"],
            name="feed_observations",
        ),
        feed_observation_head=_contained(
            root,
            profile["runtime"]["feed_observation_head"],
            name="feed_observation_head",
        ),
        events=_contained(root, profile["runtime"]["events"], name="events"),
        objects=_contained(root, profile["runtime"]["objects"], name="objects"),
        capsules=_contained(root, profile["runtime"]["capsules"], name="capsules"),
        candidate_receipts=_contained(
            root, profile["runtime"]["candidate_receipts"], name="candidate_receipts"
        ),
        slots=_contained(root, profile["runtime"]["slots"], name="slots"),
    )


def _acquire_manager_lock(paths: RegistryPaths):
    _mkdir(paths.root, root=paths.root)
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    handle = None
    try:
        descriptor = os.open(paths.manager_lock, flags, 0o600)
        handle = os.fdopen(descriptor, "a+b", buffering=0)
        descriptor = None
        opened = os.fstat(handle.fileno())
        pathname = os.lstat(paths.manager_lock)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(pathname.st_mode)
            or (opened.st_dev, opened.st_ino) != (pathname.st_dev, pathname.st_ino)
        ):
            raise EpochRegistryIntegrityError("Manager lock path is not its open file")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = os.fstat(handle.fileno())
        pathname = os.lstat(paths.manager_lock)
        if (locked.st_dev, locked.st_ino) != (pathname.st_dev, pathname.st_ino):
            raise EpochRegistryIntegrityError("Manager lock changed while acquired")
        return handle
    except BlockingIOError as exc:
        if handle is not None:
            handle.close()
        raise EpochRegistryBusyError("Epoch registry manager lock is busy") from exc
    except EpochRegistryIntegrityError:
        if handle is not None:
            handle.close()
        raise
    except OSError as exc:
        if handle is not None:
            handle.close()
        raise EpochRegistryIntegrityError("Cannot safely acquire manager lock") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _all_static_bindings(profile: Mapping[str, Any]) -> list[dict[str, str]]:
    bindings = [
        *profile["upstream_profiles"].values(),
        profile["implementation"],
        *profile["root_environment"].values(),
        *profile["trusted_time_dependencies"].values(),
        *profile["capsule_files"],
    ]
    by_path: dict[str, dict[str, str]] = {}
    for binding in bindings:
        by_path.setdefault(binding["path"], binding)
    return [by_path[key] for key in sorted(by_path)]


def _capture_capsule(
    profile: Mapping[str, Any], paths: RegistryPaths
) -> tuple[dict[str, object], ArtifactSnapshot]:
    project_root = Path(str(profile["_project_root"])).resolve()
    records: list[dict[str, object]] = []
    bindings: list[tuple[str, str | None]] = [
        (
            str(Path(str(profile["_profile_path"])).relative_to(project_root)),
            str(profile["_profile_sha256"]),
        ),
        *[
            (binding["path"], binding["expected_sha256"])
            for binding in _all_static_bindings(profile)
        ],
    ]
    seen: set[str] = set()
    for logical_path, expected in sorted(bindings):
        if logical_path in seen:
            continue
        seen.add(logical_path)
        source = _contained(project_root, logical_path, name=f"capsule.{logical_path}")
        captured = _read_regular(source, name=f"capsule artifact {logical_path}")
        if expected is not None and captured.sha256 != expected:
            raise EpochRegistryIntegrityError(
                f"Capsule binding changed: {logical_path}"
            )
        object_path = paths.objects / captured.sha256
        published = _publish_once(
            object_path,
            captured.raw,
            root=paths.root,
            name=f"capsule object {captured.sha256}",
        )
        records.append(
            {
                "logical_path": logical_path,
                "sha256": captured.sha256,
                "size_bytes": captured.size_bytes,
                "object_path": str(published.path.relative_to(paths.root)),
            }
        )
    tree_payload = [
        {
            "logical_path": item["logical_path"],
            "sha256": item["sha256"],
            "size_bytes": item["size_bytes"],
        }
        for item in records
    ]
    manifest = {
        "schema_version": profile["protocol"]["capsule_manifest_schema_version"],
        "case": "ootang",
        "registry_profile_sha256": profile["_profile_sha256"],
        "artifact_count": len(records),
        "tree_sha256": _sha256(_canonical_bytes(tree_payload)),
        "artifacts": records,
        "materialized_executable_tree": False,
        "active_switch_authorized": False,
        "formal_warning_output": False,
    }
    raw = _canonical_bytes(manifest)
    manifest_path = paths.capsules / f"{_sha256(raw)}.json"
    snapshot = _publish_once(
        manifest_path, raw, root=paths.root, name="epoch capsule manifest"
    )
    return manifest, snapshot


def _artifact_payload(path: Path, *, root: Path, name: str) -> dict[str, object]:
    root = _absolute_lexical(root)
    path = _absolute_lexical(path)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise EpochRegistryIntegrityError(
            f"{name} is outside its candidate slot"
        ) from exc
    _ensure_existing_parents_not_symlinks(root, path)
    captured = _read_regular(path, name=name)
    return captured.record()


def _live_epoch_id(profile: Mapping[str, Any], prerequisites: object) -> str:
    source = getattr(prerequisites, "source")
    model = getattr(prerequisites, "model")
    live_binding = profile["upstream_profiles"]["live"]
    live_profile_raw = _read_regular(
        _contained(
            Path(str(profile["_project_root"])),
            live_binding["path"],
            name="live profile",
        ),
        name="live profile",
    ).raw
    live_profile = _decode_json(live_profile_raw, name="live profile")
    return _live_canonical_sha256(
        {
            "profile_id": live_profile["profile_id"],
            "profile_sha256": live_binding["expected_sha256"],
            "source_snapshot_sha256": source.sha256,
            "model_manifest_sha256": model.sha256,
            "watermark": source.watermark.isoformat(),
            "implementation_sha256": prerequisites.implementation_sha256,
            "environment_sha256": prerequisites.environment_sha256,
        }
    )


def _default_prebuilder(
    profile: Mapping[str, Any],
    paths: RegistryPaths,
    live_root: Path,
    _status_checked_at: datetime,
    _feed_copy: Path,
) -> CandidateBuild | None:
    from convlstm import ootang_production_bundle as production_bundle
    from monitoring import ootang_live_source as source_module
    from monitoring import ootang_prequential_live as live_module

    project_root = Path(str(profile["_project_root"]))
    expected_feed_copy = live_root / profile["candidate"]["feed_relative_path"]
    if _absolute_lexical(_feed_copy) != _absolute_lexical(expected_feed_copy):
        raise EpochRegistryIntegrityError("Candidate prebuilder feed path changed")
    stable_feed = _read_regular(_feed_copy, name="candidate prebuilder feed")
    try:
        deploy_path = _contained(
            project_root,
            profile["upstream_profiles"]["deploy"]["path"],
            name="deploy profile",
        )
        deploy_profile = source_module.load_deploy_profile(
            deploy_path, project_root=project_root
        )
        result = source_module.ingest_source(
            deploy_profile,
            runtime_root=live_root,
            project_root=project_root,
            now=None,
        )
        if result.source is None:
            return None
        production_bundle.run_once(deploy_path, runtime_root=live_root, now=None)

        activation = source_module.load_activation_source(
            deploy_profile, runtime_root=live_root, project_root=project_root
        )
        current = source_module.load_current_source(
            deploy_profile, runtime_root=live_root, project_root=project_root
        )
        bundle_profile = production_bundle.load_deploy_profile(
            deploy_path, project_root=project_root
        )
        loaded_bundle = production_bundle.load_deploy_bundle(
            bundle_profile,
            runtime_root=live_root,
            source=activation,
            now=None,
        )
        live_path = _contained(
            project_root,
            profile["upstream_profiles"]["live"]["path"],
            name="live profile",
        )
        live_profile = live_module.load_config(live_path)
        live_paths = live_module.runtime_paths(live_profile, runtime_root=live_root)
        prerequisites = live_module.load_prerequisites(live_profile, live_paths)
        if prerequisites is None:
            raise EpochRegistryIntegrityError(
                "Candidate source/model disappeared after public reload"
            )
    except (
        source_module.SourceBusyError,
        production_bundle.ProductionBundleBusyError,
        live_module.LiveRunnerBusy,
    ) as exc:
        raise EpochRegistryBusyError("Candidate prebuilder lock is busy") from exc
    except EpochRegistryError:
        raise
    except (
        source_module.SourceError,
        production_bundle.ProductionBundleError,
        live_module.LiveConfigError,
        live_module.LivePrerequisiteError,
        live_module.LiveInputError,
        live_module.LiveIntegrityError,
    ) as exc:
        raise EpochRegistryIntegrityError(
            f"Candidate public reload failed: {type(exc).__name__}"
        ) from exc

    semantic_payload = _decode_json(
        _read_regular(
            activation.semantic_manifest.path,
            name="candidate activation semantic manifest",
        ).raw,
        name="candidate activation semantic manifest",
    )
    lineage = _exact_object(
        semantic_payload.get("lineage"),
        {"historical_base", "daily_feed_snapshot"},
        name="candidate activation semantic lineage",
    )
    feed_lineage = _exact_object(
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
        name="candidate activation feed lineage",
    )
    feed_artifact = _validate_artifact_record(
        {key: feed_lineage[key] for key in ARTIFACT_KEYS},
        slot_root=live_root,
        name="candidate activation feed snapshot",
    )
    final_feed = _read_regular(_feed_copy, name="candidate prebuilder feed")
    if (
        feed_artifact["sha256"] != stable_feed.sha256
        or feed_artifact["size_bytes"] != stable_feed.size_bytes
        or final_feed.sha256 != stable_feed.sha256
        or final_feed.size_bytes != stable_feed.size_bytes
        or final_feed.raw != stable_feed.raw
    ):
        raise EpochRegistryIntegrityError(
            "Candidate source lineage does not bind the stable finalized feed"
        )

    source_artifacts: list[dict[str, object]] = []
    for role, artifact in (
        ("current_source_pointer", live_root / "source_current.json"),
        ("activation_source_manifest", activation.activation_manifest.path),
        ("semantic_source_manifest", activation.semantic_manifest.path),
        ("canonical_source_dataset", activation.dataset.path),
        ("current_semantic_source_manifest", current.semantic_manifest.path),
        ("current_canonical_source_dataset", current.dataset.path),
    ):
        record = _artifact_payload(artifact, root=live_root, name=role)
        record["role"] = role
        source_artifacts.append(record)
    source_artifacts.append({**feed_artifact, "role": "daily_feed_snapshot"})
    model_manifest = _artifact_payload(
        loaded_bundle.manifest_path,
        root=live_root,
        name="candidate model manifest",
    )
    training_manifest = _artifact_payload(
        loaded_bundle.training_manifest.path,
        root=live_root,
        name="candidate training manifest",
    )
    checkpoints = tuple(
        {
            **_artifact_payload(
                checkpoint.artifact.path,
                root=live_root,
                name=f"candidate checkpoint seed {checkpoint.seed}",
            ),
            "seed": checkpoint.seed,
        }
        for checkpoint in loaded_bundle.checkpoints
    )
    return CandidateBuild(
        live_epoch_id=_live_epoch_id(profile, prerequisites),
        watermark=activation.watermark.isoformat(),
        outcome_source_id=activation.outcome_source_id,
        source_artifacts=tuple(source_artifacts),
        model_manifest=model_manifest,
        training_manifest=training_manifest,
        checkpoints=checkpoints,
        algorithm_profile_sha256=prerequisites.algorithm_profile_sha256,
        implementation_sha256=prerequisites.implementation_sha256,
        environment_sha256=prerequisites.environment_sha256,
    )


def _validate_artifact_record(
    value: object,
    *,
    slot_root: Path,
    name: str,
    extra_keys: set[str] | None = None,
    verify_bytes: bool = True,
) -> dict[str, Any]:
    keys = set(ARTIFACT_KEYS)
    if extra_keys:
        keys.update(extra_keys)
    record = _exact_object(value, keys, name=name)
    path_text = _nonempty_text(record["path"], name=f"{name}.path")
    path = _absolute_lexical(Path(path_text))
    slot_root = _absolute_lexical(slot_root)
    try:
        path.relative_to(slot_root)
        if verify_bytes:
            _ensure_existing_parents_not_symlinks(slot_root, path)
    except (EpochRegistryIntegrityError, ValueError) as exc:
        raise EpochRegistryIntegrityError(
            f"{name} path escapes candidate slot"
        ) from exc
    expected_sha = _require_hash(record["sha256"], name=f"{name}.sha256")
    if type(record["size_bytes"]) is not int or record["size_bytes"] < 0:
        raise EpochRegistryIntegrityError(f"{name}.size_bytes changed")
    if verify_bytes:
        captured = _read_regular(path, name=name)
        if (
            captured.sha256 != expected_sha
            or captured.size_bytes != record["size_bytes"]
        ):
            raise EpochRegistryIntegrityError(f"{name} bytes changed")
    return record


def _validate_build(
    profile: Mapping[str, Any],
    build: CandidateBuild,
    *,
    live_root: Path,
    verify_artifact_bytes: bool = True,
    expected_feed: ArtifactSnapshot | None = None,
) -> dict[str, object]:
    payload = build.payload()
    _exact_object(payload, BUILD_KEYS, name="candidate build")
    _require_hash(payload["live_epoch_id"], name="candidate.live_epoch_id")
    _nonempty_text(payload["watermark"], name="candidate.watermark")
    try:
        datetime.fromisoformat(f"{payload['watermark']}T00:00:00+00:00")
    except ValueError as exc:
        raise EpochRegistryIntegrityError("Candidate watermark is not a date") from exc
    _nonempty_text(payload["outcome_source_id"], name="candidate.outcome_source_id")
    source_artifacts = payload["source_artifacts"]
    if not isinstance(source_artifacts, list) or len(source_artifacts) < 4:
        raise EpochRegistryIntegrityError("Candidate source artifacts are incomplete")
    roles: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(source_artifacts):
        record = _validate_artifact_record(
            item,
            slot_root=live_root,
            name=f"candidate.source_artifacts[{index}]",
            extra_keys={"role"},
            verify_bytes=verify_artifact_bytes,
        )
        role = _nonempty_text(record["role"], name="candidate source role")
        if role in roles:
            raise EpochRegistryIntegrityError("Candidate source artifact role repeated")
        roles[role] = record
    required_roles = {
        "current_source_pointer",
        "activation_source_manifest",
        "semantic_source_manifest",
        "canonical_source_dataset",
        "daily_feed_snapshot",
    }
    if not required_roles.issubset(roles):
        raise EpochRegistryIntegrityError("Candidate source lineage is incomplete")
    if expected_feed is not None and (
        roles["daily_feed_snapshot"]["sha256"] != expected_feed.sha256
        or roles["daily_feed_snapshot"]["size_bytes"] != expected_feed.size_bytes
    ):
        raise EpochRegistryIntegrityError(
            "Candidate source lineage does not match the registry feed snapshot"
        )
    model_manifest = _validate_artifact_record(
        payload["model_manifest"],
        slot_root=live_root,
        name="candidate.model_manifest",
        verify_bytes=verify_artifact_bytes,
    )
    _validate_artifact_record(
        payload["training_manifest"],
        slot_root=live_root,
        name="candidate.training_manifest",
        verify_bytes=verify_artifact_bytes,
    )
    checkpoints = payload["checkpoints"]
    if not isinstance(checkpoints, list) or len(checkpoints) != 5:
        raise EpochRegistryIntegrityError("Candidate must contain five checkpoints")
    seeds: list[int] = []
    for index, item in enumerate(checkpoints):
        record = _validate_artifact_record(
            item,
            slot_root=live_root,
            name=f"candidate.checkpoints[{index}]",
            extra_keys={"seed"},
            verify_bytes=verify_artifact_bytes,
        )
        if type(record["seed"]) is not int:
            raise EpochRegistryIntegrityError("Candidate checkpoint seed changed")
        seeds.append(record["seed"])
    if seeds != [0, 1, 2, 3, 4]:
        raise EpochRegistryIntegrityError("Candidate checkpoint seeds changed")
    for key in (
        "algorithm_profile_sha256",
        "implementation_sha256",
        "environment_sha256",
    ):
        _require_hash(payload[key], name=f"candidate.{key}")
    live_binding = profile["upstream_profiles"]["live"]
    live_profile_snapshot = _read_regular(
        _contained(
            Path(str(profile["_project_root"])),
            live_binding["path"],
            name="candidate live profile",
        ),
        name="candidate live profile",
        maximum_bytes=MAX_CONTROL_BYTES,
    )
    live_profile = _decode_json(
        live_profile_snapshot.raw, name="candidate live profile"
    )
    live_profile_id = _nonempty_text(
        live_profile.get("profile_id"), name="candidate live profile id"
    )
    expected_live_epoch_id = _live_canonical_sha256(
        {
            "profile_id": live_profile_id,
            "profile_sha256": live_binding["expected_sha256"],
            "source_snapshot_sha256": roles["activation_source_manifest"]["sha256"],
            "model_manifest_sha256": model_manifest["sha256"],
            "watermark": payload["watermark"],
            "implementation_sha256": payload["implementation_sha256"],
            "environment_sha256": payload["environment_sha256"],
        }
    )
    if payload["live_epoch_id"] != expected_live_epoch_id:
        raise EpochRegistryIntegrityError("Candidate live epoch identity changed")
    return payload


def _assert_forbidden_namespaces_empty(
    profile: Mapping[str, Any], live_root: Path
) -> None:
    for relative in profile["candidate"]["forbidden_namespaces"]:
        path = _contained(live_root, relative, name=f"forbidden namespace {relative}")
        if path.exists() or path.is_symlink():
            raise EpochRegistryIntegrityError(
                f"Candidate execution namespace is not empty: {relative}"
            )


def _verify_capsule(
    profile: Mapping[str, Any], paths: RegistryPaths, reference: object
) -> dict[str, Any]:
    record = _exact_object(reference, ARTIFACT_KEYS, name="capsule reference")
    if type(record["size_bytes"]) is not int or record["size_bytes"] < 0:
        raise EpochRegistryIntegrityError("Capsule reference size changed")
    path = _contained(paths.root, record["path"], name="capsule reference path")
    if path.parent != paths.capsules:
        raise EpochRegistryIntegrityError(
            "Capsule reference is outside capsule namespace"
        )
    snapshot = _read_regular(
        path, name="capsule manifest", maximum_bytes=MAX_CONTROL_BYTES
    )
    if (
        snapshot.sha256 != _require_hash(record["sha256"], name="capsule sha256")
        or snapshot.size_bytes != record["size_bytes"]
        or path.name != f"{snapshot.sha256}.json"
    ):
        raise EpochRegistryIntegrityError("Capsule reference changed")
    manifest = _decode_json(snapshot.raw, name="capsule manifest")
    if snapshot.raw != _canonical_bytes(manifest):
        raise EpochRegistryIntegrityError("Capsule manifest is not canonical")
    _exact_object(
        manifest,
        {
            "schema_version",
            "case",
            "registry_profile_sha256",
            "artifact_count",
            "tree_sha256",
            "artifacts",
            "materialized_executable_tree",
            "active_switch_authorized",
            "formal_warning_output",
        },
        name="capsule manifest",
    )
    if (
        manifest["schema_version"]
        != profile["protocol"]["capsule_manifest_schema_version"]
        or manifest["case"] != "ootang"
        or manifest["registry_profile_sha256"] != profile["_profile_sha256"]
        or manifest["materialized_executable_tree"] is not False
        or manifest["active_switch_authorized"] is not False
        or manifest["formal_warning_output"] is not False
    ):
        raise EpochRegistryIntegrityError("Capsule boundary changed")
    artifacts = manifest["artifacts"]
    project_root = Path(str(profile["_project_root"])).resolve()
    profile_logical_path = str(
        _absolute_lexical(Path(str(profile["_profile_path"]))).relative_to(project_root)
    )
    expected_artifacts: dict[str, str | None] = {
        profile_logical_path: profile["_profile_sha256"],
        **{
            binding["path"]: (
                None
                if binding["path"] == IMPLEMENTATION_LOGICAL_PATH
                else binding["expected_sha256"]
            )
            for binding in _all_static_bindings(profile)
        },
    }
    if (
        not isinstance(artifacts, list)
        or type(manifest["artifact_count"]) is not int
        or manifest["artifact_count"] != len(artifacts)
        or manifest["artifact_count"] != len(expected_artifacts)
    ):
        raise EpochRegistryIntegrityError("Capsule artifact count changed")
    tree: list[dict[str, object]] = []
    logical_paths: set[str] = set()
    for index, item in enumerate(artifacts):
        artifact = _exact_object(
            item, CAPSULE_ARTIFACT_KEYS, name=f"capsule.artifacts[{index}]"
        )
        logical = str(
            _relative_path(artifact["logical_path"], name="capsule logical path")
        )
        if logical in logical_paths:
            raise EpochRegistryIntegrityError("Capsule logical path repeated")
        logical_paths.add(logical)
        expected_sha = _require_hash(artifact["sha256"], name="capsule object sha")
        if logical not in expected_artifacts or (
            expected_artifacts[logical] is not None
            and expected_artifacts[logical] != expected_sha
        ):
            raise EpochRegistryIntegrityError("Capsule binding set changed")
        if type(artifact["size_bytes"]) is not int or artifact["size_bytes"] < 0:
            raise EpochRegistryIntegrityError("Capsule object size changed")
        object_path = _contained(
            paths.root, artifact["object_path"], name="capsule object path"
        )
        if object_path.parent != paths.objects or object_path.name != expected_sha:
            raise EpochRegistryIntegrityError("Capsule object namespace changed")
        captured = _read_regular(object_path, name="capsule object")
        if (
            captured.sha256 != expected_sha
            or captured.size_bytes != artifact["size_bytes"]
        ):
            raise EpochRegistryIntegrityError("Capsule object bytes changed")
        tree.append(
            {
                "logical_path": logical,
                "sha256": expected_sha,
                "size_bytes": artifact["size_bytes"],
            }
        )
    if [item["logical_path"] for item in tree] != sorted(expected_artifacts):
        raise EpochRegistryIntegrityError("Capsule artifact order changed")
    if _sha256(_canonical_bytes(tree)) != _require_hash(
        manifest["tree_sha256"], name="capsule tree sha256"
    ):
        raise EpochRegistryIntegrityError("Capsule tree hash changed")
    return manifest


def _profile_bindings(profile: Mapping[str, Any]) -> dict[str, str]:
    return {
        key: profile["upstream_profiles"][key]["expected_sha256"]
        for key in sorted(UPSTREAM_KEYS)
    }


def _candidate_artifact_specs(
    build: Mapping[str, object],
) -> list[tuple[str, Mapping[str, object]]]:
    specs: list[tuple[str, Mapping[str, object]]] = []
    source_artifacts = build.get("source_artifacts")
    checkpoints = build.get("checkpoints")
    if not isinstance(source_artifacts, list) or not isinstance(checkpoints, list):
        raise EpochRegistryIntegrityError("Candidate artifact collection changed")
    for record in source_artifacts:
        if not isinstance(record, dict):
            raise EpochRegistryIntegrityError("Candidate source artifact changed")
        role = _nonempty_text(record.get("role"), name="candidate source role")
        specs.append((f"source/{role}", record))
    for logical_id, key in (
        ("model/manifest", "model_manifest"),
        ("model/training_manifest", "training_manifest"),
    ):
        record = build.get(key)
        if not isinstance(record, dict):
            raise EpochRegistryIntegrityError(f"Candidate {key} changed")
        specs.append((logical_id, record))
    for record in checkpoints:
        if not isinstance(record, dict) or type(record.get("seed")) is not int:
            raise EpochRegistryIntegrityError("Candidate checkpoint changed")
        specs.append((f"model/checkpoint/{record['seed']}", record))
    specs.sort(key=lambda item: item[0])
    if len({logical_id for logical_id, _ in specs}) != len(specs):
        raise EpochRegistryIntegrityError("Candidate artifact logical id repeated")
    return specs


def _capture_candidate_artifacts(
    paths: RegistryPaths, build: Mapping[str, object]
) -> list[dict[str, object]]:
    snapshots: list[dict[str, object]] = []
    for logical_id, record in _candidate_artifact_specs(build):
        path = Path(_nonempty_text(record.get("path"), name=f"{logical_id} path"))
        captured = _read_regular(path, name=f"candidate artifact {logical_id}")
        expected_sha = _require_hash(
            record.get("sha256"), name=f"candidate artifact {logical_id} sha256"
        )
        size = record.get("size_bytes")
        if type(size) is not int or size < 0:
            raise EpochRegistryIntegrityError(
                f"Candidate artifact {logical_id} size changed"
            )
        if captured.sha256 != expected_sha or captured.size_bytes != size:
            raise EpochRegistryIntegrityError(
                f"Candidate artifact {logical_id} bytes changed"
            )
        published = _publish_once(
            paths.objects / captured.sha256,
            captured.raw,
            root=paths.root,
            name=f"candidate snapshot object {logical_id}",
        )
        snapshots.append(
            {
                "logical_id": logical_id,
                "original_path": str(captured.path),
                "sha256": captured.sha256,
                "size_bytes": captured.size_bytes,
                "object_path": str(published.path.relative_to(paths.root)),
            }
        )
    return snapshots


def _verify_candidate_artifacts(
    paths: RegistryPaths,
    build: Mapping[str, object],
    snapshots: object,
) -> None:
    if not isinstance(snapshots, list):
        raise EpochRegistryIntegrityError("Candidate artifact snapshots changed")
    specs = _candidate_artifact_specs(build)
    if len(snapshots) != len(specs):
        raise EpochRegistryIntegrityError("Candidate artifact snapshot count changed")
    for index, ((logical_id, expected), value) in enumerate(zip(specs, snapshots)):
        snapshot = _exact_object(
            value,
            CANDIDATE_SNAPSHOT_KEYS,
            name=f"candidate artifact snapshots[{index}]",
        )
        if snapshot["logical_id"] != logical_id:
            raise EpochRegistryIntegrityError(
                "Candidate artifact snapshot order changed"
            )
        expected_path = _nonempty_text(
            expected.get("path"), name=f"candidate artifact {logical_id} path"
        )
        expected_sha = _require_hash(
            expected.get("sha256"), name=f"candidate artifact {logical_id} sha256"
        )
        expected_size = expected.get("size_bytes")
        if (
            snapshot["original_path"] != expected_path
            or snapshot["sha256"] != expected_sha
            or snapshot["size_bytes"] != expected_size
        ):
            raise EpochRegistryIntegrityError(
                "Candidate artifact snapshot binding changed"
            )
        object_path = _contained(
            paths.root,
            snapshot["object_path"],
            name=f"candidate artifact {logical_id} object path",
        )
        if object_path.parent != paths.objects or object_path.name != expected_sha:
            raise EpochRegistryIntegrityError(
                "Candidate artifact snapshot namespace changed"
            )
        captured = _read_regular(
            object_path, name=f"candidate snapshot object {logical_id}"
        )
        if captured.sha256 != expected_sha or captured.size_bytes != expected_size:
            raise EpochRegistryIntegrityError(
                f"Candidate snapshot object {logical_id} changed"
            )


def _receipt_payload(
    profile: Mapping[str, Any],
    paths: RegistryPaths,
    *,
    slot_id: str,
    live_root: Path,
    feed_copy_path: Path,
    feed_snapshot: ArtifactSnapshot,
    capsule: ArtifactSnapshot,
    build: dict[str, object],
    candidate_artifact_snapshots: list[dict[str, object]],
) -> dict[str, object]:
    identity = {
        "schema_version": profile["protocol"]["candidate_receipt_schema_version"],
        "case": "ootang",
        "registry_profile_sha256": profile["_profile_sha256"],
        "slot_id": slot_id,
        "slot_live_root": str(live_root),
        "feed_slot_path": str(feed_copy_path),
        "feed": {
            "path": str(feed_snapshot.path.relative_to(paths.root)),
            "sha256": feed_snapshot.sha256,
            "size_bytes": feed_snapshot.size_bytes,
        },
        "capsule": {
            "path": str(capsule.path.relative_to(paths.root)),
            "sha256": capsule.sha256,
            "size_bytes": capsule.size_bytes,
        },
        "upstream_profile_sha256": _profile_bindings(profile),
        "candidate_build": build,
        "candidate_artifact_snapshots": candidate_artifact_snapshots,
        "candidate_namespaces_verified_empty": True,
        "candidate_ledger_initialized": False,
        "active_epoch_selected": False,
    }
    candidate_id = _sha256(_canonical_bytes(identity))
    return {
        **identity,
        "candidate_id": candidate_id,
        "immutable_candidate_registry_implemented": True,
        "candidate_prebuild_implemented": True,
        "active_epoch_switch_implemented": False,
        "automatic_epoch_rotation_implemented": False,
        "trusted_anchor_receipt_verified": False,
        "e2_live_evidence_eligible": False,
        "real_activation_ready": False,
        "formal_warning_output": False,
    }


def _candidate_receipt_snapshot(
    paths: RegistryPaths, reference: object
) -> ArtifactSnapshot:
    record = _exact_object(reference, ARTIFACT_KEYS, name="candidate receipt reference")
    path = _contained(paths.root, record["path"], name="candidate receipt path")
    if path.parent != paths.candidate_receipts:
        raise EpochRegistryIntegrityError("Candidate receipt namespace changed")
    expected_sha = _require_hash(record["sha256"], name="candidate receipt sha256")
    if type(record["size_bytes"]) is not int or record["size_bytes"] < 0:
        raise EpochRegistryIntegrityError("Candidate receipt size changed")
    snapshot = _read_regular(
        path, name="candidate receipt", maximum_bytes=MAX_CONTROL_BYTES
    )
    if (
        snapshot.sha256 != expected_sha
        or snapshot.size_bytes != record["size_bytes"]
        or path.name != f"{snapshot.sha256}.json"
    ):
        raise EpochRegistryIntegrityError("Candidate receipt reference changed")
    return snapshot


def _verify_receipt(
    profile: Mapping[str, Any],
    paths: RegistryPaths,
    reference: object,
    *,
    require_namespaces_empty: bool = False,
    verify_current_artifacts: bool = False,
) -> dict[str, Any]:
    snapshot = _candidate_receipt_snapshot(paths, reference)
    receipt = _decode_json(snapshot.raw, name="candidate receipt")
    if snapshot.raw != _canonical_bytes(receipt):
        raise EpochRegistryIntegrityError("Candidate receipt is not canonical")
    expected_keys = {
        "schema_version",
        "case",
        "registry_profile_sha256",
        "slot_id",
        "slot_live_root",
        "feed_slot_path",
        "feed",
        "capsule",
        "upstream_profile_sha256",
        "candidate_build",
        "candidate_artifact_snapshots",
        "candidate_namespaces_verified_empty",
        "candidate_ledger_initialized",
        "active_epoch_selected",
        "candidate_id",
        "immutable_candidate_registry_implemented",
        "candidate_prebuild_implemented",
        "active_epoch_switch_implemented",
        "automatic_epoch_rotation_implemented",
        "trusted_anchor_receipt_verified",
        "e2_live_evidence_eligible",
        "real_activation_ready",
        "formal_warning_output",
    }
    _exact_object(receipt, expected_keys, name="candidate receipt")
    if (
        receipt["schema_version"]
        != profile["protocol"]["candidate_receipt_schema_version"]
        or receipt["case"] != "ootang"
        or receipt["registry_profile_sha256"] != profile["_profile_sha256"]
        or receipt["upstream_profile_sha256"] != _profile_bindings(profile)
        or receipt["candidate_namespaces_verified_empty"] is not True
        or receipt["candidate_ledger_initialized"] is not False
        or receipt["active_epoch_selected"] is not False
        or receipt["immutable_candidate_registry_implemented"] is not True
        or receipt["candidate_prebuild_implemented"] is not True
    ):
        raise EpochRegistryIntegrityError("Candidate receipt boundary changed")
    for key in CLAIM_KEYS:
        if receipt[key] is not False:
            raise EpochRegistryIntegrityError(f"Candidate receipt claim {key} changed")
    slot_id = _require_hash(receipt["slot_id"], name="receipt.slot_id")
    live_root = _absolute_lexical(paths.slots / slot_id / "live")
    if receipt["slot_live_root"] != str(live_root):
        raise EpochRegistryIntegrityError("Candidate slot root changed")
    feed = _exact_object(receipt["feed"], ARTIFACT_KEYS, name="candidate feed snapshot")
    feed_path = _contained(paths.root, feed["path"], name="candidate feed object path")
    feed_sha = _require_hash(feed["sha256"], name="candidate feed sha256")
    feed_size = feed["size_bytes"]
    if type(feed_size) is not int or feed_size < 0:
        raise EpochRegistryIntegrityError("Candidate feed size changed")
    if feed_path.parent != paths.objects or feed_path.name != feed_sha:
        raise EpochRegistryIntegrityError("Candidate feed object namespace changed")
    feed_snapshot = _read_regular(feed_path, name="candidate feed snapshot")
    if feed_snapshot.sha256 != feed_sha or feed_snapshot.size_bytes != feed_size:
        raise EpochRegistryIntegrityError("Candidate feed snapshot changed")
    expected_feed = live_root / "incoming" / "daily_finalized_feed.json"
    if _absolute_lexical(Path(str(receipt["feed_slot_path"]))) != expected_feed:
        raise EpochRegistryIntegrityError("Candidate feed path changed")
    if verify_current_artifacts:
        current_feed = _read_regular(
            expected_feed, name="candidate feed in stable slot"
        )
        if (
            current_feed.sha256 != feed_snapshot.sha256
            or current_feed.size_bytes != feed_snapshot.size_bytes
            or current_feed.raw != feed_snapshot.raw
        ):
            raise EpochRegistryIntegrityError(
                "Candidate feed in stable slot changed before commit"
            )
    feed_semantics = _feed_semantics(feed_snapshot)
    capsule_manifest = _verify_capsule(profile, paths, receipt["capsule"])
    expected_slot_id = _sha256(
        _canonical_bytes(
            {
                "domain": profile["protocol"]["slot_identity_domain"],
                "registry_profile_sha256": profile["_profile_sha256"],
                "feed_sha256": feed_sha,
                "capsule_tree_sha256": capsule_manifest["tree_sha256"],
            }
        )
    )
    if slot_id != expected_slot_id:
        raise EpochRegistryIntegrityError("Candidate stable slot identity changed")
    build = _exact_object(
        receipt["candidate_build"], BUILD_KEYS, name="candidate build"
    )
    candidate_build = CandidateBuild(
        live_epoch_id=build["live_epoch_id"],
        watermark=build["watermark"],
        outcome_source_id=build["outcome_source_id"],
        source_artifacts=tuple(build["source_artifacts"]),
        model_manifest=build["model_manifest"],
        training_manifest=build["training_manifest"],
        checkpoints=tuple(build["checkpoints"]),
        algorithm_profile_sha256=build["algorithm_profile_sha256"],
        implementation_sha256=build["implementation_sha256"],
        environment_sha256=build["environment_sha256"],
    )
    _validate_build(
        profile,
        candidate_build,
        live_root=live_root,
        verify_artifact_bytes=verify_current_artifacts,
        expected_feed=feed_snapshot,
    )
    _verify_candidate_artifacts(paths, build, receipt["candidate_artifact_snapshots"])
    if (
        candidate_build.watermark != feed_semantics.watermark.isoformat()
        or candidate_build.outcome_source_id != feed_semantics.outcome_source_id
    ):
        raise EpochRegistryIntegrityError(
            "Candidate build does not match finalized feed semantics"
        )
    if require_namespaces_empty:
        _assert_forbidden_namespaces_empty(profile, live_root)
    identity = {
        key: receipt[key]
        for key in (
            "schema_version",
            "case",
            "registry_profile_sha256",
            "slot_id",
            "slot_live_root",
            "feed_slot_path",
            "feed",
            "capsule",
            "upstream_profile_sha256",
            "candidate_build",
            "candidate_artifact_snapshots",
            "candidate_namespaces_verified_empty",
            "candidate_ledger_initialized",
            "active_epoch_selected",
        )
    }
    if receipt["candidate_id"] != _sha256(_canonical_bytes(identity)):
        raise EpochRegistryIntegrityError("Candidate identity changed")
    return receipt


def _receipt_feed_semantics(
    paths: RegistryPaths, receipt: Mapping[str, object]
) -> FeedSemantics:
    feed = receipt.get("feed")
    if not isinstance(feed, dict):
        raise EpochRegistryIntegrityError("Candidate receipt feed changed")
    path = _contained(paths.root, feed.get("path"), name="candidate receipt feed path")
    return _feed_semantics(_read_regular(path, name="candidate receipt feed"))


def _find_current_slot_candidate(
    profile: Mapping[str, Any],
    paths: RegistryPaths,
    events: Sequence[Mapping[str, object]],
    *,
    slot_id: str,
) -> tuple[dict[str, Any], ArtifactSnapshot] | None:
    matching_events = [event for event in events if event["slot_id"] == slot_id]
    if matching_events:
        if len(matching_events) != 1 or matching_events[0] is not events[-1]:
            raise EpochRegistryIntegrityError(
                "Candidate feed would roll the registry back to an older slot"
            )
        reference = matching_events[0]["candidate_receipt"]
        receipt = _verify_receipt(profile, paths, reference)
        return receipt, _candidate_receipt_snapshot(paths, reference)

    if (
        not paths.candidate_receipts.exists()
        and not paths.candidate_receipts.is_symlink()
    ):
        return None
    try:
        mode = os.lstat(paths.candidate_receipts).st_mode
    except OSError as exc:
        raise EpochRegistryIntegrityError(
            "Cannot inspect candidate receipt registry"
        ) from exc
    if not stat.S_ISDIR(mode) or stat.S_ISLNK(mode):
        raise EpochRegistryIntegrityError(
            "Candidate receipt registry is not a real directory"
        )
    matches: list[tuple[dict[str, Any], ArtifactSnapshot]] = []
    for path in sorted(paths.candidate_receipts.iterdir(), key=lambda item: item.name):
        snapshot = _read_regular(
            path, name="candidate receipt", maximum_bytes=MAX_CONTROL_BYTES
        )
        reference = {
            "path": str(snapshot.path.relative_to(paths.root)),
            "sha256": snapshot.sha256,
            "size_bytes": snapshot.size_bytes,
        }
        receipt = _verify_receipt(profile, paths, reference)
        if receipt["slot_id"] == slot_id:
            receipt = _verify_receipt(
                profile,
                paths,
                reference,
                require_namespaces_empty=True,
                verify_current_artifacts=True,
            )
            matches.append((receipt, snapshot))
    if len(matches) > 1:
        raise EpochRegistryIntegrityError(
            "Stable slot has multiple candidate receipt semantics"
        )
    return matches[0] if matches else None


def _assert_current_feed_follows_registry(
    profile: Mapping[str, Any],
    paths: RegistryPaths,
    events: Sequence[Mapping[str, object]],
    current: FeedSemantics,
) -> None:
    if not events:
        return
    revision_ids: dict[date, set[str]] = {}
    record_hashes: dict[date, set[str]] = {}
    previous: FeedSemantics | None = None
    for event in events:
        receipt = _verify_receipt(
            profile,
            paths,
            event["candidate_receipt"],
            require_namespaces_empty=False,
        )
        semantics = _receipt_feed_semantics(paths, receipt)
        _extend_feed_history(semantics, revision_ids, record_hashes)
        previous = semantics
    assert previous is not None
    _assert_feed_successor(
        previous,
        current,
        seen_revision_ids=revision_ids,
        seen_record_hashes=record_hashes,
    )


def _feed_observation_path(paths: RegistryPaths, sequence: int) -> Path:
    return paths.feed_observations / f"{sequence:020d}.json"


def _feed_observation_head_payload(
    profile: Mapping[str, Any], event: Mapping[str, object]
) -> dict[str, object]:
    return {
        "schema_version": profile["protocol"]["feed_observation_head_schema_version"],
        "registry_profile_sha256": profile["_profile_sha256"],
        "sequence_id": event["sequence_id"],
        "entry_sha256": event["entry_sha256"],
        "feed_sha256": event["feed"]["sha256"],
    }


def _read_feed_observation_head(
    profile: Mapping[str, Any], paths: RegistryPaths
) -> dict[str, Any] | None:
    if (
        not paths.feed_observation_head.exists()
        and not paths.feed_observation_head.is_symlink()
    ):
        return None
    snapshot = _read_regular(
        paths.feed_observation_head,
        name="feed observation head",
        maximum_bytes=MAX_CONTROL_BYTES,
    )
    head = _decode_json(snapshot.raw, name="feed observation head")
    if snapshot.raw != _canonical_bytes(head):
        raise EpochRegistryIntegrityError("Feed observation head is not canonical")
    _exact_object(
        head,
        {
            "schema_version",
            "registry_profile_sha256",
            "sequence_id",
            "entry_sha256",
            "feed_sha256",
        },
        name="feed observation head",
    )
    if (
        head["schema_version"]
        != profile["protocol"]["feed_observation_head_schema_version"]
        or head["registry_profile_sha256"] != profile["_profile_sha256"]
    ):
        raise EpochRegistryIntegrityError("Feed observation head contract changed")
    _require_hash(head["entry_sha256"], name="feed observation head entry")
    _require_hash(head["feed_sha256"], name="feed observation head feed")
    return head


def replay_feed_observations(
    profile: Mapping[str, Any], paths: RegistryPaths
) -> tuple[tuple[dict[str, Any], ...], tuple[FeedSemantics, ...]]:
    if (
        not paths.feed_observations.exists()
        and not paths.feed_observations.is_symlink()
    ):
        if _read_feed_observation_head(profile, paths) is not None:
            raise EpochRegistryIntegrityError(
                "Feed observation head exists without its chain"
            )
        return (), ()
    if not stat.S_ISDIR(os.lstat(paths.feed_observations).st_mode):
        raise EpochRegistryIntegrityError(
            "Feed observation path is not a real directory"
        )
    entries = sorted(paths.feed_observations.iterdir(), key=lambda item: item.name)
    events: list[dict[str, Any]] = []
    semantics: list[FeedSemantics] = []
    previous_entry = ZERO_HASH
    seen_feed_hashes: set[str] = set()
    seen_revision_ids: dict[date, set[str]] = {}
    seen_record_hashes: dict[date, set[str]] = {}
    previous_feed: FeedSemantics | None = None
    for expected_sequence, path in enumerate(entries, start=1):
        if path.name != f"{expected_sequence:020d}.json":
            raise EpochRegistryIntegrityError(
                "Feed observation sequence has a gap or branch"
            )
        snapshot = _read_regular(
            path, name="feed observation", maximum_bytes=MAX_FEED_OBSERVATION_BYTES
        )
        event = _decode_json(snapshot.raw, name="feed observation")
        if snapshot.raw != _canonical_bytes(event):
            raise EpochRegistryIntegrityError("Feed observation is not canonical")
        _exact_object(
            event,
            {
                "schema_version",
                "registry_profile_sha256",
                "sequence_id",
                "feed",
                "previous_entry_sha256",
                "entry_sha256",
            },
            name="feed observation",
        )
        if (
            event["schema_version"]
            != profile["protocol"]["feed_observation_schema_version"]
            or event["registry_profile_sha256"] != profile["_profile_sha256"]
            or type(event["sequence_id"]) is not int
            or event["sequence_id"] != expected_sequence
            or event["previous_entry_sha256"] != previous_entry
        ):
            raise EpochRegistryIntegrityError("Feed observation contract changed")
        unsigned = {key: event[key] for key in event if key != "entry_sha256"}
        calculated = _sha256(_canonical_bytes(unsigned))
        if event["entry_sha256"] != calculated:
            raise EpochRegistryIntegrityError("Feed observation hash changed")
        feed_record = _exact_object(
            event["feed"],
            FEED_OBSERVATION_PAYLOAD_KEYS,
            name="observed feed payload",
        )
        feed_sha = _require_hash(feed_record["sha256"], name="observed feed sha256")
        feed_size = feed_record["size_bytes"]
        encoded = _nonempty_text(
            feed_record["raw_base64"], name="observed feed raw bytes"
        )
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise EpochRegistryIntegrityError(
                "Observed feed bytes are not canonical base64"
            ) from exc
        if (
            base64.b64encode(raw).decode("ascii") != encoded
            or type(feed_size) is not int
            or feed_size < 0
            or feed_size > MAX_FEED_BYTES
            or len(raw) != feed_size
            or _sha256(raw) != feed_sha
        ):
            raise EpochRegistryIntegrityError("Observed feed payload changed")
        feed_snapshot = ArtifactSnapshot(
            paths.objects / feed_sha, raw, feed_sha, feed_size
        )
        current = _feed_semantics(feed_snapshot)
        if current.sha256 in seen_feed_hashes:
            raise EpochRegistryIntegrityError("Feed was observed more than once")
        if previous_feed is not None:
            _assert_feed_successor(
                previous_feed,
                current,
                seen_revision_ids=seen_revision_ids,
                seen_record_hashes=seen_record_hashes,
            )
        _extend_feed_history(current, seen_revision_ids, seen_record_hashes)
        seen_feed_hashes.add(current.sha256)
        events.append(event)
        semantics.append(current)
        previous_feed = current
        previous_entry = calculated
    head = _read_feed_observation_head(profile, paths)
    if head is not None:
        sequence = head["sequence_id"]
        if type(sequence) is not int or sequence < 1 or sequence > len(events):
            raise EpochRegistryIntegrityError(
                "Feed observation head claims an unknown sequence"
            )
        expected = _feed_observation_head_payload(profile, events[sequence - 1])
        if head != expected:
            raise EpochRegistryIntegrityError(
                "Feed observation head conflicts with its event"
            )
    return tuple(events), tuple(semantics)


def _refresh_feed_observation_head(
    profile: Mapping[str, Any],
    paths: RegistryPaths,
    events: Sequence[Mapping[str, object]],
) -> None:
    if not events:
        return
    expected = _feed_observation_head_payload(profile, events[-1])
    head = _read_feed_observation_head(profile, paths)
    if head is not None:
        sequence = head["sequence_id"]
        if type(sequence) is not int or sequence > len(events):
            raise EpochRegistryIntegrityError(
                "Feed observation head cannot be rolled back"
            )
        if sequence == len(events) and head != expected:
            raise EpochRegistryIntegrityError(
                "Feed observation head conflicts with chain tip"
            )
    _atomic_cache(
        paths.feed_observation_head,
        _canonical_bytes(expected),
        root=paths.root,
        name="feed observation head",
    )


def _record_feed_observation(
    profile: Mapping[str, Any],
    paths: RegistryPaths,
    source_feed: ArtifactSnapshot,
    current: FeedSemantics,
    events: Sequence[Mapping[str, object]],
    semantics: Sequence[FeedSemantics],
) -> tuple[ArtifactSnapshot, tuple[dict[str, Any], ...], tuple[FeedSemantics, ...]]:
    if current.sha256 != source_feed.sha256:
        raise EpochRegistryIntegrityError("Prevalidated candidate feed bytes changed")
    if source_feed.size_bytes > MAX_FEED_BYTES:
        raise EpochRegistryIntegrityError(
            "Candidate feed exceeds the registry size limit"
        )
    if semantics:
        revision_ids: dict[date, set[str]] = {}
        record_hashes: dict[date, set[str]] = {}
        for observed in semantics:
            _extend_feed_history(observed, revision_ids, record_hashes)
        _assert_feed_successor(
            semantics[-1],
            current,
            seen_revision_ids=revision_ids,
            seen_record_hashes=record_hashes,
        )
    if semantics and current.sha256 == semantics[-1].sha256:
        feed_snapshot = _publish_once(
            paths.objects / source_feed.sha256,
            source_feed.raw,
            root=paths.root,
            name="candidate finalized feed snapshot",
        )
        return feed_snapshot, tuple(dict(event) for event in events), tuple(semantics)
    sequence = len(events) + 1
    unsigned: dict[str, object] = {
        "schema_version": profile["protocol"]["feed_observation_schema_version"],
        "registry_profile_sha256": profile["_profile_sha256"],
        "sequence_id": sequence,
        "feed": {
            "sha256": source_feed.sha256,
            "size_bytes": source_feed.size_bytes,
            "raw_base64": base64.b64encode(source_feed.raw).decode("ascii"),
        },
        "previous_entry_sha256": (events[-1]["entry_sha256"] if events else ZERO_HASH),
    }
    event = {**unsigned, "entry_sha256": _sha256(_canonical_bytes(unsigned))}
    _publish_once(
        _feed_observation_path(paths, sequence),
        _canonical_bytes(event),
        root=paths.root,
        name="feed observation",
    )
    replayed_events, replayed_semantics = replay_feed_observations(profile, paths)
    if len(replayed_events) != sequence or replayed_events[-1] != event:
        raise EpochRegistryIntegrityError(
            "Feed observation append did not become its tip"
        )
    _refresh_feed_observation_head(profile, paths, replayed_events)
    feed_snapshot = _publish_once(
        paths.objects / source_feed.sha256,
        source_feed.raw,
        root=paths.root,
        name="candidate finalized feed snapshot",
    )
    return feed_snapshot, replayed_events, replayed_semantics


def _ready_candidate_summary(
    receipt: Mapping[str, object], snapshot: ArtifactSnapshot
) -> dict[str, object]:
    build = receipt["candidate_build"]
    if not isinstance(build, dict):
        raise EpochRegistryIntegrityError("Candidate build summary changed")
    return {
        "slot_id": receipt["slot_id"],
        "candidate_id": receipt["candidate_id"],
        "live_epoch_id": build["live_epoch_id"],
        "watermark": build["watermark"],
        "candidate_receipt_sha256": snapshot.sha256,
    }


def _event_unsigned(value: Mapping[str, object]) -> dict[str, object]:
    return {key: value[key] for key in value if key != "entry_sha256"}


def _event_path(paths: RegistryPaths, sequence: int) -> Path:
    return paths.events / f"{sequence:020d}.json"


def _head_payload(
    profile: Mapping[str, Any], event: Mapping[str, object]
) -> dict[str, object]:
    return {
        "schema_version": profile["protocol"]["head_schema_version"],
        "registry_profile_sha256": profile["_profile_sha256"],
        "sequence_id": event["sequence_id"],
        "entry_sha256": event["entry_sha256"],
        "candidate_id": event["candidate_id"],
        "candidate_receipt": event["candidate_receipt"],
    }


def _read_head(
    profile: Mapping[str, Any], paths: RegistryPaths
) -> dict[str, Any] | None:
    if not paths.head.exists() and not paths.head.is_symlink():
        return None
    snapshot = _read_regular(
        paths.head, name="registry head", maximum_bytes=MAX_CONTROL_BYTES
    )
    head = _decode_json(snapshot.raw, name="registry head")
    if snapshot.raw != _canonical_bytes(head):
        raise EpochRegistryIntegrityError("Registry head is not canonical")
    _exact_object(
        head,
        {
            "schema_version",
            "registry_profile_sha256",
            "sequence_id",
            "entry_sha256",
            "candidate_id",
            "candidate_receipt",
        },
        name="registry head",
    )
    if (
        head["schema_version"] != profile["protocol"]["head_schema_version"]
        or head["registry_profile_sha256"] != profile["_profile_sha256"]
    ):
        raise EpochRegistryIntegrityError("Registry head contract changed")
    return head


def replay_registry(
    profile: Mapping[str, Any],
    paths: RegistryPaths,
    *,
    _observed_feed_semantics: Sequence[FeedSemantics] | None = None,
) -> tuple[dict[str, Any], ...]:
    if _observed_feed_semantics is None:
        _, observed_feed_semantics = replay_feed_observations(profile, paths)
    else:
        observed_feed_semantics = tuple(_observed_feed_semantics)
    observed_positions = {
        semantics.sha256: index
        for index, semantics in enumerate(observed_feed_semantics)
    }
    if not paths.events.exists() and not paths.events.is_symlink():
        if paths.head.exists() or paths.head.is_symlink():
            raise EpochRegistryIntegrityError(
                "Registry head exists without event chain"
            )
        return ()
    if not stat.S_ISDIR(os.lstat(paths.events).st_mode):
        raise EpochRegistryIntegrityError("Registry events path is not a directory")
    entries = sorted(paths.events.iterdir(), key=lambda item: item.name)
    events: list[dict[str, Any]] = []
    previous = ZERO_HASH
    candidate_ids: set[str] = set()
    slot_ids: set[str] = set()
    previous_feed: FeedSemantics | None = None
    seen_revision_ids: dict[date, set[str]] = {}
    seen_record_hashes: dict[date, set[str]] = {}
    previous_observation_position = -1
    for expected_sequence, path in enumerate(entries, start=1):
        if path.name != f"{expected_sequence:020d}.json":
            raise EpochRegistryIntegrityError(
                "Registry event sequence has a gap or branch"
            )
        snapshot = _read_regular(
            path, name="registry event", maximum_bytes=MAX_CONTROL_BYTES
        )
        event = _decode_json(snapshot.raw, name="registry event")
        if snapshot.raw != _canonical_bytes(event):
            raise EpochRegistryIntegrityError("Registry event is not canonical")
        _exact_object(
            event,
            {
                "schema_version",
                "registry_profile_sha256",
                "sequence_id",
                "event_type",
                "candidate_id",
                "slot_id",
                "candidate_receipt",
                "previous_entry_sha256",
                "entry_sha256",
                "active_epoch_selected",
                "automatic_epoch_rotation_implemented",
                "e2_live_evidence_eligible",
                "formal_warning_output",
            },
            name="registry event",
        )
        if (
            event["schema_version"] != profile["protocol"]["event_schema_version"]
            or event["registry_profile_sha256"] != profile["_profile_sha256"]
            or type(event["sequence_id"]) is not int
            or event["sequence_id"] != expected_sequence
            or event["event_type"] != "candidate_ready"
            or event["previous_entry_sha256"] != previous
            or event["active_epoch_selected"] is not False
            or event["automatic_epoch_rotation_implemented"] is not False
            or event["e2_live_evidence_eligible"] is not False
            or event["formal_warning_output"] is not False
        ):
            raise EpochRegistryIntegrityError("Registry event contract changed")
        calculated = _sha256(_canonical_bytes(_event_unsigned(event)))
        if event["entry_sha256"] != calculated:
            raise EpochRegistryIntegrityError("Registry event hash changed")
        receipt = _verify_receipt(
            profile,
            paths,
            event["candidate_receipt"],
        )
        feed_semantics = _receipt_feed_semantics(paths, receipt)
        observation_position = observed_positions.get(feed_semantics.sha256)
        if (
            observation_position is None
            or observation_position < previous_observation_position
        ):
            raise EpochRegistryIntegrityError(
                "Candidate feed is not ordered in the observation chain"
            )
        if previous_feed is not None:
            _assert_feed_successor(
                previous_feed,
                feed_semantics,
                seen_revision_ids=seen_revision_ids,
                seen_record_hashes=seen_record_hashes,
            )
        _extend_feed_history(feed_semantics, seen_revision_ids, seen_record_hashes)
        previous_feed = feed_semantics
        previous_observation_position = observation_position
        if (
            event["candidate_id"] != receipt["candidate_id"]
            or event["slot_id"] != receipt["slot_id"]
        ):
            raise EpochRegistryIntegrityError("Registry event receipt link changed")
        candidate_id = _require_hash(event["candidate_id"], name="event.candidate_id")
        slot_id = _require_hash(event["slot_id"], name="event.slot_id")
        if candidate_id in candidate_ids:
            raise EpochRegistryIntegrityError("Candidate appears twice in registry")
        if slot_id in slot_ids:
            raise EpochRegistryIntegrityError("Stable slot appears twice in registry")
        candidate_ids.add(candidate_id)
        slot_ids.add(slot_id)
        previous = calculated
        events.append(event)
    head = _read_head(profile, paths)
    if head is not None:
        if not events:
            raise EpochRegistryIntegrityError("Registry head exists without events")
        sequence = head["sequence_id"]
        if type(sequence) is not int or sequence < 1 or sequence > len(events):
            raise EpochRegistryIntegrityError(
                "Registry head claims an unknown sequence"
            )
        expected = _head_payload(profile, events[sequence - 1])
        if head != expected:
            raise EpochRegistryIntegrityError("Registry head conflicts with its event")
    return tuple(events)


def _append_candidate_event(
    profile: Mapping[str, Any],
    paths: RegistryPaths,
    events: Sequence[Mapping[str, object]],
    receipt_snapshot: ArtifactSnapshot,
    receipt: Mapping[str, object],
) -> dict[str, Any]:
    if events and events[-1]["candidate_id"] == receipt["candidate_id"]:
        return dict(events[-1])
    verified_receipt = _verify_receipt(
        profile,
        paths,
        {
            "path": str(receipt_snapshot.path.relative_to(paths.root)),
            "sha256": receipt_snapshot.sha256,
            "size_bytes": receipt_snapshot.size_bytes,
        },
        require_namespaces_empty=True,
        verify_current_artifacts=True,
    )
    if verified_receipt != receipt:
        raise EpochRegistryIntegrityError(
            "Candidate receipt changed before registry commit"
        )
    if any(event["candidate_id"] == receipt["candidate_id"] for event in events):
        raise EpochRegistryIntegrityError("Candidate would branch the registry")
    if any(event["slot_id"] == receipt["slot_id"] for event in events):
        raise EpochRegistryIntegrityError(
            "Stable slot already has different candidate semantics"
        )
    _assert_current_feed_follows_registry(
        profile,
        paths,
        events,
        _receipt_feed_semantics(paths, receipt),
    )
    sequence = len(events) + 1
    previous = events[-1]["entry_sha256"] if events else ZERO_HASH
    unsigned: dict[str, object] = {
        "schema_version": profile["protocol"]["event_schema_version"],
        "registry_profile_sha256": profile["_profile_sha256"],
        "sequence_id": sequence,
        "event_type": "candidate_ready",
        "candidate_id": receipt["candidate_id"],
        "slot_id": receipt["slot_id"],
        "candidate_receipt": {
            "path": str(receipt_snapshot.path.relative_to(paths.root)),
            "sha256": receipt_snapshot.sha256,
            "size_bytes": receipt_snapshot.size_bytes,
        },
        "previous_entry_sha256": previous,
        "active_epoch_selected": False,
        "automatic_epoch_rotation_implemented": False,
        "e2_live_evidence_eligible": False,
        "formal_warning_output": False,
    }
    event = {**unsigned, "entry_sha256": _sha256(_canonical_bytes(unsigned))}
    _publish_once(
        _event_path(paths, sequence),
        _canonical_bytes(event),
        root=paths.root,
        name="registry event",
    )
    return event


def _refresh_head(
    profile: Mapping[str, Any],
    paths: RegistryPaths,
    events: Sequence[Mapping[str, object]],
) -> None:
    if not events:
        return
    expected = _head_payload(profile, events[-1])
    head = _read_head(profile, paths)
    if head is not None:
        sequence = head["sequence_id"]
        if type(sequence) is not int or sequence > len(events):
            raise EpochRegistryIntegrityError("Registry head cannot be rolled back")
        if sequence == len(events) and head != expected:
            raise EpochRegistryIntegrityError("Registry head conflicts with chain tip")
    _atomic_cache(
        paths.head,
        _canonical_bytes(expected),
        root=paths.root,
        name="registry head",
    )


def _status_payload(
    profile: Mapping[str, Any],
    *,
    checked_at: datetime,
    status: str,
    reason: str,
    events: Sequence[Mapping[str, object]],
    feed_observation_count: int,
    candidate: Mapping[str, object] | None = None,
) -> dict[str, object]:
    head = events[-1] if events else None
    return {
        "schema_version": profile["protocol"]["status_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "checked_at_utc": checked_at.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "registry_status": status,
        "reason": reason,
        "feed_observation_count": feed_observation_count,
        "event_count": len(events),
        "terminal_entry_sha256": head["entry_sha256"] if head else ZERO_HASH,
        "latest_candidate_id": head["candidate_id"] if head else None,
        "candidate": candidate,
        "immutable_candidate_registry_implemented": True,
        "candidate_prebuild_implemented": True,
        "active_epoch_switch_implemented": False,
        "automatic_epoch_rotation_implemented": False,
        "trusted_anchor_receipt_verified": False,
        "e2_live_evidence_eligible": False,
        "real_activation_ready": False,
        "formal_warning_output": False,
    }


def _write_status(
    profile: Mapping[str, Any], paths: RegistryPaths, payload: dict[str, object]
) -> Path:
    _atomic_cache(
        paths.status,
        _canonical_bytes(payload),
        root=paths.root,
        name="registry status",
    )
    return paths.status


def _poll_epoch_registry(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    runtime_root: Path | None = None,
    project_root: Path = ROOT,
    clock: Callable[[], datetime] | None = None,
    _prebuilder: Prebuilder | None = None,
    _source_feed_path: Path | None = None,
) -> Path:
    if (
        _prebuilder is not None or _source_feed_path is not None
    ) and runtime_root is None:
        raise EpochRegistryIntegrityError(
            "Injected registry inputs are restricted to isolated test runtimes"
        )
    if runtime_root is not None:
        isolated_root = _absolute_lexical(
            runtime_root.parent.resolve(strict=False) / runtime_root.name
        )
        production_tree = _absolute_lexical(ROOT / "runtime")
        try:
            isolated_root.relative_to(production_tree)
        except ValueError:
            pass
        else:
            raise EpochRegistryIntegrityError(
                "Private runtime overrides cannot target the production runtime tree"
            )
    prebuilder = _prebuilder or _default_prebuilder
    profile = load_registry_profile(config_path, project_root=project_root)
    paths = registry_paths(
        profile,
        runtime_root=runtime_root,
        source_feed_path=_source_feed_path,
    )
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    if now.tzinfo is None or now.utcoffset() is None:
        raise EpochRegistryIntegrityError("Registry clock must be timezone-aware")
    _mkdir(paths.root, root=paths.root)
    lock = _acquire_manager_lock(paths)
    try:
        _cleanup_temporary_namespace(paths.root)
        observation_events, observed_feed_semantics = replay_feed_observations(
            profile, paths
        )
        _refresh_feed_observation_head(profile, paths, observation_events)
        events = replay_registry(
            profile,
            paths,
            _observed_feed_semantics=observed_feed_semantics,
        )
        _refresh_head(profile, paths, events)
        if not paths.source_feed.exists() and not paths.source_feed.is_symlink():
            return _write_status(
                profile,
                paths,
                _status_payload(
                    profile,
                    checked_at=now,
                    status="waiting_for_candidate_feed",
                    reason="candidate finalized feed is absent",
                    events=events,
                    feed_observation_count=len(observation_events),
                ),
            )
        feed = _read_regular(
            paths.source_feed,
            name="candidate finalized feed",
            maximum_bytes=MAX_FEED_BYTES,
        )
        validation_now = now if _prebuilder is not None else datetime.now(timezone.utc)
        current_feed_semantics = _feed_semantics(feed, now=validation_now)
        feed_snapshot, observation_events, observed_feed_semantics = (
            _record_feed_observation(
                profile,
                paths,
                feed,
                current_feed_semantics,
                observation_events,
                observed_feed_semantics,
            )
        )
        if observed_feed_semantics[-1].sha256 != current_feed_semantics.sha256:
            raise EpochRegistryIntegrityError(
                "Current feed did not become the observation tip"
            )
        _assert_current_feed_follows_registry(
            profile, paths, events, current_feed_semantics
        )
        capsule_manifest, capsule = _capture_capsule(profile, paths)
        slot_identity = {
            "domain": profile["protocol"]["slot_identity_domain"],
            "registry_profile_sha256": profile["_profile_sha256"],
            "feed_sha256": feed.sha256,
            "capsule_tree_sha256": capsule_manifest["tree_sha256"],
        }
        slot_id = _sha256(_canonical_bytes(slot_identity))
        existing = _find_current_slot_candidate(profile, paths, events, slot_id=slot_id)
        if existing is not None:
            existing_receipt, existing_snapshot = existing
            event = _append_candidate_event(
                profile,
                paths,
                events,
                existing_snapshot,
                existing_receipt,
            )
            events = replay_registry(profile, paths)
            if events[-1] != event:
                raise EpochRegistryIntegrityError(
                    "Recovered candidate did not become the registry tip"
                )
            _refresh_head(profile, paths, events)
            return _write_status(
                profile,
                paths,
                _status_payload(
                    profile,
                    checked_at=now,
                    status="immutable_candidate_record_ready",
                    reason=(
                        "immutable candidate record verified; mutable slot lifecycle "
                        "is outside R1"
                    ),
                    events=events,
                    feed_observation_count=len(observation_events),
                    candidate=_ready_candidate_summary(
                        existing_receipt, existing_snapshot
                    ),
                ),
            )
        live_root = paths.slots / slot_id / "live"
        _mkdir(live_root, root=paths.root)
        feed_copy_path = live_root / profile["candidate"]["feed_relative_path"]
        feed_copy = _publish_once(
            feed_copy_path,
            feed.raw,
            root=paths.root,
            name="candidate feed copy",
        )
        build = prebuilder(profile, paths, live_root, now, feed_copy.path)
        if build is None:
            return _write_status(
                profile,
                paths,
                _status_payload(
                    profile,
                    checked_at=now,
                    status="waiting_for_candidate_source",
                    reason="candidate source is not ready for a bundle",
                    events=events,
                    feed_observation_count=len(observation_events),
                    candidate={"slot_id": slot_id},
                ),
            )
        build_payload = _validate_build(
            profile, build, live_root=live_root, expected_feed=feed_snapshot
        )
        if (
            build.watermark != current_feed_semantics.watermark.isoformat()
            or build.outcome_source_id != current_feed_semantics.outcome_source_id
        ):
            raise EpochRegistryIntegrityError(
                "Candidate prebuild does not match finalized feed semantics"
            )
        _assert_forbidden_namespaces_empty(profile, live_root)
        postbuild_manifest, postbuild_capsule = _capture_capsule(profile, paths)
        if (
            postbuild_manifest != capsule_manifest
            or postbuild_capsule.sha256 != capsule.sha256
            or postbuild_capsule.raw != capsule.raw
        ):
            raise EpochRegistryIntegrityError(
                "Reviewed worktree bindings changed during candidate prebuild"
            )
        candidate_artifact_snapshots = _capture_candidate_artifacts(
            paths, build_payload
        )
        receipt = _receipt_payload(
            profile,
            paths,
            slot_id=slot_id,
            live_root=live_root,
            feed_copy_path=feed_copy.path,
            feed_snapshot=feed_snapshot,
            capsule=capsule,
            build=build_payload,
            candidate_artifact_snapshots=candidate_artifact_snapshots,
        )
        receipt_raw = _canonical_bytes(receipt)
        receipt_snapshot = _publish_once(
            paths.candidate_receipts / f"{_sha256(receipt_raw)}.json",
            receipt_raw,
            root=paths.root,
            name="candidate receipt",
        )
        _verify_receipt(
            profile,
            paths,
            {
                "path": str(receipt_snapshot.path.relative_to(paths.root)),
                "sha256": receipt_snapshot.sha256,
                "size_bytes": receipt_snapshot.size_bytes,
            },
            require_namespaces_empty=True,
            verify_current_artifacts=True,
        )
        event = _append_candidate_event(
            profile, paths, events, receipt_snapshot, receipt
        )
        events = replay_registry(profile, paths)
        if events[-1] != event:
            raise EpochRegistryIntegrityError("Registry append did not become its tip")
        _refresh_head(profile, paths, events)
        return _write_status(
            profile,
            paths,
            _status_payload(
                profile,
                checked_at=now,
                status="immutable_candidate_record_ready",
                reason=(
                    "candidate committed from verified current artifacts; later mutable "
                    "slot lifecycle is outside R1"
                ),
                events=events,
                feed_observation_count=len(observation_events),
                candidate=_ready_candidate_summary(receipt, receipt_snapshot),
            ),
        )
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


def poll_epoch_registry(*, config_path: Path = DEFAULT_CONFIG_PATH) -> Path:
    """Run the production registry with only its reviewed profile and machine inputs."""

    return _poll_epoch_registry(config_path=config_path)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        status_path = poll_epoch_registry(config_path=args.config)
    except EpochRegistryBusyError as exc:
        print(f"[ootang-epoch-registry] busy: {exc}", file=sys.stderr)
        return 3
    except EpochRegistryError as exc:
        print(f"[ootang-epoch-registry] blocked: {exc}", file=sys.stderr)
        return 2
    print(f"[ootang-epoch-registry] status: {status_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
