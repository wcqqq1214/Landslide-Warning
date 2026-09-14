"""No fitting: causal historical errors, fixed shrinkage and fixed RMS rules."""

import numpy as np

from tcn_conditional_trajectory.core import (  # noqa: F401
    ROOT,
    check_deadline,
    load_npz,
    read_json,
    read_labels,
    scores,
    sha,
    summarize,
    utc,
    write_json,
)
from sequence_conditional.run import event, lock, verify_lock  # noqa: F401

CONFIG = ROOT / "config/ootang_transformer_calibration.v1_0.json"
SOURCES = ROOT / "docs/ootang_transformer_calibration_sources.v1.0.json"
B = "BPLUS_CONTINUOUS"
OLD = "TRANSFORMER_BRES_COND"
REG = "TRANSFORMER_BRES_REG1"
HALF = "TRANSFORMER_BRES_HALF"


def spec():
    return read_json(CONFIG)


def guard_sources():
    files = read_json(SOURCES)["files"]
    for path, digest in files.items():
        if sha(ROOT / path) != digest:
            raise ValueError("Frozen source changed: " + path)
    return len(files)


def shrink(physical, original, weight):
    physical, original = np.asarray(physical), np.asarray(original)
    if physical.shape != original.shape or not np.isfinite(weight):
        raise ValueError("Invalid shrinkage inputs")
    result = physical + weight * (original - physical)
    if not np.isfinite(result).all():
        raise ValueError("Nonfinite mean")
    return result


def unit(y_prefix):
    if y_prefix.ndim != 2 or y_prefix.shape[1] != 4:
        raise ValueError("Expected training-prefix by four-point labels")
    return np.maximum((y_prefix - y_prefix[0]).std(axis=0), 1.0)


def neighbors(historical_distances, forecast_days, rule, count=90):
    distances = np.asarray(historical_distances, int)
    if (
        len(distances) < count
        or np.any(np.diff(distances) <= 0)
        or distances[0] < 1
        or forecast_days < 1
    ):
        raise ValueError("Need distinct positive ordered mature forecast distances")
    if rule == "LAST90":
        return np.tile(
            np.arange(len(distances) - count, len(distances)), (forecast_days, 1)
        )
    if rule not in ("DIST90", "DIST90_UNIT"):
        raise ValueError("Unknown frozen calibration rule")
    return np.stack(
        [
            np.lexsort((distances, abs(distances - h)))[:count]
            for h in range(1, forecast_days + 1)
        ]
    )


def calibration(history_mean, y_prefix, source, forecast_days, rule, floor=1e-6):
    """The only label argument is already restricted to the issue-time prefix."""
    lo, hi = source["indices"]
    prefix = source["teacher_prefix"]
    if not 0 < prefix <= lo < hi <= len(y_prefix):
        raise ValueError("Calibration teacher/labels cross the issue-time boundary")
    if lo <= source["available_after_selection_index"]:
        raise ValueError("Selection labels cannot be recycled for calibration")
    if history_mean.shape != (hi - lo, 4):
        raise ValueError("Historical issued mean must align with mature indices")
    errors = history_mean - y_prefix[lo:hi]
    if not np.isfinite(errors).all():
        raise ValueError("Nonfinite calibration error")
    distances = np.arange(lo, hi) - prefix + 1
    selected = neighbors(distances, forecast_days, rule)
    # Canonical chronological summation makes equal sample sets bit-identical.
    values = errors[np.sort(selected, axis=1)]
    if rule == "DIST90_UNIT":
        values = values / unit(y_prefix[:prefix]) * unit(y_prefix)
    sigma = np.maximum(np.sqrt(np.mean(values**2, axis=1)), floor)
    return sigma, selected, errors


def effect(candidate, reference, cfg):
    c, r, e = summarize(candidate), summarize(reference), cfg["effect"]
    mean = {
        "average_" + k: c[k] <= r[k] * (1 - e["mean_relative_improvement"])
        for k in ("mae", "rmse")
    }
    probability = {
        "average_" + k: c[k] <= r[k] * (1 - e["probability_relative_improvement"])
        for k in ("crps", "interval_score90")
    }
    probability["coverage90_average"] = c["coverage90"] >= e["coverage90_average_min"]
    for i, p in enumerate(cfg["points"]):
        for k in ("mae", "rmse"):
            mean[p + "_" + k] = (
                candidate[i][k] <= reference[i][k] + e["point_mean_atol_mm"]
            )
        for k in ("crps", "interval_score90"):
            probability[p + "_" + k] = candidate[i][k] <= reference[i][k] * (
                1 + e["point_probability_max_regression"]
            )
        probability[p + "_coverage90"] = (
            candidate[i]["coverage90"] >= e["coverage90_point_min"]
        )
    return dict(
        mean_pass=all(mean.values()),
        probability_pass=all(probability.values()),
        joint_pass=all(mean.values()) and all(probability.values()),
        mean_checks=mean,
        probability_checks=probability,
    )


def choose(items, keys, order, tolerance=1e-12):
    remaining = list(order)
    for key in keys:
        best = min(items[k][key] for k in remaining)
        remaining = [k for k in remaining if items[k][key] <= best + tolerance]
    return remaining[0]
