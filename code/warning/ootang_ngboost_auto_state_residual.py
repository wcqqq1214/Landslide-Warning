"""Pre-registered lag-conditioned residual NGBoost challenger for Ootang."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
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
from warning import ootang_ngboost_auto_state_memory as memory  # noqa: E402
from warning.draft_evidence import FileReplacement, promote_staged_files  # noqa: E402
from warning.levels import WARNING_COLORS  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = ROOT / "config" / "ootang_ngboost_auto_state_residual.v3.json"
DELTA_VALUES = np.array([-2, -1, 0, 1], dtype=int)
EXPECTED_DELTA_COUNTS = np.array([4, 49, 177, 43], dtype=int)
PROBABILITY_COLUMNS = classifier.PROBABILITY_COLUMNS


@dataclass(frozen=True)
class ResidualArtifacts:
    site_predictions_path: Path
    comparison_metrics_path: Path
    delta_class_definition_path: Path
    model_path: Path
    manifest_path: Path


def _load_config(path: Path) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        raise artifact_io.AutoStateInputError(
            f"Cannot read residual config: {path}"
        ) from exc
    task = config.get("residual_task", {})
    fixed = {
        "schema_version": 1,
        "profile_id": "ootang_ngboost_auto_state_residual_v3",
        "profile_version": 3,
        "status": "exploratory_residual_challenger_not_formal_warning",
        "case": "ootang",
        "formal_warning_output": False,
        "default_pipeline_member": False,
        "scientific_feature_whitelist": list(classifier.SCIENTIFIC_FEATURES),
        "training_policy": {
            "hyperparameter_search": False,
            "posthoc_probability_calibration": False,
            "resampling": False,
            "predict_all_dates": True,
        },
    }
    if not isinstance(config, dict) or any(
        config.get(key) != value for key, value in fixed.items()
    ):
        raise artifact_io.AutoStateInputError(
            "Residual config violates the fixed protocol"
        )
    if task != {
        "fit_fold": 1,
        "fit_rows": 273,
        "feature_count": 33,
        "lag_days": 7,
        "delta_values": [-2, -1, 0, 1],
        "delta_counts": [4, 49, 177, 43],
        "delta_encoding": [0, 1, 2, 3],
        "missing_lag_policy": "v1_ngboost_row_fallback",
    }:
        raise artifact_io.AutoStateInputError("Invalid fixed residual task")
    if config.get("state_probability_mapping") != {
        "policy": "zero_infeasible_then_renormalize_then_sum_to_lag_plus_delta",
        "state_levels": [0, 1, 2, 3, 4],
        "clamp": False,
        "epsilon": False,
        "posthoc_mixture": False,
    }:
        raise artifact_io.AutoStateInputError("Invalid fixed state-probability mapping")
    comparison = config.get("comparison", {})
    if comparison.get("metric_folds") != [2] or comparison.get(
        "prediction_only_folds"
    ) != [3]:
        raise artifact_io.AutoStateInputError("Only fold 2 may have residual metrics")
    if comparison.get("common_rows") != 273:
        raise artifact_io.AutoStateInputError(
            "Residual common comparison must contain 273 rows"
        )
    if comparison.get("decision") != {
        "macro_f1_strictly_above": "lag7_persistence",
        "ordinal_mae_strictly_below": "lag7_persistence",
        "log_loss_strictly_below": "v1_ngboost",
        "brier_strictly_below": "v1_ngboost",
        "log_loss_absolute_upper": 1.6094,
    }:
        raise artifact_io.AutoStateInputError(
            "Invalid pre-registered residual decision"
        )
    expected_ngboost = {
        "classes": 4,
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
    }
    if config.get("ngboost") != expected_ngboost:
        raise artifact_io.AutoStateInputError(
            "Residual NGBoost parameters are not fixed"
        )
    if set(config.get("outputs", {})) != {
        "site_predictions",
        "comparison_metrics",
        "delta_class_definition",
        "manifest",
    }:
        raise artifact_io.AutoStateInputError("Invalid residual output set")
    return config


def _feature_names() -> tuple[str, ...]:
    return memory._memory_feature_names()


def _delta_training_rows(site_frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    mask = (
        site_frame["fold"].eq(1)
        & site_frame["truth_status"].eq("valid")
        & site_frame["lag7_actual_level"].notna()
    ).to_numpy()
    delta = site_frame.loc[mask, "actual_level"].to_numpy(dtype=int) - site_frame.loc[
        mask, "lag7_actual_level"
    ].to_numpy(dtype=int)
    counts = np.array([(delta == value).sum() for value in DELTA_VALUES])
    if len(delta) != 273 or not np.array_equal(counts, EXPECTED_DELTA_COUNTS):
        raise artifact_io.AutoStateInputError(
            f"Fold-1 delta support changed: values={sorted(set(delta))}, counts={counts.tolist()}"
        )
    encoded = np.searchsorted(DELTA_VALUES, delta)
    return mask, encoded


def _fold2_delta_drift(site_frame: pd.DataFrame) -> dict[str, Any]:
    fold1_mask = memory._comparison_masks(site_frame, fold=1)["lag7_common"]
    fold1 = site_frame.loc[fold1_mask]
    fold1_delta = fold1["actual_level"].to_numpy(dtype=int) - fold1[
        "lag7_actual_level"
    ].to_numpy(dtype=int)
    fold1_condition = fold1["lag7_actual_level"].eq(3).to_numpy() & (fold1_delta == -2)
    if int(fold1_condition.sum()) != 0:
        raise artifact_io.AutoStateInputError(
            "The registered unseen (lag=3, delta=-2) condition is present in fold 1"
        )
    mask = memory._comparison_masks(site_frame, fold=2)["lag7_common"]
    frame = site_frame.loc[mask]
    delta = frame["actual_level"].to_numpy(dtype=int) - frame[
        "lag7_actual_level"
    ].to_numpy(dtype=int)
    if set(delta) != set(DELTA_VALUES):
        raise artifact_io.AutoStateInputError(
            "Fold-2 delta support differs from fold 1"
        )
    unseen = frame.loc[
        frame["lag7_actual_level"].eq(3) & pd.Series(delta, index=frame.index).eq(-2)
    ]
    if len(unseen) != 4:
        raise artifact_io.AutoStateInputError(
            "Expected four unseen fold-2 (lag=3, delta=-2) rows"
        )
    return {
        "counts": {str(value): int((delta == value).sum()) for value in DELTA_VALUES},
        "delta_zero_fraction": float((delta == 0).mean()),
        "unseen_fold1_condition": {"lag_level": 3, "delta": -2, "fold2_rows": 4},
        "status": "exposed_distribution_shift_advisory",
    }


def _validate_delta_probabilities(values: np.ndarray, rows: int) -> np.ndarray:
    probabilities = np.asarray(values, dtype=float)
    if probabilities.shape != (rows, 4):
        raise artifact_io.AutoStateInputError(
            "Residual classifier must return rows x four delta probabilities"
        )
    if not np.isfinite(probabilities).all() or (probabilities < 0).any():
        raise artifact_io.AutoStateInputError(
            "Residual probabilities must be finite and nonnegative"
        )
    if not np.allclose(probabilities.sum(axis=1), 1.0, atol=1e-9, rtol=1e-9):
        raise artifact_io.AutoStateInputError("Residual probabilities must sum to one")
    return probabilities


def _map_delta_probabilities(
    delta_probabilities: np.ndarray,
    lag_levels: np.ndarray,
) -> np.ndarray:
    """Remove impossible deltas, renormalize, and map them to five state levels."""

    probabilities = np.asarray(delta_probabilities, dtype=float)
    lag = np.asarray(lag_levels, dtype=int)
    if probabilities.shape != (len(lag), 4) or not np.isfinite(probabilities).all():
        raise artifact_io.AutoStateInputError("Invalid residual probability matrix")
    if (probabilities < 0).any() or ((lag < 0) | (lag > 4)).any():
        raise artifact_io.AutoStateInputError(
            "Invalid residual probabilities or lag levels"
        )
    states = lag[:, None] + DELTA_VALUES[None, :]
    feasible = (states >= 0) & (states <= 4)
    bounded = np.where(feasible, probabilities, 0.0)
    denominator = bounded.sum(axis=1, keepdims=True)
    if (denominator <= 0).any():
        raise artifact_io.AutoStateInputError(
            "Residual feasible probability mass is zero"
        )
    bounded /= denominator
    result = np.zeros((len(lag), 5), dtype=float)
    for column in range(4):
        rows = np.arange(len(lag))[feasible[:, column]]
        result[rows, states[rows, column]] += bounded[rows, column]
    return classifier._validate_probabilities(result, len(lag))


def _compose_all_predictions(
    site_frame: pd.DataFrame,
    residual_probabilities: np.ndarray,
    v1_ngboost: pd.DataFrame,
) -> pd.DataFrame:
    """Use residual predictions where lag is mature and v1 row fallback otherwise."""

    result = site_frame.loc[
        :,
        [
            "fold",
            "date",
            "truth_status",
            "actual_level",
            "transition_truth",
            "lag7_actual_level",
        ],
    ].copy()
    result["fold_role"] = result["fold"].map(classifier.FOLD_ROLES)
    result["prediction_status"] = "available"
    available = result["lag7_actual_level"].notna().to_numpy()
    if residual_probabilities.shape != (int(available.sum()), 5):
        raise artifact_io.AutoStateInputError(
            "Residual rows do not match lag-available dates"
        )
    fallback = v1_ngboost.sort_values(["fold", "date"])
    ordered = result.sort_values(["fold", "date"])
    if (
        not ordered[["fold", "date"]]
        .reset_index(drop=True)
        .equals(fallback[["fold", "date"]].reset_index(drop=True))
    ):
        raise artifact_io.AutoStateInputError("v1 fallback keys do not align")
    fallback_probabilities = fallback.loc[:, PROBABILITY_COLUMNS].to_numpy(dtype=float)
    output = fallback_probabilities.copy()
    output[available] = residual_probabilities
    output = classifier._validate_probabilities(output, len(result))
    result["prediction_source"] = np.where(available, "residual_ngboost", "v1_fallback")
    fallback_counts = result.loc[~available].groupby("fold").size().to_dict()
    if fallback_counts != {1: 7, 2: 7, 3: 7}:
        raise artifact_io.AutoStateInputError(
            f"Expected seven v1 fallback rows per fold: {fallback_counts}"
        )
    if len(result) != 861 or int(available.sum()) != 840:
        raise artifact_io.AutoStateInputError(
            "Expected 861 predictions: 840 residual and 21 fallback"
        )
    result["predicted_level"] = pd.Series(output.argmax(axis=1), dtype="Int64")
    result["predicted_color"] = result["predicted_level"].map(
        lambda value: WARNING_COLORS[int(value)]
    )
    for index, column in enumerate(PROBABILITY_COLUMNS):
        result[column] = output[:, index]
    return result


def _metric_rows(
    residual: pd.DataFrame,
    comparators: pd.DataFrame,
    site_frame: pd.DataFrame,
) -> pd.DataFrame:
    common_dates = set(
        site_frame.loc[
            memory._comparison_masks(site_frame, fold=2)["lag7_common"], "date"
        ]
    )
    residual_rows = residual.loc[
        residual["fold"].eq(2) & residual["date"].isin(common_dates)
    ].assign(estimator="residual_ngboost", prediction_status="available")
    combined = pd.concat([residual_rows, comparators], ignore_index=True, sort=False)
    rows: list[dict[str, Any]] = []
    for estimator in ("residual_ngboost", "v1_ngboost", "lag7_persistence"):
        frame = combined.loc[
            combined["fold"].eq(2)
            & combined["date"].isin(common_dates)
            & combined["estimator"].eq(estimator)
            & combined["prediction_status"].eq("available")
        ]
        if len(frame) != 273:
            raise artifact_io.AutoStateInputError(
                f"{estimator} fold-2 comparison is not 273 rows"
            )
        truth = frame["actual_level"].to_numpy(dtype=int)
        prediction = frame["predicted_level"].to_numpy(dtype=int)
        values = {
            "accuracy": accuracy_score(truth, prediction),
            "macro_f1_fixed_five": f1_score(
                truth, prediction, labels=range(5), average="macro", zero_division=0
            ),
            "ordinal_mae": mean_absolute_error(truth, prediction),
        }
        if estimator != "lag7_persistence":
            probabilities = classifier._validate_probabilities(
                frame.loc[:, PROBABILITY_COLUMNS].to_numpy(dtype=float), len(frame)
            )
            values["multiclass_log_loss"] = log_loss(
                truth, probabilities, labels=range(5)
            )
            values["multiclass_brier"] = (
                np.square(probabilities - np.eye(5)[truth]).sum(axis=1).mean()
            )
        rows.extend(
            {
                "fold": 2,
                "fold_role": classifier.FOLD_ROLES[2],
                "scope": "lag7_common",
                "estimator": estimator,
                "n": 273,
                "metric": metric,
                "value": float(value),
            }
            for metric, value in values.items()
        )
    return pd.DataFrame(rows)


def _decision(metrics: pd.DataFrame) -> dict[str, Any]:
    def value(estimator: str, metric: str) -> float:
        found = metrics.loc[
            metrics["estimator"].eq(estimator) & metrics["metric"].eq(metric), "value"
        ]
        if len(found) != 1:
            raise artifact_io.AutoStateInputError(
                f"Missing residual decision metric {estimator}/{metric}"
            )
        return float(found.iloc[0])

    checks = {
        "macro_f1_above_persistence": value("residual_ngboost", "macro_f1_fixed_five")
        > value("lag7_persistence", "macro_f1_fixed_five"),
        "ordinal_mae_below_persistence": value("residual_ngboost", "ordinal_mae")
        < value("lag7_persistence", "ordinal_mae"),
        "log_loss_below_v1": value("residual_ngboost", "multiclass_log_loss")
        < value("v1_ngboost", "multiclass_log_loss"),
        "brier_below_v1": value("residual_ngboost", "multiclass_brier")
        < value("v1_ngboost", "multiclass_brier"),
        "log_loss_below_1_6094": value("residual_ngboost", "multiclass_log_loss")
        < 1.6094,
    }
    passed = all(checks.values())
    return {
        "passed": passed,
        "status": "improvement_candidate" if passed else "rejected",
        "checks": checks,
    }


def run_residual_challenger(
    *, config_path: Path = DEFAULT_CONFIG_PATH
) -> ResidualArtifacts:
    """Fit the registered challenger; this function is not called on import."""

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
    v1 = memory._load_v1_predictions(
        paths["classifier_site_predictions"], paths["classifier_manifest"], ecdf_paths
    )
    site_frame = memory._build_memory_site_frame(station, site)
    fold2_delta_drift = _fold2_delta_drift(site_frame)
    comparators = memory._normalize_comparator_rows(v1, site_frame)
    fit_mask, y_fit = _delta_training_rows(site_frame)
    feature_names = _feature_names()
    available = site_frame["lag7_actual_level"].notna().to_numpy()
    X = site_frame.loc[:, feature_names].to_numpy(dtype=float)
    if not np.isfinite(X[available]).all():
        raise artifact_io.AutoStateInputError("Residual X contains non-finite values")
    with threadpool_limits(limits=1):
        model = classifier._make_ngboost(config)
        model.fit(X[fit_mask], y_fit)
        delta_probabilities = _validate_delta_probabilities(
            model.predict_proba(X[available]), int(available.sum())
        )
    state_probabilities = _map_delta_probabilities(
        delta_probabilities,
        site_frame.loc[available, "lag7_actual_level"].to_numpy(dtype=int),
    )
    v1_ngboost = v1.loc[v1["estimator"].eq("ngboost")].copy()
    predictions = _compose_all_predictions(site_frame, state_probabilities, v1_ngboost)
    metrics = _metric_rows(predictions, comparators, site_frame)
    decision = _decision(metrics)
    fold2_counts = [
        int(fold2_delta_drift["counts"][str(value)]) for value in DELTA_VALUES
    ]
    if fold2_counts != [7, 16, 219, 31]:
        raise artifact_io.AutoStateInputError("Fold-2 delta counts changed")
    delta_definition = pd.DataFrame(
        {
            "delta_class": range(4),
            "delta_value": DELTA_VALUES,
            "fold1_count": EXPECTED_DELTA_COUNTS,
            "fold2_count": fold2_counts,
        }
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
        prefix=".ngboost-residual-", dir=output_dir.parent
    ) as temporary:
        staging = Path(temporary)
        staged = {name: staging / target.name for name, target in targets.items()}
        staged_model = staging / model_target.name
        frames = {
            "site_predictions": predictions,
            "comparison_metrics": metrics,
            "delta_class_definition": delta_definition,
        }
        for name, frame in frames.items():
            artifact_io._write_csv(frame, staged[name])
        with staged_model.open("wb") as handle:
            pickle.dump(model, handle, protocol=pickle.HIGHEST_PROTOCOL)
        manifest = {
            "schema_version": 1,
            "artifact_kind": "ootang_ngboost_lag_conditioned_residual_v3",
            "artifact_status": config["status"],
            "case": config["case"],
            "formal_warning_output": False,
            "profile": {
                "id": config["profile_id"],
                "version": config["profile_version"],
                "path": artifact_io._relative_path(config_path),
                "sha256": memory._sha256(config_path),
            },
            "source_label_gate_passed": True,
            "source_label_boundary_version": ecdf_manifest["boundary_version"],
            "feature_order": list(feature_names),
            "feature_count": 33,
            "state_probability_mapping": config["state_probability_mapping"],
            "model": {"ngboost": config["ngboost"]},
            "fit": {
                "fold": 1,
                "rows": int(fit_mask.sum()),
                "delta_values": DELTA_VALUES.tolist(),
            },
            "fold2_delta_drift": fold2_delta_drift,
            "comparison": {
                **config["comparison"],
                "decision_result": decision,
                "fold3_metrics_status": "prediction_only_no_metrics",
            },
            "runtime_seconds": time.perf_counter() - started,
            "inputs": {
                name: {
                    "path": artifact_io._relative_path(path),
                    "sha256": memory._sha256(path),
                }
                for name, path in {**paths, "source_code": Path(__file__)}.items()
            },
            "outputs": {
                name: {
                    "path": artifact_io._relative_path(targets[name]),
                    "sha256": memory._sha256(staged[name]),
                    "rows": len(frame),
                }
                for name, frame in frames.items()
            },
            "model_output": {
                "path": artifact_io._relative_path(model_target),
                "sha256": memory._sha256(staged_model),
                "size_bytes": staged_model.stat().st_size,
            },
            "not_claimed": config["not_claimed"],
        }
        artifact_io._write_json(manifest, staged["manifest"])
        replacements = tuple(
            FileReplacement(staged[name], target) for name, target in targets.items()
        )
        replacements += (FileReplacement(staged_model, model_target),)
        promote_staged_files(replacements)
    return ResidualArtifacts(
        site_predictions_path=targets["site_predictions"],
        comparison_metrics_path=targets["comparison_metrics"],
        delta_class_definition_path=targets["delta_class_definition"],
        model_path=model_target,
        manifest_path=targets["manifest"],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    args = parser.parse_args(argv)
    artifacts = run_residual_challenger(config_path=args.config)
    print(json.dumps({"manifest": str(artifacts.manifest_path)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "DELTA_VALUES",
    "ResidualArtifacts",
    "run_residual_challenger",
]
