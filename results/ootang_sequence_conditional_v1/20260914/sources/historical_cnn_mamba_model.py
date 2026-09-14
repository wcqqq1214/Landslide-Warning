"""CNN-Mamba displacement-interval forecast with station-level evaluation."""
from pathlib import Path
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F

try:
    from mamba_ssm import Mamba
except ImportError:  # pragma: no cover - exercised only before CUDA deps install.
    Mamba = None

CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from cnn_mamba.block_bootstrap import moving_block_indices, percentile_interval  # noqa: E402
from cnn_mamba.grid_interp import GRID_H, GRID_W, load_coords, make_interpolator  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FEAT_CSV = ROOT / "data" / "features.csv"
OUT_PT = ROOT / "models" / "cnn_mamba.pt"
FIG_DIR = ROOT / "figures" / "cnn_mamba"
OUT_PNG = FIG_DIR / "forecast_interval.png"
OUT_METRICS = FIG_DIR / "forecast_metrics.csv"
OUT_PERIOD_METRICS = FIG_DIR / "forecast_period_metrics.csv"
OUT_CALIBRATION_METRICS = FIG_DIR / "forecast_calibration_metrics.csv"
OUT_BOOTSTRAP_CI = FIG_DIR / "forecast_bootstrap_ci.csv"

DISP_COLS = ["MJ9_disp", "MJ1_disp", "MJ3_disp",
             "ATU1_disp", "ATU2_disp", "ATU3_disp", "ATU4_disp", "ATU5_disp"]
EXOG_COLS = ["RWL", "RWL_rate", "Rain_cum7", "Rain_cum15", "Rain_cum30"]
THESIS_WINDOWS = {"MJ1": 2, "MJ9": 7, "MJ3": 2}
PLOT_STATIONS = ["MJ9", "MJ1", "MJ3"]
LOOKBACK = max(THESIS_WINDOWS.values())
HORIZON = 1
TRAIN_FRAC = 0.8
CAL_FRAC = 0.2
TARGET_COVERAGE = 0.8
QUANTILES = [0.1, 0.5, 0.9]
HIDDEN = 16
KERNEL = 3
MAMBA_STATE_DIM = 16
MAMBA_CONV = 4
MAMBA_EXPAND = 2
EPOCHS = 120
LR = 1e-3
SEED = 0
MODEL_NAME = "OfficialCNNMambaForecast"
BOOTSTRAP_RESAMPLES = 1000
BOOTSTRAP_BLOCK_LENGTHS = (7, 14, 30)
BOOTSTRAP_PRIMARY_BLOCK_LENGTH = 14
BOOTSTRAP_CONFIDENCE_LEVEL = 0.95
BOOTSTRAP_SEED = 20260621

POINT_BOOTSTRAP_METRICS = (
    "model_rmse",
    "baseline_rmse",
    "rmse_difference_vs_baseline",
    "rmse_skill_vs_baseline",
    "model_mae",
    "baseline_mae",
    "mae_difference_vs_baseline",
    "mae_skill_vs_baseline",
)
INTERVAL_BOOTSTRAP_METRICS = (
    "coverage",
    "mean_width",
    "mean_pinball",
    "interval_score_80",
)
CALIBRATION_COMPARISON_BOOTSTRAP_METRICS = (
    "coverage",
    "absolute_coverage_gap",
    "mean_width",
    "mean_pinball",
    "interval_score_80",
)

torch.manual_seed(SEED)
np.random.seed(SEED)


def require_cuda_device():
    """Return the CUDA device required by the official mamba-ssm kernels."""
    if Mamba is None:
        raise RuntimeError(
            "Official CNN-Mamba requires mamba-ssm. Install it in WSL/Linux "
            "with CUDA, for example: "
            "pip install 'mamba-ssm[causal-conv1d]' --no-build-isolation"
        )
    if not torch.cuda.is_available():
        raise RuntimeError(
            "Official mamba-ssm requires an NVIDIA CUDA device for this "
            "experiment. Run it in WSL/Linux with a CUDA-enabled PyTorch build."
        )
    return torch.device("cuda")


class CNNMambaForecast(nn.Module):
    """CNN encodes each grid frame, then official mamba-ssm mixes time."""

    def __init__(
        self,
        hid_ch,
        kernel,
        n_q=None,
        in_ch=1,
        quantiles=None,
        state_dim=MAMBA_STATE_DIM,
        mamba_conv=MAMBA_CONV,
        mamba_expand=MAMBA_EXPAND,
    ):
        super().__init__()
        if Mamba is None:
            raise RuntimeError(
                "mamba_ssm.Mamba is not available. Install official "
                "mamba-ssm before constructing CNNMambaForecast."
            )
        self.quantiles = list(QUANTILES if quantiles is None else quantiles)
        self.n_q = len(self.quantiles) if n_q is None else n_q
        if self.n_q != 3 or self.quantiles != [0.1, 0.5, 0.9]:
            raise ValueError("当前单调输出头只支持 P10/P50/P90 三个分位数")
        pad = kernel // 2
        group_count = next(
            groups
            for groups in range(min(4, hid_ch), 0, -1)
            if hid_ch % groups == 0
        )
        self.spatial = nn.Sequential(
            nn.Conv2d(in_ch, hid_ch, kernel, padding=pad),
            nn.GroupNorm(group_count, hid_ch),
            nn.SiLU(),
            nn.Conv2d(hid_ch, hid_ch, 1),
            nn.SiLU(),
        )
        self.mamba = Mamba(
            d_model=hid_ch,
            d_state=state_dim,
            d_conv=mamba_conv,
            expand=mamba_expand,
        )
        self.head = nn.Conv2d(hid_ch, 3, 1)

    def forward(self, x):
        if x.ndim == 4:
            x = x.unsqueeze(2)
        batch, steps, channels, height, width = x.shape
        frames = x.reshape(batch * steps, channels, height, width)
        encoded = self.spatial(frames).reshape(
            batch,
            steps,
            -1,
            height,
            width,
        )
        tokens = encoded.permute(0, 3, 4, 1, 2).reshape(
            batch * height * width,
            steps,
            -1,
        )
        mixed = self.mamba(tokens)[:, -1]
        latest = mixed.reshape(batch, height, width, -1).permute(0, 3, 1, 2)
        raw = self.head(latest)
        return ordered_quantiles_from_raw(raw)


ForecastModel = CNNMambaForecast


def ordered_quantiles_from_raw(raw):
    """Convert raw low-width, median and high-width channels to ordered quantiles."""
    low_width = F.softplus(raw[:, 0:1])
    median = raw[:, 1:2]
    high_width = F.softplus(raw[:, 2:3])
    return torch.cat([median - low_width, median, median + high_width], dim=1)


def make_windows(arr, lookback, horizon):
    """Slice sequential arrays into lookback windows."""
    X, dy, last = [], [], []
    for i in range(len(arr) - lookback - horizon + 1):
        win = arr[i:i + lookback]
        future = arr[i + lookback + horizon - 1]
        X.append(win)
        last.append(win[-1])
        dy.append(future - win[-1])
    return np.array(X), np.array(dy), np.array(last)


def make_station_windows(values, split, lookback, horizon, stop=None):
    """按真实测点序列切窗口目标。

    split 表示第一个预测目标所在的时间索引。
    """
    start = split - lookback
    if start < 0:
        raise ValueError("split 必须大于等于 lookback")
    stop = len(values) if stop is None else stop
    future, last, delta = [], [], []
    for i in range(start, stop - lookback - horizon + 1):
        last_i = i + lookback - 1
        future_i = i + lookback + horizon - 1
        last.append(values[last_i])
        future.append(values[future_i])
        delta.append(values[future_i] - values[last_i])
    return np.array(future), np.array(last), np.array(delta)


def pinball_loss(pred, target, quantiles):
    """Quantile pinball loss."""
    target = target.unsqueeze(1)
    err = target - pred
    q_shape = [1, len(quantiles)] + [1] * (pred.ndim - 2)
    q = torch.tensor(quantiles, device=pred.device).view(*q_shape)
    return torch.maximum(q * err, (q - 1) * err).mean()


def station_readout_weights(gx, gy, xy, power=2.0, eps=1e-6):
    """从 H×W 网格读回测点的固定 IDW 权重矩阵,形状 (N_station,H*W)。"""
    gpts = np.column_stack([gx.ravel(), gy.ravel()])
    d = np.linalg.norm(xy[:, None, :] - gpts[None, :, :], axis=2)
    d = np.maximum(d, eps)
    w = 1.0 / (d ** power)
    w /= w.sum(axis=1, keepdims=True)
    return w.astype(np.float32)


def readout_grid_at_stations(grid_values, weights):
    """Read grid values back to station values."""
    if torch.is_tensor(grid_values):
        B, Q, H, W = grid_values.shape
        flat = grid_values.reshape(B, Q, H * W)
        return torch.einsum("bqm,nm->bqn", flat, weights.to(grid_values.device))
    B, Q, H, W = grid_values.shape
    flat = grid_values.reshape(B, Q, H * W)
    return np.einsum("bqm,nm->bqn", flat, weights)


def compute_forecast_metrics(p10, p50, p90, y_true, last):
    """Compute model, persistence baseline and interval metrics."""
    model_err = p50 - y_true
    baseline_err = last - y_true
    baseline_rmse = float(np.sqrt((baseline_err ** 2).mean()))
    model_rmse = float(np.sqrt((model_err ** 2).mean()))
    target_mean = float(y_true.mean())
    denominator = float(((y_true - target_mean) ** 2).sum())
    model_efficiency = (
        float(1.0 - (model_err ** 2).sum() / denominator)
        if denominator > 0
        else np.nan
    )
    baseline_efficiency = (
        float(1.0 - (baseline_err ** 2).sum() / denominator)
        if denominator > 0
        else np.nan
    )
    pinball = {}
    for quantile, prediction in zip(QUANTILES, (p10, p50, p90)):
        error = y_true - prediction
        pinball[quantile] = float(
            np.maximum(quantile * error, (quantile - 1) * error).mean()
        )
    alpha = 1.0 - TARGET_COVERAGE
    width = p90 - p10
    interval_score = (
        width
        + (2.0 / alpha) * np.maximum(p10 - y_true, 0.0)
        + (2.0 / alpha) * np.maximum(y_true - p90, 0.0)
    )
    coverage = float(((y_true >= p10) & (y_true <= p90)).mean())
    metrics = {
        "model_rmse": model_rmse,
        "model_mae": float(np.abs(model_err).mean()),
        "model_r2": model_efficiency,
        "model_nse": model_efficiency,
        "baseline_rmse": baseline_rmse,
        "baseline_mae": float(np.abs(baseline_err).mean()),
        "baseline_r2": baseline_efficiency,
        "baseline_nse": baseline_efficiency,
        "pinball_p10": pinball[0.1],
        "pinball_p50": pinball[0.5],
        "pinball_p90": pinball[0.9],
        "mean_pinball": float(np.mean(list(pinball.values()))),
        "coverage": coverage,
        "coverage_gap": coverage - TARGET_COVERAGE,
        "mean_width": float(width.mean()),
        "interval_score_80": float(interval_score.mean()),
        "p10_gt_p50": int((p10 > p50).sum()),
        "p50_gt_p90": int((p50 > p90).sum()),
        "total_points": int(p50.size),
    }
    metrics["rmse_skill_vs_baseline"] = (
        float(1.0 - model_rmse / baseline_rmse) if baseline_rmse > 0 else np.nan
    )
    metrics["rmse_difference_vs_baseline"] = model_rmse - baseline_rmse
    metrics["mae_difference_vs_baseline"] = (
        metrics["model_mae"] - metrics["baseline_mae"]
    )
    metrics["mae_skill_vs_baseline"] = (
        float(1.0 - metrics["model_mae"] / metrics["baseline_mae"])
        if metrics["baseline_mae"] > 0
        else np.nan
    )
    return metrics


def station_metric_rows(
    p10,
    p50,
    p90,
    y_true,
    last,
    station_names,
    thesis_windows=None,
    interval_variant="calibrated",
):
    rows = []
    thesis_windows = {} if thesis_windows is None else thesis_windows
    for i, station in enumerate(station_names):
        metrics = compute_forecast_metrics(
            p10[:, i], p50[:, i], p90[:, i], y_true[:, i], last[:, i]
        )
        rows.append({
            "station": station,
            "thesis_window": thesis_windows.get(station, ""),
            "interval_variant": interval_variant,
            **metrics,
        })
    return rows


def period_metric_rows(
    p10,
    p50,
    p90,
    y_true,
    last,
    dates,
    n_periods=3,
    interval_variant="calibrated",
):
    """Evaluate contiguous test-period blocks without reshuffling dates."""
    dates = pd.DatetimeIndex(pd.to_datetime(dates))
    if len(dates) != len(y_true):
        raise ValueError("dates 与预测目标长度不一致")
    rows = []
    for number, indices in enumerate(np.array_split(np.arange(len(dates)), n_periods), 1):
        if len(indices) == 0:
            continue
        metrics = compute_forecast_metrics(
            p10[indices],
            p50[indices],
            p90[indices],
            y_true[indices],
            last[indices],
        )
        rows.append({
            "period": f"test_block_{number}",
            "interval_variant": interval_variant,
            "start_date": dates[indices[0]].date().isoformat(),
            "end_date": dates[indices[-1]].date().isoformat(),
            "n_dates": int(len(indices)),
            **metrics,
        })
    return rows


def make_delta_scale(train_delta, floor=0.05):
    """Scale normalized targets by train-set daily displacement increments."""
    return np.maximum(train_delta.std(axis=0), floor)


def chronological_fit_calibration_split(n_windows, calibration_fraction):
    """Return chronological fit/calibration counts without shuffling."""
    if n_windows <= 0:
        raise ValueError("n_windows 必须为正数")
    if not 0 <= calibration_fraction < 1:
        raise ValueError("calibration_fraction 必须在 [0, 1) 内")
    n_calibration = int(n_windows * calibration_fraction)
    if calibration_fraction > 0 and n_calibration == 0:
        raise ValueError("校准比例非零但校准窗口数量为 0")
    n_fit = n_windows - n_calibration
    if n_fit <= 0:
        raise ValueError("拟合窗口数量必须为正数")
    return n_fit, n_calibration


def calibrate_intervals(p10, p90, y_true, target_coverage=TARGET_COVERAGE):
    """Expand P10/P90 using a separate conformal score for each station."""
    if not 0 < target_coverage < 1:
        raise ValueError("target_coverage 必须在 (0, 1) 内")
    p10 = np.asarray(p10, dtype=float)
    p90 = np.asarray(p90, dtype=float)
    y_true = np.asarray(y_true, dtype=float)
    if p10.shape != p90.shape or p10.shape != y_true.shape:
        raise ValueError("校准预测与真实值形状必须一致")
    if p10.ndim != 2 or p10.shape[0] == 0:
        raise ValueError("校准输入必须是非空的日期 x 测点二维数组")

    scores = np.maximum(p10 - y_true, y_true - p90)
    level = min(
        1.0,
        np.ceil((len(scores) + 1) * target_coverage) / len(scores),
    )
    qhat = np.quantile(scores, level, axis=0, method="higher")
    qhat = np.maximum(qhat, 0.0)
    return p10 - qhat[None, :], p90 + qhat[None, :], qhat


def calibration_metric_rows(
    station_names,
    qhat,
    calibration_raw,
    calibration_adjusted,
    test_raw,
    test_adjusted,
    split_metadata,
):
    """Build an auditable raw-versus-calibrated interval comparison."""
    rows = []
    for index, station in enumerate(station_names):
        row = {
            "station": station,
            "qhat_mm": float(qhat[index]),
            **split_metadata,
        }
        for sample_name, raw_metrics, adjusted_metrics in (
            ("calibration", calibration_raw[index], calibration_adjusted[index]),
            ("test", test_raw[index], test_adjusted[index]),
        ):
            for metric_name in (
                "coverage",
                "coverage_gap",
                "mean_width",
                "mean_pinball",
                "interval_score_80",
            ):
                row[f"{sample_name}_raw_{metric_name}"] = raw_metrics[metric_name]
                row[f"{sample_name}_calibrated_{metric_name}"] = adjusted_metrics[
                    metric_name
                ]
        rows.append(row)
    return rows


def bootstrap_ci_rows(
    raw_p10,
    p50,
    raw_p90,
    calibrated_p10,
    calibrated_p90,
    y_true,
    last,
    station_names,
    test_dates,
    *,
    block_lengths=BOOTSTRAP_BLOCK_LENGTHS,
    resamples=BOOTSTRAP_RESAMPLES,
    confidence_level=BOOTSTRAP_CONFIDENCE_LEVEL,
    seed=BOOTSTRAP_SEED,
    primary_block_length=BOOTSTRAP_PRIMARY_BLOCK_LENGTH,
    target_coverage=TARGET_COVERAGE,
):
    """Estimate paired metric uncertainty with date-level moving blocks."""
    arrays = [raw_p10, p50, raw_p90, calibrated_p10, calibrated_p90, y_true, last]
    arrays = [np.asarray(values, dtype=float) for values in arrays]
    expected_shape = arrays[0].shape
    if any(values.shape != expected_shape for values in arrays):
        raise ValueError("bootstrap 输入数组形状必须一致")
    if len(expected_shape) != 2 or expected_shape[0] == 0:
        raise ValueError("bootstrap 输入必须是非空的日期 x 测点二维数组")
    if expected_shape[1] != len(station_names):
        raise ValueError("station_names 与预测数组测点数不一致")
    if len(set(station_names)) != len(station_names):
        raise ValueError("station_names 不得重复")
    if resamples <= 0:
        raise ValueError("resamples 必须为正数")
    if not 0 < confidence_level < 1:
        raise ValueError("confidence_level 必须在 0 和 1 之间")
    if not 0 < target_coverage < 1:
        raise ValueError("target_coverage 必须在 0 和 1 之间")

    dates = pd.DatetimeIndex(pd.to_datetime(test_dates))
    if len(dates) != expected_shape[0]:
        raise ValueError("test_dates 与预测日期数不一致")
    if dates.has_duplicates or not dates.is_monotonic_increasing:
        raise ValueError("test_dates 必须严格递增且不得重复")
    if len(dates) > 1 and not np.all(np.diff(dates.asi8) == pd.Timedelta(days=1).value):
        raise ValueError("以天为单位的 block bootstrap 要求测试日期逐日连续")

    block_lengths = tuple(int(length) for length in block_lengths)
    if not block_lengths:
        raise ValueError("block_lengths 不得为空")
    if len(set(block_lengths)) != len(block_lengths):
        raise ValueError("block_lengths 不得重复")
    if any(length < 1 or length > len(dates) for length in block_lengths):
        raise ValueError("block_lengths 必须在 1 和测试日期数之间")
    if int(primary_block_length) not in block_lengths:
        raise ValueError("primary_block_length 必须包含在 block_lengths 中")

    raw_p10, p50, raw_p90, calibrated_p10, calibrated_p90, y_true, last = arrays
    scopes = [("overall", None), *zip(station_names, range(len(station_names)))]

    def select_scope(values, indices, station_index):
        selected = values[indices]
        return selected if station_index is None else selected[:, station_index]

    rows = []
    for block_length in block_lengths:
        effective_seed = seed + int(block_length)
        rng = np.random.default_rng(effective_seed)
        samples = {}
        for scope, station_index in scopes:
            for metric_name in POINT_BOOTSTRAP_METRICS:
                samples[(scope, "not_applicable", metric_name)] = []
            for variant in ("raw", "calibrated"):
                for metric_name in INTERVAL_BOOTSTRAP_METRICS:
                    samples[(scope, variant, metric_name)] = []
            for metric_name in CALIBRATION_COMPARISON_BOOTSTRAP_METRICS:
                samples[(scope, "calibrated_minus_raw", metric_name)] = []

        for _ in range(resamples):
            indices = moving_block_indices(len(dates), block_length, rng)
            for scope, station_index in scopes:
                raw_metrics = compute_forecast_metrics(
                    select_scope(raw_p10, indices, station_index),
                    select_scope(p50, indices, station_index),
                    select_scope(raw_p90, indices, station_index),
                    select_scope(y_true, indices, station_index),
                    select_scope(last, indices, station_index),
                )
                calibrated_metrics = compute_forecast_metrics(
                    select_scope(calibrated_p10, indices, station_index),
                    select_scope(p50, indices, station_index),
                    select_scope(calibrated_p90, indices, station_index),
                    select_scope(y_true, indices, station_index),
                    select_scope(last, indices, station_index),
                )
                for metric_name in POINT_BOOTSTRAP_METRICS:
                    samples[(scope, "not_applicable", metric_name)].append(
                        raw_metrics[metric_name]
                    )
                for metric_name in INTERVAL_BOOTSTRAP_METRICS:
                    samples[(scope, "raw", metric_name)].append(
                        raw_metrics[metric_name]
                    )
                    samples[(scope, "calibrated", metric_name)].append(
                        calibrated_metrics[metric_name]
                    )
                calibration_comparison = {
                    metric_name: calibrated_metrics[metric_name]
                    - raw_metrics[metric_name]
                    for metric_name in (
                        "coverage",
                        "mean_width",
                        "mean_pinball",
                        "interval_score_80",
                    )
                }
                calibration_comparison["absolute_coverage_gap"] = (
                    abs(calibrated_metrics["coverage"] - target_coverage)
                    - abs(raw_metrics["coverage"] - target_coverage)
                )
                for metric_name in CALIBRATION_COMPARISON_BOOTSTRAP_METRICS:
                    samples[(
                        scope,
                        "calibrated_minus_raw",
                        metric_name,
                    )].append(calibration_comparison[metric_name])

        block_basis = {
            7: "model_lookback",
            14: "two_times_lookback_primary",
            30: "monthly_sensitivity",
        }.get(int(block_length), "sensitivity")
        for scope, station_index in scopes:
            raw_estimate = compute_forecast_metrics(
                raw_p10 if station_index is None else raw_p10[:, station_index],
                p50 if station_index is None else p50[:, station_index],
                raw_p90 if station_index is None else raw_p90[:, station_index],
                y_true if station_index is None else y_true[:, station_index],
                last if station_index is None else last[:, station_index],
            )
            calibrated_estimate = compute_forecast_metrics(
                calibrated_p10
                if station_index is None
                else calibrated_p10[:, station_index],
                p50 if station_index is None else p50[:, station_index],
                calibrated_p90
                if station_index is None
                else calibrated_p90[:, station_index],
                y_true if station_index is None else y_true[:, station_index],
                last if station_index is None else last[:, station_index],
            )
            metric_specs = [
                ("not_applicable", metric_name, raw_estimate[metric_name])
                for metric_name in POINT_BOOTSTRAP_METRICS
            ]
            metric_specs.extend(
                ("raw", metric_name, raw_estimate[metric_name])
                for metric_name in INTERVAL_BOOTSTRAP_METRICS
            )
            metric_specs.extend(
                ("calibrated", metric_name, calibrated_estimate[metric_name])
                for metric_name in INTERVAL_BOOTSTRAP_METRICS
            )
            calibration_comparison_estimate = {
                metric_name: calibrated_estimate[metric_name]
                - raw_estimate[metric_name]
                for metric_name in (
                    "coverage",
                    "mean_width",
                    "mean_pinball",
                    "interval_score_80",
                )
            }
            calibration_comparison_estimate["absolute_coverage_gap"] = (
                abs(calibrated_estimate["coverage"] - target_coverage)
                - abs(raw_estimate["coverage"] - target_coverage)
            )
            metric_specs.extend(
                (
                    "calibrated_minus_raw",
                    metric_name,
                    calibration_comparison_estimate[metric_name],
                )
                for metric_name in CALIBRATION_COMPARISON_BOOTSTRAP_METRICS
            )
            for variant, metric_name, estimate in metric_specs:
                lower, upper = percentile_interval(
                    samples[(scope, variant, metric_name)],
                    confidence_level,
                )
                rows.append({
                    "scope": scope,
                    "interval_variant": variant,
                    "metric": metric_name,
                    "estimate": estimate,
                    "ci_lower": lower,
                    "ci_upper": upper,
                    "confidence_level": confidence_level,
                    "target_coverage": target_coverage,
                    "method": "overlapping_moving_block_bootstrap_percentile",
                    "resampling_unit": "date_block_all_stations_paired",
                    "block_length_days": int(block_length),
                    "block_length_basis": block_basis,
                    "primary_block_length": (
                        int(block_length) == int(primary_block_length)
                    ),
                    "resamples": int(resamples),
                    "bootstrap_seed_base": int(seed),
                    "effective_seed": effective_seed,
                    "n_dates": len(dates),
                    "n_stations": (
                        len(station_names) if station_index is None else 1
                    ),
                    "test_start_date": dates[0].date().isoformat(),
                    "test_end_date": dates[-1].date().isoformat(),
                    "model_refit_each_resample": False,
                    "qhat_reestimated_each_resample": False,
                    "stationarity_note": (
                        "local_stationarity_required_but_test_drift_detected"
                    ),
                })
    return rows


def make_model_inputs(df, disp, stats_stop, interp):
    """Build input channels from displacement grids and broadcast exogenous drivers."""
    disp_mu = disp[:stats_stop].mean(0)
    disp_sigma = np.maximum(disp[:stats_stop].std(0), 1.0)
    disp_norm = (disp - disp_mu) / disp_sigma
    disp_grid = interp(disp_norm).astype(np.float32)[:, None, :, :]

    exog = df[EXOG_COLS].values.astype(np.float64)
    exog_mu = exog[:stats_stop].mean(0)
    exog_sigma = np.maximum(exog[:stats_stop].std(0), 1e-6)
    exog_norm = ((exog - exog_mu) / exog_sigma).astype(np.float32)
    exog_grid = np.broadcast_to(
        exog_norm[:, :, None, None],
        (len(df), len(EXOG_COLS), GRID_H, GRID_W),
    )
    inputs = np.concatenate([disp_grid, exog_grid], axis=1).astype(np.float32)
    return inputs, disp_sigma


def main():
    df = pd.read_csv(FEAT_CSV)
    disp = df[DISP_COLS].values.astype(np.float64)

    names, xy = load_coords(DISP_COLS)
    interp, (gx, gy) = make_interpolator(xy, GRID_H, GRID_W)

    split = int(len(disp) * TRAIN_FRAC)
    n_train_windows = split - LOOKBACK - HORIZON + 1
    n_fit, n_cal = chronological_fit_calibration_split(
        n_train_windows,
        CAL_FRAC,
    )
    if n_cal == 0:
        raise RuntimeError("独立区间校准要求至少一个校准窗口")
    fit_stats_stop = n_fit + LOOKBACK + HORIZON - 1
    inputs, _ = make_model_inputs(df, disp, fit_stats_stop, interp)
    readout_w = station_readout_weights(gx, gy, xy)

    Xtr, _, _ = make_windows(inputs[:split], LOOKBACK, HORIZON)
    ytr_future, ytr_last, ytr_delta = make_station_windows(
        disp, split=LOOKBACK, lookback=LOOKBACK, horizon=HORIZON, stop=split
    )
    Xte, _, _ = make_windows(inputs[split - LOOKBACK:], LOOKBACK, HORIZON)
    yte_real, last_te, _ = make_station_windows(
        disp, split=split, lookback=LOOKBACK, horizon=HORIZON
    )
    if len(Xtr) != len(ytr_delta) or len(Xte) != len(yte_real):
        raise RuntimeError("输入窗口和真实测点目标数量不一致")

    if len(Xtr) != n_train_windows:
        raise RuntimeError("训练窗口数量与切分计划不一致")

    delta_scale = make_delta_scale(ytr_delta[:n_fit])
    ytr_norm = (ytr_delta / delta_scale).astype(np.float32)
    device = require_cuda_device()
    Xtr_t = torch.from_numpy(Xtr).to(device)
    ytr_t = torch.from_numpy(ytr_norm).to(device)
    Xte_t = torch.from_numpy(Xte).to(device)
    readout_t = torch.from_numpy(readout_w).to(device)

    Xfit_t, yfit_t = Xtr_t[:n_fit], ytr_t[:n_fit]

    model = ForecastModel(
        in_ch=Xtr_t.shape[2],
        hid_ch=HIDDEN,
        kernel=KERNEL,
        quantiles=QUANTILES,
    ).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=LR)

    for ep in range(EPOCHS):
        model.train()
        opt.zero_grad()
        pred_grid = model(Xfit_t)
        pred_station = readout_grid_at_stations(pred_grid, readout_t)
        loss = pinball_loss(pred_station, yfit_t, QUANTILES)
        loss.backward()
        opt.step()
        if (ep + 1) % 20 == 0:
            print(f"[cnn-mamba] epoch {ep+1}/{EPOCHS} pinball={loss.item():.4f}")

    model.eval()
    qi = {q: i for i, q in enumerate(QUANTILES)}
    Xcal_t = Xtr_t[n_fit:]
    ycal_real = ytr_future[n_fit:]
    last_cal = ytr_last[n_fit:]
    with torch.no_grad():
        cal_grid = model(Xcal_t)
        cal_norm = readout_grid_at_stations(cal_grid, readout_t).cpu().numpy()
    cal_pred = last_cal[:, None, :] + cal_norm * delta_scale[None, None, :]
    cal_p10, cal_p50, cal_p90 = (
        cal_pred[:, qi[0.1]],
        cal_pred[:, qi[0.5]],
        cal_pred[:, qi[0.9]],
    )
    cal_p10_adjusted, cal_p90_adjusted, qhat = calibrate_intervals(
        cal_p10,
        cal_p90,
        ycal_real,
        target_coverage=TARGET_COVERAGE,
    )

    with torch.no_grad():
        dgrid = model(Xte_t)
        dpred_norm = readout_grid_at_stations(dgrid, readout_t).cpu().numpy()

    pred = last_te[:, None, :] + dpred_norm * delta_scale[None, None, :]

    raw_p10, p50, raw_p90 = (
        pred[:, qi[0.1]],
        pred[:, qi[0.5]],
        pred[:, qi[0.9]],
    )
    raw_metrics = compute_forecast_metrics(
        raw_p10,
        p50,
        raw_p90,
        yte_real,
        last_te,
    )
    p10 = raw_p10 - qhat[None, :]
    p90 = raw_p90 + qhat[None, :]
    metrics = compute_forecast_metrics(p10, p50, p90, yte_real, last_te)
    station_names = [c.replace("_disp", "") for c in DISP_COLS]
    calibration_raw_rows = station_metric_rows(
        cal_p10,
        cal_p50,
        cal_p90,
        ycal_real,
        last_cal,
        station_names,
        THESIS_WINDOWS,
        interval_variant="raw",
    )
    calibration_adjusted_rows = station_metric_rows(
        cal_p10_adjusted,
        cal_p50,
        cal_p90_adjusted,
        ycal_real,
        last_cal,
        station_names,
        THESIS_WINDOWS,
        interval_variant="calibrated",
    )
    raw_rows = station_metric_rows(
        raw_p10,
        p50,
        raw_p90,
        yte_real,
        last_te,
        station_names,
        THESIS_WINDOWS,
        interval_variant="raw",
    )
    calibrated_rows = station_metric_rows(
        p10,
        p50,
        p90,
        yte_real,
        last_te,
        station_names,
        THESIS_WINDOWS,
        interval_variant="calibrated",
    )
    rows = raw_rows + calibrated_rows

    dates = pd.to_datetime(df["Date"])
    first_train_target = LOOKBACK + HORIZON - 1
    fit_dates = dates.iloc[first_train_target:first_train_target + n_fit]
    calibration_dates = dates.iloc[first_train_target + n_fit:split]
    test_dates = dates.iloc[split + HORIZON - 1:].reset_index(drop=True)
    split_metadata = {
        "fit_start_date": fit_dates.iloc[0].date().isoformat(),
        "fit_end_date": fit_dates.iloc[-1].date().isoformat(),
        "fit_windows": n_fit,
        "calibration_start_date": calibration_dates.iloc[0].date().isoformat(),
        "calibration_end_date": calibration_dates.iloc[-1].date().isoformat(),
        "calibration_windows": n_cal,
        "test_start_date": test_dates.iloc[0].date().isoformat(),
        "test_end_date": test_dates.iloc[-1].date().isoformat(),
        "test_windows": len(test_dates),
        "target_coverage": TARGET_COVERAGE,
        "method": "stationwise_symmetric_split_conformal",
    }
    calibration_rows = calibration_metric_rows(
        station_names,
        qhat,
        calibration_raw_rows,
        calibration_adjusted_rows,
        raw_rows,
        calibrated_rows,
        split_metadata,
    )
    raw_period_rows = period_metric_rows(
        raw_p10,
        p50,
        raw_p90,
        yte_real,
        last_te,
        test_dates,
        interval_variant="raw",
    )
    calibrated_period_rows = period_metric_rows(
        p10,
        p50,
        p90,
        yte_real,
        last_te,
        test_dates,
        interval_variant="calibrated",
    )
    period_rows = raw_period_rows + calibrated_period_rows
    bootstrap_rows = bootstrap_ci_rows(
        raw_p10,
        p50,
        raw_p90,
        p10,
        p90,
        yte_real,
        last_te,
        station_names,
        test_dates,
    )

    OUT_PT.parent.mkdir(parents=True, exist_ok=True)
    torch.save({
        "state_dict": {
            key: value.detach().cpu()
            for key, value in model.state_dict().items()
        },
        "device": str(device),
        "config": {
            "in_ch": int(Xtr_t.shape[2]),
            "model_name": MODEL_NAME,
            "hidden": HIDDEN,
            "kernel": KERNEL,
            "mamba_state_dim": MAMBA_STATE_DIM,
            "mamba_conv": MAMBA_CONV,
            "mamba_expand": MAMBA_EXPAND,
            "quantiles": QUANTILES,
            "lookback": LOOKBACK,
            "horizon": HORIZON,
            "exog_cols": EXOG_COLS,
            "grid_h": GRID_H,
            "grid_w": GRID_W,
            "train_fraction": TRAIN_FRAC,
            "calibration_fraction_within_train": CAL_FRAC,
            "target_coverage": TARGET_COVERAGE,
            "calibration_method": "stationwise_symmetric_split_conformal",
            "split_metadata": split_metadata,
            "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
            "bootstrap_block_lengths": BOOTSTRAP_BLOCK_LENGTHS,
            "bootstrap_primary_block_length": BOOTSTRAP_PRIMARY_BLOCK_LENGTH,
            "bootstrap_confidence_level": BOOTSTRAP_CONFIDENCE_LEVEL,
            "bootstrap_seed": BOOTSTRAP_SEED,
        },
        "delta_scale": delta_scale,
        "readout_weights": readout_w,
        "calibration_q": qhat,
    }, OUT_PT)

    OUT_PNG.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(len(PLOT_STATIONS), 1, figsize=(11, 9), sharex=True)
    for ax, station in zip(axes, PLOT_STATIONS):
        ch = DISP_COLS.index(f"{station}_disp")
        ax.plot(yte_real[:, ch], label="actual", color="k", lw=1)
        ax.plot(last_te[:, ch], label="persistence", color="0.55", lw=1, ls="--")
        ax.plot(p50[:, ch], label="P50", color="C1", lw=1)
        ax.fill_between(range(len(p50)), p10[:, ch], p90[:, ch],
                        alpha=0.25, color="C1", label="calibrated P10-P90")
        ax.set_title(f"{station} forecast interval")
        ax.set_ylabel("mm")
    axes[-1].set_xlabel("test time step")
    axes[0].legend(loc="upper left")
    fig.suptitle(f"CNN-Mamba forecast intervals ({GRID_H}x{GRID_W}, lookback={LOOKBACK}d, horizon={HORIZON}d)")
    plt.tight_layout()
    plt.savefig(OUT_PNG, dpi=150)
    plt.close()

    pd.DataFrame(rows).to_csv(OUT_METRICS, index=False)
    pd.DataFrame(period_rows).to_csv(OUT_PERIOD_METRICS, index=False)
    pd.DataFrame(calibration_rows).to_csv(OUT_CALIBRATION_METRICS, index=False)
    pd.DataFrame(bootstrap_rows).to_csv(OUT_BOOTSTRAP_CI, index=False)

    print(f"[cnn-mamba] 模型: {OUT_PT}")
    print(f"[cnn-mamba] 区间图: {OUT_PNG}")
    print(f"[cnn-mamba] 测点指标: {OUT_METRICS}")
    print(f"[cnn-mamba] 分时段指标: {OUT_PERIOD_METRICS}")
    print(f"[cnn-mamba] 校准审计: {OUT_CALIBRATION_METRICS}")
    print(f"[cnn-mamba] 时间块置信区间: {OUT_BOOTSTRAP_CI}")
    print(f"[cnn-mamba] 网格: {GRID_H}x{GRID_W}  测点数: {len(names)}")
    print(f"[cnn-mamba] 论文窗口: {THESIS_WINDOWS}; 当前统一输入窗口: {LOOKBACK} 天")
    print(f"[cnn-mamba] 输入通道: 位移网格 + {EXOG_COLS}")
    print(
        "[cnn-mamba] fit/calibration/test 窗口: "
        f"{n_fit}/{n_cal}/{len(test_dates)}"
    )
    print(f"[cnn-mamba] 测试集 RMSE(P50, 全测点): {metrics['model_rmse']:.3f} mm")
    print(f"[cnn-mamba] persistence 基线 RMSE: {metrics['baseline_rmse']:.3f} mm")
    print(f"[cnn-mamba] RMSE skill vs baseline: {metrics['rmse_skill_vs_baseline']:.3f}")
    print(f"[cnn-mamba] 测试集 MAE(P50, 全测点): {metrics['model_mae']:.3f} mm")
    print(f"[cnn-mamba] persistence 基线 MAE: {metrics['baseline_mae']:.3f} mm")
    print(f"[cnn-mamba] 原始区间覆盖率(P10-P90): {raw_metrics['coverage']:.3f}")
    qhat_text = ", ".join(
        f"{station}={value:.3f}" for station, value in zip(station_names, qhat)
    )
    print(f"[cnn-mamba] stationwise conformal qhat: {qhat_text} mm")
    print(f"[cnn-mamba] 校准后区间覆盖率(P10-P90): {metrics['coverage']:.3f}  (目标≈0.80)")
    print(
        "[cnn-mamba] 平均区间宽度 raw/calibrated: "
        f"{raw_metrics['mean_width']:.3f}/{metrics['mean_width']:.3f} mm"
    )
    print(
        "[cnn-mamba] 平均 pinball loss raw/calibrated: "
        f"{raw_metrics['mean_pinball']:.4f}/{metrics['mean_pinball']:.4f} mm"
    )
    print(
        "[cnn-mamba] 80% interval score raw/calibrated: "
        f"{raw_metrics['interval_score_80']:.3f}/"
        f"{metrics['interval_score_80']:.3f} mm"
    )
    print(f"[cnn-mamba] 分位数交叉: P10>P50 {metrics['p10_gt_p50']}, "
          f"P50>P90 {metrics['p50_gt_p90']} / {metrics['total_points']}")


if __name__ == "__main__":
    main()
