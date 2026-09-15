"""Fixed checkpoint inputs and explicit horizon-weight accounting."""

import numpy as np
import torch
from tide_features import core as prior

o, ROOT = prior.o, prior.ROOT
CONFIG = ROOT / "config/ootang_tide_kin_diagnostic.v1_0.json"
SOURCES = ROOT / "docs/ootang_tide_kin_diagnostic_sources.v1.0.json"


def spec():
    return o.read_json(CONFIG)


def guard(implementation=True):
    files = o.read_json(SOURCES)["files"]
    for path, digest in files.items():
        assert o.sha(ROOT / path) == digest, path
    lock = ROOT / spec()["out"] / "implementation_lock.json"
    if implementation:
        o.verify_lock(lock)
    return len(files)


def folder(cfg, n, seed):
    return ROOT / cfg["prior_out"] / f"origin_{n}/TiDE_KIN/seed_{seed}"


def reload(cfg, n, seed, step):
    model, sc, ck = prior.reload(folder(cfg, n, seed) / f"e{step}.pt")
    model.requires_grad_(False)
    model.eval()
    return model, sc, ck


def inputs(teachers, y, forcing, n, sc, origins=None, visible=293):
    if len(y) != n:
        raise ValueError("Exact fitted observation prefix required")
    current = origins is None
    return prior.arrays(
        prior.spec(),
        teachers,
        y,
        forcing[: n + visible] if current else forcing[:n],
        [n] if current else origins,
        sc,
        "TiDE_KIN",
        current=current,
        targets=not current,
    )


def model_forward(model, data, batch=32):
    with torch.no_grad():
        return np.concatenate(
            [
                model(data[0][i : i + batch], data[1][i : i + batch]).numpy()
                for i in range(0, len(data[0]), batch)
            ]
        )


def supervision(n, schedule, step):
    origins = np.arange(432, n)
    lengths = np.minimum(293, n - origins)
    supported = np.arange(1, 294)[None, :] <= lengths[:, None]
    expected = (supported / lengths[:, None]).mean(0)
    flat = schedule[:step].ravel()
    if step:
        actual = supported[flat - 432]
        weight = (actual / lengths[flat - 432, None]).mean(0)
        draws = actual.sum(0)
        unique = supported[np.unique(flat) - 432].sum(0)
        updates = actual.reshape(step, 8, 293).any(1).sum(0)
    else:
        weight = np.zeros(293)
        draws = unique = updates = np.zeros(293, dtype=int)
    return dict(
        eligible_count=supported.sum(0),
        expected_loss_weight=expected,
        actual_loss_weight=weight,
        sampled_terms=draws,
        sampled_unique_origins=unique,
        updates_with_horizon=updates,
    )


def training_stats(q, target, mask, unit, bins):
    error = q[:, 1:] - target
    mm = error * unit
    lengths = mask.sum(1)
    rows = []
    for b in bins:
        sl = slice(b["start"] - 1, b["end"])
        active = mask[:, sl].astype(bool)
        count = int(active.sum())
        for p in range(4):
            e = mm[:, sl, p][active]
            loss_part = np.where(active, error[:, sl, p] ** 2, 0).sum(1)
            rows.append(
                dict(
                    bin=b["name"],
                    point=p,
                    n_terms=count,
                    supported_origins=int(active.any(1).sum()),
                    mae=float(abs(e).mean()) if count else np.nan,
                    rmse=float(np.sqrt((e * e).mean())) if count else np.nan,
                    bias=float(e.mean()) if count else np.nan,
                    normalized_mse=float((e * e).mean() / unit[p] ** 2)
                    if count
                    else np.nan,
                    objective_contribution=float((loss_part / lengths / 4).mean()),
                )
            )
    return rows
