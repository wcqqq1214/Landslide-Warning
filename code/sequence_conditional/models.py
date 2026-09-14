"""Small causal models, with a real Mamba-1 selective state-space recurrence.

Mamba equations and special dt/A/D initialization follow state-spaces/mamba
at e9594ce1c732d97440f0332fdc43170a2294dbfa (Apache-2.0). Original source and
license are preserved in the experiment's sources directory. This is a local
CPU/float64 implementation, not the official fused CUDA implementation.
"""

import math

import torch
from torch import nn
from torch.nn import functional as F


def affine_scan(a, b):
    """Inclusive associative scan of h[t] = a[t]*h[t-1] + b[t], h[-1]=0."""
    if a.shape != b.shape or a.ndim != 4:
        raise ValueError("Scan expects batch, time, channel, state tensors")
    offset = 1
    while offset < a.shape[1]:
        old_a, old_b = a, b
        a = torch.cat(
            (old_a[:, :offset], old_a[:, offset:] * old_a[:, :-offset]), dim=1
        )
        b = torch.cat(
            (
                old_b[:, :offset],
                old_b[:, offset:] + old_a[:, offset:] * old_b[:, :-offset],
            ),
            dim=1,
        )
        offset *= 2
    return b


def selective_scan(u, dt, A, B, C, D, z):
    """Mamba real variable-B/C scan; dt is already positive (softplus)."""
    a = torch.exp(dt[..., None] * A)
    b = dt[..., None] * B[:, :, None, :] * u[..., None]
    state = affine_scan(a, b)
    return ((state * C[:, :, None, :]).sum(-1) + D * u) * F.silu(z)


class MambaOne(nn.Module):
    def __init__(self, cfg):
        super().__init__()
        d, n, inner = cfg["hidden"], cfg["d_state"], cfg["hidden"] * cfg["expand"]
        self.n, self.rank, self.kernel = n, cfg["dt_rank"], cfg["d_conv"]
        self.in_proj = nn.Linear(d, 2 * inner, bias=False)
        self.conv = nn.Conv1d(inner, inner, self.kernel, groups=inner, bias=True)
        self.x_proj = nn.Linear(inner, self.rank + 2 * n, bias=False)
        self.dt_proj = nn.Linear(self.rank, inner, bias=True)
        nn.init.uniform_(self.dt_proj.weight, -(self.rank**-0.5), self.rank**-0.5)
        dt = torch.exp(
            torch.rand(inner) * math.log(cfg["dt_max"] / cfg["dt_min"])
            + math.log(cfg["dt_min"])
        ).clamp(min=cfg["dt_init_floor"])
        with torch.no_grad():
            self.dt_proj.bias.copy_(dt + torch.log(-torch.expm1(-dt)))
        self.A_log = nn.Parameter(torch.arange(1, n + 1).float().log().repeat(inner, 1))
        self.D = nn.Parameter(torch.ones(inner))
        self.out_proj = nn.Linear(inner, d, bias=False)

    def forward(self, x):
        u, z = self.in_proj(x).chunk(2, dim=-1)
        u = F.silu(self.conv(F.pad(u.transpose(1, 2), (self.kernel - 1, 0))))
        u = u.transpose(1, 2)
        dt, b, c = self.x_proj(u).split((self.rank, self.n, self.n), dim=-1)
        dt = F.softplus(self.dt_proj(dt))
        mixed = selective_scan(u, dt, -torch.exp(self.A_log), b, c, self.D, z)
        return self.out_proj(mixed)


class MambaBlock(nn.Module):
    def __init__(self, cfg):
        super().__init__()
        self.norm = nn.LayerNorm(cfg["hidden"], eps=cfg["layer_norm_eps"])
        self.mixer = MambaOne(cfg)

    def forward(self, x):
        return x + self.mixer(self.norm(x))


class AttentionBlock(nn.Module):
    def __init__(self, cfg):
        super().__init__()
        d = cfg["hidden"]
        self.norm1 = nn.LayerNorm(d, eps=cfg["layer_norm_eps"])
        self.attention = nn.MultiheadAttention(
            d, cfg["heads"], dropout=0, batch_first=True
        )
        self.norm2 = nn.LayerNorm(d, eps=cfg["layer_norm_eps"])
        self.fc1 = nn.Linear(d, cfg["feedforward"])
        self.fc2 = nn.Linear(cfg["feedforward"], d)

    def forward(self, x, mask):
        z = self.norm1(x)
        x = x + self.attention(z, z, z, attn_mask=mask, need_weights=False)[0]
        return x + self.fc2(F.gelu(self.fc1(self.norm2(x))))


def positional_encoding(length, hidden, dtype, device):
    days = torch.arange(length, dtype=dtype, device=device)[:, None]
    frequencies = torch.exp(
        torch.arange(0, hidden, 2, dtype=dtype, device=device)
        * (-math.log(10000) / hidden)
    )
    angles = days * frequencies
    return torch.stack((torch.sin(angles), torch.cos(angles)), dim=-1).reshape(
        length, hidden
    )


class TrajectoryModel(nn.Module):
    def __init__(self, cfg, seed, arm):
        super().__init__()
        torch.manual_seed(seed)
        self.family = next(k for k, pair in cfg["families"].items() if arm in pair)
        if self.family == "TRANSFORMER":
            n = cfg["neural"]["transformer"]
            self.input = nn.Linear(cfg["neural"]["input_channels"], n["hidden"])
            self.blocks = nn.ModuleList([AttentionBlock(n) for _ in range(n["layers"])])
        else:
            n = cfg["neural"]["cnn_mamba"]
            self.kernel = n["cnn_kernel"]
            self.input = nn.Conv1d(
                cfg["neural"]["input_channels"], n["hidden"], self.kernel
            )
            self.blocks = nn.ModuleList([MambaBlock(n) for _ in range(n["layers"])])
        self.norm = nn.LayerNorm(n["hidden"], eps=n["layer_norm_eps"])
        self.head = nn.Linear(n["hidden"], 4)
        nn.init.zeros_(self.head.weight)
        nn.init.zeros_(self.head.bias)
        self.double()

    def forward(self, x):
        if x.ndim != 3 or x.shape[0] != 1 or x.shape[1] != 22:
            raise ValueError("Expected one complete 22-channel sequence")
        if self.family == "TRANSFORMER":
            z = self.input(x.transpose(1, 2))
            z = z + positional_encoding(z.shape[1], z.shape[2], z.dtype, z.device)
            mask = torch.ones(
                z.shape[1], z.shape[1], dtype=torch.bool, device=z.device
            ).triu(1)
            for block in self.blocks:
                z = block(z, mask)
        else:
            z = F.silu(self.input(F.pad(x, (self.kernel - 1, 0)))).transpose(1, 2)
            for block in self.blocks:
                z = block(z)
        return self.head(self.norm(z))[0]
