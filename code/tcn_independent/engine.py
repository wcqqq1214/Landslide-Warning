"""Forecast from a supplied observation prefix and a fixed physical scenario."""

import json
from datetime import datetime, timezone

import numpy as np
import torch

from rolling_probability.data import Teacher, example
from short_horizon.common import ROOT, sha, array_sha
from short_horizon.models import RidgeMean
from tcn_short_horizon.run import load_group


def read_spec(path):
    spec = json.loads((ROOT / path).read_text())
    for name, digest in spec["source_sha256"].items():
        if sha(ROOT / name) != digest:
            raise ValueError("Frozen source changed: " + name)
    return spec


def guard(spec):
    if datetime.now(timezone.utc) >= datetime.fromisoformat(spec["deadline_utc"]):
        raise TimeoutError("Independent experiment deadline reached")


def features(history, teacher):
    x, z, means = example(history, teacher, horizon=7, window=30)
    if z.shape != (7, 8, 4):
        raise ValueError("A complete seven-day decoder block is required")
    return dict(
        x=x[None],
        z=z[None],
        anchor=means["B_ANCHOR"][None],
        last_y=history[-1][None],
        origins=np.asarray([len(history)]),
    )


def rollout(history, teacher, length, predictor, record=False):
    """The predictor cannot access future observations; append its own outputs only."""
    history = np.asarray(history, float).copy()
    origin = len(history)
    prediction = np.full((length, 4), np.nan)
    trace = []
    for offset in range(0, length, 7):
        k = min(7, length - offset)
        data = features(history, teacher)
        mu = np.asarray(predictor(data), float)
        if mu.shape != (7, 4):
            raise ValueError("Decoder shape changed")
        entry = dict(offset=offset, model_origin=len(history), observed_prefix=origin)
        if record:
            entry.update(history_sha256=array_sha(history), output_sha256=array_sha(mu))
        if not np.isfinite(mu).all():
            entry["status"] = "nonfinite_output_remaining_forecast_missing"
            trace.append(entry)
            break
        prediction[offset : offset + k] = mu[:k]
        history = np.concatenate([history, mu[:k]])
        if record:
            entry["status"] = "finite"
            trace.append(entry)
    return prediction, trace


class Learners:
    def __init__(self, spec):
        self.spec = spec
        tcn_spec = json.loads((ROOT / spec["tcn_config"]).read_text())
        self.groups, self.ridge = {}, {}
        for prefix, phase in spec["model_prefixes"].items():
            q = int(prefix)
            self.groups[q] = {}
            for name in ("TCN_DIRECT", "TCN_BRES"):
                path = ROOT / spec["tcn_root"] / phase / "training" / name
                self.groups[q][name] = load_group(path, tcn_spec, spec["steps"])
            self.ridge[q] = RidgeMean(
                json.loads(
                    (
                        ROOT / spec["legacy_root"] / phase / "RR_DIRECT_model.json"
                    ).read_text()
                )
            )

    @torch.no_grad()
    def block(self, q, name, seed, data):
        if name == "RR_DIRECT":
            return self.ridge[q].predict(data)[0]
        models, scale = self.groups[q][name]
        return models[seed](scale.tensors(data), scale, name)[0].numpy()[0]


def forecast_origin(spec, y_available, bank, learners, origin, length, record=False):
    guard(spec)
    if len(y_available) < origin:
        raise ValueError("Observation prefix incomplete")
    past = y_available[:origin].copy()
    padded = 7 * ((length + 6) // 7)
    q, forcing, b, states, _, difference = bank.forecast(origin, padded)
    if q != max(p for p in learners.groups if p <= origin):
        raise ValueError("Physical and neural prefix contracts differ")
    teacher = Teacher(
        q, b, forcing, states["moisture"], states["rain_head"], states["reservoir_head"]
    )
    h = np.arange(1, length + 1)[:, None]
    means = dict(
        B_ANCHOR=past[-1] + b[origin : origin + length] - b[origin - 1],
        DRIFT1=past[-1] + h * (past[-1] - past[-2]),
    )
    seeds, traces = {}, {}
    for name in ("RR_DIRECT", "TCN_DIRECT", "TCN_BRES"):
        runs, details = [], []
        for seed in range(1) if name == "RR_DIRECT" else spec["seeds"]:
            mu, tr = rollout(
                past,
                teacher,
                length,
                lambda d, n=name, s=seed: learners.block(q, n, s, d),
                record,
            )
            runs.append(mu)
            details.append(tr)
        runs = np.asarray(runs)
        means[name] = runs.mean(axis=0)
        seeds[name] = runs
        if record:
            traces[name] = details
    audit = dict(
        origin=origin,
        forecast_length=length,
        model_prefix=q,
        physical_prefix=q,
        historical_state_difference=difference,
        future_rainfall_mm=float(forcing[origin, 0]),
        future_rwl_m=float(forcing[origin, 1]),
        observation_prefix_sha256=array_sha(past),
        forcing_prefix_sha256=array_sha(forcing[:origin]),
        future_forcing_constant=bool(np.all(forcing[origin:] == forcing[origin])),
        traces=traces,
        new_fits=0,
        new_optimizer_updates=0,
    )
    return means, seeds, audit


def calibrate(origins, forecasts, labels, start, length=293, window=90):
    """Each lead has its own last 90 fully matured independent-forecast errors."""
    sigma = {name: np.full((length, 4), np.nan) for name in forecasts}
    pools = np.empty((length, window), int)
    for k in range(length):
        legal = np.flatnonzero(origins + k < start)
        ids = legal[-window:]
        if len(ids) != window:
            raise ValueError("Insufficient mature historical forecasts")
        pools[k] = origins[ids]
        if not np.array_equal(pools[k], np.arange(start - k - window, start - k)):
            raise ValueError("Calibration origins are not the fixed latest 90")
        for name, means in forecasts.items():
            error = labels[origins[ids] + k] - means[ids, k]
            # Missing/failed runs are retained. Never replace them by finite subsets.
            sigma[name][k] = np.maximum(np.sqrt(np.mean(error**2, axis=0)), 1e-6)
    return sigma, pools
