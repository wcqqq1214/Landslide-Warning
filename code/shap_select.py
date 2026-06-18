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
from sklearn.metrics import mean_squared_error, roc_auc_score, recall_score, precision_score, f1_score
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
STATIONS = {
    "MJ9": "MJ9/mm",
    "MJ1": "MJ1/mm",
    "MJ3": "MJ3/mm",
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
OUT_THRESHOLDS_CSV = FIG_DIR / "v0_thresholds.csv"
SHAP_SAMPLE_SIZE = 200


def build_lagged_samples(
    df,
    stations=STATIONS,
    window=WINDOW,
    month_window_days=MONTH_WINDOW_DAYS,
    thresholds=None,
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

    rows, y_reg, y_cls, meta = [], [], [], []
    for station, disp_col in stations.items():
        disp = df[disp_col].astype(float)
        delta = disp.diff()
        monthly_rate = monthly_displacement_rate(disp, month_window_days)
        warning_levels = classify_monthly_rates(
            monthly_rate,
            thresholds[station]["v0_mm_per_month"],
        )
        sample_start = max(window, month_window_days)
        for t in range(sample_start, len(df)):
            row = {}
            for lag in range(1, window + 1):
                row[f"disp_lag{lag}"] = disp.iloc[t - lag]
                for name, col in ENV_COLS.items():
                    row[f"{name}_lag{lag}"] = df[col].iloc[t - lag]
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
                "warning_level": warning_level,
            })

    return (
        pd.DataFrame(rows),
        pd.Series(y_reg, name="delta_disp"),
        pd.Series(y_cls, name="warning"),
        pd.DataFrame(meta),
    )


def train_models(X_train, y_reg_train, y_cls_train, n_estimators=300):
    reg = NGBRegressor(
        n_estimators=n_estimators,
        learning_rate=0.03,
        minibatch_frac=0.8,
        col_sample=0.8,
        random_state=0,
        verbose=False,
    )
    cls = NGBClassifier(
        Dist=k_categorical(2),
        n_estimators=n_estimators,
        learning_rate=0.03,
        minibatch_frac=0.8,
        col_sample=0.8,
        random_state=0,
        verbose=False,
    )
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


def shap_matrix(model, background, sample, task):
    columns = list(sample.columns)

    def predict_fn(values):
        values = np.asarray(values)
        if task == "classification":
            return model.predict_proba(values)[:, 1]
        return model.predict(values)

    masker = shap.maskers.Independent(background.values)
    explainer = shap.Explainer(predict_fn, masker, algorithm="permutation")
    explanation = explainer(sample.values, max_evals=2 * len(columns) + 1)
    return np.asarray(explanation.values)


def importance_frame(shap_values, columns):
    mean_abs = np.abs(shap_values).mean(axis=0)
    out = pd.DataFrame({"feature": columns, "mean_abs_shap": mean_abs})
    return out.sort_values("mean_abs_shap", ascending=False).reset_index(drop=True)


def save_summary_plot(shap_values, X, path, title):
    plt.figure(figsize=(10, 7))
    shap.summary_plot(shap_values, X, show=False, max_display=20)
    plt.title(title)
    plt.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()


def main():
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
    cls_pred = (cls_prob >= 0.5).astype(int)
    metrics = {
        "reg_mse": mean_squared_error(y_reg_test, reg_pred),
        "cls_auc": roc_auc_score(y_cls_test, cls_prob) if y_cls_test.nunique() > 1 else np.nan,
        "cls_recall": recall_score(y_cls_test, cls_pred, zero_division=0),
        "cls_precision": precision_score(y_cls_test, cls_pred, zero_division=0),
        "cls_f1": f1_score(y_cls_test, cls_pred, zero_division=0),
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
    reg_importance.to_csv(OUT_REG_CSV, index=False)
    cls_importance.to_csv(OUT_CLS_CSV, index=False)
    pd.DataFrame([metrics]).to_csv(OUT_METRICS_CSV, index=False)
    pd.DataFrame(threshold_rows(thresholds)).to_csv(OUT_THRESHOLDS_CSV, index=False)
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
    print(f"[shap] 输出: {OUT_REG_PNG}, {OUT_CLS_PNG}")
    print("[shap] 回归 top10:")
    for _, row in reg_importance.head(10).iterrows():
        print(f"        {row['feature']:20s} {row['mean_abs_shap']:.5f}")
    print("[shap] 分类 top10:")
    for _, row in cls_importance.head(10).iterrows():
        print(f"        {row['feature']:20s} {row['mean_abs_shap']:.5f}")


if __name__ == "__main__":
    main()
