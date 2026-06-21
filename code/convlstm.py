"""ConvLSTM displacement-interval forecast with station-level evaluation."""
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

sys.path.append(str(Path(__file__).resolve().parent))
from grid_interp import load_coords, make_interpolator, GRID_H, GRID_W

ROOT = Path(__file__).resolve().parent.parent
FEAT_CSV = ROOT / "data" / "features.csv"
OUT_PT = ROOT / "models" / "convlstm.pt"
FIG_DIR = ROOT / "figures" / "convlstm"
OUT_PNG = FIG_DIR / "forecast_interval.png"
OUT_METRICS = FIG_DIR / "forecast_metrics.csv"
OUT_PERIOD_METRICS = FIG_DIR / "forecast_period_metrics.csv"
OUT_CALIBRATION_METRICS = FIG_DIR / "forecast_calibration_metrics.csv"

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
EPOCHS = 120
LR = 1e-3
SEED = 0

torch.manual_seed(SEED)
np.random.seed(SEED)


class ConvLSTMCell(nn.Module):
    """ConvLSTM cell with 2D-convolution gates."""
    def __init__(self, in_ch, hid_ch, kernel):
        super().__init__()
        pad = kernel // 2
        self.hid_ch = hid_ch
        self.conv = nn.Conv2d(in_ch + hid_ch, 4 * hid_ch, kernel, padding=pad)

    def forward(self, x, h, c):
        z = self.conv(torch.cat([x, h], dim=1))
        i, f, o, g = torch.chunk(z, 4, dim=1)
        i, f, o = torch.sigmoid(i), torch.sigmoid(f), torch.sigmoid(o)
        g = torch.tanh(g)
        c = f * c + i * g
        h = o * torch.tanh(c)
        return h, c


class ConvLSTMForecast(nn.Module):
    """ConvLSTM 编码多通道时序网格 -> 有序 P10/P50/P90 增量图。"""
    def __init__(self, hid_ch, kernel, n_q=None, in_ch=1, quantiles=None):
        super().__init__()
        self.quantiles = list(QUANTILES if quantiles is None else quantiles)
        self.n_q = len(self.quantiles) if n_q is None else n_q
        if self.n_q != 3 or self.quantiles != [0.1, 0.5, 0.9]:
            raise ValueError("当前单调输出头只支持 P10/P50/P90 三个分位数")
        self.cell = ConvLSTMCell(in_ch, hid_ch, kernel)
        self.head = nn.Conv2d(hid_ch, 3, 1)

    def forward(self, x):
        if x.ndim == 4:
            x = x.unsqueeze(2)
        B, T, C, H, W = x.shape
        h = x.new_zeros(B, self.cell.hid_ch, H, W)
        c = x.new_zeros(B, self.cell.hid_ch, H, W)
        for t in range(T):
            h, c = self.cell(x[:, t], h, c)
        raw = self.head(h)
        return ordered_quantiles_from_raw(raw)


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
    Xtr_t = torch.from_numpy(Xtr)
    ytr_t = torch.from_numpy(ytr_norm)
    Xte_t = torch.from_numpy(Xte)
    readout_t = torch.from_numpy(readout_w)

    Xfit_t, yfit_t = Xtr_t[:n_fit], ytr_t[:n_fit]

    model = ConvLSTMForecast(
        in_ch=Xtr_t.shape[2],
        hid_ch=HIDDEN,
        kernel=KERNEL,
        quantiles=QUANTILES,
    )
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
            print(f"[convlstm] epoch {ep+1}/{EPOCHS} pinball={loss.item():.4f}")

    model.eval()
    qi = {q: i for i, q in enumerate(QUANTILES)}
    Xcal_t = Xtr_t[n_fit:]
    ycal_real = ytr_future[n_fit:]
    last_cal = ytr_last[n_fit:]
    with torch.no_grad():
        cal_grid = model(Xcal_t)
        cal_norm = readout_grid_at_stations(cal_grid, readout_t).numpy()
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
        dpred_norm = readout_grid_at_stations(dgrid, readout_t).numpy()

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

    OUT_PT.parent.mkdir(parents=True, exist_ok=True)
    torch.save({
        "state_dict": model.state_dict(),
        "config": {
            "in_ch": int(Xtr_t.shape[2]),
            "hidden": HIDDEN,
            "kernel": KERNEL,
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
    fig.suptitle(f"ConvLSTM forecast intervals ({GRID_H}x{GRID_W}, lookback={LOOKBACK}d, horizon={HORIZON}d)")
    plt.tight_layout()
    plt.savefig(OUT_PNG, dpi=150)
    plt.close()

    pd.DataFrame(rows).to_csv(OUT_METRICS, index=False)
    pd.DataFrame(period_rows).to_csv(OUT_PERIOD_METRICS, index=False)
    pd.DataFrame(calibration_rows).to_csv(OUT_CALIBRATION_METRICS, index=False)

    print(f"[convlstm] 模型: {OUT_PT}")
    print(f"[convlstm] 区间图: {OUT_PNG}")
    print(f"[convlstm] 测点指标: {OUT_METRICS}")
    print(f"[convlstm] 分时段指标: {OUT_PERIOD_METRICS}")
    print(f"[convlstm] 校准审计: {OUT_CALIBRATION_METRICS}")
    print(f"[convlstm] 网格: {GRID_H}x{GRID_W}  测点数: {len(names)}")
    print(f"[convlstm] 论文窗口: {THESIS_WINDOWS}; 当前统一输入窗口: {LOOKBACK} 天")
    print(f"[convlstm] 输入通道: 位移网格 + {EXOG_COLS}")
    print(
        "[convlstm] fit/calibration/test 窗口: "
        f"{n_fit}/{n_cal}/{len(test_dates)}"
    )
    print(f"[convlstm] 测试集 RMSE(P50, 全测点): {metrics['model_rmse']:.3f} mm")
    print(f"[convlstm] persistence 基线 RMSE: {metrics['baseline_rmse']:.3f} mm")
    print(f"[convlstm] RMSE skill vs baseline: {metrics['rmse_skill_vs_baseline']:.3f}")
    print(f"[convlstm] 测试集 MAE(P50, 全测点): {metrics['model_mae']:.3f} mm")
    print(f"[convlstm] persistence 基线 MAE: {metrics['baseline_mae']:.3f} mm")
    print(f"[convlstm] 原始区间覆盖率(P10-P90): {raw_metrics['coverage']:.3f}")
    qhat_text = ", ".join(
        f"{station}={value:.3f}" for station, value in zip(station_names, qhat)
    )
    print(f"[convlstm] stationwise conformal qhat: {qhat_text} mm")
    print(f"[convlstm] 校准后区间覆盖率(P10-P90): {metrics['coverage']:.3f}  (目标≈0.80)")
    print(
        "[convlstm] 平均区间宽度 raw/calibrated: "
        f"{raw_metrics['mean_width']:.3f}/{metrics['mean_width']:.3f} mm"
    )
    print(
        "[convlstm] 平均 pinball loss raw/calibrated: "
        f"{raw_metrics['mean_pinball']:.4f}/{metrics['mean_pinball']:.4f} mm"
    )
    print(
        "[convlstm] 80% interval score raw/calibrated: "
        f"{raw_metrics['interval_score_80']:.3f}/"
        f"{metrics['interval_score_80']:.3f} mm"
    )
    print(f"[convlstm] 分位数交叉: P10>P50 {metrics['p10_gt_p50']}, "
          f"P50>P90 {metrics['p50_gt_p90']} / {metrics['total_points']}")


if __name__ == "__main__":
    main()
