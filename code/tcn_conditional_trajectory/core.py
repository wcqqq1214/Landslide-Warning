"""Frozen data boundaries, continuous B+ features, paired TCN, and scoring."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import warnings

import numpy as np
import pandas as pd
import torch
from torch import nn

from physics_guided.reference import load as load_reference
from rolling_probability.scoring import crps, interval
from tcn_short_horizon.models import ResidualBlock

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config/ootang_tcn_conditional_trajectory.v1_0.json"
POINTS = ("ATU1", "ATU5", "MJ3", "MJ1")
ARMS = ("TCN_DIRECT_COND", "TCN_BRES_COND")


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def array_sha(value):
    return hashlib.sha256(
        np.ascontiguousarray(value, dtype=np.float64).tobytes()
    ).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )


def read_json(path):
    return json.loads(Path(path).read_text())


def spec():
    return read_json(CONFIG)


def guard_sources():
    sources = read_json(ROOT / "docs/ootang_tcn_conditional_sources.v1.0.json")["files"]
    for path, digest in sources.items():
        if sha(ROOT / path) != digest:
            raise ValueError("Frozen source changed: " + path)
    return len(sources)


def check_deadline(cfg):
    if datetime.now(timezone.utc) >= datetime.fromisoformat(cfg["deadline_utc"]):
        raise TimeoutError("Frozen execution deadline reached")


def read_forcing(path, end):
    frame = pd.read_csv(path, nrows=end, usecols=["Date", "Rainfall/mm", "RWL/m"])
    dates = pd.DatetimeIndex(pd.to_datetime(frame.Date, errors="raise"))
    if not dates.equals(pd.date_range("2016-07-01", periods=end)):
        raise ValueError("Invalid complete daily calendar")
    forcing = frame[["Rainfall/mm", "RWL/m"]].to_numpy(np.float64)
    if not np.isfinite(forcing).all() or (forcing[:, 0] < 0).any():
        raise ValueError("Invalid given forcing")
    if ((forcing[:, 1] < 130) | (forcing[:, 1] > 190)).any():
        raise ValueError("Reservoir level outside original lookup range")
    return forcing, dates.strftime("%Y-%m-%d").to_numpy(dtype="U10")


def read_labels(path, end):
    if not isinstance(end, int) or not 1 <= end <= 1461:
        raise ValueError("Explicit label prefix required")
    frame = pd.read_csv(path, nrows=end, usecols=[p + "/mm" for p in POINTS])
    y = frame[[p + "/mm" for p in POINTS]].to_numpy(np.float64)
    if y.shape != (end, 4) or not np.isfinite(y).all():
        raise ValueError("Invalid label prefix")
    return y


def feature_matrix(mean, forcing, states, y0):
    db = np.diff(mean, axis=0, prepend=mean[:1])
    dr = np.diff(forcing[:, 1], prepend=forcing[0, 1])
    total = np.r_[0.0, np.cumsum(forcing[:, 0])]
    ends = np.arange(1, len(mean) + 1)
    rain = [total[ends] - total[np.maximum(0, ends - k)] for k in (7, 30)]
    x = np.column_stack(
        [
            mean - y0,
            db,
            forcing,
            dr,
            *rain,
            states["moisture"],
            states["rain_head"],
            states["reservoir_head"],
        ]
    )
    if x.shape != (len(mean), 22) or not np.isfinite(x).all():
        raise ValueError("Expected finite 22-channel features")
    return np.ascontiguousarray(x)


def physical_trajectory(cfg, prefix, forcing, y0):
    ref = load_reference(ROOT / cfg["runtime"])
    source = ROOT / cfg["teacher_sources"][str(prefix)]
    if source.suffix == ".npz":
        with np.load(source) as a:
            theta = a["theta"].copy()
    else:
        theta = np.asarray(read_json(source)["theta"], np.float64)
    if theta.shape != (54,) or not np.isfinite(theta).all():
        raise ValueError("Invalid fixed B+ parameter vector")
    with warnings.catch_warnings(record=True) as log:
        warnings.simplefilter("always")
        mean, states = ref.forward(
            theta, ref.Context(forcing), substeps=64, states=True
        )
    mean = mean + y0
    return dict(
        mean=mean,
        theta=theta,
        forcing=forcing.copy(),
        y0=y0.copy(),
        x=feature_matrix(mean, forcing, states, y0),
        **states,
    ), [str(x.message) for x in log]


def load_npz(path):
    with np.load(path, allow_pickle=False) as a:
        return {k: a[k].copy() for k in a.files}


class Scaling:
    def __init__(self, x=None, labels=None, state=None, cfg=None):
        if state is None:
            if len(x) != len(labels):
                raise ValueError("Scaling may only receive the training prefix")
            c = cfg["normalization"]
            state = dict(
                x_mean=x.mean(0).tolist(),
                x_std=np.maximum(x.std(0, ddof=0), c["x_std_floor"]).tolist(),
                unit=np.maximum(
                    (labels - labels[0]).std(0, ddof=0), c["target_std_floor_mm"]
                ).tolist(),
                y0=labels[0].tolist(),
                training_rows=len(labels),
            )
        self.state = state
        self.x_mean, self.x_std = np.array(state["x_mean"]), np.array(state["x_std"])
        self.unit, self.y0 = np.array(state["unit"]), np.array(state["y0"])

    def tensor(self, x):
        return torch.tensor(
            (x - self.x_mean) / self.x_std, dtype=torch.float64
        ).T.unsqueeze(0)


class TrajectoryTCN(nn.Module):
    def __init__(self, cfg, seed):
        super().__init__()
        torch.manual_seed(seed)
        n = cfg["neural"]
        incoming, blocks = n["input_channels"], []
        for dilation in n["dilations"]:
            blocks.append(ResidualBlock(incoming, n["hidden"], n["kernel"], dilation))
            incoming = n["hidden"]
        self.encoder = nn.Sequential(*blocks)
        self.head = nn.Conv1d(incoming, 4, 1)
        nn.init.zeros_(self.head.weight)
        nn.init.zeros_(self.head.bias)
        self.double()

    def forward(self, x):
        return self.head(self.encoder(x))[0].T


def baseline(arm, physical, scale):
    if arm == ARMS[0]:
        return np.broadcast_to(scale.y0, physical.shape).copy()
    if arm == ARMS[1]:
        return physical
    raise ValueError("Unknown paired arm")


def predict(model, scale, arm, x, physical):
    model.eval()
    with torch.no_grad():
        residual = model(scale.tensor(x)).numpy()
    result = baseline(arm, physical, scale) + residual * scale.unit
    if not np.isfinite(result).all():
        raise ArithmeticError("Nonfinite complete neural trajectory")
    return result


def reload_model(path, cfg):
    saved = torch.load(path, map_location="cpu", weights_only=True)
    model = TrajectoryTCN(cfg, saved["seed"])
    model.load_state_dict(saved["state_dict"], strict=True)
    return model, Scaling(state=saved["scaling"]), saved


def fit_ridge(x, labels, scale, alpha):
    z = (x - scale.x_mean) / scale.x_std
    target = (labels - scale.y0) / scale.unit
    xbar, ybar = z.mean(0), target.mean(0)
    centered = z - xbar
    # Explicit contraction avoids the previously recorded platform matmul warning.
    gram = np.einsum("ni,nj->ij", centered, centered, optimize=False)
    rhs = np.einsum("ni,nj->ij", centered, target - ybar, optimize=False)
    coef = np.linalg.solve(gram + alpha * np.eye(x.shape[1]), rhs)
    intercept = ybar - np.einsum("i,ij->j", xbar, coef, optimize=False)
    return dict(
        coef=coef,
        intercept=intercept,
        x_mean=scale.x_mean,
        x_std=scale.x_std,
        unit=scale.unit,
        y0=scale.y0,
    )


def ridge_predict(model, x):
    z = (x - model["x_mean"]) / model["x_std"]
    r = np.einsum("ni,ij->nj", z, model["coef"], optimize=False) + model["intercept"]
    return model["y0"] + model["unit"] * r


def drift(labels, horizon):
    return labels[-1] + np.arange(1, horizon + 1)[:, None] * (labels[-1] - labels[-2])


def calibrate(errors, cfg):
    if errors.shape != (90, 4) or not np.isfinite(errors).all():
        raise ValueError("Calibration requires exactly 90 mature four-point errors")
    return np.maximum(
        np.sqrt(np.mean(errors**2, axis=0)), cfg["calibration"]["sigma_floor_mm"]
    )


def scores(y, mean, sigma=None):
    if y.shape != mean.shape or not np.isfinite(mean).all():
        raise ValueError("A complete finite prediction must match all targets")
    err = mean - y
    rows = []
    for p, point in enumerate(POINTS):
        row = dict(
            point=point,
            n=len(y),
            mae=float(abs(err[:, p]).mean()),
            rmse=float(np.sqrt(np.mean(err[:, p] ** 2))),
        )
        if sigma is not None:
            sd = np.broadcast_to(sigma, mean.shape)[:, p]
            row["crps"] = float(crps(y[:, p], mean[:, p], sd).mean())
            for lev in (0.8, 0.9, 0.95):
                cv, wi, sc = interval(y[:, p], mean[:, p], sd, lev)
                for k, value in (
                    ("coverage", cv),
                    ("width", wi),
                    ("interval_score", sc),
                ):
                    row[k + str(round(100 * lev))] = float(value.mean())
        rows.append(row)
    return rows


def summarize(rows):
    keys = [k for k in rows[0] if k not in ("point", "n")]
    return dict(
        n_per_point=rows[0]["n"],
        **{k: float(np.mean([r[k] for r in rows])) for k in keys},
    )


def choose(summary, keys, methods, tolerance):
    remaining = list(methods)
    for key in keys:
        best = min(summary[m][key] for m in remaining)
        remaining = [m for m in remaining if summary[m][key] <= best + tolerance]
    return remaining[0]


def gates(metrics, cfg):
    b = metrics["BPLUS_CONTINUOUS"]
    bs = summarize(b)
    e = cfg["effect"]
    answer = {}
    for arm in ARMS:
        a, avg = metrics[arm], summarize(metrics[arm])
        mean = {
            f"average_{k}": avg[k] <= bs[k] * (1 - e["mean_relative_improvement"])
            for k in ("mae", "rmse")
        }
        probability = {
            f"average_{k}": avg[k]
            <= bs[k] * (1 - e["probability_relative_improvement"])
            for k in ("crps", "interval_score90")
        }
        probability["coverage90_average"] = (
            avg["coverage90"] >= e["coverage90_average_min"]
        )
        for p in range(4):
            for k in ("mae", "rmse"):
                mean[f"{POINTS[p]}_{k}"] = a[p][k] <= b[p][k] + e["point_mean_atol_mm"]
            for k in ("crps", "interval_score90"):
                probability[f"{POINTS[p]}_{k}"] = a[p][k] <= b[p][k] * (
                    1 + e["point_probability_max_regression"]
                )
            probability[f"{POINTS[p]}_coverage90"] = (
                a[p]["coverage90"] >= e["coverage90_point_min"]
            )
        answer[arm] = dict(
            mean_pass=all(mean.values()),
            probability_pass=all(probability.values()),
            joint_pass=all(mean.values()) and all(probability.values()),
            mean_checks=mean,
            probability_checks=probability,
        )
    return answer
