"""Ootang-only NGBoost pilot for next-day interval-risk proxy states.

This module is deliberately isolated from the ConvLSTM and operational-v4
implementations.  It consumes their versioned artifacts without retraining or
rewriting either model, and it never produces a formal warning output.
"""

from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
import pickle
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

import numpy as np
import pandas as pd
from ngboost import NGBClassifier
from ngboost.distns import k_categorical
from ngboost.scores import LogScore
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    cohen_kappa_score,
    confusion_matrix,
    f1_score,
    log_loss,
    precision_recall_fscore_support,
    roc_auc_score,
)
from sklearn.tree import DecisionTreeRegressor


CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning.draft_evidence import (  # noqa: E402
    FileReplacement,
    OOTANG_STATIONS,
    promote_staged_files,
)
from warning.interval_state import classify_observed_interval_states  # noqa: E402
from warning.levels import WARNING_COLORS, WARNING_LEVELS  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE_PATH = ROOT / "config" / "ootang_ngboost_interval_proxy_pilot.v1.json"
DEFAULT_PREDICTIONS_PATH = ROOT / "figures" / "convlstm" / "forecast_predictions.csv"
DEFAULT_FORECAST_MANIFEST_PATH = ROOT / "figures" / "convlstm" / "forecast_run_manifest.json"
DEFAULT_KINEMATICS_PATH = ROOT / "data" / "ootang_kinematics_long.csv"
DEFAULT_THRESHOLDS_PATH = ROOT / "figures" / "warning_operational_draft_v4" / "ootang_operational_thresholds.csv"
DEFAULT_V4_MANIFEST_PATH = ROOT / "figures" / "warning_operational_draft_v4" / "ootang_operational_run_manifest.json"
DEFAULT_MODEL_PATH = ROOT / "models" / "ootang_ngboost_interval_proxy_pilot_v1.pkl"
DEFAULT_OUTPUT_DIR = ROOT / "figures" / "ngboost_interval_proxy_pilot_ootang_v1"

ARTIFACT_KIND = "ootang_ngboost_next_day_interval_proxy_pilot"
ARTIFACT_STATUS = "exploratory_proxy_pilot_not_formal"
MODEL_FEATURES = (
    "interval_z",
    "velocity_mm_per_day",
    "delta_v_mm_per_day",
    "tangent_angle_degree",
)
EXPECTED_SCIENTIFIC_FEATURES = (
    {
        "name": "interval_z",
        "source": "current_raw_p10_p50_p90_interval_state",
        "unit": "standardized_residual",
    },
    {
        "name": "velocity_mm_per_day",
        "source": "current_point_velocity",
        "unit": "mm_per_day",
    },
    {
        "name": "delta_v_mm_per_day",
        "source": "current_velocity_increment",
        "unit": "mm_per_day",
    },
    {
        "name": "tangent_angle_degree",
        "source": "degrees_arctan_velocity_over_comparator_v0",
        "unit": "degree",
    },
)
CONTROL_FEATURES = tuple(f"station_{station}" for station in OOTANG_STATIONS)
CLASS_LEVELS = tuple(int(level) for level in WARNING_LEVELS)
SPLIT_ORDER = ("fit", "calibration", "test")
SPLIT_ROLES = {
    "fit": "training_only",
    "calibration": "chronological_evaluation_only",
    "test": "historical_holdout_evaluation_only",
}
PREDICTION_COLUMNS = (
    "date",
    "station",
    "split",
    "actual",
    "p10",
    "p50",
    "p90",
)
KINEMATICS_COLUMNS = (
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
THRESHOLD_COLUMNS = (
    "station",
    "comparator_v0_mm_per_day",
    "artifact_status",
    "case",
    "vajont_used",
    "formal_warning_output",
)
PROBABILITY_COLUMNS = tuple(f"prob_{color}" for color in WARNING_COLORS)
METRIC_COLUMNS = (
    "split",
    "evaluation_role",
    "estimator",
    "subset",
    "metric",
    "class_level",
    "class_color",
    "value",
    "numerator",
    "denominator",
    "status",
)
EXPECTED_EVALUATION_METRICS = (
    "accuracy",
    "per_class_precision_recall_f1_support",
    "macro_f1_fixed_five",
    "macro_f1_supported_classes",
    "balanced_accuracy_supported_classes",
    "ordinal_mae",
    "quadratic_weighted_kappa",
    "multiclass_log_loss",
    "multiclass_brier",
    "top_label_ece",
    "classwise_ece",
    "one_vs_rest_roc_auc",
    "one_vs_rest_pr_auc",
    "false_escalation_vs_proxy",
    "false_deescalation_vs_proxy",
)
EXPECTED_NOT_CLAIMED = (
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
    "out_of_fold_or_cross_fitted_fit_interval_features",
    "external_confirmation",
    "vajont_use_or_validation",
)
EXPECTED_FORBIDDEN_FEATURES = (
    "acceleration",
    "acceleration_status",
    "acceleration_thresholds",
    "actual_displacement_as_model_input",
    "calibrated_p10",
    "calibrated_p90",
    "qhat_mm",
    "rainfall",
    "reservoir_water_level",
    "groundwater_level",
    "temperature_or_other_environment",
    "coordinates_or_elevation",
    "spatial_blocks",
    "v4_station_or_site_fused_levels",
    "date_or_split_as_model_input",
    "future_or_target_side_fields",
)
EXPECTED_TRAINING_POLICY = {
    "hyperparameter_search": False,
    "early_stopping": False,
    "validation_input_to_fit": False,
    "refit_on_fit_plus_calibration": False,
    "sample_weight_used": False,
    "resampling_used": False,
    "smote_used": False,
    "synthetic_labels_used": False,
    "posthoc_probability_calibration": False,
    "posthoc_class_threshold_selection": False,
}


class PilotProfileError(ValueError):
    """Raised when the frozen pilot profile is changed or malformed."""


class PilotInputError(RuntimeError):
    """Raised when a source artifact violates the pilot contract."""


class PilotOutputError(RuntimeError):
    """Raised when model output cannot satisfy the artifact contract."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _manifest_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return str(resolved)


def _load_json(path: Path, *, source_name: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PilotInputError(f"Missing {source_name}: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise PilotInputError(f"Invalid {source_name}: {path}") from exc
    if not isinstance(payload, dict):
        raise PilotInputError(f"{source_name} must be a JSON object")
    return payload


def _require_equal(actual: object, expected: object, *, name: str) -> None:
    if actual != expected:
        raise PilotProfileError(f"{name} must be {expected!r}; found {actual!r}")


def load_pilot_profile(path: Path = DEFAULT_PROFILE_PATH) -> dict[str, Any]:
    """Load and fail-closed validate the version-one pilot profile."""

    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PilotProfileError(f"Missing pilot profile: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise PilotProfileError(f"Invalid pilot profile: {path}") from exc
    if not isinstance(profile, dict):
        raise PilotProfileError("Pilot profile must be a JSON object")

    fixed = {
        "profile_id": "ootang-ngboost-interval-proxy-pilot-v1",
        "profile_version": "1.0-pilot",
        "status": "exploratory_pilot",
        "case": "ootang",
        "formal_warning_output": False,
        "vajont_used": False,
        "default_pipeline_member": False,
        "stations": list(OOTANG_STATIONS),
    }
    for name, expected in fixed.items():
        _require_equal(profile.get(name), expected, name=name)

    target = profile.get("target")
    if not isinstance(target, dict):
        raise PilotProfileError("target must be an object")
    target_expected = {
        "kind": "next_calendar_day_raw_interval_proxy_level",
        "horizon_days": 1,
        "source_function": "warning.interval_state.classify_observed_interval_states",
        "raw_quantiles_only": True,
        "class_order": list(WARNING_COLORS),
        "interpretation": "future_interval_risk_proxy_not_expert_or_field_truth",
    }
    for name, expected in target_expected.items():
        _require_equal(target.get(name), expected, name=f"target.{name}")

    features = profile.get("features")
    if not isinstance(features, dict):
        raise PilotProfileError("features must be an object")
    scientific = features.get("scientific")
    _require_equal(
        scientific,
        list(EXPECTED_SCIENTIFIC_FEATURES),
        name="features.scientific",
    )
    _require_equal(features.get("controls"), list(CONTROL_FEATURES), name="features.controls")
    _require_equal(
        features.get("forbidden"),
        list(EXPECTED_FORBIDDEN_FEATURES),
        name="features.forbidden",
    )

    splits = profile.get("splits")
    if not isinstance(splits, dict):
        raise PilotProfileError("splits must be an object")
    split_expected = {
        "fit_role": SPLIT_ROLES["fit"],
        "calibration_role": SPLIT_ROLES["calibration"],
        "test_role": SPLIT_ROLES["test"],
        "require_same_split_pair": True,
        "require_exact_calendar_gap_days": 1,
        "require_all_stations_per_feature_date": True,
        "expected_sample_counts": {"fit": 7280, "calibration": 1808, "test": 2288},
        "expected_target_class_counts": {
            "fit": {"green": 3538, "blue": 2870, "yellow": 701, "orange": 83, "red": 88},
            "calibration": {"green": 531, "blue": 847, "yellow": 287, "orange": 143, "red": 0},
            "test": {"green": 584, "blue": 1039, "yellow": 182, "orange": 133, "red": 350},
        },
        "expected_transition_counts": {"fit": 575, "calibration": 151, "test": 110},
    }
    for name, expected in split_expected.items():
        _require_equal(splits.get(name), expected, name=f"splits.{name}")

    model = profile.get("model")
    if not isinstance(model, dict):
        raise PilotProfileError("model must be an object")
    model_expected = {
        "class": "NGBClassifier",
        "distribution": {"kind": "k_categorical", "classes": 5},
        "score": "LogScore",
        "base_learner": {
            "class": "DecisionTreeRegressor",
            "criterion": "friedman_mse",
            "min_samples_split": 2,
            "min_samples_leaf": 1,
            "min_weight_fraction_leaf": 0.0,
            "max_depth": 3,
            "splitter": "best",
            "random_state": 0,
        },
        "natural_gradient": True,
        "n_estimators": 500,
        "learning_rate": 0.01,
        "minibatch_frac": 1.0,
        "col_sample": 1.0,
        "verbose": False,
        "verbose_eval": 100,
        "tol": 0.0001,
        "random_state": 0,
    }
    for name, expected in model_expected.items():
        _require_equal(model.get(name), expected, name=f"model.{name}")

    training = profile.get("training_policy")
    _require_equal(
        training,
        EXPECTED_TRAINING_POLICY,
        name="training_policy",
    )

    evaluation = profile.get("evaluation")
    if not isinstance(evaluation, dict):
        raise PilotProfileError("evaluation must be an object")
    _require_equal(evaluation.get("estimators"), ["ngboost", "fit_majority_prior", "current_state_persistence"], name="evaluation.estimators")
    _require_equal(evaluation.get("subsets"), ["all", "transition_only"], name="evaluation.subsets")
    _require_equal(evaluation.get("reliability_bins"), 10, name="evaluation.reliability_bins")
    _require_equal(
        evaluation.get("metrics"),
        list(EXPECTED_EVALUATION_METRICS),
        name="evaluation.metrics",
    )

    outputs = profile.get("outputs")
    output_expected = {
        "model": "models/ootang_ngboost_interval_proxy_pilot_v1.pkl",
        "directory": "figures/ngboost_interval_proxy_pilot_ootang_v1",
        "predictions": "predictions.csv",
        "metrics": "metrics.csv",
        "confusion_matrices": "confusion_matrices.csv",
        "reliability": "reliability.csv",
        "manifest": "manifest.json",
    }
    if not isinstance(outputs, dict):
        raise PilotProfileError("outputs must be an object")
    for name, expected in output_expected.items():
        _require_equal(outputs.get(name), expected, name=f"outputs.{name}")

    not_claimed = profile.get("not_claimed")
    _require_equal(
        not_claimed,
        list(EXPECTED_NOT_CLAIMED),
        name="not_claimed",
    )
    return profile


def _require_station_date_frame(
    frame: pd.DataFrame,
    *,
    source_name: str,
    require_case: bool = False,
) -> pd.DataFrame:
    result = frame.copy()
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    result["station"] = result["station"].astype("string").str.strip()
    if result["date"].isna().any():
        raise PilotInputError(f"{source_name} contains invalid dates")
    if result["station"].isna().any() or result["station"].eq("").any():
        raise PilotInputError(f"{source_name} contains blank stations")
    if result.duplicated(["station", "date"]).any():
        raise PilotInputError(f"{source_name} contains duplicate station/date rows")
    if set(result["station"].tolist()) != set(OOTANG_STATIONS):
        raise PilotInputError(f"{source_name} must contain exactly the Ootang stations")
    if require_case:
        if "case" not in result or not result["case"].astype("string").str.strip().eq("ootang").all():
            raise PilotInputError(f"{source_name} must contain only case=ootang")
    return result


def _manifest_output_sha(manifest: dict[str, Any], key: str) -> str | None:
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        return None
    record = outputs.get(key)
    return record.get("sha256") if isinstance(record, dict) else None


def validate_forecast_lineage(
    predictions_path: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    manifest = _load_json(manifest_path, source_name="forecast manifest")
    required = {
        "schema_version": "ootang_elevation_aware_convlstm_run_v1",
        "artifact_status": "prototype_internal_not_confirmatory",
        "case": "ootang",
        "formal_warning_output": False,
        "vajont_used": False,
        "prototype_run_gate": "allowed",
        "confirmatory_evidence_gate": "blocked",
    }
    for name, expected in required.items():
        if manifest.get(name) != expected:
            raise PilotInputError(f"Forecast manifest has invalid {name}")
    actual_sha = _sha256_file(predictions_path)
    declared_sha = _manifest_output_sha(
        manifest,
        "figures/convlstm/forecast_predictions.csv",
    )
    if declared_sha != actual_sha:
        raise PilotInputError("Forecast predictions do not match their manifest")
    return {
        "path": _manifest_path(manifest_path),
        "sha256": _sha256_file(manifest_path),
        "schema_version": manifest["schema_version"],
        "prediction_sha256": actual_sha,
        "prediction_sha256_matches": True,
    }


def validate_v4_lineage(
    *,
    kinematics_path: Path,
    predictions_path: Path,
    thresholds_path: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    manifest = _load_json(manifest_path, source_name="v4 operational manifest")
    required = {
        "artifact_kind": "ootang_operational_four_indicator_run",
        "artifact_status": "operational_draft_not_formal",
        "case": "ootang",
        "formal_warning_output": False,
        "vajont_used": False,
        "ootang_stations": list(OOTANG_STATIONS),
    }
    for name, expected in required.items():
        if manifest.get(name) != expected:
            raise PilotInputError(f"v4 manifest has invalid {name}")

    source_inputs = manifest.get("source_inputs")
    outputs = manifest.get("outputs")
    if not isinstance(source_inputs, dict) or not isinstance(outputs, dict):
        raise PilotInputError("v4 manifest lacks source or output records")
    checks = (
        (source_inputs.get("kinematics"), kinematics_path, "kinematics"),
        (source_inputs.get("predictions"), predictions_path, "predictions"),
        (outputs.get("thresholds"), thresholds_path, "thresholds"),
    )
    records: dict[str, Any] = {}
    for record, path, name in checks:
        if not isinstance(record, dict) or record.get("sha256") != _sha256_file(path):
            raise PilotInputError(f"{name} does not match the v4 manifest")
        records[name] = {
            "path": _manifest_path(path),
            "sha256": record["sha256"],
        }
    return {
        "path": _manifest_path(manifest_path),
        "sha256": _sha256_file(manifest_path),
        "sources": records,
    }


def _validate_output_destinations(
    output_dir: Path,
    model_output_path: Path,
) -> None:
    """Permit fixed repository outputs or isolated paths outside the repository."""

    for path, expected, name in (
        (output_dir, DEFAULT_OUTPUT_DIR.resolve(), "output_dir"),
        (model_output_path, DEFAULT_MODEL_PATH.resolve(), "model_output_path"),
    ):
        resolved = path.resolve()
        if resolved.is_relative_to(ROOT.resolve()) and resolved != expected:
            raise PilotOutputError(
                f"{name} inside the repository is fixed to the isolated pilot path"
            )


def compute_improved_tangent_angle_degree(
    velocity: np.ndarray | pd.Series | float,
    comparator_v0: np.ndarray | pd.Series | float,
) -> np.ndarray:
    """Return the current continuous tangent-angle input in degrees."""

    velocity_values = np.asarray(velocity, dtype=float)
    v0_values = np.asarray(comparator_v0, dtype=float)
    if not np.isfinite(velocity_values).all():
        raise PilotInputError("Tangent-angle velocity input must be finite")
    if not np.isfinite(v0_values).all() or (v0_values <= 0).any():
        raise PilotInputError("Tangent-angle comparator V0 must be finite and positive")
    return np.degrees(np.arctan(velocity_values / v0_values))


def _validate_daily_groups(frame: pd.DataFrame) -> None:
    for (station, split), group in frame.groupby(["station", "split"], sort=False):
        ordered = group.sort_values("date", kind="stable")
        gaps = ordered["date"].diff().dropna().dt.days
        if not gaps.eq(1).all():
            raise PilotInputError(
                f"Interior date gap for station={station}, split={split}"
            )


def _expected_class_counts(profile: dict[str, Any], split: str) -> dict[int, int]:
    raw = profile["splits"]["expected_target_class_counts"][split]
    return {index: int(raw[color]) for index, color in enumerate(WARNING_COLORS)}


def _validate_sample_contract(samples: pd.DataFrame, profile: dict[str, Any]) -> None:
    expected_rows = profile["splits"]["expected_sample_counts"]
    expected_transitions = profile["splits"]["expected_transition_counts"]
    for split in SPLIT_ORDER:
        group = samples.loc[samples["split"].eq(split)]
        if len(group) != int(expected_rows[split]):
            raise PilotInputError(f"Unexpected {split} pilot sample count")
        actual_counts = (
            group["target_interval_level"].value_counts().reindex(CLASS_LEVELS, fill_value=0)
        ).to_dict()
        if actual_counts != _expected_class_counts(profile, split):
            raise PilotInputError(f"Unexpected {split} target class support")
        transitions = int(group["transition_type"].ne("stable").sum())
        if transitions != int(expected_transitions[split]):
            raise PilotInputError(f"Unexpected {split} transition count")


def build_future_interval_proxy_samples(
    predictions: pd.DataFrame,
    kinematics: pd.DataFrame,
    thresholds: pd.DataFrame,
    profile: dict[str, Any],
) -> pd.DataFrame:
    """Build strict same-station, same-split, next-calendar-day pilot samples."""

    missing_predictions = set(PREDICTION_COLUMNS).difference(predictions.columns)
    missing_kinematics = set(KINEMATICS_COLUMNS).difference(kinematics.columns)
    missing_thresholds = set(THRESHOLD_COLUMNS).difference(thresholds.columns)
    if missing_predictions or missing_kinematics or missing_thresholds:
        raise PilotInputError(
            "Pilot inputs lack required columns: "
            f"predictions={sorted(missing_predictions)}, "
            f"kinematics={sorted(missing_kinematics)}, "
            f"thresholds={sorted(missing_thresholds)}"
        )

    prediction_frame = _require_station_date_frame(
        predictions.loc[:, PREDICTION_COLUMNS],
        source_name="predictions",
    )
    prediction_frame["split"] = prediction_frame["split"].astype("string").str.strip()
    if set(prediction_frame["split"].tolist()) != set(SPLIT_ORDER):
        raise PilotInputError("Predictions must contain only fit/calibration/test")

    interval_frame = classify_observed_interval_states(prediction_frame)
    if not interval_frame["interval_status"].eq("valid").all():
        raise PilotInputError("Every pilot prediction row must have a valid raw interval state")

    kinematics_frame = _require_station_date_frame(
        kinematics.loc[:, KINEMATICS_COLUMNS],
        source_name="kinematics",
        require_case=True,
    )
    for column in ("dt_days", "velocity", "delta_v"):
        kinematics_frame[column] = pd.to_numeric(
            kinematics_frame[column], errors="coerce"
        )
    for column in ("time_status", "velocity_status", "delta_v_status"):
        kinematics_frame[column] = (
            kinematics_frame[column].astype("string").str.strip()
        )

    threshold_frame = thresholds.loc[:, THRESHOLD_COLUMNS].copy()
    threshold_frame["station"] = threshold_frame["station"].astype("string").str.strip()
    if threshold_frame["station"].duplicated().any() or set(
        threshold_frame["station"].tolist()
    ) != set(OOTANG_STATIONS):
        raise PilotInputError("Threshold input must contain one row per Ootang station")
    if not threshold_frame["case"].astype("string").str.strip().eq("ootang").all():
        raise PilotInputError("Threshold input must contain only case=ootang")
    if threshold_frame["vajont_used"].astype(bool).any():
        raise PilotInputError("Threshold input must retain vajont_used=false")
    if threshold_frame["formal_warning_output"].astype(bool).any():
        raise PilotInputError("Threshold input must retain formal_warning_output=false")
    if not threshold_frame["artifact_status"].astype("string").eq(
        "operational_draft_not_formal"
    ).all():
        raise PilotInputError("Threshold input must remain an operational draft")
    threshold_frame["comparator_v0_mm_per_day"] = pd.to_numeric(
        threshold_frame["comparator_v0_mm_per_day"], errors="coerce"
    )
    threshold_frame = threshold_frame.loc[
        :, ["station", "comparator_v0_mm_per_day"]
    ]

    frame = interval_frame.merge(
        kinematics_frame,
        on=["date", "station"],
        how="left",
        validate="one_to_one",
    ).merge(
        threshold_frame,
        on="station",
        how="left",
        validate="many_to_one",
    )
    if frame["case"].isna().any() or frame["comparator_v0_mm_per_day"].isna().any():
        raise PilotInputError("Every prediction row must map to kinematics and V0")
    valid_status = (
        frame["time_status"].eq("valid")
        & frame["velocity_status"].eq("valid")
        & frame["delta_v_status"].eq("valid")
    )
    if not valid_status.all():
        raise PilotInputError("Every pilot source row must have valid time/velocity/delta_v")
    numeric = frame.loc[
        :,
        ["interval_z", "velocity", "delta_v", "comparator_v0_mm_per_day"],
    ].to_numpy(dtype=float)
    if not np.isfinite(numeric).all():
        raise PilotInputError("Every pilot scientific input must be finite")

    frame = frame.rename(
        columns={
            "velocity": "velocity_mm_per_day",
            "delta_v": "delta_v_mm_per_day",
            "interval_level": "current_interval_level",
            "interval_color": "current_interval_color",
        }
    )
    frame["tangent_angle_degree"] = compute_improved_tangent_angle_degree(
        frame["velocity_mm_per_day"],
        frame["comparator_v0_mm_per_day"],
    )
    frame = frame.sort_values(["station", "split", "date"], kind="stable")
    _validate_daily_groups(frame)

    target_columns = {
        "date": "target_date",
        "actual": "target_actual",
        "p10": "target_p10",
        "p50": "target_p50",
        "p90": "target_p90",
        "current_interval_level": "target_interval_level",
        "current_interval_color": "target_interval_color",
    }
    grouped = frame.groupby(["station", "split"], sort=False)
    for source, target in target_columns.items():
        frame[target] = grouped[source].shift(-1)

    terminal = frame["target_date"].isna()
    expected_terminals = len(OOTANG_STATIONS) * len(SPLIT_ORDER)
    if int(terminal.sum()) != expected_terminals:
        raise PilotInputError("Each station/split group must have exactly one terminal row")
    samples = frame.loc[~terminal].copy()
    gap_days = (samples["target_date"] - samples["date"]).dt.days
    if not gap_days.eq(profile["target"]["horizon_days"]).all():
        raise PilotInputError("Pilot pairs must be exactly one calendar day apart")

    for station in OOTANG_STATIONS:
        samples[f"station_{station}"] = samples["station"].eq(station).astype(int)
    if not samples.loc[:, CONTROL_FEATURES].sum(axis=1).eq(1).all():
        raise PilotInputError("Every sample must have exactly one station control")

    date_counts = samples.groupby(["split", "date"], sort=False)["station"].nunique()
    if not date_counts.eq(len(OOTANG_STATIONS)).all():
        raise PilotInputError("Every retained feature date must contain all Ootang stations")

    samples["target_interval_level"] = samples["target_interval_level"].astype(int)
    samples["current_interval_level"] = samples["current_interval_level"].astype(int)
    samples["transition_type"] = np.select(
        [
            samples["target_interval_level"].gt(samples["current_interval_level"]),
            samples["target_interval_level"].lt(samples["current_interval_level"]),
        ],
        ["escalation", "deescalation"],
        default="stable",
    )
    samples["artifact_kind"] = ARTIFACT_KIND
    samples["artifact_status"] = ARTIFACT_STATUS
    samples["case"] = "ootang"
    samples["vajont_used"] = False
    samples["formal_warning_output"] = False

    split_rank = {name: index for index, name in enumerate(SPLIT_ORDER)}
    station_rank = {name: index for index, name in enumerate(OOTANG_STATIONS)}
    samples["_split_rank"] = samples["split"].map(split_rank)
    samples["_station_rank"] = samples["station"].map(station_rank)
    samples = samples.sort_values(
        ["_split_rank", "date", "_station_rank"], kind="stable"
    ).drop(columns=["_split_rank", "_station_rank"])
    samples = samples.reset_index(drop=True)
    _validate_sample_contract(samples, profile)
    return samples


def build_model_matrix(
    samples: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.Series]:
    feature_order = (*MODEL_FEATURES, *CONTROL_FEATURES)
    missing = set(feature_order).difference(samples.columns)
    if missing:
        raise PilotInputError(f"Pilot samples lack model features: {sorted(missing)}")
    X = samples.loc[:, feature_order].apply(pd.to_numeric, errors="coerce")
    y = pd.to_numeric(samples["target_interval_level"], errors="coerce").astype(int)
    if not np.isfinite(X.to_numpy(dtype=float)).all():
        raise PilotInputError("Pilot model matrix must be finite")
    if not set(y.tolist()).issubset(set(CLASS_LEVELS)):
        raise PilotInputError("Pilot target contains an invalid warning level")
    return X, y


def make_ngboost_classifier(profile: dict[str, Any]) -> NGBClassifier:
    config = profile["model"]
    base = config["base_learner"]
    learner = DecisionTreeRegressor(
        criterion=base["criterion"],
        min_samples_split=base["min_samples_split"],
        min_samples_leaf=base["min_samples_leaf"],
        min_weight_fraction_leaf=base["min_weight_fraction_leaf"],
        max_depth=base["max_depth"],
        splitter=base["splitter"],
        random_state=base["random_state"],
    )
    return NGBClassifier(
        Dist=k_categorical(config["distribution"]["classes"]),
        Score=LogScore,
        Base=learner,
        natural_gradient=config["natural_gradient"],
        n_estimators=config["n_estimators"],
        learning_rate=config["learning_rate"],
        minibatch_frac=config["minibatch_frac"],
        col_sample=config["col_sample"],
        verbose=config["verbose"],
        verbose_eval=config["verbose_eval"],
        tol=config["tol"],
        random_state=config["random_state"],
    )


def _validate_probabilities(probabilities: np.ndarray, n_rows: int) -> np.ndarray:
    values = np.asarray(probabilities, dtype=float)
    if values.shape != (n_rows, len(CLASS_LEVELS)):
        raise PilotOutputError("NGBClassifier must return one probability per five-level class")
    if not np.isfinite(values).all() or (values < 0).any():
        raise PilotOutputError("NGBClassifier probabilities must be finite and non-negative")
    if not np.allclose(values.sum(axis=1), 1.0, rtol=1e-10, atol=1e-10):
        raise PilotOutputError("NGBClassifier probability rows must sum to one")
    return values


def fit_predict_pilot(
    samples: pd.DataFrame,
    profile: dict[str, Any],
) -> tuple[NGBClassifier, pd.DataFrame, np.ndarray]:
    """Fit on fit only, then attach fixed five-class pilot predictions."""

    X, y = build_model_matrix(samples)
    fit_mask = samples["split"].eq("fit")
    fit_classes = set(y.loc[fit_mask].tolist())
    if fit_classes != set(CLASS_LEVELS):
        raise PilotInputError("Fit split must contain all five proxy classes")
    model = make_ngboost_classifier(profile)
    model.fit(X.loc[fit_mask].to_numpy(), y.loc[fit_mask].to_numpy())

    probabilities = _validate_probabilities(model.predict_proba(X.to_numpy()), len(X))
    predictions = samples.copy()
    for index, column in enumerate(PROBABILITY_COLUMNS):
        predictions[column] = probabilities[:, index]
    predictions["ngboost_predicted_level"] = probabilities.argmax(axis=1).astype(int)
    predictions["ngboost_predicted_color"] = predictions[
        "ngboost_predicted_level"
    ].map(dict(enumerate(WARNING_COLORS)))

    fit_support = (
        y.loc[fit_mask].value_counts().reindex(CLASS_LEVELS, fill_value=0).to_numpy(dtype=float)
    )
    fit_priors = fit_support / fit_support.sum()
    majority_level = int(fit_priors.argmax())
    predictions["fit_majority_predicted_level"] = majority_level
    predictions["fit_majority_predicted_color"] = WARNING_COLORS[majority_level]
    predictions["persistence_predicted_level"] = predictions[
        "current_interval_level"
    ].astype(int)
    predictions["persistence_predicted_color"] = predictions[
        "persistence_predicted_level"
    ].map(dict(enumerate(WARNING_COLORS)))
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
    return model, predictions, fit_priors


def _metric_row(
    *,
    split: str,
    estimator: str,
    subset: str,
    metric: str,
    value: float | int | None,
    status: str = "ok",
    class_level: int | None = None,
    numerator: int | float | None = None,
    denominator: int | float | None = None,
) -> dict[str, Any]:
    return {
        "split": split,
        "evaluation_role": SPLIT_ROLES[split],
        "estimator": estimator,
        "subset": subset,
        "metric": metric,
        "class_level": class_level,
        "class_color": None if class_level is None else WARNING_COLORS[class_level],
        "value": value,
        "numerator": numerator,
        "denominator": denominator,
        "status": status,
    }


def _expected_calibration_error(
    truth: np.ndarray,
    confidence: np.ndarray,
    bins: int,
) -> float:
    edges = np.linspace(0.0, 1.0, bins + 1)
    indices = np.minimum(np.digitize(confidence, edges[1:-1], right=False), bins - 1)
    total = len(truth)
    error = 0.0
    for index in range(bins):
        mask = indices == index
        if mask.any():
            error += mask.mean() * abs(float(confidence[mask].mean()) - float(truth[mask].mean()))
    return float(error) if total else float("nan")


def _hard_metric_rows(
    *,
    split: str,
    estimator: str,
    subset: str,
    y_true: np.ndarray,
    y_pred: np.ndarray,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    n_rows = len(y_true)
    if n_rows == 0:
        for metric in (
            "row_count",
            "accuracy",
            "macro_f1_fixed_five",
            "macro_f1_supported_classes",
            "balanced_accuracy_supported_classes",
            "ordinal_mae",
            "quadratic_weighted_kappa",
            "false_escalation_vs_proxy_count",
            "false_escalation_vs_proxy_rate",
            "false_deescalation_vs_proxy_count",
            "false_deescalation_vs_proxy_rate",
        ):
            rows.append(
                _metric_row(
                    split=split,
                    estimator=estimator,
                    subset=subset,
                    metric=metric,
                    value=None,
                    status="empty_subset",
                )
            )
        return rows

    rows.append(_metric_row(split=split, estimator=estimator, subset=subset, metric="row_count", value=n_rows))
    rows.append(_metric_row(split=split, estimator=estimator, subset=subset, metric="accuracy", value=float(accuracy_score(y_true, y_pred))))
    rows.append(
        _metric_row(
            split=split,
            estimator=estimator,
            subset=subset,
            metric="macro_f1_fixed_five",
            value=float(f1_score(y_true, y_pred, labels=CLASS_LEVELS, average="macro", zero_division=0)),
        )
    )
    supported = sorted(np.unique(y_true).astype(int).tolist())
    rows.append(
        _metric_row(
            split=split,
            estimator=estimator,
            subset=subset,
            metric="macro_f1_supported_classes",
            value=float(f1_score(y_true, y_pred, labels=supported, average="macro", zero_division=0)),
        )
    )
    recalls = []
    precision, recall, f1, support = precision_recall_fscore_support(
        y_true,
        y_pred,
        labels=CLASS_LEVELS,
        zero_division=0,
    )
    for level in CLASS_LEVELS:
        class_status = "ok" if int(support[level]) > 0 else "absent_target_class"
        if support[level] > 0:
            recalls.append(float(recall[level]))
        for metric, value in (
            ("precision", precision[level]),
            ("recall", recall[level]),
            ("f1", f1[level]),
            ("support", support[level]),
        ):
            rows.append(
                _metric_row(
                    split=split,
                    estimator=estimator,
                    subset=subset,
                    metric=metric,
                    class_level=level,
                    value=float(value) if class_status == "ok" or metric == "support" else None,
                    status=class_status,
                    numerator=int(support[level]) if metric == "support" else None,
                    denominator=n_rows if metric == "support" else None,
                )
            )
    rows.append(
        _metric_row(
            split=split,
            estimator=estimator,
            subset=subset,
            metric="balanced_accuracy_supported_classes",
            value=float(np.mean(recalls)),
        )
    )
    rows.append(_metric_row(split=split, estimator=estimator, subset=subset, metric="ordinal_mae", value=float(np.abs(y_pred - y_true).mean())))
    kappa = cohen_kappa_score(y_true, y_pred, labels=CLASS_LEVELS, weights="quadratic")
    rows.append(
        _metric_row(
            split=split,
            estimator=estimator,
            subset=subset,
            metric="quadratic_weighted_kappa",
            value=float(kappa) if np.isfinite(kappa) else None,
            status="ok" if np.isfinite(kappa) else "undefined",
        )
    )
    escalation = int((y_pred > y_true).sum())
    deescalation = int((y_pred < y_true).sum())
    for metric, count in (
        ("false_escalation_vs_proxy", escalation),
        ("false_deescalation_vs_proxy", deescalation),
    ):
        rows.append(_metric_row(split=split, estimator=estimator, subset=subset, metric=f"{metric}_count", value=count, numerator=count, denominator=n_rows))
        rows.append(_metric_row(split=split, estimator=estimator, subset=subset, metric=f"{metric}_rate", value=count / n_rows, numerator=count, denominator=n_rows))
    return rows


def _probability_metric_rows(
    *,
    split: str,
    estimator: str,
    subset: str,
    y_true: np.ndarray,
    probabilities: np.ndarray,
    bins: int,
) -> list[dict[str, Any]]:
    if len(y_true) == 0:
        return [
            _metric_row(
                split=split,
                estimator=estimator,
                subset=subset,
                metric=metric,
                value=None,
                status="empty_subset",
            )
            for metric in ("multiclass_log_loss", "multiclass_brier", "top_label_ece")
        ]
    rows = [
        _metric_row(
            split=split,
            estimator=estimator,
            subset=subset,
            metric="multiclass_log_loss",
            value=float(log_loss(y_true, probabilities, labels=CLASS_LEVELS)),
        )
    ]
    one_hot = np.eye(len(CLASS_LEVELS))[y_true]
    brier = float(np.mean(np.sum((probabilities - one_hot) ** 2, axis=1)))
    rows.append(_metric_row(split=split, estimator=estimator, subset=subset, metric="multiclass_brier", value=brier))
    confidence = probabilities.max(axis=1)
    correct = (probabilities.argmax(axis=1) == y_true).astype(float)
    rows.append(
        _metric_row(
            split=split,
            estimator=estimator,
            subset=subset,
            metric="top_label_ece",
            value=_expected_calibration_error(correct, confidence, bins),
        )
    )
    for level in CLASS_LEVELS:
        binary = (y_true == level).astype(int)
        if binary.sum() == 0:
            for metric in ("classwise_ece", "one_vs_rest_roc_auc", "one_vs_rest_pr_auc"):
                rows.append(
                    _metric_row(
                        split=split,
                        estimator=estimator,
                        subset=subset,
                        metric=metric,
                        class_level=level,
                        value=None,
                        status="no_positive_examples",
                    )
                )
            continue
        if binary.sum() == len(binary):
            auc_status = "no_negative_examples"
        else:
            auc_status = "ok"
        rows.append(
            _metric_row(
                split=split,
                estimator=estimator,
                subset=subset,
                metric="classwise_ece",
                class_level=level,
                value=_expected_calibration_error(binary.astype(float), probabilities[:, level], bins),
            )
        )
        rows.append(
            _metric_row(
                split=split,
                estimator=estimator,
                subset=subset,
                metric="one_vs_rest_roc_auc",
                class_level=level,
                value=float(roc_auc_score(binary, probabilities[:, level])) if auc_status == "ok" else None,
                status=auc_status,
            )
        )
        rows.append(
            _metric_row(
                split=split,
                estimator=estimator,
                subset=subset,
                metric="one_vs_rest_pr_auc",
                class_level=level,
                value=float(average_precision_score(binary, probabilities[:, level])) if auc_status == "ok" else None,
                status=auc_status,
            )
        )
    return rows


def build_metric_rows(
    predictions: pd.DataFrame,
    fit_priors: np.ndarray,
    bins: int,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for split in SPLIT_ORDER:
        split_rows = predictions.loc[predictions["split"].eq(split)].copy()
        estimators = {
            "ngboost": (
                split_rows["ngboost_predicted_level"].to_numpy(dtype=int),
                split_rows.loc[:, PROBABILITY_COLUMNS].to_numpy(dtype=float),
            ),
            "fit_majority_prior": (
                split_rows["fit_majority_predicted_level"].to_numpy(dtype=int),
                np.broadcast_to(fit_priors, (len(split_rows), len(fit_priors))),
            ),
            "current_state_persistence": (
                split_rows["persistence_predicted_level"].to_numpy(dtype=int),
                None,
            ),
        }
        for subset in ("all", "transition_only"):
            subset_rows = (
                split_rows
                if subset == "all"
                else split_rows.loc[split_rows["transition_type"].ne("stable")]
            )
            y_true = subset_rows["target_interval_level"].to_numpy(dtype=int)
            subset_indices = subset_rows.index
            for estimator, (all_pred, all_prob) in estimators.items():
                positions = split_rows.index.get_indexer(subset_indices)
                y_pred = all_pred[positions]
                rows.extend(
                    _hard_metric_rows(
                        split=split,
                        estimator=estimator,
                        subset=subset,
                        y_true=y_true,
                        y_pred=y_pred,
                    )
                )
                if all_prob is not None:
                    rows.extend(
                        _probability_metric_rows(
                            split=split,
                            estimator=estimator,
                            subset=subset,
                            y_true=y_true,
                            probabilities=all_prob[positions],
                            bins=bins,
                        )
                    )
                else:
                    for metric in ("multiclass_log_loss", "multiclass_brier", "top_label_ece"):
                        rows.append(
                            _metric_row(
                                split=split,
                                estimator=estimator,
                                subset=subset,
                                metric=metric,
                                value=None,
                                status="probability_not_available",
                            )
                        )
    return pd.DataFrame(rows, columns=METRIC_COLUMNS)


def build_confusion_rows(predictions: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    estimator_columns = {
        "ngboost": "ngboost_predicted_level",
        "fit_majority_prior": "fit_majority_predicted_level",
        "current_state_persistence": "persistence_predicted_level",
    }
    for split in SPLIT_ORDER:
        split_rows = predictions.loc[predictions["split"].eq(split)]
        for subset in ("all", "transition_only"):
            subset_rows = (
                split_rows
                if subset == "all"
                else split_rows.loc[split_rows["transition_type"].ne("stable")]
            )
            y_true = subset_rows["target_interval_level"].to_numpy(dtype=int)
            for estimator, column in estimator_columns.items():
                y_pred = subset_rows[column].to_numpy(dtype=int)
                matrix = confusion_matrix(y_true, y_pred, labels=CLASS_LEVELS)
                for actual_level in CLASS_LEVELS:
                    for predicted_level in CLASS_LEVELS:
                        rows.append(
                            {
                                "split": split,
                                "evaluation_role": SPLIT_ROLES[split],
                                "estimator": estimator,
                                "subset": subset,
                                "actual_level": actual_level,
                                "actual_color": WARNING_COLORS[actual_level],
                                "predicted_level": predicted_level,
                                "predicted_color": WARNING_COLORS[predicted_level],
                                "count": int(matrix[actual_level, predicted_level]),
                            }
                        )
    return pd.DataFrame(rows)


def _reliability_rows_for_probabilities(
    *,
    split: str,
    estimator: str,
    subset: str,
    y_true: np.ndarray,
    probabilities: np.ndarray,
    bins: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    edges = np.linspace(0.0, 1.0, bins + 1)

    top_confidence = probabilities.max(axis=1)
    top_correct = (probabilities.argmax(axis=1) == y_true).astype(float)
    top_indices = np.minimum(np.digitize(top_confidence, edges[1:-1]), bins - 1)
    for index in range(bins):
        mask = top_indices == index
        rows.append(
            {
                "split": split,
                "evaluation_role": SPLIT_ROLES[split],
                "estimator": estimator,
                "subset": subset,
                "reliability_kind": "top_label",
                "class_level": None,
                "class_color": None,
                "bin_index": index,
                "bin_lower": edges[index],
                "bin_upper": edges[index + 1],
                "count": int(mask.sum()),
                "mean_predicted_probability": float(top_confidence[mask].mean()) if mask.any() else None,
                "observed_frequency": float(top_correct[mask].mean()) if mask.any() else None,
                "class_support": len(y_true),
                "calibration_status": "ok" if mask.any() else "empty_bin",
            }
        )

    for level in CLASS_LEVELS:
        binary = (y_true == level).astype(float)
        class_probability = probabilities[:, level]
        class_indices = np.minimum(np.digitize(class_probability, edges[1:-1]), bins - 1)
        class_support = int(binary.sum())
        for index in range(bins):
            mask = class_indices == index
            if class_support == 0:
                status = "no_positive_examples"
            elif mask.any():
                status = "ok"
            else:
                status = "empty_bin"
            rows.append(
                {
                    "split": split,
                    "evaluation_role": SPLIT_ROLES[split],
                    "estimator": estimator,
                    "subset": subset,
                    "reliability_kind": "one_vs_rest",
                    "class_level": level,
                    "class_color": WARNING_COLORS[level],
                    "bin_index": index,
                    "bin_lower": edges[index],
                    "bin_upper": edges[index + 1],
                    "count": int(mask.sum()),
                    "mean_predicted_probability": float(class_probability[mask].mean()) if mask.any() else None,
                    "observed_frequency": float(binary[mask].mean()) if mask.any() and class_support > 0 else None,
                    "class_support": class_support,
                    "calibration_status": status,
                }
            )
    return rows


def build_reliability_rows(
    predictions: pd.DataFrame,
    fit_priors: np.ndarray,
    bins: int,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for split in SPLIT_ORDER:
        split_rows = predictions.loc[predictions["split"].eq(split)]
        for subset in ("all", "transition_only"):
            subset_rows = (
                split_rows
                if subset == "all"
                else split_rows.loc[split_rows["transition_type"].ne("stable")]
            )
            y_true = subset_rows["target_interval_level"].to_numpy(dtype=int)
            probability_sets = {
                "ngboost": subset_rows.loc[:, PROBABILITY_COLUMNS].to_numpy(dtype=float),
                "fit_majority_prior": np.broadcast_to(
                    fit_priors,
                    (len(subset_rows), len(fit_priors)),
                ),
            }
            for estimator, probabilities in probability_sets.items():
                rows.extend(
                    _reliability_rows_for_probabilities(
                        split=split,
                        estimator=estimator,
                        subset=subset,
                        y_true=y_true,
                        probabilities=probabilities,
                        bins=bins,
                    )
                )
    return pd.DataFrame(rows)


def _git_commit() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _git_worktree_dirty() -> bool | None:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return bool(result.stdout.strip()) if result.returncode == 0 else None


def _package_versions() -> dict[str, str]:
    names = ("ngboost", "scikit-learn", "numpy", "pandas")
    return {name: metadata.version(name) for name in names}


def _class_support(samples: pd.DataFrame) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for split in SPLIT_ORDER:
        counts = (
            samples.loc[samples["split"].eq(split), "target_interval_level"]
            .value_counts()
            .reindex(CLASS_LEVELS, fill_value=0)
        )
        result[split] = {
            WARNING_COLORS[level]: int(counts.loc[level]) for level in CLASS_LEVELS
        }
    return result


def _split_summary(samples: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for split in SPLIT_ORDER:
        group = samples.loc[samples["split"].eq(split)]
        result[split] = {
            "evaluation_role": SPLIT_ROLES[split],
            "rows": int(len(group)),
            "feature_dates": int(group["date"].nunique()),
            "feature_start": group["date"].min().date().isoformat(),
            "feature_end": group["date"].max().date().isoformat(),
            "target_start": group["target_date"].min().date().isoformat(),
            "target_end": group["target_date"].max().date().isoformat(),
            "transition_rows": int(group["transition_type"].ne("stable").sum()),
        }
    return result


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, lineterminator="\n")


def _manifest(
    *,
    profile: dict[str, Any],
    profile_path: Path,
    forecast_source: dict[str, Any],
    v4_source: dict[str, Any],
    predictions_path: Path,
    forecast_manifest_path: Path,
    kinematics_path: Path,
    thresholds_path: Path,
    v4_manifest_path: Path,
    samples: pd.DataFrame,
    fit_priors: np.ndarray,
    model: NGBClassifier,
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
            "path": _manifest_path(profile_path),
            "file_sha256": _sha256_file(profile_path),
            "content_sha256": _canonical_json_sha256(profile),
        },
        "git_commit": _git_commit(),
        "git_worktree_dirty": _git_worktree_dirty(),
        "package_versions": _package_versions(),
        "target": profile["target"],
        "scientific_predictors": list(MODEL_FEATURES),
        "control_features": list(CONTROL_FEATURES),
        "forbidden_predictors": profile["features"]["forbidden"],
        "feature_order": [*MODEL_FEATURES, *CONTROL_FEATURES],
        "split_summary": _split_summary(samples),
        "target_class_support": _class_support(samples),
        "terminal_source_rows_excluded": len(OOTANG_STATIONS) * len(SPLIT_ORDER),
        "model": {
            **profile["model"],
            "fitted_boosting_iterations": int(len(model.base_models)),
        },
        "training_policy": {
            **profile["training_policy"],
            "sample_weight_api_available": True,
            "fit_split_only": True,
        },
        "fit_class_priors": {
            WARNING_COLORS[level]: float(fit_priors[level]) for level in CLASS_LEVELS
        },
        "evaluation": {
            **profile["evaluation"],
            "calibration_red_support": 0,
            "calibration_red_metric_policy": "undefined_metrics_are_NA_with_explicit_status",
            "test_status": "historical_holdout_already_inspected_not_confirmatory",
        },
        "source_inputs": {
            "predictions": {"path": _manifest_path(predictions_path), "sha256": _sha256_file(predictions_path)},
            "forecast_manifest": {"path": _manifest_path(forecast_manifest_path), "sha256": _sha256_file(forecast_manifest_path), "audit": forecast_source},
            "kinematics": {"path": _manifest_path(kinematics_path), "sha256": _sha256_file(kinematics_path)},
            "thresholds": {"path": _manifest_path(thresholds_path), "sha256": _sha256_file(thresholds_path), "used_columns": ["station", "comparator_v0_mm_per_day"]},
            "v4_manifest": {"path": _manifest_path(v4_manifest_path), "sha256": _sha256_file(v4_manifest_path), "audit": v4_source},
        },
        "implementation_sources": {
            "pilot": {"path": _manifest_path(Path(__file__)), "sha256": _sha256_file(Path(__file__))},
            "interval_state": {"path": "code/warning/interval_state.py", "sha256": _sha256_file(ROOT / "code" / "warning" / "interval_state.py")},
        },
        "outputs": output_records,
        "not_claimed": profile["not_claimed"],
    }


def _prediction_output_columns() -> list[str]:
    return [
        "artifact_kind",
        "artifact_status",
        "case",
        "vajont_used",
        "formal_warning_output",
        "split",
        "date",
        "target_date",
        "station",
        *MODEL_FEATURES,
        *CONTROL_FEATURES,
        "current_interval_level",
        "current_interval_color",
        "target_interval_level",
        "target_interval_color",
        "ngboost_predicted_level",
        "ngboost_predicted_color",
        *PROBABILITY_COLUMNS,
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
    ]


def write_ootang_ngboost_interval_proxy_pilot(
    *,
    profile_path: Path = DEFAULT_PROFILE_PATH,
    predictions_path: Path = DEFAULT_PREDICTIONS_PATH,
    forecast_manifest_path: Path = DEFAULT_FORECAST_MANIFEST_PATH,
    kinematics_path: Path = DEFAULT_KINEMATICS_PATH,
    thresholds_path: Path = DEFAULT_THRESHOLDS_PATH,
    v4_manifest_path: Path = DEFAULT_V4_MANIFEST_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    model_output_path: Path = DEFAULT_MODEL_PATH,
) -> Path:
    """Materialize the isolated Ootang NGBoost proxy-pilot bundle."""

    profile_path = Path(profile_path).resolve()
    predictions_path = Path(predictions_path).resolve()
    forecast_manifest_path = Path(forecast_manifest_path).resolve()
    kinematics_path = Path(kinematics_path).resolve()
    thresholds_path = Path(thresholds_path).resolve()
    v4_manifest_path = Path(v4_manifest_path).resolve()
    output_dir = Path(output_dir).resolve()
    model_output_path = Path(model_output_path).resolve()
    _validate_output_destinations(output_dir, model_output_path)

    profile = load_pilot_profile(profile_path)
    forecast_source = validate_forecast_lineage(predictions_path, forecast_manifest_path)
    v4_source = validate_v4_lineage(
        kinematics_path=kinematics_path,
        predictions_path=predictions_path,
        thresholds_path=thresholds_path,
        manifest_path=v4_manifest_path,
    )

    predictions = pd.read_csv(predictions_path, usecols=PREDICTION_COLUMNS)
    kinematics = pd.read_csv(kinematics_path, usecols=KINEMATICS_COLUMNS)
    thresholds = pd.read_csv(thresholds_path, usecols=THRESHOLD_COLUMNS)
    samples = build_future_interval_proxy_samples(
        predictions,
        kinematics,
        thresholds,
        profile,
    )
    model, prediction_frame, fit_priors = fit_predict_pilot(samples, profile)
    metrics = build_metric_rows(
        prediction_frame,
        fit_priors,
        profile["evaluation"]["reliability_bins"],
    )
    confusion = build_confusion_rows(prediction_frame)
    reliability = build_reliability_rows(
        prediction_frame,
        fit_priors,
        profile["evaluation"]["reliability_bins"],
    )

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    model_output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".ootang-ngboost-interval-proxy-pilot-",
        dir=ROOT,
    ) as directory:
        staging = Path(directory)
        staged_model = staging / model_output_path.name
        staged_predictions = staging / profile["outputs"]["predictions"]
        staged_metrics = staging / profile["outputs"]["metrics"]
        staged_confusion = staging / profile["outputs"]["confusion_matrices"]
        staged_reliability = staging / profile["outputs"]["reliability"]
        staged_manifest = staging / profile["outputs"]["manifest"]

        bundle = {
            "artifact_kind": ARTIFACT_KIND,
            "artifact_status": ARTIFACT_STATUS,
            "case": "ootang",
            "formal_warning_output": False,
            "vajont_used": False,
            "profile_id": profile["profile_id"],
            "profile_content_sha256": _canonical_json_sha256(profile),
            "target": profile["target"],
            "scientific_predictors": list(MODEL_FEATURES),
            "control_features": list(CONTROL_FEATURES),
            "feature_order": [*MODEL_FEATURES, *CONTROL_FEATURES],
            "class_order": list(WARNING_COLORS),
            "package_versions": _package_versions(),
            "model": model,
        }
        with staged_model.open("wb") as handle:
            pickle.dump(bundle, handle, protocol=pickle.HIGHEST_PROTOCOL)

        prediction_output = prediction_frame.loc[:, _prediction_output_columns()].copy()
        prediction_output["date"] = pd.to_datetime(prediction_output["date"]).dt.strftime("%Y-%m-%d")
        prediction_output["target_date"] = pd.to_datetime(prediction_output["target_date"]).dt.strftime("%Y-%m-%d")
        _write_csv(prediction_output, staged_predictions)
        _write_csv(metrics, staged_metrics)
        _write_csv(confusion, staged_confusion)
        _write_csv(reliability, staged_reliability)

        targets = {
            "model": model_output_path,
            "predictions": output_dir / profile["outputs"]["predictions"],
            "metrics": output_dir / profile["outputs"]["metrics"],
            "confusion_matrices": output_dir / profile["outputs"]["confusion_matrices"],
            "reliability": output_dir / profile["outputs"]["reliability"],
            "manifest": output_dir / profile["outputs"]["manifest"],
        }
        staged = {
            "model": staged_model,
            "predictions": staged_predictions,
            "metrics": staged_metrics,
            "confusion_matrices": staged_confusion,
            "reliability": staged_reliability,
        }
        output_records = {
            name: {
                "path": _manifest_path(targets[name]),
                "sha256": _sha256_file(path),
                "size_bytes": path.stat().st_size,
                **(
                    {"n_rows": len(prediction_output)}
                    if name == "predictions"
                    else {"n_rows": len(metrics)}
                    if name == "metrics"
                    else {"n_rows": len(confusion)}
                    if name == "confusion_matrices"
                    else {"n_rows": len(reliability)}
                    if name == "reliability"
                    else {}
                ),
            }
            for name, path in staged.items()
        }
        manifest = _manifest(
            profile=profile,
            profile_path=profile_path,
            forecast_source=forecast_source,
            v4_source=v4_source,
            predictions_path=predictions_path,
            forecast_manifest_path=forecast_manifest_path,
            kinematics_path=kinematics_path,
            thresholds_path=thresholds_path,
            v4_manifest_path=v4_manifest_path,
            samples=samples,
            fit_priors=fit_priors,
            model=model,
            output_records=output_records,
        )
        staged_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        replacements = (
            FileReplacement(staged_model, targets["model"]),
            FileReplacement(staged_predictions, targets["predictions"]),
            FileReplacement(staged_metrics, targets["metrics"]),
            FileReplacement(staged_confusion, targets["confusion_matrices"]),
            FileReplacement(staged_reliability, targets["reliability"]),
            FileReplacement(staged_manifest, targets["manifest"]),
        )
        promote_staged_files(replacements)
    return targets["manifest"]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the isolated Ootang NGBoost next-day interval proxy pilot."
    )
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE_PATH)
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS_PATH)
    parser.add_argument("--forecast-manifest", type=Path, default=DEFAULT_FORECAST_MANIFEST_PATH)
    parser.add_argument("--kinematics", type=Path, default=DEFAULT_KINEMATICS_PATH)
    parser.add_argument("--thresholds", type=Path, default=DEFAULT_THRESHOLDS_PATH)
    parser.add_argument("--v4-manifest", type=Path, default=DEFAULT_V4_MANIFEST_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    manifest = write_ootang_ngboost_interval_proxy_pilot(
        profile_path=args.profile,
        predictions_path=args.predictions,
        forecast_manifest_path=args.forecast_manifest,
        kinematics_path=args.kinematics,
        thresholds_path=args.thresholds,
        v4_manifest_path=args.v4_manifest,
    )
    print(f"[ngboost-interval-proxy-pilot] exploratory Ootang bundle: {manifest}")


if __name__ == "__main__":
    main()


__all__ = [
    "ARTIFACT_KIND",
    "ARTIFACT_STATUS",
    "CLASS_LEVELS",
    "CONTROL_FEATURES",
    "DEFAULT_MODEL_PATH",
    "DEFAULT_OUTPUT_DIR",
    "DEFAULT_PROFILE_PATH",
    "MODEL_FEATURES",
    "PROBABILITY_COLUMNS",
    "PilotInputError",
    "PilotOutputError",
    "PilotProfileError",
    "build_confusion_rows",
    "build_future_interval_proxy_samples",
    "build_metric_rows",
    "build_model_matrix",
    "build_reliability_rows",
    "compute_improved_tangent_angle_degree",
    "fit_predict_pilot",
    "load_pilot_profile",
    "make_ngboost_classifier",
    "validate_forecast_lineage",
    "validate_v4_lineage",
    "write_ootang_ngboost_interval_proxy_pilot",
]
