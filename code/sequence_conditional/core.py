"""Reuse frozen data/scoring contracts; own model/config/source boundaries."""

import numpy as np
import torch

from tcn_conditional_trajectory.core import (  # noqa: F401
    ROOT,
    POINTS,
    Scaling,
    array_sha,
    calibrate,
    check_deadline,
    choose,
    drift,
    feature_matrix,
    load_npz,
    read_forcing,
    read_json,
    read_labels,
    ridge_predict,
    scores,
    sha,
    summarize,
    utc,
    write_json,
)
from tcn_conditional_trajectory.core import gates as tcn_gates
from .models import TrajectoryModel

CONFIG = ROOT / "config/ootang_sequence_conditional.v1_0.json"
ARMS = (
    "TRANSFORMER_DIRECT_COND",
    "TRANSFORMER_BRES_COND",
    "CNN_MAMBA_DIRECT_COND",
    "CNN_MAMBA_BRES_COND",
)


def spec():
    return read_json(CONFIG)


def guard_sources():
    sources = read_json(ROOT / "docs/ootang_sequence_conditional_sources.v1.0.json")[
        "files"
    ]
    for path, digest in sources.items():
        if sha(ROOT / path) != digest:
            raise ValueError("Frozen source changed: " + path)
    return len(sources)


def baseline(arm, physical, scale):
    if arm not in ARMS:
        raise ValueError("Unknown paired arm")
    if "_BRES_" in arm:
        return physical
    return np.broadcast_to(scale.y0, physical.shape).copy()


def predict(model, scale, arm, x, physical):
    model.eval()
    with torch.no_grad():
        output = model(scale.tensor(x)).numpy()
    result = baseline(arm, physical, scale) + output * scale.unit
    if not np.isfinite(result).all():
        raise ArithmeticError("Nonfinite complete trajectory")
    return result


def reload_model(path, cfg):
    saved = torch.load(path, map_location="cpu", weights_only=True)
    model = TrajectoryModel(cfg, saved["seed"], saved["arm"])
    model.load_state_dict(saved["state_dict"], strict=True)
    return model, Scaling(state=saved["scaling"]), saved


def gates(metrics, cfg):
    answer = {}
    for pair in cfg["families"].values():
        mapped = {
            "BPLUS_CONTINUOUS": metrics["BPLUS_CONTINUOUS"],
            "TCN_DIRECT_COND": metrics[pair[0]],
            "TCN_BRES_COND": metrics[pair[1]],
        }
        old = tcn_gates(mapped, cfg)
        answer.update({pair[0]: old["TCN_DIRECT_COND"], pair[1]: old["TCN_BRES_COND"]})
    return answer
