"""NGBoost warning-level classifier from tangent-angle labels."""
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

ROOT = Path(__file__).resolve().parent.parent
FEAT_CSV = ROOT / "data" / "features.csv"
OUT_PKL = ROOT / "models" / "ngboost.pkl"
OUT_PNG = ROOT / "figures" / "confusion_matrix.png"

ALPHA_COLS = [f"{s}_alpha" for s in
              ["MJ9", "MJ1", "MJ3", "ATU1", "ATU2", "ATU3", "ATU4", "ATU5"]]
V_COLS = [f"{s}_v" for s in
          ["MJ9", "MJ1", "MJ3", "ATU1", "ATU2", "ATU3", "ATU4", "ATU5"]]
A_COLS = [f"{s}_a" for s in
          ["MJ9", "MJ1", "MJ3", "ATU1", "ATU2", "ATU3", "ATU4", "ATU5"]]
DRIVERS = ["RWL", "RWL_rate", "Rain_cum7", "Rain_cum15", "Rain_cum30"]
ALPHA_THRESH = [45, 80, 85]
TRAIN_FRAC = 0.8
LEVEL_NAMES = ["stable", "early-accel", "mid-accel", "critical"]
SEED = 0

np.random.seed(SEED)


def alpha_to_level(alpha_row):
    """单测点切线角 -> 等级 0..3。"""
    lv = np.zeros_like(alpha_row, dtype=int)
    lv = np.where(alpha_row >= ALPHA_THRESH[0], 1, lv)
    lv = np.where(alpha_row >= ALPHA_THRESH[1], 2, lv)
    lv = np.where(alpha_row >= ALPHA_THRESH[2], 3, lv)
    return lv


def main():
    df = pd.read_csv(FEAT_CSV)

    levels = alpha_to_level(df[ALPHA_COLS].values)
    y = levels.max(axis=1)

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

    k = len(LEVEL_NAMES)
    model = NGBClassifier(Dist=k_categorical(k), verbose=False, random_state=SEED)
    model.fit(Xtr.values, ytr)

    proba = model.predict_proba(Xte.values)
    pred = proba.argmax(axis=1)

    OUT_PKL.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PKL, "wb") as f:
        pickle.dump(model, f)

    present = sorted(set(np.concatenate([yte, pred])))
    cm = confusion_matrix(yte, pred, labels=present)
    OUT_PNG.parent.mkdir(parents=True, exist_ok=True)
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
    print(f"[ngboost] 全样本各级数量(类别不平衡透明化):")
    for lv in range(k):
        print(f"        级{lv} {LEVEL_NAMES[lv]:12s}: {(y == lv).sum()}")
    acc = (pred == yte).mean()
    print(f"[ngboost] 测试集准确率: {acc:.3f}")
    print(classification_report(yte, pred,
          labels=present, target_names=[LEVEL_NAMES[i] for i in present],
          zero_division=0))


if __name__ == "__main__":
    main()
