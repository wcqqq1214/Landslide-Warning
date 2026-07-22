"""Cross-fold SHAP stability and preregistered feature-group ablation."""

from itertools import combinations
from pathlib import Path
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error

CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from explainability.shap_select import (  # noqa: E402
    ANALYSIS_SCOPE,
    DATA_CSV,
    EXPLAINED_MODEL,
    ENV_COLS,
    FEATURE_KINEMATICS_DEFINITION,
    MODEL_ROLE,
    MONTH_WINDOW_DAYS,
    RAIN_WINDOWS,
    SEED,
    STATIONS,
    WINDOW,
    binary_probability_metrics,
    build_lagged_samples,
    evenly_spaced_date_sample,
    make_classifier,
    make_regressor,
    shap_matrix,
    task_metadata,
    legacy_v0_label_provenance,
    walk_forward_date_ranges,
)
from warning.warning_thresholds import compute_station_thresholds  # noqa: E402
from warning.legacy_warning import (  # noqa: E402
    attach_legacy_warning_metadata,
    write_legacy_warning_manifest,
)

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "figures" / "shap" / "stability"
OUT_PROTOCOL = OUT_DIR / "cross_fold_protocol.csv"
OUT_FEATURE_IMPORTANCE = OUT_DIR / "cross_fold_feature_importance.csv"
OUT_FEATURE_STABILITY = OUT_DIR / "cross_fold_feature_stability.csv"
OUT_RANK_STABILITY = OUT_DIR / "cross_fold_rank_stability.csv"
OUT_STATION_FEATURE_IMPORTANCE = OUT_DIR / "cross_fold_station_feature_importance.csv"
OUT_STATION_FEATURE_STABILITY = OUT_DIR / "cross_fold_station_feature_stability.csv"
OUT_GROUP_IMPORTANCE = OUT_DIR / "cross_fold_group_importance.csv"
OUT_ABLATION_FOLDS = OUT_DIR / "group_ablation_fold_metrics.csv"
OUT_ABLATION_SUMMARY = OUT_DIR / "group_ablation_summary.csv"
OUT_GROUP_PLOT = OUT_DIR / "shap_group_stability.png"
OUT_ABLATION_PLOT = OUT_DIR / "group_ablation.png"
OUT_LEGACY_MANIFEST = OUT_DIR / "legacy_warning_manifest.json"

N_SPLITS = 5
N_ESTIMATORS = 300
BACKGROUND_DATE_COUNT = 12
EXPLANATION_DATE_COUNT = 24
TOP_K = 10

GROUP_ORDER = (
    "displacement_kinematics",
    "raw_environment",
    "environmental_rates",
    "cumulative_rainfall",
    "station_identity",
)
EXPECTED_GROUP_COUNTS = {
    "displacement_kinematics": 15,
    "raw_environment": 40,
    "environmental_rates": 10,
    "cumulative_rainfall": 15,
    "station_identity": 8,
}


def task_provenance(task):
    """Return the interpretive boundary attached to one stability task."""
    return {
        "explained_model": EXPLAINED_MODEL,
        "model_role": MODEL_ROLE,
        "analysis_scope": ANALYSIS_SCOPE,
        "feature_kinematics_definition": FEATURE_KINEMATICS_DEFINITION,
        **task_metadata(task),
    }


def attach_task_provenance(frame):
    """Append task-level model and target provenance to an output frame."""
    out = frame.copy()
    for task in out["task"].dropna().unique():
        mask = out["task"].eq(task)
        for field, value in task_provenance(task).items():
            out.loc[mask, field] = value
    return out


def assign_feature_group(feature):
    """Map one current NGBoost input to a preregistered group."""
    if feature.startswith(("disp_lag", "disp_rate_lag", "disp_delta_v_lag")):
        return "displacement_kinematics"
    if feature.startswith("station_"):
        return "station_identity"
    if feature.startswith("Rain_cum"):
        return "cumulative_rainfall"
    if feature.startswith(("RWL_rate_lag", "GWT_rate_lag")):
        return "environmental_rates"
    if any(feature.startswith(f"{name}_lag") for name in ENV_COLS):
        return "raw_environment"
    raise ValueError(f"未注册特征组: {feature}")


def build_feature_groups(columns, *, validate_counts=False):
    groups = {group: [] for group in GROUP_ORDER}
    for feature in columns:
        groups[assign_feature_group(feature)].append(feature)
    if validate_counts:
        counts = {group: len(features) for group, features in groups.items()}
        if counts != EXPECTED_GROUP_COUNTS:
            raise ValueError(f"特征组数量与预注册不一致: {counts}")
    return groups


def spearman_from_values(left, right):
    """Compute Spearman correlation without inferential significance tests."""
    left = pd.Series(np.asarray(left, dtype=float))
    right = pd.Series(np.asarray(right, dtype=float))
    if len(left) != len(right):
        raise ValueError("秩相关输入长度不一致")
    valid = left.notna() & right.notna()
    left = left.loc[valid]
    right = right.loc[valid]
    if len(left) < 2 or left.nunique() < 2 or right.nunique() < 2:
        return np.nan
    left_rank = left.rank(method="average").to_numpy(dtype=float)
    right_rank = right.rank(method="average").to_numpy(dtype=float)
    return float(np.corrcoef(left_rank, right_rank)[0, 1])


def fold_feature_importance(
    shap_values,
    sample,
    task,
    fold,
    *,
    station=None,
    provenance=None,
):
    """Summarize one SHAP matrix, optionally for one station stratum."""
    mean_abs = np.abs(shap_values).mean(axis=0)
    total = float(mean_abs.sum())
    frame = pd.DataFrame(
        {
            "task": task,
            "fold": fold,
            "feature": sample.columns,
            "feature_group": [assign_feature_group(name) for name in sample.columns],
            "mean_abs_shap": mean_abs,
            "normalized_share": mean_abs / total if total > 0 else np.nan,
        }
    )
    if station is not None:
        frame.insert(2, "station", station)
    if provenance:
        for field, value in provenance.items():
            frame[field] = value
    frame["rank"] = frame["mean_abs_shap"].rank(
        method="average",
        ascending=False,
    )
    frame["direction_spearman"] = [
        np.nan
        if feature.startswith("station_")
        else spearman_from_values(sample[feature], shap_values[:, index])
        for index, feature in enumerate(sample.columns)
    ]
    return frame.sort_values("rank").reset_index(drop=True)


def fold_group_importance(feature_frame):
    grouped = feature_frame.groupby(
        ["task", "fold", "feature_group"], as_index=False
    ).agg(
        mean_abs_shap=("mean_abs_shap", "sum"),
        normalized_share=("normalized_share", "sum"),
        feature_count=("feature", "count"),
    )
    grouped["rank"] = grouped.groupby(["task", "fold"])["mean_abs_shap"].rank(
        method="average", ascending=False
    )
    return grouped.sort_values(["task", "fold", "rank"]).reset_index(drop=True)


def summarize_feature_stability(feature_importance, top_k=TOP_K, strata=()):
    """Summarize feature ranks and directions across folds and optional strata."""
    group_columns = ["task", *strata, "feature", "feature_group"]
    rows = []
    for keys, frame in feature_importance.groupby(
        group_columns,
        sort=False,
    ):
        row_identity = dict(zip(group_columns, keys, strict=True))
        directions = frame["direction_spearman"].dropna()
        positive = int((directions > 0).sum())
        negative = int((directions < 0).sum())
        zero = int((directions == 0).sum())
        valid = positive + negative + zero
        if valid == 0:
            dominant = "not_applicable"
            consistency = np.nan
        elif positive > negative and positive > zero:
            dominant = "positive"
            consistency = positive / valid
        elif negative > positive and negative > zero:
            dominant = "negative"
            consistency = negative / valid
        elif zero > positive and zero > negative:
            dominant = "zero"
            consistency = zero / valid
        else:
            dominant = "mixed"
            consistency = max(positive, negative, zero) / valid
        rank_q25 = frame["rank"].quantile(0.25)
        rank_q75 = frame["rank"].quantile(0.75)
        rows.append(
            {
                **row_identity,
                "fold_count": int(frame["fold"].nunique()),
                "mean_normalized_share": frame["normalized_share"].mean(),
                "median_rank": frame["rank"].median(),
                "rank_q25": rank_q25,
                "rank_q75": rank_q75,
                "rank_iqr": rank_q75 - rank_q25,
                "best_rank": frame["rank"].min(),
                "worst_rank": frame["rank"].max(),
                "top_k": top_k,
                "top_k_folds": int((frame["rank"] <= top_k).sum()),
                "direction_valid_folds": valid,
                "direction_positive_folds": positive,
                "direction_negative_folds": negative,
                "direction_zero_folds": zero,
                "dominant_direction": dominant,
                "direction_consistency": consistency,
                "median_direction_spearman": directions.median() if valid else np.nan,
            }
        )
    return (
        pd.DataFrame(rows)
        .sort_values(
            ["task", *strata, "median_rank", "mean_normalized_share"],
            ascending=[True] * (2 + len(strata)) + [False],
        )
        .reset_index(drop=True)
    )


def pairwise_rank_stability(feature_importance, group_importance):
    rows = []
    for level, frame, item_column in (
        ("feature", feature_importance, "feature"),
        ("group", group_importance, "feature_group"),
    ):
        for task, task_frame in frame.groupby("task", sort=False):
            rank_table = task_frame.pivot(
                index=item_column,
                columns="fold",
                values="rank",
            )
            for fold_a, fold_b in combinations(rank_table.columns, 2):
                pair = rank_table[[fold_a, fold_b]].dropna()
                rows.append(
                    {
                        "task": task,
                        "level": level,
                        "fold_a": int(fold_a),
                        "fold_b": int(fold_b),
                        "item_count": int(len(pair)),
                        "spearman_rank_correlation": spearman_from_values(
                            pair[fold_a],
                            pair[fold_b],
                        ),
                    }
                )
    return pd.DataFrame(rows)


def regression_metrics(y_true, prediction):
    return {
        "rmse": mean_squared_error(y_true, prediction) ** 0.5,
        "mae": mean_absolute_error(y_true, prediction),
    }


def fit_and_score(task, X_train, X_test, y_train, y_test):
    if task == "regression":
        model = make_regressor(n_estimators=N_ESTIMATORS)
        model.fit(X_train.values, y_train.values)
        metrics = regression_metrics(y_test, model.predict(X_test.values))
        baseline = regression_metrics(y_test, np.zeros(len(y_test)))
        metrics.update({f"baseline_{name}": value for name, value in baseline.items()})
        return model, metrics
    model = make_classifier(n_estimators=N_ESTIMATORS)
    model.fit(X_train.values, y_train.values)
    probability = model.predict_proba(X_test.values)[:, 1]
    return model, binary_probability_metrics(y_test, probability)


def score_degradation(task, metric, full_value, ablated_value):
    if pd.isna(full_value) or pd.isna(ablated_value):
        return np.nan
    if task == "regression" or metric == "brier":
        return ablated_value - full_value
    return full_value - ablated_value


def summarize_ablation(ablation_folds):
    rows = []
    for task, metric in (("regression", "mae"), ("classification", "brier")):
        task_frame = ablation_folds[ablation_folds["task"] == task]
        full = task_frame[task_frame["model_variant"] == "full"].set_index("fold")
        for group in GROUP_ORDER:
            ablated = task_frame[
                (task_frame["model_variant"] == "drop_group")
                & (task_frame["dropped_group"] == group)
            ].set_index("fold")
            common = full.index.intersection(ablated.index)
            degradation = pd.Series(
                [
                    score_degradation(
                        task,
                        metric,
                        full.loc[fold, metric],
                        ablated.loc[fold, metric],
                    )
                    for fold in common
                ],
                index=common,
                dtype=float,
            ).dropna()
            rows.append(
                {
                    "task": task,
                    "dropped_group": group,
                    "primary_metric": metric,
                    "degradation_definition": "ablated_minus_full",
                    "eligible_folds": int(len(degradation)),
                    "mean_degradation": degradation.mean(),
                    "median_degradation": degradation.median(),
                    "min_degradation": degradation.min(),
                    "max_degradation": degradation.max(),
                    "positive_degradation_folds": int((degradation > 0).sum()),
                    "negative_degradation_folds": int((degradation < 0).sum()),
                    "zero_degradation_folds": int((degradation == 0).sum()),
                }
            )
    return pd.DataFrame(rows)


def plot_group_stability(group_importance, path):
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.8), constrained_layout=True)
    for axis, task in zip(axes, ("regression", "classification")):
        table = (
            group_importance[group_importance["task"] == task]
            .pivot(index="feature_group", columns="fold", values="normalized_share")
            .reindex(GROUP_ORDER)
        )
        image = axis.imshow(table.to_numpy(), aspect="auto", cmap="viridis")
        axis.set_title(f"{task}: normalized mean |SHAP|")
        axis.set_xlabel("fold")
        axis.set_yticks(range(len(table.index)), labels=table.index)
        axis.set_xticks(range(len(table.columns)), labels=table.columns)
        for row in range(table.shape[0]):
            for column in range(table.shape[1]):
                axis.text(
                    column,
                    row,
                    f"{table.iloc[row, column]:.2f}",
                    ha="center",
                    va="center",
                    color="white" if table.iloc[row, column] > 0.35 else "black",
                    fontsize=8,
                )
        fig.colorbar(image, ax=axis, fraction=0.046, pad=0.04)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def plot_ablation(ablation_folds, path):
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.8), constrained_layout=True)
    colors = plt.get_cmap("tab10").colors
    specifications = (("regression", "mae"), ("classification", "brier"))
    for axis, (task, metric) in zip(axes, specifications):
        task_frame = ablation_folds[ablation_folds["task"] == task]
        full = task_frame[task_frame["model_variant"] == "full"].set_index("fold")
        for group_index, group in enumerate(GROUP_ORDER):
            ablated = task_frame[
                (task_frame["model_variant"] == "drop_group")
                & (task_frame["dropped_group"] == group)
            ].set_index("fold")
            values = []
            for fold in full.index.intersection(ablated.index):
                value = score_degradation(
                    task,
                    metric,
                    full.loc[fold, metric],
                    ablated.loc[fold, metric],
                )
                values.append(value)
                axis.scatter(
                    group_index,
                    value,
                    color=colors[(int(fold) - 1) % len(colors)],
                    s=34,
                    alpha=0.85,
                )
            if values:
                axis.scatter(
                    group_index,
                    np.nanmean(values),
                    color="black",
                    marker="_",
                    s=220,
                    linewidths=2,
                )
        axis.axhline(0, color="black", linewidth=0.8)
        axis.set_title(f"{task}: {metric} degradation")
        axis.set_ylabel("positive = worse after removal")
        axis.set_xticks(
            range(len(GROUP_ORDER)),
            labels=GROUP_ORDER,
            rotation=25,
            ha="right",
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def main():
    np.random.seed(SEED)
    ordered = pd.read_csv(DATA_CSV).rename(columns=lambda column: column.strip())
    ordered["Date"] = pd.to_datetime(ordered["Date"])
    ordered = ordered.sort_values("Date").reset_index(drop=True)
    warmup = max(MONTH_WINDOW_DAYS, WINDOW + 2, max(RAIN_WINDOWS) + WINDOW - 1)
    eligible_dates = ordered["Date"].iloc[warmup:]

    feature_rows = []
    station_feature_rows = []
    group_rows = []
    ablation_rows = []
    protocol_rows = []

    for date_range in walk_forward_date_ranges(eligible_dates, n_splits=N_SPLITS):
        fold = date_range["fold"]
        fold_history = ordered[ordered["Date"] <= date_range["train_end"]]
        thresholds = compute_station_thresholds(
            fold_history,
            STATIONS,
            train_frac=1.0,
        )
        label_provenance = legacy_v0_label_provenance(
            fold_history,
            thresholds,
            train_frac=1.0,
        )
        X, y_reg, y_cls, meta = build_lagged_samples(ordered, thresholds=thresholds)
        groups = build_feature_groups(X.columns, validate_counts=True)
        train_mask = meta["Date"] <= date_range["train_end"]
        test_mask = meta["Date"].between(
            date_range["test_start"],
            date_range["test_end"],
        )
        X_train, X_test = X.loc[train_mask], X.loc[test_mask]
        background, background_dates = evenly_spaced_date_sample(
            X,
            meta,
            train_mask,
            BACKGROUND_DATE_COUNT,
        )
        explanation_sample, explanation_dates = evenly_spaced_date_sample(
            X,
            meta,
            test_mask,
            EXPLANATION_DATE_COUNT,
        )

        for task, target in (("regression", y_reg), ("classification", y_cls)):
            y_train, y_test = target.loc[train_mask], target.loc[test_mask]
            provenance = task_provenance(task)
            if task == "classification":
                provenance.update(label_provenance)
            status = (
                "evaluated"
                if task == "regression" or y_test.nunique() == 2
                else "single_class_test"
            )
            protocol_rows.append(
                {
                    "task": task,
                    **date_range,
                    **provenance,
                    "status": status,
                    "train_rows": int(len(y_train)),
                    "train_positive_rows": int(y_train.sum())
                    if task == "classification"
                    else np.nan,
                    "test_rows": int(len(y_test)),
                    "test_positive_rows": int(y_test.sum())
                    if task == "classification"
                    else np.nan,
                    "feature_count": int(X.shape[1]),
                    "background_date_count": int(len(background_dates)),
                    "background_rows": int(len(background)),
                    "background_start": background_dates.min(),
                    "background_end": background_dates.max(),
                    "background_sample_role": (
                        "deterministic_evenly_spaced_training_dates_all_stations"
                    ),
                    "explanation_date_count": int(len(explanation_dates)),
                    "explanation_rows": int(len(explanation_sample)),
                    "explanation_start": explanation_dates.min(),
                    "explanation_end": explanation_dates.max(),
                    "explanation_sample_role": (
                        "deterministic_evenly_spaced_holdout_dates_all_stations"
                    ),
                    "output_scale": (
                        "target_date_displacement_increment_mm_per_observation_interval"
                        if task == "regression"
                        else "positive_class_probability"
                    ),
                    "n_estimators": N_ESTIMATORS,
                    "learning_rate": 0.03,
                    "minibatch_frac": 0.8,
                    "col_sample": 0.8,
                    "random_seed": SEED,
                }
            )

            full_model, full_metrics = fit_and_score(
                task,
                X_train,
                X_test,
                y_train,
                y_test,
            )
            ablation_rows.append(
                {
                    "task": task,
                    "fold": fold,
                    **provenance,
                    "model_variant": "full",
                    "dropped_group": "none",
                    "feature_count": int(X_train.shape[1]),
                    "status": status,
                    **full_metrics,
                }
            )

            fold_shap = shap_matrix(
                full_model,
                background,
                explanation_sample,
                task,
            )
            feature_frame = fold_feature_importance(
                fold_shap,
                explanation_sample,
                task,
                fold,
                provenance={
                    **provenance,
                    "explanation_start": explanation_dates.min(),
                    "explanation_end": explanation_dates.max(),
                    "explanation_rows": int(len(explanation_sample)),
                    "explanation_sample_role": (
                        "deterministic_evenly_spaced_holdout_dates_all_stations"
                    ),
                },
            )
            feature_rows.append(feature_frame)
            group_rows.append(fold_group_importance(feature_frame))

            explanation_meta = meta.loc[explanation_sample.index]
            for station in sorted(explanation_meta["station"].unique()):
                station_mask = explanation_meta["station"].eq(station).to_numpy()
                station_sample = explanation_sample.loc[
                    explanation_meta.index[station_mask]
                ]
                station_feature_rows.append(
                    fold_feature_importance(
                        fold_shap[station_mask],
                        station_sample,
                        task,
                        fold,
                        station=station,
                        provenance={
                            **provenance,
                            "explanation_start": explanation_dates.min(),
                            "explanation_end": explanation_dates.max(),
                            "explanation_rows_per_station": int(station_mask.sum()),
                            "explanation_sample_role": (
                                "deterministic_evenly_spaced_holdout_dates_per_station"
                            ),
                        },
                    )
                )

            for group in GROUP_ORDER:
                retained = [
                    column for column in X.columns if column not in groups[group]
                ]
                _, metrics = fit_and_score(
                    task,
                    X_train[retained],
                    X_test[retained],
                    y_train,
                    y_test,
                )
                ablation_rows.append(
                    {
                        "task": task,
                        "fold": fold,
                        **provenance,
                        "model_variant": "drop_group",
                        "dropped_group": group,
                        "feature_count": int(len(retained)),
                        "status": status,
                        **metrics,
                    }
                )
        print(f"[shap-stability] fold {fold}/{N_SPLITS} completed", flush=True)

    feature_importance = pd.concat(feature_rows, ignore_index=True)
    station_feature_importance = pd.concat(station_feature_rows, ignore_index=True)
    group_importance = pd.concat(group_rows, ignore_index=True)
    feature_stability = summarize_feature_stability(feature_importance)
    station_feature_stability = summarize_feature_stability(
        station_feature_importance,
        strata=("station",),
    )
    rank_stability = pairwise_rank_stability(feature_importance, group_importance)
    ablation_folds = pd.DataFrame(ablation_rows)
    ablation_summary = summarize_ablation(ablation_folds)
    protocol = pd.DataFrame(protocol_rows)

    feature_stability = attach_task_provenance(feature_stability)
    station_feature_stability = attach_task_provenance(station_feature_stability)
    rank_stability = attach_task_provenance(rank_stability)
    group_importance = attach_task_provenance(group_importance)
    ablation_summary = attach_task_provenance(ablation_summary)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    attach_legacy_warning_metadata(protocol).to_csv(OUT_PROTOCOL, index=False)
    attach_legacy_warning_metadata(feature_importance).to_csv(
        OUT_FEATURE_IMPORTANCE,
        index=False,
    )
    attach_legacy_warning_metadata(feature_stability).to_csv(
        OUT_FEATURE_STABILITY,
        index=False,
    )
    attach_legacy_warning_metadata(rank_stability).to_csv(
        OUT_RANK_STABILITY,
        index=False,
    )
    attach_legacy_warning_metadata(station_feature_importance).to_csv(
        OUT_STATION_FEATURE_IMPORTANCE,
        index=False,
    )
    attach_legacy_warning_metadata(station_feature_stability).to_csv(
        OUT_STATION_FEATURE_STABILITY,
        index=False,
    )
    attach_legacy_warning_metadata(group_importance).to_csv(
        OUT_GROUP_IMPORTANCE,
        index=False,
    )
    attach_legacy_warning_metadata(ablation_folds).to_csv(
        OUT_ABLATION_FOLDS,
        index=False,
    )
    attach_legacy_warning_metadata(ablation_summary).to_csv(
        OUT_ABLATION_SUMMARY,
        index=False,
    )
    plot_group_stability(group_importance, OUT_GROUP_PLOT)
    plot_ablation(ablation_folds, OUT_ABLATION_PLOT)
    write_legacy_warning_manifest(
        OUT_LEGACY_MANIFEST,
        producer="explainability.shap_stability",
        artifacts=(
            OUT_PROTOCOL.relative_to(ROOT),
            OUT_FEATURE_IMPORTANCE.relative_to(ROOT),
            OUT_FEATURE_STABILITY.relative_to(ROOT),
            OUT_RANK_STABILITY.relative_to(ROOT),
            OUT_STATION_FEATURE_IMPORTANCE.relative_to(ROOT),
            OUT_STATION_FEATURE_STABILITY.relative_to(ROOT),
            OUT_GROUP_IMPORTANCE.relative_to(ROOT),
            OUT_ABLATION_FOLDS.relative_to(ROOT),
            OUT_ABLATION_SUMMARY.relative_to(ROOT),
            OUT_GROUP_PLOT.relative_to(ROOT),
            OUT_ABLATION_PLOT.relative_to(ROOT),
        ),
    )

    print("[shap-stability] primary ablation summary:")
    print(ablation_summary.to_string(index=False))
    print(f"[shap-stability] outputs: {OUT_DIR}")


if __name__ == "__main__":
    main()
