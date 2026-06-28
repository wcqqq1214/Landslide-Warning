"""Expanding-window temporal validation for the CNN-Mamba forecast model."""

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


OUT_FOLDS = base.FIG_DIR / "rolling_validation_folds.csv"
OUT_METRICS = base.FIG_DIR / "rolling_validation_metrics.csv"
OUT_PREDICTIONS = base.FIG_DIR / "rolling_validation_predictions.csv"

N_SPLITS = 3
TEST_WINDOWS = 287
MIN_FIT_WINDOWS = 365
VALIDATION_SEED = base.SEED
VALIDATION_METHOD = "expanding_window_nonoverlapping_fixed_287d_test"


@dataclass(frozen=True)
class RollingSplit:
    fold: int
    train_windows: int
    fit_windows: int
    calibration_windows: int
    test_windows: int
    split_index: int
    test_stop_index: int


def expanding_window_splits(
    n_observations,
    *,
    n_splits=N_SPLITS,
    test_windows=TEST_WINDOWS,
    calibration_fraction=base.CAL_FRAC,
    min_fit_windows=MIN_FIT_WINDOWS,
):
    """Create fixed-length, non-overlapping tests with expanding histories."""
    integer_fields = {
        "n_observations": n_observations,
        "n_splits": n_splits,
        "test_windows": test_windows,
        "min_fit_windows": min_fit_windows,
    }
    if any(not isinstance(value, (int, np.integer)) for value in integer_fields.values()):
        raise TypeError("滚动验证的窗口参数必须为整数")
    if n_observations <= 0:
        raise ValueError("观测数必须为正数")
    if n_splits <= 0 or test_windows <= 0 or min_fit_windows <= 0:
        raise ValueError("折数、测试窗口和最小拟合窗口必须为正数")

    total_windows = n_observations - base.LOOKBACK - base.HORIZON + 1
    initial_train_windows = total_windows - n_splits * test_windows
    if initial_train_windows <= 0:
        raise ValueError("观测数不足以构造预设滚动验证折")

    splits = []
    for fold in range(1, n_splits + 1):
        train_windows = initial_train_windows + (fold - 1) * test_windows
        fit_windows, calibration_windows = base.chronological_fit_calibration_split(
            train_windows,
            calibration_fraction,
        )
        if calibration_windows == 0:
            raise ValueError("每个滚动折必须包含独立校准窗口")
        if fit_windows < min_fit_windows:
            raise ValueError(
                f"第 {fold} 折拟合窗口 {fit_windows} 少于预设最小值 "
                f"{min_fit_windows}"
            )
        split_index = train_windows + base.LOOKBACK + base.HORIZON - 1
        test_stop_index = split_index + test_windows + base.HORIZON - 1
        if test_stop_index > n_observations:
            raise RuntimeError("滚动验证测试范围超过观测序列")
        splits.append(RollingSplit(
            fold=fold,
            train_windows=train_windows,
            fit_windows=fit_windows,
            calibration_windows=calibration_windows,
            test_windows=test_windows,
            split_index=split_index,
            test_stop_index=test_stop_index,
        ))
    return splits


def split_metadata(split, dates, *, seed=VALIDATION_SEED):
    """Build auditable date boundaries for one rolling split."""
    dates = pd.DatetimeIndex(pd.to_datetime(dates))
    first_target = base.LOOKBACK + base.HORIZON - 1
    fit_dates = dates[first_target:first_target + split.fit_windows]
    calibration_dates = dates[
        first_target + split.fit_windows:split.split_index
    ]
    test_dates = dates[
        split.split_index + base.HORIZON - 1:split.test_stop_index
    ]
    if (
        len(fit_dates) != split.fit_windows
        or len(calibration_dates) != split.calibration_windows
        or len(test_dates) != split.test_windows
    ):
        raise RuntimeError("滚动验证日期边界与窗口计划不一致")
    if not (fit_dates[-1] < calibration_dates[0] <= calibration_dates[-1] < test_dates[0]):
        raise RuntimeError("滚动验证必须满足拟合期 < 校准期 < 测试期")
    return {
        "fold": split.fold,
        "validation_method": VALIDATION_METHOD,
        "fit_start_date": fit_dates[0].date().isoformat(),
        "fit_end_date": fit_dates[-1].date().isoformat(),
        "fit_windows": split.fit_windows,
        "calibration_start_date": calibration_dates[0].date().isoformat(),
        "calibration_end_date": calibration_dates[-1].date().isoformat(),
        "calibration_windows": split.calibration_windows,
        "train_windows": split.train_windows,
        "test_start_date": test_dates[0].date().isoformat(),
        "test_end_date": test_dates[-1].date().isoformat(),
        "test_windows": len(test_dates),
        "lookback_days": base.LOOKBACK,
        "horizon_days": base.HORIZON,
        "calibration_fraction_within_train": base.CAL_FRAC,
        "target_coverage": base.TARGET_COVERAGE,
        "seed": seed,
        "model": base.MODEL_NAME,
        "hidden_channels": base.HIDDEN,
        "kernel_size": base.KERNEL,
        "mamba_state_dim": base.MAMBA_STATE_DIM,
        "mamba_conv": base.MAMBA_CONV,
        "mamba_expand": base.MAMBA_EXPAND,
        "epochs": base.EPOCHS,
        "learning_rate": base.LR,
        "test_length_basis": "matches_existing_287d_holdout",
        "test_length_selected_from_results": False,
        "confirmatory_external_validation": False,
    }


def train_predict_fold(
    df,
    disp,
    interp,
    readout_weights,
    split,
    *,
    seed,
    epochs=base.EPOCHS,
    hidden_channels=base.HIDDEN,
    weight_decay=0.0,
):
    """Fit, calibrate and predict one fold without accessing its future targets."""
    if not isinstance(epochs, (int, np.integer)):
        raise TypeError("训练轮数必须为整数")
    if epochs <= 0:
        raise ValueError("训练轮数必须为正数")
    if not isinstance(hidden_channels, (int, np.integer)):
        raise TypeError("隐藏通道数必须为整数")
    if hidden_channels <= 0:
        raise ValueError("隐藏通道数必须为正数")
    if not np.isfinite(weight_decay) or weight_decay < 0:
        raise ValueError("权重衰减必须为非负有限数值")
    torch.manual_seed(seed)
    np.random.seed(seed)

    fit_stats_stop = split.fit_windows + base.LOOKBACK + base.HORIZON - 1
    inputs, _ = base.make_model_inputs(df, disp, fit_stats_stop, interp)
    x_train, _, _ = base.make_windows(
        inputs[:split.split_index],
        base.LOOKBACK,
        base.HORIZON,
    )
    y_train_future, y_train_last, y_train_delta = base.make_station_windows(
        disp,
        split=base.LOOKBACK,
        lookback=base.LOOKBACK,
        horizon=base.HORIZON,
        stop=split.split_index,
    )
    x_test = base.make_windows(
        inputs[split.split_index - base.LOOKBACK:split.test_stop_index],
        base.LOOKBACK,
        base.HORIZON,
    )[0]
    y_test, last_test, _ = base.make_station_windows(
        disp,
        split=split.split_index,
        lookback=base.LOOKBACK,
        horizon=base.HORIZON,
        stop=split.test_stop_index,
    )
    if len(x_train) != split.train_windows or len(y_train_delta) != len(x_train):
        raise RuntimeError("滚动折训练窗口数量与计划不一致")
    if len(x_test) != split.test_windows or len(y_test) != len(x_test):
        raise RuntimeError("滚动折测试窗口数量与计划不一致")

    delta_scale = base.make_delta_scale(y_train_delta[:split.fit_windows])
    y_train_normalized = (y_train_delta / delta_scale).astype(np.float32)
    device = base.require_cuda_device()
    x_train_tensor = torch.from_numpy(x_train).to(device)
    y_train_tensor = torch.from_numpy(y_train_normalized).to(device)
    readout_tensor = torch.from_numpy(readout_weights).to(device)

    model = base.ForecastModel(
        in_ch=x_train_tensor.shape[2],
        hid_ch=hidden_channels,
        kernel=base.KERNEL,
        quantiles=base.QUANTILES,
    ).to(device)
    optimizer = torch.optim.Adam(
        model.parameters(),
        lr=base.LR,
        weight_decay=weight_decay,
    )
    training_history = []
    for epoch in range(epochs):
        model.train()
        optimizer.zero_grad()
        prediction_grid = model(x_train_tensor[:split.fit_windows])
        prediction_station = base.readout_grid_at_stations(
            prediction_grid,
            readout_tensor,
        )
        loss = base.pinball_loss(
            prediction_station,
            y_train_tensor[:split.fit_windows],
            base.QUANTILES,
        )
        loss.backward()
        gradient_l2_norm = float(torch.sqrt(sum(
            parameter.grad.detach().square().sum()
            for parameter in model.parameters()
            if parameter.grad is not None
        )))
        optimizer.step()
        training_history.append({
            "epoch": epoch + 1,
            "train_pinball_loss": float(loss.detach()),
            "gradient_l2_norm": gradient_l2_norm,
        })

    model.eval()
    quantile_index = {quantile: i for i, quantile in enumerate(base.QUANTILES)}
    calibration_slice = slice(split.fit_windows, split.train_windows)
    with torch.no_grad():
        calibration_grid = model(x_train_tensor[calibration_slice])
        calibration_normalized = base.readout_grid_at_stations(
            calibration_grid,
            readout_tensor,
        ).cpu().numpy()
        test_grid = model(torch.from_numpy(x_test).to(device))
        test_normalized = base.readout_grid_at_stations(
            test_grid,
            readout_tensor,
        ).cpu().numpy()

    calibration_last = y_train_last[calibration_slice]
    calibration_actual = y_train_future[calibration_slice]
    calibration_prediction = (
        calibration_last[:, None, :]
        + calibration_normalized * delta_scale[None, None, :]
    )
    calibration_p10 = calibration_prediction[:, quantile_index[0.1]]
    calibration_p90 = calibration_prediction[:, quantile_index[0.9]]
    _, _, qhat = base.calibrate_intervals(
        calibration_p10,
        calibration_p90,
        calibration_actual,
        target_coverage=base.TARGET_COVERAGE,
    )

    prediction = (
        last_test[:, None, :] + test_normalized * delta_scale[None, None, :]
    )
    raw_p10 = prediction[:, quantile_index[0.1]]
    p50 = prediction[:, quantile_index[0.5]]
    raw_p90 = prediction[:, quantile_index[0.9]]
    return {
        "raw_p10": raw_p10,
        "p50": p50,
        "raw_p90": raw_p90,
        "calibrated_p10": raw_p10 - qhat[None, :],
        "calibrated_p90": raw_p90 + qhat[None, :],
        "actual": y_test,
        "persistence": last_test,
        "qhat": qhat,
        "delta_scale": delta_scale,
        "training_history": training_history,
    }


def increment_diagnostics(actual, predicted, persistence):
    """Describe whether predicted increments track observed temporal changes."""
    actual_increment = np.asarray(actual - persistence).reshape(-1)
    predicted_increment = np.asarray(predicted - persistence).reshape(-1)
    if len(actual_increment) < 2:
        return {
            "actual_increment_std": np.nan,
            "predicted_increment_std": np.nan,
            "increment_std_ratio": np.nan,
            "increment_correlation": np.nan,
        }
    actual_std = float(actual_increment.std(ddof=1))
    predicted_std = float(predicted_increment.std(ddof=1))
    correlation = (
        float(np.corrcoef(actual_increment, predicted_increment)[0, 1])
        if actual_std > 0 and predicted_std > 0
        else np.nan
    )
    return {
        "actual_increment_std": actual_std,
        "predicted_increment_std": predicted_std,
        "increment_std_ratio": (
            predicted_std / actual_std if actual_std > 0 else np.nan
        ),
        "increment_correlation": correlation,
    }


def metric_rows(result, station_names, metadata):
    """Return overall and station-level raw/calibrated metrics for one fold."""
    rows = []
    scopes = [("overall", None), *zip(station_names, range(len(station_names)))]
    for interval_variant, lower_key, upper_key in (
        ("raw", "raw_p10", "raw_p90"),
        ("calibrated", "calibrated_p10", "calibrated_p90"),
    ):
        for scope, station_index in scopes:
            def select(values):
                return values if station_index is None else values[:, station_index]

            metrics = base.compute_forecast_metrics(
                select(result[lower_key]),
                select(result["p50"]),
                select(result[upper_key]),
                select(result["actual"]),
                select(result["persistence"]),
            )
            actual = select(result["actual"])
            persistence = select(result["persistence"])
            p50 = select(result["p50"])
            increment_metrics = increment_diagnostics(actual, p50, persistence)
            rows.append({
                "fold": metadata["fold"],
                "scope": scope,
                "interval_variant": interval_variant,
                "test_start_date": metadata["test_start_date"],
                "test_end_date": metadata["test_end_date"],
                "n_dates": metadata["test_windows"],
                "n_stations": len(station_names) if station_index is None else 1,
                "model_mean_error": float((p50 - actual).mean()),
                "baseline_mean_error": float((persistence - actual).mean()),
                "mean_actual_increment": float((actual - persistence).mean()),
                "mean_predicted_increment": float((p50 - persistence).mean()),
                **increment_metrics,
                **metrics,
            })
    return rows


def prediction_rows(result, station_names, test_dates, fold):
    """Return long-form predictions so every reported metric remains auditable."""
    rows = []
    for date_index, date in enumerate(pd.DatetimeIndex(test_dates)):
        for station_index, station in enumerate(station_names):
            rows.append({
                "fold": fold,
                "date": date.date().isoformat(),
                "station": station,
                "actual": result["actual"][date_index, station_index],
                "persistence": result["persistence"][date_index, station_index],
                "raw_p10": result["raw_p10"][date_index, station_index],
                "p50": result["p50"][date_index, station_index],
                "raw_p90": result["raw_p90"][date_index, station_index],
                "calibrated_p10": result["calibrated_p10"][
                    date_index,
                    station_index,
                ],
                "calibrated_p90": result["calibrated_p90"][
                    date_index,
                    station_index,
                ],
                "qhat_mm": result["qhat"][station_index],
            })
    return rows


def validate_output_frames(folds, metrics, predictions, station_names):
    """Reject incomplete, duplicated or numerically invalid rolling outputs."""
    expected_metric_rows = len(folds) * 2 * (len(station_names) + 1)
    expected_prediction_rows = int(folds["test_windows"].sum()) * len(station_names)
    if len(metrics) != expected_metric_rows:
        raise RuntimeError("滚动指标行数与折数、测点数不一致")
    if len(predictions) != expected_prediction_rows:
        raise RuntimeError("滚动逐日预测行数与折计划不一致")
    if folds["fold"].duplicated().any():
        raise RuntimeError("滚动折编号不得重复")
    if predictions.duplicated(["fold", "date", "station"]).any():
        raise RuntimeError("滚动逐日预测包含重复的折-日期-测点")
    if predictions.groupby("date")["fold"].nunique().max() != 1:
        raise RuntimeError("同一日期不得进入多个滚动测试折")
    expected_stations = set(station_names)
    station_sets = predictions.groupby(["fold", "date"])["station"].agg(set)
    if any(stations != expected_stations for stations in station_sets):
        raise RuntimeError("每个滚动测试日期必须包含全部且仅包含预设测点")

    prediction_numeric = [
        "actual",
        "persistence",
        "raw_p10",
        "p50",
        "raw_p90",
        "calibrated_p10",
        "calibrated_p90",
        "qhat_mm",
    ]
    metric_numeric = metrics.select_dtypes(include=[np.number]).columns
    if not np.isfinite(predictions[prediction_numeric].to_numpy()).all():
        raise RuntimeError("滚动逐日预测包含非有限数值")
    if not np.isfinite(metrics[metric_numeric].to_numpy()).all():
        raise RuntimeError("滚动指标包含非有限数值")
    if not (
        (predictions["raw_p10"] <= predictions["p50"]).all()
        and (predictions["p50"] <= predictions["raw_p90"]).all()
        and (predictions["calibrated_p10"] <= predictions["p50"]).all()
        and (predictions["p50"] <= predictions["calibrated_p90"]).all()
    ):
        raise RuntimeError("滚动预测分位数顺序异常")


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
    splits = expanding_window_splits(len(df))

    fold_rows = []
    all_metric_rows = []
    all_prediction_rows = []
    for split in splits:
        metadata = split_metadata(split, dates)
        result = train_predict_fold(
            df,
            disp,
            interp,
            readout_weights,
            split,
            seed=VALIDATION_SEED,
        )
        metadata.update({
            f"qhat_{station}_mm": float(value)
            for station, value in zip(station_names, result["qhat"])
        })
        test_dates = dates[
            split.split_index + base.HORIZON - 1:split.test_stop_index
        ]
        fold_rows.append(metadata)
        fold_metric_rows = metric_rows(result, station_names, metadata)
        all_metric_rows.extend(fold_metric_rows)
        all_prediction_rows.extend(
            prediction_rows(result, station_names, test_dates, split.fold)
        )

        overall = next(
            row
            for row in fold_metric_rows
            if row["scope"] == "overall" and row["interval_variant"] == "raw"
        )
        calibrated_overall = next(
            row
            for row in fold_metric_rows
            if row["scope"] == "overall"
            and row["interval_variant"] == "calibrated"
        )
        print(
            f"[cnn-mamba-rolling] fold {split.fold}: "
            f"test={metadata['test_start_date']}..{metadata['test_end_date']} "
            f"RMSE={overall['model_rmse']:.3f}/"
            f"{overall['baseline_rmse']:.3f} mm "
            f"coverage={calibrated_overall['coverage']:.3f}"
        )

    fold_frame = pd.DataFrame(fold_rows)
    metric_frame = pd.DataFrame(all_metric_rows)
    prediction_frame = pd.DataFrame(all_prediction_rows)
    validate_output_frames(
        fold_frame,
        metric_frame,
        prediction_frame,
        station_names,
    )
    OUT_FOLDS.parent.mkdir(parents=True, exist_ok=True)
    fold_frame.to_csv(OUT_FOLDS, index=False)
    metric_frame.to_csv(OUT_METRICS, index=False)
    prediction_frame.to_csv(OUT_PREDICTIONS, index=False)
    print(f"[cnn-mamba-rolling] 折计划: {OUT_FOLDS}")
    print(f"[cnn-mamba-rolling] 逐折指标: {OUT_METRICS}")
    print(f"[cnn-mamba-rolling] 逐日预测: {OUT_PREDICTIONS}")


if __name__ == "__main__":
    main()
