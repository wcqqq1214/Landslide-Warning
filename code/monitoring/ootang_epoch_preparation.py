"""Materialize and smoke-check same-origin Ootang epoch candidates (R2a).

R2a consumes the immutable R1 candidate chain.  It resolves the transitive
local Python import closure, publishes a content-addressed executable tree,
and runs an isolated import/compile/prerequisite-reload smoke check.  It does
not drain an old epoch, select an active epoch, or authorize E2 evidence.
"""

from __future__ import annotations

import argparse
import ast
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
CODE_DIR = ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from monitoring import ootang_epoch_registry as registry  # noqa: E402


DEFAULT_CONFIG_PATH = ROOT / "config" / "ootang_epoch_preparation.v1.json"
DEFAULT_CONFIG_SHA256 = (
    "c6ec0b1f340effd9e3fd5cd1a0ee67ebca9ffa4dc743a9cb36d701850dc875f9"
)
IMPLEMENTATION_LOGICAL_PATH = "code/monitoring/ootang_epoch_preparation.py"
REGISTRY_PROFILE_SHA256 = registry.DEFAULT_CONFIG_SHA256
REGISTRY_IMPLEMENTATION_SHA256 = (
    "1418b754012b71b296c200539efa846cea63e5a4374e44d216bd656f8b047b9c"
)
ZERO_HASH = "0" * 64
MAX_CONTROL_BYTES = 8 * 1024 * 1024
MAX_SMOKE_OUTPUT_BYTES = 1024 * 1024
HEX_DIGITS = frozenset("0123456789abcdef")
MODULE_RE = re.compile(r"^[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*$")

EXPECTED_REGISTRY = {
    "profile_path": "config/ootang_epoch_registry.v1.json",
    "profile_sha256": REGISTRY_PROFILE_SHA256,
    "implementation_path": "code/monitoring/ootang_epoch_registry.py",
    "implementation_sha256": REGISTRY_IMPLEMENTATION_SHA256,
}
EXPECTED_ROOT_ENVIRONMENT = {
    "pyproject_path": "pyproject.toml",
    "pyproject_sha256": (
        "bcc6b1e10534d0f2ed2c5e7510ee1761c7be4ca7a52fc743f4b266afedcf15f0"
    ),
    "uv_lock_path": "uv.lock",
    "uv_lock_sha256": (
        "f1d880ae806b501cd946f0c7564a552e288c7f3b2833a1801132675f5ec8841c"
    ),
    "required_python_version": "3.10.20",
    "required_uv_version": "0.12.5",
    "required_uv_path": "/opt/homebrew/Cellar/uv/0.12.5/bin/uv",
    "required_uv_sha256": (
        "debc68c21b3bb1086e20d9889b53ff5ccf9ef343fda9a57dc2022212e3511125"
    ),
    "root_smoke_domain": {
        "distribution_count": 43,
        "distribution_inventory_sha256": (
            "007dc4fbca73360ff0ca20b744509d2a3b0fbd44711234e64c35715032ebc34e"
        ),
        "python_executable_sha256": (
            "694bcacb03f978975c57396caaec10a42d3fec62a789f82f8661197c9dd17a2e"
        ),
        "python_soabi": "cpython-310-darwin",
        "platform": "macOS-26.5.1-arm64-arm-64bit",
    },
    "trusted_time_smoke_domain": {
        "distribution_count": 5,
        "distribution_inventory_sha256": (
            "622737b4a53f420c3e895e4d74205b456fd7efa15b0e4a9250e760c7c24264c5"
        ),
        "python_executable_sha256": (
            "694bcacb03f978975c57396caaec10a42d3fec62a789f82f8661197c9dd17a2e"
        ),
        "python_soabi": "cpython-310-darwin",
        "platform": "macOS-26.5.1-arm64-arm-64bit",
    },
}
EXPECTED_RUNTIME = {
    "root": "runtime/ootang_epoch_registry_v1",
    "manager_lock": "manager.lock",
    "status": "preparation_status.json",
    "head": "preparation_head.json",
    "events": "preparation_events",
    "objects": "objects/sha256",
    "capsules": "executable_capsules",
    "trees": "executable_trees",
    "smoke_receipts": "executable_smoke_receipts/sha256",
}
EXPECTED_PROTOCOL = {
    "event_schema_version": "ootang_epoch_preparation_event_v1",
    "head_schema_version": "ootang_epoch_preparation_head_v1",
    "status_schema_version": "ootang_epoch_preparation_status_v1",
    "capsule_schema_version": "ootang_epoch_executable_capsule_v1",
    "smoke_receipt_schema_version": "ootang_epoch_executable_smoke_receipt_v1",
    "initial_previous_entry_sha256": ZERO_HASH,
    "canonical_json": "utf8_sort_keys_compact_no_nan_trailing_lf",
    "same_candidate_same_semantics": "idempotent_preserve_first_bytes",
    "same_candidate_changed_semantics": "machine_revalidate_on_implementation_change",
    "active_switch": "not_implemented_r2a_preparation_only",
}
EXPECTED_EXECUTABLE_CAPSULE = {
    "root_modules": [
        "monitoring.ootang_prequential_cycle_v3",
        "monitoring.ootang_trusted_time_shadow",
    ],
    "closure_seed_modules": [
        "monitoring.ootang_prequential_cycle_v3",
        "monitoring.ootang_trusted_time_shadow",
        "monitoring.ootang_trusted_time_shadow_core",
    ],
    "local_packages": ["convlstm", "monitoring", "warning"],
    "expected_local_module_count": 22,
    "augmentation_files": [
        {
            "path": "code/convlstm/__init__.py",
            "expected_sha256": (
                "4b51a22e572727ec42d888387891d94bf8ffa0ef17245be9b453b9b56967f127"
            ),
        },
        {
            "path": "code/monitoring/__init__.py",
            "expected_sha256": (
                "215064d50f1aa515312aeee43318de1231bcfb0ee6614811d1f3b2f83b5ff82a"
            ),
        },
    ],
    "required_logical_paths": [
        "code/monitoring/ootang_trusted_time_shadow_core.py",
        "config/ootang_prequential_cycle.v3.json",
        "config/ootang_trusted_time_shadow.v1.json",
        "tools/ootang_trusted_time_runtime/pyproject.toml",
        "tools/ootang_trusted_time_runtime/uv.lock",
    ],
    "isolated_smoke_timeout_seconds": 120,
    "numerical_replay_absolute_tolerance_mm": 0.000001,
    "reject_dynamic_local_imports": True,
    "materialize_all_r1_capsule_artifacts": False,
    "canonical_project_root_required": True,
    "canonical_slot_root_required": True,
    "relocatable": False,
    "portable_offline_runtime": False,
}
EXPECTED_CAPABILITIES = {
    "immutable_candidate_registry_verified": True,
    "transitive_local_import_closure_implemented": True,
    "content_addressed_executable_materialization_implemented": True,
    "same_origin_executable_preflight_implemented": True,
    "portable_offline_runtime": False,
    "isolated_import_compile_smoke_implemented": True,
    "isolated_candidate_prerequisite_reload_smoke_implemented": True,
    "five_seed_numerical_replay_smoke_implemented": True,
    "trusted_time_isolated_domain_smoke_implemented": True,
    "old_epoch_drain_implemented": False,
    "active_epoch_switch_implemented": False,
    "automatic_epoch_rotation_implemented": False,
    "trusted_anchor_receipt_verified": False,
    "e2_live_evidence_eligible": False,
    "real_activation_ready": False,
    "formal_warning_output": False,
}
FALSE_CLAIM_KEYS = {
    "old_epoch_drain_implemented",
    "active_epoch_switch_implemented",
    "automatic_epoch_rotation_implemented",
    "trusted_anchor_receipt_verified",
    "e2_live_evidence_eligible",
    "real_activation_ready",
    "formal_warning_output",
}
REFERENCE_KEYS = {"path", "sha256", "size_bytes"}
CAPSULE_ARTIFACT_KEYS = {
    "logical_path",
    "sha256",
    "size_bytes",
    "object_path",
    "source",
    "role",
}


class EpochPreparationError(RuntimeError):
    """Base error for R2a executable candidate preparation."""


class EpochPreparationConfigError(EpochPreparationError):
    """The reviewed R2a contract or one of its fixed bindings changed."""


class EpochPreparationIntegrityError(EpochPreparationError):
    """A candidate, executable capsule, tree, smoke receipt, or chain changed."""


class EpochPreparationBusyError(EpochPreparationError):
    """The shared R1/R2 epoch manager lock is held by another process."""


@dataclass(frozen=True)
class PreparationPaths:
    root: Path
    manager_lock: Path
    status: Path
    head: Path
    events: Path
    objects: Path
    capsules: Path
    trees: Path
    smoke_receipts: Path


SmokeRunner = Callable[
    [Mapping[str, Any], PreparationPaths, Mapping[str, Any], Path],
    dict[str, object],
]


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _require_hash(value: object, *, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in HEX_DIGITS for character in value)
    ):
        raise EpochPreparationIntegrityError(f"{name} is not a lowercase SHA-256")
    return value


def _require_text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise EpochPreparationIntegrityError(
            f"{name} must be a canonical non-empty string"
        )
    return value


def _exact_object(value: object, keys: set[str], *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise EpochPreparationIntegrityError(f"{name} keys changed")
    return value


def _relative_path(value: object, *, name: str) -> PurePosixPath:
    text = _require_text(value, name=name)
    path = PurePosixPath(text)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or "\\" in text
    ):
        raise EpochPreparationIntegrityError(f"{name} is not project-relative")
    return path


def _profile_artifact(project_root: Path, logical_path: object, *, name: str) -> Path:
    relative = _relative_path(logical_path, name=name)
    try:
        return registry._contained(  # noqa: SLF001
            project_root, str(relative), name=name
        )
    except registry.EpochRegistryError as exc:
        raise EpochPreparationIntegrityError(str(exc)) from exc


def load_preparation_profile(
    path: Path = DEFAULT_CONFIG_PATH, *, project_root: Path = ROOT
) -> dict[str, Any]:
    """Load only the reviewed R2a profile and verify all fixed R1 bindings."""

    root = project_root.resolve()
    resolved = path if path.is_absolute() else root / path
    resolved = registry._absolute_lexical(resolved)  # noqa: SLF001
    try:
        resolved.relative_to(root)
        registry._ensure_existing_parents_not_symlinks(  # noqa: SLF001
            root, resolved
        )
    except (registry.EpochRegistryError, ValueError) as exc:
        raise EpochPreparationConfigError(
            "Epoch preparation profile path escapes the canonical project root"
        ) from exc
    if (
        root != ROOT.resolve()
        or resolved != registry._absolute_lexical(DEFAULT_CONFIG_PATH)  # noqa: SLF001
    ):
        raise EpochPreparationConfigError(
            "Only the reviewed default epoch preparation profile is accepted"
        )
    try:
        snapshot = registry._read_regular(  # noqa: SLF001
            resolved,
            name="epoch preparation profile",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
        if snapshot.sha256 != DEFAULT_CONFIG_SHA256:
            raise EpochPreparationIntegrityError(
                "Reviewed epoch preparation profile digest changed"
            )
        profile = registry._decode_json(  # noqa: SLF001
            snapshot.raw, name="epoch preparation profile"
        )
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
                "registry",
                "root_environment",
                "runtime",
                "protocol",
                "executable_capsule",
                "engineering_capabilities",
            },
            name="epoch preparation profile",
        )
        identity = (
            profile["schema_version"],
            profile["profile_id"],
            profile["profile_version"],
            profile["case"],
            profile["artifact_status"],
        )
        if identity != (
            "ootang_epoch_preparation_profile_v1",
            "ootang-epoch-preparation-v1",
            "1.0.0-engineering",
            "ootang",
            "immutable_epoch_executable_preparation_engineering_only_not_live_evidence",
        ):
            raise EpochPreparationIntegrityError(
                "Epoch preparation profile identity changed"
            )
        for key in (
            "formal_warning_output",
            "independent_label_used",
            "confirmatory_external_validation",
            "vajont_used",
            "default_pipeline_member",
        ):
            if profile[key] is not False:
                raise EpochPreparationIntegrityError(
                    f"Epoch preparation flag {key} must remain false"
                )
        if profile["registry"] != EXPECTED_REGISTRY:
            raise EpochPreparationIntegrityError("R1 registry binding changed")
        if profile["root_environment"] != EXPECTED_ROOT_ENVIRONMENT:
            raise EpochPreparationIntegrityError("Root environment binding changed")
        if profile["runtime"] != EXPECTED_RUNTIME:
            raise EpochPreparationIntegrityError("Preparation runtime mapping changed")
        if profile["protocol"] != EXPECTED_PROTOCOL:
            raise EpochPreparationIntegrityError("Preparation protocol mapping changed")
        if profile["executable_capsule"] != EXPECTED_EXECUTABLE_CAPSULE:
            raise EpochPreparationIntegrityError("Executable capsule contract changed")
        if profile["engineering_capabilities"] != EXPECTED_CAPABILITIES:
            raise EpochPreparationIntegrityError(
                "Preparation capability boundary changed"
            )
        for logical, expected in (
            (
                EXPECTED_REGISTRY["profile_path"],
                EXPECTED_REGISTRY["profile_sha256"],
            ),
            (
                EXPECTED_REGISTRY["implementation_path"],
                EXPECTED_REGISTRY["implementation_sha256"],
            ),
            (
                EXPECTED_ROOT_ENVIRONMENT["pyproject_path"],
                EXPECTED_ROOT_ENVIRONMENT["pyproject_sha256"],
            ),
            (
                EXPECTED_ROOT_ENVIRONMENT["uv_lock_path"],
                EXPECTED_ROOT_ENVIRONMENT["uv_lock_sha256"],
            ),
        ):
            captured = registry._read_regular(  # noqa: SLF001
                _profile_artifact(root, logical, name=f"bound artifact {logical}"),
                name=f"bound artifact {logical}",
            )
            if captured.sha256 != expected:
                raise EpochPreparationIntegrityError(
                    f"Bound artifact changed: {logical}"
                )
        for item in profile["executable_capsule"]["augmentation_files"]:
            logical = item["path"]
            expected = _require_hash(
                item["expected_sha256"], name="augmentation expected sha256"
            )
            captured = registry._read_regular(  # noqa: SLF001
                _profile_artifact(root, logical, name=f"bound augmentation {logical}"),
                name=f"bound augmentation {logical}",
            )
            if captured.sha256 != expected:
                raise EpochPreparationIntegrityError(
                    f"Bound augmentation changed: {logical}"
                )
        uv_executable = registry._read_regular(  # noqa: SLF001
            Path(profile["root_environment"]["required_uv_path"]),
            name="reviewed uv executable",
        )
        if uv_executable.sha256 != profile["root_environment"]["required_uv_sha256"]:
            raise EpochPreparationIntegrityError(
                "Reviewed uv executable binding changed"
            )
        implementation = registry._read_regular(  # noqa: SLF001
            _profile_artifact(
                root,
                IMPLEMENTATION_LOGICAL_PATH,
                name="epoch preparation implementation",
            ),
            name="epoch preparation implementation",
        )
    except (EpochPreparationIntegrityError, registry.EpochRegistryError) as exc:
        raise EpochPreparationConfigError(str(exc)) from exc
    profile["_profile_path"] = str(resolved)
    profile["_profile_sha256"] = snapshot.sha256
    profile["_project_root"] = str(root)
    profile["_implementation_sha256"] = implementation.sha256
    return profile


def preparation_paths(
    profile: Mapping[str, Any], *, runtime_root: Path | None = None
) -> PreparationPaths:
    root = (
        _profile_artifact(
            Path(str(profile["_project_root"])),
            profile["runtime"]["root"],
            name="runtime.root",
        )
        if runtime_root is None
        else registry._absolute_lexical(  # noqa: SLF001
            runtime_root.parent.resolve(strict=False) / runtime_root.name
        )
    )

    def child(key: str) -> Path:
        try:
            return registry._contained(  # noqa: SLF001
                root, profile["runtime"][key], name=f"runtime.{key}"
            )
        except registry.EpochRegistryError as exc:
            raise EpochPreparationConfigError(str(exc)) from exc

    return PreparationPaths(
        root=root,
        manager_lock=child("manager_lock"),
        status=child("status"),
        head=child("head"),
        events=child("events"),
        objects=child("objects"),
        capsules=child("capsules"),
        trees=child("trees"),
        smoke_receipts=child("smoke_receipts"),
    )


def _r1_paths(
    r1_profile: Mapping[str, Any], paths: PreparationPaths
) -> registry.RegistryPaths:
    result = registry.registry_paths(r1_profile, runtime_root=paths.root)
    if (
        result.root != paths.root
        or result.manager_lock != paths.manager_lock
        or result.objects != paths.objects
    ):
        raise EpochPreparationIntegrityError(
            "R1 and R2a do not share one serialized registry namespace"
        )
    return result


def _module_candidates(module_name: str) -> tuple[str, str]:
    parts = module_name.split(".")
    joined = "/".join(parts)
    return f"code/{joined}.py", f"code/{joined}/__init__.py"


def _module_package(module_name: str, logical_path: str) -> str:
    if logical_path.endswith("/__init__.py"):
        return module_name
    return module_name.rpartition(".")[0]


def _absolute_import_base(
    current_module: str,
    current_logical_path: str,
    node: ast.ImportFrom,
) -> str:
    if node.level == 0:
        return node.module or ""
    package = _module_package(current_module, current_logical_path)
    parts = package.split(".") if package else []
    remove = node.level - 1
    if remove > len(parts):
        raise EpochPreparationIntegrityError(
            f"Relative import escaped local package in {current_module}"
        )
    base_parts = parts[: len(parts) - remove]
    if node.module:
        base_parts.extend(node.module.split("."))
    return ".".join(base_parts)


def _capsule_snapshot_map(
    r1_paths: registry.RegistryPaths, r1_manifest: Mapping[str, Any]
) -> dict[str, tuple[bytes, str, int, str]]:
    result: dict[str, tuple[bytes, str, int, str]] = {}
    artifacts = r1_manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise EpochPreparationIntegrityError("R1 capsule artifacts changed")
    for index, value in enumerate(artifacts):
        artifact = _exact_object(
            value,
            {"logical_path", "sha256", "size_bytes", "object_path"},
            name=f"R1 capsule artifact[{index}]",
        )
        logical = str(
            _relative_path(artifact["logical_path"], name="R1 capsule logical path")
        )
        expected_sha = _require_hash(
            artifact["sha256"], name="R1 capsule artifact sha256"
        )
        size = artifact["size_bytes"]
        if type(size) is not int or size < 0:
            raise EpochPreparationIntegrityError("R1 capsule artifact size changed")
        object_path = registry._contained(  # noqa: SLF001
            r1_paths.root,
            artifact["object_path"],
            name="R1 capsule object path",
        )
        captured = registry._read_regular(  # noqa: SLF001
            object_path, name=f"R1 capsule object {logical}"
        )
        if captured.sha256 != expected_sha or captured.size_bytes != size:
            raise EpochPreparationIntegrityError("R1 capsule object bytes changed")
        if logical in result:
            raise EpochPreparationIntegrityError("R1 capsule logical path repeated")
        result[logical] = (captured.raw, expected_sha, size, "r1_capsule")
    return result


def _resolve_local_closure(
    profile: Mapping[str, Any],
    paths: PreparationPaths,
    r1_paths: registry.RegistryPaths,
    r1_manifest: Mapping[str, Any],
    *,
    publish_objects: bool = True,
) -> tuple[list[dict[str, object]], list[str]]:
    """Resolve local imports from immutable R1 bytes plus captured missing package files."""

    project_root = Path(str(profile["_project_root"]))
    snapshots = _capsule_snapshot_map(r1_paths, r1_manifest)
    local_packages = set(profile["executable_capsule"]["local_packages"])
    augmentation = {
        str(_relative_path(item["path"], name="augmentation file path")): _require_hash(
            item["expected_sha256"], name="augmentation file sha256"
        )
        for item in profile["executable_capsule"]["augmentation_files"]
    }
    if len(augmentation) != len(profile["executable_capsule"]["augmentation_files"]):
        raise EpochPreparationIntegrityError("Augmentation file binding repeated")

    def read_logical(logical: str) -> tuple[bytes, str, int, str]:
        existing = snapshots.get(logical)
        if existing is not None:
            return existing
        if not logical.startswith("code/") or not logical.endswith(".py"):
            raise EpochPreparationIntegrityError(
                f"Missing executable artifact is outside local Python closure: {logical}"
            )
        expected = augmentation.get(logical)
        if expected is None:
            raise EpochPreparationIntegrityError(
                f"Local closure needs an unreviewed augmentation: {logical}"
            )
        captured = registry._read_regular(  # noqa: SLF001
            _profile_artifact(
                project_root, logical, name=f"local closure artifact {logical}"
            ),
            name=f"local closure artifact {logical}",
        )
        if captured.sha256 != expected:
            raise EpochPreparationIntegrityError(
                f"Reviewed local closure augmentation changed: {logical}"
            )
        if publish_objects:
            published = registry._publish_once(  # noqa: SLF001
                paths.objects / captured.sha256,
                captured.raw,
                root=paths.root,
                name=f"local closure object {logical}",
            )
            value = (
                published.raw,
                published.sha256,
                published.size_bytes,
                "r2_closure_capture",
            )
        else:
            value = (
                captured.raw,
                captured.sha256,
                captured.size_bytes,
                "r2_closure_capture",
            )
        snapshots[logical] = value
        return value

    def resolve_module(module_name: str) -> str | None:
        if not MODULE_RE.fullmatch(module_name):
            raise EpochPreparationIntegrityError(
                f"Local module name is not canonical: {module_name}"
            )
        if module_name.split(".", 1)[0] not in local_packages:
            return None
        for logical in _module_candidates(module_name):
            if logical in snapshots:
                return logical
            path = _profile_artifact(
                project_root, logical, name=f"local module {module_name}"
            )
            if path.exists() and not path.is_symlink():
                if not stat.S_ISREG(os.lstat(path).st_mode):
                    raise EpochPreparationIntegrityError(
                        f"Local module is not a regular file: {logical}"
                    )
                read_logical(logical)
                return logical
        return None

    queue = list(profile["executable_capsule"]["closure_seed_modules"])
    visited: dict[str, str] = {}
    while queue:
        module_name = queue.pop(0)
        if module_name in visited:
            continue
        logical = resolve_module(module_name)
        if logical is None:
            raise EpochPreparationIntegrityError(
                f"Required local module is absent: {module_name}"
            )
        visited[module_name] = logical
        raw, _, _, _ = read_logical(logical)
        try:
            source = raw.decode("utf-8")
            tree = ast.parse(source, filename=logical)
        except (UnicodeDecodeError, SyntaxError) as exc:
            raise EpochPreparationIntegrityError(
                f"Local module cannot be parsed: {logical}"
            ) from exc
        if profile["executable_capsule"]["reject_dynamic_local_imports"]:
            dynamic_names = {"__import__", "eval", "exec"}
            dynamic_attributes = {
                "import_module",
                "run_module",
                "run_path",
                "spec_from_file_location",
                "module_from_spec",
                "find_loader",
                "iter_modules",
                "walk_packages",
                "SourceFileLoader",
                "SourcelessFileLoader",
                "ExtensionFileLoader",
            }
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in {
                    "builtins",
                    "importlib",
                    "importlib.util",
                    "importlib.machinery",
                    "pkgutil",
                    "runpy",
                }:
                    for alias in node.names:
                        if (
                            alias.name in dynamic_attributes
                            or alias.name in dynamic_names
                        ):
                            dynamic_names.add(alias.asname or alias.name)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and (
                    isinstance(node.func, ast.Name)
                    and node.func.id in dynamic_names
                    or isinstance(node.func, ast.Attribute)
                    and node.func.attr in dynamic_attributes
                    or isinstance(node.func, ast.Name)
                    and node.func.id == "getattr"
                    and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant)
                    and node.args[1].value in dynamic_attributes
                ):
                    raise EpochPreparationIntegrityError(
                        f"Dynamic import is forbidden in executable closure: {logical}"
                    )
        discovered: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                discovered.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if any(alias.name == "*" for alias in node.names):
                    base = _absolute_import_base(module_name, logical, node)
                    if base.split(".", 1)[0] in local_packages:
                        raise EpochPreparationIntegrityError(
                            f"Wildcard local import is forbidden: {logical}"
                        )
                    continue
                base = _absolute_import_base(module_name, logical, node)
                if base:
                    discovered.add(base)
                for alias in node.names:
                    candidate = f"{base}.{alias.name}" if base else alias.name
                    if (
                        candidate.split(".", 1)[0] in local_packages
                        and MODULE_RE.fullmatch(candidate)
                        and resolve_module(candidate) is not None
                    ):
                        discovered.add(candidate)
        for imported in sorted(discovered):
            if imported.split(".", 1)[0] in local_packages:
                if resolve_module(imported) is None:
                    raise EpochPreparationIntegrityError(
                        f"Local import cannot be resolved: {imported}"
                    )
                if imported not in visited and imported not in queue:
                    queue.append(imported)
        package = _module_package(module_name, logical)
        while package:
            package_logical = resolve_module(package)
            if (
                package_logical is not None
                and package not in visited
                and package not in queue
            ):
                queue.append(package)
            package = package.rpartition(".")[0]

    if len(visited) != profile["executable_capsule"]["expected_local_module_count"]:
        raise EpochPreparationIntegrityError(
            "Resolved local module count differs from the reviewed activation closure"
        )
    if set(augmentation) != {
        logical
        for logical, (_, _, _, source) in snapshots.items()
        if source == "r2_closure_capture"
    }:
        raise EpochPreparationIntegrityError(
            "Reviewed closure augmentation set changed"
        )

    required = set(profile["executable_capsule"]["required_logical_paths"])
    if not required.issubset(snapshots):
        missing = sorted(required.difference(snapshots))
        raise EpochPreparationIntegrityError(
            f"R1 capsule lacks required executable artifacts: {missing}"
        )

    if not profile["executable_capsule"]["materialize_all_r1_capsule_artifacts"]:
        selected = {
            logical
            for logical, (_, _, _, source) in snapshots.items()
            if source == "r1_capsule" and not logical.endswith(".py")
        }
        selected.update(visited.values())
        selected.update(required)
        snapshots = {key: snapshots[key] for key in sorted(selected)}

    records: list[dict[str, object]] = []
    executable_logical = set(visited.values())
    trusted_core = "code/monitoring/ootang_trusted_time_shadow_core.py"
    for logical, (raw, sha256, size, source) in sorted(snapshots.items()):
        object_path = paths.objects / sha256
        if publish_objects:
            published = registry._publish_once(  # noqa: SLF001
                object_path,
                raw,
                root=paths.root,
                name=f"executable object {logical}",
            )
            if published.size_bytes != size:
                raise EpochPreparationIntegrityError(
                    f"Executable object size changed: {logical}"
                )
        records.append(
            {
                "logical_path": logical,
                "sha256": sha256,
                "size_bytes": size,
                "object_path": str(object_path.relative_to(paths.root)),
                "source": source,
                "role": (
                    "trusted_time_isolated_entrypoint"
                    if logical == trusted_core
                    else "activation_local_module"
                    if logical in executable_logical
                    else "bound_resource"
                ),
            }
        )
    return records, sorted(visited)


def _tree_payload(artifacts: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "logical_path": artifact["logical_path"],
            "sha256": artifact["sha256"],
            "size_bytes": artifact["size_bytes"],
        }
        for artifact in artifacts
    ]


def _build_executable_capsule(
    profile: Mapping[str, Any],
    paths: PreparationPaths,
    r1_profile: Mapping[str, Any],
    r1_paths: registry.RegistryPaths,
    candidate_event: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> tuple[dict[str, object], registry.ArtifactSnapshot]:
    r1_manifest = registry._verify_capsule(  # noqa: SLF001
        r1_profile, r1_paths, receipt["capsule"]
    )
    artifacts, closure_modules = _resolve_local_closure(
        profile, paths, r1_paths, r1_manifest
    )
    implementation = registry._read_regular(  # noqa: SLF001
        _profile_artifact(
            Path(str(profile["_project_root"])),
            IMPLEMENTATION_LOGICAL_PATH,
            name="epoch preparation implementation",
        ),
        name="epoch preparation implementation",
    )
    if implementation.sha256 != profile["_implementation_sha256"]:
        raise EpochPreparationIntegrityError(
            "Running preparation implementation changed during capsule capture"
        )
    implementation_object = registry._publish_once(  # noqa: SLF001
        paths.objects / implementation.sha256,
        implementation.raw,
        root=paths.root,
        name="epoch preparation implementation object",
    )
    profile_snapshot = registry._read_regular(  # noqa: SLF001
        Path(str(profile["_profile_path"])),
        name="epoch preparation profile",
        maximum_bytes=MAX_CONTROL_BYTES,
    )
    if profile_snapshot.sha256 != profile["_profile_sha256"]:
        raise EpochPreparationIntegrityError(
            "Preparation profile changed during capsule capture"
        )
    profile_object = registry._publish_once(  # noqa: SLF001
        paths.objects / profile_snapshot.sha256,
        profile_snapshot.raw,
        root=paths.root,
        name="epoch preparation profile object",
    )
    tree_payload = _tree_payload(artifacts)
    build = receipt.get("candidate_build")
    if not isinstance(build, dict):
        raise EpochPreparationIntegrityError("Candidate build changed")
    manifest: dict[str, object] = {
        "schema_version": profile["protocol"]["capsule_schema_version"],
        "case": "ootang",
        "capsule_kind": "same_origin_executable_preflight",
        "preparation_profile_sha256": profile["_profile_sha256"],
        "preparation_profile": _capsule_reference(paths, profile_object),
        "preparation_implementation_sha256": profile["_implementation_sha256"],
        "preparation_implementation": _capsule_reference(paths, implementation_object),
        "registry_profile_sha256": r1_profile["_profile_sha256"],
        "registry_event_sequence_id": candidate_event["sequence_id"],
        "registry_event_entry_sha256": candidate_event["entry_sha256"],
        "candidate_id": receipt["candidate_id"],
        "slot_id": receipt["slot_id"],
        "slot_live_root": receipt["slot_live_root"],
        "candidate_live_epoch_id": build["live_epoch_id"],
        "r1_capsule_tree_sha256": r1_manifest["tree_sha256"],
        "root_modules": list(profile["executable_capsule"]["root_modules"]),
        "closure_modules": closure_modules,
        "artifact_count": len(artifacts),
        "tree_sha256": _sha256(registry._canonical_bytes(tree_payload)),  # noqa: SLF001
        "artifacts": artifacts,
        "materialized_executable_tree": True,
        "transitive_local_import_closure_materialized": True,
        "isolated_import_compile_smoke_required": True,
        "isolated_candidate_prerequisite_reload_required": True,
        "five_seed_numerical_replay_required": True,
        "trusted_time_isolated_domain_smoke_required": True,
        "canonical_project_root_required": True,
        "canonical_slot_root_required": True,
        "relocatable": False,
        "portable_offline_runtime": False,
        "old_epoch_drain_implemented": False,
        "active_epoch_switch_implemented": False,
        "automatic_epoch_rotation_implemented": False,
        "e2_live_evidence_eligible": False,
        "formal_warning_output": False,
    }
    raw = registry._canonical_bytes(manifest)  # noqa: SLF001
    snapshot = registry._publish_once(  # noqa: SLF001
        paths.capsules / f"{_sha256(raw)}.json",
        raw,
        root=paths.root,
        name="executable capsule manifest",
    )
    return manifest, snapshot


def _capsule_reference(
    paths: PreparationPaths, snapshot: registry.ArtifactSnapshot
) -> dict[str, object]:
    return {
        "path": str(snapshot.path.relative_to(paths.root)),
        "sha256": snapshot.sha256,
        "size_bytes": snapshot.size_bytes,
    }


def _verify_executable_capsule(
    profile: Mapping[str, Any],
    paths: PreparationPaths,
    reference: object,
    *,
    r1_paths: registry.RegistryPaths,
    r1_manifest: Mapping[str, Any],
    expected_candidate: Mapping[str, Any],
    expected_registry_event: Mapping[str, Any],
    require_current_implementation: bool = False,
) -> dict[str, Any]:
    record = _exact_object(reference, REFERENCE_KEYS, name="executable capsule ref")
    expected_sha = _require_hash(record["sha256"], name="executable capsule sha256")
    size = record["size_bytes"]
    if type(size) is not int or size < 0:
        raise EpochPreparationIntegrityError("Executable capsule size changed")
    path = registry._contained(  # noqa: SLF001
        paths.root, record["path"], name="executable capsule path"
    )
    if path.parent != paths.capsules or path.name != f"{expected_sha}.json":
        raise EpochPreparationIntegrityError("Executable capsule namespace changed")
    snapshot = registry._read_regular(  # noqa: SLF001
        path, name="executable capsule", maximum_bytes=MAX_CONTROL_BYTES
    )
    if snapshot.sha256 != expected_sha or snapshot.size_bytes != size:
        raise EpochPreparationIntegrityError("Executable capsule bytes changed")
    manifest = registry._decode_json(  # noqa: SLF001
        snapshot.raw, name="executable capsule"
    )
    if snapshot.raw != registry._canonical_bytes(manifest):  # noqa: SLF001
        raise EpochPreparationIntegrityError("Executable capsule is not canonical")
    keys = {
        "schema_version",
        "case",
        "capsule_kind",
        "preparation_profile_sha256",
        "preparation_profile",
        "preparation_implementation_sha256",
        "preparation_implementation",
        "registry_profile_sha256",
        "registry_event_sequence_id",
        "registry_event_entry_sha256",
        "candidate_id",
        "slot_id",
        "slot_live_root",
        "candidate_live_epoch_id",
        "r1_capsule_tree_sha256",
        "root_modules",
        "closure_modules",
        "artifact_count",
        "tree_sha256",
        "artifacts",
        "materialized_executable_tree",
        "transitive_local_import_closure_materialized",
        "isolated_import_compile_smoke_required",
        "isolated_candidate_prerequisite_reload_required",
        "five_seed_numerical_replay_required",
        "trusted_time_isolated_domain_smoke_required",
        "canonical_project_root_required",
        "canonical_slot_root_required",
        "relocatable",
        "portable_offline_runtime",
        "old_epoch_drain_implemented",
        "active_epoch_switch_implemented",
        "automatic_epoch_rotation_implemented",
        "e2_live_evidence_eligible",
        "formal_warning_output",
    }
    _exact_object(manifest, keys, name="executable capsule")
    if (
        manifest["schema_version"] != profile["protocol"]["capsule_schema_version"]
        or manifest["case"] != "ootang"
        or manifest["capsule_kind"] != "same_origin_executable_preflight"
        or manifest["preparation_profile_sha256"] != profile["_profile_sha256"]
        or manifest["registry_profile_sha256"] != REGISTRY_PROFILE_SHA256
        or manifest["root_modules"] != profile["executable_capsule"]["root_modules"]
        or manifest["transitive_local_import_closure_materialized"] is not True
        or manifest["materialized_executable_tree"] is not True
        or manifest["isolated_import_compile_smoke_required"] is not True
        or manifest["isolated_candidate_prerequisite_reload_required"] is not True
        or manifest["five_seed_numerical_replay_required"] is not True
        or manifest["trusted_time_isolated_domain_smoke_required"] is not True
        or manifest["canonical_project_root_required"] is not True
        or manifest["canonical_slot_root_required"] is not True
        or manifest["relocatable"] is not False
        or manifest["portable_offline_runtime"] is not False
        or manifest["old_epoch_drain_implemented"] is not False
        or manifest["active_epoch_switch_implemented"] is not False
        or manifest["automatic_epoch_rotation_implemented"] is not False
        or manifest["e2_live_evidence_eligible"] is not False
        or manifest["formal_warning_output"] is not False
    ):
        raise EpochPreparationIntegrityError("Executable capsule boundary changed")
    implementation_sha256 = _require_hash(
        manifest["preparation_implementation_sha256"],
        name="executable capsule preparation implementation",
    )
    profile_reference = _exact_object(
        manifest["preparation_profile"],
        REFERENCE_KEYS,
        name="preparation profile ref",
    )
    profile_path = registry._contained(  # noqa: SLF001
        paths.root,
        profile_reference["path"],
        name="preparation profile object path",
    )
    if (
        profile_path.parent != paths.objects
        or profile_path.name != profile["_profile_sha256"]
        or profile_reference["sha256"] != profile["_profile_sha256"]
        or type(profile_reference["size_bytes"]) is not int
        or profile_reference["size_bytes"] < 0
    ):
        raise EpochPreparationIntegrityError("Preparation profile reference changed")
    profile_object = registry._read_regular(  # noqa: SLF001
        profile_path,
        name="preparation profile object",
        maximum_bytes=MAX_CONTROL_BYTES,
    )
    if (
        profile_object.sha256 != profile["_profile_sha256"]
        or profile_object.size_bytes != profile_reference["size_bytes"]
    ):
        raise EpochPreparationIntegrityError("Preparation profile object bytes changed")
    implementation_reference = _exact_object(
        manifest["preparation_implementation"],
        REFERENCE_KEYS,
        name="preparation implementation ref",
    )
    implementation_path = registry._contained(  # noqa: SLF001
        paths.root,
        implementation_reference["path"],
        name="preparation implementation object path",
    )
    if (
        implementation_path.parent != paths.objects
        or implementation_path.name != implementation_sha256
        or implementation_reference["sha256"] != implementation_sha256
        or type(implementation_reference["size_bytes"]) is not int
        or implementation_reference["size_bytes"] < 0
    ):
        raise EpochPreparationIntegrityError(
            "Preparation implementation reference changed"
        )
    implementation_object = registry._read_regular(  # noqa: SLF001
        implementation_path,
        name="preparation implementation object",
        maximum_bytes=MAX_CONTROL_BYTES,
    )
    if (
        implementation_object.sha256 != implementation_sha256
        or implementation_object.size_bytes != implementation_reference["size_bytes"]
    ):
        raise EpochPreparationIntegrityError(
            "Preparation implementation object bytes changed"
        )
    if (
        require_current_implementation
        and implementation_sha256 != profile["_implementation_sha256"]
    ):
        raise EpochPreparationIntegrityError(
            "Executable capsule was not built by the running preparation implementation"
        )
    if require_current_implementation:
        current_profile = registry._read_regular(  # noqa: SLF001
            Path(str(profile["_profile_path"])),
            name="current preparation profile",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
        if current_profile.raw != profile_object.raw:
            raise EpochPreparationIntegrityError(
                "Current preparation profile differs from captured bytes"
            )
        current_implementation = registry._read_regular(  # noqa: SLF001
            _profile_artifact(
                Path(str(profile["_project_root"])),
                IMPLEMENTATION_LOGICAL_PATH,
                name="current preparation implementation",
            ),
            name="current preparation implementation",
            maximum_bytes=MAX_CONTROL_BYTES,
        )
        if current_implementation.raw != implementation_object.raw:
            raise EpochPreparationIntegrityError(
                "Current preparation implementation differs from captured bytes"
            )
    for key in (
        "registry_event_entry_sha256",
        "candidate_id",
        "slot_id",
        "candidate_live_epoch_id",
        "r1_capsule_tree_sha256",
        "tree_sha256",
    ):
        _require_hash(manifest[key], name=f"executable capsule {key}")
    if (
        type(manifest["registry_event_sequence_id"]) is not int
        or manifest["registry_event_sequence_id"] < 1
    ):
        raise EpochPreparationIntegrityError("Registry event sequence changed")
    if (
        manifest["registry_event_sequence_id"] != expected_registry_event["sequence_id"]
        or manifest["registry_event_entry_sha256"]
        != expected_registry_event["entry_sha256"]
    ):
        raise EpochPreparationIntegrityError(
            "Executable capsule registry event binding changed"
        )
    r1_tree_sha256 = _require_hash(
        r1_manifest.get("tree_sha256"), name="verified R1 capsule tree sha256"
    )
    if manifest["r1_capsule_tree_sha256"] != r1_tree_sha256:
        raise EpochPreparationIntegrityError(
            "Executable capsule R1 tree binding changed"
        )
    closure = manifest["closure_modules"]
    if (
        not isinstance(closure, list)
        or closure != sorted(set(closure))
        or len(closure) != profile["executable_capsule"]["expected_local_module_count"]
        or not set(profile["executable_capsule"]["root_modules"]).issubset(closure)
        or any(
            not isinstance(module, str) or not MODULE_RE.fullmatch(module)
            for module in closure
        )
    ):
        raise EpochPreparationIntegrityError("Executable closure modules changed")
    artifacts = manifest["artifacts"]
    if (
        not isinstance(artifacts, list)
        or type(manifest["artifact_count"]) is not int
        or manifest["artifact_count"] != len(artifacts)
        or not artifacts
    ):
        raise EpochPreparationIntegrityError("Executable artifact count changed")
    expected_artifacts, expected_closure = _resolve_local_closure(
        profile,
        paths,
        r1_paths,
        r1_manifest,
        publish_objects=False,
    )
    if closure != expected_closure or artifacts != expected_artifacts:
        raise EpochPreparationIntegrityError(
            "Executable capsule differs from the exact reviewed local closure"
        )
    tree: list[dict[str, object]] = []
    logical_paths: list[str] = []
    for index, value in enumerate(artifacts):
        artifact = _exact_object(
            value, CAPSULE_ARTIFACT_KEYS, name=f"executable artifact[{index}]"
        )
        logical = str(
            _relative_path(artifact["logical_path"], name="executable logical path")
        )
        sha256 = _require_hash(artifact["sha256"], name="executable artifact sha")
        artifact_size = artifact["size_bytes"]
        if type(artifact_size) is not int or artifact_size < 0:
            raise EpochPreparationIntegrityError("Executable artifact size changed")
        if artifact["source"] not in {"r1_capsule", "r2_closure_capture"}:
            raise EpochPreparationIntegrityError("Executable artifact source changed")
        expected_role = (
            "trusted_time_isolated_entrypoint"
            if logical == "code/monitoring/ootang_trusted_time_shadow_core.py"
            else "activation_local_module"
            if logical.endswith(".py")
            else "bound_resource"
        )
        if artifact["role"] != expected_role:
            raise EpochPreparationIntegrityError("Executable artifact role changed")
        object_path = registry._contained(  # noqa: SLF001
            paths.root, artifact["object_path"], name="executable object path"
        )
        if object_path.parent != paths.objects or object_path.name != sha256:
            raise EpochPreparationIntegrityError("Executable object namespace changed")
        captured = registry._read_regular(  # noqa: SLF001
            object_path, name=f"executable object {logical}"
        )
        if captured.sha256 != sha256 or captured.size_bytes != artifact_size:
            raise EpochPreparationIntegrityError("Executable object bytes changed")
        logical_paths.append(logical)
        tree.append(
            {
                "logical_path": logical,
                "sha256": sha256,
                "size_bytes": artifact_size,
            }
        )
    if logical_paths != sorted(set(logical_paths)):
        raise EpochPreparationIntegrityError("Executable artifact order changed")
    closure_logical: set[str] = set()
    for module_name in closure:
        matches = set(_module_candidates(module_name)).intersection(logical_paths)
        if len(matches) != 1:
            raise EpochPreparationIntegrityError(
                "Executable closure module-to-file mapping changed"
            )
        closure_logical.update(matches)
    if closure_logical != {
        logical for logical in logical_paths if logical.endswith(".py")
    }:
        raise EpochPreparationIntegrityError(
            "Executable Python files differ from the resolved closure"
        )
    if _sha256(registry._canonical_bytes(tree)) != manifest["tree_sha256"]:  # noqa: SLF001
        raise EpochPreparationIntegrityError("Executable tree digest changed")
    if not set(profile["executable_capsule"]["required_logical_paths"]).issubset(
        logical_paths
    ):
        raise EpochPreparationIntegrityError("Required executable artifacts are absent")
    build = expected_candidate.get("candidate_build")
    if not isinstance(build, dict):
        raise EpochPreparationIntegrityError("Expected candidate build changed")
    expected = {
        "candidate_id": expected_candidate["candidate_id"],
        "slot_id": expected_candidate["slot_id"],
        "slot_live_root": expected_candidate["slot_live_root"],
        "candidate_live_epoch_id": build["live_epoch_id"],
    }
    if any(manifest[key] != value for key, value in expected.items()):
        raise EpochPreparationIntegrityError(
            "Executable capsule candidate binding changed"
        )
    return manifest


def _tree_root(paths: PreparationPaths, manifest: Mapping[str, Any]) -> Path:
    return registry._contained(  # noqa: SLF001
        paths.trees, manifest["tree_sha256"], name="executable tree root"
    )


def _verify_materialized_tree(
    paths: PreparationPaths, manifest: Mapping[str, Any], tree_root: Path
) -> None:
    expected: dict[str, tuple[str, int]] = {}
    expected_directories: set[str] = set()
    for artifact in manifest["artifacts"]:
        logical_path = str(artifact["logical_path"])
        expected[logical_path] = (
            str(artifact["sha256"]),
            int(artifact["size_bytes"]),
        )
        parent = PurePosixPath(logical_path).parent
        while str(parent) != ".":
            expected_directories.add(str(parent))
            parent = parent.parent
    if not tree_root.exists() or tree_root.is_symlink():
        raise EpochPreparationIntegrityError(
            "Executable tree root is absent or aliased"
        )
    if not stat.S_ISDIR(os.lstat(tree_root).st_mode):
        raise EpochPreparationIntegrityError("Executable tree root is not a directory")
    actual: set[str] = set()
    actual_directories: set[str] = set()
    for directory, directory_names, file_names in os.walk(
        tree_root, topdown=True, followlinks=False
    ):
        base = Path(directory)
        for name in directory_names:
            child = base / name
            logical_directory = str(child.relative_to(tree_root))
            if child.is_symlink() or not stat.S_ISDIR(os.lstat(child).st_mode):
                raise EpochPreparationIntegrityError(
                    "Executable tree contains an aliased/non-directory parent"
                )
            if logical_directory not in expected_directories:
                raise EpochPreparationIntegrityError(
                    "Executable tree contains an undeclared directory: "
                    f"{logical_directory}"
                )
            actual_directories.add(logical_directory)
        for name in file_names:
            child = base / name
            logical = str(child.relative_to(tree_root))
            if logical not in expected:
                raise EpochPreparationIntegrityError(
                    f"Executable tree contains an undeclared file: {logical}"
                )
            captured = registry._read_regular(  # noqa: SLF001
                child, name=f"materialized executable {logical}"
            )
            if (captured.sha256, captured.size_bytes) != expected[logical]:
                raise EpochPreparationIntegrityError(
                    f"Materialized executable changed: {logical}"
                )
            actual.add(logical)
    if actual != set(expected):
        raise EpochPreparationIntegrityError("Executable tree is incomplete")
    if actual_directories != expected_directories:
        raise EpochPreparationIntegrityError(
            "Executable tree directory closure is incomplete"
        )


def _materialize_tree(paths: PreparationPaths, manifest: Mapping[str, Any]) -> Path:
    tree_root = _tree_root(paths, manifest)
    registry._mkdir(tree_root, root=paths.root)  # noqa: SLF001
    for artifact in manifest["artifacts"]:
        logical = str(artifact["logical_path"])
        object_path = registry._contained(  # noqa: SLF001
            paths.root, artifact["object_path"], name=f"object for {logical}"
        )
        raw = registry._read_regular(  # noqa: SLF001
            object_path, name=f"object for {logical}"
        )
        if raw.sha256 != artifact["sha256"] or raw.size_bytes != artifact["size_bytes"]:
            raise EpochPreparationIntegrityError(
                f"Executable object changed before materialization: {logical}"
            )
        target = registry._contained(  # noqa: SLF001
            tree_root, logical, name=f"materialized path {logical}"
        )
        registry._publish_once(  # noqa: SLF001
            target,
            raw.raw,
            root=paths.root,
            name=f"materialized executable {logical}",
        )
    _verify_materialized_tree(paths, manifest, tree_root)
    return tree_root


_ISOLATED_SMOKE_SCRIPT = r"""
import hashlib
import importlib
import importlib.metadata
import json
import math
import platform
from pathlib import Path
import sys
import sysconfig

manifest_path = Path(sys.argv[1]).resolve()
tree_root = Path(sys.argv[2]).resolve()
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
if sys.flags.isolated != 1 or sys.flags.dont_write_bytecode != 1:
    raise RuntimeError("python isolation flags changed")
if sys.version.split()[0] != sys.argv[3]:
    raise RuntimeError("python version changed")
compiled = 0
for artifact in manifest["artifacts"]:
    path = (tree_root / artifact["logical_path"]).resolve()
    path.relative_to(tree_root)
    raw = path.read_bytes()
    if len(raw) != artifact["size_bytes"] or hashlib.sha256(raw).hexdigest() != artifact["sha256"]:
        raise RuntimeError("materialized artifact changed")
    if path.suffix == ".py":
        compile(raw, str(path), "exec", dont_inherit=True)
        compiled += 1
sys.path.insert(0, str(tree_root / "code"))
imported = []
for module_name in manifest["root_modules"]:
    module = importlib.import_module(module_name)
    module_path = Path(module.__file__).resolve()
    module_path.relative_to(tree_root)
    imported.append(module_name)
live = importlib.import_module("monitoring.ootang_prequential_live")
live_profile = live.load_config(tree_root / "config" / "ootang_prequential_live.v1.json")
live_paths = live.runtime_paths(live_profile, runtime_root=Path(manifest["slot_live_root"]))
prerequisites = live.load_prerequisites(live_profile, live_paths)
if prerequisites is None:
    raise RuntimeError("candidate prerequisites disappeared")
reloaded_epoch_id = live._epoch_id(live_profile, prerequisites)
if reloaded_epoch_id != manifest["candidate_live_epoch_id"]:
    raise RuntimeError("candidate live epoch identity changed")
source_module = importlib.import_module("monitoring.ootang_live_source")
bundle_module = importlib.import_module("convlstm.ootang_production_bundle")
deploy_path = tree_root / "config" / "ootang_prequential_deploy.v1.json"
source_profile = source_module.load_deploy_profile(deploy_path, project_root=tree_root)
source = source_module.load_activation_source(
    source_profile,
    runtime_root=Path(manifest["slot_live_root"]),
    project_root=tree_root,
)
bundle_profile = bundle_module.load_deploy_profile(deploy_path, project_root=tree_root)
loaded_bundle = bundle_module.load_deploy_bundle(
    bundle_profile,
    runtime_root=Path(manifest["slot_live_root"]),
    source=source,
    now=None,
)
predictions = bundle_module.predict_p50(
    loaded_bundle, source.frame, profile=bundle_profile
)
training = json.loads(loaded_bundle.training_manifest.path.read_text(encoding="utf-8"))
replay_by_seed = {row["seed"]: row for row in training["reload_replay"]}
model_stations = bundle_profile["source_feed"]["station_order_model"]
tolerance = float(sys.argv[4])
if not math.isfinite(tolerance) or tolerance < 0:
    raise RuntimeError("five-seed tolerance changed")
maximum_difference = 0.0
if list(predictions) != [0, 1, 2, 3, 4]:
    raise RuntimeError("five-seed replay order changed")
if len(training["reload_replay"]) != 5 or set(replay_by_seed) != {0, 1, 2, 3, 4}:
    raise RuntimeError("training replay seed set changed")
for seed, observed in predictions.items():
    expected_values = replay_by_seed[seed]["reloaded_p50_mm"]
    if not isinstance(expected_values, list) or len(expected_values) != len(model_stations):
        raise RuntimeError("training replay station values changed")
    if list(observed) != list(model_stations):
        raise RuntimeError("prediction station order changed")
    for station, expected_value in zip(model_stations, expected_values):
        value = float(observed[station])
        expected_value = float(expected_value)
        if not math.isfinite(value) or not math.isfinite(expected_value):
            raise RuntimeError("five-seed replay contains a non-finite value")
        difference = abs(value - expected_value)
        if not math.isfinite(difference):
            raise RuntimeError("five-seed replay difference is non-finite")
        maximum_difference = max(maximum_difference, difference)
        if difference > tolerance:
            raise RuntimeError("five-seed numerical replay changed")
inventory = sorted(
    {
        f"{(distribution.metadata.get('Name') or '').lower().replace('_', '-')}=={distribution.version}"
        for distribution in importlib.metadata.distributions()
        if distribution.metadata.get("Name")
    }
)
inventory_raw = ("\n".join(inventory) + "\n").encode("utf-8")
python_executable = Path(sys.executable).resolve()
result = {
    "schema_version": "ootang_epoch_root_domain_smoke_v1",
    "python_version": sys.version.split()[0],
    "isolated_mode": True,
    "dont_write_bytecode": True,
    "tree_sha256": manifest["tree_sha256"],
    "root_modules_imported": imported,
    "compiled_python_file_count": compiled,
    "candidate_live_epoch_id": manifest["candidate_live_epoch_id"],
    "candidate_prerequisites_reloaded": True,
    "five_seed_numerical_replay_passed": True,
    "five_seed_max_abs_difference_mm": maximum_difference,
    "distribution_count": len(inventory),
    "distribution_inventory_sha256": hashlib.sha256(inventory_raw).hexdigest(),
    "python_executable_sha256": hashlib.sha256(python_executable.read_bytes()).hexdigest(),
    "python_soabi": sysconfig.get_config_var("SOABI"),
    "platform": platform.platform(),
}
print(json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
"""


_TRUSTED_TIME_SMOKE_SCRIPT = r"""
import hashlib
import importlib
import importlib.metadata
import json
import platform
from pathlib import Path
import sys
import sysconfig

tree_root = Path(sys.argv[1]).resolve()
if sys.flags.isolated != 1 or sys.flags.dont_write_bytecode != 1:
    raise RuntimeError("trusted-time python isolation flags changed")
if sys.version.split()[0] != sys.argv[2]:
    raise RuntimeError("trusted-time python version changed")
sys.path.insert(0, str(tree_root / "code"))
module = importlib.import_module("monitoring.ootang_trusted_time_shadow_core")
Path(module.__file__).resolve().relative_to(tree_root)
inventory = sorted(
    {
        f"{(distribution.metadata.get('Name') or '').lower().replace('_', '-')}=={distribution.version}"
        for distribution in importlib.metadata.distributions()
        if distribution.metadata.get("Name")
    }
)
inventory_raw = ("\n".join(inventory) + "\n").encode("utf-8")
python_executable = Path(sys.executable).resolve()
result = {
    "schema_version": "ootang_epoch_trusted_time_domain_smoke_v1",
    "python_version": sys.version.split()[0],
    "isolated_mode": True,
    "dont_write_bytecode": True,
    "trusted_time_core_imported": True,
    "distribution_count": len(inventory),
    "distribution_inventory_sha256": hashlib.sha256(inventory_raw).hexdigest(),
    "python_executable_sha256": hashlib.sha256(python_executable.read_bytes()).hexdigest(),
    "python_soabi": sysconfig.get_config_var("SOABI"),
    "platform": platform.platform(),
}
print(json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
"""


def _validate_smoke_result(
    profile: Mapping[str, Any], manifest: Mapping[str, Any], result: object
) -> dict[str, object]:
    payload = _exact_object(
        result,
        {
            "schema_version",
            "python_version",
            "uv_version",
            "uv_executable_sha256",
            "isolated_mode",
            "dont_write_bytecode",
            "tree_sha256",
            "root_modules_imported",
            "compiled_python_file_count",
            "candidate_live_epoch_id",
            "candidate_prerequisites_reloaded",
            "five_seed_numerical_replay_passed",
            "five_seed_max_abs_difference_mm",
            "root_domain",
            "trusted_time_domain",
        },
        name="isolated smoke result",
    )
    if (
        payload["schema_version"] != "ootang_epoch_isolated_smoke_result_v2"
        or payload["python_version"]
        != profile["root_environment"]["required_python_version"]
        or payload["uv_version"] != profile["root_environment"]["required_uv_version"]
        or _require_hash(payload["uv_executable_sha256"], name="uv executable sha256")
        != payload["uv_executable_sha256"]
        or payload["uv_executable_sha256"]
        != profile["root_environment"]["required_uv_sha256"]
        or payload["isolated_mode"] is not True
        or payload["dont_write_bytecode"] is not True
        or payload["tree_sha256"] != manifest["tree_sha256"]
        or payload["root_modules_imported"] != manifest["root_modules"]
        or type(payload["compiled_python_file_count"]) is not int
        or payload["compiled_python_file_count"]
        != profile["executable_capsule"]["expected_local_module_count"]
        or payload["candidate_live_epoch_id"] != manifest["candidate_live_epoch_id"]
        or payload["candidate_prerequisites_reloaded"] is not True
        or payload["five_seed_numerical_replay_passed"] is not True
        or not isinstance(payload["five_seed_max_abs_difference_mm"], (int, float))
        or isinstance(payload["five_seed_max_abs_difference_mm"], bool)
        or not math.isfinite(float(payload["five_seed_max_abs_difference_mm"]))
        or not 0
        <= float(payload["five_seed_max_abs_difference_mm"])
        <= profile["executable_capsule"]["numerical_replay_absolute_tolerance_mm"]
    ):
        raise EpochPreparationIntegrityError("Isolated smoke result changed")
    domain_keys = {
        "schema_version",
        "python_version",
        "isolated_mode",
        "dont_write_bytecode",
        "distribution_count",
        "distribution_inventory_sha256",
        "python_executable_sha256",
        "python_soabi",
        "platform",
    }
    root_domain = _exact_object(
        payload["root_domain"],
        domain_keys.union(
            {
                "tree_sha256",
                "root_modules_imported",
                "compiled_python_file_count",
                "candidate_live_epoch_id",
                "candidate_prerequisites_reloaded",
                "five_seed_numerical_replay_passed",
                "five_seed_max_abs_difference_mm",
            }
        ),
        name="root smoke domain",
    )
    trusted_domain = _exact_object(
        payload["trusted_time_domain"],
        domain_keys.union({"trusted_time_core_imported"}),
        name="trusted-time smoke domain",
    )
    for name, domain, schema, expected_domain in (
        (
            "root",
            root_domain,
            "ootang_epoch_root_domain_smoke_v1",
            profile["root_environment"]["root_smoke_domain"],
        ),
        (
            "trusted-time",
            trusted_domain,
            "ootang_epoch_trusted_time_domain_smoke_v1",
            profile["root_environment"]["trusted_time_smoke_domain"],
        ),
    ):
        if (
            domain["schema_version"] != schema
            or domain["python_version"]
            != profile["root_environment"]["required_python_version"]
            or domain["isolated_mode"] is not True
            or domain["dont_write_bytecode"] is not True
            or type(domain["distribution_count"]) is not int
            or domain["distribution_count"] != expected_domain["distribution_count"]
            or not isinstance(domain["python_soabi"], str)
            or domain["python_soabi"] != expected_domain["python_soabi"]
            or not isinstance(domain["platform"], str)
            or domain["platform"] != expected_domain["platform"]
        ):
            raise EpochPreparationIntegrityError(f"{name} smoke domain changed")
        _require_hash(
            domain["distribution_inventory_sha256"],
            name=f"{name} distribution inventory",
        )
        if (
            domain["distribution_inventory_sha256"]
            != expected_domain["distribution_inventory_sha256"]
        ):
            raise EpochPreparationIntegrityError(
                f"{name} distribution inventory changed"
            )
        _require_hash(
            domain["python_executable_sha256"],
            name=f"{name} python executable",
        )
        if (
            domain["python_executable_sha256"]
            != expected_domain["python_executable_sha256"]
        ):
            raise EpochPreparationIntegrityError(f"{name} Python executable changed")
    if (
        root_domain["tree_sha256"] != manifest["tree_sha256"]
        or root_domain["root_modules_imported"] != manifest["root_modules"]
        or type(root_domain["compiled_python_file_count"]) is not int
        or root_domain["compiled_python_file_count"]
        != profile["executable_capsule"]["expected_local_module_count"]
        or root_domain["candidate_live_epoch_id"] != manifest["candidate_live_epoch_id"]
        or root_domain["candidate_prerequisites_reloaded"] is not True
        or root_domain["five_seed_numerical_replay_passed"] is not True
        or not isinstance(root_domain["five_seed_max_abs_difference_mm"], (int, float))
        or isinstance(root_domain["five_seed_max_abs_difference_mm"], bool)
        or not math.isfinite(float(root_domain["five_seed_max_abs_difference_mm"]))
        or float(root_domain["five_seed_max_abs_difference_mm"])
        != float(payload["five_seed_max_abs_difference_mm"])
        or trusted_domain["trusted_time_core_imported"] is not True
    ):
        raise EpochPreparationIntegrityError("Isolated smoke domain binding changed")
    return payload


def _default_smoke_runner(
    profile: Mapping[str, Any],
    paths: PreparationPaths,
    manifest: Mapping[str, Any],
    tree_root: Path,
) -> dict[str, object]:
    capsule_raw = registry._canonical_bytes(dict(manifest))  # noqa: SLF001
    capsule_path = paths.capsules / f"{_sha256(capsule_raw)}.json"
    uv_path = Path(profile["root_environment"]["required_uv_path"])
    try:
        uv_snapshot = registry._read_regular(  # noqa: SLF001
            uv_path, name="uv executable"
        )
    except registry.EpochRegistryError as exc:
        raise EpochPreparationIntegrityError(str(exc)) from exc
    if uv_snapshot.sha256 != profile["root_environment"]["required_uv_sha256"]:
        raise EpochPreparationIntegrityError("Reviewed uv executable bytes changed")
    environment = {
        "PATH": f"{uv_path.parent}:/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "TZ": "UTC",
        "PYTHONDONTWRITEBYTECODE": "1",
        "UV_NO_PROGRESS": "1",
        "UV_PYTHON_DOWNLOADS": "never",
    }
    for key in ("HOME", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR"):
        value = os.environ.get(key)
        if value:
            environment[key] = value

    def run(command: list[str], *, name: str) -> subprocess.CompletedProcess[bytes]:
        try:
            completed = subprocess.run(
                command,
                cwd=paths.root,
                env=environment,
                check=False,
                capture_output=True,
                timeout=profile["executable_capsule"]["isolated_smoke_timeout_seconds"],
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise EpochPreparationIntegrityError(f"{name} could not complete") from exc
        if (
            completed.returncode != 0
            or len(completed.stdout) > MAX_SMOKE_OUTPUT_BYTES
            or len(completed.stderr) > MAX_SMOKE_OUTPUT_BYTES
        ):
            raise EpochPreparationIntegrityError(f"{name} failed closed")
        return completed

    version_process = run([str(uv_path), "--version"], name="uv version check")
    try:
        uv_version = version_process.stdout.decode("utf-8").strip().split()[1]
    except (UnicodeDecodeError, IndexError) as exc:
        raise EpochPreparationIntegrityError("uv version output changed") from exc
    if uv_version != profile["root_environment"]["required_uv_version"]:
        raise EpochPreparationIntegrityError("uv version changed")

    python_version = profile["root_environment"]["required_python_version"]
    root_command = [
        str(uv_path),
        "--no-config",
        "run",
        "--project",
        str(tree_root),
        "--isolated",
        "--frozen",
        "--python",
        python_version,
        "python",
        "-I",
        "-B",
        "-c",
        _ISOLATED_SMOKE_SCRIPT,
        str(capsule_path),
        str(tree_root),
        python_version,
        str(profile["executable_capsule"]["numerical_replay_absolute_tolerance_mm"]),
    ]
    trusted_project = tree_root / "tools" / "ootang_trusted_time_runtime"
    trusted_command = [
        str(uv_path),
        "--no-config",
        "run",
        "--project",
        str(trusted_project),
        "--isolated",
        "--frozen",
        "--python",
        python_version,
        "python",
        "-I",
        "-B",
        "-c",
        _TRUSTED_TIME_SMOKE_SCRIPT,
        str(tree_root),
        python_version,
    ]
    root_completed = run(root_command, name="isolated root executable smoke")
    trusted_completed = run(
        trusted_command, name="isolated trusted-time executable smoke"
    )
    try:
        root_result = json.loads(root_completed.stdout.decode("utf-8"))
        trusted_result = json.loads(trusted_completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EpochPreparationIntegrityError(
            "Isolated executable smoke returned invalid JSON"
        ) from exc
    if not isinstance(root_result, dict) or not isinstance(trusted_result, dict):
        raise EpochPreparationIntegrityError(
            "Isolated executable smoke result is not an object"
        )
    result = {
        "schema_version": "ootang_epoch_isolated_smoke_result_v2",
        "python_version": python_version,
        "uv_version": uv_version,
        "uv_executable_sha256": uv_snapshot.sha256,
        "isolated_mode": True,
        "dont_write_bytecode": True,
        "tree_sha256": manifest["tree_sha256"],
        "root_modules_imported": root_result.get("root_modules_imported"),
        "compiled_python_file_count": root_result.get("compiled_python_file_count"),
        "candidate_live_epoch_id": manifest["candidate_live_epoch_id"],
        "candidate_prerequisites_reloaded": root_result.get(
            "candidate_prerequisites_reloaded"
        ),
        "five_seed_numerical_replay_passed": root_result.get(
            "five_seed_numerical_replay_passed"
        ),
        "five_seed_max_abs_difference_mm": root_result.get(
            "five_seed_max_abs_difference_mm"
        ),
        "root_domain": root_result,
        "trusted_time_domain": trusted_result,
    }
    return _validate_smoke_result(profile, manifest, result)


def _smoke_receipt_payload(
    profile: Mapping[str, Any],
    capsule_snapshot: registry.ArtifactSnapshot,
    manifest: Mapping[str, Any],
    result: Mapping[str, object],
) -> dict[str, object]:
    return {
        "schema_version": profile["protocol"]["smoke_receipt_schema_version"],
        "case": "ootang",
        "preparation_profile_sha256": profile["_profile_sha256"],
        "preparation_profile": manifest["preparation_profile"],
        "preparation_implementation_sha256": manifest[
            "preparation_implementation_sha256"
        ],
        "preparation_implementation": manifest["preparation_implementation"],
        "candidate_id": manifest["candidate_id"],
        "slot_id": manifest["slot_id"],
        "executable_capsule_sha256": capsule_snapshot.sha256,
        "tree_sha256": manifest["tree_sha256"],
        "smoke_result": dict(result),
        "isolated_import_compile_smoke_passed": True,
        "isolated_candidate_prerequisite_reload_passed": True,
        "five_seed_numerical_replay_passed": True,
        "trusted_time_isolated_domain_smoke_passed": True,
        "same_origin_executable_preflight_verified": True,
        "portable_offline_runtime": False,
        "old_epoch_drain_implemented": False,
        "active_epoch_switch_implemented": False,
        "automatic_epoch_rotation_implemented": False,
        "e2_live_evidence_eligible": False,
        "formal_warning_output": False,
    }


def _same_current_smoke_semantics(
    stored: Mapping[str, Any], current: Mapping[str, Any]
) -> bool:
    def normalized(value: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(value)
        smoke = dict(result["smoke_result"])
        root_domain = dict(smoke["root_domain"])
        smoke["five_seed_max_abs_difference_mm"] = 0.0
        root_domain["five_seed_max_abs_difference_mm"] = 0.0
        smoke["root_domain"] = root_domain
        result["smoke_result"] = smoke
        return result

    return normalized(stored) == normalized(current)


def _publish_smoke_receipt(
    profile: Mapping[str, Any],
    paths: PreparationPaths,
    capsule_snapshot: registry.ArtifactSnapshot,
    manifest: Mapping[str, Any],
    result: Mapping[str, object],
) -> registry.ArtifactSnapshot:
    payload = _smoke_receipt_payload(profile, capsule_snapshot, manifest, result)
    raw = registry._canonical_bytes(payload)  # noqa: SLF001
    return registry._publish_once(  # noqa: SLF001
        paths.smoke_receipts / f"{_sha256(raw)}.json",
        raw,
        root=paths.root,
        name="executable smoke receipt",
    )


def _verify_smoke_receipt(
    profile: Mapping[str, Any],
    paths: PreparationPaths,
    reference: object,
    *,
    capsule_manifest: Mapping[str, Any],
    capsule_sha256: str,
) -> dict[str, Any]:
    record = _exact_object(reference, REFERENCE_KEYS, name="smoke receipt ref")
    expected_sha = _require_hash(record["sha256"], name="smoke receipt sha256")
    size = record["size_bytes"]
    if type(size) is not int or size < 0:
        raise EpochPreparationIntegrityError("Smoke receipt size changed")
    path = registry._contained(  # noqa: SLF001
        paths.root, record["path"], name="smoke receipt path"
    )
    if path.parent != paths.smoke_receipts or path.name != f"{expected_sha}.json":
        raise EpochPreparationIntegrityError("Smoke receipt namespace changed")
    snapshot = registry._read_regular(  # noqa: SLF001
        path, name="smoke receipt", maximum_bytes=MAX_CONTROL_BYTES
    )
    if snapshot.sha256 != expected_sha or snapshot.size_bytes != size:
        raise EpochPreparationIntegrityError("Smoke receipt bytes changed")
    receipt = registry._decode_json(snapshot.raw, name="smoke receipt")  # noqa: SLF001
    if snapshot.raw != registry._canonical_bytes(receipt):  # noqa: SLF001
        raise EpochPreparationIntegrityError("Smoke receipt is not canonical")
    expected = _smoke_receipt_payload(
        profile,
        registry.ArtifactSnapshot(
            path=paths.capsules / f"{capsule_sha256}.json",
            raw=b"",
            sha256=capsule_sha256,
            size_bytes=0,
        ),
        capsule_manifest,
        _validate_smoke_result(profile, capsule_manifest, receipt.get("smoke_result")),
    )
    if receipt != expected:
        raise EpochPreparationIntegrityError("Smoke receipt binding changed")
    return receipt


def _find_matching_smoke_receipt(
    profile: Mapping[str, Any],
    paths: PreparationPaths,
    *,
    capsule_manifest: Mapping[str, Any],
    capsule_sha256: str,
) -> tuple[dict[str, Any], registry.ArtifactSnapshot] | None:
    if not paths.smoke_receipts.exists() and not paths.smoke_receipts.is_symlink():
        return None
    if paths.smoke_receipts.is_symlink() or not stat.S_ISDIR(
        os.lstat(paths.smoke_receipts).st_mode
    ):
        raise EpochPreparationIntegrityError(
            "Smoke receipt namespace is not a real directory"
        )
    matches: list[tuple[dict[str, Any], registry.ArtifactSnapshot]] = []
    for path in sorted(paths.smoke_receipts.iterdir(), key=lambda item: item.name):
        snapshot = registry._read_regular(  # noqa: SLF001
            path, name="orphan smoke receipt", maximum_bytes=MAX_CONTROL_BYTES
        )
        if path.name != f"{snapshot.sha256}.json":
            raise EpochPreparationIntegrityError("Smoke receipt object name changed")
        preliminary = registry._decode_json(  # noqa: SLF001
            snapshot.raw, name="orphan smoke receipt"
        )
        if (
            preliminary.get("candidate_id") != capsule_manifest["candidate_id"]
            or preliminary.get("executable_capsule_sha256") != capsule_sha256
        ):
            continue
        reference = _capsule_reference(paths, snapshot)
        receipt = _verify_smoke_receipt(
            profile,
            paths,
            reference,
            capsule_manifest=capsule_manifest,
            capsule_sha256=capsule_sha256,
        )
        matches.append((receipt, snapshot))
    if len(matches) > 1:
        raise EpochPreparationIntegrityError(
            "Candidate has multiple executable smoke semantics"
        )
    return matches[0] if matches else None


def _event_unsigned(event: Mapping[str, object]) -> dict[str, object]:
    return {key: event[key] for key in event if key != "entry_sha256"}


def _event_path(paths: PreparationPaths, sequence: int) -> Path:
    return paths.events / f"{sequence:020d}.json"


def _head_payload(
    profile: Mapping[str, Any], event: Mapping[str, object]
) -> dict[str, object]:
    return {
        "schema_version": profile["protocol"]["head_schema_version"],
        "preparation_profile_sha256": profile["_profile_sha256"],
        "sequence_id": event["sequence_id"],
        "entry_sha256": event["entry_sha256"],
        "candidate_id": event["candidate_id"],
        "registry_event_sequence_id": event["registry_event_sequence_id"],
    }


def _read_head(
    profile: Mapping[str, Any], paths: PreparationPaths
) -> dict[str, Any] | None:
    if not paths.head.exists() and not paths.head.is_symlink():
        return None
    snapshot = registry._read_regular(  # noqa: SLF001
        paths.head, name="preparation head", maximum_bytes=MAX_CONTROL_BYTES
    )
    head = registry._decode_json(snapshot.raw, name="preparation head")  # noqa: SLF001
    if snapshot.raw != registry._canonical_bytes(head):  # noqa: SLF001
        raise EpochPreparationIntegrityError("Preparation head is not canonical")
    _exact_object(
        head,
        {
            "schema_version",
            "preparation_profile_sha256",
            "sequence_id",
            "entry_sha256",
            "candidate_id",
            "registry_event_sequence_id",
        },
        name="preparation head",
    )
    if (
        head["schema_version"] != profile["protocol"]["head_schema_version"]
        or head["preparation_profile_sha256"] != profile["_profile_sha256"]
    ):
        raise EpochPreparationIntegrityError("Preparation head contract changed")
    return head


def replay_preparations(
    profile: Mapping[str, Any],
    paths: PreparationPaths,
    r1_profile: Mapping[str, Any],
    r1_paths: registry.RegistryPaths,
    r1_events: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    if not paths.events.exists() and not paths.events.is_symlink():
        if paths.head.exists() or paths.head.is_symlink():
            raise EpochPreparationIntegrityError(
                "Preparation head exists without event chain"
            )
        return ()
    if paths.events.is_symlink() or not stat.S_ISDIR(os.lstat(paths.events).st_mode):
        raise EpochPreparationIntegrityError(
            "Preparation events path is not a real directory"
        )
    by_registry_sequence = {event["sequence_id"]: event for event in r1_events}
    entries = sorted(paths.events.iterdir(), key=lambda item: item.name)
    events: list[dict[str, Any]] = []
    previous = ZERO_HASH
    previous_registry_sequence = 0
    preparation_identities: set[tuple[str, str]] = set()
    for expected_sequence, path in enumerate(entries, start=1):
        if path.name != f"{expected_sequence:020d}.json":
            raise EpochPreparationIntegrityError(
                "Preparation event sequence has a gap or branch"
            )
        snapshot = registry._read_regular(  # noqa: SLF001
            path, name="preparation event", maximum_bytes=MAX_CONTROL_BYTES
        )
        event = registry._decode_json(snapshot.raw, name="preparation event")  # noqa: SLF001
        if snapshot.raw != registry._canonical_bytes(event):  # noqa: SLF001
            raise EpochPreparationIntegrityError("Preparation event is not canonical")
        _exact_object(
            event,
            {
                "schema_version",
                "preparation_profile_sha256",
                "preparation_profile",
                "preparation_implementation_sha256",
                "preparation_implementation",
                "sequence_id",
                "event_type",
                "registry_event_sequence_id",
                "registry_event_entry_sha256",
                "candidate_id",
                "slot_id",
                "executable_capsule",
                "smoke_receipt",
                "previous_entry_sha256",
                "entry_sha256",
                "transitive_local_import_closure_materialized",
                "materialized_executable_tree",
                "isolated_import_compile_smoke_passed",
                "isolated_candidate_prerequisite_reload_passed",
                "five_seed_numerical_replay_passed",
                "trusted_time_isolated_domain_smoke_passed",
                "same_origin_executable_preflight_verified",
                "portable_offline_runtime",
                "old_epoch_drain_implemented",
                "active_epoch_switch_implemented",
                "automatic_epoch_rotation_implemented",
                "trusted_anchor_receipt_verified",
                "e2_live_evidence_eligible",
                "real_activation_ready",
                "formal_warning_output",
            },
            name="preparation event",
        )
        registry_sequence = event["registry_event_sequence_id"]
        if (
            event["schema_version"] != profile["protocol"]["event_schema_version"]
            or event["preparation_profile_sha256"] != profile["_profile_sha256"]
            or _require_hash(
                event["preparation_implementation_sha256"],
                name="event preparation implementation",
            )
            != event["preparation_implementation_sha256"]
            or type(event["sequence_id"]) is not int
            or event["sequence_id"] != expected_sequence
            or type(registry_sequence) is not int
            or registry_sequence < previous_registry_sequence
            or event["previous_entry_sha256"] != previous
            or event["transitive_local_import_closure_materialized"] is not True
            or event["materialized_executable_tree"] is not True
            or event["isolated_import_compile_smoke_passed"] is not True
            or event["isolated_candidate_prerequisite_reload_passed"] is not True
            or event["five_seed_numerical_replay_passed"] is not True
            or event["trusted_time_isolated_domain_smoke_passed"] is not True
            or event["same_origin_executable_preflight_verified"] is not True
            or event["portable_offline_runtime"] is not False
            or any(event[key] is not False for key in FALSE_CLAIM_KEYS)
        ):
            raise EpochPreparationIntegrityError("Preparation event contract changed")
        calculated = _sha256(
            registry._canonical_bytes(_event_unsigned(event))  # noqa: SLF001
        )
        if event["entry_sha256"] != calculated:
            raise EpochPreparationIntegrityError("Preparation event hash changed")
        r1_event = by_registry_sequence.get(registry_sequence)
        if (
            r1_event is None
            or event["registry_event_entry_sha256"] != r1_event["entry_sha256"]
            or event["candidate_id"] != r1_event["candidate_id"]
            or event["slot_id"] != r1_event["slot_id"]
        ):
            raise EpochPreparationIntegrityError("Preparation event R1 binding changed")
        same_registry_event = bool(events) and (
            registry_sequence == previous_registry_sequence
        )
        expected_event_type = (
            "candidate_revalidated" if same_registry_event else "candidate_prepared"
        )
        if event["event_type"] != expected_event_type:
            raise EpochPreparationIntegrityError(
                "Preparation event lifecycle type changed"
            )
        if same_registry_event and (
            event["registry_event_entry_sha256"]
            != events[-1]["registry_event_entry_sha256"]
            or event["candidate_id"] != events[-1]["candidate_id"]
            or event["slot_id"] != events[-1]["slot_id"]
        ):
            raise EpochPreparationIntegrityError(
                "Candidate revalidation changed its immutable R1 identity"
            )
        candidate_id = _require_hash(event["candidate_id"], name="candidate id")
        preparation_identity = (
            candidate_id,
            event["preparation_implementation_sha256"],
        )
        if preparation_identity in preparation_identities:
            raise EpochPreparationIntegrityError(
                "Candidate implementation was prepared twice"
            )
        receipt = registry._verify_receipt(  # noqa: SLF001
            r1_profile, r1_paths, r1_event["candidate_receipt"]
        )
        capsule = _verify_executable_capsule(
            profile,
            paths,
            event["executable_capsule"],
            r1_paths=r1_paths,
            r1_manifest=registry._verify_capsule(  # noqa: SLF001
                r1_profile, r1_paths, receipt["capsule"]
            ),
            expected_candidate=receipt,
            expected_registry_event=r1_event,
        )
        if (
            event["preparation_profile"] != capsule["preparation_profile"]
            or event["preparation_implementation_sha256"]
            != capsule["preparation_implementation_sha256"]
            or event["preparation_implementation"]
            != capsule["preparation_implementation"]
        ):
            raise EpochPreparationIntegrityError(
                "Preparation event implementation binding changed"
            )
        _verify_materialized_tree(paths, capsule, _tree_root(paths, capsule))
        capsule_ref = _exact_object(
            event["executable_capsule"], REFERENCE_KEYS, name="capsule ref"
        )
        _verify_smoke_receipt(
            profile,
            paths,
            event["smoke_receipt"],
            capsule_manifest=capsule,
            capsule_sha256=_require_hash(
                capsule_ref["sha256"], name="capsule ref sha256"
            ),
        )
        preparation_identities.add(preparation_identity)
        previous = calculated
        previous_registry_sequence = registry_sequence
        events.append(event)
    head = _read_head(profile, paths)
    if head is not None:
        sequence = head["sequence_id"]
        if type(sequence) is not int or sequence < 1 or sequence > len(events):
            raise EpochPreparationIntegrityError(
                "Preparation head claims an unknown sequence"
            )
        if head != _head_payload(profile, events[sequence - 1]):
            raise EpochPreparationIntegrityError(
                "Preparation head conflicts with its event"
            )
    return tuple(events)


def _append_preparation_event(
    profile: Mapping[str, Any],
    paths: PreparationPaths,
    r1_profile: Mapping[str, Any],
    r1_paths: registry.RegistryPaths,
    events: Sequence[Mapping[str, Any]],
    candidate_event: Mapping[str, Any],
    capsule_snapshot: registry.ArtifactSnapshot,
    smoke_snapshot: registry.ArtifactSnapshot,
) -> dict[str, object]:
    receipt = registry._verify_receipt(  # noqa: SLF001
        r1_profile,
        r1_paths,
        candidate_event["candidate_receipt"],
        require_namespaces_empty=True,
        verify_current_artifacts=True,
    )
    r1_manifest = registry._verify_capsule(  # noqa: SLF001
        r1_profile, r1_paths, receipt["capsule"]
    )
    capsule_reference = _capsule_reference(paths, capsule_snapshot)
    capsule_manifest = _verify_executable_capsule(
        profile,
        paths,
        capsule_reference,
        r1_paths=r1_paths,
        r1_manifest=r1_manifest,
        expected_candidate=receipt,
        expected_registry_event=candidate_event,
        require_current_implementation=True,
    )
    _verify_materialized_tree(
        paths, capsule_manifest, _tree_root(paths, capsule_manifest)
    )
    smoke_reference = _capsule_reference(paths, smoke_snapshot)
    _verify_smoke_receipt(
        profile,
        paths,
        smoke_reference,
        capsule_manifest=capsule_manifest,
        capsule_sha256=_require_hash(
            capsule_reference["sha256"], name="capsule ref sha256"
        ),
    )
    sequence = len(events) + 1
    event_type = (
        "candidate_revalidated"
        if events
        and events[-1]["registry_event_sequence_id"] == candidate_event["sequence_id"]
        else "candidate_prepared"
    )
    unsigned: dict[str, object] = {
        "schema_version": profile["protocol"]["event_schema_version"],
        "preparation_profile_sha256": profile["_profile_sha256"],
        "preparation_profile": capsule_manifest["preparation_profile"],
        "preparation_implementation_sha256": capsule_manifest[
            "preparation_implementation_sha256"
        ],
        "preparation_implementation": capsule_manifest["preparation_implementation"],
        "sequence_id": sequence,
        "event_type": event_type,
        "registry_event_sequence_id": candidate_event["sequence_id"],
        "registry_event_entry_sha256": candidate_event["entry_sha256"],
        "candidate_id": candidate_event["candidate_id"],
        "slot_id": candidate_event["slot_id"],
        "executable_capsule": capsule_reference,
        "smoke_receipt": smoke_reference,
        "previous_entry_sha256": events[-1]["entry_sha256"] if events else ZERO_HASH,
        "transitive_local_import_closure_materialized": True,
        "materialized_executable_tree": True,
        "isolated_import_compile_smoke_passed": True,
        "isolated_candidate_prerequisite_reload_passed": True,
        "five_seed_numerical_replay_passed": True,
        "trusted_time_isolated_domain_smoke_passed": True,
        "same_origin_executable_preflight_verified": True,
        "portable_offline_runtime": False,
        "old_epoch_drain_implemented": False,
        "active_epoch_switch_implemented": False,
        "automatic_epoch_rotation_implemented": False,
        "trusted_anchor_receipt_verified": False,
        "e2_live_evidence_eligible": False,
        "real_activation_ready": False,
        "formal_warning_output": False,
    }
    event = {
        **unsigned,
        "entry_sha256": _sha256(registry._canonical_bytes(unsigned)),  # noqa: SLF001
    }
    registry._publish_once(  # noqa: SLF001
        _event_path(paths, sequence),
        registry._canonical_bytes(event),  # noqa: SLF001
        root=paths.root,
        name="preparation event",
    )
    return event


def _refresh_head(
    profile: Mapping[str, Any],
    paths: PreparationPaths,
    events: Sequence[Mapping[str, object]],
) -> None:
    if not events:
        return
    expected = _head_payload(profile, events[-1])
    head = _read_head(profile, paths)
    if head is not None:
        sequence = head["sequence_id"]
        if type(sequence) is not int or sequence > len(events):
            raise EpochPreparationIntegrityError(
                "Preparation head cannot be rolled back"
            )
        if sequence == len(events) and head != expected:
            raise EpochPreparationIntegrityError(
                "Preparation head conflicts with chain tip"
            )
    registry._atomic_cache(  # noqa: SLF001
        paths.head,
        registry._canonical_bytes(expected),  # noqa: SLF001
        root=paths.root,
        name="preparation head",
    )


def _status_payload(
    profile: Mapping[str, Any],
    *,
    checked_at: datetime,
    status: str,
    reason: str,
    r1_events: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
) -> dict[str, object]:
    latest = events[-1] if events else None
    return {
        "schema_version": profile["protocol"]["status_schema_version"],
        "profile_id": profile["profile_id"],
        "profile_sha256": profile["_profile_sha256"],
        "checked_at_utc": checked_at.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "preparation_status": status,
        "reason": reason,
        "registry_event_count": len(r1_events),
        "preparation_event_count": len(events),
        "terminal_entry_sha256": latest["entry_sha256"] if latest else ZERO_HASH,
        "latest_prepared_candidate_id": latest["candidate_id"] if latest else None,
        "immutable_candidate_registry_verified": True,
        "transitive_local_import_closure_implemented": True,
        "content_addressed_executable_materialization_implemented": True,
        "same_origin_executable_preflight_verified": bool(events),
        "canonical_project_root_required": True,
        "canonical_slot_root_required": True,
        "relocatable": False,
        "portable_offline_runtime": False,
        "isolated_import_compile_smoke_implemented": True,
        "isolated_candidate_prerequisite_reload_smoke_implemented": True,
        "five_seed_numerical_replay_verified": bool(events),
        "trusted_time_isolated_domain_smoke_verified": bool(events),
        "old_epoch_drain_implemented": False,
        "active_epoch_switch_implemented": False,
        "automatic_epoch_rotation_implemented": False,
        "trusted_anchor_receipt_verified": False,
        "e2_live_evidence_eligible": False,
        "real_activation_ready": False,
        "formal_warning_output": False,
    }


def _write_status(
    profile: Mapping[str, Any], paths: PreparationPaths, payload: Mapping[str, object]
) -> Path:
    registry._atomic_cache(  # noqa: SLF001
        paths.status,
        registry._canonical_bytes(dict(payload)),  # noqa: SLF001
        root=paths.root,
        name="preparation status",
    )
    return paths.status


def _poll_epoch_preparation(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    runtime_root: Path | None = None,
    clock: Callable[[], datetime] | None = None,
    _smoke_runner: SmokeRunner | None = None,
) -> Path:
    if _smoke_runner is not None and runtime_root is None:
        raise EpochPreparationIntegrityError(
            "Injected smoke runners are restricted to isolated test runtimes"
        )
    if runtime_root is not None:
        isolated = registry._absolute_lexical(  # noqa: SLF001
            runtime_root.parent.resolve(strict=False) / runtime_root.name
        )
        production = registry._absolute_lexical(ROOT / "runtime")  # noqa: SLF001
        try:
            isolated.relative_to(production)
        except ValueError:
            pass
        else:
            raise EpochPreparationIntegrityError(
                "Private runtime overrides cannot target the production runtime tree"
            )
    profile = load_preparation_profile(config_path)
    r1_profile = registry.load_registry_profile()
    paths = preparation_paths(profile, runtime_root=runtime_root)
    r1_paths = _r1_paths(r1_profile, paths)
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    if now.tzinfo is None or now.utcoffset() is None:
        raise EpochPreparationIntegrityError("Preparation clock must be timezone-aware")
    registry._mkdir(paths.root, root=paths.root)  # noqa: SLF001
    try:
        lock = registry._acquire_manager_lock(r1_paths)  # noqa: SLF001
    except registry.EpochRegistryBusyError as exc:
        raise EpochPreparationBusyError(str(exc)) from exc
    try:
        registry._cleanup_temporary_namespace(paths.root)  # noqa: SLF001
        observations, observed_semantics = registry.replay_feed_observations(
            r1_profile, r1_paths
        )
        del observations
        r1_events = registry.replay_registry(
            r1_profile,
            r1_paths,
            _observed_feed_semantics=observed_semantics,
        )
        events = replay_preparations(profile, paths, r1_profile, r1_paths, r1_events)
        _refresh_head(profile, paths, events)
        if not r1_events:
            return _write_status(
                profile,
                paths,
                _status_payload(
                    profile,
                    checked_at=now,
                    status="waiting_for_immutable_candidate",
                    reason="R1 has no immutable candidate event",
                    r1_events=r1_events,
                    events=events,
                ),
            )
        latest_r1 = r1_events[-1]
        if (
            events
            and events[-1]["candidate_id"] == latest_r1["candidate_id"]
            and events[-1]["preparation_implementation_sha256"]
            == profile["_implementation_sha256"]
        ):
            receipt = registry._verify_receipt(  # noqa: SLF001
                r1_profile,
                r1_paths,
                latest_r1["candidate_receipt"],
                require_namespaces_empty=True,
                verify_current_artifacts=True,
            )
            r1_manifest = registry._verify_capsule(  # noqa: SLF001
                r1_profile, r1_paths, receipt["capsule"]
            )
            capsule_reference = events[-1]["executable_capsule"]
            manifest = _verify_executable_capsule(
                profile,
                paths,
                capsule_reference,
                r1_paths=r1_paths,
                r1_manifest=r1_manifest,
                expected_candidate=receipt,
                expected_registry_event=latest_r1,
                require_current_implementation=True,
            )
            tree_root = _tree_root(paths, manifest)
            _verify_materialized_tree(paths, manifest, tree_root)
            capsule_ref = _exact_object(
                capsule_reference, REFERENCE_KEYS, name="capsule ref"
            )
            capsule_sha256 = _require_hash(
                capsule_ref["sha256"], name="capsule ref sha256"
            )
            stored_smoke = _verify_smoke_receipt(
                profile,
                paths,
                events[-1]["smoke_receipt"],
                capsule_manifest=manifest,
                capsule_sha256=capsule_sha256,
            )
            live_smoke = (_smoke_runner or _default_smoke_runner)(
                profile, paths, manifest, tree_root
            )
            live_smoke = _validate_smoke_result(profile, manifest, live_smoke)
            current_smoke = _smoke_receipt_payload(
                profile,
                registry.ArtifactSnapshot(
                    path=paths.capsules / f"{capsule_sha256}.json",
                    raw=b"",
                    sha256=capsule_sha256,
                    size_bytes=capsule_ref["size_bytes"],
                ),
                manifest,
                live_smoke,
            )
            if not _same_current_smoke_semantics(stored_smoke, current_smoke):
                raise EpochPreparationIntegrityError(
                    "Current isolated smoke differs from immutable preparation receipt"
                )
            post_profile = load_preparation_profile(config_path)
            post_r1_profile = registry.load_registry_profile()
            if (
                post_profile["_profile_sha256"] != profile["_profile_sha256"]
                or post_profile["_implementation_sha256"]
                != profile["_implementation_sha256"]
                or post_r1_profile["_profile_sha256"] != r1_profile["_profile_sha256"]
                or post_r1_profile["implementation"] != r1_profile["implementation"]
            ):
                raise EpochPreparationIntegrityError(
                    "Reviewed same-origin bindings changed during current re-attestation"
                )
            registry._verify_receipt(  # noqa: SLF001
                r1_profile,
                r1_paths,
                latest_r1["candidate_receipt"],
                require_namespaces_empty=True,
                verify_current_artifacts=True,
            )
            final_manifest = _verify_executable_capsule(
                profile,
                paths,
                capsule_reference,
                r1_paths=r1_paths,
                r1_manifest=r1_manifest,
                expected_candidate=receipt,
                expected_registry_event=latest_r1,
                require_current_implementation=True,
            )
            if final_manifest != manifest:
                raise EpochPreparationIntegrityError(
                    "Executable capsule changed during current re-attestation"
                )
            _verify_materialized_tree(paths, final_manifest, tree_root)
            _verify_smoke_receipt(
                profile,
                paths,
                events[-1]["smoke_receipt"],
                capsule_manifest=final_manifest,
                capsule_sha256=capsule_sha256,
            )
            return _write_status(
                profile,
                paths,
                _status_payload(
                    profile,
                    checked_at=now,
                    status="executable_candidate_prepared",
                    reason=(
                        "immutable executable capsule and current isolated smoke "
                        "re-attested; drain and activation remain outside R2a"
                    ),
                    r1_events=r1_events,
                    events=events,
                ),
            )
        if events:
            last_registry_sequence = events[-1]["registry_event_sequence_id"]
            if latest_r1["sequence_id"] < last_registry_sequence:
                raise EpochPreparationIntegrityError(
                    "R1 candidate tip would roll preparation backward"
                )
            if (
                latest_r1["sequence_id"] == last_registry_sequence
                and events[-1]["candidate_id"] != latest_r1["candidate_id"]
            ):
                raise EpochPreparationIntegrityError(
                    "R1 candidate identity changed at a prepared sequence"
                )
            if any(
                event["candidate_id"] == latest_r1["candidate_id"]
                and event["preparation_implementation_sha256"]
                == profile["_implementation_sha256"]
                for event in events
            ):
                raise EpochPreparationIntegrityError(
                    "Preparation implementation rollback reused prior semantics"
                )
        receipt = registry._verify_receipt(  # noqa: SLF001
            r1_profile,
            r1_paths,
            latest_r1["candidate_receipt"],
            require_namespaces_empty=True,
            verify_current_artifacts=True,
        )
        manifest, capsule_snapshot = _build_executable_capsule(
            profile, paths, r1_profile, r1_paths, latest_r1, receipt
        )
        r1_manifest = registry._verify_capsule(  # noqa: SLF001
            r1_profile, r1_paths, receipt["capsule"]
        )
        verified_manifest = _verify_executable_capsule(
            profile,
            paths,
            _capsule_reference(paths, capsule_snapshot),
            r1_paths=r1_paths,
            r1_manifest=r1_manifest,
            expected_candidate=receipt,
            expected_registry_event=latest_r1,
            require_current_implementation=True,
        )
        tree_root = _materialize_tree(paths, verified_manifest)
        existing_smoke = _find_matching_smoke_receipt(
            profile,
            paths,
            capsule_manifest=verified_manifest,
            capsule_sha256=capsule_snapshot.sha256,
        )
        smoke = (_smoke_runner or _default_smoke_runner)(
            profile, paths, verified_manifest, tree_root
        )
        smoke = _validate_smoke_result(profile, verified_manifest, smoke)
        if existing_smoke is None:
            smoke_snapshot = _publish_smoke_receipt(
                profile, paths, capsule_snapshot, verified_manifest, smoke
            )
        else:
            orphan_receipt, smoke_snapshot = existing_smoke
            current_receipt = _smoke_receipt_payload(
                profile, capsule_snapshot, verified_manifest, smoke
            )
            if not _same_current_smoke_semantics(orphan_receipt, current_receipt):
                raise EpochPreparationIntegrityError(
                    "Current isolated smoke differs from orphan preparation receipt"
                )
        _verify_smoke_receipt(
            profile,
            paths,
            _capsule_reference(paths, smoke_snapshot),
            capsule_manifest=verified_manifest,
            capsule_sha256=capsule_snapshot.sha256,
        )
        post_profile = load_preparation_profile(config_path)
        post_r1_profile = registry.load_registry_profile()
        if (
            post_profile["_profile_sha256"] != profile["_profile_sha256"]
            or post_profile["_implementation_sha256"]
            != profile["_implementation_sha256"]
            or post_r1_profile["_profile_sha256"] != r1_profile["_profile_sha256"]
            or post_r1_profile["implementation"] != r1_profile["implementation"]
        ):
            raise EpochPreparationIntegrityError(
                "Reviewed same-origin bindings changed during executable smoke"
            )
        registry._verify_receipt(  # noqa: SLF001
            r1_profile,
            r1_paths,
            latest_r1["candidate_receipt"],
            require_namespaces_empty=True,
            verify_current_artifacts=True,
        )
        event = _append_preparation_event(
            profile,
            paths,
            r1_profile,
            r1_paths,
            events,
            latest_r1,
            capsule_snapshot,
            smoke_snapshot,
        )
        replayed = replay_preparations(profile, paths, r1_profile, r1_paths, r1_events)
        if len(replayed) != len(events) + 1 or replayed[-1] != event:
            raise EpochPreparationIntegrityError(
                "Preparation append did not become its chain tip"
            )
        _refresh_head(profile, paths, replayed)
        return _write_status(
            profile,
            paths,
            _status_payload(
                profile,
                checked_at=now,
                status="executable_candidate_prepared",
                reason=(
                    "transitive local closure materialized and isolated import/"
                    "compile/prerequisite smoke passed; drain remains outside R2a"
                ),
                r1_events=r1_events,
                events=replayed,
            ),
        )
    except registry.EpochRegistryBusyError as exc:
        raise EpochPreparationBusyError(str(exc)) from exc
    except registry.EpochRegistryError as exc:
        raise EpochPreparationIntegrityError(str(exc)) from exc
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


def poll_epoch_preparation(*, config_path: Path = DEFAULT_CONFIG_PATH) -> Path:
    """Run R2a using only reviewed configuration and machine-owned inputs."""

    return _poll_epoch_preparation(config_path=config_path)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        status_path = poll_epoch_preparation(config_path=args.config)
    except EpochPreparationBusyError as exc:
        print(f"[ootang-epoch-preparation] busy: {exc}", file=sys.stderr)
        return 3
    except EpochPreparationError as exc:
        print(f"[ootang-epoch-preparation] blocked: {exc}", file=sys.stderr)
        return 2
    print(f"[ootang-epoch-preparation] status: {status_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
