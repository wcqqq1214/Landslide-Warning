"""v2.3 neural components and full-history recurrence; no training runner.

Features/scales must be prepared from the fit prefix under the v2.3 contract.
This module neither accepts observation labels nor solves complementarity.
"""

import math

import torch
from torch import nn
from torch.nn import functional as F

from .equations import Coefficients


def linear_memory(addition, decay):
    """Zero-initial y[n] = decay*y[n-1] + addition[n], without truncation.

    Doubling composes causal affine recurrences in O(log N) tensor operations.
    It avoids division by tiny powers of decay and keeps the entire gradient.
    """
    if addition.ndim != 2 or addition.shape[1] != 4 or not len(addition):
        raise ValueError("Expected a nonempty substep-by-four array")
    if decay.shape not in (torch.Size([]), torch.Size([4])):
        raise ValueError("Expected scalar or four-domain decay")
    if not torch.isfinite(addition).all() or not torch.isfinite(decay).all():
        raise ValueError("Finite recurrence inputs required")
    if (decay < 0).any() or (decay > 1).any():
        raise ValueError("Decay must be in [0, 1]")
    value, power, offset = addition, decay, 1
    while offset < len(addition):
        value = torch.cat(
            (value[:offset], value[offset:] + power * value[:-offset]), dim=0
        )
        power = power * power
        offset *= 2
    return value


def memory_states(plastic_increment, elastic, background, coefficients, dt=1 / 64):
    """Derive s,p,rb,rc,rE,b at all substeps, including the zero initial state.

    Inputs exclude day zero; background is the prescribed substep endpoint.
    Plastic increments are supplied by the network, not an active-set solver.
    """
    c = coefficients
    if not isinstance(c, Coefficients):
        raise TypeError("Expected original B+ coefficients")
    c.validate()
    if not 0 < dt < float("inf"):
        raise ValueError("Positive finite dt required")
    if plastic_increment.ndim != 2 or plastic_increment.shape[1] != 4:
        raise ValueError("Expected substep-by-four plastic increments")
    if elastic.shape != plastic_increment.shape or background.shape != elastic.shape:
        raise ValueError("Substep loads and increments must have matching shapes")
    if not all(
        torch.isfinite(x).all() for x in (plastic_increment, elastic, background)
    ):
        raise ValueError("Finite increments and loads required")
    if (plastic_increment < 0).any():
        raise ValueError("Nonnegative plastic increments required")
    beta = dt / (c.tau_motion + dt)
    ar = 1 / (1 + dt / c.tau_rest)
    ac = 1 / (1 + dt / c.tau_contact)
    ae = 1 / (1 + dt / c.tau_bulk)
    p = plastic_increment.cumsum(dim=0)
    s = linear_memory(beta * (p + elastic), 1 - beta)
    u = s + background
    du = torch.diff(u, dim=0, prepend=torch.zeros_like(u[:1]))
    rb = linear_memory(ar * c.hardening * plastic_increment, ar)
    rc = linear_memory(ac * (du @ c.contact.T), ac)
    re = linear_memory(ae * (du @ c.bulk.T), ae)
    state = torch.cat((s, p, rb, rc, re, background), dim=-1)
    return torch.cat((torch.zeros_like(state[:1]), state), dim=0)


class CreepPinn(nn.Module):
    """The 514 trainable parameters specified in v2.3; inputs are normalized."""

    def __init__(self, displacement_scale):
        super().__init__()
        if not math.isfinite(displacement_scale) or displacement_scale < 1:
            raise ValueError("Displacement scale must be finite and at least 1 mm")
        self.creep_net = nn.Sequential(nn.Linear(4, 8), nn.Tanh(), nn.Linear(8, 1))
        self.plastic_net = nn.Sequential(
            nn.Linear(9, 16), nn.Tanh(), nn.Linear(16, 16), nn.Tanh(), nn.Linear(16, 1)
        )
        self.scale_net = nn.Linear(3, 4)
        self.register_buffer(
            "displacement_scale", torch.tensor(displacement_scale, dtype=torch.float64)
        )
        self.double()
        nn.init.zeros_(self.creep_net[-1].weight)
        nn.init.zeros_(self.creep_net[-1].bias)
        nn.init.zeros_(self.plastic_net[-1].weight)
        nn.init.constant_(self.plastic_net[-1].bias, math.log(math.expm1(1)))
        nn.init.zeros_(self.scale_net.weight)
        target = (displacement_scale - 0.001) / displacement_scale
        nn.init.constant_(self.scale_net.bias, math.log(math.expm1(target)))

    def creep_response(self, features, *, fit_days=None, center=None):
        """Use a differentiable fit center or an explicitly frozen saved center.

        At inference the caller supplies the saved four-domain center; there is
        no automatic recentering on a forecast-only batch.
        """
        if features.ndim != 3 or features.shape[1:] != (4, 4):
            raise ValueError("Expected day-by-domain-by-feature input")
        if not torch.isfinite(features).all():
            raise ValueError("Finite features required")
        if (fit_days is None) == (center is None):
            raise ValueError("Specify exactly one fit prefix or frozen center")
        r = self.creep_net(features).squeeze(-1).tanh()
        if center is None:
            if not isinstance(fit_days, int) or not 1 <= fit_days <= len(features):
                raise ValueError("Invalid fit prefix length")
            center = r[:fit_days].mean(dim=0)
        elif center.shape != (4,) or not torch.isfinite(center).all():
            raise ValueError("Expected a finite four-domain frozen center")
        if (center.abs() > 1).any():
            raise ValueError("Frozen response center must lie in [-1, 1]")
        a = (r - center) / 2
        return torch.exp(math.log(2) * a), a, center

    def background(self, features, base_rate, transient, **centering):
        multiplier, a, center = self.creep_response(features, **centering)
        if base_rate.shape != multiplier.shape or transient.shape != multiplier.shape:
            raise ValueError("Expected daily four-domain rate and transient")
        if not torch.isfinite(base_rate).all() or not torch.isfinite(transient).all():
            raise ValueError("Finite background inputs required")
        if (transient[0] != 0).any():
            raise ValueError("Original transient must start at zero")
        rate = base_rate * multiplier
        accumulated = torch.cat((torch.zeros_like(rate[:1]), rate[1:].cumsum(dim=0)))
        return accumulated + transient, a, center

    def plastic_increment(self, substep_features, rate_scale):
        if substep_features.ndim != 3 or substep_features.shape[1:] != (4, 9):
            raise ValueError("Expected substep-by-domain-by-nine input")
        if rate_scale.shape != (4,) or (rate_scale <= 0).any():
            raise ValueError("Positive four-domain rate scale required")
        if (
            not torch.isfinite(substep_features).all()
            or not torch.isfinite(rate_scale).all()
        ):
            raise ValueError("Finite plastic inputs required")
        return (
            F.softplus(self.plastic_net(substep_features).squeeze(-1)) * rate_scale / 64
        )

    def sigma(self, features):
        if (
            features.ndim != 2
            or features.shape[1] != 3
            or not torch.isfinite(features).all()
        ):
            raise ValueError("Expected finite day-by-three scale features")
        return 0.001 + self.displacement_scale * F.softplus(self.scale_net(features))
