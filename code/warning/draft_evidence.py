"""Rebuild the documented Ootang draft-evidence bundle without warnings.

The project has several deliberately isolated diagnostic writers.  Running
them one by one makes it easy to accidentally retain a mixture of protocol
versions, especially after an evidence or source-boundary update.  This module
rebuilds the active Ootang-only evidence set with one *draft* protocol and
writes a bundle manifest that verifies every component remains non-formal.

It is intentionally separate from :mod:`warning.formal_warning`: it never
treats a diagnostic candidate as a formal Word-thesis ``V0``, calculates
velocity/tangent levels, a fusion result, a warning timeline, or a Vajont
artifact.  It also excludes the retired MVIF profile-slope artifact.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any

import pandas as pd


# Permit direct execution with ``uv run python code/warning/...py`` as well as
# package imports from the test suite.
CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning.bai_perron_initial_slope_diagnostics import (  # noqa: E402
    write_fit_bai_perron_initial_slope_candidates,
)
from warning.delta_v_diagnostics import write_delta_v_diagnostics  # noqa: E402
from warning.interval_diagnostics import write_calibration_diagnostics  # noqa: E402
from warning.interval_reference_states import (  # noqa: E402
    write_reference_interval_states,
)
from warning.mvif_diagnostics import write_fit_mvif_diagnostics  # noqa: E402
from warning.protocol import (  # noqa: E402
    DEFAULT_PROTOCOL_PATH,
    load_protocol,
    protocol_content_sha256,
    unresolved_item_ids,
)
from warning.stable_segment_diagnostics import (  # noqa: E402
    write_fit_stable_segment_candidates,
)
from warning.velocity_tangent_diagnostics import (  # noqa: E402
    write_velocity_tangent_diagnostics,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KINEMATICS_PATH = ROOT / "data" / "ootang_kinematics_long.csv"
DEFAULT_PREDICTIONS_PATH = ROOT / "figures" / "convlstm" / "forecast_predictions.csv"
DEFAULT_OUTPUT_DIR = ROOT / "figures" / "warning_draft"
BUNDLE_MANIFEST_FILENAME = "ootang_draft_warning_evidence_manifest.json"
BUNDLE_ARTIFACT_KIND = "ootang_draft_warning_evidence_bundle"
BUNDLE_STATUS = "draft_evidence_not_formal"
FORMAL_WARNING_ENTRY = "code/warning/formal_warning.py"
RETIRED_ARTIFACTS_EXCLUDED = ("mvif_initial_slope_candidates",)
OOTANG_STATIONS = (
    "ATU1",
    "ATU2",
    "ATU3",
    "ATU4",
    "ATU5",
    "MJ1",
    "MJ3",
    "MJ9",
)


class DraftEvidenceBundleProtocolError(ValueError):
    """Raised when a non-draft/non-Ootang protocol requests a draft bundle."""


class DraftEvidenceBundleIntegrityError(RuntimeError):
    """Raised when a component no longer satisfies the bundle's safeguards."""


@dataclass(frozen=True)
class DraftEvidenceBundleArtifacts:
    """Paths for one coherent set of non-formal Ootang evidence artifacts."""

    manifest_path: Path
    component_manifest_paths: tuple[Path, ...]


@dataclass(frozen=True)
class _StagedComponent:
    """One component written outside the live evidence directory."""

    name: str
    output_path: Path
    manifest_path: Path
    target_output_path: Path
    target_manifest_path: Path
    requires_kinematics: bool
    requires_stable_segment_candidate: bool = False


@dataclass(frozen=True)
class _FileReplacement:
    """One staged file that will atomically replace its live counterpart."""

    source: Path
    target: Path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_draft_ootang_protocol(protocol: dict[str, Any]) -> None:
    if protocol["case"] != "ootang":
        raise DraftEvidenceBundleProtocolError(
            "Draft evidence bundles are restricted to the Ootang case."
        )
    if protocol["status"] != "draft":
        raise DraftEvidenceBundleProtocolError(
            "Draft evidence bundles require a protocol with status=draft; "
            "a frozen protocol belongs to the separately reviewed formal path."
        )


def _station_set(path: Path, *, source_name: str) -> set[str]:
    """Read only the station identity needed to prevent cross-case execution."""

    try:
        stations = pd.read_csv(path, usecols=["station"])
    except FileNotFoundError as exc:
        raise DraftEvidenceBundleProtocolError(
            f"Draft evidence {source_name} input does not exist: {path}"
        ) from exc
    except ValueError as exc:
        raise DraftEvidenceBundleProtocolError(
            f"Draft evidence {source_name} input must contain a station column: {path}"
        ) from exc
    normalized = stations["station"].astype("string").str.strip()
    if normalized.isna().any() or normalized.eq("").any():
        raise DraftEvidenceBundleProtocolError(
            f"Draft evidence {source_name} input has a blank station identifier."
        )
    return set(normalized.tolist())


def _require_ootang_station_inputs(
    *,
    kinematics_path: Path,
    predictions_path: Path,
) -> None:
    """Reject non-Ootang inputs before any diagnostic can write an artifact."""

    expected = set(OOTANG_STATIONS)
    for source_name, path in (
        ("kinematics", kinematics_path),
        ("predictions", predictions_path),
    ):
        actual = _station_set(path, source_name=source_name)
        if actual != expected:
            raise DraftEvidenceBundleProtocolError(
                f"Draft evidence {source_name} stations must be exactly the eight "
                f"Ootang stations; found: {', '.join(sorted(actual)) or '<none>'}."
            )


def _load_component_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DraftEvidenceBundleIntegrityError(
            f"Draft evidence component manifest is missing: {path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise DraftEvidenceBundleIntegrityError(
            f"Draft evidence component manifest is invalid JSON: {path}"
        ) from exc
    if not isinstance(payload, dict):
        raise DraftEvidenceBundleIntegrityError(
            f"Draft evidence component manifest must be an object: {path}"
        )
    return payload


def _component_status(manifest: dict[str, Any]) -> str:
    for field in ("artifact_status", "diagnostic_status", "candidate_status"):
        value = manifest.get(field)
        if isinstance(value, str) and value:
            return value
    raise DraftEvidenceBundleIntegrityError(
        "Draft evidence component manifest has no artifact, diagnostic, or "
        "candidate status."
    )


def _component_output(
    manifest: dict[str, Any],
    *,
    physical_output_path: Path,
    recorded_output_path: Path,
) -> dict[str, Any]:
    output_container = manifest.get("outputs")
    candidates = (
        manifest.get("states"),
        manifest.get("summary"),
        output_container.get("summary")
        if isinstance(output_container, dict)
        else None,
    )
    output = next((value for value in candidates if isinstance(value, dict)), None)
    if output is None:
        raise DraftEvidenceBundleIntegrityError(
            "Draft evidence component manifest has no states or summary output."
        )

    recorded_path = output.get("path")
    recorded_sha256 = output.get("sha256")
    if not isinstance(recorded_path, str) or not isinstance(recorded_sha256, str):
        raise DraftEvidenceBundleIntegrityError(
            "Draft evidence component output must record its path and SHA-256."
        )
    if Path(recorded_path).resolve() != recorded_output_path.resolve():
        raise DraftEvidenceBundleIntegrityError(
            "Draft evidence component output path does not match the writer result."
        )
    if _sha256_file(physical_output_path) != recorded_sha256:
        raise DraftEvidenceBundleIntegrityError(
            "Draft evidence component output SHA-256 does not match its manifest."
        )
    return output


def _require_manifest_source_path(
    manifest: dict[str, Any],
    *,
    component_name: str,
    source_key: str,
    expected_path: Path,
    required: bool,
) -> None:
    source = manifest.get(source_key)
    if source is None and not required:
        return
    if not isinstance(source, dict):
        raise DraftEvidenceBundleIntegrityError(
            f"Draft evidence component {component_name!r} has no {source_key} "
            "path provenance."
        )
    recorded_path = source.get("path")
    if not isinstance(recorded_path, str):
        raise DraftEvidenceBundleIntegrityError(
            f"Draft evidence component {component_name!r} has no {source_key} path."
        )
    if Path(recorded_path).resolve() != expected_path.resolve():
        raise DraftEvidenceBundleIntegrityError(
            f"Draft evidence component {component_name!r} used a mismatched "
            f"{source_key} path."
        )


def _component_record(
    *,
    component: _StagedComponent,
    protocol: dict[str, Any],
    protocol_sha256: str,
    predictions_path: Path,
    kinematics_path: Path,
    stable_segment_candidates_path: Path,
) -> dict[str, Any]:
    """Validate one diagnostic sidecar and return a compact bundle record."""

    manifest = _load_component_manifest(component.manifest_path)
    if manifest.get("formal_warning_output") is not False:
        raise DraftEvidenceBundleIntegrityError(
            f"Draft evidence component {component.name!r} is not explicitly "
            "non-formal."
        )

    component_protocol = manifest.get("protocol")
    if not isinstance(component_protocol, dict):
        raise DraftEvidenceBundleIntegrityError(
            f"Draft evidence component {component.name!r} has no protocol provenance."
        )
    expected_protocol = {
        "id": protocol["protocol_id"],
        "version": protocol["protocol_version"],
        "status": protocol["status"],
        "content_sha256": protocol_sha256,
        "unresolved_item_ids": list(unresolved_item_ids(protocol)),
    }
    for field, expected in expected_protocol.items():
        if component_protocol.get(field) != expected:
            raise DraftEvidenceBundleIntegrityError(
                f"Draft evidence component {component.name!r} has a mismatched "
                f"protocol {field}."
            )

    _require_manifest_source_path(
        manifest,
        component_name=component.name,
        source_key="source_predictions",
        expected_path=predictions_path,
        required=True,
    )
    _require_manifest_source_path(
        manifest,
        component_name=component.name,
        source_key="source_kinematics",
        expected_path=kinematics_path,
        required=component.requires_kinematics,
    )
    if component.requires_stable_segment_candidate:
        _require_manifest_source_path(
            manifest,
            component_name=component.name,
            source_key="source_stable_segment_candidates",
            expected_path=stable_segment_candidates_path,
            required=True,
        )
    source_kinematics = manifest.get("source_kinematics")
    if isinstance(source_kinematics, dict) and source_kinematics.get("vajont_used"):
        raise DraftEvidenceBundleIntegrityError(
            f"Draft evidence component {component.name!r} unexpectedly used Vajont."
        )
    _component_output(
        manifest,
        physical_output_path=component.output_path,
        recorded_output_path=component.target_output_path,
    )
    artifact_kind = manifest.get("artifact_kind")
    if not isinstance(artifact_kind, str) or not artifact_kind:
        raise DraftEvidenceBundleIntegrityError(
            f"Draft evidence component {component.name!r} has no artifact kind."
        )

    return {
        "name": component.name,
        "artifact_kind": artifact_kind,
        "status": _component_status(manifest),
        "formal_warning_output": False,
        "output_path": str(component.target_output_path),
        "output_sha256": _sha256_file(component.output_path),
        "manifest_path": str(component.target_manifest_path),
        "manifest_sha256": _sha256_file(component.manifest_path),
    }


def _stage_component(
    *,
    name: str,
    output_path: Path,
    manifest_path: Path,
    target_dir: Path,
    requires_kinematics: bool,
    requires_stable_segment_candidate: bool = False,
) -> _StagedComponent:
    return _StagedComponent(
        name=name,
        output_path=output_path,
        manifest_path=manifest_path,
        target_output_path=target_dir / output_path.name,
        target_manifest_path=target_dir / manifest_path.name,
        requires_kinematics=requires_kinematics,
        requires_stable_segment_candidate=requires_stable_segment_candidate,
    )


def _write_staged_components(
    *,
    staging_dir: Path,
    target_dir: Path,
    kinematics_path: Path,
    predictions_path: Path,
    protocol_path: Path,
) -> tuple[_StagedComponent, ...]:
    """Run every active diagnostic outside the live artifact directory."""

    interval_states = write_reference_interval_states(
        predictions_path=predictions_path,
        output_dir=staging_dir,
        protocol_path=protocol_path,
    )
    interval_diagnostics = write_calibration_diagnostics(
        predictions_path=predictions_path,
        output_dir=staging_dir,
        protocol_path=protocol_path,
    )
    stable_segments = write_fit_stable_segment_candidates(
        kinematics_path=kinematics_path,
        predictions_path=predictions_path,
        output_dir=staging_dir,
        protocol_path=protocol_path,
    )
    delta_v = write_delta_v_diagnostics(
        kinematics_path=kinematics_path,
        predictions_path=predictions_path,
        output_dir=staging_dir,
        protocol_path=protocol_path,
    )
    velocity_tangent = write_velocity_tangent_diagnostics(
        kinematics_path=kinematics_path,
        predictions_path=predictions_path,
        stable_segment_candidates_path=stable_segments.summary_path,
        output_dir=staging_dir,
        protocol_path=protocol_path,
    )
    mvif = write_fit_mvif_diagnostics(
        kinematics_path=kinematics_path,
        predictions_path=predictions_path,
        output_dir=staging_dir,
        protocol_path=protocol_path,
    )
    bai_perron = write_fit_bai_perron_initial_slope_candidates(
        kinematics_path=kinematics_path,
        predictions_path=predictions_path,
        output_dir=staging_dir,
        protocol_path=protocol_path,
    )
    return (
        _stage_component(
            name="interval_reference_states",
            output_path=interval_states.states_path,
            manifest_path=interval_states.manifest_path,
            target_dir=target_dir,
            requires_kinematics=False,
        ),
        _stage_component(
            name="interval_calibration_diagnostics",
            output_path=interval_diagnostics.summary_path,
            manifest_path=interval_diagnostics.manifest_path,
            target_dir=target_dir,
            requires_kinematics=False,
        ),
        _stage_component(
            name="stable_segment_candidates",
            output_path=stable_segments.summary_path,
            manifest_path=stable_segments.manifest_path,
            target_dir=target_dir,
            requires_kinematics=True,
        ),
        _stage_component(
            name="delta_v_fit_calibration_diagnostics",
            output_path=delta_v.summary_path,
            manifest_path=delta_v.manifest_path,
            target_dir=target_dir,
            requires_kinematics=True,
        ),
        _stage_component(
            name="velocity_tangent_fit_calibration_diagnostics",
            output_path=velocity_tangent.summary_path,
            manifest_path=velocity_tangent.manifest_path,
            target_dir=target_dir,
            requires_kinematics=True,
            requires_stable_segment_candidate=True,
        ),
        _stage_component(
            name="mvif_fit_candidates",
            output_path=mvif.summary_path,
            manifest_path=mvif.manifest_path,
            target_dir=target_dir,
            requires_kinematics=True,
        ),
        _stage_component(
            name="bai_perron_mvif_initial_slope_candidates",
            output_path=bai_perron.summary_path,
            manifest_path=bai_perron.manifest_path,
            target_dir=target_dir,
            requires_kinematics=True,
        ),
    )


def _replace_staged_paths(value: Any, path_mapping: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _replace_staged_paths(item, path_mapping)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_replace_staged_paths(item, path_mapping) for item in value]
    if isinstance(value, str):
        return path_mapping.get(str(Path(value).resolve()), value)
    return value


def _retarget_staged_component_manifests(
    components: tuple[_StagedComponent, ...],
) -> None:
    """Replace staging paths with live paths before promoting sidecars."""

    path_mapping = {
        str(path.resolve()): str(target)
        for component in components
        for path, target in (
            (component.output_path, component.target_output_path),
            (component.manifest_path, component.target_manifest_path),
        )
    }
    for component in components:
        manifest = _load_component_manifest(component.manifest_path)
        retargeted = _replace_staged_paths(manifest, path_mapping)
        component.manifest_path.write_text(
            json.dumps(retargeted, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )


def _promote_staged_files(replacements: tuple[_FileReplacement, ...]) -> None:
    """Atomically replace each live file and roll back catchable errors.

    This protects the live snapshot from ordinary writer or filesystem errors.
    It deliberately does not claim a crash-consistent, bundle-wide transaction
    if the process is forcibly terminated between individual replacements.
    """

    if not replacements:
        return
    backup_dir = Path(
        tempfile.mkdtemp(
            prefix=".ootang-draft-evidence-backup-",
            dir=replacements[0].target.parent.parent,
        )
    )
    completed: list[tuple[_FileReplacement, Path | None]] = []
    try:
        for index, replacement in enumerate(replacements):
            replacement.target.parent.mkdir(parents=True, exist_ok=True)
            backup_path = backup_dir / f"{index:02d}-{replacement.target.name}"
            previous_path = None
            if replacement.target.exists():
                os.replace(replacement.target, backup_path)
                previous_path = backup_path
            try:
                os.replace(replacement.source, replacement.target)
            except BaseException:
                if previous_path is not None and previous_path.exists():
                    os.replace(previous_path, replacement.target)
                raise
            completed.append((replacement, previous_path))
    except BaseException:
        for replacement, previous_path in reversed(completed):
            if replacement.target.exists():
                replacement.target.unlink()
            if previous_path is not None and previous_path.exists():
                os.replace(previous_path, replacement.target)
        raise
    finally:
        shutil.rmtree(backup_dir, ignore_errors=True)


def _bundle_manifest(
    *,
    protocol: dict[str, Any],
    protocol_sha256: str,
    kinematics_path: Path,
    predictions_path: Path,
    components: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "artifact_kind": BUNDLE_ARTIFACT_KIND,
        "artifact_status": BUNDLE_STATUS,
        "formal_warning_output": False,
        "formal_warning_entry": FORMAL_WARNING_ENTRY,
        "case": "ootang",
        "vajont_used": False,
        "ootang_stations": list(OOTANG_STATIONS),
        "protocol": {
            "id": protocol["protocol_id"],
            "version": protocol["protocol_version"],
            "status": protocol["status"],
            "content_sha256": protocol_sha256,
            "unresolved_item_ids": list(unresolved_item_ids(protocol)),
        },
        "source_inputs": {
            "kinematics": {
                "path": str(kinematics_path),
                "sha256": _sha256_file(kinematics_path),
            },
            "predictions": {
                "path": str(predictions_path),
                "sha256": _sha256_file(predictions_path),
            },
        },
        "components": components,
        "retired_artifacts_excluded": list(RETIRED_ARTIFACTS_EXCLUDED),
        "not_evaluated": [
            "formal_velocity_or_tangent_warning_levels",
            "formal_delta_v_near_zero_tolerance",
            "formal_per_station_or_site_fusion",
            "formal_warning_timelines",
            "Vajont_external_case",
        ],
    }


def write_draft_warning_evidence_bundle(
    *,
    kinematics_path: str | Path = DEFAULT_KINEMATICS_PATH,
    predictions_path: str | Path = DEFAULT_PREDICTIONS_PATH,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    protocol_path: str | Path = DEFAULT_PROTOCOL_PATH,
) -> DraftEvidenceBundleArtifacts:
    """Rebuild every active Ootang draft diagnostic under one draft protocol.

    This is a reproducibility entry point, not a warning executor.  It verifies
    that all component sidecars use the exact same protocol fingerprint and
    retain ``formal_warning_output=false`` before writing the bundle manifest.
    """

    protocol_file = Path(protocol_path).resolve()
    protocol = load_protocol(protocol_file)
    _require_draft_ootang_protocol(protocol)
    protocol_sha256 = protocol_content_sha256(protocol)
    kinematics_file = Path(kinematics_path).resolve()
    predictions_file = Path(predictions_path).resolve()
    _require_ootang_station_inputs(
        kinematics_path=kinematics_file,
        predictions_path=predictions_file,
    )
    target_dir = Path(output_dir).resolve()
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{target_dir.name}.draft-evidence-",
        dir=target_dir.parent,
    ) as directory:
        staging_dir = Path(directory)
        staged_components = _write_staged_components(
            staging_dir=staging_dir,
            target_dir=target_dir,
            kinematics_path=kinematics_file,
            predictions_path=predictions_file,
            protocol_path=protocol_file,
        )
        _retarget_staged_component_manifests(staged_components)
        stable_segment_candidates_path = next(
            component.target_output_path
            for component in staged_components
            if component.name == "stable_segment_candidates"
        )
        components = [
            _component_record(
                component=component,
                protocol=protocol,
                protocol_sha256=protocol_sha256,
                predictions_path=predictions_file,
                kinematics_path=kinematics_file,
                stable_segment_candidates_path=stable_segment_candidates_path,
            )
            for component in staged_components
        ]
        staged_bundle_manifest_path = staging_dir / BUNDLE_MANIFEST_FILENAME
        staged_bundle_manifest_path.write_text(
            json.dumps(
                _bundle_manifest(
                    protocol=protocol,
                    protocol_sha256=protocol_sha256,
                    kinematics_path=kinematics_file,
                    predictions_path=predictions_file,
                    components=components,
                ),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        _promote_staged_files(
            tuple(
                _FileReplacement(component.output_path, component.target_output_path)
                for component in staged_components
            )
            + tuple(
                _FileReplacement(component.manifest_path, component.target_manifest_path)
                for component in staged_components
            )
            + (
                _FileReplacement(
                    staged_bundle_manifest_path,
                    target_dir / BUNDLE_MANIFEST_FILENAME,
                ),
            )
        )
    manifest_path = target_dir / BUNDLE_MANIFEST_FILENAME
    return DraftEvidenceBundleArtifacts(
        manifest_path=manifest_path,
        component_manifest_paths=tuple(
            component.target_manifest_path for component in staged_components
        ),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild the Ootang-only draft evidence bundle without formal warnings."
        )
    )
    parser.add_argument("--kinematics", type=Path, default=DEFAULT_KINEMATICS_PATH)
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL_PATH)
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    artifacts = write_draft_warning_evidence_bundle(
        kinematics_path=args.kinematics,
        predictions_path=args.predictions,
        output_dir=args.output_dir,
        protocol_path=args.protocol,
    )
    print(
        "wrote non-formal Ootang draft-evidence bundle: "
        f"{artifacts.manifest_path}"
    )


if __name__ == "__main__":
    main()


__all__ = [
    "BUNDLE_ARTIFACT_KIND",
    "BUNDLE_MANIFEST_FILENAME",
    "BUNDLE_STATUS",
    "DEFAULT_KINEMATICS_PATH",
    "DEFAULT_OUTPUT_DIR",
    "DEFAULT_PREDICTIONS_PATH",
    "DraftEvidenceBundleArtifacts",
    "DraftEvidenceBundleIntegrityError",
    "DraftEvidenceBundleProtocolError",
    "RETIRED_ARTIFACTS_EXCLUDED",
    "OOTANG_STATIONS",
    "write_draft_warning_evidence_bundle",
]
