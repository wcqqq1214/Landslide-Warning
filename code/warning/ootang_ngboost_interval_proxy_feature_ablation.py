"""Ootang-only fixed-NGBoost grouped feature ablation."""

from __future__ import annotations

import argparse
import json
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
from warning import ootang_ngboost_interval_proxy_horizon_sensitivity as sensitivity  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE_PATH = ROOT / "config" / "ootang_ngboost_interval_proxy_feature_ablation.v1.json"
DEFAULT_SENSITIVITY_PROFILE_PATH = sensitivity.DEFAULT_PROFILE_PATH
DEFAULT_SENSITIVITY_MANIFEST_PATH = sensitivity.DEFAULT_OUTPUT_DIR / "manifest.json"
DEFAULT_SENSITIVITY_PREDICTIONS_PATH = sensitivity.DEFAULT_OUTPUT_DIR / "predictions.csv"
DEFAULT_SENSITIVITY_METRICS_PATH = sensitivity.DEFAULT_OUTPUT_DIR / "metrics.csv"
DEFAULT_SENSITIVITY_CONFUSION_PATH = sensitivity.DEFAULT_OUTPUT_DIR / "confusion_matrices.csv"
DEFAULT_SENSITIVITY_RELIABILITY_PATH = sensitivity.DEFAULT_OUTPUT_DIR / "reliability.csv"
DEFAULT_SENSITIVITY_SUMMARY_PATH = sensitivity.DEFAULT_OUTPUT_DIR / "horizon_summary.csv"
DEFAULT_OUTPUT_DIR = ROOT / "figures" / "ngboost_interval_proxy_feature_ablation_ootang_v1"
ARTIFACT_KIND = "ootang_ngboost_interval_proxy_feature_ablation"
ARTIFACT_STATUS = "exploratory_proxy_feature_ablation_not_formal"
FEATURE_SET_ORDER = (
    "full",
    "interval_only",
    "without_interval",
    "without_velocity",
    "without_delta_v",
    "without_tangent",
    "without_station_controls",
)
SUMMARY_METRICS = (
    ("accuracy", "higher_is_better"),
    ("macro_f1_supported_classes", "higher_is_better"),
    ("balanced_accuracy_supported_classes", "higher_is_better"),
    ("ordinal_mae", "lower_is_better"),
    ("quadratic_weighted_kappa", "higher_is_better"),
    ("multiclass_log_loss", "lower_is_better"),
    ("multiclass_brier", "lower_is_better"),
    ("top_label_ece", "lower_is_better"),
    ("false_escalation_vs_proxy_rate", "lower_is_better"),
    ("false_deescalation_vs_proxy_rate", "lower_is_better"),
)
EXPECTED_NOT_CLAIMED = (
    "selected_or_optimal_feature_set",
    "feature_set_ranking",
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
EXPECTED_SENSITIVITY_ARTIFACTS = {
    "manifest": {
        "path": "figures/ngboost_interval_proxy_horizon_sensitivity_ootang_v1/manifest.json",
        "expected_artifact_kind": sensitivity.ARTIFACT_KIND,
        "expected_artifact_status": sensitivity.ARTIFACT_STATUS,
        "expected_profile_id": "ootang-ngboost-interval-proxy-horizon-sensitivity-v1",
        "expected_profile_content_sha256": "6c2ff02c77ec4dc80c710dfedd227fff842740d33ba1556b523591031e181b7d",
    },
    "model_h1": {
        "path": "models/ootang_ngboost_interval_proxy_horizon_sensitivity_v1_h1.pkl",
        "expected_sha256": "e857458eecc12b8018a07b5bc9cde67f8d091338a20d23dbe3683bcd36a89dee",
    },
    "model_h3": {
        "path": "models/ootang_ngboost_interval_proxy_horizon_sensitivity_v1_h3.pkl",
        "expected_sha256": "5a1167b028784340ae059013e8a2f8354dbc9f32fbd1d63721056d096c26c857",
    },
    "model_h7": {
        "path": "models/ootang_ngboost_interval_proxy_horizon_sensitivity_v1_h7.pkl",
        "expected_sha256": "8a5bbc36a161cd5601322937d218289b3ef4d902c83e2138d8ceea09edd5c462",
    },
    "predictions": {
        "path": "figures/ngboost_interval_proxy_horizon_sensitivity_ootang_v1/predictions.csv",
        "expected_sha256": "aa5225981fdeb1dd6205db05c923b9035b4e581c59a723a31362be74b74b2356",
    },
    "metrics": {
        "path": "figures/ngboost_interval_proxy_horizon_sensitivity_ootang_v1/metrics.csv",
        "expected_sha256": "856c1abf833fad22de780c0a562bc59244f83080f5ef0a7e7c0b06eb398b6de9",
    },
    "confusion_matrices": {
        "path": "figures/ngboost_interval_proxy_horizon_sensitivity_ootang_v1/confusion_matrices.csv",
        "expected_sha256": "533303727e25e865a04688984441e63f2e91e988ae742b5b0c7b8b7599086803",
    },
    "reliability": {
        "path": "figures/ngboost_interval_proxy_horizon_sensitivity_ootang_v1/reliability.csv",
        "expected_sha256": "e6ba37956fc87f8ceb312d80d195707bf3654158fa1b8b871ed5271f95fd8ee9",
    },
    "horizon_summary": {
        "path": "figures/ngboost_interval_proxy_horizon_sensitivity_ootang_v1/horizon_summary.csv",
        "expected_sha256": "aac1fc92bce26969a09f5987b66d3b7ebf93b0c28d1039e9b88cc964397aa667",
    },
}
EXPECTED_FEATURE_SETS = (
    {
        "name": "full",
        "scientific_features": list(base.MODEL_FEATURES),
        "control_features": list(base.CONTROL_FEATURES),
        "removed_groups": [],
    },
    {
        "name": "interval_only",
        "scientific_features": ["interval_z"],
        "control_features": list(base.CONTROL_FEATURES),
        "removed_groups": ["velocity", "delta_v", "tangent_angle"],
    },
    {
        "name": "without_interval",
        "scientific_features": [
            "velocity_mm_per_day",
            "delta_v_mm_per_day",
            "tangent_angle_degree",
        ],
        "control_features": list(base.CONTROL_FEATURES),
        "removed_groups": ["interval"],
    },
    {
        "name": "without_velocity",
        "scientific_features": [
            "interval_z",
            "delta_v_mm_per_day",
            "tangent_angle_degree",
        ],
        "control_features": list(base.CONTROL_FEATURES),
        "removed_groups": ["velocity"],
    },
    {
        "name": "without_delta_v",
        "scientific_features": [
            "interval_z",
            "velocity_mm_per_day",
            "tangent_angle_degree",
        ],
        "control_features": list(base.CONTROL_FEATURES),
        "removed_groups": ["delta_v"],
    },
    {
        "name": "without_tangent",
        "scientific_features": [
            "interval_z",
            "velocity_mm_per_day",
            "delta_v_mm_per_day",
        ],
        "control_features": list(base.CONTROL_FEATURES),
        "removed_groups": ["tangent_angle"],
    },
    {
        "name": "without_station_controls",
        "scientific_features": list(base.MODEL_FEATURES),
        "control_features": [],
        "removed_groups": ["station_controls"],
    },
)


class AblationProfileError(ValueError):
    """Raised when the ablation profile changes the frozen experiment."""


class AblationInputError(RuntimeError):
    """Raised when an ablation source or baseline is incompatible."""


class AblationOutputError(RuntimeError):
    """Raised when an ablation output violates parity or non-ranking rules."""


def _require_equal(actual: object, expected: object, *, name: str) -> None:
    if actual != expected:
        raise AblationProfileError(
            f"{name} must be {expected!r}; found {actual!r}"
        )


def _resolve_repo_path(value: str | Path) -> Path:
    path = Path(value)
    return (ROOT / path).resolve() if not path.is_absolute() else path.resolve()


def _load_json(path: Path, *, source_name: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AblationInputError(f"Missing {source_name}: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise AblationInputError(f"Invalid {source_name}: {path}") from exc
    if not isinstance(payload, dict):
        raise AblationInputError(f"{source_name} must be a JSON object")
    return payload


def load_ablation_profile(
    path: Path = DEFAULT_PROFILE_PATH,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], Path]:
    """Load ablation, sensitivity, and base-pilot profiles under fixed hashes."""

    try:
        profile = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AblationProfileError(f"Missing ablation profile: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise AblationProfileError(f"Invalid ablation profile: {path}") from exc
    if not isinstance(profile, dict):
        raise AblationProfileError("Ablation profile must be an object")

    fixed = {
        "profile_id": "ootang-ngboost-interval-proxy-feature-ablation-v1",
        "profile_version": "1.0-ablation",
        "status": "exploratory_feature_ablation",
        "case": "ootang",
        "formal_warning_output": False,
        "vajont_used": False,
        "default_pipeline_member": False,
    }
    for name, expected in fixed.items():
        _require_equal(profile.get(name), expected, name=name)

    reference = profile.get("base_sensitivity_profile")
    expected_reference = {
        "path": "config/ootang_ngboost_interval_proxy_horizon_sensitivity.v1.json",
        "expected_profile_id": "ootang-ngboost-interval-proxy-horizon-sensitivity-v1",
        "expected_profile_version": "1.0-sensitivity",
        "expected_file_sha256": "6363da3f31c2618824966572bc4ce66ac97d88f94091943912d2e6e059eba867",
        "expected_content_sha256": "6c2ff02c77ec4dc80c710dfedd227fff842740d33ba1556b523591031e181b7d",
    }
    _require_equal(reference, expected_reference, name="base_sensitivity_profile")
    sensitivity_profile_path = _resolve_repo_path(reference["path"])
    sensitivity_profile, base_profile, _ = sensitivity.load_sensitivity_profile(
        sensitivity_profile_path
    )
    if base._sha256_file(sensitivity_profile_path) != reference[
        "expected_file_sha256"
    ]:
        raise AblationProfileError("Sensitivity profile file hash changed")
    if base._canonical_json_sha256(sensitivity_profile) != reference[
        "expected_content_sha256"
    ]:
        raise AblationProfileError("Sensitivity profile content hash changed")

    _require_equal(
        profile.get("base_sensitivity_artifacts"),
        EXPECTED_SENSITIVITY_ARTIFACTS,
        name="base_sensitivity_artifacts",
    )
    _require_equal(profile.get("horizons"), list(sensitivity.HORIZONS), name="horizons")
    _require_equal(profile.get("feature_sets"), list(EXPECTED_FEATURE_SETS), name="feature_sets")

    expected_reporting = {
        "evaluation_splits": list(sensitivity.EVALUATION_SPLITS),
        "selection_performed": False,
        "ranking_performed": False,
        "selected_feature_set": None,
        "optimal_feature_set": None,
        "selected_horizon_days": None,
        "test_used_for_feature_selection": False,
        "reporting_mode": "predeclared_side_by_side_nonranking",
        "models_persisted": False,
        "summary_metrics": [
            {"name": name, "direction": direction}
            for name, direction in SUMMARY_METRICS
        ],
    }
    _require_equal(profile.get("reporting"), expected_reporting, name="reporting")
    expected_outputs = {
        "directory": "figures/ngboost_interval_proxy_feature_ablation_ootang_v1",
        "predictions": "predictions.csv",
        "metrics": "metrics.csv",
        "confusion_matrices": "confusion_matrices.csv",
        "reliability": "reliability.csv",
        "ablation_summary": "ablation_summary.csv",
        "manifest": "manifest.json",
    }
    _require_equal(profile.get("outputs"), expected_outputs, name="outputs")
    _require_equal(
        profile.get("not_claimed"),
        list(EXPECTED_NOT_CLAIMED),
        name="not_claimed",
    )
    return profile, sensitivity_profile, base_profile, sensitivity_profile_path


def validate_sensitivity_artifacts(profile: dict[str, Any]) -> dict[str, Any]:
    """Validate stable full-sensitivity payloads and compatible manifest fields."""

    records: dict[str, Any] = {}
    for name, contract in profile["base_sensitivity_artifacts"].items():
        path = _resolve_repo_path(contract["path"])
        if not path.is_file():
            raise AblationInputError(f"Missing sensitivity {name}: {path}")
        digest = base._sha256_file(path)
        if name != "manifest" and digest != contract["expected_sha256"]:
            raise AblationInputError(f"Sensitivity {name} payload hash changed")
        records[name] = {"path": base._manifest_path(path), "sha256": digest}

    manifest_contract = profile["base_sensitivity_artifacts"]["manifest"]
    manifest = _load_json(
        _resolve_repo_path(manifest_contract["path"]),
        source_name="sensitivity manifest",
    )
    required = {
        "artifact_kind": manifest_contract["expected_artifact_kind"],
        "artifact_status": manifest_contract["expected_artifact_status"],
        "case": "ootang",
        "formal_warning_output": False,
        "vajont_used": False,
        "selection_performed": False,
        "ranking_performed": False,
    }
    for name, expected in required.items():
        if manifest.get(name) != expected:
            raise AblationInputError(f"Sensitivity manifest has invalid {name}")
    manifest_profile = manifest.get("profile")
    if not isinstance(manifest_profile, dict):
        raise AblationInputError("Sensitivity manifest lacks profile record")
    if manifest_profile.get("id") != manifest_contract["expected_profile_id"]:
        raise AblationInputError("Sensitivity manifest profile id changed")
    if manifest_profile.get("content_sha256") != manifest_contract[
        "expected_profile_content_sha256"
    ]:
        raise AblationInputError("Sensitivity manifest profile content changed")
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        raise AblationInputError("Sensitivity manifest lacks output records")
    for name in (
        "model_h1",
        "model_h3",
        "model_h7",
        "predictions",
        "metrics",
        "confusion_matrices",
        "reliability",
        "horizon_summary",
    ):
        record = outputs.get(name)
        expected_sha = profile["base_sensitivity_artifacts"][name][
            "expected_sha256"
        ]
        if not isinstance(record, dict) or record.get("sha256") != expected_sha:
            raise AblationInputError(
                f"Sensitivity manifest no longer declares frozen {name} payload"
            )
    return records


def _target_contract(
    base_profile: dict[str, Any],
    horizon_days: int,
) -> dict[str, Any]:
    source = base_profile["target"]
    return {
        "kind": "future_raw_interval_proxy_level",
        "horizon_days": int(horizon_days),
        "source_function": source["source_function"],
        "raw_quantiles_only": source["raw_quantiles_only"],
        "class_order": source["class_order"],
        "interpretation": source["interpretation"],
    }


def build_feature_set_matrix(
    samples: pd.DataFrame,
    feature_set: dict[str, Any],
) -> tuple[pd.DataFrame, pd.Series, tuple[str, ...]]:
    """Build exactly one profile-declared feature matrix."""

    scientific = tuple(feature_set["scientific_features"])
    controls = tuple(feature_set["control_features"])
    if not scientific:
        raise AblationInputError("Each feature set needs a scientific predictor")
    if not set(scientific).issubset(set(base.MODEL_FEATURES)):
        raise AblationInputError("Feature set contains an unknown scientific predictor")
    if not set(controls).issubset(set(base.CONTROL_FEATURES)):
        raise AblationInputError("Feature set contains an unknown control predictor")
    order = (*scientific, *controls)
    if len(order) != len(set(order)):
        raise AblationInputError("Feature set contains duplicate predictors")
    missing = set(order).difference(samples.columns)
    if missing:
        raise AblationInputError(f"Samples lack ablation predictors: {sorted(missing)}")
    X = samples.loc[:, order].apply(pd.to_numeric, errors="coerce")
    y = pd.to_numeric(samples["target_interval_level"], errors="coerce").astype(int)
    if not np.isfinite(X.to_numpy(dtype=float)).all():
        raise AblationInputError("Ablation model matrix must be finite")
    if set(y.loc[samples["split"].eq("fit")]) != set(base.CLASS_LEVELS):
        raise AblationInputError("Ablation fit split must contain all five classes")
    return X, y, order


def fit_predict_feature_set(
    samples: pd.DataFrame,
    model_profile: dict[str, Any],
    feature_set: dict[str, Any],
) -> tuple[NGBClassifier, pd.DataFrame, np.ndarray, tuple[str, ...]]:
    """Fit one fixed classifier on fit only for a declared feature set."""

    X, y, feature_order = build_feature_set_matrix(samples, feature_set)
    fit_mask = samples["split"].eq("fit")
    model = base.make_ngboost_classifier(model_profile)
    model.fit(X.loc[fit_mask].to_numpy(), y.loc[fit_mask].to_numpy())
    probabilities = base._validate_probabilities(model.predict_proba(X.to_numpy()), len(X))

    predictions = samples.copy()
    for index, column in enumerate(base.PROBABILITY_COLUMNS):
        predictions[column] = probabilities[:, index]
    predictions["ngboost_predicted_level"] = probabilities.argmax(axis=1).astype(int)
    predictions["ngboost_predicted_color"] = predictions[
        "ngboost_predicted_level"
    ].map(dict(enumerate(base.WARNING_COLORS)))
    fit_support = (
        y.loc[fit_mask]
        .value_counts()
        .reindex(base.CLASS_LEVELS, fill_value=0)
        .to_numpy(dtype=float)
    )
    fit_priors = fit_support / fit_support.sum()
    majority_level = int(fit_priors.argmax())
    predictions["fit_majority_predicted_level"] = majority_level
    predictions["fit_majority_predicted_color"] = base.WARNING_COLORS[
        majority_level
    ]
    predictions["persistence_predicted_level"] = predictions[
        "current_interval_level"
    ].astype(int)
    predictions["persistence_predicted_color"] = predictions[
        "persistence_predicted_level"
    ].map(dict(enumerate(base.WARNING_COLORS)))
    predictions["ngboost_ordinal_error"] = (
        predictions["ngboost_predicted_level"]
        - predictions["target_interval_level"]
    )
    predictions["ngboost_abs_ordinal_error"] = predictions[
        "ngboost_ordinal_error"
    ].abs()
    predictions["ngboost_false_escalation_vs_proxy"] = predictions[
        "ngboost_ordinal_error"
    ].gt(0)
    predictions["ngboost_false_deescalation_vs_proxy"] = predictions[
        "ngboost_ordinal_error"
    ].lt(0)
    return model, predictions, fit_priors, feature_order


def _compare_frames(
    candidate: pd.DataFrame,
    reference: pd.DataFrame,
    columns: list[str],
    keys: list[str],
    *,
    name: str,
) -> dict[str, Any]:
    left = candidate.loc[:, columns].copy()
    right = reference.loc[:, columns].copy()
    for frame in (left, right):
        for column in ("date", "target_date"):
            if column in frame:
                frame[column] = pd.to_datetime(frame[column])
        frame.sort_values(keys, inplace=True, kind="stable")
        frame.reset_index(drop=True, inplace=True)
    if len(left) != len(right):
        raise AblationOutputError(f"Full parity row count failed for {name}")

    numeric = [
        column
        for column in columns
        if pd.api.types.is_numeric_dtype(left[column])
        and pd.api.types.is_numeric_dtype(right[column])
    ]
    text = [column for column in columns if column not in numeric]
    for column in text:
        if not np.array_equal(
            left[column].astype("string").fillna("<NA>").to_numpy(),
            right[column].astype("string").fillna("<NA>").to_numpy(),
        ):
            raise AblationOutputError(f"Full parity failed for {name}/{column}")
    left_numeric = left.loc[:, numeric].to_numpy(dtype=float)
    right_numeric = right.loc[:, numeric].to_numpy(dtype=float)
    if not np.allclose(
        left_numeric,
        right_numeric,
        rtol=0,
        atol=1e-12,
        equal_nan=True,
    ):
        raise AblationOutputError(f"Full numeric parity failed for {name}")
    difference = np.abs(left_numeric - right_numeric)
    return {
        "name": name,
        "rows": len(left),
        "columns": columns,
        "maximum_absolute_numeric_difference": float(np.nanmax(difference)),
        "status": "exact_within_1e-12",
    }


def verify_full_parity(
    *,
    predictions: pd.DataFrame,
    metrics: pd.DataFrame,
    confusion: pd.DataFrame,
    reliability: pd.DataFrame,
    reference_predictions_path: Path = sensitivity.DEFAULT_OUTPUT_DIR / "predictions.csv",
    reference_metrics_path: Path = sensitivity.DEFAULT_OUTPUT_DIR / "metrics.csv",
    reference_confusion_path: Path = sensitivity.DEFAULT_OUTPUT_DIR / "confusion_matrices.csv",
    reference_reliability_path: Path = sensitivity.DEFAULT_OUTPUT_DIR / "reliability.csv",
) -> dict[str, Any]:
    """Require the ablation full set to reproduce committed sensitivity output."""

    reference_predictions = pd.read_csv(reference_predictions_path)
    reference_metrics = pd.read_csv(reference_metrics_path)
    reference_confusion = pd.read_csv(reference_confusion_path)
    reference_reliability = pd.read_csv(reference_reliability_path)
    full_predictions = predictions.loc[predictions["feature_set"].eq("full")]
    full_metrics = metrics.loc[metrics["feature_set"].eq("full")]
    full_confusion = confusion.loc[confusion["feature_set"].eq("full")]
    full_reliability = reliability.loc[reliability["feature_set"].eq("full")]

    prediction_columns = [
        "horizon_days",
        *sensitivity._comparison_columns(),
    ]
    metric_columns = [
        "horizon_days",
        *base.METRIC_COLUMNS,
    ]
    confusion_columns = [
        "horizon_days",
        "split",
        "evaluation_role",
        "estimator",
        "subset",
        "actual_level",
        "actual_color",
        "predicted_level",
        "predicted_color",
        "count",
    ]
    reliability_columns = [
        "horizon_days",
        "split",
        "evaluation_role",
        "estimator",
        "subset",
        "reliability_kind",
        "class_level",
        "class_color",
        "bin_index",
        "bin_lower",
        "bin_upper",
        "count",
        "mean_predicted_probability",
        "observed_frequency",
        "class_support",
        "calibration_status",
    ]
    reference_metrics = reference_metrics.loc[
        reference_metrics["estimator"].eq("ngboost")
    ]
    reference_confusion = reference_confusion.loc[
        reference_confusion["estimator"].eq("ngboost")
    ]
    reference_reliability = reference_reliability.loc[
        reference_reliability["estimator"].eq("ngboost")
    ]
    return {
        "predictions": _compare_frames(
            full_predictions,
            reference_predictions,
            prediction_columns,
            ["horizon_days", "split", "date", "station"],
            name="predictions",
        ),
        "metrics": _compare_frames(
            full_metrics,
            reference_metrics,
            metric_columns,
            ["horizon_days", "split", "subset", "metric", "class_level"],
            name="metrics",
        ),
        "confusion_matrices": _compare_frames(
            full_confusion,
            reference_confusion,
            confusion_columns,
            [
                "horizon_days",
                "split",
                "subset",
                "actual_level",
                "predicted_level",
            ],
            name="confusion_matrices",
        ),
        "reliability": _compare_frames(
            full_reliability,
            reference_reliability,
            reliability_columns,
            [
                "horizon_days",
                "split",
                "subset",
                "reliability_kind",
                "class_level",
                "bin_index",
            ],
            name="reliability",
        ),
    }


def build_ablation_summary(
    metrics: pd.DataFrame,
    feature_sets: list[dict[str, Any]],
) -> pd.DataFrame:
    """Compare every fixed set against full without ranking or selection."""

    rows: list[dict[str, Any]] = []
    directions = dict(SUMMARY_METRICS)
    feature_map = {record["name"]: record for record in feature_sets}
    for feature_set in FEATURE_SET_ORDER:
        definition = feature_map[feature_set]
        for horizon in sensitivity.HORIZONS:
            for split in sensitivity.EVALUATION_SPLITS:
                for subset in ("all", "transition_only"):
                    scoped = metrics.loc[
                        metrics["horizon_days"].eq(horizon)
                        & metrics["split"].eq(split)
                        & metrics["subset"].eq(subset)
                        & metrics["class_level"].isna()
                    ]
                    full_rows = scoped.loc[scoped["feature_set"].eq("full")]
                    ablation_rows = scoped.loc[
                        scoped["feature_set"].eq(feature_set)
                    ]
                    row_count_record = ablation_rows.loc[
                        ablation_rows["metric"].eq("row_count")
                    ]
                    if len(row_count_record) != 1:
                        raise AblationOutputError("Ablation row count is ambiguous")
                    row_count = int(row_count_record.iloc[0]["value"])
                    for metric, direction in SUMMARY_METRICS:
                        full_record = full_rows.loc[full_rows["metric"].eq(metric)]
                        ablation_record = ablation_rows.loc[
                            ablation_rows["metric"].eq(metric)
                        ]
                        if len(full_record) != 1 or len(ablation_record) != 1:
                            raise AblationOutputError(
                                f"Ablation summary source is ambiguous: {feature_set}/{horizon}/{split}/{subset}/{metric}"
                            )
                        full_value = full_record.iloc[0]["value"]
                        ablation_value = ablation_record.iloc[0]["value"]
                        full_value = None if pd.isna(full_value) else float(full_value)
                        ablation_value = (
                            None if pd.isna(ablation_value) else float(ablation_value)
                        )
                        delta = (
                            ablation_value - full_value
                            if ablation_value is not None and full_value is not None
                            else None
                        )
                        rows.append(
                            {
                                "feature_set": feature_set,
                                "scientific_features": ";".join(
                                    definition["scientific_features"]
                                ),
                                "control_features": (
                                    ";".join(definition["control_features"])
                                    or "<none>"
                                ),
                                "removed_groups": (
                                    ";".join(definition["removed_groups"])
                                    or "<none>"
                                ),
                                "horizon_days": horizon,
                                "split": split,
                                "evaluation_role": base.SPLIT_ROLES[split],
                                "subset": subset,
                                "row_count": row_count,
                                "metric": metric,
                                "metric_direction": directions[metric],
                                "full_value": full_value,
                                "ablation_value": ablation_value,
                                "ablation_minus_full": delta,
                                "full_status": str(full_record.iloc[0]["status"]),
                                "ablation_status": str(
                                    ablation_record.iloc[0]["status"]
                                ),
                            }
                        )
    result = pd.DataFrame(rows)
    if len(result) != 840:
        raise AblationOutputError("Ablation summary must contain 840 rows")
    forbidden = {"rank", "winner", "selected", "optimal"}
    if any(
        token in column.lower() for column in result.columns for token in forbidden
    ):
        raise AblationOutputError("Ablation summary must remain non-ranking")
    return result


def _validate_output_destination(output_dir: Path) -> None:
    resolved = output_dir.resolve()
    if resolved.is_relative_to(ROOT.resolve()) and resolved != DEFAULT_OUTPUT_DIR.resolve():
        raise AblationOutputError(
            "Repository output_dir is fixed to the ablation namespace"
        )
    if resolved.exists() and resolved.is_file():
        raise AblationOutputError("Ablation output_dir must be a directory")


def _output_record(
    path: Path,
    target: Path,
    *,
    n_rows: int,
) -> dict[str, Any]:
    return {
        "path": base._manifest_path(target),
        "sha256": base._sha256_file(path),
        "size_bytes": path.stat().st_size,
        "n_rows": n_rows,
    }


def _manifest(
    *,
    profile: dict[str, Any],
    profile_path: Path,
    sensitivity_profile: dict[str, Any],
    sensitivity_profile_path: Path,
    base_profile: dict[str, Any],
    sensitivity_artifacts: dict[str, Any],
    forecast_source: dict[str, Any],
    v4_source: dict[str, Any],
    predictions_path: Path,
    kinematics_path: Path,
    thresholds_path: Path,
    fit_records: list[dict[str, Any]],
    full_parity: dict[str, Any],
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
        "base_sensitivity_profile": {
            "id": sensitivity_profile["profile_id"],
            "version": sensitivity_profile["profile_version"],
            "path": base._manifest_path(sensitivity_profile_path),
            "file_sha256": base._sha256_file(sensitivity_profile_path),
            "content_sha256": base._canonical_json_sha256(sensitivity_profile),
        },
        "base_sensitivity_artifacts": sensitivity_artifacts,
        "git_commit": base._git_commit(),
        "git_worktree_dirty": base._git_worktree_dirty(),
        "package_versions": base._package_versions(),
        "horizons": list(sensitivity.HORIZONS),
        "targets_by_horizon": {
            str(horizon): _target_contract(base_profile, horizon)
            for horizon in sensitivity.HORIZONS
        },
        "feature_sets": profile["feature_sets"],
        "reporting": profile["reporting"],
        "selection_performed": False,
        "ranking_performed": False,
        "selected_feature_set": None,
        "optimal_feature_set": None,
        "selected_horizon_days": None,
        "test_used_for_feature_selection": False,
        "models_persisted": False,
        "fit_count": len(fit_records),
        "fit_records": fit_records,
        "full_parity": full_parity,
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
        "shared_contract": {
            "target_source_function": base_profile["target"]["source_function"],
            "target_raw_quantiles_only": base_profile["target"][
                "raw_quantiles_only"
            ],
            "target_class_order": base_profile["target"]["class_order"],
            "target_interpretation": base_profile["target"]["interpretation"],
            "full_scientific_features": list(base.MODEL_FEATURES),
            "station_control_features": list(base.CONTROL_FEATURES),
            "model": base_profile["model"],
            "training_policy": base_profile["training_policy"],
        },
        "implementation_sources": {
            "ablation": {
                "path": base._manifest_path(Path(__file__)),
                "sha256": base._sha256_file(Path(__file__)),
            },
            "pilot": {
                "path": base._manifest_path(Path(base.__file__)),
                "sha256": base._sha256_file(Path(base.__file__)),
            },
            "sensitivity": {
                "path": base._manifest_path(Path(sensitivity.__file__)),
                "sha256": base._sha256_file(Path(sensitivity.__file__)),
            },
        },
        "outputs": output_records,
        "not_claimed": profile["not_claimed"],
    }


def write_ootang_ngboost_interval_proxy_feature_ablation(
    *,
    profile_path: Path = DEFAULT_PROFILE_PATH,
    sensitivity_profile_path: Path = DEFAULT_SENSITIVITY_PROFILE_PATH,
    predictions_path: Path = base.DEFAULT_PREDICTIONS_PATH,
    forecast_manifest_path: Path = base.DEFAULT_FORECAST_MANIFEST_PATH,
    kinematics_path: Path = base.DEFAULT_KINEMATICS_PATH,
    thresholds_path: Path = base.DEFAULT_THRESHOLDS_PATH,
    v4_manifest_path: Path = base.DEFAULT_V4_MANIFEST_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> Path:
    """Materialize the fixed 7-set x 3-horizon ablation bundle."""

    profile_path = Path(profile_path).resolve()
    sensitivity_profile_path = Path(sensitivity_profile_path).resolve()
    predictions_path = Path(predictions_path).resolve()
    forecast_manifest_path = Path(forecast_manifest_path).resolve()
    kinematics_path = Path(kinematics_path).resolve()
    thresholds_path = Path(thresholds_path).resolve()
    v4_manifest_path = Path(v4_manifest_path).resolve()
    output_dir = Path(output_dir).resolve()
    _validate_output_destination(output_dir)

    profile, sensitivity_profile, base_profile, locked_sensitivity_path = (
        load_ablation_profile(profile_path)
    )
    if locked_sensitivity_path != sensitivity_profile_path:
        raise AblationProfileError("Ablation sensitivity profile path is fixed")
    sensitivity_artifacts = validate_sensitivity_artifacts(profile)
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
    fit_records: list[dict[str, Any]] = []
    feature_sets = profile["feature_sets"]

    for horizon_record in sensitivity_profile["horizons"]:
        horizon = int(horizon_record["horizon_days"])
        horizon_profile = sensitivity._horizon_profile(
            base_profile,
            sensitivity_profile,
            horizon_record,
        )
        samples = base.build_future_interval_proxy_samples(
            predictions,
            kinematics,
            thresholds,
            horizon_profile,
        )
        for feature_set in feature_sets:
            model, predicted, priors, feature_order = fit_predict_feature_set(
                samples,
                horizon_profile,
                feature_set,
            )
            evaluated = predicted.loc[
                predicted["split"].isin(sensitivity.EVALUATION_SPLITS)
            ].copy()
            evaluated.insert(0, "horizon_days", horizon)
            evaluated.insert(0, "feature_set", feature_set["name"])
            evaluated.insert(
                2,
                "scientific_features",
                ";".join(feature_set["scientific_features"]),
            )
            evaluated.insert(
                3,
                "control_features",
                ";".join(feature_set["control_features"]) or "<none>",
            )
            evaluated["artifact_kind"] = ARTIFACT_KIND
            evaluated["artifact_status"] = ARTIFACT_STATUS
            combined_predictions.append(evaluated)

            metrics = base.build_metric_rows(
                predicted,
                priors,
                base_profile["evaluation"]["reliability_bins"],
            )
            metrics = metrics.loc[
                metrics["split"].isin(sensitivity.EVALUATION_SPLITS)
                & metrics["estimator"].eq("ngboost")
            ].copy()
            metrics.insert(0, "horizon_days", horizon)
            metrics.insert(0, "feature_set", feature_set["name"])
            combined_metrics.append(metrics)

            confusion = base.build_confusion_rows(predicted)
            confusion = confusion.loc[
                confusion["split"].isin(sensitivity.EVALUATION_SPLITS)
                & confusion["estimator"].eq("ngboost")
            ].copy()
            confusion.insert(0, "horizon_days", horizon)
            confusion.insert(0, "feature_set", feature_set["name"])
            combined_confusion.append(confusion)

            reliability = base.build_reliability_rows(
                predicted,
                priors,
                base_profile["evaluation"]["reliability_bins"],
            )
            reliability = reliability.loc[
                reliability["split"].isin(sensitivity.EVALUATION_SPLITS)
                & reliability["estimator"].eq("ngboost")
            ].copy()
            reliability.insert(0, "horizon_days", horizon)
            reliability.insert(0, "feature_set", feature_set["name"])
            combined_reliability.append(reliability)

            fit_rows = int(samples["split"].eq("fit").sum())
            fit_records.append(
                {
                    "feature_set": feature_set["name"],
                    "horizon_days": horizon,
                    "target": _target_contract(base_profile, horizon),
                    "scientific_features": feature_set["scientific_features"],
                    "control_features": feature_set["control_features"],
                    "feature_order": list(feature_order),
                    "fit_rows": fit_rows,
                    "fit_class_support": horizon_record[
                        "expected_target_class_counts"
                    ]["fit"],
                    "fitted_boosting_iterations": int(len(model.base_models)),
                    "model_class": type(model).__name__,
                    "model_parameters": base_profile["model"],
                    "sample_weight_used": False,
                    "validation_input_used": False,
                    "resampling_used": False,
                    "model_persisted": False,
                }
            )

    prediction_frame = pd.concat(combined_predictions, ignore_index=True)
    metrics_frame = pd.concat(combined_metrics, ignore_index=True)
    confusion_frame = pd.concat(combined_confusion, ignore_index=True)
    reliability_frame = pd.concat(combined_reliability, ignore_index=True)
    summary_frame = build_ablation_summary(metrics_frame, feature_sets)

    expected_rows = {
        "predictions": 85120,
        "metrics": 4116,
        "confusion_matrices": 2100,
        "reliability": 5040,
        "ablation_summary": 840,
    }
    actual_rows = {
        "predictions": len(prediction_frame),
        "metrics": len(metrics_frame),
        "confusion_matrices": len(confusion_frame),
        "reliability": len(reliability_frame),
        "ablation_summary": len(summary_frame),
    }
    if actual_rows != expected_rows:
        raise AblationOutputError(f"Ablation row counts changed: {actual_rows}")
    if len(fit_records) != 21:
        raise AblationOutputError("Ablation must perform exactly 21 fixed fits")

    full_parity = verify_full_parity(
        predictions=prediction_frame,
        metrics=metrics_frame,
        confusion=confusion_frame,
        reliability=reliability_frame,
    )

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".ootang-ngboost-feature-ablation-", dir=ROOT
    ) as directory:
        staging = Path(directory)
        output_names = profile["outputs"]
        staged = {
            "predictions": staging / output_names["predictions"],
            "metrics": staging / output_names["metrics"],
            "confusion_matrices": staging / output_names["confusion_matrices"],
            "reliability": staging / output_names["reliability"],
            "ablation_summary": staging / output_names["ablation_summary"],
        }
        prediction_columns = [
            "feature_set",
            "horizon_days",
            "scientific_features",
            "control_features",
            *base._prediction_output_columns(),
        ]
        prediction_output = prediction_frame.loc[:, prediction_columns].copy()
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
            "ablation_summary": summary_frame,
        }
        for name, frame in frames.items():
            base._write_csv(frame, staged[name])
        targets = {
            name: output_dir / output_names[name] for name in staged
        }
        targets["manifest"] = output_dir / output_names["manifest"]
        output_records = {
            name: _output_record(staged[name], targets[name], n_rows=len(frames[name]))
            for name in staged
        }
        manifest = _manifest(
            profile=profile,
            profile_path=profile_path,
            sensitivity_profile=sensitivity_profile,
            sensitivity_profile_path=sensitivity_profile_path,
            base_profile=base_profile,
            sensitivity_artifacts=sensitivity_artifacts,
            forecast_source=forecast_source,
            v4_source=v4_source,
            predictions_path=predictions_path,
            kinematics_path=kinematics_path,
            thresholds_path=thresholds_path,
            fit_records=fit_records,
            full_parity=full_parity,
            output_records=output_records,
        )
        staged_manifest = staging / output_names["manifest"]
        staged_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        replacements = tuple(
            [FileReplacement(staged[name], targets[name]) for name in staged]
            + [FileReplacement(staged_manifest, targets["manifest"])]
        )
        promote_staged_files(replacements)
    return targets["manifest"]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the fixed Ootang NGBoost grouped feature ablation."
    )
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE_PATH)
    parser.add_argument(
        "--sensitivity-profile",
        type=Path,
        default=DEFAULT_SENSITIVITY_PROFILE_PATH,
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    manifest = write_ootang_ngboost_interval_proxy_feature_ablation(
        profile_path=args.profile,
        sensitivity_profile_path=args.sensitivity_profile,
        predictions_path=args.predictions,
        forecast_manifest_path=args.forecast_manifest,
        kinematics_path=args.kinematics,
        thresholds_path=args.thresholds,
        v4_manifest_path=args.v4_manifest,
    )
    print(f"[ngboost-feature-ablation] exploratory Ootang bundle: {manifest}")


if __name__ == "__main__":
    main()


__all__ = [
    "ARTIFACT_KIND",
    "ARTIFACT_STATUS",
    "DEFAULT_OUTPUT_DIR",
    "DEFAULT_PROFILE_PATH",
    "FEATURE_SET_ORDER",
    "AblationInputError",
    "AblationOutputError",
    "AblationProfileError",
    "build_ablation_summary",
    "build_feature_set_matrix",
    "fit_predict_feature_set",
    "load_ablation_profile",
    "validate_sensitivity_artifacts",
    "verify_full_parity",
    "write_ootang_ngboost_interval_proxy_feature_ablation",
]
