"""Legal node-local inputs, fixed graph GRU, and frozen episode supervision."""

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

from transformer_origin.core import (  # noqa: F401
    B,
    ROOT,
    check_deadline,
    draw_schedule,
    effect,
    event,
    load_npz,
    lock,
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

CONFIG = ROOT / "config/ootang_overnight_graph.v1_0.json"
SOURCES = ROOT / "docs/ootang_overnight_graph_sources.v1.0.json"


def spec():
    return read_json(CONFIG)


def guard():
    files = read_json(SOURCES)["files"]
    for name, h in files.items():
        assert sha(ROOT / name) == h, name
    return len(files)


def setup(cfg):
    torch.set_num_threads(cfg["threads"])
    torch.use_deterministic_algorithms(True)


def labels(cfg, n, purpose):
    event(ROOT / cfg["out"], "label_prefix_read", rows=n, purpose=purpose)
    return read_labels(ROOT / cfg["data"], n)


def bank(cfg):
    old = ROOT / cfg["prior_out"]
    temporal = ROOT / cfg["teacher_cache_prior_out"] / "implementation_verification"
    return {
        432: load_npz(old / "preflight/teacher_432.npz"),
        612: load_npz(old / "preflight/teacher_612.npz"),
        792: load_npz(temporal / "teacher_972.npz"),
        1168: load_npz(temporal / "teacher_1168.npz"),
    }


def teacher_id(n, current=False):
    if current and n == 1168:
        return 1168
    if n < 432:
        raise ValueError("no legal teacher")
    return max(p for p in [432, 612, 792] if p <= n)


def own_physics(teacher):
    x = teacher["x"]
    return np.stack(
        [x[:, [p, 4 + p, 8, 9, 10, 11, 12, 13 + p, 17 + p, 21]] for p in range(4)],
        axis=1,
    )


def history_raw(teacher, y):
    n = len(y)
    physical = own_physics(teacher)[:n]
    r = y - teacher["mean"][:n]
    extra = np.stack(
        [
            y - y[0],
            np.diff(y, axis=0, prepend=y[:1]),
            r,
            np.diff(r, axis=0, prepend=r[:1]),
        ],
        axis=-1,
    )
    return np.concatenate([physical, extra], axis=-1)


def scaling(teacher, y, cfg):
    raw = history_raw(teacher, y)
    return dict(
        mean=raw.mean(0).tolist(),
        std=np.maximum(raw.std(0), cfg["normalization"]["x_std_floor"]).tolist(),
        unit=np.maximum(y.std(0), cfg["normalization"]["target_std_floor_mm"]).tolist(),
        y0=y[0].tolist(),
        fit_prefix=len(y),
    )


def inputs(teachers, y, origins, horizons, scale, current=False):
    origins, horizons = np.asarray(origins), np.asarray(horizons)
    assert horizons.ndim == 2 and horizons.shape[0] == len(origins)
    if (
        origins.min() < 432
        or origins.max() > len(y)
        or horizons.min() < 1
        or horizons.max() > 293
    ):
        raise ValueError("illegal observation/query bounds")
    length = int(origins.max())
    hist = np.zeros((len(origins), 4, length, 14))
    future = np.zeros((*horizons.shape, 4, 10))
    avg, std = np.asarray(scale["mean"]), np.asarray(scale["std"])
    for i, m in enumerate(origins):
        teacher = teachers[teacher_id(m, current)]
        ix = m + horizons[i] - 1
        assert ix.max() < len(teacher["mean"])
        raw = history_raw(teacher, y[:m])
        hist[i, :, :m] = ((raw - avg) / std).transpose(1, 0, 2)
        future[i] = (own_physics(teacher)[ix] - avg[:, :10]) / std[:, :10]
    distances = np.stack([horizons / 293, np.log1p(horizons) / np.log(294)], axis=-1)
    return tuple(
        torch.from_numpy(a) for a in [hist, origins.astype(np.int64), future, distances]
    )


def ema30(r):
    slow = np.empty_like(r)
    slow[0] = r[0]
    for t in range(1, len(r)):
        slow[t] = (2 / 31) * r[t] + (29 / 31) * slow[t - 1]
    return slow


def target_bank(teachers, y):
    output = {}
    for tid in [432, 612, 792]:
        count = min(len(y), len(teachers[tid]["mean"]))
        r = y[:count] - teachers[tid]["mean"][:count]
        slow = ema30(r)
        output[tid] = np.stack([slow, r - slow], axis=-1)
    return output


def targets(cache, origins, horizons, scale, dual=False):
    values = []
    for m, hs in zip(origins, horizons):
        ix = m + hs - 1
        c = cache[teacher_id(m)]
        if ix.max() >= scale["fit_prefix"] or ix.max() >= len(c):
            raise ValueError("unmatured target")
        v = c[ix] / np.asarray(scale["unit"])[None, :, None]
        values.append(v if dual else v.sum(-1, keepdims=True))
    return torch.tensor(np.array(values), dtype=torch.float64)


class GraphGRU(nn.Module):
    def __init__(self, cfg, seed, arm):
        super().__init__()
        if arm not in cfg["arms"] + [cfg["conditional_arm"]]:
            raise ValueError(arm)
        torch.manual_seed(seed)
        self.arm = arm
        self.dual = arm == cfg["conditional_arm"]
        self.gru = nn.GRU(14, 8, batch_first=True)
        self.message = nn.Linear(8, 8)
        self.point = nn.Embedding(4, 4)
        self.decode = nn.Linear(34, 16)
        self.head = nn.Linear(16, 2 if self.dual else 1)
        nn.init.zeros_(self.head.weight)
        nn.init.zeros_(self.head.bias)
        a = torch.eye(4, dtype=torch.float64)
        if arm != "GRU_LOCAL":
            a[2, 3] = a[3, 2] = 1
            a = a / a.sum(-1, keepdim=True)
        self.register_buffer("adjacency", a)
        self.double()

    def forward(self, hist, lengths, future, distances):
        b, p, t, _ = hist.shape
        sequence = hist.reshape(b * p, t, 14)
        states, _ = self.gru(sequence)
        h = states[torch.arange(b * p), lengths.repeat_interleave(p) - 1].reshape(
            b, p, 8
        )
        context = torch.tanh(
            self.message(torch.einsum("pq,bqk->bpk", self.adjacency, h))
        )
        last = hist[
            torch.arange(b)[:, None], torch.arange(4)[None, :], (lengths - 1)[:, None]
        ][..., [11, 12]]
        queries = future.shape[1]
        z = torch.cat(
            [
                h[:, None].expand(-1, queries, -1, -1),
                context[:, None].expand(-1, queries, -1, -1),
                future,
                last[:, None].expand(-1, queries, -1, -1),
                distances[:, :, None].expand(-1, -1, 4, -1),
                self.point(torch.arange(4))[None, None].expand(b, queries, -1, -1),
            ],
            dim=-1,
        )
        return self.head(F.gelu(self.decode(z)))


def predict(model, teachers, y, n, end, scale):
    model.eval()
    with torch.no_grad():
        c = model(
            *inputs(teachers, y, [n], np.arange(1, end - n + 1)[None], scale, True)
        ).numpy()[0]
    mean = teachers[teacher_id(n, True)]["mean"][n:end] + c.sum(-1) * scale["unit"]
    if not np.isfinite(mean).all():
        raise ArithmeticError("nonfinite forecast")
    return mean, c * np.asarray(scale["unit"])[None, :, None]


def reload(path, cfg):
    saved = torch.load(path, map_location="cpu", weights_only=True)
    model = GraphGRU(cfg, saved["seed"], saved["arm"])
    model.load_state_dict(saved["state_dict"], strict=True)
    return model, saved["scaling"], saved


def verify_implementation(cfg):
    guard()
    for name, h in read_json(ROOT / cfg["out"] / "implementation_lock.json")[
        "files"
    ].items():
        assert sha(ROOT / name) == h, name
