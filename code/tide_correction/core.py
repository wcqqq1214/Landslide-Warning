"""Frozen bases, fully mature out-of-training paths and equal-capacity heads."""

import numpy as np
import torch
from torch import nn
from tide_fusion import core as fusion

prior, old, o, ROOT = fusion.prior, fusion.old, fusion.o, fusion.ROOT
B, BA = fusion.B, fusion.BA
CONFIG = ROOT / "config/ootang_tide_correction.v1_0.json"
SOURCES = ROOT / "docs/ootang_tide_correction_sources.v1.0.json"


def spec():
    return o.read_json(CONFIG)


def guard(implementation=True):
    files = o.read_json(SOURCES)["files"]
    for p, h in files.items():
        if o.sha(ROOT / p) != h:
            raise ValueError("Frozen source changed: " + p)
    if implementation:
        o.verify_lock(ROOT / spec()["out"] / "implementation_lock.json")
    return len(files)


def eligible(cfg, n):
    return np.arange(cfg["correction"]["first_oof_origin"], n - 293 + 1, dtype=int)


def base_prefix(cfg, m):
    return max(
        r
        for r in cfg["correction"]["refit_prefixes"]
        + cfg["correction"]["reused_oof_prefixes"]
        if r <= m
    )


def base_path(cfg, r, seed):
    if r in cfg["correction"]["refit_prefixes"]:
        return ROOT / cfg["out"] / f"historical_bases/fit_{r}/seed_{seed}/e400.pt"
    return ROOT / cfg["prior_feature_out"] / f"origin_{r}/TiDE_KIN/seed_{seed}/e400.pt"


def base_reload(path):
    ck = torch.load(path, map_location="cpu", weights_only=True)
    model = old.Tide(old.spec(), ck["seed"])
    model.load_state_dict(ck["state_dict"], strict=True)
    model.requires_grad_(False)
    model.eval()
    return model, ck["scaling"], ck


def base_arrays(teachers, y, forcing, ms, sc, current=False, targets=False):
    return prior.arrays(
        prior.spec(), teachers, y, forcing, ms, sc, "TiDE_KIN", current, targets
    )


def base_predict(model, sc, teachers, y, forcing, m):
    if len(y) != m or len(forcing) != m + 293 or sc["fit_prefix"] > m:
        raise ValueError("OOF predictor must receive exact legal history/scenario")
    a = base_arrays(teachers, y, forcing, [m], sc, current=True)
    with torch.no_grad():
        q = model(*a[:2]).numpy()[0]
    assert np.array_equal(q[0], np.zeros(4))
    return y[-1] + q[1:] * np.asarray(sc["unit"])


def raw_cov(teachers, ms):
    """Legal cached H states, relative distance and known mask; no labels."""
    out = np.zeros((len(ms), 4, 474, 5), dtype=np.float64)
    for j, m in enumerate(ms):
        ix = np.r_[np.arange(m - 180, m), m - 1 + np.arange(294)]
        t = teachers[o.teacher_id(int(m), True)]
        assert int(t["teacher_fit_prefix"]) <= m and ix[-1] < len(t["mean"])
        out[j, ..., :3] = old.physical_features(t)[ix, :, 2:].transpose(1, 0, 2)
        out[j, ..., 3] = (ix - (m - 1)) / 293
        out[j, ..., 4] = 1.0
    return out


def head_cov(raw, sc, arm):
    if arm not in spec()["new_arms"]:
        raise ValueError(arm)
    x = raw.copy()
    mean, std = (
        np.asarray(sc["physics_mean"])[:, 2:],
        np.asarray(sc["physics_std"])[:, 2:],
    )
    x[..., :3] = (x[..., :3] - mean[None, :, None, :]) / std[None, :, None, :]
    if arm == "TiDE_CAL":
        x[..., :3] = 0.0
    return x


def scaling(cfg, y, forcing, teacher):
    return fusion.scaling(cfg, y, forcing, teacher)


class Head(nn.Module):
    def __init__(self, cfg, seed, sc):
        super().__init__()
        f = cfg["fusion"]
        with torch.random.fork_rng():
            torch.manual_seed(seed + f["branch_seed_offset"])
            self.h_feature = old.ResidualDense(5, 8, 1)
            self.h_encoder = old.ResidualDense(478, 8, 8)
            self.h_decoder = old.ResidualDense(8, 8, 294)
            self.h_point = nn.Embedding(4, 4)
        for layer in (self.h_decoder.b, self.h_decoder.skip):
            nn.init.zeros_(layer.weight)
            nn.init.zeros_(layer.bias)
        self.register_buffer(
            "cap_norm",
            torch.tensor(np.asarray(sc["hydro_cap_mm"]) / np.asarray(sc["unit"])),
        )
        self.double()
        assert sum(p.numel() for p in self.parameters()) == 13179

    def parts(self, x):
        batch = x.shape[0]
        f = self.h_feature(x.reshape(batch * 4, 474, 5)).flatten(1)
        point = self.h_point(torch.arange(4).repeat(batch))
        raw = self.h_decoder(self.h_encoder(torch.cat([f, point], -1)))
        raw = (raw - raw[:, :1]).reshape(batch, 4, 294).transpose(1, 2)
        cap = self.cap_norm[None, None, :]
        return raw, cap * torch.tanh(raw / cap)

    def forward(self, x):
        return self.parts(x)[1]


def schedule(cfg, count, seed):
    if count <= 0:
        raise ValueError("No training schedule for an empty OOF pool")
    return np.random.default_rng(seed).integers(0, count, size=(cfg["updates"], 8))


def head_targets(cfg, y, ms, forecasts, sc):
    if not len(ms) or forecasts.shape != (len(ms), 293, 4):
        raise ValueError("Nonempty complete seed-matched OOF forecasts required")
    if ms.min() < 480 or ms.max() + 293 > len(y):
        raise ValueError("Incomplete path label or conditional-driver maturity")
    truth = np.stack([y[m : m + 293] for m in ms])
    return (truth - forecasts) / np.asarray(sc["unit"])


def checkpoint_folder(cfg, n, arm, seed):
    parent = cfg["prior_feature_out"] if arm == "TiDE_KIN" else cfg["out"]
    return ROOT / parent / f"origin_{n}/{arm}/seed_{seed}"


def final_step(n):
    return 0 if n == 612 else 400


def reload(path):
    ck = torch.load(path, map_location="cpu", weights_only=True)
    model = Head(spec(), ck["seed"], ck["scaling"])
    model.load_state_dict(ck["state_dict"], strict=True)
    model.eval()
    return model, ck["scaling"], ck


def pool(cfg, n):
    root = ROOT / cfg["out"] / "oof"
    chunks = []
    for p in sorted(root.glob("batch_*/lock.json")):
        meta = o.verify_lock(p)
        if meta["mature_prefix"] <= n:
            chunks.append(np.load(p.parent / "paths.npz"))
    if not chunks:
        return None
    ms = np.concatenate([p["origins"] for p in chunks])
    order = np.argsort(ms)
    return dict(
        origins=ms[order],
        base_prefixes=np.concatenate([p["base_prefixes"] for p in chunks])[order],
        raw_cov=np.concatenate([p["raw_cov"] for p in chunks])[order],
        seeds=np.concatenate([p["seeds"] for p in chunks], axis=1)[:, order],
    )
