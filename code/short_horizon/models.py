"""Paired mean learners; all reported probability scales are calibrated externally."""

import numpy as np
from scipy.linalg import solve
import torch
from torch import nn

from rolling_probability.models import DirectConvLSTM
from .data import ridge_features
from .physics import BatchDay


class Scaling:
    def __init__(self, data=None, state=None):
        if state is not None:
            self.state = state
        else:
            self.state = dict(
                x_mean=data["x"].mean(axis=(0, 1, 3), keepdims=True).tolist(),
                x_std=np.maximum(
                    data["x"].std(axis=(0, 1, 3), keepdims=True), 1e-6
                ).tolist(),
                z_mean=data["z"].mean(axis=(0, 1, 3), keepdims=True).tolist(),
                z_std=np.maximum(
                    data["z"].std(axis=(0, 1, 3), keepdims=True), 1e-6
                ).tolist(),
                target_unit=np.maximum(
                    np.sqrt(
                        np.mean((data["target"] - data["last_y"][:, None]) ** 2, axis=0)
                    ),
                    1e-6,
                ).tolist(),
                state_unit=np.maximum(
                    np.sqrt(
                        np.mean(
                            (data["state"][:, 1:, :20] - data["state"][:, :1, :20])
                            ** 2,
                            axis=(0, 1),
                        )
                    ),
                    1e-6,
                ).tolist(),
            )
        for k, v in self.state.items():
            setattr(self, k, np.asarray(v, float))

    def tensors(self, data, indices=None):
        keys = (
            "x",
            "z",
            "anchor",
            "last_y",
            "state",
            "force",
            "elastic",
            "coeff",
            "target",
        )
        t = {}
        for k in keys:
            if k not in data:
                continue
            a = data[k] if indices is None else data[k][indices]
            if k == "x":
                a = (a - self.x_mean) / self.x_std
            if k == "z":
                a = (a - self.z_mean) / self.z_std
            t[k] = torch.as_tensor(np.ascontiguousarray(a), dtype=torch.float64)
        t["obs"] = torch.as_tensor(data["obs"], dtype=torch.float64)
        return t


class ConvMean(DirectConvLSTM):
    def __init__(self, x_channels=25, z_channels=8, hidden=16):
        super().__init__(x_channels, z_channels, hidden)
        self.decoder[-1] = nn.Conv1d(hidden, 1, 1)
        nn.init.zeros_(self.decoder[-1].weight)
        nn.init.zeros_(self.decoder[-1].bias)

    def forward(self, data, scaling, name):
        d = self.encoded_decoder(data["x"], data["z"])[:, :, 0]
        unit = torch.as_tensor(scaling.target_unit, dtype=d.dtype)
        base = data["anchor"] if name == "CL_BRES" else data["last_y"][:, None]
        return base + d * unit, {}


class StatePINN(nn.Module):
    def __init__(self, x_channels=25, z_channels=8, width=32):
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Linear(3 * x_channels * 4, width),
            nn.Tanh(),
            nn.Linear(width, width),
            nn.Tanh(),
        )
        self.initial_head = nn.Linear(width, 4)
        self.decoder = nn.Sequential(
            nn.Linear(width + z_channels * 4, width), nn.Tanh(), nn.Linear(width, 20)
        )
        for layer in self.modules():
            if isinstance(layer, nn.Linear):
                nn.init.xavier_uniform_(layer.weight)
                nn.init.zeros_(layer.bias)
        for layer in (self.initial_head, self.decoder[-1]):
            nn.init.zeros_(layer.weight)
            nn.init.zeros_(layer.bias)

    def forward(self, data, scaling, name):
        x = data["x"]
        summary = torch.cat(
            [x[:, -1].flatten(1), x[:, -7:].mean(1).flatten(1), x.mean(1).flatten(1)],
            dim=1,
        )
        encoded = self.encoder(summary)
        initial_norm = torch.tanh(self.initial_head(encoded))
        unit = torch.as_tensor(scaling.state_unit, dtype=x.dtype)
        initial = data["state"][:, 0].clone()
        initial[:, :4] = initial[:, :4] + initial_norm * unit[:4]
        H = data["z"].shape[1]
        inp = torch.cat(
            [encoded[:, None].expand(-1, H, -1), data["z"].flatten(2)], dim=2
        )
        correction = torch.tanh(self.decoder(inp)) * unit
        states = torch.cat(
            [data["state"][:, 1:, :20] + correction, data["state"][:, 1:, 20:]], dim=-1
        )
        coords = (
            states[:, :, :4]
            + states[:, :, 20:]
            - (initial[:, :4] + initial[:, 20:])[:, None]
        )
        mean = data["last_y"][:, None] + torch.einsum(
            "nhd,pd->nhp", coords, data["obs"]
        )
        return mean, dict(states=states, initial=initial, initial_norm=initial_norm)


def new_model(name, spec):
    model = (
        ConvMean(hidden=spec["neural"]["conv_hidden"])
        if name.startswith("CL_")
        else StatePINN()
    )
    return model.double()


def objective(model, name, data, scaling, spec):
    mean, extra = model(data, scaling, name)
    unit = torch.as_tensor(scaling.target_unit, dtype=torch.float64)
    loss = ((mean - data["target"]) / unit).square().mean()
    parts = {"mse": float(loss.detach())}
    if name.startswith("PINN_"):
        reg = extra["initial_norm"].square().mean()
        loss = loss + spec["pinn"]["initial_regularization"] * reg
        if name == "PINN_EQ":
            states, initial = extra["states"], extra["initial"]
            old = torch.cat([initial[:, None], states[:, :-1]], dim=1)
            coeff = data["coeff"][:, None].expand(-1, 7, -1).contiguous()
            advanced = BatchDay.apply(
                old, states[:, :, 20:], data["force"], data["elastic"], coeff
            )
            su = torch.as_tensor(scaling.state_unit, dtype=torch.float64)
            defect = ((states[:, :, :20] - advanced[:, :, :20]) / su).square().mean()
            plastic = (
                (torch.relu(-(states[:, :, 4:8] - old[:, :, 4:8])) / su[4:8])
                .square()
                .mean()
            )
            loss = (
                loss
                + spec["pinn"]["equation_weight"] * defect
                + spec["pinn"]["plastic_monotonic_weight"] * plastic
            )
            parts.update(
                equation=float(defect.detach()), plastic=float(plastic.detach())
            )
    return loss, parts


@torch.no_grad()
def predict_neural(models, name, scaling, data, spec, batch=128):
    means, details = [], []
    for model in models:
        model.eval()
        chunks = []
        states = []
        initial = []
        for i in range(0, len(data["origins"]), batch):
            t = scaling.tensors(data, slice(i, i + batch))
            mu, extra = model(t, scaling, name)
            chunks.append(mu.numpy())
            if extra:
                states.append(extra["states"].numpy())
                initial.append(extra["initial"].numpy())
        means.append(np.concatenate(chunks))
        if states:
            details.append(
                dict(states=np.concatenate(states), initial=np.concatenate(initial))
            )
    return np.stack(means), details


class RidgeMean:
    def __init__(self, state):
        self.state = state
        for k in ("feature_mean", "feature_std", "target_unit", "beta"):
            setattr(self, k, np.asarray(state[k], float))

    @classmethod
    def fit(cls, data, scaling, alpha, name):
        x = ridge_features(data)
        m = x.mean(0)
        sd = np.maximum(x.std(0), 1e-6)
        z = (x - m) / sd
        z = np.concatenate([np.ones_like(z[..., :1]), z], axis=-1)
        base = data["anchor"] if name == "RR_BRES" else data["last_y"][:, None]
        target = (data["target"] - base) / scaling.target_unit
        beta = np.empty((7, 4, z.shape[-1]))
        N = len(z)
        penalty = alpha * np.eye(z.shape[-1])
        penalty[0, 0] = 0
        for h in range(7):
            for p in range(4):
                A = z[:, h, p]
                b = target[:, h, p]
                G = np.einsum("ni,nj->ij", A, A, optimize=False) / N + penalty
                rhs = np.einsum("ni,n->i", A, b, optimize=False) / N
                beta[h, p] = solve(G, rhs, assume_a="pos")
        return cls(
            dict(
                name=name,
                alpha=float(alpha),
                feature_mean=m.tolist(),
                feature_std=sd.tolist(),
                target_unit=scaling.target_unit.tolist(),
                beta=beta.tolist(),
                training_samples=N,
            )
        )

    def predict(self, data):
        x = (ridge_features(data) - self.feature_mean) / self.feature_std
        x = np.concatenate([np.ones_like(x[..., :1]), x], axis=-1)
        delta = (
            np.einsum("nhpd,hpd->nhp", x, self.beta, optimize=False) * self.target_unit
        )
        base = (
            data["anchor"]
            if self.state["name"] == "RR_BRES"
            else data["last_y"][:, None]
        )
        return base + delta
