"""Rank reservoir level and rainfall drivers with SHAP."""
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import shap
from sklearn.ensemble import RandomForestRegressor

ROOT = Path(__file__).resolve().parent.parent
FEAT_CSV = ROOT / "data" / "features.csv"
OUT_PNG = ROOT / "figures" / "shap_summary.png"

TARGET = "ATU5_v"
DRIVERS = ["RWL", "RWL_rate", "Rain", "Rain_cum7", "Rain_cum15", "Rain_cum30"]


def main():
    df = pd.read_csv(FEAT_CSV)
    X = df[DRIVERS]
    y = df[TARGET]

    model = RandomForestRegressor(n_estimators=300, random_state=0, n_jobs=-1)
    model.fit(X, y)

    explainer = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X)

    OUT_PNG.parent.mkdir(parents=True, exist_ok=True)
    shap.summary_plot(shap_values, X, show=False)
    plt.tight_layout()
    plt.savefig(OUT_PNG, dpi=150)
    plt.close()

    import numpy as np
    mean_abs = np.abs(shap_values).mean(axis=0)
    ranking = sorted(zip(DRIVERS, mean_abs), key=lambda t: t[1], reverse=True)
    print(f"[shap] 目标变量: {TARGET}")
    print(f"[shap] SHAP 图输出: {OUT_PNG}")
    print(f"[shap] 因子重要性排序(mean|SHAP|):")
    for name, val in ranking:
        print(f"        {name:12s} {val:.4f}")


if __name__ == "__main__":
    main()
