"""Only the normalized residual objective changes; old modules stay frozen."""

import math

import torch

from sequence_conditional.core import (  # noqa: F401
    ROOT,
    Scaling,
    TrajectoryModel,
    array_sha,
    calibrate,
    check_deadline,
    choose,
    gates,
    load_npz,
    predict,
    read_forcing,
    read_json,
    read_labels,
    reload_model,
    scores,
    sha,
    summarize,
    utc,
    write_json,
)
from sequence_conditional.run import event, lock, verify_lock  # noqa: F401

CONFIG = ROOT / "config/ootang_transformer_regularization.v1_0.json"
SOURCES = ROOT / "docs/ootang_transformer_regularization_sources.v1.0.json"
OLD = "TRANSFORMER_BRES_COND"
NEW = "TRANSFORMER_BRES_REG1"


def spec():
    return read_json(CONFIG)


def guard_sources():
    values = read_json(SOURCES)["files"]
    for name, digest in values.items():
        if sha(ROOT / name) != digest:
            raise ValueError("Frozen source changed: " + name)
    return len(values)


def objective(output, target, strength):
    if output.shape != target.shape or output.ndim != 2 or output.shape[1] != 4:
        raise ValueError("Expected equal training-day x four-point arrays")
    if not math.isfinite(strength) or strength < 0:
        raise ValueError("Invalid regularization strength")
    data = (output - target).square().mean()
    penalty = output.square().mean()
    total = data + strength * penalty
    if not torch.isfinite(total):
        raise ArithmeticError("Nonfinite regularized training objective")
    return total, data, penalty


def training_inputs(data, y, cfg):
    n = len(y)
    if data["x"].shape[0] < n or data["mean"].shape[0] < n:
        raise ValueError("Training prefix outside teacher")
    scale = Scaling(data["x"][:n], y, cfg=cfg)
    x = scale.tensor(data["x"][:n])
    target = torch.tensor((y - data["mean"][:n]) / scale.unit, dtype=torch.float64)
    return scale, x, target


def labels(cfg, root, end, purpose):
    event(root, "label_prefix_read", rows=end, last_index=end - 1, purpose=purpose)
    return read_labels(ROOT / cfg["data"], end)
