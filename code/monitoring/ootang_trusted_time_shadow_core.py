"""Acquire and replay a pinned RFC 3161 trusted-time receipt in shadow mode.

This additive stage timestamps only a completed replay-gated live issue seal.  It
does not modify the reviewed live/verified-live protocols, read an outcome inbox,
authorize E2 evidence, activate a model, or emit a formal warning.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import fcntl
import hashlib
from importlib.metadata import PackageNotFoundError, version as package_version
import json
import math
import os
from pathlib import Path, PurePosixPath
import secrets
import signal
import stat
import sys
import tempfile
import time as time_module
from typing import Any
import urllib.error
import urllib.request

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import ExtensionOID, ExtendedKeyUsageOID, ObjectIdentifier
from rfc3161_client import VerifierBuilder, decode_timestamp_response


ROOT = Path(__file__).resolve().parents[2]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_prequential_live as live  # noqa: E402
from monitoring import ootang_verified_live as verified_live  # noqa: E402


DEFAULT_CONFIG_PATH = ROOT / "config" / "ootang_trusted_time_shadow.v1.json"
SHA256_OID = "2.16.840.1.101.3.4.2.1"
TIMESTAMPING_EKU_OID = "1.3.6.1.5.5.7.3.8"
HEX_DIGITS = frozenset("0123456789abcdef")
MAX_CONTROL_BYTES = 1024 * 1024
SCRATCH_DIRECTORY = ".trusted_time_create_scratch"
REQUIRED_PYTHON_VERSION = (3, 10, 20)
TRUST_MANIFEST_SHA256 = (
    "33d22cc6dbdf8bf016b0cb96e291ca4538109ffb5d22a639edb34d2f42c80eef"
)
TRUST_LEAF_PEM_SHA256 = (
    "366bf80f560983b54049de0329485edda59c1c92606fbe507529355f194f2bdf"
)
TRUST_LEAF_DER_SHA256 = (
    "85f927bc07ab62cac3b44356c10efc81b2c6883fda7ab9e6d870d9d13acd05b7"
)
TRUST_ROOT_PEM_SHA256 = (
    "bf6960b216d500905b7f71129be406a60a38a3dd34eea82fad5b36cc22dbb03f"
)
TRUST_ROOT_DER_SHA256 = (
    "2aca8fea5d3ce48b01cc77076293c280e6c23ffe44034757ee7833ca9f45d633"
)


class TrustedTimeError(RuntimeError):
    """Base trusted-time error."""


class TrustedTimeConfigError(TrustedTimeError):
    """A versioned profile, dependency, or trust input changed."""


class TrustedTimeIntegrityError(TrustedTimeError):
    """An immutable artifact or cryptographic claim failed closed."""


class TrustedTimeNetworkError(TrustedTimeError):
    """The fixed timestamp service is temporarily unavailable."""


class TrustedTimeBusyError(TrustedTimeError):
    """Another process owns the shared live runner lock."""


@dataclass(frozen=True)
class ArtifactSnapshot:
    path: Path
    raw: bytes
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class TrustedTimePaths:
    root: Path
    status: Path
    requests: Path
    request_der: Path
    objects: Path
    response_links: Path
    receipts: Path
    runner_lock: Path
    live_profile: Path
    verified_live_profile: Path
    trust_manifest: Path
    leaf: Path
    root_certificate: Path


@dataclass(frozen=True)
class TrustMaterial:
    manifest_sha256: str
    leaf: x509.Certificate
    root: x509.Certificate
    leaf_pem_sha256: str
    root_pem_sha256: str
    leaf_der_sha256: str
    root_der_sha256: str


@dataclass(frozen=True)
class Candidate:
    target: date
    envelope: dict[str, object]


@dataclass(frozen=True)
class TransportResponse:
    body: bytes
    status: int
    media_type: str
    content_encoding: str | None
    final_url: str


@dataclass(frozen=True)
class RequestArtifacts:
    record: dict[str, Any]
    record_snapshot: ArtifactSnapshot
    der_snapshot: ArtifactSnapshot
    message: bytes
    nonce: int


Transport = Callable[[str, bytes, float, int], TransportResponse]
Clock = Callable[[], datetime]
Monotonic = Callable[[], float]
NonceFactory = Callable[[], int]


def _reject_constant(value: str) -> None:
    raise TrustedTimeIntegrityError(f"Forbidden JSON constant: {value}")


def _pairs_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise TrustedTimeIntegrityError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _decode_json(raw: bytes, *, name: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            parse_constant=_reject_constant,
            object_pairs_hook=_pairs_object,
        )
    except TrustedTimeIntegrityError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TrustedTimeIntegrityError(f"{name} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise TrustedTimeIntegrityError(f"{name} must be a JSON object")
    return value


def _canonical_bytes(value: object) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
    except (TypeError, ValueError) as exc:
        raise TrustedTimeIntegrityError("Value is not canonical finite JSON") from exc


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _require_exact_keys(
    value: object, expected: set[str], *, name: str
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise TrustedTimeIntegrityError(f"{name} keys changed")
    return value


def _require_text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise TrustedTimeIntegrityError(f"{name} must be non-empty canonical text")
    return value


def _require_sha256(value: object, *, name: str) -> str:
    text = _require_text(value, name=name)
    if len(text) != 64 or any(character not in HEX_DIGITS for character in text):
        raise TrustedTimeIntegrityError(f"{name} must be lowercase SHA-256")
    return text


def _require_false(value: object, *, name: str) -> None:
    if value is not False:
        raise TrustedTimeIntegrityError(f"{name} must remain false")


def _project_path(value: object, *, project_root: Path, name: str) -> Path:
    text = _require_text(value, name=name)
    pure = PurePosixPath(text)
    if pure.is_absolute() or ".." in pure.parts or "." in pure.parts:
        raise TrustedTimeConfigError(f"{name} must be a safe project-relative path")
    candidate = project_root.resolve() / Path(*pure.parts)
    try:
        candidate.resolve(strict=False).relative_to(project_root.resolve())
    except (OSError, RuntimeError, ValueError) as exc:
        raise TrustedTimeConfigError(f"{name} escaped the project root") from exc
    return candidate


def _runtime_child(root: Path, value: object, *, name: str) -> Path:
    text = _require_text(value, name=name)
    pure = PurePosixPath(text)
    if (
        pure.is_absolute()
        or not pure.parts
        or pure.as_posix() != text
        or any(part in {"", ".", ".."} for part in pure.parts)
        or "\\" in text
    ):
        raise TrustedTimeConfigError(f"{name} must be a safe relative path")
    candidate = root
    for part in pure.parts:
        candidate /= part
        try:
            if candidate.is_symlink():
                raise TrustedTimeConfigError(f"{name} contains a symlink component")
        except OSError as exc:
            raise TrustedTimeConfigError(f"{name} cannot be inspected") from exc
    try:
        candidate.resolve(strict=False).relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError) as exc:
        raise TrustedTimeConfigError(f"{name} escaped the runtime root") from exc
    return candidate


def _ensure_directory(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
        metadata = path.lstat()
    except OSError as exc:
        raise TrustedTimeIntegrityError(
            f"Cannot create runtime directory {path}"
        ) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise TrustedTimeIntegrityError(
            f"Runtime directory is not a real directory: {path}"
        )


def _read_regular(
    path: Path, *, name: str, maximum_bytes: int = MAX_CONTROL_BYTES
) -> ArtifactSnapshot:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise TrustedTimeIntegrityError(f"{name} cannot be opened safely") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum_bytes:
            raise TrustedTimeIntegrityError(f"{name} is not a bounded regular file")
        chunks: list[bytes] = []
        remaining = maximum_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > maximum_bytes:
            raise TrustedTimeIntegrityError(f"{name} exceeds its size limit")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        pathname = path.lstat()
    except OSError as exc:
        raise TrustedTimeIntegrityError(f"{name} pathname changed during read") from exc
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after or (pathname.st_dev, pathname.st_ino) != (
        after.st_dev,
        after.st_ino,
    ):
        raise TrustedTimeIntegrityError(f"{name} changed during read")
    return ArtifactSnapshot(path, raw, _sha256(raw), len(raw))


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_create_only(path: Path, raw: bytes, *, name: str) -> ArtifactSnapshot:
    _ensure_directory(path.parent)
    try:
        existing = _read_regular(path, name=name, maximum_bytes=max(len(raw), 1))
    except FileNotFoundError:
        existing = None
    if existing is not None:
        if existing.raw != raw:
            raise TrustedTimeIntegrityError(f"{name} changed immutable bytes")
        return existing
    scratch = path.parent / SCRATCH_DIRECTORY
    _ensure_directory(scratch)
    temporary = scratch / f"{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    try:
        descriptor = os.open(temporary, flags, 0o600)
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short immutable artifact write")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            existing = _read_regular(path, name=name, maximum_bytes=max(len(raw), 1))
            if existing.raw != raw:
                raise TrustedTimeIntegrityError(f"Concurrent {name} changed bytes")
        _fsync_directory(path.parent)
    except TrustedTimeIntegrityError:
        raise
    except OSError as exc:
        raise TrustedTimeIntegrityError(f"Cannot publish immutable {name}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
    return _read_regular(path, name=name, maximum_bytes=max(len(raw), 1))


def _atomic_status(path: Path, raw: bytes) -> None:
    _ensure_directory(path.parent)
    try:
        if path.is_symlink():
            raise TrustedTimeIntegrityError("Trusted-time status is a symlink")
    except OSError as exc:
        raise TrustedTimeIntegrityError(
            "Trusted-time status cannot be inspected"
        ) from exc
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
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _utc_text(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _clock_value(clock: Clock | None = None) -> datetime:
    try:
        value = (clock or (lambda: datetime.now(timezone.utc)))()
    except Exception as exc:
        raise TrustedTimeIntegrityError("Machine clock failed") from exc
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise TrustedTimeIntegrityError("Machine clock must be timezone-aware")
    return value.astimezone(timezone.utc)


def _parse_utc(value: object, *, name: str) -> datetime:
    text = _require_text(value, name=name)
    if not text.endswith("Z"):
        raise TrustedTimeIntegrityError(f"{name} must be UTC Z time")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise TrustedTimeIntegrityError(f"{name} is not a valid UTC time") from exc
    return parsed.astimezone(timezone.utc)


def _artifact_reference(snapshot: ArtifactSnapshot, root: Path) -> dict[str, object]:
    try:
        relative = snapshot.path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, RuntimeError, ValueError) as exc:
        raise TrustedTimeIntegrityError("Artifact escaped the runtime root") from exc
    return {
        "path": relative,
        "sha256": snapshot.sha256,
        "size_bytes": snapshot.size_bytes,
    }


def _load_reference(
    value: object,
    *,
    root: Path,
    name: str,
    maximum_bytes: int = MAX_CONTROL_BYTES,
) -> ArtifactSnapshot:
    record = _require_exact_keys(value, {"path", "sha256", "size_bytes"}, name=name)
    path = _runtime_child(root, record["path"], name=f"{name}.path")
    expected_sha = _require_sha256(record["sha256"], name=f"{name}.sha256")
    size = record["size_bytes"]
    if type(size) is not int or size < 0 or size > maximum_bytes:
        raise TrustedTimeIntegrityError(f"{name}.size_bytes is invalid")
    snapshot = _read_regular(path, name=name, maximum_bytes=maximum_bytes)
    if snapshot.sha256 != expected_sha or snapshot.size_bytes != size:
        raise TrustedTimeIntegrityError(f"{name} reference changed")
    return snapshot


def load_trusted_time_profile(
    path: Path = DEFAULT_CONFIG_PATH, *, project_root: Path = ROOT
) -> dict[str, Any]:
    if (
        sys.implementation.name != "cpython"
        or tuple(sys.version_info[:3]) != REQUIRED_PYTHON_VERSION
    ):
        raise TrustedTimeConfigError("Trusted-time core requires exact CPython 3.10.20")
    try:
        snapshot = _read_regular(path.resolve(), name="trusted-time profile")
        profile = _decode_json(snapshot.raw, name="trusted-time profile")
    except TrustedTimeIntegrityError as exc:
        raise TrustedTimeConfigError(str(exc)) from exc
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
        "verified_live_profile",
        "implementation",
        "trust_manifest",
        "trust",
        "rfc3161",
        "runtime",
        "protocol",
        "engineering_capabilities",
    }
    try:
        _require_exact_keys(profile, expected_top, name="trusted-time profile")
        if (
            profile["schema_version"] != "ootang_trusted_time_shadow_profile_v1"
            or profile["profile_id"] != "ootang-trusted-time-shadow-v1"
            or profile["profile_version"] != "1.0.0-engineering"
            or profile["case"] != "ootang"
            or profile["artifact_status"]
            != "rfc3161_trusted_time_shadow_engineering_only_not_live_evidence"
        ):
            raise TrustedTimeIntegrityError("Trusted-time profile identity changed")
        for flag in (
            "formal_warning_output",
            "independent_label_used",
            "confirmatory_external_validation",
            "vajont_used",
            "default_pipeline_member",
        ):
            _require_false(profile[flag], name=flag)
        bindings = {
            "live_profile": "bf7c60a19e26e9a54fc4e1980b3556d6e6d1e3fec4b3a3a7f3de0dbb9b83cf00",
            "verified_live_profile": "081af2dfd4b95f28b750d915a2ff74d508381e62f5d539aaaaa62b8add44992b",
        }
        for key, reviewed_sha in bindings.items():
            record = _require_exact_keys(
                profile[key], {"path", "expected_sha256"}, name=key
            )
            expected_sha = _require_sha256(
                record["expected_sha256"], name=f"{key}.expected_sha256"
            )
            if expected_sha != reviewed_sha:
                raise TrustedTimeIntegrityError(f"{key} reviewed binding changed")
            bound = _project_path(
                record["path"], project_root=project_root, name=f"{key}.path"
            )
            if _read_regular(bound, name=key).sha256 != expected_sha:
                raise TrustedTimeIntegrityError(f"{key} file changed")
        implementation = _require_exact_keys(
            profile["implementation"],
            {
                "launcher_path",
                "launcher_sha256",
                "core_path",
                "core_sha256",
                "runtime_project_path",
                "runtime_project_sha256",
                "runtime_lock_path",
                "runtime_lock_sha256",
                "required_uv_version",
                "required_python_version",
            },
            name="implementation",
        )
        if (
            implementation["required_uv_version"] != "0.12.5"
            or implementation["required_python_version"] != "3.10.20"
        ):
            raise TrustedTimeIntegrityError("Trusted-time uv or Python version changed")
        implementation_bindings = (
            ("launcher", "launcher_path", "launcher_sha256"),
            ("core", "core_path", "core_sha256"),
            ("runtime project", "runtime_project_path", "runtime_project_sha256"),
            ("runtime lock", "runtime_lock_path", "runtime_lock_sha256"),
        )
        for artifact_name, path_key, sha_key in implementation_bindings:
            artifact_path = _project_path(
                implementation[path_key],
                project_root=project_root,
                name=f"implementation.{path_key}",
            )
            expected_artifact_sha = _require_sha256(
                implementation[sha_key], name=f"implementation.{sha_key}"
            )
            if (
                _read_regular(
                    artifact_path, name=f"trusted-time {artifact_name}"
                ).sha256
                != expected_artifact_sha
            ):
                raise TrustedTimeIntegrityError(f"Trusted-time {artifact_name} changed")
        manifest_binding = _require_exact_keys(
            profile["trust_manifest"],
            {"path", "expected_sha256"},
            name="trust_manifest",
        )
        _require_sha256(
            manifest_binding["expected_sha256"], name="trust_manifest.expected_sha256"
        )
        if (
            manifest_binding["path"]
            != "config/trust/sigstore_tsa_2025_manifest.v1.json"
            or manifest_binding["expected_sha256"] != TRUST_MANIFEST_SHA256
        ):
            raise TrustedTimeIntegrityError("Reviewed trust manifest binding changed")
        trust = _require_exact_keys(
            profile["trust"],
            {
                "leaf_path",
                "leaf_pem_sha256",
                "leaf_der_sha256",
                "root_path",
                "root_pem_sha256",
                "root_der_sha256",
            },
            name="trust",
        )
        for key in (
            "leaf_pem_sha256",
            "leaf_der_sha256",
            "root_pem_sha256",
            "root_der_sha256",
        ):
            _require_sha256(trust[key], name=f"trust.{key}")
        expected_trust = {
            "leaf_path": "config/trust/sigstore_tsa_2025_leaf.pem",
            "leaf_pem_sha256": TRUST_LEAF_PEM_SHA256,
            "leaf_der_sha256": TRUST_LEAF_DER_SHA256,
            "root_path": "config/trust/sigstore_tsa_2025_root.pem",
            "root_pem_sha256": TRUST_ROOT_PEM_SHA256,
            "root_der_sha256": TRUST_ROOT_DER_SHA256,
        }
        if trust != expected_trust:
            raise TrustedTimeIntegrityError("Reviewed TSA trust binding changed")
        rfc = _require_exact_keys(
            profile["rfc3161"],
            {
                "provider",
                "endpoint",
                "request_media_type",
                "response_media_type",
                "policy_oid",
                "message_imprint_algorithm_oid",
                "message_imprint_algorithm",
                "nonce_bits",
                "cert_req",
                "accepted_pki_status",
                "provider_epoch_not_before_utc",
                "target_timezone",
                "target_utc_offset",
                "maximum_accuracy_seconds",
                "maximum_network_round_trip_seconds",
                "network_timeout_seconds",
                "maximum_response_bytes",
                "wall_clock_advisory_limit_seconds",
                "redirects_allowed",
                "compressed_responses_allowed",
                "required_dependency",
                "required_dependency_version",
            },
            name="rfc3161",
        )
        expected_rfc = {
            "provider": "sigstore-production-tsa",
            "endpoint": "https://timestamp.sigstore.dev/api/v1/timestamp",
            "request_media_type": "application/timestamp-query",
            "response_media_type": "application/timestamp-reply",
            "policy_oid": "1.3.6.1.4.1.57264.2",
            "message_imprint_algorithm_oid": SHA256_OID,
            "message_imprint_algorithm": "sha256",
            "nonce_bits": 256,
            "cert_req": True,
            "accepted_pki_status": 0,
            "provider_epoch_not_before_utc": "2025-07-04T00:00:00Z",
            "target_timezone": "Asia/Shanghai",
            "target_utc_offset": "+08:00",
            "maximum_accuracy_seconds": 1.0,
            "maximum_network_round_trip_seconds": 10.0,
            "network_timeout_seconds": 10.0,
            "maximum_response_bytes": 65536,
            "wall_clock_advisory_limit_seconds": 300.0,
            "redirects_allowed": False,
            "compressed_responses_allowed": False,
            "required_dependency": "rfc3161-client",
            "required_dependency_version": "1.0.8",
        }
        if rfc != expected_rfc:
            raise TrustedTimeIntegrityError("RFC 3161 contract changed")
        runtime = _require_exact_keys(
            profile["runtime"],
            {
                "root",
                "status",
                "requests",
                "request_der",
                "objects",
                "response_links",
                "receipts",
                "runner_lock",
            },
            name="runtime",
        )
        for key, value in runtime.items():
            _require_text(value, name=f"runtime.{key}")
        expected_runtime = {
            "root": "runtime/ootang_prequential_live_v1",
            "status": "trusted_time_shadow_status.json",
            "requests": "trusted_time_shadow_requests",
            "request_der": "trusted_time_shadow_request_der",
            "objects": "trusted_time_shadow_objects/sha256",
            "response_links": "trusted_time_shadow_response_links",
            "receipts": "trusted_time_shadow_receipts",
            "runner_lock": "runner.lock",
        }
        if runtime != expected_runtime:
            raise TrustedTimeIntegrityError("Trusted-time runtime namespace changed")
        protocol = _require_exact_keys(
            profile["protocol"],
            {
                "status_schema_version",
                "request_schema_version",
                "response_link_schema_version",
                "receipt_schema_version",
                "evidence_envelope_schema_version",
                "domain_separator",
                "canonical_json",
                "same_target_same_semantics",
                "same_target_changed_semantics",
                "require_verified_live_completion_for_outstanding_issue",
                "outcome_read",
                "activation_integration",
            },
            name="protocol",
        )
        expected_protocol = {
            "status_schema_version": "ootang_trusted_time_shadow_status_v1",
            "request_schema_version": "ootang_trusted_time_shadow_request_v1",
            "response_link_schema_version": "ootang_trusted_time_shadow_response_link_v1",
            "receipt_schema_version": "ootang_trusted_time_shadow_receipt_v1",
            "evidence_envelope_schema_version": "ootang_trusted_time_evidence_envelope_v1",
            "domain_separator": "org.ootang.landslide-warning/trusted-time-shadow/rfc3161/v1",
            "canonical_json": "utf8_sort_keys_compact_no_nan_trailing_lf",
            "same_target_same_semantics": "idempotent_preserve_first_bytes",
            "same_target_changed_semantics": "blocked_integrity",
            "require_verified_live_completion_for_outstanding_issue": True,
            "outcome_read": False,
            "activation_integration": "not_implemented_additive_shadow_only",
        }
        if protocol != expected_protocol:
            raise TrustedTimeIntegrityError("Trusted-time protocol changed")
        expected_capabilities = {
            "rfc3161_request_and_cryptographic_verification_implemented": True,
            "cryptographic_time_shadow_verified_by_receipt_only": True,
            "trusted_anchor_receipt_verified": False,
            "automatic_epoch_rotation_implemented": False,
            "automatic_calibration_promotion_implemented": False,
            "e2_live_evidence_eligible": False,
            "real_activation_ready": False,
        }
        if profile["engineering_capabilities"] != expected_capabilities:
            raise TrustedTimeIntegrityError("Trusted-time capability boundary changed")
    except TrustedTimeIntegrityError as exc:
        raise TrustedTimeConfigError(str(exc)) from exc
    profile["_profile_sha256"] = snapshot.sha256
    profile["_profile_path"] = str(path.resolve())
    profile["_project_root"] = str(project_root.resolve())
    return profile


def trusted_time_paths(
    profile: Mapping[str, Any], *, runtime_root: Path | None = None
) -> TrustedTimePaths:
    project_root = Path(str(profile["_project_root"]))
    root = (
        runtime_root.resolve()
        if runtime_root is not None
        else _project_path(
            profile["runtime"]["root"], project_root=project_root, name="runtime.root"
        ).resolve()
    )
    return TrustedTimePaths(
        root=root,
        status=_runtime_child(
            root, profile["runtime"]["status"], name="runtime.status"
        ),
        requests=_runtime_child(
            root, profile["runtime"]["requests"], name="runtime.requests"
        ),
        request_der=_runtime_child(
            root, profile["runtime"]["request_der"], name="runtime.request_der"
        ),
        objects=_runtime_child(
            root, profile["runtime"]["objects"], name="runtime.objects"
        ),
        response_links=_runtime_child(
            root, profile["runtime"]["response_links"], name="runtime.response_links"
        ),
        receipts=_runtime_child(
            root, profile["runtime"]["receipts"], name="runtime.receipts"
        ),
        runner_lock=_runtime_child(
            root, profile["runtime"]["runner_lock"], name="runtime.runner_lock"
        ),
        live_profile=_project_path(
            profile["live_profile"]["path"],
            project_root=project_root,
            name="live_profile.path",
        ),
        verified_live_profile=_project_path(
            profile["verified_live_profile"]["path"],
            project_root=project_root,
            name="verified_live_profile.path",
        ),
        trust_manifest=_project_path(
            profile["trust_manifest"]["path"],
            project_root=project_root,
            name="trust_manifest.path",
        ),
        leaf=_project_path(
            profile["trust"]["leaf_path"],
            project_root=project_root,
            name="trust.leaf_path",
        ),
        root_certificate=_project_path(
            profile["trust"]["root_path"],
            project_root=project_root,
            name="trust.root_path",
        ),
    )


def _check_dependency(profile: Mapping[str, Any]) -> str:
    name = profile["rfc3161"]["required_dependency"]
    expected = profile["rfc3161"]["required_dependency_version"]
    try:
        actual = package_version(name)
    except PackageNotFoundError as exc:
        raise TrustedTimeConfigError(f"Required dependency {name} is missing") from exc
    if actual != expected:
        raise TrustedTimeConfigError(
            f"Required dependency {name} changed from {expected} to {actual}"
        )
    return actual


def load_trust_material(
    profile: Mapping[str, Any], paths: TrustedTimePaths
) -> TrustMaterial:
    manifest_snapshot = _read_regular(paths.trust_manifest, name="trust manifest")
    expected_manifest_sha = profile["trust_manifest"]["expected_sha256"]
    if manifest_snapshot.sha256 != expected_manifest_sha:
        raise TrustedTimeConfigError("Trust manifest changed")
    manifest = _decode_json(manifest_snapshot.raw, name="trust manifest")
    if (
        manifest.get("schema_version") != "ootang-rfc3161-trust-bundle-v1"
        or manifest.get("provider") != profile["rfc3161"]["provider"]
        or manifest.get("endpoint") != profile["rfc3161"]["endpoint"]
        or manifest.get("policy_oid") != profile["rfc3161"]["policy_oid"]
        or manifest.get("provider_epoch_not_before_utc")
        != profile["rfc3161"]["provider_epoch_not_before_utc"]
        or manifest.get("scope") != "engineering_trust_input_only"
        or manifest.get("formal_warning_output") is not False
        or manifest.get("e2_live_evidence_eligible") is not False
    ):
        raise TrustedTimeConfigError("Trust manifest identity changed")
    upstream = manifest.get("upstream")
    if (
        not isinstance(upstream, dict)
        or upstream.get("commit") != "ba3066c420970c13772ba0625f09f1ec97193116"
    ):
        raise TrustedTimeConfigError("Trust manifest upstream commit changed")
    leaf_snapshot = _read_regular(paths.leaf, name="pinned TSA leaf")
    root_snapshot = _read_regular(paths.root_certificate, name="pinned TSA root")
    trust = profile["trust"]
    if (
        leaf_snapshot.sha256 != trust["leaf_pem_sha256"]
        or root_snapshot.sha256 != trust["root_pem_sha256"]
    ):
        raise TrustedTimeConfigError("Pinned TSA PEM bytes changed")
    try:
        leaf = x509.load_pem_x509_certificate(leaf_snapshot.raw)
        root = x509.load_pem_x509_certificate(root_snapshot.raw)
    except ValueError as exc:
        raise TrustedTimeConfigError(
            "Pinned TSA certificate cannot be decoded"
        ) from exc
    leaf_der_sha = leaf.fingerprint(hashes.SHA256()).hex()
    root_der_sha = root.fingerprint(hashes.SHA256()).hex()
    if (
        leaf_der_sha != trust["leaf_der_sha256"]
        or root_der_sha != trust["root_der_sha256"]
    ):
        raise TrustedTimeConfigError("Pinned TSA DER fingerprint changed")
    try:
        leaf.verify_directly_issued_by(root)
        root.verify_directly_issued_by(root)
    except (TypeError, ValueError) as exc:
        raise TrustedTimeConfigError(
            "Pinned TSA leaf/root signature relationship changed"
        ) from exc
    manifest_leaf = manifest.get("leaf")
    manifest_root = manifest.get("root")
    if (
        not isinstance(manifest_leaf, dict)
        or not isinstance(manifest_root, dict)
        or manifest_leaf.get("pem_sha256") != leaf_snapshot.sha256
        or manifest_leaf.get("der_sha256") != leaf_der_sha
        or manifest_root.get("pem_sha256") != root_snapshot.sha256
        or manifest_root.get("der_sha256") != root_der_sha
        or manifest_leaf.get("extended_key_usage") != TIMESTAMPING_EKU_OID
    ):
        raise TrustedTimeConfigError("Trust manifest certificate binding changed")
    try:
        eku_extension = leaf.extensions.get_extension_for_oid(
            ExtensionOID.EXTENDED_KEY_USAGE
        )
    except x509.ExtensionNotFound as exc:
        raise TrustedTimeConfigError("Pinned TSA leaf lacks timestamping EKU") from exc
    if not eku_extension.critical or list(eku_extension.value) != [
        ExtendedKeyUsageOID.TIME_STAMPING
    ]:
        raise TrustedTimeConfigError("Pinned TSA leaf EKU changed")
    return TrustMaterial(
        manifest_snapshot.sha256,
        leaf,
        root,
        leaf_snapshot.sha256,
        root_snapshot.sha256,
        leaf_der_sha,
        root_der_sha,
    )


def _der_length(length: int) -> bytes:
    if length < 0:
        raise ValueError("negative DER length")
    if length < 128:
        return bytes([length])
    encoded = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(encoded)]) + encoded


def _der(tag: int, content: bytes) -> bytes:
    return bytes([tag]) + _der_length(len(content)) + content


def _der_integer(value: int) -> bytes:
    if type(value) is not int or value < 0:
        raise TrustedTimeIntegrityError("DER integer must be non-negative")
    encoded = (
        b"\x00" if value == 0 else value.to_bytes((value.bit_length() + 7) // 8, "big")
    )
    if encoded[0] & 0x80:
        encoded = b"\x00" + encoded
    return _der(0x02, encoded)


def _base128(value: int) -> bytes:
    if value < 0:
        raise ValueError("negative OID arc")
    pieces = [value & 0x7F]
    value >>= 7
    while value:
        pieces.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(pieces))


def _der_oid(oid: str) -> bytes:
    try:
        arcs = [int(part) for part in oid.split(".")]
    except ValueError as exc:
        raise TrustedTimeIntegrityError("Invalid OID") from exc
    if (
        len(arcs) < 2
        or arcs[0] not in (0, 1, 2)
        or arcs[1] < 0
        or (arcs[0] < 2 and arcs[1] > 39)
    ):
        raise TrustedTimeIntegrityError("Invalid OID arcs")
    content = _base128(40 * arcs[0] + arcs[1]) + b"".join(
        _base128(arc) for arc in arcs[2:]
    )
    return _der(0x06, content)


def build_timestamp_request(message: bytes, nonce: int, policy_oid: str) -> bytes:
    """Build the strict v1 TimeStampReq that the dependency builder cannot express."""

    if not isinstance(message, bytes) or not message:
        raise TrustedTimeIntegrityError("Timestamp message must be non-empty bytes")
    if type(nonce) is not int or nonce.bit_length() != 256:
        raise TrustedTimeIntegrityError("Timestamp nonce must be exactly 256 bits")
    digest = hashlib.sha256(message).digest()
    algorithm = _der(0x30, _der_oid(SHA256_OID) + b"\x05\x00")
    imprint = _der(0x30, algorithm + _der(0x04, digest))
    return _der(
        0x30,
        _der_integer(1)
        + imprint
        + _der_oid(policy_oid)
        + _der_integer(nonce)
        + b"\x01\x01\xff",
    )


def _default_nonce() -> int:
    return int.from_bytes(secrets.token_bytes(32), "big") | (1 << 255)


def verify_timestamp_response(
    response_der: bytes,
    message: bytes,
    nonce: int,
    target: date,
    profile: Mapping[str, Any],
    trust: TrustMaterial,
) -> dict[str, object]:
    """Cryptographically verify a raw TSR and return deterministic facts."""

    maximum = profile["rfc3161"]["maximum_response_bytes"]
    if (
        not isinstance(response_der, bytes)
        or not response_der
        or len(response_der) > maximum
    ):
        raise TrustedTimeIntegrityError("Timestamp response size is invalid")
    if type(nonce) is not int or nonce.bit_length() != 256:
        raise TrustedTimeIntegrityError("Timestamp nonce is not 256 bits")
    try:
        response = decode_timestamp_response(response_der)
    except Exception as exc:
        raise TrustedTimeIntegrityError("Timestamp response DER is invalid") from exc
    rfc = profile["rfc3161"]
    if response.status != rfc["accepted_pki_status"]:
        raise TrustedTimeIntegrityError("Timestamp response was not exactly GRANTED")
    info = response.tst_info
    if info is None or info.version != 1:
        raise TrustedTimeIntegrityError("Timestamp TSTInfo version changed")
    if info.policy.dotted_string != rfc["policy_oid"]:
        raise TrustedTimeIntegrityError("Timestamp policy changed")
    if info.nonce != nonce:
        raise TrustedTimeIntegrityError("Timestamp nonce changed")
    if info.message_imprint.hash_algorithm.dotted_string != SHA256_OID:
        raise TrustedTimeIntegrityError("Timestamp imprint algorithm changed")
    expected_imprint = hashlib.sha256(message).digest()
    if info.message_imprint.message != expected_imprint:
        raise TrustedTimeIntegrityError("Timestamp message imprint changed")
    if (
        not isinstance(info.name, x509.DirectoryName)
        or info.name.value != trust.leaf.subject
    ):
        raise TrustedTimeIntegrityError("Timestamp TSA name changed")
    digest_algorithms = {
        algorithm.dotted_string for algorithm in response.signed_data.digest_algorithms
    }
    if digest_algorithms != {SHA256_OID}:
        raise TrustedTimeIntegrityError("Timestamp CMS digest algorithms changed")
    if len(response.signed_data.signer_infos) != 1:
        raise TrustedTimeIntegrityError("Timestamp response must have one signer")
    certificates = set(response.signed_data.certificates)
    if certificates != {trust.leaf.public_bytes(Encoding.DER)}:
        raise TrustedTimeIntegrityError("Timestamp embedded leaf changed")
    try:
        verifier = (
            VerifierBuilder()
            .policy_id(ObjectIdentifier(rfc["policy_oid"]))
            .tsa_certificate(trust.leaf)
            .add_root_certificate(trust.root)
            .nonce(nonce)
            .build()
        )
        verified = verifier.verify_message(response, message)
    except Exception as exc:
        raise TrustedTimeIntegrityError(
            "Timestamp signature or chain verification failed"
        ) from exc
    if verified is not True:
        raise TrustedTimeIntegrityError("Timestamp verifier did not return true")
    generated = info.gen_time
    if generated.tzinfo is None or generated.utcoffset() is None:
        raise TrustedTimeIntegrityError("Timestamp genTime lacks UTC information")
    generated = generated.astimezone(timezone.utc)
    if generated < _parse_utc(
        rfc["provider_epoch_not_before_utc"], name="provider epoch"
    ):
        raise TrustedTimeIntegrityError("Timestamp predates the pinned provider epoch")
    accuracy = info.accuracy
    if accuracy is None:
        raise TrustedTimeIntegrityError("Timestamp accuracy is absent")
    seconds = 0 if accuracy.seconds is None else accuracy.seconds
    millis = 0 if accuracy.millis is None else accuracy.millis
    micros = 0 if accuracy.micros is None else accuracy.micros
    if (
        type(seconds) is not int
        or type(millis) is not int
        or type(micros) is not int
        or seconds < 0
        or not 0 <= millis <= 999
        or not 0 <= micros <= 999
    ):
        raise TrustedTimeIntegrityError("Timestamp accuracy fields are invalid")
    accuracy_seconds = seconds + millis / 1000.0 + micros / 1_000_000.0
    if (
        not math.isfinite(accuracy_seconds)
        or accuracy_seconds > rfc["maximum_accuracy_seconds"]
    ):
        raise TrustedTimeIntegrityError("Timestamp accuracy exceeds the profile")
    upper = generated + timedelta(seconds=accuracy_seconds)
    frozen_target_timezone = timezone(timedelta(hours=8), name="Asia/Shanghai+08:v1")
    target_boundary = datetime.combine(
        target, time.min, frozen_target_timezone
    ).astimezone(timezone.utc)
    return {
        "pki_status": int(response.status),
        "tst_info_version": int(info.version),
        "policy_oid": info.policy.dotted_string,
        "message_imprint_algorithm_oid": info.message_imprint.hash_algorithm.dotted_string,
        "message_imprint_sha256": expected_imprint.hex(),
        "nonce_hex": f"{nonce:064x}",
        "serial_number": str(info.serial_number),
        "gen_time_utc": _utc_text(generated),
        "accuracy_seconds": accuracy_seconds,
        "trusted_upper_bound_utc": _utc_text(upper),
        "target_boundary_utc": _utc_text(target_boundary),
        "causality_before_target": upper < target_boundary,
        "ordering": bool(info.ordering),
        "signer_count": 1,
        "embedded_certificate_count": len(certificates),
        "leaf_der_sha256": trust.leaf_der_sha256,
        "root_der_sha256": trust.root_der_sha256,
        "tsa_name_matches_leaf_subject": True,
        "signature_and_chain_verified": True,
    }


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def post_timestamp_query(
    endpoint: str, request_der: bytes, timeout_seconds: float, maximum_bytes: int
) -> TransportResponse:
    try:
        blocked_signals = signal.pthread_sigmask(signal.SIG_BLOCK, set())
    except (AttributeError, OSError, ValueError) as exc:
        raise TrustedTimeIntegrityError(
            "Timestamp transport signal mask cannot be inspected"
        ) from exc
    if signal.SIGALRM in blocked_signals:
        raise TrustedTimeIntegrityError(
            "Timestamp transport deadline signal is blocked"
        )
    request = urllib.request.Request(
        endpoint,
        data=request_der,
        method="POST",
        headers={
            "Content-Type": "application/timestamp-query",
            "Accept": "application/timestamp-reply",
            "Accept-Encoding": "identity",
            "User-Agent": "ootang-trusted-time-shadow/1",
        },
    )
    opener = urllib.request.build_opener(_NoRedirect())
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    if previous_timer != (0.0, 0.0):
        raise TrustedTimeIntegrityError("An unrelated process alarm is already active")

    def deadline_exceeded(_signum, _frame):  # type: ignore[no-untyped-def]
        raise TimeoutError("Timestamp transport total deadline exceeded")

    try:
        signal.signal(signal.SIGALRM, deadline_exceeded)
        signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
    except (OSError, ValueError) as exc:
        try:
            signal.signal(signal.SIGALRM, previous_handler)
        except (OSError, ValueError):
            pass
        raise TrustedTimeIntegrityError(
            "Timestamp transport deadline cannot be armed"
        ) from exc
    try:
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                status_code = int(response.status)
                media_type = response.headers.get_content_type().lower()
                encoding = response.headers.get("Content-Encoding")
                final_url = response.geturl()
                body = response.read(maximum_bytes + 1)
        except urllib.error.HTTPError as exc:
            if exc.code in {408, 425, 429} or exc.code >= 500:
                raise TrustedTimeNetworkError(
                    f"Timestamp service HTTP {exc.code}"
                ) from exc
            raise TrustedTimeIntegrityError(
                f"Timestamp service rejected fixed request with HTTP {exc.code}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TrustedTimeNetworkError("Timestamp service transport failed") from exc
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)
    if len(body) > maximum_bytes:
        raise TrustedTimeIntegrityError("Timestamp response exceeds its size limit")
    return TransportResponse(body, status_code, media_type, encoding, final_url)


def _acquire_lock(path: Path):
    _ensure_directory(path.parent)
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    handle = None
    try:
        descriptor = os.open(path, flags, 0o600)
        handle = os.fdopen(descriptor, "a+b", buffering=0)
        descriptor = None
        handle_stat = os.fstat(handle.fileno())
        path_stat = os.lstat(path)
        if (
            not stat.S_ISREG(handle_stat.st_mode)
            or not stat.S_ISREG(path_stat.st_mode)
            or (handle_stat.st_dev, handle_stat.st_ino)
            != (path_stat.st_dev, path_stat.st_ino)
        ):
            raise TrustedTimeIntegrityError(
                "Shared runner lock path does not match its regular open file"
            )
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked_path_stat = os.lstat(path)
        locked_handle_stat = os.fstat(handle.fileno())
        if not stat.S_ISREG(locked_path_stat.st_mode) or (
            locked_handle_stat.st_dev,
            locked_handle_stat.st_ino,
        ) != (locked_path_stat.st_dev, locked_path_stat.st_ino):
            raise TrustedTimeIntegrityError(
                "Shared runner lock path changed while it was acquired"
            )
        return handle
    except BlockingIOError as exc:
        if handle is not None:
            handle.close()
        raise TrustedTimeBusyError("Shared live runner lock is busy") from exc
    except TrustedTimeIntegrityError:
        if handle is not None:
            handle.close()
        raise
    except OSError as exc:
        if handle is not None:
            handle.close()
        raise TrustedTimeIntegrityError(
            "Shared runner lock cannot be opened safely"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _candidate(
    profile: Mapping[str, Any],
    paths: TrustedTimePaths,
    *,
    requested_target: date | None = None,
) -> tuple[Candidate | None, str]:
    live_profile = live.load_config(paths.live_profile)
    live_paths = live.runtime_paths(live_profile, runtime_root=paths.root)
    prerequisites = live.load_prerequisites(live_profile, live_paths)
    if prerequisites is None:
        return None, "waiting_for_live_prerequisites"
    try:
        projection = live.load_verified_ledger_projection(
            live_profile, live_paths, prerequisites
        )
    except live.LivePrerequisiteError:
        return None, "waiting_for_live_ledger"
    if requested_target is None:
        target = projection.outstanding_target_date
        seal = projection.seal_event
        issue_id = projection.outstanding_issue_id
        if target is None:
            return None, "waiting_for_outstanding_issue"
        if seal is None or issue_id is None:
            raise TrustedTimeIntegrityError("Outstanding issue lacks its durable seal")
    else:
        target = requested_target
        seals = [
            event
            for event in projection.ledger_events
            if event.event_type == "issue_batch_sealed"
            and event.target_date == target.isoformat()
        ]
        if len(seals) != 1 or seals[0].issue_id is None:
            raise TrustedTimeIntegrityError(
                "Trusted-time target lacks one verified live seal"
            )
        seal = seals[0]
        issue_id = seal.issue_id
    guard = verified_live.guard_progress_payload(
        config_path=paths.verified_live_profile,
        runtime_root=paths.root,
        project_root=Path(str(profile["_project_root"])),
    )
    completions = [
        item
        for item in guard["completions"]
        if item.get("target_date") == target.isoformat()
    ]
    intents = [
        item
        for item in guard["intents"]
        if item.get("target_date") == target.isoformat()
    ]
    if not completions:
        return None, "waiting_for_verified_live_completion"
    if len(completions) != 1 or len(intents) != 1:
        raise TrustedTimeIntegrityError("Guard progress has ambiguous target identity")
    completion = completions[0]
    intent = intents[0]
    if completion["live_issue_seal_event_key"] != seal.event_key:
        raise TrustedTimeIntegrityError(
            "Guard completion does not identify the outstanding seal"
        )
    envelope = {
        "schema_version": profile["protocol"]["evidence_envelope_schema_version"],
        "domain_separator": profile["protocol"]["domain_separator"],
        "case": "ootang",
        "trusted_time_profile_id": profile["profile_id"],
        "trusted_time_profile_sha256": profile["_profile_sha256"],
        "trust_manifest_sha256": profile["trust_manifest"]["expected_sha256"],
        "live_profile_sha256": profile["live_profile"]["expected_sha256"],
        "verified_live_profile_sha256": profile["verified_live_profile"][
            "expected_sha256"
        ],
        "live_epoch_id": projection.epoch_id,
        "target_date": target.isoformat(),
        "outstanding_issue_id": issue_id,
        "live_issue_seal_sequence_id": seal.sequence_id,
        "live_issue_seal_entry_sha256": seal.entry_sha256,
        "live_issue_seal_event_key": seal.event_key,
        "live_issue_seal_science_sha256": completion[
            "live_issue_seal_entry_science_sha256"
        ],
        "guard_completion_semantic_sha256": completion["completion_semantic_sha256"],
        "guard_intent_semantic_sha256": completion["intent_semantic_sha256"],
        "replay_semantic_sha256": completion["replay_semantic_sha256"],
        "issue_sha256": completion["issue_sha256"],
        "input_manifest_sha256": completion["input_manifest_sha256"],
        "model_manifest_sha256": intent["model_manifest_sha256"],
        "outcome_read": False,
    }
    return Candidate(target, envelope), "ready_for_timestamp_request"


def _target_path(directory: Path, target: date, suffix: str) -> Path:
    return directory / f"{target.isoformat()}{suffix}"


def _ensure_request(
    profile: Mapping[str, Any],
    paths: TrustedTimePaths,
    candidate: Candidate,
    *,
    now: datetime,
    nonce_factory: NonceFactory,
) -> RequestArtifacts:
    record_path = _target_path(paths.requests, candidate.target, ".json")
    der_path = _target_path(paths.request_der, candidate.target, ".tsq")
    message = _canonical_bytes(candidate.envelope)
    try:
        record_snapshot = _read_regular(record_path, name="timestamp request record")
    except FileNotFoundError:
        if der_path.exists() or der_path.is_symlink():
            raise TrustedTimeIntegrityError(
                "Timestamp DER exists without its request record"
            )
        nonce = nonce_factory()
        if (
            type(nonce) is not int
            or nonce.bit_length() != profile["rfc3161"]["nonce_bits"]
        ):
            raise TrustedTimeIntegrityError("Nonce factory did not produce 256 bits")
        request_der = build_timestamp_request(
            message, nonce, profile["rfc3161"]["policy_oid"]
        )
        request_der_reference = {
            "path": der_path.relative_to(paths.root).as_posix(),
            "sha256": _sha256(request_der),
            "size_bytes": len(request_der),
        }
        record = {
            "schema_version": profile["protocol"]["request_schema_version"],
            "profile_id": profile["profile_id"],
            "profile_sha256": profile["_profile_sha256"],
            "artifact_status": profile["artifact_status"],
            "target_date": candidate.target.isoformat(),
            "created_at_utc": _utc_text(now),
            "evidence_envelope": candidate.envelope,
            "evidence_envelope_sha256": _sha256(message),
            "nonce_hex": f"{nonce:064x}",
            "policy_oid": profile["rfc3161"]["policy_oid"],
            "message_imprint_algorithm_oid": SHA256_OID,
            "message_imprint_sha256": hashlib.sha256(message).hexdigest(),
            "cert_req": True,
            "request_der": request_der_reference,
            "outcome_read": False,
            "trusted_anchor_receipt_verified": False,
            "e2_live_evidence_eligible": False,
            "real_activation_ready": False,
            "formal_warning_output": False,
        }
        record_snapshot = _publish_create_only(
            record_path, _canonical_bytes(record), name="timestamp request record"
        )
        der_snapshot = _publish_create_only(
            der_path, request_der, name="timestamp request DER"
        )
        return RequestArtifacts(record, record_snapshot, der_snapshot, message, nonce)
    record = _decode_json(record_snapshot.raw, name="timestamp request record")
    if record_snapshot.raw != _canonical_bytes(record):
        raise TrustedTimeIntegrityError("Timestamp request record is not canonical")
    expected_keys = {
        "schema_version",
        "profile_id",
        "profile_sha256",
        "artifact_status",
        "target_date",
        "created_at_utc",
        "evidence_envelope",
        "evidence_envelope_sha256",
        "nonce_hex",
        "policy_oid",
        "message_imprint_algorithm_oid",
        "message_imprint_sha256",
        "cert_req",
        "request_der",
        "outcome_read",
        "trusted_anchor_receipt_verified",
        "e2_live_evidence_eligible",
        "real_activation_ready",
        "formal_warning_output",
    }
    _require_exact_keys(record, expected_keys, name="timestamp request record")
    if (
        record["schema_version"] != profile["protocol"]["request_schema_version"]
        or record["profile_id"] != profile["profile_id"]
        or record["profile_sha256"] != profile["_profile_sha256"]
        or record["artifact_status"] != profile["artifact_status"]
        or record["target_date"] != candidate.target.isoformat()
        or record["evidence_envelope"] != candidate.envelope
        or record["evidence_envelope_sha256"] != _sha256(message)
        or record["policy_oid"] != profile["rfc3161"]["policy_oid"]
        or record["message_imprint_algorithm_oid"] != SHA256_OID
        or record["message_imprint_sha256"] != hashlib.sha256(message).hexdigest()
        or record["cert_req"] is not True
    ):
        raise TrustedTimeIntegrityError("Timestamp request semantics changed")
    _parse_utc(record["created_at_utc"], name="request.created_at_utc")
    for key in (
        "outcome_read",
        "trusted_anchor_receipt_verified",
        "e2_live_evidence_eligible",
        "real_activation_ready",
        "formal_warning_output",
    ):
        _require_false(record[key], name=f"request.{key}")
    nonce_text = _require_text(record["nonce_hex"], name="request.nonce_hex")
    if len(nonce_text) != 64 or any(
        character not in HEX_DIGITS for character in nonce_text
    ):
        raise TrustedTimeIntegrityError("Request nonce encoding changed")
    nonce = int(nonce_text, 16)
    if nonce.bit_length() != 256:
        raise TrustedTimeIntegrityError("Request nonce is not 256 bits")
    expected_der = build_timestamp_request(
        message, nonce, profile["rfc3161"]["policy_oid"]
    )
    reference = record["request_der"]
    expected_reference = {
        "path": der_path.relative_to(paths.root).as_posix(),
        "sha256": _sha256(expected_der),
        "size_bytes": len(expected_der),
    }
    if reference != expected_reference:
        raise TrustedTimeIntegrityError("Timestamp request DER reference changed")
    try:
        der_snapshot = _read_regular(der_path, name="timestamp request DER")
    except FileNotFoundError:
        der_snapshot = _publish_create_only(
            der_path, expected_der, name="timestamp request DER"
        )
    if der_snapshot.raw != expected_der:
        raise TrustedTimeIntegrityError("Timestamp request DER changed")
    return RequestArtifacts(record, record_snapshot, der_snapshot, message, nonce)


def _verification_with_transport(
    verification: Mapping[str, object],
    *,
    response_sha256: str,
    dependency_version: str,
    received_at: datetime,
    rtt: float,
    advisory_limit: float,
) -> dict[str, object]:
    generated = _parse_utc(
        verification["gen_time_utc"], name="verification.gen_time_utc"
    )
    skew = abs((received_at - generated).total_seconds())
    return {
        **verification,
        "response_sha256": response_sha256,
        "rfc3161_client_version": dependency_version,
        "network_round_trip_seconds": rtt,
        "received_at_utc": _utc_text(received_at),
        "local_clock_skew_seconds": skew,
        "local_clock_within_advisory_limit": skew <= advisory_limit,
    }


def _link_path(paths: TrustedTimePaths, target: date) -> Path:
    return _target_path(paths.response_links, target, ".json")


def _receipt_path(paths: TrustedTimePaths, target: date) -> Path:
    return _target_path(paths.receipts, target, ".json")


def _load_link(
    profile: Mapping[str, Any], paths: TrustedTimePaths, request: RequestArtifacts
) -> tuple[dict[str, Any], ArtifactSnapshot, ArtifactSnapshot]:
    target = date.fromisoformat(request.record["target_date"])
    link_snapshot = _read_regular(
        _link_path(paths, target), name="timestamp response link"
    )
    link = _decode_json(link_snapshot.raw, name="timestamp response link")
    if link_snapshot.raw != _canonical_bytes(link):
        raise TrustedTimeIntegrityError("Timestamp response link is not canonical")
    expected_keys = {
        "schema_version",
        "profile_id",
        "profile_sha256",
        "artifact_status",
        "target_date",
        "request_record",
        "request_der",
        "response_object",
        "endpoint",
        "media_type",
        "content_encoding",
        "received_at_utc",
        "network_round_trip_seconds",
        "outcome_read",
        "trusted_anchor_receipt_verified",
        "e2_live_evidence_eligible",
        "real_activation_ready",
        "formal_warning_output",
    }
    _require_exact_keys(link, expected_keys, name="timestamp response link")
    if (
        link["schema_version"] != profile["protocol"]["response_link_schema_version"]
        or link["profile_id"] != profile["profile_id"]
        or link["profile_sha256"] != profile["_profile_sha256"]
        or link["artifact_status"] != profile["artifact_status"]
        or link["target_date"] != target.isoformat()
        or link["request_record"]
        != _artifact_reference(request.record_snapshot, paths.root)
        or link["request_der"] != _artifact_reference(request.der_snapshot, paths.root)
        or link["endpoint"] != profile["rfc3161"]["endpoint"]
        or link["media_type"] != profile["rfc3161"]["response_media_type"]
        or link["content_encoding"] is not None
    ):
        raise TrustedTimeIntegrityError("Timestamp response link changed")
    for key in (
        "outcome_read",
        "trusted_anchor_receipt_verified",
        "e2_live_evidence_eligible",
        "real_activation_ready",
        "formal_warning_output",
    ):
        _require_false(link[key], name=f"response_link.{key}")
    _parse_utc(link["received_at_utc"], name="response_link.received_at_utc")
    rtt = link["network_round_trip_seconds"]
    if (
        type(rtt) not in (int, float)
        or isinstance(rtt, bool)
        or not math.isfinite(float(rtt))
        or not 0
        <= float(rtt)
        <= profile["rfc3161"]["maximum_network_round_trip_seconds"]
    ):
        raise TrustedTimeIntegrityError("Timestamp response link RTT changed")
    response_reference = _require_exact_keys(
        link["response_object"],
        {"path", "sha256", "size_bytes"},
        name="timestamp response object",
    )
    response_sha256 = _require_sha256(
        response_reference["sha256"], name="timestamp response object.sha256"
    )
    expected_object_path = (
        (paths.objects / f"{response_sha256}.tsr").relative_to(paths.root).as_posix()
    )
    if response_reference["path"] != expected_object_path:
        raise TrustedTimeIntegrityError(
            "Timestamp response object is outside its content-addressed namespace"
        )
    response_snapshot = _load_reference(
        response_reference,
        root=paths.root,
        name="timestamp response object",
        maximum_bytes=profile["rfc3161"]["maximum_response_bytes"],
    )
    return link, link_snapshot, response_snapshot


def _expected_receipt(
    profile: Mapping[str, Any],
    paths: TrustedTimePaths,
    trust: TrustMaterial,
    dependency_version: str,
    request: RequestArtifacts,
    link: Mapping[str, Any],
    link_snapshot: ArtifactSnapshot,
    response_snapshot: ArtifactSnapshot,
) -> dict[str, object]:
    target = date.fromisoformat(request.record["target_date"])
    verification = verify_timestamp_response(
        response_snapshot.raw, request.message, request.nonce, target, profile, trust
    )
    verification = _verification_with_transport(
        verification,
        response_sha256=response_snapshot.sha256,
        dependency_version=dependency_version,
        received_at=_parse_utc(
            link["received_at_utc"], name="response_link.received_at_utc"
        ),
        rtt=float(link["network_round_trip_seconds"]),
        advisory_limit=profile["rfc3161"]["wall_clock_advisory_limit_seconds"],
    )
    return {
        "schema_version": profile["protocol"]["receipt_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "artifact_status": profile["artifact_status"],
        "target_date": target.isoformat(),
        "request_record": _artifact_reference(request.record_snapshot, paths.root),
        "request_der": _artifact_reference(request.der_snapshot, paths.root),
        "response_link": _artifact_reference(link_snapshot, paths.root),
        "response_object": _artifact_reference(response_snapshot, paths.root),
        "verification": verification,
        "rfc3161_receipt_verified": True,
        "cryptographic_time_shadow_verified": True,
        "causality_before_target": verification["causality_before_target"],
        "outcome_read": False,
        "trusted_anchor_receipt_verified": False,
        "e2_live_evidence_eligible": False,
        "real_activation_ready": False,
        "automatic_epoch_rotation_implemented": False,
        "automatic_calibration_promotion_implemented": False,
        "formal_warning_output": False,
    }


def _materialize_receipt(
    profile: Mapping[str, Any],
    paths: TrustedTimePaths,
    trust: TrustMaterial,
    dependency_version: str,
    request: RequestArtifacts,
) -> dict[str, object]:
    link, link_snapshot, response_snapshot = _load_link(profile, paths, request)
    expected = _expected_receipt(
        profile,
        paths,
        trust,
        dependency_version,
        request,
        link,
        link_snapshot,
        response_snapshot,
    )
    target = date.fromisoformat(request.record["target_date"])
    snapshot = _publish_create_only(
        _receipt_path(paths, target),
        _canonical_bytes(expected),
        name="timestamp receipt",
    )
    if snapshot.raw != _canonical_bytes(expected):
        raise TrustedTimeIntegrityError("Timestamp receipt changed")
    return expected


def load_verified_trusted_time_receipt(
    target: date,
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    runtime_root: Path | None = None,
    project_root: Path = ROOT,
) -> dict[str, object]:
    """Reload all immutable artifacts and redo cryptographic verification."""

    profile = load_trusted_time_profile(config_path, project_root=project_root)
    paths = trusted_time_paths(profile, runtime_root=runtime_root)
    dependency = _check_dependency(profile)
    trust = load_trust_material(profile, paths)
    candidate, reason = _candidate(profile, paths, requested_target=target)
    if candidate is None:
        raise TrustedTimeIntegrityError(
            f"Trusted-time evidence envelope cannot be rederived: {reason}"
        )
    record_snapshot = _read_regular(
        _target_path(paths.requests, target, ".json"), name="timestamp request record"
    )
    record = _decode_json(record_snapshot.raw, name="timestamp request record")
    request = _ensure_request(
        profile,
        paths,
        candidate,
        now=_parse_utc(record.get("created_at_utc"), name="request.created_at_utc"),
        nonce_factory=lambda: 0,
    )
    link, link_snapshot, response_snapshot = _load_link(profile, paths, request)
    expected = _expected_receipt(
        profile,
        paths,
        trust,
        dependency,
        request,
        link,
        link_snapshot,
        response_snapshot,
    )
    receipt_snapshot = _read_regular(
        _receipt_path(paths, target), name="timestamp receipt"
    )
    if receipt_snapshot.raw != _canonical_bytes(expected):
        raise TrustedTimeIntegrityError("Timestamp receipt differs from full replay")
    return expected


def _write_status(
    profile: Mapping[str, Any],
    paths: TrustedTimePaths,
    *,
    now: datetime,
    runner_status: str,
    reason: str,
    target: date | None = None,
    receipt: Mapping[str, object] | None = None,
) -> Path:
    verified = receipt is not None
    causality = (
        bool(receipt["causality_before_target"]) if receipt is not None else False
    )
    status = {
        "schema_version": profile["protocol"]["status_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "artifact_status": profile["artifact_status"],
        "polled_at_utc": _utc_text(now),
        "runner_status": runner_status,
        "reason": reason,
        "target_date": target.isoformat() if target is not None else None,
        "rfc3161_receipt_verified": verified,
        "cryptographic_time_shadow_verified": verified,
        "causality_before_target": causality,
        "receipt_sha256": (
            _sha256(_canonical_bytes(receipt)) if receipt is not None else None
        ),
        "outcome_read": False,
        "trusted_anchor_receipt_verified": False,
        "e2_live_evidence_eligible": False,
        "real_activation_ready": False,
        "automatic_epoch_rotation_implemented": False,
        "automatic_calibration_promotion_implemented": False,
        "formal_warning_output": False,
    }
    _atomic_status(paths.status, _canonical_bytes(status))
    return paths.status


def poll_trusted_time_shadow(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    runtime_root: Path | None = None,
    project_root: Path = ROOT,
    transport: Transport = post_timestamp_query,
    clock: Clock | None = None,
    monotonic: Monotonic = time_module.monotonic,
    nonce_factory: NonceFactory = _default_nonce,
) -> Path:
    profile = load_trusted_time_profile(config_path, project_root=project_root)
    paths = trusted_time_paths(profile, runtime_root=runtime_root)
    dependency = _check_dependency(profile)
    trust = load_trust_material(profile, paths)
    _ensure_directory(paths.root)
    lock = _acquire_lock(paths.runner_lock)
    try:
        now = _clock_value(clock)
        candidate, waiting_reason = _candidate(profile, paths)
        if candidate is None:
            return _write_status(
                profile,
                paths,
                now=now,
                runner_status=waiting_reason,
                reason=waiting_reason,
            )
        request = _ensure_request(
            profile, paths, candidate, now=now, nonce_factory=nonce_factory
        )
        link_path = _link_path(paths, candidate.target)
        if not link_path.exists() and not link_path.is_symlink():
            start = monotonic()
            try:
                transport_response = transport(
                    profile["rfc3161"]["endpoint"],
                    request.der_snapshot.raw,
                    profile["rfc3161"]["network_timeout_seconds"],
                    profile["rfc3161"]["maximum_response_bytes"],
                )
            except TrustedTimeNetworkError as exc:
                return _write_status(
                    profile,
                    paths,
                    now=_clock_value(clock),
                    runner_status="waiting_for_timestamp_service",
                    reason=str(exc),
                    target=candidate.target,
                )
            end = monotonic()
            if (
                not isinstance(start, (int, float))
                or not isinstance(end, (int, float))
                or not math.isfinite(float(start))
                or not math.isfinite(float(end))
                or end < start
            ):
                raise TrustedTimeIntegrityError("Monotonic clock changed invalidly")
            rtt = float(end - start)
            if rtt > profile["rfc3161"]["maximum_network_round_trip_seconds"]:
                return _write_status(
                    profile,
                    paths,
                    now=_clock_value(clock),
                    runner_status="waiting_for_timestamp_service",
                    reason="timestamp_service_round_trip_exceeded",
                    target=candidate.target,
                )
            if (
                transport_response.status != 200
                or transport_response.media_type
                != profile["rfc3161"]["response_media_type"]
                or transport_response.content_encoding not in (None, "")
                or transport_response.final_url != profile["rfc3161"]["endpoint"]
                or len(transport_response.body)
                > profile["rfc3161"]["maximum_response_bytes"]
            ):
                raise TrustedTimeIntegrityError("Timestamp transport contract changed")
            verification = verify_timestamp_response(
                transport_response.body,
                request.message,
                request.nonce,
                candidate.target,
                profile,
                trust,
            )
            response_sha = _sha256(transport_response.body)
            object_path = paths.objects / f"{response_sha}.tsr"
            response_snapshot = _publish_create_only(
                object_path,
                transport_response.body,
                name="timestamp response object",
            )
            received_at = _clock_value(clock)
            link = {
                "schema_version": profile["protocol"]["response_link_schema_version"],
                "profile_id": profile["profile_id"],
                "profile_sha256": profile["_profile_sha256"],
                "artifact_status": profile["artifact_status"],
                "target_date": candidate.target.isoformat(),
                "request_record": _artifact_reference(
                    request.record_snapshot, paths.root
                ),
                "request_der": _artifact_reference(request.der_snapshot, paths.root),
                "response_object": _artifact_reference(response_snapshot, paths.root),
                "endpoint": profile["rfc3161"]["endpoint"],
                "media_type": transport_response.media_type,
                "content_encoding": None,
                "received_at_utc": _utc_text(received_at),
                "network_round_trip_seconds": rtt,
                "outcome_read": False,
                "trusted_anchor_receipt_verified": False,
                "e2_live_evidence_eligible": False,
                "real_activation_ready": False,
                "formal_warning_output": False,
            }
            _ = verification
            _publish_create_only(
                link_path, _canonical_bytes(link), name="timestamp response link"
            )
        receipt = _materialize_receipt(profile, paths, trust, dependency, request)
        replayed = load_verified_trusted_time_receipt(
            candidate.target,
            config_path=config_path,
            runtime_root=runtime_root,
            project_root=project_root,
        )
        if replayed != receipt:
            raise TrustedTimeIntegrityError("Public receipt replay changed semantics")
        return _write_status(
            profile,
            paths,
            now=_clock_value(clock),
            runner_status=(
                "verified_before_target"
                if receipt["causality_before_target"]
                else "verified_after_target_boundary"
            ),
            reason="rfc3161_receipt_replayed_from_raw_response",
            target=candidate.target,
            receipt=receipt,
        )
    finally:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        finally:
            lock.close()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--runtime-root", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        status = poll_trusted_time_shadow(
            config_path=args.config, runtime_root=args.runtime_root
        )
    except TrustedTimeBusyError as exc:
        print(f"[trusted-time-shadow] busy: {exc}", file=sys.stderr)
        return 3
    except TrustedTimeNetworkError as exc:
        print(f"[trusted-time-shadow] waiting: {exc}", file=sys.stderr)
        return 0
    except (TrustedTimeConfigError, TrustedTimeIntegrityError) as exc:
        print(f"[trusted-time-shadow] blocked: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(
            f"[trusted-time-shadow] blocked: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2
    print(f"[trusted-time-shadow] status: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
