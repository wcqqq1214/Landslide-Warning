"""Explicit prefix contracts and immutable candidate identities."""

import numpy as np

from sequence_conditional.core import (  # noqa: F401
    ROOT,
    POINTS,
    Scaling,
    TrajectoryModel,
    array_sha,
    check_deadline,
    drift,
    load_npz,
    predict,
    read_forcing,
    read_json,
    read_labels,
    reload_model,
    ridge_predict,
    scores,
    sha,
    summarize,
    utc,
    write_json,
)
from sequence_conditional.run import event, lock, verify_lock  # noqa: F401
from tcn_conditional_trajectory.core import fit_ridge, physical_trajectory  # noqa: F401
from transformer_calibration.core import choose, effect, shrink  # noqa: F401
from transformer_regularization.core import objective, training_inputs  # noqa: F401

CONFIG = ROOT / "config/ootang_transformer_temporal.v1_0.json"
SOURCES = ROOT / "docs/ootang_transformer_temporal_sources.v1.0.json"
ARM = "TRANSFORMER_BRES_COND"
B = "BPLUS_CONTINUOUS"
ALPHAS = dict(zip(["A0", "A025", "A05", "A075", "A1"], [0, 0.25, 0.5, 0.75, 1]))
BASES = [B, "DRIFT1", "RR_COND"]


def spec():
    return read_json(CONFIG)


def checkpoint_updates(saved):
    values = [saved[k] for k in ("step", "updates") if k in saved]
    if not values or any(v != values[0] for v in values):
        raise ValueError("Missing or conflicting checkpoint update metadata")
    return int(values[0])


def guard():
    files = read_json(SOURCES)["files"]
    for p, h in files.items():
        if sha(ROOT / p) != h:
            raise ValueError("Frozen source changed: " + p)
    return len(files)


def labels(cfg, root, end, purpose):
    event(root, "label_prefix_read", rows=end, last_index=end - 1, purpose=purpose)
    return read_labels(ROOT / cfg["data"], end)


def teacher_path(cfg, n):
    return ROOT / cfg["out"] / "implementation_verification" / f"teacher_{n}.npz"


def slice_roles(previous, current):
    if not 0 < previous < previous + 90 < previous + 180 <= current:
        raise ValueError("Historical selection/calibration labels are not mature")
    return (previous, previous + 90), (previous + 90, previous + 180)


def select_and_calibrate(previous_means, y_prefix, previous, current, order, cfg):
    if y_prefix.shape != (current, 4):
        raise ValueError("Selection accepts exactly the current label prefix")
    sel, cal = slice_roles(previous, current)
    summary = {}
    sigmas = {}
    errors = {}
    for key, mean in previous_means.items():
        if len(mean) < cal[1]:
            raise ValueError("Historical issued trajectory too short")
        summary[key] = summarize(
            scores(y_prefix[sel[0] : sel[1]], mean[sel[0] : sel[1]])
        )
        errors[key] = mean[cal[0] : cal[1]] - y_prefix[cal[0] : cal[1]]
        sigmas[key] = np.maximum(
            np.sqrt(np.mean(errors[key] ** 2, axis=0)),
            cfg["calibration"]["sigma_floor_mm"],
        )
    chosen = choose(
        summary, cfg["selection"]["keys"], order, cfg["selection"]["tie_tolerance"]
    )
    record = dict(
        previous_origin=previous,
        issue_origin=current,
        selection_indices=list(sel),
        calibration_indices=list(cal),
        last_label_index=cal[1] - 1,
        selected=chosen,
        scores=summary,
        displacement_feedback=False,
    )
    return record, sigmas, errors


def verify_implementation(cfg):
    root = ROOT / cfg["out"]
    guard()
    for p, h in read_json(root / "implementation_lock.json")["files"].items():
        if sha(ROOT / p) != h:
            raise ValueError("Implementation changed after freeze: " + p)
    assert (
        read_json(root / "implementation_verification/receipt.json")["status"]
        == "passed"
    )


def saved_source(cfg, n, key, seed, step=400):
    if key not in cfg["reuse"] or str(n) not in cfg["reuse_phases"]:
        return None
    folder = cfg["reuse_phases"][str(n)]
    arm = ARM if key == "L0" else "TRANSFORMER_BRES_REG1"
    return ROOT / cfg["reuse"][key] / folder / arm / f"seed_{seed}/e{step}.pt"
