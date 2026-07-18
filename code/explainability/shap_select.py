"""SHAP analysis for the project's independent exploratory NGBoost models.

This module does not explain ConvLSTM.  Its classification target is retained
only as a legacy same-day V0-derived exploratory label and is not the draft
five-level warning output.
"""

import json
from pathlib import Path
import sys
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import shap
from ngboost import NGBClassifier, NGBRegressor
from ngboost.distns import k_categorical
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    f1_score,
    mean_squared_error,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import TimeSeriesSplit

CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from warning.warning_thresholds import (  # noqa: E402
    MONTH_WINDOW_DAYS,
    TRAIN_FRAC,
    classify_monthly_rates,
    compute_station_thresholds,
    monthly_displacement_rate,
    threshold_rows,
)
from features.kinematics import compute_point_kinematics  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DATA_CSV = ROOT / "data" / "monitoring_data.csv"
FIG_DIR = ROOT / "figures" / "shap"

WINDOW = 5
RAIN_WINDOWS = (7, 15, 30)
STATIONS = {
    "MJ9": "MJ9/mm",
    "MJ1": "MJ1/mm",
    "MJ3": "MJ3/mm",
    "ATU1": "ATU1/mm",
    "ATU2": "ATU2/mm",
    "ATU3": "ATU3/mm",
    "ATU4": "ATU4/mm",
    "ATU5": "ATU5/mm",
}
ENV_COLS = {
    "Rainfall": "Rainfall/mm",
    "RWL": "RWL/m",
    "GWT": "GWT/m",
    "aveT": "aveT/℃",
    "minT": "minT/℃",
    "maxT": "maxT/℃",
    "DP": "DP",
    "RH": "RH",
}

OUT_REG_PNG = FIG_DIR / "shap_reg_summary.png"
OUT_CLS_PNG = FIG_DIR / "shap_cls_summary.png"
OUT_REG_CSV = FIG_DIR / "shap_reg_importance.csv"
OUT_CLS_CSV = FIG_DIR / "shap_cls_importance.csv"
OUT_METRICS_CSV = FIG_DIR / "shap_model_metrics.csv"
OUT_CV_METRICS_CSV = FIG_DIR / "shap_binary_cv_metrics.csv"
OUT_PROVENANCE_JSON = FIG_DIR / "shap_provenance.json"
OUT_THRESHOLDS_CSV = ROOT / "figures" / "thresholds" / "v0_thresholds.csv"
SHAP_BACKGROUND_DATE_COUNT = 12
SHAP_EXPLANATION_DATE_COUNT = 25
SEED = 0

EXPLAINED_MODEL = "NGBoost"
MODEL_ROLE = "independent_exploratory_explainer"
ANALYSIS_SCOPE = (
    "exploratory independent NGBoost model-dependence analysis; not ConvLSTM "
    "explanation, causal inference, or formal five-level warning output"
)
FEATURE_KINEMATICS_DEFINITION = (
    "lagged point velocity (mm/day) and delta_v=velocity_t-velocity_t-1 "
    "(mm/day); delta_v is a velocity increment, not acceleration"
)
TASK_DEFINITIONS = {
    "regression": {
        "task_id": "target_date_displacement_increment_regression",
        "target_definition": "target-observation displacement increment U_t - U_(t-1)",
        "target_unit": "mm per observation interval",
        "target_status": "exploratory prediction target; not a warning level",
        "plot_target": "target-date displacement increment",
        "plot_title_target": "target-date displacement increment",
    },
    "classification": {
        "task_id": "legacy_same_day_station_v0_level_ge_1_classification",
        "target_definition": (
            "legacy same-day station-specific V0 monthly-rate label: "
            "warning_thresholds.warning_level >= 1"
        ),
        "target_unit": "binary label; SHAP explains positive-class probability",
        "target_status": (
            "legacy exploratory label; not the formal five-level fusion warning"
        ),
        "plot_target": "legacy same-day V0 monthly-rate level >= 1",
        "plot_title_target": "legacy same-day V0 label >= 1",
    },
}


def task_metadata(task):
    """Return immutable provenance fields for one explained task."""
    try:
        return dict(TASK_DEFINITIONS[task])
    except KeyError as error:
        raise ValueError(f"未知 SHAP 任务: {task}") from error


def _date_to_iso(value):
    return pd.Timestamp(value).date().isoformat()


def legacy_v0_label_provenance(df, thresholds, *, train_frac, threshold_artifact=None):
    """Describe the historical V0 fit used only for the legacy class label."""
    ordered = df.copy()
    ordered.columns = [column.strip() for column in ordered.columns]
    dates = pd.to_datetime(ordered["Date"]).sort_values().reset_index(drop=True)
    fit_observations = int(len(dates) * train_frac)
    values = next(iter(thresholds.values()))
    month_window_days = int(values["month_window_days"])
    if fit_observations <= month_window_days:
        raise ValueError("V0 拟合期不足以构造月位移量标签")
    artifact_path = Path(threshold_artifact) if threshold_artifact is not None else None
    if artifact_path is not None and not artifact_path.is_absolute():
        artifact_path = ROOT / artifact_path
    artifact = (
        str(artifact_path.relative_to(ROOT)) if artifact_path is not None else None
    )
    return {
        "legacy_label_scheme": "same_day_station_v0_monthly_rate_level_ge_1",
        "legacy_label_status": "not_formal_five_level_warning",
        "v0_estimation_method": values["v0_estimation_method"],
        "v0_train_fraction": float(train_frac),
        "v0_fit_observation_start": _date_to_iso(dates.iloc[1]),
        "v0_fit_observation_end": _date_to_iso(dates.iloc[fit_observations - 1]),
        "v0_monthly_rate_start": _date_to_iso(dates.iloc[month_window_days]),
        "v0_monthly_rate_end": _date_to_iso(dates.iloc[fit_observations - 1]),
        "v0_month_window_days": month_window_days,
        "v0_accel_percentile": float(values["accel_percentile"]),
        "v0_threshold_artifact": artifact,
    }


def build_lagged_samples(
    df,
    stations=STATIONS,
    window=WINDOW,
    month_window_days=MONTH_WINDOW_DAYS,
    thresholds=None,
    rain_windows=RAIN_WINDOWS,
):
    df = df.copy()
    df.columns = [c.strip() for c in df.columns]
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)

    if thresholds is None:
        thresholds = compute_station_thresholds(
            df,
            stations,
            month_window_days=month_window_days,
        )

    rainfall = df[ENV_COLS["Rainfall"]].astype(float)
    rwl_rate = compute_point_kinematics(
        df["Date"],
        df[ENV_COLS["RWL"]],
    )["velocity"]
    gwt_rate = compute_point_kinematics(
        df["Date"],
        df[ENV_COLS["GWT"]],
    )["velocity"]
    rainfall_cumulative = {
        days: rainfall.rolling(days, min_periods=days).sum() for days in rain_windows
    }
    derived_warmup = window + 2
    if rain_windows:
        derived_warmup = max(derived_warmup, max(rain_windows) + window - 1)

    rows, y_reg, y_cls, meta = [], [], [], []
    for station, disp_col in stations.items():
        disp = df[disp_col].astype(float)
        delta = disp.diff()
        kinematics = compute_point_kinematics(df["Date"], disp)
        displacement_rate = kinematics["velocity"]
        displacement_delta_v = kinematics["delta_v"]
        monthly_rate = monthly_displacement_rate(disp, month_window_days)
        warning_levels = classify_monthly_rates(
            monthly_rate,
            thresholds[station]["v0_mm_per_month"],
        )
        sample_start = max(derived_warmup, month_window_days)
        for t in range(sample_start, len(df)):
            row = {}
            for lag in range(1, window + 1):
                row[f"disp_lag{lag}"] = disp.iloc[t - lag]
                row[f"disp_rate_lag{lag}"] = displacement_rate.iloc[t - lag]
                row[f"disp_delta_v_lag{lag}"] = displacement_delta_v.iloc[t - lag]
                for name, col in ENV_COLS.items():
                    row[f"{name}_lag{lag}"] = df[col].iloc[t - lag]
                row[f"RWL_rate_lag{lag}"] = rwl_rate.iloc[t - lag]
                row[f"GWT_rate_lag{lag}"] = gwt_rate.iloc[t - lag]
                for days, cumulative in rainfall_cumulative.items():
                    row[f"Rain_cum{days}_lag{lag}"] = cumulative.iloc[t - lag]
            for name in stations:
                row[f"station_{name}"] = int(name == station)
            rows.append(row)
            y = float(delta.iloc[t])
            warning_level = int(warning_levels[t])
            y_reg.append(y)
            y_cls.append(int(warning_level >= 1))
            meta.append(
                {
                    "Date": df["Date"].iloc[t],
                    "station": station,
                    "monthly_rate": monthly_rate.iloc[t],
                    "previous_monthly_rate": monthly_rate.iloc[t - 1],
                    "v0_mm_per_month": thresholds[station]["v0_mm_per_month"],
                    "previous_warning_level": int(warning_levels[t - 1]),
                    "warning_level": warning_level,
                }
            )

    return (
        pd.DataFrame(rows),
        pd.Series(y_reg, name="delta_disp"),
        pd.Series(y_cls, name="warning"),
        pd.DataFrame(meta),
    )


def make_classifier(n_estimators=300):
    """Create the NGBoost classifier used by SHAP and evaluation."""
    return NGBClassifier(
        Dist=k_categorical(2),
        n_estimators=n_estimators,
        learning_rate=0.03,
        minibatch_frac=0.8,
        col_sample=0.8,
        random_state=SEED,
        verbose=False,
    )


def make_regressor(n_estimators=300):
    """Create the NGBoost regressor used by SHAP and evaluation."""
    return NGBRegressor(
        n_estimators=n_estimators,
        learning_rate=0.03,
        minibatch_frac=0.8,
        col_sample=0.8,
        random_state=SEED,
        verbose=False,
    )


def train_models(X_train, y_reg_train, y_cls_train, n_estimators=300):
    reg = make_regressor(n_estimators=n_estimators)
    cls = make_classifier(n_estimators=n_estimators)
    reg.fit(X_train.values, y_reg_train.values)
    cls.fit(X_train.values, y_cls_train.values)
    return reg, cls


def time_train_mask(meta, train_frac=0.8):
    unique_dates = pd.DatetimeIndex(meta["Date"].unique()).sort_values()
    if len(unique_dates) < 2:
        raise ValueError("至少需要两个不同日期才能进行时间切分")
    split_idx = min(max(int(len(unique_dates) * train_frac), 1), len(unique_dates) - 1)
    split_date = unique_dates[split_idx]
    return meta["Date"] < split_date, split_date


def evenly_spaced_date_sample(X, meta, mask, date_count):
    """Select every station on deterministic, evenly spaced eligible dates."""
    eligible = pd.DatetimeIndex(meta.loc[mask, "Date"].unique()).sort_values()
    if len(eligible) == 0:
        raise ValueError("日期范围内没有可抽取样本")
    count = min(date_count, len(eligible))
    indices = np.rint(np.linspace(0, len(eligible) - 1, count)).astype(int)
    selected_dates = eligible[np.unique(indices)]
    selected_mask = mask & meta["Date"].isin(selected_dates)
    return X.loc[selected_mask].copy(), selected_dates


def explanation_provenance(
    task,
    *,
    explanation_dates,
    explanation_rows,
    explanation_role,
    background_dates,
    background_rows,
    background_role,
):
    """Build CSV-safe provenance for a deterministic SHAP explanation set."""
    return {
        "explained_model": EXPLAINED_MODEL,
        "model_role": MODEL_ROLE,
        "analysis_scope": ANALYSIS_SCOPE,
        "feature_kinematics_definition": FEATURE_KINEMATICS_DEFINITION,
        **task_metadata(task),
        "explanation_sample_role": explanation_role,
        "explanation_start": _date_to_iso(explanation_dates.min()),
        "explanation_end": _date_to_iso(explanation_dates.max()),
        "explanation_date_count": int(len(explanation_dates)),
        "explanation_rows": int(explanation_rows),
        "background_sample_role": background_role,
        "background_start": _date_to_iso(background_dates.min()),
        "background_end": _date_to_iso(background_dates.max()),
        "background_date_count": int(len(background_dates)),
        "background_rows": int(background_rows),
    }


def binary_probability_metrics(y_true, probability, threshold=0.5):
    """Return discrimination, calibration, and threshold metrics."""
    y_true = np.asarray(y_true, dtype=int)
    probability = np.asarray(probability, dtype=float)
    prediction = (probability >= threshold).astype(int)
    has_both_classes = len(np.unique(y_true)) == 2
    return {
        "auc": roc_auc_score(y_true, probability) if has_both_classes else np.nan,
        "pr_auc": (
            average_precision_score(y_true, probability) if has_both_classes else np.nan
        ),
        "brier": brier_score_loss(y_true, probability),
        "recall": recall_score(y_true, prediction, zero_division=0),
        "precision": precision_score(y_true, prediction, zero_division=0),
        "f1": f1_score(y_true, prediction, zero_division=0),
        "rows": int(len(y_true)),
        "positive_rows": int(y_true.sum()),
        "predicted_positive_rows": int(prediction.sum()),
    }


def persistence_baseline_metrics(y_true, meta):
    """Score today's warning from yesterday's normalized monthly rate."""
    y_true = np.asarray(y_true, dtype=int)
    score = meta["previous_monthly_rate"].to_numpy(dtype=float) / meta[
        "v0_mm_per_month"
    ].to_numpy(dtype=float)
    prediction = meta["previous_warning_level"].to_numpy(dtype=int) >= 1
    has_both_classes = len(np.unique(y_true)) == 2
    return {
        "auc": roc_auc_score(y_true, score) if has_both_classes else np.nan,
        "pr_auc": (
            average_precision_score(y_true, score) if has_both_classes else np.nan
        ),
        "recall": recall_score(y_true, prediction, zero_division=0),
        "precision": precision_score(y_true, prediction, zero_division=0),
        "f1": f1_score(y_true, prediction, zero_division=0),
        "predicted_positive_rows": int(prediction.sum()),
    }


def walk_forward_date_ranges(dates, n_splits=5):
    """Return expanding-window date boundaries without mixing dates."""
    unique_dates = pd.DatetimeIndex(pd.to_datetime(dates).unique()).sort_values()
    splitter = TimeSeriesSplit(n_splits=n_splits)
    ranges = []
    for fold, (train_indices, test_indices) in enumerate(
        splitter.split(unique_dates),
        start=1,
    ):
        ranges.append(
            {
                "fold": fold,
                "train_start": unique_dates[train_indices[0]],
                "train_end": unique_dates[train_indices[-1]],
                "test_start": unique_dates[test_indices[0]],
                "test_end": unique_dates[test_indices[-1]],
            }
        )
    return ranges


def evaluate_binary_walk_forward(
    df,
    stations=STATIONS,
    n_splits=5,
    n_estimators=300,
):
    """Evaluate NGBoost with fold-local V0 thresholds and expanding dates."""
    ordered = df.rename(columns=lambda column: column.strip()).copy()
    ordered["Date"] = pd.to_datetime(ordered["Date"])
    ordered = ordered.sort_values("Date").reset_index(drop=True)
    warmup = max(MONTH_WINDOW_DAYS, WINDOW + 2, max(RAIN_WINDOWS) + WINDOW - 1)
    eligible_dates = ordered["Date"].iloc[warmup:]
    rows = []

    for date_range in walk_forward_date_ranges(eligible_dates, n_splits=n_splits):
        fold_train = ordered[ordered["Date"] <= date_range["train_end"]]
        thresholds = compute_station_thresholds(
            fold_train,
            stations,
            train_frac=1.0,
        )
        label_provenance = legacy_v0_label_provenance(
            fold_train,
            thresholds,
            train_frac=1.0,
        )
        X, _, y_cls, meta = build_lagged_samples(
            ordered,
            stations=stations,
            thresholds=thresholds,
        )
        train_mask = meta["Date"] <= date_range["train_end"]
        test_mask = meta["Date"].between(
            date_range["test_start"],
            date_range["test_end"],
        )
        X_train, X_test = X.loc[train_mask], X.loc[test_mask]
        y_train, y_test = y_cls.loc[train_mask], y_cls.loc[test_mask]

        row = {
            **date_range,
            "task": task_metadata("classification")["task_id"],
            "explained_model": EXPLAINED_MODEL,
            "model_role": MODEL_ROLE,
            "analysis_scope": ANALYSIS_SCOPE,
            "target_definition": task_metadata("classification")["target_definition"],
            "target_status": task_metadata("classification")["target_status"],
            "feature_kinematics_definition": FEATURE_KINEMATICS_DEFINITION,
            **label_provenance,
            "train_rows": int(len(y_train)),
            "train_positive_rows": int(y_train.sum()),
            "test_rows": int(len(y_test)),
            "test_positive_rows": int(y_test.sum()),
        }
        if y_train.nunique() < 2 or len(y_test) == 0:
            rows.append({**row, "status": "skipped_single_class_train"})
            continue

        classifier = make_classifier(n_estimators=n_estimators)
        classifier.fit(X_train.values, y_train.values)
        probability = classifier.predict_proba(X_test.values)[:, 1]
        metrics = binary_probability_metrics(y_test, probability)
        baseline = persistence_baseline_metrics(y_test, meta.loc[test_mask])
        rows.append(
            {
                **row,
                "status": "evaluated" if y_test.nunique() == 2 else "single_class_test",
                **{f"test_{key}": value for key, value in metrics.items()},
                **{f"baseline_{key}": value for key, value in baseline.items()},
            }
        )

    return pd.DataFrame(rows)


def shap_matrix(model, background, sample, task):
    columns = list(sample.columns)

    def predict_fn(values):
        values = np.asarray(values)
        if task == "classification":
            return model.predict_proba(values)[:, 1]
        return model.predict(values)

    masker = shap.maskers.Independent(background.values)
    explainer = shap.Explainer(
        predict_fn,
        masker,
        algorithm="permutation",
        seed=SEED,
    )
    explanation = explainer(sample.values, max_evals=2 * len(columns) + 1)
    return np.asarray(explanation.values)


def importance_frame(shap_values, columns, provenance=None):
    mean_abs = np.abs(shap_values).mean(axis=0)
    out = pd.DataFrame({"feature": columns, "mean_abs_shap": mean_abs})
    if provenance:
        for field, value in provenance.items():
            out[field] = value
    return out.sort_values("mean_abs_shap", ascending=False).reset_index(drop=True)


def save_summary_plot(shap_values, X, path, title):
    plt.figure(figsize=(10, 7))
    shap.summary_plot(
        shap_values,
        X,
        show=False,
        max_display=20,
        rng=np.random.default_rng(SEED),
    )
    figure = plt.gcf()
    figure.suptitle(title, fontsize=9, y=0.995)
    figure.tight_layout(rect=(0, 0, 1, 0.76))
    path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(path, dpi=150)
    plt.close()


def summary_title(task, explanation_dates, explanation_rows):
    """Return a concise, visible boundary for a SHAP summary figure."""
    metadata = task_metadata(task)
    return (
        f"{EXPLAINED_MODEL} SHAP (independent; not ConvLSTM)\n"
        f"target: {metadata['plot_title_target']}\n"
        f"holdout: {_date_to_iso(explanation_dates.min())} to "
        f"{_date_to_iso(explanation_dates.max())}; n={explanation_rows}; "
        "exploratory model dependence only"
    )


def main():
    np.random.seed(SEED)
    df = pd.read_csv(DATA_CSV)
    thresholds = compute_station_thresholds(df, STATIONS)
    label_provenance = legacy_v0_label_provenance(
        df,
        thresholds,
        train_frac=TRAIN_FRAC,
        threshold_artifact=OUT_THRESHOLDS_CSV,
    )
    X, y_reg, y_cls, meta = build_lagged_samples(df, thresholds=thresholds)

    train_mask, split_date = time_train_mask(meta)
    X_train, X_test = X.loc[train_mask], X.loc[~train_mask]
    y_reg_train, y_reg_test = y_reg.loc[train_mask], y_reg.loc[~train_mask]
    y_cls_train, y_cls_test = y_cls.loc[train_mask], y_cls.loc[~train_mask]

    reg, cls = train_models(X_train, y_reg_train, y_cls_train)

    reg_pred = reg.predict(X_test.values)
    cls_prob = cls.predict_proba(X_test.values)[:, 1]
    cls_metrics = binary_probability_metrics(y_cls_test, cls_prob)
    persistence_metrics = persistence_baseline_metrics(
        y_cls_test,
        meta.loc[~train_mask],
    )
    background, background_dates = evenly_spaced_date_sample(
        X,
        meta,
        train_mask,
        SHAP_BACKGROUND_DATE_COUNT,
    )
    sample, explanation_dates = evenly_spaced_date_sample(
        X,
        meta,
        ~train_mask,
        SHAP_EXPLANATION_DATE_COUNT,
    )
    common_provenance = {
        "explained_model": EXPLAINED_MODEL,
        "model_role": MODEL_ROLE,
        "analysis_scope": ANALYSIS_SCOPE,
        "feature_kinematics_definition": FEATURE_KINEMATICS_DEFINITION,
        "split_date": _date_to_iso(split_date),
        "train_start": _date_to_iso(meta.loc[train_mask, "Date"].min()),
        "train_end": _date_to_iso(meta.loc[train_mask, "Date"].max()),
        "test_start": _date_to_iso(meta.loc[~train_mask, "Date"].min()),
        "test_end": _date_to_iso(meta.loc[~train_mask, "Date"].max()),
    }
    reg_provenance = explanation_provenance(
        "regression",
        explanation_dates=explanation_dates,
        explanation_rows=len(sample),
        explanation_role="deterministic_evenly_spaced_holdout_dates_all_stations",
        background_dates=background_dates,
        background_rows=len(background),
        background_role="deterministic_evenly_spaced_training_dates_all_stations",
    )
    cls_provenance = explanation_provenance(
        "classification",
        explanation_dates=explanation_dates,
        explanation_rows=len(sample),
        explanation_role="deterministic_evenly_spaced_holdout_dates_all_stations",
        background_dates=background_dates,
        background_rows=len(background),
        background_role="deterministic_evenly_spaced_training_dates_all_stations",
    )
    cls_provenance.update(label_provenance)

    metrics = {
        "report_scope": "single_holdout_regression_and_legacy_classification",
        **common_provenance,
        "regression_task_id": task_metadata("regression")["task_id"],
        "regression_target_definition": task_metadata("regression")[
            "target_definition"
        ],
        "regression_target_unit": task_metadata("regression")["target_unit"],
        "regression_target_status": task_metadata("regression")["target_status"],
        "classification_task_id": task_metadata("classification")["task_id"],
        "classification_target_definition": task_metadata("classification")[
            "target_definition"
        ],
        "classification_target_unit": task_metadata("classification")["target_unit"],
        "classification_target_status": task_metadata("classification")[
            "target_status"
        ],
        **{f"classification_{key}": value for key, value in label_provenance.items()},
        "reg_mse": mean_squared_error(y_reg_test, reg_pred),
        **{f"cls_{key}": value for key, value in cls_metrics.items()},
        **{f"persistence_{key}": value for key, value in persistence_metrics.items()},
        "window": WINDOW,
        "warning_method": "station_v0_monthly_rate",
        "month_window_days": MONTH_WINDOW_DAYS,
        "train_rows": int(train_mask.sum()),
        "test_rows": int((~train_mask).sum()),
        "train_warning_rows": int(y_cls_train.sum()),
        "test_warning_rows": int(y_cls_test.sum()),
        "shap_explanation_sample_role": reg_provenance["explanation_sample_role"],
        "shap_explanation_start": reg_provenance["explanation_start"],
        "shap_explanation_end": reg_provenance["explanation_end"],
        "shap_explanation_date_count": reg_provenance["explanation_date_count"],
        "shap_explanation_rows": reg_provenance["explanation_rows"],
        "shap_background_sample_role": reg_provenance["background_sample_role"],
        "shap_background_start": reg_provenance["background_start"],
        "shap_background_end": reg_provenance["background_end"],
        "shap_background_date_count": reg_provenance["background_date_count"],
        "shap_background_rows": reg_provenance["background_rows"],
    }

    reg_shap = shap_matrix(reg, background, sample, "regression")
    cls_shap = shap_matrix(cls, background, sample, "classification")
    reg_importance = importance_frame(
        reg_shap,
        sample.columns,
        provenance={**common_provenance, **reg_provenance},
    )
    cls_importance = importance_frame(
        cls_shap,
        sample.columns,
        provenance={**common_provenance, **cls_provenance},
    )

    FIG_DIR.mkdir(parents=True, exist_ok=True)
    OUT_THRESHOLDS_CSV.parent.mkdir(parents=True, exist_ok=True)
    reg_importance.to_csv(OUT_REG_CSV, index=False)
    cls_importance.to_csv(OUT_CLS_CSV, index=False)
    pd.DataFrame([metrics]).to_csv(OUT_METRICS_CSV, index=False)
    pd.DataFrame(threshold_rows(thresholds)).to_csv(OUT_THRESHOLDS_CSV, index=False)
    cv_metrics = evaluate_binary_walk_forward(df)
    cv_metrics.to_csv(OUT_CV_METRICS_CSV, index=False)
    provenance = {
        "schema_version": 1,
        "data_source": str(DATA_CSV.relative_to(ROOT)),
        "explained_model": EXPLAINED_MODEL,
        "model_role": MODEL_ROLE,
        "analysis_scope": ANALYSIS_SCOPE,
        "feature_kinematics_definition": FEATURE_KINEMATICS_DEFINITION,
        "single_holdout": {
            **common_provenance,
            "train_rows": int(train_mask.sum()),
            "test_rows": int((~train_mask).sum()),
            "shap_background": {
                key.removeprefix("background_"): value
                for key, value in reg_provenance.items()
                if key.startswith("background_")
            },
            "shap_explanation": {
                key.removeprefix("explanation_"): value
                for key, value in reg_provenance.items()
                if key.startswith("explanation_")
            },
            "legacy_classification_label": label_provenance,
        },
        "tasks": {
            "regression": task_metadata("regression"),
            "classification": task_metadata("classification"),
        },
        "outputs": {
            "regression_importance": str(OUT_REG_CSV.relative_to(ROOT)),
            "classification_importance": str(OUT_CLS_CSV.relative_to(ROOT)),
            "metrics": str(OUT_METRICS_CSV.relative_to(ROOT)),
            "walk_forward_classification_metrics": str(
                OUT_CV_METRICS_CSV.relative_to(ROOT)
            ),
        },
    }
    OUT_PROVENANCE_JSON.write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    save_summary_plot(
        reg_shap,
        sample,
        OUT_REG_PNG,
        summary_title("regression", explanation_dates, len(sample)),
    )
    save_summary_plot(
        cls_shap,
        sample,
        OUT_CLS_PNG,
        summary_title("classification", explanation_dates, len(sample)),
    )

    print(f"[shap] 模型: 独立 NGBoost 解释模型（非 ConvLSTM）；样本窗口: {WINDOW} 天")
    print(
        "[shap] 分类标签: 遗留同日测点 V0 月位移量标签 "
        "warning_level >= 1（非正式五级预警）"
    )
    print(
        "[shap] SHAP 解释样本: 测试期均匀日期 × 全部测点 "
        f"{reg_provenance['explanation_start']}--{reg_provenance['explanation_end']} "
        f"(n={reg_provenance['explanation_rows']})"
    )
    for station, values in thresholds.items():
        print(
            f"[shap] {station}: V0={values['v0_mm_per_month']:.3f}, "
            f"5V0={values['v0_orange_threshold']:.3f}, "
            f"10V0={values['v0_red_threshold']:.3f} mm/M"
        )
    print(f"[shap] 回归 MSE: {metrics['reg_mse']:.4f}")
    print(f"[shap] 分类 AUC: {metrics['cls_auc']:.4f}")
    evaluated_folds = cv_metrics[cv_metrics["status"] == "evaluated"]
    print(
        f"[shap] 扩展窗口可评价折: {len(evaluated_folds)}/{len(cv_metrics)}; "
        f"输出: {OUT_CV_METRICS_CSV}"
    )
    print(f"[shap] 输出: {OUT_REG_PNG}, {OUT_CLS_PNG}, {OUT_PROVENANCE_JSON}")
    print("[shap] 回归 top10:")
    for _, row in reg_importance.head(10).iterrows():
        print(f"        {row['feature']:20s} {row['mean_abs_shap']:.5f}")
    print("[shap] 分类 top10:")
    for _, row in cls_importance.head(10).iterrows():
        print(f"        {row['feature']:20s} {row['mean_abs_shap']:.5f}")


if __name__ == "__main__":
    main()
