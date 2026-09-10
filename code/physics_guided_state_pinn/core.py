"""Causal background integration and the frozen v1.9 state/residual architecture."""

import math

import numpy as np
import torch
from torch import nn

from physics_guided_pinn.equations import Coefficients, observe, residuals

FEATURE_FLOOR = np.array([1, 1, 1, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 1])
PHYSICS_KEYS = (
    "motion",
    "basal_memory",
    "contact_memory",
    "bulk_memory",
    "negative_slip",
    "negative_gap",
    "complementarity",
)


def tensor(value):
    return torch.as_tensor(value, dtype=torch.float64)


def rms(values):
    return np.sqrt(np.mean(np.square(values), axis=0))


def daily_features(saved, forcing):
    n = len(saved["mean"])
    if forcing.shape != (n, 2) or not np.isfinite(forcing).all():
        raise ValueError("Exact finite driver prefix required")
    return np.column_stack(
        [
            forcing[:, 0],
            forcing[:, 1],
            np.r_[0.0, np.diff(forcing[:, 1])],
            saved["rain_head"],
            saved["moisture"],
            saved["reservoir_head"],
        ]
    )


def training_constants(saved, features, length, h):
    if not 31 <= h <= len(features) or features.shape[1:] != (12,):
        raise ValueError("A training prefix of at least 31 days is required")
    q, p = saved["coordinates"][:h], saved["plastic"][:h]
    rb, rc, re = [
        saved[name][:h] for name in ("basal_reaction", "contact", "bulk_reaction")
    ]
    sq = np.maximum(1.0, rms(q))
    frb, frc, fre = [np.maximum(1.0, rms(x)) for x in (rb, rc, re)]
    vq, vp = [np.maximum(0.001, rms(np.diff(x, axis=0))) for x in (q, p)]
    th = saved["theta"]
    kr = np.exp(th[12:16]) * length
    tr, tm = np.exp(th[16:20]), np.exp(th[20:24])
    tc, te, dt = np.exp(th[42]), np.exp(th[43]), 1 / 64
    slip_scale = dt / (tm + dt) * dt * vp
    gap_scale = np.maximum(1.0, np.maximum(rms(saved["force"][:h]), rms(rb + rc + re)))
    physics = {
        "motion": dt * vq,
        "basal_memory": dt * np.maximum(frb / tr, kr * vp),
        "contact_memory": dt * np.maximum(frc / tc, abs(saved["kc"]) @ vq),
        "bulk_memory": dt * np.maximum(fre / te, abs(saved["ke"]) @ vq),
        "negative_slip": slip_scale,
        "negative_gap": gap_scale,
        "complementarity": slip_scale * gap_scale,
    }
    result = {
        "training_days": h,
        "feature_mean": features[:h].mean(axis=0).tolist(),
        "feature_std": np.maximum(features[:h].std(axis=0), FEATURE_FLOOR).tolist(),
        "q_scale": sq.tolist(),
        "state_scale": np.r_[sq, sq, frb, frc, fre].tolist(),
        "physics_scales": {name: value.tolist() for name, value in physics.items()},
        "q_rate_scale": vq.tolist(),
        "plastic_rate_scale": vp.tolist(),
    }
    for value in [result["feature_std"], sq, result["state_scale"], *physics.values()]:
        value = np.asarray(value)
        if not np.isfinite(value).all() or (value <= 0).any():
            raise ValueError("Nonfinite or nonpositive training scale")
    return result


class Case:
    """Frozen teachers/forcing and prefix-fitted scales; contains no observed labels."""

    def __init__(self, saved, trace, forcing, y0, h, constants=None):
        self.days, self.h = len(saved["mean"]), h
        steps = (self.days - 1) * 64
        if trace["current"].shape != (steps, 24) or trace["loads"].shape != (steps, 12):
            raise ValueError("Complete original substep trace required")
        if trace["length"].shape != (4,) or not np.isfinite(trace["length"]).all():
            raise ValueError("Four finite original domain lengths required")
        features = daily_features(saved, forcing)
        expected = training_constants(saved, features, trace["length"], h)
        if constants is not None and constants != expected:
            raise ValueError(
                "Checkpoint constants differ from the exact training prefix"
            )
        self.constants = expected
        self.x = tensor(
            (features - np.array(expected["feature_mean"]))
            / np.array(expected["feature_std"])
        )
        self.base = tensor(np.vstack([np.zeros((1, 24)), trace["current"]]))
        self.loads = tensor(trace["loads"])
        self.rate = tensor(saved["background_rate"])
        self.background = tensor(saved["background"])
        self.observation, self.y0 = tensor(saved["observation_matrix"]), tensor(y0)
        self.sq, self.sz = tensor(expected["q_scale"]), tensor(expected["state_scale"])
        self.scales = {
            name: tensor(scale) for name, scale in expected["physics_scales"].items()
        }
        self.coefficient = Coefficients.from_reference(
            saved["theta"], trace["length"], saved["kc"], saved["ke"]
        )
        self.coefficient.validate()
        for value in (
            self.x,
            self.base,
            self.loads,
            self.rate,
            self.background,
            self.observation,
            self.y0,
        ):
            if not torch.isfinite(value).all():
                raise ValueError("Nonfinite PINN template")
        if self.rate.shape != (self.days, 4) or (self.rate < 0).any():
            raise ValueError("Original nonnegative creep rates required")
        if self.observation.shape != (4, 4) or self.y0.shape != (4,):
            raise ValueError("Original four-point observation map required")


def interpolate_daily(values):
    """A single state at every knot; gradients connect all neighboring days."""
    if values.ndim != 2 or len(values) < 2:
        raise ValueError("At least two days of vector values required")
    index = torch.arange((len(values) - 1) * 64 + 1, device=values.device)
    left = index // 64
    right = torch.clamp(left + 1, max=len(values) - 1)
    fraction = (index % 64).to(values.dtype)[:, None] / 64
    return values[left] + fraction * (values[right] - values[left])


def background_delta(rate, multiplier):
    if rate.shape != multiplier.shape or rate.ndim != 2 or rate.shape[1] != 4:
        raise ValueError("Matched four-domain rate and multiplier arrays required")
    return torch.cat(
        [
            torch.zeros_like(rate[:1]),
            torch.cumsum(rate[1:] * (multiplier[1:] - 1), dim=0),
        ]
    )


def network(sizes):
    layers = []
    for index, (a, b) in enumerate(zip(sizes[:-1], sizes[1:])):
        linear = nn.Linear(a, b, dtype=torch.float64)
        nn.init.xavier_uniform_(linear.weight)
        nn.init.zeros_(linear.bias)
        layers.append(linear)
        if index < len(sizes) - 2:
            layers.append(nn.Tanh())
    nn.init.zeros_(layers[-1].weight)
    nn.init.zeros_(layers[-1].bias)
    return nn.Sequential(*layers)


class StatePINN(nn.Module):
    def __init__(self):
        super().__init__()
        self.rate_net = network([12, 16, 16, 4])
        self.state_net = network([40, 32, 32, 20])

    def forward(self, case, days=None, chunk=None):
        days = case.days if days is None else days
        if not isinstance(days, int) or not 2 <= days <= case.days:
            raise ValueError("Prediction requires a consecutive registered prefix")
        if chunk is not None and (not isinstance(chunk, int) or chunk < 1):
            raise ValueError("Positive inference chunk required")
        multiplier = torch.exp(math.log(2) * torch.tanh(self.rate_net(case.x[:days])))
        delta_daily = background_delta(case.rate[:days], multiplier)
        delta_substep = interpolate_daily(delta_daily)
        base = case.base[: len(delta_substep)]
        features = torch.cat(
            [
                interpolate_daily(case.x[:days]),
                base[:, :20] / case.sz,
                base[:, 20:] / case.sq,
                delta_substep / case.sq,
            ],
            dim=1,
        )
        raw = (
            self.state_net(features)
            if chunk is None
            else torch.cat(
                [
                    self.state_net(features[start : start + chunk])
                    for start in range(0, len(features), chunk)
                ]
            )
        )
        time = (
            torch.arange(len(base), dtype=base.dtype, device=base.device)[:, None] / 64
        )
        state = torch.cat(
            [
                base[:, :20] + time / (time + 30) * case.sz * torch.tanh(raw),
                base[:, 20:] + delta_substep,
            ],
            dim=1,
        )
        mean = observe(state[::64], case.observation, case.y0)
        return {
            "state": state,
            "mean": mean,
            "multiplier": multiplier,
            "delta_background": delta_daily,
            "delta_substep": delta_substep,
        }


def equation_values(case, output):
    states, delta = output["state"], output["delta_substep"]
    load = case.loads[: len(states) - 1]
    return residuals(
        states[:-1],
        states[1:],
        load[:, :4],
        load[:, 4:8],
        load[:, 8:] + delta[1:] - delta[:-1],
        case.coefficient,
    )


def losses(case, output, labels, update_index):
    if labels.shape != (case.h, 4) or output["mean"].shape != labels.shape:
        raise ValueError("Training loss accepts only the exact training label prefix")
    if not isinstance(update_index, int) or not 1 <= update_index <= 200:
        raise ValueError("Only the registered 200 updates are permitted")
    if not torch.isfinite(labels).all():
        raise ValueError("Nonfinite training labels")
    values = equation_values(case, output)
    physics_parts = {
        name: (values[name] / case.scales[name]).square().mean()
        for name in PHYSICS_KEYS
    }
    physical = torch.stack(list(physics_parts.values())).mean()
    data = ((output["mean"][30:] - labels[30:]) / 100).square().mean()
    rate = (torch.log(output["multiplier"][1:]) / math.log(2)).square().mean()
    weight = min(1.0, update_index / 20)
    total = data + weight * physical + 0.001 * rate
    if not torch.isfinite(total):
        raise ArithmeticError("Nonfinite complete-prefix PINN loss")
    return total, {
        "data": data,
        "physics": physical,
        "rate_prior": rate,
        "physics_weight": weight,
        **physics_parts,
    }
