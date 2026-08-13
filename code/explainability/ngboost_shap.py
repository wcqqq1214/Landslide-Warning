"""Independent NGBoost--SHAP analysis for candidate model dependence.

This module deliberately does not explain ConvLSTM and does not train a
warning classifier.  It explains a separate NGBoost regressor whose target is
the next observed displacement increment.  The resulting SHAP values are
therefore exploratory model-dependence evidence, not causal control factors
or five-level warning probabilities.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import shap
from ngboost import NGBRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error

CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

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

OUT_PNG = FIG_DIR / "ngboost_regression_shap.png"
OUT_IMPORTANCE = FIG_DIR / "ngboost_regression_shap_importance.csv"
OUT_METRICS = FIG_DIR / "ngboost_regression_metrics.csv"
OUT_PROVENANCE = FIG_DIR / "ngboost_regression_shap_provenance.json"

SHAP_BACKGROUND_DATE_COUNT = 12
SHAP_EXPLANATION_DATE_COUNT = 25
SEED = 0
EXPLAINED_MODEL = "NGBoost regressor"
MODEL_ROLE = "independent_exploratory_explainer"
ANALYSIS_SCOPE = (
    "independent NGBoost regression model-dependence analysis; not ConvLSTM "
    "explanation, causal inference, warning classification, or formal five-level output"
)
TARGET_DEFINITION = "target-observation displacement increment U_t - U_(t-1)"
TARGET_UNIT = "mm per observation interval"
FEATURE_KINEMATICS_DEFINITION = (
    "lagged point velocity (mm/day) and delta_v=velocity_t-velocity_t-1 "
    "(mm/day); delta_v is retained as an input feature, not a warning label"
)


def _date_to_iso(value: object) -> str:
    return pd.Timestamp(value).date().isoformat()


def _sha256_file(path: Path) -> str:
    """Return a streaming SHA-256 digest for one provenance input."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_lagged_samples(
    df: pd.DataFrame,
    *,
    stations: dict[str, str] = STATIONS,
    window: int = WINDOW,
    rain_windows: tuple[int, ...] = RAIN_WINDOWS,
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame]:
    """Build causal lagged inputs and a next-observation increment target."""

    ordered = df.copy()
    ordered.columns = [column.strip() for column in ordered.columns]
    ordered["Date"] = pd.to_datetime(ordered["Date"])
    ordered = ordered.sort_values("Date").reset_index(drop=True)
    rainfall = ordered[ENV_COLS["Rainfall"]].astype(float)
    rwl_rate = compute_point_kinematics(
        ordered["Date"], ordered[ENV_COLS["RWL"]]
    )["velocity"]
    gwt_rate = compute_point_kinematics(
        ordered["Date"], ordered[ENV_COLS["GWT"]]
    )["velocity"]
    rainfall_cumulative = {
        days: rainfall.rolling(days, min_periods=days).sum() for days in rain_windows
    }
    warmup = max(window + 2, max(rain_windows) + window - 1)

    rows: list[dict[str, float | int]] = []
    targets: list[float] = []
    metadata: list[dict[str, object]] = []
    for station, displacement_column in stations.items():
        displacement = ordered[displacement_column].astype(float)
        target_increment = displacement.diff()
        kinematics = compute_point_kinematics(ordered["Date"], displacement)
        for index in range(warmup, len(ordered)):
            row: dict[str, float | int] = {}
            for lag in range(1, window + 1):
                source_index = index - lag
                row[f"disp_lag{lag}"] = float(displacement.iloc[source_index])
                row[f"disp_rate_lag{lag}"] = float(
                    kinematics["velocity"].iloc[source_index]
                )
                row[f"disp_delta_v_lag{lag}"] = float(
                    kinematics["delta_v"].iloc[source_index]
                )
                for name, column in ENV_COLS.items():
                    row[f"{name}_lag{lag}"] = float(ordered[column].iloc[source_index])
                row[f"RWL_rate_lag{lag}"] = float(rwl_rate.iloc[source_index])
                row[f"GWT_rate_lag{lag}"] = float(gwt_rate.iloc[source_index])
                for days, cumulative in rainfall_cumulative.items():
                    row[f"Rain_cum{days}_lag{lag}"] = float(
                        cumulative.iloc[source_index]
                    )
            for station_name in stations:
                row[f"station_{station_name}"] = int(station_name == station)
            if not np.isfinite(list(row.values())).all() or not np.isfinite(
                target_increment.iloc[index]
            ):
                continue
            rows.append(row)
            targets.append(float(target_increment.iloc[index]))
            metadata.append({"Date": ordered["Date"].iloc[index], "station": station})

    return (
        pd.DataFrame(rows),
        pd.Series(targets, name="target_displacement_increment"),
        pd.DataFrame(metadata),
    )


def make_regressor(n_estimators: int = 300) -> NGBRegressor:
    """Create the fixed exploratory regressor used only for SHAP analysis."""

    return NGBRegressor(
        n_estimators=n_estimators,
        learning_rate=0.03,
        minibatch_frac=0.8,
        col_sample=0.8,
        random_state=SEED,
        verbose=False,
    )


def time_train_mask(meta: pd.DataFrame, train_frac: float = 0.8) -> tuple[pd.Series, pd.Timestamp]:
    """Return a chronological holdout split without mixing dates."""

    dates = pd.DatetimeIndex(meta["Date"].unique()).sort_values()
    if len(dates) < 2:
        raise ValueError("至少需要两个不同日期才能进行时间切分")
    split_index = min(max(int(len(dates) * train_frac), 1), len(dates) - 1)
    split_date = dates[split_index]
    return meta["Date"] < split_date, split_date


def evenly_spaced_date_sample(
    X: pd.DataFrame,
    meta: pd.DataFrame,
    mask: pd.Series,
    date_count: int,
) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    """Select all stations on deterministic, evenly spaced eligible dates."""

    eligible_dates = pd.DatetimeIndex(meta.loc[mask, "Date"].unique()).sort_values()
    if len(eligible_dates) == 0:
        raise ValueError("日期范围内没有可抽取样本")
    count = min(date_count, len(eligible_dates))
    selected_indices = np.rint(np.linspace(0, len(eligible_dates) - 1, count)).astype(int)
    selected_dates = eligible_dates[np.unique(selected_indices)]
    return X.loc[mask & meta["Date"].isin(selected_dates)].copy(), selected_dates


def shap_matrix(
    model: NGBRegressor, background: pd.DataFrame, sample: pd.DataFrame
) -> np.ndarray:
    """Compute deterministic permutation SHAP values for the independent model."""

    masker = shap.maskers.Independent(background.values)
    explainer = shap.Explainer(
        lambda values: model.predict(np.asarray(values)),
        masker,
        algorithm="permutation",
        seed=SEED,
    )
    explanation = explainer(sample.values, max_evals=2 * len(sample.columns) + 1)
    return np.asarray(explanation.values)


def importance_frame(
    shap_values: np.ndarray, columns: pd.Index, provenance: dict[str, object]
) -> pd.DataFrame:
    """Summarize mean absolute SHAP values with explicit interpretation fields."""

    frame = pd.DataFrame(
        {"feature": columns, "mean_abs_shap": np.abs(shap_values).mean(axis=0)}
    ).sort_values("mean_abs_shap", ascending=False)
    for name, value in provenance.items():
        frame[name] = value
    return frame.reset_index(drop=True)


def regression_metrics(y_true: pd.Series, prediction: np.ndarray) -> dict[str, float | int]:
    """Return descriptive holdout metrics for the explanatory regression only."""

    return {
        "rmse": float(mean_squared_error(y_true, prediction) ** 0.5),
        "mae": float(mean_absolute_error(y_true, prediction)),
        "rows": int(len(y_true)),
    }


def _summary_title(dates: pd.DatetimeIndex, rows: int) -> str:
    return (
        "Independent NGBoost regression SHAP (not ConvLSTM)\n"
        "target: next-observation displacement increment\n"
        f"holdout: {_date_to_iso(dates.min())} to {_date_to_iso(dates.max())}; "
        f"n={rows}; exploratory model dependence only"
    )


def _save_summary_plot(shap_values: np.ndarray, sample: pd.DataFrame, path: Path, title: str) -> None:
    plt.figure(figsize=(10, 7))
    shap.summary_plot(
        shap_values,
        sample,
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


def main() -> None:
    """Materialize the current independent NGBoost--SHAP artifact bundle."""

    np.random.seed(SEED)
    X, target, meta = build_lagged_samples(pd.read_csv(DATA_CSV))
    train_mask, split_date = time_train_mask(meta)
    model = make_regressor()
    model.fit(X.loc[train_mask].values, target.loc[train_mask].values)
    prediction = model.predict(X.loc[~train_mask].values)
    background, background_dates = evenly_spaced_date_sample(
        X, meta, train_mask, SHAP_BACKGROUND_DATE_COUNT
    )
    sample, explanation_dates = evenly_spaced_date_sample(
        X, meta, ~train_mask, SHAP_EXPLANATION_DATE_COUNT
    )
    provenance: dict[str, object] = {
        "schema_version": 2,
        "explained_model": EXPLAINED_MODEL,
        "model_role": MODEL_ROLE,
        "analysis_scope": ANALYSIS_SCOPE,
        "target_definition": TARGET_DEFINITION,
        "target_unit": TARGET_UNIT,
        "feature_kinematics_definition": FEATURE_KINEMATICS_DEFINITION,
        "split_date": _date_to_iso(split_date),
        "train_start": _date_to_iso(meta.loc[train_mask, "Date"].min()),
        "train_end": _date_to_iso(meta.loc[train_mask, "Date"].max()),
        "holdout_start": _date_to_iso(meta.loc[~train_mask, "Date"].min()),
        "holdout_end": _date_to_iso(meta.loc[~train_mask, "Date"].max()),
        "background_date_count": int(len(background_dates)),
        "background_rows": int(len(background)),
        "background_start": _date_to_iso(background_dates.min()),
        "background_end": _date_to_iso(background_dates.max()),
        "explanation_date_count": int(len(explanation_dates)),
        "explanation_rows": int(len(sample)),
        "explanation_start": _date_to_iso(explanation_dates.min()),
        "explanation_end": _date_to_iso(explanation_dates.max()),
        "data_source": str(DATA_CSV.relative_to(ROOT)),
        "data_sha256": _sha256_file(DATA_CSV),
        "implementation_source": str(Path(__file__).resolve().relative_to(ROOT)),
        "implementation_sha256": _sha256_file(Path(__file__).resolve()),
    }
    shap_values = shap_matrix(model, background, sample)
    importance = importance_frame(shap_values, sample.columns, provenance)
    metrics = {**provenance, **regression_metrics(target.loc[~train_mask], prediction)}
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    importance.to_csv(OUT_IMPORTANCE, index=False)
    pd.DataFrame([metrics]).to_csv(OUT_METRICS, index=False)
    OUT_PROVENANCE.write_text(
        json.dumps(
            {
                **provenance,
                "outputs": {
                    "summary_plot": str(OUT_PNG.relative_to(ROOT)),
                    "importance": str(OUT_IMPORTANCE.relative_to(ROOT)),
                    "metrics": str(OUT_METRICS.relative_to(ROOT)),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _save_summary_plot(shap_values, sample, OUT_PNG, _summary_title(explanation_dates, len(sample)))
    print(
        "[ngboost-shap] wrote independent regression model-dependence analysis: "
        f"{OUT_PROVENANCE}"
    )


if __name__ == "__main__":
    main()
