"""SHAP feature analysis with five-day lagged monitoring factors."""
from pathlib import Path
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
from warning_thresholds import (
    MONTH_WINDOW_DAYS,
    classify_monthly_rates,
    compute_station_thresholds,
    monthly_displacement_rate,
    threshold_rows,
)

ROOT = Path(__file__).resolve().parent.parent
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
OUT_THRESHOLDS_CSV = ROOT / "figures" / "thresholds" / "v0_thresholds.csv"
SHAP_SAMPLE_SIZE = 200
SEED = 0


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
    rwl_rate = df[ENV_COLS["RWL"]].astype(float).diff()
    gwt_rate = df[ENV_COLS["GWT"]].astype(float).diff()
    rainfall_cumulative = {
        days: rainfall.rolling(days, min_periods=days).sum()
        for days in rain_windows
    }
    derived_warmup = window + 2
    if rain_windows:
        derived_warmup = max(derived_warmup, max(rain_windows) + window - 1)

    rows, y_reg, y_cls, meta = [], [], [], []
    for station, disp_col in stations.items():
        disp = df[disp_col].astype(float)
        delta = disp.diff()
        displacement_rate = disp.diff()
        displacement_acceleration = displacement_rate.diff()
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
                row[f"disp_accel_lag{lag}"] = displacement_acceleration.iloc[t - lag]
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
            meta.append({
                "Date": df["Date"].iloc[t],
                "station": station,
                "monthly_rate": monthly_rate.iloc[t],
                "previous_monthly_rate": monthly_rate.iloc[t - 1],
                "v0_mm_per_month": thresholds[station]["v0_mm_per_month"],
                "previous_warning_level": int(warning_levels[t - 1]),
                "warning_level": warning_level,
            })

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


def binary_probability_metrics(y_true, probability, threshold=0.5):
    """Return discrimination, calibration, and threshold metrics."""
    y_true = np.asarray(y_true, dtype=int)
    probability = np.asarray(probability, dtype=float)
    prediction = (probability >= threshold).astype(int)
    has_both_classes = len(np.unique(y_true)) == 2
    return {
        "auc": roc_auc_score(y_true, probability) if has_both_classes else np.nan,
        "pr_auc": (
            average_precision_score(y_true, probability)
            if has_both_classes else np.nan
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
    score = (
        meta["previous_monthly_rate"].to_numpy(dtype=float)
        / meta["v0_mm_per_month"].to_numpy(dtype=float)
    )
    prediction = meta["previous_warning_level"].to_numpy(dtype=int) >= 1
    has_both_classes = len(np.unique(y_true)) == 2
    return {
        "auc": roc_auc_score(y_true, score) if has_both_classes else np.nan,
        "pr_auc": (
            average_precision_score(y_true, score)
            if has_both_classes else np.nan
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
        ranges.append({
            "fold": fold,
            "train_start": unique_dates[train_indices[0]],
            "train_end": unique_dates[train_indices[-1]],
            "test_start": unique_dates[test_indices[0]],
            "test_end": unique_dates[test_indices[-1]],
        })
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
            "task": "binary_warning_state",
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
        rows.append({
            **row,
            "status": "evaluated" if y_test.nunique() == 2 else "single_class_test",
            **{f"test_{key}": value for key, value in metrics.items()},
            **{f"baseline_{key}": value for key, value in baseline.items()},
        })

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


def importance_frame(shap_values, columns):
    mean_abs = np.abs(shap_values).mean(axis=0)
    out = pd.DataFrame({"feature": columns, "mean_abs_shap": mean_abs})
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
    plt.title(title)
    plt.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()


def main():
    np.random.seed(SEED)
    df = pd.read_csv(DATA_CSV)
    thresholds = compute_station_thresholds(df, STATIONS)
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
    metrics = {
        "task": "binary_warning_state",
        "reg_mse": mean_squared_error(y_reg_test, reg_pred),
        **{f"cls_{key}": value for key, value in cls_metrics.items()},
        **{
            f"persistence_{key}": value
            for key, value in persistence_metrics.items()
        },
        "window": WINDOW,
        "warning_method": "station_v0_monthly_rate",
        "month_window_days": MONTH_WINDOW_DAYS,
        "split_date": split_date.date().isoformat(),
        "train_rows": int(train_mask.sum()),
        "test_rows": int((~train_mask).sum()),
        "train_warning_rows": int(y_cls_train.sum()),
        "test_warning_rows": int(y_cls_test.sum()),
    }

    sample = X_train.tail(min(SHAP_SAMPLE_SIZE, len(X_train)))
    background = X_train.iloc[::max(1, len(X_train) // 100)].tail(min(100, len(X_train)))
    reg_shap = shap_matrix(reg, background, sample, "regression")
    cls_shap = shap_matrix(cls, background, sample, "classification")
    reg_importance = importance_frame(reg_shap, sample.columns)
    cls_importance = importance_frame(cls_shap, sample.columns)

    FIG_DIR.mkdir(parents=True, exist_ok=True)
    OUT_THRESHOLDS_CSV.parent.mkdir(parents=True, exist_ok=True)
    reg_importance.to_csv(OUT_REG_CSV, index=False)
    cls_importance.to_csv(OUT_CLS_CSV, index=False)
    pd.DataFrame([metrics]).to_csv(OUT_METRICS_CSV, index=False)
    pd.DataFrame(threshold_rows(thresholds)).to_csv(OUT_THRESHOLDS_CSV, index=False)
    cv_metrics = evaluate_binary_walk_forward(df)
    cv_metrics.to_csv(OUT_CV_METRICS_CSV, index=False)
    save_summary_plot(reg_shap, sample, OUT_REG_PNG, "SHAP summary - displacement increment")
    save_summary_plot(cls_shap, sample, OUT_CLS_PNG, "SHAP summary - warning state")

    print(f"[shap] 模型: NGBoost; 样本窗口: {WINDOW} 天; 预警标签: 各测点动态 V0")
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
    print(f"[shap] 输出: {OUT_REG_PNG}, {OUT_CLS_PNG}")
    print("[shap] 回归 top10:")
    for _, row in reg_importance.head(10).iterrows():
        print(f"        {row['feature']:20s} {row['mean_abs_shap']:.5f}")
    print("[shap] 分类 top10:")
    for _, row in cls_importance.head(10).iterrows():
        print(f"        {row['feature']:20s} {row['mean_abs_shap']:.5f}")


if __name__ == "__main__":
    main()
