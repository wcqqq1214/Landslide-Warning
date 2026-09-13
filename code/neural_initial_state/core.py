"""Bounded four-state head and strict differentiable seven-day C recursion."""

from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
import torch
from torch import nn

from physics_guided.mechanics import library
from short_horizon.common import ROOT, CALLS, sha
from short_horizon.data import load_cache
from short_horizon.physics import day_numpy


def read_spec(path):
    spec = json.loads(Path(path).read_text())
    for name, digest in spec["source_sha256"].items():
        if sha(ROOT / name) != digest:
            raise ValueError("Frozen input changed: " + name)
    return spec


def guard(spec, stage=None):
    key = {
        "feasibility": "feasibility_deadline_utc",
        "training": "training_deadline_utc",
    }.get(stage, "deadline_utc")
    if datetime.now(timezone.utc) >= datetime.fromisoformat(spec[key]):
        raise TimeoutError("Independent experiment deadline reached: " + key)


def cache_for(spec):
    return load_cache({"output_root": spec["cache_root"]})


class StrictDay(torch.autograd.Function):
    @staticmethod
    def forward(ctx, old, end, force, elastic, coeff):
        old_np = np.ascontiguousarray(old.detach().numpy())
        arrays = [np.ascontiguousarray(v.detach().numpy()) for v in (end, force, elastic, coeff)]
        out = np.empty_like(old_np)
        masks = np.empty((len(old_np), 64), np.int32)
        for i in range(len(old_np)):
            out[i], masks[i], audit = day_numpy(old_np[i], *[a[i] for a in arrays])
            if (
                audit[0] < -1e-8 or audit[1] < -1e-7 or audit[2] > 1e-7
                or np.min(out[i, 4:8] - old_np[i, 4:8]) < -1e-8
            ):
                raise ArithmeticError("Strict C trajectory failed physical constraints")
        ctx.coeff, ctx.masks = arrays[-1], masks
        return torch.from_numpy(out)

    @staticmethod
    def backward(ctx, grad):
        lib = library()
        values = np.ascontiguousarray(grad.detach().numpy())
        old = np.empty_like(values)
        unused_end = np.empty(4)
        for i in range(len(values)):
            CALLS["day_backward"] += 1
            lib.day_backward(ctx.coeff[i], ctx.masks[i], values[i], old[i], unused_end)
        return torch.from_numpy(old), None, None, None, None


def advance(data, initial):
    old = initial
    states = []
    for h in range(7):
        old = StrictDay.apply(
            old, data["state"][:, h + 1, 20:], data["force"][:, h],
            data["elastic"][:, h], data["coeff"],
        )
        states.append(old)
    states = torch.stack(states, dim=1)
    delta = states[:, :, :4] + states[:, :, 20:] - (initial[:, :4] + initial[:, 20:])[:, None]
    mean = data["last_y"][:, None] + torch.einsum("nhd,pd->nhp", delta, data["obs"])
    return mean, states


def corrected_initial(data, unit, normalized):
    initial = data["state"][:, 0].clone()
    units = torch.as_tensor(unit[:4], dtype=initial.dtype)
    radius = torch.minimum(units, torch.clamp(initial[:, :4], min=0))
    delta = radius * normalized
    initial[:, :4] = initial[:, :4] + delta
    return initial, delta, radius


class InitialStateNet(nn.Module):
    def __init__(self):
        super().__init__()
        self.encoder = nn.Sequential(nn.Linear(300, 32), nn.Tanh(), nn.Linear(32, 32), nn.Tanh())
        self.initial_head = nn.Linear(32, 4)
        for layer in self.modules():
            if isinstance(layer, nn.Linear):
                nn.init.xavier_uniform_(layer.weight)
                nn.init.zeros_(layer.bias)
        nn.init.zeros_(self.initial_head.weight)
        nn.init.zeros_(self.initial_head.bias)
        self.double()

    def forward(self, data, scaling):
        x = data["x"]
        summary = torch.cat(
            [x[:, -1].flatten(1), x[:, -7:].mean(1).flatten(1), x.mean(1).flatten(1)], dim=1
        )
        normalized = torch.tanh(self.initial_head(self.encoder(summary)))
        initial, delta, radius = corrected_initial(data, scaling.state_unit, normalized)
        mean, states = advance(data, initial)
        return mean, dict(initial=initial, states=states, delta=delta, radius=radius)


def objective(model, data, scaling, spec):
    mean, extra = model(data, scaling)
    unit = torch.as_tensor(scaling.target_unit, dtype=torch.float64)
    z_unit = torch.as_tensor(scaling.state_unit[:4], dtype=torch.float64)
    mse = ((mean - data["target"]) / unit).square().mean()
    regularization = (extra["delta"] / z_unit).square().mean()
    loss = mse + spec["neural"]["initial_regularization"] * regularization
    return loss, {"mse": float(mse.detach()), "initial_regularization": float(regularization.detach())}


@torch.no_grad()
def predict(model, scaling, data, spec, batch=64):
    model.eval()
    means, detail = [], {k: [] for k in ("initial", "states", "delta", "radius")}
    for i in range(0, len(data["origins"]), batch):
        guard(spec)
        mu, values = model(scaling.tensors(data, slice(i, i + batch)), scaling)
        means.append(mu.numpy())
        for k in detail:
            detail[k].append(values[k].numpy())
    return np.concatenate(means), {k: np.concatenate(v) for k, v in detail.items()}


def initial_checks(initial, base, unit):
    difference = initial - base
    if not np.isfinite(initial).all():
        raise ArithmeticError("Nonfinite initial state")
    if np.min(initial[..., :8]) < -1e-8:
        raise ArithmeticError("Negative z or plastic initial state")
    if not np.array_equal(initial[..., 4:], base[..., 4:]):
        raise ArithmeticError("Plastic, reaction or background memory changed")
    if np.any(abs(difference[..., :4]) > np.asarray(unit[:4]) + 1e-12):
        raise ArithmeticError("Initial correction exceeded frozen bound")
    return dict(max_correction_mm=float(np.max(abs(difference[..., :4]))), min_z_mm=float(np.min(initial[..., :4])), preserved_rest=True)
