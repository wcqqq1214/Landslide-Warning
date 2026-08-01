"""Run a reproducible, non-formal four-indicator Ootang implementation profile.

This module exists so the Ootang case can be run end-to-end for supervisor
review without silently converting the unresolved formal protocol into a
claimed warning result.  It uses a separate, versioned operational profile:
the source-referenced interval mapping is retained, while the missing V0,
blue-band, delta-V, and site-combination conventions are explicit,
project-specific, and replaceable.

The profile intentionally remains distinct from :mod:`warning.formal_warning`.
Every output carries ``formal_warning_output=false`` and records both the base
draft protocol and the operational-profile fingerprints.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# Permit direct execution with ``uv run python code/warning/...py`` as well as
# package imports from the test suite.
CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning.draft_evidence import (
    OOTANG_STATIONS,
    FileReplacement,
    promote_staged_files,
    write_draft_warning_evidence_bundle,
)
from warning.interval_state import classify_observed_interval_states
from warning.levels import WARNING_LEVELS, WarningLevel
from warning.operational_v2_fusion import (
    SpatialSiteFusionResult,
    StationEvidenceResult,
    fuse_site_spatial_blocks,
    fuse_station_evidence_families,
)
from warning.operational_v3_fusion import (
    SpatialSiteFusionV3Result,
    fuse_site_spatial_blocks_v3,
)
from warning.protocol import (
    load_protocol,
    protocol_content_sha256,
    unresolved_item_ids,
)
from warning.rule_fusion import (
    fuse_station_indicators_with_minimum_support,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE_PATH = ROOT / "config" / "ootang_operational_run.v1.draft.json"
DEFAULT_KINEMATICS_PATH = ROOT / "data" / "ootang_kinematics_long.csv"
DEFAULT_PREDICTIONS_PATH = ROOT / "figures" / "convlstm" / "forecast_predictions.csv"
DEFAULT_FORECAST_MANIFEST_PATH = (
    ROOT / "figures" / "convlstm" / "forecast_run_manifest.json"
)
DEFAULT_OUTPUT_DIR = ROOT / "figures" / "warning_operational_draft"
DEFAULT_V2_OUTPUT_DIR = ROOT / "figures" / "warning_operational_draft_v2"
DEFAULT_V3_OUTPUT_DIR = ROOT / "figures" / "warning_operational_draft_v3"
DEFAULT_EVIDENCE_DIR = ROOT / "figures" / "warning_draft"
STATION_TIMELINE_FILENAME = "ootang_operational_station_timeline.csv"
SITE_TIMELINE_FILENAME = "ootang_operational_site_timeline.csv"
THRESHOLDS_FILENAME = "ootang_operational_thresholds.csv"
MANIFEST_FILENAME = "ootang_operational_run_manifest.json"
ARTIFACT_KIND = "ootang_operational_four_indicator_run"
ARTIFACT_STATUS = "operational_draft_not_formal"
FORMAL_WARNING_ENTRY = "code/warning/formal_warning.py"
_VALID_INPUT_STATUSES = {"valid", "warmup", "invalid", "not_applicable"}
_LEVEL_LABELS = tuple(level.color for level in WARNING_LEVELS)
_DELTA_V_LABELS = ("negative", "near_zero", "positive")
_V1_STATION_FUSION = "warning.rule_fusion.fuse_station_indicators_with_minimum_support"
_V2_STATION_FUSION = "warning.operational_v2_fusion.fuse_station_evidence_families"
_V1_SITE_FUSION = "highest_non_green_at_or_above_level_with_configured_station_support"
_V2_SITE_FUSION = "warning.operational_v2_fusion.fuse_site_spatial_blocks"
_V2_PROFILE_ID = "ootang-operational-spatial-v2"
_V3_SITE_FUSION = "warning.operational_v3_fusion.fuse_site_spatial_blocks_v3"
_V3_PROFILE_ID = "ootang-operational-spatial-v3"
_REQUIRED_PREDICTION_COLUMNS = (
    "date",
    "station",
    "split",
    "actual",
    "p10",
    "p50",
    "p90",
)
_REQUIRED_KINEMATICS_COLUMNS = (
    "case",
    "date",
    "station",
    "dt_days",
    "time_status",
    "velocity",
    "velocity_status",
    "delta_v",
    "delta_v_status",
)
_REQUIRED_CANDIDATE_COLUMNS = (
    "protocol_id",
    "protocol_version",
    "protocol_status",
    "protocol_content_sha256",
    "candidate_status",
    "candidate_method_id",
    "candidate_method_role",
    "source_split",
    "station",
    "selection_status",
    "segment_start_date",
    "segment_end_date",
    "V",
    "sigma",
    "V0",
)


class OperationalRunProfileError(ValueError):
    """Raised when the replaceable implementation profile is malformed."""


class OperationalRunInputError(ValueError):
    """Raised when Ootang inputs cannot support the requested implementation run."""


@dataclass(frozen=True)
class OperationalRunArtifacts:
    """Paths emitted by one complete non-formal Ootang implementation run."""

    station_timeline_path: Path
    site_timeline_path: Path
    thresholds_path: Path
    manifest_path: Path
    evidence_manifest_path: Path


@dataclass(frozen=True)
class _LoadedProfile:
    profile: dict[str, Any]
    profile_path: Path
    base_protocol: dict[str, Any]
    base_protocol_path: Path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_path(path: Path, *, repository_relative: bool) -> str:
    resolved = path.resolve()
    if repository_relative:
        try:
            return resolved.relative_to(ROOT).as_posix()
        except ValueError:
            pass
    return str(resolved)


def _canonical_json_sha256(payload: dict[str, Any]) -> str:
    try:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise OperationalRunProfileError(
            "Operational profile must contain only finite JSON values."
        ) from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _read_json_object(path: Path, *, name: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise OperationalRunProfileError(f"{name} does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise OperationalRunProfileError(f"{name} is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise OperationalRunProfileError(f"{name} must be a JSON object: {path}")
    return payload


def _require_finite_number(value: Any, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OperationalRunProfileError(f"{name} must be a finite number.")
    numeric = float(value)
    if not np.isfinite(numeric):
        raise OperationalRunProfileError(f"{name} must be a finite number.")
    return numeric


def _require_integer_in_range(
    value: Any,
    *,
    name: str,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise OperationalRunProfileError(
            f"{name} must be an integer from {minimum} to {maximum}."
        )
    if not minimum <= value <= maximum:
        raise OperationalRunProfileError(
            f"{name} must be an integer from {minimum} to {maximum}."
        )
    return value


def _require_boundary_spec(
    value: Any,
    *,
    name: str,
    allowed_kinds: set[str],
) -> None:
    if not isinstance(value, dict) or not isinstance(value.get("kind"), str):
        raise OperationalRunProfileError(f"{name} must be a boundary object with kind.")
    kind = value["kind"]
    if kind not in allowed_kinds:
        raise OperationalRunProfileError(f"{name} has unsupported boundary kind: {kind}.")
    if kind == "v0_plus_sigma":
        _require_finite_number(value.get("sigma_multiplier"), name=f"{name}.sigma_multiplier")
    elif kind == "v0_multiple":
        multiple = _require_finite_number(value.get("multiple"), name=f"{name}.multiple")
        if multiple <= 0:
            raise OperationalRunProfileError(f"{name}.multiple must be positive.")
    elif kind == "absolute_degrees":
        _require_finite_number(value.get("value"), name=f"{name}.value")


def _require_range_mapping(
    mapping: Any,
    *,
    name: str,
    labels: tuple[str, ...],
    allowed_kinds: set[str],
) -> None:
    if not isinstance(mapping, list) or len(mapping) != len(labels):
        raise OperationalRunProfileError(
            f"{name} must be an ordered list with labels: {', '.join(labels)}."
        )
    for index, (entry, label) in enumerate(zip(mapping, labels)):
        if not isinstance(entry, dict) or entry.get("label") != label:
            raise OperationalRunProfileError(
                f"{name}[{index}] must declare label={label}."
            )
        has_lower = "lower" in entry
        has_upper = "upper" in entry
        if has_lower != (index > 0) or has_upper != (index < len(labels) - 1):
            raise OperationalRunProfileError(
                f"{name}[{index}] must define only the contiguous bounds for {label}."
            )
        if has_lower:
            _require_boundary_spec(
                entry["lower"],
                name=f"{name}[{index}].lower",
                allowed_kinds=allowed_kinds,
            )
            if not isinstance(entry.get("lower_inclusive"), bool):
                raise OperationalRunProfileError(
                    f"{name}[{index}].lower_inclusive must be boolean."
                )
        if has_upper:
            _require_boundary_spec(
                entry["upper"],
                name=f"{name}[{index}].upper",
                allowed_kinds=allowed_kinds,
            )
            if not isinstance(entry.get("upper_inclusive"), bool):
                raise OperationalRunProfileError(
                    f"{name}[{index}].upper_inclusive must be boolean."
                )


def _require_v1_fusion_profile(
    station_fusion: dict[str, Any],
    site_fusion: dict[str, Any],
) -> None:
    if station_fusion.get("implementation") != _V1_STATION_FUSION:
        raise OperationalRunProfileError(
            "Operational profile must name a supported station-fusion implementation."
        )
    _require_integer_in_range(
        station_fusion.get("minimum_support"),
        name="station_fusion.minimum_support",
        minimum=1,
        maximum=4,
    )
    if station_fusion.get("required_input_policy") != "all_four_inputs_must_be_valid":
        raise OperationalRunProfileError(
            "Operational station fusion must preserve the all-input validity policy."
        )
    if station_fusion.get("positive_delta_v_support") != "at_each_non_green_level":
        raise OperationalRunProfileError(
            "Operational station fusion must preserve its positive delta-V support policy."
        )
    if station_fusion.get("isolated_signal_policy") != "uncorroborated":
        raise OperationalRunProfileError(
            "Operational station fusion must preserve isolated signals as uncorroborated."
        )

    if site_fusion.get("implementation") != _V1_SITE_FUSION:
        raise OperationalRunProfileError(
            "Operational profile must name a supported site-fusion implementation."
        )
    minimum_valid = _require_integer_in_range(
        site_fusion.get("minimum_valid_station_count"),
        name="site_fusion.minimum_valid_station_count",
        minimum=1,
        maximum=len(OOTANG_STATIONS),
    )
    _require_integer_in_range(
        site_fusion.get("minimum_supporting_stations"),
        name="site_fusion.minimum_supporting_stations",
        minimum=1,
        maximum=minimum_valid,
    )
    if site_fusion.get("all_valid_green_policy") != "green" or site_fusion.get(
        "isolated_elevation_policy"
    ) != "uncorroborated":
        raise OperationalRunProfileError(
            "Operational site fusion must preserve green and uncorroborated policies."
        )


def _require_spatial_blocks(value: Any) -> None:
    if not isinstance(value, dict) or tuple(value) != ("O1", "O2", "O3"):
        raise OperationalRunProfileError(
            "Spatial site fusion must declare ordered blocks O1, O2, O3."
        )
    expected = {
        "O1": ("MJ9", "MJ1", "MJ3"),
        "O2": ("ATU4", "ATU5", "ATU3"),
        "O3": ("ATU2", "ATU1"),
    }
    for name, stations in expected.items():
        if tuple(value.get(name, ())) != stations:
            raise OperationalRunProfileError(
                f"Spatial site fusion block {name} must match the reviewed Ootang layout."
            )


def _resolve_spatial_block_source(
    site_fusion: dict[str, Any],
    *,
    profile_path: Path,
) -> Path:
    """Verify the exact paper copy used for the Ootang block topology."""

    source = site_fusion.get("spatial_blocks_source")
    if not isinstance(source, dict):
        raise OperationalRunProfileError(
            "Spatial site fusion must record a spatial-block source object."
        )
    required_strings = (
        "citation",
        "doi",
        "source_file",
        "source_file_sha256",
        "figure",
        "section",
        "scope",
    )
    for name in required_strings:
        if not isinstance(source.get(name), str) or not source[name].strip():
            raise OperationalRunProfileError(
                f"Spatial-block source must provide non-blank {name}."
            )
    if source["doi"] != "10.1029/2025JH000592":
        raise OperationalRunProfileError(
            "Spatial-block source must identify Wang et al. (2025) by DOI."
        )
    if source.get("pdf_page") != 7 or source["figure"] != "Figure 4(a, d)":
        raise OperationalRunProfileError(
            "Spatial-block source must locate the topology at PDF page 7, Figure 4(a, d)."
        )
    source_path = (profile_path.parent / source["source_file"]).resolve()
    if not source_path.is_file():
        raise OperationalRunProfileError(
            "Spatial-block source file does not exist: " + str(source_path)
        )
    if _sha256_file(source_path) != source["source_file_sha256"]:
        raise OperationalRunProfileError(
            "Spatial-block source file fingerprint does not match the profile."
        )
    return source_path


def _require_evidence_family_station_fusion(
    station_fusion: dict[str, Any],
) -> None:
    if station_fusion.get("implementation") != _V2_STATION_FUSION:
        raise OperationalRunProfileError(
            "Operational profile must name the evidence-family station fusion."
        )
    if station_fusion.get("required_input_policy") != "all_four_inputs_must_be_valid":
        raise OperationalRunProfileError(
            "Evidence-family station fusion must preserve the all-input validity policy."
        )
    kinematic = station_fusion.get("kinematic_family")
    if not isinstance(kinematic, dict) or kinematic != {
        "members": ["velocity", "tangent_angle"],
        "combine": "max_level_one_evidence_family",
        "independence_policy": "not_independent_not_two_votes",
    }:
        raise OperationalRunProfileError(
            "Evidence-family station fusion must define velocity/tangent as one "
            "kinematic family."
        )
    if station_fusion.get("positive_delta_v_role") != (
        "acceleration_qualifier_only_no_ordinal_color_escalation"
    ):
        raise OperationalRunProfileError(
            "Evidence-family station fusion must keep positive delta-V as a "
            "qualifier only."
        )
    if station_fusion.get("single_family_elevation_policy") != (
        "visible_assessable_candidate_not_missing"
    ):
        raise OperationalRunProfileError(
            "Evidence-family station fusion must retain single-family candidates "
            "visibly."
        )


def _require_v2_fusion_profile(
    station_fusion: dict[str, Any],
    site_fusion: dict[str, Any],
) -> None:
    _require_evidence_family_station_fusion(station_fusion)

    if site_fusion.get("implementation") != _V2_SITE_FUSION:
        raise OperationalRunProfileError(
            "Operational profile must name the v2 spatial site fusion."
        )
    _require_spatial_blocks(site_fusion.get("spatial_blocks"))
    if site_fusion.get("cross_block_confirmation_minimum_level") != "yellow":
        raise OperationalRunProfileError(
            "v2 spatial confirmation must begin at yellow, not blue."
        )
    if site_fusion.get("blue_site_policy") != (
        "visible_only_when_highest_candidate_is_blue"
    ):
        raise OperationalRunProfileError(
            "v2 site fusion must retain the reviewed blue-candidate policy."
        )
    if site_fusion.get("higher_unconfirmed_candidate_policy") != (
        "not_downgraded_to_blue"
    ):
        raise OperationalRunProfileError(
            "v2 site fusion must not downgrade an unconfirmed yellow-red candidate to blue."
        )
    minimum_assessable = _require_integer_in_range(
        site_fusion.get("minimum_assessable_station_count"),
        name="site_fusion.minimum_assessable_station_count",
        minimum=1,
        maximum=len(OOTANG_STATIONS),
    )
    if site_fusion.get("require_all_blocks_for_green") is not True:
        raise OperationalRunProfileError(
            "v2 whole-body green requires assessable coverage of every block."
        )
    _require_integer_in_range(
        site_fusion.get("minimum_supporting_stations"),
        name="site_fusion.minimum_supporting_stations",
        minimum=1,
        maximum=minimum_assessable,
    )
    _require_integer_in_range(
        site_fusion.get("minimum_supporting_blocks"),
        name="site_fusion.minimum_supporting_blocks",
        minimum=1,
        maximum=3,
    )
    if site_fusion.get("candidate_not_confirmed_policy") != (
        "visible_candidate_not_site_confirmed"
    ):
        raise OperationalRunProfileError(
            "v2 site fusion must retain unconfirmed candidates visibly."
        )


def _require_v3_fusion_profile(
    station_fusion: dict[str, Any],
    site_fusion: dict[str, Any],
) -> None:
    _require_evidence_family_station_fusion(station_fusion)
    if site_fusion.get("implementation") != _V3_SITE_FUSION:
        raise OperationalRunProfileError(
            "Operational profile must name the v3 dual-axis spatial site fusion."
        )
    _require_spatial_blocks(site_fusion.get("spatial_blocks"))
    minimum_assessable = _require_integer_in_range(
        site_fusion.get("minimum_assessable_station_count"),
        name="site_fusion.minimum_assessable_station_count",
        minimum=1,
        maximum=len(OOTANG_STATIONS),
    )
    if minimum_assessable != 3:
        raise OperationalRunProfileError(
            "v3 global coverage requires exactly 3 assessable stations."
        )
    if site_fusion.get("require_all_blocks_for_any_site_level") is not True:
        raise OperationalRunProfileError(
            "v3 requires every spatial block before any site colour can be issued."
        )
    minimum_supporting_stations = _require_integer_in_range(
        site_fusion.get("minimum_supporting_stations"),
        name="site_fusion.minimum_supporting_stations",
        minimum=1,
        maximum=minimum_assessable,
    )
    minimum_supporting_blocks = _require_integer_in_range(
        site_fusion.get("minimum_supporting_blocks"),
        name="site_fusion.minimum_supporting_blocks",
        minimum=1,
        maximum=3,
    )
    if minimum_supporting_stations != 2 or minimum_supporting_blocks != 2:
        raise OperationalRunProfileError(
            "v3 spatial confirmation requires exactly 2 stations and exactly 2 blocks."
        )
    if site_fusion.get("higher_confirmation_minimum_level") != "yellow":
        raise OperationalRunProfileError(
            "v3 higher-level spatial confirmation must begin at yellow."
        )
    if site_fusion.get("blue_site_policy") != (
        "two_station_two_block_or_green_with_localized_blue_attention"
    ):
        raise OperationalRunProfileError(
            "v3 blue policy must separate site blue from localized blue attention."
        )
    if site_fusion.get("higher_unconfirmed_candidate_policy") != (
        "visible_candidate_not_site_confirmed_not_downgraded"
    ):
        raise OperationalRunProfileError(
            "v3 must retain unconfirmed yellow-red candidates without downgrading."
        )
    if site_fusion.get("candidate_not_confirmed_policy") != (
        "visible_candidate_not_site_confirmed"
    ):
        raise OperationalRunProfileError(
            "v3 site fusion must retain unconfirmed candidates visibly."
        )
    if site_fusion.get("dual_axis_output") != {
        "site_axis": "site_confirmed_level",
        "local_axis": "local_max_candidate_level",
    }:
        raise OperationalRunProfileError(
            "v3 site fusion must declare its site-confirmed and local-candidate axes."
        )


def _require_profile_fields(profile: dict[str, Any]) -> None:
    required = {
        "profile_id",
        "profile_version",
        "status",
        "case",
        "formal_warning_output",
        "base_draft_protocol",
        "ootang_stations",
        "parameter_fit_splits",
        "result_splits",
        "velocity_baseline",
        "delta_v",
        "tangent_angle",
        "station_fusion",
        "site_fusion",
        "not_claimed",
    }
    missing = sorted(required.difference(profile))
    if missing:
        raise OperationalRunProfileError(
            "Operational profile is missing required keys: " + ", ".join(missing)
        )
    if profile["status"] != "operational_draft":
        raise OperationalRunProfileError(
            "Operational profile status must be operational_draft."
        )
    if profile["case"] != "ootang":
        raise OperationalRunProfileError("Operational profile is restricted to Ootang.")
    if profile["formal_warning_output"] is not False:
        raise OperationalRunProfileError(
            "Operational profile must explicitly prohibit formal warning output."
        )
    if tuple(profile["ootang_stations"]) != OOTANG_STATIONS:
        raise OperationalRunProfileError(
            "Operational profile station order must match the eight Ootang stations."
        )
    if profile["parameter_fit_splits"] != ["fit"]:
        raise OperationalRunProfileError(
            "Operational parameters must be derived only from split=fit."
        )
    if profile["result_splits"] != ["calibration", "test"]:
        raise OperationalRunProfileError(
            "Operational results must be limited to calibration and test splits."
        )
    velocity = profile["velocity_baseline"]
    if not isinstance(velocity, dict) or velocity.get("source") != (
        "raw_velocity_kmeans_comparator"
    ):
        raise OperationalRunProfileError(
            "Operational profile must name the KMeans comparator baseline explicitly."
        )
    blue_band = velocity.get("blue_band")
    if not isinstance(blue_band, dict) or blue_band.get("method") != (
        "configured_comparator_v0_plus_scaled_sigma"
    ):
        raise OperationalRunProfileError(
            "Operational velocity blue band must declare the configured V0/sigma method."
        )
    _require_boundary_spec(
        blue_band.get("lower"),
        name="velocity_baseline.blue_band.lower",
        allowed_kinds={"v0_plus_sigma"},
    )
    _require_boundary_spec(
        blue_band.get("upper"),
        name="velocity_baseline.blue_band.upper",
        allowed_kinds={"v0_plus_sigma"},
    )
    _require_range_mapping(
        velocity.get("mapping"),
        name="velocity_baseline.mapping",
        labels=_LEVEL_LABELS,
        allowed_kinds={"blue_lower", "blue_upper", "v0_multiple"},
    )

    delta_v = profile["delta_v"]
    if not isinstance(delta_v, dict):
        raise OperationalRunProfileError("Operational delta-V configuration must be an object.")
    tolerance = delta_v.get("tolerance")
    if not isinstance(tolerance, dict) or tolerance.get("method") != (
        "1.4826_times_median_absolute_deviation_about_segment_delta_v_median"
    ):
        raise OperationalRunProfileError(
            "Operational profile must declare the selected-segment MAD delta-V rule."
        )
    if _require_finite_number(tolerance.get("scale"), name="delta_v.tolerance.scale") <= 0:
        raise OperationalRunProfileError("Operational delta-V scale must be positive.")
    _require_finite_number(tolerance.get("state_center"), name="delta_v.tolerance.state_center")
    if _require_finite_number(
        tolerance.get("minimum_absolute_tolerance"),
        name="delta_v.tolerance.minimum_absolute_tolerance",
    ) <= 0:
        raise OperationalRunProfileError(
            "Operational delta-V minimum tolerance must be positive."
        )
    _require_range_mapping(
        delta_v.get("mapping"),
        name="delta_v.mapping",
        labels=_DELTA_V_LABELS,
        allowed_kinds={"state_center_minus_tau", "state_center_plus_tau"},
    )

    tangent = profile["tangent_angle"]
    if not isinstance(tangent, dict) or tangent.get("formula") != {
        "kind": "atan_velocity_over_v0_degrees"
    }:
        raise OperationalRunProfileError(
            "Operational tangent-angle formula must be atan(velocity/V0) in degrees."
        )
    tangent_blue = tangent.get("blue_band")
    if not isinstance(tangent_blue, dict):
        raise OperationalRunProfileError("Operational tangent-angle blue band must be an object.")
    _require_boundary_spec(
        tangent_blue.get("lower"),
        name="tangent_angle.blue_band.lower",
        allowed_kinds={"angle_from_velocity_blue_lower", "absolute_degrees"},
    )
    _require_boundary_spec(
        tangent_blue.get("upper"),
        name="tangent_angle.blue_band.upper",
        allowed_kinds={"angle_from_velocity_blue_upper", "absolute_degrees"},
    )
    _require_range_mapping(
        tangent.get("mapping"),
        name="tangent_angle.mapping",
        labels=_LEVEL_LABELS,
        allowed_kinds={"blue_lower", "blue_upper", "absolute_degrees"},
    )
    nonregular = tangent.get("nonregular_handling")
    if not isinstance(nonregular, dict) or nonregular.get("action") != "not_applicable":
        raise OperationalRunProfileError(
            "Operational tangent-angle nonregular policy must preserve not_applicable."
        )
    if nonregular.get("required_time_status") != "valid":
        raise OperationalRunProfileError(
            "Operational tangent-angle policy must require valid time status."
        )
    if _require_finite_number(
        nonregular.get("required_dt_days"),
        name="tangent_angle.nonregular_handling.required_dt_days",
    ) <= 0:
        raise OperationalRunProfileError(
            "Operational tangent-angle required dt_days must be positive."
        )

    station_fusion = profile["station_fusion"]
    site_fusion = profile["site_fusion"]
    if not isinstance(station_fusion, dict) or not isinstance(site_fusion, dict):
        raise OperationalRunProfileError(
            "Operational station_fusion and site_fusion must be JSON objects."
        )
    if station_fusion.get("implementation") == _V1_STATION_FUSION:
        _require_v1_fusion_profile(station_fusion, site_fusion)
    elif (
        station_fusion.get("implementation") == _V2_STATION_FUSION
        and site_fusion.get("implementation") == _V2_SITE_FUSION
    ):
        _require_v2_fusion_profile(station_fusion, site_fusion)
    elif (
        station_fusion.get("implementation") == _V2_STATION_FUSION
        and site_fusion.get("implementation") == _V3_SITE_FUSION
    ):
        _require_v3_fusion_profile(station_fusion, site_fusion)
    elif station_fusion.get("implementation") == _V2_STATION_FUSION:
        raise OperationalRunProfileError(
            "Operational profile must name a supported site-fusion implementation."
        )
    else:
        raise OperationalRunProfileError(
            "Operational profile must name a supported station-fusion implementation."
        )
    if not isinstance(profile["not_claimed"], list) or "formal_warning_output" not in profile[
        "not_claimed"
    ]:
        raise OperationalRunProfileError(
            "Operational profile must explicitly retain the non-formal boundary."
        )


def _load_operational_profile(path: str | Path) -> _LoadedProfile:
    profile_path = Path(path).resolve()
    profile = _read_json_object(profile_path, name="Operational profile")
    _require_profile_fields(profile)
    site_implementation = profile["site_fusion"]["implementation"]
    if site_implementation in {_V2_SITE_FUSION, _V3_SITE_FUSION}:
        expected_profile_id = (
            _V2_PROFILE_ID
            if site_implementation == _V2_SITE_FUSION
            else _V3_PROFILE_ID
        )
        if profile["profile_id"] != expected_profile_id:
            raise OperationalRunProfileError(
                "The spatial site-fusion implementation requires its separately "
                "versioned profile id."
            )
        _resolve_spatial_block_source(
            profile["site_fusion"],
            profile_path=profile_path,
        )

    base_spec = profile["base_draft_protocol"]
    if not isinstance(base_spec, dict) or not isinstance(base_spec.get("path"), str):
        raise OperationalRunProfileError(
            "Operational profile must name its base draft protocol path."
        )
    base_path = (profile_path.parent / base_spec["path"]).resolve()
    base_protocol = load_protocol(base_path)
    expected = {
        "protocol_id": base_spec.get("expected_protocol_id"),
        "protocol_version": base_spec.get("expected_protocol_version"),
        "case": base_spec.get("expected_case"),
        "status": base_spec.get("expected_status"),
    }
    for field, expected_value in expected.items():
        if base_protocol.get(field) != expected_value:
            raise OperationalRunProfileError(
                f"Base draft protocol {field} does not match the operational profile."
            )
    expected_hash = base_spec.get("expected_protocol_content_sha256")
    if protocol_content_sha256(base_protocol) != expected_hash:
        raise OperationalRunProfileError(
            "Base draft protocol content fingerprint does not match the operational profile."
        )
    expected_unresolved = base_spec.get("expected_unresolved_item_ids")
    if not isinstance(expected_unresolved, list) or tuple(unresolved_item_ids(base_protocol)) != tuple(
        expected_unresolved
    ):
        raise OperationalRunProfileError(
            "Base draft protocol unresolved items do not match the operational profile."
        )
    return _LoadedProfile(
        profile=profile,
        profile_path=profile_path,
        base_protocol=base_protocol,
        base_protocol_path=base_path,
    )


def _default_output_dir_for_profile(loaded: _LoadedProfile) -> Path:
    """Return the version-owned output directory for a supported profile."""

    site_implementation = loaded.profile["site_fusion"]["implementation"]
    if site_implementation == _V2_SITE_FUSION:
        return DEFAULT_V2_OUTPUT_DIR
    if site_implementation == _V3_SITE_FUSION:
        return DEFAULT_V3_OUTPUT_DIR
    return DEFAULT_OUTPUT_DIR


def _resolve_operational_output_dir(
    loaded: _LoadedProfile,
    output_dir: str | Path | None,
) -> Path:
    """Select a safe output destination without cross-version overwrites."""

    expected = _default_output_dir_for_profile(loaded).resolve()
    target = expected if output_dir is None else Path(output_dir).resolve()
    reserved = {
        "v1": DEFAULT_OUTPUT_DIR.resolve(),
        "v2": DEFAULT_V2_OUTPUT_DIR.resolve(),
        "v3": DEFAULT_V3_OUTPUT_DIR.resolve(),
    }
    generation = next(
        name for name, directory in reserved.items() if directory == expected
    )
    conflicting = next(
        (
            name
            for name, directory in reserved.items()
            if directory == target and directory != expected
        ),
        None,
    )
    if conflicting is not None:
        raise OperationalRunProfileError(
            f"The {generation} profile must not write into the preserved "
            f"{conflicting} operational directory."
        )
    return target


def _spatial_block_source_manifest(
    loaded: _LoadedProfile,
) -> dict[str, Any] | None:
    if loaded.profile["site_fusion"]["implementation"] not in {
        _V2_SITE_FUSION,
        _V3_SITE_FUSION,
    }:
        return None
    source = loaded.profile["site_fusion"]["spatial_blocks_source"]
    source_path = _resolve_spatial_block_source(
        loaded.profile["site_fusion"],
        profile_path=loaded.profile_path,
    )
    return {
        "citation": source["citation"],
        "doi": source["doi"],
        "path": str(source_path),
        "sha256": _sha256_file(source_path),
        "pdf_page": source["pdf_page"],
        "figure": source["figure"],
        "section": source["section"],
        "scope": source["scope"],
    }


def _read_csv_columns(path: Path, columns: tuple[str, ...], *, source_name: str) -> pd.DataFrame:
    try:
        return pd.read_csv(path, usecols=list(columns))
    except FileNotFoundError as exc:
        raise OperationalRunInputError(f"{source_name} does not exist: {path}") from exc
    except ValueError as exc:
        required = ", ".join(columns)
        raise OperationalRunInputError(
            f"{source_name} must contain: {required}"
        ) from exc


def _normalise_coordinates(frame: pd.DataFrame, *, source_name: str) -> pd.DataFrame:
    normalized = frame.copy()
    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
    normalized["station"] = normalized["station"].astype("string").str.strip()
    if normalized["date"].isna().any():
        raise OperationalRunInputError(f"{source_name} contains invalid dates.")
    if normalized["station"].isna().any() or normalized["station"].eq("").any():
        raise OperationalRunInputError(f"{source_name} contains blank station identifiers.")
    duplicate = normalized.duplicated(["station", "date"], keep=False)
    if duplicate.any():
        raise OperationalRunInputError(
            f"{source_name} contains duplicate station/date rows."
        )
    stations = set(normalized["station"])
    if stations != set(OOTANG_STATIONS):
        raise OperationalRunInputError(
            f"{source_name} stations must be exactly the Ootang set; found "
            f"{sorted(stations)}."
        )
    return normalized


def _load_predictions(path: Path, *, result_splits: tuple[str, ...]) -> pd.DataFrame:
    predictions = _read_csv_columns(
        path,
        _REQUIRED_PREDICTION_COLUMNS,
        source_name="Prediction input",
    )
    predictions = _normalise_coordinates(predictions, source_name="Prediction input")
    predictions["split"] = predictions["split"].astype("string").str.strip()
    present_splits = set(predictions["split"])
    required_splits = {"fit", *result_splits}
    if not required_splits.issubset(present_splits):
        raise OperationalRunInputError(
            "Prediction input does not contain the required fit/calibration/test splits."
        )
    result = predictions.loc[predictions["split"].isin(result_splits)].copy()
    expected_rows_per_station = result.groupby("station", sort=True).size()
    if expected_rows_per_station.nunique() != 1:
        raise OperationalRunInputError(
            "Every Ootang station must have the same operational prediction dates."
        )
    return result.sort_values(["date", "station"], kind="stable").reset_index(drop=True)


def _load_kinematics(path: Path) -> pd.DataFrame:
    kinematics = _read_csv_columns(
        path,
        _REQUIRED_KINEMATICS_COLUMNS,
        source_name="Kinematics input",
    )
    kinematics = _normalise_coordinates(kinematics, source_name="Kinematics input")
    case = kinematics["case"].astype("string").str.strip()
    if not case.eq("ootang").all():
        raise OperationalRunInputError("Kinematics input must contain only case=ootang.")
    for column in ("dt_days", "velocity", "delta_v"):
        kinematics[column] = pd.to_numeric(kinematics[column], errors="coerce")
    for column in ("time_status", "velocity_status", "delta_v_status"):
        kinematics[column] = kinematics[column].astype("string").str.strip()
    return kinematics.sort_values(["date", "station"], kind="stable").reset_index(
        drop=True
    )


def _load_candidate_baselines(
    path: Path,
    *,
    loaded: _LoadedProfile,
) -> pd.DataFrame:
    candidates = _read_csv_columns(
        path,
        _REQUIRED_CANDIDATE_COLUMNS,
        source_name="Operational stable-segment candidate",
    )
    candidates["station"] = candidates["station"].astype("string").str.strip()
    if candidates["station"].duplicated().any() or set(candidates["station"]) != set(
        OOTANG_STATIONS
    ):
        raise OperationalRunInputError(
            "Operational stable-segment candidate must contain one row per Ootang station."
        )
    profile_baseline = loaded.profile["velocity_baseline"]
    base_hash = protocol_content_sha256(loaded.base_protocol)
    expected_values = {
        "protocol_id": loaded.base_protocol["protocol_id"],
        "protocol_version": loaded.base_protocol["protocol_version"],
        "protocol_status": loaded.base_protocol["status"],
        "protocol_content_sha256": base_hash,
        "candidate_status": profile_baseline["candidate_status"],
        "candidate_method_id": profile_baseline["candidate_method_id"],
        "candidate_method_role": profile_baseline["candidate_method_role"],
        "source_split": "fit",
        "selection_status": profile_baseline["selection_status"],
    }
    for column, expected in expected_values.items():
        if not candidates[column].eq(expected).all():
            raise OperationalRunInputError(
                f"Operational stable-segment candidate has mismatched {column}."
            )
    for column in ("V", "sigma", "V0"):
        candidates[column] = pd.to_numeric(candidates[column], errors="coerce")
    numeric = candidates.loc[:, ["V", "sigma", "V0"]]
    if not np.isfinite(numeric.to_numpy(dtype=float)).all():
        raise OperationalRunInputError(
            "Operational stable-segment candidate has non-finite V, sigma, or V0."
        )
    if (candidates["V0"] <= 0).any() or (candidates["sigma"] < 0).any():
        raise OperationalRunInputError(
            "Operational stable-segment candidate has an invalid V0 or sigma."
        )
    for column in ("segment_start_date", "segment_end_date"):
        candidates[column] = pd.to_datetime(candidates[column], errors="coerce")
    if candidates[["segment_start_date", "segment_end_date"]].isna().any().any() or (
        candidates["segment_start_date"] > candidates["segment_end_date"]
    ).any():
        raise OperationalRunInputError(
            "Operational stable-segment candidate has invalid segment dates."
        )
    return candidates.sort_values("station", kind="stable").reset_index(drop=True)


def _resolve_boundary(spec: dict[str, Any], *, values: dict[str, float]) -> float:
    """Resolve one profile-declared boundary from an audited station baseline."""

    kind = spec["kind"]
    if kind == "v0_plus_sigma":
        value = values["v0"] + float(spec["sigma_multiplier"]) * values["sigma"]
    elif kind == "v0_multiple":
        value = float(spec["multiple"]) * values["v0"]
    elif kind in {"blue_lower", "blue_upper"}:
        value = values[kind]
    elif kind == "angle_from_velocity_blue_lower":
        value = float(
            np.degrees(
                np.arctan(values["velocity_blue_lower"] / values["v0"])
            )
        )
    elif kind == "angle_from_velocity_blue_upper":
        value = float(
            np.degrees(
                np.arctan(values["velocity_blue_upper"] / values["v0"])
            )
        )
    elif kind == "absolute_degrees":
        value = float(spec["value"])
    elif kind == "state_center_minus_tau":
        value = values["state_center"] - values["tau"]
    elif kind == "state_center_plus_tau":
        value = values["state_center"] + values["tau"]
    else:  # Profile validation should make this unreachable.
        raise OperationalRunProfileError(f"Unsupported configured boundary kind: {kind}.")
    if not np.isfinite(value):
        raise OperationalRunProfileError(f"Configured boundary {kind} resolved to non-finite.")
    return float(value)


def _classify_configured_range(
    *,
    value: float,
    mapping: list[dict[str, Any]],
    boundary_values: dict[str, float],
    mapping_name: str,
) -> str:
    """Classify a finite value from a contiguous, profile-declared range table."""

    resolved: list[tuple[dict[str, Any], float | None, float | None]] = []
    for entry in mapping:
        lower = (
            _resolve_boundary(entry["lower"], values=boundary_values)
            if "lower" in entry
            else None
        )
        upper = (
            _resolve_boundary(entry["upper"], values=boundary_values)
            if "upper" in entry
            else None
        )
        if lower is not None and upper is not None and not lower < upper:
            raise OperationalRunProfileError(
                f"{mapping_name} has a non-increasing configured range."
            )
        resolved.append((entry, lower, upper))

    for index in range(1, len(resolved)):
        previous, _, previous_upper = resolved[index - 1]
        current, current_lower, _ = resolved[index]
        if previous_upper is None or current_lower is None or not np.isclose(
            previous_upper,
            current_lower,
            rtol=1e-12,
            atol=1e-12,
        ):
            raise OperationalRunProfileError(
                f"{mapping_name} must have contiguous configured ranges."
            )
        if previous["upper_inclusive"] == current["lower_inclusive"]:
            raise OperationalRunProfileError(
                f"{mapping_name} must assign each shared boundary exactly once."
            )

    for entry, lower, upper in resolved:
        lower_matches = lower is None or (
            value >= lower if entry["lower_inclusive"] else value > lower
        )
        upper_matches = upper is None or (
            value <= upper if entry["upper_inclusive"] else value < upper
        )
        if lower_matches and upper_matches:
            return str(entry["label"])
    raise OperationalRunProfileError(
        f"{mapping_name} does not cover the configured value {value}."
    )


def _derive_thresholds(
    *,
    kinematics: pd.DataFrame,
    candidates: pd.DataFrame,
    profile: dict[str, Any],
) -> pd.DataFrame:
    """Derive all project-specific operating parameters from fit-only segments."""

    velocity_profile = profile["velocity_baseline"]
    velocity_blue_band = velocity_profile["blue_band"]
    tangent_profile = profile["tangent_angle"]
    tangent_blue_band = tangent_profile["blue_band"]
    tolerance_spec = profile["delta_v"]["tolerance"]
    scale = float(tolerance_spec["scale"])
    state_center = float(tolerance_spec["state_center"])
    tolerance_floor = float(tolerance_spec["minimum_absolute_tolerance"])
    records: list[dict[str, Any]] = []
    for candidate in candidates.itertuples(index=False):
        segment = kinematics.loc[
            kinematics["station"].eq(candidate.station)
            & kinematics["date"].ge(candidate.segment_start_date)
            & kinematics["date"].le(candidate.segment_end_date)
            & kinematics["delta_v_status"].eq("valid")
        ].copy()
        delta_v = pd.to_numeric(segment["delta_v"], errors="coerce")
        delta_v = delta_v.loc[np.isfinite(delta_v.to_numpy(dtype=float))]
        if delta_v.empty:
            raise OperationalRunInputError(
                f"No valid fit-segment delta-V values for station {candidate.station}."
            )
        median = float(delta_v.median())
        mad = float((delta_v - median).abs().median())
        tau = max(scale * mad, tolerance_floor)
        velocity_values = {
            "v0": float(candidate.V0),
            "sigma": float(candidate.sigma),
        }
        lower_velocity = _resolve_boundary(
            velocity_blue_band["lower"],
            values=velocity_values,
        )
        upper_velocity = _resolve_boundary(
            velocity_blue_band["upper"],
            values=velocity_values,
        )
        if not (0.0 <= lower_velocity < upper_velocity):
            raise OperationalRunInputError(
                f"Operational blue velocity band is invalid for {candidate.station}."
            )
        tangent_values = {
            "v0": float(candidate.V0),
            "velocity_blue_lower": lower_velocity,
            "velocity_blue_upper": upper_velocity,
        }
        lower_angle = _resolve_boundary(
            tangent_blue_band["lower"],
            values=tangent_values,
        )
        upper_angle = _resolve_boundary(
            tangent_blue_band["upper"],
            values=tangent_values,
        )
        if not (0.0 <= lower_angle < upper_angle):
            raise OperationalRunInputError(
                f"Operational blue tangent band is invalid for {candidate.station}."
            )
        _classify_configured_range(
            value=lower_velocity,
            mapping=velocity_profile["mapping"],
            boundary_values={
                **velocity_values,
                "blue_lower": lower_velocity,
                "blue_upper": upper_velocity,
            },
            mapping_name="velocity_baseline.mapping",
        )
        _classify_configured_range(
            value=lower_angle,
            mapping=tangent_profile["mapping"],
            boundary_values={"blue_lower": lower_angle, "blue_upper": upper_angle},
            mapping_name="tangent_angle.mapping",
        )
        _classify_configured_range(
            value=state_center,
            mapping=profile["delta_v"]["mapping"],
            boundary_values={"state_center": state_center, "tau": tau},
            mapping_name="delta_v.mapping",
        )
        records.append(
            {
                "station": candidate.station,
                "fit_segment_start_date": candidate.segment_start_date.strftime("%Y-%m-%d"),
                "fit_segment_end_date": candidate.segment_end_date.strftime("%Y-%m-%d"),
                "selected_segment_v_mm_per_day": float(candidate.V),
                "selected_segment_sigma_mm_per_day": float(candidate.sigma),
                "comparator_v0_mm_per_day": float(candidate.V0),
                "velocity_blue_lower_mm_per_day": lower_velocity,
                "velocity_blue_upper_mm_per_day": upper_velocity,
                "tangent_blue_lower_degree": lower_angle,
                "tangent_blue_upper_degree": upper_angle,
                "delta_v_fit_segment_n": len(delta_v),
                "delta_v_fit_segment_median_mm_per_day": median,
                "delta_v_fit_segment_mad_mm_per_day": mad,
                "delta_v_near_zero_tau_mm_per_day": tau,
                "delta_v_state_center_mm_per_day": state_center,
                "velocity_baseline_source": profile["velocity_baseline"]["source"],
                "delta_v_tolerance_method": tolerance_spec["method"],
            }
        )
    return pd.DataFrame(records).sort_values("station", kind="stable").reset_index(
        drop=True
    )


def _normalise_input_status(value: object) -> str:
    if isinstance(value, str) and value.strip() in _VALID_INPUT_STATUSES:
        return value.strip()
    return "invalid"


def _as_float_or_nan(value: object) -> float:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return float(numeric) if pd.notna(numeric) else float("nan")


def _level_record(level: WarningLevel, *, name: str) -> dict[str, Any]:
    return {f"{name}_level": int(level), f"{name}_color": level.color}


def _classify_velocity(
    *,
    velocity: float,
    status: str,
    lower_blue: float,
    upper_blue: float,
    v0: float,
    sigma: float,
    mapping: list[dict[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "velocity_indicator_status": status,
        "velocity_level": pd.NA,
        "velocity_color": pd.NA,
        "velocity_reason": pd.NA,
    }
    if status != "valid":
        result["velocity_reason"] = f"source_{status}"
        return result
    if not np.isfinite(velocity):
        result["velocity_indicator_status"] = "invalid"
        result["velocity_reason"] = "nonfinite_velocity"
        return result
    label = _classify_configured_range(
        value=velocity,
        mapping=mapping,
        boundary_values={
            "v0": v0,
            "sigma": sigma,
            "blue_lower": lower_blue,
            "blue_upper": upper_blue,
        },
        mapping_name="velocity_baseline.mapping",
    )
    level = WarningLevel[label.upper()]
    reason = f"configured_{label}_range"
    result.update(_level_record(level, name="velocity"))
    result["velocity_reason"] = reason
    return result


def _classify_tangent_angle(
    *,
    velocity: float,
    velocity_status: str,
    time_status: str,
    dt_days: float,
    v0: float,
    lower_blue: float,
    upper_blue: float,
    formula: dict[str, Any],
    mapping: list[dict[str, Any]],
    nonregular_handling: dict[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "tangent_angle_indicator_status": "invalid",
        "tangent_angle_degree": np.nan,
        "tangent_angle_level": pd.NA,
        "tangent_angle_color": pd.NA,
        "tangent_angle_reason": pd.NA,
    }
    if velocity_status != "valid":
        result["tangent_angle_indicator_status"] = velocity_status
        result["tangent_angle_reason"] = f"velocity_{velocity_status}"
        return result
    required_status = str(nonregular_handling["required_time_status"])
    required_dt_days = float(nonregular_handling["required_dt_days"])
    if (
        time_status != required_status
        or not np.isfinite(dt_days)
        or not np.isclose(dt_days, required_dt_days)
    ):
        result["tangent_angle_indicator_status"] = "not_applicable"
        result["tangent_angle_reason"] = "nonregular_time_input"
        return result
    if not np.isfinite(velocity):
        result["tangent_angle_reason"] = "nonfinite_velocity"
        return result
    if formula["kind"] != "atan_velocity_over_v0_degrees":
        raise OperationalRunProfileError("Unsupported operational tangent-angle formula.")
    angle = float(np.degrees(np.arctan(velocity / v0)))
    result["tangent_angle_degree"] = angle
    label = _classify_configured_range(
        value=angle,
        mapping=mapping,
        boundary_values={"blue_lower": lower_blue, "blue_upper": upper_blue},
        mapping_name="tangent_angle.mapping",
    )
    level = WarningLevel[label.upper()]
    reason = f"configured_{label}_range"
    result["tangent_angle_indicator_status"] = "valid"
    result.update(_level_record(level, name="tangent_angle"))
    result["tangent_angle_reason"] = reason
    return result


def _classify_delta_v(
    *,
    delta_v: float,
    status: str,
    tau: float,
    state_center: float,
    mapping: list[dict[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "delta_v_indicator_status": status,
        "delta_v_state": pd.NA,
        "delta_v_reason": pd.NA,
    }
    if status != "valid":
        result["delta_v_reason"] = f"source_{status}"
        return result
    if not np.isfinite(delta_v):
        result["delta_v_indicator_status"] = "invalid"
        result["delta_v_reason"] = "nonfinite_delta_v"
        return result
    state = _classify_configured_range(
        value=delta_v,
        mapping=mapping,
        boundary_values={"state_center": state_center, "tau": tau},
        mapping_name="delta_v.mapping",
    )
    result["delta_v_state"] = state
    result["delta_v_reason"] = f"operational_tau_{state}"
    return result


def _build_station_timeline(
    *,
    predictions: pd.DataFrame,
    kinematics: pd.DataFrame,
    thresholds: pd.DataFrame,
    loaded: _LoadedProfile,
) -> pd.DataFrame:
    interval_input = predictions.copy()
    interval_input["interval_input_status"] = "valid"
    interval = classify_observed_interval_states(
        interval_input,
        input_status_column="interval_input_status",
    )
    timeline = interval.merge(
        kinematics,
        on=["date", "station"],
        how="left",
        validate="one_to_one",
        suffixes=("", "_kinematics"),
    )
    if timeline["case"].isna().any():
        raise OperationalRunInputError(
            "Every operational prediction row must map to a kinematics row."
        )
    timeline = timeline.merge(
        thresholds,
        on="station",
        how="left",
        validate="many_to_one",
    )
    if timeline["comparator_v0_mm_per_day"].isna().any():
        raise OperationalRunInputError(
            "Every operational prediction row must map to a station baseline."
        )

    records: list[dict[str, Any]] = []
    for row in timeline.itertuples(index=False):
        velocity_status = _normalise_input_status(row.velocity_status)
        delta_v_status = _normalise_input_status(row.delta_v_status)
        interval_status = _normalise_input_status(row.interval_status)
        velocity_value = _as_float_or_nan(row.velocity)
        delta_v_value = _as_float_or_nan(row.delta_v)
        dt_days_value = _as_float_or_nan(row.dt_days)
        velocity_record = _classify_velocity(
            velocity=velocity_value,
            status=velocity_status,
            lower_blue=float(row.velocity_blue_lower_mm_per_day),
            upper_blue=float(row.velocity_blue_upper_mm_per_day),
            v0=float(row.comparator_v0_mm_per_day),
            sigma=float(row.selected_segment_sigma_mm_per_day),
            mapping=loaded.profile["velocity_baseline"]["mapping"],
        )
        tangent_record = _classify_tangent_angle(
            velocity=velocity_value,
            velocity_status=velocity_status,
            time_status=_normalise_input_status(row.time_status),
            dt_days=dt_days_value,
            v0=float(row.comparator_v0_mm_per_day),
            lower_blue=float(row.tangent_blue_lower_degree),
            upper_blue=float(row.tangent_blue_upper_degree),
            formula=loaded.profile["tangent_angle"]["formula"],
            mapping=loaded.profile["tangent_angle"]["mapping"],
            nonregular_handling=loaded.profile["tangent_angle"][
                "nonregular_handling"
            ],
        )
        delta_v_record = _classify_delta_v(
            delta_v=delta_v_value,
            status=delta_v_status,
            tau=float(row.delta_v_near_zero_tau_mm_per_day),
            state_center=float(row.delta_v_state_center_mm_per_day),
            mapping=loaded.profile["delta_v"]["mapping"],
        )
        fusion_inputs = {
            "interval_level": (
                None if interval_status != "valid" else int(row.interval_level)
            ),
            "velocity_level": (
                None
                if velocity_record["velocity_indicator_status"] != "valid"
                else int(velocity_record["velocity_level"])
            ),
            "delta_v_state": (
                None
                if delta_v_record["delta_v_indicator_status"] != "valid"
                else str(delta_v_record["delta_v_state"])
            ),
            "tangent_angle_level": (
                None
                if tangent_record["tangent_angle_indicator_status"] != "valid"
                else int(tangent_record["tangent_angle_level"])
            ),
            "input_statuses": {
                "interval": interval_status,
                "velocity": velocity_record["velocity_indicator_status"],
                "delta_v": delta_v_record["delta_v_indicator_status"],
                "tangent_angle": tangent_record["tangent_angle_indicator_status"],
            },
        }
        if loaded.profile["station_fusion"]["implementation"] == _V1_STATION_FUSION:
            fusion = fuse_station_indicators_with_minimum_support(
                **fusion_inputs,
                minimum_support=int(loaded.profile["station_fusion"]["minimum_support"]),
            )
        else:
            fusion = fuse_station_evidence_families(**fusion_inputs)
        record = row._asdict()
        record.update(velocity_record)
        record.update(tangent_record)
        record.update(delta_v_record)
        record.update(fusion.to_record())
        record.update(
            {
                "operational_profile_id": loaded.profile["profile_id"],
                "operational_profile_version": loaded.profile["profile_version"],
                "base_protocol_id": loaded.base_protocol["protocol_id"],
                "base_protocol_version": loaded.base_protocol["protocol_version"],
                "base_protocol_content_sha256": protocol_content_sha256(
                    loaded.base_protocol
                ),
            }
        )
        records.append(record)
    return pd.DataFrame(records).sort_values(
        ["date", "station"], kind="stable"
    ).reset_index(drop=True)


def _attach_output_metadata(
    frame: pd.DataFrame,
    *,
    loaded: _LoadedProfile,
) -> pd.DataFrame:
    """Attach the non-formal identity to every standalone operational CSV."""

    result = frame.copy()
    for column, value in {
        "artifact_kind": ARTIFACT_KIND,
        "artifact_status": ARTIFACT_STATUS,
        "case": "ootang",
        "vajont_used": False,
        "formal_warning_output": False,
        "operational_profile_id": loaded.profile["profile_id"],
        "operational_profile_version": loaded.profile["profile_version"],
        "base_protocol_id": loaded.base_protocol["protocol_id"],
        "base_protocol_version": loaded.base_protocol["protocol_version"],
        "base_protocol_content_sha256": protocol_content_sha256(loaded.base_protocol),
    }.items():
        result[column] = value
    return result


def _join_stations(rows: pd.Series) -> str:
    return ";".join(sorted(str(value) for value in rows))


def _warning_level_or_none(value: object) -> WarningLevel | None:
    if pd.isna(value):
        return None
    try:
        return WarningLevel(int(value))
    except (TypeError, ValueError) as exc:
        raise OperationalRunInputError(
            "Evidence-family station timeline has an invalid warning level."
        ) from exc


def _parse_serialized_input_statuses(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, str) or not value:
        raise OperationalRunInputError(
            "Evidence-family station timeline lacks input-status audit data."
        )
    records: list[tuple[str, str]] = []
    for entry in value.split(";"):
        name, separator, status = entry.partition(":")
        if not separator or not name or not status:
            raise OperationalRunInputError(
                "Evidence-family station timeline has invalid input-status data."
            )
        records.append((name, status))
    return tuple(records)


def _station_evidence_results(
    rows: pd.DataFrame,
) -> dict[str, StationEvidenceResult]:
    results: dict[str, StationEvidenceResult] = {}
    for row in rows.itertuples(index=False):
        station = str(row.station)
        if station in results:
            raise OperationalRunInputError(
                "Spatial site date has duplicated station results."
            )
        status = str(row.station_assessment_status)
        candidate = _warning_level_or_none(row.candidate_level)
        kinematic = _warning_level_or_none(row.kinematic_level)
        families = tuple(
            value
            for value in str(row.evidence_families).split(";")
            if value and value != "<NA>"
        )
        acceleration = (
            None
            if pd.isna(row.acceleration_status)
            else str(row.acceleration_status)
        )
        confirmation = str(row.station_confirmation_status)
        reason = str(row.fusion_reason)
        results[station] = StationEvidenceResult(
            status=status,
            candidate_level=candidate,
            kinematic_level=kinematic,
            evidence_families=families,
            acceleration_status=acceleration,
            confirmation_status=confirmation,
            reason=reason,
            input_statuses=_parse_serialized_input_statuses(row.input_statuses),
        )
    return results


def _enrich_spatial_site_record(
    record: dict[str, Any],
    *,
    date: pd.Timestamp,
    rows: pd.DataFrame,
) -> dict[str, Any]:
    """Attach the common audit fields emitted by spatial site fusion."""

    assessable = rows.loc[rows["station_assessment_status"].eq("valid")].copy()
    nonassessable = rows.loc[
        ~rows["station_assessment_status"].eq("valid"), "station"
    ]
    single_family = assessable.loc[
        assessable["candidate_level"]
        .fillna(0)
        .astype(int)
        .gt(int(WarningLevel.GREEN))
        & assessable["evidence_family_count"].eq(1),
        "station",
    ]
    enriched = dict(record)
    enriched.update(
        {
            "date": date.strftime("%Y-%m-%d"),
            "split": str(rows["split"].iloc[0]),
            "formal_warning_output": False,
            "nonassessable_stations": _join_stations(nonassessable),
            "single_family_candidate_stations": _join_stations(single_family),
            **{
                f"station_count_at_or_above_{level.color}": int(
                    (
                        assessable["candidate_level"].fillna(-1).astype(int)
                        >= int(level)
                    ).sum()
                )
                for level in WARNING_LEVELS
            },
        }
    )
    return enriched


def _site_record_v2(
    date: pd.Timestamp,
    rows: pd.DataFrame,
    *,
    profile: dict[str, Any],
) -> dict[str, Any]:
    site_config = profile["site_fusion"]
    station_results = _station_evidence_results(rows)
    result: SpatialSiteFusionResult = fuse_site_spatial_blocks(
        station_results,
        blocks=site_config["spatial_blocks"],
        minimum_assessable_station_count=int(
            site_config["minimum_assessable_station_count"]
        ),
        require_all_blocks_for_green=bool(
            site_config["require_all_blocks_for_green"]
        ),
        minimum_supporting_stations=int(site_config["minimum_supporting_stations"]),
        minimum_supporting_blocks=int(site_config["minimum_supporting_blocks"]),
        cross_block_confirmation_minimum_level=WarningLevel[
            str(site_config["cross_block_confirmation_minimum_level"]).upper()
        ],
    )
    record = result.to_record(
        minimum_assessable_station_count=int(
            site_config["minimum_assessable_station_count"]
        ),
        require_all_blocks_for_green=bool(
            site_config["require_all_blocks_for_green"]
        ),
        minimum_supporting_stations=int(site_config["minimum_supporting_stations"]),
        minimum_supporting_blocks=int(site_config["minimum_supporting_blocks"]),
        cross_block_confirmation_minimum_level=WarningLevel[
            str(site_config["cross_block_confirmation_minimum_level"]).upper()
        ],
    )
    return _enrich_spatial_site_record(record, date=date, rows=rows)


def _site_record_v3(
    date: pd.Timestamp,
    rows: pd.DataFrame,
    *,
    profile: dict[str, Any],
) -> dict[str, Any]:
    site_config = profile["site_fusion"]
    station_results = _station_evidence_results(rows)
    higher_minimum = WarningLevel[
        str(site_config["higher_confirmation_minimum_level"]).upper()
    ]
    result: SpatialSiteFusionV3Result = fuse_site_spatial_blocks_v3(
        station_results,
        blocks=site_config["spatial_blocks"],
        minimum_assessable_station_count=int(
            site_config["minimum_assessable_station_count"]
        ),
        require_all_blocks_for_any_site_level=bool(
            site_config["require_all_blocks_for_any_site_level"]
        ),
        minimum_supporting_stations=int(site_config["minimum_supporting_stations"]),
        minimum_supporting_blocks=int(site_config["minimum_supporting_blocks"]),
        higher_confirmation_minimum_level=higher_minimum,
    )
    record = result.to_record(
        minimum_assessable_station_count=int(
            site_config["minimum_assessable_station_count"]
        ),
        require_all_blocks_for_any_site_level=bool(
            site_config["require_all_blocks_for_any_site_level"]
        ),
        minimum_supporting_stations=int(site_config["minimum_supporting_stations"]),
        minimum_supporting_blocks=int(site_config["minimum_supporting_blocks"]),
        higher_confirmation_minimum_level=higher_minimum,
    )
    return _enrich_spatial_site_record(record, date=date, rows=rows)


def _site_record(
    date: pd.Timestamp,
    rows: pd.DataFrame,
    *,
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Apply the profile-declared site rule for one date."""

    if rows["split"].nunique() != 1:
        raise OperationalRunInputError(
            f"Operational site date {date:%Y-%m-%d} spans multiple prediction splits."
        )
    expected = set(OOTANG_STATIONS)
    if set(rows["station"]) != expected:
        raise OperationalRunInputError(
            f"Operational site date {date:%Y-%m-%d} does not contain all stations."
        )
    if profile["site_fusion"]["implementation"] == _V2_SITE_FUSION:
        return _site_record_v2(date, rows, profile=profile)
    if profile["site_fusion"]["implementation"] == _V3_SITE_FUSION:
        return _site_record_v3(date, rows, profile=profile)
    valid = rows.loc[rows["fusion_status"].eq("valid")].copy()
    uncorroborated = rows.loc[rows["fusion_status"].eq("uncorroborated"), "station"]
    nonvalid = rows.loc[
        ~rows["fusion_status"].isin(["valid", "uncorroborated"]), "station"
    ]
    counts = {
        level: int((valid["final_level"] == int(level)).sum())
        for level in WARNING_LEVELS
    }
    at_or_above = {
        level: int((valid["final_level"] >= int(level)).sum())
        for level in WARNING_LEVELS
    }
    site_config = profile["site_fusion"]
    minimum_valid = int(site_config["minimum_valid_station_count"])
    minimum_support = int(site_config["minimum_supporting_stations"])
    site_level: WarningLevel | None = None
    site_status: str
    site_reason: str
    contributors: tuple[str, ...] = ()
    if len(valid) < minimum_valid:
        site_status = "insufficient_valid_station_results"
        site_reason = f"valid_station_count_below_{minimum_valid}"
    else:
        supported = [
            level
            for level in tuple(WARNING_LEVELS)[1:]
            if at_or_above[level] >= minimum_support
        ]
        if supported:
            site_level = supported[-1]
            site_status = "valid"
            site_reason = (
                f"{minimum_support}_station_corroborated_{site_level.color}"
            )
            contributors = tuple(
                sorted(
                    valid.loc[
                        valid["final_level"] >= int(site_level), "station"
                    ].astype(str)
                )
            )
        elif not uncorroborated.empty or any(
            count > 0 for level, count in counts.items() if level > WarningLevel.GREEN
        ):
            site_status = "uncorroborated"
            site_reason = f"no_{minimum_support}_station_elevated_support"
        else:
            site_level = WarningLevel.GREEN
            site_status = "valid"
            site_reason = "all_valid_station_results_green"
    return {
        "date": date.strftime("%Y-%m-%d"),
        "split": str(rows["split"].iloc[0]),
        "site_fusion_status": site_status,
        "site_level": None if site_level is None else int(site_level),
        "site_color": None if site_level is None else site_level.color,
        "site_fusion_reason": site_reason,
        "formal_warning_output": False,
        "total_station_count": len(rows),
        "valid_station_count": len(valid),
        "minimum_valid_station_count": minimum_valid,
        "minimum_supporting_stations": minimum_support,
        "contributing_stations": ";".join(contributors),
        "uncorroborated_stations": _join_stations(uncorroborated),
        "nonvalid_stations": _join_stations(nonvalid),
        **{
            f"station_count_{level.color}": counts[level] for level in WARNING_LEVELS
        },
        **{
            f"station_count_at_or_above_{level.color}": at_or_above[level]
            for level in WARNING_LEVELS
        },
    }


def _build_site_timeline(
    station_timeline: pd.DataFrame,
    *,
    profile: dict[str, Any],
) -> pd.DataFrame:
    records = [
        _site_record(date, rows, profile=profile)
        for date, rows in station_timeline.groupby("date", sort=True)
    ]
    return pd.DataFrame(records).sort_values("date", kind="stable").reset_index(
        drop=True
    )


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, lineterminator="\n")


def _forecast_manifest_source_record(
    manifest_path: Path,
    predictions_path: Path,
) -> dict[str, Any]:
    """Audit the elevation-aware forecast manifest used by the draft run."""

    if not manifest_path.is_file():
        raise OperationalRunInputError(
            f"Missing elevation-aware forecast manifest: {manifest_path}"
        )
    if not predictions_path.is_file():
        raise OperationalRunInputError(
            f"Missing forecast predictions: {predictions_path}"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OperationalRunInputError(
            f"Invalid elevation-aware forecast manifest: {manifest_path}"
        ) from exc
    if not isinstance(manifest, dict):
        raise OperationalRunInputError(
            "Elevation-aware forecast manifest must be a JSON object."
        )
    if manifest.get("schema_version") != "ootang_elevation_aware_convlstm_run_v1":
        raise OperationalRunInputError(
            "Forecast manifest schema is not the elevation-aware Ootang v1 schema."
        )
    if manifest.get("artifact_status") != "prototype_internal_not_confirmatory":
        raise OperationalRunInputError(
            "Forecast manifest must remain a non-confirmatory prototype."
        )
    if manifest.get("formal_warning_output") is not False:
        raise OperationalRunInputError(
            "Forecast manifest must retain formal_warning_output=false."
        )
    if manifest.get("vajont_used") is not False:
        raise OperationalRunInputError(
            "Forecast manifest must retain vajont_used=false."
        )
    if manifest.get("prototype_run_gate") != "allowed":
        raise OperationalRunInputError(
            "Forecast manifest must retain prototype_run_gate=allowed."
        )
    if manifest.get("confirmatory_evidence_gate") != "blocked":
        raise OperationalRunInputError(
            "Forecast manifest must retain confirmatory_evidence_gate=blocked."
        )
    spatial_representation = manifest.get("spatial_representation")
    if not isinstance(spatial_representation, dict):
        raise OperationalRunInputError(
            "Forecast manifest spatial_representation must be an object."
        )
    elevation = spatial_representation.get("elevation")
    if not isinstance(elevation, dict):
        raise OperationalRunInputError(
            "Forecast manifest elevation representation must be an object."
        )
    if elevation.get("usage") != "static_model_input_channel":
        raise OperationalRunInputError(
            "Forecast manifest does not document the required elevation channel."
        )
    expected_channels = [
        "displacement_idw_grid",
        "elevation_static_idw_grid",
        "RWL",
        "RWL_rate",
        "Rain_cum7",
        "Rain_cum15",
        "Rain_cum30",
    ]
    model = manifest.get("model")
    if not isinstance(model, dict):
        raise OperationalRunInputError(
            "Forecast manifest model must be an object."
        )
    if (
        model.get("input_schema") != "displacement_elevation_exog_v1"
        or model.get("input_channel_count") != len(expected_channels)
        or model.get("input_channels") != expected_channels
    ):
        raise OperationalRunInputError(
            "Forecast manifest does not match the required seven-channel "
            "elevation-aware model input."
        )
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        raise OperationalRunInputError(
            "Forecast manifest outputs must be an object."
        )
    declared_prediction = outputs.get(
        "figures/convlstm/forecast_predictions.csv",
        {},
    )
    if not isinstance(declared_prediction, dict):
        raise OperationalRunInputError(
            "Forecast prediction output record must be an object."
        )
    try:
        actual_prediction_sha = _sha256_file(predictions_path)
        manifest_sha = _sha256_file(manifest_path)
    except OSError as exc:
        raise OperationalRunInputError(
            "Unable to hash the forecast manifest or predictions."
        ) from exc
    declared_prediction_sha = declared_prediction.get("sha256")
    if declared_prediction_sha != actual_prediction_sha:
        raise OperationalRunInputError(
            "Prediction CSV does not match the elevation-aware forecast manifest."
        )
    return {
        "path": str(manifest_path),
        "sha256": manifest_sha,
        "schema_version": manifest.get("schema_version"),
        "prototype_run_gate": manifest.get("prototype_run_gate"),
        "confirmatory_evidence_gate": manifest.get(
            "confirmatory_evidence_gate"
        ),
        "elevation_usage": elevation["usage"],
        "elevation_method": elevation.get("method"),
        "prediction_sha256": actual_prediction_sha,
        "declared_prediction_sha256": declared_prediction_sha,
        "prediction_sha256_matches": True,
    }


def _manifest(
    *,
    loaded: _LoadedProfile,
    kinematics_path: Path,
    predictions_path: Path,
    forecast_manifest_source: dict[str, Any],
    evidence_manifest_path: Path,
    station_path: Path,
    site_path: Path,
    thresholds_path: Path,
    station_hash_path: Path,
    site_hash_path: Path,
    thresholds_hash_path: Path,
    station_timeline: pd.DataFrame,
    site_timeline: pd.DataFrame,
) -> dict[str, Any]:
    base_protocol_hash = protocol_content_sha256(loaded.base_protocol)
    spatial_block_source = _spatial_block_source_manifest(loaded)
    is_v3 = loaded.profile["site_fusion"]["implementation"] == _V3_SITE_FUSION
    forecast_source = dict(forecast_manifest_source)
    if is_v3 and isinstance(forecast_source.get("path"), str):
        forecast_source["path"] = _manifest_path(
            Path(forecast_source["path"]),
            repository_relative=True,
        )
    if is_v3 and spatial_block_source is not None:
        spatial_block_source = dict(spatial_block_source)
        spatial_block_source["path"] = _manifest_path(
            Path(spatial_block_source["path"]),
            repository_relative=True,
        )
    v3_implementation_sources = (
        {
            "runner": Path(__file__).resolve(),
            "station_fusion": ROOT / "code" / "warning" / "operational_v2_fusion.py",
            "site_fusion": ROOT / "code" / "warning" / "operational_v3_fusion.py",
            "spatial_blocks": ROOT / "code" / "warning" / "spatial_blocks.py",
        }
        if is_v3
        else {}
    )
    return {
        "artifact_kind": ARTIFACT_KIND,
        "artifact_status": ARTIFACT_STATUS,
        "formal_warning_output": False,
        "formal_warning_entry": FORMAL_WARNING_ENTRY,
        "case": "ootang",
        "vajont_used": False,
        "ootang_stations": list(OOTANG_STATIONS),
        "operational_profile": {
            "id": loaded.profile["profile_id"],
            "version": loaded.profile["profile_version"],
            "status": loaded.profile["status"],
            "path": _manifest_path(
                loaded.profile_path,
                repository_relative=is_v3,
            ),
            "content_sha256": _canonical_json_sha256(loaded.profile),
        },
        "base_draft_protocol": {
            "id": loaded.base_protocol["protocol_id"],
            "version": loaded.base_protocol["protocol_version"],
            "status": loaded.base_protocol["status"],
            "path": _manifest_path(
                loaded.base_protocol_path,
                repository_relative=is_v3,
            ),
            "content_sha256": base_protocol_hash,
            "unresolved_item_ids": list(unresolved_item_ids(loaded.base_protocol)),
        },
        **(
            {
                "site_output_contract": {
                    "version": "v3_dual_axis_1",
                    "site_axis": "site_confirmed_level",
                    "local_axis": "local_max_candidate_level",
                    "localized_blue_policy": (
                        "site_green_with_localized_blue_attention"
                    ),
                },
                "implementation_sources": {
                    name: {
                        "path": _manifest_path(
                            path,
                            repository_relative=True,
                        ),
                        "sha256": _sha256_file(path),
                    }
                    for name, path in v3_implementation_sources.items()
                },
            }
            if is_v3
            else {}
        ),
        "parameter_fit_splits": list(loaded.profile["parameter_fit_splits"]),
        "result_splits": list(loaded.profile["result_splits"]),
        "source_inputs": {
            "kinematics": {
                "path": _manifest_path(
                    kinematics_path,
                    repository_relative=is_v3,
                ),
                "sha256": _sha256_file(kinematics_path),
            },
            "predictions": {
                "path": _manifest_path(
                    predictions_path,
                    repository_relative=is_v3,
                ),
                "sha256": _sha256_file(predictions_path),
            },
            "forecast_run_manifest": forecast_source,
            "draft_evidence_manifest": {
                "path": _manifest_path(
                    evidence_manifest_path,
                    repository_relative=is_v3,
                ),
                "sha256": _sha256_file(evidence_manifest_path),
            },
            **(
                {"spatial_block_topology": spatial_block_source}
                if spatial_block_source is not None
                else {}
            ),
        },
        "outputs": {
            "station_timeline": {
                "path": _manifest_path(
                    station_path,
                    repository_relative=is_v3,
                ),
                "sha256": _sha256_file(station_hash_path),
                "n_rows": len(station_timeline),
            },
            "site_timeline": {
                "path": _manifest_path(
                    site_path,
                    repository_relative=is_v3,
                ),
                "sha256": _sha256_file(site_hash_path),
                "n_rows": len(site_timeline),
            },
            "thresholds": {
                "path": _manifest_path(
                    thresholds_path,
                    repository_relative=is_v3,
                ),
                "sha256": _sha256_file(thresholds_hash_path),
            },
        },
        "result_status_counts": {
            "station_fusion": {
                str(status): int(count)
                for status, count in station_timeline["fusion_status"].value_counts(
                    dropna=False
                ).items()
            },
            "site_fusion": {
                str(status): int(count)
                for status, count in site_timeline["site_fusion_status"].value_counts(
                    dropna=False
                ).items()
            },
            **(
                {
                    "site_confirmed_color": {
                        str(status): int(count)
                        for status, count in site_timeline[
                            "site_confirmed_color"
                        ]
                        .fillna("not_site_confirmed")
                        .value_counts(dropna=False)
                        .items()
                    },
                    "local_max_candidate_color": {
                        str(status): int(count)
                        for status, count in site_timeline[
                            "local_max_candidate_color"
                        ]
                        .fillna("no_assessable_candidate")
                        .value_counts(dropna=False)
                        .items()
                    },
                    "local_attention_status": {
                        str(status): int(count)
                        for status, count in site_timeline[
                            "local_attention_status"
                        ].value_counts(dropna=False).items()
                    },
                }
                if is_v3
                else {}
            ),
        },
        "not_claimed": list(loaded.profile["not_claimed"]),
    }


def write_ootang_operational_run(
    *,
    profile_path: str | Path = DEFAULT_PROFILE_PATH,
    kinematics_path: str | Path = DEFAULT_KINEMATICS_PATH,
    predictions_path: str | Path = DEFAULT_PREDICTIONS_PATH,
    forecast_manifest_path: str | Path = DEFAULT_FORECAST_MANIFEST_PATH,
    output_dir: str | Path | None = None,
    evidence_dir: str | Path | None = None,
) -> OperationalRunArtifacts:
    """Write a full Ootang implementation timeline without formal warnings.

    All tunable conventions originate in ``profile_path`` and only fit data can
    produce them.  Calibration and test rows are execution-only result rows;
    no test information is used to choose an operating parameter.
    """

    loaded = _load_operational_profile(profile_path)
    kinematics_file = Path(kinematics_path).resolve()
    predictions_file = Path(predictions_path).resolve()
    forecast_manifest_file = Path(forecast_manifest_path).resolve()
    forecast_manifest_source = _forecast_manifest_source_record(
        forecast_manifest_file,
        predictions_file,
    )
    target_dir = _resolve_operational_output_dir(loaded, output_dir)
    evidence_target = (
        Path(evidence_dir).resolve()
        if evidence_dir is not None
        else DEFAULT_EVIDENCE_DIR
    )
    evidence = write_draft_warning_evidence_bundle(
        kinematics_path=kinematics_file,
        predictions_path=predictions_file,
        output_dir=evidence_target,
        protocol_path=loaded.base_protocol_path,
    )
    predictions = _load_predictions(
        predictions_file,
        result_splits=tuple(loaded.profile["result_splits"]),
    )
    kinematics = _load_kinematics(kinematics_file)
    candidates = _load_candidate_baselines(
        evidence_target / "stable_segment_candidates.csv",
        loaded=loaded,
    )
    thresholds = _derive_thresholds(
        kinematics=kinematics,
        candidates=candidates,
        profile=loaded.profile,
    )
    station_timeline = _build_station_timeline(
        predictions=predictions,
        kinematics=kinematics,
        thresholds=thresholds,
        loaded=loaded,
    )
    site_timeline = _build_site_timeline(
        station_timeline,
        profile=loaded.profile,
    )
    thresholds = _attach_output_metadata(thresholds, loaded=loaded)
    station_timeline = _attach_output_metadata(station_timeline, loaded=loaded)
    site_timeline = _attach_output_metadata(site_timeline, loaded=loaded)

    station_path = target_dir / STATION_TIMELINE_FILENAME
    site_path = target_dir / SITE_TIMELINE_FILENAME
    thresholds_path = target_dir / THRESHOLDS_FILENAME
    manifest_path = target_dir / MANIFEST_FILENAME
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{target_dir.name}.operational-",
        dir=target_dir.parent,
    ) as directory:
        staging_dir = Path(directory)
        staged_station_path = staging_dir / STATION_TIMELINE_FILENAME
        staged_site_path = staging_dir / SITE_TIMELINE_FILENAME
        staged_thresholds_path = staging_dir / THRESHOLDS_FILENAME
        staged_manifest_path = staging_dir / MANIFEST_FILENAME
        _write_csv(station_timeline, staged_station_path)
        _write_csv(site_timeline, staged_site_path)
        _write_csv(thresholds, staged_thresholds_path)
        staged_manifest_path.write_text(
            json.dumps(
                _manifest(
                    loaded=loaded,
                    kinematics_path=kinematics_file,
                    predictions_path=predictions_file,
                    forecast_manifest_source=forecast_manifest_source,
                    evidence_manifest_path=evidence.manifest_path,
                    station_path=station_path,
                    site_path=site_path,
                    thresholds_path=thresholds_path,
                    station_hash_path=staged_station_path,
                    site_hash_path=staged_site_path,
                    thresholds_hash_path=staged_thresholds_path,
                    station_timeline=station_timeline,
                    site_timeline=site_timeline,
                ),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        promote_staged_files(
            (
                FileReplacement(staged_station_path, station_path),
                FileReplacement(staged_site_path, site_path),
                FileReplacement(staged_thresholds_path, thresholds_path),
                FileReplacement(staged_manifest_path, manifest_path),
            )
        )
    return OperationalRunArtifacts(
        station_timeline_path=station_path,
        site_timeline_path=site_path,
        thresholds_path=thresholds_path,
        manifest_path=manifest_path,
        evidence_manifest_path=evidence.manifest_path,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the non-formal, replaceable four-indicator Ootang implementation "
            "profile."
        )
    )
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE_PATH)
    parser.add_argument("--kinematics", type=Path, default=DEFAULT_KINEMATICS_PATH)
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS_PATH)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--evidence-dir", type=Path)
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    artifacts = write_ootang_operational_run(
        profile_path=args.profile,
        kinematics_path=args.kinematics,
        predictions_path=args.predictions,
        output_dir=args.output_dir,
        evidence_dir=args.evidence_dir,
    )
    print(
        "wrote non-formal Ootang operational timeline: "
        f"{artifacts.manifest_path}"
    )


if __name__ == "__main__":
    main()


__all__ = [
    "ARTIFACT_KIND",
    "ARTIFACT_STATUS",
    "DEFAULT_EVIDENCE_DIR",
    "DEFAULT_KINEMATICS_PATH",
    "DEFAULT_OUTPUT_DIR",
    "DEFAULT_PREDICTIONS_PATH",
    "DEFAULT_PROFILE_PATH",
    "DEFAULT_V2_OUTPUT_DIR",
    "DEFAULT_V3_OUTPUT_DIR",
    "MANIFEST_FILENAME",
    "OOTANG_STATIONS",
    "SITE_TIMELINE_FILENAME",
    "STATION_TIMELINE_FILENAME",
    "THRESHOLDS_FILENAME",
    "OperationalRunArtifacts",
    "OperationalRunInputError",
    "OperationalRunProfileError",
    "write_ootang_operational_run",
]
