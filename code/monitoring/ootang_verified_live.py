"""Guarded E2 live entrypoint with independent checkpoint/input replay.

This additive runner leaves the reviewed live-v1 protocol and ledger untouched.
It holds the live-v1 runner lock while it advances at most one scientific
transition.  Before it can seal a new issue it durably orders four commits:

``replay receipt -> guard intent -> live-v1 issue transaction -> completion``.

The two JSON guard records make the unavoidable cross-ledger crash windows
recoverable.  They do not authenticate the local writer, disable the old live-v1
CLI, verify trusted time, create E2 evidence, or authorize real activation.
Final lock/artifact components fail closed on symlinks; concurrent intermediate
directory replacement remains inside the explicitly trusted-local-writer boundary.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tempfile
from typing import Any, BinaryIO, Callable
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_prequential_live as live  # noqa: E402


DEFAULT_CONFIG_PATH = ROOT / "config" / "ootang_verified_live.v1.json"
DEFAULT_CONFIG_SHA256 = (
    "081af2dfd4b95f28b750d915a2ff74d508381e62f5d539aaaaa62b8add44992b"
)
LIVE_PROFILE_SHA256 = "bf7c60a19e26e9a54fc4e1980b3556d6e6d1e3fec4b3a3a7f3de0dbb9b83cf00"
REPLAY_PROFILE_SHA256 = (
    "c42a56a547691654f9281f44b94e5d79ef66a8ff0064b255a59d67c6939e6fd5"
)
ZERO_HASH = "0" * 64
HEX_DIGITS = frozenset("0123456789abcdef")
MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024
CREATE_SCRATCH_DIRECTORY = ".verified_live_create_scratch"

SUCCESS_STATUSES = frozenset(
    {
        "waiting_for_live_prerequisites",
        "waiting_for_new_data",
        "waiting_for_missing_natural_day",
        "waiting_for_backfill_outcome",
        "waiting_for_outcome",
        "work_remaining",
    }
)


class VerifiedLiveError(RuntimeError):
    """Base error for the additive verified-live entrypoint."""


class VerifiedLiveConfigError(VerifiedLiveError):
    """The guard profile or one of its immutable bindings changed."""


class VerifiedLiveIntegrityError(VerifiedLiveError):
    """A replay, intent, completion, or live transition failed closed."""


class VerifiedLiveBusyError(VerifiedLiveError):
    """Another process owns the live-v1 runner lock."""


@dataclass(frozen=True)
class GuardRuntimePaths:
    root: Path
    status: Path
    intents: Path
    completions: Path
    live: live.RuntimePaths
    live_profile_path: Path
    replay_profile_path: Path


@dataclass(frozen=True)
class ArtifactSnapshot:
    path: Path
    sha256: str
    size_bytes: int
    raw: bytes

    def reference(self, root: Path) -> dict[str, object]:
        try:
            lexical_root = _lexical_absolute(root)
            try:
                relative_path = self.path.relative_to(lexical_root)
            except ValueError:
                resolved_root = _lexical_absolute(root.resolve())
                parent_canonical_path = (
                    _lexical_absolute(self.path.parent.resolve()) / self.path.name
                )
                relative_path = parent_canonical_path.relative_to(resolved_root)
            relative = relative_path.as_posix()
        except (OSError, RuntimeError, ValueError) as exc:
            raise VerifiedLiveIntegrityError(
                "Guard artifact escaped the runtime root"
            ) from exc
        return {
            "path": relative,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


Clock = Callable[[], datetime]


def _reject_constant(value: str) -> None:
    raise VerifiedLiveConfigError(f"Forbidden JSON constant: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise VerifiedLiveConfigError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _decode_json(raw: bytes, *, name: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise VerifiedLiveConfigError(f"{name} is not strict finite JSON") from exc
    if not isinstance(value, dict):
        raise VerifiedLiveConfigError(f"{name} must be a JSON object")
    return value


def _lexical_absolute(path: Path) -> Path:
    """Return an absolute normalized path without following symlinks."""

    return Path(os.path.abspath(os.fspath(path)))


def _read_snapshot(path: Path, *, name: str) -> ArtifactSnapshot:
    lexical_path = _lexical_absolute(path)
    descriptor: int | None = None
    try:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(lexical_path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            descriptor_before = os.fstat(handle.fileno())
            path_before = os.lstat(lexical_path)
            if (
                not stat.S_ISREG(descriptor_before.st_mode)
                or not stat.S_ISREG(path_before.st_mode)
                or (descriptor_before.st_dev, descriptor_before.st_ino)
                != (path_before.st_dev, path_before.st_ino)
            ):
                raise VerifiedLiveIntegrityError(f"{name} is not a regular file")
            if not 0 <= descriptor_before.st_size <= MAX_SNAPSHOT_BYTES:
                raise VerifiedLiveIntegrityError(f"{name} exceeds the snapshot size limit")
            raw = handle.read(MAX_SNAPSHOT_BYTES + 1)
            if len(raw) > MAX_SNAPSHOT_BYTES:
                raise VerifiedLiveIntegrityError(f"{name} exceeds the snapshot size limit")
            descriptor_after = os.fstat(handle.fileno())
            path_after = os.lstat(lexical_path)
            if (
                not stat.S_ISREG(path_after.st_mode)
                or (descriptor_after.st_dev, descriptor_after.st_ino)
                != (path_after.st_dev, path_after.st_ino)
                or (path_before.st_dev, path_before.st_ino)
                != (path_after.st_dev, path_after.st_ino)
            ):
                raise VerifiedLiveIntegrityError(
                    f"{name} pathname changed while being captured"
                )
            stable_fields = (
                "st_dev",
                "st_ino",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
            if any(
                getattr(descriptor_before, field) != getattr(descriptor_after, field)
                for field in stable_fields
            ):
                raise VerifiedLiveIntegrityError(f"{name} changed while being captured")
            if descriptor_after.st_size != len(raw):
                raise VerifiedLiveIntegrityError(f"{name} changed while being captured")
    except VerifiedLiveIntegrityError:
        raise
    except OSError as exc:
        raise VerifiedLiveIntegrityError(f"{name} cannot be read") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return ArtifactSnapshot(
        path=lexical_path,
        sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),
        raw=raw,
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
    except (TypeError, ValueError, RecursionError) as exc:
        raise VerifiedLiveIntegrityError(
            "Guard record is not canonical finite JSON"
        ) from exc


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _require_object(value: object, keys: set[str], *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise VerifiedLiveConfigError(f"{name} keys changed")
    return value


def _require_text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise VerifiedLiveConfigError(f"{name} must be a canonical non-empty string")
    return value


def _require_sha256(value: object, *, name: str) -> str:
    text = _require_text(value, name=name)
    if len(text) != 64 or any(character not in HEX_DIGITS for character in text):
        raise VerifiedLiveConfigError(f"{name} must be a lowercase SHA-256")
    return text


def _project_path(value: object, *, project_root: Path, name: str) -> Path:
    text = _require_text(value, name=name)
    relative = PurePosixPath(text)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or "\\" in text
    ):
        raise VerifiedLiveConfigError(f"{name} must be project-relative")
    root = project_root.resolve()
    path = root.joinpath(*relative.parts)
    try:
        path.resolve(strict=False).relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise VerifiedLiveConfigError(f"{name} escaped the project root") from exc
    return path


def _runtime_child(root: Path, value: object, *, name: str) -> Path:
    text = _require_text(value, name=name)
    relative = PurePosixPath(text)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or "\\" in text
    ):
        raise VerifiedLiveConfigError(f"{name} must be runtime-relative")
    current = root
    for part in relative.parts:
        current /= part
        try:
            if current.is_symlink():
                raise VerifiedLiveConfigError(f"{name} contains a symlink component")
        except OSError as exc:
            raise VerifiedLiveConfigError(f"{name} cannot be inspected") from exc
    try:
        current.resolve(strict=False).relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError) as exc:
        raise VerifiedLiveConfigError(f"{name} escaped the runtime root") from exc
    return current


def load_verified_live_profile(
    path: Path = DEFAULT_CONFIG_PATH, *, project_root: Path = ROOT
) -> dict[str, Any]:
    """Load the exact additive guard profile and verify its two base profiles."""

    snapshot = _read_snapshot(path.resolve(), name="verified-live profile")
    if path.resolve() == DEFAULT_CONFIG_PATH.resolve() and (
        snapshot.sha256 != DEFAULT_CONFIG_SHA256
    ):
        raise VerifiedLiveConfigError(
            "Verified-live default profile bytes changed; create a new version"
        )
    profile = _decode_json(snapshot.raw, name="verified-live profile")
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
        "replay_profile",
        "runtime",
        "protocol",
        "engineering_capabilities",
    }
    _require_object(profile, expected_top, name="verified-live profile")
    if (
        profile["schema_version"] != "ootang_verified_live_profile_v1"
        or profile["profile_id"] != "ootang-verified-live-v1"
        or profile["profile_version"] != "1.0.0-engineering"
        or profile["case"] != "ootang"
        or profile["artifact_status"]
        != "e2_verified_live_entrypoint_engineering_only_not_live_evidence"
    ):
        raise VerifiedLiveConfigError("Verified-live profile identity changed")
    for key in (
        "formal_warning_output",
        "independent_label_used",
        "confirmatory_external_validation",
        "vajont_used",
        "default_pipeline_member",
    ):
        if profile[key] is not False:
            raise VerifiedLiveConfigError(f"Verified-live flag {key} must remain false")

    bindings = (
        ("live_profile", LIVE_PROFILE_SHA256),
        ("replay_profile", REPLAY_PROFILE_SHA256),
    )
    for key, expected_hash in bindings:
        record = _require_object(profile[key], {"path", "expected_sha256"}, name=key)
        if (
            _require_sha256(record["expected_sha256"], name=f"{key}.expected_sha256")
            != expected_hash
        ):
            raise VerifiedLiveConfigError(f"{key} reviewed hash changed")
        bound_path = _project_path(
            record["path"], project_root=project_root, name=f"{key}.path"
        )
        if _read_snapshot(bound_path, name=key).sha256 != expected_hash:
            raise VerifiedLiveConfigError(f"{key} file differs from its exact binding")

    runtime = _require_object(
        profile["runtime"], {"root", "status", "intents", "completions"}, name="runtime"
    )
    for key in runtime:
        _require_text(runtime[key], name=f"runtime.{key}")
    protocol = _require_object(
        profile["protocol"],
        {
            "status_schema_version",
            "intent_schema_version",
            "completion_schema_version",
            "progress_schema_version",
            "transition_policy",
            "issue_commit_order",
            "require_completion_before_any_outcome_read_for_outstanding_issue",
            "direct_live_v1_issue_policy",
            "same_target_same_semantics",
            "same_target_changed_semantics",
            "old_live_v1_entrypoint_disabled",
            "scheduler_entrypoint_authorization_implemented",
        },
        name="protocol",
    )
    expected_protocol = {
        "status_schema_version": "ootang_verified_live_status_v1",
        "intent_schema_version": "ootang_verified_live_intent_v1",
        "completion_schema_version": "ootang_verified_live_completion_v1",
        "progress_schema_version": "ootang_verified_live_progress_v1",
        "transition_policy": "at_most_one_scientific_transition_per_poll",
        "issue_commit_order": [
            "independent_replay_receipt",
            "verified_live_intent",
            "live_issue_batch",
            "verified_live_completion",
        ],
        "require_completion_before_any_outcome_read_for_outstanding_issue": True,
        "direct_live_v1_issue_policy": "blocked_never_retroactively_authorized",
        "same_target_same_semantics": "idempotent_preserve_first_bytes",
        "same_target_changed_semantics": "blocked_integrity",
        "old_live_v1_entrypoint_disabled": False,
        "scheduler_entrypoint_authorization_implemented": False,
    }
    if protocol != expected_protocol:
        raise VerifiedLiveConfigError("Verified-live protocol changed")
    expected_capabilities = {
        "designated_entrypoint_replay_gate_implemented": True,
        "runner_independent_checkpoint_inference_replayed": True,
        "cross_ledger_commit_is_single_database_atomic": False,
        "trusted_anchor_receipt_verified": False,
        "automatic_epoch_rotation_implemented": False,
        "automatic_calibration_promotion_implemented": False,
        "e2_live_evidence_eligible": False,
        "real_activation_ready": False,
    }
    if profile["engineering_capabilities"] != expected_capabilities:
        raise VerifiedLiveConfigError("Verified-live capability boundary changed")
    profile["_profile_sha256"] = snapshot.sha256
    profile["_profile_path"] = str(path.resolve())
    profile["_project_root"] = str(project_root.resolve())
    return profile


def runtime_paths(
    profile: Mapping[str, Any], *, runtime_root: Path | None = None
) -> GuardRuntimePaths:
    root = (
        runtime_root.resolve()
        if runtime_root is not None
        else _project_path(
            profile["runtime"]["root"],
            project_root=Path(str(profile["_project_root"])),
            name="runtime.root",
        ).resolve()
    )
    live_profile_path = _project_path(
        profile["live_profile"]["path"],
        project_root=Path(str(profile["_project_root"])),
        name="live_profile.path",
    )
    replay_profile_path = _project_path(
        profile["replay_profile"]["path"],
        project_root=Path(str(profile["_project_root"])),
        name="replay_profile.path",
    )
    live_profile = live.load_config(live_profile_path)
    return GuardRuntimePaths(
        root=root,
        status=_runtime_child(
            root, profile["runtime"]["status"], name="runtime.status"
        ),
        intents=_runtime_child(
            root, profile["runtime"]["intents"], name="runtime.intents"
        ),
        completions=_runtime_child(
            root, profile["runtime"]["completions"], name="runtime.completions"
        ),
        live=live.runtime_paths(live_profile, runtime_root=root),
        live_profile_path=live_profile_path,
        replay_profile_path=replay_profile_path,
    )


def _clock_value(clock: Clock | None) -> datetime:
    try:
        value = (clock or (lambda: datetime.now(timezone.utc)))()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise VerifiedLiveIntegrityError(
                "Verified-live machine clock must be timezone-aware"
            )
        return value
    except VerifiedLiveIntegrityError:
        raise
    except Exception as exc:
        raise VerifiedLiveIntegrityError("Verified-live machine clock failed") from exc


def _utc_text(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _parse_record_utc(value: object, *, name: str) -> datetime:
    if not isinstance(value, str):
        raise VerifiedLiveIntegrityError(f"{name} must be a canonical UTC timestamp")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise VerifiedLiveIntegrityError(
            f"{name} must be a canonical UTC timestamp"
        ) from exc
    if _utc_text(parsed) != value:
        raise VerifiedLiveIntegrityError(f"{name} is not canonical UTC")
    return parsed


def _sample_machine_time_fence(
    clock: Clock | None,
    *,
    not_before: datetime,
    target: date,
    timezone_name: str,
) -> tuple[datetime, bool]:
    """Resample untrusted machine time and classify target-day eligibility."""

    sampled = _clock_value(clock)
    if sampled.astimezone(timezone.utc) < not_before.astimezone(timezone.utc):
        raise VerifiedLiveIntegrityError(
            "Local machine clock regressed during the guarded issue commit"
        )
    local_today = sampled.astimezone(ZoneInfo(timezone_name)).date()
    return sampled, target > local_today


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _acquire_runner_lock(path: Path) -> BinaryIO:
    """Acquire the shared runner lock without following its final component."""

    path.parent.mkdir(parents=True, exist_ok=True)
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    handle: BinaryIO | None = None
    try:
        descriptor = os.open(path, flags, 0o600)
        handle = os.fdopen(descriptor, "a+b")
        descriptor = None
        handle_stat = os.fstat(handle.fileno())
        path_stat = os.lstat(path)
        if (
            not stat.S_ISREG(handle_stat.st_mode)
            or not stat.S_ISREG(path_stat.st_mode)
            or (handle_stat.st_dev, handle_stat.st_ino)
            != (path_stat.st_dev, path_stat.st_ino)
        ):
            raise VerifiedLiveIntegrityError(
                "Live runner lock path does not match its regular open file"
            )
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked_path_stat = os.lstat(path)
        locked_handle_stat = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(locked_path_stat.st_mode)
            or (locked_handle_stat.st_dev, locked_handle_stat.st_ino)
            != (locked_path_stat.st_dev, locked_path_stat.st_ino)
        ):
            raise VerifiedLiveIntegrityError(
                "Live runner lock path changed while it was acquired"
            )
        return handle
    except BlockingIOError as exc:
        if handle is not None:
            handle.close()
        raise VerifiedLiveBusyError(
            "Another E2-A machine poll is already running"
        ) from exc
    except VerifiedLiveIntegrityError:
        if handle is not None:
            handle.close()
        raise
    except OSError as exc:
        if handle is not None:
            handle.close()
        raise VerifiedLiveIntegrityError("Cannot safely open live runner lock") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _atomic_write(path: Path, raw: bytes) -> None:
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
        _fsync_directory(path.parent)
    except OSError as exc:
        raise VerifiedLiveIntegrityError(f"Cannot publish {path.name}") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _atomic_create(path: Path, raw: bytes) -> bool:
    """Publish complete bytes without replacing a prior commit."""

    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.parent.parent / CREATE_SCRATCH_DIRECTORY
    try:
        scratch.mkdir(mode=0o700, parents=True, exist_ok=True)
        scratch_stat = os.lstat(scratch)
        if not stat.S_ISDIR(scratch_stat.st_mode):
            raise VerifiedLiveIntegrityError(
                "Verified-live create scratch is not a directory"
            )
    except VerifiedLiveIntegrityError:
        raise
    except OSError as exc:
        raise VerifiedLiveIntegrityError(
            "Cannot prepare verified-live create scratch"
        ) from exc
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f"{path.parent.name}.{path.name}.", suffix=".tmp", dir=scratch
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
            return False
        _fsync_directory(path.parent)
        return True
    except OSError as exc:
        raise VerifiedLiveIntegrityError(f"Cannot create {path.name}") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _load_record(path: Path, *, name: str) -> tuple[dict[str, Any], ArtifactSnapshot]:
    snapshot = _read_snapshot(path, name=name)
    try:
        payload = _decode_json(snapshot.raw, name=name)
    except VerifiedLiveConfigError as exc:
        raise VerifiedLiveIntegrityError(str(exc)) from exc
    if snapshot.raw != _canonical_bytes(payload):
        raise VerifiedLiveIntegrityError(f"{name} bytes are not canonical")
    return payload, snapshot


def _record_path(directory: Path, target: date) -> Path:
    return directory / f"{target.isoformat()}.json"


def _artifact_reference(snapshot: ArtifactSnapshot, root: Path) -> dict[str, object]:
    return snapshot.reference(root)


def _validate_artifact_reference(
    value: object, *, root: Path, name: str
) -> ArtifactSnapshot:
    if not isinstance(value, dict) or set(value) != {"path", "sha256", "size_bytes"}:
        raise VerifiedLiveIntegrityError(f"{name} artifact keys changed")
    relative = value["path"]
    if not isinstance(relative, str):
        raise VerifiedLiveIntegrityError(f"{name}.path changed")
    try:
        path = _runtime_child(root, relative, name=f"{name}.path")
    except VerifiedLiveConfigError as exc:
        raise VerifiedLiveIntegrityError(str(exc)) from exc
    snapshot = _read_snapshot(path, name=name)
    if value["sha256"] != snapshot.sha256 or value["size_bytes"] != snapshot.size_bytes:
        raise VerifiedLiveIntegrityError(f"{name} artifact changed")
    return snapshot


def _base_status(
    profile: Mapping[str, Any],
    paths: GuardRuntimePaths,
    *,
    now: datetime,
    runner_status: str,
    reason: str,
) -> dict[str, Any]:
    if runner_status not in SUCCESS_STATUSES and runner_status != "blocked_integrity":
        raise VerifiedLiveIntegrityError("Verified-live status is outside its contract")
    return {
        "schema_version": profile["protocol"]["status_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "artifact_status": profile["artifact_status"],
        "runner_status": runner_status,
        "reason": reason,
        "recorded_at_utc": _utc_text(now),
        "runtime_root": str(paths.root),
        "live_profile_sha256": LIVE_PROFILE_SHA256,
        "replay_profile_sha256": REPLAY_PROFILE_SHA256,
        "designated_entrypoint_replay_gate_implemented": True,
        "runner_independent_checkpoint_inference_replayed": False,
        "guarded_issue_completed": False,
        "old_live_v1_entrypoint_disabled": False,
        "scheduler_entrypoint_authorization_implemented": False,
        "trusted_anchor_receipt_verified": False,
        "automatic_epoch_rotation_implemented": False,
        "automatic_calibration_promotion_implemented": False,
        "formal_warning_output": False,
        "independent_label_used": False,
        "confirmatory_external_validation": False,
        "vajont_used": False,
        "e2_live_evidence_eligible": False,
        "real_activation_ready": False,
    }


def _write_status(
    profile: Mapping[str, Any],
    paths: GuardRuntimePaths,
    *,
    now: datetime,
    runner_status: str,
    reason: str,
    live_projection: live.LiveProjection | None = None,
    intent: ArtifactSnapshot | None = None,
    completion: ArtifactSnapshot | None = None,
    replay_receipt: ArtifactSnapshot | None = None,
) -> Path:
    status = _base_status(
        profile, paths, now=now, runner_status=runner_status, reason=reason
    )
    if live_projection is not None:
        events = (
            live.AppendOnlyLedger(paths.live.ledger).read_events()
            if paths.live.ledger.is_file()
            else ()
        )
        head = events[-1] if events else None
        status.update(
            {
                "live_epoch_id": live_projection.epoch_id,
                "live_ledger_event_count": len(events),
                "live_ledger_terminal_sequence_id": head.sequence_id if head else 0,
                "live_ledger_terminal_sha256": (
                    head.entry_sha256 if head else ZERO_HASH
                ),
                "last_finalized_date": live_projection.last_finalized_date.isoformat(),
                "next_target_date": (
                    live_projection.last_finalized_date + timedelta(days=1)
                ).isoformat(),
                "outstanding_target_date": (
                    live_projection.outstanding_target_date.isoformat()
                    if live_projection.outstanding_target_date is not None
                    else None
                ),
            }
        )
    for key, snapshot in (
        ("guard_intent", intent),
        ("guard_completion", completion),
        ("replay_receipt", replay_receipt),
    ):
        status[f"{key}_sha256"] = snapshot.sha256 if snapshot else None
    if completion is not None and replay_receipt is not None:
        status["runner_independent_checkpoint_inference_replayed"] = True
        status["guarded_issue_completed"] = True
    _atomic_write(paths.status, _canonical_bytes(status))
    return paths.status


def _best_effort_blocked_status(
    profile: Mapping[str, Any],
    paths: GuardRuntimePaths,
    *,
    now: datetime,
    error: BaseException,
) -> None:
    try:
        _write_status(
            profile,
            paths,
            now=now,
            runner_status="blocked_integrity",
            reason=f"{type(error).__name__}:{error}",
        )
    except Exception:
        pass


_FALSE_RECORD_FLAGS = {
    "formal_warning_output": False,
    "e2_live_evidence_eligible": False,
    "real_activation_ready": False,
    "automatic_calibration_promotion": False,
}

_INTENT_RECORD_KEYS = {
    "schema_version",
    "profile_id",
    "profile_sha256",
    "artifact_status",
    "target_date",
    "created_at_utc",
    "live_pre_head",
    "issue",
    "input_manifest",
    "model_manifest_sha256",
    "replay_receipt",
    "guard_implementation_sha256",
    "outcome_read",
    "old_live_v1_direct_entrypoint_disabled",
    *_FALSE_RECORD_FLAGS,
}

_COMPLETION_RECORD_KEYS = {
    "schema_version",
    "profile_id",
    "profile_sha256",
    "artifact_status",
    "target_date",
    "completed_at_utc",
    "intent",
    "replay_receipt",
    "live_issue_opened_sequence_id",
    "live_issue_opened_entry_sha256",
    "live_issue_seal_sequence_id",
    "live_issue_seal_entry_sha256",
    "outcome_read_before_completion",
    "old_live_v1_direct_entrypoint_disabled",
    *_FALSE_RECORD_FLAGS,
}


def _record_text(value: object, *, name: str) -> str:
    try:
        return _require_text(value, name=name)
    except VerifiedLiveConfigError as exc:
        raise VerifiedLiveIntegrityError(str(exc)) from exc


def _record_sha256(value: object, *, name: str) -> str:
    try:
        return _require_sha256(value, name=name)
    except VerifiedLiveConfigError as exc:
        raise VerifiedLiveIntegrityError(str(exc)) from exc


def _record_positive_int(value: object, *, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise VerifiedLiveIntegrityError(f"{name} must be a positive integer")
    return value


def _pre_head(events: tuple[live.LedgerEvent, ...], epoch_id: str) -> dict[str, object]:
    head = events[-1] if events else None
    return {
        "epoch_id": epoch_id,
        "event_count": len(events),
        "sequence_id": head.sequence_id if head is not None else 0,
        "entry_sha256": head.entry_sha256 if head is not None else ZERO_HASH,
    }


def _issue_snapshot(issue: live.IssueBatch) -> ArtifactSnapshot:
    snapshot = _read_snapshot(issue.path, name="pending live issue")
    if snapshot.sha256 != issue.sha256:
        raise VerifiedLiveIntegrityError("Pending issue changed after live-v1 parsing")
    return snapshot


def _input_snapshot(issue: live.IssueBatch) -> ArtifactSnapshot:
    snapshot = _read_snapshot(issue.input_manifest.path, name="issue input manifest")
    if (
        snapshot.sha256 != issue.input_manifest.sha256
        or snapshot.size_bytes != issue.input_manifest.size_bytes
    ):
        raise VerifiedLiveIntegrityError("Issue input manifest changed after parsing")
    return snapshot


def _validate_replay_receipt_identity(
    receipt: ArtifactSnapshot,
    *,
    root: Path,
    target: date,
    expected_pre_head: Mapping[str, object],
    issue: ArtifactSnapshot,
    input_manifest: ArtifactSnapshot,
) -> dict[str, Any]:
    try:
        payload = _decode_json(receipt.raw, name="issue replay receipt")
    except VerifiedLiveConfigError as exc:
        raise VerifiedLiveIntegrityError(str(exc)) from exc
    expected_keys = {
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
    if set(payload) != expected_keys:
        raise VerifiedLiveIntegrityError("Issue replay receipt top-level keys changed")
    if receipt.raw != _canonical_bytes(payload):
        raise VerifiedLiveIntegrityError("Issue replay receipt bytes are not canonical")
    _parse_record_utc(
        payload.get("verified_at_utc"), name="replay_receipt.verified_at_utc"
    )
    if (
        payload["schema_version"] != "ootang_issue_replay_receipt_v1"
        or payload["profile_id"] != "ootang-issue-replay-v1"
        or payload["artifact_status"]
        != "e2_checkpoint_input_replay_engineering_only_not_live_evidence"
        or payload["target_date"] != target.isoformat()
        or payload["ledger_pre_head"] != dict(expected_pre_head)
        or any(payload[key] is not value for key, value in _FALSE_RECORD_FLAGS.items())
    ):
        raise VerifiedLiveIntegrityError("Issue replay receipt identity changed")
    issue_ref = payload["issue"]
    input_ref = payload["input_manifest"]
    if not isinstance(issue_ref, dict) or not isinstance(input_ref, dict):
        raise VerifiedLiveIntegrityError("Replay receipt artifact references changed")
    for record, snapshot, name in (
        (issue_ref, issue, "issue"),
        (input_ref, input_manifest, "input_manifest"),
    ):
        if set(record) != {"path", "sha256", "size_bytes"}:
            raise VerifiedLiveIntegrityError(f"Replay receipt {name} keys changed")
        if (
            record["sha256"] != snapshot.sha256
            or record["size_bytes"] != snapshot.size_bytes
        ):
            raise VerifiedLiveIntegrityError(f"Replay receipt {name} binding changed")
        path = record["path"]
        if not isinstance(path, str):
            raise VerifiedLiveIntegrityError(f"Replay receipt {name} path changed")
        recorded_path = Path(path)
        if not recorded_path.is_absolute():
            recorded_path = root / recorded_path
        try:
            recorded_identity = (
                _lexical_absolute(recorded_path.parent.resolve())
                / recorded_path.name
            )
            snapshot_identity = (
                _lexical_absolute(snapshot.path.parent.resolve())
                / snapshot.path.name
            )
            if recorded_identity != snapshot_identity:
                raise VerifiedLiveIntegrityError(
                    f"Replay receipt {name} path does not match the captured artifact"
                )
        except (OSError, RuntimeError, ValueError) as exc:
            raise VerifiedLiveIntegrityError(
                f"Replay receipt {name} path cannot be resolved"
            ) from exc
    return payload


def _load_public_replay_receipt(
    paths: GuardRuntimePaths,
    *,
    project_root: Path,
    target: date,
    expected_pre_head: Mapping[str, object],
    issue_sha256: str,
    input_manifest_sha256: str,
    model_manifest_sha256: str,
) -> ArtifactSnapshot:
    try:
        from monitoring import ootang_issue_replay as replay

        verified = replay.load_verified_replay_receipt(
            target,
            config_path=paths.replay_profile_path,
            runtime_root=paths.root,
            project_root=project_root,
            expected_issue_sha256=issue_sha256,
            expected_input_manifest_sha256=input_manifest_sha256,
            expected_model_manifest_sha256=model_manifest_sha256,
            expected_ledger_pre_head=expected_pre_head,
        )
    except ImportError as exc:
        raise VerifiedLiveConfigError(
            "Independent replay receipt loader is unavailable"
        ) from exc
    except Exception as exc:
        try:
            from monitoring import ootang_issue_replay as replay

            if isinstance(exc, replay.IssueReplayError):
                raise VerifiedLiveIntegrityError(
                    f"Independent replay receipt failed validation: {exc}"
                ) from exc
        except ImportError:
            pass
        raise
    snapshot = _read_snapshot(verified.path, name="issue replay receipt")
    if snapshot.sha256 != verified.sha256 or snapshot.size_bytes != verified.size_bytes:
        raise VerifiedLiveIntegrityError(
            "Replay receipt changed after public validation"
        )
    return snapshot


def _validate_intent_record(
    profile: Mapping[str, Any],
    paths: GuardRuntimePaths,
    *,
    target: date,
    payload: Mapping[str, Any],
    expected_public_replay: ArtifactSnapshot | None = None,
) -> tuple[ArtifactSnapshot, ArtifactSnapshot, ArtifactSnapshot]:
    """Validate an intent without requiring a current live outstanding issue."""

    if set(payload) != _INTENT_RECORD_KEYS:
        raise VerifiedLiveIntegrityError("Verified-live intent keys changed")
    if (
        payload.get("schema_version") != profile["protocol"]["intent_schema_version"]
        or payload.get("profile_id") != profile["profile_id"]
        or payload.get("profile_sha256") != profile["_profile_sha256"]
        or payload.get("artifact_status") != profile["artifact_status"]
        or payload.get("target_date") != target.isoformat()
        or payload.get("outcome_read") is not False
        or payload.get("old_live_v1_direct_entrypoint_disabled") is not False
        or any(
            payload.get(key) is not value
            for key, value in _FALSE_RECORD_FLAGS.items()
        )
    ):
        raise VerifiedLiveIntegrityError("Verified-live intent identity changed")

    created_at = _parse_record_utc(
        payload.get("created_at_utc"), name="intent.created_at_utc"
    )
    model_manifest_sha256 = _record_sha256(
        payload.get("model_manifest_sha256"),
        name="intent.model_manifest_sha256",
    )
    implementation_sha256 = _record_sha256(
        payload.get("guard_implementation_sha256"),
        name="intent.guard_implementation_sha256",
    )
    if implementation_sha256 != _read_snapshot(
        Path(__file__), name="verified-live implementation"
    ).sha256:
        raise VerifiedLiveIntegrityError(
            "Verified-live intent implementation binding changed"
        )

    pre_head = payload.get("live_pre_head")
    if not isinstance(pre_head, dict) or set(pre_head) != {
        "epoch_id",
        "event_count",
        "sequence_id",
        "entry_sha256",
    }:
        raise VerifiedLiveIntegrityError("Guard intent live pre-head changed")
    _record_text(pre_head.get("epoch_id"), name="intent.live_pre_head.epoch_id")
    event_count = _record_positive_int(
        pre_head.get("event_count"), name="intent.live_pre_head.event_count"
    )
    sequence_id = _record_positive_int(
        pre_head.get("sequence_id"), name="intent.live_pre_head.sequence_id"
    )
    entry_sha256 = _record_sha256(
        pre_head.get("entry_sha256"), name="intent.live_pre_head.entry_sha256"
    )
    if event_count != sequence_id or entry_sha256 == ZERO_HASH:
        raise VerifiedLiveIntegrityError("Guard intent live pre-head is inconsistent")

    issue = _validate_artifact_reference(
        payload.get("issue"), root=paths.root, name="intent.issue"
    )
    input_manifest = _validate_artifact_reference(
        payload.get("input_manifest"),
        root=paths.root,
        name="intent.input_manifest",
    )
    replay_receipt = _validate_artifact_reference(
        payload.get("replay_receipt"),
        root=paths.root,
        name="intent.replay_receipt",
    )
    replay_payload = _validate_replay_receipt_identity(
        replay_receipt,
        root=paths.root,
        target=target,
        expected_pre_head=pre_head,
        issue=issue,
        input_manifest=input_manifest,
    )
    replay_verified_at = _parse_record_utc(
        replay_payload.get("verified_at_utc"),
        name="replay_receipt.verified_at_utc",
    )
    if replay_verified_at > created_at:
        raise VerifiedLiveIntegrityError(
            "Guard intent predates its independent replay receipt"
        )

    public_replay = expected_public_replay or _load_public_replay_receipt(
        paths,
        project_root=Path(str(profile["_project_root"])),
        target=target,
        expected_pre_head=pre_head,
        issue_sha256=issue.sha256,
        input_manifest_sha256=input_manifest.sha256,
        model_manifest_sha256=model_manifest_sha256,
    )
    if (
        public_replay.path != replay_receipt.path
        or public_replay.sha256 != replay_receipt.sha256
        or public_replay.size_bytes != replay_receipt.size_bytes
    ):
        raise VerifiedLiveIntegrityError("Intent links a different replay receipt")
    return issue, input_manifest, replay_receipt


def _validate_pre_head_against_events(
    pre_head: Mapping[str, Any],
    events: tuple[live.LedgerEvent, ...],
) -> None:
    if not events:
        raise VerifiedLiveIntegrityError(
            "Guard intent exists without a verified live ledger"
        )
    sequence_id = int(pre_head["sequence_id"])
    if sequence_id > len(events):
        raise VerifiedLiveIntegrityError("Guard intent pre-head is beyond the ledger")
    event = events[sequence_id - 1]
    epoch_id = events[0].payload.get("live_epoch_id")
    if (
        event.sequence_id != sequence_id
        or event.entry_sha256 != pre_head["entry_sha256"]
        or epoch_id != pre_head["epoch_id"]
    ):
        raise VerifiedLiveIntegrityError(
            "Guard intent pre-head does not match the verified live ledger"
        )


def _validate_completion_record(
    profile: Mapping[str, Any],
    paths: GuardRuntimePaths,
    *,
    target: date,
    payload: Mapping[str, Any],
    intent_payload: Mapping[str, Any],
    intent_snapshot: ArtifactSnapshot,
    events: tuple[live.LedgerEvent, ...],
) -> tuple[ArtifactSnapshot, ArtifactSnapshot]:
    if set(payload) != _COMPLETION_RECORD_KEYS:
        raise VerifiedLiveIntegrityError("Guard completion keys changed")
    if (
        payload.get("schema_version")
        != profile["protocol"]["completion_schema_version"]
        or payload.get("profile_id") != profile["profile_id"]
        or payload.get("profile_sha256") != profile["_profile_sha256"]
        or payload.get("artifact_status") != profile["artifact_status"]
        or payload.get("target_date") != target.isoformat()
        or payload.get("outcome_read_before_completion") is not False
        or payload.get("old_live_v1_direct_entrypoint_disabled") is not False
        or any(
            payload.get(key) is not value
            for key, value in _FALSE_RECORD_FLAGS.items()
        )
    ):
        raise VerifiedLiveIntegrityError("Guard completion identity changed")

    completed_at = _parse_record_utc(
        payload.get("completed_at_utc"), name="completion.completed_at_utc"
    )
    created_at = _parse_record_utc(
        intent_payload.get("created_at_utc"), name="intent.created_at_utc"
    )
    if completed_at < created_at:
        raise VerifiedLiveIntegrityError("Guard completion predates its intent")

    opened_sequence = _record_positive_int(
        payload.get("live_issue_opened_sequence_id"),
        name="completion.live_issue_opened_sequence_id",
    )
    seal_sequence = _record_positive_int(
        payload.get("live_issue_seal_sequence_id"),
        name="completion.live_issue_seal_sequence_id",
    )
    opened_sha256 = _record_sha256(
        payload.get("live_issue_opened_entry_sha256"),
        name="completion.live_issue_opened_entry_sha256",
    )
    seal_sha256 = _record_sha256(
        payload.get("live_issue_seal_entry_sha256"),
        name="completion.live_issue_seal_entry_sha256",
    )
    pre_head = intent_payload.get("live_pre_head")
    if not isinstance(pre_head, dict):
        raise VerifiedLiveIntegrityError("Guard intent live pre-head changed")
    if (
        opened_sequence != pre_head["sequence_id"] + 1
        or seal_sequence <= opened_sequence
        or opened_sha256 == ZERO_HASH
        or seal_sha256 == ZERO_HASH
    ):
        raise VerifiedLiveIntegrityError("Guard completion event boundary changed")

    checked_intent = _validate_artifact_reference(
        payload.get("intent"), root=paths.root, name="completion.intent"
    )
    checked_replay = _validate_artifact_reference(
        payload.get("replay_receipt"),
        root=paths.root,
        name="completion.replay_receipt",
    )
    if (
        checked_intent.path != intent_snapshot.path
        or checked_intent.sha256 != intent_snapshot.sha256
        or checked_intent.size_bytes != intent_snapshot.size_bytes
    ):
        raise VerifiedLiveIntegrityError("Completion links a different intent")
    expected_replay = intent_payload.get("replay_receipt")
    if (
        not isinstance(expected_replay, dict)
        or payload.get("replay_receipt") != expected_replay
        or checked_replay.sha256 != expected_replay.get("sha256")
        or checked_replay.size_bytes != expected_replay.get("size_bytes")
    ):
        raise VerifiedLiveIntegrityError(
            "Completion links a different replay receipt"
        )

    opened, seal = _live_issue_events(events, target)
    seal_recorded_at = _parse_record_utc(
        seal.recorded_at_utc, name="live.issue_batch_sealed.recorded_at_utc"
    )
    if completed_at < seal_recorded_at:
        raise VerifiedLiveIntegrityError(
            "Guard completion predates its live issue seal"
        )
    issue_ref = intent_payload["issue"]
    input_ref = intent_payload["input_manifest"]
    if (
        opened.sequence_id != opened_sequence
        or opened.entry_sha256 != opened_sha256
        or opened.previous_entry_sha256 != pre_head["entry_sha256"]
        or seal.sequence_id != seal_sequence
        or seal.entry_sha256 != seal_sha256
        or opened.payload.get("issue_feed_sha256") != issue_ref["sha256"]
        or opened.input_manifest_sha256 != input_ref["sha256"]
        or seal.input_manifest_sha256 != input_ref["sha256"]
        or opened.model_manifest_sha256
        != intent_payload["model_manifest_sha256"]
        or seal.model_manifest_sha256 != intent_payload["model_manifest_sha256"]
    ):
        raise VerifiedLiveIntegrityError(
            "Guard completion does not match verified live ledger events"
        )
    _validate_issue_event_times(
        paths,
        target=target,
        intent_payload=intent_payload,
        opened=opened,
        seal=seal,
    )
    return checked_intent, checked_replay


def _validate_issue_event_times(
    paths: GuardRuntimePaths,
    *,
    target: date,
    intent_payload: Mapping[str, Any],
    opened: live.LedgerEvent,
    seal: live.LedgerEvent,
) -> None:
    created_at = _parse_record_utc(
        intent_payload.get("created_at_utc"), name="intent.created_at_utc"
    )
    opened_at = _parse_record_utc(
        opened.recorded_at_utc, name="live.issue_batch_opened.recorded_at_utc"
    )
    seal_at = _parse_record_utc(
        seal.recorded_at_utc, name="live.issue_batch_sealed.recorded_at_utc"
    )
    live_profile = live.load_config(paths.live_profile_path)
    target_start = datetime.combine(
        target,
        time.min,
        tzinfo=ZoneInfo(live_profile["target"]["date_timezone"]),
    ).astimezone(timezone.utc)
    if opened_at < created_at or seal_at < opened_at or seal_at >= target_start:
        raise VerifiedLiveIntegrityError(
            "Live issue event times violate the pre-target guard intent boundary"
        )


def _ensure_intent(
    profile: Mapping[str, Any],
    paths: GuardRuntimePaths,
    *,
    now: datetime,
    projection: live.LiveProjection,
    events: tuple[live.LedgerEvent, ...],
    issue_batch: live.IssueBatch,
    replay_receipt: ArtifactSnapshot,
) -> ArtifactSnapshot:
    target = issue_batch.target_date
    issue = _issue_snapshot(issue_batch)
    input_manifest = _input_snapshot(issue_batch)
    stable = {
        "schema_version": profile["protocol"]["intent_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "artifact_status": profile["artifact_status"],
        "target_date": target.isoformat(),
        "live_pre_head": _pre_head(events, projection.epoch_id),
        "issue": _artifact_reference(issue, paths.root),
        "input_manifest": _artifact_reference(input_manifest, paths.root),
        "model_manifest_sha256": issue_batch.model_manifest_sha256,
        "replay_receipt": _artifact_reference(replay_receipt, paths.root),
        "guard_implementation_sha256": _read_snapshot(
            Path(__file__), name="verified-live implementation"
        ).sha256,
        "outcome_read": False,
        "old_live_v1_direct_entrypoint_disabled": False,
        **_FALSE_RECORD_FLAGS,
    }
    path = _record_path(paths.intents, target)
    if path.exists():
        existing, snapshot = _load_record(path, name="verified-live intent")
        if set(existing) != {*stable, "created_at_utc"}:
            raise VerifiedLiveIntegrityError("Verified-live intent keys changed")
        comparable = {
            key: value for key, value in existing.items() if key != "created_at_utc"
        }
        if comparable != stable:
            raise VerifiedLiveIntegrityError("Verified-live intent changed semantics")
        return snapshot
    payload = {**stable, "created_at_utc": _utc_text(now)}
    raw = _canonical_bytes(payload)
    if not _atomic_create(path, raw):
        existing, snapshot = _load_record(path, name="verified-live intent")
        comparable = {
            key: value for key, value in existing.items() if key != "created_at_utc"
        }
        if set(existing) != {*stable, "created_at_utc"} or comparable != stable:
            raise VerifiedLiveIntegrityError(
                "Concurrent verified-live intent changed semantics"
            )
        return snapshot
    return _read_snapshot(path, name="verified-live intent")


def _load_intent(
    paths: GuardRuntimePaths, target: date
) -> tuple[dict[str, Any], ArtifactSnapshot]:
    path = _record_path(paths.intents, target)
    if not path.is_file():
        raise VerifiedLiveIntegrityError(
            "Outstanding direct live-v1 issue has no pre-seal guard intent; "
            "retroactive authorization is forbidden"
        )
    payload, snapshot = _load_record(path, name="verified-live intent")
    if set(payload) != _INTENT_RECORD_KEYS:
        raise VerifiedLiveIntegrityError("Verified-live intent keys changed")
    return payload, snapshot


def _live_issue_events(
    events: tuple[live.LedgerEvent, ...], target: date
) -> tuple[live.LedgerEvent, live.LedgerEvent]:
    target_text = target.isoformat()
    opened = [
        event
        for event in events
        if event.target_date == target_text and event.event_type == "issue_batch_opened"
    ]
    seals = [
        event
        for event in events
        if event.target_date == target_text and event.event_type == "issue_batch_sealed"
    ]
    if len(opened) != 1 or len(seals) != 1:
        raise VerifiedLiveIntegrityError("Outstanding live issue boundary changed")
    return opened[0], seals[0]


def _validate_intent_against_live(
    profile: Mapping[str, Any],
    paths: GuardRuntimePaths,
    *,
    projection: live.LiveProjection,
    events: tuple[live.LedgerEvent, ...],
    intent: Mapping[str, Any],
) -> ArtifactSnapshot:
    target = projection.outstanding_target_date
    if target is None or projection.seal_event is None:
        raise VerifiedLiveIntegrityError(
            "Cannot link a guard intent without an issue seal"
        )
    issue, input_manifest, receipt = _validate_intent_record(
        profile,
        paths,
        target=target,
        payload=intent,
    )
    opened, seal = _live_issue_events(events, target)
    pre_head = intent.get("live_pre_head")
    if not isinstance(pre_head, dict):
        raise VerifiedLiveIntegrityError("Guard intent live pre-head changed")
    if (
        pre_head["epoch_id"] != projection.epoch_id
        or opened.sequence_id != pre_head["sequence_id"] + 1
        or opened.previous_entry_sha256 != pre_head["entry_sha256"]
        or pre_head["event_count"] != pre_head["sequence_id"]
        or seal.entry_sha256 != projection.seal_event.entry_sha256
    ):
        raise VerifiedLiveIntegrityError(
            "Live issue does not immediately follow its guard intent"
        )
    if (
        opened.payload.get("issue_feed_sha256") != issue.sha256
        or opened.input_manifest_sha256 != input_manifest.sha256
        or seal.input_manifest_sha256 != input_manifest.sha256
        or opened.model_manifest_sha256 != intent.get("model_manifest_sha256")
        or seal.model_manifest_sha256 != intent.get("model_manifest_sha256")
    ):
        raise VerifiedLiveIntegrityError(
            "Live issue envelope differs from its guard intent"
        )
    _validate_issue_event_times(
        paths,
        target=target,
        intent_payload=intent,
        opened=opened,
        seal=seal,
    )
    return receipt


def _ensure_completion(
    profile: Mapping[str, Any],
    paths: GuardRuntimePaths,
    *,
    now: datetime,
    projection: live.LiveProjection,
    events: tuple[live.LedgerEvent, ...],
) -> tuple[ArtifactSnapshot, ArtifactSnapshot, ArtifactSnapshot]:
    target = projection.outstanding_target_date
    if target is None:
        raise VerifiedLiveIntegrityError("No outstanding issue can be completed")
    intent_payload, intent_snapshot = _load_intent(paths, target)
    replay_receipt = _validate_intent_against_live(
        profile,
        paths,
        projection=projection,
        events=events,
        intent=intent_payload,
    )
    opened, seal = _live_issue_events(events, target)
    stable = {
        "schema_version": profile["protocol"]["completion_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "artifact_status": profile["artifact_status"],
        "target_date": target.isoformat(),
        "intent": _artifact_reference(intent_snapshot, paths.root),
        "replay_receipt": _artifact_reference(replay_receipt, paths.root),
        "live_issue_opened_sequence_id": opened.sequence_id,
        "live_issue_opened_entry_sha256": opened.entry_sha256,
        "live_issue_seal_sequence_id": seal.sequence_id,
        "live_issue_seal_entry_sha256": seal.entry_sha256,
        "outcome_read_before_completion": False,
        "old_live_v1_direct_entrypoint_disabled": False,
        **_FALSE_RECORD_FLAGS,
    }
    path = _record_path(paths.completions, target)

    def validated_result(
        payload: Mapping[str, Any], snapshot: ArtifactSnapshot
    ) -> tuple[ArtifactSnapshot, ArtifactSnapshot, ArtifactSnapshot]:
        _, checked_replay = _validate_completion_record(
            profile,
            paths,
            target=target,
            payload=payload,
            intent_payload=intent_payload,
            intent_snapshot=intent_snapshot,
            events=events,
        )
        if (
            checked_replay.path != replay_receipt.path
            or checked_replay.sha256 != replay_receipt.sha256
            or checked_replay.size_bytes != replay_receipt.size_bytes
        ):
            raise VerifiedLiveIntegrityError(
                "Guard completion replay link changed during validation"
            )
        return intent_snapshot, snapshot, replay_receipt

    if path.exists():
        existing, snapshot = _load_record(path, name="verified-live completion")
        comparable = {
            key: value for key, value in existing.items() if key != "completed_at_utc"
        }
        if set(existing) != {*stable, "completed_at_utc"} or comparable != stable:
            raise VerifiedLiveIntegrityError(
                "Verified-live completion changed semantics"
            )
        return validated_result(existing, snapshot)
    created_at = _parse_record_utc(
        intent_payload.get("created_at_utc"), name="intent.created_at_utc"
    )
    seal_recorded_at = _parse_record_utc(
        seal.recorded_at_utc, name="live.issue_batch_sealed.recorded_at_utc"
    )
    if (
        not isinstance(now, datetime)
        or now.tzinfo is None
        or now.utcoffset() is None
        or now.astimezone(timezone.utc) < created_at
        or now.astimezone(timezone.utc) < seal_recorded_at
    ):
        raise VerifiedLiveIntegrityError(
            "Guard completion machine time predates its intent or live issue seal"
        )
    created_payload = {**stable, "completed_at_utc": _utc_text(now)}
    raw = _canonical_bytes(created_payload)
    if not _atomic_create(path, raw):
        existing, snapshot = _load_record(path, name="verified-live completion")
        comparable = {
            key: value for key, value in existing.items() if key != "completed_at_utc"
        }
        if set(existing) != {*stable, "completed_at_utc"} or comparable != stable:
            raise VerifiedLiveIntegrityError(
                "Concurrent verified-live completion changed semantics"
            )
        return validated_result(existing, snapshot)
    return validated_result(
        created_payload,
        _read_snapshot(path, name="verified-live completion"),
    )


def _run_replay_under_lock(
    paths: GuardRuntimePaths,
    *,
    runner_lock_handle: Any,
    project_root: Path,
    clock: Clock | None,
) -> tuple[date, ArtifactSnapshot]:
    try:
        from monitoring import ootang_issue_replay as replay

        result = replay.verify_pending_issue_under_runner_lock(
            runner_lock_handle=runner_lock_handle,
            config_path=paths.replay_profile_path,
            runtime_root=paths.root,
            project_root=project_root,
            clock=clock,
        )
    except ImportError as exc:
        raise VerifiedLiveConfigError(
            "Independent issue replay implementation is unavailable"
        ) from exc
    except Exception as exc:
        try:
            from monitoring import ootang_issue_replay as replay

            if isinstance(exc, replay.IssueReplayBusyError):
                raise VerifiedLiveBusyError(str(exc)) from exc
            if isinstance(exc, replay.IssueReplayError):
                raise VerifiedLiveIntegrityError(
                    f"Independent issue replay failed: {exc}"
                ) from exc
        except ImportError:
            pass
        raise
    if result.status not in {"verified", "already_verified_idempotent"}:
        raise VerifiedLiveIntegrityError(
            f"Independent issue replay did not verify the pending issue: {result.status}"
        )
    if not isinstance(result.target_date, date) or result.receipt_path is None:
        raise VerifiedLiveIntegrityError(
            "Independent replay returned no durable receipt"
        )
    receipt = _read_snapshot(result.receipt_path, name="issue replay receipt")
    try:
        receipt.reference(paths.root)
    except VerifiedLiveIntegrityError as exc:
        raise VerifiedLiveIntegrityError(
            "Issue replay receipt escaped the shared runtime root"
        ) from exc
    return result.target_date, receipt


def _write_live_status(
    profile: dict[str, Any],
    paths: GuardRuntimePaths,
    prerequisites: live.Prerequisites,
    ledger: live.AppendOnlyLedger,
    projection: live.LiveProjection,
    *,
    now: datetime,
    runner_status: str,
    reason: str,
) -> None:
    live._write_active_status(
        profile,
        paths.live,
        prerequisites,
        ledger,
        projection,
        now=now,
        runner_status=runner_status,
        reason=reason,
    )


def _load_guarded_outcome_batch(
    path: Path,
    profile: dict[str, Any],
    prerequisites: live.Prerequisites,
) -> live.OutcomeBatch:
    """Parse an outcome only while its exact final pathname remains stable."""

    before = _read_snapshot(path, name="live outcome batch")
    outcome = live.load_outcome_batch(path, profile, prerequisites)
    after = _read_snapshot(path, name="live outcome batch")
    if (
        after.path != before.path
        or after.sha256 != before.sha256
        or after.size_bytes != before.size_bytes
        or after.raw != before.raw
    ):
        raise VerifiedLiveIntegrityError(
            "Live outcome batch changed while guarded parsing was in progress"
        )
    if outcome.sha256 != before.sha256:
        raise VerifiedLiveIntegrityError(
            "Base live outcome parser consumed different bytes"
        )
    return outcome


def _process_one_revision(
    ledger: live.AppendOnlyLedger,
    paths: GuardRuntimePaths,
    profile: dict[str, Any],
    prerequisites: live.Prerequisites,
    projection: live.LiveProjection,
) -> bool:
    """Append at most one already-settled revision transaction."""

    originals = {**projection.backfill_events, **projection.settled_events}
    for target_key, original in sorted(originals.items()):
        path = paths.live.outcome_inbox / f"{target_key}.json"
        if not path.is_file():
            continue
        outcome = _load_guarded_outcome_batch(path, profile, prerequisites)
        if outcome.sha256 == original.payload.get("outcome_batch_sha256"):
            continue
        if outcome.target_date.isoformat() != target_key:
            raise VerifiedLiveIntegrityError("Revised outcome filename/date mismatch")
        if target_key in projection.settled_events:
            live._append_revision(ledger, profile, prerequisites, projection, outcome)
        else:
            live._append_backfill_revision(
                ledger, profile, prerequisites, projection, outcome
            )
        return True
    return False


def _work_remaining(
    guard_profile: Mapping[str, Any],
    paths: GuardRuntimePaths,
    *,
    now: datetime,
    reason: str,
    projection: live.LiveProjection,
    intent: ArtifactSnapshot | None = None,
    completion: ArtifactSnapshot | None = None,
    replay_receipt: ArtifactSnapshot | None = None,
) -> Path:
    return _write_status(
        guard_profile,
        paths,
        now=now,
        runner_status="work_remaining",
        reason=reason,
        live_projection=projection,
        intent=intent,
        completion=completion,
        replay_receipt=replay_receipt,
    )


def _poll_locked(
    guard_profile: dict[str, Any],
    paths: GuardRuntimePaths,
    *,
    now: datetime,
    clock: Clock | None,
    runner_lock_handle: Any,
    anchor_client: live.AnchorClient | None,
    project_root: Path,
) -> Path:
    live_profile = live.load_config(paths.live_profile_path)
    prerequisites = live.load_prerequisites(live_profile, paths.live)
    if prerequisites is None:
        if paths.live.ledger.exists():
            raise VerifiedLiveIntegrityError(
                "Activated live ledger exists but an epoch prerequisite disappeared"
            )
        live._write_waiting_for_prerequisites(live_profile, paths.live, now=now)
        return _write_status(
            guard_profile,
            paths,
            now=now,
            runner_status="waiting_for_live_prerequisites",
            reason="live_v1_prerequisites_are_not_ready",
        )

    live._validate_activation_time(live_profile, prerequisites, now=now)
    ledger = live.AppendOnlyLedger(paths.live.ledger)
    if ledger.head() is None:
        live._append_genesis(ledger, live_profile, prerequisites)
        events = ledger.read_events()
        projection = live._reconstruct_projection(events, live_profile, prerequisites)
        _write_live_status(
            live_profile,
            paths,
            prerequisites,
            ledger,
            projection,
            now=now,
            runner_status="waiting_for_new_data",
            reason="verified_entrypoint_created_live_genesis",
        )
        return _work_remaining(
            guard_profile,
            paths,
            now=now,
            reason="live_genesis_created",
            projection=projection,
        )

    events = ledger.read_events()
    projection = live._reconstruct_projection(events, live_profile, prerequisites)
    live._repair_and_verify_anchor_receipts(paths.live, events)

    # An outstanding issue is handled before *any* outcome/revision read.  The
    # completion is either already exact, recoverable from its durable pre-seal
    # intent, or the issue came from the bypassable old v1 entrypoint and blocks.
    if projection.outstanding_target_date is not None:
        target = projection.outstanding_target_date
        completion_path = _record_path(paths.completions, target)
        completion_existed = completion_path.is_file()
        intent, completion, replay_receipt = _ensure_completion(
            guard_profile,
            paths,
            now=now,
            projection=projection,
            events=events,
        )
        if not completion_existed:
            _write_live_status(
                live_profile,
                paths,
                prerequisites,
                ledger,
                projection,
                now=now,
                runner_status="waiting_for_outcome",
                reason="guard_completion_recovered_before_outcome_read",
            )
            return _work_remaining(
                guard_profile,
                paths,
                now=now,
                reason="guard_completion_recovered_after_live_issue_commit",
                projection=projection,
                intent=intent,
                completion=completion,
                replay_receipt=replay_receipt,
            )

        if _process_one_revision(
            ledger, paths, live_profile, prerequisites, projection
        ):
            projection = live._reconstruct_projection(
                ledger.read_events(), live_profile, prerequisites
            )
            _write_live_status(
                live_profile,
                paths,
                prerequisites,
                ledger,
                projection,
                now=now,
                runner_status="waiting_for_outcome",
                reason="historical_outcome_revisions_applied",
            )
            return _work_remaining(
                guard_profile,
                paths,
                now=now,
                reason="historical_outcome_revisions_applied",
                projection=projection,
                intent=intent,
                completion=completion,
                replay_receipt=replay_receipt,
            )

        before_anchor = ledger.head()
        live._attempt_anchor(
            ledger,
            paths.live,
            live_profile,
            prerequisites,
            projection,
            anchor_client=anchor_client,
        )
        after_anchor = ledger.head()
        if after_anchor != before_anchor:
            projection = live._reconstruct_projection(
                ledger.read_events(), live_profile, prerequisites
            )
            _write_live_status(
                live_profile,
                paths,
                prerequisites,
                ledger,
                projection,
                now=now,
                runner_status="waiting_for_outcome",
                reason="guarded_issue_anchor_attempt_recorded",
            )
            return _write_status(
                guard_profile,
                paths,
                now=now,
                runner_status="waiting_for_outcome",
                reason="guarded_issue_anchor_attempt_recorded_operational_only",
                live_projection=projection,
                intent=intent,
                completion=completion,
                replay_receipt=replay_receipt,
            )

        outcome_path = paths.live.outcome_inbox / f"{target.isoformat()}.json"
        if not outcome_path.is_file():
            _write_live_status(
                live_profile,
                paths,
                prerequisites,
                ledger,
                projection,
                now=now,
                runner_status="waiting_for_outcome",
                reason="guarded_issue_has_no_finalized_outcome",
            )
            return _write_status(
                guard_profile,
                paths,
                now=now,
                runner_status="waiting_for_outcome",
                reason="guard_completion_precedes_outcome_wait",
                live_projection=projection,
                intent=intent,
                completion=completion,
                replay_receipt=replay_receipt,
            )
        outcome = _load_guarded_outcome_batch(
            outcome_path, live_profile, prerequisites
        )
        live._validate_outcome_ready(outcome, now=now)
        live._append_outcome_batch(
            ledger, live_profile, prerequisites, projection, outcome
        )
        projection = live._reconstruct_projection(
            ledger.read_events(), live_profile, prerequisites
        )
        _write_live_status(
            live_profile,
            paths,
            prerequisites,
            ledger,
            projection,
            now=now,
            runner_status="waiting_for_new_data",
            reason="guarded_outcome_settled",
        )
        return _work_remaining(
            guard_profile,
            paths,
            now=now,
            reason="guarded_outcome_settled",
            projection=projection,
            intent=intent,
            completion=completion,
            replay_receipt=replay_receipt,
        )

    # With no outstanding issue, prior revisions are safe to consume.  Return
    # after they change state so this invocation cannot also seal an issue.
    if _process_one_revision(ledger, paths, live_profile, prerequisites, projection):
        projection = live._reconstruct_projection(
            ledger.read_events(), live_profile, prerequisites
        )
        _write_live_status(
            live_profile,
            paths,
            prerequisites,
            ledger,
            projection,
            now=now,
            runner_status="waiting_for_new_data",
            reason="historical_outcome_revisions_applied",
        )
        return _work_remaining(
            guard_profile,
            paths,
            now=now,
            reason="historical_outcome_revisions_applied",
            projection=projection,
        )

    expected = projection.last_finalized_date + timedelta(days=1)
    issue_path = paths.live.issue_inbox / f"{expected.isoformat()}.json"
    outcome_path = paths.live.outcome_inbox / f"{expected.isoformat()}.json"
    if outcome_path.is_file():
        outcome = _load_guarded_outcome_batch(
            outcome_path, live_profile, prerequisites
        )
        live._validate_outcome_ready(outcome, now=now)
        live._append_backfill(ledger, live_profile, prerequisites, projection, outcome)
        projection = live._reconstruct_projection(
            ledger.read_events(), live_profile, prerequisites
        )
        _write_live_status(
            live_profile,
            paths,
            prerequisites,
            ledger,
            projection,
            now=now,
            runner_status="waiting_for_new_data",
            reason="one_contiguous_backfill_applied",
        )
        return _work_remaining(
            guard_profile,
            paths,
            now=now,
            reason="one_contiguous_backfill_applied",
            projection=projection,
        )
    if not issue_path.is_file():
        status = (
            "waiting_for_missing_natural_day"
            if live._has_later_dated_input(paths.live, expected)
            else "waiting_for_new_data"
        )
        reason = (
            "later_input_exists_but_next_natural_day_is_missing"
            if status == "waiting_for_missing_natural_day"
            else "no_issue_or_outcome_for_next_natural_day"
        )
        _write_live_status(
            live_profile,
            paths,
            prerequisites,
            ledger,
            projection,
            now=now,
            runner_status=status,
            reason=reason,
        )
        return _write_status(
            guard_profile,
            paths,
            now=now,
            runner_status=status,
            reason=reason,
            live_projection=projection,
        )

    local_today = now.astimezone(
        ZoneInfo(live_profile["target"]["date_timezone"])
    ).date()
    if expected <= local_today:
        _write_live_status(
            live_profile,
            paths,
            prerequisites,
            ledger,
            projection,
            now=now,
            runner_status="waiting_for_backfill_outcome",
            reason="historical_target_cannot_be_retroactively_issued",
        )
        return _write_status(
            guard_profile,
            paths,
            now=now,
            runner_status="waiting_for_backfill_outcome",
            reason="historical_target_cannot_be_retroactively_issued",
            live_projection=projection,
        )

    issue_batch = live.load_issue_batch(issue_path, live_profile, prerequisites)
    live._validate_issue_ready(issue_batch, now=now)
    if issue_batch.target_date != expected or issue_path.stem != expected.isoformat():
        raise VerifiedLiveIntegrityError(
            "Issue filename/date does not match the next live target"
        )
    pre_events = ledger.read_events()
    pre_head = _pre_head(pre_events, projection.epoch_id)
    verified_target, replay_receipt = _run_replay_under_lock(
        paths,
        runner_lock_handle=runner_lock_handle,
        project_root=project_root,
        clock=clock,
    )
    if verified_target != expected:
        raise VerifiedLiveIntegrityError("Replay receipt verified a different target")

    fresh_issue = live.load_issue_batch(issue_path, live_profile, prerequisites)
    if fresh_issue != issue_batch:
        raise VerifiedLiveIntegrityError(
            "Pending issue changed during independent replay"
        )
    issue_snapshot = _issue_snapshot(fresh_issue)
    input_snapshot = _input_snapshot(fresh_issue)
    _validate_replay_receipt_identity(
        replay_receipt,
        root=paths.root,
        target=expected,
        expected_pre_head=pre_head,
        issue=issue_snapshot,
        input_manifest=input_snapshot,
    )
    public_replay_receipt = _load_public_replay_receipt(
        paths,
        project_root=project_root,
        target=expected,
        expected_pre_head=pre_head,
        issue_sha256=issue_snapshot.sha256,
        input_manifest_sha256=input_snapshot.sha256,
        model_manifest_sha256=fresh_issue.model_manifest_sha256,
    )
    if (
        public_replay_receipt.path != replay_receipt.path
        or public_replay_receipt.sha256 != replay_receipt.sha256
        or public_replay_receipt.size_bytes != replay_receipt.size_bytes
    ):
        raise VerifiedLiveIntegrityError(
            "Independent replay result changed before the pre-seal gate"
        )
    pre_intent_now, target_is_future = _sample_machine_time_fence(
        clock,
        not_before=now,
        target=expected,
        timezone_name=live_profile["target"]["date_timezone"],
    )
    if not target_is_future:
        _write_live_status(
            live_profile,
            paths,
            prerequisites,
            ledger,
            projection,
            now=pre_intent_now,
            runner_status="waiting_for_backfill_outcome",
            reason="target_became_historical_during_independent_replay",
        )
        return _write_status(
            guard_profile,
            paths,
            now=pre_intent_now,
            runner_status="waiting_for_backfill_outcome",
            reason="pre_intent_machine_time_fence_rejected_target_day",
            live_projection=projection,
            replay_receipt=public_replay_receipt,
        )
    live._validate_issue_ready(fresh_issue, now=pre_intent_now)
    intent = _ensure_intent(
        guard_profile,
        paths,
        now=pre_intent_now,
        projection=projection,
        events=pre_events,
        issue_batch=fresh_issue,
        replay_receipt=public_replay_receipt,
    )
    intent_payload, captured_intent = _load_intent(paths, expected)
    if (
        captured_intent.path != intent.path
        or captured_intent.sha256 != intent.sha256
        or captured_intent.size_bytes != intent.size_bytes
    ):
        raise VerifiedLiveIntegrityError("Guard intent changed before live commit")
    _validate_intent_record(
        guard_profile,
        paths,
        target=expected,
        payload=intent_payload,
        expected_public_replay=public_replay_receipt,
    )

    # Close both TOCTOU windows after durable intent.  If the outcome appeared,
    # leave the intent orphaned and let the next poll classify a normal backfill.
    if outcome_path.is_file():
        return _work_remaining(
            guard_profile,
            paths,
            now=pre_intent_now,
            reason="outcome_arrived_before_guarded_live_issue_commit",
            projection=projection,
            intent=intent,
            replay_receipt=public_replay_receipt,
        )
    final_issue = live.load_issue_batch(issue_path, live_profile, prerequisites)
    if final_issue != fresh_issue:
        raise VerifiedLiveIntegrityError("Pending issue changed after guard intent")
    commit_now, target_is_future = _sample_machine_time_fence(
        clock,
        not_before=pre_intent_now,
        target=expected,
        timezone_name=live_profile["target"]["date_timezone"],
    )
    if not target_is_future:
        _write_live_status(
            live_profile,
            paths,
            prerequisites,
            ledger,
            projection,
            now=commit_now,
            runner_status="waiting_for_backfill_outcome",
            reason="target_became_historical_before_live_issue_commit",
        )
        return _write_status(
            guard_profile,
            paths,
            now=commit_now,
            runner_status="waiting_for_backfill_outcome",
            reason="pre_append_machine_time_fence_rejected_target_day",
            live_projection=projection,
            intent=intent,
            replay_receipt=public_replay_receipt,
        )
    if outcome_path.is_file():
        return _work_remaining(
            guard_profile,
            paths,
            now=commit_now,
            reason="outcome_arrived_at_guarded_live_issue_commit",
            projection=projection,
            intent=intent,
            replay_receipt=public_replay_receipt,
        )
    live._validate_issue_ready(final_issue, now=commit_now)
    live._append_issue_batch(
        ledger, live_profile, prerequisites, projection, final_issue
    )
    events = ledger.read_events()
    projection = live._reconstruct_projection(events, live_profile, prerequisites)
    seal_event = projection.seal_event
    if seal_event is None:
        raise VerifiedLiveIntegrityError("Live issue append produced no seal")
    completion_now = _clock_value(clock)
    seal_recorded_at = _parse_record_utc(
        seal_event.recorded_at_utc,
        name="live.issue_batch_sealed.recorded_at_utc",
    )
    if (
        completion_now.astimezone(timezone.utc)
        < commit_now.astimezone(timezone.utc)
        or completion_now.astimezone(timezone.utc) < seal_recorded_at
    ):
        raise VerifiedLiveIntegrityError(
            "Local machine clock regressed before guard completion commit"
        )
    recovered_intent, completion, recovered_replay = _ensure_completion(
        guard_profile,
        paths,
        now=completion_now,
        projection=projection,
        events=events,
    )
    _write_live_status(
        live_profile,
        paths,
        prerequisites,
        ledger,
        projection,
        now=completion_now,
        runner_status="waiting_for_outcome",
        reason="independently_replayed_issue_sealed_and_guard_linked",
    )
    return _work_remaining(
        guard_profile,
        paths,
        now=completion_now,
        reason="independently_replayed_issue_sealed_and_guard_linked",
        projection=projection,
        intent=recovered_intent,
        completion=completion,
        replay_receipt=recovered_replay,
    )


def poll_verified_live_runner(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    runtime_root: Path | None = None,
    project_root: Path = ROOT,
    clock: Clock | None = None,
    anchor_client: live.AnchorClient | None = None,
) -> Path:
    """Advance at most one guarded live-v1 transition.

    The old live-v1 public entrypoint is intentionally not called: it can loop
    from a safe settlement/backfill directly into an unverified issue seal.  This
    wrapper owns the same runner lock and invokes only bounded v1 primitives.
    """

    profile = load_verified_live_profile(config_path, project_root=project_root)
    paths = runtime_paths(profile, runtime_root=runtime_root)
    live._ensure_runtime_directories(paths.live)
    paths.intents.mkdir(parents=True, exist_ok=True)
    paths.completions.mkdir(parents=True, exist_ok=True)
    now = _clock_value(clock)
    lock_handle = _acquire_runner_lock(paths.live.lock)
    try:
        try:
            return _poll_locked(
                profile,
                paths,
                now=now,
                clock=clock,
                runner_lock_handle=lock_handle,
                anchor_client=anchor_client,
                project_root=project_root.resolve(),
            )
        except VerifiedLiveBusyError:
            raise
        except (
            VerifiedLiveConfigError,
            VerifiedLiveIntegrityError,
            live.LiveConfigError,
            live.LivePrerequisiteError,
            live.LiveInputError,
            live.LiveIntegrityError,
            live.LedgerError,
        ) as exc:
            normalized = (
                exc
                if isinstance(
                    exc, (VerifiedLiveConfigError, VerifiedLiveIntegrityError)
                )
                else VerifiedLiveIntegrityError(f"{type(exc).__name__}:{exc}")
            )
            _best_effort_blocked_status(profile, paths, now=now, error=normalized)
            if normalized is exc:
                raise
            raise normalized from exc
        except Exception as exc:
            normalized = VerifiedLiveIntegrityError(
                f"Unexpected verified-live failure:{type(exc).__name__}:{exc}"
            )
            _best_effort_blocked_status(profile, paths, now=now, error=normalized)
            raise normalized from exc
    finally:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        finally:
            lock_handle.close()


def _record_targets(directory: Path, *, name: str) -> list[date]:
    if not directory.exists():
        return []
    try:
        entries = list(directory.iterdir())
    except OSError as exc:
        raise VerifiedLiveIntegrityError(f"Cannot inspect {name} directory") from exc
    targets: list[date] = []
    for path in entries:
        if path.is_symlink() or not path.is_file() or path.suffix != ".json":
            raise VerifiedLiveIntegrityError(f"{name} directory contains pollution")
        try:
            target = date.fromisoformat(path.stem)
        except ValueError as exc:
            raise VerifiedLiveIntegrityError(
                f"{name} filename is not a target date"
            ) from exc
        if target.isoformat() != path.stem:
            raise VerifiedLiveIntegrityError(f"{name} filename is not canonical")
        targets.append(target)
    if len(targets) != len(set(targets)):
        raise VerifiedLiveIntegrityError(f"{name} repeats a target")
    return sorted(targets)


def _progress_live_events(paths: GuardRuntimePaths) -> tuple[live.LedgerEvent, ...]:
    if not paths.live.ledger.exists():
        return ()
    if paths.live.ledger.is_symlink():
        raise VerifiedLiveIntegrityError("Verified live ledger is a symlink")
    try:
        live_profile = live.load_config(paths.live_profile_path)
        prerequisites = live.load_prerequisites(live_profile, paths.live)
        if prerequisites is None:
            raise VerifiedLiveIntegrityError(
                "Verified live ledger exists without activation prerequisites"
            )
        projection = live.load_verified_ledger_projection(
            live_profile, paths.live, prerequisites
        )
    except VerifiedLiveIntegrityError:
        raise
    except (
        live.LiveConfigError,
        live.LivePrerequisiteError,
        live.LiveInputError,
        live.LiveIntegrityError,
        live.LedgerError,
    ) as exc:
        raise VerifiedLiveIntegrityError(
            f"Verified live ledger failed progress validation: {exc}"
        ) from exc
    return tuple(projection.ledger_events)


def _semantic_artifact_projection(value: object) -> object:
    """Remove storage locations while retaining content-addressed identity."""

    if isinstance(value, Mapping):
        materialized = dict(value)
        if set(materialized) == {"path", "sha256", "size_bytes"}:
            return {
                "sha256": materialized["sha256"],
                "size_bytes": materialized["size_bytes"],
            }
        return {
            key: _semantic_artifact_projection(child)
            for key, child in materialized.items()
        }
    if isinstance(value, list):
        return [_semantic_artifact_projection(child) for child in value]
    if isinstance(value, tuple):
        return [_semantic_artifact_projection(child) for child in value]
    return value


def _semantic_sha256(value: object) -> str:
    return _sha256_bytes(_canonical_bytes(value))


_ANCHOR_BOOKKEEPING_EVENT_TYPES = {
    "anchor_requested",
    "anchor_confirmed",
    "anchor_failed",
}


def _live_event_science_payload(event: live.LedgerEvent) -> object:
    return _semantic_artifact_projection(
        {
            "event_key": event.event_key,
            "event_type": event.event_type,
            "target_date": event.target_date,
            "station": event.station,
            "issue_id": event.issue_id,
            "protocol_config_sha256": event.protocol_config_sha256,
            "code_sha256": event.code_sha256,
            "environment_sha256": event.environment_sha256,
            "input_manifest_sha256": event.input_manifest_sha256,
            "model_manifest_sha256": event.model_manifest_sha256,
            "state_before_sha256": event.state_before_sha256,
            "state_after_sha256": event.state_after_sha256,
            "payload": event.payload,
        }
    )


def _scientific_prefix_sha256(
    events: tuple[live.LedgerEvent, ...], *, terminal_sequence_id: int
) -> str:
    prefix = events[:terminal_sequence_id]
    if len(prefix) != terminal_sequence_id:
        raise VerifiedLiveIntegrityError("Guard pre-head is beyond live events")
    return _semantic_sha256(
        [
            _live_event_science_payload(event)
            for event in prefix
            if event.event_type not in _ANCHOR_BOOKKEEPING_EVENT_TYPES
        ]
    )


def _semantic_pre_head(
    pre_head: Mapping[str, Any], events: tuple[live.LedgerEvent, ...]
) -> dict[str, object]:
    return {
        "epoch_id": pre_head["epoch_id"],
        "scientific_projection_sha256": _scientific_prefix_sha256(
            events,
            terminal_sequence_id=int(pre_head["sequence_id"]),
        ),
    }


def _replay_semantic_sha256(
    receipt: ArtifactSnapshot, *, events: tuple[live.LedgerEvent, ...]
) -> str:
    try:
        payload = _decode_json(receipt.raw, name="issue replay receipt")
    except VerifiedLiveConfigError as exc:
        raise VerifiedLiveIntegrityError(str(exc)) from exc
    semantics = dict(payload)
    semantics.pop("verified_at_utc", None)
    pre_head = semantics.get("ledger_pre_head")
    if not isinstance(pre_head, Mapping):
        raise VerifiedLiveIntegrityError("Replay semantic pre-head changed")
    semantics["ledger_pre_head"] = _semantic_pre_head(pre_head, events)
    return _semantic_sha256(_semantic_artifact_projection(semantics))


def _live_event_science_sha256(event: live.LedgerEvent) -> str:
    return _semantic_sha256(_live_event_science_payload(event))


def _intent_semantic_sha256(
    payload: Mapping[str, Any],
    *,
    replay_semantic_sha256: str,
    events: tuple[live.LedgerEvent, ...],
) -> tuple[str, str]:
    semantics = dict(payload)
    semantics.pop("created_at_utc", None)
    semantics["replay_receipt"] = {
        "replay_semantic_sha256": replay_semantic_sha256
    }
    pre_head = dict(payload["live_pre_head"])
    semantic_pre_head = _semantic_pre_head(pre_head, events)
    pre_head_science_sha256 = str(
        semantic_pre_head["scientific_projection_sha256"]
    )
    semantics["live_pre_head"] = semantic_pre_head
    return (
        _semantic_sha256(_semantic_artifact_projection(semantics)),
        pre_head_science_sha256,
    )


def _completion_semantic_sha256(
    payload: Mapping[str, Any],
    *,
    intent_semantic_sha256: str,
    replay_semantic_sha256: str,
    opened: live.LedgerEvent,
    seal: live.LedgerEvent,
    opened_science_sha256: str,
    seal_science_sha256: str,
) -> str:
    semantics = dict(payload)
    semantics.pop("completed_at_utc", None)
    semantics["intent"] = {"intent_semantic_sha256": intent_semantic_sha256}
    semantics["replay_receipt"] = {
        "replay_semantic_sha256": replay_semantic_sha256
    }
    semantics.pop("live_issue_opened_sequence_id", None)
    semantics.pop("live_issue_seal_sequence_id", None)
    semantics.pop("live_issue_opened_entry_sha256", None)
    semantics.pop("live_issue_seal_entry_sha256", None)
    semantics["live_issue_opened_event"] = {
        "event_key": opened.event_key,
        "event_type": opened.event_type,
        "target_date": opened.target_date,
        "science_sha256": opened_science_sha256,
    }
    semantics["live_issue_seal_event"] = {
        "event_key": seal.event_key,
        "event_type": seal.event_type,
        "target_date": seal.target_date,
        "science_sha256": seal_science_sha256,
    }
    return _semantic_sha256(_semantic_artifact_projection(semantics))


def guard_progress_payload(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    runtime_root: Path | None = None,
    project_root: Path = ROOT,
) -> dict[str, object]:
    """Return a timestamp-free deterministic projection of guard commits.

    This function does not acquire the runner lock.  A combined cycle snapshot
    should call it while fencing the live/replay roots in the documented global
    lock order.  Every referenced record is still captured and validated here.
    """

    profile = load_verified_live_profile(config_path, project_root=project_root)
    paths = runtime_paths(profile, runtime_root=runtime_root)
    intent_targets = _record_targets(paths.intents, name="verified-live intents")
    completion_targets = _record_targets(
        paths.completions, name="verified-live completions"
    )
    if not set(completion_targets).issubset(intent_targets):
        raise VerifiedLiveIntegrityError("Guard completion has no matching intent")
    live_events = _progress_live_events(paths)
    intent_projection: list[dict[str, object]] = []
    intent_by_target: dict[
        date,
        tuple[
            dict[str, Any],
            ArtifactSnapshot,
            ArtifactSnapshot,
            str,
            str,
        ],
    ] = {}
    for target in intent_targets:
        payload, snapshot = _load_intent(paths, target)
        _, _, replay_receipt = _validate_intent_record(
            profile,
            paths,
            target=target,
            payload=payload,
        )
        pre_head = payload["live_pre_head"]
        _validate_pre_head_against_events(pre_head, live_events)
        replay_semantic = _replay_semantic_sha256(
            replay_receipt, events=live_events
        )
        intent_semantic, pre_head_science = _intent_semantic_sha256(
            payload,
            replay_semantic_sha256=replay_semantic,
            events=live_events,
        )
        projection = {
            "target_date": target.isoformat(),
            "intent_semantic_sha256": intent_semantic,
            "replay_semantic_sha256": replay_semantic,
            "issue_sha256": payload["issue"]["sha256"],
            "input_manifest_sha256": payload["input_manifest"]["sha256"],
            "model_manifest_sha256": payload["model_manifest_sha256"],
            "guard_implementation_sha256": payload["guard_implementation_sha256"],
            "live_pre_head_scientific_projection_sha256": pre_head_science,
        }
        intent_projection.append(projection)
        intent_by_target[target] = (
            payload,
            snapshot,
            replay_receipt,
            replay_semantic,
            intent_semantic,
        )
    completion_projection: list[dict[str, object]] = []
    for target in completion_targets:
        payload, completion_snapshot = _load_record(
            _record_path(paths.completions, target),
            name="verified-live completion",
        )
        (
            intent_payload,
            intent_snapshot,
            replay_receipt,
            replay_semantic,
            intent_semantic,
        ) = intent_by_target[target]
        checked_intent, checked_replay = _validate_completion_record(
            profile,
            paths,
            target=target,
            payload=payload,
            intent_payload=intent_payload,
            intent_snapshot=intent_snapshot,
            events=live_events,
        )
        if checked_replay.sha256 != replay_receipt.sha256:
            raise VerifiedLiveIntegrityError(
                "Completion replay link differs from verified intent replay"
            )
        opened, seal = _live_issue_events(live_events, target)
        opened_science = _live_event_science_sha256(opened)
        seal_science = _live_event_science_sha256(seal)
        completion_semantic = _completion_semantic_sha256(
            payload,
            intent_semantic_sha256=intent_semantic,
            replay_semantic_sha256=replay_semantic,
            opened=opened,
            seal=seal,
            opened_science_sha256=opened_science,
            seal_science_sha256=seal_science,
        )
        completion_projection.append(
            {
                "target_date": target.isoformat(),
                "completion_semantic_sha256": completion_semantic,
                "intent_semantic_sha256": intent_semantic,
                "replay_semantic_sha256": replay_semantic,
                "issue_sha256": intent_payload["issue"]["sha256"],
                "input_manifest_sha256": intent_payload["input_manifest"]["sha256"],
                "live_issue_opened_event_key": opened.event_key,
                "live_issue_opened_event_type": opened.event_type,
                "live_issue_opened_target_date": opened.target_date,
                "live_issue_opened_entry_science_sha256": opened_science,
                "live_issue_seal_event_key": seal.event_key,
                "live_issue_seal_event_type": seal.event_type,
                "live_issue_seal_target_date": seal.target_date,
                "live_issue_seal_entry_science_sha256": seal_science,
            }
        )
    return {
        "schema_version": profile["protocol"]["progress_schema_version"],
        "profile_sha256": profile["_profile_sha256"],
        "intent_count": len(intent_projection),
        "completion_count": len(completion_projection),
        "pending_intent_targets": [
            target.isoformat()
            for target in intent_targets
            if target not in set(completion_targets)
        ],
        "intents": intent_projection,
        "completions": completion_projection,
        "e2_live_evidence_eligible": False,
        "real_activation_ready": False,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--runtime-root", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        status_path = poll_verified_live_runner(
            config_path=args.config, runtime_root=args.runtime_root
        )
    except VerifiedLiveBusyError as exc:
        print(f"[verified-live] busy: {exc}", file=sys.stderr)
        return 3
    except VerifiedLiveError as exc:
        print(f"[verified-live] blocked: {exc}", file=sys.stderr)
        return 2
    try:
        payload = _decode_json(
            _read_snapshot(status_path, name="verified-live status").raw,
            name="verified-live status",
        )
    except (VerifiedLiveConfigError, VerifiedLiveIntegrityError) as exc:
        print(f"[verified-live] blocked: {exc}", file=sys.stderr)
        return 2
    print(f"[verified-live] status={payload['runner_status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_CONFIG_SHA256",
    "GuardRuntimePaths",
    "SUCCESS_STATUSES",
    "VerifiedLiveBusyError",
    "VerifiedLiveConfigError",
    "VerifiedLiveError",
    "VerifiedLiveIntegrityError",
    "guard_progress_payload",
    "load_verified_live_profile",
    "main",
    "poll_verified_live_runner",
    "runtime_paths",
]
