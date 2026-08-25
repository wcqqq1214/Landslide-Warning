"""Fail-closed G0--G4 register for a future Ootang v5 candidate runner.

This module validates decisions and evidence only.  It does not read a label
payload, train a model, run inference, fuse evidence, or create warning output.
Passing this preflight is necessary, but not sufficient, for a future G5a
run.  A separate versioned G5a run contract must freeze the model inputs,
search space, randomness, calibration procedure, failure rules, and output
namespace before any runner may read training data or write artifacts.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTER_PATH = (
    ROOT / "config" / "ootang_v5_gate_register.v1.json"
)
GATE_IDS = ("G0", "G1", "G2", "G3", "G4")
PASS = "PASS"
BLOCKED = "BLOCKED"
FORBIDDEN_PATH_TOKENS = ("vajont",)
FIXED_SPLITS = {
    "fit": {
        "start_date": "2016-08-06",
        "end_date": "2019-02-02",
        "role": "model_fit_training_and_feature_statistics_only",
    },
    "calibration": {
        "start_date": "2019-02-03",
        "end_date": "2019-09-17",
        "role": "registered_model_and_threshold_selection_only",
    },
    "historical_test": {
        "start_date": "2019-09-18",
        "end_date": "2020-06-30",
        "role": "retrospective_internal_only_already_viewed",
    },
}
V0_SELECTION_WINDOW = {
    "start_date": "2016-07-01",
    "end_date": "2019-02-02",
    "station_rows": 947,
    "role": "automatic_v0_fit_end_bounded_kinematics_only",
}
CURRENT_AVAILABILITY = {
    "available": ["MJ1", "MJ3"],
    "unavailable": {
        "ATU1": "first_break_is_not_accelerating",
        "ATU2": "first_break_is_not_accelerating",
        "ATU3": "first_break_is_not_accelerating",
        "ATU4": "first_break_is_not_accelerating",
        "ATU5": "first_break_is_not_accelerating",
        "MJ9": "nonpositive_initial_segment_slope",
    },
}
CURRENT_COVERAGE = {
    "deployment_stations": 8,
    "available_stations": 2,
    "station_coverage_fraction": 0.25,
    "result_dates": 514,
    "deployment_station_days": 4112,
    "available_station_days": 1028,
    "unavailable_station_days": 3084,
    "station_day_coverage_fraction": 0.25,
    "covered_spatial_blocks": ["O1"],
    "required_spatial_blocks_unapproved": ["O1", "O2", "O3"],
    "formal_site_output_days": 0,
}
HARD_AVAILABILITY_POLICY = {
    "unavailable_state": "not_applicable_abstain",
    "unavailable_as_negative_or_green": False,
    "imputation_allowed": False,
    "cross_station_borrowing_allowed": False,
    "v4_comparator_fallback_allowed": False,
    "constant_v0_allowed": False,
    "weight_renormalization_on_missing_allowed": False,
    "posthoc_segmentation_relaxation_allowed": False,
    "complete_case_required_for_two_branch_fusion": True,
}
PRIMARY_METRICS = [
    "event_recall",
    "false_alarms_per_100_negative_release_unit_days",
]
BASELINES = ["B0", "B1", "B2", "B3", "B4", "B5"]
V1_REGISTER_VERSION = "1.0.0-blocked"
V1_GATE_STATUSES = {
    "G0": PASS,
    "G1": BLOCKED,
    "G2": BLOCKED,
    "G3": BLOCKED,
    "G4": BLOCKED,
}
V1_BLOCKER_CODES = {
    "G0": [],
    "G1": [
        "independent_label_definition_missing",
        "independent_label_file_missing",
        "label_manifest_missing",
        "label_approval_missing",
    ],
    "G2": [
        "primary_horizon_unapproved",
        "release_unit_unapproved",
        "confirmation_block_unregistered",
        "unseal_authority_unassigned",
    ],
    "G3": [
        "deployment_scope_unapproved",
        "unavailable_behavior_unapproved",
        "coverage_denominator_unapproved",
        "site_level_behavior_unapproved",
    ],
    "G4": [
        "primary_thresholds_unset",
        "fusion_increment_metric_unset",
        "minimum_support_unset",
        "confidence_interval_method_unapproved",
        "metric_approval_missing",
    ],
}
V1_TOP_PATHS = {
    "policy_document": "docs/v5_validation_protocol.md",
    "inventory_document": "docs/v5_g1_g4_preflight.md",
    "preflight": "code/warning/ootang_v5_gate_preflight.py",
}
V1_EVIDENCE_PATHS = {
    "G0": {
        "numerical_audit": "docs/v5_v0_numerical_audit.md",
        "automatic_v0_manifest": (
            "figures/auto_v0_direct_bai_perron_ootang_v1/manifest.json"
        ),
        "automatic_v0_profile": (
            "config/ootang_auto_v0_direct_bai_perron.v1.json"
        ),
        "automatic_v0_runner": "code/warning/auto_v0_direct_bai_perron.py",
        "shared_bic_definition": "code/warning/bai_perron_initial_slope.py",
    },
    "G1": {
        "inventory": "docs/v5_g1_g4_preflight.md",
        "proxy_manifest": (
            "figures/ngboost_interval_proxy_pilot_ootang_v1/manifest.json"
        ),
        "v4_draft_manifest": (
            "figures/warning_operational_draft_v4/"
            "ootang_operational_run_manifest.json"
        ),
        "v5_candidate_manifest": (
            "figures/v5_candidate_display_ootang_v1/manifest.json"
        ),
        "expert_review": "docs/ootang_elevation_warning_expert_review.md",
    },
    "G2": {
        "inventory": "docs/v5_g1_g4_preflight.md",
        "forecast_manifest": "figures/convlstm/forecast_run_manifest.json",
        "lineage_manifest": (
            "figures/data_lineage/ootang_data_lineage_manifest.json"
        ),
        "rolling_validation_folds": (
            "figures/convlstm/rolling_validation_folds.csv"
        ),
        "calibration_review": (
            "docs/ootang_interval_calibration_expert_review.md"
        ),
    },
    "G3": {
        "inventory": "docs/v5_g1_g4_preflight.md",
        "automatic_v0_manifest": (
            "figures/auto_v0_direct_bai_perron_ootang_v1/manifest.json"
        ),
        "automatic_v0_candidates": (
            "figures/auto_v0_direct_bai_perron_ootang_v1/candidates.csv"
        ),
        "v5_candidate_manifest": (
            "figures/v5_candidate_display_ootang_v1/manifest.json"
        ),
        "v4_profile": "config/ootang_operational_run.v4.draft.json",
    },
    "G4": {
        "inventory": "docs/v5_g1_g4_preflight.md",
        "validation_protocol": "docs/v5_validation_protocol.md",
    },
}


class GateRegisterValidationError(ValueError):
    """Raised when the G0--G4 register or evidence is malformed or stale."""


class G0G4PreflightBlockedError(RuntimeError):
    """Raised when a valid register still has one or more blocked gates."""


@dataclass(frozen=True)
class GateEvaluation:
    """Deterministic evaluation of one validated G0--G4 register."""

    register_id: str
    register_version: str
    register_path: str
    register_file_sha256: str
    register_content_sha256: str
    effective_gates: dict[str, str]
    blocker_codes: dict[str, tuple[str, ...]]
    g0_g4_preflight_passed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "register": {
                "id": self.register_id,
                "version": self.register_version,
                "path": self.register_path,
                "file_sha256": self.register_file_sha256,
                "content_sha256": self.register_content_sha256,
            },
            "effective_gates": self.effective_gates,
            "blocked_gates": [
                gate
                for gate in GATE_IDS
                if self.effective_gates[gate] == BLOCKED
            ],
            "blocker_codes": {
                gate: list(self.blocker_codes[gate])
                for gate in GATE_IDS
                if self.blocker_codes[gate]
            },
            "g0_g4_preflight_passed": self.g0_g4_preflight_passed,
            "g5a_authorization_evaluated": False,
            "g5a_authorized": False,
            "ngboost_inference_output": False,
            "v5_fusion_output": False,
            "formal_warning_output": False,
            "vajont_used": False,
        }


def _reject_json_constant(value: str) -> None:
    raise GateRegisterValidationError(
        f"Gate register contains forbidden JSON constant: {value}"
    )


def _reject_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GateRegisterValidationError(
                f"Gate register contains duplicate key: {key}"
            )
        result[key] = value
    return result


def _canonical_sha256(payload: dict[str, Any]) -> str:
    try:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise GateRegisterValidationError(
            "Gate register must contain finite JSON values"
        ) from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1_048_576), b""):
                digest.update(chunk)
    except OSError as exc:
        raise GateRegisterValidationError(
            f"Evidence file cannot be hashed: {path}"
        ) from exc
    return digest.hexdigest()


def _manifest_path(path: Path, root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def _require_exact_keys(
    value: object,
    expected: set[str],
    *,
    name: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GateRegisterValidationError(f"{name} must be an object")
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise GateRegisterValidationError(
            f"{name} keys drifted; missing={missing}, extra={extra}"
        )
    return value


def _strict_json_equal(actual: object, expected: object) -> bool:
    """Compare JSON values without treating booleans as integers."""

    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or set(actual) != set(expected):
            return False
        return all(
            _strict_json_equal(actual[key], expected[key]) for key in expected
        )
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return False
        return all(
            _strict_json_equal(actual_item, expected_item)
            for actual_item, expected_item in zip(actual, expected, strict=True)
        )
    return actual == expected


def _reject_symlink_components(path: Path, *, root: Path, name: str) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise GateRegisterValidationError(
            f"{name} path escapes the repository"
        ) from exc
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise GateRegisterValidationError(
                f"{name} path is outside scope: symlinks are forbidden"
            )


def _resolve_repo_file(path_value: str, *, root: Path, name: str) -> Path:
    if not isinstance(path_value, str) or not path_value:
        raise GateRegisterValidationError(f"{name} path must be nonempty")
    candidate = Path(path_value)
    normalized = candidate.as_posix().lower()
    if candidate.is_absolute() or ".." in candidate.parts:
        raise GateRegisterValidationError(
            f"{name} path must be repository-relative without traversal"
        )
    if any(token in normalized for token in FORBIDDEN_PATH_TOKENS):
        raise GateRegisterValidationError(f"{name} path is outside scope")
    if normalized == "review.md" or normalized.endswith("/review.md"):
        raise GateRegisterValidationError(f"{name} path is outside scope")
    lexical_path = root / candidate
    _reject_symlink_components(lexical_path, root=root, name=name)
    resolved = lexical_path.resolve()
    try:
        resolved_relative = resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise GateRegisterValidationError(
            f"{name} path escapes the repository"
        ) from exc
    resolved_normalized = resolved_relative.as_posix().lower()
    if any(token in resolved_normalized for token in FORBIDDEN_PATH_TOKENS):
        raise GateRegisterValidationError(f"{name} resolved path is outside scope")
    if resolved_normalized == "review.md" or resolved_normalized.endswith(
        "/review.md"
    ):
        raise GateRegisterValidationError(f"{name} resolved path is outside scope")
    if not resolved.is_file():
        raise GateRegisterValidationError(f"{name} file is missing: {path_value}")
    return resolved


def _validate_sha256(value: object, *, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise GateRegisterValidationError(
            f"{name} must be a lowercase SHA-256"
        )
    return value


def _validate_document_record(
    value: object,
    *,
    root: Path,
    name: str,
    expected_path: str,
) -> None:
    record = _require_exact_keys(value, {"path", "sha256"}, name=name)
    if record["path"] != expected_path:
        raise GateRegisterValidationError(
            f"{name} must bind {expected_path}"
        )
    path = _resolve_repo_file(record["path"], root=root, name=name)
    declared = _validate_sha256(record["sha256"], name=f"{name}.sha256")
    if _sha256_file(path) != declared:
        raise GateRegisterValidationError(f"{name} SHA-256 does not match")


def _validate_evidence(
    value: object,
    *,
    root: Path,
    gate: str,
    expected_paths: dict[str, str],
) -> None:
    if not isinstance(value, list) or not value:
        raise GateRegisterValidationError(f"{gate}.evidence must be nonempty")
    records_by_role: dict[str, dict[str, Any]] = {}
    resolved_paths: set[Path] = set()
    for index, raw_record in enumerate(value):
        name = f"{gate}.evidence[{index}]"
        record = _require_exact_keys(
            raw_record,
            {"role", "path", "sha256"},
            name=name,
        )
        role = record["role"]
        if not isinstance(role, str) or not role:
            raise GateRegisterValidationError(f"{name}.role must be nonempty")
        if role in records_by_role:
            raise GateRegisterValidationError(
                f"{gate}.evidence contains duplicate role: {role}"
            )
        if role not in expected_paths:
            raise GateRegisterValidationError(
                f"{gate}.evidence contains unexpected role: {role}"
            )
        if record["path"] != expected_paths[role]:
            raise GateRegisterValidationError(
                f"{gate}.evidence role {role} must bind {expected_paths[role]}"
            )
        records_by_role[role] = record
        path = _resolve_repo_file(record["path"], root=root, name=name)
        if path in resolved_paths:
            raise GateRegisterValidationError(
                f"{gate}.evidence cannot reuse one file for multiple roles"
            )
        resolved_paths.add(path)
        declared = _validate_sha256(
            record["sha256"], name=f"{name}.sha256"
        )
        if _sha256_file(path) != declared:
            raise GateRegisterValidationError(
                f"{gate} evidence SHA-256 does not match: {role}"
            )
    if set(records_by_role) != set(expected_paths):
        missing = sorted(set(expected_paths) - set(records_by_role))
        extra = sorted(set(records_by_role) - set(expected_paths))
        raise GateRegisterValidationError(
            f"{gate}.evidence roles drifted; missing={missing}, extra={extra}"
        )


def _validate_g0(decision: object) -> None:
    value = _require_exact_keys(
        decision,
        {"closed_on", "basis", "candidate_availability"},
        name="G0.decision",
    )
    if value["basis"] != "docs/v5_v0_numerical_audit.md":
        raise GateRegisterValidationError("G0 basis must be the closed audit")
    if not _strict_json_equal(
        value["candidate_availability"], CURRENT_AVAILABILITY
    ):
        raise GateRegisterValidationError("G0 candidate availability drifted")
    if value["closed_on"] != "2026-08-20":
        raise GateRegisterValidationError("G0 PASS has no valid closure date")


def _validate_g1(decision: object) -> None:
    value = _require_exact_keys(
        decision,
        {
            "label_definition",
            "label_file",
            "label_manifest",
            "unknown_policy",
            "self_generated_labels_allowed",
        },
        name="G1.decision",
    )
    if value["unknown_policy"] != "preserve_unknown_never_negative":
        raise GateRegisterValidationError("G1 unknown policy is not fail-closed")
    if value["self_generated_labels_allowed"] is not False:
        raise GateRegisterValidationError("G1 cannot allow self-generated labels")
    if any(
        value[field] is not None
        for field in ("label_definition", "label_file", "label_manifest")
    ):
        raise GateRegisterValidationError(
            "G1 v1 decisions must remain null while blocked"
        )


def _validate_g2(decision: object) -> None:
    value = _require_exact_keys(
        decision,
        {
            "primary_horizon_days",
            "release_unit",
            "as_of_rule",
            "existing_splits",
            "v0_selection_window",
            "confirmation_manifest",
            "confirmation_sealed",
            "selection_used",
            "unseal_authority",
        },
        name="G2.decision",
    )
    if not _strict_json_equal(value["existing_splits"], FIXED_SPLITS):
        raise GateRegisterValidationError("G2 existing split roles drifted")
    if not _strict_json_equal(
        value["v0_selection_window"], V0_SELECTION_WINDOW
    ):
        raise GateRegisterValidationError("G2 V0 selection window drifted")
    unresolved = {
        "primary_horizon_days": None,
        "release_unit": None,
        "as_of_rule": None,
        "confirmation_manifest": None,
        "confirmation_sealed": False,
        "selection_used": None,
        "unseal_authority": None,
    }
    if any(
        not _strict_json_equal(value[field], expected)
        for field, expected in unresolved.items()
    ):
        raise GateRegisterValidationError(
            "G2 v1 decisions must remain unresolved while blocked"
        )


def _validate_g3(decision: object) -> None:
    value = _require_exact_keys(
        decision,
        {
            "deployment_scope",
            "mixed_availability_output_allowed",
            "coverage_denominator",
            "site_level_behavior",
            "hard_policy",
            "current_snapshot",
            "coverage_threshold_source",
        },
        name="G3.decision",
    )
    if not _strict_json_equal(value["hard_policy"], HARD_AVAILABILITY_POLICY):
        raise GateRegisterValidationError("G3 hard availability policy drifted")
    if not _strict_json_equal(value["current_snapshot"], CURRENT_COVERAGE):
        raise GateRegisterValidationError("G3 current coverage snapshot drifted")
    if value["coverage_threshold_source"] != "G4.decision.thresholds.coverage_min":
        raise GateRegisterValidationError("G3 coverage threshold must be single-source")
    for field in (
        "deployment_scope",
        "mixed_availability_output_allowed",
        "coverage_denominator",
        "site_level_behavior",
    ):
        if value[field] is not None:
            raise GateRegisterValidationError(
                "G3 v1 decisions must remain null while blocked"
            )


def _validate_g4(decision: object) -> None:
    value = _require_exact_keys(
        decision,
        {
            "primary_metrics",
            "baselines",
            "thresholds",
            "minimum_support",
            "ci_contract",
        },
        name="G4.decision",
    )
    if not _strict_json_equal(value["primary_metrics"], PRIMARY_METRICS):
        raise GateRegisterValidationError("G4 primary metrics drifted")
    if not _strict_json_equal(value["baselines"], BASELINES):
        raise GateRegisterValidationError("G4 baseline set drifted")
    thresholds = _require_exact_keys(
        value["thresholds"],
        {
            "event_recall_min",
            "far_max_per_100_negative_unit_days",
            "coverage_min",
            "fusion_increment",
            "far_noninferiority_margin",
        },
        name="G4.decision.thresholds",
    )
    coverage = _require_exact_keys(
        thresholds["coverage_min"],
        {
            "station_day",
            "site_day",
            "spatial_complete",
            "event_window",
            "class_conditional",
        },
        name="G4.decision.thresholds.coverage_min",
    )
    increment = _require_exact_keys(
        thresholds["fusion_increment"],
        {"metric", "direction", "delta_min", "comparator_policy"},
        name="G4.decision.thresholds.fusion_increment",
    )
    support = _require_exact_keys(
        value["minimum_support"],
        {"independent_events", "negative_unit_days", "per_class"},
        name="G4.decision.minimum_support",
    )
    ci = _require_exact_keys(
        value["ci_contract"],
        {
            "status",
            "joint_one_sided_confidence",
            "event_recall",
            "block_bootstrap",
            "station_day_independence_assumed",
        },
        name="G4.decision.ci_contract",
    )
    if ci["station_day_independence_assumed"] is not False:
        raise GateRegisterValidationError("G4 cannot assume independent station-days")
    recall_ci = _require_exact_keys(
        ci["event_recall"],
        {"method", "bound", "sampling_unit"},
        name="G4.decision.ci_contract.event_recall",
    )
    if not _strict_json_equal(
        recall_ci,
        {
            "method": "exact_clopper_pearson",
            "bound": "lower",
            "sampling_unit": "independent_event_id",
        },
    ):
        raise GateRegisterValidationError("G4 event recall CI drifted")
    bootstrap = _require_exact_keys(
        ci["block_bootstrap"],
        {
            "method",
            "sampling_unit",
            "block_length_days_rule",
            "resamples",
            "seed",
        },
        name="G4.decision.ci_contract.block_bootstrap",
    )
    if not _strict_json_equal(
        bootstrap,
        {
            "method": "paired_moving_date_block",
            "sampling_unit": "complete_date_blocks_all_stations",
            "block_length_days_rule": "max(H,7)",
            "resamples": 10000,
            "seed": 20260820,
        },
    ):
        raise GateRegisterValidationError("G4 block bootstrap contract drifted")

    if ci["status"] != "proposed_unapproved":
        raise GateRegisterValidationError(
            "Blocked G4 CI must remain proposed_unapproved"
        )
    if not _strict_json_equal(ci["joint_one_sided_confidence"], 0.975):
        raise GateRegisterValidationError(
            "Blocked G4 CI proposal must remain 0.975"
        )
    unresolved = [
        thresholds["event_recall_min"],
        thresholds["far_max_per_100_negative_unit_days"],
        *coverage.values(),
        increment["metric"],
        increment["direction"],
        increment["delta_min"],
        increment["comparator_policy"],
        thresholds["far_noninferiority_margin"],
        *support.values(),
    ]
    if any(item is not None for item in unresolved):
        raise GateRegisterValidationError(
            "Blocked G4 numeric decisions must remain null until approval"
        )


def _validate_register(register: dict[str, Any], *, root: Path) -> None:
    top = _require_exact_keys(
        register,
        {
            "schema_version",
            "register_id",
            "register_version",
            "status",
            "case",
            "scope",
            "candidate_only",
            "formal_warning_output",
            "ngboost_inference_output",
            "v5_fusion_output",
            "vajont_used",
            "default_pipeline_member",
            "policy_document",
            "inventory_document",
            "implementation_sources",
            "gates",
        },
        name="register",
    )
    fixed = {
        "schema_version": "ootang_v5_gate_register_v1",
        "register_id": "ootang-v5-g0-g4-register-v1",
        "register_version": V1_REGISTER_VERSION,
        "status": "draft_blocked",
        "case": "ootang",
        "scope": "g0_g4_preflight_only",
        "candidate_only": True,
        "formal_warning_output": False,
        "ngboost_inference_output": False,
        "v5_fusion_output": False,
        "vajont_used": False,
        "default_pipeline_member": False,
    }
    for field, expected in fixed.items():
        if not _strict_json_equal(top[field], expected):
            raise GateRegisterValidationError(
                f"register.{field} must be {expected!r}"
            )
    _validate_document_record(
        top["policy_document"],
        root=root,
        name="policy_document",
        expected_path=V1_TOP_PATHS["policy_document"],
    )
    _validate_document_record(
        top["inventory_document"],
        root=root,
        name="inventory_document",
        expected_path=V1_TOP_PATHS["inventory_document"],
    )
    sources = _require_exact_keys(
        top["implementation_sources"],
        {"preflight"},
        name="implementation_sources",
    )
    _validate_document_record(
        sources["preflight"],
        root=root,
        name="implementation_sources.preflight",
        expected_path=V1_TOP_PATHS["preflight"],
    )

    gates = _require_exact_keys(top["gates"], set(GATE_IDS), name="gates")
    for gate in GATE_IDS:
        record = _require_exact_keys(
            gates[gate],
            {"declared_status", "blocker_codes", "decision", "evidence", "approval"},
            name=gate,
        )
        status = record["declared_status"]
        expected_status = V1_GATE_STATUSES[gate]
        if status != expected_status:
            if gate != "G0" and status == PASS:
                raise GateRegisterValidationError(
                    f"{gate} v1 cannot promote to PASS; create a new schema "
                    "with evidence-specific validation"
                )
            raise GateRegisterValidationError(
                f"{gate}.declared_status must be {expected_status} in v1"
            )
        blockers = record["blocker_codes"]
        if not _strict_json_equal(blockers, V1_BLOCKER_CODES[gate]):
            raise GateRegisterValidationError(
                f"{gate}.blocker_codes drifted from the frozen v1 inventory"
            )
        if record["approval"] is not None:
            raise GateRegisterValidationError(
                f"{gate}.approval must remain null in the v1 snapshot"
            )

        _validate_evidence(
            record["evidence"],
            root=root,
            gate=gate,
            expected_paths=V1_EVIDENCE_PATHS[gate],
        )
        if gate == "G0":
            _validate_g0(record["decision"])
        elif gate == "G1":
            _validate_g1(record["decision"])
        elif gate == "G2":
            _validate_g2(record["decision"])
        elif gate == "G3":
            _validate_g3(record["decision"])
        else:
            _validate_g4(record["decision"])


def _load_validated_register_snapshot(
    path: str | Path = DEFAULT_REGISTER_PATH,
    *,
    root: Path = ROOT,
) -> tuple[dict[str, Any], Path, str]:
    """Read, parse, fingerprint, and validate one immutable byte snapshot."""

    root = Path(root).resolve()
    register_path = Path(path)
    if not register_path.is_absolute():
        register_path = root / register_path
    try:
        register_path.relative_to(root)
    except ValueError as exc:
        raise GateRegisterValidationError(
            "Gate register must stay inside the repository"
        ) from exc
    _reject_symlink_components(
        register_path,
        root=root,
        name="Gate register",
    )
    register_path = register_path.resolve()
    try:
        register_path.relative_to(root)
    except ValueError as exc:
        raise GateRegisterValidationError(
            "Gate register must stay inside the repository"
        ) from exc
    normalized = register_path.as_posix().lower()
    if any(token in normalized for token in FORBIDDEN_PATH_TOKENS) or normalized.endswith(
        "/review.md"
    ):
        raise GateRegisterValidationError("Gate register path is outside scope")
    try:
        raw_register = register_path.read_bytes()
    except FileNotFoundError as exc:
        raise GateRegisterValidationError(
            f"Gate register does not exist: {register_path}"
        ) from exc
    except OSError as exc:
        raise GateRegisterValidationError(
            f"Gate register cannot be read: {register_path}"
        ) from exc
    try:
        register_text = raw_register.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GateRegisterValidationError(
            f"Gate register is not UTF-8: {register_path}"
        ) from exc
    try:
        register = json.loads(
            register_text,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except json.JSONDecodeError as exc:
        raise GateRegisterValidationError(
            f"Gate register is invalid JSON: {register_path}"
        ) from exc
    if not isinstance(register, dict):
        raise GateRegisterValidationError("Gate register must be an object")
    _validate_register(register, root=root)
    return (
        register,
        register_path,
        hashlib.sha256(raw_register).hexdigest(),
    )


def load_and_validate_register(
    path: str | Path = DEFAULT_REGISTER_PATH,
    *,
    root: Path = ROOT,
) -> dict[str, Any]:
    """Load one repository-scoped register and verify all evidence hashes."""

    register, _, _ = _load_validated_register_snapshot(path, root=root)
    return register


def evaluate_g0_g4(
    path: str | Path = DEFAULT_REGISTER_PATH,
    *,
    root: Path = ROOT,
) -> GateEvaluation:
    """Validate and evaluate the current register without authorizing work."""

    root = Path(root).resolve()
    register, register_path, register_file_sha256 = (
        _load_validated_register_snapshot(path, root=root)
    )
    statuses = {
        gate: register["gates"][gate]["declared_status"] for gate in GATE_IDS
    }
    blockers = {
        gate: tuple(register["gates"][gate]["blocker_codes"])
        for gate in GATE_IDS
    }
    return GateEvaluation(
        register_id=register["register_id"],
        register_version=register["register_version"],
        register_path=_manifest_path(register_path, root),
        register_file_sha256=register_file_sha256,
        register_content_sha256=_canonical_sha256(register),
        effective_gates=statuses,
        blocker_codes=blockers,
        g0_g4_preflight_passed=all(
            statuses[gate] == PASS for gate in GATE_IDS
        ),
    )


def require_g0_g4_preflight() -> GateEvaluation:
    """Return only if all G0--G4 gates are valid and approved PASS.

    The frozen v1 snapshot can never pass; resolving blockers requires a new
    reviewed schema and validator.  This function does not authorize G5a.  A
    future G5a runner must also validate its own frozen run contract before
    performing any data I/O.
    """

    evaluation = evaluate_g0_g4()
    if not evaluation.g0_g4_preflight_passed:
        details = "; ".join(
            f"{gate}={','.join(evaluation.blocker_codes[gate])}"
            for gate in GATE_IDS
            if evaluation.blocker_codes[gate]
        )
        raise G0G4PreflightBlockedError(
            f"G0--G4 preflight is blocked: {details}"
        )
    return evaluation


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the Ootang v5 G0--G4 gate register"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--report",
        action="store_true",
        help="print a deterministic status report and exit zero",
    )
    mode.add_argument(
        "--require-g0-g4",
        action="store_true",
        help="require all G0--G4 gates to pass (default; does not authorize G5a)",
    )
    return parser.parse_args(argv)


def _main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.report:
            report = evaluate_g0_g4().as_dict()
            print(
                json.dumps(
                    report,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                    allow_nan=False,
                )
            )
            return 0
        require_g0_g4_preflight()
    except GateRegisterValidationError as exc:
        print(f"[v5-gates] invalid register: {exc}", file=sys.stderr)
        return 1
    except G0G4PreflightBlockedError as exc:
        print(f"[v5-gates] {exc}", file=sys.stderr)
        return 2
    print("[v5-gates] G0--G4 preflight passed; G5a remains separately gated")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
