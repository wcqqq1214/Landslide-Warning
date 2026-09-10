"""One full-history prediction path, reusing the original branchwise Day operator."""

from dataclasses import dataclass
import math

import numpy as np
import torch
from torch import nn

from physics_guided.mechanics import Day, Mechanics
from physics_guided_state_pinn.core import background_delta, network


class Budget:
    def __init__(self, limits):
        self.limits = dict(limits)
        self.counts = dict.fromkeys(limits, 0)

    def tick(self, name, amount=1):
        if not isinstance(amount, int) or amount < 1:
            raise ValueError("A positive integer call count is required")
        if self.counts[name] + amount > self.limits[name]:
            raise RuntimeError(f"Scientific budget exhausted before call: {name}")
        self.counts[name] += amount


class CountedLibrary:
    def __init__(self, library, budget):
        self.library, self.budget = library, budget

    def day_forward(self, *args):
        self.budget.tick("day_forwards")
        return self.library.day_forward(*args)

    def day_backward(self, *args):
        self.budget.tick("day_backwards")
        return self.library.day_backward(*args)


class RecordedMechanics(Mechanics):
    def __init__(self, reference, theta, drivers, budget):
        # Mechanics initialization performs exactly one original reference forward.
        budget.tick("reference_forwards")
        budget.tick("reference_substeps", (len(drivers.forcing) - 1) * 64)
        super().__init__(reference, theta, drivers)
        self.lib = CountedLibrary(self.lib, budget)
        self.records = []

    def begin(self):
        self.records = []

    def numpy_day(self, old, end, t, previous):
        out, masks, audit = super().numpy_day(old, end, t, previous)
        # Keep v1.8's stricter complementarity tolerance, beyond the old M2 check.
        if not np.isfinite(audit).all() or audit[2] > 1e-8:
            raise ArithmeticError(f"Original physical audit failed at day {t}")
        self.records.append(masks.copy())
        return out, masks, audit


@dataclass
class RateInputs:
    x: torch.Tensor
    rate: torch.Tensor
    background: torch.Tensor
    observation: torch.Tensor
    y0: torch.Tensor
    h: int

    def __post_init__(self):
        n = len(self.x)
        for value, shape in (
            (self.x, (n, 12)),
            (self.rate, (n, 4)),
            (self.background, (n, 4)),
            (self.observation, (4, 4)),
            (self.y0, (4,)),
        ):
            if (
                value.shape != shape
                or value.dtype != torch.float64
                or value.device.type != "cpu"
                or not torch.isfinite(value).all()
            ):
                raise ValueError("Exact finite CPU float64 rate inputs required")
        if not isinstance(self.h, int) or not 31 <= self.h <= n:
            raise ValueError("Valid consecutive training prefix required")
        if (self.rate < 0).any() or torch.count_nonzero(self.background[0]):
            raise ValueError("Nonnegative creep and zero initial background required")

    @classmethod
    def from_case(cls, case):
        return cls(
            case.x.clone(),
            case.rate.clone(),
            case.background.clone(),
            case.observation.clone(),
            case.y0.clone(),
            case.h,
        )


class RateReplay(nn.Module):
    def __init__(self):
        super().__init__()
        self.rate_net = network([12, 16, 16, 4])

    def forward(self, inputs, solver, budget, days=None, multiplier=None):
        days = len(inputs.x) if days is None else days
        if not isinstance(days, int) or not 2 <= days <= len(inputs.x):
            raise ValueError("Prediction requires a consecutive available prefix")
        if len(solver.force) < days or len(solver.elastic) < days:
            raise ValueError("The solver must contain the complete requested prefix")
        if multiplier is None:
            budget.tick("neural_evaluations")
            multiplier = torch.exp(
                math.log(2) * torch.tanh(self.rate_net(inputs.x[:days]))
            )
        if (
            multiplier.shape != (days, 4)
            or multiplier.dtype != torch.float64
            or multiplier.device.type != "cpu"
            or not torch.isfinite(multiplier).all()
            or (multiplier < 0.5).any()
            or (multiplier > 2).any()
        ):
            raise ValueError("Finite bounded four-domain multipliers required")
        delta = background_delta(inputs.rate[:days], multiplier)
        background = inputs.background[:days] + delta
        budget.tick("trajectories")
        solver.begin()
        old = torch.zeros(24, dtype=torch.float64)
        states, audits, previous = [old], [], 0
        for t in range(1, days):
            old, branch, audit = Day.apply(old, background[t], solver, t, previous)
            previous = int(branch)
            states.append(old)
            audits.append(audit)
        state = torch.stack(states)
        return {
            "state": state,
            "mean": (state[:, :4] + state[:, 20:24]) @ inputs.observation.T + inputs.y0,
            "multiplier": multiplier,
            "background": background,
            "delta_background": delta,
            "masks": torch.from_numpy(np.stack(solver.records)),
            "audit": torch.stack(audits),
        }


def objective(inputs, output, labels):
    if (
        labels.shape != (inputs.h, 4)
        or output["mean"].shape != labels.shape
        or output["multiplier"].shape != labels.shape
        or not torch.isfinite(labels).all()
    ):
        raise ValueError("Loss accepts only the exact finite training label prefix")
    data = ((output["mean"][30:] - labels[30:]) / 100).square().mean()
    prior = (torch.log(output["multiplier"][1:]) / math.log(2)).square().mean()
    total = data + 0.001 * prior
    if not torch.isfinite(total):
        raise ArithmeticError("Nonfinite shared mechanical objective")
    return total, {"data": data, "rate_prior": prior}
