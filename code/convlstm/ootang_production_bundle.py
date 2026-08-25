"""Build and strictly replay the Ootang E2-B five-seed model bundle.

The producer trains every predeclared seed on every window available at the
canonical source watermark.  It never selects a best seed.  Checkpoints contain
only tensors and primitive values, are loaded with ``weights_only=True``, and
are published below a SHA-256 object namespace before the outer activation
manifest is created.

This remains an engineering artifact.  It does not make the live runner an E2
evidence system and it does not implement automatic epoch rotation.
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
import stat
import sys
import tempfile
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import torch


CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from convlstm import model as base  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = ROOT / "config" / "ootang_prequential_deploy.v1.json"
CHECKPOINT_MAX_BYTES = 64 * 1024 * 1024
RELOAD_ABSOLUTE_TOLERANCE_MM = 1e-6
TEST_EPOCH_ENV = "OOTANG_E2B_ALLOW_TEST_EPOCH_OVERRIDE"

CHECKPOINT_KEYS = {
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
}
SOURCE_BINDING_KEYS = {
    "training_cutoff_date",
    "activation_captured_at_utc",
    "activation_source_manifest_sha256",
    "dataset_sha256",
    "semantic_manifest_sha256",
}
ARCHITECTURE_KEYS = {
    "model_class",
    "input_channels",
    "hidden_channels",
    "kernel_size",
    "quantiles",
    "lookback_days",
    "horizon_days",
    "grid_h",
    "grid_w",
}
FEATURE_SCHEMA_KEYS = {
    "input_schema",
    "displacement_columns",
    "exogenous_columns",
    "static_spatial_columns",
    "stations_model",
    "station_geometry_sha256",
}
NORMALIZATION_KEYS = {
    "displacement_mean",
    "displacement_scale",
    "exogenous_mean",
    "exogenous_scale",
    "delta_scale",
    "elevation_mean_m",
    "elevation_scale_m",
}
SPATIAL_KEYS = {"elevation_grid", "readout_weights"}
OUTER_MANIFEST_KEYS = {
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
}
TRAINING_MANIFEST_KEYS = {
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
ARTIFACT_KEYS = {"path", "sha256", "size_bytes"}
RUNTIME_CHILD_KEYS = {
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
LIVE_RUNTIME_KEYS = {
    "root",
    "ledger",
    "status",
    "lock",
    "issue_inbox",
    "outcome_inbox",
    "anchor_receipts",
    "single_unsettled_target_date",
    "poll_mode",
    "no_data_status",
    "missing_prerequisite_status",
}
LIVE_RUNTIME_PATH_KEYS = {
    "ledger",
    "status",
    "lock",
    "issue_inbox",
    "outcome_inbox",
    "anchor_receipts",
}


class ProductionBundleError(RuntimeError):
    """Base error for the production-bundle producer."""


class ProductionBundleConfigError(ProductionBundleError):
    """The reviewed deploy profile or base architecture changed."""


class ProductionBundleIntegrityError(ProductionBundleError):
    """An input or persisted bundle failed a fail-closed integrity check."""


class ProductionBundleConflictError(ProductionBundleIntegrityError):
    """The fixed activation target already has different semantics."""


class ProductionBundleBusyError(ProductionBundleError):
    """Another machine process owns the shared deploy-cycle lock."""


@dataclass(frozen=True)
class ArtifactBinding:
    """A verified immutable artifact reference."""

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
class SourceBindings:
    """Source identity embedded in every safe checkpoint."""

    training_cutoff_date: date
    activation_captured_at_utc: str
    activation_source_manifest_sha256: str
    dataset_sha256: str
    semantic_manifest_sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "training_cutoff_date": self.training_cutoff_date.isoformat(),
            "activation_captured_at_utc": self.activation_captured_at_utc,
            "activation_source_manifest_sha256": (
                self.activation_source_manifest_sha256
            ),
            "dataset_sha256": self.dataset_sha256,
            "semantic_manifest_sha256": self.semantic_manifest_sha256,
        }


@dataclass(frozen=True)
class NormalizationState:
    """As-of normalization parameters shared by training and inference."""

    displacement_mean: np.ndarray
    displacement_scale: np.ndarray
    exogenous_mean: np.ndarray
    exogenous_scale: np.ndarray
    delta_scale: np.ndarray
    elevation_mean_m: float
    elevation_scale_m: float


@dataclass(frozen=True)
class PreparedTrainingData:
    """Validated all-as-of tensors and spatial preprocessing state."""

    frame: pd.DataFrame
    displacement: np.ndarray
    inputs: np.ndarray
    x_train: np.ndarray
    y_train_normalized: np.ndarray
    normalization: NormalizationState
    elevation_grid: np.ndarray
    readout_weights: np.ndarray
    stations_model: tuple[str, ...]
    training_windows: int


@dataclass(frozen=True)
class LoadedCheckpoint:
    """A recursively validated safe checkpoint and reconstructed model."""

    seed: int
    artifact: ArtifactBinding
    source_bindings: SourceBindings
    model: base.ConvLSTMForecast
    normalization: NormalizationState
    elevation_grid: np.ndarray
    readout_weights: np.ndarray
    stations_model: tuple[str, ...]


@dataclass(frozen=True)
class LoadedProductionBundle:
    """The complete strict-loader result consumed by the issue producer."""

    manifest_path: Path
    manifest_sha256: str
    model_version: str
    created_at_utc: str
    training_cutoff: date
    stations_live: tuple[str, ...]
    stations_model: tuple[str, ...]
    checkpoints: tuple[LoadedCheckpoint, ...]
    source_bindings: SourceBindings
    input_schema_sha256: str
    training_manifest: ArtifactBinding
    manifest: dict[str, Any]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file_and_size(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _sha256_file(path: Path) -> str:
    return _sha256_file_and_size(path)[0]


def _read_regular_file_once(
    path: Path, *, name: str, maximum_bytes: int
) -> bytes:
    """Capture one stable regular-file byte string without reopening its path."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ProductionBundleIntegrityError(f"Cannot open {name}: {path}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ProductionBundleIntegrityError(f"{name} must be a regular file")
        if before.st_size <= 0 or before.st_size > maximum_bytes:
            raise ProductionBundleIntegrityError(
                f"{name} size is outside safety bounds"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(maximum_bytes + 1)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise ProductionBundleIntegrityError(f"Cannot read {name}: {path}") from exc
    finally:
        os.close(descriptor)
    stable_identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) == (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if not stable_identity or len(raw) != before.st_size:
        raise ProductionBundleIntegrityError(f"{name} changed while being read")
    if len(raw) > maximum_bytes:
        raise ProductionBundleIntegrityError(f"{name} size is outside safety bounds")
    return raw


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return _sha256_bytes(_canonical_json_bytes(value))


def _pretty_json_bytes(value: object) -> bytes:
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


def _require_exact_keys(value: object, expected: set[str], *, name: str) -> dict:
    if not isinstance(value, dict):
        raise ProductionBundleIntegrityError(f"{name} must be an object")
    observed = set(value)
    if observed != expected:
        missing = sorted(expected - observed)
        unknown = sorted(observed - expected)
        raise ProductionBundleIntegrityError(
            f"{name} keys changed; missing={missing}, unknown={unknown}"
        )
    if any(not isinstance(key, str) for key in value):
        raise ProductionBundleIntegrityError(f"{name} keys must be strings")
    return value


def _require_sha256(value: object, *, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ProductionBundleIntegrityError(f"{name} must be lowercase SHA-256")
    return value


def _require_int(value: object, *, name: str, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProductionBundleIntegrityError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise ProductionBundleIntegrityError(f"{name} must be >= {minimum}")
    return value


def _require_finite(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProductionBundleIntegrityError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ProductionBundleIntegrityError(f"{name} must be finite")
    return result


def _parse_date(value: object, *, name: str) -> date:
    if not isinstance(value, str):
        raise ProductionBundleIntegrityError(f"{name} must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ProductionBundleIntegrityError(f"{name} must be YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ProductionBundleIntegrityError(f"{name} must be canonical")
    return parsed


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ProductionBundleIntegrityError("Machine time must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _parse_utc(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ProductionBundleIntegrityError(f"{name} must be RFC 3339 UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ProductionBundleIntegrityError(f"{name} is invalid") from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ProductionBundleIntegrityError(f"{name} must be UTC")
    return value


def _utc_datetime(value: object, *, name: str) -> datetime:
    text = _parse_utc(value, name=name)
    return datetime.fromisoformat(text[:-1] + "+00:00")


def _load_json(path: Path, *, name: str) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise ProductionBundleIntegrityError(f"Missing {name}: {path}")
    raw = path.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProductionBundleIntegrityError(f"Invalid {name}: {path}") from exc
    if not isinstance(payload, dict):
        raise ProductionBundleIntegrityError(f"{name} must be a JSON object")
    return payload, _sha256_bytes(raw)


def _resolve(path: str | Path, *, project_root: Path = ROOT) -> Path:
    result = Path(path)
    return result.resolve() if result.is_absolute() else (project_root / result).resolve()


def _relative_runtime_path(value: object, *, name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ProductionBundleConfigError(f"{name} must be a nonblank relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path == Path("."):
        raise ProductionBundleConfigError(
            f"{name} must remain a relative path without parent traversal"
        )
    return path


def _require_path_within(path: Path, root: Path, *, name: str) -> Path:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ProductionBundleConfigError(
            f"{name} escapes its configured runtime root"
        ) from exc
    return resolved_path


def _validate_profile_runtime_paths(
    runtime: dict[str, Any], *, project_root: Path
) -> None:
    project = project_root.resolve()
    configured_root = _relative_runtime_path(runtime["root"], name="runtime.root")
    root = _require_path_within(
        project / configured_root,
        project,
        name="runtime.root",
    )
    for key in sorted(RUNTIME_CHILD_KEYS):
        child = _relative_runtime_path(runtime[key], name=f"runtime.{key}")
        _require_path_within(root / child, root, name=f"runtime.{key}")


def _artifact_from_value(
    value: object,
    *,
    name: str,
    project_root: Path = ROOT,
) -> ArtifactBinding:
    record = _require_exact_keys(value, ARTIFACT_KEYS, name=name)
    raw_path = record["path"]
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ProductionBundleIntegrityError(f"{name}.path must be nonblank")
    path = _resolve(raw_path, project_root=project_root)
    sha256 = _require_sha256(record["sha256"], name=f"{name}.sha256")
    size_bytes = _require_int(
        record["size_bytes"], name=f"{name}.size_bytes", minimum=0
    )
    if not path.is_file():
        raise ProductionBundleIntegrityError(f"Missing {name}: {path}")
    actual_sha256, actual_size = _sha256_file_and_size(path)
    if actual_size != size_bytes or actual_sha256 != sha256:
        raise ProductionBundleIntegrityError(f"{name} hash or size mismatch")
    return ArtifactBinding(path.resolve(), sha256, size_bytes)


def _artifact_from_source(value: object, *, name: str) -> ArtifactBinding:
    try:
        path = Path(getattr(value, "path")).resolve()
        sha256 = getattr(value, "sha256")
        size_bytes = getattr(value, "size_bytes")
    except (AttributeError, TypeError) as exc:
        raise ProductionBundleIntegrityError(
            f"Canonical source {name} artifact is malformed"
        ) from exc
    return _artifact_from_value(
        {"path": str(path), "sha256": sha256, "size_bytes": size_bytes},
        name=f"canonical source {name}",
    )


def _source_bindings(source: object) -> SourceBindings:
    watermark = getattr(source, "watermark", None)
    if not isinstance(watermark, date) or isinstance(watermark, datetime):
        raise ProductionBundleIntegrityError("Canonical source watermark is invalid")
    dataset = _artifact_from_source(getattr(source, "dataset", None), name="dataset")
    semantic = _artifact_from_source(
        getattr(source, "semantic_manifest", None), name="semantic_manifest"
    )
    activation = _artifact_from_source(
        getattr(source, "activation_manifest", None), name="activation_manifest"
    )
    activation_payload, activation_sha256 = _load_json(
        activation.path, name="activation source manifest"
    )
    activation_record = _require_exact_keys(
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
    activation_watermark = _parse_date(
        activation_record["maximum_complete_finalized_date"],
        name="activation.maximum_complete_finalized_date",
    )
    activation_captured_at_utc = _parse_utc(
        activation_record["captured_at_utc"], name="activation.captured_at_utc"
    )
    activation_semantic = _artifact_from_value(
        activation_record["data_manifest"], name="activation.data_manifest"
    )
    if (
        activation_sha256 != activation.sha256
        or activation_watermark != watermark
        or activation_semantic.sha256 != semantic.sha256
    ):
        raise ProductionBundleIntegrityError(
            "Canonical source is not aligned to the immutable activation epoch"
        )
    semantic_payload, semantic_sha256 = _load_json(
        semantic.path, name="activation semantic manifest"
    )
    if semantic_sha256 != semantic.sha256:
        raise ProductionBundleIntegrityError("Activation semantic manifest hash changed")
    canonical_dataset = semantic_payload.get("canonical_dataset")
    semantic_dataset = _artifact_from_value(
        canonical_dataset, name="activation semantic canonical_dataset"
    )
    if semantic_dataset.sha256 != dataset.sha256:
        raise ProductionBundleIntegrityError(
            "Activation semantic manifest points to another dataset"
        )
    return SourceBindings(
        training_cutoff_date=watermark,
        activation_captured_at_utc=activation_captured_at_utc,
        activation_source_manifest_sha256=activation.sha256,
        dataset_sha256=dataset.sha256,
        semantic_manifest_sha256=semantic.sha256,
    )


def load_deploy_profile(
    path: Path = DEFAULT_CONFIG_PATH,
    *,
    project_root: Path = ROOT,
) -> dict[str, Any]:
    """Load the deploy profile and reject drift from the base model contract."""
    path = _resolve(path, project_root=project_root)
    profile, profile_sha256 = _load_json(path, name="E2-B deploy profile")
    required_top = {
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
    _require_exact_keys(profile, required_top, name="E2-B deploy profile")
    if (
        profile["schema_version"] != "ootang_prequential_deploy_profile_v1"
        or profile["profile_id"] != "ootang-prequential-deploy-v1"
        or profile["profile_version"] != "1.0.0-engineering"
        or profile["case"] != "ootang"
        or profile["artifact_status"]
        != "e2b_engineering_only_not_live_evidence"
    ):
        raise ProductionBundleConfigError("E2-B deploy profile identity changed")
    for flag in (
        "formal_warning_output",
        "independent_label_used",
        "confirmatory_external_validation",
        "vajont_used",
        "default_pipeline_member",
    ):
        if profile[flag] is not False:
            raise ProductionBundleConfigError(f"E2-B flag {flag} must remain false")

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
    if profile["engineering_capabilities"] != expected_capabilities:
        raise ProductionBundleConfigError("E2-B engineering capability boundary changed")

    _require_exact_keys(
        profile["runtime"],
        {"root", *RUNTIME_CHILD_KEYS},
        name="runtime",
    )
    _validate_profile_runtime_paths(
        profile["runtime"], project_root=project_root
    )

    live_contract = _require_exact_keys(
        profile["live_profile"], {"path", "expected_sha256"}, name="live_profile"
    )
    live_path = _resolve(live_contract["path"], project_root=project_root)
    if _sha256_file(live_path) != live_contract["expected_sha256"]:
        raise ProductionBundleConfigError("E2-A live profile hash changed")

    model = profile["model"]
    expected_model_constants = {
        "seeds": [0, 1, 2, 3, 4],
        "best_seed_selected": False,
        "device": "cpu",
        "deterministic_algorithms": True,
        "torch_threads": 1,
        "lookback_days": base.LOOKBACK,
        "horizon_days": base.HORIZON,
        "input_schema": base.MODEL_INPUT_SCHEMA,
        "input_channels": base.MODEL_INPUT_CHANNELS,
        "displacement_columns": list(base.DISP_COLS),
        "exogenous_columns": list(base.EXOG_COLS),
        "static_spatial_columns": list(base.STATIC_SPATIAL_COLS),
        "quantiles": list(base.QUANTILES),
        "hidden_channels": base.HIDDEN,
        "kernel_size": base.KERNEL,
        "epochs": base.EPOCHS,
        "learning_rate": base.LR,
    }
    for key, expected in expected_model_constants.items():
        if model.get(key) != expected:
            raise ProductionBundleConfigError(
                f"Deploy model field {key} differs from base ConvLSTM"
            )
    if model.get("fit_policy") != "all_as_of_windows_fixed_epochs_no_holdout_selection":
        raise ProductionBundleConfigError("Production fit policy changed")
    if model.get("minimum_training_windows") != 365:
        raise ProductionBundleConfigError("Minimum training-window contract changed")
    if model.get("delta_scale_floor_mm") != 0.05:
        raise ProductionBundleConfigError("Delta-scale floor changed")

    historical = profile["historical_base"]
    geometry = profile["station_geometry"]
    for contract, name in ((historical, "historical_base"), (geometry, "geometry")):
        artifact_path = _resolve(contract["path"], project_root=project_root)
        if _sha256_file(artifact_path) != contract["sha256"]:
            raise ProductionBundleConfigError(f"{name} SHA-256 changed")
    profile["_profile_path"] = str(path)
    profile["_profile_sha256"] = profile_sha256
    profile["_project_root"] = str(project_root.resolve())
    return profile


def _validated_profile(profile: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize profiles loaded by the source/issue modules to this strict view."""
    if profile is None:
        return load_deploy_profile()
    if not isinstance(profile, dict):
        raise ProductionBundleConfigError("Deploy profile must be an object")
    project_root = Path(profile.get("_project_root", ROOT))
    profile_path = Path(profile.get("_profile_path", DEFAULT_CONFIG_PATH))
    validated = load_deploy_profile(profile_path, project_root=project_root)
    public_profile = {key: value for key, value in profile.items() if not key.startswith("_")}
    expected_public = {
        key: value for key, value in validated.items() if not key.startswith("_")
    }
    if public_profile != expected_public:
        raise ProductionBundleConfigError(
            "Passed deploy profile differs from the reviewed profile bytes"
        )
    return validated


def input_schema_record(profile: dict[str, Any]) -> dict[str, object]:
    """Return the canonical architecture/preprocessing schema binding."""
    profile = _validated_profile(profile)
    model = profile["model"]
    return {
        "schema_version": "ootang_convlstm_input_schema_binding_v1",
        "input_schema": model["input_schema"],
        "input_channels": model["input_channels"],
        "displacement_columns": model["displacement_columns"],
        "exogenous_columns": model["exogenous_columns"],
        "static_spatial_columns": model["static_spatial_columns"],
        "stations_model": profile["source_feed"]["station_order_model"],
        "stations_live": profile["source_feed"]["station_order_live"],
        "station_geometry_sha256": profile["station_geometry"]["sha256"],
        "grid_height": base.GRID_H,
        "grid_width": base.GRID_W,
        "interpolation": "horizontal_idw_power_2",
        "elevation": "station_zscore_then_horizontal_idw_static_channel",
        "normalization_scope": "all_rows_available_at_source_watermark",
    }


def input_schema_sha256(profile: dict[str, Any]) -> str:
    """Hash the complete live input/preprocessing schema."""
    return _canonical_sha256(input_schema_record(profile))


def _validate_source_frame(
    source: object, profile: dict[str, Any]
) -> tuple[pd.DataFrame, np.ndarray]:
    frame = getattr(source, "frame", None)
    if not isinstance(frame, pd.DataFrame):
        raise ProductionBundleIntegrityError("Canonical source frame is missing")
    frame = frame.copy(deep=True)
    model = profile["model"]
    required = [
        "Date",
        *model["displacement_columns"],
        *model["exogenous_columns"],
    ]
    missing = [column for column in required if column not in frame]
    if missing:
        raise ProductionBundleIntegrityError(
            f"Canonical source lacks model columns: {missing}"
        )
    dates = pd.to_datetime(frame["Date"], format="%Y-%m-%d", errors="raise")
    if dates.isna().any() or dates.duplicated().any() or not dates.is_monotonic_increasing:
        raise ProductionBundleIntegrityError("Canonical source dates are not strict")
    if len(dates) > 1 and not np.all(np.diff(dates.to_numpy()) == np.timedelta64(1, "D")):
        raise ProductionBundleIntegrityError("Canonical source dates are not contiguous")
    watermark = getattr(source, "watermark", None)
    if dates.iloc[-1].date() != watermark:
        raise ProductionBundleIntegrityError("Canonical source watermark/frame mismatch")
    numeric = frame[required[1:]].to_numpy(dtype=np.float64)
    if not np.isfinite(numeric).all():
        raise ProductionBundleIntegrityError("Canonical model inputs are not finite")
    training_windows = len(frame) - model["lookback_days"] - model["horizon_days"] + 1
    if training_windows < model["minimum_training_windows"]:
        raise ProductionBundleIntegrityError(
            "Canonical source has fewer than the minimum all-as-of windows"
        )
    dataset = _artifact_from_source(getattr(source, "dataset", None), name="dataset")
    dataset_payload, dataset_sha256 = _load_json(
        dataset.path, name="canonical activation dataset"
    )
    dataset_record = _require_exact_keys(
        dataset_payload,
        {
            "schema_version",
            "case",
            "date_timezone",
            "columns",
            "maximum_complete_finalized_date",
            "rows",
        },
        name="canonical activation dataset",
    )
    if (
        dataset_sha256 != dataset.sha256
        or dataset_record["schema_version"] != "ootang_canonical_model_source_v1"
        or dataset_record["case"] != "ootang"
        or dataset_record["date_timezone"] != profile["source_feed"]["date_timezone"]
        or dataset_record["columns"] != list(frame.columns)
        or dataset_record["maximum_complete_finalized_date"] != watermark.isoformat()
    ):
        raise ProductionBundleIntegrityError(
            "Canonical source frame metadata differs from its dataset artifact"
        )
    rows = dataset_record["rows"]
    if not isinstance(rows, list) or len(rows) != len(frame):
        raise ProductionBundleIntegrityError(
            "Canonical source frame row count differs from its dataset artifact"
        )
    artifact_frame = pd.DataFrame(rows, columns=list(frame.columns))
    if artifact_frame["Date"].tolist() != frame["Date"].astype(str).tolist():
        raise ProductionBundleIntegrityError(
            "Canonical source frame dates differ from its dataset artifact"
        )
    try:
        artifact_numeric = artifact_frame[list(frame.columns[1:])].to_numpy(
            dtype=np.float64
        )
        frame_numeric = frame[list(frame.columns[1:])].to_numpy(dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ProductionBundleIntegrityError(
            "Canonical source dataset numeric rows are invalid"
        ) from exc
    if not np.array_equal(artifact_numeric, frame_numeric):
        raise ProductionBundleIntegrityError(
            "Canonical source frame values differ from its dataset artifact"
        )
    return frame, dates.to_numpy()


def _normalization_from_frame(
    frame: pd.DataFrame,
    displacement: np.ndarray,
    elevation_m: np.ndarray,
    delta_scale: np.ndarray,
    profile: dict[str, Any],
) -> NormalizationState:
    exogenous = frame[profile["model"]["exogenous_columns"]].to_numpy(
        dtype=np.float64
    )
    elevation_scale = float(elevation_m.std())
    if elevation_scale <= 0:
        raise ProductionBundleIntegrityError("Station elevation has zero variation")
    return NormalizationState(
        displacement_mean=displacement.mean(axis=0),
        displacement_scale=np.maximum(displacement.std(axis=0), 1.0),
        exogenous_mean=exogenous.mean(axis=0),
        exogenous_scale=np.maximum(exogenous.std(axis=0), 1e-6),
        delta_scale=np.asarray(delta_scale, dtype=np.float64),
        elevation_mean_m=float(elevation_m.mean()),
        elevation_scale_m=elevation_scale,
    )


def make_inputs_from_normalization(
    frame: pd.DataFrame,
    normalization: NormalizationState,
    interp: Any,
    elevation_grid: np.ndarray,
    profile: dict[str, Any],
) -> np.ndarray:
    """Reproduce ``base.make_model_inputs`` using persisted parameters."""
    displacement = frame[profile["model"]["displacement_columns"]].to_numpy(
        dtype=np.float64
    )
    displacement_normalized = (
        displacement - normalization.displacement_mean
    ) / normalization.displacement_scale
    displacement_grid = interp(displacement_normalized).astype(np.float32)[
        :, None, :, :
    ]
    elevation_grid = np.asarray(elevation_grid, dtype=np.float32)
    if elevation_grid.shape != (base.GRID_H, base.GRID_W):
        raise ProductionBundleIntegrityError("Persisted elevation grid shape changed")
    elevation_channel = np.broadcast_to(
        elevation_grid[None, None, :, :],
        (len(frame), 1, base.GRID_H, base.GRID_W),
    )
    exogenous = frame[profile["model"]["exogenous_columns"]].to_numpy(
        dtype=np.float64
    )
    exogenous_normalized = (
        (exogenous - normalization.exogenous_mean)
        / normalization.exogenous_scale
    ).astype(np.float32)
    exogenous_grid = np.broadcast_to(
        exogenous_normalized[:, :, None, None],
        (
            len(frame),
            len(profile["model"]["exogenous_columns"]),
            base.GRID_H,
            base.GRID_W,
        ),
    )
    values = np.concatenate(
        [displacement_grid, elevation_channel, exogenous_grid], axis=1
    ).astype(np.float32)
    if not np.isfinite(values).all():
        raise ProductionBundleIntegrityError("Normalized model input is not finite")
    return values


def prepare_training_data(
    source: object, profile: dict[str, Any]
) -> PreparedTrainingData:
    """Create the all-as-of training arrays using the frozen base preprocessing."""
    profile = _validated_profile(profile)
    frame, _ = _validate_source_frame(source, profile)
    displacement = frame[profile["model"]["displacement_columns"]].to_numpy(
        dtype=np.float64
    )
    stations_model, xy, elevation_m = base.load_station_geometry(
        profile["model"]["displacement_columns"]
    )
    if stations_model != profile["source_feed"]["station_order_model"]:
        raise ProductionBundleIntegrityError("Model station geometry order changed")
    interp, (grid_x, grid_y) = base.make_interpolator(
        xy, base.GRID_H, base.GRID_W
    )
    elevation_grid = base.make_elevation_grid(elevation_m, interp)
    readout_weights = base.station_readout_weights(grid_x, grid_y, xy)
    _, _, training_delta = base.make_station_windows(
        displacement,
        split=profile["model"]["lookback_days"],
        lookback=profile["model"]["lookback_days"],
        horizon=profile["model"]["horizon_days"],
        stop=len(displacement),
    )
    delta_scale = base.make_delta_scale(
        training_delta, floor=profile["model"]["delta_scale_floor_mm"]
    )
    normalization = _normalization_from_frame(
        frame, displacement, elevation_m, delta_scale, profile
    )
    inputs = make_inputs_from_normalization(
        frame, normalization, interp, elevation_grid, profile
    )
    base_inputs, base_displacement_scale = base.make_model_inputs(
        frame,
        displacement,
        len(frame),
        interp,
        elevation_grid=elevation_grid,
    )
    if not np.array_equal(inputs, base_inputs):
        raise ProductionBundleIntegrityError(
            "Persistable preprocessing differs from base.make_model_inputs"
        )
    if not np.array_equal(
        normalization.displacement_scale, base_displacement_scale
    ):
        raise ProductionBundleIntegrityError("Base displacement scaling changed")

    x_train = base.make_windows(
        inputs,
        profile["model"]["lookback_days"],
        profile["model"]["horizon_days"],
    )[0]
    y_train_normalized = (training_delta / delta_scale).astype(np.float32)
    if len(x_train) != len(y_train_normalized):
        raise ProductionBundleIntegrityError("Training input/target windows diverged")
    return PreparedTrainingData(
        frame=frame,
        displacement=displacement,
        inputs=inputs,
        x_train=x_train,
        y_train_normalized=y_train_normalized,
        normalization=normalization,
        elevation_grid=elevation_grid,
        readout_weights=readout_weights,
        stations_model=tuple(stations_model),
        training_windows=len(x_train),
    )


def _configure_deterministic_cpu(profile: dict[str, Any], seed: int) -> None:
    if profile["model"]["device"] != "cpu":
        raise ProductionBundleConfigError("Production bundle is CPU-only")
    torch.set_num_threads(profile["model"]["torch_threads"])
    torch.use_deterministic_algorithms(True)
    torch.backends.mkldnn.enabled = False
    torch.manual_seed(seed)
    np.random.seed(seed)


def _training_epochs(profile: dict[str, Any], override: int | None) -> int:
    if override is None:
        return profile["model"]["epochs"]
    if os.environ.get(TEST_EPOCH_ENV) != "1":
        raise ProductionBundleConfigError(
            "Epoch override is restricted to explicit unit-test mode"
        )
    if isinstance(override, bool) or not isinstance(override, int) or override <= 0:
        raise ProductionBundleConfigError("Test epoch override must be positive")
    if override >= profile["model"]["epochs"]:
        raise ProductionBundleConfigError("Test override must be below live epochs")
    return override


def _train_seed(
    prepared: PreparedTrainingData,
    profile: dict[str, Any],
    *,
    seed: int,
    epochs: int,
) -> tuple[base.ConvLSTMForecast, list[float]]:
    _configure_deterministic_cpu(profile, seed)
    x_train = torch.from_numpy(prepared.x_train)
    y_train = torch.from_numpy(prepared.y_train_normalized)
    readout_weights = torch.from_numpy(prepared.readout_weights)
    model = base.ConvLSTMForecast(
        in_ch=profile["model"]["input_channels"],
        hid_ch=profile["model"]["hidden_channels"],
        kernel=profile["model"]["kernel_size"],
        quantiles=profile["model"]["quantiles"],
    )
    learning_rate = profile["model"]["learning_rate"]
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
    if optimizer.param_groups[0]["lr"] != learning_rate:
        raise ProductionBundleIntegrityError("Optimizer learning rate changed")
    losses: list[float] = []
    for _ in range(epochs):
        model.train()
        optimizer.zero_grad()
        prediction_grid = model(x_train)
        prediction_station = base.readout_grid_at_stations(
            prediction_grid, readout_weights
        )
        loss = base.pinball_loss(
            prediction_station, y_train, profile["model"]["quantiles"]
        )
        if not torch.isfinite(loss):
            raise ProductionBundleIntegrityError("Training loss became non-finite")
        loss.backward()
        optimizer.step()
        losses.append(float(loss.detach()))
    model.eval()
    return model, losses


def _tensor(value: np.ndarray, *, dtype: torch.dtype) -> torch.Tensor:
    return torch.as_tensor(np.asarray(value), dtype=dtype).clone().contiguous().cpu()


def _checkpoint_payload(
    model: base.ConvLSTMForecast,
    prepared: PreparedTrainingData,
    profile: dict[str, Any],
    source_bindings: SourceBindings,
    *,
    seed: int,
) -> dict[str, object]:
    normalization = prepared.normalization
    state_dict = {
        key: value.detach().clone().contiguous().cpu()
        for key, value in model.state_dict().items()
    }
    return {
        "schema_version": profile["model"]["checkpoint_schema_version"],
        "checkpoint_format": profile["model"]["checkpoint_format"],
        "case": "ootang",
        "seed": seed,
        "source": source_bindings.as_dict(),
        "architecture": {
            "model_class": "ConvLSTMForecast",
            "input_channels": profile["model"]["input_channels"],
            "hidden_channels": profile["model"]["hidden_channels"],
            "kernel_size": profile["model"]["kernel_size"],
            "quantiles": profile["model"]["quantiles"],
            "lookback_days": profile["model"]["lookback_days"],
            "horizon_days": profile["model"]["horizon_days"],
            "grid_h": base.GRID_H,
            "grid_w": base.GRID_W,
        },
        "feature_schema": {
            "input_schema": profile["model"]["input_schema"],
            "displacement_columns": profile["model"]["displacement_columns"],
            "exogenous_columns": profile["model"]["exogenous_columns"],
            "static_spatial_columns": profile["model"]["static_spatial_columns"],
            "stations_model": list(prepared.stations_model),
            "station_geometry_sha256": profile["station_geometry"]["sha256"],
        },
        "normalization": {
            "displacement_mean": _tensor(
                normalization.displacement_mean, dtype=torch.float64
            ),
            "displacement_scale": _tensor(
                normalization.displacement_scale, dtype=torch.float64
            ),
            "exogenous_mean": _tensor(
                normalization.exogenous_mean, dtype=torch.float64
            ),
            "exogenous_scale": _tensor(
                normalization.exogenous_scale, dtype=torch.float64
            ),
            "delta_scale": _tensor(normalization.delta_scale, dtype=torch.float64),
            "elevation_mean_m": normalization.elevation_mean_m,
            "elevation_scale_m": normalization.elevation_scale_m,
        },
        "spatial": {
            "elevation_grid": _tensor(
                prepared.elevation_grid, dtype=torch.float32
            ),
            "readout_weights": _tensor(
                prepared.readout_weights, dtype=torch.float32
            ),
        },
        "state_dict": state_dict,
    }


def _serialize_safe_checkpoint(payload: dict[str, object]) -> bytes:
    buffer = io.BytesIO()
    torch.save(payload, buffer)
    return buffer.getvalue()


def _atomic_write_bytes(path: Path, value: bytes, *, replace: bool) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    created = True
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path)
            except FileExistsError:
                created = False
                if path.read_bytes() != value:
                    raise ProductionBundleConflictError(
                        f"Immutable target already differs: {path}"
                    )
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()
    return created


def _remove_exact_publication(path: Path, value: bytes) -> None:
    """Revoke only the exact pointer bytes created by this failed publication."""
    try:
        observed = path.read_bytes()
    except OSError as exc:
        raise ProductionBundleConflictError(
            f"Cannot revoke failed bundle publication: {path}"
        ) from exc
    if observed != value:
        raise ProductionBundleConflictError(
            "Bundle publication changed before fail-closed revocation"
        )
    path.unlink()
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _publish_object(objects_root: Path, value: bytes) -> ArtifactBinding:
    sha256 = _sha256_bytes(value)
    path = objects_root / sha256
    _atomic_write_bytes(path, value, replace=False)
    actual_sha256, actual_size = _sha256_file_and_size(path)
    if actual_sha256 != sha256 or actual_size != len(value):
        raise ProductionBundleIntegrityError("Published object failed replay")
    return ArtifactBinding(path.resolve(), sha256, actual_size)


def _validate_source_binding_value(
    value: object, expected: SourceBindings, *, name: str
) -> SourceBindings:
    record = _require_exact_keys(value, SOURCE_BINDING_KEYS, name=name)
    result = SourceBindings(
        training_cutoff_date=_parse_date(
            record["training_cutoff_date"], name=f"{name}.training_cutoff_date"
        ),
        activation_captured_at_utc=_parse_utc(
            record["activation_captured_at_utc"],
            name=f"{name}.activation_captured_at_utc",
        ),
        activation_source_manifest_sha256=_require_sha256(
            record["activation_source_manifest_sha256"],
            name=f"{name}.activation_source_manifest_sha256",
        ),
        dataset_sha256=_require_sha256(
            record["dataset_sha256"], name=f"{name}.dataset_sha256"
        ),
        semantic_manifest_sha256=_require_sha256(
            record["semantic_manifest_sha256"],
            name=f"{name}.semantic_manifest_sha256",
        ),
    )
    if result != expected:
        raise ProductionBundleIntegrityError(f"{name} does not match current source")
    return result


def _require_tensor(
    value: object,
    *,
    name: str,
    shape: tuple[int, ...],
    dtype: torch.dtype,
    positive: bool = False,
) -> torch.Tensor:
    if type(value) is not torch.Tensor:
        raise ProductionBundleIntegrityError(f"{name} must be a plain tensor")
    tensor = value
    if (
        tensor.device.type != "cpu"
        or tensor.layout != torch.strided
        or tensor.dtype != dtype
        or tuple(tensor.shape) != shape
        or tensor.requires_grad
        or not tensor.is_contiguous()
        or tensor.storage_offset() != 0
        or tensor.untyped_storage().nbytes() != tensor.numel() * tensor.element_size()
    ):
        raise ProductionBundleIntegrityError(f"{name} tensor metadata changed")
    if not torch.isfinite(tensor).all():
        raise ProductionBundleIntegrityError(f"{name} must be finite")
    if positive and not torch.all(tensor > 0):
        raise ProductionBundleIntegrityError(f"{name} must be strictly positive")
    return tensor.detach().clone().contiguous()


def load_safe_checkpoint(
    artifact: ArtifactBinding | Path,
    *,
    expected_sha256: str | None = None,
    expected_size_bytes: int | None = None,
    profile: dict[str, Any],
    source_bindings: SourceBindings,
) -> LoadedCheckpoint:
    """Capture, hash, weights-only load, validate, and reconstruct a model."""
    if isinstance(artifact, ArtifactBinding):
        path = artifact.path
        expected_sha256 = artifact.sha256
        expected_size_bytes = artifact.size_bytes
    else:
        path = Path(artifact)
    if expected_sha256 is None:
        raise ProductionBundleIntegrityError("Checkpoint expected SHA-256 is required")
    _require_sha256(expected_sha256, name="checkpoint.expected_sha256")
    raw = _read_regular_file_once(
        path,
        name="checkpoint",
        maximum_bytes=CHECKPOINT_MAX_BYTES,
    )
    actual_sha256 = _sha256_bytes(raw)
    actual_size = len(raw)
    if actual_sha256 != expected_sha256:
        raise ProductionBundleIntegrityError("Checkpoint SHA-256 mismatch")
    if expected_size_bytes is not None and actual_size != expected_size_bytes:
        raise ProductionBundleIntegrityError("Checkpoint size mismatch")
    try:
        payload = torch.load(io.BytesIO(raw), map_location="cpu", weights_only=True)
    except Exception as exc:
        raise ProductionBundleIntegrityError(
            "Checkpoint is not a safe weights-only tensor bundle"
        ) from exc
    record = _require_exact_keys(payload, CHECKPOINT_KEYS, name="checkpoint")
    model_contract = profile["model"]
    if (
        record["schema_version"] != model_contract["checkpoint_schema_version"]
        or record["checkpoint_format"] != model_contract["checkpoint_format"]
        or record["case"] != "ootang"
    ):
        raise ProductionBundleIntegrityError("Checkpoint identity changed")
    seed = _require_int(record["seed"], name="checkpoint.seed", minimum=0)
    if seed not in model_contract["seeds"]:
        raise ProductionBundleIntegrityError("Checkpoint seed is not predeclared")
    checked_source = _validate_source_binding_value(
        record["source"], source_bindings, name="checkpoint.source"
    )

    architecture = _require_exact_keys(
        record["architecture"], ARCHITECTURE_KEYS, name="checkpoint.architecture"
    )
    expected_architecture = {
        "model_class": "ConvLSTMForecast",
        "input_channels": model_contract["input_channels"],
        "hidden_channels": model_contract["hidden_channels"],
        "kernel_size": model_contract["kernel_size"],
        "quantiles": model_contract["quantiles"],
        "lookback_days": model_contract["lookback_days"],
        "horizon_days": model_contract["horizon_days"],
        "grid_h": base.GRID_H,
        "grid_w": base.GRID_W,
    }
    if architecture != expected_architecture:
        raise ProductionBundleIntegrityError("Checkpoint architecture changed")

    feature_schema = _require_exact_keys(
        record["feature_schema"], FEATURE_SCHEMA_KEYS, name="checkpoint.feature_schema"
    )
    expected_features = {
        "input_schema": model_contract["input_schema"],
        "displacement_columns": model_contract["displacement_columns"],
        "exogenous_columns": model_contract["exogenous_columns"],
        "static_spatial_columns": model_contract["static_spatial_columns"],
        "stations_model": profile["source_feed"]["station_order_model"],
        "station_geometry_sha256": profile["station_geometry"]["sha256"],
    }
    if feature_schema != expected_features:
        raise ProductionBundleIntegrityError("Checkpoint feature schema changed")

    n_stations = len(feature_schema["stations_model"])
    n_exogenous = len(feature_schema["exogenous_columns"])
    normalization_value = _require_exact_keys(
        record["normalization"], NORMALIZATION_KEYS, name="checkpoint.normalization"
    )
    displacement_mean = _require_tensor(
        normalization_value["displacement_mean"],
        name="normalization.displacement_mean",
        shape=(n_stations,),
        dtype=torch.float64,
    )
    displacement_scale = _require_tensor(
        normalization_value["displacement_scale"],
        name="normalization.displacement_scale",
        shape=(n_stations,),
        dtype=torch.float64,
        positive=True,
    )
    exogenous_mean = _require_tensor(
        normalization_value["exogenous_mean"],
        name="normalization.exogenous_mean",
        shape=(n_exogenous,),
        dtype=torch.float64,
    )
    exogenous_scale = _require_tensor(
        normalization_value["exogenous_scale"],
        name="normalization.exogenous_scale",
        shape=(n_exogenous,),
        dtype=torch.float64,
        positive=True,
    )
    delta_scale = _require_tensor(
        normalization_value["delta_scale"],
        name="normalization.delta_scale",
        shape=(n_stations,),
        dtype=torch.float64,
        positive=True,
    )
    elevation_mean = _require_finite(
        normalization_value["elevation_mean_m"], name="normalization.elevation_mean_m"
    )
    elevation_scale = _require_finite(
        normalization_value["elevation_scale_m"],
        name="normalization.elevation_scale_m",
    )
    if elevation_scale <= 0:
        raise ProductionBundleIntegrityError("Elevation scale must be positive")

    spatial = _require_exact_keys(
        record["spatial"], SPATIAL_KEYS, name="checkpoint.spatial"
    )
    elevation_grid = _require_tensor(
        spatial["elevation_grid"],
        name="spatial.elevation_grid",
        shape=(base.GRID_H, base.GRID_W),
        dtype=torch.float32,
    )
    readout_weights = _require_tensor(
        spatial["readout_weights"],
        name="spatial.readout_weights",
        shape=(n_stations, base.GRID_H * base.GRID_W),
        dtype=torch.float32,
        positive=True,
    )
    row_sums = readout_weights.to(torch.float64).sum(dim=1)
    if not torch.allclose(row_sums, torch.ones_like(row_sums), rtol=0, atol=1e-6):
        raise ProductionBundleIntegrityError("Readout weights are not normalized")
    stations_model, xy, elevation_m = base.load_station_geometry(
        model_contract["displacement_columns"]
    )
    if stations_model != feature_schema["stations_model"]:
        raise ProductionBundleIntegrityError("Checkpoint station geometry order changed")
    interp, (grid_x, grid_y) = base.make_interpolator(
        xy, base.GRID_H, base.GRID_W
    )
    expected_elevation_grid = base.make_elevation_grid(elevation_m, interp)
    expected_readout_weights = base.station_readout_weights(grid_x, grid_y, xy)
    if (
        not np.array_equal(elevation_grid.numpy(), expected_elevation_grid)
        or not np.array_equal(readout_weights.numpy(), expected_readout_weights)
        or not math.isclose(
            elevation_mean, float(elevation_m.mean()), rel_tol=0, abs_tol=1e-12
        )
        or not math.isclose(
            elevation_scale, float(elevation_m.std()), rel_tol=0, abs_tol=1e-12
        )
    ):
        raise ProductionBundleIntegrityError(
            "Checkpoint spatial tensors differ from bound station geometry"
        )

    model = base.ConvLSTMForecast(
        in_ch=model_contract["input_channels"],
        hid_ch=model_contract["hidden_channels"],
        kernel=model_contract["kernel_size"],
        quantiles=model_contract["quantiles"],
    )
    state = record["state_dict"]
    if not isinstance(state, dict) or any(not isinstance(key, str) for key in state):
        raise ProductionBundleIntegrityError("Checkpoint state_dict is malformed")
    expected_state = model.state_dict()
    if set(state) != set(expected_state):
        raise ProductionBundleIntegrityError("Checkpoint state_dict keys changed")
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
        raise ProductionBundleIntegrityError("Checkpoint state_dict rejected") from exc
    model.eval()
    normalization = NormalizationState(
        displacement_mean=displacement_mean.numpy(),
        displacement_scale=displacement_scale.numpy(),
        exogenous_mean=exogenous_mean.numpy(),
        exogenous_scale=exogenous_scale.numpy(),
        delta_scale=delta_scale.numpy(),
        elevation_mean_m=elevation_mean,
        elevation_scale_m=elevation_scale,
    )
    binding = ArtifactBinding(path.resolve(), actual_sha256, actual_size)
    return LoadedCheckpoint(
        seed=seed,
        artifact=binding,
        source_bindings=checked_source,
        model=model,
        normalization=normalization,
        elevation_grid=elevation_grid.numpy(),
        readout_weights=readout_weights.numpy(),
        stations_model=tuple(feature_schema["stations_model"]),
    )


def _predict_checkpoint_p50_model_order(
    checkpoint: LoadedCheckpoint,
    frame: pd.DataFrame,
    profile: dict[str, Any],
) -> np.ndarray:
    torch.backends.mkldnn.enabled = False
    if len(frame) < profile["model"]["lookback_days"]:
        raise ProductionBundleIntegrityError("Inference frame is shorter than lookback")
    stations_model, xy, elevation_m = base.load_station_geometry(
        profile["model"]["displacement_columns"]
    )
    if tuple(stations_model) != checkpoint.stations_model:
        raise ProductionBundleIntegrityError("Inference station geometry changed")
    if (
        not math.isclose(
            float(elevation_m.mean()),
            checkpoint.normalization.elevation_mean_m,
            rel_tol=0,
            abs_tol=1e-12,
        )
        or not math.isclose(
            float(elevation_m.std()),
            checkpoint.normalization.elevation_scale_m,
            rel_tol=0,
            abs_tol=1e-12,
        )
    ):
        raise ProductionBundleIntegrityError("Inference elevation metadata changed")
    interp, _ = base.make_interpolator(xy, base.GRID_H, base.GRID_W)
    recent = frame.iloc[-profile["model"]["lookback_days"] :]
    inputs = make_inputs_from_normalization(
        recent,
        checkpoint.normalization,
        interp,
        checkpoint.elevation_grid,
        profile,
    )
    tensor = torch.from_numpy(inputs[None, :, :, :, :])
    with torch.no_grad():
        prediction_grid = checkpoint.model(tensor)
        normalized = base.readout_grid_at_stations(
            prediction_grid, torch.from_numpy(checkpoint.readout_weights)
        )[0]
    median_index = profile["model"]["quantiles"].index(0.5)
    median_delta = (
        normalized[median_index].to(torch.float64).numpy()
        * checkpoint.normalization.delta_scale
    )
    latest = recent[profile["model"]["displacement_columns"]].iloc[-1].to_numpy(
        dtype=np.float64
    )
    prediction = latest + median_delta
    if prediction.shape != (len(checkpoint.stations_model),) or not np.isfinite(
        prediction
    ).all():
        raise ProductionBundleIntegrityError("Checkpoint inference is invalid")
    return prediction


def _validate_checkpoint_against_activation_source(
    checkpoint: LoadedCheckpoint, prepared: PreparedTrainingData
) -> None:
    """Reject shape-valid preprocessing tensors from another source snapshot."""
    observed = checkpoint.normalization
    expected = prepared.normalization
    for name in (
        "displacement_mean",
        "displacement_scale",
        "exogenous_mean",
        "exogenous_scale",
        "delta_scale",
    ):
        if not np.array_equal(getattr(observed, name), getattr(expected, name)):
            raise ProductionBundleIntegrityError(
                f"Checkpoint {name} differs from activation-source preprocessing"
            )
    if (
        observed.elevation_mean_m != expected.elevation_mean_m
        or observed.elevation_scale_m != expected.elevation_scale_m
        or not np.array_equal(checkpoint.elevation_grid, prepared.elevation_grid)
        or not np.array_equal(checkpoint.readout_weights, prepared.readout_weights)
        or checkpoint.stations_model != prepared.stations_model
    ):
        raise ProductionBundleIntegrityError(
            "Checkpoint spatial preprocessing differs from activation source"
        )


def predict_p50(
    bundle: LoadedProductionBundle,
    frame: pd.DataFrame,
    *,
    profile: dict[str, Any] | None = None,
) -> dict[int, dict[str, float]]:
    """Run all five checkpoints and return cumulative P50 in live station order."""
    profile = _validated_profile(profile)
    model_to_index = {
        station: index for index, station in enumerate(bundle.stations_model)
    }
    result: dict[int, dict[str, float]] = {}
    for checkpoint in bundle.checkpoints:
        torch.backends.mkldnn.enabled = False
        model_order = _predict_checkpoint_p50_model_order(checkpoint, frame, profile)
        result[checkpoint.seed] = {
            station: float(model_order[model_to_index[station]])
            for station in bundle.stations_live
        }
    if tuple(result) != tuple(profile["model"]["seeds"]):
        raise ProductionBundleIntegrityError("Five-seed inference is incomplete")
    return result


def _validate_training_manifest(
    artifact: ArtifactBinding,
    *,
    outer: dict[str, Any],
    profile: dict[str, Any],
    source_bindings: SourceBindings,
    allow_test_epochs: bool,
) -> dict[str, Any]:
    payload, observed_sha = _load_json(artifact.path, name="training manifest")
    if observed_sha != artifact.sha256:
        raise ProductionBundleIntegrityError("Training manifest hash changed")
    record = _require_exact_keys(
        payload, TRAINING_MANIFEST_KEYS, name="training manifest"
    )
    if (
        record["schema_version"]
        != profile["model"]["training_manifest_schema_version"]
        or record["case"] != "ootang"
        or record["model_version"] != outer["model_version"]
        or record["created_at_utc"] != outer["created_at_utc"]
        or record["training_cutoff_date"] != outer["training_cutoff_date"]
        or record["input_schema_sha256"] != outer["input_schema_sha256"]
        or record["seeds"] != profile["model"]["seeds"]
        or record["best_seed_selected"] is not False
    ):
        raise ProductionBundleIntegrityError("Training manifest identity changed")
    _validate_source_binding_value(
        record["source_bindings"], source_bindings, name="training.source_bindings"
    )
    if record["stations"] != {
        "live": profile["source_feed"]["station_order_live"],
        "model": profile["source_feed"]["station_order_model"],
    }:
        raise ProductionBundleIntegrityError("Training station orders changed")
    policy = _require_exact_keys(
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
        name="training.training_policy",
    )
    if (
        policy["fit_policy"] != profile["model"]["fit_policy"]
        or policy["online_calibration_policy"]
        != profile["model"]["online_calibration_policy"]
        or policy["device"] != "cpu"
        or policy["deterministic_algorithms"] is not True
        or policy["torch_threads"] != 1
        or policy["learning_rate"] != profile["model"]["learning_rate"]
        or policy["holdout_windows"] != 0
        or policy["best_seed_selected"] is not False
        or policy["mkldnn_enabled"] is not False
    ):
        raise ProductionBundleIntegrityError("Training policy changed")
    epochs = _require_int(policy["epochs"], name="training.epochs", minimum=1)
    expected_epochs = profile["model"]["epochs"]
    if epochs == expected_epochs:
        if policy["test_epoch_override"] is not False:
            raise ProductionBundleIntegrityError("Live epochs cannot be test-marked")
    elif (
        not allow_test_epochs
        or policy["test_epoch_override"] is not True
        or epochs >= expected_epochs
    ):
        raise ProductionBundleIntegrityError(
            "Reduced-epoch checkpoint is forbidden outside explicit test loading"
        )
    _require_int(record["training_windows"], name="training_windows", minimum=365)
    seed_training = record["seed_training"]
    if not isinstance(seed_training, list) or len(seed_training) != 5:
        raise ProductionBundleIntegrityError("Seed training histories are incomplete")
    for seed, row in zip(profile["model"]["seeds"], seed_training):
        training_row = _require_exact_keys(
            row,
            {"seed", "epochs", "training_windows", "pinball_loss"},
            name=f"seed_training[{seed}]",
        )
        if (
            training_row["seed"] != seed
            or training_row["epochs"] != epochs
            or training_row["training_windows"] != record["training_windows"]
            or not isinstance(training_row["pinball_loss"], list)
            or len(training_row["pinball_loss"]) != epochs
        ):
            raise ProductionBundleIntegrityError("Seed training history changed")
        for loss in training_row["pinball_loss"]:
            _require_finite(loss, name=f"seed_training[{seed}].pinball_loss")
    if not isinstance(record["checkpoints"], list) or not isinstance(
        record["reload_replay"], list
    ):
        raise ProductionBundleIntegrityError("Training checkpoint/replay rows invalid")
    if record["checkpoints"] != outer["checkpoints"]:
        raise ProductionBundleIntegrityError("Training/outer checkpoints differ")
    if len(record["reload_replay"]) != 5:
        raise ProductionBundleIntegrityError("Training replay rows are incomplete")
    for seed, replay in zip(profile["model"]["seeds"], record["reload_replay"]):
        replay_record = _require_exact_keys(
            replay,
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
        if replay_record["seed"] != seed or replay_record["equivalent"] is not True:
            raise ProductionBundleIntegrityError("Checkpoint reload replay failed")
        difference = _require_finite(
            replay_record["max_abs_difference_mm"], name="replay difference"
        )
        tolerance = _require_finite(
            replay_record["absolute_tolerance_mm"], name="replay tolerance"
        )
        if tolerance != RELOAD_ABSOLUTE_TOLERANCE_MM or difference > tolerance:
            raise ProductionBundleIntegrityError("Checkpoint replay tolerance exceeded")
        for key in ("in_memory_p50_mm", "reloaded_p50_mm"):
            values = replay_record[key]
            if (
                not isinstance(values, list)
                or len(values) != 8
                or any(
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or not math.isfinite(float(value))
                    for value in values
                )
            ):
                raise ProductionBundleIntegrityError("Replay prediction vector invalid")
    implementation = _require_exact_keys(
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
    for key in (
        "deploy_profile_sha256",
        "base_model_sha256",
        "producer_sha256",
        "pyproject_sha256",
        "uv_lock_sha256",
    ):
        _require_sha256(implementation[key], name=f"implementation.{key}")
    if implementation["deploy_profile_sha256"] != profile["_profile_sha256"]:
        raise ProductionBundleIntegrityError("Training deploy profile binding changed")
    if implementation["base_model_sha256"] != _sha256_file(Path(base.__file__)):
        raise ProductionBundleIntegrityError("Training base model binding changed")
    if implementation["producer_sha256"] != _sha256_file(Path(__file__)):
        raise ProductionBundleIntegrityError("Training producer binding changed")
    if implementation["pyproject_sha256"] != _sha256_file(ROOT / "pyproject.toml"):
        raise ProductionBundleIntegrityError("Training pyproject binding changed")
    if implementation["uv_lock_sha256"] != _sha256_file(ROOT / "uv.lock"):
        raise ProductionBundleIntegrityError("Training dependency lock changed")
    if (
        implementation["torch_version"] != torch.__version__
        or implementation["numpy_version"] != np.__version__
    ):
        raise ProductionBundleIntegrityError("Training runtime version changed")
    expected_identity = _bundle_identity(
        profile,
        source_bindings,
        record["checkpoints"],
        epochs=epochs,
        training_windows=record["training_windows"],
    )
    expected_version = (
        f"{profile['model']['model_version_prefix']}-{expected_identity[:16]}"
    )
    if record["model_version"] != expected_version:
        raise ProductionBundleIntegrityError("Model version identity hash changed")
    return record


def _load_verified_live_profile(profile: dict[str, Any]) -> dict[str, Any]:
    project_root = Path(profile["_project_root"])
    live_path = _resolve(profile["live_profile"]["path"], project_root=project_root)
    live, observed_sha256 = _load_json(live_path, name="E2-A live profile")
    if observed_sha256 != profile["live_profile"]["expected_sha256"]:
        raise ProductionBundleConfigError("E2-A live profile hash changed")
    return live


def _load_live_target(profile: dict[str, Any]) -> dict[str, str]:
    live = _load_verified_live_profile(profile)
    target = live.get("target")
    if not isinstance(target, dict):
        raise ProductionBundleConfigError("E2-A target contract is missing")
    expected = {
        "name": "next_natural_day_cumulative_displacement",
        "unit": "mm",
        "horizon": "P1D",
    }
    observed = {key: target.get(key) for key in expected}
    if observed != expected:
        raise ProductionBundleConfigError("E2-A target contract changed")
    return expected


def _validate_bundle_time_boundary(
    bindings: SourceBindings,
    profile: dict[str, Any],
    *,
    created_at: datetime,
    machine_now: datetime,
) -> None:
    if machine_now.tzinfo is None or created_at.tzinfo is None:
        raise ProductionBundleIntegrityError("Bundle clocks must be timezone-aware")
    created_at = created_at.astimezone(timezone.utc)
    machine_now = machine_now.astimezone(timezone.utc)
    captured_at = _utc_datetime(
        bindings.activation_captured_at_utc,
        name="source.activation_captured_at_utc",
    )
    target_date = bindings.training_cutoff_date + timedelta(days=1)
    target_start = datetime.combine(
        target_date,
        time.min,
        tzinfo=ZoneInfo(profile["source_feed"]["date_timezone"]),
    ).astimezone(timezone.utc)
    if created_at < captured_at:
        raise ProductionBundleIntegrityError(
            "Bundle creation predates activation-source capture"
        )
    if created_at > machine_now:
        raise ProductionBundleIntegrityError("Bundle creation is in the machine future")
    if created_at >= target_start:
        raise ProductionBundleIntegrityError(
            "Bundle was not durable before the first target natural day"
        )


def _target_start_utc(
    bindings: SourceBindings, profile: dict[str, Any]
) -> datetime:
    target_date = bindings.training_cutoff_date + timedelta(days=1)
    return datetime.combine(
        target_date,
        time.min,
        tzinfo=ZoneInfo(profile["source_feed"]["date_timezone"]),
    ).astimezone(timezone.utc)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _publication_clock(now: datetime | None) -> datetime:
    return now if now is not None else _utc_now()


def _validate_publication_deadline(
    bindings: SourceBindings,
    profile: dict[str, Any],
    *,
    machine_now: datetime,
) -> None:
    if machine_now.tzinfo is None:
        raise ProductionBundleIntegrityError("Bundle clocks must be timezone-aware")
    if machine_now.astimezone(timezone.utc) >= _target_start_utc(bindings, profile):
        raise ProductionBundleIntegrityError(
            "Bundle publication reached the first target natural day"
        )


def _runtime_root(profile: dict[str, Any], runtime_root: Path | None) -> Path:
    if runtime_root is not None:
        return Path(runtime_root).resolve()
    project_root = Path(profile["_project_root"]).resolve()
    relative = _relative_runtime_path(profile["runtime"]["root"], name="runtime.root")
    return _require_path_within(
        project_root / relative,
        project_root,
        name="runtime.root",
    )


def _runtime_path(
    profile: dict[str, Any], key: str, runtime_root: Path | None
) -> Path:
    if key not in RUNTIME_CHILD_KEYS:
        raise ProductionBundleConfigError(f"Unknown runtime path contract: {key}")
    root = _runtime_root(profile, runtime_root)
    relative = _relative_runtime_path(
        profile["runtime"][key], name=f"runtime.{key}"
    )
    return _require_path_within(root / relative, root, name=f"runtime.{key}")


def _model_manifest_path(profile: dict[str, Any], runtime_root: Path | None) -> Path:
    return _runtime_path(profile, "model_manifest", runtime_root)


def _live_runner_lock_path(
    profile: dict[str, Any], runtime_root: Path | None
) -> Path:
    live = _load_verified_live_profile(profile)
    live_runtime = _require_exact_keys(
        live.get("runtime"), LIVE_RUNTIME_KEYS, name="E2-A runtime"
    )
    live_root = _relative_runtime_path(
        live_runtime["root"], name="E2-A runtime.root"
    )
    deploy_root = _relative_runtime_path(
        profile["runtime"]["root"], name="runtime.root"
    )
    if live_root != deploy_root:
        raise ProductionBundleConfigError(
            "E2-A and E2-B runtime roots must be identical"
        )
    root = _runtime_root(profile, runtime_root)
    resolved_paths: dict[str, Path] = {}
    for key in sorted(LIVE_RUNTIME_PATH_KEYS):
        relative = _relative_runtime_path(
            live_runtime[key], name=f"E2-A runtime.{key}"
        )
        resolved_paths[key] = _require_path_within(
            root / relative, root, name=f"E2-A runtime.{key}"
        )
    runner_lock = resolved_paths["lock"]
    deploy_lock = _runtime_path(profile, "deploy_lock", runtime_root)
    if runner_lock == deploy_lock:
        raise ProductionBundleConfigError(
            "E2-A runner lock must differ from the E2-B deploy lock"
        )
    return runner_lock


def _acquire_publish_runner_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = path.open("a+b")
    except OSError as exc:
        raise ProductionBundleIntegrityError(
            f"Cannot open E2-A runner lock: {path}"
        ) from exc
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise ProductionBundleBusyError(
            "E2-A runner owns runner.lock during bundle publication"
        ) from exc
    except OSError as exc:
        handle.close()
        raise ProductionBundleIntegrityError(
            f"Cannot acquire E2-A runner lock: {path}"
        ) from exc
    return handle


def _validate_configured_activation_source(
    source: object,
    profile: dict[str, Any],
    *,
    runtime_root: Path | None,
) -> None:
    configured_path = _runtime_path(
        profile, "activation_source_manifest", runtime_root
    )
    activation = _artifact_from_source(
        getattr(source, "activation_manifest", None), name="activation_manifest"
    )
    if activation.path != configured_path:
        raise ProductionBundleIntegrityError(
            "Canonical source activation reference is not the configured immutable path"
        )


def _publish_bundle_manifest(
    path: Path,
    value: bytes,
    *,
    bindings: SourceBindings,
    profile: dict[str, Any],
    created_at: datetime,
    now: datetime | None,
    runtime_root: Path | None,
) -> None:
    """Atomically publish only while fresh clocks remain before target start."""
    runner_lock = _acquire_publish_runner_lock(
        _live_runner_lock_path(profile, runtime_root)
    )
    try:
        before = _publication_clock(now)
        _validate_bundle_time_boundary(
            bindings,
            profile,
            created_at=created_at,
            machine_now=before,
        )
        _validate_publication_deadline(
            bindings, profile, machine_now=before
        )
        created = _atomic_write_bytes(path, value, replace=False)
        after = _publication_clock(now)
        try:
            _validate_bundle_time_boundary(
                bindings,
                profile,
                created_at=created_at,
                machine_now=after,
            )
            _validate_publication_deadline(
                bindings, profile, machine_now=after
            )
        except ProductionBundleIntegrityError:
            if created:
                _remove_exact_publication(path, value)
            raise
    finally:
        fcntl.flock(runner_lock.fileno(), fcntl.LOCK_UN)
        runner_lock.close()


def load_deploy_bundle(
    profile: dict[str, Any] | None = None,
    *,
    runtime_root: Path | None = None,
    source: object | None = None,
    now: datetime | None = None,
    _allow_test_epochs: bool = False,
) -> LoadedProductionBundle:
    """Strictly load the full outer manifest, training record, and checkpoints."""
    if _allow_test_epochs and os.environ.get(TEST_EPOCH_ENV) != "1":
        raise ProductionBundleConfigError(
            "Reduced-epoch bundle loading requires explicit test mode"
        )
    profile = _validated_profile(profile)
    if source is None:
        from monitoring import ootang_live_source

        source = ootang_live_source.load_activation_source(
            profile,
            runtime_root=runtime_root,
            project_root=Path(profile["_project_root"]),
        )
    _validate_configured_activation_source(
        source, profile, runtime_root=runtime_root
    )
    expected_source = _source_bindings(source)
    prepared_source = prepare_training_data(source, profile)
    manifest_path = _model_manifest_path(profile, runtime_root)
    outer, manifest_sha256 = _load_json(
        manifest_path, name="five-seed model bundle manifest"
    )
    record = _require_exact_keys(
        outer, OUTER_MANIFEST_KEYS, name="five-seed model bundle manifest"
    )
    model = profile["model"]
    if (
        record["schema_version"] != model["bundle_schema_version"]
        or record["case"] != "ootang"
        or record["training_cutoff_date"]
        != expected_source.training_cutoff_date.isoformat()
        or record["stations"] != profile["source_feed"]["station_order_live"]
        or record["seeds"] != model["seeds"]
        or record["best_seed_selected"] is not False
        or record["target"] != _load_live_target(profile)
        or record["input_schema_sha256"] != input_schema_sha256(profile)
    ):
        raise ProductionBundleIntegrityError("Outer model bundle identity changed")
    if (
        not isinstance(record["model_version"], str)
        or not record["model_version"].startswith(model["model_version_prefix"] + "-")
    ):
        raise ProductionBundleIntegrityError("Model version changed")
    created_at = _parse_utc(record["created_at_utc"], name="model.created_at_utc")
    created_at_datetime = _utc_datetime(
        created_at, name="model.created_at_utc"
    )
    machine_now = now or datetime.now(timezone.utc)
    _validate_bundle_time_boundary(
        expected_source,
        profile,
        created_at=created_at_datetime,
        machine_now=machine_now,
    )
    training_artifact = _artifact_from_value(
        record["training_manifest"], name="model.training_manifest"
    )
    checkpoints_value = record["checkpoints"]
    if not isinstance(checkpoints_value, list) or len(checkpoints_value) != 5:
        raise ProductionBundleIntegrityError("Outer checkpoints are incomplete")
    loaded: list[LoadedCheckpoint] = []
    for expected_seed, item in zip(model["seeds"], checkpoints_value):
        checkpoint_record = _require_exact_keys(
            item, {"seed", "artifact"}, name=f"checkpoint[{expected_seed}]"
        )
        if checkpoint_record["seed"] != expected_seed:
            raise ProductionBundleIntegrityError("Checkpoint seed order changed")
        artifact = _artifact_from_value(
            checkpoint_record["artifact"], name=f"checkpoint[{expected_seed}].artifact"
        )
        checkpoint = load_safe_checkpoint(
            artifact, profile=profile, source_bindings=expected_source
        )
        if checkpoint.seed != expected_seed:
            raise ProductionBundleIntegrityError("Checkpoint embedded seed changed")
        _validate_checkpoint_against_activation_source(checkpoint, prepared_source)
        loaded.append(checkpoint)
    _validate_training_manifest(
        training_artifact,
        outer=record,
        profile=profile,
        source_bindings=expected_source,
        allow_test_epochs=_allow_test_epochs,
    )
    return LoadedProductionBundle(
        manifest_path=manifest_path.resolve(),
        manifest_sha256=manifest_sha256,
        model_version=record["model_version"],
        created_at_utc=created_at,
        training_cutoff=expected_source.training_cutoff_date,
        stations_live=tuple(record["stations"]),
        stations_model=tuple(profile["source_feed"]["station_order_model"]),
        checkpoints=tuple(loaded),
        source_bindings=expected_source,
        input_schema_sha256=record["input_schema_sha256"],
        training_manifest=training_artifact,
        manifest=record,
    )


def _bundle_identity(
    profile: dict[str, Any],
    source_bindings: SourceBindings,
    checkpoints: list[dict[str, object]],
    *,
    epochs: int,
    training_windows: int,
) -> str:
    content_checkpoints = []
    for value in checkpoints:
        checkpoint = _require_exact_keys(
            value, {"seed", "artifact"}, name="bundle identity checkpoint"
        )
        artifact = _require_exact_keys(
            checkpoint["artifact"], ARTIFACT_KEYS, name="bundle identity artifact"
        )
        content_checkpoints.append(
            {
                "seed": checkpoint["seed"],
                "sha256": artifact["sha256"],
                "size_bytes": artifact["size_bytes"],
            }
        )
    return _canonical_sha256(
        {
            "schema_version": "ootang_five_seed_bundle_identity_v1",
            "source_bindings": source_bindings.as_dict(),
            "input_schema_sha256": input_schema_sha256(profile),
            "fit_policy": profile["model"]["fit_policy"],
            "seeds": profile["model"]["seeds"],
            "epochs": epochs,
            "training_windows": training_windows,
            "checkpoints": content_checkpoints,
        }
    )


def build_model_bundle(
    source: object,
    profile: dict[str, Any] | None = None,
    *,
    runtime_root: Path | None = None,
    now: datetime | None = None,
    _epochs_override: int | None = None,
) -> tuple[LoadedProductionBundle, str]:
    """Build once or return a byte-verified identical existing activation bundle."""
    profile = _validated_profile(profile)
    epochs = _training_epochs(profile, _epochs_override)
    bindings = _source_bindings(source)
    _validate_configured_activation_source(
        source, profile, runtime_root=runtime_root
    )
    manifest_path = _model_manifest_path(profile, runtime_root)
    if manifest_path.exists():
        try:
            existing = load_deploy_bundle(
                profile,
                runtime_root=runtime_root,
                source=source,
                now=now,
                _allow_test_epochs=_epochs_override is not None,
            )
        except ProductionBundleError as exc:
            raise ProductionBundleConflictError(
                "Existing model target differs or is not replayable"
            ) from exc
        existing_training, _ = _load_json(
            existing.training_manifest.path, name="existing training manifest"
        )
        existing_policy = existing_training.get("training_policy")
        if not isinstance(existing_policy, dict) or existing_policy.get("epochs") != epochs:
            raise ProductionBundleConflictError(
                "Existing model target was trained with different epoch semantics"
            )
        return existing, "reused_identical"

    prepared = prepare_training_data(source, profile)
    objects_root = _runtime_path(profile, "objects", runtime_root)
    checkpoint_rows: list[dict[str, object]] = []
    replay_rows: list[dict[str, object]] = []
    training_summaries: list[dict[str, object]] = []
    for seed in profile["model"]["seeds"]:
        model, losses = _train_seed(
            prepared, profile, seed=seed, epochs=epochs
        )
        payload = _checkpoint_payload(
            model, prepared, profile, bindings, seed=seed
        )
        checkpoint_artifact = _publish_object(
            objects_root, _serialize_safe_checkpoint(payload)
        )
        reloaded = load_safe_checkpoint(
            checkpoint_artifact, profile=profile, source_bindings=bindings
        )
        transient = LoadedCheckpoint(
            seed=seed,
            artifact=checkpoint_artifact,
            source_bindings=bindings,
            model=model,
            normalization=prepared.normalization,
            elevation_grid=prepared.elevation_grid,
            readout_weights=prepared.readout_weights,
            stations_model=prepared.stations_model,
        )
        in_memory = _predict_checkpoint_p50_model_order(
            transient, prepared.frame, profile
        )
        replayed = _predict_checkpoint_p50_model_order(
            reloaded, prepared.frame, profile
        )
        max_difference = float(np.max(np.abs(in_memory - replayed)))
        if max_difference > RELOAD_ABSOLUTE_TOLERANCE_MM:
            raise ProductionBundleIntegrityError(
                f"Seed {seed} checkpoint reload changed inference"
            )
        checkpoint_rows.append(
            {"seed": seed, "artifact": checkpoint_artifact.as_dict()}
        )
        replay_rows.append(
            {
                "seed": seed,
                "in_memory_p50_mm": [float(value) for value in in_memory],
                "reloaded_p50_mm": [float(value) for value in replayed],
                "max_abs_difference_mm": max_difference,
                "absolute_tolerance_mm": RELOAD_ABSOLUTE_TOLERANCE_MM,
                "equivalent": True,
            }
        )
        training_summaries.append(
            {
                "seed": seed,
                "epochs": epochs,
                "training_windows": prepared.training_windows,
                "pinball_loss": losses,
            }
        )

    identity = _bundle_identity(
        profile,
        bindings,
        checkpoint_rows,
        epochs=epochs,
        training_windows=prepared.training_windows,
    )
    model_version = f"{profile['model']['model_version_prefix']}-{identity[:16]}"
    creation_clock = _publication_clock(now)
    _validate_bundle_time_boundary(
        bindings,
        profile,
        created_at=creation_clock,
        machine_now=creation_clock,
    )
    created_at = _format_utc(creation_clock)
    training_manifest = {
        "schema_version": profile["model"]["training_manifest_schema_version"],
        "case": "ootang",
        "model_version": model_version,
        "created_at_utc": created_at,
        "training_cutoff_date": bindings.training_cutoff_date.isoformat(),
        "source_bindings": bindings.as_dict(),
        "input_schema_sha256": input_schema_sha256(profile),
        "training_policy": {
            "fit_policy": profile["model"]["fit_policy"],
            "online_calibration_policy": profile["model"][
                "online_calibration_policy"
            ],
            "device": "cpu",
            "deterministic_algorithms": True,
            "torch_threads": 1,
            "epochs": epochs,
            "learning_rate": profile["model"]["learning_rate"],
            "holdout_windows": 0,
            "best_seed_selected": False,
            "mkldnn_enabled": False,
            "test_epoch_override": epochs != profile["model"]["epochs"],
        },
        "stations": {
            "live": profile["source_feed"]["station_order_live"],
            "model": profile["source_feed"]["station_order_model"],
        },
        "seeds": profile["model"]["seeds"],
        "best_seed_selected": False,
        "training_windows": prepared.training_windows,
        "seed_training": training_summaries,
        "checkpoints": checkpoint_rows,
        "reload_replay": replay_rows,
        "implementation": {
            "deploy_profile_sha256": profile["_profile_sha256"],
            "base_model_sha256": _sha256_file(Path(base.__file__)),
            "producer_sha256": _sha256_file(Path(__file__)),
            "pyproject_sha256": _sha256_file(ROOT / "pyproject.toml"),
            "uv_lock_sha256": _sha256_file(ROOT / "uv.lock"),
            "torch_version": torch.__version__,
            "numpy_version": np.__version__,
        },
    }
    if [row["seed"] for row in training_summaries] != profile["model"]["seeds"]:
        raise ProductionBundleIntegrityError("Training seed summaries are incomplete")
    training_artifact = _publish_object(
        objects_root, _pretty_json_bytes(training_manifest)
    )
    outer = {
        "schema_version": profile["model"]["bundle_schema_version"],
        "case": "ootang",
        "model_version": model_version,
        "created_at_utc": created_at,
        "training_cutoff_date": bindings.training_cutoff_date.isoformat(),
        "stations": profile["source_feed"]["station_order_live"],
        "seeds": profile["model"]["seeds"],
        "best_seed_selected": False,
        "target": _load_live_target(profile),
        "input_schema_sha256": input_schema_sha256(profile),
        "training_manifest": training_artifact.as_dict(),
        "checkpoints": checkpoint_rows,
    }
    _publish_bundle_manifest(
        manifest_path,
        _pretty_json_bytes(outer),
        bindings=bindings,
        profile=profile,
        created_at=creation_clock,
        now=now,
        runtime_root=runtime_root,
    )
    loaded = load_deploy_bundle(
        profile,
        runtime_root=runtime_root,
        source=source,
        now=now,
        _allow_test_epochs=_epochs_override is not None,
    )
    return loaded, "created"


def _status_path(profile: dict[str, Any], runtime_root: Path | None) -> Path:
    return _runtime_path(profile, "bundle_status", runtime_root)


def _write_status(
    profile: dict[str, Any],
    *,
    runtime_root: Path | None,
    now: datetime,
    bundle_status: str,
    reason: str,
    bundle: LoadedProductionBundle | None = None,
    checkpoint_inference_replayed: bool = False,
) -> Path:
    if checkpoint_inference_replayed and bundle is None:
        raise ProductionBundleIntegrityError(
            "Checkpoint replay cannot be reported without a loaded bundle"
        )
    status = {
        "schema_version": "ootang_model_bundle_status_v1",
        "profile_id": profile["profile_id"],
        "deploy_profile_sha256": profile["_profile_sha256"],
        "artifact_status": profile["artifact_status"],
        "bundle_status": bundle_status,
        "reason": reason,
        "recorded_at_utc": _format_utc(now),
        "runtime_root": str(_runtime_root(profile, runtime_root)),
        "model_manifest_path": str(_model_manifest_path(profile, runtime_root)),
        "model_manifest_sha256": (
            bundle.manifest_sha256 if bundle is not None else None
        ),
        "model_version": bundle.model_version if bundle is not None else None,
        "training_cutoff_date": (
            bundle.training_cutoff.isoformat() if bundle is not None else None
        ),
        "seeds": profile["model"]["seeds"],
        "best_seed_selected": False,
        "safe_checkpoint_loader_implemented": True,
        "producer_checkpoint_inference_replay_implemented": True,
        "safe_checkpoint_loading_exercised": bundle is not None,
        "producer_checkpoint_inference_replayed": checkpoint_inference_replayed,
        "formal_warning_output": False,
        "independent_label_used": False,
        "confirmatory_external_validation": False,
        "vajont_used": False,
        "e2_live_evidence_eligible": False,
        "real_activation_ready": False,
    }
    path = _status_path(profile, runtime_root)
    _atomic_write_bytes(path, _pretty_json_bytes(status), replace=True)
    return path


def run_once(
    config_path: Path = DEFAULT_CONFIG_PATH,
    *,
    runtime_root: Path | None = None,
    now: datetime | None = None,
) -> Path:
    """Perform one machine deploy poll and write waiting/ready/blocked status."""
    injected_now = now
    checked_at = now or datetime.now(timezone.utc)
    profile = load_deploy_profile(config_path)
    lock_path = _runtime_path(profile, "deploy_lock", runtime_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ProductionBundleBusyError(
                "another machine deploy cycle owns the lock"
            ) from exc
        try:
            activation_source = _runtime_path(
                profile, "activation_source_manifest", runtime_root
            )
            if not activation_source.is_file():
                return _write_status(
                    profile,
                    runtime_root=runtime_root,
                    now=checked_at,
                    bundle_status="waiting_for_semantically_validated_source",
                    reason="missing:activation_source_manifest",
                )
            from monitoring import ootang_live_source

            source = ootang_live_source.load_activation_source(
                profile,
                runtime_root=runtime_root,
                project_root=Path(profile["_project_root"]),
            )
            bundle, disposition = build_model_bundle(
                source,
                profile,
                runtime_root=runtime_root,
                now=injected_now,
            )
            completed_at = injected_now or datetime.now(timezone.utc)
            return _write_status(
                profile,
                runtime_root=runtime_root,
                now=completed_at,
                bundle_status="ready",
                reason=disposition,
                bundle=bundle,
                checkpoint_inference_replayed=disposition == "created",
            )
        except Exception as exc:
            failed_at = injected_now or datetime.now(timezone.utc)
            _write_status(
                profile,
                runtime_root=runtime_root,
                now=failed_at,
                bundle_status="blocked_integrity",
                reason=f"{type(exc).__name__}:{exc}",
            )
            raise
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the E2-B five-seed content-addressed model bundle"
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--runtime-root", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        status_path = run_once(args.config, runtime_root=args.runtime_root)
    except ProductionBundleBusyError as exc:
        print(f"[ootang-production-bundle] busy: {exc}", file=sys.stderr)
        return 3
    except ProductionBundleError as exc:
        print(f"[ootang-production-bundle] blocked: {exc}", file=sys.stderr)
        return 2
    print(f"[ootang-production-bundle] status: {status_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
