"""Data, original-C replay and scoring for the single v2.3 development run."""

import ctypes
from dataclasses import dataclass
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from physics_guided.data import POINTS
from physics_guided.probability import crps, interval_score, summarize
from physics_guided.reference import ROOT, load, prepare, sha

from .creep import memory_states
from .equations import Coefficients, observe, residuals
from .substep_audit import audit_trace
from .trace import compile_trace, inputs_from_saved


def tensor(value):
    return torch.as_tensor(value, dtype=torch.float64, device="cpu")


def guard_sources(spec):
    for name, digest in spec["source_sha256"].items():
        if sha(ROOT / name) != digest:
            raise ValueError(f"Frozen source changed: {name}")


def read_labels(path, days):
    frame = pd.read_csv(path, nrows=days, usecols=[p + "/mm" for p in POINTS])
    labels = frame[[p + "/mm" for p in POINTS]].to_numpy(dtype=np.float64)
    if labels.shape != (days, 4) or not np.isfinite(labels).all():
        raise ValueError("Invalid observation prefix")
    return labels


def native_replay(library, inputs):
    """Replay the hash-pinned original integrator with an explicit background.

    Unlike the historic audit wrapper, this isolated v2.3 wrapper accepts the
    complete development/final length. It never edits the original C solver.
    """
    n = len(inputs["force"])
    if not 2 <= n <= 1461:
        raise ValueError("Expected 2..1461 complete daily loads")
    arrays = {}
    for name, shape in {
        "force": (n, 4),
        "elastic": (n, 4),
        "background": (n, 4),
        "eta": (4,),
        "hardening": (4,),
        "tau_rest": (4,),
        "tau_motion": (4,),
        "kc": (4, 4),
        "ke": (4, 4),
    }.items():
        value = np.ascontiguousarray(inputs[name], dtype=np.float64)
        if value.shape != shape or not np.isfinite(value).all():
            raise ValueError(f"Invalid original-C input {name}")
        arrays[name] = value
    for name in ("eta", "hardening", "tau_rest", "tau_motion"):
        if (arrays[name] <= 0).any():
            raise ValueError(f"Nonpositive original coefficient {name}")
    tc, te = float(inputs["tau_contact"]), float(inputs["tau_bulk"])
    if not (0 < tc < math.inf and 0 < te < math.inf):
        raise ValueError("Invalid memory time constants")
    if (arrays["background"][0] != 0).any():
        raise ValueError("The original integrator requires zero initial background")
    lib = ctypes.CDLL(str(library))
    ptr = np.ctypeslib.ndpointer(dtype=np.float64, flags="C_CONTIGUOUS")
    iptr = np.ctypeslib.ndpointer(dtype=np.int32, flags="C_CONTIGUOUS")
    fn = lib.integrate_trace
    fn.argtypes = [ctypes.c_int, ctypes.c_int] + [ptr] * 7
    fn.argtypes += [ctypes.c_double, ptr, ctypes.c_double] + [ptr] * 10 + [iptr]
    fn.restype = ctypes.c_int
    daily = {
        name: np.empty((n, 4))
        for name in (
            "coordinates",
            "plastic",
            "contact",
            "bulk_reaction",
            "basal_reaction",
        )
    }
    steps = (n - 1) * 64
    recorded = {
        "previous": np.empty((steps, 24)),
        "current": np.empty((steps, 24)),
        "loads": np.empty((steps, 12)),
        "lcp": np.empty((steps, 16)),
        "masks": np.empty((steps, 2), dtype=np.int32),
    }
    args = [n, 64] + [
        arrays[k]
        for k in (
            "force",
            "elastic",
            "eta",
            "hardening",
            "tau_rest",
            "tau_motion",
            "kc",
        )
    ]
    args += [
        tc,
        arrays["ke"],
        te,
        arrays["background"],
        *daily.values(),
        *recorded.values(),
    ]
    bad = fn(*args)
    return {**recorded, **daily, "bad": np.asarray(bad)}


def replay_audit(recorded, saved, background, spec):
    same = dict(saved)
    same.update(
        {
            k: recorded[k]
            for k in (
                "coordinates",
                "plastic",
                "contact",
                "bulk_reaction",
                "basal_reaction",
            )
        }
    )
    same["background"] = np.asarray(background)
    same["mean"] = recorded["coordinates"] @ saved["observation_matrix"].T + saved["y0"]
    return audit_trace(
        recorded,
        same,
        saved["length"],
        saved["observation_matrix"],
        saved["y0"],
        spec["tolerances"],
    )


def prepare_reference(spec, out):
    """Read only forcing through n and observations through h before training."""
    guard_sources(spec)
    n, h = spec["end_days"], spec["fit_days"]
    path = ROOT / spec["input"]
    frame = pd.read_csv(path, nrows=n, usecols=["Date", "Rainfall/mm", "RWL/m"])
    dates = pd.DatetimeIndex(pd.to_datetime(frame.Date, errors="raise"))
    if not dates.equals(pd.date_range("2016-07-01", periods=n)):
        raise ValueError("Unexpected development calendar")
    forcing = frame[["Rainfall/mm", "RWL/m"]].to_numpy(dtype=np.float64)
    if not np.isfinite(forcing).all() or (forcing[:, 0] < 0).any():
        raise ValueError("Invalid forcing")
    labels = read_labels(path, h)
    source = ROOT / spec["source_run"]
    params = json.loads((source / "prefix_calibrated.json").read_text())
    package = json.loads((source / "physics_package_development.json").read_text())
    if params["physics_version"] != "prefix_792_v1_1" or h != 792 or n != 1168:
        raise ValueError(
            "This execution is restricted to the original development split"
        )
    theta = np.asarray(params["theta"], dtype=np.float64)
    if not np.array_equal(theta, package["theta"]) or not np.array_equal(
        labels[0], package["y0"]
    ):
        raise ValueError("Original prefix parameters or initial observation disagree")
    runtime = ROOT / "runtime/ootang_probability_pinn_v2_3/reference"
    if not (runtime / "provenance.json").exists():
        ref = prepare(runtime)
    else:
        provenance = json.loads((runtime / "provenance.json").read_text())
        for name, digest in provenance["source_hashes"].items():
            if sha(runtime / name) != digest:
                raise ValueError("Cached reference source changed")
        if (
            sha(runtime / "section2d_v4/physical_model_posix.py")
            != provenance["adapted_python_sha256"]
        ):
            raise ValueError("Cached reference loader changed")
        library = runtime / "section2d_v4" / Path(provenance["command"][-1]).name
        if sha(library) != provenance["library_sha256"]:
            raise ValueError("Cached reference library changed")
        ref = load(runtime)
    ctx = ref.Context(forcing)
    displacement, saved = ref.forward(theta, ctx, substeps=64, states=True)
    saved.update(
        theta=theta,
        y0=labels[0],
        mean=displacement + labels[0],
        length=ctx.length,
        observation_matrix=ctx.obs,
        forcing=forcing,
        dates=dates.strftime("%Y-%m-%d").to_numpy(dtype="U10"),
    )
    with np.load(
        source / "development/M1/selected_predictions.npz", allow_pickle=False
    ) as original:
        error = float(
            np.max(np.abs(original["means"][:, 30:] - saved["mean"][None, 30:]))
        )
    if error > spec["tolerances"]["displacement_absolute"]:
        raise ValueError(f"792-day B+ replay differs from frozen e0: {error}")
    library = compile_trace(out / "native")
    recorded = native_replay(library, inputs_from_saved(saved, saved["length"]))
    audit = audit_trace(
        recorded,
        saved,
        saved["length"],
        saved["observation_matrix"],
        saved["y0"],
        spec["tolerances"],
    )
    audit["frozen_e0_max_mean_difference_mm"] = error
    np.savez_compressed(out / "reference.npz", **saved)
    np.savez_compressed(out / "reference_trace.npz", **recorded)
    (out / "reference_provenance.json").write_bytes(
        (runtime / "provenance.json").read_bytes()
    )
    return saved, recorded, labels, library, audit


@dataclass
class Inputs:
    daily_features: torch.Tensor
    substep_features: torch.Tensor
    scale_features: torch.Tensor
    base_rate: torch.Tensor
    transient: torch.Tensor
    elastic: torch.Tensor
    force: torch.Tensor
    rate_scale: torch.Tensor
    force_scale: torch.Tensor
    slip_scale: torch.Tensor
    coefficients: Coefficients
    observation: torch.Tensor
    y0: torch.Tensor
    fit_labels: torch.Tensor
    fit_days: int
    normalizers: dict


def build_inputs(saved, recorded, labels, fit_days):
    h, n = fit_days, len(saved["mean"])
    if labels.shape != (h, 4) or not 30 < h < n:
        raise ValueError("Only the exact fit-label prefix is accepted")
    theta = saved["theta"]

    def rms(x, floor):
        return np.maximum(floor, np.sqrt(np.mean(np.asarray(x) ** 2, axis=0)))

    head, moisture = saved["rain_head"], saved["moisture"]
    delta_h = saved["reservoir_head"] - saved["forcing"][:, 1]
    delta_r = np.diff(saved["forcing"][:, 1], prepend=saved["forcing"][0, 1])
    sh, shr, sr = rms(head[:h], 1e-6), rms(delta_h[:h], 1e-6), rms(delta_r[:h], 1e-6)
    features = np.stack(
        (
            moisture,
            head / sh,
            np.broadcast_to((delta_h / shr)[:, None], (n, 4)),
            np.broadcast_to((delta_r / sr)[:, None], (n, 4)),
        ),
        axis=-1,
    )
    day = np.repeat(np.arange(1, n), 64)
    time = np.arange(1, len(day) + 1) / (64 * (n - 1))
    sub_features = np.concatenate(
        (
            np.broadcast_to(time[:, None, None], (len(day), 4, 1)),
            features[day],
            np.broadcast_to(np.eye(4), (len(day), 4, 4)),
        ),
        axis=-1,
    )
    scale_features = np.column_stack(
        (moisture.mean(axis=1), delta_h / shr, delta_r / sr)
    )
    days = np.arange(n)[:, None]
    transient = theta[28:32] * (1 - np.exp(-days / np.exp(theta[32:36])))
    rate_scale = np.maximum(
        1e-6, (saved["plastic"][h - 1] - saved["plastic"][0]) / (h - 1)
    )
    force_scale = rms(saved["force"][:h], 1.0)
    slip_scale = rms(recorded["lcp"][: (h - 1) * 64, :4], 1e-8)
    sy = max(1.0, float(np.sqrt(np.mean((saved["mean"][30:h] - labels[30:h]) ** 2))))
    normalizers = dict(
        head_rms=sh.tolist(),
        reservoir_lag_rms=float(shr),
        reservoir_change_rms=float(sr),
        plastic_rate=rate_scale.tolist(),
        force_rms=force_scale.tolist(),
        slip_rms=slip_scale.tolist(),
        displacement_scale=sy,
        fit_days=h,
        end_days=n,
    )
    return Inputs(
        tensor(features),
        tensor(sub_features),
        tensor(scale_features),
        tensor(saved["background_rate"]),
        tensor(transient),
        tensor(recorded["loads"][:, 4:8]),
        tensor(recorded["loads"][:, :4]),
        tensor(rate_scale),
        tensor(force_scale),
        tensor(slip_scale),
        Coefficients.from_reference(theta, saved["length"], saved["kc"], saved["ke"]),
        tensor(saved["observation_matrix"]),
        tensor(saved["y0"]),
        tensor(labels.copy()),
        h,
        normalizers,
    )


def substep_background(daily):
    fraction = (
        torch.arange(1, 65, dtype=daily.dtype, device=daily.device)[None, :, None] / 64
    )
    return (
        daily[:-1, None, :] + fraction * (daily[1:] - daily[:-1])[:, None, :]
    ).reshape(-1, 4)


def forward(model, data, center=None):
    centering = {"fit_days": data.fit_days} if center is None else {"center": center}
    background, a, fitted_center = model.background(
        data.daily_features, data.base_rate, data.transient, **centering
    )
    b = substep_background(background)
    dp = model.plastic_increment(data.substep_features, data.rate_scale)
    states = memory_states(dp, data.elastic, b, data.coefficients)
    mu = observe(states[::64], data.observation, data.y0)
    sigma = model.sigma(data.scale_features)
    db = torch.diff(b, dim=0, prepend=torch.zeros_like(b[:1]))
    raw = residuals(
        states[:-1], states[1:], data.force, data.elastic, db, data.coefficients
    )
    return dict(
        mean=mu,
        sigma=sigma,
        states=states,
        background=background,
        a=a,
        center=fitted_center,
        raw=raw,
    )


def gaussian_crps(mean, sigma, y):
    z = (y - mean) / sigma
    return sigma * (
        z * torch.erf(z / math.sqrt(2))
        + math.sqrt(2 / math.pi) * torch.exp(-z * z / 2)
        - 1 / math.sqrt(math.pi)
    )


def loss(output, data):
    h = data.fit_days
    mu, sigma, y = output["mean"][30:h], output["sigma"][30:h], data.fit_labels[30:h]
    raw = output["raw"]
    sy = data.normalizers["displacement_scale"]
    terms = dict(
        mae=(mu - y).abs().mean() / sy,
        crps=gaussian_crps(mu, sigma, y).mean() / sy,
        negative_gap=(raw["negative_gap"] / data.force_scale).square().mean(),
        complementarity=(raw["complementarity"] / (data.slip_scale * data.force_scale))
        .square()
        .mean(),
        response=1e-3 * output["a"][:h].square().mean(),
    )
    return sum(terms.values()), terms


def metrics(means, sigmas, observed, start, end):
    mu = means[:, start:end].mean(axis=0)
    y = observed[start:end]
    error = mu - y
    result = {
        "mae": np.abs(error).mean(axis=0),
        "rmse": np.sqrt(np.mean(error**2, axis=0)),
    }
    pooled = float(np.sqrt(np.mean(error**2)))
    if sigmas is not None:
        m, s = means[:, start:end], sigmas[:, start:end]
        result["crps"] = crps(m, s, y).mean(axis=0)
        q = summarize(m, s)
        for level in (80, 90, 95):
            low, high = q[f"lower_{level}"], q[f"upper_{level}"]
            result[f"coverage_{level}"] = ((y >= low) & (y <= high)).mean(axis=0)
            result[f"width_{level}"] = (high - low).mean(axis=0)
            result[f"interval_score_{level}"] = interval_score(
                y, low, high, level
            ).mean(axis=0)
    return {k: np.asarray(v).tolist() for k, v in result.items()}, pooled


def evaluate_distributions(
    spec, observed, baseline, old_means, old_sigmas, means, sigmas, reference_means
):
    h, n, tol = spec["fit_days"], spec["end_days"], spec["score_tolerance_mm"]
    records, table = {}, []
    distributions = {
        "M0": (baseline[None], None),
        "v1.1-e0": (old_means, old_sigmas),
        "PINN": (means, sigmas),
        "replay_diagnostic": (reference_means, sigmas),
    }
    for name, (m, s) in distributions.items():
        records[name] = {}
        for phase, start, end in (("train", 30, h), ("prediction", h, n)):
            values, pooled = metrics(m, s, observed, start, end)
            records[name][phase] = values
            for key, v in values.items():
                for station, value in zip(
                    [*POINTS, "point_mean"], [*v, float(np.mean(v))]
                ):
                    table.append(
                        dict(
                            model=name,
                            phase=phase,
                            station=station,
                            metric=key,
                            value=value,
                        )
                    )
            table.append(
                dict(
                    model=name,
                    phase=phase,
                    station="pooled",
                    metric="rmse",
                    value=pooled,
                )
            )
    pred = records["PINN"]["prediction"]
    base, old = records["M0"]["prediction"], records["v1.1-e0"]["prediction"]
    gains = {
        key: np.array((base if key == "mae" else old)[key]) - np.array(pred[key])
        for key in ("mae", "crps", "interval_score_90")
    }
    checks = {
        "prediction_mae_each_lower": bool((gains["mae"] > tol).all()),
        "fit_mae_each_not_worse": bool(
            (
                np.array(records["PINN"]["train"]["mae"])
                <= np.array(records["M0"]["train"]["mae"]) + tol
            ).all()
        ),
    }
    for key in ("crps", "interval_score_90"):
        checks[f"{key}_average_lower"] = bool(gains[key].mean() > tol)
        checks[f"{key}_each_not_worse"] = bool((gains[key] >= -tol).all())
    approximation = {
        "mae": np.abs(means[:, h:] - reference_means[:, h:]).mean(axis=(0, 1))
    }
    # Probability score drift is checked for each point claiming a positive gain
    # and for the four-point average, with the same frozen scale on both paths.
    for key in ("crps", "interval_score_90"):
        approximation[key] = np.abs(
            np.array(pred[key])
            - np.array(records["replay_diagnostic"]["prediction"][key])
        )
    fraction = spec["approximation_gain_fraction"]
    checks["mean_approximation_small_relative_to_gain"] = bool(
        ((gains["mae"] > tol) & (approximation["mae"] <= fraction * gains["mae"])).all()
    )
    for key in ("crps", "interval_score_90"):
        positive = gains[key] > tol
        avg_drift = abs(
            np.mean(pred[key])
            - np.mean(records["replay_diagnostic"]["prediction"][key])
        )
        checks[f"{key}_approximation_small_relative_to_gain"] = bool(
            gains[key].mean() > tol
            and avg_drift <= fraction * gains[key].mean()
            and (approximation[key][positive] <= fraction * gains[key][positive]).all()
        )
    return (
        records,
        pd.DataFrame(table),
        dict(
            passed=all(checks.values()),
            checks=checks,
            gains={k: v.tolist() for k, v in gains.items()},
            approximation={k: v.tolist() for k, v in approximation.items()},
            final_stage_started=False,
            interpretation="exposed historical development window; no mentor acceptance or blind validation",
        ),
    )
