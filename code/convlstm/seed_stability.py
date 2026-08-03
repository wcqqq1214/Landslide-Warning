"""Predeclared multi-seed diagnostics for the fixed ConvLSTM protocol."""

from __future__ import annotations

import sys
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from convlstm import elevation_diagnostic_protocol as protocol
from convlstm import model as base
from convlstm import rolling_validation as rolling

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = protocol.SEED_DIR
OUT_RUNS = OUT_DIR / "seed_stability_runs.csv"
OUT_METRICS = OUT_DIR / "seed_stability_metrics.csv"
OUT_SUMMARY = OUT_DIR / "seed_stability_summary.csv"
OUT_TRAINING = OUT_DIR / "seed_stability_training.csv"
OUT_PREDICTIONS = OUT_DIR / "seed_stability_predictions.csv"
OUT_MANIFEST = OUT_DIR / protocol.MANIFEST_NAME

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


def validate_runtime_protocol_constants():
    """Reject seed-stage drift from the immutable fixed120_v1 protocol."""
    configured = tuple(protocol.FROZEN_PROTOCOL["seed_stability"]["seeds"])
    if SEEDS != (0, 1, 2, 3, 4) or SEEDS != configured:
        raise RuntimeError("多种子集合已偏离 fixed120_v1 冻结设置")
    rolling.validate_runtime_protocol_constants()


def load_rolling_reference():
    """Load and validate the required seed-0 rolling bundle before training."""
    required = (
        rolling.OUT_FOLDS.name,
        rolling.OUT_METRICS.name,
        rolling.OUT_PREDICTIONS.name,
    )
    if not rolling.OUT_MANIFEST.is_file():
        raise RuntimeError("五种子诊断前必须先完成 seed=0 滚动验证")
    protocol.validate_stage_manifest(
        rolling.OUT_MANIFEST,
        expected_stage="convlstm-rolling",
        required_outputs=required,
    )
    frames = tuple(
        pd.read_csv(path)
        for path in (
            rolling.OUT_FOLDS,
            rolling.OUT_METRICS,
            rolling.OUT_PREDICTIONS,
        )
    )
    station_names, _, _ = base.load_station_geometry(base.DISP_COLS)
    rolling.validate_output_frames(*frames, station_names)
    rolling.validate_frozen_fold_boundaries(frames[0])
    return frames


def _assert_seed0_frame_matches(seed_frame, reference, *, keys, artifact_name):
    columns = list(reference.columns)
    candidate = seed_frame.loc[seed_frame["seed"] == 0, columns]
    candidate = candidate.sort_values(keys).reset_index(drop=True)
    expected = reference.sort_values(keys).reset_index(drop=True)
    try:
        pd.testing.assert_frame_equal(
            candidate,
            expected,
            check_dtype=False,
            check_exact=False,
            rtol=0,
            atol=1e-12,
        )
    except AssertionError as exc:
        raise RuntimeError(
            f"五种子阶段 seed=0 与滚动阶段不一致: {artifact_name}"
        ) from exc


def validate_seed0_reproduction(runs, metrics, predictions, rolling_reference):
    """Use the duplicated seed-0 fits as a deterministic reproduction gate."""
    fold_reference, metric_reference, prediction_reference = rolling_reference
    _assert_seed0_frame_matches(
        runs,
        fold_reference,
        keys=["fold"],
        artifact_name="folds",
    )
    _assert_seed0_frame_matches(
        metrics,
        metric_reference,
        keys=["fold", "scope", "interval_variant"],
        artifact_name="metrics",
    )
    _assert_seed0_frame_matches(
        predictions,
        prediction_reference,
        keys=["fold", "date", "station"],
        artifact_name="predictions",
    )


def validate_prediction_key_contract(predictions, station_names):
    """Require the full frozen seed-fold-date-station Cartesian key set."""
    expected_keys = set()
    for seed in SEEDS:
        for fold in protocol.EXPECTED_FOLDS:
            dates = pd.date_range(
                fold["test_start_date"],
                fold["test_end_date"],
            )
            if len(dates) != rolling.TEST_WINDOWS:
                raise RuntimeError("冻结测试日期数量不等于每折 287 日")
            expected_keys.update(
                (
                    seed,
                    fold["fold"],
                    date.date().isoformat(),
                    station,
                )
                for date, station in product(dates, station_names)
            )
    observed_keys = set(
        predictions[["seed", "fold", "date", "station"]].itertuples(
            index=False,
            name=None,
        )
    )
    if observed_keys != expected_keys:
        raise RuntimeError("多种子逐日预测未覆盖冻结的完整笛卡尔键集")


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
    provenance = rolling.current_model_input_provenance()
    return [
        {"seed": seed, "fold": fold, **provenance, **epoch_row}
        for epoch_row in result["training_history"]
    ]


def prediction_rows(result, station_names, test_dates, *, seed, fold):
    """Attach the seed to every fold-date-station prediction key."""
    return [
        {"seed": seed, **row}
        for row in rolling.prediction_rows(
            result,
            station_names,
            test_dates,
            fold,
        )
    ]


def aggregate_seed_metrics(metrics, *, seeds=SEEDS):
    """Aggregate every predeclared seed without selecting the best run."""
    rows = []
    group_columns = ["fold", "scope", "interval_variant"]
    for keys, group in metrics.groupby(group_columns, sort=True):
        observed_seeds = tuple(sorted(group["seed"].unique()))
        if observed_seeds != tuple(seeds):
            raise RuntimeError("多种子汇总缺少预设种子或包含额外种子")
        provenance_columns = set(rolling.MODEL_INPUT_PROVENANCE_COLUMNS)
        present_provenance = provenance_columns & set(group.columns)
        if present_provenance and present_provenance != provenance_columns:
            raise RuntimeError("多种子指标的模型输入溯源字段不完整")
        row = {
            **dict(zip(group_columns, keys)),
            **{
                column: group[column].iloc[0]
                for column in rolling.MODEL_INPUT_PROVENANCE_COLUMNS
                if column in group.columns
            },
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


def validate_output_frames(
    runs,
    metrics,
    summary,
    training,
    predictions,
    station_names,
):
    """Reject incomplete, duplicated or non-finite multi-seed outputs."""
    expected_runs = len(SEEDS) * rolling.N_SPLITS
    expected_metrics = expected_runs * 2 * (len(station_names) + 1)
    expected_summary = rolling.N_SPLITS * 2 * (len(station_names) + 1)
    expected_training = expected_runs * base.EPOCHS
    expected_predictions = (
        len(SEEDS)
        * rolling.N_SPLITS
        * rolling.TEST_WINDOWS
        * len(station_names)
    )
    expected_seeds = set(SEEDS)
    if len(runs) != expected_runs:
        raise RuntimeError("多种子运行协议行数不完整")
    if len(metrics) != expected_metrics:
        raise RuntimeError("多种子逐折指标行数不完整")
    if len(summary) != expected_summary:
        raise RuntimeError("多种子汇总指标行数不完整")
    if len(training) != expected_training:
        raise RuntimeError("多种子训练曲线行数不完整")
    if len(predictions) != expected_predictions:
        raise RuntimeError("多种子逐日预测行数不完整")
    for frame, keys in (
        (runs, ["seed", "fold"]),
        (metrics, ["seed", "fold", "scope", "interval_variant"]),
        (summary, ["fold", "scope", "interval_variant"]),
        (training, ["seed", "fold", "epoch"]),
        (predictions, ["seed", "fold", "date", "station"]),
    ):
        if frame.duplicated(keys).any():
            raise RuntimeError(f"多种子输出包含重复主键: {keys}")
    if set(runs["seed"]) != expected_seeds or set(metrics["seed"]) != expected_seeds:
        raise RuntimeError("多种子输出的种子集合与预设不一致")
    if set(training["seed"]) != expected_seeds:
        raise RuntimeError("训练曲线的种子集合与预设不一致")
    if set(predictions["seed"]) != expected_seeds:
        raise RuntimeError("逐日预测的种子集合与预设不一致")
    expected_seed_folds = set(product(SEEDS, range(1, rolling.N_SPLITS + 1)))
    for frame, name in (
        (runs, "runs"),
        (metrics, "metrics"),
        (training, "training"),
        (predictions, "predictions"),
    ):
        observed_seed_folds = set(
            frame[["seed", "fold"]].itertuples(index=False, name=None)
        )
        if observed_seed_folds != expected_seed_folds:
            raise RuntimeError(f"多种子 {name} 的种子-折组合不完整")
    if set(summary["fold"]) != set(range(1, rolling.N_SPLITS + 1)):
        raise RuntimeError("多种子汇总的折集合不完整")
    epoch_sets = training.groupby(["seed", "fold"])["epoch"].agg(set)
    expected_epochs = set(range(1, base.EPOCHS + 1))
    if any(epochs != expected_epochs for epochs in epoch_sets):
        raise RuntimeError("每个种子折必须保存全部训练 epoch")
    if runs["best_seed_selected"].any() or summary["best_seed_selected"].any():
        raise RuntimeError("稳定性诊断不得选择最佳种子")
    expected_stations = set(station_names)
    station_sets = predictions.groupby(
        ["seed", "fold", "date"]
    )["station"].agg(set)
    if any(stations != expected_stations for stations in station_sets):
        raise RuntimeError("每个种子-折-日期必须包含全部且仅包含预设测点")
    validate_prediction_key_contract(predictions, station_names)
    for frame in (runs, metrics, summary, training, predictions):
        numeric = frame.select_dtypes(include=[np.number])
        if not np.isfinite(numeric.to_numpy()).all():
            raise RuntimeError("多种子输出包含非有限数值")
        rolling.require_current_model_input_provenance(
            frame,
            artifact_name="seed stability output",
        )


def main():
    validate_runtime_protocol_constants()
    rolling_reference = load_rolling_reference()
    df = pd.read_csv(base.FEAT_CSV)
    dates = pd.DatetimeIndex(pd.to_datetime(df["Date"]))
    if dates.has_duplicates or not dates.is_monotonic_increasing:
        raise RuntimeError("特征日期必须严格递增且不得重复")
    disp = df[base.DISP_COLS].values.astype(np.float64)
    station_names, xy, elevation_m = base.load_station_geometry(base.DISP_COLS)
    interp, (grid_x, grid_y) = base.make_interpolator(
        xy,
        base.GRID_H,
        base.GRID_W,
    )
    elevation_grid = base.make_elevation_grid(elevation_m, interp)
    readout_weights = base.station_readout_weights(grid_x, grid_y, xy)
    splits = rolling.expanding_window_splits(len(df))

    run_rows = []
    metric_rows = []
    all_training_rows = []
    all_prediction_rows = []
    for seed in SEEDS:
        for split in splits:
            metadata = rolling.split_metadata(split, dates, seed=seed)
            result = rolling.train_predict_fold(
                df,
                disp,
                interp,
                readout_weights,
                split,
                elevation_grid=elevation_grid,
                seed=seed,
            )
            run_rows.append(run_row(metadata, result, station_names))
            fold_metrics = rolling.metric_rows(result, station_names, metadata)
            metric_rows.extend({"seed": seed, **row} for row in fold_metrics)
            all_training_rows.extend(
                training_rows(result, seed=seed, fold=split.fold)
            )
            test_dates = dates[
                split.split_index + base.HORIZON - 1:split.test_stop_index
            ]
            all_prediction_rows.extend(
                prediction_rows(
                    result,
                    station_names,
                    test_dates,
                    seed=seed,
                    fold=split.fold,
                )
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
    prediction_frame = pd.DataFrame(all_prediction_rows)
    summary_frame = aggregate_seed_metrics(metric_frame)
    validate_output_frames(
        run_frame,
        metric_frame,
        summary_frame,
        training_frame,
        prediction_frame,
        station_names,
    )
    for seed in SEEDS:
        rolling.validate_frozen_fold_boundaries(
            run_frame.loc[run_frame["seed"] == seed]
        )
    validate_seed0_reproduction(
        run_frame,
        metric_frame,
        prediction_frame,
        rolling_reference,
    )
    protocol.write_stage_bundle(
        OUT_DIR,
        {
            OUT_RUNS.name: run_frame,
            OUT_METRICS.name: metric_frame,
            OUT_SUMMARY.name: summary_frame,
            OUT_TRAINING.name: training_frame,
            OUT_PREDICTIONS.name: prediction_frame,
        },
        stage="convlstm-seeds",
        stage_parameters={
            "seeds": list(SEEDS),
            "fold_count": rolling.N_SPLITS,
            "run_count": len(SEEDS) * rolling.N_SPLITS,
            "best_seed_selected": False,
            "save_all_predictions": True,
            "epochs": base.EPOCHS,
            "hidden_channels": base.HIDDEN,
            "kernel_size": base.KERNEL,
            "lookback_days": base.LOOKBACK,
            "horizon_days": base.HORIZON,
            "learning_rate": base.LR,
            "test_used_for_selection": False,
        },
        source_paths=(
            Path(__file__),
            Path(rolling.__file__),
            Path(base.__file__),
            Path(protocol.__file__),
            ROOT / "code" / "convlstm" / "grid_interp.py",
        ),
    )
    print(f"[convlstm-seeds] 运行协议: {OUT_RUNS}")
    print(f"[convlstm-seeds] 逐种子指标: {OUT_METRICS}")
    print(f"[convlstm-seeds] 跨种子汇总: {OUT_SUMMARY}")
    print(f"[convlstm-seeds] 训练诊断: {OUT_TRAINING}")
    print(f"[convlstm-seeds] 逐日预测: {OUT_PREDICTIONS}")
    print(f"[convlstm-seeds] 运行清单: {OUT_MANIFEST}")


if __name__ == "__main__":
    main()
