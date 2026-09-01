"""Fixed NGBoost classifier for ECDF-derived Ootang deformation states.

The site model is the primary research task.  A shared station model is only
a diagnostic.  Both consume four explicitly whitelisted current-time inputs;
future outcomes and automatic-label construction fields never enter X.
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
import warnings

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import BoundaryNorm, ListedColormap  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from ngboost import NGBClassifier  # noqa: E402
from ngboost.distns import k_categorical  # noqa: E402
from ngboost.scores import LogScore  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import shap  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import (  # noqa: E402
    accuracy_score,
    confusion_matrix,
    f1_score,
    log_loss,
    mean_absolute_error,
    recall_score,
)
from sklearn.tree import DecisionTreeRegressor  # noqa: E402
from sklearn.pipeline import Pipeline  # noqa: E402
from sklearn.preprocessing import StandardScaler  # noqa: E402
from threadpoolctl import threadpool_limits  # noqa: E402

plt.rcParams.update(
    {
        "font.family": "sans-serif",
        "font.sans-serif": [
            "Arial Unicode MS",
            "PingFang SC",
            "Songti SC",
            "Arial",
            "Helvetica",
            "DejaVu Sans",
            "sans-serif",
        ],
        "font.size": 7,
        "pdf.fonttype": 42,
        "svg.fonttype": "none",
    }
)


CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning import ootang_ngboost_auto_state as artifact_io  # noqa: E402
from warning.draft_evidence import FileReplacement, promote_staged_files  # noqa: E402
from warning.levels import WARNING_COLORS  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = ROOT / "config" / "ootang_ngboost_auto_state_classifier.v1.json"
ARTIFACT_KIND = "ootang_ngboost_ecdf_auto_state_classifier"
ARTIFACT_STATUS = "exploratory_probability_classifier_not_formal_warning"
SCIENTIFIC_FEATURES = (
    "interval_z",
    "velocity_mm_per_day",
    "acceleration_mm_per_day_squared",
    "tangent_angle_degree",
)
PROBABILITY_COLUMNS = tuple(f"prob_{color}" for color in WARNING_COLORS)
ESTIMATORS = ("ngboost", "multinomial_logistic", "fold1_prior", "lag7_persistence")
FOLD_ROLES = {
    1: "fit",
    2: "development_already_exposed",
    3: "historical_already_exposed_not_confirmatory",
}


@dataclass(frozen=True)
class ClassifierArtifacts:
    site_predictions_path: Path
    station_predictions_path: Path
    metrics_path: Path
    confusion_matrix_path: Path
    feature_importance_path: Path
    site_shap_values_path: Path
    site_shap_importance_path: Path
    site_shap_summary_paths: tuple[Path, Path, Path]
    warning_timeline_paths: tuple[Path, Path, Path]
    site_model_path: Path
    station_model_path: Path
    manifest_path: Path
    fold2_transition_status: str


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
            f"Cannot read classifier config: {path}"
        ) from exc
    if not isinstance(config, dict):
        raise artifact_io.AutoStateInputError("Classifier config must be a JSON object")
    fixed = {
        "schema_version": 1,
        "profile_id": "ootang_ngboost_auto_state_classifier_v1",
        "profile_version": 1,
        "status": ARTIFACT_STATUS,
        "case": "ootang",
        "formal_warning_output": False,
        "default_pipeline_member": False,
        "stations": list(artifact_io.OOTANG_STATIONS),
        "scientific_feature_whitelist": list(SCIENTIFIC_FEATURES),
        "fold_roles": {str(key): value for key, value in FOLD_ROLES.items()},
    }
    for key, expected in fixed.items():
        if config.get(key) != expected:
            raise artifact_io.AutoStateInputError(f"config {key} must be {expected!r}")
    if config.get("site_task") != {
        "feature_layout": "station_major_then_scientific_feature",
        "feature_count": 32,
        "target": "site_auto_state_level",
    }:
        raise artifact_io.AutoStateInputError("Invalid fixed site-task definition")
    expected_ngboost = {
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
    }
    if config.get("ngboost") != expected_ngboost:
        raise artifact_io.AutoStateInputError("Invalid fixed NGBoost definition")
    expected_logistic = {
        "class": "LogisticRegression",
        "scaling": "StandardScaler_fold1_fit_only",
        "solver": "lbfgs",
        "penalty": "l2",
        "C": 1.0,
        "max_iter": 2000,
        "random_state": 0,
    }
    if config.get("logistic_baseline") != expected_logistic:
        raise artifact_io.AutoStateInputError(
            "Invalid fixed LogisticRegression definition"
        )
    if config.get("persistence_baseline") != {
        "kind": "strict_causal_label_lag",
        "lag_days": 7,
        "first_lag_days_status": "unavailable",
    }:
        raise artifact_io.AutoStateInputError("Invalid persistence baseline definition")
    if config.get("training_policy") != {
        "hyperparameter_search": False,
        "posthoc_probability_calibration": False,
        "resampling": False,
        "use_only_valid_labels_for_fit_and_metrics": True,
        "predict_all_dates": True,
    }:
        raise artifact_io.AutoStateInputError("Invalid training policy")
    if config.get("importance") != {
        "site_method": "permutation_shap_expected_ordinal_level",
        "site_background_fold1_uniform_dates_max": 12,
        "site_explanation_fold2_uniform_dates_max": 25,
        "site_max_evals": 65,
        "site_seed": 0,
        "station_diagnostic_method": "ngboost_builtin_feature_importances_non_shap",
    }:
        raise artifact_io.AutoStateInputError("Invalid fixed importance definition")
    inputs = config.get("inputs")
    if not isinstance(inputs, dict) or set(inputs) != {
        "station_labels",
        "site_labels",
        "label_gate",
        "labels_manifest",
    }:
        raise artifact_io.AutoStateInputError("Invalid classifier input set")
    for name, value in inputs.items():
        artifact_io._resolve_config_path(value, name=f"inputs.{name}")
    artifact_io._resolve_config_path(config.get("output_dir"), name="output_dir")
    models = config.get("model_outputs")
    if not isinstance(models, dict) or set(models) != {"site_model", "station_model"}:
        raise artifact_io.AutoStateInputError("Invalid model output set")
    for name, value in models.items():
        artifact_io._resolve_config_path(value, name=f"model_outputs.{name}")
    expected_outputs = {
        "site_predictions",
        "station_predictions",
        "metrics",
        "confusion_matrix",
        "feature_importance",
        "site_shap_values",
        "site_shap_importance",
        "site_shap_summary_png",
        "site_shap_summary_pdf",
        "site_shap_summary_svg",
        "warning_timeline_png",
        "warning_timeline_pdf",
        "warning_timeline_svg",
        "manifest",
    }
    if (
        not isinstance(config.get("outputs"), dict)
        or set(config["outputs"]) != expected_outputs
    ):
        raise artifact_io.AutoStateInputError("Invalid classifier output set")
    if not isinstance(config.get("not_claimed"), list) or not config["not_claimed"]:
        raise artifact_io.AutoStateInputError("not_claimed must be a nonempty list")
    return config


def _load_sources(
    paths: dict[str, Path],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    try:
        gate = json.loads(paths["label_gate"].read_text(encoding="utf-8"))
        manifest = json.loads(paths["labels_manifest"].read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        raise artifact_io.AutoStateInputError("Cannot read ECDF gate/manifest") from exc
    if (
        gate.get("label_gate_passed") is not True
        or manifest.get("label_gate_passed") is not True
    ):
        raise artifact_io.AutoStateInputError(
            "ECDF label gate must pass before classification"
        )
    if (
        manifest.get("formal_warning_output") is not False
        or manifest.get("ngboost_trained") is not False
    ):
        raise artifact_io.AutoStateInputError(
            "ECDF source must remain labels-only and non-formal"
        )
    outputs = manifest.get("outputs", {})
    for key, path in (
        ("station_labels", paths["station_labels"]),
        ("site_labels", paths["site_labels"]),
        ("gate", paths["label_gate"]),
    ):
        record = outputs.get(key)
        if not isinstance(record, dict):
            raise artifact_io.AutoStateInputError(f"ECDF manifest lacks {key}")
        if record.get("path") != artifact_io._relative_path(path) or record.get(
            "sha256"
        ) != _sha256(path):
            raise artifact_io.AutoStateInputError(f"ECDF manifest mismatch for {key}")
    station = artifact_io._read_csv(paths["station_labels"], name="ECDF station labels")
    site = artifact_io._read_csv(paths["site_labels"], name="ECDF site labels")
    required_station = {
        "fold",
        "date",
        "station",
        "label_status",
        "auto_state_level",
        *SCIENTIFIC_FEATURES,
    }
    required_site = {"fold", "date", "label_status", "auto_state_level"}
    artifact_io._require_columns(station, required_station, name="ECDF station labels")
    artifact_io._require_columns(site, required_site, name="ECDF site labels")
    station = artifact_io._parse_dates(station, ("date",), name="ECDF station labels")
    site = artifact_io._parse_dates(site, ("date",), name="ECDF site labels")
    for frame in (station, site):
        frame["fold"] = pd.to_numeric(frame["fold"], errors="raise").astype(int)
        frame["auto_state_level"] = pd.to_numeric(
            frame["auto_state_level"], errors="coerce"
        ).astype("Int64")
    station["station"] = station["station"].astype("string").str.strip()
    if len(site) != 861 or len(station) != 861 * len(artifact_io.OOTANG_STATIONS):
        raise artifact_io.AutoStateInputError(
            "ECDF source row counts are not 861 days x 8 stations"
        )
    if (
        station.duplicated(["fold", "date", "station"]).any()
        or site.duplicated(["fold", "date"]).any()
    ):
        raise artifact_io.AutoStateInputError("ECDF sources contain duplicate keys")
    numeric = station.loc[:, SCIENTIFIC_FEATURES].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(numeric.to_numpy(dtype=float)).all():
        raise artifact_io.AutoStateInputError(
            "All four current-time features must be finite"
        )
    station.loc[:, SCIENTIFIC_FEATURES] = numeric
    return station, site, manifest


def _site_feature_names() -> tuple[str, ...]:
    return tuple(
        f"{station}__{feature}"
        for station in artifact_io.OOTANG_STATIONS
        for feature in SCIENTIFIC_FEATURES
    )


def _build_site_frame(station: pd.DataFrame, site: pd.DataFrame) -> pd.DataFrame:
    """Return one row/day with exactly the registered 32 scientific inputs."""

    pieces = []
    for feature in SCIENTIFIC_FEATURES:
        pivot = station.pivot(index=["fold", "date"], columns="station", values=feature)
        pivot = pivot.loc[:, list(artifact_io.OOTANG_STATIONS)]
        pivot.columns = [f"{station_name}__{feature}" for station_name in pivot.columns]
        pieces.append(pivot)
    wide = pd.concat(pieces, axis=1)
    wide = wide.loc[:, _site_feature_names()].reset_index()
    truth = site.loc[:, ["fold", "date", "label_status", "auto_state_level"]].rename(
        columns={
            "label_status": "truth_status",
            "auto_state_level": "actual_level",
        }
    )
    result = wide.merge(truth, on=["fold", "date"], validate="one_to_one")
    matrix = result.loc[:, _site_feature_names()].to_numpy(dtype=float)
    if matrix.shape != (861, 32) or not np.isfinite(matrix).all():
        raise artifact_io.AutoStateInputError("Site matrix must be finite 861 x 32")
    return result.sort_values(["fold", "date"], kind="stable").reset_index(drop=True)


def _attach_transition_and_lag7_truth(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["transition_truth"] = False
    result["lag7_actual_level"] = pd.Series(pd.NA, index=result.index, dtype="Int64")
    for fold, group in result.groupby("fold", sort=True):
        ordered = group.sort_values("date", kind="stable")
        valid_lookup = {
            row.date: int(row.actual_level)
            for row in ordered.itertuples()
            if row.truth_status == "valid" and not pd.isna(row.actual_level)
        }
        previous_level: int | None = None
        previous_date: pd.Timestamp | None = None
        for index, row in ordered.iterrows():
            valid = row["truth_status"] == "valid" and not pd.isna(row["actual_level"])
            if (
                valid
                and previous_date is not None
                and row["date"] - previous_date == pd.Timedelta(days=1)
            ):
                result.loc[index, "transition_truth"] = (
                    int(row["actual_level"]) != previous_level
                )
            if valid:
                previous_level = int(row["actual_level"])
                previous_date = row["date"]
            lagged = valid_lookup.get(row["date"] - pd.Timedelta(days=7))
            if lagged is not None:
                result.loc[index, "lag7_actual_level"] = lagged
    return result


def _make_ngboost(config: dict[str, Any]) -> NGBClassifier:
    model = config["ngboost"]
    learner = model["base_learner"]
    return NGBClassifier(
        Dist=k_categorical(model["classes"]),
        Score=LogScore,
        Base=DecisionTreeRegressor(
            max_depth=learner["max_depth"],
            criterion=learner["criterion"],
            random_state=learner["random_state"],
        ),
        n_estimators=model["n_estimators"],
        learning_rate=model["learning_rate"],
        minibatch_frac=model["minibatch_frac"],
        col_sample=model["col_sample"],
        natural_gradient=model["natural_gradient"],
        random_state=model["random_state"],
        verbose=model["verbose"],
    )


def _validate_probabilities(values: np.ndarray, rows: int) -> np.ndarray:
    probabilities = np.asarray(values, dtype=float)
    if probabilities.shape != (rows, 5):
        raise artifact_io.AutoStateInputError(
            "Classifier must return rows x five probabilities"
        )
    if not np.isfinite(probabilities).all() or (probabilities < 0).any():
        raise artifact_io.AutoStateInputError(
            "Classifier probabilities must be finite/nonnegative"
        )
    if not np.allclose(probabilities.sum(axis=1), 1.0, atol=1e-9, rtol=1e-9):
        raise artifact_io.AutoStateInputError(
            "Classifier probabilities must sum to one"
        )
    return probabilities


def _probability_rows(
    base_frame: pd.DataFrame,
    *,
    estimator: str,
    probabilities: np.ndarray,
    available: np.ndarray | None = None,
) -> pd.DataFrame:
    if available is None:
        available = np.ones(len(base_frame), dtype=bool)
    probabilities = np.asarray(probabilities, dtype=float)
    result = base_frame.loc[
        :, ["fold", "date", "truth_status", "actual_level", "transition_truth"]
    ].copy()
    result["fold_role"] = result["fold"].map(FOLD_ROLES)
    result["estimator"] = estimator
    result["prediction_status"] = np.where(
        available, "available", "unavailable_initial_lag7"
    )
    predicted = np.full(len(result), np.nan)
    predicted[available] = probabilities[available].argmax(axis=1)
    result["predicted_level"] = pd.Series(predicted, dtype="Int64")
    result["predicted_color"] = (
        result["predicted_level"]
        .map(lambda value: pd.NA if pd.isna(value) else WARNING_COLORS[int(value)])
        .astype("string")
    )
    for index, column in enumerate(PROBABILITY_COLUMNS):
        result[column] = np.where(available, probabilities[:, index], np.nan)
    return result


def _fit_site_models(
    frame: pd.DataFrame,
    config: dict[str, Any],
) -> tuple[NGBClassifier, Pipeline, np.ndarray, pd.DataFrame]:
    features = _site_feature_names()
    fit = frame["fold"].eq(1) & frame["truth_status"].eq("valid")
    X = frame.loc[:, features].to_numpy(dtype=float)
    y_fit = frame.loc[fit, "actual_level"].to_numpy(dtype=int)
    if set(y_fit) != set(range(5)):
        raise artifact_io.AutoStateInputError(
            "Fold 1 site target must contain all five classes"
        )
    with threadpool_limits(limits=1):
        ngboost = _make_ngboost(config)
        ngboost.fit(X[fit], y_fit)
        logistic_config = config["logistic_baseline"]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            logistic = Pipeline(
                [
                    ("scaler", StandardScaler()),
                    (
                        "classifier",
                        LogisticRegression(
                            solver=logistic_config["solver"],
                            penalty=logistic_config["penalty"],
                            C=logistic_config["C"],
                            max_iter=logistic_config["max_iter"],
                            random_state=logistic_config["random_state"],
                        ),
                    ),
                ]
            ).fit(X[fit], y_fit)
        if not np.array_equal(
            logistic.named_steps["classifier"].classes_, np.arange(5)
        ):
            raise artifact_io.AutoStateInputError(
                "Logistic probability class order must be green-to-red"
            )
        ngb_probability = _validate_probabilities(ngboost.predict_proba(X), len(frame))
        logistic_probability = _validate_probabilities(
            logistic.predict_proba(X), len(frame)
        )
    prior = np.bincount(y_fit, minlength=5).astype(float)
    prior /= prior.sum()
    prior_probability = np.broadcast_to(prior, (len(frame), 5)).copy()
    lag_probability = np.zeros((len(frame), 5), dtype=float)
    lag_available = frame["lag7_actual_level"].notna().to_numpy()
    lag_levels = frame.loc[lag_available, "lag7_actual_level"].to_numpy(dtype=int)
    lag_probability[np.flatnonzero(lag_available), lag_levels] = 1.0
    predictions = pd.concat(
        [
            _probability_rows(
                frame, estimator="ngboost", probabilities=ngb_probability
            ),
            _probability_rows(
                frame,
                estimator="multinomial_logistic",
                probabilities=logistic_probability,
            ),
            _probability_rows(
                frame, estimator="fold1_prior", probabilities=prior_probability
            ),
            _probability_rows(
                frame,
                estimator="lag7_persistence",
                probabilities=lag_probability,
                available=lag_available,
            ),
        ],
        ignore_index=True,
    )
    return ngboost, logistic, prior, predictions


def _fit_station_model(
    station: pd.DataFrame,
    config: dict[str, Any],
) -> tuple[NGBClassifier, pd.DataFrame, tuple[str, ...]]:
    frame = station.copy()
    controls = tuple(
        f"station_{station_name}" for station_name in artifact_io.OOTANG_STATIONS
    )
    for station_name, column in zip(artifact_io.OOTANG_STATIONS, controls, strict=True):
        frame[column] = frame["station"].eq(station_name).astype(int)
    features = (*SCIENTIFIC_FEATURES, *controls)
    X = frame.loc[:, features].to_numpy(dtype=float)
    fit = frame["fold"].eq(1) & frame["label_status"].eq("valid")
    y_fit = frame.loc[fit, "auto_state_level"].to_numpy(dtype=int)
    if set(y_fit) != set(range(5)):
        raise artifact_io.AutoStateInputError(
            "Fold 1 station target must contain all classes"
        )
    with threadpool_limits(limits=1):
        model = _make_ngboost(config)
        model.fit(X[fit], y_fit)
        probabilities = _validate_probabilities(model.predict_proba(X), len(frame))
    result = frame.loc[
        :, ["fold", "date", "station", "label_status", "auto_state_level"]
    ].rename(
        columns={"label_status": "truth_status", "auto_state_level": "actual_level"}
    )
    result["fold_role"] = result["fold"].map(FOLD_ROLES)
    result["prediction_status"] = "available"
    result["predicted_level"] = pd.Series(probabilities.argmax(axis=1), dtype="Int64")
    result["predicted_color"] = (
        result["predicted_level"]
        .map(lambda value: WARNING_COLORS[int(value)])
        .astype("string")
    )
    for index, column in enumerate(PROBABILITY_COLUMNS):
        result[column] = probabilities[:, index]
    return model, result, features


def _metric_rows(predictions: pd.DataFrame, *, folds: tuple[int, ...]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for fold in folds:
        transition_dates = set(
            predictions.loc[
                predictions["fold"].eq(fold)
                & predictions["estimator"].eq("lag7_persistence")
                & predictions["truth_status"].eq("valid")
                & predictions["prediction_status"].eq("available")
                & predictions["transition_truth"],
                "date",
            ]
        )
        for estimator in ESTIMATORS:
            base = predictions.loc[
                predictions["fold"].eq(fold)
                & predictions["estimator"].eq(estimator)
                & predictions["truth_status"].eq("valid")
                & predictions["prediction_status"].eq("available")
            ]
            for subset in ("all_valid", "transition_truth"):
                frame = (
                    base
                    if subset == "all_valid"
                    else base.loc[base["date"].isin(transition_dates)]
                )
                y_true = frame["actual_level"].to_numpy(dtype=int)
                y_pred = frame["predicted_level"].to_numpy(dtype=int)
                probability = frame.loc[:, PROBABILITY_COLUMNS].to_numpy(dtype=float)
                common = {
                    "fold": fold,
                    "fold_role": FOLD_ROLES[fold],
                    "estimator": estimator,
                    "subset": subset,
                    "n": len(frame),
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
                if subset == "all_valid" and estimator != "lag7_persistence":
                    one_hot = np.eye(5)[y_true]
                    hard["multiclass_log_loss"] = float(
                        log_loss(y_true, probability, labels=range(5))
                    )
                    hard["multiclass_brier"] = float(
                        np.mean(np.square(probability - one_hot).sum(axis=1))
                    )
                for metric, value in hard.items():
                    status = (
                        "small_support_descriptive_only"
                        if subset == "transition_truth" and len(frame) < 20
                        else "reported_descriptive"
                    )
                    rows.append(
                        {
                            **common,
                            "metric": metric,
                            "class_level": None,
                            "class_color": None,
                            "value": value,
                            "status": status,
                        }
                    )
                if subset == "all_valid":
                    recalls = recall_score(
                        y_true, y_pred, labels=range(5), average=None, zero_division=0
                    )
                    for level, value in enumerate(recalls):
                        rows.append(
                            {
                                **common,
                                "metric": "recall",
                                "class_level": level,
                                "class_color": WARNING_COLORS[level],
                                "value": float(value),
                                "status": "reported_descriptive",
                            }
                        )
    return pd.DataFrame(rows)


def _confusion_rows(
    predictions: pd.DataFrame, *, folds: tuple[int, ...]
) -> pd.DataFrame:
    rows = []
    for fold in folds:
        transition_dates = set(
            predictions.loc[
                predictions["fold"].eq(fold)
                & predictions["estimator"].eq("lag7_persistence")
                & predictions["truth_status"].eq("valid")
                & predictions["prediction_status"].eq("available")
                & predictions["transition_truth"],
                "date",
            ]
        )
        for estimator in ESTIMATORS:
            base = predictions.loc[
                predictions["fold"].eq(fold)
                & predictions["estimator"].eq(estimator)
                & predictions["truth_status"].eq("valid")
                & predictions["prediction_status"].eq("available")
            ]
            for subset in ("all_valid", "transition_truth"):
                frame = (
                    base
                    if subset == "all_valid"
                    else base.loc[base["date"].isin(transition_dates)]
                )
                matrix = confusion_matrix(
                    frame["actual_level"].to_numpy(dtype=int),
                    frame["predicted_level"].to_numpy(dtype=int),
                    labels=range(5),
                )
                for actual in range(5):
                    for predicted in range(5):
                        rows.append(
                            {
                                "fold": fold,
                                "fold_role": FOLD_ROLES[fold],
                                "estimator": estimator,
                                "subset": subset,
                                "actual_level": actual,
                                "actual_color": WARNING_COLORS[actual],
                                "predicted_level": predicted,
                                "predicted_color": WARNING_COLORS[predicted],
                                "count": int(matrix[actual, predicted]),
                                "status": "reported",
                            }
                        )
    return pd.DataFrame(rows)


def _builtin_importance_rows(
    model: NGBClassifier,
    features: tuple[str, ...],
    *,
    model_scope: str,
) -> list[dict[str, Any]]:
    values = np.asarray(model.feature_importances_, dtype=float)
    if values.ndim == 1:
        values = values.reshape(1, -1)
    if values.shape[1] != len(features):
        raise artifact_io.AutoStateInputError(
            "Unexpected NGBoost feature-importance shape"
        )
    rows = []
    for parameter, vector in enumerate(values):
        for feature, value in zip(features, vector, strict=True):
            rows.append(
                {
                    "model_scope": model_scope,
                    "importance_method": "ngboost_builtin_feature_importances_non_shap",
                    "distribution_parameter": parameter,
                    "feature": feature,
                    "importance": float(value),
                    "interpretation": "model_dependency_not_causal",
                }
            )
    return rows


def _uniform_rows(frame: pd.DataFrame, maximum: int) -> pd.DataFrame:
    if len(frame) <= maximum:
        return frame.copy()
    positions = np.linspace(0, len(frame) - 1, maximum).round().astype(int)
    return frame.iloc[np.unique(positions)].copy()


def _site_shap(
    model: NGBClassifier,
    site_frame: pd.DataFrame,
    config: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    features = _site_feature_names()
    importance = config["importance"]
    background_rows = _uniform_rows(
        site_frame.loc[
            site_frame["fold"].eq(1) & site_frame["truth_status"].eq("valid")
        ],
        int(importance["site_background_fold1_uniform_dates_max"]),
    )
    explanation_rows = _uniform_rows(
        site_frame.loc[
            site_frame["fold"].eq(2) & site_frame["truth_status"].eq("valid")
        ],
        int(importance["site_explanation_fold2_uniform_dates_max"]),
    )
    background = background_rows.loc[:, features].to_numpy(dtype=float)
    explain = explanation_rows.loc[:, features].to_numpy(dtype=float)
    masker = shap.maskers.Independent(background)

    def expected_level(values: np.ndarray) -> np.ndarray:
        with (
            warnings.catch_warnings(),
            np.errstate(over="ignore", divide="ignore", invalid="ignore"),
        ):
            warnings.simplefilter("ignore", RuntimeWarning)
            probabilities = _validate_probabilities(
                model.predict_proba(values), len(values)
            )
        with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
            return probabilities @ np.arange(5, dtype=float)

    explainer = shap.Explainer(
        expected_level,
        masker,
        algorithm="permutation",
        seed=int(importance["site_seed"]),
        feature_names=list(features),
    )
    explanation = explainer(
        explain,
        max_evals=int(importance["site_max_evals"]),
        silent=True,
    )
    values = np.asarray(explanation.values, dtype=float)
    if values.shape != (len(explanation_rows), len(features)):
        raise artifact_io.AutoStateInputError(
            f"Unexpected site SHAP shape: {values.shape}"
        )
    rows = []
    for sample_index, source in enumerate(explanation_rows.itertuples(index=False)):
        for feature_index, feature in enumerate(features):
            station, indicator = feature.split("__", maxsplit=1)
            rows.append(
                {
                    "fold": 2,
                    "date": source.date,
                    "feature": feature,
                    "station": station,
                    "indicator": indicator,
                    "explained_output": "expected_ordinal_level",
                    "shap_value_expected_ordinal_level": float(
                        values[sample_index, feature_index]
                    ),
                    "feature_value": float(explain[sample_index, feature_index]),
                    "method": "permutation_shap_ngboost_expected_ordinal_level",
                    "interpretation": "model_dependency_not_causal_not_convlstm_internal_shap",
                }
            )
    shap_values = pd.DataFrame(rows)
    overall = (
        shap_values.assign(
            abs_shap=shap_values["shap_value_expected_ordinal_level"].abs()
        )
        .groupby(["feature", "station", "indicator"], sort=True)["abs_shap"]
        .mean()
        .reset_index(name="mean_abs_shap")
    )
    overall["explained_output"] = "expected_ordinal_level"
    overall["aggregation"] = "overall_across_fold2_explanation_dates"
    shap_importance = overall
    shap_importance["method"] = "permutation_shap_ngboost_expected_ordinal_level"
    shap_importance["interpretation"] = (
        "model_dependency_not_causal_not_convlstm_internal_shap"
    )
    return shap_values, shap_importance


def _save_figure(fig: plt.Figure, paths: tuple[Path, Path, Path]) -> None:
    expected_suffixes = (".png", ".pdf", ".svg")
    if tuple(path.suffix.lower() for path in paths) != expected_suffixes:
        raise artifact_io.AutoStateInputError(
            "Figure outputs must be ordered as PNG, PDF, and SVG"
        )
    for path in paths:
        fig.savefig(
            path,
            dpi=600,
            bbox_inches="tight",
            facecolor="white",
            metadata={"Creator": "Landslide-Warning"},
        )


def _format_shap_cell(value: float) -> str:
    if value == 0.0:
        return "0"
    if value < 0.001:
        return "<0.001"
    return f"{value:.3f}"


def _plot_shap_summary(
    importance: pd.DataFrame, paths: tuple[Path, Path, Path]
) -> None:
    indicator_labels = {
        "interval_z": "位移区间偏离",
        "velocity_mm_per_day": "逐点速度",
        "acceleration_mm_per_day_squared": "逐点加速度",
        "tangent_angle_degree": "改进切线角",
    }
    expected_index = pd.MultiIndex.from_product(
        [artifact_io.OOTANG_STATIONS, SCIENTIFIC_FEATURES],
        names=("station", "indicator"),
    )
    observed = importance.set_index(["station", "indicator"])
    if observed.index.has_duplicates or set(observed.index) != set(expected_index):
        raise artifact_io.AutoStateInputError(
            "Site SHAP heatmap requires one importance value for each of the "
            "fixed 8 stations x 4 indicators"
        )
    matrix = (
        observed["mean_abs_shap"]
        .unstack("indicator")
        .reindex(index=artifact_io.OOTANG_STATIONS, columns=SCIENTIFIC_FEATURES)
    )
    values = matrix.to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values < 0.0).any():
        raise artifact_io.AutoStateInputError(
            "Site SHAP importance must be finite and non-negative"
        )

    fig, axis = plt.subplots(figsize=(7.2, 4.6), constrained_layout=True)
    image = axis.imshow(values, cmap="Blues", vmin=0.0, aspect="auto")
    color_limit = float(values.max())
    text_threshold = 0.45 * color_limit
    for row_index in range(values.shape[0]):
        for column_index in range(values.shape[1]):
            value = values[row_index, column_index]
            axis.text(
                column_index,
                row_index,
                _format_shap_cell(value),
                ha="center",
                va="center",
                fontsize=6,
                color="white" if value >= text_threshold else "#202020",
            )
    axis.set_xticks(
        np.arange(len(SCIENTIFIC_FEATURES)),
        labels=[indicator_labels[indicator] for indicator in SCIENTIFIC_FEATURES],
    )
    axis.set_yticks(
        np.arange(len(artifact_io.OOTANG_STATIONS)),
        labels=artifact_io.OOTANG_STATIONS,
    )
    axis.set_xticks(np.arange(-0.5, len(SCIENTIFIC_FEATURES), 1), minor=True)
    axis.set_yticks(np.arange(-0.5, len(artifact_io.OOTANG_STATIONS), 1), minor=True)
    axis.grid(which="minor", color="white", linewidth=0.8)
    axis.tick_params(which="minor", bottom=False, left=False)
    axis.set_xlabel("四项预警指标")
    axis.set_ylabel("监测点")
    axis.set_title("滑坡体级 NGBoost：32 项特征依赖（非因果）")
    colorbar = fig.colorbar(image, ax=axis, pad=0.025, shrink=0.9)
    colorbar.set_label("平均 |SHAP值|（期望等级）")
    _save_figure(fig, paths)
    plt.close(fig)


def _date_bin_edges(dates: pd.Series) -> np.ndarray:
    numbers = mdates.date2num(pd.to_datetime(dates).array.to_pydatetime())
    if len(numbers) < 2 or not np.all(np.diff(numbers) > 0.0):
        raise artifact_io.AutoStateInputError(
            "Warning timeline dates must be strictly increasing"
        )
    interior = (numbers[:-1] + numbers[1:]) / 2.0
    return np.concatenate(
        (
            [numbers[0] - (interior[0] - numbers[0])],
            interior,
            [numbers[-1] + (numbers[-1] - interior[-1])],
        )
    )


def _prediction_timeline_codes(
    frame: pd.DataFrame, dates: pd.Series, *, missing_code: int
) -> np.ndarray:
    aligned = frame.copy()
    aligned["date"] = pd.to_datetime(aligned["date"])
    if aligned["date"].duplicated().any():
        raise artifact_io.AutoStateInputError(
            "Warning timeline prediction rows must be unique by date"
        )
    aligned = aligned.set_index("date").reindex(pd.DatetimeIndex(dates))
    codes = aligned["predicted_color"].map(
        {color: level for level, color in enumerate(WARNING_COLORS)}
    )
    available = aligned["prediction_status"].eq("available") & codes.notna()
    return codes.where(available, missing_code).to_numpy(dtype=int)


def _plot_timeline(
    site_predictions: pd.DataFrame,
    station_predictions: pd.DataFrame,
    paths: tuple[Path, Path, Path],
) -> None:
    palette = {
        "green": "#2ca02c",
        "blue": "#1f77b4",
        "yellow": "#f1c40f",
        "orange": "#ff7f0e",
        "red": "#d62728",
    }
    warning_labels = {
        "green": "绿色（0）",
        "blue": "蓝色（1）",
        "yellow": "黄色（2）",
        "orange": "橙色（3）",
        "red": "红色（4）",
    }
    fold_stage_labels = {
        "fit": "拟合",
        "development_already_exposed": "开发",
        "historical_already_exposed_not_confirmatory": "历史回顾",
    }
    immature_color = "#D9D9D9"
    missing_color = "#595959"
    immature_code = len(WARNING_COLORS)
    missing_code = immature_code + 1

    site_ngboost = site_predictions.loc[
        site_predictions["estimator"].eq("ngboost")
    ].copy()
    site_ngboost["date"] = pd.to_datetime(site_ngboost["date"])
    if site_ngboost["date"].duplicated().any():
        raise artifact_io.AutoStateInputError(
            "Warning timeline requires one site NGBoost row per date"
        )
    site_ngboost = site_ngboost.sort_values("date", kind="stable").reset_index(
        drop=True
    )
    dates = site_ngboost["date"]
    date_edges = _date_bin_edges(dates)

    truth_codes = np.full(len(site_ngboost), missing_code, dtype=int)
    truth_status = site_ngboost["truth_status"].astype("string")
    actual_level = pd.to_numeric(site_ngboost["actual_level"], errors="coerce")
    mature = truth_status.eq("valid") & actual_level.notna()
    mature_levels = actual_level.loc[mature]
    if (
        not mature_levels.between(0, len(WARNING_COLORS) - 1).all()
        or not np.equal(mature_levels, np.floor(mature_levels)).all()
    ):
        raise artifact_io.AutoStateInputError(
            "Mature automatic labels must be integer levels from 0 through 4"
        )
    truth_codes[mature.to_numpy()] = mature_levels.to_numpy(dtype=int)
    immature = truth_status.eq("unavailable_fold_terminal")
    truth_codes[immature.to_numpy()] = immature_code

    row_labels = ["H=7 自动代理标签", "滑坡体级 NGBoost"]
    row_codes = [
        truth_codes,
        _prediction_timeline_codes(
            site_ngboost, dates, missing_code=missing_code
        ),
    ]
    for station_name in artifact_io.OOTANG_STATIONS:
        station_frame = station_predictions.loc[
            station_predictions["station"].eq(station_name)
        ]
        row_labels.append(station_name)
        row_codes.append(
            _prediction_timeline_codes(
                station_frame, dates, missing_code=missing_code
            )
        )

    timeline = np.vstack(row_codes)
    expected_shape = (2 + len(artifact_io.OOTANG_STATIONS), len(dates))
    if timeline.shape != expected_shape:
        raise artifact_io.AutoStateInputError(
            f"Unexpected warning timeline shape: {timeline.shape}"
        )

    colors = [palette[color] for color in WARNING_COLORS] + [
        immature_color,
        missing_color,
    ]
    color_map = ListedColormap(colors)
    normalization = BoundaryNorm(
        np.arange(-0.5, len(colors) + 0.5, 1.0), color_map.N
    )
    fig, axis = plt.subplots(figsize=(7.2, 4.4), constrained_layout=True)
    axis.pcolormesh(
        date_edges,
        np.arange(timeline.shape[0] + 1),
        timeline,
        cmap=color_map,
        norm=normalization,
        shading="flat",
        rasterized=True,
    )
    axis.set_xlim(date_edges[0], date_edges[-1])
    axis.set_ylim(timeline.shape[0], 0)
    axis.set_yticks(np.arange(timeline.shape[0]) + 0.5, labels=row_labels)
    axis.tick_params(axis="y", length=0)
    axis.hlines(
        np.arange(1, timeline.shape[0]),
        date_edges[0],
        date_edges[-1],
        color="white",
        linewidth=0.7,
    )

    fold_spans = (
        site_ngboost.groupby("fold", sort=False)["date"]
        .agg(start="min", end="max")
        .sort_values("start")
    )
    if set(fold_spans.index) != set(FOLD_ROLES):
        raise artifact_io.AutoStateInputError(
            "Warning timeline requires fold 1, fold 2, and fold 3"
        )
    span_rows = list(fold_spans.itertuples())
    for previous, current in zip(span_rows, span_rows[1:], strict=False):
        boundary = (
            mdates.date2num(previous.end) + mdates.date2num(current.start)
        ) / 2.0
        axis.axvline(boundary, color="#303030", linestyle="--", linewidth=0.8)
    for span in span_rows:
        role = FOLD_ROLES[int(span.Index)]
        midpoint = span.start + (span.end - span.start) / 2
        axis.text(
            mdates.date2num(midpoint),
            1.015,
            f"折 {int(span.Index)} · {fold_stage_labels[role]}",
            transform=axis.get_xaxis_transform(),
            ha="center",
            va="bottom",
            fontsize=6.5,
            color="#303030",
        )

    date_locator = mdates.AutoDateLocator(minticks=5, maxticks=9)
    axis.xaxis.set_major_locator(date_locator)
    date_formatter = mdates.ConciseDateFormatter(date_locator)
    date_formatter.formats = ["%Y", "%m月", "%m-%d", "%H:%M", "%H:%M", "%S"]
    date_formatter.zero_formats = [
        "",
        "%Y",
        "%m月",
        "%m-%d",
        "%H:%M",
        "%H:%M",
    ]
    axis.xaxis.set_major_formatter(date_formatter)
    axis.set_xlabel("日期")
    axis.set_title("藕塘滑坡体级与 8 测点 NGBoost 逐日输出", pad=24)

    immature_count = int((truth_codes == immature_code).sum())
    legend_handles = [
        Patch(
            facecolor=palette[color],
            edgecolor="none",
            label=warning_labels[color],
        )
        for color in WARNING_COLORS
    ]
    legend_handles.extend(
        [
            Patch(
                facecolor=immature_color,
                edgecolor="#A6A6A6",
                label=f"H=7 标签未成熟（{immature_count}日）",
            ),
            Patch(
                facecolor=missing_color,
                edgecolor="none",
                label="其他缺失/不可用",
            ),
        ]
    )
    fig.legend(
        handles=legend_handles,
        loc="outside lower center",
        ncol=7,
        fontsize=5.8,
        frameon=False,
        handlelength=1.1,
        columnspacing=0.7,
    )
    _save_figure(fig, paths)
    plt.close(fig)


def run_auto_state_classifier(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> ClassifierArtifacts:
    """Fit fixed fold-1 models and materialize all-date probability outputs."""

    started = time.perf_counter()
    config_path = Path(config_path)
    config = _load_config(config_path)
    input_paths = {
        name: artifact_io._resolve_config_path(value, name=f"inputs.{name}")
        for name, value in config["inputs"].items()
    }
    output_dir = artifact_io._resolve_config_path(
        config["output_dir"], name="output_dir"
    )
    model_targets = {
        name: artifact_io._resolve_config_path(value, name=f"model_outputs.{name}")
        for name, value in config["model_outputs"].items()
    }
    station, site, labels_manifest = _load_sources(input_paths)
    site_frame = _attach_transition_and_lag7_truth(_build_site_frame(station, site))
    site_model, _logistic_model, prior, site_predictions = _fit_site_models(
        site_frame, config
    )
    station_model, station_predictions, station_features = _fit_station_model(
        station, config
    )
    metrics = _metric_rows(site_predictions, folds=(1, 2, 3))
    confusion = _confusion_rows(site_predictions, folds=(1, 2, 3))
    fold2_transition = metrics.loc[
        metrics["fold"].eq(2)
        & metrics["subset"].eq("transition_truth")
        & metrics["metric"].isin(["macro_f1_fixed_five", "ordinal_mae"])
    ]
    fold2_transition_n = int(fold2_transition["n"].dropna().iloc[0])
    development_summary = {
        "status": "small_support_descriptive_only",
        "transition_days_common_persistence_available_mask": fold2_transition_n,
        "scientific_pass_fail_decision": False,
        "values": {
            f"{row.estimator}__{row.metric}": float(row.value)
            for row in fold2_transition.itertuples()
        },
    }
    importance_rows = [
        *_builtin_importance_rows(
            site_model, _site_feature_names(), model_scope="site_primary"
        ),
        *_builtin_importance_rows(
            station_model, station_features, model_scope="shared_station_diagnostic"
        ),
    ]
    feature_importance = pd.DataFrame(importance_rows)
    shap_values, shap_importance = _site_shap(site_model, site_frame, config)

    output_targets = {
        name: output_dir / filename for name, filename in config["outputs"].items()
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".ngboost-auto-state-classifier-", dir=output_dir.parent
    ) as temporary:
        staging = Path(temporary)
        staged = {name: staging / path.name for name, path in output_targets.items()}
        staged_models = {
            name: staging / path.name for name, path in model_targets.items()
        }
        artifact_io._write_csv(site_predictions, staged["site_predictions"])
        artifact_io._write_csv(station_predictions, staged["station_predictions"])
        artifact_io._write_csv(metrics, staged["metrics"])
        artifact_io._write_csv(confusion, staged["confusion_matrix"])
        artifact_io._write_csv(feature_importance, staged["feature_importance"])
        artifact_io._write_csv(shap_values, staged["site_shap_values"])
        artifact_io._write_csv(shap_importance, staged["site_shap_importance"])
        shap_figure_paths = tuple(
            staged[name]
            for name in (
                "site_shap_summary_png",
                "site_shap_summary_pdf",
                "site_shap_summary_svg",
            )
        )
        timeline_paths = tuple(
            staged[name]
            for name in (
                "warning_timeline_png",
                "warning_timeline_pdf",
                "warning_timeline_svg",
            )
        )
        _plot_shap_summary(shap_importance, shap_figure_paths)
        _plot_timeline(site_predictions, station_predictions, timeline_paths)
        with staged_models["site_model"].open("wb") as handle:
            pickle.dump(site_model, handle, protocol=pickle.HIGHEST_PROTOCOL)
        with staged_models["station_model"].open("wb") as handle:
            pickle.dump(station_model, handle, protocol=pickle.HIGHEST_PROTOCOL)

        elapsed = time.perf_counter() - started
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
            "source_label_boundary_version": labels_manifest["boundary_version"],
            "features": {
                "site_feature_order": list(_site_feature_names()),
                "site_feature_count": 32,
                "station_feature_order": list(station_features),
                "scientific_feature_whitelist": list(SCIENTIFIC_FEATURES),
                "future_target_or_label_fields_in_X": False,
            },
            "models": {
                "ngboost": config["ngboost"],
                "logistic_baseline": config["logistic_baseline"],
                "hyperparameter_search": False,
                "posthoc_probability_calibration": False,
                "fit_fold": 1,
                "site_model_role": "primary",
                "shared_station_model_role": "diagnostic_only",
            },
            "baselines": {
                "fold1_prior": prior.tolist(),
                "lag7_persistence": "strict_same_fold_y_t_minus_7_first_7_days_unavailable",
                "lag7_persistence_probability_metrics": False,
                "lag7_probability_columns_role": "one_hot_display_only_not_probabilistic_forecast",
                "multinomial_logistic_same_X": True,
            },
            "fold_roles": {str(key): value for key, value in FOLD_ROLES.items()},
            "fold2_development_transition": development_summary,
            "fold3_metrics_status": "reported_historical_already_exposed_not_confirmatory",
            "shap": {
                "computed": True,
                "method": "permutation_shap_ngboost_expected_ordinal_level",
                "background": "fold1_uniform_max_12_dates",
                "explanation": "fold2_uniform_max_25_dates",
                "max_evals": config["importance"]["site_max_evals"],
                "interpretation": "model_dependency_not_causal_not_convlstm_internal_shap",
            },
            "row_counts": {
                "site_dates": len(site_frame),
                "site_prediction_rows": len(site_predictions),
                "station_prediction_rows": len(station_predictions),
                "metrics_rows": len(metrics),
                "confusion_rows": len(confusion),
                "site_shap_value_rows": len(shap_values),
            },
            "runtime_seconds": elapsed,
            "inputs": {
                name: {
                    "path": artifact_io._relative_path(path),
                    "sha256": _sha256(path),
                }
                for name, path in {
                    **input_paths,
                    "source_code": Path(__file__),
                }.items()
            },
            "outputs": {
                name: {
                    "path": artifact_io._relative_path(output_targets[name]),
                    "sha256": _sha256(path),
                    "rows": len(frame),
                }
                for name, path, frame in (
                    ("site_predictions", staged["site_predictions"], site_predictions),
                    (
                        "station_predictions",
                        staged["station_predictions"],
                        station_predictions,
                    ),
                    ("metrics", staged["metrics"], metrics),
                    ("confusion_matrix", staged["confusion_matrix"], confusion),
                    (
                        "feature_importance",
                        staged["feature_importance"],
                        feature_importance,
                    ),
                    ("site_shap_values", staged["site_shap_values"], shap_values),
                    (
                        "site_shap_importance",
                        staged["site_shap_importance"],
                        shap_importance,
                    ),
                )
            },
            "figures": {
                name: {
                    "path": artifact_io._relative_path(output_targets[name]),
                    "sha256": _sha256(staged[name]),
                }
                for name in (
                    "site_shap_summary_png",
                    "site_shap_summary_pdf",
                    "site_shap_summary_svg",
                    "warning_timeline_png",
                    "warning_timeline_pdf",
                    "warning_timeline_svg",
                )
            },
            "model_outputs": {
                name: {
                    "path": artifact_io._relative_path(model_targets[name]),
                    "sha256": _sha256(path),
                    "size_bytes": path.stat().st_size,
                }
                for name, path in staged_models.items()
            },
            "not_claimed": config["not_claimed"],
        }
        artifact_io._write_json(manifest, staged["manifest"])
        replacements = tuple(
            FileReplacement(staged[name], target)
            for name, target in output_targets.items()
        ) + tuple(
            FileReplacement(staged_models[name], target)
            for name, target in model_targets.items()
        )
        promote_staged_files(replacements)

    return ClassifierArtifacts(
        site_predictions_path=output_targets["site_predictions"],
        station_predictions_path=output_targets["station_predictions"],
        metrics_path=output_targets["metrics"],
        confusion_matrix_path=output_targets["confusion_matrix"],
        feature_importance_path=output_targets["feature_importance"],
        site_shap_values_path=output_targets["site_shap_values"],
        site_shap_importance_path=output_targets["site_shap_importance"],
        site_shap_summary_paths=tuple(
            output_targets[name]
            for name in (
                "site_shap_summary_png",
                "site_shap_summary_pdf",
                "site_shap_summary_svg",
            )
        ),
        warning_timeline_paths=tuple(
            output_targets[name]
            for name in (
                "warning_timeline_png",
                "warning_timeline_pdf",
                "warning_timeline_svg",
            )
        ),
        site_model_path=model_targets["site_model"],
        station_model_path=model_targets["station_model"],
        manifest_path=output_targets["manifest"],
        fold2_transition_status="small_support_descriptive_only",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    artifacts = run_auto_state_classifier(config_path=args.config)
    print(
        json.dumps(
            {
                "fold2_transition_status": artifacts.fold2_transition_status,
                "manifest": str(artifacts.manifest_path),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ClassifierArtifacts",
    "DEFAULT_CONFIG_PATH",
    "SCIENTIFIC_FEATURES",
    "run_auto_state_classifier",
]
