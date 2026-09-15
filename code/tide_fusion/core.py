"""Separate K and H encoding with a paired, training-only amplitude transform."""

import numpy as np
import torch
from torch import nn
from tide_features import core as prior

old = prior.old
o, ROOT = old.o, old.ROOT
B, BA = old.B, old.BA
CONFIG = ROOT / "config/ootang_tide_fusion.v1_0.json"
SOURCES = ROOT / "docs/ootang_tide_fusion_sources.v1.0.json"
schedule, loss = old.schedule, old.loss


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


def scaling(cfg, y, forcing, teacher):
    sc = old.scaling(cfg, y, forcing, teacher)
    lag = cfg["fusion"]["cap_lag_days"]
    if len(y) <= lag:
        raise ValueError("Insufficient prefix for fixed cap")
    sc["hydro_cap_mm"] = np.maximum(
        np.sqrt(np.mean((y[lag:] - y[:-lag]) ** 2, axis=0)),
        cfg["fusion"]["cap_floor_mm"],
    ).tolist()
    return sc


def arrays(cfg, teachers, y, forcing, origins, sc, arm, current=False, targets=False):
    if arm in cfg["reuse_arms"]:
        return prior.arrays(
            prior.spec(), teachers, y, forcing, origins, sc, arm, current, targets
        )
    if arm not in cfg["new_arms"]:
        raise ValueError(arm)
    return old.arrays(
        old.spec(), teachers, y, forcing, origins, sc, "TiDE_PHYS", current, targets
    )


class Tide(nn.Module):
    def __init__(self, cfg, seed, arm, sc):
        super().__init__()
        if arm not in cfg["new_arms"]:
            raise ValueError(arm)
        self.bounded = arm == cfg["fusion"]["bounded_arm"]
        self.length = cfg["network"]["lookback"]
        self.slots = cfg["network"]["output_slots"]
        self.main = old.Tide(old.spec(), seed)
        f = cfg["fusion"]
        with torch.random.fork_rng():
            torch.manual_seed(seed + f["branch_seed_offset"])
            self.h_feature = old.ResidualDense(5, f["hidden"], f["projected"])
            incoming = (self.length + self.slots) * f["projected"] + f[
                "point_embedding"
            ]
            self.h_encoder = old.ResidualDense(incoming, f["hidden"], f["hidden"])
            self.h_decoder = old.ResidualDense(f["hidden"], f["hidden"], self.slots)
            self.h_point = nn.Embedding(4, f["point_embedding"])
        for layer in (self.h_decoder.b, self.h_decoder.skip):
            nn.init.zeros_(layer.weight)
            nn.init.zeros_(layer.bias)
        self.register_buffer(
            "cap_norm",
            torch.tensor(np.asarray(sc["hydro_cap_mm"]) / np.asarray(sc["unit"])),
        )
        self.double()
        count = sum(p.numel() for p in self.parameters())
        if count != cfg["parameters"] or count > cfg["network"]["max_parameters"]:
            raise ValueError(
                f"Parameter count {count} differs from frozen specification"
            )

    def parts(self, hist, cov):
        main_cov = cov.clone()
        main_cov[..., 7:10] = 0
        main = self.main(hist, main_cov)
        batch = hist.shape[0]
        h = cov[..., [7, 8, 9, 10, 11]].reshape(batch * 4, self.length + self.slots, 5)
        feature = self.h_feature(h).flatten(1)
        point = self.h_point(torch.arange(4).repeat(batch))
        raw = self.h_decoder(self.h_encoder(torch.cat([feature, point], -1)))
        anchored = (raw - raw[:, :1]).reshape(batch, 4, self.slots).transpose(1, 2)
        cap = self.cap_norm[None, None, :]
        actual = cap * torch.tanh(anchored / cap) if self.bounded else anchored
        return main, anchored, actual

    def forward(self, hist, cov):
        main, _, hydro = self.parts(hist, cov)
        return main + hydro


def make_model(cfg, seed, arm, sc):
    return (
        Tide(cfg, seed, arm, sc)
        if arm in cfg["new_arms"]
        else old.Tide(old.spec(), seed)
    )


def checkpoint_folder(cfg, n, arm, seed):
    parent = cfg["out"]
    if arm == "TiDE_KIN":
        parent = cfg["prior_feature_out"]
    elif arm == "TiDE_PHYS":
        parent = cfg["prior_tide_out"]
    return ROOT / parent / f"origin_{n}" / arm / f"seed_{seed}"


def checkpoint_config(arm):
    return (
        prior.CONFIG
        if arm == "TiDE_KIN"
        else old.CONFIG
        if arm == "TiDE_PHYS"
        else CONFIG
    )


def reload(path):
    ck = torch.load(path, map_location="cpu", weights_only=True)
    model = make_model(spec(), ck["seed"], ck["arm"], ck["scaling"])
    model.load_state_dict(ck["state_dict"], strict=True)
    return model, ck["scaling"], ck


def predict(cfg, model, teachers, y, forcing, n, end, sc, arm):
    if len(y) != n or len(forcing) != end or end - n != 293:
        raise ValueError("Exact issue prefix/full conditional scenario required")
    v = arrays(cfg, teachers, y, forcing, [n], sc, arm, current=True)
    model.eval()
    with torch.no_grad():
        q = model(*v[:2]).numpy()[0]
    assert np.array_equal(q[0], np.zeros(4))
    change = q[1:] * np.asarray(sc["unit"])
    mu = y[-1] + change
    if not np.isfinite(mu).all():
        raise ArithmeticError("Nonfinite issued path")
    return mu, change


def components(cfg, model, teachers, y, forcing, n, sc, arm):
    v = arrays(cfg, teachers, y, forcing, [n], sc, arm, current=True)
    with torch.no_grad():
        parts = model.parts(*v[:2])
    return {
        name: value.numpy()[0, 1:] * np.asarray(sc["unit"])
        for name, value in zip(["main", "hydro_raw", "hydro"], parts)
    }
