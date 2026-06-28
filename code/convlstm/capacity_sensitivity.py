"""Predeclared CNN-Mamba capacity and weight-decay sensitivity analysis."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

import numpy as np
import pandas as pd

CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from convlstm import inner_validation as inner  # noqa: E402
from convlstm import model as base  # noqa: E402
from convlstm import rolling_validation as rolling  # noqa: E402
from convlstm import seed_stability as stability  # noqa: E402


FIG_DIR = base.FIG_DIR
OUT_CANDIDATES = FIG_DIR / "capacity_candidates.csv"
OUT_SELECTION_SUMMARY = FIG_DIR / "capacity_selection_summary.csv"
OUT_SELECTION_HISTORY = FIG_DIR / "capacity_selection_history.csv"
OUT_RUNS = FIG_DIR / "capacity_selected_runs.csv"
OUT_REFIT = FIG_DIR / "capacity_selected_refit_history.csv"
OUT_METRICS = FIG_DIR / "capacity_selected_metrics.csv"
OUT_SUMMARY = FIG_DIR / "capacity_selected_summary.csv"
OUT_PREDICTIONS = FIG_DIR / "capacity_selected_predictions.csv"
OUT_COMPARISON = FIG_DIR / "capacity_selected_comparison.csv"
REFERENCE_RUNS = inner.OUT_RUNS
REFERENCE_METRICS = inner.OUT_METRICS

ANALYSIS_METHOD = "predeclared_2x2_capacity_weight_decay_sensitivity"
REFERENCE_CONFIG_ID = "h16_wd0"
REFERENCE_LOSS_ATOL = 5 * np.finfo(float).eps
COMPARISON_METRIC_ATOL = 1e-12


@dataclass(frozen=True)
class CandidateConfig:
    config_id: str
    hidden_channels: int
    weight_decay: float


CONFIGS = (
    CandidateConfig("h08_wd0", 8, 0.0),
    CandidateConfig("h08_wd1e4", 8, 1e-4),
    CandidateConfig("h16_wd0", 16, 0.0),
    CandidateConfig("h16_wd1e4", 16, 1e-4),
)
CONFIG_BY_ID = {config.config_id: config for config in CONFIGS}


def model_parameter_count(hidden_channels, *, input_channels=6, kernel=3):
    """Return the exact trainable parameter count for the current forecast model."""
    if hidden_channels <= 0 or input_channels <= 0 or kernel <= 0:
        raise ValueError("通道数和卷积核必须为正数")
    model = base.ForecastModel(
        in_ch=input_channels,
        hid_ch=hidden_channels,
        kernel=kernel,
        quantiles=base.QUANTILES,
    )
    return sum(parameter.numel() for parameter in model.parameters())


def candidate_row(config, *, seed, fold, selection):
    """Build one candidate-seed-fold selection record."""
    return {
        "config_id": config.config_id,
        "hidden_channels": config.hidden_channels,
        "weight_decay": config.weight_decay,
        "parameter_count": model_parameter_count(config.hidden_channels),
        "seed": seed,
        "fold": fold,
        "selected_epoch": selection["selected_epoch"],
        "selected_validation_loss": selection["selected_validation_loss"],
        "observed_epochs": selection["observed_epochs"],
        "stop_reason": selection["stop_reason"],
        "best_seed_selected": False,
        "outer_test_used_for_ranking": False,
    }


def selection_history_rows(config, *, seed, fold, selection):
    """Attach candidate and run identifiers to an inner selection history."""
    return [
        {
            "config_id": config.config_id,
            "hidden_channels": config.hidden_channels,
            "weight_decay": config.weight_decay,
            "seed": seed,
            "fold": fold,
            **row,
        }
        for row in selection["history"]
    ]


def aggregate_candidates(candidates, *, seeds=stability.SEEDS):
    """Aggregate and rank candidates using inner validation only."""
    rows = []
    expected_configs = set(CONFIG_BY_ID)
    for (fold, config_id), group in candidates.groupby(
        ["fold", "config_id"],
        sort=True,
    ):
        if set(group["seed"]) != set(seeds):
            raise RuntimeError("每个候选折必须包含全部预设种子")
        first = group.iloc[0]
        losses = group["selected_validation_loss"].astype(float)
        epochs = group["selected_epoch"].astype(float)
        rows.append({
            "fold": int(fold),
            "config_id": config_id,
            "hidden_channels": int(first["hidden_channels"]),
            "weight_decay": float(first["weight_decay"]),
            "parameter_count": int(first["parameter_count"]),
            "seed_count": len(seeds),
            "seeds": ",".join(str(seed) for seed in seeds),
            "validation_loss_mean": float(losses.mean()),
            "validation_loss_std": float(losses.std(ddof=1)),
            "validation_loss_min": float(losses.min()),
            "validation_loss_max": float(losses.max()),
            "selected_epoch_mean": float(epochs.mean()),
            "selected_epoch_std": float(epochs.std(ddof=1)),
            "selected_epoch_min": int(epochs.min()),
            "selected_epoch_max": int(epochs.max()),
            "outer_test_used_for_ranking": False,
            "best_seed_selected": False,
        })
    frame = pd.DataFrame(rows)
    if len(frame) != rolling.N_SPLITS * len(CONFIGS):
        raise RuntimeError("候选汇总行数不完整")
    for fold, group in frame.groupby("fold"):
        if set(group["config_id"]) != expected_configs:
            raise RuntimeError(f"第 {fold} 折候选集合与预注册不一致")

    ranked = []
    for _, group in frame.groupby("fold", sort=True):
        ordered = group.assign(
            regularization_tie_rank=(group["weight_decay"] == 0).astype(int),
        ).sort_values(
            [
                "validation_loss_mean",
                "parameter_count",
                "regularization_tie_rank",
                "config_id",
            ],
            kind="mergesort",
        )
        ordered["selection_rank"] = range(1, len(ordered) + 1)
        ordered["selected_config"] = ordered["selection_rank"] == 1
        ranked.append(ordered.drop(columns="regularization_tie_rank"))
    return pd.concat(ranked, ignore_index=True)


def selected_configs(selection_summary):
    """Return exactly one inner-selected configuration for each outer fold."""
    selected = selection_summary[selection_summary["selected_config"]]
    if len(selected) != rolling.N_SPLITS or selected["fold"].duplicated().any():
        raise RuntimeError("每个外层折必须且只能选择一个配置")
    return {
        int(row.fold): CONFIG_BY_ID[row.config_id]
        for row in selected.itertuples(index=False)
    }


def validate_reference_candidate(candidates, reference_runs):
    """Ensure the current 16-channel candidate reproduces the saved reference."""
    keys = ["seed", "fold"]
    current = candidates[
        candidates["config_id"] == REFERENCE_CONFIG_ID
    ].copy()
    reference_fields = [
        "selected_epoch",
        "selected_validation_loss",
        "observed_epochs",
        "stop_reason",
    ]
    reference = reference_runs[keys + reference_fields].copy()
    merged = current.merge(
        reference,
        on=keys,
        how="outer",
        validate="one_to_one",
        suffixes=("_candidate", "_reference"),
        indicator=True,
    )
    if len(merged) != len(stability.SEEDS) * rolling.N_SPLITS:
        raise RuntimeError("当前配置与早停参照运行数量不一致")
    if not (merged["_merge"] == "both").all():
        raise RuntimeError("当前配置与早停参照无法一一配对")
    for field in ("selected_epoch", "observed_epochs", "stop_reason"):
        if not (
            merged[f"{field}_candidate"]
            == merged[f"{field}_reference"]
        ).all():
            raise RuntimeError(f"当前配置未复现早停参照字段: {field}")
    if not np.allclose(
        merged["selected_validation_loss_candidate"],
        merged["selected_validation_loss_reference"],
        rtol=0,
        atol=REFERENCE_LOSS_ATOL,
    ):
        raise RuntimeError("当前配置未在机器精度容差内复现早停参照验证 loss")


def selected_run_row(
    metadata,
    config,
    selection,
    result,
    station_names,
):
    """Build one final refit record after inner-only configuration selection."""
    row = inner.run_row(metadata, selection, result, station_names)
    row.update({
        "analysis_method": ANALYSIS_METHOD,
        "config_id": config.config_id,
        "hidden_channels": config.hidden_channels,
        "weight_decay": config.weight_decay,
        "parameter_count": model_parameter_count(config.hidden_channels),
        "epochs": selection["selected_epoch"],
        "hyperparameter_tuning": True,
        "limited_predeclared_sensitivity": True,
        "configuration_selected_by_inner_validation_only": True,
        "outer_test_used_for_ranking": False,
        "reference_config_id": REFERENCE_CONFIG_ID,
    })
    return row


def build_reference_comparison(metrics, reference_metrics, runs):
    """Pair selected-capacity metrics with the current early-stop reference."""
    comparison = inner.build_fixed_comparison(
        metrics,
        reference_metrics,
        runs,
    )
    renames = {}
    for column in comparison.columns:
        if column.startswith("fixed_"):
            renames[column] = "reference_" + column.removeprefix("fixed_")
        elif column.startswith("early_"):
            renames[column] = "selected_" + column.removeprefix("early_")
    comparison = comparison.rename(columns=renames)
    comparison["rmse_improved"] = (
        comparison["delta_model_rmse"] < -COMPARISON_METRIC_ATOL
    )
    comparison["mae_improved"] = (
        comparison["delta_model_mae"] < -COMPARISON_METRIC_ATOL
    )
    comparison["rmse_numerically_equal"] = (
        comparison["delta_model_rmse"].abs() <= COMPARISON_METRIC_ATOL
    )
    comparison["mae_numerically_equal"] = (
        comparison["delta_model_mae"].abs() <= COMPARISON_METRIC_ATOL
    )
    comparison["comparison_metric_atol"] = COMPARISON_METRIC_ATOL
    comparison["analysis_method"] = ANALYSIS_METHOD
    comparison["reference_config_id"] = REFERENCE_CONFIG_ID
    comparison["outer_test_used_for_ranking"] = False
    return comparison


def validate_output_frames(
    candidates,
    selection_summary,
    selection_history,
    runs,
    refit,
    metrics,
    summary,
    predictions,
    comparison,
    station_names,
):
    """Reject incomplete or internally inconsistent sensitivity outputs."""
    expected_runs = len(stability.SEEDS) * rolling.N_SPLITS
    expected_candidates = expected_runs * len(CONFIGS)
    expected_metrics = expected_runs * 2 * (len(station_names) + 1)
    expected_summary = rolling.N_SPLITS * 2 * (len(station_names) + 1)
    expected_predictions = (
        expected_runs * rolling.TEST_WINDOWS * len(station_names)
    )
    expected_lengths = (
        (candidates, expected_candidates, "候选运行"),
        (selection_summary, rolling.N_SPLITS * len(CONFIGS), "候选汇总"),
        (runs, expected_runs, "最终运行"),
        (metrics, expected_metrics, "最终指标"),
        (summary, expected_summary, "最终汇总"),
        (predictions, expected_predictions, "最终逐日预测"),
        (comparison, expected_metrics, "参照配对"),
    )
    for frame, expected, name in expected_lengths:
        if len(frame) != expected:
            raise RuntimeError(f"{name}行数不完整: {len(frame)} != {expected}")
    for frame, keys in (
        (candidates, ["config_id", "seed", "fold"]),
        (selection_summary, ["config_id", "fold"]),
        (selection_history, ["config_id", "seed", "fold", "epoch"]),
        (runs, ["seed", "fold"]),
        (refit, ["seed", "fold", "epoch"]),
        (metrics, ["seed", "fold", "scope", "interval_variant"]),
        (summary, ["fold", "scope", "interval_variant"]),
        (predictions, ["seed", "fold", "date", "station"]),
        (comparison, ["seed", "fold", "scope", "interval_variant"]),
    ):
        if frame.duplicated(keys).any():
            raise RuntimeError(f"容量敏感性输出包含重复主键: {keys}")

    for row in candidates.itertuples(index=False):
        history = selection_history[
            (selection_history["config_id"] == row.config_id)
            & (selection_history["seed"] == row.seed)
            & (selection_history["fold"] == row.fold)
        ]
        if list(history["epoch"]) != list(range(1, row.observed_epochs + 1)):
            raise RuntimeError("候选内层选择曲线 epoch 不连续")
        epoch, loss = inner.select_best_epoch(history.to_dict("records"))
        if epoch != row.selected_epoch or not np.isclose(
            loss,
            row.selected_validation_loss,
        ):
            raise RuntimeError("候选记录与最优验证 epoch 不一致")
    for row in runs.itertuples(index=False):
        history = refit[(refit["seed"] == row.seed) & (refit["fold"] == row.fold)]
        if list(history["epoch"]) != list(range(1, row.selected_epoch + 1)):
            raise RuntimeError("最终重训曲线与所选 epoch 不一致")

    selected = selected_configs(selection_summary)
    for row in runs.itertuples(index=False):
        if row.config_id != selected[row.fold].config_id:
            raise RuntimeError("最终运行配置与内层选择结果不一致")
    if candidates["best_seed_selected"].any():
        raise RuntimeError("容量敏感性不得选择最佳种子")
    if runs["best_seed_selected"].any() or summary["best_seed_selected"].any():
        raise RuntimeError("最终汇总不得选择最佳种子")
    for frame in (
        candidates,
        selection_summary,
        selection_history,
        runs,
        refit,
        metrics,
        summary,
        predictions,
        comparison,
    ):
        numeric = frame.select_dtypes(include=[np.number])
        if not np.isfinite(numeric.to_numpy()).all():
            raise RuntimeError("容量敏感性输出包含非有限数值")


def main():
    df = pd.read_csv(base.FEAT_CSV)
    dates = pd.DatetimeIndex(pd.to_datetime(df["Date"]))
    if dates.has_duplicates or not dates.is_monotonic_increasing:
        raise RuntimeError("特征日期必须严格递增且不得重复")
    if not REFERENCE_RUNS.is_file() or not REFERENCE_METRICS.is_file():
        raise RuntimeError("缺少内层早停参照输出，无法执行预设配对")

    disp = df[base.DISP_COLS].values.astype(np.float64)
    station_names, xy = base.load_coords(base.DISP_COLS)
    interp, (grid_x, grid_y) = base.make_interpolator(
        xy,
        base.GRID_H,
        base.GRID_W,
    )
    readout_weights = base.station_readout_weights(grid_x, grid_y, xy)
    splits = rolling.expanding_window_splits(len(df))

    candidate_rows = []
    history_rows = []
    selection_results = {}
    for config in CONFIGS:
        for seed in stability.SEEDS:
            for split in splits:
                nested = inner.make_inner_split(split.fit_windows)
                selection = inner.run_epoch_selection(
                    df,
                    disp,
                    interp,
                    readout_weights,
                    split,
                    nested,
                    seed=seed,
                    hidden_channels=config.hidden_channels,
                    weight_decay=config.weight_decay,
                )
                key = (config.config_id, seed, split.fold)
                selection_results[key] = selection
                candidate_rows.append(candidate_row(
                    config,
                    seed=seed,
                    fold=split.fold,
                    selection=selection,
                ))
                history_rows.extend(selection_history_rows(
                    config,
                    seed=seed,
                    fold=split.fold,
                    selection=selection,
                ))

    candidate_frame = pd.DataFrame(candidate_rows)
    history_frame = pd.DataFrame(history_rows)
    reference_runs = pd.read_csv(REFERENCE_RUNS)
    validate_reference_candidate(candidate_frame, reference_runs)
    selection_summary = aggregate_candidates(candidate_frame)
    chosen = selected_configs(selection_summary)
    for fold in sorted(chosen):
        selected_row = selection_summary[
            (selection_summary["fold"] == fold)
            & selection_summary["selected_config"]
        ].iloc[0]
        print(
            f"[cnn-mamba-capacity] fold={fold} selected={chosen[fold].config_id} "
            f"validation={selected_row['validation_loss_mean']:.6f}",
            flush=True,
        )

    run_rows = []
    refit_rows = []
    metric_rows = []
    prediction_rows = []
    for seed in stability.SEEDS:
        for split in splits:
            config = chosen[split.fold]
            selection = selection_results[(config.config_id, seed, split.fold)]
            nested = inner.make_inner_split(split.fit_windows)
            metadata = rolling.split_metadata(split, dates, seed=seed)
            metadata.update(inner.inner_date_metadata(split, nested, dates))
            result = rolling.train_predict_fold(
                df,
                disp,
                interp,
                readout_weights,
                split,
                seed=seed,
                epochs=selection["selected_epoch"],
                hidden_channels=config.hidden_channels,
                weight_decay=config.weight_decay,
            )
            run_rows.append(selected_run_row(
                metadata,
                config,
                selection,
                result,
                station_names,
            ))
            refit_rows.extend(inner.add_training_keys(
                result["training_history"],
                seed=seed,
                fold=split.fold,
                phase="selected_configuration_full_fit_refit",
            ))
            fold_metrics = rolling.metric_rows(result, station_names, metadata)
            metric_rows.extend({"seed": seed, **row} for row in fold_metrics)
            test_dates = dates[
                split.split_index + base.HORIZON - 1:split.test_stop_index
            ]
            prediction_rows.extend(
                {"seed": seed, **row}
                for row in rolling.prediction_rows(
                    result,
                    station_names,
                    test_dates,
                    split.fold,
                )
            )

    run_frame = pd.DataFrame(run_rows)
    refit_frame = pd.DataFrame(refit_rows)
    metric_frame = pd.DataFrame(metric_rows)
    summary_frame = stability.aggregate_seed_metrics(metric_frame)
    prediction_frame = pd.DataFrame(prediction_rows)
    reference_metrics = pd.read_csv(REFERENCE_METRICS)
    comparison_frame = build_reference_comparison(
        metric_frame,
        reference_metrics,
        run_frame,
    )
    validate_output_frames(
        candidate_frame,
        selection_summary,
        history_frame,
        run_frame,
        refit_frame,
        metric_frame,
        summary_frame,
        prediction_frame,
        comparison_frame,
        station_names,
    )

    FIG_DIR.mkdir(parents=True, exist_ok=True)
    candidate_frame.to_csv(OUT_CANDIDATES, index=False)
    selection_summary.to_csv(OUT_SELECTION_SUMMARY, index=False)
    history_frame.to_csv(OUT_SELECTION_HISTORY, index=False)
    run_frame.to_csv(OUT_RUNS, index=False)
    refit_frame.to_csv(OUT_REFIT, index=False)
    metric_frame.to_csv(OUT_METRICS, index=False)
    summary_frame.to_csv(OUT_SUMMARY, index=False)
    prediction_frame.to_csv(OUT_PREDICTIONS, index=False)
    comparison_frame.to_csv(OUT_COMPARISON, index=False)
    print(f"[cnn-mamba-capacity] 候选运行: {OUT_CANDIDATES}")
    print(f"[cnn-mamba-capacity] 内层选择汇总: {OUT_SELECTION_SUMMARY}")
    print(f"[cnn-mamba-capacity] 候选训练轨迹: {OUT_SELECTION_HISTORY}")
    print(f"[cnn-mamba-capacity] 最终运行: {OUT_RUNS}")
    print(f"[cnn-mamba-capacity] 最终重训轨迹: {OUT_REFIT}")
    print(f"[cnn-mamba-capacity] 外层指标: {OUT_METRICS}")
    print(f"[cnn-mamba-capacity] 跨种子汇总: {OUT_SUMMARY}")
    print(f"[cnn-mamba-capacity] 逐日预测: {OUT_PREDICTIONS}")
    print(f"[cnn-mamba-capacity] 早停参照配对: {OUT_COMPARISON}")


if __name__ == "__main__":
    main()
