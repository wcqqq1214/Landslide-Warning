"""One small dense path model; paired availability of physical covariates."""

import numpy as np
import torch
from torch import nn
from overnight_graph import core as o

ROOT = o.ROOT
CONFIG = ROOT / "config/ootang_tide_direct.v1_0.json"
SOURCES = ROOT / "docs/ootang_tide_direct_sources.v1.0.json"
B = "BPLUS_CONTINUOUS"
BA = "BPLUS_ORIGIN_ANCHOR"


def spec():
    return o.read_json(CONFIG)


def guard(implementation=True):
    files = o.read_json(SOURCES)["files"]
    for path, digest in files.items():
        if o.sha(ROOT / path) != digest:
            raise ValueError("Frozen source changed: " + path)
    if implementation:
        o.verify_lock(ROOT / spec()["out"] / "implementation_lock.json")
    return len(files)


def driver_features(forcing):
    rain, level = forcing.T
    total = np.r_[0.0, np.cumsum(rain)]
    ends = np.arange(1, len(rain) + 1)
    return np.column_stack(
        [
            rain,
            level,
            np.diff(level, prepend=level[0]),
            total[ends] - total[np.maximum(0, ends - 7)],
            total[ends] - total[np.maximum(0, ends - 30)],
        ]
    )


def physical_features(teacher):
    return o.own_physics(teacher)[:, :, [0, 1, 7, 8, 9]]


def scaling(cfg, y, forcing, teacher):
    if len(forcing) != len(y):
        raise ValueError("Training prefix required for every scaler")
    d = driver_features(forcing)
    p = physical_features(teacher)[: len(y)]
    floor = cfg["normalization"]["x_std_floor"]
    return dict(
        fit_prefix=len(y),
        unit=np.maximum(y.std(0), 1.0).tolist(),
        driver_mean=d.mean(0).tolist(),
        driver_std=np.maximum(d.std(0), floor).tolist(),
        physics_mean=p.mean(0).tolist(),
        physics_std=np.maximum(p.std(0), floor).tolist(),
    )


def schedule(cfg, n, seed):
    return np.random.default_rng(seed).integers(
        cfg["training"]["first_origin"],
        n,
        size=(cfg["updates"], cfg["training"]["batch_origins"]),
    )


def arrays(cfg, teachers, y, forcing, origins, sc, arm, current=False, targets=False):
    """Only prefix y is accepted; future features outside it are masked in fitting."""
    if arm not in cfg["arms"]:
        raise ValueError(arm)
    ms = np.asarray(origins, dtype=np.int64)
    n = len(y)
    limit = len(forcing) if current else n
    if ms.ndim != 1 or ms.min() < 432 or ms.max() > n or len(forcing) < n:
        raise ValueError("Illegal origin/prefix")
    if targets and (current or ms.max() >= n):
        raise ValueError("Targets require mature training examples")
    length, slots = cfg["network"]["lookback"], cfg["network"]["output_slots"]
    hist = np.zeros((len(ms), 4, length), np.float64)
    cov = np.zeros((len(ms), 4, length + slots, 12), np.float64)
    target = np.zeros((len(ms), slots - 1, 4), np.float64)
    mask = np.zeros((len(ms), slots - 1), np.float64)
    raw = driver_features(forcing[:limit])
    normalized = (raw - sc["driver_mean"]) / sc["driver_std"]
    unit = np.asarray(sc["unit"])
    for i, m in enumerate(ms):
        ix = np.r_[np.arange(m - length, m), m - 1 + np.arange(slots)]
        known = ix < limit
        hist[i] = ((y[m - length : m] - y[m - 1]) / unit).T
        cov[i, :, known, :5] = np.broadcast_to(
            normalized[ix[known], None, :], (known.sum(), 4, 5)
        )
        if arm == "TiDE_PHYS":
            tid = o.teacher_id(int(m), current)
            t = teachers[tid]
            if int(t["teacher_fit_prefix"]) > m or ix[known].max() >= len(t["mean"]):
                raise ValueError("Illegal teacher prefix or coverage")
            physical = (physical_features(t)[ix[known]] - sc["physics_mean"]) / sc[
                "physics_std"
            ]
            cov[i, :, known, 5:10] = physical
        cov[i, :, :, 10] = (ix - (m - 1)) / 293
        cov[i, :, :, 11] = known.astype(float)
        if targets:
            mature = min(slots - 1, n - int(m))
            target[i, :mature] = (y[m : m + mature] - y[m - 1]) / unit
            mask[i, :mature] = 1.0
    return tuple(torch.from_numpy(a) for a in (hist, cov, target, mask))


class ResidualDense(nn.Module):
    """TiDE residual dense block, independently implemented in PyTorch."""

    def __init__(self, incoming, hidden, outgoing):
        super().__init__()
        self.a = nn.Linear(incoming, hidden)
        self.b = nn.Linear(hidden, outgoing)
        self.skip = nn.Linear(incoming, outgoing)

    def forward(self, x):
        return self.b(torch.relu(self.a(x))) + self.skip(x)


class Tide(nn.Module):
    def __init__(self, cfg, seed):
        super().__init__()
        torch.manual_seed(seed)
        n = cfg["network"]
        self.length, self.slots = n["lookback"], n["output_slots"]
        self.feature = ResidualDense(12, n["feature_hidden"], n["feature_output"])
        incoming = (
            self.length
            + (self.length + self.slots) * n["feature_output"]
            + n["point_embedding"]
        )
        self.encoder = ResidualDense(incoming, n["encoder_hidden"], n["encoder_output"])
        self.decoder = ResidualDense(
            n["encoder_output"],
            n["decoder_hidden"],
            self.slots * n["decoder_output_per_horizon"],
        )
        self.temporal = ResidualDense(
            n["feature_output"] + n["decoder_output_per_horizon"],
            n["temporal_hidden"],
            1,
        )
        self.linear = nn.Linear(self.length, self.slots)
        self.point = nn.Embedding(4, n["point_embedding"])
        for layer in (self.temporal.b, self.temporal.skip, self.linear):
            nn.init.zeros_(layer.weight)
            nn.init.zeros_(layer.bias)
        self.double()
        count = sum(p.numel() for p in self.parameters())
        if count != cfg["parameters"] or count > n["max_parameters"]:
            raise ValueError(
                f"Parameter count {count} differs from frozen specification"
            )

    def forward(self, hist, cov):
        batch = hist.shape[0]
        y = hist.reshape(batch * 4, self.length)
        x = cov.reshape(batch * 4, self.length + self.slots, 12)
        f = self.feature(x)
        point = self.point(torch.arange(4).repeat(batch))
        encoded = self.encoder(torch.cat([y, f.flatten(1), point], dim=-1))
        decoded = self.decoder(encoded).reshape(batch * 4, self.slots, -1)
        raw = self.temporal(torch.cat([decoded, f[:, self.length :]], dim=-1)).squeeze(
            -1
        ) + self.linear(y)
        anchored = raw - raw[:, :1]
        return anchored.reshape(batch, 4, self.slots).transpose(1, 2)


def loss(model, batch):
    hist, cov, target, mask = batch
    q = model(hist, cov)[:, 1:]
    mse = ((q - target).square().mean(-1) * mask).sum(-1) / mask.sum(-1)
    return mse.mean()


def predict(cfg, model, teachers, y, forcing, n, end, sc, arm):
    if len(y) != n or len(forcing) != end or end - n != 293:
        raise ValueError("Exact issue prefix and full 293-day scenario required")
    v = arrays(cfg, teachers, y, forcing, [n], sc, arm, current=True)
    model.eval()
    with torch.no_grad():
        q = model(*v[:2]).numpy()[0]
    assert np.array_equal(q[0], np.zeros(4))
    change = q[1:] * np.asarray(sc["unit"])
    mean = y[-1] + change
    if not np.isfinite(mean).all():
        raise ArithmeticError("Nonfinite issued path")
    return mean, change


def reload(path):
    ck = torch.load(path, map_location="cpu", weights_only=True)
    model = Tide(spec(), ck["seed"])
    model.load_state_dict(ck["state_dict"], strict=True)
    return model, ck["scaling"], ck
