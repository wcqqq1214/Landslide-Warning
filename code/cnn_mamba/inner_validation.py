"""Nested temporal validation for CNN-Mamba epoch selection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import torch

CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from cnn_mamba import model as base  # noqa: E402
from cnn_mamba import rolling_validation as rolling  # noqa: E402
from cnn_mamba import seed_stability as stability  # noqa: E402


FIG_DIR = base.FIG_DIR
OUT_RUNS = FIG_DIR / "inner_validation_runs.csv"
OUT_SELECTION = FIG_DIR / "inner_validation_selection_history.csv"
OUT_REFIT = FIG_DIR / "inner_validation_refit_history.csv"
OUT_METRICS = FIG_DIR / "inner_validation_metrics.csv"
OUT_SUMMARY = FIG_DIR / "inner_validation_summary.csv"
OUT_PREDICTIONS = FIG_DIR / "inner_validation_predictions.csv"
OUT_COMPARISON = FIG_DIR / "inner_validation_comparison.csv"
FIXED_METRICS = FIG_DIR / "seed_stability_metrics.csv"

INNER_VALIDATION_FRACTION = 0.2
MAX_EPOCHS = 300
MIN_EPOCHS = 30
PATIENCE = 30
MIN_RELATIVE_IMPROVEMENT = 0.001
SEEDS = stability.SEEDS
ANALYSIS_METHOD = "nested_temporal_epoch_selection_five_seed_diagnostic"
SELECTION_METRIC = "normalized_mean_pinball_loss"
COMPARISON_METRICS = (
    "model_rmse",
    "model_mae",
    "rmse_skill_vs_baseline",
    "mae_skill_vs_baseline",
    "model_mean_error",
    "mean_predicted_increment",
    "predicted_increment_std",
    "increment_std_ratio",
    "increment_correlation",
    "coverage",
    "mean_width",
    "mean_pinball",
    "interval_score_80",
)


@dataclass(frozen=True)
class InnerSplit:
    train_windows: int
    validation_windows: int


def make_inner_split(
    fit_windows,
    *,
    validation_fraction=INNER_VALIDATION_FRACTION,
):
    """Split an outer fit period into chronological train and validation parts."""
    train_windows, validation_windows = base.chronological_fit_calibration_split(
        fit_windows,
        validation_fraction,
    )
    if validation_windows == 0:
        raise ValueError("内层时间验证至少需要一个窗口")
    return InnerSplit(
        train_windows=train_windows,
        validation_windows=validation_windows,
    )


def is_monitor_improvement(
    candidate,
    monitored_best,
    *,
    min_relative_improvement=MIN_RELATIVE_IMPROVEMENT,
):
    """Return whether a loss clears the predeclared relative improvement."""
    if not np.isfinite(candidate):
        raise ValueError("验证 loss 必须为有限数值")
    if monitored_best is None:
        return True
    if monitored_best < 0 or not np.isfinite(monitored_best):
        raise ValueError("监控最优 loss 必须为非负有限数值")
    if not 0 <= min_relative_improvement < 1:
        raise ValueError("最小相对改进必须在 [0, 1) 内")
    return candidate < monitored_best * (1.0 - min_relative_improvement)


def select_best_epoch(history):
    """Select the absolute minimum validation loss, breaking ties earlier."""
    if not history:
        raise ValueError("训练历史不得为空")
    best = min(
        history,
        key=lambda row: (row["validation_pinball_loss"], row["epoch"]),
    )
    return int(best["epoch"]), float(best["validation_pinball_loss"])


def inner_date_metadata(split, inner_split, dates):
    """Describe chronological inner train and validation boundaries."""
    dates = pd.DatetimeIndex(pd.to_datetime(dates))
    first_target = base.LOOKBACK + base.HORIZON - 1
    train_dates = dates[
        first_target:first_target + inner_split.train_windows
    ]
    validation_dates = dates[
        first_target + inner_split.train_windows:
        first_target + split.fit_windows
    ]
    if (
        len(train_dates) != inner_split.train_windows
        or len(validation_dates) != inner_split.validation_windows
    ):
        raise RuntimeError("内层日期边界与窗口计划不一致")
    if train_dates[-1] >= validation_dates[0]:
        raise RuntimeError("内层训练期必须早于内层验证期")
    return {
        "inner_train_start_date": train_dates[0].date().isoformat(),
        "inner_train_end_date": train_dates[-1].date().isoformat(),
        "inner_train_windows": inner_split.train_windows,
        "inner_validation_start_date": validation_dates[0].date().isoformat(),
        "inner_validation_end_date": validation_dates[-1].date().isoformat(),
        "inner_validation_windows": inner_split.validation_windows,
    }


def run_epoch_selection(
    df,
    disp,
    interp,
    readout_weights,
    split,
    inner_split,
    *,
    seed,
    hidden_channels=base.HIDDEN,
    weight_decay=0.0,
):
    """Choose an epoch using only the chronological inner validation segment."""
    torch.manual_seed(seed)
    np.random.seed(seed)
    if not isinstance(hidden_channels, (int, np.integer)):
        raise TypeError("隐藏通道数必须为整数")
    if hidden_channels <= 0:
        raise ValueError("隐藏通道数必须为正数")
    if not np.isfinite(weight_decay) or weight_decay < 0:
        raise ValueError("权重衰减必须为非负有限数值")

    stats_stop = (
        inner_split.train_windows + base.LOOKBACK + base.HORIZON - 1
    )
    fit_stop = split.fit_windows + base.LOOKBACK + base.HORIZON - 1
    inputs, _ = base.make_model_inputs(df, disp, stats_stop, interp)
    x_fit = base.make_windows(
        inputs[:fit_stop],
        base.LOOKBACK,
        base.HORIZON,
    )[0]
    _, _, y_fit_delta = base.make_station_windows(
        disp,
        split=base.LOOKBACK,
        lookback=base.LOOKBACK,
        horizon=base.HORIZON,
        stop=fit_stop,
    )
    if len(x_fit) != split.fit_windows or len(y_fit_delta) != len(x_fit):
        raise RuntimeError("内层输入、目标和外层拟合窗口数量不一致")

    delta_scale = base.make_delta_scale(
        y_fit_delta[:inner_split.train_windows]
    )
    y_fit_normalized = (y_fit_delta / delta_scale).astype(np.float32)
    device = base.require_cuda_device()
    x_tensor = torch.from_numpy(x_fit).to(device)
    y_tensor = torch.from_numpy(y_fit_normalized).to(device)
    readout_tensor = torch.from_numpy(readout_weights).to(device)
    validation_slice = slice(inner_split.train_windows, split.fit_windows)

    model = base.ForecastModel(
        in_ch=x_tensor.shape[2],
        hid_ch=hidden_channels,
        kernel=base.KERNEL,
        quantiles=base.QUANTILES,
    ).to(device)
    optimizer = torch.optim.Adam(
        model.parameters(),
        lr=base.LR,
        weight_decay=weight_decay,
    )
    history = []
    monitored_best = None
    stale_epochs = 0
    stop_reason = "max_epochs"

    for epoch in range(1, MAX_EPOCHS + 1):
        model.train()
        optimizer.zero_grad()
        train_grid = model(x_tensor[:inner_split.train_windows])
        train_station = base.readout_grid_at_stations(
            train_grid,
            readout_tensor,
        )
        train_loss = base.pinball_loss(
            train_station,
            y_tensor[:inner_split.train_windows],
            base.QUANTILES,
        )
        train_loss.backward()
        gradient_l2_norm = float(torch.sqrt(sum(
            parameter.grad.detach().square().sum()
            for parameter in model.parameters()
            if parameter.grad is not None
        )))
        optimizer.step()

        model.eval()
        with torch.no_grad():
            validation_grid = model(x_tensor[validation_slice])
            validation_station = base.readout_grid_at_stations(
                validation_grid,
                readout_tensor,
            )
            validation_loss = float(base.pinball_loss(
                validation_station,
                y_tensor[validation_slice],
                base.QUANTILES,
            ))

        monitor_improved = is_monitor_improvement(
            validation_loss,
            monitored_best,
        )
        if monitor_improved:
            monitored_best = validation_loss
            stale_epochs = 0
        else:
            stale_epochs += 1
        history.append({
            "epoch": epoch,
            "train_pinball_loss": float(train_loss.detach()),
            "validation_pinball_loss": validation_loss,
            "gradient_l2_norm": gradient_l2_norm,
            "monitor_improved": monitor_improved,
            "stale_epochs": stale_epochs,
        })
        if epoch >= MIN_EPOCHS and stale_epochs >= PATIENCE:
            stop_reason = "patience_exhausted"
            break

    selected_epoch, selected_validation_loss = select_best_epoch(history)
    return {
        "history": history,
        "selected_epoch": selected_epoch,
        "selected_validation_loss": selected_validation_loss,
        "observed_epochs": len(history),
        "stop_reason": stop_reason,
        "inner_delta_scale": delta_scale,
    }


def run_row(metadata, selection, result, station_names):
    """Build one auditable seed-fold record for nested epoch selection."""
    row = {
        **metadata,
        "analysis_method": ANALYSIS_METHOD,
        "selection_metric": SELECTION_METRIC,
        "inner_validation_fraction": INNER_VALIDATION_FRACTION,
        "max_epochs": MAX_EPOCHS,
        "minimum_observed_epochs": MIN_EPOCHS,
        "patience": PATIENCE,
        "minimum_relative_improvement": MIN_RELATIVE_IMPROVEMENT,
        "selected_epoch": selection["selected_epoch"],
        "selected_validation_loss": selection["selected_validation_loss"],
        "observed_epochs": selection["observed_epochs"],
        "stop_reason": selection["stop_reason"],
        "fixed_reference_epochs": base.EPOCHS,
        "hyperparameter_tuning": False,
        "best_seed_selected": False,
        "seed_set_predeclared": True,
    }
    row.update({
        f"inner_delta_scale_{station}_mm": float(value)
        for station, value in zip(
            station_names,
            selection["inner_delta_scale"],
        )
    })
    row.update({
        f"refit_delta_scale_{station}_mm": float(value)
        for station, value in zip(station_names, result["delta_scale"])
    })
    row.update({
        f"qhat_{station}_mm": float(value)
        for station, value in zip(station_names, result["qhat"])
    })
    return row


def add_training_keys(rows, *, seed, fold, phase):
    """Attach run identifiers to optimization histories."""
    return [
        {"seed": seed, "fold": fold, "phase": phase, **row}
        for row in rows
    ]


def build_fixed_comparison(early_metrics, fixed_metrics, runs):
    """Pair early-stopped and fixed-120 metrics without selecting a winner."""
    keys = ["seed", "fold", "scope", "interval_variant"]
    if early_metrics.duplicated(keys).any() or fixed_metrics.duplicated(keys).any():
        raise RuntimeError("早停或固定轮数指标包含重复主键")
    required = set(keys) | set(COMPARISON_METRICS) | {
        "test_start_date",
        "test_end_date",
        "baseline_rmse",
        "baseline_mae",
    }
    for name, frame in (("early", early_metrics), ("fixed", fixed_metrics)):
        missing = required - set(frame.columns)
        if missing:
            raise RuntimeError(f"{name} 指标缺少比较列: {sorted(missing)}")

    early = early_metrics[list(required)].copy()
    fixed = fixed_metrics[list(required)].copy()
    merged = early.merge(
        fixed,
        on=keys,
        how="outer",
        validate="one_to_one",
        suffixes=("_early", "_fixed"),
        indicator=True,
    )
    if len(merged) != len(early_metrics) or not (merged["_merge"] == "both").all():
        raise RuntimeError("早停与固定轮数指标无法一一配对")
    for field in ("test_start_date", "test_end_date"):
        if not (merged[f"{field}_early"] == merged[f"{field}_fixed"]).all():
            raise RuntimeError("早停与固定轮数测试日期不一致")
    for metric in ("baseline_rmse", "baseline_mae"):
        if not np.allclose(
            merged[f"{metric}_early"],
            merged[f"{metric}_fixed"],
        ):
            raise RuntimeError("早停与固定轮数的持久性基线不一致")

    comparison = merged[keys].copy()
    comparison["test_start_date"] = merged["test_start_date_early"]
    comparison["test_end_date"] = merged["test_end_date_early"]
    comparison = comparison.merge(
        runs[["seed", "fold", "selected_epoch", "stop_reason"]],
        on=["seed", "fold"],
        how="left",
        validate="many_to_one",
    )
    for metric in COMPARISON_METRICS:
        comparison[f"fixed_{metric}"] = merged[f"{metric}_fixed"]
        comparison[f"early_{metric}"] = merged[f"{metric}_early"]
        comparison[f"delta_{metric}"] = (
            merged[f"{metric}_early"] - merged[f"{metric}_fixed"]
        )
    comparison["rmse_improved"] = (
        comparison["early_model_rmse"] < comparison["fixed_model_rmse"]
    )
    comparison["mae_improved"] = (
        comparison["early_model_mae"] < comparison["fixed_model_mae"]
    )
    comparison["best_seed_selected"] = False
    return comparison


def validate_output_frames(
    runs,
    selection,
    refit,
    metrics,
    summary,
    predictions,
    comparison,
    station_names,
):
    """Reject incomplete, duplicated or internally inconsistent outputs."""
    expected_runs = len(SEEDS) * rolling.N_SPLITS
    expected_metrics = expected_runs * 2 * (len(station_names) + 1)
    expected_summary = rolling.N_SPLITS * 2 * (len(station_names) + 1)
    expected_predictions = (
        expected_runs * rolling.TEST_WINDOWS * len(station_names)
    )
    if len(runs) != expected_runs:
        raise RuntimeError("内层验证运行记录不完整")
    if len(metrics) != expected_metrics or len(comparison) != expected_metrics:
        raise RuntimeError("内层验证指标或固定轮数配对不完整")
    if len(summary) != expected_summary:
        raise RuntimeError("内层验证跨种子汇总不完整")
    if len(predictions) != expected_predictions:
        raise RuntimeError("内层验证逐日预测不完整")
    for frame, keys in (
        (runs, ["seed", "fold"]),
        (selection, ["seed", "fold", "epoch"]),
        (refit, ["seed", "fold", "epoch"]),
        (metrics, ["seed", "fold", "scope", "interval_variant"]),
        (summary, ["fold", "scope", "interval_variant"]),
        (predictions, ["seed", "fold", "date", "station"]),
        (comparison, ["seed", "fold", "scope", "interval_variant"]),
    ):
        if frame.duplicated(keys).any():
            raise RuntimeError(f"内层验证输出包含重复主键: {keys}")

    for (seed, fold), run in runs.set_index(["seed", "fold"]).iterrows():
        selection_group = selection[
            (selection["seed"] == seed) & (selection["fold"] == fold)
        ]
        refit_group = refit[(refit["seed"] == seed) & (refit["fold"] == fold)]
        if list(selection_group["epoch"]) != list(
            range(1, int(run["observed_epochs"]) + 1)
        ):
            raise RuntimeError("内层选择曲线 epoch 不连续")
        if list(refit_group["epoch"]) != list(
            range(1, int(run["selected_epoch"]) + 1)
        ):
            raise RuntimeError("最终重训曲线与所选 epoch 不一致")
        selected_epoch, selected_loss = select_best_epoch(
            selection_group.to_dict("records")
        )
        if selected_epoch != run["selected_epoch"] or not np.isclose(
            selected_loss,
            run["selected_validation_loss"],
        ):
            raise RuntimeError("运行记录与验证 loss 最优 epoch 不一致")

    if runs["best_seed_selected"].any() or summary["best_seed_selected"].any():
        raise RuntimeError("内层验证不得选择最佳种子")
    if comparison["best_seed_selected"].any():
        raise RuntimeError("固定轮数比较不得选择最佳种子")
    if set(runs["seed"]) != set(SEEDS):
        raise RuntimeError("内层验证种子集合与预设不一致")
    for frame in (
        runs,
        selection,
        refit,
        metrics,
        summary,
        predictions,
        comparison,
    ):
        numeric = frame.select_dtypes(include=[np.number])
        if not np.isfinite(numeric.to_numpy()).all():
            raise RuntimeError("内层验证输出包含非有限数值")


def main():
    df = pd.read_csv(base.FEAT_CSV)
    dates = pd.DatetimeIndex(pd.to_datetime(df["Date"]))
    if dates.has_duplicates or not dates.is_monotonic_increasing:
        raise RuntimeError("特征日期必须严格递增且不得重复")
    if not FIXED_METRICS.is_file():
        raise RuntimeError("缺少固定 120 轮多种子指标，无法执行预设配对比较")

    disp = df[base.DISP_COLS].values.astype(np.float64)
    station_names, xy = base.load_coords(base.DISP_COLS)
    interp, (grid_x, grid_y) = base.make_interpolator(
        xy,
        base.GRID_H,
        base.GRID_W,
    )
    readout_weights = base.station_readout_weights(grid_x, grid_y, xy)
    splits = rolling.expanding_window_splits(len(df))

    run_rows = []
    selection_rows = []
    refit_rows = []
    metric_rows = []
    prediction_rows = []
    for seed in SEEDS:
        for split in splits:
            inner_split = make_inner_split(split.fit_windows)
            metadata = rolling.split_metadata(split, dates, seed=seed)
            metadata.update(inner_date_metadata(split, inner_split, dates))
            selection_result = run_epoch_selection(
                df,
                disp,
                interp,
                readout_weights,
                split,
                inner_split,
                seed=seed,
            )
            result = rolling.train_predict_fold(
                df,
                disp,
                interp,
                readout_weights,
                split,
                seed=seed,
                epochs=selection_result["selected_epoch"],
            )
            run_rows.append(
                run_row(metadata, selection_result, result, station_names)
            )
            selection_rows.extend(add_training_keys(
                selection_result["history"],
                seed=seed,
                fold=split.fold,
                phase="inner_selection",
            ))
            refit_rows.extend(add_training_keys(
                result["training_history"],
                seed=seed,
                fold=split.fold,
                phase="full_fit_refit",
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
            overall = next(
                row
                for row in fold_metrics
                if row["scope"] == "overall"
                and row["interval_variant"] == "calibrated"
            )
            print(
                f"[cnn-mamba-inner] seed={seed} fold={split.fold}: "
                f"selected={selection_result['selected_epoch']}/"
                f"{selection_result['observed_epochs']} "
                f"stop={selection_result['stop_reason']} "
                f"RMSE={overall['model_rmse']:.3f}/"
                f"{overall['baseline_rmse']:.3f} mm",
                flush=True,
            )

    run_frame = pd.DataFrame(run_rows)
    selection_frame = pd.DataFrame(selection_rows)
    refit_frame = pd.DataFrame(refit_rows)
    metric_frame = pd.DataFrame(metric_rows)
    summary_frame = stability.aggregate_seed_metrics(metric_frame)
    prediction_frame = pd.DataFrame(prediction_rows)
    fixed_frame = pd.read_csv(FIXED_METRICS)
    comparison_frame = build_fixed_comparison(
        metric_frame,
        fixed_frame,
        run_frame,
    )
    validate_output_frames(
        run_frame,
        selection_frame,
        refit_frame,
        metric_frame,
        summary_frame,
        prediction_frame,
        comparison_frame,
        station_names,
    )

    FIG_DIR.mkdir(parents=True, exist_ok=True)
    run_frame.to_csv(OUT_RUNS, index=False)
    selection_frame.to_csv(OUT_SELECTION, index=False)
    refit_frame.to_csv(OUT_REFIT, index=False)
    metric_frame.to_csv(OUT_METRICS, index=False)
    summary_frame.to_csv(OUT_SUMMARY, index=False)
    prediction_frame.to_csv(OUT_PREDICTIONS, index=False)
    comparison_frame.to_csv(OUT_COMPARISON, index=False)
    print(f"[cnn-mamba-inner] 运行协议: {OUT_RUNS}")
    print(f"[cnn-mamba-inner] 内层选择曲线: {OUT_SELECTION}")
    print(f"[cnn-mamba-inner] 最终重训曲线: {OUT_REFIT}")
    print(f"[cnn-mamba-inner] 外层指标: {OUT_METRICS}")
    print(f"[cnn-mamba-inner] 跨种子汇总: {OUT_SUMMARY}")
    print(f"[cnn-mamba-inner] 逐日预测: {OUT_PREDICTIONS}")
    print(f"[cnn-mamba-inner] 固定轮数配对比较: {OUT_COMPARISON}")


if __name__ == "__main__":
    main()
