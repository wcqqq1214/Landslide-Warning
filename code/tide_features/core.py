"""Keep the existing model and supervision; mask two physical feature groups."""

import numpy as np
import torch
from tide_direct import core as old

o = old.o
ROOT = old.ROOT
CONFIG = ROOT / "config/ootang_tide_features.v1_0.json"
SOURCES = ROOT / "docs/ootang_tide_features_sources.v1.0.json"
B, BA = old.B, old.BA
Tide = old.Tide
scaling = old.scaling
schedule = old.schedule
loss = old.loss


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


def arrays(cfg, teachers, y, forcing, origins, sc, arm, current=False, targets=False):
    if arm not in cfg["arms"]:
        raise ValueError(arm)
    mode = "TiDE_DATA" if arm == "TiDE_DATA" else "TiDE_PHYS"
    values = old.arrays(
        old.spec(), teachers, y, forcing, origins, sc, mode, current, targets
    )
    removed = [j for j in range(5, 10) if j not in cfg["masks"][arm]]
    values[1][..., removed] = 0.0
    return values


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


def checkpoint_folder(cfg, n, arm, seed):
    parent = cfg["prior_tide_out"] if arm in cfg["reuse_arms"] else cfg["out"]
    return ROOT / parent / f"origin_{n}" / arm / f"seed_{seed}"


def reload(path):
    ck = torch.load(path, map_location="cpu", weights_only=True)
    model = Tide(spec(), ck["seed"])
    model.load_state_dict(ck["state_dict"], strict=True)
    return model, ck["scaling"], ck


def factor_values(metric_by_arm, cfg):
    ordered = np.stack(
        [metric_by_arm[a] for a in cfg["reporting"]["factorial_arm_order"]]
    )
    return {
        name: np.einsum("a,a...->...", weights, ordered, optimize=False)
        for name, weights in cfg["reporting"]["factorial_effects"].items()
    }
