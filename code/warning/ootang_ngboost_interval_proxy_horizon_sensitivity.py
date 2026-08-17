"""Ootang-only fixed-NGBoost horizon sensitivity for interval proxy states."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import pickle
from pathlib import Path
import sys
import tempfile
from typing import Any

import numpy as np
import pandas as pd
from ngboost import NGBClassifier


CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning.draft_evidence import FileReplacement, promote_staged_files  # noqa: E402
from warning import ootang_ngboost_interval_proxy_pilot as base  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE_PATH = ROOT / "config" / "ootang_ngboost_interval_proxy_horizon_sensitivity.v1.json"
DEFAULT_BASE_PROFILE_PATH = base.DEFAULT_PROFILE_PATH
DEFAULT_BASE_MANIFEST_PATH = base.DEFAULT_OUTPUT_DIR / "manifest.json"
DEFAULT_BASE_MODEL_PATH = base.DEFAULT_MODEL_PATH
DEFAULT_BASE_PREDICTIONS_PATH = base.DEFAULT_OUTPUT_DIR / "predictions.csv"
DEFAULT_BASE_METRICS_PATH = base.DEFAULT_OUTPUT_DIR / "metrics.csv"
DEFAULT_BASE_CONFUSION_PATH = base.DEFAULT_OUTPUT_DIR / "confusion_matrices.csv"
DEFAULT_BASE_RELIABILITY_PATH = base.DEFAULT_OUTPUT_DIR / "reliability.csv"
DEFAULT_OUTPUT_DIR = ROOT / "figures" / "ngboost_interval_proxy_horizon_sensitivity_ootang_v1"
DEFAULT_MODEL_PATHS = {
    1: ROOT / "models" / "ootang_ngboost_interval_proxy_horizon_sensitivity_v1_h1.pkl",
    3: ROOT / "models" / "ootang_ngboost_interval_proxy_horizon_sensitivity_v1_h3.pkl",
    7: ROOT / "models" / "ootang_ngboost_interval_proxy_horizon_sensitivity_v1_h7.pkl",
}
HORIZONS = (1, 3, 7)
EVALUATION_SPLITS = ("calibration", "test")
OUTPUT_FILENAMES = (
    "predictions.csv",
    "metrics.csv",
    "confusion_matrices.csv",
    "reliability.csv",
    "horizon_summary.csv",
    "manifest.json",
)
ARTIFACT_KIND = "ootang_ngboost_interval_proxy_horizon_sensitivity"
ARTIFACT_STATUS = "exploratory_proxy_horizon_sensitivity_not_formal"
SUMMARY_METRICS = (
    ("accuracy", "higher_is_better"),
    ("macro_f1_fixed_five", "higher_is_better"),
    ("macro_f1_supported_classes", "higher_is_better"),
    ("balanced_accuracy_supported_classes", "higher_is_better"),
    ("ordinal_mae", "lower_is_better"),
    ("quadratic_weighted_kappa", "higher_is_better"),
    ("false_escalation_vs_proxy_rate", "lower_is_better"),
    ("false_deescalation_vs_proxy_rate", "lower_is_better"),
)
EXPECTED_NOT_CLAIMED = (
    "selected_or_optimal_horizon",
    "horizon_ranking",
    "expert_or_field_truth",
    "independent_event_labels",
    "formal_warning_output",
    "operational_warning_validation",
    "operational_false_alarm_or_missed_event_rates",
    "convlstm_replacement_or_improvement",
    "v4_replacement_validation_threshold_or_fusion_change",
    "optimized_hyperparameters",
    "posthoc_probability_calibration",
    "causal_interpretation",
    "external_confirmation",
    "vajont_use_or_validation",
)
EXPECTED_HORIZON_CONTRACTS = {
    1: {
        "terminal_source_rows_excluded": 24,
        "expected_sample_counts": {"fit": 7280, "calibration": 1808, "test": 2288},
        "expected_target_class_counts": {
            "fit": {"green": 3538, "blue": 2870, "yellow": 701, "orange": 83, "red": 88},
            "calibration": {"green": 531, "blue": 847, "yellow": 287, "orange": 143, "red": 0},
            "test": {"green": 584, "blue": 1039, "yellow": 182, "orange": 133, "red": 350},
        },
        "expected_transition_counts": {"fit": 575, "calibration": 151, "test": 110},
        "expected_persistence_correct": {"fit": 6705, "calibration": 1657, "test": 2178},
    },
    3: {
        "terminal_source_rows_excluded": 72,
        "expected_sample_counts": {"fit": 7264, "calibration": 1792, "test": 2272},
        "expected_target_class_counts": {
            "fit": {"green": 3528, "blue": 2864, "yellow": 701, "orange": 83, "red": 88},
            "calibration": {"green": 519, "blue": 845, "yellow": 285, "orange": 143, "red": 0},
            "test": {"green": 571, "blue": 1038, "yellow": 180, "orange": 133, "red": 350},
        },
        "expected_transition_counts": {"fit": 1343, "calibration": 300, "test": 281},
        "expected_persistence_correct": {"fit": 5921, "calibration": 1492, "test": 1991},
    },
    7: {
        "terminal_source_rows_excluded": 168,
        "expected_sample_counts": {"fit": 7232, "calibration": 1760, "test": 2240},
        "expected_target_class_counts": {
            "fit": {"green": 3506, "blue": 2854, "yellow": 701, "orange": 83, "red": 88},
            "calibration": {"green": 499, "blue": 841, "yellow": 278, "orange": 142, "red": 0},
            "test": {"green": 547, "blue": 1034, "yellow": 176, "orange": 133, "red": 350},
        },
        "expected_transition_counts": {"fit": 2139, "calibration": 450, "test": 508},
        "expected_persistence_correct": {"fit": 5093, "calibration": 1310, "test": 1732},
    },
}
EXPECTED_BASE_ARTIFACTS = {
    "manifest": {
        "path": "figures/ngboost_interval_proxy_pilot_ootang_v1/manifest.json",
        "expected_artifact_kind": base.ARTIFACT_KIND,
        "expected_artifact_status": base.ARTIFACT_STATUS,
        "expected_profile_id": "ootang-ngboost-interval-proxy-pilot-v1",
        "expected_profile_content_sha256": "1e73852dcd2406bab78f5b2495e7d4d6b9972518df85803722683b16dfbfb5ef",
    },
    "model": {
        "path": "models/ootang_ngboost_interval_proxy_pilot_v1.pkl",
        "expected_sha256": "44728c506d34820ca2bec1af0c5056575f2033e75e046bd41e559156f895a8e3",
    },
    "predictions": {
        "path": "figures/ngboost_interval_proxy_pilot_ootang_v1/predictions.csv",
        "expected_sha256": "4e66e9b767e6d0728f60ed5d3183b5722fcf9c50ad1969644b02764fd0f53725",
    },
    "metrics": {
        "path": "figures/ngboost_interval_proxy_pilot_ootang_v1/metrics.csv",
        "expected_sha256": "65b51add02f9fb06266051b7a39f87f3e03b9412c4a8cef3fcba617443f98d23",
    },
    "confusion_matrices": {
        "path": "figures/ngboost_interval_proxy_pilot_ootang_v1/confusion_matrices.csv",
        "expected_sha256": "417759f91fb9b5c8eaa4dfc8a1b0e62375c00454d6d0901e3f4143e204ebf1ae",
    },
    "reliability": {
        "path": "figures/ngboost_interval_proxy_pilot_ootang_v1/reliability.csv",
        "expected_sha256": "18a4e0f8b7bc95854d41efcf7c25a799c3db989157c5df5bbcc3f8aab197818a",
    },
}


class SensitivityProfileError(ValueError):
    """Raised when the sensitivity profile departs from the frozen contract."""


class SensitivityInputError(RuntimeError):
    """Raised when a sensitivity input or baseline fails validation."""


class SensitivityOutputError(RuntimeError):
    """Raised when a sensitivity output violates the non-ranking contract."""


def _require_equal(actual: object, expected: object, *, name: str) -> None:
    if actual != expected:
        raise SensitivityProfileError(
            f"{name} must be {expected!r}; found {actual!r}"
        )


def _resolve_repo_path(value: str | Path) -> Path:
    path = Path(value)
    return (ROOT / path).resolve() if not path.is_absolute() else path.resolve()


def _load_json(path: Path, *, source_name: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SensitivityInputError(f"Missing {source_name}: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise SensitivityInputError(f"Invalid {source_name}: {path}") from exc
    if not isinstance(payload, dict):
        raise SensitivityInputError(f"{source_name} must be a JSON object")
    return payload


def load_sensitivity_profile(
    path: Path = DEFAULT_PROFILE_PATH,
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    """Load the sensitivity profile and its locked base-pilot profile."""

    try:
        profile = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SensitivityProfileError(f"Missing sensitivity profile: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise SensitivityProfileError(f"Invalid sensitivity profile: {path}") from exc
    if not isinstance(profile, dict):
        raise SensitivityProfileError("Sensitivity profile must be an object")

    fixed = {
        "profile_id": "ootang-ngboost-interval-proxy-horizon-sensitivity-v1",
        "profile_version": "1.0-sensitivity",
        "status": "exploratory_horizon_sensitivity",
        "case": "ootang",
        "formal_warning_output": False,
        "vajont_used": False,
        "default_pipeline_member": False,
    }
    for name, expected in fixed.items():
        _require_equal(profile.get(name), expected, name=name)

    base_reference = profile.get("base_pilot_profile")
    expected_base_reference = {
        "path": "config/ootang_ngboost_interval_proxy_pilot.v1.json",
        "expected_profile_id": "ootang-ngboost-interval-proxy-pilot-v1",
        "expected_profile_version": "1.0-pilot",
        "expected_file_sha256": "a550c73828f5443a748bb3d494a428a96b1b1722c42b9daf7b03b940480d21aa",
        "expected_content_sha256": "1e73852dcd2406bab78f5b2495e7d4d6b9972518df85803722683b16dfbfb5ef",
    }
    _require_equal(
        base_reference,
        expected_base_reference,
        name="base_pilot_profile",
    )
    base_profile_path = _resolve_repo_path(base_reference["path"])
    base_profile = base.load_pilot_profile(base_profile_path)
    if base._sha256_file(base_profile_path) != base_reference["expected_file_sha256"]:
        raise SensitivityProfileError("Base pilot profile file hash changed")
    if base._canonical_json_sha256(base_profile) != base_reference["expected_content_sha256"]:
        raise SensitivityProfileError("Base pilot profile content hash changed")

    _require_equal(
        profile.get("base_pilot_artifacts"),
        EXPECTED_BASE_ARTIFACTS,
        name="base_pilot_artifacts",
    )

    expected_horizons = [
        {"horizon_days": horizon, **EXPECTED_HORIZON_CONTRACTS[horizon]}
        for horizon in HORIZONS
    ]
    _require_equal(profile.get("horizons"), expected_horizons, name="horizons")
    for record in expected_horizons:
        horizon = record["horizon_days"]
        for split in base.SPLIT_ORDER:
            rows = record["expected_sample_counts"][split]
            transitions = record["expected_transition_counts"][split]
            correct = record["expected_persistence_correct"][split]
            if correct != rows - transitions:
                raise SensitivityProfileError(
                    f"Horizon {horizon} {split} persistence counts are inconsistent"
                )

    expected_reporting = {
        "evaluation_splits": list(EVALUATION_SPLITS),
        "selection_performed": False,
        "ranking_performed": False,
        "selected_horizon_days": None,
        "optimal_horizon_days": None,
        "test_used_for_horizon_choice": False,
        "reporting_mode": "predeclared_side_by_side_nonranking",
        "summary_metrics": [
            {"name": name, "direction": direction}
            for name, direction in SUMMARY_METRICS
        ],
    }
    _require_equal(profile.get("reporting"), expected_reporting, name="reporting")

    expected_outputs = {
        "models": {
            "1": "models/ootang_ngboost_interval_proxy_horizon_sensitivity_v1_h1.pkl",
            "3": "models/ootang_ngboost_interval_proxy_horizon_sensitivity_v1_h3.pkl",
            "7": "models/ootang_ngboost_interval_proxy_horizon_sensitivity_v1_h7.pkl",
        },
        "directory": "figures/ngboost_interval_proxy_horizon_sensitivity_ootang_v1",
        "predictions": "predictions.csv",
        "metrics": "metrics.csv",
        "confusion_matrices": "confusion_matrices.csv",
        "reliability": "reliability.csv",
        "horizon_summary": "horizon_summary.csv",
        "manifest": "manifest.json",
    }
    _require_equal(profile.get("outputs"), expected_outputs, name="outputs")
    _require_equal(
        profile.get("not_claimed"),
        list(EXPECTED_NOT_CLAIMED),
        name="not_claimed",
    )
    return profile, base_profile, base_profile_path


def validate_base_pilot_artifacts(profile: dict[str, Any]) -> dict[str, Any]:
    """Require the committed h=1 pilot payloads before running sensitivity."""

    records: dict[str, Any] = {}
    for name, expected in profile["base_pilot_artifacts"].items():
        path = _resolve_repo_path(expected["path"])
        if not path.is_file():
            raise SensitivityInputError(f"Missing base pilot {name}: {path}")
        digest = base._sha256_file(path)
        if name != "manifest" and digest != expected["expected_sha256"]:
            raise SensitivityInputError(f"Base pilot {name} hash changed")
        records[name] = {"path": base._manifest_path(path), "sha256": digest}

    manifest_contract = profile["base_pilot_artifacts"]["manifest"]
    manifest = _load_json(
        _resolve_repo_path(manifest_contract["path"]),
        source_name="base pilot manifest",
    )
    required = {
        "artifact_kind": manifest_contract["expected_artifact_kind"],
        "artifact_status": manifest_contract["expected_artifact_status"],
        "case": "ootang",
        "formal_warning_output": False,
        "vajont_used": False,
        "default_pipeline_member": False,
    }
    for name, expected in required.items():
        if manifest.get(name) != expected:
            raise SensitivityInputError(f"Base pilot manifest has invalid {name}")
    manifest_profile = manifest.get("profile")
    if not isinstance(manifest_profile, dict):
        raise SensitivityInputError("Base pilot manifest lacks its profile record")
    if manifest_profile.get("id") != manifest_contract["expected_profile_id"]:
        raise SensitivityInputError("Base pilot manifest profile id changed")
    if manifest_profile.get("content_sha256") != manifest_contract[
        "expected_profile_content_sha256"
    ]:
        raise SensitivityInputError("Base pilot manifest profile content changed")
    manifest_outputs = manifest.get("outputs")
    if not isinstance(manifest_outputs, dict):
        raise SensitivityInputError("Base pilot manifest lacks output records")
    for name in (
        "model",
        "predictions",
        "metrics",
        "confusion_matrices",
        "reliability",
    ):
        output = manifest_outputs.get(name)
        expected_sha = profile["base_pilot_artifacts"][name]["expected_sha256"]
        if not isinstance(output, dict) or output.get("sha256") != expected_sha:
            raise SensitivityInputError(
                f"Base pilot manifest no longer declares the frozen {name} payload"
            )
    return records


def _horizon_profile(
    base_profile: dict[str, Any],
    sensitivity_profile: dict[str, Any],
    horizon_record: dict[str, Any],
) -> dict[str, Any]:
    profile = deepcopy(base_profile)
    profile["target"]["horizon_days"] = horizon_record["horizon_days"]
    profile["splits"]["expected_sample_counts"] = deepcopy(
        horizon_record["expected_sample_counts"]
    )
    profile["splits"]["expected_target_class_counts"] = deepcopy(
        horizon_record["expected_target_class_counts"]
    )
    profile["splits"]["expected_transition_counts"] = deepcopy(
        horizon_record["expected_transition_counts"]
    )
    profile["profile_id"] = sensitivity_profile["profile_id"]
    profile["profile_version"] = sensitivity_profile["profile_version"]
    profile["status"] = sensitivity_profile["status"]
    return profile


def _validate_persistence(
    samples: pd.DataFrame,
    horizon_record: dict[str, Any],
) -> dict[str, dict[str, float | int]]:
    result: dict[str, dict[str, float | int]] = {}
    for split in base.SPLIT_ORDER:
        group = samples.loc[samples["split"].eq(split)]
        correct = int(
            group["current_interval_level"].eq(group["target_interval_level"]).sum()
        )
        expected_correct = horizon_record["expected_persistence_correct"][split]
        if correct != expected_correct:
            raise SensitivityInputError(
                f"Unexpected horizon {horizon_record['horizon_days']} {split} persistence"
            )
        result[split] = {
            "correct": correct,
            "total": int(len(group)),
            "accuracy": correct / len(group),
        }
    return result


def _comparison_columns() -> tuple[str, ...]:
    return (
        "split",
        "date",
        "target_date",
        "station",
        *base.MODEL_FEATURES,
        *base.CONTROL_FEATURES,
        "current_interval_level",
        "current_interval_color",
        "target_interval_level",
        "target_interval_color",
        "ngboost_predicted_level",
        "ngboost_predicted_color",
        *base.PROBABILITY_COLUMNS,
        "fit_majority_predicted_level",
        "fit_majority_predicted_color",
        "persistence_predicted_level",
        "persistence_predicted_color",
        "transition_type",
        "ngboost_ordinal_error",
        "ngboost_abs_ordinal_error",
        "ngboost_false_escalation_vs_proxy",
        "ngboost_false_deescalation_vs_proxy",
        "actual",
        "p10",
        "p50",
        "p90",
        "target_actual",
        "target_p10",
        "target_p50",
        "target_p90",
    )


def verify_h1_parity(
    sensitivity_h1: pd.DataFrame,
    baseline_path: Path = DEFAULT_BASE_PREDICTIONS_PATH,
) -> dict[str, Any]:
    """Require exact h=1 calibration/test parity with the committed pilot."""

    baseline_frame = pd.read_csv(baseline_path)
    baseline_frame = baseline_frame.loc[
        baseline_frame["split"].isin(EVALUATION_SPLITS), _comparison_columns()
    ].copy()
    candidate = sensitivity_h1.loc[:, _comparison_columns()].copy()
    for frame in (baseline_frame, candidate):
        frame["date"] = pd.to_datetime(frame["date"])
        frame["target_date"] = pd.to_datetime(frame["target_date"])
        frame.sort_values(["split", "date", "station"], inplace=True, kind="stable")
        frame.reset_index(drop=True, inplace=True)
    if len(baseline_frame) != 4096 or len(candidate) != 4096:
        raise SensitivityOutputError("h=1 parity requires 4,096 evaluation rows")

    numeric_columns = [
        column
        for column in _comparison_columns()
        if pd.api.types.is_numeric_dtype(baseline_frame[column])
        and pd.api.types.is_numeric_dtype(candidate[column])
    ]
    nonnumeric_columns = [
        column for column in _comparison_columns() if column not in numeric_columns
    ]
    for column in nonnumeric_columns:
        baseline_values = (
            baseline_frame[column].astype("string").fillna("<NA>").to_numpy()
        )
        candidate_values = (
            candidate[column].astype("string").fillna("<NA>").to_numpy()
        )
        if not np.array_equal(baseline_values, candidate_values):
            raise SensitivityOutputError(
                f"h=1 categorical/key parity failed for {column}"
            )
    numeric_difference = np.abs(
        baseline_frame.loc[:, numeric_columns].to_numpy(dtype=float)
        - candidate.loc[:, numeric_columns].to_numpy(dtype=float)
    )
    max_difference = float(np.nanmax(numeric_difference))
    if not np.allclose(
        baseline_frame.loc[:, numeric_columns].to_numpy(dtype=float),
        candidate.loc[:, numeric_columns].to_numpy(dtype=float),
        rtol=0,
        atol=1e-12,
        equal_nan=True,
    ):
        raise SensitivityOutputError("h=1 numeric/probability parity failed")
    probability_difference = np.abs(
        baseline_frame.loc[:, base.PROBABILITY_COLUMNS].to_numpy(dtype=float)
        - candidate.loc[:, base.PROBABILITY_COLUMNS].to_numpy(dtype=float)
    )
    return {
        "status": "exact_within_1e-12",
        "rows": len(candidate),
        "compared_columns": list(_comparison_columns()),
        "maximum_absolute_numeric_difference": max_difference,
        "maximum_absolute_probability_difference": float(
            np.nanmax(probability_difference)
        ),
    }


def build_horizon_summary(metrics: pd.DataFrame) -> pd.DataFrame:
    """Build side-by-side raw deltas without ranking or selecting horizons."""

    rows: list[dict[str, Any]] = []
    directions = dict(SUMMARY_METRICS)
    for horizon in HORIZONS:
        for split in EVALUATION_SPLITS:
            for subset in ("all", "transition_only"):
                scoped = metrics.loc[
                    metrics["horizon_days"].eq(horizon)
                    & metrics["split"].eq(split)
                    & metrics["subset"].eq(subset)
                    & metrics["class_level"].isna()
                ]
                row_count_record = scoped.loc[
                    scoped["estimator"].eq("ngboost")
                    & scoped["metric"].eq("row_count")
                ]
                if len(row_count_record) != 1:
                    raise SensitivityOutputError("Summary row-count source is ambiguous")
                row_count = int(row_count_record.iloc[0]["value"])
                for metric, direction in SUMMARY_METRICS:
                    values: dict[str, float | None] = {}
                    statuses: dict[str, str] = {}
                    for estimator in (
                        "ngboost",
                        "current_state_persistence",
                        "fit_majority_prior",
                    ):
                        record = scoped.loc[
                            scoped["estimator"].eq(estimator)
                            & scoped["metric"].eq(metric)
                        ]
                        if len(record) != 1:
                            raise SensitivityOutputError(
                                f"Summary metric source is ambiguous: {horizon}/{split}/{subset}/{metric}/{estimator}"
                            )
                        value = record.iloc[0]["value"]
                        values[estimator] = (
                            None if pd.isna(value) else float(value)
                        )
                        statuses[estimator] = str(record.iloc[0]["status"])
                    persistence_delta = (
                        values["ngboost"] - values["current_state_persistence"]
                        if values["ngboost"] is not None
                        and values["current_state_persistence"] is not None
                        else None
                    )
                    majority_delta = (
                        values["ngboost"] - values["fit_majority_prior"]
                        if values["ngboost"] is not None
                        and values["fit_majority_prior"] is not None
                        else None
                    )
                    rows.append(
                        {
                            "horizon_days": horizon,
                            "split": split,
                            "evaluation_role": base.SPLIT_ROLES[split],
                            "subset": subset,
                            "row_count": row_count,
                            "metric": metric,
                            "metric_direction": directions[metric],
                            "ngboost_value": values["ngboost"],
                            "current_state_persistence_value": values[
                                "current_state_persistence"
                            ],
                            "ngboost_minus_current_state_persistence": persistence_delta,
                            "fit_majority_prior_value": values["fit_majority_prior"],
                            "ngboost_minus_fit_majority_prior": majority_delta,
                            "ngboost_status": statuses["ngboost"],
                            "current_state_persistence_status": statuses[
                                "current_state_persistence"
                            ],
                            "fit_majority_prior_status": statuses[
                                "fit_majority_prior"
                            ],
                        }
                    )
    result = pd.DataFrame(rows)
    if len(result) != 96:
        raise SensitivityOutputError("Horizon summary must contain exactly 96 rows")
    forbidden = {"rank", "winner", "selected_horizon", "optimal_horizon"}
    if forbidden.intersection(result.columns):
        raise SensitivityOutputError("Horizon summary must remain non-ranking")
    return result


def _validate_output_destinations(
    output_dir: Path,
    model_paths: dict[int, Path],
) -> None:
    checks = [(output_dir, DEFAULT_OUTPUT_DIR.resolve(), "output_dir")]
    checks.extend(
        (model_paths[horizon], DEFAULT_MODEL_PATHS[horizon].resolve(), f"model_h{horizon}")
        for horizon in HORIZONS
    )
    resolved_models = [model_paths[horizon].resolve() for horizon in HORIZONS]
    if len(set(resolved_models)) != len(HORIZONS):
        raise SensitivityOutputError("Sensitivity model targets must be distinct")
    resolved_output_dir = output_dir.resolve()
    table_targets = {
        (resolved_output_dir / filename).resolve() for filename in OUTPUT_FILENAMES
    }
    overlap = set(resolved_models).intersection(table_targets)
    if overlap:
        raise SensitivityOutputError(
            "Sensitivity model targets cannot overlap table or manifest outputs"
        )
    for model_path in resolved_models:
        if model_path == resolved_output_dir or model_path in resolved_output_dir.parents:
            raise SensitivityOutputError(
                "Sensitivity model targets cannot equal or contain output_dir"
            )
        if model_path.exists() and model_path.is_dir():
            raise SensitivityOutputError(
                "Sensitivity model targets must be files, not directories"
            )
    for path, expected, name in checks:
        resolved = path.resolve()
        if resolved.is_relative_to(ROOT.resolve()) and resolved != expected:
            raise SensitivityOutputError(
                f"{name} inside the repository is fixed to the sensitivity namespace"
            )


def _staged_model_paths(
    staging: Path,
    model_paths: dict[int, Path],
) -> dict[int, Path]:
    return {
        horizon: staging / f"h{horizon}-{model_paths[horizon].name}"
        for horizon in HORIZONS
    }


def _output_record(path: Path, target: Path, *, n_rows: int | None = None) -> dict[str, Any]:
    record: dict[str, Any] = {
        "path": base._manifest_path(target),
        "sha256": base._sha256_file(path),
        "size_bytes": path.stat().st_size,
    }
    if n_rows is not None:
        record["n_rows"] = n_rows
    return record


def _sensitivity_manifest(
    *,
    profile: dict[str, Any],
    profile_path: Path,
    base_profile: dict[str, Any],
    base_profile_path: Path,
    base_artifacts: dict[str, Any],
    forecast_source: dict[str, Any],
    v4_source: dict[str, Any],
    predictions_path: Path,
    kinematics_path: Path,
    thresholds_path: Path,
    horizon_records: dict[int, dict[str, Any]],
    h1_parity: dict[str, Any],
    output_records: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "artifact_kind": ARTIFACT_KIND,
        "artifact_status": ARTIFACT_STATUS,
        "case": "ootang",
        "formal_warning_output": False,
        "vajont_used": False,
        "default_pipeline_member": False,
        "profile": {
            "id": profile["profile_id"],
            "version": profile["profile_version"],
            "status": profile["status"],
            "path": base._manifest_path(profile_path),
            "file_sha256": base._sha256_file(profile_path),
            "content_sha256": base._canonical_json_sha256(profile),
        },
        "base_pilot_profile": {
            "id": base_profile["profile_id"],
            "version": base_profile["profile_version"],
            "path": base._manifest_path(base_profile_path),
            "file_sha256": base._sha256_file(base_profile_path),
            "content_sha256": base._canonical_json_sha256(base_profile),
        },
        "base_pilot_artifacts": base_artifacts,
        "git_commit": base._git_commit(),
        "git_worktree_dirty": base._git_worktree_dirty(),
        "package_versions": base._package_versions(),
        "horizons": [horizon_records[horizon] for horizon in HORIZONS],
        "reporting": profile["reporting"],
        "selection_performed": False,
        "ranking_performed": False,
        "selected_horizon_days": None,
        "optimal_horizon_days": None,
        "test_used_for_horizon_choice": False,
        "scientific_predictors": list(base.MODEL_FEATURES),
        "control_features": list(base.CONTROL_FEATURES),
        "forbidden_predictors": base_profile["features"]["forbidden"],
        "feature_order": [*base.MODEL_FEATURES, *base.CONTROL_FEATURES],
        "shared_model": base_profile["model"],
        "shared_training_policy": {
            **base_profile["training_policy"],
            "fit_split_only": True,
            "one_independent_fit_per_horizon": True,
        },
        "h1_parity": h1_parity,
        "source_inputs": {
            "predictions": {
                "path": base._manifest_path(predictions_path),
                "sha256": base._sha256_file(predictions_path),
            },
            "forecast_manifest": forecast_source,
            "kinematics": {
                "path": base._manifest_path(kinematics_path),
                "sha256": base._sha256_file(kinematics_path),
            },
            "thresholds": {
                "path": base._manifest_path(thresholds_path),
                "sha256": base._sha256_file(thresholds_path),
                "used_columns": ["station", "comparator_v0_mm_per_day"],
            },
            "v4_manifest": v4_source,
        },
        "implementation_sources": {
            "sensitivity": {
                "path": base._manifest_path(Path(__file__)),
                "sha256": base._sha256_file(Path(__file__)),
            },
            "base_pilot": {
                "path": base._manifest_path(Path(base.__file__)),
                "sha256": base._sha256_file(Path(base.__file__)),
            },
            "interval_state": {
                "path": "code/warning/interval_state.py",
                "sha256": base._sha256_file(
                    ROOT / "code" / "warning" / "interval_state.py"
                ),
            },
        },
        "outputs": output_records,
        "not_claimed": profile["not_claimed"],
    }


def write_ootang_ngboost_interval_proxy_horizon_sensitivity(
    *,
    profile_path: Path = DEFAULT_PROFILE_PATH,
    base_profile_path: Path = DEFAULT_BASE_PROFILE_PATH,
    predictions_path: Path = base.DEFAULT_PREDICTIONS_PATH,
    forecast_manifest_path: Path = base.DEFAULT_FORECAST_MANIFEST_PATH,
    kinematics_path: Path = base.DEFAULT_KINEMATICS_PATH,
    thresholds_path: Path = base.DEFAULT_THRESHOLDS_PATH,
    v4_manifest_path: Path = base.DEFAULT_V4_MANIFEST_PATH,
    base_predictions_path: Path = DEFAULT_BASE_PREDICTIONS_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    model_paths: dict[int, Path] | None = None,
) -> Path:
    """Materialize the fixed, non-ranking h=1/3/7 sensitivity bundle."""

    profile_path = Path(profile_path).resolve()
    base_profile_path = Path(base_profile_path).resolve()
    predictions_path = Path(predictions_path).resolve()
    forecast_manifest_path = Path(forecast_manifest_path).resolve()
    kinematics_path = Path(kinematics_path).resolve()
    thresholds_path = Path(thresholds_path).resolve()
    v4_manifest_path = Path(v4_manifest_path).resolve()
    base_predictions_path = Path(base_predictions_path).resolve()
    output_dir = Path(output_dir).resolve()
    model_paths = (
        {horizon: Path(path).resolve() for horizon, path in model_paths.items()}
        if model_paths is not None
        else {horizon: path.resolve() for horizon, path in DEFAULT_MODEL_PATHS.items()}
    )
    if set(model_paths) != set(HORIZONS):
        raise SensitivityOutputError("Sensitivity requires one model path per horizon")
    _validate_output_destinations(output_dir, model_paths)

    profile, base_profile, locked_base_profile_path = load_sensitivity_profile(
        profile_path
    )
    if locked_base_profile_path != base_profile_path:
        raise SensitivityProfileError("Sensitivity base profile path is fixed")
    base_artifacts = validate_base_pilot_artifacts(profile)
    if base_predictions_path != _resolve_repo_path(
        profile["base_pilot_artifacts"]["predictions"]["path"]
    ):
        raise SensitivityInputError("Base predictions path is fixed")

    forecast_source = base.validate_forecast_lineage(
        predictions_path, forecast_manifest_path
    )
    v4_source = base.validate_v4_lineage(
        kinematics_path=kinematics_path,
        predictions_path=predictions_path,
        thresholds_path=thresholds_path,
        manifest_path=v4_manifest_path,
    )
    predictions = pd.read_csv(predictions_path, usecols=base.PREDICTION_COLUMNS)
    kinematics = pd.read_csv(kinematics_path, usecols=base.KINEMATICS_COLUMNS)
    thresholds = pd.read_csv(thresholds_path, usecols=base.THRESHOLD_COLUMNS)

    combined_predictions: list[pd.DataFrame] = []
    combined_metrics: list[pd.DataFrame] = []
    combined_confusion: list[pd.DataFrame] = []
    combined_reliability: list[pd.DataFrame] = []
    models: dict[int, NGBClassifier] = {}
    fit_priors: dict[int, np.ndarray] = {}
    horizon_records: dict[int, dict[str, Any]] = {}
    h1_candidate: pd.DataFrame | None = None

    for horizon_record in profile["horizons"]:
        horizon = int(horizon_record["horizon_days"])
        horizon_profile = _horizon_profile(
            base_profile, profile, horizon_record
        )
        samples = base.build_future_interval_proxy_samples(
            predictions,
            kinematics,
            thresholds,
            horizon_profile,
        )
        persistence = _validate_persistence(samples, horizon_record)
        model, predicted, priors = base.fit_predict_pilot(samples, horizon_profile)
        models[horizon] = model
        fit_priors[horizon] = priors

        evaluation_predictions = predicted.loc[
            predicted["split"].isin(EVALUATION_SPLITS)
        ].copy()
        if horizon == 1:
            h1_candidate = evaluation_predictions.copy()
        evaluation_predictions["horizon_days"] = horizon
        evaluation_predictions["artifact_kind"] = ARTIFACT_KIND
        evaluation_predictions["artifact_status"] = ARTIFACT_STATUS
        combined_predictions.append(evaluation_predictions)

        metrics = base.build_metric_rows(
            predicted,
            priors,
            base_profile["evaluation"]["reliability_bins"],
        )
        metrics = metrics.loc[metrics["split"].isin(EVALUATION_SPLITS)].copy()
        metrics.insert(0, "horizon_days", horizon)
        combined_metrics.append(metrics)

        confusion = base.build_confusion_rows(predicted)
        confusion = confusion.loc[
            confusion["split"].isin(EVALUATION_SPLITS)
        ].copy()
        confusion.insert(0, "horizon_days", horizon)
        combined_confusion.append(confusion)

        reliability = base.build_reliability_rows(
            predicted,
            priors,
            base_profile["evaluation"]["reliability_bins"],
        )
        reliability = reliability.loc[
            reliability["split"].isin(EVALUATION_SPLITS)
        ].copy()
        reliability.insert(0, "horizon_days", horizon)
        combined_reliability.append(reliability)

        horizon_records[horizon] = {
            "horizon_days": horizon,
            "terminal_source_rows_excluded": horizon_record[
                "terminal_source_rows_excluded"
            ],
            "sample_counts": horizon_record["expected_sample_counts"],
            "target_class_support": horizon_record[
                "expected_target_class_counts"
            ],
            "transition_counts": horizon_record["expected_transition_counts"],
            "persistence": persistence,
            "fit_class_priors": {
                base.WARNING_COLORS[level]: float(priors[level])
                for level in base.CLASS_LEVELS
            },
            "fitted_boosting_iterations": int(len(model.base_models)),
            "calibration_red_support": 0,
        }

    if h1_candidate is None:
        raise SensitivityOutputError("Sensitivity did not produce h=1 output")
    h1_parity = verify_h1_parity(h1_candidate, base_predictions_path)

    prediction_frame = pd.concat(combined_predictions, ignore_index=True)
    metrics_frame = pd.concat(combined_metrics, ignore_index=True)
    confusion_frame = pd.concat(combined_confusion, ignore_index=True)
    reliability_frame = pd.concat(combined_reliability, ignore_index=True)
    summary_frame = build_horizon_summary(metrics_frame)
    expected_rows = {
        "predictions": 12160,
        "metrics": 1584,
        "confusion_matrices": 900,
        "reliability": 1440,
        "horizon_summary": 96,
    }
    actual_rows = {
        "predictions": len(prediction_frame),
        "metrics": len(metrics_frame),
        "confusion_matrices": len(confusion_frame),
        "reliability": len(reliability_frame),
        "horizon_summary": len(summary_frame),
    }
    if actual_rows != expected_rows:
        raise SensitivityOutputError(
            f"Sensitivity output row counts changed: {actual_rows}"
        )

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    for path in model_paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".ootang-ngboost-horizon-sensitivity-", dir=ROOT
    ) as directory:
        staging = Path(directory)
        staged_models = _staged_model_paths(staging, model_paths)
        for horizon in HORIZONS:
            bundle = {
                "artifact_kind": ARTIFACT_KIND,
                "artifact_status": ARTIFACT_STATUS,
                "case": "ootang",
                "formal_warning_output": False,
                "vajont_used": False,
                "horizon_days": horizon,
                "profile_id": profile["profile_id"],
                "profile_content_sha256": base._canonical_json_sha256(profile),
                "base_profile_id": base_profile["profile_id"],
                "scientific_predictors": list(base.MODEL_FEATURES),
                "control_features": list(base.CONTROL_FEATURES),
                "feature_order": [*base.MODEL_FEATURES, *base.CONTROL_FEATURES],
                "class_order": list(base.WARNING_COLORS),
                "package_versions": base._package_versions(),
                "model": models[horizon],
            }
            with staged_models[horizon].open("wb") as handle:
                pickle.dump(bundle, handle, protocol=pickle.HIGHEST_PROTOCOL)

        output_names = profile["outputs"]
        staged_csvs = {
            "predictions": staging / output_names["predictions"],
            "metrics": staging / output_names["metrics"],
            "confusion_matrices": staging / output_names["confusion_matrices"],
            "reliability": staging / output_names["reliability"],
            "horizon_summary": staging / output_names["horizon_summary"],
        }
        prediction_output = prediction_frame.loc[
            :,
            ["horizon_days", *base._prediction_output_columns()],
        ].copy()
        prediction_output["date"] = pd.to_datetime(
            prediction_output["date"]
        ).dt.strftime("%Y-%m-%d")
        prediction_output["target_date"] = pd.to_datetime(
            prediction_output["target_date"]
        ).dt.strftime("%Y-%m-%d")
        frames = {
            "predictions": prediction_output,
            "metrics": metrics_frame,
            "confusion_matrices": confusion_frame,
            "reliability": reliability_frame,
            "horizon_summary": summary_frame,
        }
        for name, frame in frames.items():
            base._write_csv(frame, staged_csvs[name])

        targets = {
            **{f"model_h{horizon}": model_paths[horizon] for horizon in HORIZONS},
            **{name: output_dir / output_names[name] for name in staged_csvs},
            "manifest": output_dir / output_names["manifest"],
        }
        output_records: dict[str, dict[str, Any]] = {}
        for horizon in HORIZONS:
            output_records[f"model_h{horizon}"] = _output_record(
                staged_models[horizon], targets[f"model_h{horizon}"]
            )
        for name, frame in frames.items():
            output_records[name] = _output_record(
                staged_csvs[name], targets[name], n_rows=len(frame)
            )
        manifest = _sensitivity_manifest(
            profile=profile,
            profile_path=profile_path,
            base_profile=base_profile,
            base_profile_path=base_profile_path,
            base_artifacts=base_artifacts,
            forecast_source=forecast_source,
            v4_source=v4_source,
            predictions_path=predictions_path,
            kinematics_path=kinematics_path,
            thresholds_path=thresholds_path,
            horizon_records=horizon_records,
            h1_parity=h1_parity,
            output_records=output_records,
        )
        staged_manifest = staging / output_names["manifest"]
        staged_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        replacements = tuple(
            [
                FileReplacement(
                    staged_models[horizon], targets[f"model_h{horizon}"]
                )
                for horizon in HORIZONS
            ]
            + [
                FileReplacement(staged_csvs[name], targets[name])
                for name in staged_csvs
            ]
            + [FileReplacement(staged_manifest, targets["manifest"])]
        )
        promote_staged_files(replacements)
    return targets["manifest"]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the fixed Ootang h=1/3/7 NGBoost horizon sensitivity."
    )
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE_PATH)
    parser.add_argument(
        "--base-profile", type=Path, default=DEFAULT_BASE_PROFILE_PATH
    )
    parser.add_argument(
        "--predictions", type=Path, default=base.DEFAULT_PREDICTIONS_PATH
    )
    parser.add_argument(
        "--forecast-manifest",
        type=Path,
        default=base.DEFAULT_FORECAST_MANIFEST_PATH,
    )
    parser.add_argument(
        "--kinematics", type=Path, default=base.DEFAULT_KINEMATICS_PATH
    )
    parser.add_argument(
        "--thresholds", type=Path, default=base.DEFAULT_THRESHOLDS_PATH
    )
    parser.add_argument(
        "--v4-manifest", type=Path, default=base.DEFAULT_V4_MANIFEST_PATH
    )
    parser.add_argument(
        "--base-predictions", type=Path, default=DEFAULT_BASE_PREDICTIONS_PATH
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    manifest = write_ootang_ngboost_interval_proxy_horizon_sensitivity(
        profile_path=args.profile,
        base_profile_path=args.base_profile,
        predictions_path=args.predictions,
        forecast_manifest_path=args.forecast_manifest,
        kinematics_path=args.kinematics,
        thresholds_path=args.thresholds,
        v4_manifest_path=args.v4_manifest,
        base_predictions_path=args.base_predictions,
    )
    print(f"[ngboost-horizon-sensitivity] exploratory Ootang bundle: {manifest}")


if __name__ == "__main__":
    main()


__all__ = [
    "ARTIFACT_KIND",
    "ARTIFACT_STATUS",
    "DEFAULT_MODEL_PATHS",
    "DEFAULT_OUTPUT_DIR",
    "DEFAULT_PROFILE_PATH",
    "EVALUATION_SPLITS",
    "HORIZONS",
    "SensitivityInputError",
    "SensitivityOutputError",
    "SensitivityProfileError",
    "build_horizon_summary",
    "load_sensitivity_profile",
    "validate_base_pilot_artifacts",
    "verify_h1_parity",
    "write_ootang_ngboost_interval_proxy_horizon_sensitivity",
]
