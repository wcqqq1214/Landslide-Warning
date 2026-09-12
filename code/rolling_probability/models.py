"""Small genuine convolutional LSTM with a direct conditional Gaussian decoder."""

import math
import numpy as np
import torch
from torch import nn


class Scaling:
    def __init__(self, data=None, state=None):
        if state is not None:
            self.__dict__.update({k: np.asarray(v) for k, v in state.items()})
            return
        self.x_mean = data["x"].mean(axis=(0, 1, 3), keepdims=True)
        self.x_std = np.maximum(data["x"].std(axis=(0, 1, 3), keepdims=True), 1e-4)
        self.z_mean = data["z"].mean(axis=(0, 1, 3), keepdims=True)
        self.z_std = np.maximum(data["z"].std(axis=(0, 1, 3), keepdims=True), 1e-4)
        self.target_scale = np.maximum(
            np.sqrt(np.mean((data["y"] - data["anchor"]) ** 2, axis=0)), 0.01
        )

    def state(self):
        return {k: v.tolist() for k, v in self.__dict__.items()}

    def transform(self, x, z):
        return ((x - self.x_mean) / self.x_std).astype(np.float32), (
            (z - self.z_mean) / self.z_std
        ).astype(np.float32)


class DirectConvLSTM(nn.Module):
    def __init__(self, x_channels, z_channels, hidden=16):
        super().__init__()
        self.hidden = hidden
        self.gates = nn.Conv1d(x_channels + hidden, 4 * hidden, 3, padding=1)
        self.decoder = nn.Sequential(
            nn.Conv1d(hidden + z_channels, hidden, 1),
            nn.Tanh(),
            nn.Conv1d(hidden, 2, 1),
        )
        for layer in self.modules():
            if isinstance(layer, nn.Conv1d):
                nn.init.xavier_uniform_(layer.weight)
                nn.init.zeros_(layer.bias)
        nn.init.zeros_(self.decoder[-1].weight)
        nn.init.zeros_(self.decoder[-1].bias)

    def forward(self, x, z):
        h = x.new_zeros((len(x), self.hidden, 4))
        c = torch.zeros_like(h)
        for row in x.unbind(dim=1):
            i, f, o, g = self.gates(torch.cat([row, h], dim=1)).chunk(4, dim=1)
            c = torch.sigmoid(f) * c + torch.sigmoid(i) * torch.tanh(g)
            h = torch.sigmoid(o) * torch.tanh(c)
        H = z.shape[1]
        context = h[:, None].expand(-1, H, -1, -1)
        d = self.decoder(
            torch.cat([context, z], dim=2).reshape(len(x) * H, -1, 4)
        ).reshape(len(x), H, 2, 4)
        return d[:, :, 0], torch.exp(4 * torch.tanh(d[:, :, 1] / 4))


def gaussian_crps_torch(mu, sigma, target):
    z = (target - mu) / sigma
    return sigma * (
        z * torch.erf(z / math.sqrt(2))
        + math.sqrt(2 / math.pi) * torch.exp(-0.5 * z * z)
        - 1 / math.sqrt(math.pi)
    )


def objective(mu, sigma, target, mse_weight=0.25):
    return (
        gaussian_crps_torch(mu, sigma, target) + mse_weight * (mu - target).square()
    ).mean()


@torch.no_grad()
def predict(models, scaling, x, z, anchor):
    x, z = scaling.transform(x, z)
    x, z = torch.from_numpy(x), torch.from_numpy(z)
    H = z.shape[1]
    scale = scaling.target_scale[None, :H]
    means, sigmas = [], []
    for model in models:
        model.eval()
        delta, sigma = model(x, z)
        means.append(anchor + delta.numpy().astype(float) * scale)
        sigmas.append(np.maximum(0.01, sigma.numpy().astype(float) * scale))
    means, sigmas = np.stack(means), np.stack(sigmas)
    mean = means.mean(axis=0)
    # Centered computation avoids cancellation from large cumulative displacement.
    variance = (sigmas**2 + (means - mean[None]) ** 2).mean(axis=0)
    return mean, np.sqrt(variance), means, sigmas
