"""Pre-registered lag-7 state-memory challenger for Ootang site states.

This module is intentionally separate from the committed classifier.  It adds
exactly one input, ``lag7_state_level``, to the same 32 current-time features.
No fitting occurs on import; call the CLI only after the matching protocol is
committed.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import pickle
import sys
import tempfile
import time
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, f1_score, log_loss, mean_absolute_error
from threadpoolctl import threadpool_limits


CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning import ootang_ngboost_auto_state as artifact_io  # noqa: E402
from warning import ootang_ngboost_auto_state_classifier as classifier  # noqa: E402
from warning.draft_evidence import FileReplacement, promote_staged_files  # noqa: E402
from warning.levels import WARNING_COLORS  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = ROOT / "config" / "ootang_ngboost_auto_state_memory.v2.json"
ARTIFACT_KIND = "ootang_ngboost_ecdf_auto_state_lag7_memory_challenger"
ARTIFACT_STATUS = "exploratory_state_memory_challenger_not_formal_warning"
MEMORY_FEATURE = "lag7_state_level"
MEMORY_SENTINEL = -1
PROBABILITY_COLUMNS = classifier.PROBABILITY_COLUMNS


@dataclass(frozen=True)
class MemoryArtifacts:
    site_predictions_path: Path
    comparison_metrics_path: Path
    feature_importance_path: Path
    model_path: Path
    manifest_path: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_config(path: Path) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        raise artifact_io.AutoStateInputError(
            f"Cannot read memory config: {path}"
        ) from exc
    fixed = {
        "schema_version": 1,
        "profile_id": "ootang_ngboost_auto_state_memory_v2",
        "profile_version": 2,
        "status": ARTIFACT_STATUS,
        "case": "ootang",
        "formal_warning_output": False,
        "default_pipeline_member": False,
        "scientific_feature_whitelist": list(classifier.SCIENTIFIC_FEATURES),
        "ngboost": {
            "classes": 5,
            "score": "LogScore",
            "base_learner": {
                "class": "DecisionTreeRegressor",
                "max_depth": 3,
                "criterion": "friedman_mse",
                "random_state": 0,
            },
            "n_estimators": 500,
            "learning_rate": 0.01,
            "minibatch_frac": 1.0,
            "col_sample": 1.0,
            "natural_gradient": True,
            "random_state": 0,
            "verbose": False,
        },
        "training_policy": {
            "hyperparameter_search": False,
            "posthoc_probability_calibration": False,
            "resampling": False,
            "predict_all_dates": True,
        },
    }
    if not isinstance(config, dict):
        raise artifact_io.AutoStateInputError("Memory config must be a JSON object")
    for key, expected in fixed.items():
        if config.get(key) != expected:
            raise artifact_io.AutoStateInputError(f"config {key} must be {expected!r}")
    if config.get("memory_feature") != {
        "name": MEMORY_FEATURE,
        "lag_days": 7,
        "source": "strict_same_fold_automatic_label_anchor_t_minus_7",
        "missing_sentinel": MEMORY_SENTINEL,
        "availability_feature": False,
    }:
        raise artifact_io.AutoStateInputError("Invalid fixed memory feature")
    if config.get("site_task") != {
        "base_feature_count": 32,
        "total_feature_count": 33,
        "target": "site_auto_state_level",
        "fit_fold": 1,
        "fit_valid_rows": 280,
    }:
        raise artifact_io.AutoStateInputError("Invalid fixed memory site task")
    comparison = config.get("comparison", {})
    if comparison.get("metric_folds") != [2] or comparison.get(
        "prediction_only_folds"
    ) != [3]:
        raise artifact_io.AutoStateInputError(
            "Metrics must be fold 2 only; fold 3 is prediction only"
        )
    if (
        comparison.get("lag7_common_expected_rows_per_fold") != 273
        or comparison.get("fold2_transition_expected_rows") != 11
    ):
        raise artifact_io.AutoStateInputError("Invalid fixed fold-2 comparison counts")
    if comparison.get("decision", {}).get("log_loss_absolute_upper") != 1.6094:
        raise artifact_io.AutoStateInputError("Invalid pre-registered log-loss bound")
    expected_inputs = {
        "ecdf_station_labels",
        "ecdf_site_labels",
        "ecdf_label_gate",
        "ecdf_manifest",
        "classifier_site_predictions",
        "classifier_manifest",
    }
    if (
        not isinstance(config.get("inputs"), dict)
        or set(config["inputs"]) != expected_inputs
    ):
        raise artifact_io.AutoStateInputError("Invalid memory input set")
    for name, value in config["inputs"].items():
        artifact_io._resolve_config_path(value, name=f"inputs.{name}")
    artifact_io._resolve_config_path(config.get("output_dir"), name="output_dir")
    artifact_io._resolve_config_path(config.get("model_output"), name="model_output")
    if not isinstance(config.get("outputs"), dict) or set(config["outputs"]) != {
        "site_predictions",
        "comparison_metrics",
        "feature_importance",
        "manifest",
    }:
        raise artifact_io.AutoStateInputError("Invalid memory output set")
    return config


def _memory_feature_names() -> tuple[str, ...]:
    """Return the immutable 32 current inputs followed by one lagged state."""

    return (*classifier._site_feature_names(), MEMORY_FEATURE)


def _attach_lag7_memory(
    site_frame: pd.DataFrame,
    *,
    sentinel: int = MEMORY_SENTINEL,
) -> pd.DataFrame:
    """Add same-fold y(t-7) only when its seven-day target matures at issue time."""

    if sentinel != MEMORY_SENTINEL:
        raise ValueError("The registered memory sentinel is fixed to -1")
    required = {"fold", "date", "target_end_date", "truth_status", "actual_level"}
    artifact_io._require_columns(site_frame, required, name="memory site frame")
    result = site_frame.copy()
    result["date"] = pd.to_datetime(result["date"])
    result["target_end_date"] = pd.to_datetime(result["target_end_date"])
    result["lag7_actual_level"] = pd.Series(pd.NA, index=result.index, dtype="Int64")
    result["transition_truth"] = False
    for fold, fold_frame in result.groupby("fold", sort=True):
        ordered = fold_frame.sort_values("date")
        valid = ordered.loc[ordered["truth_status"].eq("valid")]
        previous_level: int | None = None
        previous_date: pd.Timestamp | None = None
        for index, row in valid.iterrows():
            level = int(row["actual_level"])
            if previous_level is not None and row[
                "date"
            ] - previous_date == pd.Timedelta(days=1):
                result.at[index, "transition_truth"] = level != previous_level
            previous_level = level
            previous_date = row["date"]
        sources = valid.set_index("date")
        if not sources.index.is_unique:
            raise artifact_io.AutoStateInputError(f"fold={fold} has duplicate dates")
        for index, row in ordered.iterrows():
            source_date = row["date"] - pd.Timedelta(days=7)
            if source_date not in sources.index:
                continue
            source = sources.loc[source_date]
            if pd.Timestamp(source["target_end_date"]) != pd.Timestamp(row["date"]):
                raise artifact_io.AutoStateInputError(
                    f"fold={fold} lag-7 source target does not mature at issue date"
                )
            result.at[index, "lag7_actual_level"] = int(source["actual_level"])
    result[MEMORY_FEATURE] = (
        result["lag7_actual_level"].astype("Float64").fillna(sentinel).astype(float)
    )
    return result


def _build_memory_site_frame(station: pd.DataFrame, site: pd.DataFrame) -> pd.DataFrame:
    """Build the fixed site features while retaining target maturity dates."""

    base = classifier._build_site_frame(station, site)
    maturity = site.loc[:, ["fold", "date", "target_end_date"]].copy()
    maturity["date"] = pd.to_datetime(maturity["date"])
    maturity["target_end_date"] = pd.to_datetime(maturity["target_end_date"])
    if maturity.duplicated(["fold", "date"]).any():
        raise artifact_io.AutoStateInputError(
            "ECDF site labels contain duplicate fold/date"
        )
    merged = base.merge(maturity, on=["fold", "date"], validate="one_to_one")
    valid = merged["truth_status"].eq("valid")
    if merged.loc[valid, "target_end_date"].isna().any():
        raise artifact_io.AutoStateInputError(
            "Valid site labels require target_end_date"
        )
    expected_end = merged.loc[valid, "date"] + pd.Timedelta(days=7)
    if not expected_end.equals(merged.loc[valid, "target_end_date"]):
        raise artifact_io.AutoStateInputError(
            "Valid site target_end_date must equal date + 7 days"
        )
    return _attach_lag7_memory(merged)


def _comparison_masks(frame: pd.DataFrame, *, fold: int) -> dict[str, np.ndarray]:
    """Return all-valid, lag-7-common and adjacent-transition common masks."""

    fold_mask = frame["fold"].eq(fold).to_numpy()
    truth_valid = frame["truth_status"].eq("valid").to_numpy()
    lag_available = frame["lag7_actual_level"].notna().to_numpy()
    transition = frame["transition_truth"].astype(bool).to_numpy()
    all_valid = fold_mask & truth_valid
    lag7_common = all_valid & lag_available
    return {
        "all_valid": all_valid,
        "lag7_common": lag7_common,
        "transition_common": lag7_common & transition,
    }


def _load_v1_predictions(
    path: Path,
    manifest_path: Path,
    ecdf_paths: dict[str, Path],
) -> pd.DataFrame:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        raise artifact_io.AutoStateInputError(
            "Cannot read classifier v1 manifest"
        ) from exc
    record = manifest.get("outputs", {}).get("site_predictions")
    if not isinstance(record, dict):
        raise artifact_io.AutoStateInputError(
            "Classifier manifest lacks site_predictions"
        )
    if record.get("path") != artifact_io._relative_path(path) or record.get(
        "sha256"
    ) != _sha256(path):
        raise artifact_io.AutoStateInputError(
            "Classifier site predictions fail manifest validation"
        )
    if manifest.get("formal_warning_output") is not False:
        raise artifact_io.AutoStateInputError(
            "Classifier v1 source must remain non-formal"
        )
    source_names = {
        "station_labels": "station_labels",
        "site_labels": "site_labels",
        "label_gate": "label_gate",
        "labels_manifest": "labels_manifest",
    }
    for input_name, ecdf_name in source_names.items():
        source = ecdf_paths[ecdf_name]
        recorded = manifest.get("inputs", {}).get(input_name)
        if not isinstance(recorded, dict) or recorded.get(
            "path"
        ) != artifact_io._relative_path(source):
            raise artifact_io.AutoStateInputError(
                f"Classifier v1 {input_name} path mismatch"
            )
        if recorded.get("sha256") != _sha256(source):
            raise artifact_io.AutoStateInputError(
                f"Classifier v1 {input_name} hash mismatch"
            )
    frame = artifact_io._read_csv(path, name="classifier v1 site predictions")
    required = {
        "fold",
        "date",
        "estimator",
        "truth_status",
        "actual_level",
        "transition_truth",
        "prediction_status",
        "predicted_level",
        *PROBABILITY_COLUMNS,
    }
    artifact_io._require_columns(frame, required, name="classifier v1 site predictions")
    frame = artifact_io._parse_dates(
        frame, ("date",), name="classifier v1 site predictions"
    )
    return frame


def _prediction_frame(
    site_frame: pd.DataFrame,
    probabilities: np.ndarray,
) -> pd.DataFrame:
    result = site_frame.loc[
        :,
        [
            "fold",
            "date",
            "truth_status",
            "actual_level",
            "transition_truth",
            "lag7_actual_level",
            MEMORY_FEATURE,
        ],
    ].copy()
    result["fold_role"] = result["fold"].map(classifier.FOLD_ROLES)
    result["estimator"] = "memory_ngboost"
    result["prediction_status"] = "available"
    result["predicted_level"] = pd.Series(probabilities.argmax(axis=1), dtype="Int64")
    result["predicted_color"] = (
        result["predicted_level"]
        .map(lambda value: WARNING_COLORS[int(value)])
        .astype("string")
    )
    for index, column in enumerate(PROBABILITY_COLUMNS):
        result[column] = probabilities[:, index]
    return result


def _normalize_comparator_rows(
    v1: pd.DataFrame,
    site_frame: pd.DataFrame,
) -> pd.DataFrame:
    """Require exact comparator coverage and agreement with canonical site truth."""

    keep = v1.loc[v1["estimator"].isin(["ngboost", "lag7_persistence"])].copy()
    canonical = site_frame.loc[
        :,
        [
            "fold",
            "date",
            "truth_status",
            "actual_level",
            "transition_truth",
            "lag7_actual_level",
        ],
    ]
    keys = ["fold", "date"]
    if canonical.duplicated(keys).any():
        raise artifact_io.AutoStateInputError(
            "Canonical site truth has duplicate fold/date"
        )
    for estimator in ("ngboost", "lag7_persistence"):
        subset = keep.loc[keep["estimator"].eq(estimator)]
        if len(subset) != 861 or subset.duplicated(keys).any():
            raise artifact_io.AutoStateInputError(
                f"{estimator} must cover 861 unique fold/date keys"
            )
        aligned = canonical.merge(
            subset, on=keys, suffixes=("_site", "_v1"), validate="one_to_one"
        )
        if len(aligned) != len(canonical):
            raise artifact_io.AutoStateInputError(
                f"{estimator} keys do not match site truth"
            )
        for column in ("truth_status", "transition_truth"):
            left = aligned[f"{column}_site"].astype("string").fillna("<NA>")
            right = aligned[f"{column}_v1"].astype("string").fillna("<NA>")
            if not left.equals(right):
                raise artifact_io.AutoStateInputError(f"{estimator} {column} mismatch")
        left_level = pd.to_numeric(aligned["actual_level_site"], errors="coerce")
        right_level = pd.to_numeric(aligned["actual_level_v1"], errors="coerce")
        same_level = left_level.eq(right_level) | (
            left_level.isna() & right_level.isna()
        )
        if not bool(same_level.all()):
            raise artifact_io.AutoStateInputError(f"{estimator} actual_level mismatch")
        if estimator == "lag7_persistence":
            available = aligned["lag7_actual_level"].notna()
            if not np.array_equal(
                available.to_numpy(),
                aligned["prediction_status"].eq("available").to_numpy(),
            ):
                raise artifact_io.AutoStateInputError(
                    "Persistence availability mismatch"
                )
            if not np.array_equal(
                aligned.loc[available, "predicted_level"].astype(int).to_numpy(),
                aligned.loc[available, "lag7_actual_level"].astype(int).to_numpy(),
            ):
                raise artifact_io.AutoStateInputError("Persistence value mismatch")
    keep["estimator"] = keep["estimator"].replace({"ngboost": "v1_ngboost"})
    return keep


def _metric_rows(
    memory: pd.DataFrame,
    comparators: pd.DataFrame,
    site_frame: pd.DataFrame,
) -> pd.DataFrame:
    combined = pd.concat([memory, comparators], ignore_index=True, sort=False)
    rows: list[dict[str, Any]] = []
    probability_estimators = {"memory_ngboost", "v1_ngboost"}
    for fold in (2,):
        masks = _comparison_masks(site_frame, fold=fold)
        dates_by_scope = {
            name: set(site_frame.loc[mask, "date"]) for name, mask in masks.items()
        }
        estimator_by_scope = {
            "all_valid": ("memory_ngboost",),
            "lag7_common": ("memory_ngboost", "v1_ngboost", "lag7_persistence"),
            "transition_common": ("memory_ngboost", "v1_ngboost", "lag7_persistence"),
        }
        expected_rows = {name: int(mask.sum()) for name, mask in masks.items()}
        for scope, estimators in estimator_by_scope.items():
            for estimator in estimators:
                frame = combined.loc[
                    combined["fold"].eq(fold)
                    & combined["estimator"].eq(estimator)
                    & combined["date"].isin(dates_by_scope[scope])
                    & combined["prediction_status"].eq("available")
                ]
                if len(frame) != expected_rows[scope]:
                    raise artifact_io.AutoStateInputError(
                        f"fold={fold} scope={scope} estimator={estimator} "
                        f"must contain {expected_rows[scope]} rows"
                    )
                y_true = frame["actual_level"].to_numpy(dtype=int)
                y_pred = frame["predicted_level"].to_numpy(dtype=int)
                status = (
                    "small_support_descriptive_only"
                    if scope == "transition_common"
                    else "reported_descriptive"
                )
                common = {
                    "fold": fold,
                    "fold_role": classifier.FOLD_ROLES[fold],
                    "scope": scope,
                    "estimator": estimator,
                    "n": len(frame),
                    "status": status,
                }
                hard = {
                    "accuracy": float(accuracy_score(y_true, y_pred)),
                    "macro_f1_fixed_five": float(
                        f1_score(
                            y_true,
                            y_pred,
                            labels=range(5),
                            average="macro",
                            zero_division=0,
                        )
                    ),
                    "ordinal_mae": float(mean_absolute_error(y_true, y_pred)),
                }
                for metric, value in hard.items():
                    rows.append({**common, "metric": metric, "value": value})
                if scope != "transition_common" and estimator in probability_estimators:
                    probabilities = frame.loc[:, PROBABILITY_COLUMNS].to_numpy(
                        dtype=float
                    )
                    one_hot = np.eye(5)[y_true]
                    rows.extend(
                        [
                            {
                                **common,
                                "metric": "multiclass_log_loss",
                                "value": float(
                                    log_loss(y_true, probabilities, labels=range(5))
                                ),
                            },
                            {
                                **common,
                                "metric": "multiclass_brier",
                                "value": float(
                                    np.mean(
                                        np.square(probabilities - one_hot).sum(axis=1)
                                    )
                                ),
                            },
                        ]
                    )
    return pd.DataFrame(rows)


def _decision_from_metrics(
    metrics: pd.DataFrame, config: dict[str, Any]
) -> dict[str, Any]:
    """Evaluate the pre-registered fold-2 common-set decision criteria."""

    common = metrics.loc[metrics["fold"].eq(2) & metrics["scope"].eq("lag7_common")]

    def value(estimator: str, metric: str) -> float:
        found = common.loc[
            common["estimator"].eq(estimator) & common["metric"].eq(metric), "value"
        ]
        if len(found) != 1:
            raise artifact_io.AutoStateInputError(
                f"Missing decision metric {estimator}/{metric}"
            )
        return float(found.iloc[0])

    values = {
        estimator: {
            "macro_f1_fixed_five": value(estimator, "macro_f1_fixed_five"),
            "ordinal_mae": value(estimator, "ordinal_mae"),
        }
        for estimator in ("memory_ngboost", "v1_ngboost", "lag7_persistence")
    }
    for estimator in ("memory_ngboost", "v1_ngboost"):
        values[estimator]["multiclass_log_loss"] = value(
            estimator, "multiclass_log_loss"
        )
        values[estimator]["multiclass_brier"] = value(estimator, "multiclass_brier")
    upper = float(config["comparison"]["decision"]["log_loss_absolute_upper"])
    memory = values["memory_ngboost"]
    baseline = values["v1_ngboost"]
    persistence = values["lag7_persistence"]
    checks = {
        "macro_f1_above_v1": memory["macro_f1_fixed_five"]
        > baseline["macro_f1_fixed_five"],
        "macro_f1_above_persistence": memory["macro_f1_fixed_five"]
        > persistence["macro_f1_fixed_five"],
        "ordinal_mae_below_v1": memory["ordinal_mae"] < baseline["ordinal_mae"],
        "ordinal_mae_below_persistence": memory["ordinal_mae"]
        < persistence["ordinal_mae"],
        "log_loss_below_v1": memory["multiclass_log_loss"]
        < baseline["multiclass_log_loss"],
        "brier_below_v1": memory["multiclass_brier"] < baseline["multiclass_brier"],
        "log_loss_below_absolute_upper": memory["multiclass_log_loss"] < upper,
    }
    passed = all(checks.values())
    return {
        "passed": passed,
        "status": "improvement_candidate" if passed else "rejected",
        "evaluation_fold": 2,
        "scope": "lag7_common_273",
        "checks": checks,
        "values": values,
        "log_loss_absolute_upper": upper,
    }


def _importance_rows(model: Any) -> pd.DataFrame:
    values = np.asarray(model.feature_importances_, dtype=float)
    if values.ndim == 1:
        values = values.reshape(1, -1)
    features = _memory_feature_names()
    if values.shape[1] != len(features):
        raise artifact_io.AutoStateInputError("Unexpected memory importance shape")
    return pd.DataFrame(
        [
            {
                "distribution_parameter": parameter,
                "feature": feature,
                "importance": float(value),
                "method": "ngboost_builtin_feature_importances_non_shap",
                "interpretation": "model_dependency_not_causal",
            }
            for parameter, vector in enumerate(values)
            for feature, value in zip(features, vector, strict=True)
        ]
    )


def run_memory_challenger(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> MemoryArtifacts:
    """Fit and run the challenger; invoke only after protocol pre-registration."""

    started = time.perf_counter()
    config_path = Path(config_path)
    config = _load_config(config_path)
    paths = {
        name: artifact_io._resolve_config_path(value, name=f"inputs.{name}")
        for name, value in config["inputs"].items()
    }
    ecdf_paths = {
        "station_labels": paths["ecdf_station_labels"],
        "site_labels": paths["ecdf_site_labels"],
        "label_gate": paths["ecdf_label_gate"],
        "labels_manifest": paths["ecdf_manifest"],
    }
    station, site, ecdf_manifest = classifier._load_sources(ecdf_paths)
    v1 = _load_v1_predictions(
        paths["classifier_site_predictions"], paths["classifier_manifest"], ecdf_paths
    )
    site_frame = _build_memory_site_frame(station, site)
    feature_names = _memory_feature_names()
    X = site_frame.loc[:, feature_names].to_numpy(dtype=float)
    fit = site_frame["fold"].eq(1) & site_frame["truth_status"].eq("valid")
    if int(fit.sum()) != 280:
        raise artifact_io.AutoStateInputError("Memory fit must contain 280 fold-1 rows")
    y_fit = site_frame.loc[fit, "actual_level"].to_numpy(dtype=int)
    with threadpool_limits(limits=1):
        model = classifier._make_ngboost(config)
        model.fit(X[fit], y_fit)
        probabilities = classifier._validate_probabilities(
            model.predict_proba(X), len(site_frame)
        )
    memory_predictions = _prediction_frame(site_frame, probabilities)
    comparators = _normalize_comparator_rows(v1, site_frame)
    metrics = _metric_rows(memory_predictions, comparators, site_frame)
    decision = _decision_from_metrics(metrics, config)
    importance = _importance_rows(model)

    for fold in (2,):
        masks = _comparison_masks(site_frame, fold=fold)
        if int(masks["all_valid"].sum()) != 280:
            raise artifact_io.AutoStateInputError(
                f"fold={fold} must have 280 valid labels"
            )
        if int(masks["lag7_common"].sum()) != 273:
            raise artifact_io.AutoStateInputError(
                f"fold={fold} lag-7 common set must have 273 rows"
            )
    fold2_transition_n = int(
        _comparison_masks(site_frame, fold=2)["transition_common"].sum()
    )
    if fold2_transition_n != 11:
        raise artifact_io.AutoStateInputError(
            "fold 2 transition common set must have 11 rows"
        )

    output_dir = artifact_io._resolve_config_path(
        config["output_dir"], name="output_dir"
    )
    model_target = artifact_io._resolve_config_path(
        config["model_output"], name="model_output"
    )
    targets = {
        name: output_dir / filename for name, filename in config["outputs"].items()
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".ngboost-auto-state-memory-", dir=output_dir.parent
    ) as temporary:
        staging = Path(temporary)
        staged = {name: staging / path.name for name, path in targets.items()}
        staged_model = staging / model_target.name
        artifact_io._write_csv(memory_predictions, staged["site_predictions"])
        artifact_io._write_csv(metrics, staged["comparison_metrics"])
        artifact_io._write_csv(importance, staged["feature_importance"])
        with staged_model.open("wb") as handle:
            pickle.dump(model, handle, protocol=pickle.HIGHEST_PROTOCOL)
        manifest = {
            "schema_version": 1,
            "artifact_kind": ARTIFACT_KIND,
            "artifact_status": ARTIFACT_STATUS,
            "case": "ootang",
            "formal_warning_output": False,
            "profile": {
                "id": config["profile_id"],
                "version": config["profile_version"],
                "path": artifact_io._relative_path(config_path),
                "sha256": _sha256(config_path),
            },
            "source_label_gate_passed": True,
            "source_label_boundary_version": ecdf_manifest["boundary_version"],
            "feature_order": list(feature_names),
            "feature_count": len(feature_names),
            "future_severity_target_end_fields_in_X": False,
            "memory": config["memory_feature"],
            "model": {
                "ngboost": config["ngboost"],
                "fit_fold": 1,
                "fit_valid_rows": int(fit.sum()),
                "hyperparameter_search": False,
                "posthoc_probability_calibration": False,
                "resampling": False,
            },
            "comparison": {
                **config["comparison"],
                "fold2_transition_actual_rows": fold2_transition_n,
                "decision": decision,
                "persistence_probability_metrics": False,
                "fold3_metrics_status": "prediction_only_no_metrics",
            },
            "fold_roles": {
                str(key): value for key, value in classifier.FOLD_ROLES.items()
            },
            "runtime_seconds": time.perf_counter() - started,
            "row_counts": {
                "site_predictions": len(memory_predictions),
                "comparison_metrics": len(metrics),
                "feature_importance": len(importance),
            },
            "inputs": {
                name: {
                    "path": artifact_io._relative_path(path),
                    "sha256": _sha256(path),
                }
                for name, path in {**paths, "source_code": Path(__file__)}.items()
            },
            "outputs": {
                name: {
                    "path": artifact_io._relative_path(targets[name]),
                    "sha256": _sha256(staged[name]),
                    "rows": len(frame),
                }
                for name, frame in (
                    ("site_predictions", memory_predictions),
                    ("comparison_metrics", metrics),
                    ("feature_importance", importance),
                )
            },
            "model_output": {
                "path": artifact_io._relative_path(model_target),
                "sha256": _sha256(staged_model),
                "size_bytes": staged_model.stat().st_size,
            },
            "not_claimed": config["not_claimed"],
        }
        artifact_io._write_json(manifest, staged["manifest"])
        replacements = tuple(
            FileReplacement(staged[name], target) for name, target in targets.items()
        ) + (FileReplacement(staged_model, model_target),)
        promote_staged_files(replacements)
    return MemoryArtifacts(
        site_predictions_path=targets["site_predictions"],
        comparison_metrics_path=targets["comparison_metrics"],
        feature_importance_path=targets["feature_importance"],
        model_path=model_target,
        manifest_path=targets["manifest"],
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    artifacts = run_memory_challenger(config_path=args.config)
    print(json.dumps({"manifest": str(artifacts.manifest_path)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "MEMORY_FEATURE",
    "MEMORY_SENTINEL",
    "MemoryArtifacts",
    "run_memory_challenger",
]
