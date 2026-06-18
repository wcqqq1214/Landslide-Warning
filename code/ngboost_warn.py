"""NGBoost warning-level classifier from station-specific V0 labels."""
from pathlib import Path
import pickle
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from ngboost import NGBClassifier
from ngboost.distns import k_categorical
from sklearn.metrics import confusion_matrix, classification_report
from warning_thresholds import (
    MONTH_WINDOW_DAYS,
    build_warning_frame,
    threshold_rows,
)

ROOT = Path(__file__).resolve().parent.parent
FEAT_CSV = ROOT / "data" / "features.csv"
RAW_CSV = ROOT / "data" / "monitoring_data.csv"
OUT_PKL = ROOT / "models" / "ngboost.pkl"
FIG_DIR = ROOT / "figures" / "ngboost"
OUT_PNG = FIG_DIR / "confusion_matrix.png"
OUT_THRESHOLDS_CSV = FIG_DIR / "v0_thresholds.csv"

V_COLS = [f"{s}_v" for s in
          ["MJ9", "MJ1", "MJ3", "ATU1", "ATU2", "ATU3", "ATU4", "ATU5"]]
A_COLS = [f"{s}_a" for s in
          ["MJ9", "MJ1", "MJ3", "ATU1", "ATU2", "ATU3", "ATU4", "ATU5"]]
DRIVERS = ["RWL", "RWL_rate", "Rain_cum7", "Rain_cum15", "Rain_cum30"]
WARNING_STATIONS = {
    "MJ9": "MJ9/mm",
    "MJ1": "MJ1/mm",
    "MJ3": "MJ3/mm",
}
TRAIN_FRAC = 0.8
LEVEL_NAMES = ["green", "yellow", "orange", "red"]
SEED = 0

np.random.seed(SEED)


def attach_dynamic_warning_labels(
    features,
    raw,
    stations=WARNING_STATIONS,
    thresholds=None,
    month_window_days=MONTH_WINDOW_DAYS,
):
    features = features.copy()
    features.columns = [c.strip() for c in features.columns]
    features["Date"] = pd.to_datetime(features["Date"])
    warning_frame, thresholds = build_warning_frame(
        raw,
        stations=stations,
        thresholds=thresholds,
        month_window_days=month_window_days,
    )
    merged = features.merge(warning_frame, on="Date", how="inner")
    merged = merged[merged["warning_level"] >= 0].reset_index(drop=True)
    return merged, thresholds


def class_count_for_labels(labels):
    labels = np.asarray(labels, dtype=int)
    if labels.size == 0 or labels.min() < 0:
        raise ValueError("预警标签为空或包含无效等级")
    class_count = int(labels.max()) + 1
    if class_count < 2:
        raise ValueError("训练数据只有一个预警等级，无法训练分类器")
    return class_count


def main():
    features = pd.read_csv(FEAT_CSV)
    raw = pd.read_csv(RAW_CSV)
    df, thresholds = attach_dynamic_warning_labels(features, raw)
    y = df["warning_level"].to_numpy(dtype=int)

    X = pd.DataFrame({
        "v_mean": df[V_COLS].mean(axis=1),
        "v_max": df[V_COLS].max(axis=1),
        "a_mean": df[A_COLS].mean(axis=1),
        "a_max": df[A_COLS].max(axis=1),
    })
    for c in DRIVERS:
        X[c] = df[c]

    split = int(len(X) * TRAIN_FRAC)
    Xtr, Xte = X.iloc[:split], X.iloc[split:]
    ytr, yte = y[:split], y[split:]

    k = class_count_for_labels(ytr)
    model = NGBClassifier(Dist=k_categorical(k), verbose=False, random_state=SEED)
    model.fit(Xtr.values, ytr)

    proba = model.predict_proba(Xte.values)
    pred = proba.argmax(axis=1)

    OUT_PKL.parent.mkdir(parents=True, exist_ok=True)
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_PKL, "wb") as f:
        pickle.dump(model, f)
    pd.DataFrame(threshold_rows(thresholds)).to_csv(OUT_THRESHOLDS_CSV, index=False)

    present = sorted(set(np.concatenate([yte, pred])))
    cm = confusion_matrix(yte, pred, labels=present)
    fig, ax = plt.subplots(figsize=(6, 5))
    im = ax.imshow(cm, cmap="Blues")
    ax.set_xticks(range(len(present))); ax.set_yticks(range(len(present)))
    labs = [LEVEL_NAMES[i] for i in present]
    ax.set_xticklabels(labs, rotation=45, ha="right"); ax.set_yticklabels(labs)
    ax.set_xlabel("predicted"); ax.set_ylabel("actual")
    ax.set_title("NGBoost warning-level confusion matrix (test set)")
    for i in range(len(present)):
        for j in range(len(present)):
            ax.text(j, i, cm[i, j], ha="center", va="center",
                    color="white" if cm[i, j] > cm.max() / 2 else "black")
    fig.colorbar(im); plt.tight_layout(); plt.savefig(OUT_PNG, dpi=150); plt.close()

    print(f"[ngboost] 模型输出: {OUT_PKL}")
    print(f"[ngboost] 混淆矩阵图: {OUT_PNG}")
    print(f"[ngboost] 动态阈值: {OUT_THRESHOLDS_CSV}")
    for station, values in thresholds.items():
        print(
            f"        {station}: V0={values['v0_mm_per_month']:.3f}, "
            f"5V0={values['v0_orange_threshold']:.3f}, "
            f"10V0={values['v0_red_threshold']:.3f} mm/M"
        )
    print(f"[ngboost] 全样本各级数量(类别不平衡透明化):")
    for lv in range(len(LEVEL_NAMES)):
        print(f"        级{lv} {LEVEL_NAMES[lv]:12s}: {(y == lv).sum()}")
    print(f"[ngboost] 训练集实际类别数: {k}; 未出现的高等级保留在 V0 规则中，不参与本次拟合")
    acc = (pred == yte).mean()
    print(f"[ngboost] 测试集准确率: {acc:.3f}")
    print(classification_report(yte, pred,
          labels=present, target_names=[LEVEL_NAMES[i] for i in present],
          zero_division=0))


if __name__ == "__main__":
    main()
