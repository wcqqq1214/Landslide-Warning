"""Separate observed history from known future inputs; preserve recurrent state."""

from dataclasses import dataclass

import numpy as np
import torch
from torch import nn

from physics_guided.models import initialize


@dataclass
class Sequence:
    encoder: torch.Tensor
    decoder: torch.Tensor
    baseline: torch.Tensor
    origin: int


class Budget:
    def __init__(self, limits):
        self.limits = dict(limits)
        self.counts = {name: 0 for name in limits}

    def tick(self, name, count=1):
        if count < 0 or self.counts[name] + count > self.limits[name]:
            raise RuntimeError(f"Sequence validation budget exhausted: {name}")
        self.counts[name] += count


def make_sequence(
    base, features, labels, origin, horizon, physical_scaler, history_scaler
):
    if (
        type(origin) is not int
        or origin < 31
        or type(horizon) is not int
        or not 1 <= horizon <= 180
    ):
        raise ValueError("Registered history and forecast lengths required")
    if (
        labels.shape != (origin, 4)
        or base.shape != (len(features), 4)
        or features.shape[1:] != (20, 4)
        or origin + horizon > len(base)
    ):
        raise ValueError(
            "Exact observation prefix and complete physical inputs required"
        )
    for scaler, shape in ((physical_scaler, (20, 4)), (history_scaler, (2, 4))):
        if (
            scaler.mean.shape != shape
            or scaler.scale.shape != shape
            or not np.isfinite(scaler.mean).all()
            or not np.isfinite(scaler.scale).all()
            or (scaler.scale <= 0).any()
        ):
            raise ValueError("Finite frozen four-point scalers required")
    # The derivative's left endpoint is the only extra observed day needed.
    residual = labels[-31:] - base[origin - 31 : origin]
    history = np.stack([residual[1:], np.diff(residual, axis=0)], axis=1)
    observed = history_scaler.transform(history)
    physical = physical_scaler.transform(features[origin - 30 : origin + horizon])
    baseline = base[origin : origin + horizon].copy()
    encoder = np.concatenate(
        [
            physical[:30],
            observed,
            np.broadcast_to(np.arange(-30, 0)[:, None, None] / 179, (30, 1, 4)),
        ],
        axis=1,
    )
    decoder = np.concatenate(
        [
            physical[30:],
            np.zeros((horizon, 2, 4)),
            np.broadcast_to(np.arange(horizon)[:, None, None] / 179, (horizon, 1, 4)),
        ],
        axis=1,
    )
    if not all(np.isfinite(value).all() for value in (encoder, decoder, baseline)):
        raise ValueError("Nonfinite required sequence input")
    return Sequence(
        torch.tensor(encoder[None, :, :, None, :], dtype=torch.float64),
        torch.tensor(decoder[None, :, :, None, :], dtype=torch.float64),
        torch.tensor(baseline[None], dtype=torch.float64),
        origin,
    )


def tick(budget, name, count=1):
    if budget is not None:
        budget.tick(name, count)


def valid_input(value):
    if (
        value.ndim != 5
        or value.shape[2:] != (23, 1, 4)
        or value.shape[0] < 1
        or not 1 <= value.shape[1] <= 180
        or value.dtype != torch.float64
        or value.device.type != "cpu"
        or not torch.isfinite(value).all()
    ):
        raise ValueError("Finite CPU float64 [batch,time,23,1,4] input required")


class SequenceM1(nn.Module):
    def __init__(self):
        super().__init__()
        self.gates = nn.Conv2d(39, 64, (1, 3), padding=(0, 1), dtype=torch.float64)
        self.output = nn.Conv2d(16, 1, 1, dtype=torch.float64)
        initialize(self, self.output)

    def step(self, value, state, budget=None):
        tick(budget, "cell_sample_steps", len(value))
        hidden, cell = state
        i, f, o, g = self.gates(torch.cat([value, hidden], dim=1)).chunk(4, dim=1)
        cell = torch.sigmoid(f) * cell + torch.sigmoid(i) * torch.tanh(g)
        return torch.sigmoid(o) * torch.tanh(cell), cell

    def encode(self, history, budget=None):
        valid_input(history)
        if history.shape[1] != 30:
            raise ValueError("Exactly 30 historical days required")
        tick(budget, "sequence_segments")
        state = (
            history.new_zeros((len(history), 16, 1, 4)),
            history.new_zeros((len(history), 16, 1, 4)),
        )
        for value in history.unbind(dim=1):
            state = self.step(value, state, budget)
        return state

    def decode(self, future, state, budget=None):
        valid_input(future)
        if len(state) != 2 or any(
            value.shape != (len(future), 16, 1, 4)
            or value.dtype != future.dtype
            or value.device != future.device
            or not torch.isfinite(value).all()
            for value in state
        ):
            raise ValueError("Matching finite encoder hidden/cell states required")
        tick(budget, "sequence_segments")
        corrections = []
        for value in future.unbind(dim=1):
            state = self.step(value, state, budget)
            tick(budget, "readout_sample_steps", len(future))
            corrections.append(100 * self.output(state[0])[:, 0, 0])
        return torch.stack(corrections, dim=1), state

    def forward(self, history, future, budget=None):
        tick(budget, "model_calls")
        return self.decode(future, self.encode(history, budget), budget)[0]


def initial_models(seed=0):
    torch.manual_seed(seed)
    zero = SequenceM1()
    probe = SequenceM1()
    probe.load_state_dict(zero.state_dict())
    with torch.no_grad():
        probe.output.weight.copy_(
            torch.linspace(-0.01, 0.01, 16, dtype=torch.float64).reshape(1, 16, 1, 1)
        )
    return zero, probe
