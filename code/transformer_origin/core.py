"""Legal episode interfaces and a small history cross-attention decoder."""

import math

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

from transformer_temporal.core import (  # noqa: F401
    ROOT,
    B,
    array_sha,
    check_deadline,
    effect,
    event,
    load_npz,
    lock,
    physical_trajectory,
    read_forcing,
    read_json,
    read_labels,
    scores,
    sha,
    summarize,
    utc,
    verify_lock,
    write_json,
)

CONFIG = ROOT / "config/ootang_transformer_origin.v1_0.json"
SOURCES = ROOT / "docs/ootang_transformer_origin_sources.v1.0.json"


def spec():
    return read_json(CONFIG)


def guard():
    files = read_json(SOURCES)["files"]
    for name, digest in files.items():
        assert sha(ROOT / name) == digest, name
    return len(files)


def setup(cfg):
    torch.set_num_threads(cfg["threads"])
    torch.use_deterministic_algorithms(True)


def labels(cfg, n, purpose):
    event(ROOT / cfg["out"], "label_prefix_read", rows=n, purpose=purpose)
    return read_labels(ROOT / cfg["data"], n)


def teacher_for(origin):
    legal = [n for n in (432, 612, 792) if n <= origin]
    if not legal:
        raise ValueError("No legal frozen teacher")
    return max(legal)


def bank(cfg):
    pre = ROOT / cfg["out"] / "preflight"
    old = ROOT / cfg["prior_out"] / "implementation_verification"
    return {
        432: load_npz(pre / "teacher_432.npz"),
        612: load_npz(pre / "teacher_612.npz"),
        792: load_npz(old / "teacher_972.npz"),
        1168: load_npz(old / "teacher_1168.npz"),
    }


def current_teacher(teachers, n):
    return teachers[1168 if n == 1168 else teacher_for(n)]


def scaling(data, y, cfg):
    n = len(y)
    history = np.column_stack([data["x"][:n], y - y[0], y - data["mean"][:n]])
    floor = cfg["normalization"]
    sd = np.maximum(history.std(0), floor["x_std_floor"])
    return dict(
        mean=history.mean(0).tolist(),
        std=sd.tolist(),
        unit=np.maximum(y.std(0), floor["target_std_floor_mm"]).tolist(),
        y0=y[0].tolist(),
        fit_prefix=n,
    )


def inputs(teachers, y_prefix, origins, horizons, scale, current=False):
    """Reads observations strictly before each origin; future labels are not inputs."""
    origins, horizons = np.asarray(origins), np.asarray(horizons)
    if horizons.ndim != 2 or horizons.shape[0] != len(origins):
        raise ValueError("Expected origin-by-distance query indices")
    size = int(origins.max())
    hist = np.zeros((len(origins), size, 30), np.float64)
    future = np.zeros((*horizons.shape, 22), np.float64)
    valid = np.zeros(hist.shape[:2], bool)
    pos = np.arange(size)[None, :] - origins[:, None]
    mean, std = np.asarray(scale["mean"]), np.asarray(scale["std"])
    for b, m in enumerate(origins):
        if (
            not 432 <= m <= len(y_prefix)
            or np.any(horizons[b] < 1)
            or np.any(horizons[b] > 293)
        ):
            raise ValueError("Illegal origin or horizon")
        data = (
            current_teacher(teachers, int(m)) if current else teachers[teacher_for(m)]
        )
        ix = m + horizons[b] - 1
        if ix.max() >= len(data["mean"]):
            raise ValueError("Teacher rollout does not cover target")
        raw = np.column_stack(
            [data["x"][:m], y_prefix[:m] - scale["y0"], y_prefix[:m] - data["mean"][:m]]
        )
        hist[b, :m] = (raw - mean) / std
        future[b] = (data["x"][ix] - mean[:22]) / std[:22]
        valid[b, :m] = True
    return tuple(torch.from_numpy(a) for a in (hist, future, valid, pos, horizons - 1))


def targets(teachers, y_prefix, origins, horizons, scale):
    result = []
    for m, hs in zip(origins, horizons):
        ix = m + hs - 1
        if ix.max() >= len(y_prefix):
            raise ValueError("Unmatured training label")
        result.append(
            (y_prefix[ix] - teachers[teacher_for(m)]["mean"][ix]) / scale["unit"]
        )
    return torch.tensor(np.array(result), dtype=torch.float64)


def draw_schedule(n, seed, cfg):
    rng = np.random.default_rng(seed)
    tc = cfg["training"]
    origins = rng.integers(
        tc["first_origin"], n, size=(cfg["updates"], tc["batch_origins"])
    )
    distances = np.empty((*origins.shape, tc["distances_per_origin"]), dtype=np.int64)
    for i in range(len(origins)):
        for b, m in enumerate(origins[i]):
            distances[i, b] = rng.integers(
                1, min(293, n - m) + 1, tc["distances_per_origin"]
            )
    return origins, distances


def position(index, hidden=16):
    freq = torch.exp(
        torch.arange(0, hidden, 2, dtype=torch.float64) * (-math.log(10000) / hidden)
    )
    angles = index[..., None] * freq
    return torch.stack([torch.sin(angles), torch.cos(angles)], -1).flatten(-2)


class OriginModel(nn.Module):
    def __init__(self, cfg, seed, arm):
        super().__init__()
        if arm not in cfg["arms"]:
            raise ValueError(arm)
        torch.manual_seed(seed)
        self.arm, self.heads = arm, cfg["heads"]
        d = cfg["hidden"]
        self.history = nn.Linear(30, d)
        self.future = nn.Linear(22, d)
        self.norm_h = nn.LayerNorm(d)
        self.norm_q = nn.LayerNorm(d) if arm != "POOL_MLP" else nn.Identity()
        self.value, self.proj = nn.Linear(d, d), nn.Linear(d, d)
        self.norm_ff = nn.LayerNorm(d)
        self.ff1, self.ff2 = (
            nn.Linear(d, cfg["feedforward"]),
            nn.Linear(cfg["feedforward"], d),
        )
        self.norm_out, self.head = nn.LayerNorm(d), nn.Linear(d, 4)
        # Optional q/k are initialized last so every common parameter matches across arms.
        if arm != "POOL_MLP":
            self.query, self.key = nn.Linear(d, d), nn.Linear(d, d)
        nn.init.zeros_(self.head.weight)
        nn.init.zeros_(self.head.bias)
        self.double()

    def forward(self, hist, future, valid, hp, fp):
        if self.arm == "NO_OBS_ATTN":
            hist = torch.cat([hist[..., :22], torch.zeros_like(hist[..., 22:])], -1)
        h = self.norm_h(F.gelu(self.history(hist)) + position(hp))
        q0 = F.gelu(self.future(future)) + position(fp)
        q = self.norm_q(q0)
        v = self.value(h)
        if self.arm == "POOL_MLP":
            context = (v * valid[..., None]).sum(1, keepdim=True) / valid.sum(1)[
                :, None, None
            ]
        else:
            b, t, d = q.shape
            klen, heads = h.shape[1], self.heads
            qq = self.query(q).reshape(b, t, heads, d // heads).transpose(1, 2)
            kk = self.key(h).reshape(b, klen, heads, d // heads).transpose(1, 2)
            vv = v.reshape(b, klen, heads, d // heads).transpose(1, 2)
            score = (qq @ kk.transpose(-2, -1)) / math.sqrt(d // heads)
            weight = score.masked_fill(~valid[:, None, None, :], -torch.inf).softmax(-1)
            context = (weight @ vv).transpose(1, 2).reshape(b, t, d)
        z = q0 + self.proj(context)
        z = z + self.ff2(F.gelu(self.ff1(self.norm_ff(z))))
        return self.head(self.norm_out(z))


def predict(model, teachers, y, n, end, scale):
    hs = np.arange(1, end - n + 1)[None, :]
    model.eval()
    with torch.no_grad():
        r = model(*inputs(teachers, y, [n], hs, scale, current=True)).numpy()[0]
    result = current_teacher(teachers, n)["mean"][n:end] + r * scale["unit"]
    if not np.isfinite(result).all():
        raise ArithmeticError("Nonfinite issued forecast")
    return result


def reload(path, cfg):
    saved = torch.load(path, map_location="cpu", weights_only=True)
    model = OriginModel(cfg, saved["seed"], saved["arm"])
    model.load_state_dict(saved["state_dict"], strict=True)
    return model, saved["scaling"], saved


def verify_implementation(cfg):
    guard()
    for name, digest in read_json(ROOT / cfg["out"] / "implementation_lock.json")[
        "files"
    ].items():
        assert sha(ROOT / name) == digest, name
