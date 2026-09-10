"""The two fixed v1.1 architectures and shared marginal Gaussian scale head."""

import math
import numpy as np
import torch
from torch import nn
from .mechanics import Day, tensor
from .features import point_features, public_features


def initialize(module, final):
    for layer in module.modules():
        if isinstance(layer, (nn.Linear, nn.Conv2d)):
            nn.init.xavier_uniform_(layer.weight)
            nn.init.zeros_(layer.bias)
    nn.init.zeros_(final.weight)
    nn.init.zeros_(final.bias)


class M1(nn.Module):
    def __init__(self):
        super().__init__()
        self.gates = nn.Conv2d(
            20 + 16, 4 * 16, (1, 3), padding=(0, 1), dtype=torch.float64
        )
        self.output = nn.Conv2d(16, 1, 1, dtype=torch.float64)
        initialize(self, self.output)

    def forward(self, windows):
        h = windows.new_zeros((len(windows), 16, 1, 4))
        c = torch.zeros_like(h)
        for t in range(30):
            i, f, o, g = self.gates(torch.cat([windows[:, t], h], dim=1)).chunk(
                4, dim=1
            )
            c = torch.sigmoid(f) * c + torch.sigmoid(i) * torch.tanh(g)
            h = torch.sigmoid(o) * torch.tanh(c)
        return 100 * self.output(h)[:, 0, 0]

    def predict(self, drivers, mechanics, scaler, chunk=128):
        x = tensor(scaler.transform(point_features(drivers, mechanics)))
        u = tensor(mechanics.u) + tensor(drivers.y0)
        values = [u.new_full((29, 4), float("nan"))]
        for start in range(29, len(x), chunk):
            ids = range(start, min(start + chunk, len(x)))
            windows = torch.stack([x[t - 29 : t + 1, :, None, :] for t in ids])
            values.append(u[start : start + len(windows)] + self(windows))
        return torch.cat(values)


class M2(nn.Module):
    def __init__(self):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(38, 16, dtype=torch.float64),
            nn.Tanh(),
            nn.Linear(16, 16, dtype=torch.float64),
            nn.Tanh(),
            nn.Linear(16, 4, dtype=torch.float64),
        )
        initialize(self, self.net[-1])

    def predict(self, drivers, mechanics, scaler, return_states=False):
        # old and accumulated creep remain on the autograd path across every day.
        old, creep = (
            torch.zeros(24, dtype=torch.float64),
            torch.zeros(4, dtype=torch.float64),
        )
        states, multipliers, audits = [old], [], []
        s, th = mechanics.reference, mechanics.theta
        public = tensor(
            np.column_stack(
                [public_features(drivers, s), s["rain_head"], s["moisture"]]
            )
        )
        le, obs, y0 = (
            tensor(mechanics.ctx.length),
            tensor(mechanics.ctx.obs),
            tensor(drivers.y0),
        )
        rate = tensor(s["background_rate"])
        kelvin = tensor(
            th[28:32]
            * (1 - np.exp(-np.arange(len(public))[:, None] / np.exp(th[32:36])))
        )
        previous = 0
        for t in range(1, len(public)):
            features = torch.cat(
                [public[t], old[:8], old[8:20] / le.repeat(3), old[20:24]]
            )
            multiplier = torch.exp(
                math.log(2) * torch.tanh(self.net(scaler.torch_transform(features)))
            )
            creep = creep + rate[t] * multiplier
            end = creep + kelvin[t]
            old, branch, audit = Day.apply(old, end, mechanics, t, previous)
            previous = int(branch)
            states.append(old)
            multipliers.append(multiplier)
            audits.append(audit)
        state = torch.stack(states)
        mu = (state[:, :4] + state[:, 20:24]) @ obs.T + y0
        if return_states:
            return mu, state, torch.stack(multipliers), torch.stack(audits)
        return mu


class ScaleHead(nn.Module):
    def __init__(self, rmse):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(20, 16, dtype=torch.float64),
            nn.Tanh(),
            nn.Linear(16, 1, dtype=torch.float64),
        )
        initialize(self, self.net[-1])
        value = (max(float(rmse), 0.002) - 0.001) / 100
        nn.init.constant_(self.net[-1].bias, math.log(math.expm1(value)))

    def forward(self, x):
        return 0.001 + 100 * nn.functional.softplus(self.net(x).squeeze(-1))


def scale_features(drivers, mechanics, scaler, mu):
    # Input is explicitly a detached array: scale fitting cannot modify the mean.
    x = point_features(drivers, mechanics, np.asarray(mu) - drivers.y0)
    return tensor(scaler.transform(x).transpose(0, 2, 1))
