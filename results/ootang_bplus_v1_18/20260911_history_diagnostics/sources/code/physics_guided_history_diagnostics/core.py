"""Same-weight probes and descriptive correction statistics; no optimization."""

import numpy as np
import pandas as pd
import torch

from physics_guided_history_learning.core import training_pairs, evaluation_pairs

POINTS = ("ATU1", "ATU5", "MJ3", "MJ1")
PARTS = ("training_pairs", "fit_readout", "prediction")
BANDS = (("all", -1, 180), ("1_30", 0, 30), ("31_90", 30, 90), ("91_180", 90, 180))


def queries(h):
    train = training_pairs(h)
    evaluation = evaluation_pairs(h, h + 180)
    pairs = np.concatenate([train, evaluation])
    kind = np.concatenate(
        [np.zeros(len(train), dtype=int), np.where(evaluation[:, 1] < h, 1, 2)]
    )
    return pairs, kind


def masked_inputs(x):
    if x.ndim != 5 or x.shape[1:] != (30, 23, 1, 4) or not torch.isfinite(x).all():
        raise ValueError("Expected finite, standardized history-learning inputs")
    masked = x.clone()
    masked[:, :, 20:22] = 0
    return masked


def evaluate(model, x, batch, calls, limit):
    """Count every forward and forbid gradients or mutable model parameters."""
    if type(batch) is not int or batch < 1 or not len(x):
        raise ValueError("Invalid diagnostic batch")
    model.eval().requires_grad_(False)
    before = {k: v.detach().clone() for k, v in model.state_dict().items()}
    output = []
    with torch.inference_mode():
        for start in range(0, len(x), batch):
            if calls["forward_calls"] >= limit:
                raise RuntimeError("Registered diagnostic forward budget exhausted")
            calls["forward_calls"] += 1
            value = model(x[start : start + batch]).numpy()
            if (
                value.shape != (min(batch, len(x) - start), 4)
                or not np.isfinite(value).all()
            ):
                raise ArithmeticError("Invalid diagnostic output")
            output.append(value)
    if any(not torch.equal(v, before[k]) for k, v in model.state_dict().items()):
        raise ValueError("Frozen model state changed during inference")
    return np.concatenate(output)


def summary_arrays(saved, base, labels):
    """Include the original day-30 zero correction only in the all-fit readout."""
    pairs, kind = saved["pairs"], saved["kind"]
    target = np.r_[pairs[:, 1], 30]
    return dict(
        kind=np.r_[kind, 1],
        lead=np.r_[pairs[:, 1] - pairs[:, 0], -1],
        need=labels[target] - base[target],
        H=np.concatenate([saved["H"], np.zeros((3, 1, 4))], axis=1),
        H0=np.concatenate([saved["H0"], np.zeros((3, 1, 4))], axis=1),
    )


def demand_stats(need, training, tolerance=1e-8):
    if (
        not len(need)
        or not len(training)
        or not np.isfinite(need).all()
        or not np.isfinite(training).all()
    ):
        raise ValueError("Demand comparisons need finite, nonempty samples")
    lo, hi = training.min(), training.max()
    rms, train_rms = np.sqrt(np.mean(need**2)), np.sqrt(np.mean(training**2))
    excess = np.maximum(np.maximum(lo - need, need - hi), 0)
    return dict(
        count=len(need),
        need_mean_mm=float(need.mean()),
        need_rms_mm=float(rms),
        need_min_mm=float(need.min()),
        need_max_mm=float(need.max()),
        training_count=len(training),
        training_need_min_mm=float(lo),
        training_need_max_mm=float(hi),
        training_need_rms_mm=float(train_rms),
        outside_training_count=int(np.count_nonzero(excess > 0)),
        outside_training_fraction=float(np.mean(excess > 0)),
        maximum_excess_mm=float(excess.max()),
        need_rms_over_training=float(rms / train_rms)
        if train_rms > tolerance
        else np.nan,
    )


def model_stats(need, full, zeroed, tolerance=1e-6):
    if (
        not (need.shape == full.shape == zeroed.shape)
        or not len(need)
        or not all(np.isfinite(x).all() for x in (need, full, zeroed))
    ):
        raise ValueError("Matched finite demand and corrections required")
    result = dict(count=len(need))
    for name, correction in (("H", full), ("H0", zeroed)):
        error = correction - need
        active = abs(correction) > tolerance
        wrong = (
            active & (abs(need) > tolerance) & (np.sign(correction) != np.sign(need))
        )
        result.update(
            {
                name + "_correction_mean_mm": float(correction.mean()),
                name + "_correction_rms_mm": float(np.sqrt(np.mean(correction**2))),
                name + "_rmse_mm": float(np.sqrt(np.mean(error**2))),
                name + "_mae_mm": float(np.mean(abs(error))),
                name + "_bias_mm": float(error.mean()),
                name + "_active_count": int(active.sum()),
                name + "_wrong_direction_count": int(wrong.sum()),
                name + "_mse_minus_P0_mm2": float(np.mean(error**2) - np.mean(need**2)),
                name + "_mse_identity_mm2": float(
                    np.mean(correction**2 - 2 * correction * need)
                ),
            }
        )
    effect = full - zeroed
    result.update(
        history_effect_mean_mm=float(effect.mean()),
        history_effect_rms_mm=float(np.sqrt(np.mean(effect**2))),
        history_effect_max_abs_mm=float(abs(effect).max()),
        H_minus_H0_rmse_mm=result["H_rmse_mm"] - result["H0_rmse_mm"],
        H_minus_H0_mae_mm=result["H_mae_mm"] - result["H0_mae_mm"],
        H_minus_H0_mse_mm2=float(np.mean((full - need) ** 2 - (zeroed - need) ** 2)),
        history_mse_identity_mm2=float(
            np.mean(effect**2 + 2 * effect * (zeroed - need))
        ),
    )
    return result


def build_tables(prefixes):
    demand, models, coverage = [], [], []
    for h, data in prefixes.items():
        for lead in range(180):
            coverage.append(
                dict(
                    fit_days=h,
                    lead=lead,
                    **{
                        part: int(np.sum((data["kind"] == i) & (data["lead"] == lead)))
                        for i, part in enumerate(PARTS)
                    },
                )
            )
        for i, part in enumerate(PARTS):
            for band, start, stop in BANDS:
                within = (data["lead"] >= start) & (data["lead"] < stop)
                selected = (data["kind"] == i) & within
                training = (data["kind"] == 0) & within
                for j, station in enumerate(POINTS):
                    key = dict(fit_days=h, part=part, band=band, station=station)
                    need = data["need"][selected, j]
                    demand.append(
                        {**key, **demand_stats(need, data["need"][training, j])}
                    )
                    for seed in ("0", "1", "2", "ensemble"):
                        values = {
                            k: (
                                data[k].mean(axis=0)
                                if seed == "ensemble"
                                else data[k][int(seed)]
                            )[selected, j]
                            for k in ("H", "H0")
                        }
                        models.append(
                            {
                                **key,
                                "seed": seed,
                                **model_stats(need, values["H"], values["H0"]),
                            }
                        )
    return {
        "demand": pd.DataFrame(demand),
        "models": pd.DataFrame(models),
        "lead_coverage": pd.DataFrame(coverage),
    }
