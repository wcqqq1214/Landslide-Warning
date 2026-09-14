"""One 30-day causal TCN, with paired direct and B+ residual output baselines."""

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F


ARMS = ("TCN_DIRECT", "TCN_BRES")


def history_features(data):
    """Use only 30 observed days; full-history B+ memory is explicitly permitted."""
    x = data["x"]
    if x.shape[1:] != (30, 25, 4):
        raise ValueError("Expected the frozen v4 history interface")
    relative_y, relative_b = x[:, :, 9], x[:, :, 10]

    def within_window_delta(a):
        return np.diff(a, axis=1, prepend=a[:, :1])

    dy, db = within_window_delta(relative_y), within_window_delta(relative_b)
    rwl = x[:, :, 12, :1]
    pieces = [relative_y, dy, relative_b, db, dy - db]
    pieces += [x[:, :, c, :1] for c in (11, 12)]
    pieces += [within_window_delta(rwl)]
    pieces += [x[:, :, c, :1] for c in (16, 17, 18, 19, 20)]
    return np.ascontiguousarray(np.concatenate(pieces, axis=2))


class Scaling:
    def __init__(self, data=None, state=None):
        if state is None:
            x, z = history_features(data), data["z"]
            state = dict(
                x_mean=x.mean(axis=(0, 1), keepdims=True).tolist(),
                x_std=np.maximum(x.std(axis=(0, 1), keepdims=True), 1e-6).tolist(),
                z_mean=z.mean(axis=(0, 1), keepdims=True).tolist(),
                z_std=np.maximum(z.std(axis=(0, 1), keepdims=True), 1e-6).tolist(),
                target_unit=np.maximum(
                    np.sqrt(
                        np.mean((data["target"] - data["last_y"][:, None]) ** 2, axis=0)
                    ),
                    1e-6,
                ).tolist(),
            )
        self.state = state
        for k, v in state.items():
            setattr(self, k, np.asarray(v, float))

    def tensors(self, data, indices=None):
        part = (
            data
            if indices is None
            else {
                k: data[k][indices]
                for k in ("x", "z", "last_y", "anchor", "target")
                if k in data
            }
        )
        values = {
            "x": (history_features(part) - self.x_mean) / self.x_std,
            "z": (part["z"] - self.z_mean) / self.z_std,
        }
        values.update({k: part[k] for k in ("last_y", "anchor", "target") if k in part})
        return {
            k: torch.as_tensor(np.ascontiguousarray(v), dtype=torch.float64)
            for k, v in values.items()
        }


class CausalConv(nn.Module):
    def __init__(self, incoming, outgoing, kernel, dilation):
        super().__init__()
        self.left = (kernel - 1) * dilation
        self.conv = nn.Conv1d(incoming, outgoing, kernel, dilation=dilation)

    def forward(self, x):
        return self.conv(F.pad(x, (self.left, 0)))


class ResidualBlock(nn.Module):
    def __init__(self, incoming, outgoing, kernel, dilation):
        super().__init__()
        self.first = CausalConv(incoming, outgoing, kernel, dilation)
        self.second = CausalConv(outgoing, outgoing, kernel, dilation)
        self.skip = (
            nn.Conv1d(incoming, outgoing, 1) if incoming != outgoing else nn.Identity()
        )

    def forward(self, x):
        return F.relu(F.relu(self.second(F.relu(self.first(x)))) + self.skip(x))


class TCN(nn.Module):
    def __init__(self, spec):
        super().__init__()
        cfg = spec["neural"]
        incoming = cfg["history_channels"]
        layers = []
        for dilation in cfg["dilations"]:
            layers.append(
                ResidualBlock(incoming, cfg["hidden"], cfg["kernel"], dilation)
            )
            incoming = cfg["hidden"]
        self.encoder = nn.Sequential(*layers)
        self.head = nn.Linear(incoming + int(np.prod(cfg["future_shape"])), 28)
        nn.init.zeros_(self.head.weight)
        nn.init.zeros_(self.head.bias)
        self.double()

    def normalized_output(self, data):
        last = self.encoder(data["x"].transpose(1, 2))[:, :, -1]
        return self.head(torch.cat([last, data["z"].flatten(1)], dim=1)).reshape(
            -1, 7, 4
        )

    def forward(self, data, scaling, name):
        if name not in ARMS:
            raise ValueError("Only the two frozen TCN arms are supported")
        delta = self.normalized_output(data)
        unit = torch.as_tensor(scaling.target_unit, dtype=torch.float64)
        base = data["anchor"] if name == "TCN_BRES" else data["last_y"][:, None]
        return base + delta * unit, {}


def objective(model, name, data, scaling):
    unit = torch.as_tensor(scaling.target_unit, dtype=torch.float64)
    base = data["anchor"] if name == "TCN_BRES" else data["last_y"][:, None]
    target = (data["target"] - base) / unit
    return (model.normalized_output(data) - target).square().mean()


@torch.no_grad()
def predict(models, name, scaling, data, batch=128):
    answers = []
    for model in models:
        model.eval()
        chunks = [
            model(scaling.tensors(data, slice(i, i + batch)), scaling, name)[0].numpy()
            for i in range(0, len(data["origins"]), batch)
        ]
        answers.append(np.concatenate(chunks))
    return np.stack(answers)
