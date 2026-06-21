"""Predeclared multi-seed diagnostics for the fixed ConvLSTM protocol."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import convlstm as base
import convlstm_rolling_validation as rolling


ROOT = Path(__file__).resolve().parent.parent
OUT_RUNS = ROOT / "figures" / "convlstm" / "seed_stability_runs.csv"
OUT_METRICS = ROOT / "figures" / "convlstm" / "seed_stability_metrics.csv"
OUT_SUMMARY = ROOT / "figures" / "convlstm" / "seed_stability_summary.csv"
OUT_TRAINING = ROOT / "figures" / "convlstm" / "seed_stability_training.csv"

SEEDS = (0, 1, 2, 3, 4)
ANALYSIS_METHOD = "fixed_protocol_five_seed_optimization_diagnostic"
SUMMARY_METRICS = (
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


def skill_sign(values):
    """Classify whether baseline skill has a consistent sign across seeds."""
    values = np.asarray(values, dtype=float)
    if np.all(values > 0):
        return "all_positive"
    if np.all(values <= 0):
        return "all_nonpositive"
    return "mixed"


def run_row(metadata, result, station_names):
    """Build one auditable seed-fold protocol row."""
    row = {
        **metadata,
        "analysis_method": ANALYSIS_METHOD,
        "hyperparameter_tuning": False,
        "best_seed_selected": False,
        "seed_set_predeclared": True,
    }
    row.update({
        f"qhat_{station}_mm": float(value)
        for station, value in zip(station_names, result["qhat"])
    })
    row.update({
        f"delta_scale_{station}_mm": float(value)
        for station, value in zip(station_names, result["delta_scale"])
    })
    return row


def training_rows(result, *, seed, fold):
    """Attach seed and fold identifiers to each optimization epoch."""
    return [
        {"seed": seed, "fold": fold, **epoch_row}
        for epoch_row in result["training_history"]
    ]


def aggregate_seed_metrics(metrics, *, seeds=SEEDS):
    """Aggregate every predeclared seed without selecting the best run."""
    rows = []
    group_columns = ["fold", "scope", "interval_variant"]
    for keys, group in metrics.groupby(group_columns, sort=True):
        observed_seeds = tuple(sorted(group["seed"].unique()))
        if observed_seeds != tuple(seeds):
            raise RuntimeError("多种子汇总缺少预设种子或包含额外种子")
        row = {
            **dict(zip(group_columns, keys)),
            "test_start_date": group["test_start_date"].iloc[0],
            "test_end_date": group["test_end_date"].iloc[0],
            "n_dates": int(group["n_dates"].iloc[0]),
            "n_stations": int(group["n_stations"].iloc[0]),
            "seed_count": len(seeds),
            "seeds": ",".join(str(seed) for seed in seeds),
            "baseline_rmse": float(group["baseline_rmse"].iloc[0]),
            "baseline_mae": float(group["baseline_mae"].iloc[0]),
            "mean_actual_increment": float(
                group["mean_actual_increment"].iloc[0]
            ),
            "actual_increment_std": float(
                group["actual_increment_std"].iloc[0]
            ),
            "rmse_skill_positive_seeds": int(
                (group["rmse_skill_vs_baseline"] > 0).sum()
            ),
            "mae_skill_positive_seeds": int(
                (group["mae_skill_vs_baseline"] > 0).sum()
            ),
            "rmse_skill_sign": skill_sign(group["rmse_skill_vs_baseline"]),
            "mae_skill_sign": skill_sign(group["mae_skill_vs_baseline"]),
            "best_seed_selected": False,
            "confirmatory_external_validation": False,
        }
        for metric in SUMMARY_METRICS:
            values = group[metric].astype(float)
            row.update({
                f"{metric}_mean": float(values.mean()),
                f"{metric}_std": float(values.std(ddof=1)),
                f"{metric}_min": float(values.min()),
                f"{metric}_max": float(values.max()),
            })
        rows.append(row)
    return pd.DataFrame(rows)


def validate_output_frames(runs, metrics, summary, training, station_names):
    """Reject incomplete, duplicated or non-finite multi-seed outputs."""
    expected_runs = len(SEEDS) * rolling.N_SPLITS
    expected_metrics = expected_runs * 2 * (len(station_names) + 1)
    expected_summary = rolling.N_SPLITS * 2 * (len(station_names) + 1)
    expected_training = expected_runs * base.EPOCHS
    expected_seeds = set(SEEDS)
    if len(runs) != expected_runs:
        raise RuntimeError("多种子运行协议行数不完整")
    if len(metrics) != expected_metrics:
        raise RuntimeError("多种子逐折指标行数不完整")
    if len(summary) != expected_summary:
        raise RuntimeError("多种子汇总指标行数不完整")
    if len(training) != expected_training:
        raise RuntimeError("多种子训练曲线行数不完整")
    for frame, keys in (
        (runs, ["seed", "fold"]),
        (metrics, ["seed", "fold", "scope", "interval_variant"]),
        (summary, ["fold", "scope", "interval_variant"]),
        (training, ["seed", "fold", "epoch"]),
    ):
        if frame.duplicated(keys).any():
            raise RuntimeError(f"多种子输出包含重复主键: {keys}")
    if set(runs["seed"]) != expected_seeds or set(metrics["seed"]) != expected_seeds:
        raise RuntimeError("多种子输出的种子集合与预设不一致")
    if set(training["seed"]) != expected_seeds:
        raise RuntimeError("训练曲线的种子集合与预设不一致")
    epoch_sets = training.groupby(["seed", "fold"])["epoch"].agg(set)
    expected_epochs = set(range(1, base.EPOCHS + 1))
    if any(epochs != expected_epochs for epochs in epoch_sets):
        raise RuntimeError("每个种子折必须保存全部训练 epoch")
    if runs["best_seed_selected"].any() or summary["best_seed_selected"].any():
        raise RuntimeError("稳定性诊断不得选择最佳种子")
    for frame in (runs, metrics, summary, training):
        numeric = frame.select_dtypes(include=[np.number])
        if not np.isfinite(numeric.to_numpy()).all():
            raise RuntimeError("多种子输出包含非有限数值")


def main():
    df = pd.read_csv(base.FEAT_CSV)
    dates = pd.DatetimeIndex(pd.to_datetime(df["Date"]))
    if dates.has_duplicates or not dates.is_monotonic_increasing:
        raise RuntimeError("特征日期必须严格递增且不得重复")
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
    metric_rows = []
    all_training_rows = []
    for seed in SEEDS:
        for split in splits:
            metadata = rolling.split_metadata(split, dates, seed=seed)
            result = rolling.train_predict_fold(
                df,
                disp,
                interp,
                readout_weights,
                split,
                seed=seed,
            )
            run_rows.append(run_row(metadata, result, station_names))
            fold_metrics = rolling.metric_rows(result, station_names, metadata)
            metric_rows.extend({"seed": seed, **row} for row in fold_metrics)
            all_training_rows.extend(
                training_rows(result, seed=seed, fold=split.fold)
            )
            overall = next(
                row
                for row in fold_metrics
                if row["scope"] == "overall"
                and row["interval_variant"] == "calibrated"
            )
            print(
                f"[convlstm-seeds] seed={seed} fold={split.fold}: "
                f"RMSE={overall['model_rmse']:.3f}/"
                f"{overall['baseline_rmse']:.3f} mm "
                f"increment_r={overall['increment_correlation']:.3f}"
            )

    run_frame = pd.DataFrame(run_rows)
    metric_frame = pd.DataFrame(metric_rows)
    training_frame = pd.DataFrame(all_training_rows)
    summary_frame = aggregate_seed_metrics(metric_frame)
    validate_output_frames(
        run_frame,
        metric_frame,
        summary_frame,
        training_frame,
        station_names,
    )
    OUT_RUNS.parent.mkdir(parents=True, exist_ok=True)
    run_frame.to_csv(OUT_RUNS, index=False)
    metric_frame.to_csv(OUT_METRICS, index=False)
    summary_frame.to_csv(OUT_SUMMARY, index=False)
    training_frame.to_csv(OUT_TRAINING, index=False)
    print(f"[convlstm-seeds] 运行协议: {OUT_RUNS}")
    print(f"[convlstm-seeds] 逐种子指标: {OUT_METRICS}")
    print(f"[convlstm-seeds] 跨种子汇总: {OUT_SUMMARY}")
    print(f"[convlstm-seeds] 训练诊断: {OUT_TRAINING}")


if __name__ == "__main__":
    main()
